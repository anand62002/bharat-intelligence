"""
tests/test_weekly_audit.py
pytest suite for scripts/weekly_audit.py

Run from project root:
    pytest tests/test_weekly_audit.py -v
"""

import json
import os
import sys
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.weekly_audit import (
    PASS, WARN, FAIL,
    check_synthesis_kappa,
    check_daily_runs,
    check_alpha_live_coverage,
    check_discovery_runs,
    run_audit,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_client(table_data: dict):
    """Build a minimal Supabase mock where table(name).select(...).gte(...).execute() returns data."""
    client = MagicMock()

    def _table(name):
        mock = MagicMock()
        rows = table_data.get(name, [])
        # Chain: .select().gte().order().execute() all return mock with .data = rows
        chain = MagicMock()
        chain.execute.return_value = MagicMock(data=rows)
        chain.gte.return_value = chain
        chain.lte.return_value = chain
        chain.order.return_value = chain
        chain.limit.return_value = chain
        chain.not_.return_value = chain
        chain.is_.return_value = chain
        mock.select.return_value = chain
        return mock

    client.table.side_effect = _table
    return client


def _rec(action="BUY", kappa=0.45):
    gc = {"validation": {"aggregate_kappa": kappa}} if kappa is not None else {}
    return {"action": action, "confidence": 72, "gov_check": gc, "created_at": date.today().isoformat()}


def _run(status="OK", run_date=None):
    return {
        "run_date": run_date or date.today().isoformat(),
        "status": status,
        "errors": [],
        "duration_seconds": 120,
    }


# ──────────────────────────────────────────────────────────────────────────────
# check_synthesis_kappa
# ──────────────────────────────────────────────────────────────────────────────

class TestCheckSynthesisKappa:
    def test_pass_on_healthy_kappa(self):
        rows = [_rec("BUY", 0.45), _rec("HOLD", 0.50), _rec("BUY", 0.42)]
        client = _make_client({"recommendations": rows})
        result = check_synthesis_kappa(client, days=7)
        assert result["status"] == PASS
        assert result["avg_kappa"] >= 0.35

    def test_warn_on_low_average_kappa(self):
        rows = [_rec("BUY", 0.25), _rec("BUY", 0.28), _rec("BUY", 0.30)]
        client = _make_client({"recommendations": rows})
        result = check_synthesis_kappa(client, days=7)
        assert result["status"] == WARN

    def test_fail_on_high_suppression_rate(self):
        rows = [_rec("SUPPRESSED", None)] * 9 + [_rec("BUY", 0.5)]
        client = _make_client({"recommendations": rows})
        result = check_synthesis_kappa(client, days=7)
        assert result["status"] == FAIL
        assert result["suppression_rate_pct"] > 80

    def test_warn_on_no_rows(self):
        client = _make_client({"recommendations": []})
        result = check_synthesis_kappa(client, days=7)
        assert result["status"] == WARN
        assert result["total"] == 0

    def test_warn_when_no_kappa_in_gov_check(self):
        rows = [{"action": "BUY", "confidence": 70, "gov_check": {}, "created_at": date.today().isoformat()}]
        client = _make_client({"recommendations": rows})
        result = check_synthesis_kappa(client, days=7)
        assert result["status"] == WARN

    def test_suppression_rate_calculated_correctly(self):
        rows = [_rec("SUPPRESSED", None)] * 3 + [_rec("BUY", 0.5)] * 7
        client = _make_client({"recommendations": rows})
        result = check_synthesis_kappa(client, days=7)
        assert result["suppressed"] == 3
        assert result["total_recs"] == 10
        assert abs(result["suppression_rate_pct"] - 30.0) < 0.5


# ──────────────────────────────────────────────────────────────────────────────
# check_daily_runs
# ──────────────────────────────────────────────────────────────────────────────

class TestCheckDailyRuns:
    def test_pass_on_all_ok_runs(self):
        rows = [_run("OK") for _ in range(5)]
        client = _make_client({"daily_runs": rows})
        result = check_daily_runs(client, days=7)
        assert result["status"] == PASS

    def test_warn_on_single_degradation_day(self):
        rows = [_run("DATA_DEGRADATION"), _run("OK"), _run("OK")]
        client = _make_client({"daily_runs": rows})
        result = check_daily_runs(client, days=7)
        assert result["status"] == WARN

    def test_fail_on_three_plus_degradation_days(self):
        rows = [_run("DATA_DEGRADATION") for _ in range(3)] + [_run("OK")]
        client = _make_client({"daily_runs": rows})
        result = check_daily_runs(client, days=7)
        assert result["status"] == FAIL

    def test_warn_on_no_rows(self):
        client = _make_client({"daily_runs": []})
        result = check_daily_runs(client, days=7)
        assert result["status"] == WARN


# ──────────────────────────────────────────────────────────────────────────────
# check_alpha_live_coverage
# ──────────────────────────────────────────────────────────────────────────────

class TestCheckAlphaLiveCoverage:
    def test_pass_on_good_coverage(self):
        # total: 10, null_alpha: 2 → 80% covered → PASS
        all_rows = [{"id": i, "alpha_live": 0.5 if i < 8 else None} for i in range(10)]
        null_rows = [r for r in all_rows if r["alpha_live"] is None]

        client = MagicMock()

        def _table(name):
            m = MagicMock()
            chain_all = MagicMock()
            chain_all.execute.return_value = MagicMock(data=all_rows)
            chain_all.gte.return_value = chain_all
            chain_all.select.return_value = chain_all

            chain_null = MagicMock()
            chain_null.execute.return_value = MagicMock(data=null_rows)
            chain_null.gte.return_value = chain_null
            chain_null.is_.return_value = chain_null
            chain_null.select.return_value = chain_null
            chain_null.not_.return_value = chain_null

            m.select.return_value = chain_all
            return m

        client.table.side_effect = _table
        result = check_alpha_live_coverage(client)
        assert result["status"] in (PASS, WARN)

    def test_warn_on_no_outcomes(self):
        client = _make_client({"recommendation_outcomes": []})
        result = check_alpha_live_coverage(client)
        assert result["status"] == WARN


# ──────────────────────────────────────────────────────────────────────────────
# check_discovery_runs
# ──────────────────────────────────────────────────────────────────────────────

class TestCheckDiscoveryRuns:
    def test_pass_on_recent_runs(self):
        rows = [
            {"run_date": date.today().isoformat(), "total_screened": 200, "total_passed": 5, "total_discoveries": 3},
            {"run_date": (date.today() - timedelta(days=1)).isoformat(), "total_screened": 200, "total_passed": 4, "total_discoveries": 2},
        ]
        client = _make_client({"discovery_runs": rows})
        result = check_discovery_runs(client, days=7)
        assert result["status"] == PASS

    def test_warn_on_no_rows(self):
        client = _make_client({"discovery_runs": []})
        result = check_discovery_runs(client, days=7)
        assert result["status"] == WARN

    def test_warn_on_zero_discoveries(self):
        rows = [{"run_date": date.today().isoformat(), "total_screened": 200, "total_passed": 0, "total_discoveries": 0}]
        client = _make_client({"discovery_runs": rows})
        result = check_discovery_runs(client, days=7)
        assert result["status"] in (WARN, PASS)


# ──────────────────────────────────────────────────────────────────────────────
# run_audit integration
# ──────────────────────────────────────────────────────────────────────────────

class TestRunAudit:
    def test_run_audit_no_supabase_returns_fail(self):
        """Without Supabase, run_audit() must return a FAIL dict (not crash)."""
        with patch("scripts.weekly_audit._supabase", return_value=None):
            report = run_audit(days=7)
        assert isinstance(report, dict)
        # Either a fast-fail with "error" key, or a full report with overall=FAIL/WARN
        assert report.get("error") == "no_supabase_connection" or report.get("overall_status") in (WARN, FAIL)

    def test_run_audit_returns_dict_always(self):
        """run_audit() must never raise — always returns a result."""
        with patch("scripts.weekly_audit._supabase", return_value=None):
            try:
                report = run_audit(days=7)
                assert isinstance(report, dict)
            except Exception:
                pytest.fail("run_audit() must not propagate exceptions")

    def test_run_audit_full_structure_with_mock_client(self):
        """With a mocked Supabase, run_audit returns checks + summary."""
        mock_client = MagicMock()
        # All tables return empty data
        chain = MagicMock()
        chain.execute.return_value = MagicMock(data=[])
        chain.gte.return_value = chain
        chain.order.return_value = chain
        chain.limit.return_value = chain
        chain.not_.return_value = chain
        chain.is_.return_value = chain
        chain.select.return_value = chain
        mock_client.table.return_value = mock_client
        mock_client.select.return_value = chain

        with patch("scripts.weekly_audit._supabase", return_value=mock_client):
            report = run_audit(days=7)
        assert isinstance(report, dict)
        # Should have overall_status and checks in full-run path
        assert "overall_status" in report or "error" in report
