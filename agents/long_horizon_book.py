"""
agents/long_horizon_book.py — paper book for the long-horizon multibagger track (P8-E).

Separate from the 90-day paper portfolio (agents/paper_portfolio.py) on purpose:
different horizon, different exits, different sizing, its own track record.

Sizing — evidence-gated
-----------------------
  BASE        ₹10,000 per pick — always.
  CONVICTION  ₹50,000 for picks scoring ≥ CONVICTION_SCORE — LOCKED until this
              track has ≥ UNLOCK_MIN_RESOLVED picks resolved at 1 year and
              ≥ UNLOCK_HIT_RATE of them beat the Nifty 50.
The 90-day book showed what happens without the gate: its "highest conviction"
band was its worst (mean −7.5%, 11/46 winners). Bigger bets here are earned by
outcomes, not by the score. Until then the dashboard shows how far off the
unlock is.

Exits — built for multi-year holds
----------------------------------
  HARD_STOP      price ≤ entry × (1 − HARD_STOP_PCT). Wide: small/mid caps
                 routinely draw down 25–30% on the way to a multi-bag.
  TRAILING_STOP  once the peak has doubled the entry, the stop trails
                 TRAIL_PCT below the peak (one-way) — protects a big winner
                 without exiting it on normal volatility.
  THESIS_BROKEN  the weekly review (multibagger_screener.review_picks) found
                 the quality no longer durable.
  No time exit: outcomes are recorded at t+180 / t+365 / t+730 days instead.

Daily worker job 16:50 IST: open_positions → update_positions → resolve_outcomes.
CLI: python -m agents.long_horizon_book [--run] [--report]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, timedelta
from typing import Any, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(_ROOT, ".env"))

log = logging.getLogger(__name__)

BASE_ALLOCATION       = 10_000
CONVICTION_ALLOCATION = 50_000
CONVICTION_SCORE      = 85
UNLOCK_MIN_RESOLVED   = 20
UNLOCK_HIT_RATE       = 0.60

HARD_STOP_PCT  = 0.35
TRAIL_TRIGGER  = 2.0     # trailing starts once the peak is 2× entry
TRAIL_PCT      = 0.30

HORIZONS = (180, 365, 730)


# ─────────────────────────────────────────────────────────────────────────────
# Pure rules (unit-tested)
# ─────────────────────────────────────────────────────────────────────────────

def track_record(picks: list[dict]) -> dict:
    """1-year record of this track and whether CONVICTION sizing is unlocked."""
    resolved = [p for p in picks if p.get("alpha_t365") is not None]
    hits = sum(1 for p in resolved if float(p["alpha_t365"]) > 0)
    n = len(resolved)
    rate = hits / n if n else None
    unlocked = n >= UNLOCK_MIN_RESOLVED and rate is not None and rate >= UNLOCK_HIT_RATE
    return {
        "resolved_1y": n, "beat_nifty_1y": hits,
        "hit_rate_1y": round(rate * 100, 1) if rate is not None else None,
        "conviction_unlocked": unlocked,
        "unlock_rule": (f"≥{UNLOCK_MIN_RESOLVED} picks resolved at 1 year with "
                        f"≥{UNLOCK_HIT_RATE * 100:.0f}% beating the Nifty 50"),
        "unlock_progress": f"{n}/{UNLOCK_MIN_RESOLVED} resolved"
                           + (f", {rate * 100:.0f}% beat Nifty" if rate is not None else ""),
    }


def allocation_for(pick_score: float, record: dict) -> tuple[str, float]:
    if record.get("conviction_unlocked") and pick_score >= CONVICTION_SCORE:
        return "CONVICTION", CONVICTION_ALLOCATION
    return "BASE", BASE_ALLOCATION


def next_stop(entry: float, peak: float, current_stop: Optional[float]) -> float:
    """Hard stop, raised to a trailing stop once the peak doubles entry. Never lowers."""
    stop = entry * (1 - HARD_STOP_PCT)
    if peak >= entry * TRAIL_TRIGGER:
        stop = max(stop, peak * (1 - TRAIL_PCT))
    return round(max(stop, float(current_stop or 0)), 2)


def exit_reason(price: float, entry: float, stop: float, pick_status: str) -> Optional[str]:
    if pick_status == "THESIS_BROKEN":
        return "THESIS_BROKEN"
    if price <= stop:
        return "TRAILING_STOP" if stop > entry * (1 - HARD_STOP_PCT) + 0.01 else "HARD_STOP"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# DB-backed jobs
# ─────────────────────────────────────────────────────────────────────────────

def _supabase():
    url, key = os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        return None
    from supabase import create_client
    return create_client(url, key)


def _all(client: Any, table: str, **eq) -> list[dict]:
    q = client.table(table).select("*")
    for k, v in eq.items():
        q = q.eq(k, v)
    return q.execute().data or []


def run_book(dry_run: bool = True, client: Any = None) -> dict:
    """Open positions for new picks, update/close open ones, resolve outcomes."""
    from agents.outcome_tracker import (
        NIFTY_SYMBOL, _batch_current_prices, _fetch_price_on_date, _resolve_yf_symbol,
    )
    from agents.multibagger_screener import is_missing_table

    client = client or _supabase()
    out = {"opened": [], "closed": [], "updated": 0, "resolved": 0, "errors": [], "dry_run": dry_run}
    if not client:
        out["errors"].append("Supabase not configured")
        return out
    try:
        picks = _all(client, "long_horizon_picks")
        positions = _all(client, "long_horizon_positions")
    except Exception as exc:
        if is_missing_table(exc):
            out["errors"].append("migration needed: db/migrations/create_long_horizon.sql")
            return out
        raise

    record = track_record(picks)
    pick_by_id = {p["id"]: p for p in picks}
    open_pos = [p for p in positions if p["status"] == "OPEN"]
    has_position = {p["pick_id"] for p in positions}

    symbols = {p["symbol"] for p in picks} | {p["symbol"] for p in open_pos}
    yf_of = {s: _resolve_yf_symbol(s) for s in symbols}
    prices = _batch_current_prices(sorted(set(yf_of.values()) | {NIFTY_SYMBOL}))
    nifty = prices.get(NIFTY_SYMBOL)
    today = date.today()

    def write(table: str, op: str, payload: dict, row_id: Optional[str] = None):
        if dry_run:
            return
        t = client.table(table)
        (t.insert(payload) if op == "insert" else t.update(payload).eq("id", row_id)).execute()

    # 1. Open a position for every ACTIVE pick that has none.
    for p in picks:
        if p["status"] != "ACTIVE" or p["id"] in has_position:
            continue
        price = prices.get(yf_of[p["symbol"]]) or p.get("entry_price")
        if not price:
            continue
        tier, alloc = allocation_for(float(p["score"]), record)
        pos = {
            "pick_id": p["id"], "symbol": p["symbol"], "yf_symbol": yf_of[p["symbol"]],
            "entry_date": today.isoformat(), "entry_price": round(price, 2),
            "quantity": round(alloc / price, 4), "allocation_inr": alloc, "tier": tier,
            "nifty_entry": nifty, "current_price": round(price, 2), "peak_price": round(price, 2),
            "stop_price": next_stop(price, price, None), "unrealized_pnl_pct": 0.0, "status": "OPEN",
        }
        try:
            write("long_horizon_positions", "insert", pos)
            if not p.get("nifty_entry") or not p.get("entry_price"):
                write("long_horizon_picks", "update",
                      {"entry_price": p.get("entry_price") or round(price, 2), "nifty_entry": nifty}, p["id"])
            out["opened"].append({"symbol": p["symbol"], "tier": tier, "allocation": alloc, "entry": round(price, 2)})
        except Exception as exc:
            out["errors"].append(f"open {p['symbol']}: {exc}")

    # 2. Update open positions; close on stop or broken thesis.
    for pos in open_pos:
        price = prices.get(yf_of.get(pos["symbol"]) or pos.get("yf_symbol"))
        if not price:
            continue
        entry = float(pos["entry_price"])
        peak = max(float(pos.get("peak_price") or entry), price)
        stop = next_stop(entry, peak, pos.get("stop_price"))
        pick = pick_by_id.get(pos["pick_id"]) or {}
        reason = exit_reason(price, entry, stop, pick.get("status", "ACTIVE"))
        upd = {"current_price": round(price, 2), "peak_price": round(peak, 2), "stop_price": stop,
               "unrealized_pnl_pct": round((price / entry - 1) * 100, 2)}
        if reason:
            ret = price / entry - 1
            n_ret = (nifty / float(pos["nifty_entry"]) - 1) if (nifty and pos.get("nifty_entry")) else None
            upd.update(status="CLOSED", exit_date=today.isoformat(), exit_price=round(price, 2),
                       exit_reason=reason, realized_pnl_pct=round(ret * 100, 2),
                       alpha_pct=round((ret - n_ret) * 100, 2) if n_ret is not None else None)
            out["closed"].append({"symbol": pos["symbol"], "reason": reason, "return_pct": round(ret * 100, 1)})
            if pick.get("status") == "ACTIVE":
                write("long_horizon_picks", "update",
                      {"status": "EXITED", "status_reason": f"paper position closed: {reason}"}, pick["id"])
        try:
            write("long_horizon_positions", "update", upd, pos["id"])
            out["updated"] += 1
        except Exception as exc:
            out["errors"].append(f"update {pos['symbol']}: {exc}")

    # 3. Resolve t+180 / t+365 / t+730 outcomes vs the Nifty.
    for p in picks:
        if not p.get("entry_price") or not p.get("nifty_entry"):
            continue
        pick_day = date.fromisoformat(str(p["pick_date"])[:10])
        upd = {}
        for h in HORIZONS:
            if p.get(f"price_t{h}") is not None or today < pick_day + timedelta(days=h):
                continue
            when = pick_day + timedelta(days=h)
            px = _fetch_price_on_date(yf_of.get(p["symbol"]) or _resolve_yf_symbol(p["symbol"]), when)
            nx = _fetch_price_on_date(NIFTY_SYMBOL, when)
            if px and nx:
                alpha = (px / float(p["entry_price"]) - 1) - (nx / float(p["nifty_entry"]) - 1)
                upd.update({f"price_t{h}": round(px, 2), f"nifty_t{h}": round(nx, 2), f"alpha_t{h}": round(alpha, 6)})
        if upd:
            try:
                write("long_horizon_picks", "update", upd, p["id"])
                out["resolved"] += 1
            except Exception as exc:
                out["errors"].append(f"resolve {p['symbol']}: {exc}")

    out["track_record"] = record
    log.info("long-horizon book%s — opened=%d closed=%d updated=%d resolved=%d errors=%d unlock=%s",
             " (DRY RUN)" if dry_run else "", len(out["opened"]), len(out["closed"]), out["updated"],
             out["resolved"], len(out["errors"]), record["unlock_progress"])
    return out


def get_summary(client: Any = None) -> dict:
    """Everything the dashboard panel needs. Flags migration_needed instead of raising."""
    from agents.multibagger_screener import is_missing_table, PICK_THRESHOLD
    client = client or _supabase()
    if not client:
        return {"migration_needed": False, "picks": [], "positions": [], "error": "Supabase not configured"}
    try:
        picks = (client.table("long_horizon_picks").select("*")
                 .order("pick_date", desc=True).limit(500).execute().data or [])
        positions = (client.table("long_horizon_positions").select("*")
                     .order("entry_date", desc=True).limit(500).execute().data or [])
    except Exception as exc:
        if is_missing_table(exc):
            return {"migration_needed": True, "picks": [], "positions": [],
                    "migration": "db/migrations/create_long_horizon.sql"}
        raise

    open_pos = [p for p in positions if p["status"] == "OPEN"]
    closed = [p for p in positions if p["status"] == "CLOSED"]
    invested = sum(float(p["allocation_inr"]) for p in open_pos)
    value = sum(float(p["quantity"]) * float(p.get("current_price") or p["entry_price"]) for p in open_pos)
    realized = sum(float(p["allocation_inr"]) * float(p.get("realized_pnl_pct") or 0) / 100 for p in closed)
    deployed = sum(float(p["allocation_inr"]) for p in positions)
    return {
        "migration_needed": False,
        "picks": picks,
        "positions": positions,
        "track_record": track_record(picks),
        "rules": {
            "pick_threshold": PICK_THRESHOLD, "base_allocation": BASE_ALLOCATION,
            "conviction_allocation": CONVICTION_ALLOCATION, "conviction_score": CONVICTION_SCORE,
            "hard_stop_pct": HARD_STOP_PCT * 100, "trail_trigger_x": TRAIL_TRIGGER, "trail_pct": TRAIL_PCT * 100,
        },
        "summary": {
            "active_picks": sum(1 for p in picks if p["status"] == "ACTIVE"),
            "open_positions": len(open_pos), "closed_positions": len(closed),
            "invested_open": round(invested, 2), "value_open": round(value, 2),
            "unrealized_pnl": round(value - invested, 2), "realized_pnl": round(realized, 2),
            # Total P&L over ALL capital ever deployed — realised and unrealised
            # (the 90-day book's total_pnl_pct counted only open positions).
            "total_pnl_pct": round((value - invested + realized) / deployed * 100, 2) if deployed else 0.0,
            "best_multiple": round(max((float(p.get("peak_price") or 0) / float(p["entry_price"])
                                        for p in positions), default=0), 2),
        },
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser(description="Long-horizon multibagger paper book")
    ap.add_argument("--run", action="store_true", help="write changes (default: dry run)")
    ap.add_argument("--report", action="store_true", help="print the summary")
    a = ap.parse_args()
    if a.report:
        import json
        s = get_summary()
        print(json.dumps({"migration_needed": True} if s.get("migration_needed")
                         else {**s["summary"], **s["track_record"]}, indent=2, default=str))
    else:
        print(run_book(dry_run=not a.run))
