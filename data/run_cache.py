"""
data/run_cache.py — P7-H: intra-run memoisation for expensive remote fetches
============================================================================
Collapses the duplicate remote calls that happen *within a single pipeline run*.

The problem
-----------
Five consumers independently fetch the same fundamentals for the same symbol
during one analysis:

    fundamental          -> get_screener_data
    warren_bot           -> get_screener_data + get_screener_history
    mgmt_quality         -> get_screener_data + get_screener_history
    governance_screener  -> get_screener_data + get_screener_history
    insider_signal       -> get_screener_history   (called twice: once by
                            sentiment, once by institutional)

That is ~9 remote fetches per symbol where 2 would do. Across the orchestrator
(~24 symbols) and discovery (200 pre-screen + 25 deep) it works out to roughly
640 fetches/day for data that changes quarterly — and that volume from one
Railway IP is a prime suspect for the 403s that push agents onto degraded data.

Why a plain memo dict is not enough
-----------------------------------
fundamental, warren_bot and mgmt_quality run *concurrently* — the orchestrator
dispatches them in one asyncio.gather, each via asyncio.to_thread. They all call
get_screener_data(symbol) at the same instant, so a naive check-then-fetch cache
misses in all three threads and issues all three requests anyway.

This module therefore uses **per-key locking with double-checked reads**: the
first caller for a key fetches while the others block on that key's lock and are
served the result. Concurrent duplicates collapse into a single request. Locks
are per-key, so unrelated symbols never serialise against each other.

Staleness
---------
Entries carry a TTL (default 30 min, `RUN_CACHE_TTL_S` to override). Fundamentals
update quarterly, so a half-hour window is far inside the data's natural refresh
period. The TTL exists mainly to bound staleness in the long-lived API process,
which — unlike the worker's discrete runs — never restarts between requests.
Callers with hard run boundaries should still call `clear()` at run start.

Returned values are deep-copied so a caller mutating its result cannot poison
the entry for the next consumer.

Usage
-----
    from data.run_cache import memoise_run

    @memoise_run()
    def get_screener_data(symbol: str) -> dict | None:
        ...

    # at a run boundary
    from data.run_cache import clear, log_stats
    clear()
    ...run the pipeline...
    log_stats("orchestrator")
"""

from __future__ import annotations

import copy
import functools
import logging
import os
import threading
import time
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

# Fundamentals change quarterly; 30 minutes is well inside that. Mainly bounds
# staleness in the long-lived web process, which has no natural run boundary.
DEFAULT_TTL_S = float(os.environ.get("RUN_CACHE_TTL_S", 1800))

_MISS = object()          # sentinel — distinguishes "absent" from a cached None

# Soft bounds for the long-lived web process, which has no run boundary to
# clear on. A discovery pre-screen legitimately holds ~400 entries (200 symbols
# x 2 payloads), so the cap sits comfortably above that.
_SWEEP_THRESHOLD = 512    # entries before an expiry sweep runs on write
_MAX_ENTRIES     = 1024   # hard ceiling; oldest evicted first

_CACHE: dict[str, tuple[float, Any]] = {}
_KEY_LOCKS: dict[str, threading.Lock] = {}
_GUARD = threading.Lock()  # protects _CACHE and _KEY_LOCKS themselves

_STATS = {"hits": 0, "misses": 0, "collapsed": 0, "bypassed": 0}


# ──────────────────────────────────────────────────────────────────────────────
# Internals
# ──────────────────────────────────────────────────────────────────────────────

def _make_key(label: str, args: tuple, kwargs: dict) -> Optional[str]:
    """Stable key for this call, or None if the arguments are not representable."""
    try:
        return f"{label}({args!r},{sorted(kwargs.items())!r})"
    except Exception:
        return None


def _read(key: str, ttl: float) -> Any:
    """Return the cached value, or _MISS when absent or expired."""
    with _GUARD:
        entry = _CACHE.get(key)
        if entry is None:
            return _MISS
        stored_at, value = entry
        if (time.monotonic() - stored_at) > ttl:
            _CACHE.pop(key, None)
            return _MISS
        return value


def _write(key: str, value: Any) -> None:
    with _GUARD:
        _CACHE[key] = (time.monotonic(), value)
        if len(_CACHE) > _SWEEP_THRESHOLD:
            _sweep_expired_locked()


def _sweep_expired_locked() -> None:
    """
    Drop expired entries. Caller must hold _GUARD.

    Needed because the API process has no run boundary: the worker clears at the
    start of every run, but the web dyno accumulates an entry per distinct symbol
    requested via /api/warren_bot/{sym}, /api/valuation/{sym} and on-demand
    analysis, and entries are otherwise only evicted when read again.
    """
    now = time.monotonic()
    stale = [k for k, (ts, _) in _CACHE.items() if (now - ts) > DEFAULT_TTL_S]
    for k in stale:
        _CACHE.pop(k, None)
        _KEY_LOCKS.pop(k, None)

    # If everything is still live we are legitimately holding a large working
    # set (e.g. a 200-symbol discovery pre-screen). Evict oldest-first rather
    # than grow without bound.
    if len(_CACHE) > _MAX_ENTRIES:
        oldest = sorted(_CACHE.items(), key=lambda kv: kv[1][0])[: len(_CACHE) - _MAX_ENTRIES]
        for k, _ in oldest:
            _CACHE.pop(k, None)
            _KEY_LOCKS.pop(k, None)


def _key_lock(key: str) -> threading.Lock:
    with _GUARD:
        lock = _KEY_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _KEY_LOCKS[key] = lock
        return lock


def _bump(field: str) -> None:
    with _GUARD:
        _STATS[field] += 1


# ──────────────────────────────────────────────────────────────────────────────
# Public decorator
# ──────────────────────────────────────────────────────────────────────────────

def memoise_run(
    ttl: float | None = None,
    label: str | None = None,
) -> Callable:
    """
    Memoise a function for the duration of a run (bounded by `ttl`).

    Exceptions are never cached — a failing call propagates and the next caller
    retries. A returned ``None`` *is* cached: within one run a source that just
    failed will almost certainly fail again, and retrying it 9 times per symbol
    is exactly the hammering this module exists to stop.
    """
    def decorator(fn: Callable) -> Callable:
        fn_label = label or f"{fn.__module__}.{fn.__qualname__}"
        effective_ttl = ttl if ttl is not None else DEFAULT_TTL_S

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = _make_key(fn_label, args, kwargs)
            if key is None:                       # unhashable/unrepresentable
                _bump("bypassed")
                return fn(*args, **kwargs)

            # Fast path — no lock contention on a warm entry.
            cached = _read(key, effective_ttl)
            if cached is not _MISS:
                _bump("hits")
                return copy.deepcopy(cached)

            # Slow path. Hold this key's lock so concurrent callers for the SAME
            # key wait here rather than each issuing their own request.
            with _key_lock(key):
                # Re-check: another thread may have populated it while we waited.
                cached = _read(key, effective_ttl)
                if cached is not _MISS:
                    _bump("collapsed")
                    return copy.deepcopy(cached)

                result = fn(*args, **kwargs)      # exceptions propagate, uncached
                _write(key, result)
                _bump("misses")
                return copy.deepcopy(result)

        wrapper._run_cache_label = fn_label       # type: ignore[attr-defined]
        wrapper._run_cache_uncached = fn          # type: ignore[attr-defined]
        return wrapper

    return decorator


# ──────────────────────────────────────────────────────────────────────────────
# Lifecycle / observability
# ──────────────────────────────────────────────────────────────────────────────

def clear() -> None:
    """Drop every entry and reset counters. Call at a run boundary."""
    with _GUARD:
        _CACHE.clear()
        _KEY_LOCKS.clear()
        for k in _STATS:
            _STATS[k] = 0


def stats() -> dict:
    """
    Snapshot of cache activity.

    saved_calls / saved_pct measure remote requests avoided: every hit and every
    collapsed concurrent duplicate is one fetch that did not leave the box.
    """
    with _GUARD:
        s = dict(_STATS)
        s["entries"] = len(_CACHE)

    saved = s["hits"] + s["collapsed"]
    attempted = saved + s["misses"]
    s["saved_calls"] = saved
    s["attempted_calls"] = attempted
    s["saved_pct"] = round(saved / attempted * 100, 1) if attempted else 0.0
    return s


def log_stats(context: str = "") -> dict:
    """Log a one-line summary of what the cache saved. Returns the stats dict."""
    s = stats()
    if s["attempted_calls"]:
        log.info(
            "run_cache%s: %d/%d remote calls avoided (%.1f%%) "
            "— %d hits, %d concurrent collapses, %d fetched, %d entries",
            f" [{context}]" if context else "",
            s["saved_calls"], s["attempted_calls"], s["saved_pct"],
            s["hits"], s["collapsed"], s["misses"], s["entries"],
        )
    return s


_depth = threading.local()


class scope:
    """
    Context manager for a cache-scoped run.

        with run_cache.scope("discovery"):
            ...

    Clears on entry and logs a summary on exit.

    Nesting-safe: only the outermost scope clears and reports. An inner scope
    is a no-op, so calling a scoped entry point from inside another one cannot
    wipe the outer run's cache mid-flight.
    """

    def __init__(self, context: str = ""):
        self.context = context
        self._outermost = False

    def __enter__(self) -> "scope":
        level = getattr(_depth, "level", 0)
        self._outermost = (level == 0)
        _depth.level = level + 1
        if self._outermost:
            clear()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        _depth.level = max(0, getattr(_depth, "level", 1) - 1)
        if self._outermost:
            log_stats(self.context)
        return False


def scoped(context: str = "") -> Callable:
    """
    Decorator form of `scope` — wraps a whole entry point in one cache scope.

        @scoped("discovery")
        def run_discovery(...): ...
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with scope(context):
                return fn(*args, **kwargs)
        return wrapper
    return decorator
