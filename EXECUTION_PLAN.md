# Bharat Intelligence — Investment-Grade Execution Plan
### Target: 6.0 → 8.8 / 10 System Robustness
*Last updated: 2026-05-03*

> **Rule:** CLAUDE.md must be updated as the **final step** of every build phase below.
> **Frugality principle:** Every new paid service is explicitly justified. Code-only fixes are always done first.

---

## 📋 PENDING DB MIGRATIONS (Run These First — Manual Steps)

All 5 tables you confirmed exist. One missing table needs a new migration:

### ❌ MISSING: `earnings_calendar` table
The `agents/earnings_guard.py` primary lookup queries this table — it doesn't exist yet.

**Step 1:** Go to Supabase → SQL Editor and run:

```sql
-- Migration: earnings_calendar
-- Referenced by: agents/earnings_guard.py
-- Run once in Supabase SQL Editor

CREATE TABLE IF NOT EXISTS earnings_calendar (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol        TEXT NOT NULL,
    earnings_date DATE NOT NULL,
    quarter       TEXT,                       -- e.g. Q1FY26, Q4FY25
    source        TEXT DEFAULT 'yfinance',   -- yfinance / manual / nse_api
    confirmed     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_earnings_symbol_date UNIQUE (symbol, earnings_date)
);

CREATE INDEX IF NOT EXISTS idx_ec_symbol        ON earnings_calendar (symbol);
CREATE INDEX IF NOT EXISTS idx_ec_date          ON earnings_calendar (earnings_date);
CREATE INDEX IF NOT EXISTS idx_ec_upcoming      ON earnings_calendar (earnings_date)
    WHERE earnings_date >= CURRENT_DATE;

GRANT ALL ON earnings_calendar TO service_role;
```

### 📥 PENDING: Seed 150 Historical Events (Gap 11)
**Step 2:** Run locally (one-time):
```powershell
cd "C:\Users\gargi\OneDrive\Desktop\Claude code\Stock analysis"
python -m db.seed_historical_events_comprehensive --append
```

---

## 🔍 STEP 9: Log Analysis (Do This Before Any Build Work)

### Railway Logs
1. Go to Railway dashboard → your `web` service → **Logs** tab
2. Copy the last 200 lines of logs
3. Repeat for the `worker` service
4. Paste into Claude with this prompt:

```
You are a senior backend engineer reviewing production logs for Bharat Intelligence 
(FastAPI + APScheduler Python system on Railway). Analyse these logs and:
1. List all ERROR and WARNING entries with their root cause
2. Identify any recurring patterns (failing agents, timeout loops, DB errors)
3. Flag any silent failures (jobs running but producing wrong output)
4. List the top 3 issues to fix ordered by severity
5. For each issue, provide the exact file + line number and a specific code fix

Logs:
[PASTE LOGS HERE]
```

### Vercel Logs
1. Go to Vercel dashboard → your project → **Functions** tab → click `aria.js` and `research.js`
2. Check **Runtime Logs** for errors
3. Also check **Build Logs** for any env var or dependency issues
4. Paste into Claude with this prompt:

```
You are a senior frontend/serverless engineer reviewing Vercel function logs for 
Bharat Intelligence (React SPA + 2 serverless functions: aria.js and research.js).
Analyse these logs and:
1. List all errors with root cause
2. Identify any missing env vars or auth failures  
3. Flag any CORS or API routing issues
4. Provide specific fixes for each issue found

Logs:
[PASTE LOGS HERE]
```

---

## PHASE 0 — Immediate Code Fixes (Zero New Services, High Impact)
*These improve tomorrow's production run. Build first.*

---

### P0-A: Sector-Specific WACC (replaces hardcoded 12%)
**File:** `agents/valuation_scenarios.py`  
**Problem:** All stocks use 12% WACC regardless of sector risk, leverage, or beta.  
**Fix:** Add a sector→WACC lookup table. Infer sector from screener data.  

**WACC table (based on Indian market cost of capital):**
| Sector | WACC |
|---|---|
| FMCG / Consumer Staples | 10% |
| IT / Software | 11% |
| Pharma | 11.5% |
| Private Banks / NBFCs | 12% |
| Auto / Auto-Ancillary | 12% |
| Speciality Chemicals | 12.5% |
| Capital Goods / Industrials | 13% |
| Real Estate / Construction | 13.5% |
| PSU Banks | 13% |
| Metals / Mining | 14% |
| Infrastructure / Power | 13.5% |
| Aviation / Logistics | 15% |
| Default (unknown) | 12% |

**Also fix in:** `agents/warren_bot.py` (uses hardcoded `DISCOUNT_RATE = 0.12`)  
**Manual step:** None — code only.

---

### P0-B: Stock-Specific Macro Sensitivities
**File:** `scheduler/orchestrator.py` + `agents/macro.py`  
**Problem:** All stocks receive the same macro score (macro agent runs once, result cloned to every symbol).  
**Fix:** Add a sector macro sensitivity multiplier applied during composite scoring.

**Sensitivity mapping:**
| Macro Factor | Sector | Direction |
|---|---|---|
| INR/USD depreciation | IT/Pharma exporters | Positive (revenue ↑) |
| INR/USD depreciation | Oil importers, Airlines, Paints | Negative (costs ↑) |
| Crude oil spike | Aviation, Paints, Chemicals | Negative |
| Crude oil spike | ONGC, Reliance (E&P) | Positive |
| US 10Y yield rising | IT (P/E compression) | Negative |
| US 10Y yield rising | Banks (NIM expansion) | Positive |
| RBI rate cut | NBFCs, Housing Finance | Positive |
| RBI rate cut | Banks (NIM compression) | Slightly negative |

**Implementation:** `_SECTOR_MACRO_SENSITIVITY` dict in `agents/macro.py`. Composite score node reads sector from fundamental result and applies multiplier.  
**Manual step:** None — code only.

---

### P0-C: Fix `warren_bot._log_to_supabase()` Notes Column Error
**File:** `agents/warren_bot.py` line ~625  
**Problem:** CLAUDE.md documents this — warren_bot tries to write a `notes` column to `agent_performance` which doesn't exist → recurring WARNING in every production log.  
**Fix:** Remove `notes` from the INSERT dict in `_log_to_supabase()`.  
**Manual step:** None.

---

### P0-D: Fix DCF Owner Earnings — Maintenance CapEx Adjustment
**File:** `agents/valuation_scenarios.py` (`_extract_base_params`)  
**Problem:** `valuation_scenarios` uses `PAT + dep - capex` (full capex). Warren_bot correctly uses `0.6× capex`. This understates owner earnings for capital-heavy businesses.  
**Fix:** Change valuation_scenarios to use `PAT + dep - 0.6 × capex` to match warren_bot methodology.  
**Manual step:** None.

---

### P0-E: Fix Discovery CRITICAL Tier Threshold
**File:** `agents/discovery_screener.py`  
**Problem:** CRITICAL = upside ≥ 100% produces mostly false positives from screener data artefacts (stale/incorrect data for small caps).  
**Fix:** Add a data quality gate — CRITICAL tier only fires if `data_quality != "ESTIMATED"` AND `owner_earnings_cr > 0` (from actual PAT data, not proxy). Also add warren_bot score requirement: `warren_score >= 50` to qualify as CRITICAL.  
**Manual step:** None.

---

### P0-F: Calibrated Discovery Pre-Screen — Fix Index-Level FII Filter
**File:** `agents/discovery_screener.py`  
**Problem:** FII filter uses aggregate NSE FII net buy/sell (index-level), not stock-specific. A day of ₹5,000cr FII selling doesn't mean every individual stock saw selling.  
**Fix:** Remove the FII filter from per-stock pre-screening. Replace with: `institutional_ownership_high = screener data, FII holding % > 5%` as a proxy for institutional interest. Fall back to relaxed threshold (`_MIN_PRESCREEN_PASS_NO_FII = 3`) always.  
**Manual step:** None.

---

## PHASE 1 — Core Infrastructure Gaps
*Significant new capabilities. Highest business impact.*

---

### P1-A: Historical Backtest Framework
**New files:** `agents/backtester.py`, `db/migrations/create_backtest_results.sql`, `GET /api/backtest/summary`  
**New DB table:** `backtest_results`

**What it does:**
Uses 5-year yfinance OHLCV + current screener quality filters to replay technical entry signals and measure performance vs NIFTY 50.

**Methodology:**
1. **Quality Universe**: Filter NIFTY 500 using current screener data for characteristics that persist (ROCE > 15%, positive 5yr EPS CAGR, D/E < 1.5, market cap > 500 Cr, not disqualified by warren_bot)
2. **Technical Signal Replay**: On each trading day (2020–2024), compute RSI, EMA200, MACD using historical OHLCV. Generate BUY when RSI 40–65 + price > EMA200 + MACD positive crossover. Generate EXIT when RSI > 75 OR price drops 15% from entry.
3. **Walk-Forward Split**: Train 2020–2022, Test 2023–2024 (out-of-sample). Check for overfitting.
4. **Metrics per signal**: abs_return_90d, nifty_return_90d, alpha_90d, hit_rate, sharpe_ratio, max_drawdown, win_loss_ratio
5. **Output summary**: stored in `backtest_results` table; accessible via `/api/backtest/summary`

**DB Migration — run in Supabase SQL Editor:**
```sql
CREATE TABLE IF NOT EXISTS backtest_results (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_date        DATE NOT NULL DEFAULT CURRENT_DATE,
    universe        TEXT NOT NULL DEFAULT 'NIFTY500_QUALITY',
    period_start    DATE NOT NULL,
    period_end      DATE NOT NULL,
    split_type      TEXT CHECK (split_type IN ('TRAIN', 'TEST', 'FULL')),
    total_signals   INTEGER,
    hit_rate_90d    NUMERIC(5,2),      -- % of BUY signals that beat NIFTY50 at 90d
    avg_alpha_90d   NUMERIC(7,4),      -- mean (stock_return - nifty_return) at 90d
    avg_alpha_180d  NUMERIC(7,4),
    sharpe_ratio    NUMERIC(6,3),
    max_drawdown    NUMERIC(7,4),
    win_loss_ratio  NUMERIC(6,3),
    signal_details  JSONB,             -- per-symbol breakdown
    created_at      TIMESTAMPTZ DEFAULT now()
);
GRANT ALL ON backtest_results TO service_role;
```

**CLI:** `python -m agents.backtester --period 2020-2024 --universe nifty500_quality`  
**Worker job:** Monthly (1st of month, 07:45 IST) — re-runs backtest with latest quality filter + appends new 30 days of forward data  
**Manual step:** Run migration SQL above.

---

### P1-B: Options Data Paid Feed Integration

**Provider Evaluation:**

| Provider | Monthly Cost | Data Type | Server-Side | Pre-computed Analytics | Recommendation |
|---|---|---|---|---|---|
| **Quantsapp Pro** | ₹2,499 | PCR, max pain, IV percentile, IV skew | ✅ REST API | ✅ Yes | ⭐ **Best fit** |
| Truedata | ₹1,500–2,000 | Raw option chain | ✅ REST + WS | ❌ DIY | Good but more work |
| NSE Data Products | ₹5,000+ | Official option chain | ✅ API | ❌ DIY | Too expensive |
| Upstox API | Free (needs demat) | Raw option chain | ✅ REST | ❌ DIY | Free if demat account |
| Zerodha Kite Connect | ₹2,000 | Raw option chain | ✅ REST | ❌ DIY | Pricey |

**Recommendation: Quantsapp Pro (₹2,499/month)**
- Returns PCR, max pain, IV percentile, OI buildup directly via REST API call
- No computation on our side — maps exactly to `options_sentiment.py` inputs
- Server-side works without browser session (unlike NSE)

**Fallback: Upstox API (free)** — if you already have or are willing to open an Upstox demat account (zero brokerage, free account). The API provides full option chain; we compute PCR/max pain ourselves.

**Manual Setup Steps (Quantsapp):**
1. Sign up at https://quantsapp.com → subscribe to Pro plan
2. Get API key from dashboard → Settings → API Access
3. Add `QUANTSAPP_API_KEY=xxx` to Railway env vars (web + worker services)
4. Add to Vercel env vars if frontend needs it (not needed — backend only)

**Manual Setup Steps (Upstox alternative):**
1. Open free Upstox demat account at https://upstox.com
2. Go to https://upstox.com/developer/apps → create new app
3. Get API key + secret → generate access token (OAuth2 flow — one-time)
4. Add `UPSTOX_API_KEY`, `UPSTOX_API_SECRET`, `UPSTOX_ACCESS_TOKEN` to Railway
5. Note: access token expires daily — need a token refresh job (see implementation notes)

**Files to modify:** `data/options_fetcher.py` (replace fallback-only logic with provider client), `agents/options_sentiment.py` (update source field)

---

### P1-C: GPT-4o as Independent 3rd Validation Judge

**Context:** Currently all 3 synthesis judges use Claude variants — correlated sampling, not truly independent. Adding GPT-4o as the 3rd judge gives genuine model diversity.

**Note:** OpenAI API is already in the stack (used for Historical RAG embeddings via `text-embedding-3-small`). Same API key covers both.

**Manual Steps:**
1. Confirm `OPENAI_API_KEY` is set in Railway env vars (both web + worker)
2. If not set: get key from https://platform.openai.com/api-keys → add to Railway
3. Confirm key works: `python -c "import openai; c=openai.OpenAI(); print('OK')"`

**Files to modify:** `scheduler/synthesis_validator.py` — replace 3rd judge (currently `claude-haiku`) with `gpt-4o-mini` (cheaper, still independent model). Judge 1 = Claude Sonnet, Judge 2 = Claude Haiku, Judge 3 = GPT-4o-mini.

**Cost estimate:** GPT-4o-mini at ~$0.15/1M input tokens. 3 judges per recommendation × ~1,000 token prompt = $0.00045 per recommendation. Negligible.

---

### P1-D: Confidence Calibration — Fix Arbitrary Composite Thresholds
**Problem:** Fallback synthesis uses `≥72 = BUY, ≥55 = HOLD, ≤35 = AVOID` — round numbers with no empirical basis.  
**Fix:** After backtesting (P1-A), use hit-rate data to derive thresholds. Phase 1 interim fix: shift thresholds to `≥75 = BUY, ≥58 = HOLD, ≤30 = AVOID` (more conservative, fewer false BUYs).

**Files:** `scheduler/orchestrator.py` (`_fallback_synthesis` function)  
**Manual step:** None.

---

## PHASE 2 — Signal Quality Improvements

---

### P2-A: Data Provider Diversification — Secondary Fundamental Source
**Problem:** 100% dependency on screener.in scraper. One IP block = all fundamental agents down.  
**Options:**
- **Trendlyne API** (₹999/month) — financial ratios, screener-equivalent data, REST API
- **BSE India XBRL filings** (free) — raw regulatory filings, complex parsing
- **Tickertape API** (freemium) — fundamental data, simpler than screener.in

**Recommended approach:** Add Trendlyne as a fallback layer in `data/fetchers.py`. Primary = screener.in, fallback = Trendlyne if screener returns None.

**Manual setup:** Sign up at https://trendlyne.com/developers → get API key → add `TRENDLYNE_API_KEY` to Railway.  
**Cost:** ₹999/month.  
**Files:** `data/fetchers.py` (add `_get_trendlyne_data()` fallback function)

---

### P2-B: Historical RAG Corpus Expansion + Auto-Refresh
**Current state:** 150 manually-curated events. Static — not updated.  
**Fix:** Add a monthly job that scans recent NSE circulars, RBI policy releases, major corporate events, and auto-appends them to `historical_events` with embeddings via OpenAI `text-embedding-3-small`.  
**Files:** `db/auto_seed_rag.py` (new), `worker.py` (add monthly job at 08:00 IST on 1st)  
**Manual step:** None after initial seed.

---

### P2-C: Portfolio-Level Risk Aggregation
**Problem:** System monitors individual holdings but doesn't flag portfolio-level concentration.  
**Fix:** Add concentration alerts: if >40% portfolio value is in one sector → WARNING. If 3+ holdings have same macro sensitivity (e.g., all IT stocks) → INFO alert.  
**Files:** `scheduler/portfolio_monitor.py` (add `_check_concentration()`)  
**Manual step:** None.

---

### P2-D: Earnings Calendar Auto-Population
**Current state:** `earnings_calendar` table is empty — earnings_guard falls through to yfinance live probe every time.  
**Fix:** Build `data/earnings_fetcher.py` to populate the table from NSE bulletin board + yfinance calendar for all portfolio + watchlist symbols. Run daily at 08:30 IST.  
**Files:** `data/earnings_fetcher.py` (extend existing), `worker.py` (add daily job)  
**Manual step:** Run `earnings_calendar` migration (see top of this doc).

---

## PHASE 3 — Portfolio Intelligence

---

### P3-A: Position Sizing Output in Recommendations
**What it is:** Adds a `suggested_position_pct` field to every recommendation — how much % of a portfolio to allocate.

**Logic (tiered by Margin of Safety + Conviction):**
| Condition | Position Size |
|---|---|
| MOS > 40% AND warren_score ≥ 70 AND confidence ≥ 75% | Full (5% of portfolio) |
| MOS > 20% AND confidence ≥ 65% | Half (2.5%) |
| MOS > 0% AND confidence ≥ 55% | Quarter (1.25%) |
| MOS < 0% or confidence < 55% | Avoid (0%) |

**Note:** These are *suggestions*, not instructions. The user decides actual rupee amounts.  
**Files:** `scheduler/orchestrator.py` (`_build_recommendation()` + `valuation_scenarios.py`)  
**Manual step:** None.

---

### P3-B: Correlation-Aware Portfolio Alerts
**Problem:** No alerting when holdings are highly correlated (e.g., 4 IT stocks in portfolio = concentrated sector exposure).  
**Fix:** Add `correlation_alert` type to `portfolio_alerts`. Compute pairwise sector/theme overlap across all OPEN holdings weekly.  
**Files:** `scheduler/portfolio_monitor.py`  
**Manual step:** None.

---

## PHASE 4 — Production Robustness

---

### P4-A: Warren Bot Commentary Grounding Fix
**Problem:** Claude Haiku generates `why_buffett_would_like` without tight numerical grounding — can produce plausible text contradicting the actual scores.  
**Fix:** Add structured JSON output constraint to the Haiku prompt, requiring it to reference specific numbers (ROCE %, MOS %, EPS CAGR) from the data. Add validation: if generated commentary doesn't contain at least 2 numbers from the actual data → use template fallback.  
**Files:** `agents/warren_bot.py` (`_generate_commentary()`)  
**Manual step:** None.

---

### P4-B: Symbol Resolution Cache Persistence
**Current state:** `_symbol_cache` in `api/main.py` is process-memory only — cleared on every Railway redeploy.  
**Fix:** Use `symbol_resolutions` Supabase table (migration already applied) as a persistent cache. On startup, warm process cache from DB. Write new resolutions to both process + DB.  
**Files:** `api/main.py` (`_resolve_yf_symbol()`)  
**Manual step:** None (table already exists).

---

### P4-C: Governance Fact-Check — Add Numerical Grounding Check
**Problem:** `fact_checker.py` checks claim accuracy via Claude Haiku but doesn't verify that numerical claims (e.g., "20% revenue growth") match the underlying agent data.  
**Fix:** Add a deterministic pre-check before the Haiku call: extract numbers from synthesis text, compare against agent_results dict values. Flag mismatches as CONTRADICTED before even calling the LLM.  
**Files:** `governance/fact_checker.py`  
**Manual step:** None.

---

## PHASE 5 — Forward Paper Portfolio Tracker (Robust Version)

*Build after all Phase 0–4 items are done.*

---

### P5-A: Enhanced Forward Outcome Tracker
**Current state:** `agents/outcome_tracker.py` tracks per-recommendation outcomes (HIT/MISS/PARTIAL) at 90/180/365 days.  
**Gaps to fix:**
1. No benchmark-relative tracking (only absolute return, but alpha is computed)  
2. No confidence calibration — doesn't measure if 80% confidence calls win 80% of time
3. No position-sizing-weighted portfolio returns
4. No agent-level attribution — which agent's signal was most predictive?

**New file:** `agents/performance_analyzer.py` — reads `recommendation_outcomes`, computes:
- Hit rate by confidence decile (is 80% confidence actually 80%?)
- Per-agent signal accuracy (which single agent is most predictive of final outcome?)
- Portfolio Sharpe if recommendations were traded at equal weight
- Rolling 90-day alpha trend

**Worker job:** Weekly (Sundays 08:00 IST) — computes and writes to `agent_performance` table  
**Manual step:** None.

---

### P5-B: Paper Portfolio Simulation Mode
**What it does:** Simulates a paper portfolio from a start date where every BUY recommendation received a fixed ₹10,000 allocation. Tracks:
- Portfolio value over time vs NIFTY50 buy-and-hold
- Max drawdown
- Best/worst positions
- Sector concentration over time

**Files:** `agents/paper_portfolio.py` (new), `GET /api/performance/paper-portfolio` (new endpoint)  
**Manual step:** None. Initial simulation can back-populate from oldest `recommendation_outcomes` rows.

---

## PHASE 6 — Dashboard & Reporting

*Lowest priority — data won't be meaningful until enough outcomes accumulate (Aug 2025+).*

---

### P6-A: System Performance Dashboard Tab
**What it shows:**
- Hit rate % (BUY recs that beat NIFTY at 90d) — live from `recommendation_outcomes`
- Avg alpha vs NIFTY50 at 90/180 days
- Confidence calibration chart (expected vs actual win rate per confidence band)
- Top 5 best and worst calls
- Backtest summary (from `backtest_results` table)
- Agent accuracy leaderboard (which agent has highest predictive correlation?)

**Files:** `dashboard/src/App.jsx` (add `PerformanceTab` component)  
**Manual step:** None. Shows `EmptyState` until August 2025 data accumulates.

---

### P6-B: Backtest Results Panel in Dashboard
**What it shows:** Walk-forward backtest summary — hit rate %, avg alpha, Sharpe ratio vs benchmark. Links to methodology explanation.  
**Files:** `dashboard/src/App.jsx` (add to PerformanceTab)  
**Manual step:** None.

---

## OPTIONS DATA DECISION FLOWCHART

```
Do you already have a demat account with Upstox?
  YES → Use Upstox API (free) — see P1-B Upstox steps
  NO  → Open free Upstox account (15 min) OR
        Sign up for Quantsapp Pro (₹2,499/month)

Quantsapp: Simpler integration (pre-computed analytics, 1 API call per symbol)
Upstox:    Free but requires daily token refresh job + PCR/max pain computation
```

---

## COMPLETE PRIORITY LADDER

| Priority | Item | Type | New Service / Cost | Effort |
|---|---|---|---|---|
| **Pre-work** | Run `earnings_calendar` migration | Manual SQL | None | 2 min |
| **Pre-work** | Seed 150 RAG events | CLI command | None | 5 min |
| **Step 9** | Railway + Vercel log analysis | Manual + AI review | None | 15 min |
| **P0-A** | Sector-specific WACC | Code | None | M |
| **P0-B** | Stock-specific macro sensitivities | Code | None | M |
| **P0-C** | Fix warren_bot notes column error | Code | None | XS |
| **P0-D** | Fix DCF owner earnings (maintenance capex) | Code | None | XS |
| **P0-E** | Fix discovery CRITICAL tier threshold | Code | None | S |
| **P0-F** | Fix index-level FII filter in discovery | Code | None | S |
| **P1-A** | Historical backtest framework | Code + SQL | None | XL |
| **P1-B** | Options paid feed (Quantsapp/Upstox) | Code + Service | ₹0–2,499/mo | L |
| **P1-C** | GPT-4o as 3rd validation judge | Code | OpenAI API (existing) | S |
| **P1-D** | Calibrate composite score thresholds | Code | None | XS |
| **P2-A** | Data provider diversification (Trendlyne) | Code + Service | ₹999/mo | L |
| **P2-B** | RAG corpus auto-refresh monthly job | Code | None (OpenAI existing) | M |
| **P2-C** | Portfolio-level concentration alerts | Code | None | M |
| **P2-D** | Earnings calendar auto-population | Code | None | M |
| **P3-A** | Position sizing output in recs | Code | None | S |
| **P3-B** | Correlation-aware portfolio alerts | Code | None | M |
| **P4-A** | Warren bot commentary grounding | Code | None | S |
| **P4-B** | Symbol resolution cache persistence | Code | None | S |
| **P4-C** | Governance numerical grounding check | Code | None | M |
| **P5-A** | Enhanced outcome tracker + attribution | Code | None | L |
| **P5-B** | Paper portfolio simulation mode | Code | None | L |
| **P6-A** | System performance dashboard tab | Code | None | M |
| **P6-B** | Backtest results dashboard panel | Code | None | S |
| **Always** | CLAUDE.md update | Doc | None | XS |

*Effort scale: XS=<1hr, S=1-3hr, M=3-6hr, L=6-12hr, XL=12-24hr*

---

## TOTAL ESTIMATED NEW COSTS

| Service | Monthly Cost | Required? |
|---|---|---|
| Quantsapp Pro (options) | ₹2,499 (~$30) | P1-B — if no Upstox account |
| Upstox API (options) | ₹0 | P1-B — if opening demat account |
| Trendlyne API (fundamentals) | ₹999 (~$12) | P2-A — optional |
| OpenAI API (GPT-4o-mini judges) | ~₹40-80/mo | P1-C — marginal cost |
| **Total new monthly** | **₹1,039–3,498** | |

*OpenAI API already in stack for RAG embeddings. GPT-4o-mini judge cost is ~₹50/month at current recommendation volume.*

---

## RULE: CLAUDE.md Update Checklist (After Every Build Phase)

After completing each phase, update `CLAUDE.md` with:
- [ ] New files added (agents, migrations, data fetchers)
- [ ] New DB tables created
- [ ] New API endpoints
- [ ] New worker jobs + schedule times
- [ ] New env vars required
- [ ] Known issues resolved (remove from Known Issues section)
- [ ] New known issues discovered

---

*Document version: 1.0 — 2026-05-03*
*Next review: after Phase 0 completion*
