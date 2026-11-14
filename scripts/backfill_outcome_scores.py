"""
scripts/backfill_outcome_scores.py — fill composite_score / validation_kappa on
existing recommendation_outcomes rows (WP-004, ADR-002).

Why
---
Every resolved outcome had composite_score and validation_kappa NULL: the seeder
read the score from gov_check.validation (where it never was), discovery recs
have no judge panel, and the fact-checker overwrote gov_check before published
recs were saved, dropping the panel's kappa. Without these fields neither the
score nor the gate can be tested against results.

What it does
------------
For each outcome row with a NULL composite_score or validation_kappa, read its
recommendation and derive both with agents.rec_outcome_seeder.outcome_scores()
(the same rule every seeding path now uses). Only NULL fields are filled; a
value already present is never overwritten.

Caveat printed in the report: for discovery rows written before WP-004 the
score comes from metadata.signal_score, which re-confirmations and the daily
re-check refresh. Rows with metadata.last_confirmed_at may therefore carry a
later score than the one the call was made on. --exclude-reconfirmed leaves
those rows NULL instead.

Usage (project root, service_role / sb_secret key in .env):

    python scripts/backfill_outcome_scores.py                       # dry run: counts only
    python scripts/backfill_outcome_scores.py --run                 # backup to logs/, then write
    python scripts/backfill_outcome_scores.py --run --exclude-reconfirmed

Rollback: restore from the JSON backup written to logs/ before any write
(each entry holds the row id and its previous composite_score/validation_kappa).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.rec_outcome_seeder import outcome_scores  # noqa: E402

_PAGE = 1000
_IN_CHUNK = 100


# ── pure planning (unit-tested) ──────────────────────────────────────────────

def _reconfirmed(rec: dict) -> bool:
    meta = rec.get("metadata") if isinstance(rec.get("metadata"), dict) else {}
    return bool(rec.get("is_discovery")) and "call_signal_score" not in meta \
        and bool(meta.get("last_confirmed_at"))


def plan_updates(outcomes: list[dict], recs_by_id: dict[str, dict],
                 exclude_reconfirmed: bool = False) -> tuple[list[dict], dict]:
    """
    Returns (updates, stats). Each update: {id, set: {...}, before: {...}}.
    Only fields that are NULL on the outcome row and derivable from the rec are set.
    """
    stats = {"outcome_rows": len(outcomes), "needs_fill": 0, "no_rec": 0,
             "nothing_derivable": 0, "fill_score": 0, "fill_kappa": 0,
             "score_possibly_after_call": 0, "excluded_reconfirmed": 0}
    updates: list[dict] = []
    for o in outcomes:
        need_score = o.get("composite_score") is None
        need_kappa = o.get("validation_kappa") is None
        if not (need_score or need_kappa):
            continue
        stats["needs_fill"] += 1
        rec = recs_by_id.get(str(o.get("rec_id")))
        if rec is None:
            stats["no_rec"] += 1
            continue
        score, kappa = outcome_scores(rec)
        new: dict = {}
        if need_score and score is not None:
            if _reconfirmed(rec):
                if exclude_reconfirmed:
                    stats["excluded_reconfirmed"] += 1
                else:
                    stats["score_possibly_after_call"] += 1
                    new["composite_score"] = score
            else:
                new["composite_score"] = score
        if need_kappa and kappa is not None:
            new["validation_kappa"] = kappa
        if not new:
            stats["nothing_derivable"] += 1
            continue
        stats["fill_score"] += "composite_score" in new
        stats["fill_kappa"] += "validation_kappa" in new
        updates.append({
            "id": o["id"],
            "set": new,
            "before": {"composite_score": o.get("composite_score"),
                       "validation_kappa": o.get("validation_kappa")},
        })
    return updates, stats


# ── database ─────────────────────────────────────────────────────────────────

def _client():
    try:
        from dotenv import load_dotenv
        load_dotenv(_ROOT / ".env", override=True)
    except Exception:
        pass
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not (url and key):
        print("FAIL: SUPABASE_URL / SUPABASE_SERVICE_KEY not set")
        sys.exit(2)
    if key.startswith("sb_publishable_"):
        print("WARNING: publishable/anon key — RLS will hide rows; counts will read 0.")
    from supabase import create_client
    return create_client(url, key)


def fetch_outcomes(client) -> list[dict]:
    rows, start = [], 0
    while True:
        page = (client.table("recommendation_outcomes")
                .select("id,rec_id,composite_score,validation_kappa")
                .range(start, start + _PAGE - 1).execute().data or [])
        rows += page
        if len(page) < _PAGE:
            return rows
        start += _PAGE


def fetch_recs(client, rec_ids: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(rec_ids), _IN_CHUNK):
        chunk = rec_ids[i:i + _IN_CHUNK]
        for r in (client.table("recommendations")
                  .select("id,is_discovery,metadata,gov_check")
                  .in_("id", chunk).execute().data or []):
            out[str(r["id"])] = r
    return out


def write_backup(updates: list[dict], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"outcome_scores_backup_{datetime.now():%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps(
        [{"id": u["id"], **u["before"]} for u in updates], indent=1), encoding="utf-8")
    return path


def apply_updates(client, updates: list[dict]) -> tuple[int, list[str]]:
    done, errors = 0, []
    for u in updates:
        try:
            # .is_ guards make the write fill-only even if the row changed since planning
            q = client.table("recommendation_outcomes").update(u["set"]).eq("id", u["id"])
            for col in u["set"]:
                q = q.is_(col, "null")
            q.execute()
            done += 1
        except Exception as exc:
            errors.append(f"{u['id']}: {exc}")
    return done, errors


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Backfill outcome composite_score / validation_kappa (WP-004)")
    ap.add_argument("--run", action="store_true", help="write (default: dry run)")
    ap.add_argument("--exclude-reconfirmed", action="store_true",
                    help="leave discovery rows whose signal_score may post-date the call NULL")
    args = ap.parse_args(argv)

    client = _client()
    outcomes = fetch_outcomes(client)
    rec_ids = sorted({str(o["rec_id"]) for o in outcomes
                      if o.get("rec_id") and (o.get("composite_score") is None
                                              or o.get("validation_kappa") is None)})
    recs = fetch_recs(client, rec_ids)
    updates, s = plan_updates(outcomes, recs, exclude_reconfirmed=args.exclude_reconfirmed)

    print("=" * 62)
    print(f"OUTCOME SCORE BACKFILL — {'LIVE' if args.run else 'DRY RUN'}")
    print("=" * 62)
    print(f"recommendation_outcomes rows        : {s['outcome_rows']}")
    print(f"  with a NULL score or kappa         : {s['needs_fill']}")
    print(f"  rec not found                      : {s['no_rec']}")
    print(f"  nothing derivable from the rec     : {s['nothing_derivable']}")
    print(f"rows that would be updated           : {len(updates)}")
    print(f"  composite_score filled             : {s['fill_score']}")
    print(f"    of which score may post-date call: {s['score_possibly_after_call']}")
    print(f"    excluded (--exclude-reconfirmed) : {s['excluded_reconfirmed']}")
    print(f"  validation_kappa filled            : {s['fill_kappa']}")

    if not args.run:
        print("-" * 62)
        print("Dry run — nothing written. Re-run with --run to apply.")
        return 0
    if not updates:
        print("Nothing to write.")
        return 0

    backup = write_backup(updates, _ROOT / "logs")
    print(f"backup written                       : {backup}")
    done, errors = apply_updates(client, updates)
    print(f"rows updated                         : {done}")
    for e in errors[:10]:
        print(f"  ERROR {e}")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
