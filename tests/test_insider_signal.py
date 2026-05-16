"""
tests/test_insider_signal.py
pytest suite for data/insider_signal.py

Run from project root:
    pytest tests/test_insider_signal.py -v
"""

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.insider_signal import (
    get_promoter_signal,
    _classify,
    _build_note,
    _ACCUM_1Y_PP,
    _ACCUM_3Y_PP,
    _DISTRIB_1Y_PP,
    _DISTRIB_3Y_PP,
)


# ──────────────────────────────────────────────────────────────────────────────
# Unit: _classify
# ──────────────────────────────────────────────────────────────────────────────

class TestClassify:
    def test_accumulating_by_1y(self):
        """≥1 pp gain in 1 year → ACCUMULATING."""
        assert _classify(1.0, None) == "ACCUMULATING"
        assert _classify(2.5, None) == "ACCUMULATING"

    def test_accumulating_by_3y(self):
        """≥2 pp gain over 3 years → ACCUMULATING even with small 1y change."""
        assert _classify(0.5, 3.0) == "ACCUMULATING"
        assert _classify(0.0, 2.0) == "ACCUMULATING"

    def test_distributing_by_1y(self):
        """≥2 pp loss in 1 year → DISTRIBUTING."""
        assert _classify(-2.0, None) == "DISTRIBUTING"
        assert _classify(-5.0, None) == "DISTRIBUTING"

    def test_distributing_by_3y(self):
        """≥5 pp loss over 3 years → DISTRIBUTING."""
        assert _classify(-1.0, -5.0) == "DISTRIBUTING"
        assert _classify(-1.0, -8.0) == "DISTRIBUTING"

    def test_neutral_small_changes(self):
        """Changes within noise band → NEUTRAL."""
        assert _classify(0.5, 1.0) == "NEUTRAL"
        assert _classify(-1.0, -2.0) == "NEUTRAL"
        assert _classify(0.0, 0.0) == "NEUTRAL"

    def test_none_change_1y_is_neutral(self):
        assert _classify(None, None) == "NEUTRAL"
        assert _classify(None, 5.0) == "NEUTRAL"

    def test_threshold_boundaries(self):
        """Exactly at threshold → classified."""
        assert _classify(_ACCUM_1Y_PP, None) == "ACCUMULATING"
        assert _classify(_DISTRIB_1Y_PP, None) == "DISTRIBUTING"
        assert _classify(None, None) == "NEUTRAL"


# ──────────────────────────────────────────────────────────────────────────────
# Unit: _build_note
# ──────────────────────────────────────────────────────────────────────────────

class TestBuildNote:
    def test_accumulating_note(self):
        note = _build_note("ACCUMULATING", 52.0, 2.0, 4.0)
        assert "Promoter buying" in note
        assert "52.0" in note

    def test_distributing_note(self):
        note = _build_note("DISTRIBUTING", 45.0, -3.0, -6.0)
        assert "Promoter selling" in note

    def test_neutral_note(self):
        note = _build_note("NEUTRAL", 50.0, 0.3, 1.0)
        assert "stable" in note.lower()

    def test_missing_current_no_crash(self):
        note = _build_note("NEUTRAL", None, None, None)
        assert isinstance(note, str)
        assert len(note) > 0


# ──────────────────────────────────────────────────────────────────────────────
# Integration: get_promoter_signal via screener history
# ──────────────────────────────────────────────────────────────────────────────

class TestGetPromoterSignalHistory:
    def test_accumulating_from_history(self):
        """Promoter holding rising 1 pp/year over 5 years → ACCUMULATING."""
        history = {"promoter_holding": [47.0, 48.0, 49.0, 50.0, 51.0]}
        with patch("data.insider_signal.get_screener_history", return_value=history), \
             patch("data.insider_signal.get_screener_data", return_value=None):
            result = get_promoter_signal("RELIANCE")
        assert result["signal"] == "ACCUMULATING"
        assert result["source"] == "screener_history"
        assert result["current_holding"] == 51.0
        assert result["change_1y"] == 1.0

    def test_distributing_from_history(self):
        """Promoter holding falling 3 pp in 1 year → DISTRIBUTING."""
        history = {"promoter_holding": [55.0, 54.0, 53.0, 51.0, 48.0]}
        with patch("data.insider_signal.get_screener_history", return_value=history), \
             patch("data.insider_signal.get_screener_data", return_value=None):
            result = get_promoter_signal("RELIANCE")
        assert result["signal"] == "DISTRIBUTING"
        assert result["change_1y"] == -3.0

    def test_neutral_from_history(self):
        """Holding nearly flat → NEUTRAL."""
        history = {"promoter_holding": [50.0, 50.2, 50.1, 50.3, 50.4]}
        with patch("data.insider_signal.get_screener_history", return_value=history), \
             patch("data.insider_signal.get_screener_data", return_value=None):
            result = get_promoter_signal("RELIANCE")
        assert result["signal"] == "NEUTRAL"

    def test_3y_change_computed_correctly(self):
        """3-year change = current - value 3 years ago (ph_valid[-4] for 5 valid points)."""
        history = {"promoter_holding": [45.0, 47.0, 48.0, 49.0, 50.0]}
        # Indices: 0→45, 1→47, 2→48, 3→49, 4→50 (current)
        # 1y change: 50 - 49 = 1.0
        # 3y change: 50 - ph_valid[-4] = 50 - 47 = 3.0 (3 years earlier)
        with patch("data.insider_signal.get_screener_history", return_value=history), \
             patch("data.insider_signal.get_screener_data", return_value=None):
            result = get_promoter_signal("RELIANCE")
        assert result["change_1y"] == 1.0
        assert result["change_3y"] == 3.0
        assert result["signal"] == "ACCUMULATING"

    def test_only_2_history_points_still_works(self):
        """Minimum 2 valid points → computes 1y change, no 3y change."""
        history = {"promoter_holding": [None, None, None, 49.0, 51.0]}
        with patch("data.insider_signal.get_screener_history", return_value=history), \
             patch("data.insider_signal.get_screener_data", return_value=None):
            result = get_promoter_signal("RELIANCE")
        assert result["source"] == "screener_history"
        assert result["change_1y"] == 2.0
        assert result["change_3y"] is None    # only 2 valid points
        assert result["signal"] == "ACCUMULATING"

    def test_single_history_point_falls_back_to_snapshot(self):
        """Only 1 valid history point → cannot compute trend → falls back to screener snapshot."""
        history = {"promoter_holding": [None, None, None, None, 51.0]}
        snap = {"promoter_holding": 51.0, "pe": 20.0}
        with patch("data.insider_signal.get_screener_history", return_value=history), \
             patch("data.insider_signal.get_screener_data", return_value=snap):
            result = get_promoter_signal("RELIANCE")
        # 1 valid point → no trend → falls back to snapshot (NEUTRAL)
        assert result["signal"] == "NEUTRAL"
        assert result["source"] in ("screener_history", "screener_snapshot")

    def test_empty_history_falls_back_to_snapshot(self):
        """Empty / all-None history → falls through to snapshot."""
        snap = {"promoter_holding": 52.5}
        with patch("data.insider_signal.get_screener_history", return_value={}), \
             patch("data.insider_signal.get_screener_data", return_value=snap):
            result = get_promoter_signal("RELIANCE")
        assert result["signal"] == "NEUTRAL"
        assert result["current_holding"] == 52.5
        assert result["source"] == "screener_snapshot"

    def test_history_raises_falls_back_to_snapshot(self):
        """If screener_history raises, fallback to snapshot."""
        snap = {"promoter_holding": 48.0}
        with patch("data.insider_signal.get_screener_history",
                   side_effect=Exception("blocked")), \
             patch("data.insider_signal.get_screener_data", return_value=snap):
            result = get_promoter_signal("RELIANCE")
        assert result["signal"] == "NEUTRAL"    # snapshot only → no trend
        assert result["source"] == "screener_snapshot"


# ──────────────────────────────────────────────────────────────────────────────
# Integration: get_promoter_signal via screener snapshot (no history)
# ──────────────────────────────────────────────────────────────────────────────

class TestGetPromoterSignalSnapshot:
    def test_snapshot_returns_neutral_with_holding(self):
        snap = {"promoter_holding": 55.3, "pe": 18.0}
        with patch("data.insider_signal.get_screener_history", return_value={}), \
             patch("data.insider_signal.get_screener_data", return_value=snap):
            result = get_promoter_signal("TCS")
        assert result["signal"] == "NEUTRAL"
        assert result["current_holding"] == 55.3
        assert result["source"] == "screener_snapshot"
        assert result["change_1y"] is None

    def test_no_promoter_in_snapshot_falls_through(self):
        """Snapshot with no promoter_holding → falls through to trendlyne / none."""
        snap = {"pe": 18.0, "promoter_holding": None}
        with patch("data.insider_signal.get_screener_history", return_value={}), \
             patch("data.insider_signal.get_screener_data", return_value=snap), \
             patch.dict(os.environ, {"TRENDLYNE_SESSION": ""}, clear=False):
            result = get_promoter_signal("TCS")
        # No trendlyne session → should return default NEUTRAL
        assert result["signal"] == "NEUTRAL"
        assert result["source"] in ("none", "trendlyne_snapshot", "screener_snapshot")


# ──────────────────────────────────────────────────────────────────────────────
# Integration: all sources unavailable
# ──────────────────────────────────────────────────────────────────────────────

class TestGetPromoterSignalNoData:
    def test_all_sources_fail_returns_neutral(self):
        """If all data sources fail, return NEUTRAL gracefully."""
        with patch("data.insider_signal.get_screener_history",
                   side_effect=Exception("network")), \
             patch("data.insider_signal.get_screener_data",
                   side_effect=Exception("network")), \
             patch.dict(os.environ, {}, clear=False):
            result = get_promoter_signal("UNKNOWN")
        assert result["signal"] == "NEUTRAL"
        assert result["source"] == "none"
        assert result["current_holding"] is None
        assert isinstance(result["note"], str)

    def test_result_always_has_required_keys(self):
        """Output dict must always have all required keys regardless of data availability."""
        required = {"signal", "current_holding", "change_1y", "change_3y", "source", "note"}
        with patch("data.insider_signal.get_screener_history",
                   side_effect=Exception("network")), \
             patch("data.insider_signal.get_screener_data",
                   side_effect=Exception("network")):
            result = get_promoter_signal("ANY")
        assert required.issubset(result.keys())

    def test_symbol_cleaned_before_lookup(self):
        """Symbols with .NS suffix should still resolve correctly."""
        history = {"promoter_holding": [50.0, 51.5]}
        with patch("data.insider_signal.get_screener_history", return_value=history) as mock_h, \
             patch("data.insider_signal.get_screener_data", return_value=None):
            get_promoter_signal("RELIANCE.NS")
        # Should have been called with "RELIANCE" (cleaned)
        mock_h.assert_called_once_with("RELIANCE")


# ──────────────────────────────────────────────────────────────────────────────
# Trendlyne snapshot fallback
# ──────────────────────────────────────────────────────────────────────────────

class TestGetPromoterSignalTrendlyne:
    def test_trendlyne_snapshot_when_no_session(self):
        """Without TRENDLYNE_SESSION, trendlyne fallback is skipped → source=none."""
        with patch("data.insider_signal.get_screener_history", return_value={}), \
             patch("data.insider_signal.get_screener_data", return_value={"promoter_holding": None}), \
             patch.dict(os.environ, {k: v for k, v in os.environ.items()
                                     if k != "TRENDLYNE_SESSION"}, clear=True):
            result = get_promoter_signal("TCS")
        assert result["source"] in ("none", "screener_snapshot")

    def test_trendlyne_snapshot_used_when_screener_fails(self):
        """When screener returns None promoter, Trendlyne snapshot is tried."""
        tl_data = {"promoter_holding": 60.5, "pe": 22.0, "roce": 18.0,
                   "roe": 15.0, "revenue_growth": 12.0, "data_source": "trendlyne_fallback"}
        with patch("data.insider_signal.get_screener_history", return_value={}), \
             patch("data.insider_signal.get_screener_data", return_value=None), \
             patch.dict(os.environ, {"TRENDLYNE_SESSION": "test-session"}), \
             patch("data.trendlyne_fetcher.get_trendlyne_fundamentals", return_value=tl_data):
            result = get_promoter_signal("TCS")
        assert result["signal"] == "NEUTRAL"   # snapshot only — no trend
        assert result["current_holding"] == 60.5
        assert result["source"] == "trendlyne_snapshot"
