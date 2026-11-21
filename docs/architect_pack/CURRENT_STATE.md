# Bharat Intelligence — Current State Evidence Pack

> Generated 2026-11-21 by `scripts/build_architect_pack.py` from the code and the live database. Facts only — no recommendations. Aggregates only, no personal holdings.

## Findings (computed)

1. Portfolio pipeline, last 7 days: 192 published rows (0 holding reviews), 0 suppressed — publishing.
2. **Validation evidence disagrees with live evidence.** The technical-only backtest beats Nifty ~62–66% of the time; live discovery calls beat it ~46–50% (see the measured-outcomes section, with intervals). The backtest is survivorship-biased and tests a different strategy (WP-022).

## Models in production

| Role | Model |
|---|---|
| Synthesis | `claude-opus-5-5` |
| Validation judges | `gpt-6-luna` · `claude-sonnet-4-6` · `claude-opus-5-5` |
| Research agent | `claude-sonnet-4-6` |
| Haiku tier (sentiment, digest, fact-check, ARIA) | `claude-haiku-5-5` |

## Measured outcomes — 90-day discovery calls

- Tracked discovery calls: **240** (outcome rows) — non-discovery: 231
- Resolved at 90 days: **157**, beat Nifty **49.7% [43.2–56.2] (n=157)**, median alpha -0.26%
- Live (unresolved) alpha median -2.24% over 240 calls
- Calibration (1 call/symbol/30d, Beta-shrunk): n=96, overall beat-Nifty 45.8%
  - by raw signal band: 0–70: 40.7% (n=16), 70–80: 54.6% (n=35), 80–90: 44.2% (n=32), 90–101: 37.3% (n=13)
  - Note: the raw score was published as 'confidence' until 2026-11-10 and ran INVERSELY to outcomes.
- Live discovery calls by status: {'WEAKENED': 26, 'ACTIVE': 39}

## Paper portfolios

- 90-day paper book (auto-follows discovery BUYs): deployed ₹1,737,774, total P&L ₹-30,491 (**-1.75%**), same-trades Nifty -0.96%, alpha **-0.8%**
- Exits {'HORIZON': 157, 'TARGET': 3, 'STOPLOSS': 40}

| Rec source | Positions | Open | Closed | Closed win rate | Closed beat-Nifty rate | Median closed alpha |
|---|---|---|---|---|---|---|
| discovery | 344 | 43 | 301 | 33.9% [29.6–38.5] (n=301) | 42.2% [36.7–48.0] (n=206) | -2.74% |

- Position sizes: {'Half position (2.5%)': 343, 'Base position (2.5%)': 1} (WP-019: BASE until a band's 90% lower bound clears 55%)
- Long-horizon multibagger track (started 2026-11-11): 30 active picks, 30 open paper positions, total P&L -3.34%. Conviction sizing LOCKED (0/20 resolved).

## Walk-forward backtest (latest run per split)

Technical rule only (RSI 40-65 + price > EMA200 + MACD cross), not the live multi-agent system. Survivorship-biased: today's NIFTY 500 applied back to 2020. 'worstTrade' is the worst single trade, not a portfolio drawdown; 'alphaMeanOverStd' is mean/std of per-trade 90d alpha, not an annualised Sharpe. Fixed 2020–24 window → monthly reruns are near-identical.

| Split | Period | Signals | Hit (beat Nifty 90d) | Avg alpha | Sharpe* | Worst trade |
|---|---|---|---|---|---|---|
| TRAIN | 2020-01-01→2023-01-01 | 1006 | 58.55% | +2.98% | 0.188 | -0.2989 |
| TEST | 2023-01-01→2024-12-31 | 835 | 65.75% | +4.21% | 0.294 | -0.2932 |
| FULL | 2020-01-01→2024-12-31 | 1841 | 61.81% | +3.54% | 0.233 | -0.2989 |

*Sharpe here = mean/std of per-trade alpha, not annualised.

## Pipeline runs by pipeline

Runs per pipeline, all time (`select pipeline, count(*) from daily_runs group by 1`):

- orchestrator runs: 9
- discovery runs: 288
- other pipelines: breeze_token_refresh 7, discovery_rescorer 7, earnings_calendar 7, forward_poller 7, gift_nifty_fetch 7, job_watchdog 7, long_horizon_book 7, market_digest_closing 7, market_digest_morning 7, multibagger_review 1, multibagger_screen 7, options_snapshot 7, outcome_tracker 7, paper_portfolio_open 7, paper_portfolio_update 7, performance_tracker 7, portfolio_monitor 28, rec_outcome_seeder 7, regime_detector 7, research_agent 7, target_updater 7, weekly_audit 1

- orchestrator, last 30 days: 9 runs, status {'WARNING': 2, 'OK': 7}, median duration 887s
- discovery, last 30 days: 31 runs, status {'OK': 9, 'success': 22}, median duration 695s
- Symbols analysed by the orchestrator: 216 over 9 runs; per reason, share of symbols analysed: agent_run_failed 0.0% [0.0–1.2] (n=216); suppressed_by_judges 0.0% [0.0–1.2] (n=216); synthesis_failed 0.0% [0.0–1.2] (n=216); synthesis_fallback 0.9% [0.3–2.8] (n=216)
- Portfolio-pipeline rows (30d) by kind and action: recommendation/AVOID 17, recommendation/HOLD 187, recommendation/SELL 12, recommendation/SUPPRESSED 10

## Job ledger

- Coverage since 2026-11-15 (ledger started 2026-11-15): 95.7% [91.9–97.8] (n=140) of scheduled job runs left a `daily_runs` row

| Day | Jobs due | Logged | Missing |
|---|---|---|---|
| 2026-11-15 | 25 | 24 | portfolio_risk |
| 2026-11-16 | 23 | 22 | portfolio_risk |
| 2026-11-17 | 23 | 22 | portfolio_risk |
| 2026-11-18 | 23 | 22 | portfolio_risk |
| 2026-11-19 | 23 | 22 | portfolio_risk |
| 2026-11-20 | 23 | 22 | portfolio_risk |

- Invariant breaches (JOB_INVARIANT alerts), last 30 days: 0
- Exception allowlist (silent broad handlers still allowed, WP-009): 144
- Schema contract (WP-007): production has every column the code writes
- LLM tokens per run (Anthropic, from details.llm_usage):
  - market_digest_closing: median 2,055 tokens/run over 7 runs
  - market_digest_morning: median 2,029 tokens/run over 7 runs
  - multibagger_review: median 8,829 tokens/run over 1 runs
  - multibagger_screen: median 8,713 tokens/run over 7 runs
  - research_agent: median 108,213 tokens/run over 7 runs
  - target_updater: median 2,312 tokens/run over 7 runs

## Candidate ledger

- Rows: 1355 — by source {'portfolio': 144, 'discovery': 1200, 'multibagger': 11}; by stage {'PROMOTED': 165, 'SCREENED': 1164, 'DEEP': 26}
- Labelled: t+30 0, t+90 0
- Data quality (rows that reached deep analysis): FULL 100.0% [97.9–100.0] (n=128)

**Pre-screen pass vs fail — beat Nifty at 30 days (provisional)**

- passed: — (n=0)
- failed: — (n=0)

**Gate score top vs bottom half — beat Nifty at 30 days (provisional)**

- 0 labelled rows with a gate score — not enough to split

### Shadow model — t+30d

insufficient data: 0 rows, 0 weeks (needs ≥300 labelled rows across ≥8 distinct weeks, ADR-006)


## Worker schedule

- Daily Orchestrator
- Performance Tracker
- Research Agent
- GIFT Nifty Pre-Market Signal (08:30 IST)
- Breeze Token Refresh
- Market Digest Morning Brief (05:30 IST)
- Market Digest Closing Digest (16:20 IST)
- Earnings Calendar
- Regime Detector
- Paper Portfolio Open (07:05 IST)
- Paper Portfolio Update (16:15 IST)
- Forward Outcome Poller (16:30 IST)
- Multibagger Screen (12:30 IST)
- Long-Horizon Book (16:50 IST)
- Multibagger Review (Sunday 08:00 IST)
- Discovery Re-check (16:45 IST)
- Rec Outcome Seeder
- Dynamic Target & Stoploss Updater (17:00 IST)
- Outcome Tracker
- Discovery Screener — Live Prices (10:30 IST)
- Portfolio Risk
- Options Market Snapshot
- Weekly Health Audit (Sunday 07:45 IST)
- Monthly Backtest (07:45 IST, 1st)
- Monthly RAG Corpus Refresh (08:15 IST, 1st)
- Job Invariant Watchdog (19:00 IST)

26 scheduled jobs (APScheduler, single worker process).

## Recent defect patterns

These were found and fixed in Nov 2026 sessions. Each is a *pattern* the architecture let happen — the review should ask what structural change prevents the next one:

1. **Silent failure by default.** tz-naive/aware subtraction swallowed at DEBUG froze outcome tracking for ~3 months; a wrong dict key (`intrinsic_value` vs `intrinsic_value_per_share`) disabled target extension for months; tests mocked the same wrong key.
2. **Docstring-only features.** The GPT judge's "falls back to Haiku" and the governance "approve via note" path existed only in comments.
3. **Uncalibrated numbers shown as probabilities.** "Confidence" was a quality score; stated 90%+ beat Nifty 40% of the time.
4. **Unbounded state growth.** 233 overlapping "valid" discovery rows (INFY ×12) + top-N-by-upside queries froze the dashboard on May data.
5. **Mutable history.** Re-confirmations rewrote a call's entry/stoploss, so stops slid down behind falling stocks.
6. **Metric mislabels.** Paper "total P&L %" excluded realised losses (+6.4% shown vs −1.8% actual); backtest "Max DD" is the worst single trade.
7. **Model churn.** Deprecations (gpt-4o-mini, Haiku 4.5, Sonnet 4.5) and API-shape changes (thinking blocks first, max_completion_tokens) broke call sites spread across ~10 files until a single registry (`data/model_config.py`) was added.
8. **Parallel edits.** A concurrent dashboard redesign was swept into a commit and broke the Vercel build.

## Code map

| Module | Lines | Purpose (docstring) |
|---|---|---|
| `agents/backtester.py` | 694 | agents/backtester.py — Walk-Forward Historical Backtest Framework |
| `agents/base.py` | 538 | agents/base.py — Bharat Intelligence: Data Completeness Validator |
| `agents/call_calibration.py` | 293 | agents/call_calibration.py — turn a discovery signal score into an honest, |
| `agents/commodities.py` | 625 | agents/commodities.py — Commodities Agent |
| `agents/contracts.py` | 297 | agents/contracts.py — what other modules read from each agent's result (WP-016, ADR-005). |
| `agents/discovery_rescorer.py` | 361 | agents/discovery_rescorer.py — daily re-check of every live discovery call. |
| `agents/discovery_screener.py` | 2102 | agents/discovery_screener.py — Proactive Discovery Screener |
| `agents/earnings_guard.py` | 116 | agents/earnings_guard.py — Pre-Earnings Risk Guard |
| `agents/fundamental.py` | 1855 | agents/fundamental.py — Fundamental Analysis Agent |
| `agents/governance_screener.py` | 286 | agents/governance_screener.py — Corporate Governance Red Flag Screener |
| `agents/historical_rag.py` | 420 | agents/historical_rag.py — Historical RAG Agent |
| `agents/institutional.py` | 992 | agents/institutional.py — Institutional Flow Analysis Agent |
| `agents/long_horizon_book.py` | 306 | agents/long_horizon_book.py — paper book for the long-horizon multibagger track (P8-E). |
| `agents/macro.py` | 810 | agents/macro.py — Macro Environment Agent |
| `agents/market_digest.py` | 485 | agents/market_digest.py — Daily Market Digest Generator (P6-C) |
| `agents/mgmt_quality.py` | 465 | agents/mgmt_quality.py — Management Quality Scoring Agent |
| `agents/multibagger_screener.py` | 375 | agents/multibagger_screener.py — long-horizon "multibagger" screen (P8-E). |
| `agents/options_sentiment.py` | 281 | agents/options_sentiment.py — Options Market Sentiment Agent |
| `agents/outcome_tracker.py` | 1292 | agents/outcome_tracker.py — Recommendation Outcome Tracker |
| `agents/paper_portfolio.py` | 995 | agents/paper_portfolio.py — Paper Portfolio Simulation (P5-B) |
| `agents/portfolio_risk.py` | 493 | agents/portfolio_risk.py — Portfolio-Level Risk Framework |
| `agents/position_sizer.py` | 272 | agents/position_sizer.py — Position Sizing Output for Recommendations |
| `agents/rationale.py` | 457 | agents/rationale.py — Human-readable "why did this agent vote that way?" text |
| `agents/rec_outcome_seeder.py` | 375 | agents/rec_outcome_seeder.py — Recommendation Outcome Seeder (P5-C) |
| `agents/regime_detector.py` | 572 | agents/regime_detector.py — Market Regime Detection Engine |
| `agents/sector_valuation.py` | 539 | agents/sector_valuation.py — Live Sector Valuation Regime |
| `agents/sentiment.py` | 1131 | agents/sentiment.py — Sentiment Analysis Agent |
| `agents/shadow_model.py` | 498 | agents/shadow_model.py — shadow probability model on the candidate ledger (WP-017, ADR-006). |
| `agents/target_updater.py` | 761 | agents/target_updater.py — Dynamic Target & Stoploss Manager (P7-A) |
| `agents/technical.py` | 856 | agents/technical.py — Technical Analysis Agent |
| `agents/valuation_scenarios.py` | 576 | agents/valuation_scenarios.py — Valuation Sensitivity Analysis |
| `agents/warren_bot.py` | 1220 | agents/warren_bot.py — Business Quality Assessment Agent |
| `scheduler/decision.py` | 406 | scheduler/decision.py — the portfolio decision is computed in code (WP-018, ADR-001). |
| `scheduler/invariants.py` | 309 | scheduler/invariants.py — what a healthy day looks like, per job (WP-006, ADR-005). |
| `scheduler/orchestrator.py` | 2596 | scheduler/orchestrator.py — Bharat Intelligence Daily Orchestrator |
| `scheduler/performance_tracker.py` | 655 | scheduler/performance_tracker.py — Bharat Intelligence: Daily Performance Tracker |
| `scheduler/portfolio_monitor.py` | 1287 | scheduler/portfolio_monitor.py — Bharat Intelligence Portfolio Monitor |
| `scheduler/sector_pe_tracker.py` | 526 | scheduler/sector_pe_tracker.py — Daily Sector PE Snapshot Tracker |
| `scheduler/synthesis_validator.py` | 910 | scheduler/synthesis_validator.py — Pre-publication Agreement-Gated Validation |
| `governance/fact_checker.py` | 1020 | governance/fact_checker.py — Bharat Intelligence Governance: Fact Checker |
| `governance/github_manager.py` | 644 | governance/github_manager.py — Bharat Intelligence: GitHub Automation Manager |
| `governance/hallucination_detector.py` | 1593 | governance/hallucination_detector.py — Bharat Intelligence Governance: Hallucination Detector |
| `governance/performance_tracker.py` | 1165 | governance/performance_tracker.py — Bharat Intelligence: Weekly Outcome Tracker |
| `governance/proposal_tags.py` | 80 | governance/proposal_tags.py |
| `governance/research_agent.py` | 1978 | governance/research_agent.py -- Bharat Intelligence: Daily AI Research Scanner |
| `data/breeze_auth.py` | 432 | data/breeze_auth.py — ICICI Breeze Connect Session Manager |
| `data/candidate_ledger.py` | 282 | data/candidate_ledger.py — writes to the `candidates` table (WP-013, ADR-002). |
| `data/decision_config.py` | 130 | Decision configuration — the ONLY place agent weights are defined. |
| `data/earnings_fetcher.py` | 309 | data/earnings_fetcher.py — Earnings Calendar Data Fetcher |
| `data/fetchers.py` | 2415 | data/fetchers.py — Bharat Intelligence India Market Data Fetchers |
| `data/forward_estimates.py` | 342 | data/forward_estimates.py — Forward Earnings Estimates |
| `data/fundamentals_cache.py` | 348 | data/fundamentals_cache.py — P7-I: persistent fundamentals cache with decay |
| `data/gift_nifty_fetcher.py` | 354 | data/gift_nifty_fetcher.py — P6-D-7: GIFT Nifty Pre-Market Signal Layer |
| `data/impact_cost.py` | 213 | data/impact_cost.py — Market Impact Cost Estimator |
| `data/insider_signal.py` | 201 | data/insider_signal.py — Promoter / Insider Signal Module |
| `data/llm_utils.py` | 141 | data/llm_utils.py — safe extraction of text from an Anthropic response. |
| `data/model_config.py` | 68 | data/model_config.py — the single source of truth for every LLM model ID. |
| `data/options_fetcher.py` | 724 | data/options_fetcher.py — Option Chain Fetcher (Trendlyne + Breeze + NSE + Fallback) |
| `data/proxy_session.py` | 162 | data/proxy_session.py — Outbound HTTP proxy abstraction |
| `data/run_cache.py` | 354 | data/run_cache.py — P7-H: intra-run memoisation for expensive remote fetches |
| `data/run_log.py` | 231 | data/run_log.py — one writer for the daily_runs table. |
| `data/symbol_map.py` | 348 | data/symbol_map.py — NSE Symbol Resolution & Normalisation |
| `data/trendlyne_analyst_fetcher.py` | 1005 | data/trendlyne_analyst_fetcher.py |
| `data/trendlyne_fetcher.py` | 1033 | data/trendlyne_fetcher.py |
| `data/trendlyne_fno_fetcher.py` | 539 | data/trendlyne_fno_fetcher.py |
| `api/main.py` | 3628 | api/main.py — Bharat Intelligence FastAPI Backend |

Dashboard: `dashboard/src/App.jsx` 4728 lines (single-file SPA) + `LongHorizonPanel.jsx`. Tests: 84 files.
