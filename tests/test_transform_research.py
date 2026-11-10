"""
_transform_research() must pass through the paper/proposal detail that
research_agent._save_proposal() writes into research_proposals.metadata
(OPS-2 audit 2026-11-10: those fields were saved but never sent to React).
"""
import pytest

from api.main import _transform_research
from governance.proposal_tags import VALID_TAGS, classify_tag, classify_type


def _row(**overrides):
    row = {
        "id": "p1",
        "title": "Temporal decay for news sentiment",
        "source": "arxiv_api",
        "url": "https://arxiv.org/abs/0000.00000",
        "relevance": 82,
        "summary": "abstract",
        "proposed_change": "change",
        "impacted_agents": ["sentiment"],
        "cost_impact": "low",
        "debate_log": [{"stance": "FOR"}, {"stance": "AGAINST"}, {"stance": "ABSTAIN"}],
        "status": "pending",
        "created_at": "2026-11-09T02:00:00+00:00",
        "metadata": {
            "authors": ["Alice", "Bob"],
            "venue": "NeurIPS",
            "published": "2026-11-01",
            "expected_improvement": "+3% hit rate",
            "file_to_change": "agents/sentiment.py",
            "implementation_effort_hours": 6,
            "requires_paid_data": False,
            "relevance_reason": "Directly targets headline decay",
        },
    }
    row.update(overrides)
    return row


def test_metadata_fields_passed_through_in_camel_case():
    out = _transform_research(_row())
    assert out["authors"] == ["Alice", "Bob"]
    assert out["venue"] == "NeurIPS"
    assert out["publishedAt"] == "2026-11-01"
    assert out["expectedImprovement"] == "+3% hit rate"
    assert out["fileToChange"] == "agents/sentiment.py"
    assert out["effortHours"] == 6
    assert out["requiresPaidData"] is False
    assert out["relevanceReason"] == "Directly targets headline decay"


def test_requires_paid_data_true():
    row = _row()
    row["metadata"]["requires_paid_data"] = True
    assert _transform_research(row)["requiresPaidData"] is True


def test_missing_or_null_metadata_gives_safe_defaults():
    for meta in (None, {}, {"authors": None, "venue": None, "published": None}):
        out = _transform_research(_row(metadata=meta))
        assert out["authors"] == []
        assert out["venue"] == ""
        assert out["publishedAt"] == ""
        assert out["relevanceReason"] == ""
        assert out["expectedImprovement"] == ""
        assert out["fileToChange"] == ""
        assert out["effortHours"] is None
        assert out["requiresPaidData"] is False


def test_existing_fields_unchanged():
    out = _transform_research(_row())
    assert out["votes"] == {"for": 1, "against": 1, "abstain": 1}
    assert out["debateStatus"] == "debating"
    assert out["date"] == "2026-11-09"


# ── tag / type (governance/proposal_tags.py) ──────────────────────────────────

def test_stored_tag_and_type_win():
    row = _row()
    row["metadata"].update(tag="Governance", type="article")
    out = _transform_research(row)
    assert out["tag"] == "Governance"
    assert out["type"] == "article"


def test_legacy_row_without_tag_is_classified():
    # agents/sentiment.py + arxiv source -> Signal Quality whitepaper
    out = _transform_research(_row())
    assert out["tag"] == "Signal Quality"
    assert out["type"] == "whitepaper"


def test_invalid_stored_tag_is_reclassified():
    row = _row()
    row["metadata"]["tag"] = "Research"   # the old placeholder
    assert _transform_research(row)["tag"] in VALID_TAGS


def test_tag_never_falls_back_to_placeholder():
    for meta in (None, {}):
        out = _transform_research(_row(metadata=meta, impacted_agents=[]))
        assert out["tag"] in VALID_TAGS
        assert out["type"] in ("whitepaper", "article")


@pytest.mark.parametrize("kwargs,expected", [
    ({"file_to_change": "governance/fact_checker.py"},                  "Governance"),
    ({"impacted_agents": ["hallucination_detector"]},                    "Governance"),
    ({"proposed_change": "Add a third judge to improve kappa"},          "Governance"),
    ({"file_to_change": "data/run_cache.py"},                            "Performance"),
    ({"proposed_change": "Batch requests to cut latency by 40%"},        "Performance"),
    ({"file_to_change": "scheduler/orchestrator.py"},                    "Architecture"),
    ({"proposed_change": "Introduce a multi-agent debate stage"},        "Architecture"),
    ({"file_to_change": "agents/technical.py",
      "proposed_change": "Add a volume-weighted RSI"},                   "Signal Quality"),
    ({},                                                                 "Signal Quality"),
    ({"file_to_change": "governance\\research_agent.py"},                "Governance"),  # Windows path
])
def test_classify_tag(kwargs, expected):
    assert classify_tag(**kwargs) == expected


@pytest.mark.parametrize("source,url,expected", [
    ("arxiv_rss_q-fin.TR", "", "whitepaper"),
    ("arxiv_api", "", "whitepaper"),
    ("arxiv_ss", "", "whitepaper"),
    ("semanticscholar", "", "whitepaper"),
    ("ssrn", "", "whitepaper"),
    ("huggingface_daily", "", "whitepaper"),
    ("ai_scout", "https://arxiv.org/abs/2601.00001", "whitepaper"),
    ("ai_scout", "https://some-blog.example.com/post", "article"),
    (None, None, "article"),
])
def test_classify_type(source, url, expected):
    assert classify_type(source, url) == expected


def test_save_proposal_writes_tag_and_type():
    from unittest.mock import MagicMock
    from governance.research_agent import ResearchPaper, _save_proposal

    client = MagicMock()
    client.table.return_value.insert.return_value.execute.return_value.data = [{"id": "x"}]
    paper = ResearchPaper(title="Faster caching for agents", abstract="a",
                          url="https://arxiv.org/abs/1", source="arxiv_api", relevance=80)
    _save_proposal(client, paper,
                   {"file_to_change": "data/run_cache.py",
                    "proposed_change": "cache", "impacted_agents": []},
                   [], dry_run=False)
    row = client.table.return_value.insert.call_args[0][0]
    assert row["metadata"]["tag"] == "Performance"
    assert row["metadata"]["type"] == "whitepaper"
