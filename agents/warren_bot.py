"""
agents/warren_bot.py — Business Quality Assessment Agent
=========================================================
Models Buffett + Munger + Jhunjhunwala philosophy.
Produces Business Quality Score (0-100) and conviction rating.

Entry point: analyse(symbol: str) -> dict
"""

from __future__ import annotations

import logging
import math
import os
import sys
from datetime import date
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from data.fetchers import get_ohlcv, get_screener_data, get_screener_history
import yfinance as yf

from agents.base import DataCompletenessValidator, insufficient_data_result

_dcv = DataCompletenessValidator()

log = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

AGENT_NAME = "warren_bot"
DISCOUNT_RATE = 0.12
TERMINAL_GROWTH = 0.07
MAX_STAGE1_GROWTH = 0.25
STAGE2_FLOOR_GROWTH = 0.12
CONGLOMERATE_DISCOUNT = 0.20
MIN_MARKET_CAP_CR = 200.0


# ─── Small utilities ──────────────────────────────────────────────────────────

def _safe_list(lst, default=None):
    """Return lst if it is a non-empty list, otherwise return default."""
    if isinstance(lst, list) and len(lst) > 0:
        return lst
    return default


def _valid_floats(lst) -> list[float]:
    """Return a list containing only the non-None float values from lst."""
    if not isinstance(lst, list):
        return []
    return [float(v) for v in lst if v is not None]


# ─── Moat inference ──────────────────────────────────────────────────────────

def _infer_moat_type(sector: str, ebitda_margin: float) -> str:
    """
    Infer the type of economic moat from sector and margin characteristics.

    Returns one of: "BRAND", "SWITCHING_COSTS", "NETWORK_EFFECT",
    "REGULATORY_LICENCE", "COST_ADVANTAGE", "NONE".
    """
    s = sector.lower() if sector else ""
    consumer_keywords = ("consumer", "fmcg", "retail", "branded", "apparel")
    software_keywords = ("software", "it", "saas", "accounting", "technology")
    network_keywords  = ("exchange", "platform", "marketplace", "network")
    regulatory_keywords = ("banking", "insurance", "regulatory", "licence")
    cost_keywords = ("pharma", "chemical", "speciality", "cost")

    if any(k in s for k in consumer_keywords) and ebitda_margin > 15:
        return "BRAND"
    if any(k in s for k in software_keywords) and ebitda_margin > 20:
        return "SWITCHING_COSTS"
    if any(k in s for k in network_keywords):
        return "NETWORK_EFFECT"
    if any(k in s for k in regulatory_keywords):
        return "REGULATORY_LICENCE"
    if any(k in s for k in cost_keywords):
        return "COST_ADVANTAGE"
    return "NONE"


# ─── Dimension 1: Moat Strength (0–20) ───────────────────────────────────────

def _score_moat(ebitda_margins: list, sector: str) -> tuple[int, str]:
    """
    Score the width and durability of the company's economic moat (0–20).

    Logic:
      - Declining 3+ consecutive years → 0, "NONE"
      - >= 7 years above 20%           → 20, inferred moat type
      - All valid >= 15% consistently  → 14, moat type if max > 15 else "NONE"
      - Volatile / below 15%           → proportional 0–10, "NONE"
      - No data                        → 8, "NONE" (partial neutral)

    Returns:
        (score, moat_type)
    """
    valid = _valid_floats(ebitda_margins)
    if not valid:
        return 8, "NONE"

    avg_margin = sum(valid) / len(valid)
    max_margin = max(valid)

    # Check for 3+ consecutive declining years anywhere in the series.
    # "Declining" means each year's margin is strictly lower than the prior year.
    # We scan for ANY run of 3+ consecutive declines in the full series.
    if len(valid) >= 3:
        consecutive_decline = 0
        triggered = False
        for i in range(1, len(valid)):
            if valid[i] < valid[i - 1]:
                consecutive_decline += 1
                if consecutive_decline >= 3:
                    triggered = True
                    break
            else:
                consecutive_decline = 0
        if triggered:
            return 0, "NONE"

    # Count years above 20%
    above_20_count = sum(1 for m in valid if m >= 20)
    if above_20_count >= 7:
        moat_type = _infer_moat_type(sector, avg_margin)
        return 20, moat_type

    # All valid >= 15% consistently
    if valid and all(m >= 15 for m in valid):
        moat_type = _infer_moat_type(sector, avg_margin) if max_margin > 15 else "NONE"
        return 14, moat_type

    # Volatile / below 15%
    score = max(0, int(avg_margin / 15 * 10))
    return score, "NONE"


# ─── Dimension 2: ROCE Quality (0–20) ────────────────────────────────────────

def _score_roce(roce_history: list, de_ratio: Optional[float]) -> tuple[int, Optional[float]]:
    """
    Score the quality and consistency of Return on Capital Employed (0–20).

    Logic:
      - >= 7 years above 20%         → 20
      - All valid >= 15%             → 14
      - Average >= 10%               → proportional 0–13
      - Below 10%                    → 0
      - D/E > 1.5 penalty: -5 (floor 0)

    Returns:
        (score, avg_roce)
    """
    valid = _valid_floats(roce_history)
    if not valid:
        avg_roce = None
        score = 8  # neutral when no data
    else:
        avg_roce = round(sum(valid) / len(valid), 2)
        above_20_count = sum(1 for r in valid if r >= 20)

        if above_20_count >= 7:
            score = 20
        elif all(r >= 15 for r in valid):
            score = 14
        elif avg_roce >= 10:
            # Proportional 0–13
            score = min(13, int((avg_roce - 10) / 10 * 13))
        else:
            score = 0

    # D/E > 1.5 penalty
    if de_ratio is not None and de_ratio > 1.5:
        score = max(0, score - 5)

    return score, avg_roce


# ─── Dimension 3: Management Quality (0–20) ──────────────────────────────────

def _score_management(
    promoter_history: list,
    pledging: Optional[float],
    snap: dict,
) -> tuple[int, str]:
    """
    Score management quality based on promoter commitment and capital returns (0–20).

    Logic:
      - Pledging > 30%               → 0, "DISQUALIFIED"
      - pledging < 5 AND stable/inc  → 20 base, "EXCELLENT"
      - pledging 5–15 AND stable     → 13 base, "GOOD"
      - pledging 15–30 OR declining  → 7 base, "CONCERN"
      - Bonus: dividend payout increasing over 5 years → +2
      - Bonus: ocf_margin > 0 (shareholder returns proxy) → +1
      - Cap at 20

    Returns:
        (score, quality_str)
    """
    pledging_val = pledging if pledging is not None else 0.0

    # Hard disqualifier
    if pledging_val > 30:
        return 0, "DISQUALIFIED"

    # Determine promoter holding trend
    valid_hist = _valid_floats(promoter_history)
    stable_or_increasing = True
    if len(valid_hist) >= 5:
        # Compare latest to 5 periods ago
        latest = valid_hist[-1]
        five_ago = valid_hist[-5]
        stable_or_increasing = latest >= five_ago - 1.0  # allow 1% tolerance
    elif len(valid_hist) >= 2:
        stable_or_increasing = valid_hist[-1] >= valid_hist[0] - 1.0

    # Base score
    if pledging_val < 5 and stable_or_increasing:
        base = 20
        quality = "EXCELLENT"
    elif pledging_val < 15 and stable_or_increasing:
        base = 13
        quality = "GOOD"
    else:
        base = 7
        quality = "CONCERN"

    score = base
    dividend_payout_history = snap.get("dividend_payout_history", [])
    if isinstance(dividend_payout_history, list):
        div_valid = _valid_floats(dividend_payout_history)
        if len(div_valid) >= 5:
            # Check if dividend payout is increasing over last 5 years
            recent = div_valid[-5:]
            is_increasing = all(recent[i] >= recent[i - 1] for i in range(1, len(recent)))
            if is_increasing:
                score += 2

    ocf_margin = snap.get("ocf_margin") if isinstance(snap, dict) else None
    if ocf_margin is not None and ocf_margin > 0:
        score += 1

    return min(score, 20), quality


# ─── Dimension 4: Earnings Quality (0–20) ────────────────────────────────────

def _score_earnings(
    pat_history: list,
    eps_history: list,
) -> tuple[int, Optional[float]]:
    """
    Score the consistency and trend of earnings growth (0–20).

    Logic:
      - Count YoY PAT growth years
      - >= 8 years of PAT growth     → 20
      - 6–7 years                    → 13
      - < 6 years                    → proportional 0–8
      - Penalty: last 2 years PAT declined > 20% → -5
      - Compute 10-year EPS CAGR

    Returns:
        (score, eps_cagr_pct)
    """
    pat_valid = _valid_floats(pat_history)
    eps_valid = _valid_floats(eps_history)

    # Count YoY PAT growth years
    growth_years = 0
    if len(pat_valid) >= 2:
        for i in range(1, len(pat_valid)):
            if pat_valid[i] > pat_valid[i - 1]:
                growth_years += 1

    if growth_years >= 8:
        score = 20
    elif growth_years >= 6:
        score = 13
    else:
        score = min(8, max(0, int(growth_years / 6 * 8)))

    # Penalty: PAT declined > 20% in last 2 years
    if len(pat_valid) >= 3:
        # Check last 2 YoY comparisons
        recent_declines = 0
        for i in range(len(pat_valid) - 2, len(pat_valid)):
            if pat_valid[i - 1] > 0 and (pat_valid[i] - pat_valid[i - 1]) / pat_valid[i - 1] < -0.20:
                recent_declines += 1
        if recent_declines >= 1:
            score = max(0, score - 5)

    # Compute 10-year EPS CAGR
    eps_cagr_pct: Optional[float] = None
    if len(eps_valid) >= 2:
        earliest = eps_valid[0]
        latest = eps_valid[-1]
        n_years = len(eps_valid) - 1
        if earliest > 0 and latest > 0 and n_years > 0:
            try:
                eps_cagr_pct = round(((latest / earliest) ** (1.0 / n_years) - 1) * 100, 2)
            except (ValueError, ZeroDivisionError):
                eps_cagr_pct = None

    return score, eps_cagr_pct


# ─── Owner Earnings helper ────────────────────────────────────────────────────

def _calculate_owner_earnings(
    pat: Optional[float],
    depr: Optional[float],
    capex: Optional[float],
) -> Optional[float]:
    """
    Compute Buffett's Owner Earnings: PAT + Depreciation - 0.6 * Capex.

    Maintenance capex is approximated as 60% of total investing outflow.
    Returns None if PAT is None (cannot compute without earnings base).
    """
    if pat is None:
        return None
    depr_val = depr if depr is not None else 0.0
    capex_val = capex if capex is not None else 0.0
    return pat + depr_val - 0.6 * capex_val


# ─── Dimension 5: DCF Valuation (0–20) ───────────────────────────────────────

def _dcf_valuation(
    owner_earnings: float,
    growth_rate: float,
    shares_cr: Optional[float],
    current_price: float,
    conglomerate_discount: bool,
) -> tuple[int, Optional[float], Optional[float]]:
    """
    3-Stage DCF model to compute intrinsic value and margin of safety (0–20).

    Stages:
      Stage 1 (yr 1–5):  grow at min(growth_rate, MAX_STAGE1_GROWTH), discount at 12%
      Stage 2 (yr 6–10): fade linearly from stage1_growth to STAGE2_FLOOR_GROWTH
      Terminal:          CF_10 * (1 + TERMINAL_GROWTH) / (DISCOUNT_RATE - TERMINAL_GROWTH)
                         discounted back at year 10

    Args:
        owner_earnings:      latest annual owner earnings in Crores
        growth_rate:         expected near-term growth rate as decimal (e.g. 0.18)
        shares_cr:           shares outstanding in Crores
        current_price:       current market price per share in INR
        conglomerate_discount: apply 20% conglomerate discount to total DCF

    Returns:
        (score, intrinsic_per_share, margin_of_safety_pct)
        Returns (10, None, None) if owner_earnings <= 0 or shares_cr is None.
    """
    if owner_earnings <= 0 or shares_cr is None or shares_cr <= 0:
        return 10, None, None

    stage1_growth = min(growth_rate, MAX_STAGE1_GROWTH)

    # Stage 1: years 1–5
    total_dcf = 0.0
    cf = owner_earnings
    for yr in range(1, 6):
        cf = cf * (1 + stage1_growth)
        pv = cf / ((1 + DISCOUNT_RATE) ** yr)
        total_dcf += pv

    # Stage 2: years 6–10, growth fades linearly from stage1_growth to STAGE2_FLOOR_GROWTH
    stage2_growths = []
    for step in range(5):  # steps 0..4 correspond to years 6..10
        fade_fraction = step / 4.0  # 0.0 at yr6, 1.0 at yr10
        g = stage1_growth + fade_fraction * (STAGE2_FLOOR_GROWTH - stage1_growth)
        stage2_growths.append(g)

    cf_10 = cf  # cf at end of stage 1 (year 5)
    for yr_idx, g in enumerate(stage2_growths):
        yr = 6 + yr_idx
        cf_10 = cf_10 * (1 + g)
        pv = cf_10 / ((1 + DISCOUNT_RATE) ** yr)
        total_dcf += pv

    # Terminal value at year 10
    terminal_cf = cf_10 * (1 + TERMINAL_GROWTH)
    terminal_value = terminal_cf / (DISCOUNT_RATE - TERMINAL_GROWTH)
    terminal_pv = terminal_value / ((1 + DISCOUNT_RATE) ** 10)
    total_dcf += terminal_pv

    # Conglomerate discount
    if conglomerate_discount:
        total_dcf = total_dcf * (1 - CONGLOMERATE_DISCOUNT)

    # Intrinsic value per share (both total_dcf and shares_cr in Crores)
    intrinsic_per_share = total_dcf / shares_cr

    # Margin of safety
    mos_pct = (intrinsic_per_share - current_price) / intrinsic_per_share * 100

    # Score
    if mos_pct >= 40:
        score = 20
    elif mos_pct >= 20:
        score = 14
    elif mos_pct >= -20:
        score = 8
    else:
        score = max(0, 5 + int(mos_pct / 10))

    return score, round(intrinsic_per_share, 2), round(mos_pct, 2)


# ─── Jhunjhunwala Bonus (0–11) ────────────────────────────────────────────────

def _jhunjhunwala_bonus(
    sector: str,
    pe: Optional[float],
    pb_ratio: Optional[float],
) -> tuple[int, bool, bool, bool]:
    """
    Apply the Jhunjhunwala lens: India consumption, early penetration, and
    cyclical-at-trough bonuses (total up to 11 points).

    Returns:
        (bonus, india_consumption, early_penetration, cyclical_flag)
    """
    s = sector.lower() if sector else ""

    india_consumption_keywords = (
        "consumer", "fmcg", "retail", "financial services", "banking",
        "insurance", "healthcare", "pharma", "auto", "automobile",
        "two-wheeler", "food", "beverage",
    )
    early_penetration_keywords = (
        "jewellery", "mutual fund", "insurance", "aviation", "diagnostic",
        "hospital", "asset management",
    )
    cyclical_keywords = (
        "metals", "chemicals", "capital goods", "shipping", "materials",
        "basic materials", "energy",
    )

    india_consumption = any(k in s for k in india_consumption_keywords)
    early_pen = any(k in s for k in early_penetration_keywords)
    is_cyclical = any(k in s for k in cyclical_keywords)

    # Cyclical flag: sector is cyclical AND PE < 12 (cheap on cycle) or
    # P/B < 1.5 (trading near book — classic cyclical trough signal)
    cyclical_flag = False
    if is_cyclical:
        if pe is not None and pe > 0 and pe < 12:
            cyclical_flag = True
        elif pb_ratio is not None and pb_ratio > 0 and pb_ratio < 1.5:
            cyclical_flag = True

    bonus = (4 if india_consumption else 0) + (3 if early_pen else 0) + (4 if cyclical_flag else 0)
    return bonus, india_consumption, early_pen, cyclical_flag


# ─── Hard Disqualifiers ───────────────────────────────────────────────────────

def _check_disqualifiers(
    hist: Optional[dict],
    snap: Optional[dict],
    market_cap_cr: Optional[float],
    pledging: Optional[float],
) -> list[str]:
    """
    Check four hard disqualifiers. Returns list of triggered disqualifier strings.

    Disqualifiers:
      1. Operating history < 5 years (years_available < 5)
      2. Market cap < 200 Cr
      3. Promoter pledging > 50%
      4. PAT negative in 3+ of last 5 years
    """
    triggered: list[str] = []

    # 1. Operating history
    years_available = hist.get("years_available", 0) if hist else 0
    if years_available < 5:
        triggered.append(f"INSUFFICIENT_HISTORY: only {years_available} years of data (min 5)")

    # 2. Market cap
    if market_cap_cr is not None and market_cap_cr < MIN_MARKET_CAP_CR:
        triggered.append(
            f"BELOW_MIN_MARKET_CAP: {market_cap_cr:.0f} Cr < {MIN_MARKET_CAP_CR:.0f} Cr minimum"
        )

    # 3. Pledging
    if pledging is not None and pledging > 50:
        triggered.append(f"CRITICAL_PLEDGING: {pledging:.1f}% > 50% threshold")

    # 4. PAT negative in 3+ of last 5 years
    if hist is not None:
        pat_hist = hist.get("pat_history", [])
        pat_valid_recent = [v for v in (pat_hist[-5:] if len(pat_hist) >= 5 else pat_hist) if v is not None]
        if pat_valid_recent:
            negative_years = sum(1 for v in pat_valid_recent if v < 0)
            if negative_years >= 3:
                triggered.append(
                    f"LOSS_MAKING: PAT negative in {negative_years} of last {len(pat_valid_recent)} years"
                )

    return triggered


# ─── AI Commentary ────────────────────────────────────────────────────────────

def _generate_commentary(
    symbol: str,
    moat_type: str,
    roce_avg: Optional[float],
    eps_cagr: Optional[float],
    mos_pct: Optional[float],
    score: int,
) -> tuple[str, str]:
    """
    Generate 2-sentence Buffett/Jhunjhunwala-style commentary using Claude Haiku.

    Uses ANTHROPIC_API_KEY environment variable. Returns fallback strings if
    key is missing or API call fails.

    Returns:
        (why_like, why_pass) — both plain English strings
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        why_like = (
            f"{symbol} demonstrates {moat_type.lower().replace('_', ' ')} characteristics "
            f"with strong capital returns that compound shareholder wealth over time."
        )
        why_pass = (
            f"Without a deeper margin of safety and clearer moat evidence, "
            f"the risk/reward on {symbol} does not meet our long-term hurdle rate."
        )
        return why_like, why_pass

    roce_str = f"{roce_avg:.1f}" if roce_avg is not None else "N/A"
    cagr_str = f"{eps_cagr:.1f}" if eps_cagr is not None else "N/A"
    mos_str  = f"{mos_pct:.1f}"  if mos_pct  is not None else "N/A"

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        like_prompt = (
            f"In 2 sentences as Warren Buffett and Rakesh Jhunjhunwala would speak, "
            f"explain why you would like investing in {symbol} given: "
            f"moat_type={moat_type}, roce_avg={roce_str}%, "
            f"eps_cagr={cagr_str}%, margin_of_safety={mos_str}%. "
            f"Be specific and direct."
        )
        pass_prompt = (
            f"In 2 sentences as Warren Buffett and Rakesh Jhunjhunwala would speak, "
            f"explain what concerns would make you pass on {symbol} given: "
            f"moat_type={moat_type}, roce_avg={roce_str}%, "
            f"eps_cagr={cagr_str}%, margin_of_safety={mos_str}%. "
            f"Be honest and specific."
        )

        why_like_msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{"role": "user", "content": like_prompt}],
        )
        why_like = why_like_msg.content[0].text.strip()

        why_pass_msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{"role": "user", "content": pass_prompt}],
        )
        why_pass = why_pass_msg.content[0].text.strip()

        return why_like, why_pass

    except Exception as exc:
        log.warning("_generate_commentary: API call failed for %s: %s", symbol, exc)
        why_like = (
            f"{symbol} shows {moat_type.lower().replace('_', ' ')} moat with "
            f"ROCE of {roce_str}% — the kind of compounding machine we seek."
        )
        why_pass = (
            f"At current valuations (MoS={mos_str}%), the price does not offer "
            f"the margin of safety a Buffett-style investor demands."
        )
        return why_like, why_pass


# ─── Supabase Audit Logger ────────────────────────────────────────────────────

def _log_to_supabase(
    symbol: str,
    data_points_fetched: int,
    total_points: int,
) -> None:
    """
    Write a warren_bot analysis audit record to the agent_performance table.

    Uses SUPABASE_URL and SUPABASE_SERVICE_KEY environment variables.
    Silently fails with a warning log if Supabase is not configured or
    the write fails for any reason.
    """
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        log.debug("_log_to_supabase: Supabase not configured — skipping write")
        return
    try:
        from supabase import create_client
        client = create_client(url, key)
        client.table("agent_performance").insert({
            "agent_name": "warren_bot",
            "audit_date": str(date.today()),
        }).execute()
    except Exception as exc:
        log.warning("_log_to_supabase: write failed for %s: %s", symbol, exc)


# ─── Main Entry Point ─────────────────────────────────────────────────────────

def analyse(symbol: str) -> dict:
    """
    Business quality assessment. Returns orchestrator-compatible dict.

    Scores five dimensions (moat, ROCE, management, earnings, valuation),
    applies a Jhunjhunwala thematic bonus, checks hard disqualifiers, and
    calls Claude Haiku for plain-English commentary.

    Args:
        symbol: NSE ticker, e.g. "RELIANCE.NS" or "TCS"

    Returns:
        Dict with agent_name, signal, score, conviction_rating, moat details,
        intrinsic value, margins of safety, commentary, key_risks, and
        data_gaps. Never raises — returns a partial result with error in
        data_gaps on unrecoverable failure.
    """
    # ── Safe fallback for total failure ──────────────────────────────────────
    def _safe_fallback(err: str) -> dict:
        return {
            "agent_name": AGENT_NAME,
            "symbol": symbol,
            "signal": "AVOID",
            "score": 0,
            "business_quality_score": 0,
            "conviction_rating": "DOES NOT QUALIFY — momentum trade only",
            "moat_type": "NONE",
            "moat_strength_score": 0,
            "roce_score": 0,
            "management_score": 0,
            "earnings_score": 0,
            "valuation_score": 0,
            "intrinsic_value_per_share": None,
            "current_price": None,
            "margin_of_safety_pct": None,
            "ten_year_eps_cagr": None,
            "roce_avg_10yr": None,
            "promoter_quality": "UNKNOWN",
            "india_consumption_play": False,
            "early_penetration_play": False,
            "jhunjhunwala_cyclical_flag": False,
            "why_buffett_would_like": "",
            "why_buffett_would_pass": "",
            "key_risks": [err],
            "detail": f"Analysis failed: {err}",
            "confidence": 0,
            "data_sources": [],
            "data_gaps": [err],
        }

    try:
        # ── Step 1: Clean symbol ─────────────────────────────────────────────
        screener_symbol = symbol.replace(".NS", "").replace(".BO", "").upper()
        yf_symbol = symbol if symbol.endswith(".NS") or symbol.endswith(".BO") else f"{symbol}.NS"

        data_gaps: list[str] = []
        confidence = 100
        data_sources = ["screener.in", "yfinance"]

        # ── Step 2: Fetch screener snapshot ──────────────────────────────────
        snap = get_screener_data(screener_symbol)
        if snap is None:
            data_gaps.append("screener_snapshot_unavailable")
            confidence -= 8
            snap = {}

        # ── Step 3: Fetch historical data ─────────────────────────────────────
        hist = get_screener_history(screener_symbol)
        if hist is None:
            data_gaps.append("screener_history_unavailable")
            confidence -= 8
            hist = {
                "years": [],
                "revenue_history": [],
                "ebitda_margins": [],
                "pat_history": [],
                "eps_history": [],
                "depreciation_history": [],
                "capex_history": [],
                "roce_history": [],
                "roe_history": [],
                "dividend_payout_history": [],
                "promoter_holding_history": [],
                "promoter_holding_quarters": [],
                "years_available": 0,
            }

        # ── Step 4: Fetch OHLCV for current price ─────────────────────────────
        ohlcv = get_ohlcv(yf_symbol)
        current_price: Optional[float] = None
        if ohlcv is not None and not ohlcv.empty:
            try:
                current_price = float(ohlcv["Close"].dropna().iloc[-1])
            except (IndexError, KeyError, ValueError):
                current_price = None
        if current_price is None:
            data_gaps.append("current_price_unavailable")
            confidence -= 8

        # ── Step 5: Fetch yfinance info ───────────────────────────────────────
        market_cap_cr: Optional[float] = None
        shares_cr: Optional[float] = None
        sector = ""
        pb_ratio: Optional[float] = None
        pe_yf: Optional[float] = None

        try:
            ticker = yf.Ticker(yf_symbol)
            info = ticker.info

            market_cap_raw = info.get("marketCap")
            if market_cap_raw is not None:
                try:
                    market_cap_cr = float(market_cap_raw) / 1e7
                except (TypeError, ValueError):
                    market_cap_cr = None

            sector = (info.get("sector") or "").strip()

            pb_raw = info.get("priceToBook")
            if pb_raw is not None:
                try:
                    pb_ratio = float(pb_raw)
                except (TypeError, ValueError):
                    pb_ratio = None

            # Current price fallback from yfinance info
            if current_price is None:
                cp_raw = info.get("currentPrice") or info.get("regularMarketPrice")
                if cp_raw is not None:
                    try:
                        current_price = float(cp_raw)
                        data_gaps = [g for g in data_gaps if g != "current_price_unavailable"]
                    except (TypeError, ValueError):
                        pass

            # PE from yfinance as fallback
            pe_raw = info.get("trailingPE") or info.get("forwardPE")
            if pe_raw is not None:
                try:
                    pe_yf = float(pe_raw)
                except (TypeError, ValueError):
                    pe_yf = None

            # Shares outstanding
            shares_raw = info.get("sharesOutstanding")
            if shares_raw is not None:
                try:
                    shares_cr = float(shares_raw) / 1e7
                except (TypeError, ValueError):
                    shares_cr = None

        except Exception as yf_exc:
            log.warning("warren_bot: yfinance fetch failed for %s: %s", yf_symbol, yf_exc)
            data_gaps.append("yfinance_info_unavailable")
            confidence -= 8

        # ── Step 5b: Data completeness check ─────────────────────────────────
        _pe_val = snap.get("pe") or pe_yf
        _years  = hist.get("years_available", 0) or 0
        _snapshot = {
            "pe":              _pe_val,
            "years_available": _years,
            "revenue_history": len(_valid_floats(hist.get("revenue_history", []))),
            "roce_history":    len(_valid_floats(hist.get("roce_history", []))),
            "current_price":   current_price,
            "market_cap":      market_cap_cr,
        }
        _chk = _dcv.validate(_snapshot, "warren_bot")
        if not _chk.is_sufficient:
            return insufficient_data_result("warren_bot", _chk,
                                            data_sources=data_sources,
                                            symbol=symbol,
                                            data_gaps=data_gaps + _chk.all_missing)

        # ── Step 6: Extract key values ────────────────────────────────────────
        pe = snap.get("pe") or pe_yf
        ebitda_margin_snap = snap.get("ebitda_margin")
        de_ratio = snap.get("debt_equity")
        pledging = snap.get("promoter_pledging")
        ocf_margin = snap.get("ocf_margin")

        ebitda_margins_hist = hist.get("ebitda_margins", [])
        pat_history = hist.get("pat_history", [])
        eps_history = hist.get("eps_history", [])
        roce_history = hist.get("roce_history", [])
        depreciation_history = hist.get("depreciation_history", [])
        capex_history = hist.get("capex_history", [])
        promoter_holding_history = hist.get("promoter_holding_history", [])
        dividend_payout_history = hist.get("dividend_payout_history", [])

        # Fall back to snapshot ebitda_margin for moat scoring if history empty
        if not _valid_floats(ebitda_margins_hist) and ebitda_margin_snap is not None:
            ebitda_margins_hist = [ebitda_margin_snap]
            data_gaps.append("ebitda_margin_history_limited")
            confidence -= 8

        # Compute shares_cr from PAT and EPS as a better estimate
        pat_valid = _valid_floats(pat_history)
        eps_valid = _valid_floats(eps_history)
        if pat_valid and eps_valid:
            pat_latest = pat_valid[-1]
            eps_latest = eps_valid[-1]
            if eps_latest and abs(eps_latest) > 0.001:
                try:
                    shares_from_financials = pat_latest / eps_latest  # Crores of shares
                    # Sanity check: shares_from_financials should be positive
                    if shares_from_financials > 0:
                        shares_cr = shares_from_financials
                except ZeroDivisionError:
                    pass

        if market_cap_cr is None:
            data_gaps.append("market_cap_unavailable")
            confidence -= 8

        # ── Step 7: Assess data completeness ─────────────────────────────────
        if not pat_valid:
            data_gaps.append("pat_history_unavailable")
            confidence -= 8
        if not eps_valid:
            data_gaps.append("eps_history_unavailable")
            confidence -= 8
        if not _valid_floats(roce_history):
            data_gaps.append("roce_history_unavailable")
            confidence -= 8

        confidence = max(0, min(100, confidence))

        # ── Step 8: Score 5 dimensions ────────────────────────────────────────

        # Dimension 1: Moat
        moat_score, moat_type = _score_moat(ebitda_margins_hist, sector)

        # Dimension 2: ROCE
        roce_score, roce_avg = _score_roce(roce_history, de_ratio)

        # Dimension 3: Management
        snap_with_hist = dict(snap)
        snap_with_hist["dividend_payout_history"] = dividend_payout_history
        snap_with_hist["ocf_margin"] = ocf_margin
        mgmt_score, promoter_quality = _score_management(
            promoter_holding_history, pledging, snap_with_hist
        )

        # Dimension 4: Earnings
        earn_score, eps_cagr = _score_earnings(pat_history, eps_history)

        # Dimension 5: DCF Valuation
        # Determine growth rate for DCF: use EPS CAGR or revenue CAGR from snap
        growth_inputs = [
            snap.get("eps_cagr_5y"),
            snap.get("revenue_cagr_5y"),
            snap.get("eps_cagr_3y"),
            snap.get("revenue_cagr_3y"),
        ]
        growth_inputs_pct = [v for v in growth_inputs if v is not None and v > 0]
        raw_growth_rate = (sum(growth_inputs_pct) / len(growth_inputs_pct) / 100) if growth_inputs_pct else 0.10
        growth_rate = max(0.03, min(raw_growth_rate, MAX_STAGE1_GROWTH))

        # Owner earnings from latest available
        pat_latest = pat_valid[-1] if pat_valid else None
        depr_latest_list = _valid_floats(depreciation_history)
        depr_latest = depr_latest_list[-1] if depr_latest_list else None
        capex_latest_list = _valid_floats(capex_history)
        capex_latest = capex_latest_list[-1] if capex_latest_list else None

        owner_earnings = _calculate_owner_earnings(pat_latest, depr_latest, capex_latest)

        # Is this a conglomerate?
        conglomerate_keywords = ("diversified", "conglomerate", "holding")
        is_conglomerate = any(k in sector.lower() for k in conglomerate_keywords)

        val_score: int
        intrinsic: Optional[float]
        mos_pct: Optional[float]

        if owner_earnings is not None and current_price is not None and current_price > 0:
            val_score, intrinsic, mos_pct = _dcf_valuation(
                owner_earnings, growth_rate, shares_cr, current_price, is_conglomerate
            )
        else:
            val_score, intrinsic, mos_pct = 10, None, None
            data_gaps.append("dcf_valuation_incomplete")

        # ── Step 9: Jhunjhunwala bonus ────────────────────────────────────────
        jj_bonus, india_consumption, early_pen, cyclical_flag = _jhunjhunwala_bonus(
            sector, pe, pb_ratio
        )

        # ── Step 10: Total score ──────────────────────────────────────────────
        raw_total = moat_score + roce_score + mgmt_score + earn_score + val_score + jj_bonus
        total_score = max(0, min(100, raw_total))

        # ── Step 11: Hard disqualifiers ───────────────────────────────────────
        disqualifiers = _check_disqualifiers(hist, snap, market_cap_cr, pledging)

        # ── Step 12: Signal ───────────────────────────────────────────────────
        if disqualifiers or total_score < 50:
            signal = "AVOID"
        elif total_score < 65:
            signal = "WATCHLIST"
        else:
            signal = "QUALITY_BUY"

        # If disqualifiers but score is otherwise >= 65, still AVOID
        if disqualifiers:
            signal = "AVOID"

        # ── Step 13: Conviction rating ────────────────────────────────────────
        if total_score >= 80:
            conviction_rating = "STRONG CONVICTION — 10-year compounding candidate"
        elif total_score >= 65:
            conviction_rating = "MODERATE CONVICTION — 5-year hold candidate"
        elif total_score >= 50:
            conviction_rating = "WATCHLIST — quality business, wait for better price"
        else:
            conviction_rating = "DOES NOT QUALIFY — momentum trade only"

        # ── Step 14: Key risks ────────────────────────────────────────────────
        # Generate risks based on weakest dimension scores
        dimension_scores = [
            (moat_score, "moat", "Economic moat not clearly established or declining margins"),
            (roce_score, "roce", "ROCE below 15% suggests capital allocation inefficiency"),
            (mgmt_score, "management", "Promoter pledging or declining holding raises governance concern"),
            (earn_score, "earnings", "Earnings trajectory inconsistent; PAT growth not sustained"),
            (val_score, "valuation", "Current price offers limited margin of safety at DCF-implied value"),
        ]
        dimension_scores.sort(key=lambda x: x[0])
        key_risks = [desc for _, _, desc in dimension_scores[:4]]

        # Add disqualifier risks
        for dq in disqualifiers:
            key_risks.insert(0, f"DISQUALIFIER: {dq}")
        key_risks = key_risks[:4]

        # ── Step 15: Commentary ───────────────────────────────────────────────
        why_like, why_pass = _generate_commentary(
            screener_symbol, moat_type, roce_avg, eps_cagr, mos_pct, total_score
        )

        # ── Step 16: Detail string ────────────────────────────────────────────
        signal_map = {
            "QUALITY_BUY": "meets Buffett-Jhunjhunwala quality criteria",
            "WATCHLIST":   "shows quality characteristics but needs better entry price",
            "AVOID":       "does not meet minimum quality thresholds for long-term holding",
        }
        moat_desc = moat_type.lower().replace("_", " ")
        roce_desc = f"ROCE avg {roce_avg:.1f}%" if roce_avg is not None else "ROCE data limited"
        detail = (
            f"{screener_symbol} {signal_map.get(signal, 'requires further analysis')} "
            f"with {moat_desc} moat and {roce_desc}. "
            f"Business quality score {total_score}/100 reflects {conviction_rating.split(' — ')[0].lower()} "
            f"based on 10-year financial history and DCF-derived margin of safety."
        )

        # ── Step 17: Data tracking ─────────────────────────────────────────────
        data_points_possible = 14  # major data series
        data_points_fetched = data_points_possible - len([g for g in data_gaps if "unavailable" in g])
        data_points_fetched = max(0, data_points_fetched)

        # ── Step 18: Log to Supabase ───────────────────────────────────────────
        _log_to_supabase(screener_symbol, data_points_fetched, data_points_possible)

        # ── Step 19: Build and return result dict ─────────────────────────────
        return {
            "agent_name":                  AGENT_NAME,
            "symbol":                      symbol,
            "signal":                      signal,
            "score":                       total_score,
            "business_quality_score":      total_score,
            "conviction_rating":           conviction_rating,
            "moat_type":                   moat_type,
            "moat_strength_score":         moat_score,
            "roce_score":                  roce_score,
            "management_score":            mgmt_score,
            "earnings_score":              earn_score,
            "valuation_score":             val_score,
            "intrinsic_value_per_share":   intrinsic,
            "current_price":               current_price,
            "margin_of_safety_pct":        mos_pct,
            "ten_year_eps_cagr":           eps_cagr,
            "roce_avg_10yr":               roce_avg,
            "promoter_quality":            promoter_quality,
            "india_consumption_play":      india_consumption,
            "early_penetration_play":      early_pen,
            "jhunjhunwala_cyclical_flag":  cyclical_flag,
            "why_buffett_would_like":      why_like,
            "why_buffett_would_pass":      why_pass,
            "key_risks":                   key_risks,
            "detail":                      detail,
            "confidence":                  confidence,
            "data_sources":               data_sources,
            "data_gaps":                   data_gaps,
        }

    except Exception as exc:
        log.error("warren_bot.analyse(%s): unhandled exception: %s", symbol, exc, exc_info=True)
        return _safe_fallback(str(exc))


# ─── Smoke test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    sym = sys.argv[1] if len(sys.argv) > 1 else "TCS"
    print(f"\nWarren Bot — Business Quality Assessment: {sym}\n")
    result = analyse(sym)
    print(json.dumps(result, indent=2, default=str))
