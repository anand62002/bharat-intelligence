"""
tests/test_schema_contract.py — db/contract.py + scripts/check_schema.py (WP-007, ADR-005).
No live DB: the Supabase client is a fake that rejects chosen columns the way PostgREST does.
"""
import ast
import logging
import sys
from collections import defaultdict
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from db.contract import CONTRACT  # noqa: E402
from scripts import check_schema as cs  # noqa: E402

RUNTIME_DIRS = ["agents", "scheduler", "data", "api", "governance"]


# ── the contract matches the code ────────────────────────────────────────────

def _db_columns_of_save_recs_node() -> set[str]:
    tree = ast.parse((ROOT / "scheduler" / "orchestrator.py").read_text(encoding="utf-8"))
    for fn in ast.walk(tree):
        if isinstance(fn, ast.AsyncFunctionDef) and fn.name == "save_recs_node":
            for n in ast.walk(fn):
                if isinstance(n, ast.Assign) and any(
                        isinstance(t, ast.Name) and t.id == "_DB_COLUMNS" for t in n.targets):
                    return {e.value for e in n.value.elts}
    raise AssertionError("save_recs_node._DB_COLUMNS not found")


def test_save_recs_db_columns_are_all_in_the_contract():
    cols = _db_columns_of_save_recs_node()
    assert "danger_drop_pct" in cols      # the column that broke production
    assert cols <= CONTRACT["recommendations"], cols - CONTRACT["recommendations"]


def _table_of(call):
    node = call
    while isinstance(node, (ast.Call, ast.Attribute)):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr == "table" and node.args \
                    and isinstance(node.args[0], ast.Constant):
                return node.args[0].value
            node = f
        else:
            node = node.value
    return None


def _dict_keys(node):
    if isinstance(node, ast.Dict):
        out = set()
        for k, v in zip(node.keys, node.values):
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                out.add(k.value)
            elif k is None:
                out |= _dict_keys(v) or set()
        return out
    return None


def _literal_writes() -> dict[str, dict[str, list[str]]]:
    """{table: {column: [file:line]}} for literal-dict writes visible in each function."""
    found: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    files = [p for d in RUNTIME_DIRS for p in (ROOT / d).rglob("*.py")] + [ROOT / "worker.py"]
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            assigned: dict[str, set] = defaultdict(set)
            for n in ast.walk(fn):
                if isinstance(n, ast.Assign):
                    for t in n.targets:
                        if isinstance(t, ast.Name) and (ks := _dict_keys(n.value)):
                            assigned[t.id] |= ks
            for n in ast.walk(fn):
                if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                        and n.func.attr in ("insert", "update", "upsert") and n.args):
                    continue
                table = _table_of(n.func.value)
                if table not in CONTRACT:
                    continue
                arg = n.args[0]
                ks = _dict_keys(arg)
                if ks is None and isinstance(arg, ast.Name):
                    ks = assigned.get(arg.id)
                for k in ks or ():
                    found[table][k].append(f"{path.relative_to(ROOT)}:{n.lineno}")
    return found


def test_every_literal_write_column_is_in_the_contract():
    """Add a column to an insert/update → add it to db/contract.py in the same change."""
    gaps = {f"{t}.{c}": sites for t, cols in _literal_writes().items()
            for c, sites in cols.items() if c not in CONTRACT[t]}
    assert not gaps, f"columns written but not in db/contract.py: {gaps}"


def test_columns_that_never_existed_are_not_written_any_more():
    writes = _literal_writes()
    assert "portfolio_id" not in writes["portfolio_alerts"]
    assert "notes" not in writes["agent_performance"]


# ── the checker ──────────────────────────────────────────────────────────────

class FakeClient:
    """Rejects columns like PostgREST: one missing column named per error."""

    def __init__(self, missing=None, missing_tables=(), style="42703", other_error=None):
        self.missing = {t: set(c) for t, c in (missing or {}).items()}
        self.missing_tables = set(missing_tables)
        self.style = style
        self.other_error = other_error
        self.selects = []

    def table(self, name):
        client = self

        class Q:
            def select(self, cols):
                self.cols = cols.split(",")
                client.selects.append((name, cols))
                return self

            def limit(self, n):
                return self

            def execute(self):
                if client.other_error:
                    raise Exception(client.other_error)
                if name in client.missing_tables:
                    raise Exception("{'code': 'PGRST205', 'message': \"Could not find the table "
                                    f"'public.{name}' in the schema cache\"}}")
                for c in self.cols:
                    if c in client.missing.get(name, ()):
                        if client.style == "42703":
                            raise Exception(f"{{'code': '42703', 'message': 'column {name}.{c} does not exist'}}")
                        raise Exception(f"{{'code': 'PGRST204', 'message': \"Could not find the '{c}' column\"}}")
                return MagicMock(data=[])
        return Q()


def test_checker_reports_a_missing_column():
    res = cs.check_schema(FakeClient({"recommendations": {"danger_drop_pct"}}))
    assert res["recommendations"] == ["danger_drop_pct"]
    assert cs.missing_only(res) == {"recommendations": ["danger_drop_pct"]}


def test_checker_finds_every_missing_column_one_error_at_a_time():
    c = FakeClient({"portfolio_holdings": {"qty", "notes", "sector"}})
    assert cs.check_table(c, "portfolio_holdings", CONTRACT["portfolio_holdings"]) == \
        ["notes", "qty", "sector"]


def test_checker_understands_pgrst204_wording():
    c = FakeClient({"daily_runs": {"pipeline"}}, style="PGRST204")
    assert cs.check_table(c, "daily_runs", CONTRACT["daily_runs"]) == ["pipeline"]


def test_missing_table_is_reported():
    res = cs.check_schema(FakeClient(missing_tables={"long_horizon_picks"}))
    assert res["long_horizon_picks"] == [cs.TABLE_MISSING]


def test_other_errors_are_reported_not_raised():
    res = cs.check_schema(FakeClient(other_error="connection refused"))
    assert all(v[0].startswith("<error: connection refused") for v in res.values())


def test_healthy_schema_and_one_line_per_table():
    res = cs.check_schema(FakeClient())
    assert cs.missing_only(res) == {}
    report = cs.format_report(res)
    assert len(report.splitlines()) == len(CONTRACT)
    assert "recommendations" in report and "OK (" in report


def test_selects_use_limit_zero_and_only_contract_columns():
    c = FakeClient()
    cs.check_schema(c)
    for table, cols in c.selects:
        assert set(cols.split(",")) == CONTRACT[table]


def test_main_exit_codes(capsys):
    with patch.object(cs, "_client", return_value=FakeClient()):
        assert cs.main() == 0
    with patch.object(cs, "_client", return_value=FakeClient({"recommendations": {"danger_drop_pct"}})):
        assert cs.main() == 1
    out = capsys.readouterr().out
    assert "recommendations" in out and "MISSING: danger_drop_pct" in out


# ── worker start-up and weekly audit ─────────────────────────────────────────

def test_worker_startup_logs_critical_per_missing_column_and_continues(caplog):
    import worker
    fake = FakeClient({"recommendations": {"danger_drop_pct"}, "daily_runs": {"pipeline", "details"}})
    with patch.object(worker, "_service_client", return_value=fake), caplog.at_level(logging.CRITICAL):
        gaps = worker.startup_schema_check()
    crit = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert len(crit) == 3
    assert gaps["daily_runs"] == ["details", "pipeline"]


def test_worker_startup_never_raises():
    import worker
    with patch.object(worker, "_service_client", side_effect=Exception("no env")):
        assert "error" in worker.startup_schema_check()


def test_worker_main_runs_the_check_before_scheduling():
    import worker
    src = ast.unparse(next(n for n in ast.parse((ROOT / "worker.py").read_text(encoding="utf-8")).body
                           if isinstance(n, ast.FunctionDef) and n.name == "main"))
    assert src.index("startup_schema_check()") < src.index("build_scheduler()")


def test_weekly_audit_schema_check():
    from scripts import weekly_audit as wa
    assert any(k == "schema_contract" for k, _, _ in wa.CHECKS)
    assert wa.check_schema_contract(FakeClient())["status"] == wa.PASS
    r = wa.check_schema_contract(FakeClient({"recommendations": {"danger_drop_pct"}}))
    assert r["status"] == wa.FAIL and "danger_drop_pct" in r["detail"]
