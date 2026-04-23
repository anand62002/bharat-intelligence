"""
governance/research_agent.py -- Bharat Intelligence: Daily AI Research Scanner
===============================================================================
Scans arXiv (cs.AI, cs.LG, q-fin), SSRN (via Semantic Scholar), and keyword
searches for papers relevant to improving the multi-agent stock analysis system.

Pipeline (daily, 07:30 IST)
-----------------------------
  1. Gather papers from all sources (arXiv RSS + API, Semantic Scholar)
  2. Deduplicate by URL against Supabase (skip already-processed papers)
  3. Score each paper 0-100 via Claude Haiku relevance scoring
  4. For relevance >= 75:
       a. Generate proposed_change via Claude Sonnet
       b. Run per-agent debate (8 agents x 1 Haiku call each)
       c. Save to research_proposals table
  5. Telegram digest of high-relevance findings
  6. Proposal approval (via CLI --approve <id>) triggers GitHub PR via github_manager

Usage
-----
  python governance/research_agent.py --run-now              # run immediately
  python governance/research_agent.py --run-now --dry        # dry run, no DB writes
  python governance/research_agent.py --approve <id>         # create PR for approved proposal
  python governance/research_agent.py --list [--status pending]

Entry points
------------
  run(dry_run=False) -> dict              Daily pipeline callable
  approve_proposal(proposal_id) -> dict   Create GitHub PR for approved proposal
  list_proposals(status, limit) -> list   Dashboard data source
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

import feedparser
import requests
from dotenv import load_dotenv

# GitHubManager is optional (requires PyGithub).  Import at module level so
# tests can patch governance.research_agent.GitHubManager cleanly.
try:
    from governance.github_manager import GitHubManager
except Exception:          # ImportError or RuntimeError when env vars missing
    GitHubManager = None   # type: ignore[assignment,misc]

load_dotenv()

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HAIKU_MODEL         = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5")
SONNET_MODEL        = os.getenv("CLAUDE_MODEL",       "claude-sonnet-4-5")
HAIKU_MAX_TOKENS    = 512
SONNET_MAX_TOKENS   = 1024
RELEVANCE_THRESHOLD = 75    # papers scoring >= this get a Sonnet proposal
MAX_PAPERS_PER_SOURCE = 15  # per source per run
DAYS_LOOKBACK       = 7     # only process papers from the last N days
SS_REQUESTS_DELAY   = 2.0   # seconds between Semantic Scholar calls (rate limit)

# arXiv RSS categories to scan
ARXIV_RSS_CATEGORIES = [
    "cs.AI",     # Artificial Intelligence
    "cs.LG",     # Machine Learning
    "cs.CL",     # Computation and Language (NLP/LLM papers)
    "cs.IR",     # Information Retrieval (RAG / embedding papers)
    "q-fin.CP",  # Computational Finance
    "q-fin.ST",  # Statistical Finance
    "q-fin.TR",  # Trading and Market Microstructure
    "q-fin.PM",  # Portfolio Management
]

# Keyword queries for arXiv API search
ARXIV_KEYWORD_QUERIES = [
    "LLM financial forecasting",
    "multi-agent stock analysis",
    "hallucination detection financial",
    "large language model trading",
    "RAG retrieval augmented generation finance",
    "reinforcement learning portfolio management LLM",
]

# Keyword queries for Semantic Scholar (covers arXiv + SSRN + all venues)
SEMANTIC_SCHOLAR_QUERIES = [
    "LLM financial forecasting",
    "multi-agent trading system",
    "hallucination detection finance",
    "large language model stock prediction India",
    "sentiment analysis stock market NLP",
]

# Keyword pre-filter for HuggingFace Daily Papers
# (50 community-curated ML papers/day; Haiku scoring does final relevance check)
HF_KEYWORD_FILTER = {
    "llm", "language model", "agent", "multi-agent", "financ", "trading",
    "forecast", "predict", "sentiment", "portfolio", "stock", "market",
    "reinforcement", "transformer", "retrieval", "rag", "embedding",
    "hallucin", "fact", "ground", "reason", "chain", "tool", "function",
}

# Queries Claude AI Scout searches for (via web_search tool)
AI_SCOUT_QUERIES = [
    "LLM-based financial forecasting emerging markets 2025 arxiv paper",
    "multi-agent AI trading system hallucination detection 2025",
    "Indian stock market NLP sentiment analysis language model 2025",
    "RAG retrieval augmented generation financial analysis new paper 2025",
]

DEBATE_AGENTS = [
    "technical",
    "fundamental",
    "sentiment",
    "institutional",
    "macro",
    "commodities",
    "historical_rag",
    "fact_checker",
]

# ---------------------------------------------------------------------------
# System context given to Sonnet for proposal generation
# ---------------------------------------------------------------------------

_SYSTEM_CONTEXT = """
You are a senior software architect for Bharat Intelligence, a production multi-agent
Indian stock analysis system.

Architecture:
- 7 analysis agents: technical (RSI/MACD/ADX/EMA-20/50/200), fundamental
  (PE/ROCE/debt_equity/promoter holdings via Screener.in), sentiment (RSS NLP via
  Haiku, misinformation detection), institutional (FII/DII flows from NSE/BSE, bulk
  deals), macro (RBI repo rate/INR-USD/India VIX/FRED US10Y), commodities
  (GOLDBEES/Brent/WTI/silver), historical_rag (pgvector similarity on OpenAI embeddings)
- Each agent returns: {signal: BUY/HOLD/SELL/AVOID, score: 0-100, detail: dict,
  agent_name: str, data_sources: list[str]}
- Daily LangGraph orchestrator synthesises all agents via Claude Sonnet
- Governance: fact_checker (Haiku claim verification post-synthesis),
  performance_tracker (weekly outcome-vs-price accuracy), hallucination_detector
  (weekly audit), research_agent (this module)
- Stack: Python 3.11, LangGraph, Supabase (PostgreSQL + pgvector), Anthropic API,
  feedparser, yfinance, BeautifulSoup
- Target: NSE/BSE Nifty 500 stocks; Indian retail investor audience
""".strip()

# ---------------------------------------------------------------------------
# Agent debate personas
# ---------------------------------------------------------------------------

_AGENT_PERSONAS: dict[str, str] = {
    "technical": (
        "You are the Technical Analysis Agent in Bharat Intelligence. "
        "You compute RSI (14), MACD (12/26/9), ADX (14), and EMA crossovers "
        "(20/50/200) for NSE/BSE stocks using yfinance OHLCV data. "
        "You care about signal quality, false-positive rates, look-ahead bias, "
        "and whether proposed changes would survive a 90-day backtest."
    ),
    "fundamental": (
        "You are the Fundamental Analysis Agent. You parse Screener.in data "
        "for PE ratios, ROCE, revenue CAGR, debt/equity, promoter holdings, "
        "EPS growth, and OCF margins. "
        "You value financial metric accuracy, sector-appropriate benchmarks, "
        "and data freshness. You are sceptical of over-engineered scoring."
    ),
    "sentiment": (
        "You are the Sentiment Analysis Agent. You parse 5+ Indian financial "
        "RSS feeds (ET Markets, Moneycontrol, Hindu BizLine, Google News) and "
        "score headline sentiment via Claude Haiku. "
        "You care about NLP accuracy for Indian financial vocabulary, "
        "handling Hinglish content, and false-positive misinformation flags."
    ),
    "institutional": (
        "You are the Institutional Flow Analysis Agent. You track NSE/BSE "
        "FII/DII daily flows, bulk/block deals, and mutual fund activity. "
        "You care about data freshness (multiple fallback sources), "
        "flow-to-price correlation accuracy, and anti-bot bypass robustness."
    ),
    "macro": (
        "You are the Macro Analysis Agent. You monitor RBI repo rate, "
        "INR/USD, India VIX, FRED US 10-year yield, DXY index, and "
        "commodity indices. You care about leading indicator quality, "
        "India-specific calibration, and avoiding spurious US-India correlations."
    ),
    "commodities": (
        "You are the Commodities Agent. You track gold (NSE GOLDBEES proxy), "
        "Brent and WTI crude, and silver. You map commodity moves to sector "
        "impact (energy, metals, FMCG, auto). "
        "You care about commodity-sector correlation accuracy for Indian markets."
    ),
    "historical_rag": (
        "You are the Historical RAG Agent. You retrieve analogous historical "
        "market events using pgvector cosine similarity on OpenAI embeddings. "
        "You care about retrieval quality, embedding freshness, event database "
        "coverage, and whether retrieved analogues are genuinely predictive."
    ),
    "fact_checker": (
        "You are the Governance/Fact Checker Agent -- the most sceptical voice. "
        "You verify agent claims against raw data via Claude Haiku and flag "
        "hallucinations. "
        "ALWAYS ask: Does this proposal increase or decrease hallucination risk? "
        "Is the expected improvement measurable and testable? Will it complicate "
        "the governance layer? Lean AGAINST unless the paper is empirically solid."
    ),
}


# ---------------------------------------------------------------------------
# Paper dataclass
# ---------------------------------------------------------------------------

@dataclass
class ResearchPaper:
    title:     str
    abstract:  str
    url:       str
    source:    str            # 'arxiv_rss' | 'arxiv_api' | 'semanticscholar'
    published: Optional[str] = None
    authors:   list          = field(default_factory=list)
    venue:     Optional[str] = None
    # Filled after scoring
    relevance:  Optional[int] = None
    relevance_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Infrastructure helpers
# ---------------------------------------------------------------------------

def _supabase():
    """Return a Supabase client or None if not configured."""
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception as exc:
        log.warning("Supabase connect failed: %s", exc)
        return None


def _claude():
    """Return an Anthropic client or None if API key is missing."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        log.warning("ANTHROPIC_API_KEY not set -- AI scoring disabled")
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=api_key)
    except Exception as exc:
        log.warning("Anthropic client init failed: %s", exc)
        return None


def _send_telegram(message: str, dry_run: bool = False) -> bool:
    """Send Telegram notification (same pattern as performance_tracker)."""
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if dry_run:
        print(f"\n  [TELEGRAM DRY RUN]\n{message}\n")
        return True
    if not token or not chat_id:
        log.debug("Telegram not configured -- skipping notification")
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        log.warning("Telegram send failed: %s", exc)
        return False


def _extract_json(text: str) -> dict:
    """
    Robustly extract the first JSON object from *text*.
    Handles markdown code fences and trailing text after the closing brace
    (a common Haiku output pattern).
    """
    # Strip markdown fences
    text = re.sub(r"^```[a-z]*\n?", "", text.strip())
    text = re.sub(r"\n?```$", "", text).strip()
    start = text.find("{")
    if start < 0:
        raise ValueError("No JSON object found in model response")
    depth = 0
    for idx, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : idx + 1])
    raise ValueError("Unclosed JSON object in model response")


# ---------------------------------------------------------------------------
# Paper fetching
# ---------------------------------------------------------------------------

_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; BharatIntelligence/1.0; "
        "+https://github.com/anand62002/bharat-intelligence)"
    )
}


def _fetch_arxiv_rss(
    categories: list[str] = ARXIV_RSS_CATEGORIES,
    days: int = DAYS_LOOKBACK,
) -> list[ResearchPaper]:
    """
    Fetch recent papers from arXiv RSS feeds for the given categories.

    Each arXiv RSS feed provides the newest submissions (last ~24h).
    We filter by date and by a keyword relevance pre-check on the title.
    """
    papers: list[ResearchPaper] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Keywords to pre-filter arXiv CS/ML papers (q-fin papers pass by default)
    _RELEVANT_KEYWORDS = {
        "llm", "language model", "agent", "multi-agent", "hallucin",
        "financ", "trading", "forecast", "predict", "sentiment",
        "portfolio", "stock", "market", "reinforcement", "attention",
        "transformer", "retrieval", "rag", "embedding",
    }

    for cat in categories:
        url = f"https://rss.arxiv.org/rss/{cat}"
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:MAX_PAPERS_PER_SOURCE]:
                title    = (entry.get("title") or "").strip().replace("\n", " ")
                abstract = (entry.get("summary") or "").strip()
                # Prefer link (HTTPS URL) over id (may be OAI format like oai:arXiv.org:2604.18873v1)
                link = entry.get("link") or entry.get("id") or ""
                # Convert OAI identifiers to proper HTTPS arXiv URLs
                if link and not link.startswith("http"):
                    m = re.search(r"(\d{4}\.\d{4,5})", link)
                    if m:
                        link = f"https://arxiv.org/abs/{m.group(1)}"

                if not title or not abstract or not link:
                    continue

                # Date filtering (arXiv RSS entries have published/updated)
                pub_str = entry.get("published", "") or entry.get("updated", "")
                if pub_str:
                    try:
                        import email.utils
                        pub_dt = datetime(*email.utils.parsedate(pub_str)[:6],
                                          tzinfo=timezone.utc)
                        if pub_dt < cutoff:
                            continue
                    except Exception:
                        pass  # can't parse date -- include anyway

                # Keyword pre-filter for cs.* categories (q-fin passes by default)
                if cat.startswith("cs."):
                    title_lower = title.lower()
                    abstract_lower = abstract.lower()
                    combined = title_lower + " " + abstract_lower
                    if not any(kw in combined for kw in _RELEVANT_KEYWORDS):
                        continue

                papers.append(ResearchPaper(
                    title     = title,
                    abstract  = abstract[:1200],
                    url       = link,
                    source    = f"arxiv_rss_{cat}",
                    published = pub_str,
                    venue     = f"arXiv {cat}",
                ))

            log.info("arXiv RSS [%s]: %d papers collected", cat, len(papers))
        except Exception as exc:
            log.warning("arXiv RSS [%s] failed: %s", cat, exc)

    return papers


def _fetch_arxiv_api(
    query: str,
    max_results: int = 10,
    days: int = DAYS_LOOKBACK,
) -> list[ResearchPaper]:
    """
    Search arXiv API for papers matching *query* (title + abstract search).
    Uses the official arXiv Atom API.
    """
    endpoint = "http://export.arxiv.org/api/query"
    params = {
        "search_query": f"all:{quote_plus(query)}",
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    try:
        resp = requests.get(endpoint, params=params, timeout=20,
                            headers=_REQUEST_HEADERS)
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        papers: list[ResearchPaper] = []
        for entry in feed.entries:
            title    = (entry.get("title") or "").strip().replace("\n", " ")
            abstract = (entry.get("summary") or "").strip()
            link     = entry.get("id") or entry.get("link") or ""
            if not title or not abstract or not link:
                continue

            pub_str = entry.get("published", "")
            if pub_str:
                try:
                    pub_dt = datetime.fromisoformat(
                        pub_str.replace("Z", "+00:00")
                    )
                    if pub_dt < cutoff:
                        continue
                except Exception:
                    pass

            authors = [
                a.get("name", "") for a in entry.get("authors", [])
            ]
            papers.append(ResearchPaper(
                title     = title,
                abstract  = abstract[:1200],
                url       = link,
                source    = "arxiv_api",
                published = pub_str,
                authors   = authors[:5],
                venue     = "arXiv",
            ))
        return papers
    except Exception as exc:
        log.warning("arXiv API [%s] failed: %s", query, exc)
        return []


def _fetch_semantic_scholar(
    query: str,
    max_results: int = 10,
) -> list[ResearchPaper]:
    """
    Search Semantic Scholar API for papers.

    Semantic Scholar indexes arXiv, SSRN, ACL, AAAI, NeurIPS, ICML, ICLR,
    EMNLP, and most major venues. The free public API allows ~1 req/sec.
    """
    endpoint = "https://api.semanticscholar.org/graph/v1/paper/search"
    params = {
        "query":  query,
        "fields": "title,abstract,year,url,externalIds,venue,authors,publicationDate",
        "limit":  max_results,
        # Only 2024+ papers
        "year":   "2024-",
    }
    headers = dict(_REQUEST_HEADERS)
    ss_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")
    if ss_key:
        headers["x-api-key"] = ss_key

    try:
        resp = requests.get(endpoint, params=params, headers=headers, timeout=20)
        if resp.status_code == 429:
            log.warning("Semantic Scholar rate limited -- sleeping 10s")
            time.sleep(10)
            resp = requests.get(endpoint, params=params,
                                headers=headers, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        papers: list[ResearchPaper] = []
        for item in data.get("data", []):
            title    = (item.get("title") or "").strip()
            abstract = (item.get("abstract") or "").strip()
            url      = item.get("url") or ""

            # Prefer the arXiv URL when available (canonical)
            ext_ids = item.get("externalIds") or {}
            if "ArXiv" in ext_ids:
                url = f"https://arxiv.org/abs/{ext_ids['ArXiv']}"
            elif "SSRN" in ext_ids:
                url = f"https://papers.ssrn.com/sol3/papers.cfm?abstract_id={ext_ids['SSRN']}"

            if not title or not abstract or not url:
                continue

            # Detect SSRN as source label
            source_label = "semanticscholar"
            if "SSRN" in ext_ids:
                source_label = "ssrn"
            elif "ArXiv" in ext_ids:
                source_label = "arxiv_ss"

            authors = [
                a.get("name", "") for a in (item.get("authors") or [])
            ]
            papers.append(ResearchPaper(
                title     = title,
                abstract  = abstract[:1200],
                url       = url,
                source    = source_label,
                published = item.get("publicationDate") or str(item.get("year", "")),
                authors   = authors[:5],
                venue     = item.get("venue") or "",
            ))
        return papers
    except Exception as exc:
        log.warning("Semantic Scholar [%s] failed: %s", query, exc)
        return []


def _fetch_huggingface_papers(days: int = DAYS_LOOKBACK) -> list[ResearchPaper]:
    """
    Fetch the daily curated ML papers from Hugging Face Daily Papers.

    HuggingFace community members submit arXiv papers daily. The endpoint
    returns ~50 high-signal ML papers per day — all with full abstracts,
    authors, and arXiv IDs.  We pre-filter by domain keywords before
    sending to Haiku relevance scoring.

    URL: https://huggingface.co/api/daily_papers
    """
    url = "https://huggingface.co/api/daily_papers"
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    papers: list[ResearchPaper] = []
    try:
        resp = requests.get(url, timeout=20, headers=_REQUEST_HEADERS)
        resp.raise_for_status()
        items = resp.json()
        if not isinstance(items, list):
            log.warning("HuggingFace: unexpected response format")
            return []

        for item in items:
            paper = item.get("paper") or {}
            title    = str(paper.get("title") or "").strip()
            abstract = str(paper.get("summary") or "").strip()
            arxiv_id = str(paper.get("id") or "").strip()

            if not title or not abstract or not arxiv_id:
                continue

            arxiv_url = f"https://arxiv.org/abs/{arxiv_id}"

            # Date filter
            pub_str = paper.get("publishedAt") or item.get("publishedAt") or ""
            if pub_str:
                try:
                    pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                    if pub_dt < cutoff:
                        continue
                except Exception:
                    pass

            # Keyword pre-filter: skip clearly unrelated CS papers
            combined = (title + " " + abstract).lower()
            if not any(kw in combined for kw in HF_KEYWORD_FILTER):
                continue

            authors = [
                str(a.get("name", "")) for a in (paper.get("authors") or [])
                if isinstance(a, dict) and a.get("name")
            ]
            papers.append(ResearchPaper(
                title     = title,
                abstract  = abstract[:1200],
                url       = arxiv_url,
                source    = "huggingface_daily",
                published = pub_str,
                authors   = authors[:5],
                venue     = "HuggingFace Daily / arXiv",
            ))

        log.info("HuggingFace Daily Papers: %d relevant papers collected "
                 "(from %d total)", len(papers), len(items))
    except Exception as exc:
        log.warning("HuggingFace Daily Papers failed: %s", exc)
    return papers


def _fetch_ai_scout_papers(claude_client, days: int = DAYS_LOOKBACK) -> list[ResearchPaper]:
    """
    AI Research Scout: uses Claude Sonnet with the web_search_20250305 built-in
    tool to actively search the web for recent papers the RSS/API sources
    missed (conference papers not yet on arXiv, preprints on institutional
    pages, late-breaking workshop papers, etc.).

    Falls back gracefully if:
      - No Claude client available
      - web_search tool not enabled for the account
      - Any other API error

    The scout performs up to 3 web searches and returns a structured JSON
    list of papers.  We re-use _extract_json() for robustness.
    """
    if claude_client is None:
        return []

    query_bullets = "\n".join(f"- {q}" for q in AI_SCOUT_QUERIES)
    prompt = f"""\
Search the web for recent academic papers published within the last {days * 2} days
(year 2025-2026) that are highly relevant to any of these topics:
{query_bullets}

Search on arxiv.org, papers.ssrn.com, recent conference proceedings
(NeurIPS 2025, ICLR 2025, ICML 2025, ACL 2025, EMNLP 2025, AAAI 2025,
FinNLP, FinLLM workshops), and institutional preprint servers.

Return ONLY valid JSON (no prose before or after):
{{
  "papers": [
    {{
      "title": "Full paper title",
      "abstract": "1-3 sentence summary of key findings",
      "url": "https://arxiv.org/abs/XXXX.XXXXX or https://papers.ssrn.com/...",
      "authors": ["Author Name"],
      "venue": "arXiv | SSRN | NeurIPS 2025 | etc",
      "published": "YYYY-MM-DD"
    }}
  ]
}}

Include ONLY papers with real, specific URLs.  Aim for 5-8 papers."""

    papers: list[ResearchPaper] = []
    try:
        resp = claude_client.messages.create(
            model      = SONNET_MODEL,
            max_tokens = 2000,
            tools      = [{
                "type":     "web_search_20250305",
                "name":     "web_search",
                "max_uses": 3,
            }],
            messages = [{"role": "user", "content": prompt}],
        )

        # Collect all text blocks (tool-result blocks have no .text)
        text_content = "".join(
            block.text for block in resp.content
            if hasattr(block, "text")
        )
        if not text_content.strip():
            log.warning("AI Scout: no text in response (only tool-use blocks?)")
            return []

        parsed     = _extract_json(text_content)
        paper_list = parsed.get("papers") or []

        for item in paper_list:
            title    = str(item.get("title",    "")).strip()
            abstract = str(item.get("abstract", "")).strip()
            url      = str(item.get("url",      "")).strip()
            if not title or not abstract or not url:
                continue
            # Basic URL sanity check
            if not url.startswith("http"):
                continue
            papers.append(ResearchPaper(
                title     = title,
                abstract  = abstract[:1200],
                url       = url,
                source    = "ai_scout",
                published = str(item.get("published", "")),
                authors   = [str(a) for a in (item.get("authors") or [])][:5],
                venue     = str(item.get("venue", "")),
            ))
        log.info("AI Scout: %d papers discovered via web search", len(papers))

    except Exception as exc:
        # Gracefully skip if web_search tool is unavailable or quota exceeded
        err_str = str(exc).lower()
        if any(kw in err_str for kw in ("web_search", "tool", "beta", "not supported")):
            log.info("AI Scout: web_search tool unavailable -- skipping (%s)", exc)
        else:
            log.warning("AI Scout failed: %s", exc)

    return papers


def _gather_papers(
    days: int = DAYS_LOOKBACK,
    claude_client=None,
) -> list[ResearchPaper]:
    """
    Collect papers from all four source tiers and de-duplicate by URL.

    Source tiers:
      Tier 1 – arXiv RSS (cs.AI/LG/CL/IR + q-fin.*)   : newest preprints
      Tier 2 – arXiv API keyword search                 : targeted recall
      Tier 3 – Semantic Scholar keyword search          : covers SSRN + venues
      Tier 4 – HuggingFace Daily Papers                 : curated community picks
      Tier 5 – AI Research Scout (Claude + web_search)  : deep web search

    Returns a list of unique ResearchPaper objects.
    """
    all_papers: list[ResearchPaper] = []
    seen_urls: set[str] = set()

    def _add(papers: list[ResearchPaper]) -> None:
        for p in papers:
            if p.url and p.url not in seen_urls:
                seen_urls.add(p.url)
                all_papers.append(p)

    # Tier 1 — arXiv RSS
    log.info("[Tier 1] arXiv RSS (%d categories)...", len(ARXIV_RSS_CATEGORIES))
    _add(_fetch_arxiv_rss(days=days))

    # Tier 2 — arXiv API keyword search
    log.info("[Tier 2] arXiv API (%d queries)...", len(ARXIV_KEYWORD_QUERIES))
    for query in ARXIV_KEYWORD_QUERIES:
        _add(_fetch_arxiv_api(query, max_results=10, days=days))
        time.sleep(0.5)

    # Tier 3 — Semantic Scholar (covers arXiv + SSRN + conference venues)
    log.info("[Tier 3] Semantic Scholar (%d queries)...",
             len(SEMANTIC_SCHOLAR_QUERIES))
    for query in SEMANTIC_SCHOLAR_QUERIES:
        _add(_fetch_semantic_scholar(query, max_results=10))
        time.sleep(SS_REQUESTS_DELAY)

    # Tier 4 — HuggingFace Daily Papers
    log.info("[Tier 4] HuggingFace Daily Papers...")
    _add(_fetch_huggingface_papers(days=days))

    # Tier 5 — AI Research Scout (optional; requires Claude client)
    if claude_client is not None:
        log.info("[Tier 5] AI Research Scout (Claude + web_search)...")
        _add(_fetch_ai_scout_papers(claude_client, days=days))
    else:
        log.info("[Tier 5] AI Research Scout skipped (no Claude client)")

    log.info("Total unique papers gathered: %d across all tiers", len(all_papers))
    return all_papers


# ---------------------------------------------------------------------------
# Relevance scoring (Claude Haiku)
# ---------------------------------------------------------------------------

_RELEVANCE_SYSTEM = (
    "You are a research relevance scorer for Bharat Intelligence, a multi-agent "
    "Indian stock analysis system that uses LLMs (Claude Haiku + Sonnet), "
    "LangGraph, yfinance, and Supabase.\n\n"
    "Score papers 0-100 on how relevant their findings are to IMPROVING this system.\n\n"
    "High scores (75-100): Papers directly applicable — LLM accuracy in finance, "
    "multi-agent coordination, hallucination detection, Indian market NLP, "
    "reinforcement learning for trading, RAG for financial Q&A, "
    "news sentiment for stock prediction.\n\n"
    "Medium scores (40-74): Potentially applicable — general LLM evaluation, "
    "time-series forecasting, portfolio optimisation, NLP for structured data.\n\n"
    "Low scores (0-39): Tangentially related or not applicable — "
    "pure computer vision, non-financial NLP, hardware optimisation."
)

_RELEVANCE_PROMPT_TMPL = """\
Rate this paper's relevance to improving the Bharat Intelligence multi-agent stock analysis system.

Title: {title}
Venue/Source: {venue}
Abstract:
{abstract}

Return ONLY valid JSON (no prose before or after):
{{
  "relevance": <integer 0-100>,
  "reason": "<one concise sentence>",
  "applicable_agent": "<most relevant agent name or null>"
}}"""


def _score_relevance(paper: ResearchPaper, client) -> int:
    """
    Ask Claude Haiku to score how relevant *paper* is to the system (0-100).
    Returns 0 on failure.
    """
    if client is None:
        return 0
    prompt = _RELEVANCE_PROMPT_TMPL.format(
        title    = paper.title,
        venue    = paper.venue or paper.source,
        abstract = paper.abstract[:800],
    )
    try:
        msg = client.messages.create(
            model      = HAIKU_MODEL,
            max_tokens = HAIKU_MAX_TOKENS,
            system     = _RELEVANCE_SYSTEM,
            messages   = [{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        parsed = _extract_json(raw)
        score  = int(parsed.get("relevance", 0))
        reason = str(parsed.get("reason", ""))
        agent  = parsed.get("applicable_agent")
        paper.relevance        = max(0, min(100, score))
        paper.relevance_reason = reason
        log.info(
            "  Relevance %3d  [%s]  agent=%s  %s",
            score, paper.source, agent, paper.title[:60],
        )
        return paper.relevance
    except Exception as exc:
        log.warning("Relevance scoring failed for '%s': %s", paper.title[:50], exc)
        return 0


# ---------------------------------------------------------------------------
# Proposal generation (Claude Sonnet)
# ---------------------------------------------------------------------------

_PROPOSAL_PROMPT_TMPL = """\
Based on this academic paper, propose a SPECIFIC and CONCRETE code improvement for Bharat Intelligence.

Paper title: {title}
Source: {source}
Published: {published}
Abstract:
{abstract}

Requirements:
- Be SPECIFIC: name the exact file (e.g. agents/technical.py), function, and change
- Be ACTIONABLE: describe what code to add/modify/remove
- Be REALISTIC: consider implementation effort (hours, not months)
- Flag if paid data sources are required

Return ONLY valid JSON (no prose before or after):
{{
  "proposed_change": "<2-4 sentences: what specifically to change and why>",
  "impacted_agents": ["<agent_name>", ...],
  "cost_impact": "<low|medium|high>",
  "implementation_effort_hours": <integer>,
  "requires_paid_data": <true|false>,
  "expected_improvement": "<specific measurable outcome>",
  "file_to_change": "<relative/path/to/file.py>"
}}"""


def _generate_proposal(paper: ResearchPaper, client) -> Optional[dict]:
    """
    Ask Claude Sonnet to generate a concrete improvement proposal based on *paper*.
    Returns a dict or None on failure.
    """
    if client is None:
        return None
    prompt = _PROPOSAL_PROMPT_TMPL.format(
        title     = paper.title,
        source    = paper.source,
        published = paper.published or "unknown",
        abstract  = paper.abstract[:1200],
    )
    try:
        msg = client.messages.create(
            model      = SONNET_MODEL,
            max_tokens = SONNET_MAX_TOKENS,
            system     = _SYSTEM_CONTEXT,
            messages   = [{"role": "user", "content": prompt}],
        )
        raw    = msg.content[0].text.strip()
        parsed = _extract_json(raw)
        return parsed
    except Exception as exc:
        log.warning("Proposal generation failed for '%s': %s", paper.title[:50], exc)
        return None


# ---------------------------------------------------------------------------
# Agent debate (Claude Haiku per agent persona)
# ---------------------------------------------------------------------------

_DEBATE_PROMPT_TMPL = """\
You are reviewing a proposed system improvement based on recent AI research.

Paper title: {title}
Abstract: {abstract}

Proposed change to Bharat Intelligence:
{proposed_change}

Impacted agents: {impacted_agents}

Evaluate from YOUR agent's perspective. Consider whether this genuinely improves
the system's accuracy, reliability, or maintainability.

Return ONLY valid JSON (no prose before or after):
{{
  "stance": "<FOR|AGAINST|ABSTAIN>",
  "argument": "<one concrete sentence from your agent's viewpoint>",
  "confidence": <integer 0-100>,
  "key_concern": "<specific technical benefit or risk you see>"
}}"""


def _debate_one_agent(
    agent_name: str,
    paper: ResearchPaper,
    proposed_change: str,
    impacted_agents: list,
    client,
) -> dict:
    """
    Ask one agent persona to vote FOR / AGAINST / ABSTAIN on the proposal.
    Returns a debate entry dict (always -- uses ABSTAIN on error).
    """
    persona = _AGENT_PERSONAS.get(agent_name, f"You are the {agent_name} agent.")
    prompt  = _DEBATE_PROMPT_TMPL.format(
        title           = paper.title,
        abstract        = paper.abstract[:600],
        proposed_change = proposed_change[:500],
        impacted_agents = ", ".join(impacted_agents) if impacted_agents else "unknown",
    )
    default = {
        "agent":      agent_name,
        "stance":     "ABSTAIN",
        "argument":   "Unable to evaluate (API error)",
        "confidence": 0,
        "key_concern": "",
        "timestamp":  datetime.now(timezone.utc).isoformat(),
    }
    if client is None:
        return default

    try:
        msg = client.messages.create(
            model      = HAIKU_MODEL,
            max_tokens = HAIKU_MAX_TOKENS,
            system     = persona,
            messages   = [{"role": "user", "content": prompt}],
        )
        raw    = msg.content[0].text.strip()
        parsed = _extract_json(raw)
        stance = str(parsed.get("stance", "ABSTAIN")).upper()
        if stance not in {"FOR", "AGAINST", "ABSTAIN"}:
            stance = "ABSTAIN"
        return {
            "agent":      agent_name,
            "stance":     stance,
            "argument":   str(parsed.get("argument", ""))[:300],
            "confidence": max(0, min(100, int(parsed.get("confidence", 50)))),
            "key_concern": str(parsed.get("key_concern", ""))[:200],
            "timestamp":  datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        log.warning("Debate [%s] failed for '%s': %s",
                    agent_name, paper.title[:40], exc)
        return default


def _run_debate(
    paper: ResearchPaper,
    proposed_change: str,
    impacted_agents: list,
    client,
) -> list[dict]:
    """
    Run all 8 agent personas through the debate.  Returns debate_log list.
    """
    log.info("  Running agent debate (%d agents)...", len(DEBATE_AGENTS))
    debate_log: list[dict] = []
    for agent_name in DEBATE_AGENTS:
        vote = _debate_one_agent(
            agent_name, paper, proposed_change, impacted_agents, client
        )
        debate_log.append(vote)
        log.info(
            "    [%s] %s  (confidence=%d)",
            agent_name, vote["stance"], vote["confidence"],
        )
        time.sleep(0.3)   # avoid bursting the Haiku rate limit
    return debate_log


# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------

def _url_already_processed(client, url: str) -> bool:
    """Return True if this URL is already in research_proposals."""
    if not client or not url:
        return False
    try:
        resp = (
            client.table("research_proposals")
            .select("id")
            .eq("url", url)
            .limit(1)
            .execute()
        )
        return bool(resp.data)
    except Exception as exc:
        log.debug("URL check failed: %s", exc)
        return False


def _save_proposal(
    client,
    paper:          ResearchPaper,
    proposal:       dict,
    debate_log:     list[dict],
    dry_run:        bool,
) -> Optional[str]:
    """
    Insert a research proposal into research_proposals.
    Returns the new row UUID or None.
    """
    row = {
        "title":           paper.title[:500],
        "source":          paper.source,
        "url":             paper.url,
        "relevance":       paper.relevance,
        "summary":         paper.abstract[:800],
        "proposed_change": proposal.get("proposed_change", ""),
        "impacted_agents": proposal.get("impacted_agents", []),
        "cost_impact":     proposal.get("cost_impact", "medium"),
        "debate_log":      debate_log,
        "status":          "pending",
        "created_at":      datetime.now(timezone.utc).isoformat(),
        # Extra metadata stored for richer dashboard display
        "metadata": {
            "authors":               paper.authors,
            "venue":                 paper.venue,
            "published":             paper.published,
            "expected_improvement":  proposal.get("expected_improvement", ""),
            "file_to_change":        proposal.get("file_to_change", ""),
            "implementation_effort_hours": proposal.get("implementation_effort_hours"),
            "requires_paid_data":    proposal.get("requires_paid_data", False),
            "relevance_reason":      paper.relevance_reason,
        },
    }

    # Print summary
    for_count     = sum(1 for d in debate_log if d["stance"] == "FOR")
    against_count = sum(1 for d in debate_log if d["stance"] == "AGAINST")
    abstain_count = sum(1 for d in debate_log if d["stance"] == "ABSTAIN")
    print(
        f"\n  [NEW PROPOSAL] relevance={paper.relevance}  "
        f"debate: FOR={for_count} AGAINST={against_count} ABSTAIN={abstain_count}\n"
        f"  Title: {paper.title[:80]}\n"
        f"  Change: {row['proposed_change'][:120]}...\n"
        f"  Impact: {row['cost_impact']}  agents={row['impacted_agents']}"
    )

    if dry_run:
        print("  [DRY RUN] Would save to research_proposals")
        return "dry-run-id"

    if not client:
        return None

    try:
        resp = client.table("research_proposals").insert(row).execute()
        new_id = (resp.data or [{}])[0].get("id")
        log.info("Saved proposal id=%s  '%s'", new_id, paper.title[:60])
        return new_id
    except Exception as exc:
        log.warning("research_proposals insert failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Proposal approval -- creates GitHub PR
# ---------------------------------------------------------------------------

def approve_proposal(proposal_id: str, dry_run: bool = False) -> dict:
    """
    Approve a research proposal and create a GitHub pull request.

    Steps:
      1. Load the proposal from Supabase
      2. Create a feature branch  research/<short-id>
      3. Commit a proposal summary doc  governance/proposals/<id>.md
      4. Open a PR via github_manager
      5. Update proposal status -> 'approved' + store pr_url

    Returns a result dict with pr_number and pr_url.
    """
    client = _supabase()
    if not client:
        return {"error": "Supabase unavailable", "proposal_id": proposal_id}

    # 1. Load proposal
    try:
        resp = (
            client.table("research_proposals")
            .select("*")
            .eq("id", proposal_id)
            .single()
            .execute()
        )
        proposal = resp.data
    except Exception as exc:
        return {"error": f"Proposal not found: {exc}", "proposal_id": proposal_id}

    if not proposal:
        return {"error": "Proposal not found", "proposal_id": proposal_id}

    title          = proposal.get("title", "Research Proposal")
    proposed_change = proposal.get("proposed_change", "")
    impacted       = proposal.get("impacted_agents", [])
    debate_log     = proposal.get("debate_log") or []
    metadata       = proposal.get("metadata") or {}

    # Tally debate votes
    for_count     = sum(1 for d in debate_log if d.get("stance") == "FOR")
    against_count = sum(1 for d in debate_log if d.get("stance") == "AGAINST")

    short_id   = str(proposal_id)[:8]
    branch     = f"research/{short_id}"
    doc_path   = f"governance/proposals/{proposal_id}.md"

    # Build proposal doc content
    debate_rows = "\n".join(
        f"| {d.get('agent','')} | {d.get('stance','')} | "
        f"{d.get('confidence',0)} | {d.get('argument','')[:80]} |"
        for d in debate_log
    )

    doc_content = f"""# Research Proposal: {title}

**Proposal ID**: `{proposal_id}`
**Source**: {proposal.get('source', 'unknown')}
**Relevance Score**: {proposal.get('relevance', 'N/A')}/100
**Status**: approved
**Created**: {proposal.get('created_at', '')}

## Paper

- **Title**: {title}
- **URL**: {proposal.get('url', 'N/A')}
- **Published**: {metadata.get('published', 'N/A')}
- **Authors**: {', '.join(metadata.get('authors') or [])}

## Abstract

{proposal.get('summary', '')}

## Proposed Change

{proposed_change}

**Impacted agents**: {', '.join(impacted)}
**File to change**: `{metadata.get('file_to_change', 'TBD')}`
**Cost impact**: {proposal.get('cost_impact', 'medium')}
**Est. effort**: {metadata.get('implementation_effort_hours', '?')} hours
**Requires paid data**: {metadata.get('requires_paid_data', False)}

## Expected Improvement

{metadata.get('expected_improvement', 'TBD')}

## Agent Debate ({for_count} FOR / {against_count} AGAINST)

| Agent | Stance | Confidence | Argument |
|-------|--------|------------|----------|
{debate_rows}

---
*Generated by `governance/research_agent.py` -- Bharat Intelligence*
"""

    pr_body = f"""## Research-Driven Improvement

**Paper**: [{title}]({proposal.get('url', '#')})
**Relevance**: {proposal.get('relevance', 'N/A')}/100
**Agent debate**: {for_count} FOR / {against_count} AGAINST

### Proposed Change
{proposed_change}

**Impacted agents**: {', '.join(impacted)}
**Est. effort**: {metadata.get('implementation_effort_hours', '?')} hours

### How to implement
See `{doc_path}` for full context.

---
*Auto-generated from research proposal `{proposal_id}` via `governance/research_agent.py`*
"""

    if dry_run:
        print(f"\n  [DRY RUN] Would create branch: {branch}")
        print(f"  [DRY RUN] Would commit: {doc_path}")
        print(f"  [DRY RUN] Would open PR: {title[:60]}")
        return {"dry_run": True, "branch": branch, "proposal_id": proposal_id}

    if GitHubManager is None:
        return {
            "error": "PyGithub not installed -- run: pip install PyGithub",
            "proposal_id": proposal_id,
        }

    try:
        mgr = GitHubManager()
        mgr.create_enhancement_branch(proposal_id, branch)
        mgr.commit_enhancement(
            branch_name    = branch,
            file_path      = doc_path,
            content        = doc_content,
            commit_message = f"research: add proposal doc for {short_id}",
        )
        pr_number = mgr.create_pull_request(
            branch = branch,
            title  = f"[Research] {title[:70]}",
            body   = pr_body,
        )
        pr_url = f"https://github.com/{os.getenv('GITHUB_REPO', '')}/pull/{pr_number}"

        # 5. Update Supabase
        client.table("research_proposals").update({
            "status": "approved",
            "pr_url": pr_url,
        }).eq("id", proposal_id).execute()

        log.info("Proposal %s approved -> PR #%d %s", proposal_id, pr_number, pr_url)
        return {
            "proposal_id": proposal_id,
            "pr_number":   pr_number,
            "pr_url":      pr_url,
            "branch":      branch,
        }
    except Exception as exc:
        log.error("approve_proposal failed for %s: %s", proposal_id, exc)
        return {"error": str(exc), "proposal_id": proposal_id}


# ---------------------------------------------------------------------------
# Dashboard data access
# ---------------------------------------------------------------------------

def list_proposals(
    status: Optional[str] = None,
    limit: int = 20,
    min_relevance: int = 0,
) -> list[dict]:
    """
    Return research proposals from Supabase, ordered by relevance desc.

    Used by the dashboard API and the CLI --list command.
    """
    client = _supabase()
    if not client:
        return []
    try:
        q = (
            client.table("research_proposals")
            .select("id, title, source, url, relevance, cost_impact, "
                    "impacted_agents, status, pr_url, created_at, debate_log")
            .order("relevance", desc=True)
            .limit(limit)
        )
        if status:
            q = q.eq("status", status)
        if min_relevance > 0:
            q = q.gte("relevance", min_relevance)
        resp = q.execute()
        rows = resp.data or []
        # Attach debate summary counts to each row
        for row in rows:
            dl = row.get("debate_log") or []
            row["debate_for"]     = sum(1 for d in dl if d.get("stance") == "FOR")
            row["debate_against"] = sum(1 for d in dl if d.get("stance") == "AGAINST")
            row["debate_abstain"] = sum(1 for d in dl if d.get("stance") == "ABSTAIN")
        return rows
    except Exception as exc:
        log.warning("list_proposals failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

def run(dry_run: bool = False) -> dict:
    """
    Daily research scanner pipeline.

    1. Gather papers from arXiv RSS, arXiv API, Semantic Scholar
    2. Deduplicate against Supabase research_proposals
    3. Score each paper's relevance via Haiku
    4. For relevance >= 75: generate proposal + run agent debate + save
    5. Send Telegram digest

    Returns a summary dict.
    """
    t0     = time.time()
    today  = date.today().isoformat()
    errors: list[str] = []

    log.info("Research agent: starting daily scan (dry_run=%s)", dry_run)

    client = _supabase()
    claude = _claude()

    if claude is None:
        log.warning(
            "No Claude client -- relevance scoring disabled; "
            "only gathering papers will run"
        )

    # ── Step 1: Gather papers ─────────────────────────────────────────────────
    papers = _gather_papers(days=DAYS_LOOKBACK, claude_client=claude)

    print()
    print("-" * 72)
    print(f"  Research Agent -- {today}   ({len(papers)} papers gathered)")
    print("-" * 72)

    # ── Step 2: Deduplicate against DB ────────────────────────────────────────
    new_papers = [
        p for p in papers
        if not _url_already_processed(client, p.url)
    ]
    log.info("%d papers after DB deduplication (skipped %d)",
             len(new_papers), len(papers) - len(new_papers))

    # ── Step 3-4: Score + process high-relevance papers ──────────────────────
    papers_relevant = 0
    proposals_saved = 0
    high_relevance_titles: list[str] = []

    for paper in new_papers:
        try:
            score = _score_relevance(paper, claude)

            if score < RELEVANCE_THRESHOLD:
                continue

            papers_relevant += 1
            high_relevance_titles.append(paper.title[:80])
            log.info("High-relevance paper (score=%d): %s", score, paper.title[:60])

            # Generate Sonnet proposal
            proposal = _generate_proposal(paper, claude)
            if proposal is None:
                proposal = {
                    "proposed_change": (
                        "Review this paper manually and identify applicable improvements."
                    ),
                    "impacted_agents":  [],
                    "cost_impact":      "medium",
                    "implementation_effort_hours": None,
                    "requires_paid_data": False,
                    "expected_improvement": "TBD after manual review",
                    "file_to_change": "TBD",
                }

            # Run agent debate
            impacted = proposal.get("impacted_agents") or []
            debate_log = _run_debate(paper, proposal["proposed_change"],
                                     impacted, claude)

            # Save to Supabase
            saved_id = _save_proposal(client, paper, proposal,
                                      debate_log, dry_run)
            if saved_id:
                proposals_saved += 1

        except Exception as exc:
            err = f"Paper '{paper.title[:40]}': {exc}"
            log.warning("Processing error -- %s", err)
            errors.append(err)

    print("-" * 72)
    print(f"  Scanned: {len(papers)}  New: {len(new_papers)}  "
          f"Relevant (>={RELEVANCE_THRESHOLD}): {papers_relevant}  "
          f"Saved: {proposals_saved}")
    print("-" * 72)
    print()

    # ── Step 5: Telegram digest ───────────────────────────────────────────────
    if proposals_saved > 0:
        titles_text = "\n".join(
            f"  {i+1}. {t}" for i, t in enumerate(high_relevance_titles[:5])
        )
        tg_msg = (
            f"<b>Research Agent Daily Digest</b>\n"
            f"Date: {today}\n"
            f"Papers scanned: {len(new_papers)} new\n"
            f"High-relevance proposals: <b>{proposals_saved}</b>\n\n"
            f"<b>New proposals:</b>\n{titles_text}\n\n"
            f"Review at ARIA dashboard -> Research tab"
        )
        _send_telegram(tg_msg, dry_run=dry_run)

    duration = round(time.time() - t0, 2)
    log.info(
        "Research agent done -- %d papers scanned, %d relevant, "
        "%d proposals saved in %.1fs",
        len(new_papers), papers_relevant, proposals_saved, duration,
    )

    return {
        "run_date":          today,
        "papers_gathered":   len(papers),
        "papers_new":        len(new_papers),
        "papers_relevant":   papers_relevant,
        "proposals_saved":   proposals_saved,
        "errors":            errors,
        "duration_seconds":  duration,
        "dry_run":           dry_run,
    }


# ---------------------------------------------------------------------------
# APScheduler + CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bharat Intelligence Research Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python governance/research_agent.py                       # start 07:30 IST daily scheduler
  python governance/research_agent.py --run-now             # run immediately
  python governance/research_agent.py --run-now --dry       # dry run, no DB writes
  python governance/research_agent.py --approve <id>        # create PR for proposal
  python governance/research_agent.py --list                # list pending proposals
  python governance/research_agent.py --list --status approved
        """,
    )
    parser.add_argument(
        "--run-now", action="store_true",
        help="Execute the daily scan immediately",
    )
    parser.add_argument(
        "--dry", action="store_true",
        help="Dry run: no DB writes, print what would happen",
    )
    parser.add_argument(
        "--approve", metavar="PROPOSAL_ID",
        help="Approve a proposal and create a GitHub PR",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List research proposals",
    )
    parser.add_argument(
        "--status", default=None,
        help="Filter --list by status (pending/approved/rejected/implemented)",
    )
    parser.add_argument(
        "--min-relevance", type=int, default=0, metavar="N",
        help="Filter --list by minimum relevance score",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level  = logging.INFO,
        format = "%(asctime)s [%(levelname)s] %(name)s -- %(message)s",
    )

    # ── Approve mode ─────────────────────────────────────────────────────────
    if args.approve:
        result = approve_proposal(args.approve, dry_run=args.dry)
        if "error" in result:
            log.error("Approval failed: %s", result["error"])
        else:
            pr_url = result.get("pr_url", "")
            log.info("PR created: %s", pr_url)
            print(f"\n  [OK] PR #{result.get('pr_number')} created: {pr_url}\n")
        return

    # ── List mode ─────────────────────────────────────────────────────────────
    if args.list:
        rows = list_proposals(
            status        = args.status,
            limit         = 50,
            min_relevance = args.min_relevance,
        )
        if not rows:
            print("  No proposals found.")
            return
        print(f"\n{'ID':<10} {'Rel':>4} {'Status':<14} {'For':>4} {'Against':>8}  Title")
        print("-" * 80)
        for r in rows:
            short_id = str(r.get("id", ""))[:8]
            print(
                f"  {short_id:<8}  {r.get('relevance', 0):>4}"
                f"  {r.get('status', ''):<14}"
                f"  {r.get('debate_for', 0):>2}F"
                f"  {r.get('debate_against', 0):>2}A"
                f"  {r.get('title', '')[:50]}"
            )
        print()
        return

    # ── Run now mode ──────────────────────────────────────────────────────────
    if args.run_now:
        result = run(dry_run=args.dry)
        log.info("Run result: %s", result)
        if result.get("errors"):
            log.warning(
                "%d error(s): %s",
                len(result["errors"]),
                "; ".join(result["errors"][:3]),
            )
        return

    # ── Scheduled mode: daily 07:30 IST ──────────────────────────────────────
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        log.error("apscheduler not installed -- run: pip install apscheduler")
        sys.exit(1)

    try:
        from zoneinfo import ZoneInfo
        IST = ZoneInfo("Asia/Kolkata")
    except ImportError:
        import pytz
        IST = pytz.timezone("Asia/Kolkata")

    def _job() -> None:
        log.info("Daily research scan triggered by scheduler...")
        run(dry_run=False)

    scheduler = BlockingScheduler(timezone=IST)
    scheduler.add_job(
        _job,
        CronTrigger(hour=7, minute=30, timezone=IST),
        id        = "daily_research_scan",
        name      = "Bharat Intelligence Research Agent",
        max_instances = 1,
        coalesce  = True,
    )

    log.info("-" * 60)
    log.info("  Bharat Intelligence Research Agent")
    log.info("  Schedule: daily at 07:30 IST")
    log.info("  Press Ctrl+C to stop")
    log.info("-" * 60)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped cleanly")


if __name__ == "__main__":
    main()
