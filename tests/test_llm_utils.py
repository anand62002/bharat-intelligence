"""
tests/test_llm_utils.py — model-agnostic Anthropic response text extraction.

Every call site used response.content[0].text, which assumes the first block is
the text block. Models with adaptive thinking put a `thinking` block first, and
on recent models that block's text is empty by default — so content[0].text
returns "" and json.loads fails. The failure is silent: the caller logs
"returned non-JSON" and discards the result (a dropped judge, or a whole symbol
dropped from the run).
"""

from types import SimpleNamespace

import pytest

from data.llm_utils import first_text


def _block(type_, text):
    return SimpleNamespace(type=type_, text=text)


def _resp(*blocks):
    return SimpleNamespace(content=list(blocks))


class TestOrdinaryResponses:
    def test_single_text_block(self):
        assert first_text(_resp(_block("text", '{"score": 4}'))) == '{"score": 4}'

    def test_picks_first_of_several_text_blocks(self):
        assert first_text(_resp(_block("text", "first"), _block("text", "second"))) == "first"


class TestThinkingModels:
    def test_skips_leading_thinking_block(self):
        """The regression this exists for."""
        r = _resp(_block("thinking", "let me reason..."), _block("text", '{"score": 4}'))
        assert first_text(r) == '{"score": 4}'

    def test_skips_thinking_block_with_empty_text(self):
        """Recent models omit thinking content by default, so .text is ''."""
        r = _resp(_block("thinking", ""), _block("text", '{"score": 4}'))
        assert first_text(r) == '{"score": 4}'

    def test_multiple_leading_non_text_blocks(self):
        r = _resp(_block("thinking", ""), _block("redacted_thinking", ""),
                  _block("text", "payload"))
        assert first_text(r) == "payload"


class TestDegenerateInput:
    def test_empty_content(self):
        assert first_text(_resp()) == ""

    def test_all_blocks_empty(self):
        assert first_text(_resp(_block("thinking", ""), _block("text", ""))) == ""

    def test_custom_default(self):
        assert first_text(_resp(), default="{}") == "{}"

    def test_untyped_block_with_text_still_works(self):
        """Some SDK versions / tool-use responses do not set .type."""
        assert first_text(_resp(SimpleNamespace(text="hello"))) == "hello"

    @pytest.mark.parametrize("bad", [None, object(), SimpleNamespace(), "string", 42])
    def test_never_raises(self, bad):
        assert isinstance(first_text(bad), str)


class TestAllCallSitesMigrated:
    def test_no_content_index_zero_remains(self):
        """content[0].text must not reappear — it is the bug this replaces."""
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        hits = []
        for d in ("agents", "governance", "scheduler", "api", "data"):
            p = root / d
            if not p.is_dir():
                continue
            for f in p.rglob("*.py"):
                # llm_utils documents the pattern it replaces in its docstring.
                if "worktrees" in str(f) or f.name == "llm_utils.py":
                    continue
                for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if "content[0].text" in line and not line.strip().startswith("#"):
                        hits.append(f"{f.relative_to(root)}:{i}")
        assert not hits, f"content[0].text still present: {hits}"
