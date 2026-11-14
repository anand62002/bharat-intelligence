"""
data/llm_utils.py — safe extraction of text from an Anthropic response.

Why this exists
---------------
Every call site in this project read `response.content[0].text`, which assumes
the first content block is the text block. That held for Claude 4.x without
thinking, but it is not guaranteed:

  * models with adaptive thinking return a `thinking` block FIRST, and the
    text block after it
  * on recent models the thinking block's text is empty by default
    (display="omitted"), so `content[0].text` yields "" rather than raising

The failure mode is therefore silent: json.loads("") raises JSONDecodeError,
the caller logs "returned non-JSON", and the result is discarded. In the
validator that silently drops a judge and depresses kappa; in the orchestrator
it drops the whole symbol from the run.

`first_text(resp)` scans for the first block that actually carries text, so the
same code works whether or not a model emits thinking blocks. It is a pure
function over the response object and makes no assumptions about model family.
"""

from __future__ import annotations

import contextlib
import contextvars
import threading
from dataclasses import dataclass, field
from typing import Any, Iterator


# ─────────────────────────────────────────────────────────────────────────────
# Token usage per job run (WP-005, ADR-005)
# ─────────────────────────────────────────────────────────────────────────────
# data/run_log.tracked_job opens a scope per worker job; every Anthropic
# response that passes through first_text() / record_usage() adds its usage to
# it. The scope lives in a ContextVar holding a shared mutable counter, so calls
# made from the job thread, from asyncio.run() inside it, and from
# asyncio.to_thread() workers (which copy the context) all land in the same run.
# Calls from bare ThreadPoolExecutor threads do not inherit the context; they
# are counted in the process-wide UNATTRIBUTED counter instead of being lost.

@dataclass
class LLMUsage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def add(self, usage: Any) -> None:
        def _get(name: str) -> int:
            v = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
            try:
                return int(v or 0)
            except (TypeError, ValueError):
                return 0
        with self._lock:
            self.calls += 1
            self.input_tokens += _get("input_tokens")
            self.output_tokens += _get("output_tokens")
            self.cache_read_input_tokens += _get("cache_read_input_tokens")
            self.cache_creation_input_tokens += _get("cache_creation_input_tokens")

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def as_dict(self) -> dict:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
        }


UNATTRIBUTED = LLMUsage()
_current: contextvars.ContextVar[LLMUsage | None] = contextvars.ContextVar("llm_usage", default=None)


@contextlib.contextmanager
def llm_usage_scope() -> Iterator[LLMUsage]:
    """Count tokens for everything called inside this block (one job run)."""
    usage = LLMUsage()
    token = _current.set(usage)
    try:
        yield usage
    finally:
        _current.reset(token)


def record_usage(resp: Any) -> None:
    """
    Add a Messages response's token usage to the current run.

    Accepts an SDK response object (resp.usage) or a raw-HTTP JSON body
    (body["usage"]). Never raises — accounting must not break a call site.
    """
    try:
        usage = resp.get("usage") if isinstance(resp, dict) else getattr(resp, "usage", None)
        if usage is None:
            return
        (_current.get() or UNATTRIBUTED).add(usage)
    except Exception:
        return


def first_text(resp: Any, default: str = "") -> str:
    """
    Return the first non-empty text block from an Anthropic response.

    Falls back to `default` when the response carries no text at all (an
    all-thinking response, an empty content list, or a malformed object).
    Never raises. Also records the response's token usage (record_usage).
    """
    record_usage(resp)
    try:
        blocks = getattr(resp, "content", None) or []
    except Exception:
        return default

    for block in blocks:
        # Prefer explicitly-typed text blocks.
        if getattr(block, "type", None) == "text":
            text = getattr(block, "text", None)
            if text:
                return str(text)

    # Some SDK versions / tool-use responses do not set .type; fall back to any
    # block exposing a non-empty .text.
    for block in blocks:
        text = getattr(block, "text", None)
        if text:
            return str(text)

    return default
