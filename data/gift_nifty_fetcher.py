"""
data/gift_nifty_fetcher.py — P6-D-7: GIFT Nifty Pre-Market Signal Layer
========================================================================
GIFT Nifty (formerly SGX Nifty, rebranded Jul 2023) is the NIFTY 50
futures contract traded on NSE IFSC at GIFT City, Gujarat.

Trading hours: Mon–Fri, ~06:30 IST to ~02:45 IST next day (two sessions)
Source: NSE IX public market-rate API (near-month NIFTY index future)
Pre-market window: 06:30–09:15 IST (~3 h before NSE cash opens)

Why it matters:
  - Reflects overnight US / Asian market sentiment
  - FII futures activity creates directional bias at NSE open
  - Historical accuracy: ±0.5% at 08:30 IST → correct direction ~72%

Signal thresholds:
  ≥ +1.0%  → POSITIVE_OPEN STRONG
  +0.5–1%  → POSITIVE_OPEN MODERATE
  -0.5–0.5% → FLAT
  -0.5–1%  → NEGATIVE_OPEN MODERATE
  ≤ -1.0%  → NEGATIVE_OPEN STRONG

Usage:
  from data.gift_nifty_fetcher import get_gift_nifty_signal
  signal = get_gift_nifty_signal()   # {gift_price, prev_close, premium_pct, signal, ...}
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

# In-memory cache — GIFT Nifty changes every minute; 30 min is fine for our jobs
_CACHE_TTL_SECONDS = 1_800
_cache: dict = {}
_cache_ts: float = 0.0

# ─────────────────────────────────────────────────────────────────────────────
# Signal threshold helpers
# ─────────────────────────────────────────────────────────────────────────────

def _classify_signal(premium_pct: float) -> tuple[str, str]:
    """Return (signal, strength) from premium_pct."""
    if premium_pct >= 1.0:
        return "POSITIVE_OPEN", "STRONG"
    if premium_pct >= 0.5:
        return "POSITIVE_OPEN", "MODERATE"
    if premium_pct <= -1.0:
        return "NEGATIVE_OPEN", "STRONG"
    if premium_pct <= -0.5:
        return "NEGATIVE_OPEN", "MODERATE"
    return "FLAT", "WEAK"


def _market_note(signal: str, strength: str, premium_pct: float) -> str:
    if signal == "POSITIVE_OPEN":
        return (f"GIFT Nifty {premium_pct:+.2f}% — "
                f"{'bullish gap-up' if strength == 'STRONG' else 'mild positive bias'} expected at open")
    if signal == "NEGATIVE_OPEN":
        return (f"GIFT Nifty {premium_pct:+.2f}% — "
                f"{'gap-down expected; consider pre-open alerts' if strength == 'STRONG' else 'mild selling pressure expected'} at open")
    return f"GIFT Nifty {premium_pct:+.2f}% — no directional edge, flat open expected"


# ─────────────────────────────────────────────────────────────────────────────
# Previous NIFTY 50 close (yfinance)
# ─────────────────────────────────────────────────────────────────────────────

def _ist_now() -> datetime:
    from datetime import timedelta
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)


def _get_prev_nifty_close() -> Optional[float]:
    """
    Last COMPLETED NSE Nifty 50 session close.

    During NSE hours (before 15:30 IST) yfinance's last daily bar is today's
    live, unfinished session, so the bar before it is the previous close.
    """
    try:
        import yfinance as yf
        hist = yf.Ticker("^NSEI").history(period="5d", auto_adjust=True)
        if hist.empty:
            return None
        prices = hist["Close"].dropna()
        if prices.empty:
            return None
        now = _ist_now()
        last_day = prices.index[-1].date()
        if last_day == now.date() and (now.hour, now.minute) < (15, 30) and len(prices) > 1:
            return float(prices.iloc[-2])
        return float(prices.iloc[-1])
    except Exception as exc:
        log.warning("GIFT Nifty: could not fetch ^NSEI prev close: %s", exc)
        return None


_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


# ─────────────────────────────────────────────────────────────────────────────
# Source 1 — NSE IX (the exchange GIFT Nifty trades on) public market-rate API
# ─────────────────────────────────────────────────────────────────────────────
# GIFT Nifty is the NIFTY index future (instrument FUTIDX, symbol NIFTY) listed
# on NSE International Exchange. nseix.com's homepage reads this endpoint; it
# needs no token or cookie. Verified 2026-11-15: near-month 25,266 (last trade
# 14-Nov 02:44 IST) vs NSE close 25,144.30.

NSEIX_URL = "https://www.nseix.com/api/market-rate?type=derivatives"


def _pick_near_month(rows: list[dict]) -> Optional[dict]:
    """Nearest-expiry NIFTY index future with a positive last price."""
    best: Optional[tuple] = None
    for r in rows or []:
        if str(r.get("INSTRUMENTTYPE", "")).upper() != "FUTIDX":
            continue
        if str(r.get("SYMBOL", "")).upper() != "NIFTY":
            continue
        try:
            price = float(str(r.get("LASTPRICE", "")).replace(",", ""))
            expiry = datetime.strptime(str(r.get("EXPIRYDATE")), "%d-%b-%Y").date()
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        if best is None or expiry < best[0]:
            best = (expiry, price, r)
    if best is None:
        return None
    expiry, price, r = best
    return {"price": price, "expiry": expiry.isoformat(), "traded_at": r.get("TIMESTMP")}


def _fetch_nseix() -> Optional[dict]:
    """Near-month GIFT Nifty from NSE IX; retries through the outbound proxy if configured."""
    import requests
    headers = {**_BROWSER_HEADERS, "Referer": "https://www.nseix.com/"}
    attempts = ["direct"]
    try:
        from data.proxy_session import proxy_configured
        if proxy_configured():
            attempts.append("proxy")
    except Exception:
        pass
    for mode in attempts:
        try:
            session = requests.Session()
            if mode == "proxy":
                from data.proxy_session import apply_proxy_to_session
                apply_proxy_to_session(session)
            resp = session.get(NSEIX_URL, headers=headers, timeout=10)
            if resp.status_code != 200:
                log.warning("GIFT Nifty NSE IX (%s) HTTP %s", mode, resp.status_code)
                continue
            picked = _pick_near_month((resp.json() or {}).get("data") or [])
            if picked:
                return picked
            log.warning("GIFT Nifty NSE IX (%s): no NIFTY index future in response", mode)
        except Exception as exc:
            log.warning("GIFT Nifty NSE IX (%s) failed: %s", mode, exc)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Source 2 — NSE (legacy endpoint; kept as a fallback)
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_nseindia() -> Optional[dict]:
    """Attempt the NSE GiftNifty endpoint with a cookie session."""
    import requests
    headers = {**_BROWSER_HEADERS, "Referer": "https://www.nseindia.com/"}
    try:
        session = requests.Session()
        session.get("https://www.nseindia.com/", headers=headers, timeout=8)
        resp = session.get("https://www.nseindia.com/api/GiftNifty", headers=headers, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            records = data.get("data") or [data]
            if records:
                price = records[0].get("lastPrice") or records[0].get("LTP")
                if price:
                    return {"price": float(str(price).replace(",", "")), "expiry": None, "traded_at": None}
    except Exception as exc:
        log.info("GIFT Nifty NSE fallback failed: %s", exc)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Source 3 — Moneycontrol widget scrape (fallback)
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_moneycontrol() -> Optional[dict]:
    import requests
    try:
        resp = requests.get(
            "https://www.moneycontrol.com/mc/widget/sgxnifty/nifty-futures.html",
            headers={"User-Agent": "Mozilla/5.0 (compatible; BharatIntelligence/1.0)"},
            timeout=8,
        )
        if resp.status_code == 200:
            text = resp.text
            for pattern in [
                r'"lastPrice"\s*:\s*([\d,\.]+)',
                r'class="[^"]*ltp[^"]*"[^>]*>([\d,\.]+)',
                r'data-value="([\d,\.]+)"',
            ]:
                m = re.search(pattern, text)
                if m:
                    candidate = float(m.group(1).replace(",", ""))
                    if 15_000 < candidate < 40_000:   # plausible NIFTY range
                        return {"price": candidate, "expiry": None, "traded_at": None}
    except Exception as exc:
        log.info("GIFT Nifty Moneycontrol fallback failed: %s", exc)
    return None


# NOTE (2026-11-15): there used to be a fourth "yfinance_intraday" source that
# read ^NSEI — the Nifty 50 index itself, not GIFT Nifty. Before the NSE open
# its latest bar IS the previous close, so the "premium" was always 0.00% FLAT,
# and during NSE hours it reported the Nifty's own move under the GIFT label.
# It is gone on purpose: no data is reported as UNAVAILABLE, never faked.


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def get_gift_nifty_signal(force_refresh: bool = False) -> dict:
    """
    Fetch GIFT Nifty price and compute pre-market directional signal.

    Returns:
    {
        "gift_price":      float | None,   # GIFT Nifty near-month futures price
        "gift_expiry":     str | None,     # ISO date of the contract used
        "gift_traded_at":  str | None,     # last trade time as the source reports it (IST)
        "prev_nifty_close": float | None,  # last completed NSE NIFTY 50 close
        "premium_pts":     float | None,   # gift_price − prev_close
        "premium_pct":     float | None,   # premium as % of prev_close
        "signal":          str,            # POSITIVE_OPEN | NEGATIVE_OPEN | FLAT | UNAVAILABLE
        "signal_strength": str,            # STRONG | MODERATE | WEAK
        "source":          str,            # nseix | nseindia | moneycontrol | unavailable
        "fetched_at":      str,            # ISO8601 timestamp
        "market_note":     str,            # human-readable summary
        "is_pre_market":   bool,           # True if fetched during GIFT trading hours (IST 6:30–23:30)
    }
    """
    global _cache, _cache_ts
    now_ts = time.monotonic()
    if not force_refresh and _cache and (now_ts - _cache_ts) < _CACHE_TTL_SECONDS:
        return _cache

    fetched_at = datetime.now(timezone.utc).isoformat()
    ist_now = _ist_now()
    ist_hour_min = ist_now.hour * 60 + ist_now.minute
    is_pre_market = (6 * 60 + 30) <= ist_hour_min <= (23 * 60 + 30)

    prev_close = _get_prev_nifty_close()

    quote: Optional[dict] = None
    source = "unavailable"
    for src_name, fetcher in (
        ("nseix",        _fetch_nseix),
        ("nseindia",     _fetch_nseindia),
        ("moneycontrol", _fetch_moneycontrol),
    ):
        try:
            q = fetcher()
        except Exception as exc:
            log.warning("GIFT Nifty fetcher %s raised: %s", src_name, exc)
            continue
        if q and 15_000 < float(q.get("price") or 0) < 40_000:
            quote, source = q, src_name
            break

    gift_price = round(float(quote["price"]), 2) if quote else None
    base = {
        "gift_price":       gift_price,
        "gift_expiry":      quote.get("expiry") if quote else None,
        "gift_traded_at":   quote.get("traded_at") if quote else None,
        "prev_nifty_close": round(prev_close, 2) if prev_close else prev_close,
        "source":           source,
        "fetched_at":       fetched_at,
        "is_pre_market":    is_pre_market,
    }

    if gift_price is None or prev_close is None:
        log.warning(
            "GIFT Nifty unavailable (gift_price=%s, prev_close=%s) — every source failed",
            gift_price, prev_close,
        )
        result = {
            **base,
            "premium_pts":     None,
            "premium_pct":     None,
            "signal":          "UNAVAILABLE",
            "signal_strength": "WEAK",
            "market_note":     "GIFT Nifty data unavailable — no pre-market signal",
        }
        _cache, _cache_ts = result, now_ts
        return result

    premium_pts = round(gift_price - prev_close, 2)
    premium_pct = round((premium_pts / prev_close) * 100, 3)
    signal, strength = _classify_signal(premium_pct)
    note = _market_note(signal, strength, premium_pct)
    if base["gift_traded_at"]:
        note += f" (last trade {base['gift_traded_at']} IST)"

    result = {
        **base,
        "premium_pts":     premium_pts,
        "premium_pct":     premium_pct,
        "signal":          signal,
        "signal_strength": strength,
        "market_note":     note,
    }
    _cache, _cache_ts = result, now_ts
    log.info(
        "GIFT Nifty signal: %s %s (%.2f pts / %.3f%%) source=%s",
        signal, strength, premium_pts, premium_pct, source,
    )
    return result


def get_gift_nifty_macro_adjustment(gift: dict) -> int:
    """
    Return macro score adjustment (−3 to +3) based on GIFT Nifty signal.
    Used by agents/macro.py in the morning orchestrator run.
    """
    if gift.get("signal") == "POSITIVE_OPEN" and gift.get("signal_strength") == "STRONG":
        return 3
    if gift.get("signal") == "POSITIVE_OPEN" and gift.get("signal_strength") == "MODERATE":
        return 1
    if gift.get("signal") == "NEGATIVE_OPEN" and gift.get("signal_strength") == "STRONG":
        return -3
    if gift.get("signal") == "NEGATIVE_OPEN" and gift.get("signal_strength") == "MODERATE":
        return -1
    return 0
