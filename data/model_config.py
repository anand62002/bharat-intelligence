"""
data/model_config.py — the single source of truth for every LLM model ID.

Why this exists
---------------
Model IDs used to be scattered across ~10 modules: some hardcoded (with and
without date suffixes), some behind env vars with *different* defaults. No model
env vars are set on Railway, so the defaults in code are what production runs —
and a deprecation meant hunting for every copy. Edit the constants here; nothing
else in the codebase should contain a model ID string.

The env-var overrides below are optional escape hatches for experiments. They
are deliberately NOT set on Railway; leave them unset in production so this file
stays authoritative.

Last verified against the live provider deprecation pages: 2026-11-10.
  Anthropic  — claude-haiku-4-5's guaranteed-until date (2026-10-15) had passed,
               so Haiku moved to claude-haiku-5-5. Opus 5.5 / Sonnet 4.6 active.
  OpenAI     — gpt-4o-mini deprecated 2026-10-14, shutdown 2027-01-14;
               replacement gpt-6-luna. text-embedding-3-small not deprecated.
"""

from __future__ import annotations

import os


def _env(name: str, default: str) -> str:
    """Optional override; an unset or blank env var falls back to the default."""
    return (os.getenv(name) or "").strip() or default


# ── Anthropic ─────────────────────────────────────────────────────────────────
# Synthesis — highest-leverage call in the system (P7-A).
SYNTHESIS_MODEL = _env("CLAUDE_MODEL", "claude-opus-5-5")

# Synthesis-validation judges. Changing a judge shifts the kappa distribution,
# which gates publishing via KAPPA_SUPPRESS — watch suppression rate afterwards.
JUDGE_MODEL_SONNET = _env("JUDGE_MODEL_SONNET", "claude-sonnet-4-6")
JUDGE_MODEL_OPUS   = _env("JUDGE_MODEL_OPUS",   "claude-opus-5-5")

# Research agent proposal generation (separate from synthesis since 2026-09-10).
RESEARCH_MODEL = _env("RESEARCH_AGENT_MODEL", "claude-sonnet-4-6")

# Cheap, high-volume calls: sentiment, digest, fact-checker, warren_bot
# commentary, hallucination reconciliation, ticker normalisation, research
# relevance scoring, ARIA (dashboard/api/aria.js mirrors this — keep in sync).
HAIKU_MODEL = _env("CLAUDE_HAIKU_MODEL", "claude-haiku-5-5")

# Haiku 5.5 runs adaptive thinking by default: it emits a `thinking` block
# BEFORE the text, and the thinking spends the (deliberately small, 40–800)
# max_tokens budgets these calls use. Every Haiku call is a short
# classification/extraction, so thinking is turned off explicitly. Pass this as
# `thinking=HAIKU_THINKING` (SDK) or `"thinking": HAIKU_THINKING` (raw HTTP).
# NOTE: {"type": "disabled"} is a 400 on Opus 5.5 / Sonnet 5.5 — only send it
# to Haiku.
HAIKU_THINKING = {"type": "disabled"}

# ── OpenAI ────────────────────────────────────────────────────────────────────
# GPT validation judge + RAG corpus classifier.
# gpt-6-luna rejects `max_tokens` (use `max_completion_tokens`) and any
# non-default `temperature` — both verified live 2026-11-10.
OPENAI_CHAT_MODEL = _env("JUDGE_MODEL_GPT", "gpt-6-luna")


def is_openai_model(model_id: str) -> bool:
    """True when the model is called via the OpenAI API (not Anthropic)."""
    return model_id.startswith(("gpt-", "o1-", "o3-", "o4-"))
