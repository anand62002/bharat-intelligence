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

# OpenAI models deprecated with a shutdown date — must not appear in live code.
# gpt-4o-mini: deprecated 2026-10-14, shutdown 2027-01-14 (-> gpt-6-luna).
RETIRED_OPENAI = ["gpt-4o-mini", "gpt-4o", "gpt-4.1"]

# Current, verified against client.models.list() on 2026-09-10; claude-haiku-5-5
# added 2026-11-10 (verified via client.models.retrieve). Newest first.
# Re-run that call before editing this set — a cached list goes stale fast:
# the one this project was working from in May 2026 topped out at Opus 4.8 and
# had missed six later releases by September.
CURRENT = {
    "claude-haiku-5-5",
    "claude-sonnet-5-5",
    "claude-opus-5-5",
    "claude-fable-5-1",
    "claude-opus-5",
    "claude-sonnet-5",
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


# ── Single source of truth (2026-11-10) ───────────────────────────────────────
# No model env vars are set on Railway, so the defaults in data/model_config.py
# ARE production. A model ID hardcoded anywhere else is a copy that the next
# deprecation sweep can miss — which is exactly how Haiku ended up pinned in
# four modules with a dated suffix.

# The only files allowed to contain a model ID literal. aria.js is a Vercel
# function and cannot import Python, so it carries a mirrored constant.
_REGISTRY = _ROOT / "data" / "model_config.py"
_ARIA_JS  = _ROOT / "dashboard" / "api" / "aria.js"
_MODEL_LITERAL = re.compile(r"[\"']((?:claude|gpt)-[a-z0-9.\-]+)[\"']")


def _all_code_files():
    files = _code_files()
    files.extend(f for f in (_ROOT / "db").glob("*.py"))
    files.append(_ROOT / "worker.py")
    return [f for f in files if f.exists()]


def test_model_ids_live_only_in_the_registry():
    stray = []
    for f in _all_code_files():
        if f in (_REGISTRY, _ARIA_JS):
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//"):
                continue
            for m in _MODEL_LITERAL.finditer(line):
                stray.append(f"{f.relative_to(_ROOT)}:{i} -> {m.group(1)}")
    assert not stray, (
        "model ID hardcoded outside data/model_config.py — import it from the "
        f"registry instead: {stray}"
    )


@pytest.mark.parametrize("retired", RETIRED_OPENAI)
def test_no_deprecated_openai_models(retired):
    hits = []
    for f in _all_code_files():
        for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//"):
                continue
            if re.search(rf"[\"']{re.escape(retired)}[\"']", line):
                hits.append(f"{f.relative_to(_ROOT)}:{i}")
    assert not hits, f"deprecated OpenAI model {retired!r} still referenced: {hits}"


def test_registry_defaults_are_current(monkeypatch):
    """The DEFAULTS (what Railway runs with no env vars) must all be current."""
    import importlib
    for var in ("CLAUDE_MODEL", "JUDGE_MODEL_SONNET", "JUDGE_MODEL_OPUS",
                "RESEARCH_AGENT_MODEL", "CLAUDE_HAIKU_MODEL", "JUDGE_MODEL_GPT"):
        monkeypatch.delenv(var, raising=False)
    from data import model_config
    mc = importlib.reload(model_config)
    for name in ("SYNTHESIS_MODEL", "JUDGE_MODEL_SONNET", "JUDGE_MODEL_OPUS",
                 "RESEARCH_MODEL", "HAIKU_MODEL"):
        assert getattr(mc, name) in CURRENT, f"{name}={getattr(mc, name)!r} not current"
    assert mc.OPENAI_CHAT_MODEL not in RETIRED_OPENAI
    importlib.reload(model_config)


def test_aria_js_mirrors_the_registry_haiku_model():
    from data.model_config import HAIKU_MODEL
    src = _ARIA_JS.read_text(encoding="utf-8")
    m = re.search(r'const ARIA_MODEL = "([^"]+)"', src)
    assert m, "ARIA_MODEL constant not found in dashboard/api/aria.js"
    assert m.group(1) == HAIKU_MODEL, (
        f"aria.js ARIA_MODEL={m.group(1)!r} drifted from registry HAIKU_MODEL={HAIKU_MODEL!r}"
    )
    # The browser must not choose the model — aria.js overrides it.
    assert "payload.model = ARIA_MODEL" in src
    assert 'model:"claude' not in (_ROOT / "dashboard" / "src" / "App.jsx").read_text(encoding="utf-8")


def test_every_haiku_call_disables_thinking():
    """
    Haiku 5.5 thinks by default: a thinking block arrives before the text and
    spends the small (40-800) max_tokens budgets these calls use. Every module
    that calls HAIKU_MODEL must pass HAIKU_THINKING alongside it.
    """
    missing = []
    for f in _all_code_files():
        if f == _REGISTRY:
            continue
        src = f.read_text(encoding="utf-8", errors="replace")
        n_calls = len(re.findall(
            r'model\s*[=:]\s*(?:CLAUDE_)?HAIKU_MODEL|"model":\s*HAIKU_MODEL', src))
        n_think = len(re.findall(r'thinking"?\s*[=:]\s*HAIKU_THINKING', src))
        if n_think < n_calls:
            missing.append(f"{f.relative_to(_ROOT)} ({n_calls} calls, {n_think} with thinking off)")
    assert not missing, f"Haiku calls without HAIKU_THINKING: {missing}"


def test_no_code_assumes_first_content_block_is_text():
    """
    content[0] is a `thinking` block on models with adaptive thinking (Opus 5.5,
    Haiku 5.5 unless disabled). scripts/test_synthesis.py read
    `resp.content[0].text` and failed the 2026-11-10 Railway deploy with
    "'ThinkingBlock' object has no attribute 'text'". Use data.llm_utils.first_text
    (SDK objects) or scan for the first {"type": "text"} block (raw HTTP).
    """
    pattern = re.compile(r"""content\s*\[\s*0\s*\]\s*(\.text|\[\s*["']text["']\s*\])|content\?\.\[0\]\?\.text""")
    hits = []
    for f in _all_code_files():
        for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//") or stripped.startswith("*"):
                continue
            # Backtick spans are prose quoting the anti-pattern (docstrings).
            if pattern.search(re.sub(r"`[^`]*`", "", line)):
                hits.append(f"{f.relative_to(_ROOT)}:{i}")
    assert not hits, f"code reads content[0] as the text block: {hits}"
