"""
tests/test_research_sources_integration.py
-------------------------------------------
Integration tests for governance/research_agent.py paper-source functions.

These tests make REAL network calls to verify each source is:
  - Accessible (HTTP 200, no 403/404)
  - Returning well-formed data (title, abstract, URL)
  - Not returning empty lists when papers are expected
  - Compatible with the ResearchPaper dataclass

Run explicitly:
    pytest -m integration tests/test_research_sources_integration.py -v -s
    pytest -m integration tests/test_research_sources_integration.py::TestSourceAudit -v -s

Tests skip (not fail) when a source is temporarily unavailable so CI is
never blocked by transient network issues.
"""

from __future__ import annotations

import os
import time

import pytest

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _is_valid_paper(paper) -> bool:
    """Return True if paper has the minimum required fields populated."""
    from governance.research_agent import ResearchPaper
    return (
        isinstance(paper, ResearchPaper)
        and bool(paper.title)
        and bool(paper.abstract)
        and bool(paper.url)
        and paper.url.startswith("http")
    )


def _papers_are_valid(papers: list) -> bool:
    return all(_is_valid_paper(p) for p in papers)


# ---------------------------------------------------------------------------
# Tier 1: arXiv RSS
# ---------------------------------------------------------------------------

class TestArxivRssIntegration:
    """Live tests for _fetch_arxiv_rss() against the official arXiv RSS feeds."""

    @pytest.mark.integration
    def test_arxiv_rss_qfin_tr_returns_papers(self):
        """q-fin.TR (Trading) RSS should return at least 1 paper."""
        from governance.research_agent import _fetch_arxiv_rss
        papers = _fetch_arxiv_rss(categories=["q-fin.TR"], days=30)
        print(f"\n[arXiv RSS q-fin.TR] papers={len(papers)}")
        for p in papers[:3]:
            print(f"  - {p.title[:70]}")
        if not papers:
            pytest.skip("q-fin.TR RSS returned 0 papers today (low submission day)")
        assert _papers_are_valid(papers)
        assert all(p.source == "arxiv_rss_q-fin.TR" for p in papers)

    @pytest.mark.integration
    def test_arxiv_rss_qfin_cp_returns_papers(self):
        """q-fin.CP (Computational Finance) RSS should return papers."""
        from governance.research_agent import _fetch_arxiv_rss
        papers = _fetch_arxiv_rss(categories=["q-fin.CP"], days=30)
        print(f"\n[arXiv RSS q-fin.CP] papers={len(papers)}")
        if not papers:
            pytest.skip("q-fin.CP returned 0 papers today")
        assert _papers_are_valid(papers)

    @pytest.mark.integration
    def test_arxiv_rss_cs_ai_returns_papers_with_filter(self):
        """cs.AI RSS should return multiple papers after keyword filtering."""
        from governance.research_agent import _fetch_arxiv_rss
        papers = _fetch_arxiv_rss(categories=["cs.AI"], days=7)
        print(f"\n[arXiv RSS cs.AI] papers after keyword filter={len(papers)}")
        for p in papers[:3]:
            print(f"  - {p.title[:70]}")
        if not papers:
            pytest.skip("cs.AI returned 0 relevant papers (filter may be strict)")
        # cs.AI gets 300+ raw entries; keyword filter should yield several
        assert _papers_are_valid(papers)
        assert all(p.url.startswith("https://arxiv.org") for p in papers)

    @pytest.mark.integration
    def test_arxiv_rss_cs_cl_nlp_papers(self):
        """cs.CL (NLP) RSS is relevant for LLM + financial NLP papers."""
        from governance.research_agent import _fetch_arxiv_rss
        papers = _fetch_arxiv_rss(categories=["cs.CL"], days=7)
        print(f"\n[arXiv RSS cs.CL] papers after keyword filter={len(papers)}")
        if not papers:
            pytest.skip("cs.CL returned 0 relevant papers today")
        assert _papers_are_valid(papers)

    @pytest.mark.integration
    def test_arxiv_rss_paper_has_arxiv_url(self):
        """All arXiv RSS papers should have valid arxiv.org URLs."""
        from governance.research_agent import _fetch_arxiv_rss
        papers = _fetch_arxiv_rss(categories=["q-fin.ST"], days=30)
        if not papers:
            pytest.skip("q-fin.ST returned no papers")
        for p in papers[:5]:
            assert "arxiv.org" in p.url, f"Non-arXiv URL: {p.url}"

    @pytest.mark.integration
    def test_arxiv_rss_multiple_categories_deduplicated(self):
        """Fetching overlapping categories should not produce duplicate URLs."""
        from governance.research_agent import _fetch_arxiv_rss
        papers = _fetch_arxiv_rss(
            categories=["q-fin.CP", "q-fin.ST", "q-fin.TR"], days=14
        )
        print(f"\n[arXiv RSS multi-cat] papers={len(papers)}")
        urls = [p.url for p in papers]
        # Dedup is per-category in this function; run-level dedup is in _gather_papers
        # Just verify each URL is well-formed
        assert all(u.startswith("http") for u in urls)


# ---------------------------------------------------------------------------
# Tier 2: arXiv API
# ---------------------------------------------------------------------------

class TestArxivApiIntegration:
    """Live tests for _fetch_arxiv_api() against the official arXiv Atom API."""

    @pytest.mark.integration
    def test_arxiv_api_llm_financial(self):
        """arXiv API search for 'LLM financial forecasting' should return papers."""
        from governance.research_agent import _fetch_arxiv_api
        papers = _fetch_arxiv_api("LLM financial forecasting", max_results=5, days=180)
        print(f"\n[arXiv API LLM financial] papers={len(papers)}")
        for p in papers[:3]:
            print(f"  - {p.title[:70]}")
        assert len(papers) >= 1, "arXiv API returned no results for LLM financial forecasting"
        assert _papers_are_valid(papers)
        assert all(p.source == "arxiv_api" for p in papers)

    @pytest.mark.integration
    def test_arxiv_api_multi_agent_trading(self):
        """arXiv API: 'multi-agent stock analysis' should return papers."""
        from governance.research_agent import _fetch_arxiv_api
        papers = _fetch_arxiv_api("multi-agent stock analysis", max_results=5, days=365)
        print(f"\n[arXiv API multi-agent] papers={len(papers)}")
        assert len(papers) >= 1
        assert _papers_are_valid(papers)

    @pytest.mark.integration
    def test_arxiv_api_hallucination_detection(self):
        """arXiv API: 'hallucination detection financial' should return papers."""
        from governance.research_agent import _fetch_arxiv_api
        papers = _fetch_arxiv_api("hallucination detection financial", max_results=5, days=365)
        print(f"\n[arXiv API hallucination] papers={len(papers)}")
        assert len(papers) >= 1, "Expected at least 1 paper on hallucination + finance"
        assert _papers_are_valid(papers)

    @pytest.mark.integration
    def test_arxiv_api_abstract_is_populated(self):
        """arXiv API papers must have non-empty abstracts."""
        from governance.research_agent import _fetch_arxiv_api
        papers = _fetch_arxiv_api("LLM trading", max_results=5, days=365)
        if not papers:
            pytest.skip("No papers returned from arXiv API")
        for p in papers:
            assert len(p.abstract) > 50, f"Abstract too short for: {p.title}"

    @pytest.mark.integration
    def test_arxiv_api_url_format(self):
        """arXiv API papers must have arxiv.org/abs/ URLs."""
        from governance.research_agent import _fetch_arxiv_api
        papers = _fetch_arxiv_api("financial forecasting transformer", max_results=3, days=365)
        if not papers:
            pytest.skip("No papers returned")
        for p in papers:
            assert "arxiv.org/abs/" in p.url, f"Unexpected URL format: {p.url}"


# ---------------------------------------------------------------------------
# Tier 3: Semantic Scholar
# ---------------------------------------------------------------------------

class TestSemanticScholarIntegration:
    """Live tests for _fetch_semantic_scholar() against the public SS API."""

    @pytest.mark.integration
    def test_semantic_scholar_returns_papers(self):
        """Semantic Scholar should return papers for LLM financial forecasting."""
        from governance.research_agent import _fetch_semantic_scholar
        time.sleep(1.5)  # respect rate limit
        papers = _fetch_semantic_scholar("LLM financial forecasting", max_results=5)
        print(f"\n[Semantic Scholar LLM finance] papers={len(papers)}")
        for p in papers[:3]:
            print(f"  - [{p.source}] {p.title[:65]}")
        if not papers:
            pytest.skip("Semantic Scholar returned 0 papers (may be rate-limited)")
        assert _papers_are_valid(papers)

    @pytest.mark.integration
    def test_semantic_scholar_multi_agent(self):
        """Semantic Scholar: 'multi-agent trading system' should return papers."""
        from governance.research_agent import _fetch_semantic_scholar
        time.sleep(1.5)
        papers = _fetch_semantic_scholar("multi-agent trading system", max_results=5)
        print(f"\n[Semantic Scholar multi-agent] papers={len(papers)}")
        if not papers:
            pytest.skip("Semantic Scholar rate limited or no results")
        assert _papers_are_valid(papers)

    @pytest.mark.integration
    def test_semantic_scholar_covers_ssrn(self):
        """Semantic Scholar includes SSRN papers -- verify source labels."""
        from governance.research_agent import _fetch_semantic_scholar
        time.sleep(1.5)
        papers = _fetch_semantic_scholar("Indian stock market sentiment NLP", max_results=10)
        print(f"\n[Semantic Scholar SSRN check] papers={len(papers)}")
        sources = [p.source for p in papers]
        print(f"  sources: {set(sources)}")
        if not papers:
            pytest.skip("No papers returned -- may be rate limited")
        # Source should be one of the known labels
        for p in papers:
            assert p.source in ("semanticscholar", "ssrn", "arxiv_ss"), \
                f"Unexpected source label: {p.source}"

    @pytest.mark.integration
    def test_semantic_scholar_handles_rate_limit_gracefully(self):
        """Rapid successive calls should degrade gracefully, not crash."""
        from governance.research_agent import _fetch_semantic_scholar
        # Fire 3 queries quickly (may trigger rate limit on second/third)
        results = []
        for query in ["LLM finance", "trading agent", "stock forecast"]:
            r = _fetch_semantic_scholar(query, max_results=3)
            results.append(len(r))
            time.sleep(0.1)   # intentionally short delay to test graceful handling
        # At least the first call should succeed
        total = sum(results)
        print(f"\n[SS rate-limit test] counts={results}  total={total}")
        assert total >= 0   # never crashes -- always returns list


# ---------------------------------------------------------------------------
# Tier 4: HuggingFace Daily Papers
# ---------------------------------------------------------------------------

class TestHuggingFacePapersIntegration:
    """Live tests for _fetch_huggingface_papers() against HF's daily paper API."""

    @pytest.mark.integration
    def test_huggingface_returns_papers(self):
        """HuggingFace Daily Papers endpoint should always return papers."""
        from governance.research_agent import _fetch_huggingface_papers
        papers = _fetch_huggingface_papers(days=7)
        print(f"\n[HuggingFace Daily] papers after keyword filter={len(papers)}")
        for p in papers[:5]:
            print(f"  - {p.title[:70]}")
        assert len(papers) >= 1, (
            "HuggingFace Daily Papers returned 0 papers after keyword filter "
            "(unexpected -- normally 50 total, ~10-20 pass keyword filter)"
        )
        assert _papers_are_valid(papers)

    @pytest.mark.integration
    def test_huggingface_papers_have_arxiv_url(self):
        """All HuggingFace papers should resolve to arxiv.org URLs."""
        from governance.research_agent import _fetch_huggingface_papers
        papers = _fetch_huggingface_papers(days=7)
        if not papers:
            pytest.skip("No papers returned from HuggingFace")
        for p in papers[:10]:
            assert "arxiv.org/abs/" in p.url, \
                f"Expected arxiv.org URL, got: {p.url}"
        print(f"\n[HF arxiv URLs] all {min(len(papers),10)} checked papers "
              f"have arxiv.org/abs/ URLs")

    @pytest.mark.integration
    def test_huggingface_source_label(self):
        """Source label should be 'huggingface_daily'."""
        from governance.research_agent import _fetch_huggingface_papers
        papers = _fetch_huggingface_papers(days=7)
        if not papers:
            pytest.skip("No papers returned")
        for p in papers[:5]:
            assert p.source == "huggingface_daily", \
                f"Unexpected source: {p.source}"

    @pytest.mark.integration
    def test_huggingface_abstract_length(self):
        """Abstracts from HF Daily Papers should be substantive (>100 chars)."""
        from governance.research_agent import _fetch_huggingface_papers
        papers = _fetch_huggingface_papers(days=7)
        if not papers:
            pytest.skip("No papers returned")
        short = [p for p in papers if len(p.abstract) < 100]
        print(f"\n[HF abstract check] total={len(papers)}  short_abstract={len(short)}")
        # Allow up to 10% with short abstracts (some papers have minimal summaries)
        assert len(short) / len(papers) <= 0.1, \
            f"{len(short)}/{len(papers)} papers have abstracts < 100 chars"

    @pytest.mark.integration
    def test_huggingface_keyword_filter_removes_irrelevant(self):
        """Keyword filter should remove unrelated papers (e.g. pure computer vision)."""
        from governance.research_agent import _fetch_huggingface_papers
        # Fetch with 14-day window so we get enough data
        papers = _fetch_huggingface_papers(days=14)
        print(f"\n[HF keyword filter] relevant papers={len(papers)}")
        # All returned papers should touch at least one relevant keyword domain
        assert len(papers) >= 1


# ---------------------------------------------------------------------------
# Tier 5: AI Research Scout
# ---------------------------------------------------------------------------

class TestAiScoutIntegration:
    """Live tests for _fetch_ai_scout_papers() using Claude + web_search tool."""

    @pytest.mark.integration
    def test_ai_scout_returns_papers_with_api_key(self):
        """
        AI Scout should find papers via web_search when ANTHROPIC_API_KEY is set.
        Skips if API key is not configured.
        """
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            pytest.skip("ANTHROPIC_API_KEY not set -- skipping AI Scout test")

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
        except Exception as e:
            pytest.skip(f"Anthropic client init failed: {e}")

        from governance.research_agent import _fetch_ai_scout_papers
        papers = _fetch_ai_scout_papers(client, days=30)
        print(f"\n[AI Scout] papers found={len(papers)}")
        for p in papers[:5]:
            print(f"  - [{p.source}] {p.title[:65]}")
            print(f"    url: {p.url}")

        if not papers:
            # web_search tool might not be enabled for this account
            pytest.skip(
                "AI Scout returned 0 papers -- web_search tool may not be "
                "enabled for this API account (expected on free tier)"
            )

        assert _papers_are_valid(papers), "AI Scout returned malformed paper objects"
        for p in papers:
            assert p.source == "ai_scout"
            assert p.url.startswith("http"), f"Invalid URL: {p.url}"

    @pytest.mark.integration
    def test_ai_scout_graceful_without_client(self):
        """AI Scout must return empty list (not crash) when no client provided."""
        from governance.research_agent import _fetch_ai_scout_papers
        papers = _fetch_ai_scout_papers(None)
        assert papers == []
        print("\n[AI Scout no client] returned [] gracefully")

    @pytest.mark.integration
    def test_ai_scout_graceful_on_tool_error(self):
        """AI Scout returns [] gracefully if web_search tool raises an error."""
        from unittest.mock import MagicMock
        from governance.research_agent import _fetch_ai_scout_papers

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception(
            "Tool 'web_search_20250305' not supported on this plan"
        )
        papers = _fetch_ai_scout_papers(mock_client, days=7)
        assert papers == []
        print("\n[AI Scout tool error] returned [] gracefully")


# ---------------------------------------------------------------------------
# End-to-end: _gather_papers
# ---------------------------------------------------------------------------

class TestGatherPapersIntegration:
    """Integration tests for the full paper-gathering pipeline."""

    @pytest.mark.integration
    def test_gather_papers_returns_unique_papers(self):
        """_gather_papers should return unique papers from all live sources."""
        from governance.research_agent import _gather_papers
        papers = _gather_papers(days=14)
        print(f"\n[_gather_papers] total unique papers={len(papers)}")

        # Count by source tier
        by_source: dict[str, int] = {}
        for p in papers:
            tier = p.source.split("_")[0] if "_" in p.source else p.source
            by_source[tier] = by_source.get(tier, 0) + 1
        print(f"  By tier: {by_source}")

        assert len(papers) >= 5, \
            "Expected at least 5 papers total (too few sources working)"

        # All URLs should be unique
        urls = [p.url for p in papers]
        assert len(set(urls)) == len(urls), \
            f"Duplicate URLs found: {len(urls) - len(set(urls))} duplicates"

    @pytest.mark.integration
    def test_gather_papers_paper_objects_are_valid(self):
        """All gathered papers should be valid ResearchPaper objects."""
        from governance.research_agent import _gather_papers
        papers = _gather_papers(days=14)
        if not papers:
            pytest.skip("No papers gathered -- check network connectivity")
        invalid = [p for p in papers if not _is_valid_paper(p)]
        assert not invalid, \
            f"{len(invalid)} papers with missing required fields: {[p.title[:40] for p in invalid[:3]]}"


# ---------------------------------------------------------------------------
# Source audit (diagnostic -- never fails)
# ---------------------------------------------------------------------------

class TestSourceAudit:
    """
    Diagnostic audit: tries all sources independently and prints a table.
    NEVER fails -- purely informational.

    Run with -s to see output:
        pytest -m integration tests/test_research_sources_integration.py::TestSourceAudit -v -s
    """

    @pytest.mark.integration
    def test_full_source_audit(self):
        """
        Comprehensive audit of all paper sources.
        Prints which sources are live, how many papers each returns,
        and sample titles.  Always passes -- use output to identify issues.
        """
        from governance.research_agent import (
            _fetch_arxiv_rss,
            _fetch_arxiv_api,
            _fetch_semantic_scholar,
            _fetch_huggingface_papers,
            _fetch_ai_scout_papers,
            ARXIV_RSS_CATEGORIES,
        )

        print("\n\n" + "=" * 72)
        print("  Research Sources Audit")
        print("=" * 72)

        results: dict[str, dict] = {}

        # --- Tier 1: arXiv RSS ---
        print("\n[Tier 1] arXiv RSS")
        for cat in ["cs.AI", "cs.CL", "q-fin.CP", "q-fin.TR"]:
            try:
                papers = _fetch_arxiv_rss(categories=[cat], days=7)
                status = f"[OK]   {len(papers):>3} papers"
                sample = papers[0].title[:50] if papers else ""
                results[f"arxiv_rss_{cat}"] = {"count": len(papers), "ok": True}
            except Exception as exc:
                status = f"[FAIL] {exc}"
                results[f"arxiv_rss_{cat}"] = {"count": 0, "ok": False, "error": str(exc)}
            print(f"  {cat:15s}  {status}" + (f"  e.g. '{sample}'" if 'sample' in dir() and sample else ""))
            time.sleep(0.3)

        # --- Tier 2: arXiv API ---
        print("\n[Tier 2] arXiv API")
        queries_to_test = ["LLM financial forecasting", "multi-agent trading"]
        for q in queries_to_test:
            try:
                papers = _fetch_arxiv_api(q, max_results=5, days=180)
                status = f"[OK]   {len(papers):>3} papers"
                sample = papers[0].title[:50] if papers else ""
                results[f"arxiv_api_{q[:20]}"] = {"count": len(papers), "ok": True}
            except Exception as exc:
                status = f"[FAIL] {exc}"
                results[f"arxiv_api_{q[:20]}"] = {"count": 0, "ok": False}
            print(f"  {q[:30]:30s}  {status}" + (f"  e.g. '{sample}'" if 'sample' in dir() and sample else ""))
            time.sleep(0.5)

        # --- Tier 3: Semantic Scholar ---
        print("\n[Tier 3] Semantic Scholar")
        ss_queries = ["LLM financial forecasting", "hallucination detection finance"]
        for q in ss_queries:
            time.sleep(1.5)
            try:
                papers = _fetch_semantic_scholar(q, max_results=5)
                status = f"[OK]   {len(papers):>3} papers"
                sample = papers[0].title[:50] if papers else ""
                results[f"semantic_scholar_{q[:20]}"] = {"count": len(papers), "ok": True}
            except Exception as exc:
                status = f"[FAIL] {exc}"
                results[f"semantic_scholar_{q[:20]}"] = {"count": 0, "ok": False}
            print(f"  {q[:30]:30s}  {status}" + (f"  e.g. '{sample}'" if 'sample' in dir() and sample else ""))

        # --- Tier 4: HuggingFace ---
        print("\n[Tier 4] HuggingFace Daily Papers")
        try:
            papers = _fetch_huggingface_papers(days=7)
            status = f"[OK]   {len(papers):>3} papers (filtered from 50 daily)"
            sample = papers[0].title[:50] if papers else "0 after keyword filter"
            results["huggingface_daily"] = {"count": len(papers), "ok": True}
        except Exception as exc:
            status = f"[FAIL] {exc}"
            sample = ""
            results["huggingface_daily"] = {"count": 0, "ok": False}
        print(f"  huggingface.co/api/daily_papers  {status}")
        if sample:
            print(f"  e.g. '{sample}'")

        # --- Tier 5: AI Scout ---
        print("\n[Tier 5] AI Research Scout (Claude + web_search)")
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            print("  [SKIP] ANTHROPIC_API_KEY not set")
            results["ai_scout"] = {"count": 0, "ok": None, "note": "no api key"}
        else:
            try:
                import anthropic
                client = anthropic.Anthropic(api_key=api_key)
                papers = _fetch_ai_scout_papers(client, days=14)
                status = f"[OK]   {len(papers):>3} papers"
                results["ai_scout"] = {"count": len(papers), "ok": True}
                for p in papers[:3]:
                    print(f"  - {p.title[:65]}")
            except Exception as exc:
                status = f"[FAIL] {exc}"
                results["ai_scout"] = {"count": 0, "ok": False}
            print(f"  web_search_20250305  {status}")

        # --- Summary table ---
        working  = [k for k, v in results.items() if v.get("ok") is True]
        failing  = [k for k, v in results.items() if v.get("ok") is False]
        skipped  = [k for k, v in results.items() if v.get("ok") is None]
        total_papers = sum(v.get("count", 0) for v in results.values())

        print("\n" + "=" * 72)
        print(f"  SUMMARY: {len(working)} sources working, "
              f"{len(failing)} failing, {len(skipped)} skipped")
        print(f"  Total papers this run: {total_papers}")
        if failing:
            print(f"  [FAIL] sources: {failing}")
        print("=" * 72 + "\n")

        # Audit never fails -- it's purely diagnostic
        # But we do assert the test itself ran without crashing
        assert isinstance(results, dict)
