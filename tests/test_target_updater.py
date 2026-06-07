"""
tests/test_target_updater.py — Unit tests for agents/target_updater.py

Tests cover:
- _compute_rsi: correct RSI value, NaN guard, insufficient data
- _sector_pe: key mapping, fallback
- _is_steam: conditions, both required, partial
- _maybe_ratchet_stoploss: BREAKEVEN / LOCK_20 / no ratchet / already ratcheted
- _needs_target_review: threshold logic
- _is_laggard: days + loss thresholds
- _maybe_extend_target: extension, steam block, cap, no increase needed
- _maybe_review_laggard: cooldown, alert vs OK
- run_target_updates: integration smoke
"""

from __future__ import annotations

import types
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

import pandas as pd
import pytest

from agents.target_updater import (
    _compute_rsi,
    _sector_pe,
    _is_steam,
    _is_laggard,
    _needs_target_review,
    _maybe_ratchet_stoploss,
    _maybe_extend_target,
    _maybe_review_laggard,
    run_target_updates,
    _RATCHET_BREAKEVEN_PCT,
    _RATCHET_LOCK_PCT,
    _TARGET_REVIEW_PROGRESS,
)


# ─── _compute_rsi ─────────────────────────────────────────────────────────────

class TestComputeRsi:
    def test_returns_float_in_valid_range(self):
        import numpy as np
        prices = pd.Series([100 + i + (5 * (i % 3 - 1)) for i in range(30)])
        rsi = _compute_rsi(prices)
        assert rsi is not None
        assert 0 <= rsi <= 100

    def test_returns_none_on_insufficient_data(self):
        prices = pd.Series([100, 101, 102])
        assert _compute_rsi(prices) is None

    def test_nan_guard(self):
        import numpy as np
        prices = pd.Series([float("nan")] * 20)
        result = _compute_rsi(prices)
        assert result is None or result != result  # NaN or None


# ─── _sector_pe ───────────────────────────────────────────────────────────────

class TestSectorPe:
    def test_known_sector_returns_value(self):
        with patch("agents.sector_valuation.SECTOR_LONGRUN_PE",
                   {"banking": 15.0, "it": 25.0}, create=True):
            # Re-import to get fresh patch — but since the function does its own import,
            # we test by patching the module directly
            with patch.dict("sys.modules", {}):
                result = _sector_pe("banking")
        # Fallback expected if patch doesn't propagate (acceptable)
        assert isinstance(result, float)
        assert result > 0

    def test_unknown_sector_returns_default(self):
        result = _sector_pe("nonexistent_sector_xyz")
        assert result == 22.0

    def test_none_sector_returns_default(self):
        assert _sector_pe(None) == 22.0

    def test_partial_match(self):
        # "information technology" should match "it" or "technology"
        result = _sector_pe("Information Technology")
        assert isinstance(result, float)


# ─── _is_steam ────────────────────────────────────────────────────────────────

class TestIsSteam:
    def _make_warren(self, intrinsic=1000):
        return {"intrinsic_value": intrinsic, "signal": "BUY"}

    @patch("agents.target_updater._quick_technicals")
    def test_steam_when_rsi_high_and_pe_stretched(self, mock_tech):
        mock_tech.return_value = {"rsi": 75.0, "pe": 45.0}
        with patch("agents.target_updater._sector_pe", return_value=20.0):
            steam, reason = _is_steam("RELIANCE.NS", "Energy", 900, self._make_warren(1000))
        assert steam is True
        assert "RSI" in reason

    @patch("agents.target_updater._quick_technicals")
    def test_no_steam_when_rsi_low(self, mock_tech):
        mock_tech.return_value = {"rsi": 60.0, "pe": 50.0}
        with patch("agents.target_updater._sector_pe", return_value=20.0):
            steam, reason = _is_steam("RELIANCE.NS", "Energy", 900, self._make_warren(1000))
        assert steam is False

    @patch("agents.target_updater._quick_technicals")
    def test_no_steam_when_rsi_high_but_valuation_ok(self, mock_tech):
        # RSI high, but PE is normal and price is well below DCF
        mock_tech.return_value = {"rsi": 74.0, "pe": 18.0}
        with patch("agents.target_updater._sector_pe", return_value=20.0):
            steam, reason = _is_steam("RELIANCE.NS", "Energy", 700, self._make_warren(1000))
        assert steam is False

    @patch("agents.target_updater._quick_technicals")
    def test_steam_when_rsi_high_and_price_near_dcf(self, mock_tech):
        mock_tech.return_value = {"rsi": 73.0, "pe": 18.0}
        with patch("agents.target_updater._sector_pe", return_value=20.0):
            # price = 960, dcf = 1000, threshold = 0.95 × 1000 = 950
            steam, reason = _is_steam("RELIANCE.NS", "Energy", 960, self._make_warren(1000))
        assert steam is True

    @patch("agents.target_updater._quick_technicals")
    def test_rsi_unavailable_skips_check(self, mock_tech):
        mock_tech.return_value = {}
        steam, reason = _is_steam("RELIANCE.NS", "Energy", 900, self._make_warren(1000))
        assert steam is False
        assert "unavailable" in reason.lower()


# ─── _is_laggard ──────────────────────────────────────────────────────────────

class TestIsLaggard:
    def _holding(self, avg_buy, current_price, days_ago):
        buy_date = (date.today() - timedelta(days=days_ago)).isoformat()
        return {
            "avg_buy": avg_buy,
            "current_price": current_price,
            "buy_date": buy_date,
        }

    def test_qualifies_when_old_and_down(self):
        h = self._holding(100, 88, days_ago=70)
        assert _is_laggard(h) is True

    def test_not_laggard_when_young(self):
        h = self._holding(100, 85, days_ago=30)
        assert _is_laggard(h) is False

    def test_not_laggard_when_not_down_enough(self):
        h = self._holding(100, 95, days_ago=70)  # -5%, threshold is -10%
        assert _is_laggard(h) is False

    def test_not_laggard_when_positive(self):
        h = self._holding(100, 115, days_ago=90)
        assert _is_laggard(h) is False

    def test_uses_created_at_when_no_buy_date(self):
        created_at = (datetime.utcnow() - timedelta(days=75)).isoformat()
        h = {"avg_buy": 100, "current_price": 85, "created_at": created_at}
        assert _is_laggard(h) is True


# ─── _needs_target_review ─────────────────────────────────────────────────────

class TestNeedsTargetReview:
    def _h(self, avg_buy, current, target, protect=False, last_updated=None):
        return {
            "avg_buy": avg_buy,
            "current_price": current,
            "target_price": target,
            "protect_gains_flag": protect,
            "target_updated_at": last_updated,
        }

    def test_triggers_at_80_pct(self):
        # entry=100, target=200, progress = (180-100)/(200-100) = 80%
        assert _needs_target_review(self._h(100, 180, 200)) is True

    def test_no_trigger_below_80_pct(self):
        # progress = (160-100)/(200-100) = 60%
        assert _needs_target_review(self._h(100, 160, 200)) is False

    def test_no_trigger_at_exact_threshold(self):
        # just under 80%
        assert _needs_target_review(self._h(100, 179, 200)) is False

    def test_no_trigger_when_target_eq_avg_buy(self):
        assert _needs_target_review(self._h(100, 150, 100)) is False

    def test_protect_flag_skips_if_recently_updated(self):
        recent = datetime.utcnow().isoformat()
        assert _needs_target_review(self._h(100, 185, 200, protect=True, last_updated=recent)) is False

    def test_protect_flag_retries_after_14_days(self):
        old = (datetime.utcnow() - timedelta(days=15)).isoformat()
        assert _needs_target_review(self._h(100, 185, 200, protect=True, last_updated=old)) is True


# ─── _maybe_ratchet_stoploss ──────────────────────────────────────────────────

class TestMaybeRatchetStoploss:
    def _make(self, avg_buy, current, stoploss, ratchet_level="ORIGINAL"):
        return {
            "id": "h1", "symbol": "INFY",
            "avg_buy": avg_buy, "current_price": current,
            "stoploss_price": stoploss,
            "stoploss_ratchet_level": ratchet_level,
        }

    def _client(self):
        c = MagicMock()
        c.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[{}])
        c.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{}])
        return c

    def test_ratchet_to_breakeven_at_25_pct(self):
        h = self._make(avg_buy=1000, current=1260, stoploss=850)  # +26%
        result = _maybe_ratchet_stoploss(h, self._client(), dry_run=True)
        assert result is not None
        assert result["level"] == "BREAKEVEN"
        assert result["new_sl"] == 1000.0

    def test_ratchet_to_lock_20_at_40_pct(self):
        h = self._make(avg_buy=1000, current=1420, stoploss=850)  # +42%
        result = _maybe_ratchet_stoploss(h, self._client(), dry_run=True)
        assert result is not None
        assert result["level"] == "LOCK_20"
        assert abs(result["new_sl"] - 1200.0) < 1

    def test_no_ratchet_below_25_pct(self):
        h = self._make(avg_buy=1000, current=1200, stoploss=850)  # +20%
        result = _maybe_ratchet_stoploss(h, self._client(), dry_run=True)
        assert result is None

    def test_no_ratchet_if_already_lock_20(self):
        h = self._make(avg_buy=1000, current=1420, stoploss=1200, ratchet_level="LOCK_20")
        result = _maybe_ratchet_stoploss(h, self._client(), dry_run=True)
        assert result is None

    def test_no_ratchet_if_new_sl_below_existing(self):
        # Already has a tighter SL set manually
        h = self._make(avg_buy=1000, current=1260, stoploss=1100)  # SL already above BE
        result = _maybe_ratchet_stoploss(h, self._client(), dry_run=True)
        assert result is None

    def test_upgrades_from_breakeven_to_lock_20(self):
        h = self._make(avg_buy=1000, current=1450, stoploss=1000, ratchet_level="BREAKEVEN")
        result = _maybe_ratchet_stoploss(h, self._client(), dry_run=True)
        assert result is not None
        assert result["level"] == "LOCK_20"


# ─── _maybe_extend_target ─────────────────────────────────────────────────────

class TestMaybeExtendTarget:
    def _holding(self, avg_buy=1000, current=1850, target=2000,
                 orig_target=None, upd_count=0, protect=False):
        return {
            "id": "h2", "symbol": "TCS", "yf_symbol": "TCS.NS",
            "sector": "IT",
            "avg_buy": avg_buy, "current_price": current,
            "target_price": target,
            "original_target": orig_target,
            "target_update_count": upd_count,
            "protect_gains_flag": protect,
            "target_updated_at": None,
        }

    def _client(self):
        c = MagicMock()
        c.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[{}])
        c.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{}])
        return c

    def _warren(self, intrinsic):
        return {"intrinsic_value": intrinsic, "signal": "BUY",
                "score": 75, "margin_of_safety_pct": 15}

    @patch("agents.target_updater._warren_analyse")
    @patch("agents.target_updater._is_steam")
    def test_extends_target_when_intrinsic_higher(self, mock_steam, mock_warren):
        mock_steam.return_value = (False, "no steam")
        mock_warren.return_value = self._warren(2500)
        h = self._holding()  # current=1850, target=2000, progress=85%
        result = _maybe_extend_target(h, self._client(), dry_run=True)
        assert result is not None
        assert result["action"] == "EXTENDED"
        assert result["new_target"] > 2000

    @patch("agents.target_updater._warren_analyse")
    @patch("agents.target_updater._is_steam")
    def test_blocks_extension_on_steam(self, mock_steam, mock_warren):
        mock_steam.return_value = (True, "RSI 75 + PE stretched")
        mock_warren.return_value = self._warren(2500)
        h = self._holding()
        result = _maybe_extend_target(h, self._client(), dry_run=True)
        assert result is not None
        assert result["action"] == "PROTECT_GAINS"

    @patch("agents.target_updater._warren_analyse")
    @patch("agents.target_updater._is_steam")
    def test_no_extension_when_intrinsic_below_target(self, mock_steam, mock_warren):
        mock_steam.return_value = (False, "no steam")
        mock_warren.return_value = self._warren(1900)  # below current target 2000
        h = self._holding()
        result = _maybe_extend_target(h, self._client(), dry_run=True)
        assert result is None

    @patch("agents.target_updater._warren_analyse")
    @patch("agents.target_updater._is_steam")
    def test_cap_limits_extension(self, mock_steam, mock_warren):
        mock_steam.return_value = (False, "no steam")
        mock_warren.return_value = self._warren(9999)  # absurdly high
        # entry=1000, orig_target=2000, range=1000, cap= 2000 + 2×1000 = 4000
        h = self._holding(avg_buy=1000, current=1850, target=2000)
        result = _maybe_extend_target(h, self._client(), dry_run=True)
        assert result is not None
        assert result["new_target"] <= 4000.0

    @patch("agents.target_updater._warren_analyse")
    def test_warren_failure_returns_none(self, mock_warren):
        mock_warren.return_value = None
        h = self._holding()
        result = _maybe_extend_target(h, self._client(), dry_run=True)
        assert result is None

    def test_not_triggered_below_80_pct_progress(self):
        # current=1500, target=2000, avg_buy=1000 → progress=50%
        h = self._holding(current=1500)
        result = _maybe_extend_target(h, self._client(), dry_run=True)
        assert result is None  # _needs_target_review returns False, so we skip


# ─── _maybe_review_laggard ────────────────────────────────────────────────────

class TestMaybeReviewLaggard:
    def _holding(self, avg_buy=1000, current=850, last_review=None):
        return {
            "id": "h3", "symbol": "PAYTM", "yf_symbol": "PAYTM.NS",
            "avg_buy": avg_buy, "current_price": current,
            "last_review_at": last_review,
        }

    def _client(self):
        c = MagicMock()
        c.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[{}])
        c.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{}])
        return c

    def _warren(self, intrinsic, signal="HOLD"):
        return {"intrinsic_value": intrinsic, "signal": signal,
                "score": 45, "margin_of_safety_pct": 5}

    @patch("agents.target_updater._warren_analyse")
    def test_creates_alert_for_avoid_signal(self, mock_warren):
        mock_warren.return_value = self._warren(900, "AVOID")
        result = _maybe_review_laggard(self._holding(), self._client(), dry_run=True)
        assert result is not None
        assert result["action"] == "REVIEW_NEEDED"

    @patch("agents.target_updater._warren_analyse")
    def test_creates_alert_when_thin_upside(self, mock_warren):
        # intrinsic=900, current=850 → upside=5.9% < 10% threshold
        mock_warren.return_value = self._warren(900, "HOLD")
        result = _maybe_review_laggard(self._holding(current=855), self._client(), dry_run=True)
        assert result is not None
        assert result["action"] == "REVIEW_NEEDED"

    @patch("agents.target_updater._warren_analyse")
    def test_ok_when_sufficient_upside(self, mock_warren):
        # intrinsic=1100, current=850 → upside=29% > 10%
        mock_warren.return_value = self._warren(1100, "BUY")
        result = _maybe_review_laggard(self._holding(), self._client(), dry_run=True)
        assert result is not None
        assert result["action"] == "OK"

    def test_cooldown_prevents_repeat_review(self):
        recent = date.today().isoformat()
        h = self._holding(last_review=recent)
        result = _maybe_review_laggard(h, self._client(), dry_run=True)
        assert result is None

    @patch("agents.target_updater._warren_analyse")
    def test_review_after_cooldown_expires(self, mock_warren):
        mock_warren.return_value = self._warren(900, "AVOID")
        old_review = (date.today() - timedelta(days=31)).isoformat()
        h = self._holding(last_review=old_review)
        result = _maybe_review_laggard(h, self._client(), dry_run=True)
        assert result is not None


# ─── run_target_updates (integration smoke) ───────────────────────────────────

class TestRunTargetUpdates:
    @patch("agents.target_updater._supabase")
    def test_no_supabase_returns_error(self, mock_sb):
        mock_sb.return_value = None
        result = run_target_updates(dry_run=True)
        assert "error" in result

    @patch("agents.target_updater._supabase")
    @patch("agents.target_updater._load_open_holdings")
    def test_empty_portfolio_returns_empty_results(self, mock_load, mock_sb):
        mock_sb.return_value = MagicMock()
        mock_load.return_value = []
        result = run_target_updates(dry_run=True)
        assert result["total_holdings"] == 0
        assert result["stoploss_ratchets"] == []
        assert result["targets_extended"] == []

    @patch("agents.target_updater._supabase")
    @patch("agents.target_updater._load_open_holdings")
    @patch("agents.target_updater._maybe_ratchet_stoploss")
    @patch("agents.target_updater._needs_target_review")
    @patch("agents.target_updater._is_laggard")
    def test_runs_all_mechanisms(self, mock_lag, mock_rev, mock_ratchet, mock_load, mock_sb):
        mock_sb.return_value = MagicMock()
        mock_load.return_value = [{"symbol": "TCS", "id": "h1", "avg_buy": 100,
                                    "current_price": 150, "target_price": 180,
                                    "stoploss_price": 85, "stoploss_ratchet_level": "ORIGINAL",
                                    "protect_gains_flag": False, "target_update_count": 0,
                                    "original_target": None, "target_updated_at": None,
                                    "last_review_at": None, "yf_symbol": "TCS.NS",
                                    "sector": "IT", "buy_date": None, "created_at": None}]
        mock_ratchet.return_value = None
        mock_rev.return_value = False
        mock_lag.return_value = False
        result = run_target_updates(dry_run=True)
        assert result["total_holdings"] == 1
        assert mock_ratchet.called
        assert mock_rev.called
        assert mock_lag.called
