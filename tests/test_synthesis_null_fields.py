"""
tests/test_synthesis_null_fields.py

Regression suite for the ARIA /analyse failure observed on 2026-08-13:

    [TATASTEEL.NS] synthesis failed:
        float() argument must be a string or a real number, not 'NoneType'

Two independent defects combined to produce it:

1. Claude emits explicit JSON nulls for numeric fields that don't apply to the
   call it made (`"target": null`, `"upside_pct": null` on a HOLD). The
   orchestrator read them with `float(synthesis_data.get(key, default))`, and
   `dict.get` does NOT substitute the default when the key exists with a None
   value — so float(None) raised and the symbol was dropped from the run.

2. yfinance 1.2.x returns a partial trailing bar with Volume but NaN OHLC.
   `get_ohlcv` passed it through, so the technical agent's completeness check
   failed on "Current close price" and returned INSUFFICIENT_DATA for every
   symbol — which is why the synthesis had no price anchor and produced nulls.

Run from project root:
    pytest tests/test_synthesis_null_fields.py -v
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scheduler.orchestrator import (            # noqa: E402
    _apply_consensus_gate,
    _build_recommendation,
    _num,
)


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def null_synthesis():
    """A Claude HOLD response with every optional numeric field set to null."""
    return {
        "action":            "HOLD",
        "confidence":        None,
        "risk_score":        None,
        "entry_low":         None,
        "entry_high":        None,
        "target":            None,
        "stoploss":          None,
        "horizon_days":      None,
        "upside_pct":        None,
        "upside_confidence": None,
        "danger_drop_pct":   None,
        "danger_confidence": None,
        "headline":          None,
        "synthesis":         None,
        "bull_case":         [],
        "bear_case":         [],
    }


@pytest.fixture()
def agent_results():
    return {
        "technical": {
            "signal": "BUY", "score": 62,
            "detail": {"indicators": {"current_price": 186.20}},
        },
        "fundamental": {
            "signal": "HOLD", "score": 50,
            "detail": {"valuation": {"current_price": 186.20}},
        },
    }


@pytest.fixture()
def weights():
    return {"technical": 0.1429, "fundamental": 0.1429}


# ──────────────────────────────────────────────────────────────────────────────
# _num()
# ──────────────────────────────────────────────────────────────────────────────

class TestNumCoercion:

    def test_none_falls_back_to_default(self):
        assert _num(None, 42.0) == 42.0

    def test_default_is_zero(self):
        assert _num(None) == 0.0

    def test_numeric_passthrough(self):
        assert _num(17.5, 0.0) == 17.5
        assert _num(0, 99.0) == 0.0          # a real 0 must NOT become the default

    def test_numeric_string_parsed(self):
        assert _num("28.5", 0.0) == 28.5

    def test_unparseable_string_falls_back(self):
        assert _num("N/A", 5.0) == 5.0

    def test_nan_and_inf_fall_back(self):
        assert _num(float("nan"), 7.0) == 7.0
        assert _num(float("inf"), 7.0) == 7.0
        assert _num(float("-inf"), 7.0) == 7.0


# ──────────────────────────────────────────────────────────────────────────────
# _build_recommendation — the actual crash site
# ──────────────────────────────────────────────────────────────────────────────

class TestBuildRecommendationWithNulls:

    def test_all_null_numerics_do_not_raise(self, null_synthesis, agent_results, weights):
        """The exact production payload shape that crashed TATASTEEL."""
        rec = _build_recommendation("TATASTEEL.NS", null_synthesis,
                                    agent_results, weights, composite=53.3)
        assert rec["action"] == "HOLD"

    def test_null_confidence_falls_back_to_composite(self, null_synthesis, agent_results, weights):
        rec = _build_recommendation("TATASTEEL.NS", null_synthesis,
                                    agent_results, weights, composite=53.3)
        assert rec["confidence"] == 53.3
        assert rec["risk_score"] == pytest.approx(46.7)

    def test_null_upside_and_danger_become_zero(self, null_synthesis, agent_results, weights):
        rec = _build_recommendation("TATASTEEL.NS", null_synthesis,
                                    agent_results, weights, composite=53.3)
        assert rec["upside_pct"] == 0.0
        assert rec["danger_drop_pct"] == 0.0
        assert rec["danger_confidence"] == 0.0

    def test_null_horizon_defaults_to_180(self, null_synthesis, agent_results, weights):
        rec = _build_recommendation("TATASTEEL.NS", null_synthesis,
                                    agent_results, weights, composite=53.3)
        assert rec["horizon_days"] == 180

    def test_nullable_price_levels_stay_none(self, null_synthesis, agent_results, weights):
        """entry/target/stoploss are nullable in the schema — they must not become 0.0."""
        rec = _build_recommendation("TATASTEEL.NS", null_synthesis,
                                    agent_results, weights, composite=53.3)
        assert rec["entry_low"] is None
        assert rec["entry_high"] is None
        assert rec["target"] is None
        assert rec["stoploss"] is None

    def test_null_headline_is_generated(self, null_synthesis, agent_results, weights):
        rec = _build_recommendation("TATASTEEL.NS", null_synthesis,
                                    agent_results, weights, composite=53.3)
        assert rec["headline"] and "TATASTEEL.NS" in rec["headline"]
        assert rec["summary"] == ""

    def test_null_action_defaults_to_hold(self, null_synthesis, agent_results, weights):
        null_synthesis["action"] = None
        rec = _build_recommendation("TATASTEEL.NS", null_synthesis,
                                    agent_results, weights, composite=53.3)
        assert rec["action"] == "HOLD"

    def test_agent_fallback_used_when_synthesis_upside_null(self, null_synthesis, weights):
        """A null upside_pct should still pick up the fundamental agent's estimate."""
        ar = {
            "technical":   {"signal": "BUY", "score": 62},
            "fundamental": {"signal": "BUY", "score": 70, "upside_pct": 22.0},
        }
        rec = _build_recommendation("X.NS", null_synthesis, ar, weights, composite=60.0)
        assert rec["upside_pct"] == 22.0

    def test_upside_derived_from_target_when_null(self, null_synthesis, agent_results, weights):
        """Claude gave a target but no upside_pct — derive rather than publish 0%."""
        null_synthesis["target"] = 220.0
        rec = _build_recommendation("TATASTEEL.NS", null_synthesis,
                                    agent_results, weights, composite=53.3)
        assert rec["target"] == 220.0
        assert rec["upside_pct"] == pytest.approx(18.15, abs=0.05)

    def test_real_values_still_honoured(self, agent_results, weights):
        """The fix must not override values Claude actually supplied."""
        sd = {
            "action": "BUY", "confidence": 72, "risk_score": 30,
            "entry_low": 180.0, "entry_high": 190.0, "target": 240.0,
            "stoploss": 170.0, "horizon_days": 90, "upside_pct": 28.5,
            "upside_confidence": 68, "danger_drop_pct": 12.0,
            "danger_confidence": 55, "headline": "BUY: X", "synthesis": "text",
        }
        rec = _build_recommendation("X.NS", sd, agent_results, weights, composite=53.3)
        assert (rec["action"], rec["confidence"], rec["upside_pct"]) == ("BUY", 72.0, 28.5)
        assert rec["horizon_days"] == 90
        assert rec["target"] == 240.0
        assert rec["headline"] == "BUY: X"


# ──────────────────────────────────────────────────────────────────────────────
# _apply_consensus_gate
# ──────────────────────────────────────────────────────────────────────────────

class TestConsensusGateWithNulls:

    def test_null_confidence_does_not_raise(self):
        ar = {"technical": {"signal": "BUY", "score": 62}}
        out = _apply_consensus_gate("X.NS", {"action": "BUY", "confidence": None}, ar)
        assert out["confidence"] == 55.0        # 65 default − 10 (single-agent conviction)

    def test_null_action_is_not_gated(self):
        out = _apply_consensus_gate("X.NS", {"action": None, "confidence": None}, {})
        assert out["action"] is None            # untouched — gate only fires on BUY

    def test_zero_bulls_downgrades_with_null_confidence(self):
        ar = {"technical": {"signal": "SELL"}, "fundamental": {"signal": "AVOID"}}
        out = _apply_consensus_gate("X.NS", {"action": "BUY", "confidence": None}, ar)
        assert out["action"] == "HOLD"
        assert out["confidence"] == 45.0        # 65 − 20


# ──────────────────────────────────────────────────────────────────────────────
# get_ohlcv — NaN trailing bar
# ──────────────────────────────────────────────────────────────────────────────

def _frame_with_partial_last_bar():
    """Three clean bars plus yfinance's Volume-only in-progress bar."""
    idx = pd.to_datetime(["2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13"])
    return pd.DataFrame(
        {
            "Open":   [189.0, 190.2, 188.3, np.nan],
            "High":   [191.0, 190.5, 188.8, np.nan],
            "Low":    [187.0, 186.7, 183.6, np.nan],
            "Close":  [190.0, 188.2, 186.2, np.nan],
            "Volume": [19_000_000, 20_973_262, 36_484_049, 19_918_432],
        },
        index=idx.tz_localize("Asia/Kolkata"),
    )


class TestGetOhlcvDropsPartialBar:

    def test_nan_close_row_dropped(self):
        from data import fetchers

        ticker = MagicMock()
        with patch.object(fetchers, "yf") as mock_yf, \
             patch.object(fetchers, "yf_fetch_with_retry",
                          return_value=_frame_with_partial_last_bar()):
            mock_yf.Ticker.return_value = ticker
            df = fetchers.get_ohlcv("TATASTEEL.NS", period="1y")

        assert len(df) == 3
        assert df["Close"].iloc[-1] == pytest.approx(186.2)
        assert not df["Close"].isna().any()

    def test_clean_frame_untouched(self):
        from data import fetchers

        clean = _frame_with_partial_last_bar().iloc[:-1]
        with patch.object(fetchers, "yf") as mock_yf, \
             patch.object(fetchers, "yf_fetch_with_retry", return_value=clean):
            mock_yf.Ticker.return_value = MagicMock()
            df = fetchers.get_ohlcv("TATASTEEL.NS", period="1y")

        assert len(df) == 3

    def test_all_nan_close_returns_none(self):
        from data import fetchers

        bad = _frame_with_partial_last_bar().copy()
        bad["Close"] = np.nan
        with patch.object(fetchers, "yf") as mock_yf, \
             patch.object(fetchers, "yf_fetch_with_retry", return_value=bad):
            mock_yf.Ticker.return_value = MagicMock()
            assert fetchers.get_ohlcv("TATASTEEL.NS", period="1y") is None

    def test_technical_snapshot_close_is_usable(self):
        """
        End-to-end guard on the completeness check: the close pulled from the
        returned frame must be a positive float, not NaN.
        """
        from data import fetchers
        from agents.base import DataCompletenessValidator

        with patch.object(fetchers, "yf") as mock_yf, \
             patch.object(fetchers, "yf_fetch_with_retry",
                          return_value=_frame_with_partial_last_bar()):
            mock_yf.Ticker.return_value = MagicMock()
            df = fetchers.get_ohlcv("TATASTEEL.NS", period="1y")

        snapshot = {
            "ohlcv_rows":  len(df),
            "close":       float(df["Close"].iloc[-1]),
            "volume_avg":  float(df["Volume"].mean()),
            "has_volume":  True,
            "ema200_rows": len(df),
        }
        result = DataCompletenessValidator().validate(snapshot, "technical")
        assert "Current close price" not in result.critical_below_threshold


# ──────────────────────────────────────────────────────────────────────────────
# /api/analyse must report the real reason
# ──────────────────────────────────────────────────────────────────────────────

class TestAnalyseSurfacesPipelineErrors:

    @pytest.mark.anyio
    async def test_synthesis_error_is_reported(self):
        """
        A crash inside synthesise_node lands in state["errors"] and yields no
        rec. Reporting that as "suppressed" hid a real bug for a full day.
        """
        from api.main import on_demand_analyse

        state = {
            "recommendations": [],
            "symbol_results":  {"TATASTEEL.NS": {}},
            "errors": ["TATASTEEL.NS synthesis: float() argument must be a "
                       "string or a real number, not 'NoneType'"],
        }
        req = MagicMock()
        req.json = AsyncMock(return_value={"symbol": "TATASTEEL"})

        with patch("api.main._resolve_yf_symbol", return_value="TATASTEEL.NS"), \
             patch("api.main.asyncio.wait_for", new_callable=AsyncMock, return_value=state):
            resp = await on_demand_analyse(req)

        assert resp["status"] == "NO_RECOMMENDATION"
        assert "float()" in resp["detail"]
        assert resp["errors"]

    @pytest.mark.anyio
    async def test_genuine_suppression_keeps_original_wording(self):
        from api.main import on_demand_analyse

        state = {"recommendations": [], "symbol_results": {"TINY.NS": {}}, "errors": []}
        req = MagicMock()
        req.json = AsyncMock(return_value={"symbol": "TINY"})

        with patch("api.main._resolve_yf_symbol", return_value="TINY.NS"), \
             patch("api.main.asyncio.wait_for", new_callable=AsyncMock, return_value=state):
            resp = await on_demand_analyse(req)

        assert resp["status"] == "NO_RECOMMENDATION"
        assert "suppressed" in resp["detail"].lower()
        assert resp["errors"] == []
