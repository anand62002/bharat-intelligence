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

from typing import Any


def first_text(resp: Any, default: str = "") -> str:
    """
    Return the first non-empty text block from an Anthropic response.

    Falls back to `default` when the response carries no text at all (an
    all-thinking response, an empty content list, or a malformed object).
    Never raises.
    """
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
