"""
tests/test_volume_profile.py
=============================
Unit tests for volume profile functions in agents/technical.py

Tests:
  - _volume_profile: POC, VAH, VAL, above_poc
  - _volume_divergence: BEARISH_DIVERGENCE, BULLISH_DIVERGENCE, NONE
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.technical import _volume_profile, _volume_divergence


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_ohlcv(n=252, seed=42):
    rng = np.random.default_rng(seed)
    close = 1000 + np.cumsum(rng.normal(0, 5, n))
    high  = close + rng.uniform(2, 8, n)
    low   = close - rng.uniform(2, 8, n)
    vol   = rng.integers(100_000, 1_000_000, n).astype(float)
    idx   = pd.date_range("2024-01-01", periods=n, freq="B")
    return (
        pd.Series(high,  index=idx),
        pd.Series(low,   index=idx),
        pd.Series(close, index=idx),
        pd.Series(vol,   index=idx),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Volume profile
# ──────────────────────────────────────────────────────────────────────────────

class TestVolumeProfile:
    def setup_method(self):
        self.high, self.low, self.close, self.vol = _make_ohlcv()

    def test_returns_dict(self):
        r = _volume_profile(self.high, self.low, self.close, self.vol)
        assert isinstance(r, dict)

    def test_poc_within_price_range(self):
        r = _volume_profile(self.high, self.low, self.close, self.vol)
        p = (self.high + self.low + self.close) / 3
        assert r["poc"] >= float(p.min()) * 0.99
        assert r["poc"] <= float(p.max()) * 1.01

    def test_val_le_poc_le_vah(self):
        r = _volume_profile(self.high, self.low, self.close, self.vol)
        assert r["val"] <= r["poc"] <= r["vah"]

    def test_above_poc_boolean(self):
        r = _volume_profile(self.high, self.low, self.close, self.vol)
        assert isinstance(r["above_poc"], (bool, np.bool_))

    def test_current_price_matches_close(self):
        r = _volume_profile(self.high, self.low, self.close, self.vol)
        assert r["current_price"] == pytest.approx(float(self.close.iloc[-1]), rel=1e-4)

    def test_flat_price_returns_empty(self):
        """If price range is zero, return empty dict."""
        n    = 100
        flat = pd.Series([1000.0] * n)
        vol  = pd.Series([1_000.0] * n)
        r = _volume_profile(flat, flat, flat, vol)
        assert r == {}

    def test_zero_volume_returns_empty(self):
        n   = 100
        cl  = pd.Series(np.linspace(900, 1100, n))
        vol = pd.Series([0.0] * n)
        r = _volume_profile(cl, cl, cl, vol)
        assert r == {}

    def test_concentrated_volume_at_level(self):
        """Heavy volume at ₹1000 → POC should be close to 1000."""
        n = 200
        rng = np.random.default_rng(99)
        close_arr = np.full(n, 1000.0)
        # add small noise to avoid flat-price edge case
        close_arr += rng.uniform(-1, 1, n)
        high_arr  = close_arr + 2
        low_arr   = close_arr - 2
        # Concentrate 80% of volume in middle 20 bars
        vol_arr   = np.ones(n) * 10_000
        vol_arr[90:110] = 1_000_000

        h = pd.Series(high_arr)
        l = pd.Series(low_arr)
        c = pd.Series(close_arr)
        v = pd.Series(vol_arr)

        r = _volume_profile(h, l, c, v)
        # POC should be near 1000
        assert abs(r["poc"] - 1000.0) < 20.0

    def test_custom_bins(self):
        r20 = _volume_profile(self.high, self.low, self.close, self.vol, bins=20)
        r100 = _volume_profile(self.high, self.low, self.close, self.vol, bins=100)
        assert r20["poc"] is not None
        assert r100["poc"] is not None

    def test_value_area_pct_stored(self):
        r = _volume_profile(self.high, self.low, self.close, self.vol, value_area_pct=0.80)
        assert r["value_area_pct"] == 0.80


# ──────────────────────────────────────────────────────────────────────────────
# Volume divergence
# ──────────────────────────────────────────────────────────────────────────────

class TestVolumeDivergence:
    def test_bearish_divergence(self):
        """Rising price + falling volume → BEARISH_DIVERGENCE."""
        n     = 30
        close = pd.Series(np.linspace(900, 1100, n))   # rising
        vol   = pd.Series(np.linspace(1_000_000, 200_000, n))  # falling
        r = _volume_divergence(close, vol)
        assert r["signal"] == "BEARISH_DIVERGENCE"

    def test_bullish_divergence(self):
        """Falling price + falling volume → BULLISH_DIVERGENCE (selling exhaustion)."""
        n     = 30
        close = pd.Series(np.linspace(1100, 900, n))    # falling
        vol   = pd.Series(np.linspace(1_000_000, 200_000, n))  # falling
        r = _volume_divergence(close, vol)
        assert r["signal"] == "BULLISH_DIVERGENCE"

    def test_no_divergence_flat(self):
        """Flat price + flat volume → NONE."""
        n     = 30
        close = pd.Series([1000.0] * n)
        vol   = pd.Series([500_000.0] * n)
        r = _volume_divergence(close, vol)
        assert r["signal"] == "NONE"

    def test_returns_trends(self):
        n     = 30
        close = pd.Series(np.linspace(900, 1100, n))
        vol   = pd.Series(np.linspace(1_000_000, 200_000, n))
        r = _volume_divergence(close, vol)
        assert "price_trend" in r
        assert "vol_trend" in r

    def test_price_trend_sign(self):
        """Rising price → positive price_trend slope."""
        n     = 30
        close = pd.Series(np.linspace(900, 1100, n))
        vol   = pd.Series([500_000.0] * n)
        r = _volume_divergence(close, vol)
        assert r["price_trend"] > 0

    def test_insufficient_data(self):
        """Fewer bars than lookback → NONE."""
        r = _volume_divergence(
            pd.Series([1000.0] * 5),
            pd.Series([100_000.0] * 5),
            lookback=20,
        )
        assert r["signal"] == "NONE"

    def test_result_keys(self):
        n     = 30
        close = pd.Series(np.linspace(900, 1100, n))
        vol   = pd.Series([500_000.0] * n)
        r = _volume_divergence(close, vol)
        assert {"signal", "price_trend", "vol_trend"} <= set(r.keys())
