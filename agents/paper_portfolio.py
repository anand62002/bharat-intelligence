"""
agents/paper_portfolio.py — Paper Portfolio Simulation (P5-B)
=============================================================
Simulates a virtual portfolio that auto-follows the system's BUY
recommendations using fixed-INR allocations per position tier.

This is the validation layer before live trading (Phase 7). It lets us
measure whether the agents actually generate alpha before real capital is
deployed.

Allocation per position tier
─────────────────────────────
  Full position  (5%)   → INR 10,000
  Half position  (2.5%) → INR  5,000
  Quarter position (1.25%) → INR 2,500
  Avoid / no tier       → skip (no position opened)
  Unknown / None        → INR 5,000 (conservative default)

Exit rules (checked daily after market close)
──────────────────────────────────────────────
  STOPLOSS    : current_price ≤ stoploss_price (from rec, or entry × 0.85)
  TARGET      : current_price ≥ target_price   (from rec, or entry × 1.40)
  HORIZON     : entry_date + 90 trading-calendar days
  SELL_SIGNAL : a new SELL/AVOID rec for the same symbol appears

India T+1 rule: positions opened today cannot be exited on the same day.

Usage
-----
  python -m agents.paper_portfolio                      # dry run
  python -m agents.paper_portfolio --run                # live run
  python -m agents.paper_portfolio --report             # print summary
  python -m agents.paper_portfolio --run --backfill     # seed historical recs
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import yfinance as yf

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

NIFTY_SYMBOL          = "^NSEI"
DEFAULT_ALLOCATION_INR = 5_000
DEFAULT_STOPLOSS_PCT   = 0.15          # 15% below entry if no stoploss in rec
DEFAULT_TARGET_PCT     = 0.40          # 40% above entry if no target in rec
HORIZON_DAYS           = 90            # calendar days before forced exit
PRICE_WINDOW           = 4             # ±days for historical price lookup

# Map position_label substring → INR allocation
_TIER_ALLOCATIONS: dict[str, float] = {
    "full":    10_000,
    "half":     5_000,
    "quarter":  2_500,
    "avoid":    0,
}


# ─────────────────────────────────────────────────────────────────────────────
# Supabase helper
# ─────────────────────────────────────────────────────────────────────────────

def _supabase():
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_KEY", "")
    if not (url and key):
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception as exc:
        log.warning("Supabase init failed: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Price helpers
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_yf_symbol(symbol: str) -> str:
    if "." in symbol or symbol.startswith("^"):
        return symbol
    return f"{symbol}.NS"


def _fetch_price_on_date(
    yf_symbol: str,
    target_date: date,
    window: int = PRICE_WINDOW,
) -> Optional[float]:
    """Fetch closing price on or nearest to target_date (±window days)."""
    try:
        import pandas as pd
        start = target_date - timedelta(days=window + 5)
        end   = target_date + timedelta(days=window + 5)
        hist  = yf.Ticker(yf_symbol).history(
            start=str(start), end=str(end), auto_adjust=True
        )
        if hist.empty:
            return None
        hist.index = pd.to_datetime(hist.index).normalize()
        mask    = (hist.index >= pd.Timestamp(start)) & (hist.index <= pd.Timestamp(end))
        subset  = hist.loc[mask].copy()
        if subset.empty:
            return None
        subset["delta"] = (subset.index - pd.Timestamp(target_date)).abs()
        closest = subset.sort_values("delta").iloc[0]
        if closest["delta"].days <= window:
            return float(closest["Close"])
        return None
    except Exception as exc:
        log.debug("Price fetch failed for %s on %s: %s", yf_symbol, target_date, exc)
        return None


def _fetch_current_price(yf_symbol: str) -> Optional[float]:
    """Fetch latest available closing price."""
    try:
        hist = yf.Ticker(yf_symbol).history(period="5d", auto_adjust=True)
        if hist.empty:
            return None
        return float(hist["Close"].dropna().iloc[-1])
    except Exception as exc:
        log.debug("Current price fetch failed for %s: %s", yf_symbol, exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Allocation / sizing helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_allocation_inr(position_label: Optional[str]) -> float:
    """Return INR allocation based on position_label from position_sizer."""
    if not position_label:
        return DEFAULT_ALLOCATION_INR
    lower = position_label.lower()
    for tier_key, alloc in _TIER_ALLOCATIONS.items():
        if tier_key in lower:
            return alloc
    return DEFAULT_ALLOCATION_INR


def _entry_price_from_rec(rec: dict) -> Optional[float]:
    """Best-effort entry price: mid(entry_low, entry_high) → either → agent_signals snapshot price."""
    lo = _safe_float(rec.get("entry_low"))
    hi = _safe_float(rec.get("entry_high"))
    if lo and hi:
        return (lo + hi) / 2
    if lo:
        return lo
    if hi:
        return hi
    # Discovery screener stores snapshot price inside agent_signals.discovery.price
    signals = rec.get("agent_signals") or {}
    if isinstance(signals, dict):
        disc = signals.get("discovery") or {}
        if isinstance(disc, dict) and disc.get("price"):
            try:
                return float(disc["price"])
            except (ValueError, TypeError):
                pass
    return None


def _safe_float(v) -> Optional[float]:
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Exit checkers
# ─────────────────────────────────────────────────────────────────────────────

def _is_stoploss_hit(current: float, entry: float, stoploss_price: Optional[float]) -> bool:
    sl = stoploss_price if stoploss_price else entry * (1 - DEFAULT_STOPLOSS_PCT)
    return current <= sl


def _is_target_hit(current: float, entry: float, target_price: Optional[float]) -> bool:
    tp = target_price if target_price else entry * (1 + DEFAULT_TARGET_PCT)
    return current >= tp


def _is_horizon_reached(entry_date: date, today: date = None) -> bool:
    today = today or date.today()
    return (today - entry_date).days >= HORIZON_DAYS


# ─────────────────────────────────────────────────────────────────────────────
# Core operations
# ─────────────────────────────────────────────────────────────────────────────

def open_new_positions(client, dry_run: bool = True) -> dict:
    """
    Open paper positions for BUY recs that don't have a position yet.

    Fetches all BUY recommendations, finds ones without a corresponding
    paper_portfolio_positions row, and opens them.

    Returns: { opened: int, skipped: int, errors: list[str] }
    """
    log.info("=== Paper Portfolio: open_new_positions (dry_run=%s) ===", dry_run)
    today   = date.today()
    errors: list[str] = []
    opened  = 0
    skipped = 0

    # Fetch all BUY recs
    try:
        recs = (
            client.table("recommendations")
            .select("id,symbol,action,entry_low,entry_high,target,stoploss,"
                    "created_at,position_label,agent_signals")
            .in_("action", ["BUY"])
            .order("created_at", desc=False)
            .execute()
            .data or []
        )
    except Exception as exc:
        log.error("Failed to fetch recommendations: %s", exc)
        return {"opened": 0, "skipped": 0, "errors": [str(exc)]}

    if not recs:
        log.info("No BUY recommendations found")
        return {"opened": 0, "skipped": 0, "errors": []}

    # Fetch existing paper position rec_ids
    try:
        existing_rows = (
            client.table("paper_portfolio_positions")
            .select("rec_id")
            .execute()
            .data or []
        )
        existing_rec_ids: set[str] = {
            str(r["rec_id"]) for r in existing_rows if r.get("rec_id")
        }
    except Exception as exc:
        log.warning("Could not fetch existing positions: %s — assuming none", exc)
        existing_rec_ids = set()

    log.info(
        "BUY recs: %d total, %d already have positions, %d to process",
        len(recs), len(existing_rec_ids), len(recs) - len(existing_rec_ids),
    )

    for rec in recs:
        rec_id  = str(rec.get("id", ""))
        symbol  = rec.get("symbol", "UNKNOWN")
        action  = rec.get("action", "BUY")

        if rec_id in existing_rec_ids:
            skipped += 1
            continue

        # Determine INR allocation from position_label
        position_label = rec.get("position_label") or ""
        allocation_inr = _get_allocation_inr(position_label)

        if allocation_inr == 0:
            log.debug("[%s] AVOID tier — skipping paper position", symbol)
            skipped += 1
            continue

        # Entry date
        created_at = rec.get("created_at") or ""
        try:
            entry_date = date.fromisoformat(created_at[:10])
        except (ValueError, TypeError):
            log.warning("[%s] Invalid created_at '%s' — skipping", symbol, created_at)
            skipped += 1
            errors.append(f"{symbol}: invalid created_at '{created_at}'")
            continue

        # Entry price (try rec fields first, fall back to historical price)
        entry_price = _entry_price_from_rec(rec)
        if entry_price is None:
            yf_sym = _resolve_yf_symbol(symbol)
            entry_price = _fetch_price_on_date(yf_sym, entry_date)
        if entry_price is None or entry_price <= 0:
            log.warning("[%s] Could not determine entry price — skipping", symbol)
            skipped += 1
            errors.append(f"{symbol}: no entry price available")
            continue

        entry_price = float(entry_price)
        yf_sym      = _resolve_yf_symbol(symbol)

        # Quantity = floor(allocation / price), minimum 1
        quantity = max(1, int(allocation_inr / entry_price))

        # Stoploss / target from rec
        stoploss_price = _safe_float(rec.get("stoploss"))
        target_price   = _safe_float(rec.get("target"))

        # Nifty at entry
        nifty_entry = _fetch_price_on_date(NIFTY_SYMBOL, entry_date, window=3)

        row = {
            "rec_id":          rec_id,
            "symbol":          symbol,
            "yf_symbol":       yf_sym,
            "action":          action,
            "entry_date":      str(entry_date),
            "entry_price":     round(entry_price, 4),
            "quantity":        quantity,
            "allocation_inr":  round(allocation_inr, 2),
            "position_label":  position_label,
            "stoploss_price":  round(stoploss_price, 4) if stoploss_price else None,
            "target_price":    round(target_price, 4) if target_price else None,
            "nifty_entry":     round(nifty_entry, 2) if nifty_entry else None,
            "status":          "OPEN",
            "current_price":   round(entry_price, 4),
            "current_value":   round(entry_price * quantity, 2),
            "unrealized_pnl":  0.0,
            "unrealized_pnl_pct": 0.0,
        }

        if dry_run:
            log.info(
                "[DRY RUN] Would open: %s %s entry=%.2f qty=%d alloc=₹%.0f tier=%s",
                action, symbol, entry_price, quantity, allocation_inr, position_label or "default",
            )
            opened += 1
            continue

        try:
            client.table("paper_portfolio_positions").insert(row).execute()
            opened += 1
            log.info(
                "[%s] Paper position opened: entry=%.2f qty=%d alloc=₹%.0f %s",
                symbol, entry_price, quantity, allocation_inr, entry_date,
            )
        except Exception as exc:
            log.error("[%s] Insert failed: %s", symbol, exc)
            errors.append(f"{symbol}: {exc}")
            skipped += 1

    log.info(
        "=== open_new_positions done: opened=%d skipped=%d errors=%d ===",
        opened, skipped, len(errors),
    )
    return {"opened": opened, "skipped": skipped, "errors": errors}


def update_open_positions(client, dry_run: bool = True) -> dict:
    """
    Refresh prices for all OPEN positions, check exit conditions, close if triggered.

    Returns: { updated: int, closed: int, errors: list[str],
               closed_details: list[dict] }
    """
    log.info("=== Paper Portfolio: update_open_positions (dry_run=%s) ===", dry_run)
    today  = date.today()
    errors: list[str] = []
    updated = 0
    closed  = 0
    closed_details: list[dict] = []

    # Fetch OPEN positions
    try:
        positions = (
            client.table("paper_portfolio_positions")
            .select("*")
            .eq("status", "OPEN")
            .execute()
            .data or []
        )
    except Exception as exc:
        log.error("Failed to fetch open positions: %s", exc)
        return {"updated": 0, "closed": 0, "errors": [str(exc)], "closed_details": []}

    if not positions:
        log.info("No OPEN positions to update")
        return {"updated": 0, "closed": 0, "errors": [], "closed_details": []}

    log.info("Updating %d OPEN positions", len(positions))

    for pos in positions:
        pos_id       = pos["id"]
        symbol       = pos["symbol"]
        yf_sym       = pos.get("yf_symbol") or _resolve_yf_symbol(symbol)
        entry_price  = float(pos.get("entry_price") or 0)
        quantity     = float(pos.get("quantity") or 1)
        stoploss_price = _safe_float(pos.get("stoploss_price"))
        target_price   = _safe_float(pos.get("target_price"))
        nifty_entry    = _safe_float(pos.get("nifty_entry"))

        try:
            entry_date = date.fromisoformat(str(pos["entry_date"])[:10])
        except (ValueError, TypeError):
            log.warning("[%s] Invalid entry_date '%s' — skipping", symbol, pos.get("entry_date"))
            continue

        # T+1 rule: don't close positions opened today
        if entry_date >= today:
            log.debug("[%s] Entry today — skipping exit checks (T+1 rule)", symbol)
            continue

        # Fetch current price
        current_price = _fetch_current_price(yf_sym)
        if current_price is None:
            log.warning("[%s] Could not fetch current price — skipping update", symbol)
            errors.append(f"{symbol}: current price unavailable")
            continue

        current_value    = current_price * quantity
        unrealized_pnl   = current_value - (entry_price * quantity)
        unrealized_pnl_pct = ((current_price / entry_price) - 1) * 100 if entry_price else 0

        # Check exit conditions
        exit_reason = None
        if _is_stoploss_hit(current_price, entry_price, stoploss_price):
            exit_reason = "STOPLOSS"
        elif _is_target_hit(current_price, entry_price, target_price):
            exit_reason = "TARGET"
        elif _is_horizon_reached(entry_date, today):
            exit_reason = "HORIZON"

        if exit_reason:
            # Close position
            exit_price   = current_price
            nifty_exit   = _fetch_current_price(NIFTY_SYMBOL)
            realized_pnl = (exit_price - entry_price) * quantity
            realized_pnl_pct = ((exit_price / entry_price) - 1) * 100 if entry_price else 0

            # Alpha = stock return - nifty return over same period
            alpha_pct: Optional[float] = None
            if nifty_entry and nifty_exit and nifty_entry > 0:
                nifty_ret_pct = ((nifty_exit / nifty_entry) - 1) * 100
                alpha_pct = realized_pnl_pct - nifty_ret_pct

            close_updates = {
                "status":            "CLOSED",
                "current_price":     round(current_price, 4),
                "current_value":     round(current_value, 2),
                "unrealized_pnl":    0.0,
                "unrealized_pnl_pct": 0.0,
                "exit_date":         str(today),
                "exit_price":        round(exit_price, 4),
                "nifty_exit":        round(nifty_exit, 2) if nifty_exit else None,
                "realized_pnl":      round(realized_pnl, 2),
                "realized_pnl_pct":  round(realized_pnl_pct, 2),
                "alpha_pct":         round(alpha_pct, 2) if alpha_pct is not None else None,
                "exit_reason":       exit_reason,
                "updated_at":        datetime.now(timezone.utc).isoformat(),
            }

            log.info(
                "[%s] CLOSING — reason=%s exit=%.2f pnl=%.1f%% alpha=%s",
                symbol, exit_reason, exit_price, realized_pnl_pct,
                f"{alpha_pct:.1f}%" if alpha_pct is not None else "N/A",
            )

            closed_details.append({
                "symbol": symbol, "exit_reason": exit_reason,
                "realized_pnl": round(realized_pnl, 2),
                "realized_pnl_pct": round(realized_pnl_pct, 2),
                "alpha_pct": round(alpha_pct, 2) if alpha_pct is not None else None,
            })

            if not dry_run:
                try:
                    client.table("paper_portfolio_positions").update(close_updates).eq("id", pos_id).execute()
                    closed += 1
                except Exception as exc:
                    log.error("[%s] Close update failed: %s", symbol, exc)
                    errors.append(f"{symbol}: close failed — {exc}")
            else:
                log.info("[DRY RUN] Would close %s at %.2f (%s)", symbol, exit_price, exit_reason)
                closed += 1

        else:
            # Update price only
            price_updates = {
                "current_price":      round(current_price, 4),
                "current_value":      round(current_value, 2),
                "unrealized_pnl":     round(unrealized_pnl, 2),
                "unrealized_pnl_pct": round(unrealized_pnl_pct, 2),
                "updated_at":         datetime.now(timezone.utc).isoformat(),
            }
            if not dry_run:
                try:
                    client.table("paper_portfolio_positions").update(price_updates).eq("id", pos_id).execute()
                    updated += 1
                except Exception as exc:
                    log.error("[%s] Price update failed: %s", symbol, exc)
                    errors.append(f"{symbol}: price update failed — {exc}")
            else:
                log.debug("[DRY RUN] Would update %s → %.2f (pnl=%.1f%%)", symbol, current_price, unrealized_pnl_pct)
                updated += 1

    log.info(
        "=== update_open_positions done: updated=%d closed=%d errors=%d ===",
        updated, closed, len(errors),
    )
    return {
        "updated": updated, "closed": closed,
        "errors": errors, "closed_details": closed_details,
    }


def save_daily_snapshot(client, dry_run: bool = True) -> dict:
    """
    Compute portfolio-level metrics and upsert today's snapshot row.

    Returns: the snapshot dict.
    """
    log.info("=== Paper Portfolio: save_daily_snapshot (dry_run=%s) ===", dry_run)
    today = date.today()

    try:
        positions = (
            client.table("paper_portfolio_positions")
            .select("status,allocation_inr,current_value,unrealized_pnl,realized_pnl,alpha_pct")
            .execute()
            .data or []
        )
    except Exception as exc:
        log.error("Failed to fetch positions for snapshot: %s", exc)
        return {}

    open_positions   = [p for p in positions if p.get("status") == "OPEN"]
    closed_positions = [p for p in positions if p.get("status") == "CLOSED"]

    total_invested  = sum(_safe_float(p.get("allocation_inr")) or 0 for p in open_positions)
    total_cur_value = sum(_safe_float(p.get("current_value"))  or 0 for p in open_positions)
    unrealized_pnl  = sum(_safe_float(p.get("unrealized_pnl")) or 0 for p in open_positions)
    realized_pnl    = sum(_safe_float(p.get("realized_pnl"))   or 0 for p in closed_positions)
    total_pnl       = unrealized_pnl + realized_pnl
    total_pnl_pct   = (unrealized_pnl / total_invested * 100) if total_invested > 0 else 0.0

    # Benchmark: today's Nifty value
    nifty_value       = _fetch_current_price(NIFTY_SYMBOL)
    nifty_return_pct  = None
    alpha_pct         = None

    # Compute Nifty return since first ever position
    try:
        first_pos = (
            client.table("paper_portfolio_positions")
            .select("entry_date,nifty_entry")
            .order("entry_date", desc=False)
            .limit(1)
            .execute()
            .data or []
        )
        if first_pos and nifty_value:
            nifty_entry_val = _safe_float(first_pos[0].get("nifty_entry"))
            if nifty_entry_val and nifty_entry_val > 0:
                nifty_return_pct = ((nifty_value / nifty_entry_val) - 1) * 100
                alpha_pct = total_pnl_pct - nifty_return_pct
    except Exception:
        pass

    snapshot = {
        "snapshot_date":       str(today),
        "total_invested":      round(total_invested, 2),
        "total_current_value": round(total_cur_value, 2),
        "unrealized_pnl":      round(unrealized_pnl, 2),
        "realized_pnl":        round(realized_pnl, 2),
        "total_pnl":           round(total_pnl, 2),
        "total_pnl_pct":       round(total_pnl_pct, 2),
        "open_positions":      len(open_positions),
        "closed_positions":    len(closed_positions),
        "nifty_value":         round(nifty_value, 2) if nifty_value else None,
        "nifty_return_pct":    round(nifty_return_pct, 2) if nifty_return_pct is not None else None,
        "alpha_pct":           round(alpha_pct, 2) if alpha_pct is not None else None,
    }

    log.info(
        "Snapshot: invested=₹%.0f pnl=₹%.0f (%.1f%%) alpha=%s open=%d closed=%d",
        total_invested, total_pnl, total_pnl_pct,
        f"{alpha_pct:.1f}%" if alpha_pct is not None else "N/A",
        len(open_positions), len(closed_positions),
    )

    if not dry_run:
        try:
            client.table("paper_portfolio_snapshots").upsert(snapshot, on_conflict="snapshot_date").execute()
            log.info("Snapshot saved for %s", today)
        except Exception as exc:
            log.error("Failed to save snapshot: %s", exc)

    return snapshot


def get_portfolio_summary(client) -> dict:
    """
    Return current paper portfolio summary for the API.
    Fetches open positions + latest snapshot + closed P&L stats.
    """
    try:
        open_positions = (
            client.table("paper_portfolio_positions")
            .select("*")
            .eq("status", "OPEN")
            .order("entry_date", desc=True)
            .execute()
            .data or []
        )
    except Exception as exc:
        log.error("Failed to fetch open positions: %s", exc)
        open_positions = []

    try:
        closed_positions = (
            client.table("paper_portfolio_positions")
            .select(
                "symbol,entry_date,exit_date,entry_price,exit_price,"
                "quantity,allocation_inr,position_label,"
                "realized_pnl,realized_pnl_pct,alpha_pct,exit_reason"
            )
            .eq("status", "CLOSED")
            .order("exit_date", desc=True)
            .limit(200)
            .execute()
            .data or []
        )
    except Exception:
        closed_positions = []

    try:
        latest_snapshot = (
            client.table("paper_portfolio_snapshots")
            .select("*")
            .order("snapshot_date", desc=True)
            .limit(1)
            .execute()
            .data or [{}]
        )[0]
    except Exception:
        latest_snapshot = {}

    # Closed stats
    alpha_vals  = [float(p["alpha_pct"]) for p in closed_positions if p.get("alpha_pct") is not None]
    pnl_vals    = [float(p["realized_pnl_pct"]) for p in closed_positions if p.get("realized_pnl_pct") is not None]
    wins        = sum(1 for v in pnl_vals if v > 0)
    win_rate    = round(wins / len(pnl_vals) * 100, 1) if pnl_vals else None
    avg_alpha   = round(sum(alpha_vals) / len(alpha_vals), 2) if alpha_vals else None

    return {
        "open_positions":     open_positions,
        "closed_count":       len(closed_positions),
        "trade_history":      closed_positions,
        "summary":            latest_snapshot,
        "win_rate":           win_rate,
        "avg_alpha_closed":   avg_alpha,
        "closed_count_total": len(closed_positions),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def run_paper_portfolio(dry_run: bool = True, backfill: bool = False) -> dict:
    """
    Main job entry point called by worker.py.

    - open_new_positions: seeds paper positions for any untracked BUY recs
    - update_open_positions: refreshes prices, handles exits
    - save_daily_snapshot: upserts today's portfolio-level metrics

    Set backfill=True on first run to process all historical BUY recs.
    """
    client = _supabase()
    if client is None:
        log.error("Supabase not configured")
        return {"error": "Supabase not configured"}

    log.info("=== Paper Portfolio run (dry_run=%s, backfill=%s) ===", dry_run, backfill)

    open_result   = open_new_positions(client, dry_run=dry_run)
    update_result = update_open_positions(client, dry_run=dry_run)
    snapshot      = save_daily_snapshot(client, dry_run=dry_run) if not dry_run else {}

    return {
        "open_new":   open_result,
        "updates":    update_result,
        "snapshot":   snapshot,
    }


def _print_report() -> None:
    client = _supabase()
    if client is None:
        print("Supabase not configured — set SUPABASE_URL and SUPABASE_SERVICE_KEY")
        return

    summary = get_portfolio_summary(client)
    snap    = summary.get("summary", {})

    print(f"\n{'='*60}")
    print("  BHARAT INTELLIGENCE — PAPER PORTFOLIO SUMMARY")
    print(f"{'='*60}")
    print(f"  Open positions        : {len(summary['open_positions'])}")
    print(f"  Closed positions      : {summary['closed_count']}")
    if snap:
        print(f"  Total invested        : ₹{snap.get('total_invested', 0):,.0f}")
        print(f"  Unrealized P&L        : ₹{snap.get('unrealized_pnl', 0):,.0f}  ({snap.get('total_pnl_pct', 0):.1f}%)")
        print(f"  Realized P&L          : ₹{snap.get('realized_pnl', 0):,.0f}")
        nrp = snap.get("nifty_return_pct")
        alp = snap.get("alpha_pct")
        if nrp is not None:
            print(f"  Nifty return (same period): {nrp:+.1f}%")
        if alp is not None:
            print(f"  Portfolio alpha       : {alp:+.1f}%")
    if summary.get("win_rate") is not None:
        print(f"  Win rate (closed)     : {summary['win_rate']:.1f}%")
    if summary.get("avg_alpha_closed") is not None:
        print(f"  Avg alpha (closed)    : {summary['avg_alpha_closed']:+.2f}%")
    print()
    if summary["open_positions"]:
        print("  Top open positions:")
        for p in summary["open_positions"][:5]:
            pnl = p.get("unrealized_pnl_pct") or 0
            print(f"    {p['symbol']:15s}  entry=₹{p.get('entry_price',0):,.0f}  pnl={pnl:+.1f}%  {p.get('position_label','')}")
    print(f"{'='*60}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import logging as _logging
    from dotenv import load_dotenv
    load_dotenv()

    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    parser = argparse.ArgumentParser(description="Bharat Intelligence — Paper Portfolio (P5-B)")
    parser.add_argument("--run",      action="store_true", help="Live run — write to DB (default: dry run)")
    parser.add_argument("--backfill", action="store_true", help="Process all historical BUY recs")
    parser.add_argument("--report",   action="store_true", help="Print summary and exit")
    args = parser.parse_args()

    if args.report:
        _print_report()
        sys.exit(0)

    result = run_paper_portfolio(dry_run=not args.run, backfill=args.backfill)
    opened = result.get("open_new", {}).get("opened", 0)
    closed = result.get("updates", {}).get("closed", 0)
    upd    = result.get("updates", {}).get("updated", 0)
    print(f"\nDone: opened={opened} updated={upd} closed={closed}")
