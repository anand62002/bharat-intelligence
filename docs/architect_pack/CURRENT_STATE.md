# Bharat Intelligence — Current State Evidence Pack

> Generated 2026-11-12 by `scripts/build_architect_pack.py` from the code and the live database. Facts only — no recommendations. Aggregates only, no personal holdings.

## OPEN findings (not yet fixed)

1. **The daily portfolio pipeline published nothing from at least 2026-09-04 to 2026-11-12** (non-discovery rows: {'SUPPRESSED': 15}). Root cause, fixed 2026-11-12: production `recommendations` lacks `danger_drop_pct` (schema drift from db/schema.sql), so every published-rec insert failed with PGRST204; only SUPPRESSED rows (a separate insert) landed. Compounded by: judges seeing only the first 300 chars of the synthesis, Opus judges truncated at 500 tokens, and the orchestrator's daily_runs insert failing on a missing column — so daily_runs (3–13 symbols/day, errors=0) was written by the discovery screener only. Verify after deploy: non-SUPPRESSED rows appear.
2. **Validation evidence disagrees with live evidence.** The technical-only backtest beats Nifty ~62–66% of the time; live discovery calls beat it ~46–50%. Survivorship bias explains part of the gap.

## Models in production

| Role | Model |
|---|---|
| Synthesis | `claude-opus-5-5` |
| Validation judges | `gpt-6-luna` · `claude-sonnet-4-6` · `claude-opus-5-5` |
| Research agent | `claude-sonnet-4-6` |
| Haiku tier (sentiment, digest, fact-check, ARIA) | `claude-haiku-5-5` |

## Measured outcomes — 90-day discovery calls

- Tracked discovery calls: **235** (outcome rows) — non-discovery: 15
- Resolved at 90 days: **154**, beat Nifty **50.0%**, median alpha +0.20%
- Live (unresolved) alpha median -0.69% over 235 calls
- Calibration (1 call/symbol/30d, Beta-shrunk): n=94, overall beat-Nifty 45.7%
  - by raw signal band: 0–70: 41.6% (n=13), 70–80: 53.3% (n=38), 80–90: 43.9% (n=30), 90–101: 37.3% (n=13)
  - Note: the raw score was published as 'confidence' until 2026-11-10 and ran INVERSELY to outcomes.
- Live discovery calls by status: {'ACTIVE': 40, 'WEAKENED': 25}

## Paper portfolios

- 90-day paper book (auto-follows discovery BUYs): deployed ₹1,687,757, total P&L ₹-29,659 (**-1.76%**), same-trades Nifty -1.35%, alpha **-0.4%**
- Win rate 27.5% · avg closed-trade alpha -0.15% · exits {'HORIZON': 156, 'STOPLOSS': 41, 'TARGET': 3}
- Sizing: every position has been the same 'Half' (₹5k) tier — the sizer never differentiates.
- Long-horizon multibagger track (started 2026-11-11): 6 active picks, 6 open paper positions, total P&L 0.33%. Conviction sizing LOCKED (0/20 resolved).

## Walk-forward backtest (latest run per split)

Technical-rule-only backtest (RSI 40–65 + >EMA200 + MACD cross; 15% SL / 90d). Universe = TODAY's NIFTY 500 list → survivorship bias. Fixed 2020–24 window → monthly reruns are near-identical.

| Split | Period | Signals | Hit (beat Nifty 90d) | Avg alpha | Sharpe* | Worst trade |
|---|---|---|---|---|---|---|
| TRAIN | 2020-01-01→2023-01-01 | 1006 | 58.55% | +2.98% | 0.188 | -0.2989 |
| TEST | 2023-01-01→2024-12-31 | 835 | 65.75% | +4.21% | 0.294 | -0.2932 |
| FULL | 2020-01-01→2024-12-31 | 1841 | 61.81% | +3.54% | 0.233 | -0.2989 |

*Sharpe here = mean/std of per-trade alpha, not annualised.

## Pipeline health (last 30 days)

- Orchestrator runs (30d): 31 — status {'success': 31}; median duration 721s
- Portfolio recs (30d) by action: {'SUPPRESSED': 11} — suppressed by the judge panel: 100.0%

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

25 scheduled jobs (APScheduler, single worker process).

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
| `agents/backtester.py` | 666 | agents/backtester.py — Walk-Forward Historical Backtest Framework |
| `agents/base.py` | 538 | agents/base.py — Bharat Intelligence: Data Completeness Validator |
| `agents/call_calibration.py` | 193 | agents/call_calibration.py — turn a discovery signal score into an honest, |
| `agents/commodities.py` | 625 | agents/commodities.py — Commodities Agent |
| `agents/discovery_rescorer.py` | 361 | agents/discovery_rescorer.py — daily re-check of every live discovery call. |
| `agents/discovery_screener.py` | 2007 | agents/discovery_screener.py — Proactive Discovery Screener |
| `agents/earnings_guard.py` | 116 | agents/earnings_guard.py — Pre-Earnings Risk Guard |
| `agents/fundamental.py` | 1855 | agents/fundamental.py — Fundamental Analysis Agent |
| `agents/governance_screener.py` | 286 | agents/governance_screener.py — Corporate Governance Red Flag Screener |
| `agents/historical_rag.py` | 420 | agents/historical_rag.py — Historical RAG Agent |
| `agents/institutional.py` | 992 | agents/institutional.py — Institutional Flow Analysis Agent |
| `agents/long_horizon_book.py` | 306 | agents/long_horizon_book.py — paper book for the long-horizon multibagger track (P8-E). |
| `agents/macro.py` | 810 | agents/macro.py — Macro Environment Agent |
| `agents/market_digest.py` | 483 | agents/market_digest.py — Daily Market Digest Generator (P6-C) |
| `agents/mgmt_quality.py` | 465 | agents/mgmt_quality.py — Management Quality Scoring Agent |
| `agents/multibagger_screener.py` | 345 | agents/multibagger_screener.py — long-horizon "multibagger" screen (P8-E). |
| `agents/options_sentiment.py` | 281 | agents/options_sentiment.py — Options Market Sentiment Agent |
| `agents/outcome_tracker.py` | 1251 | agents/outcome_tracker.py — Recommendation Outcome Tracker |
| `agents/paper_portfolio.py` | 973 | agents/paper_portfolio.py — Paper Portfolio Simulation (P5-B) |
| `agents/portfolio_risk.py` | 493 | agents/portfolio_risk.py — Portfolio-Level Risk Framework |
| `agents/position_sizer.py` | 201 | agents/position_sizer.py — Position Sizing Output for Recommendations |
| `agents/rationale.py` | 457 | agents/rationale.py — Human-readable "why did this agent vote that way?" text |
| `agents/rec_outcome_seeder.py` | 337 | agents/rec_outcome_seeder.py — Recommendation Outcome Seeder (P5-C) |
| `agents/regime_detector.py` | 572 | agents/regime_detector.py — Market Regime Detection Engine |
| `agents/sector_valuation.py` | 539 | agents/sector_valuation.py — Live Sector Valuation Regime |
| `agents/sentiment.py` | 1117 | agents/sentiment.py — Sentiment Analysis Agent |
| `agents/target_updater.py` | 757 | agents/target_updater.py — Dynamic Target & Stoploss Manager (P7-A) |
| `agents/technical.py` | 856 | agents/technical.py — Technical Analysis Agent |
| `agents/valuation_scenarios.py` | 576 | agents/valuation_scenarios.py — Valuation Sensitivity Analysis |
| `agents/warren_bot.py` | 1211 | agents/warren_bot.py — Business Quality Assessment Agent |
| `scheduler/orchestrator.py` | 2227 | scheduler/orchestrator.py — Bharat Intelligence Daily Orchestrator |
| `scheduler/performance_tracker.py` | 655 | scheduler/performance_tracker.py — Bharat Intelligence: Daily Performance Tracker |
| `scheduler/portfolio_monitor.py` | 1287 | scheduler/portfolio_monitor.py — Bharat Intelligence Portfolio Monitor |
| `scheduler/sector_pe_tracker.py` | 526 | scheduler/sector_pe_tracker.py — Daily Sector PE Snapshot Tracker |
| `scheduler/synthesis_validator.py` | 894 | scheduler/synthesis_validator.py — Pre-publication Agreement-Gated Validation |
| `governance/fact_checker.py` | 998 | governance/fact_checker.py — Bharat Intelligence Governance: Fact Checker |
| `governance/github_manager.py` | 644 | governance/github_manager.py — Bharat Intelligence: GitHub Automation Manager |
| `governance/hallucination_detector.py` | 1593 | governance/hallucination_detector.py — Bharat Intelligence Governance: Hallucination Detector |
| `governance/performance_tracker.py` | 1165 | governance/performance_tracker.py — Bharat Intelligence: Weekly Outcome Tracker |
| `governance/proposal_tags.py` | 80 | governance/proposal_tags.py |
| `governance/research_agent.py` | 1978 | governance/research_agent.py -- Bharat Intelligence: Daily AI Research Scanner |
| `data/breeze_auth.py` | 432 | data/breeze_auth.py — ICICI Breeze Connect Session Manager |
| `data/earnings_fetcher.py` | 309 | data/earnings_fetcher.py — Earnings Calendar Data Fetcher |
| `data/fetchers.py` | 2415 | data/fetchers.py — Bharat Intelligence India Market Data Fetchers |
| `data/forward_estimates.py` | 342 | data/forward_estimates.py — Forward Earnings Estimates |
| `data/fundamentals_cache.py` | 348 | data/fundamentals_cache.py — P7-I: persistent fundamentals cache with decay |
| `data/gift_nifty_fetcher.py` | 291 | data/gift_nifty_fetcher.py — P6-D-7: GIFT Nifty Pre-Market Signal Layer |
| `data/impact_cost.py` | 213 | data/impact_cost.py — Market Impact Cost Estimator |
| `data/insider_signal.py` | 201 | data/insider_signal.py — Promoter / Insider Signal Module |
| `data/llm_utils.py` | 57 | data/llm_utils.py — safe extraction of text from an Anthropic response. |
| `data/model_config.py` | 68 | data/model_config.py — the single source of truth for every LLM model ID. |
| `data/options_fetcher.py` | 724 | data/options_fetcher.py — Option Chain Fetcher (Trendlyne + Breeze + NSE + Fallback) |
| `data/proxy_session.py` | 162 | data/proxy_session.py — Outbound HTTP proxy abstraction |
| `data/run_cache.py` | 354 | data/run_cache.py — P7-H: intra-run memoisation for expensive remote fetches |
| `data/run_log.py` | 69 | data/run_log.py — one writer for the daily_runs table. |
| `data/symbol_map.py` | 348 | data/symbol_map.py — NSE Symbol Resolution & Normalisation |
| `data/trendlyne_analyst_fetcher.py` | 1005 | data/trendlyne_analyst_fetcher.py |
| `data/trendlyne_fetcher.py` | 1033 | data/trendlyne_fetcher.py |
| `data/trendlyne_fno_fetcher.py` | 539 | data/trendlyne_fno_fetcher.py |
| `api/main.py` | 3520 | api/main.py — Bharat Intelligence FastAPI Backend |

Dashboard: `dashboard/src/App.jsx` 4178 lines (single-file SPA) + `LongHorizonPanel.jsx`. Tests: 63 files.
