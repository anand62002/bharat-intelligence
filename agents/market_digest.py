"""
agents/market_digest.py — Daily Market Digest Generator (P6-C)
==============================================================
Generates concise Morning Brief (08:45 IST) and Closing Digest (16:20 IST)
for the Indian equity market using Claude Haiku.

Morning Brief:
  • Overnight global cues (US/Asia, SGX Nifty, crude, gold)
  • Key domestic events scheduled for the day
  • Top corporate news since previous close

Closing Digest:
  • How Nifty / Sensex / sectors performed today
  • Notable movers and reasons
  • FII/DII flow summary
  • Tomorrow's watchlist / risk events

Entry points
────────────
  generate_digest(digest_type: str) -> dict
  save_digest(digest: dict, client=None, dry_run=False) -> str | None

CLI
───
  python -m agents.market_digest --type MORNING
  python -m agents.market_digest --type CLOSING
  python -m agents.market_digest --type MORNING --no-save
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

import feedparser
from dotenv import load_dotenv

load_dotenv()

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

log = logging.getLogger(__name__)

DIGEST_TYPES  = ("MORNING", "CLOSING")
HAIKU_MODEL   = "claude-haiku-4-5-20251001"
MAX_HEADLINES = 20   # fed to the prompt; more = richer but slower
MAX_PROMPT_HEADLINES = 18  # hard cap on what goes into the LLM prompt

# ── Market-wide RSS sources (no per-symbol filter) ────────────────────────────
_MARKET_RSS_FEEDS = [
    ("ET Markets",       "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("Moneycontrol",     "https://www.moneycontrol.com/rss/business.xml"),
    ("Hindu BizLine",    "https://www.thehindubusinessline.com/markets/feeder/default.rss"),
    ("Business Standard","https://www.business-standard.com/rss/markets-106.rss"),
]

# Google News topic RSS feeds focused on India market
_MARKET_GOOGLE_NEWS = [
    ("GNews India Market", "https://news.google.com/rss/search?q=Indian+stock+market+Nifty+Sensex&hl=en-IN&gl=IN&ceid=IN:en"),
    ("GNews India Macro",  "https://news.google.com/rss/search?q=RBI+SEBI+India+economy+budget&hl=en-IN&gl=IN&ceid=IN:en"),
    ("GNews FII DII",      "https://news.google.com/rss/search?q=FII+DII+India+market+foreign+institutional&hl=en-IN&gl=IN&ceid=IN:en"),
]

# ── Prompt templates ──────────────────────────────────────────────────────────

_MORNING_PROMPT = """\
You are a concise Indian equity market analyst writing the Morning Brief.
Today is {today}. Analyse the following {n} headlines from the last 14 hours.

HEADLINES:
{headlines}

Generate ONLY a valid JSON object (no markdown, no commentary, no extra text):
{{
  "market_mood": "BULLISH|BEARISH|NEUTRAL|VOLATILE|MIXED",
  "summary": "<2-3 paragraph morning outlook, 120-160 words, plain English>",
  "key_events": [
    {{"event": "<event description, max 15 words>", "impact": "POSITIVE|NEGATIVE|NEUTRAL|WATCH"}}
  ],
  "top_themes": ["<theme 1>", "<theme 2>", "<theme 3>"],
  "sectors_in_focus": ["<sector name>"],
  "nifty_signal": "<10-word Nifty outlook for today>"
}}

Focus on: global cues, domestic macro/policy, corporate events, FII/DII flow.
Limit key_events to the 4-5 most market-moving items.
"""

_CLOSING_PROMPT = """\
You are a concise Indian equity market analyst writing the Closing Digest.
Today is {today}. Analyse the following {n} headlines from today's session.

HEADLINES:
{headlines}

Generate ONLY a valid JSON object (no markdown, no commentary, no extra text):
{{
  "market_mood": "BULLISH|BEARISH|NEUTRAL|VOLATILE|MIXED",
  "summary": "<what happened today across Nifty/sectors/FII, 120-160 words, plain English>",
  "key_events": [
    {{"event": "<event description, max 15 words>", "impact": "POSITIVE|NEGATIVE|NEUTRAL|WATCH"}}
  ],
  "top_themes": ["<theme 1>", "<theme 2>", "<theme 3>"],
  "sectors_in_focus": ["<sector name>"],
  "nifty_signal": "<10-word outlook for tomorrow>"
}}

Focus on: today's price action reasons, sector rotation, FII/DII data, earnings reactions.
Limit key_events to the 4-5 most market-moving items.
"""


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_market_headlines(hours: int = 14) -> list[dict]:
    """
    Fetch India-market-wide headlines from static RSS + Google News.
    Returns list of {title, source, published, url}, de-duplicated by title.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    seen: set[str] = set()
    results: list[dict] = []

    all_feeds = _MARKET_RSS_FEEDS + _MARKET_GOOGLE_NEWS

    for source_name, feed_url in all_feeds:
        try:
            feed = feedparser.parse(feed_url)
            if feed.bozo and not feed.entries:
                continue

            for entry in feed.entries:
                title = (entry.get("title") or "").strip()
                if not title or title in seen:
                    continue
                seen.add(title)

                # Parse published date
                pub_str = ""
                try:
                    if entry.get("published_parsed"):
                        pub_dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                        # Skip if older than cutoff
                        if pub_dt < cutoff:
                            continue
                        pub_str = pub_dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    pass

                results.append({
                    "title":     title,
                    "source":    source_name,
                    "published": pub_str,
                    "url":       entry.get("link", ""),
                })

                if len(results) >= MAX_HEADLINES:
                    break
        except Exception as exc:
            log.debug("market_digest: feed error [%s]: %s", source_name, exc)

        if len(results) >= MAX_HEADLINES:
            break

    return results


def _format_headlines_for_prompt(headlines: list[dict]) -> str:
    """Format headline list into a numbered plain-text string for the prompt."""
    lines = []
    for i, h in enumerate(headlines[:MAX_PROMPT_HEADLINES], 1):
        ts = h.get("published", "")
        ts_part = f" [{ts}]" if ts else ""
        lines.append(f"{i}. [{h.get('source','')}]{ts_part} {h.get('title','')}")
    return "\n".join(lines)


def _call_haiku_digest(prompt: str) -> dict:
    """
    Call Claude Haiku with the digest prompt.
    Returns parsed JSON dict.
    Raises RuntimeError on any failure.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    payload = json.dumps({
        "model": HAIKU_MODEL,
        "max_tokens": 800,
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
    except HTTPError as exc:
        raise RuntimeError(f"Haiku API error {exc.code}: {exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Haiku network error: {exc.reason}") from exc

    raw_text = body["content"][0]["text"].strip()
    # Strip accidental markdown fences
    raw_text = re.sub(r"^```[a-z]*\n?", "", raw_text)
    raw_text = re.sub(r"\n?```$", "", raw_text).strip()

    parsed = json.loads(raw_text)
    return parsed


def _fallback_digest(headlines: list[dict], digest_type: str) -> dict:
    """
    Keyword-based fallback digest when Haiku is unavailable.
    Returns a minimal but valid digest dict.
    """
    bullish_kw = ["rally", "surge", "gain", "buy", "positive", "record", "high",
                  "growth", "profit", "upgrade", "beat", "strong"]
    bearish_kw = ["fall", "crash", "sell", "loss", "weak", "decline", "fraud",
                  "penalty", "ban", "raid", "probe", "warning", "cut", "default"]

    bull = sum(
        1 for h in headlines
        for w in bullish_kw if w in h.get("title", "").lower()
    )
    bear = sum(
        1 for h in headlines
        for w in bearish_kw if w in h.get("title", "").lower()
    )

    if bull > bear + 3:
        mood = "BULLISH"
    elif bear > bull + 3:
        mood = "BEARISH"
    elif bull == bear:
        mood = "NEUTRAL"
    else:
        mood = "MIXED"

    top_titles = [h["title"] for h in headlines[:5]]
    summary = (
        f"{'Morning' if digest_type == 'MORNING' else 'Closing'} brief generated from "
        f"{len(headlines)} headlines. Top stories: "
        + "; ".join(t[:60] for t in top_titles[:3]) + "."
    )

    return {
        "market_mood":      mood,
        "summary":          summary,
        "key_events":       [{"event": h["title"][:80], "impact": "NEUTRAL"} for h in headlines[:5]],
        "top_themes":       [],
        "sectors_in_focus": [],
        "nifty_signal":     "Insufficient data for signal",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def generate_digest(digest_type: str = "MORNING") -> dict:
    """
    Generate a Morning Brief or Closing Digest for today's Indian equity market.

    Parameters
    ----------
    digest_type : "MORNING" | "CLOSING"

    Returns
    -------
    dict with keys:
      digest_type, digest_date, headline_count, top_themes, summary,
      key_events, market_mood, nifty_signal, sectors_in_focus,
      raw_headlines, generated_at, source
    """
    digest_type = digest_type.upper()
    if digest_type not in DIGEST_TYPES:
        raise ValueError(f"digest_type must be one of {DIGEST_TYPES}")

    today_str = date.today().isoformat()
    hours = 14 if digest_type == "MORNING" else 10  # closing looks at today only

    log.info("market_digest: fetching headlines for %s digest", digest_type)
    headlines = _fetch_market_headlines(hours=hours)
    log.info("market_digest: fetched %d headlines", len(headlines))

    # Attempt Haiku generation
    haiku_used = False
    parsed: dict = {}

    if headlines and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            formatted = _format_headlines_for_prompt(headlines)
            template = _MORNING_PROMPT if digest_type == "MORNING" else _CLOSING_PROMPT
            prompt = template.format(
                today=today_str,
                n=min(len(headlines), MAX_PROMPT_HEADLINES),
                headlines=formatted,
            )
            parsed = _call_haiku_digest(prompt)
            haiku_used = True
            log.info("market_digest: Haiku generation OK (%s)", digest_type)
        except Exception as exc:
            log.warning("market_digest: Haiku failed, using fallback: %s", exc)
            parsed = _fallback_digest(headlines, digest_type)
    else:
        log.warning("market_digest: no ANTHROPIC_API_KEY or no headlines — fallback digest")
        parsed = _fallback_digest(headlines or [], digest_type)

    # Validate and normalise parsed response
    valid_moods = {"BULLISH", "BEARISH", "NEUTRAL", "VOLATILE", "MIXED"}
    mood = str(parsed.get("market_mood", "NEUTRAL")).upper()
    if mood not in valid_moods:
        mood = "NEUTRAL"

    key_events = parsed.get("key_events") or []
    if not isinstance(key_events, list):
        key_events = []
    # Normalise impact values
    valid_impacts = {"POSITIVE", "NEGATIVE", "NEUTRAL", "WATCH"}
    for evt in key_events:
        if isinstance(evt, dict):
            imp = str(evt.get("impact", "NEUTRAL")).upper()
            evt["impact"] = imp if imp in valid_impacts else "NEUTRAL"

    top_themes = parsed.get("top_themes") or []
    if not isinstance(top_themes, list):
        top_themes = []

    sectors = parsed.get("sectors_in_focus") or []
    if not isinstance(sectors, list):
        sectors = []

    digest = {
        "digest_type":      digest_type,
        "digest_date":      today_str,
        "headline_count":   len(headlines),
        "market_mood":      mood,
        "summary":          str(parsed.get("summary") or "")[:2000],
        "key_events":       key_events[:8],
        "top_themes":       top_themes[:6],
        "sectors_in_focus": sectors[:6],
        "nifty_signal":     str(parsed.get("nifty_signal") or "")[:200],
        "raw_headlines":    [
            {"title": h["title"], "source": h["source"], "published": h.get("published","")}
            for h in headlines[:MAX_HEADLINES]
        ],
        "generated_at":     datetime.now(timezone.utc).isoformat(),
        "source":           "claude_haiku" if haiku_used else "keyword_fallback",
    }
    return digest


def save_digest(
    digest: dict,
    client=None,
    dry_run: bool = False,
) -> Optional[str]:
    """
    Upsert a digest dict to the market_digests Supabase table.

    Returns the row ID on success, None otherwise.
    """
    if dry_run:
        log.info(
            "[DRY RUN] market_digest: would save %s digest for %s",
            digest.get("digest_type"), digest.get("digest_date"),
        )
        return None

    if client is None:
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_SERVICE_KEY")
        if not url or not key:
            log.warning("market_digest: Supabase env vars not set — cannot save digest")
            return None
        try:
            from supabase import create_client
            client = create_client(url, key)
        except Exception as exc:
            log.warning("market_digest: Supabase init failed: %s", exc)
            return None

    row = {
        "digest_type":      digest["digest_type"],
        "digest_date":      digest["digest_date"],
        "headline_count":   digest.get("headline_count", 0),
        "market_mood":      digest.get("market_mood"),
        "summary":          digest.get("summary"),
        "key_events":       digest.get("key_events", []),
        "top_themes":       digest.get("top_themes", []),
        "sectors_in_focus": digest.get("sectors_in_focus", []),
        "nifty_signal":     digest.get("nifty_signal"),
        "raw_headlines":    digest.get("raw_headlines", []),
    }

    try:
        resp = (
            client.table("market_digests")
            .upsert(row, on_conflict="digest_type,digest_date")
            .execute()
        )
        rows = resp.data or []
        row_id = rows[0].get("id") if rows else None
        log.info(
            "market_digest: saved %s digest for %s (id=%s)",
            digest["digest_type"], digest["digest_date"], row_id,
        )
        return row_id
    except Exception as exc:
        log.warning("market_digest: save failed: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a market digest")
    parser.add_argument("--type", choices=["MORNING", "CLOSING"], default="MORNING")
    parser.add_argument("--no-save", action="store_true", help="Print only; do not write to Supabase")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    digest = generate_digest(args.type)

    print(f"\n{'='*60}")
    print(f"  {args.type} MARKET BRIEF — {digest['digest_date']}")
    print(f"  Mood: {digest['market_mood']} | Headlines: {digest['headline_count']} | Source: {digest['source']}")
    print(f"{'='*60}")
    print(f"\n{digest['summary']}\n")
    if digest["key_events"]:
        print("KEY EVENTS:")
        for evt in digest["key_events"]:
            print(f"  [{evt.get('impact','?')}] {evt.get('event','')}")
    if digest["top_themes"]:
        print(f"\nTHEMES: {', '.join(digest['top_themes'])}")
    print(f"\nNIFTY SIGNAL: {digest['nifty_signal']}\n")

    if not args.no_save:
        row_id = save_digest(digest)
        if row_id:
            print(f"Saved to Supabase: {row_id}")
        else:
            print("Save skipped (dry run or Supabase unavailable)")


if __name__ == "__main__":
    main()
