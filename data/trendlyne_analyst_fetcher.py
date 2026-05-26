"""
data/trendlyne_analyst_fetcher.py
===================================
Scrapes Trendlyne stock pages for analyst consensus target prices,
buy/hold/sell rating distribution, and EPS estimates.

Data source:
  Primary:  https://trendlyne.com/equity/{SYMBOL}/analyst-targets/
  Fallback: https://trendlyne.com/equity/{SYMBOL}/

Requires env vars (same as trendlyne_fno_fetcher):
  TRENDLYNE_SESSION  (.trendlyne cookie value)
  TRENDLYNE_CSRF     (csrftoken cookie value)

Optional (enables auto-cookie-refresh on session expiry):
  TRENDLYNE_USER     (Trendlyne login email)
  TRENDLYNE_PASS     (Trendlyne login password)

Public API:
  get_analyst_targets(symbol)  →  dict with keys:
      consensus_target     — median analyst target price (₹)
      target_high          — highest analyst target (₹)
      target_low           — lowest analyst target (₹)
      analyst_count        — number of analysts covering the stock
      strong_buy           — count of Strong Buy ratings
      buy                  — count of Buy ratings
      hold                 — count of Hold ratings
      sell                 — count of Sell / Underperform ratings
      buy_pct              — (strong_buy + buy) / total (0–100)
      consensus_rating     — BUY | HOLD | SELL derived from buy_pct
      upside_to_consensus  — (consensus_target - current_price) / current_price * 100
      eps_current_yr       — consensus EPS estimate for current FY (₹)
      eps_next_yr          — consensus EPS estimate for next FY (₹)
      revenue_current_yr   — consensus revenue estimate (₹ Cr) for current FY
      source               — "trendlyne_analyst"
      error                — None or error string

Memory design:
  Per-symbol in-process dict cache with 6-hour TTL.
  Cookies are refreshed once per session if a login redirect is detected
  and TRENDLYNE_USER + TRENDLYNE_PASS env vars are set.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── credentials ──────────────────────────────────────────────────────────────
_TL_SESS = os.getenv("TRENDLYNE_SESSION", "")
_TL_CSRF = os.getenv("TRENDLYNE_CSRF", "")
_TL_USER = os.getenv("TRENDLYNE_USER", "")
_TL_PASS = os.getenv("TRENDLYNE_PASS", "")

_BASE_URL = "https://trendlyne.com"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# ── in-process per-symbol cache ───────────────────────────────────────────────
# { symbol: { ...result_dict, "_ts": float } }
_cache: dict[str, dict] = {}
_CACHE_TTL = 3600 * 6  # 6 hours

# ── session (shared across calls, refreshed on 302→/login) ───────────────────
_session: Optional[requests.Session] = None
_cookies_refreshed_this_process = False  # attempt once per process


# ──────────────────────────────────────────────────────────────────────────────
# Session / cookie management
# ──────────────────────────────────────────────────────────────────────────────

def _make_session() -> requests.Session:
    """Build a requests.Session pre-loaded with Trendlyne cookies and proxy."""
    global _TL_SESS, _TL_CSRF
    # Re-read env vars so a process-level refresh is picked up
    _TL_SESS = os.getenv("TRENDLYNE_SESSION", _TL_SESS)
    _TL_CSRF = os.getenv("TRENDLYNE_CSRF", _TL_CSRF)

    s = requests.Session()
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

    # Apply outbound proxy (SCRAPERAPI_KEY / FIXIE_URL / HTTPS_PROXY)
    # Bypasses Railway IP blocks — session cookies are preserved in proxy mode
    try:
        from data.proxy_session import apply_proxy_to_session, proxy_configured
        apply_proxy_to_session(s)
        if not proxy_configured():
            logger.warning(
                "Trendlyne session: no proxy configured — may be blocked from "
                "Railway (set SCRAPERAPI_KEY or FIXIE_URL env var)"
            )
    except ImportError:
        pass

    return s


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = _make_session()
    return _session


def _try_refresh_cookies() -> bool:
    """
    Attempt to re-authenticate using TRENDLYNE_USER + TRENDLYNE_PASS.
    Updates the module-level globals _TL_SESS, _TL_CSRF and rebuilds
    the shared session.  Returns True on success, False otherwise.
    Called at most once per process to avoid hammering the login endpoint.
    """
    global _TL_SESS, _TL_CSRF, _session, _cookies_refreshed_this_process

    if _cookies_refreshed_this_process:
        return False  # already tried this process
    _cookies_refreshed_this_process = True

    if not _TL_USER or not _TL_PASS:
        logger.warning(
            "Trendlyne session expired. Set TRENDLYNE_USER + TRENDLYNE_PASS "
            "for auto-refresh, or manually update TRENDLYNE_SESSION."
        )
        return False

    logger.info("Attempting Trendlyne cookie refresh via login …")
    try:
        s = requests.Session()
        s.headers.update({"User-Agent": _UA})

        # Step 1: GET the login page to grab a fresh CSRF token
        login_page = s.get(f"{_BASE_URL}/login/", timeout=15)
        csrf_match = re.search(
            r'csrfmiddlewaretoken["\s]+value["\s=]+([A-Za-z0-9]+)',
            login_page.text,
        )
        if not csrf_match:
            # Fall back to cookie-based CSRF
            csrf_token = s.cookies.get("csrftoken", "")
        else:
            csrf_token = csrf_match.group(1)

        if not csrf_token:
            logger.warning("Could not extract CSRF token from Trendlyne login page")
            return False

        # Step 2: POST credentials
        resp = s.post(
            f"{_BASE_URL}/login/",
            data={
                "username":           _TL_USER,
                "password":           _TL_PASS,
                "csrfmiddlewaretoken": csrf_token,
                "next":               "/",
            },
            headers={
                "Referer":    f"{_BASE_URL}/login/",
                "X-CSRFToken": csrf_token,
            },
            timeout=20,
            allow_redirects=True,
        )

        # Successful login → session cookie is set
        new_sess = s.cookies.get(".trendlyne", "")
        new_csrf = s.cookies.get("csrftoken", "")

        if not new_sess:
            logger.warning("Trendlyne login returned no session cookie — check credentials")
            return False

        _TL_SESS = new_sess
        _TL_CSRF = new_csrf
        _session = _make_session()
        logger.info("Trendlyne cookies refreshed successfully")
        return True

    except Exception as exc:
        logger.warning("Trendlyne cookie refresh failed: %s", exc)
        return False


# ──────────────────────────────────────────────────────────────────────────────
# HTTP fetch helpers
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_page(url: str, *, retry_on_login: bool = True) -> Optional[str]:
    """
    Fetch a Trendlyne page, handling:
      • 302 redirect to /login/  → attempt cookie refresh once then retry
      • Non-200 responses
      • Network errors

    Returns HTML string or None on failure.
    """
    s = _get_session()
    try:
        resp = s.get(url, timeout=20, allow_redirects=False)

        # Redirect to login page — session expired
        if resp.status_code in (301, 302):
            loc = resp.headers.get("Location", "")
            if "login" in loc:
                logger.info("Trendlyne redirected to login for %s", url)
                if retry_on_login and _try_refresh_cookies():
                    return _fetch_page(url, retry_on_login=False)
                return None
            # Other redirects (e.g. trailing slash) — follow manually
            resp = s.get(f"{_BASE_URL}{loc}" if loc.startswith("/") else loc,
                         timeout=20)

        if resp.status_code == 200:
            return resp.text
        if resp.status_code == 404:
            logger.debug("Trendlyne 404: %s", url)
            return None

        if resp.status_code == 405:
            # 405 = Method Not Allowed on a GET request means either:
            # (a) Railway IP is blocked by Trendlyne WAF (returns 405 to confuse scrapers)
            # (b) URL structure changed on Trendlyne's side
            # Try alternative URL patterns before giving up.
            logger.warning("Trendlyne HTTP 405 for %s — trying alternative URL patterns", url)
            alt_urls = _get_alt_urls(url)
            for alt_url in alt_urls:
                try:
                    alt_resp = s.get(alt_url, timeout=20, allow_redirects=True)
                    if alt_resp.status_code == 200:
                        logger.info("Trendlyne alt URL worked: %s", alt_url)
                        return alt_resp.text
                    logger.debug("Trendlyne alt URL %s → %d", alt_url, alt_resp.status_code)
                except Exception:
                    pass
            logger.warning(
                "Trendlyne HTTP 405 — all URL patterns failed. "
                "IP likely blocked by Railway. Set SCRAPERAPI_KEY or FIXIE_URL."
            )
            return None

        logger.warning("Trendlyne HTTP %d for %s", resp.status_code, url)
        return None

    except requests.RequestException as exc:
        logger.warning("Trendlyne request failed for %s: %s", url, exc)
        return None


def _get_alt_urls(original_url: str) -> list[str]:
    """
    Generate alternative URL patterns to try when original returns 405.
    Trendlyne occasionally restructures their equity page URLs.
    """
    # Extract symbol from URL like https://trendlyne.com/equity/RELIANCE/NSE/
    import re
    m = re.search(r"/equity/([^/]+)/NSE/?", original_url)
    if not m:
        return []
    symbol = m.group(1)
    return [
        # With trailing slash vs without
        f"https://trendlyne.com/equity/{symbol}/NSE",
        # Analyst targets sub-page (sometimes works when equity root is blocked)
        f"https://trendlyne.com/equity/{symbol}/analyst-targets/",
        # Alternative path structure used in some Trendlyne versions
        f"https://trendlyne.com/stocks/{symbol}/NSE/",
    ]


# ──────────────────────────────────────────────────────────────────────────────
# HTML parsing helpers
# ──────────────────────────────────────────────────────────────────────────────

def _safe_number(text: Optional[str]) -> Optional[float]:
    """Extract the first number (possibly decimal) from a text string."""
    if not text:
        return None
    # Remove ₹, commas, spaces, % signs
    cleaned = re.sub(r"[₹,\s%]", "", text.strip())
    match = re.search(r"-?\d[\d.]*", cleaned)
    if match:
        try:
            return float(match.group())
        except ValueError:
            pass
    return None


def _parse_json_from_script(html: str, key: str) -> Optional[dict]:
    """
    Many Trendlyne pages embed data as JSON in a <script> tag.
    Tries to extract a dict keyed by `key` from window.__INITIAL_STATE__
    or a similar pattern.
    """
    # window.__DATA__ = {...} or window.pageData = {...}
    patterns = [
        rf'window\.__INITIAL_STATE__\s*=\s*(\{{.*?\}});',
        rf'"{key}"\s*:\s*(\{{[^}}]*\}})',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
    return None


def _parse_analyst_targets_page(html: str) -> dict:
    """
    Parse the analyst-targets sub-page.

    Trendlyne's analyst-targets page structure (as of 2025):
    ─────────────────────────────────────────────────────────
    • A "Analyst Ratings" section with:
        - A bar/donut chart section that has labels "Strong Buy", "Buy", "Hold", "Sell"
          and their counts in nearby <span> or <td> elements.
        - Consensus target price in a prominent <span> or <div>
        - High / Low targets
        - Number of analysts

    The exact selectors vary by page version; we try multiple approaches
    and take the first that returns a non-None value.
    """
    result: dict = {}
    soup = BeautifulSoup(html, "html.parser")

    # ── Strategy 0: Next.js __NEXT_DATA__ / inline JSON blob ─────────────────
    # Trendlyne is a Next.js app — all page data is serialised in a
    # <script id="__NEXT_DATA__"> tag.  This is the most reliable source.
    next_script = soup.find("script", {"id": "__NEXT_DATA__"})
    if next_script:
        try:
            nd = json.loads(next_script.get_text())
            # Walk the pageProps tree looking for analyst data keys
            def _walk(obj, depth=0):
                if depth > 10 or not isinstance(obj, (dict, list)):
                    return
                if isinstance(obj, list):
                    for item in obj:
                        _walk(item, depth + 1)
                    return
                for k, v in obj.items():
                    kl = k.lower()
                    if kl in ("targetprice", "consensustarget", "analysttarget", "mediantarget"):
                        vn = _safe_number(str(v)) if v else None
                        if vn and vn > 10:
                            result.setdefault("consensus_target", vn)
                    elif kl in ("hightarget", "targethigh", "maxprice"):
                        vn = _safe_number(str(v)) if v else None
                        if vn and vn > 10:
                            result.setdefault("target_high", vn)
                    elif kl in ("lowtarget", "targetlow", "minprice"):
                        vn = _safe_number(str(v)) if v else None
                        if vn and vn > 10:
                            result.setdefault("target_low", vn)
                    elif kl in ("analystcount", "numanalysts", "totalanalysts", "coveragecount"):
                        vn = _safe_number(str(v)) if v else None
                        if vn and 1 <= vn <= 200:
                            result.setdefault("analyst_count", int(vn))
                    elif kl in ("strongbuycount", "strongbuy"):
                        vn = _safe_number(str(v)) if v else None
                        if vn is not None:
                            result.setdefault("strong_buy", int(vn))
                    elif kl in ("buycount", "buys"):
                        vn = _safe_number(str(v)) if v else None
                        if vn is not None:
                            result.setdefault("buy", int(vn))
                    elif kl in ("holdcount", "holds", "neutral"):
                        vn = _safe_number(str(v)) if v else None
                        if vn is not None:
                            result.setdefault("hold", int(vn))
                    elif kl in ("sellcount", "sells", "underperformcount"):
                        vn = _safe_number(str(v)) if v else None
                        if vn is not None:
                            result.setdefault("sell", int(vn))
                    elif isinstance(v, (dict, list)):
                        _walk(v, depth + 1)
            _walk(nd)
        except (json.JSONDecodeError, TypeError, RecursionError):
            pass

    # ── Strategy 1: Look for structured data tables ───────────────────────────
    # Some pages render a table with rows: Strong Buy | Buy | Hold | Sell | Total
    if not result.get("analyst_count"):  # skip if Strategy 0 already got it
        rating_map = {"strong_buy": 0, "buy": 0, "hold": 0, "sell": 0}
        total_analysts = 0

        for row in soup.select("table tr, .analyst-row, [class*='rating-row'], [class*='analyst-rating']"):
            text = row.get_text(separator=" ", strip=True).lower()
            cells = row.find_all(["td", "th", "span", "div"], recursive=False)
            count_text = cells[-1].get_text(strip=True) if cells else ""
            count = _safe_number(count_text)
            if count is None:
                continue
            if "strong buy" in text:
                rating_map["strong_buy"] = int(count)
                total_analysts += int(count)
            elif "strong sell" in text or "underperform" in text:
                rating_map["sell"] += int(count)
                total_analysts += int(count)
            elif "buy" in text and "strong" not in text:
                rating_map["buy"] = int(count)
                total_analysts += int(count)
            elif "hold" in text or "neutral" in text:
                rating_map["hold"] = int(count)
                total_analysts += int(count)
            elif "sell" in text and "strong" not in text and "under" not in text:
                rating_map["sell"] += int(count)
                total_analysts += int(count)

        if total_analysts > 0:
            result.update({k: result.get(k) or v for k, v in rating_map.items()})
            result.setdefault("analyst_count", total_analysts)

    # ── Strategy 2: Look for consensus target price spans ────────────────────
    # Common class names across Trendlyne page versions
    if "consensus_target" not in result:
        target_selectors = [
            "[class*='target-price']",
            "[class*='consensus']",
            "[class*='analyst-target']",
            "[class*='price-target']",
            "[class*='targetPrice']",
            "[data-label*='target']",
            "[data-label*='Target']",
            "[data-key*='target']",
        ]
        for sel in target_selectors:
            el = soup.select_one(sel)
            if el:
                val = _safe_number(el.get_text(strip=True))
                if val and val > 10:
                    result["consensus_target"] = val
                    break

    # ── Strategy 3: Regex scan for "Target Price ₹X,XXX" patterns ───────────
    if "consensus_target" not in result:
        for pat in [
            r"[Tt]arget\s*[Pp]rice[^₹\d]*[₹]?\s*([\d,]+(?:\.\d+)?)",
            r"[Cc]onsensus\s*[Tt]arget[^₹\d]*[₹]?\s*([\d,]+(?:\.\d+)?)",
            r"[Mm]edian\s*[Tt]arget[^₹\d]*[₹]?\s*([\d,]+(?:\.\d+)?)",
            r'"targetPrice"\s*:\s*"?([\d.]+)"?',
            r'"consensusTarget"\s*:\s*"?([\d.]+)"?',
            r'"analystTarget"\s*:\s*"?([\d.]+)"?',
        ]:
            m = re.search(pat, html)
            if m:
                val = _safe_number(m.group(1))
                if val and val > 10:
                    result["consensus_target"] = val
                    break

    # ── Strategy 4: High / Low targets ───────────────────────────────────────
    for pat, key in [
        (r"[Hh]igh\s*[Tt]arget[^₹\d]*[₹]?\s*([\d,]+(?:\.\d+)?)", "target_high"),
        (r"[Ll]ow\s*[Tt]arget[^₹\d]*[₹]?\s*([\d,]+(?:\.\d+)?)",  "target_low"),
    ]:
        if key not in result:
            m = re.search(pat, html)
            if m:
                val = _safe_number(m.group(1))
                if val and val > 10:
                    result[key] = val

    # ── Strategy 5: Number-of-analysts ───────────────────────────────────────
    if "analyst_count" not in result or result["analyst_count"] == 0:
        for pat in [
            r"(\d+)\s*[Aa]nalysts?",
            r"[Cc]overed by\s*(\d+)",
            r"(\d+)\s*[Ee]stimates?",
        ]:
            m = re.search(pat, html)
            if m:
                n = int(m.group(1))
                if 1 <= n <= 200:
                    result.setdefault("analyst_count", n)
                    break

    # ── Strategy 6: EPS estimates ─────────────────────────────────────────────
    for pat, key in [
        (r"EPS\s*(?:FY\d{2}|current\s*year|CY)[^₹\d]*[₹]?\s*([\d,]+(?:\.\d+)?)", "eps_current_yr"),
        (r"EPS\s*(?:FY\d{2}|next\s*year|NY)[^₹\d]*[₹]?\s*([\d,]+(?:\.\d+)?)", "eps_next_yr"),
    ]:
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            val = _safe_number(m.group(1))
            if val is not None:
                result.setdefault(key, val)

    # ── Strategy 7: Embedded JSON in <script> tags ────────────────────────────
    for script in soup.find_all("script"):
        src = script.get_text()
        if "analystTarget" not in src and "targetPrice" not in src:
            continue
        for m in re.finditer(r'\{[^{}]{20,}\}', src):
            try:
                blob = json.loads(m.group())
                if "targetPrice" in blob or "analystTarget" in blob:
                    tp = blob.get("targetPrice") or blob.get("analystTarget")
                    if tp:
                        result.setdefault("consensus_target", float(tp))
                if "buyCount" in blob or "buy_count" in blob:
                    result.setdefault("buy", int(blob.get("buyCount") or blob.get("buy_count", 0)))
                if "holdCount" in blob or "hold_count" in blob:
                    result.setdefault("hold", int(blob.get("holdCount") or blob.get("hold_count", 0)))
                if "sellCount" in blob or "sell_count" in blob:
                    result.setdefault("sell", int(blob.get("sellCount") or blob.get("sell_count", 0)))
            except (json.JSONDecodeError, ValueError, TypeError):
                pass

    return result


def _parse_main_stock_page(html: str) -> dict:
    """
    Fallback parser for the main stock page.
    The main page has a compact analyst summary widget — less detail than
    the analyst-targets sub-page but often enough for the core fields.
    """
    result: dict = {}
    soup = BeautifulSoup(html, "html.parser")

    # Look for analyst summary widget / card
    widget_selectors = [
        "[class*='analyst']",
        "[class*='recommendation']",
        "[id*='analyst']",
        ".research-recommendation",
    ]
    widget_html = html  # fallback: search whole page
    for sel in widget_selectors:
        el = soup.select_one(sel)
        if el:
            widget_html = str(el)
            break

    return _parse_analyst_targets_page(widget_html)


def _current_price_from_page(html: str) -> Optional[float]:
    """Extract the current traded price from a Trendlyne stock page."""
    soup = BeautifulSoup(html, "html.parser")
    for sel in [
        "[class*='current-price']",
        "[class*='last-price']",
        "[class*='ltp']",
        "[id*='current-price']",
    ]:
        el = soup.select_one(sel)
        if el:
            val = _safe_number(el.get_text(strip=True))
            if val and val > 1:
                return val
    # Regex fallback
    for pat in [
        r'[Cc]urrent\s*[Pp]rice[^₹\d]*[₹]?\s*([\d,]+(?:\.\d+)?)',
        r'"lastPrice"\s*:\s*"?([\d.]+)',
        r'"currentPrice"\s*:\s*"?([\d.]+)',
    ]:
        m = re.search(pat, html)
        if m:
            val = _safe_number(m.group(1))
            if val and val > 1:
                return val
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Symbol normalisation
# ──────────────────────────────────────────────────────────────────────────────

def _plain(symbol: str) -> str:
    """Strip .NS / .BO suffix, uppercase."""
    return symbol.replace(".NS", "").replace(".BO", "").upper()


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def get_analyst_targets(symbol: str, force_refresh: bool = False) -> dict:
    """
    Return analyst consensus data for a Trendlyne-covered NSE stock.

    Scrapes:
      1. https://trendlyne.com/equity/{SYMBOL}/analyst-targets/
      2. Falls back to https://trendlyne.com/equity/{SYMBOL}/ if (1) returns no data

    Returns dict with keys:
      consensus_target     — median analyst target price (₹), or None
      target_high          — highest analyst target (₹), or None
      target_low           — lowest analyst target (₹), or None
      analyst_count        — number of analysts covering the stock, or None
      strong_buy           — count of Strong Buy ratings
      buy                  — count of Buy ratings
      hold                 — count of Hold ratings
      sell                 — count of Sell/Underperform ratings
      buy_pct              — (strong_buy + buy) / total as %, or None
      consensus_rating     — "BUY" | "HOLD" | "SELL" | None
      upside_to_consensus  — % upside from current price to consensus target, or None
      eps_current_yr       — consensus current-FY EPS estimate (₹), or None
      eps_next_yr          — consensus next-FY EPS estimate (₹), or None
      revenue_current_yr   — consensus current-FY revenue estimate (₹ Cr), or None
      current_price_tl     — current price from Trendlyne page (₹), or None
      source               — "trendlyne_analyst"
      error                — None or error string
    """
    sym = _plain(symbol)
    now = time.time()

    # ── Cache check ───────────────────────────────────────────────────────────
    if not force_refresh and sym in _cache:
        cached = _cache[sym]
        if now - cached.get("_ts", 0) < _CACHE_TTL:
            logger.debug("get_analyst_targets(%s): cache hit", sym)
            return {k: v for k, v in cached.items() if k != "_ts"}

    empty: dict = {
        "consensus_target":    None,
        "target_high":         None,
        "target_low":          None,
        "analyst_count":       None,
        "strong_buy":          0,
        "buy":                 0,
        "hold":                0,
        "sell":                0,
        "buy_pct":             None,
        "consensus_rating":    None,
        "upside_to_consensus": None,
        "eps_current_yr":      None,
        "eps_next_yr":         None,
        "revenue_current_yr":  None,
        "current_price_tl":    None,
        "source":              "trendlyne_analyst",
        "error":               None,
    }

    try:
        # ── Fetch analyst-targets sub-page ────────────────────────────────────
        at_url   = f"{_BASE_URL}/equity/{sym}/analyst-targets/"
        at_html  = _fetch_page(at_url)
        parsed   = {}

        if at_html:
            parsed = _parse_analyst_targets_page(at_html)
            # Also try to get current price from this page
            empty["current_price_tl"] = _current_price_from_page(at_html)

        # ── Fallback: main stock page ─────────────────────────────────────────
        if not parsed.get("consensus_target") and not parsed.get("analyst_count"):
            main_url  = f"{_BASE_URL}/equity/{sym}/"
            main_html = _fetch_page(main_url)
            if main_html:
                parsed = _parse_main_stock_page(main_html)
                if empty["current_price_tl"] is None:
                    empty["current_price_tl"] = _current_price_from_page(main_html)
                logger.debug("get_analyst_targets(%s): used main page fallback", sym)

        if not parsed:
            empty["error"] = f"No analyst data found for {sym} on Trendlyne"
            _cache[sym] = {**empty, "_ts": now}
            return empty

        # ── Merge parsed data into result ─────────────────────────────────────
        result = {**empty, **parsed}

        # ── Derived fields ────────────────────────────────────────────────────
        sb   = int(result.get("strong_buy") or 0)
        buy  = int(result.get("buy") or 0)
        hold = int(result.get("hold") or 0)
        sell = int(result.get("sell") or 0)
        total = sb + buy + hold + sell
        # NOTE: do NOT fall back to analyst_count when buy/hold/sell breakdown is
        # unavailable — that would set buy_pct=0 and force a fake SELL rating.
        # If we have analyst_count but no breakdown, leave buy_pct=None (honest).

        if total > 0:
            buy_total = sb + buy
            result["buy_pct"] = round(buy_total / total * 100, 1)
            result["analyst_count"] = result.get("analyst_count") or total
            if result["buy_pct"] >= 60:
                result["consensus_rating"] = "BUY"
            elif result["buy_pct"] >= 35:
                result["consensus_rating"] = "HOLD"
            else:
                result["consensus_rating"] = "SELL"

        # Upside to consensus
        if result.get("consensus_target") and result.get("current_price_tl"):
            cp = result["current_price_tl"]
            tp = result["consensus_target"]
            if cp > 0:
                result["upside_to_consensus"] = round((tp - cp) / cp * 100, 2)

        result["source"] = "trendlyne_analyst"
        result["error"] = None

        logger.info(
            "get_analyst_targets(%s): target=₹%s, rating=%s, analysts=%s, buy_pct=%s%%",
            sym,
            result.get("consensus_target"),
            result.get("consensus_rating"),
            result.get("analyst_count"),
            result.get("buy_pct"),
        )

        _cache[sym] = {**result, "_ts": now}
        return result

    except Exception as exc:
        logger.error("get_analyst_targets(%s) failed: %s", sym, exc)
        empty["error"] = str(exc)
        _cache[sym] = {**empty, "_ts": now}
        return empty


def clear_cache(symbol: Optional[str] = None) -> None:
    """
    Clear the in-process cache.
      clear_cache()          → clears all symbols
      clear_cache("HDFC")    → clears one symbol
    """
    global _cache
    if symbol:
        _cache.pop(_plain(symbol), None)
    else:
        _cache.clear()
    logger.debug("Trendlyne analyst cache cleared (symbol=%s)", symbol or "ALL")


# ──────────────────────────────────────────────────────────────────────────────
# Interpretation helper (consumed by fundamental.py)
# ──────────────────────────────────────────────────────────────────────────────

def interpret_analyst_targets(targets: dict, our_upside_pct: Optional[float] = None) -> dict:
    """
    Return a human-readable interpretation of the analyst targets dict.

    Args:
        targets:        Result of get_analyst_targets()
        our_upside_pct: Our own DCF/PE-based upside estimate for comparison

    Returns dict:
        signal          — ALIGNED | DIVERGENT_BULLISH | DIVERGENT_BEARISH | UNKNOWN
        summary         — One-sentence plain-language description
        consensus_note  — e.g. "23 analysts: 65% Buy, target ₹3,200 (+18%)"
        divergence_note — e.g. "Our DCF: +32% vs Street: +18% — we are more bullish"
    """
    ct      = targets.get("consensus_target")
    rating  = targets.get("consensus_rating")
    buy_pct = targets.get("buy_pct")
    upside  = targets.get("upside_to_consensus")
    n       = targets.get("analyst_count")

    if not ct and not rating:
        return {
            "signal":         "UNKNOWN",
            "summary":        "No analyst consensus data available from Trendlyne.",
            "consensus_note": None,
            "divergence_note": None,
        }

    # Build consensus note
    parts: list[str] = []
    if n:
        parts.append(f"{n} analyst{'s' if n != 1 else ''}")
    if buy_pct is not None:
        parts.append(f"{buy_pct:.0f}% Buy")
    if ct:
        upside_str = f" ({upside:+.1f}%)" if upside is not None else ""
        parts.append(f"target ₹{ct:,.0f}{upside_str}")
    consensus_note = ": ".join(["Street consensus"] + [", ".join(parts)]) if parts else None

    # Signal
    if rating == "BUY" and (upside is None or upside > 0):
        signal = "ALIGNED" if (our_upside_pct is None or our_upside_pct > 0) else "DIVERGENT_BEARISH"
    elif rating == "SELL" or (upside is not None and upside < -5):
        signal = "DIVERGENT_BEARISH" if (our_upside_pct is None or our_upside_pct > 5) else "ALIGNED"
    else:
        signal = "ALIGNED"

    # Divergence note
    divergence_note = None
    if our_upside_pct is not None and upside is not None:
        diff = our_upside_pct - upside
        if abs(diff) >= 10:
            direction = "more bullish" if diff > 0 else "more bearish"
            divergence_note = (
                f"Our model: {our_upside_pct:+.1f}% vs Street: {upside:+.1f}% "
                f"— we are {direction} than consensus"
            )
            if diff > 0:
                signal = "DIVERGENT_BULLISH"
            else:
                signal = "DIVERGENT_BEARISH"

    summary_parts: list[str] = []
    if consensus_note:
        summary_parts.append(consensus_note)
    if divergence_note:
        summary_parts.append(divergence_note)

    return {
        "signal":          signal,
        "summary":         " | ".join(summary_parts) or "Analyst data available.",
        "consensus_note":  consensus_note,
        "divergence_note": divergence_note,
    }


# ──────────────────────────────────────────────────────────────────────────────
# CLI smoke test
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json
    import sys
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")

    symbols = sys.argv[1:] or ["RELIANCE", "HDFCBANK", "TCS", "INFY"]
    for sym in symbols:
        print(f"\n{'='*55}")
        print(f"  {sym}")
        print('='*55)
        result = get_analyst_targets(sym, force_refresh=True)
        print(json.dumps(result, indent=2, default=str))
        interp = interpret_analyst_targets(result, our_upside_pct=20.0)
        print(f"\nInterpretation:")
        print(json.dumps(interp, indent=2))
