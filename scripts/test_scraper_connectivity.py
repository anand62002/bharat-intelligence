"""
scripts/test_scraper_connectivity.py
=====================================
Manual connectivity test — run this on Railway to diagnose IP blocks.

Usage:
  # From your local machine (tests local network)
  python scripts/test_scraper_connectivity.py

  # On Railway (tests Railway's IP — the one that matters)
  railway run python scripts/test_scraper_connectivity.py

  # With .env loaded
  python -m dotenv run python scripts/test_scraper_connectivity.py

What it tests:
  1. What is Railway's outbound IP?
  2. Can screener.in be reached?
  3. Can Trendlyne be reached (with your session cookie)?
  4. Is a proxy configured and does it help?
"""

import os
import sys
import time
from pathlib import Path

# Load .env if available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Add project root to path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import requests

LINE = "─" * 60

def _check(label: str, ok: bool, detail: str = ""):
    icon = "✅" if ok else "❌"
    print(f"  {icon}  {label}")
    if detail:
        print(f"       {detail}")


def main():
    print(f"\n{LINE}")
    print("  Bharat Intelligence — Scraper Connectivity Test")
    print(LINE)

    # ── 1. What is our outbound IP? ───────────────────────────────────────────
    print("\n[1] Outbound IP Address")
    try:
        ip_info = requests.get("https://api.ipify.org?format=json", timeout=5).json()
        ip = ip_info.get("ip", "unknown")
        print(f"       Outbound IP: {ip}")
        print(f"       (This is what screener.in / Trendlyne sees)")
    except Exception as exc:
        ip = "unknown"
        print(f"       Could not determine IP: {exc}")

    # ── 2. Proxy configuration ────────────────────────────────────────────────
    print(f"\n[2] Proxy Configuration")
    scraperapi_key = os.getenv("SCRAPERAPI_KEY", "").strip()
    fixie_url      = os.getenv("FIXIE_URL", "").strip()
    https_proxy    = os.getenv("HTTPS_PROXY", "").strip()

    if scraperapi_key:
        print(f"  ✅  SCRAPERAPI_KEY configured ({scraperapi_key[:8]}...)")
        proxy_dict = {
            "http":  f"http://scraperapi:{scraperapi_key}@proxy-server.scraperapi.com:8001",
            "https": f"http://scraperapi:{scraperapi_key}@proxy-server.scraperapi.com:8001",
        }
        proxy_name = "ScraperAPI"
    elif fixie_url:
        print(f"  ✅  FIXIE_URL configured ({fixie_url[:30]}...)")
        proxy_dict = {"http": fixie_url, "https": fixie_url}
        proxy_name = "Fixie"
    elif https_proxy:
        print(f"  ✅  HTTPS_PROXY configured")
        proxy_dict = {"http": https_proxy, "https": https_proxy}
        proxy_name = "HTTPS_PROXY"
    else:
        print("  ⚠️   No proxy configured (direct connection)")
        print("       To fix: set SCRAPERAPI_KEY or FIXIE_URL in Railway env vars")
        proxy_dict = {}
        proxy_name = None

    # ── 3. Screener.in connectivity ───────────────────────────────────────────
    print(f"\n[3] Screener.in")

    def _test_screener(proxies: dict, label: str):
        try:
            t0 = time.time()
            r = requests.get(
                "https://screener.in/",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36"
                },
                proxies=proxies,
                timeout=10,
                allow_redirects=True,
            )
            elapsed = round((time.time() - t0) * 1000)
            ok = r.status_code == 200
            _check(
                f"Homepage [{label}]",
                ok,
                f"HTTP {r.status_code} in {elapsed}ms  ({'OK' if ok else 'BLOCKED'})"
            )

            if ok:
                # Try a company page
                t0 = time.time()
                r2 = requests.get(
                    "https://www.screener.in/company/RELIANCE/consolidated/",
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36",
                        "Referer": "https://screener.in/",
                    },
                    proxies=proxies,
                    timeout=12,
                    allow_redirects=True,
                )
                elapsed2 = round((time.time() - t0) * 1000)
                has_data = r2.status_code == 200 and "company-ratios" in r2.text
                _check(
                    f"RELIANCE page [{label}]",
                    has_data,
                    f"HTTP {r2.status_code} in {elapsed2}ms  ({'DATA FOUND' if has_data else 'NO DATA'})"
                )
                return has_data
        except Exception as exc:
            _check(f"[{label}]", False, str(exc)[:120])
        return False

    screener_direct = _test_screener({}, "direct")

    if proxy_dict and not screener_direct:
        print(f"       → Direct failed, trying via {proxy_name}…")
        screener_proxy = _test_screener(proxy_dict, proxy_name)
    elif proxy_dict:
        screener_proxy = _test_screener(proxy_dict, proxy_name)
    else:
        screener_proxy = False

    # ── 4. Trendlyne connectivity ─────────────────────────────────────────────
    print(f"\n[4] Trendlyne")

    tl_sess = os.getenv("TRENDLYNE_SESSION", "").strip()
    tl_csrf = os.getenv("TRENDLYNE_CSRF", "").strip()

    print(f"  {'✅' if tl_sess else '⚠️ '}  TRENDLYNE_SESSION: {'configured' if tl_sess else 'NOT SET'}")
    print(f"  {'✅' if tl_csrf else '⚠️ '}  TRENDLYNE_CSRF:    {'configured' if tl_csrf else 'NOT SET'}")

    def _test_trendlyne(proxies: dict, label: str):
        try:
            s = requests.Session()
            s.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36",
                "Referer": "https://trendlyne.com/",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            })
            if tl_sess:
                s.cookies.set(".trendlyne", tl_sess, domain="trendlyne.com")
            if tl_csrf:
                s.cookies.set("csrftoken", tl_csrf, domain="trendlyne.com")
            if proxies:
                s.proxies.update(proxies)

            t0 = time.time()
            r = s.get("https://trendlyne.com/equity/RELIANCE/NSE/", timeout=15, allow_redirects=True)
            elapsed = round((time.time() - t0) * 1000)

            has_data = r.status_code == 200 and ("data-metrics" in r.text or "Reliance" in r.text)
            status_msg = {
                200: "DATA FOUND" if has_data else "200 but no data (may need session cookie)",
                302: "REDIRECT (session expired — update TRENDLYNE_SESSION cookie)",
                403: "FORBIDDEN (IP blocked or session invalid)",
                405: "METHOD NOT ALLOWED (Railway IP blocked by Trendlyne WAF)",
                429: "RATE LIMITED",
            }.get(r.status_code, f"HTTP {r.status_code}")
            _check(
                f"RELIANCE equity page [{label}]",
                has_data,
                f"HTTP {r.status_code} in {elapsed}ms  ({status_msg})"
            )
            return has_data
        except Exception as exc:
            _check(f"[{label}]", False, str(exc)[:120])
        return False

    tl_direct = _test_trendlyne({}, "direct")

    if proxy_dict and not tl_direct:
        print(f"       → Direct failed, trying via {proxy_name}…")
        tl_proxy = _test_trendlyne(proxy_dict, proxy_name)
    elif proxy_dict:
        tl_proxy = _test_trendlyne(proxy_dict, proxy_name)
    else:
        tl_proxy = False

    # ── 5. Summary ────────────────────────────────────────────────────────────
    print(f"\n{LINE}")
    print("  SUMMARY")
    print(LINE)

    screener_ok = screener_direct or screener_proxy
    trendlyne_ok = tl_direct or tl_proxy

    _check("Screener.in", screener_ok,
           "via proxy" if (screener_proxy and not screener_direct) else
           "direct" if screener_direct else "BLOCKED — set SCRAPERAPI_KEY or FIXIE_URL")
    _check("Trendlyne",   trendlyne_ok,
           "via proxy" if (tl_proxy and not tl_direct) else
           "direct" if tl_direct else "BLOCKED — set SCRAPERAPI_KEY or FIXIE_URL")

    if not proxy_dict and (not screener_ok or not trendlyne_ok):
        print()
        print("  ACTION REQUIRED:")
        print("  1. Sign up for ScraperAPI free trial: https://www.scraperapi.com/")
        print("  2. Copy your API key")
        print("  3. Set in Railway: SCRAPERAPI_KEY=<your-key>")
        print("  4. Re-deploy Railway worker + web services")
        print("  5. Re-run this script to confirm proxy works")
    elif proxy_dict and screener_ok and trendlyne_ok:
        print()
        print("  All sources working via proxy. Pipeline will produce recommendations.")
    elif proxy_dict and (not screener_ok or not trendlyne_ok):
        print()
        print("  Proxy configured but still blocked.")
        print("  → ScraperAPI rotating residential proxy is most reliable")
        print("  → Try a different ScraperAPI plan or contact their support")

    print(f"{LINE}\n")

    return 0 if (screener_ok and trendlyne_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
