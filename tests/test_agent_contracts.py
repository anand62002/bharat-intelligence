"""
tests/test_agent_contracts.py — consumers read keys agents actually emit (WP-016, ADR-005).

Checked against REAL recorded outputs (tests/fixtures/agent_outputs/, recorded
by scripts/record_agent_fixtures.py), never against mocks: the
intrinsic_value bug survived because the tests mocked the same wrong key.
"""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents import contracts as c  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "agent_outputs"


def _fixture(agent: str) -> dict:
    return json.loads((FIXTURES / f"{agent}.json").read_text(encoding="utf-8"))


def _unexplained(agent: str, output: dict) -> dict[str, list[str]]:
    return {k: v for k, v in c.missing_keys(agent, output).items()
            if (agent, k) not in c.KNOWN_MISSING and (agent, k) not in c.PATH_DEPENDENT}


@pytest.mark.parametrize("agent", sorted(c.CONSUMED_KEYS))
def test_every_consumed_key_exists_in_the_recorded_output(agent):
    missing = _unexplained(agent, _fixture(agent))
    assert not missing, (
        f"{agent}: consumers read keys the agent does not emit: "
        + "; ".join(f"{k} ← {', '.join(v)}" for k, v in sorted(missing.items())))


def test_renaming_intrinsic_value_per_share_fails_the_contract():
    """The bug this package exists for: a renamed key must be caught."""
    w = copy.deepcopy(_fixture("warren_bot"))
    w["intrinsic_value"] = w.pop("intrinsic_value_per_share")
    missing = _unexplained("warren_bot", w)
    assert "intrinsic_value_per_share" in missing
    assert "agents/target_updater.py::_intrinsic" in missing["intrinsic_value_per_share"]


def test_known_missing_keys_are_really_missing():
    """KNOWN_MISSING only shrinks: once an agent emits a key, delete its entry."""
    now_present = [(a, k) for (a, k) in c.KNOWN_MISSING if c.lookup(_fixture(a), k)[0]]
    assert not now_present, f"remove from KNOWN_MISSING (now emitted): {now_present}"


def test_known_missing_and_path_dependent_keys_are_still_consumed():
    stale = [(a, k) for (a, k) in {**c.KNOWN_MISSING, **c.PATH_DEPENDENT}
             if k not in c.all_consumed(a)]
    assert not stale, f"no consumer reads these any more — delete the entries: {stale}"


def test_sector_adjusted_macro_supplies_its_path_dependent_keys():
    """raw_macro_score / sector_outlook come from get_sector_adjusted_macro_score."""
    from agents.macro import get_sector_adjusted_macro_score
    adj = get_sector_adjusted_macro_score(_fixture("macro"), "Information Technology")
    assert not c.missing_keys("macro", adj)


def test_every_agent_has_a_fixture_type_and_signal_score():
    for agent in c.CONSUMED_KEYS:
        out = _fixture(agent)
        assert agent in c.AGENT_RESULT_TYPES
        assert "signal" in out and "score" in out, agent
        assert out["_recorded"]["by"] == "scripts/record_agent_fixtures.py"


def test_typed_dicts_cover_every_top_level_consumed_key():
    for agent, typ in c.AGENT_RESULT_TYPES.items():
        declared = set()
        for k in typ.__mro__:
            declared |= set(getattr(k, "__annotations__", {}))
        top = {k.split(".")[0] for k in c.all_consumed(agent)}
        undeclared = top - declared - {k for (a, k) in c.KNOWN_MISSING if a == agent}
        assert not undeclared, f"{agent}: add to {typ.__name__}: {sorted(undeclared)}"


def test_lookup_paths():
    assert c.lookup({"a": {"b": 1}}, "a.b") == (True, 1)
    assert c.lookup({"a": {"b": 1}}, "a.c") == (False, None)
    assert c.lookup({"a": None}, "a.b") == (False, None)
