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
Cache: in-memory for the process lifetime (one download per worker run).
"""

from __future__ import annotations

import io
import logging
import os
import time
import urllib.parse
from functools import lru_cache
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
_cache: dict = {}          # {"fno_data": df, "fno_stocks": df, "ts": float}
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


def _load(force: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return cached (fno_data, fno_stocks), downloading if stale."""
    global _cache
    if not force and _cache.get("ts") and time.time() - _cache["ts"] < _CACHE_TTL:
        return _cache["fno_data"], _cache["fno_stocks"]

    fno_data, fno_stocks = _download_excel()
    _cache = {"fno_data": fno_data, "fno_stocks": fno_stocks, "ts": time.time()}
    return fno_data, fno_stocks


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def get_fno_universe() -> list[dict]:
    """
    Return list of all 219 F&O stocks with key metadata.
    Useful for filtering the discovery universe to only F&O stocks.
    """
    try:
        _, fno_stocks = _load()
        result = []
        for _, row in fno_stocks.iterrows():
            result.append({
                "symbol":     row.get("NSE code", ""),
                "name":       row.get("Stock Name", ""),
                "bse_code":   str(row.get("BSE code", "")),
                "isin":       row.get("ISIN", ""),
                "price":      row.get("Current Price"),
                "industry":   row.get("Industry Name", ""),
                "ann_vol":    row.get("Annualized Volatility"),
            })
        return result
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
        fno_data, fno_stocks = _load()

        # ── per-stock summary ──────────────────────────────────────────────
        stock_row = fno_stocks[fno_stocks["NSE code"] == symbol]
        if stock_row.empty:
            empty["error"] = f"{symbol} not in F&O universe"
            return empty

        sr = stock_row.iloc[0]
        pcr_oi     = sr.get("FnO PCR OI Put to call open interest ratio")
        pcr_vol    = sr.get("FnO PCR Put to call Volume ratio")
        ann_vol    = sr.get("Annualized Volatility")
        oi_chg     = sr.get("FnO Total Open Interest change %")
        mwpl       = sr.get("FnO Marketwide Position Limit %")
        spot_price = sr.get("Current Price")

        # ── contract-level data ────────────────────────────────────────────
        sym_data = fno_data[fno_data["SYMBOL"] == symbol].copy()
        if sym_data.empty:
            empty.update({
                "pcr": pcr_oi, "pcr_volume": pcr_vol,
                "ann_vol": ann_vol, "oi_change_pct": oi_chg,
                "mwpl_pct": mwpl, "spot": spot_price,
            })
            return empty

        # Lot size (from any row for this symbol)
        lot_size = sym_data["LOT SIZE"].dropna().iloc[0] if "LOT SIZE" in sym_data else None

        # Build-up signal from nearest-expiry future
        futures = sym_data[sym_data["OPTION TYPE"] == "FUTURE"].copy()
        buildup = None
        if not futures.empty:
            futures = futures.sort_values("EXPIRY")
            buildup = str(futures.iloc[0].get("BUILD UP", ""))

        # ATM IV — find options nearest to spot, nearest expiry
        options = sym_data[sym_data["OPTION TYPE"].isin(["CE", "PE"])].copy()
        atm_iv = None
        iv_skew = None
        max_pain = None

        if not options.empty and spot_price:
            nearest_expiry = options["EXPIRY"].min()
            exp_opts = options[options["EXPIRY"] == nearest_expiry].copy()

            # ATM strike
            exp_opts["dist"] = (exp_opts["STRIKE PRICE"] - spot_price).abs()
            atm_strike = exp_opts.loc[exp_opts["dist"].idxmin(), "STRIKE PRICE"]
            atm_rows = exp_opts[exp_opts["STRIKE PRICE"] == atm_strike]
            iv_vals = atm_rows["IV"].dropna()
            atm_iv = float(iv_vals.mean()) if not iv_vals.empty else None

            # IV skew — OTM put (strike ~95% of spot) vs OTM call (strike ~105% of spot)
            try:
                otm_put_strike  = exp_opts[exp_opts["OPTION TYPE"] == "PE"]["STRIKE PRICE"]
                otm_call_strike = exp_opts[exp_opts["OPTION TYPE"] == "CE"]["STRIKE PRICE"]
                if not otm_put_strike.empty and not otm_call_strike.empty:
                    put_95  = otm_put_strike.iloc[(otm_put_strike - spot_price * 0.95).abs().argsort().iloc[0]]
                    call_105 = otm_call_strike.iloc[(otm_call_strike - spot_price * 1.05).abs().argsort().iloc[0]]
                    put_iv  = exp_opts[(exp_opts["STRIKE PRICE"] == put_95)  & (exp_opts["OPTION TYPE"] == "PE")]["IV"].dropna()
                    call_iv = exp_opts[(exp_opts["STRIKE PRICE"] == call_105) & (exp_opts["OPTION TYPE"] == "CE")]["IV"].dropna()
                    if not put_iv.empty and not call_iv.empty:
                        iv_skew = float(put_iv.iloc[0]) - float(call_iv.iloc[0])
            except Exception:
                pass

            # Max pain — strike where total OI loss is maximised for option writers
            try:
                strikes = exp_opts["STRIKE PRICE"].dropna().unique()
                pain = {}
                for s in strikes:
                    calls_above = exp_opts[(exp_opts["OPTION TYPE"] == "CE") & (exp_opts["STRIKE PRICE"] <= s)]["OI"].sum()
                    puts_below  = exp_opts[(exp_opts["OPTION TYPE"] == "PE") & (exp_opts["STRIKE PRICE"] >= s)]["OI"].sum()
                    pain[s] = calls_above + puts_below
                if pain:
                    max_pain = max(pain, key=pain.get)
            except Exception:
                pass

        return {
            "pcr":           float(pcr_oi)  if pcr_oi  is not None else None,
            "pcr_volume":    float(pcr_vol) if pcr_vol is not None else None,
            "atm_iv":        atm_iv,
            "iv_skew":       iv_skew,
            "max_pain":      max_pain,
            "ann_vol":       float(ann_vol) if ann_vol is not None else None,
            "buildup":       buildup,
            "oi_change_pct": float(oi_chg)  if oi_chg  is not None else None,
            "mwpl_pct":      float(mwpl)    if mwpl    is not None else None,
            "lot_size":      int(lot_size)  if lot_size is not None else None,
            "spot":          float(spot_price) if spot_price is not None else None,
            "source":        "trendlyne_fno",
            "error":         None,
        }

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
        _, fno_stocks = _load()
        result = []
        for _, row in fno_stocks.iterrows():
            oi_chg = row.get("FnO Total Open Interest change %")
            if oi_chg is None:
                continue
            try:
                oi_chg = float(oi_chg)
            except (TypeError, ValueError):
                continue
            if abs(oi_chg) >= min_oi_change_pct:
                result.append({
                    "symbol":        row.get("NSE code", ""),
                    "name":          row.get("Stock Name", ""),
                    "oi_change_pct": oi_chg,
                    "pcr_oi":        row.get("FnO PCR OI Put to call open interest ratio"),
                    "pcr_vol":       row.get("FnO PCR Put to call Volume ratio"),
                    "mwpl_pct":      row.get("FnO Marketwide Position Limit %"),
                    "price":         row.get("Current Price"),
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
