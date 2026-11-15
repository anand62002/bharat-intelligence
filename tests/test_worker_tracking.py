"""
tests/test_worker_tracking.py — every worker job writes to the job ledger (WP-005).
No live APIs or DB: job dependencies and the Supabase client are mocked.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import worker  # noqa: E402  (acceptance: `import worker` succeeds)
from data import run_log  # noqa: E402

JOBS = {n: getattr(worker, n) for n in dir(worker) if n.startswith("job_")}


def test_every_job_is_tracked_with_a_unique_name():
    names = {}
    for fn_name, fn in JOBS.items():
        assert hasattr(fn, "job_name"), f"{fn_name} is not wrapped in tracked_job"
        names.setdefault(fn.job_name, []).append(fn_name)
    dupes = {k: v for k, v in names.items() if len(v) > 1}
    assert not dupes, dupes
    assert len(JOBS) >= 25


def test_jobs_with_their_own_row_use_their_pipeline_names():
    # The orchestrator and discovery write daily_runs themselves as 'orchestrator'
    # and 'discovery'; the ledger must use the same names so a crash row matches.
    assert worker.job_orchestrator.job_name == "orchestrator"
    assert worker.job_discovery_screener.job_name == "discovery"


def test_enough_daily_jobs_for_the_acceptance_count():
    """Acceptance: >= 18 distinct pipelines on a normal day. Count jobs whose trigger fires daily."""
    sched = worker.build_scheduler()
    daily = set()
    for job in sched.get_jobs():
        fields = {f.name: str(f) for f in job.trigger.fields}
        if fields.get("day") == "*" and fields.get("day_of_week") == "*" and fields.get("month") == "*":
            daily.add(getattr(job.func, "job_name", job.id))
    assert len(daily) >= 18, sorted(daily)


@pytest.fixture
def ledger():
    c = MagicMock()
    with patch.object(run_log, "_supabase_client", return_value=c):
        yield c


def _row(c):
    return c.table.return_value.insert.call_args_list[-1].args[0]


def test_failing_dependency_records_failed_and_does_not_raise(ledger):
    with patch("agents.regime_detector.detect_regime", side_effect=RuntimeError("nse 503")):
        assert worker.job_regime_detector() is None
    row = _row(ledger)
    assert (row["pipeline"], row["status"]) == ("regime_detector", "FAILED")
    assert "nse 503" in row["details"]["error"]


def test_returning_job_records_ok_and_count(ledger):
    res = {"seeded": 4, "skipped": 10, "errors": []}
    with patch("agents.rec_outcome_seeder.run_seeder", return_value=res):
        out = worker.job_rec_outcome_seeder()
    assert out["count"] == 4
    row = _row(ledger)
    assert (row["pipeline"], row["status"], row["symbols_processed"]) == ("rec_outcome_seeder", "OK", 4)


def test_job_errors_record_warning(ledger):
    res = {"polled": 3, "live_updated": 2, "t30_resolved": 0, "errors": ["X: no price"]}
    with patch("agents.outcome_tracker.run_forward_polling", return_value=res):
        worker.job_forward_poller()
    row = _row(ledger)
    assert row["status"] == "WARNING" and row["symbols_processed"] == 2


def test_orchestrator_success_not_logged_twice(ledger):
    async def fake_pipeline(dry_run=False):
        return {"errors": [], "recommendations": [1, 2], "symbols_processed": 2}

    with patch("scheduler.orchestrator.run_pipeline", side_effect=fake_pipeline):
        out = worker.job_orchestrator()
    assert out["count"] == 2
    assert not ledger.table.return_value.insert.called


def test_orchestrator_crash_is_recorded(ledger):
    with patch("scheduler.orchestrator.run_pipeline", side_effect=RuntimeError("langgraph")):
        worker.job_orchestrator()
    row = _row(ledger)
    assert (row["pipeline"], row["status"]) == ("orchestrator", "FAILED")


def test_research_scan_failure_still_runs_gates_then_fails(ledger):
    gate = MagicMock(return_value={"checked": 1, "approved": 0, "skipped": 1, "errors": []})
    with patch("governance.research_agent.run", side_effect=RuntimeError("arxiv down")), \
         patch("governance.research_agent.auto_approve_proposals", gate), \
         patch("governance.research_agent.auto_merge_enabled", return_value=False):
        worker.job_research_agent()
    gate.assert_called_once()
    assert _row(ledger)["status"] == "FAILED"


def test_outcome_tracker_failure_still_runs_sentiment_validation(ledger):
    sv = MagicMock(return_value={"validated": 1})
    labels = MagicMock(side_effect=lambda client, h: {"horizon": h, "due": 0, "labelled": 0,
                                                      "failed": 0, "errors": []})
    with patch("agents.outcome_tracker.run_outcome_tracking", side_effect=RuntimeError("yf")), \
         patch("agents.outcome_tracker.run_sentiment_validation", sv), \
         patch("data.candidate_ledger.get_client", return_value=None), \
         patch("data.candidate_ledger.label_candidates", labels):
        worker.job_outcome_tracker()
    sv.assert_called_once()
    assert labels.call_count == 2          # candidate labels (WP-014) still run too
    assert _row(ledger)["status"] == "FAILED"


def test_options_snapshot_one_symbol_failing_is_warning(ledger):
    def fake(sym):
        if sym == "BANKNIFTY":
            raise RuntimeError("chain blocked")
        return {"signal": "NEUTRAL", "score": 50}

    with patch("agents.options_sentiment.analyse_options", side_effect=fake):
        out = worker.job_options_snapshot()
    assert out["count"] == 1
    assert _row(ledger)["status"] == "WARNING"


def test_breeze_refresh_failure_is_warning(ledger):
    with patch("data.breeze_auth.refresh_session",
               return_value={"success": False, "mode": "manual", "message": "token expired"}):
        worker.job_breeze_token_refresh()
    row = _row(ledger)
    assert row["status"] == "WARNING" and row["symbols_processed"] == 0


def test_job_runs_without_supabase():
    with patch.object(run_log, "_supabase_client", return_value=None), \
         patch("agents.regime_detector.detect_regime", return_value={"regime": "BULL", "confidence": 70}):
        out = worker.job_regime_detector()
    assert out["regime"] == "BULL"
