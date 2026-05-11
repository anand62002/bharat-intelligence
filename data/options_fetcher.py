"""
data/options_fetcher.py — Option Chain Fetcher (Breeze + NSE + Fallback)
=========================================================================
Fetches option chain data for Indian indices and equities.

Data source priority
--------------------
1. ICICI Breeze Connect  (real option chain OI + IV — requires BREEZE_* env vars)
2. NSE Open API          (requires browser-like cookie dance; blocked on Railway)
3. Fallback              (India VIX + realized vol estimates via yfinance)

Key metrics returned
--------------------
  pcr             — Put-Call Ratio (total put OI / total call OI)
  max_pain        — Strike where aggregate option buyer loss is maximized
  atm_iv          — At-the-money implied volatility
  iv_skew         — OTM put IV − OTM call IV (positive = put-heavy skew)
  india_vix       — Current India VIX value
  hv20            — 20-day historical/realized volatility (annualised %)
  iv_hv_ratio     — India VIX / HV20 (>1.2 → fear; <0.8 → complacency)
  source          — "breeze" | "nse" | "fallback"

SQL (run once in Supabase)
--------------------------
No table needed — results are ephemeral (not persisted).

Usage
-----
    from data.options_fetcher import get_option_metrics
    m = get_option_metrics("NIFTY")          # index
    m = get_option_metrics("RELIANCE")       # equity
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Optional

log = logging.getLogger(__name__)

# ── Breeze option-chain result cache (15-min TTL per symbol) ─────────────────
_breeze_cache: dict[str, dict] = {}   # symbol → {"data": [...], "ts": float}

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


# ─── Breeze Connect option chain (primary source) ────────────────────────────

# Stock codes Breeze uses for indices (NFO exchange, product_type = "options")
_BREEZE_INDEX_CODES = {
    "NIFTY":      "NIFTY",
    "BANKNIFTY":  "BANKNIFTY",
    "FINNIFTY":   "FINNIFTY",
    "MIDCPNIFTY": "MIDCPNIFTY",
}

# Strike step (points) per index / equity
_STRIKE_STEP = {
    "NIFTY":      50,
    "BANKNIFTY":  100,
    "FINNIFTY":   50,
    "MIDCPNIFTY": 25,
}
_DEFAULT_STRIKE_STEP = 5   # for single-stock options


def _get_near_expiry_date() -> str:
    """
    Return the nearest upcoming Thursday as ISO-8601 string used by Breeze.
    NSE weekly options (NIFTY/BANKNIFTY) expire every Thursday.
    If today is Thursday, return next Thursday (avoid same-day expiry artefacts).
    Format: "YYYY-MM-DDT06:00:00.000Z"  (matches Breeze SDK examples)
    """
    today = date.today()
    # 3 = Thursday in Python's weekday() (Mon=0 … Sun=6)
    days_ahead = (3 - today.weekday()) % 7
    if days_ahead == 0:          # today is Thursday — roll to next week
        days_ahead = 7
    expiry = today + timedelta(days=days_ahead)
    return f"{expiry.isoformat()}T06:00:00.000Z"


def _get_underlying_price(symbol: str) -> Optional[float]:
    """Fetch current spot price via yfinance (fast — 1-bar query)."""
    try:
        import yfinance as yf
        yf_sym = _resolve_yf_sym(symbol)
        t = yf.Ticker(yf_sym)
        price = t.fast_info.last_price
        if price and price > 0:
            return float(price)
        # Fallback to 1-day history
        hist = yf.download(yf_sym, period="2d", progress=False, auto_adjust=True)
        if not hist.empty:
            return float(hist["Close"].squeeze().iloc[-1])
    except Exception as exc:
        log.debug("_get_underlying_price(%s): %s", symbol, exc)
    return None


def _build_strike_range(spot: float, step: int, pct: float = 0.08) -> list[int]:
    """
    Return strikes from (spot − pct%) to (spot + pct%) aligned to `step`.
    Covers ~80–85% of open interest for indices.
    """
    lo = int((spot * (1 - pct)) // step) * step
    hi = int((spot * (1 + pct)) // step + 1) * step
    return list(range(lo, hi + step, step))


def _fetch_one_breeze_strike(
    breeze,
    stock_code: str,
    exchange_code: str,
    expiry_date: str,
    strike: int,
    right: str,
) -> Optional[dict]:
    """Fetch a single strike/right pair from Breeze API."""
    try:
        from data.breeze_auth import breeze_proxy
        with breeze_proxy():
            resp = breeze.get_option_chain_quotes(
                stock_code=stock_code,
                exchange_code=exchange_code,
                product_type="options",
                expiry_date=expiry_date,
                right=right.lower(),
                strike_price=str(strike),
            )
        rows = (resp or {}).get("Success") or []
        return rows[0] if rows else None
    except Exception as exc:
        log.debug("Breeze strike fetch failed [%s %s %s]: %s", stock_code, strike, right, exc)
        return None


def _fetch_breeze_option_chain(symbol: str) -> Optional[list[dict]]:
    """
    Fetch the near-month option chain via Breeze Connect.

    Strategy:
      1. Try bulk call with strike_price="" — Breeze API may return all strikes.
      2. If that returns nothing, fan out individual strike calls in parallel
         (ThreadPoolExecutor, 10 workers, ±8% from spot).

    Returns list of dicts, each with keys:
        strike_price, right, open_interest, implied_volatility_of_ltp

    Returns None if Breeze is not configured or all attempts fail.
    """
    from data.breeze_auth import get_breeze_client  # lazy import to avoid circular

    # Check 15-min cache
    cached = _breeze_cache.get(symbol)
    if cached and time.time() - cached["ts"] < 900:
        log.debug("Breeze option chain [%s]: serving from cache", symbol)
        return cached["data"]

    breeze = get_breeze_client()
    if breeze is None:
        return None

    sym        = symbol.upper()
    is_index   = sym in _BREEZE_INDEX_CODES
    stock_code = _BREEZE_INDEX_CODES.get(sym, sym)
    exchange   = "NFO" if is_index else "NFO"
    expiry     = _get_near_expiry_date()
    step       = _STRIKE_STEP.get(sym, _DEFAULT_STRIKE_STEP)

    # ── Strategy 1: bulk call with empty strike_price ──────────────────────
    from data.breeze_auth import breeze_proxy
    rows_bulk: list[dict] = []
    for right in ("call", "put"):
        try:
            with breeze_proxy():
                resp = breeze.get_option_chain_quotes(
                    stock_code=stock_code,
                    exchange_code=exchange,
                    product_type="options",
                    expiry_date=expiry,
                    right=right,
                    strike_price="",
                )
            success = (resp or {}).get("Success") or []
            rows_bulk.extend(success)
        except Exception as exc:
            log.debug("Breeze bulk fetch failed [%s %s]: %s", sym, right, exc)

    if rows_bulk:
        _breeze_cache[symbol] = {"data": rows_bulk, "ts": time.time()}
        log.info("Breeze option chain [%s]: bulk fetch — %d rows (source=breeze)", sym, len(rows_bulk))
        return rows_bulk

    # ── Strategy 2: parallel individual strike calls ───────────────────────
    spot = _get_underlying_price(sym)
    if spot is None:
        log.warning("Breeze option chain [%s]: can't determine spot price", sym)
        return None

    strikes = _build_strike_range(spot, step, pct=0.08)
    log.debug("Breeze option chain [%s]: individual calls — spot=%.0f, strikes=%d", sym, spot, len(strikes))

    tasks = []
    for strike in strikes:
        for right in ("call", "put"):
            tasks.append((strike, right))

    rows_parallel: list[dict] = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {
            pool.submit(
                _fetch_one_breeze_strike,
                breeze, stock_code, exchange, expiry, strike, right,
            ): (strike, right)
            for strike, right in tasks
        }
        for fut in as_completed(futures):
            row = fut.result()
            if row:
                rows_parallel.append(row)

    if rows_parallel:
        _breeze_cache[symbol] = {"data": rows_parallel, "ts": time.time()}
        log.info(
            "Breeze option chain [%s]: parallel fetch — %d/%d rows returned (source=breeze)",
            sym, len(rows_parallel), len(tasks),
        )
        return rows_parallel

    log.warning("Breeze option chain [%s]: all strategies returned empty", sym)
    return None


def _parse_breeze_chain(rows: list[dict], spot: float) -> dict:
    """
    Convert Breeze option chain rows into the same metrics as the NSE parser.
    Breeze row keys: strike_price, right, open_interest, implied_volatility_of_ltp
    """
    call_oi: dict[float, float] = {}
    put_oi:  dict[float, float] = {}
    call_iv: dict[float, float] = {}
    put_iv:  dict[float, float] = {}

    for row in rows:
        try:
            k    = float(row.get("strike_price") or 0)
            oi   = float(row.get("open_interest") or 0)
            iv   = float(row.get("implied_volatility_of_ltp") or 0)
            side = str(row.get("right") or "").lower()
            if k <= 0:
                continue
            if "call" in side or side == "ce":
                call_oi[k] = oi
                if iv > 0:
                    call_iv[k] = iv
            elif "put" in side or side == "pe":
                put_oi[k] = oi
                if iv > 0:
                    put_iv[k] = iv
        except Exception:
            continue

    total_call = sum(call_oi.values())
    total_put  = sum(put_oi.values())
    pcr        = round(total_put / total_call, 3) if total_call > 0 else None

    # Max pain
    strikes = sorted(set(call_oi) | set(put_oi))
    max_pain = None
    if strikes:
        best, min_pain = strikes[0], float("inf")
        for cand in strikes:
            pain = (
                sum(max(0.0, cand - k) * call_oi.get(k, 0) for k in strikes) +
                sum(max(0.0, k - cand) * put_oi.get(k, 0) for k in strikes)
            )
            if pain < min_pain:
                min_pain = pain
                best     = cand
        max_pain = float(best)

    # ATM IV (nearest strike to spot)
    atm_iv = None
    if spot > 0 and strikes:
        nearest_k = min(strikes, key=lambda k: abs(k - spot))
        ivs = []
        if call_iv.get(nearest_k, 0) > 0:
            ivs.append(call_iv[nearest_k])
        if put_iv.get(nearest_k, 0) > 0:
            ivs.append(put_iv[nearest_k])
        atm_iv = round(sum(ivs) / len(ivs), 2) if ivs else None

    # IV skew: OTM put IV (−2% strike) − OTM call IV (+2% strike)
    iv_skew = None
    if spot > 0:
        otm_put_k  = min(strikes, key=lambda k: abs(k - spot * 0.98)) if strikes else None
        otm_call_k = min(strikes, key=lambda k: abs(k - spot * 1.02)) if strikes else None
        if otm_put_k and otm_call_k and put_iv.get(otm_put_k) and call_iv.get(otm_call_k):
            iv_skew = round(put_iv[otm_put_k] - call_iv[otm_call_k], 2)

    return {
        "pcr":      pcr,
        "max_pain": max_pain,
        "atm_iv":   atm_iv,
        "iv_skew":  iv_skew,
    }


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


_INDEX_YF_MAP = {
    "NIFTY":      "^NSEI",
    "BANKNIFTY":  "^NSEBANK",
    "FINNIFTY":   "^NSEI",       # closest proxy
    "MIDCPNIFTY": "^NSEI",       # closest proxy
    "NIFTYIT":    "^CNXIT",
}


def _resolve_yf_sym(symbol: str) -> str:
    """Quick resolution: index overrides first, then YF_SYMBOL_MAP, then .NS suffix."""
    sym = symbol.upper()
    # Index symbols must be resolved before YF_SYMBOL_MAP (which would return NIFTY.NS)
    if sym in _INDEX_YF_MAP:
        return _INDEX_YF_MAP[sym]
    try:
        from data.symbol_map import YF_SYMBOL_MAP
        return YF_SYMBOL_MAP.get(sym, sym + ".NS")
    except ImportError:
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

    # ── Priority 1: ICICI Breeze Connect (real option chain) ──────────────────
    breeze_rows = _fetch_breeze_option_chain(sym)
    if breeze_rows:
        spot     = _get_underlying_price(sym) or 0.0
        metrics  = _parse_breeze_chain(breeze_rows, spot)
        # Enrich with India VIX + HV20 (cheap yfinance call — always useful)
        fb_extra = _fallback_metrics(sym)
        log.debug("option_metrics(%s): Breeze source", sym)
        return {
            "symbol":           sym,
            "pcr":              metrics["pcr"],
            "max_pain":         metrics["max_pain"],
            "atm_iv":           metrics["atm_iv"],
            "iv_skew":          metrics["iv_skew"],
            "india_vix":        fb_extra.get("india_vix"),
            "hv20":             fb_extra.get("hv20"),
            "iv_hv_ratio":      fb_extra.get("iv_hv_ratio"),
            "underlying_price": spot or fb_extra.get("underlying_price"),
            "source":           "breeze",
        }

    # ── Priority 2: NSE Open API ───────────────────────────────────────────────
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

    # ── Priority 3: India VIX + realized vol fallback ─────────────────────────
    log.debug("option_metrics(%s): Breeze+NSE unavailable, using VIX fallback", sym)
    fb = _fallback_metrics(sym)
    fb["symbol"] = sym
    return fb
