"""
tests/test_rec_outcome_seeder.py
================================
Unit tests for agents/rec_outcome_seeder.py (P5-C).

All Supabase calls are mocked — no network access.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.rec_outcome_seeder import _entry_price, run_seeder, print_coverage_report


# ─── _entry_price ─────────────────────────────────────────────────────────────

class TestEntryPrice:
    def test_midpoint_when_both_present(self):
        rec = {"entry_low": 100.0, "entry_high": 120.0}
        assert _entry_price(rec) == 110.0

    def test_uses_entry_low_when_high_missing(self):
        rec = {"entry_low": 100.0, "entry_high": None}
        assert _entry_price(rec) == 100.0

    def test_uses_entry_high_when_low_missing(self):
        rec = {"entry_low": None, "entry_high": 120.0}
        assert _entry_price(rec) == 120.0

    def test_falls_back_to_metadata_price(self):
        rec = {"entry_low": None, "entry_high": None, "metadata": {"price": 150.0}}
        assert _entry_price(rec) == 150.0

    def test_returns_none_when_no_price(self):
        rec = {"entry_low": None, "entry_high": None}
        assert _entry_price(rec) is None

    def test_metadata_price_as_string(self):
        rec = {"entry_low": None, "entry_high": None, "metadata": {"price": "200.5"}}
        assert _entry_price(rec) == 200.5

    def test_entry_low_takes_priority_over_metadata(self):
        rec = {"entry_low": 100.0, "entry_high": None, "metadata": {"price": 999.0}}
        assert _entry_price(rec) == 100.0


# ─── run_seeder (dry_run) ─────────────────────────────────────────────────────

def _make_mock_client(recs, existing_outcome_ids=None):
    """Build a mock Supabase client that returns given recs and outcome rows."""
    mock = MagicMock()

    existing_outcome_ids = existing_outcome_ids or []
    existing_rows = [{"rec_id": rid} for rid in existing_outcome_ids]

    # recommendations table
    rec_q = MagicMock()
    rec_q.execute.return_value.data = recs
    mock.table.return_value.select.return_value.order.return_value = rec_q

    # recommendation_outcomes table (existing)
    out_q = MagicMock()
    out_q.execute.return_value.data = existing_rows

    # Route based on table name
    def table_router(name):
        t = MagicMock()
        if name == "recommendations":
            t.select.return_value.order.return_value = rec_q
        elif name == "recommendation_outcomes":
            # first call = select existing; subsequent = insert
            t.select.return_value.execute.return_value.data = existing_rows
            t.insert.return_value.execute.return_value.data = [{}]
        return t

    mock.table.side_effect = table_router
    return mock


class TestRunSeederDryRun:
    """Dry-run mode: prints what would happen, no DB writes."""

    def _make_recs(self, n=3):
        base = date(2025, 1, 1)
        return [
            {
                "id": f"rec-{i}",
                "symbol": f"SYM{i}",
                "action": "BUY",
                "entry_low": 100.0 * (i + 1),
                "entry_high": 110.0 * (i + 1),
                "confidence": 70.0,
                "created_at": (base + timedelta(days=i)).isoformat() + "T06:00:00",
                "agent_signals": {},
                "gov_check": {},
                "metadata": None,
                "upside_pct": 20.0,
            }
            for i in range(n)
        ]

    def test_dry_run_returns_seeded_count(self):
        recs = self._make_recs(3)
        with patch("agents.rec_outcome_seeder._supabase") as mock_sb:
            mock_client = _make_mock_client(recs, existing_outcome_ids=[])
            mock_sb.return_value = mock_client
            result = run_seeder(dry_run=True)
        assert result["seeded"] == 3
        assert result["skipped"] == 0
        assert result["errors"] == []

    def test_dry_run_skips_existing(self):
        recs = self._make_recs(3)
        with patch("agents.rec_outcome_seeder._supabase") as mock_sb:
            mock_client = _make_mock_client(recs, existing_outcome_ids=["rec-0", "rec-1"])
            mock_sb.return_value = mock_client
            result = run_seeder(dry_run=True)
        assert result["seeded"] == 1    # only rec-2 is new
        assert result["skipped"] == 2

    def test_dry_run_no_recs(self):
        with patch("agents.rec_outcome_seeder._supabase") as mock_sb:
            mock_client = _make_mock_client([])
            mock_sb.return_value = mock_client
            result = run_seeder(dry_run=True)
        assert result["seeded"] == 0
        assert result["skipped"] == 0


class TestRunSeederLive:
    """Live mode: actually calls seed_pending_outcome."""

    def _make_recs(self, n=2):
        base = date(2025, 6, 1)
        return [
            {
                "id": f"rec-live-{i}",
                "symbol": f"LIVE{i}",
                "action": "BUY",
                "entry_low": 500.0,
                "entry_high": 550.0,
                "confidence": 75.0,
                "created_at": (base + timedelta(days=i * 30)).isoformat() + "T06:00:00",
                "agent_signals": {"technical": {"signal": "BUY"}},
                "gov_check": {"validation": {"composite_score": 78.5}},
                "metadata": None,
                "upside_pct": 25.0,
            }
            for i in range(n)
        ]

    def test_live_run_calls_seed(self):
        recs = self._make_recs(2)
        with (
            patch("agents.rec_outcome_seeder._supabase") as mock_sb,
            patch("agents.outcome_tracker.seed_pending_outcome") as mock_seed,
            patch("agents.outcome_tracker._fetch_price_on_date", return_value=23000.0),
        ):
            mock_client = _make_mock_client(recs, existing_outcome_ids=[])
            mock_sb.return_value = mock_client
            mock_seed.return_value = True

            result = run_seeder(dry_run=False)

        assert result["seeded"] == 2
        assert mock_seed.call_count == 2

    def test_live_run_passes_correct_symbol(self):
        recs = self._make_recs(1)
        with (
            patch("agents.rec_outcome_seeder._supabase") as mock_sb,
            patch("agents.outcome_tracker.seed_pending_outcome") as mock_seed,
            patch("agents.outcome_tracker._fetch_price_on_date", return_value=22000.0),
        ):
            mock_client = _make_mock_client(recs, existing_outcome_ids=[])
            mock_sb.return_value = mock_client
            mock_seed.return_value = True

            run_seeder(dry_run=False)

        call_kwargs = mock_seed.call_args
        assert call_kwargs.kwargs["symbol"] == "LIVE0"
        assert call_kwargs.kwargs["action"] == "BUY"

    def test_live_run_extracts_composite_score(self):
        recs = self._make_recs(1)
        with (
            patch("agents.rec_outcome_seeder._supabase") as mock_sb,
            patch("agents.outcome_tracker.seed_pending_outcome") as mock_seed,
            patch("agents.outcome_tracker._fetch_price_on_date", return_value=22000.0),
        ):
            mock_client = _make_mock_client(recs, existing_outcome_ids=[])
            mock_sb.return_value = mock_client
            mock_seed.return_value = True

            run_seeder(dry_run=False)

        kwargs = mock_seed.call_args.kwargs
        assert kwargs["composite_score"] == 78.5

    def test_live_run_skips_existing(self):
        recs = self._make_recs(2)
        with (
            patch("agents.rec_outcome_seeder._supabase") as mock_sb,
            patch("agents.outcome_tracker.seed_pending_outcome") as mock_seed,
        ):
            mock_client = _make_mock_client(recs, existing_outcome_ids=["rec-live-0"])
            mock_sb.return_value = mock_client
            mock_seed.return_value = True

            result = run_seeder(dry_run=False)

        assert result["seeded"] == 1    # only rec-live-1 is new
        assert result["skipped"] == 1
        assert mock_seed.call_count == 1

    def test_live_run_handles_seed_failure(self):
        recs = self._make_recs(1)
        with (
            patch("agents.rec_outcome_seeder._supabase") as mock_sb,
            patch("agents.outcome_tracker.seed_pending_outcome") as mock_seed,
            patch("agents.outcome_tracker._fetch_price_on_date", return_value=22000.0),
        ):
            mock_client = _make_mock_client(recs, existing_outcome_ids=[])
            mock_sb.return_value = mock_client
            mock_seed.return_value = False   # seed failed

            result = run_seeder(dry_run=False)

        assert result["seeded"] == 0
        assert result["skipped"] == 1
        assert len(result["errors"]) == 1

    def test_supabase_not_configured(self):
        with patch("agents.rec_outcome_seeder._supabase", return_value=None):
            result = run_seeder(dry_run=False)
        assert result["seeded"] == 0
        assert len(result["errors"]) >= 1

    def test_invalid_created_at_skipped(self):
        recs = [
            {
                "id": "bad-rec",
                "symbol": "BAD",
                "action": "BUY",
                "entry_low": 100.0,
                "entry_high": 110.0,
                "confidence": 70.0,
                "created_at": "not-a-date",   # invalid
                "agent_signals": {},
                "gov_check": {},
                "metadata": None,
                "upside_pct": 10.0,
            }
        ]
        with (
            patch("agents.rec_outcome_seeder._supabase") as mock_sb,
            patch("agents.outcome_tracker.seed_pending_outcome") as mock_seed,
        ):
            mock_client = _make_mock_client(recs, existing_outcome_ids=[])
            mock_sb.return_value = mock_client
            result = run_seeder(dry_run=False)
        assert result["skipped"] == 1
        assert mock_seed.call_count == 0


# ─── WP-004: composite_score + validation_kappa on every seeding path ────────

from agents.rec_outcome_seeder import outcome_scores  # noqa: E402

_DISCOVERY_REC = {
    "id": "disc-1", "symbol": "GRASIM", "action": "BUY", "is_discovery": True,
    "entry_low": 100.0, "entry_high": 102.0, "created_at": "2026-11-10T05:00:00+00:00",
    "metadata": {"signal_score": 81.0, "price": 101.0},
    "gov_check": {"flags": [], "passed": True, "note": "discovery_auto"},
}
_PORTFOLIO_REC = {
    "id": "port-1", "symbol": "TITAN", "action": "HOLD", "is_discovery": False,
    "entry_low": 3000.0, "entry_high": 3050.0, "created_at": "2026-11-14T00:48:00+00:00",
    "metadata": {"composite_score": 63.4, "data_quality": "FULL"},
    "gov_check": {"claims_checked": 6, "validation": {"aggregate_kappa": 0.519, "status": "QUALIFIED"}},
}


class TestOutcomeScores:
    def test_discovery_prefers_frozen_call_score(self):
        rec = {"is_discovery": True, "metadata": {"call_signal_score": 77.0, "signal_score": 90.0}}
        assert outcome_scores(rec) == (77.0, None)

    def test_discovery_falls_back_to_signal_score(self):
        assert outcome_scores(_DISCOVERY_REC) == (81.0, None)

    def test_portfolio_reads_persisted_composite_and_kappa(self):
        assert outcome_scores(_PORTFOLIO_REC) == (63.4, 0.519)

    def test_portfolio_in_memory_composite_wins(self):
        rec = {**_PORTFOLIO_REC, "composite_score": 70.0}
        assert outcome_scores(rec)[0] == 70.0

    def test_zero_score_is_kept_not_treated_as_missing(self):
        rec = {"is_discovery": False, "composite_score": 0.0}
        assert outcome_scores(rec) == (0.0, None)

    def test_legacy_validation_composite_fallback(self):
        rec = {"is_discovery": False, "gov_check": {"validation": {"composite_score": 78.5}}}
        assert outcome_scores(rec)[0] == 78.5

    def test_garbage_is_none(self):
        assert outcome_scores({"metadata": "x", "gov_check": None}) == (None, None)
        assert outcome_scores({"is_discovery": True, "metadata": {"signal_score": "nan"}}) == (None, None)


class TestSeederFillsBothFields:
    @pytest.mark.parametrize("rec,score,kappa", [
        (_DISCOVERY_REC, 81.0, None),
        (_PORTFOLIO_REC, 63.4, 0.519),
    ])
    def test_seeder_passes_score_and_kappa(self, rec, score, kappa):
        with (
            patch("agents.rec_outcome_seeder._supabase") as mock_sb,
            patch("agents.outcome_tracker.seed_pending_outcome", return_value=True) as mock_seed,
        ):
            mock_sb.return_value = _make_mock_client([rec], existing_outcome_ids=[])
            run_seeder(dry_run=False)
        kw = mock_seed.call_args.kwargs
        assert kw["composite_score"] == score
        assert kw["validation_kappa"] == kappa

    def test_seeder_selects_is_discovery(self):
        rec_table = MagicMock()
        rec_table.select.return_value.order.return_value.execute.return_value.data = []
        client = MagicMock()
        client.table.side_effect = lambda name: rec_table
        with patch("agents.rec_outcome_seeder._supabase", return_value=client):
            run_seeder(dry_run=True)
        cols = rec_table.select.call_args_list[0].args[0]
        assert "is_discovery" in cols


class TestOrchestratorSeedPath:
    """save_recs_node seeds the outcome row with the same fields, and a failure is loud."""

    def _state(self):
        rec = {
            "symbol": "TITAN.NS", "action": "HOLD", "confidence": 60.0, "risk_score": 40.0,
            "entry_low": 3000.0, "entry_high": 3050.0, "horizon_days": 90,
            "upside_pct": 10.0, "upside_confidence": 60.0, "danger_drop_pct": 15.0,
            "danger_confidence": 50.0, "headline": "h", "summary": "s",
            "agent_signals": {"technical": {"signal": "BUY", "score": 60}},
            "is_discovery": False, "composite_score": 63.4,
            "gov_check": {"claims_checked": 6, "validation": {"aggregate_kappa": 0.519}},
        }
        return {"recommendations": [rec], "errors": [], "dry_run": False, "symbol_results": {}}

    def _client(self):
        client = MagicMock()
        client.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{"id": "r1"}])
        return client

    def test_seed_gets_score_and_kappa_and_metadata_persists_composite(self):
        import asyncio
        from scheduler import orchestrator as orch
        client = self._client()
        with (
            patch.object(orch, "_supabase", return_value=client),
            patch("agents.outcome_tracker.seed_pending_outcome", return_value=True) as mock_seed,
        ):
            out = asyncio.run(orch.save_recs_node(self._state()))
        kw = mock_seed.call_args.kwargs
        assert (kw["composite_score"], kw["validation_kappa"]) == (63.4, 0.519)
        inserted = client.table.return_value.insert.call_args_list[0].args[0]
        assert inserted["metadata"]["composite_score"] == 63.4
        assert not [e for e in out["errors"] if "outcome_seed" in e]

    def test_seed_exception_logged_at_warning_and_recorded(self, caplog):
        import asyncio
        import logging
        from scheduler import orchestrator as orch
        with (
            patch.object(orch, "_supabase", return_value=self._client()),
            patch("agents.outcome_tracker.seed_pending_outcome", side_effect=RuntimeError("boom")),
            caplog.at_level(logging.WARNING),
        ):
            out = asyncio.run(orch.save_recs_node(self._state()))
        assert any(r.levelno == logging.WARNING and "outcome seed failed" in r.getMessage()
                   for r in caplog.records)
        assert any("outcome_seed TITAN.NS: boom" in e for e in out["errors"])

    def test_seed_returning_false_is_recorded(self):
        import asyncio
        from scheduler import orchestrator as orch
        with (
            patch.object(orch, "_supabase", return_value=self._client()),
            patch("agents.outcome_tracker.seed_pending_outcome", return_value=False),
        ):
            out = asyncio.run(orch.save_recs_node(self._state()))
        assert any("outcome_seed TITAN.NS" in e for e in out["errors"])
