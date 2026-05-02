"""
agents/mgmt_quality.py — Management Quality Scoring Agent
==========================================================
Scores management quality across 5 dimensions using historical data
from screener.in and yfinance.

Dimensions (20 pts each → max 100)
------------------------------------
1. Capital Allocation Efficiency  — ROCE trend, capex discipline, FCF conversion
2. Earnings Reliability           — EPS consistency, low earnings revisions, smooth PAT growth
3. Balance Sheet Prudence         — D/E trend, interest coverage, working capital mgmt
4. Promoter Commitment            — pledging trend, insider buying/holding level
5. Shareholder Returns            — dividend consistency, buybacks, EPS CAGR alignment

Signal thresholds (same as other agents):
  ≥72 → STRONG_BUY  |  ≥55 → BUY  |  ≥40 → HOLD  |  ≥25 → AVOID  |  <25 → SELL

Usage
-----
    from agents.mgmt_quality import analyse
    result = analyse("HDFCBANK")
    # {signal, score, detail, risk_flags, agent_name}

Standalone
----------
    python -m agents.mgmt_quality HDFCBANK
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)
AGENT_NAME = "mgmt_quality"

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ──────────────────────────────────────────────────────────────────────────────
# Scoring helpers
# ──────────────────────────────────────────────────────────────────────────────

def _trend_slope(values: list) -> float:
    """Normalised linear slope of a list of floats. Positive = improving."""
    import numpy as np
    v = [x for x in values if x is not None]
    if len(v) < 2:
        return 0.0
    x = list(range(len(v)))
    if max(v) == min(v):
        return 0.0
    v_n = [(vi - min(v)) / (max(v) - min(v)) for vi in v]
    return float(np.polyfit(x, v_n, 1)[0])


def _safe(v, default=None):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


# ──────────────────────────────────────────────────────────────────────────────
# Dimension 1: Capital Allocation Efficiency (0–20)
# ──────────────────────────────────────────────────────────────────────────────

def _score_capital_allocation(raw: dict, history: dict) -> tuple[int, list[str]]:
    """
    ROCE trend, capex/sales ratio trend, FCF conversion (PAT→FCF).
    """
    notes: list[str] = []
    score = 0

    # ROCE trend (10-yr) — max 8 pts
    roce_hist = history.get("roce") or []
    if len(roce_hist) >= 4:
        slope = _trend_slope(roce_hist[-8:])
        last  = _safe(roce_hist[-1], 0)
        if slope > 0.02:
            score += 4; notes.append(f"ROCE improving trend (slope={slope:.3f})")
        if last >= 20:
            score += 4; notes.append(f"ROCE {last:.1f}% — excellent capital efficiency")
        elif last >= 12:
            score += 2; notes.append(f"ROCE {last:.1f}% — decent capital efficiency")

    # Capex / Revenue (lower = better capital discipline — unless it's a growth co.)
    rev_hist   = history.get("revenue") or []
    capex_hist = history.get("capex")   or []
    if len(rev_hist) >= 3 and len(capex_hist) >= 3:
        capex_rev_ratios = [
            abs(_safe(c, 0)) / max(_safe(r, 1), 1)
            for c, r in zip(capex_hist, rev_hist)
            if r and c is not None
        ]
        if capex_rev_ratios:
            recent_cr = sum(capex_rev_ratios[-3:]) / len(capex_rev_ratios[-3:])
            if recent_cr < 0.05:
                score += 4; notes.append(f"Low capex/revenue {recent_cr*100:.1f}% — asset-light model")
            elif recent_cr < 0.12:
                score += 2; notes.append(f"Moderate capex/revenue {recent_cr*100:.1f}%")

    # FCF proxy: PAT growth consistency (higher = more cash generation)
    pat_hist = history.get("pat") or []
    if len(pat_hist) >= 4:
        slope = _trend_slope(pat_hist[-6:])
        if slope > 0.03:
            score += 4; notes.append("PAT consistently growing — high earnings quality")
        elif slope > 0:
            score += 2; notes.append("PAT modestly growing")

    return min(score, 20), notes


# ──────────────────────────────────────────────────────────────────────────────
# Dimension 2: Earnings Reliability (0–20)
# ──────────────────────────────────────────────────────────────────────────────

def _score_earnings_reliability(raw: dict, history: dict) -> tuple[int, list[str]]:
    """
    EPS CAGR, consecutive growth years, PAT volatility (low = reliable).
    """
    import numpy as np
    notes: list[str] = []
    score = 0

    eps_hist = history.get("eps") or []
    pat_hist = history.get("pat") or []

    # Consecutive positive EPS years (max 8)
    if len(eps_hist) >= 3:
        consec = 0
        for e in reversed(eps_hist):
            if _safe(e, 0) and _safe(e) > 0:
                consec += 1
            else:
                break
        if consec >= 8:
            score += 8; notes.append(f"{consec} consecutive years of positive EPS")
        elif consec >= 5:
            score += 5; notes.append(f"{consec} consecutive years of positive EPS")
        elif consec >= 3:
            score += 3; notes.append(f"{consec} consecutive years of positive EPS")

    # EPS CAGR 5-yr from raw (max 8)
    eps_cagr = _safe(raw.get("eps_cagr_5y") or raw.get("eps_cagr_3y"), 0)
    if eps_cagr >= 20:
        score += 8; notes.append(f"EPS CAGR {eps_cagr:.1f}% — exceptional growth")
    elif eps_cagr >= 12:
        score += 5; notes.append(f"EPS CAGR {eps_cagr:.1f}% — solid growth")
    elif eps_cagr >= 6:
        score += 2; notes.append(f"EPS CAGR {eps_cagr:.1f}% — modest growth")

    # PAT volatility (coefficient of variation — lower = more reliable)
    if len(pat_hist) >= 4:
        vals = [_safe(p) for p in pat_hist if p is not None and _safe(p) is not None]
        if vals and np.mean(vals) != 0:
            cv = np.std(vals) / abs(np.mean(vals))
            if cv < 0.15:
                score += 4; notes.append(f"Low PAT volatility (CV={cv:.2f}) — highly reliable")
            elif cv < 0.30:
                score += 2; notes.append(f"Moderate PAT volatility (CV={cv:.2f})")

    return min(score, 20), notes


# ──────────────────────────────────────────────────────────────────────────────
# Dimension 3: Balance Sheet Prudence (0–20)
# ──────────────────────────────────────────────────────────────────────────────

def _score_balance_sheet(raw: dict, history: dict) -> tuple[int, list[str]]:
    """
    D/E trend, interest coverage, current ratio.
    """
    notes: list[str] = []
    score = 0

    # D/E (lower is better; <0.5 excellent, <1.0 good)
    de = _safe(raw.get("debt_equity"), 999)
    if de <= 0:
        score += 6; notes.append("Debt-free (D/E ≤ 0)")
    elif de <= 0.5:
        score += 5; notes.append(f"Low D/E {de:.2f} — conservative leverage")
    elif de <= 1.0:
        score += 3; notes.append(f"Moderate D/E {de:.2f}")
    elif de <= 2.0:
        score += 1; notes.append(f"Elevated D/E {de:.2f}")

    # Interest coverage (EBIT/interest expense) via ICR proxy
    icr = _safe(raw.get("icr") or raw.get("interest_coverage_ratio"))
    if icr is None:
        # Proxy: EBITDA margin as crude ICR stand-in
        ebitda = _safe(raw.get("ebitda_margin"), 0)
        if ebitda and ebitda > 20:
            score += 3; notes.append(f"High EBITDA margin {ebitda:.1f}% — likely strong coverage")
        elif ebitda and ebitda > 10:
            score += 1
    elif icr >= 10:
        score += 6; notes.append(f"ICR {icr:.1f}x — excellent debt serviceability")
    elif icr >= 4:
        score += 4; notes.append(f"ICR {icr:.1f}x — good coverage")
    elif icr >= 2:
        score += 2; notes.append(f"ICR {icr:.1f}x — adequate coverage")

    # D/E trend (improving = score bonus)
    # If historical data available via screener history, compute slope
    de_hist = history.get("debt_equity") or []
    if len(de_hist) >= 4:
        slope = _trend_slope(de_hist[-6:])
        if slope < -0.05:   # declining D/E over time = improvement
            score += 4; notes.append("D/E improving over time — balance sheet strengthening")
        elif slope < 0:
            score += 2; notes.append("D/E modestly improving")

    return min(score, 20), notes


# ──────────────────────────────────────────────────────────────────────────────
# Dimension 4: Promoter Commitment (0–20)
# ──────────────────────────────────────────────────────────────────────────────

def _score_promoter_commitment(raw: dict, history: dict) -> tuple[int, list[str]]:
    """
    Promoter holding level, pledging %, holding trend.
    """
    notes: list[str] = []
    score = 0

    ph = _safe(raw.get("promoter_holding"), 0)
    pledge = _safe(raw.get("promoter_pledging") or raw.get("pledged_pct"), 0)

    # Promoter holding level
    if ph >= 65:
        score += 8; notes.append(f"High promoter holding {ph:.1f}% — strong owner-operator alignment")
    elif ph >= 50:
        score += 6; notes.append(f"Promoter holding {ph:.1f}%")
    elif ph >= 35:
        score += 3; notes.append(f"Moderate promoter holding {ph:.1f}%")
    elif ph <= 10:
        notes.append("Very low promoter holding — weak owner alignment")

    # Pledging penalty
    if pledge is not None and pledge > 0:
        if pledge > 40:
            score -= 8; notes.append(f"CRITICAL pledging {pledge:.1f}% — severe risk")
        elif pledge > 20:
            score -= 4; notes.append(f"High pledging {pledge:.1f}% — elevated risk")
        elif pledge > 5:
            score -= 1; notes.append(f"Moderate pledging {pledge:.1f}%")

    # Promoter holding trend
    ph_hist = history.get("promoter_holding") or []
    if len(ph_hist) >= 4:
        slope = _trend_slope(ph_hist[-6:])
        if slope > 0.02:
            score += 8; notes.append("Promoter increasing stake — strong conviction")
        elif slope > 0:
            score += 4; notes.append("Promoter holding stable/rising")
        elif slope < -0.05:
            score -= 2; notes.append("Promoter consistently reducing stake")

    return min(max(score, 0), 20), notes


# ──────────────────────────────────────────────────────────────────────────────
# Dimension 5: Shareholder Returns (0–20)
# ──────────────────────────────────────────────────────────────────────────────

def _score_shareholder_returns(raw: dict, history: dict) -> tuple[int, list[str]]:
    """
    Dividend payout consistency, dividend yield, buybacks proxy.
    """
    notes: list[str] = []
    score = 0

    div_yield   = _safe(raw.get("dividend_yield"), 0)
    div_payout  = _safe(raw.get("dividend_payout") or raw.get("payout_ratio"), 0)

    # Dividend yield
    if div_yield and div_yield >= 3:
        score += 6; notes.append(f"High dividend yield {div_yield:.1f}% — strong income")
    elif div_yield and div_yield >= 1:
        score += 3; notes.append(f"Dividend yield {div_yield:.1f}%")

    # Dividend payout consistency (history)
    div_hist = history.get("dividend_payout") or []
    if len(div_hist) >= 4:
        paid_years = sum(1 for d in div_hist if _safe(d, 0) and _safe(d) > 5)
        if paid_years >= len(div_hist) * 0.8:
            score += 6; notes.append(f"Consistent dividends ({paid_years}/{len(div_hist)} years)")
        elif paid_years >= len(div_hist) * 0.5:
            score += 3; notes.append(f"Moderate dividend consistency ({paid_years}/{len(div_hist)} years)")

    # Revenue growth → shareholders benefit proxy
    rev_cagr = _safe(raw.get("revenue_cagr_3y") or raw.get("revenue_cagr"), 0)
    if rev_cagr and rev_cagr >= 15:
        score += 5; notes.append(f"Revenue CAGR {rev_cagr:.1f}% — shareholders benefiting from growth")
    elif rev_cagr and rev_cagr >= 8:
        score += 2

    # EPS CAGR alignment with revenue (shows earnings leverage)
    eps_cagr = _safe(raw.get("eps_cagr_5y") or raw.get("eps_cagr_3y"), 0)
    if eps_cagr and rev_cagr and eps_cagr >= rev_cagr:
        score += 3; notes.append("EPS growing faster than revenue — operating leverage")

    return min(score, 20), notes


# ──────────────────────────────────────────────────────────────────────────────
# Risk flags
# ──────────────────────────────────────────────────────────────────────────────

def _extract_risk_flags(raw: dict, history: dict) -> list[str]:
    """Return list of red-flag strings (non-empty = concerns)."""
    flags: list[str] = []

    pledge = _safe(raw.get("promoter_pledging") or raw.get("pledged_pct"), 0)
    de     = _safe(raw.get("debt_equity"), 0)

    if pledge and pledge > 30:
        flags.append(f"HIGH_PLEDGING ({pledge:.1f}%)")
    if de and de > 3:
        flags.append(f"HIGH_LEVERAGE (D/E={de:.1f}x)")

    eps_hist = history.get("eps") or []
    loss_years = sum(1 for e in eps_hist if _safe(e, 0) and _safe(e) < 0)
    if loss_years >= 3:
        flags.append(f"LOSS_MAKING ({loss_years} of last {len(eps_hist)} years)")

    pat_hist = history.get("pat") or []
    if len(pat_hist) >= 3:
        recent_growth = [
            (_safe(pat_hist[i]) - _safe(pat_hist[i-1])) / max(abs(_safe(pat_hist[i-1], 1)), 1)
            for i in range(1, min(4, len(pat_hist)))
            if pat_hist[i] is not None and pat_hist[i-1] is not None
        ]
        big_misses = sum(1 for g in recent_growth if g < -0.20)
        if big_misses >= 2:
            flags.append(f"EARNINGS_VOLATILITY ({big_misses} large PAT drops)")

    ph_hist = history.get("promoter_holding") or []
    if len(ph_hist) >= 4:
        total_change = _safe(ph_hist[-1], 0) - _safe(ph_hist[-4], 0)
        if total_change and total_change < -10:
            flags.append(f"PROMOTER_SELLING ({total_change:.1f}pp over 4 years)")

    return flags


# ──────────────────────────────────────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────────────────────────────────────

def analyse(symbol: str) -> dict:
    """
    Score management quality for `symbol` across 5 dimensions.

    Returns
    -------
    {
      signal, score (0–100),
      detail: {
        capital_allocation: {score, notes},
        earnings_reliability: {score, notes},
        balance_sheet: {score, notes},
        promoter_commitment: {score, notes},
        shareholder_returns: {score, notes},
      },
      risk_flags: list[str],
      agent_name: "mgmt_quality"
    }
    """
    plain = symbol.replace(".NS", "").replace(".BO", "").upper()

    # ── Fetch data ─────────────────────────────────────────────────────────────
    try:
        from data.fetchers import get_screener_data, get_screener_history
        raw     = get_screener_data(plain)   or {}
        history = get_screener_history(plain) or {}
    except Exception as exc:
        log.warning("mgmt_quality(%s): data fetch failed: %s", plain, exc)
        raw = {}
        history = {}

    if not raw and not history:
        return {
            "signal":      "NO_DATA",
            "score":       0,
            "detail":      {"error": f"No screener data for {plain}"},
            "risk_flags":  [],
            "agent_name":  AGENT_NAME,
        }

    # ── Score each dimension ───────────────────────────────────────────────────
    cap_score,  cap_notes  = _score_capital_allocation(raw, history)
    earn_score, earn_notes = _score_earnings_reliability(raw, history)
    bs_score,   bs_notes   = _score_balance_sheet(raw, history)
    prom_score, prom_notes = _score_promoter_commitment(raw, history)
    sh_score,   sh_notes   = _score_shareholder_returns(raw, history)

    total = cap_score + earn_score + bs_score + prom_score + sh_score
    total = max(0, min(100, total))

    # ── Signal ────────────────────────────────────────────────────────────────
    if total >= 72:
        signal = "STRONG_BUY"
    elif total >= 55:
        signal = "BUY"
    elif total >= 40:
        signal = "HOLD"
    elif total >= 25:
        signal = "AVOID"
    else:
        signal = "SELL"

    # ── Risk flags ────────────────────────────────────────────────────────────
    flags = _extract_risk_flags(raw, history)

    return {
        "signal":     signal,
        "score":      total,
        "detail": {
            "capital_allocation": {
                "score": cap_score, "notes": cap_notes,
            },
            "earnings_reliability": {
                "score": earn_score, "notes": earn_notes,
            },
            "balance_sheet": {
                "score": bs_score, "notes": bs_notes,
            },
            "promoter_commitment": {
                "score": prom_score, "notes": prom_notes,
            },
            "shareholder_returns": {
                "score": sh_score, "notes": sh_notes,
            },
            "raw_inputs": {
                "promoter_holding":  _safe(raw.get("promoter_holding")),
                "pledging_pct":      _safe(raw.get("promoter_pledging") or raw.get("pledged_pct")),
                "debt_equity":       _safe(raw.get("debt_equity")),
                "dividend_yield":    _safe(raw.get("dividend_yield")),
                "eps_cagr_5y":       _safe(raw.get("eps_cagr_5y") or raw.get("eps_cagr_3y")),
            },
        },
        "risk_flags":  flags,
        "agent_name":  AGENT_NAME,
    }


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    from dotenv import load_dotenv
    load_dotenv()
    sym = sys.argv[1] if len(sys.argv) > 1 else "HDFCBANK"
    out = analyse(sym)
    print(json.dumps(out, indent=2))
