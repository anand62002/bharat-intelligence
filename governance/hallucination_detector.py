"""
governance/hallucination_detector.py — Bharat Intelligence Governance: Hallucination Detector
==============================================================================================
Runs weekly (Sunday 08:00 IST) to audit past recommendations and measure
per-agent directional accuracy and hallucination rates.

Algorithm
─────────
  1. Sample up to 20 past recommendations from Supabase where the horizon has
     elapsed (created_at + horizon_days <= today).
  2. For each recommendation, fetch the price at creation date and at the
     horizon end date via yfinance history.
  3. Evaluate whether each agent's individual signal was directionally correct:
       BUY  signal  → price increased over horizon        (actual_return > +2%)
       SELL signal  → price decreased over horizon        (actual_return < -2%)
       HOLD signal  → price stayed within ±10% of entry
       NO_DATA      → excluded from accuracy calculation
  4. Compute accuracy_90d per agent as:
       correct_signals / total_evaluated_signals × 100
  5. Upsert accuracy_90d into agent_performance (one row per agent per audit_date).
  6. Separately compute hallucination_rate from the gov_check data stored on
     recommendations (contradicted_count / claims_checked × 100, averaged over
     the sampled recs that have gov_check populated).
  7. Emit a portfolio_alert (severity=WARNING) for any agent whose
     hallucination_rate exceeds 1.5%.

Entry points
────────────
  run(dry_run) -> dict        Weekly job callable; also invoked by CLI.

Usage
─────
  python governance/hallucination_detector.py --run-now
  python governance/hallucination_detector.py --run-now --dry
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
MAX_SAMPLE_RECS          = 20
HALLUCINATION_ALERT_PCT  = 1.5   # alert threshold for hallucination_rate
DIRECTIONAL_BUFFER_PCT   = 2.0   # return > +2% = BUY correct, < -2% = SELL correct
HOLD_BAND_PCT            = 10.0  # within ±10% = HOLD correct
IMPROVING_THRESHOLD      = 1.0   # accuracy improvement ≥ 1 pt = IMPROVING
DEGRADING_THRESHOLD      = 1.0   # accuracy drop ≥ 1 pt = DEGRADING


# ─────────────────────────────────────────────────────────────────────────────
# Infrastructure helpers
# ─────────────────────────────────────────────────────────────────────────────

def _supabase():
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
    Return the adjusted closing price for *symbol* on *target_date*.
    Looks up to 5 calendar days forward to handle weekends/holidays.
    Returns None if no data is available.
    """
    try:
        import yfinance as yf
        from data.fetchers import yf_fetch_with_retry
        # yfinance history end date is exclusive; fetch a 7-day window
        start = target_date
        end   = target_date + timedelta(days=7)
        _t    = yf.Ticker(symbol)
        df    = yf_fetch_with_retry(
            _t.history, start=str(start), end=str(end), auto_adjust=True
        )
        if df.empty:
            return None
        # Return the first available session >= target_date
        return float(df["Close"].iloc[0])
    except Exception as exc:
        log.debug("Price fetch failed (%s, %s): %s", symbol, target_date, exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Directional accuracy helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_signal_correct(signal: str, actual_return_pct: float) -> Optional[bool]:
    """
    Determine whether a single agent signal was directionally correct.

    Returns:
        True  → signal was correct
        False → signal was wrong
        None  → signal is NO_DATA or unknown; exclude from stats
    """
    sig = (signal or "").upper()
    if sig in ("NO_DATA", "", "NEUTRAL"):
        return None   # cannot evaluate

    if sig == "BUY":
        return actual_return_pct > DIRECTIONAL_BUFFER_PCT
    if sig in ("SELL", "AVOID"):
        return actual_return_pct < -DIRECTIONAL_BUFFER_PCT
    if sig == "HOLD":
        return abs(actual_return_pct) <= HOLD_BAND_PCT

    return None  # unrecognised signal


def _evaluate_rec(rec: dict) -> dict[str, Optional[bool]]:
    """
    For one recommendation, fetch entry price and horizon-end price, then
    evaluate each agent signal.

    Returns dict mapping agent_name → True/False/None (correct/wrong/N/A).
    """
    symbol       = rec.get("symbol", "")
    created_at   = rec.get("created_at", "")
    horizon_days = int(rec.get("horizon_days") or 180)
    agent_signals= rec.get("agent_signals") or {}

    # Parse creation date
    try:
        if isinstance(created_at, str):
            # Strip timezone info if present for date extraction
            created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        elif isinstance(created_at, datetime):
            created_dt = created_at
        else:
            log.debug("Unrecognised created_at type for %s", symbol)
            return {}
        entry_date   = created_dt.date()
        horizon_date = entry_date + timedelta(days=horizon_days)
    except (ValueError, AttributeError) as exc:
        log.debug("Date parse failed for rec %s: %s", rec.get("id"), exc)
        return {}

    # Prices
    entry_price   = _fetch_price_on_date(symbol, entry_date)
    horizon_price = _fetch_price_on_date(symbol, horizon_date)

    if entry_price is None or entry_price == 0:
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

def _compute_accuracy(
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

    out = {}
    for agent, tally in tallies.items():
        total   = tally["total"]
        correct = tally["correct"]
        acc     = round((correct / total) * 100.0, 2) if total > 0 else 0.0
        out[agent] = {
            "correct":     correct,
            "total":       total,
            "accuracy_90d": acc,
        }

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Hallucination rate from gov_check data
# ─────────────────────────────────────────────────────────────────────────────

def _compute_hallucination_rates(recs: list[dict]) -> dict[str, float]:
    """
    Derive per-agent hallucination rate from the gov_check JSONB stored on
    each recommendation. Aggregates contradicted_count / claims_checked across
    all recs that have gov_check data.

    Returns:
        {agent_name: hallucination_rate_pct}   (only agents with gov_check data)
    """
    # We track per-agent claim totals and contradiction counts
    agent_totals: dict[str, dict] = {}

    for rec in recs:
        gov = rec.get("gov_check")
        if not gov or not isinstance(gov, dict):
            continue

        claim_detail = gov.get("claim_detail") or []
        for entry in claim_detail:
            agent = entry.get("agent", "unknown")
            status= str(entry.get("status", "UNVERIFIED")).upper()
            agent_totals.setdefault(agent, {"total": 0, "contradicted": 0})
            agent_totals[agent]["total"] += 1
            if status == "CONTRADICTED":
                agent_totals[agent]["contradicted"] += 1

    rates = {}
    for agent, counts in agent_totals.items():
        total = counts["total"]
        if total == 0:
            continue
        rates[agent] = round((counts["contradicted"] / total) * 100.0, 2)

    return rates


# ─────────────────────────────────────────────────────────────────────────────
# DB upsert helpers
# ─────────────────────────────────────────────────────────────────────────────

def _prev_accuracy(client, agent_name: str) -> Optional[float]:
    """Fetch the most recent accuracy_90d for an agent from agent_performance."""
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
        if rows:
            return float(rows[0].get("accuracy_90d") or 0)
    except Exception:
        pass
    return None


def _prev_hallucination_rate(client, agent_name: str) -> Optional[float]:
    """Fetch the most recent hallucination_rate for an agent."""
    try:
        resp = (
            client.table("agent_performance")
            .select("hallucination_rate")
            .eq("agent_name", agent_name)
            .order("audit_date", desc=True)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if rows and rows[0].get("hallucination_rate") is not None:
            return float(rows[0]["hallucination_rate"])
    except Exception:
        pass
    return None


def _upsert_agent_performance(
    client,
    agent_name:      str,
    accuracy_90d:    Optional[float],
    hallucination_rate: Optional[float],
    dry_run:         bool,
) -> None:
    """
    Write a new agent_performance row for today's audit.
    Computes trend by comparing new values against the most recent stored row.
    """
    today = date.today().isoformat()

    # Trend for accuracy
    acc_trend = "STABLE"
    if accuracy_90d is not None and client:
        prev_acc = _prev_accuracy(client, agent_name)
        if prev_acc is not None:
            delta = accuracy_90d - prev_acc
            if delta >= IMPROVING_THRESHOLD:
                acc_trend = "IMPROVING"
            elif delta <= -DEGRADING_THRESHOLD:
                acc_trend = "DEGRADING"

    row: dict = {
        "agent_name":  agent_name,
        "audit_date":  today,
        "trend":       acc_trend,
    }
    if accuracy_90d is not None:
        row["accuracy_90d"] = accuracy_90d
    if hallucination_rate is not None:
        row["hallucination_rate"] = hallucination_rate

    if dry_run:
        acc_str  = f"accuracy_90d={accuracy_90d:.2f}%" if accuracy_90d is not None else "accuracy_90d=N/A"
        hall_str = f"hallucination_rate={hallucination_rate:.2f}%" if hallucination_rate is not None else ""
        print(
            f"  [DRY RUN] agent_performance: {agent_name}  "
            f"{acc_str}  {hall_str}  trend={acc_trend}"
        )
        return

    if not client:
        return

    try:
        client.table("agent_performance").insert(row).execute()
        log.info("agent_performance updated: %s %s", agent_name, row)
    except Exception as exc:
        log.warning("agent_performance upsert failed for %s: %s", agent_name, exc)


def _emit_hallucination_alert(
    client,
    agent_name:         str,
    hallucination_rate: float,
    dry_run:            bool,
) -> None:
    """Create a portfolio_alert if hallucination_rate exceeds the threshold."""
    if hallucination_rate <= HALLUCINATION_ALERT_PCT:
        return

    log.warning(
        "HALLUCINATION ALERT: agent=%s rate=%.2f%% (threshold=%.1f%%)",
        agent_name, hallucination_rate, HALLUCINATION_ALERT_PCT,
    )

    if dry_run:
        print(
            f"  [DRY RUN] ALERT: agent={agent_name} "
            f"hallucination_rate={hallucination_rate:.2f}% exceeds {HALLUCINATION_ALERT_PCT}%"
        )
        return

    if not client:
        return

    try:
        client.table("portfolio_alerts").insert({
            "symbol":     agent_name,
            "severity":   "WARNING",
            "alert_type": "HIGH_HALLUCINATION_RATE",
            "title": (
                f"Agent {agent_name} hallucination rate {hallucination_rate:.2f}% "
                f"exceeds {HALLUCINATION_ALERT_PCT}% threshold"
            ),
            "detail": (
                f"Weekly hallucination audit found {hallucination_rate:.2f}% "
                f"of fact-checked claims were CONTRADICTED. "
                f"Review recent recommendations for {agent_name} agent outputs."
            ),
            "resolved": False,
        }).execute()
        log.info("Hallucination alert created for agent=%s", agent_name)
    except Exception as exc:
        log.debug("Alert insert failed for %s: %s", agent_name, exc)


# ─────────────────────────────────────────────────────────────────────────────
# Main run logic
# ─────────────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False) -> dict:
    """
    Weekly hallucination detection job.

    Steps:
      1. Load up to MAX_SAMPLE_RECS mature recommendations (horizon elapsed).
      2. For each rec, price-check entry vs horizon-end to evaluate agent
         directional accuracy.
      3. Compute accuracy_90d per agent.
      4. Compute per-agent hallucination_rate from stored gov_check data.
      5. Upsert agent_performance; emit alerts where hallucination_rate > 1.5%.

    Returns summary dict with keys:
        recs_sampled, agents_evaluated, errors, duration_seconds
    """
    t0     = time.time()
    errors: list[str] = []

    log.info("Hallucination detector: starting weekly audit (dry_run=%s)", dry_run)

    client = _supabase()
    if not client and not dry_run:
        log.error("Supabase unavailable — cannot run hallucination detector")
        return {
            "recs_sampled":      0,
            "agents_evaluated":  0,
            "errors":            ["Supabase unavailable"],
            "duration_seconds":  round(time.time() - t0, 2),
        }

    # ── Step 1: Fetch mature recommendations ─────────────────────────────────
    today          = date.today()
    cutoff_str     = today.isoformat()  # created_at + horizon_days <= today

    recs: list[dict] = []
    if client:
        try:
            # Fetch recent recs ordered by creation date; filter mature ones in Python
            # (Supabase doesn't support computed column filtering directly)
            resp = (
                client.table("recommendations")
                .select(
                    "id, symbol, created_at, horizon_days, "
                    "agent_signals, gov_check, action"
                )
                .order("created_at", desc=True)
                .limit(MAX_SAMPLE_RECS * 3)   # fetch extra; filter after
                .execute()
            )
            all_recs = resp.data or []
        except Exception as exc:
            log.error("Failed to load recommendations: %s", exc)
            return {
                "recs_sampled":     0,
                "agents_evaluated": 0,
                "errors":           [str(exc)],
                "duration_seconds": round(time.time() - t0, 2),
            }

        # Filter to mature recs (horizon has elapsed)
        for rec in all_recs:
            if len(recs) >= MAX_SAMPLE_RECS:
                break
            try:
                created_str  = rec.get("created_at", "")
                if not created_str:
                    continue
                created_dt   = datetime.fromisoformat(
                    str(created_str).replace("Z", "+00:00")
                )
                horizon_days = int(rec.get("horizon_days") or 180)
                horizon_date = created_dt.date() + timedelta(days=horizon_days)
                if horizon_date <= today:
                    recs.append(rec)
            except (ValueError, TypeError):
                continue
    else:
        # dry_run with no client — nothing to fetch but still report
        log.info("[DRY RUN] No Supabase client; skipping DB fetch")

    log.info("Sampled %d mature recommendations for accuracy audit", len(recs))

    if not recs:
        log.info("No mature recommendations found — audit complete with no updates")
        return {
            "recs_sampled":     0,
            "agents_evaluated": 0,
            "errors":           errors,
            "duration_seconds": round(time.time() - t0, 2),
        }

    # ── Step 2: Evaluate directional accuracy per agent ───────────────────────
    all_evaluations: list[dict[str, Optional[bool]]] = []
    for rec in recs:
        try:
            evals = _evaluate_rec(rec)
            if evals:
                all_evaluations.append(evals)
        except Exception as exc:
            err = f"rec {rec.get('id', '?')} [{rec.get('symbol', '?')}]: {exc}"
            log.warning("Evaluation error — %s", err)
            errors.append(err)

    # ── Step 3: Compute accuracy_90d per agent ────────────────────────────────
    accuracy_by_agent = _compute_accuracy(all_evaluations)

    # ── Step 4: Compute hallucination rates from gov_check data ───────────────
    hallucination_rates = _compute_hallucination_rates(recs)

    # ── Step 5: Collect all agent names encountered in either metric ──────────
    all_agents = sorted(
        set(accuracy_by_agent.keys()) | set(hallucination_rates.keys())
    )

    if not all_agents:
        log.info("No agent data found in sampled recs — nothing to update")
        return {
            "recs_sampled":     len(recs),
            "agents_evaluated": 0,
            "errors":           errors,
            "duration_seconds": round(time.time() - t0, 2),
        }

    # ── Step 6: Upsert and alert ──────────────────────────────────────────────
    print()
    print("-" * 70)
    print(f"  Hallucination Audit — {today}   ({len(recs)} recs sampled)")
    print("-" * 70)

    for agent in all_agents:
        acc_data = accuracy_by_agent.get(agent)
        hall_rate = hallucination_rates.get(agent)

        acc_90d = acc_data["accuracy_90d"] if acc_data else None

        # Log summary
        acc_str  = f"{acc_90d:.1f}%" if acc_90d is not None else "N/A"
        hall_str = f"{hall_rate:.2f}%" if hall_rate is not None else "N/A"
        signals  = f"{acc_data['correct']}/{acc_data['total']}" if acc_data else "N/A"

        print(
            f"  {agent:<18}  accuracy_90d={acc_str:<8}  "
            f"signals={signals:<8}  hallucination_rate={hall_str}"
        )

        _upsert_agent_performance(
            client, agent, acc_90d, hall_rate, dry_run
        )

        if hall_rate is not None:
            _emit_hallucination_alert(client, agent, hall_rate, dry_run)

    print("-" * 70)
    print()

    log.info(
        "Hallucination audit complete — %d agents evaluated in %.1fs",
        len(all_agents), time.time() - t0,
    )

    return {
        "recs_sampled":     len(recs),
        "agents_evaluated": len(all_agents),
        "accuracy_by_agent": {
            a: d["accuracy_90d"] for a, d in accuracy_by_agent.items()
        },
        "hallucination_rates": hallucination_rates,
        "errors":           errors,
        "duration_seconds": round(time.time() - t0, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# APScheduler + CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bharat Intelligence Hallucination Detector",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python governance/hallucination_detector.py                # start Sunday 08:00 IST scheduler
  python governance/hallucination_detector.py --run-now      # run immediately (writes to DB)
  python governance/hallucination_detector.py --run-now --dry # audit without DB writes
        """,
    )
    parser.add_argument(
        "--run-now", action="store_true",
        help="Execute the audit immediately instead of waiting for the schedule",
    )
    parser.add_argument(
        "--dry", action="store_true",
        help="Dry run: perform analysis but skip all Supabase writes",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    if args.run_now:
        result = run(dry_run=args.dry)
        log.info("Audit result: %s", result)
        if result.get("errors"):
            log.warning(
                "%d error(s): %s",
                len(result["errors"]),
                "; ".join(result["errors"][:5]),
            )
        return

    # ── Scheduled mode: Sunday 08:00 IST ─────────────────────────────────────
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
        log.info("Weekly hallucination audit triggered by scheduler...")
        run(dry_run=False)

    scheduler = BlockingScheduler(timezone=IST)
    scheduler.add_job(
        _job,
        CronTrigger(day_of_week="sun", hour=8, minute=0, timezone=IST),
        id="weekly_hallucination_audit",
        name="Bharat Intelligence Weekly Hallucination Detector",
        max_instances=1,
        coalesce=True,
    )

    log.info("-" * 60)
    log.info("  Bharat Intelligence Hallucination Detector")
    log.info("  Schedule: every Sunday at 08:00 IST")
    log.info("  Press Ctrl+C to stop")
    log.info("-" * 60)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped cleanly")


if __name__ == "__main__":
    main()
