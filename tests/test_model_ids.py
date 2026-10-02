"""
tests/test_model_ids.py — no retired model IDs in live code.

Model deprecations have bitten this project before: BF-18 records
claude-sonnet-4-5 and claude-opus-4-5 being retired in the same wave as ARIA's
claude-sonnet-4-20250514, which failed silently in production. A 2026-09-10
sweep then found claude-sonnet-4-5 still live in governance/research_agent.py,
missed because that module read CLAUDE_MODEL with its own stale default.

This asserts over the actual source of every module that calls a model, so the
next retirement surfaces as a red test rather than a 404 at 07:30 IST.
"""

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent

# Retired — must not appear in live code.
RETIRED = [
    "claude-sonnet-4-5",
    "claude-opus-4-5",
    "claude-sonnet-4-20250514",
    "claude-3-5-sonnet",
    "claude-3-opus",
    "claude-2",
]

# Current, per the Anthropic model list.
CURRENT = {
    "claude-fable-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    "claude-haiku-4-5-20251001",
}

# Source dirs that actually issue model calls. Docs and the tracker legitimately
# name retired models when recording past migrations, so they are excluded.
CODE_DIRS = ["agents", "governance", "scheduler", "api", "data", "scripts"]


def _code_files():
    files = []
    for d in CODE_DIRS:
        p = _ROOT / d
        if p.is_dir():
            files.extend(f for f in p.rglob("*.py") if "worktrees" not in str(f))
    jsx = _ROOT / "dashboard" / "src" / "App.jsx"
    if jsx.exists():
        files.append(jsx)
    for js in (_ROOT / "dashboard" / "api").glob("*.js"):
        files.append(js)
    return files


@pytest.mark.parametrize("retired", RETIRED)
def test_no_retired_model_ids(retired):
    hits = []
    for f in _code_files():
        text = f.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            # Ignore comments — those may document the migration itself.
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//"):
                continue
            if retired in line:
                hits.append(f"{f.relative_to(_ROOT)}:{i}")
    assert not hits, f"retired model {retired!r} still referenced: {hits}"


def test_every_model_id_in_code_is_current():
    """Catch typos and date-suffixed variants that do not exist."""
    pattern = re.compile(r"[\"']((?:claude)-[a-z0-9.\-]+)[\"']")
    unknown = []
    for f in _code_files():
        text = f.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//"):
                continue
            for m in pattern.finditer(line):
                mid = m.group(1)
                if mid not in CURRENT:
                    unknown.append(f"{f.relative_to(_ROOT)}:{i} -> {mid}")
    assert not unknown, (
        "model IDs not in the current list (typo, invented date suffix, or a "
        f"new model that needs adding to CURRENT): {unknown}"
    )


def test_research_agent_does_not_share_the_orchestrator_env_var():
    """
    research_agent and orchestrator both read CLAUDE_MODEL with *different*
    defaults, so they drifted apart unnoticed and setting CLAUDE_MODEL to tune
    synthesis silently changed the research agent too.
    """
    from governance import research_agent
    from scheduler import orchestrator

    src = (_ROOT / "governance" / "research_agent.py").read_text(encoding="utf-8")
    assert 'os.getenv("CLAUDE_MODEL"' not in src, (
        "research_agent must not read the orchestrator's CLAUDE_MODEL env var"
    )
    assert research_agent.SONNET_MODEL in CURRENT
    assert orchestrator.CLAUDE_MODEL in CURRENT
