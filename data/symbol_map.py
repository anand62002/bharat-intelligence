"""
data/symbol_map.py — NSE Symbol Resolution & Normalisation
===========================================================
Single source of truth for symbol corrections across:
  - Yahoo Finance  (get_ohlcv, commodities, technical agent)
  - screener.in    (get_screener_data, fundamental agent)

Usage:
    from data.symbol_map import resolve_yf, resolve_screener, is_excluded

Public API:
    resolve_yf(symbol)       → correct Yahoo Finance ticker (e.g. "INDIGO.NS")
    resolve_screener(symbol) → correct screener.in slug    (e.g. "LTIMINDTREE")
    is_excluded(symbol)      → True if symbol should be skipped (US stocks, delisted)
    probe_and_resolve(symbol)→ validate via live yfinance call; returns working ticker or None

Why this module exists:
  Yahoo Finance and screener.in both deviate from NSE's official symbols in
  predictable ways:
    - Company renames  (WELSPUNIND → WELSPUNLIV)
    - Post-merger slugs (LTIM → LTIMINDTREE on screener.in)
    - Typos / spaces   (JSW ENERGY → JSWENERGY)
    - screener.in uses URL-safe slugs that differ from the NSE symbol
    - Apple Inc (AAPL) was accidentally included in the NIFTY 500 list
"""

import logging
import re
from typing import Optional

log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Symbols that must NEVER be sent to any Indian market API
# (US/foreign stocks, permanently delisted, or data errors)
# ──────────────────────────────────────────────────────────────────────────────
EXCLUDED_SYMBOLS: set[str] = {
    "AAPL",         # Apple Inc. — US stock, not NSE
    "GOOGL",        # Alphabet — US stock
    "MSFT",         # Microsoft — US stock
    "AMZN",         # Amazon — US stock
    "VIJAYA",       # Vijaya Bank — merged into Bank of Baroda (2019), delisted
    "DENABANK",     # Dena Bank — merged into Bank of Baroda (2019), delisted
    "CORPORATEBANK",# Corporation Bank — merged into Union Bank (2020), delisted
    "LAKSHVILAS",   # Lakshmi Vilas Bank — merged into DBS India (2020), delisted
    "IDEA",         # Vi (Vodafone Idea) — extremely distressed, often no data
}

# ──────────────────────────────────────────────────────────────────────────────
# Yahoo Finance ticker overrides
# Key   = NSE base symbol (no .NS, no .BO, UPPERCASE)
# Value = correct full Yahoo Finance ticker  (None = exclude)
# ──────────────────────────────────────────────────────────────────────────────
YF_SYMBOL_MAP: dict[str, Optional[str]] = {
    # ── Space / formatting issues ─────────────────────────────────────────────
    "JSW ENERGY":       "JSWENERGY.NS",      # space in symbol
    "JSW STEEL":        "JSWSTEEL.NS",
    "JSW INFRA":        "JSWINFRA.NS",

    # ── Company renames / mergers ─────────────────────────────────────────────
    "INTERGLOBE":       "INDIGO.NS",          # InterGlobe Aviation = IndiGo airline
    "WELSPUNIND":       "WELSPUNLIV.NS",      # Welspun India → Welspun Living (2022)
    "MINDTREE":         "LTIM.NS",            # Mindtree merged into LTIMindtree; YF ticker = LTIM
    "L&TINFOTECH":      "LTIM.NS",            # L&T Infotech merged into LTIMindtree
    "LTINFOTECH":       "LTIM.NS",
    "LTIMINDTREE":      "LTIM.NS",            # NSE brand name; YF uses shorter LTIM.NS
    # ── Corporate rebrands ────────────────────────────────────────────────────
    "ZOMATO":           "ETERNAL.NS",         # Zomato rebranded → Eternal (2025); NSE ticker = ETERNAL
    # ── Old/short NSE symbols that differ from Yahoo Finance ─────────────────
    "KPIT":             "KPITTECH.NS",        # KPIT Technologies NSE symbol = KPITTECH
    "CSB":              "CSBBANK.NS",         # CSB Bank NSE symbol = CSBBANK
    "DALMIA":           "DALBHARAT.NS",       # Dalmia Bharat NSE symbol = DALBHARAT

    # ── Brand-name / popular aliases (NSE ticker differs from brand) ──────────
    # Users often type the brand/display name rather than the exchange ticker.
    "IHCL":             "INDHOTEL.NS",        # Indian Hotels Co. (IHCL brand) → NSE: INDHOTEL
    "TAJHOTELS":        "INDHOTEL.NS",        # Taj Hotels brand alias
    "BHARATSEAT":       "BHARATSE.NS",        # Bharat Seats Ltd → NSE: BHARATSE
    "BHARATSEATS":      "BHARATSE.NS",        # plural alias
    "HITACHIENERGYINDIA": "POWERINDIA.NS",    # Hitachi Energy India → NSE: POWERINDIA
    "HITACHIENERGY":    "POWERINDIA.NS",      # short alias
    "POWERINDIA":       "POWERINDIA.NS",      # direct NSE symbol
    "MUTHOOT":          "MUTHOOTFIN.NS",      # Muthoot Finance (common short alias)
    "BAJAJ FINANCE":    "BAJFINANCE.NS",      # space alias
    "BAJAJFINANCE":     "BAJFINANCE.NS",      # no-space alias

    # ── BSE-only / wrong-suffix fixes ─────────────────────────────────────────
    "SHAKTIPUMPS":      "SHAKTIPUMP.NS",      # Shakti Pumps — NSE ticker is SHAKTIPUMP (no S)
    "GEVERNOVA":        "522275.BO",          # GE Vernova T&D India Ltd — NSE ticker absent in YF;
                                              # BSE code 522275 is the only working YF handle
    "GE VERNOVA":       "522275.BO",          # space-variant alias
    "GETDINDIA":        "522275.BO",          # legacy GE T&D India alias
    "ELFORGE":          "ELFORGE.BO",         # E L Forge Ltd — BSE listed; ELFORGE.NS returns no data
    "HDFCFIN":          "HDFCBANK.NS",        # HDFC Bank (post-merger)
    "L&T":              "LT.NS",              # Larsen & Toubro
    "LNT":              "LT.NS",              # another L&T alias
    "M&M":              "M&M.NS",             # Mahindra (handles ampersand)
    "MAHINDRA":         "M&M.NS",
    "TATAMOTORS":       "TATAMOTORS.NS",      # explicit pass-through (correct)
    "PREMEXPLN":        "PREMEXPLN.NS",       # Premier Explosives
    "TATAPOWER":        "TATAPOWER.NS",       # explicit pass-through
    "CIPLA":            "CIPLA.NS",           # explicit pass-through
    "DRREDDY":          "DRREDDY.NS",         # explicit pass-through

    # ── Yahoo Finance uses longer/different form ───────────────────────────────
    "DEEPAKNITRITE":    "DEEPAKNITR.NS",       # NSE list sometimes uses full name; YF uses DEEPAKNITR
    "AARTI":            "AARTIIND.NS",        # Aarti Industries
    "BARBEQUE":         "BARBEQUE-N.NS",      # Barbeque Nation (NSE: BARBEQUE-N)
    "FINOLEX":          "FINOLEXCAB.NS",      # Finolex Cables (vs. FINOLEXIND)
    "IPCA":             "IPCALAB.NS",         # IPCA Laboratories
    "CEAT":             "CEATLTD.NS",         # CEAT Ltd
    "HDFCAMC":          "HDFCAMC.NS",
    "NIELSENIQ":        None,                 # Not listed on NSE
    "SWIGGY":           "SWIGGY.NS",          # Swiggy (IPO 2024) — may not have 1y history

    # ── Excluded (US stocks, permanent delistings) ────────────────────────────
    "AAPL":             None,
    "GOOGL":            None,
    "MSFT":             None,
    "VIJAYA":           None,
    "DENABANK":         None,
    "LAKSHVILAS":       None,
}

# ──────────────────────────────────────────────────────────────────────────────
# screener.in slug overrides
# Key   = NSE base symbol (no .NS, UPPERCASE)
# Value = slug used in https://www.screener.in/company/{slug}/
# ──────────────────────────────────────────────────────────────────────────────
SCREENER_SLUG_MAP: dict[str, str] = {
    # ── Post-merger renames ───────────────────────────────────────────────────
    "LTIM":             "LTIMINDTREE",    # LTIMindtree post-merger slug
    "LTIMINDTREE":      "LTIMINDTREE",
    "MINDTREE":         "LTIMINDTREE",
    "L&TINFOTECH":      "LTIMINDTREE",

    # ── Welspun rename ────────────────────────────────────────────────────────
    "WELSPUNLIV":       "WELSPUNLIV",     # screener uses WELSPUNLIV
    "WELSPUNIND":       "WELSPUNLIV",     # old symbol redirects

    # ── Special characters ────────────────────────────────────────────────────
    # screener.in handles & fine in URLs (requests library encodes it)
    "M&M":              "M&M",
    "M&MFIN":           "M&MFIN",
    "BARBEQUE-N":       "BARBEQUE-N",
    "BAJAJ-AUTO":       "BAJAJ-AUTO",

    # ── Brand / display name vs. NSE symbol ──────────────────────────────────
    "DEEPAKNITRITE":    "DEEPAKNITR",     # screener uses shorter form
    "DEEPAKNITR":       "DEEPAKNITR",
    "AARTIIND":         "AARTIIND",
    "IPCALAB":          "IPCALAB",
    "FINOLEXCAB":       "FINOLEXCAB",

    # ── Others that need explicit mapping ─────────────────────────────────────
    "JSWENERGY":        "JSWENERGY",
    "JSWSTEEL":         "JSWSTEEL",
    "INDIGO":           "INTERGLOBE",     # screener uses INTERGLOBE for IndiGo
    # ── Post-rename / ticker change mappings ─────────────────────────────────
    "KPITTECH":         "KPITTECH",
    "CSBBANK":          "CSBBANK",
    "DALBHARAT":        "DALBHARAT",      # Dalmia Bharat
    "ETERNAL":          "ZOMATO",         # Zomato → Eternal rebrand; screener still uses ZOMATO slug
    "DEEPAKNITR":       "DEEPAKNITR",     # screener.in uses DEEPAKNITR
}

# ──────────────────────────────────────────────────────────────────────────────
# Probe cache: symbol → working YF ticker (or None = confirmed invalid)
# Avoids repeated network hits for the same symbol in one run
# ──────────────────────────────────────────────────────────────────────────────
_probe_cache: dict[str, Optional[str]] = {}


# ──────────────────────────────────────────────────────────────────────────────
# Core helpers
# ──────────────────────────────────────────────────────────────────────────────

def _base(symbol: str) -> str:
    """Strip .NS / .BO suffix and uppercase."""
    return symbol.upper().replace(".NS", "").replace(".BO", "").strip()


def is_excluded(symbol: str) -> bool:
    """Return True if this symbol must be skipped."""
    b = _base(symbol)
    if b in EXCLUDED_SYMBOLS:
        return True
    if b in YF_SYMBOL_MAP and YF_SYMBOL_MAP[b] is None:
        return True
    return False


def resolve_yf(symbol: str) -> Optional[str]:
    """
    Return the correct Yahoo Finance ticker for a given symbol.

    Examples:
        resolve_yf("INTERGLOBE.NS")   → "INDIGO.NS"
        resolve_yf("JSW ENERGY.NS")   → "JSWENERGY.NS"
        resolve_yf("AAPL.NS")         → None  (excluded)
        resolve_yf("TCS.NS")          → "TCS.NS"  (no change needed)

    Rules applied in order:
        1. Strip suffix, uppercase → base symbol
        2. If base in EXCLUDED_SYMBOLS → return None
        3. If base in YF_SYMBOL_MAP → return mapped value (None = excluded)
        4. Clean spaces and special formatting
        5. Ensure .NS suffix present → return as-is
    """
    b = _base(symbol)

    # Explicit exclusion
    if b in EXCLUDED_SYMBOLS:
        log.debug("resolve_yf: %s is excluded", symbol)
        return None

    # Explicit override
    if b in YF_SYMBOL_MAP:
        mapped = YF_SYMBOL_MAP[b]
        if mapped is None:
            log.debug("resolve_yf: %s maps to None (excluded)", symbol)
        return mapped

    # Auto-clean: remove internal spaces (e.g. "JSW ENERGY" → "JSWENERGY")
    cleaned_base = b.replace(" ", "")
    if cleaned_base != b:
        log.debug("resolve_yf: auto-cleaned space: %s → %s.NS", b, cleaned_base)
        return f"{cleaned_base}.NS"

    # Default: ensure .NS suffix
    if symbol.upper().endswith(".NS") or symbol.upper().endswith(".BO"):
        return symbol.upper()
    return f"{b}.NS"


def resolve_screener(symbol: str) -> str:
    """
    Return the correct screener.in URL slug for a given symbol.

    Examples:
        resolve_screener("LTIM.NS")        → "LTIMINDTREE"
        resolve_screener("INDIGO.NS")      → "INTERGLOBE"
        resolve_screener("TCS.NS")         → "TCS"
        resolve_screener("M&M.NS")         → "M&M"
    """
    b = _base(symbol)

    if b in SCREENER_SLUG_MAP:
        return SCREENER_SLUG_MAP[b]

    # Auto-clean spaces
    cleaned = b.replace(" ", "")
    if cleaned in SCREENER_SLUG_MAP:
        return SCREENER_SLUG_MAP[cleaned]

    return cleaned if cleaned else b


# ──────────────────────────────────────────────────────────────────────────────
# Live probe (optional — validates via a real yfinance call)
# ──────────────────────────────────────────────────────────────────────────────

def probe_and_resolve(symbol: str, period: str = "5d") -> Optional[str]:
    """
    Resolve the symbol and confirm it returns data from Yahoo Finance.
    Uses an in-process cache so each unique symbol is probed only once per run.

    Returns the working Yahoo Finance ticker, or None if no data found.

    This is an optional validation layer — it is NOT called on every get_ohlcv()
    because that would double the latency. Call it during pre-screen validation
    to build a confirmed-valid working set.
    """
    resolved = resolve_yf(symbol)
    if resolved is None:
        _probe_cache[symbol] = None
        return None

    if resolved in _probe_cache:
        return _probe_cache[resolved]

    # Try primary resolved symbol
    result = _try_yf(resolved, period)
    if result:
        _probe_cache[resolved] = resolved
        return resolved

    # Try alternate suffix (.BO instead of .NS)
    alt = resolved.replace(".NS", ".BO")
    result = _try_yf(alt, period)
    if result:
        log.info("probe_and_resolve: %s failed, %s works — using BSE", resolved, alt)
        _probe_cache[resolved] = alt
        return alt

    # Try base symbol without suffix (some indices)
    base = _base(resolved)
    result = _try_yf(base, period)
    if result:
        _probe_cache[resolved] = base
        return base

    log.warning("probe_and_resolve: no data found for %s (tried %s, %s)", symbol, resolved, alt)
    _probe_cache[resolved] = None
    return None


def _try_yf(ticker: str, period: str) -> bool:
    """Return True if yfinance returns at least 1 row for this ticker."""
    try:
        import yfinance as yf
        from data.fetchers import yf_fetch_with_retry
        t = yf.Ticker(ticker)
        df = yf_fetch_with_retry(t.history, period=period)
        return df is not None and not df.empty
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Batch helpers
# ──────────────────────────────────────────────────────────────────────────────

def clean_symbol_list(symbols: list[str]) -> list[str]:
    """
    Filter and resolve a list of symbols, returning only the valid,
    non-excluded symbols with correct Yahoo Finance tickers.
    Deduplicates the result.

    Args:
        symbols: list of raw symbols (may include .NS/.BO suffix or not)

    Returns:
        Deduplicated list of resolved valid Yahoo Finance tickers.
    """
    seen: set[str] = set()
    out: list[str] = []
    for sym in symbols:
        if is_excluded(sym):
            continue
        resolved = resolve_yf(sym)
        if resolved is None:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append(resolved)
    return out
