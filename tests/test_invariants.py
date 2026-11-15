"""
tests/test_invariants.py — scheduler/invariants.py + job_watchdog (WP-006, ADR-005).
No live DB or network: Supabase is a MagicMock and the trading calendar is injected.
"""
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scheduler import invariants as inv  # noqa: E402
from scheduler.invariants import Context, check_invariants  # noqa: E402  (acceptance import)

MON = date(2026, 11, 16)
SAT = date(2026, 11, 14)
OPEN = lambda d: d.weekday() < 5   # noqa: E731  injected weekday calendar


def healthy_rows() -> list[dict]:
    return [
        {"pipeline": "orchestrator", "status": "OK",
         "details": {"recommendations": 24, "reasons": {}}},
        {"pipeline": "discovery", "status": "OK", "details": {}},
        {"pipeline": "forward_poller", "status": "OK",
         "details": {"output": {"polled": 100, "live_updated": 98}}},
        {"pipeline": "paper_portfolio_update", "status": "OK",
         "details": {"output": {"updated": 40, "closed": 1, "snapshot": {"open_positions": 41}}}},
        {"pipeline": "discovery_rescorer", "status": "OK",
         "details": {"output": {"symbols": 60, "errors": []}}},
        {"pipeline": "outcome_tracker", "status": "OK", "details": {"output": {"tracked": 0}}},
        {"pipeline": "target_updater", "status": "OK",
         "details": {"output": {"total_holdings": 24}}},
    ]


def _client(rows, alerts_existing=None):
    """daily_runs → rows; portfolio_alerts select → alerts_existing; inserts recorded."""
    c = MagicMock()
    c.inserted = []

    def table(name):
        t = MagicMock()
        if name == "daily_runs":
            t.select.return_value.eq.return_value.order.return_value.execute.return_value = \
                MagicMock(data=rows)
        elif name == "portfolio_holdings":
            t.select.return_value.eq.return_value.limit.return_value.execute.return_value = \
                MagicMock(count=24)
        elif name == "portfolio_alerts":
            q = t.select.return_value.eq.return_value.like.return_value.limit.return_value
            q.execute.side_effect = lambda: MagicMock(data=list(alerts_existing or []))
            t.insert.side_effect = lambda row: (c.inserted.append(row), MagicMock())[1]
        return t

    c.table.side_effect = table
    return c


def _check(rows, day=MON, **kw):
    return check_invariants(_client(rows), day, trading_day=OPEN,
                            context=kw.pop("context", Context(open_holdings=24)), **kw)


# ── the cases the work package names ─────────────────────────────────────────

def test_healthy_day_has_no_breaches():
    assert _check(healthy_rows()) == []


def test_missing_row_is_exactly_one_breach():
    rows = [r for r in healthy_rows() if r["pipeline"] != "forward_poller"]
    b = _check(rows)
    assert [(x.job, x.kind) for x in b] == [("forward_poller", "MISSING")]


def test_failed_row_is_exactly_one_breach():
    rows = healthy_rows()
    rows[5] = {"pipeline": "outcome_tracker", "status": "FAILED",
               "details": {"error": "RuntimeError: yf"}}
    b = _check(rows)
    assert [(x.job, x.kind) for x in b] == [("outcome_tracker", "FAILED")]
    assert "RuntimeError: yf" in b[0].message


def test_zero_output_orchestrator_is_exactly_one_breach():
    rows = healthy_rows()
    rows[0] = {"pipeline": "orchestrator", "status": "OK", "details": {"recommendations": 0}}
    b = _check(rows)
    assert [(x.job, x.kind) for x in b] == [("orchestrator", "OUTPUT")]
    assert "0 recommendations" in b[0].message


def test_non_trading_day_skips_trading_jobs():
    """Saturday: only the daily jobs (orchestrator, outcome tracker) are due."""
    rows = [r for r in healthy_rows() if r["pipeline"] in ("orchestrator", "outcome_tracker")]
    assert _check(rows, day=SAT) == []
    # …and a missing daily job is still caught on a weekend
    b = _check([rows[0]], day=SAT)
    assert [(x.job, x.kind) for x in b] == [("outcome_tracker", "MISSING")]


def test_exchange_holiday_skips_trading_jobs():
    rows = [r for r in healthy_rows() if r["pipeline"] in ("orchestrator", "outcome_tracker")]
    assert check_invariants(_client(rows), MON, trading_day=lambda d: False,
                            context=Context(open_holdings=24)) == []


# ── per-job rules ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status", ["ALL_SUPPRESSED", "NO_OUTPUT"])
def test_orchestrator_bad_status(status):
    rows = healthy_rows()
    rows[0] = {"pipeline": "orchestrator", "status": status,
               "details": {"recommendations": 0, "reasons": {"suppressed_by_judges": 24}}}
    b = _check(rows)
    assert b[0].kind == "OUTPUT" and status in b[0].message


def test_orchestrator_zero_recs_ok_without_holdings():
    rows = healthy_rows()
    rows[0] = {"pipeline": "orchestrator", "status": "OK", "details": {"recommendations": 0}}
    rows[6]["details"]["output"]["total_holdings"] = 0
    assert _check(rows, context=Context(open_holdings=0)) == []


def test_forward_poller_under_90_percent():
    rows = healthy_rows()
    rows[2]["details"]["output"] = {"polled": 100, "live_updated": 80}
    assert [x.job for x in _check(rows)] == ["forward_poller"]


def test_paper_update_without_snapshot_or_movement():
    rows = healthy_rows()
    rows[3]["details"]["output"] = {"updated": 40, "closed": 0, "snapshot": None}
    assert [x.job for x in _check(rows)] == ["paper_portfolio_update"]
    rows[3]["details"]["output"] = {"updated": 0, "closed": 0, "snapshot": {"open_positions": 41}}
    assert "0 of 41" in _check(rows)[0].message


def test_rescorer_all_symbols_failed():
    rows = healthy_rows()
    rows[4]["details"]["output"] = {"symbols": 3, "errors": ["a", "b", "c"]}
    assert [x.job for x in _check(rows)] == ["discovery_rescorer"]


def test_target_updater_reviewed_nothing():
    rows = healthy_rows()
    rows[6]["details"]["output"] = {"total_holdings": 0}
    assert [x.job for x in _check(rows)] == ["target_updater"]


def test_latest_row_per_job_wins():
    """A rerun that succeeded after a crash clears the breach (rows arrive newest first)."""
    rows = healthy_rows()
    rows.insert(3, {"pipeline": "forward_poller", "status": "FAILED", "details": {}})  # older
    assert _check(rows) == []
    rows.insert(0, {"pipeline": "forward_poller", "status": "FAILED", "details": {}})  # newest
    assert [(x.job, x.kind) for x in _check(rows)] == [("forward_poller", "FAILED")]


def test_unreadable_daily_runs_is_a_breach_not_an_exception():
    c = MagicMock()
    c.table.return_value.select.return_value.eq.return_value.order.return_value.execute.side_effect = \
        Exception("db down")
    b = check_invariants(c, MON, trading_day=OPEN, context=Context(open_holdings=1))
    assert [(x.job, x.kind) for x in b] == [("watchdog", "FAILED")]


def test_rule_that_raises_becomes_a_breach(monkeypatch):
    bad = inv.Invariant("outcome_tracker", "daily", lambda r, c: 1 / 0, "x")
    monkeypatch.setattr(inv, "INVARIANTS", [bad])
    b = _check(healthy_rows())
    assert b[0].kind == "OUTPUT" and "raised" in b[0].message


def test_check_range_covers_each_day_oldest_first():
    seen = []
    with patch.object(inv, "check_invariants", side_effect=lambda c, d, **kw: seen.append(d) or []):
        assert inv.check_range(MagicMock(), end=MON, days=3, context=Context()) == []
    assert seen == [date(2026, 11, 14), date(2026, 11, 15), MON]


def test_trading_calendar_weekend_and_holiday():
    assert inv.is_trading_day(SAT) is False
    empty = MagicMock()
    empty.history.return_value = MagicMock(empty=True)
    with patch("yfinance.Ticker", return_value=empty):
        assert inv.is_trading_day(date(2026, 11, 10)) is False      # past weekday, no bar
    with patch("yfinance.Ticker", side_effect=Exception("net")):
        assert inv.is_trading_day(date(2026, 11, 10)) is True       # unknown → assume open


# ── alerts: one CRITICAL row per job per day ─────────────────────────────────

def test_raise_alerts_inserts_critical_rows():
    b = _check([r for r in healthy_rows() if r["pipeline"] != "discovery"])
    c = _client([])
    res = inv.raise_alerts(c, b)
    assert res == {"inserted": 1, "duplicates": 0, "errors": []}
    row = c.inserted[0]
    assert row["severity"] == "CRITICAL" and row["alert_type"] == "JOB_INVARIANT"
    assert row["title"].startswith("Job invariant: discovery 2026-11-16")


def test_raise_alerts_deduplicates_per_job_per_day():
    b = _check([r for r in healthy_rows() if r["pipeline"] != "discovery"])
    c = _client([], alerts_existing=[{"id": 1}])
    res = inv.raise_alerts(c, b)
    assert res["inserted"] == 0 and res["duplicates"] == 1 and c.inserted == []


def test_raise_alerts_never_raises():
    c = MagicMock()
    c.table.side_effect = Exception("db down")
    res = inv.raise_alerts(c, [inv.Breach("x", "2026-11-16", "MISSING", "m")])
    assert res["inserted"] == 0 and len(res["errors"]) == 1


# ── the 19:00 watchdog job ───────────────────────────────────────────────────

def test_watchdog_drill_one_disabled_job_gives_one_alert():
    """Acceptance drill, offline: one job missing → exactly one JOB_INVARIANT alert."""
    import worker
    from data import run_log
    rows = [r for r in healthy_rows() if r["pipeline"] != "target_updater"]
    db = _client(rows)
    ledger = MagicMock()
    real = inv.check_invariants
    with patch.object(worker, "_service_client", return_value=db), \
         patch.object(run_log, "_supabase_client", return_value=ledger), \
         patch("scheduler.invariants.check_invariants",
               side_effect=lambda c: real(c, MON, trading_day=OPEN,
                                          context=Context(open_holdings=24))):
        out = worker.job_watchdog()
    assert out["count"] == 1
    assert len(db.inserted) == 1 and db.inserted[0]["alert_type"] == "JOB_INVARIANT"
    ledger_row = ledger.table.return_value.insert.call_args.args[0]
    assert ledger_row["pipeline"] == "job_watchdog" and ledger_row["status"] == "WARNING"


def test_watchdog_scheduled_at_1900():
    import worker
    job = worker.build_scheduler().get_job("job_watchdog")
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert (fields["hour"], fields["minute"]) == ("19", "0")


def test_every_invariant_names_a_real_job():
    import worker
    names = {getattr(worker, n).job_name for n in dir(worker) if n.startswith("job_")}
    for i in inv.INVARIANTS:
        assert i.job in names, f"invariant for unknown job {i.job}"
