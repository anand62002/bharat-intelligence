"""
tests/test_loud_failures.py — decision-path failures are logged at WARNING (WP-010, ADR-005).

Each test forces a previously-silent handler to fire and checks two things:
the failure now leaves a WARNING naming what failed, and control flow is
unchanged (the same fallback value is returned).
"""
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _Boom(dict):
    """A mapping whose every access raises — simulates a broken import/config."""
    def __contains__(self, k):
        raise RuntimeError("map broken")

    def items(self):
        raise RuntimeError("map broken")


def _warned(caplog, *needles) -> bool:
    return any(r.levelno == logging.WARNING and all(n in r.getMessage() for n in needles)
               for r in caplog.records)


def test_discovery_sector_pe_live_median_failure_warns_and_falls_back(caplog):
    from agents import discovery_screener as ds
    with patch("scheduler.sector_pe_tracker.compute_rolling_longrun_pe",
               side_effect=RuntimeError("db down")), caplog.at_level(logging.WARNING):
        pe = ds._get_sector_pe("Banking")
    assert pe > 0                                    # static map / default still used
    assert _warned(caplog, "_get_sector_pe(Banking)", "db down")


def test_outcome_tracker_symbol_map_failure_warns_and_keeps_default(caplog):
    from agents import outcome_tracker as ot
    with patch("data.symbol_map.YF_SYMBOL_MAP", _Boom()), caplog.at_level(logging.WARNING):
        out = ot._resolve_yf_symbol("RELIANCE")
    assert out == "RELIANCE.NS"                      # unchanged fallback
    assert _warned(caplog, "_resolve_yf_symbol(RELIANCE)", "map broken")


def test_target_updater_sector_pe_failure_warns_and_returns_22x(caplog):
    from agents import target_updater as tu
    with patch("agents.sector_valuation.SECTOR_LONGRUN_PE", _Boom()), \
         caplog.at_level(logging.WARNING):
        assert tu._sector_pe("Information Technology") == 22.0
    assert _warned(caplog, "_sector_pe(Information Technology)", "map broken")


def test_portfolio_monitor_alert_dedup_failure_warns_and_allows_alert(caplog):
    from scheduler import portfolio_monitor as pm
    client = MagicMock()
    client.table.side_effect = RuntimeError("timeout")
    with caplog.at_level(logging.WARNING):
        # unchanged: a failed dedup check is treated as "no duplicate"
        assert pm._alert_exists(client, "h-1", "STOPLOSS_HIT", symbol="TITAN") is False
    assert _warned(caplog, "Alert dedup check failed", "timeout")


def test_paper_portfolio_resolve_failure_warns(caplog):
    from agents import paper_portfolio as pp
    with patch("data.symbol_map.YF_SYMBOL_MAP", _Boom()), caplog.at_level(logging.WARNING):
        out = pp._resolve_yf_symbol("INFY")
    assert out == "INFY.NS"
    assert _warned(caplog, "_resolve_yf_symbol(INFY)", "map broken")


def test_synthesise_node_failures_reach_the_run_errors():
    """The two synthesise_node sites append to the run's errors list (→ daily_runs ledger)."""
    import ast
    src = (Path(__file__).resolve().parent.parent / "scheduler" / "orchestrator.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "synthesise_node")
    texts = [ast.unparse(h) for h in ast.walk(fn) if isinstance(h, ast.ExceptHandler)]
    assert any("leakage audit failed" in t and "errors.append" in t for t in texts)
    assert any("earnings guard failed" in t and "errors.append" in t for t in texts)
