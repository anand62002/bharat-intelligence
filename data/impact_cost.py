"""
data/impact_cost.py — Market Impact Cost Estimator
====================================================
Estimates the impact cost (slippage) of trading a given value in a stock,
using yfinance intraday OHLCV data.

Impact cost is the % price moved vs mid-price when executing a market order
of a given size. NSE officially defines it as cost for a ₹1L portfolio —
we generalise to any trade_value.

Algorithm
---------
1. Fetch recent 5-day 5-min OHLCV bars (yfinance interval="5m").
2. Compute:
   - avg_daily_volume_inr  : median daily value traded (close × volume)
   - avg_spread_pct        : proxy spread = (high - low) / close  per bar, averaged
   - participation_rate    : trade_value / avg_daily_volume_inr
   - impact_cost_pct       : spread_pct/2 + 0.5 × √participation_rate × 100  (sqrt model)
3. Classify liquidity:
     HIGH      impact_cost < 0.1%  AND avg_daily_vol > ₹10 Cr
     MEDIUM    impact_cost < 0.3%  AND avg_daily_vol > ₹1 Cr
     LOW       impact_cost < 1.0%
     ILLIQUID  otherwise

Usage
-----
  from data.impact_cost import estimate_impact_cost
  result = estimate_impact_cost("RELIANCE", trade_value_inr=500_000)
  # {'symbol':'RELIANCE','impact_cost_pct':0.07,'liquidity_tier':'HIGH',
  #   'avg_daily_volume_inr':4.2e10,'avg_spread_pct':0.14,'participation_rate':0.000012,...}

Standalone
----------
  python -m data.impact_cost RELIANCE 500000
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ── thresholds ─────────────────────────────────────────────────────────────────
HIGH_VOL_INR     = 10_00_00_000   # ₹10 Cr daily
MEDIUM_VOL_INR   = 1_00_00_000    # ₹1 Cr  daily
ILLIQUID_COST    = 1.0            # 1% impact → illiquid
LOW_COST         = 0.3            # 0.3% → low
HIGH_COST        = 0.1            # 0.1% → high


def _to_yf(symbol: str) -> str:
    s = symbol.replace(".NS", "").replace(".BO", "").upper()
    return f"{s}.NS"


def estimate_impact_cost(symbol: str, trade_value_inr: float = 5_00_000) -> dict:
    """
    Estimate impact cost for `symbol` at the given trade size.

    Parameters
    ----------
    symbol          : NSE symbol (with or without .NS)
    trade_value_inr : trade size in INR (default ₹5 L)

    Returns
    -------
    dict with keys:
      symbol, impact_cost_pct, liquidity_tier, avg_daily_volume_inr,
      avg_spread_pct, participation_rate, data_days, source, error
    """
    plain = symbol.replace(".NS", "").replace(".BO", "").upper()

    def _err(msg: str) -> dict:
        return {
            "symbol":               plain,
            "impact_cost_pct":      None,
            "liquidity_tier":       "UNKNOWN",
            "avg_daily_volume_inr": None,
            "avg_spread_pct":       None,
            "participation_rate":   None,
            "data_days":            0,
            "source":               "error",
            "error":                msg,
        }

    try:
        import yfinance as yf
        import numpy as np

        ticker = yf.Ticker(_to_yf(plain))
        df = ticker.history(period="5d", interval="5m", auto_adjust=True)

        if df is None or df.empty:
            return _err("no intraday data")

        # ── compute per-bar spread proxy ───────────────────────────────────────
        df = df[df["Volume"] > 0].copy()
        if df.empty:
            return _err("no volume data")

        df["vwap_bar"]   = df["Close"]                     # mid ≈ close
        df["spread_pct"] = (df["High"] - df["Low"]) / df["Close"] * 100

        # ── daily aggregate ────────────────────────────────────────────────────
        df["value_inr"] = df["Close"] * df["Volume"]
        daily = df.groupby(df.index.date).agg(
            daily_value_inr=("value_inr", "sum"),
            spread_pct_avg=("spread_pct", "mean"),
        )

        if daily.empty:
            return _err("insufficient daily data")

        avg_daily_vol   = float(daily["daily_value_inr"].median())
        avg_spread_pct  = float(daily["spread_pct_avg"].mean())
        data_days       = len(daily)

        # ── impact cost model ──────────────────────────────────────────────────
        if avg_daily_vol <= 0:
            return _err("zero daily volume")

        participation     = trade_value_inr / avg_daily_vol
        # sqrt participation model (Almgren-Chriss simplified)
        impact_cost_pct   = (avg_spread_pct / 2.0) + 0.5 * (participation ** 0.5) * 100

        # ── liquidity tier ─────────────────────────────────────────────────────
        if impact_cost_pct < HIGH_COST and avg_daily_vol >= HIGH_VOL_INR:
            tier = "HIGH"
        elif impact_cost_pct < LOW_COST and avg_daily_vol >= MEDIUM_VOL_INR:
            tier = "MEDIUM"
        elif impact_cost_pct < ILLIQUID_COST:
            tier = "LOW"
        else:
            tier = "ILLIQUID"

        return {
            "symbol":               plain,
            "impact_cost_pct":      round(impact_cost_pct, 4),
            "liquidity_tier":       tier,
            "avg_daily_volume_inr": round(avg_daily_vol, 0),
            "avg_spread_pct":       round(avg_spread_pct, 4),
            "participation_rate":   round(participation, 6),
            "data_days":            data_days,
            "source":               "yfinance_5m",
            "error":                None,
        }

    except Exception as exc:
        log.debug("estimate_impact_cost(%s) failed: %s", plain, exc)
        return _err(str(exc))


# ── batch helper ───────────────────────────────────────────────────────────────

def batch_impact_cost(
    symbols: list[str],
    trade_value_inr: float = 5_00_000,
    max_workers: int = 8,
) -> dict[str, dict]:
    """Return {symbol: estimate_impact_cost(symbol)} for all symbols in parallel."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        fut_map = {pool.submit(estimate_impact_cost, s, trade_value_inr): s for s in symbols}
        for fut in as_completed(fut_map):
            sym = fut_map[fut]
            try:
                results[sym] = fut.result()
            except Exception as exc:
                results[sym] = {"symbol": sym, "liquidity_tier": "UNKNOWN", "error": str(exc)}
    return results


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    sym   = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    value = float(sys.argv[2]) if len(sys.argv) > 2 else 500_000
    res   = estimate_impact_cost(sym, value)
    print(json.dumps(res, indent=2))
