"""
tests/test_gov_performance_tracker.py
Unit tests for governance/performance_tracker.py

Coverage:
  TestDetermineOutcome          — SUCCESS / PARTIAL_FAIL / EXPIRED / IN_PROGRESS
  TestEvaluateAgentSignals      — signal × outcome matrix; NO_DATA; IN_PROGRESS guard
  TestBuildProposal             — template rendering for all 7 agents + _default
  TestSaveProposal              — dry_run print, DB insert, exception handling
  TestProposalExists            — duplicate detection query
  TestConsecutiveLowWeeks       — 2-week threshold check; insufficient history
  TestUpsertAccuracy            — DB insert, dry_run, no client
  TestWriteOutcome              — DB update, dry_run, no client
  TestEmitOutcomeAlert          — severity, alert_type per outcome
  TestRunOutcomeTracker         — full run(); no Supabase; no recs; proposals trigger

Run:
    pytest tests/test_gov_performance_tracker.py -v
"""

import os
import sys
from datetime import date, timedelta, timezone, datetime
from unittest.mock import MagicMock, patch, call

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from governance.performance_tracker import (
    ACCURACY_THRESHOLD,
    CONSECUTIVE_WEEKS,
    EXPIRED,
    IN_PROGRESS,
    MIN_AGE_DAYS,
    PARTIAL_FAIL,
    SUCCESS,
    OutcomeResult,
    _build_proposal,
    _consecutive_low_weeks,
    _determine_outcome,
    _emit_outcome_alert,
    _evaluate_agent_signals,
    _proposal_exists,
    _save_proposal,
    _upsert_accuracy,
    _write_outcome,
    run,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_rec(
    symbol:       str = "RELIANCE.NS",
    action:       str = "BUY",
    target:       float = 3000.0,
    stoploss:     float = 2600.0,
    horizon_days: int = 90,
    days_old:     int = 45,
    agent_signals: dict | None = None,
    rec_id:       str = "rec-001",
) -> dict:
    created = (date.today() - timedelta(days=days_old)).isoformat() + "T10:00:00Z"
    if agent_signals is None:
        agent_signals = {
            "technical":   {"signal": "BUY"},
            "fundamental": {"signal": "BUY"},
        }
    return {
        "id":            rec_id,
        "symbol":        symbol,
        "action":        action,
        "created_at":    created,
        "horizon_days":  horizon_days,
        "target":        target,
        "stoploss":      stoploss,
        "agent_signals": agent_signals,
        "outcome":       None,
    }


def _mock_client() -> tuple:
    client = MagicMock()
    chain  = MagicMock()
    chain.select.return_value   = chain
    chain.eq.return_value       = chain
    chain.in_.return_value      = chain
    chain.lte.return_value      = chain
    chain.or_.return_value      = chain
    chain.order.return_value    = chain
    chain.limit.return_value    = chain
    chain.insert.return_value   = chain
    chain.update.return_value   = chain
    chain.execute.return_value  = MagicMock(data=[])
    client.table.return_value   = chain
    return client, chain


# ──────────────────────────────────────────────────────────────────────────────
# TestDetermineOutcome
# ──────────────────────────────────────────────────────────────────────────────

class TestDetermineOutcome:
    def test_success_when_price_hits_target(self):
        rec = _make_rec(target=3000.0, stoploss=2600.0)
        assert _determine_outcome(rec, 3000.0) == SUCCESS

    def test_success_when_price_exceeds_target(self):
        rec = _make_rec(target=3000.0, stoploss=2600.0)
        assert _determine_outcome(rec, 3100.0) == SUCCESS

    def test_partial_fail_when_price_hits_stoploss(self):
        rec = _make_rec(target=3000.0, stoploss=2600.0)
        assert _determine_outcome(rec, 2600.0) == PARTIAL_FAIL

    def test_partial_fail_when_price_below_stoploss(self):
        rec = _make_rec(target=3000.0, stoploss=2600.0)
        assert _determine_outcome(rec, 2500.0) == PARTIAL_FAIL

    def test_in_progress_when_price_between(self):
        rec = _make_rec(target=3000.0, stoploss=2600.0, horizon_days=180, days_old=45)
        assert _determine_outcome(rec, 2800.0) == IN_PROGRESS

    def test_expired_when_horizon_elapsed_and_no_hit(self):
        # created 200 days ago, horizon=90 → expired 110 days ago
        rec = _make_rec(target=3000.0, stoploss=2600.0, horizon_days=90, days_old=200)
        assert _determine_outcome(rec, 2800.0) == EXPIRED

    def test_target_wins_over_expired(self):
        # Even if horizon elapsed, hitting target = SUCCESS
        rec = _make_rec(target=3000.0, stoploss=2600.0, horizon_days=90, days_old=200)
        assert _determine_outcome(rec, 3050.0) == SUCCESS

    def test_stoploss_wins_over_expired(self):
        rec = _make_rec(target=3000.0, stoploss=2600.0, horizon_days=90, days_old=200)
        assert _determine_outcome(rec, 2500.0) == PARTIAL_FAIL

    def test_no_target_no_stoploss_in_progress(self):
        rec = _make_rec(target=None, stoploss=None, horizon_days=180, days_old=45)
        rec["target"] = None
        rec["stoploss"] = None
        assert _determine_outcome(rec, 2800.0) == IN_PROGRESS

    def test_no_target_no_stoploss_expired(self):
        rec = _make_rec(horizon_days=30, days_old=60)
        rec["target"] = None
        rec["stoploss"] = None
        assert _determine_outcome(rec, 2800.0) == EXPIRED

    def test_invalid_created_at_does_not_raise(self):
        rec = _make_rec()
        rec["created_at"] = "bad-date"
        # Should not raise; horizon_elapsed defaults to False
        result = _determine_outcome(rec, 2800.0)
        assert result == IN_PROGRESS  # price between target/stoploss + no horizon info


# ──────────────────────────────────────────────────────────────────────────────
# TestEvaluateAgentSignals
# ──────────────────────────────────────────────────────────────────────────────

class TestEvaluateAgentSignals:
    def _signals(self, **agents) -> dict:
        return {k: {"signal": v} for k, v in agents.items()}

    # BUY signal
    def test_buy_on_success_correct(self):
        r = _evaluate_agent_signals(self._signals(technical="BUY"), SUCCESS)
        assert r["technical"] is True

    def test_buy_on_partial_fail_wrong(self):
        r = _evaluate_agent_signals(self._signals(technical="BUY"), PARTIAL_FAIL)
        assert r["technical"] is False

    def test_buy_on_expired_wrong(self):
        r = _evaluate_agent_signals(self._signals(technical="BUY"), EXPIRED)
        assert r["technical"] is False

    # SELL / AVOID signal
    def test_sell_on_partial_fail_correct(self):
        r = _evaluate_agent_signals(self._signals(sentiment="SELL"), PARTIAL_FAIL)
        assert r["sentiment"] is True

    def test_sell_on_success_wrong(self):
        r = _evaluate_agent_signals(self._signals(sentiment="SELL"), SUCCESS)
        assert r["sentiment"] is False

    def test_avoid_on_partial_fail_correct(self):
        r = _evaluate_agent_signals(self._signals(macro="AVOID"), PARTIAL_FAIL)
        assert r["macro"] is True

    # HOLD signal
    def test_hold_on_expired_correct(self):
        r = _evaluate_agent_signals(self._signals(fundamental="HOLD"), EXPIRED)
        assert r["fundamental"] is True

    def test_hold_on_success_wrong(self):
        r = _evaluate_agent_signals(self._signals(fundamental="HOLD"), SUCCESS)
        assert r["fundamental"] is False

    def test_hold_on_partial_fail_wrong(self):
        r = _evaluate_agent_signals(self._signals(fundamental="HOLD"), PARTIAL_FAIL)
        assert r["fundamental"] is False

    # NO_DATA / IN_PROGRESS
    def test_no_data_returns_none(self):
        r = _evaluate_agent_signals(self._signals(technical="NO_DATA"), SUCCESS)
        assert r["technical"] is None

    def test_in_progress_returns_empty(self):
        r = _evaluate_agent_signals(self._signals(technical="BUY"), IN_PROGRESS)
        assert r == {}

    def test_empty_signals_returns_empty(self):
        r = _evaluate_agent_signals({}, SUCCESS)
        assert r == {}

    def test_none_signals_returns_empty(self):
        r = _evaluate_agent_signals(None, SUCCESS)
        assert r == {}

    def test_string_signal_value(self):
        r = _evaluate_agent_signals({"technical": "BUY"}, SUCCESS)
        assert r["technical"] is True

    def test_multiple_agents_mixed(self):
        signals = self._signals(technical="BUY", fundamental="SELL", macro="HOLD")
        r = _evaluate_agent_signals(signals, SUCCESS)
        assert r["technical"] is True
        assert r["fundamental"] is False
        assert r["macro"] is False


# ──────────────────────────────────────────────────────────────────────────────
# TestBuildProposal
# ──────────────────────────────────────────────────────────────────────────────

class TestBuildProposal:
    AGENTS = [
        "technical", "fundamental", "sentiment", "institutional",
        "macro", "historical_rag", "commodities",
    ]

    @pytest.mark.parametrize("agent", AGENTS)
    def test_all_known_agents_have_templates(self, agent):
        p = _build_proposal(agent, 55.0)
        assert p["title"]
        assert p["proposed_by"] == "governance/performance_tracker"
        assert agent in p["impacted_agents"]
        assert p["trigger_agent"] == agent
        assert p["trigger_accuracy"] == 55.0
        assert p["status"] == "PENDING"
        assert isinstance(p["steps"], list)
        assert len(p["steps"]) >= 3

    def test_unknown_agent_uses_default_template(self):
        p = _build_proposal("unknown_agent", 60.0)
        assert p["trigger_agent"] == "unknown_agent"
        assert "unknown_agent" in p["impacted_agents"]
        assert len(p["steps"]) >= 1

    def test_accuracy_formatted_in_rationale(self):
        p = _build_proposal("technical", 62.5)
        assert "62.5" in p["rationale"]

    def test_cost_impact_is_valid(self):
        for agent in self.AGENTS:
            p = _build_proposal(agent, 65.0)
            assert p["cost_impact"] in ("low", "medium", "high")

    def test_is_paid_is_bool(self):
        for agent in self.AGENTS:
            p = _build_proposal(agent, 65.0)
            assert isinstance(p["is_paid"], bool)

    def test_paid_agent_is_sentiment(self):
        p = _build_proposal("sentiment", 55.0)
        assert p["is_paid"] is True


# ──────────────────────────────────────────────────────────────────────────────
# TestSaveProposal
# ──────────────────────────────────────────────────────────────────────────────

class TestSaveProposal:
    def _proposal(self) -> dict:
        return _build_proposal("technical", 60.0)

    def test_dry_run_prints_and_returns_true(self, capsys):
        ok = _save_proposal(None, self._proposal(), dry_run=True)
        assert ok is True
        captured = capsys.readouterr().out
        assert "[DRY RUN]" in captured
        assert "technical" in captured

    def test_live_mode_inserts_to_db(self):
        client, chain = _mock_client()
        ok = _save_proposal(client, self._proposal(), dry_run=False)
        assert ok is True
        chain.insert.assert_called_once()

    def test_no_client_returns_false(self):
        ok = _save_proposal(None, self._proposal(), dry_run=False)
        assert ok is False

    def test_db_exception_returns_false(self):
        client, chain = _mock_client()
        chain.execute.side_effect = Exception("DB insert failed")
        ok = _save_proposal(client, self._proposal(), dry_run=False)
        assert ok is False


# ──────────────────────────────────────────────────────────────────────────────
# TestProposalExists
# ──────────────────────────────────────────────────────────────────────────────

class TestProposalExists:
    def test_returns_true_when_open_proposal_found(self):
        client, chain = _mock_client()
        chain.execute.return_value = MagicMock(data=[{"id": "abc"}])
        assert _proposal_exists(client, "technical") is True

    def test_returns_false_when_no_open_proposal(self):
        client, chain = _mock_client()
        chain.execute.return_value = MagicMock(data=[])
        assert _proposal_exists(client, "technical") is False

    def test_returns_false_when_no_client(self):
        assert _proposal_exists(None, "technical") is False

    def test_returns_false_on_db_exception(self):
        client, chain = _mock_client()
        chain.execute.side_effect = Exception("DB error")
        assert _proposal_exists(client, "technical") is False


# ──────────────────────────────────────────────────────────────────────────────
# TestConsecutiveLowWeeks
# ──────────────────────────────────────────────────────────────────────────────

class TestConsecutiveLowWeeks:
    def test_returns_true_when_below_threshold_consecutive(self):
        client, chain = _mock_client()
        chain.execute.return_value = MagicMock(data=[
            {"accuracy_90d": 60.0, "audit_date": "2025-04-13"},
            {"accuracy_90d": 65.0, "audit_date": "2025-04-06"},
        ])
        below, acc = _consecutive_low_weeks(client, "technical")
        assert below is True
        assert acc == pytest.approx(60.0)

    def test_returns_false_when_one_week_above(self):
        client, chain = _mock_client()
        chain.execute.return_value = MagicMock(data=[
            {"accuracy_90d": 75.0, "audit_date": "2025-04-13"},  # above threshold
            {"accuracy_90d": 65.0, "audit_date": "2025-04-06"},
        ])
        below, _ = _consecutive_low_weeks(client, "technical")
        assert below is False

    def test_returns_false_when_insufficient_history(self):
        client, chain = _mock_client()
        chain.execute.return_value = MagicMock(data=[
            {"accuracy_90d": 60.0, "audit_date": "2025-04-13"},
            # only 1 row, need 2
        ])
        below, _ = _consecutive_low_weeks(client, "technical")
        assert below is False

    def test_returns_false_no_client(self):
        below, acc = _consecutive_low_weeks(None, "technical")
        assert below is False
        assert acc == 100.0

    def test_null_accuracy_treated_as_100(self):
        """None accuracy_90d should be treated as 100% (no data = no penalty)."""
        client, chain = _mock_client()
        chain.execute.return_value = MagicMock(data=[
            {"accuracy_90d": None, "audit_date": "2025-04-13"},
            {"accuracy_90d": None, "audit_date": "2025-04-06"},
        ])
        below, _ = _consecutive_low_weeks(client, "technical")
        assert below is False   # 100% treated values → not below threshold

    def test_returns_false_on_db_exception(self):
        client, chain = _mock_client()
        chain.execute.side_effect = Exception("DB error")
        below, acc = _consecutive_low_weeks(client, "technical")
        assert below is False
        assert acc == 100.0


# ──────────────────────────────────────────────────────────────────────────────
# TestUpsertAccuracy
# ──────────────────────────────────────────────────────────────────────────────

class TestUpsertAccuracy:
    def test_dry_run_prints_and_counts(self, capsys):
        stats = {
            "technical":   {"correct": 8, "total": 10, "accuracy": 80.0},
            "fundamental": {"correct": 6, "total": 10, "accuracy": 60.0},
        }
        count = _upsert_accuracy(None, stats, dry_run=True)
        assert count == 2
        captured = capsys.readouterr().out
        assert "[DRY RUN]" in captured
        assert "80.00%" in captured

    def test_live_inserts_rows(self):
        client, chain = _mock_client()
        stats = {"technical": {"correct": 7, "total": 10, "accuracy": 70.0}}
        count = _upsert_accuracy(client, stats, dry_run=False)
        assert count == 1
        chain.insert.assert_called_once()

    def test_empty_stats_returns_zero(self):
        client, _ = _mock_client()
        assert _upsert_accuracy(client, {}, dry_run=False) == 0

    def test_none_accuracy_skipped(self):
        client, chain = _mock_client()
        stats = {"technical": {"correct": 0, "total": 0, "accuracy": None}}
        count = _upsert_accuracy(client, stats, dry_run=False)
        assert count == 0
        chain.insert.assert_not_called()

    def test_db_error_does_not_raise(self):
        client, chain = _mock_client()
        chain.execute.side_effect = Exception("DB fail")
        stats = {"technical": {"correct": 5, "total": 10, "accuracy": 50.0}}
        count = _upsert_accuracy(client, stats, dry_run=False)
        assert count == 0


# ──────────────────────────────────────────────────────────────────────────────
# TestWriteOutcome
# ──────────────────────────────────────────────────────────────────────────────

class TestWriteOutcome:
    def test_dry_run_no_db_call(self):
        client, chain = _mock_client()
        _write_outcome(client, "rec-1", SUCCESS, 3050.0, dry_run=True)
        chain.update.assert_not_called()

    def test_live_updates_row(self):
        client, chain = _mock_client()
        _write_outcome(client, "rec-1", SUCCESS, 3050.0, dry_run=False)
        chain.update.assert_called_once()
        update_args = chain.update.call_args[0][0]
        assert update_args["outcome"] == SUCCESS
        assert update_args["outcome_price"] == 3050.0

    def test_no_client_no_error(self):
        _write_outcome(None, "rec-1", IN_PROGRESS, 2800.0, dry_run=False)

    def test_db_exception_silenced(self):
        client, chain = _mock_client()
        chain.execute.side_effect = Exception("DB error")
        _write_outcome(client, "rec-1", SUCCESS, 3050.0, dry_run=False)  # should not raise


# ──────────────────────────────────────────────────────────────────────────────
# TestEmitOutcomeAlert
# ──────────────────────────────────────────────────────────────────────────────

class TestEmitOutcomeAlert:
    def _rec(self):
        return _make_rec(symbol="TCS.NS", target=4200.0, stoploss=3800.0)

    def test_no_alert_for_in_progress(self):
        client, chain = _mock_client()
        _emit_outcome_alert(client, self._rec(), IN_PROGRESS, 4000.0, dry_run=False)
        chain.insert.assert_not_called()

    def test_info_alert_on_success(self):
        client, chain = _mock_client()
        _emit_outcome_alert(client, self._rec(), SUCCESS, 4250.0, dry_run=False)
        chain.insert.assert_called_once()
        row = chain.insert.call_args[0][0]
        assert row["severity"] == "INFO"
        assert row["alert_type"] == "TARGET_HIT"

    def test_warning_alert_on_partial_fail(self):
        client, chain = _mock_client()
        _emit_outcome_alert(client, self._rec(), PARTIAL_FAIL, 3750.0, dry_run=False)
        row = chain.insert.call_args[0][0]
        assert row["severity"] == "WARNING"
        assert row["alert_type"] == "STOPLOSS_HIT"

    def test_info_alert_on_expired(self):
        client, chain = _mock_client()
        _emit_outcome_alert(client, self._rec(), EXPIRED, 4000.0, dry_run=False)
        row = chain.insert.call_args[0][0]
        assert row["alert_type"] == "REC_EXPIRED"

    def test_dry_run_prints_not_inserts(self, capsys):
        client, chain = _mock_client()
        _emit_outcome_alert(client, self._rec(), SUCCESS, 4250.0, dry_run=True)
        assert "[DRY RUN]" in capsys.readouterr().out
        chain.insert.assert_not_called()


# ──────────────────────────────────────────────────────────────────────────────
# TestRunOutcomeTracker
# ──────────────────────────────────────────────────────────────────────────────

class TestRunOutcomeTracker:
    def _make_open_rec(self) -> dict:
        return _make_rec(
            days_old=45,
            target=3000.0,
            stoploss=2600.0,
            horizon_days=90,
        )

    def test_no_supabase_returns_error(self):
        with patch("governance.performance_tracker._supabase", return_value=None):
            result = run(dry_run=False)
        assert result["recs_evaluated"] == 0
        assert "Supabase unavailable" in result["errors"]

    def test_no_recs_returns_zero_counts(self):
        client, chain = _mock_client()
        chain.execute.return_value = MagicMock(data=[])
        with patch("governance.performance_tracker._supabase", return_value=client):
            result = run(dry_run=False)
        assert result["recs_evaluated"] == 0
        assert result["agents_updated"] == 0

    def test_full_run_success_outcome(self):
        rec = self._make_open_rec()
        client, chain = _mock_client()
        chain.execute.return_value = MagicMock(data=[rec])

        with (
            patch("governance.performance_tracker._supabase", return_value=client),
            patch(
                "governance.performance_tracker._fetch_current_price",
                return_value=3100.0,   # above target → SUCCESS
            ),
            patch("governance.performance_tracker._consecutive_low_weeks",
                  return_value=(False, 80.0)),
        ):
            result = run(dry_run=True)

        assert result["recs_evaluated"] == 1
        assert result["outcomes"].get(SUCCESS, 0) == 1
        assert result["outcomes"].get(PARTIAL_FAIL, 0) == 0

    def test_full_run_partial_fail_outcome(self):
        rec = self._make_open_rec()
        client, chain = _mock_client()
        chain.execute.return_value = MagicMock(data=[rec])

        with (
            patch("governance.performance_tracker._supabase", return_value=client),
            patch(
                "governance.performance_tracker._fetch_current_price",
                return_value=2500.0,   # below stoploss → PARTIAL_FAIL
            ),
            patch("governance.performance_tracker._consecutive_low_weeks",
                  return_value=(False, 80.0)),
        ):
            result = run(dry_run=True)

        assert result["outcomes"].get(PARTIAL_FAIL, 0) == 1

    def test_proposal_generated_when_low_accuracy(self):
        rec = self._make_open_rec()
        client, chain = _mock_client()
        chain.execute.return_value = MagicMock(data=[rec])

        with (
            patch("governance.performance_tracker._supabase", return_value=client),
            patch("governance.performance_tracker._fetch_current_price", return_value=3100.0),
            patch(
                "governance.performance_tracker._consecutive_low_weeks",
                return_value=(True, 60.0),   # ← triggers proposal
            ),
            patch("governance.performance_tracker._proposal_exists", return_value=False),
            patch("governance.performance_tracker._save_proposal", return_value=True),
            patch("governance.performance_tracker._send_telegram"),
        ):
            result = run(dry_run=True)

        assert result["proposals_generated"] >= 1

    def test_no_proposal_when_existing_open(self):
        rec = self._make_open_rec()
        client, chain = _mock_client()
        chain.execute.return_value = MagicMock(data=[rec])

        with (
            patch("governance.performance_tracker._supabase", return_value=client),
            patch("governance.performance_tracker._fetch_current_price", return_value=3100.0),
            patch(
                "governance.performance_tracker._consecutive_low_weeks",
                return_value=(True, 60.0),
            ),
            patch("governance.performance_tracker._proposal_exists", return_value=True),  # ← already exists
            patch("governance.performance_tracker._save_proposal") as mock_save,
        ):
            result = run(dry_run=True)

        assert result["proposals_generated"] == 0
        mock_save.assert_not_called()

    def test_price_unavailable_adds_error(self):
        rec = self._make_open_rec()
        client, chain = _mock_client()
        chain.execute.return_value = MagicMock(data=[rec])

        with (
            patch("governance.performance_tracker._supabase", return_value=client),
            patch("governance.performance_tracker._fetch_current_price", return_value=None),
        ):
            result = run(dry_run=False)

        assert len(result["errors"]) == 1
        assert "RELIANCE.NS" in result["errors"][0]

    def test_result_has_correct_keys(self):
        with patch("governance.performance_tracker._supabase", return_value=None):
            result = run(dry_run=False)
        assert {
            "run_date", "recs_evaluated", "outcomes", "agents_updated",
            "proposals_generated", "errors", "duration_seconds"
        } == set(result.keys())

    def test_dry_run_no_db_writes(self):
        rec = self._make_open_rec()
        client, chain = _mock_client()
        chain.execute.return_value = MagicMock(data=[rec])

        with (
            patch("governance.performance_tracker._supabase", return_value=client),
            patch("governance.performance_tracker._fetch_current_price", return_value=3100.0),
            patch("governance.performance_tracker._consecutive_low_weeks",
                  return_value=(False, 80.0)),
        ):
            run(dry_run=True)

        # In dry_run, update/insert should not be called on the recommendations chain
        chain.update.assert_not_called()


# ──────────────────────────────────────────────────────────────────────────────
# TestOutcomeResult dataclass
# ──────────────────────────────────────────────────────────────────────────────

class TestOutcomeResult:
    def test_defaults(self):
        r = OutcomeResult(run_date="2025-04-20", recs_evaluated=0)
        assert r.outcomes == {}
        assert r.errors == []
        assert r.dry_run is False

    def test_to_dict_round_trip(self):
        r = OutcomeResult(
            run_date="2025-04-20",
            recs_evaluated=5,
            outcomes={SUCCESS: 3, PARTIAL_FAIL: 1, IN_PROGRESS: 1},
            agents_updated=2,
            proposals_generated=1,
        )
        d = r.to_dict()
        assert d["recs_evaluated"] == 5
        assert d["outcomes"][SUCCESS] == 3

    def test_constants_are_sensible(self):
        assert MIN_AGE_DAYS > 0
        assert ACCURACY_THRESHOLD == 70.0
        assert CONSECUTIVE_WEEKS == 2
