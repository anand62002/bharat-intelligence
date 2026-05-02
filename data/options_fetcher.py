"""
data/options_fetcher.py — NSE Option Chain Fetcher
====================================================
Fetches option chain data for Indian indices and equities from NSE.
Falls back to yfinance-derived estimates (India VIX + realized vol) when
NSE blocks server-side requests.

Data sources
------------
Primary   : NSE Open API  (requires browser-like cookie dance)
Fallback  : India VIX (^INDIAVIX) + NIFTY realized vol via yfinance

Key metrics returned
--------------------
  pcr             — Put-Call Ratio (total put OI / total call OI)
  max_pain        — Strike where aggregate option buyer loss is maximized
  atm_iv          — At-the-money implied volatility
  iv_skew         — OTM put IV − OTM call IV (positive = put-heavy skew)
  india_vix       — Current India VIX value
  hv20            — 20-day historical/realized volatility (annualised %)
  iv_hv_ratio     — India VIX / HV20 (>1.2 → fear; <0.8 → complacency)
  source          — "nse" | "fallback"

SQL (run once in Supabase)
--------------------------
No table needed — results are ephemeral (not persisted).

Usage
-----
    from data.options_fetcher import get_option_metrics
    m = get_option_metrics("NIFTY")          # index
    m = get_option_metrics("RELIANCE")       # equity (NSE only; fallback if blocked)
"""

from __future__ import annotations

import logging
import time
from typing import Optional

log = logging.getLogger(__name__)

# ─── NSE session constants ────────────────────────────────────────────────────
_NSE_BASE      = "https://www.nseindia.com"
_OC_INDEX_URL  = "https://www.nseindia.com/api/option-chain-indices?symbol={}"
_OC_EQUITY_URL = "https://www.nseindia.com/api/option-chain-equities?symbol={}"
_NSE_HEADERS   = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/option-chain",
}
# Symbols NSE considers "indices" for option-chain-indices endpoint
_NSE_INDEX_SYMBOLS = {
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYIT",
}


# ─── NSE session helper ───────────────────────────────────────────────────────

def _nse_session():
    """Return a requests.Session pre-warmed with NSE cookies."""
    try:
        import requests
        s = requests.Session()
        s.get(_NSE_BASE + "/option-chain", headers=_NSE_HEADERS, timeout=10)
        time.sleep(0.4)
        return s
    except Exception as exc:
        log.debug("NSE session init failed: %s", exc)
        return None


def _fetch_nse_option_chain(symbol: str) -> Optional[dict]:
    """
    Attempt to pull option chain JSON from NSE.
    Returns raw `records` dict or None on failure.
    """
    sym = symbol.upper().replace(".NS", "").replace(".BO", "")
    url = (
        _OC_INDEX_URL.format(sym)
        if sym in _NSE_INDEX_SYMBOLS
        else _OC_EQUITY_URL.format(sym)
    )
    sess = _nse_session()
    if sess is None:
        return None
    try:
        r = sess.get(url, headers=_NSE_HEADERS, timeout=20)
        if r.status_code != 200 or len(r.content) < 50:
            return None
        data = r.json()
        records = data.get("records") or {}
        if not records.get("data"):
            return None
        return records
    except Exception as exc:
        log.debug("NSE option chain fetch failed for %s: %s", sym, exc)
        return None


# ─── Metric calculators from NSE option chain ────────────────────────────────

def _compute_pcr(records: dict) -> Optional[float]:
    """PCR = total put OI / total call OI across all strikes."""
    try:
        total_put_oi  = 0.0
        total_call_oi = 0.0
        for row in records.get("data", []):
            pe = row.get("PE") or {}
            ce = row.get("CE") or {}
            total_put_oi  += float(pe.get("openInterest") or 0)
            total_call_oi += float(ce.get("openInterest") or 0)
        if total_call_oi <= 0:
            return None
        return round(total_put_oi / total_call_oi, 3)
    except Exception:
        return None


def _compute_max_pain(records: dict) -> Optional[float]:
    """
    Max pain strike = strike at which total payout to all option buyers
    (sum of intrinsic values) is minimised.
    """
    try:
        data = records.get("data", [])
        if not data:
            return None

        strikes = sorted({float(r.get("strikePrice", 0)) for r in data if r.get("strikePrice")})
        if not strikes:
            return None

        # Build per-strike OI maps
        call_oi: dict[float, float] = {}
        put_oi:  dict[float, float] = {}
        for row in data:
            k = float(row.get("strikePrice", 0))
            call_oi[k] = float((row.get("CE") or {}).get("openInterest") or 0)
            put_oi[k]  = float((row.get("PE") or {}).get("openInterest") or 0)

        best_strike = strikes[0]
        min_pain    = float("inf")
        for candidate in strikes:
            # Payout to call holders if market settles at `candidate`
            call_pain = sum(
                max(0.0, candidate - k) * call_oi.get(k, 0)
                for k in strikes
            )
            put_pain = sum(
                max(0.0, k - candidate) * put_oi.get(k, 0)
                for k in strikes
            )
            total = call_pain + put_pain
            if total < min_pain:
                min_pain    = total
                best_strike = candidate

        return float(best_strike)
    except Exception:
        return None


def _compute_atm_iv_and_skew(records: dict, underlying: float) -> tuple[Optional[float], Optional[float]]:
    """
    Returns (atm_iv, iv_skew).
    atm_iv  — average IV of the nearest-ATM strike
    iv_skew — avg IV of 2% OTM puts minus avg IV of 2% OTM calls
    """
    try:
        data = records.get("data", [])
        if not data or underlying <= 0:
            return None, None

        def _nearest(target: float) -> list[dict]:
            nearest = min(data, key=lambda r: abs(float(r.get("strikePrice", 0)) - target))
            return [nearest]

        # ATM IV
        atm_rows = _nearest(underlying)
        ivs = []
        for row in atm_rows:
            ce_iv = float((row.get("CE") or {}).get("impliedVolatility") or 0)
            pe_iv = float((row.get("PE") or {}).get("impliedVolatility") or 0)
            if ce_iv > 0: ivs.append(ce_iv)
            if pe_iv > 0: ivs.append(pe_iv)
        atm_iv = round(sum(ivs) / len(ivs), 2) if ivs else None

        # OTM put IV (strikes ~2% below spot)
        otm_put_strike  = underlying * 0.98
        otm_call_strike = underlying * 1.02
        put_ivs  = []
        call_ivs = []
        for row in data:
            k = float(row.get("strikePrice", 0))
            if abs(k - otm_put_strike) / underlying < 0.01:
                iv = float((row.get("PE") or {}).get("impliedVolatility") or 0)
                if iv > 0: put_ivs.append(iv)
            if abs(k - otm_call_strike) / underlying < 0.01:
                iv = float((row.get("CE") or {}).get("impliedVolatility") or 0)
                if iv > 0: call_ivs.append(iv)

        iv_skew = None
        if put_ivs and call_ivs:
            iv_skew = round(
                sum(put_ivs) / len(put_ivs) - sum(call_ivs) / len(call_ivs),
                2
            )
        return atm_iv, iv_skew
    except Exception:
        return None, None


# ─── yfinance-based fallback metrics ─────────────────────────────────────────

def _fallback_metrics(symbol: str) -> dict:
    """
    Compute options-related signals from India VIX + realized volatility
    when NSE option chain is unavailable.

    Returns a partial metrics dict (source="fallback").
    """
    try:
        import yfinance as yf
        import numpy as np

        # India VIX
        vix_hist  = yf.download("^INDIAVIX", period="5d", progress=False, auto_adjust=True)
        india_vix = float(vix_hist["Close"].squeeze().iloc[-1]) if not vix_hist.empty else None

        # Underlying price
        yf_sym = _resolve_yf_sym(symbol)
        hist   = yf.download(yf_sym, period="60d", interval="1d", progress=False, auto_adjust=True)
        if hist.empty:
            return {"source": "fallback", "error": "no_price_data", "india_vix": india_vix}

        close = hist["Close"].squeeze()
        rets  = close.pct_change().dropna()
        hv20  = round(float(rets.tail(20).std() * np.sqrt(252) * 100), 2) if len(rets) >= 20 else None

        iv_hv_ratio = (
            round(india_vix / hv20, 3)
            if india_vix and hv20 and hv20 > 0
            else None
        )

        # Estimated PCR proxy: use VIX level as fear gauge
        # VIX > 20 → put premium elevated → estimated PCR > 1
        pcr_estimate = None
        if india_vix is not None:
            # Simple linear mapping: VIX 12 → PCR ~0.7, VIX 25 → PCR ~1.3
            pcr_estimate = round(0.7 + (india_vix - 12) * (0.6 / 13), 3)
            pcr_estimate = max(0.3, min(2.5, pcr_estimate))

        # Estimated max pain: ±1σ (20-day) around current price
        cur_price = float(close.iloc[-1])
        max_pain_estimate = None
        if hv20:
            sigma_1d   = hv20 / 100 / np.sqrt(252)
            sigma_20d  = sigma_1d * np.sqrt(20)
            # Max pain tends to be slightly below spot (writers sell calls above)
            max_pain_estimate = round(cur_price * (1 - 0.3 * sigma_20d), 2)

        return {
            "symbol":           symbol.upper(),
            "india_vix":        india_vix,
            "hv20":             hv20,
            "iv_hv_ratio":      iv_hv_ratio,
            "pcr":              pcr_estimate,
            "max_pain":         max_pain_estimate,
            "atm_iv":           india_vix,        # India VIX ≈ 30-day ATM IV for NIFTY
            "iv_skew":          None,             # Cannot estimate without option chain
            "underlying_price": round(cur_price, 2),
            "source":           "fallback",
        }
    except Exception as exc:
        log.debug("Fallback metrics failed for %s: %s", symbol, exc)
        return {"source": "fallback", "error": str(exc)}


def _resolve_yf_sym(symbol: str) -> str:
    """Quick resolution: use symbol_map if available, else append .NS."""
    try:
        from data.symbol_map import YF_SYMBOL_MAP
        return YF_SYMBOL_MAP.get(symbol.upper(), symbol.upper() + ".NS")
    except ImportError:
        pass
    sym = symbol.upper()
    if sym in _NSE_INDEX_SYMBOLS:
        return "^NSEI" if sym == "NIFTY" else "^NSEBANK"
    return sym + ".NS"


# ─── Public API ──────────────────────────────────────────────────────────────

def get_option_metrics(symbol: str) -> dict:
    """
    Fetch option chain metrics for `symbol`.

    Tries NSE API first; falls back to India VIX + realized vol estimates
    if NSE is unavailable (common in cloud/server environments).

    Parameters
    ----------
    symbol : str
        NSE symbol, e.g. "NIFTY", "BANKNIFTY", "RELIANCE".

    Returns
    -------
    dict with keys:
        symbol, pcr, max_pain, atm_iv, iv_skew,
        india_vix, hv20, iv_hv_ratio, underlying_price, source
    """
    sym = symbol.upper().replace(".NS", "").replace(".BO", "")

    # --- Attempt NSE option chain ---
    records = _fetch_nse_option_chain(sym)
    if records:
        underlying = float(records.get("underlyingValue") or 0)
        pcr        = _compute_pcr(records)
        max_pain   = _compute_max_pain(records)
        atm_iv, iv_skew = _compute_atm_iv_and_skew(records, underlying)
        log.debug("option_metrics(%s): NSE source", sym)
        return {
            "symbol":           sym,
            "pcr":              pcr,
            "max_pain":         max_pain,
            "atm_iv":           atm_iv,
            "iv_skew":          iv_skew,
            "india_vix":        None,   # not from NSE chain
            "hv20":             None,
            "iv_hv_ratio":      None,
            "underlying_price": underlying,
            "source":           "nse",
        }

    # --- Fallback ---
    log.debug("option_metrics(%s): NSE unavailable, using fallback", sym)
    fb = _fallback_metrics(sym)
    fb["symbol"] = sym
    return fb
