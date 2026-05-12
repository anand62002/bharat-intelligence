"""
db/auto_seed_rag.py — Monthly RAG corpus auto-refresh
======================================================
Fetches recent India macro + market events from free sources (Google News RSS),
classifies them via OpenAI gpt-4o-mini (or a keyword fallback when no key),
generates text-embedding-3-small embeddings, and appends novel events to the
historical_events table in Supabase.

Designed to run monthly on the 1st at 08:15 IST via worker.py.

Sources
-------
  Google News RSS — 8 India macro/market query terms, last N days (default 35)

Classification
--------------
  Primary  : gpt-4o-mini structured JSON (when OPENAI_API_KEY is set)
  Fallback : keyword-rule classifier (always available, no API cost)

  Each article is scored for relevance before any LLM call is made.
  Only articles with relevance_score ≥ 0.6 AND is_significant=true are inserted.

Deduplication
-------------
  Fetches existing events from the last (days + 7) days.
  Skips any article whose event_type already has a DB entry within ±7 days.
  This prevents duplicate RBI / Budget / FII entries for the same real event.

Usage
-----
  python -m db.auto_seed_rag                    # dry-run: show what would be added
  python -m db.auto_seed_rag --run              # actually insert + embed
  python -m db.auto_seed_rag --run --days 60    # look back 60 days
  python -m db.auto_seed_rag --run --max 20     # cap at 20 new events
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ─── Model constants ──────────────────────────────────────────────────────────
_EMBED_MODEL       = "text-embedding-3-small"
_EMBED_DIM         = 1536
_CHAT_MODEL        = "gpt-4o-mini"
_RATE_LIMIT_DELAY  = 0.3   # seconds between OpenAI calls

# ─── Relevance pre-filter keywords ────────────────────────────────────────────
# Article must contain at least one of these (case-insensitive) to be considered
_RELEVANCE_KW: list[str] = [
    "rbi", "repo rate", "monetary policy", "cash reserve ratio", "crr",
    "fii", "dii", "foreign investor", "foreign institutional",
    "budget", "finance minister", "union budget", "fiscal deficit",
    "gst", "direct tax", "income tax", "capital gains",
    "inflation", "cpi", "wpi", "consumer price",
    "gdp", "gross domestic", "iip", "index of industrial",
    "trade deficit", "current account deficit", "balance of payment",
    "rupee", "inr", "currency depreciation", "currency crisis",
    "sebi", "circuit breaker", "market halt", "f&o ban",
    "nifty crash", "sensex fall", "market selloff", "market rally",
    "geopolit", "war", "ceasefire", "sanction", "tariff",
    "capital flight", "outflow", "inflow", "hot money",
    "rate cut", "rate hike", "interest rate",
    "disinvestment", "privatisation", "ipo ban",
    "nbfc", "banking crisis", "default", "yes bank", "il&fs",
    "global slowdown", "recession", "fed rate",
    "oil price", "crude oil", "opec",
    "covid", "pandemic", "lockdown",
]

# ─── Valid classification enums (constrain both LLM and keyword fallback) ─────
_VALID_EVENT_TYPES: set[str] = {
    "RBI_RATE_CHANGE", "RBI_POLICY_STATEMENT", "RBI_REGULATORY",
    "BUDGET", "GOVERNMENT_POLICY", "DISINVESTMENT",
    "FII_SELLOFF", "FII_BUYING", "DII_ACTION",
    "INFLATION_DATA", "GDP_DATA", "IIP_DATA", "TRADE_DATA",
    "CURRENCY_CRISIS", "CURRENCY_RALLY",
    "GEOPOLITICAL", "GLOBAL_RECESSION_FEAR", "US_FED_DECISION",
    "MARKET_CIRCUIT_BREAKER", "SEBI_REGULATION",
    "NBFC_CRISIS", "BANKING_SECTOR_STRESS", "CORPORATE_DEFAULT",
    "SECTOR_TAILWIND", "SECTOR_HEADWIND",
    "GLOBAL_COMMODITY_SHOCK", "OIL_PRICE_SHOCK",
    "PANDEMIC_EVENT", "NATURAL_DISASTER",
    "EARNINGS_SEASON_TREND",
}

_VALID_IMPACTS: set[str] = {
    "STRONG_POSITIVE", "MILD_POSITIVE", "LONG_TERM_POSITIVE",
    "NEUTRAL",
    "MODERATE_NEGATIVE", "SEVERE_NEGATIVE",
    "SECTOR_NEGATIVE", "SEVERE_SECTOR_DISRUPTION",
}

# ─── Google News RSS queries ───────────────────────────────────────────────────
_NEWS_QUERIES: list[str] = [
    "RBI monetary policy India repo rate decision",
    "India FII DII foreign investor flows market",
    "India budget Finance Minister economy fiscal",
    "India inflation CPI GDP macroeconomic data",
    "India Nifty Sensex market crash rally circuit breaker",
    "India SEBI regulation market halt F&O",
    "India rupee currency crisis depreciation",
    "India geopolitical trade tariff war sanctions economy",
]

# ─── LLM classification prompt ────────────────────────────────────────────────
_CLASSIFY_PROMPT = """\
You are an Indian equity market historian classifying news for a RAG system.

Headline: {title}
Snippet: {snippet}

Return ONLY a valid JSON object — no markdown, no explanation:
{{
  "event_type": "<from list>",
  "market_impact": "<from list>",
  "affected_sectors": ["<sector1>", "<sector2>"],
  "outcome": "<one sentence: actual or likely market outcome>",
  "relevance_score": <0.0-1.0>,
  "is_significant": <true|false>
}}

Valid event_types: {event_types}
Valid market_impacts: {market_impacts}

Rules:
- is_significant=true only for India-wide macro/regulatory/crisis events that move the broader market
- is_significant=false for routine company news, minor updates, analyst reports, or sector-specific micro events
- relevance_score > 0.7 for RBI decisions, budget, major FII flows, geopolitical shocks
- affected_sectors: use standard NSE sector names (Banking, IT, FMCG, Auto, Pharma, Metal, Oil & Gas, Real Estate, Telecom, Broad Market)
- outcome: describe what happened to equity markets, not the economic event itself
"""


# ═════════════════════════════════════════════════════════════════════════════
# Pure helper functions (testable without DB / network)
# ═════════════════════════════════════════════════════════════════════════════

def _is_relevant(title: str, snippet: str) -> bool:
    """Return True if title+snippet contains at least one relevance keyword."""
    text = f"{title} {snippet}".lower()
    return any(kw in text for kw in _RELEVANCE_KW)


def _parse_pub_date(pub_date_str: str) -> Optional[date]:
    """Parse an RSS pubDate string (RFC 2822 or partial) into a date object."""
    if not pub_date_str:
        return None
    # Standard RFC 2822 patterns
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%d %b %Y %H:%M:%S %z",
        "%d %b %Y %H:%M:%S %Z",
    ):
        try:
            return datetime.strptime(pub_date_str.strip(), fmt).date()
        except ValueError:
            pass
    # Fallback: extract "12 May 2026" anywhere in string
    m = re.search(r"(\d{1,2})\s+(\w{3,9})\s+(\d{4})", pub_date_str)
    if m:
        try:
            return datetime.strptime(
                f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %b %Y"
            ).date()
        except ValueError:
            pass
    return None


def _build_embedding_text(row: dict) -> str:
    """
    Construct the text to embed — mirrors backfill_embeddings.py and
    historical_rag.py so all vectors are semantically consistent.
    """
    parts: list[str] = []
    if row.get("event_type"):
        parts.append(f"Event type: {row['event_type']}")
    if row.get("description"):
        parts.append(row["description"])
    if row.get("market_impact"):
        parts.append(f"Market impact: {row['market_impact']}")
    if row.get("outcome"):
        parts.append(f"Outcome: {row['outcome']}")
    if row.get("affected_sectors"):
        secs = row["affected_sectors"]
        if isinstance(secs, list):
            secs = ", ".join(str(s) for s in secs)
        parts.append(f"Sectors: {secs}")
    return " | ".join(parts) if parts else (row.get("description") or "")


def _classify_keyword_fallback(title: str, snippet: str) -> dict:
    """
    Rule-based event classification — used when OPENAI_API_KEY is absent.
    Returns the same schema as the LLM classifier.
    """
    text = f"{title} {snippet}".lower()

    # ── Determine event_type ─────────────────────────────────────────────────
    event_type = "GOVERNMENT_POLICY"
    if any(k in text for k in ("rbi", "repo rate", "monetary policy", "crr", "slr", "cash reserve")):
        if any(k in text for k in ("cut", "reduc", "lower", "ease", "accommodat")):
            event_type = "RBI_RATE_CHANGE"
        elif any(k in text for k in ("hike", "rais", "increas", "tighten", "hawkish")):
            event_type = "RBI_RATE_CHANGE"
        elif any(k in text for k in ("circular", "guideline", "regulation", "direct")):
            event_type = "RBI_REGULATORY"
        else:
            event_type = "RBI_POLICY_STATEMENT"
    elif any(k in text for k in ("budget", "finance minister", "union budget", "fiscal")):
        event_type = "BUDGET"
    elif any(k in text for k in ("fii", "foreign investor", "foreign institutional")):
        if any(k in text for k in ("sell", "outflow", "withdraw", "pull", "exit")):
            event_type = "FII_SELLOFF"
        else:
            event_type = "FII_BUYING"
    elif any(k in text for k in ("dii", "domestic institution", "mutual fund buy")):
        event_type = "DII_ACTION"
    elif any(k in text for k in ("inflation", "cpi", "wpi", "consumer price")):
        event_type = "INFLATION_DATA"
    elif any(k in text for k in ("gdp", "gross domestic")):
        event_type = "GDP_DATA"
    elif any(k in text for k in ("iip", "index of industrial")):
        event_type = "IIP_DATA"
    elif any(k in text for k in ("trade deficit", "current account", "balance of payment")):
        event_type = "TRADE_DATA"
    elif any(k in text for k in ("oil", "crude", "opec", "brent")):
        event_type = "OIL_PRICE_SHOCK"
    elif any(k in text for k in ("war", "geopolit", "sanction", "tariff", "ceasefire", "conflict")):
        event_type = "GEOPOLITICAL"
    elif any(k in text for k in ("sebi", "regulation", "circular", "f&o ban", "circuit breaker")):
        event_type = "SEBI_REGULATION"
    elif any(k in text for k in ("rupee", "inr", "currency")):
        # Use word-boundary match for short words to avoid substring hits
        # e.g. "flows" contains "low", "falling" contains "fall"
        if re.search(r"\b(fall|fallen|fell|weak|low|depreciat|crash|slump|sink|plunge)\b", text):
            event_type = "CURRENCY_CRISIS"
        else:
            event_type = "CURRENCY_RALLY"
    elif any(k in text for k in ("fed rate", "us federal reserve", "fomc")):
        event_type = "US_FED_DECISION"
    elif any(k in text for k in ("nbfc", "yes bank", "il&fs", "shadow bank")):
        event_type = "NBFC_CRISIS"
    elif any(k in text for k in ("covid", "pandemic", "lockdown", "variant")):
        event_type = "PANDEMIC_EVENT"
    elif any(k in text for k in ("recession", "global slowdown", "global growth")):
        event_type = "GLOBAL_RECESSION_FEAR"
    elif any(k in text for k in ("circuit breaker", "market halt", "trading suspend")):
        event_type = "MARKET_CIRCUIT_BREAKER"

    # ── Determine market_impact ──────────────────────────────────────────────
    _pos_kw = ["rally", "surge", "gain", "jump", "rise", "positive", "boost",
               "cut rate", "rate cut", "stimulus", "recover", "high", "record",
               "strong", "growth", "bullish", "optimis"]
    _neg_kw = ["fall", "crash", "drop", "decline", "sell", "selloff", "negative",
               "weak", "crisis", "fear", "panic", "concern", "low", "plunge",
               "bearish", "pessimis", "outflow", "flight"]

    pos_count = sum(1 for k in _pos_kw if k in text)
    neg_count = sum(1 for k in _neg_kw if k in text)

    if pos_count >= 2 and pos_count > neg_count + 1:
        market_impact = "MILD_POSITIVE"
    elif neg_count >= 3 or (neg_count >= 2 and neg_count > pos_count + 1):
        market_impact = "MODERATE_NEGATIVE"
    elif neg_count >= 2:
        market_impact = "MODERATE_NEGATIVE"
    else:
        market_impact = "NEUTRAL"

    # Upgrade severity for extreme keywords
    if any(k in text for k in ("circuit breaker", "market halt", "crash", "panic", "crisis", "war")):
        if market_impact == "MODERATE_NEGATIVE":
            market_impact = "SEVERE_NEGATIVE"
        elif market_impact == "NEUTRAL":
            market_impact = "MODERATE_NEGATIVE"

    if any(k in text for k in ("rate cut", "stimulus", "record high", "strong gdp")):
        if market_impact in ("NEUTRAL", "MILD_POSITIVE"):
            market_impact = "MILD_POSITIVE"

    # ── Determine affected_sectors ────────────────────────────────────────────
    _sector_map: dict[str, list[str]] = {
        "Banking":      ["bank", "banking", "hdfc", "icici", "sbi", "kotak", "nbfc", "credit"],
        "IT":           ["it sector", "software", "tcs", "infosys", "wipro", "tech mahindra"],
        "FMCG":         ["fmcg", "consumer goods", "hul", "itc", "dabur", "nestle"],
        "Real Estate":  ["real estate", "realty", "housing", "dlf", "godrej property"],
        "Oil & Gas":    ["oil", "crude", "petrol", "ongc", "reliance industries", "bpcl"],
        "Pharma":       ["pharma", "drug", "medicine", "healthcare", "sun pharma"],
        "Auto":         ["auto", "automobile", "car", "vehicle", "maruti", "tata motor"],
        "Metal":        ["metal", "steel", "aluminium", "tata steel", "hindalco", "jsw"],
        "Telecom":      ["telecom", "jio", "airtel", "vodafone", "bsnl"],
        "Broad Market": ["nifty", "sensex", "market", "broad", "equity", "index"],
    }
    sectors: list[str] = []
    for sector, keywords in _sector_map.items():
        if any(k in text for k in keywords):
            sectors.append(sector)
    if not sectors:
        sectors = ["Broad Market"]

    return {
        "event_type":      event_type,
        "market_impact":   market_impact,
        "affected_sectors": sectors[:4],
        "outcome":         f"{title[:120]}",
        "relevance_score": 0.65,
        "is_significant":  True,
    }


def _deduplicate_articles(
    articles: list[dict],
    existing_events: list[dict],
    window_days: int = 7,
) -> list[dict]:
    """
    Remove articles whose event_type already has a DB entry within ±window_days.

    articles:       list of dicts with 'event_type' (str) and 'event_date' (date)
    existing_events: list of dicts with 'event_type' (str) and 'event_date' (str ISO)
    Returns the subset of articles that are genuinely novel.
    """
    # Build a lookup: event_type → list of existing dates
    existing_by_type: dict[str, list[date]] = {}
    for ev in existing_events:
        et = ev.get("event_type")
        ed_raw = ev.get("event_date")
        if not et or not ed_raw:
            continue
        try:
            ed = date.fromisoformat(str(ed_raw)[:10])
        except (ValueError, TypeError):
            continue
        existing_by_type.setdefault(et, []).append(ed)

    novel: list[dict] = []
    for art in articles:
        et  = art.get("event_type", "")
        ed  = art.get("event_date")
        if not isinstance(ed, date):
            novel.append(art)   # can't check without date — include it
            continue
        if et not in existing_by_type:
            novel.append(art)
            continue
        # Check if any existing entry of the same type falls within window
        too_close = any(
            abs((ed - existing_date).days) <= window_days
            for existing_date in existing_by_type[et]
        )
        if not too_close:
            novel.append(art)

    return novel


# ═════════════════════════════════════════════════════════════════════════════
# Network helpers
# ═════════════════════════════════════════════════════════════════════════════

def _fetch_google_news(query: str, days: int) -> list[dict]:
    """Fetch Google News RSS for *query* over the last *days* days."""
    encoded = quote_plus(query)
    url = (
        f"https://news.google.com/rss/search"
        f"?q={encoded}+when:{days}d&hl=en-IN&gl=IN&ceid=IN:en"
    )
    headers = {"User-Agent": "Mozilla/5.0 BharatIntelligence/1.0"}
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=15) as resp:
            content = resp.read()
        root = ET.fromstring(content)
        items: list[dict] = []
        for item in root.findall(".//item"):
            title    = (item.findtext("title") or "").strip()
            link     = (item.findtext("link")  or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            desc     = re.sub(r"<[^>]+>", "", item.findtext("description") or "").strip()
            if title:
                items.append({
                    "title":    title,
                    "link":     link,
                    "pub_date": pub_date,
                    "snippet":  desc[:400],
                })
        return items
    except Exception as exc:
        log.warning("Google News RSS failed for '%s…': %s", query[:40], exc)
        return []


def _fetch_all_articles(queries: list[str], days: int) -> list[dict]:
    """Fetch + deduplicate articles across all queries by title."""
    seen_titles: set[str] = set()
    all_items: list[dict] = []
    for query in queries:
        items = _fetch_google_news(query, days)
        for item in items:
            key = item["title"].lower()[:80]
            if key not in seen_titles:
                seen_titles.add(key)
                all_items.append(item)
        time.sleep(0.5)   # be polite to Google
    log.info("Fetched %d unique articles across %d queries", len(all_items), len(queries))
    return all_items


# ─── OpenAI helpers ───────────────────────────────────────────────────────────

def _classify_llm(title: str, snippet: str, api_key: str) -> Optional[dict]:
    """Classify article using gpt-4o-mini. Returns dict or None on failure."""
    prompt = _CLASSIFY_PROMPT.format(
        title=title[:200],
        snippet=snippet[:400],
        event_types=", ".join(sorted(_VALID_EVENT_TYPES)),
        market_impacts=", ".join(sorted(_VALID_IMPACTS)),
    )
    payload = json.dumps({
        "model": _CHAT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 256,
    }).encode()
    req = Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
        raw = data["choices"][0]["message"]["content"].strip()
        # Strip markdown code fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        result = json.loads(raw)
        # Validate / sanitise
        if result.get("event_type") not in _VALID_EVENT_TYPES:
            result["event_type"] = "GOVERNMENT_POLICY"
        if result.get("market_impact") not in _VALID_IMPACTS:
            result["market_impact"] = "NEUTRAL"
        if not isinstance(result.get("affected_sectors"), list):
            result["affected_sectors"] = ["Broad Market"]
        result["relevance_score"]  = float(result.get("relevance_score", 0.5))
        result["is_significant"]   = bool(result.get("is_significant", False))
        return result
    except (HTTPError, URLError) as exc:
        log.warning("OpenAI chat HTTP error: %s", exc)
    except (KeyError, json.JSONDecodeError, ValueError) as exc:
        log.warning("OpenAI chat parse error: %s", exc)
    return None


def _embed_openai(text: str, api_key: str) -> Optional[list[float]]:
    """Generate 1536-dim embedding. Returns None on failure."""
    payload = json.dumps({
        "model": _EMBED_MODEL,
        "input": text[:8000],
    }).encode()
    req = Request(
        "https://api.openai.com/v1/embeddings",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
        vec = data["data"][0]["embedding"]
        if len(vec) != _EMBED_DIM:
            log.warning("Unexpected embedding dim %d (expected %d)", len(vec), _EMBED_DIM)
        return vec
    except (HTTPError, URLError) as exc:
        log.warning("OpenAI embed HTTP error: %s", exc)
    except (KeyError, json.JSONDecodeError, ValueError) as exc:
        log.warning("OpenAI embed parse error: %s", exc)
    return None


# ─── Supabase helpers ─────────────────────────────────────────────────────────

def _get_supabase_client():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
    from supabase import create_client
    return create_client(url, key)


def _fetch_recent_events(client, days: int) -> list[dict]:
    """Fetch event_type + event_date for the last (days+7) days for dedup."""
    cutoff = (date.today() - timedelta(days=days + 7)).isoformat()
    try:
        resp = (
            client.table("historical_events")
            .select("event_type, event_date")
            .gte("event_date", cutoff)
            .execute()
        )
        return resp.data or []
    except Exception as exc:
        log.warning("Could not fetch recent events for dedup: %s", exc)
        return []


def _insert_event(client, row: dict) -> bool:
    """Insert one event row. Returns True on success."""
    try:
        client.table("historical_events").insert(row).execute()
        return True
    except Exception as exc:
        log.error("Insert failed for '%s': %s", row.get("description", "?")[:60], exc)
        return False


# ═════════════════════════════════════════════════════════════════════════════
# Main pipeline
# ═════════════════════════════════════════════════════════════════════════════

def run(
    days:    int  = 35,
    max_new: int  = 30,
    dry_run: bool = False,
) -> dict:
    """
    Main entry point called from worker.py.

    Returns
    -------
    dict with keys:
        added               int  — rows inserted (0 when dry_run=True)
        skipped_duplicate   int  — articles already in DB within window
        skipped_irrelevant  int  — filtered out by keyword/LLM relevance
        errors              int  — insert / API failures
        dry_run             bool
        articles_checked    int  — total unique articles fetched
    """
    stats = {
        "added":              0,
        "skipped_duplicate":  0,
        "skipped_irrelevant": 0,
        "errors":             0,
        "dry_run":            dry_run,
        "articles_checked":   0,
    }

    openai_key = os.environ.get("OPENAI_API_KEY")
    if not openai_key:
        log.warning(
            "OPENAI_API_KEY not set — using keyword classifier (no embeddings). "
            "Events inserted without embeddings will be backfilled by db/backfill_embeddings.py."
        )

    # ── 1. Fetch articles ─────────────────────────────────────────────────────
    articles = _fetch_all_articles(_NEWS_QUERIES, days)
    stats["articles_checked"] = len(articles)
    if not articles:
        log.info("No articles fetched. Nothing to do.")
        return stats

    # ── 2. Keyword relevance pre-filter ───────────────────────────────────────
    relevant = [a for a in articles if _is_relevant(a["title"], a["snippet"])]
    stats["skipped_irrelevant"] += len(articles) - len(relevant)
    log.info(
        "Relevance filter: %d / %d articles passed",
        len(relevant), len(articles),
    )
    if not relevant:
        return stats

    # ── 3. Parse dates ────────────────────────────────────────────────────────
    dated: list[dict] = []
    cutoff_date = date.today() - timedelta(days=days)
    for art in relevant:
        art["event_date"] = _parse_pub_date(art.get("pub_date", "")) or date.today()
        if art["event_date"] >= cutoff_date:
            dated.append(art)
    log.info("Date filter: %d articles within last %d days", len(dated), days)

    # ── 4. Classify (LLM preferred, keyword fallback) ─────────────────────────
    classified: list[dict] = []
    for art in dated:
        if openai_key:
            clf = _classify_llm(art["title"], art["snippet"], openai_key)
            time.sleep(_RATE_LIMIT_DELAY)
            if clf is None:
                clf = _classify_keyword_fallback(art["title"], art["snippet"])
        else:
            clf = _classify_keyword_fallback(art["title"], art["snippet"])

        if not clf.get("is_significant") or clf.get("relevance_score", 0) < 0.5:
            stats["skipped_irrelevant"] += 1
            log.debug("Not significant: '%s'", art["title"][:60])
            continue

        art.update(clf)
        classified.append(art)

    log.info("%d articles classified as significant", len(classified))
    if not classified:
        return stats

    # ── 5. Deduplicate against DB ─────────────────────────────────────────────
    try:
        db = _get_supabase_client()
        existing = _fetch_recent_events(db, days)
    except RuntimeError as exc:
        log.error("Supabase not configured: %s", exc)
        stats["errors"] += 1
        return stats

    novel = _deduplicate_articles(classified, existing, window_days=7)
    stats["skipped_duplicate"] += len(classified) - len(novel)
    log.info(
        "Deduplication: %d novel / %d classified (%d duplicates skipped)",
        len(novel), len(classified), stats["skipped_duplicate"],
    )

    # ── 6. Cap at max_new ─────────────────────────────────────────────────────
    if len(novel) > max_new:
        log.info("Capping at %d events (max_new=%d)", max_new, max_new)
        novel = novel[:max_new]

    # ── 7. Insert events ──────────────────────────────────────────────────────
    for art in novel:
        description = art["title"]
        if art.get("snippet"):
            description = f"{art['title']}. {art['snippet'][:200]}"

        row = {
            "event_type":      art["event_type"],
            "description":     description[:500],
            "event_date":      art["event_date"].isoformat(),
            "market_impact":   art["market_impact"],
            "outcome":         art.get("outcome", art["title"])[:300],
            "affected_sectors": art.get("affected_sectors", ["Broad Market"]),
            "relevance_score": art.get("relevance_score", 0.6),
            "embedding":       None,
        }

        # Generate embedding
        if openai_key:
            embed_text = _build_embedding_text(row)
            embedding  = _embed_openai(embed_text, openai_key)
            if embedding:
                row["embedding"] = embedding
            time.sleep(_RATE_LIMIT_DELAY)

        if dry_run:
            log.info(
                "[DRY RUN] Would insert: [%s] %s | impact=%s | sectors=%s",
                row["event_type"],
                row["description"][:70],
                row["market_impact"],
                row["affected_sectors"],
            )
            stats["added"] += 1   # count as "would add" in dry-run
            continue

        ok = _insert_event(db, row)
        if ok:
            stats["added"] += 1
            log.info(
                "Inserted: [%s] %s | impact=%s",
                row["event_type"], row["description"][:70], row["market_impact"],
            )
        else:
            stats["errors"] += 1

    log.info(
        "RAG auto-refresh complete — added=%d skipped_dup=%d skipped_irrel=%d errors=%d dry_run=%s",
        stats["added"], stats["skipped_duplicate"],
        stats["skipped_irrelevant"], stats["errors"], dry_run,
    )
    return stats


# ═════════════════════════════════════════════════════════════════════════════
# CLI entry point
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Monthly RAG corpus auto-refresh — fetches and inserts new market events"
    )
    parser.add_argument(
        "--run",    action="store_true",
        help="Actually insert events (default: dry-run only)",
    )
    parser.add_argument(
        "--days",   type=int, default=35,
        help="Look back N days for articles (default: 35)",
    )
    parser.add_argument(
        "--max",    type=int, default=30,
        help="Max new events to insert per run (default: 30)",
    )
    args = parser.parse_args()

    result = run(days=args.days, max_new=args.max, dry_run=not args.run)

    print("\n── RAG Auto-Refresh Summary ──────────────────────────")
    print(f"  Articles checked  : {result['articles_checked']}")
    print(f"  Skipped irrelevant: {result['skipped_irrelevant']}")
    print(f"  Skipped duplicate : {result['skipped_duplicate']}")
    print(f"  {'Would add' if result['dry_run'] else 'Added'}        : {result['added']}")
    print(f"  Errors            : {result['errors']}")
    if result["dry_run"]:
        print("\n  ↳ Dry run — re-run with --run to actually insert")
    print("─────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()
