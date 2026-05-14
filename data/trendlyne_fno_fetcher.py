"""
data/trendlyne_fno_fetcher.py
==============================
Downloads Trendlyne's daily F&O Excel (free, no fincsv API needed) and
exposes option metrics compatible with data/options_fetcher.py.

Data source:
  https://trendlyne.com/futures-options/contracts-excel-download/
  → redirects to a fresh presigned S3 URL → 3.4 MB Excel with two sheets:
    • "F&O Data"   — 18k+ rows: every contract (futures + CE + PE)
    • "F&O Stocks" — 219 rows:  per-stock PCR, vol, OI, rollover

Requires env vars:
  TRENDLYNE_SESSION  (.trendlyne cookie value)
  TRENDLYNE_CSRF     (csrftoken cookie value)

Falls back gracefully if cookies are missing / expired.

Memory design:
  The raw Excel DataFrames are processed into a compact per-symbol dict at
  download time and then immediately deleted (+ gc.collect()).  The in-process
  cache therefore holds only ~219 small dicts instead of two large DataFrames
  (~18 k × 20 col) for the entire worker lifetime.
  One download per 6-hour TTL covers all 218 F&O stocks.
"""

from __future__ import annotations

import gc
import io
import logging
import os
import time
import urllib.parse
from typing import Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# ── credentials (set as env vars, or update here for local dev) ────────────
_TL_SESS = os.getenv("TRENDLYNE_SESSION", "bce34ncrlhwelezjn884vaxvyh0g543w")
_TL_CSRF = os.getenv("TRENDLYNE_CSRF",   "8pNPblqCbfTikxjVbCLRZwDbQq6cdZZwSWRsNjBE5AXZGgDVcZMNZNzNhtycXVDS")

_FNO_PAGE_URL = "https://trendlyne.com/futures-options/contracts-excel-download/"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

# ── in-process cache ────────────────────────────────────────────────────────
# Holds compact pre-processed dicts, NOT the raw DataFrames.
# Structure: {"metrics": {symbol: metrics_dict}, "universe": [list], "ts": float}
_cache: dict = {}
_CACHE_TTL = 3600 * 6     # 6 hours — data is EOD, no point re-downloading intraday


# ──────────────────────────────────────────────────────────────────────────────
# Download helpers
# ──────────────────────────────────────────────────────────────────────────────

def _get_presigned_url() -> str:
    """Follow the Trendlyne redirect (needs session cookies) to get the S3 URL."""
    s = requests.Session()
    s.headers.update({"User-Agent": _UA})
    if _TL_SESS:
        s.cookies.set(".trendlyne", _TL_SESS, domain="trendlyne.com")
    if _TL_CSRF:
        s.cookies.set("csrftoken",  _TL_CSRF, domain="trendlyne.com")

    r = s.get(_FNO_PAGE_URL, timeout=15, allow_redirects=False)
    if r.status_code not in (301, 302):
        raise RuntimeError(f"Expected redirect from Trendlyne, got HTTP {r.status_code}")

    loc = r.headers.get("Location", "")
    if "amazonaws" in loc:
        return loc                        # direct S3 presigned URL
    if "officeapps" in loc:              # wrapped in Office viewer
        parsed = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)
        return parsed.get("src", [loc])[0]
    if "login" in loc:
        raise RuntimeError("Trendlyne session expired — update TRENDLYNE_SESSION env var")
    return loc


def _download_excel() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Download the F&O Excel and return (fno_data_df, fno_stocks_df)."""
    try:
        import openpyxl  # noqa: F401 — checked here for a clear error message
    except ImportError:
        raise ImportError("openpyxl is required: pip install openpyxl")

    url = _get_presigned_url()
    logger.info("Downloading Trendlyne F&O Excel from S3 …")
    r = requests.get(url, timeout=60)
    r.raise_for_status()

    wb_bytes = io.BytesIO(r.content)
    fno_data   = pd.read_excel(wb_bytes, sheet_name="F&O Data",   engine="openpyxl")
    wb_bytes.seek(0)
    fno_stocks = pd.read_excel(wb_bytes, sheet_name="F&O Stocks", engine="openpyxl")

    logger.info(
        "Trendlyne F&O downloaded: %d contracts, %d stocks",
        len(fno_data), len(fno_stocks),
    )
    return fno_data, fno_stocks


# ──────────────────────────────────────────────────────────────────────────────
# Per-symbol metric computation (called once at download time for all stocks)
# ──────────────────────────────────────────────────────────────────────────────

def _compute_metrics_for_symbol(
    symbol: str,
    sym_data: pd.DataFrame,
    sr: pd.Series,
) -> dict:
    """
    Compute all option metrics for a single symbol from the raw DataFrames.
    Called once per stock at download time; results are stored in the lean cache.
    """
    pcr_oi     = sr.get("FnO PCR OI Put to call open interest ratio")
    pcr_vol    = sr.get("FnO PCR Put to call Volume ratio")
    ann_vol    = sr.get("Annualized Volatility")
    oi_chg     = sr.get("FnO Total Open Interest change %")
    mwpl       = sr.get("FnO Marketwide Position Limit %")
    spot_price = sr.get("Current Price")

    lot_size = None
    if "LOT SIZE" in sym_data.columns:
        ls_vals = sym_data["LOT SIZE"].dropna()
        if not ls_vals.empty:
            lot_size = ls_vals.iloc[0]

    # Build-up signal from nearest-expiry future
    futures = sym_data[sym_data["OPTION TYPE"] == "FUTURE"].copy()
    buildup = None
    if not futures.empty:
        futures = futures.sort_values("EXPIRY")
        buildup = str(futures.iloc[0].get("BUILD UP", ""))

    # ATM IV, IV skew, max pain — options only
    options = sym_data[sym_data["OPTION TYPE"].isin(["CE", "PE"])].copy()
    atm_iv = iv_skew = max_pain = None

    if not options.empty and spot_price:
        nearest_expiry = options["EXPIRY"].min()
        exp_opts = options[options["EXPIRY"] == nearest_expiry].copy()

        # ATM strike
        exp_opts = exp_opts.copy()
        exp_opts["dist"] = (exp_opts["STRIKE PRICE"] - spot_price).abs()
        atm_strike = exp_opts.loc[exp_opts["dist"].idxmin(), "STRIKE PRICE"]
        atm_rows = exp_opts[exp_opts["STRIKE PRICE"] == atm_strike]
        iv_vals = atm_rows["IV"].dropna()
        atm_iv = float(iv_vals.mean()) if not iv_vals.empty else None

        # IV skew — OTM put (strike ~95% of spot) vs OTM call (strike ~105% of spot)
        try:
            otm_put_strikes  = exp_opts[exp_opts["OPTION TYPE"] == "PE"]["STRIKE PRICE"]
            otm_call_strikes = exp_opts[exp_opts["OPTION TYPE"] == "CE"]["STRIKE PRICE"]
            if not otm_put_strikes.empty and not otm_call_strikes.empty:
                put_95   = otm_put_strikes.iloc[
                    (otm_put_strikes - spot_price * 0.95).abs().argsort().iloc[0]
                ]
                call_105 = otm_call_strikes.iloc[
                    (otm_call_strikes - spot_price * 1.05).abs().argsort().iloc[0]
                ]
                put_iv  = exp_opts[
                    (exp_opts["STRIKE PRICE"] == put_95)  & (exp_opts["OPTION TYPE"] == "PE")
                ]["IV"].dropna()
                call_iv = exp_opts[
                    (exp_opts["STRIKE PRICE"] == call_105) & (exp_opts["OPTION TYPE"] == "CE")
                ]["IV"].dropna()
                if not put_iv.empty and not call_iv.empty:
                    iv_skew = float(put_iv.iloc[0]) - float(call_iv.iloc[0])
        except Exception:
            pass

        # Max pain — strike where total OI loss is maximised for option writers
        try:
            strikes = exp_opts["STRIKE PRICE"].dropna().unique()
            pain: dict = {}
            for s in strikes:
                calls_above = exp_opts[
                    (exp_opts["OPTION TYPE"] == "CE") & (exp_opts["STRIKE PRICE"] <= s)
                ]["OI"].sum()
                puts_below  = exp_opts[
                    (exp_opts["OPTION TYPE"] == "PE") & (exp_opts["STRIKE PRICE"] >= s)
                ]["OI"].sum()
                pain[s] = calls_above + puts_below
            if pain:
                max_pain = max(pain, key=pain.get)
        except Exception:
            pass

    return {
        "pcr":           float(pcr_oi)     if pcr_oi     is not None else None,
        "pcr_volume":    float(pcr_vol)    if pcr_vol    is not None else None,
        "atm_iv":        atm_iv,
        "iv_skew":       iv_skew,
        "max_pain":      max_pain,
        "ann_vol":       float(ann_vol)    if ann_vol    is not None else None,
        "buildup":       buildup,
        "oi_change_pct": float(oi_chg)    if oi_chg     is not None else None,
        "mwpl_pct":      float(mwpl)      if mwpl       is not None else None,
        "lot_size":      int(lot_size)    if lot_size   is not None else None,
        "spot":          float(spot_price) if spot_price is not None else None,
        "source":        "trendlyne_fno",
        "error":         None,
    }


def _build_compiled_cache(fno_data: pd.DataFrame, fno_stocks: pd.DataFrame) -> dict:
    """
    Process raw DataFrames into compact per-symbol dicts + universe list.
    Returns {"metrics": {symbol: dict}, "universe": [list[dict]]}.

    Intentionally receives DataFrames by value so the caller can del them
    after this function returns (the compiled output holds no DataFrame refs).
    """
    metrics: dict[str, dict] = {}
    universe: list[dict] = []

    for _, sr in fno_stocks.iterrows():
        symbol = sr.get("NSE code", "")
        if not symbol:
            continue

        universe.append({
            "symbol":   symbol,
            "name":     sr.get("Stock Name", ""),
            "bse_code": str(sr.get("BSE code", "")),
            "isin":     sr.get("ISIN", ""),
            "price":    sr.get("Current Price"),
            "industry": sr.get("Industry Name", ""),
            "ann_vol":  sr.get("Annualized Volatility"),
        })

        sym_data = fno_data[fno_data["SYMBOL"] == symbol]
        try:
            metrics[symbol] = _compute_metrics_for_symbol(symbol, sym_data, sr)
        except Exception as exc:
            logger.warning("_build_compiled_cache: failed for %s: %s", symbol, exc)
            metrics[symbol] = {
                "pcr": None, "pcr_volume": None, "atm_iv": None, "iv_skew": None,
                "max_pain": None, "ann_vol": None, "buildup": None,
                "oi_change_pct": None, "mwpl_pct": None, "lot_size": None,
                "spot": None, "source": "trendlyne_fno",
                "error": str(exc),
            }

    logger.info(
        "Trendlyne F&O compiled: %d symbols processed, cache ready",
        len(metrics),
    )
    return {"metrics": metrics, "universe": universe}


# ──────────────────────────────────────────────────────────────────────────────
# Cache management
# ──────────────────────────────────────────────────────────────────────────────

def _load(force: bool = False) -> dict:
    """
    Return the compiled cache dict ({"metrics": ..., "universe": ...}).
    Downloads and processes the Excel if cache is stale, then immediately
    frees the raw DataFrames to keep memory usage low.
    """
    global _cache
    if not force and _cache.get("ts") and time.time() - _cache["ts"] < _CACHE_TTL:
        return _cache

    fno_data, fno_stocks = _download_excel()
    try:
        compiled = _build_compiled_cache(fno_data, fno_stocks)
    finally:
        # Always delete the raw DataFrames, even if compilation raised an error.
        # This is the key memory-cleanup step: raw DataFrames can be 20–50 MB;
        # the compiled dict is < 1 MB.
        del fno_data, fno_stocks
        gc.collect()
        logger.debug("Trendlyne F&O raw DataFrames freed from memory")

    _cache = {**compiled, "ts": time.time()}
    return _cache


def clear_cache() -> None:
    """Explicitly free the in-process cache (useful after pipeline completes)."""
    global _cache
    _cache = {}
    gc.collect()
    logger.info("Trendlyne F&O cache cleared")


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def get_fno_universe() -> list[dict]:
    """
    Return list of all 219 F&O stocks with key metadata.
    Useful for filtering the discovery universe to only F&O stocks.
    """
    try:
        cache = _load()
        return cache.get("universe", [])
    except Exception as e:
        logger.warning("get_fno_universe failed: %s", e)
        return []


def get_option_metrics(symbol: str) -> dict:
    """
    Return option metrics for a given NSE symbol.
    Compatible with data/options_fetcher.py get_option_metrics() signature.

    Returns dict with keys:
      pcr          — put/call OI ratio
      pcr_volume   — put/call volume ratio
      atm_iv       — ATM implied volatility (nearest strike to spot)
      iv_skew      — OTM put IV minus OTM call IV (25-delta proxy)
      max_pain     — strike with max pain (max OI on both sides)
      ann_vol      — annualised historical volatility
      buildup      — dominant build-up signal for nearest expiry future
      oi_change_pct— total OI change %
      mwpl_pct     — market-wide position limit utilisation %
      lot_size     — contract lot size
      spot         — current spot price
      source       — "trendlyne_fno"
      error        — error string if failed, else None
    """
    empty = {
        "pcr": None, "pcr_volume": None, "atm_iv": None, "iv_skew": None,
        "max_pain": None, "ann_vol": None, "buildup": None,
        "oi_change_pct": None, "mwpl_pct": None, "lot_size": None,
        "spot": None, "source": "trendlyne_fno", "error": None,
    }
    try:
        cache = _load()
        metrics = cache.get("metrics", {})
        if symbol not in metrics:
            empty["error"] = f"{symbol} not in F&O universe"
            return empty
        return metrics[symbol]
    except Exception as e:
        logger.error("get_option_metrics(%s) failed: %s", symbol, e)
        empty["error"] = str(e)
        return empty


def get_buildup_signals(min_oi_change_pct: float = 5.0) -> list[dict]:
    """
    Return all stocks with significant OI build-up today.
    Useful for the institutional/discovery agents.

    Args:
        min_oi_change_pct: minimum OI change % to include (default 5%)
    """
    try:
        cache = _load()
        universe = cache.get("universe", [])
        metrics  = cache.get("metrics", {})

        result = []
        for stock in universe:
            symbol = stock.get("symbol", "")
            m = metrics.get(symbol, {})
            oi_chg = m.get("oi_change_pct")
            if oi_chg is None:
                continue
            try:
                oi_chg = float(oi_chg)
            except (TypeError, ValueError):
                continue
            if abs(oi_chg) >= min_oi_change_pct:
                result.append({
                    "symbol":        symbol,
                    "name":          stock.get("name", ""),
                    "oi_change_pct": oi_chg,
                    "pcr_oi":        m.get("pcr"),
                    "pcr_vol":       m.get("pcr_volume"),
                    "mwpl_pct":      m.get("mwpl_pct"),
                    "price":         stock.get("price"),
                })
        return sorted(result, key=lambda x: abs(x["oi_change_pct"]), reverse=True)
    except Exception as e:
        logger.warning("get_buildup_signals failed: %s", e)
        return []


# ──────────────────────────────────────────────────────────────────────────────
# CLI smoke test
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)

    print("\n=== F&O Universe (first 5) ===")
    universe = get_fno_universe()
    print(f"Total F&O stocks: {len(universe)}")
    for s in universe[:5]:
        print(" ", s)

    print("\n=== Option Metrics: RELIANCE ===")
    m = get_option_metrics("RELIANCE")
    print(json.dumps(m, indent=2, default=str))

    print("\n=== Option Metrics: HDFCBANK ===")
    m2 = get_option_metrics("HDFCBANK")
    print(json.dumps(m2, indent=2, default=str))

    print("\n=== Top OI Build-up Signals (OI chg > 10%) ===")
    signals = get_buildup_signals(min_oi_change_pct=10.0)
    for sig in signals[:10]:
        print(f"  {sig['symbol']:15s}  OI chg: {sig['oi_change_pct']:+.1f}%  PCR: {sig['pcr_oi']}  MWPL: {sig['mwpl_pct']}%")

    print("\n=== Clearing cache after use ===")
    clear_cache()
    print("Cache cleared.")
