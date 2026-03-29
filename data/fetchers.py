"""
data/fetchers.py — Bharat Intelligence India Market Data Fetchers
All functions return None on failure and log errors to stderr.
"""

import logging
import re
from datetime import datetime

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
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period)
        if df.empty:
            log.warning("get_ohlcv: no data returned for %s (period=%s)", symbol, period)
            return None
        df.index = df.index.tz_localize(None)
        return df[["Open", "High", "Low", "Close", "Volume"]]
    except Exception as e:
        log.error("get_ohlcv(%s): %s", symbol, e)
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
                hist = ticker.history(period="1d")
                if not hist.empty:
                    result[name] = round(float(hist["Close"].iloc[-1]), 2)
                else:
                    info = ticker.info
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
    clean = symbol.replace(".NS", "").replace(".BO", "").upper()
    url = f"https://www.screener.in/company/{clean}/"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=12)
        if resp.status_code == 404:
            # Try consolidated view
            resp = requests.get(url + "consolidated/", headers=_HEADERS, timeout=12)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        result = {
            "pe": None,
            "revenue_growth": None,
            "ebitda_margin": None,
            "debt_equity": None,
            "roce": None,
            "promoter_holding": None,
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

        # ── Promoter holding from shareholding table ────────────────────────
        for row in soup.select("table.data-table tbody tr"):
            cells = row.find_all("td")
            if cells and "promoter" in cells[0].get_text(strip=True).lower():
                last_val = None
                for td in cells[1:]:
                    v = _safe_float(td.get_text(strip=True).replace("%", ""))
                    if v is not None:
                        last_val = v
                result["promoter_holding"] = last_val
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
        hist = ticker.history(period="1d")
        if not hist.empty:
            return round(float(hist["Close"].iloc[-1]), 4)
        info = ticker.info
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
        hist = ticker.history(period="1d")
        if not hist.empty:
            return round(float(hist["Close"].iloc[-1]), 2)
        info = ticker.info
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
