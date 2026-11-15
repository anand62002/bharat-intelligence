"""
data/candidate_ledger.py — writes to the `candidates` table (WP-013, ADR-002).

Why this exists
---------------
The system learned only from promoted calls, about 15 independent labels a
month. Discovery screens ~200 symbols a day and used to throw away everything it
computed for the ones it rejected, so nobody could test whether the pre-screen
itself adds value. Every screened symbol now gets one row per day with what was
known at decision time; WP-014 labels the rows with 30/90-day alpha later.

One row per (run_date, symbol, source). The deep-analysis pass later in the same
run upserts the same key with the agent fields, so a deep-analysed symbol is
updated, never duplicated.

Rules
-----
- record_candidates() never raises. A missing table logs ONE warning naming the
  migration and returns 0 — the screener's run must not fail because of the
  ledger.
- Rows in one upsert must share a key set: PostgREST fills absent keys with NULL
  for the whole batch, which would wipe columns a later update didn't mean to
  touch. Rows are grouped by key set before batching.
- Bump FEATURE_VERSION whenever the meaning of a `features` key changes, so
  later analysis never mixes incompatible rows.
"""

from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any, Iterable, Optional

log = logging.getLogger(__name__)

TABLE = "candidates"
MIGRATION = "db/migrations/create_candidates.sql"
FEATURE_VERSION = 1
ON_CONFLICT = "run_date,symbol,source"
BATCH_SIZE = 100

SOURCES = ("discovery", "portfolio", "multibagger")
STAGES = ("SCREENED", "PRESCREEN_PASS", "DEEP", "PROMOTED")

_missing_table_warned = False


def is_missing_table(exc: Exception) -> bool:
    s = str(exc)
    return "PGRST205" in s or "42P01" in s or "Could not find the table" in s or "does not exist" in s


def base_symbol(symbol: str) -> str:
    return symbol.upper().replace(".NS", "").replace(".BO", "")


def get_client() -> Any:
    """Service-role client from the environment, or None (logged) when not configured."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        log.warning("candidate ledger: SUPABASE_URL/SUPABASE_SERVICE_KEY not set — not recording")
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception as exc:
        log.warning("candidate ledger: could not create Supabase client: %s", exc)
        return None


def nifty_level() -> Optional[float]:
    """Latest Nifty 50 close. Called once per run, not per symbol."""
    try:
        from data.fetchers import get_ohlcv
        df = get_ohlcv("^NSEI", period="5d")
        close = df["Close"].dropna() if df is not None and "Close" in df.columns else None
        if close is None or close.empty:
            log.warning("candidate ledger: no Nifty close available — rows saved without it")
            return None
        return round(float(close.iloc[-1]), 2)
    except Exception as exc:
        log.warning("candidate ledger: Nifty fetch failed — rows saved without it: %s", exc)
        return None


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f or f in (float("inf"), float("-inf")) else round(f, 4)


def screen_row(run_date: date, symbol: str, source: str, *, passed: bool,
               features: Optional[dict], nifty: Optional[float]) -> dict:
    """Row for a symbol that went through the pre-screen (passed or not)."""
    feats = dict(features or {})
    return {
        "run_date": run_date.isoformat(),
        "symbol": base_symbol(symbol),
        "yf_symbol": symbol if "." in symbol or symbol.startswith("^") else f"{symbol}.NS",
        "source": source,
        "stage": "PRESCREEN_PASS" if passed else "SCREENED",
        "price": _num(feats.get("price")),
        "nifty": _num(nifty),
        "features": feats,
        "feature_version": FEATURE_VERSION,
    }


def deep_row(run_date: date, symbol: str, source: str, *, features: Optional[dict],
             agent_signals: Optional[dict], signal_score: Any, data_quality: Optional[str],
             tier: Optional[str], rec_id: Optional[str]) -> dict:
    """Same key as screen_row, with the deep-analysis fields. PROMOTED when a call was saved."""
    return {
        "run_date": run_date.isoformat(),
        "symbol": base_symbol(symbol),
        "source": source,
        "stage": "PROMOTED" if rec_id else "DEEP",
        "features": dict(features or {}),
        "agent_signals": agent_signals or {},
        "signal_score": _num(signal_score),
        "data_quality": data_quality or None,
        "tier": tier,
        "rec_id": rec_id,
        "feature_version": FEATURE_VERSION,
    }


def record_candidates(client: Any, rows: Iterable[dict]) -> int:
    """Batched upsert on (run_date, symbol, source). Returns rows written. Never raises."""
    global _missing_table_warned
    rows = [r for r in rows if r]
    if not rows:
        return 0
    if client is None:
        log.warning("candidate ledger: no client — %d rows not recorded", len(rows))
        return 0

    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault(tuple(sorted(r)), []).append(r)

    written = 0
    for group in groups.values():
        for i in range(0, len(group), BATCH_SIZE):
            batch = group[i:i + BATCH_SIZE]
            try:
                client.table(TABLE).upsert(batch, on_conflict=ON_CONFLICT).execute()
                written += len(batch)
            except Exception as exc:
                if is_missing_table(exc):
                    if not _missing_table_warned:
                        log.warning("candidate ledger: table %r missing — run %s. Not recording.",
                                    TABLE, MIGRATION)
                        _missing_table_warned = True
                    return 0
                log.warning("candidate ledger: upsert of %d rows failed: %s", len(batch), exc)
    return written
