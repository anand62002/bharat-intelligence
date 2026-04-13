"""
tests/test_commodities.py — Unit tests for agents/commodities.py

All yfinance and Supabase calls are mocked. Tests cover:
- Trend calculation
- INR correlation calculation
- Seasonal bias
- Per-commodity scoring (gold, crude, silver)
- Critical gold upside flag
- analyse() output schema and signal derivation
"""

import math
import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock
from datetime import date


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def no_supabase(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "")


from agents.commodities import (
    _latest_price,
    _trend_50d,
    _inr_correlation,
    _seasonal_month_bias,
    _score_gold,
    _score_crude,
    _score_silver,
    _signal_from_score,
    _check_critical_gold_upside,
    analyse,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_df(n: int = 60, start_price: float = 100.0, drift: float = 0.0) -> pd.DataFrame:
    """Return a simple OHLCV DataFrame with a linear price trend."""
    prices = [start_price + drift * i for i in range(n)]
    return pd.DataFrame({
        "Open":   prices,
        "High":   [p * 1.01 for p in prices],
        "Low":    [p * 0.99 for p in prices],
        "Close":  prices,
        "Volume": [1_000_000] * n,
    })


def _make_rising_df(n: int = 60) -> pd.DataFrame:
    return _make_df(n, start_price=100.0, drift=1.0)


def _make_falling_df(n: int = 60) -> pd.DataFrame:
    return _make_df(n, start_price=160.0, drift=-1.0)


def _make_flat_df(n: int = 60) -> pd.DataFrame:
    return _make_df(n, start_price=100.0, drift=0.0)


# ──────────────────────────────────────────────────────────────────────────────
# _latest_price
# ──────────────────────────────────────────────────────────────────────────────

class TestLatestPrice:
    def test_returns_last_close(self):
        df = _make_df(10, start_price=50.0, drift=2.0)
        assert _latest_price(df) == pytest.approx(68.0)

    def test_none_df_returns_none(self):
        assert _latest_price(None) is None

    def test_empty_df_returns_none(self):
        assert _latest_price(pd.DataFrame()) is None


# ──────────────────────────────────────────────────────────────────────────────
# _trend_50d
# ──────────────────────────────────────────────────────────────────────────────

class TestTrend50d:
    def test_rising_trend_positive(self):
        df = _make_rising_df(60)
        trend = _trend_50d(df)
        assert trend is not None
        assert trend > 0

    def test_falling_trend_negative(self):
        df = _make_falling_df(60)
        trend = _trend_50d(df)
        assert trend is not None
        assert trend < 0

    def test_flat_near_zero(self):
        df = _make_flat_df(60)
        trend = _trend_50d(df)
        assert trend == pytest.approx(0.0)

    def test_none_df_returns_none(self):
        assert _trend_50d(None) is None

    def test_empty_df_returns_none(self):
        assert _trend_50d(pd.DataFrame()) is None

    def test_small_df_uses_all(self):
        # Only 10 rows, should still work (uses min(50, len))
        df = _make_rising_df(10)
        trend = _trend_50d(df)
        assert trend is not None

    def test_single_row_returns_none(self):
        df = _make_df(1)
        assert _trend_50d(df) is None


# ──────────────────────────────────────────────────────────────────────────────
# _inr_correlation
# ──────────────────────────────────────────────────────────────────────────────

class TestINRCorrelation:
    def test_perfect_positive_correlation(self):
        # Both series move identically
        df1 = _make_rising_df(40)
        df2 = _make_rising_df(40)
        corr = _inr_correlation(df1, df2, window=30)
        assert corr is not None
        assert corr == pytest.approx(1.0, abs=0.01)

    def test_perfect_negative_correlation(self):
        df1 = _make_rising_df(40)
        df2 = _make_falling_df(40)
        corr = _inr_correlation(df1, df2, window=30)
        assert corr is not None
        assert corr < -0.9

    def test_none_dfs_return_none(self):
        assert _inr_correlation(None, None) is None
        assert _inr_correlation(_make_rising_df(), None) is None
        assert _inr_correlation(None, _make_rising_df()) is None

    def test_result_is_float(self):
        df = _make_rising_df(50)
        corr = _inr_correlation(df, df, window=30)
        assert isinstance(corr, float)

    def test_result_rounded_to_4(self):
        df = _make_rising_df(50)
        corr = _inr_correlation(df, df, window=30)
        if corr is not None:
            assert corr == round(corr, 4)


# ──────────────────────────────────────────────────────────────────────────────
# _seasonal_month_bias
# ──────────────────────────────────────────────────────────────────────────────

class TestSeasonalMonthBias:
    def test_gold_diwali_season_strong(self):
        with patch("agents.commodities.date") as mock_date:
            mock_date.today.return_value = date(2024, 11, 1)
            assert _seasonal_month_bias("gold") == "STRONG"

    def test_gold_wedding_season_moderate(self):
        with patch("agents.commodities.date") as mock_date:
            mock_date.today.return_value = date(2024, 5, 1)
            assert _seasonal_month_bias("gold") == "MODERATE"

    def test_gold_offseason_neutral(self):
        with patch("agents.commodities.date") as mock_date:
            mock_date.today.return_value = date(2024, 7, 1)
            assert _seasonal_month_bias("gold") == "NEUTRAL"

    def test_crude_summer_strong(self):
        with patch("agents.commodities.date") as mock_date:
            mock_date.today.return_value = date(2024, 7, 15)
            assert _seasonal_month_bias("crude") == "STRONG"

    def test_crude_winter_moderate(self):
        with patch("agents.commodities.date") as mock_date:
            mock_date.today.return_value = date(2024, 1, 15)
            assert _seasonal_month_bias("crude") == "MODERATE"

    def test_silver_oct_moderate(self):
        with patch("agents.commodities.date") as mock_date:
            mock_date.today.return_value = date(2024, 10, 1)
            assert _seasonal_month_bias("silver") == "MODERATE"

    def test_unknown_commodity_returns_neutral(self):
        result = _seasonal_month_bias("platinum")
        assert result == "NEUTRAL"

    def test_all_valid_outputs(self):
        for commodity in ["gold", "crude", "silver"]:
            for month in range(1, 13):
                with patch("agents.commodities.date") as mock_date:
                    mock_date.today.return_value = date(2024, month, 1)
                    result = _seasonal_month_bias(commodity)
                    assert result in ("STRONG", "MODERATE", "NEUTRAL", "WEAK")


# ──────────────────────────────────────────────────────────────────────────────
# _score_gold
# ──────────────────────────────────────────────────────────────────────────────

class TestScoreGold:
    def test_returns_tuple_3(self):
        score, note, upside = _score_gold(5.0, 0.6, "STRONG", 84.0)
        assert isinstance(score, int)
        assert isinstance(note, str)
        assert isinstance(upside, float)

    def test_score_in_range(self):
        for trend in [-10.0, 0.0, 5.0, 15.0, None]:
            score, _, _ = _score_gold(trend, 0.5, "NEUTRAL", 84.0)
            assert 0 <= score <= 100

    def test_strong_trend_high_score(self):
        score, _, _ = _score_gold(12.0, 0.8, "STRONG", 84.0)
        assert score >= 70

    def test_downtrend_low_trend_score(self):
        score_down, _, _ = _score_gold(-10.0, 0.1, "NEUTRAL", 83.0)
        score_up,   _, _ = _score_gold(10.0,  0.8, "STRONG",  84.0)
        assert score_up > score_down

    def test_high_inr_corr_adds_points(self):
        score_high, _, _ = _score_gold(5.0, 0.8, "NEUTRAL", 84.0)
        score_low,  _, _ = _score_gold(5.0, 0.1, "NEUTRAL", 84.0)
        assert score_high > score_low

    def test_none_trend_midrange(self):
        score, note, _ = _score_gold(None, None, "NEUTRAL", None)
        assert 30 <= score <= 70
        assert "unknown" in note.lower() or "neutral" in note.lower() or "base" in note.lower()

    def test_upside_positive_for_uptrend(self):
        _, _, upside = _score_gold(8.0, 0.5, "NEUTRAL", 84.0)
        assert upside > 5.0

    def test_upside_low_for_downtrend(self):
        _, _, upside = _score_gold(-5.0, 0.3, "NEUTRAL", 84.0)
        assert upside == pytest.approx(5.0)


# ──────────────────────────────────────────────────────────────────────────────
# _score_crude
# ──────────────────────────────────────────────────────────────────────────────

class TestScoreCrude:
    def test_returns_tuple_3(self):
        score, note, upside = _score_crude(-5.0, 83.0, "NEUTRAL")
        assert isinstance(score, int)
        assert isinstance(note, str)
        assert isinstance(upside, float)

    def test_score_in_range(self):
        for trend in [-15.0, 0.0, 10.0, None]:
            score, _, _ = _score_crude(trend, 84.0, "NEUTRAL")
            assert 0 <= score <= 100

    def test_falling_crude_high_score(self):
        score, note, _ = _score_crude(-12.0, 82.0, "NEUTRAL")
        assert score >= 60
        assert "fall" in note.lower() or "positive" in note.lower()

    def test_rising_crude_weak_inr_low_score(self):
        score, _, _ = _score_crude(12.0, 89.0, "STRONG")
        assert score < 40

    def test_strong_inr_adds_points(self):
        score_strong, _, _ = _score_crude(0.0, 81.0, "NEUTRAL")
        score_weak,   _, _ = _score_crude(0.0, 89.0, "NEUTRAL")
        assert score_strong > score_weak

    def test_strong_crude_demand_season_lowers_score(self):
        score_strong_season, _, _ = _score_crude(0.0, 84.0, "STRONG")
        score_neutral_season, _, _ = _score_crude(0.0, 84.0, "NEUTRAL")
        assert score_neutral_season >= score_strong_season

    def test_none_inputs_no_crash(self):
        score, _, upside = _score_crude(None, None, "NEUTRAL")
        assert 0 <= score <= 100


# ──────────────────────────────────────────────────────────────────────────────
# _score_silver
# ──────────────────────────────────────────────────────────────────────────────

class TestScoreSilver:
    def test_returns_tuple_3(self):
        score, note, upside = _score_silver(5.0, 0.5, "MODERATE")
        assert isinstance(score, int)
        assert isinstance(note, str)
        assert isinstance(upside, float)

    def test_score_in_range(self):
        for trend in [-10.0, 0.0, 8.0, 15.0, None]:
            score, _, _ = _score_silver(trend, 0.4, "NEUTRAL")
            assert 0 <= score <= 100

    def test_strong_uptrend_high_score(self):
        score, _, _ = _score_silver(12.0, 0.7, "STRONG")
        assert score >= 70

    def test_downtrend_low_score(self):
        score_down, _, _ = _score_silver(-8.0, 0.1, "WEAK")
        score_up,   _, _ = _score_silver(12.0, 0.7, "STRONG")
        assert score_up > score_down

    def test_upside_proportional_to_trend(self):
        _, _, upside_small = _score_silver(3.0, 0.4, "NEUTRAL")
        _, _, upside_large = _score_silver(10.0, 0.4, "NEUTRAL")
        assert upside_large > upside_small


# ──────────────────────────────────────────────────────────────────────────────
# _signal_from_score
# ──────────────────────────────────────────────────────────────────────────────

class TestSignalFromScore:
    def test_high_score_bullish(self):
        assert _signal_from_score(70) == "BULLISH"
        assert _signal_from_score(100) == "BULLISH"

    def test_mid_score_neutral(self):
        assert _signal_from_score(50) == "NEUTRAL"
        assert _signal_from_score(40) == "NEUTRAL"
        assert _signal_from_score(64) == "NEUTRAL"

    def test_low_score_bearish(self):
        assert _signal_from_score(30) == "BEARISH"
        assert _signal_from_score(0) == "BEARISH"

    def test_boundaries(self):
        assert _signal_from_score(65) == "BULLISH"
        assert _signal_from_score(64) == "NEUTRAL"
        assert _signal_from_score(39) == "BEARISH"


# ──────────────────────────────────────────────────────────────────────────────
# _check_critical_gold_upside
# ──────────────────────────────────────────────────────────────────────────────

class TestCriticalGoldUpside:
    def test_all_conditions_met(self):
        flag, conditions = _check_critical_gold_upside(
            gold_trend=7.0,
            dxy_trend=-2.5,
            inr_trend_pct=2.0,
            fed_cutting=True,
        )
        assert flag is True
        assert len(conditions) == 4

    def test_only_3_conditions(self):
        # Missing gold uptrend (< 5%)
        flag, conditions = _check_critical_gold_upside(
            gold_trend=3.0,    # below 5.0 threshold
            dxy_trend=-2.5,
            inr_trend_pct=2.0,
            fed_cutting=True,
        )
        assert flag is False
        assert len(conditions) == 3

    def test_only_2_conditions(self):
        flag, conditions = _check_critical_gold_upside(
            gold_trend=3.0,
            dxy_trend=0.5,     # not falling enough
            inr_trend_pct=2.0,
            fed_cutting=True,
        )
        assert flag is False

    def test_no_conditions(self):
        flag, conditions = _check_critical_gold_upside(
            gold_trend=-2.0,
            dxy_trend=1.0,
            inr_trend_pct=-1.0,
            fed_cutting=False,
        )
        assert flag is False
        assert len(conditions) == 0

    def test_none_values_skip_condition(self):
        flag, conditions = _check_critical_gold_upside(
            gold_trend=None,
            dxy_trend=None,
            inr_trend_pct=None,
            fed_cutting=True,
        )
        # Only fed_cutting fires
        assert flag is False
        assert len(conditions) == 1

    def test_fed_cutting_false_excluded(self):
        flag, conditions = _check_critical_gold_upside(
            gold_trend=7.0,
            dxy_trend=-2.5,
            inr_trend_pct=2.0,
            fed_cutting=False,   # only 3 conditions now
        )
        assert flag is False
        assert len(conditions) == 3


# ──────────────────────────────────────────────────────────────────────────────
# analyse() — integration
# ──────────────────────────────────────────────────────────────────────────────

def _make_mock_ticker(df):
    mock = MagicMock()
    mock.history.return_value = df
    return mock


def _patch_yf(monkeypatch, tickers: dict):
    """
    Patch yfinance.Ticker to return different DataFrames per ticker symbol.
    tickers: {symbol: DataFrame or None}
    """
    def fake_ticker(symbol):
        df = tickers.get(symbol)
        mock = MagicMock()
        mock.history.return_value = df if df is not None else pd.DataFrame()
        return mock

    monkeypatch.setattr("yfinance.Ticker", fake_ticker)


class TestAnalyse:
    def _setup_dfs(self):
        return {
            "GC=F":         _make_rising_df(60),
            "CL=F":         _make_falling_df(60),
            "SI=F":         _make_rising_df(60),
            "USDINR=X":     _make_rising_df(60),   # rising = depreciating INR
            "GOLDBEES.NS":  None,                   # not available
            "CRUDEOIL.NS":  None,
        }

    def test_output_schema(self, monkeypatch):
        import yfinance as yf
        _patch_yf(monkeypatch, self._setup_dfs())
        result = analyse()

        assert "signal" in result
        assert "score" in result
        assert "commodities" in result
        assert "critical_gold_upside" in result
        assert "gold_upside_conditions" in result
        assert "data_sources" in result
        assert result["agent_name"] == "commodities"

    def test_commodities_has_three_keys(self, monkeypatch):
        import yfinance as yf
        _patch_yf(monkeypatch, self._setup_dfs())
        result = analyse()
        assert set(result["commodities"].keys()) == {"gold", "crude", "silver"}

    def test_per_commodity_schema(self, monkeypatch):
        import yfinance as yf
        _patch_yf(monkeypatch, self._setup_dfs())
        result = analyse()
        for commodity in ["gold", "crude", "silver"]:
            c = result["commodities"][commodity]
            assert "signal" in c
            assert "score" in c
            assert "upside_pct" in c
            assert "detail" in c
            assert c["agent_name"] == "commodities"

    def test_scores_in_range(self, monkeypatch):
        import yfinance as yf
        _patch_yf(monkeypatch, self._setup_dfs())
        result = analyse()
        assert 0 <= result["score"] <= 100
        for c in result["commodities"].values():
            assert 0 <= c["score"] <= 100

    def test_signal_valid(self, monkeypatch):
        import yfinance as yf
        _patch_yf(monkeypatch, self._setup_dfs())
        result = analyse()
        assert result["signal"] in ("BULLISH", "NEUTRAL", "BEARISH")
        for c in result["commodities"].values():
            assert c["signal"] in ("BULLISH", "NEUTRAL", "BEARISH")

    def test_all_tickers_unavailable_graceful(self, monkeypatch):
        import yfinance as yf
        empty_dfs = {k: None for k in ["GC=F", "CL=F", "SI=F", "USDINR=X", "GOLDBEES.NS", "CRUDEOIL.NS"]}
        _patch_yf(monkeypatch, empty_dfs)
        result = analyse()
        # Should still return valid structure
        assert "signal" in result
        assert 0 <= result["score"] <= 100

    def test_yfinance_exception_graceful(self, monkeypatch):
        mock = MagicMock()
        mock.history.side_effect = Exception("network error")
        monkeypatch.setattr("yfinance.Ticker", lambda s: mock)
        result = analyse()
        assert "signal" in result

    def test_critical_gold_upside_in_result(self, monkeypatch):
        import yfinance as yf
        _patch_yf(monkeypatch, self._setup_dfs())
        result = analyse()
        assert isinstance(result["critical_gold_upside"], bool)
        assert isinstance(result["gold_upside_conditions"], list)

    def test_goldbees_preferred_over_gcf(self, monkeypatch):
        """GOLDBEES.NS price should be used when available."""
        import yfinance as yf
        dfs = self._setup_dfs()
        dfs["GOLDBEES.NS"] = _make_df(60, start_price=55.0, drift=0.5)
        _patch_yf(monkeypatch, dfs)
        result = analyse()
        # GOLDBEES price (≈ 55+0.5*59=84.5) should be used, not GC=F (≈159)
        gold_price = result["commodities"]["gold"]["price"]
        assert gold_price is not None
        # GOLDBEES.NS last price ≈ 84.5; GC=F last price ≈ 159
        assert gold_price < 100  # confirms GOLDBEES used

    def test_supabase_write_failure_does_not_propagate(self, monkeypatch):
        import yfinance as yf
        _patch_yf(monkeypatch, self._setup_dfs())
        with patch("agents.commodities._write_agent_performance",
                   side_effect=Exception("DB down")):
            result = analyse()
        assert "signal" in result

    def test_data_sources_populated(self, monkeypatch):
        import yfinance as yf
        _patch_yf(monkeypatch, self._setup_dfs())
        result = analyse()
        assert isinstance(result["data_sources"], list)
        # With non-empty DFs, at least yfinance_commodities should appear
        assert "yfinance_commodities" in result["data_sources"]

    def test_overall_score_weighted_average(self, monkeypatch):
        """Overall score = gold*0.4 + crude*0.4 + silver*0.2 (rounded)."""
        import yfinance as yf
        _patch_yf(monkeypatch, self._setup_dfs())
        result = analyse()
        g = result["commodities"]["gold"]["score"]
        c = result["commodities"]["crude"]["score"]
        s = result["commodities"]["silver"]["score"]
        expected = round(g * 0.4 + c * 0.4 + s * 0.2)
        assert result["score"] == expected
