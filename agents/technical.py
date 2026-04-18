"""
agents/technical.py — Technical Analysis Agent
Analyses NSE/BSE stocks for medium-to-long term (2–8 month) opportunities.

Entry point: analyse(symbol) -> dict
"""

import logging
import os
import sys
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

# Ensure project root on path so sibling packages resolve regardless of cwd
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from data.fetchers import get_ohlcv  # noqa: E402

log = logging.getLogger(__name__)
AGENT_NAME = "technical"

# ──────────────────────────────────────────────────────────────────────────────
# Indicator computations  (pure pandas/numpy — no TA-Lib dependency)
# ──────────────────────────────────────────────────────────────────────────────

def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.where(avg_loss != 0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    # When no losses exist in the period, RSI is definitionally 100
    return rsi.where(avg_loss != 0, 100.0)


def _macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema_fast = close.ewm(span=fast, min_periods=fast).mean()
    ema_slow = close.ewm(span=slow, min_periods=slow).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, min_periods=signal).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _ema(close: pd.Series, span: int) -> pd.Series:
    return close.ewm(span=span, min_periods=span).mean()


def _adx(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> tuple[pd.Series, pd.Series, pd.Series]:
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)

    up_move = high.diff()
    down_move = -low.diff()
    dm_plus = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    dm_minus = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    atr = tr.ewm(span=period, min_periods=period).mean()
    safe_atr = atr.replace(0, np.nan)
    di_plus = 100 * dm_plus.ewm(span=period, min_periods=period).mean() / safe_atr
    di_minus = 100 * dm_minus.ewm(span=period, min_periods=period).mean() / safe_atr

    denom = (di_plus + di_minus).replace(0, np.nan)
    dx = 100 * (di_plus - di_minus).abs() / denom
    adx = dx.ewm(span=period, min_periods=period).mean()
    return adx, di_plus, di_minus


def _supertrend(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 10,
    multiplier: float = 3.0,
) -> tuple[pd.Series, pd.Series]:
    """
    Returns (supertrend_series, direction_series).
    direction: 1 = bullish (price above supertrend), -1 = bearish.
    """
    hl2 = (high + low) / 2
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr = tr.ewm(span=period, min_periods=period).mean()

    basic_upper = (hl2 + multiplier * atr).values.copy()
    basic_lower = (hl2 - multiplier * atr).values.copy()
    closes = close.values
    n = len(closes)

    final_upper = basic_upper.copy()
    final_lower = basic_lower.copy()
    st = np.full(n, np.nan)
    direction = np.ones(n, dtype=int)

    for i in range(1, n):
        # Tighten bands: only move inward
        final_upper[i] = (
            basic_upper[i]
            if basic_upper[i] < final_upper[i - 1] or closes[i - 1] > final_upper[i - 1]
            else final_upper[i - 1]
        )
        final_lower[i] = (
            basic_lower[i]
            if basic_lower[i] > final_lower[i - 1] or closes[i - 1] < final_lower[i - 1]
            else final_lower[i - 1]
        )

        prev_st = st[i - 1]
        if np.isnan(prev_st) or prev_st == final_upper[i - 1]:
            if closes[i] > final_upper[i]:
                st[i], direction[i] = final_lower[i], 1
            else:
                st[i], direction[i] = final_upper[i], -1
        else:  # prev was lower band
            if closes[i] < final_lower[i]:
                st[i], direction[i] = final_upper[i], -1
            else:
                st[i], direction[i] = final_lower[i], 1

    return (
        pd.Series(st, index=close.index),
        pd.Series(direction, index=close.index),
    )


def _obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff().fillna(0))
    return (volume * direction).cumsum()


def _vwap(
    high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series
) -> pd.Series:
    typical = (high + low + close) / 3
    cum_vol = volume.cumsum().replace(0, np.nan)
    return (typical * volume).cumsum() / cum_vol


# ──────────────────────────────────────────────────────────────────────────────
# Pattern detection
# ──────────────────────────────────────────────────────────────────────────────

def _local_extrema(series: pd.Series, window: int = 10) -> tuple[list, list]:
    """Return (highs_positions, lows_positions) as integer index lists."""
    vals = series.values
    n = len(vals)
    highs, lows = [], []
    for i in range(window, n - window):
        neighbourhood = vals[i - window : i + window + 1]
        if vals[i] == neighbourhood.max():
            highs.append(i)
        if vals[i] == neighbourhood.min():
            lows.append(i)
    return highs, lows


def _detect_golden_cross(ema50: pd.Series, ema200: pd.Series, lookback: int = 30) -> bool:
    """EMA50 crossed above EMA200 in the last `lookback` bars."""
    diff = (ema50 - ema200).dropna()
    recent = diff.iloc[-lookback:]
    if len(recent) < 2:
        return False
    return bool(recent.iloc[0] < 0 and recent.iloc[-1] > 0)


def _detect_death_cross(ema50: pd.Series, ema200: pd.Series, lookback: int = 30) -> bool:
    """EMA50 crossed below EMA200 in the last `lookback` bars."""
    diff = (ema50 - ema200).dropna()
    recent = diff.iloc[-lookback:]
    if len(recent) < 2:
        return False
    return bool(recent.iloc[0] > 0 and recent.iloc[-1] < 0)


def _detect_double_bottom(
    close: pd.Series, lows: list, tol: float = 0.03
) -> bool:
    """
    Two troughs within `tol` (3%) of each other in price, at least 20 bars
    apart, both in the last 120 bars.
    """
    n = len(close)
    prices = close.values
    recent = [i for i in lows if i > n - 120]
    for a in range(len(recent)):
        for b in range(a + 1, len(recent)):
            ia, ib = recent[a], recent[b]
            if abs(ib - ia) < 20:
                continue
            pa, pb = prices[ia], prices[ib]
            if abs(pa - pb) / max(pa, pb) <= tol:
                return True
    return False


def _detect_inverse_hns(
    close: pd.Series, lows: list, tol: float = 0.05
) -> bool:
    """
    Three consecutive troughs where the middle (head) is the deepest and
    the two shoulders are within `tol` of each other.
    """
    n = len(close)
    prices = close.values
    recent = [i for i in lows if i > n - 150]
    if len(recent) < 3:
        return False
    for i in range(len(recent) - 2):
        ls, h, rs = recent[i], recent[i + 1], recent[i + 2]
        pl, ph, pr = prices[ls], prices[h], prices[rs]
        if ph >= pl or ph >= pr:
            continue
        if abs(pl - pr) / max(pl, pr) <= tol:
            return True
    return False


def _detect_cup_and_handle(
    close: pd.Series, highs: list, lows: list
) -> bool:
    """
    Cup: price makes a high, declines to a bottom, recovers ≥80% of the drop.
    Handle: a small secondary pullback (<50% of cup depth) in the last 20 bars.
    """
    if not highs or not lows:
        return False
    n = len(close)
    prices = close.values

    # Cup left rim: highest pivot that is not in the last 20 bars
    cup_candidates = [i for i in highs if i < n - 20]
    if not cup_candidates:
        return False
    cup_left = max(cup_candidates, key=lambda i: prices[i])

    cup_lows = [i for i in lows if i > cup_left]
    if not cup_lows:
        return False
    cup_bottom = min(cup_lows, key=lambda i: prices[i])

    left_price = prices[cup_left]
    bottom_price = prices[cup_bottom]
    cup_depth = left_price - bottom_price
    if cup_depth <= 0:
        return False

    # Recovery: must reach at least 80% of left rim
    right_segment = prices[cup_bottom:]
    if len(right_segment) < 5:
        return False
    if right_segment.max() < left_price * 0.80:
        return False

    # Handle: a tight consolidation in the last 20 bars
    handle = prices[-20:]
    handle_drop = handle.max() - handle.min()
    if cup_depth * 0.05 <= handle_drop <= cup_depth * 0.50:
        return True
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Scoring components  (each returns (score: int, notes: str))
# ──────────────────────────────────────────────────────────────────────────────

def _score_trend_alignment(
    ema20_v: float,
    ema50_v: float,
    ema200_v: float,
    adx_v: float,
    st_bullish: bool,
) -> tuple[int, str]:
    score = 0
    notes = []

    if ema20_v > ema50_v > ema200_v:
        score += 15
        notes.append("EMAs fully bullish (20>50>200)")
    elif ema20_v > ema50_v or ema50_v > ema200_v:
        score += 8
        notes.append("Partial EMA alignment")
    else:
        notes.append("EMAs bearish aligned")

    if adx_v >= 30:
        score += 10
        notes.append(f"Strong trend ADX={adx_v:.1f}")
    elif adx_v >= 20:
        score += 5
        notes.append(f"Moderate trend ADX={adx_v:.1f}")
    else:
        notes.append(f"Weak/choppy trend ADX={adx_v:.1f}")

    if st_bullish:
        score += 5
        notes.append("Supertrend bullish")
    else:
        notes.append("Supertrend bearish")

    return min(score, 30), "; ".join(notes)


def _score_momentum(
    rsi_v: float,
    macd_v: float,
    sig_v: float,
    hist_v: float,
    hist_prev: float,
) -> tuple[int, str]:
    score = 0
    notes = []

    if 50 <= rsi_v <= 70:
        score += 15
        notes.append(f"RSI healthy bullish ({rsi_v:.1f})")
    elif 40 <= rsi_v < 50:
        score += 8
        notes.append(f"RSI neutral ({rsi_v:.1f})")
    elif rsi_v > 70:
        score += 5
        notes.append(f"RSI overbought ({rsi_v:.1f})")
    else:
        notes.append(f"RSI oversold/weak ({rsi_v:.1f})")

    if macd_v > sig_v:
        score += 10
        notes.append("MACD above signal")
    else:
        notes.append("MACD below signal")

    if hist_v > 0 and hist_v > hist_prev:
        score += 5
        notes.append("MACD histogram expanding positive")
    elif hist_v > 0:
        score += 2
        notes.append("MACD histogram positive")

    return min(score, 30), "; ".join(notes)


def _score_volume(obv_series: pd.Series, volume: pd.Series) -> tuple[int, str]:
    score = 0
    notes = []

    obv_recent = obv_series.dropna().iloc[-20:]
    if len(obv_recent) >= 5:
        slope = np.polyfit(range(len(obv_recent)), obv_recent.values, 1)[0]
        if slope > 0:
            score += 10
            notes.append("OBV trending up (accumulation)")
        else:
            notes.append("OBV trending down (distribution)")

    avg_vol = volume.iloc[-20:].mean()
    recent_vol = float(volume.iloc[-1])
    vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1.0
    if vol_ratio >= 1.5:
        score += 7
        notes.append(f"Volume surge {vol_ratio:.1f}x avg")
    elif vol_ratio >= 1.0:
        score += 3
        notes.append(f"Volume above avg ({vol_ratio:.1f}x)")
    else:
        notes.append(f"Volume below avg ({vol_ratio:.1f}x)")

    up_obv_days = int((obv_series.iloc[-10:].diff() > 0).sum())
    if up_obv_days >= 7:
        score += 3
        notes.append(f"OBV rising {up_obv_days}/10 recent sessions")

    return min(score, 20), "; ".join(notes)


def _score_patterns(patterns: list) -> tuple[int, str]:
    weights = {
        "golden_cross":   8,
        "inverse_hns":    7,
        "double_bottom":  5,
        "cup_and_handle": 7,
        "death_cross":   -8,   # bearish penalty
    }
    score = 0
    notes = []
    for p in patterns:
        pts = weights.get(p, 0)
        score += pts
        notes.append(f"{p.replace('_', ' ').title()} ({pts:+d})")
    if not patterns:
        notes.append("No significant patterns detected")
    return max(0, min(score, 20)), "; ".join(notes)


# ──────────────────────────────────────────────────────────────────────────────
# Supabase helper
# ──────────────────────────────────────────────────────────────────────────────

def _write_agent_performance(score: int, signal: str) -> None:
    """Non-blocking insert into agent_performance. Silently skips if unconfigured."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        log.debug("Supabase not configured — skipping agent_performance write")
        return
    try:
        from supabase import create_client  # lazy import
        client = create_client(url, key)
        client.table("agent_performance").insert({
            "agent_name": AGENT_NAME,
            "accuracy_90d": None,       # populated by a separate accuracy-tracking job
            "hallucination_rate": None,
            "trend": "STABLE",
            "audit_date": date.today().isoformat(),
        }).execute()
        log.debug("agent_performance row written for %s", AGENT_NAME)
    except Exception as exc:
        log.warning("agent_performance write failed: %s", exc)


# ──────────────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────────────

def analyse(symbol: str) -> dict:
    """
    Run technical analysis on a single NSE/BSE symbol.

    Args:
        symbol: yfinance ticker, e.g. "RELIANCE.NS", "TCS.NS"

    Returns:
        {
            signal:        str   — STRONG_BUY | BUY | HOLD | AVOID | SELL | NO_DATA
            score:         int   — 0–100
            detail:        dict  — scored sub-components + raw indicator values
            upside_pct:    float — estimated upside to target price
            data_sources:  list  — data feeds used
            confidence:    float — 0.0–1.0
            agent_name:    str   — "technical"
        }
    """
    data_sources: list[str] = []

    # ── 1. Fetch OHLCV ───────────────────────────────────────────────────────
    df = get_ohlcv(symbol, period="1y")
    if df is None or len(df) < 60:
        return {
            "signal": "NO_DATA",
            "score": 0,
            "detail": {"error": f"Insufficient OHLCV data for {symbol}"},
            "upside_pct": None,
            "data_sources": [],
            "confidence": 0.0,
            "agent_name": AGENT_NAME,
        }
    data_sources.append("yfinance_ohlcv_1y")

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]
    current_price = float(close.iloc[-1])
    n_bars = len(df)

    # ── 2. Compute indicators ────────────────────────────────────────────────
    rsi      = _rsi(close)
    macd_line, macd_sig, macd_hist = _macd(close)
    ema20    = _ema(close, 20)
    ema50    = _ema(close, 50)
    ema200   = _ema(close, 200)
    adx, _di_plus, _di_minus = _adx(high, low, close)
    st_vals, st_dir = _supertrend(high, low, close, period=10, multiplier=3.0)
    obv      = _obv(close, volume)
    vwap     = _vwap(high, low, close, volume)

    def _last(s: pd.Series) -> Optional[float]:
        v = s.dropna()
        return float(v.iloc[-1]) if len(v) else None

    def _last2(s: pd.Series) -> tuple[Optional[float], Optional[float]]:
        v = s.dropna()
        if len(v) >= 2:
            return float(v.iloc[-1]), float(v.iloc[-2])
        return None, None

    rsi_v         = _last(rsi) or 50.0
    macd_v        = _last(macd_line) or 0.0
    macd_sig_v    = _last(macd_sig) or 0.0
    macd_hist_v, macd_hist_prev = _last2(macd_hist)
    macd_hist_v   = macd_hist_v   or 0.0
    macd_hist_prev = macd_hist_prev or 0.0
    ema20_v       = _last(ema20)  or current_price
    ema50_v       = _last(ema50)  or current_price
    ema200_v      = _last(ema200) or current_price
    adx_v         = _last(adx)    or 15.0
    st_v          = _last(st_vals)
    st_dir_clean  = st_dir.dropna()
    st_bullish    = bool(st_dir_clean.iloc[-1] == 1) if len(st_dir_clean) else False
    obv_v         = _last(obv) or 0.0
    vwap_v        = _last(vwap) or current_price

    # ── 3. Detect patterns ───────────────────────────────────────────────────
    highs_idx, lows_idx = _local_extrema(close, window=10)

    patterns: list[str] = []
    if _detect_golden_cross(ema50, ema200):
        patterns.append("golden_cross")
    if _detect_death_cross(ema50, ema200):
        patterns.append("death_cross")
    if _detect_double_bottom(close, lows_idx):
        patterns.append("double_bottom")
    if _detect_inverse_hns(close, lows_idx):
        patterns.append("inverse_hns")
    if _detect_cup_and_handle(close, highs_idx, lows_idx):
        patterns.append("cup_and_handle")

    # ── 4. Score ─────────────────────────────────────────────────────────────
    trend_score, trend_notes   = _score_trend_alignment(ema20_v, ema50_v, ema200_v, adx_v, st_bullish)
    mom_score,   mom_notes     = _score_momentum(rsi_v, macd_v, macd_sig_v, macd_hist_v, macd_hist_prev)
    vol_score,   vol_notes     = _score_volume(obv, volume)
    pat_score,   pat_notes     = _score_patterns(patterns)

    # Sub-scores are individually capped; max possible = 30+30+20+20 = 100
    total_score = trend_score + mom_score + vol_score + pat_score
    total_score = max(0, min(100, total_score))

    # ── 5. Signal ────────────────────────────────────────────────────────────
    if total_score >= 72:
        signal = "STRONG_BUY"
    elif total_score >= 55:
        signal = "BUY"
    elif total_score >= 40:
        signal = "HOLD"
    elif total_score >= 25:
        signal = "AVOID"
    else:
        signal = "SELL"

    # ── 6. Upside % ──────────────────────────────────────────────────────────
    analyst_target: Optional[float] = None
    try:
        import yfinance as yf
        from data.fetchers import yf_fetch_with_retry
        _t = yf.Ticker(symbol)
        info = yf_fetch_with_retry(lambda: _t.info)
        analyst_target = info.get("targetMeanPrice") or info.get("targetMedianPrice")
        if analyst_target:
            analyst_target = float(analyst_target)
            data_sources.append("yfinance_analyst_target")
    except Exception:
        pass

    target_price = analyst_target if analyst_target else ema200_v * 1.5
    upside_pct = ((target_price - current_price) / current_price * 100) if current_price > 0 else None

    # ── 7. Confidence ────────────────────────────────────────────────────────
    data_quality = min(n_bars / 252, 1.0)
    bullish_signals = sum([
        ema20_v > ema50_v > ema200_v,
        rsi_v > 50,
        macd_v > macd_sig_v,
        st_bullish,
        adx_v > 20,
    ])
    signal_agreement = bullish_signals / 5.0
    confidence = round(min(0.95, data_quality * 0.4 + signal_agreement * 0.6), 2)

    # ── 8. Build result ──────────────────────────────────────────────────────
    obv_trend = "UP" if float(obv.iloc[-5:].diff().mean() or 0) > 0 else "DOWN"
    avg_vol_20 = float(volume.iloc[-20:].mean())
    vol_ratio = round(float(volume.iloc[-1]) / avg_vol_20, 2) if avg_vol_20 > 0 else 1.0

    detail = {
        "trend_alignment": {
            "score":            trend_score,
            "ema_aligned":      ema20_v > ema50_v > ema200_v,
            "adx":              round(adx_v, 2),
            "supertrend_bullish": st_bullish,
            "notes":            trend_notes,
        },
        "momentum": {
            "score":                mom_score,
            "rsi":                  round(rsi_v, 2),
            "macd_bullish":         macd_v > macd_sig_v,
            "macd_histogram_growing": macd_hist_v > macd_hist_prev and macd_hist_v > 0,
            "notes":                mom_notes,
        },
        "volume_confirmation": {
            "score":        vol_score,
            "obv_trend":    obv_trend,
            "volume_vs_avg": vol_ratio,
            "notes":        vol_notes,
        },
        "pattern": {
            "score":    pat_score,
            "detected": patterns,
            "notes":    pat_notes,
        },
        "indicators": {
            "current_price":    round(current_price, 2),
            "rsi":              round(rsi_v, 2),
            "macd":             round(macd_v, 4),
            "macd_signal":      round(macd_sig_v, 4),
            "ema20":            round(ema20_v, 2),
            "ema50":            round(ema50_v, 2),
            "ema200":           round(ema200_v, 2),
            "adx":              round(adx_v, 2),
            "supertrend":       round(st_v, 2) if st_v is not None else None,
            "supertrend_bullish": st_bullish,
            "obv":              round(obv_v, 0),
            "vwap":             round(vwap_v, 2),
            "analyst_target":   round(analyst_target, 2) if analyst_target else None,
            "target_price_used": round(target_price, 2),
        },
    }

    result = {
        "signal":       signal,
        "score":        total_score,
        "detail":       detail,
        "upside_pct":   round(upside_pct, 2) if upside_pct is not None else None,
        "data_sources": data_sources,
        "confidence":   confidence,
        "agent_name":   AGENT_NAME,
    }

    # ── 9. Persist agent run ─────────────────────────────────────────────────
    try:
        _write_agent_performance(total_score, signal)
    except Exception as exc:
        log.warning("Persisting agent run failed (non-critical): %s", exc)

    return result


# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    sym = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE.NS"
    print(f"\nAnalysing {sym} …\n")
    out = analyse(sym)
    print(json.dumps(out, indent=2, default=str))
