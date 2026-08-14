"""
scripts/diagnose_performance.py — why is the Performance tab empty?

Answers three questions the dashboard cannot distinguish between:
  1. Are outcome rows being seeded at all?
  2. Are they being SKIPPED because entry_price / nifty_entry is NULL?
     (both the tracker and the poller `continue` past those rows silently,
      at DEBUG level, so the logs stay quiet while nothing updates)
  3. Is anything actually DUE — i.e. has t+30 / t+90 elapsed for any row?

Run from the project root on a machine with the SERVICE key (Railway shell, or
locally with SUPABASE_SERVICE_KEY set to the service_role key — NOT the anon
key, which RLS filters to zero rows and makes every table look empty):

    python scripts/diagnose_performance.py

Prints a plain-text report. Paste the whole thing back.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


def main() -> None:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not (url and key):
        print("FAIL: SUPABASE_URL / SUPABASE_SERVICE_KEY not set")
        return

    # Guard against the exact mistake that made every table look empty: an anon
    # key reads 0 rows from every RLS-protected table, which looks identical to
    # "the data is gone". Supabase issues two key formats — legacy JWTs
    # (anon / service_role) and newer opaque keys (sb_secret_… / sb_publishable_…)
    # — so handle both rather than assuming a JWT.
    if key.startswith("sb_secret_"):
        role, privileged = "secret (new format)", True
    elif key.startswith("sb_publishable_"):
        role, privileged = "publishable (new format)", False
    else:
        try:
            payload = key.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            role = json.loads(base64.urlsafe_b64decode(payload)).get("role", "unknown")
        except Exception:
            role = "unrecognised"
        privileged = (role == "service_role")

    print(f"key type = {role}")
    if not privileged:
        print("  !! This key does not bypass RLS — reads will return 0 rows and")
        print("     every count below will be meaningless.")
        print("     Supabase dashboard -> Settings -> API -> service_role (or")
        print("     'secret') key, then set SUPABASE_SERVICE_KEY in .env.")
        print()

    from supabase import create_client
    c = create_client(url, key)
    today = date.today()

    print("=" * 62)
    print(f"PERFORMANCE DIAGNOSTIC — {today}")
    print("=" * 62)

    # ── 1. Row counts ────────────────────────────────────────────────────────
    for table in ("recommendations", "recommendation_outcomes",
                  "paper_portfolio_positions", "agent_performance"):
        try:
            r = c.table(table).select("*", count="exact").limit(1).execute()
            print(f"{table:28} rows = {r.count}")
        except Exception as exc:
            print(f"{table:28} ERROR {str(exc)[:60]}")
    print()

    # ── 2. The critical check: NULL entry_price / nifty_entry ────────────────
    try:
        rows = (c.table("recommendation_outcomes")
                 .select("id,symbol,rec_date,entry_price,nifty_entry,"
                         "outcome_t30,outcome_t90,alpha_live,days_live,live_updated_at")
                 .execute().data or [])
    except Exception as exc:
        print(f"Could not read recommendation_outcomes: {exc}")
        return

    if not rows:
        print("recommendation_outcomes is EMPTY -> the 06:55 seeder is not writing.")
        return

    missing_entry = [r for r in rows if not r.get("entry_price")]
    missing_nifty = [r for r in rows if not r.get("nifty_entry")]
    skipped = {r["id"] for r in missing_entry} | {r["id"] for r in missing_nifty}

    print(f"total outcome rows        : {len(rows)}")
    print(f"NULL/zero entry_price     : {len(missing_entry)}")
    print(f"NULL/zero nifty_entry     : {len(missing_nifty)}")
    print(f"-> silently SKIPPED by tracker AND poller: {len(skipped)} "
          f"({len(skipped)/len(rows)*100:.0f}%)")
    print("   (both do `if not entry_price or not nifty_entry: continue`)")
    print()

    # ── 3. Has the live poller ever run? ─────────────────────────────────────
    with_live = [r for r in rows if r.get("alpha_live") is not None]
    print(f"rows with alpha_live set  : {len(with_live)}  "
          f"<- 0 means the 16:30 poller has never landed")
    stamps = sorted(str(r.get("live_updated_at")) for r in rows
                    if r.get("live_updated_at"))
    print(f"latest live_updated_at    : {stamps[-1] if stamps else 'NEVER'}")
    print()

    # ── 4. What is actually DUE? ─────────────────────────────────────────────
    dates = sorted(str(r["rec_date"])[:10] for r in rows if r.get("rec_date"))
    print(f"rec_date range            : {dates[0]} -> {dates[-1]}")
    for horizon in (30, 90, 180):
        cutoff = (today - timedelta(days=horizon)).isoformat()
        due = sum(1 for d in dates if d < cutoff)
        print(f"  rows past t+{horizon:<3}          : {due}"
              + ("   <- nothing to resolve yet; empty panels are CORRECT"
                 if due == 0 else "   <- these SHOULD be resolved"))
    print()
    print(f"outcome_t30 : {dict(Counter(r.get('outcome_t30') for r in rows))}")
    print(f"outcome_t90 : {dict(Counter(r.get('outcome_t90') for r in rows))}")
    print()

    # ── 5. Verdict ───────────────────────────────────────────────────────────
    print("-" * 62)
    print("VERDICT")
    if len(skipped) == len(rows):
        print("  Every row lacks entry_price/nifty_entry -> tracker and poller")
        print("  skip all of them. This is the blocker. Fix the seeder or")
        print("  backfill those two columns; nothing else can proceed.")
    elif not with_live:
        print("  Entry prices look fine but alpha_live is unset everywhere ->")
        print("  the 16:30 forward poller is not running or is erroring.")
        print("  Check Railway worker logs for 'Forward poller'.")
    elif sum(1 for d in dates if d < (today - timedelta(days=90)).isoformat()) == 0:
        print("  Data is healthy; no rec is 90 days old yet. Calibration and")
        print("  Best/Worst panels are correctly empty and will fill on their own.")
    else:
        print("  Rows are past t+90 and still PENDING -> the 18:30 outcome")
        print("  tracker is failing. Check Railway logs for 'Outcome Tracker'.")
    print("-" * 62)


if __name__ == "__main__":
    main()
