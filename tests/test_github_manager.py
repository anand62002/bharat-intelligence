"""
tests/test_github_manager.py
Unit tests for governance/github_manager.py

Coverage:
  TestCardinalRule              — _assert_not_protected on all protected branches
  TestCreateEnhancementBranch   — happy path, already-exists toleration
  TestCommitEnhancement         — create new file, update existing file, protected branch
  TestCreatePullRequest         — happy path, Telegram notification, protected branch
  TestMergePullRequest          — open PR merge, already-merged, non-open state
  TestRollbackLastDeploy        — full rollback: branch→revert commit→PR→Telegram
  TestRollbackDryRun            — dry_run=True makes no API calls
  TestRollbackNoMergeCommit     — raises when no merge commit found
  TestListOpenPrs               — list formatting
  TestGetPrStatus               — state/merge/mergeable
  TestSendTelegram              — dry_run, missing creds, HTTP error
  TestGetManager                — factory: missing token, missing repo

Run:
    pytest tests/test_github_manager.py -v
"""

import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, PropertyMock, call, patch

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from governance.github_manager import (
    DEFAULT_BASE_BRANCH,
    MERGE_METHOD,
    PROTECTED_BRANCHES,
    GitHubManager,
    _send_telegram,
    get_manager,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_manager(repo_slug: str = "owner/bharat-intelligence") -> tuple:
    """
    Return (manager, mock_repo) with PyGithub completely mocked out.
    The manager is fully initialised and its internal _repo is the mock.
    """
    mock_repo = MagicMock()
    mock_repo.full_name = repo_slug

    with (
        patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_fake", "GITHUB_REPO": repo_slug}),
        patch("governance.github_manager.Github") as mock_github_cls,
    ):
        mock_github_cls.return_value.get_repo.return_value = mock_repo
        mgr = GitHubManager()

    mgr._repo = mock_repo
    return mgr, mock_repo


def _make_commit(sha: str, message: str = "feat: something", n_parents: int = 1) -> MagicMock:
    """Build a mock GitHub commit object."""
    c = MagicMock()
    c.sha = sha

    raw = MagicMock()
    raw.sha     = sha
    raw.message = message
    raw.parents = [MagicMock(sha=f"parent_{i}_{sha[:6]}") for i in range(n_parents)]
    raw.tree    = MagicMock(sha=f"tree_{sha[:6]}")

    c.commit          = MagicMock()
    c.commit.message  = message
    c.commit.parents  = raw.parents
    return c, raw


# ──────────────────────────────────────────────────────────────────────────────
# TestCardinalRule
# ──────────────────────────────────────────────────────────────────────────────

class TestCardinalRule:
    @pytest.mark.parametrize("branch", ["main", "master", "production", "release"])
    def test_protected_branch_raises(self, branch):
        with pytest.raises(RuntimeError, match="CARDINAL RULE"):
            GitHubManager._assert_not_protected(branch)

    @pytest.mark.parametrize("branch", ["main", "MAIN", "Master", "PRODUCTION"])
    def test_case_insensitive(self, branch):
        with pytest.raises(RuntimeError, match="CARDINAL RULE"):
            GitHubManager._assert_not_protected(branch)

    @pytest.mark.parametrize("branch", [
        "feat/improve-technical",
        "rollback/20250420-120000",
        "fix/ep-42",
        "hotfix/crash-on-null",
    ])
    def test_non_protected_branches_pass(self, branch):
        GitHubManager._assert_not_protected(branch)  # should not raise

    def test_protected_branches_set_is_complete(self):
        assert "main" in PROTECTED_BRANCHES
        assert "master" in PROTECTED_BRANCHES
        assert "production" in PROTECTED_BRANCHES


# ──────────────────────────────────────────────────────────────────────────────
# TestCreateEnhancementBranch
# ──────────────────────────────────────────────────────────────────────────────

class TestCreateEnhancementBranch:
    def test_creates_branch_from_main_head(self):
        mgr, repo = _make_manager()
        main_ref = MagicMock()
        main_ref.object.sha = "abc123"
        repo.get_git_ref.return_value = main_ref

        sha = mgr.create_enhancement_branch("EP-42", "feat/improve-technical")

        repo.create_git_ref.assert_called_once_with(
            "refs/heads/feat/improve-technical", "abc123"
        )
        assert sha == "abc123"

    def test_raises_for_protected_branch(self):
        mgr, _ = _make_manager()
        with pytest.raises(RuntimeError, match="CARDINAL RULE"):
            mgr.create_enhancement_branch("EP-1", "main")

    def test_tolerates_already_exists_error(self):
        mgr, repo = _make_manager()
        main_ref = MagicMock()
        main_ref.object.sha = "abc123"
        repo.get_git_ref.return_value = main_ref
        repo.create_git_ref.side_effect = Exception("Reference already exists")

        # Should not raise
        sha = mgr.create_enhancement_branch("EP-42", "feat/improve-technical")
        assert sha == "abc123"

    def test_propagates_unknown_errors(self):
        mgr, repo = _make_manager()
        main_ref = MagicMock()
        main_ref.object.sha = "abc123"
        repo.get_git_ref.return_value = main_ref
        repo.create_git_ref.side_effect = RuntimeError("network error")

        with pytest.raises(RuntimeError, match="network error"):
            mgr.create_enhancement_branch("EP-42", "feat/improve-technical")


# ──────────────────────────────────────────────────────────────────────────────
# TestCommitEnhancement
# ──────────────────────────────────────────────────────────────────────────────

class TestCommitEnhancement:
    def test_creates_new_file_when_not_exists(self):
        mgr, repo = _make_manager()
        repo.get_contents.side_effect = Exception("Not Found")

        result_commit = MagicMock()
        result_commit.sha = "newfile_sha"
        repo.create_file.return_value = {"commit": result_commit}

        sha = mgr.commit_enhancement(
            "feat/improve-technical",
            "agents/technical.py",
            "print('hello')",
            "fix: improve rsi thresholds",
        )

        repo.create_file.assert_called_once()
        assert sha == "newfile_sha"

    def test_updates_existing_file(self):
        mgr, repo = _make_manager()
        existing = MagicMock()
        existing.sha = "old_sha_123"
        repo.get_contents.return_value = existing

        result_commit = MagicMock()
        result_commit.sha = "updated_sha"
        repo.update_file.return_value = {"commit": result_commit}

        sha = mgr.commit_enhancement(
            "feat/improve-technical",
            "agents/technical.py",
            "new content",
            "fix: update thresholds",
        )

        repo.update_file.assert_called_once()
        call_kwargs = repo.update_file.call_args
        assert call_kwargs.kwargs.get("sha") == "old_sha_123" or \
               "old_sha_123" in str(call_kwargs)
        assert sha == "updated_sha"

    def test_raises_for_protected_branch(self):
        mgr, _ = _make_manager()
        with pytest.raises(RuntimeError, match="CARDINAL RULE"):
            mgr.commit_enhancement("main", "file.py", "content", "msg")


# ──────────────────────────────────────────────────────────────────────────────
# TestCreatePullRequest
# ──────────────────────────────────────────────────────────────────────────────

class TestCreatePullRequest:
    def test_creates_pr_and_returns_number(self):
        mgr, repo = _make_manager()
        mock_pr = MagicMock()
        mock_pr.number   = 42
        mock_pr.html_url = "https://github.com/owner/repo/pull/42"
        repo.create_pull.return_value = mock_pr

        with patch("governance.github_manager._send_telegram") as mock_tg:
            pr_num = mgr.create_pull_request(
                "feat/improve-technical",
                "Improve technical agent",
                "## Summary\n- better RSI thresholds",
            )

        assert pr_num == 42
        repo.create_pull.assert_called_once_with(
            title="Improve technical agent",
            body="## Summary\n- better RSI thresholds",
            head="feat/improve-technical",
            base=DEFAULT_BASE_BRANCH,
        )
        mock_tg.assert_called_once()
        tg_msg = mock_tg.call_args[0][0]
        assert "42" in tg_msg
        assert "PR" in tg_msg.upper() or "Pull" in tg_msg

    def test_raises_for_protected_head_branch(self):
        mgr, _ = _make_manager()
        with pytest.raises(RuntimeError, match="CARDINAL RULE"):
            mgr.create_pull_request("main", "title", "body")

    def test_telegram_notification_on_pr_create(self):
        mgr, repo = _make_manager()
        mock_pr = MagicMock(number=7, html_url="https://github.com/o/r/pull/7")
        repo.create_pull.return_value = mock_pr

        with patch("governance.github_manager._send_telegram") as mock_tg:
            mgr.create_pull_request("feat/x", "title", "body")

        mock_tg.assert_called_once()


# ──────────────────────────────────────────────────────────────────────────────
# TestMergePullRequest
# ──────────────────────────────────────────────────────────────────────────────

class TestMergePullRequest:
    def test_merges_open_pr(self):
        mgr, repo = _make_manager()
        mock_pr = MagicMock()
        mock_pr.is_merged.return_value = False
        mock_pr.state = "open"
        mock_pr.head.ref = "feat/improve-technical"
        merge_result = MagicMock()
        merge_result.merged = True
        merge_result.sha    = "merge_sha_abc"
        mock_pr.merge.return_value = merge_result
        repo.get_pull.return_value = mock_pr

        ok = mgr.merge_pull_request(42)

        assert ok is True
        mock_pr.merge.assert_called_once_with(merge_method=MERGE_METHOD)

    def test_returns_true_if_already_merged(self):
        mgr, repo = _make_manager()
        mock_pr = MagicMock()
        mock_pr.is_merged.return_value = True
        mock_pr.state = "closed"
        mock_pr.head.ref = "feat/improve-technical"
        repo.get_pull.return_value = mock_pr

        ok = mgr.merge_pull_request(42)
        assert ok is True

    def test_raises_for_non_open_pr(self):
        mgr, repo = _make_manager()
        mock_pr = MagicMock()
        mock_pr.is_merged.return_value = False
        mock_pr.state = "closed"
        mock_pr.head.ref = "feat/improve-technical"
        repo.get_pull.return_value = mock_pr

        with pytest.raises(RuntimeError, match="not open"):
            mgr.merge_pull_request(42)

    def test_cardinal_rule_on_merge_pr_pointing_to_main(self):
        """If somehow a PR head branch is main, cardinal rule should fire."""
        mgr, repo = _make_manager()
        mock_pr = MagicMock()
        mock_pr.is_merged.return_value = False
        mock_pr.state = "open"
        mock_pr.head.ref = "main"   # ← protected
        repo.get_pull.return_value = mock_pr

        with pytest.raises(RuntimeError, match="CARDINAL RULE"):
            mgr.merge_pull_request(99)

    def test_returns_false_when_merge_fails(self):
        mgr, repo = _make_manager()
        mock_pr = MagicMock()
        mock_pr.is_merged.return_value = False
        mock_pr.state = "open"
        mock_pr.head.ref = "feat/x"
        merge_result = MagicMock()
        merge_result.merged  = False
        merge_result.message = "Merge conflict"
        mock_pr.merge.return_value = merge_result
        repo.get_pull.return_value = mock_pr

        ok = mgr.merge_pull_request(42)
        assert ok is False


# ──────────────────────────────────────────────────────────────────────────────
# TestRollbackLastDeploy
# ──────────────────────────────────────────────────────────────────────────────

class TestRollbackLastDeploy:
    def _setup_rollback(self, repo: MagicMock) -> dict:
        """
        Wire up the mock repo so rollback_last_deploy() can complete.
        Returns a dict of the key mock objects for assertion.
        """
        # ── Commits on main: first is a regular commit, second is a merge ────
        regular_commit, regular_raw = _make_commit("sha_regular", "feat: normal", n_parents=1)
        merge_commit,   merge_raw   = _make_commit("sha_merge", "Merge PR #5", n_parents=2)

        # Parent[0] of the merge commit = pre-merge state on main
        pre_merge_parent_sha = merge_raw.parents[0].sha
        pre_merge_raw        = MagicMock()
        pre_merge_raw.tree   = MagicMock(sha="tree_pre_merge")

        # Current HEAD of main
        main_ref = MagicMock()
        main_ref.object.sha = "sha_current_head"

        # Mock the sequence of repo calls
        def get_commits_side_effect(sha):
            return iter([regular_commit, merge_commit])

        def get_git_commit_side_effect(sha):
            if sha == "sha_merge":
                return merge_raw
            if sha == pre_merge_parent_sha:
                return pre_merge_raw
            if sha == "sha_current_head":
                m = MagicMock()
                m.tree = MagicMock(sha="tree_head")
                return m
            return MagicMock()

        repo.get_commits.side_effect = get_commits_side_effect
        repo.get_git_commit.side_effect = get_git_commit_side_effect
        repo.get_git_ref.return_value = main_ref
        repo.get_git_tree.return_value = MagicMock(sha="tree_pre_merge")

        revert_commit = MagicMock()
        revert_commit.sha = "sha_revert_commit"
        repo.create_git_commit.return_value = revert_commit
        repo.create_git_ref.return_value = MagicMock()

        rollback_ref = MagicMock()
        repo.get_git_ref.side_effect = lambda ref: (
            main_ref if "main" in ref else rollback_ref
        )

        emergency_pr = MagicMock()
        emergency_pr.number   = 99
        emergency_pr.html_url = "https://github.com/o/r/pull/99"
        repo.create_pull.return_value = emergency_pr

        return {
            "merge_commit":      merge_commit,
            "merge_raw":         merge_raw,
            "emergency_pr":      emergency_pr,
            "revert_commit":     revert_commit,
            "pre_merge_parent":  pre_merge_parent_sha,
        }

    def test_creates_rollback_branch(self):
        mgr, repo = _make_manager()
        mocks = self._setup_rollback(repo)

        with patch("governance.github_manager._send_telegram"):
            pr_num = mgr.rollback_last_deploy()

        assert pr_num == 99
        # Branch was created
        repo.create_git_ref.assert_called_once()
        branch_ref_arg = repo.create_git_ref.call_args[0][0]
        assert branch_ref_arg.startswith("refs/heads/rollback/")

    def test_creates_revert_commit(self):
        mgr, repo = _make_manager()
        mocks = self._setup_rollback(repo)

        with patch("governance.github_manager._send_telegram"):
            mgr.rollback_last_deploy()

        repo.create_git_commit.assert_called_once()
        commit_msg = repo.create_git_commit.call_args.kwargs.get("message", "") or \
                     repo.create_git_commit.call_args[1].get("message", "")
        assert "revert" in commit_msg.lower() or "rollback" in commit_msg.lower()

    def test_opens_emergency_pr(self):
        mgr, repo = _make_manager()
        self._setup_rollback(repo)

        with patch("governance.github_manager._send_telegram"):
            pr_num = mgr.rollback_last_deploy()

        repo.create_pull.assert_called_once()
        pr_call = repo.create_pull.call_args
        assert "ROLLBACK" in pr_call.kwargs.get("title", "") or \
               "ROLLBACK" in str(pr_call)
        assert pr_call.kwargs.get("base") == DEFAULT_BASE_BRANCH or \
               DEFAULT_BASE_BRANCH in str(pr_call)

    def test_pr_body_contains_reverted_sha(self):
        mgr, repo = _make_manager()
        mocks = self._setup_rollback(repo)

        with patch("governance.github_manager._send_telegram"):
            mgr.rollback_last_deploy()

        pr_body = repo.create_pull.call_args.kwargs.get("body", "")
        assert "sha_merge"[:8] in pr_body

    def test_sends_telegram_alert(self):
        mgr, repo = _make_manager()
        self._setup_rollback(repo)

        with patch("governance.github_manager._send_telegram") as mock_tg:
            mgr.rollback_last_deploy()

        mock_tg.assert_called_once()
        tg_msg = mock_tg.call_args[0][0]
        assert "ROLLBACK" in tg_msg.upper()
        assert "sha_merge"[:8] in tg_msg

    def test_raises_when_no_merge_commit_found(self):
        mgr, repo = _make_manager()
        # All commits have only 1 parent (no merge commits)
        regular1, raw1 = _make_commit("sha1", "normal commit", n_parents=1)
        regular2, raw2 = _make_commit("sha2", "another commit", n_parents=1)

        repo.get_commits.side_effect = lambda sha: iter([regular1, regular2])
        repo.get_git_commit.side_effect = lambda sha: raw1 if sha == "sha1" else raw2

        with pytest.raises(RuntimeError, match="No merge commit found"):
            mgr.rollback_last_deploy()


# ──────────────────────────────────────────────────────────────────────────────
# TestRollbackDryRun
# ──────────────────────────────────────────────────────────────────────────────

class TestRollbackDryRun:
    def test_dry_run_makes_no_api_calls(self, capsys):
        mgr, repo = _make_manager()

        merge_commit, merge_raw = _make_commit("sha_merge", "Merge PR #5", n_parents=2)
        pre_merge_parent = MagicMock()
        pre_merge_parent.sha = "sha_pre_merge"
        merge_raw.parents[0] = pre_merge_parent

        pre_merge_raw = MagicMock()
        pre_merge_raw.tree = MagicMock(sha="tree_pre")
        repo.get_commits.side_effect = lambda sha: iter([merge_commit])
        repo.get_git_commit.side_effect = lambda sha: (
            merge_raw if sha == "sha_merge" else pre_merge_raw
        )

        with patch("governance.github_manager._send_telegram") as mock_tg:
            result = mgr.rollback_last_deploy(dry_run=True)

        assert result == 0
        repo.create_git_ref.assert_not_called()
        repo.create_git_commit.assert_not_called()
        repo.create_pull.assert_not_called()

        captured = capsys.readouterr().out
        assert "DRY RUN" in captured

        # Telegram should still be called (even in dry_run mode)
        mock_tg.assert_called_once()
        mock_tg_args = mock_tg.call_args
        # dry_run=True is passed to _send_telegram
        assert mock_tg_args.kwargs.get("dry_run") is True or \
               True in mock_tg_args.args


# ──────────────────────────────────────────────────────────────────────────────
# TestListOpenPrs
# ──────────────────────────────────────────────────────────────────────────────

class TestListOpenPrs:
    def test_returns_list_of_pr_dicts(self):
        mgr, repo = _make_manager()
        mock_pr = MagicMock()
        mock_pr.number     = 5
        mock_pr.title      = "Add feature"
        mock_pr.head.ref   = "feat/add-feature"
        mock_pr.html_url   = "https://github.com/o/r/pull/5"
        mock_pr.created_at = datetime(2025, 4, 1, tzinfo=timezone.utc)
        repo.get_pulls.return_value = [mock_pr]

        prs = mgr.list_open_prs()

        assert len(prs) == 1
        assert prs[0]["number"] == 5
        assert prs[0]["branch"] == "feat/add-feature"

    def test_returns_empty_list_when_no_prs(self):
        mgr, repo = _make_manager()
        repo.get_pulls.return_value = []
        assert mgr.list_open_prs() == []


# ──────────────────────────────────────────────────────────────────────────────
# TestGetPrStatus
# ──────────────────────────────────────────────────────────────────────────────

class TestGetPrStatus:
    def test_returns_pr_status_dict(self):
        mgr, repo = _make_manager()
        mock_pr = MagicMock()
        mock_pr.number    = 10
        mock_pr.title     = "Fix bug"
        mock_pr.state     = "open"
        mock_pr.mergeable = True
        mock_pr.html_url  = "https://github.com/o/r/pull/10"
        mock_pr.head.ref  = "fix/bug"
        mock_pr.is_merged.return_value = False
        repo.get_pull.return_value = mock_pr

        status = mgr.get_pr_status(10)

        assert status["number"] == 10
        assert status["state"] == "open"
        assert status["merged"] is False
        assert status["mergeable"] is True


# ──────────────────────────────────────────────────────────────────────────────
# TestSendTelegram
# ──────────────────────────────────────────────────────────────────────────────

class TestSendTelegram:
    def test_dry_run_prints_and_returns_true(self, capsys):
        ok = _send_telegram("hello", dry_run=True)
        assert ok is True
        assert "hello" in capsys.readouterr().out

    def test_missing_token_returns_false(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "123"}):
            ok = _send_telegram("msg", dry_run=False)
        assert ok is False

    def test_missing_chat_id_returns_false(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": ""}):
            ok = _send_telegram("msg", dry_run=False)
        assert ok is False

    def test_http_error_returns_false(self):
        with (
            patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "123"}),
            patch("governance.github_manager.requests.post") as mock_post,
        ):
            mock_post.side_effect = Exception("timeout")
            ok = _send_telegram("msg", dry_run=False)
        assert ok is False

    def test_successful_send_returns_true(self):
        with (
            patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "123"}),
            patch("governance.github_manager.requests.post") as mock_post,
        ):
            mock_post.return_value = MagicMock(raise_for_status=lambda: None)
            ok = _send_telegram("msg", dry_run=False)
        assert ok is True


# ──────────────────────────────────────────────────────────────────────────────
# TestGetManager
# ──────────────────────────────────────────────────────────────────────────────

class TestGetManager:
    def test_raises_without_token(self):
        with (
            patch.dict(os.environ, {"GITHUB_TOKEN": "", "GITHUB_REPO": "o/r"}),
            pytest.raises(RuntimeError, match="GITHUB_TOKEN"),
        ):
            get_manager()

    def test_raises_without_repo(self):
        with (
            patch.dict(os.environ, {"GITHUB_TOKEN": "tok", "GITHUB_REPO": ""}),
            pytest.raises(RuntimeError, match="GITHUB_REPO"),
        ):
            get_manager()

    def test_raises_without_pygithub(self):
        with (
            patch.dict(os.environ, {"GITHUB_TOKEN": "tok", "GITHUB_REPO": "o/r"}),
            patch("governance.github_manager.Github", side_effect=NameError("Github")),
            pytest.raises((ImportError, NameError, Exception)),
        ):
            get_manager()
