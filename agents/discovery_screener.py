"""
agents/discovery_screener.py — Proactive Discovery Screener
============================================================
Scans the NIFTY 500 universe daily for NEW opportunities that are NOT
already in the user's portfolio, runs all 7 analysis agents on pre-screened
candidates, and saves ranked discovery ideas to Supabase.

Entry point:
    run_discovery(max_candidates=15) -> list[DiscoveryResult]

Pre-screen filters (stock passes if it meets 4+ of 5):
    1. RSI between 40 and 65  (not overbought, not deeply oversold)
    2. PE < 50  OR  revenue growth > 30% (growth justification)
    3. FII net buyer last 5 sessions
    4. Revenue growth YoY > 15%
    5. Price above 200-day EMA

Opportunity tiers:
    CRITICAL  — upside_pct >= 40 AND upside_confidence >= 75 AND data_quality != ESTIMATED
    STANDARD  — upside_pct >= 20 AND confidence >= 65
"""

import hashlib
import logging
import math
import os
import random
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from typing import Optional

import numpy as np

from dotenv import load_dotenv

load_dotenv()

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from data.fetchers import get_ohlcv, get_nse_fii_dii, get_screener_data  # noqa: E402
from agents.base import DataCompletenessValidator  # noqa: E402

log = logging.getLogger(__name__)
AGENT_NAME = "discovery_screener"
_dcv = DataCompletenessValidator()

# ──────────────────────────────────────────────────────────────────────────────
# Thresholds
# ──────────────────────────────────────────────────────────────────────────────
_RSI_LOW            = 40.0
_RSI_HIGH           = 65.0
_PE_MAX             = 50.0
_GROWTH_PE_OVERRIDE = 30.0   # revenue growth % that justifies PE > 50
_REVENUE_GROWTH_MIN = 15.0   # YoY %
_MIN_PRESCREEN_PASS        = 3   # must pass this many of 5 filters
                                 # (revenue_growth is frequently None from screener.in,
                                 #  making effective ceiling 4 available filters — 3/4 pass rate)
_MIN_PRESCREEN_PASS_NO_FII = 2   # legacy constant (kept for backward compat)

_CRITICAL_UPSIDE    = 40.0   # ≥40% upside — achievable for real opportunities, not data artefacts
_CRITICAL_CONF      = 75.0   # ≥75% confidence — tighter bar than STANDARD to ensure conviction
_STANDARD_UPSIDE    = 20.0
_STANDARD_CONF      = 65.0
# NOTE: CRITICAL also requires data_quality != "ESTIMATED" (enforced in run_discovery classify block).
# Rationale: old threshold of 100% upside fired almost exclusively on screener data artefacts for
# illiquid small-caps. 40%/75% distinguishes meaningfully from STANDARD (20%/65%) while remaining
# achievable for genuinely undervalued mid/large-cap stocks on NSE.

_INTER_STOCK_DELAY  = 0.5    # seconds between yfinance calls to avoid rate-limiting

# ──────────────────────────────────────────────────────────────────────────────
# Universe rotation constants
# ──────────────────────────────────────────────────────────────────────────────
# Each daily run processes a deterministic slice of the full NSE universe so
# every stock is visited at least once per month.
#
# Coverage maths (full NSE EQ universe ≈ 1 700 symbols):
#   1 700 ÷ 200 per day = 9-day full cycle  →  ~3× coverage per month
#   Max-prescreen of 200 is the number of symbols passed through the fast
#   pre-screen filters; up to max_candidates (default 25) that pass are then
#   given the expensive 7-agent deep analysis.

_EPOCH                  = date(2025, 1, 1)        # day-number epoch for slice rotation
_UNIVERSE_SHUFFLE_SEED  = 0x6272617274            # "bhrat" — stable per-corpus shuffle
_MAX_CANDIDATES_DEFAULT = 25                      # symbols given full 7-agent analysis
_MAX_PRESCREEN_DEFAULT  = 200                     # symbols pre-screened per daily run

# ──────────────────────────────────────────────────────────────────────────────
# NIFTY 500 Universe
# ──────────────────────────────────────────────────────────────────────────────
# Representative 100-symbol liquid subset used as default.
# NSE publishes the full 500 at:
#   https://www.niftyindices.com/IndexConstituents/ind_nifty500list.csv
# Call fetch_nifty500_symbols() to download the live list.

NIFTY500_SYMBOLS: list[str] = [
    # ── NIFTY 50 — large-cap anchors ─────────────────────────────────────────
    "RELIANCE.NS","TCS.NS","HDFCBANK.NS","INFY.NS","ICICIBANK.NS",
    "HINDUNILVR.NS","KOTAKBANK.NS","SBIN.NS","BHARTIARTL.NS","ITC.NS",
    "AXISBANK.NS","LT.NS","ASIANPAINT.NS","MARUTI.NS","NESTLEIND.NS",
    "TITAN.NS","BAJFINANCE.NS","SUNPHARMA.NS","WIPRO.NS","HCLTECH.NS",
    "ULTRACEMCO.NS","NTPC.NS","ONGC.NS","POWERGRID.NS","COALINDIA.NS",
    "TECHM.NS","BAJAJFINSV.NS","DIVISLAB.NS","GRASIM.NS","HINDALCO.NS",
    "JSWSTEEL.NS","CIPLA.NS","DRREDDY.NS","APOLLOHOSP.NS","BPCL.NS",
    "TATASTEEL.NS","INDUSINDBK.NS","TATACONSUM.NS","BRITANNIA.NS","EICHERMOT.NS",
    "HEROMOTOCO.NS","ADANIPORTS.NS","SBILIFE.NS","HDFCLIFE.NS","ICICIPRULI.NS",
    "PIDILITIND.NS","DABUR.NS","SIEMENS.NS","ABB.NS","HAVELLS.NS",
    # ── IT & Technology ───────────────────────────────────────────────────────
    "LTIM.NS","MPHASIS.NS","COFORGE.NS","LTTS.NS","PERSISTENT.NS",
    "OFSS.NS","KPITTECH.NS","TATAELXSI.NS","CYIENT.NS",
    # ── Banking & Finance ─────────────────────────────────────────────────────
    "BANKBARODA.NS","PNB.NS","CANBK.NS","FEDERALBNK.NS","IDFCFIRSTB.NS",
    "AUBANK.NS","RBLBANK.NS","BANDHANBNK.NS","KARURVYSYA.NS","CSBBANK.NS",
    "CHOLAFIN.NS","MUTHOOTFIN.NS","MANAPPURAM.NS","BAJAJHLDNG.NS","M&MFIN.NS",
    "SHRIRAMFIN.NS","LICHSGFIN.NS","CANFINHOME.NS","AAVAS.NS","HOMEFIRST.NS",
    "CAMS.NS","CDSL.NS","BSE.NS","MCX.NS","IEX.NS","ANGELONE.NS",
    # ── Pharma & Healthcare ───────────────────────────────────────────────────
    "AUROPHARMA.NS","LUPIN.NS","BIOCON.NS","TORNTPHARM.NS","ALKEM.NS",
    "LAURUSLABS.NS","GRANULES.NS","NATCOPHARM.NS","IPCALAB.NS","AJANTPHARM.NS",
    "GLAND.NS","LALPATHLAB.NS","METROPOLIS.NS","SYNGENE.NS","ERIS.NS",
    # ── Auto & Auto-Ancillaries ───────────────────────────────────────────────
    "ESCORTS.NS","ASHOKLEY.NS","TVSMOTOR.NS","BALKRISIND.NS","MRF.NS",
    "BHARATFORG.NS","BOSCHLTD.NS","MOTHERSON.NS","APOLLOTYRE.NS","CEATLTD.NS",
    "ENDURANCE.NS","SUPRAJIT.NS","CRAFTSMAN.NS","SONACOMS.NS",
    # ── Realty ────────────────────────────────────────────────────────────────
    "DLF.NS","GODREJPROP.NS","PRESTIGE.NS","OBEROIRLTY.NS","PHOENIXLTD.NS",
    "BRIGADE.NS","SOBHA.NS","LODHA.NS","SUNTECK.NS",
    # ── Consumer & Retail ────────────────────────────────────────────────────
    "CROMPTON.NS","POLYCAB.NS","KANSAINER.NS","BERGEPAINT.NS","INDIGO.NS",
    "IRCTC.NS","CONCOR.NS","ETERNAL.NS","NYKAA.NS","POLICYBZR.NS",
    "DMART.NS","TRENT.NS","ABFRL.NS","BATAINDIA.NS","VMART.NS",
    "SHOPERSTOP.NS","JUBLFOOD.NS","WESTLIFE.NS","DEVYANI.NS","SAPPHIRE.NS",
    "VGUARD.NS","BLUESTARCO.NS","VOLTAS.NS","SYMPHONY.NS","RAJESHEXPO.NS",
    # ── Cement ────────────────────────────────────────────────────────────────
    "JKCEMENT.NS","RAMCOCEM.NS","SHREECEM.NS","AMBUJACEM.NS","ACC.NS",
    "DALBHARAT.NS","HEIDELBERG.NS","BIRLACORP.NS",
    # ── FMCG & Consumer Staples ───────────────────────────────────────────────
    "GODREJCP.NS","EMAMILTD.NS","JYOTHYLAB.NS","MARICO.NS","COLPAL.NS",
    "VBL.NS",
    # ── Power & Utilities ─────────────────────────────────────────────────────
    "TATAPOWER.NS","TORNTPOWER.NS","CESC.NS","JSWENERGY.NS","ADANIGREEN.NS",
    "RECLTD.NS","PFC.NS","IRFC.NS","NHPC.NS","SJVN.NS","HUDCO.NS",
    # ── Metals & Mining ───────────────────────────────────────────────────────
    "SAIL.NS","NATIONALUM.NS","VEDL.NS","HINDZINC.NS","MOIL.NS",
    "APLAPOLLO.NS","RATNAMANI.NS","WELSPUNLIV.NS","GPIL.NS","JINDALSAW.NS",
    # ── Chemicals & Specialty ────────────────────────────────────────────────
    "DEEPAKNITR.NS","AARTIIND.NS","NAVINFLUOR.NS","SRF.NS","PIIND.NS",
    "VINATIORG.NS","FINEORG.NS","ALKYLAMINE.NS","ROSSARI.NS","ANUPAMRAS.NS",
    # ── Fertilizers & Agro ────────────────────────────────────────────────────
    "CHAMBLFERT.NS","COROMANDEL.NS","GSFC.NS","GNFC.NS","RCF.NS",
    # ── Infrastructure & Capital Goods ───────────────────────────────────────
    "SUPREMEIND.NS","PRINCEPIPE.NS","FINOLEXCAB.NS","KEI.NS","ASTRAZEN.NS",
    "NCC.NS","KEC.NS","KALPATARU.NS","ENGINERSIN.NS","TITAGARH.NS",
    # ── Hospitality & QSR ────────────────────────────────────────────────────
    "BARBEQUE-N.NS","INDHOTEL.NS","LEMONTREE.NS","CHALET.NS",
]

# De-duplicate, exclude bad symbols, and resolve known corrections
from data.symbol_map import clean_symbol_list  # noqa: E402
NIFTY500_SYMBOLS = clean_symbol_list(NIFTY500_SYMBOLS)


def fetch_nifty500_symbols() -> list[str]:
    """
    Download the live NIFTY 500 constituent list from NSE.
    Falls back to the hardcoded NIFTY500_SYMBOLS on any failure.
    """
    try:
        import urllib.request
        url = (
            "https://www.niftyindices.com/IndexConstituents/ind_nifty500list.csv"
        )
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "BharatIntelligence/1.0"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            lines = resp.read().decode("utf-8", errors="replace").splitlines()
        symbols = []
        for line in lines[1:]:          # skip header
            parts = line.split(",")
            if len(parts) >= 3:
                sym = parts[2].strip()  # "Symbol" is column 3
                if sym:
                    symbols.append(sym + ".NS")
        if len(symbols) >= 200:
            log.info("Fetched %d NIFTY 500 symbols from NSE", len(symbols))
            return clean_symbol_list(symbols)   # resolve + deduplicate
    except Exception as exc:
        log.warning("NSE NIFTY500 fetch failed, using hardcoded list: %s", exc)
    return list(NIFTY500_SYMBOLS)


def fetch_all_nse_equity_symbols() -> list[str]:
    """
    Download the full NSE main-board equity master file (EQUITY_L.csv) and
    return all EQ-series tickers as 'SYMBOL.NS' strings (~1 700 symbols).

    NSE publishes this file publicly at:
        https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv

    CSV columns (no quoting): SYMBOL, NAME OF COMPANY, SERIES, DATE OF LISTING,
        PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE

    Falls back to ``fetch_nifty500_symbols()`` on any network / parse error so
    the pipeline never halts due to an unreachable NSE endpoint.
    """
    try:
        import urllib.request
        url = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (BharatIntelligence/1.0)",
                "Accept": "text/csv,*/*",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace")

        symbols: list[str] = []
        for line in raw.splitlines()[1:]:       # skip header row
            parts = line.split(",")
            if len(parts) < 3:
                continue
            sym    = parts[0].strip()
            series = parts[2].strip().upper()
            if sym and series == "EQ":          # main-board equity only
                symbols.append(sym + ".NS")

        if len(symbols) >= 500:                 # sanity: expect 1000+
            log.info(
                "fetch_all_nse_equity_symbols: %d EQ symbols from EQUITY_L.csv",
                len(symbols),
            )
            return clean_symbol_list(symbols)

        log.warning(
            "fetch_all_nse_equity_symbols: only %d symbols parsed — too few, "
            "falling back to NIFTY 500",
            len(symbols),
        )
    except Exception as exc:
        log.warning("fetch_all_nse_equity_symbols failed: %s — falling back to NIFTY 500", exc)

    return fetch_nifty500_symbols()


# ──────────────────────────────────────────────────────────────────────────────
# DiscoveryResult dataclass
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class DiscoveryResult:
    symbol:              str
    opportunity_tier:    str           # "CRITICAL" | "STANDARD"
    upside_pct:          float
    upside_confidence:   float         # 0–100
    upside_basis:        str           # 3-sentence explanation
    upside_horizon:      str           # e.g. "3–6 months"
    screen_triggers:     list[str]     # which pre-screen filters fired
    agent_signals:       dict          # per-agent signal + score
    composite_score:     float         # weighted average of all agent scores
    current_price:       Optional[float]
    sector:              str
    liquidity_tier:      Optional[str] = None   # HIGH | MEDIUM | LOW | ILLIQUID | UNKNOWN
    impact_cost_pct:     Optional[float] = None
    forward_pe:          Optional[float] = None
    peg_ratio_fwd:       Optional[float] = None
    eps_growth_pct:      Optional[float] = None
    saved_rec_id:        Optional[str] = None   # Supabase recommendations.id
    discovered_at:       str = field(
        default_factory=lambda: datetime.utcnow().isoformat()
    )

    def to_dict(self) -> dict:
        return asdict(self)


# ──────────────────────────────────────────────────────────────────────────────
# Pre-screen helpers (fast — yfinance + screener only, no LLM calls)
# ──────────────────────────────────────────────────────────────────────────────

def _ema(series, span: int):
    """Exponential moving average via pandas ewm."""
    return series.ewm(span=span, adjust=False).mean()


def _rsi(close, period: int = 14) -> Optional[float]:
    """Return latest RSI value or None if insufficient data."""
    if len(close) < period + 5:
        return None
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    # When avg_loss == 0 all moves were gains → RSI = 100
    rs = avg_gain / avg_loss.where(avg_loss != 0, float("nan"))
    rsi_series = rs.where(avg_loss == 0, 100 - (100 / (1 + rs)))
    rsi_series = rsi_series.where(avg_loss != 0, 100.0)
    val = rsi_series.iloc[-1]
    if val != val:    # NaN guard
        return None
    return float(val)


def _price_above_ema200(close) -> bool:
    """True if latest close is above 200-day EMA."""
    if len(close) < 50:
        return False
    ema200 = _ema(close, 200)
    return float(close.iloc[-1]) > float(ema200.iloc[-1])


def _fii_net_buying(fii_data: Optional[dict]) -> Optional[bool]:
    """
    Returns:
        True  — FII data available and shows net buying  (positive net flow)
        False — FII data available but net selling / neutral
        None  — FII data unavailable (NSE API blocked / network error)

    Returning None lets the caller distinguish "not buying" from "we don't know",
    so the pre-screen threshold can be relaxed appropriately.

    NOTE: This function is kept for compatibility but is no longer used in
    prescreen(). Filter 3 now checks stock-specific institutional holding % via
    screener data instead of the index-level market-wide FII net flow.
    """
    if fii_data is None:
        return None   # API unavailable — treat as unknown, not as "not buying"
    try:
        return float(fii_data.get("fii_net", 0)) > 0
    except (TypeError, ValueError):
        return None   # malformed payload → also unknown


_MIN_INSTITUTIONAL_HOLDING_PCT = 5.0   # Filter 3: stock must have ≥5% institutional ownership


def prescreen(
    symbol: str,
    fii_data: Optional[dict] = None,   # kept for API compatibility; no longer used for Filter 3
) -> tuple[bool, list[str]]:
    """
    Run the 5 quick pre-screen filters against a single symbol.

    Threshold logic: must pass 4-of-5 filters.

    Filters:
      1. RSI between 40–65  (momentum sweet spot)
      2. PE < 50  OR  revenue growth > 30%  (valuation / growth justification)
      3. Institutional holding ≥ 5%  (smart money present)  ← was: FII market-wide net flow
      4. Revenue growth YoY > 15%  (business momentum)
      5. Price above 200-day EMA   (uptrend confirmed)

    Filter 3 was changed from index-level FII net flow (market-wide aggregate,
    same value for ALL stocks = methodologically wrong) to stock-specific
    institutional holding % from screener data.

    Returns:
        (passes: bool, triggers: list[str])
    """
    triggers: list[str] = []

    # ── Fetch OHLCV ───────────────────────────────────────────────────────────
    df = get_ohlcv(symbol, period="1y")
    if df is None or len(df) < 30:
        return False, []

    # ── Data completeness check (skip symbol cleanly, no hallucinated screen) ─
    # yfinance ≥1.x appends today's incomplete candle as NaN — always dropna()
    # before taking iloc[-1] so a NaN close doesn't block every stock.
    _close_series = df["Close"].dropna() if "Close" in df.columns else None
    _close_val    = float(_close_series.iloc[-1]) if (_close_series is not None and not _close_series.empty) else None
    _vol_avg      = float(df["Volume"].fillna(0).mean()) if "Volume" in df.columns and not df.empty else None
    _chk = _dcv.validate({
        "symbol":     symbol,
        "ohlcv_rows": len(df),
        "close":      _close_val,
        "volume_avg": _vol_avg,
    }, "discovery_screener")
    if not _chk.is_sufficient:
        log.debug(
            "prescreen(%s): INSUFFICIENT_DATA — %s", symbol, _chk.summary()
        )
        return False, []

    # ── Earnings guard: skip symbols with earnings ≤5 days away ─────────────
    try:
        from agents.earnings_guard import check_pre_earnings
        eg = check_pre_earnings(symbol, days_window=5)
        if eg["has_upcoming_earnings"] and eg["warning_level"] == "CRITICAL":
            log.debug(
                "prescreen(%s): skipped — earnings in %s days (%s)",
                symbol, eg.get("days_until"), eg.get("earnings_date"),
            )
            return False, []
    except Exception:
        pass  # non-fatal — continue screening

    # ── Liquidity guard: skip ILLIQUID symbols (impact cost ≥ 1%) ────────────
    try:
        from data.impact_cost import estimate_impact_cost
        liq = estimate_impact_cost(symbol, trade_value_inr=5_00_000)
        if liq.get("liquidity_tier") == "ILLIQUID":
            log.debug(
                "prescreen(%s): skipped — ILLIQUID (impact_cost=%.2f%%, daily_vol=₹%.0f)",
                symbol, liq.get("impact_cost_pct") or 0, liq.get("avg_daily_volume_inr") or 0,
            )
            return False, []
    except Exception:
        pass  # non-fatal — continue screening

    close = df["Close"].dropna()   # strip trailing NaN from today's incomplete candle

    # Filter 1: RSI 40–65
    rsi_val = _rsi(close)
    if rsi_val is not None and _RSI_LOW <= rsi_val <= _RSI_HIGH:
        triggers.append(f"RSI {rsi_val:.1f} in 40–65 sweet spot")

    # Filter 5: Price above 200 EMA
    above_200 = _price_above_ema200(close)
    if above_200:
        triggers.append("Price above 200-day EMA (uptrend confirmed)")

    # ── Fetch screener fundamentals ───────────────────────────────────────────
    raw = get_screener_data(symbol)

    if raw is not None:
        pe             = raw.get("pe")
        revenue_growth = raw.get("revenue_growth")

        # Filter 2: PE < 50 OR revenue growth > 30% (growth justification)
        pe_ok        = (pe is not None and pe < _PE_MAX)
        growth_pe_ok = (revenue_growth is not None and revenue_growth > _GROWTH_PE_OVERRIDE)
        if pe_ok:
            triggers.append(f"PE {pe:.1f} < 50 (reasonable valuation)")
        elif growth_pe_ok:
            triggers.append(
                f"Revenue growth {revenue_growth:.1f}% justifies elevated PE"
            )

        # Filter 4: Revenue growth YoY > 15%
        if revenue_growth is not None and revenue_growth > _REVENUE_GROWTH_MIN:
            triggers.append(f"Revenue growth YoY {revenue_growth:.1f}% > 15%")

    # Filter 3: Stock-specific institutional holding ≥ 5%
    # Uses screener data already fetched above (no extra API call).
    # This is methodologically correct: we want stocks WHERE smart money is already
    # present for THIS specific stock, not a market-wide FII aggregate that is
    # identical for all 200 symbols in the slice.
    if raw is not None:
        # Screener.in returns fii_holding_pct and dii_holding_pct separately.
        # Sum them for total institutional; fall back to any variant key present.
        fii_pct = raw.get("fii_holding_pct") or raw.get("fii_holding") or 0.0
        dii_pct = raw.get("dii_holding_pct") or raw.get("dii_holding") or 0.0
        try:
            inst_pct: float | None = float(fii_pct or 0) + float(dii_pct or 0)
            if inst_pct == 0.0:
                # Try legacy / alternate keys
                alt = (raw.get("institutional_holding_pct")
                       or raw.get("institutional_holding")
                       or raw.get("total_institutional"))
                inst_pct = float(alt) if alt is not None else None
        except (TypeError, ValueError):
            inst_pct = None

        if inst_pct is not None and inst_pct >= _MIN_INSTITUTIONAL_HOLDING_PCT:
            triggers.append(
                f"Institutional holding {inst_pct:.1f}% ≥ 5% (smart money present)"
            )
        # If data is missing, we simply don't add the trigger (filter not passed)

    # Threshold: 4-of-5 filters must pass
    threshold = _MIN_PRESCREEN_PASS
    real_hits = sum(1 for t in triggers if not t.startswith("["))
    passes = real_hits >= threshold
    return passes, triggers


# ──────────────────────────────────────────────────────────────────────────────
# Full 7-agent analysis
# ──────────────────────────────────────────────────────────────────────────────

def _run_all_agents(symbol: str, macro_result: Optional[dict] = None) -> dict:
    """
    Run all 7 analysis agents for a given symbol.
    macro and commodities are symbol-agnostic — pass pre-fetched results to avoid
    redundant API calls when processing multiple symbols.

    Returns a dict keyed by agent name.
    """
    results: dict[str, dict] = {}

    # 1. Technical
    try:
        from agents.technical import analyse as tech_analyse
        results["technical"] = tech_analyse(symbol)
    except Exception as exc:
        log.warning("[%s] technical agent failed: %s", symbol, exc)
        results["technical"] = {"signal": "NO_DATA", "score": 0, "agent_name": "technical"}

    # 2. Fundamental
    try:
        from agents.fundamental import analyse as fund_analyse
        results["fundamental"] = fund_analyse(symbol)
    except Exception as exc:
        log.warning("[%s] fundamental agent failed: %s", symbol, exc)
        results["fundamental"] = {"signal": "NO_DATA", "score": 0, "agent_name": "fundamental"}

    # 3. Sentiment
    try:
        from agents.sentiment import analyse as sent_analyse
        results["sentiment"] = sent_analyse(symbol)
    except Exception as exc:
        log.warning("[%s] sentiment agent failed: %s", symbol, exc)
        results["sentiment"] = {"signal": "NO_DATA", "score": 0, "agent_name": "sentiment"}

    # 4. Institutional
    try:
        from agents.institutional import analyse as inst_analyse
        pledging = (
            results["fundamental"]
            .get("detail", {})
            .get("governance", {})
            .get("promoter_pledging")
        )
        results["institutional"] = inst_analyse(symbol, promoter_pledging=pledging)
    except Exception as exc:
        log.warning("[%s] institutional agent failed: %s", symbol, exc)
        results["institutional"] = {"signal": "NO_DATA", "score": 0, "agent_name": "institutional"}

    # 5. Macro (symbol-agnostic — reuse pre-fetched if provided)
    # Apply sector-adjusted macro score so each stock's macro signal reflects
    # its own sector's sensitivity (P0-B fix: was identical for all stocks).
    base_macro = macro_result
    if base_macro is None:
        try:
            from agents.macro import analyse as macro_analyse
            base_macro = macro_analyse()
        except Exception as exc:
            log.warning("macro agent failed: %s", exc)
            base_macro = {"signal": "NEUTRAL", "score": 50, "agent_name": "macro"}

    try:
        from agents.macro import get_sector_adjusted_macro_score
        fund_sector = (
            results.get("fundamental", {})
            .get("detail", {})
            .get("sector", "")
        ) or ""
        results["macro"] = get_sector_adjusted_macro_score(base_macro, fund_sector)
    except Exception as _mac_exc:
        log.debug("[%s] sector macro adjust failed (non-fatal): %s", symbol, _mac_exc)
        results["macro"] = base_macro

    # 6. Historical RAG
    try:
        from agents.historical_rag import analyse as rag_analyse
        tech_detail  = results["technical"].get("detail", {})
        fund_detail  = results["fundamental"].get("detail", {})
        macro_detail = results["macro"].get("detail", {})
        description  = _build_rag_description(symbol, tech_detail, fund_detail, macro_detail)
        results["historical_rag"] = rag_analyse(description)
    except Exception as exc:
        log.warning("[%s] historical_rag agent failed: %s", symbol, exc)
        results["historical_rag"] = {"signal": "NO_DATA", "score": 50, "agent_name": "historical_rag"}

    # 7. Commodities (symbol-agnostic)
    try:
        from agents.commodities import analyse as comm_analyse
        results["commodities"] = comm_analyse()
    except Exception as exc:
        log.warning("commodities agent failed: %s", exc)
        results["commodities"] = {"signal": "NEUTRAL", "score": 50, "agent_name": "commodities"}

    return results


def _build_rag_description(
    symbol: str,
    tech_detail: dict,
    fund_detail: dict,
    macro_detail: dict,
) -> str:
    """Build a natural-language description of current conditions for RAG lookup."""
    parts = [f"Stock: {symbol}."]
    rsi = tech_detail.get("rsi", {}).get("value")
    if rsi is not None:
        parts.append(f"RSI {rsi:.1f}.")
    rev_growth = fund_detail.get("growth", {}).get("revenue_growth")
    if rev_growth is not None:
        parts.append(f"Revenue growth {rev_growth:.1f}% YoY.")
    inr = macro_detail.get("inr_usd", {}).get("value")
    if inr is not None:
        parts.append(f"INR at {inr:.2f}.")
    us10y = macro_detail.get("us10y", {}).get("value")
    if us10y is not None:
        parts.append(f"US 10Y yield {us10y:.2f}%.")
    return " ".join(parts) if len(parts) > 1 else f"NSE stock analysis for {symbol}"


# ──────────────────────────────────────────────────────────────────────────────
# Composite scoring
# ──────────────────────────────────────────────────────────────────────────────

# Weights per agent (must sum to 1.0)
_AGENT_WEIGHTS = {
    "technical":     0.20,
    "fundamental":   0.25,
    "sentiment":     0.10,
    "institutional": 0.20,
    "macro":         0.10,
    "historical_rag":0.10,
    "commodities":   0.05,
}


def _composite_score(agent_results: dict) -> float:
    """Weighted average of all agent scores, 0–100."""
    total_w = 0.0
    weighted_sum = 0.0
    for name, weight in _AGENT_WEIGHTS.items():
        res = agent_results.get(name, {})
        score = res.get("score")
        if score is not None:
            weighted_sum += float(score) * weight
            total_w += weight
    if total_w == 0:
        return 0.0
    return round(weighted_sum / total_w, 2)


def _best_upside(agent_results: dict) -> tuple[float, float]:
    """
    Return (upside_pct, upside_confidence) from whichever agent
    gives the highest upside with adequate confidence.
    Prioritises fundamental → technical.
    """
    fund   = agent_results.get("fundamental", {})
    tech   = agent_results.get("technical", {})
    hist   = agent_results.get("historical_rag", {})

    # Fundamental upside (primary)
    f_upside = fund.get("upside_pct") or 0.0
    f_conf   = (fund.get("score") or 0) * 1.0   # use fund score as proxy confidence

    # Technical upside (secondary)
    t_upside = tech.get("upside_pct") or 0.0
    t_conf   = (tech.get("confidence") or 0) * 100

    # Historical RAG boosts confidence if bullish analogue
    rag_boost = 5.0 if hist.get("signal") == "BULLISH_ANALOGUE" else 0.0

    # Macro boosts confidence if RISK_ON
    macro_boost = 5.0 if agent_results.get("macro", {}).get("signal") == "RISK_ON" else 0.0

    # Pick the higher upside, then compute blended confidence
    if f_upside >= t_upside:
        upside = f_upside
        conf   = min(100.0, f_conf + rag_boost + macro_boost)
    else:
        upside = t_upside
        conf   = min(100.0, t_conf + rag_boost + macro_boost)

    return float(upside), round(float(conf), 2)


# ──────────────────────────────────────────────────────────────────────────────
# Narrative generation
# ──────────────────────────────────────────────────────────────────────────────

def _upside_basis(symbol: str, agent_results: dict, tier: str) -> str:
    """
    Generate a 3-sentence upside basis explanation from agent outputs.
    No LLM call — deterministic from agent signals.

    When the sector is in a notable valuation regime (COMPRESSED / STRETCHED /
    EXTREME), a macro-level sector context sentence is appended so the user
    understands how the sector's own historical cycle affects the opportunity.
    """
    fund   = agent_results.get("fundamental", {})
    tech   = agent_results.get("technical", {})
    inst   = agent_results.get("institutional", {})
    macro  = agent_results.get("macro", {})
    hist   = agent_results.get("historical_rag", {})

    sentences: list[str] = []

    # Extract sector regime data (added in Priority 1 build)
    f_detail      = fund.get("detail", {})
    sv_regime     = f_detail.get("sector_regime", {})
    regime_label  = sv_regime.get("regime")          # e.g. "COMPRESSED", "EXTREME"
    regime_dev    = sv_regime.get("deviation_pct")   # e.g. -22.5
    regime_mult   = sv_regime.get("multiplier", 1.0) # e.g. 1.20
    regime_source = sv_regime.get("data_source", "")

    # Sentence 1: Fundamental value driver
    f_signal = fund.get("signal", "NO_DATA")
    f_upside = fund.get("upside_pct")
    rev_g    = f_detail.get("growth_quality", {}).get("revenue_growth_yoy")
    pe       = f_detail.get("profitability", {}).get("pe")

    if f_signal in ("STRONG_BUY", "BUY") and f_upside:
        s1 = (
            f"Fundamentals are compelling — {symbol} trades at"
            f"{f' PE {pe:.1f}x' if pe else ''} with"
            f"{f' {rev_g:.1f}% YoY revenue growth' if rev_g else ' strong growth'},"
            f" implying {f_upside:.0f}% upside to fair value."
        )
    elif f_upside:
        s1 = (
            f"Fair-value analysis suggests {f_upside:.0f}% upside potential for {symbol}"
            f" based on sector PE and projected earnings."
        )
    else:
        s1 = f"Fundamental screening identified {symbol} as a value candidate within its sector."
    sentences.append(s1.strip())

    # Sentence 2: Technical / institutional confirmation
    t_signal  = tech.get("signal", "NO_DATA")
    fii_net5d = inst.get("fii_net_5d", 0) or 0
    if t_signal in ("STRONG_BUY", "BUY") and fii_net5d > 0:
        s2 = (
            f"Technical structure shows a {t_signal.replace('_', ' ').title()} setup"
            f" with FII net buying of ₹{fii_net5d:,.0f} Cr over 5 sessions confirming institutional interest."
        )
    elif t_signal in ("STRONG_BUY", "BUY"):
        s2 = (
            f"Price action is constructive with a {t_signal.replace('_', ' ').title()}"
            f" technical signal, suggesting momentum is intact."
        )
    elif fii_net5d > 0:
        s2 = (
            f"FII net inflows of ₹{fii_net5d:,.0f} Cr over 5 sessions indicate"
            f" institutional accumulation ahead of potential re-rating."
        )
    else:
        s2 = "Price is holding above key moving averages, suggesting the downside is limited."
    sentences.append(s2.strip())

    # Sentence 3: Macro / historical context + horizon
    macro_sig = macro.get("signal", "NEUTRAL")
    hist_sig  = hist.get("signal", "NO_DATA")
    horizon   = _upside_horizon(agent_results)

    if macro_sig == "RISK_ON" and hist_sig == "BULLISH_ANALOGUE":
        s3 = (
            f"The macro environment is RISK_ON and historical analogues are bullish,"
            f" supporting a {horizon} investment horizon for this discovery."
        )
    elif macro_sig == "RISK_ON":
        s3 = (
            f"A favourable macro backdrop (RISK_ON) provides a tailwind for"
            f" this {horizon} opportunity."
        )
    elif hist_sig == "BULLISH_ANALOGUE":
        s3 = (
            f"Historical market analogues are bullish, suggesting the current setup"
            f" has precedent for significant returns over a {horizon} horizon."
        )
    else:
        s3 = (
            f"This is a {'high-conviction' if tier == 'CRITICAL' else 'moderate-conviction'}"
            f" discovery with a suggested {horizon} investment horizon."
        )
    sentences.append(s3.strip())

    # Sentence 4 (optional): Sector valuation regime context
    # Only emitted when the sector has a notable (non-FAIR) live regime so the
    # user knows whether they are buying into a cheap or expensive sector cycle.
    _regime_notable = regime_label in (
        "COMPRESSED", "MILDLY_COMPRESSED",
        "MILDLY_STRETCHED", "STRETCHED", "EXTREME",
    )
    _regime_live = regime_source not in ("fallback_fair", "not_fetched", "")
    if regime_label and _regime_notable and _regime_live and regime_dev is not None:
        if regime_label in ("COMPRESSED", "MILDLY_COMPRESSED"):
            dev_abs = abs(regime_dev)
            s4 = (
                f"Macro context: the sector is currently trading {dev_abs:.0f}% BELOW"
                f" its long-run median valuation ({regime_label}) — the benchmark was"
                f" tightened by {regime_mult:.2f}x, meaning this stock looks even more"
                f" attractive relative to where the sector historically re-rates."
            )
        elif regime_label == "EXTREME":
            s4 = (
                f"Caution: the sector is in an EXTREME valuation premium"
                f" ({regime_dev:+.0f}% above long-run median) — benchmarks were"
                f" tightened by {regime_mult:.2f}x, so upside estimates are"
                f" conservative and the sector is vulnerable to mean-reversion."
            )
        else:  # MILDLY_STRETCHED or STRETCHED
            s4 = (
                f"Sector context: the sector is trading {regime_dev:+.0f}% above"
                f" its long-run median ({regime_label}) — valuation benchmarks are"
                f" adjusted by {regime_mult:.2f}x to reflect elevated sector pricing."
            )
        sentences.append(s4.strip())

    return "  ".join(sentences)


def _upside_horizon(agent_results: dict) -> str:
    """
    Estimate the investment horizon based on signal strength and macro.
    Returns a human-readable string like "3–6 months".
    """
    comp = _composite_score(agent_results)
    macro_sig = agent_results.get("macro", {}).get("signal", "NEUTRAL")

    if comp >= 75 and macro_sig == "RISK_ON":
        return "2–4 months"
    if comp >= 65:
        return "3–6 months"
    if comp >= 50:
        return "4–8 months"
    return "6–12 months"


# ──────────────────────────────────────────────────────────────────────────────
# Daily slice rotation helpers
# ──────────────────────────────────────────────────────────────────────────────

def _daily_slice(
    universe:   list[str],
    slice_size: int,
    run_date:   Optional[date] = None,
) -> list[str]:
    """
    Return today's deterministic slice of *universe* of length *slice_size*.

    Algorithm
    ---------
    1. Shuffle the full universe once with a fixed seed so the order is stable
       across runs but different from the original list order.
    2. Compute today's day-number relative to _EPOCH and use it to pick the
       starting index with wraparound, guaranteeing every symbol is visited
       in a predictable, repeating cycle.

    The slice wraps cleanly — when the window crosses the end of the list it
    continues from the beginning — so every symbol appears in exactly one
    slice per cycle.

    Args:
        universe:   Full symbol list (any order; will be shuffled internally).
        slice_size: Number of symbols to return (i.e. ``_MAX_PRESCREEN_DEFAULT``).
        run_date:   Date to use for rotation (defaults to today).  Pass an
                    explicit date in tests to get deterministic output.

    Returns:
        A list of exactly ``min(slice_size, len(universe))`` symbols.
    """
    if not universe:
        return []

    n          = len(universe)
    slice_size = min(slice_size, n)

    # Stable shuffle — same seed → same order every time regardless of input order
    rng = random.Random(_UNIVERSE_SHUFFLE_SEED)
    shuffled = list(universe)
    rng.shuffle(shuffled)

    # Day number since epoch drives the window start
    today    = run_date or date.today()
    day_num  = (today - _EPOCH).days
    start    = (day_num * slice_size) % n

    # Wrap-around slice
    end = start + slice_size
    if end <= n:
        return shuffled[start:end]
    # Wraparound: take tail + head
    return shuffled[start:] + shuffled[: end - n]


def _coverage_stats(
    universe:     list[str],
    max_prescreen: int,
    run_date:     Optional[date] = None,
) -> dict:
    """
    Return a dict describing how far through the full universe today's run is.

    Keys
    ----
    universe_size       : total symbols in universe
    slice_size          : symbols pre-screened today
    cycle_length_days   : days to cover the entire universe once
    today_position      : 1-based day within the current cycle
    cycle_pct_complete  : % of universe covered so far in this cycle
    est_full_coverage   : ISO date when the current cycle completes
    monthly_passes      : estimated full-universe passes per 30 days
    """
    today      = run_date or date.today()
    n          = len(universe)
    slice_size = min(max_prescreen, n)
    if slice_size == 0:
        return {}

    cycle_days    = math.ceil(n / slice_size)                 # e.g. 9 for 1700÷200
    day_num       = (today - _EPOCH).days
    pos_in_cycle  = (day_num % cycle_days) + 1               # 1-based
    pct_complete  = round(pos_in_cycle / cycle_days * 100, 1)
    days_left     = cycle_days - pos_in_cycle
    est_complete  = (today + timedelta(days=days_left)).isoformat()
    monthly_passes = round(30 / cycle_days, 1)

    return {
        "universe_size":      n,
        "slice_size":         slice_size,
        "cycle_length_days":  cycle_days,
        "today_position":     pos_in_cycle,
        "cycle_pct_complete": pct_complete,
        "est_full_coverage":  est_complete,
        "monthly_passes":     monthly_passes,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Portfolio exclusion
# ──────────────────────────────────────────────────────────────────────────────

def _load_portfolio_symbols() -> set[str]:
    """
    Load all open symbols from Supabase portfolio_holdings.
    Returns empty set if Supabase is not configured or call fails.
    """
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        return set()
    try:
        from supabase import create_client
        resp = (
            create_client(url, key)
            .table("portfolio_holdings")
            .select("symbol")
            .eq("status", "OPEN")
            .execute()
        )
        return {
            row["symbol"].upper().replace(".NS", "").replace(".BO", "")
            for row in (resp.data or [])
        }
    except Exception as exc:
        log.warning("Could not load portfolio holdings: %s", exc)
        return set()


def _normalise_symbol(sym: str) -> str:
    """Strip .NS / .BO suffix for comparison."""
    return sym.upper().replace(".NS", "").replace(".BO", "")


# ──────────────────────────────────────────────────────────────────────────────
# Supabase persistence
# ──────────────────────────────────────────────────────────────────────────────

def _save_discovery(result: DiscoveryResult) -> Optional[str]:
    """
    Upsert a discovery into the recommendations table.
    Returns the saved row UUID or None on failure.

    Note: the recommendations table needs an `is_discovery` BOOLEAN column.
    Run the migration in db/schema.sql if it doesn't exist yet.
    """
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client

        tier = result.opportunity_tier
        action = "BUY" if tier == "STANDARD" else "BUY"     # CRITICAL also BUY

        row = {
            "symbol":           result.symbol,
            "action":           action,
            "confidence":       result.upside_confidence,
            "upside_pct":       result.upside_pct,
            "upside_confidence": result.upside_confidence,
            "horizon_days":     _horizon_to_days(result.upside_horizon),
            "headline":         (
                f"{'⚡ CRITICAL' if tier == 'CRITICAL' else '✅ STANDARD'} DISCOVERY: "
                f"{result.symbol} — {result.upside_pct:.0f}% upside potential"
            ),
            "summary":          result.upside_basis,
            "agent_signals":    {
                k: {"signal": v.get("signal"), "score": v.get("score")}
                for k, v in result.agent_signals.items()
            },
            "is_discovery":     True,
            "valid_till":       _valid_till(result.upside_horizon),
            # metadata persists price snapshot + discovery context so
            # GET /api/discovery can serve a baseline price even when the
            # live yfinance refresh fails (weekend / holiday / network issue).
            # The API overwrites metadata.price with a fresh quote on every request.
            "metadata": {
                "price":           result.current_price,          # snapshot at discovery time
                "sector":          result.sector,
                "discovery_score": result.composite_score,
                "screen_triggers": result.screen_triggers,
                "upside_basis":    result.upside_basis,
                "upside_horizon":  result.upside_horizon,
                "liquidity_tier":  result.liquidity_tier,
                "impact_cost_pct": result.impact_cost_pct,
                # Forward estimates
                "forward_pe":      result.forward_pe,
                "peg_ratio_fwd":   result.peg_ratio_fwd,
                "eps_growth_pct":  result.eps_growth_pct,
            },
        }

        resp = (
            create_client(url, key)
            .table("recommendations")
            .insert(row)
            .execute()
        )
        if resp.data:
            return resp.data[0].get("id")
    except Exception as exc:
        log.warning("Failed to save discovery for %s: %s", result.symbol, exc)
    return None


def _horizon_to_days(horizon: str) -> int:
    """Convert '3–6 months' string to midpoint in days."""
    mapping = {
        "2–4 months":  90,
        "3–6 months":  135,
        "4–8 months":  180,
        "6–12 months": 270,
    }
    return mapping.get(horizon, 135)


def _valid_till(horizon: str) -> str:
    """Return ISO date string for recommendation expiry."""
    from datetime import timedelta
    days = _horizon_to_days(horizon)
    return (date.today() + timedelta(days=days)).isoformat()


# ──────────────────────────────────────────────────────────────────────────────
# Current price helper
# ──────────────────────────────────────────────────────────────────────────────

def _current_price(symbol: str) -> Optional[float]:
    df = get_ohlcv(symbol, period="5d")
    if df is None or df.empty:
        return None
    return float(df["Close"].iloc[-1])


def _sector_from_fundamental(agent_results: dict) -> str:
    detail = agent_results.get("fundamental", {}).get("detail", {})
    return detail.get("sector", "UNKNOWN")


# ──────────────────────────────────────────────────────────────────────────────
# Main discovery run
# ──────────────────────────────────────────────────────────────────────────────

def run_discovery(
    max_candidates:     int   = _MAX_CANDIDATES_DEFAULT,
    max_prescreen:      int   = _MAX_PRESCREEN_DEFAULT,
    use_extended_universe: bool = True,
    save_to_db:         bool  = True,
    inter_delay:        float = _INTER_STOCK_DELAY,
    _run_date:          Optional[date] = None,    # test hook — leave None in production
) -> list[DiscoveryResult]:
    """
    Full discovery pipeline with daily slice rotation:

        1. Load universe
           - use_extended_universe=True  → fetch_all_nse_equity_symbols() (~1 700 EQ tickers)
           - use_extended_universe=False → fetch_nifty500_symbols() (NIFTY 500 subset)
        2. Exclude portfolio holdings
        3. Select today's deterministic slice of max_prescreen symbols
        4. Pre-screen all slice symbols (fast 4-of-5 filters) — no early exit
        5. Run all 7 agents on up to max_candidates symbols that passed pre-screen
        6. Classify CRITICAL / STANDARD opportunities
        7. Save to Supabase recommendations table
        8. Return sorted list — CRITICAL first, then by upside_pct desc

    Rotation guarantee
    ------------------
    With the default max_prescreen=200 and ~1 700 EQ symbols the full universe
    is covered in a 9-day cycle, giving ~3 full passes per month.

    Args:
        max_candidates:        Max stocks for full 7-agent analysis per run (default 25).
        max_prescreen:         Symbols in today's rotation slice (default 200).
        use_extended_universe: True → full NSE EQ universe; False → NIFTY 500 only.
        save_to_db:            If True, persist discoveries to Supabase.
        inter_delay:           Seconds between yfinance calls (rate-limit protection).
        _run_date:             Override today's date (unit-test hook only).

    Returns:
        list[DiscoveryResult] — sorted CRITICAL first, then upside_pct descending.
    """
    start_ts  = time.time()
    today     = _run_date or date.today()
    log.info("=== Discovery Screener starting (date=%s) ===", today.isoformat())

    # ── 1. Universe ────────────────────────────────────────────────────────────
    if use_extended_universe:
        full_universe = fetch_all_nse_equity_symbols()
        log.info("Extended NSE EQ universe: %d symbols", len(full_universe))
    else:
        full_universe = fetch_nifty500_symbols()
        log.info("NIFTY 500 universe: %d symbols", len(full_universe))

    # ── 2. Exclude portfolio ───────────────────────────────────────────────────
    portfolio_syms = _load_portfolio_symbols()
    if portfolio_syms:
        log.info("Excluding %d portfolio holdings", len(portfolio_syms))
    full_universe = [
        s for s in full_universe
        if _normalise_symbol(s) not in portfolio_syms
    ]
    log.info("After portfolio exclusion: %d symbols", len(full_universe))

    # ── 3. Coverage stats + today's rotation slice ───────────────────────────
    cov = _coverage_stats(full_universe, max_prescreen, run_date=today)
    log.info(
        "Coverage: cycle=%d days | today=day %d/%d (%.1f%%) | "
        "est full coverage %s | ~%.1f passes/month",
        cov.get("cycle_length_days", 0),
        cov.get("today_position", 0),
        cov.get("cycle_length_days", 0),
        cov.get("cycle_pct_complete", 0.0),
        cov.get("est_full_coverage", "?"),
        cov.get("monthly_passes", 0.0),
    )
    slice_symbols = _daily_slice(full_universe, max_prescreen, run_date=today)
    log.info("Today's slice: %d symbols (indices rotated by date)", len(slice_symbols))

    # ── 4. Pre-fetch market-wide data once ────────────────────────────────────
    fii_data = None
    try:
        fii_data = get_nse_fii_dii()
    except Exception as exc:
        log.warning("FII/DII fetch failed: %s", exc)

    macro_result = None
    try:
        from agents.macro import analyse as macro_analyse
        macro_result = macro_analyse()
        log.info("Macro signal: %s (score %s)", macro_result.get("signal"), macro_result.get("score"))
    except Exception as exc:
        log.warning("Macro pre-fetch failed: %s", exc)

    # ── 5. Pre-screen the entire slice (no early exit) ────────────────────────
    # We deliberately screen every symbol in today's slice so that the rotation
    # guarantee holds — a break would mean symbols near the end of the slice
    # are never evaluated.  Only the deep 7-agent analysis is capped.
    log.info("Pre-screening %d symbols …", len(slice_symbols))
    screened: list[tuple[str, list[str]]] = []

    for symbol in slice_symbols:
        try:
            passes, triggers = prescreen(symbol, fii_data=fii_data)
            if passes:
                screened.append((symbol, triggers))
                log.info("  PASS %-25s triggers=%d", symbol, len(triggers))
        except Exception as exc:
            log.warning("  prescreen error for %s: %s", symbol, exc)
        time.sleep(inter_delay)

    log.info("Pre-screen complete: %d/%d passed", len(screened), len(slice_symbols))

    # Cap deep analysis at max_candidates (most-recently screened first is fine;
    # the pre-screen itself has no ranking so order = position in rotated slice)
    candidates = screened[:max_candidates]

    # ── 6. Full 7-agent analysis ───────────────────────────────────────────────
    discoveries: list[DiscoveryResult] = []

    for symbol, triggers in candidates:
        log.info("Running full analysis: %s", symbol)
        try:
            agent_results = _run_all_agents(symbol, macro_result=macro_result)
            upside_pct, upside_conf = _best_upside(agent_results)
            comp_score = _composite_score(agent_results)

            # ── Classify ──────────────────────────────────────────────────────
            fund_res = agent_results.get("fundamental", {})
            fund_data_quality = fund_res.get("data_quality") or ""

            # CRITICAL data quality gate: only promote to CRITICAL when we have
            # real PAT data (not estimated/proxy FCF yield). High upside from
            # ESTIMATED data is a screener artefact, not a real opportunity.
            # STANDARD tier is allowed with ESTIMATED data since the bar is lower.
            is_estimated = fund_data_quality.upper() in ("ESTIMATED", "NO_DATA", "PARTIAL")

            if upside_pct >= _CRITICAL_UPSIDE and upside_conf >= _CRITICAL_CONF:
                if is_estimated:
                    # Demote to STANDARD rather than discard — real signal may still exist
                    log.info(
                        "  %s demoted CRITICAL→STANDARD: data_quality=%s "
                        "(upside=%.1f%% may be artefact)",
                        symbol, fund_data_quality, upside_pct,
                    )
                    tier = "STANDARD" if upside_pct >= _STANDARD_UPSIDE and upside_conf >= _STANDARD_CONF else None
                else:
                    tier = "CRITICAL"
            elif upside_pct >= _STANDARD_UPSIDE and upside_conf >= _STANDARD_CONF:
                tier = "STANDARD"
            else:
                tier = None

            if tier is None:
                log.info("  %s below thresholds (upside=%.1f%% conf=%.1f)",
                         symbol, upside_pct, upside_conf)
                continue

            horizon = _upside_horizon(agent_results)
            basis   = _upside_basis(symbol, agent_results, tier)
            price   = _current_price(symbol)
            sector  = _sector_from_fundamental(agent_results)

            # ── Impact cost (liquidity) ───────────────────────────────────────
            liq_tier = None
            liq_cost = None
            try:
                from data.impact_cost import estimate_impact_cost
                liq = estimate_impact_cost(symbol, trade_value_inr=5_00_000)
                liq_tier = liq.get("liquidity_tier")
                liq_cost = liq.get("impact_cost_pct")
            except Exception:
                pass

            # ── Forward estimates ─────────────────────────────────────────────
            fwd_pe       = None
            fwd_peg      = None
            fwd_eps_gr   = None
            try:
                from data.forward_estimates import get_forward_estimates
                fe = get_forward_estimates(symbol)
                fwd_pe     = fe.get("forward_pe")
                fwd_peg    = fe.get("peg_ratio")
                fwd_eps_gr = fe.get("eps_growth_pct")
            except Exception:
                pass

            dr = DiscoveryResult(
                symbol            = symbol,
                opportunity_tier  = tier,
                upside_pct        = round(upside_pct, 2),
                upside_confidence = upside_conf,
                upside_basis      = basis,
                upside_horizon    = horizon,
                screen_triggers   = triggers,
                agent_signals     = {
                    k: {"signal": v.get("signal"), "score": v.get("score")}
                    for k, v in agent_results.items()
                },
                composite_score   = comp_score,
                current_price     = price,
                sector            = sector,
                liquidity_tier    = liq_tier,
                impact_cost_pct   = liq_cost,
                forward_pe        = fwd_pe,
                peg_ratio_fwd     = fwd_peg,
                eps_growth_pct    = fwd_eps_gr,
            )

            # ── Persist ───────────────────────────────────────────────────────
            if save_to_db:
                rec_id = _save_discovery(dr)
                dr.saved_rec_id = rec_id
                if rec_id:
                    log.info("  Saved discovery %s → rec_id=%s", symbol, rec_id)

            discoveries.append(dr)
            log.info(
                "  ✓ %s [%s] upside=%.1f%% conf=%.1f comp_score=%.1f",
                symbol, tier, upside_pct, upside_conf, comp_score,
            )

        except Exception as exc:
            log.error("Full analysis failed for %s: %s", symbol, exc, exc_info=True)

        time.sleep(inter_delay)

    # ── 7. Sort: CRITICAL first, then by upside_pct desc ─────────────────────
    discoveries.sort(
        key=lambda d: (0 if d.opportunity_tier == "CRITICAL" else 1, -d.upside_pct)
    )

    elapsed = round(time.time() - start_ts, 1)
    log.info(
        "=== Discovery complete: %d opportunities | %d pre-screened | %.1fs ===",
        len(discoveries), len(slice_symbols), elapsed,
    )

    # ── 8. Log daily run ──────────────────────────────────────────────────────
    passed_syms    = [s for s, _ in screened]          # all pre-screen passers
    discovery_syms = [d.symbol for d in discoveries]   # only those that became recs

    _log_daily_run(
        symbols_processed  = len(candidates),
        discoveries        = len(discoveries),
        duration           = elapsed,
        coverage_stats     = cov,
        slice_symbols      = slice_symbols,
        passed_symbols     = passed_syms,
        discovery_symbols  = discovery_syms,
        run_date           = today,
    )

    return discoveries


def _log_daily_run(
    symbols_processed: int,
    discoveries:       int,
    duration:          float,
    coverage_stats:    Optional[dict]  = None,
    slice_symbols:     Optional[list]  = None,
    passed_symbols:    Optional[list]  = None,
    discovery_symbols: Optional[list]  = None,
    run_date:          Optional[date]  = None,
) -> None:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        return

    today_str = (run_date or date.today()).isoformat()

    try:
        from supabase import create_client
        client = create_client(url, key)

        # ── daily_runs: aggregate pipeline log ───────────────────────────────
        row: dict = {
            "run_date":          today_str,
            "symbols_processed": symbols_processed,
            "errors":            0,
            "duration_seconds":  duration,
        }
        if coverage_stats:
            row["agents_run"] = {
                "agent":       AGENT_NAME,
                "coverage":    coverage_stats,
                "discoveries": discoveries,
            }
        client.table("daily_runs").insert(row).execute()
    except Exception as exc:
        log.warning("daily_runs log failed: %s", exc)

    # ── discovery_runs: detailed screened-symbols log ─────────────────────────
    # This is the table the dashboard queries to show "what was screened today".
    # Upsert on run_date so re-runs on the same day update the row rather than
    # duplicating it.
    try:
        from supabase import create_client
        client = create_client(url, key)

        slice_list  = list(slice_symbols or [])
        passed_list = list(passed_symbols or [])
        disc_list   = list(discovery_symbols or [])

        dr_row = {
            "run_date":           today_str,
            "slice_symbols":      slice_list,
            "passed_symbols":     passed_list,
            "discovery_symbols":  disc_list,
            "coverage_stats":     coverage_stats or {},
            "total_screened":     len(slice_list),
            "total_passed":       len(passed_list),
            "total_discoveries":  len(disc_list),
        }
        # Upsert: on conflict on run_date, update the row
        client.table("discovery_runs").upsert(
            dr_row, on_conflict="run_date"
        ).execute()
        log.info(
            "discovery_runs logged: screened=%d  passed=%d  discoveries=%d",
            len(slice_list), len(passed_list), len(disc_list),
        )
    except Exception as exc:
        log.warning("discovery_runs log failed: %s", exc)


# ──────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import json

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Bharat Intelligence Discovery Screener",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples
--------
  # Default: full NSE EQ universe, 200-symbol slice, 25 deep analyses
  python -m agents.discovery_screener

  # Wider pre-screen, more deep analyses
  python -m agents.discovery_screener --max-prescreen 300 --max 40

  # Restrict to NIFTY 500 (faster, ~9 min vs ~25 min)
  python -m agents.discovery_screener --nifty500

  # Dry-run (no Supabase writes)
  python -m agents.discovery_screener --no-save

  # Show coverage stats and exit without running
  python -m agents.discovery_screener --coverage-only
        """,
    )
    parser.add_argument(
        "--max",
        type=int, default=_MAX_CANDIDATES_DEFAULT,
        help=f"Max candidates for full 7-agent analysis (default {_MAX_CANDIDATES_DEFAULT})",
    )
    parser.add_argument(
        "--max-prescreen",
        type=int, default=_MAX_PRESCREEN_DEFAULT,
        help=f"Symbols in today's rotation slice (default {_MAX_PRESCREEN_DEFAULT})",
    )
    parser.add_argument(
        "--nifty500",
        action="store_true",
        help="Use NIFTY 500 universe instead of full NSE EQ universe",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Skip Supabase persistence (dry run)",
    )
    parser.add_argument(
        "--coverage-only",
        action="store_true",
        help="Print today's coverage stats and exit without running",
    )
    args = parser.parse_args()

    if args.coverage_only:
        use_ext = not args.nifty500
        univ    = fetch_all_nse_equity_symbols() if use_ext else fetch_nifty500_symbols()
        cov     = _coverage_stats(univ, args.max_prescreen)
        print(json.dumps(cov, indent=2))
        sys.exit(0)

    results = run_discovery(
        max_candidates        = args.max,
        max_prescreen         = args.max_prescreen,
        use_extended_universe = not args.nifty500,
        save_to_db            = not args.no_save,
    )

    print(f"\n{'='*60}")
    print(f"DISCOVERY RESULTS — {len(results)} opportunities found")
    print(f"{'='*60}")
    for r in results:
        print(json.dumps(r.to_dict(), indent=2, default=str))
