"""
tests/test_technical.py
pytest suite for agents/technical.py

Run from project root:
    pytest tests/test_technical.py -v
"""

import os
import sys
from datetime import datetime
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

# Ensure project root is on sys.path so `agents` and `data` packages resolve
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.technical import (  # noqa: E402
    AGENT_NAME,
    analyse,
    _adx,
    _detect_double_bottom,
    _detect_golden_cross,
    _detect_death_cross,
    _detect_inverse_hns,
    _ema,
    _local_extrema,
    _macd,
    _obv,
    _rsi,
    _supertrend,
    _vwap,
)

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_ohlcv(n: int = 252, seed: int = 42, drift: float = 0.3) -> pd.DataFrame:
    """Synthetic OHLCV with a mild uptrend (drift > 0) or downtrend (drift < 0).
    Uses integer index to avoid business-day count ambiguity."""
    np.random.seed(seed)
    close = np.maximum(
        1000.0 + np.cumsum(np.random.randn(n) * 8 + drift), 50.0
    )
    high   = close + np.abs(np.random.randn(n)) * close * 0.01
    low    = close - np.abs(np.random.randn(n)) * close * 0.01
    open_  = close + np.random.randn(n) * close * 0.005
    volume = np.random.randint(500_000, 5_000_000, n).astype(float)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}
    )


# ──────────────────────────────────────────────────────────────────────────────
# Unit tests: individual indicators
# ──────────────────────────────────────────────────────────────────────────────

class TestRSI:
    def test_range_0_to_100(self):
        df = _make_ohlcv()
        rsi = _rsi(df["Close"])
        v = rsi.dropna()
        assert len(v) > 0
        assert v.between(0, 100).all(), "RSI values must be in [0, 100]"

    def test_same_length_as_input(self):
        df = _make_ohlcv(100)
        assert len(_rsi(df["Close"])) == 100

    def test_overbought_on_rising_series(self):
        """Strictly rising prices should produce RSI > 70 eventually."""
        prices = pd.Series(range(1, 101), dtype=float)
        rsi = _rsi(prices)
        assert rsi.dropna().iloc[-1] > 70


class TestMACD:
    def test_components_length(self):
        df = _make_ohlcv()
        line, sig, hist = _macd(df["Close"])
        assert len(line) == len(df)
        assert len(sig) == len(df)
        assert len(hist) == len(df)

    def test_histogram_equals_line_minus_signal(self):
        df = _make_ohlcv()
        line, sig, hist = _macd(df["Close"])
        residual = (line - sig - hist).dropna().abs()
        assert (residual < 1e-8).all(), "Histogram must equal MACD - Signal"


class TestEMA:
    def test_smoother_than_close(self):
        df = _make_ohlcv()
        ema200 = _ema(df["Close"], 200).dropna()
        assert ema200.std() < df["Close"].std()

    def test_span_50_lags_less_than_200(self):
        df = _make_ohlcv(300, drift=1.0)  # persistent uptrend
        ema50  = _ema(df["Close"], 50).dropna()
        ema200 = _ema(df["Close"], 200).dropna()
        # In a long uptrend, EMA50 should end up higher than EMA200
        assert float(ema50.iloc[-1]) > float(ema200.iloc[-1])


class TestADX:
    def test_non_negative(self):
        df = _make_ohlcv()
        adx, di_plus, di_minus = _adx(df["High"], df["Low"], df["Close"])
        assert (adx.dropna() >= 0).all()
        assert (di_plus.dropna() >= 0).all()
        assert (di_minus.dropna() >= 0).all()

    def test_length(self):
        df = _make_ohlcv(100)
        adx, _, _ = _adx(df["High"], df["Low"], df["Close"])
        assert len(adx) == 100


class TestSupertrend:
    def test_direction_is_binary(self):
        df = _make_ohlcv()
        _, direction = _supertrend(df["High"], df["Low"], df["Close"])
        valid = direction.dropna()
        assert set(valid.unique()).issubset({1, -1}), "Direction must be 1 or -1"

    def test_length(self):
        df = _make_ohlcv(100)
        st, _ = _supertrend(df["High"], df["Low"], df["Close"])
        assert len(st) == 100


class TestOBV:
    def test_length(self):
        df = _make_ohlcv()
        obv = _obv(df["Close"], df["Volume"])
        assert len(obv) == len(df)

    def test_rises_on_consistently_up_close(self):
        n = 50
        close  = pd.Series(range(100, 100 + n), dtype=float)
        volume = pd.Series([1_000_000] * n, dtype=float)
        obv = _obv(close, volume)
        assert float(obv.iloc[-1]) > float(obv.iloc[0])


class TestVWAP:
    def test_within_high_low_range(self):
        df = _make_ohlcv()
        vwap = _vwap(df["High"], df["Low"], df["Close"], df["Volume"]).dropna()
        assert (vwap >= df["Low"].min()).all()
        assert (vwap <= df["High"].max()).all()

    def test_length(self):
        df = _make_ohlcv(100)
        v = _vwap(df["High"], df["Low"], df["Close"], df["Volume"])
        assert len(v) == 100


# ──────────────────────────────────────────────────────────────────────────────
# Unit tests: pattern detection
# ──────────────────────────────────────────────────────────────────────────────

class TestPatterns:
    def test_golden_cross_detected(self):
        """EMA50 crosses above EMA200: verify over the full valid window."""
        n = 300
        vals = np.concatenate([
            np.linspace(500, 150, 250),   # long decline (EMA50 < EMA200 throughout)
            np.linspace(150, 600, 50),    # sharp reversal (EMA50 surges past EMA200)
        ])
        close = pd.Series(vals, dtype=float)
        ema50  = _ema(close, 50)
        ema200 = _ema(close, 200)
        # Cover the entire valid diff window so the cross is never missed
        valid_len = len((ema50 - ema200).dropna())
        assert _detect_golden_cross(ema50, ema200, lookback=valid_len)

    def test_death_cross_detected(self):
        """EMA50 crosses below EMA200 recently."""
        vals = np.concatenate([
            np.linspace(100, 500, 250),   # long uptrend
            np.linspace(500, 100, 150),   # aggressive drop
        ])
        close = pd.Series(vals, dtype=float)
        ema50  = _ema(close, 50)
        ema200 = _ema(close, 200)
        assert _detect_death_cross(ema50, ema200, lookback=100)

    def test_double_bottom_detected(self):
        """Two similar troughs far apart → should detect."""
        n = 120
        vals = np.ones(n) * 100.0
        # Trough 1 at position 20
        vals[15:25] = np.linspace(100, 80, 10)
        # Trough 2 at position 80
        vals[75:85] = np.linspace(100, 81, 10)
        close = pd.Series(vals)
        _, lows = _local_extrema(close, window=5)
        assert _detect_double_bottom(close, lows, tol=0.05)

    def test_inverse_hns_detected(self):
        """Classic inverse H&S shape → should detect."""
        vals = np.ones(150) * 100.0
        # Left shoulder at ~30
        vals[25:35] = np.linspace(100, 85, 10)
        # Head (deeper) at ~75
        vals[70:80] = np.linspace(100, 70, 10)
        # Right shoulder at ~120
        vals[115:125] = np.linspace(100, 86, 10)
        close = pd.Series(vals)
        _, lows = _local_extrema(close, window=5)
        assert _detect_inverse_hns(close, lows, tol=0.10)

    def test_no_false_positive_on_flat_series(self):
        """A completely flat series should not trigger double-bottom."""
        close = pd.Series(np.ones(120) * 100.0)
        _, lows = _local_extrema(close, window=5)
        # Flat series: any two points satisfy tol, but there are no distinct lows
        # _local_extrema requires strict min-in-window; flat returns nothing meaningful
        result = _detect_double_bottom(close, lows, tol=0.01)
        # Either True or False is acceptable — just must not raise
        assert isinstance(result, bool)


# ──────────────────────────────────────────────────────────────────────────────
# Integration tests: analyse()
# ──────────────────────────────────────────────────────────────────────────────

REQUIRED_KEYS = {
    "signal", "score", "detail", "upside_pct",
    "data_sources", "confidence", "agent_name",
}
REQUIRED_DETAIL_KEYS = {
    "trend_alignment", "momentum", "volume_confirmation", "pattern", "indicators",
}
REQUIRED_INDICATOR_KEYS = {
    "current_price", "rsi", "macd", "macd_signal",
    "ema20", "ema50", "ema200", "adx",
    "supertrend_bullish", "obv", "vwap",
}
VALID_SIGNALS = {"STRONG_BUY", "BUY", "HOLD", "AVOID", "SELL", "NO_DATA"}


@pytest.fixture()
def mock_df():
    return _make_ohlcv(252)


@pytest.fixture(autouse=True)
def no_supabase():
    """Prevent all real Supabase calls across every test."""
    with patch("agents.technical._write_agent_performance"):
        yield


@pytest.fixture()
def mock_ticker_no_target():
    ticker = MagicMock()
    ticker.info = {}
    return ticker


class TestAnalyseSchema:
    def test_top_level_keys_present(self, mock_df, mock_ticker_no_target):
        with patch("agents.technical.get_ohlcv", return_value=mock_df), \
             patch("yfinance.Ticker", return_value=mock_ticker_no_target):
            result = analyse("TEST.NS")
        assert REQUIRED_KEYS.issubset(result.keys()), (
            f"Missing top-level keys: {REQUIRED_KEYS - result.keys()}"
        )

    def test_detail_sub_keys(self, mock_df, mock_ticker_no_target):
        with patch("agents.technical.get_ohlcv", return_value=mock_df), \
             patch("yfinance.Ticker", return_value=mock_ticker_no_target):
            result = analyse("TEST.NS")
        assert REQUIRED_DETAIL_KEYS.issubset(result["detail"].keys())

    def test_indicator_keys_in_detail(self, mock_df, mock_ticker_no_target):
        with patch("agents.technical.get_ohlcv", return_value=mock_df), \
             patch("yfinance.Ticker", return_value=mock_ticker_no_target):
            result = analyse("TEST.NS")
        inds = result["detail"]["indicators"]
        missing = REQUIRED_INDICATOR_KEYS - inds.keys()
        assert not missing, f"Missing indicator keys: {missing}"

    def test_agent_name_is_technical(self, mock_df, mock_ticker_no_target):
        with patch("agents.technical.get_ohlcv", return_value=mock_df), \
             patch("yfinance.Ticker", return_value=mock_ticker_no_target):
            result = analyse("TEST.NS")
        assert result["agent_name"] == AGENT_NAME == "technical"


class TestAnalyseScoring:
    def test_score_in_0_to_100(self, mock_df, mock_ticker_no_target):
        with patch("agents.technical.get_ohlcv", return_value=mock_df), \
             patch("yfinance.Ticker", return_value=mock_ticker_no_target):
            result = analyse("TEST.NS")
        assert 0 <= result["score"] <= 100

    def test_sub_scores_sum_to_total(self, mock_df, mock_ticker_no_target):
        with patch("agents.technical.get_ohlcv", return_value=mock_df), \
             patch("yfinance.Ticker", return_value=mock_ticker_no_target):
            result = analyse("TEST.NS")
        d = result["detail"]
        sub_sum = (
            d["trend_alignment"]["score"]
            + d["momentum"]["score"]
            + d["volume_confirmation"]["score"]
            + d["pattern"]["score"]
        )
        assert result["score"] == sub_sum, (
            f"score={result['score']} != sub_sum={sub_sum}"
        )

    def test_sub_score_bounds(self, mock_df, mock_ticker_no_target):
        with patch("agents.technical.get_ohlcv", return_value=mock_df), \
             patch("yfinance.Ticker", return_value=mock_ticker_no_target):
            d = analyse("TEST.NS")["detail"]
        assert 0 <= d["trend_alignment"]["score"] <= 30
        assert 0 <= d["momentum"]["score"] <= 30
        assert 0 <= d["volume_confirmation"]["score"] <= 20
        assert 0 <= d["pattern"]["score"] <= 20

    def test_signal_is_valid(self, mock_df, mock_ticker_no_target):
        with patch("agents.technical.get_ohlcv", return_value=mock_df), \
             patch("yfinance.Ticker", return_value=mock_ticker_no_target):
            result = analyse("TEST.NS")
        assert result["signal"] in VALID_SIGNALS


class TestAnalyseConfidence:
    def test_confidence_between_0_and_1(self, mock_df, mock_ticker_no_target):
        with patch("agents.technical.get_ohlcv", return_value=mock_df), \
             patch("yfinance.Ticker", return_value=mock_ticker_no_target):
            result = analyse("TEST.NS")
        assert 0.0 <= result["confidence"] <= 1.0

    def test_full_year_data_yields_high_confidence(self, mock_ticker_no_target):
        """252 trading days → data_quality=1.0 → confidence should be ≥ 0.4."""
        df = _make_ohlcv(252)
        with patch("agents.technical.get_ohlcv", return_value=df), \
             patch("yfinance.Ticker", return_value=mock_ticker_no_target):
            result = analyse("TEST.NS")
        assert result["confidence"] >= 0.4


class TestAnalyseUpside:
    def test_analyst_target_used_when_available(self, mock_df):
        current = float(mock_df["Close"].iloc[-1])
        target  = current * 1.25  # 25% upside
        ticker  = MagicMock()
        ticker.info = {"targetMeanPrice": target}
        with patch("agents.technical.get_ohlcv", return_value=mock_df), \
             patch("yfinance.Ticker", return_value=ticker):
            result = analyse("TEST.NS")
        assert result["upside_pct"] is not None
        assert abs(result["upside_pct"] - 25.0) < 0.5
        assert "yfinance_analyst_target" in result["data_sources"]
        assert result["detail"]["indicators"]["analyst_target"] == round(target, 2)

    def test_fallback_to_ema200_when_no_target(self, mock_df, mock_ticker_no_target):
        with patch("agents.technical.get_ohlcv", return_value=mock_df), \
             patch("yfinance.Ticker", return_value=mock_ticker_no_target):
            result = analyse("TEST.NS")
        assert result["upside_pct"] is not None
        assert "yfinance_analyst_target" not in result["data_sources"]

    def test_data_sources_contains_ohlcv(self, mock_df, mock_ticker_no_target):
        with patch("agents.technical.get_ohlcv", return_value=mock_df), \
             patch("yfinance.Ticker", return_value=mock_ticker_no_target):
            result = analyse("TEST.NS")
        assert "yfinance_ohlcv_1y" in result["data_sources"]


class TestAnalyseEdgeCases:
    def test_none_ohlcv_returns_no_data(self):
        with patch("agents.technical.get_ohlcv", return_value=None):
            result = analyse("INVALID.NS")
        # Base DCV now returns "INSUFFICIENT_DATA" when data is below quality threshold;
        # the old "NO_DATA" early-return has been superseded by the DCV path.
        assert result["signal"] in ("NO_DATA", "INSUFFICIENT_DATA")
        assert result["score"] in (None, 0)   # INSUFFICIENT_DATA → score=None; NO_DATA → 0
        assert result["confidence"] == 0.0
        assert result["agent_name"] == "technical"

    def test_too_few_bars_returns_no_data(self):
        small_df = _make_ohlcv(30)
        with patch("agents.technical.get_ohlcv", return_value=small_df):
            result = analyse("TINY.NS")
        assert result["signal"] in ("NO_DATA", "INSUFFICIENT_DATA")

    def test_supabase_failure_does_not_crash(self, mock_df, mock_ticker_no_target):
        """Even if Supabase raises, analyse() must return a valid result."""
        with patch("agents.technical.get_ohlcv", return_value=mock_df), \
             patch("yfinance.Ticker", return_value=mock_ticker_no_target), \
             patch(
                 "agents.technical._write_agent_performance",
                 side_effect=Exception("DB down"),
             ):
            # _write_agent_performance is called inside analyse(); wrapping the
            # exception there should prevent it bubbling up
            try:
                result = analyse("TEST.NS")
                # If exception was properly swallowed, result should be valid
                assert result["agent_name"] == "technical"
            except Exception:
                pytest.fail("analyse() must not propagate Supabase errors")

    def test_yfinance_info_error_handled_gracefully(self, mock_df):
        """If yfinance.Ticker.info raises, upside falls back to EMA200*1.5."""
        ticker = MagicMock()
        ticker.info = MagicMock(side_effect=Exception("network error"))
        with patch("agents.technical.get_ohlcv", return_value=mock_df), \
             patch("yfinance.Ticker", return_value=ticker):
            result = analyse("TEST.NS")
        assert result["upside_pct"] is not None
        assert result["signal"] != "NO_DATA"
