"""
agents/institutional.py — Institutional Flow Analysis Agent
Tracks FII/DII flows, bulk/block deals, and MF activity for NSE stocks.

Entry point: analyse(symbol, sector=None) -> dict
"""

import csv
import io
import logging
import os
import sys
from datetime import date, datetime, timedelta
from typing import Optional

import requests

from dotenv import load_dotenv

load_dotenv()

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from data.fetchers import get_nse_fii_dii  # noqa: E402
from agents.base import DataCompletenessValidator, insufficient_data_result

_dcv = DataCompletenessValidator()

log = logging.getLogger(__name__)
AGENT_NAME = "institutional"

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

# NSE bulk-deal endpoints to try in order (the first has moved; we try both)
_NSE_BULK_URLS = [
    # Current endpoint (www subdomain, with session cookie)
    "https://www.nseindia.com/api/historical/bulk-deals?from={from_date}&to={to_date}&symbol={symbol}",
    # Legacy endpoint that was used previously
    "https://nseindia.com/api/historical/bulk-deals?from={from_date}&to={to_date}&symbol={symbol}",
    # allorigins proxy as last resort (bypasses cookie requirement)
    "https://api.allorigins.win/get?url=https%3A//www.nseindia.com/api/historical/bulk-deals%3Ffrom%3D{from_date}%26to%3D{to_date}%26symbol%3D{symbol}",
]

# Fallback NSE headers (required or NSE blocks the request)
_NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/csv, */*",
    "Referer": "https://www.nseindia.com/",
    "Accept-Language": "en-US,en;q=0.9",
}

# MF-linked keywords in bulk deal client names
_MF_KEYWORDS = {
    "mutual fund", "mf", "trustee", "amc", "asset management",
    "sbi mf", "hdfc mf", "icici pru", "axis mf", "kotak mf",
    "nippon", "mirae", "dsp", "franklin", "uti", "invesco", "aditya birla",
}

# Block-deal minimum size (₹ Cr) to be considered significant
_BLOCK_DEAL_MIN_CR = 25.0
_CRORE = 1e7  # 1 Cr = 10M

# Danger / opportunity thresholds (spec)
_FII_DANGER_5D_CR   = -500.0   # FII net sell > ₹500 Cr in 5 sessions
_FII_OPPTY_10D_CR   =  1000.0  # FII net buy  > ₹1000 Cr in 10 sessions

# ──────────────────────────────────────────────────────────────────────────────
# NSE bulk-deal fetcher
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_bulk_deals(symbol: str, days: int = 30) -> list[dict]:
    """
    Fetch bulk/block deal records from NSE for a symbol over the last `days`.

    Tries multiple URL patterns (the original endpoint returned 404 after NSE
    restructured their API). Falls back to allorigins.win proxy when direct
    calls fail. Returns [] on all failures; caller handles gracefully.
    """
    import json as _json

    clean     = symbol.replace(".NS", "").replace(".BO", "").upper()
    to_date   = date.today()
    from_date = to_date - timedelta(days=days)
    fmt       = {"symbol": clean,
                 "from_date": from_date.strftime("%d-%m-%Y"),
                 "to_date":   to_date.strftime("%d-%m-%Y")}

    raw = None
    for url_tmpl in _NSE_BULK_URLS:
        url = url_tmpl.format(**fmt)
        is_proxy = "allorigins" in url
        try:
            if is_proxy:
                resp = requests.get(url, headers=_NSE_HEADERS, timeout=15)
                resp.raise_for_status()
                raw = resp.json().get("contents", "")
            else:
                session = requests.Session()
                session.get("https://www.nseindia.com/", headers=_NSE_HEADERS, timeout=10)
                resp = session.get(url, headers=_NSE_HEADERS, timeout=12)
                resp.raise_for_status()
                raw = resp.text
            if raw and raw.strip():
                break
        except requests.RequestException as exc:
            log.debug("NSE bulk-deal (%s) failed: %s", url_tmpl[:60], exc)
            raw = None

    if not raw:
        log.warning("NSE bulk-deal fetch failed: all %d URL variants exhausted", len(_NSE_BULK_URLS))
        return []

    try:
        data    = _json.loads(raw)
        records = data if isinstance(data, list) else data.get("data", [])
    except (_json.JSONDecodeError, AttributeError):
        records = _parse_bulk_csv(raw, clean)

    deals = []
    for r in records:
        try:
            qty      = float(r.get("quantityTraded") or r.get("TD_QTY_TRADED") or 0)
            price    = float(r.get("tradePrice")     or r.get("TD_TRADE_PRICE") or 0)
            value_cr = qty * price / _CRORE
            if value_cr < _BLOCK_DEAL_MIN_CR:
                continue

            client   = str(r.get("clientName") or r.get("TD_CLIENT_NAME") or "").strip()
            buy_sell = str(r.get("buySell")    or r.get("TD_BUY_SELL") or "").upper()
            deal_date= str(r.get("date")       or r.get("TD_DT") or "")

            deals.append({
                "date":     deal_date,
                "symbol":   clean,
                "client":   client,
                "side":     "BUY" if buy_sell.startswith("B") else "SELL",
                "qty":      int(qty),
                "price":    round(price, 2),
                "value_cr": round(value_cr, 2),
                "is_mf":    _is_mf_client(client),
            })
        except (ValueError, TypeError):
            continue

    return deals


def _fetch_yf_institutional_holders(symbol: str) -> dict:
    """
    Fallback when NSE bulk-deal API is blocked.

    Fetches yfinance institutional_holders (quarterly snapshot, NOT daily flows).
    Used only as a supplementary signal — does NOT replace flow-based scoring.

    Returns:
        {
            pct_institutions: float|None  — % of shares held by institutions
            top_holders:      list[dict]  — [{name, shares, pct_out}]
            source:           "yfinance_institutional"
        }
        or {} on failure.
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)

        pct_institutions: Optional[float] = None
        try:
            major = ticker.major_holders
            if major is not None and not major.empty:
                # Row 1 in major_holders = "% of Shares Held by Institutions"
                raw_val = major.iloc[1, 0]
                pct_institutions = round(float(str(raw_val).strip("%")) * (
                    0.01 if float(str(raw_val).strip("%")) <= 1.0 else 1.0
                ), 2)
        except Exception:
            pass

        top_holders: list[dict] = []
        try:
            inst = ticker.institutional_holders
            if inst is not None and not inst.empty:
                for _, row in inst.head(5).iterrows():
                    top_holders.append({
                        "name":    str(row.get("Holder", "")),
                        "shares":  int(row.get("Shares", 0)),
                        "pct_out": round(float(row.get("% Out", 0)) * 100, 2),
                    })
        except Exception:
            pass

        if pct_institutions is None and not top_holders:
            return {}

        return {
            "pct_institutions": pct_institutions,
            "top_holders":      top_holders,
            "source":           "yfinance_institutional",
        }
    except Exception as exc:
        log.debug("yfinance institutional_holders failed: %s", exc)
        return {}


def _parse_bulk_csv(raw: str, symbol: str) -> list[dict]:
    """
    Parse NSE bulk-deal CSV. Header columns vary by year; we handle common variants.
    Expected columns (approx): Symbol, Client Name, Buy/Sell, Quantity Traded, Trade Price
    """
    records = []
    try:
        reader = csv.DictReader(io.StringIO(raw))
        for row in reader:
            # Normalise keys to lowercase with underscores
            norm = {k.strip().lower().replace(" ", "_"): v.strip() for k, v in row.items()}
            sym = norm.get("symbol", "").upper()
            if sym and sym != symbol:
                continue
            records.append({
                "clientName":    norm.get("client_name", ""),
                "buySell":       norm.get("buy/sell", norm.get("buy_sell", "")),
                "quantityTraded": norm.get("quantity_traded", "0"),
                "tradePrice":    norm.get("trade_price", "0"),
                "date":          norm.get("date", ""),
            })
    except Exception as exc:
        log.debug("CSV parse failed: %s", exc)
    return records


def _is_mf_client(client: str) -> bool:
    cl = client.lower()
    return any(kw in cl for kw in _MF_KEYWORDS)


# ──────────────────────────────────────────────────────────────────────────────
# Supabase: rolling flow history
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_historical_flows(sessions: int = 10) -> list[dict]:
    """
    Pull the last `sessions` rows of FII/DII data from Supabase daily_runs
    or a dedicated flow cache.  Falls back to an empty list if Supabase is
    unconfigured — callers handle missing history gracefully.

    Returns list of {date, fii_net, dii_net} sorted oldest-first.
    """
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        return []
    try:
        from supabase import create_client
        client = create_client(url, key)
        resp = (
            client.table("institutional_flows")
            .select("session_date, fii_net, dii_net")
            .order("session_date", desc=True)
            .limit(sessions)
            .execute()
        )
        rows = resp.data or []
        return sorted(rows, key=lambda r: r.get("session_date", ""))
    except Exception as exc:
        log.debug("Supabase flow fetch failed (non-critical): %s", exc)
        return []


def _store_flow(session_date: str, fii_net: float, dii_net: float) -> None:
    """Upsert today's FII/DII flow into Supabase institutional_flows."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        return
    try:
        from supabase import create_client
        create_client(url, key).table("institutional_flows").upsert({
            "session_date": session_date,
            "fii_net": fii_net,
            "dii_net": dii_net,
        }, on_conflict="session_date").execute()
    except Exception as exc:
        log.debug("Supabase flow store failed (non-critical): %s", exc)


def _save_institutional_flows(result: dict, client) -> None:
    """
    Upsert today's market-wide FII/DII flow into institutional_flows.

    Designed to be called ONCE per pipeline run from the orchestrator
    save_recs node, using the Supabase client already open for the run.
    Supplements the per-symbol _store_flow() calls inside analyse() by
    ensuring the write always happens even when get_nse_fii_dii() fails
    mid-pipeline, and adds fii_buy/fii_sell when available.
    """
    fii_net      = result.get("today_fii_net")
    dii_net      = result.get("today_dii_net")
    if fii_net is None and dii_net is None:
        log.debug("_save_institutional_flows: no live data in result, skipping")
        return
    session_date = result.get("today_date") or date.today().isoformat()
    try:
        client.table("institutional_flows").upsert(
            {
                "session_date": session_date,
                "fii_net":      float(fii_net)  if fii_net  is not None else None,
                "dii_net":      float(dii_net)  if dii_net  is not None else None,
                "fii_buy":      result.get("today_fii_buy"),
                "fii_sell":     result.get("today_fii_sell"),
            },
            on_conflict="session_date",
        ).execute()
        log.info(
            "institutional_flows upserted: date=%s fii_net=%.0f dii_net=%.0f",
            session_date, fii_net or 0, dii_net or 0,
        )
    except Exception as exc:
        log.warning("_save_institutional_flows failed (non-critical): %s", exc)


def _write_agent_performance() -> None:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        return
    try:
        from supabase import create_client
        create_client(url, key).table("agent_performance").insert({
            "agent_name": AGENT_NAME,
            "accuracy_90d": None,
            "hallucination_rate": None,
            "trend": "STABLE",
            "audit_date": date.today().isoformat(),
        }).execute()
    except Exception as exc:
        log.warning("agent_performance write failed: %s", exc)


# ──────────────────────────────────────────────────────────────────────────────
# Flow aggregation
# ──────────────────────────────────────────────────────────────────────────────

def _build_flow_history(
    live: Optional[dict],
    historical: list[dict],
    sessions: int,
) -> list[dict]:
    """
    Merge live (today's) flow with Supabase history.
    Returns list of up to `sessions` records, oldest-first.
    """
    rows = list(historical)           # already sorted oldest-first
    if live:
        today = live.get("date", date.today().isoformat())
        # Avoid duplicating today if already stored
        if not rows or rows[-1].get("session_date", rows[-1].get("date")) != today:
            rows.append({"session_date": today,
                         "fii_net": live["fii_net"],
                         "dii_net": live["dii_net"]})
    return rows[-sessions:]           # keep most recent N


def _net_totals(rows: list[dict]) -> tuple[float, float]:
    """Return (fii_net_total, dii_net_total) across all rows."""
    fii = sum(float(r.get("fii_net") or 0) for r in rows)
    dii = sum(float(r.get("dii_net") or 0) for r in rows)
    return round(fii, 2), round(dii, 2)


def _consecutive_direction(rows: list[dict], key: str, direction: str) -> int:
    """
    Count the longest streak of sessions where `key` has the given `direction`
    ('buy' if value > 0, 'sell' if value < 0), reading backwards from most recent.
    """
    streak = 0
    for r in reversed(rows):
        val = float(r.get(key) or 0)
        if direction == "buy"  and val > 0:
            streak += 1
        elif direction == "sell" and val < 0:
            streak += 1
        else:
            break
    return streak


# ──────────────────────────────────────────────────────────────────────────────
# Scoring
# ──────────────────────────────────────────────────────────────────────────────

def _score_fii(rows_5d: list[dict]) -> tuple[int, str]:
    """
    FII score component — max ±30 pts.
    Consistent 3+ day buying  = +30 pts (spec).
    Consistent 3+ day selling = -30 pts (spec).
    Otherwise scaled linearly by net flow direction.
    """
    if not rows_5d:
        return 0, "No FII data available"

    buy_streak  = _consecutive_direction(rows_5d, "fii_net", "buy")
    sell_streak = _consecutive_direction(rows_5d, "fii_net", "sell")
    fii_5d, _   = _net_totals(rows_5d)

    if buy_streak >= 3:
        score = 30
        note  = f"FII buying {buy_streak} consecutive sessions (net ₹{fii_5d:.0f} Cr)"
    elif sell_streak >= 3:
        score = -30
        note  = f"FII selling {sell_streak} consecutive sessions (net ₹{fii_5d:.0f} Cr)"
    elif fii_5d > 0:
        score = min(20, int(fii_5d / 50))   # +1 pt per ₹50 Cr, cap 20
        note  = f"FII net buying ₹{fii_5d:.0f} Cr over {len(rows_5d)} sessions"
    else:
        score = max(-20, int(fii_5d / 50))
        note  = f"FII net selling ₹{abs(fii_5d):.0f} Cr over {len(rows_5d)} sessions"

    return max(-30, min(30, score)), note


def _score_dii(rows_5d: list[dict], fii_score: int) -> tuple[int, str]:
    """
    DII score component — max 15 pts.
    DII absorbing (buying while FII selling) = +15 pts (spec).
    DII simply buying = +10 pts.
    DII selling = 0 pts.
    """
    if not rows_5d:
        return 0, "No DII data available"

    _, dii_5d = _net_totals(rows_5d)
    dii_buying = dii_5d > 0

    if dii_buying and fii_score < 0:
        score = 15
        note  = f"DII absorbing FII selling (DII net ₹{dii_5d:.0f} Cr)"
    elif dii_buying:
        score = 10
        note  = f"DII net buying ₹{dii_5d:.0f} Cr"
    else:
        score = 0
        note  = f"DII net selling ₹{abs(dii_5d):.0f} Cr"

    return score, note


def _score_bulk_deals(deals: list[dict], symbol: str) -> tuple[int, str]:
    """
    Bulk/block deal component — max ±20 pts.
    Large institutional buys = positive; large sells = negative.
    MF involvement weighted slightly higher.
    """
    if not deals:
        return 0, "No significant bulk deals found"

    buy_cr  = sum(d["value_cr"] for d in deals if d["side"] == "BUY")
    sell_cr = sum(d["value_cr"] for d in deals if d["side"] == "SELL")
    mf_buy  = sum(d["value_cr"] for d in deals if d["side"] == "BUY"  and d["is_mf"])
    mf_sell = sum(d["value_cr"] for d in deals if d["side"] == "SELL" and d["is_mf"])

    net_cr = buy_cr - sell_cr
    score  = max(-20, min(20, int(net_cr / 50)))   # ±1 pt per ₹50 Cr

    parts = []
    if buy_cr:
        parts.append(f"institutional buy ₹{buy_cr:.0f} Cr")
    if sell_cr:
        parts.append(f"institutional sell ₹{sell_cr:.0f} Cr")
    if mf_buy:
        parts.append(f"MF buy ₹{mf_buy:.0f} Cr")
    if mf_sell:
        parts.append(f"MF sell ₹{mf_sell:.0f} Cr")

    return score, "; ".join(parts) or "Bulk deals present"


# ──────────────────────────────────────────────────────────────────────────────
# Danger & opportunity detection
# ──────────────────────────────────────────────────────────────────────────────

def _detect_signals(
    rows_5d: list[dict],
    rows_10d: list[dict],
    deals: list[dict],
    promoter_pledging: Optional[float],
) -> list[dict]:
    """
    Returns list of danger/opportunity signal dicts.

    CRITICAL DANGER  (spec):
        FII net sell > ₹500 Cr in 5 sessions
        + MF exits detected in bulk deals
        + promoter pledging rising (pledging > 20%)

    CRITICAL OPPORTUNITY (spec):
        FII net buy > ₹1000 Cr in 10 sessions
        + DII also buying (rare convergence)
    """
    signals: list[dict] = []

    fii_5d,  dii_5d  = _net_totals(rows_5d)
    fii_10d, dii_10d = _net_totals(rows_10d)

    mf_sell_cr = sum(d["value_cr"] for d in deals if d["is_mf"] and d["side"] == "SELL")
    mf_exit    = mf_sell_cr > 0

    pledging_concern = (promoter_pledging is not None and promoter_pledging > 20)

    # ── CRITICAL DANGER ───────────────────────────────────────────────────────
    danger_triggers: list[str] = []
    if fii_5d <= _FII_DANGER_5D_CR:
        danger_triggers.append(
            f"fii_net_sell_{abs(fii_5d):.0f}cr_5sessions"
        )
    if mf_exit:
        danger_triggers.append(f"mf_exits_detected_{mf_sell_cr:.0f}cr")
    if pledging_concern:
        danger_triggers.append(
            f"promoter_pledging_{promoter_pledging:.0f}pct_above_20"
        )

    if len(danger_triggers) >= 3:
        signals.append({
            "type":        "CRITICAL_DANGER",
            "label":       "fii_sell_mf_exit_pledging_rising",
            "fii_net_5d":  fii_5d,
            "mf_sell_cr":  round(mf_sell_cr, 2),
            "pledging":    promoter_pledging,
            "triggers":    danger_triggers,
            "description": (
                f"FII sold ₹{abs(fii_5d):.0f} Cr in 5 sessions, "
                f"MF exits ₹{mf_sell_cr:.0f} Cr, "
                f"promoter pledging {promoter_pledging}%"
            ),
        })
    elif len(danger_triggers) == 2:
        signals.append({
            "type":     "WARNING",
            "label":    "partial_institutional_danger",
            "triggers": danger_triggers,
            "description": "2 of 3 institutional danger triggers active",
        })
    elif len(danger_triggers) == 1:
        signals.append({
            "type":     "WATCH",
            "label":    "single_institutional_concern",
            "triggers": danger_triggers,
            "description": f"Monitoring: {danger_triggers[0]}",
        })

    # ── CRITICAL OPPORTUNITY ─────────────────────────────────────────────────
    if fii_10d >= _FII_OPPTY_10D_CR and dii_10d > 0:
        signals.append({
            "type":        "CRITICAL_OPPORTUNITY",
            "label":       "fii_dii_convergence_buying",
            "fii_net_10d": fii_10d,
            "dii_net_10d": dii_10d,
            "description": (
                f"Rare convergence: FII bought ₹{fii_10d:.0f} Cr + "
                f"DII bought ₹{dii_10d:.0f} Cr over 10 sessions"
            ),
        })

    return signals


# ──────────────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────────────

def analyse(
    symbol: str,
    promoter_pledging: Optional[float] = None,
) -> dict:
    """
    Run institutional flow analysis for a single NSE/BSE symbol.

    Args:
        symbol:             NSE ticker, e.g. "RELIANCE", "TCS.NS"
        promoter_pledging:  % of promoter shares pledged (from fundamental agent)

    Returns:
        {
            signal:         str   — STRONG_BUY | BUY | HOLD | AVOID | SELL | NO_DATA
            score:          int   — net score (FII ±30 + DII 0–15 + bulk ±20; normalised 0–100)
            detail:         dict  — sub-scores, streaks, deal summary
            fii_net_5d:     float — FII net flow last 5 sessions (₹ Cr)
            dii_net_5d:     float — DII net flow last 5 sessions (₹ Cr)
            bulk_deals:     list  — significant deals (≥ ₹25 Cr)
            danger_signals: list  — CRITICAL_DANGER / CRITICAL_OPPORTUNITY / WARNING / WATCH
            data_sources:   list[str]
            agent_name:     str   — "institutional"
        }
    """
    data_sources: list[str] = []

    # ── 1. Live FII/DII ───────────────────────────────────────────────────────
    live = get_nse_fii_dii()
    if live:
        data_sources.append("nse_fii_dii_live")
        try:
            _store_flow(
                live.get("date", date.today().isoformat()),
                live["fii_net"],
                live["dii_net"],
            )
        except Exception:
            pass

    # ── 2. Historical flows from Supabase ─────────────────────────────────────
    try:
        historical = _fetch_historical_flows(sessions=10)
    except Exception as exc:
        log.warning("Historical flow fetch failed (non-critical): %s", exc)
        historical = []
    if historical:
        data_sources.append("supabase_flow_history")

    rows_10d = _build_flow_history(live, historical, sessions=10)
    rows_5d  = rows_10d[-5:]

    if not rows_5d and not live:
        return {
            "signal":         "NO_DATA",
            "score":          50,
            "detail":         {"error": "No FII/DII data available"},
            "fii_net_5d":     None,
            "dii_net_5d":     None,
            "bulk_deals":     [],
            "danger_signals": [],
            "data_sources":   [],
            "agent_name":     AGENT_NAME,
        }

    # ── 2a. Data completeness check ──────────────────────────────────────────
    _fii_net_latest = live.get("fii_net") if live else (rows_5d[-1].get("fii_net") if rows_5d else None)
    _dii_net_latest = live.get("dii_net") if live else (rows_5d[-1].get("dii_net") if rows_5d else None)
    _snapshot = {
        "fii_net":      _fii_net_latest,
        "dii_net":      _dii_net_latest,
        "data_quality": "FULL" if live else ("PARTIAL" if rows_5d else "NO_DATA"),
    }
    _chk = _dcv.validate(_snapshot, "institutional")
    if not _chk.is_sufficient:
        return insufficient_data_result("institutional", _chk,
                                        data_sources=data_sources,
                                        fii_net_5d=None,
                                        dii_net_5d=None,
                                        bulk_deals=[],
                                        danger_signals=[],
                                        data_quality="NO_DATA",
                                        data_unavailable_note=_chk.summary())

    fii_net_5d, dii_net_5d   = _net_totals(rows_5d)
    fii_net_10d, dii_net_10d = _net_totals(rows_10d)

    # ── 3. Bulk / block deals ─────────────────────────────────────────────────
    bulk_deals = _fetch_bulk_deals(symbol, days=30)
    yf_inst: dict = {}
    if bulk_deals:
        data_sources.append("nse_bulk_deals")
    else:
        # NSE bulk-deal API blocked → fall back to yfinance institutional holders
        # This is a quarterly snapshot (not daily flows) used only for narrative;
        # it adds a small ±5pt score nudge based on institutional ownership level.
        yf_inst = _fetch_yf_institutional_holders(symbol)
        if yf_inst:
            data_sources.append("yfinance_institutional")

    # ── Data quality tier ─────────────────────────────────────────────────────
    # Used to decide whether a score of 50 (neutral) should be forced.
    # Absence of data ≠ bearish signal — explicitly label the gap.
    nse_flow_available = bool(rows_5d)
    nse_bulk_available = bool(bulk_deals)
    yf_pct_available   = bool(yf_inst and yf_inst.get("pct_institutions") is not None
                               and yf_inst["pct_institutions"] > 0)

    if nse_flow_available and nse_bulk_available:
        data_quality = "FULL"
    elif nse_flow_available:
        data_quality = "PARTIAL"          # FII/DII ok, bulk deals blocked
    elif yf_pct_available:
        data_quality = "SNAPSHOT_ONLY"    # only yfinance quarterly snapshot
    else:
        data_quality = "NO_DATA"

    # ── 4. Score ──────────────────────────────────────────────────────────────
    fii_score,  fii_note  = _score_fii(rows_5d)
    dii_score,  dii_note  = _score_dii(rows_5d, fii_score)
    bulk_score, bulk_note = _score_bulk_deals(bulk_deals, symbol)

    # yfinance snapshot nudge — POSITIVE only.
    #
    # Rationale for NO negative nudge:
    #   0% institutional ownership is AMBIGUOUS — it could mean:
    #     (a) yfinance data unavailable for this ticker
    #     (b) small-cap not yet on institutional radar (neutral, not bearish)
    #   Penalising ambiguous data violates "data gap = neutral" principle.
    #   We only add a positive signal when institutions are *confirmed* present.
    yf_nudge      = 0
    yf_nudge_note = ""
    if yf_inst and not bulk_deals:
        pct = yf_inst.get("pct_institutions")
        if pct is not None and pct > 0:
            if pct >= 20:
                yf_nudge      = 5
                yf_nudge_note = f"Strong institutional ownership {pct:.1f}% (yfinance snapshot)"
            elif pct >= 5:
                yf_nudge      = 2
                yf_nudge_note = f"Moderate institutional ownership {pct:.1f}% (yfinance snapshot)"
            else:
                # 0 < pct < 5: token holding — informational only, no score impact
                yf_nudge_note = (
                    f"Minimal institutional ownership {pct:.1f}% (yfinance snapshot); "
                    "insufficient to signal direction"
                )
        else:
            # pct = 0 or None: ambiguous — do not penalise
            yf_nudge_note = (
                "Institutional ownership data unavailable or 0% (yfinance snapshot). "
                "Treated as neutral — absence of data is not a bearish signal."
            )

    # ── Normalise to 0–100 ────────────────────────────────────────────────────
    # Formula centres at exactly 50 when raw_score = 0:
    #   raw range: [-50, 65]  →  offset = 57.5, span = 115
    #   (0 + 57.5) / 115 * 100 = 50.0 ✓
    #
    # OVERRIDE to 50 when no real flow data existed — data gap must not
    # produce a sub-50 (negative-leaning) score.
    raw_score = fii_score + dii_score + bulk_score + yf_nudge

    data_unavailable_note = ""
    if data_quality == "NO_DATA":
        normalised = 50
        data_unavailable_note = (
            "NSE FII/DII API and bulk-deal API both unavailable; "
            "yfinance institutional snapshot also returned no data. "
            "Score forced to 50 (neutral). Do NOT interpret as bearish."
        )
    elif data_quality == "SNAPSHOT_ONLY":
        # Only quarterly snapshot — no flow signal; score the snapshot nudge
        # around the neutral midpoint
        normalised = 50 + yf_nudge
        data_unavailable_note = (
            "NSE real-time data unavailable. Score based solely on "
            "yfinance quarterly institutional ownership snapshot."
        )
    else:
        normalised = round((raw_score + 57.5) / 115 * 100)

    total_score = max(0, min(100, normalised))

    # ── 5. Danger / opportunity signals ───────────────────────────────────────
    danger_signals = _detect_signals(rows_5d, rows_10d, bulk_deals, promoter_pledging)

    # ── 6. Signal ─────────────────────────────────────────────────────────────
    has_critical_danger = any(
        s["type"] == "CRITICAL_DANGER" for s in danger_signals
    )
    has_critical_oppty = any(
        s["type"] == "CRITICAL_OPPORTUNITY" for s in danger_signals
    )

    # When no real-time flow data was available, emit NO_DATA so the
    # synthesiser writes "insufficient data" — not "weak institutional signal".
    # Critical danger/opportunity override even with missing data (they use
    # promoter pledging or historical signals that don't need live NSE).
    if data_quality == "NO_DATA" and not has_critical_danger and not has_critical_oppty:
        signal = "NO_DATA"
    elif has_critical_danger:
        signal = "SELL"
    elif has_critical_oppty or total_score >= 72:
        signal = "STRONG_BUY"
    elif total_score >= 55:
        signal = "BUY"
    elif total_score >= 40:
        signal = "HOLD"
    elif total_score >= 25:
        signal = "AVOID"
    else:
        signal = "SELL"

    # ── 7. Build detail ───────────────────────────────────────────────────────
    buy_streak  = _consecutive_direction(rows_5d, "fii_net", "buy")
    sell_streak = _consecutive_direction(rows_5d, "fii_net", "sell")
    dii_buy_streak = _consecutive_direction(rows_5d, "dii_net", "buy")

    mf_deals = [d for d in bulk_deals if d["is_mf"]]

    detail = {
        "fii": {
            "score":         fii_score,
            "net_5d_cr":     fii_net_5d,
            "net_10d_cr":    fii_net_10d,
            "buy_streak":    buy_streak,
            "sell_streak":   sell_streak,
            "sessions_used": len(rows_5d),
            "notes":         fii_note,
        },
        "dii": {
            "score":         dii_score,
            "net_5d_cr":     dii_net_5d,
            "net_10d_cr":    dii_net_10d,
            "buy_streak":    dii_buy_streak,
            "notes":         dii_note,
        },
        "bulk_deals": {
            "score":         bulk_score,
            "total_deals":   len(bulk_deals),
            "mf_deals":      len(mf_deals),
            "buy_value_cr":  round(sum(d["value_cr"] for d in bulk_deals if d["side"] == "BUY"), 2),
            "sell_value_cr": round(sum(d["value_cr"] for d in bulk_deals if d["side"] == "SELL"), 2),
            "notes":         bulk_note,
        },
        "institutional_snapshot": {
            # Populated only when NSE bulk-deal API is unavailable;
            # quarterly data from yfinance — different semantic to daily flows
            "pct_institutions": yf_inst.get("pct_institutions"),
            "top_holders":      yf_inst.get("top_holders", []),
            "notes":            yf_nudge_note,
            "nudge_score":      yf_nudge,
        } if yf_inst else None,
        "raw_score":        raw_score,
        "sessions_history": len(rows_10d),
    }

    result = {
        "signal":                signal,
        "score":                 total_score,
        "detail":                detail,
        "fii_net_5d":            fii_net_5d,
        "dii_net_5d":            dii_net_5d,
        "bulk_deals":            bulk_deals,
        "danger_signals":        danger_signals,
        "data_sources":          list(dict.fromkeys(data_sources)),
        # ── Data quality metadata ─────────────────────────────────────────────
        # Synthesiser MUST use these fields to avoid misreading data gaps.
        #   FULL           — NSE FII/DII + bulk deals both available
        #   PARTIAL        — FII/DII available, bulk-deal API blocked
        #   SNAPSHOT_ONLY  — only yfinance quarterly ownership snapshot
        #   NO_DATA        — nothing available; score = 50 (neutral by design)
        "data_quality":          data_quality,
        "data_unavailable_note": data_unavailable_note,
        "agent_name":            AGENT_NAME,
        # ── Today's individual live values (for DB writer) ────────────────────
        # Exposed so the orchestrator can call _save_institutional_flows() once
        # per run using these values rather than the 5-day aggregates above.
        "today_fii_net":  live["fii_net"]        if live else None,
        "today_dii_net":  live["dii_net"]        if live else None,
        "today_fii_buy":  live.get("fii_buy")    if live else None,
        "today_fii_sell": live.get("fii_sell")   if live else None,
        "today_date":     live.get("date", date.today().isoformat()) if live else date.today().isoformat(),
    }

    try:
        _write_agent_performance()
    except Exception as exc:
        log.warning("Persisting agent run failed (non-critical): %s", exc)

    return result


# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    sym = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    print(f"\nAnalysing institutional flows for {sym} …\n")
    out = analyse(sym)
    display = {k: v for k, v in out.items() if k != "bulk_deals"}
    display["bulk_deals_count"] = len(out["bulk_deals"])
    print(json.dumps(display, indent=2, default=str))
