"""
tests/test_worker_schedule.py — job ordering in the worker schedule (WP-011).

The orchestrator (06:00 IST) reads the latest market_regime row. If the regime
job runs after it, every decision uses yesterday's regime. These tests build
the scheduler without starting it and check the ordering from the triggers.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import worker  # noqa: E402


def _next_fire(scheduler, job_id: str, after: datetime) -> datetime:
    job = scheduler.get_job(job_id)
    assert job is not None, f"job {job_id!r} not scheduled"
    return job.trigger.get_next_fire_time(None, after)


def _scheduler():
    sched = worker.build_scheduler()
    assert not sched.running
    return sched


def test_worker_imports():
    assert callable(worker.build_scheduler)


def test_regime_fires_before_orchestrator_every_weekday():
    sched = _scheduler()
    start = datetime(2026, 11, 16, 0, 0, tzinfo=worker.IST)  # a Monday
    for day in range(7):
        midnight = start + timedelta(days=day)
        regime = _next_fire(sched, "regime_detector", midnight)
        orch = _next_fire(sched, "orchestrator", midnight)
        assert regime.date() == orch.date() == midnight.date()
        assert regime < orch, f"regime {regime:%H:%M} must precede orchestrator {orch:%H:%M}"


def test_regime_runs_at_0545_ist():
    sched = _scheduler()
    t = _next_fire(sched, "regime_detector", datetime(2026, 11, 16, 0, 0, tzinfo=worker.IST))
    assert (t.hour, t.minute) == (5, 45)


def test_morning_digest_still_precedes_orchestrator():
    sched = _scheduler()
    midnight = datetime(2026, 11, 16, 0, 0, tzinfo=worker.IST)
    digest = next(j for j in sched.get_jobs() if j.func is worker.job_market_digest_morning)
    assert digest.trigger.get_next_fire_time(None, midnight) < _next_fire(sched, "orchestrator", midnight)
