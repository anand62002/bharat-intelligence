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


# ─────────────────────────────────────────────────────────────────────────────
# Labelling (WP-014): 30- and 90-day alpha on every candidate row
# ─────────────────────────────────────────────────────────────────────────────

LABEL_HORIZONS = (30, 90)
LABEL_PAGE = 500
# Rows still unlabelled this many days after their horizon are not retried
# (delisted / renamed symbols would otherwise be re-fetched every evening).
LABEL_GIVE_UP_DAYS = 60
_LABEL_COLS = "id,run_date,symbol,yf_symbol,source,stage,price,nifty"


def _due_rows(client: Any, horizon_days: int, today: date) -> list[dict]:
    """Rows whose horizon has passed and whose label is null, paged LABEL_PAGE at a time."""
    from datetime import timedelta
    cutoff = today - timedelta(days=horizon_days)
    oldest = cutoff - timedelta(days=LABEL_GIVE_UP_DAYS)
    rows: list[dict] = []
    offset = 0
    while True:
        page = (client.table(TABLE).select(_LABEL_COLS)
                .is_(f"alpha_t{horizon_days}", "null")
                .lte("run_date", cutoff.isoformat())
                .gte("run_date", oldest.isoformat())
                .order("run_date").order("symbol")
                .range(offset, offset + LABEL_PAGE - 1)
                .execute().data or [])
        rows.extend(page)
        if len(page) < LABEL_PAGE:
            return rows
        offset += LABEL_PAGE


def label_candidates(client: Any, horizon_days: int, today: Optional[date] = None) -> dict:
    """
    Write price/nifty/alpha at `horizon_days` onto every due candidate row.

    alpha = symbol return − Nifty return over the row's own window
    (run_date → run_date + horizon), as a fraction — the same formula
    recommendation_outcomes uses. Prices come from the outcome tracker's
    price-on-date helpers; one batch download per run date. A row with no
    price is counted as failed and left null — never labelled 0.

    Returns {"horizon", "due", "labelled", "failed", "errors"}. Never raises.
    """
    from collections import defaultdict
    from datetime import datetime, timedelta, timezone

    today = today or date.today()
    out = {"horizon": horizon_days, "due": 0, "labelled": 0, "failed": 0, "errors": []}
    if client is None:
        out["errors"].append("no Supabase client")
        log.warning("label_candidates(t%d): no client", horizon_days)
        return out
    try:
        due = _due_rows(client, horizon_days, today)
    except Exception as exc:
        if is_missing_table(exc):
            out["errors"].append(f"candidates table missing — run {MIGRATION}")
        else:
            out["errors"].append(f"select failed: {exc}")
        log.warning("label_candidates(t%d): could not read due rows: %s", horizon_days, exc)
        return out
    out["due"] = len(due)
    if not due:
        return out

    from agents.outcome_tracker import (NIFTY_SYMBOL, _batch_prices_on_date,
                                        _fetch_price_on_date, _resolve_yf_symbol)

    by_date: dict[str, list[dict]] = defaultdict(list)
    for r in due:
        by_date[str(r["run_date"])[:10]].append(r)

    now = datetime.now(timezone.utc).isoformat()
    updates: list[dict] = []
    failed_syms: list[str] = []
    for run_date_s, rows in sorted(by_date.items()):
        run_d = date.fromisoformat(run_date_s)
        target = run_d + timedelta(days=horizon_days)
        yf_of = {id(r): (r.get("yf_symbol") or _resolve_yf_symbol(r["symbol"])) for r in rows}
        prices = _batch_prices_on_date(list(yf_of.values()) + [NIFTY_SYMBOL], target)
        nifty_t = prices.get(NIFTY_SYMBOL)
        nifty_entry_fallback: Optional[float] = None
        if any(not _num(r.get("nifty")) for r in rows):
            nifty_entry_fallback = _fetch_price_on_date(NIFTY_SYMBOL, run_d)
        for r in rows:
            entry = _num(r.get("price"))
            nifty_entry = _num(r.get("nifty")) or nifty_entry_fallback
            px = prices.get(yf_of[id(r)])
            if not entry or not nifty_entry or px is None or nifty_t is None:
                out["failed"] += 1
                failed_syms.append(r["symbol"])
                continue
            alpha = (px / entry - 1.0) - (nifty_t / nifty_entry - 1.0)
            updates.append({
                "run_date": run_date_s, "symbol": r["symbol"], "source": r["source"],
                "stage": r["stage"],                       # NOT NULL; unchanged
                f"price_t{horizon_days}": round(px, 4),
                f"nifty_t{horizon_days}": round(nifty_t, 4),
                f"alpha_t{horizon_days}": round(alpha, 6),
                "labelled_at": now,
            })

    out["labelled"] = record_candidates(client, updates)
    if len(updates) != out["labelled"]:
        out["errors"].append(f"{len(updates) - out['labelled']} label writes failed")
    if failed_syms:
        log.warning("label_candidates(t%d): %d rows had no price (e.g. %s)",
                    horizon_days, len(failed_syms), ", ".join(failed_syms[:10]))
    log.info("label_candidates(t%d): due=%d labelled=%d failed=%d",
             horizon_days, out["due"], out["labelled"], out["failed"])
    return out
