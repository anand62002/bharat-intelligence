"""
data/run_log.py — one writer for the daily_runs table.

Why this exists
---------------
Until 2026-11-12 the 06:00 orchestrator's run-log insert failed EVERY day: it
wrote an `agents_run` column that daily_runs does not have, and the failure was
logged as a warning and forgotten. Every row in daily_runs came from the 10:30
discovery screener instead, with errors hard-coded to 0. So "status=success,
errors=0" described discovery, while the portfolio pipeline — which had
published nothing for months — left no record at all.

log_daily_run() tags each row with the pipeline that wrote it and keeps the
detail as JSON. Both columns arrive with
db/migrations/add_daily_runs_pipeline.sql; until that is applied the insert is
retried with the base columns and a WARNING names the migration — the row is
never silently lost again.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Optional

log = logging.getLogger(__name__)

MIGRATION = "db/migrations/add_daily_runs_pipeline.sql"


def _missing_column(exc: Exception) -> bool:
    s = str(exc)
    return "PGRST204" in s or "Could not find the" in s or ("column" in s and "does not exist" in s)


def log_daily_run(
    client: Any,
    pipeline: str,
    *,
    symbols_processed: int,
    errors: int,
    duration_seconds: float,
    status: str,
    details: Optional[dict] = None,
    run_date: Optional[date] = None,
) -> bool:
    """Insert one daily_runs row. Returns True if a row was written. Never raises."""
    base = {
        "run_date": (run_date or date.today()).isoformat(),
        "symbols_processed": int(symbols_processed),
        "errors": int(errors),
        "duration_seconds": round(float(duration_seconds), 2),
        "status": status,
    }
    try:
        client.table("daily_runs").insert({**base, "pipeline": pipeline, "details": details or {}}).execute()
        return True
    except Exception as exc:
        if not _missing_column(exc):
            log.warning("daily_runs insert failed (%s): %s", pipeline, exc)
            return False
        log.warning("daily_runs has no pipeline/details columns — run %s. Logging base columns only.",
                    MIGRATION)
    try:
        client.table("daily_runs").insert(base).execute()
        return True
    except Exception as exc:
        log.warning("daily_runs insert failed (%s): %s", pipeline, exc)
        return False
