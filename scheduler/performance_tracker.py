"""
scheduler/performance_tracker.py — Bharat Intelligence: Daily Performance Tracker
==================================================================================
Runs daily at 07:00 IST to find recommendations whose evaluation horizon just
elapsed and back-fills per-agent directional accuracy into agent_performance.

Complements the weekly hallucination_detector.py audit (which samples up to
20 historical recs) by keeping accuracy_90d current on a rolling daily basis.

Algorithm
─────────
  1. Query recommendations created between (today - lookback_days - max_horizon)
     and (today - min_horizon); filter in Python for those whose maturity date
     falls within the requested window.
  2. For each mature rec, fetch entry + horizon prices via yfinance and evaluate
     whether each agent's signal was directionally correct.
  3. Aggregate per-agent: correct_signals / total_evaluated × 100 = accuracy_90d.
  4. Insert rows into agent_performance (one row per agent per run_date).

Entry points
────────────
  run(lookback_days, dry_run)               Daily job callable.
  backfill(from_date, to_date, dry_run)     Historical range backfill.
  get_current_accuracy(agent_name)          Latest accuracy for a single agent.

Usage
─────
  python scheduler/performance_tracker.py --run-now
  python scheduler/performance_tracker.py --run-now --dry
  python scheduler/performance_tracker.py --backfill --from 2025-01-01 --to 2025-03-31
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
DEFAULT_ACCURACY_BASELINE = 70.0   # trust baseline (matches orchestrator.DEFAULT_ACCURACY)
DIRECTIONAL_BUFFER_PCT    = 2.0    # > +2% = BUY correct; < -2% = SELL correct
HOLD_BAND_PCT             = 10.0   # within ±10% = HOLD correct
MAX_HORIZON_DAYS          = 365    # longest horizon we search back for
MIN_HORIZON_DAYS          = 30     # skip recs with suspiciously short horizons
FETCH_BATCH_SIZE          = 200    # max recs to pull from DB per query


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PerformanceResult:
    """Summary returned by run() and backfill()."""
    run_date:          str
    lookback_days:     int
    recs_evaluated:    int
    agents_updated:    int
    accuracy_by_agent: dict = field(default_factory=dict)   # {agent_name: accuracy_90d}
    errors:            list = field(default_factory=list)
    dry_run:           bool = False
    duration_seconds:  float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# Infrastructure helpers
# ─────────────────────────────────────────────────────────────────────────────

def _supabase_client():
    """Create and return a Supabase client, or None if credentials are missing."""
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception as exc:
        log.warning("Supabase connect failed: %s", exc)
        return None


def _fetch_price_on_date(symbol: str, target_date: date) -> Optional[float]:
    """
    Return adjusted closing price for *symbol* on or just after *target_date*.
    Looks up to 7 calendar days forward to handle weekends/holidays.
    Returns None when no data is available.
    """
    try:
        import yfinance as yf
        from data.fetchers import yf_fetch_with_retry
        start = target_date
        end   = target_date + timedelta(days=7)
        _t    = yf.Ticker(symbol)
        df    = yf_fetch_with_retry(
            _t.history, start=str(start), end=str(end), auto_adjust=True
        )
        if df.empty:
            return None
        return float(df["Close"].iloc[0])
    except Exception as exc:
        log.debug("Price fetch failed (%s, %s): %s", symbol, target_date, exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Signal evaluation
# ─────────────────────────────────────────────────────────────────────────────

def _is_signal_correct(signal: str, actual_return_pct: float) -> Optional[bool]:
    """
    Evaluate whether a single agent signal was directionally correct.

    Returns:
        True  → correct  (BUY + positive return, SELL + negative, HOLD + stable)
        False → wrong
        None  → unevaluable (NO_DATA / NEUTRAL / unrecognised)
    """
    sig = (signal or "").upper()
    if sig in ("NO_DATA", "", "NEUTRAL"):
        return None
    if sig == "BUY":
        return actual_return_pct > DIRECTIONAL_BUFFER_PCT
    if sig in ("SELL", "AVOID"):
        return actual_return_pct < -DIRECTIONAL_BUFFER_PCT
    if sig == "HOLD":
        return abs(actual_return_pct) <= HOLD_BAND_PCT
    return None  # unrecognised signal


def _evaluate_rec(rec: dict) -> dict[str, Optional[bool]]:
    """
    Fetch entry + horizon prices for one recommendation and evaluate each
    agent signal.

    Returns:
        {agent_name: True/False/None} — True=correct, False=wrong, None=N/A.
        Empty dict when prices cannot be fetched.
    """
    symbol        = rec.get("symbol", "")
    created_at    = rec.get("created_at", "")
    horizon_days  = int(rec.get("horizon_days") or 180)
    agent_signals = rec.get("agent_signals") or {}

    # Parse creation date
    try:
        if isinstance(created_at, str):
            created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        elif isinstance(created_at, datetime):
            created_dt = created_at
        else:
            log.debug("Unrecognised created_at type for rec %s", rec.get("id"))
            return {}
        entry_date   = created_dt.date()
        horizon_date = entry_date + timedelta(days=horizon_days)
    except (ValueError, AttributeError) as exc:
        log.debug("Date parse error rec %s: %s", rec.get("id"), exc)
        return {}

    # Fetch prices
    entry_price   = _fetch_price_on_date(symbol, entry_date)
    horizon_price = _fetch_price_on_date(symbol, horizon_date)

    if not entry_price:
        log.debug("[%s] entry price unavailable for %s", symbol, entry_date)
        return {}
    if horizon_price is None:
        log.debug("[%s] horizon price unavailable for %s", symbol, horizon_date)
        return {}

    actual_return_pct = (horizon_price - entry_price) / entry_price * 100.0
    log.info(
        "[%s] entry=%.2f  horizon=%.2f  return=%.2f%%  (horizon_date=%s)",
        symbol, entry_price, horizon_price, actual_return_pct, horizon_date,
    )

    evaluations: dict[str, Optional[bool]] = {}
    for agent_name, signal_data in agent_signals.items():
        if isinstance(signal_data, dict):
            signal = signal_data.get("signal", "NO_DATA")
        elif isinstance(signal_data, str):
            signal = signal_data
        else:
            signal = "NO_DATA"

        result = _is_signal_correct(signal, actual_return_pct)
        evaluations[agent_name] = result
        if result is not None:
            log.debug(
                "  [%s] agent=%s signal=%s → %s",
                symbol, agent_name, signal,
                "CORRECT" if result else "WRONG",
            )

    return evaluations


# ─────────────────────────────────────────────────────────────────────────────
# Accuracy aggregation
# ─────────────────────────────────────────────────────────────────────────────

def _compute_accuracy_stats(
    all_evaluations: list[dict[str, Optional[bool]]],
) -> dict[str, dict]:
    """
    Aggregate per-agent correctness across all evaluated recommendations.

    Returns:
        {agent_name: {"correct": N, "total": N, "accuracy_90d": float}}
    """
    tallies: dict[str, dict] = {}
    for evals in all_evaluations:
        for agent, result in evals.items():
            if result is None:
                continue  # NO_DATA / unevaluable
            tallies.setdefault(agent, {"correct": 0, "total": 0})
            tallies[agent]["total"] += 1
            if result:
                tallies[agent]["correct"] += 1

    out: dict[str, dict] = {}
    for agent, tally in tallies.items():
        total   = tally["total"]
        correct = tally["correct"]
        acc     = round((correct / total) * 100.0, 2) if total > 0 else 0.0
        out[agent] = {"correct": correct, "total": total, "accuracy_90d": acc}
    return out


# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────

def _find_matured_recs(
    client,
    cutoff_start: date,
    cutoff_end:   date,
) -> list[dict]:
    """
    Return recommendations whose horizon matured within [cutoff_start, cutoff_end].

    Because `maturity_date = created_at + horizon_days` is computed, Supabase
    cannot filter it directly.  We fetch a broad window of recs and filter in Python.
    """
    # created_at must lie in [cutoff_start - MAX_HORIZON, cutoff_end - MIN_HORIZON]
    earliest_created = cutoff_start - timedelta(days=MAX_HORIZON_DAYS)
    latest_created   = cutoff_end   - timedelta(days=MIN_HORIZON_DAYS)

    if earliest_created > latest_created:
        return []

    try:
        resp = (
            client.table("recommendations")
            .select("id, symbol, created_at, horizon_days, agent_signals")
            .gte("created_at", earliest_created.isoformat())
            .lte("created_at", latest_created.isoformat() + "T23:59:59Z")
            .order("created_at", desc=False)
            .limit(FETCH_BATCH_SIZE)
            .execute()
        )
        all_recs = resp.data or []
    except Exception as exc:
        log.error("Failed to fetch recommendations from DB: %s", exc)
        return []

    # Filter to recs whose maturity falls within the requested window
    matured: list[dict] = []
    for rec in all_recs:
        try:
            created_str  = str(rec.get("created_at", ""))
            if not created_str:
                continue
            created_dt   = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            horizon_days = int(rec.get("horizon_days") or 180)
            maturity     = created_dt.date() + timedelta(days=horizon_days)
            if cutoff_start <= maturity <= cutoff_end:
                matured.append(rec)
        except (ValueError, TypeError):
            continue

    return matured


def _update_accuracy_90d(
    client,
    agent_stats: dict[str, dict],
    dry_run:     bool,
) -> int:
    """
    Insert accuracy_90d into agent_performance for each agent.

    Returns:
        Number of agents successfully written (or printed in dry_run).
    """
    if not agent_stats:
        return 0

    today   = date.today().isoformat()
    written = 0

    for agent_name, stats in agent_stats.items():
        acc_90d = stats.get("accuracy_90d")
        if acc_90d is None:
            continue

        if dry_run:
            correct = stats.get("correct", 0)
            total   = stats.get("total", 0)
            print(
                f"  [DRY RUN] agent_performance: {agent_name}  "
                f"accuracy_90d={acc_90d:.2f}%  ({correct}/{total} correct)"
            )
            written += 1
            continue

        if not client:
            continue

        try:
            client.table("agent_performance").insert({
                "agent_name":   agent_name,
                "audit_date":   today,
                "accuracy_90d": acc_90d,
                "trend":        "STABLE",  # weekly hallucination_detector owns trend computation
            }).execute()
            log.info(
                "agent_performance updated: %s  accuracy_90d=%.2f%%",
                agent_name, acc_90d,
            )
            written += 1
        except Exception as exc:
            log.warning("agent_performance update failed for %s: %s", agent_name, exc)

    return written


# ─────────────────────────────────────────────────────────────────────────────
# Core shared logic
# ─────────────────────────────────────────────────────────────────────────────

def _run_for_range(
    cutoff_start:  date,
    cutoff_end:    date,
    lookback_days: int,
    dry_run:       bool,
    t0:            float,
) -> PerformanceResult:
    """Core logic shared by run() and backfill()."""
    today  = date.today()
    errors: list[str] = []

    log.info(
        "Performance tracker: seeking recs matured %s → %s (dry_run=%s)",
        cutoff_start, cutoff_end, dry_run,
    )

    client = _supabase_client()
    if not client and not dry_run:
        log.error("Supabase unavailable — cannot run performance tracker")
        return PerformanceResult(
            run_date         = today.isoformat(),
            lookback_days    = lookback_days,
            recs_evaluated   = 0,
            agents_updated   = 0,
            errors           = ["Supabase unavailable"],
            dry_run          = dry_run,
            duration_seconds = round(time.time() - t0, 2),
        )

    # ── Step 1: Find matured recs ─────────────────────────────────────────────
    matured_recs: list[dict] = []
    if client:
        matured_recs = _find_matured_recs(client, cutoff_start, cutoff_end)
    else:
        log.info("[DRY RUN] No Supabase client; skipping DB fetch")

    log.info(
        "Found %d recs matured in [%s, %s]",
        len(matured_recs), cutoff_start, cutoff_end,
    )

    if not matured_recs:
        return PerformanceResult(
            run_date         = today.isoformat(),
            lookback_days    = lookback_days,
            recs_evaluated   = 0,
            agents_updated   = 0,
            errors           = errors,
            dry_run          = dry_run,
            duration_seconds = round(time.time() - t0, 2),
        )

    # ── Step 2: Evaluate each rec ─────────────────────────────────────────────
    all_evaluations: list[dict[str, Optional[bool]]] = []
    for rec in matured_recs:
        try:
            evals = _evaluate_rec(rec)
            if evals:
                all_evaluations.append(evals)
        except Exception as exc:
            err = f"rec {rec.get('id', '?')} [{rec.get('symbol', '?')}]: {exc}"
            log.warning("Evaluation error — %s", err)
            errors.append(err)

    # ── Step 3: Aggregate accuracy per agent ──────────────────────────────────
    agent_stats = _compute_accuracy_stats(all_evaluations)

    if agent_stats:
        print()
        print("-" * 70)
        print(f"  Performance Tracker — {today}   ({len(matured_recs)} recs evaluated)")
        print("-" * 70)
        for agent, stats in sorted(agent_stats.items()):
            print(
                f"  {agent:<18}  accuracy_90d={stats['accuracy_90d']:.1f}%  "
                f"({stats['correct']}/{stats['total']} correct)"
            )
        print("-" * 70)
        print()

    # ── Step 4: Upsert to agent_performance ──────────────────────────────────
    agents_updated = _update_accuracy_90d(client, agent_stats, dry_run)

    log.info(
        "Performance tracker done — %d recs evaluated, %d agents updated in %.1fs",
        len(matured_recs), agents_updated, time.time() - t0,
    )

    return PerformanceResult(
        run_date          = today.isoformat(),
        lookback_days     = lookback_days,
        recs_evaluated    = len(matured_recs),
        agents_updated    = agents_updated,
        accuracy_by_agent = {a: s["accuracy_90d"] for a, s in agent_stats.items()},
        errors            = errors,
        dry_run           = dry_run,
        duration_seconds  = round(time.time() - t0, 2),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def run(lookback_days: int = 2, dry_run: bool = False) -> PerformanceResult:
    """
    Daily accuracy backfill job.

    Finds recommendations whose horizon matured within the last *lookback_days*
    days, evaluates directional accuracy, and inserts results into agent_performance.

    Args:
        lookback_days:  How many days back to search (default 2 to catch today
                        and yesterday, handling weekend/holiday gaps).
        dry_run:        If True, compute accuracy but skip DB writes.

    Returns:
        PerformanceResult with per-agent accuracy and run metadata.
    """
    t0 = time.time()
    today = date.today()
    return _run_for_range(
        cutoff_start  = today - timedelta(days=lookback_days),
        cutoff_end    = today,
        lookback_days = lookback_days,
        dry_run       = dry_run,
        t0            = t0,
    )


def backfill(
    from_date: date,
    to_date:   date,
    dry_run:   bool = False,
) -> PerformanceResult:
    """
    Historical accuracy backfill over a custom date range.

    Finds all recs that matured between *from_date* and *to_date* (inclusive),
    evaluates directional accuracy, and inserts results into agent_performance.

    Args:
        from_date:  First maturity date to include (inclusive).
        to_date:    Last maturity date to include (inclusive).
        dry_run:    If True, compute but skip DB writes.

    Returns:
        PerformanceResult with per-agent accuracy and metadata.
    """
    t0 = time.time()
    window_days = (to_date - from_date).days + 1
    return _run_for_range(
        cutoff_start  = from_date,
        cutoff_end    = to_date,
        lookback_days = window_days,
        dry_run       = dry_run,
        t0            = t0,
    )


def get_current_accuracy(agent_name: str) -> Optional[float]:
    """
    Return the most recent accuracy_90d for *agent_name* from agent_performance.

    Returns None when no data is available or Supabase is unreachable.
    """
    client = _supabase_client()
    if not client:
        return None
    try:
        resp = (
            client.table("agent_performance")
            .select("accuracy_90d")
            .eq("agent_name", agent_name)
            .order("audit_date", desc=True)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if rows and rows[0].get("accuracy_90d") is not None:
            return float(rows[0]["accuracy_90d"])
    except Exception as exc:
        log.warning("get_current_accuracy failed for %s: %s", agent_name, exc)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# APScheduler + CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bharat Intelligence Daily Performance Tracker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scheduler/performance_tracker.py                           # 07:00 IST daily scheduler
  python scheduler/performance_tracker.py --run-now                 # run immediately
  python scheduler/performance_tracker.py --run-now --dry           # dry run (no DB writes)
  python scheduler/performance_tracker.py --backfill --from 2025-01-01 --to 2025-03-31
        """,
    )
    parser.add_argument(
        "--run-now", action="store_true",
        help="Execute the daily tracker immediately instead of scheduling",
    )
    parser.add_argument(
        "--dry", action="store_true",
        help="Dry run: compute accuracy but skip all Supabase writes",
    )
    parser.add_argument(
        "--backfill", action="store_true",
        help="Backfill accuracy for a historical date range",
    )
    parser.add_argument(
        "--from", dest="from_date",
        help="Backfill start date YYYY-MM-DD",
    )
    parser.add_argument(
        "--to", dest="to_date",
        help="Backfill end date YYYY-MM-DD (defaults to today)",
    )
    parser.add_argument(
        "--lookback", type=int, default=2,
        help="Days to look back for newly matured recs (default: 2)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    if args.backfill:
        if not args.from_date:
            parser.error("--backfill requires --from YYYY-MM-DD")
        fd = date.fromisoformat(args.from_date)
        td = date.fromisoformat(args.to_date) if args.to_date else date.today()
        result = backfill(fd, td, dry_run=args.dry)
        log.info("Backfill result: %s", result.to_dict())
        return

    if args.run_now:
        result = run(lookback_days=args.lookback, dry_run=args.dry)
        log.info("Run result: %s", result.to_dict())
        if result.errors:
            log.warning(
                "%d error(s): %s",
                len(result.errors), "; ".join(result.errors[:5]),
            )
        return

    # ── Scheduled mode: daily 07:00 IST ──────────────────────────────────────
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        log.error("apscheduler not installed — run: pip install apscheduler")
        sys.exit(1)

    try:
        from zoneinfo import ZoneInfo
        IST = ZoneInfo("Asia/Kolkata")
    except ImportError:
        import pytz
        IST = pytz.timezone("Asia/Kolkata")

    def _job() -> None:
        log.info("Daily performance tracker triggered by scheduler...")
        run(lookback_days=2, dry_run=False)

    scheduler = BlockingScheduler(timezone=IST)
    scheduler.add_job(
        _job,
        CronTrigger(hour=7, minute=0, timezone=IST),
        id="daily_performance_tracker",
        name="Bharat Intelligence Daily Performance Tracker",
        max_instances=1,
        coalesce=True,
    )

    log.info("-" * 60)
    log.info("  Bharat Intelligence Performance Tracker")
    log.info("  Schedule: every day at 07:00 IST")
    log.info("  Press Ctrl+C to stop")
    log.info("-" * 60)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped cleanly")


if __name__ == "__main__":
    main()
