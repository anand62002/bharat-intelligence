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
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dotenv import load_dotenv

load_dotenv()

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from data.fetchers import get_inr_usd, get_india_vix  # noqa: E402

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

    # ── 2. Score each component ───────────────────────────────────────────────
    # Max possible: 25 + 25 + 15 + 10 + 15 + 10 = 100
    s_us10y, n_us10y   = _score_us10y(us10y)
    s_dxy,   n_dxy     = _score_dxy(dxy)
    s_vix,   n_vix     = _score_vix(vix)
    s_ivix,  n_ivix    = _score_india_vix(india_vix)
    s_inr,   n_inr     = _score_inr(inr_usd)
    s_rbi,   n_rbi     = _score_rbi_rate(repo)

    total = s_us10y + s_dxy + s_vix + s_ivix + s_inr + s_rbi
    total = max(0, min(100, total))

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
    }

    result = {
        "signal":         signal,
        "score":          total,
        "detail":         detail,
        "sector_impacts": sector_impacts,
        "data_sources":   list(dict.fromkeys(data_sources)),
        "agent_name":     AGENT_NAME,
    }

    try:
        _write_agent_performance()
    except Exception as exc:
        log.warning("Persisting agent run failed (non-critical): %s", exc)

    return result


if __name__ == "__main__":
    import json as _json
    out = analyse()
    print(_json.dumps(out, indent=2, default=str))
