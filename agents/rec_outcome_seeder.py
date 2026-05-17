"""
agents/rec_outcome_seeder.py — Recommendation Outcome Seeder (P5-C)
====================================================================
One-time + incremental backfill that seeds PENDING rows in
recommendation_outcomes for every recommendation in the recommendations
table that has no matching outcome row yet.

The outcome tracker (agents/outcome_tracker.py) resolves PENDING rows at
t+90/180/365 days.  This seeder ensures every historical rec enters the
pipeline so the track record starts accumulating from day-1.

Usage
-----
  # Dry run — print what would be seeded, no DB writes
  python -m agents.rec_outcome_seeder

  # Live run — seed PENDING rows for all un-tracked recs
  python -m agents.rec_outcome_seeder --run

  # Also immediately resolve any horizons that have already passed
  python -m agents.rec_outcome_seeder --run --resolve-past

  # Report current outcome coverage
  python -m agents.rec_outcome_seeder --report

Called from worker.py once at startup and daily at 06:55 IST (just after
the orchestrator run) to catch any recs the orchestrator saved before the
seeder integration was added.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ─── Supabase helper ─────────────────────────────────────────────────────────

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


# ─── Entry price helper ───────────────────────────────────────────────────────

def _entry_price(rec: dict) -> float | None:
    """
    Best-effort entry price from a recommendation row.
    Uses midpoint of (entry_low, entry_high) when both present,
    falls back to whichever is non-None, then to metadata.price.
    """
    lo = rec.get("entry_low")
    hi = rec.get("entry_high")
    if lo and hi:
        return (float(lo) + float(hi)) / 2
    if lo:
        return float(lo)
    if hi:
        return float(hi)
    # Try metadata snapshot price
    meta = rec.get("metadata") or {}
    if isinstance(meta, dict) and meta.get("price"):
        try:
            return float(meta["price"])
        except (ValueError, TypeError):
            pass
    return None


# ─── Main seeder ──────────────────────────────────────────────────────────────

def run_seeder(dry_run: bool = True, resolve_past: bool = False) -> dict:
    """
    Seed PENDING rows for all recommendations without a matching
    recommendation_outcomes row.

    Args:
        dry_run:      If True, print what would happen but write nothing.
        resolve_past: If True, also run the outcome tracker immediately for
                      any newly-seeded rows whose horizons are already past.

    Returns:
        {seeded: int, skipped: int, errors: list[str]}
    """
    log.info("=== Rec Outcome Seeder (dry_run=%s, resolve_past=%s) ===",
             dry_run, resolve_past)

    client = _supabase()
    if client is None:
        log.error("Supabase not configured — cannot run seeder")
        return {"seeded": 0, "skipped": 0, "errors": ["Supabase not configured"]}

    # ── 1. Fetch all recommendations ─────────────────────────────────────────
    try:
        recs = (
            client.table("recommendations")
            .select("id,symbol,action,entry_low,entry_high,confidence,"
                    "created_at,agent_signals,gov_check,metadata,upside_pct")
            .order("created_at", desc=False)
            .execute()
            .data or []
        )
    except Exception as exc:
        log.error("Failed to fetch recommendations: %s", exc)
        return {"seeded": 0, "skipped": 0, "errors": [str(exc)]}

    log.info("Found %d total recommendations", len(recs))
    if not recs:
        return {"seeded": 0, "skipped": 0, "errors": []}

    # ── 2. Fetch existing outcome rec_ids ────────────────────────────────────
    try:
        existing_rows = (
            client.table("recommendation_outcomes")
            .select("rec_id")
            .execute()
            .data or []
        )
        existing_ids: set[str] = {str(r["rec_id"]) for r in existing_rows if r.get("rec_id")}
    except Exception as exc:
        log.warning("Could not fetch existing outcome rows: %s — will seed all", exc)
        existing_ids = set()

    log.info("Existing outcome rows: %d | To process: %d",
             len(existing_ids), len(recs) - len(existing_ids))

    # ── 3. Seed PENDING rows ─────────────────────────────────────────────────
    from agents.outcome_tracker import seed_pending_outcome, _fetch_price_on_date, NIFTY_SYMBOL

    seeded = 0
    skipped = 0
    errors: list[str] = []
    newly_seeded_ids: list[str] = []

    for rec in recs:
        rec_id  = str(rec.get("id", ""))
        symbol  = rec.get("symbol", "UNKNOWN")
        action  = rec.get("action", "BUY")

        if rec_id in existing_ids:
            log.debug("[%s] already has outcome row — skipping", symbol)
            skipped += 1
            continue

        created_at_str = rec.get("created_at") or ""
        try:
            rec_date = date.fromisoformat(created_at_str[:10])
        except (ValueError, TypeError):
            log.warning("[%s] invalid created_at '%s' — skipping", symbol, created_at_str)
            errors.append(f"{symbol}: invalid created_at '{created_at_str}'")
            skipped += 1
            continue

        entry_px = _entry_price(rec)

        # Extract composite_score from gov_check
        gov = rec.get("gov_check") or {}
        composite_score = None
        if isinstance(gov, dict):
            val = gov.get("validation") or {}
            composite_score = val.get("composite_score") or val.get("weighted_score")

        # Extract validation_kappa
        val_kappa = None
        if isinstance(gov, dict):
            val_kappa = (gov.get("validation") or {}).get("aggregate_kappa")

        if dry_run:
            log.info(
                "[DRY RUN] Would seed: %s %s rec_date=%s entry=%.2f",
                symbol, action, rec_date,
                entry_px or 0,
            )
            seeded += 1
            continue

        try:
            ok = seed_pending_outcome(
                client           = client,
                rec_id           = rec_id,
                symbol           = symbol,
                action           = action,
                entry_price      = entry_px,
                rec_date         = rec_date,
                composite_score  = composite_score,
                agent_signals    = rec.get("agent_signals"),
                validation_kappa = val_kappa,
            )
            if ok:
                seeded += 1
                newly_seeded_ids.append(rec_id)
                log.info("[%s] seeded PENDING row (rec_id=%s rec_date=%s)",
                         symbol, rec_id, rec_date)
            else:
                errors.append(f"{symbol}: seed_pending_outcome returned False")
                skipped += 1
        except Exception as exc:
            log.error("[%s] seed error: %s", symbol, exc)
            errors.append(f"{symbol}: {exc}")
            skipped += 1

    log.info("=== Seeder done: seeded=%d skipped=%d errors=%d ===",
             seeded, skipped, len(errors))

    # ── 4. Optionally resolve past horizons ───────────────────────────────────
    if resolve_past and newly_seeded_ids and not dry_run:
        log.info("Resolving past horizons for %d newly seeded rows ...",
                 len(newly_seeded_ids))
        from agents.outcome_tracker import run_outcome_tracking
        track_result = run_outcome_tracking(dry_run=False)
        log.info("Outcome tracker: %s", track_result)

    return {"seeded": seeded, "skipped": skipped, "errors": errors}


# ─── Coverage report ─────────────────────────────────────────────────────────

def print_coverage_report() -> None:
    """Print how many recs have outcome rows vs total."""
    client = _supabase()
    if client is None:
        print("Supabase not configured")
        return

    try:
        total_recs = len(
            client.table("recommendations").select("id").execute().data or []
        )
        outcome_rows = client.table("recommendation_outcomes").select(
            "rec_id,symbol,action,rec_date,outcome_t90,outcome_t180,alpha_t90"
        ).execute().data or []
    except Exception as exc:
        print(f"Error: {exc}")
        return

    seeded = len(outcome_rows)
    unseeded = total_recs - seeded

    print(f"\n{'='*55}")
    print(f"  RECOMMENDATION OUTCOME COVERAGE")
    print(f"{'='*55}")
    print(f"  Total recommendations : {total_recs}")
    print(f"  Seeded into tracker   : {seeded}")
    print(f"  Untracked (backfill)  : {unseeded}")
    print()

    if not outcome_rows:
        print("  No outcome rows yet. Run: python -m agents.rec_outcome_seeder --run")
        print(f"{'='*55}")
        return

    pending_90  = sum(1 for r in outcome_rows if r.get("outcome_t90") in (None, "PENDING"))
    resolved_90 = sum(1 for r in outcome_rows if r.get("outcome_t90") not in (None, "PENDING"))
    hits_90     = sum(1 for r in outcome_rows if r.get("outcome_t90") == "HIT")
    misses_90   = sum(1 for r in outcome_rows if r.get("outcome_t90") == "MISS")
    alpha_vals  = [float(r["alpha_t90"]) for r in outcome_rows if r.get("alpha_t90") is not None]

    print(f"  Resolved at 90d       : {resolved_90}")
    print(f"  Still PENDING (90d)   : {pending_90}")
    if resolved_90 > 0:
        hit_rate = hits_90 / resolved_90 * 100
        print(f"  Hit rate (90d)        : {hit_rate:.1f}%  ({hits_90} HIT, {misses_90} MISS)")
    if alpha_vals:
        avg_alpha = sum(alpha_vals) / len(alpha_vals) * 100
        print(f"  Avg alpha (90d)       : {avg_alpha:+.2f}% vs NIFTY 50")

    today = date.today()
    print()
    print(f"  Next 90d resolutions  : ", end="")
    upcoming = [
        r for r in outcome_rows
        if r.get("outcome_t90") in (None, "PENDING") and r.get("rec_date")
    ]
    if upcoming:
        soonest = min(
            date.fromisoformat(r["rec_date"][:10]) + timedelta(days=90)
            for r in upcoming
            if r.get("rec_date")
        )
        days_away = (soonest - today).days
        print(f"{soonest} ({days_away} days away)")
    else:
        print("none pending")

    print(f"{'='*55}")


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import logging as _logging
    from dotenv import load_dotenv
    load_dotenv()
    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Bharat Intelligence — Recommendation Outcome Seeder (P5-C)"
    )
    parser.add_argument("--run",          action="store_true",
                        help="Live run — write PENDING rows to DB (default: dry-run)")
    parser.add_argument("--resolve-past", action="store_true",
                        help="Also run outcome tracker after seeding to resolve past horizons")
    parser.add_argument("--report",       action="store_true",
                        help="Print coverage report and exit")
    args = parser.parse_args()

    if args.report:
        print_coverage_report()
        sys.exit(0)

    result = run_seeder(dry_run=not args.run, resolve_past=args.resolve_past)
    print(f"\nResult: seeded={result['seeded']} skipped={result['skipped']} errors={len(result['errors'])}")
    if result["errors"]:
        print("Errors:")
        for e in result["errors"][:10]:
            print(f"  - {e}")
