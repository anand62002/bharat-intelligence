"""
agents/sector_valuation.py — Live Sector Valuation Regime
==========================================================
Fetches live Nifty sector-index P/E ratios and classifies each sector into
one of four valuation regimes relative to its 5-year historical median PE:

  COMPRESSED       — sector trading >20% BELOW its long-run median PE
                     → opportunity to accumulate; widen individual-stock tolerance
  FAIR             — within ±15% of the long-run median (normal cycle range)
                     → no adjustment to valuation benchmarks
  STRETCHED        — sector trading 15–40% ABOVE its long-run median PE
                     → require more margin of safety; tighten benchmarks
  EXTREME          — sector trading >40% ABOVE its long-run median PE
                     → bubble warning; strong tightening of individual-stock benchmarks

The `regime_multiplier` is applied to ALL valuation benchmarks inside
`fundamental.analyse()`:
  effective_sector_pe       = sector_pe       × regime_multiplier
  effective_sector_ev_ebitda= sector_ev_ebitda× regime_multiplier
  effective_sector_pb       = sector_pb       × regime_multiplier

Multiplier table
----------------
  COMPRESSED  : 1.20  — wider tolerance; sector de-rating is a cycle entry point
  FAIR        : 1.00  — no adjustment
  STRETCHED   : 0.90  — moderate tightening; sector premium demands more margin of safety
  EXTREME     : 0.80  — meaningful tightening; frothy multiple, protect vs contraction

Data sources (in priority order)
---------------------------------
  1. NSE India allIndices API — single call returns ALL Nifty sectoral index P/Es
  2. yfinance constituent median — 3 representative stocks per sector; uses retry
  3. Static FAIR fallback — multiplier = 1.0, no distortion; logs at DEBUG level

Caching
-------
All sector regimes are cached in-process for _CACHE_TTL seconds (default 3600).
This ensures a discovery run over 15 stocks with the same sector pays only one
fetch, not 15.

Entry points
------------
  get_live_sector_pe_map()         -> dict[str, float]   sector_key → live PE
  get_sector_regime(sector_key)    -> dict               regime info dict
"""

import logging
import os
import sys
import time
from typing import Optional

log = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ──────────────────────────────────────────────────────────────────────────────
# Long-run sector PE benchmarks — STRUCTURAL 5-YEAR MEDIAN
# 5-year rolling median (Dec 2019 – Dec 2024), calibrated to Nifty sectoral
# indices.  These represent the "structural fair multiple" for each sector —
# not the all-time average (which includes GFC / COVID crash distortions).
#
# Used for:
#   1. Regime classification: COMPRESSED / FAIR / STRETCHED / EXTREME
#      (live_pe / longrun_pe → _REGIME_THRESHOLDS multiplier applied to scoring)
#   2. Discovery pre-screen: _get_sector_pe() in discovery_screener.py uses
#      this as tier-2 fallback (tier-1 = live rolling median from sector_pe_snapshots)
#
# Distinct from SECTOR_PE_MAP in fundamental.py (current-year scoring benchmarks).
# See that file's header comment for the full two-map architecture explanation.
# ──────────────────────────────────────────────────────────────────────────────

SECTOR_LONGRUN_PE: dict[str, float] = {
    # ── IT / Technology ──────────────────────────────────────────────────────
    # Structural re-rating post-2020: cloud, SaaS, AI demand; 22x pre-2020 → 28x
    "it":                     28.0,
    "information technology": 28.0,
    "technology":             28.0,
    # ── Banking / Finance ────────────────────────────────────────────────────
    # PSU drag suppresses aggregate; private banks at 20–25x, PSUs 5–10x
    "banking":                14.0,
    "bank":                   14.0,
    "nbfc":                   22.0,
    "financial services":     20.0,
    "finance":                18.0,
    # ── Healthcare / Pharma ──────────────────────────────────────────────────
    # India generics + specialty mix premium; hospitals at premium to pharma
    "pharmaceuticals":        26.0,
    "pharma":                 26.0,
    "healthcare":             30.0,
    # ── Consumer ─────────────────────────────────────────────────────────────
    # FMCG sustained premium from FII quality-allocation flows
    "fast moving consumer goods": 44.0,
    "fmcg":                   44.0,
    "consumer defensive":     44.0,
    "consumer discretionary": 34.0,
    "consumer cyclical":      32.0,
    # ── Telecom ──────────────────────────────────────────────────────────────
    # 5G capex cycle: negative EPS distorts PE; sector has traded 25–45x
    "telecom":                32.0,
    "telecommunications":     32.0,
    "communication services": 32.0,
    # ── Auto ─────────────────────────────────────────────────────────────────
    # EV transition multiple expansion lifting sector from 18x to 22x
    "automobile":             22.0,
    "auto":                   22.0,
    # ── Infrastructure / Industrials ─────────────────────────────────────────
    "infrastructure":         20.0,
    "industrials":            22.0,
    "construction":           17.0,
    "capital goods":          26.0,
    # ── Metals / Materials ───────────────────────────────────────────────────
    # Highly cyclical; use mid-cycle normalized PE
    "metals & mining":        10.0,
    "metals":                 10.0,
    "basic materials":        12.0,
    "materials":              12.0,
    "chemicals":              26.0,
    "cement":                 22.0,
    # ── Energy ───────────────────────────────────────────────────────────────
    # ONGC + Reliance blended; Reliance conglomerate distorts aggregate
    "energy":                 12.0,
    "oil & gas":              11.0,
    # ── Utilities ────────────────────────────────────────────────────────────
    "utilities":              16.0,
    # ── Real Estate ──────────────────────────────────────────────────────────
    # India housing super-cycle re-rated from 15x to 22x; using 5-yr blended
    "realty":                 22.0,
    "real estate":            22.0,
    # ── Media / Diversified ──────────────────────────────────────────────────
    "media":                  22.0,
    "diversified":            20.0,
    "insurance":              32.0,
    "retail":                 32.0,
}

# ──────────────────────────────────────────────────────────────────────────────
# Regime thresholds and multipliers
# ratio = current_sector_pe / long_run_pe
# ──────────────────────────────────────────────────────────────────────────────

_REGIME_THRESHOLDS = [
    # (ratio_max_exclusive, regime_name, multiplier)
    # Ordered from lowest ratio upward
    (0.80, "COMPRESSED",  1.20),   # > 20% below long-run  → widen tolerance
    (0.92, "MILDLY_COMPRESSED", 1.10),  # 8–20% below
    (1.08, "FAIR",        1.00),   # within ±8% of long-run
    (1.25, "MILDLY_STRETCHED", 0.94),   # 8–25% above
    (1.45, "STRETCHED",   0.88),   # 25–45% above → moderate tightening
    (None, "EXTREME",     0.80),   # > 45% above → meaningful tightening
]

# ──────────────────────────────────────────────────────────────────────────────
# NSE allIndices API → sector key mapping
# The NSE API returns the index by exact name; we map that to our sector keys.
# ──────────────────────────────────────────────────────────────────────────────

_NSE_INDEX_TO_SECTOR: dict[str, list[str]] = {
    "NIFTY IT":                     ["it", "information technology", "technology"],
    "NIFTY BANK":                   ["banking", "bank"],
    "NIFTY FINANCIAL SERVICES":     ["financial services", "nbfc", "finance"],
    "NIFTY PHARMA":                 ["pharmaceuticals", "pharma"],
    "NIFTY HEALTHCARE INDEX":       ["healthcare"],
    "NIFTY FMCG":                   ["fmcg", "fast moving consumer goods",
                                      "consumer defensive"],
    "NIFTY AUTO":                   ["automobile", "auto"],
    "NIFTY REALTY":                 ["realty", "real estate"],
    "NIFTY ENERGY":                 ["energy"],
    "NIFTY OIL AND GAS":            ["oil & gas"],
    "NIFTY METAL":                  ["metals & mining", "metals", "basic materials",
                                      "materials"],
    "NIFTY INFRASTRUCTURE":         ["infrastructure", "construction"],
    "NIFTY CONSUMER DURABLES":      ["consumer cyclical", "consumer discretionary"],
    "NIFTY MEDIA":                  ["media"],
    "NIFTY COMMODITIES":            ["chemicals", "cement", "diversified"],
    "NIFTY INDIA CONSUMPTION":      ["retail"],
    "NIFTY PSU BANK":               [],  # sub-index; skip to avoid overwriting NIFTY BANK
    "NIFTY PRIVATE BANK":           [],  # sub-index; skip
}

# ──────────────────────────────────────────────────────────────────────────────
# yfinance representative constituents (fallback)
# 3 liquid, well-covered stocks per sector.  Using fewer gives faster fetches
# and avoids rate-limits while still giving a reasonable median.
# ──────────────────────────────────────────────────────────────────────────────

_SECTOR_YF_REPS: dict[str, list[str]] = {
    "it":                     ["TCS.NS", "INFY.NS", "HCLTECH.NS"],
    "information technology": ["TCS.NS", "INFY.NS", "HCLTECH.NS"],
    "technology":             ["TCS.NS", "INFY.NS", "HCLTECH.NS"],
    "banking":                ["HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS"],
    "bank":                   ["HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS"],
    "financial services":     ["BAJFINANCE.NS", "HDFC.NS", "MUTHOOTFIN.NS"],
    "nbfc":                   ["BAJFINANCE.NS", "CHOLAFIN.NS", "MUTHOOTFIN.NS"],
    "pharmaceuticals":        ["SUNPHARMA.NS", "CIPLA.NS", "DRREDDY.NS"],
    "pharma":                 ["SUNPHARMA.NS", "CIPLA.NS", "DRREDDY.NS"],
    "healthcare":             ["APOLLOHOSP.NS", "LALPATHLAB.NS", "METROPOLIS.NS"],
    "fmcg":                   ["HINDUNILVR.NS", "ITC.NS", "NESTLEIND.NS"],
    "fast moving consumer goods": ["HINDUNILVR.NS", "ITC.NS", "NESTLEIND.NS"],
    "automobile":             ["MARUTI.NS", "HEROMOTOCO.NS", "TVSMOTOR.NS"],
    "auto":                   ["MARUTI.NS", "HEROMOTOCO.NS", "TVSMOTOR.NS"],
    "realty":                 ["DLF.NS", "GODREJPROP.NS", "PRESTIGE.NS"],
    "real estate":            ["DLF.NS", "GODREJPROP.NS", "PRESTIGE.NS"],
    "energy":                 ["RELIANCE.NS", "ONGC.NS", "BPCL.NS"],
    "oil & gas":              ["ONGC.NS", "BPCL.NS", "IOC.NS"],
    "metals & mining":        ["TATASTEEL.NS", "JSWSTEEL.NS", "HINDALCO.NS"],
    "metals":                 ["TATASTEEL.NS", "JSWSTEEL.NS", "HINDALCO.NS"],
    "chemicals":              ["DEEPAKNITR.NS", "SRF.NS", "NAVINFLUOR.NS"],
    "cement":                 ["ULTRACEMCO.NS", "AMBUJACEM.NS", "SHREECEM.NS"],
    "infrastructure":         ["LT.NS", "ADANIPORTS.NS", "NCC.NS"],
    "utilities":              ["NTPC.NS", "POWERGRID.NS", "TATAPOWER.NS"],
    "telecom":                ["BHARTIARTL.NS", "INDUSTOWER.NS", "VODAFONEIDEA.NS"],
    "consumer cyclical":      ["TITAN.NS", "DMART.NS", "TRENT.NS"],
}

# ──────────────────────────────────────────────────────────────────────────────
# In-process cache: sector_key → (expiry_timestamp, regime_dict)
# ──────────────────────────────────────────────────────────────────────────────

_CACHE_TTL:    float = 3600.0   # seconds — one full market session
_regime_cache: dict[str, tuple[float, dict]] = {}

# Global NSE bulk cache: index_name → live_pe (populated once per TTL)
_nse_pe_cache:  dict[str, float] = {}
_nse_cache_exp: float = 0.0

_NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.nseindia.com/",
}


# ──────────────────────────────────────────────────────────────────────────────
# Data fetchers
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_nse_all_index_pes() -> dict[str, float]:
    """
    Fetch live P/E for all Nifty sector indices in a single NSE API call.

    NSE requires a prior session GET (cookie seeding) before the data API
    will respond.  On any failure the function returns {} silently — callers
    fall back to the yfinance constituent path.

    Returns:
        dict mapping NSE index name → live PE (e.g. "NIFTY IT" → 32.5)
        Empty dict on any failure.
    """
    global _nse_pe_cache, _nse_cache_exp
    if time.time() < _nse_cache_exp and _nse_pe_cache:
        return _nse_pe_cache

    try:
        import requests
        session = requests.Session()
        # Seed cookies — NSE rejects the data endpoint without a prior home-page visit
        session.get("https://www.nseindia.com", headers=_NSE_HEADERS, timeout=10)
        resp = session.get(
            "https://www.nseindia.com/api/allIndices",
            headers=_NSE_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        result: dict[str, float] = {}
        for item in data.get("data", []):
            name = (item.get("index") or item.get("indexSymbol") or "").strip().upper()
            pe_raw = item.get("pe")
            if name and pe_raw is not None:
                try:
                    pe_val = float(pe_raw)
                    if pe_val > 0:          # zero / negative = no meaningful PE
                        result[name] = round(pe_val, 2)
                except (TypeError, ValueError):
                    pass
        if result:
            _nse_pe_cache  = result
            _nse_cache_exp = time.time() + _CACHE_TTL
            log.debug("NSE allIndices: fetched PE for %d indices", len(result))
        return result
    except Exception as exc:
        log.debug("NSE allIndices fetch failed: %s", exc)
        return {}


def _fetch_yf_constituent_pe(sector_key: str) -> Optional[float]:
    """
    Compute median P/E from 3 representative yfinance stocks for `sector_key`.

    Uses yf_fetch_with_retry so transient 429/timeout errors are handled.
    Returns None if < 2 valid P/E values are obtained.
    """
    reps = _SECTOR_YF_REPS.get(sector_key)
    if not reps:
        return None
    pes: list[float] = []
    try:
        import yfinance as yf
        from data.fetchers import yf_fetch_with_retry
        for sym in reps:
            try:
                info = yf_fetch_with_retry(lambda s=sym: yf.Ticker(s).info,
                                           max_retries=2, base_delay=0.5)
                pe_val = info.get("trailingPE") or info.get("forwardPE")
                if pe_val and float(pe_val) > 0:
                    pes.append(float(pe_val))
            except Exception:
                pass
    except Exception as exc:
        log.debug("yfinance constituent PE fetch error: %s", exc)
    if len(pes) < 2:
        return None
    pes.sort()
    mid = len(pes) // 2
    median = pes[mid] if len(pes) % 2 else (pes[mid - 1] + pes[mid]) / 2
    log.debug("Constituent PE for %s: %s → median %.1f", sector_key, pes, median)
    return round(median, 2)


def _get_live_pe_for_sector(sector_key: str) -> Optional[float]:
    """
    Return a live P/E estimate for the given sector key.
    Priority: NSE allIndices API → yfinance constituent median → None.
    """
    # Step 1: try NSE bulk fetch (one shared call for all sectors)
    nse_pes = _fetch_nse_all_index_pes()
    if nse_pes:
        # Walk the index→sector mapping to find a match
        for nse_name, sector_keys in _NSE_INDEX_TO_SECTOR.items():
            if sector_key in sector_keys and nse_name in nse_pes:
                return nse_pes[nse_name]

    # Step 2: yfinance constituent median
    return _fetch_yf_constituent_pe(sector_key)


# ──────────────────────────────────────────────────────────────────────────────
# Regime classification
# ──────────────────────────────────────────────────────────────────────────────

def classify_regime(
    current_pe: float,
    long_run_pe: float,
) -> tuple[str, float, float]:
    """
    Classify a sector's valuation regime and return the scoring multiplier.

    Args:
        current_pe:  Live sector P/E (from NSE or constituent median)
        long_run_pe: 5-year median benchmark from SECTOR_LONGRUN_PE

    Returns:
        (regime, multiplier, deviation_pct) where:
          regime       — "COMPRESSED" | "MILDLY_COMPRESSED" | "FAIR" |
                         "MILDLY_STRETCHED" | "STRETCHED" | "EXTREME"
          multiplier   — factor to apply to sector_pe in fundamental scoring
          deviation_pct— (current_pe / long_run_pe - 1) * 100
                         negative = sector cheaper than long-run
                         positive = sector more expensive than long-run
    """
    if long_run_pe <= 0:
        return "FAIR", 1.0, 0.0

    ratio         = current_pe / long_run_pe
    deviation_pct = round((ratio - 1) * 100, 1)

    for max_ratio, regime, multiplier in _REGIME_THRESHOLDS:
        if max_ratio is None or ratio < max_ratio:
            return regime, multiplier, deviation_pct

    # Should never reach here — last threshold is (None, ...) which always matches
    return "EXTREME", 0.80, deviation_pct


# ──────────────────────────────────────────────────────────────────────────────
# Public entry points
# ──────────────────────────────────────────────────────────────────────────────

_FAIR_REGIME: dict = {
    "regime":            "FAIR",
    "multiplier":        1.0,
    "live_pe":           None,
    "long_run_pe":       None,
    "deviation_pct":     None,
    "note":              "No live sector PE available — using FAIR regime (no adjustment)",
    "data_source":       "fallback_fair",
}


def get_sector_regime(sector_key: str) -> dict:
    """
    Return the current valuation regime for a sector.

    The result is cached for _CACHE_TTL seconds so batch discovery runs
    share a single fetch across all stocks in the same sector.

    Args:
        sector_key: Lower-case sector name matching SECTOR_PE_MAP keys,
                    e.g. "it", "banking", "pharmaceuticals".

    Returns:
        dict with keys:
          regime       — "COMPRESSED" | "MILDLY_COMPRESSED" | "FAIR" |
                         "MILDLY_STRETCHED" | "STRETCHED" | "EXTREME"
          multiplier   — float; apply to sector_pe / sector_ev_ebitda / sector_pb
          live_pe      — float or None; current sector index P/E
          long_run_pe  — float or None; 5-year calibrated median
          deviation_pct— float or None; % deviation from long-run
          note         — human-readable summary
          data_source  — "nse_api" | "yfinance_constituents" | "fallback_fair"

    Always returns a valid dict (never raises).
    """
    if not sector_key:
        return dict(_FAIR_REGIME)

    # ── Cache hit ─────────────────────────────────────────────────────────────
    cached = _regime_cache.get(sector_key)
    if cached and time.time() < cached[0]:
        return dict(cached[1])    # return a copy so callers can't mutate the cache

    long_run_pe = SECTOR_LONGRUN_PE.get(sector_key)
    if long_run_pe is None:
        # Unknown sector — return FAIR with a note
        regime_dict = dict(_FAIR_REGIME)
        regime_dict["note"] = (
            f"Sector '{sector_key}' not in SECTOR_LONGRUN_PE — using FAIR (no adjustment)"
        )
        return regime_dict

    # ── Fetch live PE ─────────────────────────────────────────────────────────
    live_pe    = None
    data_src   = "fallback_fair"
    try:
        live_pe = _get_live_pe_for_sector(sector_key)
        if live_pe is not None:
            # Determine which source provided the data
            nse_pes = _nse_pe_cache        # already populated by _get_live_pe_for_sector
            nse_hit = any(
                sector_key in sector_keys and nse_name in nse_pes
                for nse_name, sector_keys in _NSE_INDEX_TO_SECTOR.items()
            )
            data_src = "nse_api" if nse_hit else "yfinance_constituents"
    except Exception as exc:
        log.debug("get_sector_regime: live PE fetch error for %s: %s", sector_key, exc)

    # ── Classify ──────────────────────────────────────────────────────────────
    if live_pe is None:
        regime_dict = dict(_FAIR_REGIME)
        regime_dict["long_run_pe"] = long_run_pe
        regime_dict["note"] = (
            f"Live PE unavailable for '{sector_key}' — using FAIR (no adjustment). "
            f"Long-run benchmark: {long_run_pe:.1f}x"
        )
        _regime_cache[sector_key] = (time.time() + _CACHE_TTL, regime_dict)
        return dict(regime_dict)

    regime, multiplier, deviation_pct = classify_regime(live_pe, long_run_pe)

    # ── Build human-readable note ─────────────────────────────────────────────
    direction = "above" if deviation_pct >= 0 else "below"
    abs_dev   = abs(deviation_pct)
    action    = {
        "COMPRESSED":       "wider PE tolerance (+20%) — sector de-rating = cycle opportunity",
        "MILDLY_COMPRESSED":"slightly wider tolerance (+10%) — sector modestly cheap",
        "FAIR":             "no adjustment — sector within normal range",
        "MILDLY_STRETCHED": "slight tightening (-6%) — sector modestly stretched",
        "STRETCHED":        "moderate tightening (-12%) — sector above long-run fair value",
        "EXTREME":          "significant tightening (-20%) — sector at frothy multiples; "
                            "protect against multiple contraction",
    }[regime]
    note = (
        f"Sector '{sector_key}': live PE {live_pe:.1f}x "
        f"({abs_dev:.0f}% {direction} long-run {long_run_pe:.1f}x) → "
        f"{regime} — {action}"
    )

    regime_dict = {
        "regime":        regime,
        "multiplier":    multiplier,
        "live_pe":       live_pe,
        "long_run_pe":   long_run_pe,
        "deviation_pct": deviation_pct,
        "note":          note,
        "data_source":   data_src,
    }

    _regime_cache[sector_key] = (time.time() + _CACHE_TTL, regime_dict)
    log.info(
        "SectorRegime [%s]: live=%.1fx long_run=%.1fx dev=%+.0f%% → %s (×%.2f)",
        sector_key, live_pe, long_run_pe, deviation_pct, regime, multiplier,
    )
    return dict(regime_dict)


def get_live_sector_pe_map() -> dict[str, float]:
    """
    Return a dict mapping every known sector key to its current live P/E.
    Keys with unavailable live data are omitted.

    Useful for pre-fetching a full sector map before a batch discovery run.
    """
    # Trigger the NSE bulk fetch once
    nse_pes = _fetch_nse_all_index_pes()
    result: dict[str, float] = {}
    for sector_key in SECTOR_LONGRUN_PE:
        live = _get_live_pe_for_sector(sector_key)
        if live is not None:
            result[sector_key] = live
    return result


def clear_cache() -> None:
    """Flush the in-process regime cache (useful in tests)."""
    global _nse_pe_cache, _nse_cache_exp
    _regime_cache.clear()
    _nse_pe_cache  = {}
    _nse_cache_exp = 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Smoke test
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json as _json
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    for sec in ["it", "banking", "pharmaceuticals", "realty", "energy", "fmcg"]:
        r = get_sector_regime(sec)
        print(f"{sec:30s}  {r['regime']:20s}  ×{r['multiplier']:.2f}  "
              f"live={r['live_pe']}  long_run={r['long_run_pe']}  dev={r['deviation_pct']}")
