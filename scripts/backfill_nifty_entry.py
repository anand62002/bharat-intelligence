"""
scripts/backfill_nifty_entry.py — repair NULL nifty_entry in recommendation_outcomes

Why this is needed
------------------
_fetch_price_on_date() built tz-naive pandas Timestamps from rec_date and
compared them against yfinance's tz-AWARE index (Asia/Kolkata for NSE symbols).
That raises

    TypeError: Cannot subtract tz-naive and tz-aware datetime-like objects

which the function's except clause swallowed into a silent None — at DEBUG
level, so nothing surfaced. Every nifty_entry written by seed_pending_outcome
was therefore NULL, and both run_outcome_tracking() and run_forward_polling()
skip such rows on

    if not entry_price or not nifty_entry: continue

so no outcome ever resolved and alpha_live was never populated. The dashboard's
Live Positions, Agent Attribution, Calibration and Best/Worst panels all sit
downstream of that, which is why they were empty.

The code defect is fixed in agents/outcome_tracker.py; this repairs the rows
already written. NIFTY closes are fetched once per distinct rec_date, so 148
rows cost ~30 lookups, not 148.

Usage
-----
    python scripts/backfill_nifty_entry.py            # dry run, shows the plan
    python scripts/backfill_nifty_entry.py --run      # apply

Requires SUPABASE_SERVICE_KEY to be a service_role JWT (an anon key reads 0
rows under RLS and the script will refuse to continue).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(override=True)      # .env wins over a stale OS variable
except Exception:
    pass


def _key_is_privileged(key: str) -> bool:
    if key.startswith("sb_secret_"):
        return True
    if key.startswith("sb_publishable_"):
        return False
    try:
        p = key.split(".")[1]
        p += "=" * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p)).get("role") == "service_role"
    except Exception:
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true", help="apply changes (default: dry run)")
    args = ap.parse_args()
    dry = not args.run

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not (url and key):
        print("FAIL: SUPABASE_URL / SUPABASE_SERVICE_KEY not set")
        return
    if not _key_is_privileged(key):
        print("FAIL: SUPABASE_SERVICE_KEY is not a service_role key.")
        print("      RLS would hide the rows and this would silently do nothing.")
        return

    from supabase import create_client
    from agents.outcome_tracker import _fetch_price_on_date, NIFTY_SYMBOL

    client = create_client(url, key)

    rows = (client.table("recommendation_outcomes")
                  .select("id,symbol,rec_date,nifty_entry")
                  .is_("nifty_entry", "null")
                  .execute()
                  .data or [])

    print(f"{'DRY RUN — no writes' if dry else 'LIVE'}")
    print(f"rows with NULL nifty_entry: {len(rows)}")
    if not rows:
        print("Nothing to repair.")
        return

    # One NIFTY lookup per distinct date, not per row.
    wanted = sorted({str(r["rec_date"])[:10] for r in rows if r.get("rec_date")})
    print(f"distinct rec_dates to price: {len(wanted)}")

    closes: dict[str, float] = {}
    for d in wanted:
        px = _fetch_price_on_date(NIFTY_SYMBOL, date.fromisoformat(d), window=5)
        if px:
            closes[d] = float(px)
        else:
            print(f"  WARN no NIFTY close near {d}")
    print(f"resolved {len(closes)}/{len(wanted)} dates\n")

    updated = failed = 0
    for r in rows:
        d = str(r.get("rec_date"))[:10]
        px = closes.get(d)
        if not px:
            failed += 1
            continue
        if dry:
            updated += 1
            continue
        try:
            (client.table("recommendation_outcomes")
                   .update({"nifty_entry": px})
                   .eq("id", r["id"])
                   .execute())
            updated += 1
        except Exception as exc:
            print(f"  ERROR {r['symbol']}: {exc}")
            failed += 1

    verb = "would update" if dry else "updated"
    print(f"{verb}: {updated}   unresolved: {failed}")
    if dry:
        print("\nRe-run with --run to apply.")
    else:
        print("\nNext: the 16:30 forward poller will fill alpha_live, and the")
        print("18:30 tracker will resolve the 113 rows already past t+30.")
        print("To do it now without waiting:")
        print("  python -c \"from agents.outcome_tracker import run_forward_polling as f; print(f(dry_run=False))\"")
        print("  python -c \"from agents.outcome_tracker import run_outcome_tracking as t; print(t(dry_run=False))\"")


if __name__ == "__main__":
    main()
