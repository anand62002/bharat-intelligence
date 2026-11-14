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

import dataclasses
import functools
import json
import logging
import os
import time
import traceback
from datetime import date
from typing import Any, Callable, Optional

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


# ─────────────────────────────────────────────────────────────────────────────
# Job ledger (WP-005, ADR-005): every worker job writes one daily_runs row
# ─────────────────────────────────────────────────────────────────────────────

_OUTPUT_MAX_CHARS = 20_000


def _supabase_client() -> Any:
    """Service client from env, or None. Never raises."""
    url, key = os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_SERVICE_KEY", "")
    if not (url and key):
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception as exc:
        log.warning("job ledger: Supabase client init failed: %s", exc)
        return None


def _jsonable(value: Any) -> Any:
    """Best-effort conversion of a job's return value to JSON-safe data."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = dataclasses.asdict(value)
    try:
        return json.loads(json.dumps(value, default=str))
    except Exception:
        return str(value)


def _output_dict(result: Any) -> dict:
    """Normalise a job's return value to a dict (None → {}, list → {count, items})."""
    if result is None:
        return {}
    if isinstance(result, list):
        return {"count": len(result), "items": result}
    data = _jsonable(result)
    return data if isinstance(data, dict) else {"value": data}


def _trim(output: dict) -> dict:
    """Keep the stored output under _OUTPUT_MAX_CHARS; drop the largest values first."""
    text = json.dumps(output, default=str)
    if len(text) <= _OUTPUT_MAX_CHARS:
        return output
    trimmed = dict(output)
    for key in sorted(trimmed, key=lambda k: len(json.dumps(trimmed[k], default=str)), reverse=True):
        trimmed[key] = f"<truncated: {len(json.dumps(trimmed[key], default=str))} chars>"
        if len(json.dumps(trimmed, default=str)) <= _OUTPUT_MAX_CHARS:
            break
    return trimmed


def _error_count(output: dict) -> int:
    errs = output.get("errors")
    if isinstance(errs, list):
        return len(errs)
    if isinstance(errs, (int, float)) and not isinstance(errs, bool):
        return int(errs)
    return 0


def tracked_job(name: str, *, logs_own_row: bool = False) -> Callable:
    """
    Decorator for worker jobs: one daily_runs row per run, tagged pipeline=<name>.

    • Times the job and catches ANY exception (logged at ERROR with traceback,
      never propagated — APScheduler must keep running).
    • The job may return a dict: stored under details.output; its "count" key
      becomes symbols_processed; a non-empty "errors" makes the status WARNING.
    • status: OK · WARNING (job returned errors) · FAILED (job raised).
    • details.llm_usage: Anthropic tokens spent during the run (data/llm_utils.py).
    • logs_own_row=True for jobs that already write their own daily_runs row
      (orchestrator, discovery): only a FAILED row is written, so a crash before
      their own logging is still recorded and a success is not logged twice.
    • A missing Supabase client is logged at WARNING; the job still runs.
    """
    def decorate(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            from data.llm_utils import llm_usage_scope
            started = time.monotonic()
            result: Any = None
            exc_text: Optional[str] = None
            with llm_usage_scope() as usage:
                try:
                    result = fn(*args, **kwargs)
                except Exception as exc:
                    exc_text = f"{type(exc).__name__}: {exc}"
                    log.error("Job %s failed: %s", name, exc, exc_info=True)
                    tb = traceback.format_exc()
            duration = time.monotonic() - started

            output = _output_dict(result)
            n_err = _error_count(output)
            if exc_text:
                status = "FAILED"
            elif n_err:
                status = "WARNING"
            else:
                status = "OK"

            if logs_own_row and status != "FAILED":
                return result

            details: dict = {"output": _trim(output), "llm_usage": usage.as_dict()}
            if exc_text:
                details["error"] = exc_text[:500]
                details["traceback"] = tb[-2000:]

            count = output.get("count")
            client = _supabase_client()
            if client is None:
                log.warning("job ledger: no Supabase client — %s run not recorded (status=%s)",
                            name, status)
                return result
            log_daily_run(
                client, name,
                symbols_processed=int(count) if isinstance(count, (int, float))
                and not isinstance(count, bool) else 0,
                errors=n_err + (1 if exc_text else 0),
                duration_seconds=duration,
                status=status,
                details=details,
            )
            log.info("job ledger: %s %s in %.1fs (count=%s, errors=%d, llm_tokens=%d)",
                     name, status, duration, count, n_err, usage.total)
            return result
        wrapper.job_name = name  # type: ignore[attr-defined]
        return wrapper
    return decorate
