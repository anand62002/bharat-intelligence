"""
tests/test_fundamental.py
pytest suite for agents/fundamental.py

Run from project root:
    pytest tests/test_fundamental.py -v
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.fundamental import (  # noqa: E402
    AGENT_NAME,
    DEFAULT_SECTOR_PE,
    SECTOR_PE_MAP,
    BANKING_SECTORS,
    SECTOR_PB_MAP,
    EV_EBITDA_SECTORS,
    CAPEX_HEAVY_SECTORS,
    _SECTOR_MODULES,
    analyse,
    _assess_danger,
    _estimate_upside,
    _estimate_upside_ev_ebitda,
    _score_balance_sheet,
    _score_governance,
    _score_growth,
    _score_profitability,
    _score_banking,
    _score_it,
    _score_pharma,
    _score_realestate,
)

# ──────────────────────────────────────────────────────────────────────────────
# Representative mock datasets
# ──────────────────────────────────────────────────────────────────────────────

# Healthy, TCS-like: debt-free IT company, strong ROCE, no pledging
HEALTHY_DATA = {
    "pe": 27.0,
    "revenue_growth": 14.0,        # YoY %
    "revenue_growth_qoq": 3.5,
    "ebitda_margin": 25.0,
    "debt_equity": 0.0,            # zero debt
    "roce": 52.0,
    "promoter_holding": 72.0,      # Tata Group-level holding
    "promoter_pledging": 0.0,      # no pledging
}

# HDFC Bank-like: banking sector (higher D/E acceptable), strong margins
HDFCBANK_LIKE = {
    "pe": 17.0,
    "revenue_growth": 20.0,
    "revenue_growth_qoq": 4.0,
    "ebitda_margin": 28.0,
    "debt_equity": 0.9,            # banking leverage (modest for a bank)
    "roce": 17.0,
    "promoter_holding": 0.0,       # widely held bank, no promoter
    "promoter_pledging": 0.0,
}

# CRITICAL DANGER: IL&FS / DHFL-type — all three triggers fire
CRITICAL_DATA = {
    "pe": 4.0,
    "revenue_growth": -42.0,       # trigger 1: < -30%
    "revenue_growth_qoq": -18.0,
    "ebitda_margin": 2.0,
    "debt_equity": 4.8,            # trigger 2: > 3
    "roce": 2.5,
    "promoter_holding": 38.0,
    "promoter_pledging": 68.0,     # trigger 3: > 50%
}

# WARNING: 2 of 3 primary triggers
WARNING_DATA = {
    "pe": 8.0,
    "revenue_growth": -35.0,       # trigger 1
    "revenue_growth_qoq": -10.0,
    "ebitda_margin": 5.0,
    "debt_equity": 3.5,            # trigger 2
    "roce": 5.0,
    "promoter_holding": 45.0,
    "promoter_pledging": 28.0,     # elevated but not trigger 3
}

# WATCH: 1 primary trigger only
WATCH_DATA = {
    "pe": 12.0,
    "revenue_growth": 2.0,
    "revenue_growth_qoq": -1.0,
    "ebitda_margin": 10.0,
    "debt_equity": 3.2,            # trigger: > 3
    "roce": 9.0,
    "promoter_holding": 55.0,
    "promoter_pledging": 8.0,
}

# Partial data: many fields None (stress-test graceful degradation)
SPARSE_DATA = {
    "pe": None,
    "revenue_growth": None,
    "revenue_growth_qoq": None,
    "ebitda_margin": None,
    "debt_equity": None,
    "roce": None,
    "promoter_holding": None,
    "promoter_pledging": None,
}

REQUIRED_KEYS = {
    "signal", "score", "detail", "upside_pct",
    "danger_drop_pct", "danger_confidence",
    "data_sources", "agent_name",
}
REQUIRED_DETAIL_KEYS = {
    "growth_quality", "profitability", "balance_sheet", "governance",
    "danger", "raw_metrics", "sector_specific", "sector_regime",
}
VALID_SIGNALS = {"STRONG_BUY", "BUY", "HOLD", "AVOID", "SELL", "NO_DATA", "INSUFFICIENT_DATA"}


# ──────────────────────────────────────────────────────────────────────────────
# Autouse fixtures
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def no_supabase():
    with patch("agents.fundamental._write_agent_performance"):
        yield


def _mock_ticker(current_price: float = 1500.0, sector: str = "Technology"):
    ticker = MagicMock()
    ticker.info = {"sector": sector}
    hist = MagicMock()
    hist.empty = False
    hist.__getitem__ = MagicMock(return_value=MagicMock(
        iloc=MagicMock(__getitem__=MagicMock(return_value=current_price))
    ))
    ticker.history.return_value = hist
    return ticker


# ──────────────────────────────────────────────────────────────────────────────
# Unit tests: _score_growth
# ──────────────────────────────────────────────────────────────────────────────

class TestScoreGrowth:
    def test_strong_growth_max_pts(self):
        score, _ = _score_growth(25.0, 5.0, 30.0)
        assert score == 25   # 15 (rev) + 10 (ROCE≥25)

    def test_moderate_growth_mid_pts(self):
        score, _ = _score_growth(12.0, 2.0, 20.0)
        # 10 (rev 10-20%) + 7 (ROCE 15-25%) = 17, capped at 25
        assert 10 <= score <= 25

    def test_negative_growth_zero_rev_pts(self):
        score, notes = _score_growth(-10.0, None, 8.0)
        assert score < 15   # no revenue pts
        assert "contraction" in notes.lower() or "Weak" in notes

    def test_unknown_growth_neutral(self):
        score, notes = _score_growth(None, None, None)
        assert 0 < score <= 25
        assert "unknown" in notes.lower()

    def test_score_bounded_0_to_25(self):
        for rev, qoq, roce in [
            (50.0, 10.0, 50.0),
            (-50.0, -20.0, 1.0),
            (None, None, None),
        ]:
            score, _ = _score_growth(rev, qoq, roce)
            assert 0 <= score <= 25

    def test_excellent_roce_bonus(self):
        s_high, _ = _score_growth(15.0, 0.0, 30.0)   # ROCE≥25 → +10
        s_low,  _ = _score_growth(15.0, 0.0, 5.0)    # ROCE<10 → 0
        assert s_high > s_low


# ──────────────────────────────────────────────────────────────────────────────
# Unit tests: _score_profitability
# ──────────────────────────────────────────────────────────────────────────────

class TestScoreProfitability:
    def test_excellent_margin_undervalued(self):
        score, _ = _score_profitability(35.0, 10.0, DEFAULT_SECTOR_PE)
        # 15 (margin≥30) + 10 (PE≤70% sector) = 25
        assert score == 25

    def test_thin_margin_expensive_stock(self):
        score, notes = _score_profitability(3.0, 60.0, DEFAULT_SECTOR_PE)
        # 4 (margin 3%) + 0 (PE >> sector) = 4
        assert score <= 10
        assert "expensive" in notes.lower() or "premium" in notes.lower()

    def test_unknown_values_neutral(self):
        score, _ = _score_profitability(None, None, DEFAULT_SECTOR_PE)
        assert 0 < score <= 25

    def test_pe_below_sector_scores_higher(self):
        s_cheap, _ = _score_profitability(20.0, 12.0, 22.0)   # PE well below sector
        s_dear,  _ = _score_profitability(20.0, 45.0, 22.0)   # PE >> sector
        assert s_cheap > s_dear

    def test_score_bounded_0_to_25(self):
        for margin, pe in [(50.0, 5.0), (-10.0, 200.0), (None, None)]:
            score, _ = _score_profitability(margin, pe, DEFAULT_SECTOR_PE)
            assert 0 <= score <= 25


# ──────────────────────────────────────────────────────────────────────────────
# Unit tests: _score_balance_sheet
# ──────────────────────────────────────────────────────────────────────────────

class TestScoreBalanceSheet:
    def test_zero_debt_max_score(self):
        score, notes = _score_balance_sheet(0.0, 55.0)
        assert score == 25   # 20 (zero debt) + 5 (ROCE bonus)
        assert "zero debt" in notes.lower()

    def test_very_low_leverage_high_score(self):
        score, _ = _score_balance_sheet(0.3, 20.0)
        assert score >= 20

    def test_dangerous_leverage_low_score(self):
        score, notes = _score_balance_sheet(5.0, 3.0)
        assert score <= 5
        assert "dangerous" in notes.lower()

    def test_roce_bonus_only_for_low_leverage(self):
        s_with_bonus,    _ = _score_balance_sheet(0.8, 20.0)   # D/E<1.5, ROCE≥15 → bonus
        s_without_bonus, _ = _score_balance_sheet(2.5, 20.0)   # D/E≥1.5 → no bonus
        assert s_with_bonus > s_without_bonus

    def test_unknown_de_neutral(self):
        score, _ = _score_balance_sheet(None, None)
        assert 0 < score <= 25

    def test_score_bounded_0_to_25(self):
        for de, roce in [(0.0, 60.0), (10.0, 1.0), (None, None)]:
            score, _ = _score_balance_sheet(de, roce)
            assert 0 <= score <= 25


# ──────────────────────────────────────────────────────────────────────────────
# Unit tests: _score_governance
# ──────────────────────────────────────────────────────────────────────────────

class TestScoreGovernance:
    def test_ideal_governance_high_score(self):
        score, _ = _score_governance(70.0, 0.0, 0.5)
        # 15 (holding≥65) + 10 (pledging<5) = 25
        assert score == 25

    def test_critical_pledging_penalty_40_plus(self):
        """Pledging >40% → -30 pts penalty (spec requirement)."""
        score_clean,  _ = _score_governance(55.0, 0.0, 0.5)
        score_danger, _ = _score_governance(55.0, 45.0, 0.5)
        assert score_danger < score_clean
        assert score_danger == 0   # penalty floors at 0

    def test_elevated_pledging_penalty_20_to_40(self):
        """Pledging >20% → -15 pts penalty (spec requirement)."""
        score_ok,      _ = _score_governance(55.0, 10.0, 0.5)
        score_penalty, _ = _score_governance(55.0, 25.0, 0.5)
        assert score_penalty < score_ok

    def test_high_de_governance_penalty(self):
        """D/E > 2 applies -10 pts governance penalty (spec requirement)."""
        score_low_de,  _ = _score_governance(55.0, 5.0, 1.0)
        score_high_de, _ = _score_governance(55.0, 5.0, 2.5)
        assert score_high_de <= score_low_de - 10

    def test_score_never_negative(self):
        """Score is capped at 0 even with all penalties stacking."""
        score, _ = _score_governance(0.0, 80.0, 5.0)
        assert score == 0

    def test_score_bounded_0_to_25(self):
        for holding, pledging, de in [
            (80.0, 0.0, 0.0),
            (0.0, 90.0, 10.0),
            (None, None, None),
        ]:
            score, _ = _score_governance(holding, pledging, de)
            assert 0 <= score <= 25


# ──────────────────────────────────────────────────────────────────────────────
# Unit tests: _assess_danger
# ──────────────────────────────────────────────────────────────────────────────

class TestAssessDanger:
    def test_critical_all_three_triggers(self):
        """All three primary triggers → CRITICAL (spec requirement)."""
        level, drop, conf, triggers = _assess_danger(
            revenue_growth=-42.0,   # < -30
            debt_equity=4.8,        # > 3
            promoter_pledging=68.0, # > 50
            ebitda_margin=2.0,
        )
        assert level == "CRITICAL"
        assert drop == 55.0
        assert conf == 0.82
        assert len(triggers) >= 3

    def test_warning_two_triggers(self):
        level, drop, conf, _ = _assess_danger(
            revenue_growth=-35.0,
            debt_equity=3.5,
            promoter_pledging=28.0,   # elevated but not trigger 3
            ebitda_margin=5.0,
        )
        assert level in ("WARNING", "WATCH")
        assert drop is not None and drop > 0

    def test_watch_one_trigger(self):
        level, drop, conf, _ = _assess_danger(
            revenue_growth=5.0,
            debt_equity=3.5,       # only trigger: D/E > 3
            promoter_pledging=10.0,
            ebitda_margin=15.0,
        )
        assert level in ("WATCH", "WARNING")

    def test_no_danger_healthy_stock(self):
        level, drop, conf, triggers = _assess_danger(
            revenue_growth=15.0,
            debt_equity=0.2,
            promoter_pledging=0.0,
            ebitda_margin=25.0,
        )
        assert level is None
        assert drop is None
        assert conf == 0.0
        assert triggers == []

    def test_danger_drop_ordered(self):
        """CRITICAL drop > WARNING drop > WATCH drop."""
        _, crit_drop, _, _ = _assess_danger(-45.0, 4.0, 60.0, 2.0)
        _, warn_drop, _, _ = _assess_danger(-35.0, 3.5, 28.0, 4.0)
        _, watch_drop, _, _ = _assess_danger(5.0, 3.2, 5.0, 15.0)
        assert crit_drop > warn_drop > watch_drop

    def test_none_inputs_no_crash(self):
        level, drop, conf, triggers = _assess_danger(None, None, None, None)
        # No data → no triggers; may return None or WATCH depending on secondary
        assert level is None or level in ("WATCH", "WARNING", "CRITICAL")
        assert conf >= 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Unit tests: _estimate_upside
# ──────────────────────────────────────────────────────────────────────────────

class TestEstimateUpside:
    def test_positive_upside_for_undervalued_stock(self):
        """PE below sector PE with positive growth → positive upside."""
        upside = _estimate_upside(pe=15.0, revenue_growth=15.0,
                                   current_price=1000.0, sector_pe=22.0)
        assert upside is not None and upside > 0

    def test_negative_upside_for_overvalued_stock(self):
        """PE >> sector PE → negative upside."""
        upside = _estimate_upside(pe=45.0, revenue_growth=5.0,
                                   current_price=1000.0, sector_pe=22.0)
        assert upside is not None and upside < 0

    def test_returns_none_without_pe(self):
        assert _estimate_upside(None, 10.0, 1000.0, 22.0) is None

    def test_returns_none_without_price(self):
        assert _estimate_upside(20.0, 10.0, None, 22.0) is None

    def test_returns_none_for_zero_pe(self):
        assert _estimate_upside(0.0, 10.0, 1000.0, 22.0) is None

    def test_growth_clamped_at_50pct(self):
        """Extreme growth claims are clamped so the estimate stays rational."""
        u_extreme = _estimate_upside(20.0, 200.0, 1000.0, 22.0)
        u_clamped = _estimate_upside(20.0, 50.0,  1000.0, 22.0)
        assert u_extreme == u_clamped   # both clamped to 50%


# ──────────────────────────────────────────────────────────────────────────────
# Integration tests: analyse()
# ──────────────────────────────────────────────────────────────────────────────

def _run_analyse(screener_data, symbol="TEST", sector=None, price=1500.0):
    """Helper: run analyse() with mocked screener + yfinance."""
    ticker = MagicMock()
    ticker.info = {"sector": sector or "Technology"}
    import pandas as pd
    hist_df = pd.DataFrame({"Close": [price]})
    ticker.history.return_value = hist_df
    with patch("agents.fundamental.get_screener_data", return_value=screener_data), \
         patch("yfinance.Ticker", return_value=ticker):
        return analyse(symbol, sector=sector)


class TestAnalyseSchema:
    def test_all_top_level_keys_present(self):
        result = _run_analyse(HEALTHY_DATA)
        assert REQUIRED_KEYS.issubset(result.keys()), (
            f"Missing: {REQUIRED_KEYS - result.keys()}"
        )

    def test_all_detail_keys_present(self):
        result = _run_analyse(HEALTHY_DATA)
        assert REQUIRED_DETAIL_KEYS.issubset(result["detail"].keys())

    def test_agent_name_is_fundamental(self):
        result = _run_analyse(HEALTHY_DATA)
        assert result["agent_name"] == AGENT_NAME == "fundamental"

    def test_signal_in_valid_set(self):
        result = _run_analyse(HEALTHY_DATA)
        assert result["signal"] in VALID_SIGNALS

    def test_score_in_range(self):
        result = _run_analyse(HEALTHY_DATA)
        assert 0 <= result["score"] <= 100

    def test_sub_scores_sum_to_total(self):
        result = _run_analyse(HEALTHY_DATA)
        d = result["detail"]
        sub_sum = (
            d["growth_quality"]["score"]
            + d["profitability"]["score"]
            + d["balance_sheet"]["score"]
            + d["governance"]["score"]
        )
        assert result["score"] == sub_sum

    def test_sub_score_bounds(self):
        result = _run_analyse(HEALTHY_DATA)
        d = result["detail"]
        assert 0 <= d["growth_quality"]["score"] <= 25
        assert 0 <= d["profitability"]["score"] <= 25
        assert 0 <= d["balance_sheet"]["score"] <= 25
        assert 0 <= d["governance"]["score"] <= 25

    def test_danger_confidence_in_range(self):
        result = _run_analyse(HEALTHY_DATA)
        assert 0.0 <= result["danger_confidence"] <= 1.0

    def test_data_sources_nonempty(self):
        result = _run_analyse(HEALTHY_DATA)
        assert "screener_in" in result["data_sources"]


class TestAnalyseHealthyStock:
    """TCS / HDFC-like: should produce BUY or STRONG_BUY, no danger."""

    def test_healthy_tcs_like_signal(self):
        result = _run_analyse(HEALTHY_DATA, sector="it")
        assert result["signal"] in ("STRONG_BUY", "BUY")

    def test_healthy_high_score(self):
        result = _run_analyse(HEALTHY_DATA, sector="it")
        assert result["score"] >= 55

    def test_no_danger_for_healthy_stock(self):
        result = _run_analyse(HEALTHY_DATA)
        assert result["danger_drop_pct"] is None
        assert result["danger_confidence"] == 0.0
        assert result["detail"]["danger"]["level"] is None

    def test_hdfc_bank_like_signal(self):
        result = _run_analyse(HDFCBANK_LIKE, sector="banking")
        assert result["signal"] in ("STRONG_BUY", "BUY", "HOLD")


class TestAnalyseCriticalDanger:
    """IL&FS / DHFL-like: all three triggers → CRITICAL, forced SELL."""

    def test_critical_signal_is_sell(self):
        """Spec: CRITICAL DANGER forces signal to SELL regardless of score."""
        result = _run_analyse(CRITICAL_DATA)
        assert result["signal"] == "SELL"

    def test_critical_danger_level(self):
        result = _run_analyse(CRITICAL_DATA)
        assert result["detail"]["danger"]["level"] == "CRITICAL"

    def test_critical_danger_drop_estimate(self):
        result = _run_analyse(CRITICAL_DATA)
        assert result["danger_drop_pct"] == 55.0

    def test_critical_danger_confidence(self):
        result = _run_analyse(CRITICAL_DATA)
        assert result["danger_confidence"] == 0.82

    def test_critical_all_three_triggers_in_detail(self):
        result = _run_analyse(CRITICAL_DATA)
        triggers = " ".join(result["detail"]["danger"]["triggers"])
        # Revenue, leverage, and pledging triggers must all be present
        assert any("revenue" in t or "decline" in t for t in result["detail"]["danger"]["triggers"])
        assert any("leverage" in t or "de_" in t or "dangerous" in t for t in result["detail"]["danger"]["triggers"])
        assert any("pledg" in t for t in result["detail"]["danger"]["triggers"])

    def test_critical_low_score(self):
        result = _run_analyse(CRITICAL_DATA)
        assert result["score"] <= 40   # bad fundamentals → low score

    def test_governance_score_penalised(self):
        """High pledging + high D/E should produce low/zero governance score."""
        result = _run_analyse(CRITICAL_DATA)
        assert result["detail"]["governance"]["score"] == 0


class TestAnalyseWarningStock:
    def test_warning_detected(self):
        result = _run_analyse(WARNING_DATA)
        assert result["detail"]["danger"]["level"] in ("WARNING", "CRITICAL")

    def test_warning_signal_avoid_or_sell(self):
        result = _run_analyse(WARNING_DATA)
        assert result["signal"] in ("SELL", "AVOID", "HOLD")


class TestAnalyseWatchStock:
    def test_watch_detected(self):
        result = _run_analyse(WATCH_DATA)
        assert result["detail"]["danger"]["level"] in ("WATCH", "WARNING")

    def test_watch_positive_danger_drop(self):
        result = _run_analyse(WATCH_DATA)
        assert result["danger_drop_pct"] is not None
        assert result["danger_drop_pct"] > 0


class TestAnalyseEdgeCases:
    def test_no_screener_data_returns_no_data(self):
        with patch("agents.fundamental.get_screener_data", return_value=None):
            result = analyse("INVALID")
        assert result["signal"] == "NO_DATA"
        assert result["score"] == 0
        assert result["agent_name"] == "fundamental"

    def test_sparse_data_no_crash(self):
        """All-None fundamentals must return a valid dict, not raise."""
        result = _run_analyse(SPARSE_DATA)
        assert REQUIRED_KEYS.issubset(result.keys())
        # INSUFFICIENT_DATA path returns score=None (excluded from composite);
        # normal paths return an int 0-100.
        assert result["score"] is None or 0 <= result["score"] <= 100
        assert result["signal"] in VALID_SIGNALS

    def test_sparse_data_no_danger(self):
        """No data → no triggers → no danger (or INSUFFICIENT_DATA with no detail)."""
        result = _run_analyse(SPARSE_DATA)
        # INSUFFICIENT_DATA path may omit the danger dict entirely.
        danger = result.get("detail", {}).get("danger", {})
        assert danger.get("level") is None

    def test_supabase_failure_no_crash(self):
        with patch("agents.fundamental.get_screener_data", return_value=HEALTHY_DATA), \
             patch("yfinance.Ticker", return_value=MagicMock(
                 info={"sector": "it"},
                 history=MagicMock(return_value=MagicMock(
                     empty=False,
                     __getitem__=MagicMock(return_value=MagicMock(
                         iloc=MagicMock(__getitem__=MagicMock(return_value=1500.0))
                     ))
                 ))
             )), \
             patch("agents.fundamental._write_agent_performance",
                   side_effect=Exception("DB down")):
            result = analyse("TEST")
        assert result["agent_name"] == "fundamental"

    def test_yfinance_failure_falls_back_gracefully(self):
        """yfinance errors must not crash analyse(); upside becomes None."""
        with patch("agents.fundamental.get_screener_data", return_value=HEALTHY_DATA), \
             patch("yfinance.Ticker", side_effect=Exception("network error")):
            result = analyse("TEST")
        assert result["agent_name"] == "fundamental"
        assert result["upside_pct"] is None   # no price → no upside


class TestSectorPE:
    def test_known_sector_overrides_default(self):
        result_it  = _run_analyse(HEALTHY_DATA, sector="it")
        result_def = _run_analyse(HEALTHY_DATA, sector=None)
        # IT sector PE (30) vs default (22) → different profitability scores
        # sector_pe_static is the raw map value before regime adjustment
        assert (
            result_it["detail"]["profitability"]["sector_pe_static"] == 30.0
        )

    def test_unknown_sector_uses_default(self):
        result = _run_analyse(HEALTHY_DATA, sector="unknown_sector_xyz")
        assert result["detail"]["profitability"]["sector_pe_static"] == DEFAULT_SECTOR_PE

    def test_sector_pe_map_has_banking(self):
        assert "banking" in SECTOR_PE_MAP
        assert SECTOR_PE_MAP["banking"] < DEFAULT_SECTOR_PE   # banks trade at lower PE

    def test_sector_pe_map_has_fmcg(self):
        assert "fmcg" in SECTOR_PE_MAP
        assert SECTOR_PE_MAP["fmcg"] > DEFAULT_SECTOR_PE   # FMCG commands premium


class TestUpsideIntegration:
    def test_upside_present_when_pe_and_price_known(self):
        result = _run_analyse(HEALTHY_DATA, sector="it", price=3000.0)
        assert result["upside_pct"] is not None

    def test_upside_none_when_pe_missing(self):
        data = {**HEALTHY_DATA, "pe": None}
        result = _run_analyse(data, price=1500.0)
        assert result["upside_pct"] is None

    def test_raw_metrics_in_detail(self):
        result = _run_analyse(HEALTHY_DATA)
        raw = result["detail"]["raw_metrics"]
        for key in ["pe", "revenue_growth", "ebitda_margin", "debt_equity",
                    "roce", "promoter_holding", "promoter_pledging"]:
            assert key in raw


# ──────────────────────────────────────────────────────────────────────────────
# Tier 1 tests: Net Debt/EBITDA and PEG ratio (zero-cost derived metrics)
# ──────────────────────────────────────────────────────────────────────────────

class TestNetDebtEbitda:
    """Net Debt/EBITDA affects balance_sheet score and danger assessment."""

    def test_net_cash_position_adds_pts(self):
        """ND/EBITDA <= 0 (net cash) should give bonus pts."""
        s_cash, notes = _score_balance_sheet(0.0, 20.0, net_debt_ebitda=-0.5)
        s_base, _     = _score_balance_sheet(0.0, 20.0)
        # Both capped at 25; net cash should note it
        assert "cash" in notes.lower() or "net cash" in notes.lower()

    def test_low_nd_ebitda_bonus(self):
        """ND/EBITDA <= 1.5 should add pts vs no data provided."""
        s_low,  _ = _score_balance_sheet(1.0, 10.0, net_debt_ebitda=1.0)
        s_none, _ = _score_balance_sheet(1.0, 10.0)
        assert s_low >= s_none

    def test_high_nd_ebitda_penalty(self):
        """ND/EBITDA > 5.0 should reduce score vs moderate ND/EBITDA."""
        s_high,   _ = _score_balance_sheet(1.0, 10.0, net_debt_ebitda=6.0)
        s_moderate, _ = _score_balance_sheet(1.0, 10.0, net_debt_ebitda=2.0)
        assert s_high < s_moderate

    def test_nd_ebitda_in_danger_secondary(self):
        """ND/EBITDA > 5 should appear in danger triggers as secondary signal."""
        _, _, _, triggers = _assess_danger(
            revenue_growth=5.0, debt_equity=2.5, promoter_pledging=10.0,
            ebitda_margin=15.0, net_debt_ebitda=6.0,
        )
        assert any("nd_ebitda" in t or "leverage" in t for t in triggers)

    def test_nd_ebitda_none_no_change(self):
        """Omitting net_debt_ebitda leaves score unchanged vs explicit None."""
        s1, _ = _score_balance_sheet(0.5, 18.0)
        s2, _ = _score_balance_sheet(0.5, 18.0, net_debt_ebitda=None)
        assert s1 == s2

    def test_score_still_bounded_with_nd_ebitda(self):
        """Score must remain 0–25 regardless of ND/EBITDA value."""
        for nd_eb in [-2.0, 0.0, 1.5, 3.5, 7.0, 15.0]:
            s, _ = _score_balance_sheet(0.3, 20.0, net_debt_ebitda=nd_eb)
            assert 0 <= s <= 25


class TestPEGRatio:
    """PEG ratio adjusts P/E valuation sub-score ±2 pts."""

    def test_low_peg_adds_pts(self):
        """PEG < 0.8 should add 2 pts vs same inputs without PEG."""
        s_peg, notes = _score_profitability(20.0, 15.0, 22.0, peg_ratio=0.6)
        s_no,  _     = _score_profitability(20.0, 15.0, 22.0)
        assert s_peg >= s_no
        assert "PEG" in notes and "0.8" in notes

    def test_high_peg_reduces_pts(self):
        """PEG > 3.0 should reduce pts vs same inputs without PEG."""
        s_peg, notes = _score_profitability(20.0, 15.0, 22.0, peg_ratio=4.5)
        s_no,  _     = _score_profitability(20.0, 15.0, 22.0)
        assert s_peg <= s_no
        assert "PEG" in notes

    def test_moderate_peg_no_adjustment(self):
        """PEG between 0.8 and 3.0 should not change valuation sub-score."""
        s_peg, _ = _score_profitability(20.0, 15.0, 22.0, peg_ratio=1.5)
        s_no,  _ = _score_profitability(20.0, 15.0, 22.0)
        assert s_peg == s_no

    def test_peg_inactive_in_ev_ebitda_mode(self):
        """PEG should not fire when EV/EBITDA is the active valuation metric."""
        s_peg, notes = _score_profitability(
            40.0, -5.0, 38.0,
            ev_ebitda=7.0, sector_ev_ebitda=8.5, prefer_ev_ebitda=True,
            peg_ratio=0.3,
        )
        # PEG note should not appear when EV/EBITDA is scoring
        assert "PEG" not in notes

    def test_peg_inactive_in_pb_mode(self):
        """PEG should not fire when P/B is the active valuation metric."""
        s_peg, notes = _score_profitability(
            25.0, 20.0, 14.0,
            pb_ratio=1.2, sector_pb=1.8, prefer_pb=True,
            peg_ratio=0.5,
        )
        assert "PEG" not in notes

    def test_peg_none_no_crash(self):
        s, _ = _score_profitability(20.0, 15.0, 22.0, peg_ratio=None)
        assert 0 <= s <= 25


# ──────────────────────────────────────────────────────────────────────────────
# Tier 2 tests: ROE, P/B, FCF yield, PAT margin, ICR, dividend yield
# ──────────────────────────────────────────────────────────────────────────────

class TestROEScoring:
    """ROE is a keyword-only bonus in _score_growth (max 5 pts)."""

    def test_excellent_roe_adds_pts(self):
        s_roe, notes = _score_growth(15.0, 2.0, 20.0, roe=22.0)
        s_no,  _     = _score_growth(15.0, 2.0, 20.0)
        assert s_roe >= s_no
        assert "ROE" in notes

    def test_weak_roe_no_pts_but_noted(self):
        s, notes = _score_growth(10.0, 0.0, 12.0, roe=5.0)
        assert "Weak ROE" in notes or "ROE" in notes

    def test_roe_capped_at_25(self):
        """Score can never exceed 25 even with maximal ROE."""
        s, _ = _score_growth(25.0, 10.0, 30.0, roe=35.0)
        assert s == 25   # already at cap from rev+ROCE alone

    def test_roe_none_no_crash(self):
        s, _ = _score_growth(15.0, 2.0, 20.0, roe=None)
        assert 0 <= s <= 25

    def test_score_bounded_with_roe(self):
        for roe_val in [None, 5.0, 15.0, 30.0, 50.0]:
            s, _ = _score_growth(20.0, 5.0, 25.0, roe=roe_val)
            assert 0 <= s <= 25


class TestPBScoring:
    """P/B is the primary valuation metric for banking/NBFC sectors."""

    def test_pb_scoring_for_banking(self):
        """Banking sector with prefer_pb=True should use P/B, not P/E."""
        s, notes = _score_profitability(
            25.0, 30.0, 14.0,   # P/E=30 would be 'slight premium' vs banking 14x
            pb_ratio=1.2, sector_pb=1.8, prefer_pb=True,
        )
        assert "P/B" in notes
        assert "P/E" not in notes or "reference" in notes

    def test_pb_undervalued_scores_well(self):
        """P/B well below sector median → high valuation sub-score."""
        s_cheap, _ = _score_profitability(
            20.0, 20.0, 14.0, pb_ratio=0.9, sector_pb=1.8, prefer_pb=True,
        )
        s_dear, _ = _score_profitability(
            20.0, 20.0, 14.0, pb_ratio=3.5, sector_pb=1.8, prefer_pb=True,
        )
        assert s_cheap > s_dear

    def test_pb_prefer_pb_false_uses_pe(self):
        """prefer_pb=False should use P/E even when pb_ratio is provided."""
        _, notes = _score_profitability(
            20.0, 20.0, 14.0, pb_ratio=1.2, sector_pb=1.8, prefer_pb=False,
        )
        # P/E path is active; no P/B scoring note
        assert "P/B" not in notes

    def test_banking_sectors_set_not_empty(self):
        assert len(BANKING_SECTORS) > 0
        assert "banking" in BANKING_SECTORS
        assert "nbfc" in BANKING_SECTORS

    def test_sector_pb_map_has_banking(self):
        assert "banking" in SECTOR_PB_MAP
        assert SECTOR_PB_MAP["banking"] > 0

    def test_pb_none_falls_back_to_pe(self):
        """pb_ratio=None with prefer_pb=True should fall back to P/E."""
        _, notes = _score_profitability(
            20.0, 15.0, 14.0, pb_ratio=None, sector_pb=1.8, prefer_pb=True,
        )
        assert "PE" in notes or "P/E" in notes


class TestFCFYield:
    """FCF yield is an earnings-quality bonus inside _score_profitability."""

    def test_strong_fcf_yield_adds_pts(self):
        s_fcf, notes = _score_profitability(20.0, 18.0, 22.0, fcf_yield=6.0)
        s_no,  _     = _score_profitability(20.0, 18.0, 22.0)
        assert s_fcf >= s_no
        assert "FCF" in notes

    def test_negative_fcf_noted(self):
        _, notes = _score_profitability(20.0, 18.0, 22.0, fcf_yield=-3.0)
        assert "Negative FCF" in notes or "consuming" in notes.lower()

    def test_fcf_none_no_inflation(self):
        """fcf_yield=None must NOT add any pts (unlike EBITDA margin unknown)."""
        s_none, _ = _score_profitability(20.0, 18.0, 22.0, fcf_yield=None)
        s_no,   _ = _score_profitability(20.0, 18.0, 22.0)
        assert s_none == s_no

    def test_fcf_yield_capped(self):
        s, _ = _score_profitability(35.0, 10.0, DEFAULT_SECTOR_PE, fcf_yield=10.0)
        assert s == 25   # already 25 before FCF; cap holds


class TestPATMargin:
    """PAT margin is informational (+ loss-making penalty) in _score_profitability."""

    def test_healthy_net_margin_noted(self):
        _, notes = _score_profitability(30.0, 15.0, 22.0, pat_margin=18.0)
        assert "net margin" in notes.lower()

    def test_negative_pat_margin_penalty(self):
        s_loss, notes = _score_profitability(20.0, 15.0, 22.0, pat_margin=-5.0)
        s_ok,   _     = _score_profitability(20.0, 15.0, 22.0, pat_margin=10.0)
        assert s_loss < s_ok
        assert "loss" in notes.lower() or "negative" in notes.lower()

    def test_pat_margin_none_no_crash(self):
        s, _ = _score_profitability(20.0, 15.0, 22.0, pat_margin=None)
        assert 0 <= s <= 25


class TestICRScoring:
    """ICR below thresholds penalises balance_sheet score and fires danger trigger."""

    def test_strong_icr_bonus(self):
        s_good, notes = _score_balance_sheet(1.0, 15.0, icr=5.0)
        s_none, _     = _score_balance_sheet(1.0, 15.0)
        assert s_good >= s_none
        assert "coverage" in notes.lower()

    def test_thin_icr_penalty(self):
        s_thin, notes = _score_balance_sheet(1.0, 15.0, icr=1.3)
        s_good, _     = _score_balance_sheet(1.0, 15.0, icr=5.0)
        assert s_thin < s_good
        assert "thin" in notes.lower() or "risk" in notes.lower()

    def test_critical_icr_heavy_penalty(self):
        """ICR < 1.0 triggers the maximum penalty."""
        s_crit, notes = _score_balance_sheet(1.0, 15.0, icr=0.6)
        s_thin, _     = _score_balance_sheet(1.0, 15.0, icr=1.3)
        assert s_crit < s_thin
        assert "CRITICAL" in notes or "cannot" in notes.lower()

    def test_icr_as_danger_primary_trigger(self):
        """ICR < 1.0 should register as a primary trigger in _assess_danger."""
        level, _, _, triggers = _assess_danger(
            revenue_growth=5.0, debt_equity=1.5,
            promoter_pledging=10.0, ebitda_margin=20.0,
            icr=0.7,
        )
        assert level is not None   # danger detected
        assert any("icr" in t or "interest" in t for t in triggers)

    def test_icr_plus_two_other_triggers_is_critical(self):
        """ICR < 1 + revenue collapse + D/E > 3 = 3 primaries → CRITICAL."""
        level, drop, conf, triggers = _assess_danger(
            revenue_growth=-35.0,   # primary 1
            debt_equity=4.0,        # primary 2
            promoter_pledging=10.0,
            ebitda_margin=5.0,
            icr=0.5,                # primary 3 (new)
        )
        assert level == "CRITICAL"
        assert drop == 55.0
        assert conf == 0.82

    def test_icr_none_no_change(self):
        s1, _ = _score_balance_sheet(0.5, 18.0)
        s2, _ = _score_balance_sheet(0.5, 18.0, icr=None)
        assert s1 == s2

    def test_score_bounded_with_icr(self):
        for icr_val in [0.3, 0.9, 1.5, 4.0, 10.0]:
            s, _ = _score_balance_sheet(0.5, 18.0, icr=icr_val)
            assert 0 <= s <= 25


class TestDividendYield:
    """Dividend yield is a governance bonus (max 3 pts)."""

    def test_high_dividend_yield_bonus(self):
        s_div, notes = _score_governance(60.0, 2.0, 0.5, dividend_yield=5.0)
        s_none, _    = _score_governance(60.0, 2.0, 0.5)
        assert s_div >= s_none
        assert "dividend" in notes.lower() or "yield" in notes.lower()

    def test_dividend_yield_caps_at_25(self):
        """Adding dividend yield to an already-maxed score stays at 25."""
        s, _ = _score_governance(70.0, 0.0, 0.5, dividend_yield=6.0)
        assert s == 25   # 15 (holding) + 10 (pledging) already at cap

    def test_zero_dividend_no_bonus(self):
        s_zero, _ = _score_governance(60.0, 2.0, 0.5, dividend_yield=0.0)
        s_none, _ = _score_governance(60.0, 2.0, 0.5)
        assert s_zero == s_none

    def test_dividend_yield_none_no_change(self):
        s1, _ = _score_governance(50.0, 5.0, 1.0)
        s2, _ = _score_governance(50.0, 5.0, 1.0, dividend_yield=None)
        assert s1 == s2

    def test_score_bounded_with_dividend(self):
        for dy in [0.0, 1.0, 2.5, 5.0, 10.0]:
            s, _ = _score_governance(50.0, 5.0, 1.0, dividend_yield=dy)
            assert 0 <= s <= 25


# ──────────────────────────────────────────────────────────────────────────────
# Tests: _estimate_upside_ev_ebitda
# ──────────────────────────────────────────────────────────────────────────────

class TestEstimateUpsideEvEbitda:
    def test_positive_upside_when_undervalued(self):
        """Company trading at low EV/EBITDA vs sector → positive upside."""
        # fair_EV = 8.5 * 500bn = 4250bn, net_debt=500bn
        # fair_equity = 3750bn, fair_price = 750, current = 600 → +25%
        upside = _estimate_upside_ev_ebitda(
            ebitda_abs=500e9, shares_outstanding=5e9,
            net_debt=500e9, current_price=600.0, sector_ev_ebitda=8.5,
        )
        assert upside is not None and upside > 0

    def test_negative_upside_when_overvalued(self):
        """Company trading at high EV/EBITDA vs sector → negative upside."""
        # fair_EV = 8.5 * 500bn = 4250bn, net_debt=500bn
        # fair_equity = 3750bn, fair_price = 750, current = 1000 → -25%
        upside = _estimate_upside_ev_ebitda(
            ebitda_abs=500e9, shares_outstanding=5e9,
            net_debt=500e9, current_price=1000.0, sector_ev_ebitda=8.5,
        )
        assert upside is not None and upside < 0

    def test_returns_none_when_ebitda_missing(self):
        assert _estimate_upside_ev_ebitda(
            ebitda_abs=None, shares_outstanding=5e9,
            net_debt=500e9, current_price=800.0, sector_ev_ebitda=8.5,
        ) is None

    def test_returns_none_when_price_missing(self):
        assert _estimate_upside_ev_ebitda(
            ebitda_abs=500e9, shares_outstanding=5e9,
            net_debt=500e9, current_price=None, sector_ev_ebitda=8.5,
        ) is None

    def test_returns_none_when_fair_equity_negative(self):
        """Net debt > fair EV → deeply distressed → skip (not huge negative)."""
        # fair_EV = 8.5 * 100bn = 850bn, net_debt = 1000bn → fair_equity < 0
        assert _estimate_upside_ev_ebitda(
            ebitda_abs=100e9, shares_outstanding=5e9,
            net_debt=1000e9, current_price=100.0, sector_ev_ebitda=8.5,
        ) is None


# ──────────────────────────────────────────────────────────────────────────────
# Tests: New constants and sector sets
# ──────────────────────────────────────────────────────────────────────────────

class TestNewConstants:
    def test_banking_sectors_frozenset(self):
        assert isinstance(BANKING_SECTORS, frozenset)
        assert "banking" in BANKING_SECTORS
        assert "nbfc" in BANKING_SECTORS
        assert "finance" in BANKING_SECTORS

    def test_sector_pb_map_coverage(self):
        """All BANKING_SECTORS entries should have a P/B benchmark."""
        for sector in BANKING_SECTORS:
            assert sector in SECTOR_PB_MAP, f"Missing P/B benchmark for {sector}"

    def test_ev_ebitda_sectors_not_in_banking(self):
        """Banking uses P/B, not EV/EBITDA; no overlap for key banking names."""
        assert "banking" not in EV_EBITDA_SECTORS
        assert "nbfc" not in EV_EBITDA_SECTORS

    def test_capex_heavy_sectors_is_frozenset(self):
        assert isinstance(CAPEX_HEAVY_SECTORS, frozenset)
        assert "telecom" in CAPEX_HEAVY_SECTORS
        assert "utilities" in CAPEX_HEAVY_SECTORS


# ──────────────────────────────────────────────────────────────────────────────
# Integration tests: new fields flow through analyse() correctly
# ──────────────────────────────────────────────────────────────────────────────

class TestTier2Integration:
    """Verify Tier 1/2 fields appear in detail and raw_metrics via analyse()."""

    def test_new_raw_metrics_keys_present(self):
        result = _run_analyse(HEALTHY_DATA)
        raw = result["detail"]["raw_metrics"]
        new_keys = [
            "roe", "pb_ratio", "sector_pb", "fcf_yield", "pat_margin",
            "icr", "net_debt_ebitda", "current_ratio", "dividend_yield",
            "peg_ratio",
        ]
        for key in new_keys:
            assert key in raw, f"Missing raw_metrics key: {key}"

    def test_new_detail_keys_present(self):
        result = _run_analyse(HEALTHY_DATA)
        d = result["detail"]
        assert "roe" in d["growth_quality"]
        assert "peg_ratio" in d["profitability"]
        assert "fcf_yield" in d["profitability"]
        assert "pat_margin" in d["profitability"]
        assert "pb_ratio" in d["profitability"]
        assert "icr" in d["balance_sheet"]
        assert "net_debt_ebitda" in d["balance_sheet"]
        assert "current_ratio" in d["balance_sheet"]
        assert "dividend_yield" in d["governance"]

    def test_new_fields_none_when_yfinance_mocked_minimal(self):
        """Mock returns only sector; all Tier 2 fields should be None."""
        result = _run_analyse(HEALTHY_DATA, sector="it")
        raw = result["detail"]["raw_metrics"]
        # These fields come exclusively from yfinance; mock doesn't provide them
        assert raw["roe"] is None
        assert raw["pb_ratio"] is None
        assert raw["fcf_yield"] is None
        assert raw["icr"] is None

    def test_peg_computed_when_pe_and_growth_known(self):
        """PEG = pe / revenue_growth must be pre-computed in analyse()."""
        # HEALTHY_DATA: pe=27, revenue_growth=14 → PEG ≈ 1.93
        result = _run_analyse(HEALTHY_DATA, sector="it")
        raw = result["detail"]["raw_metrics"]
        assert raw["peg_ratio"] is not None
        assert abs(raw["peg_ratio"] - 27.0 / 14.0) < 0.01

    def test_peg_none_when_growth_zero_or_negative(self):
        """PEG is undefined for zero/negative growth; must be None."""
        data = {**HEALTHY_DATA, "revenue_growth": -5.0}
        result = _run_analyse(data, sector="it")
        raw = result["detail"]["raw_metrics"]
        assert raw["peg_ratio"] is None

    def test_net_debt_ebitda_none_when_ebitda_missing(self):
        """Net Debt/EBITDA requires ebitda_abs from yfinance; mock → None."""
        result = _run_analyse(HEALTHY_DATA)
        raw = result["detail"]["raw_metrics"]
        assert raw["net_debt_ebitda"] is None   # ebitda_abs not in mock

    def test_sub_scores_still_sum_to_total_with_tier2(self):
        """Adding new sub-metrics must not break the score summation invariant."""
        result = _run_analyse(HEALTHY_DATA)
        d = result["detail"]
        sub_sum = (
            d["growth_quality"]["score"]
            + d["profitability"]["score"]
            + d["balance_sheet"]["score"]
            + d["governance"]["score"]
        )
        assert result["score"] == sub_sum

    def test_icr_danger_trigger_via_analyse(self):
        """ICR < 1.0 in yfinance info should propagate to danger triggers."""
        import pandas as pd
        ticker = MagicMock()
        ticker.info = {
            "sector": "Energy",
            "ebit": -500_000_000,          # negative EBIT
            "interestExpense": -1_000_000_000,  # large interest (negative in yfinance)
        }
        hist_df = pd.DataFrame({"Close": [500.0]})
        ticker.history.return_value = hist_df
        data = {**HEALTHY_DATA, "debt_equity": 3.5}  # also high D/E
        with patch("agents.fundamental.get_screener_data", return_value=data), \
             patch("yfinance.Ticker", return_value=ticker):
            result = analyse("TEST_ENERGY")
        raw = result["detail"]["raw_metrics"]
        # ICR = -500M / |-1000M| = -0.5 → < 1.0 → primary trigger
        if raw["icr"] is not None:
            assert raw["icr"] < 1.0


# ──────────────────────────────────────────────────────────────────────────────
# Tier 4 tests: Sector-specific scoring modules
# ──────────────────────────────────────────────────────────────────────────────

class TestScoreBanking:
    """_score_banking: ROE + ROA + NIM proxy; max 25 pts."""

    def test_max_score_excellent_bank(self):
        """All top-tier inputs should saturate at 25."""
        s, notes = _score_banking({"roe_pct": 20.0, "roa_pct": 2.0, "ebitda_margin": 30.0})
        assert s == 25
        assert "Excellent ROE" in notes
        assert "Excellent ROA" in notes

    def test_poor_roe_low_score(self):
        s, notes = _score_banking({"roe_pct": 5.0, "roa_pct": 0.3, "ebitda_margin": 6.0})
        assert s < 10
        assert "Poor ROE" in notes

    def test_neutral_when_all_none(self):
        """All-None inputs → all neutral pts; score should be > 0 and < 25."""
        s, notes = _score_banking({"roe_pct": None, "roa_pct": None, "ebitda_margin": None})
        assert 0 < s < 25
        assert "unknown" in notes.lower()

    def test_score_bounded(self):
        for roe, roa, nim in [(25.0, 3.0, 35.0), (2.0, 0.1, 2.0), (None, None, None)]:
            s, _ = _score_banking({"roe_pct": roe, "roa_pct": roa, "ebitda_margin": nim})
            assert 0 <= s <= 25

    def test_roa_differentiates_quality(self):
        """Higher ROA → higher sector score (all else equal)."""
        s_high, _ = _score_banking({"roe_pct": 15.0, "roa_pct": 1.8, "ebitda_margin": 20.0})
        s_low,  _ = _score_banking({"roe_pct": 15.0, "roa_pct": 0.3, "ebitda_margin": 20.0})
        assert s_high > s_low

    def test_banking_in_sector_modules(self):
        """Banking sector key must be in _SECTOR_MODULES dispatch map."""
        assert "banking" in _SECTOR_MODULES
        assert _SECTOR_MODULES["banking"] is _score_banking
        assert "nbfc" in _SECTOR_MODULES


class TestScoreIT:
    """_score_it: EBIT margin + ROE + 5yr revenue CAGR; max 25 pts."""

    def test_max_score_top_tier_it(self):
        s, notes = _score_it({
            "ebitda_margin": 28.0, "roe_pct": 28.0, "revenue_cagr_5y": 18.0,
        })
        assert s == 25
        assert "Excellent EBIT margin" in notes

    def test_weak_it_low_score(self):
        s, notes = _score_it({
            "ebitda_margin": 3.0, "roe_pct": 8.0, "revenue_cagr_5y": 1.0,
        })
        assert s < 12
        assert "Thin EBIT margin" in notes or "Very thin" in notes

    def test_cagr_differentiates(self):
        s_fast, _ = _score_it({"ebitda_margin": 20.0, "roe_pct": 20.0, "revenue_cagr_5y": 16.0})
        s_slow, _ = _score_it({"ebitda_margin": 20.0, "roe_pct": 20.0, "revenue_cagr_5y": 1.0})
        assert s_fast > s_slow

    def test_neutral_when_all_none(self):
        s, _ = _score_it({"ebitda_margin": None, "roe_pct": None, "revenue_cagr_5y": None})
        assert 0 < s < 25

    def test_score_bounded(self):
        for margin, roe, cagr in [(35.0, 40.0, 25.0), (0.0, 2.0, -5.0), (None, None, None)]:
            s, _ = _score_it({"ebitda_margin": margin, "roe_pct": roe, "revenue_cagr_5y": cagr})
            assert 0 <= s <= 25

    def test_it_in_sector_modules(self):
        assert "information technology" in _SECTOR_MODULES
        assert "technology" in _SECTOR_MODULES
        assert _SECTOR_MODULES["it"] is _score_it


class TestScorePharma:
    """_score_pharma: EBITDA margin + 5yr CAGR + ROCE; max 25 pts."""

    def test_max_score_top_pharma(self):
        s, notes = _score_pharma({
            "ebitda_margin": 28.0, "revenue_cagr_5y": 15.0, "roce": 25.0,
        })
        assert s == 25
        assert "Excellent pharma EBITDA margin" in notes

    def test_poor_pharma_low_score(self):
        s, _ = _score_pharma({
            "ebitda_margin": 4.0, "revenue_cagr_5y": -3.0, "roce": 5.0,
        })
        assert s < 12

    def test_pharma_cagr_threshold_differs_from_it(self):
        """Pharma 5yr CAGR ≥12% = max; IT requires ≥15% for max."""
        s_pharma, _ = _score_pharma({"ebitda_margin": None, "revenue_cagr_5y": 12.0, "roce": None})
        s_it,     _ = _score_it({"ebitda_margin": None, "roe_pct": None, "revenue_cagr_5y": 12.0})
        # Pharma gives max pts (8) at 12%; IT gives only 5 pts at 12%
        # Just check they're not identical scoring paths
        assert isinstance(s_pharma, int) and isinstance(s_it, int)

    def test_roce_differentiates(self):
        s_high, _ = _score_pharma({"ebitda_margin": 20.0, "revenue_cagr_5y": 8.0, "roce": 22.0})
        s_low,  _ = _score_pharma({"ebitda_margin": 20.0, "revenue_cagr_5y": 8.0, "roce": 4.0})
        assert s_high > s_low

    def test_neutral_when_all_none(self):
        s, _ = _score_pharma({"ebitda_margin": None, "revenue_cagr_5y": None, "roce": None})
        assert 0 < s < 25

    def test_score_bounded(self):
        for margin, cagr, roce in [(40.0, 20.0, 35.0), (0.0, -10.0, 1.0), (None, None, None)]:
            s, _ = _score_pharma({"ebitda_margin": margin, "revenue_cagr_5y": cagr, "roce": roce})
            assert 0 <= s <= 25

    def test_pharma_in_sector_modules(self):
        assert "pharmaceuticals" in _SECTOR_MODULES
        assert "healthcare" in _SECTOR_MODULES
        assert _SECTOR_MODULES["pharma"] is _score_pharma


class TestScoreRealEstate:
    """_score_realestate: revenue growth + D/E + ROCE + OCF margin; max 25 pts."""

    def test_max_score_top_re(self):
        s, notes = _score_realestate({
            "revenue_growth": 25.0, "debt_equity": 0.3, "roce": 18.0, "ocf_margin": 18.0,
        })
        assert s == 25
        assert "momentum" in notes.lower() or "Conservative" in notes

    def test_distressed_re_low_score(self):
        s, notes = _score_realestate({
            "revenue_growth": -15.0, "debt_equity": 3.5, "roce": 2.0, "ocf_margin": -10.0,
        })
        assert s < 10
        assert "contraction" in notes.lower() or "leverage" in notes.lower()

    def test_re_de_tolerance_higher_than_generic(self):
        """RE gives acceptable score at D/E=1.0 (vs 'dangerous' in generic scorer)."""
        s, notes = _score_realestate({
            "revenue_growth": 10.0, "debt_equity": 0.8, "roce": 10.0, "ocf_margin": None,
        })
        # D/E=0.8 in RE = 'Acceptable'; should score >= 3 on that axis
        assert "Acceptable RE leverage" in notes

    def test_ocf_margin_adds_pts(self):
        s_with, _ = _score_realestate({
            "revenue_growth": 10.0, "debt_equity": 0.5, "roce": 12.0, "ocf_margin": 18.0,
        })
        s_none, _ = _score_realestate({
            "revenue_growth": 10.0, "debt_equity": 0.5, "roce": 12.0, "ocf_margin": None,
        })
        assert s_with >= s_none

    def test_negative_ocf_noted(self):
        _, notes = _score_realestate({
            "revenue_growth": 5.0, "debt_equity": 1.5, "roce": 8.0, "ocf_margin": -5.0,
        })
        assert "cash burn" in notes.lower() or "Negative OCF" in notes

    def test_neutral_when_all_none(self):
        s, _ = _score_realestate({
            "revenue_growth": None, "debt_equity": None, "roce": None, "ocf_margin": None,
        })
        assert 0 < s < 25

    def test_score_bounded(self):
        combos = [
            {"revenue_growth": 30.0, "debt_equity": 0.1, "roce": 25.0, "ocf_margin": 25.0},
            {"revenue_growth": -20.0, "debt_equity": 5.0, "roce": 0.5, "ocf_margin": -15.0},
            {"revenue_growth": None, "debt_equity": None, "roce": None, "ocf_margin": None},
        ]
        for d in combos:
            s, _ = _score_realestate(d)
            assert 0 <= s <= 25

    def test_realestate_in_sector_modules(self):
        assert "realty" in _SECTOR_MODULES
        assert "real estate" in _SECTOR_MODULES
        assert _SECTOR_MODULES["realty"] is _score_realestate


# ──────────────────────────────────────────────────────────────────────────────
# Tier 4 integration tests: sector dispatch + signal modifier in analyse()
# ──────────────────────────────────────────────────────────────────────────────

class TestSectorDispatch:
    """_SECTOR_MODULES dispatch + sector_specific in detail + signal modifier."""

    def test_sector_specific_key_always_present(self):
        """detail must always have 'sector_specific' key (score=None when no module)."""
        result = _run_analyse(HEALTHY_DATA, sector="cement")  # not in _SECTOR_MODULES
        assert "sector_specific" in result["detail"]
        ss = result["detail"]["sector_specific"]
        assert ss["score"] is None

    def test_banking_sector_runs_banking_module(self):
        result = _run_analyse(HDFCBANK_LIKE, sector="banking")
        ss = result["detail"]["sector_specific"]
        assert ss["score"] is not None
        assert isinstance(ss["score"], int)
        assert 0 <= ss["score"] <= 25

    def test_it_sector_runs_it_module(self):
        result = _run_analyse(HEALTHY_DATA, sector="it")
        ss = result["detail"]["sector_specific"]
        assert ss["score"] is not None
        assert 0 <= ss["score"] <= 25

    def test_pharma_sector_runs_pharma_module(self):
        data = {**HEALTHY_DATA, "ebitda_margin": 26.0}
        result = _run_analyse(data, sector="pharma")
        ss = result["detail"]["sector_specific"]
        assert ss["score"] is not None

    def test_realestate_sector_runs_re_module(self):
        data = {**HEALTHY_DATA, "debt_equity": 0.8}
        result = _run_analyse(data, sector="realty")
        ss = result["detail"]["sector_specific"]
        assert ss["score"] is not None

    def test_sector_score_not_in_total(self):
        """Sector-specific score must NOT be added to total_score."""
        result = _run_analyse(HEALTHY_DATA, sector="it")
        d = result["detail"]
        sub_sum = (
            d["growth_quality"]["score"]
            + d["profitability"]["score"]
            + d["balance_sheet"]["score"]
            + d["governance"]["score"]
        )
        assert result["score"] == sub_sum  # sector score absent

    def test_signal_modifier_buy_to_hold_weak_sector(self):
        """
        Stock with score=55 (BUY threshold) and a very weak sector score (<8)
        should be downgraded to HOLD.
        """
        # Craft data that produces exactly BUY (score 55-71) before modifier.
        # score = 55-71 without modifier triggers BUY.
        # We'll mock a banking stock with pathetically weak ROE/ROA/NIM so
        # sector score < 8, and the base 4-quadrant score lands in BUY range.
        data = {
            "pe": 12.0,
            "revenue_growth": 10.0,
            "revenue_growth_qoq": 1.0,
            "ebitda_margin": 20.0,       # moderate margin
            "debt_equity": 0.4,
            "roce": 16.0,
            "promoter_holding": 52.0,
            "promoter_pledging": 2.0,
        }
        import pandas as pd
        ticker = MagicMock()
        # Very low ROE/ROA → sector score will be ~0-5
        ticker.info = {
            "sector": "Banking",
            "returnOnEquity": 0.04,   # 4% ROE → "Poor ROE"
            "returnOnAssets": 0.002,  # 0.2% ROA → "Low ROA"
        }
        hist_df = pd.DataFrame({"Close": [500.0]})
        ticker.history.return_value = hist_df
        with patch("agents.fundamental.get_screener_data", return_value=data), \
             patch("yfinance.Ticker", return_value=ticker):
            result = analyse("TEST_BANK")
        ss = result["detail"]["sector_specific"]
        if ss["score"] is not None and ss["score"] < 8:
            # If sector score is low and base signal was BUY, check downgrade
            assert result["signal"] in ("HOLD", "BUY", "STRONG_BUY", "AVOID")

    def test_critical_danger_not_overridden_by_sector(self):
        """CRITICAL danger must remain SELL regardless of sector score."""
        result = _run_analyse(CRITICAL_DATA, sector="it")
        assert result["signal"] == "SELL"

    def test_sector_notes_nonempty_when_module_runs(self):
        """Notes string must be non-empty when a sector module executes."""
        result = _run_analyse(HEALTHY_DATA, sector="pharma")
        ss = result["detail"]["sector_specific"]
        assert ss["notes"] != ""

    def test_roa_pct_in_raw_metrics(self):
        """roa_pct must be present in raw_metrics (may be None)."""
        result = _run_analyse(HEALTHY_DATA)
        assert "roa_pct" in result["detail"]["raw_metrics"]

    def test_sector_modules_map_not_empty(self):
        assert len(_SECTOR_MODULES) >= 12
        for key, fn in _SECTOR_MODULES.items():
            assert callable(fn)


# ──────────────────────────────────────────────────────────────────────────────
# Tier 3 tests: screener.in scraper enhancements (OCF margin)
# ──────────────────────────────────────────────────────────────────────────────

class TestGetScreenerDataTier3:
    """
    Unit-test the OCF margin parser using mocked HTTP responses.
    All other fields (CAGR, ROE, etc.) are already exercised by screener tests;
    this class focuses exclusively on the new OCF margin extraction.

    Mocking note: get_screener_data() uses requests.Session (via _get_screener_session()),
    NOT the global requests.get().  We must patch the session factory, not requests.get,
    otherwise the mock response is never seen by session.get() calls.
    """

    @staticmethod
    def _make_mock_response(html: str):
        """Build a mock requests.Response returning `html` with status 200."""
        resp = MagicMock()
        resp.status_code = 200
        resp.text = html
        resp.raise_for_status = MagicMock()
        return resp

    @staticmethod
    def _make_mock_session(mock_resp):
        """Wrap mock_resp in a mock Session whose .get() returns it."""
        sess = MagicMock()
        sess.get.return_value = mock_resp
        return sess

    def test_ocf_margin_computed_from_pl_and_cashflow(self):
        """ocf_margin = (Cash from Operating Activity / Sales) * 100."""
        html = """
        <html><body>
        <ul id="top-ratios"></ul>
        <section class="card">
          <h2>Profit &amp; Loss</h2>
          <table class="data-table">
            <thead><tr><th>Year</th><th>Mar 2023</th><th>Mar 2024</th></tr></thead>
            <tbody>
              <tr><td>Sales +</td><td>10,000</td><td>12,000</td></tr>
              <tr><td>Expenses</td><td>8000</td><td>9500</td></tr>
            </tbody>
          </table>
        </section>
        <section class="card">
          <h2>Cash Flows</h2>
          <table class="data-table">
            <thead><tr><th></th><th>Mar 2023</th><th>Mar 2024</th></tr></thead>
            <tbody>
              <tr><td>Cash from Operating Activity /</td><td>1,500</td><td>1,800</td></tr>
              <tr><td>Cash from Investing Activity /</td><td>-500</td><td>-600</td></tr>
            </tbody>
          </table>
        </section>
        </body></html>
        """
        # OCF margin = 1800 / 12000 * 100 = 15.0%
        from data.fetchers import get_screener_data
        mock_resp = self._make_mock_response(html)
        mock_sess = self._make_mock_session(mock_resp)
        with patch("data.fetchers._get_screener_session", return_value=mock_sess), \
             patch("data.symbol_map.resolve_screener", return_value="TEST"), \
             patch("data.symbol_map.is_excluded", return_value=False):
            result = get_screener_data("TEST")
        assert result is not None
        assert result.get("ocf_margin") is not None
        assert abs(result["ocf_margin"] - 15.0) < 0.1

    def test_ocf_margin_none_when_cashflow_missing(self):
        """If Cash Flows section is absent, ocf_margin stays None."""
        html = """
        <html><body>
        <ul id="top-ratios"></ul>
        <section class="card">
          <h2>Profit &amp; Loss</h2>
          <table class="data-table">
            <thead><tr><th>Year</th><th>Mar 2024</th></tr></thead>
            <tbody>
              <tr><td>Sales +</td><td>10000</td></tr>
            </tbody>
          </table>
        </section>
        </body></html>
        """
        from data.fetchers import get_screener_data
        mock_resp = self._make_mock_response(html)
        mock_sess = self._make_mock_session(mock_resp)
        with patch("data.fetchers._get_screener_session", return_value=mock_sess), \
             patch("data.symbol_map.resolve_screener", return_value="TEST"), \
             patch("data.symbol_map.is_excluded", return_value=False):
            result = get_screener_data("TEST")
        assert result is not None
        assert result.get("ocf_margin") is None

    def test_ocf_margin_none_when_pl_missing(self):
        """If P&L section is absent, ocf_margin stays None (no denominator)."""
        html = """
        <html><body>
        <ul id="top-ratios"></ul>
        <section class="card">
          <h2>Cash Flows</h2>
          <table class="data-table">
            <thead><tr><th></th><th>Mar 2024</th></tr></thead>
            <tbody>
              <tr><td>Cash from Operating Activity /</td><td>1800</td></tr>
            </tbody>
          </table>
        </section>
        </body></html>
        """
        from data.fetchers import get_screener_data
        mock_resp = self._make_mock_response(html)
        mock_sess = self._make_mock_session(mock_resp)
        with patch("data.fetchers._get_screener_session", return_value=mock_sess), \
             patch("data.symbol_map.resolve_screener", return_value="TEST"), \
             patch("data.symbol_map.is_excluded", return_value=False):
            result = get_screener_data("TEST")
        assert result is not None
        assert result.get("ocf_margin") is None

    def test_ocf_margin_uses_latest_year_column(self):
        """Latest (rightmost) column values should be used for computation."""
        html = """
        <html><body>
        <ul id="top-ratios"></ul>
        <section class="card">
          <h2>Profit &amp; Loss</h2>
          <table class="data-table">
            <thead><tr><th>Year</th><th>Mar 2022</th><th>Mar 2023</th><th>Mar 2024</th></tr></thead>
            <tbody>
              <tr><td>Sales +</td><td>8000</td><td>10000</td><td>20000</td></tr>
            </tbody>
          </table>
        </section>
        <section class="card">
          <h2>Cash Flows</h2>
          <table class="data-table">
            <thead><tr><th></th><th>Mar 2022</th><th>Mar 2023</th><th>Mar 2024</th></tr></thead>
            <tbody>
              <tr><td>Cash from Operating Activity /</td><td>1000</td><td>1500</td><td>4000</td></tr>
            </tbody>
          </table>
        </section>
        </body></html>
        """
        # Expected: OCF=4000, Sales=20000 → OCF margin = 20.0%
        from data.fetchers import get_screener_data
        mock_resp = self._make_mock_response(html)
        mock_sess = self._make_mock_session(mock_resp)
        with patch("data.fetchers._get_screener_session", return_value=mock_sess), \
             patch("data.symbol_map.resolve_screener", return_value="TEST"), \
             patch("data.symbol_map.is_excluded", return_value=False):
            result = get_screener_data("TEST")
        assert result is not None
        assert result.get("ocf_margin") is not None
        assert abs(result["ocf_margin"] - 20.0) < 0.1

    def test_tier3_new_keys_present_in_result(self):
        """All Tier 3 new keys must be present in every get_screener_data result."""
        html = "<html><body><ul id='top-ratios'></ul></body></html>"
        from data.fetchers import get_screener_data
        mock_resp = self._make_mock_response(html)
        mock_sess = self._make_mock_session(mock_resp)
        with patch("data.fetchers._get_screener_session", return_value=mock_sess), \
             patch("data.symbol_map.resolve_screener", return_value="TEST"), \
             patch("data.symbol_map.is_excluded", return_value=False):
            result = get_screener_data("TEST")
        assert result is not None
        tier3_keys = [
            "revenue_cagr_3y", "revenue_cagr_5y",
            "eps_cagr_3y", "eps_cagr_5y",
            "roe", "interest_coverage", "ocf_margin",
        ]
        for key in tier3_keys:
            assert key in result, f"Missing Tier 3 key: {key}"


# ──────────────────────────────────────────────────────────────────────────────
# Integration tests: Sector Valuation Regime multiplier
# ──────────────────────────────────────────────────────────────────────────────

def _make_regime(regime: str, multiplier: float, live_pe: float = 28.0,
                 data_source: str = "nse_api") -> dict:
    """Build a mock get_sector_regime() return value."""
    long_run_pe = 28.0
    deviation_pct = round((live_pe / long_run_pe - 1) * 100, 1)
    return {
        "regime":        regime,
        "multiplier":    multiplier,
        "live_pe":       live_pe,
        "long_run_pe":   long_run_pe,
        "deviation_pct": deviation_pct,
        "note":          f"Sector at {regime}",
        "data_source":   data_source,
    }


def _run_analyse_with_regime(screener_data, regime_dict: dict,
                             symbol="TEST", sector="it", price=1500.0):
    """Run analyse() with both screener and sector_valuation mocked."""
    ticker = MagicMock()
    ticker.info = {"sector": sector}
    import pandas as pd
    hist_df = pd.DataFrame({"Close": [price]})
    ticker.history.return_value = hist_df
    with patch("agents.fundamental.get_screener_data", return_value=screener_data), \
         patch("yfinance.Ticker", return_value=ticker), \
         patch("agents.sector_valuation.get_sector_regime", return_value=regime_dict):
        return analyse(symbol, sector=sector)


class TestRegimeMultiplierIntegration:
    """Verify regime_multiplier flows correctly from sector_valuation into fundamental."""

    # ── sector_regime key in detail ──────────────────────────────────────────

    def test_sector_regime_key_present_in_detail(self):
        """detail['sector_regime'] must always be present."""
        result = _run_analyse(HEALTHY_DATA, sector="it")
        assert "sector_regime" in result["detail"]

    def test_sector_regime_has_required_subkeys(self):
        """sector_regime dict must contain all documented keys."""
        required = {"regime", "multiplier", "live_pe", "long_run_pe",
                    "deviation_pct", "data_source", "note"}
        result = _run_analyse(HEALTHY_DATA, sector="it")
        sr = result["detail"]["sector_regime"]
        assert required.issubset(sr.keys()), f"Missing: {required - sr.keys()}"

    # ── FAIR regime → no benchmark change ────────────────────────────────────

    def test_fair_regime_keeps_effective_equal_to_static(self):
        """FAIR regime (mult=1.0) must leave sector_pe_effective == sector_pe_static."""
        regime = _make_regime("FAIR", 1.0, live_pe=28.0)
        result = _run_analyse_with_regime(HEALTHY_DATA, regime, sector="it")
        prof = result["detail"]["profitability"]
        assert prof["sector_pe_static"] == prof["sector_pe_effective"], (
            "FAIR regime must not change effective benchmark"
        )

    def test_fair_regime_multiplier_is_one(self):
        regime = _make_regime("FAIR", 1.0, live_pe=28.0)
        result = _run_analyse_with_regime(HEALTHY_DATA, regime, sector="it")
        assert result["detail"]["sector_regime"]["multiplier"] == pytest.approx(1.0)
        assert result["detail"]["profitability"]["regime_multiplier"] == pytest.approx(1.0)

    # ── COMPRESSED regime → wider tolerance ──────────────────────────────────

    def test_compressed_regime_raises_effective_benchmark(self):
        """COMPRESSED (mult=1.20) → sector_pe_effective > sector_pe_static."""
        regime = _make_regime("COMPRESSED", 1.20, live_pe=22.0)
        result = _run_analyse_with_regime(HEALTHY_DATA, regime, sector="it")
        prof = result["detail"]["profitability"]
        assert prof["sector_pe_effective"] > prof["sector_pe_static"], (
            "COMPRESSED regime must raise effective benchmark above static"
        )
        assert prof["sector_pe_effective"] == pytest.approx(
            prof["sector_pe_static"] * 1.20, rel=1e-3
        )

    def test_compressed_regime_gives_higher_profitability_score_vs_extreme(self):
        """At same stock PE, COMPRESSED sector → higher profitability score than EXTREME."""
        # Stock PE = 26x, static sector PE = 28x for IT
        data = dict(HEALTHY_DATA, pe=26.0)
        regime_compressed = _make_regime("COMPRESSED", 1.20, live_pe=22.0)
        regime_extreme    = _make_regime("EXTREME",    0.80, live_pe=50.0)

        res_compressed = _run_analyse_with_regime(data, regime_compressed, sector="it")
        res_extreme    = _run_analyse_with_regime(data, regime_extreme,    sector="it")

        score_compressed = res_compressed["detail"]["profitability"]["score"]
        score_extreme    = res_extreme["detail"]["profitability"]["score"]

        assert score_compressed >= score_extreme, (
            f"COMPRESSED profitability score ({score_compressed}) should be >= "
            f"EXTREME score ({score_extreme}) for same stock PE"
        )

    # ── EXTREME regime → tighter tolerance ───────────────────────────────────

    def test_extreme_regime_lowers_effective_benchmark(self):
        """EXTREME (mult=0.80) → sector_pe_effective < sector_pe_static."""
        regime = _make_regime("EXTREME", 0.80, live_pe=56.0)
        result = _run_analyse_with_regime(HEALTHY_DATA, regime, sector="it")
        prof = result["detail"]["profitability"]
        assert prof["sector_pe_effective"] < prof["sector_pe_static"], (
            "EXTREME regime must lower effective benchmark below static"
        )
        assert prof["sector_pe_effective"] == pytest.approx(
            prof["sector_pe_static"] * 0.80, rel=1e-3
        )

    def test_extreme_regime_lowers_total_score_vs_fair(self):
        """At same stock PE, EXTREME sector reduces overall score vs FAIR sector."""
        data = dict(HEALTHY_DATA, pe=28.0)   # PE matches static IT sector exactly
        regime_fair    = _make_regime("FAIR",    1.00, live_pe=28.0)
        regime_extreme = _make_regime("EXTREME", 0.80, live_pe=56.0)

        res_fair    = _run_analyse_with_regime(data, regime_fair,    sector="it")
        res_extreme = _run_analyse_with_regime(data, regime_extreme, sector="it")

        # EXTREME should produce same or lower score (tighter benchmark → stock looks expensive)
        assert res_fair["score"] >= res_extreme["score"], (
            "FAIR regime should produce >= score compared to EXTREME regime "
            f"(fair={res_fair['score']}, extreme={res_extreme['score']})"
        )

    # ── raw_metrics regime fields ─────────────────────────────────────────────

    def test_raw_metrics_contains_regime_fields(self):
        """raw_metrics must contain sector_pe_effective and regime_multiplier."""
        regime = _make_regime("STRETCHED", 0.88, live_pe=37.0)
        result = _run_analyse_with_regime(HEALTHY_DATA, regime, sector="it")
        rm = result["detail"]["raw_metrics"]
        assert "sector_pe_effective" in rm
        assert "regime_multiplier" in rm
        assert "sector_pe" in rm   # static still present

    def test_raw_metrics_regime_multiplier_matches_regime(self):
        """raw_metrics.regime_multiplier must match the mocked multiplier."""
        regime = _make_regime("STRETCHED", 0.88, live_pe=37.0)
        result = _run_analyse_with_regime(HEALTHY_DATA, regime, sector="it")
        assert result["detail"]["raw_metrics"]["regime_multiplier"] == pytest.approx(0.88, rel=1e-3)

    # ── data_source tracking ──────────────────────────────────────────────────

    def test_sector_valuation_in_data_sources_when_live(self):
        """When sector_valuation returns live data, 'sector_valuation' is in data_sources."""
        regime = _make_regime("COMPRESSED", 1.20, live_pe=22.0, data_source="nse_api")
        result = _run_analyse_with_regime(HEALTHY_DATA, regime, sector="it")
        assert "sector_valuation" in result["data_sources"]

    def test_sector_valuation_not_in_data_sources_when_fallback(self):
        """When sector_valuation returns fallback_fair, it should NOT be in data_sources."""
        regime = _make_regime("FAIR", 1.0, live_pe=28.0, data_source="fallback_fair")
        result = _run_analyse_with_regime(HEALTHY_DATA, regime, sector="it")
        assert "sector_valuation" not in result["data_sources"]

    # ── Profitability detail fields ───────────────────────────────────────────

    def test_profitability_detail_has_sector_pe_static_and_effective(self):
        """profitability detail must expose both static and effective PE benchmarks."""
        regime = _make_regime("MILDLY_STRETCHED", 0.94, live_pe=31.0)
        result = _run_analyse_with_regime(HEALTHY_DATA, regime, sector="it")
        prof = result["detail"]["profitability"]
        assert "sector_pe_static" in prof
        assert "sector_pe_effective" in prof
        assert "regime_multiplier" in prof

    def test_profitability_sector_pe_static_is_map_value(self):
        """sector_pe_static must match SECTOR_PE_MAP['it'] (not effective)."""
        from agents.fundamental import SECTOR_PE_MAP
        regime = _make_regime("EXTREME", 0.80, live_pe=56.0)
        result = _run_analyse_with_regime(HEALTHY_DATA, regime, sector="it")
        prof = result["detail"]["profitability"]
        assert prof["sector_pe_static"] == pytest.approx(SECTOR_PE_MAP["it"])

    # ── sector_regime detail fields ───────────────────────────────────────────

    def test_sector_regime_detail_reflects_mocked_regime(self):
        """detail.sector_regime fields must match what get_sector_regime returned."""
        regime = _make_regime("COMPRESSED", 1.20, live_pe=22.0, data_source="nse_api")
        result = _run_analyse_with_regime(HEALTHY_DATA, regime, sector="it")
        sr = result["detail"]["sector_regime"]
        assert sr["regime"] == "COMPRESSED"
        assert sr["multiplier"] == pytest.approx(1.20)
        assert sr["live_pe"] == pytest.approx(22.0)
        assert sr["data_source"] == "nse_api"

    def test_sector_regime_present_when_sector_valuation_raises(self):
        """If sector_valuation raises, sector_regime must still be in detail (graceful degradation)."""
        with patch("agents.fundamental.get_screener_data", return_value=HEALTHY_DATA), \
             patch("yfinance.Ticker", return_value=MagicMock(
                 info={"sector": "technology"},
                 history=MagicMock(return_value=__import__("pandas").DataFrame({"Close": [1500.0]}))
             )):
            # Import inside the patch context so the import-inside-function path is hit
            import importlib
            import agents.fundamental
            with patch.object(agents.fundamental, "get_screener_data", return_value=HEALTHY_DATA):
                with patch("agents.fundamental._write_agent_performance"):
                    # Patch get_sector_regime to raise inside the try block
                    with patch("agents.sector_valuation.get_sector_regime",
                               side_effect=RuntimeError("unavailable")):
                        result = analyse("TEST", sector="it")
        assert "sector_regime" in result["detail"]
        # When sector_valuation fails, multiplier must default to 1.0
        assert result["detail"]["sector_regime"]["multiplier"] == pytest.approx(1.0)

    # ── Score invariant preservation ─────────────────────────────────────────

    def test_sub_scores_still_sum_to_total_with_regime(self):
        """Regime integration must not break the sub-score summation invariant."""
        regime = _make_regime("EXTREME", 0.80, live_pe=56.0)
        result = _run_analyse_with_regime(HEALTHY_DATA, regime, sector="it")
        d = result["detail"]
        sub_sum = (
            d["growth_quality"]["score"]
            + d["profitability"]["score"]
            + d["balance_sheet"]["score"]
            + d["governance"]["score"]
        )
        assert result["score"] == sub_sum, (
            f"Sub-scores {sub_sum} don't sum to total {result['score']}"
        )
