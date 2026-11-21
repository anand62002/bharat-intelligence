"""
scripts/build_architect_pack.py — current-state evidence pack for an architecture review.

Why: docs drift. docs/ARCHITECTURE.md was last rewritten 2026-07-19 and still
describes Sonnet synthesis, GPT-4o-mini judges and uncalibrated confidence. An
architect (Fable) reviewing stale prose reviews a system that no longer exists.
This script reads the CODE and the LIVE DATABASE and writes one dated markdown
file of facts — measured outcomes, data health, schedule, module sizes — so the
review starts from what is true today.

Privacy: aggregates only. No portfolio holdings, symbols of personal positions,
API keys or account details are written.

Usage:
    python scripts/build_architect_pack.py          # → docs/architect_pack/CURRENT_STATE.md
"""

from __future__ import annotations

import ast
import os
import re
import statistics as st
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

OUT = ROOT / "docs" / "architect_pack" / "CURRENT_STATE.md"


def _db():
    from supabase import create_client
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _all(client, table: str, cols: str = "*", **eq) -> list[dict]:
    rows, off = [], 0
    while True:
        q = client.table(table).select(cols)
        for k, v in eq.items():
            q = q.eq(k, v)
        page = q.range(off, off + 999).execute().data or []
        rows += page
        if len(page) < 1000:
            return rows
        off += 1000


def _pct(n, d):
    return f"{n / d * 100:.1f}%" if d else "—"


Z90 = 1.6449


def _wilson(h: int, n: int, z: float = Z90) -> tuple[float, float]:
    """90 % Wilson interval for h/n (fractions)."""
    if n <= 0:
        return 0.0, 1.0
    p = h / n
    den = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return max(0.0, (c - m) / den), min(1.0, (c + m) / den)


def _rate(h: int, n: int) -> str:
    """'46.7% [38.1–55.4] (n=94)' — every rate in the pack carries its n and 90 % interval."""
    if not n:
        return "— (n=0)"
    lo, hi = _wilson(h, n)
    return f"{h / n * 100:.1f}% [{lo * 100:.1f}–{hi * 100:.1f}] (n={n})"


def _cluster_rate(ys: list[int], weeks: list) -> str:
    """Rate with a 90 % interval on weekly clusters (rows in one week are correlated)."""
    from agents.shadow_model import cluster_rate
    import numpy as np
    if not ys:
        return "— (n=0)"
    rate, lo, hi, n = cluster_rate(np.array(ys, dtype=float), weeks)
    return f"{rate * 100:.1f}% [{lo * 100:.1f}–{hi * 100:.1f}] (n={n}, {len(set(weeks))} weekly clusters)"


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


# ── Sections ─────────────────────────────────────────────────────────────────

def section_models() -> str:
    from data import model_config as mc
    return "\n".join([
        "| Role | Model |", "|---|---|",
        f"| Synthesis | `{mc.SYNTHESIS_MODEL}` |",
        f"| Validation judges | `{mc.OPENAI_CHAT_MODEL}` · `{mc.JUDGE_MODEL_SONNET}` · `{mc.JUDGE_MODEL_OPUS}` |",
        f"| Research agent | `{mc.RESEARCH_MODEL}` |",
        f"| Haiku tier (sentiment, digest, fact-check, ARIA) | `{mc.HAIKU_MODEL}` |",
    ])


def section_schedule() -> str:
    src = (ROOT / "worker.py").read_text(encoding="utf-8")
    jobs = re.findall(r'name="([^"]+)"', src)
    return "\n".join(f"- {j}" for j in jobs) + f"\n\n{len(jobs)} scheduled jobs (APScheduler, single worker process)."


def section_code() -> str:
    rows = []
    for d in ("agents", "scheduler", "governance", "data", "api"):
        for f in sorted((ROOT / d).glob("*.py")):
            if f.name.startswith("__"):
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            try:
                doc = (ast.get_docstring(ast.parse(text)) or "").strip().splitlines()
                first = next((l for l in doc if l.strip() and not set(l.strip()) <= set("=─-")), "")
            except SyntaxError:
                first = ""
            rows.append(f"| `{d}/{f.name}` | {len(text.splitlines())} | {first[:110]} |")
    app = ROOT / "dashboard" / "src" / "App.jsx"
    tests = list((ROOT / "tests").glob("test_*.py"))
    return ("| Module | Lines | Purpose (docstring) |\n|---|---|---|\n" + "\n".join(rows)
            + f"\n\nDashboard: `dashboard/src/App.jsx` {len(app.read_text(encoding='utf-8').splitlines())} lines "
              f"(single-file SPA) + `LongHorizonPanel.jsx`. Tests: {len(tests)} files.")


def section_discovery_outcomes(client) -> str:
    recs = {r["id"]: r for r in _all(client, "recommendations", "id,is_discovery,metadata,confidence,upside_confidence,upside_pct")}
    outs = _all(client, "recommendation_outcomes", "rec_id,alpha_t90,alpha_live,return_live,outcome_t30,outcome_t90")
    disc = [(recs[o["rec_id"]], o) for o in outs if o.get("rec_id") in recs and recs[o["rec_id"]].get("is_discovery")]
    a90 = [o["alpha_t90"] for _, o in disc if o.get("alpha_t90") is not None]
    live = [o["alpha_live"] for _, o in disc if o.get("alpha_live") is not None]
    lines = [
        f"- Tracked discovery calls: **{len(disc)}** (outcome rows) — non-discovery: {len(outs) - len(disc)}",
        f"- Resolved at 90 days: **{len(a90)}**, beat Nifty **{_rate(sum(1 for a in a90 if a > 0), len(a90))}**, "
        f"median alpha {st.median(a90) * 100:+.2f}%" if a90 else "- No 90-day resolutions yet",
        f"- Live (unresolved) alpha median {st.median(live) * 100:+.2f}% over {len(live)} calls" if live else "",
    ]
    try:
        from agents.call_calibration import load_calibration
        cal = load_calibration(client)
        lines.append(f"- Calibration (1 call/symbol/30d, Beta-shrunk): n={cal.n}, overall beat-Nifty {cal.overall}%")
        lines.append("  - by raw signal band: " + ", ".join(f"{b['lo']}–{b['hi']}: {b['rate']}% (n={b['n']})" for b in cal.bands))
        lines.append("  - Note: the raw score was published as 'confidence' until 2026-11-10 and ran INVERSELY to outcomes.")
    except Exception as exc:
        lines.append(f"- calibration unavailable: {exc}")
    live_calls = _all(client, "recommendations", "metadata,valid_till", is_discovery=True)
    status = Counter((r.get("metadata") or {}).get("call_status") for r in live_calls
                     if (r.get("valid_till") or "") >= date.today().isoformat())
    lines.append(f"- Live discovery calls by status: {dict(status)}")
    return "\n".join(l for l in lines if l)


def section_paper(client) -> str:
    from agents.paper_portfolio import get_portfolio_summary
    s = get_portfolio_summary(client)
    m = s["summary"]
    closed = s.get("trade_history") or []
    reasons = Counter(p.get("exit_reason") for p in closed)
    lines = [
        f"- 90-day paper book (auto-follows discovery BUYs): deployed ₹{m.get('deployed_capital', 0):,.0f}, "
        f"total P&L ₹{m.get('total_pnl', 0):,.0f} (**{m.get('total_pnl_pct')}%**), "
        f"same-trades Nifty {m.get('nifty_return_pct')}%, alpha **{m.get('alpha_pct')}%**",
        f"- Exits {dict(reasons)}",
    ]
    return "\n".join(lines + [paper_by_source(_all(client, "paper_portfolio_positions",
                                                    "rec_id,status,realized_pnl_pct,alpha_pct,position_label"),
                                               _all(client, "recommendations", "id,is_discovery,metadata"))])


def paper_by_source(positions: list[dict], recs: list[dict]) -> str:
    """Paper results split by where the BUY came from (WP-023), with n and intervals."""
    src_of = {}
    for r in recs:
        kind = (r.get("metadata") or {}).get("kind")
        src_of[r["id"]] = ("holding_review" if kind == "HOLDING_REVIEW"
                           else "discovery" if r.get("is_discovery") else "portfolio")
    by: dict[str, list[dict]] = {}
    for p in positions:
        by.setdefault(src_of.get(p.get("rec_id"), "unknown rec"), []).append(p)
    out = ["", "| Rec source | Positions | Open | Closed | Closed win rate | Closed beat-Nifty rate | Median closed alpha |",
           "|---|---|---|---|---|---|---|"]
    for src, ps in sorted(by.items()):
        closed = [p for p in ps if p.get("status") == "CLOSED"]
        pnl = [x for x in (_num(p.get("realized_pnl_pct")) for p in closed) if x is not None]
        alpha = [x for x in (_num(p.get("alpha_pct")) for p in closed) if x is not None]
        out.append(f"| {src} | {len(ps)} | {sum(p.get('status') == 'OPEN' for p in ps)} | {len(closed)} | "
                   f"{_rate(sum(x > 0 for x in pnl), len(pnl))} | {_rate(sum(x > 0 for x in alpha), len(alpha))} | "
                   f"{(st.median(alpha) if alpha else float('nan')):+.2f}% |")
    labels = Counter(p.get("position_label") or "—" for p in positions)
    out.append("")
    out.append(f"- Position sizes: {dict(labels)} (WP-019: BASE until a band's 90% lower bound clears 55%)")
    return "\n".join(out)


def section_long_horizon(client) -> str:
    from agents.long_horizon_book import get_summary
    s = get_summary(client)
    if s.get("migration_needed"):
        return "- Long-horizon tables not created."
    m, r = s["summary"], s["track_record"]
    return (f"- Long-horizon multibagger track (started 2026-11-11): {m['active_picks']} active picks, "
            f"{m['open_positions']} open paper positions, total P&L {m['total_pnl_pct']}%. "
            f"Conviction sizing {'UNLOCKED' if r['conviction_unlocked'] else 'LOCKED'} ({r['unlock_progress']}).")


def section_backtest(client) -> str:
    out = []
    for sp in ("TRAIN", "TEST", "FULL"):
        rows = (client.table("backtest_results").select("*").eq("split_type", sp)
                .order("run_date", desc=True).limit(1).execute().data or [])
        if rows:
            r = rows[0]
            out.append(f"| {sp} | {r['period_start']}→{r['period_end']} | {r['total_signals']} | "
                       f"{r['hit_rate_90d']}% | {r['avg_alpha_90d']:+.2f}% | {r['sharpe_ratio']} | {r['max_drawdown']} |")
    from agents.backtester import BACKTEST_CAVEAT
    return (f"{BACKTEST_CAVEAT} Fixed 2020–24 window → monthly reruns are near-identical.\n\n"
            "| Split | Period | Signals | Hit (beat Nifty 90d) | Avg alpha | Sharpe* | Worst trade |\n|---|---|---|---|---|---|---|\n"
            + "\n".join(out) + "\n\n*Sharpe here = mean/std of per-trade alpha, not annualised.")


def runs_by_pipeline(rows: list[dict]) -> dict[str, int]:
    """All-time daily_runs rows per pipeline (untagged rows predate WP-005)."""
    return dict(Counter((r.get("pipeline") or "(untagged, pre-WP-005)") for r in rows))


def suppression_share(orch_rows: list[dict]) -> str:
    """Suppression as a share of symbols ANALYSED (details.reasons), not of saved rows."""
    analysed = sum(int(r.get("symbols_processed") or 0) for r in orch_rows)
    reasons: Counter = Counter()
    for r in orch_rows:
        rs = (r.get("details") or {}).get("reasons") if isinstance(r.get("details"), dict) else None
        for k, v in (rs or {}).items():
            try:
                reasons[k] += int(v or 0)
            except (TypeError, ValueError):
                continue
    parts = [f"{k} {_rate(v, analysed)}" for k, v in sorted(reasons.items())]
    return (f"- Symbols analysed by the orchestrator: {analysed} over {len(orch_rows)} runs; per reason, "
            f"share of symbols analysed: " + ("; ".join(parts) if parts else "no reasons recorded"))


def section_pipeline_health(client) -> str:
    all_rows = _all(client, "daily_runs", "pipeline")
    counts = runs_by_pipeline(all_rows)
    since = (date.today() - timedelta(days=30)).isoformat()
    recent = client.table("daily_runs").select("*").gte("run_date", since).execute().data or []
    lines = ["Runs per pipeline, all time (`select pipeline, count(*) from daily_runs group by 1`):", ""]
    for name in ("orchestrator", "discovery"):
        lines.append(f"- {name} runs: {counts.pop(name, 0)}")
    others = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
    lines.append(f"- other pipelines: {others or 'none'}")
    lines.append("")
    for name in ("orchestrator", "discovery"):
        rs = [r for r in recent if r.get("pipeline") == name]
        dur = [float(r["duration_seconds"]) for r in rs if r.get("duration_seconds")]
        lines.append(f"- {name}, last 30 days: {len(rs)} runs, status {dict(Counter(r.get('status') for r in rs))}"
                     + (f", median duration {st.median(dur):.0f}s" if dur else ""))
    lines.append(suppression_share([r for r in recent if r.get("pipeline") == "orchestrator"]))
    recs = _all(client, "recommendations", "action,created_at,is_discovery,metadata")
    port = [r for r in recs if (r.get("created_at") or "") >= since and not r.get("is_discovery")]
    kinds = Counter(((r.get("metadata") or {}).get("kind") or "recommendation", r["action"]) for r in port)
    lines.append(f"- Portfolio-pipeline rows (30d) by kind and action: "
                 + ", ".join(f"{k}/{a} {n}" for (k, a), n in sorted(kinds.items())))
    return "\n".join(lines)


# ── Job ledger (WP-005/006/007/009) ──────────────────────────────────────────

def due_jobs(day: date) -> set[str]:
    """Ledger names of the worker jobs whose trigger fires on `day` (from the real schedule)."""
    import datetime as _dt
    import worker
    sched = worker.build_scheduler()
    start = _dt.datetime.combine(day, _dt.time.min, tzinfo=worker.IST)
    due = set()
    for job in sched.get_jobs():
        nxt = job.trigger.get_next_fire_time(None, start)
        if nxt and nxt.date() == day:
            due.add(getattr(job.func, "job_name", job.id))
    return due


def ledger_coverage(rows: list[dict], days: list[date], due_fn=due_jobs) -> list[tuple]:
    """(day, due, logged, missing) per day: did every scheduled job leave a daily_runs row?"""
    logged_by: dict[str, set] = {}
    for r in rows:
        logged_by.setdefault(str(r.get("run_date"))[:10], set()).add(r.get("pipeline"))
    out = []
    for d in days:
        due = due_fn(d)
        logged = logged_by.get(d.isoformat(), set())
        out.append((d, len(due), len(due & logged), sorted(due - logged)))
    return out


def ledger_start(rows: list[dict]) -> Optional[date]:
    """
    First day the WP-005 job ledger wrote a row. The orchestrator and discovery
    logged before it existed, so they don't count; earlier days would make every
    other job look missing.
    """
    days = [str(r.get("run_date"))[:10] for r in rows
            if r.get("pipeline") not in (None, "orchestrator", "discovery")]
    return date.fromisoformat(min(days)) if days else None


def allowlist_count() -> int:
    f = ROOT / "tests" / "exception_allowlist.txt"
    if not f.exists():
        return 0
    return sum(1 for l in f.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#"))


def llm_tokens(rows: list[dict]) -> list[str]:
    by: dict[str, list[int]] = {}
    for r in rows:
        u = (r.get("details") or {}).get("llm_usage") if isinstance(r.get("details"), dict) else None
        if isinstance(u, dict) and u.get("calls"):
            by.setdefault(r.get("pipeline") or "?", []).append(
                int(u.get("input_tokens") or 0) + int(u.get("output_tokens") or 0))
    return [f"  - {k}: median {int(st.median(v)):,} tokens/run over {len(v)} runs" for k, v in sorted(by.items())]


def section_job_ledger(client, days: int = 14) -> str:
    since = date.today() - timedelta(days=days)
    rows = (client.table("daily_runs").select("run_date,pipeline,status,details")
            .gte("run_date", since.isoformat()).execute().data or [])
    start = ledger_start(rows)
    if start is None:
        return "- The job ledger has no rows yet (WP-005 not deployed?)."
    first = max(since, start)
    span = [first + timedelta(days=i) for i in range((date.today() - first).days)]
    cov = ledger_coverage(rows, span)
    due_t, log_t = sum(c[1] for c in cov), sum(c[2] for c in cov)
    lines = [f"- Coverage since {first} (ledger started {start}): {_rate(log_t, due_t)} of scheduled job "
             f"runs left a `daily_runs` row", "",
             "| Day | Jobs due | Logged | Missing |", "|---|---|---|---|"]
    lines += [f"| {d} | {n_due} | {n_log} | {', '.join(miss) or '—'} |" for d, n_due, n_log, miss in cov]
    since30 = (date.today() - timedelta(days=30)).isoformat()
    try:
        alerts = (client.table("portfolio_alerts").select("title,created_at")
                  .eq("alert_type", "JOB_INVARIANT").gte("created_at", since30).execute().data or [])
        jobs = Counter((a.get("title") or "").split(":")[0] for a in alerts)
        lines += ["", f"- Invariant breaches (JOB_INVARIANT alerts), last 30 days: {len(alerts)}"
                  + (f" — {dict(jobs)}" if jobs else "")]
    except Exception as exc:
        lines += ["", f"- Invariant breaches unavailable: {exc}"]
    lines.append(f"- Exception allowlist (silent broad handlers still allowed, WP-009): {allowlist_count()}")
    try:
        from scripts.check_schema import check_schema, missing_only
        gaps = missing_only(check_schema(client))
        lines.append("- Schema contract (WP-007): " + ("production has every column the code writes" if not gaps
                     else "MISSING " + "; ".join(f"{t}: {', '.join(c)}" for t, c in gaps.items())))
    except Exception as exc:
        lines.append(f"- Schema contract check unavailable: {exc}")
    tok = llm_tokens(rows)
    lines.append("- LLM tokens per run (Anthropic, from details.llm_usage):" if tok else
                 "- LLM tokens per run: no runs recorded usage yet")
    lines += tok
    return "\n".join(lines)


# ── Candidate ledger (WP-012..017) ───────────────────────────────────────────

def candidate_sections(rows: list[dict]) -> str:
    """Pure: ledger rows → markdown (tested on fixtures and on an empty table)."""
    from agents import shadow_model as sm
    if not rows:
        return ("- No candidate rows yet.\n\n"
                + sm.to_markdown(sm.evaluate([], horizon=30)))
    def wk(r):
        return sm.week_of(date.fromisoformat(str(r["run_date"])[:10]))
    lines = [f"- Rows: {len(rows)} — by source {dict(Counter(r.get('source') for r in rows))}; "
             f"by stage {dict(Counter(r.get('stage') for r in rows))}",
             f"- Labelled: t+30 {sum(_num(r.get('alpha_t30')) is not None for r in rows)}, "
             f"t+90 {sum(_num(r.get('alpha_t90')) is not None for r in rows)}"]
    dq = [r.get("data_quality") for r in rows if r.get("data_quality")]
    lines.append(f"- Data quality (rows that reached deep analysis): "
                 + (", ".join(f"{k} {_rate(v, len(dq))}" for k, v in Counter(dq).most_common()) or "none yet"))
    disc = [r for r in rows if r.get("source") == "discovery" and _num(r.get("alpha_t30")) is not None]
    passed = [r for r in disc if r.get("stage") != "SCREENED"]
    failed = [r for r in disc if r.get("stage") == "SCREENED"]
    lines += ["", "**Pre-screen pass vs fail — beat Nifty at 30 days (provisional)**", "",
              f"- passed: {_cluster_rate([int(_num(r['alpha_t30']) > 0) for r in passed], [wk(r) for r in passed])}",
              f"- failed: {_cluster_rate([int(_num(r['alpha_t30']) > 0) for r in failed], [wk(r) for r in failed])}"]
    gated = sorted((r for r in rows if _num(r.get("gate_score")) is not None
                    and _num(r.get("alpha_t30")) is not None), key=lambda r: _num(r["gate_score"]))
    lines += ["", "**Gate score top vs bottom half — beat Nifty at 30 days (provisional)**", ""]
    if len(gated) >= 2:
        half = len(gated) // 2
        bottom, top = gated[:half], gated[half:]
        lines += [f"- top half: {_cluster_rate([int(_num(r['alpha_t30']) > 0) for r in top], [wk(r) for r in top])}",
                  f"- bottom half: {_cluster_rate([int(_num(r['alpha_t30']) > 0) for r in bottom], [wk(r) for r in bottom])}",
                  "- WP-020's precondition: ≥60 such rows, and the top half does NOT beat the bottom half."]
    else:
        lines.append(f"- {len(gated)} labelled rows with a gate score — not enough to split")
    labelled = [r for r in rows if r.get("source") == "discovery" and _num(r.get("alpha_t30")) is not None]
    lines += ["", sm.to_markdown(sm.evaluate(labelled, horizon=30))]
    return "\n".join(lines)


def section_candidate_ledger(client) -> str:
    try:
        rows = _all(client, "candidates",
                    "run_date,symbol,source,stage,data_quality,gate_score,signal_score,"
                    "features,agent_signals,alpha_t30,alpha_t90")
    except Exception as exc:
        return f"- candidates table unavailable: {exc}"
    return candidate_sections(rows)


def section_open_findings(client) -> str:
    """Live checks the reviewer must not miss (computed each time, not historical prose)."""
    since = (date.today() - timedelta(days=7)).isoformat()
    rows = _all(client, "recommendations", "created_at,action,metadata", is_discovery=False)
    recent = [r for r in rows if (r.get("created_at") or "") >= since]
    published = [r for r in recent if r["action"] != "SUPPRESSED"]
    reviews = [r for r in published if (r.get("metadata") or {}).get("kind") == "HOLDING_REVIEW"]
    ok = bool(published)
    return "\n".join([
        f"1. Portfolio pipeline, last 7 days: {len(published)} published rows "
        f"({len(reviews)} holding reviews), {len(recent) - len(published)} suppressed — "
        + ("publishing." if ok else "**NOT publishing** (it published nothing from ≥2026-09-04 to 2026-11-12)."),
        "2. **Validation evidence disagrees with live evidence.** The technical-only backtest beats Nifty "
        "~62–66% of the time; live discovery calls beat it ~46–50% (see the measured-outcomes section, with "
        "intervals). The backtest is survivorship-biased and tests a different strategy (WP-022).",
    ])


def section_known_gaps() -> str:
    return """These were found and fixed in Nov 2026 sessions. Each is a *pattern* the architecture let happen — the review should ask what structural change prevents the next one:

1. **Silent failure by default.** tz-naive/aware subtraction swallowed at DEBUG froze outcome tracking for ~3 months; a wrong dict key (`intrinsic_value` vs `intrinsic_value_per_share`) disabled target extension for months; tests mocked the same wrong key.
2. **Docstring-only features.** The GPT judge's "falls back to Haiku" and the governance "approve via note" path existed only in comments.
3. **Uncalibrated numbers shown as probabilities.** "Confidence" was a quality score; stated 90%+ beat Nifty 40% of the time.
4. **Unbounded state growth.** 233 overlapping "valid" discovery rows (INFY ×12) + top-N-by-upside queries froze the dashboard on May data.
5. **Mutable history.** Re-confirmations rewrote a call's entry/stoploss, so stops slid down behind falling stocks.
6. **Metric mislabels.** Paper "total P&L %" excluded realised losses (+6.4% shown vs −1.8% actual); backtest "Max DD" is the worst single trade.
7. **Model churn.** Deprecations (gpt-4o-mini, Haiku 4.5, Sonnet 4.5) and API-shape changes (thinking blocks first, max_completion_tokens) broke call sites spread across ~10 files until a single registry (`data/model_config.py`) was added.
8. **Parallel edits.** A concurrent dashboard redesign was swept into a commit and broke the Vercel build."""


def main() -> None:
    client = _db()
    sections = [
        ("Findings (computed)", lambda: section_open_findings(client)),
        ("Models in production", lambda: section_models()),
        ("Measured outcomes — 90-day discovery calls", lambda: section_discovery_outcomes(client)),
        ("Paper portfolios", lambda: section_paper(client) + "\n" + section_long_horizon(client)),
        ("Walk-forward backtest (latest run per split)", lambda: section_backtest(client)),
        ("Pipeline runs by pipeline", lambda: section_pipeline_health(client)),
        ("Job ledger", lambda: section_job_ledger(client)),
        ("Candidate ledger", lambda: section_candidate_ledger(client)),
        ("Worker schedule", section_schedule),
        ("Recent defect patterns", section_known_gaps),
        ("Code map", section_code),
    ]
    parts = [f"# Bharat Intelligence — Current State Evidence Pack\n\n"
             f"> Generated {date.today().isoformat()} by `scripts/build_architect_pack.py` from the code and the "
             f"live database. Facts only — no recommendations. Aggregates only, no personal holdings.\n"]
    for title, fn in sections:
        try:
            body = fn()
        except Exception as exc:  # one failed section must not lose the rest
            body = f"_section unavailable: {exc}_"
        parts.append(f"## {title}\n\n{body}\n")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
