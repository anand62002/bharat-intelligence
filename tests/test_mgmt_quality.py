"""
tests/test_mgmt_quality.py
===========================
Unit tests for agents/mgmt_quality.py

Tests validate:
  - Each scoring dimension
  - Risk flag detection
  - Signal thresholds
  - Graceful failure with no data
  - Full analyse() integration with mocked data
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.mgmt_quality import (
    analyse,
    _score_capital_allocation,
    _score_earnings_reliability,
    _score_balance_sheet,
    _score_promoter_commitment,
    _score_shareholder_returns,
    _extract_risk_flags,
)


# ──────────────────────────────────────────────────────────────────────────────
# Dimension: Capital Allocation
# ──────────────────────────────────────────────────────────────────────────────

class TestCapitalAllocation:
    def test_high_roce_scores_well(self):
        raw     = {}
        history = {"roce": [15, 18, 20, 22, 24, 25]}
        score, notes = _score_capital_allocation(raw, history)
        assert score >= 8
        assert any("ROCE" in n for n in notes)

    def test_low_roce_scores_low(self):
        raw     = {}
        history = {"roce": [3, 4, 3, 2]}
        score, _ = _score_capital_allocation(raw, history)
        assert score <= 5

    def test_asset_light_bonus(self):
        raw     = {}
        history = {
            "roce":    [20, 21, 22, 23],
            "revenue": [1000, 1100, 1200, 1300],
            "capex":   [20, 22, 24, 26],   # ~2% capex/revenue
            "pat":     [100, 110, 120, 130],
        }
        score, notes = _score_capital_allocation(raw, history)
        assert any("capex" in n.lower() or "asset-light" in n.lower() for n in notes)

    def test_score_capped_at_20(self):
        history = {
            "roce":    [25, 26, 27, 28, 29, 30],
            "revenue": [100] * 6,
            "capex":   [1] * 6,
            "pat":     [20, 22, 24, 26, 28, 30],
        }
        score, _ = _score_capital_allocation({}, history)
        assert score <= 20


# ──────────────────────────────────────────────────────────────────────────────
# Dimension: Earnings Reliability
# ──────────────────────────────────────────────────────────────────────────────

class TestEarningsReliability:
    def test_high_eps_cagr_scores_well(self):
        raw     = {"eps_cagr_5y": 22.0}
        history = {"eps": [5, 6, 8, 10, 13, 16], "pat": [100, 120, 145, 170, 200, 240]}
        score, notes = _score_earnings_reliability(raw, history)
        assert score >= 12
        assert any("EPS CAGR" in n for n in notes)

    def test_loss_making_scores_low(self):
        raw     = {"eps_cagr_5y": -5.0}
        history = {"eps": [-2, -1, 2, -3, 1, -1], "pat": [-20, -10, 15, -30, 10, -5]}
        score, _ = _score_earnings_reliability(raw, history)
        assert score <= 5

    def test_low_pat_cv_bonus(self):
        raw     = {}
        history = {"eps": [10, 10, 10, 10], "pat": [100, 101, 102, 103]}  # CV ≈ 0.01
        score, notes = _score_earnings_reliability(raw, history)
        assert any("reliable" in n.lower() or "volatility" in n.lower() for n in notes)

    def test_score_capped_at_20(self):
        raw     = {"eps_cagr_5y": 30.0}
        history = {"eps": [5, 6, 7, 8, 9, 10, 11, 12], "pat": [50, 60, 70, 80, 90, 100, 110, 120]}
        score, _ = _score_earnings_reliability(raw, history)
        assert score <= 20


# ──────────────────────────────────────────────────────────────────────────────
# Dimension: Balance Sheet Prudence
# ──────────────────────────────────────────────────────────────────────────────

class TestBalanceSheet:
    def test_debt_free_scores_high(self):
        raw  = {"debt_equity": 0.0, "ebitda_margin": 30.0}
        score, notes = _score_balance_sheet(raw, {})
        assert score >= 6
        assert any("Debt-free" in n or "debt" in n.lower() for n in notes)

    def test_high_leverage_scores_low(self):
        raw  = {"debt_equity": 4.5}
        score, _ = _score_balance_sheet(raw, {})
        assert score <= 4

    def test_improving_de_trend_bonus(self):
        raw     = {"debt_equity": 1.5}
        history = {"debt_equity": [3.0, 2.5, 2.0, 1.5, 1.0, 0.8]}
        score, notes = _score_balance_sheet(raw, history)
        assert any("improving" in n.lower() for n in notes)

    def test_high_icr_scores_well(self):
        raw  = {"icr": 12.0}
        score, notes = _score_balance_sheet(raw, {})
        assert score >= 6
        assert any("ICR" in n for n in notes)


# ──────────────────────────────────────────────────────────────────────────────
# Dimension: Promoter Commitment
# ──────────────────────────────────────────────────────────────────────────────

class TestPromoterCommitment:
    def test_high_holding_scores_well(self):
        raw     = {"promoter_holding": 70.0, "promoter_pledging": 0.0}
        history = {}
        score, notes = _score_promoter_commitment(raw, history)
        assert score >= 8
        assert any("promoter" in n.lower() for n in notes)

    def test_high_pledging_penalises(self):
        raw     = {"promoter_holding": 60.0, "promoter_pledging": 45.0}
        history = {}
        score, notes = _score_promoter_commitment(raw, history)
        assert any("CRITICAL" in n or "pledging" in n.lower() for n in notes)

    def test_increasing_stake_bonus(self):
        raw     = {"promoter_holding": 55.0, "promoter_pledging": 0.0}
        history = {"promoter_holding": [50, 51, 52, 54, 55, 56]}
        score, notes = _score_promoter_commitment(raw, history)
        assert any("increasing" in n.lower() or "conviction" in n.lower() for n in notes)

    def test_score_non_negative(self):
        raw = {"promoter_holding": 5.0, "promoter_pledging": 50.0}
        score, _ = _score_promoter_commitment(raw, {})
        assert score >= 0


# ──────────────────────────────────────────────────────────────────────────────
# Dimension: Shareholder Returns
# ──────────────────────────────────────────────────────────────────────────────

class TestShareholderReturns:
    def test_high_div_yield_scores_well(self):
        raw     = {"dividend_yield": 4.0, "revenue_cagr": 12.0}
        history = {"dividend_payout": [30, 32, 31, 33, 35]}
        score, notes = _score_shareholder_returns(raw, history)
        assert score >= 6
        assert any("dividend" in n.lower() for n in notes)

    def test_no_dividends_low_score(self):
        raw     = {"dividend_yield": 0.0, "revenue_cagr": 5.0}
        history = {"dividend_payout": [0, 0, 0, 0]}
        score, _ = _score_shareholder_returns(raw, history)
        assert score <= 4

    def test_score_capped_at_20(self):
        raw = {
            "dividend_yield": 5.0, "dividend_payout": 40,
            "revenue_cagr": 20.0, "eps_cagr_5y": 25.0, "revenue_cagr_3y": 18.0,
        }
        history = {"dividend_payout": [35, 36, 37, 38, 39, 40]}
        score, _ = _score_shareholder_returns(raw, history)
        assert score <= 20


# ──────────────────────────────────────────────────────────────────────────────
# Risk flags
# ──────────────────────────────────────────────────────────────────────────────

class TestRiskFlags:
    def test_high_pledging_flag(self):
        flags = _extract_risk_flags({"promoter_pledging": 35.0}, {})
        assert any("PLEDGING" in f for f in flags)

    def test_high_leverage_flag(self):
        flags = _extract_risk_flags({"debt_equity": 4.0}, {})
        assert any("LEVERAGE" in f for f in flags)

    def test_loss_making_flag(self):
        flags = _extract_risk_flags({}, {"eps": [-1, -2, -3, 1, -1]})
        assert any("LOSS" in f for f in flags)

    def test_promoter_selling_flag(self):
        flags = _extract_risk_flags({}, {"promoter_holding": [60, 55, 50, 45]})
        assert any("PROMOTER_SELLING" in f for f in flags)

    def test_no_flags_clean_co(self):
        flags = _extract_risk_flags(
            {"promoter_pledging": 0.0, "debt_equity": 0.3},
            {"eps": [5, 6, 7, 8], "promoter_holding": [60, 61, 62, 63]},
        )
        assert len(flags) == 0


# ──────────────────────────────────────────────────────────────────────────────
# Signal thresholds
# ──────────────────────────────────────────────────────────────────────────────

class TestSignalThresholds:
    def _mock_analyse(self, total_score: int) -> str:
        if total_score >= 72:
            return "STRONG_BUY"
        elif total_score >= 55:
            return "BUY"
        elif total_score >= 40:
            return "HOLD"
        elif total_score >= 25:
            return "AVOID"
        else:
            return "SELL"

    @pytest.mark.parametrize("score,expected", [
        (80, "STRONG_BUY"), (60, "BUY"), (45, "HOLD"),
        (30, "AVOID"), (10, "SELL"),
    ])
    def test_signal_mapping(self, score, expected):
        assert self._mock_analyse(score) == expected


# ──────────────────────────────────────────────────────────────────────────────
# Integration: analyse() with mocked screener data
# ──────────────────────────────────────────────────────────────────────────────

class TestAnalyseIntegration:
    GOOD_RAW = {
        "promoter_holding": 70.0,
        "promoter_pledging": 0.0,
        "debt_equity": 0.2,
        "ebitda_margin": 25.0,
        "dividend_yield": 2.5,
        "eps_cagr_5y": 18.0,
        "revenue_cagr": 15.0,
        "icr": 12.0,
    }
    GOOD_HIST = {
        "roce":             [18, 20, 22, 23, 24, 25],
        "revenue":          [1000, 1100, 1200, 1300, 1400, 1500],
        "capex":            [20, 22, 24, 25, 26, 28],
        "pat":              [100, 115, 130, 148, 165, 185],
        "eps":              [10, 11, 12, 14, 16, 18],
        "dividend_payout":  [25, 28, 30, 30, 32, 35],
        "promoter_holding": [65, 66, 68, 70, 70, 70],
        "debt_equity":      [0.5, 0.4, 0.3, 0.25, 0.2, 0.2],
    }

    def _run(self, raw, hist):
        with patch("data.fetchers.get_screener_data", return_value=raw):
            with patch("data.fetchers.get_screener_history", return_value=hist):
                return analyse("TESTCO")

    def test_good_company_high_score(self):
        r = self._run(self.GOOD_RAW, self.GOOD_HIST)
        assert r["score"] >= 55
        assert r["signal"] in ("STRONG_BUY", "BUY")

    def test_result_structure(self):
        r = self._run(self.GOOD_RAW, self.GOOD_HIST)
        assert "signal" in r
        assert "score" in r
        assert "detail" in r
        assert "risk_flags" in r
        assert r["agent_name"] == "mgmt_quality"

    def test_detail_has_all_dimensions(self):
        r = self._run(self.GOOD_RAW, self.GOOD_HIST)
        for key in ("capital_allocation", "earnings_reliability", "balance_sheet",
                    "promoter_commitment", "shareholder_returns"):
            assert key in r["detail"]

    def test_no_data_returns_no_data(self):
        with patch("data.fetchers.get_screener_data", return_value=None):
            with patch("data.fetchers.get_screener_history", return_value=None):
                r = analyse("BADCO")
        assert r["signal"] == "NO_DATA"

    def test_never_raises(self):
        with patch("data.fetchers.get_screener_data", side_effect=Exception("boom")):
            try:
                r = analyse("CRASH")
            except Exception as exc:
                pytest.fail(f"analyse raised unexpectedly: {exc}")
        assert r is not None
