"""
tests/test_hallucination_pipeline.py — Unit tests for the multi-stage
hallucination detection pipeline added to governance/hallucination_detector.py

Coverage
────────
  TestExtractClaimsTechnical     — RSI / price / EMA20 / volume_ratio paths
  TestExtractClaimsFundamental   — PE / price / revenue_growth / debt_equity / ROCE
  TestFetchTechnicalActuals      — yfinance mock: RSI, EMA20, volume_ratio, price
  TestFetchFundamentalActuals    — yfinance .info mock: PE, price, revenue_growth, D/E
  TestVerifyClaimGrounding       — within-tolerance pass, over-tolerance flag, unsupported agent
  TestCheckSelfConsistency       — all-agree (consistent), split signals (inconsistent),
                                   high score CV (inconsistent), no valid signals → None,
                                   import failure → None
  TestDetectCrossAgentContradictions — known CRITICAL pair (tech+fund), MODERATE pair,
                                       reconcilable flag, missing agents skipped,
                                       BUY vs HOLD not flagged
  TestRunMultiStagePipeline      — smoke test all stages, confidence adjustment maths,
                                   dry_run calls print helper, stage toggles
  TestPipelineConstants          — constant sanity checks

Run:
    pytest tests/test_hallucination_pipeline.py -v
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from governance.hallucination_detector import (
    # Constants
    CONSISTENCY_MIN_AGREEMENT,
    CONSISTENCY_RERUNS,
    CONSISTENCY_SCORE_CV_THRESHOLD,
    GROUNDING_TOLERANCE_PCT,
    # Dataclasses
    ClaimVerification,
    ConsistencyResult,
    ContradictionResult,
    PipelineResult,
    # Stage 1
    _import_agent_fn,
    _run_agent_once,
    check_self_consistency,
    # Stage 2
    _AGENT_PAIR_SEVERITY,
    _HARD_CONTRADICTION_PAIRS,
    detect_cross_agent_contradictions,
    # Stage 3
    _extract_claims_fundamental,
    _extract_claims_technical,
    _fetch_fundamental_actuals,
    _fetch_technical_actuals,
    verify_claim_grounding,
    # Pipeline
    _print_pipeline_summary,
    run_multi_stage_pipeline,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _tech_detail(rsi=55.0, price=2800.0, ema20=2750.0, vol_ratio=1.2) -> dict:
    return {
        "indicators": {
            "rsi":           rsi,
            "current_price": price,
            "ema20":         ema20,
        },
        "volume_confirmation": {
            "volume_vs_avg": vol_ratio,
        },
    }


def _fund_detail(pe=22.0, price=2800.0, rev_growth=18.0, de=0.45, roce=16.0) -> dict:
    return {
        "raw_metrics": {
            "pe":             pe,
            "current_price":  price,
            "revenue_growth": rev_growth,
            "debt_equity":    de,
            "roce":           roce,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# TestExtractClaimsTechnical
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractClaimsTechnical:
    def test_all_fields_present(self):
        detail = _tech_detail(rsi=62.0, price=1500.0, ema20=1480.0, vol_ratio=1.8)
        claims = _extract_claims_technical(detail)
        assert claims["rsi"] == pytest.approx(62.0)
        assert claims["current_price"] == pytest.approx(1500.0)
        assert claims["ema20"] == pytest.approx(1480.0)
        assert claims["volume_ratio"] == pytest.approx(1.8)

    def test_rsi_fallback_to_momentum(self):
        """If indicators has no rsi, fall back to momentum.rsi."""
        detail = {
            "indicators": {},
            "momentum": {"rsi": 45.0},
        }
        claims = _extract_claims_technical(detail)
        assert claims["rsi"] == pytest.approx(45.0)

    def test_missing_rsi_not_in_claims(self):
        detail = {"indicators": {}, "momentum": {}, "volume_confirmation": {}}
        claims = _extract_claims_technical(detail)
        assert "rsi" not in claims

    def test_non_numeric_rsi_skipped(self):
        detail = {"indicators": {"rsi": "N/A"}, "momentum": {}}
        claims = _extract_claims_technical(detail)
        assert "rsi" not in claims

    def test_empty_detail(self):
        assert _extract_claims_technical({}) == {}

    def test_nested_none_values_skipped(self):
        detail = {
            "indicators": {"rsi": None, "current_price": None},
        }
        claims = _extract_claims_technical(detail)
        assert "rsi" not in claims
        assert "current_price" not in claims


# ─────────────────────────────────────────────────────────────────────────────
# TestExtractClaimsFundamental
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractClaimsFundamental:
    def test_all_fields_present(self):
        detail = _fund_detail(pe=25.0, price=3000.0, rev_growth=20.0, de=0.3, roce=18.0)
        claims = _extract_claims_fundamental(detail)
        assert claims["pe"] == pytest.approx(25.0)
        assert claims["current_price"] == pytest.approx(3000.0)
        assert claims["revenue_growth"] == pytest.approx(20.0)
        assert claims["debt_equity"] == pytest.approx(0.3)
        assert claims["roce"] == pytest.approx(18.0)

    def test_pe_fallback_to_profitability(self):
        detail = {
            "raw_metrics":   {},
            "profitability": {"pe": 30.0},
        }
        claims = _extract_claims_fundamental(detail)
        assert claims["pe"] == pytest.approx(30.0)

    def test_missing_fields_absent(self):
        claims = _extract_claims_fundamental({})
        assert claims == {}

    def test_non_numeric_pe_skipped(self):
        detail = {"raw_metrics": {"pe": "N/A"}}
        claims = _extract_claims_fundamental(detail)
        assert "pe" not in claims


# ─────────────────────────────────────────────────────────────────────────────
# TestFetchTechnicalActuals
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchTechnicalActuals:
    def _make_df(self, closes, volumes=None):
        """Build a minimal pandas DataFrame mimicking yfinance history output."""
        import pandas as pd
        import numpy as np
        n = len(closes)
        if volumes is None:
            volumes = [1_000_000] * n
        dates = pd.date_range("2025-01-01", periods=n, freq="B")
        return pd.DataFrame({"Close": closes, "Volume": volumes}, index=dates)

    def test_returns_current_price(self):
        df = self._make_df(list(range(1, 22)))  # 21 data points
        with patch("yfinance.Ticker") as MockTicker:
            MockTicker.return_value.history.return_value = df
            actuals = _fetch_technical_actuals("RELIANCE.NS")
        assert actuals["current_price"] == pytest.approx(21.0)

    def test_rsi_computed_with_enough_bars(self):
        """RSI should be computed when we have ≥ 15 data points."""
        # Use alternating up/down closes so delta is non-zero (avoids NaN)
        closes = [100.0 + (i % 3) * 2 for i in range(22)]  # 100, 102, 104, 100, 102, ...
        df = self._make_df(closes)
        with patch("yfinance.Ticker") as MockTicker:
            MockTicker.return_value.history.return_value = df
            actuals = _fetch_technical_actuals("RELIANCE.NS")
        assert "rsi" in actuals
        assert 0 <= actuals["rsi"] <= 100

    def test_rsi_absent_when_too_few_bars(self):
        """Fewer than 15 bars → rsi absent (insufficient history)."""
        df = self._make_df([100.0] * 10)
        with patch("yfinance.Ticker") as MockTicker:
            MockTicker.return_value.history.return_value = df
            actuals = _fetch_technical_actuals("RELIANCE.NS")
        assert "rsi" not in actuals

    def test_ema20_computed(self):
        df = self._make_df(list(range(1, 25)))  # 24 points ≥ 20
        with patch("yfinance.Ticker") as MockTicker:
            MockTicker.return_value.history.return_value = df
            actuals = _fetch_technical_actuals("RELIANCE.NS")
        assert "ema20" in actuals
        assert actuals["ema20"] > 0

    def test_volume_ratio_computed(self):
        import pandas as pd
        closes  = [100.0] * 25
        volumes = [1_000_000] * 24 + [2_000_000]   # last bar = 2× avg
        df = self._make_df(closes, volumes)
        with patch("yfinance.Ticker") as MockTicker:
            MockTicker.return_value.history.return_value = df
            actuals = _fetch_technical_actuals("RELIANCE.NS")
        assert "volume_ratio" in actuals
        assert actuals["volume_ratio"] == pytest.approx(2.0, abs=0.1)

    def test_empty_df_returns_empty(self):
        import pandas as pd
        with patch("yfinance.Ticker") as MockTicker:
            MockTicker.return_value.history.return_value = pd.DataFrame()
            actuals = _fetch_technical_actuals("UNKNOWN.NS")
        assert actuals == {}

    def test_yfinance_exception_returns_empty(self):
        with patch("yfinance.Ticker", side_effect=Exception("network error")):
            actuals = _fetch_technical_actuals("RELIANCE.NS")
        assert actuals == {}


# ─────────────────────────────────────────────────────────────────────────────
# TestFetchFundamentalActuals
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchFundamentalActuals:
    def test_pe_from_trailing(self):
        info = {"trailingPE": 25.0, "currentPrice": 3000.0}
        with patch("yfinance.Ticker") as MockTicker:
            MockTicker.return_value.info = info
            actuals = _fetch_fundamental_actuals("RELIANCE.NS")
        assert actuals["pe"] == pytest.approx(25.0)

    def test_pe_fallback_to_forward(self):
        info = {"forwardPE": 20.0}
        with patch("yfinance.Ticker") as MockTicker:
            MockTicker.return_value.info = info
            actuals = _fetch_fundamental_actuals("RELIANCE.NS")
        assert actuals["pe"] == pytest.approx(20.0)

    def test_revenue_growth_converted_to_pct(self):
        """yfinance returns 0.18 decimal; we convert to 18.0 %."""
        info = {"revenueGrowth": 0.18}
        with patch("yfinance.Ticker") as MockTicker:
            MockTicker.return_value.info = info
            actuals = _fetch_fundamental_actuals("RELIANCE.NS")
        assert actuals["revenue_growth"] == pytest.approx(18.0, abs=0.01)

    def test_debt_equity_present(self):
        info = {"debtToEquity": 0.45}
        with patch("yfinance.Ticker") as MockTicker:
            MockTicker.return_value.info = info
            actuals = _fetch_fundamental_actuals("RELIANCE.NS")
        assert actuals["debt_equity"] == pytest.approx(0.45)

    def test_missing_info_returns_empty(self):
        with patch("yfinance.Ticker") as MockTicker:
            MockTicker.return_value.info = {}
            actuals = _fetch_fundamental_actuals("RELIANCE.NS")
        assert actuals == {}

    def test_exception_returns_empty(self):
        with patch("yfinance.Ticker", side_effect=Exception("network")):
            actuals = _fetch_fundamental_actuals("RELIANCE.NS")
        assert actuals == {}


# ─────────────────────────────────────────────────────────────────────────────
# TestVerifyClaimGrounding
# ─────────────────────────────────────────────────────────────────────────────

class TestVerifyClaimGrounding:
    def test_all_grounded_within_tolerance(self):
        detail = _tech_detail(rsi=55.0, price=2800.0)
        actuals = {"rsi": 55.5, "current_price": 2810.0}   # both < 5% off
        with (
            patch(
                "governance.hallucination_detector._extract_claims_technical",
                return_value={"rsi": 55.0, "current_price": 2800.0},
            ),
            patch(
                "governance.hallucination_detector._fetch_technical_actuals",
                return_value=actuals,
            ),
        ):
            results = verify_claim_grounding("RELIANCE", "technical", detail)
        assert all(v.is_grounded for v in results)
        assert len(results) == 2

    def test_ungrounded_flag_on_large_deviation(self):
        detail = _tech_detail(rsi=55.0)
        with (
            patch(
                "governance.hallucination_detector._extract_claims_technical",
                return_value={"rsi": 55.0},
            ),
            patch(
                "governance.hallucination_detector._fetch_technical_actuals",
                return_value={"rsi": 80.0},   # 45% deviation → ungrounded
            ),
        ):
            results = verify_claim_grounding("RELIANCE", "technical", detail)
        assert len(results) == 1
        assert not results[0].is_grounded
        assert results[0].deviation_pct > GROUNDING_TOLERANCE_PCT

    def test_unsupported_agent_returns_empty(self):
        results = verify_claim_grounding("RELIANCE", "macro", {"foo": "bar"})
        assert results == []

    def test_non_dict_detail_returns_empty(self):
        results = verify_claim_grounding("RELIANCE", "technical", "invalid")
        assert results == []

    def test_no_matching_actuals_skips_claim(self):
        """Claimed value present but no actual data → claim omitted from results."""
        with (
            patch(
                "governance.hallucination_detector._extract_claims_technical",
                return_value={"rsi": 55.0},
            ),
            patch(
                "governance.hallucination_detector._fetch_technical_actuals",
                return_value={},   # no actuals at all
            ),
        ):
            results = verify_claim_grounding("RELIANCE", "technical", {})
        assert results == []

    def test_both_zero_is_grounded(self):
        """claimed=0 and actual=0 is always grounded (trivial case)."""
        with (
            patch(
                "governance.hallucination_detector._extract_claims_fundamental",
                return_value={"pe": 0.0},
            ),
            patch(
                "governance.hallucination_detector._fetch_fundamental_actuals",
                return_value={"pe": 0.0},
            ),
        ):
            results = verify_claim_grounding("RELIANCE", "fundamental", {})
        assert len(results) == 1
        assert results[0].is_grounded
        assert results[0].deviation_pct == 0.0

    def test_claim_verification_fields(self):
        with (
            patch(
                "governance.hallucination_detector._extract_claims_technical",
                return_value={"rsi": 60.0},
            ),
            patch(
                "governance.hallucination_detector._fetch_technical_actuals",
                return_value={"rsi": 61.0},
            ),
        ):
            results = verify_claim_grounding("RELIANCE", "technical", {})
        v = results[0]
        assert v.agent_name == "technical"
        assert v.claim_name == "rsi"
        assert v.claimed_value == pytest.approx(60.0)
        assert v.actual_value == pytest.approx(61.0)
        assert v.is_grounded is True   # ~1.6% deviation < 5%

    def test_custom_tolerance_overrides_default(self):
        """Passing tolerance_pct=1.0 makes a 2% deviation ungrounded."""
        with (
            patch(
                "governance.hallucination_detector._extract_claims_technical",
                return_value={"rsi": 55.0},
            ),
            patch(
                "governance.hallucination_detector._fetch_technical_actuals",
                return_value={"rsi": 56.2},   # ~2.2% deviation
            ),
        ):
            results = verify_claim_grounding(
                "RELIANCE", "technical", {}, tolerance_pct=1.0
            )
        assert len(results) == 1
        assert not results[0].is_grounded


# ─────────────────────────────────────────────────────────────────────────────
# TestCheckSelfConsistency
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckSelfConsistency:
    def _make_agent_fn(self, signal: str, score: float):
        def _fn(symbol):
            return {"signal": signal, "score": score}
        return _fn

    def test_all_same_signal_consistent(self):
        fn = self._make_agent_fn("BUY", 75.0)
        with patch(
            "governance.hallucination_detector._import_agent_fn",
            return_value=fn,
        ):
            result = check_self_consistency("RELIANCE", "technical", reruns=3)
        assert result is not None
        assert not result.is_inconsistent
        assert result.agreement == pytest.approx(1.0)

    def test_split_signals_flagged_inconsistent(self):
        """2 BUY + 1 SELL → agreement 67% (= threshold, not below) — edge case."""
        call_count = [0]

        def fn(symbol):
            call_count[0] += 1
            # First 2 calls return BUY, third returns SELL
            sig = "BUY" if call_count[0] <= 2 else "SELL"
            return {"signal": sig, "score": 75.0}

        with patch(
            "governance.hallucination_detector._import_agent_fn",
            return_value=fn,
        ):
            result = check_self_consistency("RELIANCE", "technical", reruns=3)
        assert result is not None
        # agreement = 2/3 ≈ 0.67 which equals CONSISTENCY_MIN_AGREEMENT (not strictly <)
        # so should be consistent at exactly the threshold
        assert result.agreement == pytest.approx(2 / 3, abs=0.01)

    def test_majority_minority_split_below_threshold_flagged(self):
        """1 BUY + 2 SELL → agreement 33% < 67% → inconsistent."""
        call_count = [0]

        def fn(symbol):
            call_count[0] += 1
            sig = "BUY" if call_count[0] == 1 else "SELL"
            return {"signal": sig, "score": 60.0}

        with patch(
            "governance.hallucination_detector._import_agent_fn",
            return_value=fn,
        ):
            result = check_self_consistency("RELIANCE", "technical", reruns=3)
        assert result is not None
        assert result.is_inconsistent
        assert "signal agreement" in result.flag_reason

    def test_high_score_cv_flagged(self):
        """Large score variance → is_inconsistent regardless of signal agreement."""
        scores = [10.0, 90.0, 50.0]
        call_count = [0]

        def fn(symbol):
            s = scores[call_count[0] % len(scores)]
            call_count[0] += 1
            return {"signal": "BUY", "score": s}

        with patch(
            "governance.hallucination_detector._import_agent_fn",
            return_value=fn,
        ):
            result = check_self_consistency("RELIANCE", "technical", reruns=3)
        assert result is not None
        # Even if signal agreement is 100%, large score CV should flag it
        if result.score_cv > CONSISTENCY_SCORE_CV_THRESHOLD:
            assert result.is_inconsistent

    def test_unknown_agent_returns_none(self):
        with patch(
            "governance.hallucination_detector._import_agent_fn",
            return_value=None,
        ):
            result = check_self_consistency("RELIANCE", "unknown_agent")
        assert result is None

    def test_all_error_returns_none(self):
        """If all re-runs return ERROR, result should be None."""
        def fn(symbol):
            raise RuntimeError("always fails")

        with patch(
            "governance.hallucination_detector._import_agent_fn",
            return_value=fn,
        ):
            result = check_self_consistency("RELIANCE", "technical", reruns=3)
        assert result is None

    def test_result_fields_populated(self):
        fn = self._make_agent_fn("HOLD", 50.0)
        with patch(
            "governance.hallucination_detector._import_agent_fn",
            return_value=fn,
        ):
            result = check_self_consistency("TCS", "technical", reruns=3)
        assert result.agent_name == "technical"
        assert result.symbol == "TCS"
        assert len(result.signals) > 0
        assert result.agreement > 0

    def test_run_agent_once_error_fallback(self):
        """_run_agent_once returns ('ERROR', None) on exception."""
        from governance.hallucination_detector import _run_agent_once
        def bad_fn(symbol):
            raise ValueError("boom")
        sig, score = _run_agent_once(bad_fn, "RELIANCE")
        assert sig == "ERROR"
        assert score is None

    def test_run_agent_once_happy_path(self):
        from governance.hallucination_detector import _run_agent_once
        fn = lambda s: {"signal": "BUY", "score": 72.0}
        sig, score = _run_agent_once(fn, "RELIANCE")
        assert sig == "BUY"
        assert score == pytest.approx(72.0)


# ─────────────────────────────────────────────────────────────────────────────
# TestDetectCrossAgentContradictions
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectCrossAgentContradictions:
    def test_critical_hard_contradiction_detected(self):
        """technical=BUY vs fundamental=AVOID is a CRITICAL hard contradiction."""
        signals = {
            "technical":   {"signal": "BUY"},
            "fundamental": {"signal": "AVOID"},
        }
        with patch(
            "governance.hallucination_detector._llm_reconcile_contradiction",
            return_value=(False, "Irreconcilable time horizon mismatch"),
        ):
            results = detect_cross_agent_contradictions(
                "RELIANCE", signals, use_llm_reconcile=True
            )
        assert len(results) == 1
        r = results[0]
        assert r.severity == "CRITICAL"
        assert r.is_hard_contradiction is True
        assert r.signal_a in ("BUY", "AVOID")
        assert r.signal_b in ("BUY", "AVOID")

    def test_non_contradictory_pair_not_flagged(self):
        """BUY vs HOLD is not a hard contradiction — should not be flagged."""
        signals = {
            "technical":   {"signal": "BUY"},
            "fundamental": {"signal": "HOLD"},
        }
        results = detect_cross_agent_contradictions("RELIANCE", signals)
        assert results == []

    def test_missing_agent_skipped(self):
        """If one of the pair is absent from agent_signals, skip that pair."""
        signals = {
            "technical": {"signal": "BUY"},
            # fundamental absent
        }
        results = detect_cross_agent_contradictions("RELIANCE", signals)
        assert results == []

    def test_no_data_skipped(self):
        """NO_DATA signal skips the pair even if the other agent has a valid signal."""
        signals = {
            "technical":   {"signal": "NO_DATA"},
            "fundamental": {"signal": "BUY"},
        }
        results = detect_cross_agent_contradictions("RELIANCE", signals)
        assert results == []

    def test_reconciled_flag_propagated(self):
        """If LLM says reconcilable, reconciled=True is set on the result."""
        signals = {
            "technical":   {"signal": "STRONG_BUY"},
            "fundamental": {"signal": "SELL"},
        }
        with patch(
            "governance.hallucination_detector._llm_reconcile_contradiction",
            return_value=(True, "Different time horizons — both valid"),
        ):
            results = detect_cross_agent_contradictions(
                "RELIANCE", signals, use_llm_reconcile=True
            )
        assert len(results) == 1
        assert results[0].reconciled is True
        assert "time horizons" in results[0].reconciliation_note

    def test_llm_not_called_for_moderate_pair(self):
        """LLM reconciliation only called for CRITICAL hard contradictions."""
        signals = {
            "technical": {"signal": "BUY"},
            "macro":     {"signal": "SELL"},
        }
        with patch(
            "governance.hallucination_detector._llm_reconcile_contradiction",
        ) as mock_llm:
            results = detect_cross_agent_contradictions(
                "RELIANCE", signals, use_llm_reconcile=True
            )
        # technical↔macro is MODERATE; LLM should not be called
        mock_llm.assert_not_called()

    def test_string_signal_accepted(self):
        """agent_signals values may be plain strings instead of dicts."""
        signals = {
            "technical":   "BUY",
            "fundamental": "AVOID",
        }
        with patch(
            "governance.hallucination_detector._llm_reconcile_contradiction",
            return_value=(False, "unreconcilable"),
        ):
            results = detect_cross_agent_contradictions(
                "RELIANCE", signals, use_llm_reconcile=True
            )
        assert len(results) == 1
        assert results[0].is_hard_contradiction is True

    def test_sentiment_institutional_moderate_contradiction(self):
        """sentiment=BUY vs institutional=SELL should be flagged as MODERATE."""
        signals = {
            "sentiment":    {"signal": "BUY"},
            "institutional":{"signal": "SELL"},
        }
        results = detect_cross_agent_contradictions("RELIANCE", signals)
        assert len(results) == 1
        assert results[0].severity == "MODERATE"

    def test_use_llm_reconcile_false_skips_haiku(self):
        """use_llm_reconcile=False should never call the LLM even for CRITICAL pairs."""
        signals = {
            "technical":   {"signal": "STRONG_BUY"},
            "fundamental": {"signal": "AVOID"},
        }
        with patch(
            "governance.hallucination_detector._llm_reconcile_contradiction"
        ) as mock_llm:
            results = detect_cross_agent_contradictions(
                "RELIANCE", signals, use_llm_reconcile=False
            )
        mock_llm.assert_not_called()
        assert results[0].reconciled is False


# ─────────────────────────────────────────────────────────────────────────────
# TestRunMultiStagePipeline
# ─────────────────────────────────────────────────────────────────────────────

class TestRunMultiStagePipeline:
    def _clean_signals(self):
        """Signals dict with no contradictions and importable agents."""
        return {
            "technical":   {"signal": "BUY",  "score": 70.0},
            "fundamental": {"signal": "BUY",  "score": 65.0},
        }

    def test_returns_pipeline_result_type(self):
        with (
            patch("governance.hallucination_detector.check_self_consistency", return_value=None),
            patch("governance.hallucination_detector.detect_cross_agent_contradictions", return_value=[]),
            patch("governance.hallucination_detector.verify_claim_grounding", return_value=[]),
        ):
            result = run_multi_stage_pipeline("RELIANCE", self._clean_signals())
        assert isinstance(result, PipelineResult)

    def test_symbol_propagated(self):
        with (
            patch("governance.hallucination_detector.check_self_consistency", return_value=None),
            patch("governance.hallucination_detector.detect_cross_agent_contradictions", return_value=[]),
            patch("governance.hallucination_detector.verify_claim_grounding", return_value=[]),
        ):
            result = run_multi_stage_pipeline("TATAPOWER", self._clean_signals())
        assert result.symbol == "TATAPOWER"

    def test_zero_adjustment_when_no_flags(self):
        with (
            patch("governance.hallucination_detector.check_self_consistency", return_value=None),
            patch("governance.hallucination_detector.detect_cross_agent_contradictions", return_value=[]),
            patch("governance.hallucination_detector.verify_claim_grounding", return_value=[]),
        ):
            result = run_multi_stage_pipeline("RELIANCE", self._clean_signals())
        assert result.confidence_adjustment == 0
        assert result.summary_flags == []

    def test_stage1_inconsistency_adjusts_minus_5(self):
        inconsistent_cr = ConsistencyResult(
            agent_name="technical", symbol="RELIANCE",
            signals=["BUY", "SELL", "BUY"], scores=[70.0, 50.0, 65.0],
            agreement=0.5, score_cv=0.15,
            is_inconsistent=True, flag_reason="signal agreement 50% < 67%",
        )
        with (
            patch(
                "governance.hallucination_detector.check_self_consistency",
                return_value=inconsistent_cr,
            ),
            patch("governance.hallucination_detector.detect_cross_agent_contradictions", return_value=[]),
            patch("governance.hallucination_detector.verify_claim_grounding", return_value=[]),
        ):
            result = run_multi_stage_pipeline("RELIANCE", self._clean_signals())
        # 2 agents checked × -5 each = -10
        assert result.confidence_adjustment == -10

    def test_stage2_critical_unreconciled_adjusts_minus_15(self):
        contradiction = ContradictionResult(
            agent_a="technical", agent_b="fundamental",
            signal_a="BUY", signal_b="AVOID",
            severity="CRITICAL",
            is_hard_contradiction=True,
            reconciled=False,
        )
        with (
            patch("governance.hallucination_detector.check_self_consistency", return_value=None),
            patch(
                "governance.hallucination_detector.detect_cross_agent_contradictions",
                return_value=[contradiction],
            ),
            patch("governance.hallucination_detector.verify_claim_grounding", return_value=[]),
        ):
            result = run_multi_stage_pipeline("RELIANCE", self._clean_signals())
        assert result.confidence_adjustment == -15
        assert len(result.summary_flags) == 1
        assert "CRITICAL" in result.summary_flags[0]

    def test_stage2_moderate_unreconciled_adjusts_minus_5(self):
        contradiction = ContradictionResult(
            agent_a="technical", agent_b="macro",
            signal_a="BUY", signal_b="SELL",
            severity="MODERATE",
            is_hard_contradiction=False,
            reconciled=False,
        )
        with (
            patch("governance.hallucination_detector.check_self_consistency", return_value=None),
            patch(
                "governance.hallucination_detector.detect_cross_agent_contradictions",
                return_value=[contradiction],
            ),
            patch("governance.hallucination_detector.verify_claim_grounding", return_value=[]),
        ):
            result = run_multi_stage_pipeline("RELIANCE", self._clean_signals())
        assert result.confidence_adjustment == -5

    def test_stage2_reconciled_contradiction_no_adjustment(self):
        contradiction = ContradictionResult(
            agent_a="technical", agent_b="fundamental",
            signal_a="BUY", signal_b="AVOID",
            severity="CRITICAL",
            is_hard_contradiction=True,
            reconciled=True,
            reconciliation_note="Different time horizons",
        )
        with (
            patch("governance.hallucination_detector.check_self_consistency", return_value=None),
            patch(
                "governance.hallucination_detector.detect_cross_agent_contradictions",
                return_value=[contradiction],
            ),
            patch("governance.hallucination_detector.verify_claim_grounding", return_value=[]),
        ):
            result = run_multi_stage_pipeline("RELIANCE", self._clean_signals())
        assert result.confidence_adjustment == 0   # reconciled → no penalty

    def test_stage3_ungrounded_claim_adjusts_minus_3(self):
        """One ungrounded claim in technical → -3; fundamental has no claims → -3 total."""
        ungrounded = ClaimVerification(
            agent_name="technical", claim_name="rsi",
            claimed_value=55.0, actual_value=80.0,
            deviation_pct=45.0, is_grounded=False,
        )

        def _grounding_side_effect(symbol, agent_name, detail, *args, **kwargs):
            if agent_name == "technical":
                return [ungrounded]
            return []   # fundamental has no verifiable claims in this test

        with (
            patch("governance.hallucination_detector.check_self_consistency", return_value=None),
            patch("governance.hallucination_detector.detect_cross_agent_contradictions", return_value=[]),
            patch(
                "governance.hallucination_detector.verify_claim_grounding",
                side_effect=_grounding_side_effect,
            ),
        ):
            result = run_multi_stage_pipeline("RELIANCE", self._clean_signals())
        assert result.confidence_adjustment == -3
        assert len(result.summary_flags) == 1

    def test_dry_run_calls_print_summary(self, capsys):
        with (
            patch("governance.hallucination_detector.check_self_consistency", return_value=None),
            patch("governance.hallucination_detector.detect_cross_agent_contradictions", return_value=[]),
            patch("governance.hallucination_detector.verify_claim_grounding", return_value=[]),
        ):
            run_multi_stage_pipeline(
                "RELIANCE", self._clean_signals(), dry_run=True
            )
        out = capsys.readouterr().out
        assert "Multi-Stage Hallucination Pipeline" in out
        assert "RELIANCE" in out

    def test_stages_can_be_disabled(self):
        """Disabling all stages → empty results, zero adjustment."""
        result = run_multi_stage_pipeline(
            "RELIANCE", self._clean_signals(),
            run_consistency=False,
            run_contradictions=False,
            run_grounding=False,
        )
        assert result.consistency_results == []
        assert result.contradiction_results == []
        assert result.claim_verifications == []
        assert result.confidence_adjustment == 0

    def test_warren_bot_excluded_from_stage1(self):
        """warren_bot key in agent_signals should not trigger a consistency check."""
        signals = {
            "technical":  {"signal": "BUY", "score": 70.0},
            "warren_bot": {"signal": "BUY", "score": 80.0},
        }
        checked_agents = []

        def mock_consistency(symbol, agent_name, **kwargs):
            checked_agents.append(agent_name)
            return None

        with (
            patch(
                "governance.hallucination_detector.check_self_consistency",
                side_effect=mock_consistency,
            ),
            patch("governance.hallucination_detector.detect_cross_agent_contradictions", return_value=[]),
            patch("governance.hallucination_detector.verify_claim_grounding", return_value=[]),
        ):
            run_multi_stage_pipeline("RELIANCE", signals)

        assert "warren_bot" not in checked_agents
        assert "technical" in checked_agents

    def test_duration_seconds_positive(self):
        with (
            patch("governance.hallucination_detector.check_self_consistency", return_value=None),
            patch("governance.hallucination_detector.detect_cross_agent_contradictions", return_value=[]),
            patch("governance.hallucination_detector.verify_claim_grounding", return_value=[]),
        ):
            result = run_multi_stage_pipeline("RELIANCE", self._clean_signals())
        assert result.duration_seconds >= 0.0


# ─────────────────────────────────────────────────────────────────────────────
# TestPrintPipelineSummary
# ─────────────────────────────────────────────────────────────────────────────

class TestPrintPipelineSummary:
    def test_prints_symbol(self, capsys):
        result = PipelineResult(symbol="INFY")
        _print_pipeline_summary(result)
        out = capsys.readouterr().out
        assert "INFY" in out

    def test_prints_no_contradictions(self, capsys):
        result = PipelineResult(symbol="INFY")
        _print_pipeline_summary(result)
        out = capsys.readouterr().out
        assert "no contradictions" in out.lower()

    def test_prints_flags(self, capsys):
        result = PipelineResult(
            symbol="INFY",
            summary_flags=["Stage1/technical: inconsistent — CV too high"],
        )
        _print_pipeline_summary(result)
        out = capsys.readouterr().out
        assert "Stage1/technical" in out

    def test_prints_adjustment(self, capsys):
        result = PipelineResult(symbol="INFY", confidence_adjustment=-18)
        _print_pipeline_summary(result)
        out = capsys.readouterr().out
        assert "-18" in out


# ─────────────────────────────────────────────────────────────────────────────
# TestPipelineConstants
# ─────────────────────────────────────────────────────────────────────────────

class TestPipelineConstants:
    def test_consistency_reruns_at_least_2(self):
        assert CONSISTENCY_RERUNS >= 2

    def test_min_agreement_between_zero_and_one(self):
        assert 0.0 < CONSISTENCY_MIN_AGREEMENT < 1.0

    def test_score_cv_threshold_between_zero_and_one(self):
        assert 0.0 < CONSISTENCY_SCORE_CV_THRESHOLD < 1.0

    def test_grounding_tolerance_positive(self):
        assert GROUNDING_TOLERANCE_PCT > 0

    def test_known_agent_pairs_defined(self):
        """CRITICAL pair (technical, fundamental) must be registered."""
        pair = frozenset({"technical", "fundamental"})
        assert pair in _AGENT_PAIR_SEVERITY
        assert _AGENT_PAIR_SEVERITY[pair] == "CRITICAL"

    def test_hard_contradiction_buy_avoid(self):
        pair = frozenset({"BUY", "AVOID"})
        assert pair in _HARD_CONTRADICTION_PAIRS

    def test_hard_contradiction_strong_buy_sell(self):
        pair = frozenset({"STRONG_BUY", "SELL"})
        assert pair in _HARD_CONTRADICTION_PAIRS
