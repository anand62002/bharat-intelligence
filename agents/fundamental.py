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

# Sector median EV/EBITDA benchmarks (NSE 2024-2026 calibration).
# Used when P/E is unreliable — heavy depreciation or negative earnings make
# EV/EBITDA the industry-standard metric for these sectors.
#
# Sources: NSE/BSE company filings, Bloomberg consensus, Motilal Oswal sector reports
SECTOR_EV_EBITDA_MAP: dict[str, float] = {
    # ── Telecom ─── spectrum capex inflates EV; EBITDA is the clean metric
    "telecom":                8.5,   # Airtel ~7-9x; Jio implied ~8-10x
    "telecommunications":     8.5,
    "communication services": 8.5,
    # ── Energy / Oil & Gas ─── asset-heavy, cyclical, integrated players
    "energy":                 7.5,   # Reliance, ONGC, BPCL blended
    "oil & gas":              6.5,   # pure upstream/refining
    # ── Utilities / Power ─── regulated RAB model; long-dated cash flows
    "utilities":             11.0,   # NTPC, Power Grid, Tata Power
    # ── Metals & Mining ─── trough/peak cycle; normalised 5-yr avg used
    "metals & mining":        5.5,
    "metals":                 5.5,
    "basic materials":        7.0,
    "materials":              7.0,
    # ── Cement ─── capacity cycle; EBITDA/t is primary metric
    "cement":                11.0,   # Ultratech, Ambuja, ACC
    # ── Infrastructure / Industrials ─── long project cycles
    "infrastructure":        12.0,
    "industrials":           13.0,   # L&T, Siemens, ABB
    "construction":           9.0,
    # ── Conglomerates ─── blended P/E meaningless; EV/EBITDA on consolidated basis
    "diversified":           11.0,   # Reliance (blended), ITC, Bajaj Holdings
    # ── Real Estate ─── sometimes used alongside NAV; pre-sales cycle
    "realty":                14.0,
    "real estate":           14.0,
}

# Sectors where EV/EBITDA is the PRIMARY valuation metric.
# For these sectors: P/E is structurally distorted by large depreciation,
# negative earnings from capex cycles, or blended conglomerate structures.
# EV/EBITDA scoring replaces the P/E valuation sub-score when data is available.
EV_EBITDA_SECTORS: frozenset[str] = frozenset({
    # Telecom — spectrum auctions + 5G amortisation suppress EPS to near-zero
    "telecom", "telecommunications", "communication services",
    # Energy / Metals — highly cyclical; trough-year EPS distorts P/E wildly
    "energy", "oil & gas",
    "metals & mining", "metals", "basic materials", "materials",
    # Utilities — regulated asset base; depreciation-heavy
    "utilities",
    # Infrastructure — project-cycle lumpy earnings
    "infrastructure", "industrials", "construction",
    # Cement — capacity-cycle EPS volatility
    "cement",
    # Conglomerates — blended P/E meaningless
    "diversified",
})

# Banking/NBFC sectors where Price/Book Value (P/B) is the primary valuation
# metric. P/B captures the premium investors pay over tangible book value.
# For banks, ROE drives P/B: a bank sustaining ROE > 15% commands 3–4x P/B;
# a PSU bank at 8% ROE trades near 0.8–1.2x P/B.
BANKING_SECTORS: frozenset[str] = frozenset({
    "banking", "bank", "nbfc", "financial services", "finance",
})

# Sector median P/B benchmarks (NSE 2024-2026 calibration)
SECTOR_PB_MAP: dict[str, float] = {
    "banking":            1.8,   # HDFC Bank 3.5x, SBI 1.5x, PSU banks 0.8-1.2x
    "bank":               1.8,
    "nbfc":               2.5,   # Bajaj Finance 5x; lower-quality 1-2x; blended
    "financial services": 2.5,
    "finance":            2.2,
}

# Danger drop estimates (median historical drawdown from current price)
# calibrated against NSE events: IL&FS, DHFL, ADAG, Videocon, Suzlon, JSPL
# CRITICAL now fires when >= 3 primary triggers hit (ICR < 1 added as 4th possible)
_DANGER_DROP: dict[str, tuple[float, float]] = {
    "CRITICAL": (55.0, 0.82),   # >= 3 primary triggers
    "WARNING":  (30.0, 0.55),   # 2 primary triggers
    "WATCH":    (15.0, 0.30),   # 1 primary trigger
}

# ──────────────────────────────────────────────────────────────────────────────
# Pure scoring functions — each returns (score: int, notes: str)
# ──────────────────────────────────────────────────────────────────────────────

def _score_growth(
    revenue_growth: Optional[float],
    revenue_growth_qoq: Optional[float],
    roce: Optional[float],
    *,
    roe: Optional[float] = None,
) -> tuple[int, str]:
    """
    growth_quality — max 25 pts (capped)
      Revenue YoY:  0/5/10/15 pts
      ROCE quality: 0/3/4/7/10 pts
      ROE quality:  0/1/3/5 bonus pts (keyword-only; supplements ROCE)

    ROE cross-validates ROCE. ROCE measures returns on total capital (equity +
    debt); ROE measures returns to equity shareholders only. For banking sectors
    ROE is the primary profitability metric; for all others it flags whether
    leverage is being used to manufacture returns artificially.
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

    # QoQ momentum (informational note, no pts)
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

    # ROE — capital efficiency supplementary check (max 5 bonus pts)
    # Scores within existing 25-pt cap; does not expand the ceiling.
    if roe is not None:
        if roe >= 20:
            score += 5
            notes.append(f"Excellent ROE {roe:.1f}%")
        elif roe >= 15:
            score += 3
            notes.append(f"Good ROE {roe:.1f}%")
        elif roe >= 10:
            score += 1
            notes.append(f"Moderate ROE {roe:.1f}%")
        else:
            notes.append(f"Weak ROE {roe:.1f}%")

    return min(score, 25), "; ".join(notes)


def _score_profitability(
    ebitda_margin: Optional[float],
    pe: Optional[float],
    sector_pe: float,
    ev_ebitda: Optional[float] = None,
    sector_ev_ebitda: Optional[float] = None,
    prefer_ev_ebitda: bool = False,
    *,
    peg_ratio: Optional[float] = None,
    fcf_yield: Optional[float] = None,
    pat_margin: Optional[float] = None,
    pb_ratio: Optional[float] = None,
    sector_pb: Optional[float] = None,
    prefer_pb: bool = False,
) -> tuple[int, str]:
    """
    profitability — max 25 pts (capped)
      EBITDA margin:        0/5/8/12/15 pts
      Valuation vs sector:  0/2/5/7/10 pts   (P/E | EV/EBITDA | P/B depending on sector)
      PEG adjustment:       ±2 pts modifier   (when P/E scoring is active)
      FCF yield:            0/1/3/5 bonus pts (earnings quality)
      PAT margin:           informational note + 0/−2 pts for loss-making

    Valuation metric priority:
      1. P/B  — when prefer_pb=True (banking/NBFC) and pb_ratio available
      2. EV/EBITDA — when prefer_ev_ebitda=True (telecom, metals, etc.) or P/E ≤ 0
      3. P/E  — default for all other sectors

    All sub-scores sum within the 25-pt cap.
    """
    score = 0
    notes: list[str] = []

    # ── EBITDA margin (max 15 pts) ────────────────────────────────────────────
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

    # ── PAT (net profit) margin — earnings quality note ───────────────────────
    # A large EBITDA-PAT spread signals heavy interest expense or depreciation.
    # Negative PAT margin = loss-making after all costs.
    if pat_margin is not None:
        if pat_margin >= 15:
            notes.append(f"Healthy net margin {pat_margin:.1f}%")
        elif pat_margin >= 5:
            notes.append(f"Moderate net margin {pat_margin:.1f}%")
        elif pat_margin >= 0:
            notes.append(f"Thin net margin {pat_margin:.1f}%")
        else:
            score -= 2
            notes.append(f"Negative net margin {pat_margin:.1f}% — loss-making")

    # ── Valuation vs sector (max 10 pts) ─────────────────────────────────────
    # Priority: P/B for banking → EV/EBITDA for capex-heavy → P/E default
    _use_pb = (
        prefer_pb
        and pb_ratio is not None and pb_ratio > 0
        and sector_pb is not None
    )
    _use_ev = (
        not _use_pb
        and ev_ebitda is not None and ev_ebitda > 0
        and sector_ev_ebitda is not None
        and (prefer_ev_ebitda or (pe is None or pe <= 0))
    )

    if _use_pb:
        # P/B valuation scoring — primary for banking/NBFC
        assert sector_pb is not None
        if pb_ratio <= sector_pb * 0.70:
            score += 10
            notes.append(
                f"Deep value: P/B {pb_ratio:.1f}x vs sector {sector_pb:.1f}x"
            )
        elif pb_ratio <= sector_pb * 0.90:
            score += 7
            notes.append(
                f"Undervalued: P/B {pb_ratio:.1f}x vs sector {sector_pb:.1f}x"
            )
        elif pb_ratio <= sector_pb * 1.10:
            score += 5
            notes.append(
                f"Fairly valued: P/B {pb_ratio:.1f}x ~ sector {sector_pb:.1f}x"
            )
        elif pb_ratio <= sector_pb * 1.40:
            score += 2
            notes.append(
                f"Slight premium: P/B {pb_ratio:.1f}x vs sector {sector_pb:.1f}x"
            )
        else:
            notes.append(
                f"Expensive: P/B {pb_ratio:.1f}x >> sector {sector_pb:.1f}x"
            )
        if pe is not None and pe > 0:
            notes.append(f"(P/E {pe:.1f}x for reference; P/B used for scoring)")

    elif _use_ev:
        # EV/EBITDA valuation scoring
        assert sector_ev_ebitda is not None
        if ev_ebitda <= sector_ev_ebitda * 0.70:
            score += 10
            notes.append(
                f"Deep value: EV/EBITDA {ev_ebitda:.1f}x vs sector {sector_ev_ebitda:.1f}x"
            )
        elif ev_ebitda <= sector_ev_ebitda * 0.90:
            score += 7
            notes.append(
                f"Undervalued: EV/EBITDA {ev_ebitda:.1f}x vs sector {sector_ev_ebitda:.1f}x"
            )
        elif ev_ebitda <= sector_ev_ebitda * 1.10:
            score += 5
            notes.append(
                f"Fairly valued: EV/EBITDA {ev_ebitda:.1f}x ~ sector {sector_ev_ebitda:.1f}x"
            )
        elif ev_ebitda <= sector_ev_ebitda * 1.40:
            score += 2
            notes.append(
                f"Slight premium: EV/EBITDA {ev_ebitda:.1f}x vs sector {sector_ev_ebitda:.1f}x"
            )
        else:
            notes.append(
                f"Expensive: EV/EBITDA {ev_ebitda:.1f}x >> sector {sector_ev_ebitda:.1f}x"
            )
        if pe is not None and pe > 0:
            notes.append(f"(P/E {pe:.1f}x shown for reference; EV/EBITDA used for scoring)")

    else:
        # P/E valuation scoring (default)
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
            notes.append(f"Fairly valued: PE {pe:.1f}x approx sector {sector_pe:.0f}x")
        elif pe <= sector_pe * 1.40:
            score += 2
            notes.append(f"Slight premium: PE {pe:.1f}x vs sector {sector_pe:.0f}x")
        else:
            notes.append(f"Expensive: PE {pe:.1f}x >> sector {sector_pe:.0f}x")

        # PEG ratio adjustment — growth context for P/E valuation (±2 pts)
        # PEG < 0.8: quality growth at value price; PEG > 3.0: expensive for growth rate
        if peg_ratio is not None and peg_ratio > 0:
            if peg_ratio < 0.8:
                score += 2
                notes.append(f"PEG {peg_ratio:.2f} < 0.8 — quality growth at value price")
            elif peg_ratio > 3.0:
                score -= 2
                notes.append(f"PEG {peg_ratio:.2f} > 3.0 — expensive relative to growth rate")

    # ── FCF yield — earnings quality / cash conversion (max 5 bonus pts) ──────
    # Positive FCF yield confirms EBITDA is backed by real cash, not accruals.
    # FCF = None → no pts (unknown; do not inflate score for data-poor stocks)
    if fcf_yield is not None:
        if fcf_yield >= 5:
            score += 5
            notes.append(f"Strong FCF yield {fcf_yield:.1f}% — excellent cash conversion")
        elif fcf_yield >= 3:
            score += 3
            notes.append(f"Good FCF yield {fcf_yield:.1f}%")
        elif fcf_yield >= 1:
            score += 1
            notes.append(f"Modest FCF yield {fcf_yield:.1f}%")
        elif fcf_yield < 0:
            notes.append(f"Negative FCF yield {fcf_yield:.1f}% — consuming cash")

    return min(score, 25), "; ".join(notes)


def _score_balance_sheet(
    debt_equity: Optional[float],
    roce: Optional[float],
    *,
    icr: Optional[float] = None,
    net_debt_ebitda: Optional[float] = None,
    current_ratio: Optional[float] = None,
) -> tuple[int, str]:
    """
    balance_sheet — max 25 pts (capped), min 0 pts
      D/E health:            0/3/7/13/18/20 pts
      ROCE vs leverage:      0/5 bonus pts
      ICR (Interest Coverage): penalty up to −6 pts for inability to service debt
      Net Debt/EBITDA:       ±2/4 pts repayment horizon metric
      Current ratio:         −2 pts penalty if < 1.0 (liquidity squeeze)

    ICR < 1.0 means operating earnings cannot cover interest — a primary danger
    signal. Net Debt/EBITDA cross-validates D/E using cash earnings, not book equity.
    Both are keyword-only with None defaults for full backward compatibility.
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

    # ── Interest Coverage Ratio (ICR = EBIT / Interest Expense) ──────────────
    # ICR < 1 means operating profits cannot cover interest payments — this is
    # the classic early-warning signal seen in IL&FS, Vodafone Idea, DHFL.
    if icr is not None:
        if icr >= 4.0:
            score += 2
            notes.append(f"Strong interest coverage {icr:.1f}x")
        elif icr >= 2.0:
            notes.append(f"Adequate interest coverage {icr:.1f}x")
        elif icr >= 1.0:
            score -= 3
            notes.append(f"Thin interest coverage {icr:.1f}x — debt service risk")
        else:
            score -= 6
            notes.append(f"CRITICAL: ICR {icr:.1f}x < 1.0 — cannot cover interest payments")

    # ── Net Debt / EBITDA — repayment horizon ────────────────────────────────
    # Cross-validates D/E using cash earnings rather than book equity.
    # Net cash position (negative ND/EBITDA) is a positive quality signal.
    if net_debt_ebitda is not None:
        if net_debt_ebitda <= 0:
            score += 4
            notes.append(f"Net cash position (ND/EBITDA {net_debt_ebitda:.1f}x)")
        elif net_debt_ebitda <= 1.5:
            score += 2
            notes.append(f"Low leverage ND/EBITDA {net_debt_ebitda:.1f}x")
        elif net_debt_ebitda <= 3.0:
            notes.append(f"Moderate leverage ND/EBITDA {net_debt_ebitda:.1f}x")
        elif net_debt_ebitda <= 5.0:
            score -= 2
            notes.append(f"High leverage ND/EBITDA {net_debt_ebitda:.1f}x")
        else:
            score -= 4
            notes.append(f"Very high leverage ND/EBITDA {net_debt_ebitda:.1f}x")

    # ── Current ratio — short-term liquidity check ────────────────────────────
    # Current ratio < 1.0 means current liabilities exceed current assets —
    # a potential short-term funding stress (especially relevant for NBFCs/banks).
    if current_ratio is not None:
        if current_ratio >= 2.0:
            notes.append(f"Strong liquidity current ratio {current_ratio:.1f}x")
        elif current_ratio >= 1.0:
            notes.append(f"Adequate liquidity current ratio {current_ratio:.1f}x")
        else:
            score -= 2
            notes.append(f"Tight liquidity: current ratio {current_ratio:.1f}x < 1.0")

    return max(0, min(score, 25)), "; ".join(notes)


def _score_governance(
    promoter_holding: Optional[float],
    promoter_pledging: Optional[float],
    debt_equity: Optional[float],
    *,
    dividend_yield: Optional[float] = None,
) -> tuple[int, str]:
    """
    governance — max 25 pts (after penalties)
      Promoter holding:         0/2/4/8/12/15 pts
      Pledging adjustment:      +10 / +5 / -15 / -30 pts
      D/E>2 governance penalty: -10 pts
      Dividend yield bonus:     0/1/2/3 pts (capital allocation quality signal)

    Dividend yield signals management confidence in free cash flow. Consistent
    dividend payers (ITC, Infosys, Power Grid) demonstrate capital discipline
    and provide an income floor for long-term holders.
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

    # Dividend yield — capital allocation quality bonus (max 3 pts)
    # Consistent dividends signal management confidence in free cash flow
    # sustainability. Relevant for mature compounders and PSU income stocks.
    if dividend_yield is not None and dividend_yield > 0:
        if dividend_yield >= 4.0:
            score += 3
            notes.append(f"High dividend yield {dividend_yield:.1f}% — strong income signal")
        elif dividend_yield >= 2.0:
            score += 2
            notes.append(f"Healthy dividend yield {dividend_yield:.1f}%")
        elif dividend_yield >= 0.5:
            score += 1
            notes.append(f"Dividend paying {dividend_yield:.1f}%")

    return max(0, min(score, 25)), "; ".join(notes)


# ──────────────────────────────────────────────────────────────────────────────
# Danger assessment
# ──────────────────────────────────────────────────────────────────────────────

def _assess_danger(
    revenue_growth: Optional[float],
    debt_equity: Optional[float],
    promoter_pledging: Optional[float],
    ebitda_margin: Optional[float],
    *,
    icr: Optional[float] = None,
    net_debt_ebitda: Optional[float] = None,
) -> tuple[Optional[str], Optional[float], float, list[str]]:
    """
    Returns (danger_level, danger_drop_pct, danger_confidence, trigger_list).

    CRITICAL DANGER fires when >= 3 primary triggers hit (was originally 3 fixed;
    now >= 3 to accommodate the 4th primary trigger — ICR < 1.0):
      1. Revenue YoY < -30%
      2. D/E > 3
      3. Promoter pledging > 50%
      4. ICR < 1.0 (cannot cover interest from operating earnings) [NEW]

    Net Debt/EBITDA > 5x and > 3.5x are secondary signals.

    Secondary signals (WARNING/WATCH) fire on partial matches or
    near-threshold combinations.

    danger_drop_pct is calibrated against NSE historical incidents
    (IL&FS, DHFL, Videocon, ADAG group, Suzlon FY08, Vodafone Idea, Unitech).
    """
    triggers: list[str] = []

    # ── Primary triggers ──────────────────────────────────────────────────────
    if revenue_growth is not None and revenue_growth < -30:
        triggers.append(f"revenue_decline_{abs(revenue_growth):.0f}pct_yoy")

    if debt_equity is not None and debt_equity > 3:
        triggers.append(f"dangerous_leverage_de_{debt_equity:.1f}")

    if promoter_pledging is not None and promoter_pledging > 50:
        triggers.append(f"critical_pledging_{promoter_pledging:.0f}pct")

    # ICR < 1.0: operating earnings cannot cover interest payments
    if icr is not None and icr < 1.0:
        triggers.append(f"interest_not_covered_icr_{icr:.2f}")

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

    # Net Debt/EBITDA — repayment horizon as secondary signal
    if net_debt_ebitda is not None and net_debt_ebitda > 5.0:
        secondary.append(f"very_high_nd_ebitda_{net_debt_ebitda:.1f}x")
    elif net_debt_ebitda is not None and net_debt_ebitda > 3.5:
        secondary.append(f"elevated_nd_ebitda_{net_debt_ebitda:.1f}x")

    n_primary = len(triggers)
    n_secondary = len(secondary)

    if n_primary >= 3:
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


def _estimate_upside_ev_ebitda(
    ebitda_abs: Optional[float],
    shares_outstanding: Optional[float],
    net_debt: Optional[float],
    current_price: Optional[float],
    sector_ev_ebitda: float,
) -> Optional[float]:
    """
    Fair value via EV/EBITDA for telecom, conglomerate and capex-heavy sectors.

    Methodology:
      fair_EV           = sector_median_ev_ebitda * trailing_EBITDA
      fair_equity_value = fair_EV - net_debt
      fair_price        = fair_equity_value / shares_outstanding
      upside_pct        = (fair_price - current_price) / current_price * 100

    This avoids P/E distortion caused by:
      - Large spectrum / 5G amortisation (telecom)
      - Cyclical trough earnings (metals, energy)
      - Conglomerate blended earnings (diversified)

    Returns upside_pct (can be negative = downside) or None if data is insufficient.
    """
    if (
        ebitda_abs is None or ebitda_abs <= 0
        or shares_outstanding is None or shares_outstanding <= 0
        or current_price is None or current_price <= 0
        or net_debt is None
    ):
        return None

    fair_ev = sector_ev_ebitda * ebitda_abs
    fair_equity = fair_ev - net_debt
    if fair_equity <= 0:
        # Net debt exceeds fair EV — deeply distressed; skip rather than emit huge negative
        return None
    fair_price = fair_equity / shares_outstanding
    return round((fair_price - current_price) / current_price * 100, 2)


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

    # ── 2. Resolve sector (caller override takes precedence) ─────────────────
    sector_pe  = DEFAULT_SECTOR_PE
    sector_key = (sector or "").strip().lower()
    if sector_key:
        sector_pe = SECTOR_PE_MAP.get(sector_key, DEFAULT_SECTOR_PE)

    # ── 3. yfinance: one Ticker call fetches ALL market data ─────────────────
    # Single Ticker object avoids redundant HTTP round-trips.
    # Fields fetched (all from info dict unless noted):
    #   Existing:  sector, enterpriseToEbitda, ebitda, sharesOutstanding,
    #              totalDebt, totalCash, history(1d)
    #   Tier 2 new: returnOnEquity, priceToBook, freeCashflow, marketCap,
    #               profitMargins, ebit, interestExpense, dividendYield, currentRatio
    yf_sector_key:      str            = ""
    ev_ebitda:          Optional[float] = None
    ebitda_abs:         Optional[float] = None
    shares_outstanding: Optional[float] = None
    net_debt:           Optional[float] = None
    current_price:      Optional[float] = None
    # Tier 2 new fields
    roe_pct:            Optional[float] = None   # Return on Equity (%)
    pb_ratio:           Optional[float] = None   # Price / Book Value
    fcf_yield:          Optional[float] = None   # Free Cash Flow Yield (%)
    pat_margin:         Optional[float] = None   # Net Profit Margin (%)
    icr:                Optional[float] = None   # Interest Coverage Ratio
    dividend_yield_pct: Optional[float] = None   # Dividend Yield (%)
    current_ratio:      Optional[float] = None   # Current Assets / Current Liabilities

    def _safe_positive_float(val) -> Optional[float]:
        """Parse val as float; return None if invalid or non-positive."""
        try:
            v = float(val)
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None

    try:
        import yfinance as yf
        from data.fetchers import yf_fetch_with_retry
        ticker = yf.Ticker(symbol)
        info   = yf_fetch_with_retry(lambda: ticker.info)

        # Sector → P/E benchmark (only if caller did not provide sector override)
        yf_sector_key = (info.get("sector") or "").lower()
        if yf_sector_key and not sector_key:
            sector_pe = SECTOR_PE_MAP.get(yf_sector_key, DEFAULT_SECTOR_PE)
            data_sources.append("yfinance_sector")

        # EV/EBITDA ratio (direct from yfinance)
        ev_ebitda = _safe_positive_float(info.get("enterpriseToEbitda"))

        # Absolute EBITDA (needed for EV/EBITDA-based fair-value calc)
        ebitda_abs = _safe_positive_float(info.get("ebitda"))

        # Shares outstanding (for price-per-share in fair-value calc)
        shares_outstanding = _safe_positive_float(
            info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
        )

        # Net debt = total debt − cash & equivalents
        try:
            _total_debt = float(info.get("totalDebt") or 0)
            _total_cash = float(info.get("totalCash") or 0)
            net_debt = _total_debt - _total_cash
        except (TypeError, ValueError):
            net_debt = None

        if ev_ebitda is not None:
            data_sources.append("yfinance_ev_ebitda")

        # ── Tier 2: new financial metrics (all zero-cost — same info dict) ───

        # Return on Equity — yfinance returns as decimal (0.18 = 18%)
        _roe_raw = info.get("returnOnEquity")
        if _roe_raw is not None:
            try:
                roe_pct = round(float(_roe_raw) * 100, 2)
            except (TypeError, ValueError):
                pass

        # Price/Book Value
        pb_ratio = _safe_positive_float(info.get("priceToBook"))

        # FCF Yield = Free Cash Flow / Market Cap (expressed as %)
        _fcf_raw    = info.get("freeCashflow")
        _mktcap_raw = info.get("marketCap")
        if _fcf_raw is not None and _mktcap_raw is not None:
            try:
                _mktcap = float(_mktcap_raw)
                if _mktcap > 0:
                    fcf_yield = round(float(_fcf_raw) / _mktcap * 100, 2)
            except (TypeError, ValueError):
                pass

        # PAT (Net Profit) Margin — yfinance returns as decimal
        _pm_raw = info.get("profitMargins")
        if _pm_raw is not None:
            try:
                pat_margin = round(float(_pm_raw) * 100, 2)
            except (TypeError, ValueError):
                pass

        # Interest Coverage Ratio = EBIT / |Interest Expense|
        # Note: yfinance interestExpense is typically negative (it's an expense)
        _ebit_raw    = info.get("ebit")
        _intexp_raw  = info.get("interestExpense")
        if _ebit_raw is not None and _intexp_raw is not None:
            try:
                _ebit    = float(_ebit_raw)
                _int_exp = abs(float(_intexp_raw))
                if _int_exp > 0:
                    icr = round(_ebit / _int_exp, 2)
            except (TypeError, ValueError, ZeroDivisionError):
                pass

        # Dividend Yield — yfinance returns as decimal (0.045 = 4.5%)
        _dy_raw = info.get("dividendYield")
        if _dy_raw is not None:
            try:
                dividend_yield_pct = round(float(_dy_raw) * 100, 2)
            except (TypeError, ValueError):
                pass

        # Current Ratio
        current_ratio = _safe_positive_float(info.get("currentRatio"))

        # Current price (dropna for dividend-adjustment NaN artefacts)
        hist = yf_fetch_with_retry(ticker.history, period="1d")
        if not hist.empty:
            _close = hist["Close"].dropna()
            if not _close.empty:
                current_price = float(_close.iloc[-1])
                data_sources.append("yfinance_price")

    except Exception as exc:
        log.debug("yfinance fetch failed for %s: %s", symbol, exc)

    # ── 3b. Resolve sector benchmarks and preference flags ───────────────────
    effective_sector  = sector_key or yf_sector_key
    sector_ev_ebitda: Optional[float] = SECTOR_EV_EBITDA_MAP.get(effective_sector) \
        if effective_sector else None
    prefer_ev_ebitda: bool = effective_sector in EV_EBITDA_SECTORS
    sector_pb:        Optional[float] = SECTOR_PB_MAP.get(effective_sector) \
        if effective_sector else None
    prefer_pb:        bool = effective_sector in BANKING_SECTORS

    # ── 3c. Pre-compute derived ratios (zero extra API calls) ────────────────
    # PEG ratio: P/E ÷ revenue growth (proxy for EPS growth)
    peg_ratio: Optional[float] = None
    if pe is not None and pe > 0 and revenue_growth is not None and revenue_growth > 0:
        peg_ratio = round(pe / revenue_growth, 2)

    # Net Debt / EBITDA: repayment horizon cross-check
    net_debt_ebitda: Optional[float] = None
    if net_debt is not None and ebitda_abs is not None and ebitda_abs > 0:
        net_debt_ebitda = round(net_debt / ebitda_abs, 2)

    # ── 4. Score ─────────────────────────────────────────────────────────────
    growth_score,  growth_notes  = _score_growth(
        revenue_growth, revenue_growth_qoq, roce,
        roe=roe_pct,
    )
    profit_score,  profit_notes  = _score_profitability(
        ebitda_margin, pe, sector_pe,
        ev_ebitda=ev_ebitda,
        sector_ev_ebitda=sector_ev_ebitda,
        prefer_ev_ebitda=prefer_ev_ebitda,
        peg_ratio=peg_ratio,
        fcf_yield=fcf_yield,
        pat_margin=pat_margin,
        pb_ratio=pb_ratio,
        sector_pb=sector_pb,
        prefer_pb=prefer_pb,
    )
    bs_score,      bs_notes      = _score_balance_sheet(
        debt_equity, roce,
        icr=icr,
        net_debt_ebitda=net_debt_ebitda,
        current_ratio=current_ratio,
    )
    gov_score,     gov_notes     = _score_governance(
        promoter_holding, promoter_pledging, debt_equity,
        dividend_yield=dividend_yield_pct,
    )

    total_score = growth_score + profit_score + bs_score + gov_score
    total_score = max(0, min(100, total_score))

    # ── 5. Danger assessment ─────────────────────────────────────────────────
    danger_level, danger_drop_pct, danger_confidence, danger_triggers = _assess_danger(
        revenue_growth, debt_equity, promoter_pledging, ebitda_margin,
        icr=icr,
        net_debt_ebitda=net_debt_ebitda,
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
    # For EV/EBITDA-native sectors, fair value via EV/EBITDA is more reliable.
    # We compute both and prefer EV/EBITDA when the sector warrants it.
    upside_pe: Optional[float] = _estimate_upside(pe, revenue_growth, current_price, sector_pe)
    upside_ev: Optional[float] = None
    if prefer_ev_ebitda and sector_ev_ebitda is not None:
        upside_ev = _estimate_upside_ev_ebitda(
            ebitda_abs, shares_outstanding, net_debt, current_price, sector_ev_ebitda
        )
    # EV/EBITDA upside is primary for EV/EBITDA sectors when available;
    # P/E upside is kept as fallback or reference.
    upside_pct: Optional[float] = (
        upside_ev if (prefer_ev_ebitda and upside_ev is not None) else upside_pe
    )
    valuation_method = (
        "ev_ebitda" if (prefer_ev_ebitda and upside_ev is not None) else "pe"
    )

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
            "roe":                 roe_pct,
            "notes":               growth_notes,
        },
        "profitability": {
            "score":              profit_score,
            "ebitda_margin":      ebitda_margin,
            "pat_margin":         pat_margin,
            "pe":                 pe,
            "peg_ratio":          peg_ratio,
            "sector_pe_used":     sector_pe,
            "ev_ebitda":          ev_ebitda,
            "sector_ev_ebitda":   sector_ev_ebitda,
            "prefer_ev_ebitda":   prefer_ev_ebitda,
            "pb_ratio":           pb_ratio,
            "sector_pb":          sector_pb,
            "prefer_pb":          prefer_pb,
            "fcf_yield":          fcf_yield,
            "valuation_method":   valuation_method,
            "notes":              profit_notes,
        },
        "balance_sheet": {
            "score":            bs_score,
            "debt_equity":      debt_equity,
            "roce":             roce,
            "icr":              icr,
            "net_debt_ebitda":  net_debt_ebitda,
            "current_ratio":    current_ratio,
            "notes":            bs_notes,
        },
        "governance": {
            "score":              gov_score,
            "promoter_holding":   promoter_holding,
            "promoter_pledging":  promoter_pledging,
            "debt_equity":        debt_equity,
            "dividend_yield":     dividend_yield_pct,
            "notes":              gov_notes,
        },
        "danger": {
            "level":      danger_level,
            "triggers":   danger_triggers,
            "drop_pct":   danger_drop_pct,
            "confidence": danger_confidence,
        },
        "raw_metrics": {
            # Screener.in sourced
            "pe":                  pe,
            "revenue_growth":      revenue_growth,
            "revenue_growth_qoq":  revenue_growth_qoq,
            "ebitda_margin":       ebitda_margin,
            "debt_equity":         debt_equity,
            "roce":                roce,
            "promoter_holding":    promoter_holding,
            "promoter_pledging":   promoter_pledging,
            # yfinance sourced (existing)
            "current_price":       current_price,
            "sector_pe":           sector_pe,
            "ev_ebitda":           ev_ebitda,
            "sector_ev_ebitda":    sector_ev_ebitda,
            "ebitda_abs":          ebitda_abs,
            "net_debt":            net_debt,
            "shares_outstanding":  shares_outstanding,
            "upside_pe_pct":       upside_pe,
            "upside_ev_pct":       upside_ev,
            "valuation_method":    valuation_method,
            # yfinance sourced (Tier 2 new)
            "roe":                 roe_pct,
            "pb_ratio":            pb_ratio,
            "sector_pb":           sector_pb,
            "fcf_yield":           fcf_yield,
            "pat_margin":          pat_margin,
            "icr":                 icr,
            "net_debt_ebitda":     net_debt_ebitda,
            "current_ratio":       current_ratio,
            "dividend_yield":      dividend_yield_pct,
            # Derived
            "peg_ratio":           peg_ratio,
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
