"""
tests/test_historical_rag.py — Unit tests for agents/historical_rag.py

All network / Supabase calls are mocked. Tests cover:
- Tokenisation and TF-IDF vector helpers
- Cosine similarity correctness
- Keyword similarity ranking
- Signal derivation logic
- analyse() output schema, NO_DATA paths, embedding fallback
"""

import json
import math
import pytest
from unittest.mock import patch, MagicMock


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def no_supabase(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "")


@pytest.fixture(autouse=True)
def no_openai(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


from agents.historical_rag import (
    _tokenise,
    _tfidf_vector,
    _cosine,
    _cosine_vec,
    _keyword_similarity,
    _derive_signal,
    _fetch_all_events,
    _vector_search,
    _embed_openai,
    analyse,
)


# ──────────────────────────────────────────────────────────────────────────────
# Sample events
# ──────────────────────────────────────────────────────────────────────────────

_BULLISH_EVENT = {
    "id": "1",
    "event_type": "RBI_POLICY",
    "description": "RBI cut repo rate 50bp to stimulate economy during slowdown",
    "event_date": "2020-03-27",
    "affected_sectors": ["BANKING", "REALTY"],
    "market_impact": "STRONG_POSITIVE",
    "outcome": "Nifty rallied 12% in 30 days post rate cut announcement",
    "embedding": None,
}

_BEARISH_EVENT = {
    "id": "2",
    "event_type": "GLOBAL",
    "description": "FII massive selloff India equities USD 3bn in one week panic",
    "event_date": "2008-10-01",
    "affected_sectors": ["ALL"],
    "market_impact": "SEVERE_NEGATIVE",
    "outcome": "Nifty fell 60% peak to trough during financial crisis",
    "embedding": None,
}

_NEUTRAL_EVENT = {
    "id": "3",
    "event_type": "REGULATION",
    "description": "SEBI tightened F&O margin requirements amid volatility",
    "event_date": "2021-06-01",
    "affected_sectors": ["DERIVATIVES"],
    "market_impact": "MIXED",
    "outcome": "Volumes dropped 20% short term, recovered within 2 months",
    "embedding": None,
}


# ──────────────────────────────────────────────────────────────────────────────
# _tokenise
# ──────────────────────────────────────────────────────────────────────────────

class TestTokenise:
    def test_basic_tokenisation(self):
        tokens = _tokenise("FII selling 5000 Crore India equity")
        assert "fii" in tokens
        assert "selling" in tokens
        assert "5000" in tokens
        assert "crore" in tokens

    def test_stop_words_removed(self):
        tokens = _tokenise("the market is at a high")
        for stop in ["the", "is", "at", "a"]:
            assert stop not in tokens

    def test_short_tokens_removed(self):
        tokens = _tokenise("on in at to of")
        # all removed by stop-word list or len <= 2
        assert len(tokens) == 0

    def test_lowercase(self):
        tokens = _tokenise("RBI REPO RATE CUTS")
        assert "rbi" in tokens
        assert "repo" in tokens

    def test_punctuation_stripped(self):
        tokens = _tokenise("rate-cut: 50bp!")
        assert "ratecut" in tokens or "rate" in tokens or "50bp" in tokens

    def test_empty_string(self):
        assert _tokenise("") == []

    def test_numbers_kept(self):
        tokens = _tokenise("Nifty 18000 support level")
        assert "18000" in tokens


# ──────────────────────────────────────────────────────────────────────────────
# _tfidf_vector
# ──────────────────────────────────────────────────────────────────────────────

class TestTFIDFVector:
    def test_known_frequency(self):
        tokens = ["rate", "rate", "cut"]
        vocab = ["cut", "hike", "rate"]
        vec = _tfidf_vector(tokens, vocab)
        assert len(vec) == 3
        assert vec[2] == pytest.approx(2 / 3)   # "rate" appears 2 of 3 tokens
        assert vec[0] == pytest.approx(1 / 3)   # "cut" appears 1 of 3
        assert vec[1] == pytest.approx(0.0)     # "hike" absent

    def test_empty_tokens_zero_vector(self):
        vec = _tfidf_vector([], ["a", "b"])
        assert vec == [0.0, 0.0]

    def test_unknown_vocab_zero(self):
        vec = _tfidf_vector(["rate"], ["hike", "cut"])
        assert vec == [0.0, 0.0]


# ──────────────────────────────────────────────────────────────────────────────
# _cosine / _cosine_vec
# ──────────────────────────────────────────────────────────────────────────────

class TestCosine:
    def test_identical_vectors_is_1(self):
        v = [1.0, 2.0, 3.0]
        assert _cosine(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors_is_0(self):
        assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_zero_vector_is_0(self):
        assert _cosine([0.0, 0.0], [1.0, 2.0]) == 0.0

    def test_opposite_direction(self):
        val = _cosine([1.0, 0.0], [-1.0, 0.0])
        assert val == pytest.approx(-1.0)

    def test_cosine_vec_alias(self):
        a = [1.0, 2.0, 3.0]
        b = [4.0, 5.0, 6.0]
        assert _cosine_vec(a, b) == pytest.approx(_cosine(a, b))

    def test_result_is_rounded(self):
        # Result should be rounded to 4 decimal places
        val = _cosine([1.0, 1.0], [1.0, 0.0])
        assert val == round(val, 4)


# ──────────────────────────────────────────────────────────────────────────────
# _keyword_similarity
# ──────────────────────────────────────────────────────────────────────────────

class TestKeywordSimilarity:
    def test_most_similar_first(self):
        query = "RBI cut repo rate 50 basis points"
        candidates = [
            {"description": "FII selling panic equity crash"},
            {"description": "RBI repo rate cut 50bp monetary stimulus"},
            {"description": "SEBI margin regulation change"},
        ]
        ranked = _keyword_similarity(query, candidates)
        assert len(ranked) == 3
        # The RBI rate cut event should be most similar
        assert "RBI" in ranked[0][0]["description"] or "repo" in ranked[0][0]["description"].lower()

    def test_returns_all_candidates(self):
        query = "market selloff"
        candidates = [{"description": f"event {i}"} for i in range(5)]
        ranked = _keyword_similarity(query, candidates)
        assert len(ranked) == 5

    def test_empty_candidates(self):
        ranked = _keyword_similarity("any query", [])
        assert ranked == []

    def test_scores_are_floats_in_range(self):
        ranked = _keyword_similarity("test query", [
            {"description": "test event one"},
            {"description": "unrelated topic"},
        ])
        for _, score in ranked:
            assert isinstance(score, float)
            assert 0.0 <= score <= 1.0

    def test_sorted_descending(self):
        ranked = _keyword_similarity("gold crisis", [
            {"description": "gold price surge crisis"},
            {"description": "software it company"},
        ])
        scores = [s for _, s in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_empty_descriptions_no_crash(self):
        ranked = _keyword_similarity("test", [{"description": ""}, {"description": "test"}])
        assert len(ranked) == 2


# ──────────────────────────────────────────────────────────────────────────────
# _derive_signal
# ──────────────────────────────────────────────────────────────────────────────

class TestDeriveSignal:
    def test_empty_matches_neutral(self):
        signal, score, reasoning = _derive_signal([], [])
        assert signal == "NEUTRAL"
        assert score == 50
        assert "No historical" in reasoning

    def test_all_bullish_events(self):
        events = [
            {**_BULLISH_EVENT, "market_impact": "STRONG_POSITIVE", "outcome": "Rally of 15%"},
            {**_BULLISH_EVENT, "market_impact": "MILD_POSITIVE",   "outcome": "Mild recovery"},
        ]
        signal, score, _ = _derive_signal(events, [0.8, 0.6])
        assert signal == "BULLISH_ANALOGUE"
        assert score > 50

    def test_all_bearish_events(self):
        events = [
            {**_BEARISH_EVENT, "market_impact": "SEVERE_NEGATIVE", "outcome": "Crash -40%"},
            {**_BEARISH_EVENT, "market_impact": "MODERATE_NEGATIVE", "outcome": "Selloff -20%"},
        ]
        signal, score, _ = _derive_signal(events, [0.9, 0.7])
        assert signal == "BEARISH_ANALOGUE"
        assert score < 50

    def test_mixed_events(self):
        events = [
            {**_BULLISH_EVENT, "market_impact": "STRONG_POSITIVE", "outcome": "Rally"},
            {**_BEARISH_EVENT, "market_impact": "SEVERE_NEGATIVE", "outcome": "Crash"},
        ]
        signal, score, _ = _derive_signal(events, [0.5, 0.5])
        # Equal weights cancel out
        assert signal == "MIXED_ANALOGUE"
        assert score == 50

    def test_score_clamped_0_100(self):
        events = [{"market_impact": "STRONG_POSITIVE", "outcome": "Bull", "event_date": "2020-01-01"}]
        _, score, _ = _derive_signal(events, [1.0])
        assert 0 <= score <= 100

    def test_outcomes_in_reasoning(self):
        events = [
            {**_BULLISH_EVENT, "market_impact": "STRONG_POSITIVE",
             "outcome": "Market rallied strongly post rate cut"}
        ]
        _, _, reasoning = _derive_signal(events, [0.8])
        assert "Market rallied" in reasoning

    def test_outcome_truncated_at_120(self):
        long_outcome = "x" * 200
        events = [{"market_impact": "STRONG_POSITIVE", "outcome": long_outcome, "event_date": "2020-01-01"}]
        _, _, reasoning = _derive_signal(events, [0.8])
        # Outcome is truncated to 120 chars + date prefix
        assert len(reasoning) < 200

    def test_unknown_impact_neutral_sentiment(self):
        events = [{"market_impact": "MIXED", "outcome": "Flat markets", "event_date": "2020-01-01"}]
        signal, score, _ = _derive_signal(events, [0.7])
        assert signal == "MIXED_ANALOGUE"
        assert score == 50  # neutral sentiment, no change to base 50

    def test_sector_negative_is_negative(self):
        events = [{"market_impact": "SECTOR_NEGATIVE", "outcome": "Sector selloff", "event_date": "2021-01-01"}]
        _, score, _ = _derive_signal(events, [0.8])
        assert score < 50


# ──────────────────────────────────────────────────────────────────────────────
# _embed_openai
# ──────────────────────────────────────────────────────────────────────────────

class TestEmbedOpenAI:
    def test_no_api_key_returns_none(self):
        result = _embed_openai("test text")
        assert result is None

    def test_successful_embedding(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        embedding = [0.1] * 1536
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(
            {"data": [{"embedding": embedding}]}
        ).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("agents.historical_rag.urlopen", return_value=mock_resp):
            result = _embed_openai("FII selling Nifty crash")

        assert result == embedding
        assert len(result) == 1536

    def test_network_error_returns_none(self, monkeypatch):
        from urllib.error import URLError
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        with patch("agents.historical_rag.urlopen", side_effect=URLError("timeout")):
            result = _embed_openai("test")
        assert result is None

    def test_invalid_json_returns_none(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not json"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("agents.historical_rag.urlopen", return_value=mock_resp):
            result = _embed_openai("test")
        assert result is None


# ──────────────────────────────────────────────────────────────────────────────
# _fetch_all_events
# ──────────────────────────────────────────────────────────────────────────────

class TestFetchAllEvents:
    def test_no_supabase_config_returns_empty(self):
        events = _fetch_all_events()
        assert events == []

    def test_supabase_returns_events(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "fake_key")

        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.limit.return_value.execute.return_value.data = [
            _BULLISH_EVENT, _BEARISH_EVENT
        ]

        with patch("supabase.create_client", return_value=mock_client):
            events = _fetch_all_events()

        assert len(events) == 2

    def test_supabase_exception_returns_empty(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "fake_key")

        with patch("supabase.create_client", side_effect=Exception("connection failed")):
            events = _fetch_all_events()

        assert events == []


# ──────────────────────────────────────────────────────────────────────────────
# _vector_search
# ──────────────────────────────────────────────────────────────────────────────

class TestVectorSearch:
    def test_no_supabase_config_returns_empty(self):
        results = _vector_search([0.1] * 1536)
        assert results == []

    def test_rpc_returns_matches(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "fake_key")

        mock_client = MagicMock()
        mock_client.rpc.return_value.execute.return_value.data = [
            {**_BULLISH_EVENT, "similarity": 0.85},
            {**_BEARISH_EVENT, "similarity": 0.72},
        ]

        with patch("supabase.create_client", return_value=mock_client):
            results = _vector_search([0.1] * 1536, top_k=3)

        assert len(results) == 2
        assert results[0]["similarity"] == 0.85

    def test_rpc_exception_returns_empty(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "fake_key")

        with patch("supabase.create_client", side_effect=Exception("pgvector not set up")):
            results = _vector_search([0.1] * 1536)

        assert results == []


# ──────────────────────────────────────────────────────────────────────────────
# analyse() — integration
# ──────────────────────────────────────────────────────────────────────────────

class TestAnalyse:
    def test_empty_description_returns_no_data(self):
        result = analyse("")
        assert result["signal"] == "NO_DATA"
        assert result["score"] == 50
        assert result["matched_events"] == []

    def test_whitespace_description_returns_no_data(self):
        result = analyse("   ")
        assert result["signal"] == "NO_DATA"

    def test_no_supabase_no_openai_returns_no_data(self):
        result = analyse("FII selling 5000 Cr, India VIX at 22")
        assert result["signal"] == "NO_DATA"
        assert result["agent_name"] == "historical_rag"

    def test_keyword_fallback_path(self, monkeypatch):
        """When no OpenAI key, should use keyword TF-IDF against Supabase events."""
        monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "fake_key")

        events = [_BULLISH_EVENT, _BEARISH_EVENT, _NEUTRAL_EVENT]
        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.limit.return_value.execute.return_value.data = events

        with patch("supabase.create_client", return_value=mock_client):
            result = analyse("RBI cut repo rate to stimulate economy")

        assert result["signal"] in ("BULLISH_ANALOGUE", "BEARISH_ANALOGUE", "MIXED_ANALOGUE")
        assert len(result["matched_events"]) > 0
        assert result["detail"]["embed_method"] == "keyword_tfidf_cosine"

    def test_openai_vector_path(self, monkeypatch):
        """With OpenAI key + pgvector returning results, should use vector path."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "fake_key")

        embedding = [0.1] * 1536
        vector_results = [
            {**_BULLISH_EVENT, "similarity": 0.88},
            {**_BULLISH_EVENT, "similarity": 0.75},
        ]

        with patch("agents.historical_rag._embed_openai", return_value=embedding):
            mock_client = MagicMock()
            mock_client.rpc.return_value.execute.return_value.data = vector_results
            with patch("supabase.create_client", return_value=mock_client):
                result = analyse("RBI rate cut cycle, FII buying")

        assert result["detail"]["embed_method"] == "openai_text-embedding-3-small"
        assert result["signal"] in ("BULLISH_ANALOGUE", "BEARISH_ANALOGUE", "MIXED_ANALOGUE", "NO_DATA")

    def test_output_schema(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "fake_key")

        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.limit.return_value.execute.return_value.data = [
            _BULLISH_EVENT, _BEARISH_EVENT
        ]

        with patch("supabase.create_client", return_value=mock_client):
            result = analyse("market crash FII selling")

        assert "signal" in result
        assert "score" in result
        assert "detail" in result
        assert "matched_events" in result
        assert "similarity_scores" in result
        assert result["agent_name"] == "historical_rag"

    def test_embedding_not_in_output_events(self, monkeypatch):
        """Raw embedding vectors should be stripped from output."""
        monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "fake_key")

        event_with_embedding = {**_BULLISH_EVENT, "embedding": [0.1] * 1536}
        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.limit.return_value.execute.return_value.data = [
            event_with_embedding
        ]

        with patch("supabase.create_client", return_value=mock_client):
            result = analyse("rate cut economy stimulus")

        for ev in result["matched_events"]:
            assert "embedding" not in ev

    def test_similarity_scores_length_matches_events(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "fake_key")

        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.limit.return_value.execute.return_value.data = [
            _BULLISH_EVENT, _BEARISH_EVENT, _NEUTRAL_EVENT
        ]

        with patch("supabase.create_client", return_value=mock_client):
            result = analyse("FII outflow panic selloff")

        assert len(result["matched_events"]) == len(result["similarity_scores"])

    def test_score_in_range(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "fake_key")

        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.limit.return_value.execute.return_value.data = [
            _BULLISH_EVENT, _BEARISH_EVENT
        ]

        with patch("supabase.create_client", return_value=mock_client):
            result = analyse("macro uncertainty rising")

        assert 0 <= result["score"] <= 100

    def test_supabase_write_failure_does_not_propagate(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "fake_key")

        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.limit.return_value.execute.return_value.data = [
            _BULLISH_EVENT
        ]

        with patch("supabase.create_client", return_value=mock_client):
            with patch("agents.historical_rag._write_agent_performance",
                       side_effect=Exception("DB down")):
                result = analyse("RBI cut rates")

        assert "signal" in result

    def test_max_3_events_returned(self, monkeypatch):
        """Keyword path should return at most 3 events."""
        monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "fake_key")

        many_events = [
            {"id": str(i), "event_type": "GLOBAL",
             "description": f"FII selling equity crash event {i}",
             "event_date": "2020-01-01", "market_impact": "SEVERE_NEGATIVE",
             "outcome": f"Market fell {i}%", "affected_sectors": [], "embedding": None}
            for i in range(10)
        ]
        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.limit.return_value.execute.return_value.data = many_events

        with patch("supabase.create_client", return_value=mock_client):
            result = analyse("FII selling equity crash")

        assert len(result["matched_events"]) <= 3
