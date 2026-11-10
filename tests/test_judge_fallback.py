"""
tests/test_judge_fallback.py — the GPT validation judge falls back to Haiku.

The validator's docstring long claimed the GPT judge "falls back to claude-haiku
if OPENAI_API_KEY is not set", but no such code existed: any OpenAI failure just
dropped the vote, which applies the single-judge reliability penalty and
depresses kappa. That matters on 2027-01-14, when gpt-4o-mini shuts down — every
GPT call would have failed and silently shrunk the panel to two judges.

Also pins the gpt-6-luna request shape: it rejects `max_tokens` and any
non-default `temperature` with a 400.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from data.model_config import HAIKU_MODEL, HAIKU_THINKING
from scheduler import synthesis_validator as sv


def _anthropic_reply(score=4, rationale="ok"):
    block = SimpleNamespace(type="text", text=json.dumps({"score": score, "rationale": rationale}))
    return SimpleNamespace(content=[block])


def _run(coro):
    return asyncio.run(coro)


def test_openai_failure_falls_back_to_haiku(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    ant = MagicMock()
    ant.messages.create.return_value = _anthropic_reply(score=5, rationale="fallback")

    with patch.object(sv, "_call_openai_judge", side_effect=RuntimeError("model retired")):
        score, rationale = _run(sv._call_judge("gpt", "gpt-6-luna", "data_provenance", "p", ant))

    assert (score, rationale) == (5, "fallback")
    kwargs = ant.messages.create.call_args.kwargs
    assert kwargs["model"] == HAIKU_MODEL
    assert kwargs["thinking"] == HAIKU_THINKING


def test_missing_openai_key_falls_back_to_haiku(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    ant = MagicMock()
    ant.messages.create.return_value = _anthropic_reply(score=3)

    score, _ = _run(sv._call_judge("gpt", "gpt-6-luna", "logic_coherence", "p", ant))

    assert score == 3
    assert ant.messages.create.call_args.kwargs["model"] == HAIKU_MODEL


def test_claude_judges_do_not_send_disabled_thinking():
    """{"type": "disabled"} is a 400 on Opus 5.5 — only Haiku may receive it."""
    ant = MagicMock()
    ant.messages.create.return_value = _anthropic_reply()

    _run(sv._call_judge("opus", sv.JUDGE_MODELS["opus"], "r", "p", ant))
    _run(sv._call_judge("sonnet", sv.JUDGE_MODELS["sonnet"], "r", "p", ant))

    for call in ant.messages.create.call_args_list:
        assert "thinking" not in call.kwargs


def test_openai_request_uses_max_completion_tokens_and_no_temperature(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"score": 4, "rationale": "x"}'))]
    )
    fake_openai = SimpleNamespace(OpenAI=MagicMock(return_value=fake_client))

    with patch.dict("sys.modules", {"openai": fake_openai}):
        score, _ = _run(sv._call_openai_judge("gpt", "gpt-6-luna", "r", "p"))

    assert score == 4
    kwargs = fake_client.chat.completions.create.call_args.kwargs
    assert "max_completion_tokens" in kwargs
    assert "max_tokens" not in kwargs
    assert "temperature" not in kwargs


def test_gpt_judge_is_not_a_deprecated_model():
    assert sv.JUDGE_MODELS["gpt"] not in {"gpt-4o-mini", "gpt-4o", "gpt-4.1"}
