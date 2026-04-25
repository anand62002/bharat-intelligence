"""
tests/test_warren_bot.py
pytest suite for agents/warren_bot.py

Tests the Business Quality Assessment Agent (Buffett + Jhunjhunwala model).
All external dependencies are mocked: screener.in, yfinance, OHLCV, Anthropic API,
and Supabase. No real network calls are made.

Run from project root:
    pytest tests/test_warren_bot.py -v
"""

import os
import sys
from unittest.mock import MagicMock, patch, PropertyMock

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.warren_bot import (  # noqa: E402
    AGENT_NAME,
    analyse,
    _infer_moat_type,
    _score_moat,
    _score_roce,
    _score_management,
    _score_earnings,
    _calculate_owner_earnings,
    _dcf_valuation,
    _jhunjhunwala_bonus,
    _check_disqualifiers,
    _generate_commentary,
)


# ─── Mock helpers ─────────────────────────────────────────────────────────────

def _mock_ohlcv(price: float = 2500.0) -> pd.DataFrame:
    """
    Return a minimal DataFrame with a single row and 'Close' column.
    Mimics the shape returned by get_ohlcv().
    """
    return pd.DataFrame({"Close": [price]})


def _mock_yf_info(market_cap_cr: float = 5000.0, sector: str = "Consumer Goods") -> MagicMock:
    """
    Return a MagicMock whose .info property returns a realistic yfinance dict.

    Args:
        market_cap_cr: Market cap in Crores (converted to raw rupees internally).
        sector:        yfinance sector string.
    """
    mock_ticker = MagicMock()
    mock_ticker.info = {
        "marketCap":        market_cap_cr * 1e7,   # convert Cr → rupees
        "sharesOutstanding": 100e6,                  # 10 Cr shares (100M / 1e7 = 10)
        "sector":           sector,
        "priceToBook":      4.5,
        "trailingPE":       28.0,
        "returnOnEquity":   0.22,
        "currentPrice":     2500.0,
    }
    return mock_ticker


# ─── Shared mock screener data ────────────────────────────────────────────────

def _high_quality_snap() -> dict:
    """Screener snapshot for a high-quality consumer business."""
    return {
        "pe":               25.0,
        "roce":             26.0,
        "roe":              24.0,
        "ebitda_margin":    24.0,
        "debt_equity":      0.2,
        "promoter_holding": 72.0,
        "promoter_pledging": 1.0,
        "revenue_cagr_3y":  16.0,
        "revenue_cagr_5y":  18.0,
        "eps_cagr_3y":      18.0,
        "eps_cagr_5y":      20.0,
        "ocf_margin":       18.0,
        "interest_coverage": 15.0,
        "revenue_growth":   17.0,
        "promoter_pledging": 1.0,
    }


def _high_quality_hist() -> dict:
    """10-year screener history for a high-quality consumer business."""
    years = [
        "Mar 2015", "Mar 2016", "Mar 2017", "Mar 2018", "Mar 2019",
        "Mar 2020", "Mar 2021", "Mar 2022", "Mar 2023", "Mar 2024",
    ]
    return {
        "years":                    years,
        "ebitda_margins":           [21, 22, 23, 22, 24, 23, 25, 24, 26, 27],
        "pat_history":              [100, 120, 130, 145, 160, 180, 170, 200, 230, 260],
        "eps_history":              [10, 12, 13, 14.5, 16, 18, 17, 20, 23, 26],
        "roce_history":             [22, 24, 23, 25, 24, 26, 25, 27, 26, 28],
        "promoter_holding_history": [70, 71, 71, 72, 72, 73, 73, 74, 74, 74],
        "dividend_payout_history":  [20, 22, 23, 25, 26, 28, 28, 30, 30, 32],
        "depreciation_history":     [20, 22, 24, 26, 28, 30, 32, 34, 36, 38],
        "capex_history":            [30, 35, 38, 40, 45, 50, 55, 55, 60, 65],
        "revenue_history":          [800, 920, 1050, 1200, 1380, 1540, 1460, 1750, 2010, 2280],
        "roe_history":              [20, 21, 22, 23, 22, 24, 23, 25, 24, 26],
        "promoter_holding_quarters": ["Jun 2022", "Sep 2022", "Dec 2022", "Mar 2023",
                                      "Jun 2023", "Sep 2023", "Dec 2023", "Mar 2024",
                                      "Jun 2024", "Sep 2024"],
        "years_available":          10,
    }


# ─── Test 1: High quality business scores above 75 ───────────────────────────

class TestHighQualityBusiness:
    """Test that a genuinely high-quality business earns score >= 75."""

    @patch("agents.warren_bot._log_to_supabase")
    @patch("agents.warren_bot._generate_commentary")
    @patch("agents.warren_bot.yf")
    @patch("agents.warren_bot.get_ohlcv")
    @patch("agents.warren_bot.get_screener_history")
    @patch("agents.warren_bot.get_screener_data")
    def test_high_quality_business_score_above_75(
        self,
        mock_snap,
        mock_hist,
        mock_ohlcv,
        mock_yf,
        mock_commentary,
        mock_supabase,
    ):
        """
        A business with 10 years of 20%+ margins, consistent ROCE above 20%,
        low pledging, and growing earnings should score >= 75 and not be AVOID.
        """
        # Arrange
        mock_snap.return_value = _high_quality_snap()
        mock_hist.return_value = _high_quality_hist()
        mock_ohlcv.return_value = _mock_ohlcv(price=2500.0)
        mock_yf.Ticker.return_value = _mock_yf_info(
            market_cap_cr=10000.0, sector="Consumer Goods"
        )
        mock_commentary.return_value = (
            "This is a wonderful business at a fair price.",
            "The only concern is the current valuation premium.",
        )
        mock_supabase.return_value = None

        # Act
        result = analyse("TESTCONSUMER.NS")

        # Assert
        assert result["agent_name"] == AGENT_NAME
        assert result["business_quality_score"] >= 75, (
            f"Expected score >= 75, got {result['business_quality_score']}. "
            f"Dimension scores — moat:{result['moat_strength_score']}, "
            f"roce:{result['roce_score']}, mgmt:{result['management_score']}, "
            f"earn:{result['earnings_score']}, val:{result['valuation_score']}"
        )
        assert result["signal"] != "AVOID", (
            f"High-quality business should not be AVOID, got signal={result['signal']}"
        )
        assert result["moat_type"] != "NONE", (
            "Expected a recognised moat type for a high-margin consumer business"
        )


# ─── Test 2: High pledging disqualifies promoter ─────────────────────────────

class TestHighPledging:
    """Test that promoter pledging > 30% triggers the DISQUALIFIED management score."""

    @patch("agents.warren_bot._log_to_supabase")
    @patch("agents.warren_bot._generate_commentary")
    @patch("agents.warren_bot.yf")
    @patch("agents.warren_bot.get_ohlcv")
    @patch("agents.warren_bot.get_screener_history")
    @patch("agents.warren_bot.get_screener_data")
    def test_high_pledging_disqualifies_promoter(
        self,
        mock_snap,
        mock_hist,
        mock_ohlcv,
        mock_yf,
        mock_commentary,
        mock_supabase,
    ):
        """
        When promoter pledging is 35%, the management score must be 0
        and promoter_quality must be 'DISQUALIFIED'.
        """
        # Arrange — all other metrics decent, pledging is the red flag
        snap = _high_quality_snap()
        snap["promoter_pledging"] = 35.0   # above 30% threshold

        hist = _high_quality_hist()

        mock_snap.return_value = snap
        mock_hist.return_value = hist
        mock_ohlcv.return_value = _mock_ohlcv(price=2500.0)
        mock_yf.Ticker.return_value = _mock_yf_info(
            market_cap_cr=10000.0, sector="Consumer Goods"
        )
        mock_commentary.return_value = (
            "Strong business but governance risk is elevated.",
            "Pledging at 35% is a deal-breaker for any serious investor.",
        )
        mock_supabase.return_value = None

        # Act
        result = analyse("PLEDGETEST.NS")

        # Assert
        assert result["management_score"] == 0, (
            f"Pledging 35% should set management_score=0, got {result['management_score']}"
        )
        assert result["promoter_quality"] == "DISQUALIFIED", (
            f"Expected 'DISQUALIFIED', got '{result['promoter_quality']}'"
        )


# ─── Test 3: Missing history reduces confidence ───────────────────────────────

class TestMissingHistoryReducesConfidence:
    """Test that unavailable screener history lowers confidence and adds data gaps."""

    @patch("agents.warren_bot._log_to_supabase")
    @patch("agents.warren_bot._generate_commentary")
    @patch("agents.warren_bot.yf")
    @patch("agents.warren_bot.get_ohlcv")
    @patch("agents.warren_bot.get_screener_history")
    @patch("agents.warren_bot.get_screener_data")
    def test_missing_history_reduces_confidence(
        self,
        mock_snap,
        mock_hist,
        mock_ohlcv,
        mock_yf,
        mock_commentary,
        mock_supabase,
    ):
        """
        When get_screener_history returns None, confidence must be < 70
        and data_gaps must contain at least one entry.
        """
        # Arrange — history fetch fails, only minimal snapshot available
        minimal_snap = {
            "pe":               22.0,
            "roce":             18.0,
            "roe":              15.0,
            "ebitda_margin":    16.0,
            "debt_equity":      0.5,
            "promoter_holding": 55.0,
            "promoter_pledging": 2.0,
            "revenue_cagr_5y":  12.0,
            "eps_cagr_5y":      10.0,
            "ocf_margin":       8.0,
            "interest_coverage": 8.0,
            "revenue_growth":   11.0,
        }
        mock_snap.return_value = minimal_snap
        mock_hist.return_value = None   # <— fetch failed

        mock_ohlcv.return_value = _mock_ohlcv(price=500.0)
        mock_yf.Ticker.return_value = _mock_yf_info(
            market_cap_cr=2000.0, sector="Consumer Goods"
        )
        mock_commentary.return_value = (
            "Limited data makes a full assessment difficult.",
            "Lack of historical data is itself a risk signal.",
        )
        mock_supabase.return_value = None

        # Act
        result = analyse("NOHISTORY.NS")

        # Assert
        assert result["confidence"] < 70, (
            f"Expected confidence < 70 when history unavailable, got {result['confidence']}"
        )
        assert len(result["data_gaps"]) > 0, (
            "Expected at least one data_gap entry when screener history is None"
        )
        assert "screener_history_unavailable" in result["data_gaps"], (
            "Expected 'screener_history_unavailable' in data_gaps"
        )


# ─── Test 4: Output contains all required keys ───────────────────────────────

class TestOutputContainsAllRequiredKeys:
    """Test that the result dict always contains every key the orchestrator expects."""

    REQUIRED_KEYS = [
        "agent_name",
        "symbol",
        "signal",
        "score",
        "business_quality_score",
        "conviction_rating",
        "moat_type",
        "moat_strength_score",
        "roce_score",
        "management_score",
        "earnings_score",
        "valuation_score",
        "intrinsic_value_per_share",
        "current_price",
        "margin_of_safety_pct",
        "ten_year_eps_cagr",
        "roce_avg_10yr",
        "promoter_quality",
        "india_consumption_play",
        "early_penetration_play",
        "jhunjhunwala_cyclical_flag",
        "why_buffett_would_like",
        "why_buffett_would_pass",
        "key_risks",
        "detail",
        "confidence",
        "data_sources",
        "data_gaps",
    ]

    @patch("agents.warren_bot._log_to_supabase")
    @patch("agents.warren_bot._generate_commentary")
    @patch("agents.warren_bot.yf")
    @patch("agents.warren_bot.get_ohlcv")
    @patch("agents.warren_bot.get_screener_history")
    @patch("agents.warren_bot.get_screener_data")
    def test_output_contains_all_required_keys(
        self,
        mock_snap,
        mock_hist,
        mock_ohlcv,
        mock_yf,
        mock_commentary,
        mock_supabase,
    ):
        """
        The analyse() return dict must contain every orchestrator-required key,
        even when data is partial.
        """
        # Arrange — use high-quality data for a stable baseline
        mock_snap.return_value = _high_quality_snap()
        mock_hist.return_value = _high_quality_hist()
        mock_ohlcv.return_value = _mock_ohlcv(price=1800.0)
        mock_yf.Ticker.return_value = _mock_yf_info(
            market_cap_cr=7500.0, sector="Consumer Defensive"
        )
        mock_commentary.return_value = (
            "A wonderful business at a fair price.",
            "The valuation leaves little room for error.",
        )
        mock_supabase.return_value = None

        # Act
        result = analyse("KEYTEST.NS")

        # Assert — every required key must be present
        missing = [k for k in self.REQUIRED_KEYS if k not in result]
        assert missing == [], (
            f"analyse() result is missing required keys: {missing}"
        )

        # Type checks for critical fields
        assert isinstance(result["agent_name"], str)
        assert isinstance(result["signal"], str)
        assert result["signal"] in ("QUALITY_BUY", "WATCHLIST", "AVOID")
        assert isinstance(result["score"], int)
        assert 0 <= result["score"] <= 100
        assert isinstance(result["key_risks"], list)
        assert isinstance(result["data_gaps"], list)
        assert isinstance(result["data_sources"], list)
        assert isinstance(result["confidence"], (int, float))
        assert 0 <= result["confidence"] <= 100


# ─── Test 5: Hard disqualifier sets AVOID signal ─────────────────────────────

class TestHardDisqualifierSetsAvoidSignal:
    """Test that a company below the minimum market cap threshold gets AVOID."""

    @patch("agents.warren_bot._log_to_supabase")
    @patch("agents.warren_bot._generate_commentary")
    @patch("agents.warren_bot.yf")
    @patch("agents.warren_bot.get_ohlcv")
    @patch("agents.warren_bot.get_screener_history")
    @patch("agents.warren_bot.get_screener_data")
    def test_hard_disqualifier_sets_avoid_signal(
        self,
        mock_snap,
        mock_hist,
        mock_ohlcv,
        mock_yf,
        mock_commentary,
        mock_supabase,
    ):
        """
        When market cap is below MIN_MARKET_CAP_CR (200 Cr), the signal must
        be AVOID regardless of otherwise good fundamentals.
        """
        # Arrange — deliberately tiny market cap (50 Cr)
        mock_snap.return_value = _high_quality_snap()
        mock_hist.return_value = _high_quality_hist()
        mock_ohlcv.return_value = _mock_ohlcv(price=50.0)

        # market_cap_cr = 50 → below 200 Cr minimum
        tiny_cap_info = {
            "marketCap":         50 * 1e7,   # 50 Cr in rupees
            "sharesOutstanding": 10e6,
            "sector":            "Consumer Goods",
            "priceToBook":       2.0,
            "trailingPE":        18.0,
            "returnOnEquity":    0.20,
            "currentPrice":      50.0,
        }
        mock_ticker = MagicMock()
        mock_ticker.info = tiny_cap_info
        mock_yf.Ticker.return_value = mock_ticker

        mock_commentary.return_value = (
            "An interesting micro-cap, but too small for our portfolio.",
            "Market cap below our minimum threshold is an automatic disqualifier.",
        )
        mock_supabase.return_value = None

        # Act
        result = analyse("TINYCAP.NS")

        # Assert
        assert result["signal"] == "AVOID", (
            f"Expected signal='AVOID' for market_cap < 200 Cr, got '{result['signal']}'"
        )
        # There should be a disqualifier in key_risks or data_gaps referencing the cap
        disqualifier_mentioned = any(
            "BELOW_MIN_MARKET_CAP" in str(r) or "200" in str(r)
            for r in result["key_risks"]
        )
        assert disqualifier_mentioned, (
            f"Expected market cap disqualifier in key_risks, got: {result['key_risks']}"
        )


# ─── Unit tests for helper functions ─────────────────────────────────────────

class TestHelperFunctions:
    """Unit tests for pure scoring helpers — no mocking needed."""

    # ── _infer_moat_type ──────────────────────────────────────────────────────

    def test_infer_moat_brand_consumer_high_margin(self):
        assert _infer_moat_type("Consumer Goods", 22.0) == "BRAND"

    def test_infer_moat_switching_costs_software(self):
        assert _infer_moat_type("Information Technology", 24.0) == "SWITCHING_COSTS"

    def test_infer_moat_none_low_margin(self):
        assert _infer_moat_type("Consumer Goods", 10.0) == "NONE"

    def test_infer_moat_network_effect_exchange(self):
        assert _infer_moat_type("Stock Exchange Platform", 30.0) == "NETWORK_EFFECT"

    def test_infer_moat_regulatory_banking(self):
        assert _infer_moat_type("Banking", 12.0) == "REGULATORY_LICENCE"

    # ── _score_moat ───────────────────────────────────────────────────────────

    def test_moat_score_returns_20_for_7plus_years_above_20pct(self):
        margins = [21, 22, 23, 22, 24, 23, 25, 24, 26, 27]
        score, moat_type = _score_moat(margins, "Consumer Goods")
        assert score == 20
        assert moat_type != "NONE"

    def test_moat_score_neutral_on_empty(self):
        score, moat_type = _score_moat([], "Technology")
        assert score == 8
        assert moat_type == "NONE"

    def test_moat_declining_3_consecutive_years_returns_zero(self):
        # Values include a run of 3 strictly declining years: 24 > 20 > 15 > 10
        margins = [22, 23, 24, 22, 24, 22, 24, 20, 15, 10]
        score, moat_type = _score_moat(margins, "Metals")
        assert score == 0
        assert moat_type == "NONE"

    # ── _score_roce ───────────────────────────────────────────────────────────

    def test_roce_score_20_for_7plus_years_above_20pct(self):
        roce_hist = [22, 24, 23, 25, 24, 26, 25, 27, 26, 28]
        score, avg = _score_roce(roce_hist, de_ratio=0.2)
        assert score == 20
        assert avg is not None and avg > 20

    def test_roce_penalty_for_high_de(self):
        roce_hist = [22, 24, 23, 25, 24, 26, 25, 27, 26, 28]
        score_low_de, _ = _score_roce(roce_hist, de_ratio=0.2)
        score_high_de, _ = _score_roce(roce_hist, de_ratio=2.0)
        assert score_high_de == score_low_de - 5

    def test_roce_neutral_on_empty(self):
        score, avg = _score_roce([], de_ratio=None)
        assert score == 8
        assert avg is None

    # ── _score_management ────────────────────────────────────────────────────

    def test_management_disqualified_when_pledging_above_30(self):
        snap = {"ocf_margin": 10.0, "dividend_payout_history": [20, 22, 24, 26, 28]}
        score, quality = _score_management([70, 71, 72, 73, 74], 35.0, snap)
        assert score == 0
        assert quality == "DISQUALIFIED"

    def test_management_excellent_low_pledging_stable_holding(self):
        snap = {"ocf_margin": 12.0, "dividend_payout_history": [20, 22, 23, 25, 28]}
        history = [70, 71, 71, 72, 72, 73, 73, 74, 74, 74]
        score, quality = _score_management(history, 1.0, snap)
        assert quality == "EXCELLENT"
        assert score > 0

    # ── _score_earnings ───────────────────────────────────────────────────────

    def test_earnings_score_20_for_8plus_growth_years(self):
        # 10 values — 9 YoY growth years
        pat = [100, 120, 130, 145, 160, 180, 200, 220, 245, 270]
        eps = [10, 12, 13, 14.5, 16, 18, 20, 22, 24.5, 27]
        score, cagr = _score_earnings(pat, eps)
        assert score == 20
        assert cagr is not None and cagr > 0

    def test_earnings_penalty_for_recent_pat_decline(self):
        # Last 2 years PAT declined > 20%
        pat = [100, 120, 140, 160, 180, 200, 220, 240, 180, 130]
        eps = [10, 12, 14, 16, 18, 20, 22, 24, 18, 13]
        score_no_decline, _ = _score_earnings(
            [100, 120, 140, 160, 180, 200, 220, 240, 270, 300],
            eps,
        )
        score_with_decline, _ = _score_earnings(pat, eps)
        assert score_with_decline < score_no_decline

    # ── _calculate_owner_earnings ─────────────────────────────────────────────

    def test_owner_earnings_basic_calculation(self):
        result = _calculate_owner_earnings(pat=200.0, depr=30.0, capex=60.0)
        # 200 + 30 - 0.6 * 60 = 200 + 30 - 36 = 194
        assert result == pytest.approx(194.0, abs=0.01)

    def test_owner_earnings_returns_none_when_pat_none(self):
        assert _calculate_owner_earnings(None, 30.0, 60.0) is None

    def test_owner_earnings_handles_missing_depr_and_capex(self):
        result = _calculate_owner_earnings(pat=200.0, depr=None, capex=None)
        assert result == pytest.approx(200.0, abs=0.01)

    # ── _dcf_valuation ────────────────────────────────────────────────────────

    def test_dcf_returns_neutral_for_zero_owner_earnings(self):
        score, intrinsic, mos = _dcf_valuation(
            owner_earnings=0.0,
            growth_rate=0.15,
            shares_cr=10.0,
            current_price=500.0,
            conglomerate_discount=False,
        )
        assert score == 10
        assert intrinsic is None
        assert mos is None

    def test_dcf_returns_neutral_for_none_shares(self):
        score, intrinsic, mos = _dcf_valuation(
            owner_earnings=200.0,
            growth_rate=0.15,
            shares_cr=None,
            current_price=500.0,
            conglomerate_discount=False,
        )
        assert score == 10
        assert intrinsic is None

    def test_dcf_conglomerate_discount_reduces_intrinsic(self):
        kwargs = dict(
            owner_earnings=300.0,
            growth_rate=0.15,
            shares_cr=10.0,
            current_price=1000.0,
        )
        _, intrinsic_no_discount, _ = _dcf_valuation(**kwargs, conglomerate_discount=False)
        _, intrinsic_discounted,   _ = _dcf_valuation(**kwargs, conglomerate_discount=True)
        assert intrinsic_discounted is not None
        assert intrinsic_no_discount is not None
        assert intrinsic_discounted < intrinsic_no_discount

    def test_dcf_high_mos_scores_20(self):
        # Very cheap: owner_earnings large relative to price
        score, intrinsic, mos = _dcf_valuation(
            owner_earnings=500.0,   # very high earnings
            growth_rate=0.20,
            shares_cr=5.0,          # few shares → high per-share value
            current_price=100.0,    # extremely cheap price
            conglomerate_discount=False,
        )
        assert score == 20, f"Expected score=20 for high MoS, got {score} (MoS={mos}%)"
        assert mos is not None and mos >= 40

    # ── _jhunjhunwala_bonus ───────────────────────────────────────────────────

    def test_jhunjhunwala_india_consumption_bonus(self):
        bonus, consumption, early, cyclical = _jhunjhunwala_bonus("Consumer Goods", 25.0, 4.0)
        assert consumption is True
        assert bonus >= 4

    def test_jhunjhunwala_cyclical_flag_at_trough_pe(self):
        bonus, _, _, cyclical = _jhunjhunwala_bonus("Metals", 9.0, 1.2)
        assert cyclical is True
        assert bonus >= 4

    def test_jhunjhunwala_no_bonus_for_unknown_sector(self):
        bonus, consumption, early, cyclical = _jhunjhunwala_bonus("Unknown", 30.0, 5.0)
        assert bonus == 0
        assert consumption is False
        assert early is False
        assert cyclical is False

    # ── _check_disqualifiers ──────────────────────────────────────────────────

    def test_disqualifier_insufficient_history(self):
        hist = {"years_available": 3, "pat_history": [10, 20, 30]}
        dq = _check_disqualifiers(hist, {}, market_cap_cr=5000.0, pledging=2.0)
        assert any("INSUFFICIENT_HISTORY" in d for d in dq)

    def test_disqualifier_below_min_market_cap(self):
        hist = {"years_available": 10, "pat_history": [100] * 10}
        dq = _check_disqualifiers(hist, {}, market_cap_cr=50.0, pledging=2.0)
        assert any("BELOW_MIN_MARKET_CAP" in d for d in dq)

    def test_disqualifier_critical_pledging(self):
        hist = {"years_available": 10, "pat_history": [100] * 10}
        dq = _check_disqualifiers(hist, {}, market_cap_cr=5000.0, pledging=55.0)
        assert any("CRITICAL_PLEDGING" in d for d in dq)

    def test_disqualifier_loss_making_3_of_5_years(self):
        # Last 5 values: [-50, -30, 20, -40, -10] — 4 negatives in the last 5
        hist = {
            "years_available": 10,
            "pat_history": [100, 120, 130, 140, 150, -50, -30, 20, -40, -10],
        }
        dq = _check_disqualifiers(hist, {}, market_cap_cr=5000.0, pledging=2.0)
        assert any("LOSS_MAKING" in d for d in dq)

    def test_no_disqualifiers_for_clean_company(self):
        hist = {
            "years_available": 10,
            "pat_history": [100, 120, 140, 160, 180, 200, 220, 240, 260, 280],
        }
        dq = _check_disqualifiers(hist, {}, market_cap_cr=5000.0, pledging=2.0)
        assert dq == [], f"Expected no disqualifiers, got {dq}"
