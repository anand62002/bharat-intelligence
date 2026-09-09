"""
data/fundamentals_cache.py — P7-I: persistent fundamentals cache with decay
===========================================================================
Removes the *across-day* refetching of data that only changes quarterly, and
keeps the agents on real fundamentals when upstream sources are blocked.

Layering
--------
    agent
      -> get_screener_data()            P7-H  in-process memo, ~30 min
        -> @persist_cache               P7-I  Supabase, days            <- here
          -> network                          screener -> trendlyne -> yfinance

The memo sits outside, so a repeat call inside one run costs neither a network
request nor a database round-trip.

Decay
-----
TTL is set per kind, matching how fast the underlying data actually moves:

    snapshot   7 days    ratios, PE, margins, promoter holding
    history   30 days    10-year annual series — only changes at results

An entry is also invalidated early when the symbol's `earnings_calendar` date
falls between `fetched_at` and now: results are the one event that genuinely
changes fundamentals, and waiting out the TTL would serve pre-results numbers
into post-results analysis.

Quality ranking
---------------
A successful screener.in fetch leaves `data_source` unset; the fallbacks tag
themselves. That lets us rank payloads:

    3 screener.in   2 trendlyne   1 yfinance_fallback   0 unusable

Two rules follow, and they are the point of this module:

  * A lower-quality fetch NEVER overwrites a higher-quality cached row (unless
    the cached row is older than STALE_FLOOR_DAYS). A 403 storm cannot degrade
    a good cache.
  * When a live fetch comes back worse than what is cached, the CACHED payload
    is returned instead, flagged with `cache_age_days` and
    `served_from_cache=True`. On 2026-09-09 every fundamental score was computed
    on yfinance fallback data; with this, they would have used real screener
    fundamentals a few days old.

Never raises: any cache failure degrades to a normal live fetch.

Usage
-----
    from data.fundamentals_cache import persist_cache

    @memoise_run(key_fn=symbol_key)
    @persist_cache(kind="snapshot")
    def get_screener_data(symbol): ...
"""

from __future__ import annotations

import functools
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

# ── TTL per kind (days) ───────────────────────────────────────────────────────
TTL_DAYS = {
    "snapshot": int(os.getenv("FUNDAMENTALS_TTL_SNAPSHOT_DAYS", 7)),
    "history":  int(os.getenv("FUNDAMENTALS_TTL_HISTORY_DAYS", 30)),
}

# Beyond this age a cached row is too old to defend, and even a degraded live
# fetch is preferable.
STALE_FLOOR_DAYS = int(os.getenv("FUNDAMENTALS_STALE_FLOOR_DAYS", 90))

QUALITY = {"screener_in": 3, "trendlyne_fallback": 2, "yfinance_fallback": 1}

_ENABLED = os.getenv("FUNDAMENTALS_CACHE", "true").strip().lower() not in ("0", "false", "no", "off")

_STATS = {"hits": 0, "misses": 0, "stale_served": 0, "writes": 0, "skipped_writes": 0}


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _client():
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_KEY", "")
    if not (url and key):
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception as exc:
        log.debug("fundamentals_cache: Supabase unavailable: %s", exc)
        return None


def _base_symbol(symbol: str) -> str:
    s = str(symbol).strip().upper()
    for suffix in (".NS", ".BO"):
        if s.endswith(suffix):
            return s[: -len(suffix)]
    return s


def payload_quality(payload: Optional[dict]) -> int:
    """
    Rank a payload by origin. A successful screener.in fetch does not set
    `data_source`, so an absent marker on a non-empty payload means screener.
    """
    if not payload or not isinstance(payload, dict):
        return 0
    src = payload.get("data_source")
    if src:
        return QUALITY.get(str(src), 1)
    return QUALITY["screener_in"]


def payload_source(payload: Optional[dict]) -> str:
    if not payload or not isinstance(payload, dict):
        return "unusable"
    return str(payload.get("data_source") or "screener_in")


def _age_days(fetched_at: Any) -> Optional[float]:
    if not fetched_at:
        return None
    try:
        ts = str(fetched_at).replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0
    except Exception:
        return None


def _earnings_since(client, symbol: str, fetched_at: Any) -> bool:
    """
    True when the symbol reported results between `fetched_at` and today.
    Results are the one event that genuinely invalidates fundamentals.
    """
    age = _age_days(fetched_at)
    if age is None:
        return False
    try:
        since = (date.today() - timedelta(days=age + 1)).isoformat()
        rows = (client.table("earnings_calendar")
                      .select("earnings_date")
                      .eq("symbol", _base_symbol(symbol))
                      .gte("earnings_date", since)
                      .lte("earnings_date", date.today().isoformat())
                      .limit(1)
                      .execute()
                      .data or [])
        return bool(rows)
    except Exception:
        return False        # table missing / unreadable — do not block on it


# ──────────────────────────────────────────────────────────────────────────────
# Read / write
# ──────────────────────────────────────────────────────────────────────────────

def read_entry(symbol: str, kind: str) -> Optional[dict]:
    """Return the raw cache row, or None. Never raises."""
    client = _client()
    if client is None:
        return None
    try:
        rows = (client.table("fundamentals_cache")
                      .select("*")
                      .eq("symbol", _base_symbol(symbol))
                      .eq("kind", kind)
                      .limit(1)
                      .execute()
                      .data or [])
        return rows[0] if rows else None
    except Exception as exc:
        log.debug("fundamentals_cache read failed (%s/%s): %s", symbol, kind, exc)
        return None


def write_entry(symbol: str, kind: str, payload: dict, existing: Optional[dict] = None) -> bool:
    """
    Upsert a payload, refusing to downgrade a better cached row.

    Returns True when written. Never raises.
    """
    q_new = payload_quality(payload)
    if q_new == 0:
        return False

    if existing is not None:
        q_old = int(existing.get("quality") or 0)
        age   = _age_days(existing.get("fetched_at")) or 0.0
        if q_new < q_old and age < STALE_FLOOR_DAYS:
            _STATS["skipped_writes"] += 1
            log.debug(
                "fundamentals_cache: refusing to downgrade %s/%s (cached q=%d age=%.1fd, new q=%d)",
                symbol, kind, q_old, age, q_new,
            )
            return False

    client = _client()
    if client is None:
        return False
    try:
        now = datetime.now(timezone.utc).isoformat()
        client.table("fundamentals_cache").upsert({
            "symbol":     _base_symbol(symbol),
            "kind":       kind,
            "payload":    payload,
            "source":     payload_source(payload),
            "quality":    q_new,
            "fetched_at": now,
            "updated_at": now,
        }, on_conflict="symbol,kind").execute()
        _STATS["writes"] += 1
        return True
    except Exception as exc:
        log.debug("fundamentals_cache write failed (%s/%s): %s", symbol, kind, exc)
        return False


def is_fresh(entry: dict, kind: str, symbol: str = "", client=None) -> bool:
    """Fresh = inside its TTL AND no results reported since it was fetched."""
    age = _age_days(entry.get("fetched_at"))
    if age is None or age > TTL_DAYS.get(kind, 7):
        return False
    if client is not None and symbol and _earnings_since(client, symbol, entry.get("fetched_at")):
        log.info("fundamentals_cache: %s/%s invalidated — results reported since fetch",
                 symbol, kind)
        return False
    return True


# ──────────────────────────────────────────────────────────────────────────────
# Decorator
# ──────────────────────────────────────────────────────────────────────────────

def persist_cache(kind: str) -> Callable:
    """
    Wrap a symbol-first fetcher with the persistent cache.

    Flow:
      1. Fresh cached row  -> return it (flagged with its age).
      2. Otherwise fetch live.
      3. Live result at least as good as cache -> store and return it.
      4. Live result worse (or empty) and a usable cached row exists -> return
         the CACHED payload instead, flagged. This is what keeps agents on real
         fundamentals during a 403 outage.
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(symbol, *args, **kwargs):
            if not _ENABLED:
                return fn(symbol, *args, **kwargs)

            # The cache must never be able to break a fetch. Any failure in the
            # lookup path falls through to the live call.
            client = entry = None
            try:
                client = _client()
                entry  = read_entry(symbol, kind) if client is not None else None

                if entry and is_fresh(entry, kind, symbol, client):
                    _STATS["hits"] += 1
                    payload = dict(entry.get("payload") or {})
                    payload["served_from_cache"] = True
                    payload["cache_age_days"] = round(_age_days(entry.get("fetched_at")) or 0, 2)
                    return payload
            except Exception as exc:
                log.debug("fundamentals_cache: lookup failed for %s/%s — "
                          "falling through to live fetch: %s", symbol, kind, exc)
                client = entry = None

            _STATS["misses"] += 1
            live = fn(symbol, *args, **kwargs)

            # Everything below is best-effort: the live result is already in
            # hand, so a cache problem here must not lose it.
            try:
                q_live   = payload_quality(live)
                q_cached = int(entry.get("quality") or 0) if entry else 0

                if entry and q_live < q_cached:
                    age = _age_days(entry.get("fetched_at")) or 0.0
                    if age < STALE_FLOOR_DAYS:
                        _STATS["stale_served"] += 1
                        log.info(
                            "fundamentals_cache: serving cached %s/%s (q=%d, %.1fd old) — "
                            "live fetch degraded to q=%d",
                            symbol, kind, q_cached, age, q_live,
                        )
                        payload = dict(entry.get("payload") or {})
                        payload["served_from_cache"] = True
                        payload["cache_age_days"] = round(age, 2)
                        payload["cache_reason"] = "live fetch degraded"
                        return payload

                if q_live > 0:
                    write_entry(symbol, kind, live, existing=entry)
            except Exception as exc:
                log.debug("fundamentals_cache: post-fetch handling failed for %s/%s: %s",
                          symbol, kind, exc)
            return live

        wrapper._persist_cache_kind = kind          # type: ignore[attr-defined]
        wrapper._persist_cache_uncached = fn        # type: ignore[attr-defined]
        return wrapper

    return decorator


# ──────────────────────────────────────────────────────────────────────────────
# Observability
# ──────────────────────────────────────────────────────────────────────────────

def stats() -> dict:
    s = dict(_STATS)
    looked = s["hits"] + s["misses"]
    s["lookups"] = looked
    s["hit_rate_pct"] = round(s["hits"] / looked * 100, 1) if looked else 0.0
    s["enabled"] = _ENABLED
    return s


def reset_stats() -> None:
    for k in _STATS:
        _STATS[k] = 0


def log_stats(context: str = "") -> dict:
    s = stats()
    if s["lookups"]:
        log.info(
            "fundamentals_cache%s: %d/%d served from cache (%.1f%%) — "
            "%d live fetches, %d stale-served during outage, %d writes, %d downgrades refused",
            f" [{context}]" if context else "",
            s["hits"], s["lookups"], s["hit_rate_pct"],
            s["misses"], s["stale_served"], s["writes"], s["skipped_writes"],
        )
    return s
