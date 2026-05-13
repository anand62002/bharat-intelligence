"""
probe_options_sources.py — Options data source reachability probe
=================================================================
Run this directly on Railway to find out which options data sources
are accessible from Railway's IP before committing to an implementation.

Usage
-----
    python probe_options_sources.py            # full probe
    python probe_options_sources.py --quick    # skip jugaad (no install needed)

What it tests
-------------
  1. NSE homepage reachability (can we even reach nseindia.com?)
  2. NSE option-chain API with cookie dance (NIFTY index chain)
  3. jugaad-data library (if installed; pip install jugaad-data)
  4. Angel One Smart API endpoint reachability (no account needed — just connectivity)
  5. Current Breeze / Fixie status (is the proxy actually routing correctly?)

Each probe prints:  PASS / FAIL / SKIP  + the key data returned or error reason.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

# Force UTF-8 output on Windows so box/emoji chars don't crash
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ok(label: str, detail: str = "") -> None:
    print("  [PASS]  " + label + ("  ->  " + detail if detail else ""))

def _fail(label: str, reason: str = "") -> None:
    print("  [FAIL]  " + label + ("  ->  " + reason if reason else ""))

def _skip(label: str, reason: str = "") -> None:
    print("  [SKIP]  " + label + ("  ->  " + reason if reason else ""))

def _section(title: str) -> None:
    print("\n" + "-"*60)
    print("  " + title)
    print("-"*60)


# ─────────────────────────────────────────────────────────────────────────────
# Probe 1 — NSE homepage (basic connectivity)
# ─────────────────────────────────────────────────────────────────────────────

def probe_nse_homepage() -> bool:
    _section("Probe 1 — NSE homepage connectivity")
    try:
        import requests
        t0 = time.time()
        r = requests.get(
            "https://www.nseindia.com",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/124.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate",
            },
            timeout=15,
        )
        ms = int((time.time() - t0) * 1000)
        if r.status_code == 200:
            cookies = dict(r.cookies)
            _ok("nseindia.com reachable",
                f"HTTP 200  {ms}ms  cookies={list(cookies.keys())[:5]}")
            return True
        else:
            _fail("nseindia.com", f"HTTP {r.status_code}  {ms}ms")
            return False
    except Exception as e:
        _fail("nseindia.com", str(e)[:120])
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Probe 2 — NSE option-chain API with cookie dance
# ─────────────────────────────────────────────────────────────────────────────

def probe_nse_option_chain() -> bool:
    _section("Probe 2 — NSE option-chain API (cookie dance)")
    try:
        import requests

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Referer": "https://www.nseindia.com/option-chain",
        }

        # Step 1: warm the session (get cookies)
        print("    Step 1: warming NSE session...")
        s = requests.Session()
        t0 = time.time()
        s.get("https://www.nseindia.com/option-chain", headers=headers, timeout=15)
        time.sleep(0.5)

        # Step 2: hit the actual data endpoint
        print("    Step 2: fetching NIFTY option chain...")
        t1 = time.time()
        r = s.get(
            "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY",
            headers=headers,
            timeout=15,
        )
        ms = int((time.time() - t0) * 1000)

        if r.status_code == 200:
            try:
                data = r.json()
                records = data.get("records", {})
                expiry_dates = records.get("expiryDates", [])
                chain_data = records.get("data", [])
                underlying = records.get("underlyingValue", "N/A")
                _ok("NSE option chain API",
                    f"HTTP 200  {ms}ms  "
                    f"underlying={underlying}  "
                    f"expiries={len(expiry_dates)}  "
                    f"strikes={len(chain_data)}")
                if expiry_dates:
                    print(f"    Next expiry: {expiry_dates[0]}")
                # Compute a quick PCR from the data
                if chain_data:
                    total_put_oi = sum(
                        (row.get("PE", {}) or {}).get("openInterest", 0)
                        for row in chain_data
                    )
                    total_call_oi = sum(
                        (row.get("CE", {}) or {}).get("openInterest", 0)
                        for row in chain_data
                    )
                    if total_call_oi > 0:
                        pcr = round(total_put_oi / total_call_oi, 3)
                        print(f"    Live PCR (total OI): {pcr}  "
                              f"(put_oi={total_put_oi:,}  call_oi={total_call_oi:,})")
                return True
            except Exception as parse_err:
                _fail("NSE option chain parse", str(parse_err)[:120])
                print(f"    Raw response (first 200 chars): {r.text[:200]}")
                return False
        elif r.status_code == 403:
            _fail("NSE option chain", f"HTTP 403 — Railway IP is blocked by NSE  {ms}ms")
            return False
        elif r.status_code == 401:
            _fail("NSE option chain", f"HTTP 401 — session cookies not accepted  {ms}ms")
            return False
        else:
            _fail("NSE option chain", f"HTTP {r.status_code}  {ms}ms  body={r.text[:100]}")
            return False

    except Exception as e:
        _fail("NSE option chain", str(e)[:120])
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Probe 3 — jugaad-data library
# ─────────────────────────────────────────────────────────────────────────────

def probe_jugaad() -> bool | None:
    """Returns True=works, False=installed but broken, None=not installed."""
    _section("Probe 3 — jugaad-data library")
    try:
        import jugaad_data  # noqa
    except ImportError:
        _skip("jugaad-data", "not installed — run: pip install jugaad-data")
        return None  # distinguish from "installed but failed"

    try:
        from jugaad_data.nse import NSELive
        t0 = time.time()
        n = NSELive()
        data = n.get_index_option_chain("NIFTY")
        ms = int((time.time() - t0) * 1000)

        if data and isinstance(data, dict):
            records = data.get("records", data)
            chain = records.get("data", [])
            underlying = records.get("underlyingValue", "N/A")
            _ok("jugaad-data NSELive.get_index_option_chain",
                f"{ms}ms  underlying={underlying}  strikes={len(chain)}")
            return True
        else:
            _fail("jugaad-data", f"returned unexpected type: {type(data)}  {ms}ms")
            return False
    except Exception as e:
        _fail("jugaad-data", str(e)[:120])
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Probe 4 — Angel One Smart API reachability (connectivity only, no account)
# ─────────────────────────────────────────────────────────────────────────────

def probe_angel_one() -> bool:
    _section("Probe 4 — Angel One Smart API reachability")
    try:
        import requests
        # Just test that the API host is reachable from Railway
        # (no auth — we're only checking network-level connectivity)
        endpoints = [
            ("Login endpoint", "https://apiconnect.angelone.in/rest/auth/angelbroking/user/v1/loginByPassword"),
            ("Market data host", "https://apiconnect.angelone.in/rest/secure/angelbroking/market/v1/quote"),
        ]
        all_ok = True
        for name, url in endpoints:
            try:
                t0 = time.time()
                r = requests.post(
                    url,
                    json={},
                    headers={"Content-Type": "application/json", "Accept": "application/json"},
                    timeout=10,
                )
                ms = int((time.time() - t0) * 1000)
                # 400/401/403 = reachable (just auth failed, expected)
                # 000/timeout = network blocked
                if r.status_code in (400, 401, 403):
                    _ok(f"Angel One {name}",
                        f"HTTP {r.status_code} (auth error = reachable)  {ms}ms")
                elif r.status_code == 200:
                    _ok(f"Angel One {name}", f"HTTP 200  {ms}ms")
                else:
                    _fail(f"Angel One {name}", f"HTTP {r.status_code}  {ms}ms")
                    all_ok = False
            except Exception as e:
                _fail(f"Angel One {name}", str(e)[:80])
                all_ok = False
        return all_ok
    except Exception as e:
        _fail("Angel One probe", str(e)[:120])
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Probe 5 — Breeze + Fixie diagnostic
# ─────────────────────────────────────────────────────────────────────────────

def probe_breeze_fixie() -> None:
    _section("Probe 5 — Breeze / Fixie diagnostic")

    fixie_url = os.getenv("FIXIE_URL", "")
    breeze_key = os.getenv("BREEZE_API_KEY", "")
    breeze_secret = os.getenv("BREEZE_API_SECRET", "")
    breeze_token = os.getenv("BREEZE_SESSION_TOKEN", "")

    print(f"    FIXIE_URL:             {'SET (' + fixie_url[:30] + '...)' if fixie_url else '❌ NOT SET'}")
    print(f"    BREEZE_API_KEY:        {'SET' if breeze_key else '❌ NOT SET'}")
    print(f"    BREEZE_API_SECRET:     {'SET' if breeze_secret else '❌ NOT SET'}")
    print(f"    BREEZE_SESSION_TOKEN:  {'SET (' + breeze_token[:8] + '...)' if breeze_token else '❌ NOT SET'}")

    if not fixie_url:
        _skip("Fixie proxy test", "FIXIE_URL not set")
        return

    # Test if Fixie proxy itself is reachable and credentials are valid
    try:
        import requests
        print("\n    Testing Fixie proxy connectivity (GET httpbin.org/ip through proxy)...")
        proxies = {"https": fixie_url, "http": fixie_url}
        t0 = time.time()
        r = requests.get("https://httpbin.org/ip", proxies=proxies, timeout=15)
        ms = int((time.time() - t0) * 1000)
        if r.status_code == 200:
            data = r.json()
            outbound_ip = data.get("origin", "unknown")
            _ok("Fixie proxy", f"HTTP 200  {ms}ms  outbound IP={outbound_ip}")
            print("    ℹ️  This is the IP Railway sees for Fixie-proxied requests.")
            print("       It must match the IP whitelisted on ICICI Direct portal.")
        elif r.status_code == 407:
            _fail("Fixie proxy", "407 Proxy Auth Required — FIXIE_URL credentials are wrong or quota exhausted")
        else:
            _fail("Fixie proxy", f"HTTP {r.status_code}  {ms}ms")
    except Exception as e:
        _fail("Fixie proxy", str(e)[:120])

    # Now test the Breeze API through Fixie (if all creds present)
    if not (breeze_key and breeze_secret and breeze_token):
        _skip("Breeze API via Fixie", "Missing BREEZE_* env vars")
        return

    try:
        import requests
        print("\n    Testing Breeze API call through Fixie proxy...")
        proxies = {"https": fixie_url, "http": fixie_url}
        t0 = time.time()
        r = requests.get(
            "https://api.icicidirect.com/breezeapi/api/v1/customerdetails",
            headers={"apikey": breeze_key},
            proxies=proxies,
            timeout=15,
        )
        ms = int((time.time() - t0) * 1000)
        if r.status_code == 200:
            _ok("Breeze API via Fixie", f"HTTP 200  {ms}ms  — API is reachable!")
        elif r.status_code == 407:
            _fail("Breeze API via Fixie",
                  f"407 even with proxy — ICICI blocking Fixie's outbound IP. "
                  f"Check IP whitelist on ICICI Direct portal.")
        elif r.status_code in (401, 403):
            _ok("Breeze API via Fixie",
                f"HTTP {r.status_code} (auth error = network reachable)  {ms}ms")
        else:
            _fail("Breeze API via Fixie", f"HTTP {r.status_code}  {ms}ms  {r.text[:80]}")
    except Exception as e:
        _fail("Breeze API via Fixie", str(e)[:120])


# ─────────────────────────────────────────────────────────────────────────────
# Probe 6 — Current outbound IP (so we know what Railway's IP looks like)
# ─────────────────────────────────────────────────────────────────────────────

def probe_outbound_ip() -> None:
    _section("Probe 6 — Railway outbound IP identity")
    try:
        import requests
        r = requests.get("https://httpbin.org/ip", timeout=10)
        if r.status_code == 200:
            ip = r.json().get("origin", "unknown")
            _ok("Railway outbound IP", ip)
            print(f"    ℹ️  This is the IP that NSE / screener.in / ICICI see.")
            print(f"       If NSE is blocking: this IP range is in their blocklist.")
        else:
            _fail("outbound IP check", f"HTTP {r.status_code}")
    except Exception as e:
        _fail("outbound IP", str(e)[:80])


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

def print_summary(results: dict) -> None:
    print("\n" + "="*60)
    print("  SUMMARY -- Options Source Viability")
    print("="*60)

    nse_ok = results.get("nse_homepage") and results.get("nse_optchain")
    jugaad_ok = results.get("jugaad")
    angel_ok = results.get("angel")

    if nse_ok:
        print("  [PASS]  NSE direct -- WORKS from Railway. Use jugaad-data or raw session.")
        print("          -> Recommended path: P3-D with jugaad-data / raw NSE session")
    elif results.get("nse_homepage") and not results.get("nse_optchain"):
        print("  [WARN]  NSE homepage reachable BUT option chain API is blocked.")
        print("          -> NSE blocks Railway IP for data endpoints (common).")
    else:
        print("  [FAIL]  NSE not reachable at all from Railway.")

    if jugaad_ok:
        print("  [PASS]  jugaad-data -- WORKS. Clean drop-in replacement for Breeze.")
    elif results.get("jugaad") is False:
        print("  [FAIL]  jugaad-data -- installed but not working from Railway IP.")
    else:
        print("  [SKIP]  jugaad-data -- not installed (pip install jugaad-data to test).")

    if angel_ok:
        print("  [PASS]  Angel One API -- reachable. Viable if NSE is blocked.")
        print("          -> Requires opening Angel One account + OAuth setup (one-time).")
    else:
        print("  [FAIL]  Angel One API -- not reachable.")

    print()
    if not nse_ok and not angel_ok:
        print("  Conclusion: Keep VIX-based fallback for options.")
        print("    Options signals are not critical for a fundamentals-first platform.")
        print("    Focus effort on P3-C (Trendlyne fundamentals) instead.")
    elif nse_ok:
        print("  Conclusion: Replace Breeze with NSE direct / jugaad-data.")
        print("    Remove all BREEZE_* and FIXIE_URL env vars from Railway.")
    elif angel_ok:
        print("  Conclusion: Angel One Smart API as Breeze replacement.")
        print("    One-time account setup, 7-day refresh token, no daily manual work.")
    print("="*60 + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Probe options data sources from Railway")
    parser.add_argument("--quick", action="store_true",
                        help="Skip jugaad probe (don't need it installed)")
    parser.add_argument("--nse-only", action="store_true",
                        help="Only run NSE probes")
    args = parser.parse_args()

    print("\n" + "="*60)
    print("  Bharat Intelligence -- Options Source Probe")
    print("  Run this script on Railway to test data source reachability")
    print("="*60)

    results: dict = {}

    results["nse_homepage"] = probe_nse_homepage()
    results["nse_optchain"] = probe_nse_option_chain()

    if not args.nse_only:
        if not args.quick:
            results["jugaad"] = probe_jugaad()

        results["angel"] = probe_angel_one()
        probe_breeze_fixie()
        probe_outbound_ip()

    print_summary(results)


if __name__ == "__main__":
    main()
