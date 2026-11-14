"""
tests/test_run_log.py — data/run_log.py: log_daily_run + tracked_job (WP-005, ADR-005).
No live DB: the Supabase client is a MagicMock.
"""
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data import run_log  # noqa: E402
from data.run_log import log_daily_run, tracked_job  # noqa: E402


def _inserted(client) -> dict:
    return client.table.return_value.insert.call_args_list[-1].args[0]


@pytest.fixture
def client():
    c = MagicMock()
    with patch.object(run_log, "_supabase_client", return_value=c):
        yield c


# ── tracked_job ──────────────────────────────────────────────────────────────

def test_raising_job_writes_failed_row_and_does_not_propagate(client, caplog):
    @tracked_job("boom_job")
    def job():
        raise ValueError("source down")

    with caplog.at_level(logging.ERROR):
        assert job() is None                      # swallowed, scheduler keeps running
    row = _inserted(client)
    assert row["pipeline"] == "boom_job"
    assert row["status"] == "FAILED"
    assert row["errors"] == 1
    assert row["details"]["error"] == "ValueError: source down"
    assert "Traceback" in row["details"]["traceback"]
    assert "Job boom_job failed" in caplog.text


def test_returning_job_writes_ok_with_count(client):
    @tracked_job("good_job")
    def job():
        return {"count": 24, "saved": 3}

    assert job() == {"count": 24, "saved": 3}
    row = _inserted(client)
    assert (row["pipeline"], row["status"], row["symbols_processed"], row["errors"]) == \
        ("good_job", "OK", 24, 0)
    assert row["details"]["output"] == {"count": 24, "saved": 3}
    assert row["duration_seconds"] >= 0


def test_returned_errors_make_warning(client):
    @tracked_job("partial_job")
    def job():
        return {"count": 5, "errors": ["TCS: timeout", "INFY: 403"]}

    job()
    row = _inserted(client)
    assert row["status"] == "WARNING" and row["errors"] == 2


def test_integer_errors_field_counts(client):
    @tracked_job("rag_like")
    def job():
        return {"count": 1, "errors": 3}

    job()
    assert _inserted(client)["errors"] == 3


def test_none_result_is_ok_with_zero_count(client):
    @tracked_job("silent_job")
    def job():
        return None

    job()
    row = _inserted(client)
    assert row["status"] == "OK" and row["symbols_processed"] == 0
    assert row["details"]["output"] == {}


def test_dataclass_and_list_results_are_stored(client):
    @dataclass
    class Res:
        count: int
        note: str

    @tracked_job("dc_job")
    def dc():
        return Res(7, "x")

    @tracked_job("list_job")
    def lst():
        return ["a", "b"]

    dc()
    assert _inserted(client)["details"]["output"] == {"count": 7, "note": "x"}
    lst()
    row = _inserted(client)
    assert row["symbols_processed"] == 2 and row["details"]["output"]["items"] == ["a", "b"]


def test_large_output_is_trimmed(client):
    @tracked_job("big_job")
    def job():
        return {"count": 1, "blob": "x" * 50_000}

    job()
    out = _inserted(client)["details"]["output"]
    assert out["count"] == 1
    assert out["blob"].startswith("<truncated")


def test_missing_supabase_client_does_not_crash(caplog):
    calls = []

    @tracked_job("no_db_job")
    def job():
        calls.append(1)
        return {"count": 1}

    with patch.object(run_log, "_supabase_client", return_value=None), \
         caplog.at_level(logging.WARNING):
        assert job() == {"count": 1}
    assert calls == [1]
    assert "no Supabase client" in caplog.text


def test_missing_client_env_returns_none(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    assert run_log._supabase_client() is None


def test_insert_failure_does_not_crash_job(client):
    client.table.return_value.insert.return_value.execute.side_effect = Exception("db down")

    @tracked_job("db_down_job")
    def job():
        return {"count": 2}

    assert job() == {"count": 2}


def test_logs_own_row_skips_success_but_records_failure(client):
    @tracked_job("orchestrator", logs_own_row=True)
    def ok_job():
        return {"count": 24}

    @tracked_job("orchestrator", logs_own_row=True)
    def bad_job():
        raise RuntimeError("graph crashed")

    ok_job()
    assert not client.table.return_value.insert.called       # its own row covers success
    bad_job()
    row = _inserted(client)
    assert row["pipeline"] == "orchestrator" and row["status"] == "FAILED"


def test_llm_usage_recorded_in_details(client):
    from data.llm_utils import first_text, record_usage

    class Usage:
        input_tokens = 100
        output_tokens = 40

    class Block:
        type = "text"
        text = "{}"

    class Resp:
        usage = Usage()
        content = [Block()]

    @tracked_job("llm_job")
    def job():
        first_text(Resp())
        record_usage({"usage": {"input_tokens": 10, "output_tokens": 2}})   # raw-HTTP body
        return {"count": 1}

    job()
    usage = _inserted(client)["details"]["llm_usage"]
    assert usage["calls"] == 2
    assert (usage["input_tokens"], usage["output_tokens"]) == (110, 42)


def test_llm_usage_scopes_do_not_leak_between_jobs(client):
    from data.llm_utils import record_usage

    @tracked_job("a")
    def a():
        record_usage({"usage": {"input_tokens": 5, "output_tokens": 5}})

    @tracked_job("b")
    def b():
        return None

    a()
    b()
    assert _inserted(client)["details"]["llm_usage"]["calls"] == 0


def test_llm_usage_reaches_to_thread_workers(client):
    import asyncio
    from data.llm_utils import record_usage

    @tracked_job("async_job")
    def job():
        async def main():
            await asyncio.to_thread(record_usage, {"usage": {"input_tokens": 7, "output_tokens": 1}})
        asyncio.run(main())

    job()
    assert _inserted(client)["details"]["llm_usage"]["input_tokens"] == 7


def test_record_usage_never_raises():
    from data.llm_utils import record_usage
    record_usage(None)
    record_usage({"usage": "garbage"})
    record_usage(object())


# ── log_daily_run (existing behaviour) ───────────────────────────────────────

def test_log_daily_run_falls_back_to_base_columns():
    c = MagicMock()
    c.table.return_value.insert.return_value.execute.side_effect = [
        Exception("PGRST204 Could not find the 'pipeline' column"), MagicMock()]
    assert log_daily_run(c, "x", symbols_processed=1, errors=0, duration_seconds=1.0, status="OK")
    second = c.table.return_value.insert.call_args_list[1].args[0]
    assert "pipeline" not in second and "details" not in second
