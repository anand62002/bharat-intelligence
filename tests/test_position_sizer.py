"""
tests/test_position_sizer.py — Unit tests for P3-A Position Sizing

Tests cover:
  - calc_position_size() pure function — all tier boundaries
  - AVOID action hard override
  - Warren-bot MOS vs upside_pct proxy routing
  - FULL tier requires warren data (proxy cannot qualify)
  - Output schema completeness
"""
from __future__ import annotations

import pytest

from agents.position_sizer import (
    calc_position_size,
    POSITION_TIERS,
    FULL_MOS_MIN, FULL_WARREN_MIN, FULL_CONF_MIN,
    HALF_MOS_MIN, HALF_CONF_MIN,
    QUARTER_MOS_MIN, QUARTER_CONF_MIN,
)


# ═════════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════════

def _buy(upside=30, confidence=70, mos=None, warren=None):
    return calc_position_size(upside, confidence, "BUY", mos_pct=mos, warren_score=warren)


# ═════════════════════════════════════════════════════════════════════════════
# Output schema
# ═════════════════════════════════════════════════════════════════════════════

class TestOutputSchema:

    REQUIRED_KEYS = {
        "suggested_position_pct", "position_label",
        "position_tier", "sizing_rationale",
        "mos_used", "mos_source",
    }

    def test_all_keys_present_full_tier(self):
        result = _buy(upside=50, confidence=80, mos=45, warren=75)
        assert self.REQUIRED_KEYS.issubset(result.keys())

    def test_all_keys_present_avoid_tier(self):
        result = _buy(upside=-5, confidence=30)
        assert self.REQUIRED_KEYS.issubset(result.keys())

    def test_suggested_position_pct_is_float(self):
        result = _buy()
        assert isinstance(result["suggested_position_pct"], float)

    def test_tier_is_valid_string(self):
        result = _buy()
        assert result["position_tier"] in POSITION_TIERS

    def test_pct_matches_tier(self):
        result = _buy(upside=50, confidence=80, mos=45, warren=75)
        assert result["suggested_position_pct"] == POSITION_TIERS[result["position_tier"]]

    def test_mos_source_is_warren_when_mos_provided(self):
        result = _buy(mos=30)
        assert result["mos_source"] == "warren_dcf"

    def test_mos_source_is_proxy_without_mos(self):
        result = _buy(upside=30)
        assert result["mos_source"] == "upside_proxy"

    def test_mos_used_equals_mos_pct_when_provided(self):
        result = _buy(upside=50, mos=35)
        assert result["mos_used"] == 35.0

    def test_mos_used_equals_upside_when_no_mos(self):
        result = _buy(upside=28, mos=None)
        assert result["mos_used"] == 28.0


# ═════════════════════════════════════════════════════════════════════════════
# AVOID action hard-override
# ═════════════════════════════════════════════════════════════════════════════

class TestAvoidActionOverride:

    def test_avoid_action_returns_zero(self):
        result = calc_position_size(50, 90, "AVOID", mos_pct=60, warren_score=80)
        assert result["suggested_position_pct"] == 0.0
        assert result["position_tier"] == "AVOID"

    def test_sell_action_returns_zero(self):
        result = calc_position_size(50, 90, "SELL", mos_pct=60, warren_score=80)
        assert result["suggested_position_pct"] == 0.0

    def test_strong_sell_returns_zero(self):
        result = calc_position_size(50, 90, "STRONG_SELL")
        assert result["suggested_position_pct"] == 0.0

    def test_action_case_insensitive(self):
        result = calc_position_size(50, 90, "sell")
        assert result["suggested_position_pct"] == 0.0

    def test_buy_action_not_overridden(self):
        result = calc_position_size(30, 70, "BUY")
        assert result["suggested_position_pct"] > 0.0

    def test_hold_action_can_qualify(self):
        """HOLD with good numbers should still get a size (cautious allocation)."""
        result = calc_position_size(25, 66, "HOLD")
        assert result["suggested_position_pct"] == POSITION_TIERS["HALF"]


# ═════════════════════════════════════════════════════════════════════════════
# FULL tier (5%)
# ═════════════════════════════════════════════════════════════════════════════

class TestFullTier:

    def test_full_tier_fires_when_all_conditions_met(self):
        result = _buy(
            upside=50, confidence=FULL_CONF_MIN, mos=FULL_MOS_MIN + 1,
            warren=FULL_WARREN_MIN,
        )
        assert result["position_tier"] == "FULL"
        assert result["suggested_position_pct"] == 5.0

    def test_full_tier_requires_warren_mos_not_proxy(self):
        """Even with very high upside and confidence, proxy cannot qualify for FULL."""
        result = _buy(upside=50, confidence=80, mos=None, warren=75)
        assert result["position_tier"] != "FULL"

    def test_full_tier_requires_warren_score(self):
        """Warren MOS alone — no score — cannot qualify for FULL."""
        result = _buy(upside=50, confidence=80, mos=45, warren=None)
        assert result["position_tier"] != "FULL"

    def test_full_tier_mos_boundary_exactly_at_threshold_fails(self):
        """MOS exactly at threshold (not strictly greater) → NOT FULL."""
        result = _buy(
            upside=FULL_MOS_MIN, confidence=FULL_CONF_MIN, mos=FULL_MOS_MIN,
            warren=FULL_WARREN_MIN,
        )
        assert result["position_tier"] != "FULL"

    def test_full_tier_warren_below_min_drops_to_half(self):
        result = _buy(
            upside=50, confidence=80, mos=45, warren=FULL_WARREN_MIN - 1,
        )
        assert result["position_tier"] in ("HALF", "QUARTER", "AVOID")

    def test_full_tier_conf_below_min_drops_to_half(self):
        result = _buy(
            upside=50, confidence=FULL_CONF_MIN - 1, mos=45, warren=75,
        )
        assert result["position_tier"] in ("HALF", "QUARTER", "AVOID")

    def test_full_tier_pct_is_5(self):
        result = _buy(upside=50, confidence=80, mos=45, warren=75)
        assert result["suggested_position_pct"] == 5.0


# ═════════════════════════════════════════════════════════════════════════════
# HALF tier (2.5%)
# ═════════════════════════════════════════════════════════════════════════════

class TestHalfTier:

    def test_half_tier_fires_on_warren_mos(self):
        result = _buy(upside=10, confidence=HALF_CONF_MIN, mos=HALF_MOS_MIN + 1)
        assert result["position_tier"] == "HALF"
        assert result["suggested_position_pct"] == 2.5

    def test_half_tier_fires_on_upside_proxy(self):
        result = _buy(upside=HALF_MOS_MIN + 1, confidence=HALF_CONF_MIN)
        assert result["position_tier"] == "HALF"

    def test_half_tier_mos_exactly_at_boundary_fails(self):
        result = _buy(upside=10, confidence=HALF_CONF_MIN, mos=HALF_MOS_MIN)
        assert result["position_tier"] != "HALF"

    def test_half_tier_conf_exactly_at_boundary_passes(self):
        result = _buy(upside=25, confidence=HALF_CONF_MIN, mos=25)
        assert result["position_tier"] == "HALF"

    def test_half_tier_conf_one_below_fails(self):
        result = _buy(upside=25, confidence=HALF_CONF_MIN - 1, mos=25)
        assert result["position_tier"] in ("QUARTER", "AVOID")

    def test_half_tier_pct_is_2_5(self):
        result = _buy(upside=25, confidence=67, mos=25)
        assert result["suggested_position_pct"] == 2.5


# ═════════════════════════════════════════════════════════════════════════════
# QUARTER tier (1.25%)
# ═════════════════════════════════════════════════════════════════════════════

class TestQuarterTier:

    def test_quarter_tier_fires_on_small_upside_adequate_conf(self):
        result = _buy(upside=5, confidence=QUARTER_CONF_MIN)
        assert result["position_tier"] == "QUARTER"
        assert result["suggested_position_pct"] == 1.25

    def test_quarter_tier_mos_exactly_zero_fails(self):
        result = _buy(upside=0, confidence=QUARTER_CONF_MIN)
        assert result["position_tier"] == "AVOID"

    def test_quarter_tier_tiny_positive_upside_passes(self):
        result = _buy(upside=0.01, confidence=QUARTER_CONF_MIN)
        assert result["position_tier"] == "QUARTER"

    def test_quarter_tier_conf_exactly_at_boundary_passes(self):
        result = _buy(upside=5, confidence=QUARTER_CONF_MIN)
        assert result["position_tier"] == "QUARTER"

    def test_quarter_tier_conf_one_below_avoids(self):
        result = _buy(upside=5, confidence=QUARTER_CONF_MIN - 1)
        assert result["position_tier"] == "AVOID"

    def test_quarter_tier_pct_is_1_25(self):
        result = _buy(upside=5, confidence=60)
        assert result["suggested_position_pct"] == 1.25


# ═════════════════════════════════════════════════════════════════════════════
# AVOID tier (0%)
# ═════════════════════════════════════════════════════════════════════════════

class TestAvoidTier:

    def test_avoid_on_negative_upside(self):
        result = _buy(upside=-10, confidence=70)
        assert result["position_tier"] == "AVOID"
        assert result["suggested_position_pct"] == 0.0

    def test_avoid_on_zero_upside(self):
        result = _buy(upside=0, confidence=70)
        assert result["position_tier"] == "AVOID"

    def test_avoid_on_low_confidence(self):
        result = _buy(upside=30, confidence=QUARTER_CONF_MIN - 1)
        assert result["position_tier"] == "AVOID"

    def test_avoid_pct_is_zero(self):
        result = _buy(upside=-5, confidence=30)
        assert result["suggested_position_pct"] == 0.0


# ═════════════════════════════════════════════════════════════════════════════
# Tier priority (higher tier wins over lower)
# ═════════════════════════════════════════════════════════════════════════════

class TestTierPriority:

    def test_full_beats_half_when_both_qualify(self):
        """Full conditions met → should get FULL, not HALF."""
        result = _buy(upside=50, confidence=80, mos=45, warren=75)
        assert result["position_tier"] == "FULL"

    def test_half_beats_quarter_when_both_qualify(self):
        """Half conditions met → should get HALF, not QUARTER."""
        result = _buy(upside=25, confidence=68)
        assert result["position_tier"] == "HALF"

    def test_avoid_action_overrides_full_conditions(self):
        result = calc_position_size(60, 90, "AVOID", mos_pct=60, warren_score=90)
        assert result["position_tier"] == "AVOID"


# ═════════════════════════════════════════════════════════════════════════════
# Rationale string quality
# ═════════════════════════════════════════════════════════════════════════════

class TestRationale:

    def test_rationale_is_non_empty_string(self):
        result = _buy(upside=25, confidence=70)
        assert isinstance(result["sizing_rationale"], str)
        assert len(result["sizing_rationale"]) > 10

    def test_full_rationale_mentions_warren_score(self):
        result = _buy(upside=50, confidence=80, mos=45, warren=75)
        assert "Warren" in result["sizing_rationale"] or "warren" in result["sizing_rationale"]

    def test_avoid_rationale_explains_reason(self):
        result = _buy(upside=-5, confidence=30)
        rationale = result["sizing_rationale"].lower()
        assert "confidence" in rationale or "mos" in rationale or "upside" in rationale

    def test_position_label_matches_tier(self):
        from agents.position_sizer import POSITION_LABELS
        for tier in POSITION_TIERS:
            # Manufacture conditions for each tier
            if tier == "FULL":
                result = _buy(upside=50, confidence=80, mos=45, warren=75)
            elif tier == "HALF":
                result = _buy(upside=25, confidence=68)
            elif tier == "QUARTER":
                result = _buy(upside=5, confidence=58)
            else:
                result = _buy(upside=-5, confidence=30)
            if result["position_tier"] == tier:
                assert result["position_label"] == POSITION_LABELS[tier]


# ─────────────────────────────────────────────────────────────────────────────
# WP-019 (ADR-006): size from calibrated probability, conviction locked by default
# ─────────────────────────────────────────────────────────────────────────────

from agents.call_calibration import Calibration, SIGNAL_BANDS, wilson_lower  # noqa: E402
from agents.position_sizer import size_from_probability  # noqa: E402
from data import decision_config as _dc  # noqa: E402


def _cal(band_counts):
    """Calibration from raw (hits, n) per SIGNAL_BAND."""
    bands = [{"lo": lo, "hi": hi, "n": n, "hits": h, "rate": round(100 * h / n, 1) if n else 50.0}
             for (lo, hi), (h, n) in zip(SIGNAL_BANDS, band_counts)]
    return Calibration(bands=bands, overall=46.7, n=sum(n for _, n in band_counts), has_history=True)


def test_todays_calibration_yields_base_for_every_band():
    """2026-11 discovery bands: 41.6 / 53.3 / 43.9 / 37.3 % on n = 13–38."""
    cal = _cal([(16, 38), (16, 30), (6, 13), (7, 19)])
    for score in (60, 75, 85, 95):
        sz = size_from_probability(cal.confidence(score), cal.band_n(score), cal.lower_bound(score))
        assert sz["position_tier"] == "BASE" and sz["conviction_locked"] is True
        assert sz["suggested_position_pct"] == _dc.BASE_POSITION_PCT
        assert "locked" in sz["sizing_rationale"]


def test_synthetic_band_at_70pct_n60_unlocks():
    cal = _cal([(16, 38), (42, 60), (6, 13), (7, 19)])            # 70-80 band: 70 % on 60
    assert cal.lower_bound(75) > _dc.SIZING_UNLOCK_LOWER_BOUND
    sz = size_from_probability(cal.confidence(75), cal.band_n(75), cal.lower_bound(75))
    assert sz["position_tier"] == "CONVICTION" and sz["conviction_locked"] is False
    assert sz["suggested_position_pct"] == _dc.CONVICTION_POSITION_PCT
    # other bands stay locked
    assert size_from_probability(cal.confidence(60), cal.band_n(60),
                                 cal.lower_bound(60))["position_tier"] == "BASE"


def test_high_rate_on_too_few_calls_stays_locked():
    sz = size_from_probability(90.0, 12, 70.0)
    assert sz["position_tier"] == "BASE" and "only 12 resolved" in sz["sizing_rationale"]


def test_sell_and_avoid_get_zero():
    for act in ("SELL", "AVOID"):
        assert size_from_probability(80.0, 100, 70.0, action=act)["suggested_position_pct"] == 0.0


def test_no_history_has_zero_lower_bound():
    assert Calibration().lower_bound(80) == 0.0


def test_wilson_lower_bound_values():
    assert round(wilson_lower(42, 60) * 100, 1) == 59.6
    assert wilson_lower(0, 0) == 0.0
    assert wilson_lower(30, 30) < 1.0


def test_paper_book_allocates_by_new_labels():
    from agents.paper_portfolio import _get_allocation_inr
    from agents.position_sizer import PROB_LABELS
    assert _get_allocation_inr(PROB_LABELS["BASE"]) == 5_000
    assert _get_allocation_inr(PROB_LABELS["CONVICTION"]) == 10_000
    assert _get_allocation_inr(PROB_LABELS["AVOID"]) == 0
    assert _get_allocation_inr("Half position (2.5%)") == 5_000      # legacy labels unchanged


def test_discovery_dry_run_logs_base_conviction_locked_for_every_promoted(monkeypatch, caplog):
    """Acceptance: --no-save logs 'sizing: BASE (conviction locked)' for every promoted symbol."""
    import logging
    from agents import discovery_screener as ds
    monkeypatch.setattr(ds, "fetch_all_nse_equity_symbols", lambda: ["AAA.NS", "BBB.NS"])
    monkeypatch.setattr(ds, "_load_portfolio_symbols", lambda: set())
    monkeypatch.setattr(ds, "get_nse_fii_dii", lambda: {})
    monkeypatch.setattr("agents.macro.analyse", lambda: {"signal": "NEUTRAL", "score": 50})
    monkeypatch.setattr(ds, "prescreen", lambda s, fii_data=None, features=None: (True, ["a"] * 4))
    monkeypatch.setattr(ds, "_run_all_agents", lambda s, macro_result=None: {
        a: {"signal": "BUY", "score": 70, "upside_pct": 30.0, "confidence": 0.7, "data_quality": "OK"}
        for a in ("technical", "fundamental", "sentiment", "institutional", "macro",
                  "historical_rag", "commodities")})
    monkeypatch.setattr(ds, "_best_upside", lambda ar: (30.0, 70.0))
    monkeypatch.setattr(ds, "_current_price", lambda s: 100.0)
    monkeypatch.setattr(ds, "_upside_basis", lambda *a, **k: "b")
    monkeypatch.setattr("data.impact_cost.estimate_impact_cost", lambda *a, **k: {})
    monkeypatch.setattr("data.forward_estimates.get_forward_estimates", lambda s: {})
    monkeypatch.setattr(ds, "_get_calibration", lambda: _cal([(16, 38), (16, 30), (6, 13), (7, 19)]))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with caplog.at_level(logging.INFO):
        out = ds.run_discovery(max_candidates=5, max_prescreen=2, save_to_db=False, inter_delay=0)
    assert len(out) == 2
    for r in out:
        assert r.sizing["position_tier"] == "BASE"
        assert any(f"{r.symbol} sizing: BASE (conviction locked)" in m for m in caplog.messages)
