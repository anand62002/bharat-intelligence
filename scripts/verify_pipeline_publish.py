"""
scripts/verify_pipeline_publish.py — did the 06:00 portfolio pipeline publish? (WP-001)

Why this exists
---------------
Until bf5d3fa (2026-11-12) the 06:00 orchestrator published nothing for months:
every published-rec insert failed on a missing `danger_drop_pct` column and its
own daily_runs insert failed too, so nothing recorded the outage. This script
answers one question for a given date, read-only, following
docs/architecture/REVIEW.md section 3:

    PUBLISHED          ≥1 non-SUPPRESSED portfolio rec saved; no run errors
                       other than (possibly) unrelated ones.
    SCHEMA_DRIFT_ONLY  ≥1 non-SUPPRESSED rec saved, and the only errors the run
                       reported are SCHEMA_DRIFT (tolerant insert worked; a
                       migration is still pending — WP-002).
    ALL_SUPPRESSED     recs saved but every one is SUPPRESSED (or the run says
                       so): the judge gate, not the insert, is the blocker.
    NO_OUTPUT          the run logged a row but saved no recs: read
                       details.reasons (synthesis_fallback / agent_run_failed).
    NO_RUN             no orchestrator run row and no portfolio recs.

Exit code: 0 only when at least one non-SUPPRESSED portfolio rec exists for the
date (PUBLISHED or SCHEMA_DRIFT_ONLY); 1 otherwise.

Usage (project root, with the service_role / sb_secret key in .env):

    python scripts/verify_pipeline_publish.py                 # today (UTC date)
    python scripts/verify_pipeline_publish.py --date 2026-11-15

Note: the 06:00 IST run happens at 00:30 UTC, so it falls on the same UTC date.
The script performs SELECTs only.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

PUBLISHED = "PUBLISHED"
SCHEMA_DRIFT_ONLY = "SCHEMA_DRIFT_ONLY"
ALL_SUPPRESSED = "ALL_SUPPRESSED"
NO_OUTPUT = "NO_OUTPUT"
NO_RUN = "NO_RUN"
VERDICTS = (PUBLISHED, SCHEMA_DRIFT_ONLY, ALL_SUPPRESSED, NO_OUTPUT, NO_RUN)

# Statuses only the orchestrator writes (discovery always writes "OK"). Used to
# recognise the orchestrator's row when daily_runs has no `pipeline` column yet.
_ORCHESTRATOR_ONLY_STATUSES = {"WARNING", "ALL_SUPPRESSED", "NO_OUTPUT"}


# ── pure logic (unit-tested) ─────────────────────────────────────────────────

def _run_errors(run_row: Optional[dict]) -> list[str]:
    details = (run_row or {}).get("details") or {}
    errs = details.get("errors") or []
    return [str(e) for e in errs]


def verdict(run_row: Optional[dict], actions: Iterable[str]) -> str:
    """
    Classify one day of the portfolio pipeline.

    run_row: the orchestrator's daily_runs row for the date, or None.
    actions: the `action` of every is_discovery=false recommendation that day.
    """
    actions = [str(a or "").upper() for a in actions]
    published = sum(1 for a in actions if a != "SUPPRESSED")

    if published > 0:
        errs = _run_errors(run_row)
        if errs and all(e.startswith("SCHEMA_DRIFT") for e in errs):
            return SCHEMA_DRIFT_ONLY
        return PUBLISHED

    if actions:                                   # rows exist, all SUPPRESSED
        return ALL_SUPPRESSED
    if run_row is None:
        return NO_RUN
    if str(run_row.get("status", "")).upper() == ALL_SUPPRESSED:
        return ALL_SUPPRESSED
    return NO_OUTPUT


def exit_code(v: str) -> int:
    return 0 if v in (PUBLISHED, SCHEMA_DRIFT_ONLY) else 1


def pick_orchestrator_row(rows: list[dict], has_pipeline_col: bool) -> Optional[dict]:
    """Latest orchestrator row from the day's daily_runs rows (newest first)."""
    if has_pipeline_col:
        rows = [r for r in rows if r.get("pipeline") == "orchestrator"]
    else:
        rows = [r for r in rows if str(r.get("status", "")).upper() in _ORCHESTRATOR_ONLY_STATUSES]
    return rows[0] if rows else None


# ── database (read-only) ─────────────────────────────────────────────────────

def _missing_column(exc: Exception) -> bool:
    s = str(exc)
    return "PGRST204" in s or "42703" in s or "does not exist" in s or "Could not find the" in s


def _client():
    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)   # .env on disk wins over a stale OS variable (see diagnose_performance.py)
    except Exception:
        pass
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not (url and key):
        print("FAIL: SUPABASE_URL / SUPABASE_SERVICE_KEY not set")
        sys.exit(2)
    if key.startswith("sb_publishable_"):
        print("WARNING: publishable/anon key — RLS will hide rows and every count will read 0.")
    from supabase import create_client
    return create_client(url, key)


def fetch_run_rows(client, day: date) -> tuple[list[dict], bool]:
    """daily_runs rows for the date, newest first; second value = pipeline column exists."""
    try:
        r = (client.table("daily_runs")
             .select("run_date, pipeline, status, symbols_processed, errors, details, created_at")
             .eq("run_date", day.isoformat())
             .order("created_at", desc=True)
             .execute())
        return r.data or [], True
    except Exception as exc:
        if not _missing_column(exc):
            raise
        print("NOTE: daily_runs has no pipeline/details columns "
              "(db/migrations/add_daily_runs_pipeline.sql pending) — "
              "recognising the orchestrator row by status only.")
        r = (client.table("daily_runs")
             .select("run_date, status, symbols_processed, errors, created_at")
             .eq("run_date", day.isoformat())
             .order("created_at", desc=True)
             .execute())
        return r.data or [], False


def fetch_actions(client, day: date) -> list[str]:
    start, end = day.isoformat(), (day + timedelta(days=1)).isoformat()
    r = (client.table("recommendations")
         .select("action")
         .eq("is_discovery", False)
         .gte("created_at", start)
         .lt("created_at", end)
         .execute())
    return [row.get("action") for row in (r.data or [])]


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--date", type=date.fromisoformat, default=date.today(),
                    help="YYYY-MM-DD (default: today)")
    args = ap.parse_args(argv)
    day: date = args.date

    client = _client()
    rows, has_pipeline_col = fetch_run_rows(client, day)
    run = pick_orchestrator_row(rows, has_pipeline_col)
    actions = fetch_actions(client, day)

    print("=" * 62)
    print(f"PORTFOLIO PIPELINE PUBLISH CHECK — {day}")
    print("=" * 62)
    if run is None:
        print("daily_runs (orchestrator): no row")
    else:
        details = run.get("details") or {}
        print(f"daily_runs (orchestrator): status={run.get('status')} "
              f"symbols_processed={run.get('symbols_processed')} errors={run.get('errors')}")
        print(f"  details.reasons = {details.get('reasons')}")
        print(f"  details.errors  = {details.get('errors')}")
    counts = Counter(str(a) for a in actions)
    print(f"recommendations (is_discovery=false): {len(actions)} rows")
    for action, n in sorted(counts.items()):
        print(f"  {action:<12} {n}")

    v = verdict(run, actions)
    print("-" * 62)
    print(f"VERDICT: {v}")
    return exit_code(v)


if __name__ == "__main__":
    sys.exit(main())
