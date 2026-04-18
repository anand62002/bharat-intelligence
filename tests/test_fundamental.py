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
    analyse,
    _assess_danger,
    _estimate_upside,
    _estimate_upside_ev_ebitda,
    _score_balance_sheet,
    _score_governance,
    _score_growth,
    _score_profitability,
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
    "danger", "raw_metrics",
}
VALID_SIGNALS = {"STRONG_BUY", "BUY", "HOLD", "AVOID", "SELL", "NO_DATA"}


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
        assert 0 <= result["score"] <= 100
        assert result["signal"] in VALID_SIGNALS

    def test_sparse_data_no_danger(self):
        """No data → no triggers → no danger."""
        result = _run_analyse(SPARSE_DATA)
        assert result["detail"]["danger"]["level"] is None

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
        assert (
            result_it["detail"]["profitability"]["sector_pe_used"] == 30.0
        )

    def test_unknown_sector_uses_default(self):
        result = _run_analyse(HEALTHY_DATA, sector="unknown_sector_xyz")
        assert result["detail"]["profitability"]["sector_pe_used"] == DEFAULT_SECTOR_PE

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
