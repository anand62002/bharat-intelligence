"""
data/trendlyne_fetcher.py
=========================
Trendlyne fundamentals scraper — tier-2 fallback in the chain:
    screener.in  →  Trendlyne (this module)  →  yfinance

Parses the Trendlyne equity page for snapshot fundamental metrics and
DVM (Durability-Valuation-Momentum) scores without requiring a paid
subscription. Data is embedded as HTML attributes (`data-metrics` JSON
and `data-score` / `data-title` attributes) that are rendered server-side.

Data sources (all from a single page request):
  URL:  https://trendlyne.com/equity/{SYMBOL}/NSE/
  1. `data-metrics` JSON attribute — 15 snapshot financial metrics
  2. `data-score` + `data-title` attributes — DVM composite scores
  3. Regex fallbacks on page text for any metric not found via attributes

Public API:
  get_trendlyne_fundamentals(symbol) → dict
      Returns same schema as get_screener_data() with data_source="trendlyne_fallback".
      Covers: pe, ebitda_margin, revenue_growth, roce, roe, debt_equity,
              promoter_holding, fii_holding_pct, dii_holding_pct, market_cap,
              revenue_cagr_3y, eps_cagr_3y  (others set to None)
      Returns None on network/session failure.

  get_trendlyne_dvm(symbol) → dict | None
      Returns: {durability_score, valuation_score, momentum_score, composite_dvm}
      Returns None on failure.

Requires env vars (same as trendlyne_analyst_fetcher):
  TRENDLYNE_SESSION  (.trendlyne cookie value)
  TRENDLYNE_CSRF     (csrftoken cookie value)

Optional (enables auto-cookie-refresh on session expiry):
  TRENDLYNE_USER     (Trendlyne login email)
  TRENDLYNE_PASS     (Trendlyne login password)

Caching:
  Per-symbol in-process dict cache with 6-hour TTL (same as analyst fetcher).
  DVM and fundamentals share a single cached HTML page per symbol.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── re-use session/HTTP layer from analyst fetcher ────────────────────────────
# Both modules use the same Trendlyne cookies; piggy-back on the shared session
# and cookie-refresh logic rather than duplicating it.
try:
    from data.trendlyne_analyst_fetcher import _fetch_page as _tl_fetch_page
    _HAVE_ANALYST_FETCHER = True
except ImportError:
    _HAVE_ANALYST_FETCHER = False
    logger.warning(
        "trendlyne_fetcher: could not import _fetch_page from "
        "trendlyne_analyst_fetcher — will build own session"
    )

if not _HAVE_ANALYST_FETCHER:
    # Fallback: build a minimal session using the same env vars
    import os
    import requests as _requests

    _TL_SESS = os.getenv("TRENDLYNE_SESSION", "")
    _TL_CSRF = os.getenv("TRENDLYNE_CSRF", "")
    _UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    _own_session: Optional[_requests.Session] = None

    def _make_own_session() -> _requests.Session:
        s = _requests.Session()
        s.headers.update({
            "User-Agent": _UA,
            "Referer":    "https://trendlyne.com/",
            "Accept":     "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
        if _TL_SESS:
            s.cookies.set(".trendlyne", _TL_SESS, domain="trendlyne.com")
        if _TL_CSRF:
            s.cookies.set("csrftoken", _TL_CSRF, domain="trendlyne.com")
        return s

    def _tl_fetch_page(url: str) -> Optional[str]:  # type: ignore[misc]
        global _own_session
        if _own_session is None:
            _own_session = _make_own_session()
        try:
            resp = _own_session.get(url, timeout=20, allow_redirects=True)
            if resp.status_code == 200:
                return resp.text
            logger.warning("trendlyne_fetcher: HTTP %d for %s", resp.status_code, url)
        except Exception as exc:
            logger.warning("trendlyne_fetcher: request failed for %s: %s", url, exc)
        return None


_BASE_URL = "https://trendlyne.com"

# ── per-symbol page cache ─────────────────────────────────────────────────────
# { "RELIANCE": { "_ts": float, "_html": str, "fundamentals": dict, "dvm": dict } }
_page_cache: dict[str, dict] = {}
_CACHE_TTL = 3600 * 6  # 6 hours


# ──────────────────────────────────────────────────────────────────────────────
# URL resolution
# ──────────────────────────────────────────────────────────────────────────────

def _trendlyne_equity_url(symbol: str) -> str:
    """
    Return the Trendlyne equity page URL for an NSE symbol.
    Uses the /NSE/ suffix so Trendlyne resolves the symbol server-side.
    """
    clean = symbol.replace(".NS", "").replace(".BO", "").upper()
    return f"{_BASE_URL}/equity/{clean}/NSE/"


# ──────────────────────────────────────────────────────────────────────────────
# HTML parsing helpers
# ──────────────────────────────────────────────────────────────────────────────

def _safe_float(text: Optional[str]) -> Optional[float]:
    """Strip text artefacts and return a float, or None."""
    if not text:
        return None
    cleaned = re.sub(r"[₹,\s%]", "", str(text).strip())
    # Remove Cr / L / M / B suffixes to get bare number
    cleaned = re.sub(r"(Cr|cr|L|lakh|M|B)$", "", cleaned).strip()
    match = re.search(r"-?\d[\d.]*", cleaned)
    if match:
        try:
            return float(match.group())
        except ValueError:
            pass
    return None


def _parse_next_data_fundamentals(html: str) -> dict:
    """
    Strategy 0: extract fundamental metrics from the Next.js __NEXT_DATA__ blob.

    Trendlyne is a Next.js app; all server-side data is serialised into
        <script id="__NEXT_DATA__" type="application/json">{ ... }</script>
    This is the authoritative source now that `data-metrics` HTML attributes
    are no longer rendered server-side.

    We walk the entire JSON tree recursively (depth-limited to 15) and map
    any key whose name matches a known alias to our canonical metric name.
    Returns a flat dict of canonical_name → float (None omitted).
    """
    metrics: dict[str, float] = {}

    # Find the __NEXT_DATA__ script tag
    nd_start = html.find('"__NEXT_DATA__"')
    # More reliable: look for the script tag directly
    tag_start = html.find('<script id="__NEXT_DATA__"')
    if tag_start == -1:
        tag_start = html.find("<script id='__NEXT_DATA__'")
    if tag_start == -1:
        return metrics

    tag_end = html.find("</script>", tag_start)
    if tag_end == -1:
        return metrics

    json_start = html.find(">", tag_start) + 1
    raw_json = html[json_start:tag_end].strip()
    try:
        nd = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return metrics

    # Alias map: canonical_key → set of possible JSON key names (lower-cased)
    # Covers camelCase, snake_case, and abbreviated variants seen in Trendlyne's
    # Next.js data layer across multiple page versions.
    _ALIASES: dict[str, list[str]] = {
        "pe": [
            "pe", "pe_ratio", "peRatio", "price_earnings", "price_to_earnings",
            "trailingpe", "trailingPE", "pricetoearningsratio",
        ],
        "ebitda_margin": [
            "opm", "opm_pct", "opmPercent", "operating_profit_margin",
            "ebitda_margin", "ebitdaMargin", "ebitdamargins", "operatingmargin",
            "operatingMargin", "opmPercent", "operating_margin",
        ],
        "revenue_growth": [
            "revenue_growth", "revenueGrowth", "sales_growth", "salesGrowth",
            "revenue_growth_1y", "sales_growth_1y", "toplineGrowth",
            "revenuegrowthpct", "salesGrowthPct",
        ],
        "revenue_cagr_3y": [
            "revenue_cagr_3y", "revenueCagr3y", "sales_cagr_3y", "salesCagr3y",
            "revenueGrowth3y", "salesGrowth3y", "revenue_growth_3yr",
        ],
        "revenue_cagr_5y": [
            "revenue_cagr_5y", "revenueCagr5y", "sales_cagr_5y", "salesCagr5y",
            "revenueGrowth5y", "salesGrowth5y",
        ],
        "eps_cagr_3y": [
            "eps_cagr_3y", "epsCagr3y", "profit_cagr_3y", "profitCagr3y",
            "pat_cagr_3y", "earningsGrowth3y", "profitGrowth3y", "netprofitGrowth3y",
        ],
        "eps_cagr_5y": [
            "eps_cagr_5y", "epsCagr5y", "profit_cagr_5y", "profitCagr5y",
            "pat_cagr_5y", "earningsGrowth5y",
        ],
        "roce": [
            "roce", "rocePercent", "return_on_capital_employed",
            "returnOnCapitalEmployed", "returnoncapital",
        ],
        "roe": [
            "roe", "roePercent", "return_on_equity", "returnOnEquity",
            "returnOnEquityPercent",
        ],
        "debt_equity": [
            "de_ratio", "deRatio", "debt_equity", "debtEquity", "debtToEquity",
            "debt_to_equity", "debttoequity",
        ],
        "promoter_holding": [
            "promoter_holding", "promoterHolding", "promoterHoldingPct",
            "promoter_shareholding", "promoterShareholding",
        ],
        "promoter_pledging": [
            "promoter_pledging", "promoterPledging", "pledgingPct",
            "pledgedPct", "pledgepct",
        ],
        "fii_holding_pct": [
            "fii_holding", "fiiHolding", "fiiHoldingPct", "fii_pct",
            "fpiHolding", "foreign_institutional", "foreignInstitutional",
        ],
        "dii_holding_pct": [
            "dii_holding", "diiHolding", "diiHoldingPct", "dii_pct",
            "domestic_institutional", "domesticInstitutional",
        ],
        "market_cap": [
            "market_cap", "marketCap", "market_cap_cr", "marketCapCr",
            "marketCapitalisation", "mcap",
        ],
        "interest_coverage": [
            "interest_coverage", "interestCoverage", "interestCoverageRatio",
            "icr", "interest_cover",
        ],
    }

    # Build a flat lookup: alias_lower → canonical
    _alias_lookup: dict[str, str] = {}
    for canonical, aliases in _ALIASES.items():
        for alias in aliases:
            _alias_lookup[alias.lower()] = canonical

    def _walk(obj: object, depth: int = 0) -> None:
        if depth > 15 or not isinstance(obj, (dict, list)):
            return
        if isinstance(obj, list):
            for item in obj:
                _walk(item, depth + 1)
            return
        for k, v in obj.items():
            canonical = _alias_lookup.get(k.lower())
            if canonical and canonical not in metrics:
                # Value may be a bare number, string, or nested {"value": ...}
                raw_val = v
                if isinstance(v, dict):
                    raw_val = v.get("value") or v.get("val") or v.get("data")
                if raw_val is not None:
                    parsed = _safe_float(str(raw_val))
                    if parsed is not None:
                        metrics[canonical] = parsed
            elif isinstance(v, (dict, list)):
                _walk(v, depth + 1)

    _walk(nd)
    return metrics


def _parse_next_data_dvm(html: str) -> dict:
    """
    Strategy 0 for DVM scores: extract durability/valuation/momentum from
    the __NEXT_DATA__ JSON blob.  Returns dict with same keys as _parse_dvm_scores().
    """
    result: dict[str, Optional[float]] = {
        "durability_score": None,
        "valuation_score":  None,
        "momentum_score":   None,
        "composite_dvm":    None,
    }

    tag_start = html.find('<script id="__NEXT_DATA__"')
    if tag_start == -1:
        tag_start = html.find("<script id='__NEXT_DATA__'")
    if tag_start == -1:
        return result

    tag_end = html.find("</script>", tag_start)
    if tag_end == -1:
        return result

    json_start = html.find(">", tag_start) + 1
    try:
        nd = json.loads(html[json_start:tag_end].strip())
    except (json.JSONDecodeError, TypeError):
        return result

    _DVM_ALIASES = {
        "durability_score": ["durability", "durabilityScore", "durability_score", "d_score", "dscore"],
        "valuation_score":  ["valuation", "valuationScore", "valuation_score", "v_score", "vscore"],
        "momentum_score":   ["momentum", "momentumScore", "momentum_score", "m_score", "mscore"],
    }
    _dvm_lookup: dict[str, str] = {}
    for canonical, aliases in _DVM_ALIASES.items():
        for alias in aliases:
            _dvm_lookup[alias.lower()] = canonical

    def _walk_dvm(obj: object, depth: int = 0) -> None:
        if depth > 15 or not isinstance(obj, (dict, list)):
            return
        if isinstance(obj, list):
            for item in obj:
                _walk_dvm(item, depth + 1)
            return
        for k, v in obj.items():
            canonical = _dvm_lookup.get(k.lower())
            if canonical and result[canonical] is None:
                raw_val = v
                if isinstance(v, dict):
                    raw_val = v.get("value") or v.get("val") or v.get("score")
                if raw_val is not None:
                    parsed = _safe_float(str(raw_val))
                    if parsed is not None and 0 <= parsed <= 100:
                        result[canonical] = parsed
            elif isinstance(v, (dict, list)):
                _walk_dvm(v, depth + 1)

    _walk_dvm(nd)

    scores = [v for v in (result["durability_score"], result["valuation_score"],
                           result["momentum_score"]) if v is not None]
    if scores:
        result["composite_dvm"] = round(sum(scores) / len(scores), 1)

    return result


def _parse_data_metrics(soup: BeautifulSoup) -> dict:
    """
    Extract the `data-metrics` JSON attribute from the page.

    Trendlyne embeds financial parameters in an element like:
        <div id="parameters-widget" data-metrics='{"pe": 22.4, "opm": 15.0, ...}'>

    The JSON keys vary across page versions; we apply a comprehensive
    alias map so any known variant is captured.

    NOTE: As of mid-2025 Trendlyne migrated to Next.js and this attribute
    is no longer server-side rendered.  _parse_next_data_fundamentals() is
    now the primary parser; this function is kept as a legacy fallback.

    Returns a flat dict of normalised metric names → float.
    """
    metrics: dict[str, Optional[float]] = {}

    # Try every element that has a data-metrics attribute
    for el in soup.find_all(attrs={"data-metrics": True}):
        raw = el.get("data-metrics", "")
        if not raw:
            continue
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            continue

        if not isinstance(data, dict):
            continue

        # Alias map: canonical_key → list of possible Trendlyne JSON key names
        # Built from Trendlyne page inspection + common naming conventions.
        _ALIASES: dict[str, list[str]] = {
            "pe": [
                "pe", "pe_ratio", "price_earnings", "price_to_earnings",
                "trailing_pe", "trailingPE", "PE",
            ],
            "ebitda_margin": [
                "opm", "opm_pct", "operating_profit_margin", "ebitda_margin",
                "operating_margin", "operatingMargin", "ebitdaMargins",
                "opm_percent", "operating_profit_pct",
            ],
            "revenue_growth": [
                "revenue_growth", "sales_growth", "revenue_growth_1y",
                "sales_growth_1y", "revenue_growth_pct", "revenueGrowth",
                "topline_growth", "revenue_growth_ttm",
            ],
            "revenue_cagr_3y": [
                "revenue_cagr_3y", "sales_cagr_3y", "revenue_growth_3y",
                "sales_growth_3y", "revenue_cagr_3yr",
            ],
            "revenue_cagr_5y": [
                "revenue_cagr_5y", "sales_cagr_5y", "revenue_growth_5y",
                "sales_growth_5y", "revenue_cagr_5yr",
            ],
            "eps_cagr_3y": [
                "eps_cagr_3y", "profit_cagr_3y", "pat_cagr_3y",
                "earnings_growth_3y", "profit_growth_3y",
                "net_profit_growth_3y",
            ],
            "eps_cagr_5y": [
                "eps_cagr_5y", "profit_cagr_5y", "pat_cagr_5y",
                "earnings_growth_5y", "profit_growth_5y",
            ],
            "roce": [
                "roce", "return_on_capital_employed", "return_on_capital",
                "ROCE", "roce_pct",
            ],
            "roe": [
                "roe", "return_on_equity", "ROE", "roe_pct",
                "returnOnEquity",
            ],
            "debt_equity": [
                "de_ratio", "debt_equity", "debt_to_equity",
                "debtToEquity", "d_e_ratio", "debt_equity_ratio",
            ],
            "promoter_holding": [
                "promoter_holding", "promoter_pct", "promoter_holding_pct",
                "promoter_shareholding", "promoterHolding",
            ],
            "promoter_pledging": [
                "promoter_pledging", "promoter_pledging_pct", "pledging_pct",
                "pledged_pct", "pledgePct",
            ],
            "fii_holding_pct": [
                "fii_holding", "fii_holding_pct", "fii_pct",
                "fpi_holding", "foreign_institutional",
                "institutional_holding",  # combined; split later
            ],
            "dii_holding_pct": [
                "dii_holding", "dii_holding_pct", "dii_pct",
                "domestic_institutional",
            ],
            "market_cap": [
                "market_cap", "marketCap", "market_cap_cr",
                "market_capitalisation", "mcap",
            ],
            "interest_coverage": [
                "interest_coverage", "interest_coverage_ratio",
                "icr", "interest_cover",
            ],
        }

        for canonical, aliases in _ALIASES.items():
            for alias in aliases:
                if alias in data:
                    raw_val = data[alias]
                    # Value may be nested: {"value": "22.4", "label": "P/E"}
                    if isinstance(raw_val, dict):
                        raw_val = raw_val.get("value") or raw_val.get("val")
                    val = _safe_float(str(raw_val)) if raw_val is not None else None
                    if val is not None and canonical not in metrics:
                        metrics[canonical] = val
                        break  # found this metric — move to next canonical

        # If we found at least 3 fields, trust this element
        filled = sum(1 for v in metrics.values() if v is not None)
        if filled >= 3:
            break  # stop scanning more elements

    return metrics


def _parse_parameters_section(soup: BeautifulSoup) -> dict:
    """
    Fallback parser: scan the 'parameters' section or key-value pairs
    that Trendlyne renders as text inside specific containers.

    Trendlyne equity pages have a parameters card/section with rows like:
        P/E Ratio      22.4
        OPM %          15.0
        Rev Growth %   12.5
        ...
    We scan all elements looking for these label→value pairs.
    """
    metrics: dict[str, Optional[float]] = {}

    # Pattern mapping: (regex for label, canonical key, sign_flip_if_negative)
    _LABEL_PATTERNS = [
        (r"p/e|price.?earn", "pe"),
        (r"op.*marg|opm|ebitda.*marg|operating.*marg", "ebitda_margin"),
        (r"rev.*growth|sales.*growth|topline.*growth", "revenue_growth"),
        (r"revenue.*cagr.*3|sales.*cagr.*3|3.*yr.*rev.*cagr", "revenue_cagr_3y"),
        (r"revenue.*cagr.*5|sales.*cagr.*5|5.*yr.*rev.*cagr", "revenue_cagr_5y"),
        (r"eps.*cagr.*3|profit.*cagr.*3|pat.*cagr.*3|earn.*growth.*3", "eps_cagr_3y"),
        (r"eps.*cagr.*5|profit.*cagr.*5|pat.*cagr.*5", "eps_cagr_5y"),
        (r"roce|return.*capital", "roce"),
        (r"^roe$|return.*equity", "roe"),
        (r"debt.?(to.?)?equity|d/?e ratio", "debt_equity"),
        (r"promoter.*hold|promoter.*share", "promoter_holding"),
        (r"promoter.*pledg|pledg", "promoter_pledging"),
        (r"^fii|^fpi|foreign.*inst", "fii_holding_pct"),
        (r"^dii|domestic.*inst", "dii_holding_pct"),
        (r"interest.*cover", "interest_coverage"),
        (r"market.*cap", "market_cap"),
    ]

    # Try label-value adjacent element pairs
    for el in soup.find_all(["td", "th", "dt", "span", "div", "li"]):
        label = el.get_text(strip=True).lower()
        if not label or len(label) > 60:
            continue

        for pattern, canonical in _LABEL_PATTERNS:
            if re.search(pattern, label, re.IGNORECASE) and canonical not in metrics:
                # Look for value in sibling or next element
                for sibling in [el.find_next_sibling(), el.find_next_sibling("td"),
                                el.find_next_sibling("span"), el.find_next_sibling("div")]:
                    if sibling:
                        val = _safe_float(sibling.get_text(strip=True))
                        if val is not None:
                            metrics[canonical] = val
                            break
                break

    return metrics


def _parse_dvm_scores(soup: BeautifulSoup, html: str) -> dict:
    """
    Parse DVM (Durability-Valuation-Momentum) composite scores.

    Trendlyne renders these in elements with `data-score` attributes
    and `data-title` strings like:
        "Durability Score : 65, Valuation Score : 50, Momentum Score : 32"

    Also tries to find the circular/gauge score displays directly.
    Returns: {durability_score, valuation_score, momentum_score, composite_dvm}
    """
    result: dict[str, Optional[float]] = {
        "durability_score":  None,
        "valuation_score":   None,
        "momentum_score":    None,
        "composite_dvm":     None,
    }

    # Strategy 1: data-title attribute with all three scores on one element
    for el in soup.find_all(attrs={"data-title": True}):
        title = el.get("data-title", "")
        if not title:
            continue
        # "Durability Score : 65, Valuation Score : 50, Momentum Score : 32"
        dur_m  = re.search(r"[Dd]urability\s*[Ss]core\s*[:\-]\s*(\d+)", title)
        val_m  = re.search(r"[Vv]aluation\s*[Ss]core\s*[:\-]\s*(\d+)", title)
        mom_m  = re.search(r"[Mm]omentum\s*[Ss]core\s*[:\-]\s*(\d+)", title)
        if dur_m:
            result["durability_score"] = float(dur_m.group(1))
        if val_m:
            result["valuation_score"] = float(val_m.group(1))
        if mom_m:
            result["momentum_score"] = float(mom_m.group(1))
        if any(result[k] is not None for k in ("durability_score", "valuation_score", "momentum_score")):
            break

    # Strategy 2: data-score attribute (individual score elements)
    if result["durability_score"] is None:
        for el in soup.find_all(attrs={"data-score": True}):
            score_val = _safe_float(el.get("data-score", ""))
            score_label = (
                el.get("data-label", "") or
                el.get("data-type", "") or
                el.get("title", "") or
                el.get_text(strip=True)
            ).lower()
            if score_val is None:
                continue
            if "durability" in score_label and result["durability_score"] is None:
                result["durability_score"] = score_val
            elif "valuation" in score_label and result["valuation_score"] is None:
                result["valuation_score"] = score_val
            elif "momentum" in score_label and result["momentum_score"] is None:
                result["momentum_score"] = score_val

    # Strategy 3: regex scan on raw HTML for the score values
    if result["durability_score"] is None:
        patterns = [
            (r"[Dd]urability[^:\d]{0,20}[:\-]\s*(\d{1,3})", "durability_score"),
            (r"[Vv]aluation[^:\d]{0,20}[:\-]\s*(\d{1,3})", "valuation_score"),
            (r"[Mm]omentum[^:\d]{0,20}[:\-]\s*(\d{1,3})", "momentum_score"),
        ]
        for pattern, key in patterns:
            m = re.search(pattern, html)
            if m and result[key] is None:
                val = float(m.group(1))
                if 0 <= val <= 100:  # sanity check — scores are 0–100
                    result[key] = val

    # Strategy 4: look for DVM scores in JSON script tags
    for script in soup.find_all("script"):
        src = script.get_text()
        if "durability" not in src.lower() and "dvm" not in src.lower():
            continue
        for m in re.finditer(r'\{[^{}]{10,}\}', src):
            try:
                blob = json.loads(m.group())
                for k, v in blob.items():
                    kl = k.lower()
                    if "durability" in kl and result["durability_score"] is None:
                        val = _safe_float(str(v))
                        if val is not None and 0 <= val <= 100:
                            result["durability_score"] = val
                    elif "valuation" in kl and result["valuation_score"] is None:
                        val = _safe_float(str(v))
                        if val is not None and 0 <= val <= 100:
                            result["valuation_score"] = val
                    elif "momentum" in kl and result["momentum_score"] is None:
                        val = _safe_float(str(v))
                        if val is not None and 0 <= val <= 100:
                            result["momentum_score"] = val
            except (json.JSONDecodeError, TypeError):
                pass

    # Compute composite (simple average of non-None scores)
    scores = [v for v in (result["durability_score"], result["valuation_score"],
                           result["momentum_score"]) if v is not None]
    if scores:
        result["composite_dvm"] = round(sum(scores) / len(scores), 1)

    return result


def _fetch_and_cache(symbol: str) -> Optional[dict]:
    """
    Fetch the Trendlyne equity page for `symbol`, parse it, and cache the
    results.  Returns the cache entry dict or None on failure.
    """
    clean = symbol.replace(".NS", "").replace(".BO", "").upper()
    now = time.time()

    # Cache hit
    if clean in _page_cache:
        entry = _page_cache[clean]
        if now - entry["_ts"] < _CACHE_TTL:
            return entry

    url = _trendlyne_equity_url(clean)
    logger.info("trendlyne_fetcher: fetching %s", url)
    html = _tl_fetch_page(url)

    if not html:
        logger.warning("trendlyne_fetcher: no HTML for symbol %s", clean)
        return None

    soup = BeautifulSoup(html, "html.parser")

    # Strategy 0: __NEXT_DATA__ JSON blob (primary since Trendlyne migrated to Next.js)
    metrics_nd  = _parse_next_data_fundamentals(html)
    dvm_nd      = _parse_next_data_dvm(html)

    filled_nd = sum(1 for v in metrics_nd.values() if v is not None)
    logger.debug("trendlyne_fetcher: __NEXT_DATA__ yielded %d metrics for %s", filled_nd, clean)

    # Strategy 1+2: legacy data-metrics attribute + label-text scan (fallback)
    metrics_primary   = _parse_data_metrics(soup)
    metrics_secondary = _parse_parameters_section(soup)

    # Merge all strategies: __NEXT_DATA__ wins, then data-metrics, then text scan
    merged: dict = {
        **metrics_secondary,
        **{k: v for k, v in metrics_primary.items() if v is not None},
        **metrics_nd,   # __NEXT_DATA__ is most authoritative
    }

    # DVM: merge Next.js data with legacy HTML attribute strategies
    dvm_legacy = _parse_dvm_scores(soup, html)
    # __NEXT_DATA__ wins for fields it found; legacy fills gaps
    dvm: dict = {
        k: (dvm_nd.get(k) if dvm_nd.get(k) is not None else dvm_legacy.get(k))
        for k in dvm_legacy
    }

    # Market cap: Trendlyne often shows it in Crores — convert to absolute ₹
    # (matches yfinance.marketCap scale)
    raw_mcap = merged.get("market_cap")
    if raw_mcap is not None and raw_mcap < 1e7:
        # Likely in Crores (e.g. 45000 Cr) → convert to absolute
        merged["market_cap"] = int(raw_mcap * 1e7)

    # fii_holding_pct might hold combined institutional; split 60/40 if dii absent
    if merged.get("fii_holding_pct") and not merged.get("dii_holding_pct"):
        combined = merged["fii_holding_pct"]
        merged["fii_holding_pct"] = round(combined * 0.60, 2)
        merged["dii_holding_pct"] = round(combined * 0.40, 2)

    entry: dict = {
        "_ts":          now,
        "_html":        html,
        "fundamentals": merged,
        "dvm":          dvm,
    }
    _page_cache[clean] = entry
    return entry


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def get_trendlyne_fundamentals(symbol: str) -> Optional[dict]:
    """
    Return fundamental data for `symbol` matching the get_screener_data() schema.

    Fields populated (best-effort; None where unavailable):
      pe, ebitda_margin, revenue_growth, revenue_cagr_3y, revenue_cagr_5y,
      eps_cagr_3y, eps_cagr_5y, roce, roe, debt_equity, promoter_holding,
      promoter_pledging, fii_holding_pct, dii_holding_pct, market_cap,
      interest_coverage, sector (None — Trendlyne doesn't expose in page attrs)

    Fields always None (not available without JS execution or subscription):
      revenue_growth_qoq, ocf_margin

    Returns None if the page cannot be fetched or Trendlyne session is invalid.
    """
    entry = _fetch_and_cache(symbol)
    if entry is None:
        return None

    m = entry["fundamentals"]
    if not m:
        return None

    # Validate: need at least 2 key fields to be useful
    key_fields = ("pe", "roce", "roe", "revenue_growth", "ebitda_margin")
    populated = sum(1 for f in key_fields if m.get(f) is not None)
    if populated < 2:
        logger.warning(
            "trendlyne_fetcher: insufficient data for %s (only %d key fields)",
            symbol, populated,
        )
        return None

    result = {
        "pe":                 m.get("pe"),
        "revenue_growth":     m.get("revenue_growth"),
        "ebitda_margin":      m.get("ebitda_margin"),
        "debt_equity":        m.get("debt_equity"),
        "roce":               m.get("roce"),
        "roe":                m.get("roe"),
        "promoter_holding":   m.get("promoter_holding"),
        "promoter_pledging":  m.get("promoter_pledging"),
        "revenue_growth_qoq": None,       # not available without JS
        "revenue_cagr_3y":    m.get("revenue_cagr_3y"),
        "revenue_cagr_5y":    m.get("revenue_cagr_5y"),
        "eps_cagr_3y":        m.get("eps_cagr_3y"),
        "eps_cagr_5y":        m.get("eps_cagr_5y"),
        "interest_coverage":  m.get("interest_coverage"),
        "fii_holding_pct":    m.get("fii_holding_pct"),
        "dii_holding_pct":    m.get("dii_holding_pct"),
        "ocf_margin":         None,       # not available without JS
        "market_cap":         m.get("market_cap"),
        "sector":             None,       # not in Trendlyne page attrs
        "data_source":        "trendlyne_fallback",
    }

    clean = symbol.replace(".NS", "").replace(".BO", "").upper()
    logger.info(
        "trendlyne_fetcher: %s → pe=%s roce=%s roe=%s revg=%s (trendlyne_fallback)",
        clean,
        result["pe"], result["roce"], result["roe"], result["revenue_growth"],
    )
    return result


def get_trendlyne_dvm(symbol: str) -> Optional[dict]:
    """
    Return DVM (Durability-Valuation-Momentum) scores for `symbol`.

    Returns:
        {
          durability_score:  float 0–100  (business quality / moat proxy)
          valuation_score:   float 0–100  (value vs peers / sector)
          momentum_score:    float 0–100  (price + earnings momentum)
          composite_dvm:     float 0–100  (simple average of non-None scores)
        }
    Returns None if no scores could be parsed.
    """
    entry = _fetch_and_cache(symbol)
    if entry is None:
        return None

    dvm = entry["dvm"]
    if dvm.get("composite_dvm") is None:
        logger.debug("trendlyne_fetcher: no DVM scores found for %s", symbol)
        return None

    return dvm


def get_upcoming_earnings(symbol: str) -> Optional[dict]:
    """
    Parse board meeting / results date from Trendlyne equity page.

    Trendlyne shows upcoming board meeting dates and result dates in sections
    labelled "Board Meeting" or "Result Date" (HTML text scan + regex).

    Returns:
        {
            "date":      str   — ISO date string "YYYY-MM-DD"
            "source":    str   — "trendlyne_board_meeting" | "trendlyne_result_date"
            "confirmed": bool  — True if an explicit board meeting date was found
            "raw_text":  str   — raw date string from page (for debugging)
        }
    Returns None if no upcoming date was found or page could not be fetched.
    """
    import re as _re
    from datetime import date as _date, datetime as _datetime

    entry = _fetch_and_cache(symbol)
    if entry is None:
        return None

    html  = entry.get("_html", "")
    soup  = BeautifulSoup(html, "html.parser")
    today = _date.today()

    # ── Month name → number (handles both Jan and January) ───────────────────
    _MONTHS = {
        "jan": 1,  "feb": 2,  "mar": 3,  "apr": 4,
        "may": 5,  "jun": 6,  "jul": 7,  "aug": 8,
        "sep": 9,  "oct": 10, "nov": 11, "dec": 12,
        "january": 1, "february": 2, "march": 3, "april": 4,
        "june": 6, "july": 7, "august": 8, "september": 9,
        "october": 10, "november": 11, "december": 12,
    }

    def _parse_date_text(text: str) -> Optional[_date]:
        """Try multiple date formats: DD-Mon-YYYY, DD/MM/YYYY, YYYY-MM-DD, DD Mon YYYY."""
        text = text.strip()
        # ISO
        m = _re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
        if m:
            try:
                return _date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                pass
        # DD-Mon-YYYY or DD Mon YYYY
        m = _re.match(r"(\d{1,2})[-\s/]([A-Za-z]+)[-\s/](\d{4})", text)
        if m:
            mon = _MONTHS.get(m.group(2).lower()[:3])
            if mon:
                try:
                    return _date(int(m.group(3)), mon, int(m.group(1)))
                except ValueError:
                    pass
        # DD/MM/YYYY
        m = _re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
        if m:
            try:
                return _date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            except ValueError:
                pass
        return None

    # ── Scan for board meeting / result date labels ───────────────────────────
    _BOARD_LABELS = _re.compile(
        r"board\s*meeting|result\s*date|results?\s*on|q[1-4]\s*result",
        _re.IGNORECASE,
    )
    _DATE_PATTERN  = _re.compile(
        r"\b(\d{1,2}[-/\s][A-Za-z]{3,9}[-/\s]\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})\b"
    )

    best_date: Optional[_date] = None
    best_source = "trendlyne_result_date"
    best_raw    = ""

    # Strategy 1: find label elements, then look for adjacent dates
    for el in soup.find_all(string=_BOARD_LABELS):
        # Search surrounding text (parent + siblings)
        parent = el.parent if el.parent else None
        if parent is None:
            continue
        search_text = parent.get_text(" ", strip=True)
        # Expand to grandparent if not enough text
        if len(search_text) < 10 and parent.parent:
            search_text = parent.parent.get_text(" ", strip=True)

        for m in _DATE_PATTERN.finditer(search_text):
            d = _parse_date_text(m.group(1))
            if d and d >= today:
                is_board = bool(_re.search(r"board\s*meeting", search_text, _re.IGNORECASE))
                if best_date is None or d < best_date:
                    best_date   = d
                    best_source = "trendlyne_board_meeting" if is_board else "trendlyne_result_date"
                    best_raw    = m.group(1)

    # Strategy 2: raw HTML scan for "Board Meeting" near a date string
    if best_date is None:
        for m in _re.finditer(
            r"(?:Board\s*Meeting|Result\s*Date|Results?\s*On)[^<]{0,80}"
            r"(\d{1,2}[-/\s][A-Za-z]{3,9}[-/\s]\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})",
            html, _re.IGNORECASE,
        ):
            d = _parse_date_text(m.group(1))
            if d and d >= today:
                context   = m.group(0)
                is_board  = bool(_re.search(r"board\s*meeting", context, _re.IGNORECASE))
                if best_date is None or d < best_date:
                    best_date   = d
                    best_source = "trendlyne_board_meeting" if is_board else "trendlyne_result_date"
                    best_raw    = m.group(1)

    if best_date is None:
        logger.debug("get_upcoming_earnings(%s): no upcoming date found on Trendlyne page", symbol)
        return None

    logger.info(
        "get_upcoming_earnings(%s): found %s on %s (raw=%s)",
        symbol, best_source, best_date, best_raw,
    )
    return {
        "date":      str(best_date),
        "source":    best_source,
        "confirmed": best_source == "trendlyne_board_meeting",
        "raw_text":  best_raw,
    }


def clear_cache(symbol: Optional[str] = None) -> None:
    """
    Invalidate the page cache.
    Pass a symbol to clear only that entry, or no args to clear all.
    """
    if symbol is None:
        _page_cache.clear()
        logger.debug("trendlyne_fetcher: full cache cleared")
    else:
        clean = symbol.replace(".NS", "").replace(".BO", "").upper()
        _page_cache.pop(clean, None)
        logger.debug("trendlyne_fetcher: cache cleared for %s", clean)


# ──────────────────────────────────────────────────────────────────────────────
# CLI quick-test
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import json as _json

    sym = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    print(f"\n=== Trendlyne Fundamentals: {sym} ===")
    fund = get_trendlyne_fundamentals(sym)
    if fund:
        for k, v in fund.items():
            if k not in ("_ts", "_html"):
                print(f"  {k:<25} {v}")
    else:
        print("  [no data returned]")

    print(f"\n=== Trendlyne DVM: {sym} ===")
    dvm = get_trendlyne_dvm(sym)
    if dvm:
        for k, v in dvm.items():
            print(f"  {k:<25} {v}")
    else:
        print("  [no DVM scores found]")
