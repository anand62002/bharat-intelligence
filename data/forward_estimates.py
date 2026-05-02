"""
data/forward_estimates.py — Forward Earnings Estimates
=======================================================
Fetches consensus analyst EPS / Revenue estimates from yfinance and computes
forward PE and PEG ratios.  Results are cached in Supabase
`forward_estimates_cache` for 24 hours so the orchestrator can run without
hammering yfinance on every symbol.

Supabase migration (run once):
-------------------------------
    CREATE TABLE IF NOT EXISTS forward_estimates_cache (
        symbol          TEXT PRIMARY KEY,
        eps_current_yr  NUMERIC,
        eps_next_yr     NUMERIC,
        rev_current_yr  NUMERIC,
        rev_next_yr     NUMERIC,
        eps_growth_pct  NUMERIC,
        forward_pe      NUMERIC,
        peg_ratio       NUMERIC,
        current_price   NUMERIC,
        analyst_count   INT,
        cached_at       TIMESTAMPTZ DEFAULT now()
    );
    GRANT ALL ON forward_estimates_cache TO service_role;

Usage
-----
    from data.forward_estimates import get_forward_estimates
    est = get_forward_estimates("RELIANCE")
    # {'symbol':'RELIANCE','eps_current_yr':120.5,'eps_next_yr':142.0,
    #   'eps_growth_pct':17.8,'forward_pe':22.4,'peg_ratio':1.26,...}

    # Bust cache
    est = get_forward_estimates("RELIANCE", force_refresh=True)

Standalone
----------
    python -m data.forward_estimates RELIANCE
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_CACHE_TTL_HOURS = 24


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _plain(symbol: str) -> str:
    return symbol.replace(".NS", "").replace(".BO", "").upper()


def _to_yf(symbol: str) -> str:
    return f"{_plain(symbol)}.NS"


def _safe_float(val) -> Optional[float]:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


# ──────────────────────────────────────────────────────────────────────────────
# yfinance fetch
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_from_yfinance(symbol: str) -> dict:
    """
    Pull analyst estimates via yfinance Ticker.info + earnings_estimate.

    Returns a dict with raw estimate fields; may have None values if
    yfinance doesn't have data for this symbol.
    """
    import yfinance as yf

    ticker = yf.Ticker(_to_yf(symbol))
    info   = ticker.info or {}

    eps_cur  = _safe_float(info.get("forwardEps"))
    pe_fwd   = _safe_float(info.get("forwardPE"))
    peg      = _safe_float(info.get("pegRatio"))
    price    = _safe_float(info.get("currentPrice") or info.get("regularMarketPrice"))
    analysts = info.get("numberOfAnalystOpinions")

    # Revenue estimates (yfinance stores TTM and forward)
    rev_cur  = _safe_float(info.get("revenuePerShare"))
    rev_next = None

    # EPS growth: yfinance exposes earningsGrowth (TTM) or earningsQuarterlyGrowth
    # We prefer the more forward-looking earningsGrowth as a proxy.
    eps_growth_raw = _safe_float(
        info.get("earningsGrowth") or info.get("earningsQuarterlyGrowth")
    )
    eps_growth_pct = round(eps_growth_raw * 100, 2) if eps_growth_raw is not None else None

    # Derive next-year EPS from current + growth rate when not directly available
    eps_next = None
    if eps_cur is not None and eps_growth_pct is not None:
        eps_next = round(eps_cur * (1 + eps_growth_pct / 100), 2)

    # If forwardPE and eps_cur are available but price is missing, derive price
    if price is None and pe_fwd is not None and eps_cur is not None and eps_cur != 0:
        price = round(pe_fwd * eps_cur, 2)

    # If forwardPE missing but we have price and eps_cur, compute it
    if pe_fwd is None and price is not None and eps_cur is not None and eps_cur > 0:
        pe_fwd = round(price / eps_cur, 2)

    # PEG = forward_PE / eps_growth_pct
    if peg is None and pe_fwd is not None and eps_growth_pct and eps_growth_pct > 0:
        peg = round(pe_fwd / eps_growth_pct, 2)

    return {
        "symbol":          _plain(symbol),
        "eps_current_yr":  eps_cur,
        "eps_next_yr":     eps_next,
        "rev_current_yr":  rev_cur,
        "rev_next_yr":     rev_next,
        "eps_growth_pct":  eps_growth_pct,
        "forward_pe":      pe_fwd,
        "peg_ratio":       peg,
        "current_price":   price,
        "analyst_count":   int(analysts) if analysts else None,
        "cached_at":       datetime.now(timezone.utc).isoformat(),
        "source":          "yfinance",
        "error":           None,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Supabase cache
# ──────────────────────────────────────────────────────────────────────────────

def _supabase_client():
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        return None
    from supabase import create_client
    return create_client(url, key)


def _cache_read(symbol: str) -> Optional[dict]:
    """Return cached estimate if still fresh, else None."""
    client = _supabase_client()
    if client is None:
        return None
    try:
        rows = (
            client
            .table("forward_estimates_cache")
            .select("*")
            .eq("symbol", _plain(symbol))
            .limit(1)
            .execute()
            .data or []
        )
        if not rows:
            return None
        row = rows[0]
        cached_at = datetime.fromisoformat(row["cached_at"].replace("Z", "+00:00"))
        age_h = (datetime.now(timezone.utc) - cached_at).total_seconds() / 3600
        if age_h > _CACHE_TTL_HOURS:
            return None
        row["source"] = "cache"
        row["error"]  = None
        return row
    except Exception as exc:
        log.debug("forward_estimates cache read failed: %s", exc)
        return None


def _cache_write(est: dict) -> None:
    """Upsert estimate into Supabase cache table."""
    client = _supabase_client()
    if client is None:
        return
    try:
        payload = {
            "symbol":          est["symbol"],
            "eps_current_yr":  est.get("eps_current_yr"),
            "eps_next_yr":     est.get("eps_next_yr"),
            "rev_current_yr":  est.get("rev_current_yr"),
            "rev_next_yr":     est.get("rev_next_yr"),
            "eps_growth_pct":  est.get("eps_growth_pct"),
            "forward_pe":      est.get("forward_pe"),
            "peg_ratio":       est.get("peg_ratio"),
            "current_price":   est.get("current_price"),
            "analyst_count":   est.get("analyst_count"),
            "cached_at":       datetime.now(timezone.utc).isoformat(),
        }
        (
            client
            .table("forward_estimates_cache")
            .upsert(payload, on_conflict="symbol")
            .execute()
        )
    except Exception as exc:
        log.debug("forward_estimates cache write failed: %s", exc)


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def get_forward_estimates(symbol: str, force_refresh: bool = False) -> dict:
    """
    Return forward earnings estimates for `symbol`.

    Lookup order:
      1. Supabase cache (if < 24h old and not force_refresh)
      2. yfinance live fetch → write to cache

    Returns
    -------
    dict with keys:
      symbol, eps_current_yr, eps_next_yr, rev_current_yr, rev_next_yr,
      eps_growth_pct, forward_pe, peg_ratio, current_price, analyst_count,
      cached_at, source, error
    """
    plain = _plain(symbol)

    # ── 1. Supabase cache ─────────────────────────────────────────────────────
    if not force_refresh:
        cached = _cache_read(plain)
        if cached:
            log.debug("forward_estimates(%s): cache hit", plain)
            return cached

    # ── 2. yfinance live ──────────────────────────────────────────────────────
    try:
        est = _fetch_from_yfinance(plain)
        _cache_write(est)
        return est
    except Exception as exc:
        log.warning("forward_estimates(%s) fetch failed: %s", plain, exc)
        return {
            "symbol":          plain,
            "eps_current_yr":  None,
            "eps_next_yr":     None,
            "rev_current_yr":  None,
            "rev_next_yr":     None,
            "eps_growth_pct":  None,
            "forward_pe":      None,
            "peg_ratio":       None,
            "current_price":   None,
            "analyst_count":   None,
            "cached_at":       None,
            "source":          "error",
            "error":           str(exc),
        }


def interpret_estimates(est: dict) -> dict:
    """
    Return a human-readable signal based on forward PE and PEG.

    Returns a dict:
      {valuation_signal, forward_pe_comment, peg_comment, summary}
    """
    fpe  = est.get("forward_pe")
    peg  = est.get("peg_ratio")
    gr   = est.get("eps_growth_pct")

    # Forward PE signal
    if fpe is None:
        pe_signal = "UNKNOWN"
        pe_comment = "Forward PE not available"
    elif fpe < 10:
        pe_signal = "CHEAP"
        pe_comment = f"Forward PE {fpe:.1f}x — deeply discounted vs market"
    elif fpe < 20:
        pe_signal = "FAIR"
        pe_comment = f"Forward PE {fpe:.1f}x — reasonable valuation"
    elif fpe < 35:
        pe_signal = "GROWTH"
        pe_comment = f"Forward PE {fpe:.1f}x — pricing in growth"
    else:
        pe_signal = "EXPENSIVE"
        pe_comment = f"Forward PE {fpe:.1f}x — elevated, growth must deliver"

    # PEG signal
    if peg is None:
        peg_comment = "PEG not available"
        peg_signal  = "UNKNOWN"
    elif peg < 0.5:
        peg_signal  = "UNDERVALUED"
        peg_comment = f"PEG {peg:.2f} — stock appears undervalued relative to growth"
    elif peg < 1.0:
        peg_signal  = "FAIR"
        peg_comment = f"PEG {peg:.2f} — fair value relative to growth"
    elif peg < 2.0:
        peg_signal  = "FULL"
        peg_comment = f"PEG {peg:.2f} — fully priced relative to growth"
    else:
        peg_signal  = "EXPENSIVE"
        peg_comment = f"PEG {peg:.2f} — expensive relative to growth rate"

    # Overall signal — weight PEG over forward PE if both available
    if peg is not None:
        valuation_signal = peg_signal
    else:
        valuation_signal = pe_signal

    growth_str = f" ({gr:.1f}% EPS growth expected)" if gr is not None else ""
    summary    = f"{pe_comment}. {peg_comment}{growth_str}."

    return {
        "valuation_signal":   valuation_signal,
        "forward_pe_comment": pe_comment,
        "peg_comment":        peg_comment,
        "summary":            summary,
    }


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    from dotenv import load_dotenv
    load_dotenv()
    sym = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    est = get_forward_estimates(sym, force_refresh=True)
    interp = interpret_estimates(est)
    print(json.dumps({**est, **interp}, indent=2))
