"""
agents/macro.py — Macro Environment Agent
Fetches US (FRED) and India macro indicators, scores the environment 0-100,
and outputs sector-specific implications.

Entry point: analyse() -> dict
"""

import json
import logging
import os
import re
import sys
from datetime import date, datetime, timedelta
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urlencode
from urllib.request import Request, urlopen

from dotenv import load_dotenv

load_dotenv()

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from data.fetchers import get_inr_usd, get_india_vix  # noqa: E402
from agents.base import DataCompletenessValidator, insufficient_data_result

_dcv = DataCompletenessValidator()

log = logging.getLogger(__name__)
AGENT_NAME = "macro"

# ──────────────────────────────────────────────────────────────────────────────
# FRED API
# ──────────────────────────────────────────────────────────────────────────────

_FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
_FRED_SERIES = {
    "us10y": "DGS10",         # US 10-Year Treasury yield
    "dxy":   "DTWEXBGS",      # Broad USD Index
    "vix":   "VIXCLS",        # CBOE VIX
}


def _fred_latest(series_id: str, api_key: str) -> Optional[float]:
    """Fetch the most recent non-null observation for a FRED series."""
    params = urlencode({
        "series_id":      series_id,
        "api_key":        api_key,
        "file_type":      "json",
        "sort_order":     "desc",
        "limit":          5,
        "observation_start": (date.today() - timedelta(days=10)).isoformat(),
    })
    url = f"{_FRED_BASE}?{params}"
    try:
        req = Request(url, headers={"User-Agent": "BharatIntelligence/1.0"})
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        for obs in data.get("observations", []):
            val = obs.get("value", ".")
            if val != ".":
                return float(val)
    except (URLError, HTTPError, json.JSONDecodeError, ValueError) as exc:
        log.warning("FRED fetch failed for %s: %s", series_id, exc)
    return None


def fetch_fred_indicators() -> dict:
    """
    Returns {us10y, dxy, vix} from FRED.
    Uses FRED_API_KEY env var (free at fred.stlouisfed.org).
    Returns None values if key absent or network fails.
    """
    api_key = os.environ.get("FRED_API_KEY")
    result = {k: None for k in _FRED_SERIES}
    if not api_key:
        log.debug("FRED_API_KEY not set — skipping FRED fetch")
        return result
    for name, sid in _FRED_SERIES.items():
        result[name] = _fred_latest(sid, api_key)
    return result


# ──────────────────────────────────────────────────────────────────────────────
# RBI repo rate scraper
# ──────────────────────────────────────────────────────────────────────────────

_RBI_PRESS_URL = "https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx"
_RBI_HEADERS   = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*",
}
_REPO_RE = re.compile(
    r"repo\s+rate[^0-9]{0,30}(\d{1,2}(?:\.\d{1,2})?)\s*(?:per\s+cent|%)",
    re.IGNORECASE,
)


def fetch_rbi_repo_rate() -> Optional[float]:
    """
    Scrape RBI press releases for the current repo rate.
    Falls back to None on any failure — callers use a sensible default.
    """
    try:
        req = Request(_RBI_PRESS_URL, headers=_RBI_HEADERS)
        with urlopen(req, timeout=12) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        m = _REPO_RE.search(html)
        if m:
            return float(m.group(1))
    except Exception as exc:
        log.warning("RBI scrape failed: %s", exc)
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Scoring helpers
# ──────────────────────────────────────────────────────────────────────────────

def _score_us10y(us10y: Optional[float]) -> tuple[int, str]:
    """
    US 10Y yield.
    High yield = tight global liquidity = negative for EMs.
    0 pts = >5%, 25 pts = <3.5%
    """
    if us10y is None:
        return 10, "US10Y unknown (neutral)"
    if us10y < 3.5:
        return 25, f"US10Y {us10y:.2f}% — loose global liquidity, EM-positive"
    if us10y < 4.0:
        return 18, f"US10Y {us10y:.2f}% — moderate yield, manageable"
    if us10y < 4.5:
        return 12, f"US10Y {us10y:.2f}% — elevated, FII caution"
    if us10y < 5.0:
        return 5,  f"US10Y {us10y:.2f}% — high, FII outflow risk"
    return 0, f"US10Y {us10y:.2f}% — very high, EM headwind"


def _score_dxy(dxy: Optional[float]) -> tuple[int, str]:
    """
    Dollar index — higher USD = negative for INR and Indian equities.
    """
    if dxy is None:
        return 10, "DXY unknown (neutral)"
    if dxy < 98:
        return 25, f"DXY {dxy:.1f} — weak USD, INR/EM supportive"
    if dxy < 102:
        return 18, f"DXY {dxy:.1f} — neutral USD"
    if dxy < 106:
        return 10, f"DXY {dxy:.1f} — strong USD, mild INR pressure"
    return 3,  f"DXY {dxy:.1f} — very strong USD, INR depreciation risk"


def _score_vix(vix: Optional[float]) -> tuple[int, str]:
    """Global risk sentiment via VIX."""
    if vix is None:
        return 8, "VIX unknown (neutral)"
    if vix < 15:
        return 15, f"VIX {vix:.1f} — low fear, risk-on"
    if vix < 20:
        return 12, f"VIX {vix:.1f} — calm markets"
    if vix < 25:
        return 7,  f"VIX {vix:.1f} — moderate volatility"
    if vix < 35:
        return 3,  f"VIX {vix:.1f} — elevated fear"
    return 0, f"VIX {vix:.1f} — crisis-level fear"


def _score_india_vix(india_vix: Optional[float]) -> tuple[int, str]:
    """India VIX — local market fear gauge."""
    if india_vix is None:
        return 5, "India VIX unknown (neutral)"
    if india_vix < 13:
        return 10, f"India VIX {india_vix:.1f} — very calm"
    if india_vix < 18:
        return 8,  f"India VIX {india_vix:.1f} — low volatility"
    if india_vix < 25:
        return 5,  f"India VIX {india_vix:.1f} — moderate"
    if india_vix < 35:
        return 2,  f"India VIX {india_vix:.1f} — elevated"
    return 0, f"India VIX {india_vix:.1f} — panic"


def _score_inr(inr_usd: Optional[float]) -> tuple[int, str]:
    """
    INR/USD rate.  Higher number = weaker rupee.
    Persistent weakness = negative for import-heavy sectors.
    """
    if inr_usd is None:
        return 8, "INR/USD unknown (neutral)"
    if inr_usd < 82:
        return 15, f"INR {inr_usd:.2f}/USD — strong rupee"
    if inr_usd < 84:
        return 12, f"INR {inr_usd:.2f}/USD — stable"
    if inr_usd < 86:
        return 8,  f"INR {inr_usd:.2f}/USD — mild weakness"
    if inr_usd < 88:
        return 4,  f"INR {inr_usd:.2f}/USD — weak rupee"
    return 1, f"INR {inr_usd:.2f}/USD — sharply weak rupee"


def _score_rbi_rate(repo: Optional[float]) -> tuple[int, str]:
    """
    RBI repo rate context.
    Rate-cut cycle = positive for rate-sensitives.
    """
    if repo is None:
        return 7, "RBI repo rate unknown (neutral)"
    if repo <= 5.0:
        return 10, f"RBI repo {repo:.2f}% — accommodative"
    if repo <= 6.0:
        return 7,  f"RBI repo {repo:.2f}% — neutral"
    if repo <= 6.75:
        return 4,  f"RBI repo {repo:.2f}% — mildly restrictive"
    return 2, f"RBI repo {repo:.2f}% — restrictive"


# ──────────────────────────────────────────────────────────────────────────────
# India macro news monitoring  (catches PM/RBI/Budget announcements)
# ──────────────────────────────────────────────────────────────────────────────

# Google News RSS — India-specific macro queries (no API key needed)
_MACRO_NEWS_QUERIES = [
    "India economy RBI",
    "India budget fiscal policy",
    "Modi India economic policy",
    "India GDP inflation",
]

# NewsAPI query (if NEWSAPI_KEY set) — broader coverage
_NEWSAPI_MACRO_QUERY = "India economy OR RBI OR Modi economic OR India budget OR India market"

# Keywords that indicate POSITIVE macro events
_MACRO_POSITIVE_KW = [
    "rate cut", "repo cut", "rate reduction", "repo rate reduced",
    "gdp growth", "growth beats", "economic surplus", "trade surplus",
    "stimulus package", "tax relief", "tax cut", "reform package",
    "trade deal", "ceasefire", "peace deal", "foreign investment inflow",
    "rating upgrade", "credit upgrade", "current account surplus",
    "record gst", "gst collection record",
]

# Keywords that indicate NEGATIVE macro events
_MACRO_NEGATIVE_KW = [
    "rate hike", "repo rate hike", "inflation spike", "inflation surge",
    "war declared", "military strike", "border conflict", "sanctions imposed",
    "economic default", "currency crisis", "capital flight", "credit downgrade",
    "gdp miss", "gdp contraction", "recession", "tariff hike", "trade war",
    "geopolitical tension", "cross-border tension", "crude oil spike",
    "rupee crash", "rupee falls sharply", "foreign outflow", "fpi outflow record",
    "economic slowdown", "growth downgrade",
]

# Keywords that signal a major announcement (neutral score but must be flagged)
_MACRO_WATCHLIST_KW = [
    "union budget", "budget 2025", "budget 2026", "rbi policy", "rbi mpc",
    "monetary policy committee", "rbi governor", "repo rate decision",
    "federal reserve", "fed rate", "us fed", "msci rebalance",
    "crude oil", "oil prices", "global recession", "us tariff",
]


def _fetch_india_macro_news(hours: int = 36) -> list[dict]:
    """
    Fetch recent India macro/policy news headlines via Google News RSS.

    Uses multiple macro query terms to cast a wide net.  No API key required.
    Returns list of {title, source, published, url} deduplicated by title.
    Returns [] on complete failure (non-fatal).
    """
    import feedparser

    seen_titles: set[str] = set()
    results: list[dict] = []

    google_tmpl = (
        "https://news.google.com/rss/search"
        "?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
    )

    for query in _MACRO_NEWS_QUERIES:
        url = google_tmpl.format(query=quote_plus(query))
        try:
            feed = feedparser.parse(url)
            if feed.bozo and not feed.entries:
                continue
            for entry in feed.entries[:8]:          # max 8 per query term
                title = (entry.get("title") or "").strip()
                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)
                published = entry.get("published", "")
                try:
                    if entry.get("published_parsed"):
                        published = str(
                            datetime(*entry.published_parsed[:6]).date()
                        )
                except Exception:
                    pass
                results.append({
                    "title":     title,
                    "source":    "Google News",
                    "published": published,
                    "url":       entry.get("link", ""),
                })
        except Exception as exc:
            log.debug("macro news RSS fetch failed (query=%r): %s", query, exc)

    # Also try NewsAPI if key is available
    newsapi_key = os.environ.get("NEWSAPI_KEY") or os.environ.get("NEWS_API_KEY")
    if newsapi_key:
        try:
            from datetime import timezone
            from urllib.request import Request, urlopen
            from_dt = (
                datetime.now(timezone.utc) - timedelta(hours=hours)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            params = urlencode({
                "q":        _NEWSAPI_MACRO_QUERY,
                "from":     from_dt,
                "sortBy":   "publishedAt",
                "language": "en",
                "pageSize": 20,
                "apiKey":   newsapi_key,
            })
            req = Request(
                f"https://newsapi.org/v2/everything?{params}",
                headers={"User-Agent": "BharatIntelligence/1.0"},
            )
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            for art in data.get("articles") or []:
                title = ((art.get("title") or "")).strip()
                if not title or title == "[Removed]" or title in seen_titles:
                    continue
                seen_titles.add(title)
                src = (art.get("source") or {}).get("name") or "NewsAPI"
                results.append({
                    "title":     title,
                    "source":    src,
                    "published": (art.get("publishedAt") or "")[:10],
                    "url":       art.get("url") or "",
                })
        except Exception as exc:
            log.debug("macro NewsAPI fetch failed: %s", exc)

    return results


def _score_macro_news(headlines: list[dict]) -> tuple[int, str, list[str]]:
    """
    Score macro news headlines for market impact.

    Returns:
        (score_adjustment, signal, key_events)

        score_adjustment: int in [-10, +10] — added to the base macro total
        signal: "POSITIVE_SHOCK" | "NEGATIVE_SHOCK" | "MAJOR_EVENT" | "ROUTINE"
        key_events: list of short headline excerpts that triggered scoring
    """
    if not headlines:
        return 0, "ROUTINE", []

    pos_count   = 0
    neg_count   = 0
    watch_count = 0
    key_events: list[str] = []

    for h in headlines:
        title_lc = h.get("title", "").lower()

        matched_pos = [kw for kw in _MACRO_POSITIVE_KW if kw in title_lc]
        matched_neg = [kw for kw in _MACRO_NEGATIVE_KW if kw in title_lc]
        matched_wl  = [kw for kw in _MACRO_WATCHLIST_KW if kw in title_lc]

        if matched_neg:
            neg_count += len(matched_neg)
            key_events.append(h["title"][:120])
        elif matched_pos:
            pos_count += len(matched_pos)
            key_events.append(h["title"][:120])
        elif matched_wl:
            watch_count += 1
            key_events.append(h["title"][:120])

    # Deduplicate key events
    seen: set[str] = set()
    unique_events: list[str] = []
    for e in key_events:
        if e not in seen:
            seen.add(e)
            unique_events.append(e)
    key_events = unique_events[:8]           # top 8 only

    # Score: +5 per positive event (cap +10), -5 per negative (cap -10)
    adj = max(-10, min(10, pos_count * 5 - neg_count * 5))

    if neg_count >= 2:
        signal = "NEGATIVE_SHOCK"
    elif pos_count >= 2:
        signal = "POSITIVE_SHOCK"
    elif neg_count == 1 or pos_count == 1:
        signal = "POSITIVE_SHOCK" if pos_count >= neg_count else "NEGATIVE_SHOCK"
    elif watch_count >= 1:
        signal = "MAJOR_EVENT"
    else:
        signal = "ROUTINE"

    return adj, signal, key_events


# ──────────────────────────────────────────────────────────────────────────────
# Sector impact mapping
# ──────────────────────────────────────────────────────────────────────────────

def _sector_impacts(
    us10y: Optional[float],
    dxy: Optional[float],
    india_vix: Optional[float],
    inr_usd: Optional[float],
    repo: Optional[float],
) -> dict[str, dict]:
    """
    Returns a dict mapping sector → {outlook, reason}.
    Rules are based on standard macro-sector relationships for India.
    """
    impacts: dict[str, dict] = {}

    high_us10y = us10y is not None and us10y > 4.5
    strong_usd = dxy is not None and dxy > 104
    weak_inr   = inr_usd is not None and inr_usd > 84
    low_repo   = repo is not None and repo <= 6.0
    high_vix   = india_vix is not None and india_vix > 20

    # IT — benefits from weak INR (revenue in USD), hurt by US slowdown signals
    it_outlook = "POSITIVE" if weak_inr and not high_us10y else (
        "NEGATIVE" if high_us10y and not weak_inr else "NEUTRAL"
    )
    impacts["IT"] = {
        "outlook": it_outlook,
        "reason": (
            "Weak INR boosts USD revenue realisation"
            if weak_inr else
            "High US yields signal slower US growth, IT demand risk"
            if high_us10y else
            "Balanced macro for IT"
        ),
    }

    # Banking / NBFC — rate-sensitive; benefits from rate cuts
    bank_outlook = "POSITIVE" if low_repo else "NEGATIVE" if (repo or 6) > 6.5 else "NEUTRAL"
    impacts["BANKING"] = {
        "outlook": bank_outlook,
        "reason": (
            f"RBI repo {repo:.2f}% supportive for NIM expansion"
            if low_repo else
            "High rates compress NIMs for variable-rate books"
        ),
    }

    # Pharma — USD earner; benefits from weak INR
    impacts["PHARMA"] = {
        "outlook": "POSITIVE" if weak_inr else "NEUTRAL",
        "reason": "Weak INR boosts US generic export realisations" if weak_inr
                  else "Neutral INR impact on pharma exports",
    }

    # Oil & Gas / OMCs — crude costs in USD, weak INR = higher import cost
    impacts["OIL_GAS"] = {
        "outlook": "NEGATIVE" if (weak_inr or strong_usd) else "NEUTRAL",
        "reason": "Weak INR / strong USD raises USD-denominated crude import cost"
                  if weak_inr or strong_usd else "Manageable currency impact",
    }

    # Realty / Infra — highly rate-sensitive
    impacts["REALTY"] = {
        "outlook": "POSITIVE" if low_repo else "NEGATIVE" if (repo or 6) > 6.5 else "NEUTRAL",
        "reason": (
            "Low repo rate reduces mortgage costs, boosts demand"
            if low_repo else
            "High rates dampen mortgage affordability"
        ),
    }

    # Auto — domestic demand + rate sensitivity
    impacts["AUTO"] = {
        "outlook": "POSITIVE" if low_repo and not high_vix else "NEUTRAL",
        "reason": "Low rates and stable markets support auto finance demand"
                  if low_repo else "Neutral macro for auto",
    }

    # Metals / Mining — global growth proxy; hurt by strong USD
    impacts["METALS"] = {
        "outlook": "NEGATIVE" if strong_usd else "NEUTRAL",
        "reason": "Strong USD historically pressures commodity/metal prices"
                  if strong_usd else "USD neutral for metals",
    }

    # FMCG — defensive; benefits from stable macro
    impacts["FMCG"] = {
        "outlook": "POSITIVE" if not high_vix else "NEUTRAL",
        "reason": "Low volatility favours defensive FMCG holdings"
                  if not high_vix else "High volatility; FMCG defensive but muted",
    }

    return impacts


# ──────────────────────────────────────────────────────────────────────────────
# Supabase helper
# ──────────────────────────────────────────────────────────────────────────────

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
# Public entry point
# ──────────────────────────────────────────────────────────────────────────────

def analyse() -> dict:
    """
    Score the macro environment for Indian equity investing.

    Returns:
        {
            signal:         str   — RISK_ON | NEUTRAL | RISK_OFF
            score:          int   — 0–100
            detail:         dict  — per-indicator scores and notes
            sector_impacts: dict  — sector → {outlook, reason}
            data_sources:   list[str]
            agent_name:     str   — "macro"
        }
    """
    data_sources: list[str] = []

    # ── 1. Fetch all indicators ───────────────────────────────────────────────
    fred = fetch_fred_indicators()
    us10y = fred.get("us10y")
    dxy   = fred.get("dxy")
    vix   = fred.get("vix")
    if any(v is not None for v in fred.values()):
        data_sources.append("fred_api")

    repo = fetch_rbi_repo_rate()
    if repo is not None:
        data_sources.append("rbi_press_releases")

    inr_usd = get_inr_usd()
    if inr_usd is not None:
        data_sources.append("yfinance_usdinr")

    india_vix = get_india_vix()
    if india_vix is not None:
        data_sources.append("yfinance_indiavix")

    # ── 1b. Data completeness check ──────────────────────────────────────────
    _available = sum(1 for v in [us10y, dxy, vix, india_vix, inr_usd, repo] if v is not None)
    _snapshot = {
        "indicators_available": _available,
        "inr_usd":              inr_usd,
        "india_vix":            india_vix,
    }
    _chk = _dcv.validate(_snapshot, "macro")
    if not _chk.is_sufficient:
        return insufficient_data_result("macro", _chk,
                                        data_sources=data_sources,
                                        sector_impacts={})

    # ── 1c. India macro news (catches PM/RBI/Budget announcements) ───────────
    macro_headlines = _fetch_india_macro_news(hours=36)
    news_adj, news_signal, key_news_events = _score_macro_news(macro_headlines)
    if macro_headlines:
        data_sources.append("google_news_macro")
    log.info(
        "macro news: signal=%s adj=%+d events=%d",
        news_signal, news_adj, len(key_news_events),
    )

    # ── 2. Score each component ───────────────────────────────────────────────
    # Indicator max: 25 + 25 + 15 + 10 + 15 + 10 = 100
    # News adjustment: ±10 (only applied if a genuine shock detected)
    s_us10y, n_us10y   = _score_us10y(us10y)
    s_dxy,   n_dxy     = _score_dxy(dxy)
    s_vix,   n_vix     = _score_vix(vix)
    s_ivix,  n_ivix    = _score_india_vix(india_vix)
    s_inr,   n_inr     = _score_inr(inr_usd)
    s_rbi,   n_rbi     = _score_rbi_rate(repo)

    base_total = s_us10y + s_dxy + s_vix + s_ivix + s_inr + s_rbi
    total = max(0, min(100, base_total + news_adj))

    # ── 2b. P6-D-7: GIFT Nifty pre-market adjustment ─────────────────────────
    # Only applied when running within the pre-market window (06:30–09:15 IST)
    # to avoid stale futures prices influencing afternoon runs.
    gift_nifty_data: dict = {}
    try:
        from data.gift_nifty_fetcher import get_gift_nifty_signal, get_gift_nifty_macro_adjustment
        gift_nifty_data = get_gift_nifty_signal()
        if gift_nifty_data.get("is_pre_market") and gift_nifty_data.get("signal") != "UNAVAILABLE":
            gift_adj = get_gift_nifty_macro_adjustment(gift_nifty_data)
            if gift_adj != 0:
                total = max(0, min(100, total + gift_adj))
                log.info(
                    "GIFT Nifty %s %s → macro score adjusted by %+d (total=%d)",
                    gift_nifty_data.get("signal"), gift_nifty_data.get("signal_strength"),
                    gift_adj, total,
                )
            data_sources.append(f"gift_nifty_{gift_nifty_data.get('source', 'unknown')}")
    except Exception as exc:
        log.debug("GIFT Nifty integration skipped: %s", exc)

    # ── 3. Signal ─────────────────────────────────────────────────────────────
    if total >= 65:
        signal = "RISK_ON"
    elif total >= 40:
        signal = "NEUTRAL"
    else:
        signal = "RISK_OFF"

    # ── 4. Sector impacts ─────────────────────────────────────────────────────
    sector_impacts = _sector_impacts(us10y, dxy, india_vix, inr_usd, repo)

    detail = {
        "us10y":      {"value": us10y, "score": s_us10y, "note": n_us10y},
        "dxy":        {"value": dxy,   "score": s_dxy,   "note": n_dxy},
        "vix":        {"value": vix,   "score": s_vix,   "note": n_vix},
        "india_vix":  {"value": india_vix, "score": s_ivix, "note": n_ivix},
        "inr_usd":    {"value": inr_usd,   "score": s_inr,  "note": n_inr},
        "rbi_repo":   {"value": repo,      "score": s_rbi,  "note": n_rbi},
        "max_possible": 100,
        # ── News signal (new) ─────────────────────────────────────────────────
        # macro_news_signal flags major India macro events (PM announcements,
        # RBI policy surprises, budget, geopolitical shocks) that pure
        # quantitative indicators miss until they show up in VIX/INR/yields.
        "macro_news": {
            "signal":       news_signal,
            "score_adj":    news_adj,
            "key_events":   key_news_events,
            "headlines_scanned": len(macro_headlines),
        },
    }

    result = {
        "signal":               signal,
        "score":                total,
        "macro_news_signal":    news_signal,        # top-level shortcut for synthesiser
        "macro_news_events":    key_news_events,    # top-level shortcut for synthesiser
        "gift_nifty":           gift_nifty_data,    # P6-D-7: pre-market futures signal
        "detail":               detail,
        "sector_impacts":       sector_impacts,
        "data_sources":         list(dict.fromkeys(data_sources)),
        "agent_name":           AGENT_NAME,
    }

    try:
        _write_agent_performance()
    except Exception as exc:
        log.warning("Persisting agent run failed (non-critical): %s", exc)

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Sector-adjusted macro score  (P0-B fix)
# ──────────────────────────────────────────────────────────────────────────────

# Sector-to-canonical-key mapping for lookup in sector_impacts dict.
# Keys must match what _sector_impacts() returns.
_SECTOR_MACRO_KEY: dict[str, str] = {
    "IT":              "IT",
    "Technology":      "IT",
    "Software":        "IT",
    "Banking":         "BANKING",
    "Finance":         "BANKING",
    "NBFC":            "BANKING",
    "Insurance":       "BANKING",
    "Pharma":          "PHARMA",
    "Healthcare":      "PHARMA",
    "Oil & Gas":       "OIL_GAS",
    "Refineries":      "OIL_GAS",
    "Realty":          "REALTY",
    "Infrastructure":  "REALTY",
    "Construction":    "REALTY",
    "Auto":            "AUTO",
    "Automobile":      "AUTO",
    "Metals":          "METALS",
    "Mining":          "METALS",
    "Steel":           "METALS",
    "FMCG":            "FMCG",
    "Consumer Staples":"FMCG",
    "Consumer Goods":  "FMCG",
}

# Score adjustments applied when a sector's macro outlook is POSITIVE / NEGATIVE
_MACRO_SECTOR_BOOST  = +8   # points added to raw macro score for POSITIVE outlook
_MACRO_SECTOR_DRAG   = -8   # points deducted for NEGATIVE outlook
# NEUTRAL → no change


def get_sector_adjusted_macro_score(macro_result: dict, sector: str) -> dict:
    """
    Return a copy of *macro_result* with the macro score adjusted for
    sector-specific macro sensitivity.

    The raw macro score is a market-wide aggregate.  A RISK_ON macro score of 70
    means different things for an IT exporter (benefits from weak INR) vs an
    oil importer (hurt by weak INR).  This function adjusts the score by
    ±8 points based on the sector's specific macro outlook already computed
    in _sector_impacts(), then re-derives the signal.

    Usage
    -----
    In orchestrator / discovery_screener, call this AFTER the fundamental agent
    has returned the stock's sector:

        sector = fund_res.get("detail", {}).get("sector", "")
        adj_macro = get_sector_adjusted_macro_score(macro_result, sector)
        results["macro"] = adj_macro

    Parameters
    ----------
    macro_result : the dict returned by agents.macro.analyse()
    sector       : sector string from the fundamental agent (e.g. "IT", "Pharma")

    Returns
    -------
    dict — same shape as macro_result but with score, signal, and
           sector_adjusted=True / sector_key / sector_outlook added to detail.
    """
    if not macro_result or not sector:
        return macro_result

    # Already adjusted for this sector? Return as-is to avoid double-adjusting.
    if macro_result.get("sector_adjusted"):
        return macro_result

    # Resolve sector to canonical key
    sector_key = None
    if sector in _SECTOR_MACRO_KEY:
        sector_key = _SECTOR_MACRO_KEY[sector]
    else:
        sector_lower = sector.lower()
        for k, v in _SECTOR_MACRO_KEY.items():
            if k.lower() in sector_lower or sector_lower in k.lower():
                sector_key = v
                break

    if not sector_key:
        # Unknown sector — return unchanged with a note
        result = dict(macro_result)
        result["sector_adjusted"] = False
        result["sector_key"] = None
        return result

    # Get the sector's macro outlook from sector_impacts in macro_result
    sector_impacts = macro_result.get("sector_impacts", {})
    outlook = sector_impacts.get(sector_key, {}).get("outlook", "NEUTRAL")

    # Apply score adjustment
    base_score = int(macro_result.get("score", 50))
    if outlook == "POSITIVE":
        adj_score = min(100, base_score + _MACRO_SECTOR_BOOST)
    elif outlook == "NEGATIVE":
        adj_score = max(0, base_score + _MACRO_SECTOR_DRAG)
    else:
        adj_score = base_score

    # Re-derive signal from adjusted score
    if adj_score >= 65:
        adj_signal = "RISK_ON"
    elif adj_score >= 40:
        adj_signal = "NEUTRAL"
    else:
        adj_signal = "RISK_OFF"

    result = dict(macro_result)
    result["score"]           = adj_score
    result["signal"]          = adj_signal
    result["sector_adjusted"] = True
    result["sector_key"]      = sector_key
    result["sector_outlook"]  = outlook
    # Preserve original score for audit / debugging
    result["raw_macro_score"] = base_score

    return result


if __name__ == "__main__":
    import json as _json
    out = analyse()
    print(_json.dumps(out, indent=2, default=str))
