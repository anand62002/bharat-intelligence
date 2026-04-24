"""
api/main.py — Bharat Intelligence FastAPI Backend
==================================================
Serves live data to the React dashboard.

Endpoints
---------
  GET  /api/recommendations      Latest recs sorted by upside_pct, critical first
  GET  /api/discovery            is_discovery=true recs created today (7-day fallback)
  GET  /api/portfolio            Open portfolio holdings (status = OPEN)
  POST /api/portfolio            Add or update a holding (upsert by symbol+OPEN)
  GET  /api/portfolio/alerts     Unresolved portfolio alerts
  GET  /api/governance/alerts    Open governance / agent-health alerts
  GET  /api/governance/research  Research proposals ordered by relevance desc
  GET  /api/market/pulse         Live prices (yfinance) + FII net from Supabase
  WS   /ws/alerts                Real-time critical-danger broadcast

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
                .execute()
            )
            if result.data:
                await manager.broadcast({
                    "type":      "critical_alert",
                    "alerts":    result.data,
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

    meta    = row.get("metadata") or {}
    agents  = row.get("agent_signals") or {}
    gov     = row.get("gov_check")     or {}
    horizon = row.get("horizon_days")

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
        "govCheck":         gov,
        "createdAt":        str(row.get("created_at") or ""),
        # Discovery-tab extra fields (stored in metadata by the discovery screener)
        "discoveryScore":   meta.get("discovery_score")  or 0,
        "discoveryReason":  meta.get("discovery_reason") or "",
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
    """
    syms = [sym for _, sym, _ in MARKET_SYMBOLS]
    out: dict[str, tuple[float, float]] = {}
    try:
        df = yf.download(
            syms,
            period    = "2d",
            interval  = "1d",
            auto_adjust = True,
            progress  = False,
            group_by  = "ticker",
        )
        for sym in syms:
            try:
                closes = (df["Close"] if len(syms) == 1 else df[sym]["Close"]).dropna()
                if len(closes) >= 2:
                    out[sym] = (float(closes.iloc[-1]), float(closes.iloc[-2]))
                elif len(closes) == 1:
                    out[sym] = (float(closes.iloc[-1]), float(closes.iloc[-1]))
            except Exception:
                pass
    except Exception as exc:
        log.warning("yfinance batch download failed: %s", exc)
        # Fallback: try individual tickers
        for sym in syms:
            try:
                closes = yf.Ticker(sym).history(period="2d")["Close"].dropna()
                if len(closes) >= 2:
                    out[sym] = (float(closes.iloc[-1]), float(closes.iloc[-2]))
                elif len(closes) == 1:
                    out[sym] = (float(closes.iloc[-1]), float(closes.iloc[-1]))
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

    recs = [_transform_recommendation(r) for r in rows]
    recs.sort(key=lambda r: 0 if (r["upsidePct"] >= 100 and r["upsideConfidence"] >= 70) else 1)
    return recs


# ── 2. Discovery ───────────────────────────────────────────────────────────────

@app.get("/api/discovery", tags=["recommendations"])
async def get_discovery(
    _: None = Depends(require_api_key),
):
    """
    Returns is_discovery=true recommendations created today.
    Falls back to last 7 days when today has no rows.
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
        week_ago = (date.today() - timedelta(days=7)).isoformat()
        rows = (db.table("recommendations")
                  .select("*")
                  .eq("is_discovery", True)
                  .gte("created_at", week_ago)
                  .order("created_at", desc=True)
                  .limit(10)
                  .execute()
                  .data or [])

    recs = [_transform_recommendation(r) for r in rows]
    recs.sort(key=lambda r: 0 if (r["upsidePct"] >= 100 and r["upsideConfidence"] >= 70) else 1)
    return recs


# ── 3 & 4. Portfolio ───────────────────────────────────────────────────────────

@app.get("/api/portfolio", tags=["portfolio"])
async def get_portfolio(
    _: None = Depends(require_api_key),
):
    """Returns all OPEN portfolio holdings, danger holdings first."""
    rows = (_db()
            .table("portfolio_holdings")
            .select("*")
            .eq("status", "OPEN")
            .order("created_at", desc=True)
            .execute()
            .data or [])

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

    Required body field: symbol
    Optional: name, sector, qty, avg_buy, target_price, stoploss_price,
              notes, linked_rec_id, status, current_price
    """
    db     = _db()
    symbol = (payload.get("symbol") or "").upper().strip()
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol is required")

    existing = (db.table("portfolio_holdings")
                  .select("id")
                  .eq("symbol", symbol)
                  .eq("status", "OPEN")
                  .limit(1)
                  .execute()
                  .data or [])

    row: dict[str, Any] = {
        "symbol":         symbol,
        "name":           payload.get("name")           or symbol,
        "sector":         payload.get("sector")         or "—",
        "qty":            int(payload.get("qty") or 1),
        "avg_buy":        float(payload.get("avg_buy") or 0),
        "current_price":  float(payload.get("current_price") or payload.get("avg_buy") or 0) or None,
        "target_price":   float(payload.get("target_price")  or 0) or None,
        "stoploss_price": float(payload.get("stoploss_price") or 0) or None,
        "notes":          payload.get("notes")          or "",
        "linked_rec_id":  payload.get("linked_rec_id"),
        "status":         payload.get("status")         or "OPEN",
    }

    if existing:
        result = (db.table("portfolio_holdings")
                    .update(row)
                    .eq("id", existing[0]["id"])
                    .execute())
    else:
        result = db.table("portfolio_holdings").insert(row).execute()

    if result.data:
        return _transform_holding(result.data[0])
    raise HTTPException(status_code=500, detail="Upsert returned no data")


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
    try:
        for row in (db.table("portfolio_alerts")
                      .select("*")
                      .eq("resolved", False)
                      .in_("severity", ["CRITICAL", "DANGER"])
                      .execute()
                      .data or []):
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


# ── 9. WebSocket — real-time critical alerts ───────────────────────────────────

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
