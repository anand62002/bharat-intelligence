"""
tests/test_pipeline_publish_fixes.py — why the portfolio pipeline published nothing.

2026-11-12 investigation: every non-discovery recommendation since 2026-09-04
was SUPPRESSED, and daily_runs said "success, 0 errors". Four compounding causes:

  1. Judges saw only the first 300 chars of the synthesis → constraint_awareness
     and risk_disclosure scored 1 from every judge.
  2. Opus 5.5 judges (always thinking) were truncated at max_tokens=500 →
     "Unterminated string … column 27" → their vote dropped → lower kappa.
  3. A truncated/failed synthesis silently became the empty fallback template,
     which the judges then (rightly) suppressed.
  4. The orchestrator's daily_runs insert failed on a non-existent column every
     day; the only rows came from discovery with errors hard-coded to 0.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scheduler import synthesis_validator as sv


# ── 1. judges see the whole synthesis ────────────────────────────────────────

def test_judges_see_full_synthesis_text():
    long_synthesis = ("Agents agree on X. " * 60) + "CIRCUIT LIMIT 10% and T+2 noted at the END."
    f = sv._synthesis_fields("ABC.NS", {"synthesis": long_synthesis, "action": "BUY"})
    assert "CIRCUIT LIMIT 10% and T+2 noted at the END." in f["synthesis_text"]


def test_market_constraints_block_reaches_judges():
    f = sv._synthesis_fields("ABC.NS", {"synthesis": "short", "market_constraints": "Circuit band 20%; T+2"})
    assert "Circuit band 20%; T+2" in f["synthesis_text"]


# ── 2. judge replies: robust parse, truncation named, effort low ─────────────

def test_parse_tolerates_brace_inside_rationale_and_trailing_text():
    text = 'Here you go: {"score": 4, "rationale": "uses {atr_stoploss} correctly"} hope that helps'
    assert sv._parse_judge_json(text) == {"score": 4, "rationale": "uses {atr_stoploss} correctly"}


def _reply(text, stop="end_turn"):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop)


def test_truncated_judge_reply_is_reported_as_truncation():
    client = MagicMock()
    client.messages.create.return_value = _reply('{"score": 3, "rationale": "The rec', stop="max_tokens")
    with pytest.raises(sv.JudgeTruncated, match="truncated at max_tokens"):
        asyncio.run(sv._call_anthropic_judge("opus", sv.JUDGE_MODEL_OPUS, "risk_disclosure", "p", client))


def test_claude_judges_run_at_low_effort_with_headroom():
    client = MagicMock()
    client.messages.create.return_value = _reply('{"score": 4, "rationale": "ok"}')
    asyncio.run(sv._call_anthropic_judge("opus", sv.JUDGE_MODEL_OPUS, "r", "p", client))
    kw = client.messages.create.call_args.kwargs
    assert kw["output_config"] == {"effort": "low"}
    assert kw["max_tokens"] >= 2000
    assert "thinking" not in kw          # disabled thinking is a 400 on Opus 5.5


# ── 3/4. run log is written by a single helper and survives a missing column ──

def test_run_log_retries_without_new_columns_before_migration():
    from data.run_log import log_daily_run
    client = MagicMock()
    inserts = []

    def insert(row):
        inserts.append(row)
        m = MagicMock()
        if "pipeline" in row:
            m.execute.side_effect = Exception("PGRST204 Could not find the 'pipeline' column")
        return m
    client.table.return_value.insert.side_effect = insert

    ok = log_daily_run(client, "orchestrator", symbols_processed=24, errors=3,
                       duration_seconds=700, status="NO_OUTPUT", details={"x": 1})
    assert ok is True
    assert inserts[0]["pipeline"] == "orchestrator" and "pipeline" not in inserts[1]
    assert inserts[1]["status"] == "NO_OUTPUT" and inserts[1]["errors"] == 3


def test_run_log_writes_pipeline_and_details_when_columns_exist():
    from data.run_log import log_daily_run
    client = MagicMock()
    assert log_daily_run(client, "discovery", symbols_processed=6, errors=0,
                         duration_seconds=10, status="OK", details={"a": 1})
    row = client.table.return_value.insert.call_args.args[0]
    assert row["pipeline"] == "discovery" and row["details"] == {"a": 1}


def test_orchestrator_run_status_names_why_nothing_published(monkeypatch):
    from scheduler import orchestrator as orch
    written = {}
    monkeypatch.setattr(orch, "_supabase", lambda: MagicMock())
    monkeypatch.setattr("data.run_log.log_daily_run",
                        lambda client, pipeline, **kw: written.update(pipeline=pipeline, **kw) or True)
    state = {
        "start_time": 0, "recommendations": [], "symbols_processed": 3, "symbols": ["A", "B", "C", "D"],
        "dry_run": False, "data_quality_summary": {},
        "errors": ["A: SUPPRESSED (κ=0.161)", "B: SYNTHESIS_FALLBACK (unparseable JSON: x)",
                   "C synthesis: truncated"],
    }
    asyncio.run(orch.log_run_node(state))
    assert written["pipeline"] == "orchestrator"
    assert written["status"] == "NO_OUTPUT"
    r = written["details"]["reasons"]
    assert r == {"suppressed_by_judges": 1, "synthesis_fallback": 1,
                 "synthesis_failed": 1, "agent_run_failed": 1}


# ── 5. the actual root cause: schema drift killed every portfolio insert ─────

def test_save_survives_a_column_missing_in_production():
    from scheduler.orchestrator import _insert_tolerating_schema_drift
    client = MagicMock()
    attempts = []

    def insert(row):
        attempts.append(dict(row))
        m = MagicMock()
        if "danger_drop_pct" in row:
            m.execute.side_effect = Exception(
                "{'code': 'PGRST204', 'message': \"Could not find the 'danger_drop_pct' column "
                "of 'recommendations' in the schema cache\"}")
        else:
            m.execute.return_value = SimpleNamespace(data=[{"id": "rec-1"}])
        return m
    client.table.return_value.insert.side_effect = insert

    errors: list[str] = []
    row = {"symbol": "TITAN.NS", "action": "HOLD", "danger_drop_pct": 12.0}
    resp = _insert_tolerating_schema_drift(client, "recommendations", row, errors)
    _insert_tolerating_schema_drift(client, "recommendations", row, errors)   # second rec

    assert resp.data == [{"id": "rec-1"}]
    assert "danger_drop_pct" not in attempts[1] and attempts[1]["symbol"] == "TITAN.NS"
    assert errors == ["SCHEMA_DRIFT: recommendations.danger_drop_pct missing"]   # once, not per rec


def test_other_insert_errors_still_raise():
    from scheduler.orchestrator import _insert_tolerating_schema_drift
    client = MagicMock()
    client.table.return_value.insert.return_value.execute.side_effect = Exception("23514 check constraint")
    with pytest.raises(Exception, match="23514"):
        _insert_tolerating_schema_drift(client, "recommendations", {"a": 1}, [])
