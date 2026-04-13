"""
tests/test_sentiment.py
pytest suite for agents/sentiment.py

Run from project root:
    pytest tests/test_sentiment.py -v
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch, call
from urllib.error import HTTPError, URLError
import io

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.sentiment import (  # noqa: E402
    AGENT_NAME,
    MAX_HAIKU_CALLS,
    HAIKU_MODEL,
    analyse,
    _call_haiku,
    _detect_danger_signals,
    _detect_misinformation,
    _extract_domain,
    _fallback_score,
    _fetch_newsapi,
    _phrase_fingerprint,
    _rolling_trend,
)

# ──────────────────────────────────────────────────────────────────────────────
# Fixture data
# ──────────────────────────────────────────────────────────────────────────────

REQUIRED_KEYS = {
    "signal", "score", "detail", "danger_signals", "data_sources", "agent_name"
}
REQUIRED_DETAIL_KEYS = {
    "headlines_analysed", "avg_score", "sentiment_breakdown",
    "scored_headlines", "rolling_trend", "misinformation_flags",
    "haiku_calls_used",
}
VALID_SIGNALS = {
    "BULLISH", "MILDLY_BULLISH", "NEUTRAL", "MILDLY_BEARISH", "BEARISH", "NO_DATA"
}


def _make_headlines(n: int = 5, source: str = "ET Markets",
                    prefix: str = "Reliance Q3 results") -> list[dict]:
    return [
        {
            "title": f"{prefix} headline {i}",
            "source": source,
            "published": "2024-01-15",
            "url": f"https://economictimes.indiatimes.com/article-{i}",
        }
        for i in range(n)
    ]


def _haiku_response(sentiment: str = "bullish", score: int = 75,
                    key_claim: str = "strong earnings") -> dict:
    return {"sentiment": sentiment, "score": score, "key_claim": key_claim}


# ──────────────────────────────────────────────────────────────────────────────
# Autouse: suppress Supabase + real network
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolate():
    with patch("agents.sentiment._write_agent_performance"), \
         patch("agents.sentiment.get_nse_fii_dii", return_value=None):
        yield


# ──────────────────────────────────────────────────────────────────────────────
# Unit: _extract_domain
# ──────────────────────────────────────────────────────────────────────────────

class TestExtractDomain:
    def test_https_url(self):
        assert _extract_domain("https://www.moneycontrol.com/news/abc") == "moneycontrol.com"

    def test_http_no_www(self):
        assert _extract_domain("http://economictimes.indiatimes.com/xyz") == "economictimes.indiatimes.com"

    def test_empty_string(self):
        assert _extract_domain("") == ""

    def test_query_params_stripped(self):
        domain = _extract_domain("https://livemint.com/article?ref=123")
        assert "?" not in domain


# ──────────────────────────────────────────────────────────────────────────────
# Unit: _phrase_fingerprint
# ──────────────────────────────────────────────────────────────────────────────

class TestPhraseFingerprint:
    def test_same_title_same_fp(self):
        t = "Reliance Industries reports record profit in Q3"
        assert _phrase_fingerprint(t) == _phrase_fingerprint(t)

    def test_different_titles_different_fp(self):
        fp1 = _phrase_fingerprint("HDFC Bank beats estimates")
        fp2 = _phrase_fingerprint("Infosys misses revenue target")
        assert fp1 != fp2

    def test_stop_words_ignored(self):
        """Titles that differ only in stop words should have identical fingerprint."""
        fp1 = _phrase_fingerprint("the company is reporting strong results")
        fp2 = _phrase_fingerprint("a company are reporting strong results")
        assert fp1 == fp2

    def test_returns_12_char_hex(self):
        fp = _phrase_fingerprint("Some headline here")
        assert len(fp) == 12
        assert all(c in "0123456789abcdef" for c in fp)


# ──────────────────────────────────────────────────────────────────────────────
# Unit: _fallback_score
# ──────────────────────────────────────────────────────────────────────────────

class TestFallbackScore:
    def test_bullish_headline(self):
        result = _fallback_score("Company posts record profit and strong growth")
        assert result["sentiment"] == "bullish"
        assert result["score"] > 50

    def test_bearish_headline(self):
        result = _fallback_score("SEBI fraud probe crash loss default penalty")
        assert result["sentiment"] == "bearish"
        assert result["score"] < 50

    def test_neutral_headline(self):
        result = _fallback_score("Company holds annual general meeting")
        assert result["sentiment"] == "neutral"
        assert result["score"] == 50

    def test_score_always_0_to_100(self):
        for h in [
            "profit profit profit profit profit profit profit",
            "loss loss loss loss loss loss loss loss loss",
            "something completely unremarkable happened today",
        ]:
            r = _fallback_score(h)
            assert 0 <= r["score"] <= 100

    def test_fallback_flag_set(self):
        result = _fallback_score("neutral headline")
        assert result.get("fallback") is True


# ──────────────────────────────────────────────────────────────────────────────
# Unit: _detect_misinformation
# ──────────────────────────────────────────────────────────────────────────────

class TestDetectMisinformation:
    def test_three_identical_from_same_domain(self):
        """3+ identical-phrase articles from same domain → flagged."""
        headlines = [
            {
                "title": "Reliance Industries reports record profit",
                "url": "https://fakeblog.com/article-1",
            },
            {
                "title": "Reliance Industries reports record profit",
                "url": "https://fakeblog.com/article-2",
            },
            {
                "title": "Reliance Industries reports record profit",
                "url": "https://fakeblog.com/article-3",
            },
        ]
        flags = _detect_misinformation(headlines)
        assert len(flags) == 1
        assert flags[0]["count"] == 3
        assert flags[0]["flag"] == "coordinated_amplification"

    def test_two_identical_not_flagged(self):
        """Only 2 identical → below threshold, no flag."""
        headlines = [
            {"title": "TCS beats Q3 estimates", "url": "https://site.com/a"},
            {"title": "TCS beats Q3 estimates", "url": "https://site.com/b"},
        ]
        assert _detect_misinformation(headlines) == []

    def test_different_domains_not_flagged(self):
        """Same phrase across different domains is normal syndication, not coordinated."""
        headlines = [
            {"title": "TCS profit up 20 percent this quarter results", "url": "https://site-a.com/1"},
            {"title": "TCS profit up 20 percent this quarter results", "url": "https://site-b.com/2"},
            {"title": "TCS profit up 20 percent this quarter results", "url": "https://site-c.com/3"},
        ]
        assert _detect_misinformation(headlines) == []

    def test_empty_list_no_crash(self):
        assert _detect_misinformation([]) == []

    def test_flag_contains_domain_and_count(self):
        headlines = [
            {"title": "Stock manipulation detected fraud scheme", "url": "https://pump.io/a"},
            {"title": "Stock manipulation detected fraud scheme", "url": "https://pump.io/b"},
            {"title": "Stock manipulation detected fraud scheme", "url": "https://pump.io/c"},
        ]
        flags = _detect_misinformation(headlines)
        assert flags[0]["domain"] == "pump.io"
        assert flags[0]["count"] >= 3


# ──────────────────────────────────────────────────────────────────────────────
# Unit: _rolling_trend
# ──────────────────────────────────────────────────────────────────────────────

class TestRollingTrend:
    def test_improving_trend(self):
        scored = (
            [{"published": "2024-01-10", "score": 30}] * 3 +
            [{"published": "2024-01-14", "score": 75}] * 3
        )
        trend = _rolling_trend(scored)
        assert trend["direction"] == "IMPROVING"

    def test_declining_trend(self):
        scored = (
            [{"published": "2024-01-10", "score": 75}] * 3 +
            [{"published": "2024-01-14", "score": 30}] * 3
        )
        trend = _rolling_trend(scored)
        assert trend["direction"] == "DECLINING"

    def test_stable_trend(self):
        scored = [{"published": "2024-01-10", "score": 50}] * 6
        trend = _rolling_trend(scored)
        assert trend["direction"] == "STABLE"

    def test_empty_scored_no_crash(self):
        trend = _rolling_trend([])
        assert "direction" in trend
        assert isinstance(trend["dates"], list)

    def test_dates_and_scores_same_length(self):
        scored = [
            {"published": "2024-01-10", "score": 60},
            {"published": "2024-01-11", "score": 55},
            {"published": "2024-01-12", "score": 70},
        ]
        trend = _rolling_trend(scored)
        assert len(trend["dates"]) == len(trend["avg_scores"])

    def test_at_most_7_days(self):
        scored = [
            {"published": f"2024-01-{i:02d}", "score": 50}
            for i in range(1, 15)
        ]
        trend = _rolling_trend(scored)
        assert len(trend["dates"]) <= 7


# ──────────────────────────────────────────────────────────────────────────────
# Unit: _detect_danger_signals
# ──────────────────────────────────────────────────────────────────────────────

class TestDetectDangerSignals:
    def _make_auth_regulatory(self, n: int = 5) -> tuple[list, list]:
        headlines = [
            {
                "title": f"SEBI probe into stock manipulation case {i}",
                "source": "Economic Times",
                "url": "https://economictimes.indiatimes.com/a",
            }
            for i in range(n)
        ]
        scored = [
            {"title": h["title"], "score": 20, "sentiment": "bearish"}
            for h in headlines
        ]
        return headlines, scored

    def test_critical_five_auth_plus_fii_selling(self):
        """5+ authoritative + regulatory headlines AND FII selling → CRITICAL."""
        headlines, scored = self._make_auth_regulatory(5)
        signals = _detect_danger_signals(headlines, scored, fii_net=-5000.0)
        types = [s["type"] for s in signals]
        assert "CRITICAL" in types

    def test_no_critical_without_fii_selling(self):
        """Regulatory headlines alone (no FII outflow) must not fire CRITICAL."""
        headlines, scored = self._make_auth_regulatory(5)
        signals = _detect_danger_signals(headlines, scored, fii_net=None)
        types = [s["type"] for s in signals]
        assert "CRITICAL" not in types

    def test_no_critical_with_only_4_auth_sources(self):
        """Only 4 authoritative sources → threshold not met."""
        headlines, scored = self._make_auth_regulatory(4)
        signals = _detect_danger_signals(headlines, scored, fii_net=-3000.0)
        types = [s["type"] for s in signals]
        assert "CRITICAL" not in types

    def test_fii_watch_signal_alone(self):
        """FII selling with no regulatory headlines → WATCH only."""
        headlines = [{"title": "Good results", "source": "unknown", "url": ""}]
        scored    = [{"title": "Good results", "score": 70, "sentiment": "bullish"}]
        signals   = _detect_danger_signals(headlines, scored, fii_net=-1000.0)
        types = [s["type"] for s in signals]
        assert "WATCH" in types
        assert "CRITICAL" not in types

    def test_no_signals_for_clean_data(self):
        headlines = [{"title": "Company posts record profit", "source": "ET Markets", "url": ""}]
        scored    = [{"title": "Company posts record profit", "score": 80, "sentiment": "bullish"}]
        signals   = _detect_danger_signals(headlines, scored, fii_net=500.0)
        assert signals == []

    def test_critical_signal_has_required_fields(self):
        headlines, scored = self._make_auth_regulatory(6)
        signals = _detect_danger_signals(headlines, scored, fii_net=-8000.0)
        crit = next((s for s in signals if s["type"] == "CRITICAL"), None)
        assert crit is not None
        assert "label" in crit
        assert "description" in crit
        assert "authoritative_count" in crit


# ──────────────────────────────────────────────────────────────────────────────
# Unit: _call_haiku  (mocked HTTP)
# ──────────────────────────────────────────────────────────────────────────────

class TestCallHaiku:
    def _mock_urlopen(self, response_dict: dict):
        """Return a context-manager mock that yields a fake HTTP response."""
        body = json.dumps({
            "content": [{"text": json.dumps(response_dict)}]
        }).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_parses_valid_haiku_response(self):
        payload = {"sentiment": "bullish", "score": 78, "key_claim": "profit beat"}
        mock_resp = self._mock_urlopen(payload)
        with patch("agents.sentiment.urlopen", return_value=mock_resp), \
             patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
            result = _call_haiku("Company posts record profit", "RELIANCE")
        assert result["sentiment"] == "bullish"
        assert result["score"] == 78
        assert result["key_claim"] == "profit beat"

    def test_score_clamped_0_to_100(self):
        payload = {"sentiment": "bullish", "score": 999, "key_claim": ""}
        mock_resp = self._mock_urlopen(payload)
        with patch("agents.sentiment.urlopen", return_value=mock_resp), \
             patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
            result = _call_haiku("Test headline", "TCS")
        assert result["score"] == 100

    def test_strips_markdown_fences(self):
        """Haiku sometimes wraps JSON in ```json ... ``` — must be stripped."""
        inner = json.dumps({"sentiment": "neutral", "score": 50, "key_claim": "test"})
        body = json.dumps({
            "content": [{"text": f"```json\n{inner}\n```"}]
        }).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("agents.sentiment.urlopen", return_value=mock_resp), \
             patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
            result = _call_haiku("Test headline", "TCS")
        assert result["sentiment"] == "neutral"

    def test_raises_on_http_error(self):
        exc = HTTPError(url="", code=429, msg="Too Many Requests", hdrs={}, fp=None)
        with patch("agents.sentiment.urlopen", side_effect=exc), \
             patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
            with pytest.raises(RuntimeError, match="Haiku API error"):
                _call_haiku("Test headline", "TCS")

    def test_raises_without_api_key(self):
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
                _call_haiku("Test headline", "TCS")

    def test_uses_correct_model(self):
        """Request body must reference the haiku model constant."""
        payload = {"sentiment": "neutral", "score": 50, "key_claim": ""}
        mock_resp = self._mock_urlopen(payload)
        captured_body = {}

        def fake_urlopen(req, timeout=None):
            captured_body["data"] = json.loads(req.data.decode())
            return mock_resp

        with patch("agents.sentiment.urlopen", side_effect=fake_urlopen), \
             patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
            _call_haiku("Test headline", "TCS")
        assert captured_body["data"]["model"] == HAIKU_MODEL


# ──────────────────────────────────────────────────────────────────────────────
# Unit: _fetch_newsapi
# ──────────────────────────────────────────────────────────────────────────────

class TestFetchNewsAPI:
    def _mock_newsapi_response(self, articles: list) -> MagicMock:
        body = json.dumps({"status": "ok", "articles": articles}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_returns_empty_without_api_key(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("NEWSAPI_KEY", "NEWS_API_KEY")}
        with patch.dict(os.environ, env, clear=True):
            result = _fetch_newsapi("RELIANCE")
        assert result == []

    def test_parses_articles_correctly(self):
        articles = [
            {
                "title": "Reliance profit surges",
                "source": {"name": "Reuters"},
                "publishedAt": "2024-01-15T10:00:00Z",
                "url": "https://reuters.com/article-1",
            }
        ]
        mock_resp = self._mock_newsapi_response(articles)
        with patch("agents.sentiment.urlopen", return_value=mock_resp), \
             patch.dict(os.environ, {"NEWSAPI_KEY": "test-key"}):
            result = _fetch_newsapi("RELIANCE")
        assert len(result) == 1
        assert result[0]["title"] == "Reliance profit surges"
        assert result[0]["source"] == "Reuters"

    def test_filters_removed_articles(self):
        """Articles with '[Removed]' title must be dropped."""
        articles = [
            {"title": "[Removed]", "source": {"name": "X"},
             "publishedAt": "2024-01-15T10:00:00Z", "url": ""},
            {"title": "Good headline", "source": {"name": "Reuters"},
             "publishedAt": "2024-01-15T10:00:00Z", "url": "https://reuters.com/b"},
        ]
        mock_resp = self._mock_newsapi_response(articles)
        with patch("agents.sentiment.urlopen", return_value=mock_resp), \
             patch.dict(os.environ, {"NEWSAPI_KEY": "test-key"}):
            result = _fetch_newsapi("TCS")
        assert len(result) == 1
        assert result[0]["title"] == "Good headline"

    def test_network_error_returns_empty(self):
        with patch("agents.sentiment.urlopen", side_effect=URLError("timeout")), \
             patch.dict(os.environ, {"NEWSAPI_KEY": "test-key"}):
            result = _fetch_newsapi("INFY")
        assert result == []


# ──────────────────────────────────────────────────────────────────────────────
# Integration: analyse()
# ──────────────────────────────────────────────────────────────────────────────

def _run_analyse(
    symbol: str = "RELIANCE",
    rss_headlines: list | None = None,
    newsapi_articles: list | None = None,
    haiku_score: dict | None = None,
    fii_net: float | None = None,
):
    """
    Run analyse() with fully mocked external calls.
    haiku_score=None → fall back to keyword scoring (no API key).
    """
    if rss_headlines is None:
        rss_headlines = _make_headlines(5)
    if newsapi_articles is None:
        newsapi_articles = []

    env_patch = {}
    if haiku_score is not None:
        env_patch["ANTHROPIC_API_KEY"] = "sk-test"

    def fake_haiku(headline, sym):
        return haiku_score or {"sentiment": "neutral", "score": 50, "key_claim": ""}

    fii_return = {"date": "2024-01-15", "fii_net": fii_net, "dii_net": 0} if fii_net is not None else None

    with patch("agents.sentiment.get_rss_headlines", return_value=rss_headlines), \
         patch("agents.sentiment._fetch_newsapi", return_value=newsapi_articles), \
         patch("agents.sentiment._call_haiku", side_effect=fake_haiku), \
         patch("agents.sentiment.get_nse_fii_dii", return_value=fii_return), \
         patch.dict(os.environ, env_patch):
        return analyse(symbol)


class TestAnalyseSchema:
    def test_top_level_keys(self):
        result = _run_analyse()
        assert REQUIRED_KEYS.issubset(result.keys())

    def test_detail_keys(self):
        result = _run_analyse()
        assert REQUIRED_DETAIL_KEYS.issubset(result["detail"].keys())

    def test_agent_name(self):
        assert _run_analyse()["agent_name"] == AGENT_NAME == "sentiment"

    def test_signal_in_valid_set(self):
        assert _run_analyse()["signal"] in VALID_SIGNALS

    def test_score_0_to_100(self):
        result = _run_analyse()
        assert 0 <= result["score"] <= 100

    def test_danger_signals_is_list(self):
        result = _run_analyse()
        assert isinstance(result["danger_signals"], list)

    def test_data_sources_is_list(self):
        result = _run_analyse()
        assert isinstance(result["data_sources"], list)


class TestAnalyseNoData:
    def test_no_rss_no_newsapi_returns_no_data(self):
        with patch("agents.sentiment.get_rss_headlines", return_value=None), \
             patch("agents.sentiment._fetch_newsapi", return_value=[]):
            result = analyse("EMPTY")
        assert result["signal"] == "NO_DATA"
        assert result["score"] == 50  # neutral default

    def test_empty_rss_no_newsapi_returns_no_data(self):
        with patch("agents.sentiment.get_rss_headlines", return_value=[]), \
             patch("agents.sentiment._fetch_newsapi", return_value=[]):
            result = analyse("EMPTY")
        assert result["signal"] == "NO_DATA"


class TestAnalyseBullishScenario:
    def test_bullish_headlines_produce_bullish_signal(self):
        headlines = _make_headlines(6, prefix="Record profit beat strong growth rally")
        result = _run_analyse(
            rss_headlines=headlines,
            haiku_score={"sentiment": "bullish", "score": 80, "key_claim": "record profit"},
        )
        assert result["signal"] in ("BULLISH", "MILDLY_BULLISH")
        assert result["score"] > 50

    def test_sentiment_breakdown_counts_correctly(self):
        headlines = _make_headlines(4)
        result = _run_analyse(
            rss_headlines=headlines,
            haiku_score={"sentiment": "bullish", "score": 75, "key_claim": "good"},
        )
        breakdown = result["detail"]["sentiment_breakdown"]
        assert breakdown["bullish"] == 4
        assert breakdown["bearish"] == 0


class TestAnalyseBearishScenario:
    def test_bearish_headlines_produce_bearish_signal(self):
        headlines = _make_headlines(6, prefix="SEBI fraud probe default loss penalty crash")
        result = _run_analyse(
            rss_headlines=headlines,
            haiku_score={"sentiment": "bearish", "score": 20, "key_claim": "sebi probe"},
        )
        assert result["signal"] in ("BEARISH", "MILDLY_BEARISH")
        assert result["score"] < 50


class TestAnalyseDangerScenario:
    def test_critical_danger_forces_bearish_signal(self):
        """5 ET/BS authoritative + regulatory + FII selling → BEARISH signal."""
        reg_headlines = [
            {
                "title": f"SEBI probe into stock manipulation case {i}",
                "source": "Economic Times",
                "url": "https://economictimes.indiatimes.com/a",
                "published": "2024-01-15",
            }
            for i in range(6)
        ]
        result = _run_analyse(
            rss_headlines=reg_headlines,
            haiku_score={"sentiment": "bearish", "score": 15, "key_claim": "sebi probe"},
            fii_net=-6000.0,
        )
        assert result["signal"] == "BEARISH"
        types = [d["type"] for d in result["danger_signals"]]
        assert "CRITICAL" in types

    def test_danger_signal_fields_present(self):
        reg_headlines = [
            {
                "title": f"SEBI enforcement action fraud probe {i}",
                "source": "Business Standard",
                "url": "https://business-standard.com/a",
                "published": "2024-01-15",
            }
            for i in range(5)
        ]
        result = _run_analyse(
            rss_headlines=reg_headlines,
            haiku_score={"sentiment": "bearish", "score": 18, "key_claim": "sebi"},
            fii_net=-4500.0,
        )
        crit = next((d for d in result["danger_signals"] if d["type"] == "CRITICAL"), None)
        if crit:
            assert "label" in crit
            assert "description" in crit


class TestAnalyseRateLimit:
    def test_haiku_calls_capped_at_max(self):
        """With 15 unique headlines, Haiku must not be called more than MAX_HAIKU_CALLS."""
        headlines = _make_headlines(15, prefix="Unique headline number")
        # Give each a unique title so dedup doesn't remove them
        for i, h in enumerate(headlines):
            h["title"] = f"Completely unique headline number {i} text here now"

        call_count = {"n": 0}
        def counting_haiku(headline, sym):
            call_count["n"] += 1
            return {"sentiment": "neutral", "score": 50, "key_claim": ""}

        with patch("agents.sentiment.get_rss_headlines", return_value=headlines), \
             patch("agents.sentiment._fetch_newsapi", return_value=[]), \
             patch("agents.sentiment._call_haiku", side_effect=counting_haiku), \
             patch("agents.sentiment.get_nse_fii_dii", return_value=None), \
             patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
            result = analyse("TEST")

        assert call_count["n"] <= MAX_HAIKU_CALLS
        assert result["detail"]["haiku_calls_used"] <= MAX_HAIKU_CALLS

    def test_haiku_calls_used_reported_in_detail(self):
        result = _run_analyse(
            rss_headlines=_make_headlines(3),
            haiku_score={"sentiment": "neutral", "score": 50, "key_claim": ""},
        )
        assert "haiku_calls_used" in result["detail"]
        assert isinstance(result["detail"]["haiku_calls_used"], int)


class TestAnalyseMisinformation:
    def test_misinfo_flags_in_detail(self):
        """Coordinated identical headlines from same domain → misinformation flag."""
        headlines = [
            {
                "title": "Reliance Industries major acquisition deal announced",
                "source": "FakeBlog",
                "url": "https://fakeblog.xyz/a",
                "published": "2024-01-15",
            }
        ] * 4   # 4 identical from same domain
        result = _run_analyse(rss_headlines=headlines)
        assert isinstance(result["detail"]["misinformation_flags"], list)
        # Note: dedup removes duplicates before misinfo check; this tests the pipeline
        # still runs without crash; real misinfo test is in TestDetectMisinformation

    def test_no_misinfo_for_clean_headlines(self):
        clean = [
            {"title": f"Unique headline about stock {i}", "source": f"source{i}",
             "url": f"https://site{i}.com/a", "published": "2024-01-15"}
            for i in range(5)
        ]
        result = _run_analyse(rss_headlines=clean)
        assert result["detail"]["misinformation_flags"] == []


class TestAnalyseDeduplication:
    def test_duplicate_titles_deduplicated(self):
        """Same headline from RSS and NewsAPI must only be scored once."""
        dup_title = "TCS beats Q3 estimates with strong margins"
        rss = [{"title": dup_title, "source": "ET", "published": "2024-01-15", "url": ""}]
        news = [{"title": dup_title, "source": "NewsAPI", "published": "2024-01-15", "url": ""}]
        result = _run_analyse(rss_headlines=rss, newsapi_articles=news)
        headlines_analysed = result["detail"]["headlines_analysed"]
        assert headlines_analysed == 1

    def test_data_sources_deduped(self):
        result = _run_analyse(
            rss_headlines=_make_headlines(3),
            newsapi_articles=_make_headlines(2),
        )
        assert len(result["data_sources"]) == len(set(result["data_sources"]))


class TestAnalyseHaikuFallback:
    def test_fallback_used_when_no_api_key(self):
        """With no ANTHROPIC_API_KEY, all scoring must use fallback, haiku_calls=0."""
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with patch("agents.sentiment.get_rss_headlines", return_value=_make_headlines(5)), \
             patch("agents.sentiment._fetch_newsapi", return_value=[]), \
             patch("agents.sentiment.get_nse_fii_dii", return_value=None), \
             patch.dict(os.environ, env, clear=True):
            result = analyse("TEST")
        assert result["detail"]["haiku_calls_used"] == 0
        assert "claude_haiku" not in result["data_sources"]

    def test_fallback_used_after_haiku_error(self):
        """If Haiku raises, fallback must be used and run must not crash."""
        with patch("agents.sentiment.get_rss_headlines", return_value=_make_headlines(3)), \
             patch("agents.sentiment._fetch_newsapi", return_value=[]), \
             patch("agents.sentiment._call_haiku", side_effect=RuntimeError("API error")), \
             patch("agents.sentiment.get_nse_fii_dii", return_value=None), \
             patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
            result = analyse("TEST")
        assert result["agent_name"] == "sentiment"
        assert result["detail"]["haiku_calls_used"] == 0


class TestAnalyseRollingTrend:
    def test_trend_present_in_detail(self):
        result = _run_analyse()
        assert "rolling_trend" in result["detail"]
        trend = result["detail"]["rolling_trend"]
        assert "direction" in trend
        assert trend["direction"] in ("IMPROVING", "DECLINING", "STABLE")

    def test_scored_headlines_list_structure(self):
        result = _run_analyse(rss_headlines=_make_headlines(3))
        for sh in result["detail"]["scored_headlines"]:
            assert "title" in sh
            assert "source" in sh
            assert "sentiment" in sh
            assert 0 <= sh["score"] <= 100
