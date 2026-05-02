"""
tests/test_options_sentiment.py
================================
Unit tests for data/options_fetcher.py + agents/options_sentiment.py

Tests cover:
  - PCR scoring across all ranges
  - Max-pain scoring (above / near / below spot)
  - VIX regime scoring
  - IV skew scoring
  - IV/HV ratio scoring
  - Signal classification thresholds
  - Full analyse_options() integration with mocked fetcher
  - NSE option-chain metric calculators (_compute_pcr, _compute_max_pain, etc.)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.options_sentiment import (
    analyse_options,
    _pcr_score,
    _max_pain_score,
    _vix_score,
    _skew_score,
    _iv_hv_score,
    _classify_signal,
    _build_commentary,
)
from data.options_fetcher import (
    _compute_pcr,
    _compute_max_pain,
    _compute_atm_iv_and_skew,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_chain_row(strike, call_oi=1000, put_oi=1000, call_iv=15.0, put_iv=15.0):
    return {
        "strikePrice": strike,
        "CE": {"openInterest": call_oi, "impliedVolatility": call_iv},
        "PE": {"openInterest": put_oi, "impliedVolatility": put_iv},
    }


def _make_records(rows, underlying=22000.0):
    return {"data": rows, "underlyingValue": underlying}


# ──────────────────────────────────────────────────────────────────────────────
# _pcr_score
# ──────────────────────────────────────────────────────────────────────────────

class TestPCRScore:
    def test_none_returns_neutral(self):
        score, note = _pcr_score(None)
        assert score == 17
        assert "unavailable" in note.lower()

    def test_very_low_pcr_bullish(self):
        score, note = _pcr_score(0.5)
        assert score == 35
        assert "bullish" in note.lower()

    def test_low_pcr_moderately_bullish(self):
        score, note = _pcr_score(0.8)
        assert score == 27

    def test_balanced_pcr_neutral(self):
        score, note = _pcr_score(1.0)
        assert score == 17

    def test_elevated_pcr_bearish(self):
        score, note = _pcr_score(1.3)
        assert score == 8
        assert "bearish" in note.lower()

    def test_very_high_pcr_strongly_bearish(self):
        score, note = _pcr_score(2.0)
        assert score == 0
        assert "bearish" in note.lower()

    def test_boundary_090(self):
        # exactly 0.9 should hit the < 1.1 branch (neutral = 17)
        score, _ = _pcr_score(0.9)
        assert score == 17

    def test_boundary_110(self):
        # exactly 1.1 should hit the < 1.5 branch (bearish = 8)
        score, _ = _pcr_score(1.1)
        assert score == 8


# ──────────────────────────────────────────────────────────────────────────────
# _max_pain_score
# ──────────────────────────────────────────────────────────────────────────────

class TestMaxPainScore:
    def test_none_max_pain_neutral(self):
        score, note = _max_pain_score(None, 22000)
        assert score == 12
        assert "unavailable" in note.lower()

    def test_none_underlying_neutral(self):
        score, _ = _max_pain_score(22000, None)
        assert score == 12

    def test_price_well_above_max_pain(self):
        score, note = _max_pain_score(21000, 21750)  # +3.5%
        assert score == 25
        assert "bullish" in note.lower()

    def test_price_slightly_above(self):
        score, note = _max_pain_score(22000, 22100)  # +0.45%
        assert score == 18

    def test_price_near_max_pain(self):
        score, _ = _max_pain_score(22000, 21950)  # -0.23%
        assert score == 10

    def test_price_well_below_max_pain(self):
        score, note = _max_pain_score(23000, 22000)  # -4.3%
        assert score == 2
        assert "bearish" in note.lower()


# ──────────────────────────────────────────────────────────────────────────────
# _vix_score
# ──────────────────────────────────────────────────────────────────────────────

class TestVIXScore:
    def test_none_neutral(self):
        score, _ = _vix_score(None)
        assert score == 10

    def test_very_low_vix_bullish(self):
        score, note = _vix_score(11.0)
        assert score == 20
        assert "bullish" in note.lower()

    def test_calm_market(self):
        score, _ = _vix_score(16.0)
        assert score == 15

    def test_moderate_vix(self):
        score, _ = _vix_score(20.0)
        assert score == 10

    def test_elevated_vix(self):
        score, _ = _vix_score(25.0)
        assert score == 4

    def test_extreme_vix(self):
        score, note = _vix_score(35.0)
        assert score == 0
        assert "bearish" in note.lower()


# ──────────────────────────────────────────────────────────────────────────────
# _skew_score
# ──────────────────────────────────────────────────────────────────────────────

class TestSkewScore:
    def test_none_neutral(self):
        score, _ = _skew_score(None)
        assert score == 5

    def test_large_negative_skew_bullish(self):
        score, note = _skew_score(-5.0)
        assert score == 10
        assert "bullish" in note.lower()

    def test_slight_negative_skew(self):
        score, _ = _skew_score(-1.5)
        assert score == 7

    def test_neutral_skew(self):
        score, _ = _skew_score(2.0)
        assert score == 5

    def test_heavy_put_skew_bearish(self):
        score, note = _skew_score(8.0)
        assert score == 1
        assert "bearish" in note.lower()


# ──────────────────────────────────────────────────────────────────────────────
# _iv_hv_score
# ──────────────────────────────────────────────────────────────────────────────

class TestIVHVScore:
    def test_none_neutral(self):
        score, _ = _iv_hv_score(None)
        assert score == 5

    def test_options_cheap(self):
        score, note = _iv_hv_score(0.7)
        assert score == 10
        assert "bullish" in note.lower()

    def test_normal_premium(self):
        score, _ = _iv_hv_score(1.0)
        assert score == 7

    def test_elevated_premium(self):
        score, _ = _iv_hv_score(1.3)
        assert score == 3

    def test_very_expensive_options(self):
        score, _ = _iv_hv_score(2.0)
        assert score == 0


# ──────────────────────────────────────────────────────────────────────────────
# _classify_signal
# ──────────────────────────────────────────────────────────────────────────────

class TestClassifySignal:
    @pytest.mark.parametrize("score,expected", [
        (90, "STRONG_BULLISH"),
        (78, "STRONG_BULLISH"),
        (70, "BULLISH"),
        (60, "BULLISH"),
        (55, "NEUTRAL"),
        (42, "NEUTRAL"),
        (30, "BEARISH"),
        (24, "BEARISH"),
        (10, "STRONG_BEARISH"),
        (0,  "STRONG_BEARISH"),
    ])
    def test_thresholds(self, score, expected):
        assert _classify_signal(score) == expected


# ──────────────────────────────────────────────────────────────────────────────
# NSE option chain calculators
# ──────────────────────────────────────────────────────────────────────────────

class TestComputePCR:
    def test_equal_oi_returns_one(self):
        rows = [_make_chain_row(22000, call_oi=1000, put_oi=1000),
                _make_chain_row(22500, call_oi=500,  put_oi=500)]
        records = _make_records(rows)
        assert _compute_pcr(records) == pytest.approx(1.0)

    def test_more_puts_above_one(self):
        rows = [_make_chain_row(22000, call_oi=1000, put_oi=2000)]
        assert _compute_pcr(_make_records(rows)) == pytest.approx(2.0)

    def test_more_calls_below_one(self):
        rows = [_make_chain_row(22000, call_oi=2000, put_oi=1000)]
        assert _compute_pcr(_make_records(rows)) == pytest.approx(0.5)

    def test_zero_call_oi_returns_none(self):
        rows = [_make_chain_row(22000, call_oi=0, put_oi=1000)]
        assert _compute_pcr(_make_records(rows)) is None

    def test_empty_data(self):
        assert _compute_pcr({"data": []}) is None


class TestComputeMaxPain:
    def test_returns_float(self):
        rows = [
            _make_chain_row(21000, call_oi=500,  put_oi=3000),
            _make_chain_row(22000, call_oi=3000, put_oi=3000),
            _make_chain_row(23000, call_oi=3000, put_oi=500),
        ]
        mp = _compute_max_pain(_make_records(rows))
        assert isinstance(mp, float)

    def test_balanced_chain_near_atm(self):
        # Equal OI everywhere — max pain should be some middle strike
        rows = [_make_chain_row(k, call_oi=1000, put_oi=1000) for k in range(20000, 24500, 500)]
        mp = _compute_max_pain(_make_records(rows))
        assert 20000 <= mp <= 24000

    def test_empty_returns_none(self):
        assert _compute_max_pain({"data": []}) is None


class TestComputeATMIVAndSkew:
    def test_atm_iv_computed(self):
        rows = [_make_chain_row(22000, call_iv=18.0, put_iv=18.0)]
        atm_iv, _ = _compute_atm_iv_and_skew(_make_records(rows, 22000), 22000)
        assert atm_iv == pytest.approx(18.0)

    def test_zero_underlying_returns_none(self):
        rows = [_make_chain_row(22000)]
        atm_iv, iv_skew = _compute_atm_iv_and_skew(_make_records(rows), 0)
        assert atm_iv is None


# ──────────────────────────────────────────────────────────────────────────────
# Integration: analyse_options() with mocked fetcher
# ──────────────────────────────────────────────────────────────────────────────

def _mock_metrics(overrides: dict) -> dict:
    base = {
        "symbol":           "NIFTY",
        "pcr":              1.0,
        "max_pain":         22000.0,
        "atm_iv":           18.0,
        "iv_skew":          2.0,
        "india_vix":        16.0,
        "hv20":             15.0,
        "iv_hv_ratio":      1.07,
        "underlying_price": 22200.0,
        "source":           "fallback",
    }
    return {**base, **overrides}


class TestAnalyseOptions:
    def _run(self, metrics_dict):
        with patch("data.options_fetcher.get_option_metrics", return_value=metrics_dict):
            return analyse_options("NIFTY")

    def test_result_has_required_keys(self):
        r = self._run(_mock_metrics({}))
        for key in ("symbol", "signal", "score", "pcr", "max_pain", "atm_iv",
                    "iv_skew", "india_vix", "hv20", "iv_hv_ratio",
                    "underlying_price", "source", "commentary", "agent_name"):
            assert key in r

    def test_agent_name(self):
        r = self._run(_mock_metrics({}))
        assert r["agent_name"] == "options_sentiment"

    def test_score_between_0_and_100(self):
        r = self._run(_mock_metrics({}))
        assert 0 <= r["score"] <= 100

    def test_bullish_scenario(self):
        r = self._run(_mock_metrics({
            "pcr": 0.6, "india_vix": 12.0,
            "underlying_price": 22500.0, "max_pain": 21500.0,
            "iv_skew": -2.0, "iv_hv_ratio": 0.85,
        }))
        assert r["signal"] in ("STRONG_BULLISH", "BULLISH")
        assert r["score"] >= 60

    def test_bearish_scenario(self):
        r = self._run(_mock_metrics({
            "pcr": 1.8, "india_vix": 30.0,
            "underlying_price": 20000.0, "max_pain": 22000.0,
            "iv_skew": 10.0, "iv_hv_ratio": 1.8,
        }))
        assert r["signal"] in ("STRONG_BEARISH", "BEARISH")
        assert r["score"] <= 30

    def test_neutral_scenario(self):
        r = self._run(_mock_metrics({
            "pcr": 1.0, "india_vix": 18.0,
            "underlying_price": 22000.0, "max_pain": 22000.0,
            "iv_skew": 0.0, "iv_hv_ratio": 1.0,
        }))
        assert r["signal"] == "NEUTRAL"

    def test_score_breakdown_present(self):
        r = self._run(_mock_metrics({}))
        bd = r.get("score_breakdown", {})
        assert "pcr_score" in bd
        assert "vix_score" in bd
        assert sum(bd.values()) == r["score"]

    def test_no_data_signal_on_error(self):
        with patch("data.options_fetcher.get_option_metrics", return_value={"error": "no_price_data"}):
            r = analyse_options("BADCO")
        assert r["signal"] == "NO_DATA"

    def test_never_raises_on_fetcher_exception(self):
        with patch("data.options_fetcher.get_option_metrics", side_effect=Exception("boom")):
            try:
                r = analyse_options("CRASH")
            except Exception as exc:
                pytest.fail(f"analyse_options raised: {exc}")
        assert r is not None

    def test_symbol_normalised(self):
        with patch("data.options_fetcher.get_option_metrics", return_value=_mock_metrics({"symbol": "RELIANCE"})):
            r = analyse_options("RELIANCE.NS")
        assert r["symbol"] == "RELIANCE"

    def test_nse_source_commentary(self):
        r = self._run(_mock_metrics({"source": "nse"}))
        assert "NSE option chain unavailable" not in r["commentary"]

    def test_fallback_source_warns(self):
        r = self._run(_mock_metrics({"source": "fallback"}))
        assert "NSE option chain unavailable" in r["commentary"]
