# Bharat Intelligence — Investment-Grade Execution Plan
### Target: 6.0 → 8.8 / 10 System Robustness
*Last updated: 2026-05-17*

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
| P2-A | Data provider diversification (yfinance fallback) | Phase 2 | ✅ **DONE** | 2026-05-12 |
| P2-B | RAG corpus auto-refresh monthly job | Phase 2 | ✅ **DONE** | 2026-05-12 |
| P2-C | Portfolio-level concentration alerts | Phase 2 | ✅ **DONE** | 2026-05-12 |
| P2-D | Earnings calendar auto-population job | Phase 2 | ⬜ TODO | — |
| P3-A | Position sizing output in recommendations | Phase 3 | ✅ **DONE** | 2026-05-13 |
| P3-B | Correlation-aware portfolio alerts | Phase 3 | ✅ **DONE** | 2026-05-14 |
| P3-C-BE | Trendlyne analyst targets scraper — consensus target, buy/hold/sell dist, EPS (Pillar B+E) | Phase 3 | ✅ **DONE** | 2026-05-15 |
| P3-C-P1 | Trendlyne Pillar 1 — fundamentals 10-yr series (screener.in fallback tier-2) | Phase 3 | ✅ **DONE** | 2026-05-16 |
| P3-C-P2 | Trendlyne Pillar 2 — corporate actions → earnings_calendar daily refresh | Phase 3 | ✅ **DONE** | 2026-05-16 |
| P3-C-P3 | Trendlyne Pillar 3 — DVM scores in discovery pre-screen | Phase 3 | ✅ **DONE** | 2026-05-16 |
| P3-C-P5 | Trendlyne Pillar 5 — BSE filings + insider sentiment in sentiment agent | Phase 3 | ✅ **DONE** | 2026-05-16 |
| P3-C-P6 | Trendlyne Pillar 6 — Insider/SAST signal in institutional agent | Phase 3 | ✅ **DONE** | 2026-05-16 |
| P3-D | Screener.in consolidated preference + Sales+/quarterly fix | Phase 3 | ✅ **DONE** | 2026-05-15 |
| P3-E | Trendlyne F&O memory cleanup (compile→compact dict, gc.collect) | Phase 3 | ✅ **DONE** | 2026-05-15 |
| DB-1 | Discovery tab blank (valid_till filter removed from 7→14d fallback) | Dashboard | ✅ **DONE** | 2026-05-15 |
| DB-2 | Governance stoploss dedup (holding_id + WebSocket broadcaster fix) | Dashboard | ✅ **DONE** | 2026-05-16 |
| DB-3 | Data source health panel in Governance tab (/api/system/health) | Dashboard | ✅ **DONE** | 2026-05-15 |
| DB-4 | Stale recs notice on Discovery tab when ideas are from prior day | Dashboard | ✅ **DONE** | 2026-05-15 |
| DB-5 | Recs tab empty state → link to health panel | Dashboard | ✅ **DONE** | 2026-05-15 |
| DB-6 | Performance tab — needs recommendation_outcomes seeding | Dashboard | ✅ **DONE** | (already built — PerformanceTab component + /api/performance/* endpoints existed) |
| DB-7 | Market tab — live news feed integration (Google News RSS per symbol) | Dashboard | ✅ **DONE** | 2026-05-17 |
| DB-8 | Portfolio recs tab — filter by portfolio holdings (only show recs for held stocks) | Dashboard | ✅ **DONE** | 2026-05-17 |
| DB-9 | ARIA — "What ran today?" button + daily_run context type | Dashboard | ✅ **DONE** | 2026-05-17 |
| DB-10 | Screener Export-to-Excel fallback — `_parse_screener_excel()` rewrites `Data Sheet` parser; POST to `/user/company/export/{id}/`; OPM% + EPS computed from raw fields; 31 tests; RELIANCE 10yr verified | Backend | ✅ **DONE** | 2026-05-17 |
| P4-A | Warren bot commentary grounding fix | Phase 4 | ✅ **DONE** | 2026-05-16 |
| P4-B | Symbol resolution cache persistence (DB-backed) | Phase 4 | ✅ **DONE** | (already built) |
| P4-C | Governance numerical grounding check | Phase 4 | ✅ **DONE** | 2026-05-16 |
| P4-D | Replace Breeze with Angel One SmartAPI — live options chain (lowest priority) | Phase 4 | ⬜ TODO | — |
| BF-8 | Discovery save silent failure — missing required DB columns + discoveries.append gate | Bug Fix | ✅ **DONE** | 2026-05-16 |
| BF-9 | Health panel daily_runs.status + agents_run column errors | Bug Fix | ✅ **DONE** | 2026-05-16 |
| BF-10 | Governance WebSocket broadcaster pushing all 107 raw alerts (bypassing dedup) | Bug Fix | ✅ **DONE** | 2026-05-16 |
| BF-11 | Synthesis 529 Overloaded — 3-attempt retry with 15s/45s backoff | Enhancement | ✅ **DONE** | 2026-05-16 |
| BF-12 | ARIA "What ran today?" shows 0/0/0 — snake_case vs camelCase mismatch in daily_run ARIA intro | Bug Fix | ✅ **DONE** | 2026-05-17 |
| P5-A | Enhanced outcome tracker + agent attribution | Phase 5 | ⬜ TODO | — |
| P5-B | Paper portfolio simulation mode | Phase 5 | ⬜ TODO | — |
| P5-C | Recommendation outcome seeder — backfill open recs into recommendation_outcomes table | Phase 5 | ✅ **DONE** | 2026-05-17 |
| P5-D | Forward outcome poller — daily job prices recs at t+30/60/90 days, writes alpha vs NIFTY | Phase 5 | ⬜ TODO | — |
| P5-E | Attribution dashboard — per-agent hit rate, avg alpha, best/worst calls over rolling 90d | Phase 5 | ⬜ TODO | — |
| P6-A | System performance dashboard tab | Phase 6 | ⬜ TODO | — |
| P6-B | Backtest results dashboard panel | Phase 6 | ⬜ TODO | — |
| P6-C | Market tab: daily start-of-day + end-of-day India market news digest (Claude + OpenAI dual summary) — scheduled 08:45 IST + 16:15 IST, stored in Supabase, served via `/api/market/digest`, rendered in Markets tab as collapsible "Morning Brief" / "Closing Digest" cards | Phase 6 | ⬜ TODO | — |
| P6-D | Elite News Intelligence Engine — FinBERT semantic layer + event classification (Janus-Q) + temporal decay + entity-centric aggregation + LLM ensemble (Claude+FinBERT) + India-native sources + backtesting loop | Phase 6 | ⬜ TODO | — |

**Progress: 53 / 59 items complete (90%)**

### Dashboard holes identified (2026-05-15)
| Issue | Root cause | Fix status |
|---|---|---|
| Discovery tab: "13 passed, 2 promoted" but blank recs | `valid_till < today` filter in 7d fallback excluded all recs | ✅ Fixed: 14d window, no valid_till filter |
| Portfolio recs tab always empty | Orchestrator generates recs for screener universe, not portfolio-specific | ✅ Fixed: DB-8 toggle "All / My Holdings" in recs tab |
| Governance: duplicate STOPLOSS_HIT per stock | API used wrong field (portfolio_id→holding_id); WebSocket broadcaster pushed all raw rows every 30s overriding dedup | ✅ Fixed: dedup by holding_id/symbol in both REST + WebSocket; SQL to bulk-resolve 107 stale DB rows |
| Performance tab no data | recommendation_outcomes table empty (recs < 90 days old) | ✅ DB-6: PerformanceTab already built; awaiting first 90-day recs |
| Market tab: empty news | No RSS-per-stock feed integrated in dashboard | ✅ Fixed: DB-7 Google News RSS panel with topic filter buttons |
| Screener returning standalone figures instead of consolidated | URL order was standalone-first; Reliance PE was 42x instead of 22.8x | ✅ Fixed: consolidated/ tried first |
| No visibility into data source failures without checking logs | No health endpoint or UI panel | ✅ Fixed: /api/system/health + Governance panel |

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

### ✅ P2-A: Data Provider Diversification — yfinance Fallback
**Status:** Done 2026-05-12  
**Files changed:** `data/fetchers.py`, `agents/fundamental.py`, `agents/discovery_screener.py`

**What was built:**

#### `data/fetchers.py` — `_get_yfinance_fundamentals()`
New function extracts fundamentals from `yfinance Ticker.info` — completely free, no API key, already in stack. Maps to the same output schema as `get_screener_data()`:
- `pe` (trailingPE), `revenue_growth` (revenueGrowth ×100), `ebitda_margin` (operatingMargins ×100)
- `debt_equity` (debtToEquity ÷100), `roce` (returnOnAssets ×100 as proxy), `roe` (returnOnEquity ×100)
- `fii_holding_pct` + `dii_holding_pct` estimated from `heldPercentInstitutions` (60/40 split)
- `ocf_margin` (operatingCashflow ÷ totalRevenue ×100), `sector`, `market_cap`
- Returns `data_source: "yfinance_fallback"` so agents know which path was taken

`get_screener_data()`: when all screener.in URL variants fail (IP block on Railway), calls yfinance fallback. Returns None only if both fail.

#### `agents/fundamental.py` — data quality tracking
- Detects fallback via `raw.get("data_source") == "yfinance_fallback"`
- `data_sources` gets `"yfinance_fundamentals"` instead of `"screener_in"`
- Result now includes `data_quality` key: `"FULL"` for screener.in, `"FALLBACK"` for yfinance
- `data_quality` was previously missing entirely — discovery screener gate never fired

#### `agents/discovery_screener.py` — FALLBACK ≠ ESTIMATED
- `"FALLBACK"` is NOT in the `is_estimated` blocked list — CRITICAL tier still allowed
- yfinance data is real market data, just less complete (no CAGR, no promoter pledging split)
- Logs when CRITICAL promotion uses fallback data

**Decision: yfinance over Trendlyne (₹999/month)**
yfinance provides the most critical fields (P/E, ROE, operating margin, revenue growth, D/E, institutional %) and is already in the stack with zero cost. Trendlyne would add CAGR series and promoter pledging but requires manual API signup + monthly cost. Can add Trendlyne as a second fallback later if needed.

**Fields NOT available in yfinance fallback (returned as None):**
`revenue_cagr_3y`, `revenue_cagr_5y`, `eps_cagr_5y`, `promoter_pledging`, `interest_coverage`, exact FII/DII split

**Verified:** RELIANCE.NS → pe=23.3, roe=9.1, revg=12.5%, ebitda=9.98%, de=0.37

---

### ✅ P2-B: Historical RAG Corpus Auto-Refresh
**Status:** Done 2026-05-12  
**Files:** `db/auto_seed_rag.py` (new), `worker.py` (monthly job added), `tests/test_auto_seed_rag.py` (74 tests, all passing)

**What was built:**

#### `db/auto_seed_rag.py` — monthly RAG event seeder
- Fetches India macro/market news from **8 Google News RSS queries** (no API key needed) for the last 35 days
- **Keyword relevance pre-filter** (`_is_relevant`) — 40+ keywords gate articles before any LLM call; eliminates company-specific micro news and irrelevant articles
- **Classification** — two-tier:
  - *Primary*: `gpt-4o-mini` structured JSON with enum-constrained output (event_type + market_impact + sectors + outcome + is_significant) — used when `OPENAI_API_KEY` set
  - *Fallback*: Pure keyword-rule classifier (`_classify_keyword_fallback`) — always available, zero cost; covers 18 event types, uses `re.search` word-boundary matching to avoid substring false positives (e.g. "flows" ≠ "low")
- **Deduplication** (`_deduplicate_articles`) — fetches existing events from DB, skips any article whose `event_type` already has an entry within ±7 days (prevents duplicate RBI / Budget / FII entries for same real event)
- **Embedding generation** — `text-embedding-3-small` when OpenAI key available; otherwise inserts with `embedding=NULL` for `backfill_embeddings.py` to handle later
- **Cap** — max 30 new events per run (configurable via `--max`)
- **Returns** `{added, skipped_duplicate, skipped_irrelevant, errors, articles_checked, dry_run}`

#### CLI
```powershell
python -m db.auto_seed_rag                  # dry-run: show what would be added
python -m db.auto_seed_rag --run            # actually insert + embed
python -m db.auto_seed_rag --run --days 60  # look back 60 days
python -m db.auto_seed_rag --run --max 20   # cap at 20 events
```

#### `worker.py` — monthly job
- `job_rag_refresh()` added
- Scheduled at **08:15 IST on 1st of each month** (after backtest 07:45 + earnings calendar 08:00)
- Logs: `added / skipped_dup / skipped_irrel / errors / articles_checked`

#### Bug caught during testing
- Python substring trap: `"low" in "flows"` is `True` — "capital flows" was triggering CURRENCY_CRISIS. Fixed with `re.search(r"\b(fall|fallen|low|weak|...)\b", text)` word-boundary matching in `_classify_keyword_fallback`.

---

### ✅ P2-C: Portfolio-Level Concentration Alerts *(completed 2026-05-12)*
**Problem:** Individual holdings monitored but no portfolio-level concentration flagging.  
**Fix:** Alerts when >40% portfolio value in one sector, or 3+ holdings with same macro sensitivity.  
**Files:** `scheduler/portfolio_monitor.py` (`_check_concentration()`, `_get_macro_sensitivity()`, `_portfolio_alert_exists()`)  
**Tests:** `tests/test_portfolio_concentration.py` — 54 tests, all passing  
**Key details:**
- `_MACRO_SENSITIVITY_MAP` — 5 macro categories (Rate-Sensitive, USD-Sensitive, Domestic Demand, Commodity-Linked, Infra/Capex) with word-boundary regex matching
- SECTOR_CONCENTRATION: severity=WARNING, alert_type fires when any known sector > 40% of portfolio value; "Other"/uncategorised excluded
- MACRO_CLUSTER: severity=WARNING fires when ≥ 3 holdings share same macro sensitivity category
- `_portfolio_alert_exists()` deduplicates by alert_type + symbol (not holding_id) with 24h window
- `holding_id=None` on portfolio-level alerts (no specific holding); enriched holdings with fresh prices fed in from `run()`

---

### ⬜ P2-D: Earnings Calendar Auto-Population
**Current state:** `earnings_calendar` table exists but is empty after initial seed — no ongoing refresh.  
**Fix:** `data/earnings_fetcher.py` populates from NSE bulletin + yfinance for portfolio + watchlist symbols. Daily at 08:30 IST.  
**Files:** `data/earnings_fetcher.py`, `worker.py`

> ⚠️ **Note:** P2-D will be superseded by P3-C Pillar 2 (Trendlyne corporate actions → earnings_calendar). Implement P2-D only if P3-C is not prioritised first — avoid duplicating the earnings calendar pipeline.

---

## ⬜ PHASE 3 — Portfolio Intelligence

---

### ✅ P3-A: Position Sizing Output in Recommendations *(completed 2026-05-13)*
**What it is:** `suggested_position_pct` field on every recommendation — how much % of portfolio to allocate.

| Condition | Suggested Size |
|---|---|
| MOS > 40% AND warren_score ≥ 70 AND confidence ≥ 75% | Full position (5% of portfolio) |
| MOS > 20% AND confidence ≥ 65% | Half position (2.5%) |
| MOS > 0% AND confidence ≥ 55% | Quarter position (1.25%) |
| MOS < 0% or confidence < 55% | Avoid (0%) |

**Files:** `agents/position_sizer.py` (new), `scheduler/orchestrator.py`, `agents/discovery_screener.py`, `api/main.py`, `dashboard/src/App.jsx`  
**DB migration:** `db/migrations/add_position_size_to_recommendations.sql` — adds `suggested_position_pct NUMERIC(5,2)` + `position_label TEXT`  
**Tests:** `tests/test_position_sizer.py` — 45 tests, all passing  
**Key details:**
- MOS source priority: warren_bot DCF `margin_of_safety_pct` → `upside_pct` proxy fallback
- FULL tier (5%) requires DCF-backed MOS — proxy cannot qualify (quality gate prevents false positives)
- AVOID/SELL actions always return 0% regardless of scores
- Wired into orchestrator after warren_bot attachment; also applied to discovery screener saves
- API: `suggestedPositionPct` + `positionLabel` in `_transform_recommendation()`
- Dashboard: 📐 position badge on both recommendation cards and discovery cards

---

### ✅ P3-B: Correlation-Aware Portfolio Alerts — DONE (2026-05-14)
**Implemented:** 60-day Pearson return correlation across all OPEN holdings.
Fires `CORR_CLUSTER` WARNING alert when ≥2 pairs exceed r=0.75, with 7-day dedup.
**Files:** `scheduler/portfolio_monitor.py` (`_compute_correlation_pairs`, `_check_correlation`)
**Tests:** `tests/test_portfolio_correlation.py` — 26 tests, all passing.

---

### ⬜ P3-C: Comprehensive Trendlyne Integration
**Status:** TODO  
**Why:** Trendlyne is not just a data fallback — it is a complete India equity intelligence platform offering signals not available anywhere else for free. Six distinct integration pillars make this the highest-ROI paid subscription in the stack.

---

#### What Trendlyne offers (evaluation summary)

| Module | What it provides | Current gap it fills |
|---|---|---|
| **DVM scores** | Pre-computed Durability (0–100) + Valuation (0–100) + Momentum (0–100) composite per stock | Discovery pre-screen has no quality-vs-price composite signal |
| **Fundamentals** | Full 10-yr annual series: revenue, PAT, EPS, CAGR, ROCE, promoter pledging, debt | warren_bot + discovery severely limited when screener.in blocked |
| **Analyst consensus** | Price target, EPS estimate, revenue estimate from 2–15 brokers per stock | No independent cross-check on our upside_pct |
| **Corporate filings / news** | BSE XML filings feed + earnings call transcripts + management commentary | Sentiment agent uses only Google News RSS — misses filings |
| **Insider/SAST trades** | Promoter buying/selling + SAST acquirer disclosures | No insider signal anywhere in current stack |
| **Corporate actions** | Dividends, splits, rights, bonus — calendar with dates | earnings_calendar auto-population (P2-D becomes part of this) |

---

#### Recommended subscription tier

| Tier | Price | Key unlocks |
|---|---|---|
| GuruQ | ₹310/month | 1,758 screener params, 75 alerts |
| **StratQ** *(recommended)* | **₹492/month** *(₹5,900/year)* | 3,500+ params, 300 real-time alerts, **unlimited data downloads**, full historical series |

> StratQ annual = ₹492/month ≈ $6/month. One alpha trade pays for multiple years.

---

#### Access method (no official API)
Trendlyne has **no public API**. Access via JSON endpoint scraping with session cookies — same pattern as `get_screener_data()` in `data/fetchers.py`.

```python
# Pattern (same as screener.in):
import requests
session = requests.Session()
session.cookies.set("sessionid", TRENDLYNE_SESSION_COOKIE)
session.cookies.set("csrftoken",  TRENDLYNE_CSRF_TOKEN)

# DVM scores:
r = session.get(f"https://trendlyne.com/equity/dvm-score/{symbol}/")
# Fundamental data:
r = session.get(f"https://trendlyne.com/equity/fundamental/{symbol}/ajax/")
# Analyst estimates:
r = session.get(f"https://trendlyne.com/equity/analyst-estimate/{symbol}/ajax/")
# Insider trades:
r = session.get(f"https://trendlyne.com/equity/insider-trades/{symbol}/ajax/")
# Corporate filings (BSE XML):
r = session.get(f"https://trendlyne.com/equity/bse-filings/{symbol}/ajax/")
```

Session cookie obtained from browser DevTools after manual login. Set as `TRENDLYNE_SESSION_COOKIE` + `TRENDLYNE_CSRF_TOKEN` env vars on Railway.

---

#### Integration architecture — 6 pillars in priority order

**Pillar 1 — Fundamental data (primary extension, not just fallback)**  
`data/trendlyne_fetcher.py` → `get_trendlyne_fundamentals(symbol)`:
- 10-yr annual revenue/PAT/EPS/CAGR series (replaces screener.in as primary for warren_bot historical series)
- Promoter pledging % (actual figure, not scraper estimate)
- ROCE consistency over 10 years (needed for moat_score)
- Returns `data_source: "trendlyne"`, `data_quality: "FULL"`

Integration: `data/fetchers.py::get_screener_data()` fallback chain becomes:
`screener.in → trendlyne → yfinance → None`

**Pillar 2 — Corporate actions → earnings_calendar (supersedes P2-D)**  
`data/trendlyne_fetcher.py` → `get_corporate_actions(symbols)`:
- Earnings result dates + board meeting dates from Trendlyne's BSE feed
- Daily refresh at 08:00 IST in `worker.py`
- Upserts to `earnings_calendar` table (already exists)
- Replaces P2-D (NSE bulletin + yfinance approach) — this is cleaner + richer

> **Note:** P2-D should be implemented as part of P3-C, not separately.

**Pillar 3 — DVM scores in discovery pre-screen**  
`agents/discovery_screener.py::prescreen()`:
- Add Filter 6 (optional, bonus): `dvm_momentum_score ≥ 45` (momentum not overheated)
- Add `valuation_score` to metadata — surfaced in discovery card on dashboard
- DVM Durability score enriches `data_quality` assessment

DVM score thresholds (Trendlyne's own scale):
- Durability: ≥ 60 = quality business, ≥ 80 = exceptional
- Valuation: 40–70 = reasonably priced, < 40 = cheap/deep-value, > 80 = expensive
- Momentum: 45–70 = constructive, > 80 = overheated/overbought

**Pillar 4 — Analyst consensus cross-validation**  
`agents/fundamental.py` enrichment (post-analysis):
- If `analyst_target_price` available AND our `intrinsic_value` differs by >30% → flag `valuation_divergence: true` in result
- Add `analyst_consensus_target` + `analyst_count` to recommendation metadata
- Synthesiser uses divergence flag as a confidence moderator (reduces confidence if analysts disagree by >30%)

**Pillar 5 — News + BSE filings in sentiment agent**  
`agents/sentiment.py`:
- Replace / augment current Google News RSS call with Trendlyne BSE filings feed
- Earnings call transcript NLP: extract management tone (capex guidance, revenue outlook, margin commentary) as structured sentiment signals
- Insider trades signal: `promoter_buying_3m` > 0 → +5 sentiment pts; `promoter_selling_3m > 2%` → -10 pts

**Pillar 6 — Insider/SAST signal in institutional agent**  
`agents/institutional.py`:
- New function `_get_insider_signal(symbol)` — fetches Trendlyne SAST disclosures
- SAST acquisition (bulk deal, open market buy by promoter) → `smart_money_signal: ACCUMULATING`
- Weighted +8 pts to institutional score when active promoter buying detected

---

#### New files / changes required

| File | Change |
|---|---|
| `data/trendlyne_fetcher.py` | New module — all 6 fetch functions + session management |
| `data/fetchers.py` | Add trendlyne to fallback chain in `get_screener_data()` |
| `agents/fundamental.py` | Analyst consensus cross-check + valuation_divergence flag |
| `agents/sentiment.py` | BSE filings feed + transcript NLP + insider sentiment signal |
| `agents/institutional.py` | `_get_insider_signal()` from Trendlyne SAST |
| `agents/discovery_screener.py` | DVM Momentum filter + valuation_score to metadata |
| `agents/warren_bot.py` | Use trendlyne 10-yr series when screener.in unavailable |
| `scheduler/worker.py` | Daily corporate actions job at 08:00 IST |
| `db/migrations/` | No new tables needed — `earnings_calendar` already exists |

---

#### New environment variables

| Variable | Description |
|---|---|
| `TRENDLYNE_SESSION_COOKIE` | Browser session cookie (rotate every ~30 days) |
| `TRENDLYNE_CSRF_TOKEN` | CSRF token paired with session cookie |

Add both to Railway `worker` + `web` services.

---

#### What this supersedes / absorbs

| Existing item | Disposition after P3-C |
|---|---|
| P2-A yfinance fallback | Remains as tier-3 fallback (screener.in → trendlyne → yfinance) |
| P2-D earnings calendar auto-populate | Absorbed into P3-C Pillar 2 — do not implement P2-D separately |
| Google News RSS in macro.py (BF-4) | Augmented — macro agent keeps RSS, sentiment agent gets Trendlyne filings |

---

#### Implementation order (within P3-C)

1. Set up `data/trendlyne_fetcher.py` with session management + test DVM endpoint
2. Wire Pillar 1 (fundamentals) into fallback chain — highest immediate ROI
3. Wire Pillar 2 (corporate actions → earnings_calendar) to replace P2-D
4. Wire Pillar 3 (DVM scores) into discovery pre-screen
5. Wire Pillar 4 (analyst consensus) into fundamental.py
6. Wire Pillar 5 (filings/transcripts) into sentiment.py
7. Wire Pillar 6 (insider/SAST) into institutional.py

**Effort estimate:** L-XL (8–16 hours). Can be done in pillars independently — Pillar 1 alone is a partial win.

---

## ⬜ PHASE 4 — Production Robustness

---

### ✅ P4-A: Warren Bot Commentary Grounding *(completed 2026-05-16)*
**Problem:** Claude Haiku commentary could say anything — free-form text with no constraint to cite actual numbers, making it possible to generate commentary that silently contradicted the real ROCE / EPS CAGR / MoS values computed by the scoring engine.

**Fix:**
1. **`_validate_commentary(text, anchor_values)`** — checks that ≥2 pre-formatted numeric strings (e.g. `"25.3"`, `"18.2"`) appear as substrings in the generated text. Returns `False` if fewer anchors found.
2. **`_build_grounded_commentary(symbol, score, signal, moat_type, roce_avg, eps_cagr, mos_pct)`** — deterministic template fallback that always embeds actual numbers. Tone calibrated to `signal`: AVOID=rejection language, QUALITY_BUY=cautiously positive, WATCHLIST=price-conditional.
3. **`_generate_commentary` rewritten** — asks Haiku for structured JSON `{"why_like": "...", "why_pass": "..."}` with an explicit prompt listing all data points and requiring ≥2 to be cited. After JSON parse, runs `_validate_commentary`; falls back to `_build_grounded_commentary` if validation fails or JSON is malformed.
4. **`signal` parameter added** — `analyse()` now passes `signal=signal` so commentary tone is consistent with the actual recommendation.

**Fallback chain (commentary always grounded):**
- No API key → `_build_grounded_commentary`
- API error → `_build_grounded_commentary`
- Non-JSON response → `_build_grounded_commentary`
- Valid JSON but validation fails (no real numbers cited) → `_build_grounded_commentary`
- Valid JSON + validation passes → LLM text used as-is

**Files changed:** `agents/warren_bot.py`  
**Tests:** `tests/test_warren_bot.py` — added 3 new test classes (27 tests): `TestValidateCommentary`, `TestBuildGroundedCommentary`, `TestGenerateCommentary`, `TestCommentaryGroundingIntegration`. All 62 warren_bot tests pass.

---

### ⬜ P4-B: Symbol Resolution Cache Persistence
**Current state:** `_symbol_cache` is process-memory only — cleared on every Railway redeploy.  
**Fix:** Use `symbol_resolutions` Supabase table (already exists) as persistent backing. Warm process cache from DB at startup.  
**Files:** `api/main.py` (`_resolve_yf_symbol()`)

---

### ✅ P4-C: Governance Numerical Grounding Check *(completed 2026-05-16)*
**Problem:** `fact_checker.py` passed every claim to Claude Haiku — including simple numeric comparisons where "PE is 40.0" vs actual PE=22.5 could be verified deterministically without an LLM. Haiku could silently agree with wrong numbers or produce false negatives.

**Fix — deterministic pre-LLM pass:**

1. **`_NUMERIC_TOLERANCES`** dict — per-metric tolerance config:
   - Relative tolerances (fraction of actual): `pe` ±15%, `revenue_growth` ±20%, `ebitda_margin/debt_equity` ±10–15%, `roce/roe` ±10%, `ema50/ema20` ±2%
   - Absolute tolerances (units): `promoter_holding` ±2pp, `promoter_pledging` ±2pp, `rsi` ±5 points

2. **`_extract_numeric_from_source(metric_key, source_name, cached_data)`** — extracts actual value from already-fetched source cache:
   - Screener.in dict → direct key lookup
   - OHLCV DataFrame → computes RSI (Wilder EMA), EMA20, EMA50

3. **`_numerical_grounding_check(claims, source_cache, symbol)`** — pre-LLM pass:
   - For each claim in `_NUMERIC_TOLERANCES`: extract actual, compare, set `claim.status`:
     - Within tolerance → `"VERIFIED"` (Haiku skipped)
     - Outside tolerance → `"CONTRADICTED"` + `corrected_claim` set (Haiku skipped)
     - Unavailable → status unchanged → Haiku handles it
   - Returns count of deterministically resolved claims (logged)

4. **`_verify_claim` updated** — skips Haiku entirely if `claim.status` already set

5. **`_check_one` updated** — calls `_numerical_grounding_check` before the Haiku loop; logs how many claims were resolved deterministically

**Impact:**
- PE, ROCE, promoter holding, EBITDA margin, RSI, EMA claims verified/contradicted without LLM calls
- Eliminates false negatives where Haiku "agrees" with a wrong number
- Produces exact corrected values: *"Actual ROCE is 8.2%, not 30.0%"*
- Reduces Haiku API calls for numeric-heavy recommendations (typically 3–5 of 7 claims)

**Files changed:** `governance/fact_checker.py`  
**New file:** `tests/test_fact_checker.py` — 40 tests covering `_extract_numeric_from_source`, `_numerical_grounding_check`, `_verify_claim` skip behaviour, and integration with `_check_one`

---

### ⬜ P4-D: Replace Breeze Connect with Angel One SmartAPI (Live Options)
**Priority:** Lowest — Trendlyne F&O already works well as primary options source.  
**What changes:**
- Remove `data/breeze_auth.py` (deprecated per CLAUDE.md P4-D note)
- Remove Breeze plumbing from `data/options_fetcher.py`
- Add `data/angel_one_fetcher.py` — Angel One SmartAPI client for live option chain
- New options source priority: **Angel One → Trendlyne F&O → NSE → VIX proxy**

**Why Angel One over Breeze:**
- Free with any Angel One demat account (no separate API portal fee)
- SmartAPI is well-documented (`pip install smartapi-python`)
- Real strike-level OI, IV, bid/ask — same quality as Breeze
- Supports automated daily session refresh via Client ID + Password + TOTP

**Angel One SmartAPI — what it provides:**
- `get_option_chain(symbol, expiry)` → full strike table with OI, volume, IV, bid/ask
- Real PCR, max pain, ATM IV, IV skew — all computable from live OI data
- Historical OHLCV as bonus (can supplement yfinance)

**New env vars needed (add to Railway worker + web):**
| Variable | Description |
|---|---|
| `ANGEL_ONE_API_KEY` | From smartapi.angelbroking.com → Apps |
| `ANGEL_ONE_CLIENT_ID` | Your Angel One login ID |
| `ANGEL_ONE_PASSWORD` | Your Angel One login password |
| `ANGEL_ONE_TOTP_SECRET` | Base32 TOTP secret (from 2FA setup) — enables automated daily token refresh |

**Current credential status:** Client ID + Password available. TOTP secret TBD (scan QR from Angel One 2FA settings).

**Files to change:**
- `data/angel_one_fetcher.py` — NEW: session manager + `get_option_chain()` + `_parse_chain()`
- `data/options_fetcher.py` — replace Breeze tier with Angel One tier; keep rest of fallback chain
- `data/breeze_auth.py` — DELETE
- `worker.py` — replace `job_breeze_token_refresh()` with `job_angel_one_token_refresh()` at 08:30 IST
- `requirements.txt` — swap `breeze-connect` → `smartapi-python`

**Effort:** M (3–6 hrs)

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
| **P2-A** | Data provider diversification (yfinance fallback) | Code | ₹0 | L | ✅ Done |
| **P2-B** | RAG corpus auto-refresh monthly job | Code | None (OpenAI existing) | M | ✅ Done |
| **P2-C** | Portfolio-level concentration alerts | Code | None | M | ✅ Done |
| **P2-D** | Earnings calendar auto-population | Code | None | M | ⬜ TODO |
| **P3-A** | Position sizing output in recs | Code | None | S | ✅ Done |
| **P3-B** | Correlation-aware portfolio alerts | Code | None | M | ✅ Done |
| **P3-C** | Comprehensive Trendlyne integration (6 pillars) | Code + Service | ₹492/mo (StratQ annual) | L–XL | ✅ Done |
| **P4-A** | Warren bot commentary grounding | Code | None | S | ✅ Done |
| **P4-B** | Symbol resolution cache persistence | Code | None | S | ✅ Done (already built) |
| **P4-C** | Governance numerical grounding check | Code | None | M | ✅ Done |
| **P4-D** | Replace Breeze with Angel One SmartAPI (live options) | Code | ₹0 (free with demat) | M | ⬜ TODO (lowest priority) |
| **P5-A** | Enhanced outcome tracker + attribution | Code | None | L | ⬜ TODO |
| **P5-B** | Paper portfolio simulation mode | Code | None | L | ⬜ TODO |
| **P5-C** | Rec outcome seeder (backfill open recs into recommendation_outcomes) | Code | None | S | ⬜ TODO |
| **P5-D** | Forward outcome poller — daily t+30/60/90 alpha vs NIFTY | Code | None | M | ⬜ TODO |
| **P5-E** | Attribution dashboard — per-agent hit rate + alpha over rolling 90d | Code | None | M | ⬜ TODO |
| **P6-A** | System performance dashboard tab | Code | None | M | ⬜ TODO |
| **P6-B** | Backtest results dashboard panel | Code | None | S | ⬜ TODO |
| **P6-C** | Market tab daily news digest (Morning Brief + Closing Digest, Claude+OpenAI) | Code | OpenAI API (existing) | L | ⬜ TODO |
| **Always** | CLAUDE.md + EXECUTION_PLAN.md update | Doc | None | XS | 🔄 Recurring |

*Effort: XS=<1hr · S=1-3hr · M=3-6hr · L=6-12hr · XL=12-24hr*

---

## TOTAL ESTIMATED NEW COSTS

| Service | Monthly Cost | Required For |
|---|---|---|
| Quantsapp Pro (options) | ₹2,499 (~$30) | P1-B — if no Upstox demat |
| Upstox API (options) | ₹0 | P1-B — if opening free demat |
| Trendlyne StratQ (annual) | ₹492/mo (~$6) | P3-C — DVM scores, fundamentals, filings, insider, analyst estimates |
| ~~Trendlyne GuruQ (monthly)~~ | ~~₹310/mo~~ | *(not recommended — limited downloads, no historical series)* |
| OpenAI API (GPT-4o-mini judges + embeddings) | ~₹40-80/mo | P1-C + RAG (key already in stack) |
| **Total new monthly (Breeze + Trendlyne + OpenAI)** | **₹532–3,071** | |
| **Total new monthly (Breeze + Trendlyne + Quantsapp + OpenAI)** | **₹3,031–3,071** | *(if Quantsapp instead of ICICI Breeze)* |

---

## RULE: End-of-Build Checklist (Every Session)

After every build session, before closing:

- [ ] CLAUDE.md updated — new files, tables, endpoints, env vars, resolved issues
- [ ] EXECUTION_PLAN.md updated — items marked ✅ with date, progress tracker refreshed
- [ ] `git commit` with descriptive message summarising the phase
- [ ] Deploy to Railway (auto via git push to main) and confirm health check passes

---

*Document version: 4.0 — 2026-05-17 (P5-C seeder + P6-D news intelligence plan + P6-D-7 GIFT Nifty signal layer)*  
*Next milestone: P5-D/E outcome attribution → P6-C morning brief → P6-D-7 GIFT Nifty → P6-D elite news engine*

---

## ⭐ P6-D: Elite News Intelligence Engine — Full Design

> **Objective:** Upgrade `agents/sentiment.py` and `agents/macro.py` from keyword-matching prototypes to an industry-grade, multi-layer news intelligence system. Target: beat passive NIFTY 50 alpha from news signal alone, measurably via P5-D outcome tracker.

### Current State Audit (2026-05-17)

| Layer | Current | Gap |
|---|---|---|
| **Data sources** | ET Markets, Moneycontrol, Google News RSS (5 feeds), NewsAPI | Missing: Hindi/regional news, BSE XML filings, earnings transcripts, options flow |
| **Sentiment NLP** | Claude Haiku (10-call cap) → keyword fallback dictionary | No semantic understanding, rate-capped, keyword misses nuanced language |
| **Macro news** | Pure keyword match (±10pt) — no LLM | Budget announcement ≡ routine inflation data to the model |
| **Deduplication** | MD5 fingerprint (same-text only) | Cross-domain same-story → counted 3× |
| **Temporal weighting** | None — 48h window, flat weight | 12h-old article = 2s-old article |
| **Entity resolution** | Symbol string match only | "Reliance" in headline about Reliance Jio ≠ RELIANCE.NS |
| **Backtesting** | None — sentiment signal never validated vs actual returns | Signal improvement cycle impossible |
| **India-specific** | Partially — some Hindi sources attempted but blocked | SEBI filings, BSE corporate announcements, Trendlyne events not used |

**Verdict: Functional but shallow.** The system is news-aware but not news-intelligent. It registers headlines exist; it cannot distinguish market-moving events from noise.

---

### Target Architecture (Research-Backed)

Based on: **Janus-Q** (Feb 2026, +17.5% direction accuracy via event-centric RL scoring), **Adaptive NIFTY Sentiment** (Dec 2025, instruction-tuned LLaMA for NSE), **LabelFusion** (Dec 2025, Claude+FinBERT 96% F1 ensemble), **MASFIN** multi-agent bias mitigation (Dec 2024), **CN-Buzz2Portfolio** (Mar 2026, news→sector rotation).

#### Layer 1 — Data Enrichment
```
Current: 5 RSS feeds + NewsAPI
Target:
  + BSE corporate announcements XML (free, real-time)    → event-level alerts
  + NSE circular feed (free)                             → regulatory signals  
  + Trendlyne BSE filings (already integrated, P3-C-P5)  → insider events
  + Screener.in concall notes (HTML scrape, authenticated)→ management guidance
  + Google News Hindi RSS (Navbharat Times, ABP Live)     → retail sentiment
  + Reddit r/IndiaInvestments RSS                         → retail sentiment
```

#### Layer 2 — Event Classification (Janus-Q pattern)
```
BEFORE sentiment scoring, classify each headline into:
  EARNINGS_SURPRISE     — beat/miss vs consensus
  REGULATORY_SHOCK      — SEBI order, RBI circular, ED/IT raid
  M&A_SIGNAL            — acquisition, merger, stake sale
  MACRO_CATALYST        — budget, rate decision, PMI, CPI
  ANALYST_ACTION        — upgrade/downgrade/target change
  MANAGEMENT_SIGNAL     — concall guidance, promoter buy/sell
  SECTOR_CATALYST       — PLI scheme, import duty, price hike
  ROUTINE               — earnings in-line, dividend, AGM date

Event class drives amplification multiplier (REGULATORY_SHOCK ×3, ROUTINE ×0.5)
```
**Implementation:** Claude Haiku batch classify (single prompt, all headlines → JSONL output) — replaces per-headline calls, removes 10-call cap bottleneck.

#### Layer 3 — Semantic Scoring (LabelFusion / FinBERT ensemble)
```
Signal = 0.6 × FinBERT_score + 0.4 × Claude_Haiku_score

FinBERT (ProsusAI/finbert, HuggingFace, open-source, no API cost):
  - 3-class: positive/negative/neutral + probability
  - Runs locally via transformers library or via HF Inference API (free tier)
  - Trained on Financial PhraseBank — 97% accuracy on financial text

Claude Haiku (existing):
  - Context-aware reasoning ("this headline is positive because Reliance 
    beat PAT by 15% vs analyst estimate of 8%")
  - Zero-shot generalisation for novel event types
```

#### Layer 4 — Temporal Decay
```
weight(article) = exp(-λ × age_hours)
  where λ = ln(2) / half_life_hours

half_life:
  EARNINGS_SURPRISE  → 6h  (impact absorbed fast)
  REGULATORY_SHOCK   → 48h (lingers, uncertain resolution)
  MACRO_CATALYST     → 12h (market digests during session)
  ROUTINE            → 2h  (stale immediately)

Replaces flat 48h window; recent news dominates composite score naturally.
```

#### Layer 5 — Entity-Centric Aggregation (NER)
```
Current: headline contains "RELIANCE" → attributed to RELIANCE.NS
Problem: "Reliance Jio tariff hike" ≠ RELIANCE.NS earnings news

Solution: spaCy NER (free, local) → extract ORG entities → 
  map via company_aliases dict (Reliance Jio → RELIANCE.NS, 
  Jio Platforms → RELIANCE.NS, Tata Sons → TATAMOTORS.NS + TATASTEEL.NS)
  
Also enables: sector-level aggregation (news hitting 3+ companies in 
  same sector → sector catalyst signal, not individual signal)
```

#### Layer 6 — Cross-Lingual Hindi Sentiment
```
Hindi Google News RSS → Google Translate API (free tier: 500K chars/month)
  or deep-translator library (free, no API key) → translated headline →
  into FinBERT + event classifier pipeline

Rationale: Hindi business news (Nav Bharat Times, Amar Ujala, Zee Business 
  Hindi) often breaks retail-sentiment-moving stories 2-4h before English 
  media picks them up. Retail investors in India primary consume Hindi media.
```

#### Layer 7 — Signal Validation Loop (closes the feedback cycle)
```
Daily at 18:30 IST (alongside outcome_tracker):
  1. For each recommendation_outcomes row resolved today (t+90 HIT/MISS)
  2. Fetch the sentiment.py signal recorded on rec_date  
  3. Compute correlation: sentiment_score vs actual alpha_t90
  4. Write to new table: sentiment_accuracy (date, symbol, sentiment_score,
     actual_alpha, correct_direction)
  5. If rolling-30d accuracy < 52% direction: auto-flag agent_performance 
     row as DEGRADING → surfaces in Governance tab

This creates the reinforcement loop from Adaptive NIFTY paper.
```

---

### Implementation Phases

| Sub-phase | Work | Effort | Cost |
|---|---|---|---|
| P6-D-1 | BSE/NSE XML feeds + Trendlyne filing events → Layer 1 | S | ₹0 |
| P6-D-2 | Event classifier (batch Haiku) replaces per-headline calls | M | ₹0 (existing Anthropic key) |
| P6-D-3 | Temporal decay implementation (sentiment.py) | S | ₹0 |
| P6-D-4 | FinBERT local integration (transformers pip package) | M | ₹0 (open-source) |
| P6-D-5 | spaCy NER entity-centric aggregation | M | ₹0 (open-source) |
| P6-D-6 | Hindi RSS + deep-translator pipeline | S | ₹0 (free tier) |
| P6-D-7 | GIFT Nifty pre-market signal layer (see below) | S | ₹0 (public data) |
| P6-D-8 | Signal validation loop (sentiment_accuracy table) | M | ₹0 |

**Total additional cost: ₹0** — FinBERT + spaCy + deep-translator are all open-source. The batch Haiku approach actually reduces Anthropic API cost vs current per-headline calls.

**New pip dependencies:**
```
transformers>=4.40.0      # FinBERT (HuggingFace)
torch>=2.1.0              # FinBERT inference (CPU ok for our batch size)
spacy>=3.7.0              # NER entity extraction
deep-translator>=1.11.0   # Hindi → English (no API key needed)
```

**New Supabase table:**
```sql
CREATE TABLE sentiment_accuracy (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  symbol      TEXT NOT NULL,
  rec_date    DATE NOT NULL,
  sentiment_score     NUMERIC,
  sentiment_signal    TEXT,
  event_class         TEXT,
  actual_alpha_t90    NUMERIC,
  correct_direction   BOOLEAN,
  created_at  TIMESTAMPTZ DEFAULT now()
);
```

### Research References
| Paper | Technique Borrowed |
|---|---|
| Janus-Q (Li et al., arXiv Feb 2026) | Event-centric classification; RL-based event importance scoring |
| Adaptive NIFTY Sentiment (Chaithra et al., Dec 2025) | India-native LLM fine-tuning; RL feedback loop closing |
| LabelFusion (Dec 2025, 96% F1) | Claude + FinBERT ensemble; hybrid scoring formula |
| MASFIN multi-agent bias mitigation (Dec 2024) | Cross-agent news fact verification; bias detection |
| CN-Buzz2Portfolio (Chen et al., Mar 2026) | News-to-sector-rotation signals |
| Transformer CoVaR (Chen et al., Feb 2026) | News + price + KG cross-modal fusion |

> **Expected improvement:** Direction accuracy of news-based signal: current ~52% (near random) → target 62-67% (matching Janus-Q benchmark) after P6-D-1 through P6-D-4. Measurable via P6-D-8 sentiment_accuracy table within 90 days of deployment.

---

### P6-D-7: GIFT Nifty Pre-Market Signal Layer

#### What is GIFT Nifty?
GIFT Nifty (formerly SGX Nifty, rebranded July 3 2023) is the **NIFTY 50 futures contract** traded on NSE IFSC (International Financial Services Centre) at GIFT City, Gujarat. It trades on an extended session: **Monday–Friday 6:30 AM – 11:30 PM IST**, giving ~3 hours of pre-market price discovery before the NSE cash market opens at 09:15 IST.

**Why it matters:**
- GIFT Nifty reflects **overnight US & Asian market sentiment** before domestic trading starts
- FII futures activity in GIFT city creates directional bias that propagates to NSE open
- Premium vs previous NIFTY 50 close = market consensus on gap-up / gap-down
- Historically: GIFT ±0.5% at 08:30 IST correctly predicts NSE open direction ~72% of sessions (vs 52% for random)
- Portfolio monitor can fire pre-open alerts if large negative signal detected (e.g., −1.5% = pause new entries)

#### Data Sources (in priority order)
| Source | URL / Method | Auth needed? | Notes |
|---|---|---|---|
| NSE IFSC official | `https://www.nseindia.com/api/GiftNifty` | None (public JSON) | Official, but NSE blocks server-side requests (bot detection) |
| Investing.com RSS / API | `https://api.investing.com/api/financialdata/assets/quotes?pairs=17425` | None / rate-limited | Reliable, but scraping risk |
| Moneycontrol scrape | `https://www.moneycontrol.com/mc/widget/sgxnifty/nifty-futures.html` | None | DOM scraping, fragile |
| Google Finance scrape | `https://www.google.com/finance/quote/NIFTY50:NSE` | None | Proxy via yfinance `^NSEI` for previous close |
| **yfinance `GC=F` proxy** | `yf.download("NIFTY50.NS", period="1d")` for prev close | None | **Primary approach**: fetch GIFT Nifty via NSE IFSC + compute delta vs yfinance prev close |

**Recommended implementation:** NSE IFSC JSON (attempt with rotation of User-Agent headers) → Moneycontrol HTML fallback → Investing.com fallback. Previous NIFTY 50 close always from yfinance `^NSEI`.

#### Signal Computation
```python
# data/gift_nifty_fetcher.py

def get_gift_nifty_signal() -> dict:
    """
    Returns pre-market GIFT Nifty directional signal.
    
    Output:
    {
        "gift_price": 24350.0,       # Current GIFT Nifty futures price
        "prev_nifty_close": 24180.0, # Previous NSE NIFTY 50 close
        "premium_pts": 170.0,        # gift_price - prev_close
        "premium_pct": 0.70,         # premium as % of prev close
        "signal": "POSITIVE_OPEN",   # POSITIVE_OPEN | NEGATIVE_OPEN | FLAT
        "signal_strength": "STRONG", # STRONG (>1%) | MODERATE (0.5–1%) | WEAK (<0.5%)
        "source": "nseindia",        # data source used
        "fetched_at": "2026-05-17T08:31:00+05:30",
        "market_note": "GIFT Nifty +0.70% → bullish gap-up expected at open"
    }
    """
```

**Signal thresholds:**
| Condition | Signal | Strength | Interpretation |
|---|---|---|---|
| premium_pct ≥ +1.0% | POSITIVE_OPEN | STRONG | Significant gap-up; FII/global buying overnight |
| premium_pct +0.5% to +1.0% | POSITIVE_OPEN | MODERATE | Mild positive bias |
| premium_pct -0.5% to +0.5% | FLAT | WEAK | No directional edge |
| premium_pct -0.5% to -1.0% | NEGATIVE_OPEN | MODERATE | Mild selling pressure expected |
| premium_pct ≤ -1.0% | NEGATIVE_OPEN | STRONG | Gap-down; consider pre-open alerts |

#### Integration Points

**1. `agents/macro.py` — pre-market enrichment**
```python
# In macro.analyse() — add GIFT Nifty as a sub-signal
from data.gift_nifty_fetcher import get_gift_nifty_signal

gift = get_gift_nifty_signal()
# Adjust macro score by ±3 pts based on GIFT signal (morning runs only)
if gift["signal"] == "POSITIVE_OPEN" and gift["signal_strength"] == "STRONG":
    score += 3
elif gift["signal"] == "NEGATIVE_OPEN" and gift["signal_strength"] == "STRONG":
    score -= 3

result["gift_nifty"] = gift  # exposed at top level of macro result
```

**2. `scheduler/portfolio_monitor.py` — pre-open alert**
```python
# In portfolio_monitor.py — fire WARNING alert when GIFT Nifty is strongly negative
gift = get_gift_nifty_signal()
if gift["signal"] == "NEGATIVE_OPEN" and gift["premium_pct"] <= -1.0:
    _create_alert(client, severity="WARNING", alert_type="GIFT_NIFTY_GAP_DOWN",
                  title=f"GIFT Nifty signals {gift['premium_pct']:.1f}% gap-down",
                  detail=gift["market_note"])
```

**3. `worker.py` — 08:30 IST fetch job**
```python
# New job: fetch GIFT Nifty at 08:30 IST (45 min before market open)
# Cache result in Supabase market_regime table or new gift_nifty_snapshots table
# Used by Morning Brief (P6-C) + portfolio monitor pre-open check
scheduler.add_job(job_gift_nifty_fetch, CronTrigger(hour=8, minute=30, timezone=IST))
```

**4. `api/main.py` — `/api/market/pulse` enrichment**
```python
# Add gift_nifty sub-key to existing market pulse response
# dashboard Markets tab can show "GIFT Nifty: +0.7% ▲" in the ticker bar
```

**5. `P6-C Morning Brief` — natural language context**
The Morning Brief LLM prompt will receive `gift_nifty` dict and include it in the opening line:
> *"GIFT Nifty is trading at +0.7% premium over yesterday's close, suggesting a positive gap-up open. US markets closed mixed overnight (S&P 500 +0.3%, Nasdaq -0.1%)..."*

**6. ARIA chat context**
```python
# In ARIA system prompt context builder
if gift_nifty and gift_nifty.get("signal") != "FLAT":
    context += f"\nGIFT Nifty pre-market: {gift_nifty['market_note']}"
```

#### New File: `data/gift_nifty_fetcher.py`
```
data/
└── gift_nifty_fetcher.py    # P6-D-7: GIFT Nifty pre-market signal
    # get_gift_nifty_signal() → {gift_price, prev_close, premium_pct, signal, signal_strength, source, fetched_at, market_note}
    # Sources: NSE IFSC JSON (primary) → Moneycontrol scrape → Investing.com fallback
    # Prev close: yfinance ^NSEI (always)
    # 30-min in-memory cache (relevant only during pre-market window)
    # Returns signal="FLAT" with note if outside GIFT trading hours or all sources fail
```

#### Fetch Schedule
| Time (IST) | Job | Purpose |
|---|---|---|
| 07:00 | `job_gift_nifty_fetch()` | Early signal for research agent + macro context |
| 08:30 | `job_gift_nifty_fetch()` | Morning Brief input + portfolio pre-open alert check |
| 09:00 | `job_gift_nifty_fetch()` | Final pre-open reading (15 min before NSE open) |

#### Supabase Storage (optional — for backtesting signal accuracy)
```sql
-- Lightweight append-only log (can be in existing market_regime table)
ALTER TABLE market_regime ADD COLUMN IF NOT EXISTS gift_nifty_premium_pct NUMERIC;
ALTER TABLE market_regime ADD COLUMN IF NOT EXISTS gift_nifty_signal TEXT;
-- Or as separate lightweight table:
CREATE TABLE gift_nifty_snapshots (
  snapshot_at   TIMESTAMPTZ PRIMARY KEY,
  gift_price    NUMERIC,
  prev_close    NUMERIC,
  premium_pct   NUMERIC,
  signal        TEXT,
  source        TEXT
);
```

#### Accuracy Expectation
| Horizon | Expected accuracy | Basis |
|---|---|---|
| Predicts open direction (first 15 min) | ~72% | Historical SGX→NSE correlation studies |
| Predicts intraday direction (full session) | ~58% | Decays as domestic factors dominate |
| Useful for portfolio pre-open alerts (≥1% gap) | High precision | Large moves usually sustained at open |

> **Implementation effort:** Small (S) — 1 new file (`data/gift_nifty_fetcher.py`), 4 minor integration edits (macro.py, portfolio_monitor.py, worker.py, api/main.py). All data is public — zero additional cost.

---
