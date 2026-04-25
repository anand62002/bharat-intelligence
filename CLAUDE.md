# Bharat Intelligence — Claude Project Brief

> This file is read automatically by Claude Code at the start of every session.
> It is the canonical reference for codebase structure, architecture, conventions,
> and deployment. Update it whenever significant changes are made.

---

## What this project is

A multi-agent Indian stock/commodity market intelligence platform.
- **9 AI agents** analyse fundamentals, technicals, sentiment, macro, institutional flows, sector PE, commodities, historical patterns, and proactively discover new opportunities.
- **Governance layer** audits agent accuracy, detects hallucinations, scans AI research papers, and proposes improvements via GitHub PRs.
- **Scheduler** runs everything daily via APScheduler.
- **FastAPI backend** (`api/main.py`) serves live data to a React dashboard.
- **React dashboard** (`dashboard/src/App.jsx`) — single-file SPA with ARIA AI chat, portfolio tracker, discovery engine, governance tab.

---

## Repo layout

```
Stock analysis/
├── agents/                     # 9 analysis agents (all extend a common pattern)
│   ├── technical.py            # TA indicators via yfinance
│   ├── fundamental.py          # Valuation, ratios, screeners
│   ├── sentiment.py            # News + social sentiment NLP
│   ├── macro.py                # RBI, inflation, currency, global macro
│   ├── institutional.py        # FII/DII flow analysis
│   ├── sector_valuation.py     # Live sector PE regime vs 5-yr average
│   ├── commodities.py          # Gold, crude, silver MCX
│   ├── historical_rag.py       # pgvector semantic similarity on past events
│   └── discovery_screener.py   # Proactive stock discovery (multi-screen)
│
├── governance/                 # Agent oversight & self-improvement
│   ├── fact_checker.py         # Cross-agent claim verification
│   ├── hallucination_detector.py
│   ├── performance_tracker.py  # Accuracy/hallucination rate logging
│   ├── research_agent.py       # Daily AI paper scanner (arXiv, SS, HuggingFace)
│   └── github_manager.py       # Opens GitHub PRs for approved research proposals
│
├── scheduler/                  # APScheduler daily pipeline
│   ├── orchestrator.py         # Master scheduler — wires all agents + governance
│   ├── portfolio_monitor.py    # Monitors open holdings, fires portfolio_alerts
│   ├── sector_pe_tracker.py    # Daily sector_pe_snapshots writes
│   └── performance_tracker.py  # Writes agent_performance rows daily
│
├── data/
│   ├── fetchers.py             # India market data fetchers (NSE, BSE, RBI, SEBI)
│   └── symbol_map.py           # NSE symbol normalisation (same logic as api/main.py _resolve_yf_symbol)
│
├── api/
│   ├── __init__.py
│   └── main.py                 # FastAPI backend (9 endpoints + WebSocket)
│
├── dashboard/
│   ├── src/App.jsx             # Entire React SPA (~1600 lines, single file)
│   ├── src/index.js
│   ├── api/
│   │   ├── aria.js             # Vercel serverless fn — proxies to Anthropic API
│   │   └── research.js         # Vercel serverless fn — proxies to Supabase research_proposals
│   ├── vercel.json             # Vercel build config (root dir must be set to dashboard/ in Vercel UI)
│   └── package.json            # React 18, CRA
│
├── db/
│   ├── schema.sql              # Full Supabase schema (run once to create all tables)
│   └── migrations/
│       ├── grant_service_role_rls.sql          # RLS policies for service_role
│       ├── create_research_proposals.sql
│       ├── enhancement_proposals.sql
│       ├── fix_rls_permissions.sql
│       └── sector_pe_snapshots.sql
│
├── tests/                      # pytest — one test file per module
├── requirements.txt
├── Procfile                    # Railway start command
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
| `daily_runs` | Scheduler run log | `run_date, status, agents_run, errors` |
| `research_proposals` | AI paper proposals | `title, source, url, relevance, status, proposed_change, impacted_agents, debate_log (jsonb), pr_url, metadata (jsonb)` |
| `sector_pe_snapshots` | Daily sector PE | `sector, pe_ratio, avg_5yr_pe, regime, snapshot_date` |
| `enhancement_proposals` | User-requested enhancements | `title, description, cost_usd, status, is_paid` |

---

## API endpoints (`api/main.py`)

Base URL (Railway): `https://bharat-intelligence-two-production.up.railway.app` *(confirm current URL)*

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Health check — no auth needed. Returns `{"status":"ok","db":true}` |
| GET | `/api/recommendations` | Latest recs sorted upside_pct desc, critical first |
| GET | `/api/discovery` | `is_discovery=true` recs from today (7-day fallback) |
| GET | `/api/portfolio` | Open holdings, refreshes current_price from yfinance |
| POST | `/api/portfolio` | Add/update holding — auto-resolves yfinance symbol, fetches live price |
| GET | `/api/portfolio/alerts` | Unresolved portfolio alerts |
| GET | `/api/symbol/resolve?q=RELIANCE` | Resolves any input to yfinance ticker + live price |
| GET | `/api/governance/alerts` | Aggregated from portfolio_alerts + degrading agent_performance |
| GET | `/api/governance/research` | Research proposals with debate status computed |
| GET | `/api/market/pulse` | Live yfinance prices (NIFTY, SENSEX, GOLD, CRUDE, VIX, FII) — 60s cache |
| WS | `/ws/alerts` | WebSocket — broadcasts DANGER/CRITICAL alerts every 30s |

**Auth:** `x-api-key: <DASHBOARD_API_KEY>` header on all HTTP. `?api_key=<key>` on WebSocket.
**Open in local dev** when `DASHBOARD_API_KEY` env var is unset.

### Symbol auto-resolution order (`_resolve_yf_symbol`)
1. `_NSE_OVERRIDES` dict (indices, ETFs, aliases: NIFTY→^NSEI, GOLD→GC=F, etc.)
2. Already has suffix (.NS/.BO/=X/=F) or starts with ^
3. Live probe SYMBOL.NS via yfinance 1-day history
4. Live probe SYMBOL.BO
5. Default: SYMBOL.NS

Results cached in `_symbol_cache` dict for process lifetime.

---

## React dashboard (`dashboard/src/App.jsx`)

**Single file ~1600 lines.** Key sections:

| Lines (approx) | Section |
|---|---|
| 1–150 | Constants: mock data (PORTFOLIO_RECOMMENDATIONS, DISCOVERY_UNIVERSE, MARKET_PULSE, etc.) — used as defaults / offline fallbacks |
| 150–200 | API config: `API_URL`, `API_KEY`, `apiFetch()` helper |
| 200–430 | Small UI components: MarketTicker, AlertBanner, CriticalOpportunityBanner, etc. |
| 430–670 | ResearchDiscoveryTab component |
| 670–820 | PortfolioTab component |
| 820–1000 | GovernanceResearchTab component |
| 1000–1130 | Charts and sub-components |
| 1130–1350 | ARIAPanel component (AI chat) |
| 1350–1640 | App() root component — state, useEffect, routing |

**Live data loading pattern:**
```javascript
// State initialises from mock constants (immediate render, no flicker)
const [portfolioRecs, setPortfolioRecs] = useState(PORTFOLIO_RECOMMENDATIONS);

// useEffect replaces with live data after mount
useEffect(() => {
  if (!API_URL) return;   // ← skips entirely if REACT_APP_API_URL not set
  apiFetch("/api/recommendations").then(d => setPortfolioRecs(d));
  // ...parallel loads for all 6 data sources...
}, []);
```

**ARIA portfolio action flow:**
1. User says: *"I bought Reliance 15 shares at 2850"*
2. ARIA outputs `<portfolio_action>{"action":"add","symbol":"RELIANCE","qty":15,"avgBuy":2850}</portfolio_action>` at end of response
3. `handlePortfolioUpdate()` in App() parses it → calls `POST /api/portfolio`
4. Backend auto-resolves RELIANCE→RELIANCE.NS, fetches live price, saves to Supabase

**ARIA endpoint:** `POST /api/aria` → Vercel serverless function (`dashboard/api/aria.js`) → Anthropic Messages API.
Uses `ANTHROPIC_API_KEY` env var server-side (never exposed to browser).

---

## Environment variables

### Railway (backend)
| Variable | Description |
|---|---|
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | service_role key (bypasses RLS) |
| `DASHBOARD_API_KEY` | Secret shared with Vercel frontend |
| `VERCEL_DASHBOARD_URL` | Exact Vercel URL for CORS (e.g. `https://bharat-intelligence-two.vercel.app`) |

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

### Railway (FastAPI backend)
- Auto-deploys on push to `main`
- Start command: `uvicorn api.main:app --host 0.0.0.0 --port $PORT` (in `Procfile` + `railway.toml`)
- Health check: `GET /health`

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

# Frontend
cd dashboard
npm install
npm start          # CRA dev server on port 3000
                   # proxies /api/* to localhost:8000 if proxy set in package.json
```

---

## Key design decisions & conventions

- **Snake_case in DB, camelCase in React.** Transformers in `api/main.py` handle the conversion: `_transform_holding()`, `_transform_recommendation()`, `_transform_research()`.
- **Mock data as fallback.** All React state is initialised with mock constants. Live data replaces it after mount. If `REACT_APP_API_URL` is blank, mock data stays — zero crash risk.
- **yf_symbol stored separately.** `portfolio_holdings` has both `symbol` (display, e.g. `RELIANCE`) and `yf_symbol` (e.g. `RELIANCE.NS`). GET /api/portfolio uses `yf_symbol` to refresh prices.
- **60s market cache.** `_market_cache` + `_market_cache_ts` globals in `api/main.py` prevent hammering yfinance on every dashboard render.
- **Governance alerts have no dedicated table.** Aggregated on the fly from `portfolio_alerts` (CRITICAL/DANGER severity) + `agent_performance` (DEGRADING trend).
- **debateStatus computed, not stored.** `research_proposals` only has `status`. `debateStatus` (pending/debating/approved) is computed from `debate_log` vote counts in `_transform_research()`.
- **Service_role BYPASSRLS.** Supabase service_role has built-in RLS bypass but still needs `GRANT ALL` for table privileges. Both are set in `db/migrations/grant_service_role_rls.sql`.

---

## Common tasks

**Add a new API endpoint:**
Edit `api/main.py` → add `@app.get("/api/...")` function → add corresponding `apiFetch()` call in `dashboard/src/App.jsx` useEffect.

**Add a new agent:**
1. Create `agents/new_agent.py` following the same pattern as `agents/technical.py`
2. Register it in `scheduler/orchestrator.py`
3. Add test in `tests/test_new_agent.py`

**Run all tests:**
```powershell
python -m pytest tests/ -q --tb=short
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

---

## Git history (recent)

| Commit | Change |
|---|---|
| `aa1e5d0` | Fix Vercel deployment — move build config to dashboard/vercel.json |
| `40fc5f8` | Fix vercel.json monorepo build (broke functions — reverted in aa1e5d0) |
| `c7fd22a` | Add Railway deployment config (Procfile + railway.toml) |
| `5b1fb1d` | Symbol auto-resolution, live price refresh, /api/symbol/resolve endpoint |
| `15c8fe4` | Add FastAPI backend (api/main.py) + wire React dashboard to live data |
| `18b5de4` | Add list_branches() to GitHubManager |
| `b887bcc` | Add service_role RLS access migration |
| `d7fef77` | Fix arXiv RSS OAI URLs, add HuggingFace+AI Scout sources, integration tests |
