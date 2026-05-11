"""
data/breeze_auth.py — ICICI Breeze Connect Session Manager
===========================================================
Manages BreezeConnect session lifecycle for options data fetching.

Two operating modes
-------------------
Manual (recommended for initial setup):
    Set BREEZE_SESSION_TOKEN env var daily.  Token is valid 24 hours.
    Get it by:
      1. Visit: https://api.icicidirect.com/apiuser/login?api_key=<BREEZE_API_KEY>
      2. Login with your ICICI Direct credentials + TOTP
      3. Copy the `code` parameter from the redirect URL
      4. Set BREEZE_SESSION_TOKEN=<code> in Railway env vars

Auto-refresh mode (TOTP-based, fully hands-off):
    Set ICICI_USER_ID + ICICI_PASSWORD + BREEZE_TOTP_SECRET.
    Worker refreshes the token every morning at 08:30 IST before
    the first options snapshot at 09:15.

Required env vars (always):
    BREEZE_API_KEY         From ICICI Direct API portal
    BREEZE_API_SECRET      From ICICI Direct API portal

Optional — manual mode:
    BREEZE_SESSION_TOKEN   Fresh session token (rotate daily)

Optional — auto mode:
    ICICI_USER_ID          Your ICICI Direct login ID
    ICICI_PASSWORD         Your ICICI Direct password
    BREEZE_TOTP_SECRET     Base32 TOTP secret from your 2FA setup

Install dependency:
    pip install breeze-connect pyotp

Usage
-----
    from data.breeze_auth import get_breeze_client
    breeze = get_breeze_client()     # None if not configured
    if breeze:
        resp = breeze.get_option_chain_quotes(...)
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
from typing import Optional

log = logging.getLogger(__name__)

# ── In-process client cache ───────────────────────────────────────────────────
_cache: dict = {
    "client":     None,
    "created_at": 0.0,
    "token":      "",       # session token used to build this client
}
_MAX_AGE_SECS = 23 * 3600  # refresh before 24-h expiry


# =============================================================================
# Proxy context (Fixie — surgical, only wraps Breeze API calls)
# =============================================================================

@contextlib.contextmanager
def breeze_proxy():
    """
    Context manager: temporarily routes outbound HTTP/HTTPS through the
    Fixie proxy (if FIXIE_URL env var is set) for the duration of the block.

    Usage:
        with breeze_proxy():
            client.generate_session(...)
            resp = client.get_option_chain_quotes(...)

    Why surgical (not global)?
        Fixie free tier = 500 req/month.  yfinance + screener calls would
        blow past that instantly.  We only need the proxy for ICICI API calls
        so Railway's outbound IP matches what ICICI has whitelisted.

    Env var:
        FIXIE_URL=http://fixie:<token>@criterium.usefixie.com:80
    """
    fixie_url = os.getenv("FIXIE_URL", "").strip()
    if not fixie_url:
        yield
        return

    # Save existing proxy settings (if any)
    saved = {
        "HTTPS_PROXY": os.environ.get("HTTPS_PROXY"),
        "HTTP_PROXY":  os.environ.get("HTTP_PROXY"),
        "https_proxy": os.environ.get("https_proxy"),
        "http_proxy":  os.environ.get("http_proxy"),
    }
    # Activate proxy
    os.environ["HTTPS_PROXY"] = fixie_url
    os.environ["HTTP_PROXY"]  = fixie_url
    os.environ["https_proxy"] = fixie_url
    os.environ["http_proxy"]  = fixie_url
    try:
        yield
    finally:
        # Restore previous state
        for key, val in saved.items():
            if val is not None:
                os.environ[key] = val
            else:
                os.environ.pop(key, None)


# =============================================================================
# Public API
# =============================================================================

def get_breeze_client():
    """
    Return a ready BreezeConnect instance, or None if Breeze is not configured.

    Reuses the cached client for up to 23 hours.  On the first call (or after
    token rotation), builds a new session.

    Never raises — returns None on any failure so callers can fall back.
    """
    api_key    = os.getenv("BREEZE_API_KEY", "").strip()
    api_secret = os.getenv("BREEZE_API_SECRET", "").strip()
    token      = os.getenv("BREEZE_SESSION_TOKEN", "").strip()

    if not (api_key and api_secret and token):
        log.debug("Breeze not configured — BREEZE_API_KEY/SECRET/SESSION_TOKEN missing")
        return None

    # Return cached client if token unchanged and not expired
    age = time.time() - _cache["created_at"]
    if _cache["client"] and _cache["token"] == token and age < _MAX_AGE_SECS:
        return _cache["client"]

    # Build a new client
    try:
        from breeze_connect import BreezeConnect  # type: ignore[import]
        client = BreezeConnect(api_key=api_key)
        # generate_session() always returns None in this SDK version — that is normal.
        # It calls api_util() internally which validates the token against ICICI and
        # decodes the real session_key from base64.  On success it sets client.user_id.
        # We verify success by checking that user_id was populated.
        # Route through Fixie proxy so Railway's outbound IP matches ICICI whitelist.
        with breeze_proxy():
            client.generate_session(api_secret=api_secret, session_token=token)

        user_id = getattr(client, "user_id", None)
        if not user_id:
            log.warning(
                "Breeze session token appears invalid — BREEZE_SESSION_TOKEN may be "
                "wrong or expired.\n"
                "  Get a fresh token:\n"
                "  1. Open: https://api.icicidirect.com/apiuser/login?api_key=%s\n"
                "  2. Login with ICICI Direct ID + password + TOTP\n"
                "  3. From the redirect URL copy the value after `apisession=`\n"
                "     (it looks like: https://127.0.0.1/?apisession=XXXXXXXX)\n"
                "  4. Set BREEZE_SESSION_TOKEN=<that value> in your .env / Railway vars",
                api_key[:8] + "...",
            )
            return None

        _cache["client"]     = client
        _cache["created_at"] = time.time()
        _cache["token"]      = token
        log.info("Breeze session established — user_id=%s (api_key=...%s)", user_id, api_key[-6:])
        return client

    except ImportError:
        log.warning(
            "breeze-connect not installed — run: pip install breeze-connect\n"
            "Options data will fall back to India VIX estimates."
        )
        return None
    except Exception as exc:
        log.warning("Breeze session setup failed: %s", exc)
        return None


def invalidate_cache() -> None:
    """Force next call to get_breeze_client() to re-authenticate."""
    _cache["client"]     = None
    _cache["created_at"] = 0.0
    _cache["token"]      = ""


# =============================================================================
# Session refresh
# =============================================================================

def refresh_session(dry_run: bool = False) -> dict:
    """
    Attempt to refresh the Breeze session token.

    Auto mode: POST login credentials + TOTP to ICICI Direct, parse the
    resulting session token from the redirect URL, write it back to the
    BREEZE_SESSION_TOKEN env var for this process (Railway restart picks
    it up from the env).  Workers call this at 08:30 IST daily.

    Manual mode: if ICICI_USER_ID/PASSWORD/BREEZE_TOTP_SECRET are not set,
    just verifies that the existing BREEZE_SESSION_TOKEN is still valid and
    logs how many hours remain.

    Returns dict with keys: mode, success, message, hours_remaining.
    """
    api_key    = os.getenv("BREEZE_API_KEY", "").strip()
    api_secret = os.getenv("BREEZE_API_SECRET", "").strip()

    if not (api_key and api_secret):
        return {
            "mode":            "unconfigured",
            "success":         False,
            "message":         "BREEZE_API_KEY or BREEZE_API_SECRET not set — skipping refresh",
            "hours_remaining": 0,
        }

    user_id     = os.getenv("ICICI_USER_ID", "").strip()
    password    = os.getenv("ICICI_PASSWORD", "").strip()
    totp_secret = os.getenv("BREEZE_TOTP_SECRET", "").strip()

    if user_id and password and totp_secret:
        return _auto_refresh(api_key, api_secret, user_id, password, totp_secret, dry_run)
    else:
        return _manual_mode_check(api_key, api_secret)


# =============================================================================
# Internal helpers
# =============================================================================

def _auto_refresh(
    api_key: str,
    api_secret: str,
    user_id: str,
    password: str,
    totp_secret: str,
    dry_run: bool,
) -> dict:
    """
    Automate ICICI Direct login to get a fresh Breeze session token.

    Flow (reverse-engineered from ICICI's web login):
      1. GET  /apiuser/login?api_key=...  → collect cookies
      2. POST /apiuser/login              → submit credentials + TOTP
      3. Parse Location header for ?code=<token>
      4. Call generate_session(api_secret, token) to validate
      5. Set BREEZE_SESSION_TOKEN env var for this process
    """
    try:
        import pyotp          # type: ignore[import]
        import requests       # type: ignore[import]
        from urllib.parse import urlparse, parse_qs

        login_url = f"https://api.icicidirect.com/apiuser/login?api_key={api_key}"

        if dry_run:
            totp_now = pyotp.TOTP(totp_secret).now()
            log.info("[dry_run] Would POST to %s with user=%s totp=%s", login_url, user_id, totp_now)
            return {"mode": "auto", "success": True, "message": "dry_run — no request sent", "hours_remaining": 24}

        sess = requests.Session()
        sess.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        })

        # Step 1 — warm up session / collect cookies
        sess.get(login_url, timeout=15)

        # Step 2 — submit credentials
        totp_now = pyotp.TOTP(totp_secret).now()
        resp = sess.post(
            "https://api.icicidirect.com/apiuser/login",
            data={
                "UserId":    user_id,
                "Password":  password,
                "totp":      totp_now,
                "api_key":   api_key,
            },
            allow_redirects=False,
            timeout=20,
        )

        # Step 3 — extract token from redirect
        location = resp.headers.get("Location", "")
        if not location:
            # Some ICICI versions embed token in JSON body
            try:
                body = resp.json()
                location = body.get("redirectUrl") or body.get("redirect_url") or ""
            except Exception:
                pass

        if not location:
            return {
                "mode":            "auto",
                "success":         False,
                "message":         f"No redirect URL in ICICI response (HTTP {resp.status_code}). "
                                   "Check ICICI_USER_ID/PASSWORD credentials.",
                "hours_remaining": 0,
            }

        parsed = urlparse(location)
        token  = parse_qs(parsed.query).get("code", [None])[0]
        if not token:
            return {
                "mode":    "auto",
                "success": False,
                "message": f"Could not parse `code` from redirect: {location}",
                "hours_remaining": 0,
            }

        # Step 4 — validate token
        from breeze_connect import BreezeConnect  # type: ignore[import]
        client = BreezeConnect(api_key=api_key)
        validate = client.generate_session(api_secret=api_secret, session_token=token)
        if isinstance(validate, dict) and validate.get("Status") == 500:
            return {
                "mode":    "auto",
                "success": False,
                "message": f"Token validation failed: {validate.get('Error')}",
                "hours_remaining": 0,
            }

        # Step 5 — cache it
        os.environ["BREEZE_SESSION_TOKEN"] = token
        invalidate_cache()
        _cache["client"]     = client
        _cache["created_at"] = time.time()
        _cache["token"]      = token
        log.info("Breeze session refreshed automatically (token=...%s)", token[-6:])

        return {
            "mode":            "auto",
            "success":         True,
            "message":         "Session token refreshed via TOTP login",
            "hours_remaining": 24,
        }

    except ImportError as exc:
        missing = "pyotp" if "pyotp" in str(exc) else "requests"
        return {
            "mode":    "auto",
            "success": False,
            "message": f"Missing dependency: {missing} — run: pip install {missing}",
            "hours_remaining": 0,
        }
    except Exception as exc:
        log.error("Auto Breeze refresh failed: %s", exc, exc_info=True)
        return {
            "mode":    "auto",
            "success": False,
            "message": str(exc),
            "hours_remaining": 0,
        }


def _manual_mode_check(api_key: str, api_secret: str) -> dict:
    """Verify existing token and report time remaining."""
    token = os.getenv("BREEZE_SESSION_TOKEN", "").strip()
    if not token:
        msg = (
            "⚠ BREEZE_SESSION_TOKEN is not set.\n"
            "  To enable real options chain data:\n"
            "  1. Visit: https://api.icicidirect.com/apiuser/login?api_key={key}\n"
            "  2. Login → copy `apisession=` value from redirect URL\n"
            "  3. Set BREEZE_SESSION_TOKEN=<code> in Railway env vars\n"
            "  Token refreshes daily.  For auto-refresh, also set:\n"
            "    ICICI_USER_ID, ICICI_PASSWORD, BREEZE_TOTP_SECRET"
        ).format(key=api_key[:8] + "...")
        log.warning(msg)
        return {"mode": "manual", "success": False, "message": msg, "hours_remaining": 0}

    # Only use cache timestamp if we actually built a client with this token.
    # If cache is cold (created_at==0), we have no idea how old the token is —
    # assume it's fresh (user just pasted it) rather than treating it as expired.
    cache_ts = _cache.get("created_at", 0.0)
    if cache_ts > 0 and _cache.get("token") == token:
        age       = time.time() - cache_ts
        remaining = max(0.0, 24 - age / 3600)
    else:
        # Token not yet used in this process — assume fresh
        remaining = 24.0

    if remaining < 2:
        log.warning(
            "Breeze session token may be expiring soon. "
            "Update BREEZE_SESSION_TOKEN in Railway env vars if calls start failing."
        )
    else:
        log.info("Breeze session token present — ~%.1f hours remaining (estimate)", remaining)

    return {
        "mode":            "manual",
        "success":         True,
        "message":         f"Token present, ~{remaining:.1f}h remaining (estimate)",
        "hours_remaining": round(remaining, 1),
    }


# =============================================================================
# CLI smoke-test
# =============================================================================

if __name__ == "__main__":
    import json
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.DEBUG)
    result = refresh_session(dry_run=("--dry-run" in __import__("sys").argv))
    print(json.dumps(result, indent=2))
    client = get_breeze_client()
    print("client:", client)
