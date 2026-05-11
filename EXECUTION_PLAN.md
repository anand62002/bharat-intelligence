# Bharat Intelligence — Investment-Grade Execution Plan
### Target: 6.0 → 8.8 / 10 System Robustness
*Last updated: 2026-05-12*

> **Standing rules (apply after every build):**
> 1. Update `CLAUDE.md` — new files, tables, endpoints, env vars, resolved issues
> 2. Update this file — mark completed items ✅, update status column, add date completed

---

## 🗂️ VISUAL PROGRESS TRACKER

| # | Item | Phase | Status | Completed |
|---|---|---|---|---|
| Pre-1 | Run `earnings_calendar` SQL migration | Pre-work | ✅ **DONE** | 2026-05-04 |
| Pre-2 | Seed 150 historical RAG events | Pre-work | ✅ **DONE** | 2026-05-04 |
| 9 | Railway + Vercel log analysis | Step 9 | ✅ **DONE** | 2026-05-09 |
| P0-A | Sector-specific WACC (valuation_scenarios + warren_bot) | Phase 0 | ✅ **DONE** | 2026-05-04 |
| P0-B | Stock-specific macro sensitivities | Phase 0 | ✅ **DONE** | 2026-05-04 |
| P0-C | warren_bot notes column bug | Phase 0 | ✅ **DONE** (already fixed) | 2026-05-04 |
| P0-D | DCF owner earnings maintenance capex (0.6×) | Phase 0 | ✅ **DONE** | 2026-05-04 |
| P0-E | Discovery CRITICAL tier data quality gate | Phase 0 | ✅ **DONE** | 2026-05-04 |
| P0-F | Replace index-level FII filter → institutional_holding_pct | Phase 0 | ✅ **DONE** | 2026-05-04 |
| P1-A | Historical backtest framework | Phase 1 | ✅ **DONE** | 2026-05-09 |
| P1-B | Options real data feed (ICICI Breeze Connect) | Phase 1 | ✅ **DONE** | 2026-05-11 |
| P1-C | GPT-4o-mini as independent 3rd validation judge | Phase 1 | ✅ **DONE** | 2026-05-11 |
| P1-D | Calibrate composite score thresholds (75/58/30) | Phase 1 | ✅ **DONE** | 2026-05-04 |
| BF-1 | yfinance 1.2.0 progress=False silent failure (all prices stuck) | Bug Fix | ✅ **DONE** | 2026-05-12 |
| BF-2 | Discovery screener: NaN close, wrong FII field, threshold 3/5 | Bug Fix | ✅ **DONE** | 2026-05-12 |
| BF-3 | FII stale-zero filter (institutional.py) | Bug Fix | ✅ **DONE** | 2026-05-12 |
| BF-4 | India macro news monitoring (macro.py) | Enhancement | ✅ **DONE** | 2026-05-12 |
| BF-5 | Historical events embeddings backfill (98→150/150) | Data | ✅ **DONE** | 2026-05-12 |
| BF-6 | ARIA partial sell support + backend field-clobber fix | Enhancement | ✅ **DONE** | 2026-05-12 |
| BF-7 | Symbol resolution: SHAKTIPUMPS, GEVERNOVA, ELFORGE | Bug Fix | ✅ **DONE** | 2026-05-12 |
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

**Progress: 20 / 33 items complete (61%)**

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

### ✅ P1-A: Historical Backtest Framework
**Status:** Done 2026-05-09  
**New files:** `agents/backtester.py`, `db/migrations/create_backtest_results.sql`  
**Modified:** `api/main.py` (`GET /api/backtest/summary`), `worker.py` (monthly job)  
**New DB table:** `backtest_results`

**What was built:**

#### `agents/backtester.py` — full walk-forward engine
1. **Quality Universe** — downloads NIFTY 500 constituent list from NSE archives CSV; falls back to YF_SYMBOL_MAP if NSE is unreachable. Filters: market cap > ₹500 Cr via yfinance `fast_info`.
2. **Indicators** — RSI(14) via Wilder EMA smoothing, EMA(200), MACD(12,26,9) + bullish crossover detection.
3. **Signal logic** — BUY: RSI 40–65 AND price > EMA200 AND MACD bullish crossover. EXIT: RSI > 75 OR price < entry × 0.85 (15% SL) OR 90 days elapsed.
4. **Alpha measurement** — each trade's 90d and 180d return vs NIFTY 50 (^NSEI) over the same holding period.
5. **Walk-forward split** — TRAIN 2020–2022 (in-sample) | TEST 2023–2024 (out-of-sample). TEST is the meaningful metric.
6. **Metrics** — `hit_rate_90d` (% signals beating NIFTY), `avg_alpha_90d/180d`, `sharpe_ratio` (mean/std of alpha across trades), `max_drawdown` (worst single trade), `win_loss_ratio`.
7. **DB persistence** — inserts 3 rows per run (TRAIN, TEST, FULL) into `backtest_results`.

#### CLI
```powershell
python -m agents.backtester                          # default: 80 symbols, 2020–2024
python -m agents.backtester --max-symbols 30 --dry-run  # quick test
python -m agents.backtester --start 2021-01-01 --end 2024-12-31
```

#### API endpoint
```
GET /api/backtest/summary?split=TEST&limit=5
```
Returns last 5 monthly run summaries for the requested split.

#### Worker schedule
Monthly on 1st of month at 07:45 IST. Takes ~20–30 min for 80 symbols.

#### ⚠️ MANUAL STEP REQUIRED
**Run in Supabase SQL Editor:**
```sql
-- db/migrations/create_backtest_results.sql
```
The table must exist before the first backtest run or job save will fail silently.

---

### ✅ P1-B: Options Real Data Feed — ICICI Breeze Connect
**Status:** Done 2026-05-11  
**Provider chosen:** ICICI Breeze Connect (free — user has ICICI Direct demat account)  
**New files:** `data/breeze_auth.py`  
**Modified:** `data/options_fetcher.py`, `worker.py`, `requirements.txt`

**What was built:**

#### `data/breeze_auth.py` — Session manager
- `get_breeze_client()` — returns a configured `BreezeConnect` instance with 23-hour in-memory cache
- `refresh_session()` — supports two modes:
  - **Auto**: Uses `ICICI_USER_ID + ICICI_PASSWORD + BREEZE_TOTP_SECRET` to POST to ICICI login, parse redirect URL for session token — fully hands-off
  - **Manual**: Validates `BREEZE_SESSION_TOKEN` env var, logs hours remaining + reminder when expiring
- CLI: `python data/breeze_auth.py` / `--dry-run`

#### `data/options_fetcher.py` — Breeze as primary source (priority 1 of 3)
New functions added:
- `_get_near_expiry_date()` — computes next Thursday as Breeze-format expiry (`YYYY-MM-DDT06:00:00.000Z`)
- `_get_underlying_price(symbol)` — fast yfinance spot price lookup
- `_build_strike_range(spot, step, pct=0.08)` — generates ±8% strike range aligned to step
- `_fetch_breeze_option_chain(symbol)` — two-strategy fetch:
  1. **Bulk**: `get_option_chain_quotes(strike_price="")` for full chain in 2 calls (CE + PE)
  2. **Parallel**: Individual strikes via `ThreadPoolExecutor(10)` as fallback (~10s for 40 strikes)
  - 15-minute in-process cache per symbol
- `_parse_breeze_chain(rows, spot)` — converts Breeze rows → PCR, max pain, ATM IV, IV skew
- Source priority in `get_option_metrics()`: **breeze → nse → fallback** (was nse → fallback)
- Breeze result enriched with India VIX + HV20 from yfinance (for iv_hv_ratio)

#### `worker.py` — Daily token refresh job
- `job_breeze_token_refresh()` added
- Scheduled at **08:30 IST** (after earnings calendar 08:00, before first options snapshot 09:15)
- Auto-refreshes if `ICICI_USER_ID/PASSWORD/BREEZE_TOTP_SECRET` set; else logs manual reminder

#### `agents/options_sentiment.py` — No changes needed
Existing scoring logic (PCR, max pain, VIX, IV skew, IV/HV) works unchanged with any source.

**What improves when Breeze is active:**
- `source` changes from `"fallback"` → `"breeze"` in all `options_snapshot` logs
- PCR: real strike-level OI ratios instead of VIX-linear estimate
- Max pain: exact computation from actual OI vs heuristic σ estimate
- ATM IV: real implied vol from live option quote vs India VIX proxy
- IV skew: real put/call IV differential vs `None` (previously always missing)

**⚠️ Manual setup required on Railway:**

**Minimum (manual token, daily rotation):**
```
BREEZE_API_KEY=<from ICICI Direct API portal>
BREEZE_API_SECRET=<from ICICI Direct API portal>
BREEZE_SESSION_TOKEN=<fresh token, see steps below>
```

**How to get BREEZE_SESSION_TOKEN daily:**
1. Visit: `https://api.icicidirect.com/apiuser/login?api_key=<BREEZE_API_KEY>`
2. Login with ICICI Direct credentials + TOTP from your authenticator app
3. Copy the `code=` value from the redirect URL
4. Set `BREEZE_SESSION_TOKEN=<code>` in Railway worker + web env vars

**Optional (fully automated daily refresh — recommended):**
```
ICICI_USER_ID=<your ICICI login ID>
ICICI_PASSWORD=<your ICICI password>
BREEZE_TOTP_SECRET=<base32 secret from 2FA setup>
```
To find your TOTP secret: scan the QR code in your ICICI 2FA setup with a TOTP secret extractor app (or your authenticator app's export feature).

**Install dependency:**
```powershell
pip install breeze-connect pyotp
```
(Already added to `requirements.txt` — Railway deploys automatically)

---

### ✅ P1-C: GPT-4o-mini as Independent 3rd Validation Judge
**Status:** Done 2026-05-11  
**Files changed:** `scheduler/synthesis_validator.py`

**What was built:**
- Replaced 3rd judge (was `claude-haiku`) with `gpt-4o-mini` (genuine model diversity)
- Added lazy-init to `_call_anthropic_judge`: if `ant_client=None` at startup, reads `ANTHROPIC_API_KEY` from env and constructs its own Anthropic client on demand. Prevents auth failures when client is passed as None from the orchestrator.
- All 3 judges now have self-healing lazy-init: GPT-4o-mini, Claude Sonnet, Claude Opus all initialise their own clients if the passed-in client is None.

**Cost:** ~₹40-80/month (negligible — OpenAI key already in stack for embeddings)

---

---

## ✅ SESSION: Bug Fixes + Enhancements (2026-05-12)

> These were not in the original execution plan but were diagnosed and fixed in response to live system observations.

### ✅ BF-1: yfinance 1.2.0 — All Portfolio Prices Stuck at Upload Price
**Root cause:** yfinance 1.2.0 removed the `progress=` parameter from `history()`. All calls used `progress=False` which raised `TypeError`, silently caught by `except Exception: return None`. Every price refresh returned None for weeks.

**Additional yfinance 1.x issue:** yfinance now appends today's incomplete candle as `NaN` as the last row. `float(df["Close"].iloc[-1])` returned `float(NaN)` — broke discovery screener DataCompletenessValidator (every stock failed price>0 check).

**Files fixed:** `api/main.py`, `data/options_fetcher.py`, `agents/backtester.py`, `agents/discovery_screener.py`
- Removed `progress=False` from all `yf.history()` / `yf.download()` calls
- Added `.dropna()` before `iloc[-1]` in all price extractions
- `api/main.py _fetch_current_price`: added period fallback loop (1d → 5d → 1mo) — needed for BSE-only stocks (e.g. GE Vernova `522275.BO` returns empty on `period='1d'`)

---

### ✅ BF-2: Discovery Screener — 0 Pre-Screen Passes
Three bugs combined to produce 0 discoveries every day:

1. **NaN close price** (yfinance 1.x trailing row) — DataCompletenessValidator failed every stock. Fixed with `.dropna()`.
2. **Wrong screener field name** — code used `raw.get("fii_holding")` but screener.in returns `fii_holding_pct` + `dii_holding_pct`. HDFCBANK's 84% institutional holding was invisible → filter 3 always 0 < 5%. Fixed with correct field names + legacy fallback.
3. **Threshold 4/5 impossible** — `revenue_growth` almost always `None` from screener.in (multi-year data, no single figure), so max achievable was 4 filters. Lowered `_MIN_PRESCREEN_PASS` from 4 → 3.

**Verified:** HDFCBANK PASS (RSI 45.7 ✓, PE 15.7 ✓, institutional 84.2% ✓), BAJFINANCE PASS (4/5), DIXON correctly skipped (earnings guard).

---

### ✅ BF-3: FII Stale-Zero Data Masking NO_DATA State
**Root cause:** `institutional_flows` table had rows with `fii_net=0.0, dii_net=0.0` since April 22 (stored when NSE API was blocked and no live data available). `_fetch_historical_flows` returned these zero rows, which `_build_flow_history` included — making `data_quality="PARTIAL"` when it should be `"NO_DATA"`. Score of 50 (neutral) was being produced for the wrong reasons.

**Fix:** `_fetch_historical_flows` now filters out rows where both `fii_net=0.0` AND `dii_net=0.0` (treated as missing-data placeholders). Over-fetches 3× rows to survive filtering. Logs a warning when all DB rows are zero.

---

### ✅ BF-4: India Macro News Monitoring (PM Modi / RBI / Budget Announcements)
**Problem:** Macro agent only read FRED + RBI repo rate + VIX/INR. Major political/policy announcements (PM Modi speech, budget surprise, geopolitical event) were completely invisible until they showed up in price action.

**Fix added to `agents/macro.py`:**
- `_fetch_india_macro_news()`: fetches Google News RSS for 4 India macro query terms. Also uses NewsAPI if `NEWSAPI_KEY` set. No API key required for RSS path.
- `_score_macro_news()`: keyword-matches positive events (rate cut, trade deal, GST record) and negative shocks (war, tariff hike, capital flight). Returns ±10 score adjustment.
- `analyse()` now calls this as Step 1c. Adds `macro_news_signal` + `macro_news_events` to top-level output for synthesiser. Score adjustment applied to base indicator total.

---

### ✅ BF-5: Historical Events Embeddings Backfill
**Problem:** 150 events in `historical_events` table, only 52 had OpenAI embeddings, 98 were NULL. RAG agent was falling back to keyword-TF-IDF for 65% of the corpus.

**Fix:** Created `db/backfill_embeddings.py` — generates `text-embedding-3-small` (1536-dim) vectors for rows missing embeddings. **All 98 generated and stored.** Table is now 150/150 complete.

**Cost:** ~$0.0002 (98 rows × ~80 tokens at $0.02/1M).

---

### ✅ BF-6: ARIA Partial Sell Support + Backend Field-Clobber Fix
**Problem:** Selling 125 of 140 Voltas shares deleted the entire position (should leave 15 shares).

Three bugs:
1. **ARIA system prompt** had no `qty` field in exit JSON → ARIA couldn't express partial quantity
2. **`handlePortfolioUpdate`** always marked the full holding as "exited" regardless of qty
3. **`upsert_portfolio` backend** rebuilt the entire row from defaults on every update — `{symbol, status:"CLOSED"}` would set `qty=1`, `avg_buy=0`, etc. before closing

**Fix:**
- ARIA system prompt: added `qty` (required), explicit partial sell instructions
- `handlePortfolioUpdate`: checks `soldQty < holding.qty` → partial (reduce qty, keep status=holding) vs full exit
- `upsert_portfolio` backend: UPDATE path now only touches fields explicitly present in payload; INSERT path unchanged

---

### ✅ BF-7: Symbol Resolution — SHAKTIPUMPS, GEVERNOVA, ELFORGE
- `SHAKTIPUMPS` → `SHAKTIPUMP.NS` (NSE ticker drops trailing S)
- `GEVERNOVA` / `GE VERNOVA` / `GETDINDIA` → `522275.BO` (GE Vernova T&D India — only on BSE in Yahoo Finance, quoteType=MUTUALFUND)
- `ELFORGE` → `ELFORGE.BO` (BSE-listed only)

Added to both `data/symbol_map.py::YF_SYMBOL_MAP` and `api/main.py::_NSE_OVERRIDES`.

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
| **Step 9** | Railway + Vercel log analysis | Manual + AI review | None | 15 min | ✅ Done |
| **P0-A** | Sector-specific WACC | Code | None | M | ✅ Done |
| **P0-B** | Stock-specific macro sensitivities | Code | None | M | ✅ Done |
| **P0-C** | warren_bot notes column fix | Code | None | XS | ✅ Done |
| **P0-D** | DCF owner earnings maintenance capex | Code | None | XS | ✅ Done |
| **P0-E** | Discovery CRITICAL tier + new threshold | Code | None | S | ✅ Done |
| **P0-F** | Replace FII filter with institutional_holding_pct | Code | None | S | ✅ Done |
| **P1-A** | Historical backtest framework | Code + SQL | None | XL | ✅ Done |
| **P1-B** | Options real data feed (ICICI Breeze) | Code + Service | ₹0 | L | ✅ Done |
| **P1-C** | GPT-4o-mini as 3rd validation judge + Anthropic lazy-init | Code | OpenAI API (existing) | S | ✅ Done |
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

*Document version: 3.0 — 2026-05-12 (Phase 0 + Phase 1 + bug-fix session complete)*  
*Next milestone: P2-A (data provider diversification)*
