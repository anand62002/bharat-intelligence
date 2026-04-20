"""
tests/test_hallucination_detector.py — Unit tests for governance/hallucination_detector.py

Coverage:
  TestGetAgentTrustScores        — trust computation, clamps, empty DB, no client
  TestEmitHallucinationAlert     — severity escalation, trust-weighted title/detail
  TestRunReturnsTrustScores      — run() surfaces trust_scores in return dict
  TestIsSignalCorrect            — BUY/SELL/HOLD/NO_DATA edge cases
  TestComputeAccuracy            — aggregation correctness
  TestComputeHallucinationRates  — claim_detail parsing, missing gov_check
  TestPrevAccuracy               — DB query helper
  TestUpsertAgentPerformance     — dry_run, trend detection, DB insert

Run:
    pytest tests/test_hallucination_detector.py -v
"""

import os
import sys
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from governance.hallucination_detector import (
    DEFAULT_ACCURACY_BASELINE,
    DIRECTIONAL_BUFFER_PCT,
    HALLUCINATION_ALERT_PCT,
    HOLD_BAND_PCT,
    TRUST_HIGH_THRESHOLD,
    TRUST_MAX,
    TRUST_MIN,
    _compute_accuracy,
    _compute_hallucination_rates,
    _emit_hallucination_alert,
    _is_signal_correct,
    _prev_accuracy,
    _upsert_agent_performance,
    get_agent_trust_scores,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _mock_supabase() -> tuple:
    """Build a chainable MagicMock for Supabase client queries."""
    client = MagicMock()
    chain  = MagicMock()
    chain.select.return_value  = chain
    chain.eq.return_value      = chain
    chain.order.return_value   = chain
    chain.limit.return_value   = chain
    chain.execute.return_value = MagicMock(data=[])
    chain.insert.return_value  = chain
    client.table.return_value  = chain
    return client, chain


# ──────────────────────────────────────────────────────────────────────────────
# TestGetAgentTrustScores
# ──────────────────────────────────────────────────────────────────────────────

class TestGetAgentTrustScores:
    def test_trust_at_baseline(self):
        """accuracy_90d == DEFAULT_ACCURACY_BASELINE → trust == 1.0."""
        client, chain = _mock_supabase()
        chain.execute.return_value = MagicMock(data=[
            {"agent_name": "technical", "accuracy_90d": DEFAULT_ACCURACY_BASELINE},
        ])
        scores = get_agent_trust_scores(client)
        assert scores["technical"] == pytest.approx(1.0, abs=0.001)

    def test_trust_above_baseline(self):
        """accuracy_90d = 84% (= baseline × 1.2) → trust = 1.2."""
        client, chain = _mock_supabase()
        chain.execute.return_value = MagicMock(data=[
            {"agent_name": "fundamental", "accuracy_90d": DEFAULT_ACCURACY_BASELINE * 1.2},
        ])
        scores = get_agent_trust_scores(client)
        assert scores["fundamental"] == pytest.approx(1.2, abs=0.001)

    def test_trust_clamped_at_max(self):
        """Very high accuracy capped at TRUST_MAX."""
        client, chain = _mock_supabase()
        chain.execute.return_value = MagicMock(data=[
            {"agent_name": "technical", "accuracy_90d": 999.0},
        ])
        scores = get_agent_trust_scores(client)
        assert scores["technical"] == TRUST_MAX

    def test_trust_clamped_at_min(self):
        """Very low accuracy capped at TRUST_MIN."""
        client, chain = _mock_supabase()
        chain.execute.return_value = MagicMock(data=[
            {"agent_name": "technical", "accuracy_90d": 0.0},
        ])
        scores = get_agent_trust_scores(client)
        assert scores["technical"] == TRUST_MIN

    def test_empty_db_returns_empty_dict(self):
        client, chain = _mock_supabase()
        chain.execute.return_value = MagicMock(data=[])
        scores = get_agent_trust_scores(client)
        assert scores == {}

    def test_no_client_returns_empty_dict(self):
        scores = get_agent_trust_scores(client=None)
        assert scores == {}

    def test_uses_most_recent_row_per_agent(self):
        """Duplicate agent_name rows — only first (most-recent ordered) is used."""
        client, chain = _mock_supabase()
        chain.execute.return_value = MagicMock(data=[
            {"agent_name": "technical", "accuracy_90d": 90.0},  # most recent
            {"agent_name": "technical", "accuracy_90d": 50.0},  # older, ignored
        ])
        scores = get_agent_trust_scores(client)
        assert scores["technical"] == pytest.approx(90.0 / DEFAULT_ACCURACY_BASELINE, abs=0.001)

    def test_multiple_agents(self):
        client, chain = _mock_supabase()
        chain.execute.return_value = MagicMock(data=[
            {"agent_name": "technical",   "accuracy_90d": 70.0},
            {"agent_name": "fundamental", "accuracy_90d": 35.0},  # half baseline → 0.5
        ])
        scores = get_agent_trust_scores(client)
        assert "technical"   in scores
        assert "fundamental" in scores
        assert scores["fundamental"] == TRUST_MIN   # 35/70 = 0.5, hits floor

    def test_null_accuracy_skipped(self):
        client, chain = _mock_supabase()
        chain.execute.return_value = MagicMock(data=[
            {"agent_name": "technical", "accuracy_90d": None},
        ])
        scores = get_agent_trust_scores(client)
        assert "technical" not in scores

    def test_db_exception_returns_empty(self):
        client, chain = _mock_supabase()
        chain.execute.side_effect = Exception("DB error")
        scores = get_agent_trust_scores(client)
        assert scores == {}

    def test_trust_values_are_rounded_to_4dp(self):
        client, chain = _mock_supabase()
        chain.execute.return_value = MagicMock(data=[
            {"agent_name": "technical", "accuracy_90d": 73.0},
        ])
        scores = get_agent_trust_scores(client)
        # Value should be a float rounded to 4 decimal places
        val = scores["technical"]
        assert val == round(val, 4)

    def test_creates_client_when_none_given(self):
        """When client=None, function should call _supabase()."""
        mock_client, chain = _mock_supabase()
        chain.execute.return_value = MagicMock(data=[
            {"agent_name": "technical", "accuracy_90d": 70.0},
        ])
        with patch("governance.hallucination_detector._supabase", return_value=mock_client):
            scores = get_agent_trust_scores(client=None)
        assert "technical" in scores


# ──────────────────────────────────────────────────────────────────────────────
# TestEmitHallucinationAlert
# ──────────────────────────────────────────────────────────────────────────────

class TestEmitHallucinationAlert:
    def test_no_alert_below_threshold(self):
        client = MagicMock()
        _emit_hallucination_alert(client, "technical", HALLUCINATION_ALERT_PCT, dry_run=False)
        client.table.assert_not_called()

    def test_no_alert_at_threshold(self):
        client = MagicMock()
        _emit_hallucination_alert(client, "technical", HALLUCINATION_ALERT_PCT, dry_run=False)
        client.table.assert_not_called()

    def test_warning_severity_low_trust(self):
        """Low trust score → severity = WARNING."""
        client, chain = _mock_supabase()
        _emit_hallucination_alert(
            client, "technical",
            hallucination_rate=HALLUCINATION_ALERT_PCT + 1.0,
            dry_run=False,
            trust_score=0.8,   # below TRUST_HIGH_THRESHOLD
        )
        call_row = chain.insert.call_args[0][0]
        assert call_row["severity"] == "WARNING"

    def test_critical_severity_high_trust(self):
        """trust ≥ TRUST_HIGH_THRESHOLD → severity = CRITICAL."""
        client, chain = _mock_supabase()
        _emit_hallucination_alert(
            client, "fundamental",
            hallucination_rate=HALLUCINATION_ALERT_PCT + 1.0,
            dry_run=False,
            trust_score=TRUST_HIGH_THRESHOLD,   # exactly at threshold
        )
        call_row = chain.insert.call_args[0][0]
        assert call_row["severity"] == "CRITICAL"

    def test_critical_severity_very_high_trust(self):
        client, chain = _mock_supabase()
        _emit_hallucination_alert(
            client, "technical",
            hallucination_rate=2.0,
            dry_run=False,
            trust_score=1.5,
        )
        call_row = chain.insert.call_args[0][0]
        assert call_row["severity"] == "CRITICAL"

    def test_default_trust_gives_warning(self):
        """Default trust=1.0 is below TRUST_HIGH_THRESHOLD → WARNING."""
        client, chain = _mock_supabase()
        _emit_hallucination_alert(
            client, "technical",
            hallucination_rate=2.0,
            dry_run=False,
            # trust_score not supplied → defaults to 1.0
        )
        call_row = chain.insert.call_args[0][0]
        assert call_row["severity"] == "WARNING"

    def test_dry_run_no_db_write(self, capsys):
        client, chain = _mock_supabase()
        _emit_hallucination_alert(
            client, "technical", 3.0, dry_run=True, trust_score=1.3,
        )
        captured = capsys.readouterr().out
        assert "[DRY RUN]" in captured
        assert "CRITICAL" in captured   # trust=1.3 ≥ threshold
        chain.insert.assert_not_called()

    def test_dry_run_warning_printed(self, capsys):
        client = MagicMock()
        _emit_hallucination_alert(
            client, "fundamental", 2.0, dry_run=True, trust_score=0.6,
        )
        captured = capsys.readouterr().out
        assert "[DRY RUN]" in captured
        assert "WARNING" in captured

    def test_alert_detail_contains_trust_note_high_trust(self):
        client, chain = _mock_supabase()
        _emit_hallucination_alert(
            client, "technical",
            hallucination_rate=3.0,
            dry_run=False,
            trust_score=1.4,
        )
        call_row = chain.insert.call_args[0][0]
        assert "trust" in call_row["detail"].lower() or "high" in call_row["detail"].lower()

    def test_no_client_no_error(self):
        """None client should not raise even above threshold."""
        _emit_hallucination_alert(None, "technical", 5.0, dry_run=False, trust_score=1.0)

    def test_db_exception_silenced(self):
        client, chain = _mock_supabase()
        chain.execute.side_effect = Exception("DB failure")
        # Should not raise
        _emit_hallucination_alert(
            client, "technical", 3.0, dry_run=False, trust_score=0.9,
        )


# ──────────────────────────────────────────────────────────────────────────────
# TestRunReturnsTrustScores
# ──────────────────────────────────────────────────────────────────────────────

class TestRunReturnsTrustScores:
    def test_run_includes_trust_scores_key_supabase_unavailable(self):
        """run() must include 'trust_scores' even in the Supabase-unavailable early-return."""
        with patch("governance.hallucination_detector._supabase", return_value=None):
            from governance.hallucination_detector import run
            result = run(dry_run=False)
        assert "trust_scores" in result

    def test_run_trust_scores_empty_when_supabase_unavailable(self):
        """Supabase unavailable → empty trust_scores."""
        with patch("governance.hallucination_detector._supabase", return_value=None):
            from governance.hallucination_detector import run
            result = run(dry_run=False)
        assert result["trust_scores"] == {}

    def test_run_trust_scores_empty_when_no_recs(self):
        """0 mature recs → early-return with trust_scores = {}."""
        client, chain = _mock_supabase()
        chain.execute.return_value = MagicMock(data=[])

        with patch("governance.hallucination_detector._supabase", return_value=client):
            from governance.hallucination_detector import run
            result = run(dry_run=False)

        assert "trust_scores" in result
        assert result["trust_scores"] == {}

    def test_run_trust_scores_populated_when_agents_found(self):
        """
        When recs + evaluations are available, trust_scores reflects live DB values.
        We patch get_agent_trust_scores to isolate the trust-score wiring.
        """
        client, chain = _mock_supabase()

        # Build a mature rec
        from datetime import date, timedelta
        today   = date.today()
        created = (today - timedelta(days=180)).isoformat() + "T10:00:00Z"
        rec = {
            "id": "r1", "symbol": "RELIANCE.NS",
            "created_at": created, "horizon_days": 180,
            "agent_signals": {"technical": {"signal": "BUY"}},
            "gov_check": None, "action": "BUY",
        }
        chain.execute.return_value = MagicMock(data=[rec])

        with (
            patch("governance.hallucination_detector._supabase", return_value=client),
            patch(
                "governance.hallucination_detector._fetch_price_on_date",
                side_effect=[100.0, 110.0],
            ),
            patch(
                "governance.hallucination_detector.get_agent_trust_scores",
                return_value={"technical": 1.1},
            ),
        ):
            from governance.hallucination_detector import run
            result = run(dry_run=True)

        assert "trust_scores" in result
        assert result["trust_scores"] == {"technical": 1.1}


# ──────────────────────────────────────────────────────────────────────────────
# TestIsSignalCorrect
# ──────────────────────────────────────────────────────────────────────────────

class TestIsSignalCorrect:
    def test_buy_correct(self):
        assert _is_signal_correct("BUY", DIRECTIONAL_BUFFER_PCT + 1) is True

    def test_buy_wrong(self):
        assert _is_signal_correct("BUY", -5.0) is False

    def test_sell_correct(self):
        assert _is_signal_correct("SELL", -(DIRECTIONAL_BUFFER_PCT + 1)) is True

    def test_sell_wrong(self):
        assert _is_signal_correct("SELL", 10.0) is False

    def test_avoid_alias(self):
        assert _is_signal_correct("AVOID", -5.0) is True

    def test_hold_correct(self):
        assert _is_signal_correct("HOLD", HOLD_BAND_PCT - 1) is True

    def test_hold_wrong_large_move(self):
        assert _is_signal_correct("HOLD", HOLD_BAND_PCT + 1) is False

    def test_no_data_none(self):
        assert _is_signal_correct("NO_DATA", 10.0) is None

    def test_empty_none(self):
        assert _is_signal_correct("", 5.0) is None

    def test_neutral_none(self):
        assert _is_signal_correct("NEUTRAL", 5.0) is None

    def test_unknown_none(self):
        assert _is_signal_correct("SPECULATIVE", 5.0) is None


# ──────────────────────────────────────────────────────────────────────────────
# TestComputeAccuracy
# ──────────────────────────────────────────────────────────────────────────────

class TestComputeAccuracy:
    def test_all_correct(self):
        evals = [{"technical": True, "macro": True}]
        acc = _compute_accuracy(evals)
        assert acc["technical"]["accuracy_90d"] == 100.0
        assert acc["macro"]["accuracy_90d"] == 100.0

    def test_half_correct(self):
        evals = [{"technical": True}, {"technical": False}]
        acc = _compute_accuracy(evals)
        assert acc["technical"]["accuracy_90d"] == 50.0

    def test_none_excluded(self):
        evals = [{"technical": None}, {"technical": True}]
        acc = _compute_accuracy(evals)
        assert acc["technical"]["total"] == 1
        assert acc["technical"]["correct"] == 1

    def test_empty_evals(self):
        assert _compute_accuracy([]) == {}


# ──────────────────────────────────────────────────────────────────────────────
# TestComputeHallucinationRates
# ──────────────────────────────────────────────────────────────────────────────

class TestComputeHallucinationRates:
    def _make_rec_with_gov(self, claims: list[dict]) -> dict:
        return {"gov_check": {"claim_detail": claims}}

    def test_zero_hallucinations(self):
        recs = [
            self._make_rec_with_gov([
                {"agent": "technical", "status": "VERIFIED"},
                {"agent": "technical", "status": "VERIFIED"},
            ])
        ]
        rates = _compute_hallucination_rates(recs)
        assert rates.get("technical", 0.0) == 0.0

    def test_fifty_percent_hallucination(self):
        recs = [
            self._make_rec_with_gov([
                {"agent": "fundamental", "status": "VERIFIED"},
                {"agent": "fundamental", "status": "CONTRADICTED"},
            ])
        ]
        rates = _compute_hallucination_rates(recs)
        assert rates["fundamental"] == 50.0

    def test_missing_gov_check_skipped(self):
        recs = [{"gov_check": None}, {"gov_check": {}}]
        rates = _compute_hallucination_rates(recs)
        assert rates == {}

    def test_multiple_recs_aggregated(self):
        recs = [
            self._make_rec_with_gov([{"agent": "technical", "status": "VERIFIED"}]),
            self._make_rec_with_gov([{"agent": "technical", "status": "CONTRADICTED"}]),
        ]
        rates = _compute_hallucination_rates(recs)
        assert rates["technical"] == 50.0

    def test_status_case_insensitive(self):
        recs = [
            self._make_rec_with_gov([{"agent": "technical", "status": "contradicted"}])
        ]
        rates = _compute_hallucination_rates(recs)
        assert rates["technical"] == 100.0


# ──────────────────────────────────────────────────────────────────────────────
# TestPrevAccuracy
# ──────────────────────────────────────────────────────────────────────────────

class TestPrevAccuracy:
    def test_returns_float_when_found(self):
        client, chain = _mock_supabase()
        chain.execute.return_value = MagicMock(data=[{"accuracy_90d": 65.5}])
        result = _prev_accuracy(client, "technical")
        assert result == 65.5

    def test_returns_none_when_no_rows(self):
        client, chain = _mock_supabase()
        chain.execute.return_value = MagicMock(data=[])
        result = _prev_accuracy(client, "technical")
        assert result is None

    def test_returns_none_on_exception(self):
        client, chain = _mock_supabase()
        chain.execute.side_effect = Exception("DB err")
        result = _prev_accuracy(client, "technical")
        assert result is None


# ──────────────────────────────────────────────────────────────────────────────
# TestUpsertAgentPerformance
# ──────────────────────────────────────────────────────────────────────────────

class TestUpsertAgentPerformance:
    def test_dry_run_prints_without_db(self, capsys):
        _upsert_agent_performance(None, "technical", 75.0, None, dry_run=True)
        captured = capsys.readouterr().out
        assert "[DRY RUN]" in captured
        assert "technical" in captured

    def test_improving_trend_detected(self):
        client, chain = _mock_supabase()
        # Previous accuracy was 70.0; new = 72.0; delta = +2 ≥ IMPROVING_THRESHOLD(1.0)
        chain.execute.return_value = MagicMock(data=[{"accuracy_90d": 70.0}])
        _upsert_agent_performance(client, "technical", 72.0, None, dry_run=False)
        insert_row = chain.insert.call_args[0][0]
        assert insert_row["trend"] == "IMPROVING"

    def test_degrading_trend_detected(self):
        client, chain = _mock_supabase()
        chain.execute.return_value = MagicMock(data=[{"accuracy_90d": 75.0}])
        _upsert_agent_performance(client, "technical", 73.0, None, dry_run=False)
        insert_row = chain.insert.call_args[0][0]
        assert insert_row["trend"] == "DEGRADING"

    def test_stable_trend_when_no_prev(self):
        client, chain = _mock_supabase()
        chain.execute.return_value = MagicMock(data=[])
        _upsert_agent_performance(client, "technical", 72.0, None, dry_run=False)
        insert_row = chain.insert.call_args[0][0]
        assert insert_row["trend"] == "STABLE"


# ──────────────────────────────────────────────────────────────────────────────
# Constants sanity checks
# ──────────────────────────────────────────────────────────────────────────────

class TestConstants:
    def test_trust_min_less_than_one(self):
        assert TRUST_MIN < 1.0

    def test_trust_max_greater_than_one(self):
        assert TRUST_MAX > 1.0

    def test_trust_high_threshold_between_one_and_max(self):
        assert 1.0 < TRUST_HIGH_THRESHOLD <= TRUST_MAX

    def test_default_accuracy_baseline_is_70(self):
        assert DEFAULT_ACCURACY_BASELINE == 70.0

    def test_hallucination_alert_pct_positive(self):
        assert HALLUCINATION_ALERT_PCT > 0

    def test_directional_buffer_positive(self):
        assert DIRECTIONAL_BUFFER_PCT > 0

    def test_hold_band_wider_than_buffer(self):
        assert HOLD_BAND_PCT > DIRECTIONAL_BUFFER_PCT
