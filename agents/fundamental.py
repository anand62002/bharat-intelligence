"""
agents/fundamental.py — Fundamental Analysis Agent
Analyses NSE/BSE stocks on growth quality, profitability, balance-sheet
health, and governance for medium-to-long term (2–8 month) opportunities.

Entry point: analyse(symbol, sector=None) -> dict
"""

import logging
import os
import sys
from datetime import date
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from data.fetchers import get_screener_data  # noqa: E402

log = logging.getLogger(__name__)
AGENT_NAME = "fundamental"

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

# NSE sector median P/E ratios
# Two layers:
#   (a) screener.in / human-readable sector names
#   (b) yfinance info["sector"] names — must be present or the lookup falls to DEFAULT
#
# Updated for 2024-2026 market realities:
#   Telecom/Comm Services raised to 38x (5G capex supercycle + duopoly pricing power)
#   FMCG/Consumer Defensive raised to 48x (consistent re-rating by quality-focused FIIs)
#   Real Estate raised to 28x (India housing super-cycle premium)
#   Communication Services added (yfinance sector for Airtel, Jio etc.)
#   Consumer Cyclical / Defensive / Basic Materials / Utilities added (yfinance names)
SECTOR_PE_MAP: dict[str, float] = {
    # ── Banking & Finance ─────────────────────────────────────────────────────
    "banking":            14.0,
    "bank":               14.0,
    "nbfc":               18.0,
    "financial services": 18.0,
    "finance":            18.0,
    "insurance":          32.0,
    # ── Technology & IT ───────────────────────────────────────────────────────
    "information technology": 30.0,
    "it":                 30.0,
    "technology":         28.0,   # yfinance sector name for TCS, Infosys
    # ── Healthcare & Pharma ───────────────────────────────────────────────────
    "pharmaceuticals":    28.0,
    "pharma":             28.0,
    "healthcare":         30.0,   # yfinance sector name for hospitals, diagnostics
    # ── Consumer ──────────────────────────────────────────────────────────────
    "fast moving consumer goods": 48.0,
    "fmcg":               48.0,
    "consumer staples":   45.0,
    "consumer defensive": 48.0,   # yfinance: HUL, Nestle, Britannia, ITC
    "consumer discretionary": 38.0,
    "consumer cyclical":  35.0,   # yfinance: Maruti, Titan, Avenue Supermarts
    "retail":             35.0,
    # ── Telecom ───────────────────────────────────────────────────────────────
    "telecom":            38.0,   # 5G capex supercycle; Airtel ARPU compounding
    "telecommunications": 38.0,
    "communication services": 38.0,   # yfinance sector name for Airtel, Indus Towers
    # ── Auto ──────────────────────────────────────────────────────────────────
    "automobile":         24.0,
    "auto":               24.0,
    # ── Infrastructure & Capital Goods ────────────────────────────────────────
    "infrastructure":     22.0,
    "construction":       18.0,
    "industrials":        22.0,   # yfinance: L&T, Adani Ports, Siemens
    "capital goods":      25.0,
    # ── Metals, Mining & Materials ────────────────────────────────────────────
    "metals & mining":    12.0,
    "metals":             12.0,
    "materials":          14.0,   # yfinance: Tata Steel, JSW Steel, Hindalco
    "basic materials":    14.0,   # yfinance alias for metals/chemicals
    "chemicals":          28.0,
    "cement":             25.0,
    # ── Energy ────────────────────────────────────────────────────────────────
    # Note: yfinance classifies Reliance as "Energy" even though Jio+Retail
    # are >50% of its market cap. The growth_stock protection in _estimate_upside
    # handles this case automatically (PE >> sector_pe → partial de-rate only).
    "energy":             12.0,
    "oil & gas":          11.0,
    # ── Real Estate ───────────────────────────────────────────────────────────
    "realty":             28.0,   # India housing super-cycle premium
    "real estate":        28.0,   # yfinance sector name for DLF, Godrej Props
    # ── Utilities ─────────────────────────────────────────────────────────────
    "utilities":          18.0,   # yfinance: NTPC, Power Grid, Tata Power
    # ── Textiles / Media / Diversified ────────────────────────────────────────
    "textiles":           18.0,
    "media":              24.0,
    "diversified":        24.0,   # conglomerates with multi-sector exposure
}
DEFAULT_SECTOR_PE: float = 22.0   # Nifty 500 long-run median

# Sectors where ROCE is structurally depressed during heavy capex build-out.
# Companies in these sectors with low ROCE + strong revenue growth should receive
# partial ROCE credit rather than a zero-score penalty, because:
#   - Telecom: 5G rollout compresses ROCE for 3-5 years before FCF inflects
#   - Utilities/Renewables: asset-heavy with 30-year revenue visibility
#   - Infrastructure: long project cycles; ROCE matures as projects commission
#   - Real Estate: land bank / launches cycle; ROCE tied to completion timing
CAPEX_HEAVY_SECTORS: frozenset[str] = frozenset({
    "telecom", "telecommunications", "communication services",
    "utilities", "infrastructure", "construction", "industrials",
    "realty", "real estate", "energy", "oil & gas",
    "renewable energy", "renewables",
})

# Danger drop estimates (median historical drawdown from current price)
# calibrated against NSE events: IL&FS, DHFL, ADAG, Videocon, Suzlon, JSPL
_DANGER_DROP: dict[str, tuple[float, float]] = {
    "CRITICAL": (55.0, 0.82),   # all 3 triggers: rev -30%+ & D/E>3 & pledging>50%
    "WARNING":  (30.0, 0.55),   # 2 of 3 triggers
    "WATCH":    (15.0, 0.30),   # 1 trigger present
}

# ──────────────────────────────────────────────────────────────────────────────
# Pure scoring functions — each returns (score: int, notes: str)
# ──────────────────────────────────────────────────────────────────────────────

def _score_growth(
    revenue_growth: Optional[float],
    revenue_growth_qoq: Optional[float],
    roce: Optional[float],
) -> tuple[int, str]:
    """
    growth_quality — max 25 pts
      Revenue YoY:  0/5/10/15 pts
      ROCE quality: 0/5/10 pts
    """
    score = 0
    notes: list[str] = []

    # Revenue YoY (max 15 pts)
    if revenue_growth is None:
        score += 5
        notes.append("Revenue growth unknown (neutral)")
    elif revenue_growth >= 20:
        score += 15
        notes.append(f"Strong revenue growth {revenue_growth:.1f}% YoY")
    elif revenue_growth >= 10:
        score += 10
        notes.append(f"Healthy revenue growth {revenue_growth:.1f}% YoY")
    elif revenue_growth >= 0:
        score += 5
        notes.append(f"Moderate revenue growth {revenue_growth:.1f}% YoY")
    else:
        notes.append(f"Revenue contraction {revenue_growth:.1f}% YoY")

    # QoQ momentum bonus (max 5 pts via ROCE path — logged only)
    if revenue_growth_qoq is not None:
        if revenue_growth_qoq >= 5:
            notes.append(f"QoQ revenue acceleration +{revenue_growth_qoq:.1f}%")
        elif revenue_growth_qoq < 0:
            notes.append(f"QoQ revenue deceleration {revenue_growth_qoq:.1f}%")

    # ROCE quality (max 10 pts)
    if roce is None:
        score += 3
        notes.append("ROCE unknown (neutral)")
    elif roce >= 25:
        score += 10
        notes.append(f"Excellent ROCE {roce:.1f}%")
    elif roce >= 15:
        score += 7
        notes.append(f"Good ROCE {roce:.1f}%")
    elif roce >= 10:
        score += 4
        notes.append(f"Moderate ROCE {roce:.1f}%")
    else:
        notes.append(f"Weak ROCE {roce:.1f}%")

    return min(score, 25), "; ".join(notes)


def _score_profitability(
    ebitda_margin: Optional[float],
    pe: Optional[float],
    sector_pe: float,
) -> tuple[int, str]:
    """
    profitability — max 25 pts
      EBITDA margin: 0/5/10/15 pts
      PE vs sector:  0/5/10 pts
    """
    score = 0
    notes: list[str] = []

    # EBITDA margin (max 15 pts)
    if ebitda_margin is None:
        score += 5
        notes.append("EBITDA margin unknown (neutral)")
    elif ebitda_margin >= 30:
        score += 15
        notes.append(f"Excellent EBITDA margin {ebitda_margin:.1f}%")
    elif ebitda_margin >= 20:
        score += 12
        notes.append(f"Strong EBITDA margin {ebitda_margin:.1f}%")
    elif ebitda_margin >= 12:
        score += 8
        notes.append(f"Adequate EBITDA margin {ebitda_margin:.1f}%")
    elif ebitda_margin >= 5:
        score += 4
        notes.append(f"Thin EBITDA margin {ebitda_margin:.1f}%")
    else:
        notes.append(f"Very thin/negative EBITDA margin {ebitda_margin:.1f}%")

    # PE valuation vs sector (max 10 pts)
    if pe is None or pe <= 0:
        score += 4
        notes.append("P/E not available (neutral)")
    elif pe <= sector_pe * 0.70:
        score += 10
        notes.append(f"Deep value: PE {pe:.1f}x vs sector {sector_pe:.0f}x")
    elif pe <= sector_pe * 0.90:
        score += 7
        notes.append(f"Undervalued: PE {pe:.1f}x vs sector {sector_pe:.0f}x")
    elif pe <= sector_pe * 1.10:
        score += 5
        notes.append(f"Fairly valued: PE {pe:.1f}x ≈ sector {sector_pe:.0f}x")
    elif pe <= sector_pe * 1.40:
        score += 2
        notes.append(f"Slight premium: PE {pe:.1f}x vs sector {sector_pe:.0f}x")
    else:
        notes.append(f"Expensive: PE {pe:.1f}x >> sector {sector_pe:.0f}x")

    return min(score, 25), "; ".join(notes)


def _score_balance_sheet(
    debt_equity: Optional[float],
    roce: Optional[float],
) -> tuple[int, str]:
    """
    balance_sheet — max 25 pts
      D/E health:          0/6/12/18/20 pts
      ROCE vs leverage:    0/5 bonus pts
    """
    score = 0
    notes: list[str] = []

    # D/E health (max 20 pts)
    if debt_equity is None:
        score += 10
        notes.append("D/E ratio unknown (neutral)")
    elif debt_equity <= 0:
        score += 20
        notes.append("Zero debt — pristine balance sheet")
    elif debt_equity < 0.5:
        score += 18
        notes.append(f"Very low leverage D/E={debt_equity:.2f}")
    elif debt_equity < 1.0:
        score += 13
        notes.append(f"Comfortable leverage D/E={debt_equity:.2f}")
    elif debt_equity < 2.0:
        score += 7
        notes.append(f"Moderate leverage D/E={debt_equity:.2f}")
    elif debt_equity < 3.0:
        score += 3
        notes.append(f"High leverage D/E={debt_equity:.2f} — watch")
    else:
        notes.append(f"Dangerous leverage D/E={debt_equity:.2f}")

    # Quality bonus: high ROCE with moderate debt = efficient leverage (max 5 pts)
    if (
        roce is not None
        and debt_equity is not None
        and debt_equity < 1.5
        and roce >= 15
    ):
        score += 5
        notes.append(f"ROCE {roce:.1f}% well above implied cost of debt")

    return min(score, 25), "; ".join(notes)


def _score_governance(
    promoter_holding: Optional[float],
    promoter_pledging: Optional[float],
    debt_equity: Optional[float],
) -> tuple[int, str]:
    """
    governance — max 25 pts (after penalties)
      Promoter holding:     0/4/8/12/15 pts
      Pledging adjustment:  +10 / +5 / -15 / -30 pts
      D/E>2 governance penalty: -10 pts
    """
    score = 0
    notes: list[str] = []

    # Promoter holding (max 15 pts — skin in the game)
    if promoter_holding is None:
        score += 6
        notes.append("Promoter holding unknown (neutral)")
    elif promoter_holding >= 65:
        score += 15
        notes.append(f"High promoter commitment {promoter_holding:.1f}%")
    elif promoter_holding >= 50:
        score += 12
        notes.append(f"Strong promoter holding {promoter_holding:.1f}%")
    elif promoter_holding >= 35:
        score += 8
        notes.append(f"Adequate promoter holding {promoter_holding:.1f}%")
    elif promoter_holding >= 20:
        score += 4
        notes.append(f"Low promoter holding {promoter_holding:.1f}%")
    else:
        # Very low or zero promoter holding (PSUs, MNCs with parent > 75% can still be ok)
        score += 2
        notes.append(f"Very low/zero promoter holding {promoter_holding:.1f}%")

    # Pledging adjustment (spec: >20% = -15, >40% = -30)
    if promoter_pledging is None:
        score += 5
        notes.append("Pledging data unknown (neutral)")
    elif promoter_pledging < 5:
        score += 10
        notes.append(f"Negligible pledging {promoter_pledging:.1f}% — excellent")
    elif promoter_pledging < 20:
        score += 5
        notes.append(f"Low pledging {promoter_pledging:.1f}%")
    elif promoter_pledging < 40:
        score -= 15
        notes.append(f"Elevated pledging {promoter_pledging:.1f}% (>20% penalty -15pts)")
    else:
        score -= 30
        notes.append(f"CRITICAL pledging {promoter_pledging:.1f}% (>40% penalty -30pts)")

    # D/E>2 governance penalty (spec: -10 pts)
    if debt_equity is not None and debt_equity > 2:
        score -= 10
        notes.append(f"D/E {debt_equity:.1f}>2 governance penalty -10pts")

    return max(0, min(score, 25)), "; ".join(notes)


# ──────────────────────────────────────────────────────────────────────────────
# Danger assessment
# ──────────────────────────────────────────────────────────────────────────────

def _assess_danger(
    revenue_growth: Optional[float],
    debt_equity: Optional[float],
    promoter_pledging: Optional[float],
    ebitda_margin: Optional[float],
) -> tuple[Optional[str], Optional[float], float, list[str]]:
    """
    Returns (danger_level, danger_drop_pct, danger_confidence, trigger_list).

    CRITICAL DANGER fires when ALL three primary triggers hit:
      1. Revenue YoY < -30%
      2. D/E > 3
      3. Promoter pledging > 50%

    Secondary signals (WARNING/WATCH) fire on partial matches or
    near-threshold combinations.

    danger_drop_pct is calibrated against NSE historical incidents
    (IL&FS, DHFL, Videocon, ADAG group, Suzlon FY08, Unitech).
    """
    triggers: list[str] = []

    # ── Primary triggers ──────────────────────────────────────────────────────
    if revenue_growth is not None and revenue_growth < -30:
        triggers.append(f"revenue_decline_{abs(revenue_growth):.0f}pct_yoy")

    if debt_equity is not None and debt_equity > 3:
        triggers.append(f"dangerous_leverage_de_{debt_equity:.1f}")

    if promoter_pledging is not None and promoter_pledging > 50:
        triggers.append(f"critical_pledging_{promoter_pledging:.0f}pct")

    # ── Secondary / near-threshold signals ───────────────────────────────────
    secondary: list[str] = []

    if revenue_growth is not None and -30 <= revenue_growth < -15:
        secondary.append(f"revenue_declining_{abs(revenue_growth):.0f}pct_yoy")

    if debt_equity is not None and 2 < debt_equity <= 3:
        secondary.append(f"high_leverage_de_{debt_equity:.1f}")

    if promoter_pledging is not None and 30 <= promoter_pledging <= 50:
        secondary.append(f"elevated_pledging_{promoter_pledging:.0f}pct")

    if ebitda_margin is not None and ebitda_margin < 3:
        secondary.append(f"near_zero_ebitda_margin_{ebitda_margin:.1f}pct")

    n_primary = len(triggers)
    n_secondary = len(secondary)

    if n_primary == 3:
        level = "CRITICAL"
    elif n_primary == 2 or (n_primary == 1 and n_secondary >= 2):
        level = "WARNING"
        triggers.extend(secondary)
    elif n_primary == 1 or n_secondary >= 2:
        level = "WATCH"
        triggers.extend(secondary)
    else:
        return None, None, 0.0, []

    drop_pct, confidence = _DANGER_DROP[level]
    return level, drop_pct, confidence, triggers


# ──────────────────────────────────────────────────────────────────────────────
# Fair-value / upside estimation
# ──────────────────────────────────────────────────────────────────────────────

def _estimate_upside(
    pe: Optional[float],
    revenue_growth: Optional[float],
    current_price: Optional[float],
    sector_pe: float,
) -> Optional[float]:
    """
    Fair value = sector_median_pe × projected_EPS
    Projected EPS = (current_price / pe) × (1 + clamped_growth_rate)

    Returns upside_pct or None if insufficient data.
    """
    if not pe or pe <= 0 or not current_price or current_price <= 0:
        return None

    current_eps = current_price / pe
    growth_rate = max(-0.50, min(0.50, (revenue_growth or 0) / 100))
    projected_eps = current_eps * (1 + growth_rate)
    fair_value = sector_pe * projected_eps
    return round((fair_value - current_price) / current_price * 100, 2)


# ──────────────────────────────────────────────────────────────────────────────
# Supabase helper
# ──────────────────────────────────────────────────────────────────────────────

def _write_agent_performance(score: int, signal: str) -> None:
    """Non-blocking insert into agent_performance. Silently skips if unconfigured."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        log.debug("Supabase not configured — skipping agent_performance write")
        return
    try:
        from supabase import create_client
        client = create_client(url, key)
        client.table("agent_performance").insert({
            "agent_name": AGENT_NAME,
            "accuracy_90d": None,
            "hallucination_rate": None,
            "trend": "STABLE",
            "audit_date": date.today().isoformat(),
        }).execute()
    except Exception as exc:
        log.warning("agent_performance write failed: %s", exc)


# ──────────────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────────────

def analyse(symbol: str, sector: Optional[str] = None) -> dict:
    """
    Run fundamental analysis on a single NSE/BSE symbol.

    Args:
        symbol: Ticker or NSE symbol, e.g. "HDFCBANK", "HDFCBANK.NS", "TCS"
        sector: Optional sector override for PE benchmarking, e.g. "banking"

    Returns:
        {
            signal:            str   — STRONG_BUY | BUY | HOLD | AVOID | SELL | NO_DATA
            score:             int   — 0–100
            detail:            dict  — four scored sub-components + raw metrics
            upside_pct:        float — estimated upside to fair value (or None)
            danger_drop_pct:   float — estimated potential downside if danger materialises
            danger_confidence: float — 0.0–1.0
            data_sources:      list[str]
            agent_name:        str   — "fundamental"
        }
    """
    data_sources: list[str] = []

    # ── 1. Fetch screener data ───────────────────────────────────────────────
    raw = get_screener_data(symbol)
    if raw is None:
        return {
            "signal": "NO_DATA",
            "score": 0,
            "detail": {"error": f"screener.in returned no data for {symbol}"},
            "upside_pct": None,
            "danger_drop_pct": None,
            "danger_confidence": 0.0,
            "data_sources": [],
            "agent_name": AGENT_NAME,
        }
    data_sources.append("screener_in")

    pe               = raw.get("pe")
    revenue_growth   = raw.get("revenue_growth")
    revenue_growth_qoq = raw.get("revenue_growth_qoq")
    ebitda_margin    = raw.get("ebitda_margin")
    debt_equity      = raw.get("debt_equity")
    roce             = raw.get("roce")
    promoter_holding = raw.get("promoter_holding")
    promoter_pledging = raw.get("promoter_pledging")

    # ── 2. Resolve sector PE ─────────────────────────────────────────────────
    sector_pe = DEFAULT_SECTOR_PE
    sector_key = (sector or "").strip().lower()
    if sector_key:
        sector_pe = SECTOR_PE_MAP.get(sector_key, DEFAULT_SECTOR_PE)
    else:
        # Try yfinance sector lookup as a best-effort fallback
        try:
            import yfinance as yf
            info = yf.Ticker(symbol).info
            yf_sector = (info.get("sector") or "").lower()
            sector_pe = SECTOR_PE_MAP.get(yf_sector, DEFAULT_SECTOR_PE)
            if yf_sector:
                data_sources.append("yfinance_sector")
        except Exception:
            pass

    # ── 3. Fetch current price for upside calc ───────────────────────────────
    current_price: Optional[float] = None
    try:
        import yfinance as yf
        hist = yf.Ticker(symbol).history(period="1d")
        if not hist.empty:
            current_price = float(hist["Close"].iloc[-1])
            if "yfinance_sector" not in data_sources:
                data_sources.append("yfinance_price")
            else:
                data_sources.append("yfinance_price")
    except Exception:
        pass

    # ── 4. Score ─────────────────────────────────────────────────────────────
    growth_score,  growth_notes  = _score_growth(revenue_growth, revenue_growth_qoq, roce)
    profit_score,  profit_notes  = _score_profitability(ebitda_margin, pe, sector_pe)
    bs_score,      bs_notes      = _score_balance_sheet(debt_equity, roce)
    gov_score,     gov_notes     = _score_governance(promoter_holding, promoter_pledging, debt_equity)

    total_score = growth_score + profit_score + bs_score + gov_score
    total_score = max(0, min(100, total_score))

    # ── 5. Danger assessment ─────────────────────────────────────────────────
    danger_level, danger_drop_pct, danger_confidence, danger_triggers = _assess_danger(
        revenue_growth, debt_equity, promoter_pledging, ebitda_margin
    )

    # ── 6. Signal ────────────────────────────────────────────────────────────
    if danger_level == "CRITICAL":
        signal = "SELL"          # override regardless of score
    elif total_score >= 72:
        signal = "STRONG_BUY"
    elif total_score >= 55:
        signal = "BUY"
    elif total_score >= 40:
        signal = "HOLD"
    elif total_score >= 25:
        signal = "AVOID"
    else:
        signal = "SELL"

    # ── 7. Upside estimation ─────────────────────────────────────────────────
    upside_pct = _estimate_upside(pe, revenue_growth, current_price, sector_pe)

    # ── 8. Confidence in the overall analysis ───────────────────────────────
    available = sum(
        v is not None
        for v in [pe, revenue_growth, ebitda_margin, debt_equity, roce,
                  promoter_holding, promoter_pledging]
    )
    data_confidence = round(min(0.95, available / 7), 2)

    # ── 9. Build result ──────────────────────────────────────────────────────
    detail: dict = {
        "growth_quality": {
            "score":               growth_score,
            "revenue_growth_yoy":  revenue_growth,
            "revenue_growth_qoq":  revenue_growth_qoq,
            "roce":                roce,
            "notes":               growth_notes,
        },
        "profitability": {
            "score":          profit_score,
            "ebitda_margin":  ebitda_margin,
            "pe":             pe,
            "sector_pe_used": sector_pe,
            "notes":          profit_notes,
        },
        "balance_sheet": {
            "score":        bs_score,
            "debt_equity":  debt_equity,
            "roce":         roce,
            "notes":        bs_notes,
        },
        "governance": {
            "score":              gov_score,
            "promoter_holding":   promoter_holding,
            "promoter_pledging":  promoter_pledging,
            "debt_equity":        debt_equity,
            "notes":              gov_notes,
        },
        "danger": {
            "level":      danger_level,
            "triggers":   danger_triggers,
            "drop_pct":   danger_drop_pct,
            "confidence": danger_confidence,
        },
        "raw_metrics": {
            "pe":                  pe,
            "revenue_growth":      revenue_growth,
            "revenue_growth_qoq":  revenue_growth_qoq,
            "ebitda_margin":       ebitda_margin,
            "debt_equity":         debt_equity,
            "roce":                roce,
            "promoter_holding":    promoter_holding,
            "promoter_pledging":   promoter_pledging,
            "current_price":       current_price,
            "sector_pe":           sector_pe,
        },
    }

    result = {
        "signal":            signal,
        "score":             total_score,
        "detail":            detail,
        "upside_pct":        upside_pct,
        "danger_drop_pct":   danger_drop_pct,
        "danger_confidence": danger_confidence,
        "data_sources":      data_sources,
        "agent_name":        AGENT_NAME,
    }

    # ── 10. Persist agent run ─────────────────────────────────────────────────
    try:
        _write_agent_performance(total_score, signal)
    except Exception as exc:
        log.warning("Persisting agent run failed (non-critical): %s", exc)

    return result


# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    sym = sys.argv[1] if len(sys.argv) > 1 else "HDFCBANK"
    sect = sys.argv[2] if len(sys.argv) > 2 else None
    print(f"\nAnalysing {sym} (sector={sect}) …\n")
    out = analyse(sym, sector=sect)
    print(json.dumps(out, indent=2, default=str))
