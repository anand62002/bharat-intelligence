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
    CRITICAL  — upside_pct >= 100 AND upside_confidence >= 70
    STANDARD  — upside_pct >=  20 AND confidence >= 65
"""

import logging
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from typing import Optional

import numpy as np

from dotenv import load_dotenv

load_dotenv()

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from data.fetchers import get_ohlcv, get_nse_fii_dii, get_screener_data  # noqa: E402

log = logging.getLogger(__name__)
AGENT_NAME = "discovery_screener"

# ──────────────────────────────────────────────────────────────────────────────
# Thresholds
# ──────────────────────────────────────────────────────────────────────────────
_RSI_LOW            = 40.0
_RSI_HIGH           = 65.0
_PE_MAX             = 50.0
_GROWTH_PE_OVERRIDE = 30.0   # revenue growth % that justifies PE > 50
_REVENUE_GROWTH_MIN = 15.0   # YoY %
_MIN_PRESCREEN_PASS        = 4   # must pass this many of 5 filters (FII data available)
_MIN_PRESCREEN_PASS_NO_FII = 3   # relaxed threshold when FII API is blocked (3-of-4 known filters)

_CRITICAL_UPSIDE    = 100.0
_CRITICAL_CONF      = 70.0
_STANDARD_UPSIDE    =  20.0
_STANDARD_CONF      =  65.0

_INTER_STOCK_DELAY  = 0.5    # seconds between yfinance calls to avoid rate-limiting

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
    """
    if fii_data is None:
        return None   # API unavailable — treat as unknown, not as "not buying"
    try:
        return float(fii_data.get("fii_net", 0)) > 0
    except (TypeError, ValueError):
        return None   # malformed payload → also unknown


def prescreen(
    symbol: str,
    fii_data: Optional[dict] = None,
) -> tuple[bool, list[str]]:
    """
    Run the 5 quick pre-screen filters against a single symbol.

    Threshold logic:
      - FII API available  → must pass 4-of-5 filters  (_MIN_PRESCREEN_PASS)
      - FII API blocked    → FII filter is skipped; must pass 3-of-4 remaining
                             filters  (_MIN_PRESCREEN_PASS_NO_FII)

    Returns:
        (passes: bool, triggers: list[str])
    """
    triggers: list[str] = []

    # ── Fetch OHLCV ───────────────────────────────────────────────────────────
    df = get_ohlcv(symbol, period="1y")
    if df is None or len(df) < 30:
        return False, []

    close = df["Close"]

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

    # Filter 3: FII net buying (market-wide indicator)
    # _fii_net_buying returns None when the NSE API is blocked, True/False when available.
    fii_result = _fii_net_buying(fii_data)
    fii_available = fii_result is not None

    if fii_available:
        # API responded — count the filter normally
        if fii_result:
            triggers.append("FII net buyer last session (market-wide flow positive)")
        threshold = _MIN_PRESCREEN_PASS            # 4-of-5
    else:
        # NSE API blocked — skip FII filter, relax threshold to 3-of-4
        triggers_meta = ["[FII data unavailable — threshold relaxed to 3-of-4 known filters]"]
        log.debug("prescreen(%s): FII API unavailable, using relaxed 3-of-4 threshold", symbol)
        # We prepend the meta note but don't count it as a passing filter
        triggers = triggers_meta + triggers
        threshold = _MIN_PRESCREEN_PASS_NO_FII     # 3-of-4

    # Count only real filter hits (not the meta note)
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
    if macro_result is not None:
        results["macro"] = macro_result
    else:
        try:
            from agents.macro import analyse as macro_analyse
            results["macro"] = macro_analyse()
        except Exception as exc:
            log.warning("macro agent failed: %s", exc)
            results["macro"] = {"signal": "NEUTRAL", "score": 50, "agent_name": "macro"}

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
    max_candidates:     int  = 15,
    use_live_universe:  bool = False,
    save_to_db:         bool = True,
    inter_delay:        float = _INTER_STOCK_DELAY,
) -> list[DiscoveryResult]:
    """
    Full discovery pipeline:
        1. Load universe (NIFTY 500 hardcoded or live from NSE)
        2. Exclude portfolio holdings
        3. Pre-screen each symbol (fast filters, 4-of-5)
        4. Run all 7 agents on pre-screened candidates (up to max_candidates)
        5. Classify CRITICAL / STANDARD opportunities
        6. Save to Supabase recommendations table
        7. Return sorted list — critical first, then by upside_pct desc

    Args:
        max_candidates:    Max stocks to run full analysis on (default 15)
        use_live_universe: If True, fetch live NIFTY 500 from NSE; else use hardcoded list
        save_to_db:        If True, persist discoveries to Supabase
        inter_delay:       Seconds to sleep between stocks (rate-limit friendly)

    Returns:
        list[DiscoveryResult]
    """
    start_ts = time.time()
    log.info("=== Discovery Screener starting ===")

    # ── 1. Universe ────────────────────────────────────────────────────────────
    universe = fetch_nifty500_symbols() if use_live_universe else list(NIFTY500_SYMBOLS)
    log.info("Universe: %d symbols", len(universe))

    # ── 2. Exclude portfolio ───────────────────────────────────────────────────
    portfolio_syms = _load_portfolio_symbols()
    if portfolio_syms:
        log.info("Excluding %d portfolio holdings: %s", len(portfolio_syms), portfolio_syms)
    universe = [
        s for s in universe
        if _normalise_symbol(s) not in portfolio_syms
    ]
    log.info("After portfolio exclusion: %d symbols", len(universe))

    # ── 3. Pre-fetch market-wide data once ────────────────────────────────────
    fii_data = None
    try:
        fii_data = get_nse_fii_dii()
    except Exception as exc:
        log.warning("FII/DII fetch failed: %s", exc)

    # Pre-fetch macro once (shared across all symbols)
    macro_result = None
    try:
        from agents.macro import analyse as macro_analyse
        macro_result = macro_analyse()
        log.info("Macro signal: %s (score %s)", macro_result.get("signal"), macro_result.get("score"))
    except Exception as exc:
        log.warning("Macro pre-fetch failed: %s", exc)

    # ── 4. Pre-screen ─────────────────────────────────────────────────────────
    log.info("Pre-screening %d symbols …", len(universe))
    screened: list[tuple[str, list[str]]] = []

    for symbol in universe:
        if len(screened) >= max_candidates * 3:  # collect 3x for safety buffer
            break
        try:
            passes, triggers = prescreen(symbol, fii_data=fii_data)
            if passes:
                screened.append((symbol, triggers))
                log.info("  PASS %-25s triggers=%d", symbol, len(triggers))
        except Exception as exc:
            log.warning("  prescreen error for %s: %s", symbol, exc)
        time.sleep(inter_delay)

    log.info("Pre-screen complete: %d candidates", len(screened))

    # Trim to max_candidates
    candidates = screened[:max_candidates]

    # ── 5. Full 7-agent analysis ───────────────────────────────────────────────
    discoveries: list[DiscoveryResult] = []

    for symbol, triggers in candidates:
        log.info("Running full analysis: %s", symbol)
        try:
            agent_results = _run_all_agents(symbol, macro_result=macro_result)
            upside_pct, upside_conf = _best_upside(agent_results)
            comp_score = _composite_score(agent_results)

            # ── Classify ──────────────────────────────────────────────────────
            if upside_pct >= _CRITICAL_UPSIDE and upside_conf >= _CRITICAL_CONF:
                tier = "CRITICAL"
            elif upside_pct >= _STANDARD_UPSIDE and upside_conf >= _STANDARD_CONF:
                tier = "STANDARD"
            else:
                log.info("  %s does not meet opportunity thresholds (upside=%.1f%% conf=%.1f)",
                         symbol, upside_pct, upside_conf)
                continue

            horizon = _upside_horizon(agent_results)
            basis   = _upside_basis(symbol, agent_results, tier)
            price   = _current_price(symbol)
            sector  = _sector_from_fundamental(agent_results)

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

    # ── 6. Sort: CRITICAL first, then by upside_pct desc ─────────────────────
    discoveries.sort(
        key=lambda d: (0 if d.opportunity_tier == "CRITICAL" else 1, -d.upside_pct)
    )

    elapsed = round(time.time() - start_ts, 1)
    log.info(
        "=== Discovery complete: %d opportunities found in %.1fs ===",
        len(discoveries), elapsed,
    )

    # ── 7. Log daily run ──────────────────────────────────────────────────────
    _log_daily_run(
        symbols_processed=len(candidates),
        discoveries=len(discoveries),
        duration=elapsed,
    )

    return discoveries


def _log_daily_run(
    symbols_processed: int,
    discoveries: int,
    duration: float,
) -> None:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        return
    try:
        from supabase import create_client
        create_client(url, key).table("daily_runs").insert({
            "run_date":          date.today().isoformat(),
            "symbols_processed": symbols_processed,
            "errors":            0,
            "duration_seconds":  duration,
        }).execute()
    except Exception as exc:
        log.warning("daily_runs log failed: %s", exc)


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

    parser = argparse.ArgumentParser(description="Bharat Intelligence Discovery Screener")
    parser.add_argument("--max",     type=int, default=15,    help="Max candidates for full analysis")
    parser.add_argument("--live",    action="store_true",     help="Fetch live NIFTY 500 from NSE")
    parser.add_argument("--no-save", action="store_true",     help="Skip Supabase persistence")
    args = parser.parse_args()

    results = run_discovery(
        max_candidates    = args.max,
        use_live_universe = args.live,
        save_to_db        = not args.no_save,
    )

    print(f"\n{'='*60}")
    print(f"DISCOVERY RESULTS — {len(results)} opportunities found")
    print(f"{'='*60}")
    for r in results:
        print(json.dumps(r.to_dict(), indent=2, default=str))
