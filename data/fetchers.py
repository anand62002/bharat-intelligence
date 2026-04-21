"""
data/fetchers.py — Bharat Intelligence India Market Data Fetchers
All functions return None on failure and log errors to stderr.
"""

import json
import logging
import random
import re
import time
from datetime import datetime
from typing import Any, Callable, Optional, Tuple, Type

import feedparser
import requests
import yfinance as yf
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


# ─── yfinance retry utility ──────────────────────────────────────────────────

# Errors that indicate a transient problem worth retrying.
# - ConnectionError / Timeout: network blip or DNS hiccup
# - HTTPError 429: Yahoo rate-limit (back off and retry)
# - HTTPError 5xx: Yahoo server error (transient)
# - JSONDecodeError: occasionally yfinance returns a malformed response
_YF_RETRYABLE: Tuple[Type[Exception], ...] = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    json.JSONDecodeError,
)

# Errors indicating a permanent failure — retrying would not help.
# - HTTP 404: symbol does not exist in Yahoo Finance
# - HTTP 400: bad request (malformed symbol)
# - ValueError / TypeError from downstream yfinance parsing
_YF_PERMANENT: Tuple[Type[Exception], ...] = (
    ValueError,
    TypeError,
)


def yf_fetch_with_retry(
    fn: Callable[..., Any],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 1.0,
    **kwargs: Any,
) -> Any:
    """
    Execute a callable (typically a yfinance API call) with exponential-backoff
    retry for transient failures.

    Retry policy
    ------------
    - Retries on: ConnectionError, Timeout, HTTP 429, HTTP 5xx, JSONDecodeError
    - Does NOT retry on: HTTP 404 / 400 (permanent), ValueError, TypeError
    - Delays: base_delay * 2^attempt + uniform jitter [0, 0.5s]
      e.g., with base_delay=1.0: ~1.0s, ~2.0s, ~4.0s

    Usage
    -----
        # .history() call with retry
        df = yf_fetch_with_retry(yf.Ticker("TCS.NS").history, period="1y")

        # .info property via lambda
        info = yf_fetch_with_retry(lambda: yf.Ticker("TCS.NS").info)

    Returns the callable's return value on success.
    Raises the last exception if all retries are exhausted.
    Raises immediately (no retry) for permanent errors.
    """
    last_exc: Exception = RuntimeError("yf_fetch_with_retry: no attempts made")

    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)

        except requests.exceptions.HTTPError as exc:
            resp = getattr(exc, "response", None)
            status = resp.status_code if resp is not None else 0
            if status in (429,) or (500 <= status < 600):
                last_exc = exc
                delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
                log.debug(
                    "yfinance HTTP %s (attempt %d/%d) — retrying in %.1fs",
                    status, attempt + 1, max_retries, delay,
                )
                time.sleep(delay)
            else:
                # 404, 400, etc. — permanent failure, raise immediately
                raise

        except _PERMANENT as exc:
            raise

        except _YF_RETRYABLE as exc:
            last_exc = exc
            delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
            log.debug(
                "yfinance transient %s (attempt %d/%d) — retrying in %.1fs",
                type(exc).__name__, attempt + 1, max_retries, delay,
            )
            time.sleep(delay)

        except Exception as exc:
            # Unknown error — treat as transient for one retry only,
            # then let it propagate (avoids swallowing genuine bugs).
            if attempt < 1:
                last_exc = exc
                delay = base_delay + random.uniform(0, 0.5)
                log.debug(
                    "yfinance unknown error %s (attempt %d/%d) — retrying once in %.1fs",
                    type(exc).__name__, attempt + 1, max_retries, delay,
                )
                time.sleep(delay)
            else:
                raise

    raise last_exc


# Private alias for the permanent-error tuple used in the except clause above.
# Defined after the function because it is only used internally.
_PERMANENT = _YF_PERMANENT


# ─── 1. OHLCV ────────────────────────────────────────────────────────────────

def get_ohlcv(symbol: str, period: str = "1y"):
    """
    Fetch OHLCV data for a symbol using yfinance.

    Args:
        symbol: Ticker symbol, e.g. "RELIANCE.NS", "TCS.NS"
        period: yfinance period string — "1d","5d","1mo","3mo","6mo","1y","2y","5y","10y","ytd","max"

    Returns:
        pandas.DataFrame with columns [Open, High, Low, Close, Volume], or None on failure.
    """
    from data.symbol_map import resolve_yf, is_excluded
    if is_excluded(symbol):
        log.debug("get_ohlcv: skipping excluded symbol %s", symbol)
        return None
    resolved = resolve_yf(symbol)
    if resolved is None:
        log.debug("get_ohlcv: %s resolved to None (excluded)", symbol)
        return None
    if resolved != symbol:
        log.debug("get_ohlcv: symbol resolved %s → %s", symbol, resolved)

    try:
        ticker = yf.Ticker(resolved)
        df = yf_fetch_with_retry(ticker.history, period=period)
        if df.empty:
            log.warning("get_ohlcv: no data returned for %s (period=%s)", resolved, period)
            return None
        df.index = df.index.tz_localize(None)
        return df[["Open", "High", "Low", "Close", "Volume"]]
    except Exception as e:
        log.error("get_ohlcv(%s): %s", resolved, e)
        return None


# ─── 2. NSE FII/DII ──────────────────────────────────────────────────────────

# ── FII/DII source-specific headers ──────────────────────────────────────────

_NSE_FII_HEADERS = {
    **_HEADERS,
    "Accept":         "application/json, text/plain, */*",
    "Referer":        "https://www.nseindia.com/market-data/fii-dii-activity",
    "Accept-Encoding":"gzip, deflate, br",
    "Cache-Control":  "no-cache",
    "Pragma":         "no-cache",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

_BSE_HEADERS = {
    **_HEADERS,
    "Accept":  "application/json, text/plain, */*",
    "Origin":  "https://www.bseindia.com",
    "Referer": "https://www.bseindia.com/markets/equity/EQReports/FiidiiActivity.aspx",
}

_MC_HEADERS = {
    **_HEADERS,
    "Referer": "https://www.moneycontrol.com/",
}


def _try_nse_fii_dii() -> dict | None:
    """
    Source 1: NSE `fiidiiTradeReact` JSON endpoint.
    Requires cookie-seeding from homepage; prone to 403 but fastest when working.
    """
    url = "https://www.nseindia.com/api/fiidiiTradeReact"
    try:
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=_NSE_FII_HEADERS, timeout=10)
        resp = session.get(url, headers=_NSE_FII_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return None
        latest = data[0]
        return {
            "date":    latest.get("date") or latest.get("DATE") or "",
            "fii_net": float(latest.get("fiiNet") or latest.get("FII_NET") or 0),
            "dii_net": float(latest.get("diiNet") or latest.get("DII_NET") or 0),
            "source":  "nse",
        }
    except Exception as exc:
        log.warning("FII/DII NSE failed: %s", exc)
        return None


def _try_bse_fii_dii() -> dict | None:
    """
    Source 2: BSE India FiiDiiActivity API.
    Different server infrastructure from NSE — primary fallback when NSE blocks.
    Returns the same underlying SEBI-reported data, just via BSE's feed.
    """
    url = "https://api.bseindia.com/BseIndiaAPI/api/FiiDiiActivity/w"
    try:
        session = requests.Session()
        # BSE also benefits from a homepage visit for cookie seeding
        session.get("https://www.bseindia.com", headers=_BSE_HEADERS, timeout=10)
        resp = session.get(url, headers=_BSE_HEADERS, timeout=12)
        resp.raise_for_status()
        data = resp.json()

        # BSE can return either a list or a dict with "Table" key
        rows = data if isinstance(data, list) else data.get("Table", data.get("table", []))
        if not rows:
            return None

        # Find most recent FII and DII rows
        fii_net = dii_net = None
        date_str = ""
        for row in rows:
            client = str(
                row.get("ClientType") or row.get("CLIENT_TYPE") or
                row.get("Category") or row.get("CATEGORY") or ""
            ).upper()
            net_raw = (
                row.get("NetActivity") or row.get("NET_ACTIVITY") or
                row.get("NetValue")    or row.get("NET_VALUE")    or
                row.get("Net")         or 0
            )
            date_raw = (
                row.get("TRDDTE") or row.get("TradeDate") or
                row.get("TRADE_DATE") or row.get("Date") or ""
            )
            try:
                net_val = float(str(net_raw).replace(",", ""))
            except (ValueError, TypeError):
                continue

            if not date_str and date_raw:
                date_str = str(date_raw)
            if "FII" in client or "FPI" in client:
                fii_net = net_val
            elif "DII" in client or "MF" in client or "DOM" in client:
                dii_net = net_val

        if fii_net is None and dii_net is None:
            return None

        return {
            "date":    date_str,
            "fii_net": fii_net or 0.0,
            "dii_net": dii_net or 0.0,
            "source":  "bse",
        }
    except Exception as exc:
        log.warning("FII/DII BSE failed: %s", exc)
        return None


def _try_moneycontrol_fii_dii() -> dict | None:
    """
    Source 3: Moneycontrol FII/DII activity page (HTML scrape).
    Consumer-facing page — rarely blocked, stable table structure.
    Falls back to this only when both NSE and BSE APIs are unavailable.
    """
    url = "https://www.moneycontrol.com/stocks/marketinfo/fiidii_activity/index.html"
    try:
        resp = requests.get(url, headers=_MC_HEADERS, timeout=12)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Moneycontrol table headers: Date | FII Buy | FII Sell | FII Net | DII Buy | DII Sell | DII Net
        table = soup.find("table", {"id": "fiidii"}) or soup.find(
            "table", class_=lambda c: c and ("fiidii" in c.lower() or "mctable" in c.lower())
        )
        if not table:
            # Try first substantial table with > 5 columns
            for t in soup.find_all("table"):
                headers = [th.get_text(strip=True).lower() for th in t.find_all("th")]
                if any("fii" in h for h in headers) and any("dii" in h for h in headers):
                    table = t
                    break

        if not table:
            return None

        # Find column indices for FII net and DII net
        header_row = table.find("tr")
        if not header_row:
            return None
        headers = [th.get_text(strip=True).lower() for th in header_row.find_all(["th", "td"])]

        fii_net_idx = dii_net_idx = date_idx = None
        for i, h in enumerate(headers):
            if "date" in h:
                date_idx = i
            elif "fii" in h and "net" in h:
                fii_net_idx = i
            elif "dii" in h and "net" in h:
                dii_net_idx = i

        if fii_net_idx is None or dii_net_idx is None:
            return None

        # First data row = most recent date
        rows = table.find_all("tr")[1:]
        for row in rows:
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cells) <= max(fii_net_idx, dii_net_idx):
                continue
            try:
                fii_net = float(cells[fii_net_idx].replace(",", "").replace("−", "-"))
                dii_net = float(cells[dii_net_idx].replace(",", "").replace("−", "-"))
                date_str = cells[date_idx] if date_idx is not None else ""
                return {
                    "date":    date_str,
                    "fii_net": fii_net,
                    "dii_net": dii_net,
                    "source":  "moneycontrol",
                }
            except (ValueError, IndexError):
                continue

        return None
    except Exception as exc:
        log.warning("FII/DII Moneycontrol failed: %s", exc)
        return None


def get_nse_fii_dii() -> dict | None:
    """
    Fetch latest daily FII/DII net flow data (₹ Crores).

    Tries three sources in order until one succeeds:
      1. NSE `fiidiiTradeReact` API  — official, fast, but prone to 403
      2. BSE India FiiDiiActivity API — different infra, same SEBI data
      3. Moneycontrol HTML scrape    — consumer page, rarely blocked

    Returns:
        {"date": str, "fii_net": float, "dii_net": float, "source": str}
        or None if all three sources fail.

    Note: All three sources report the SAME underlying data (SEBI filings).
    The "source" key is informational — callers should treat the values
    identically regardless of which source succeeded.
    """
    for attempt_fn in (_try_nse_fii_dii, _try_bse_fii_dii, _try_moneycontrol_fii_dii):
        result = attempt_fn()
        if result:
            log.info("FII/DII data fetched via %s: FII=%.0f DII=%.0f Cr",
                     result["source"], result["fii_net"], result["dii_net"])
            return result

    log.error("get_nse_fii_dii: all three sources (NSE, BSE, Moneycontrol) failed")
    return None


# ─── 3. MCX PRICES ───────────────────────────────────────────────────────────

def get_mcx_prices() -> dict | None:
    """
    Fetch Gold, Crude Oil, and Silver spot/futures prices via yfinance.

    Returns:
        dict with keys: gold ($/oz), crude ($/bbl), silver ($/oz), or None on failure.
    """
    tickers = {"gold": "GC=F", "crude": "CL=F", "silver": "SI=F"}
    result = {}
    try:
        for name, sym in tickers.items():
            try:
                ticker = yf.Ticker(sym)
                hist = yf_fetch_with_retry(ticker.history, period="1d")
                if not hist.empty:
                    result[name] = round(float(hist["Close"].iloc[-1]), 2)
                else:
                    info = yf_fetch_with_retry(lambda t=ticker: t.info)
                    result[name] = round(float(info.get("regularMarketPrice") or info.get("previousClose") or 0), 2)
            except Exception as inner:
                log.warning("get_mcx_prices: failed for %s (%s): %s", name, sym, inner)
                result[name] = None

        if all(v is None for v in result.values()):
            return None
        return result
    except Exception as e:
        log.error("get_mcx_prices: %s", e)
        return None


# ─── 4. RSS HEADLINES ────────────────────────────────────────────────────────

# Static broad-market feeds (keyword-filtered per symbol inside get_rss_headlines)
# Note: Business Standard and Livemint block automated requests; removed.
_RSS_FEEDS = [
    ("ET Markets",    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("ET Auto",       "https://economictimes.indiatimes.com/industry/auto/rssfeeds/1286551815.cms"),
    ("Moneycontrol",  "https://www.moneycontrol.com/rss/business.xml"),
    ("Hindu BizLine", "https://www.thehindubusinessline.com/markets/feeder/default.rss"),
]

# Google News RSS — dynamic per symbol; always returns symbol-specific headlines
_GOOGLE_NEWS_RSS_TMPL = (
    "https://news.google.com/rss/search"
    "?q={keyword}+NSE+stock+India"
    "&hl=en-IN&gl=IN&ceid=IN:en"
)


def get_rss_headlines(symbol: str) -> list | None:
    """
    Fetch headlines from static market RSS feeds + a dynamic Google News RSS
    feed for the specific symbol.

    Args:
        symbol: NSE/BSE ticker, e.g. "MARUTI.NS" or "TCS"

    Returns:
        List of dicts [{title, source, published, url}], or None if every
        feed request failed (network-level failure).
    """
    from urllib.parse import quote_plus

    keyword = symbol.replace(".NS", "").replace(".BO", "").strip().upper()

    # Build the full feed list: static feeds + 1 dynamic Google News feed
    feeds_to_try = list(_RSS_FEEDS) + [
        ("Google News", _GOOGLE_NEWS_RSS_TMPL.format(keyword=quote_plus(keyword))),
    ]

    results: list[dict] = []
    any_feed_ok = False

    for source_name, feed_url in feeds_to_try:
        try:
            feed = feedparser.parse(feed_url)
            # bozo=True means malformed XML, but entries may still be present
            if feed.bozo and not feed.entries:
                log.warning("get_rss_headlines: feed parse error for %s", source_name)
                continue
            any_feed_ok = True

            for entry in feed.entries:
                title   = entry.get("title", "").strip()
                summary = entry.get("summary", "")

                # For Google News the feed is already symbol-specific;
                # for static feeds we still keyword-filter.
                is_google = source_name == "Google News"
                if not is_google and (
                    keyword.lower() not in title.lower()
                    and keyword.lower() not in summary.lower()
                ):
                    continue

                published = entry.get("published", "")
                try:
                    if entry.get("published_parsed"):
                        published = str(datetime(*entry.published_parsed[:6]).date())
                except Exception:
                    pass

                results.append({
                    "title":     title,
                    "source":    source_name,
                    "published": published,
                    "url":       entry.get("link", ""),
                })
        except Exception as exc:
            log.warning("get_rss_headlines: error parsing %s: %s", source_name, exc)

    if not any_feed_ok:
        return None
    return results


# ─── 5. SCREENER DATA ────────────────────────────────────────────────────────

def get_screener_data(symbol: str) -> dict | None:
    """
    Scrape key fundamentals from screener.in for an NSE-listed company.

    Args:
        symbol: NSE symbol without exchange suffix, e.g. "RELIANCE", "TCS"

    Returns:
        dict with keys:
          pe                  — Price/Earnings ratio
          revenue_growth      — Revenue YoY growth % (TTM or 1-year)
          ebitda_margin       — EBITDA / OPM margin %
          debt_equity         — Debt/Equity ratio
          roce                — Return on Capital Employed %
          promoter_holding    — Promoter shareholding %
          promoter_pledging   — Promoter pledged shares %
          revenue_growth_qoq  — Quarter-on-quarter revenue growth %
          revenue_cagr_3y     — 3-year compounded sales growth %
          revenue_cagr_5y     — 5-year compounded sales growth %
          eps_cagr_3y         — 3-year compounded profit growth %
          eps_cagr_5y         — 5-year compounded profit growth %
          roe                 — Return on equity % (from top-ratios bar)
          interest_coverage   — Interest coverage ratio (from top-ratios bar)
          ocf_margin          — Operating cash flow / revenue % (from cash flow table)
        Values are floats or None if not found. Returns None on request failure.
    """
    from data.symbol_map import resolve_screener, is_excluded
    if is_excluded(symbol):
        log.debug("get_screener_data: skipping excluded symbol %s", symbol)
        return None
    slug = resolve_screener(symbol)
    if slug != symbol.replace(".NS", "").replace(".BO", "").upper():
        log.debug("get_screener_data: slug resolved %s → %s", symbol, slug)

    # Try standalone view first, then consolidated; also retry with original
    # base symbol if the slug override still 404s (graceful fallback chain)
    base_fallback = symbol.replace(".NS", "").replace(".BO", "").upper()
    candidates = [slug]
    if base_fallback != slug:
        candidates.append(base_fallback)   # fallback to raw NSE symbol

    resp = None
    for candidate in candidates:
        for variant in ("", "consolidated/"):
            url = f"https://www.screener.in/company/{candidate}/{variant}"
            try:
                r = requests.get(url, headers=_HEADERS, timeout=12)
                if r.status_code == 200:
                    resp = r
                    break
                log.debug("get_screener_data: %s → HTTP %s", url, r.status_code)
            except Exception as req_exc:
                log.debug("get_screener_data: request error %s: %s", url, req_exc)
        if resp is not None:
            break

    if resp is None:
        log.error("get_screener_data(%s): all URL variants returned non-200", symbol)
        return None

    try:
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        result = {
            "pe": None,
            "revenue_growth": None,
            "ebitda_margin": None,
            "debt_equity": None,
            "roce": None,
            "promoter_holding": None,
            "promoter_pledging": None,   # % of promoter shares pledged
            "revenue_growth_qoq": None,  # most recent quarter-on-quarter %
            # Tier 3 new fields
            "revenue_cagr_3y": None,     # 3-year compounded sales growth %
            "revenue_cagr_5y": None,     # 5-year compounded sales growth %
            "eps_cagr_3y": None,         # 3-year compounded profit growth %
            "eps_cagr_5y": None,         # 5-year compounded profit growth %
            "roe": None,                 # Return on equity %
            "interest_coverage": None,   # Interest coverage ratio
            # Shareholding breakdown (quarterly snapshot from screener)
            "fii_holding_pct": None,     # FII/FPI % ownership (latest quarter)
            "dii_holding_pct": None,     # DII/MF % ownership (latest quarter)
            "ocf_margin": None,          # Operating cash flow / revenue %
        }

        # ── Top ratios bar ──────────────────────────────────────────────────
        for li in soup.select("#top-ratios li"):
            name_el = li.select_one(".name")
            val_el = li.select_one(".value, .number")
            if not name_el or not val_el:
                continue
            name_txt = name_el.get_text(strip=True).lower()
            val_txt = val_el.get_text(strip=True).replace(",", "").replace("%", "").strip()
            val = _safe_float(val_txt)
            if "stock p/e" in name_txt or "p/e" == name_txt:
                result["pe"] = val
            elif "debt / equity" in name_txt or "debt/equity" in name_txt:
                result["debt_equity"] = val
            elif "roce" in name_txt:
                result["roce"] = val
            elif "roe" in name_txt and "roce" not in name_txt:
                # ROE label contains "roe" but NOT "roce"
                result["roe"] = val
            elif "interest coverage" in name_txt:
                result["interest_coverage"] = val

        # ── Shareholding table: promoter, pledging, FII/DII ────────────────────
        # Screener's shareholding section has rows:
        #   Promoters | FIIs | DIIs | Government | Public | ...
        # Each row has one value per quarter; we take the most recent (last) cell.
        found_promoter = False
        for row in soup.select("table.data-table tbody tr"):
            cells = row.find_all("td")
            if not cells:
                continue
            row_label = cells[0].get_text(strip=True).lower()

            def _last_val(cells):
                """Return the last non-None float value across all cells."""
                v_all = [
                    _safe_float(td.get_text(strip=True).replace("%", ""))
                    for td in cells[1:]
                ]
                vals = [v for v in v_all if v is not None]
                return vals[-1] if vals else None

            if not found_promoter and "promoter" in row_label and "pledg" not in row_label:
                result["promoter_holding"] = _last_val(cells)
                found_promoter = True
            elif "pledg" in row_label:
                result["promoter_pledging"] = _last_val(cells)
                # keep scanning — FII/DII rows come after pledging
            elif any(k in row_label for k in ("fii", "fpi", "foreign")):
                result["fii_holding_pct"] = _last_val(cells)
            elif any(k in row_label for k in ("dii", "mutual fund", "insurance", "domestic inst")):
                if result["dii_holding_pct"] is None:
                    result["dii_holding_pct"] = _last_val(cells)

        # ── QoQ revenue growth from quarterly sales table ────────────────────
        # Look for the quarterly P&L section (Sales row) and compute QoQ
        for table in soup.select("table.data-table"):
            header_row = table.select_one("thead tr")
            if not header_row:
                continue
            headers = [th.get_text(strip=True).lower() for th in header_row.find_all("th")]
            # We want quarterly tables (months like "Sep 2023", "Dec 2023")
            if not any("sep" in h or "dec" in h or "mar" in h or "jun" in h for h in headers):
                continue
            for row in table.select("tbody tr"):
                cells = row.find_all("td")
                if not cells:
                    continue
                if "sales" in cells[0].get_text(strip=True).lower():
                    qtrs = []
                    for td in cells[1:]:
                        v = _safe_float(td.get_text(strip=True).replace(",", ""))
                        if v is not None:
                            qtrs.append(v)
                    if len(qtrs) >= 2 and qtrs[-2] > 0:
                        result["revenue_growth_qoq"] = round(
                            (qtrs[-1] - qtrs[-2]) / qtrs[-2] * 100, 2
                        )
                    break
            if result["revenue_growth_qoq"] is not None:
                break

        # ── Revenue growth (CAGR) and Profit growth (CAGR) from section cards ──
        # Single pass over all section.card elements; captures 1yr/3yr/5yr for
        # both Sales Growth and Profit Growth sections.
        for section in soup.select("section.card"):
            header = section.find(["h2", "h3"])
            if not header:
                continue
            header_txt = header.get_text(strip=True).lower()

            if "compounded sales growth" in header_txt:
                # Extract all li items to capture 1yr/3yr/5yr
                for li in section.select("li"):
                    try:
                        txt = li.get_text(strip=True)
                        label = txt.split(":")[0].strip().lower()
                        val_str = re.sub(r"[^\d.\-]", "", txt.split(":")[-1])
                        val = _safe_float(val_str)
                        if "ttm" in label or "1 year" in label:
                            result["revenue_growth"] = val
                        elif "3 year" in label:
                            result["revenue_cagr_3y"] = val
                        elif "5 year" in label:
                            result["revenue_cagr_5y"] = val
                        # "10 years" → ignore (too long-term)
                    except Exception:
                        continue

            elif "compounded profit growth" in header_txt or (
                "profit growth" in header_txt and "sales" not in header_txt
            ):
                # Extract eps/profit CAGR: 1yr/3yr/5yr
                for li in section.select("li"):
                    try:
                        txt = li.get_text(strip=True)
                        label = txt.split(":")[0].strip().lower()
                        val_str = re.sub(r"[^\d.\-]", "", txt.split(":")[-1])
                        val = _safe_float(val_str)
                        if "3 year" in label:
                            result["eps_cagr_3y"] = val
                        elif "5 year" in label:
                            result["eps_cagr_5y"] = val
                        # 1yr / ttm → optional; not used downstream yet
                    except Exception:
                        continue

        # EBITDA margin — look for OPM in the top ratios or quarters table
        for li in soup.select("#top-ratios li"):
            name_el = li.select_one(".name")
            val_el = li.select_one(".value, .number")
            if name_el and val_el and "opm" in name_el.get_text(strip=True).lower():
                result["ebitda_margin"] = _safe_float(
                    val_el.get_text(strip=True).replace("%", "").replace(",", "")
                )

        # ── OCF margin from annual P&L + Cash Flows sections ────────────────────
        # Scan for:
        #   (a) Annual "Profit & Loss" section → latest Sales figure (denominator)
        #   (b) "Cash Flows" section → latest "Cash from Operating Activity" figure
        # Then compute  ocf_margin = OCF / Sales * 100
        # This distinguishes EBITDA-rich but cash-poor companies (accrual distortion).
        _annual_sales: Optional[float] = None
        _ocf_abs: Optional[float] = None

        for section in soup.select("section.card"):
            header = section.find(["h2", "h3"])
            if not header:
                continue
            htxt = header.get_text(strip=True).lower()

            # Annual P&L card → latest Sales row (denominator for OCF margin)
            if "profit & loss" in htxt or "profit and loss" in htxt:
                for table in section.select("table.data-table"):
                    for row in table.select("tbody tr"):
                        cells = row.find_all("td")
                        if not cells:
                            continue
                        if "sales" in cells[0].get_text(strip=True).lower():
                            vals = [
                                _safe_float(td.get_text(strip=True).replace(",", ""))
                                for td in cells[1:]
                            ]
                            vals = [v for v in vals if v is not None]
                            if vals:
                                _annual_sales = vals[-1]
                            break
                    if _annual_sales is not None:
                        break

            # Cash Flows card → row containing "operating" (Cash from Operating Activity)
            elif "cash flows" in htxt or "cash flow" in htxt:
                for table in section.select("table.data-table"):
                    for row in table.select("tbody tr"):
                        cells = row.find_all("td")
                        if not cells:
                            continue
                        row_label = cells[0].get_text(strip=True).lower()
                        if "operating" in row_label:
                            vals = [
                                _safe_float(td.get_text(strip=True).replace(",", ""))
                                for td in cells[1:]
                            ]
                            vals = [v for v in vals if v is not None]
                            if vals:
                                _ocf_abs = vals[-1]
                            break
                    if _ocf_abs is not None:
                        break

        if _annual_sales and _annual_sales > 0 and _ocf_abs is not None:
            result["ocf_margin"] = round(_ocf_abs / _annual_sales * 100, 2)

        return result
    except Exception as e:
        log.error("get_screener_data(%s): %s", symbol, e)
        return None


def _safe_float(val: str) -> float | None:
    """Convert a string to float, return None if not possible."""
    try:
        return float(str(val).strip())
    except (ValueError, TypeError):
        return None


# ─── 6. INR/USD ──────────────────────────────────────────────────────────────

def get_inr_usd() -> float | None:
    """
    Fetch the current USD → INR exchange rate via yfinance (USDINR=X).

    Returns:
        float (e.g. 83.42), or None on failure.
    """
    try:
        ticker = yf.Ticker("USDINR=X")
        hist = yf_fetch_with_retry(ticker.history, period="1d")
        if not hist.empty:
            return round(float(hist["Close"].iloc[-1]), 4)
        info = yf_fetch_with_retry(lambda: ticker.info)
        rate = info.get("regularMarketPrice") or info.get("previousClose")
        return round(float(rate), 4) if rate else None
    except Exception as e:
        log.error("get_inr_usd: %s", e)
        return None


# ─── 7. INDIA VIX ────────────────────────────────────────────────────────────

def get_india_vix() -> float | None:
    """
    Fetch the India VIX index value via yfinance (^INDIAVIX).

    Returns:
        float (e.g. 13.45), or None on failure.
    """
    try:
        ticker = yf.Ticker("^INDIAVIX")
        hist = yf_fetch_with_retry(ticker.history, period="1d")
        if not hist.empty:
            return round(float(hist["Close"].iloc[-1]), 2)
        info = yf_fetch_with_retry(lambda: ticker.info)
        vix = info.get("regularMarketPrice") or info.get("previousClose")
        return round(float(vix), 2) if vix else None
    except Exception as e:
        log.error("get_india_vix: %s", e)
        return None


# ─── SMOKE TEST ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    print("── INR/USD ──────────────────────")
    print(get_inr_usd())

    print("── India VIX ────────────────────")
    print(get_india_vix())

    print("── MCX Prices ───────────────────")
    print(json.dumps(get_mcx_prices(), indent=2))

    print("── OHLCV: TCS.NS (3mo) ──────────")
    df = get_ohlcv("TCS.NS", period="3mo")
    print(df.tail(3) if df is not None else None)

    print("── NSE FII/DII ──────────────────")
    print(json.dumps(get_nse_fii_dii(), indent=2))

    print("── RSS: TCS ─────────────────────")
    headlines = get_rss_headlines("TCS")
    print(json.dumps(headlines[:3] if headlines else headlines, indent=2))

    print("── Screener: TCS ────────────────")
    print(json.dumps(get_screener_data("TCS"), indent=2))
