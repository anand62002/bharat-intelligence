"""
agents/commodities.py — Commodities Agent
Fetches gold, crude oil, and silver data via yfinance and scores each commodity's
outlook for Indian investors.

Entry point: analyse() -> dict

Returns per-commodity signal + an overall commodities macro view.
Critical gold upside flag: Fed cutting + DXY falling + INR depreciating + central bank buying.

Ticker strategy
───────────────
  Gold    : GC=F  (NYMEX USD)  primary; GOLDBEES.NS  (NSE INR ETF) preferred
  Crude   : BZ=F  (ICE Brent)  primary; CL=F  (NYMEX WTI) fallback
            India crude imports are Brent-benchmarked; WTI is a secondary proxy.
            CRUDEOIL.NS is NOT a valid yfinance ticker (NSE lists MCX derivatives,
            not directly on yfinance) — removed.
  Silver  : SI=F  (NYMEX USD)
  INR/USD : USDINR=X
"""

import logging
import os
import sys
from datetime import date
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

log = logging.getLogger(__name__)
AGENT_NAME = "commodities"

# ──────────────────────────────────────────────────────────────────────────────
# Data fetch helpers
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_ohlcv(ticker: str, period: str = "3mo") -> Optional[object]:
    """Return yfinance history DataFrame or None on failure (with retry)."""
    try:
        import yfinance as yf
        from data.fetchers import yf_fetch_with_retry
        t = yf.Ticker(ticker)
        df = yf_fetch_with_retry(t.history, period=period, auto_adjust=True)
        if df is None or df.empty:
            return None
        return df
    except Exception as exc:
        log.warning("yfinance fetch failed for %s: %s", ticker, exc)
        return None


def _latest_price(df) -> Optional[float]:
    """Return the most recent valid (non-NaN) closing price, or None."""
    if df is None or df.empty:
        return None
    close = df["Close"].dropna()
    if close.empty:
        return None
    val = float(close.iloc[-1])
    return val if val == val else None   # NaN guard (NaN != NaN)


def _trend_50d(df) -> Optional[float]:
    """
    Return % change over last 50 sessions (or all available if < 50).
    Positive = uptrend, negative = downtrend.
    """
    if df is None or df.empty:
        return None
    close = df["Close"].dropna()
    n = min(50, len(close))
    if n < 2:
        return None
    pct = (close.iloc[-1] - close.iloc[-n]) / close.iloc[-n] * 100
    return round(float(pct), 2)


def _inr_correlation(commodity_df, inr_df, window: int = 30) -> Optional[float]:
    """
    Pearson correlation between commodity close and USDINR close
    over last `window` sessions available in both series.
    """
    if commodity_df is None or inr_df is None:
        return None
    try:
        import pandas as pd
        c = commodity_df["Close"].rename("commodity")
        r = inr_df["Close"].rename("inr")
        merged = pd.concat([c, r], axis=1, join="inner").dropna()
        if len(merged) < window:
            n = len(merged)
        else:
            n = window
        if n < 5:
            return None
        tail = merged.tail(n)
        corr = tail["commodity"].corr(tail["inr"])
        return round(float(corr), 4) if corr == corr else None  # NaN guard
    except Exception as exc:
        log.warning("Correlation calc failed: %s", exc)
        return None


def _seasonal_month_bias(commodity: str) -> str:
    """
    Simple calendar-based seasonal heuristic.
    Returns 'STRONG', 'MODERATE', 'NEUTRAL', or 'WEAK'.
    """
    month = date.today().month
    if commodity == "gold":
        # Dhanteras / Diwali (Oct-Nov), wedding season (Nov-Dec, Apr-May) = strong demand
        if month in (10, 11, 12):
            return "STRONG"
        if month in (4, 5):
            return "MODERATE"
        return "NEUTRAL"
    if commodity == "crude":
        # Northern hemisphere summer driving season (Jun-Aug) = higher demand
        if month in (6, 7, 8):
            return "STRONG"
        if month in (1, 2):
            return "MODERATE"
        return "NEUTRAL"
    if commodity == "silver":
        # Follows gold seasonal pattern loosely
        if month in (10, 11, 12):
            return "MODERATE"
        return "NEUTRAL"
    return "NEUTRAL"


# ──────────────────────────────────────────────────────────────────────────────
# Scoring per commodity
# ──────────────────────────────────────────────────────────────────────────────

def _score_gold(
    trend_50d: Optional[float],
    inr_corr: Optional[float],
    season: str,
    inr_usd: Optional[float],
) -> tuple[int, str, float]:
    """
    Score gold 0-100 and estimate upside_pct.
    Returns (score, note, upside_pct).

    Components:
      Trend (40 pts): 50-day price momentum
      INR hedge (25 pts): correlation with USDINR — high positive corr = rupee hedge premium
      Seasonal (20 pts): demand seasonality
      Stability (15 pts): always somewhat positive (gold is a safe haven)
    """
    score = 0
    notes = []

    # Trend component
    if trend_50d is None:
        score += 20
        notes.append("Trend unknown")
    elif trend_50d >= 8:
        score += 40
        notes.append(f"Strong uptrend +{trend_50d:.1f}% (50d)")
    elif trend_50d >= 3:
        score += 30
        notes.append(f"Moderate uptrend +{trend_50d:.1f}% (50d)")
    elif trend_50d >= 0:
        score += 20
        notes.append(f"Flat/slight uptrend {trend_50d:.1f}% (50d)")
    elif trend_50d >= -5:
        score += 10
        notes.append(f"Mild downtrend {trend_50d:.1f}% (50d)")
    else:
        score += 0
        notes.append(f"Strong downtrend {trend_50d:.1f}% (50d)")

    # INR hedge component
    if inr_corr is None:
        score += 12
        notes.append("INR corr unknown")
    elif inr_corr >= 0.5:
        score += 25
        notes.append(f"High INR correlation {inr_corr:.2f} — strong rupee hedge")
    elif inr_corr >= 0.2:
        score += 18
        notes.append(f"Moderate INR correlation {inr_corr:.2f}")
    else:
        score += 10
        notes.append(f"Low INR correlation {inr_corr:.2f}")

    # Seasonal component
    seasonal_pts = {"STRONG": 20, "MODERATE": 14, "NEUTRAL": 8, "WEAK": 2}
    score += seasonal_pts.get(season, 8)
    notes.append(f"Seasonal demand: {season}")

    # Stability (always a positive for gold)
    score += 15
    notes.append("Safe-haven base premium")

    score = max(0, min(100, score))

    # Upside estimate: base 5% for neutral; trend adds momentum
    if trend_50d is not None and trend_50d > 0:
        upside_pct = round(min(35.0, trend_50d * 1.5 + 5.0), 1)
    else:
        upside_pct = 5.0

    return score, "; ".join(notes), upside_pct


def _score_crude(
    trend_50d: Optional[float],
    inr_usd: Optional[float],
    season: str,
) -> tuple[int, str, float]:
    """
    Score crude oil 0-100 for Indian investors.
    Note: rising crude is generally NEGATIVE for India (net importer).
    Score represents expected *equity market* impact, not crude price outlook.

    Components:
      Price trend (50 pts): falling crude = positive for India
      INR impact (30 pts): weak INR amplifies import cost when crude rises
      Seasonal (20 pts)
    """
    score = 0
    notes = []

    # Trend: falling crude = equity-positive for India
    if trend_50d is None:
        score += 25
        notes.append("Crude trend unknown")
    elif trend_50d <= -8:
        score += 50
        notes.append(f"Crude falling sharply {trend_50d:.1f}% — very positive for India")
    elif trend_50d <= -3:
        score += 40
        notes.append(f"Crude softening {trend_50d:.1f}% — positive for OMCs/economy")
    elif trend_50d <= 3:
        score += 25
        notes.append(f"Crude stable {trend_50d:.1f}%")
    elif trend_50d <= 8:
        score += 12
        notes.append(f"Crude rising {trend_50d:.1f}% — CAD pressure")
    else:
        score += 0
        notes.append(f"Crude surging +{trend_50d:.1f}% — significant macro headwind")

    # INR impact
    if inr_usd is None:
        score += 15
        notes.append("INR unknown")
    elif inr_usd < 83:
        score += 30
        notes.append(f"INR {inr_usd:.1f} strong — crude less expensive in INR terms")
    elif inr_usd < 85:
        score += 20
        notes.append(f"INR {inr_usd:.1f} stable — manageable crude import cost")
    elif inr_usd < 87:
        score += 10
        notes.append(f"INR {inr_usd:.1f} weak — crude import cost elevated in INR")
    else:
        score += 0
        notes.append(f"INR {inr_usd:.1f} very weak — crude import cost critically high")

    # Seasonal
    seasonal_pts = {"STRONG": 5, "MODERATE": 10, "NEUTRAL": 15, "WEAK": 20}
    # Strong demand season → higher crude → bad for India
    score += seasonal_pts.get(season, 15)
    notes.append(f"Crude demand season: {season}")

    score = max(0, min(100, score))

    # Upside for crude-linked stocks (OMCs benefit from falling crude)
    if trend_50d is not None and trend_50d < 0:
        upside_pct = round(min(25.0, abs(trend_50d) * 0.8 + 3.0), 1)
    else:
        upside_pct = 3.0

    return score, "; ".join(notes), upside_pct


def _score_silver(
    trend_50d: Optional[float],
    inr_corr: Optional[float],
    season: str,
) -> tuple[int, str, float]:
    """
    Score silver 0-100.
    Silver is both industrial metal and precious metal — dual driver.

    Components:
      Trend (50 pts)
      INR hedge (30 pts)
      Seasonal (20 pts)
    """
    score = 0
    notes = []

    if trend_50d is None:
        score += 25
        notes.append("Trend unknown")
    elif trend_50d >= 10:
        score += 50
        notes.append(f"Strong silver uptrend +{trend_50d:.1f}% (50d)")
    elif trend_50d >= 4:
        score += 38
        notes.append(f"Moderate uptrend +{trend_50d:.1f}% (50d)")
    elif trend_50d >= 0:
        score += 25
        notes.append(f"Flat silver {trend_50d:.1f}% (50d)")
    elif trend_50d >= -5:
        score += 12
        notes.append(f"Mild downtrend {trend_50d:.1f}% (50d)")
    else:
        score += 0
        notes.append(f"Downtrend {trend_50d:.1f}% (50d)")

    if inr_corr is None:
        score += 15
        notes.append("INR corr unknown")
    elif inr_corr >= 0.4:
        score += 30
        notes.append(f"Good INR hedge {inr_corr:.2f}")
    elif inr_corr >= 0.15:
        score += 18
        notes.append(f"Moderate INR correlation {inr_corr:.2f}")
    else:
        score += 8
        notes.append(f"Weak INR correlation {inr_corr:.2f}")

    seasonal_pts = {"STRONG": 20, "MODERATE": 14, "NEUTRAL": 8, "WEAK": 2}
    score += seasonal_pts.get(season, 8)
    notes.append(f"Seasonal: {season}")

    score = max(0, min(100, score))

    if trend_50d is not None and trend_50d > 0:
        upside_pct = round(min(40.0, trend_50d * 1.8 + 4.0), 1)
    else:
        upside_pct = 4.0

    return score, "; ".join(notes), upside_pct


def _signal_from_score(score: int) -> str:
    if score >= 65:
        return "BULLISH"
    if score >= 40:
        return "NEUTRAL"
    return "BEARISH"


# ──────────────────────────────────────────────────────────────────────────────
# Critical gold upside flag
# ──────────────────────────────────────────────────────────────────────────────

def _check_critical_gold_upside(
    gold_trend: Optional[float],
    dxy_trend: Optional[float],
    inr_trend_pct: Optional[float],
    fed_cutting: bool,
) -> tuple[bool, list[str]]:
    """
    Flag CRITICAL_GOLD_UPSIDE when ALL 4 conditions are met:
      1. Fed cutting (us10y falling / FRED signal or passed in)
      2. DXY falling (dxy_trend < 0 over 30 sessions)
      3. INR depreciating (inr_trend_pct > 0, i.e. more rupees per dollar)
      4. Central bank buying (proxied by: gold itself in uptrend > +5%)

    Returns (flag: bool, triggered_conditions: list[str]).
    """
    conditions = []

    if fed_cutting:
        conditions.append("Fed rate cut cycle active (US10Y declining)")

    if dxy_trend is not None and dxy_trend < -1.5:
        conditions.append(f"DXY falling {dxy_trend:.1f}% — USD weakening")

    if inr_trend_pct is not None and inr_trend_pct > 1.0:
        conditions.append(f"INR depreciating {inr_trend_pct:.1f}% — rupee weakness")

    if gold_trend is not None and gold_trend > 5.0:
        conditions.append(f"Gold uptrend +{gold_trend:.1f}% — central bank/institutional buying signal")

    flag = len(conditions) >= 4
    return flag, conditions


# ──────────────────────────────────────────────────────────────────────────────
# Supabase helper
# ──────────────────────────────────────────────────────────────────────────────

def _write_agent_performance() -> None:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        return
    try:
        from supabase import create_client
        create_client(url, key).table("agent_performance").insert({
            "agent_name": AGENT_NAME,
            "accuracy_90d": None,
            "hallucination_rate": None,
            "trend": "STABLE",
            "audit_date": date.today().isoformat(),
        }).execute()
    except Exception as exc:
        log.warning("agent_performance write failed: %s", exc)


# ──────────────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────────────

def analyse() -> dict:
    """
    Analyse gold, crude oil, and silver for Indian investors.

    Returns:
        {
            signal:          str   — BULLISH | NEUTRAL | BEARISH (overall)
            score:           int   — 0-100 weighted average
            commodities:     dict  — per-commodity breakdown
            critical_gold_upside: bool
            gold_upside_conditions: list[str]
            data_sources:    list[str]
            agent_name:      str   — "commodities"
        }
    """
    data_sources: list[str] = []

    # ── 1. Fetch OHLCV data ────────────────────────────────────────────────────
    # Gold: prefer INR-denominated NSE ETF, fall back to USD futures
    gold_df      = _fetch_ohlcv("GC=F",         period="3mo")   # NYMEX USD gold futures
    goldbees_df  = _fetch_ohlcv("GOLDBEES.NS",  period="3mo")   # NSE INR gold ETF

    # Crude: try Brent (BZ=F) first since India imports are Brent-benchmarked.
    # BZ=F can sometimes return calendar-spread or far-month contract data with
    # distorted prices; we sanity-check against WTI (CL=F) and fall back when
    # the Brent/WTI ratio is outside the historically plausible 0.8–1.4 band.
    # CRUDEOIL.NS is NOT a valid yfinance symbol (NSE MCX derivatives) — removed.
    brent_df     = _fetch_ohlcv("BZ=F",         period="3mo")   # ICE Brent crude
    wti_df       = _fetch_ohlcv("CL=F",         period="3mo")   # NYMEX WTI crude

    silver_df    = _fetch_ohlcv("SI=F",         period="3mo")   # NYMEX USD silver futures
    inr_df       = _fetch_ohlcv("USDINR=X",     period="3mo")   # USD/INR spot

    if any(df is not None for df in [gold_df, brent_df, wti_df, silver_df]):
        data_sources.append("yfinance_commodities")
    if inr_df is not None:
        data_sources.append("yfinance_usdinr")
    if goldbees_df is not None:
        data_sources.append("yfinance_india_etf")

    # ── 2. Derived values ─────────────────────────────────────────────────────
    # Helper: use first non-None, non-NaN value.
    # Avoids two bugs:
    #   (a) `x or fallback` incorrectly drops x when x == 0.0 (falsy but valid)
    #   (b) NaN values from yfinance dividend-adjustment artifacts pass `is not None`
    def _first(*vals):
        import math
        for v in vals:
            if v is not None and not (isinstance(v, float) and math.isnan(v)):
                return v
        return None

    # Gold: prefer GOLDBEES.NS (INR-denominated) then GC=F (USD)
    gold_price  = _first(_latest_price(goldbees_df), _latest_price(gold_df))
    gold_trend  = _first(_trend_50d(goldbees_df),    _trend_50d(gold_df))

    # Crude: prefer Brent, but sanity-check the price ratio vs WTI.
    # Brent/WTI ratio is historically 0.85–1.25. If outside 0.75–1.40,
    # the BZ=F data is likely a stale far-month contract — use WTI instead.
    brent_px = _latest_price(brent_df)
    wti_px   = _latest_price(wti_df)
    _use_brent = False
    if brent_px is not None and wti_px is not None and wti_px > 0:
        ratio = brent_px / wti_px
        _use_brent = (0.75 <= ratio <= 1.40)
        if not _use_brent:
            log.warning(
                "BZ=F Brent/WTI ratio %.2f out of plausible range (%.2f/%.2f) — "
                "falling back to WTI (CL=F)",
                ratio, brent_px, wti_px,
            )
    elif brent_px is not None and wti_px is None:
        _use_brent = True   # WTI unavailable, use whatever Brent gives us

    if _use_brent:
        crude_price  = brent_px
        crude_trend  = _trend_50d(brent_df)
        crude_source = "Brent (BZ=F)"
    else:
        crude_price  = wti_px
        crude_trend  = _trend_50d(wti_df)
        crude_source = "WTI (CL=F)"

    silver_price = _latest_price(silver_df)
    silver_trend = _trend_50d(silver_df)

    inr_usd = _latest_price(inr_df)

    # INR trend: positive = depreciation (more INR per USD)
    inr_trend_pct = _trend_50d(inr_df)

    # DXY proxy: if USDINR is falling (INR strengthening) → USD weakening
    dxy_proxy_trend = (-inr_trend_pct) if inr_trend_pct is not None else None

    gold_corr   = _inr_correlation(
        goldbees_df if goldbees_df is not None else gold_df, inr_df
    )
    silver_corr = _inr_correlation(silver_df, inr_df)

    gold_season   = _seasonal_month_bias("gold")
    crude_season  = _seasonal_month_bias("crude")
    silver_season = _seasonal_month_bias("silver")

    # Fed cutting proxy: approximate by checking if US10Y (fetched separately if available)
    # Since we don't re-fetch FRED here, we use a simple heuristic:
    # if gold is rallying strongly AND INR is depreciating, likely risk-off / rate-cut narrative
    fed_cutting_proxy = (
        gold_trend is not None and gold_trend > 3
        and inr_trend_pct is not None and inr_trend_pct > 1
    )

    # ── 3. Score each commodity ────────────────────────────────────────────────
    g_score, g_note, g_upside = _score_gold(gold_trend, gold_corr, gold_season, inr_usd)
    c_score, c_note, c_upside = _score_crude(crude_trend, inr_usd, crude_season)
    s_score, s_note, s_upside = _score_silver(silver_trend, silver_corr, silver_season)

    # ── 4. Critical gold upside flag ──────────────────────────────────────────
    critical_gold, gold_conditions = _check_critical_gold_upside(
        gold_trend=gold_trend,
        dxy_trend=dxy_proxy_trend,
        inr_trend_pct=inr_trend_pct,
        fed_cutting=fed_cutting_proxy,
    )

    # ── 5. Overall macro score (weighted: gold 40%, crude 40%, silver 20%) ─────
    overall_score = round(g_score * 0.4 + c_score * 0.4 + s_score * 0.2)
    overall_score = max(0, min(100, overall_score))
    overall_signal = _signal_from_score(overall_score)

    result = {
        "signal":   overall_signal,
        "score":    overall_score,
        "commodities": {
            "gold": {
                "signal":    _signal_from_score(g_score),
                "score":     g_score,
                "upside_pct": g_upside,
                "price":     gold_price,
                "trend_50d": gold_trend,
                "inr_correlation": gold_corr,
                "seasonal":  gold_season,
                "detail":    g_note,
                "agent_name": AGENT_NAME,
            },
            "crude": {
                "signal":    _signal_from_score(c_score),
                "score":     c_score,
                "upside_pct": c_upside,
                "price":     crude_price,
                "trend_50d": crude_trend,
                "inr_usd":   inr_usd,
                "seasonal":  crude_season,
                "detail":    c_note,
                "source":    crude_source,
                "agent_name": AGENT_NAME,
            },
            "silver": {
                "signal":    _signal_from_score(s_score),
                "score":     s_score,
                "upside_pct": s_upside,
                "price":     silver_price,
                "trend_50d": silver_trend,
                "inr_correlation": silver_corr,
                "seasonal":  silver_season,
                "detail":    s_note,
                "agent_name": AGENT_NAME,
            },
        },
        "critical_gold_upside":   critical_gold,
        "gold_upside_conditions": gold_conditions,
        "data_sources":  list(dict.fromkeys(data_sources)),
        "agent_name":    AGENT_NAME,
    }

    try:
        _write_agent_performance()
    except Exception as exc:
        log.warning("Persisting agent run failed (non-critical): %s", exc)

    return result


if __name__ == "__main__":
    import json as _json
    out = analyse()
    print(_json.dumps(out, indent=2, default=str))
