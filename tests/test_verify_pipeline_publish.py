"""
tests/test_verify_pipeline_publish.py — WP-001.

Verdict for each of the five cases in docs/architecture/REVIEW.md section 3,
plus the exit-code rule and main() against a mocked Supabase client.
"""
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import verify_pipeline_publish as vp  # noqa: E402


def _run(status="OK", errors=None, pipeline="orchestrator"):
    return {"pipeline": pipeline, "status": status, "symbols_processed": 24,
            "errors": len(errors or []), "details": {"reasons": {}, "errors": errors or []}}


# ── the five verdicts ────────────────────────────────────────────────────────

def test_published():
    assert vp.verdict(_run("OK"), ["BUY", "HOLD", "SUPPRESSED"]) == vp.PUBLISHED


def test_published_with_other_errors_is_still_published():
    run = _run("WARNING", ["SCHEMA_DRIFT: recommendations.danger_drop_pct missing",
                           "TCS.NS: agent_run_failed"])
    assert vp.verdict(run, ["BUY"]) == vp.PUBLISHED


def test_schema_drift_only():
    run = _run("WARNING", ["SCHEMA_DRIFT: recommendations.danger_drop_pct missing"])
    assert vp.verdict(run, ["BUY", "HOLD"]) == vp.SCHEMA_DRIFT_ONLY


def test_all_suppressed():
    assert vp.verdict(_run("ALL_SUPPRESSED"), ["SUPPRESSED"] * 3) == vp.ALL_SUPPRESSED


def test_all_suppressed_from_run_status_without_rows():
    assert vp.verdict(_run("ALL_SUPPRESSED"), []) == vp.ALL_SUPPRESSED


def test_all_suppressed_rows_without_run_row():
    """Run-log insert can fail while suppressed rows still land (the pre-bf5d3fa state)."""
    assert vp.verdict(None, ["SUPPRESSED", "suppressed"]) == vp.ALL_SUPPRESSED


def test_no_output():
    assert vp.verdict(_run("NO_OUTPUT"), []) == vp.NO_OUTPUT


def test_no_run():
    assert vp.verdict(None, []) == vp.NO_RUN


def test_published_without_run_row():
    assert vp.verdict(None, ["BUY"]) == vp.PUBLISHED


# ── exit code: 0 only when a non-SUPPRESSED row exists ──────────────────────

@pytest.mark.parametrize("v,code", [
    (vp.PUBLISHED, 0), (vp.SCHEMA_DRIFT_ONLY, 0),
    (vp.ALL_SUPPRESSED, 1), (vp.NO_OUTPUT, 1), (vp.NO_RUN, 1),
])
def test_exit_code(v, code):
    assert vp.exit_code(v) == code


# ── picking the orchestrator row ────────────────────────────────────────────

def test_pick_row_by_pipeline_ignores_discovery():
    rows = [_run("OK", pipeline="discovery"), _run("WARNING")]
    assert vp.pick_orchestrator_row(rows, True)["status"] == "WARNING"


def test_pick_row_without_pipeline_column_uses_orchestrator_only_statuses():
    rows = [{"status": "OK"}, {"status": "NO_OUTPUT"}]
    assert vp.pick_orchestrator_row(rows, False)["status"] == "NO_OUTPUT"
    assert vp.pick_orchestrator_row([{"status": "OK"}], False) is None


# ── main() end to end with a mocked, read-only client ───────────────────────

def _client(run_rows, rec_rows):
    client = MagicMock()
    client.handles = []

    def table(name):
        t = MagicMock()
        q = t.select.return_value
        for m in ("eq", "gte", "lt", "order"):
            getattr(q, m).return_value = q
        q.execute.return_value = MagicMock(data=run_rows if name == "daily_runs" else rec_rows)
        client.handles.append((name, t))
        return t

    client.table.side_effect = table
    return client


def test_main_exit_0_when_published(capsys):
    c = _client([_run("OK")], [{"action": "BUY"}, {"action": "HOLD"}])
    with patch.object(vp, "_client", return_value=c):
        assert vp.main(["--date", "2026-11-15"]) == 0
    out = capsys.readouterr().out
    assert "VERDICT: PUBLISHED" in out and "BUY" in out


def test_main_exit_1_when_all_suppressed(capsys):
    c = _client([_run("ALL_SUPPRESSED")], [{"action": "SUPPRESSED"}])
    with patch.object(vp, "_client", return_value=c):
        assert vp.main(["--date", "2026-11-15"]) == 1
    assert "VERDICT: ALL_SUPPRESSED" in capsys.readouterr().out


def test_main_never_writes():
    c = _client([], [])
    with patch.object(vp, "_client", return_value=c):
        assert vp.main(["--date", "2026-11-15"]) == 1   # NO_RUN
    assert {name for name, _ in c.handles} == {"daily_runs", "recommendations"}
    for _, t in c.handles:
        assert t.select.called
        for verb in ("insert", "update", "upsert", "delete"):
            assert not getattr(t, verb).called


def test_main_falls_back_when_pipeline_column_missing(capsys):
    """Before add_daily_runs_pipeline.sql: first select fails, retry without pipeline/details."""
    c = MagicMock()
    calls = {"n": 0}

    def table(name):
        t = MagicMock()
        q = t.select.return_value
        for m in ("eq", "gte", "lt", "order"):
            getattr(q, m).return_value = q
        if name == "daily_runs":
            calls["n"] += 1
            if calls["n"] == 1:
                q.execute.side_effect = Exception("column daily_runs.pipeline does not exist (42703)")
            else:
                q.execute.return_value = MagicMock(data=[{"status": "OK"}, {"status": "WARNING"}])
        else:
            q.execute.return_value = MagicMock(data=[{"action": "BUY"}])
        return t

    c.table.side_effect = table
    with patch.object(vp, "_client", return_value=c):
        assert vp.main(["--date", "2026-11-15"]) == 0
    out = capsys.readouterr().out
    assert "add_daily_runs_pipeline.sql pending" in out
    assert "status=WARNING" in out
