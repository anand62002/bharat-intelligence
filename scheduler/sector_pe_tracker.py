"""
scheduler/sector_pe_tracker.py — Daily Sector PE Snapshot Tracker
=================================================================
Fetches live Nifty sector P/E ratios from agents/sector_valuation.py
and persists daily snapshots to the Supabase `sector_pe_snapshots` table.

This creates the historical time-series that enables:
  • Regime trend detection — is a sector getting cheaper or more expensive?
  • Rolling long-run PE updates — keeps SECTOR_LONGRUN_PE calibrated to data
  • Learning loop input — feeds Priority 3 performance_tracker with regime context

The tracker is designed to be:
  - Idempotent: running it twice on the same day upserts the same row, no duplicates
  - Fault-tolerant: individual sector failures are collected and returned, not raised
  - Lightweight: reuses sector_valuation's in-process cache; total runtime ~5–15s

Entry points
------------
  run_snapshot(sectors, dry_run)                  -> SnapshotResult
  get_sector_pe_history(sector_key, lookback_days) -> list[dict]
  get_regime_changes(sector_key, lookback_days)    -> list[dict]
  compute_rolling_longrun_pe(sector_key, window_days) -> Optional[float]
  get_regime_trend(sector_key, lookback_days)      -> str  COMPRESSING|STABLE|EXPANDING

CLI
---
  python scheduler/sector_pe_tracker.py               # run snapshot, save to DB
  python scheduler/sector_pe_tracker.py --dry         # run but don't save
  python scheduler/sector_pe_tracker.py --sectors it,banking,pharmaceuticals
  python scheduler/sector_pe_tracker.py --history it  # print 30-day history for "it"
  python scheduler/sector_pe_tracker.py --trend       # print trend for all sectors
"""

import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

log = logging.getLogger(__name__)

# ── Agent imports (module-level so tests can patch them) ─────────────────────
from agents.sector_valuation import get_sector_regime, SECTOR_LONGRUN_PE  # noqa: E402

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

_MIN_HISTORY_FOR_ROLLING: int = 90   # minimum data points for rolling PE to be valid
_TREND_SHORT_WINDOW:      int = 5    # days for short moving average in trend detection
_TREND_LONG_WINDOW:       int = 20   # days for long moving average in trend detection

# ──────────────────────────────────────────────────────────────────────────────
# Result dataclass
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class SnapshotResult:
    """Summary of a single run_snapshot() execution."""
    snapshot_date:    str            # ISO date string, e.g. "2026-04-19"
    sectors_fetched:  int            # number of sectors attempted
    sectors_saved:    int            # number upserted to DB (0 if dry_run)
    sectors_fallback: int            # number that used fallback_fair (no live data)
    regime_changes:   list[dict]     # sectors that changed regime vs yesterday
    errors:           list[str]      # non-fatal per-sector errors
    dry_run:          bool

    def to_dict(self) -> dict:
        return asdict(self)


# ──────────────────────────────────────────────────────────────────────────────
# Supabase helper
# ──────────────────────────────────────────────────────────────────────────────

def _supabase_client():
    """
    Return a live Supabase client or None if credentials are absent.
    Never raises — callers treat None as "DB unavailable".
    """
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception as exc:
        log.warning("Supabase connect failed: %s", exc)
        return None


# ──────────────────────────────────────────────────────────────────────────────
# DB write helpers
# ──────────────────────────────────────────────────────────────────────────────

def _upsert_snapshot(client, row: dict) -> bool:
    """
    Upsert a single sector snapshot row into sector_pe_snapshots.

    The table has a UNIQUE constraint on (snapshot_date, sector_key) so an
    upsert is safe to run multiple times on the same day without creating
    duplicate rows.

    Args:
        client: Live Supabase client.
        row:    Dict with keys: snapshot_date, sector_key, live_pe,
                long_run_pe, regime, multiplier, deviation_pct, data_source.

    Returns:
        True on success, False on any failure.
    """
    try:
        (
            client
            .table("sector_pe_snapshots")
            .upsert(row, on_conflict="snapshot_date,sector_key")
            .execute()
        )
        return True
    except Exception as exc:
        log.warning("sector_pe_snapshots upsert failed for %s: %s",
                    row.get("sector_key"), exc)
        return False


def _get_yesterday_regime(client, sector_key: str) -> Optional[str]:
    """
    Query sector_pe_snapshots for the most recent regime recorded for
    `sector_key` before today.

    Returns:
        regime string (e.g. "FAIR") or None if no prior row exists.
    """
    try:
        today_str = date.today().isoformat()
        resp = (
            client
            .table("sector_pe_snapshots")
            .select("regime, snapshot_date")
            .eq("sector_key", sector_key)
            .lt("snapshot_date", today_str)
            .order("snapshot_date", desc=True)
            .limit(1)
            .execute()
        )
        if resp.data:
            return resp.data[0].get("regime")
        return None
    except Exception as exc:
        log.debug("get_yesterday_regime failed for %s: %s", sector_key, exc)
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Main snapshot function
# ──────────────────────────────────────────────────────────────────────────────

def run_snapshot(
    sectors:  Optional[list[str]] = None,
    dry_run:  bool = False,
) -> SnapshotResult:
    """
    Fetch live sector PE regimes and persist them to sector_pe_snapshots.

    Args:
        sectors:  List of sector_key strings to snapshot.
                  If None, snapshots ALL sectors in SECTOR_LONGRUN_PE.
        dry_run:  If True, fetches and classifies regimes but does NOT write
                  to Supabase. Useful for testing and dry pipeline runs.

    Returns:
        SnapshotResult with counts of fetched / saved / fallback sectors,
        detected regime changes, and any non-fatal errors encountered.
    """
    today_str       = date.today().isoformat()
    target_sectors  = sectors if sectors is not None else list(SECTOR_LONGRUN_PE.keys())
    client          = None if dry_run else _supabase_client()

    sectors_saved    = 0
    sectors_fallback = 0
    regime_changes:  list[dict] = []
    errors:          list[str]  = []

    for sector_key in target_sectors:
        try:
            regime_info = get_sector_regime(sector_key)

            is_fallback = (regime_info.get("data_source") == "fallback_fair")
            if is_fallback:
                sectors_fallback += 1

            row = {
                "snapshot_date": today_str,
                "sector_key":    sector_key,
                "live_pe":       regime_info.get("live_pe"),
                "long_run_pe":   regime_info.get("long_run_pe"),
                "deviation_pct": regime_info.get("deviation_pct"),
                "regime":        regime_info.get("regime", "FAIR"),
                "multiplier":    regime_info.get("multiplier", 1.0),
                "data_source":   regime_info.get("data_source", "fallback_fair"),
            }

            # Detect regime change vs yesterday
            if client is not None:
                prev_regime = _get_yesterday_regime(client, sector_key)
                today_regime = row["regime"]
                if prev_regime is not None and prev_regime != today_regime:
                    regime_changes.append({
                        "sector_key":   sector_key,
                        "from_regime":  prev_regime,
                        "to_regime":    today_regime,
                        "snapshot_date": today_str,
                        "live_pe":      row["live_pe"],
                        "deviation_pct": row["deviation_pct"],
                    })
                    log.info(
                        "Regime CHANGE [%s]: %s → %s  (live_pe=%.1f, dev=%+.0f%%)",
                        sector_key, prev_regime, today_regime,
                        row["live_pe"] or 0,
                        row["deviation_pct"] or 0,
                    )

            # Write to DB
            if not dry_run:
                if client is None:
                    log.debug("Supabase unavailable — skipping DB write for %s", sector_key)
                else:
                    if _upsert_snapshot(client, row):
                        sectors_saved += 1

        except Exception as exc:
            msg = f"{sector_key}: {exc}"
            errors.append(msg)
            log.warning("sector_pe_tracker error for %s: %s", sector_key, exc)

    result = SnapshotResult(
        snapshot_date    = today_str,
        sectors_fetched  = len(target_sectors),
        sectors_saved    = sectors_saved,
        sectors_fallback = sectors_fallback,
        regime_changes   = regime_changes,
        errors           = errors,
        dry_run          = dry_run,
    )

    log.info(
        "Snapshot %s: fetched=%d saved=%d fallback=%d changes=%d errors=%d%s",
        today_str,
        result.sectors_fetched,
        result.sectors_saved,
        result.sectors_fallback,
        len(result.regime_changes),
        len(result.errors),
        " [DRY RUN]" if dry_run else "",
    )
    return result


# ──────────────────────────────────────────────────────────────────────────────
# History query helpers
# ──────────────────────────────────────────────────────────────────────────────

def get_sector_pe_history(
    sector_key:    str,
    lookback_days: int = 90,
) -> list[dict]:
    """
    Retrieve the daily P/E history for a sector from sector_pe_snapshots.

    Args:
        sector_key:    Lower-case sector key, e.g. "it", "banking".
        lookback_days: How far back to look (default 90 days).

    Returns:
        List of row dicts sorted by snapshot_date ASC.
        Returns [] if Supabase is not configured or there is no data.
    """
    client = _supabase_client()
    if client is None:
        return []

    cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
    try:
        resp = (
            client
            .table("sector_pe_snapshots")
            .select("snapshot_date, sector_key, live_pe, long_run_pe, "
                    "deviation_pct, regime, multiplier, data_source")
            .eq("sector_key", sector_key)
            .gte("snapshot_date", cutoff)
            .order("snapshot_date", desc=False)
            .execute()
        )
        return resp.data or []
    except Exception as exc:
        log.warning("get_sector_pe_history failed for %s: %s", sector_key, exc)
        return []


def get_regime_changes(
    sector_key:    str,
    lookback_days: int = 30,
) -> list[dict]:
    """
    Find dates when a sector's valuation regime changed.

    Args:
        sector_key:    Lower-case sector key.
        lookback_days: History window to analyse (default 30 days).

    Returns:
        List of change events, each a dict:
          {from_date, to_date, from_regime, to_regime, live_pe_change}
        Sorted chronologically. Returns [] if fewer than 2 rows exist.
    """
    rows = get_sector_pe_history(sector_key, lookback_days=lookback_days)
    if len(rows) < 2:
        return []

    changes: list[dict] = []
    prev = rows[0]
    for curr in rows[1:]:
        if curr.get("regime") != prev.get("regime"):
            prev_pe  = prev.get("live_pe")
            curr_pe  = curr.get("live_pe")
            pe_delta = None
            if prev_pe is not None and curr_pe is not None:
                try:
                    pe_delta = round(float(curr_pe) - float(prev_pe), 2)
                except (TypeError, ValueError):
                    pass

            changes.append({
                "from_date":      prev.get("snapshot_date"),
                "to_date":        curr.get("snapshot_date"),
                "from_regime":    prev.get("regime"),
                "to_regime":      curr.get("regime"),
                "live_pe_change": pe_delta,
            })
        prev = curr

    return changes


# ──────────────────────────────────────────────────────────────────────────────
# Rolling long-run PE computation (Python-side fallback)
# ──────────────────────────────────────────────────────────────────────────────

def compute_rolling_longrun_pe(
    sector_key:  str,
    window_days: int = 365,
) -> Optional[float]:
    """
    Compute the rolling median live P/E for a sector over the last `window_days`.

    This is the Python-side equivalent of the `rolling_longrun_pe` SQL function
    in the migration.  It is used when the SQL function is not available or when
    working in dry_run / test mode.

    Args:
        sector_key:  Lower-case sector key.
        window_days: Rolling window in days (default 365 = 1 year).

    Returns:
        Median live_pe (float) if >= _MIN_HISTORY_FOR_ROLLING valid data points
        exist in the window, otherwise None.
    """
    rows = get_sector_pe_history(sector_key, lookback_days=window_days)

    valid_pes: list[float] = []
    for row in rows:
        pe = row.get("live_pe")
        if pe is not None:
            try:
                valid_pes.append(float(pe))
            except (TypeError, ValueError):
                pass

    if len(valid_pes) < _MIN_HISTORY_FOR_ROLLING:
        return None

    valid_pes.sort()
    mid = len(valid_pes) // 2
    if len(valid_pes) % 2 == 1:
        return round(valid_pes[mid], 2)
    return round((valid_pes[mid - 1] + valid_pes[mid]) / 2, 2)


# ──────────────────────────────────────────────────────────────────────────────
# Regime trend detection
# ──────────────────────────────────────────────────────────────────────────────

def get_regime_trend(
    sector_key:    str,
    lookback_days: int = 30,
) -> str:
    """
    Determine whether a sector's P/E is trending lower (COMPRESSING),
    higher (EXPANDING), or is stable (STABLE).

    Method: compare the average live_pe of the most recent _TREND_SHORT_WINDOW
    days against the average of the most recent _TREND_LONG_WINDOW days.

      short_avg < long_avg * 0.97  → COMPRESSING  (PE falling, sector getting cheaper)
      short_avg > long_avg * 1.03  → EXPANDING    (PE rising, sector getting dearer)
      otherwise                    → STABLE

    Returns:
        "COMPRESSING" | "EXPANDING" | "STABLE"
    """
    rows = get_sector_pe_history(sector_key, lookback_days=lookback_days)

    # Extract valid live_pe values (most recent first)
    pes: list[float] = []
    for row in reversed(rows):   # reversed so [0] = most recent
        pe = row.get("live_pe")
        if pe is not None:
            try:
                pes.append(float(pe))
            except (TypeError, ValueError):
                pass

    if len(pes) < _TREND_LONG_WINDOW:
        return "STABLE"

    short_avg = sum(pes[:_TREND_SHORT_WINDOW]) / _TREND_SHORT_WINDOW
    long_avg  = sum(pes[:_TREND_LONG_WINDOW])  / _TREND_LONG_WINDOW

    if long_avg == 0:
        return "STABLE"

    ratio = short_avg / long_avg
    if ratio < 0.97:
        return "COMPRESSING"
    if ratio > 1.03:
        return "EXPANDING"
    return "STABLE"


# ──────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import json

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Bharat Intelligence — Sector PE Snapshot Tracker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scheduler/sector_pe_tracker.py                        # snapshot all sectors, save to DB
  python scheduler/sector_pe_tracker.py --dry                  # snapshot but don't save
  python scheduler/sector_pe_tracker.py --sectors it,banking   # specific sectors only
  python scheduler/sector_pe_tracker.py --history it           # print 30-day history
  python scheduler/sector_pe_tracker.py --trend                # print trend for all sectors
  python scheduler/sector_pe_tracker.py --rolling it           # rolling long-run PE for "it"
        """,
    )
    parser.add_argument("--dry",      action="store_true",
                        help="Fetch regimes but do not write to Supabase")
    parser.add_argument("--sectors",  default=None,
                        help="Comma-separated list of sector keys, e.g. it,banking,pharma")
    parser.add_argument("--history",  default=None, metavar="SECTOR",
                        help="Print 30-day PE history for a single sector and exit")
    parser.add_argument("--trend",    action="store_true",
                        help="Print regime trend for all sectors and exit")
    parser.add_argument("--rolling",  default=None, metavar="SECTOR",
                        help="Print rolling 365-day long-run PE for a sector and exit")
    args = parser.parse_args()

    # ── Info-only modes ───────────────────────────────────────────────────────
    if args.history:
        rows = get_sector_pe_history(args.history, lookback_days=30)
        print(f"\nPE history for '{args.history}' (last 30 days) — {len(rows)} rows")
        print(json.dumps(rows, indent=2, default=str))
        sys.exit(0)

    if args.rolling:
        pe = compute_rolling_longrun_pe(args.rolling)
        if pe is not None:
            print(f"\nRolling 365-day long-run PE for '{args.rolling}': {pe:.2f}")
        else:
            print(f"\nInsufficient data for rolling PE calculation for '{args.rolling}' "
                  f"(need >= {_MIN_HISTORY_FOR_ROLLING} data points)")
        sys.exit(0)

    if args.trend:
        print(f"\nRegime trends (last 30 days):")
        for sk in sorted(SECTOR_LONGRUN_PE.keys()):
            trend = get_regime_trend(sk, lookback_days=30)
            print(f"  {sk:35s} {trend}")
        sys.exit(0)

    # ── Snapshot run ─────────────────────────────────────────────────────────
    sector_list = None
    if args.sectors:
        sector_list = [s.strip().lower() for s in args.sectors.split(",") if s.strip()]

    result = run_snapshot(sectors=sector_list, dry_run=args.dry)
    print(json.dumps(result.to_dict(), indent=2, default=str))

    if result.regime_changes:
        print(f"\n{'='*60}")
        print(f"REGIME CHANGES DETECTED ({len(result.regime_changes)}):")
        for chg in result.regime_changes:
            print(f"  [{chg['sector_key']}] {chg['from_regime']} → {chg['to_regime']}"
                  f"  (live_pe={chg.get('live_pe')}, dev={chg.get('deviation_pct'):+.0f}%)"
                  if chg.get("deviation_pct") is not None else
                  f"  [{chg['sector_key']}] {chg['from_regime']} → {chg['to_regime']}")
