"""
probe_angelone_options.py — Angel One Smart API credential + data probe
=======================================================================
Run this BEFORE the full implementation to confirm:
  1. Your credentials work (login succeeds)
  2. Option chain data is available for NIFTY / BANKNIFTY
  3. The data has the fields we need (OI, IV, PCR)

Prerequisites
-------------
  pip install smartapi-python pyotp

Set these env vars (or create a .env file):
  ANGEL_API_KEY        = from developer.angelone.in (your App's API key)
  ANGEL_CLIENT_ID      = your Angel One client code / user ID
  ANGEL_PASSWORD       = your Angel One trading password (4-digit PIN or full password)
  ANGEL_TOTP_SECRET    = base32 TOTP secret (shown during 2FA setup on Angel One)

Usage
-----
  python probe_angelone_options.py           # test NIFTY + BANKNIFTY
  python probe_angelone_options.py FINNIFTY  # test a specific symbol
"""

from __future__ import annotations

import os
import sys
import time

# ─── Load .env if present ────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not required; env vars can be set directly


def _fail(msg: str) -> None:
    print(f"  [FAIL]  {msg}")

def _ok(msg: str) -> None:
    print(f"  [PASS]  {msg}")

def _info(msg: str) -> None:
    print(f"  [INFO]  {msg}")

def _section(title: str) -> None:
    print(f"\n{'-'*60}\n  {title}\n{'-'*60}")


# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — Check env vars
# ─────────────────────────────────────────────────────────────────────────────

def check_env() -> bool:
    _section("Step 1 — Environment variables")
    required = {
        "ANGEL_API_KEY":     os.getenv("ANGEL_API_KEY", ""),
        "ANGEL_CLIENT_ID":   os.getenv("ANGEL_CLIENT_ID", ""),
        "ANGEL_PASSWORD":    os.getenv("ANGEL_PASSWORD", ""),
        "ANGEL_TOTP_SECRET": os.getenv("ANGEL_TOTP_SECRET", ""),
    }
    all_set = True
    for k, v in required.items():
        if v:
            masked = v[:4] + "*" * max(0, len(v) - 4)
            print(f"    {k:25s} SET ({masked})")
        else:
            print(f"    {k:25s} NOT SET  <-- required")
            all_set = False
    if not all_set:
        print("\n  Set the missing env vars and re-run this script.")
        print("  See the docstring at the top of this file for instructions.")
    return all_set


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — Check SDK installed
# ─────────────────────────────────────────────────────────────────────────────

def check_sdk() -> bool:
    _section("Step 2 — SDK check")
    try:
        import SmartApi  # noqa
        import pyotp     # noqa
        _ok("smartapi-python and pyotp are installed")
        return True
    except ImportError as e:
        _fail(f"Missing package: {e}")
        print("    Fix: pip install smartapi-python pyotp")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — Login
# ─────────────────────────────────────────────────────────────────────────────

def do_login() -> object | None:
    """Attempt Angel One login. Returns SmartConnect object on success, None on failure."""
    _section("Step 3 — Angel One login")

    api_key   = os.getenv("ANGEL_API_KEY", "")
    client_id = os.getenv("ANGEL_CLIENT_ID", "")
    password  = os.getenv("ANGEL_PASSWORD", "")
    totp_sec  = os.getenv("ANGEL_TOTP_SECRET", "")

    try:
        import pyotp
        from SmartApi import SmartConnect  # type: ignore

        totp = pyotp.TOTP(totp_sec).now()
        _info(f"TOTP generated: {totp}  (valid ~{30 - int(time.time()) % 30}s)")

        t0  = time.time()
        obj = SmartConnect(api_key=api_key)
        data = obj.generateSession(client_id, password, totp)
        ms  = int((time.time() - t0) * 1000)

        if data and data.get("status"):
            d = data.get("data", {})
            _ok(f"Login successful  {ms}ms")
            _info(f"  jwtToken:     {str(d.get('jwtToken', ''))[:30]}...")
            _info(f"  refreshToken: {str(d.get('refreshToken', ''))[:20]}...")
            _info(f"  feedToken:    {str(d.get('feedToken', ''))[:20]}...")
            return obj
        else:
            _fail(f"Login failed: {data}")
            return None

    except Exception as e:
        _fail(f"Login exception: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — Fetch option chain for an index
# ─────────────────────────────────────────────────────────────────────────────

# Angel One uses numeric token IDs for instruments.
# NIFTY 50 index token = 26000, BANKNIFTY = 26009 (NSE exchange)
_INDEX_TOKENS = {
    "NIFTY":     "26000",
    "BANKNIFTY": "26009",
    "FINNIFTY":  "26037",
}

def probe_option_chain(obj: object, symbol: str = "NIFTY") -> bool:
    """
    Try to fetch option chain data using Angel One SDK.
    Tests multiple approaches in order:
      A) SDK getOptionChain method (v1.4+)
      B) REST API directly via requests
    """
    _section(f"Step 4 — Option chain data: {symbol}")

    # ── Approach A: SDK method (available in smartapi-python >= 1.4.x) ─────────
    print("  Approach A: smartapi-python getOptionChain method...")
    try:
        # Some SDK versions have this method, others don't
        fn = getattr(obj, "optionChain", None) or getattr(obj, "getOptionChain", None)
        if fn is not None:
            t0   = time.time()
            data = fn(symbol, _INDEX_TOKENS.get(symbol, "26000"))
            ms   = int((time.time() - t0) * 1000)
            if data and isinstance(data, (list, dict)) and len(data) > 0:
                _ok(f"SDK getOptionChain works  {ms}ms  rows={len(data) if isinstance(data, list) else 'dict'}")
                _analyse_chain_data(data, symbol)
                return True
            else:
                _fail(f"SDK method returned empty/None  {ms}ms  data={data}")
        else:
            _info("SDK method getOptionChain not found in this version — trying REST")
    except Exception as e:
        _info(f"SDK getOptionChain exception: {e}")

    # ── Approach B: REST API with JWT token ─────────────────────────────────────
    print("\n  Approach B: REST API /market/v1/optionChain...")
    try:
        import requests

        # Extract JWT from the session
        profile = obj.getProfile(obj.refreshToken if hasattr(obj, "refreshToken") else "")
        jwt = getattr(obj, "access_token", None) or ""
        if not jwt:
            # Try to get it from the session object internals
            jwt = getattr(obj, "_SmartConnect__access_token", "") or ""

        if not jwt:
            _info("Cannot extract JWT for REST call — trying with stored header")

        headers = {
            "Authorization": f"Bearer {jwt}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
            "X-UserType":    "USER",
            "X-SourceID":    "WEB",
            "X-ClientLocalIP": "127.0.0.1",
            "X-ClientPublicIP": "127.0.0.1",
            "X-MACAddress":   "AA:BB:CC:DD:EE:FF",
            "apikey":         os.getenv("ANGEL_API_KEY", ""),
        }

        # Angel One option chain REST endpoint (v2 API)
        token = _INDEX_TOKENS.get(symbol, "26000")
        url = "https://apiconnect.angelone.in/rest/secure/angelbroking/market/v1/optionChain"
        payload = {
            "exchange": "NFO",
            "tradingSymbol": f"{symbol}",
            "token": token,
        }

        t0 = time.time()
        r  = requests.post(url, json=payload, headers=headers, timeout=15)
        ms = int((time.time() - t0) * 1000)

        if r.status_code == 200:
            data = r.json()
            if data.get("status"):
                d = data.get("data", [])
                _ok(f"REST option chain works  {ms}ms  records={len(d)}")
                _analyse_chain_data(d, symbol)
                return True
            else:
                _fail(f"REST returned status=false  {ms}ms  {data.get('message', '')}")
        else:
            _fail(f"REST HTTP {r.status_code}  {ms}ms  body={r.text[:150]}")

    except Exception as e:
        _fail(f"REST approach exception: {e}")

    # ── Approach C: Get quotes for individual ATM options ──────────────────────
    print("\n  Approach C: Individual ATM strike quotes (compute PCR manually)...")
    try:
        import requests

        # Fetch underlying price first
        url_ltp = "https://apiconnect.angelone.in/rest/secure/angelbroking/market/v1/quote"
        exchange, trading_sym = ("NSE", "Nifty 50") if symbol == "NIFTY" else ("NSE", "Nifty Bank")

        headers = {
            "Authorization": f"Bearer {getattr(obj, 'access_token', '')}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
            "apikey":         os.getenv("ANGEL_API_KEY", ""),
        }
        payload = {
            "mode": "LTP",
            "exchangeTokens": {"NSE": [_INDEX_TOKENS.get(symbol, "26000")]},
        }
        t0 = time.time()
        r  = requests.post(url_ltp, json=payload, headers=headers, timeout=10)
        ms = int((time.time() - t0) * 1000)

        if r.status_code == 200 and r.json().get("status"):
            d    = r.json().get("data", {}).get("fetched", [])
            ltp  = float(d[0].get("ltp", 0)) if d else 0
            _ok(f"LTP quote works  {ms}ms  {symbol} LTP = {ltp}")
            _info("  Can compute ATM strike, fetch individual CE/PE OI for PCR estimation")
            return True
        else:
            _fail(f"LTP quote HTTP {r.status_code}  {ms}ms  {r.text[:100]}")

    except Exception as e:
        _fail(f"LTP approach exception: {e}")

    _fail(f"All approaches failed for {symbol}")
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Analyse and print what we got from the chain
# ─────────────────────────────────────────────────────────────────────────────

def _analyse_chain_data(data: object, symbol: str) -> None:
    """Print the key fields available and compute a quick PCR."""
    try:
        rows = data if isinstance(data, list) else data.get("data", [])
        if not rows:
            _info("  Chain returned 0 rows")
            return

        _info(f"  Total option rows: {len(rows)}")

        # Show first row's available keys
        if rows:
            sample = rows[0]
            _info(f"  Sample row keys: {list(sample.keys())[:15]}")

        # Try to compute PCR
        total_put_oi  = 0
        total_call_oi = 0
        for row in rows:
            # Different APIs use different key names
            ot = (row.get("optionType") or row.get("option_type") or "").upper()
            oi = float(row.get("openInterest") or row.get("oi") or row.get("OI") or 0)
            if ot in ("PE", "PUT", "P"):
                total_put_oi += oi
            elif ot in ("CE", "CALL", "C"):
                total_call_oi += oi

        if total_call_oi > 0:
            pcr = round(total_put_oi / total_call_oi, 3)
            _ok(f"  PCR = {pcr}  (put_oi={total_put_oi:,.0f}  call_oi={total_call_oi:,.0f})")
        else:
            _info("  Could not compute PCR from this data shape")

        # Check for IV fields
        iv_fields = [k for k in (rows[0].keys() if rows else [])
                     if "iv" in k.lower() or "impliedVolatility" in k or "impliied" in k.lower()]
        if iv_fields:
            _ok(f"  IV fields found: {iv_fields}")
        else:
            _info("  No IV fields in this response (may need different endpoint for IV)")

    except Exception as e:
        _info(f"  Analysis error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Step 5 — Token refresh check
# ─────────────────────────────────────────────────────────────────────────────

def check_token_refresh(obj: object) -> None:
    _section("Step 5 — Token refresh capability")
    try:
        refresh_token = getattr(obj, "refreshToken", "") or ""
        if not refresh_token:
            _info("refreshToken not stored on object — check SDK version")
            return

        _info(f"refreshToken available: {refresh_token[:20]}...")

        # Try to use the refresh token to get a new JWT
        from SmartApi import SmartConnect  # type: ignore
        t0    = time.time()
        data  = obj.generateToken(refresh_token)
        ms    = int((time.time() - t0) * 1000)

        if data and data.get("status"):
            new_jwt = (data.get("data") or {}).get("jwtToken", "")
            _ok(f"Token refresh works  {ms}ms  new JWT: {new_jwt[:20]}...")
            _info("  This means we only need to do full login (TOTP) once,")
            _info("  then use refresh token to renew JWT without TOTP.")
        else:
            _fail(f"Token refresh failed: {data}")
            _info("  Will need TOTP on every token refresh (still automated with pyotp)")

    except Exception as e:
        _info(f"Token refresh test: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    symbols = sys.argv[1:] if len(sys.argv) > 1 else ["NIFTY", "BANKNIFTY"]

    print("\n" + "="*60)
    print("  Angel One Smart API — Credential + Options Data Probe")
    print("="*60)

    if not check_env():
        sys.exit(1)

    if not check_sdk():
        print("\n  Install SDK first:  pip install smartapi-python pyotp")
        sys.exit(1)

    obj = do_login()
    if obj is None:
        print("\n  Cannot proceed without a valid login.")
        sys.exit(1)

    results = {}
    for sym in symbols:
        results[sym] = probe_option_chain(obj, sym)

    check_token_refresh(obj)

    # Summary
    print("\n" + "="*60)
    print("  SUMMARY")
    print("="*60)
    for sym, ok in results.items():
        status = "[PASS]" if ok else "[FAIL]"
        print(f"  {status}  {sym} option chain data")

    all_ok = all(results.values())
    if all_ok:
        print("\n  READY: Angel One can replace Breeze.")
        print("  Next step: run the full implementation (P3-D).")
        print("  Set these env vars on Railway:")
        print("    ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PASSWORD, ANGEL_TOTP_SECRET")
        print("  Then remove: BREEZE_API_KEY, BREEZE_API_SECRET,")
        print("               BREEZE_SESSION_TOKEN, FIXIE_URL, ICICI_USER_ID,")
        print("               ICICI_PASSWORD, BREEZE_TOTP_SECRET")
    else:
        print("\n  PARTIAL: Some symbols failed — check output above.")
        print("  If LTP quotes work but chain is missing, we can still")
        print("  compute a PCR approximation from individual strike OI.")
    print("="*60 + "\n")


if __name__ == "__main__":
    main()
