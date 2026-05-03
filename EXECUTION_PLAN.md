# Bharat Intelligence — Investment-Grade Execution Plan
### Target: 6.0 → 8.8 / 10 System Robustness
*Last updated: 2026-05-04*

> **Standing rules (apply after every build):**
> 1. Update `CLAUDE.md` — new files, tables, endpoints, env vars, resolved issues
> 2. Update this file — mark completed items ✅, update status column, add date completed

---

## 🗂️ VISUAL PROGRESS TRACKER

| # | Item | Phase | Status | Completed |
|---|---|---|---|---|
| Pre-1 | Run `earnings_calendar` SQL migration | Pre-work | ✅ **DONE** | 2026-05-04 |
| Pre-2 | Seed 150 historical RAG events | Pre-work | ✅ **DONE** | 2026-05-04 |
| 9 | Railway + Vercel log analysis | Step 9 | ⬜ TODO | — |
| P0-A | Sector-specific WACC (valuation_scenarios + warren_bot) | Phase 0 | ✅ **DONE** | 2026-05-04 |
| P0-B | Stock-specific macro sensitivities | Phase 0 | ✅ **DONE** | 2026-05-04 |
| P0-C | warren_bot notes column bug | Phase 0 | ✅ **DONE** (already fixed) | 2026-05-04 |
| P0-D | DCF owner earnings maintenance capex (0.6×) | Phase 0 | ✅ **DONE** | 2026-05-04 |
| P0-E | Discovery CRITICAL tier data quality gate | Phase 0 | ✅ **DONE** | 2026-05-04 |
| P0-F | Replace index-level FII filter → institutional_holding_pct | Phase 0 | ✅ **DONE** | 2026-05-04 |
| P1-A | Historical backtest framework | Phase 1 | ⬜ TODO | — |
| P1-B | Options paid data feed (Quantsapp / Upstox) | Phase 1 | ⬜ TODO | — |
| P1-C | GPT-4o as independent 3rd validation judge | Phase 1 | ⬜ TODO | — |
| P1-D | Calibrate composite score thresholds (75/58/30) | Phase 1 | ✅ **DONE** | 2026-05-04 |
| P2-A | Data provider diversification (Trendlyne fallback) | Phase 2 | ⬜ TODO | — |
| P2-B | RAG corpus auto-refresh monthly job | Phase 2 | ⬜ TODO | — |
| P2-C | Portfolio-level concentration alerts | Phase 2 | ⬜ TODO | — |
| P2-D | Earnings calendar auto-population job | Phase 2 | ⬜ TODO | — |
| P3-A | Position sizing output in recommendations | Phase 3 | ⬜ TODO | — |
| P3-B | Correlation-aware portfolio alerts | Phase 3 | ⬜ TODO | — |
| P4-A | Warren bot commentary grounding fix | Phase 4 | ⬜ TODO | — |
| P4-B | Symbol resolution cache persistence (DB-backed) | Phase 4 | ⬜ TODO | — |
| P4-C | Governance numerical grounding check | Phase 4 | ⬜ TODO | — |
| P5-A | Enhanced outcome tracker + agent attribution | Phase 5 | ⬜ TODO | — |
| P5-B | Paper portfolio simulation mode | Phase 5 | ⬜ TODO | — |
| P6-A | System performance dashboard tab | Phase 6 | ⬜ TODO | — |
| P6-B | Backtest results dashboard panel | Phase 6 | ⬜ TODO | — |

**Progress: 9 / 26 items complete (35%)**

---

## ✅ COMPLETED — Pre-work

### ✅ Pre-1: `earnings_calendar` DB Migration
**Status:** Done 2026-05-04  
**What was done:** Created `db/migrations/create_earnings_calendar.sql` and ran it in Supabase. Fixed partial index error (removed `WHERE earnings_date >= CURRENT_DATE` — `CURRENT_DATE` is volatile, not IMMUTABLE). Table created with `idx_ec_symbol` and `idx_ec_date` indexes.

### ✅ Pre-2: Seed 150 Historical RAG Events
**Status:** Done 2026-05-04  
**What was done:** Fixed RLS violation on `historical_events` table (disabled RLS entirely — static public reference table has no user-specific rows). Ran `python -m db.seed_historical_events_comprehensive --append` successfully. 150 events seeded.  
**Key fix recorded in:** `db/migrations/grant_service_role_rls.sql` → Section 6 now uses `ALTER TABLE historical_events DISABLE ROW LEVEL SECURITY`.

---

## ✅ COMPLETED — Phase 0 (All 6 items done 2026-05-04)

> All Phase 0 changes improve the **next production run** (06:00 IST daily). No manual steps needed.

### ✅ P0-A: Sector-Specific WACC
**Files changed:** `agents/valuation_scenarios.py`, `agents/warren_bot.py`  
**What changed:**
- Added `_SECTOR_WACC` dict in `valuation_scenarios.py` (FMCG/Healthcare 10% → Aviation 15%)
- Added `_get_sector_wacc(sector)` function — sector from `raw.get("sector")` via screener
- Added `_SECTOR_DISCOUNT_RATES` + `_get_sector_discount_rate(sector)` in `warren_bot.py`
- `_dcf_valuation()` now accepts optional `discount_rate` param; `analyse()` passes sector WACC from yfinance `info["sector"]`
- Hardcoded `DISCOUNT_RATE = 0.12` now only a fallback for unknown sectors

**Impact:** FMCG/Pharma intrinsic values increase (lower discount rate → higher NPV). Aviation/Metals values decrease (higher WACC → more conservative). Systematic, not arbitrary.

### ✅ P0-B: Stock-Specific Macro Sensitivities
**Files changed:** `agents/macro.py`, `scheduler/orchestrator.py`, `agents/discovery_screener.py`  
**What changed:**
- Added `get_sector_adjusted_macro_score(macro_result, sector)` at bottom of `agents/macro.py`
- Adjusts raw macro score ±8 pts based on `sector_impacts` already in macro result (IT+8 under weak INR, Oil&Gas -8, etc.)
- Returns `sector_adjusted=True` flag — prevents double-adjustment on repeat calls
- `orchestrator.py` `_run_agents_for_symbol()` calls this after Phase 1 fundamental result returns sector
- `discovery_screener.py` `_run_all_agents()` calls this after fundamental result

**Impact:** Every stock in a pipeline run now gets a different macro score reflecting its own sector's sensitivity. Before: all stocks identical.

### ✅ P0-C: warren_bot Notes Column Bug
**Files changed:** None  
**What found:** Already correct in current code. `_log_to_supabase()` only inserts `agent_name` + `audit_date`. No notes column attempted. Issue was already resolved prior to this session.

### ✅ P0-D: DCF Owner Earnings — Maintenance CapEx Adjustment
**File changed:** `agents/valuation_scenarios.py`  
**What changed:** `oe_list.append(pat + dep - capex)` → `oe_list.append(pat + dep - 0.6 * capex)`  
**Impact:** Owner earnings increase for capital-heavy businesses (only 60% of capex is treated as maintenance, 40% is growth investment that creates future value). Warren_bot already used this methodology — now both DCF engines are aligned.

### ✅ P0-E: Discovery CRITICAL Tier — Data Quality Gate + New Threshold
**File changed:** `agents/discovery_screener.py`  
**What changed:**
- `_CRITICAL_UPSIDE` changed from `100.0` → `40.0` (see P0-E rationale below)
- `_CRITICAL_CONF` changed from `70.0` → `75.0`
- Classification block now checks `fund_data_quality` before assigning CRITICAL tier
- If `data_quality` is ESTIMATED / NO_DATA / PARTIAL → demoted to STANDARD (not dropped)

**CRITICAL threshold rationale (why 40%, not 100%):**
> The old 100% upside threshold was a broken signal. On NSE, genuine 100% upside opportunities that pass multi-agent validation are vanishingly rare — the threshold was hit almost exclusively by screener data artefacts (stale earnings data for micro-caps, incorrect market cap fields). The new threshold `≥40% upside + ≥75% confidence + real data` is:
> - **Achievable for real stocks** — a Nifty 500 stock trading at a 30% discount to fair value with 10% expected earnings growth shows ~40% upside
> - **Meaningfully distinct from STANDARD** — 2× the upside bar (40 vs 20%) AND tighter confidence (75 vs 65%)
> - **Actionable signal** — 40%+ upside with high conviction warrants immediate attention; it's not a data error
> - **Protected by data gate** — only fires on real PAT data, not FCF proxy estimates

**What CRITICAL now means:** "This stock has been analysed with real fundamental data, shows ≥40% upside to fair value at ≥75% agent confidence — act on this at priority."

### ✅ P0-F: Replace Index-Level FII Filter in Discovery Pre-Screen
**File changed:** `agents/discovery_screener.py`  
**What changed:**
- Filter 3 in `prescreen()` replaced: was `_fii_net_buying(fii_data)` (NSE aggregate FII net flow — same value for all 200 symbols screened)
- Now: `institutional_holding_pct ≥ 5%` from screener data (stock-specific)
- Threshold simplified to 4-of-5 always (no more 3-of-4 relaxed path)
- `_fii_net_buying()` kept for API compatibility with a deprecation note

**Impact:** Filter 3 now measures whether smart money is actually present in this specific stock. Before, a day where FII sold ₹3,000cr of index futures would disqualify all 200 stocks regardless of whether they individually had FII ownership.

### ✅ P1-D: Confidence Calibration — Tighten Fallback Thresholds
**File changed:** `scheduler/orchestrator.py`  
**What changed:** `_fallback_synthesis()` thresholds:
- `≥72 = BUY` → `≥75 = BUY`  
- `≥55 = HOLD` → `≥58 = HOLD`
- `≤35 = AVOID` → `≤30 = AVOID`

**Impact:** Fewer BUY signals from the score-based fallback path (when Claude synthesis is unavailable). More conservative, reduces false positives.

---

## ⬜ STEP 9: Log Analysis (Do This Before Next Build)

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

## ⬜ PHASE 1 — Core Infrastructure Gaps

---

### ⬜ P1-A: Historical Backtest Framework
**New files:** `agents/backtester.py`, `db/migrations/create_backtest_results.sql`, `GET /api/backtest/summary`  
**New DB table:** `backtest_results`

**What it does:**
Uses 5-year yfinance OHLCV + current screener quality filters to replay technical entry signals and measure performance vs NIFTY 50.

**Methodology:**
1. **Quality Universe:** Filter NIFTY 500 using current screener data (ROCE > 15%, positive 5yr EPS CAGR, D/E < 1.5, market cap > 500 Cr, not disqualified by warren_bot)
2. **Technical Signal Replay:** On each trading day (2020–2024), compute RSI, EMA200, MACD using historical OHLCV. BUY when RSI 40–65 + price > EMA200 + MACD positive crossover. EXIT when RSI > 75 OR price drops 15% from entry.
3. **Walk-Forward Split:** Train 2020–2022, Test 2023–2024 (out-of-sample). Check for overfitting.
4. **Metrics per signal:** abs_return_90d, nifty_return_90d, alpha_90d, hit_rate, sharpe_ratio, max_drawdown, win_loss_ratio
5. **Output summary:** stored in `backtest_results` table; accessible via `/api/backtest/summary`

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
    hit_rate_90d    NUMERIC(5,2),
    avg_alpha_90d   NUMERIC(7,4),
    avg_alpha_180d  NUMERIC(7,4),
    sharpe_ratio    NUMERIC(6,3),
    max_drawdown    NUMERIC(7,4),
    win_loss_ratio  NUMERIC(6,3),
    signal_details  JSONB,
    created_at      TIMESTAMPTZ DEFAULT now()
);
GRANT ALL ON backtest_results TO service_role;
```

**CLI:** `python -m agents.backtester --period 2020-2024 --universe nifty500_quality`  
**Worker job:** Monthly (1st of month, 07:45 IST)  
**Manual step required:** ⚠️ Run migration SQL above in Supabase.

---

### ⬜ P1-B: Options Data Paid Feed Integration

**Provider Evaluation:**

| Provider | Monthly Cost | Data Type | Server-Side | Pre-computed Analytics | Recommendation |
|---|---|---|---|---|---|
| **Quantsapp Pro** | ₹2,499 | PCR, max pain, IV percentile, IV skew | ✅ REST API | ✅ Yes | ⭐ **Best fit** |
| Truedata | ₹1,500–2,000 | Raw option chain | ✅ REST + WS | ❌ DIY | Good but more work |
| NSE Data Products | ₹5,000+ | Official option chain | ✅ API | ❌ DIY | Too expensive |
| Upstox API | Free (needs demat) | Raw option chain | ✅ REST | ❌ DIY | Free if demat account |

**Recommendation: Quantsapp Pro (₹2,499/month)**

**Manual Setup Steps (Quantsapp):**
1. Sign up at https://quantsapp.com → subscribe to Pro plan
2. Get API key from Settings → API Access
3. Add `QUANTSAPP_API_KEY=xxx` to Railway env vars (web + worker)

**Manual Setup Steps (Upstox free alternative):**
1. Open free Upstox demat at https://upstox.com
2. Go to https://upstox.com/developer/apps → create app
3. Add `UPSTOX_API_KEY`, `UPSTOX_API_SECRET`, `UPSTOX_ACCESS_TOKEN` to Railway
4. ⚠️ Note: access token expires daily — daily token refresh job also needed

**Files to modify:** `data/options_fetcher.py`, `agents/options_sentiment.py`

---

### ⬜ P1-C: GPT-4o as Independent 3rd Validation Judge
**Context:** All 3 synthesis judges use Claude variants — correlated failure modes. GPT-4o as 3rd judge gives genuine model diversity.

**Note:** OpenAI API already in stack (RAG embeddings use `text-embedding-3-small`). Same key covers this.

**Manual steps:**
1. Confirm `OPENAI_API_KEY` is in Railway env vars (web + worker)
2. If not: get from https://platform.openai.com/api-keys → add to Railway
3. Smoke-test: `python -c "import openai; c=openai.OpenAI(); print('OK')"`

**Files:** `scheduler/synthesis_validator.py` — replace 3rd judge (currently `claude-haiku`) with `gpt-4o-mini`  
**Cost:** ~₹40-80/month (negligible)

---

## ⬜ PHASE 2 — Signal Quality Improvements

---

### ⬜ P2-A: Data Provider Diversification
**Problem:** 100% dependency on screener.in. One IP block = all fundamental agents down.  
**Fix:** Add Trendlyne as fallback in `data/fetchers.py`. Primary = screener.in → fallback = Trendlyne.

**Manual setup:**
1. Sign up at https://trendlyne.com/developers → get API key
2. Add `TRENDLYNE_API_KEY` to Railway env vars
3. **Cost:** ₹999/month

**Files:** `data/fetchers.py` (add `_get_trendlyne_data()` fallback)

---

### ⬜ P2-B: Historical RAG Corpus Auto-Refresh
**Current state:** 150 manually-curated events — static, not updated.  
**Fix:** Monthly job that scans NSE circulars, RBI policy releases, major corporate events → auto-appends to `historical_events` with OpenAI embeddings.  
**Files:** `db/auto_seed_rag.py` (new), `worker.py` (monthly job at 08:00 IST on 1st)  
**Manual step:** None after initial seed.

---

### ⬜ P2-C: Portfolio-Level Concentration Alerts
**Problem:** Individual holdings monitored but no portfolio-level concentration flagging.  
**Fix:** Alerts when >40% portfolio value in one sector, or 3+ holdings with same macro sensitivity.  
**Files:** `scheduler/portfolio_monitor.py` (`_check_concentration()`)

---

### ⬜ P2-D: Earnings Calendar Auto-Population
**Current state:** `earnings_calendar` table exists but is empty after initial seed — no ongoing refresh.  
**Fix:** `data/earnings_fetcher.py` populates from NSE bulletin + yfinance for portfolio + watchlist symbols. Daily at 08:30 IST.  
**Files:** `data/earnings_fetcher.py`, `worker.py`

---

## ⬜ PHASE 3 — Portfolio Intelligence

---

### ⬜ P3-A: Position Sizing Output in Recommendations
**What it is:** `suggested_position_pct` field on every recommendation — how much % of portfolio to allocate.

| Condition | Suggested Size |
|---|---|
| MOS > 40% AND warren_score ≥ 70 AND confidence ≥ 75% | Full position (5% of portfolio) |
| MOS > 20% AND confidence ≥ 65% | Half position (2.5%) |
| MOS > 0% AND confidence ≥ 55% | Quarter position (1.25%) |
| MOS < 0% or confidence < 55% | Avoid (0%) |

**Files:** `scheduler/orchestrator.py` (`_build_recommendation()`), `agents/valuation_scenarios.py`

---

### ⬜ P3-B: Correlation-Aware Portfolio Alerts
**Fix:** Weekly pairwise sector/theme overlap check across OPEN holdings.  
**Files:** `scheduler/portfolio_monitor.py`

---

## ⬜ PHASE 4 — Production Robustness

---

### ⬜ P4-A: Warren Bot Commentary Grounding
**Problem:** Claude Haiku commentary can contradict actual scores (no numerical grounding constraint).  
**Fix:** Structured JSON output with required fields referencing actual data numbers. Validate at least 2 data points appear in generated text.  
**Files:** `agents/warren_bot.py` (`_generate_commentary()`)

---

### ⬜ P4-B: Symbol Resolution Cache Persistence
**Current state:** `_symbol_cache` is process-memory only — cleared on every Railway redeploy.  
**Fix:** Use `symbol_resolutions` Supabase table (already exists) as persistent backing. Warm process cache from DB at startup.  
**Files:** `api/main.py` (`_resolve_yf_symbol()`)

---

### ⬜ P4-C: Governance Numerical Grounding Check
**Problem:** `fact_checker.py` uses LLM to check claims but doesn't deterministically verify numbers.  
**Fix:** Pre-LLM pass: extract numbers from synthesis text, compare against `agent_results` values. Auto-flag mismatches as CONTRADICTED before Haiku call.  
**Files:** `governance/fact_checker.py`

---

## ⬜ PHASE 5 — Forward Paper Portfolio Tracker

*Build after Phase 0–4 complete.*

---

### ⬜ P5-A: Enhanced Outcome Tracker + Attribution
**Gaps in current `outcome_tracker.py`:**
1. No confidence calibration (does 80% confidence actually win 80%?)
2. No agent-level attribution (which agent's signal was most predictive?)
3. No portfolio-level Sharpe from recommendation set

**New file:** `agents/performance_analyzer.py`  
**Worker job:** Weekly Sundays 08:00 IST  

---

### ⬜ P5-B: Paper Portfolio Simulation Mode
**What it does:** Simulates ₹10,000 per BUY rec from start date → tracks portfolio value vs NIFTY50.  
**Files:** `agents/paper_portfolio.py`, `GET /api/performance/paper-portfolio`

---

## ⬜ PHASE 6 — Dashboard & Reporting

*Data won't be meaningful until Aug 2025+. Build last.*

---

### ⬜ P6-A: System Performance Dashboard Tab
Shows: hit rate %, avg alpha at 90/180d, confidence calibration chart, top 5 best/worst calls, agent accuracy leaderboard.  
**Files:** `dashboard/src/App.jsx` (add `PerformanceTab`)

### ⬜ P6-B: Backtest Results Dashboard Panel
Walk-forward backtest summary — hit rate, avg alpha, Sharpe.  
**Files:** `dashboard/src/App.jsx` (add to PerformanceTab)

---

## OPTIONS DATA DECISION FLOWCHART

```
Do you already have a demat account with Upstox?
  YES → Use Upstox API (free) — see P1-B Upstox setup steps
  NO  → Open free Upstox account (15 min)
        OR sign up for Quantsapp Pro (₹2,499/month — simpler integration)

Quantsapp: Pre-computed PCR/max pain/IV skew — 1 API call per symbol
Upstox:    Free but needs daily token refresh job + our own PCR/max pain computation
```

---

## COMPLETE PRIORITY LADDER

| Priority | Item | Type | New Service / Cost | Effort | Status |
|---|---|---|---|---|---|
| **Pre-work** | Run `earnings_calendar` migration | Manual SQL | None | 2 min | ✅ Done |
| **Pre-work** | Seed 150 RAG events | CLI command | None | 5 min | ✅ Done |
| **Step 9** | Railway + Vercel log analysis | Manual + AI review | None | 15 min | ⬜ TODO |
| **P0-A** | Sector-specific WACC | Code | None | M | ✅ Done |
| **P0-B** | Stock-specific macro sensitivities | Code | None | M | ✅ Done |
| **P0-C** | warren_bot notes column fix | Code | None | XS | ✅ Done |
| **P0-D** | DCF owner earnings maintenance capex | Code | None | XS | ✅ Done |
| **P0-E** | Discovery CRITICAL tier + new threshold | Code | None | S | ✅ Done |
| **P0-F** | Replace FII filter with institutional_holding_pct | Code | None | S | ✅ Done |
| **P1-A** | Historical backtest framework | Code + SQL | None | XL | ⬜ TODO |
| **P1-B** | Options paid feed (Quantsapp/Upstox) | Code + Service | ₹0–2,499/mo | L | ⬜ TODO |
| **P1-C** | GPT-4o as 3rd validation judge | Code | OpenAI API (existing) | S | ⬜ TODO |
| **P1-D** | Calibrate composite score thresholds | Code | None | XS | ✅ Done |
| **P2-A** | Data provider diversification (Trendlyne) | Code + Service | ₹999/mo | L | ⬜ TODO |
| **P2-B** | RAG corpus auto-refresh monthly job | Code | None (OpenAI existing) | M | ⬜ TODO |
| **P2-C** | Portfolio-level concentration alerts | Code | None | M | ⬜ TODO |
| **P2-D** | Earnings calendar auto-population | Code | None | M | ⬜ TODO |
| **P3-A** | Position sizing output in recs | Code | None | S | ⬜ TODO |
| **P3-B** | Correlation-aware portfolio alerts | Code | None | M | ⬜ TODO |
| **P4-A** | Warren bot commentary grounding | Code | None | S | ⬜ TODO |
| **P4-B** | Symbol resolution cache persistence | Code | None | S | ⬜ TODO |
| **P4-C** | Governance numerical grounding check | Code | None | M | ⬜ TODO |
| **P5-A** | Enhanced outcome tracker + attribution | Code | None | L | ⬜ TODO |
| **P5-B** | Paper portfolio simulation mode | Code | None | L | ⬜ TODO |
| **P6-A** | System performance dashboard tab | Code | None | M | ⬜ TODO |
| **P6-B** | Backtest results dashboard panel | Code | None | S | ⬜ TODO |
| **Always** | CLAUDE.md + EXECUTION_PLAN.md update | Doc | None | XS | 🔄 Recurring |

*Effort: XS=<1hr · S=1-3hr · M=3-6hr · L=6-12hr · XL=12-24hr*

---

## TOTAL ESTIMATED NEW COSTS

| Service | Monthly Cost | Required For |
|---|---|---|
| Quantsapp Pro (options) | ₹2,499 (~$30) | P1-B — if no Upstox demat |
| Upstox API (options) | ₹0 | P1-B — if opening free demat |
| Trendlyne API (fundamentals fallback) | ₹999 (~$12) | P2-A |
| OpenAI API (GPT-4o-mini judges) | ~₹40-80/mo | P1-C — marginal (key already in stack) |
| **Total new monthly** | **₹1,039–3,498** | |

---

## RULE: End-of-Build Checklist (Every Session)

After every build session, before closing:

- [ ] CLAUDE.md updated — new files, tables, endpoints, env vars, resolved issues
- [ ] EXECUTION_PLAN.md updated — items marked ✅ with date, progress tracker refreshed
- [ ] `git commit` with descriptive message summarising the phase
- [ ] Deploy to Railway (auto via git push to main) and confirm health check passes

---

*Document version: 2.0 — 2026-05-04 (Phase 0 + Pre-work complete)*  
*Next milestone: Step 9 (log analysis) → P1-A (backtest framework)*
