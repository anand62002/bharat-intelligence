"""
tests/test_regime_detector.py
Unit tests for agents/regime_detector.py

Run:
    python -m pytest tests/test_regime_detector.py -v
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import numpy as np
import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.regime_detector import (
    _classify_regime,
    _nifty_trend,
    _vix_state,
    _momentum_state,
    _breadth_state,
    apply_regime_multipliers,
    REGIME_WEIGHT_MULTIPLIERS,
)


# =============================================================================
# Helper — build a mock OHLCV DataFrame
# =============================================================================

def _make_df(prices: list[float]) -> pd.DataFrame:
    import pandas as pd
    n = len(prices)
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({"Close": prices, "Open": prices, "High": prices, "Low": prices, "Volume": [1_000_000] * n}, index=idx)


def _trending_up(n=220) -> pd.DataFrame:
    """Simulates a steadily rising price series — guarantees UPTREND."""
    prices = [100.0 + i * 0.5 for i in range(n)]
    return _make_df(prices)


def _trending_down(n=220) -> pd.DataFrame:
    """Simulates a steadily falling price series — guarantees DOWNTREND."""
    prices = [200.0 - i * 0.5 for i in range(n)]
    return _make_df(prices)


def _flat(n=220, base=150.0) -> pd.DataFrame:
    """Flat price series with small noise — tends toward SIDEWAYS."""
    import random
    prices = [base + random.uniform(-2, 2) for _ in range(n)]
    return _make_df(prices)


# =============================================================================
# Tests — NIFTY trend
# =============================================================================

class TestNiftyTrend:
    def test_uptrend_detected(self):
        df     = _trending_up(220)
        trend, raw = _nifty_trend(df)
        assert trend == "UPTREND"
        assert "ema50" in raw and "ema200" in raw

    def test_downtrend_detected(self):
        df     = _trending_down(220)
        trend, _ = _nifty_trend(df)
        assert trend == "DOWNTREND"

    def test_insufficient_history_returns_sideways(self):
        df = _trending_up(50)  # only 50 bars — need 200
        trend, raw = _nifty_trend(df)
        assert trend == "SIDEWAYS"
        assert "error" in raw

    def test_none_returns_sideways(self):
        trend, _ = _nifty_trend(None)
        assert trend == "SIDEWAYS"


# =============================================================================
# Tests — VIX state
# =============================================================================

class TestVixState:
    def test_calm(self):
        df = _make_df([10.0] * 5)
        state, raw = _vix_state(df)
        assert state == "CALM"

    def test_normal(self):
        df = _make_df([16.0] * 5)
        state, _ = _vix_state(df)
        assert state == "NORMAL"

    def test_elevated(self):
        df = _make_df([22.0] * 5)
        state, _ = _vix_state(df)
        assert state == "ELEVATED"

    def test_stressed(self):
        df = _make_df([30.0] * 5)
        state, _ = _vix_state(df)
        assert state == "STRESSED"

    def test_none_returns_normal(self):
        state, raw = _vix_state(None)
        assert state == "NORMAL"
        assert "error" in raw


# =============================================================================
# Tests — Momentum (RSI)
# =============================================================================

class TestMomentumState:
    def test_overbought_when_rsi_high(self):
        # Monotonically rising series → RSI will be very high
        prices = [100 + i for i in range(50)]
        df     = _make_df(prices)
        state, raw = _momentum_state(df)
        assert state == "OVERBOUGHT"

    def test_oversold_when_rsi_low(self):
        # Monotonically falling series → RSI will be very low
        prices = [200 - i for i in range(50)]
        df     = _make_df(prices)
        state, raw = _momentum_state(df)
        assert state == "OVERSOLD"

    def test_neutral_flat(self):
        prices = [100.0] * 30
        df     = _make_df(prices)
        state, raw = _momentum_state(df)
        # flat series → RSI is undefined (NaN), fallback to NEUTRAL
        assert state == "NEUTRAL"

    def test_none_returns_neutral(self):
        state, _ = _momentum_state(None)
        assert state == "NEUTRAL"


# =============================================================================
# Tests — Composite regime classification
# =============================================================================

class TestClassifyRegime:
    def test_bull_conditions(self):
        regime, conf = _classify_regime(
            nifty_trend="UPTREND",
            vix_state="CALM",
            fii_trend="NET_BUYER",
            breadth_state="BROAD_ADVANCE",
            momentum_state="NEUTRAL",
        )
        assert regime == "BULL"
        assert conf == 100   # all 5 indicators agree

    def test_bull_with_neutral_fii(self):
        regime, _ = _classify_regime(
            nifty_trend="UPTREND",
            vix_state="NORMAL",
            fii_trend="NEUTRAL",
            breadth_state="MIXED",
            momentum_state="OVERBOUGHT",
        )
        assert regime == "BULL"

    def test_bear_conditions(self):
        # BEAR: DOWNTREND + ELEVATED VIX + FII NEUTRAL (not all-3 → doesn't fire HIGH_VOL rule)
        # The HIGH_VOLATILITY triple-condition requires ELEVATED + NET_SELLER + DOWNTREND together.
        # Using NET_SELLER alone (without ELEVATED VIX for the triple rule) → BEAR.
        # Or: DOWNTREND + ELEVATED + NEUTRAL_FII → BEAR (stressed market, FII not selling yet)
        regime, conf = _classify_regime(
            nifty_trend="DOWNTREND",
            vix_state="ELEVATED",
            fii_trend="NEUTRAL",         # FII neutral breaks the HIGH_VOLATILITY triple-condition
            breadth_state="BROAD_DECLINE",
            momentum_state="OVERSOLD",
        )
        assert regime == "BEAR"
        assert conf >= 40

    def test_high_volatility_stressed_vix(self):
        regime, conf = _classify_regime(
            nifty_trend="DOWNTREND",
            vix_state="STRESSED",
            fii_trend="NET_SELLER",
            breadth_state="BROAD_DECLINE",
            momentum_state="OVERSOLD",
        )
        assert regime == "HIGH_VOLATILITY"
        assert conf >= 80

    def test_high_volatility_triple_condition(self):
        # VIX elevated + FII seller + downtrend → HIGH_VOLATILITY
        regime, conf = _classify_regime(
            nifty_trend="DOWNTREND",
            vix_state="ELEVATED",
            fii_trend="NET_SELLER",
            breadth_state="BROAD_DECLINE",
            momentum_state="NEUTRAL",
        )
        assert regime == "HIGH_VOLATILITY"

    def test_sideways_default(self):
        regime, _ = _classify_regime(
            nifty_trend="SIDEWAYS",
            vix_state="NORMAL",
            fii_trend="NEUTRAL",
            breadth_state="MIXED",
            momentum_state="NEUTRAL",
        )
        assert regime == "SIDEWAYS"

    def test_uptrend_with_elevated_vix_not_bull(self):
        # UPTREND + ELEVATED VIX → doesn't meet BULL criteria (VIX must be CALM/NORMAL)
        # Should fall through to SIDEWAYS
        regime, _ = _classify_regime(
            nifty_trend="UPTREND",
            vix_state="ELEVATED",
            fii_trend="NEUTRAL",
            breadth_state="MIXED",
            momentum_state="NEUTRAL",
        )
        # Not BULL (VIX too high) and not BEAR/HIGH_VOL (no seller + not DOWNTREND)
        assert regime == "SIDEWAYS"


# =============================================================================
# Tests — Regime weight multipliers
# =============================================================================

class TestApplyRegimeMultipliers:
    BASE_WEIGHTS = {
        "technical": 0.2, "fundamental": 0.2, "macro": 0.1,
        "institutional": 0.15, "sentiment": 0.1,
        "historical_rag": 0.1, "commodities": 0.15,
    }

    def test_bull_regime_boosts_technical(self):
        adjusted = apply_regime_multipliers(self.BASE_WEIGHTS, "BULL")
        assert adjusted["technical"] > adjusted["macro"]  # technical boosted, macro reduced

    def test_bear_regime_boosts_macro_and_institutional(self):
        adjusted = apply_regime_multipliers(self.BASE_WEIGHTS, "BEAR")
        assert adjusted["macro"] > self.BASE_WEIGHTS["macro"] / sum(self.BASE_WEIGHTS.values())
        assert adjusted["technical"] < adjusted["macro"]

    def test_high_volatility_boosts_macro_most(self):
        adjusted = apply_regime_multipliers(self.BASE_WEIGHTS, "HIGH_VOLATILITY")
        # macro and institutional should both be boosted in HIGH_VOLATILITY
        assert adjusted["macro"] > adjusted["technical"]

    def test_sideways_boosts_fundamental(self):
        adjusted = apply_regime_multipliers(self.BASE_WEIGHTS, "SIDEWAYS")
        # fundamental gets 1.3× in SIDEWAYS regime
        assert adjusted["fundamental"] > adjusted["technical"]

    def test_weights_sum_to_one_after_adjustment(self):
        for regime in REGIME_WEIGHT_MULTIPLIERS:
            adjusted = apply_regime_multipliers(self.BASE_WEIGHTS, regime)
            total = sum(adjusted.values())
            assert abs(total - 1.0) < 1e-4, f"{regime}: weights sum to {total}, expected ~1.0"

    def test_unknown_regime_returns_unchanged(self):
        adjusted = apply_regime_multipliers(self.BASE_WEIGHTS, "UNKNOWN_REGIME")
        # should return original weights unchanged
        assert adjusted == self.BASE_WEIGHTS

    def test_empty_weights_handled(self):
        adjusted = apply_regime_multipliers({}, "BULL")
        assert adjusted == {}
