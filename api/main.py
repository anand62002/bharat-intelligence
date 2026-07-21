"""
api/main.py — Bharat Intelligence FastAPI Backend
==================================================
Serves live data to the React dashboard.

Endpoints
---------
  GET  /api/recommendations        Latest recs sorted by upside_pct, critical first
  GET  /api/discovery              is_discovery=true recs created today (7-day fallback)
  GET  /api/portfolio              Open portfolio holdings (status = OPEN)
  POST /api/portfolio              Add or update a holding (upsert by symbol+OPEN)
  GET  /api/portfolio/alerts       Unresolved portfolio alerts
  GET  /api/governance/alerts      Open governance / agent-health alerts
  GET  /api/governance/research    Research proposals ordered by relevance desc
  GET  /api/market/pulse           Live prices (yfinance) + FII net from Supabase
  GET  /api/warren_bot/{symbol}    On-demand Buffett quality analysis (24h Supabase cache)
  WS   /ws/alerts                  Real-time critical-danger broadcast

Auth
----
  HTTP:       x-api-key header  == DASHBOARD_API_KEY env var
  WebSocket:  ?api_key=<key>    query param
  If DASHBOARD_API_KEY is unset, all requests are allowed (local dev).

CORS
----
  VERCEL_DASHBOARD_URL env var — space-separated list of allowed origins.
  Defaults to ["*"] when unset.

Run
---
  uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any

import yfinance as yf
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security.api_key import APIKeyHeader
from supabase import Client, create_client

load_dotenv()

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

# ── Environment ────────────────────────────────────────────────────────────────
SUPABASE_URL         = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
DASHBOARD_API_KEY    = os.getenv("DASHBOARD_API_KEY", "")
VERCEL_DASHBOARD_URL = os.getenv("VERCEL_DASHBOARD_URL", "")   # e.g. "https://app.vercel.app"

# ── Supabase client ────────────────────────────────────────────────────────────
_supabase: Client | None = None
if SUPABASE_URL and SUPABASE_SERVICE_KEY:
    _supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    log.info("Supabase client initialised → %s", SUPABASE_URL)
else:
    log.warning("Supabase not configured — set SUPABASE_URL and SUPABASE_SERVICE_KEY")


def _sanitise_floats(obj: Any) -> Any:
    """Recursively replace NaN / ±Inf with None so FastAPI can serialise to JSON."""
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitise_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitise_floats(v) for v in obj]
    return obj

# ── Market symbols ─────────────────────────────────────────────────────────────
# (display_label, yfinance_symbol, format_mode)
#   index       → plain integer  (Nifty, Sensex)
#   inr_usd     → USD × USDINR  (gold/crude futures USD→INR)
#   rate        → 2-decimal float  (currency pair)
#   vix         → 1-decimal float
MARKET_SYMBOLS: list[tuple[str, str, str]] = [
    ("NIFTY 50",   "^NSEI",     "index"),
    ("SENSEX",     "^BSESN",    "index"),
    ("NIFTY BANK", "^NSEBANK",  "index"),
    ("GOLD MCX",   "GC=F",      "inr_usd"),   # USD/oz → INR/10g approx
    ("CRUDE MCX",  "CL=F",      "inr_usd"),   # USD/bbl → INR/bbl approx
    ("INR/USD",    "USDINR=X",  "rate"),
    ("INDIA VIX",  "^INDIAVIX", "vix"),
]

# Simple 60-second in-memory cache for market data (avoids hammering yfinance)
_market_cache: list[dict] = []
_market_cache_ts: float   = 0.0
MARKET_CACHE_TTL           = 60   # seconds

# 24-hour in-memory fallback cache for warren_bot on-demand results.
# Primary cache is the warren_bot_cache Supabase table; this is the fallback
# for when the table has not been created yet.
_warren_bot_mem_cache: dict[str, tuple[dict, float]] = {}   # symbol → (result, unix_ts)
WARREN_BOT_CACHE_TTL = 86_400   # 24 hours in seconds

# ── Symbol resolutions DB cache ────────────────────────────────────────────────
# Loaded at startup from symbol_resolutions Supabase table.
# Also populated at runtime as new symbols are probed / searched.
# key = UPPERCASE input symbol (no .NS/.BO), value = validated yfinance ticker
_symbol_resolutions_cache: dict[str, str] = {}

# =============================================================================
# NSE / BSE symbol resolver
# =============================================================================

# Overrides for symbols that don't follow the plain {NAME}.NS pattern
# (indices, commodity ETFs, currency pairs, mutual fund ETFs)
_NSE_OVERRIDES: dict[str, str] = {
    # Indices
    "NIFTY":        "^NSEI",
    "NIFTY50":      "^NSEI",
    "NIFTY 50":     "^NSEI",
    "SENSEX":       "^BSESN",
    "BANKNIFTY":    "^NSEBANK",
    "BANK NIFTY":   "^NSEBANK",
    "NIFTYBANK":    "^NSEBANK",
    "VIX":          "^INDIAVIX",
    "INDIAVIX":     "^INDIAVIX",
    # Gold
    "GOLDBEES":     "GOLDBEES.NS",
    "GOLD BEES":    "GOLDBEES.NS",
    "NIPPONINDGOLD":"NIPPONINDGOLD.NS",
    "SGBSEP31":     "SGBSEP31.NS",
    # Silver / Commodities
    "SILVERBEES":   "SILVERBEES.NS",
    # Liquid / Overnight ETFs
    "LIQUIDBEES":   "LIQUIDBEES.NS",
    "LIQUID BEES":  "LIQUIDBEES.NS",
    "LIQUIDETF":    "LIQUIDETF.NS",
    # Index ETFs
    "NIFTYBEES":    "NIFTYBEES.NS",
    "JUNIORBEES":   "JUNIORBEES.NS",
    "BANKBEES":     "BANKBEES.NS",
    "ITBEES":       "ITBEES.NS",
    "MON100":       "MON100.NS",
    "MAFANG":       "MAFANG.NS",
    # International / USD proxies
    "GOLD":         "GC=F",
    "CRUDE":        "CL=F",
    "CRUDEOIL":     "CL=F",
    "USDINR":       "USDINR=X",
    "INRUSD":       "USDINR=X",
    # Well-known company aliases
    "HDFCLIFE":     "HDFCLIFE.NS",
    "SBILIFE":      "SBILIFE.NS",
    "SBICARDS":     "SBICARD.NS",
    "PAYTM":        "PAYTM.NS",
    "ZOMATO":       "ZOMATO.NS",
    # Brand-name / popular aliases where NSE ticker differs from brand name.
    # Without these the live-probe falls through to SYMBOL.NS which 404s on
    # Yahoo Finance, causing GET /api/portfolio to return price=None for those
    # holdings and the dashboard shows stale or zero prices.
    "IHCL":                  "INDHOTEL.NS",   # Indian Hotels Company (brand = IHCL, NSE = INDHOTEL)
    "TAJHOTELS":             "INDHOTEL.NS",
    "BHARATSEAT":            "BHARATSE.NS",   # Bharat Seats Ltd (NSE = BHARATSE, not BHARATSEAT)
    "BHARATSEATS":           "BHARATSE.NS",
    "HITACHIENERGYINDIA":    "POWERINDIA.NS", # Hitachi Energy India (NSE = POWERINDIA)
    "HITACHIENERGY":         "POWERINDIA.NS",
    "POWERINDIA":            "POWERINDIA.NS",
    "MUTHOOT":               "MUTHOOTFIN.NS", # Muthoot Finance (popular short alias)
    "BAJAJ FINANCE":         "BAJFINANCE.NS",
    "BAJAJFINANCE":          "BAJFINANCE.NS",
    "L&T":                   "LT.NS",
    "LNT":                   "LT.NS",
    "M&M":                   "M&M.NS",
    "MAHINDRA":              "M&M.NS",
    # ── BSE-only / wrong-suffix fixes ─────────────────────────────────────────
    "SHAKTIPUMPS":           "SHAKTIPUMP.NS", # Shakti Pumps — NSE ticker is SHAKTIPUMP (no S)
    "GEVERNOVA":             "522275.BO",     # GE Vernova T&D India Ltd — BSE code only in YF
    "GE VERNOVA":            "522275.BO",     # space alias
    "GETDINDIA":             "522275.BO",     # legacy GE T&D India alias
    "ELFORGE":               "ELFORGE.BO",   # E L Forge Ltd — BSE listed; .NS returns no data
}

# Cache for resolved symbols so we don't hit yfinance on every request
_symbol_cache: dict[str, str] = {}


def _load_symbol_resolutions() -> None:
    """
    Preload all rows from the symbol_resolutions Supabase table into
    _symbol_resolutions_cache and _symbol_cache at API startup.

    This means every manually-confirmed or auto-discovered resolution is
    available immediately, without any live probe, from the very first request.
    Silently skipped if the table doesn't exist yet (pre-migration).
    """
    if _supabase is None:
        return
    try:
        rows = (
            _supabase
            .table("symbol_resolutions")
            .select("input_symbol,yf_symbol")
            .execute()
            .data or []
        )
        for row in rows:
            k = (row.get("input_symbol") or "").upper().strip()
            v = (row.get("yf_symbol")    or "").strip()
            if k and v:
                _symbol_resolutions_cache[k] = v
                _symbol_cache[k] = v          # also prime the main request cache
        log.info("Loaded %d symbol resolutions from DB", len(rows))
    except Exception as exc:
        log.debug(
            "Could not load symbol_resolutions (table may not exist yet): %s", exc
        )


def _persist_resolution(input_sym: str, yf_sym: str, source: str = "auto") -> None:
    """
    Persist a successful resolution to the symbol_resolutions table.
    Best-effort — errors are logged at DEBUG, never raised.
    Also updates the in-process caches so subsequent requests skip the probe.
    """
    if _supabase is None:
        return
    key = input_sym.upper().strip()
    try:
        _supabase.table("symbol_resolutions").upsert(
            {
                "input_symbol": key,
                "yf_symbol":    yf_sym,
                "source":       source,
                "resolved_at":  datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="input_symbol",
        ).execute()
        _symbol_resolutions_cache[key] = yf_sym
        _symbol_cache[key]             = yf_sym
        log.info(
            "Symbol resolution persisted: %s → %s  (source=%s)", key, yf_sym, source
        )
    except Exception as exc:
        log.debug("Symbol resolution persist skipped: %s", exc)


def _search_yf_symbol(query: str) -> str | None:
    """
    Use yf.Search to find a valid NSE/BSE ticker for a company name or alias.

    yf.Search works well with company names ("Bharat Seats", "Indian Hotels")
    but NOT with raw NSE symbols ("BHARATSEAT") — those fail the standard
    .NS/.BO probes already and end up here only when both probes return no data.

    Returns the first validated ticker (NSE preferred over BSE), or None.
    """
    try:
        results = yf.Search(query, news_count=0, max_results=10)
        quotes  = getattr(results, "quotes", []) or []

        # Prefer NSE listings (exchange code = NSI)
        for exch_codes, suffix in (
            ({"NSI", "NSE"},     ".NS"),
            ({"BSE", "BOM", "BOM"},  ".BO"),
        ):
            for q in quotes:
                if (q.get("exchange") in exch_codes
                        and q.get("quoteType") == "EQUITY"):
                    ticker = q.get("symbol", "")
                    if ticker.endswith(suffix):
                        # Quick validation — must return a price
                        try:
                            hist = yf.Ticker(ticker).history(period="1d")
                            close = hist["Close"].dropna()
                            if not close.empty and float(close.iloc[-1]) > 0:
                                return ticker
                        except Exception:
                            pass
    except Exception as exc:
        log.debug("yf.Search('%s') failed: %s", query, exc)
    return None


def _resolve_yf_symbol(raw: str) -> str:
    """
    Maps any user-provided input to the correct yfinance ticker symbol.

    Resolution order:
      1. Exact match in _NSE_OVERRIDES (indices, ETFs, known aliases)
      2. DB-loaded symbol resolutions (startup-preloaded; updated at runtime)
      3. Already has a recognised suffix (.NS / .BO / =X / =F) or starts with ^
      4. Live probe: try {SYMBOL}.NS — validate with a 1-day history call
         → on success: persist resolution to DB
      5. Live probe: try {SYMBOL}.BO as BSE fallback
         → on success: persist resolution to DB
      6. yf.Search company-name lookup — handles brand names / display names
         that differ from the NSE ticker (e.g. "IHCL" → "INDHOTEL.NS")
         → on success: persist resolution to DB
      7. Default to {SYMBOL}.NS with a warning (last resort)

    Results are cached in _symbol_cache for the lifetime of the process.
    Steps 4–6 also persist to the symbol_resolutions Supabase table so the
    same lookup is instant on the next API restart.
    """
    key = raw.upper().strip()
    if key in _symbol_cache:
        return _symbol_cache[key]

    # 1. Known override (indices, ETFs, known brand-name aliases)
    if key in _NSE_OVERRIDES:
        result = _NSE_OVERRIDES[key]
        _symbol_cache[key] = result
        return result

    # Remove spaces for the remaining checks
    sym = key.replace(" ", "")

    # 1b. Space-stripped version may also be in overrides
    if sym != key and sym in _NSE_OVERRIDES:
        result = _NSE_OVERRIDES[sym]
        _symbol_cache[key] = result
        return result

    # 2. DB-loaded resolutions (persisted from previous successful lookups)
    for lookup_key in (key, sym):
        if lookup_key in _symbol_resolutions_cache:
            result = _symbol_resolutions_cache[lookup_key]
            _symbol_cache[key] = result
            return result

    # 3. Already has a suffix or is an index / forex symbol
    if (sym.endswith(".NS") or sym.endswith(".BO")
            or sym.endswith("=X") or sym.endswith("=F")
            or sym.startswith("^")):
        _symbol_cache[key] = sym
        return sym

    # 4 & 5. Live probe — try NSE first, then BSE
    for candidate in (f"{sym}.NS", f"{sym}.BO"):
        try:
            hist = yf.Ticker(candidate).history(period="1d")
            close = hist["Close"].dropna()
            if not close.empty and float(close.iloc[-1]) > 0:
                log.info("Symbol resolved via probe: %s → %s", raw, candidate)
                _symbol_cache[key] = candidate
                # Persist so next restart skips the probe
                _persist_resolution(sym, candidate, "probe")
                return candidate
        except Exception:
            pass

    # 6. yf.Search fallback — works for company names and brand aliases that
    #    don't match any NSE symbol directly (e.g. "IHCL", "Bharat Seats")
    search_result = _search_yf_symbol(raw)
    if search_result:
        log.info("Symbol resolved via yf.Search: %s → %s", raw, search_result)
        _symbol_cache[key] = search_result
        _symbol_resolutions_cache[sym] = search_result
        _persist_resolution(sym, search_result, "search")
        return search_result

    # 7. Last resort: assume NSE, but log a warning so broken symbols are visible
    fallback = f"{sym}.NS"
    log.warning(
        "Symbol resolution defaulted (no data found): %s → %s  "
        "(add a manual override via POST /api/symbol/override if this is wrong)",
        raw, fallback,
    )
    _symbol_cache[key] = fallback
    return fallback


def _fetch_current_price(yf_symbol: str) -> float | None:
    """
    Fetches the latest closing price for a single yfinance symbol.

    Uses progressively longer periods so BSE-only stocks (e.g. 522275.BO)
    that sometimes return empty on short windows still get a price.
    progress= param was removed in yfinance ≥1.0 — never pass it.
    """
    ticker = yf.Ticker(yf_symbol)
    for period in ("1d", "5d", "1mo"):
        try:
            hist = ticker.history(period=period)["Close"].dropna()
            if not hist.empty:
                return float(hist.iloc[-1])
        except Exception:
            pass
    return None


# =============================================================================
# WebSocket connection manager
# =============================================================================

class ConnectionManager:
    def __init__(self) -> None:
        self._conns: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._conns.add(ws)
        log.info("WS connected  — %d active", len(self._conns))

    def disconnect(self, ws: WebSocket) -> None:
        self._conns.discard(ws)
        log.info("WS disconnected — %d active", len(self._conns))

    async def broadcast(self, payload: dict) -> None:
        dead: set[WebSocket] = set()
        for ws in self._conns:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.add(ws)
        self._conns -= dead

    @property
    def active(self) -> bool:
        return bool(self._conns)


manager = ConnectionManager()


# =============================================================================
# Background task — alert broadcaster
# =============================================================================

async def _alert_broadcaster() -> None:
    """
    Every 30 s: check Supabase for unresolved DANGER/CRITICAL portfolio alerts
    and broadcast them to all connected WebSocket clients.
    Applies the same dedup as /api/governance/alerts so the WebSocket doesn't
    override the deduplicated list with all raw rows.
    """
    while True:
        try:
            await asyncio.sleep(30)
            if not manager.active or _supabase is None:
                continue

            result = (
                _supabase
                .table("portfolio_alerts")
                .select("*")
                .eq("resolved", False)
                .in_("severity", ["DANGER", "CRITICAL"])
                .order("created_at", desc=True)
                .execute()
            )
            if result.data:
                # Dedup: keep only the most recent alert per (alert_type, holding_id/symbol)
                _seen: set[str] = set()
                deduped = []
                for row in result.data:
                    key = (
                        f"{row.get('alert_type','?')}::"
                        f"{row.get('holding_id') or row.get('symbol') or row.get('title','?')}"
                    )
                    if key not in _seen:
                        _seen.add(key)
                        deduped.append(row)
                await manager.broadcast({
                    "type":      "critical_alert",
                    "alerts":    deduped,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.warning("alert_broadcaster: %s", exc)


# =============================================================================
# App lifespan — start/stop background broadcaster
# =============================================================================

@asynccontextmanager
async def lifespan(_app: FastAPI):          # noqa: RUF029
    # Preload symbol resolutions from DB so brand-name aliases work immediately
    # without any per-request Yahoo Finance probe
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _load_symbol_resolutions)

    task = asyncio.create_task(_alert_broadcaster())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# =============================================================================
# FastAPI app + middleware
# =============================================================================

_allowed_origins = VERCEL_DASHBOARD_URL.split() if VERCEL_DASHBOARD_URL else ["*"]

app = FastAPI(
    title       = "Bharat Intelligence API",
    description = "Live data backend for the React dashboard",
    version     = "1.0.0",
    lifespan    = lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins     = _allowed_origins,
    allow_credentials = True,
    allow_methods     = ["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers     = ["*"],
)


# =============================================================================
# Auth dependency
# =============================================================================

_api_key_header = APIKeyHeader(name="x-api-key", auto_error=False)


async def require_api_key(key: str | None = Depends(_api_key_header)) -> None:
    """Validates x-api-key header. Skipped when DASHBOARD_API_KEY is not set."""
    if not DASHBOARD_API_KEY:
        return                              # open in local dev
    if key != DASHBOARD_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing x-api-key")


def _db() -> Client:
    """Returns Supabase client or raises 503."""
    if _supabase is None:
        raise HTTPException(status_code=503, detail="Database not configured")
    return _supabase


# =============================================================================
# Data transformers  (DB snake_case → camelCase for React)
# =============================================================================

def _fmt_inr(val: float | int | None) -> str:
    """Formats a number as ₹X,XXX (no decimals)."""
    if val is None:
        return "—"
    return f"₹{float(val):,.0f}"


def _transform_holding(row: dict) -> dict:
    """portfolio_holdings DB row → React holding shape."""
    danger_sources = row.get("danger_sources") or []
    if isinstance(danger_sources, str):
        danger_sources = [danger_sources]

    status_map = {"OPEN": "holding", "CLOSED": "exited", "PARTIAL": "partial"}
    db_status  = (row.get("status") or "OPEN").upper()

    # ── P7-A: dynamic target tracking fields ─────────────────────────────────
    orig_target   = row.get("original_target")
    upd_count     = int(row.get("target_update_count") or 0)
    ratchet_level = row.get("stoploss_ratchet_level") or "ORIGINAL"
    protect_gains = bool(row.get("protect_gains_flag") or False)

    return {
        "id":               row["id"],
        "symbol":           row["symbol"],
        "name":             row.get("name") or row["symbol"],
        "sector":           row.get("sector") or "—",
        "qty":              int(row.get("qty") or 0),
        "avgBuy":           float(row.get("avg_buy") or 0),
        "currentPrice":     float(row.get("current_price") or row.get("avg_buy") or 0),
        "buyDate":          str(row.get("buy_date") or ""),
        "linkedRecId":      row.get("linked_rec_id"),
        "notes":            row.get("notes") or "",
        "targetPrice":      float(row.get("target_price") or 0),
        "stoplossPrice":    float(row.get("stoploss_price") or 0),
        "status":           status_map.get(db_status, "holding"),
        "dangerDropPct":    float(row.get("danger_drop_pct") or 0),
        "dangerConfidence": float(row.get("danger_confidence") or 0),
        "dangerTrigger":    row.get("danger_trigger"),
        "dangerWindow":     str(row.get("danger_window") or ""),
        "dangerSources":    danger_sources,
        "earningsAlert":    row.get("_earnings_alert"),  # injected by get_portfolio
        # P7-A: dynamic target tracking
        "originalTarget":        float(orig_target) if orig_target is not None else None,
        "targetUpdateCount":     upd_count,
        "targetUpdatedAt":       str(row.get("target_updated_at") or ""),
        "protectGainsFlag":      protect_gains,
        "stoplossRatchetLevel":  ratchet_level,
        "lastReviewAt":          str(row.get("last_review_at") or ""),
    }


def _transform_recommendation(row: dict) -> dict:
    """recommendations DB row → React recommendation shape."""
    el  = row.get("entry_low")
    eh  = row.get("entry_high")
    tgt = row.get("target")
    sl  = row.get("stoploss")

    if el and eh:
        entry_str = f"₹{float(el):,.0f}–₹{float(eh):,.0f}"
    elif el:
        entry_str = _fmt_inr(el)
    else:
        entry_str = "—"

    meta           = row.get("metadata") or {}
    agent_signals  = row.get("agent_signals") or {}
    gov            = row.get("gov_check")     or {}
    horizon        = row.get("horizon_days")

    # warren_bot is stored nested inside agent_signals JSONB by the orchestrator.
    # Extract it and surface it as a top-level field so the frontend can render
    # the Buffett quality panel without digging into the agents dict.
    warren_bot_data: dict | None = None
    agents: dict = {}
    if isinstance(agent_signals, dict):
        warren_bot_data = agent_signals.get("warren_bot")   # None if absent
        agents = {k: v for k, v in agent_signals.items() if k != "warren_bot"}
    else:
        agents = agent_signals

    return {
        "id":               row["id"],
        "symbol":           row["symbol"],
        "action":           row.get("action") or "HOLD",
        "confidence":       float(row.get("confidence") or 0),
        "riskScore":        float(row.get("risk_score") or 0),
        "entry":            entry_str,
        "entryLow":         el,
        "entryHigh":        eh,
        "target":           _fmt_inr(tgt),
        "targetNum":        tgt,
        "stoploss":         _fmt_inr(sl),
        "stoplossNum":      sl,
        "horizon":          f"{horizon} days" if horizon else "—",
        "validTill":        str(row.get("valid_till") or ""),
        "headline":         row.get("headline") or "",
        "summary":          row.get("summary") or "",
        "upsidePct":        float(row.get("upside_pct") or 0),
        "upsideConfidence": float(row.get("upside_confidence") or 0),
        "isDiscovery":      bool(row.get("is_discovery")),
        "agents":           agents,
        "warrenBot":        warren_bot_data,   # None if warren_bot hasn't run for this rec
        "govCheck":         gov,
        "createdAt":        str(row.get("created_at") or ""),
        # Discovery-tab extra fields (stored in metadata by the discovery screener)
        "discoveryScore":   meta.get("discovery_score")  or 0,
        "discoveryReason":  meta.get("discovery_reason") or "",
        # Discovery streak — how many times this stock appeared within the cooldown window
        "discoveryCount":   int(meta.get("discovery_count") or 1),
        "discoveryDates":   meta.get("discovery_dates")   or [],
        "lastConfirmedAt":  meta.get("last_confirmed_at") or str(row.get("created_at") or "")[:10],
        "screenTriggers":   meta.get("screen_triggers")  or [],
        "risks":            meta.get("risks")             or [],
        "catalysts":        meta.get("catalysts")         or [],
        "upsideBasis":      meta.get("upside_basis")      or "",
        "upsideHorizon":    meta.get("upside_horizon")    or "",
        "name":             meta.get("name")              or row["symbol"],
        "sector":           meta.get("sector")            or "",
        "price":            meta.get("price")             or 0,
        "change":           meta.get("change")            or 0,
        "pe":               meta.get("pe")                or 0,
        "mktCap":           meta.get("mkt_cap")           or "",
        "notInPortfolio":   True,
        # Impact-cost / liquidity (stored in metadata by discovery screener)
        "liquidityTier":    meta.get("liquidity_tier")    or None,
        "impactCostPct":    meta.get("impact_cost_pct")   or None,
        # Forward estimates (from fundamental agent or metadata)
        "forwardPe":        meta.get("forward_pe")        or None,
        "pegRatio":         meta.get("peg_ratio_fwd") or meta.get("peg_ratio") or None,
        "epsGrowthPct":     meta.get("eps_growth_pct")    or None,
        # P3-A: Position sizing (top-level DB columns, None for legacy recs)
        "suggestedPositionPct": row.get("suggested_position_pct"),
        "positionLabel":        row.get("position_label") or "",
    }


def _transform_research(row: dict) -> dict:
    """research_proposals DB row → React AI_RESEARCH_FEED shape."""
    debate_log = row.get("debate_log") or []
    v_for      = sum(1 for d in debate_log if str(d.get("stance","")).upper() == "FOR")
    v_against  = sum(1 for d in debate_log if str(d.get("stance","")).upper() == "AGAINST")
    v_abstain  = sum(1 for d in debate_log if str(d.get("stance","")).upper() == "ABSTAIN")

    status = str(row.get("status") or "pending").lower()
    if status in ("approved", "implemented"):
        debate_status = "approved"
    elif v_for > 0 and v_against > 0 and v_for == v_against:
        debate_status = "debating"
    else:
        debate_status = "pending"

    meta    = row.get("metadata") or {}
    created = str(row.get("created_at") or "")

    return {
        "id":             row["id"],
        "type":           meta.get("type")  or "whitepaper",
        "date":           created[:10],
        "source":         row.get("source") or "",
        "title":          row.get("title")  or "",
        "relevance":      int(row.get("relevance") or 0),
        "summary":        row.get("summary")          or "",
        "proposedChange": row.get("proposed_change")  or "",
        "impactedAgents": row.get("impacted_agents")  or [],
        "costImpact":     str(row.get("cost_impact")  or "medium"),
        "debateStatus":   debate_status,
        "status":         status,
        "votes":          {"for": v_for, "against": v_against, "abstain": v_abstain},
        "debateLog":      debate_log,
        "prUrl":          row.get("pr_url"),
        "url":            row.get("url"),
        "tag":            meta.get("tag") or "Research",
        "createdAt":      created,
    }


# =============================================================================
# Market pulse helpers
# =============================================================================

def _fetch_prices_sync() -> dict[str, tuple[float, float]]:
    """
    Fetches closing prices for all MARKET_SYMBOLS via yfinance.
    Returns {yf_symbol: (last_close, prev_close)}.
    Run via loop.run_in_executor to avoid blocking the event loop.

    yfinance 1.2.x column-structure note:
      Multi-ticker download returns (Price, Ticker) MultiIndex columns:
        df["Close"]["^NSEI"]  — use this form
      Older yfinance returned (Ticker, Price):
        df["^NSEI"]["Close"]  — fallback tried if new form fails
    """
    syms = [sym for _, sym, _ in MARKET_SYMBOLS]
    out: dict[str, tuple[float, float]] = {}

    def _extract(df, sym: str):
        """Try yfinance 1.2.x column format first, then legacy format."""
        for accessor in [
            lambda: df["Close"][sym],         # yfinance 1.2.x: (Price, Ticker)
            lambda: df[sym]["Close"],          # legacy: (Ticker, Price)
            lambda: df["Close"],               # single-ticker flat
        ]:
            try:
                s = accessor().dropna()
                if len(s):
                    return s
            except Exception:
                continue
        return None

    try:
        df = yf.download(
            syms,
            period      = "2d",
            interval    = "1d",
            auto_adjust = True,
            # progress / group_by removed — deprecated/removed in yfinance 1.2.x
        )
        for sym in syms:
            try:
                closes = _extract(df, sym)
                if closes is not None and len(closes) >= 2:
                    out[sym] = (float(closes.iloc[-1]), float(closes.iloc[-2]))
                elif closes is not None and len(closes) == 1:
                    out[sym] = (float(closes.iloc[0]), float(closes.iloc[0]))
            except Exception:
                pass
    except Exception as exc:
        log.warning("yfinance batch download failed: %s", exc)

    # Per-symbol fallback for any sym not yet in out
    missing = [s for s in syms if s not in out]
    if missing:
        log.debug("Market pulse: fetching %d symbols individually", len(missing))
    for sym in missing:
        try:
            closes = yf.Ticker(sym).history(period="5d")["Close"].dropna()
            if len(closes) >= 2:
                out[sym] = (float(closes.iloc[-1]), float(closes.iloc[-2]))
            elif len(closes) == 1:
                out[sym] = (float(closes.iloc[0]), float(closes.iloc[0]))
        except Exception:
            pass

    return out


def _build_pulse(prices: dict[str, tuple[float, float]], fii_row: dict | None) -> list[dict]:
    """Formats raw prices into [{key, value, change, up}] shape the React ticker expects."""
    # USDINR for commodity conversion (gold/crude USD→INR)
    usdinr = prices.get("USDINR=X", (83.5, 83.5))[0]

    pulse: list[dict] = []
    for key, sym, fmt in MARKET_SYMBOLS:
        pair = prices.get(sym)
        if pair:
            price, prev = pair
            chg_pct = ((price - prev) / prev * 100) if prev else 0

            if fmt == "index":
                value  = f"{price:,.0f}"
                change = f"{chg_pct:+.1f}%"
            elif fmt == "inr_usd":
                # Gold:  USD/troy-oz → INR/10g   (1 oz = 31.1035g)
                # Crude: USD/bbl     → INR/bbl
                inr = price * usdinr * (10 / 31.1035) if sym == "GC=F" else price * usdinr
                value  = f"₹{inr:,.0f}"
                change = f"{chg_pct:+.1f}%"
            elif fmt == "rate":
                value  = f"{price:.2f}"
                change = f"{chg_pct:+.2f}%"
            else:                                 # vix
                value  = f"{price:.1f}"
                change = f"{chg_pct:+.1f}"

            pulse.append({"key": key, "value": value, "change": change, "up": chg_pct >= 0})
        else:
            pulse.append({"key": key, "value": "—", "change": "—", "up": True})

    # FII NET — from Supabase institutional_flows
    if fii_row:
        fii   = float(fii_row.get("fii_net") or 0)
        sign  = "+" if fii >= 0 else ""
        pulse.append({
            "key":    "FII NET",
            "value":  f"{sign}₹{abs(fii):,.0f} Cr",
            "change": "buy" if fii >= 0 else "sell",
            "up":     fii >= 0,
        })

    return pulse


async def _get_market_pulse() -> list[dict]:
    """Returns market pulse, refreshed at most every MARKET_CACHE_TTL seconds."""
    global _market_cache, _market_cache_ts
    if time.time() - _market_cache_ts < MARKET_CACHE_TTL and _market_cache:
        return _market_cache

    loop      = asyncio.get_event_loop()
    prices_fut = loop.run_in_executor(None, _fetch_prices_sync)

    fii_row: dict | None = None
    if _supabase:
        try:
            res = (
                _supabase
                .table("institutional_flows")
                .select("fii_net, dii_net, session_date")
                .order("session_date", desc=True)
                .limit(1)
                .execute()
            )
            fii_row = (res.data or [None])[0]
        except Exception as exc:
            log.warning("FII fetch failed: %s", exc)

    prices           = await prices_fut
    pulse            = _build_pulse(prices, fii_row)

    # ── P6-D-7: GIFT Nifty pre-market signal ─────────────────────────────────
    try:
        from data.gift_nifty_fetcher import get_gift_nifty_signal
        gift = get_gift_nifty_signal()
        if gift.get("signal") != "UNAVAILABLE" and gift.get("premium_pct") is not None:
            arrow = "▲" if gift["signal"] == "POSITIVE_OPEN" else ("▼" if gift["signal"] == "NEGATIVE_OPEN" else "→")
            strength_suffix = f" ({gift.get('signal_strength', '')})" if gift.get('signal_strength') != "WEAK" else ""
            pulse.append({
                "key":    "GIFT NIFTY",
                "value":  f"{gift['premium_pct']:+.2f}% {arrow}{strength_suffix}",
                "change": gift["signal"].lower(),
                "up":     gift["signal"] == "POSITIVE_OPEN",
                "note":   gift.get("market_note"),
            })
    except Exception as exc:
        log.debug("GIFT Nifty pulse integration skipped: %s", exc)

    _market_cache    = pulse
    _market_cache_ts = time.time()
    return pulse


# =============================================================================
# Endpoints
# =============================================================================

@app.get("/health", tags=["infra"])
async def health():
    """Quick health check — no auth required."""
    return {
        "status":    "ok",
        "db":        _supabase is not None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── 1. Recommendations ─────────────────────────────────────────────────────────

@app.get("/api/recommendations", tags=["recommendations"])
async def get_recommendations(
    limit: int     = Query(50, ge=1, le=200),
    _:    None     = Depends(require_api_key),
):
    """
    Latest recommendations sorted critical-first, then by upside_pct desc.
    Critical = upside_pct >= 100 AND upside_confidence >= 70.
    """
    rows = (_db()
            .table("recommendations")
            .select("*")
            .order("upside_pct", desc=True)
            .limit(limit)
            .execute()
            .data or [])

    # Deduplicate by symbol — keep only the row with the highest upside_pct
    # (DB may have multiple rows per symbol from different run dates; the UI
    # should show one card per stock, not one card per run).
    seen: dict[str, dict] = {}
    for row in rows:
        sym = row["symbol"]
        if sym not in seen or (row.get("upside_pct") or 0) > (seen[sym].get("upside_pct") or 0):
            seen[sym] = row
    rows = list(seen.values())

    recs = [_transform_recommendation(r) for r in rows]
    recs.sort(key=lambda r: (0 if (r["upsidePct"] >= 100 and r["upsideConfidence"] >= 70) else 1,
                              -r["upsidePct"]))
    return recs


# ── 2. Discovery ───────────────────────────────────────────────────────────────

@app.get("/api/discovery", tags=["recommendations"])
async def get_discovery(
    _: None = Depends(require_api_key),
):
    """
    Returns is_discovery=true recommendations created today.
    Falls back to last 14 days when today has no rows.
    The valid_till filter is intentionally NOT applied in the fallback — discovery
    ideas are still research-worthy even when technically "expired" (they have
    fresh prices refreshed below), and removing the filter prevents the dashboard
    from going blank when the scheduler hasn't run today yet.
    Live current_price is refreshed from yfinance for every returned symbol (same pattern
    as GET /api/portfolio) so the UI never shows stale entry prices.
    """
    db    = _db()
    today = date.today().isoformat()

    rows = (db.table("recommendations")
              .select("*")
              .eq("is_discovery", True)
              .gte("created_at", today)
              .order("upside_pct", desc=True)
              .execute()
              .data or [])

    if not rows:
        # 14-day fallback — no valid_till filter so expired ideas still visible.
        # The UI shows a subtle "stale" indicator via the validTill field.
        two_weeks_ago = (date.today() - timedelta(days=14)).isoformat()
        rows = (db.table("recommendations")
                  .select("*")
                  .eq("is_discovery", True)
                  .gte("created_at", two_weeks_ago)
                  .order("created_at", desc=True)
                  .limit(20)
                  .execute()
                  .data or [])

    # ── Refresh live prices (mirrors GET /api/portfolio pattern) ─────────────────
    # Discovery rows have metadata.price set at write-time by _save_discovery().
    # We overwrite it here with a fresh yfinance quote so the UI always shows the
    # current market price regardless of when the recommendation was created.
    if rows:
        def _refresh_discovery_prices(rows_: list[dict]) -> list[dict]:
            updated: list[dict] = []
            for row in rows_:
                yf_sym = _resolve_yf_symbol(row["symbol"])
                price  = _fetch_current_price(yf_sym)
                if price:
                    meta = dict(row.get("metadata") or {})
                    meta["price"] = round(price, 2)
                    row = {**row, "metadata": meta}
                updated.append(row)
            return updated

        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(None, _refresh_discovery_prices, rows)

    # Deduplicate by symbol — keep only the most recent row per symbol.
    # Multiple rows can exist from different daily screener runs or when the
    # 10-day cooldown window straddles the lookback window.
    seen_disc: dict[str, dict] = {}
    for row in rows:
        sym = row["symbol"]
        if sym not in seen_disc:
            seen_disc[sym] = row
        else:
            # Keep row with highest discovery_count (streak), then by created_at
            existing_count = (seen_disc[sym].get("metadata") or {}).get("discovery_count", 1)
            new_count      = (row.get("metadata") or {}).get("discovery_count", 1)
            if new_count > existing_count:
                seen_disc[sym] = row
            elif new_count == existing_count:
                # Same streak — prefer most recently created
                if (row.get("created_at") or "") > (seen_disc[sym].get("created_at") or ""):
                    seen_disc[sym] = row
    rows = list(seen_disc.values())

    recs = [_transform_recommendation(r) for r in rows]
    recs.sort(key=lambda r: (0 if (r["upsidePct"] >= 100 and r["upsideConfidence"] >= 70) else 1,
                              -r["upsidePct"]))
    return recs


# ── 2b. Discovery runs log ────────────────────────────────────────────────────

@app.get("/api/discovery/runs", tags=["recommendations"])
async def get_discovery_runs(
    days: int  = Query(7, ge=1, le=30, description="How many past days to return"),
    _: None    = Depends(require_api_key),
):
    """
    Returns the last N days of discovery screener run logs from discovery_runs table.
    Each row contains which symbols were pre-screened, which passed filters, and which
    were promoted to full recommendations — powers the dashboard 'Daily Screened Stocks'
    collapsible panel.

    Returns [] when the table doesn't exist yet (pre-migration) so the UI degrades
    gracefully.
    """
    try:
        db        = _db()
        cutoff    = (date.today() - timedelta(days=days)).isoformat()
        rows      = (db.table("discovery_runs")
                      .select("run_date,slice_symbols,passed_symbols,discovery_symbols,"
                              "coverage_stats,total_screened,total_passed,total_discoveries,"
                              "created_at")
                      .gte("run_date", cutoff)
                      .order("run_date", desc=True)
                      .limit(days)
                      .execute()
                      .data or [])
        return [
            {
                "runDate":          r.get("run_date"),
                "totalScreened":    r.get("total_screened", 0),
                "totalPassed":      r.get("total_passed", 0),
                "totalDiscoveries": r.get("total_discoveries", 0),
                "sliceSymbols":     r.get("slice_symbols") or [],
                "passedSymbols":    r.get("passed_symbols") or [],
                "discoverySymbols": r.get("discovery_symbols") or [],
                "coverageStats":    r.get("coverage_stats") or {},
                "createdAt":        r.get("created_at"),
            }
            for r in rows
        ]
    except Exception as exc:
        log.warning("GET /api/discovery/runs failed: %s", exc)
        return []


# ── 3 & 4. Portfolio ───────────────────────────────────────────────────────────

@app.get("/api/portfolio", tags=["portfolio"])
async def get_portfolio(
    refresh_prices: bool = Query(True, description="Refresh current_price from yfinance"),
    _:              None = Depends(require_api_key),
):
    """
    Returns all OPEN portfolio holdings, danger holdings first.
    By default, refreshes current_price for every holding from yfinance (run in executor).
    Pass ?refresh_prices=false to skip the live-price fetch and return stored prices only.
    """
    rows = (_db()
            .table("portfolio_holdings")
            .select("*")
            .eq("status", "OPEN")
            .order("created_at", desc=True)
            .execute()
            .data or [])

    # Refresh live prices via yfinance (non-blocking — runs in thread executor)
    if refresh_prices and rows:
        def _refresh_all(rows_: list[dict]) -> list[dict]:
            updated: list[dict] = []
            for row in rows_:
                yf_sym = _resolve_yf_symbol(row.get("yf_symbol") or row["symbol"])
                price  = _fetch_current_price(yf_sym)
                if price:
                    row = {**row, "current_price": price}
                    # Persist the refreshed price back to DB (best-effort, no await)
                    try:
                        _supabase and _supabase.table("portfolio_holdings").update(
                            {"current_price": price, "yf_symbol": yf_sym}
                        ).eq("id", row["id"]).execute()
                    except Exception:
                        pass
                updated.append(row)
            return updated

        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(None, _refresh_all, rows)

    # ── Earnings alert enrichment (best-effort) ────────────────────────────────
    try:
        from agents.earnings_guard import check_pre_earnings
        from concurrent.futures import ThreadPoolExecutor
        symbols = [r["symbol"] for r in rows]

        def _check_one(sym):
            eg = check_pre_earnings(sym, days_window=7)
            return sym, eg if eg["has_upcoming_earnings"] else None

        with ThreadPoolExecutor(max_workers=6) as pool:
            eg_results = dict(pool.map(lambda s: _check_one(s), symbols))

        for r in rows:
            r["_earnings_alert"] = eg_results.get(r["symbol"])
    except Exception:
        pass  # non-fatal — skip earnings enrichment

    holdings = [_transform_holding(r) for r in rows]
    # Critical danger holdings first
    holdings.sort(key=lambda h: 0 if (h["dangerDropPct"] >= 70 and h["dangerConfidence"] >= 65) else 1)
    return holdings


@app.post("/api/portfolio", status_code=201, tags=["portfolio"])
async def upsert_portfolio(
    payload: dict[str, Any],
    _:       None = Depends(require_api_key),
):
    """
    Add a new holding or update an existing OPEN one (matched by symbol).

    Required body field: symbol  (plain NSE name like RELIANCE, HDFC, or ZOMATO —
                                  the backend auto-resolves to the correct yfinance ticker)
    Optional: name, sector, qty, avg_buy, target_price, stoploss_price,
              notes, linked_rec_id, status, current_price

    Auto-resolution:
      • "RELIANCE"  → RELIANCE.NS
      • "HDFCBANK"  → HDFCBANK.NS
      • "GOLD"      → GC=F  (international gold futures)
      • "SENSEX"    → ^BSESN
    Live current_price is fetched from yfinance if not supplied by the caller.
    """
    db  = _db()
    raw = (payload.get("symbol") or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="symbol is required")

    # ── Symbol resolution (runs in thread to avoid blocking the event loop) ──────
    loop          = asyncio.get_event_loop()
    yf_symbol     = await loop.run_in_executor(None, _resolve_yf_symbol, raw)

    # Normalise stored symbol: use upper-case base name (without suffix) for display,
    # keep yf_symbol in a separate metadata field so GET /api/portfolio can refresh it.
    display_symbol = raw.upper().replace(" ", "")

    # ── Fetch current price if caller didn't supply one ──────────────────────────
    supplied_price = payload.get("current_price") or payload.get("avg_buy")
    if supplied_price:
        current_price: float | None = float(supplied_price)
    else:
        current_price = await loop.run_in_executor(None, _fetch_current_price, yf_symbol)

    log.info("Portfolio upsert: %s → yf=%s  price=%.2f",
             display_symbol, yf_symbol, current_price or 0)

    existing = (db.table("portfolio_holdings")
                  .select("id")
                  .eq("symbol", display_symbol)
                  .eq("status", "OPEN")
                  .limit(1)
                  .execute()
                  .data or [])

    if existing:
        # ── UPDATE: only touch fields explicitly present in the payload ─────────
        # This is critical for partial-sell and status-only updates — we must NOT
        # clobber qty/avg_buy/sector/name with defaults when only status is sent.
        update_row: dict[str, Any] = {"yf_symbol": yf_symbol}
        if "name"          in payload and payload["name"]:          update_row["name"]          = payload["name"]
        if "sector"        in payload and payload["sector"]:        update_row["sector"]        = payload["sector"]
        if "qty"           in payload and payload["qty"] is not None: update_row["qty"]         = int(payload["qty"])
        if "avg_buy"       in payload and payload["avg_buy"]:       update_row["avg_buy"]       = float(payload["avg_buy"])
        if current_price is not None:                               update_row["current_price"] = current_price
        if "target_price"  in payload:                              update_row["target_price"]  = float(payload["target_price"]  or 0) or None
        if "stoploss_price" in payload:                             update_row["stoploss_price"]= float(payload["stoploss_price"] or 0) or None
        if "notes"         in payload:                              update_row["notes"]         = payload["notes"] or ""
        if "linked_rec_id" in payload:                              update_row["linked_rec_id"] = payload["linked_rec_id"]
        if "status"        in payload:                              update_row["status"]        = payload["status"]
        result = (db.table("portfolio_holdings")
                    .update(update_row)
                    .eq("id", existing[0]["id"])
                    .execute())
    else:
        # ── INSERT: require all fields, use sensible defaults for optional ones ──
        row: dict[str, Any] = {
            "symbol":         display_symbol,
            "yf_symbol":      yf_symbol,
            "name":           payload.get("name")           or display_symbol,
            "sector":         payload.get("sector")         or "—",
            "qty":            int(payload.get("qty") or 1),
            "avg_buy":        float(payload.get("avg_buy") or 0),
            "current_price":  current_price,
            "target_price":   float(payload.get("target_price")  or 0) or None,
            "stoploss_price": float(payload.get("stoploss_price") or 0) or None,
            "notes":          payload.get("notes")          or "",
            "linked_rec_id":  payload.get("linked_rec_id"),
            "status":         payload.get("status")         or "OPEN",
        }
        result = db.table("portfolio_holdings").insert(row).execute()

    if result.data:
        return _transform_holding(result.data[0])
    raise HTTPException(status_code=500, detail="Upsert returned no data")


# ── 4b. Symbol resolver ────────────────────────────────────────────────────────

@app.get("/api/symbol/resolve", tags=["portfolio"])
async def resolve_symbol(
    q: str  = Query(..., min_length=1, description="Raw symbol or company name to resolve"),
    _: None = Depends(require_api_key),
):
    """
    Maps any user-provided string to the correct yfinance ticker symbol.

    Examples
    --------
      ?q=RELIANCE   → {"input": "RELIANCE", "yf_symbol": "RELIANCE.NS",  "exchange": "NSE"}
      ?q=HDFCBANK   → {"input": "HDFCBANK",  "yf_symbol": "HDFCBANK.NS",  "exchange": "NSE"}
      ?q=GOLD       → {"input": "GOLD",       "yf_symbol": "GC=F",         "exchange": "COMEX"}
      ?q=SENSEX     → {"input": "SENSEX",     "yf_symbol": "^BSESN",       "exchange": "INDEX"}
      ?q=ZOMATO.NS  → {"input": "ZOMATO.NS",  "yf_symbol": "ZOMATO.NS",    "exchange": "NSE"}

    Also attempts to fetch the current price so the frontend can show a
    confirmation (e.g. "Found RELIANCE.NS — ₹2,847").
    """
    loop      = asyncio.get_event_loop()
    yf_symbol = await loop.run_in_executor(None, _resolve_yf_symbol, q)
    price     = await loop.run_in_executor(None, _fetch_current_price, yf_symbol)

    # Derive a human-readable exchange label
    if yf_symbol.startswith("^"):
        exchange = "INDEX"
    elif yf_symbol.endswith("=X"):
        exchange = "FOREX"
    elif yf_symbol.endswith("=F"):
        exchange = "COMEX"
    elif yf_symbol.endswith(".BO"):
        exchange = "BSE"
    else:
        exchange = "NSE"

    return {
        "input":      q,
        "yf_symbol":  yf_symbol,
        "exchange":   exchange,
        "price":      price,
        "price_str":  f"₹{price:,.2f}" if price and exchange in ("NSE", "BSE", "INDEX") else (f"{price:.4f}" if price else None),
        "resolved":   True,
    }


# ── 4b-ii. Liquidity / impact-cost probe ─────────────────────────────────────

@app.get("/api/symbol/liquidity", tags=["portfolio"])
async def symbol_liquidity(
    q:            str   = Query(..., min_length=1, description="NSE symbol"),
    trade_value:  float = Query(500_000, description="Trade size in INR (default ₹5 L)"),
    _:            None  = Depends(require_api_key),
):
    """
    Estimate impact cost (slippage) for executing a trade of `trade_value` INR
    in the given symbol.

    Returns
    -------
    {
      symbol, impact_cost_pct, liquidity_tier, avg_daily_volume_inr,
      avg_spread_pct, participation_rate, data_days, source, error
    }

    liquidity_tier: HIGH | MEDIUM | LOW | ILLIQUID | UNKNOWN
    """
    loop   = asyncio.get_event_loop()
    plain  = q.replace(".NS", "").replace(".BO", "").upper()

    def _run():
        from data.impact_cost import estimate_impact_cost
        return estimate_impact_cost(plain, trade_value_inr=trade_value)

    result = await loop.run_in_executor(None, _run)
    return result


# ── 4c. Symbol override (manual fix) ─────────────────────────────────────────

@app.post("/api/symbol/override", tags=["portfolio"])
async def override_symbol(
    payload: dict[str, Any],
    _:       None = Depends(require_api_key),
):
    """
    Manually fix the yfinance ticker for a symbol that can't be auto-resolved.

    Body: {"symbol": "IHCL", "yf_symbol": "INDHOTEL.NS"}

    Validates the supplied ticker has live price data, then:
      • Persists to symbol_resolutions table (survives restarts)
      • Updates the in-process caches immediately (takes effect without restart)
      • Patches all OPEN portfolio holdings that use this symbol with the correct
        yf_symbol and the latest price

    Use GET /api/portfolio/broken to discover which symbols need fixing.
    """
    raw    = (payload.get("symbol")    or "").strip().upper()
    yf_sym = (payload.get("yf_symbol") or "").strip()
    if not raw or not yf_sym:
        raise HTTPException(
            status_code=400,
            detail="Both 'symbol' (NSE name) and 'yf_symbol' (Yahoo ticker) are required",
        )

    # Validate the supplied ticker actually returns price data
    loop  = asyncio.get_event_loop()
    price = await loop.run_in_executor(None, _fetch_current_price, yf_sym)
    if price is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"No price data found for '{yf_sym}' — please check the ticker is correct "
                f"(e.g. INDHOTEL.NS, not IHCL.NS)"
            ),
        )

    # Persist to DB and update in-process caches
    await loop.run_in_executor(None, _persist_resolution, raw, yf_sym, "manual")
    # Also update _symbol_cache directly (persist_resolution only updates if DB write succeeds)
    _symbol_cache[raw] = yf_sym
    _symbol_resolutions_cache[raw] = yf_sym

    # Patch all OPEN holdings that use this symbol
    updated_holdings = 0
    if _supabase:
        try:
            res = (
                _supabase
                .table("portfolio_holdings")
                .update({"yf_symbol": yf_sym, "current_price": price})
                .eq("symbol", raw)
                .eq("status", "OPEN")
                .execute()
            )
            updated_holdings = len(res.data or [])
        except Exception as exc:
            log.warning("override_symbol: portfolio update failed for %s: %s", raw, exc)

    log.info(
        "Symbol override applied: %s → %s  price=%.2f  holdings_updated=%d",
        raw, yf_sym, price, updated_holdings,
    )
    return {
        "symbol":           raw,
        "yf_symbol":        yf_sym,
        "price":            price,
        "source":           "manual",
        "updated_holdings": updated_holdings,
    }


# ── 4d. Broken portfolio symbols ──────────────────────────────────────────────

@app.get("/api/portfolio/broken", tags=["portfolio"])
async def get_broken_portfolio_symbols(
    _: None = Depends(require_api_key),
):
    """
    Returns OPEN portfolio holdings where current_price is null or zero.

    These holdings have a yfinance ticker that doesn't return price data —
    usually because the user added the stock by its NSE display name or brand
    name rather than the exact Yahoo Finance ticker.

    For each broken symbol, we attempt a yf.Search() suggestion so the
    dashboard can show a one-click fix (calls POST /api/symbol/override).

    Response:
      {
        "broken": [
          {
            "id":           "<uuid>",
            "symbol":       "IHCL",
            "yf_symbol":    "IHCL.NS",       ← what's stored (wrong)
            "name":         "Indian Hotels",
            "avg_buy":      250.0,
            "suggested_yf": "INDHOTEL.NS"    ← what yf.Search found (may be null)
          }, ...
        ],
        "count": 1
      }
    """
    rows = (
        _db()
        .table("portfolio_holdings")
        .select("id, symbol, yf_symbol, name, avg_buy, current_price")
        .eq("status", "OPEN")
        .execute()
        .data or []
    )

    broken = [
        r for r in rows
        if not r.get("current_price") or float(r.get("current_price") or 0) <= 0
    ]
    if not broken:
        return {"broken": [], "count": 0}

    # Try to suggest a fix for each broken symbol via yf.Search
    loop = asyncio.get_event_loop()

    def _suggest_fixes(broken_rows: list[dict]) -> list[dict]:
        suggestions: list[dict] = []
        for row in broken_rows:
            sym  = row.get("symbol", "")
            name = row.get("name")  or sym

            # Use company name for search (more reliable than the NSE symbol)
            query = name if name != sym else sym
            suggested = _search_yf_symbol(query)

            # If name search found nothing, try the raw symbol string
            if not suggested and query != sym:
                suggested = _search_yf_symbol(sym)

            suggestions.append({
                "id":           row["id"],
                "symbol":       sym,
                "yf_symbol":    row.get("yf_symbol"),
                "name":         name,
                "avg_buy":      row.get("avg_buy"),
                "suggested_yf": suggested,
            })
        return suggestions

    result = await loop.run_in_executor(None, _suggest_fixes, broken)
    return {"broken": result, "count": len(result)}


# ── 5. Portfolio alerts ────────────────────────────────────────────────────────

@app.get("/api/portfolio/alerts", tags=["portfolio"])
async def get_portfolio_alerts(
    _: None = Depends(require_api_key),
):
    """Returns all unresolved portfolio alerts, most severe first."""
    _sev = {"CRITICAL": 0, "DANGER": 1, "WARNING": 2, "INFO": 3}

    rows = (_db()
            .table("portfolio_alerts")
            .select("*")
            .eq("resolved", False)
            .execute()
            .data or [])

    rows.sort(key=lambda r: _sev.get((r.get("severity") or "INFO").upper(), 99))
    return rows


# ── 5b. Portfolio Risk ────────────────────────────────────────────────────────

@app.get("/api/portfolio/risk", tags=["portfolio"])
async def get_portfolio_risk(
    refresh: bool = Query(False, description="Recompute metrics live (slow)"),
    _:       None = Depends(require_api_key),
):
    """
    Portfolio-level risk metrics: VaR, CVaR, volatility, Sharpe, sector
    concentration, HHI, correlation matrix, max drawdown per holding.

    By default returns the last saved snapshot from `portfolio_risk_snapshots`.
    Pass `?refresh=true` to recompute live (slow — fetches 1yr daily data).
    """
    loop = asyncio.get_event_loop()

    def _run():
        from agents.portfolio_risk import run_portfolio_risk, load_latest_snapshot
        if refresh:
            return run_portfolio_risk(dry_run=False)
        snap = load_latest_snapshot()
        if snap:
            return snap
        # No snapshot yet — compute live on first call
        return run_portfolio_risk(dry_run=False)

    try:
        result = await loop.run_in_executor(None, _run)
        # portfolio_risk may produce NaN/Inf for delisted symbols — sanitise before
        # serialising, otherwise FastAPI raises ValueError: Out of range float values
        return _sanitise_floats(result)
    except Exception as exc:
        log.error("portfolio/risk error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── 6. Governance alerts ───────────────────────────────────────────────────────

@app.get("/api/governance/alerts", tags=["governance"])
async def get_governance_alerts(
    _: None = Depends(require_api_key),
):
    """
    Aggregated governance / system-health alerts from two sources:
      a) portfolio_alerts where severity IN ('CRITICAL','DANGER') — system-level events
      b) agent_performance rows where trend = 'DEGRADING' — synthesised as WARNINGs
    Returns a unified list sorted critical → warning.
    """
    db     = _db()
    alerts: list[dict] = []

    # Source a: critical/danger portfolio_alerts (unresolved)
    # Deduplicated: keep only the most recent alert per (alert_type, portfolio_id)
    # so the same stock triggering STOPLOSS_HIT multiple times doesn't spam the UI.
    try:
        raw_alerts = (db.table("portfolio_alerts")
                        .select("*")
                        .eq("resolved", False)
                        .in_("severity", ["CRITICAL", "DANGER"])
                        .order("created_at", desc=True)   # newest first for dedup
                        .execute()
                        .data or [])

        _seen_alert_keys: set[str] = set()
        for row in raw_alerts:
            # Dedup key: alert_type + holding_id (or symbol as fallback).
            # NOTE: the column is 'holding_id', NOT 'portfolio_id'.
            # Using symbol (not title) so that price-change between monitor runs
            # doesn't cause the same stock to appear multiple times.
            dedup_key = f"{row.get('alert_type','?')}::{row.get('holding_id') or row.get('symbol') or row.get('title','?')}"
            if dedup_key in _seen_alert_keys:
                continue
            _seen_alert_keys.add(dedup_key)
            alerts.append({
                "id":       row["id"],
                "severity": (row.get("severity") or "info").lower(),
                "module":   row.get("alert_type") or "System",
                "title":    row.get("title")      or "",
                "detail":   row.get("detail")     or "",
                "action":   "Review and resolve this alert",
                "time":     str(row.get("created_at") or ""),
                "resolved": False,
            })
    except Exception as exc:
        log.warning("governance_alerts/portfolio_alerts: %s", exc)

    # Source b: degrading agents → WARNING
    try:
        for row in (db.table("agent_performance")
                      .select("agent_name, accuracy_90d, hallucination_rate, trend, audit_date")
                      .eq("trend", "DEGRADING")
                      .execute()
                      .data or []):
            alerts.append({
                "id":       f"agent-{row['agent_name']}-degrading",
                "severity": "warning",
                "module":   row["agent_name"],
                "title":    f"Agent degrading — {row['agent_name']}",
                "detail":   (
                    f"90-day accuracy: {row.get('accuracy_90d','?')}% · "
                    f"Hallucination rate: {row.get('hallucination_rate','?')}% · "
                    f"Trend: DEGRADING as of {row.get('audit_date','?')}"
                ),
                "action":   "Review agent configuration and recent outputs",
                "time":     str(row.get("audit_date") or ""),
                "resolved": False,
            })
    except Exception as exc:
        log.warning("governance_alerts/agent_performance: %s", exc)

    _sev_order = {"critical": 0, "danger": 1, "warning": 2, "info": 3}
    alerts.sort(key=lambda a: _sev_order.get(a["severity"], 99))
    return alerts


# ── 6b. System / data-source health ───────────────────────────────────────────

@app.get("/api/system/health", tags=["governance"])
async def get_system_health(
    _: None = Depends(require_api_key),
):
    """
    Lightweight health check for every data source and API integration.
    Designed to run in < 2s without making live external requests (reads DB + env only).
    Returns a list of checks: {name, status, detail, severity, action}

    Checks:
      - Supabase: connectivity (implicit — if this endpoint returns, DB is reachable)
      - Last daily run: date and status from daily_runs table
      - FII data freshness: most recent row in institutional_flows
      - Screener.in: whether Railway IP was recently blocked (inferred from daily_run errors)
      - Breeze Connect: whether session token is configured + not expired
      - Anthropic API: whether ANTHROPIC_API_KEY is set
      - OpenAI (GPT-4o-mini judge): whether OPENAI_API_KEY is set
      - Trendlyne F&O: whether session cookies are configured
    """
    checks: list[dict] = []
    db = _db()

    def _ok(name: str, detail: str) -> dict:
        return {"name": name, "status": "ok", "detail": detail, "severity": "ok", "action": None}

    def _info(name: str, detail: str, action: str) -> dict:
        # INFO = not an error, not a warning — informational note (e.g. deprecated integrations)
        return {"name": name, "status": "info", "detail": detail, "severity": "info", "action": action}

    def _warn(name: str, detail: str, action: str) -> dict:
        return {"name": name, "status": "warning", "detail": detail, "severity": "warning", "action": action}

    def _err(name: str, detail: str, action: str) -> dict:
        return {"name": name, "status": "error", "detail": detail, "severity": "error", "action": action}

    # ── Supabase ──────────────────────────────────────────────────────────────
    checks.append(_ok("Supabase", "Connection active — this response proves it"))

    # ── Last daily pipeline run ───────────────────────────────────────────────
    # daily_runs schema: run_date, symbols_processed, errors (INTEGER count),
    # duration_seconds, status (OK | WARNING | DATA_DEGRADATION), created_at.
    try:
        run_rows = (db.table("daily_runs")
                      .select("run_date, symbols_processed, errors, duration_seconds, status")
                      .order("run_date", desc=True)
                      .limit(1)
                      .execute()
                      .data or [])
        if run_rows:
            run = run_rows[0]
            run_date_str   = str(run.get("run_date", "?"))
            n_syms         = run.get("symbols_processed") or 0
            n_errors       = run.get("errors") or 0
            run_status_col = run.get("status") or ("OK" if n_errors == 0 else "WARNING")
            duration       = run.get("duration_seconds")
            dur_str        = f" ({duration:.0f}s)" if duration else ""
            days_ago       = (date.today() - date.fromisoformat(run_date_str)).days \
                             if run_date_str != "?" else 99

            # Determine detail message based on status column
            if run_status_col == "DATA_DEGRADATION":
                run_status_str = f"{n_errors} symbols suppressed (data degradation)"
                hint = ("All analyses suppressed — screener.in + Trendlyne were unreachable. "
                        "Refresh TRENDLYNE_SESSION/TRENDLYNE_CSRF in Railway env vars. "
                        "Tomorrow's run will retry automatically.")
                check_fn = _warn
            elif n_errors == 0:
                run_status_str = "success"
                hint = ""
                check_fn = _ok
            else:
                run_status_str = f"{n_errors} errors"
                hint = "Check Railway worker logs for synthesis errors"
                check_fn = _warn

            if days_ago == 0:
                checks.append(check_fn(
                    "Daily Pipeline",
                    f"Ran today ({run_date_str}){dur_str} — {n_syms} symbols, {run_status_str}",
                    hint,
                ))
            elif days_ago <= 1:
                checks.append(_warn(
                    "Daily Pipeline",
                    f"Last run: {run_date_str} ({days_ago}d ago){dur_str} — {n_syms} symbols, {run_status_str}",
                    "Check Railway worker logs"
                ))
            else:
                checks.append(_err(
                    "Daily Pipeline",
                    f"Last run: {run_date_str} ({days_ago} days ago) — pipeline may not be running",
                    "Check Railway worker dyno is running. Run: python worker.py --now"
                ))
            # Screener.in sub-check
            checks.append(_ok("Screener.in", f"Last run {run_date_str}: screener ran (check Railway logs for 403/fallback detail)"))
        else:
            checks.append(_warn("Daily Pipeline", "No daily_runs rows found — scheduler may not have run yet", "Deploy worker.py to Railway and verify it starts"))
    except Exception as exc:
        checks.append(_warn("Daily Pipeline", f"Could not read daily_runs: {exc}", "Verify Supabase schema has daily_runs table"))

    # ── FII data freshness ────────────────────────────────────────────────────
    try:
        fii_rows = (db.table("institutional_flows")
                      .select("session_date, fii_net")
                      .order("session_date", desc=True)
                      .limit(1)
                      .execute()
                      .data or [])
        if fii_rows:
            fii_date = str(fii_rows[0].get("session_date", "?"))
            fii_net  = fii_rows[0].get("fii_net")
            try:
                days_stale = (date.today() - date.fromisoformat(fii_date)).days
            except Exception:
                days_stale = 99
            if days_stale <= 1:
                checks.append(_ok("FII/DII Data", f"Fresh — {fii_date}, FII net: ₹{fii_net:,.0f} Cr" if fii_net else f"Fresh — {fii_date}"))
            elif days_stale <= 3:
                checks.append(_warn("FII/DII Data", f"Stale — last row: {fii_date} ({days_stale}d ago)", "Likely NSE/BSE API temporarily blocked — will auto-recover"))
            else:
                checks.append(_err("FII/DII Data", f"Very stale — last row: {fii_date} ({days_stale}d ago)", "NSE FII source may be down. Check _try_nse_fii_dii / _try_bse_fii_dii in fetchers.py"))
        else:
            checks.append(_warn("FII/DII Data", "No institutional_flows rows", "institutional.py needs to run at least once to populate the table"))
    except Exception as exc:
        checks.append(_warn("FII/DII Data", f"Could not read institutional_flows: {exc}", "Verify table exists in Supabase"))

    # ── Breeze Connect (marked for removal in P4 — superseded by Trendlyne F&O) ──
    # Breeze requires a manual daily session token refresh which is high-maintenance.
    # Trendlyne F&O Excel provides the same option chain data automatically.
    # Breeze will be removed entirely in P4-D.
    breeze_key     = os.getenv("BREEZE_API_KEY", "")
    breeze_secret  = os.getenv("BREEZE_API_SECRET", "")
    breeze_token   = os.getenv("BREEZE_SESSION_TOKEN", "")
    if breeze_key and breeze_secret and breeze_token:
        checks.append(_info(
            "Breeze Connect",
            "Configured but marked for removal (P4-D) — Trendlyne F&O is the primary options source",
            "No action needed. Breeze will be removed in Phase 4 (P4-D) to simplify the stack."
        ))
    else:
        # Not configured — this is fine, Trendlyne F&O is primary
        checks.append(_info(
            "Breeze Connect",
            "Not configured — this is expected. Trendlyne F&O Excel is the primary options source.",
            "No action needed. Breeze is deprecated (P4-D) and will be removed in Phase 4."
        ))

    # ── Anthropic API (ARIA + Claude judge) ──────────────────────────────────
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    if anthropic_key:
        checks.append(_ok("Anthropic API", "ANTHROPIC_API_KEY configured ✓"))
    else:
        checks.append(_err(
            "Anthropic API",
            "ANTHROPIC_API_KEY not set — ARIA chat and Claude validation judge will fail",
            "Set ANTHROPIC_API_KEY in Railway env vars (backend) AND Vercel env vars (ARIA serverless)"
        ))

    # ── OpenAI API (GPT-4o-mini judge) ────────────────────────────────────────
    openai_key = os.getenv("OPENAI_API_KEY", "")
    if openai_key:
        checks.append(_ok("OpenAI API", "OPENAI_API_KEY configured ✓"))
    else:
        checks.append(_warn(
            "OpenAI API",
            "OPENAI_API_KEY not set — GPT-4o-mini validation judge disabled (Claude-only validation)",
            "Set OPENAI_API_KEY in Railway env vars for model-diversity in synthesis validation"
        ))

    # ── Trendlyne (F&O + Analyst targets) ────────────────────────────────────
    tl_sess = os.getenv("TRENDLYNE_SESSION", "")
    tl_csrf = os.getenv("TRENDLYNE_CSRF", "")
    tl_user = os.getenv("TRENDLYNE_USER", "")
    tl_pass = os.getenv("TRENDLYNE_PASS", "")
    _tl_placeholder = "bce34ncrlhwelezjn884vaxvyh0g543w"

    if tl_sess and tl_csrf and tl_sess != _tl_placeholder:
        # Auto-refresh configured?
        if tl_user and tl_pass:
            checks.append(_ok(
                "Trendlyne",
                "Session cookies + auto-refresh credentials configured ✓ (F&O Excel + analyst targets)"
            ))
        else:
            checks.append(_ok(
                "Trendlyne",
                "Session cookies configured ✓ — set TRENDLYNE_USER + TRENDLYNE_PASS for auto-refresh on expiry"
            ))
    elif tl_sess == _tl_placeholder:
        checks.append(_warn(
            "Trendlyne",
            "Using placeholder/default session cookies — F&O Excel and analyst targets may fail",
            "Login to trendlyne.com, copy .trendlyne + csrftoken cookies, set TRENDLYNE_SESSION + TRENDLYNE_CSRF"
        ))
    else:
        checks.append(_warn(
            "Trendlyne",
            "TRENDLYNE_SESSION / TRENDLYNE_CSRF not configured — F&O Excel and analyst targets disabled",
            "Set TRENDLYNE_SESSION + TRENDLYNE_CSRF in Railway env vars (optionally TRENDLYNE_USER + TRENDLYNE_PASS)"
        ))

    # ── Discovery screener health ──────────────────────────────────────────────
    # Checks whether the last run promoted any recs to the recommendations table.
    # A "0 promoted" run is not an error but is flagged as a warning when it
    # persists for more than 3 consecutive days — it usually means the save is
    # silently failing (DB issue) or thresholds need calibration.
    try:
        disc_rows = (db.table("discovery_runs")
                       .select("run_date,total_discoveries,total_passed,total_screened")
                       .order("run_date", desc=True)
                       .limit(7)
                       .execute()
                       .data or [])
        if disc_rows:
            latest = disc_rows[0]
            run_date_str   = latest.get("run_date", "?")
            total_disc     = latest.get("total_discoveries", 0) or 0
            total_passed   = latest.get("total_passed", 0) or 0
            total_screened = latest.get("total_screened", 0) or 0
            # Count consecutive days with 0 promotions
            consecutive_zeros = sum(1 for r in disc_rows if (r.get("total_discoveries") or 0) == 0)

            if total_disc > 0:
                checks.append(_ok(
                    "Discovery Screener",
                    f"Last run {run_date_str}: {total_disc} promoted, {total_passed} passed/{total_screened} screened ✓"
                ))
            elif consecutive_zeros >= 3:
                checks.append(_warn(
                    "Discovery Screener",
                    f"0 promotions for {consecutive_zeros} consecutive days (last run: {run_date_str}, "
                    f"{total_passed} passed pre-screen/{total_screened} screened) — "
                    f"check if _save_discovery is failing or thresholds need calibration",
                    "Check Railway logs for '_save_discovery FAILED' errors. "
                    "Run: GET /api/debug/screener to validate screener.in access."
                ))
            else:
                checks.append(_ok(
                    "Discovery Screener",
                    f"Last run {run_date_str}: 0 promoted ({total_passed} passed pre-screen/{total_screened} screened) — "
                    f"normal if market conditions don't meet 20%+ upside threshold"
                ))
        else:
            checks.append(_warn(
                "Discovery Screener",
                "No discovery_runs rows found — screener has not run yet",
                "Deploy worker.py and verify 06:00 IST job fires, or run: python -m agents.discovery_screener"
            ))
    except Exception as exc:
        checks.append(_warn("Discovery Screener", f"Could not read discovery_runs: {exc}",
                            "Verify discovery_runs table exists (run db/migrations/create_discovery_runs.sql)"))

    # Sort: errors first, then warnings, then info, then ok
    _sev_order = {"error": 0, "warning": 1, "info": 2, "ok": 3}
    checks.sort(key=lambda c: _sev_order.get(c["severity"], 99))
    return {
        "checks":     checks,
        "errors":     sum(1 for c in checks if c["severity"] == "error"),
        "warnings":   sum(1 for c in checks if c["severity"] == "warning"),
        "ok":         sum(1 for c in checks if c["severity"] == "ok"),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


# ── 6b. Screener.in live connectivity diagnostic ──────────────────────────────

@app.get("/api/debug/screener", tags=["governance"])
async def debug_screener(
    symbol: str = Query("RELIANCE", description="NSE symbol to test"),
    _: None = Depends(require_api_key),
):
    """
    Live connectivity probe for screener.in — makes real HTTP requests and
    reports the exact status code, response time, and whether real data was
    parsed.  Use this to quickly diagnose Railway IP blocks from the dashboard
    or via curl without digging through logs.

    Returns:
      {
        symbol, tested_urls: [
          {url, status_code, elapsed_ms, got_data, error}
        ],
        session_warmed, cookies_present,
        screener_working, fallback_used,
        data_sample: {pe, revenue_growth, ebitda_margin, ...} | null
      }
    """
    import time as _time
    import requests as _req

    from data.fetchers import (
        _get_screener_session,
        _screener_headers,
        _SCREENER_USER_AGENTS,
        reset_screener_session,
    )
    from data.symbol_map import resolve_screener
    import random

    sym = symbol.upper().replace(".NS", "").replace(".BO", "")
    slug = resolve_screener(sym)

    tested_urls: list[dict] = []

    # ── Step 1: warmup probe ──────────────────────────────────────────────────
    warmup_status = None
    warmup_elapsed = None
    try:
        t0 = _time.time()
        wr = _req.get(
            "https://screener.in/",
            headers=_screener_headers(),
            timeout=10,
            allow_redirects=True,
        )
        warmup_elapsed = round((_time.time() - t0) * 1000)
        warmup_status  = wr.status_code
    except Exception as exc:
        warmup_status = f"error: {exc}"

    # ── Step 2: test each candidate URL ──────────────────────────────────────
    session = _get_screener_session()
    candidates = list({slug, sym})  # dedup
    for candidate in candidates:
        for variant in ("consolidated/", ""):
            url = f"https://www.screener.in/company/{candidate}/{variant}"
            try:
                t0 = _time.time()
                r  = session.get(
                    url,
                    headers=_screener_headers({
                        "Referer": "https://screener.in/",
                        "User-Agent": random.choice(_SCREENER_USER_AGENTS),
                    }),
                    timeout=12,
                )
                elapsed = round((_time.time() - t0) * 1000)
                # Check if we actually got company data (not a login page or 404)
                got_data = (
                    r.status_code == 200
                    and "company-ratios" in r.text
                    and len(r.text) > 5000
                )
                tested_urls.append({
                    "url":          url,
                    "status_code":  r.status_code,
                    "elapsed_ms":   elapsed,
                    "got_data":     got_data,
                    "response_len": len(r.text) if r.status_code == 200 else None,
                    "error":        None,
                })
            except Exception as exc:
                tested_urls.append({
                    "url":         url,
                    "status_code": None,
                    "elapsed_ms":  None,
                    "got_data":    False,
                    "error":       str(exc),
                })

    # ── Step 3: try to parse actual data ─────────────────────────────────────
    screener_working = any(u["got_data"] for u in tested_urls)
    data_sample = None
    fallback_used = False

    if screener_working:
        try:
            from data.fetchers import get_screener_data
            data_sample = get_screener_data(sym)
            if data_sample and data_sample.get("data_source") == "yfinance_fallback":
                screener_working = False
                fallback_used = True
        except Exception:
            pass
    else:
        fallback_used = True

    cookies_present = bool(session.cookies.get("csrftoken") or session.cookies.get("sessionid"))

    return {
        "symbol":          sym,
        "slug":            slug,
        "warmup": {
            "status_code": warmup_status,
            "elapsed_ms":  warmup_elapsed,
        },
        "tested_urls":      tested_urls,
        "session_warmed":   cookies_present,
        "cookies_present":  cookies_present,
        "cookie_names":     list(session.cookies.keys()),
        "screener_working": screener_working,
        "fallback_used":    fallback_used,
        "diagnosis": (
            "WORKING — screener.in accessible from this Railway pod"
            if screener_working else
            "BLOCKED — Railway IP is being blocked by screener.in (HTTP 403/429 or no data in response). "
            "yfinance fallback is active. Options: (1) whitelist Railway IP on screener.in Pro, "
            "(2) use a proxy, or (3) rely on yfinance fallback."
        ),
        "data_sample":  {
            k: v for k, v in (data_sample or {}).items()
            if k in ("pe", "revenue_growth", "ebitda_margin", "roce",
                     "debt_equity", "promoter_holding", "data_source")
        } if data_sample else None,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Trendlyne + proxy health debug endpoint ───────────────────────────────────

@app.get("/api/debug/scraper-health", tags=["governance"])
async def debug_scraper_health(
    _: None = Depends(require_api_key),
):
    """
    Combined scraper health check — tests screener.in, Trendlyne, and proxy config.

    Call this from a browser or curl to confirm whether Railway can reach these
    data sources. Use after setting SCRAPERAPI_KEY or FIXIE_URL to verify the
    proxy is working.

    Returns a clear BLOCKED / WORKING status per source, plus proxy details.
    """
    import time as _time
    import requests as _req

    results: dict = {
        "proxy": {},
        "screener_in": {},
        "trendlyne": {},
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

    # ── Proxy status ──────────────────────────────────────────────────────────
    try:
        from data.proxy_session import proxy_configured, get_proxy_dict, _active_proxy
        results["proxy"] = {
            "configured": proxy_configured(),
            "mode": _active_proxy or "none (direct connection)",
            "recommendation": (
                "Proxy active — should bypass Railway IP blocks"
                if proxy_configured() else
                "No proxy configured. Set SCRAPERAPI_KEY (recommended, $29/month) "
                "or FIXIE_URL (Railway add-on) to bypass Railway IP blocks on "
                "screener.in and Trendlyne."
            ),
        }
    except Exception as exc:
        results["proxy"] = {"error": str(exc)}

    # ── Screener.in probe ─────────────────────────────────────────────────────
    try:
        from data.proxy_session import get_proxy_dict
        proxies = get_proxy_dict() or {}
        t0 = _time.time()
        r = _req.get(
            "https://screener.in/",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36"},
            proxies=proxies,
            timeout=10,
            allow_redirects=True,
        )
        elapsed = round((_time.time() - t0) * 1000)
        results["screener_in"] = {
            "status": r.status_code,
            "elapsed_ms": elapsed,
            "working": r.status_code == 200,
            "diagnosis": "WORKING" if r.status_code == 200 else f"BLOCKED (HTTP {r.status_code})",
        }
    except Exception as exc:
        results["screener_in"] = {
            "status": None,
            "working": False,
            "error": str(exc)[:200],
            "diagnosis": "BLOCKED (network unreachable — IP-level block or no proxy configured)",
        }

    # ── Trendlyne probe ───────────────────────────────────────────────────────
    try:
        from data.proxy_session import get_proxy_dict
        import os
        proxies = get_proxy_dict() or {}
        tl_sess = os.getenv("TRENDLYNE_SESSION", "")
        tl_csrf = os.getenv("TRENDLYNE_CSRF", "")
        s = _req.Session()
        s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36",
            "Referer": "https://trendlyne.com/",
        })
        if tl_sess:
            s.cookies.set(".trendlyne", tl_sess, domain="trendlyne.com")
        if tl_csrf:
            s.cookies.set("csrftoken", tl_csrf, domain="trendlyne.com")
        if proxies:
            s.proxies.update(proxies)

        t0 = _time.time()
        r = s.get("https://trendlyne.com/equity/RELIANCE/NSE/", timeout=15, allow_redirects=True)
        elapsed = round((_time.time() - t0) * 1000)
        is_data_page = r.status_code == 200 and ("data-metrics" in r.text or "Reliance" in r.text)
        results["trendlyne"] = {
            "status": r.status_code,
            "elapsed_ms": elapsed,
            "working": is_data_page,
            "has_session_cookie": bool(tl_sess),
            "diagnosis": (
                "WORKING — data page returned"
                if is_data_page else
                f"BLOCKED (HTTP {r.status_code}) — " + (
                    "IP blocked by Trendlyne WAF. Set SCRAPERAPI_KEY or FIXIE_URL."
                    if r.status_code == 405 else
                    "Session expired — update TRENDLYNE_SESSION cookie."
                    if r.status_code in (302, 401, 403) else
                    f"Unexpected response"
                )
            ),
        }
    except Exception as exc:
        results["trendlyne"] = {
            "status": None,
            "working": False,
            "error": str(exc)[:200],
            "diagnosis": "BLOCKED (network error — IP-level block or no proxy)",
        }

    # ── Overall recommendation ────────────────────────────────────────────────
    screener_ok = results["screener_in"].get("working", False)
    trendlyne_ok = results["trendlyne"].get("working", False)
    proxy_active = results["proxy"].get("configured", False)

    if screener_ok and trendlyne_ok:
        overall = "ALL SOURCES WORKING"
    elif proxy_active and (not screener_ok or not trendlyne_ok):
        overall = "PROXY ACTIVE BUT STILL BLOCKED — proxy IP may also be in blocklist, try ScraperAPI"
    elif not proxy_active and (not screener_ok or not trendlyne_ok):
        overall = "SOURCES BLOCKED — set SCRAPERAPI_KEY in Railway env vars to fix permanently"
    else:
        overall = "PARTIAL — check individual source results"

    results["overall"] = overall
    return results


# ── 7. Research proposals ──────────────────────────────────────────────────────

@app.get("/api/governance/research", tags=["governance"])
async def get_research_proposals(
    status:        str | None = Query(None, description="Filter by status"),
    min_relevance: int        = Query(0, ge=0, le=100),
    limit:         int        = Query(20, ge=1, le=100),
    _:             None       = Depends(require_api_key),
):
    """Returns research proposals ordered by relevance desc, then created_at desc."""
    db = _db()
    q  = (db.table("research_proposals")
            .select("*")
            .order("relevance",   desc=True)
            .order("created_at",  desc=True)
            .limit(limit))

    if status:
        q = q.eq("status", status)
    if min_relevance > 0:
        q = q.gte("relevance", min_relevance)

    rows      = q.execute().data or []
    proposals = [_transform_research(r) for r in rows]
    return {"proposals": proposals, "count": len(proposals)}


# ── 8. Market pulse ────────────────────────────────────────────────────────────

@app.get("/api/market/pulse", tags=["market"])
async def get_market_pulse(
    _: None = Depends(require_api_key),
):
    """
    Live market prices from yfinance + FII net from Supabase.
    Results are cached for 60 seconds.
    """
    return await _get_market_pulse()


@app.get("/api/market/digest", tags=["market"])
async def get_market_digest(
    digest_type: Optional[str] = Query(None, description="MORNING or CLOSING; omit for both"),
    digest_date: Optional[str] = Query(None, description="ISO date YYYY-MM-DD; defaults to today"),
    _: None = Depends(require_api_key),
):
    """
    P6-C: Latest Morning Brief and/or Closing Digest for the Indian equity market.
    Returns both types for today by default; filter via digest_type and digest_date.
    """
    if not _supabase:
        raise HTTPException(status_code=503, detail="Database unavailable")

    today = date.today().isoformat()
    target_date = digest_date or today

    try:
        q = (
            _supabase.table("market_digests")
            .select(
                "id, digest_type, digest_date, market_mood, summary, "
                "key_events, top_themes, sectors_in_focus, nifty_signal, "
                "headline_count, created_at"
            )
            .eq("digest_date", target_date)
            .order("created_at", desc=True)
        )
        if digest_type:
            q = q.eq("digest_type", digest_type.upper())

        rows = q.execute().data or []
    except Exception as exc:
        log.error("/api/market/digest query failed: %s", exc)
        return {"digests": [], "date": target_date, "note": "query error"}

    # Return latest of each type (upsert may create duplicates in edge cases)
    seen_types: set = set()
    digests = []
    for row in rows:
        dt = row.get("digest_type")
        if dt in seen_types:
            continue
        seen_types.add(dt)
        digests.append({
            "id":             row.get("id"),
            "digestType":     dt,
            "digestDate":     row.get("digest_date"),
            "marketMood":     row.get("market_mood"),
            "summary":        row.get("summary"),
            "keyEvents":      row.get("key_events") or [],
            "topThemes":      row.get("top_themes") or [],
            "sectorsInFocus": row.get("sectors_in_focus") or [],
            "niftySignal":    row.get("nifty_signal"),
            "headlineCount":  row.get("headline_count", 0),
            "createdAt":      row.get("created_at"),
        })

    return {
        "digests":  digests,
        "date":     target_date,
        "count":    len(digests),
    }


@app.get("/api/earnings/upcoming", tags=["portfolio"])
async def get_upcoming_earnings(
    days: int  = Query(14, description="Days ahead to look for earnings"),
    _:    None = Depends(require_api_key),
):
    """
    Returns upcoming earnings for all OPEN portfolio holdings within `days` days.
    Also triggers a fresh fetch + upsert to keep the calendar current.
    """
    if not _supabase:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        # Load portfolio symbols
        rows = (
            _supabase
            .table("portfolio_holdings")
            .select("symbol")
            .eq("status", "OPEN")
            .execute()
            .data or []
        )
        symbols = [r["symbol"] for r in rows]
        if not symbols:
            return {"earnings": [], "symbols_checked": 0}

        loop = asyncio.get_event_loop()

        # Refresh calendar for portfolio symbols (non-blocking)
        def _refresh():
            from data.earnings_fetcher import fetch_upcoming_earnings, upsert_earnings_calendar
            records = fetch_upcoming_earnings(symbols, days_ahead=days)
            upsert_earnings_calendar(records)
            return records

        records = await loop.run_in_executor(None, _refresh)
        return {
            "earnings":        records,
            "symbols_checked": len(symbols),
            "days_ahead":      days,
        }
    except Exception as exc:
        log.error("earnings/upcoming error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/news/{symbol}", tags=["market"])
async def get_symbol_news(
    symbol: str,
    _: None = Depends(require_api_key),
):
    """
    Fetch recent news headlines for a stock/index symbol via Google News RSS.
    Returns up to 15 headlines (title, source, published, url).
    No API key required — uses free Google News RSS feed.
    Results are NOT cached (lightweight enough to call on demand).
    """
    try:
        import feedparser
        from urllib.parse import quote_plus

        clean = symbol.upper().replace(".NS", "").replace(".BO", "").strip()

        # Use two targeted queries for maximum coverage
        queries = [
            f"{clean} stock NSE India",
            f"{clean} earnings revenue profit India",
        ]

        google_tmpl = (
            "https://news.google.com/rss/search"
            "?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
        )

        seen: set[str] = set()
        articles: list[dict] = []

        for query in queries:
            url = google_tmpl.format(query=quote_plus(query))
            try:
                feed = feedparser.parse(url)
                if feed.bozo and not feed.entries:
                    continue
                for entry in feed.entries[:8]:
                    title = (entry.get("title") or "").strip()
                    if not title or title in seen:
                        continue
                    seen.add(title)
                    published = entry.get("published", "")
                    try:
                        if entry.get("published_parsed"):
                            published = str(
                                datetime(*entry.published_parsed[:6]).date()
                            )
                    except Exception:
                        pass
                    articles.append({
                        "title":     title,
                        "source":    "Google News",
                        "published": published,
                        "url":       entry.get("link", ""),
                    })
            except Exception as exc:
                log.debug("news RSS fetch error (symbol=%s, query=%r): %s", symbol, query, exc)

        return {
            "symbol":  clean,
            "news":    articles[:15],
            "count":   len(articles[:15]),
        }
    except Exception as exc:
        log.warning("get_symbol_news(%s) failed: %s", symbol, exc)
        return {"symbol": symbol.upper(), "news": [], "count": 0}


@app.get("/api/market/regime", tags=["market"])
async def get_market_regime(
    days: int = Query(30, description="Days of history to return"),
    _:    None = Depends(require_api_key),
):
    """
    Current market regime (BULL/BEAR/SIDEWAYS/HIGH_VOLATILITY) + last N days history.
    Regime is detected daily at 06:30 IST before the main orchestrator run.

    Returns:
      { current: {...regime row...}, history: [...], last_updated: str }
    """
    if not _supabase:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        cutoff = str(date.today() - timedelta(days=days))
        rows   = (
            _supabase
            .table("market_regime")
            .select("regime_date,regime,confidence,nifty_trend,vix_state,fii_trend,breadth_state,momentum_state,raw_signals,created_at")
            .gte("regime_date", cutoff)
            .order("regime_date", desc=True)
            .limit(days + 5)
            .execute()
            .data or []
        )
        current = rows[0] if rows else None
        return {
            "current":      current,
            "history":      rows,
            "last_updated": current.get("created_at") if current else None,
        }
    except Exception as exc:
        log.error("market/regime error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── 9. Warren Bot — on-demand Buffett quality analysis ────────────────────────

async def _get_warren_bot_cached(symbol: str, force_refresh: bool) -> dict:
    """
    Return warren_bot analysis for *symbol*, using a 24-hour cache.

    Cache hierarchy:
      1. Supabase warren_bot_cache table (survives process restarts)
      2. In-process _warren_bot_mem_cache dict (fallback when table doesn't exist)
      3. Live warren_bot.analyse() run
    """
    import json as _json
    from agents.warren_bot import analyse as _warren_analyse

    key     = symbol.upper()
    loop    = asyncio.get_event_loop()
    cutoff  = datetime.now(timezone.utc) - timedelta(seconds=WARREN_BOT_CACHE_TTL)

    # ── 1. Supabase cache ─────────────────────────────────────────────────────
    if not force_refresh and _supabase:
        try:
            rows = (
                _supabase.table("warren_bot_cache")
                .select("result, cached_at")
                .eq("symbol", key)
                .gte("cached_at", cutoff.isoformat())
                .order("cached_at", desc=True)
                .limit(1)
                .execute()
                .data or []
            )
            if rows:
                log.info("warren_bot cache hit (Supabase): %s", key)
                return rows[0]["result"]
        except Exception as exc:
            # Table may not exist yet — silently fall through
            log.debug("warren_bot Supabase cache lookup skipped: %s", exc)

    # ── 2. In-memory cache ────────────────────────────────────────────────────
    if not force_refresh and key in _warren_bot_mem_cache:
        cached_result, cached_ts = _warren_bot_mem_cache[key]
        if time.time() - cached_ts < WARREN_BOT_CACHE_TTL:
            log.info("warren_bot cache hit (memory): %s", key)
            return cached_result

    # ── 3. Live run ───────────────────────────────────────────────────────────
    log.info("Running warren_bot on-demand for %s...", key)
    result: dict = await loop.run_in_executor(None, _warren_analyse, symbol)

    # Normalise to JSON-serialisable form before caching
    result_clean: dict = _json.loads(_json.dumps(result, default=str))

    # Write to in-memory cache
    _warren_bot_mem_cache[key] = (result_clean, time.time())

    # Write to Supabase cache (best-effort; silently skipped if table missing)
    if _supabase:
        try:
            _supabase.table("warren_bot_cache").upsert(
                {"symbol": key, "result": result_clean,
                 "cached_at": datetime.now(timezone.utc).isoformat()},
                on_conflict="symbol",
            ).execute()
            log.info("warren_bot result cached (Supabase): %s", key)
        except Exception as exc:
            log.debug("warren_bot Supabase cache write skipped: %s", exc)

    return result_clean


# =============================================================================
# Performance / Outcome Tracking endpoints
# =============================================================================

@app.get("/api/performance/outcomes", tags=["performance"])
async def get_performance_outcomes(
    days: int = Query(90, description="Look-back window in days"),
    _:    None = Depends(require_api_key),
):
    """
    Last `days` days of resolved outcome records, grouped by action.
    Returns:
      { outcomes: [...], grouped: { BUY: [...], SELL: [...], ... } }
    """
    if not _supabase:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        cutoff = str(date.today() - timedelta(days=days))
        rows = (
            _supabase
            .table("recommendation_outcomes")
            .select("id,rec_id,symbol,action,entry_price,rec_date,outcome_t90,alpha_t90,outcome_t180,alpha_t180,outcome_t365,alpha_t365,last_updated")
            .gte("rec_date", cutoff)
            .order("rec_date", desc=True)
            .limit(200)
            .execute()
            .data or []
        )

        # Group by action
        grouped: dict[str, list] = {}
        for r in rows:
            action = r.get("action", "UNKNOWN")
            grouped.setdefault(action, []).append(r)

        return {"outcomes": rows, "grouped": grouped, "total": len(rows)}
    except Exception as exc:
        log.error("performance/outcomes error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/performance/accuracy", tags=["performance"])
async def get_performance_accuracy(
    _: None = Depends(require_api_key),
):
    """
    Accuracy scorecard: hit rate by action, average alpha by horizon.
    Returns:
      { by_action: { BUY: { hit_rate_90d, avg_alpha_90d, ... }, ... }, total_tracked: int }
    """
    if not _supabase:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        rows = (
            _supabase
            .table("recommendation_outcomes")
            .select("action,outcome_t90,outcome_t180,outcome_t365,alpha_t90,alpha_t180,alpha_t365")
            .execute()
            .data or []
        )

        from collections import defaultdict
        groups: dict[str, list] = defaultdict(list)
        for r in rows:
            groups[r.get("action", "UNKNOWN")].append(r)

        scorecard: dict[str, dict] = {}
        for action, grp in groups.items():
            def _hr(horizon: str):
                resolved = [r for r in grp if r.get(f"outcome_t{horizon}") not in (None, "PENDING")]
                hits     = sum(1 for r in resolved if r.get(f"outcome_t{horizon}") == "HIT")
                return round(hits / len(resolved) * 100, 1) if resolved else None, len(resolved)

            def _avg_alpha(horizon: str):
                vals = [r[f"alpha_t{horizon}"] for r in grp if r.get(f"alpha_t{horizon}") is not None]
                return round(sum(vals) / len(vals) * 100, 2) if vals else None

            hr90,  n90  = _hr("90")
            hr180, n180 = _hr("180")
            hr365, n365 = _hr("365")

            scorecard[action] = {
                "total_recs":       len(grp),
                "resolved_90d":     n90,
                "hit_rate_90d":     hr90,
                "avg_alpha_90d":    _avg_alpha("90"),
                "resolved_180d":    n180,
                "hit_rate_180d":    hr180,
                "avg_alpha_180d":   _avg_alpha("180"),
                "resolved_365d":    n365,
                "hit_rate_365d":    hr365,
                "avg_alpha_365d":   _avg_alpha("365"),
            }

        return {"by_action": scorecard, "total_tracked": len(rows)}
    except Exception as exc:
        log.error("performance/accuracy error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/performance/calibration", tags=["performance"])
async def get_performance_calibration(
    _: None = Depends(require_api_key),
):
    """
    Confidence calibration: how well does our stated confidence predict actual hit rate?

    Buckets recommendation_outcomes by composite_score (0-100) into 5 tiers.
    For each tier: expected hit rate (bucket midpoint) vs actual hit rate (% HIT).

    A well-calibrated system → actual ≈ expected across all buckets.
    Over-confident → actual < expected. Under-confident → actual > expected.

    Returns:
      {
        buckets: [
          { label: "50–60", min: 50, max: 60, total: 12, hits: 6,
            hit_rate_pct: 50.0, expected_pct: 55.0 },
          ...
        ],
        total_resolved: int,
        note: str
      }
    """
    if not _supabase:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        rows = (
            _supabase
            .table("recommendation_outcomes")
            .select("composite_score, outcome_t90")
            .not_.is_("composite_score", "null")
            .not_.is_("outcome_t90", "null")
            .neq("outcome_t90", "PENDING")
            .execute()
            .data or []
        )

        # Five confidence tiers (composite_score is 0–100)
        TIERS = [
            ("50–60", 50, 60),
            ("60–70", 60, 70),
            ("70–80", 70, 80),
            ("80–90", 80, 90),
            ("90+",   90, 101),
        ]

        buckets = []
        for label, lo, hi in TIERS:
            tier_rows = [
                r for r in rows
                if r.get("composite_score") is not None
                and lo <= float(r["composite_score"]) < hi
            ]
            hits  = sum(1 for r in tier_rows if r.get("outcome_t90") == "HIT")
            total = len(tier_rows)
            midpt = (lo + min(hi - 1, 95)) / 2   # expected midpoint
            buckets.append({
                "label":        label,
                "min":          lo,
                "max":          hi,
                "total":        total,
                "hits":         hits,
                "hit_rate_pct": round(hits / total * 100, 1) if total > 0 else None,
                "expected_pct": round(midpt, 1),
            })

        total_resolved = len(rows)
        note = (
            "Calibration data will appear once the first 90-day outcomes are resolved."
            if total_resolved == 0
            else f"{total_resolved} resolved outcomes across all tiers."
        )
        return {"buckets": buckets, "total_resolved": total_resolved, "note": note}

    except Exception as exc:
        log.error("performance/calibration error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/performance/alpha_chart", tags=["performance"])
async def get_performance_alpha_chart(
    weeks: int = Query(26, description="Number of weeks of history"),
    _:     None = Depends(require_api_key),
):
    """
    Weekly average alpha (t90d) time series for charting.
    Returns:
      { series: [ { week: "2025-W01", avg_alpha_pct: 2.4, n: 5 }, ... ] }
    """
    if not _supabase:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        cutoff = str(date.today() - timedelta(weeks=weeks))
        rows = (
            _supabase
            .table("recommendation_outcomes")
            .select("rec_date,alpha_t90,outcome_t90")
            .gte("rec_date", cutoff)
            .not_.is_("alpha_t90", "null")
            .execute()
            .data or []
        )

        # Bucket into ISO weeks
        from collections import defaultdict
        buckets: dict[str, list[float]] = defaultdict(list)
        for r in rows:
            try:
                d      = date.fromisoformat(str(r["rec_date"])[:10])
                iso_wk = f"{d.isocalendar().year}-W{d.isocalendar().week:02d}"
                buckets[iso_wk].append(float(r["alpha_t90"]) * 100)
            except Exception:
                continue

        series = [
            {"week": wk, "avg_alpha_pct": round(sum(vals) / len(vals), 2), "n": len(vals)}
            for wk, vals in sorted(buckets.items())
        ]
        return {"series": series}
    except Exception as exc:
        log.error("performance/alpha_chart error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/estimates/{symbol}", tags=["analysis"])
async def get_forward_estimates_endpoint(
    symbol:        str,
    force_refresh: bool = Query(False, description="Bypass 24h cache and re-fetch"),
    _:             None = Depends(require_api_key),
):
    """
    Forward earnings estimates for a symbol — analyst EPS, revenue, forward PE, PEG.
    Cached in Supabase `forward_estimates_cache` for 24 hours.

    Returns
    -------
    {
      symbol, eps_current_yr, eps_next_yr, eps_growth_pct,
      forward_pe, peg_ratio, current_price, analyst_count,
      valuation_signal, forward_pe_comment, peg_comment, summary,
      cached_at, source, error
    }
    """
    loop  = asyncio.get_event_loop()
    plain = symbol.replace(".NS", "").replace(".BO", "").upper()

    def _run():
        from data.forward_estimates import get_forward_estimates, interpret_estimates
        est    = get_forward_estimates(plain, force_refresh=force_refresh)
        interp = interpret_estimates(est)
        return {**est, **interp}

    try:
        result = await loop.run_in_executor(None, _run)
        return result
    except Exception as exc:
        log.error("estimates/%s error: %s", plain, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/backtest/summary", tags=["performance"])
async def get_backtest_summary(
    split:   str  = Query("TEST", description="TRAIN | TEST | FULL — TEST is the out-of-sample period"),
    limit:   int  = Query(5, description="Number of most-recent monthly runs to return"),
    _:       None = Depends(require_api_key),
):
    """
    Walk-forward backtest summary from the backtest_results table.

    split=TEST (2023–2024 out-of-sample) is the most meaningful metric for
    evaluating whether the system's signal logic generates genuine alpha.

    Run automatically by worker.py on the 1st of each month at 07:45 IST.
    Trigger manually: python -m agents.backtester --dry-run (test without saving)
    """
    if not _supabase:
        raise HTTPException(status_code=503, detail="Database not configured")
    try:
        rows = (
            _supabase.table("backtest_results")
            .select(
                "run_date, universe, period_start, period_end, split_type, "
                "total_signals, hit_rate_90d, avg_alpha_90d, avg_alpha_180d, "
                "sharpe_ratio, max_drawdown, win_loss_ratio, created_at"
            )
            .eq("split_type", split.upper())
            .order("run_date", desc=True)
            .limit(limit)
            .execute()
        ).data or []
        return {"split": split.upper(), "count": len(rows), "results": rows}
    except Exception as exc:
        log.error("backtest/summary error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/warren_bot/{symbol}", tags=["analysis"])
async def get_warren_bot(
    symbol:        str,
    force_refresh: bool = Query(False, description="Bypass cache and re-run analysis"),
    _:             None = Depends(require_api_key),
):
    """
    On-demand Buffett + Jhunjhunwala quality analysis for any NSE symbol.

    Results are cached for 24 hours (Supabase warren_bot_cache table, with
    an in-process dict fallback). Use ?force_refresh=true to bypass the cache.

    The response is the full warren_bot output dict:
      score, conviction_rating, moat_type, roce_avg_10yr, margin_of_safety_pct,
      intrinsic_value_per_share, why_buffett_would_like, why_buffett_would_pass,
      key_risks, data_gaps, signal, commentary … (28 keys total)

    Powers the ARIA "analyse this stock like Buffett" on-demand feature.

    Cache setup (one-time SQL, run in Supabase SQL Editor):
      CREATE TABLE IF NOT EXISTS warren_bot_cache (
        symbol     TEXT PRIMARY KEY,
        result     JSONB NOT NULL,
        cached_at  TIMESTAMPTZ NOT NULL DEFAULT now()
      );
    """
    raw = symbol.strip()
    if not raw:
        raise HTTPException(status_code=400, detail="symbol path parameter is required")

    try:
        result = await _get_warren_bot_cached(raw, force_refresh=force_refresh)
    except Exception as exc:
        log.error("warren_bot on-demand failed for %s: %s", raw, exc)
        raise HTTPException(status_code=500, detail=f"warren_bot analysis failed: {exc}") from exc

    return {
        "symbol":        raw.upper(),
        "cached":        not force_refresh,
        "analysis":      result,
    }


# ── On-demand full analysis (ARIA command) ────────────────────────────────────

@app.post("/api/analyse", tags=["analysis"])
async def on_demand_analyse(
    request: Request,
    _: None = Depends(require_api_key),
):
    """
    Run a full 10-agent analysis + synthesis for any NSE/BSE symbol on demand.
    Triggered by the ARIA /analyse command on the dashboard.

    Does NOT write to recommendations table (dry_run=True) — result is returned
    directly to the caller. The full pipeline takes ~45–90 seconds for one symbol.

    Request body: {"symbol": "RELIANCE"}
    Response: {symbol, yf_symbol, analysis: {action, confidence, synthesis, ...}, agents: {...}}
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON body required: {\"symbol\": \"RELIANCE\"}")

    raw = (body.get("symbol") or "").strip().upper()
    if not raw:
        raise HTTPException(status_code=400, detail="symbol field required")

    yf_symbol = _resolve_yf_symbol(raw)
    log.info("On-demand analysis triggered: %s -> %s", raw, yf_symbol)

    try:
        from scheduler.orchestrator import run_pipeline
        state = await asyncio.wait_for(
            run_pipeline(dry_run=True, symbols_override=[yf_symbol]),
            timeout=180,  # 3-minute timeout
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail=f"Analysis timed out after 180s for {raw}")
    except Exception as exc:
        log.error("On-demand analysis failed for %s: %s", raw, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}")

    recs = state.get("recommendations") or []
    agent_results = (state.get("agent_results") or {}).get(yf_symbol, {})

    if not recs:
        # No recommendation produced — return agent signals at minimum
        return {
            "symbol":    raw,
            "yf_symbol": yf_symbol,
            "status":    "NO_RECOMMENDATION",
            "detail":    "Synthesis was suppressed or no actionable signal produced",
            "agents":    {k: {"signal": v.get("signal"), "score": v.get("score")} for k, v in agent_results.items()},
        }

    rec = recs[0]
    return {
        "symbol":    raw,
        "yf_symbol": yf_symbol,
        "status":    "OK",
        "analysis":  {
            "action":       rec.get("action"),
            "confidence":   rec.get("confidence"),
            "risk_score":   rec.get("risk_score"),
            "headline":     rec.get("headline"),
            "bull_case":    rec.get("bull_case") or [],
            "bear_case":    rec.get("bear_case") or [],
            "synthesis":    rec.get("synthesis") or rec.get("summary"),
            "entry_low":    rec.get("entry_low"),
            "entry_high":   rec.get("entry_high"),
            "target":       rec.get("target"),
            "stoploss":     rec.get("stoploss"),
            "upside_pct":   rec.get("upside_pct"),
            "horizon_days": rec.get("horizon_days"),
        },
        "agents": {
            k: {"signal": v.get("signal"), "score": v.get("score")}
            for k, v in agent_results.items()
        },
    }


# ── 10. Options market sentiment ──────────────────────────────────────────────

@app.get("/api/options/{symbol}", tags=["analysis"])
async def get_options_sentiment(
    symbol:  str,
    _:       None = Depends(require_api_key),
):
    """
    Options market sentiment for an NSE index or equity symbol.

    Tries NSE option chain (PCR, max pain, IV skew). Falls back to
    India VIX + realized vol estimates when NSE blocks server-side access.

    Response keys:
      symbol, signal, score, pcr, max_pain, atm_iv, iv_skew,
      india_vix, hv20, iv_hv_ratio, underlying_price,
      source ("nse" | "fallback"), commentary, score_breakdown, agent_name
    """
    plain = symbol.strip().upper().replace(".NS", "").replace(".BO", "")
    if not plain:
        raise HTTPException(status_code=400, detail="symbol is required")
    try:
        from agents.options_sentiment import analyse_options
        result = await asyncio.get_event_loop().run_in_executor(
            None, analyse_options, plain
        )
        return result
    except Exception as exc:
        log.error("options_sentiment failed for %s: %s", plain, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── 11. Valuation scenarios (bull / base / bear DCF) ─────────────────────────

@app.get("/api/valuation/{symbol}", tags=["analysis"])
async def get_valuation_scenarios(
    symbol: str,
    _:      None = Depends(require_api_key),
):
    """
    Bull / base / bear DCF valuation scenarios for an NSE equity symbol.

    Uses 3-stage DCF identical to warren_bot but with three parameterised
    scenarios and a sensitivity tornado showing which assumption matters most.

    Response keys:
      symbol, current_price, base_assumptions,
      scenarios {BULL/BASE/BEAR each: intrinsic_value, margin_of_safety_pct,
                 upside_pct, growth_rate, wacc, terminal_growth},
      fair_value_range {low, mid, high},
      margin_of_safety {bull, base, bear},
      upside_pct {bull, base, bear},
      tornado [{assumption, low_iv, high_iv, impact, impact_pct}],
      recommendation, data_quality, agent_name
    """
    plain = symbol.strip().upper().replace(".NS", "").replace(".BO", "")
    if not plain:
        raise HTTPException(status_code=400, detail="symbol is required")
    try:
        from agents.valuation_scenarios import run_scenarios
        result = await asyncio.get_event_loop().run_in_executor(
            None, run_scenarios, plain
        )
        return result
    except Exception as exc:
        log.error("valuation_scenarios failed for %s: %s", plain, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── 12. Paper Portfolio (P5-B) ────────────────────────────────────────────────

@app.get("/api/paper/portfolio", tags=["paper_portfolio"])
async def get_paper_portfolio(
    _: None = Depends(require_api_key),
):
    """
    Current paper portfolio: open positions, recent closed trades, summary metrics.

    Returns:
      {
        open_positions: [...],
        recent_closed:  [...],
        closed_count:   int,
        summary:        { total_invested, unrealized_pnl, realized_pnl, total_pnl_pct,
                          nifty_return_pct, alpha_pct, open_positions, closed_positions },
        win_rate:       float | null,
        avg_alpha_closed: float | null,
      }
    """
    if not _supabase:
        raise HTTPException(status_code=503, detail="Database unavailable")
    try:
        loop = asyncio.get_event_loop()
        def _run():
            from agents.paper_portfolio import get_portfolio_summary
            return get_portfolio_summary(_supabase)
        result = await loop.run_in_executor(None, _run)
        return _sanitise_floats(result)
    except Exception as exc:
        log.error("paper/portfolio error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/paper/history", tags=["paper_portfolio"])
async def get_paper_portfolio_history(
    days: int  = Query(180, description="History window in days"),
    _:    None = Depends(require_api_key),
):
    """
    Daily paper portfolio snapshots for the P&L chart.

    Returns:
      { snapshots: [ { snapshot_date, total_pnl_pct, nifty_return_pct, alpha_pct,
                       total_invested, open_positions, closed_positions }, ... ] }
    """
    if not _supabase:
        raise HTTPException(status_code=503, detail="Database unavailable")
    try:
        cutoff = str(date.today() - timedelta(days=days))
        rows = (
            _supabase
            .table("paper_portfolio_snapshots")
            .select("snapshot_date,total_invested,total_current_value,unrealized_pnl,"
                    "realized_pnl,total_pnl,total_pnl_pct,open_positions,closed_positions,"
                    "nifty_value,nifty_return_pct,alpha_pct")
            .gte("snapshot_date", cutoff)
            .order("snapshot_date", desc=False)
            .execute()
            .data or []
        )
        return _sanitise_floats({"snapshots": rows, "total": len(rows)})
    except Exception as exc:
        log.error("paper/history error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── 13. Agent Attribution (P5-A) ───────────────────────────────────────────────

@app.get("/api/attribution/agents", tags=["performance"])
async def get_agent_attribution(
    _: None = Depends(require_api_key),
):
    """
    Per-agent accuracy attribution derived from resolved recommendation_outcomes.

    For each agent that voted BULLISH on a recommendation, computes hit rate and
    average alpha at 90 days — shows which agents are adding signal vs noise.

    Returns:
      { agents: [ { agent_name, signal_count, bullish_count,
                    hit_rate_90d, avg_alpha_90d, avg_score_bullish,
                    contribution_score }, ... ],
        total_resolved: int,
        note: str }
    """
    if not _supabase:
        raise HTTPException(status_code=503, detail="Database unavailable")
    try:
        loop = asyncio.get_event_loop()
        def _run():
            from agents.outcome_tracker import run_attribution_analysis
            return run_attribution_analysis()
        agents = await loop.run_in_executor(None, _run)
        note = (
            "Attribution data will populate after recommendations are 90+ days old."
            if not agents else
            f"Attribution based on {sum(a['signal_count'] for a in agents)} resolved signal votes."
        )
        return _sanitise_floats({
            "agents":         agents,
            "total_resolved": len(agents),
            "note":           note,
        })
    except Exception as exc:
        log.error("attribution/agents error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── 14. P5-E: Live performance snapshot ────────────────────────────────────────

@app.get("/api/performance/live", tags=["performance"])
async def get_live_performance(
    _: None = Depends(require_api_key),
):
    """
    P5-D/E: Current live performance of all open (PENDING) recommendations.

    Returns portfolio-level aggregate stats plus per-rec detail, sorted by
    alpha_live descending. Data is populated daily at 16:30 IST by the
    Forward Outcome Poller (run_forward_polling). Before the first poller run
    on a fresh deploy, returns empty stats.

    Response:
      { total_open, total_resolved, avg_live_return_pct, avg_live_alpha_pct,
        positive_count, has_live_data, by_action: {...}, recs: [...] }
    """
    if not _supabase:
        raise HTTPException(status_code=503, detail="Database unavailable")
    try:
        loop = asyncio.get_event_loop()
        def _run():
            from agents.outcome_tracker import get_live_performance_summary
            return get_live_performance_summary()
        summary = await loop.run_in_executor(None, _run)
        return _sanitise_floats(summary or {})
    except Exception as exc:
        log.error("performance/live error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/attribution/live", tags=["performance"])
async def get_live_attribution(
    _: None = Depends(require_api_key),
):
    """
    P5-E: Per-agent attribution using LIVE alpha (alpha_live) instead of waiting
    for 90-day resolution.

    For each agent that voted BULLISH on any open recommendation, computes:
      - avg_bull_alpha_live: average alpha vs NIFTY 50 since recommendation date
      - positive_rate_live: % of bullish votes currently outperforming NIFTY
      - signal_count, bullish_count, avg_days_held

    Useful immediately — updates daily as soon as run_forward_polling fires.

    Response:
      { agents: [...], total_open: int, note: str }
    """
    if not _supabase:
        raise HTTPException(status_code=503, detail="Database unavailable")
    try:
        loop = asyncio.get_event_loop()
        def _run():
            from agents.outcome_tracker import run_live_attribution
            return run_live_attribution()
        agents = await loop.run_in_executor(None, _run)
        note = (
            "Live attribution based on current prices vs NIFTY 50. "
            "Updates daily at 16:30 IST. 90-day resolved data will replace this once available."
            if agents else
            "No live data yet — the Forward Poller runs at 16:30 IST and will populate this."
        )
        return _sanitise_floats({
            "agents":     agents,
            "total_open": len(agents),
            "note":       note,
        })
    except Exception as exc:
        log.error("attribution/live error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── 15. WebSocket — real-time critical alerts ──────────────────────────────────

@app.websocket("/ws/alerts")
async def websocket_alerts(
    websocket: WebSocket,
    api_key:   str | None = Query(default=None),
):
    """
    Persistent WebSocket connection.
    Auth: ?api_key=<DASHBOARD_API_KEY> query param.
    Receives: nothing (server-push only)
    Sends:
      {type: "ping"}                              every 25 s (keepalive)
      {type: "critical_alert", alerts: [...]}     when broadcaster finds unresolved CRITICAL/DANGER alerts
    """
    if DASHBOARD_API_KEY and api_key != DASHBOARD_API_KEY:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    await manager.connect(websocket)
    try:
        while True:
            await asyncio.sleep(25)
            try:
                await websocket.send_json({"type": "ping"})
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(websocket)
