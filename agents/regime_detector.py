"""
agents/regime_detector.py — Market Regime Detection Engine
===========================================================
Classifies the current Indian equity market regime using five independent
indicators and writes the result to the `market_regime` Supabase table.

The orchestrator reads this each morning and applies regime-specific weight
multipliers to the composite score, so signals are always interpreted in the
correct macro context.

Setup (run once in Supabase SQL Editor):
-----------------------------------------
  CREATE TABLE IF NOT EXISTS market_regime (
      id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      regime_date     DATE UNIQUE NOT NULL,
      regime          TEXT NOT NULL,   -- BULL / BEAR / SIDEWAYS / HIGH_VOLATILITY
      confidence      NUMERIC,         -- 0-100
      nifty_trend     TEXT,            -- UPTREND / DOWNTREND / SIDEWAYS
      vix_state       TEXT,            -- CALM / NORMAL / ELEVATED / STRESSED
      fii_trend       TEXT,            -- NET_BUYER / NET_SELLER / NEUTRAL
      breadth_state   TEXT,            -- BROAD_ADVANCE / BROAD_DECLINE / MIXED
      momentum_state  TEXT,            -- OVERBOUGHT / NEUTRAL / OVERSOLD
      raw_signals     JSONB,
      created_at      TIMESTAMPTZ DEFAULT now()
  );
  GRANT ALL ON market_regime TO service_role;

Usage
-----
  python -m agents.regime_detector          # detect today's regime + upsert to DB
  python -m agents.regime_detector --dry    # print without DB write
  python -m agents.regime_detector --date 2025-01-15   # historical mode
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import yfinance as yf
import pandas as pd
import numpy as np

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ─────────────────────────────────────────────────────────────────────────────
# Symbols
# ─────────────────────────────────────────────────────────────────────────────

NIFTY_SYMBOL     = "^NSEI"
VIX_SYMBOL       = "^INDIAVIX"
MIDCAP_SYMBOL    = "^CNXMDCP50"   # NIFTY Midcap 50 (^CNXMIDCAP delisted on Yahoo Finance)
HISTORY_PERIOD   = "1y"           # enough for EMA-200, RSI-14

# FII flow thresholds (₹ Crore, 10-day cumulative)
FII_BUYER_THRESHOLD  =  5_000.0
FII_SELLER_THRESHOLD = -5_000.0

# VIX thresholds
VIX_CALM     = 13.0
VIX_NORMAL   = 18.0
VIX_ELEVATED = 25.0

# Breadth thresholds (midcap vs nifty 20d performance)
BREADTH_ADVANCE_PCT =  2.0   # midcap outperforms NIFTY by >2%
BREADTH_DECLINE_PCT = -2.0   # midcap underperforms NIFTY by >2%

# RSI thresholds
RSI_OVERBOUGHT = 65.0
RSI_OVERSOLD   = 40.0


# ─────────────────────────────────────────────────────────────────────────────
# Supabase helper
# ─────────────────────────────────────────────────────────────────────────────

def _supabase():
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_KEY", "")
    if not (url and key):
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception as exc:
        log.warning("Supabase init failed: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Technical helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi(series: pd.Series, period: int = 14) -> float | None:
    """Compute RSI for the latest data point."""
    if len(series) < period + 1:
        return None
    delta = series.diff().dropna()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = gain.rolling(period).mean()
    avg_l = loss.rolling(period).mean()
    # When avg_loss == 0 all gains → RSI = 100 (fully overbought)
    last_g = float(avg_g.iloc[-1])
    last_l = float(avg_l.iloc[-1])
    if pd.isna(last_g) or pd.isna(last_l):
        return None
    if last_l == 0:
        return 100.0 if last_g > 0 else 50.0
    rs  = last_g / last_l
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return round(rsi, 2)


def _fetch_ohlcv(symbol: str) -> pd.DataFrame | None:
    """Return daily OHLCV for last 1 year. None on failure."""
    try:
        hist = yf.Ticker(symbol).history(period=HISTORY_PERIOD, auto_adjust=True)
        if hist.empty or len(hist) < 20:
            return None
        return hist
    except Exception as exc:
        log.warning("OHLCV fetch failed for %s: %s", symbol, exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Five indicators
# ─────────────────────────────────────────────────────────────────────────────

def _nifty_trend(hist: pd.DataFrame | None) -> tuple[str, dict]:
    """
    Price vs EMA-50 vs EMA-200.
    Returns (trend_label, raw_signals_dict).
    """
    if hist is None or len(hist) < 200:
        return "SIDEWAYS", {"error": "insufficient_history"}

    close  = hist["Close"]
    ema50  = _ema(close, 50).iloc[-1]
    ema200 = _ema(close, 200).iloc[-1]
    price  = float(close.iloc[-1])

    raw = {
        "price":   round(price, 2),
        "ema50":   round(float(ema50), 2),
        "ema200":  round(float(ema200), 2),
    }

    if price > ema50 > ema200:
        return "UPTREND", raw
    elif price < ema50 < ema200:
        return "DOWNTREND", raw
    else:
        return "SIDEWAYS", raw


def _vix_state(hist: pd.DataFrame | None) -> tuple[str, dict]:
    """
    Current India VIX level → CALM / NORMAL / ELEVATED / STRESSED.
    """
    if hist is None or hist.empty:
        return "NORMAL", {"error": "vix_unavailable"}

    vix = float(hist["Close"].iloc[-1])
    raw = {"vix": round(vix, 2)}

    if vix < VIX_CALM:
        return "CALM", raw
    elif vix < VIX_NORMAL:
        return "NORMAL", raw
    elif vix < VIX_ELEVATED:
        return "ELEVATED", raw
    else:
        return "STRESSED", raw


def _fii_trend() -> tuple[str, dict]:
    """
    10-day cumulative FII net flow from institutional_flows Supabase table.
    Returns (trend_label, raw_signals_dict).
    """
    client = _supabase()
    if not client:
        return "NEUTRAL", {"error": "supabase_unavailable"}

    try:
        cutoff = str(date.today() - timedelta(days=14))
        rows   = (
            client
            .table("institutional_flows")
            .select("fii_net, session_date")
            .gte("session_date", cutoff)
            .order("session_date", desc=True)
            .limit(10)
            .execute()
            .data or []
        )
        if not rows:
            return "NEUTRAL", {"error": "no_fii_data"}

        net_10d = sum(float(r.get("fii_net") or 0) for r in rows)
        raw     = {
            "fii_net_10d_cr": round(net_10d, 2),
            "days_available": len(rows),
        }
        if net_10d > FII_BUYER_THRESHOLD:
            return "NET_BUYER", raw
        elif net_10d < FII_SELLER_THRESHOLD:
            return "NET_SELLER", raw
        else:
            return "NEUTRAL", raw
    except Exception as exc:
        log.warning("FII trend fetch failed: %s", exc)
        return "NEUTRAL", {"error": str(exc)}


def _breadth_state(nifty_hist: pd.DataFrame | None, midcap_hist: pd.DataFrame | None) -> tuple[str, dict]:
    """
    NIFTY Midcap 100 vs NIFTY 50 relative 20-day performance.
    Proxy for market breadth (is the advance broad or narrow?).
    """
    if nifty_hist is None or midcap_hist is None or len(nifty_hist) < 21 or len(midcap_hist) < 21:
        return "MIXED", {"error": "insufficient_history"}

    nifty_ret  = float(nifty_hist["Close"].iloc[-1]  / nifty_hist["Close"].iloc[-21]  - 1) * 100
    midcap_ret = float(midcap_hist["Close"].iloc[-1] / midcap_hist["Close"].iloc[-21] - 1) * 100
    rel_perf   = midcap_ret - nifty_ret

    raw = {
        "nifty_20d_ret_pct":  round(nifty_ret, 2),
        "midcap_20d_ret_pct": round(midcap_ret, 2),
        "relative_pct":       round(rel_perf, 2),
    }
    if rel_perf > BREADTH_ADVANCE_PCT:
        return "BROAD_ADVANCE", raw
    elif rel_perf < BREADTH_DECLINE_PCT:
        return "BROAD_DECLINE", raw
    else:
        return "MIXED", raw


def _momentum_state(hist: pd.DataFrame | None) -> tuple[str, dict]:
    """
    NIFTY 50 RSI (14-day).
    """
    if hist is None or len(hist) < 20:
        return "NEUTRAL", {"error": "insufficient_history"}

    rsi = _rsi(hist["Close"])
    raw = {"rsi14": round(rsi, 2) if rsi is not None else None}

    if rsi is None:
        return "NEUTRAL", raw
    elif rsi > RSI_OVERBOUGHT:
        return "OVERBOUGHT", raw
    elif rsi < RSI_OVERSOLD:
        return "OVERSOLD", raw
    else:
        return "NEUTRAL", raw


# ─────────────────────────────────────────────────────────────────────────────
# Composite regime classification
# ─────────────────────────────────────────────────────────────────────────────

def _classify_regime(
    nifty_trend:   str,
    vix_state:     str,
    fii_trend:     str,
    breadth_state: str,
    momentum_state: str,
) -> tuple[str, int]:
    """
    Map five indicator labels → one regime label + confidence score (0-100).

    Classification rules (in priority order):
      HIGH_VOLATILITY: VIX STRESSED, OR (VIX ELEVATED + FII NET_SELLER + DOWNTREND)
      BEAR:            DOWNTREND + (VIX ELEVATED or STRESSED) + FII NET_SELLER
      BULL:            UPTREND + (VIX CALM or NORMAL) + FII (NET_BUYER or NEUTRAL)
      SIDEWAYS:        Everything else

    Confidence = count of indicators that agree with the composite label × 20.
    """
    # ── HIGH_VOLATILITY ───────────────────────────────────────────────────────
    if vix_state == "STRESSED":
        regime = "HIGH_VOLATILITY"
        agreeing = 1
        if fii_trend  == "NET_SELLER":  agreeing += 1
        if nifty_trend == "DOWNTREND":  agreeing += 1
        if breadth_state == "BROAD_DECLINE": agreeing += 1
        if momentum_state == "OVERSOLD": agreeing += 1
        return regime, agreeing * 20

    if (vix_state == "ELEVATED"
            and fii_trend == "NET_SELLER"
            and nifty_trend == "DOWNTREND"):
        regime = "HIGH_VOLATILITY"
        agreeing = 3
        if breadth_state == "BROAD_DECLINE": agreeing += 1
        if momentum_state in ("OVERSOLD", "NEUTRAL"): agreeing += 1
        return regime, agreeing * 20

    # ── BEAR ──────────────────────────────────────────────────────────────────
    # Distinct from HIGH_VOLATILITY: BEAR does not require the full triple
    # (ELEVATED + NET_SELLER + DOWNTREND) that triggers the panic-regime.
    # FII can be NEUTRAL or NET_SELLER (not NET_BUYER).
    if (nifty_trend == "DOWNTREND"
            and vix_state in ("ELEVATED", "STRESSED")
            and fii_trend in ("NET_SELLER", "NEUTRAL")):
        regime = "BEAR"
        agreeing = 2  # downtrend + elevated VIX
        if fii_trend == "NET_SELLER":        agreeing += 1
        if breadth_state == "BROAD_DECLINE": agreeing += 1
        if momentum_state == "OVERSOLD":     agreeing += 1
        return regime, agreeing * 20

    # ── BULL ──────────────────────────────────────────────────────────────────
    if (nifty_trend == "UPTREND"
            and vix_state in ("CALM", "NORMAL")
            and fii_trend in ("NET_BUYER", "NEUTRAL")):
        regime = "BULL"
        agreeing = 3
        if breadth_state == "BROAD_ADVANCE": agreeing += 1
        if momentum_state in ("NEUTRAL", "OVERBOUGHT"): agreeing += 1
        return regime, agreeing * 20

    # ── SIDEWAYS (default) ───────────────────────────────────────────────────
    regime   = "SIDEWAYS"
    agreeing = 1  # default baseline
    if nifty_trend   == "SIDEWAYS":     agreeing += 1
    if vix_state     == "NORMAL":       agreeing += 1
    if fii_trend     == "NEUTRAL":      agreeing += 1
    if breadth_state == "MIXED":        agreeing += 1
    if momentum_state == "NEUTRAL":     agreeing += 1
    return regime, min(agreeing * 20, 100)


# ─────────────────────────────────────────────────────────────────────────────
# Regime multipliers applied in orchestrator._composite_score()
# ─────────────────────────────────────────────────────────────────────────────

REGIME_WEIGHT_MULTIPLIERS: dict[str, dict[str, float]] = {
    "BULL": {
        "technical":    1.2,
        "fundamental":  1.0,
        "macro":        0.8,
        "institutional":1.0,
        "sentiment":    1.0,
        "historical_rag":1.0,
        "commodities":  1.0,
    },
    "BEAR": {
        "technical":    0.6,
        "fundamental":  1.0,
        "macro":        1.5,
        "institutional":1.5,
        "sentiment":    0.9,
        "historical_rag":1.0,
        "commodities":  1.0,
    },
    "HIGH_VOLATILITY": {
        "technical":    0.4,
        "fundamental":  1.0,
        "macro":        2.0,
        "institutional":2.0,
        "sentiment":    0.8,
        "historical_rag":1.5,
        "commodities":  1.0,
    },
    "SIDEWAYS": {
        "technical":    1.0,
        "fundamental":  1.3,
        "macro":        1.0,
        "institutional":1.0,
        "sentiment":    1.0,
        "historical_rag":1.0,
        "commodities":  1.0,
    },
}


def apply_regime_multipliers(
    weights: dict[str, float],
    regime:  str,
) -> dict[str, float]:
    """
    Apply regime-specific multipliers to base agent weights and re-normalise.
    Returns a new weights dict (sum ≈ 1.0).
    """
    multipliers = REGIME_WEIGHT_MULTIPLIERS.get(regime, {})
    if not multipliers:
        return weights

    adjusted = {
        agent: weight * multipliers.get(agent, 1.0)
        for agent, weight in weights.items()
    }
    total = sum(adjusted.values())
    if total == 0:
        return weights
    return {agent: round(w / total, 6) for agent, w in adjusted.items()}


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def detect_regime(run_date: date | None = None, dry_run: bool = False) -> dict:
    """
    Detect today's market regime and (unless dry_run) upsert to market_regime.

    Returns a dict with:
      regime, confidence, nifty_trend, vix_state, fii_trend,
      breadth_state, momentum_state, raw_signals, regime_date
    """
    if run_date is None:
        run_date = date.today()

    log.info("=== Regime Detector: %s ===", run_date)

    # ── Fetch price data in parallel ─────────────────────────────────────────
    log.info("Fetching NIFTY, VIX, Midcap price history...")
    nifty_hist  = _fetch_ohlcv(NIFTY_SYMBOL)
    vix_hist    = _fetch_ohlcv(VIX_SYMBOL)
    midcap_hist = _fetch_ohlcv(MIDCAP_SYMBOL)

    # ── Compute five indicators ───────────────────────────────────────────────
    nifty_trend,   raw_nifty    = _nifty_trend(nifty_hist)
    vix_label,     raw_vix      = _vix_state(vix_hist)
    fii_label,     raw_fii      = _fii_trend()
    breadth_label, raw_breadth  = _breadth_state(nifty_hist, midcap_hist)
    momentum_label,raw_momentum = _momentum_state(nifty_hist)

    log.info(
        "Indicators: nifty_trend=%s vix=%s fii=%s breadth=%s momentum=%s",
        nifty_trend, vix_label, fii_label, breadth_label, momentum_label,
    )

    # ── Composite classification ──────────────────────────────────────────────
    regime, confidence = _classify_regime(
        nifty_trend, vix_label, fii_label, breadth_label, momentum_label
    )

    log.info(
        "Regime: %s (confidence=%d%%)",
        regime, confidence,
    )

    raw_signals = {
        "nifty":    raw_nifty,
        "vix":      raw_vix,
        "fii":      raw_fii,
        "breadth":  raw_breadth,
        "momentum": raw_momentum,
    }

    result = {
        "regime":         regime,
        "confidence":     confidence,
        "nifty_trend":    nifty_trend,
        "vix_state":      vix_label,
        "fii_trend":      fii_label,
        "breadth_state":  breadth_label,
        "momentum_state": momentum_label,
        "raw_signals":    raw_signals,
        "regime_date":    str(run_date),
    }

    # ── Upsert to Supabase ────────────────────────────────────────────────────
    if not dry_run:
        client = _supabase()
        if client:
            try:
                row = {k: v for k, v in result.items()}
                row["raw_signals"] = raw_signals  # already JSON-serialisable
                client.table("market_regime").upsert(
                    row, on_conflict="regime_date"
                ).execute()
                log.info("Regime upserted to market_regime table")
            except Exception as exc:
                log.warning("Failed to upsert market_regime: %s", exc)
        else:
            log.warning("Supabase unavailable — regime not persisted")
    else:
        log.info("[DRY RUN] Regime not persisted: %s confidence=%d%%", regime, confidence)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Load today's regime from Supabase (called by orchestrator)
# ─────────────────────────────────────────────────────────────────────────────

def load_current_regime() -> dict | None:
    """
    Load today's regime from the market_regime Supabase table.
    Falls back to yesterday if today hasn't been computed yet.
    Returns None if no data is available.
    """
    client = _supabase()
    if not client:
        return None
    try:
        cutoff = str(date.today() - timedelta(days=2))
        rows = (
            client
            .table("market_regime")
            .select("regime,confidence,nifty_trend,vix_state,fii_trend,breadth_state,momentum_state,regime_date")
            .gte("regime_date", cutoff)
            .order("regime_date", desc=True)
            .limit(1)
            .execute()
            .data or []
        )
        return rows[0] if rows else None
    except Exception as exc:
        log.warning("Failed to load current regime: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import logging as _logging
    from dotenv import load_dotenv
    load_dotenv()

    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    parser = argparse.ArgumentParser(description="Bharat Intelligence — Regime Detector")
    parser.add_argument("--dry",  action="store_true",  help="Print regime without DB write")
    parser.add_argument("--date", type=str, default=None, help="Run for specific date (YYYY-MM-DD)")
    args = parser.parse_args()

    run_date = date.fromisoformat(args.date) if args.date else None
    result   = detect_regime(run_date=run_date, dry_run=args.dry)

    print(f"\n{'='*55}")
    print(f"  MARKET REGIME: {result['regime']}  (confidence: {result['confidence']}%)")
    print(f"{'='*55}")
    print(f"  NIFTY trend  : {result['nifty_trend']}")
    print(f"  VIX state    : {result['vix_state']}  ({result['raw_signals']['vix'].get('vix','?')})")
    print(f"  FII trend    : {result['fii_trend']}  (10d net: {result['raw_signals']['fii'].get('fii_net_10d_cr','?')} Cr)")
    print(f"  Breadth      : {result['breadth_state']}  (midcap rel: {result['raw_signals']['breadth'].get('relative_pct','?')}%)")
    print(f"  Momentum     : {result['momentum_state']}  (RSI-14: {result['raw_signals']['momentum'].get('rsi14','?')})")
    print(f"{'='*55}\n")
