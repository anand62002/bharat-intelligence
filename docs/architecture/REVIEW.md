# Architecture Review — Bharat Intelligence

*Reviewed 2026-11-12 against commit `bf5d3fa`, `docs/architect_pack/CURRENT_STATE.md` of the same
date, and read-only aggregate queries on the live database.*

Every claim is marked **V** (verified in code or by query) or **I** (inferred).

---

## 1. Verdict

The plumbing is better than the results suggest, and the decision core is worse.

Caching, the model registry, immutable discovery calls, alpha-vs-Nifty outcome tracking and the
paper books are sound and should stay. But the system has no demonstrated edge and no working
mechanism to find one. Discovery beats the Nifty 50.0% of the time (n=154), and the score that
selects and sizes calls shows no relationship to outcomes.

The expensive part — ten agents, an Opus synthesis, a 15-call judge panel — produces numbers and
prose that have never been tested against results, because the fields needed to test them were
never stored. The "learning loop" is disconnected: in production it runs on six stale rows that
zero out the technical agent. One of the two products, portfolio recommendations, has never saved
a published row.

The recurring silent failures are not bad luck. They follow from three structural choices:
exceptions are swallowed by default, the database schema has no contract with the code, and
agents exchange untyped dicts.

Recommendation: stop adding agents and models. Spend three months making the system record
everything it considers, fail loudly, and let code — not an LLM — produce every number a user
acts on. Until a pre-registered test shows an edge, the product should say plainly that none has
been measured.

---

## 2. The five most important problems

Ranked by impact on decision quality.

### P1 — No measured edge, and the selection score is not predictive

- **V** 154 discovery calls resolved at 90 days: 50.0% beat the Nifty, median alpha +0.20%
  (`CURRENT_STATE.md:22`).
- **V** Calibration by raw signal band: 41.6% (n=13), 53.3% (n=38), 43.9% (n=30), 37.3% (n=13);
  overall 45.7% on n=94 (`CURRENT_STATE.md:24-25`). The bands are non-monotonic. At n=94 the 95%
  interval on the overall rate is roughly ±10 points, so no band is distinguishable from another
  or from a coin flip.
- **V** That same score still decides which stocks are promoted (`agents/discovery_screener.py:80-83`,
  `:1621-1658`) and is the confidence input to sizing (`agents/discovery_screener.py:1250-1252`).
  Because it is always ≥65, the sizer always returns HALF: 334 of 334 paper positions are ₹5,000.
- **V** The backtest (62–66%) tests a different strategy — a technical rule on today's NIFTY 500
  list — not the pipeline that runs live (`CURRENT_STATE.md:38`).
- **Consequence.** The calibrated confidence introduced on 2026-11-10 is honest but carries no
  information: every call is shown at roughly the base rate. The system cannot currently tell a
  good idea from a bad one.

### P2 — The portfolio pipeline has never persisted a published recommendation

- **V** `recommendations` holds 251 rows; the earliest is a discovery row from 2026-05-18. Only 15
  rows are non-discovery and all 15 are SUPPRESSED, dated 2026-09-04 onward (live query).
  "Since September" is only the point at which SUPPRESSED rows became storable
  (`allow_suppressed_action.sql`, applied 2026-08-14).
- **V** The fix in `bf5d3fa` was committed at 21:01 IST on 2026-11-12 and has not yet run:
  `daily_runs` has the new `pipeline`/`details` columns but no row with `pipeline='orchestrator'`.
- **V** 24 holdings are open, so 24 symbols a day went through ten agents, one Opus synthesis and
  15 judge calls for six months with nothing saved.
- See section 3 for the root cause and how to confirm it.

### P3 — The learning loop is disconnected and partly inverted

- **V** Agent weights come from the latest non-null `agent_performance.accuracy_90d`
  (`scheduler/orchestrator.py:858-870`), floored at 1.0 and defaulted to 70
  (`scheduler/orchestrator.py:207`). The table has six non-null rows in total. The latest are
  technical = 0.0 (2026-10-10) and institutional = 100.0 (2026-10-08). Resulting weights:
  technical ≈ 0.2%, institutional ≈ 22%, every other agent ≈ 15.5%. The one signal with any
  backtest support is effectively switched off.
- **V** "Accuracy" is absolute-return direction with a ±2% buffer, not alpha
  (`scheduler/performance_tracker.py:141-146`), so in a rising market every bullish agent looks
  accurate.
- **V** At least three modules write the column with different definitions; one writes a fraction
  where the others write a percentage (`agents/outcome_tracker.py:1176`).
- **V** All 154 resolved outcome rows have null `composite_score` and null `validation_kappa`, and
  all are BUY. Nothing can test whether the composite or the gate predicts outcomes, and
  `/api/performance/calibration`, which buckets `composite_score`, has nothing to bucket.
- **V** Outcome seeding from the orchestrator still fails silently at DEBUG
  (`scheduler/orchestrator.py:1735`).

### P4 — The publication gate scores prose, and the LLM invents the numbers

- **V** The metric named "kappa" is mean rubric score × pairwise agreement
  (`scheduler/synthesis_validator.py:321-348`). It is not a kappa statistic; it is a quality score
  with an agreement discount.
- **V** The rubrics reward mentioning circuit-breaker bands, T+2 settlement and position tiers
  (`scheduler/synthesis_validator.py:146-153`). The synthesis prompt instructs the model to
  include exactly those items (`prompts/orchestrator_synthesis.txt:39`). The gate largely checks
  that the model followed its own template.
- **V** The prompt asks the LLM for `confidence`, `upside_confidence`, `target`, `upside_pct` and
  `danger_drop_pct` and supplies a table mapping composite score to a confidence range
  (`prompts/orchestrator_synthesis.txt:43-52`, `:94-99`). These are uncalibrated by construction —
  the same defect that was fixed for discovery on 2026-11-10 is still the design of the portfolio
  pipeline.
- **V** No outcome row carries a gate score, so the gate's value is unmeasured after six months.
- **I** At 15 judge calls per symbol (five on Opus) the gate is likely the largest LLM cost in the
  daily run. Token spend is not recorded anywhere, so this is not confirmed.

### P5 — Silent failure is structural

- **V** 473 `except Exception` handlers and 251 `log.debug` calls across `agents/`, `data/`,
  `scheduler/`, `governance/` and `api/`.
- **V** There is no contract between `db/schema.sql` and production. 28 migrations are applied by
  hand; the missing `danger_drop_pct` column was invisible for six months.
- **V** Agents return untyped dicts. The `intrinsic_value` / `intrinsic_value_per_share` mismatch
  disabled target extension for months, and the tests mocked the same wrong key.
- **V** No job records that it ran or what it produced. Until today the only `daily_runs` rows came
  from discovery with `errors` hard-coded to 0.
- **V** CI passes the production `SUPABASE_SERVICE_KEY` and `ANTHROPIC_API_KEY` to every pull-request
  test run (`.github/workflows/ci.yml:19-20`). A test that forgets to mock can write to production,
  and the governance auto-PR path runs under the same workflow.

---

## 3. The suppressed pipeline — root cause and confirmation

**Hypothesis.** The primary cause is the one fixed in `bf5d3fa`: every insert of a published
recommendation failed with PGRST204 because production lacks `recommendations.danger_drop_pct`.
The judge panel was a secondary cause, not the main one.

**Supporting arithmetic (I).** On 2026-11-12 the run analysed 24 holdings and 3 SUPPRESSED rows
were saved (κ = 0.136, 0.161, 0.217). On 2026-11-11 there were 5. So roughly 19–21 symbols a day
passed the gate and were lost at insert. The evidence pack's "suppressed by the judge panel: 100%"
is a denominator artefact: it counts only rows that reached the table.

**What I could not verify.** Railway logs. The per-symbol `save failed` lines would confirm the
count directly.

**How to confirm, in order.**

1. After the next 06:00 IST run:

   ```sql
   select run_date, status, symbols_processed, errors, details->'reasons' as reasons
   from daily_runs where pipeline = 'orchestrator' order by created_at desc limit 3;

   select created_at::date as d, action, count(*)
   from recommendations where is_discovery = false
   group by 1, 2 order by 1 desc limit 20;
   ```

   Expected: one orchestrator row with status `OK` or `WARNING`, and roughly 19–21 non-SUPPRESSED
   rows for the day.
2. If `details->'errors'` contains `SCHEMA_DRIFT: recommendations.danger_drop_pct`, the tolerant
   insert is working and the migration is still pending (WP-002).
3. If the status is `ALL_SUPPRESSED`, the hypothesis is wrong and the gate is the blocker. Read
   `gov_check.validation.judge_scores` on the new SUPPRESSED rows to see which rubric fails.
4. If the status is `NO_OUTPUT`, read `details->'reasons'`: `synthesis_fallback` points at
   truncation or overload, `agent_run_failed` at data sources.
5. In Railway, search the worker log for `save failed` on any date before 2026-11-12 to confirm
   the historical count.

**Two things to watch once it publishes.**

- **V** `agents/paper_portfolio.py:319-327` opens a paper position for every BUY row with no
  `is_discovery` filter. Portfolio-pipeline BUYs on stocks already held will enter the 90-day
  paper book and mix two strategies in one track record. WP-004 and WP-021 keep holdings
  reviews out of that book; until WP-021 lands, WP-023 reports the book split by source.
- **V** The regime detector runs at 06:30, after the 06:00 orchestrator (`worker.py:720`, `:818`),
  so the regime multipliers always use yesterday's regime.

---

## 4. Where the documents and the code disagree

| Document says | Code or database says |
|---|---|
| CLAUDE.md: regime detector runs "before orchestrator" | It runs 30 minutes after it (`worker.py:720`, `:818`) |
| CLAUDE.md env table: `CLAUDE_MAX_TOKENS` default 4096, `JUDGE_MAX_TOKENS` default 500 | 16000 and 2000 (`scheduler/orchestrator.py:97`, `scheduler/synthesis_validator.py:103`) |
| CLAUDE.md: `add_daily_runs_pipeline.sql` pending | Applied — the columns exist in production |
| CLAUDE.md: pipeline published nothing "since ≥2026-09-04" | No published row has ever existed |
| CURRENT_STATE.md: "Orchestrator runs (30d): 31 — success" | All 31 rows are discovery runs |
| CURRENT_STATE.md: "suppressed by the judge panel: 100%" | Share of saved rows, not of symbols analysed |
| Validator and docs: "kappa", "agreement-gated" | Mean rubric score × agreement |
| CLAUDE.md: weights from `accuracy_90d` imply a learning loop | Six stale rows; technical weighted ≈ 0.2% |
| EXECUTION_PLAN.md: "78 / 90 items complete (87%)" | Counts features shipped, not outcomes achieved |

---

## 5. What to keep

Do not rebuild these.

- **`data/fundamentals_cache.py` and `data/run_cache.py`.** Quality-ranked persistence and per-key
  locking are correct and measured (9 → 2 fetches per symbol).
- **`data/model_config.py` with `tests/test_model_ids.py`.** A registry enforced by a test is the
  pattern the rest of the system needs.
- **Discovery call lifecycle.** One live call per symbol, levels anchored to the call price, daily
  re-check, reversible status changes (`agents/discovery_rescorer.py`).
- **Outcome measurement.** Alpha against the Nifty over each call's own window, one call per symbol
  per 30 days, shrinkage on thin bands (`agents/call_calibration.py`,
  `agents/outcome_tracker.py`).
- **`compute_pnl_metrics()`.** One P&L definition shared by snapshot, API and backfill.
- **The long-horizon conviction lock.** A pre-registered unlock rule (≥20 resolved, ≥60% beating
  the Nifty) is the right way to earn trust. ADR-006 generalises it.
- **`agents/rationale.py`.** Explanations templated from the agent's own numbers, with no LLM call.
- **`agents/target_updater.py` exit signals.** Deterministic, advisory, explainable.
- **`data/run_log.py` and `scripts/build_architect_pack.py`.** The start of honest run logging and
  a regenerable evidence pack.
- **The deployment shape.** One web service, one worker, Supabase, Vercel. Nothing in this review
  needs another service.

---

## 6. Assumptions I am challenging

- **"More agents and stronger models improve the calls."** There is no evidence for it. The
  composite of seven agents has never been compared with outcomes, and the strongest evidence in
  the pack is for a three-rule technical screen.
- **"The judge panel protects quality."** It protects template compliance. Whether that correlates
  with returns is unknown.
- **"Agent weights should adapt to accuracy."** With fewer than 100 independent resolved calls,
  per-agent weights cannot be estimated. Equal weights are the honest choice (ADR-003).
- **"A paid data feed would help."** Not yet. The binding constraint is evaluation, not data
  (ADR-008).
- **"The 90-day horizon is how we learn."** At about one independent call per symbol per month,
  learning from promoted calls alone takes years. Logging every screened candidate gives thousands
  of labelled rows in a quarter (ADR-002).
