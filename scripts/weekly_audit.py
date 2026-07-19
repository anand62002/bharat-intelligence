"""
scripts/weekly_audit.py — Bharat Intelligence Weekly Health Audit
=================================================================
Runs every Sunday at 07:45 IST via worker.py.
Can also be run manually at any time:
  python -m scripts.weekly_audit [--days N] [--json]

Checks 9 health dimensions and logs a structured pass/warn/fail report
to stdout (visible in Railway logs under job_weekly_audit).

Exit code: 0 = all pass/warn, 1 = any FAIL
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ── Ensure project root is on sys.path ────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv()

log = logging.getLogger(__name__)

# ── Status constants ───────────────────────────────────────────────────────────
PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"


def _supabase():
    """Return Supabase client or None."""
    try:
        from supabase import create_client
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_SERVICE_KEY", "")
        if not url or not key:
            return None
        return create_client(url, key)
    except Exception:
        return None


# =============================================================================
# Audit checks
# =============================================================================

def check_synthesis_kappa(client, days: int = 7) -> dict[str, Any]:
    """
    Query recommendations table for aggregate_kappa from gov_check JSONB.
    Returns distribution, suppression rate, and trend.
    """
    since = (date.today() - timedelta(days=days)).isoformat()
    try:
        rows = (
            client.table("recommendations")
            .select("action, confidence, gov_check, created_at")
            .gte("created_at", since)
            .execute()
            .data
        )
    except Exception as exc:
        return {"status": WARN, "detail": f"DB query failed: {exc}"}

    total = len(rows)
    if total == 0:
        return {
            "status": WARN,
            "detail": f"No recommendations in last {days} days",
            "total": 0,
        }

    suppressed = [r for r in rows if r.get("action") == "SUPPRESSED"]
    suppression_rate = len(suppressed) / total * 100

    # Extract kappa values from gov_check JSONB
    kappas: list[float] = []
    for r in rows:
        gc = r.get("gov_check") or {}
        if isinstance(gc, str):
            try:
                gc = json.loads(gc)
            except Exception:
                gc = {}
        val = gc.get("validation") or {}
        k = val.get("aggregate_kappa")
        if k is not None:
            try:
                kappas.append(float(k))
            except (TypeError, ValueError):
                pass

    if not kappas:
        return {
            "status": WARN,
            "detail": "No kappa values found in gov_check.validation — kappa may not be stored",
            "total": total,
            "suppressed": len(suppressed),
            "suppression_rate_pct": round(suppression_rate, 1),
        }

    avg_kappa = sum(kappas) / len(kappas)
    min_kappa = min(kappas)
    low_kappa_count = sum(1 for k in kappas if k < 0.35)

    # Status thresholds:
    # suppression >80% of total in a day = likely data degradation
    # avg kappa < 0.35 = borderline quality
    if suppression_rate > 80:
        status = FAIL
        detail = f"Suppression rate {suppression_rate:.0f}% — very high, all agents likely SUPPRESSED (data degradation day)"
    elif suppression_rate > 50:
        status = WARN
        detail = f"Suppression rate {suppression_rate:.0f}% — elevated, Trendlyne/screener data may be degraded"
    elif avg_kappa < 0.35:
        status = WARN
        detail = f"Average kappa {avg_kappa:.3f} below 0.35 — judge disagreement higher than expected"
    else:
        status = PASS
        detail = f"avg_kappa={avg_kappa:.3f}  suppression_rate={suppression_rate:.0f}%"

    return {
        "status": status,
        "detail": detail,
        "total_recs": total,
        "suppressed": len(suppressed),
        "suppression_rate_pct": round(suppression_rate, 1),
        "kappa_values_found": len(kappas),
        "avg_kappa": round(avg_kappa, 3),
        "min_kappa": round(min_kappa, 3),
        "low_kappa_count": low_kappa_count,
    }


def check_daily_runs(client, days: int = 7) -> dict[str, Any]:
    """Check daily_runs for DATA_DEGRADATION or WARNING days."""
    since = (date.today() - timedelta(days=days)).isoformat()
    try:
        rows = (
            client.table("daily_runs")
            .select("run_date, status, errors, duration_seconds")
            .gte("run_date", since)
            .order("run_date", desc=True)
            .execute()
            .data
        )
    except Exception as exc:
        return {"status": WARN, "detail": f"DB query failed: {exc}"}

    if not rows:
        return {
            "status": WARN,
            "detail": f"No daily_runs rows in last {days} days — orchestrator may not be writing",
        }

    total = len(rows)
    degradation_days = [r for r in rows if r.get("status") == "DATA_DEGRADATION"]
    warning_days = [r for r in rows if r.get("status") == "WARNING"]
    ok_days = [r for r in rows if r.get("status") == "OK"]

    avg_duration = sum(r.get("duration_seconds", 0) for r in rows) / total

    if len(degradation_days) >= 3:
        status = FAIL
        detail = f"{len(degradation_days)}/{total} days DATA_DEGRADATION — screener/Trendlyne likely down"
    elif len(degradation_days) >= 1:
        status = WARN
        detail = f"{len(degradation_days)} degradation day(s) in last {days} days: {[r['run_date'] for r in degradation_days]}"
    elif len(warning_days) > total // 2:
        status = WARN
        detail = f"{len(warning_days)}/{total} days WARNING status"
    else:
        status = PASS
        detail = f"{len(ok_days)}/{total} days OK  avg_duration={avg_duration:.0f}s"

    return {
        "status": status,
        "detail": detail,
        "total_days": total,
        "ok": len(ok_days),
        "warning": len(warning_days),
        "data_degradation": len(degradation_days),
        "avg_duration_s": round(avg_duration, 1),
    }


def check_alpha_live_coverage(client) -> dict[str, Any]:
    """
    Check how many recommendation_outcomes rows have alpha_live populated.
    A low coverage (< 50%) after 30+ days indicates the forward poller is failing.
    """
    try:
        all_rows = (
            client.table("recommendation_outcomes")
            .select("id, symbol, alpha_live, entry_price, rec_date")
            .execute()
            .data
        )
    except Exception as exc:
        return {"status": WARN, "detail": f"DB query failed: {exc}"}

    total = len(all_rows)
    if total == 0:
        return {"status": WARN, "detail": "recommendation_outcomes table is empty — P5-C seeder may not have run"}

    with_alpha = [r for r in all_rows if r.get("alpha_live") is not None]
    null_entry = [r for r in all_rows if not r.get("entry_price")]
    coverage_pct = len(with_alpha) / total * 100

    # Rows older than 3 days should have alpha_live if forward poller is running
    cutoff = (date.today() - timedelta(days=3)).isoformat()
    old_rows = [r for r in all_rows if r.get("rec_date", "") < cutoff]
    old_without_alpha = [r for r in old_rows if r.get("alpha_live") is None]

    if len(null_entry) > 0 and len(with_alpha) == 0:
        status = FAIL
        detail = (
            f"ALL {total} rows missing alpha_live. "
            f"{len(null_entry)} rows have NULL entry_price — forward poller skipping them. "
            "Run run_forward_polling() with backfill enabled."
        )
    elif len(old_without_alpha) > len(old_rows) * 0.5 and len(old_rows) > 5:
        status = WARN
        detail = (
            f"{len(old_without_alpha)}/{len(old_rows)} rows older than 3 days still lack alpha_live. "
            "Forward poller may be failing — check 16:30 IST job logs."
        )
    elif coverage_pct < 30:
        status = WARN
        detail = f"Only {coverage_pct:.0f}% of rows have alpha_live ({len(with_alpha)}/{total})"
    else:
        status = PASS
        detail = f"{len(with_alpha)}/{total} rows have alpha_live ({coverage_pct:.0f}%)"

    return {
        "status": status,
        "detail": detail,
        "total": total,
        "with_alpha_live": len(with_alpha),
        "coverage_pct": round(coverage_pct, 1),
        "null_entry_price": len(null_entry),
        "old_rows_without_alpha": len(old_without_alpha),
    }


def check_trendlyne_session(client) -> dict[str, Any]:
    """
    Check Trendlyne session validity by hitting /api/debug/scraper-health
    if the API is reachable, or by attempting a direct Trendlyne probe.
    """
    api_url = os.getenv("REACT_APP_API_URL") or os.getenv("API_URL", "")
    api_key = os.getenv("DASHBOARD_API_KEY", "")

    if api_url:
        try:
            import requests
            url = api_url.rstrip("/") + "/api/debug/scraper-health"
            r = requests.get(url, headers={"x-api-key": api_key}, timeout=20)
            if r.status_code == 200:
                data = r.json()
                tl = data.get("trendlyne", {})
                sc = data.get("screener", {})

                tl_ok = tl.get("status") == "ok"
                sc_ok = sc.get("status") == "ok"

                if not tl_ok and not sc_ok:
                    return {
                        "status": FAIL,
                        "detail": "Both Trendlyne AND screener.in unreachable — DATA DEGRADATION risk",
                        "trendlyne": tl,
                        "screener": sc,
                    }
                elif not tl_ok:
                    return {
                        "status": WARN,
                        "detail": f"Trendlyne unreachable: {tl.get('error', 'unknown')} — F&O/analyst data will be stale",
                        "trendlyne": tl,
                        "screener": sc,
                    }
                else:
                    return {
                        "status": PASS,
                        "detail": f"Trendlyne OK ({tl.get('latency_ms', '?')}ms)  Screener {'OK' if sc_ok else 'UNREACHABLE'}",
                        "trendlyne": tl,
                        "screener": sc,
                    }
        except Exception as exc:
            pass  # fall through to env-var check

    # Fallback: just check if session cookies are set
    tl_session = os.getenv("TRENDLYNE_SESSION", "")
    tl_csrf = os.getenv("TRENDLYNE_CSRF", "")
    if not tl_session or not tl_csrf:
        return {
            "status": FAIL,
            "detail": "TRENDLYNE_SESSION or TRENDLYNE_CSRF env var not set — F&O data unavailable",
        }

    return {
        "status": WARN,
        "detail": "Trendlyne cookies are set but connectivity not verified (API URL not configured locally)",
        "session_length": len(tl_session),
        "csrf_length": len(tl_csrf),
    }


def check_discovery_runs(client, days: int = 7) -> dict[str, Any]:
    """Check discovery_runs for last N days — are discoveries happening?"""
    since = (date.today() - timedelta(days=days)).isoformat()
    try:
        rows = (
            client.table("discovery_runs")
            .select("run_date, total_screened, total_passed, total_discoveries")
            .gte("run_date", since)
            .order("run_date", desc=True)
            .execute()
            .data
        )
    except Exception as exc:
        return {"status": WARN, "detail": f"DB query failed: {exc}"}

    if not rows:
        return {
            "status": WARN,
            "detail": f"No discovery_runs rows in last {days} days — discovery screener may not be running",
        }

    total_disc = sum(r.get("total_discoveries", 0) or 0 for r in rows)
    avg_screened = sum(r.get("total_screened", 0) or 0 for r in rows) / len(rows)
    zero_pass_days = [r for r in rows if (r.get("total_passed") or 0) == 0]

    if len(zero_pass_days) > len(rows) * 0.5:
        status = WARN
        detail = f"{len(zero_pass_days)}/{len(rows)} days had 0 passes — pre-screen may be too strict or data degraded"
    else:
        status = PASS
        detail = f"{len(rows)} runs, {total_disc} discoveries, avg {avg_screened:.0f} screened/day"

    return {
        "status": status,
        "detail": detail,
        "run_days": len(rows),
        "total_discoveries": total_disc,
        "avg_screened_per_day": round(avg_screened, 0),
        "zero_pass_days": len(zero_pass_days),
    }


def check_rag_corpus(client) -> dict[str, Any]:
    """Check RAG corpus freshness — max event_date and embedding coverage."""
    try:
        rows = (
            client.table("historical_events")
            .select("event_date, embedding")
            .execute()
            .data
        )
    except Exception as exc:
        return {"status": WARN, "detail": f"DB query failed: {exc}"}

    if not rows:
        return {"status": FAIL, "detail": "historical_events table is empty — RAG corpus not seeded"}

    total = len(rows)
    with_emb = sum(1 for r in rows if r.get("embedding"))
    dates = [r["event_date"] for r in rows if r.get("event_date")]
    max_date = max(dates) if dates else None

    # Stale if max event is >60 days old (monthly auto-seeder should prevent this)
    stale = False
    if max_date:
        age_days = (date.today() - date.fromisoformat(max_date[:10])).days
        stale = age_days > 60

    emb_pct = with_emb / total * 100

    if emb_pct < 90:
        status = WARN
        detail = f"Only {emb_pct:.0f}% of events have embeddings ({with_emb}/{total}) — RAG similarity degraded"
    elif stale:
        status = WARN
        detail = f"RAG corpus newest event is {age_days} days old ({max_date}) — auto-seeder may have stalled"
    else:
        status = PASS
        detail = f"{total} events, {emb_pct:.0f}% embedded, newest={max_date}"

    return {
        "status": status,
        "detail": detail,
        "total_events": total,
        "with_embeddings": with_emb,
        "embedding_pct": round(emb_pct, 1),
        "newest_event": max_date,
    }


def check_agent_performance(client) -> dict[str, Any]:
    """Check agent_performance table for DEGRADING trends."""
    try:
        rows = (
            client.table("agent_performance")
            .select("agent_name, trend, accuracy_90d, hallucination_rate, audit_date")
            .order("audit_date", desc=True)
            .limit(30)
            .execute()
            .data
        )
    except Exception as exc:
        return {"status": WARN, "detail": f"DB query failed: {exc}"}

    if not rows:
        return {
            "status": WARN,
            "detail": "agent_performance table is empty — performance tracker needs 180d of matured recs to populate",
        }

    degrading = [r for r in rows if r.get("trend") == "DEGRADING"]
    if degrading:
        names = list({r["agent_name"] for r in degrading})
        return {
            "status": WARN,
            "detail": f"DEGRADING agents: {names} — review their data sources",
            "degrading_agents": names,
        }

    return {
        "status": PASS,
        "detail": f"{len(rows)} performance rows, no DEGRADING agents",
        "row_count": len(rows),
    }


def check_forward_poller_recency(client) -> dict[str, Any]:
    """Check when the forward poller last ran — should run daily at 16:30 IST."""
    try:
        rows = (
            client.table("recommendation_outcomes")
            .select("live_updated_at")
            .not_.is_("live_updated_at", "null")
            .order("live_updated_at", desc=True)
            .limit(1)
            .execute()
            .data
        )
    except Exception as exc:
        return {"status": WARN, "detail": f"DB query failed: {exc}"}

    if not rows:
        return {
            "status": WARN,
            "detail": "No live_updated_at values found — forward poller may never have run or column missing",
        }

    last_update = rows[0]["live_updated_at"]
    # Parse and compute age
    try:
        last_dt = datetime.fromisoformat(last_update.replace("Z", "+00:00"))
        now_utc = datetime.now(timezone.utc)
        age_hours = (now_utc - last_dt).total_seconds() / 3600
    except Exception:
        return {"status": WARN, "detail": f"Could not parse live_updated_at: {last_update}"}

    if age_hours > 48:
        status = FAIL
        detail = f"Forward poller last ran {age_hours:.0f}h ago ({last_update}) — should run daily"
    elif age_hours > 25:
        status = WARN
        detail = f"Forward poller last ran {age_hours:.0f}h ago — missed yesterday's run?"
    else:
        status = PASS
        detail = f"Forward poller ran {age_hours:.0f}h ago ({last_update})"

    return {
        "status": status,
        "detail": detail,
        "last_live_updated_at": last_update,
        "age_hours": round(age_hours, 1),
    }


def check_outcome_seeder(client) -> dict[str, Any]:
    """Verify recommendation_outcomes is being seeded for new recs."""
    try:
        # Count recs in last 7 days
        since = (date.today() - timedelta(days=7)).isoformat()
        recs = (
            client.table("recommendations")
            .select("id")
            .gte("created_at", since)
            .not_.eq("action", "SUPPRESSED")
            .execute()
            .data
        )
        outcomes = (
            client.table("recommendation_outcomes")
            .select("rec_id")
            .gte("created_at", since)
            .execute()
            .data
        )
    except Exception as exc:
        return {"status": WARN, "detail": f"DB query failed: {exc}"}

    rec_count = len(recs)
    outcome_count = len(outcomes)

    if rec_count == 0:
        return {"status": WARN, "detail": "No non-SUPPRESSED recommendations in last 7 days"}

    seeded_pct = outcome_count / rec_count * 100 if rec_count else 0

    if seeded_pct < 50:
        status = WARN
        detail = f"Only {outcome_count}/{rec_count} recs have outcome rows ({seeded_pct:.0f}%) — P5-C seeder may have failed"
    else:
        status = PASS
        detail = f"{outcome_count}/{rec_count} recs seeded ({seeded_pct:.0f}%)"

    return {
        "status": status,
        "detail": detail,
        "recs_7d": rec_count,
        "outcomes_7d": outcome_count,
        "seeded_pct": round(seeded_pct, 1),
    }


# =============================================================================
# Main audit runner
# =============================================================================

CHECKS = [
    ("synthesis_kappa",      "Synthesis Kappa Quality",      check_synthesis_kappa),
    ("daily_runs",           "Daily Orchestrator Runs",      check_daily_runs),
    ("alpha_live_coverage",  "Alpha Live Coverage",          check_alpha_live_coverage),
    ("trendlyne_session",    "Trendlyne Session",            check_trendlyne_session),
    ("discovery_runs",       "Discovery Screener Runs",      check_discovery_runs),
    ("rag_corpus",           "RAG Corpus Freshness",         check_rag_corpus),
    ("agent_performance",    "Agent Performance Trends",     check_agent_performance),
    ("forward_poller",       "Forward Poller Recency",       check_forward_poller_recency),
    ("outcome_seeder",       "Outcome Seeder Coverage",      check_outcome_seeder),
]


def run_audit(days: int = 7, emit_json: bool = False) -> dict[str, Any]:
    """
    Run all audit checks. Returns a dict with all results.
    Logs a structured pass/warn/fail summary to stdout.
    """
    client = _supabase()
    if not client:
        log.error("WEEKLY AUDIT FAILED — cannot connect to Supabase (check SUPABASE_URL + SUPABASE_SERVICE_KEY)")
        return {"status": FAIL, "error": "no_supabase_connection"}

    results: dict[str, Any] = {}
    for key, label, fn in CHECKS:
        try:
            if fn.__code__.co_varnames[:2] == ("client", "days") and "days" in fn.__code__.co_varnames:
                r = fn(client, days=days)
            else:
                r = fn(client)
        except Exception as exc:
            r = {"status": WARN, "detail": f"Check raised exception: {exc}"}
        results[key] = {**r, "label": label}

    # ── Summary ──────────────────────────────────────────────────────────────
    fails = [k for k, v in results.items() if v.get("status") == FAIL]
    warns = [k for k, v in results.items() if v.get("status") == WARN]
    passes = [k for k, v in results.items() if v.get("status") == PASS]

    overall = FAIL if fails else WARN if warns else PASS

    summary = {
        "audit_date": date.today().isoformat(),
        "overall_status": overall,
        "pass": len(passes),
        "warn": len(warns),
        "fail": len(fails),
        "checks": results,
    }

    # ── Structured log output ─────────────────────────────────────────────────
    log.info("=" * 70)
    log.info("  BHARAT INTELLIGENCE — WEEKLY HEALTH AUDIT  %s", date.today().isoformat())
    log.info("=" * 70)
    log.info("  Overall: %s  |  PASS=%d  WARN=%d  FAIL=%d", overall, len(passes), len(warns), len(fails))
    log.info("-" * 70)
    for key, v in results.items():
        icon = "✓" if v["status"] == PASS else "⚠" if v["status"] == WARN else "✗"
        log.info("  [%s] %s — %s", v["status"], v.get("label", key), v.get("detail", ""))
        if v["status"] in (WARN, FAIL):
            # Log extra detail for actionable items
            extras = {k: val for k, val in v.items() if k not in ("status", "detail", "label")}
            if extras:
                log.info("       → %s", json.dumps(extras))
    log.info("=" * 70)

    if fails:
        log.error("WEEKLY AUDIT: %d FAIL check(s) require immediate attention: %s", len(fails), fails)
    elif warns:
        log.warning("WEEKLY AUDIT: %d WARN check(s) require review: %s", len(warns), warns)
    else:
        log.info("WEEKLY AUDIT: All checks passed")

    if emit_json:
        print(json.dumps(summary, indent=2))

    return summary


# =============================================================================
# Entry point
# =============================================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        stream=sys.stdout,
    )

    parser = argparse.ArgumentParser(description="Bharat Intelligence Weekly Audit")
    parser.add_argument("--days", type=int, default=7, help="Lookback window in days (default: 7)")
    parser.add_argument("--json", action="store_true", help="Also print full JSON report to stdout")
    args = parser.parse_args()

    result = run_audit(days=args.days, emit_json=args.json)
    sys.exit(0 if result.get("overall_status") != FAIL else 1)
