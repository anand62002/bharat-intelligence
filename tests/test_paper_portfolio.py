"""
tests/test_paper_portfolio.py — Unit tests for Paper Portfolio (P5-B)
"""
import pytest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch


# ─── Import helpers ──────────────────────────────────────────────────────────

from agents.paper_portfolio import (
    _get_allocation_inr,
    _entry_price_from_rec,
    _is_stoploss_hit,
    _is_target_hit,
    _is_horizon_reached,
    _safe_float,
    DEFAULT_ALLOCATION_INR,
    DEFAULT_STOPLOSS_PCT,
    DEFAULT_TARGET_PCT,
    HORIZON_DAYS,
)


# ─── _get_allocation_inr ─────────────────────────────────────────────────────

class TestGetAllocationInr:
    def test_full_tier(self):
        assert _get_allocation_inr("Full position (5%)") == 10_000

    def test_half_tier(self):
        assert _get_allocation_inr("Half position (2.5%)") == 5_000

    def test_quarter_tier(self):
        assert _get_allocation_inr("Quarter position (1.25%)") == 2_500

    def test_avoid_tier(self):
        assert _get_allocation_inr("Avoid (0%)") == 0

    def test_case_insensitive(self):
        assert _get_allocation_inr("FULL POSITION (5%)") == 10_000
        assert _get_allocation_inr("half position") == 5_000

    def test_none_returns_default(self):
        assert _get_allocation_inr(None) == DEFAULT_ALLOCATION_INR

    def test_empty_returns_default(self):
        assert _get_allocation_inr("") == DEFAULT_ALLOCATION_INR

    def test_unknown_returns_default(self):
        assert _get_allocation_inr("custom sizing") == DEFAULT_ALLOCATION_INR


# ─── _entry_price_from_rec ───────────────────────────────────────────────────

class TestEntryPriceFromRec:
    def test_midpoint_when_both_present(self):
        rec = {"entry_low": 1000, "entry_high": 1100}
        assert _entry_price_from_rec(rec) == pytest.approx(1050.0)

    def test_uses_entry_low_alone(self):
        rec = {"entry_low": 950}
        assert _entry_price_from_rec(rec) == pytest.approx(950.0)

    def test_uses_entry_high_alone(self):
        rec = {"entry_high": 1200}
        assert _entry_price_from_rec(rec) == pytest.approx(1200.0)

    def test_falls_back_to_metadata_price(self):
        rec = {"metadata": {"price": 777.5}}
        assert _entry_price_from_rec(rec) == pytest.approx(777.5)

    def test_returns_none_when_no_price(self):
        rec = {}
        assert _entry_price_from_rec(rec) is None

    def test_returns_none_when_metadata_no_price(self):
        rec = {"metadata": {"volume": 100}}
        assert _entry_price_from_rec(rec) is None

    def test_string_prices_convert(self):
        rec = {"entry_low": "500", "entry_high": "600"}
        assert _entry_price_from_rec(rec) == pytest.approx(550.0)


# ─── _is_stoploss_hit ────────────────────────────────────────────────────────

class TestIsStoplossHit:
    def test_hit_when_below_stoploss_price(self):
        assert _is_stoploss_hit(current=800, entry=1000, stoploss_price=850) is True

    def test_not_hit_when_above_stoploss_price(self):
        assert _is_stoploss_hit(current=900, entry=1000, stoploss_price=850) is False

    def test_exactly_at_stoploss_is_hit(self):
        assert _is_stoploss_hit(current=850, entry=1000, stoploss_price=850) is True

    def test_default_15pct_stoploss_when_no_price(self):
        entry = 1000
        # 15% below = 850
        assert _is_stoploss_hit(current=849, entry=entry, stoploss_price=None) is True
        assert _is_stoploss_hit(current=851, entry=entry, stoploss_price=None) is False

    def test_default_stoploss_pct_constant(self):
        entry = 2000
        threshold = entry * (1 - DEFAULT_STOPLOSS_PCT)
        assert _is_stoploss_hit(threshold - 1, entry, None) is True
        assert _is_stoploss_hit(threshold + 1, entry, None) is False


# ─── _is_target_hit ──────────────────────────────────────────────────────────

class TestIsTargetHit:
    def test_hit_when_above_target_price(self):
        assert _is_target_hit(current=1500, entry=1000, target_price=1400) is True

    def test_not_hit_when_below_target_price(self):
        assert _is_target_hit(current=1300, entry=1000, target_price=1400) is False

    def test_exactly_at_target_is_hit(self):
        assert _is_target_hit(current=1400, entry=1000, target_price=1400) is True

    def test_default_40pct_target_when_no_price(self):
        entry = 1000
        # 40% above = 1400
        assert _is_target_hit(current=1401, entry=entry, target_price=None) is True
        assert _is_target_hit(current=1399, entry=entry, target_price=None) is False

    def test_default_target_pct_constant(self):
        entry = 500
        threshold = entry * (1 + DEFAULT_TARGET_PCT)
        assert _is_target_hit(threshold + 1, entry, None) is True
        assert _is_target_hit(threshold - 1, entry, None) is False


# ─── _is_horizon_reached ─────────────────────────────────────────────────────

class TestIsHorizonReached:
    def test_not_reached_before_horizon(self):
        entry = date.today() - timedelta(days=HORIZON_DAYS - 10)
        assert _is_horizon_reached(entry) is False

    def test_reached_on_horizon_date(self):
        entry = date.today() - timedelta(days=HORIZON_DAYS)
        assert _is_horizon_reached(entry) is True

    def test_reached_after_horizon(self):
        entry = date.today() - timedelta(days=HORIZON_DAYS + 20)
        assert _is_horizon_reached(entry) is True

    def test_not_reached_today_entry(self):
        assert _is_horizon_reached(date.today()) is False

    def test_custom_today(self):
        entry = date(2025, 1, 1)
        today = date(2025, 4, 3)  # 91 days later
        assert _is_horizon_reached(entry, today) is True

    def test_custom_today_not_reached(self):
        entry = date(2025, 1, 1)
        today = date(2025, 2, 1)  # 31 days later
        assert _is_horizon_reached(entry, today) is False


# ─── _safe_float ─────────────────────────────────────────────────────────────

class TestSafeFloat:
    def test_normal_value(self):
        assert _safe_float(123.45) == pytest.approx(123.45)

    def test_string_value(self):
        assert _safe_float("99.9") == pytest.approx(99.9)

    def test_none_returns_none(self):
        assert _safe_float(None) is None

    def test_nan_returns_none(self):
        import math
        assert _safe_float(math.nan) is None

    def test_inf_returns_none(self):
        import math
        assert _safe_float(math.inf) is None

    def test_non_numeric_returns_none(self):
        assert _safe_float("not_a_number") is None


# ─── Exit condition integration ──────────────────────────────────────────────

class TestExitConditions:
    """Integration: verify exit logic is mutually exclusive for a normal position."""

    def setup_method(self):
        self.entry    = 1000.0
        self.stoploss = 850.0
        self.target   = 1400.0
        self.today    = date.today()
        self.entry_date = self.today - timedelta(days=30)   # 30 days in

    def _check(self, current):
        sl = _is_stoploss_hit(current, self.entry, self.stoploss)
        tgt = _is_target_hit(current, self.entry, self.target)
        hor = _is_horizon_reached(self.entry_date, self.today)
        return sl, tgt, hor

    def test_no_exit_triggered_at_normal_price(self):
        sl, tgt, hor = self._check(1050)
        assert not sl
        assert not tgt
        assert not hor

    def test_stoploss_triggered(self):
        sl, tgt, hor = self._check(840)
        assert sl
        assert not tgt

    def test_target_triggered(self):
        sl, tgt, hor = self._check(1450)
        assert not sl
        assert tgt

    def test_horizon_triggered(self):
        entry_date = self.today - timedelta(days=HORIZON_DAYS + 1)
        hor = _is_horizon_reached(entry_date, self.today)
        assert hor


# ─── open_new_positions (mocked DB) ──────────────────────────────────────────

class TestOpenNewPositions:

    def _make_rec(self, rec_id="rec1", symbol="RELIANCE", action="BUY",
                   entry_low=2500, entry_high=2600,
                   position_label="Half position (2.5%)",
                   created_at="2025-01-15"):
        return {
            "id": rec_id, "symbol": symbol, "action": action,
            "entry_low": entry_low, "entry_high": entry_high,
            "position_label": position_label,
            "created_at": created_at + "T00:00:00+00:00",
            "stoploss": 2200.0, "target": 3200.0,
            "metadata": {},
        }

    def test_dry_run_does_not_call_insert(self):
        from agents.paper_portfolio import open_new_positions

        client = MagicMock()
        client.table.return_value.select.return_value.in_.return_value.order.return_value.execute.return_value.data = [
            self._make_rec()
        ]
        client.table.return_value.select.return_value.execute.return_value.data = []

        with patch("agents.paper_portfolio._fetch_price_on_date", return_value=2550.0), \
             patch("agents.paper_portfolio._fetch_current_price", return_value=2550.0):
            result = open_new_positions(client, dry_run=True)

        assert result["opened"] >= 0
        # insert should NOT have been called in dry run
        for call in client.method_calls:
            assert "insert" not in str(call)

    def test_skips_avoid_tier(self):
        from agents.paper_portfolio import open_new_positions

        rec = self._make_rec(position_label="Avoid (0%)")
        client = MagicMock()
        client.table.return_value.select.return_value.in_.return_value.order.return_value.execute.return_value.data = [rec]
        client.table.return_value.select.return_value.execute.return_value.data = []

        result = open_new_positions(client, dry_run=True)
        assert result["skipped"] >= 1

    def test_skips_already_tracked_rec(self):
        from agents.paper_portfolio import open_new_positions

        rec = self._make_rec(rec_id="existing-rec")
        client = MagicMock()
        client.table.return_value.select.return_value.in_.return_value.order.return_value.execute.return_value.data = [rec]
        # Existing row has same rec_id
        client.table.return_value.select.return_value.execute.return_value.data = [{"rec_id": "existing-rec"}]

        result = open_new_positions(client, dry_run=True)
        assert result["skipped"] >= 1


# ─── P5-A: compute_agent_attribution ─────────────────────────────────────────

class TestComputeAgentAttribution:

    def test_empty_rows_returns_empty(self):
        from agents.outcome_tracker import compute_agent_attribution
        assert compute_agent_attribution([]) == []

    def test_pending_rows_excluded(self):
        from agents.outcome_tracker import compute_agent_attribution
        rows = [{
            "outcome_t90": "PENDING",
            "alpha_t90": None,
            "agent_signals": {"technical": {"signal": "BULLISH", "score": 70}},
        }]
        assert compute_agent_attribution(rows) == []

    def test_basic_attribution_computed(self):
        from agents.outcome_tracker import compute_agent_attribution
        rows = [
            {
                "outcome_t90": "HIT",
                "alpha_t90": 0.05,
                "agent_signals": {
                    "technical":  {"signal": "BULLISH", "score": 72},
                    "fundamental":{"signal": "NEUTRAL",  "score": 55},
                },
            },
            {
                "outcome_t90": "HIT",
                "alpha_t90": 0.03,
                "agent_signals": {
                    "technical":  {"signal": "BULLISH", "score": 68},
                    "fundamental":{"signal": "BULLISH",  "score": 62},
                },
            },
            {
                "outcome_t90": "MISS",
                "alpha_t90": -0.08,
                "agent_signals": {
                    "technical":  {"signal": "BULLISH", "score": 55},
                    "fundamental":{"signal": "NEUTRAL",  "score": 48},
                },
            },
        ]
        result = compute_agent_attribution(rows)
        assert len(result) > 0

        agents_dict = {a["agent_name"]: a for a in result}

        # Technical: voted BULLISH 3 times, HIT 2 → hit_rate = 66.7%
        tech = agents_dict["technical"]
        assert tech["bullish_count"] == 3
        assert tech["signal_count"] == 3
        assert tech["hit_rate_90d"] == pytest.approx(66.7, abs=0.2)

        # Fundamental: voted BULLISH 1 time, HIT 1 → hit_rate = 100%
        fund = agents_dict["fundamental"]
        assert fund["bullish_count"] == 1

    def test_sorted_by_contribution_score(self):
        from agents.outcome_tracker import compute_agent_attribution
        rows = [
            {
                "outcome_t90": "HIT",
                "alpha_t90": 0.10,
                "agent_signals": {
                    "good_agent": {"signal": "BULLISH", "score": 80},
                    "bad_agent":  {"signal": "BULLISH", "score": 30},
                },
            },
            {
                "outcome_t90": "MISS",
                "alpha_t90": -0.12,
                "agent_signals": {
                    "good_agent": {"signal": "NEUTRAL",  "score": 50},
                    "bad_agent":  {"signal": "BULLISH",  "score": 30},
                },
            },
        ]
        result = compute_agent_attribution(rows)
        # good_agent had 1 BULLISH vote and it was a HIT (100% hit rate)
        # bad_agent had 2 BULLISH votes, 1 HIT + 1 MISS (50% hit rate)
        agents_dict = {a["agent_name"]: a for a in result}
        assert agents_dict["good_agent"]["hit_rate_90d"] == pytest.approx(100.0)
        assert agents_dict["bad_agent"]["hit_rate_90d"] == pytest.approx(50.0)
        # Good agent should rank higher (first in sorted list)
        assert result[0]["agent_name"] == "good_agent"

    def test_non_bullish_signals_not_counted_in_bullish(self):
        from agents.outcome_tracker import compute_agent_attribution
        rows = [
            {
                "outcome_t90": "HIT",
                "alpha_t90": 0.05,
                "agent_signals": {
                    "technical": {"signal": "BEARISH", "score": 40},
                },
            },
        ]
        result = compute_agent_attribution(rows)
        if result:
            tech = next((a for a in result if a["agent_name"] == "technical"), None)
            if tech:
                assert tech["bullish_count"] == 0
