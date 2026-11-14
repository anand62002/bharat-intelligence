"""
tests/test_orchestrator.py — composite scoring under ADR-003 (WP-003).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data import decision_config                      # noqa: E402
from scheduler import orchestrator as orch            # noqa: E402


def _results(**scores):
    return {a: {"signal": "BUY", "score": scores.get(a, 50)} for a in orch.AGENT_NAMES}


def _equal():
    return {a: 1 / len(orch.AGENT_NAMES) for a in orch.AGENT_NAMES}


def test_agent_names_come_from_decision_config():
    assert orch.AGENT_NAMES == list(decision_config.AGENT_NAMES)


def test_composite_is_plain_mean_with_equal_weights():
    r = _results(technical=100, fundamental=0)
    assert orch._composite_score(r, _equal()) == pytest.approx(50.0)
    r = _results(technical=90)
    assert orch._composite_score(r, _equal()) == pytest.approx((90 + 50 * 6) / 7, abs=0.01)


def test_regime_multipliers_ignored_when_disabled():
    r = _results(technical=90, fundamental=10, institutional=80, macro=20)
    flat = orch._composite_score(r, _equal())
    for regime in ("BULL", "BEAR", "HIGH_VOLATILITY", "SIDEWAYS"):
        assert orch._composite_score(r, _equal(), regime=regime) == flat


def test_regime_multipliers_applied_when_enabled(monkeypatch):
    from agents.regime_detector import REGIME_WEIGHT_MULTIPLIERS
    monkeypatch.setattr(decision_config, "REGIME_MULTIPLIERS_ENABLED", True)
    r = _results(technical=90, fundamental=10, institutional=80, macro=20)
    flat = orch._composite_score(r, _equal())
    changed = [
        reg for reg in REGIME_WEIGHT_MULTIPLIERS
        if orch._composite_score(r, _equal(), regime=reg) != flat
    ]
    assert changed, "enabling regime multipliers should change at least one regime's composite"


def test_no_data_agents_excluded():
    r = _results(technical=100)
    r["technical"]["signal"] = "INSUFFICIENT_DATA"
    assert orch._composite_score(r, _equal()) == pytest.approx(50.0)
