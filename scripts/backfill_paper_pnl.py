"""
scripts/backfill_paper_pnl.py — repair paper-portfolio P&L history (2026-11-12).

Two fixes, so the P&L chart uses one definition end to end:

  1. Fill missing nifty_entry / nifty_exit on paper positions (95 of 334 had
     one or both missing), one Nifty lookup per distinct date. Without them the
     same-trades-in-Nifty benchmark covers only part of the book.
  2. Recompute total_pnl_pct / nifty_return_pct / alpha_pct on every stored
     snapshot. total_pnl_pct used to be unrealised P&L / open capital (it left
     out realised losses); alpha compared it with the Nifty since the very first
     position. Each snapshot's own stored unrealized_pnl + realized_pnl are used,
     divided by the capital deployed as of that date — see
     agents/paper_portfolio.compute_pnl_metrics for today's definition.

Old values are written to a JSON backup before anything is changed.

Usage:
    python scripts/backfill_paper_pnl.py            # dry run
    python scripts/backfill_paper_pnl.py --run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(_ROOT, ".env"))

from agents.outcome_tracker import NIFTY_SYMBOL, _fetch_price_on_date  # noqa: E402
from agents.paper_portfolio import _PNL_COLS, _cost, _safe_float, _supabase  # noqa: E402


def _d(v) -> str:
    return str(v or "")[:10]


def snapshot_metrics(snap: dict, positions: list[dict]) -> dict:
    """Corrected P&L % for one historical snapshot, from its own stored totals."""
    day = _d(snap["snapshot_date"])
    live = [p for p in positions if p.get("status") in ("OPEN", "CLOSED") and _d(p.get("entry_date")) <= day]
    deployed = sum(_cost(p) for p in live)
    total = (_safe_float(snap.get("unrealized_pnl")) or 0) + (_safe_float(snap.get("realized_pnl")) or 0)
    out = {"total_pnl_pct": round(total / deployed * 100, 2) if deployed else 0.0,
           "nifty_return_pct": None, "alpha_pct": None}
    nifty_day = _safe_float(snap.get("nifty_value"))
    if not nifty_day or not deployed:
        return out
    bench = cost_m = 0.0
    for p in live:
        n0 = _safe_float(p.get("nifty_entry"))
        closed = p.get("status") == "CLOSED" and _d(p.get("exit_date")) and _d(p.get("exit_date")) <= day
        n1 = _safe_float(p.get("nifty_exit")) if closed else nifty_day
        if n0 and n1:
            c = _cost(p)
            cost_m += c
            bench += c * (n1 / n0 - 1)
    if cost_m:
        out["nifty_return_pct"] = round(bench / cost_m * 100, 2)
        out["alpha_pct"] = round(out["total_pnl_pct"] - out["nifty_return_pct"], 2)
    return out


def _read_positions(client) -> list[dict]:
    rows, off = [], 0
    while True:
        page = (client.table("paper_portfolio_positions").select("id," + _PNL_COLS)
                .order("entry_date").range(off, off + 999).execute().data or [])
        rows += page
        if len(page) < 1000:
            return rows
        off += 1000


def main(run: bool) -> None:
    client = _supabase()
    positions = _read_positions(client)

    # ── 1. Missing Nifty prices — one lookup per distinct date ────────────────
    need: dict[str, float | None] = {}
    for p in positions:
        if not p.get("nifty_entry") and p.get("entry_date"):
            need[_d(p["entry_date"])] = None
        if p.get("status") == "CLOSED" and not p.get("nifty_exit") and p.get("exit_date"):
            need[_d(p["exit_date"])] = None
    for d in sorted(need):
        need[d] = _fetch_price_on_date(NIFTY_SYMBOL, date.fromisoformat(d))

    fills: dict[str, dict] = {}
    for p in positions:
        upd = {}
        if not p.get("nifty_entry") and need.get(_d(p.get("entry_date"))):
            upd["nifty_entry"] = round(need[_d(p["entry_date"])], 2)
        if p.get("status") == "CLOSED" and not p.get("nifty_exit") and need.get(_d(p.get("exit_date"))):
            upd["nifty_exit"] = round(need[_d(p["exit_date"])], 2)
        if upd:
            fills[p["id"]] = upd
    print(f"positions needing Nifty prices: {len(fills)} (date lookups: {len(need)}, "
          f"unresolved: {sum(1 for v in need.values() if not v)})")
    if run:
        for pid, upd in fills.items():
            client.table("paper_portfolio_positions").update(upd).eq("id", pid).execute()
    positions = [{**p, **fills.get(p["id"], {})} for p in positions]

    # ── 2. Recompute historical snapshots ────────────────────────────────────
    snaps = client.table("paper_portfolio_snapshots").select("*").order("snapshot_date").execute().data or []
    backup = os.path.join(_ROOT, "logs", f"paper_snapshots_backup_{datetime.now():%Y%m%d_%H%M%S}.json")
    os.makedirs(os.path.dirname(backup), exist_ok=True)
    with open(backup, "w", encoding="utf-8") as fh:
        json.dump(snaps, fh, default=str, indent=1)
    print(f"backed up {len(snaps)} snapshots → {backup}")

    for s in snaps:
        new = snapshot_metrics(s, positions)
        changed = any(s.get(k) != v for k, v in new.items())
        if changed:
            print(f"  {_d(s['snapshot_date'])}  total_pnl_pct {s.get('total_pnl_pct')} → {new['total_pnl_pct']}  "
                  f"nifty {s.get('nifty_return_pct')} → {new['nifty_return_pct']}  alpha {s.get('alpha_pct')} → {new['alpha_pct']}")
            if run:
                client.table("paper_portfolio_snapshots").update(new).eq("snapshot_date", s["snapshot_date"]).execute()
    print("DONE" if run else "DRY RUN — nothing written")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    main(ap.parse_args().run)
