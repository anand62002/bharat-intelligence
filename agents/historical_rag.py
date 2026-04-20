"""
agents/historical_rag.py — Historical RAG Agent
Embeds a current market setup description, queries Supabase pgvector for the
top-3 most similar historical events, and extracts a signal + lesson.

Entry point: analyse(market_description) -> dict

Embedding strategy (in priority order):
  1. OpenAI text-embedding-3-small (if OPENAI_API_KEY is set)   — 1536-dim
  2. Keyword-TF-IDF cosine similarity (always available, no deps)
     Works against plain-text descriptions when pgvector rows have no embedding.
"""

import json
import logging
import math
import os
import re
import sys
from collections import Counter
from datetime import date
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv

load_dotenv()

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

log = logging.getLogger(__name__)
AGENT_NAME = "historical_rag"
_EMBED_DIM  = 1536     # OpenAI text-embedding-3-small

# ──────────────────────────────────────────────────────────────────────────────
# Embedding: OpenAI (primary) + keyword TF-IDF (fallback)
# ──────────────────────────────────────────────────────────────────────────────

def _embed_openai(text: str) -> Optional[list[float]]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    payload = json.dumps({
        "model": "text-embedding-3-small",
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
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        return data["data"][0]["embedding"]
    except (URLError, HTTPError, KeyError, json.JSONDecodeError) as exc:
        log.warning("OpenAI embedding failed: %s", exc)
        return None


def _tokenise(text: str) -> list[str]:
    stop = {
        "a","an","the","and","or","of","in","on","at","to","for","is","are",
        "was","were","its","it","by","with","as","that","this","from","be",
        "has","have","had","not","but","if","so","than","then","also","into",
        "after","during","while","when","such","due","following","per",
    }
    tokens = re.sub(r"[^a-z0-9 ]", "", text.lower()).split()
    return [t for t in tokens if t not in stop and len(t) > 2]


def _tfidf_vector(tokens: list[str], vocab: list[str]) -> list[float]:
    """Produce a simple TF-IDF-like vector over a given vocabulary."""
    tf = Counter(tokens)
    total = max(len(tokens), 1)
    return [tf.get(w, 0) / total for w in vocab]


def _cosine(a: list[float], b: list[float]) -> float:
    dot   = sum(x * y for x, y in zip(a, b))
    na    = math.sqrt(sum(x * x for x in a))
    nb    = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return round(dot / (na * nb), 4)


def _cosine_vec(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length float lists."""
    return _cosine(a, b)


def _keyword_similarity(query: str, candidates: list[dict]) -> list[tuple[dict, float]]:
    """
    Keyword TF-IDF cosine similarity between query and each candidate's description.
    candidates: list of historical_event dicts with 'description' field.
    Returns list of (event_dict, similarity_score) sorted by similarity desc.
    """
    all_texts = [query] + [c.get("description", "") for c in candidates]
    all_tokens = [_tokenise(t) for t in all_texts]

    # Build vocabulary from all tokens
    vocab = sorted(set(tok for toks in all_tokens for tok in toks))
    if not vocab:
        return [(c, 0.0) for c in candidates]

    query_vec = _tfidf_vector(all_tokens[0], vocab)
    results = []
    for i, cand in enumerate(candidates):
        cand_vec = _tfidf_vector(all_tokens[i + 1], vocab)
        sim = _cosine(query_vec, cand_vec)
        results.append((cand, sim))

    return sorted(results, key=lambda x: x[1], reverse=True)


# ──────────────────────────────────────────────────────────────────────────────
# Supabase: fetch candidates + vector search
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_all_events(limit: int = 100) -> list[dict]:
    """
    Pull up to `limit` historical_events rows from Supabase.
    Returns [] if Supabase is unconfigured.
    """
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        log.debug("Supabase not configured — RAG unavailable")
        return []
    try:
        from supabase import create_client
        resp = (
            create_client(url, key)
            .table("historical_events")
            .select("id, event_type, description, event_date, affected_sectors, market_impact, outcome, embedding")
            .limit(limit)
            .execute()
        )
        return resp.data or []
    except Exception as exc:
        log.warning("Supabase event fetch failed: %s", exc)
        return []


def _vector_search(embedding: list[float], top_k: int = 3) -> list[dict]:
    """
    Run pgvector cosine-distance query via Supabase RPC.
    Falls back to [] if the function doesn't exist (embeddings not populated yet).
    """
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        return []
    try:
        from supabase import create_client
        resp = (
            create_client(url, key)
            .rpc("match_historical_events", {
                "query_embedding": embedding,
                "match_threshold": 0.5,
                "match_count": top_k,
            })
            .execute()
        )
        return resp.data or []
    except Exception as exc:
        log.debug("pgvector RPC failed (may not be set up yet): %s", exc)
        return []


def _write_agent_performance() -> None:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        return
    try:
        from supabase import create_client
        create_client(url, key).table("agent_performance").insert({
            "agent_name": AGENT_NAME,
            "accuracy_90d": None,
            "hallucination_rate": None,
            "trend": "STABLE",
            "audit_date": date.today().isoformat(),
        }).execute()
    except Exception as exc:
        log.warning("agent_performance write failed: %s", exc)


# ──────────────────────────────────────────────────────────────────────────────
# Signal derivation from matched events
# ──────────────────────────────────────────────────────────────────────────────

_NEGATIVE_IMPACTS = {"SEVERE_NEGATIVE", "MODERATE_NEGATIVE", "SECTOR_NEGATIVE",
                     "SEVERE_SECTOR_DISRUPTION"}
_POSITIVE_IMPACTS = {"STRONG_POSITIVE", "MILD_POSITIVE", "LONG_TERM_POSITIVE"}

# ── Score floor guard ──────────────────────────────────────────────────────────
# When the historical_events table has fewer than _MIN_BALANCED_COUNT events
# of EACH polarity (positive / negative), a small or one-sided seed can drive
# the weighted-sentiment score all the way to its floor (~10).  That "max-
# bearish default" misleads the downstream composite scorer.
#
# The floor of 30 keeps the signal in a cautious-but-not-extreme range until
# the event library grows large enough to be statistically trusted.
# Formula: score = round(50 + avg * 40)  →  floor-of-30 ≡ avg ≤ -0.5
_RAG_SCORE_FLOOR:    int = 30
_MIN_BALANCED_COUNT: int = 10   # need ≥10 positive AND ≥10 negative events


def _check_db_balance() -> tuple[int, int]:
    """
    Count positive-impact and negative-impact events in the historical_events table.

    Reuses _fetch_all_events (up to 200 rows) so no extra query type is needed.
    Returns (n_positive, n_negative).  Both 0 when Supabase is unavailable.

    A "balanced" DB satisfies:
        n_positive >= _MIN_BALANCED_COUNT AND n_negative >= _MIN_BALANCED_COUNT
    """
    events = _fetch_all_events(limit=200)
    n_pos = sum(1 for e in events if e.get("market_impact") in _POSITIVE_IMPACTS)
    n_neg = sum(1 for e in events if e.get("market_impact") in _NEGATIVE_IMPACTS)
    return n_pos, n_neg


def _derive_signal(matches: list[dict], scores: list[float]) -> tuple[str, int, str]:
    """
    Derive signal, score, and reasoning from top matched events.
    Weights outcomes by similarity score.
    Returns (signal, score 0-100, reasoning).
    """
    if not matches:
        return "NEUTRAL", 50, "No historical analogues found"

    weighted_sentiment = 0.0
    total_weight = 0.0
    outcomes: list[str] = []

    for event, sim in zip(matches, scores):
        impact = event.get("market_impact", "") or ""
        if impact in _POSITIVE_IMPACTS:
            sentiment = 1.0
        elif impact in _NEGATIVE_IMPACTS:
            sentiment = -1.0
        else:
            sentiment = 0.0
        weighted_sentiment += sentiment * sim
        total_weight += sim
        outcome = event.get("outcome") or ""
        if outcome:
            outcomes.append(f"[{event.get('event_date','?')}] {outcome[:120]}")

    avg = weighted_sentiment / total_weight if total_weight > 0 else 0.0
    score = round(50 + avg * 40)   # maps [-1,1] → [10,90]
    score = max(0, min(100, score))

    if avg >= 0.4:
        signal = "BULLISH_ANALOGUE"
    elif avg <= -0.4:
        signal = "BEARISH_ANALOGUE"
    else:
        signal = "MIXED_ANALOGUE"

    reasoning = "; ".join(outcomes[:2]) if outcomes else "See matched events for context"
    return signal, score, reasoning


# ──────────────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────────────

def analyse(market_description: str) -> dict:
    """
    Find the top-3 historical analogues for the current market setup.

    Args:
        market_description: Free-text description of current conditions, e.g.
            "FII selling 5000 Cr, RBI cutting rates, INR at 86, VIX at 22"

    Returns:
        {
            signal:           str   — BULLISH_ANALOGUE | BEARISH_ANALOGUE | MIXED_ANALOGUE | NO_DATA
            score:            int   — 0–100 (weighted by similarity)
            detail:           dict  — embedding method, query text
            matched_events:   list  — top-3 events with full context
            similarity_scores: list — cosine similarity per match
            agent_name:       str   — "historical_rag"
        }
    """
    if not market_description or not market_description.strip():
        return {
            "signal": "NO_DATA",
            "score": 50,
            "detail": {"error": "Empty market description provided"},
            "matched_events": [],
            "similarity_scores": [],
            "agent_name": AGENT_NAME,
        }

    embed_method = "none"
    matched_events: list[dict] = []
    similarity_scores: list[float] = []

    # ── 1. Try OpenAI embedding + pgvector search ─────────────────────────────
    embedding = _embed_openai(market_description)
    if embedding:
        embed_method = "openai_text-embedding-3-small"
        vector_results = _vector_search(embedding, top_k=3)
        if vector_results:
            matched_events    = vector_results
            similarity_scores = [round(float(r.get("similarity", 0)), 4) for r in vector_results]

    # ── 2. Fallback: keyword similarity against all events ────────────────────
    if not matched_events:
        all_events = _fetch_all_events(limit=100)
        if all_events:
            ranked = _keyword_similarity(market_description, all_events)
            top3 = ranked[:3]
            matched_events    = [e for e, _ in top3]
            similarity_scores = [s for _, s in top3]
            embed_method = "keyword_tfidf_cosine"
        else:
            # No Supabase data at all — return graceful no-data
            return {
                "signal": "NO_DATA",
                "score":  50,
                "detail": {
                    "error": "No historical events in database (run db/seed_historical_events.py first)",
                    "embed_method": embed_method,
                    "query": market_description[:200],
                },
                "matched_events":   [],
                "similarity_scores": [],
                "agent_name": AGENT_NAME,
            }

    # ── 3. Derive signal ──────────────────────────────────────────────────────
    signal, score, reasoning = _derive_signal(matched_events, similarity_scores)

    # ── 4. Score floor: prevent max-bearish default on sparse / biased DB ────
    # Count polarity balance in the full event table.  When neither side has
    # reached _MIN_BALANCED_COUNT rows yet, clamp the score to _RAG_SCORE_FLOOR
    # so a biased seed can't push the composite signal to its worst extreme.
    n_pos_events, n_neg_events = _check_db_balance()
    db_balanced = (
        n_pos_events >= _MIN_BALANCED_COUNT
        and n_neg_events >= _MIN_BALANCED_COUNT
    )
    score_floor_applied = False
    if not db_balanced and score < _RAG_SCORE_FLOOR:
        log.debug(
            "RAG score floor %d applied (n_pos=%d n_neg=%d raw_score=%d) — DB not yet balanced",
            _RAG_SCORE_FLOOR, n_pos_events, n_neg_events, score,
        )
        score = _RAG_SCORE_FLOOR
        score_floor_applied = True

    # Clean events for output (remove raw embedding vectors — too large)
    clean_events = []
    for ev in matched_events:
        clean_events.append({k: v for k, v in ev.items() if k != "embedding"})

    result = {
        "signal":           signal,
        "score":            score,
        "detail": {
            "embed_method":        embed_method,
            "query":               market_description[:300],
            "reasoning":           reasoning,
            "events_in_db":        len(matched_events),
            "db_n_positive":       n_pos_events,
            "db_n_negative":       n_neg_events,
            "db_balanced":         db_balanced,
            "score_floor_applied": score_floor_applied,
        },
        "matched_events":    clean_events,
        "similarity_scores": similarity_scores,
        "agent_name":        AGENT_NAME,
    }

    try:
        _write_agent_performance()
    except Exception as exc:
        log.warning("Persisting agent run failed (non-critical): %s", exc)

    return result


if __name__ == "__main__":
    import json as _json
    import sys as _sys
    query = " ".join(_sys.argv[1:]) or "FII selling 5000 Cr, INR at 86, India VIX at 22, RBI cutting rates"
    print(f"\nRAG query: {query}\n")
    out = analyse(query)
    print(_json.dumps(out, indent=2, default=str))
