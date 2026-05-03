"""
tests/test_valuation_scenarios.py
===================================
Unit tests for agents/valuation_scenarios.py

Tests cover:
  - Core DCF engine (_dcf) with known numerical inputs
  - Scenario runner (_run_scenario) for BULL / BASE / BEAR
  - Tornado building (_build_tornado)
  - Recommendation classification
  - Integration run_scenarios() with mocked screener data
  - Edge cases: zero earnings, negative FCF, tiny shares, price extremes
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.valuation_scenarios import (
    run_scenarios,
    _dcf,
    _run_scenario,
    _build_tornado,
    _recommendation,
    AGENT_NAME,
)


# ──────────────────────────────────────────────────────────────────────────────
# _dcf — core engine
# ──────────────────────────────────────────────────────────────────────────────

class TestDCF:
    # Reference: OE = 1000 Cr, growth = 15%, wacc = 12%, terminal = 7%, shares = 100 Cr
    OE    = 1000.0
    G     = 0.15
    WACC  = 0.12
    T     = 0.07
    SH    = 100.0

    def test_returns_positive_for_positive_inputs(self):
        iv = _dcf(self.OE, self.G, self.WACC, self.T, self.SH)
        assert iv is not None and iv > 0

    def test_higher_growth_gives_higher_value(self):
        iv_high = _dcf(self.OE, 0.20, self.WACC, self.T, self.SH)
        iv_low  = _dcf(self.OE, 0.08, self.WACC, self.T, self.SH)
        assert iv_high > iv_low

    def test_higher_wacc_gives_lower_value(self):
        iv_low_wacc  = _dcf(self.OE, self.G, 0.10, self.T, self.SH)
        iv_high_wacc = _dcf(self.OE, self.G, 0.15, self.T, self.SH)
        assert iv_low_wacc > iv_high_wacc

    def test_higher_terminal_growth_gives_higher_value(self):
        iv_high_t = _dcf(self.OE, self.G, self.WACC, 0.08, self.SH)
        iv_low_t  = _dcf(self.OE, self.G, self.WACC, 0.05, self.SH)
        assert iv_high_t > iv_low_t

    def test_zero_owner_earnings_returns_none(self):
        assert _dcf(0.0, self.G, self.WACC, self.T, self.SH) is None

    def test_negative_owner_earnings_returns_none(self):
        assert _dcf(-500.0, self.G, self.WACC, self.T, self.SH) is None

    def test_zero_shares_returns_none(self):
        assert _dcf(self.OE, self.G, self.WACC, self.T, 0.0) is None

    def test_wacc_less_than_terminal_returns_none(self):
        # Invalid: Gordon Growth requires wacc > terminal
        assert _dcf(self.OE, self.G, 0.06, 0.07, self.SH) is None

    def test_very_high_growth_capped(self):
        # Growth of 50% should be capped at _MAX_GROWTH (30%)
        iv_capped   = _dcf(self.OE, 0.50, self.WACC, self.T, self.SH)
        iv_at_max   = _dcf(self.OE, 0.30, self.WACC, self.T, self.SH)
        assert iv_capped == pytest.approx(iv_at_max, rel=0.01)

    def test_larger_shares_gives_lower_per_share_value(self):
        iv_few  = _dcf(self.OE, self.G, self.WACC, self.T, 50.0)
        iv_many = _dcf(self.OE, self.G, self.WACC, self.T, 200.0)
        assert iv_few > iv_many


# ──────────────────────────────────────────────────────────────────────────────
# _run_scenario
# ──────────────────────────────────────────────────────────────────────────────

class TestRunScenario:
    PARAMS = dict(
        owner_earnings_cr=500.0,
        base_growth=0.12,
        base_wacc=0.12,
        base_terminal=0.07,
        shares_cr=50.0,
        current_price=800.0,
    )

    def test_bull_intrinsic_value_above_base(self):
        bull = _run_scenario("BULL", **self.PARAMS)
        base = _run_scenario("BASE", **self.PARAMS)
        assert bull["intrinsic_value"] > base["intrinsic_value"]

    def test_base_intrinsic_value_above_bear(self):
        base = _run_scenario("BASE", **self.PARAMS)
        bear = _run_scenario("BEAR", **self.PARAMS)
        assert base["intrinsic_value"] > bear["intrinsic_value"]

    def test_result_has_required_keys(self):
        r = _run_scenario("BASE", **self.PARAMS)
        for k in ("scenario", "intrinsic_value", "margin_of_safety_pct",
                  "upside_pct", "growth_rate", "wacc", "terminal_growth"):
            assert k in r

    def test_growth_rate_is_pct_not_decimal(self):
        r = _run_scenario("BASE", **self.PARAMS)
        assert 1 < r["growth_rate"] < 50   # should be ~12, not 0.12

    def test_wacc_is_pct_not_decimal(self):
        r = _run_scenario("BASE", **self.PARAMS)
        assert 5 < r["wacc"] < 30

    def test_positive_mos_when_cheap(self):
        # OE=1000 Cr, shares=1 Cr → OE per share = 1000. With growth+terminal, IV >> 800
        r = _run_scenario("BASE",
                          owner_earnings_cr=1000.0, base_growth=0.15, base_wacc=0.12,
                          base_terminal=0.07, shares_cr=1.0, current_price=800.0)
        assert r["margin_of_safety_pct"] > 0

    def test_negative_mos_when_expensive(self):
        # Very low OE relative to price: OE=5 Cr, shares=100 Cr → OE/share = 0.05 ₹
        r = _run_scenario("BASE",
                          owner_earnings_cr=5.0, base_growth=0.05, base_wacc=0.12,
                          base_terminal=0.05, shares_cr=100.0, current_price=5000.0)
        assert r["margin_of_safety_pct"] < 0


# ──────────────────────────────────────────────────────────────────────────────
# _build_tornado
# ──────────────────────────────────────────────────────────────────────────────

class TestBuildTornado:
    def _tornado(self):
        return _build_tornado(
            owner_earnings_cr=500.0,
            base_growth=0.12,
            base_wacc=0.12,
            base_terminal=0.07,
            shares_cr=50.0,
            current_price=800.0,
        )

    def test_returns_list_of_four(self):
        t = self._tornado()
        assert len(t) == 4

    def test_sorted_by_impact_descending(self):
        t = self._tornado()
        impacts = [r["impact"] for r in t]
        assert impacts == sorted(impacts, reverse=True)

    def test_each_row_has_required_keys(self):
        t = self._tornado()
        for row in t:
            for k in ("assumption", "low_iv", "high_iv", "impact", "impact_pct"):
                assert k in row

    def test_high_iv_above_low_iv(self):
        # High case should give higher IV for growth/terminal; lower IV for WACC
        t = self._tornado()
        for row in t:
            # high_iv should always be the bullish direction
            assert row["high_iv"] >= row["low_iv"]

    def test_wacc_has_nonzero_impact(self):
        t = self._tornado()
        wacc_row = next(r for r in t if r["assumption"] == "WACC")
        assert wacc_row["impact"] > 0


# ──────────────────────────────────────────────────────────────────────────────
# _recommendation
# ──────────────────────────────────────────────────────────────────────────────

class TestRecommendation:
    @pytest.mark.parametrize("base_mos,bear_mos,expected", [
        (50, 20,  "STRONG_BUY"),
        (30, 5,   "BUY"),
        (10, -5,  "HOLD"),
        (-10, -20, "AVOID"),
        (-30, -40, "SELL"),
        (None, None, "INSUFFICIENT_DATA"),
    ])
    def test_thresholds(self, base_mos, bear_mos, expected):
        assert _recommendation(base_mos, bear_mos) == expected


# ──────────────────────────────────────────────────────────────────────────────
# Integration: run_scenarios()
# ──────────────────────────────────────────────────────────────────────────────

_GOOD_RAW = {
    "eps_cagr_5y": 18.0,
    "current_price": 1500.0,
    "market_cap": 150000.0,   # Cr
}
_GOOD_HIST = {
    "revenue":      [1000, 1200, 1400, 1600, 1800, 2000],
    "pat":          [100, 120, 140, 160, 180, 200],
    "depreciation": [50, 55, 60, 65, 70, 75],
    "capex":        [80, 90, 100, 110, 120, 130],
    "eps":          [10, 12, 14, 16, 18, 20],
}


def _mock_run(raw=None, hist=None):
    r = raw or _GOOD_RAW
    h = hist or _GOOD_HIST
    with patch("data.fetchers.get_screener_data", return_value=r):
        with patch("data.fetchers.get_screener_history", return_value=h):
            with patch("yfinance.Ticker") as mock_ticker:
                mock_ticker.return_value.fast_info.last_price = 1500.0
                return run_scenarios("TESTCO")


class TestRunScenarios:
    def test_result_structure(self):
        r = _mock_run()
        for key in ("symbol", "current_price", "scenarios", "fair_value_range",
                    "margin_of_safety", "upside_pct", "tornado",
                    "recommendation", "data_quality", "agent_name"):
            assert key in r

    def test_agent_name(self):
        r = _mock_run()
        assert r["agent_name"] == AGENT_NAME

    def test_three_scenarios_present(self):
        r = _mock_run()
        assert set(r["scenarios"].keys()) >= {"BULL", "BASE", "BEAR"}

    def test_bull_iv_above_bear_iv(self):
        r = _mock_run()
        bull_iv = r["scenarios"]["BULL"]["intrinsic_value"]
        bear_iv = r["scenarios"]["BEAR"]["intrinsic_value"]
        assert bull_iv is not None and bear_iv is not None
        assert bull_iv > bear_iv

    def test_fair_value_range_ordered(self):
        r = _mock_run()
        fv = r["fair_value_range"]
        if fv["low"] and fv["high"]:
            assert fv["low"] <= fv["mid"] <= fv["high"]

    def test_tornado_has_four_entries(self):
        r = _mock_run()
        assert len(r["tornado"]) == 4

    def test_symbol_normalised(self):
        with patch("data.fetchers.get_screener_data", return_value=_GOOD_RAW):
            with patch("data.fetchers.get_screener_history", return_value=_GOOD_HIST):
                with patch("yfinance.Ticker") as mock_ticker:
                    mock_ticker.return_value.fast_info.last_price = 1500.0
                    r = run_scenarios("TESTCO.NS")
        assert r["symbol"] == "TESTCO"

    def test_no_data_graceful(self):
        with patch("data.fetchers.get_screener_data", return_value=None):
            with patch("data.fetchers.get_screener_history", return_value=None):
                r = run_scenarios("BADCO")
        assert r is not None
        assert r["recommendation"] == "INSUFFICIENT_DATA"

    def test_never_raises_on_exception(self):
        with patch("data.fetchers.get_screener_data", side_effect=Exception("boom")):
            try:
                r = run_scenarios("CRASH")
            except Exception as exc:
                pytest.fail(f"run_scenarios raised: {exc}")
        assert r is not None

    def test_data_quality_full_with_complete_data(self):
        r = _mock_run()
        # With complete PAT + capex + dep data, should be FULL or PARTIAL
        assert r["data_quality"] in ("FULL", "PARTIAL", "ESTIMATED")

    def test_base_assumptions_present(self):
        r = _mock_run()
        if r["data_quality"] != "NO_DATA":
            assert "base_assumptions" in r
            ba = r["base_assumptions"]
            assert "owner_earnings_cr" in ba
            assert "base_growth_pct" in ba
            assert "base_wacc_pct" in ba

    def test_margin_of_safety_dict_keys(self):
        r = _mock_run()
        mos = r["margin_of_safety"]
        assert "bull" in mos and "base" in mos and "bear" in mos

    def test_upside_pct_dict_keys(self):
        r = _mock_run()
        up = r["upside_pct"]
        assert "bull" in up and "base" in up and "bear" in up

    def test_recommendation_is_valid_string(self):
        r = _mock_run()
        assert r["recommendation"] in (
            "STRONG_BUY", "BUY", "HOLD", "AVOID", "SELL", "INSUFFICIENT_DATA"
        )

    def test_cheap_stock_recommendation(self):
        # Very high OE per share → intrinsic >> market price → STRONG_BUY
        cheap_raw = {
            "eps_cagr_5y": 20.0,
            "current_price": 100.0,   # very cheap
            "market_cap": 500.0,      # 5 Cr shares at 100
        }
        cheap_hist = {
            "pat":          [100, 120, 150, 180, 200, 250],
            "depreciation": [30, 35, 40, 45, 50, 55],
            "capex":        [40, 45, 50, 55, 60, 65],
            "revenue":      [1000, 1200, 1400, 1600, 1800, 2000],
            "eps":          [20, 24, 30, 36, 40, 50],
        }
        with patch("data.fetchers.get_screener_data", return_value=cheap_raw):
            with patch("data.fetchers.get_screener_history", return_value=cheap_hist):
                with patch("yfinance.Ticker") as mock_ticker:
                    mock_ticker.return_value.fast_info.last_price = 100.0
                    r = run_scenarios("CHEAP")
        if r["data_quality"] != "NO_DATA":
            assert r["recommendation"] in ("STRONG_BUY", "BUY")
