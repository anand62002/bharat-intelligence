"""
worker.py — Bharat Intelligence Unified Background Worker
==========================================================
Runs ALL four scheduled jobs in a single process using one APScheduler
BlockingScheduler instance. Deploy as the `worker` dyno on Railway.

Schedule (all times IST / Asia/Kolkata)
----------------------------------------
  06:00  Daily orchestrator   — runs all 9 agents, synthesises recs, saves to DB
  07:00  Performance tracker  — audits agent accuracy, writes agent_performance
  07:30  Research agent       — scans arXiv / Semantic Scholar, saves proposals
  09:15, 11:30, 13:30, 15:15  Portfolio monitor — danger/alert checks (market hours)

Usage
-----
  python worker.py          # normal production mode
  python worker.py --now    # fire all jobs once immediately then start scheduler
                             # (useful to verify Railway env is wired correctly)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── Ensure project root is on sys.path ────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    stream=sys.stdout,   # Railway shows stdout as [inf], stderr as [err]
)
log = logging.getLogger("worker")

# ── IST timezone ──────────────────────────────────────────────────────────────
try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except ImportError:
    import pytz
    IST = pytz.timezone("Asia/Kolkata")


# =============================================================================
# Job wrappers
# =============================================================================

def job_orchestrator() -> None:
    """06:00 IST — main agent pipeline."""
    log.info("=" * 60)
    log.info("  JOB START: Daily Orchestrator (06:00 IST)")
    log.info("=" * 60)
    try:
        from scheduler.orchestrator import run_pipeline
        final = asyncio.run(run_pipeline(dry_run=False))
        errs  = final.get("errors", [])
        recs  = len(final.get("recommendations", []))
        syms  = final.get("symbols_processed", 0)
        log.info("Orchestrator done — symbols=%d  recs=%d  errors=%d", syms, recs, len(errs))
        if errs:
            for e in errs:
                log.warning("  orchestrator error: %s", e)
    except Exception as exc:
        log.error("Orchestrator job failed: %s", exc, exc_info=True)


def job_performance_tracker() -> None:
    """07:00 IST — audit agent accuracy."""
    log.info("=" * 60)
    log.info("  JOB START: Performance Tracker (07:00 IST)")
    log.info("=" * 60)
    try:
        from scheduler.performance_tracker import run as pt_run
        pt_run()
        log.info("Performance tracker done")
    except Exception as exc:
        log.error("Performance tracker job failed: %s", exc, exc_info=True)


def job_research_agent() -> None:
    """07:30 IST — AI paper scanner."""
    log.info("=" * 60)
    log.info("  JOB START: Research Agent (07:30 IST)")
    log.info("=" * 60)
    try:
        from governance.research_agent import run as ra_run
        result = ra_run(dry_run=False)
        saved = result.get("saved", 0) if isinstance(result, dict) else "?"
        log.info("Research agent done — proposals saved: %s", saved)
    except Exception as exc:
        log.error("Research agent job failed: %s", exc, exc_info=True)


def job_portfolio_monitor() -> None:
    """Every 2h during market hours — danger / stoploss / target alerts."""
    log.info("-" * 50)
    log.info("  JOB START: Portfolio Monitor")
    log.info("-" * 50)
    try:
        from scheduler.portfolio_monitor import run as pm_run
        pm_run()
        log.info("Portfolio monitor done")
    except Exception as exc:
        log.error("Portfolio monitor job failed: %s", exc, exc_info=True)


# =============================================================================
# Scheduler setup
# =============================================================================

def build_scheduler():
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron       import CronTrigger

    scheduler = BlockingScheduler(timezone=IST)

    # ── Daily orchestrator ────────────────────────────────────────────────────
    scheduler.add_job(
        job_orchestrator,
        CronTrigger(hour=6, minute=0, timezone=IST),
        id="orchestrator",
        name="Daily Orchestrator",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,     # tolerate up to 30-min startup delay
    )

    # ── Performance tracker ───────────────────────────────────────────────────
    scheduler.add_job(
        job_performance_tracker,
        CronTrigger(hour=7, minute=0, timezone=IST),
        id="performance_tracker",
        name="Performance Tracker",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
    )

    # ── Research agent ────────────────────────────────────────────────────────
    scheduler.add_job(
        job_research_agent,
        CronTrigger(hour=7, minute=30, timezone=IST),
        id="research_agent",
        name="Research Agent",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
    )

    # ── Portfolio monitor — 4× during market hours ────────────────────────────
    # 09:15 (open), 11:30 (mid-morning), 13:30 (post-lunch), 15:15 (pre-close)
    for h, m in [(9, 15), (11, 30), (13, 30), (15, 15)]:
        scheduler.add_job(
            job_portfolio_monitor,
            CronTrigger(hour=h, minute=m, timezone=IST),
            id=f"portfolio_monitor_{h:02d}{m:02d}",
            name=f"Portfolio Monitor {h:02d}:{m:02d}",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=600,
        )

    return scheduler


# =============================================================================
# Entry point
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Bharat Intelligence Worker")
    parser.add_argument(
        "--now",
        action="store_true",
        help="Fire all jobs once immediately (for smoke-testing Railway env), then start scheduler",
    )
    args = parser.parse_args()

    if args.now:
        log.info("--now flag: running all jobs once before starting scheduler...")
        job_orchestrator()
        job_performance_tracker()
        job_research_agent()
        job_portfolio_monitor()
        log.info("--now run complete. Starting scheduler...")

    try:
        from apscheduler.schedulers.blocking import BlockingScheduler  # noqa: F401
    except ImportError:
        log.error("apscheduler not installed — run: pip install apscheduler")
        sys.exit(1)

    scheduler = build_scheduler()

    log.info("=" * 60)
    log.info("  Bharat Intelligence Worker started")
    log.info("  Jobs scheduled (all times IST):")
    log.info("    06:00  Daily Orchestrator")
    log.info("    07:00  Performance Tracker")
    log.info("    07:30  Research Agent")
    log.info("    09:15  Portfolio Monitor")
    log.info("    11:30  Portfolio Monitor")
    log.info("    13:30  Portfolio Monitor")
    log.info("    15:15  Portfolio Monitor")
    log.info("  Press Ctrl+C to stop")
    log.info("=" * 60)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Worker stopped cleanly")


if __name__ == "__main__":
    main()
