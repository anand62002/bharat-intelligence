"""
governance/proposal_tags.py
===========================
Deterministic tag + type for research proposals.

The dashboard colour-codes each proposal by one of four tags
(Architecture / Signal Quality / Governance / Performance) and labels the
source as a whitepaper or article. Nothing used to write either value, so
every card fell back to "Research" / "whitepaper".

Classification is rule-based on purpose — no LLM call — so it costs nothing,
is stable across reruns, and can be applied retroactively to rows saved
before these fields existed. It is used in two places:
  - research_agent._save_proposal() stores the result in metadata (new rows)
  - api.main._transform_research() falls back to it when metadata lacks one
"""
from __future__ import annotations

from typing import Iterable

TAG_ARCHITECTURE   = "Architecture"
TAG_SIGNAL_QUALITY = "Signal Quality"
TAG_GOVERNANCE     = "Governance"
TAG_PERFORMANCE    = "Performance"
VALID_TAGS = (TAG_ARCHITECTURE, TAG_SIGNAL_QUALITY, TAG_GOVERNANCE, TAG_PERFORMANCE)

TYPE_WHITEPAPER = "whitepaper"
TYPE_ARTICLE    = "article"

# Checked in this order — first match wins; Signal Quality is the default
# because most proposals tune how an agent scores a stock.
_GOVERNANCE_AGENTS = {"fact_checker", "hallucination_detector", "research_agent",
                      "performance_tracker", "synthesis_validator", "governance"}
_GOVERNANCE_WORDS  = ("hallucinat", "fact-check", "fact check", "audit", "judge",
                      "kappa", "calibrat", "grounding", "leakage", "oversight")
_PERFORMANCE_FILES = ("data/run_cache", "data/fundamentals_cache", "worker.py")
_PERFORMANCE_WORDS = ("latency", "throughput", "cache", "caching", "speed up",
                      "faster", "runtime", "token cost", "api cost", "memory usage",
                      "parallelis", "paralleliz", "batching")
_ARCH_FILES        = ("scheduler/", "api/", "orchestrator")
_ARCH_WORDS        = ("architecture", "pipeline", "orchestrat", "multi-agent",
                      "langgraph", "debate", "refactor", "workflow", "routing")

# Sources that are scholarly paper feeds by construction.
_PAPER_SOURCES = ("arxiv", "semanticscholar", "ssrn", "huggingface")
_PAPER_URL_HINTS = ("arxiv.org", "doi.org", "ssrn.com", "semanticscholar.org",
                    "openreview.net", "aclanthology.org", "huggingface.co/papers")


def classify_tag(
    file_to_change: str | None = None,
    impacted_agents: Iterable[str] | None = None,
    proposed_change: str | None = None,
    title: str | None = None,
) -> str:
    """Return one of VALID_TAGS for a proposal."""
    path   = (file_to_change or "").replace("\\", "/").lower()
    agents = {str(a).lower().strip() for a in (impacted_agents or []) if a}
    text   = f"{proposed_change or ''} {title or ''}".lower()

    if path.startswith("governance/") or agents & _GOVERNANCE_AGENTS \
            or any(w in text for w in _GOVERNANCE_WORDS):
        return TAG_GOVERNANCE
    if any(path.startswith(p) for p in _PERFORMANCE_FILES) \
            or any(w in text for w in _PERFORMANCE_WORDS):
        return TAG_PERFORMANCE
    if any(p in path for p in _ARCH_FILES) or any(w in text for w in _ARCH_WORDS):
        return TAG_ARCHITECTURE
    return TAG_SIGNAL_QUALITY


def classify_type(source: str | None = None, url: str | None = None) -> str:
    """'whitepaper' for scholarly papers, 'article' for anything else."""
    src  = (source or "").lower()
    link = (url or "").lower()
    if any(src.startswith(s) for s in _PAPER_SOURCES) or src == "arxiv_ss":
        return TYPE_WHITEPAPER
    if any(h in link for h in _PAPER_URL_HINTS):
        return TYPE_WHITEPAPER
    return TYPE_ARTICLE
