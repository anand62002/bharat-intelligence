"""
data/proxy_session.py — Outbound HTTP proxy abstraction
========================================================
Both screener.in and Trendlyne block Railway's static AWS datacenter IPs at the
network/firewall level (Errno 101 ENETUNREACHABLE for screener, HTTP 405 from
Trendlyne WAF). All user-agent rotation in the world cannot bypass an IP-level
block. The fix is routing requests through residential/rotating proxy IPs.

This module provides a single `apply_proxy_to_session()` function. All data
fetchers (screener.in, Trendlyne) call it when building their requests.Session.

Proxy priority (first configured wins):
  1. SCRAPERAPI_KEY  — ScraperAPI rotating residential proxy ($29/month,
                       250k credits, purpose-built for scraping)
  2. FIXIE_URL       — Fixie static HTTP proxy (Railway add-on, ~$25/month
                       for 25k req; already in codebase for Breeze auth)
  3. HTTPS_PROXY     — Generic HTTPS proxy env var (standard docker/k8s pattern)
  4. (nothing)       — Direct connection (current behaviour; will still fail
                       for Railway-blocked IPs but preserved as fallback for
                       local dev where IPs are not blocked)

ScraperAPI notes
----------------
  - Uses HTTP proxy mode (preserves session cookies — critical for Trendlyne)
  - ScraperAPI proxy: proxy-server.scraperapi.com:8001
  - Credits used: 1 per request (250k/month on $29 plan is far more than needed)
  - SSL: ScraperAPI uses CONNECT tunnelling in proxy mode → verify=True is fine

Fixie notes
-----------
  - FIXIE_URL format: http://user:pass@proxy.usefixie.com:80
  - Already configured on Railway for ICICI Breeze auth
  - Static IP (not rotating), but residential-grade (not a known datacenter)
  - Plans: Free=500/mo, Socks=$10/5k, Business=$25/25k

Usage
-----
  from data.proxy_session import apply_proxy_to_session, proxy_configured

  session = requests.Session()
  apply_proxy_to_session(session)       # no-op if no proxy configured

  if not proxy_configured():
      log.warning("No proxy configured — screener.in/Trendlyne may be blocked")
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import requests

log = logging.getLogger(__name__)

# ── Read proxy config once at import time ────────────────────────────────────
_SCRAPERAPI_KEY = os.getenv("SCRAPERAPI_KEY", "").strip()
_FIXIE_URL      = os.getenv("FIXIE_URL",      "").strip()
_HTTPS_PROXY    = os.getenv("HTTPS_PROXY",    os.getenv("https_proxy", "")).strip()
_HTTP_PROXY     = os.getenv("HTTP_PROXY",     os.getenv("http_proxy",  "")).strip()

# Log proxy mode once at startup (import time)
if _SCRAPERAPI_KEY:
    _active_proxy = "scraperapi"
    log.info("proxy_session: using ScraperAPI rotating residential proxy")
elif _FIXIE_URL:
    _active_proxy = "fixie"
    log.info("proxy_session: using Fixie static proxy (%s...)", _FIXIE_URL[:30])
elif _HTTPS_PROXY:
    _active_proxy = "env"
    log.info("proxy_session: using HTTPS_PROXY env var")
else:
    _active_proxy = None
    log.debug(
        "proxy_session: no proxy configured — screener.in/Trendlyne will use "
        "direct connection (Railway IP may be blocked; set SCRAPERAPI_KEY or FIXIE_URL)"
    )


def proxy_configured() -> bool:
    """Return True if any proxy is configured."""
    return _active_proxy is not None


def get_proxy_dict() -> Optional[dict[str, str]]:
    """
    Return a {'http': ..., 'https': ...} proxies dict for requests, or None.

    Pass directly to session.proxies.update() or requests.get(proxies=...).
    """
    if _SCRAPERAPI_KEY:
        # HTTP proxy mode — keeps session cookies intact, CONNECT tunnelling
        proxy = (
            f"http://scraperapi:{_SCRAPERAPI_KEY}"
            f"@proxy-server.scraperapi.com:8001"
        )
        return {"http": proxy, "https": proxy}

    if _FIXIE_URL:
        return {"http": _FIXIE_URL, "https": _FIXIE_URL}

    if _HTTPS_PROXY:
        return {
            "http":  _HTTP_PROXY or _HTTPS_PROXY,
            "https": _HTTPS_PROXY,
        }

    return None


def apply_proxy_to_session(session: requests.Session) -> requests.Session:
    """
    Apply the configured proxy to a requests.Session in-place.
    No-op if no proxy is configured.
    Returns the same session for chaining.
    """
    proxies = get_proxy_dict()
    if proxies:
        session.proxies.update(proxies)
    return session


def scraper_get(
    url: str,
    session: Optional[requests.Session] = None,
    **kwargs,
) -> requests.Response:
    """
    Convenience: GET url with proxy applied.

    Uses session if provided (preserves cookies), otherwise creates a one-off
    request. For stateless pings / connectivity tests only — prefer
    apply_proxy_to_session() for cookie-bearing fetchers.
    """
    proxies = get_proxy_dict()
    if session is not None:
        return session.get(url, **kwargs)
    if proxies:
        kwargs.setdefault("proxies", proxies)
    return requests.get(url, **kwargs)
