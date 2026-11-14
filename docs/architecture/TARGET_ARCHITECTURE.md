# Target Architecture — Bharat Intelligence, February 2027

*Companion to `REVIEW.md`. Decisions are recorded in `ADR-001` … `ADR-008`; the build order is in
`workplan.yaml`.*

## 1. Principles

1. **Code produces numbers; the LLM produces words.** Confidence, target, stop, size and action
   come from deterministic code fitted on recorded outcomes. The LLM explains them.
2. **Record everything considered, not only what was promoted.** Learning needs the rejected
   candidates as much as the accepted ones.
3. **Nothing changes a decision without a pre-registered test.** A new score, gate, weight or model
   runs in shadow first and is promoted by a rule written down in advance.
4. **Every job states what it should produce, and a missing output is an alert.**
5. **Same deployment.** One FastAPI web service, one APScheduler worker, Supabase, Vercel.

## 2. Components

```
                    ┌───────────────────────────────────────────────┐
                    │                DATA SOURCES                   │
                    │ screener.in · Trendlyne · yfinance · NSE/BSE  │
                    └──────────────────────┬────────────────────────┘
                                           │  run_cache → fundamentals_cache (kept)
                                           ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │ FEATURE AGENTS  (existing agents, unchanged maths)                           │
 │ technical · fundamental · sentiment · macro · institutional · sector · RAG   │
 │ Output: typed AgentResult (agents/contracts.py) — score, signal, features,   │
 │ data_quality, data_as_of, reason                                             │
 └──────────────────────────────────┬───────────────────────────────────────────┘
                                    ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │ EVIDENCE LAYER                                                               │
 │ candidates (append-only): every screened symbol, its features, price, Nifty, │
 │   stage reached, feature_version                                             │
 │ labels: alpha at 30 and 90 days written back onto each candidate row         │
 │ daily_runs: one row per job run — status, counts, reasons                    │
 └──────────────────────────────────┬───────────────────────────────────────────┘
                                    ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │ DECISION LAYER  (deterministic)                                              │
 │ score      → equal-weight composite today; fitted model once promoted        │
 │ probability→ P(beat Nifty at 90d) from the ledger, walk-forward, shrunk      │
 │ levels     → entry / stop from ATR; target from capped valuation gap         │
 │ size       → one base size until a band's lower bound clears the unlock rule │
 │ action     → rule on probability + guards (earnings, liquidity, data quality)│
 └──────────────────────────────────┬───────────────────────────────────────────┘
                                    ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │ NARRATIVE + CHECK                                                            │
 │ LLM writes bull case, bear case, summary, headline from the fixed numbers.   │
 │ It may veto (downgrade to HOLD with a stated reason); it may not upgrade or  │
 │ change a number.                                                             │
 │ Grounding check: deterministic comparison of every number in the text with   │
 │ agent data. One LLM judge runs in shadow until it shows outcome lift.        │
 └──────────────────────────────────┬───────────────────────────────────────────┘
                                    ▼
           recommendations · holdings review · paper books · API · dashboard
```

## 3. Data flow, by product

**Discovery (10:30 IST).** Screen the day's 200-symbol slice. Write one `candidates` row for every
symbol with the cheap pre-screen features, whether or not it passes. Deep-analyse the passes and
update their rows with agent features. Promote by rule; create or merge the live call as today.

**Portfolio (06:00 IST).** Becomes a holdings review (ADR-007). For each holding: run the feature
agents, compute the exit signal already implemented in `agents/target_updater.py`, and publish one
review per holding per day — EXIT, BOOK PARTIAL, TRAIL, REVIEW or HOLD — with the reason and a
short LLM narrative. It no longer issues fresh BUY calls on stocks already owned.

**Long-horizon (12:30 IST).** Unchanged. Its picks are also written to `candidates` with
`source='multibagger'`.

**Labelling (16:30 and 18:30 IST).** The existing poller and outcome tracker also label
`candidates` rows at 30 and 90 days, using the same price lookup and the same alpha definition.

## 4. Decision and validation design

| Output | Today | Target |
|---|---|---|
| Composite score | Accuracy-weighted from six stale rows | Equal weights, then a fitted model if promoted |
| Confidence | LLM estimate (portfolio); band hit rate (discovery) | Ledger-fitted probability for both |
| Target / stop | LLM (portfolio); formula (discovery) | Formula for both, anchored at call time |
| Action | LLM | Rule; LLM may veto downward only |
| Size | Always HALF by accident | One base size by decision, until unlocked |
| Gate | 15 judge calls, "kappa" | Deterministic grounding; one shadow judge |

A recommendation is published when the rule produces an action and the grounding check finds no
number in the narrative that contradicts agent data. A grounding failure regenerates the narrative
once, then publishes the numbers with a templated explanation from `agents/rationale.py`. A symbol
is never dropped because prose failed.

## 5. How confidence is calibrated

- **Label.** `alpha_t90 > 0`, the definition already used in `agents/call_calibration.py`.
- **Population.** Labelled `candidates` rows. One row per symbol per 30 days for headline rates.
- **Method.** Stage one is today's banded rate with Beta shrinkage, refit daily. Stage two is a
  small regularised logistic model on a handful of features, followed by isotonic calibration.
  It is fitted walk-forward only: train on rows labelled before date *t*, score rows created after.
- **Honesty rules.** Show the probability with its sample size. If the band's interval includes
  50%, the dashboard says "no measured edge" instead of a percentage. CRITICAL requires the lower
  bound of the band's 90% interval to exceed 55%.
- **Drift.** The evidence pack reports Brier score against the base-rate baseline weekly. A model
  that loses to the baseline for four consecutive weeks is demoted automatically to the banded rate.

## 6. How the system learns from outcomes

```
candidates (features at decision time)
      │  30 / 90 days later
      ▼
labels (alpha vs Nifty)  ──►  weekly evaluation in the evidence pack
                                   │
                                   ├─ does the pre-screen add value?  (passed vs failed rows)
                                   ├─ does each feature rank outcomes? (quintile spread)
                                   ├─ does the gate score rank outcomes?
                                   └─ does the shadow model beat the base rate out of sample?
                                   │
                                   ▼
                          promotion rule (ADR-006)  ──►  change in data/decision_config.py
```

Guardrails against overfitting a hot streak:

- Minimum 300 labelled rows spanning at least 8 distinct weeks before any promotion.
- Walk-forward evaluation only; no in-sample numbers in the pack.
- At most one promoted change per month, so its effect can be attributed.
- Every promotion is a pull request that changes one config value and cites the pack.
- Weights and thresholds live in one file, enforced by a test, as model IDs are today.

## 7. Observability — how a silent failure becomes loud

1. **Job ledger.** Every worker job runs inside `tracked_job(name)`, which writes a `daily_runs`
   row with status, duration, an output count and any exception. A job that raises is recorded as
   `FAILED`, not lost in a log.
2. **Expected-output invariants.** A declarative table states what each job must produce, for
   example "orchestrator: ≥1 published review when holdings exist" and "forward poller: updated
   rows ≥ 90% of open rows". A watchdog job at 19:00 IST checks the day's rows and raises a
   CRITICAL `portfolio_alerts` row for any missing run or broken invariant.
3. **Schema contract.** `db/contract.py` lists the columns the code writes. A check at worker
   start and in the weekly audit selects each column and reports any that production lacks.
4. **Exception policy.** A test fails the build if an `except Exception` block only passes,
   returns an empty value or logs below WARNING, unless the site is on an allowlist that can only
   shrink.
5. **Typed agent results.** `agents/contracts.py` defines the keys consumers read; contract tests
   run each agent on recorded inputs and assert the keys exist.
6. **Health endpoint.** `/api/system/health` reads the job ledger and invariants, so the dashboard
   shows the same truth as the alerts.

## 8. Migration path

No rewrite. Four stages; each leaves production working.

| Stage | Weeks | What changes | Work packages |
|---|---|---|---|
| 1. Make it loud | 1–2 | Confirm the pipeline publishes; equal weights; job ledger; invariants; schema check; CI key removed | WP-001 … WP-011 |
| 2. Record everything | 2–4 | Candidate ledger; labelling; gate score stored for every symbol; typed contracts | WP-012 … WP-016 |
| 3. Shadow | 4–10 | Shadow probability model; weekly evaluation in the pack; numbers moved out of the prompt | WP-017, WP-018, WP-023 |
| 4. Switch by rule | 10–13 | Sizing from probability; judge panel cut if it shows no lift; holdings review; honest backtest labels | WP-019 … WP-022, WP-024 |

Stage 3 is bounded by the calendar: 30-day labels need 30 days. Stages 1 and 2 should therefore
land as early as possible.

## 9. Cost

LLM token spend is not recorded today, so the current daily cost is unknown; WP-005 records it per
run. The target removes 14 of 15 judge calls per symbol and keeps one synthesis call, which should
reduce spend. No new paid service is proposed.

## 10. What success looks like in three months

- A published holdings review exists for every holding on every trading day.
- Every job has a ledger row every day; a deliberately broken job raises an alert within one day.
- At least 5,000 labelled candidate rows.
- The evidence pack answers, with intervals: does the pre-screen add value, does any feature rank
  outcomes, does the gate rank outcomes.
- Either one change has been promoted by the rule, or the product states that no edge has been
  measured. Both are acceptable outcomes; an unmeasured claim is not.
