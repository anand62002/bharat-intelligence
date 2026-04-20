"""
tests/test_performance_tracker.py — Unit tests for scheduler/performance_tracker.py

Coverage:
  TestPerformanceResult       — dataclass construction, to_dict, defaults
  TestIsSignalCorrect         — BUY/SELL/HOLD/NO_DATA/edge cases
  TestEvaluateRec             — price fetch integration, agent signal loop
  TestComputeAccuracyStats    — aggregation, mixed results, no data
  TestFindMaturedRecs         — date window filter, DB query, error handling
  TestUpdateAccuracy90d       — DB insert, dry_run, empty stats, errors
  TestRunPerformanceTracker   — full run(); no Supabase; no matured recs; errors
  TestBackfill                — date range delegation, window_days computation
  TestGetCurrentAccuracy      — DB query, absent data, Supabase failure

Run:
    pytest tests/test_performance_tracker.py -v
"""

import os
import sys
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scheduler.performance_tracker import (
    DEFAULT_ACCURACY_BASELINE,
    DIRECTIONAL_BUFFER_PCT,
    FETCH_BATCH_SIZE,
    HOLD_BAND_PCT,
    MAX_HORIZON_DAYS,
    MIN_HORIZON_DAYS,
    PerformanceResult,
    _compute_accuracy_stats,
    _evaluate_rec,
    _find_matured_recs,
    _is_signal_correct,
    _supabase_client,
    _update_accuracy_90d,
    backfill,
    get_current_accuracy,
    run,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_rec(
    symbol:       str = "RELIANCE.NS",
    created_at:   str | None = None,
    horizon_days: int = 90,
    agent_signals: dict | None = None,
    rec_id:       str = "rec-001",
) -> dict:
    """Build a minimal recommendation dict for testing."""
    if created_at is None:
        created_at = (date.today() - timedelta(days=horizon_days + 5)).isoformat() + "T10:00:00Z"
    if agent_signals is None:
        agent_signals = {
            "technical":    {"signal": "BUY"},
            "fundamental":  {"signal": "BUY"},
        }
    return {
        "id":           rec_id,
        "symbol":       symbol,
        "created_at":   created_at,
        "horizon_days": horizon_days,
        "agent_signals": agent_signals,
    }


def _mock_supabase_client() -> MagicMock:
    """Build a chainable MagicMock for a Supabase client."""
    client = MagicMock()
    chain  = MagicMock()
    chain.select.return_value  = chain   # ← required: .select() must return chain
    chain.gte.return_value     = chain
    chain.lte.return_value     = chain
    chain.eq.return_value      = chain
    chain.order.return_value   = chain
    chain.limit.return_value   = chain
    chain.execute.return_value = MagicMock(data=[])
    chain.insert.return_value  = chain
    client.table.return_value  = chain
    return client, chain


# ──────────────────────────────────────────────────────────────────────────────
# TestPerformanceResult
# ──────────────────────────────────────────────────────────────────────────────

class TestPerformanceResult:
    def test_defaults(self):
        r = PerformanceResult(
            run_date="2025-04-19", lookback_days=2,
            recs_evaluated=0, agents_updated=0
        )
        assert r.accuracy_by_agent == {}
        assert r.errors == []
        assert r.dry_run is False
        assert r.duration_seconds == 0.0

    def test_to_dict_round_trip(self):
        r = PerformanceResult(
            run_date="2025-04-19", lookback_days=2,
            recs_evaluated=5, agents_updated=3,
            accuracy_by_agent={"technical": 80.0},
            errors=["e1"],
            dry_run=True,
            duration_seconds=1.23,
        )
        d = r.to_dict()
        assert d["run_date"] == "2025-04-19"
        assert d["recs_evaluated"] == 5
        assert d["agents_updated"] == 3
        assert d["accuracy_by_agent"] == {"technical": 80.0}
        assert d["errors"] == ["e1"]
        assert d["dry_run"] is True
        assert d["duration_seconds"] == 1.23

    def test_to_dict_contains_all_fields(self):
        r = PerformanceResult(run_date="x", lookback_days=1, recs_evaluated=0, agents_updated=0)
        keys = set(r.to_dict().keys())
        assert {"run_date", "lookback_days", "recs_evaluated", "agents_updated",
                "accuracy_by_agent", "errors", "dry_run", "duration_seconds"} == keys


# ──────────────────────────────────────────────────────────────────────────────
# TestIsSignalCorrect
# ──────────────────────────────────────────────────────────────────────────────

class TestIsSignalCorrect:
    # BUY — correct when return > DIRECTIONAL_BUFFER_PCT
    def test_buy_correct(self):
        assert _is_signal_correct("BUY", DIRECTIONAL_BUFFER_PCT + 0.1) is True

    def test_buy_wrong_negative(self):
        assert _is_signal_correct("BUY", -5.0) is False

    def test_buy_at_buffer_edge_not_correct(self):
        assert _is_signal_correct("BUY", DIRECTIONAL_BUFFER_PCT) is False

    # SELL / AVOID — correct when return < -DIRECTIONAL_BUFFER_PCT
    def test_sell_correct(self):
        assert _is_signal_correct("SELL", -(DIRECTIONAL_BUFFER_PCT + 0.1)) is True

    def test_sell_wrong_positive(self):
        assert _is_signal_correct("SELL", 5.0) is False

    def test_avoid_correct(self):
        assert _is_signal_correct("AVOID", -3.0) is True

    # HOLD — correct when abs(return) <= HOLD_BAND_PCT
    def test_hold_correct_positive(self):
        assert _is_signal_correct("HOLD", HOLD_BAND_PCT - 0.1) is True

    def test_hold_correct_negative(self):
        assert _is_signal_correct("HOLD", -(HOLD_BAND_PCT - 0.1)) is True

    def test_hold_wrong_large_move(self):
        assert _is_signal_correct("HOLD", HOLD_BAND_PCT + 0.1) is False

    # Unevaluable signals
    def test_no_data_returns_none(self):
        assert _is_signal_correct("NO_DATA", 10.0) is None

    def test_empty_signal_returns_none(self):
        assert _is_signal_correct("", 10.0) is None

    def test_neutral_returns_none(self):
        assert _is_signal_correct("NEUTRAL", 5.0) is None

    def test_unknown_signal_returns_none(self):
        assert _is_signal_correct("WATCH", 5.0) is None

    # Case insensitivity
    def test_lowercase_buy(self):
        assert _is_signal_correct("buy", 5.0) is True

    def test_lowercase_sell(self):
        assert _is_signal_correct("sell", -5.0) is True


# ──────────────────────────────────────────────────────────────────────────────
# TestEvaluateRec
# ──────────────────────────────────────────────────────────────────────────────

class TestEvaluateRec:
    def _make_mock_price(self, entry: float, horizon: float):
        """Return a side_effect fn that returns entry then horizon price."""
        calls = iter([entry, horizon])
        return lambda *a, **kw: next(calls)

    def test_buy_correct_evaluation(self):
        rec = _make_rec(
            agent_signals={"technical": {"signal": "BUY"}, "fundamental": "SELL"}
        )
        with patch(
            "scheduler.performance_tracker._fetch_price_on_date",
            side_effect=self._make_mock_price(100.0, 110.0),
        ):
            result = _evaluate_rec(rec)
        assert result["technical"] is True     # BUY + +10% return = correct
        assert result["fundamental"] is False  # SELL + +10% return = wrong

    def test_sell_correct_evaluation(self):
        rec = _make_rec(
            agent_signals={"sentiment": {"signal": "SELL"}}
        )
        with patch(
            "scheduler.performance_tracker._fetch_price_on_date",
            side_effect=self._make_mock_price(100.0, 90.0),
        ):
            result = _evaluate_rec(rec)
        assert result["sentiment"] is True   # SELL + -10% return = correct

    def test_hold_correct_evaluation(self):
        rec = _make_rec(
            agent_signals={"macro": {"signal": "HOLD"}}
        )
        with patch(
            "scheduler.performance_tracker._fetch_price_on_date",
            side_effect=self._make_mock_price(100.0, 105.0),
        ):
            result = _evaluate_rec(rec)
        assert result["macro"] is True   # HOLD + +5% (within ±10%) = correct

    def test_no_data_signal_evaluates_to_none(self):
        rec = _make_rec(agent_signals={"institutional": {"signal": "NO_DATA"}})
        with patch(
            "scheduler.performance_tracker._fetch_price_on_date",
            side_effect=self._make_mock_price(100.0, 120.0),
        ):
            result = _evaluate_rec(rec)
        assert result["institutional"] is None

    def test_entry_price_unavailable_returns_empty(self):
        rec = _make_rec()
        with patch(
            "scheduler.performance_tracker._fetch_price_on_date",
            return_value=None,
        ):
            result = _evaluate_rec(rec)
        assert result == {}

    def test_horizon_price_unavailable_returns_empty(self):
        rec = _make_rec()
        prices = [100.0, None]
        with patch(
            "scheduler.performance_tracker._fetch_price_on_date",
            side_effect=prices,
        ):
            result = _evaluate_rec(rec)
        assert result == {}

    def test_entry_price_zero_returns_empty(self):
        rec = _make_rec()
        with patch(
            "scheduler.performance_tracker._fetch_price_on_date",
            side_effect=[0.0, 110.0],
        ):
            result = _evaluate_rec(rec)
        assert result == {}

    def test_invalid_created_at_returns_empty(self):
        rec = _make_rec(created_at="not-a-date")
        result = _evaluate_rec(rec)
        assert result == {}

    def test_string_signal_value(self):
        """agent_signals values can be plain strings (not dicts)."""
        rec = _make_rec(agent_signals={"historical_rag": "BUY"})
        with patch(
            "scheduler.performance_tracker._fetch_price_on_date",
            side_effect=self._make_mock_price(100.0, 108.0),
        ):
            result = _evaluate_rec(rec)
        assert result["historical_rag"] is True

    def test_none_agent_signals(self):
        rec = _make_rec()
        rec["agent_signals"] = None
        with patch(
            "scheduler.performance_tracker._fetch_price_on_date",
            side_effect=self._make_mock_price(100.0, 110.0),
        ):
            result = _evaluate_rec(rec)
        assert result == {}

    def test_datetime_object_created_at(self):
        """created_at may be a datetime object, not just a string."""
        rec = _make_rec()
        rec["created_at"] = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        with patch(
            "scheduler.performance_tracker._fetch_price_on_date",
            side_effect=self._make_mock_price(200.0, 220.0),
        ):
            result = _evaluate_rec(rec)
        assert isinstance(result, dict)
        assert len(result) > 0


# ──────────────────────────────────────────────────────────────────────────────
# TestComputeAccuracyStats
# ──────────────────────────────────────────────────────────────────────────────

class TestComputeAccuracyStats:
    def test_perfect_accuracy(self):
        evals = [
            {"technical": True, "fundamental": True},
            {"technical": True, "fundamental": True},
        ]
        stats = _compute_accuracy_stats(evals)
        assert stats["technical"]["accuracy_90d"] == 100.0
        assert stats["technical"]["correct"] == 2
        assert stats["technical"]["total"] == 2

    def test_zero_accuracy(self):
        evals = [{"technical": False}, {"technical": False}]
        stats = _compute_accuracy_stats(evals)
        assert stats["technical"]["accuracy_90d"] == 0.0

    def test_mixed_accuracy(self):
        evals = [
            {"technical": True},
            {"technical": False},
            {"technical": True},
            {"technical": True},
        ]
        stats = _compute_accuracy_stats(evals)
        assert stats["technical"]["accuracy_90d"] == 75.0
        assert stats["technical"]["correct"] == 3
        assert stats["technical"]["total"] == 4

    def test_none_results_excluded(self):
        evals = [
            {"technical": None, "fundamental": True},
            {"technical": True, "fundamental": None},
        ]
        stats = _compute_accuracy_stats(evals)
        assert stats["technical"]["total"] == 1
        assert stats["fundamental"]["total"] == 1

    def test_empty_evaluations(self):
        stats = _compute_accuracy_stats([])
        assert stats == {}

    def test_multiple_agents(self):
        evals = [
            {"technical": True, "macro": False},
            {"technical": True, "macro": True},
        ]
        stats = _compute_accuracy_stats(evals)
        assert stats["technical"]["accuracy_90d"] == 100.0
        assert stats["macro"]["accuracy_90d"] == 50.0

    def test_all_none_evaluations(self):
        evals = [{"technical": None}, {"technical": None}]
        stats = _compute_accuracy_stats(evals)
        assert "technical" not in stats

    def test_accuracy_rounded_to_two_decimals(self):
        # 1/3 = 33.33...
        evals = [{"technical": True}, {"technical": False}, {"technical": False}]
        stats = _compute_accuracy_stats(evals)
        assert stats["technical"]["accuracy_90d"] == 33.33


# ──────────────────────────────────────────────────────────────────────────────
# TestFindMaturedRecs
# ──────────────────────────────────────────────────────────────────────────────

class TestFindMaturedRecs:
    def _make_rec_row(
        self,
        symbol: str,
        created_offset_days: int,   # days before today
        horizon_days: int = 90,
    ) -> dict:
        created = (date.today() - timedelta(days=created_offset_days)).isoformat() + "T00:00:00Z"
        return {
            "id": f"rec-{symbol}",
            "symbol": symbol,
            "created_at": created,
            "horizon_days": horizon_days,
            "agent_signals": {"technical": {"signal": "BUY"}},
        }

    def test_filters_to_matured_within_window(self):
        today = date.today()
        # This rec matures exactly today (created 90 days ago, horizon=90)
        rec_today = self._make_rec_row("RELIANCE.NS", created_offset_days=90, horizon_days=90)
        # This rec matures 10 days from now (not yet matured)
        rec_future = self._make_rec_row("TCS.NS", created_offset_days=80, horizon_days=90)
        # This rec matured 5 days ago (outside 2-day window)
        rec_past = self._make_rec_row("INFY.NS", created_offset_days=95, horizon_days=90)

        client = MagicMock()
        chain  = MagicMock()
        chain.select.return_value  = chain   # ← required
        chain.gte.return_value     = chain
        chain.lte.return_value     = chain
        chain.order.return_value   = chain
        chain.limit.return_value   = chain
        chain.execute.return_value = MagicMock(data=[rec_today, rec_future, rec_past])
        client.table.return_value  = chain

        cutoff_start = today - timedelta(days=2)
        cutoff_end   = today

        result = _find_matured_recs(client, cutoff_start, cutoff_end)

        # Only rec_today matures in [today-2, today]
        symbols = [r["symbol"] for r in result]
        assert "RELIANCE.NS" in symbols
        assert "TCS.NS" not in symbols   # not yet matured
        assert "INFY.NS" not in symbols  # matured before window

    def test_empty_db_result(self):
        client, chain = _mock_supabase_client()
        chain.execute.return_value = MagicMock(data=[])
        result = _find_matured_recs(client, date.today() - timedelta(2), date.today())
        assert result == []

    def test_db_exception_returns_empty(self):
        client = MagicMock()
        chain  = MagicMock()
        chain.select.return_value  = chain   # ← required
        chain.gte.return_value     = chain
        chain.lte.return_value     = chain
        chain.order.return_value   = chain
        chain.limit.return_value   = chain
        chain.execute.side_effect  = Exception("DB timeout")
        client.table.return_value  = chain

        result = _find_matured_recs(client, date.today() - timedelta(2), date.today())
        assert result == []

    def test_impossible_date_range_returns_empty(self):
        """When cutoff_end - MIN_HORIZON < cutoff_start - MAX_HORIZON, impossible range."""
        client = MagicMock()
        # cutoff window is very small and horizon constraints create empty range
        from_date = date.today() - timedelta(days=5)
        to_date   = date.today() - timedelta(days=10)  # to < from → impossible
        result = _find_matured_recs(client, from_date, to_date)
        # earliest_created > latest_created triggers early return
        assert result == []

    def test_malformed_created_at_skipped(self):
        rec_bad = {"id": "bad", "symbol": "X", "created_at": "not-a-date",
                   "horizon_days": 90, "agent_signals": {}}
        today = date.today()
        rec_good = {
            "id": "good", "symbol": "RELIANCE.NS",
            "created_at": (today - timedelta(days=90)).isoformat() + "T00:00:00Z",
            "horizon_days": 90, "agent_signals": {"technical": {"signal": "BUY"}},
        }
        client = MagicMock()
        chain  = MagicMock()
        chain.select.return_value  = chain   # ← required
        chain.gte.return_value     = chain
        chain.lte.return_value     = chain
        chain.order.return_value   = chain
        chain.limit.return_value   = chain
        chain.execute.return_value = MagicMock(data=[rec_bad, rec_good])
        client.table.return_value  = chain

        result = _find_matured_recs(client, today - timedelta(2), today)
        symbols = [r["symbol"] for r in result]
        assert "X" not in symbols
        assert "RELIANCE.NS" in symbols


# ──────────────────────────────────────────────────────────────────────────────
# TestUpdateAccuracy90d
# ──────────────────────────────────────────────────────────────────────────────

class TestUpdateAccuracy90d:
    def test_dry_run_prints_and_returns_count(self, capsys):
        agent_stats = {
            "technical":   {"correct": 8, "total": 10, "accuracy_90d": 80.0},
            "fundamental": {"correct": 6, "total": 10, "accuracy_90d": 60.0},
        }
        count = _update_accuracy_90d(client=None, agent_stats=agent_stats, dry_run=True)
        captured = capsys.readouterr().out
        assert count == 2
        assert "technical" in captured
        assert "80.00%" in captured
        assert "[DRY RUN]" in captured

    def test_live_mode_inserts_rows(self):
        client, chain = _mock_supabase_client()
        agent_stats = {"technical": {"correct": 7, "total": 10, "accuracy_90d": 70.0}}
        count = _update_accuracy_90d(client, agent_stats, dry_run=False)
        assert count == 1
        chain.insert.assert_called_once()
        call_kwargs = chain.insert.call_args[0][0]
        assert call_kwargs["agent_name"] == "technical"
        assert call_kwargs["accuracy_90d"] == 70.0

    def test_empty_stats_returns_zero(self):
        client, _ = _mock_supabase_client()
        count = _update_accuracy_90d(client, {}, dry_run=False)
        assert count == 0

    def test_db_error_does_not_raise(self):
        client, chain = _mock_supabase_client()
        chain.insert.return_value = chain
        chain.execute.side_effect = Exception("DB insert failed")
        agent_stats = {"technical": {"correct": 5, "total": 10, "accuracy_90d": 50.0}}
        # Should not raise; errors are swallowed with a log warning
        count = _update_accuracy_90d(client, agent_stats, dry_run=False)
        assert count == 0

    def test_none_client_live_mode_returns_zero(self):
        agent_stats = {"technical": {"correct": 5, "total": 10, "accuracy_90d": 50.0}}
        count = _update_accuracy_90d(client=None, agent_stats=agent_stats, dry_run=False)
        assert count == 0


# ──────────────────────────────────────────────────────────────────────────────
# TestRunPerformanceTracker
# ──────────────────────────────────────────────────────────────────────────────

class TestRunPerformanceTracker:
    def _make_matured_rec(self, symbol: str = "RELIANCE.NS") -> dict:
        # Created exactly 90 days ago with horizon=90 → matures today (within 2-day window)
        created = (date.today() - timedelta(days=90)).isoformat() + "T10:00:00Z"
        return {
            "id":            f"rec-{symbol}",
            "symbol":        symbol,
            "created_at":    created,
            "horizon_days":  90,
            "agent_signals": {
                "technical":   {"signal": "BUY"},
                "fundamental": {"signal": "BUY"},
            },
        }

    def test_no_supabase_returns_error_result(self):
        with patch("scheduler.performance_tracker._supabase_client", return_value=None):
            result = run(lookback_days=2, dry_run=False)
        assert result.recs_evaluated == 0
        assert "Supabase unavailable" in result.errors

    def test_no_matured_recs_returns_zero_counts(self):
        client, chain = _mock_supabase_client()
        chain.execute.return_value = MagicMock(data=[])
        with patch("scheduler.performance_tracker._supabase_client", return_value=client):
            result = run(lookback_days=2, dry_run=False)
        assert result.recs_evaluated == 0
        assert result.agents_updated == 0
        assert result.accuracy_by_agent == {}

    def _make_chain(self, data: list) -> tuple:
        """Build a properly-chainable Supabase mock chain."""
        client = MagicMock()
        chain  = MagicMock()
        chain.select.return_value  = chain   # ← required
        chain.gte.return_value     = chain
        chain.lte.return_value     = chain
        chain.eq.return_value      = chain
        chain.order.return_value   = chain
        chain.limit.return_value   = chain
        chain.execute.return_value = MagicMock(data=data)
        chain.insert.return_value  = chain
        client.table.return_value  = chain
        return client, chain

    def test_full_run_with_matured_rec(self):
        rec = self._make_matured_rec()
        client, chain = self._make_chain([rec])

        with (
            patch("scheduler.performance_tracker._supabase_client", return_value=client),
            patch(
                "scheduler.performance_tracker._fetch_price_on_date",
                side_effect=[100.0, 110.0],   # +10% = BUY correct
            ),
        ):
            result = run(lookback_days=2, dry_run=False)

        assert result.recs_evaluated == 1
        assert result.agents_updated == 2   # technical + fundamental
        assert result.accuracy_by_agent["technical"] == 100.0
        assert result.accuracy_by_agent["fundamental"] == 100.0

    def test_dry_run_skips_db_writes(self, capsys):
        rec = self._make_matured_rec()
        client, chain = self._make_chain([rec])

        with (
            patch("scheduler.performance_tracker._supabase_client", return_value=client),
            patch(
                "scheduler.performance_tracker._fetch_price_on_date",
                side_effect=[100.0, 115.0],
            ),
        ):
            result = run(lookback_days=2, dry_run=True)

        assert result.dry_run is True
        captured = capsys.readouterr().out
        assert "[DRY RUN]" in captured
        # No actual inserts
        chain.insert.assert_not_called()

    def test_evaluation_error_captured_in_errors(self):
        rec = self._make_matured_rec()
        client, chain = self._make_chain([rec])

        with (
            patch("scheduler.performance_tracker._supabase_client", return_value=client),
            patch(
                "scheduler.performance_tracker._evaluate_rec",
                side_effect=RuntimeError("price service down"),
            ),
        ):
            result = run(lookback_days=2, dry_run=False)

        assert len(result.errors) == 1
        assert "price service down" in result.errors[0]

    def test_result_has_run_date_today(self):
        with patch("scheduler.performance_tracker._supabase_client", return_value=None):
            result = run(lookback_days=2, dry_run=False)
        assert result.run_date == date.today().isoformat()

    def test_result_reflects_lookback_days(self):
        with patch("scheduler.performance_tracker._supabase_client", return_value=None):
            result = run(lookback_days=7, dry_run=False)
        assert result.lookback_days == 7

    def test_duration_seconds_positive(self):
        with patch("scheduler.performance_tracker._supabase_client", return_value=None):
            result = run(dry_run=False)
        assert result.duration_seconds >= 0.0


# ──────────────────────────────────────────────────────────────────────────────
# TestBackfill
# ──────────────────────────────────────────────────────────────────────────────

class TestBackfill:
    def test_delegates_to_run_for_range(self):
        from_date = date(2025, 1, 1)
        to_date   = date(2025, 3, 31)
        with (
            patch("scheduler.performance_tracker._supabase_client", return_value=None),
        ):
            result = backfill(from_date, to_date, dry_run=False)

        # lookback_days = (to_date - from_date).days + 1 = 89 + 1 = 90
        assert result.lookback_days == 90

    def test_single_day_backfill(self):
        target = date.today() - timedelta(days=100)
        with patch("scheduler.performance_tracker._supabase_client", return_value=None):
            result = backfill(target, target, dry_run=False)
        assert result.lookback_days == 1

    def test_dry_run_propagated(self):
        fd = date.today() - timedelta(days=30)
        td = date.today() - timedelta(days=1)
        with patch("scheduler.performance_tracker._supabase_client", return_value=None):
            result = backfill(fd, td, dry_run=True)
        assert result.dry_run is True

    def test_no_supabase_returns_error(self):
        fd = date(2024, 6, 1)
        td = date(2024, 9, 1)
        with patch("scheduler.performance_tracker._supabase_client", return_value=None):
            result = backfill(fd, td, dry_run=False)
        assert "Supabase unavailable" in result.errors


# ──────────────────────────────────────────────────────────────────────────────
# TestGetCurrentAccuracy
# ──────────────────────────────────────────────────────────────────────────────

class TestGetCurrentAccuracy:
    """
    get_current_accuracy calls _supabase_client() then chains:
      .table().select().eq().order().limit().execute()
    The mock chain must route .select() back to itself.
    """

    def _client_returning(self, data: list) -> tuple:
        client, chain = _mock_supabase_client()
        chain.execute.return_value = MagicMock(data=data)
        return client, chain

    def test_returns_float_when_data_exists(self):
        client, _ = self._client_returning([{"accuracy_90d": 75.5}])
        with patch("scheduler.performance_tracker._supabase_client", return_value=client):
            result = get_current_accuracy("technical")
        assert result == 75.5

    def test_returns_none_when_no_rows(self):
        client, _ = self._client_returning([])
        with patch("scheduler.performance_tracker._supabase_client", return_value=client):
            result = get_current_accuracy("technical")
        assert result is None

    def test_returns_none_when_accuracy_is_null(self):
        client, _ = self._client_returning([{"accuracy_90d": None}])
        with patch("scheduler.performance_tracker._supabase_client", return_value=client):
            result = get_current_accuracy("technical")
        assert result is None

    def test_returns_none_when_no_supabase(self):
        with patch("scheduler.performance_tracker._supabase_client", return_value=None):
            result = get_current_accuracy("technical")
        assert result is None

    def test_returns_none_on_db_exception(self):
        client, chain = _mock_supabase_client()
        chain.execute.side_effect = Exception("DB error")
        with patch("scheduler.performance_tracker._supabase_client", return_value=client):
            result = get_current_accuracy("technical")
        assert result is None

    def test_queries_correct_agent_name(self):
        client, chain = self._client_returning([{"accuracy_90d": 65.0}])
        with patch("scheduler.performance_tracker._supabase_client", return_value=client):
            get_current_accuracy("sentiment")
        chain.eq.assert_called_with("agent_name", "sentiment")


# ──────────────────────────────────────────────────────────────────────────────
# TestSupabaseClient
# ──────────────────────────────────────────────────────────────────────────────

class TestSupabaseClient:
    def test_returns_none_when_no_url(self):
        with patch.dict(os.environ, {"SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": "k"}):
            result = _supabase_client()
        assert result is None

    def test_returns_none_when_no_key(self):
        with patch.dict(os.environ, {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": ""}):
            result = _supabase_client()
        assert result is None

    def test_returns_none_when_import_fails(self):
        with (
            patch.dict(os.environ, {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "k"}),
            patch("builtins.__import__", side_effect=ImportError("no supabase")),
        ):
            result = _supabase_client()
        assert result is None


# ──────────────────────────────────────────────────────────────────────────────
# Constants sanity check
# ──────────────────────────────────────────────────────────────────────────────

class TestConstants:
    def test_default_accuracy_baseline(self):
        assert DEFAULT_ACCURACY_BASELINE == 70.0

    def test_directional_buffer_positive(self):
        assert DIRECTIONAL_BUFFER_PCT > 0

    def test_hold_band_wider_than_buffer(self):
        assert HOLD_BAND_PCT > DIRECTIONAL_BUFFER_PCT

    def test_max_horizon_greater_than_min(self):
        assert MAX_HORIZON_DAYS > MIN_HORIZON_DAYS

    def test_fetch_batch_size_reasonable(self):
        assert 10 <= FETCH_BATCH_SIZE <= 1000
