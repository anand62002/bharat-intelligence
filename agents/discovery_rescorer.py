"""
agents/discovery_rescorer.py — daily re-check of every live discovery call.

Why this exists
---------------
A discovery call used to be written once and then left alone for 6–9 months:
`valid_till` was set to created + 180/270 days, nothing ever re-examined it, and
the screener only revisits a symbol every ~12 days (2,376-symbol universe, 200 a
day). Each revisit inside a 10-day cooldown updated the row; outside it, a NEW row
was inserted. By 2026-11-10 there were 233 "valid" discovery rows — INFY ×12,
ITC ×7 — and because GET /api/discovery took the top 50 by upside, May's +762%
SPARC outranked every find made since, so the dashboard looked frozen.

What it does (worker job, 16:45 IST — after the 16:30 forward poller)
-------------------------------------------------------------------
  1. Collapse duplicates: one live call per symbol (the most recently confirmed);
     older rows are marked SUPERSEDED and expired. Their outcome rows are left
     alone — outcome tracking is keyed on rec_id, not valid_till.
  2. Price every live call in one batch download.
  3. Close calls that hit their stoploss (STOPPED_OUT) or target (TARGET_HIT).
  4. Re-run the fundamental + technical agents and ask "would this still pass
     discovery today?" Yes → ACTIVE, valid for another CALL_HORIZON_DAYS.
     No → WEAKENED: stays visible with the reason, but is not extended, so it
     ages out unless the screener re-confirms it.
  5. Recompute confidence as the calibrated historical hit rate for the call's
     signal band (agents/call_calibration.py) — so confidence on old calls moves
     every day as both the stock and the track record change.

Reversible: the raw score is preserved in metadata.signal_score and the old
expiry in metadata.prev_valid_till before anything is overwritten.

CLI:
    python -m agents.discovery_rescorer            # dry run, prints the plan
    python -m agents.discovery_rescorer --run      # write changes
    python -m agents.discovery_rescorer --run --no-agents   # price/status only
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(_ROOT, ".env"))

from agents.call_calibration import (  # noqa: E402
    CALL_HORIZON_DAYS, Calibration, load_calibration, signal_score_of,
)

log = logging.getLogger(__name__)

ACTIVE, WEAKENED = "ACTIVE", "WEAKENED"
STOPPED_OUT, TARGET_HIT, SUPERSEDED = "STOPPED_OUT", "TARGET_HIT", "SUPERSEDED"
CLOSED_STATUSES = {STOPPED_OUT, TARGET_HIT, SUPERSEDED}

# Measured from the ORIGINAL call price. Re-confirmations used to rewrite the
# stoploss from that day's price, so a call could fall 31% (SWIGGY) without ever
# touching a stop that kept sliding down behind it. 15% matches the paper
# portfolio's own stop (paper_portfolio.DEFAULT_STOPLOSS_PCT).
MAX_LOSS_FROM_CALL = 0.15


# ─────────────────────────────────────────────────────────────────────────────
# Pure helpers (unit-tested without a database)
# ─────────────────────────────────────────────────────────────────────────────

def _confirmed_on(row: dict) -> str:
    meta = row.get("metadata") or {}
    return str(meta.get("last_confirmed_at") or row.get("created_at") or "")[:10]


def pick_primary(rows: list[dict]) -> tuple[dict, list[dict]]:
    """Most recently confirmed row is the live call; the rest are duplicates."""
    ordered = sorted(rows, key=lambda r: (_confirmed_on(r), str(r.get("created_at") or "")), reverse=True)
    return ordered[0], ordered[1:]


def entry_price_of(row: dict, outcome_entry: Optional[float]) -> Optional[float]:
    """Price the call was made at: tracked outcome entry → call_price → entry band → snapshot."""
    if outcome_entry:
        return float(outcome_entry)
    meta = row.get("metadata") or {}
    if meta.get("call_price"):
        return float(meta["call_price"])
    lo, hi = row.get("entry_low"), row.get("entry_high")
    if lo and hi:
        return (float(lo) + float(hi)) / 2
    return float(meta["price"]) if meta.get("price") else None


def effective_levels(entry: Optional[float], stoploss: Optional[float],
                     target: Optional[float], upside_pct: Optional[float] = None,
                     ) -> tuple[Optional[float], Optional[float]]:
    """
    Stop and target anchored to the call price: the stop is never looser than
    MAX_LOSS_FROM_CALL below entry, the target never beyond _UPSIDE_CAP above it
    (older rows were saved with uncapped targets). Rows saved before targets
    were stored at all get one derived from their capped upside.
    """
    from agents.discovery_screener import _UPSIDE_CAP
    if not entry:
        return stoploss, target
    floor = entry * (1 - MAX_LOSS_FROM_CALL)
    ceiling = entry * (1 + _UPSIDE_CAP / 100)
    sl = max(float(stoploss), floor) if stoploss else floor
    if not target and upside_pct:
        target = entry * (1 + min(float(upside_pct), _UPSIDE_CAP) / 100)
    tg = min(float(target), ceiling) if target else None
    return round(sl, 2), (round(tg, 2) if tg else None)


def price_status(price: float, stoploss: Optional[float], target: Optional[float]) -> Optional[str]:
    """Closing status from price alone, or None if the call is still open."""
    if stoploss and price <= float(stoploss):
        return STOPPED_OUT
    if target and price >= float(target):
        return TARGET_HIT
    return None


def thesis_check(agent_results: dict) -> tuple[bool, float, float, float, str]:
    """
    Would this symbol still pass discovery today, on fundamental + technical?

    Returns (intact, signal_score, upside_capped, valuation_gap, reason). Uses
    the screener's own thresholds and upside logic so the two cannot diverge.
    """
    from agents.discovery_screener import (
        _best_upside, _STANDARD_CONF, _STANDARD_UPSIDE, _UPSIDE_CAP,
    )
    gap, signal = _best_upside(agent_results)
    upside = min(gap, _UPSIDE_CAP)
    if upside < _STANDARD_UPSIDE:
        return False, signal, upside, gap, f"upside to fair value now {upside:.0f}% (< {_STANDARD_UPSIDE:.0f}%)"
    if signal < _STANDARD_CONF:
        return False, signal, upside, gap, f"signal score now {signal:.0f} (< {_STANDARD_CONF:.0f})"
    return True, signal, upside, gap, "still passes discovery on fundamental + technical"


def tier_for(hit_rate: float, upside_remaining: float) -> str:
    from agents.discovery_screener import _CRITICAL_CALIBRATED_MIN, _CRITICAL_UPSIDE
    if hit_rate >= _CRITICAL_CALIBRATED_MIN and upside_remaining >= _CRITICAL_UPSIDE:
        return "CRITICAL"
    return "STANDARD"


def headline_for(tier: str, symbol: str, count: int, upside: float) -> str:
    prefix = "⚡ CRITICAL" if tier == "CRITICAL" else "✅ STANDARD"
    streak = f" ×{count}" if count > 1 else ""
    return f"{prefix} DISCOVERY{streak}: {symbol} — {upside:.0f}% upside"


# ─────────────────────────────────────────────────────────────────────────────
# Main job
# ─────────────────────────────────────────────────────────────────────────────

def _supabase():
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY not set")
    from supabase import create_client
    return create_client(url, key)


def _load_live_rows(client: Any, today: str) -> list[dict]:
    rows: list[dict] = []
    off = 0
    while True:
        page = (client.table("recommendations").select("*")
                .eq("is_discovery", True).gte("valid_till", today)
                .range(off, off + 999).execute().data or [])
        rows += page
        if len(page) < 1000:
            break
        off += 1000
    return rows


def _load_outcome_entries(client: Any, rec_ids: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for i in range(0, len(rec_ids), 100):
        chunk = rec_ids[i:i + 100]
        for o in (client.table("recommendation_outcomes").select("rec_id,entry_price")
                  .in_("rec_id", chunk).execute().data or []):
            if o.get("entry_price"):
                out[o["rec_id"]] = float(o["entry_price"])
    return out


def _run_thesis_agents(symbol: str) -> dict:
    results: dict = {}
    try:
        from agents.fundamental import analyse as fund_analyse
        results["fundamental"] = fund_analyse(symbol)
    except Exception as exc:
        log.warning("rescore(%s): fundamental failed: %s", symbol, exc)
    try:
        from agents.technical import analyse as tech_analyse
        results["technical"] = tech_analyse(symbol)
    except Exception as exc:
        log.warning("rescore(%s): technical failed: %s", symbol, exc)
    return results


def run_rescore(dry_run: bool = True, rerun_agents: bool = True,
                client: Any = None, calibration: Optional[Calibration] = None) -> dict:
    """Re-check every live discovery call. Returns a summary; never raises on a single symbol."""
    from agents.outcome_tracker import _batch_current_prices, _resolve_yf_symbol

    client = client or _supabase()
    today_d = date.today()
    today = today_d.isoformat()
    expire = (today_d - timedelta(days=1)).isoformat()
    renew = (today_d + timedelta(days=CALL_HORIZON_DAYS)).isoformat()

    rows = _load_live_rows(client, today)
    cal = calibration or load_calibration(client)
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_symbol[r["symbol"]].append(r)

    summary = {"live_rows": len(rows), "symbols": len(by_symbol), "superseded": 0,
               ACTIVE: 0, WEAKENED: 0, STOPPED_OUT: 0, TARGET_HIT: 0,
               "no_price": 0, "errors": [], "calibration": cal.summary(), "dry_run": dry_run,
               "changes": []}
    updates: list[tuple[str, dict]] = []

    primaries: list[dict] = []
    for sym, group in by_symbol.items():
        primary, dups = pick_primary(group)
        primaries.append(primary)
        for d in dups:
            meta = dict(d.get("metadata") or {})
            meta.setdefault("signal_score", signal_score_of(d))
            meta.update(call_status=SUPERSEDED, superseded_by=primary["id"],
                        prev_valid_till=d.get("valid_till"), last_rescored_at=today)
            updates.append((d["id"], {"valid_till": expire, "metadata": meta}))
            summary["superseded"] += 1

    yf_of = {p["symbol"]: _resolve_yf_symbol(p["symbol"]) for p in primaries}
    prices = _batch_current_prices(sorted(set(yf_of.values())))
    entries = _load_outcome_entries(client, [p["id"] for p in primaries])

    for row in primaries:
        sym = row["symbol"]
        try:
            meta = dict(row.get("metadata") or {})
            # Preserve the raw score BEFORE confidence is overwritten (reversibility,
            # and calibration reads it back).
            signal = signal_score_of(row)
            meta.setdefault("signal_score", signal)
            meta.setdefault("prev_valid_till", row.get("valid_till"))

            price = prices.get(yf_of[sym])
            if not price:
                summary["no_price"] += 1
                continue
            entry = entry_price_of(row, entries.get(row["id"]))
            call_date = str(meta.get("call_date") or (meta.get("discovery_dates") or [None])[0]
                            or str(row.get("created_at") or "")[:10])
            meta.update(
                price=round(price, 2),
                call_date=call_date,
                call_price=round(entry, 2) if entry else None,
                return_since_call_pct=round((price / entry - 1) * 100, 2) if entry else None,
                days_active=(today_d - date.fromisoformat(call_date)).days if call_date else None,
                last_rescored_at=today,
            )
            stoploss, target = effective_levels(entry, row.get("stoploss"), row.get("target"),
                                              row.get("upside_pct"))
            update: dict = {}
            if stoploss != row.get("stoploss") or target != row.get("target"):
                meta.setdefault("prev_stoploss", row.get("stoploss"))
                meta.setdefault("prev_target", row.get("target"))
                update.update(stoploss=stoploss, target=target)

            closed = price_status(price, stoploss, target)
            if closed:
                meta.update(call_status=closed,
                            status_reason=(f"price ₹{price:,.2f} at/below stoploss ₹{float(stoploss):,.2f}"
                                           if closed == STOPPED_OUT else
                                           f"price ₹{price:,.2f} reached target ₹{float(target):,.2f}"))
                update["valid_till"] = expire
                summary[closed] += 1
            else:
                intact, reason = True, "price check only (agents not re-run)"
                if rerun_agents:
                    intact, new_signal, _upside, gap, reason = thesis_check(_run_thesis_agents(sym))
                    signal = new_signal
                    meta.update(signal_score=round(new_signal, 2), valuation_gap_pct=round(gap, 2))
                status = ACTIVE if intact else WEAKENED
                meta.update(call_status=status, status_reason=reason)
                if intact and rerun_agents:
                    update["valid_till"] = renew
                    meta["last_confirmed_at"] = today
                summary[status] += 1

            hit_rate = cal.confidence(signal)
            upside_remaining = round((float(target) / price - 1) * 100, 2) if target else (row.get("upside_pct") or 0)
            tier = tier_for(hit_rate, upside_remaining) if not closed else (
                "CRITICAL" if "CRITICAL" in (row.get("headline") or "") else "STANDARD")
            meta.update(calibration_n=cal.band_n(signal), calibration_as_of=cal.as_of,
                        has_track_record=cal.has_history, call_horizon_days=CALL_HORIZON_DAYS,
                        upside_horizon=f"{CALL_HORIZON_DAYS}-day call · re-checked daily")
            update.update(
                confidence=hit_rate, upside_confidence=hit_rate,
                upside_pct=upside_remaining, horizon_days=CALL_HORIZON_DAYS,
                headline=headline_for(tier, sym, int(meta.get("discovery_count") or 1), upside_remaining),
                metadata=meta,
            )
            updates.append((row["id"], update))
            summary["changes"].append({
                "symbol": sym, "status": meta["call_status"], "hit_rate": hit_rate,
                "was_conf": row.get("confidence"), "upside_now": upside_remaining,
                "ret": meta.get("return_since_call_pct"), "reason": meta.get("status_reason"),
            })
        except Exception as exc:
            log.warning("rescore(%s) failed: %s", sym, exc, exc_info=True)
            summary["errors"].append(f"{sym}: {exc}")

    if not dry_run:
        for rec_id, upd in updates:
            try:
                client.table("recommendations").update(upd).eq("id", rec_id).execute()
            except Exception as exc:
                summary["errors"].append(f"update {rec_id}: {exc}")

    log.info("discovery rescore%s — live_rows=%d symbols=%d superseded=%d active=%d weakened=%d "
             "stopped=%d target=%d no_price=%d errors=%d",
             " (DRY RUN)" if dry_run else "", summary["live_rows"], summary["symbols"],
             summary["superseded"], summary[ACTIVE], summary[WEAKENED], summary[STOPPED_OUT],
             summary[TARGET_HIT], summary["no_price"], len(summary["errors"]))
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser(description="Daily re-check of live discovery calls")
    ap.add_argument("--run", action="store_true", help="write changes (default: dry run)")
    ap.add_argument("--no-agents", action="store_true", help="skip re-running agents (price/status only)")
    a = ap.parse_args()
    s = run_rescore(dry_run=not a.run, rerun_agents=not a.no_agents)
    print(f"\nlive rows {s['live_rows']} → {s['symbols']} symbols; superseded {s['superseded']}")
    print(f"ACTIVE {s[ACTIVE]}  WEAKENED {s[WEAKENED]}  STOPPED_OUT {s[STOPPED_OUT]}  "
          f"TARGET_HIT {s[TARGET_HIT]}  no_price {s['no_price']}  errors {len(s['errors'])}")
    for c in sorted(s["changes"], key=lambda c: c["status"]):
        print(f"  {c['symbol']:<15} {c['status']:<11} conf {c['was_conf']}→{c['hit_rate']}  "
              f"upside_now {c['upside_now']}%  since_call {c['ret']}%  — {c['reason']}")
    for e in s["errors"]:
        print("  ERROR", e)
