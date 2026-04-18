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

def get_nse_fii_dii() -> dict | None:
    """
    Scrape latest FII/DII activity from NSE's public data endpoint.

    Returns:
        dict with keys: date (str), fii_net (float, crores), dii_net (float, crores)
        or None on failure.
    """
    url = "https://www.nseindia.com/api/fiidiiTradeReact"
    session = requests.Session()
    try:
        # Seed cookies — NSE requires a prior visit to the main site
        session.get("https://www.nseindia.com", headers=_HEADERS, timeout=10)
        resp = session.get(url, headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if not data:
            log.warning("get_nse_fii_dii: empty response")
            return None

        # Latest entry is first in the array
        latest = data[0]
        fii_net = float(latest.get("fiiNet") or latest.get("FII_NET") or 0)
        dii_net = float(latest.get("diiNet") or latest.get("DII_NET") or 0)
        date_str = latest.get("date") or latest.get("DATE") or ""

        return {"date": date_str, "fii_net": fii_net, "dii_net": dii_net}
    except Exception as e:
        log.error("get_nse_fii_dii: %s", e)
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

_RSS_FEEDS = [
    ("ET Markets", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("Moneycontrol", "https://www.moneycontrol.com/rss/business.xml"),
]

def get_rss_headlines(symbol: str) -> list | None:
    """
    Parse ET Markets and Moneycontrol RSS feeds for mentions of symbol.

    Args:
        symbol: Company name or ticker, e.g. "Reliance" or "TCS"

    Returns:
        List of dicts: [{title, source, published, url}], empty list if no matches, None on failure.
    """
    keyword = symbol.replace(".NS", "").replace(".BO", "").upper()
    results = []
    any_feed_ok = False

    for source_name, feed_url in _RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            if feed.bozo and not feed.entries:
                log.warning("get_rss_headlines: feed parse error for %s", source_name)
                continue
            any_feed_ok = True
            for entry in feed.entries:
                title = entry.get("title", "")
                if keyword.lower() in title.lower() or keyword.lower() in entry.get("summary", "").lower():
                    published = entry.get("published", "")
                    try:
                        published = str(datetime(*entry.published_parsed[:6]).date()) if entry.get("published_parsed") else published
                    except Exception:
                        pass
                    results.append({
                        "title": title.strip(),
                        "source": source_name,
                        "published": published,
                        "url": entry.get("link", ""),
                    })
        except Exception as e:
            log.warning("get_rss_headlines: error parsing %s: %s", source_name, e)

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
        dict with keys: pe, revenue_growth, ebitda_margin, debt_equity, roce,
        promoter_holding — values are floats or None if not found. Returns None on request failure.
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

        # ── Promoter holding + pledging from shareholding table ─────────────
        found_promoter = False
        for row in soup.select("table.data-table tbody tr"):
            cells = row.find_all("td")
            if not cells:
                continue
            row_label = cells[0].get_text(strip=True).lower()
            if not found_promoter and "promoter" in row_label and "pledg" not in row_label:
                last_val = None
                for td in cells[1:]:
                    v = _safe_float(td.get_text(strip=True).replace("%", ""))
                    if v is not None:
                        last_val = v
                result["promoter_holding"] = last_val
                found_promoter = True
            elif "pledg" in row_label:
                # Take the most recent quarter (last non-None value)
                vals = []
                for td in cells[1:]:
                    v = _safe_float(td.get_text(strip=True).replace("%", ""))
                    if v is not None:
                        vals.append(v)
                if vals:
                    result["promoter_pledging"] = vals[-1]
                break  # pledging row found — stop scanning

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

        # ── Revenue growth and EBITDA margin from financial ratios section ──
        for section in soup.select("section.card"):
            header = section.find(["h2", "h3"])
            if not header:
                continue
            header_txt = header.get_text(strip=True).lower()
            if "compounded sales growth" in header_txt or "growth" in header_txt:
                for li in section.select("li"):
                    txt = li.get_text(strip=True)
                    if "ttm" in txt.lower() or "1 year" in txt.lower():
                        result["revenue_growth"] = _safe_float(
                            re.sub(r"[^\d.\-]", "", txt.split(":")[-1])
                        )
                        break

        # EBITDA margin — look for OPM in the top ratios or quarters table
        for li in soup.select("#top-ratios li"):
            name_el = li.select_one(".name")
            val_el = li.select_one(".value, .number")
            if name_el and val_el and "opm" in name_el.get_text(strip=True).lower():
                result["ebitda_margin"] = _safe_float(
                    val_el.get_text(strip=True).replace("%", "").replace(",", "")
                )

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
