"""
governance/github_manager.py — Bharat Intelligence: GitHub Automation Manager
==============================================================================
Manages code changes, pull requests, and emergency rollbacks via PyGithub.

╔══════════════════════════════════════════════════════════════════════════════╗
║  CARDINAL RULE: No code is EVER pushed directly to main.                   ║
║  Every change goes through a PR. Hard-coded in _assert_not_protected().    ║
║  Attempting to target a protected branch raises RuntimeError immediately.  ║
╚══════════════════════════════════════════════════════════════════════════════╝

Features
────────
  create_enhancement_branch(proposal_id, branch_name)
      Create a feature branch from the current HEAD of main.

  commit_enhancement(branch_name, file_path, content, commit_message)
      Commit a file change (create or update) to a branch.

  create_pull_request(branch, title, body) → int
      Open a PR from branch → main. Returns PR number.
      Sends Telegram notification.

  merge_pull_request(pr_number) → bool
      Merge an approved PR (squash merge). Validates branch ≠ main.

  rollback_last_deploy() → int
      1. Locate the most recent merge commit on main.
      2. Create rollback/<timestamp> branch at current HEAD.
      3. Push a revert commit (tree reset to pre-merge state).
      4. Open an emergency PR titled 🚨 EMERGENCY ROLLBACK.
      5. Send a Telegram alert.
      Returns emergency PR number.

Required env vars
─────────────────
  GITHUB_TOKEN   Personal access token with repo scope.
  GITHUB_REPO    Owner/repo slug, e.g. "anand62002/bharat-intelligence".

Optional
─────────
  TELEGRAM_BOT_TOKEN   For PR/rollback notifications.
  TELEGRAM_CHAT_ID

Installation
────────────
  pip install PyGithub requests
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

# Module-level import so patch("governance.github_manager.Github") works in tests.
# Falls back to None when PyGithub is not installed; error is raised lazily in __init__.
try:
    from github import Github
except ImportError:
    Github = None  # type: ignore[assignment,misc]

load_dotenv()

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
PROTECTED_BRANCHES = frozenset({"main", "master", "production", "release"})
DEFAULT_BASE_BRANCH = "main"
MERGE_METHOD        = "squash"   # squash | merge | rebase


# ─────────────────────────────────────────────────────────────────────────────
# Telegram helper  (same pattern as portfolio_monitor)
# ─────────────────────────────────────────────────────────────────────────────

def _send_telegram(message: str, dry_run: bool = False) -> bool:
    """Send a Telegram message. Returns True on success."""
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    if dry_run:
        print(f"\n  [TELEGRAM DRY RUN]\n{message}\n")
        return True
    if not token or not chat_id:
        log.debug("Telegram not configured — skipping notification")
        return False
    try:
        url  = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        resp.raise_for_status()
        log.info("Telegram notification sent")
        return True
    except Exception as exc:
        log.warning("Telegram send failed: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# GitHubManager
# ─────────────────────────────────────────────────────────────────────────────

class GitHubManager:
    """
    Wraps PyGithub to provide safe, PR-gated code change management.

    Usage:
        mgr = GitHubManager()
        mgr.create_enhancement_branch("EP-42", "feat/improve-technical-agent")
        mgr.commit_enhancement(
            "feat/improve-technical-agent",
            "agents/technical.py",
            new_content,
            "fix: tighten RSI thresholds for ranging markets",
        )
        pr_num = mgr.create_pull_request(
            "feat/improve-technical-agent",
            "Improve technical agent RSI calibration",
            body_markdown,
        )
        mgr.merge_pull_request(pr_num)
    """

    def __init__(
        self,
        token:    Optional[str] = None,
        repo_slug: Optional[str] = None,
    ) -> None:
        """
        Initialise GitHubManager.

        Args:
            token:     GitHub personal access token. Falls back to GITHUB_TOKEN env var.
            repo_slug: "owner/repo" string. Falls back to GITHUB_REPO env var.

        Raises:
            RuntimeError: If token or repo_slug cannot be resolved.
        """
        self._token     = token     or os.getenv("GITHUB_TOKEN", "")
        self._repo_slug = repo_slug or os.getenv("GITHUB_REPO", "")

        if not self._token:
            raise RuntimeError(
                "GITHUB_TOKEN not set. Add it to .env or pass token= to GitHubManager()."
            )
        if not self._repo_slug:
            raise RuntimeError(
                "GITHUB_REPO not set. Add it to .env (e.g. 'owner/repo') "
                "or pass repo_slug= to GitHubManager()."
            )

        if Github is None:
            raise ImportError(
                "PyGithub not installed — run: pip install PyGithub"
            )

        self._github = Github(self._token)
        self._repo   = self._github.get_repo(self._repo_slug)
        log.info("GitHubManager connected to %s", self._repo.full_name)

    # ── Cardinal rule enforcement ────────────────────────────────────────────

    @staticmethod
    def _assert_not_protected(branch_name: str) -> None:
        """
        CARDINAL RULE: Raise immediately if *branch_name* is a protected branch.
        All code changes must go through a PR, never pushed directly.
        """
        if branch_name.lower().strip() in PROTECTED_BRANCHES:
            raise RuntimeError(
                f"CARDINAL RULE VIOLATION: Direct push to '{branch_name}' is forbidden. "
                "All changes must go through a pull request. "
                "Use create_enhancement_branch() to create a feature branch first."
            )

    # ── Branch management ────────────────────────────────────────────────────

    def create_enhancement_branch(
        self,
        proposal_id: str,
        branch_name: str,
    ) -> str:
        """
        Create a new branch off the current HEAD of main.

        Args:
            proposal_id:  Enhancement proposal ID (logged/attached to PR body).
            branch_name:  New branch name (must not be a protected branch).

        Returns:
            The SHA of the commit the new branch points to.

        Raises:
            RuntimeError: If branch_name is protected.
        """
        self._assert_not_protected(branch_name)

        main_ref  = self._repo.get_git_ref(f"heads/{DEFAULT_BASE_BRANCH}")
        base_sha  = main_ref.object.sha

        try:
            self._repo.create_git_ref(f"refs/heads/{branch_name}", base_sha)
            log.info(
                "Branch created: %s (proposal=%s, base=%s)",
                branch_name, proposal_id, base_sha[:12],
            )
        except Exception as exc:
            # Branch may already exist — log and continue
            if "already exists" in str(exc).lower() or "Reference already exists" in str(exc):
                log.warning("Branch %s already exists — using existing", branch_name)
            else:
                raise

        return base_sha

    # ── File commit ─────────────────────────────────────────────────────────

    def commit_enhancement(
        self,
        branch_name:    str,
        file_path:      str,
        content:        str,
        commit_message: str,
    ) -> str:
        """
        Commit a file change to *branch_name*.

        Creates the file if it doesn't exist; updates it if it does.

        Args:
            branch_name:    Target branch (must not be a protected branch).
            file_path:      Repo-relative path, e.g. "agents/technical.py".
            content:        Full file content as a string.
            commit_message: Git commit message.

        Returns:
            The new commit SHA.

        Raises:
            RuntimeError: If branch_name is protected.
        """
        self._assert_not_protected(branch_name)

        # Try to get the existing file SHA (needed for update)
        existing_sha: Optional[str] = None
        try:
            existing = self._repo.get_contents(file_path, ref=branch_name)
            existing_sha = existing.sha  # type: ignore[union-attr]
        except Exception:
            pass  # file doesn't exist yet — will create

        if existing_sha:
            result = self._repo.update_file(
                path    = file_path,
                message = commit_message,
                content = content.encode("utf-8"),
                sha     = existing_sha,
                branch  = branch_name,
            )
        else:
            result = self._repo.create_file(
                path    = file_path,
                message = commit_message,
                content = content.encode("utf-8"),
                branch  = branch_name,
            )

        commit_sha = result["commit"].sha
        log.info(
            "Committed %s to %s: %s (%s)",
            file_path, branch_name, commit_message[:60], commit_sha[:12],
        )
        return commit_sha

    # ── Pull request ─────────────────────────────────────────────────────────

    def create_pull_request(
        self,
        branch: str,
        title:  str,
        body:   str,
    ) -> int:
        """
        Open a pull request from *branch* → main.

        Sends a Telegram notification after the PR is created.

        Args:
            branch:  Head branch (must not be a protected branch).
            title:   PR title.
            body:    PR body in Markdown.

        Returns:
            PR number.

        Raises:
            RuntimeError: If branch is protected.
        """
        self._assert_not_protected(branch)

        pr = self._repo.create_pull(
            title = title,
            body  = body,
            head  = branch,
            base  = DEFAULT_BASE_BRANCH,
        )

        log.info("PR #%d created: %s", pr.number, pr.html_url)

        tg_msg = (
            f"🔀 <b>Pull Request Created</b>\n"
            f"Repo: <code>{self._repo_slug}</code>\n"
            f"PR #<b>{pr.number}</b>: {title}\n"
            f"Branch: <code>{branch}</code> → <code>{DEFAULT_BASE_BRANCH}</code>\n"
            f"<a href=\"{pr.html_url}\">View PR →</a>"
        )
        _send_telegram(tg_msg)

        return pr.number

    # ── Merge ────────────────────────────────────────────────────────────────

    def merge_pull_request(self, pr_number: int) -> bool:
        """
        Merge an approved pull request.

        The PR's head branch is validated against PROTECTED_BRANCHES as an
        extra safety check before merging.

        Args:
            pr_number: GitHub PR number.

        Returns:
            True on successful merge.

        Raises:
            RuntimeError: If the PR head branch is a protected branch (should never happen
                          if create_pull_request was used correctly, but checked defensively).
        """
        pr = self._repo.get_pull(pr_number)

        # Defensive cardinal rule check
        self._assert_not_protected(pr.head.ref)

        if pr.is_merged():
            log.warning("PR #%d is already merged — nothing to do", pr_number)
            return True

        if pr.state != "open":
            raise RuntimeError(
                f"PR #{pr_number} is not open (state={pr.state!r}). Cannot merge."
            )

        merge_result = pr.merge(merge_method=MERGE_METHOD)

        if merge_result.merged:
            log.info("PR #%d merged successfully (%s)", pr_number, merge_result.sha[:12])
            return True

        log.error("PR #%d merge failed: %s", pr_number, merge_result.message)
        return False

    # ── Rollback ─────────────────────────────────────────────────────────────

    def rollback_last_deploy(self, dry_run: bool = False) -> int:
        """
        Revert the most recent merge commit on main.

        Algorithm:
          1. Walk commits on main to find the last merge commit (≥ 2 parents).
          2. Create rollback/<timestamp> branch at the current HEAD of main.
          3. Build a revert commit whose tree matches the pre-merge parent's tree.
          4. Point the rollback branch at the revert commit.
          5. Open an emergency PR titled "🚨 EMERGENCY ROLLBACK".
          6. Send a Telegram alert.

        Args:
            dry_run: If True, log what would happen but make no changes.

        Returns:
            Emergency PR number (or 0 in dry_run mode).

        Raises:
            RuntimeError: If no merge commit is found on main.
        """
        log.warning("rollback_last_deploy() called — searching for last merge on main")

        # ── Find last merge commit ────────────────────────────────────────────
        last_merge_commit = None
        for gh_commit in self._repo.get_commits(sha=DEFAULT_BASE_BRANCH):
            raw = self._repo.get_git_commit(gh_commit.sha)
            if len(raw.parents) >= 2:
                last_merge_commit = gh_commit
                break

        if last_merge_commit is None:
            raise RuntimeError(
                f"No merge commit found on '{DEFAULT_BASE_BRANCH}' — nothing to roll back."
            )

        raw_merge    = self._repo.get_git_commit(last_merge_commit.sha)
        pre_merge_sha = raw_merge.parents[0].sha          # first parent = main before the merge
        pre_merge_raw = self._repo.get_git_commit(pre_merge_sha)
        pre_merge_tree_sha = pre_merge_raw.tree.sha

        merge_summary = last_merge_commit.commit.message.splitlines()[0]
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        rollback_branch = f"rollback/{ts}"

        log.warning(
            "Rolling back merge %s ('%s') → branch %s",
            last_merge_commit.sha[:12], merge_summary, rollback_branch,
        )

        if dry_run:
            print(
                f"\n  [DRY RUN] ROLLBACK\n"
                f"  Would revert : {last_merge_commit.sha[:12]} '{merge_summary}'\n"
                f"  Pre-merge SHA: {pre_merge_sha[:12]}\n"
                f"  New branch   : {rollback_branch}\n"
            )
            _send_telegram(
                f"🚨 <b>ROLLBACK DRY RUN</b>\n"
                f"Would revert: <code>{last_merge_commit.sha[:12]}</code>\n"
                f"Message: {merge_summary}",
                dry_run=True,
            )
            return 0

        # ── Create rollback branch at current main HEAD ───────────────────────
        main_sha = self._repo.get_git_ref(f"heads/{DEFAULT_BASE_BRANCH}").object.sha
        self._repo.create_git_ref(f"refs/heads/{rollback_branch}", main_sha)

        # ── Create revert commit (tree = pre-merge parent's tree) ─────────────
        pre_merge_tree = self._repo.get_git_tree(pre_merge_tree_sha)
        main_git_commit = self._repo.get_git_commit(main_sha)

        revert_commit = self._repo.create_git_commit(
            message = (
                f"revert: rollback to pre-merge state [{ts}]\n\n"
                f"Reverts merge commit {last_merge_commit.sha[:12]}\n"
                f"Original message: {merge_summary}"
            ),
            tree    = pre_merge_tree,
            parents = [main_git_commit],
        )

        # Point rollback branch at the revert commit
        rollback_ref = self._repo.get_git_ref(f"heads/{rollback_branch}")
        rollback_ref.edit(revert_commit.sha, force=True)
        log.info("Revert commit %s pushed to %s", revert_commit.sha[:12], rollback_branch)

        # ── Open emergency PR ─────────────────────────────────────────────────
        pr = self._repo.create_pull(
            title = f"🚨 EMERGENCY ROLLBACK — reverts {last_merge_commit.sha[:8]}",
            body  = (
                f"## 🚨 Emergency Rollback\n\n"
                f"This PR reverts the last merge commit on `{DEFAULT_BASE_BRANCH}`.\n\n"
                f"| Field | Value |\n"
                f"|---|---|\n"
                f"| **Reverted commit** | `{last_merge_commit.sha[:12]}` |\n"
                f"| **Reverted message** | {merge_summary} |\n"
                f"| **Rolled back to** | `{pre_merge_sha[:12]}` |\n"
                f"| **Rollback branch** | `{rollback_branch}` |\n"
                f"| **Triggered at** | {ts} UTC |\n\n"
                f"---\n\n"
                f"> ⚠️ **Review carefully before merging.** This will undo all changes "
                f"introduced by the reverted commit.\n\n"
                f"_Generated automatically by `governance/github_manager.py`_"
            ),
            head  = rollback_branch,
            base  = DEFAULT_BASE_BRANCH,
        )

        log.warning(
            "EMERGENCY ROLLBACK PR #%d created: %s",
            pr.number, pr.html_url,
        )

        # ── Telegram alert ────────────────────────────────────────────────────
        tg_msg = (
            f"🚨 <b>EMERGENCY ROLLBACK</b>\n"
            f"Repo: <code>{self._repo_slug}</code>\n"
            f"Reverted: <code>{last_merge_commit.sha[:12]}</code>\n"
            f"Message: {merge_summary}\n"
            f"PR #<b>{pr.number}</b>: <a href=\"{pr.html_url}\">Review &amp; merge →</a>\n\n"
            f"⚠️ Awaiting manual approval before merge."
        )
        _send_telegram(tg_msg)

        return pr.number

    # ── Introspection helpers ─────────────────────────────────────────────────

    def list_open_prs(self) -> list[dict]:
        """Return a list of open PR summaries for this repo."""
        return [
            {
                "number": pr.number,
                "title":  pr.title,
                "branch": pr.head.ref,
                "url":    pr.html_url,
                "created": pr.created_at.isoformat(),
            }
            for pr in self._repo.get_pulls(state="open", base=DEFAULT_BASE_BRANCH)
        ]

    def get_pr_status(self, pr_number: int) -> dict:
        """Return state, merge status, and review status for a PR."""
        pr = self._repo.get_pull(pr_number)
        return {
            "number":    pr.number,
            "title":     pr.title,
            "state":     pr.state,
            "merged":    pr.is_merged(),
            "mergeable": pr.mergeable,
            "url":       pr.html_url,
            "branch":    pr.head.ref,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Convenience factory
# ─────────────────────────────────────────────────────────────────────────────

def get_manager(
    token:    Optional[str] = None,
    repo_slug: Optional[str] = None,
) -> GitHubManager:
    """
    Create and return a GitHubManager using env-var defaults.
    Raises RuntimeError if GITHUB_TOKEN or GITHUB_REPO are not set.
    """
    return GitHubManager(token=token, repo_slug=repo_slug)


# ─────────────────────────────────────────────────────────────────────────────
# CLI — quick manual testing
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    parser = argparse.ArgumentParser(description="Bharat Intelligence GitHub Manager")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list-prs",           help="List open PRs")
    rp = sub.add_parser("rollback",      help="Rollback last deploy")
    rp.add_argument("--dry", action="store_true")
    mp = sub.add_parser("merge-pr",      help="Merge a PR by number")
    mp.add_argument("pr_number", type=int)

    args = parser.parse_args()
    mgr  = get_manager()

    if args.command == "list-prs":
        for pr in mgr.list_open_prs():
            print(f"  #{pr['number']}  {pr['title']}  [{pr['branch']}]  {pr['url']}")

    elif args.command == "rollback":
        pr_num = mgr.rollback_last_deploy(dry_run=args.dry)
        if pr_num:
            print(f"Emergency PR #{pr_num} created.")

    elif args.command == "merge-pr":
        ok = mgr.merge_pull_request(args.pr_number)
        print(f"PR #{args.pr_number} {'merged' if ok else 'FAILED'}.")

    else:
        parser.print_help()
