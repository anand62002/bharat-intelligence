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
        f"- Resolved at 90 days: **{len(a90)}**, beat Nifty **{_pct(sum(1 for a in a90 if a > 0), len(a90))}**, "
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
    return "\n".join([
        f"- 90-day paper book (auto-follows discovery BUYs): deployed ₹{m.get('deployed_capital', 0):,.0f}, "
        f"total P&L ₹{m.get('total_pnl', 0):,.0f} (**{m.get('total_pnl_pct')}%**), "
        f"same-trades Nifty {m.get('nifty_return_pct')}%, alpha **{m.get('alpha_pct')}%**",
        f"- Win rate {s.get('win_rate')}% · avg closed-trade alpha {s.get('avg_alpha_closed')}% · exits {dict(reasons)}",
        "- Sizing: every position has been the same 'Half' (₹5k) tier — the sizer never differentiates.",
    ])


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
    return ("Technical-rule-only backtest (RSI 40–65 + >EMA200 + MACD cross; 15% SL / 90d). "
            "Universe = TODAY's NIFTY 500 list → survivorship bias. Fixed 2020–24 window → monthly reruns are near-identical.\n\n"
            "| Split | Period | Signals | Hit (beat Nifty 90d) | Avg alpha | Sharpe* | Worst trade |\n|---|---|---|---|---|---|---|\n"
            + "\n".join(out) + "\n\n*Sharpe here = mean/std of per-trade alpha, not annualised.")


def section_pipeline_health(client) -> str:
    since = (date.today() - timedelta(days=30)).isoformat()
    runs = client.table("daily_runs").select("*").gte("run_date", since).order("run_date").execute().data or []
    status = Counter(r.get("status") for r in runs)
    dur = [float(r["duration_seconds"]) for r in runs if r.get("duration_seconds")]
    recs = _all(client, "recommendations", "action,created_at,is_discovery")
    recent = [r for r in recs if (r.get("created_at") or "") >= since and not r.get("is_discovery")]
    act = Counter(r["action"] for r in recent)
    sup = act.get("SUPPRESSED", 0)
    return "\n".join([
        f"- Orchestrator runs (30d): {len(runs)} — status {dict(status)}; median duration {st.median(dur):.0f}s" if dur else f"- Orchestrator runs (30d): {len(runs)}",
        f"- Portfolio recs (30d) by action: {dict(act)} — suppressed by the judge panel: {_pct(sup, sum(act.values()))}",
    ])


def section_open_findings(client) -> str:
    """Live problems not yet fixed — measured, so the reviewer can't miss them."""
    rows = _all(client, "recommendations", "created_at,action", is_discovery=False)
    by_action = Counter(r["action"] for r in rows)
    first = min((r["created_at"][:10] for r in rows), default="—")
    runs = client.table("daily_runs").select("symbols_processed").order("run_date", desc=True).limit(14).execute().data or []
    processed = [int(r["symbols_processed"] or 0) for r in runs]
    return "\n".join([
        f"1. **The daily portfolio pipeline published nothing from at least 2026-09-04 to 2026-11-12** "
        f"(non-discovery rows: {dict(by_action)}). Root cause, fixed 2026-11-12: production "
        "`recommendations` lacks `danger_drop_pct` (schema drift from db/schema.sql), so every "
        "published-rec insert failed with PGRST204; only SUPPRESSED rows (a separate insert) landed. "
        "Compounded by: judges seeing only the first 300 chars of the synthesis, Opus judges truncated "
        "at 500 tokens, and the orchestrator's daily_runs insert failing on a missing column — so "
        f"daily_runs ({min(processed, default=0)}–{max(processed, default=0)} symbols/day, errors=0) "
        "was written by the discovery screener only. Verify after deploy: non-SUPPRESSED rows appear.",
        "2. **Validation evidence disagrees with live evidence.** The technical-only backtest beats Nifty "
        "~62–66% of the time; live discovery calls beat it ~46–50%. Survivorship bias explains part of the gap.",
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
        ("OPEN findings (not yet fixed)", lambda: section_open_findings(client)),
        ("Models in production", lambda: section_models()),
        ("Measured outcomes — 90-day discovery calls", lambda: section_discovery_outcomes(client)),
        ("Paper portfolios", lambda: section_paper(client) + "\n" + section_long_horizon(client)),
        ("Walk-forward backtest (latest run per split)", lambda: section_backtest(client)),
        ("Pipeline health (last 30 days)", lambda: section_pipeline_health(client)),
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
