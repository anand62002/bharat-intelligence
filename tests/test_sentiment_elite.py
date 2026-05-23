"""
tests/test_sentiment_elite.py — Tests for P6-D Elite Sentiment upgrades

Coverage:
  TestEventClassConstants       — EVENT_CLASSES, multipliers, half-lives defined
  TestBatchClassifyHeadlines    — batch Haiku call, fallback, event_class assignment
  TestTemporalWeight            — decay function correctness, half-life boundaries
  TestFinBertHF                 — HF API call, label parsing, ensemble conversion
  TestFinBertToScore            — positive/negative/neutral mapping
  TestBseAnnouncements          — get_bse_announcements (data/fetchers.py D-1)
  TestAnalyseIntegration        — analyse() with D-1/D-2/D-3/D-4 enabled

Run:
    pytest tests/test_sentiment_elite.py -v
"""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agents.sentiment import (
    EVENT_CLASSES,
    _BATCH_CLASSIFY_PROMPT,
    _EVENT_MULTIPLIERS,
    _HALF_LIVES_HOURS,
    _batch_classify_headlines,
    _call_finbert_hf,
    _fallback_score,
    _finbert_to_score,
    _temporal_weight,
)
from data.fetchers import get_bse_announcements


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now_minus(hours: float) -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    return dt.strftime("%Y-%m-%d %H:%M")


def _mk_headline(title: str = "Test headline", pub: str = "") -> dict:
    return {"title": title, "source": "Test", "published": pub, "url": ""}


def _mock_urlopen(body: dict):
    resp = MagicMock()
    resp.read.return_value = json.dumps(body).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ─────────────────────────────────────────────────────────────────────────────
# TestEventClassConstants
# ─────────────────────────────────────────────────────────────────────────────

class TestEventClassConstants:
    def test_event_classes_defined(self):
        assert len(EVENT_CLASSES) >= 8
        assert "EARNINGS_SURPRISE" in EVENT_CLASSES
        assert "REGULATORY_SHOCK" in EVENT_CLASSES
        assert "ROUTINE" in EVENT_CLASSES

    def test_all_event_classes_have_multiplier(self):
        for ec in EVENT_CLASSES:
            assert ec in _EVENT_MULTIPLIERS, f"{ec} missing from _EVENT_MULTIPLIERS"

    def test_all_event_classes_have_half_life(self):
        for ec in EVENT_CLASSES:
            assert ec in _HALF_LIVES_HOURS, f"{ec} missing from _HALF_LIVES_HOURS"

    def test_regulatory_shock_highest_multiplier(self):
        assert _EVENT_MULTIPLIERS["REGULATORY_SHOCK"] >= max(
            v for k, v in _EVENT_MULTIPLIERS.items() if k != "REGULATORY_SHOCK"
        )

    def test_routine_lowest_multiplier(self):
        assert _EVENT_MULTIPLIERS["ROUTINE"] <= 1.0

    def test_regulatory_shock_longest_half_life(self):
        assert _HALF_LIVES_HOURS["REGULATORY_SHOCK"] >= 24


# ─────────────────────────────────────────────────────────────────────────────
# TestBatchClassifyHeadlines
# ─────────────────────────────────────────────────────────────────────────────

class TestBatchClassifyHeadlines:
    def _haiku_body(self, classifications: list[dict]) -> dict:
        return {"content": [{"text": json.dumps(classifications)}]}

    def test_batch_classify_returns_list_same_length(self):
        headlines = [_mk_headline(f"Headline {i}") for i in range(3)]
        mock_resp = _mock_urlopen(self._haiku_body([
            {"idx": 0, "event_class": "EARNINGS_SURPRISE", "score": 75, "key_claim": "beat"},
            {"idx": 1, "event_class": "REGULATORY_SHOCK", "score": 20, "key_claim": "sebi"},
            {"idx": 2, "event_class": "ROUTINE", "score": 50, "key_claim": "agm"},
        ]))
        with (
            patch("agents.sentiment.urlopen", return_value=mock_resp),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            results = _batch_classify_headlines(headlines, "RELIANCE")
        assert len(results) == 3

    def test_event_class_assigned(self):
        headlines = [_mk_headline("Nifty crash on SEBI order")]
        mock_resp = _mock_urlopen(self._haiku_body([
            {"idx": 0, "event_class": "REGULATORY_SHOCK", "score": 15, "key_claim": "sebi"},
        ]))
        with (
            patch("agents.sentiment.urlopen", return_value=mock_resp),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            results = _batch_classify_headlines(headlines, "TCS")
        assert results[0]["event_class"] == "REGULATORY_SHOCK"

    def test_invalid_event_class_normalised_to_unknown(self):
        headlines = [_mk_headline("Some news")]
        mock_resp = _mock_urlopen(self._haiku_body([
            {"idx": 0, "event_class": "INVALID_CLASS", "score": 50, "key_claim": "x"},
        ]))
        with (
            patch("agents.sentiment.urlopen", return_value=mock_resp),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            results = _batch_classify_headlines(headlines, "TCS")
        assert results[0]["event_class"] == "UNKNOWN"

    def test_missing_idx_uses_fallback(self):
        headlines = [_mk_headline("No idx entry")]
        mock_resp = _mock_urlopen(self._haiku_body([
            {"event_class": "ROUTINE", "score": 50}  # no idx
        ]))
        with (
            patch("agents.sentiment.urlopen", return_value=mock_resp),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            results = _batch_classify_headlines(headlines, "TCS")
        assert len(results) == 1
        assert results[0].get("fallback") is True or "event_class" in results[0]

    def test_no_api_key_uses_keyword_fallback(self):
        headlines = [_mk_headline("Profit beat analysts")]
        with patch.dict(os.environ, {}, clear=True):
            results = _batch_classify_headlines(headlines, "INFY")
        assert len(results) == 1
        assert results[0].get("fallback") is True

    def test_haiku_json_error_falls_back(self):
        bad_resp = MagicMock()
        bad_resp.read.return_value = b'{"content": [{"text": "NOT JSON"}]}'
        bad_resp.__enter__ = lambda s: s
        bad_resp.__exit__ = MagicMock(return_value=False)
        with (
            patch("agents.sentiment.urlopen", return_value=bad_resp),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            results = _batch_classify_headlines([_mk_headline("Test")], "TCS")
        assert len(results) == 1
        # Should use keyword fallback
        assert results[0].get("fallback") is True

    def test_score_range_clamped(self):
        headlines = [_mk_headline("Extreme news")]
        mock_resp = _mock_urlopen(self._haiku_body([
            {"idx": 0, "event_class": "ROUTINE", "score": 150, "key_claim": "x"},
        ]))
        with (
            patch("agents.sentiment.urlopen", return_value=mock_resp),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            results = _batch_classify_headlines(headlines, "TCS")
        assert 0 <= results[0]["score"] <= 100


# ─────────────────────────────────────────────────────────────────────────────
# TestTemporalWeight
# ─────────────────────────────────────────────────────────────────────────────

class TestTemporalWeight:
    def test_weight_is_1_for_fresh_headline(self):
        h = _mk_headline(pub=_now_minus(0.1))
        w = _temporal_weight(h, "ROUTINE")
        assert 0.9 <= w <= 1.0, f"Expected ~1.0 for very fresh, got {w}"

    def test_weight_is_half_at_half_life(self):
        # ROUTINE half-life = 2h
        h = _mk_headline(pub=_now_minus(2.0))
        w = _temporal_weight(h, "ROUTINE")
        assert 0.45 <= w <= 0.55, f"Expected ~0.5 at half-life, got {w}"

    def test_earnings_half_life_6h(self):
        h = _mk_headline(pub=_now_minus(6.0))
        w = _temporal_weight(h, "EARNINGS_SURPRISE")
        assert 0.45 <= w <= 0.55, f"Expected ~0.5 at EARNINGS_SURPRISE half-life, got {w}"

    def test_regulatory_shock_half_life_48h(self):
        h = _mk_headline(pub=_now_minus(48.0))
        w = _temporal_weight(h, "REGULATORY_SHOCK")
        assert 0.45 <= w <= 0.55, f"Expected ~0.5 at REG_SHOCK half-life, got {w}"

    def test_old_news_has_low_weight(self):
        h = _mk_headline(pub=_now_minus(72.0))
        w = _temporal_weight(h, "ROUTINE")
        assert w < 0.1, f"72h-old ROUTINE news should be near 0, got {w}"

    def test_missing_published_returns_1(self):
        h = _mk_headline(pub="")
        w = _temporal_weight(h, "ROUTINE")
        assert w == 1.0

    def test_date_only_string_accepted(self):
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        h = _mk_headline(pub=yesterday)
        w = _temporal_weight(h, "ROUTINE")
        assert 0.0 < w <= 1.0

    def test_invalid_date_returns_1(self):
        h = _mk_headline(pub="not-a-date")
        w = _temporal_weight(h, "ROUTINE")
        assert w == 1.0

    def test_weight_monotonically_decreases_with_age(self):
        ages = [0, 1, 3, 6, 12, 24]
        weights = [_temporal_weight(_mk_headline(pub=_now_minus(a)), "MACRO_CATALYST") for a in ages]
        for i in range(1, len(weights)):
            assert weights[i] <= weights[i-1], "Weight should decrease with age"


# ─────────────────────────────────────────────────────────────────────────────
# TestFinBertHF
# ─────────────────────────────────────────────────────────────────────────────

class TestFinBertHF:
    def test_parses_nested_list_response(self):
        mock_resp = _mock_urlopen([
            [{"label": "positive", "score": 0.8},
             {"label": "negative", "score": 0.1},
             {"label": "neutral",  "score": 0.1}]
        ])
        with patch("agents.sentiment.urlopen", return_value=mock_resp):
            result = _call_finbert_hf("Nifty surges on strong earnings")
        assert result is not None
        assert 0 <= result["positive"] <= 1.0
        assert 0 <= result["negative"] <= 1.0

    def test_parses_flat_dict_response(self):
        mock_resp = _mock_urlopen([{"label": "positive", "score": 0.9}])
        with patch("agents.sentiment.urlopen", return_value=mock_resp):
            result = _call_finbert_hf("Strong earnings beat")
        assert result is not None

    def test_returns_none_on_network_error(self):
        from urllib.error import URLError
        with patch("agents.sentiment.urlopen", side_effect=URLError("timeout")):
            result = _call_finbert_hf("Any headline")
        assert result is None

    def test_returns_none_on_empty_response(self):
        mock_resp = _mock_urlopen([])
        with patch("agents.sentiment.urlopen", return_value=mock_resp):
            result = _call_finbert_hf("Any headline")
        assert result is None

    def test_handles_hf_token_env_var(self):
        """HF token is added to headers when set (no error)."""
        mock_resp = _mock_urlopen([[{"label": "positive", "score": 0.7}, {"label": "negative", "score": 0.2}, {"label": "neutral", "score": 0.1}]])
        with (
            patch("agents.sentiment.urlopen", return_value=mock_resp),
            patch.dict(os.environ, {"HF_API_TOKEN": "hf-test-token"}),
        ):
            result = _call_finbert_hf("Test")
        assert result is not None


# ─────────────────────────────────────────────────────────────────────────────
# TestFinBertToScore
# ─────────────────────────────────────────────────────────────────────────────

class TestFinBertToScore:
    def test_all_positive_gives_100(self):
        assert _finbert_to_score({"positive": 1.0, "negative": 0.0, "neutral": 0.0}) == 100

    def test_all_negative_gives_0(self):
        assert _finbert_to_score({"positive": 0.0, "negative": 1.0, "neutral": 0.0}) == 0

    def test_all_neutral_gives_50(self):
        assert _finbert_to_score({"positive": 0.0, "negative": 0.0, "neutral": 1.0}) == 50

    def test_mixed_positive_negative(self):
        score = _finbert_to_score({"positive": 0.6, "negative": 0.3, "neutral": 0.1})
        assert score > 50, "More positive than negative → above 50"

    def test_output_clamped_to_0_100(self):
        # Edge: probabilities slightly > 1 due to float arithmetic
        score = _finbert_to_score({"positive": 1.01, "negative": 0.0, "neutral": 0.0})
        assert 0 <= score <= 100


# ─────────────────────────────────────────────────────────────────────────────
# TestBseAnnouncements
# ─────────────────────────────────────────────────────────────────────────────

class TestBseAnnouncements:
    # get_bse_announcements uses local-import urlopen inside the function body,
    # so we patch the stdlib target directly.
    _PATCH_PATH = "urllib.request.urlopen"

    def _mock_bse_resp(self, data: dict) -> MagicMock:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(data).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_returns_list_on_valid_api_response(self):
        mock_data = {
            "Table": [{
                "HEADLINE": "Results for Q4 FY25",
                "COMPANYNAME": "Reliance Industries Ltd",
                "NEWS_DT": "2025-06-15",
                "NEWSID": "123",
                "SCRIP_CD": "500325",
            }]
        }
        with patch(self._PATCH_PATH, return_value=self._mock_bse_resp(mock_data)):
            result = get_bse_announcements("RELIANCE", hours=24)
        assert isinstance(result, list)
        assert len(result) == 1
        assert "Reliance Industries Ltd" in result[0]["title"] or "Results" in result[0]["title"]

    def test_returns_empty_list_on_network_error(self):
        from urllib.error import URLError
        with patch(self._PATCH_PATH, side_effect=URLError("timeout")):
            result = get_bse_announcements("RELIANCE", hours=24)
        assert isinstance(result, list)
        assert result == []

    def test_symbol_without_ns_suffix(self):
        # Should not raise — .NS stripped internally
        with patch(self._PATCH_PATH, return_value=self._mock_bse_resp({"Table": []})):
            result = get_bse_announcements("RELIANCE.NS", hours=24)
        assert isinstance(result, list)

    def test_empty_symbol_market_wide(self):
        with patch(self._PATCH_PATH, return_value=self._mock_bse_resp({"Table": []})):
            result = get_bse_announcements("", hours=24)
        assert isinstance(result, list)


# ─────────────────────────────────────────────────────────────────────────────
# TestAnalyseIntegration
# ─────────────────────────────────────────────────────────────────────────────

class TestAnalyseIntegration:
    """Integration tests for the upgraded analyse() function."""

    def _mock_analyse(self, symbol="TCS.NS"):
        """Run analyse() with all external calls mocked out."""
        from agents.sentiment import analyse

        sample_headlines = [
            {"title": "TCS Q4 profit beats estimates", "source": "ET", "published": _now_minus(2), "url": ""},
            {"title": "TCS announces share buyback", "source": "MC", "published": _now_minus(4), "url": ""},
        ]
        classified = [
            {"event_class": "EARNINGS_SURPRISE", "score": 75, "key_claim": "beat", "sentiment": "bullish", "fallback": False},
            {"event_class": "M_A_SIGNAL",        "score": 70, "key_claim": "buyback", "sentiment": "bullish", "fallback": False},
        ]
        finbert_result = {"positive": 0.75, "negative": 0.1, "neutral": 0.15}

        with (
            patch("agents.sentiment.get_rss_headlines", return_value=sample_headlines),
            patch("agents.sentiment.get_bse_announcements", return_value=[]),
            patch("agents.sentiment._fetch_newsapi", return_value=[]),
            patch("agents.sentiment._batch_classify_headlines", return_value=classified),
            patch("agents.sentiment._call_finbert_hf", return_value=finbert_result),
            patch("agents.sentiment.get_nse_fii_dii", return_value={"fii_net": 500.0}),
            patch("agents.sentiment.get_promoter_signal", return_value={"signal": "NEUTRAL"}),
            patch("agents.sentiment._write_agent_performance", return_value=None),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            return analyse(symbol)

    def test_analyse_returns_valid_signal(self):
        result = self._mock_analyse()
        valid = {"BULLISH", "MILDLY_BULLISH", "NEUTRAL", "MILDLY_BEARISH", "BEARISH", "NO_DATA"}
        assert result["signal"] in valid

    def test_analyse_detail_has_event_class_breakdown(self):
        result = self._mock_analyse()
        assert "event_class_breakdown" in result["detail"]
        assert isinstance(result["detail"]["event_class_breakdown"], dict)

    def test_analyse_detail_has_temporal_decay_flag(self):
        result = self._mock_analyse()
        assert result["detail"].get("temporal_decay_applied") is True

    def test_analyse_detail_has_finbert_flag(self):
        result = self._mock_analyse()
        assert "finbert_used" in result["detail"]

    def test_analyse_preserves_existing_keys(self):
        result = self._mock_analyse()
        required = {"signal", "score", "detail", "danger_signals", "data_sources", "agent_name"}
        assert required <= set(result.keys())
        required_detail = {
            "headlines_analysed", "avg_score", "sentiment_breakdown",
            "scored_headlines", "rolling_trend", "misinformation_flags",
            "haiku_calls_used",
        }
        assert required_detail <= set(result["detail"].keys())
