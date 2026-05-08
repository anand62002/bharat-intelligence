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
import asyncio
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


def job_earnings_calendar() -> None:
    """08:00 IST — refresh earnings calendar for portfolio symbols."""
    log.info("-" * 50)
    log.info("  JOB START: Earnings Calendar (08:00 IST)")
    log.info("-" * 50)
    try:
        from supabase import create_client
        import os
        client = create_client(os.getenv("SUPABASE_URL",""), os.getenv("SUPABASE_SERVICE_KEY",""))
        rows   = client.table("portfolio_holdings").select("symbol").eq("status","OPEN").execute().data or []
        symbols = [r["symbol"] for r in rows]
        if not symbols:
            log.info("No open holdings — skipping earnings calendar refresh")
            return
        from data.earnings_fetcher import fetch_upcoming_earnings, upsert_earnings_calendar
        records = fetch_upcoming_earnings(symbols, days_ahead=30)
        n = upsert_earnings_calendar(records)
        log.info("Earnings calendar refresh done — %d records upserted for %d symbols", n, len(symbols))
    except Exception as exc:
        log.error("Earnings calendar job failed: %s", exc, exc_info=True)


def job_regime_detector() -> None:
    """06:30 IST — detect market regime before orchestrator runs."""
    log.info("-" * 50)
    log.info("  JOB START: Regime Detector (06:30 IST)")
    log.info("-" * 50)
    try:
        from agents.regime_detector import detect_regime
        result = detect_regime(dry_run=False)
        log.info(
            "Regime: %s (confidence=%d%%) — nifty=%s vix=%s fii=%s",
            result.get("regime"), result.get("confidence", 0),
            result.get("nifty_trend"), result.get("vix_state"), result.get("fii_trend"),
        )
    except Exception as exc:
        log.error("Regime detector job failed: %s", exc, exc_info=True)


def job_outcome_tracker() -> None:
    """18:30 IST — resolve pending recommendation outcomes at 90/180/365d horizons."""
    log.info("=" * 60)
    log.info("  JOB START: Outcome Tracker (18:30 IST)")
    log.info("=" * 60)
    try:
        from agents.outcome_tracker import run_outcome_tracking
        result = run_outcome_tracking(dry_run=False)
        log.info(
            "Outcome tracker done — tracked=%d updated=%d hits=%d misses=%d avg_alpha_90d=%s",
            result.get("tracked", 0), result.get("updated", 0),
            result.get("hits", 0), result.get("misses", 0),
            f"{result['avg_alpha_90d']:.2%}" if result.get("avg_alpha_90d") is not None else "N/A",
        )
        if result.get("errors"):
            for e in result["errors"]:
                log.warning("  outcome tracker error: %s", e)
    except Exception as exc:
        log.error("Outcome tracker job failed: %s", exc, exc_info=True)


def job_portfolio_risk() -> None:
    """16:00 IST — compute portfolio-level risk metrics after market close."""
    log.info("-" * 50)
    log.info("  JOB START: Portfolio Risk (16:00 IST)")
    log.info("-" * 50)
    try:
        from agents.portfolio_risk import run_portfolio_risk
        result = run_portfolio_risk(dry_run=False)
        log.info(
            "Portfolio risk done — vol=%.1f%% VaR95=%.2f%% Sharpe=%s HHI=%.3f warnings=%d",
            result.get("portfolio_vol") or 0,
            result.get("var_95") or 0,
            result.get("sharpe"),
            result.get("hhi") or 0,
            len(result.get("warnings", [])),
        )
        if result.get("warnings"):
            for w in result["warnings"]:
                log.warning("  portfolio risk warning: %s", w)
    except Exception as exc:
        log.error("Portfolio risk job failed: %s", exc, exc_info=True)


def job_options_snapshot() -> None:
    """15:45 IST — capture options market snapshot (PCR, max pain, VIX) just before close."""
    log.info("-" * 50)
    log.info("  JOB START: Options Market Snapshot (15:45 IST)")
    log.info("-" * 50)
    try:
        from agents.options_sentiment import analyse_options
        symbols = ["NIFTY", "BANKNIFTY"]
        for sym in symbols:
            r = analyse_options(sym)
            log.info(
                "Options[%s] signal=%s score=%s pcr=%s vix=%s source=%s",
                sym,
                r.get("signal"),
                r.get("score"),
                r.get("pcr"),
                r.get("india_vix"),
                r.get("source"),
            )
    except Exception as exc:
        log.error("Options snapshot job failed: %s", exc, exc_info=True)


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


def job_backtest() -> None:
    """1st of every month, 07:45 IST — walk-forward backtest on NIFTY 500 quality universe."""
    log.info("=" * 60)
    log.info("  JOB START: Monthly Backtest (07:45 IST, 1st of month)")
    log.info("=" * 60)
    try:
        from agents.backtester import run_backtest
        result = run_backtest(dry_run=False)
        if "error" in result:
            log.error("Backtest failed: %s", result["error"])
        else:
            t = result["test"]
            log.info(
                "Backtest done — symbols=%d TEST: signals=%d hit_rate=%.1f%% "
                "avg_alpha=%.2f%% sharpe=%s",
                result["symbols_processed"],
                t["total_signals"],
                t["hit_rate_90d"],
                t["avg_alpha_90d"],
                t.get("sharpe_ratio"),
            )
    except Exception as exc:
        log.error("Backtest job failed: %s", exc, exc_info=True)


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

    # ── Earnings calendar — daily refresh ────────────────────────────────────
    scheduler.add_job(
        job_earnings_calendar,
        CronTrigger(hour=8, minute=0, timezone=IST),
        id="earnings_calendar",
        name="Earnings Calendar",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
    )

    # ── Regime detector — before orchestrator ────────────────────────────────
    scheduler.add_job(
        job_regime_detector,
        CronTrigger(hour=6, minute=30, timezone=IST),
        id="regime_detector",
        name="Regime Detector",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
    )

    # ── Outcome tracker — after market close ─────────────────────────────────
    scheduler.add_job(
        job_outcome_tracker,
        CronTrigger(hour=18, minute=30, timezone=IST),
        id="outcome_tracker",
        name="Outcome Tracker",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
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

    # ── Portfolio risk — after market close ───────────────────────────────────
    scheduler.add_job(
        job_portfolio_risk,
        CronTrigger(hour=16, minute=0, timezone=IST),
        id="portfolio_risk",
        name="Portfolio Risk",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
    )

    # ── Options snapshot — just before market close ───────────────────────────
    scheduler.add_job(
        job_options_snapshot,
        CronTrigger(hour=15, minute=45, timezone=IST),
        id="options_snapshot",
        name="Options Market Snapshot",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )

    # ── Monthly backtest — 1st of month, 07:45 IST ───────────────────────────
    # Runs after performance tracker (07:00) + research agent (07:30) so the
    # system is warm. Takes ~20–30 min for 80 symbols × 5yr OHLCV.
    scheduler.add_job(
        job_backtest,
        CronTrigger(day=1, hour=7, minute=45, timezone=IST),
        id="backtest_monthly",
        name="Monthly Backtest (07:45 IST, 1st)",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=7200,  # 2-hour grace (long job)
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
        job_regime_detector()
        job_orchestrator()
        job_performance_tracker()
        job_research_agent()
        job_portfolio_monitor()
        job_outcome_tracker()
        job_options_snapshot()
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
    log.info("    06:30  Regime Detector")
    log.info("    08:00  Earnings Calendar")
    log.info("    07:00  Performance Tracker")
    log.info("    07:30  Research Agent")
    log.info("    09:15  Portfolio Monitor")
    log.info("    11:30  Portfolio Monitor")
    log.info("    13:30  Portfolio Monitor")
    log.info("    15:15  Portfolio Monitor")
    log.info("    15:45  Options Market Snapshot")
    log.info("    16:00  Portfolio Risk")
    log.info("    18:30  Outcome Tracker")
    log.info("    07:45 (1st/month)  Monthly Backtest")
    log.info("  Press Ctrl+C to stop")
    log.info("=" * 60)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Worker stopped cleanly")


if __name__ == "__main__":
    main()
