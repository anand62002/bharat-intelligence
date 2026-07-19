"""
worker.py — Bharat Intelligence Unified Background Worker
==========================================================
Runs ALL four scheduled jobs in a single process using one APScheduler
BlockingScheduler instance. Deploy as the `worker` dyno on Railway.

Schedule (all times IST / Asia/Kolkata)
----------------------------------------
  05:30  Market Digest MORNING — overnight global cues + domestic news before orchestrator
  06:00  Daily orchestrator   — runs all 9 agents, synthesises recs (digest injected as context)
  06:30  Regime detector      — market regime classification before orchestrator
  06:55  Rec outcome seeder   — seed PENDING rows for new recs (P5-C)
  07:00  Performance tracker  — audits agent accuracy, writes agent_performance
  07:05  Paper portfolio open — open paper positions for today's BUY recs (P5-B)
  07:30  Research agent       — scans arXiv / Semantic Scholar, saves proposals
  08:00  Earnings calendar    — refresh earnings dates for portfolio + discovery symbols
  08:15  RAG refresh (1st)    — monthly RAG corpus auto-refresh
  08:30  GIFT Nifty signal    — pre-market futures premium (P6-D-7)
  08:35  Breeze token refresh — ICICI session token (DEPRECATED, P4-D)
  09:15, 11:30, 13:30, 15:15  Portfolio monitor — danger/alert checks (market hours)
  10:30  Discovery screener   — proactive stock discovery with live market prices
  15:45  Options snapshot     — F&O metrics (PCR, max pain, ATM IV)
  16:00  Portfolio risk       — concentration + correlation metrics after close
  16:15  Paper portfolio      — refresh prices, check exits, save daily snapshot (P5-B)
  16:20  Market Digest CLOSING — end-of-day recap
  16:30  Forward poller       — live price snapshot for all open recs + t+30 resolve (P5-D)
  18:30  Outcome tracker      — resolve t+90/180/365 milestones + sentiment validation (P5-D/P6-D-8)
  07:45 (Sunday)  Weekly health audit — kappa, alpha_live, Trendlyne, discovery, RAG freshness

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


def job_market_digest_morning() -> None:
    """05:30 IST — generate Morning Brief for Indian equity market (P6-C).

    Runs BEFORE the orchestrator (06:00 IST) so the digest's market_mood,
    nifty_signal, and sectors_in_focus are available to inject into every
    synthesis prompt during the main pipeline run.
    """
    log.info("-" * 50)
    log.info("  JOB START: Market Digest Morning Brief (05:30 IST)")
    log.info("-" * 50)
    try:
        from agents.market_digest import generate_digest, save_digest
        digest = generate_digest("MORNING")
        row_id = save_digest(digest)
        log.info(
            "Morning Brief generated — mood=%s headlines=%d id=%s",
            digest.get("market_mood"), digest.get("headline_count", 0), row_id,
        )
    except Exception as exc:
        log.error("Market digest morning job failed: %s", exc, exc_info=True)


def job_market_digest_closing() -> None:
    """16:20 IST — generate Closing Digest for Indian equity market (P6-C)."""
    log.info("-" * 50)
    log.info("  JOB START: Market Digest Closing (16:20 IST)")
    log.info("-" * 50)
    try:
        from agents.market_digest import generate_digest, save_digest
        digest = generate_digest("CLOSING")
        row_id = save_digest(digest)
        log.info(
            "Closing Digest generated — mood=%s headlines=%d id=%s",
            digest.get("market_mood"), digest.get("headline_count", 0), row_id,
        )
    except Exception as exc:
        log.error("Market digest closing job failed: %s", exc, exc_info=True)


def job_earnings_calendar() -> None:
    """08:00 IST — refresh earnings calendar for portfolio + recently-screened symbols (P3-C-P2)."""
    log.info("-" * 50)
    log.info("  JOB START: Earnings Calendar (08:00 IST)")
    log.info("-" * 50)
    try:
        import os
        from supabase import create_client
        client = create_client(os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_SERVICE_KEY", ""))

        # --- Source 1: Open portfolio holdings ---
        portfolio_rows = (
            client.table("portfolio_holdings")
            .select("symbol")
            .eq("status", "OPEN")
            .execute()
            .data or []
        )
        portfolio_symbols = {r["symbol"] for r in portfolio_rows}

        # --- Source 2: Recently-screened symbols (last 3 days of discovery_runs) ---
        # P3-C-P2: expand coverage beyond portfolio to symbols the screener just touched.
        # We include symbols that 'passed' the pre-screen (higher quality filter) over
        # the last 3 days so the earnings guard can block a stock before it enters
        # a full agent pipeline run the next day.
        discovery_symbols: set[str] = set()
        try:
            from datetime import date, timedelta
            cutoff = str(date.today() - timedelta(days=3))
            disc_rows = (
                client.table("discovery_runs")
                .select("passed_symbols")
                .gte("run_date", cutoff)
                .execute()
                .data or []
            )
            for row in disc_rows:
                passed = row.get("passed_symbols") or []
                if isinstance(passed, list):
                    discovery_symbols.update(s for s in passed if isinstance(s, str))
            if discovery_symbols:
                log.info(
                    "Earnings calendar: found %d recently-screened symbols from last 3 days",
                    len(discovery_symbols),
                )
        except Exception as exc:
            log.warning("Could not fetch recently-screened symbols: %s", exc)

        # Merge; portfolio holdings take priority (already in set)
        all_symbols = sorted(portfolio_symbols | discovery_symbols)
        if not all_symbols:
            log.info("No symbols to refresh — skipping earnings calendar")
            return

        log.info(
            "Earnings calendar refresh: %d total symbols (%d portfolio + %d discovery)",
            len(all_symbols),
            len(portfolio_symbols),
            len(discovery_symbols - portfolio_symbols),
        )

        from data.earnings_fetcher import fetch_upcoming_earnings, upsert_earnings_calendar
        # Use wider look-ahead for screened symbols (60d) so upcoming earnings
        # are available even if the stock is processed next cycle week
        records = fetch_upcoming_earnings(all_symbols, days_ahead=60)
        n = upsert_earnings_calendar(records)
        log.info(
            "Earnings calendar done — %d records upserted for %d symbols",
            n, len(all_symbols),
        )
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


def job_rec_outcome_seeder() -> None:
    """06:55 IST — seed PENDING outcome rows for any new recommendations (P5-C)."""
    log.info("-" * 50)
    log.info("  JOB: Rec Outcome Seeder (06:55 IST)")
    log.info("-" * 50)
    try:
        from agents.rec_outcome_seeder import run_seeder
        result = run_seeder(dry_run=False, resolve_past=False)
        log.info(
            "Seeder done — seeded=%d skipped=%d errors=%d",
            result.get("seeded", 0), result.get("skipped", 0), len(result.get("errors", [])),
        )
    except Exception as exc:
        log.error("Rec outcome seeder job failed: %s", exc, exc_info=True)


def job_outcome_tracker() -> None:
    """18:30 IST — resolve pending recommendation outcomes at 90/180/365d horizons + sentiment validation."""
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

    # ── P6-D-8: Sentiment signal validation ───────────────────────────────────
    try:
        from agents.outcome_tracker import run_sentiment_validation
        sv = run_sentiment_validation(dry_run=False)
        log.info(
            "Sentiment validation done — validated=%d accuracy_30d=%s degrading=%s",
            sv.get("validated", 0),
            f"{sv['accuracy_30d']:.1f}%" if sv.get("accuracy_30d") is not None else "N/A",
            sv.get("degrading", False),
        )
    except Exception as exc:
        log.warning("Sentiment validation job failed (non-critical): %s", exc)


def job_target_updater() -> None:
    """17:00 IST — P7-A: dynamic stoploss ratchet + target extension + laggard review."""
    log.info("-" * 50)
    log.info("  JOB START: Target Updater (17:00 IST)")
    log.info("-" * 50)
    try:
        from agents.target_updater import run_target_updates
        result = run_target_updates(dry_run=False)
        log.info(
            "Target updater done — holdings=%d  ratchets=%d  extended=%d  protect=%d  reviews=%d  errors=%d",
            result.get("total_holdings", 0),
            len(result.get("stoploss_ratchets", [])),
            len(result.get("targets_extended", [])),
            len(result.get("protect_gains", [])),
            len(result.get("laggard_reviews", [])),
            len(result.get("errors", [])),
        )
        for r in result.get("stoploss_ratchets", []):
            log.info("  🔒 SL ratchet %s: %s → ₹%.0f (%s)", r["symbol"], r.get("level"), r.get("new_sl", 0), r.get("level"))
        for r in result.get("targets_extended", []):
            log.info("  📈 Target raised %s: ₹%.0f → ₹%.0f (#%d)", r["symbol"], r.get("old_target", 0), r.get("new_target", 0), r.get("revision", 0))
        for r in result.get("protect_gains", []):
            log.info("  🛡 Protect gains %s: %s", r["symbol"], r.get("reason", ""))
        for r in result.get("laggard_reviews", []):
            log.info("  ⚠ Laggard review %s: signal=%s gain=%.1f%%", r["symbol"], r.get("signal"), r.get("gain_pct", 0))
    except Exception as exc:
        log.error("Target updater job failed: %s", exc, exc_info=True)


def job_forward_poller() -> None:
    """16:30 IST — P5-D: update live price snapshot for all pending recs + resolve t+30 milestone."""
    log.info("-" * 50)
    log.info("  JOB START: Forward Outcome Poller (16:30 IST)")
    try:
        from agents.outcome_tracker import run_forward_polling
        result = run_forward_polling(dry_run=False)
        log.info(
            "Forward poller done — polled=%d live_updated=%d t30_resolved=%d errors=%d",
            result.get("polled", 0), result.get("live_updated", 0),
            result.get("t30_resolved", 0), len(result.get("errors", [])),
        )
        if result.get("errors"):
            for e in result["errors"]:
                log.warning("  forward poller error: %s", e)
    except Exception as exc:
        log.error("Forward poller job failed: %s", exc, exc_info=True)


def job_paper_portfolio_open() -> None:
    """07:05 IST — open paper positions for any new BUY recs (P5-B)."""
    log.info("-" * 50)
    log.info("  JOB START: Paper Portfolio Open (07:05 IST)")
    log.info("-" * 50)
    try:
        from agents.paper_portfolio import open_new_positions
        from supabase import create_client
        import os
        client = create_client(os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_SERVICE_KEY", ""))
        result = open_new_positions(client, dry_run=False)
        log.info(
            "Paper portfolio open done — opened=%d skipped=%d errors=%d",
            result.get("opened", 0), result.get("skipped", 0), len(result.get("errors", [])),
        )
    except Exception as exc:
        log.error("Paper portfolio open job failed: %s", exc, exc_info=True)


def job_paper_portfolio_update() -> None:
    """16:15 IST — update open paper positions (price refresh + exit checks) + daily snapshot (P5-B)."""
    log.info("-" * 50)
    log.info("  JOB START: Paper Portfolio Update (16:15 IST)")
    log.info("-" * 50)
    try:
        from agents.paper_portfolio import update_open_positions, save_daily_snapshot
        from supabase import create_client
        import os
        client = create_client(os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_SERVICE_KEY", ""))
        upd = update_open_positions(client, dry_run=False)
        log.info(
            "Paper portfolio update done — updated=%d closed=%d errors=%d",
            upd.get("updated", 0), upd.get("closed", 0), len(upd.get("errors", [])),
        )
        for cd in upd.get("closed_details", []):
            log.info(
                "  Closed %s (%s): pnl=%.1f%% alpha=%s",
                cd["symbol"], cd["exit_reason"],
                cd.get("realized_pnl_pct", 0),
                f"{cd['alpha_pct']:.1f}%" if cd.get("alpha_pct") is not None else "N/A",
            )
        save_daily_snapshot(client, dry_run=False)
        log.info("Daily snapshot saved")
    except Exception as exc:
        log.error("Paper portfolio update job failed: %s", exc, exc_info=True)


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


def job_gift_nifty_fetch() -> None:
    """08:30 IST — fetch GIFT Nifty pre-market signal (P6-D-7)."""
    log.info("-" * 50)
    log.info("  JOB START: GIFT Nifty Fetch (08:30 IST)")
    log.info("-" * 50)
    try:
        from data.gift_nifty_fetcher import get_gift_nifty_signal
        signal = get_gift_nifty_signal(force_refresh=True)
        log.info(
            "GIFT Nifty: %s %s (%.2f pts / %.3f%%) source=%s",
            signal.get("signal", "?"),
            signal.get("signal_strength", "?"),
            signal.get("premium_pts") or 0,
            signal.get("premium_pct") or 0,
            signal.get("source", "?"),
        )
        log.info("  %s", signal.get("market_note", ""))
    except Exception as exc:
        log.error("GIFT Nifty fetch job failed: %s", exc, exc_info=True)


def job_breeze_token_refresh() -> None:
    """08:30 IST — refresh ICICI Breeze session token before market open."""
    log.info("-" * 50)
    log.info("  JOB START: Breeze Token Refresh (08:30 IST)")
    log.info("-" * 50)
    try:
        from data.breeze_auth import refresh_session
        result = refresh_session(dry_run=False)
        mode = result.get("mode", "?")
        if result.get("success"):
            log.info(
                "Breeze token refresh OK — mode=%s hours_remaining=%s",
                mode, result.get("hours_remaining"),
            )
        else:
            log.warning(
                "Breeze token refresh FAILED — mode=%s: %s",
                mode, result.get("message"),
            )
    except Exception as exc:
        log.error("Breeze token refresh job failed: %s", exc, exc_info=True)


def job_discovery_screener() -> None:
    """10:30 IST — proactive discovery screener (P6 schedule redesign).

    Moved from the orchestrator's LangGraph pipeline (was at 06:00 IST) to a
    standalone post-open job at 10:30 IST. Running after market open means:
    - Live intraday prices used instead of yesterday's close
    - RSI / EMA signals reflect today's opening moves
    - Overnight gap-ups/downs already priced in
    - GIFT Nifty pre-market signal already processed by orchestrator
    """
    log.info("-" * 50)
    log.info("  JOB START: Discovery Screener (10:30 IST — live prices)")
    log.info("-" * 50)
    try:
        from agents.discovery_screener import run_discovery
        results = run_discovery(save_to_db=True)
        log.info(
            "Discovery screener done — %d discoveries found",
            len(results),
        )
        for dr in results:
            log.info(
                "  [%s] %s upside=%.1f%% conf=%.1f",
                dr.opportunity_tier, dr.symbol, dr.upside_pct, dr.upside_confidence,
            )
    except Exception as exc:
        log.error("Discovery screener job failed: %s", exc, exc_info=True)


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


def job_rag_refresh() -> None:
    """1st of every month, 08:15 IST — fetch new India market events and append to RAG corpus."""
    log.info("-" * 50)
    log.info("  JOB START: RAG Corpus Auto-Refresh (08:15 IST, 1st of month)")
    log.info("-" * 50)
    try:
        from db.auto_seed_rag import run as rag_run
        result = rag_run(days=35, max_new=30, dry_run=False)
        log.info(
            "RAG refresh done — added=%d skipped_dup=%d skipped_irrel=%d errors=%d articles_checked=%d",
            result.get("added", 0),
            result.get("skipped_duplicate", 0),
            result.get("skipped_irrelevant", 0),
            result.get("errors", 0),
            result.get("articles_checked", 0),
        )
        if result.get("errors", 0) > 0:
            log.warning("RAG refresh had %d errors — check logs above", result["errors"])
    except Exception as exc:
        log.error("RAG corpus refresh job failed: %s", exc, exc_info=True)


def job_weekly_audit() -> None:
    """Sunday 07:45 IST — system health audit: kappa, suppression, alpha_live, Trendlyne, discovery."""
    log.info("-" * 50)
    log.info("  JOB START: Weekly Health Audit (Sunday 07:45 IST)")
    log.info("-" * 50)
    try:
        from scripts.weekly_audit import run_audit
        result = run_audit(days=7)
        status = result.get("overall_status", "?")
        log.info(
            "Weekly audit done — status=%s  pass=%d  warn=%d  fail=%d",
            status,
            result.get("pass", 0),
            result.get("warn", 0),
            result.get("fail", 0),
        )
        if result.get("fail", 0) > 0:
            fails = [k for k, v in result.get("checks", {}).items() if v.get("status") == "FAIL"]
            log.error("AUDIT FAIL items: %s", fails)
    except Exception as exc:
        log.error("Weekly audit job failed: %s", exc, exc_info=True)


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

    # ── GIFT Nifty pre-market signal (P6-D-7) ────────────────────────────────
    # 08:30 IST: 45 min before NSE open; feeds macro.py + portfolio_monitor pre-open alert.
    scheduler.add_job(
        job_gift_nifty_fetch,
        CronTrigger(hour=8, minute=30, timezone=IST),
        id="gift_nifty_fetch",
        name="GIFT Nifty Pre-Market Signal (08:30 IST)",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
    )

    # ── Breeze token refresh — before market open ─────────────────────────────
    # Runs at 08:35 IST (slightly after GIFT Nifty).
    # DEPRECATED (P4-D) — will be removed when Angel One SmartAPI is integrated.
    scheduler.add_job(
        job_breeze_token_refresh,
        CronTrigger(hour=8, minute=35, timezone=IST),
        id="breeze_token_refresh",
        name="Breeze Token Refresh",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
    )

    # ── Market Digest — Morning Brief (P6-C) ─────────────────────────────────
    # Runs at 05:30 IST: 30 min before orchestrator (06:00).
    # This is the key change from the previous schedule (was 08:45 IST, AFTER
    # the orchestrator ran). Now the digest runs first so its market_mood /
    # nifty_signal / sectors_in_focus are available to inject into every
    # synthesis prompt during the 06:00 orchestrator run.
    scheduler.add_job(
        job_market_digest_morning,
        CronTrigger(hour=5, minute=30, timezone=IST),
        id="market_digest_morning",
        name="Market Digest Morning Brief (05:30 IST)",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
    )

    # ── Market Digest — Closing Digest (P6-C) ────────────────────────────────
    # Runs at 16:20 IST: after market close (15:30), before paper portfolio (16:15).
    # Actually 16:20 is after paper portfolio (16:15) but before forward poller (16:30).
    scheduler.add_job(
        job_market_digest_closing,
        CronTrigger(hour=16, minute=20, timezone=IST),
        id="market_digest_closing",
        name="Market Digest Closing Digest (16:20 IST)",
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

    # ── Paper portfolio — open new positions after orchestrator (P5-B) ──────
    # Runs at 07:05 — after rec_outcome_seeder (06:55) which seeds PENDING rows.
    # Opens paper positions for any new BUY recs from today's orchestrator run.
    scheduler.add_job(
        job_paper_portfolio_open,
        CronTrigger(hour=7, minute=5, timezone=IST),
        id="paper_portfolio_open",
        name="Paper Portfolio Open (07:05 IST)",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
    )

    # ── Paper portfolio — price update + exits after market close (P5-B) ────
    # Runs at 16:15 — after market close (15:30), after portfolio_risk (16:00).
    # Refreshes prices, checks stoploss/target/horizon exits, saves daily snapshot.
    scheduler.add_job(
        job_paper_portfolio_update,
        CronTrigger(hour=16, minute=15, timezone=IST),
        id="paper_portfolio_update",
        name="Paper Portfolio Update (16:15 IST)",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
    )

    # ── Forward outcome poller — live snapshot + t+30 milestone (P5-D) ─────────
    # Runs at 16:30 — after paper portfolio update (16:15).
    # Batch-fetches current prices for all PENDING recs, writes alpha_live /
    # return_live / days_live to recommendation_outcomes. Also resolves t+30
    # milestone once 30 days have elapsed. Gives Performance tab live data.
    scheduler.add_job(
        job_forward_poller,
        CronTrigger(hour=16, minute=30, timezone=IST),
        id="forward_poller",
        name="Forward Outcome Poller (16:30 IST)",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    # ── Rec outcome seeder — after orchestrator (P5-C) ───────────────────────
    # Seeds PENDING rows for any new recs saved by the orchestrator (06:00).
    # Runs at 06:55 so orchestrator + discovery (06:00) have time to finish.
    scheduler.add_job(
        job_rec_outcome_seeder,
        CronTrigger(hour=6, minute=55, timezone=IST),
        id="rec_outcome_seeder",
        name="Rec Outcome Seeder",
        max_instances=1,
        coalesce=True,
    )

    # ── Target updater — after forward poller (P7-A) ─────────────────────────
    # 17:00 IST: after paper portfolio (16:15), closing digest (16:20),
    # and forward poller (16:30). Uses final closing prices from yfinance.
    # Runs: stoploss ratchet + target extension (warren_bot) + laggard review.
    scheduler.add_job(
        job_target_updater,
        CronTrigger(hour=17, minute=0, timezone=IST),
        id="target_updater",
        name="Dynamic Target & Stoploss Updater (17:00 IST)",
        max_instances=1,
        coalesce=True,
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

    # ── Discovery screener — post-market-open with live prices ───────────────
    # 10:30 IST: ~75 min after NSE open (09:15). Using live intraday prices
    # means RSI/EMA signals reflect today's real moves, not yesterday's close.
    # Moved out of the orchestrator pipeline so it no longer blocks the morning
    # synthesis run and can use the freshest available price data.
    scheduler.add_job(
        job_discovery_screener,
        CronTrigger(hour=10, minute=30, timezone=IST),
        id="discovery_screener",
        name="Discovery Screener — Live Prices (10:30 IST)",
        max_instances=1,
        coalesce=True,
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

    # ── Weekly health audit — every Sunday, 07:45 IST ───────────────────────
    # Runs after performance tracker (07:00) and research agent (07:30).
    # Checks: kappa suppression rate, alpha_live coverage, Trendlyne session,
    # daily_run status, discovery runs, RAG corpus freshness, forward poller recency.
    scheduler.add_job(
        job_weekly_audit,
        CronTrigger(day_of_week="sun", hour=7, minute=45, timezone=IST),
        id="weekly_audit",
        name="Weekly Health Audit (Sunday 07:45 IST)",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=7200,
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

    # ── Monthly RAG corpus refresh — 1st of month, 08:15 IST ─────────────────
    # After backtest (07:45) and earnings calendar (08:00). Fetches India macro
    # market events from Google News RSS, classifies them, and appends novel
    # events with embeddings to historical_events table.
    scheduler.add_job(
        job_rag_refresh,
        CronTrigger(day=1, hour=8, minute=15, timezone=IST),
        id="rag_refresh_monthly",
        name="Monthly RAG Corpus Refresh (08:15 IST, 1st)",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
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
        job_rec_outcome_seeder()
        job_paper_portfolio_open()
        job_forward_poller()
        job_outcome_tracker()
        job_paper_portfolio_update()
        job_options_snapshot()
        job_rag_refresh()
        job_gift_nifty_fetch()
        job_market_digest_morning()
        job_market_digest_closing()
        job_discovery_screener()
        job_target_updater()
        job_weekly_audit()
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
    log.info("    08:30  Breeze Token Refresh")
    log.info("    07:00  Performance Tracker")
    log.info("    07:30  Research Agent")
    log.info("    09:15  Portfolio Monitor")
    log.info("    11:30  Portfolio Monitor")
    log.info("    13:30  Portfolio Monitor")
    log.info("    15:15  Portfolio Monitor")
    log.info("    15:45  Options Market Snapshot")
    log.info("    16:00  Portfolio Risk")
    log.info("    07:05  Paper Portfolio Open (P5-B)")
    log.info("    16:15  Paper Portfolio Update (P5-B)")
    log.info("    18:30  Outcome Tracker")
    log.info("    07:45 (1st/month)  Monthly Backtest")
    log.info("    08:15 (1st/month)  RAG Corpus Auto-Refresh")
    log.info("    08:45  Market Digest Morning Brief (P6-C)")
    log.info("    16:20  Market Digest Closing Digest (P6-C)")
    log.info("  Press Ctrl+C to stop")
    log.info("=" * 60)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Worker stopped cleanly")


if __name__ == "__main__":
    main()
