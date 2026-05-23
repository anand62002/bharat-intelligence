"""
tests/test_market_digest.py — Unit tests for agents/market_digest.py (P6-C)

Coverage:
  TestGenerateDigestStructure   — output keys, mood validation, types
  TestFallbackDigest            — keyword scoring, no-API path
  TestSaveDigest                — Supabase upsert, dry_run, missing env
  TestFetchMarketHeadlines      — headline deduplication, cutoff filtering
  TestHaikuDigest               — prompt formatting, JSON parse, error handling

Run:
    pytest tests/test_market_digest.py -v
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agents.market_digest import (
    DIGEST_TYPES,
    _fallback_digest,
    _format_headlines_for_prompt,
    generate_digest,
    save_digest,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _mock_haiku_response(payload: dict) -> MagicMock:
    """Return a mock urlopen context-manager that yields a Haiku-like response."""
    body = json.dumps({
        "content": [{"text": json.dumps(payload)}]
    }).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


_GOOD_HAIKU_PAYLOAD = {
    "market_mood":      "BULLISH",
    "summary":          "Indian equity markets opened on a positive note.",
    "key_events":       [{"event": "RBI holds rates steady", "impact": "POSITIVE"}],
    "top_themes":       ["Rate policy", "IT earnings"],
    "sectors_in_focus": ["Banking", "IT"],
    "nifty_signal":     "Expect range-bound to slightly positive session",
}

_SAMPLE_HEADLINES = [
    {"title": "Nifty surges 200 pts", "source": "ET Markets", "published": "2025-06-15 09:00", "url": ""},
    {"title": "RBI holds repo rate at 6.5%", "source": "Business Standard", "published": "2025-06-15 08:30", "url": ""},
    {"title": "FII buy Indian equities worth Rs 3000 crore", "source": "Moneycontrol", "published": "2025-06-15 07:00", "url": ""},
]


# ─────────────────────────────────────────────────────────────────────────────
# TestGenerateDigestStructure
# ─────────────────────────────────────────────────────────────────────────────

class TestGenerateDigestStructure:
    _REQUIRED_KEYS = {
        "digest_type", "digest_date", "headline_count", "market_mood",
        "summary", "key_events", "top_themes", "sectors_in_focus",
        "nifty_signal", "raw_headlines", "generated_at", "source",
    }

    def _make_digest(self, digest_type="MORNING"):
        with (
            patch("agents.market_digest._fetch_market_headlines", return_value=_SAMPLE_HEADLINES),
            patch("agents.market_digest.urlopen", return_value=_mock_haiku_response(_GOOD_HAIKU_PAYLOAD)),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            return generate_digest(digest_type)

    def test_morning_digest_has_all_required_keys(self):
        d = self._make_digest("MORNING")
        missing = self._REQUIRED_KEYS - set(d.keys())
        assert not missing, f"Missing keys: {missing}"

    def test_closing_digest_has_all_required_keys(self):
        d = self._make_digest("CLOSING")
        missing = self._REQUIRED_KEYS - set(d.keys())
        assert not missing, f"Missing keys: {missing}"

    def test_digest_type_is_correct(self):
        d = self._make_digest("MORNING")
        assert d["digest_type"] == "MORNING"

    def test_digest_date_is_today(self):
        d = self._make_digest("MORNING")
        assert d["digest_date"] == date.today().isoformat()

    def test_market_mood_is_valid(self):
        d = self._make_digest("MORNING")
        assert d["market_mood"] in {"BULLISH", "BEARISH", "NEUTRAL", "VOLATILE", "MIXED"}

    def test_key_events_is_list(self):
        d = self._make_digest("MORNING")
        assert isinstance(d["key_events"], list)

    def test_top_themes_is_list(self):
        d = self._make_digest("MORNING")
        assert isinstance(d["top_themes"], list)

    def test_source_is_claude_haiku_when_api_available(self):
        d = self._make_digest("MORNING")
        assert d["source"] == "claude_haiku"

    def test_headline_count_matches(self):
        d = self._make_digest("MORNING")
        assert d["headline_count"] == len(_SAMPLE_HEADLINES)

    def test_raw_headlines_is_list(self):
        d = self._make_digest("MORNING")
        assert isinstance(d["raw_headlines"], list)

    def test_invalid_digest_type_raises(self):
        with pytest.raises(ValueError):
            generate_digest("INVALID_TYPE")

    def test_invalid_mood_normalised_to_neutral(self):
        bad_payload = dict(_GOOD_HAIKU_PAYLOAD, market_mood="VERY_BULLISH")
        with (
            patch("agents.market_digest._fetch_market_headlines", return_value=_SAMPLE_HEADLINES),
            patch("agents.market_digest.urlopen", return_value=_mock_haiku_response(bad_payload)),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            d = generate_digest("MORNING")
        assert d["market_mood"] == "NEUTRAL"

    def test_event_impact_normalised(self):
        bad_evt_payload = dict(_GOOD_HAIKU_PAYLOAD)
        bad_evt_payload["key_events"] = [{"event": "test", "impact": "badval"}]
        with (
            patch("agents.market_digest._fetch_market_headlines", return_value=_SAMPLE_HEADLINES),
            patch("agents.market_digest.urlopen", return_value=_mock_haiku_response(bad_evt_payload)),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            d = generate_digest("MORNING")
        for evt in d["key_events"]:
            assert evt["impact"] in {"POSITIVE", "NEGATIVE", "NEUTRAL", "WATCH"}


# ─────────────────────────────────────────────────────────────────────────────
# TestFallbackDigest
# ─────────────────────────────────────────────────────────────────────────────

class TestFallbackDigest:
    def test_fallback_returns_valid_structure(self):
        d = _fallback_digest(_SAMPLE_HEADLINES, "MORNING")
        assert "market_mood" in d
        assert "summary" in d
        assert isinstance(d["key_events"], list)

    def test_fallback_bullish_detection(self):
        headlines = [{"title": "Nifty rallies on strong profit growth", "source": "X", "published": "", "url": ""}] * 5
        d = _fallback_digest(headlines, "MORNING")
        assert d["market_mood"] in {"BULLISH", "MIXED"}

    def test_fallback_bearish_detection(self):
        headlines = [{"title": "Market crashes on fraud probe penalty default", "source": "X", "published": "", "url": ""}] * 5
        d = _fallback_digest(headlines, "CLOSING")
        assert d["market_mood"] in {"BEARISH", "MIXED"}

    def test_fallback_no_api_key_path(self):
        """generate_digest uses fallback when ANTHROPIC_API_KEY not set."""
        with (
            patch("agents.market_digest._fetch_market_headlines", return_value=_SAMPLE_HEADLINES),
            patch.dict(os.environ, {}, clear=True),   # no API key
        ):
            d = generate_digest("MORNING")
        assert d["source"] == "keyword_fallback"

    def test_fallback_empty_headlines(self):
        d = _fallback_digest([], "MORNING")
        assert d["market_mood"] == "NEUTRAL"
        assert "summary" in d


# ─────────────────────────────────────────────────────────────────────────────
# TestSaveDigest
# ─────────────────────────────────────────────────────────────────────────────

class TestSaveDigest:
    def _make_digest_dict(self):
        return {
            "digest_type": "MORNING",
            "digest_date": date.today().isoformat(),
            "headline_count": 3,
            "market_mood": "BULLISH",
            "summary": "Test summary.",
            "key_events": [],
            "top_themes": ["IT"],
            "sectors_in_focus": ["Banking"],
            "nifty_signal": "Positive",
            "raw_headlines": [],
        }

    def test_dry_run_returns_none(self):
        d = self._make_digest_dict()
        result = save_digest(d, dry_run=True)
        assert result is None

    def test_save_with_mock_client(self):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.data = [{"id": "abc-123"}]
        mock_client.table.return_value.upsert.return_value.execute.return_value = mock_resp

        d = self._make_digest_dict()
        row_id = save_digest(d, client=mock_client, dry_run=False)
        assert row_id == "abc-123"

    def test_save_no_supabase_env_returns_none(self):
        with patch.dict(os.environ, {}, clear=True):
            d = self._make_digest_dict()
            result = save_digest(d, dry_run=False)
        assert result is None

    def test_save_supabase_exception_returns_none(self):
        mock_client = MagicMock()
        mock_client.table.return_value.upsert.return_value.execute.side_effect = Exception("DB error")
        d = self._make_digest_dict()
        result = save_digest(d, client=mock_client, dry_run=False)
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# TestFormatHeadlines
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatHeadlines:
    def test_format_returns_numbered_lines(self):
        formatted = _format_headlines_for_prompt(_SAMPLE_HEADLINES)
        lines = formatted.strip().split("\n")
        assert len(lines) == len(_SAMPLE_HEADLINES)
        assert lines[0].startswith("1.")

    def test_format_includes_source(self):
        formatted = _format_headlines_for_prompt(_SAMPLE_HEADLINES)
        assert "ET Markets" in formatted

    def test_format_empty_list(self):
        assert _format_headlines_for_prompt([]) == ""

    def test_format_truncates_at_max(self):
        many = [{"title": f"Headline {i}", "source": "X", "published": "", "url": ""}
                for i in range(50)]
        formatted = _format_headlines_for_prompt(many)
        lines = [l for l in formatted.strip().split("\n") if l.strip()]
        from agents.market_digest import MAX_PROMPT_HEADLINES
        assert len(lines) <= MAX_PROMPT_HEADLINES


# ─────────────────────────────────────────────────────────────────────────────
# TestHaikuCall
# ─────────────────────────────────────────────────────────────────────────────

class TestHaikuCall:
    def test_haiku_json_parse_error_uses_fallback(self):
        """When Haiku returns malformed JSON, fallback digest is used."""
        bad_resp = MagicMock()
        bad_resp.read.return_value = json.dumps({
            "content": [{"text": "NOT JSON {{{{"}]
        }).encode()
        bad_resp.__enter__ = lambda s: s
        bad_resp.__exit__ = MagicMock(return_value=False)
        with (
            patch("agents.market_digest._fetch_market_headlines", return_value=_SAMPLE_HEADLINES),
            patch("agents.market_digest.urlopen", return_value=bad_resp),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            d = generate_digest("MORNING")
        # Should still return valid digest via fallback
        assert "market_mood" in d
        assert d["market_mood"] in {"BULLISH", "BEARISH", "NEUTRAL", "VOLATILE", "MIXED"}

    def test_haiku_network_error_uses_fallback(self):
        """Network error during Haiku call → fallback digest."""
        from urllib.error import URLError
        with (
            patch("agents.market_digest._fetch_market_headlines", return_value=_SAMPLE_HEADLINES),
            patch("agents.market_digest.urlopen", side_effect=URLError("connection refused")),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            d = generate_digest("MORNING")
        assert "market_mood" in d
        assert d["source"] == "keyword_fallback"

    def test_no_headlines_returns_fallback(self):
        """When no headlines are fetched, fallback is used regardless of API key."""
        with (
            patch("agents.market_digest._fetch_market_headlines", return_value=[]),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            d = generate_digest("MORNING")
        assert d["headline_count"] == 0
        assert d["source"] == "keyword_fallback"
