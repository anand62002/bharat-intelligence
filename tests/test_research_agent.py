"""
tests/test_research_agent.py -- Unit tests for governance/research_agent.py

All tests mock external I/O (Anthropic API, Supabase, network requests,
feedparser). No real API calls are made -- use the integration marker for
those (pytest -m integration).

Coverage areas
--------------
  TestExtractJson       -- _extract_json() robustness (fences, trailing text)
  TestScoreRelevance    -- Haiku relevance scoring with mocked Claude
  TestGenerateProposal  -- Sonnet proposal generation with mocked Claude
  TestAgentDebate       -- Per-agent debate voting with mocked Claude
  TestFetchArxivRss     -- arXiv RSS fetching with mocked feedparser
  TestFetchArxivApi     -- arXiv API search with mocked requests
  TestFetchSemanticScholar -- Semantic Scholar API with mocked requests
  TestGatherPapers      -- Deduplication across sources
  TestSupabaseHelpers   -- _url_already_processed + _save_proposal
  TestListProposals     -- list_proposals() Supabase query
  TestApproveProposal   -- approve_proposal() GitHub PR creation
  TestRunFunction       -- end-to-end run() with all I/O mocked
  TestSendTelegram      -- Notification helper
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_paper(
    title    = "LLM for Financial Forecasting in Emerging Markets",
    abstract = "We propose a novel multi-agent LLM approach to forecast NSE stock returns...",
    url      = "https://arxiv.org/abs/2506.12345",
    source   = "arxiv_rss_cs.AI",
):
    from governance.research_agent import ResearchPaper
    return ResearchPaper(
        title    = title,
        abstract = abstract,
        url      = url,
        source   = source,
        published = "2026-04-22",
        authors  = ["Alice Smith", "Bob Kumar"],
        venue    = "arXiv cs.AI",
    )


def _mock_claude_response(content: str):
    """Build a minimal mock Anthropic response object."""
    msg = MagicMock()
    msg.content = [MagicMock(text=content)]
    return msg


# ---------------------------------------------------------------------------
# TestExtractJson
# ---------------------------------------------------------------------------

class TestExtractJson:
    """_extract_json robustly extracts the first JSON object from any text."""

    def test_plain_json(self):
        from governance.research_agent import _extract_json
        result = _extract_json('{"relevance": 82, "reason": "Highly applicable"}')
        assert result["relevance"] == 82

    def test_json_with_markdown_fence(self):
        from governance.research_agent import _extract_json
        text = '```json\n{"relevance": 75, "reason": "Applicable"}\n```'
        result = _extract_json(text)
        assert result["relevance"] == 75

    def test_json_with_trailing_prose(self):
        from governance.research_agent import _extract_json
        text = '{"relevance": 90, "reason": "Great fit"} This paper is excellent...'
        result = _extract_json(text)
        assert result["relevance"] == 90

    def test_json_with_leading_prose(self):
        from governance.research_agent import _extract_json
        text = 'Here is my assessment: {"relevance": 60, "reason": "Moderate"}'
        result = _extract_json(text)
        assert result["relevance"] == 60

    def test_nested_json_object(self):
        from governance.research_agent import _extract_json
        text = '{"stance": "FOR", "details": {"score": 85, "note": "good"}}'
        result = _extract_json(text)
        assert result["stance"] == "FOR"
        assert result["details"]["score"] == 85

    def test_no_json_raises(self):
        from governance.research_agent import _extract_json
        with pytest.raises(ValueError, match="No JSON object"):
            _extract_json("This response has no JSON at all.")

    def test_unclosed_json_raises(self):
        from governance.research_agent import _extract_json
        with pytest.raises((ValueError, json.JSONDecodeError)):
            _extract_json('{"stance": "FOR", "argument": "missing closing brace"')

    def test_empty_string_raises(self):
        from governance.research_agent import _extract_json
        with pytest.raises((ValueError, json.JSONDecodeError)):
            _extract_json("")

    def test_valid_debate_json(self):
        from governance.research_agent import _extract_json
        text = (
            '{"stance": "AGAINST", "argument": "Adds complexity", '
            '"confidence": 70, "key_concern": "Backtest required"}'
        )
        result = _extract_json(text)
        assert result["stance"] == "AGAINST"
        assert result["confidence"] == 70


# ---------------------------------------------------------------------------
# TestScoreRelevance
# ---------------------------------------------------------------------------

class TestScoreRelevance:

    def test_high_relevance_paper(self):
        from governance.research_agent import _score_relevance
        client = MagicMock()
        client.messages.create.return_value = _mock_claude_response(
            '{"relevance": 92, "reason": "Directly applicable to FII/DII flow prediction",'
            ' "applicable_agent": "institutional"}'
        )
        paper = _make_paper()
        score = _score_relevance(paper, client)
        assert score == 92
        assert paper.relevance == 92
        assert "FII/DII" in paper.relevance_reason

    def test_low_relevance_paper(self):
        from governance.research_agent import _score_relevance
        client = MagicMock()
        client.messages.create.return_value = _mock_claude_response(
            '{"relevance": 12, "reason": "Hardware optimisation paper", "applicable_agent": null}'
        )
        paper = _make_paper(title="GPU memory optimisation for transformers")
        score = _score_relevance(paper, client)
        assert score == 12
        assert paper.relevance == 12

    def test_relevance_clamped_to_100(self):
        from governance.research_agent import _score_relevance
        client = MagicMock()
        client.messages.create.return_value = _mock_claude_response(
            '{"relevance": 150, "reason": "Extremely relevant", "applicable_agent": "technical"}'
        )
        score = _score_relevance(_make_paper(), client)
        assert score == 100

    def test_relevance_clamped_to_zero(self):
        from governance.research_agent import _score_relevance
        client = MagicMock()
        client.messages.create.return_value = _mock_claude_response(
            '{"relevance": -5, "reason": "Not relevant", "applicable_agent": null}'
        )
        score = _score_relevance(_make_paper(), client)
        assert score == 0

    def test_none_client_returns_zero(self):
        from governance.research_agent import _score_relevance
        paper = _make_paper()
        score = _score_relevance(paper, None)
        assert score == 0

    def test_api_error_returns_zero(self):
        from governance.research_agent import _score_relevance
        client = MagicMock()
        client.messages.create.side_effect = Exception("API error")
        score = _score_relevance(_make_paper(), client)
        assert score == 0

    def test_invalid_json_returns_zero(self):
        from governance.research_agent import _score_relevance
        client = MagicMock()
        client.messages.create.return_value = _mock_claude_response(
            "Sorry, I cannot score this paper."
        )
        score = _score_relevance(_make_paper(), client)
        assert score == 0


# ---------------------------------------------------------------------------
# TestGenerateProposal
# ---------------------------------------------------------------------------

class TestGenerateProposal:

    def _sonnet_response(self):
        return _mock_claude_response(
            '{"proposed_change": "Add LLM-based sentiment weighting to the technical agent '
            'EMA crossover signal using the method from the paper.", '
            '"impacted_agents": ["technical", "sentiment"], '
            '"cost_impact": "low", "implementation_effort_hours": 8, '
            '"requires_paid_data": false, '
            '"expected_improvement": "5-8% reduction in false-positive EMA crossover signals", '
            '"file_to_change": "agents/technical.py"}'
        )

    def test_generates_proposal_dict(self):
        from governance.research_agent import _generate_proposal
        client = MagicMock()
        client.messages.create.return_value = self._sonnet_response()
        proposal = _generate_proposal(_make_paper(), client)
        assert proposal is not None
        assert "proposed_change" in proposal
        assert "impacted_agents" in proposal
        assert "cost_impact" in proposal
        assert proposal["cost_impact"] in ("low", "medium", "high")

    def test_proposal_has_file_to_change(self):
        from governance.research_agent import _generate_proposal
        client = MagicMock()
        client.messages.create.return_value = self._sonnet_response()
        proposal = _generate_proposal(_make_paper(), client)
        assert "file_to_change" in proposal
        assert ".py" in proposal["file_to_change"]

    def test_impacted_agents_is_list(self):
        from governance.research_agent import _generate_proposal
        client = MagicMock()
        client.messages.create.return_value = self._sonnet_response()
        proposal = _generate_proposal(_make_paper(), client)
        assert isinstance(proposal["impacted_agents"], list)
        assert len(proposal["impacted_agents"]) >= 1

    def test_none_client_returns_none(self):
        from governance.research_agent import _generate_proposal
        assert _generate_proposal(_make_paper(), None) is None

    def test_api_error_returns_none(self):
        from governance.research_agent import _generate_proposal
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("timeout")
        assert _generate_proposal(_make_paper(), client) is None

    def test_sonnet_uses_correct_model(self):
        from governance.research_agent import _generate_proposal, SONNET_MODEL
        client = MagicMock()
        client.messages.create.return_value = self._sonnet_response()
        _generate_proposal(_make_paper(), client)
        call_kwargs = client.messages.create.call_args[1]
        assert call_kwargs["model"] == SONNET_MODEL


# ---------------------------------------------------------------------------
# TestAgentDebate
# ---------------------------------------------------------------------------

class TestAgentDebate:

    def _debate_response(self, stance="FOR"):
        return _mock_claude_response(
            f'{{"stance": "{stance}", "argument": "This improves signal accuracy.", '
            f'"confidence": 78, "key_concern": "Requires backtesting on NSE data"}}'
        )

    def test_debate_one_agent_for(self):
        from governance.research_agent import _debate_one_agent
        client = MagicMock()
        client.messages.create.return_value = self._debate_response("FOR")
        result = _debate_one_agent(
            "technical", _make_paper(), "Add LLM weighting", ["technical"], client
        )
        assert result["stance"] == "FOR"
        assert result["agent"] == "technical"
        assert result["confidence"] == 78
        assert "timestamp" in result

    def test_debate_one_agent_against(self):
        from governance.research_agent import _debate_one_agent
        client = MagicMock()
        client.messages.create.return_value = self._debate_response("AGAINST")
        result = _debate_one_agent(
            "fact_checker", _make_paper(), "Change signal logic", ["technical"], client
        )
        assert result["stance"] == "AGAINST"
        assert result["agent"] == "fact_checker"

    def test_invalid_stance_normalises_to_abstain(self):
        from governance.research_agent import _debate_one_agent
        client = MagicMock()
        client.messages.create.return_value = _mock_claude_response(
            '{"stance": "MAYBE", "argument": "Not sure", "confidence": 50, "key_concern": ""}'
        )
        result = _debate_one_agent(
            "macro", _make_paper(), "Change macro model", ["macro"], client
        )
        assert result["stance"] == "ABSTAIN"

    def test_api_error_returns_abstain(self):
        from governance.research_agent import _debate_one_agent
        client = MagicMock()
        client.messages.create.side_effect = Exception("API error")
        result = _debate_one_agent(
            "sentiment", _make_paper(), "Change NLP model", ["sentiment"], client
        )
        assert result["stance"] == "ABSTAIN"
        assert result["agent"] == "sentiment"
        assert result["confidence"] == 0

    def test_none_client_returns_abstain(self):
        from governance.research_agent import _debate_one_agent
        result = _debate_one_agent(
            "institutional", _make_paper(), "Add FII signal", ["institutional"], None
        )
        assert result["stance"] == "ABSTAIN"

    def test_run_debate_returns_8_entries(self):
        from governance.research_agent import _run_debate, DEBATE_AGENTS
        client = MagicMock()
        client.messages.create.return_value = self._debate_response("FOR")
        debate_log = _run_debate(
            _make_paper(), "Improve RSI thresholds", ["technical"], client
        )
        assert len(debate_log) == len(DEBATE_AGENTS)

    def test_run_debate_all_agents_represented(self):
        from governance.research_agent import _run_debate, DEBATE_AGENTS
        client = MagicMock()
        client.messages.create.return_value = self._debate_response("FOR")
        debate_log = _run_debate(
            _make_paper(), "Change weighting", ["technical"], client
        )
        agent_names = {entry["agent"] for entry in debate_log}
        assert agent_names == set(DEBATE_AGENTS)

    def test_run_debate_vote_counting(self):
        from governance.research_agent import _run_debate, DEBATE_AGENTS
        client = MagicMock()
        responses = []
        for i, _ in enumerate(DEBATE_AGENTS):
            stance = "FOR" if i < 5 else "AGAINST"
            responses.append(self._debate_response(stance))
        client.messages.create.side_effect = responses
        debate_log = _run_debate(_make_paper(), "Some change", [], client)
        for_count     = sum(1 for d in debate_log if d["stance"] == "FOR")
        against_count = sum(1 for d in debate_log if d["stance"] == "AGAINST")
        assert for_count == 5
        assert against_count == 3

    def test_debate_uses_haiku_model(self):
        from governance.research_agent import _debate_one_agent, HAIKU_MODEL
        client = MagicMock()
        client.messages.create.return_value = self._debate_response("FOR")
        _debate_one_agent("technical", _make_paper(), "change", [], client)
        call_kwargs = client.messages.create.call_args[1]
        assert call_kwargs["model"] == HAIKU_MODEL


# ---------------------------------------------------------------------------
# TestFetchArxivRss
# ---------------------------------------------------------------------------

class TestFetchArxivRss:
    """
    feedparser entries support BOTH attribute and dict-style access.
    _FeedEntry emulates this so production code calling entry.get("title")
    and entry.title both work correctly in tests.
    """

    class _FeedEntry(dict):
        """dict subclass that also exposes keys as attributes."""
        def __getattr__(self, name):
            try:
                return self[name]
            except KeyError:
                return None

    def _make_feed(self, entries):
        feed = SimpleNamespace(entries=entries)
        return feed

    def _make_entry(self, title="Test Paper", summary="Abstract here.",
                    url="https://arxiv.org/abs/2506.001", published=None):
        return self._FeedEntry(
            title     = title,
            summary   = summary,
            id        = url,
            link      = url,
            published = published or "Wed, 22 Apr 2026 00:00:00 +0000",
            updated   = None,
            authors   = [],
        )

    def test_returns_research_papers(self):
        from governance.research_agent import _fetch_arxiv_rss
        entry = self._make_entry(
            title="LLM trading agent for NSE stocks",
            summary="We study LLM agents for multi-agent trading...",
        )
        with patch("feedparser.parse", return_value=self._make_feed([entry])):
            papers = _fetch_arxiv_rss(categories=["q-fin.TR"])
        assert len(papers) >= 1
        assert papers[0].title == "LLM trading agent for NSE stocks"

    def test_filters_irrelevant_cs_papers(self):
        from governance.research_agent import _fetch_arxiv_rss
        entry = self._make_entry(
            title="New CMOS transistor fabrication technique",
            summary="We present a novel silicon etching method for VLSI design.",
        )
        with patch("feedparser.parse", return_value=self._make_feed([entry])):
            papers = _fetch_arxiv_rss(categories=["cs.AI"])
        assert len(papers) == 0

    def test_qfin_passes_without_keyword_filter(self):
        from governance.research_agent import _fetch_arxiv_rss
        entry = self._make_entry(
            title="Order book dynamics in Indian equity markets",
            summary="We model limit order book dynamics using stochastic processes.",
        )
        with patch("feedparser.parse", return_value=self._make_feed([entry])):
            papers = _fetch_arxiv_rss(categories=["q-fin.TR"])
        assert len(papers) == 1

    def test_empty_abstract_is_skipped(self):
        from governance.research_agent import _fetch_arxiv_rss
        entry = self._make_entry(summary="")
        with patch("feedparser.parse", return_value=self._make_feed([entry])):
            papers = _fetch_arxiv_rss(categories=["q-fin.CP"])
        assert len(papers) == 0

    def test_network_error_returns_empty(self):
        from governance.research_agent import _fetch_arxiv_rss
        with patch("feedparser.parse", side_effect=Exception("connection error")):
            papers = _fetch_arxiv_rss(categories=["cs.AI"])
        assert papers == []

    def test_source_field_set_correctly(self):
        from governance.research_agent import _fetch_arxiv_rss
        entry = self._make_entry(
            title="Hallucination detection in financial LLMs",
            summary="We detect hallucinations in financial LLM outputs...",
        )
        with patch("feedparser.parse", return_value=self._make_feed([entry])):
            papers = _fetch_arxiv_rss(categories=["cs.AI"])
        assert len(papers) == 1
        assert papers[0].source == "arxiv_rss_cs.AI"


# ---------------------------------------------------------------------------
# TestFetchArxivApi
# ---------------------------------------------------------------------------

class TestFetchArxivApi:

    def _arxiv_atom_response(self):
        atom = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>https://arxiv.org/abs/2506.99999</id>
    <title>Multi-agent LLM System for NSE Stock Prediction</title>
    <summary>Abstract about multi-agent LLM stock prediction...</summary>
    <published>2026-04-22T00:00:00Z</published>
    <author><name>Alice Smith</name></author>
  </entry>
</feed>"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = atom
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    def test_returns_papers(self):
        from governance.research_agent import _fetch_arxiv_api
        with patch("requests.get", return_value=self._arxiv_atom_response()):
            papers = _fetch_arxiv_api("LLM financial forecasting", max_results=5)
        assert len(papers) >= 1
        assert papers[0].source == "arxiv_api"

    def test_network_error_returns_empty(self):
        from governance.research_agent import _fetch_arxiv_api
        with patch("requests.get", side_effect=Exception("timeout")):
            papers = _fetch_arxiv_api("LLM trading")
        assert papers == []

    def test_url_is_arxiv_abs(self):
        from governance.research_agent import _fetch_arxiv_api
        with patch("requests.get", return_value=self._arxiv_atom_response()):
            papers = _fetch_arxiv_api("LLM financial")
        assert "arxiv.org/abs/" in papers[0].url


# ---------------------------------------------------------------------------
# TestFetchSemanticScholar
# ---------------------------------------------------------------------------

class TestFetchSemanticScholar:

    def _ss_response(self, include_ssrn=False):
        ext_ids = {"ArXiv": "2506.12345"}
        if include_ssrn:
            ext_ids = {"SSRN": "4567890"}
        payload = {
            "data": [{
                "paperId":       "abc123",
                "title":         "Hallucination Detection in Financial LLMs",
                "abstract":      "We study how to detect hallucinations in financial contexts.",
                "year":          2026,
                "url":           "https://www.semanticscholar.org/paper/abc123",
                "externalIds":   ext_ids,
                "venue":         "arXiv",
                "authors":       [{"name": "Bob Kumar"}, {"name": "Alice Singh"}],
                "publicationDate": "2026-04-20",
            }]
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = payload
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    def test_returns_research_papers(self):
        from governance.research_agent import _fetch_semantic_scholar
        with patch("requests.get", return_value=self._ss_response()):
            papers = _fetch_semantic_scholar("hallucination detection finance")
        assert len(papers) >= 1
        assert "arxiv.org/abs/" in papers[0].url

    def test_ssrn_url_constructed_correctly(self):
        from governance.research_agent import _fetch_semantic_scholar
        with patch("requests.get", return_value=self._ss_response(include_ssrn=True)):
            papers = _fetch_semantic_scholar("SSRN finance paper")
        assert len(papers) >= 1
        assert "ssrn.com" in papers[0].url
        assert papers[0].source == "ssrn"

    def test_missing_abstract_skipped(self):
        from governance.research_agent import _fetch_semantic_scholar
        payload = {"data": [{"paperId": "x", "title": "No abstract", "abstract": None,
                              "url": "https://x.com", "externalIds": {}}]}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = payload
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.get", return_value=mock_resp):
            papers = _fetch_semantic_scholar("test")
        assert len(papers) == 0

    def test_rate_limit_retries(self):
        from governance.research_agent import _fetch_semantic_scholar
        rate_mock  = MagicMock(status_code=429, raise_for_status=MagicMock())
        retry_mock = self._ss_response()
        with patch("requests.get", side_effect=[rate_mock, retry_mock]):
            with patch("time.sleep"):   # don't actually sleep
                papers = _fetch_semantic_scholar("test query")
        assert len(papers) >= 1

    def test_network_error_returns_empty(self):
        from governance.research_agent import _fetch_semantic_scholar
        with patch("requests.get", side_effect=Exception("connection refused")):
            papers = _fetch_semantic_scholar("test")
        assert papers == []


# ---------------------------------------------------------------------------
# TestGatherPapers
# ---------------------------------------------------------------------------

class TestGatherPapers:

    def test_deduplication_by_url(self):
        from governance.research_agent import _gather_papers, ResearchPaper
        paper1 = ResearchPaper(
            title="Paper A", abstract="Abstract A",
            url="https://arxiv.org/abs/001", source="arxiv_rss_cs.AI"
        )
        paper2 = ResearchPaper(
            title="Paper A (duplicate)", abstract="Abstract A",
            url="https://arxiv.org/abs/001", source="arxiv_api"
        )
        paper3 = ResearchPaper(
            title="Paper B", abstract="Abstract B",
            url="https://arxiv.org/abs/002", source="arxiv_api"
        )
        with patch("governance.research_agent._fetch_arxiv_rss",
                   return_value=[paper1]):
            with patch("governance.research_agent._fetch_arxiv_api",
                       return_value=[paper2, paper3]):
                with patch("governance.research_agent._fetch_semantic_scholar",
                           return_value=[]):
                    with patch("time.sleep"):
                        papers = _gather_papers()

        urls = [p.url for p in papers]
        assert len(set(urls)) == len(urls), "Duplicate URLs found"
        assert len(papers) == 2   # paper1 + paper3; paper2 is a dup

    def test_empty_sources_returns_empty_list(self):
        from governance.research_agent import _gather_papers
        with patch("governance.research_agent._fetch_arxiv_rss", return_value=[]):
            with patch("governance.research_agent._fetch_arxiv_api", return_value=[]):
                with patch("governance.research_agent._fetch_semantic_scholar",
                           return_value=[]):
                    with patch("time.sleep"):
                        papers = _gather_papers()
        assert papers == []


# ---------------------------------------------------------------------------
# TestSupabaseHelpers
# ---------------------------------------------------------------------------

class TestSupabaseHelpers:

    def test_url_already_processed_true(self):
        from governance.research_agent import _url_already_processed
        client = MagicMock()
        client.table.return_value.select.return_value.eq.return_value \
            .limit.return_value.execute.return_value \
            = MagicMock(data=[{"id": "abc123"}])
        assert _url_already_processed(client, "https://arxiv.org/abs/001") is True

    def test_url_already_processed_false(self):
        from governance.research_agent import _url_already_processed
        client = MagicMock()
        client.table.return_value.select.return_value.eq.return_value \
            .limit.return_value.execute.return_value \
            = MagicMock(data=[])
        assert _url_already_processed(client, "https://arxiv.org/abs/002") is False

    def test_url_already_processed_none_client(self):
        from governance.research_agent import _url_already_processed
        assert _url_already_processed(None, "https://arxiv.org/abs/003") is False

    def test_url_already_processed_exception_returns_false(self):
        from governance.research_agent import _url_already_processed
        client = MagicMock()
        client.table.side_effect = Exception("DB error")
        assert _url_already_processed(client, "https://arxiv.org/abs/004") is False

    def test_save_proposal_calls_insert(self):
        from governance.research_agent import _save_proposal
        paper = _make_paper()
        paper.relevance = 85
        paper.relevance_reason = "Directly applicable"

        client = MagicMock()
        client.table.return_value.insert.return_value.execute.return_value \
            = MagicMock(data=[{"id": "new-uuid-123"}])

        proposal  = {
            "proposed_change": "Add attention mechanism to RSI calculation",
            "impacted_agents": ["technical"],
            "cost_impact": "low",
        }
        debate_log = [
            {"agent": "technical", "stance": "FOR", "argument": "Improves signal",
             "confidence": 80, "key_concern": "", "timestamp": "2026-04-22T00:00:00Z"},
        ]

        saved_id = _save_proposal(client, paper, proposal, debate_log, dry_run=False)
        assert saved_id == "new-uuid-123"
        client.table.assert_called_with("research_proposals")
        insert_call = client.table.return_value.insert
        assert insert_call.called

    def test_save_proposal_dry_run_returns_dry_run_id(self):
        from governance.research_agent import _save_proposal
        paper = _make_paper()
        paper.relevance = 80
        client = MagicMock()

        saved_id = _save_proposal(
            client, paper,
            {"proposed_change": "test", "impacted_agents": [], "cost_impact": "low"},
            [],
            dry_run=True,
        )
        assert saved_id == "dry-run-id"
        # No real DB call
        client.table.return_value.insert.assert_not_called()

    def test_save_proposal_db_error_returns_none(self):
        from governance.research_agent import _save_proposal
        paper = _make_paper()
        paper.relevance = 80
        client = MagicMock()
        client.table.return_value.insert.return_value.execute.side_effect = \
            Exception("DB constraint error")

        saved_id = _save_proposal(
            client, paper,
            {"proposed_change": "test", "impacted_agents": [], "cost_impact": "low"},
            [],
            dry_run=False,
        )
        assert saved_id is None

    def test_save_proposal_none_client_returns_none(self):
        from governance.research_agent import _save_proposal
        paper = _make_paper()
        paper.relevance = 80
        saved_id = _save_proposal(
            None, paper,
            {"proposed_change": "test", "impacted_agents": [], "cost_impact": "low"},
            [],
            dry_run=False,
        )
        assert saved_id is None


# ---------------------------------------------------------------------------
# TestListProposals
# ---------------------------------------------------------------------------

class TestListProposals:

    def test_returns_list_with_debate_summary(self):
        from governance.research_agent import list_proposals
        rows = [
            {
                "id": "abc", "title": "Paper A", "source": "arxiv",
                "url": "https://x.com", "relevance": 85,
                "cost_impact": "low", "impacted_agents": ["technical"],
                "status": "pending", "pr_url": None, "created_at": "2026-04-22",
                "debate_log": [
                    {"stance": "FOR"}, {"stance": "FOR"},
                    {"stance": "AGAINST"}, {"stance": "ABSTAIN"},
                ],
                "metadata": {},
            }
        ]
        mock_client = MagicMock()
        chain = mock_client.table.return_value.select.return_value \
            .order.return_value.limit.return_value
        chain.execute.return_value = MagicMock(data=rows)

        with patch("governance.research_agent._supabase", return_value=mock_client):
            result = list_proposals()

        assert len(result) == 1
        assert result[0]["debate_for"]     == 2
        assert result[0]["debate_against"] == 1
        assert result[0]["debate_abstain"] == 1

    def test_status_filter_applied(self):
        from governance.research_agent import list_proposals
        mock_client = MagicMock()
        chain = mock_client.table.return_value.select.return_value \
            .order.return_value.limit.return_value.eq.return_value
        chain.execute.return_value = MagicMock(data=[])

        with patch("governance.research_agent._supabase", return_value=mock_client):
            list_proposals(status="approved")

        # Verify .eq("status", "approved") was called
        mock_client.table.return_value.select.return_value \
            .order.return_value.limit.return_value.eq \
            .assert_called_once_with("status", "approved")

    def test_no_supabase_returns_empty(self):
        from governance.research_agent import list_proposals
        with patch("governance.research_agent._supabase", return_value=None):
            result = list_proposals()
        assert result == []

    def test_db_exception_returns_empty(self):
        from governance.research_agent import list_proposals
        mock_client = MagicMock()
        mock_client.table.side_effect = Exception("DB offline")
        with patch("governance.research_agent._supabase", return_value=mock_client):
            result = list_proposals()
        assert result == []


# ---------------------------------------------------------------------------
# TestApproveProposal
# ---------------------------------------------------------------------------

class TestApproveProposal:

    def _make_supabase_with_proposal(self, proposal_id="test-uuid"):
        client = MagicMock()
        proposal_data = {
            "id":              proposal_id,
            "title":           "LLM for Indian Stock Forecasting",
            "source":          "arxiv_api",
            "url":             "https://arxiv.org/abs/2506.12345",
            "relevance":       88,
            "proposed_change": "Add attention weighting to RSI signal",
            "impacted_agents": ["technical"],
            "cost_impact":     "low",
            "debate_log": [
                {"agent": "technical", "stance": "FOR", "confidence": 85,
                 "argument": "Improves signal quality", "key_concern": ""},
                {"agent": "fact_checker", "stance": "AGAINST", "confidence": 70,
                 "argument": "Needs backtest validation", "key_concern": ""},
            ],
            "metadata": {
                "file_to_change": "agents/technical.py",
                "expected_improvement": "5% reduction in false positives",
                "implementation_effort_hours": 6,
                "requires_paid_data": False,
                "published": "2026-04-20",
                "authors": ["Alice Smith"],
            },
        }
        client.table.return_value.select.return_value.eq.return_value \
            .single.return_value.execute.return_value \
            = MagicMock(data=proposal_data)
        # mock the status update
        client.table.return_value.update.return_value.eq.return_value \
            .execute.return_value = MagicMock(data=[{"id": proposal_id}])
        return client, proposal_data

    def test_dry_run_returns_without_github(self):
        from governance.research_agent import approve_proposal
        client, _ = self._make_supabase_with_proposal()
        with patch("governance.research_agent._supabase", return_value=client):
            result = approve_proposal("test-uuid", dry_run=True)
        assert result.get("dry_run") is True
        assert "branch" in result

    def test_no_supabase_returns_error(self):
        from governance.research_agent import approve_proposal
        with patch("governance.research_agent._supabase", return_value=None):
            result = approve_proposal("test-uuid")
        assert "error" in result

    def test_proposal_not_found_returns_error(self):
        from governance.research_agent import approve_proposal
        client = MagicMock()
        client.table.return_value.select.return_value.eq.return_value \
            .single.return_value.execute.return_value = MagicMock(data=None)
        with patch("governance.research_agent._supabase", return_value=client):
            result = approve_proposal("nonexistent-uuid")
        assert "error" in result

    def test_github_manager_error_returns_error(self):
        """GitHubManager is imported at module level -- patch at research_agent.GitHubManager."""
        from governance.research_agent import approve_proposal
        client, _ = self._make_supabase_with_proposal()
        mock_mgr_instance = MagicMock()
        mock_mgr_instance.create_enhancement_branch.side_effect = Exception("GitHub error")
        mock_mgr_class = MagicMock(return_value=mock_mgr_instance)
        with patch("governance.research_agent._supabase", return_value=client):
            with patch("governance.research_agent.GitHubManager", mock_mgr_class):
                result = approve_proposal("test-uuid", dry_run=False)
        assert "error" in result

    def test_approve_creates_pr_and_updates_status(self):
        from governance.research_agent import approve_proposal
        client, _ = self._make_supabase_with_proposal("test-uuid-123")
        mock_mgr_instance = MagicMock()
        mock_mgr_instance.create_pull_request.return_value = 42
        mock_mgr_class = MagicMock(return_value=mock_mgr_instance)

        with patch("governance.research_agent._supabase", return_value=client):
            with patch("governance.research_agent.GitHubManager", mock_mgr_class):
                with patch.dict("os.environ",
                                {"GITHUB_REPO": "anand62002/bharat-intelligence"}):
                    result = approve_proposal("test-uuid-123", dry_run=False)

        assert result.get("pr_number") == 42
        assert "pr_url" in result
        # Verify status was updated to 'approved'
        client.table.return_value.update.assert_called()


# ---------------------------------------------------------------------------
# TestRunFunction
# ---------------------------------------------------------------------------

class TestRunFunction:

    def _setup_mocks(self, papers=None, relevance_score=85):
        """Return a dict of patch targets with sensible defaults."""
        if papers is None:
            papers = [_make_paper()]

        patches = {
            "_gather_papers": papers,
            "_url_already_processed": False,
            "_score_relevance_result": relevance_score,
        }
        return patches

    def test_dry_run_returns_summary_dict(self):
        from governance.research_agent import run, ResearchPaper
        paper = _make_paper()
        paper.relevance = 88

        mock_client = MagicMock()
        mock_claude = MagicMock()

        # Relevance score response
        mock_claude.messages.create.return_value = _mock_claude_response(
            '{"relevance": 88, "reason": "Highly applicable", "applicable_agent": "technical"}'
        )

        with patch("governance.research_agent._gather_papers", return_value=[paper]):
            with patch("governance.research_agent._supabase", return_value=mock_client):
                with patch("governance.research_agent._claude", return_value=mock_claude):
                    with patch("governance.research_agent._url_already_processed",
                               return_value=False):
                        with patch("governance.research_agent._generate_proposal",
                                   return_value={
                                       "proposed_change": "Add attention weighting",
                                       "impacted_agents": ["technical"],
                                       "cost_impact": "low",
                                   }):
                            with patch("governance.research_agent._run_debate",
                                       return_value=[]):
                                with patch("governance.research_agent._save_proposal",
                                           return_value="dry-run-id"):
                                    result = run(dry_run=True)

        assert "run_date" in result
        assert "papers_gathered" in result
        assert "papers_new" in result
        assert "papers_relevant" in result
        assert "proposals_saved" in result
        assert result["dry_run"] is True
        assert isinstance(result["errors"], list)
        assert "duration_seconds" in result

    def test_run_skips_already_processed(self):
        from governance.research_agent import run
        paper = _make_paper()

        with patch("governance.research_agent._gather_papers", return_value=[paper]):
            with patch("governance.research_agent._supabase", return_value=MagicMock()):
                with patch("governance.research_agent._claude", return_value=MagicMock()):
                    with patch("governance.research_agent._url_already_processed",
                               return_value=True):
                        with patch("governance.research_agent._score_relevance") as mock_score:
                            result = run(dry_run=True)

        # Score should never be called for already-processed papers
        mock_score.assert_not_called()
        assert result["papers_new"] == 0

    def test_run_low_relevance_skips_proposal(self):
        from governance.research_agent import run
        paper = _make_paper()

        with patch("governance.research_agent._gather_papers", return_value=[paper]):
            with patch("governance.research_agent._supabase", return_value=MagicMock()):
                with patch("governance.research_agent._claude", return_value=MagicMock()):
                    with patch("governance.research_agent._url_already_processed",
                               return_value=False):
                        with patch("governance.research_agent._score_relevance",
                                   return_value=30) as mock_score:
                            with patch("governance.research_agent._generate_proposal") as mock_gen:
                                result = run(dry_run=True)

        mock_score.assert_called_once()
        mock_gen.assert_not_called()    # below threshold -- no proposal
        assert result["papers_relevant"] == 0

    def test_run_paper_error_is_collected(self):
        from governance.research_agent import run
        paper = _make_paper()

        with patch("governance.research_agent._gather_papers", return_value=[paper]):
            with patch("governance.research_agent._supabase", return_value=MagicMock()):
                with patch("governance.research_agent._claude", return_value=MagicMock()):
                    with patch("governance.research_agent._url_already_processed",
                               return_value=False):
                        with patch("governance.research_agent._score_relevance",
                                   side_effect=Exception("unexpected crash")):
                            result = run(dry_run=True)

        assert len(result["errors"]) >= 1

    def test_run_sends_telegram_when_proposals_saved(self):
        from governance.research_agent import run
        paper = _make_paper()

        with patch("governance.research_agent._gather_papers", return_value=[paper]):
            with patch("governance.research_agent._supabase", return_value=MagicMock()):
                with patch("governance.research_agent._claude", return_value=MagicMock()):
                    with patch("governance.research_agent._url_already_processed",
                               return_value=False):
                        with patch("governance.research_agent._score_relevance",
                                   return_value=90):
                            with patch("governance.research_agent._generate_proposal",
                                       return_value={"proposed_change": "x",
                                                     "impacted_agents": [],
                                                     "cost_impact": "low"}):
                                with patch("governance.research_agent._run_debate",
                                           return_value=[]):
                                    with patch("governance.research_agent._save_proposal",
                                               return_value="saved-id"):
                                        with patch("governance.research_agent._send_telegram") as mock_tg:
                                            run(dry_run=True)

        mock_tg.assert_called_once()


# ---------------------------------------------------------------------------
# TestSendTelegram
# ---------------------------------------------------------------------------

class TestSendTelegram:

    def test_dry_run_prints_message(self, capsys):
        from governance.research_agent import _send_telegram
        _send_telegram("Test message", dry_run=True)
        out = capsys.readouterr().out
        assert "Test message" in out

    def test_no_token_returns_false(self):
        from governance.research_agent import _send_telegram
        with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}):
            result = _send_telegram("Test message", dry_run=False)
        assert result is False

    def test_successful_send_returns_true(self):
        from governance.research_agent import _send_telegram
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch.dict("os.environ",
                        {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "123"}):
            with patch("requests.post", return_value=mock_resp):
                result = _send_telegram("Test")
        assert result is True

    def test_network_error_returns_false(self):
        from governance.research_agent import _send_telegram
        with patch.dict("os.environ",
                        {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "123"}):
            with patch("requests.post", side_effect=Exception("timeout")):
                result = _send_telegram("Test")
        assert result is False


# ---------------------------------------------------------------------------
# TestResearchPaperDataclass
# ---------------------------------------------------------------------------

class TestResearchPaperDataclass:

    def test_default_fields(self):
        from governance.research_agent import ResearchPaper
        p = ResearchPaper(
            title="T", abstract="A", url="http://x.com", source="arxiv"
        )
        assert p.relevance is None
        assert p.relevance_reason is None
        assert p.authors == []
        assert p.venue is None
        assert p.published is None

    def test_all_fields_set(self):
        from governance.research_agent import ResearchPaper
        p = ResearchPaper(
            title="T", abstract="A", url="http://x.com", source="ssrn",
            published="2026-04-22", authors=["Alice"], venue="SSRN",
            relevance=90, relevance_reason="Great fit",
        )
        assert p.relevance == 90
        assert p.authors == ["Alice"]
