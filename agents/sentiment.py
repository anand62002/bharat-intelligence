"""
agents/sentiment.py — Sentiment Analysis Agent
Fetches last 48h headlines from RSS + NewsAPI, scores each with Claude Haiku,
detects misinformation patterns, and computes a rolling sentiment signal.

Entry point: analyse(symbol) -> dict
"""

import hashlib
import json
import logging
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from dotenv import load_dotenv

load_dotenv()

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import math

from data.fetchers import get_rss_headlines, get_nse_fii_dii, get_bse_announcements  # noqa: E402
from agents.base import DataCompletenessValidator, insufficient_data_result
from data.insider_signal import get_promoter_signal  # noqa: E402  # P3-C-P5

_dcv = DataCompletenessValidator()

log = logging.getLogger(__name__)
AGENT_NAME = "sentiment"

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

MAX_HAIKU_CALLS = 10          # hard cap per agent run (cost guard)
HAIKU_MODEL     = "claude-haiku-4-5-20251001"

# Sources treated as authoritative for regulatory-action danger flag
AUTHORITATIVE_SOURCES = {
    "economic times", "et markets", "business standard", "moneycontrol",
    "livemint", "mint", "hindu business line", "cnbc tv18", "reuters", "bloomberg",
}

# Regex patterns that indicate regulatory / FII danger
_REGULATORY_PATTERNS = re.compile(
    r"sebi|rbi|ed |enforcement|fraud|cbi|notice|penalty|ban|suspension"
    r"|adjudication|arrest|raid|probe|investigation|default",
    re.IGNORECASE,
)
_FII_SELLING_PATTERNS = re.compile(
    r"fii.*sell|fpi.*sell|foreign.*outflow|sell.*fii|sell.*fpi"
    r"|institutional.*selling|heavy.*selling",
    re.IGNORECASE,
)

# ── D-2: Event class taxonomy (Janus-Q inspired) ─────────────────────────────
EVENT_CLASSES = [
    "EARNINGS_SURPRISE",  # beat/miss vs consensus
    "REGULATORY_SHOCK",   # SEBI order, RBI circular, ED/IT raid, court order
    "M_A_SIGNAL",         # acquisition, merger, stake sale, delisting
    "MACRO_CATALYST",     # budget, rate decision, PMI, CPI, GDP
    "ANALYST_ACTION",     # upgrade, downgrade, target change
    "MANAGEMENT_SIGNAL",  # concall guidance, promoter buy/sell, AGM outcome
    "SECTOR_CATALYST",    # PLI scheme, import duty, price hike, industry regulation
    "ROUTINE",            # in-line earnings, dividend, record date, AGM date notice
]

# D-2: Amplification multipliers — event impact ×N on the raw sentiment score delta
_EVENT_MULTIPLIERS: dict[str, float] = {
    "EARNINGS_SURPRISE": 2.5,
    "REGULATORY_SHOCK":  3.0,
    "M_A_SIGNAL":        2.0,
    "MACRO_CATALYST":    1.5,
    "ANALYST_ACTION":    1.5,
    "MANAGEMENT_SIGNAL": 1.8,
    "SECTOR_CATALYST":   1.5,
    "ROUTINE":           0.5,
    "UNKNOWN":           1.0,
}

# D-3: Temporal decay half-lives in hours
# exp(-ln(2)/half_life × age_hours) → 1.0 when fresh, 0.5 at half-life
_HALF_LIVES_HOURS: dict[str, float] = {
    "EARNINGS_SURPRISE": 6.0,
    "REGULATORY_SHOCK":  48.0,
    "M_A_SIGNAL":        24.0,
    "MACRO_CATALYST":    12.0,
    "ANALYST_ACTION":    8.0,
    "MANAGEMENT_SIGNAL": 18.0,
    "SECTOR_CATALYST":   12.0,
    "ROUTINE":           2.0,
    "UNKNOWN":           6.0,
}

# D-4: HuggingFace Inference API endpoint for FinBERT
# Free-tier, no API key needed; rate-limited to ~10 req/s
_HF_FINBERT_URL = "https://api-inference.huggingface.co/models/ProsusAI/finbert"
_FINBERT_ENSEMBLE_W = 0.6   # weight of FinBERT in ensemble (1 - w = Claude Haiku)

# D-2: Batch classification prompt
_BATCH_CLASSIFY_PROMPT = """\
You are a financial news event classifier for Indian equity markets.
Classify EACH headline into exactly ONE category from this list:
EARNINGS_SURPRISE, REGULATORY_SHOCK, M_A_SIGNAL, MACRO_CATALYST,
ANALYST_ACTION, MANAGEMENT_SIGNAL, SECTOR_CATALYST, ROUTINE, UNKNOWN

Also score each headline sentiment 0-100 (100=most bullish) for {symbol}.

Return ONLY a JSON array (no markdown, no text outside the array):
[
  {{"idx": 0, "event_class": "CATEGORY", "score": <int 0-100>, "key_claim": "<5 words>"}},
  ...
]

Headlines to classify:
{headlines}
"""


# Haiku scoring prompt template
_SCORE_PROMPT = (
    "Rate this headline for {symbol} as bullish/bearish/neutral. "
    "Score 0-100 where 100=most bullish. "
    "Return JSON only (no markdown, no explanation): "
    '{{\"sentiment\": \"bullish|bearish|neutral\", \"score\": <int 0-100>, '
    '\"key_claim\": \"<5-10 word summary>\"}}\n\n'
    "Headline: {headline}"
)

# ──────────────────────────────────────────────────────────────────────────────
# NewsAPI fetcher
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_newsapi(symbol: str, hours: int = 48) -> list[dict]:
    """
    Pull up to 20 articles from NewsAPI for the given symbol.

    Returns list of {title, source, published, url} — same schema as get_rss_headlines.
    Returns [] on any failure (non-fatal).
    """
    api_key = os.environ.get("NEWSAPI_KEY") or os.environ.get("NEWS_API_KEY")
    if not api_key:
        log.debug("NEWSAPI_KEY not set — skipping NewsAPI fetch")
        return []

    clean = symbol.replace(".NS", "").replace(".BO", "").strip()
    from_dt = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    params = urlencode({
        "q": clean,
        "from": from_dt,
        "sortBy": "publishedAt",
        "language": "en",
        "pageSize": 20,
        "apiKey": api_key,
    })
    url = f"https://newsapi.org/v2/everything?{params}"

    try:
        req = Request(url, headers={"User-Agent": "BharatIntelligence/1.0"})
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except (URLError, HTTPError, json.JSONDecodeError) as exc:
        log.warning("NewsAPI fetch failed: %s", exc)
        return []

    articles = data.get("articles") or []
    results = []
    for art in articles:
        src = (art.get("source") or {}).get("name") or "NewsAPI"
        title = (art.get("title") or "").strip()
        if not title or title == "[Removed]":
            continue
        results.append({
            "title": title,
            "source": src,
            "published": (art.get("publishedAt") or "")[:10],
            "url": art.get("url") or "",
        })
    return results


# ──────────────────────────────────────────────────────────────────────────────
# Claude Haiku scoring
# ──────────────────────────────────────────────────────────────────────────────

def _call_haiku(headline: str, symbol: str) -> dict:
    """
    Score a single headline with Claude Haiku.

    Returns {sentiment, score, key_claim} or a fallback on error.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    prompt = _SCORE_PROMPT.format(symbol=symbol, headline=headline[:500])
    payload = json.dumps({
        "model": HAIKU_MODEL,
        "max_tokens": 120,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode())
    except HTTPError as exc:
        raise RuntimeError(f"Haiku API error {exc.code}: {exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Haiku network error: {exc.reason}") from exc

    raw_text = body["content"][0]["text"].strip()
    # Strip accidental markdown fences
    raw_text = re.sub(r"^```[a-z]*\n?", "", raw_text)
    raw_text = re.sub(r"\n?```$", "", raw_text).strip()

    parsed = json.loads(raw_text)
    return {
        "sentiment": str(parsed.get("sentiment", "neutral")).lower(),
        "score":     max(0, min(100, int(parsed.get("score", 50)))),
        "key_claim": str(parsed.get("key_claim", ""))[:120],
    }


def _fallback_score(headline: str) -> dict:
    """
    Keyword-based fallback when Haiku is unavailable or rate-limit reached.
    Covers the most common bullish/bearish signal words in Indian financial news.
    """
    h = headline.lower()

    bullish_words = [
        "profit", "beat", "surge", "rally", "upgrade", "buy", "strong",
        "growth", "record", "high", "positive", "outperform", "raises",
        "dividend", "acquisition", "expansion", "order", "win",
    ]
    bearish_words = [
        "loss", "miss", "fall", "crash", "sell", "weak", "decline", "default",
        "fraud", "penalty", "sebi", "ban", "suspension", "probe", "raid",
        "downgrade", "cut", "lower", "warning", "risk", "layoff", "debt",
    ]

    bull = sum(1 for w in bullish_words if w in h)
    bear = sum(1 for w in bearish_words if w in h)
    net = bull - bear

    if net >= 2:
        sentiment, score = "bullish", min(80, 55 + net * 6)
    elif net <= -2:
        sentiment, score = "bearish", max(20, 45 + net * 6)
    else:
        sentiment, score = "neutral", 50

    return {"sentiment": sentiment, "score": score, "key_claim": "", "fallback": True}


# ──────────────────────────────────────────────────────────────────────────────
# D-2: Batch event classifier (Janus-Q pattern)
# ──────────────────────────────────────────────────────────────────────────────

def _batch_classify_headlines(headlines: list[dict], symbol: str) -> list[dict]:
    """
    Classify all headlines in a single Claude Haiku call (D-2).

    Replaces the per-headline loop + MAX_HAIKU_CALLS cap.  One prompt covers
    all headlines, returning event_class + sentiment score for each.

    Returns a list parallel to `headlines` (same order), each element with:
      event_class : str   — one of EVENT_CLASSES or "UNKNOWN"
      score       : int   — 0-100 bullishness for this symbol
      key_claim   : str   — 5-word summary
      fallback    : bool  — True if Haiku failed / not configured

    Falls back gracefully to keyword scoring if Haiku unavailable.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or not headlines:
        return [_fallback_score(h.get("title", "")) for h in headlines]

    # Build numbered headline list for the prompt
    lines = "\n".join(
        f'{i}. {h.get("title", "")[:200]}'
        for i, h in enumerate(headlines)
    )
    prompt = _BATCH_CLASSIFY_PROMPT.format(symbol=symbol, headlines=lines)

    payload = json.dumps({
        "model": HAIKU_MODEL,
        "max_tokens": max(200, len(headlines) * 60),
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode())
        raw_text = body["content"][0]["text"].strip()
        raw_text = re.sub(r"^```[a-z]*\n?", "", raw_text)
        raw_text = re.sub(r"\n?```$", "", raw_text).strip()
        items = json.loads(raw_text)
    except Exception as exc:
        log.warning("_batch_classify_headlines failed (%s) — using keyword fallback", exc)
        return [_fallback_score(h.get("title", "")) for h in headlines]

    # Build index → result map, fill gaps with fallback
    results: list[dict] = []
    result_map = {}
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and "idx" in item:
                result_map[int(item["idx"])] = item

    valid_classes = set(EVENT_CLASSES) | {"UNKNOWN"}
    for i, h in enumerate(headlines):
        item = result_map.get(i)
        if item:
            ec = str(item.get("event_class", "UNKNOWN")).upper()
            if ec not in valid_classes:
                ec = "UNKNOWN"
            results.append({
                "event_class": ec,
                "score":       max(0, min(100, int(item.get("score", 50)))),
                "key_claim":   str(item.get("key_claim", ""))[:120],
                "sentiment":   "bullish" if int(item.get("score", 50)) >= 60
                               else ("bearish" if int(item.get("score", 50)) <= 40 else "neutral"),
                "fallback":    False,
            })
        else:
            fb = _fallback_score(h.get("title", ""))
            fb["event_class"] = "UNKNOWN"
            results.append(fb)

    return results


# ──────────────────────────────────────────────────────────────────────────────
# D-3: Temporal decay
# ──────────────────────────────────────────────────────────────────────────────

def _temporal_weight(headline: dict, event_class: str = "UNKNOWN") -> float:
    """
    Compute exponential temporal decay weight for a headline (D-3).

    weight = exp(-ln(2) / half_life × age_hours)

    Published date ("YYYY-MM-DD" or "YYYY-MM-DD HH:MM") is used to compute age.
    Returns 1.0 (full weight) when publication time is unknown.
    Returns a value in (0, 1].
    """
    pub_str = headline.get("published") or ""
    if not pub_str:
        return 1.0

    try:
        # Accept "YYYY-MM-DD" or "YYYY-MM-DD HH:MM"
        if len(pub_str) >= 16:
            pub_dt = datetime.strptime(pub_str[:16], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        else:
            pub_dt = datetime.strptime(pub_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return 1.0

    age_hours = (datetime.now(timezone.utc) - pub_dt).total_seconds() / 3600.0
    if age_hours < 0:
        age_hours = 0.0

    half_life = _HALF_LIVES_HOURS.get(event_class, _HALF_LIVES_HOURS["UNKNOWN"])
    lam = math.log(2) / half_life
    return math.exp(-lam * age_hours)


# ──────────────────────────────────────────────────────────────────────────────
# D-4: FinBERT via HuggingFace Inference API
# ──────────────────────────────────────────────────────────────────────────────

def _call_finbert_hf(headline: str) -> Optional[dict]:
    """
    Score a headline using ProsusAI/finbert via the HuggingFace Inference API (D-4).

    Returns {positive: float, negative: float, neutral: float} in [0,1],
    or None on failure (rate-limit, network error, etc.).

    HF free tier allows ~10 req/s; we call this once per batch (not per headline).
    """
    hf_token = os.environ.get("HF_API_TOKEN", "")   # optional — works without token on free tier
    headers = {"Content-Type": "application/json"}
    if hf_token:
        headers["Authorization"] = f"Bearer {hf_token}"

    payload = json.dumps({"inputs": headline[:512]}).encode()
    req = Request(
        _HF_FINBERT_URL,
        data=payload,
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(req, timeout=8) as resp:
            body = json.loads(resp.read().decode())
    except Exception as exc:
        log.debug("_call_finbert_hf: request failed: %s", exc)
        return None

    # HF response: [[{"label":"positive","score":0.8}, {"label":"negative","score":0.1}, ...]]
    scores: dict[str, float] = {}
    if isinstance(body, list) and body:
        inner = body[0]
        if isinstance(inner, list):
            for item in inner:
                if isinstance(item, dict) and "label" in item:
                    scores[item["label"].lower()] = float(item.get("score", 0))
        elif isinstance(inner, dict):
            scores[inner.get("label", "").lower()] = float(inner.get("score", 0))

    if not scores:
        return None

    return {
        "positive": scores.get("positive", 0.0),
        "negative": scores.get("negative", 0.0),
        "neutral":  scores.get("neutral",  0.0),
    }


def _finbert_to_score(finbert: dict) -> int:
    """Convert FinBERT {positive, negative, neutral} to 0-100 bullishness score."""
    # Map positive→100, negative→0, neutral→50; weighted by probability
    raw = (
        finbert.get("positive", 0) * 100
        + finbert.get("neutral",  0) * 50
        + finbert.get("negative", 0) * 0
    )
    return max(0, min(100, round(raw)))


# ──────────────────────────────────────────────────────────────────────────────
# Misinformation / coordinated-campaign detection
# ──────────────────────────────────────────────────────────────────────────────

def _extract_domain(url: str) -> str:
    """Return bare domain from a URL string, e.g. 'economictimes.indiatimes.com'."""
    url = url.lower().replace("https://", "").replace("http://", "").replace("www.", "")
    return url.split("/")[0].split("?")[0]


def _phrase_fingerprint(title: str) -> str:
    """
    Normalised, stop-word-stripped fingerprint of a headline.
    Used to cluster near-identical titles across sources.
    """
    stop = {
        "a", "an", "the", "and", "or", "of", "in", "on", "at", "to",
        "for", "is", "are", "was", "were", "its", "it", "by", "with",
        "as", "that", "this", "from",
    }
    tokens = re.sub(r"[^a-z0-9 ]", "", title.lower()).split()
    key_tokens = [t for t in tokens if t not in stop and len(t) > 2]
    # Bi-gram + unigram hash of first 8 key tokens
    sig = " ".join(key_tokens[:8])
    return hashlib.md5(sig.encode()).hexdigest()[:12]


def _detect_misinformation(headlines: list[dict]) -> list[dict]:
    """
    Flag coordinated-amplification if 3+ articles from the **same domain**
    share an identical phrase fingerprint within the batch.

    Returns list of {domain, count, sample_title, fingerprint} flags.
    """
    # domain → fingerprint → [titles]
    domain_fp: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))

    for h in headlines:
        domain = _extract_domain(h.get("url", "") or h.get("source", "unknown"))
        fp = _phrase_fingerprint(h.get("title", ""))
        domain_fp[domain][fp].append(h.get("title", ""))

    flags = []
    for domain, fp_map in domain_fp.items():
        for fp, titles in fp_map.items():
            if len(titles) >= 3:
                flags.append({
                    "domain": domain,
                    "count": len(titles),
                    "sample_title": titles[0],
                    "fingerprint": fp,
                    "flag": "coordinated_amplification",
                })
    return flags


# ──────────────────────────────────────────────────────────────────────────────
# Rolling 7-day trend (within the fetched batch)
# ──────────────────────────────────────────────────────────────────────────────

def _rolling_trend(scored: list[dict]) -> dict:
    """
    Buckets scored headlines by published date and computes daily avg sentiment.
    Returns {dates: [...], avg_scores: [...], direction: 'IMPROVING'|'DECLINING'|'STABLE'}.
    """
    by_date: dict[str, list[int]] = defaultdict(list)
    for item in scored:
        pub = str(item.get("published", ""))[:10]   # YYYY-MM-DD
        if pub:
            by_date[pub].append(item["score"])

    sorted_dates = sorted(by_date.keys())[-7:]      # last 7 unique days
    avg_scores = [
        round(sum(by_date[d]) / len(by_date[d]), 1) for d in sorted_dates
    ]

    direction = "STABLE"
    if len(avg_scores) >= 2:
        # Split into earlier half vs later half so 2-date case works correctly
        mid     = max(1, len(avg_scores) // 2)
        earlier = sum(avg_scores[:mid]) / mid
        recent  = sum(avg_scores[mid:]) / len(avg_scores[mid:])
        delta   = recent - earlier
        if delta >= 5:
            direction = "IMPROVING"
        elif delta <= -5:
            direction = "DECLINING"

    return {"dates": sorted_dates, "avg_scores": avg_scores, "direction": direction}


# ──────────────────────────────────────────────────────────────────────────────
# Danger signal detection
# ──────────────────────────────────────────────────────────────────────────────

def _detect_danger_signals(
    headlines: list[dict],
    scored: list[dict],
    fii_net: Optional[float],
) -> list[dict]:
    """
    CRITICAL DANGER flag:
      5+ authoritative sources reporting regulatory action AND FII net selling
      in the same window.

    Returns list of danger signal dicts.
    """
    signals: list[dict] = []

    # Map url/source → scored item for quick lookup
    title_to_score = {s.get("title", ""): s for s in scored}

    reg_hits: list[dict] = []
    for h in headlines:
        src = (h.get("source") or "").lower()
        title = h.get("title", "")
        is_auth = any(auth in src for auth in AUTHORITATIVE_SOURCES)
        is_reg = bool(_REGULATORY_PATTERNS.search(title))
        if is_auth and is_reg:
            scored_item = title_to_score.get(title, {})
            reg_hits.append({
                "source": h["source"],
                "title": title,
                "sentiment_score": scored_item.get("score", 50),
            })

    fii_selling = fii_net is not None and fii_net < 0

    if len(reg_hits) >= 5 and fii_selling:
        signals.append({
            "type": "CRITICAL",
            "label": "regulatory_action_with_fii_outflow",
            "authoritative_count": len(reg_hits),
            "fii_net_crores": fii_net,
            "sample_headlines": [r["title"] for r in reg_hits[:3]],
            "description": (
                f"{len(reg_hits)} authoritative sources reporting regulatory action "
                f"with FII net selling ₹{abs(fii_net):.0f} Cr"
            ),
        })

    # Secondary: heavy bearish coverage from authoritative sources (non-regulatory)
    auth_bearish = [
        h for h in headlines
        if any(auth in (h.get("source") or "").lower() for auth in AUTHORITATIVE_SOURCES)
        and title_to_score.get(h.get("title", ""), {}).get("score", 50) < 30
    ]
    if len(auth_bearish) >= 4:
        signals.append({
            "type": "WARNING",
            "label": "heavy_institutional_bearish_coverage",
            "count": len(auth_bearish),
            "sample_headlines": [h["title"] for h in auth_bearish[:3]],
            "description": f"{len(auth_bearish)} authoritative sources with bearish sentiment",
        })

    # FII selling alone (informational)
    if fii_selling and not signals:
        signals.append({
            "type": "WATCH",
            "label": "fii_net_selling",
            "fii_net_crores": fii_net,
            "description": f"FII net selling ₹{abs(fii_net):.0f} Cr today",
        })

    return signals


# ──────────────────────────────────────────────────────────────────────────────
# Supabase helper
# ──────────────────────────────────────────────────────────────────────────────

def _write_agent_performance(score: int, signal: str) -> None:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        return
    try:
        from supabase import create_client
        from datetime import date as _date
        create_client(url, key).table("agent_performance").insert({
            "agent_name": AGENT_NAME,
            "accuracy_90d": None,
            "hallucination_rate": None,
            "trend": "STABLE",
            "audit_date": _date.today().isoformat(),
        }).execute()
    except Exception as exc:
        log.warning("agent_performance write failed: %s", exc)


# ──────────────────────────────────────────────────────────────────────────────
# P6-D-5: spaCy NER entity-centric headline filtering
# ──────────────────────────────────────────────────────────────────────────────

# Entity alias map: subsidiary / brand name → parent NSE ticker (partial list).
# Prevents Reliance Jio news being attributed to RELIANCE.NS without context.
_ENTITY_ALIAS_MAP: dict[str, str] = {
    "reliance jio": "RELIANCE", "jio platforms": "RELIANCE",
    "tata motors":  "TATAMOTORS", "jaguar land rover": "TATAMOTORS",
    "tata steel":   "TATASTEEL", "tata chemicals": "TATACHEM",
    "hdfc bank":    "HDFCBANK", "hdfc life": "HDFCLIFE",
    "infosys bpm":  "INFY", "wipro digital": "WIPRO",
    "bajaj auto":   "BAJAJ-AUTO", "bajaj finance": "BAJFINANCE",
    "bajaj finserv": "BAJAJFINSV",
    "ongc videsh":  "ONGC",
    "coal india":   "COALINDIA",
    "ntpc green":   "NTPC", "ntpc renewable": "NTPC",
    "adani green":  "ADANIGREEN", "adani ports": "ADANIPORTS",
    "adani enterprises": "ADANIENT", "adani total gas": "ATGL",
}

_spacy_nlp = None
_spacy_available: bool | None = None   # None = not yet checked


def _get_spacy_nlp():
    """Lazy-load spaCy small English model (en_core_web_sm). Returns None if unavailable."""
    global _spacy_nlp, _spacy_available
    if _spacy_available is False:
        return None
    if _spacy_nlp is not None:
        return _spacy_nlp
    try:
        import spacy
        _spacy_nlp = spacy.load("en_core_web_sm")
        _spacy_available = True
        log.info("spaCy NER loaded (en_core_web_sm)")
        return _spacy_nlp
    except ImportError:
        log.debug("spaCy not installed — NER filtering disabled. Install: pip install spacy")
        _spacy_available = False
        return None
    except OSError:
        log.debug("spaCy model en_core_web_sm not found — run: python -m spacy download en_core_web_sm")
        _spacy_available = False
        return None


def _ner_filter_headlines(headlines: list[dict], clean_symbol: str) -> list[dict]:
    """
    P6-D-5: Use spaCy NER to drop headlines that mention the symbol keyword but
    actually refer to a *different* organisation.

    Heuristic:
      1. Extract ORG entities from headline text via en_core_web_sm.
      2. If any extracted ORG matches a known alias for a *different* NSE ticker
         (via _ENTITY_ALIAS_MAP), and the direct clean_symbol text is NOT also
         present as a standalone mention, discard the headline.
      3. If spaCy unavailable → pass-through (no filtering, no crash).

    Returns the (possibly shorter) filtered list.
    """
    nlp = _get_spacy_nlp()
    if nlp is None:
        return headlines  # graceful no-op

    filtered: list[dict] = []
    sym_lower = clean_symbol.lower()

    for h in headlines:
        title = h.get("title", "")
        title_lower = title.lower()
        doc = nlp(title)

        # Extract ORG entities
        org_texts = {ent.text.lower() for ent in doc.ents if ent.label_ == "ORG"}
        keep = True

        for org_text in org_texts:
            mapped_ticker = _ENTITY_ALIAS_MAP.get(org_text)
            if mapped_ticker and mapped_ticker.upper() != sym_lower.upper():
                # This headline mentions a subsidiary/brand that belongs to a
                # different ticker.  Only discard if our symbol is not mentioned
                # standalone (e.g. "Reliance reported..." alongside "Jio...")
                if sym_lower not in title_lower:
                    keep = False
                    log.debug(
                        "NER filter: dropped '%s' (org='%s' → %s, not %s)",
                        title[:60], org_text, mapped_ticker, clean_symbol,
                    )
                    break

        if keep:
            filtered.append(h)

    dropped = len(headlines) - len(filtered)
    if dropped > 0:
        log.debug("NER filter: %d/%d headlines dropped for %s", dropped, len(headlines), clean_symbol)
    return filtered


# ──────────────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────────────

def analyse(symbol: str) -> dict:
    """
    Run sentiment analysis for a single NSE/BSE symbol.

    Args:
        symbol: NSE ticker, e.g. "RELIANCE", "TCS.NS"

    Returns:
        {
            signal:        str         — BULLISH | BEARISH | NEUTRAL | NO_DATA
            score:         int         — 0–100 composite sentiment score
            detail: {
                headlines_analysed: int
                avg_score:          float
                sentiment_breakdown:{bullish, bearish, neutral counts}
                scored_headlines:   list of {title, source, sentiment, score, key_claim}
                rolling_trend:      {dates, avg_scores, direction}
                misinformation_flags: list
                haiku_calls_used:   int
            }
            danger_signals: list       — CRITICAL / WARNING / WATCH dicts
            data_sources:   list[str]
            agent_name:     str        — "sentiment"
        }
    """
    data_sources: list[str] = []
    all_headlines: list[dict] = []

    # ── 1. RSS headlines ──────────────────────────────────────────────────────
    rss = get_rss_headlines(symbol)
    if rss:
        all_headlines.extend(rss)
        data_sources.append("rss_feeds")

    # ── 1b. BSE corporate announcements (D-1 data enrichment) ────────────────
    clean_sym = symbol.replace(".NS", "").replace(".BO", "").strip().upper()
    try:
        bse_anns = get_bse_announcements(clean_sym, hours=24)
        if bse_anns:
            all_headlines.extend(bse_anns)
            data_sources.append("bse_filings")
            log.debug("sentiment(%s): %d BSE announcements added", symbol, len(bse_anns))
    except Exception as _bse_exc:
        log.debug("sentiment(%s): BSE fetch skipped: %s", symbol, _bse_exc)

    # ── 1c. P6-D-6: Hindi RSS (retail sentiment 2-4h earlier than English press) ─
    try:
        from data.fetchers import get_hindi_headlines
        hindi = get_hindi_headlines(symbol)
        if hindi:
            all_headlines.extend(hindi)
            data_sources.append("hindi_rss")
            log.debug("sentiment(%s): %d Hindi headlines added", symbol, len(hindi))
    except Exception as _hi_exc:
        log.debug("sentiment(%s): Hindi RSS skipped: %s", symbol, _hi_exc)

    # ── 2. NewsAPI ────────────────────────────────────────────────────────────
    newsapi_articles = _fetch_newsapi(symbol)
    if newsapi_articles:
        all_headlines.extend(newsapi_articles)
        data_sources.append("newsapi")

    if not all_headlines:
        return {
            "signal": "NO_DATA",
            "score": 50,
            "detail": {"error": f"No headlines found for {symbol}"},
            "danger_signals": [],
            "data_sources": [],
            "agent_name": AGENT_NAME,
        }

    # Deduplicate by title
    seen_titles: set[str] = set()
    unique_headlines: list[dict] = []
    for h in all_headlines:
        t = h.get("title", "").strip()
        if t and t not in seen_titles:
            seen_titles.add(t)
            unique_headlines.append(h)

    # ── 2b. P6-D-5: spaCy NER entity-centric filtering ──────────────────────
    # Removes headlines that mention the symbol text but refer to a *different*
    # entity (e.g. "Reliance Jio tariff hike" ≠ RELIANCE.NS earnings news).
    # Requires: pip install spacy && python -m spacy download en_core_web_sm
    # Gracefully skipped if spaCy not installed — no functionality lost.
    unique_headlines = _ner_filter_headlines(unique_headlines, clean_sym)

    # ── 3. Score headlines — batch Haiku classifier + FinBERT ensemble ────────
    # D-2: One batch Haiku call classifies all headlines (event_class + score).
    #       Replaces the per-headline loop and MAX_HAIKU_CALLS cap.
    # D-3: Temporal decay weight applied per headline × event_class half-life.
    # D-4: FinBERT (HF Inference API) ensemble on top-3 headlines by decay weight.
    haiku_available = bool(os.environ.get("ANTHROPIC_API_KEY"))
    haiku_calls = 0   # set to 1 below only if batch call actually succeeds
    scored: list[dict] = []
    finbert_used = False

    # D-2: Batch classify all headlines in one Haiku call
    if haiku_available and unique_headlines:
        classified = _batch_classify_headlines(unique_headlines, symbol)
        if any(not c.get("fallback") for c in classified):
            haiku_calls = 1
            data_sources.append("claude_haiku")
        log.debug("sentiment(%s): batch classified %d headlines", symbol, len(classified))
    else:
        classified = [_fallback_score(h.get("title", "")) for h in unique_headlines]

    # D-3 + D-4: Apply temporal decay and optional FinBERT ensemble
    # Select top-5 headlines by temporal weight for FinBERT to avoid rate-limiting
    weights = []
    for h, cls in zip(unique_headlines, classified):
        ec = cls.get("event_class", "UNKNOWN")
        w = _temporal_weight(h, ec)
        weights.append(w)

    # Sort indices by weight descending to find top candidates for FinBERT
    top_indices = sorted(range(len(weights)), key=lambda i: weights[i], reverse=True)[:5]

    for i, (h, cls) in enumerate(zip(unique_headlines, classified)):
        result = dict(h)
        result.update(cls)

        ec = cls.get("event_class", "UNKNOWN")
        decay_w = weights[i]

        # D-4: FinBERT ensemble on high-weight headlines
        haiku_s = cls.get("score", 50)
        if i in top_indices:
            try:
                fb = _call_finbert_hf(h.get("title", ""))
                if fb:
                    fb_s = _finbert_to_score(fb)
                    # Ensemble: 0.6 × FinBERT + 0.4 × Haiku
                    ensembled = round(
                        _FINBERT_ENSEMBLE_W * fb_s + (1 - _FINBERT_ENSEMBLE_W) * haiku_s
                    )
                    result["score"] = ensembled
                    result["finbert_score"]  = fb_s
                    result["finbert_raw"]    = fb
                    result["score_source"]   = "finbert_ensemble"
                    finbert_used = True
                    log.debug(
                        "sentiment(%s): FinBERT[%s] haiku=%d finbert=%d ensemble=%d",
                        symbol, h.get("title", "")[:40], haiku_s, fb_s, ensembled,
                    )
                else:
                    result["score_source"] = "haiku_only"
            except Exception as _fb_exc:
                log.debug("sentiment(%s): FinBERT call failed: %s", symbol, _fb_exc)
                result["score_source"] = "haiku_only"
        else:
            result["score_source"] = "haiku_only"

        # D-3: Apply temporal decay to the final score (pull toward neutral 50)
        raw_s = result.get("score", 50)
        decayed_s = round(50.0 + (raw_s - 50.0) * decay_w)
        result["score_weighted"]  = decayed_s
        result["temporal_weight"] = round(decay_w, 3)
        result["event_class"]     = ec

        # D-2: Apply event amplification multiplier (to the delta from neutral)
        multiplier = _EVENT_MULTIPLIERS.get(ec, 1.0)
        amplified = round(50.0 + (decayed_s - 50.0) * multiplier)
        result["score"] = max(0, min(100, amplified))

        # Derive sentiment label from final score
        if result["score"] >= 60:
            result["sentiment"] = "bullish"
        elif result["score"] <= 40:
            result["sentiment"] = "bearish"
        else:
            result["sentiment"] = "neutral"

        scored.append(result)

    if finbert_used:
        data_sources.append("finbert_hf")

    # ── 4. Aggregate scores (event-class weighted) ────────────────────────────
    # Use amplified+decayed scores for aggregate; fall back if all 50
    scores = [s["score"] for s in scored]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 50.0

    # Summarise event class distribution
    event_class_breakdown = Counter(s.get("event_class", "UNKNOWN") for s in scored)

    breakdown = Counter(s.get("sentiment", "neutral") for s in scored)

    # ── 4b. Insider / promoter signal (P3-C-P5) ──────────────────────────────
    # Promoter buying = +5 pts (institutional vote of confidence)
    # Promoter selling = -10 pts (smart money exit — stronger negative signal)
    # NEUTRAL / snapshot-only = 0 pts (no evidence either way)
    insider_adjustment = 0
    insider_data: dict = {}
    try:
        insider_data = get_promoter_signal(symbol)
        ins_signal   = insider_data.get("signal", "NEUTRAL")
        if ins_signal == "ACCUMULATING":
            insider_adjustment = +5
            data_sources.append("insider_promoter_buying")
            log.debug("sentiment(%s): insider +5 (promoter ACCUMULATING)", symbol)
        elif ins_signal == "DISTRIBUTING":
            insider_adjustment = -10
            data_sources.append("insider_promoter_selling")
            log.debug("sentiment(%s): insider -10 (promoter DISTRIBUTING)", symbol)
        # else NEUTRAL: 0 adjustment
    except Exception as _ins_exc:
        log.debug("sentiment(%s): insider signal skipped: %s", symbol, _ins_exc)

    if insider_adjustment != 0:
        avg_score = max(0.0, min(100.0, round(avg_score + insider_adjustment, 1)))

    # ── 5. Misinformation detection ───────────────────────────────────────────
    misinfo_flags = _detect_misinformation(unique_headlines)

    # ── 6. Rolling 7-day trend ────────────────────────────────────────────────
    trend = _rolling_trend(scored)

    # ── 7. FII data for danger detection (optional — news-only fallback) ──────
    fii_net: Optional[float] = None
    fii_available = False
    try:
        fii_data = get_nse_fii_dii()
        if fii_data:
            fii_net_raw = fii_data.get("fii_net")
            # Guard: skip zero values (holiday data or stale parse)
            if fii_net_raw is not None and fii_net_raw != 0.0:
                fii_net = float(fii_net_raw)
                fii_available = True
                data_sources.append("nse_fii_dii")
            else:
                log.debug("FII net = 0 or None — treating as unavailable")
    except Exception as exc:
        log.debug("FII fetch failed (non-critical): %s", exc)

    # ── 7b. Data completeness check ──────────────────────────────────────────
    # FII is now non-critical: sentiment proceeds as news-only when FII absent.
    _snapshot = {
        "headline_count": len(unique_headlines),
        "fii_net":        fii_net,      # None is tolerated (non-critical field)
        "min_headlines":  len(unique_headlines),
    }
    _chk = _dcv.validate(_snapshot, "sentiment")
    if not _chk.is_sufficient:
        return insufficient_data_result("sentiment", _chk,
                                        data_sources=data_sources,
                                        danger_signals=[])

    # ── 8. Danger signals ─────────────────────────────────────────────────────
    danger_signals = _detect_danger_signals(unique_headlines, scored, fii_net)

    # ── 9. Final signal ───────────────────────────────────────────────────────
    has_critical = any(d["type"] == "CRITICAL" for d in danger_signals)

    if has_critical:
        signal = "BEARISH"
    elif avg_score >= 65:
        signal = "BULLISH"
    elif avg_score >= 55:
        signal = "MILDLY_BULLISH"
    elif avg_score >= 45:
        signal = "NEUTRAL"
    elif avg_score >= 35:
        signal = "MILDLY_BEARISH"
    else:
        signal = "BEARISH"

    # Misinformation degrades confidence; adjust score toward neutral
    if misinfo_flags:
        log.warning("%d misinformation flags for %s", len(misinfo_flags), symbol)
        avg_score = round(avg_score * 0.85 + 50 * 0.15, 1)  # pull toward neutral

    result = {
        "signal": signal,
        "score":  round(avg_score),
        "detail": {
            "headlines_analysed": len(scored),
            "avg_score":          avg_score,
            "sentiment_breakdown": {
                "bullish":  breakdown.get("bullish", 0),
                "bearish":  breakdown.get("bearish", 0),
                "neutral":  breakdown.get("neutral", 0),
            },
            "scored_headlines": [
                {
                    "title":     s.get("title", ""),
                    "source":    s.get("source", ""),
                    "published": s.get("published", ""),
                    "sentiment": s.get("sentiment", "neutral"),
                    "score":     s.get("score", 50),
                    "key_claim": s.get("key_claim", ""),
                }
                for s in scored
            ],
            "rolling_trend":        trend,
            "misinformation_flags": misinfo_flags,
            "haiku_calls_used":     haiku_calls,
            # Surface FII availability so downstream / dashboard can show context
            "fii_net":              fii_net,
            "fii_available":        fii_available,
            "news_only_mode":       not fii_available,
            # D-2/D-3/D-4 enrichment fields
            "event_class_breakdown": dict(event_class_breakdown),
            "finbert_used":          finbert_used,
            "temporal_decay_applied": True,
            # P3-C-P5: insider / promoter signal
            "insider_signal":       insider_data.get("signal", "NEUTRAL"),
            "insider_adjustment":   insider_adjustment,
            "insider_note":         insider_data.get("note", ""),
        },
        "danger_signals": danger_signals,
        "data_sources":   list(dict.fromkeys(data_sources)),  # dedup, preserve order
        "agent_name":     AGENT_NAME,
    }

    try:
        _write_agent_performance(round(avg_score), signal)
    except Exception as exc:
        log.warning("Persisting agent run failed (non-critical): %s", exc)

    return result


# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    sym = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    print(f"\nAnalysing sentiment for {sym} …\n")
    out = analyse(sym)
    # Print without scored_headlines list to keep output readable
    summary = {k: v for k, v in out.items() if k != "detail"}
    summary["detail"] = {
        k: v for k, v in out["detail"].items() if k != "scored_headlines"
    }
    summary["detail"]["scored_headlines_count"] = len(
        out["detail"].get("scored_headlines", [])
    )
    print(json.dumps(summary, indent=2, default=str))
