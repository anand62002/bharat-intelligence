"""
scheduler/invariants.py — what a healthy day looks like, per job (WP-006, ADR-005).

Why this exists
---------------
The 06:00 portfolio pipeline ran every day for months, produced nothing and
reported success. A job ledger (WP-005) records that a job ran; this module
says what it must have PRODUCED. check_invariants() reads one day's
daily_runs rows and returns every breach:

  MISSING   the job was due and left no row
  FAILED    its latest row has status FAILED
  OUTPUT    it ran but its output breaks the job's rule (e.g. 0 recommendations
            while holdings exist, < 90% of open recs repriced)

job_watchdog (worker.py, 19:00 IST) turns each breach into one CRITICAL
portfolio_alerts row (alert_type JOB_INVARIANT, one per job per day).
/api/system/health and scripts/weekly_audit.py show the same breaches.

Adding a job: append an Invariant. `days` is "daily" (every calendar day) or
"trading" (NSE trading days only — skipped on weekends and on weekdays with no
Nifty 50 bar, i.e. exchange holidays). `check` receives the job's latest row
for the day plus a Context and returns a breach message or None.

Context counts (open holdings) are read once, at check time. For past days in
the weekly audit they are today's numbers — an approximation, noted in the
breach text.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

ALERT_TYPE = "JOB_INVARIANT"


@dataclass
class Context:
    open_holdings: Optional[int] = None
    live_reviews: Optional[int] = None     # WP-021: live HOLDING_REVIEW rows on the run date


@dataclass
class Invariant:
    job: str                       # daily_runs.pipeline
    days: str                      # "daily" | "trading"
    check: Callable[[dict, Context], Optional[str]] = field(default=lambda row, ctx: None)
    why: str = ""


@dataclass
class Breach:
    job: str
    run_date: str
    kind: str                      # MISSING | FAILED | OUTPUT
    message: str

    def as_dict(self) -> dict:
        return {"job": self.job, "run_date": self.run_date, "kind": self.kind,
                "message": self.message}


# ── helpers ──────────────────────────────────────────────────────────────────

def _details(row: dict) -> dict:
    d = row.get("details")
    return d if isinstance(d, dict) else {}


def _output(row: dict) -> dict:
    o = _details(row).get("output")
    return o if isinstance(o, dict) else {}


def _num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── per-job rules ────────────────────────────────────────────────────────────

def _orchestrator(row: dict, ctx: Context) -> Optional[str]:
    status = str(row.get("status") or "").upper()
    reasons = _details(row).get("reasons")
    if status not in ("OK", "WARNING"):
        return f"status {status or '?'} (reasons: {reasons})"
    recs = _num(_details(row).get("recommendations"))
    if ctx.open_holdings and (recs is None or recs < 1):
        return (f"0 recommendations published with {ctx.open_holdings} open holdings "
                f"(reasons: {reasons})")
    # WP-021 (ADR-007): exactly one live review per open holding.
    if ctx.open_holdings is not None and ctx.live_reviews is not None \
            and ctx.live_reviews != ctx.open_holdings:
        return (f"{ctx.live_reviews} live holding reviews for {ctx.open_holdings} open holdings "
                f"(expected one each)")
    return None


def _status_ok(row: dict, ctx: Context) -> Optional[str]:
    status = str(row.get("status") or "").upper()
    return None if status == "OK" else f"status {status or '?'}"


def _forward_poller(row: dict, ctx: Context) -> Optional[str]:
    out = _output(row)
    polled, updated = _num(out.get("polled")), _num(out.get("live_updated"))
    if polled is None or updated is None:
        return "output missing polled/live_updated"
    if polled > 0 and updated < 0.9 * polled:
        return f"repriced {int(updated)}/{int(polled)} open recs (< 90%)"
    return None


def _paper_update(row: dict, ctx: Context) -> Optional[str]:
    out = _output(row)
    snap = out.get("snapshot")
    if not isinstance(snap, dict) or not snap:
        return "no daily snapshot saved"
    open_n = _num(snap.get("open_positions")) or 0
    moved = (_num(out.get("updated")) or 0) + (_num(out.get("closed")) or 0)
    if open_n > 0 and moved == 0:
        return f"0 of {int(open_n)} open paper positions updated"
    return None


def _discovery_rescorer(row: dict, ctx: Context) -> Optional[str]:
    out = _output(row)
    symbols = _num(out.get("symbols")) or 0
    errors = out.get("errors") or []
    n_err = len(errors) if isinstance(errors, list) else int(_num(errors) or 0)
    if symbols > 0 and n_err >= symbols:
        return f"every live call failed its re-check ({n_err} errors / {int(symbols)} symbols)"
    return None


def _target_updater(row: dict, ctx: Context) -> Optional[str]:
    n = _num(_output(row).get("total_holdings"))
    if ctx.open_holdings and not n:
        return f"reviewed 0 holdings with {ctx.open_holdings} open"
    return None


INVARIANTS: list[Invariant] = [
    Invariant("orchestrator", "daily", _orchestrator,
              "published ≥1 recommendation and one live review per open holding"),
    Invariant("discovery", "trading", _status_ok, "screener completed"),
    Invariant("forward_poller", "trading", _forward_poller, "≥90% of open recs repriced"),
    Invariant("paper_portfolio_update", "trading", _paper_update,
              "open paper positions updated and a snapshot saved"),
    Invariant("discovery_rescorer", "trading", _discovery_rescorer,
              "live discovery calls re-checked"),
    Invariant("outcome_tracker", "daily", lambda row, ctx: None, "ran (zero due is normal)"),
    Invariant("target_updater", "trading", _target_updater, "every open holding reviewed"),
]


# ── trading calendar ─────────────────────────────────────────────────────────

def is_trading_day(d: date) -> bool:
    """
    False on weekends, and on weekdays the Nifty 50 has no daily bar for
    (exchange holidays). If the lookup fails, assume it was a trading day — a
    false alarm is better than a missed one.
    """
    if d.weekday() >= 5:
        return False
    if d >= date.today():
        return True            # today's bar may not exist yet; holidays are rare
    try:
        import yfinance as yf
        hist = yf.Ticker("^NSEI").history(start=d.isoformat(),
                                          end=(d + timedelta(days=1)).isoformat())
        return not hist.empty
    except Exception as exc:
        log.warning("invariants: trading-day lookup failed for %s (%s) — assuming open", d, exc)
        return True


# ── main entry points ────────────────────────────────────────────────────────

def _load_context(client, run_date: Optional[date] = None) -> Context:
    ctx = Context()
    try:
        n = (client.table("portfolio_holdings").select("id", count="exact")
             .eq("status", "OPEN").limit(1).execute().count)
        ctx.open_holdings = int(n or 0)
    except Exception as exc:
        log.warning("invariants: could not count open holdings: %s", exc)
    try:
        d = (run_date or date.today()).isoformat()
        n = (client.table("recommendations").select("id", count="exact")
             .eq("metadata->>kind", "HOLDING_REVIEW").gte("valid_till", d)
             .limit(1).execute().count)
        ctx.live_reviews = int(n or 0)
    except Exception as exc:
        log.warning("invariants: could not count live holding reviews: %s", exc)
    return ctx


def _rows_by_job(client, run_date: date) -> dict[str, dict]:
    """Latest daily_runs row per pipeline for the date."""
    rows = (client.table("daily_runs")
            .select("pipeline,status,symbols_processed,errors,details,created_at")
            .eq("run_date", run_date.isoformat())
            .order("created_at", desc=True)
            .execute().data or [])
    latest: dict[str, dict] = {}
    for r in rows:
        latest.setdefault(r.get("pipeline") or "", r)
    return latest


def check_invariants(
    client,
    run_date: Optional[date] = None,
    *,
    trading_day: Optional[Callable[[date], bool]] = None,
    context: Optional[Context] = None,
) -> list[Breach]:
    """
    Breaches for one day. Never raises: a failure to read daily_runs is itself
    returned as a breach (job="watchdog"), because a silent checker is the
    failure mode this module exists to remove.
    """
    run_date = run_date or date.today()
    is_open = trading_day or is_trading_day
    try:
        latest = _rows_by_job(client, run_date)
    except Exception as exc:
        log.warning("invariants: could not read daily_runs for %s: %s", run_date, exc)
        return [Breach("watchdog", run_date.isoformat(), "FAILED",
                       f"could not read daily_runs: {exc}")]
    ctx = context or _load_context(client, run_date)

    open_today: Optional[bool] = None
    breaches: list[Breach] = []
    for inv in INVARIANTS:
        if inv.days == "trading":
            if open_today is None:
                open_today = is_open(run_date)
            if not open_today:
                continue
        row = latest.get(inv.job)
        if row is None:
            breaches.append(Breach(inv.job, run_date.isoformat(), "MISSING",
                                   f"no daily_runs row (expected: {inv.why})"))
            continue
        if str(row.get("status") or "").upper() == "FAILED":
            err = _details(row).get("error") or "see details.traceback"
            breaches.append(Breach(inv.job, run_date.isoformat(), "FAILED", f"job failed: {err}"))
            continue
        try:
            msg = inv.check(row, ctx)
        except Exception as exc:
            msg = f"invariant check raised: {exc}"
        if msg:
            breaches.append(Breach(inv.job, run_date.isoformat(), "OUTPUT", msg))
    return breaches


def check_range(client, end: Optional[date] = None, days: int = 7, **kw) -> list[Breach]:
    """Breaches for the `days` days ending at `end` (inclusive), oldest first."""
    end = end or date.today()
    ctx = kw.pop("context", None) or _load_context(client)
    out: list[Breach] = []
    for i in range(days - 1, -1, -1):
        out += check_invariants(client, end - timedelta(days=i), context=ctx, **kw)
    return out


def alert_title(b: Breach) -> str:
    return f"Job invariant: {b.job} {b.run_date} — {b.kind}"


def raise_alerts(client, breaches: list[Breach]) -> dict:
    """
    One CRITICAL portfolio_alerts row per breach, deduplicated per job per day
    (by title). Returns {"inserted": n, "duplicates": n, "errors": [...]}.
    """
    out = {"inserted": 0, "duplicates": 0, "errors": []}
    for b in breaches:
        title = alert_title(b)
        try:
            existing = (client.table("portfolio_alerts").select("id")
                        .eq("alert_type", ALERT_TYPE)
                        .like("title", f"Job invariant: {b.job} {b.run_date} %")
                        .limit(1).execute().data or [])
            if existing:
                out["duplicates"] += 1
                continue
            client.table("portfolio_alerts").insert({
                "severity":   "CRITICAL",
                "alert_type": ALERT_TYPE,
                "title":      title,
                "detail":     b.message,
                "resolved":   False,
            }).execute()
            out["inserted"] += 1
        except Exception as exc:
            log.warning("invariants: could not write alert for %s: %s", b.job, exc)
            out["errors"].append(f"{b.job}: {exc}")
    return out
