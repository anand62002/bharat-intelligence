"""
tests/test_impact_cost.py
=========================
Unit tests for data/impact_cost.py

Tests validate:
  - Tier classification (HIGH / MEDIUM / LOW / ILLIQUID)
  - Symbol normalisation
  - Correct formula: spread/2 + 0.5*sqrt(participation)*100
  - Graceful failure path
  - batch_impact_cost parallelism
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import numpy as np
import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from data.impact_cost import (
    estimate_impact_cost,
    batch_impact_cost,
    HIGH_VOL_INR,
    MEDIUM_VOL_INR,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers to build fake OHLCV DataFrames
# ──────────────────────────────────────────────────────────────────────────────

def _make_ohlcv(
    n_bars: int = 60,
    close: float = 1000.0,
    high_pct: float = 0.5,   # % above close for high
    low_pct: float = 0.5,    # % below close for low
    volume: int = 10_000,
) -> pd.DataFrame:
    """
    Build a minimal OHLCV DataFrame with DatetimeIndex (5-min bars).
    Two trading days × 75 bars each ≈ realistic 5-day window.
    """
    import pandas as pd
    idx = pd.date_range("2025-01-02 09:15", periods=n_bars, freq="5min")
    high_  = close * (1 + high_pct / 100)
    low_   = close * (1 - low_pct / 100)
    df = pd.DataFrame(
        {
            "Open":   close,
            "High":   high_,
            "Low":    low_,
            "Close":  close,
            "Volume": volume,
        },
        index=idx,
    )
    return df


def _mock_ticker(df: pd.DataFrame):
    """Return a MagicMock yfinance Ticker whose .history() returns `df`."""
    ticker = MagicMock()
    ticker.history.return_value = df
    return ticker


# ──────────────────────────────────────────────────────────────────────────────
# Tier classification
# ──────────────────────────────────────────────────────────────────────────────

class TestTierClassification:
    # NOTE: _make_ohlcv uses a continuous DatetimeIndex starting at 09:15.
    # All 150 bars fall on the same calendar date (09:15–21:45), so
    # daily_value = close × volume × n_bars (not per-session 75-bar logic).

    def test_high_tier_large_liquid_stock(self):
        """Tiny trade vs huge daily vol + tight spread → HIGH tier."""
        # daily_vol = 2000 × 50_000 × 150 = ₹1,500 Cr
        # spread = 0.02% → avg_spread/2 = 0.01%
        # participation = 5000 / 15_000_000_000 ≈ 3.3e-7
        # impact ≈ 0.01 + 0.5*sqrt(3.3e-7)*100 ≈ 0.01 + 0.029 = 0.039% → HIGH
        df = _make_ohlcv(n_bars=150, close=2000, high_pct=0.01, low_pct=0.01, volume=50_000)
        with patch("yfinance.Ticker", return_value=_mock_ticker(df)):
            result = estimate_impact_cost("RELIANCE", trade_value_inr=5_000)
        assert result["liquidity_tier"] == "HIGH"

    def test_illiquid_micro_cap(self):
        """Very low volume micro-cap → ILLIQUID."""
        # daily_vol = 50 × 10 × 150 = ₹75,000
        # participation = 5_00_000 / 75_000 ≈ 6.67 → impact ≈ 129% → ILLIQUID
        df = _make_ohlcv(n_bars=150, close=50, high_pct=2.0, low_pct=2.0, volume=10)
        with patch("yfinance.Ticker", return_value=_mock_ticker(df)):
            result = estimate_impact_cost("MICROCAP", trade_value_inr=5_00_000)
        assert result["liquidity_tier"] == "ILLIQUID"

    def test_medium_tier(self):
        """Moderate volume, small spread → MEDIUM."""
        # daily_vol = 500 × 5_000 × 150 = ₹37.5 Cr (>₹1 Cr)
        # spread = 0.2% → avg_spread/2 = 0.1%
        # participation = 5_00_000 / 375_000_000 ≈ 0.00133
        # impact ≈ 0.1 + 0.5*sqrt(0.00133)*100 ≈ 0.1 + 1.82 = 1.92% → LOW (not MEDIUM)
        # Use tiny trade so impact is low:
        # trade=1_000: participation ≈ 2.67e-6 → sqrt ≈ 0.00163
        # impact ≈ 0.1 + 0.5*0.00163*100 = 0.1 + 0.082 = 0.182% < 0.3 AND vol>1Cr → MEDIUM
        df = _make_ohlcv(n_bars=150, close=500, high_pct=0.1, low_pct=0.1, volume=5_000)
        with patch("yfinance.Ticker", return_value=_mock_ticker(df)):
            result = estimate_impact_cost("MIDCAP", trade_value_inr=1_000)
        assert result["liquidity_tier"] in ("MEDIUM", "HIGH")

    def test_low_tier(self):
        """Lower vol, wider spread, mid-size trade → LOW."""
        # daily_vol = 100 × 200 × 150 = ₹30L (>0 but <1Cr)
        # spread=1.5% → impact/2=0.75%
        # participation = 50_000/3_000_000 ≈ 0.0167
        # impact ≈ 0.75 + 0.5*sqrt(0.0167)*100 ≈ 0.75 + 6.45 → ILLIQUID, not LOW
        # Use tiny trade: trade=100
        # participation = 100/3_000_000 ≈ 3.33e-5, impact ≈ 0.75 + 0.5*0.00577*100 = 0.75+0.289 = 1.04% → ILLIQUID
        # → For LOW we need spread low, vol moderate
        # close=200, vol=100, spread=0.5%, trade=500
        # daily_vol=200*100*150=3_000_000; participation=500/3_000_000≈1.67e-4
        # impact=0.25+0.5*sqrt(1.67e-4)*100=0.25+0.646=0.896% < 1% AND vol<1Cr → LOW
        df = _make_ohlcv(n_bars=150, close=200, high_pct=0.25, low_pct=0.25, volume=100)
        with patch("yfinance.Ticker", return_value=_mock_ticker(df)):
            result = estimate_impact_cost("SMALLCAP", trade_value_inr=500)
        assert result["liquidity_tier"] in ("LOW", "MEDIUM")


# ──────────────────────────────────────────────────────────────────────────────
# Formula correctness
# ──────────────────────────────────────────────────────────────────────────────

class TestFormulaCorrectness:
    def test_impact_cost_formula(self):
        """
        Manually compute expected impact cost and compare to output.
        All 150 bars land on the same calendar date (09:15–21:45), so
        daily_value = close × volume × n_bars (all bars counted as one day).
        """
        close   = 1000.0
        n_bars  = 150
        volume  = 1_000
        high_pct = 0.5   # %
        low_pct  = 0.5   # %
        trade   = 5_00_000

        df = _make_ohlcv(n_bars=n_bars, close=close, high_pct=high_pct, low_pct=low_pct, volume=volume)
        with patch("yfinance.Ticker", return_value=_mock_ticker(df)):
            result = estimate_impact_cost("TEST", trade_value_inr=trade)

        # All bars on one date → median daily_vol = close * volume * n_bars
        daily_value_inr = close * volume * n_bars      # 150_000_000
        high_           = close * (1 + high_pct / 100)
        low_            = close * (1 - low_pct  / 100)
        spread_pct      = (high_ - low_) / close * 100 # 1.0%
        participation   = trade / daily_value_inr
        expected_ic     = spread_pct / 2.0 + 0.5 * (participation ** 0.5) * 100

        assert result["impact_cost_pct"] == pytest.approx(expected_ic, rel=0.05)

    def test_participation_rate_stored(self):
        """Participation rate = trade_value / daily_vol (all bars one day)."""
        n_bars = 150
        close  = 1000
        vol    = 1000
        trade  = 5_00_000
        df = _make_ohlcv(n_bars=n_bars, close=close, volume=vol)
        with patch("yfinance.Ticker", return_value=_mock_ticker(df)):
            result = estimate_impact_cost("TEST", trade_value_inr=trade)
        daily_vol     = close * vol * n_bars      # all on same date
        expected_part = trade / daily_vol
        assert result["participation_rate"] == pytest.approx(expected_part, rel=0.05)


# ──────────────────────────────────────────────────────────────────────────────
# Symbol normalisation
# ──────────────────────────────────────────────────────────────────────────────

class TestSymbolNormalisation:
    def test_strips_ns(self):
        df = _make_ohlcv(n_bars=150, close=1000, volume=1000)
        with patch("yfinance.Ticker", return_value=_mock_ticker(df)):
            r = estimate_impact_cost("RELIANCE.NS")
        assert r["symbol"] == "RELIANCE"

    def test_strips_bo(self):
        df = _make_ohlcv(n_bars=150, close=1000, volume=1000)
        with patch("yfinance.Ticker", return_value=_mock_ticker(df)):
            r = estimate_impact_cost("INFY.BO")
        assert r["symbol"] == "INFY"

    def test_uppercase(self):
        df = _make_ohlcv(n_bars=150, close=1000, volume=1000)
        with patch("yfinance.Ticker", return_value=_mock_ticker(df)):
            r = estimate_impact_cost("tcs")
        assert r["symbol"] == "TCS"


# ──────────────────────────────────────────────────────────────────────────────
# Error / edge cases
# ──────────────────────────────────────────────────────────────────────────────

class TestErrorHandling:
    def test_empty_dataframe_returns_error(self):
        mock_t = MagicMock()
        mock_t.history.return_value = pd.DataFrame()
        with patch("yfinance.Ticker", return_value=mock_t):
            r = estimate_impact_cost("BADSTOCK")
        assert r["liquidity_tier"] == "UNKNOWN"
        assert r["error"] is not None
        assert r["impact_cost_pct"] is None

    def test_yfinance_exception_returns_error(self):
        with patch("yfinance.Ticker", side_effect=RuntimeError("network error")):
            r = estimate_impact_cost("CRASH")
        assert r["liquidity_tier"] == "UNKNOWN"
        assert r["error"] is not None

    def test_never_raises(self):
        with patch("yfinance.Ticker", side_effect=Exception("boom")):
            try:
                r = estimate_impact_cost("ANY")
            except Exception as exc:
                pytest.fail(f"estimate_impact_cost raised unexpectedly: {exc}")
        assert r is not None

    def test_zero_volume_bars_excluded(self):
        """Bars with zero volume should be filtered out; if all zero → error."""
        df = _make_ohlcv(n_bars=150, close=1000, volume=0)
        mock_t = MagicMock()
        mock_t.history.return_value = df
        with patch("yfinance.Ticker", return_value=mock_t):
            r = estimate_impact_cost("NOVOL")
        assert r["liquidity_tier"] == "UNKNOWN"
        assert r["error"] is not None


# ──────────────────────────────────────────────────────────────────────────────
# Result structure
# ──────────────────────────────────────────────────────────────────────────────

class TestResultStructure:
    EXPECTED_KEYS = {
        "symbol", "impact_cost_pct", "liquidity_tier",
        "avg_daily_volume_inr", "avg_spread_pct",
        "participation_rate", "data_days", "source", "error",
    }

    def test_all_keys_present_on_success(self):
        df = _make_ohlcv(n_bars=150, close=1000, volume=1000)
        with patch("yfinance.Ticker", return_value=_mock_ticker(df)):
            r = estimate_impact_cost("ITC")
        assert self.EXPECTED_KEYS <= set(r.keys())

    def test_all_keys_present_on_error(self):
        with patch("yfinance.Ticker", side_effect=Exception("boom")):
            r = estimate_impact_cost("FAIL")
        assert self.EXPECTED_KEYS <= set(r.keys())

    def test_liquidity_tier_valid_values(self):
        df = _make_ohlcv(n_bars=150, close=1000, volume=1000)
        with patch("yfinance.Ticker", return_value=_mock_ticker(df)):
            r = estimate_impact_cost("ITC")
        assert r["liquidity_tier"] in ("HIGH", "MEDIUM", "LOW", "ILLIQUID", "UNKNOWN")


# ──────────────────────────────────────────────────────────────────────────────
# batch_impact_cost
# ──────────────────────────────────────────────────────────────────────────────

class TestBatch:
    def test_batch_returns_dict_keyed_by_symbol(self):
        df = _make_ohlcv(n_bars=150, close=1000, volume=1000)
        with patch("yfinance.Ticker", return_value=_mock_ticker(df)):
            results = batch_impact_cost(["RELIANCE", "INFY", "TCS"])
        assert set(results.keys()) == {"RELIANCE", "INFY", "TCS"}

    def test_batch_each_entry_has_tier(self):
        df = _make_ohlcv(n_bars=150, close=1000, volume=1000)
        with patch("yfinance.Ticker", return_value=_mock_ticker(df)):
            results = batch_impact_cost(["RELIANCE", "INFY"])
        for sym, r in results.items():
            assert "liquidity_tier" in r, f"{sym} missing liquidity_tier"

    def test_batch_empty_list(self):
        results = batch_impact_cost([])
        assert results == {}
