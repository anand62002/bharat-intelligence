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
  16:45  Discovery re-check   — one live call per symbol, stop/target closes, calibrated confidence
  12:30  Multibagger screen   — long-horizon track on today's discovery slice (P8-E)
  16:50  Long-horizon book    — open/update/close paper positions, t+180/365/730 outcomes (P8-E)
  08:00 (Sunday)  Multibagger review — re-check durability of every active long-horizon pick
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

# Every job_* below is wrapped in data.run_log.tracked_job (WP-005, ADR-005):
# one daily_runs row per run (pipeline=<job name>), status OK / WARNING /
# FAILED, the returned dict under details.output, LLM tokens under
# details.llm_usage. Jobs therefore let exceptions propagate (the decorator
# logs them at ERROR and never re-raises) and RETURN their result dict — its
# "count" becomes symbols_processed and a non-empty "errors" makes it WARNING.
# Secondary steps inside a job keep their own try and report into "errors" so
# one failed sub-step cannot hide the main step's result.
from data.run_log import tracked_job  # noqa: E402


def _service_client():
    import os
    from supabase import create_client
    return create_client(os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_SERVICE_KEY", ""))


@tracked_job("orchestrator", logs_own_row=True)
def job_orchestrator() -> dict:
    """06:00 IST — main agent pipeline. Writes its own daily_runs row (scheduler/orchestrator.py)."""
    log.info("=" * 60)
    log.info("  JOB START: Daily Orchestrator (06:00 IST)")
    log.info("=" * 60)
    from scheduler.orchestrator import run_pipeline
    final = asyncio.run(run_pipeline(dry_run=False))
    errs  = final.get("errors", [])
    recs  = len(final.get("recommendations", []))
    syms  = final.get("symbols_processed", 0)
    log.info("Orchestrator done — symbols=%d  recs=%d  errors=%d", syms, recs, len(errs))
    for e in errs:
        log.warning("  orchestrator error: %s", e)
    return {"count": syms, "recommendations": recs, "errors": [str(e) for e in errs]}


@tracked_job("performance_tracker")
def job_performance_tracker():
    """07:00 IST — audit agent accuracy."""
    log.info("=" * 60)
    log.info("  JOB START: Performance Tracker (07:00 IST)")
    log.info("=" * 60)
    from scheduler.performance_tracker import run as pt_run
    result = pt_run()
    log.info("Performance tracker done")
    return result


@tracked_job("research_agent")
def job_research_agent() -> dict:
    """07:30 IST — AI paper scanner."""
    log.info("=" * 60)
    log.info("  JOB START: Research Agent (07:30 IST)")
    log.info("=" * 60)
    out: dict = {"count": 0, "errors": []}
    scan_exc: Exception | None = None
    try:
        from governance.research_agent import run as ra_run
        result = ra_run(dry_run=False)
        saved = result.get("saved", 0) if isinstance(result, dict) else "?"
        log.info("Research agent done — proposals saved: %s", saved)
        out.update(count=saved if isinstance(saved, int) else 0, scan=result)
    except Exception as exc:     # held: the gates below still run, then re-raised
        log.error("Research agent scan failed: %s", exc, exc_info=True)
        scan_exc = exc

    # Close the loop: until now the agent produced proposals and debates daily
    # and nothing ever consumed the vote, so everything sat at 'pending'.
    # Opens a PR for supermajority + zero-cost proposals; never merges.
    # Runs in its own try so a gate failure cannot mask the scan above.
    try:
        from governance.research_agent import auto_approve_proposals
        gate = auto_approve_proposals(dry_run=False)
        log.info(
            "Auto-approval gate — checked=%d approved=%d skipped=%d errors=%d",
            gate.get("checked", 0), gate.get("approved", 0),
            gate.get("skipped", 0), len(gate.get("errors", [])),
        )
        for url in gate.get("pr_urls", []):
            log.info("  PR opened: %s", url)
        for err in gate.get("errors", []):
            log.warning("  auto-approval error: %s", err)
        out["auto_approve"] = gate
        out["errors"] += [f"auto_approve: {e}" for e in gate.get("errors", [])]
    except Exception as exc:
        log.error("Auto-approval gate failed: %s", exc, exc_info=True)
        out["errors"].append(f"auto_approve: {exc}")

    # Auto-merge, gated on GOVERNANCE_AUTO_MERGE=true. Stricter than auto-PR:
    # unanimous vote AND fully green CI. Merges PRs opened on EARLIER runs,
    # since CI cannot have finished for one opened moments ago.
    try:
        from governance.research_agent import auto_merge_approved_prs, auto_merge_enabled
        if auto_merge_enabled():
            mg = auto_merge_approved_prs(dry_run=False)
            log.info(
                "Auto-merge — checked=%d merged=%d skipped=%d errors=%d",
                mg.get("checked", 0), mg.get("merged", 0),
                mg.get("skipped", 0), len(mg.get("errors", [])),
            )
            for n in mg.get("merged_prs", []):
                log.info("  MERGED PR #%s", n)
            for err in mg.get("errors", []):
                log.warning("  auto-merge error: %s", err)
            out["auto_merge"] = mg
            out["errors"] += [f"auto_merge: {e}" for e in mg.get("errors", [])]
        else:
            log.info("Auto-merge disabled (GOVERNANCE_AUTO_MERGE not set)")
    except Exception as exc:
        log.error("Auto-merge failed: %s", exc, exc_info=True)
        out["errors"].append(f"auto_merge: {exc}")
    if scan_exc is not None:
        raise scan_exc          # ledger records FAILED for the main step
    return out


def _run_digest(kind: str) -> dict:
    from agents.market_digest import generate_digest, save_digest
    digest = generate_digest(kind)
    row_id = save_digest(digest)
    log.info(
        "%s digest generated — mood=%s headlines=%d id=%s",
        kind.title(), digest.get("market_mood"), digest.get("headline_count", 0), row_id,
    )
    return {"count": digest.get("headline_count", 0), "market_mood": digest.get("market_mood"),
            "nifty_signal": digest.get("nifty_signal"), "row_id": row_id,
            "errors": [] if row_id else ["digest not saved"]}


@tracked_job("market_digest_morning")
def job_market_digest_morning() -> dict:
    """05:30 IST — generate Morning Brief for Indian equity market (P6-C).

    Runs BEFORE the orchestrator (06:00 IST) so the digest's market_mood,
    nifty_signal, and sectors_in_focus are available to inject into every
    synthesis prompt during the main pipeline run.
    """
    log.info("-" * 50)
    log.info("  JOB START: Market Digest Morning Brief (05:30 IST)")
    log.info("-" * 50)
    return _run_digest("MORNING")


@tracked_job("market_digest_closing")
def job_market_digest_closing() -> dict:
    """16:20 IST — generate Closing Digest for Indian equity market (P6-C)."""
    log.info("-" * 50)
    log.info("  JOB START: Market Digest Closing (16:20 IST)")
    log.info("-" * 50)
    return _run_digest("CLOSING")


@tracked_job("earnings_calendar")
def job_earnings_calendar() -> dict:
    """08:00 IST — refresh earnings calendar for portfolio + recently-screened symbols (P3-C-P2)."""
    log.info("-" * 50)
    log.info("  JOB START: Earnings Calendar (08:00 IST)")
    log.info("-" * 50)
    client = _service_client()
    errors: list[str] = []

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
        errors.append(f"discovery_runs: {exc}")

    # Merge; portfolio holdings take priority (already in set)
    all_symbols = sorted(portfolio_symbols | discovery_symbols)
    if not all_symbols:
        log.info("No symbols to refresh — skipping earnings calendar")
        return {"count": 0, "upserted": 0, "errors": errors}

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
    return {"count": len(all_symbols), "upserted": n, "errors": errors}


@tracked_job("regime_detector")
def job_regime_detector() -> dict:
    """06:30 IST — detect market regime before orchestrator runs."""
    log.info("-" * 50)
    log.info("  JOB START: Regime Detector (06:30 IST)")
    log.info("-" * 50)
    from agents.regime_detector import detect_regime
    result = detect_regime(dry_run=False)
    log.info(
        "Regime: %s (confidence=%d%%) — nifty=%s vix=%s fii=%s",
        result.get("regime"), result.get("confidence", 0),
        result.get("nifty_trend"), result.get("vix_state"), result.get("fii_trend"),
    )
    return {"count": 1 if result.get("regime") else 0, **result}


@tracked_job("rec_outcome_seeder")
def job_rec_outcome_seeder() -> dict:
    """06:55 IST — seed PENDING outcome rows for any new recommendations (P5-C)."""
    log.info("-" * 50)
    log.info("  JOB: Rec Outcome Seeder (06:55 IST)")
    log.info("-" * 50)
    from agents.rec_outcome_seeder import run_seeder
    result = run_seeder(dry_run=False, resolve_past=False)
    log.info(
        "Seeder done — seeded=%d skipped=%d errors=%d",
        result.get("seeded", 0), result.get("skipped", 0), len(result.get("errors", [])),
    )
    return {"count": result.get("seeded", 0), **result}


@tracked_job("outcome_tracker")
def job_outcome_tracker() -> dict:
    """18:30 IST — resolve pending recommendation outcomes at 90/180/365d horizons + sentiment validation."""
    log.info("=" * 60)
    log.info("  JOB START: Outcome Tracker (18:30 IST)")
    log.info("=" * 60)
    out: dict = {"count": 0, "errors": []}
    track_exc: Exception | None = None
    try:
        from agents.outcome_tracker import run_outcome_tracking
        result = run_outcome_tracking(dry_run=False)
        log.info(
            "Outcome tracker done — tracked=%d updated=%d hits=%d misses=%d avg_alpha_90d=%s",
            result.get("tracked", 0), result.get("updated", 0),
            result.get("hits", 0), result.get("misses", 0),
            f"{result['avg_alpha_90d']:.2%}" if result.get("avg_alpha_90d") is not None else "N/A",
        )
        for e in result.get("errors") or []:
            log.warning("  outcome tracker error: %s", e)
        out = {"count": result.get("updated", 0), **result}
        out["errors"] = list(result.get("errors") or [])
    except Exception as exc:     # held: sentiment validation still runs, then re-raised
        log.error("Outcome tracker failed: %s", exc, exc_info=True)
        track_exc = exc

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
        out["sentiment_validation"] = sv
    except Exception as exc:
        log.warning("Sentiment validation job failed (non-critical): %s", exc)
        out["errors"].append(f"sentiment_validation: {exc}")
    if track_exc is not None:
        raise track_exc         # ledger records FAILED for the main step
    return out


@tracked_job("target_updater")
def job_target_updater() -> dict:
    """17:00 IST — P7-A: dynamic stoploss ratchet + target extension + laggard review."""
    log.info("-" * 50)
    log.info("  JOB START: Target Updater (17:00 IST)")
    log.info("-" * 50)
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
    return {"count": result.get("total_holdings", 0), **result}


@tracked_job("forward_poller")
def job_forward_poller() -> dict:
    """16:30 IST — P5-D: update live price snapshot for all pending recs + resolve t+30 milestone."""
    log.info("-" * 50)
    log.info("  JOB START: Forward Outcome Poller (16:30 IST)")
    from agents.outcome_tracker import run_forward_polling
    result = run_forward_polling(dry_run=False)
    log.info(
        "Forward poller done — polled=%d live_updated=%d t30_resolved=%d errors=%d",
        result.get("polled", 0), result.get("live_updated", 0),
        result.get("t30_resolved", 0), len(result.get("errors", [])),
    )
    for e in result.get("errors") or []:
        log.warning("  forward poller error: %s", e)
    return {"count": result.get("live_updated", 0), **result}


@tracked_job("discovery_rescorer")
def job_discovery_rescorer() -> dict:
    """
    16:45 IST — re-check every live discovery call: collapse duplicates, close
    calls that hit stoploss/target, re-run fundamental + technical to confirm or
    weaken the thesis, and recompute confidence as the calibrated hit rate.
    """
    log.info("-" * 50)
    log.info("  JOB START: Discovery Re-check (16:45 IST)")
    from agents.discovery_rescorer import run_rescore
    s = run_rescore(dry_run=False)
    log.info(
        "Discovery re-check done — symbols=%d superseded=%d active=%d weakened=%d "
        "stopped=%d target=%d errors=%d",
        s["symbols"], s["superseded"], s["ACTIVE"], s["WEAKENED"],
        s["STOPPED_OUT"], s["TARGET_HIT"], len(s["errors"]),
    )
    for e in s["errors"][:10]:
        log.warning("  discovery re-check error: %s", e)
    return {"count": s.get("symbols", 0), **s}


@tracked_job("multibagger_screen")
def job_multibagger_screen() -> dict:
    """12:30 IST — P8-E long-horizon screen on today's discovery slice (cached fundamentals)."""
    log.info("-" * 50)
    log.info("  JOB START: Multibagger Screen (12:30 IST)")
    from agents.multibagger_screener import run_screen
    r = run_screen(dry_run=False)
    log.info("Multibagger screen done — screened=%d prefilter=%d deep=%d picks=%d errors=%d",
             r["screened"], r["prefilter_passed"], r["deep_checked"], len(r["picks"]), len(r["errors"]))
    for e in r["errors"][:5]:
        log.warning("  multibagger error: %s", e)
    return {"count": r.get("screened", 0), **r}


@tracked_job("long_horizon_book")
def job_long_horizon_book() -> dict:
    """16:50 IST — P8-E long-horizon paper book: open, update, close, resolve outcomes."""
    log.info("-" * 50)
    log.info("  JOB START: Long-Horizon Book (16:50 IST)")
    from agents.long_horizon_book import run_book
    r = run_book(dry_run=False)
    log.info("Long-horizon book done — opened=%d closed=%d updated=%d resolved=%d errors=%d",
             len(r["opened"]), len(r["closed"]), r["updated"], r["resolved"], len(r["errors"]))
    for e in r["errors"][:5]:
        log.warning("  long-horizon book error: %s", e)
    return {"count": r.get("updated", 0), **r}


@tracked_job("multibagger_review")
def job_multibagger_review() -> dict:
    """Sunday 08:00 IST — P8-E: is each active pick's quality still durable?"""
    log.info("-" * 50)
    log.info("  JOB START: Multibagger Review (Sunday 08:00 IST)")
    from agents.multibagger_screener import review_picks
    r = review_picks(dry_run=False)
    log.info("Multibagger review done — reviewed=%d broken=%d errors=%d",
             r["reviewed"], len(r["broken"]), len(r["errors"]))
    return {"count": r.get("reviewed", 0), **r}


@tracked_job("paper_portfolio_open")
def job_paper_portfolio_open() -> dict:
    """07:05 IST — open paper positions for any new BUY recs (P5-B)."""
    log.info("-" * 50)
    log.info("  JOB START: Paper Portfolio Open (07:05 IST)")
    log.info("-" * 50)
    from agents.paper_portfolio import open_new_positions
    result = open_new_positions(_service_client(), dry_run=False)
    log.info(
        "Paper portfolio open done — opened=%d skipped=%d errors=%d",
        result.get("opened", 0), result.get("skipped", 0), len(result.get("errors", [])),
    )
    return {"count": result.get("opened", 0), **result}


@tracked_job("paper_portfolio_update")
def job_paper_portfolio_update() -> dict:
    """16:15 IST — update open paper positions (price refresh + exit checks) + daily snapshot (P5-B)."""
    log.info("-" * 50)
    log.info("  JOB START: Paper Portfolio Update (16:15 IST)")
    log.info("-" * 50)
    from agents.paper_portfolio import update_open_positions, save_daily_snapshot
    client = _service_client()
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
    snap = save_daily_snapshot(client, dry_run=False)
    log.info("Daily snapshot saved")
    return {"count": upd.get("updated", 0), **upd, "snapshot": snap}


@tracked_job("portfolio_risk")
def job_portfolio_risk() -> dict:
    """16:00 IST — compute portfolio-level risk metrics after market close."""
    log.info("-" * 50)
    log.info("  JOB START: Portfolio Risk (16:00 IST)")
    log.info("-" * 50)
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
    for w in result.get("warnings") or []:
        log.warning("  portfolio risk warning: %s", w)
    # Risk warnings are findings about the portfolio, not job errors.
    out = {"count": result.get("holdings_count", 0), **result}
    out["errors"] = [str(result["error"])] if result.get("error") else []
    return out


@tracked_job("options_snapshot")
def job_options_snapshot() -> dict:
    """15:45 IST — capture options market snapshot (PCR, max pain, VIX) just before close."""
    log.info("-" * 50)
    log.info("  JOB START: Options Market Snapshot (15:45 IST)")
    log.info("-" * 50)
    from agents.options_sentiment import analyse_options
    out: dict = {"count": 0, "symbols": {}, "errors": []}
    for sym in ["NIFTY", "BANKNIFTY"]:
        try:
            r = analyse_options(sym)
        except Exception as exc:
            log.error("Options[%s] failed: %s", sym, exc, exc_info=True)
            out["errors"].append(f"{sym}: {exc}")
            continue
        log.info(
            "Options[%s] signal=%s score=%s pcr=%s vix=%s source=%s",
            sym, r.get("signal"), r.get("score"), r.get("pcr"), r.get("india_vix"), r.get("source"),
        )
        out["symbols"][sym] = r
        out["count"] += 1
    return out


@tracked_job("gift_nifty_fetch")
def job_gift_nifty_fetch() -> dict:
    """08:30 IST — fetch GIFT Nifty pre-market signal (P6-D-7)."""
    log.info("-" * 50)
    log.info("  JOB START: GIFT Nifty Fetch (08:30 IST)")
    log.info("-" * 50)
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
    # UNAVAILABLE or a quote older than 3 days is a broken feed, not an OK run —
    # the old code faked 0.00% from ^NSEI and the ledger showed OK every day.
    errors: list[str] = []
    if signal.get("signal") == "UNAVAILABLE":
        errors.append(f"GIFT Nifty unavailable (source={signal.get('source')})")
    else:
        traded = signal.get("gift_traded_at")
        try:
            from datetime import datetime, timedelta
            age = datetime.now(IST).replace(tzinfo=None) - datetime.strptime(traded, "%d-%b-%Y %H:%M:%S")
            if age > timedelta(days=3):
                errors.append(f"GIFT Nifty quote stale: last trade {traded} IST")
        except (TypeError, ValueError):
            pass   # fallback sources carry no trade time
    ok = signal.get("signal") != "UNAVAILABLE"
    return {"count": 1 if ok else 0, **signal, "errors": errors}


@tracked_job("breeze_token_refresh")
def job_breeze_token_refresh() -> dict:
    """08:30 IST — refresh ICICI Breeze session token before market open."""
    log.info("-" * 50)
    log.info("  JOB START: Breeze Token Refresh (08:30 IST)")
    log.info("-" * 50)
    from data.breeze_auth import refresh_session
    result = refresh_session(dry_run=False)
    mode = result.get("mode", "?")
    if result.get("success"):
        log.info(
            "Breeze token refresh OK — mode=%s hours_remaining=%s",
            mode, result.get("hours_remaining"),
        )
        return {"count": 1, **result}
    log.warning(
        "Breeze token refresh FAILED — mode=%s: %s",
        mode, result.get("message"),
    )
    return {"count": 0, **result, "errors": [str(result.get("message") or "refresh failed")]}


@tracked_job("discovery", logs_own_row=True)
def job_discovery_screener() -> dict:
    """10:30 IST — proactive discovery screener (P6 schedule redesign).

    Moved from the orchestrator's LangGraph pipeline (was at 06:00 IST) to a
    standalone post-open job at 10:30 IST. Running after market open means:
    - Live intraday prices used instead of yesterday's close
    - RSI / EMA signals reflect today's opening moves
    - Overnight gap-ups/downs already priced in
    - GIFT Nifty pre-market signal already processed by orchestrator

    Writes its own daily_runs row (pipeline='discovery'); the ledger records
    only a crash.
    """
    log.info("-" * 50)
    log.info("  JOB START: Discovery Screener (10:30 IST — live prices)")
    log.info("-" * 50)
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
    return {"count": len(results), "symbols": [dr.symbol for dr in results]}


@tracked_job("portfolio_monitor")
def job_portfolio_monitor():
    """Every 2h during market hours — danger / stoploss / target alerts."""
    log.info("-" * 50)
    log.info("  JOB START: Portfolio Monitor")
    log.info("-" * 50)
    from scheduler.portfolio_monitor import run as pm_run
    result = pm_run()
    log.info("Portfolio monitor done")
    return result


@tracked_job("rag_refresh")
def job_rag_refresh() -> dict:
    """1st of every month, 08:15 IST — fetch new India market events and append to RAG corpus."""
    log.info("-" * 50)
    log.info("  JOB START: RAG Corpus Auto-Refresh (08:15 IST, 1st of month)")
    log.info("-" * 50)
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
    return {"count": result.get("added", 0), **result}


@tracked_job("weekly_audit")
def job_weekly_audit() -> dict:
    """Sunday 07:45 IST — system health audit: kappa, suppression, alpha_live, Trendlyne, discovery."""
    log.info("-" * 50)
    log.info("  JOB START: Weekly Health Audit (Sunday 07:45 IST)")
    log.info("-" * 50)
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
    fails: list = []
    if result.get("fail", 0) > 0:
        fails = [k for k, v in result.get("checks", {}).items() if v.get("status") == "FAIL"]
        log.error("AUDIT FAIL items: %s", fails)
    return {"count": len(result.get("checks", {}) or {}), **result,
            "errors": [f"audit FAIL: {k}" for k in fails]}


@tracked_job("backtest")
def job_backtest() -> dict:
    """1st of every month, 07:45 IST — walk-forward backtest on NIFTY 500 quality universe."""
    log.info("=" * 60)
    log.info("  JOB START: Monthly Backtest (07:45 IST, 1st of month)")
    log.info("=" * 60)
    from agents.backtester import run_backtest
    result = run_backtest(dry_run=False)
    if "error" in result:
        log.error("Backtest failed: %s", result["error"])
        return {"count": 0, **result, "errors": [str(result["error"])]}
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
    return {"count": result.get("symbols_processed", 0), **result}


@tracked_job("job_watchdog")
def job_watchdog() -> dict:
    """
    19:00 IST — WP-006: check today's daily_runs against scheduler/invariants.py
    and raise one CRITICAL portfolio_alerts row (alert_type JOB_INVARIANT) per
    missing, failed or zero-output job. Absence of output is the alarm.
    """
    log.info("-" * 50)
    log.info("  JOB START: Job Invariant Watchdog (19:00 IST)")
    from scheduler.invariants import check_invariants, raise_alerts
    client = _service_client()
    breaches = check_invariants(client)
    for b in breaches:
        log.error("JOB INVARIANT BREACH %s [%s]: %s", b.job, b.kind, b.message)
    alerts = raise_alerts(client, breaches)
    log.info("Watchdog done — breaches=%d alerts_inserted=%d duplicates=%d",
             len(breaches), alerts["inserted"], alerts["duplicates"])
    return {
        "count": len(breaches),
        "breaches": [b.as_dict() for b in breaches],
        "alerts": alerts,
        # Breaches make the watchdog's own row WARNING, so it is visible in the ledger too.
        "errors": [f"{b.job}: {b.kind}" for b in breaches] + alerts["errors"],
    }


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

    # ── P8-E long-horizon multibagger track ──────────────────────────────────
    # Screen after the 10:30 discovery run has cached today's slice fundamentals;
    # book after the 16:45 discovery re-check; durability review weekly.
    scheduler.add_job(
        job_multibagger_screen,
        CronTrigger(hour=12, minute=30, timezone=IST),
        id="multibagger_screen",
        name="Multibagger Screen (12:30 IST)",
        max_instances=1, coalesce=True, misfire_grace_time=3600,
    )
    scheduler.add_job(
        job_long_horizon_book,
        CronTrigger(hour=16, minute=50, timezone=IST),
        id="long_horizon_book",
        name="Long-Horizon Book (16:50 IST)",
        max_instances=1, coalesce=True, misfire_grace_time=3600,
    )
    scheduler.add_job(
        job_multibagger_review,
        CronTrigger(day_of_week="sun", hour=8, minute=0, timezone=IST),
        id="multibagger_review",
        name="Multibagger Review (Sunday 08:00 IST)",
        max_instances=1, coalesce=True, misfire_grace_time=7200,
    )

    # ── Discovery re-check — after the forward poller ────────────────────────
    # One live call per symbol, stop/target closes, thesis re-check, calibrated
    # confidence. Keeps the Discovery tab current between ~12-day screener cycles.
    scheduler.add_job(
        job_discovery_rescorer,
        CronTrigger(hour=16, minute=45, timezone=IST),
        id="discovery_rescorer",
        name="Discovery Re-check (16:45 IST)",
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

    # ── WP-006: job invariant watchdog — after the last daily job (18:30) ─────
    scheduler.add_job(
        job_watchdog,
        CronTrigger(hour=19, minute=0, timezone=IST),
        id="job_watchdog",
        name="Job Invariant Watchdog (19:00 IST)",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    return scheduler


# =============================================================================
# Entry point
# =============================================================================

def startup_schema_check() -> dict:
    """
    WP-007: compare production with db/contract.py once at start-up. A missing
    column is logged at CRITICAL (one line each) and the worker carries on — the
    writes that need it fail loudly on their own; this names the cause up front.
    Never raises.
    """
    try:
        from scripts.check_schema import check_schema, missing_only
        gaps = missing_only(check_schema(_service_client()))
    except Exception as exc:
        log.error("Schema check could not run: %s", exc)
        return {"error": str(exc)}
    for table, cols in gaps.items():
        for col in cols:
            log.critical("SCHEMA DRIFT: production %s is missing %s — code writes it "
                         "(see db/contract.py; apply the matching db/migrations file)", table, col)
    if not gaps:
        log.info("Schema check: production has every column in db/contract.py")
    return gaps

def main() -> None:
    parser = argparse.ArgumentParser(description="Bharat Intelligence Worker")
    parser.add_argument(
        "--now",
        action="store_true",
        help="Fire all jobs once immediately (for smoke-testing Railway env), then start scheduler",
    )
    args = parser.parse_args()

    startup_schema_check()

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
        job_discovery_rescorer()
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
