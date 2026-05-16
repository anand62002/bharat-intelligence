"""
tests/test_fact_checker.py
pytest suite for governance/fact_checker.py — P4-C: Numerical Grounding Check

Tests the deterministic pre-LLM numerical verification layer:
  _extract_numeric_from_source()
  _numerical_grounding_check()
  Integration: _check_one() uses grounding before Haiku

Run from project root:
    pytest tests/test_fact_checker.py -v
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Optional
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from governance.fact_checker import (
    Claim,
    _NUMERIC_TOLERANCES,
    _extract_numeric_from_source,
    _numerical_grounding_check,
    _verify_claim,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_claim(
    metric_key:   str,
    claimed_value,
    data_source:  str = "screener_in",
    agent_name:   str = "fundamental",
    status:       str = "",
) -> Claim:
    return Claim(
        claim_text    = f"{metric_key} is {claimed_value}",
        metric_key    = metric_key,
        claimed_value = claimed_value,
        data_source   = data_source,
        agent_name    = agent_name,
        context_text  = "",
        status        = status,
    )


def _make_ohlcv(close_prices: list[float]) -> pd.DataFrame:
    """Return a minimal OHLCV DataFrame with the given close prices."""
    n = len(close_prices)
    return pd.DataFrame({
        "Close": close_prices,
        "High":  [p * 1.02 for p in close_prices],
        "Low":   [p * 0.98 for p in close_prices],
        "Open":  close_prices,
        "Volume":[1_000_000] * n,
    })


def _make_screener(pe=25.0, roce=22.0, promoter_holding=65.0,
                   ebitda_margin=20.0, debt_equity=0.5,
                   revenue_growth=15.0, promoter_pledging=2.0,
                   roe=18.0) -> dict:
    return {
        "pe": pe, "roce": roce, "roe": roe,
        "promoter_holding": promoter_holding,
        "promoter_pledging": promoter_pledging,
        "ebitda_margin": ebitda_margin,
        "debt_equity": debt_equity,
        "revenue_growth": revenue_growth,
    }


# ─── Unit: _extract_numeric_from_source ──────────────────────────────────────

class TestExtractNumericFromSource:

    def test_screener_returns_pe(self):
        snap = _make_screener(pe=22.5)
        result = _extract_numeric_from_source("pe", "screener_in", snap)
        assert result == pytest.approx(22.5)

    def test_screener_returns_roce(self):
        snap = _make_screener(roce=18.3)
        result = _extract_numeric_from_source("roce", "screener_in", snap)
        assert result == pytest.approx(18.3)

    def test_screener_returns_promoter_holding(self):
        snap = _make_screener(promoter_holding=71.2)
        result = _extract_numeric_from_source("promoter_holding", "screener_in", snap)
        assert result == pytest.approx(71.2)

    def test_screener_returns_promoter_pledging(self):
        snap = _make_screener(promoter_pledging=4.5)
        result = _extract_numeric_from_source("promoter_pledging", "screener_in", snap)
        assert result == pytest.approx(4.5)

    def test_screener_missing_metric_returns_none(self):
        snap = {"pe": 25.0}  # no ROCE key
        result = _extract_numeric_from_source("roce", "screener_in", snap)
        assert result is None

    def test_screener_none_value_returns_none(self):
        snap = {"pe": None}
        result = _extract_numeric_from_source("pe", "screener_in", snap)
        assert result is None

    def test_none_cached_data_returns_none(self):
        result = _extract_numeric_from_source("pe", "screener_in", None)
        assert result is None

    def test_ohlcv_returns_rsi(self):
        # Oscillating prices → RSI neither 0 nor 100 (not monotone)
        close = [100.0 + (i % 5 - 2) * 1.5 for i in range(30)]
        df = _make_ohlcv(close)
        result = _extract_numeric_from_source("rsi", "yfinance_ohlcv_1y", df)
        assert result is not None
        assert 0 <= result <= 100

    def test_ohlcv_returns_ema50(self):
        close = [100.0 + i * 0.1 for i in range(80)]
        df = _make_ohlcv(close)
        result = _extract_numeric_from_source("ema50", "yfinance_ohlcv_1y", df)
        assert result is not None
        assert result > 0

    def test_ohlcv_returns_ema20(self):
        close = [200.0] * 40
        df = _make_ohlcv(close)
        result = _extract_numeric_from_source("ema20", "yfinance_ohlcv_1y", df)
        assert result == pytest.approx(200.0, rel=0.01)

    def test_ohlcv_empty_dataframe_returns_none(self):
        df = pd.DataFrame({"Close": []})
        result = _extract_numeric_from_source("rsi", "yfinance_ohlcv_1y", df)
        assert result is None

    def test_unknown_source_returns_none(self):
        result = _extract_numeric_from_source("pe", "unknown_source", {"pe": 25.0})
        assert result is None

    def test_unknown_ohlcv_metric_returns_none(self):
        close = [100.0 + i for i in range(30)]
        df = _make_ohlcv(close)
        result = _extract_numeric_from_source("pe", "yfinance_ohlcv_1y", df)
        assert result is None


# ─── Unit: _numerical_grounding_check ────────────────────────────────────────

class TestNumericalGroundingCheckVerified:

    def test_within_relative_tolerance_pe_verified(self):
        """PE claimed 25.0, actual 24.0 → 4% diff, threshold 15% → VERIFIED."""
        claim = _make_claim("pe", 25.0)
        snap  = _make_screener(pe=24.0)
        cache = {"screener_in::TCS": snap}
        n = _numerical_grounding_check([claim], cache, "TCS")
        assert claim.status == "VERIFIED"
        assert n == 1

    def test_within_absolute_tolerance_promoter_verified(self):
        """Promoter holding claimed 65.0, actual 64.5 → 0.5pp diff, threshold ±2pp → VERIFIED."""
        claim = _make_claim("promoter_holding", 65.0)
        snap  = _make_screener(promoter_holding=64.5)
        cache = {"screener_in::RELIANCE": snap}
        n = _numerical_grounding_check([claim], cache, "RELIANCE")
        assert claim.status == "VERIFIED"
        assert n == 1

    def test_exactly_at_relative_threshold_verified(self):
        """PE claimed 25.0, actual 21.74 → exactly 15% diff → VERIFIED (boundary)."""
        # 25 / 21.74 - 1 ≈ 0.15 (15% relative)
        claim = _make_claim("pe", 25.0)
        snap  = _make_screener(pe=21.74)
        cache = {"screener_in::TEST": snap}
        _numerical_grounding_check([claim], cache, "TEST")
        assert claim.status == "VERIFIED"

    def test_exactly_at_absolute_threshold_verified(self):
        """Promoter holding claimed 65.0, actual 63.0 → exactly 2pp diff → VERIFIED."""
        claim = _make_claim("promoter_holding", 65.0)
        snap  = _make_screener(promoter_holding=63.0)
        cache = {"screener_in::TEST": snap}
        _numerical_grounding_check([claim], cache, "TEST")
        assert claim.status == "VERIFIED"

    def test_reason_populated_on_verified(self):
        claim = _make_claim("roce", 22.0)
        snap  = _make_screener(roce=22.0)
        cache = {"screener_in::TCS": snap}
        _numerical_grounding_check([claim], cache, "TCS")
        assert "Numerical check" in claim.reason
        assert "22.0" in claim.reason


class TestNumericalGroundingCheckContradicted:

    def test_outside_relative_tolerance_contradicted(self):
        """PE claimed 40.0, actual 22.5 → 78% diff >> 15% → CONTRADICTED."""
        claim = _make_claim("pe", 40.0)
        snap  = _make_screener(pe=22.5)
        cache = {"screener_in::HDFC": snap}
        n = _numerical_grounding_check([claim], cache, "HDFC")
        assert claim.status == "CONTRADICTED"
        assert n == 1

    def test_contradicted_sets_corrected_claim(self):
        """corrected_claim must contain actual value."""
        claim = _make_claim("pe", 40.0)
        snap  = _make_screener(pe=22.5)
        cache = {"screener_in::HDFC": snap}
        _numerical_grounding_check([claim], cache, "HDFC")
        assert "22.5" in claim.corrected_claim
        assert "40.0" in claim.corrected_claim

    def test_outside_absolute_tolerance_contradicted(self):
        """Promoter holding claimed 70.0, actual 60.0 → 10pp >> 2pp → CONTRADICTED."""
        claim = _make_claim("promoter_holding", 70.0)
        snap  = _make_screener(promoter_holding=60.0)
        cache = {"screener_in::INFOSYS": snap}
        _numerical_grounding_check([claim], cache, "INFOSYS")
        assert claim.status == "CONTRADICTED"

    def test_contradicted_reason_shows_diff(self):
        claim = _make_claim("promoter_holding", 70.0)
        snap  = _make_screener(promoter_holding=60.0)
        cache = {"screener_in::TEST": snap}
        _numerical_grounding_check([claim], cache, "TEST")
        assert "70.0" in claim.reason
        assert "60.0" in claim.reason

    def test_roce_contradicted_when_far_off(self):
        """ROCE claimed 30%, actual 8% → 275% relative diff → CONTRADICTED."""
        claim = _make_claim("roce", 30.0)
        snap  = _make_screener(roce=8.0)
        cache = {"screener_in::BADCO": snap}
        _numerical_grounding_check([claim], cache, "BADCO")
        assert claim.status == "CONTRADICTED"
        assert "8.0" in claim.corrected_claim


class TestNumericalGroundingCheckSkips:

    def test_no_tolerance_metric_skipped(self):
        """upside_pct has no entry in _NUMERIC_TOLERANCES → status unchanged."""
        # Make sure upside_pct is not in tolerances
        assert "upside_pct" not in _NUMERIC_TOLERANCES
        claim = _make_claim("upside_pct", 35.0)
        cache = {"screener_in::TCS": _make_screener()}
        n = _numerical_grounding_check([claim], cache, "TCS")
        assert claim.status == ""
        assert n == 0

    def test_non_numeric_claimed_value_skipped(self):
        """String claimed_value → can't compare → status unchanged."""
        claim = _make_claim("pe", "bullish")
        cache = {"screener_in::TCS": _make_screener(pe=25.0)}
        n = _numerical_grounding_check([claim], cache, "TCS")
        assert claim.status == ""
        assert n == 0

    def test_missing_source_data_skipped(self):
        """No cached data for symbol → status unchanged → Haiku handles it."""
        claim = _make_claim("pe", 25.0)
        cache = {}  # nothing cached
        n = _numerical_grounding_check([claim], cache, "TCS")
        assert claim.status == ""
        assert n == 0

    def test_already_resolved_claim_skipped(self):
        """Claim already VERIFIED from a previous pass → not re-processed."""
        claim = _make_claim("pe", 25.0, status="VERIFIED")
        claim.reason = "already resolved"
        cache = {"screener_in::TCS": _make_screener(pe=99.0)}  # contradicting data
        n = _numerical_grounding_check([claim], cache, "TCS")
        # Should still be VERIFIED, reason unchanged
        assert claim.status == "VERIFIED"
        assert claim.reason == "already resolved"
        assert n == 0

    def test_metric_missing_in_cached_data_skipped(self):
        """Metric not present in screener snapshot → status unchanged."""
        claim = _make_claim("roce", 22.0)
        snap  = {"pe": 25.0}  # ROCE not in snap
        cache = {"screener_in::TCS": snap}
        n = _numerical_grounding_check([claim], cache, "TCS")
        assert claim.status == ""
        assert n == 0

    def test_actual_value_none_skipped(self):
        """Metric key present but value is None → status unchanged."""
        claim = _make_claim("pe", 25.0)
        snap  = {"pe": None}
        cache = {"screener_in::TCS": snap}
        n = _numerical_grounding_check([claim], cache, "TCS")
        assert claim.status == ""
        assert n == 0


class TestNumericalGroundingCheckMixed:

    def test_multiple_claims_mixed_results(self):
        """Mix of verified, contradicted, and skipped claims in one pass."""
        claims = [
            _make_claim("pe",                25.0),   # actual=24.0 → VERIFIED (4% diff < 15%)
            _make_claim("roce",              30.0),   # actual=8.0  → CONTRADICTED (275% diff > 10%)
            _make_claim("promoter_holding",  65.0),   # actual=64.5 → VERIFIED (0.5pp < 2pp)
            _make_claim("upside_pct",        40.0),   # no tolerance → skipped
        ]
        snap  = _make_screener(pe=24.0, roce=8.0, promoter_holding=64.5)
        cache = {"screener_in::TCS": snap}

        n = _numerical_grounding_check(claims, cache, "TCS")

        assert claims[0].status == "VERIFIED"
        assert claims[1].status == "CONTRADICTED"
        assert claims[2].status == "VERIFIED"
        assert claims[3].status == ""     # skipped — no tolerance defined
        assert n == 3

    def test_returns_correct_resolved_count(self):
        """Count only includes claims that got a deterministic result."""
        claims = [
            _make_claim("pe",       25.0),  # → resolved
            _make_claim("roce",     22.0),  # → resolved
            _make_claim("upside_pct",40.0), # → skipped
        ]
        snap  = _make_screener(pe=25.0, roce=22.0)
        cache = {"screener_in::X": snap}
        n = _numerical_grounding_check(claims, cache, "X")
        assert n == 2

    def test_zero_actual_value_handled(self):
        """actual=0 edge case — no division by zero."""
        claim = _make_claim("pe", 0.5)
        snap  = {"pe": 0.0}
        cache = {"screener_in::TEST": snap}
        # actual=0, claimed=0.5 → use_absolute=False → actual==0 branch → claimed != 0 → CONTRADICTED
        _numerical_grounding_check([claim], cache, "TEST")
        # Should be CONTRADICTED (claimed 0.5 ≠ 0)
        assert claim.status == "CONTRADICTED"


# ─── Unit: _verify_claim skip behaviour ──────────────────────────────────────

class TestVerifyClaimSkipsResolved:

    def test_already_verified_claim_not_sent_to_haiku(self):
        """_verify_claim must return immediately if status already set."""
        claim = _make_claim("pe", 25.0, status="VERIFIED")
        claim.reason = "pre-resolved"
        mock_client = MagicMock()

        result = _verify_claim(claim, "TCS", "fake_prompt", mock_client)

        mock_client.messages.create.assert_not_called()
        assert result.status == "VERIFIED"
        assert result.reason == "pre-resolved"

    def test_already_contradicted_claim_not_sent_to_haiku(self):
        claim = _make_claim("pe", 40.0, status="CONTRADICTED")
        claim.reason  = "grounding check"
        claim.corrected_claim = "actual pe is 22.5, not 40.0"
        mock_client = MagicMock()

        result = _verify_claim(claim, "TCS", "fake_prompt", mock_client)

        mock_client.messages.create.assert_not_called()
        assert result.status == "CONTRADICTED"

    def test_unresolved_claim_sent_to_haiku(self):
        """Status='' → Haiku is called."""
        claim = _make_claim("pe", 25.0)  # status=""
        mock_client = MagicMock()
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text='{"status":"VERIFIED","reason":"ok","corrected_claim":""}')]
        mock_client.messages.create.return_value = mock_msg

        _verify_claim(claim, "TCS", "Prompt: {claim} {text} {symbol} {source_name} {agent_name}", mock_client)

        mock_client.messages.create.assert_called_once()
        assert claim.status == "VERIFIED"


# ─── Integration: grounding resolves before Haiku in _check_one ──────────────

class TestGroundingIntegration:

    @patch("governance.fact_checker._supabase", return_value=None)
    @patch("governance.fact_checker._extract_claims")
    @patch("governance.fact_checker._haiku_client")
    def test_grounded_claims_skip_haiku_call(
        self, mock_haiku_client, mock_extract_claims, mock_sb
    ):
        """
        When all claims can be resolved by numerical grounding,
        Haiku should not be called at all.

        Note: MIN_CLAIMS_FOR_CHECK=3 so we must return ≥3 claims to avoid
        the early-exit guard. All 3 here are numerically grounded.
        """
        snap = _make_screener(pe=24.0, roce=22.0, promoter_holding=65.0)
        # All 3 within tolerance: PE 25→24 (4%<15%), ROCE 22→22 (0%<10%), holding 65→65 (0pp<2pp)
        claims = [
            _make_claim("pe",               25.0),
            _make_claim("roce",             22.0),
            _make_claim("promoter_holding", 65.0),
        ]
        source_cache_contents = {"screener_in::RELIANCE": snap}

        def fake_extract(rec, agent_results, source_cache):
            source_cache.update(source_cache_contents)
            return claims
        mock_extract_claims.side_effect = fake_extract

        mock_client = MagicMock()
        mock_haiku_client.return_value = mock_client

        from governance.fact_checker import _check_one
        rec = {"symbol": "RELIANCE", "confidence": 75.0}

        _check_one(rec, {}, "fake_prompt", mock_client, {}, dry_run=True)

        # Haiku should NOT have been called — all 3 resolved by grounding
        mock_client.messages.create.assert_not_called()
        assert all(c.status == "VERIFIED" for c in claims)

    @patch("governance.fact_checker._supabase", return_value=None)
    @patch("governance.fact_checker._extract_claims")
    @patch("governance.fact_checker._haiku_client")
    def test_unresolvable_claims_still_go_to_haiku(
        self, mock_haiku_client, mock_extract_claims, mock_sb
    ):
        """
        Claims with no tolerance defined (e.g. upside_pct) must still be
        sent to Haiku for verification.

        Setup: 3 claims returned (meets MIN_CLAIMS_FOR_CHECK threshold).
        Two are grounded by PE/ROCE; one (upside_pct) has no tolerance → Haiku.
        """
        snap = _make_screener(pe=25.0, roce=22.0)
        claims = [
            _make_claim("pe",         25.0),   # → VERIFIED by grounding
            _make_claim("roce",       22.0),   # → VERIFIED by grounding
            _make_claim("upside_pct", 35.0),   # no tolerance → Haiku
        ]
        source_cache_contents = {"screener_in::TCS": snap}

        def fake_extract(rec, agent_results, source_cache):
            source_cache.update(source_cache_contents)
            return claims
        mock_extract_claims.side_effect = fake_extract

        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(
            text='{"status":"VERIFIED","reason":"plausible upside","corrected_claim":""}'
        )]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_msg
        mock_haiku_client.return_value = mock_client

        from governance.fact_checker import _check_one
        rec = {"symbol": "TCS", "confidence": 70.0}

        _check_one(rec, {}, "Prompt: {claim} {text} {symbol} {source_name} {agent_name}",
                   mock_client, {}, dry_run=True)

        # Haiku called exactly once — for upside_pct only
        mock_client.messages.create.assert_called_once()

    def test_tolerance_map_coverage(self):
        """Sanity check that all key fundamental metrics have tolerances defined."""
        required_metrics = {
            "pe", "revenue_growth", "ebitda_margin", "debt_equity",
            "roce", "promoter_holding", "promoter_pledging",
        }
        missing = required_metrics - set(_NUMERIC_TOLERANCES.keys())
        assert not missing, f"Missing tolerances for: {missing}"

    def test_tolerance_values_are_positive(self):
        """All tolerance values must be positive numbers."""
        for key, (tol, _) in _NUMERIC_TOLERANCES.items():
            assert tol > 0, f"Tolerance for {key} must be > 0, got {tol}"

    def test_relative_tolerances_below_100_pct(self):
        """Relative tolerances must be < 1.0 (i.e. < 100%)."""
        for key, (tol, use_abs) in _NUMERIC_TOLERANCES.items():
            if not use_abs:
                assert tol < 1.0, (
                    f"Relative tolerance for {key} is {tol:.0%} — "
                    f"must be <100% or it would always VERIFY"
                )
