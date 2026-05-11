# Bharat Intelligence — Claude Project Brief

> This file is read automatically by Claude Code at the start of every session.
> It is the canonical reference for codebase structure, architecture, conventions,
> and deployment. Update it whenever significant changes are made.

---

## What this project is

A multi-agent Indian stock/commodity market intelligence platform.
- **10 AI agents** analyse fundamentals, technicals, sentiment, macro, institutional flows, sector PE, commodities, historical patterns, long-term quality (warren_bot), and proactively discover new opportunities.
- **Governance layer** audits agent accuracy, detects hallucinations, scans AI research papers, and proposes improvements via GitHub PRs.
- **Scheduler** (`worker.py`) runs everything daily via APScheduler — two Railway services: web (uvicorn) + worker (python worker.py).
- **FastAPI backend** (`api/main.py`) serves live data to a React dashboard.
- **React dashboard** (`dashboard/src/App.jsx`) — single-file SPA with ARIA AI chat, portfolio tracker, discovery engine, governance tab.

---

## Repo layout

```
Stock analysis/
├── agents/                     # 10 analysis agents (all extend a common pattern)
│   ├── technical.py            # TA indicators via yfinance
│   ├── fundamental.py          # Valuation, ratios, screeners
│   ├── sentiment.py            # News + social sentiment NLP
│   ├── macro.py                # RBI, inflation, currency, global macro
│   ├── institutional.py        # FII/DII flow analysis
│   ├── sector_valuation.py     # Live sector PE regime vs 5-yr average
│   ├── commodities.py          # Gold, crude, silver MCX
│   ├── historical_rag.py       # pgvector semantic similarity on past events
│   ├── discovery_screener.py   # Proactive stock discovery — full NSE EQ universe
│   │                           # daily slice rotation (200/day → 9-day full cycle)
│   └── warren_bot.py           # Long-term business quality (Buffett+Jhunjhunwala)
│
├── governance/                 # Agent oversight & self-improvement
│   ├── fact_checker.py         # Cross-agent claim verification
│   ├── hallucination_detector.py
│   ├── performance_tracker.py  # Accuracy/hallucination rate logging
│   ├── research_agent.py       # Daily AI paper scanner (arXiv, SS, HuggingFace)
│   └── github_manager.py       # Opens GitHub PRs for approved research proposals
│
├── scheduler/                  # APScheduler daily pipeline
│   ├── orchestrator.py         # Master LangGraph pipeline — all agents + governance
│   │                           # Pipeline: sector_pe_snapshot → load_symbols → load_weights
│   │                           # → run_agents → synthesise → fact_check → save_recs
│   │                           # → monitor → log_run → run_discovery → END
│   ├── portfolio_monitor.py    # Monitors open holdings, fires portfolio_alerts
│   ├── sector_pe_tracker.py    # Daily sector_pe_snapshots writes
│   └── performance_tracker.py  # Writes agent_performance rows daily
│
├── worker.py                   # Unified background worker (runs on Railway worker dyno)
│   #  Schedule (IST):
│   #    06:00 — orchestrator (all agents + discovery)
│   #    07:00 — performance tracker
│   #    07:30 — research agent
│   #    08:00 — earnings calendar refresh
│   #    08:30 — Breeze token refresh (P1-B)
│   #    09:15, 11:30, 13:30, 15:15 — portfolio monitor
│   #    15:45 — options snapshot (uses Breeze if configured)
│   #    07:45 (1st of month) — historical backtest (agents/backtester.py)
│
├── data/
│   ├── fetchers.py             # India market data fetchers (NSE, BSE, RBI, SEBI)
│   │                           # + get_screener_history() — 10yr annual time series
│   ├── symbol_map.py           # NSE → yfinance symbol resolution (YF_SYMBOL_MAP)
│   │                           # Single source of truth for all agents.
│   │                           # Also has SCREENER_SLUG_MAP for screener.in slugs.
│   ├── options_fetcher.py      # Option chain: Breeze → NSE → VIX fallback (P1-B)
│   │                           # get_option_metrics(symbol) → pcr, max_pain, atm_iv, iv_skew
│   └── breeze_auth.py          # ICICI Breeze Connect session manager (P1-B)
│   #                             get_breeze_client() with 23h cache + auto/manual refresh
│
├── api/
│   ├── __init__.py
│   └── main.py                 # FastAPI backend (11 endpoints + WebSocket)
│   #                             _NSE_OVERRIDES: brand-name → yfinance ticker aliases
│   #                             _symbol_cache: process-lifetime resolution cache
│
├── dashboard/
│   ├── src/App.jsx             # Entire React SPA (~1900 lines, single file)
│   ├── src/index.js
│   ├── api/
│   │   ├── aria.js             # Vercel serverless fn — proxies to Anthropic API
│   │   └── research.js         # Vercel serverless fn — proxies to Supabase research_proposals
│   ├── vercel.json             # Vercel build config (root dir must be set to dashboard/ in Vercel UI)
│   └── package.json            # React 18, CRA
│
├── db/
│   ├── schema.sql              # Full Supabase schema (run once to create all tables)
│   ├── backfill_embeddings.py  # One-time: generate OpenAI embeddings for historical_events rows
│   │                           # python -m db.backfill_embeddings [--run] [--batch N] [--limit N]
│   │                           # All 150/150 rows now have embeddings (run 2026-05-12)
│   └── migrations/
│       ├── grant_service_role_rls.sql          # RLS policies for service_role
│       ├── create_research_proposals.sql
│       ├── enhancement_proposals.sql
│       ├── fix_rls_permissions.sql
│       ├── sector_pe_snapshots.sql
│       ├── create_warren_bot_cache.sql         # warren_bot 24-hr result cache
│       ├── create_discovery_runs.sql           # daily screened-symbol log
│       ├── create_earnings_calendar.sql        # earnings dates for earnings_guard
│       ├── create_portfolio_risk_snapshots.sql # portfolio risk snapshot table
│       └── create_backtest_results.sql         # ← NEW: walk-forward backtest results (P1-A)
│
├── tests/                      # pytest — one test file per module
├── requirements.txt
├── Procfile                    # web: uvicorn ...  worker: python worker.py
├── railway.toml                # Railway deployment config
└── vercel.json                 # Root placeholder (actual config in dashboard/vercel.json)
```

---

## Supabase database schema

| Table | Purpose | Key columns |
|---|---|---|
| `recommendations` | Agent-generated buy/sell recs | `symbol, action, confidence, risk_score, entry_low, entry_high, target, stoploss, upside_pct, upside_confidence, is_discovery, agent_signals (jsonb), gov_check (jsonb), metadata (jsonb)` |
| `portfolio_holdings` | User's open positions | `symbol, yf_symbol, name, sector, qty, avg_buy, current_price, target_price, stoploss_price, status (OPEN/CLOSED/PARTIAL), danger_drop_pct, danger_confidence, danger_trigger, linked_rec_id` |
| `portfolio_alerts` | Risk/danger alerts | `severity (INFO/WARNING/DANGER/CRITICAL), alert_type, title, detail, resolved, portfolio_id` |
| `agent_performance` | Daily agent accuracy log | `agent_name, accuracy_90d, hallucination_rate, trend (IMPROVING/STABLE/DEGRADING), audit_date` |
| `historical_events` | RAG knowledge base | `event_type, description, embedding (vector), outcome, relevance_score` |
| `institutional_flows` | FII/DII daily data | `fii_net, dii_net, fii_buy, fii_sell, session_date` |
| `daily_runs` | Scheduler run log | `run_date, status, agents_run (jsonb — includes discovery coverage stats), errors` |
| `research_proposals` | AI paper proposals | `title, source, url, relevance, status, proposed_change, impacted_agents, debate_log (jsonb), pr_url, metadata (jsonb)` |
| `sector_pe_snapshots` | Daily sector PE | `sector, pe_ratio, avg_5yr_pe, regime, snapshot_date` |
| `enhancement_proposals` | User-requested enhancements | `title, description, cost_usd, status, is_paid` |
| `warren_bot_cache` | 24-hr on-demand cache | `symbol (PK), result (jsonb), cached_at` |
| `discovery_runs` | Daily screened-symbol log | `run_date (unique), slice_symbols, passed_symbols, discovery_symbols, coverage_stats, total_screened, total_passed, total_discoveries` |
| `recommendation_outcomes` | Forward outcome tracker | `rec_id, symbol, action, entry_price, rec_date, price_t90/t180/t365, nifty_t90/t180/t365, alpha_t90/t180/t365, outcome_t90/t180/t365, nifty_entry, composite_score, validation_kappa` |
| `market_regime` | Daily market regime | `regime_date (unique), regime, confidence, nifty_trend, vix_state, fii_trend, breadth_state, momentum_state, raw_signals (jsonb)` |
| `earnings_calendar` | Earnings dates for pre-earnings guard | `symbol, earnings_date, quarter, source, confirmed` |
| `backtest_results` | Walk-forward backtest runs | `run_date, universe, period_start/end, split_type (TRAIN/TEST/FULL), hit_rate_90d, avg_alpha_90d/180d, sharpe_ratio, max_drawdown, win_loss_ratio, signal_details (jsonb)` |

> **All migrations applied ✅** (warren_bot_cache, sector_pe_snapshots, discovery_runs, symbol_resolutions, add_yf_symbol_danger_sources, enhancement_proposals, recommendation_outcomes, market_regime)
>
> **New pending migration (run in Supabase SQL Editor):**
> - `db/migrations/create_earnings_calendar.sql` — required for earnings_guard primary lookup
>
> **Pending data seed:**
> - `python -m db.seed_historical_events_comprehensive --append` — loads 57 new historical events (Gap 11)

---

## API endpoints (`api/main.py`)

Base URL (Railway): `https://bharat-intelligence-two-production.up.railway.app` *(confirm current URL)*

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Health check — no auth needed. Returns `{"status":"ok","db":true}` |
| GET | `/api/recommendations` | Latest recs sorted upside_pct desc, critical first |
| GET | `/api/discovery` | `is_discovery=true` recs from today (7-day fallback, expired filtered via valid_till). Live price refresh on every call. |
| GET | `/api/discovery/runs` | Last N days of screener run logs (slice/passed/discovery symbols + coverage stats). Powers dashboard "Daily Screened Stocks" panel. |
| GET | `/api/portfolio` | Open holdings, refreshes current_price from yfinance |
| POST | `/api/portfolio` | Add/update holding — auto-resolves yfinance symbol, fetches live price |
| GET | `/api/portfolio/alerts` | Unresolved portfolio alerts |
| GET | `/api/symbol/resolve?q=RELIANCE` | Resolves any input to yfinance ticker + live price |
| GET | `/api/governance/alerts` | Aggregated from portfolio_alerts + degrading agent_performance |
| GET | `/api/governance/research` | Research proposals with debate status computed |
| GET | `/api/market/pulse` | Live yfinance prices (NIFTY, SENSEX, GOLD, CRUDE, VIX, FII) — 60s cache |
| GET | `/api/warren_bot/{symbol}` | On-demand Buffett/Jhunjhunwala quality score — 24h Supabase cache |
| GET | `/api/backtest/summary` | Walk-forward backtest summary from `backtest_results` — `?split=TEST\|TRAIN\|FULL&limit=5` |
| WS | `/ws/alerts` | WebSocket — broadcasts DANGER/CRITICAL alerts every 30s |

**Auth:** `x-api-key: <DASHBOARD_API_KEY>` header on all HTTP. `?api_key=<key>` on WebSocket.
**Open in local dev** when `DASHBOARD_API_KEY` env var is unset.

### Symbol auto-resolution order (`_resolve_yf_symbol`)
1. `_NSE_OVERRIDES` dict in `api/main.py` — indices, ETFs, brand-name aliases (IHCL→INDHOTEL.NS, BHARATSEAT→BHARATSE.NS, etc.)
2. Already has suffix (.NS/.BO/=X/=F) or starts with ^
3. Live probe SYMBOL.NS via yfinance 1-day history
4. Live probe SYMBOL.BO
5. Default: SYMBOL.NS

Results cached in `_symbol_cache` dict for process lifetime.

**Canonical symbol map:** `data/symbol_map.py` → `YF_SYMBOL_MAP` is the source of truth used by all agents. `_NSE_OVERRIDES` in `api/main.py` must mirror the same aliases for the portfolio API layer.

**Known brand→ticker aliases (must exist in both maps):**

| User input | yfinance ticker | Note |
|---|---|---|
| `IHCL` | `INDHOTEL.NS` | Indian Hotels Co. (IHCL brand, NSE = INDHOTEL) |
| `BHARATSEAT` | `BHARATSE.NS` | Bharat Seats Ltd (NSE drops last 3 chars) |
| `HITACHIENERGYINDIA` | `POWERINDIA.NS` | Hitachi Energy India (NSE legacy = POWERINDIA) |
| `ZOMATO` | `ETERNAL.NS` | Zomato rebranded → Eternal (2025) |
| `MUTHOOT` | `MUTHOOTFIN.NS` | Short alias |
| `L&T` / `LNT` | `LT.NS` | Larsen & Toubro |
| `SHAKTIPUMPS` | `SHAKTIPUMP.NS` | NSE ticker drops trailing S |
| `GEVERNOVA` / `GE VERNOVA` / `GETDINDIA` | `522275.BO` | GE Vernova T&D India — BSE only in YF |
| `ELFORGE` | `ELFORGE.BO` | El Forge Ltd — BSE-listed only |

---

## Discovery screener (`agents/discovery_screener.py`)

### Universe & rotation
- **Extended universe:** `fetch_all_nse_equity_symbols()` downloads NSE `EQUITY_L.csv` (~1 700 EQ-series tickers). Falls back to NIFTY 500 on failure.
- **Daily slice rotation:** `_daily_slice(universe, slice_size=200, run_date)` — stable shuffle (seed `0x6272617274`) + date-window. Every symbol visited once per ~9-day cycle (~3× monthly).
- **Coverage stats:** `_coverage_stats()` — returns `cycle_length_days`, `today_position`, `cycle_pct_complete`, `est_full_coverage`, `monthly_passes`.

### Pipeline
1. Load full NSE EQ universe → exclude portfolio holdings → take today's 200-symbol slice
2. Pre-screen **all 200** (no early break) — fast filters: RSI 40–65, PE<50 or revGrowth>30%, FII buying, revGrowth>15%, price>EMA200
3. Run full 7-agent analysis on up to 25 symbols that passed pre-screen
4. Classify CRITICAL (upside≥100%, conf≥70%) or STANDARD (upside≥20%, conf≥65%)
5. Save to `recommendations` (is_discovery=True) with metadata.price snapshot
6. Upsert to `discovery_runs` with full symbol lists for dashboard audit trail

### CLI
```powershell
python -m agents.discovery_screener                          # default: 200 slice, 25 deep
python -m agents.discovery_screener --max-prescreen 300 --max 40
python -m agents.discovery_screener --nifty500               # restrict to NIFTY 500
python -m agents.discovery_screener --no-save                # dry run
python -m agents.discovery_screener --coverage-only          # print stats and exit
```

### Orchestrator integration
`run_discovery_node` is the **final step** in the LangGraph pipeline (after `log_run`). Fires automatically at 06:00 IST daily via `worker.py`.

---

## React dashboard (`dashboard/src/App.jsx`)

**Single file ~1900 lines.** Key sections:

| Lines (approx) | Section |
|---|---|
| 1–150 | Constants: mock data — used as offline fallbacks only when `API_URL` is unset |
| 150–200 | API config: `IS_LIVE`, `API_URL`, `API_KEY`, `apiFetch()` helper |
| 200–430 | Small UI components: MarketTicker, AlertBanner, EmptyState, etc. |
| 430–920 | ResearchDiscoveryTab + DiscoveryRunsPanel (new) |
| 920–1060 | PortfolioTab component |
| 1060–1230 | GovernanceResearchTab component |
| 1230–1350 | Charts and sub-components |
| 1350–1520 | ARIAPanel component (AI chat) |
| 1520–1900 | App() root component — state, useEffect, routing |

**IS_LIVE pattern:**
```javascript
const IS_LIVE = Boolean(API_URL);
// When IS_LIVE: states init empty [], live data fills them after mount
// When not IS_LIVE: states init from mock constants (local dev / no backend)
const [discoveryUniverse, setDiscoveryUniverse] = useState(IS_LIVE ? [] : DISCOVERY_UNIVERSE);
```

**apiLoaded flag:**
```javascript
const [apiLoaded, setApiLoaded] = useState(!IS_LIVE);
// Set to true after Promise.allSettled() completes — distinguishes loading vs loaded+empty
```

**Mock data removed:** All mock constants (`LIVE_PRICES`, `NEWS_FEED`, `AGENT_DEBATE_LOG`, `ENHANCEMENT_PROPOSALS`, `AGENT_PERF`) deleted. Components show `EmptyState` when data is absent.

**Discovery tab — Daily Screened Stocks panel (`DiscoveryRunsPanel`):**
- Collapsible panel at bottom of Discovery tab
- Fetches `GET /api/discovery/runs`
- Shows per-day accordion: total screened / passed / promoted
- Expanded day: symbol pills colour-coded (⚡ promoted, ✓ passed, dim = screened only)
- Coverage stats mini-bar: universe size, cycle day, monthly passes

**ARIA portfolio action flow:**
1. User says: *"I bought Reliance 15 shares at 2850"*
2. ARIA outputs `<portfolio_action>{"action":"add","symbol":"RELIANCE","qty":15,"avgBuy":2850}</portfolio_action>` at end of response
3. `handlePortfolioUpdate()` in App() parses it → calls `POST /api/portfolio`
4. Backend auto-resolves RELIANCE→RELIANCE.NS, fetches live price, saves to Supabase

**ARIA sell / partial sell:**
- Full exit: `{"action":"exit","symbol":"VOLTAS","exitPrice":1650,"qty":140,"notes":"..."}`
  → marks holding as "exited", POSTs `status: "CLOSED"` to backend
- Partial sell: `{"action":"exit","symbol":"VOLTAS","exitPrice":1650,"qty":125,"notes":"..."}`
  → reduces holding qty from 140 → 15, keeps `status: "holding"`, POSTs `{qty: 15, notes: ...}` (no status change)
- Backend UPDATE path only updates fields explicitly in payload (no field-clobber)

**ARIA endpoint:** `POST /api/aria` → Vercel serverless function (`dashboard/api/aria.js`) → Anthropic Messages API.
Uses `ANTHROPIC_API_KEY` env var server-side (never exposed to browser).

---

## Environment variables

### Railway (backend — two services: web + worker)
| Variable | Description |
|---|---|
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | service_role key (bypasses RLS) |
| `DASHBOARD_API_KEY` | Secret shared with Vercel frontend |
| `VERCEL_DASHBOARD_URL` | Exact Vercel URL for CORS (e.g. `https://bharat-intelligence-two.vercel.app`) |
| `BREEZE_API_KEY` | ICICI Breeze Connect API key (from ICICI Direct API portal) — P1-B |
| `BREEZE_API_SECRET` | ICICI Breeze Connect API secret — P1-B |
| `BREEZE_SESSION_TOKEN` | Daily session token (get from login redirect URL, rotate every 24h) — P1-B |
| `ICICI_USER_ID` | *(Optional)* ICICI Direct login ID — enables auto-token refresh at 08:30 IST |
| `ICICI_PASSWORD` | *(Optional)* ICICI Direct password — enables auto-token refresh |
| `BREEZE_TOTP_SECRET` | *(Optional)* Base32 TOTP secret — enables fully automated daily refresh |

### Vercel (frontend)
| Variable | Description |
|---|---|
| `REACT_APP_API_URL` | Railway backend URL (no trailing slash) |
| `REACT_APP_API_KEY` | Must match `DASHBOARD_API_KEY` on Railway |
| `ANTHROPIC_API_KEY` | For `dashboard/api/aria.js` serverless function |
| `SUPABASE_URL` | For `dashboard/api/research.js` serverless function |
| `SUPABASE_SERVICE_KEY` | For `dashboard/api/research.js` serverless function |

### Local dev (`.env` at project root)
Same as Railway vars above. `.env` is gitignored.

---

## Deployment

### Railway — two services
| Service | Start command | Health check |
|---|---|---|
| web | `uvicorn api.main:app --host 0.0.0.0 --port $PORT` | `GET /health` |
| worker | `python worker.py` | none |

- Both auto-deploy on push to `main`
- `railway.toml` sets `restartPolicyType = "on_failure"` — worker restarts on crash
- `Procfile` defines both roles; per-service start commands set in Railway dashboard

### Vercel (React frontend)
- Auto-deploys on push to `main`
- **Root Directory must be set to `dashboard/`** in Vercel project Settings → General
- Build config in `dashboard/vercel.json` (framework: create-react-app)
- Serverless functions auto-discovered from `dashboard/api/`
- `REACT_APP_*` vars baked at build time — must redeploy after changing them

### Run locally
```powershell
# Backend
pip install -r requirements.txt
uvicorn api.main:app --reload --port 8000

# Worker (optional — runs scheduled jobs)
python worker.py --now   # fire all jobs once immediately

# Frontend
cd dashboard
npm install
npm start          # CRA dev server on port 3000
```

---

## Key design decisions & conventions

- **Snake_case in DB, camelCase in React.** Transformers in `api/main.py` handle the conversion: `_transform_holding()`, `_transform_recommendation()`, `_transform_research()`.
- **IS_LIVE / mock data as fallback.** `IS_LIVE = Boolean(API_URL)`. When live, states init empty and fill from API. When no backend (local dev), mock constants are used. No mock data shown in production.
- **yf_symbol stored separately.** `portfolio_holdings` has both `symbol` (display, e.g. `RELIANCE`) and `yf_symbol` (e.g. `RELIANCE.NS`). GET /api/portfolio uses `yf_symbol` to refresh prices.
- **60s market cache.** `_market_cache` + `_market_cache_ts` globals in `api/main.py` prevent hammering yfinance on every dashboard render.
- **Discovery price refresh.** `GET /api/discovery` refreshes live prices on every call (same pattern as GET /api/portfolio). `metadata.price` in recommendations is the snapshot at write-time; overwritten on each API response.
- **Discovery valid_till filter.** 7-day fallback query uses `.gte("valid_till", today)` to exclude expired recs.
- **Governance alerts have no dedicated table.** Aggregated on the fly from `portfolio_alerts` (CRITICAL/DANGER severity) + `agent_performance` (DEGRADING trend).
- **debateStatus computed, not stored.** `research_proposals` only has `status`. `debateStatus` (pending/debating/approved) is computed from `debate_log` vote counts in `_transform_research()`.
- **Service_role BYPASSRLS.** Supabase service_role has built-in RLS bypass but still needs `GRANT ALL` for table privileges. Both are set in `db/migrations/grant_service_role_rls.sql`.
- **Symbol resolution two-layer.** `data/symbol_map.py::YF_SYMBOL_MAP` is used by all agents. `api/main.py::_NSE_OVERRIDES` covers the API layer. Both must be updated together when adding new aliases.

---

## Common tasks

**Add a new API endpoint:**
Edit `api/main.py` → add `@app.get("/api/...")` function → add corresponding `apiFetch()` call in `dashboard/src/App.jsx` useEffect.

**Add a new agent:**
1. Create `agents/new_agent.py` following the same pattern as `agents/technical.py`
2. Register it in `scheduler/orchestrator.py`
3. Add test in `tests/test_new_agent.py`

**Add a brand-name alias (symbol doesn't resolve to correct price):**
Add to BOTH `data/symbol_map.py::YF_SYMBOL_MAP` AND `api/main.py::_NSE_OVERRIDES`.
Also clear `_symbol_cache` on the running API pod (or redeploy) to pick up the change.

**Run all tests:**
```powershell
python -m pytest tests/ -q --tb=short
# Skip known-flaky network tests:
python -m pytest tests/ -q --tb=short --ignore=tests/test_research_agent.py --ignore=tests/test_fetchers_integration.py
```

**Run integration tests only:**
```powershell
python -m pytest -m integration -v -s
```

**Apply a new DB migration:**
Run the SQL file in Supabase dashboard → SQL Editor.

**Check GitHub integration:**
```powershell
python -c "from governance.github_manager import GitHubManager; gm=GitHubManager(); print(gm.list_branches())"
```

**Smoke-test the worker (fires all jobs once):**
```powershell
python worker.py --now
```

---

## Warren bot — `agents/warren_bot.py`

Entry point: `analyse(symbol: str) -> dict` — never raises, always returns a result dict.
API endpoint: `GET /api/warren_bot/{symbol}` — 24-hr Supabase cache (`warren_bot_cache` table).

### Scoring dimensions (20 pts each, total 100 before bonuses)
| Dimension | Key inputs |
|---|---|
| Moat Strength | ROCE consistency, OPM%, revenue CAGR, promoter holding trend |
| ROCE Quality | 10-yr ROCE avg, consistency score, recent acceleration |
| Management Quality | Pledging %, promoter holding trend, dividend payout, capex efficiency |
| Earnings Consistency | EPS CAGR (5yr/10yr), consecutive growth years, recent PAT direction |
| DCF Valuation | Owner earnings DCF, 3-stage (5yr growth + 5yr fade + terminal), 12% discount rate, MOS% |

### Jhunjhunwala India Lens (bonus pts, cap 100 total)
- India consumption play (FMCG/Consumer/Retail/Finance/Pharma): +4 pts
- Early penetration (<30% national, large addressable market): +3 pts
- Cyclical trough (P/E < 10): +4 pts

### Hard disqualifiers (any one → signal = AVOID, score capped at 30)
- <5 years of screener data
- Market cap < ₹500 Cr
- Promoter pledging > 40%
- Loss-making in 3+ of last 5 years

### Data fetching
- `get_screener_data(symbol)` → snapshot ratios, P/E, P/B, market cap, sector
- `get_screener_history(symbol)` → 10-yr annual: revenue, OPM%, PAT, EPS, depreciation, capex, ROCE, ROE, dividend payout, promoter holding
- `get_ohlcv(symbol)` → current price (yfinance)

### Output keys (28 total)
`symbol`, `signal`, `score`, `moat_score`, `roce_score`, `mgmt_score`, `earnings_score`, `dcf_score`,
`moat_type`, `roce_avg`, `eps_cagr_5yr`, `eps_cagr_10yr`, `revenue_cagr`,
`intrinsic_value`, `current_price`, `margin_of_safety_pct`, `owner_earnings`,
`jhunjhunwala_bonus_pts`, `is_consumption_play`, `is_early_penetration`, `is_cyclical_trough`,
`disqualifiers`, `commentary`, `data_quality`, `years_available`,
`agent_name` ("warren_bot"), `analysed_at`, `error`

> **Known issue (RESOLVED):** warren_bot `_log_to_supabase()` only inserts `agent_name` + `audit_date` — no `notes` column issue exists in current code. ✅

---

## git history (recent)

| Commit | Change |
|---|---|
| `51fa452` | Fix partial sell: ARIA qty field, partial vs full exit logic, backend field-clobber fix |
| `77d5775` | Fix log format string in backfill_embeddings (UUID id, not int) |
| `4472416` | Fix FII stale zeros, add India macro news monitoring, add embedding backfill script |
| `897ea26` | Fix yfinance 1.2.0 breaking changes + discovery screener pre-screen bugs |
| `a7ec99a` | Fix all price refresh failures + ARIA sell action |
| `d6fc799` | Fix symbol resolution for Shakti Pumps, GE Vernova, El Forge |
| `c5b2c4a` | Fix Vercel build + ARIA portfolio update bugs |
| `3f6d68d` | Fix Anthropic judge lazy-init: self-heal when ant_client=None |
| `293d517` | P1-C: Replace Claude Haiku judge with GPT-4o-mini for model diversity |
| Phase 0 | Sector WACC, macro sensitivity, owner earnings capex fix, discovery quality gate + FII filter fix, fallback thresholds |
| `5cb2b76` | Fix portfolio price failures: IHCL→INDHOTEL.NS, BHARATSEAT→BHARATSE.NS, HITACHIENERGYINDIA→POWERINDIA.NS + proactive aliases |

---

## Known Issues (tracked)

| Issue | Severity | File | Status |
|---|---|---|---|
| `warren_bot._log_to_supabase()` notes column issue | LOW | `agents/warren_bot.py` | ✅ Already correct — no issue |
| Options signal is India VIX proxy, not real option chain (NSE blocks server-side) | HIGH | `data/options_fetcher.py` | ✅ Fixed (P1-B) — Breeze Connect as primary source |
| WACC hardcoded 12% for all stocks | HIGH | `agents/valuation_scenarios.py`, `agents/warren_bot.py` | ✅ Fixed (P0-A) — sector WACC table added |
| Macro score identical for all stocks in same pipeline run | HIGH | `scheduler/orchestrator.py` | ✅ Fixed (P0-B) — `get_sector_adjusted_macro_score()` wired |
| DCF owner earnings uses full capex (not 0.6× maintenance) in `valuation_scenarios.py` | MEDIUM | `agents/valuation_scenarios.py` | ✅ Fixed (P0-D) — `0.6 * capex` |
| Discovery CRITICAL threshold produces false positives from data artefacts | MEDIUM | `agents/discovery_screener.py` | ✅ Fixed (P0-E) — data quality gate + threshold changed to 40%/75% |
| FII filter in discovery pre-screen is index-level, not stock-specific | MEDIUM | `agents/discovery_screener.py` | ✅ Fixed (P0-F) — now uses `institutional_holding_pct ≥ 5%` |
| All 3 synthesis validation judges use Claude variants — correlated sampling | MEDIUM | `scheduler/synthesis_validator.py` | ✅ Fixed (P1-C) — GPT-4o-mini as 3rd judge + Anthropic lazy-init |
| `earnings_calendar` table not yet created | MEDIUM | `agents/earnings_guard.py` | ✅ Migration run + 150 events seeded |
| `fallback_synthesis` thresholds (≥72=BUY) uncalibrated | LOW | `scheduler/orchestrator.py` | ✅ Fixed (P1-D) — now ≥75/58/30 |
| Single data provider (screener.in) — no fallback if blocked | HIGH | `data/fetchers.py` | 🔲 P2-A |
| portfolio_monitor HTTP 400 on ALL recommendations queries (danger_trigger/window not in table) | CRITICAL | `scheduler/portfolio_monitor.py` | ✅ Fixed (Step 9) — removed non-existent columns from SELECT |
| `/api/portfolio/risk` returns HTTP 500 — NaN floats not JSON-serialisable | HIGH | `api/main.py` | ✅ Fixed (Step 9) — `_sanitise_floats()` wrapper added |
| `portfolio_risk_snapshots` table missing (PGRST205) | HIGH | `agents/portfolio_risk.py` | ✅ Migration created — run `db/migrations/create_portfolio_risk_snapshots.sql` |
| portfolio_risk uses wrong yf_symbol for IHCL/HITACHIENERGYINDIA/BHARATSEAT | MEDIUM | `agents/portfolio_risk.py` | ✅ Fixed (Step 9) — `_resolve_yf_symbol()` added to `_load_holdings()` |
| `institutional_flows` table stale since April 22 (fii_net=0.0 — NSE API blocked) | HIGH | `agents/institutional.py` | ✅ Fixed (BF-3) — zero rows filtered, NO_DATA returned correctly |
| Discovery screener returning 0 passes (yfinance NaN + wrong field names + threshold 4/5) | CRITICAL | `agents/discovery_screener.py` | ✅ Fixed (BF-2) — .dropna(), fii_holding_pct, threshold=3 |
| All portfolio prices stuck at upload price (yfinance 1.2.0 progress=False removed) | CRITICAL | `api/main.py`, `data/options_fetcher.py`, `agents/backtester.py` | ✅ Fixed (BF-1) — removed progress=False, added .dropna() |
| Macro agent blind to major announcements (PM Modi, budget, geopolitical) | HIGH | `agents/macro.py` | ✅ Fixed (BF-4) — Google News RSS macro monitoring added |
| 98 of 150 historical_events rows missing OpenAI embeddings | MEDIUM | `db/` | ✅ Fixed (BF-5) — all 150/150 now have embeddings |
| ARIA partial sell removes entire position instead of reducing qty | HIGH | `dashboard/src/App.jsx`, `api/main.py` | ✅ Fixed (BF-6) — partial sell support + backend field-clobber fix |
| Telegram not configured — STOPLOSS_HIT / CRITICAL alerts not delivered | HIGH | `scheduler/portfolio_monitor.py` | 🔲 Set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID env vars on Railway |
| `recommendation_outcomes` table empty — no forward tracking | MEDIUM | `agents/outcome_tracker.py` | 🔲 Needs seeding from historical recs |
| ICICI Breeze primary IP update due ~May 18 | MEDIUM | Railway env | 🔲 Update primary IP to `52.5.155.132` on ICICI Direct portal |

---

## Phase 0 — What changed (affects every production run from here)

### `agents/valuation_scenarios.py`
- **P0-D**: Owner earnings now uses `PAT + Dep - 0.6 × Capex` (was full capex). Aligns with warren_bot methodology.
- **P0-A**: `_SECTOR_WACC` dict added (FMCG 10% → Aviation 15%). `_get_sector_wacc(sector)` called in `_extract_base_params()`. Sector inferred from `raw.get("sector")`.

### `agents/warren_bot.py`
- **P0-A**: `_SECTOR_DISCOUNT_RATES` dict + `_get_sector_discount_rate(sector)` added. `_dcf_valuation()` now accepts optional `discount_rate` param. `analyse()` passes sector WACC from yfinance `info["sector"]`.
- **P0-C**: Already correct — `_log_to_supabase()` only inserts `agent_name` + `audit_date`. No change needed.

### `agents/macro.py`
- **P0-B**: `get_sector_adjusted_macro_score(macro_result, sector)` added at bottom of file. Adjusts macro score ±8 pts based on sector's specific macro outlook (IT benefits from weak INR, Oil&Gas penalised). Returns `sector_adjusted=True` flag to prevent double-adjustment.
- **BF-4**: `_fetch_india_macro_news()` + `_score_macro_news()` added. Fetches Google News RSS for 4 India macro query terms every run (no API key needed). Keyword-matches positive/negative macro shocks (±10 score adjustment). `analyse()` now outputs `macro_news_signal`, `macro_news_events` at top level + `detail.macro_news` sub-dict.

### `scheduler/orchestrator.py`
- **P0-B**: `_run_agents_for_symbol()` now calls `get_sector_adjusted_macro_score()` after Phase 1 gives the fundamental sector, replacing the identical market-wide macro result with a stock-specific one.
- **P1-D**: `_fallback_synthesis()` thresholds tightened: `≥75=BUY, ≥58=HOLD, ≤30=AVOID` (was 72/55/35).

### `agents/discovery_screener.py`
- **P0-F**: `prescreen()` Filter 3 replaced: was `_fii_net_buying()` (market-wide aggregate, same value for all 200 stocks) → now `institutional_holding_pct ≥ 5%` from screener data (stock-specific). Threshold simplified to 4-of-5 (no more relaxed 3-of-4 path since FII is no longer needed).
- **P0-E**: CRITICAL tier threshold changed from `upside ≥ 100% / conf ≥ 70%` to `upside ≥ 40% / conf ≥ 75% / data_quality != ESTIMATED`. Old 100% threshold fired almost exclusively on screener artefacts. New threshold is achievable for genuinely undervalued stocks and a meaningful step above STANDARD (20%/65%).
- **P0-B**: `_run_all_agents()` now applies `get_sector_adjusted_macro_score()` after the fundamental agent returns the sector.

---

## Execution Roadmap

Full investment-grade improvement plan: see **`EXECUTION_PLAN.md`** in project root.

> **Standing rule:** After every build session, update BOTH `CLAUDE.md` (technical state) AND `EXECUTION_PLAN.md` (visual progress tracker — mark items ✅ with date, update progress count).

**Phase summary:**
- **Pre-work** ✅: Run `create_earnings_calendar.sql` migration + seed 150 RAG events
- **Step 9** ✅: Analyse Railway + Vercel logs before coding
- **Phase 0 (P0)** ✅: Zero-cost code fixes — WACC, macro sensitivity, DCF fix, discovery thresholds
- **Phase 1 (P1)** ✅: Historical backtest framework, options paid feed, GPT-4o 3rd judge, score calibration
- **Bug Fix Session** ✅: yfinance 1.2.0 fix, discovery screener 0-pass bugs, FII stale zeros, macro news, embeddings, partial sell, symbol aliases
- **Phase 2 (P2)** ← CURRENT: Data diversification, RAG auto-refresh, portfolio concentration alerts, earnings calendar auto-populate
- **Phase 3 (P3)**: Position sizing, correlation alerts
- **Phase 4 (P4)**: Commentary grounding, symbol cache persistence, governance numerical check
- **Phase 5 (P5)**: Robust forward paper portfolio tracker + attribution analysis
- **Phase 6 (P6)**: Dashboard performance tab (hit rate, alpha, backtest results)

**Estimated additional monthly cost at full build:** ₹1,039–3,498/month (Quantsapp options feed + Trendlyne fundamentals backup + OpenAI GPT-4o-mini judges)
