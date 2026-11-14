"""
tests/test_decision_config.py — WP-003 / ADR-003.

Agent weights are equal and defined in exactly one place: data/decision_config.py.
"""
import ast
import asyncio
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data import decision_config  # noqa: E402

RUNTIME_DIRS = ["agents", "scheduler", "data", "api", "governance"]
RUNTIME_FILES = ["worker.py"]
ALLOWED = {ROOT / "data" / "decision_config.py"}


# ── config itself ─────────────────────────────────────────────────────────────

def test_defaults_are_equal_and_regime_off():
    assert decision_config.AGENT_WEIGHTS_MODE == "equal"
    assert decision_config.REGIME_MULTIPLIERS_ENABLED is False


def test_equal_weights_sum_to_one_and_are_equal():
    w = decision_config.equal_weights()
    assert set(w) == set(decision_config.AGENT_NAMES)
    assert len(set(w.values())) == 1
    assert sum(w.values()) == pytest.approx(1.0)


def test_discovery_weights_equal_in_equal_mode():
    assert decision_config.discovery_weights() == decision_config.equal_weights()


def test_discovery_weights_fall_back_to_legacy_on_rollback(monkeypatch):
    monkeypatch.setattr(decision_config, "AGENT_WEIGHTS_MODE", "accuracy")
    assert decision_config.discovery_weights() == decision_config.LEGACY_DISCOVERY_WEIGHTS


def test_describe_weights_log_line():
    assert decision_config.describe_weights() == "Agent weights: equal (7 × 0.1429)"


# ── orchestrator honours the config whatever agent_performance contains ─────

def _skewed_client():
    """agent_performance as found in prod: technical 0, institutional 100, one fraction."""
    rows = [
        {"agent_name": "technical", "accuracy_90d": 0.0},
        {"agent_name": "institutional", "accuracy_90d": 100.0},
        {"agent_name": "sentiment", "accuracy_90d": 0.45},
    ]
    client = MagicMock()
    client.table.return_value.select.return_value.order.return_value.execute.return_value = (
        MagicMock(data=rows)
    )
    return client


def test_equal_mode_ignores_agent_performance(caplog):
    from scheduler import orchestrator as orch
    client = _skewed_client()
    with patch.object(orch, "_supabase", return_value=client) as sb, \
         patch("agents.regime_detector.load_current_regime", return_value=None), \
         caplog.at_level(logging.INFO):
        out = asyncio.run(orch.load_weights_node({}))
    w = out["agent_weights"]
    assert set(w) == set(orch.AGENT_NAMES)
    assert all(v == pytest.approx(1 / 7, abs=1e-6) for v in w.values())
    sb.assert_not_called()            # agent_performance is not even read
    client.table.assert_not_called()
    assert "Agent weights: equal" in caplog.text


def test_accuracy_mode_still_reads_agent_performance(monkeypatch):
    """Rollback path: AGENT_WEIGHTS_MODE='accuracy' restores adaptive weights."""
    from scheduler import orchestrator as orch
    monkeypatch.setattr(decision_config, "AGENT_WEIGHTS_MODE", "accuracy")
    with patch.object(orch, "_supabase", return_value=_skewed_client()), \
         patch("agents.regime_detector.load_current_regime", return_value=None):
        w = asyncio.run(orch.load_weights_node({}))["agent_weights"]
    assert w["institutional"] > w["technical"]


# ── no other module defines an agent-weight table ────────────────────────────

def _runtime_files():
    for d in RUNTIME_DIRS:
        yield from (ROOT / d).rglob("*.py")
    for f in RUNTIME_FILES:
        yield ROOT / f


def _is_agent_weight_table(node: ast.AST) -> bool:
    """A dict literal with >=3 agent-name keys mapped to numeric constants."""
    if not isinstance(node, ast.Dict):
        return False
    hits = 0
    for k, v in zip(node.keys, node.values):
        if (isinstance(k, ast.Constant) and k.value in decision_config.AGENT_NAMES
                and isinstance(v, ast.Constant) and isinstance(v.value, (int, float))):
            hits += 1
    return hits >= 3


def _assigned_name(node) -> str:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for t in targets:
        if isinstance(t, ast.Name):
            return t.id
        if isinstance(t, ast.Attribute):
            return t.attr
    return ""


def _find_weight_tables(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            if "WEIGHT" in _assigned_name(node).upper() and _is_agent_weight_table(node.value):
                yield node


def test_no_other_module_defines_agent_weights():
    offenders = []
    for path in _runtime_files():
        if path in ALLOWED or "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offenders += [f"{path.relative_to(ROOT)}:{n.lineno}" for n in _find_weight_tables(tree)]
    assert not offenders, (
        "Agent weights must be defined only in data/decision_config.py (ADR-003): "
        + ", ".join(offenders)
    )


def test_scan_detects_a_planted_weight_table():
    """Guard against the scan silently matching nothing."""
    tree = ast.parse('_AGENT_WEIGHTS = {"technical": 0.2, "fundamental": 0.3, "macro": 0.5}')
    assert len(list(_find_weight_tables(tree))) == 1


def test_outcome_tracker_writes_percentage_not_fraction():
    src = (ROOT / "agents" / "outcome_tracker.py").read_text(encoding="utf-8")
    assert "accuracy_30d / 100" not in src
