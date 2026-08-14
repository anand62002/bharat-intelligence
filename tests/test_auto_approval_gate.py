"""
tests/test_auto_approval_gate.py — governance auto-approval gate.

The gate decides which LLM-authored proposals get a PR opened automatically,
so every boundary condition is pinned here.
"""

from unittest.mock import MagicMock, patch

import pytest

from governance.research_agent import (
    AUTO_APPROVE_MAX_AGAINST,
    AUTO_APPROVE_MIN_FOR_RATIO,
    AUTO_APPROVE_MIN_VOTES,
    auto_approve_proposals,
    evaluate_auto_approval,
)


def _votes(for_n=8, against_n=0, abstain_n=0):
    return (
        [{"agent": f"a{i}", "stance": "FOR"} for i in range(for_n)]
        + [{"agent": f"b{i}", "stance": "AGAINST"} for i in range(against_n)]
        + [{"agent": f"c{i}", "stance": "ABSTAIN"} for i in range(abstain_n)]
    )


def _proposal(**over):
    base = {
        "id": "p1", "title": "Test proposal", "status": "pending",
        "cost_impact": "low", "debate_log": _votes(), "metadata": {},
        "pr_url": None, "relevance": 90,
    }
    base.update(over)
    return base


class TestSupermajority:
    def test_unanimous_passes(self):
        ok, reason = evaluate_auto_approval(_proposal())
        assert ok, reason

    def test_single_dissenter_tolerated(self):
        ok, _ = evaluate_auto_approval(_proposal(debate_log=_votes(7, 1)))
        assert ok

    def test_two_dissenters_rejected(self):
        ok, reason = evaluate_auto_approval(_proposal(debate_log=_votes(8, 2)))
        assert not ok and "AGAINST" in reason

    def test_minimum_passing_shape(self):
        """
        Tightest configuration that still clears every check:
        4 FOR / 1 AGAINST = 5 decisive votes at 80% support.
        """
        ok, reason = evaluate_auto_approval(_proposal(debate_log=_votes(4, 1)))
        assert ok, reason

    def test_dissent_cap_binds_before_the_ratio_bar(self):
        """
        Documents an interaction in the current constants: with MAX_AGAINST=1
        and MIN_VOTES=5, the lowest reachable support is 4/5 = 80%, which is
        already above MIN_FOR_RATIO=75%. So the ratio check never rejects
        anything the dissent cap has not already rejected — it is
        defence-in-depth for future constant changes, not live logic.

        This test fails if someone raises MAX_AGAINST without revisiting the
        ratio, which is exactly when the ratio starts mattering.
        """
        min_reachable_ratio = (
            AUTO_APPROVE_MIN_VOTES - AUTO_APPROVE_MAX_AGAINST
        ) / AUTO_APPROVE_MIN_VOTES
        assert min_reachable_ratio >= AUTO_APPROVE_MIN_FOR_RATIO, (
            "MAX_AGAINST is now loose enough that MIN_FOR_RATIO governs — "
            "re-check both constants together"
        )

    def test_split_vote_rejected(self):
        ok, reason = evaluate_auto_approval(_proposal(debate_log=_votes(5, 2)))
        assert not ok

    def test_abstains_excluded_from_denominator(self):
        """6 FOR / 0 AGAINST / 4 ABSTAIN is unanimous among decisive voters."""
        ok, reason = evaluate_auto_approval(_proposal(debate_log=_votes(6, 0, 4)))
        assert ok, reason

    def test_too_few_decisive_votes_rejected(self):
        n = AUTO_APPROVE_MIN_VOTES - 1
        ok, reason = evaluate_auto_approval(_proposal(debate_log=_votes(n, 0)))
        assert not ok and "decisive votes" in reason

    def test_no_debate_rejected(self):
        ok, reason = evaluate_auto_approval(_proposal(debate_log=[]))
        assert not ok and "no debate" in reason.lower()


class TestCostGate:
    @pytest.mark.parametrize("cost", ["low", "none", "free", "zero", "LOW", " Low "])
    def test_zero_cost_variants_pass(self, cost):
        ok, reason = evaluate_auto_approval(_proposal(cost_impact=cost))
        assert ok, reason

    @pytest.mark.parametrize("cost", ["medium", "high"])
    def test_paid_cost_rejected(self, cost):
        ok, reason = evaluate_auto_approval(_proposal(cost_impact=cost))
        assert not ok and "cost_impact" in reason

    def test_missing_cost_defaults_to_medium_and_is_rejected(self):
        p = _proposal(); p.pop("cost_impact")
        ok, reason = evaluate_auto_approval(p)
        assert not ok, "absent cost must not be treated as free"

    def test_requires_paid_data_rejected(self):
        ok, reason = evaluate_auto_approval(
            _proposal(metadata={"requires_paid_data": True})
        )
        assert not ok and "paid data" in reason


class TestStatusGuards:
    @pytest.mark.parametrize("status", ["approved", "rejected", "implemented"])
    def test_non_pending_rejected(self, status):
        ok, reason = evaluate_auto_approval(_proposal(status=status))
        assert not ok and "pending" in reason

    def test_existing_pr_rejected(self):
        ok, reason = evaluate_auto_approval(
            _proposal(pr_url="https://github.com/x/y/pull/1")
        )
        assert not ok and "PR already exists" in reason

    def test_never_raises_on_malformed_input(self):
        for bad in ({}, {"debate_log": None}, {"status": None},
                    {"status": "pending", "debate_log": [{"stance": None}]}):
            ok, reason = evaluate_auto_approval(bad)
            assert isinstance(ok, bool) and isinstance(reason, str)


class TestRunner:
    def _client(self, rows):
        c = MagicMock()
        (c.table.return_value.select.return_value
          .eq.return_value.order.return_value.execute.return_value.data) = rows
        return c

    def test_dry_run_opens_nothing(self):
        with patch("governance.research_agent._supabase",
                   return_value=self._client([_proposal()])), \
             patch("governance.research_agent.approve_proposal") as ap:
            res = auto_approve_proposals(dry_run=True)
        ap.assert_not_called()
        assert res["approved"] == 1 and res["checked"] == 1

    def test_live_run_opens_pr_for_eligible_only(self):
        rows = [
            _proposal(id="good", title="Eligible"),
            _proposal(id="pricey", title="Costly", cost_impact="high"),
            _proposal(id="split", title="Contested", debate_log=_votes(4, 4)),
        ]
        with patch("governance.research_agent._supabase",
                   return_value=self._client(rows)), \
             patch("governance.research_agent.approve_proposal",
                   return_value={"pr_number": 7,
                                 "pr_url": "https://github.com/x/y/pull/7"}) as ap, \
             patch("governance.research_agent._send_telegram"):
            res = auto_approve_proposals(dry_run=False)

        ap.assert_called_once_with("good", dry_run=False)
        assert res["approved"] == 1
        assert res["skipped"] == 2
        assert res["pr_urls"] == ["https://github.com/x/y/pull/7"]

    def test_per_run_cap_defers_extras(self):
        rows = [_proposal(id=f"p{i}", title=f"P{i}") for i in range(6)]
        with patch("governance.research_agent._supabase",
                   return_value=self._client(rows)), \
             patch("governance.research_agent.approve_proposal",
                   return_value={"pr_url": "u"}), \
             patch("governance.research_agent._send_telegram"):
            res = auto_approve_proposals(dry_run=False, limit=2)
        assert res["approved"] == 2
        assert res["skipped"] == 4

    def test_pr_failure_is_recorded_not_raised(self):
        with patch("governance.research_agent._supabase",
                   return_value=self._client([_proposal()])), \
             patch("governance.research_agent.approve_proposal",
                   return_value={"error": "github down"}), \
             patch("governance.research_agent._send_telegram"):
            res = auto_approve_proposals(dry_run=False)
        assert res["approved"] == 0
        assert res["errors"] and "github down" in res["errors"][0]

    def test_exception_during_approval_does_not_abort_the_batch(self):
        rows = [_proposal(id="boom", title="Boom"), _proposal(id="ok", title="Ok")]
        with patch("governance.research_agent._supabase",
                   return_value=self._client(rows)), \
             patch("governance.research_agent.approve_proposal",
                   side_effect=[RuntimeError("net"), {"pr_url": "u"}]), \
             patch("governance.research_agent._send_telegram"):
            res = auto_approve_proposals(dry_run=False)
        assert res["approved"] == 1
        assert len(res["errors"]) == 1

    def test_no_supabase_returns_error_not_crash(self):
        with patch("governance.research_agent._supabase", return_value=None):
            res = auto_approve_proposals()
        assert res["approved"] == 0 and res["errors"]

    def test_decisions_recorded_for_every_proposal(self):
        rows = [_proposal(id="a"), _proposal(id="b", cost_impact="high")]
        with patch("governance.research_agent._supabase",
                   return_value=self._client(rows)):
            res = auto_approve_proposals(dry_run=True)
        assert len(res["decisions"]) == 2
        assert [d["eligible"] for d in res["decisions"]] == [True, False]


class TestGateConstants:
    def test_bar_is_a_supermajority_not_a_simple_majority(self):
        assert AUTO_APPROVE_MIN_FOR_RATIO >= 0.66
        assert AUTO_APPROVE_MAX_AGAINST <= 1
        assert AUTO_APPROVE_MIN_VOTES >= 3


# ---------------------------------------------------------------------------
# Auto-MERGE — lands code on main unreviewed, so the bar is higher
# ---------------------------------------------------------------------------

from governance.research_agent import (          # noqa: E402
    auto_merge_approved_prs,
    auto_merge_enabled,
    evaluate_auto_merge,
)


def _approved(**over):
    base = _proposal(status="approved",
                     pr_url="https://github.com/anand62002/bharat-intelligence/pull/42")
    base.update(over)
    return base


class TestAutoMergeVoteGate:
    def test_unanimous_passes(self):
        ok, reason = evaluate_auto_merge(_approved())
        assert ok and "unanimous" in reason

    def test_single_dissenter_blocks_merge_even_though_it_allowed_the_PR(self):
        """7 FOR / 1 AGAINST opens a PR but must never auto-merge."""
        p = _approved(debate_log=_votes(7, 1))
        assert evaluate_auto_approval(_proposal(debate_log=_votes(7, 1)))[0], \
            "sanity: this shape should still qualify for a PR"
        ok, reason = evaluate_auto_merge(p)
        assert not ok and "unanimous" in reason

    def test_pending_proposal_not_merged(self):
        ok, reason = evaluate_auto_merge(_approved(status="pending"))
        assert not ok and "not approved" in reason

    def test_missing_pr_url_rejected(self):
        ok, reason = evaluate_auto_merge(_approved(pr_url=None))
        assert not ok and "no PR" in reason

    def test_cost_and_paid_data_still_apply(self):
        assert not evaluate_auto_merge(_approved(cost_impact="high"))[0]
        assert not evaluate_auto_merge(
            _approved(metadata={"requires_paid_data": True}))[0]


class TestAutoMergeFlag:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("GOVERNANCE_AUTO_MERGE", raising=False)
        assert auto_merge_enabled() is False
        res = auto_merge_approved_prs()
        assert res["merged"] == 0 and res["errors"]

    @pytest.mark.parametrize("val", ["true", "TRUE", "1", "yes", "on"])
    def test_enabled_values(self, monkeypatch, val):
        monkeypatch.setenv("GOVERNANCE_AUTO_MERGE", val)
        assert auto_merge_enabled() is True

    @pytest.mark.parametrize("val", ["false", "0", "no", ""])
    def test_disabled_values(self, monkeypatch, val):
        monkeypatch.setenv("GOVERNANCE_AUTO_MERGE", val)
        assert auto_merge_enabled() is False


class TestAutoMergeCIGate:
    def _run(self, monkeypatch, checks, proposals=None, dry_run=False):
        monkeypatch.setenv("GOVERNANCE_AUTO_MERGE", "true")
        rows = proposals if proposals is not None else [_approved()]
        client = MagicMock()
        (client.table.return_value.select.return_value
               .eq.return_value.order.return_value.execute.return_value.data) = rows
        mgr = MagicMock()
        mgr.get_pr_checks.return_value = checks
        mgr.merge_pull_request.return_value = True
        with patch("governance.research_agent._supabase", return_value=client), \
             patch("governance.github_manager.get_manager", return_value=mgr), \
             patch("governance.research_agent._send_telegram"):
            res = auto_merge_approved_prs(dry_run=dry_run)
        return res, mgr

    def test_green_ci_merges(self, monkeypatch):
        res, mgr = self._run(monkeypatch,
            {"total": 1, "success": 1, "failed": 0, "pending": 0, "all_green": True})
        mgr.merge_pull_request.assert_called_once_with(42)
        assert res["merged"] == 1 and res["merged_prs"] == [42]

    def test_failing_ci_blocks_merge(self, monkeypatch):
        res, mgr = self._run(monkeypatch,
            {"total": 1, "success": 0, "failed": 1, "pending": 0, "all_green": False})
        mgr.merge_pull_request.assert_not_called()
        assert res["merged"] == 0 and "CI not green" in res["decisions"][0]["reason"]

    def test_pending_ci_blocks_merge(self, monkeypatch):
        res, mgr = self._run(monkeypatch,
            {"total": 1, "success": 0, "failed": 0, "pending": 1, "all_green": False})
        mgr.merge_pull_request.assert_not_called()
        assert res["merged"] == 0

    def test_no_checks_fails_closed(self, monkeypatch):
        """A PR with zero checks must NOT be read as 'nothing failed'."""
        res, mgr = self._run(monkeypatch,
            {"total": 0, "success": 0, "failed": 0, "pending": 0, "all_green": False})
        mgr.merge_pull_request.assert_not_called()
        assert "failing closed" in res["decisions"][0]["reason"]

    def test_dry_run_never_merges(self, monkeypatch):
        res, mgr = self._run(monkeypatch,
            {"total": 1, "success": 1, "failed": 0, "pending": 0, "all_green": True},
            dry_run=True)
        mgr.merge_pull_request.assert_not_called()
        assert res["merged"] == 1

    def test_per_run_cap(self, monkeypatch):
        rows = [_approved(id=f"p{i}",
                          pr_url=f"https://github.com/o/r/pull/{i}") for i in range(5)]
        res, mgr = self._run(monkeypatch,
            {"total": 1, "success": 1, "failed": 0, "pending": 0, "all_green": True},
            proposals=rows)
        assert res["merged"] == 2, "per-run cap must bound blast radius"

    def test_ci_lookup_error_blocks_merge(self, monkeypatch):
        monkeypatch.setenv("GOVERNANCE_AUTO_MERGE", "true")
        client = MagicMock()
        (client.table.return_value.select.return_value
               .eq.return_value.order.return_value.execute.return_value.data) = [_approved()]
        mgr = MagicMock()
        mgr.get_pr_checks.side_effect = RuntimeError("github 503")
        with patch("governance.research_agent._supabase", return_value=client), \
             patch("governance.github_manager.get_manager", return_value=mgr):
            res = auto_merge_approved_prs(dry_run=False)
        mgr.merge_pull_request.assert_not_called()
        assert res["merged"] == 0
