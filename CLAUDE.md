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
│   │                           # Result includes ohlcv_last_date: ISO date of last OHLCV bar
│   │                           #   used by audit_data_leakage() for temporal integrity checks
│   │                           # ATR-14 stoploss fields (added 2026-07-22):
│   │                           #   atr_14: float — 14-day EWM Average True Range (₹)
│   │                           #   atr_stoploss: float — current_price - 2×ATR14 (floor for synthesis)
│   │                           #   atr_stoploss_pct: float — atr_stoploss as % of price
│   │                           # Synthesis prompt constraint: stoploss MUST be ≤ atr_stoploss
│   │                           #   (injected via {atr_stoploss} placeholder in orchestrator_synthesis.txt)
│   ├── fundamental.py          # Valuation, ratios, screeners
│   │                           # Result includes data_as_of: date.today().isoformat() — snapshot
│   │                           #   fetch time; used by audit_data_leakage() for leakage detection
│   ├── sentiment.py            # News + social sentiment NLP
│   │                           # P6-D D-1: get_bse_announcements() feeds corporate filings into pipeline
│   │                           # P6-D D-2: _batch_classify_headlines() — single Haiku batch call (Janus-Q);
│   │                           #   event taxonomy: EARNINGS_SURPRISE/REGULATORY_SHOCK/M_A_SIGNAL/MACRO_CATALYST/
│   │                           #   ANALYST_ACTION/MANAGEMENT_SIGNAL/SECTOR_CATALYST/ROUTINE; multipliers 0.5–3.0×
│   │                           # P6-D D-3: _temporal_weight() — exp(-ln(2)/half_life × age_hours);
│   │                           #   event-specific half-lives 2–48h; applied before multiplier
│   │                           # P6-D D-4: _call_finbert_hf() — ProsusAI/finbert via HF Inference API;
│   │                           #   ensemble 0.6×FinBERT + 0.4×Haiku on top-5 headlines by decay weight;
│   │                           #   requires optional HF_API_TOKEN; falls back gracefully on rate-limit
│   ├── macro.py                # RBI, inflation, currency, global macro
│   ├── institutional.py        # FII/DII flow analysis
│   ├── sector_valuation.py     # Live sector PE regime vs 5-yr average
│   │                           # SECTOR_LONGRUN_PE: 5-yr structural median (Dec 2019–Dec 2024)
│   │                           # — used for regime classification AND as tier-2 fallback in
│   │                           #   discovery _get_sector_pe() lookup
│   │                           # SECTOR_PE_MAP (in fundamental.py): current-year forward benchmarks
│   │                           # — different from SECTOR_LONGRUN_PE by design (see fundamental.py header)
│   │                           # Live pipeline: NSE allIndices API → sector_pe_snapshots table (daily)
│   │                           # compute_rolling_longrun_pe() → 365-day median; auto-activates as tier-1
│   │                           #   in _get_sector_pe() once ≥90 data points accumulate (~3 months)
│   ├── commodities.py          # Gold, crude, silver MCX
│   ├── historical_rag.py       # pgvector semantic similarity on past events
│   ├── discovery_screener.py   # Proactive stock discovery — full NSE EQ universe
│   │                           # daily slice rotation (200/day → 9-day full cycle)
│   │                           # Filter 2 (PE): sector-relative three-tier filter
│   │                           #   Tier A: PE ≤ sector_median → undervalued vs peers (strong)
│   │                           #   Tier B: PE ≤ sector×1.2 AND PE≤80 → fair value vs peers
│   │                           #   Tier C: PE ≤ sector×2.0 AND PE≤80 AND revGrowth>30% → growth premium
│   │                           #   Hard cap: PE > 80 always fails regardless of sector/growth
│   │                           #   Sector median source: _get_sector_pe() three-layer lookup:
│   │                           #     1. compute_rolling_longrun_pe() — live 365-day DB median (≥90 pts)
│   │                           #     2. SECTOR_LONGRUN_PE in sector_valuation.py (5-yr structural median)
│   │                           #     3. DEFAULT_SECTOR_PE 22x fallback
│   ├── market_digest.py        # P6-C: Morning Brief + Closing Digest agent
│   │                           # Entry: generate_digest(digest_type) → dict; save_digest(digest, client, dry_run)
│   │                           # digest_type: MORNING | CLOSING
│   │                           # RSS sources: ET Markets, Moneycontrol, Hindu BizLine, BS + 3 Google News feeds
│   │                           # Single Claude Haiku call → JSON {market_mood, summary, key_events,
│   │                           #   top_themes, sectors_in_focus, nifty_signal}
│   │                           # Keyword fallback when no ANTHROPIC_API_KEY
│   │                           # Stores in market_digests table (upsert on digest_type+digest_date)
│   │                           # VALID_MOODS: BULLISH|BEARISH|NEUTRAL|VOLATILE|MIXED
│   ├── warren_bot.py           # Long-term business quality (Buffett+Jhunjhunwala)
│   ├── position_sizer.py       # P3-A: 4-tier Kelly position sizing (FULL 5%/HALF 2.5%/QUARTER 1.25%/AVOID 0%)
│   ├── paper_portfolio.py      # P5-B: Paper portfolio simulation — auto-follows BUY signals, tracks P&L vs Nifty 50
│   │                           # open_new_positions() seeds paper positions for every new BUY rec (07:05 IST daily)
│   │                           # update_open_positions() refreshes prices + checks SL/target/horizon exits (16:15 IST)
│   │                           # save_daily_snapshot() persists portfolio-level P&L metrics for charting
│   │                           # Allocation: FULL=₹10k, HALF=₹5k, QUARTER=₹2.5k; exit: SL 15%, target 40%, horizon 90d
│   │                           # CLI: python -m agents.paper_portfolio [--run] [--backfill] [--report]
│   └── outcome_tracker.py      # P5-A/D/E: outcome resolution + live poller + attribution
│   #   run_outcome_tracking()  — daily 18:30 IST, resolves t+90/180/365 horizons (HIT/MISS/PARTIAL)
│   #   run_forward_polling()   — P5-D: daily 16:30 IST, batch live prices → alpha_live/return_live/days_live
│   #                             + resolves t+30 milestone (price_t30/outcome_t30)
│   #   get_live_performance_summary() — P5-E: portfolio-level live stats for /api/performance/live
│   #   run_live_attribution()  — P5-E: per-agent live alpha before 90d data exists
│   #   compute_agent_attribution() + run_attribution_analysis() — P5-A resolved 90d attribution
│   #                             calc_position_size(upside_pct, confidence, action, mos_pct, warren_score)
│   #                             MOS source: warren_bot DCF (primary) → upside_pct proxy (fallback)
│   #                             FULL tier requires DCF MOS — proxy cannot qualify (quality gate)
│
├── governance/                 # Agent oversight & self-improvement
│   ├── fact_checker.py         # Cross-agent claim verification
│   ├── hallucination_detector.py
│   ├── performance_tracker.py  # Accuracy/hallucination rate logging
│   │                           # + audit_data_leakage() — temporal leakage audit
│   │                           #   LeakageViolation / DataLeakageReport dataclasses
│   │                           #   _check_technical_temporal_integrity() — flags ohlcv_last_date > signal_ts+1d (BLOCKING)
│   │                           #     or > 7d stale (WARNING)
│   │                           #   _check_fundamental_temporal_integrity() — flags data_as_of > signal_ts (WARNING)
│   │                           #   _check_rag_temporal_integrity() — flags matched_event.event_date > signal_ts (BLOCKING)
│   │                           #   Called from orchestrator synthesise_node() before _apply_consensus_gate()
│   │                           #   block_on_leak=False by default — logs only; set True for strict backtest mode
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
│   #    08:45 — market digest MORNING (P6-C)
│   #    09:15, 11:30, 13:30, 15:15 — portfolio monitor
│   #    15:45 — options snapshot (uses Breeze if configured)
│   #    16:20 — market digest CLOSING (P6-C)
│   #    07:45 (Sunday) — weekly health audit (scripts/weekly_audit.py) ← NEW
│   #    07:45 (1st of month) — historical backtest (agents/backtester.py)
│   #    08:15 (1st of month) — RAG corpus auto-refresh (db/auto_seed_rag.py)
│
├── data/
│   ├── fetchers.py             # India market data fetchers (NSE, BSE, RBI, SEBI)
│   │                           # + get_bse_announcements(symbol, hours) — P6-D D-1: BSE corporate
│   │                           #   announcements API (api.bseindia.com); pre-tags event_hint="BSE_FILING";
│   │                           #   consumed by sentiment.py; strips .NS/.BO suffix before querying
│   │                           # + get_screener_history() — 10yr annual time series
│   │                           # + _parse_screener_excel() — DB-10: parses 'Data Sheet' tab from
│   │                           #   screener.in Excel export (POST /user/company/export/{id}/).
│   │                           #   Visual sheets (P&L, Balance Sheet) use merged cells → unusable.
│   │                           #   Data Sheet: datetime Report Date row → years; computes OPM% &
│   │                           #   EPS from raw fields. ROCE/ROE/Promoter not in export (→ []).
│   │                           #   Export triggered when HTML parsing gives < 5 years of data.
│   ├── symbol_map.py           # NSE → yfinance symbol resolution (YF_SYMBOL_MAP)
│   │                           # Single source of truth for all agents.
│   │                           # Also has SCREENER_SLUG_MAP for screener.in slugs.
│   ├── options_fetcher.py      # Option chain: Trendlyne F&O → NSE → VIX fallback
│   │                           # get_option_metrics(symbol) → pcr, max_pain, atm_iv, iv_skew
│   ├── trendlyne_fno_fetcher.py # Trendlyne F&O Excel download (primary options source)
│   │                           # get_option_metrics(), get_fno_universe(), get_buildup_signals()
│   │                           # Memory design: compile→compact dict at download time, gc.collect()
│   ├── trendlyne_analyst_fetcher.py  # Trendlyne analyst targets scraper (P3-C-BE)
│   │                           # get_analyst_targets(symbol) → consensus_target, buy_pct,
│   │                           # consensus_rating, upside_to_consensus, eps_current_yr, eps_next_yr
│   │                           # interpret_analyst_targets(targets, our_upside_pct) → signal+summary
│   │                           # Per-symbol 6h cache; auto-cookie-refresh via TRENDLYNE_USER+PASS
│   ├── trendlyne_fetcher.py    # Trendlyne equity page scraper — tier-2 fallback for screener.in (P3-C-P1)
│   │                           # get_trendlyne_fundamentals(symbol) → same schema as get_screener_data()
│   │                           # get_trendlyne_dvm(symbol) → {durability,valuation,momentum,composite_dvm}
│   │                           # get_upcoming_earnings(symbol) → {date,source,confirmed,raw_text} (P3-C-P2)
│   │                           # Shares page cache (6h TTL) across all three functions per symbol
│   ├── insider_signal.py       # Promoter/insider holding trend signal (P3-C-P5/P3-C-P6)
│   │                           # get_promoter_signal(symbol) → {signal,current_holding,change_1y,change_3y,source,note}
│   │                           # signal: ACCUMULATING | DISTRIBUTING | NEUTRAL
│   │                           # Data: screener_history (trend) → screener_snapshot → trendlyne_snapshot
│   │                           # Used by: sentiment.py (+5/-10 pts) + institutional.py (+8 pts ACCUMULATING)
│   ├── forward_estimates.py    # yfinance forward EPS/PE estimates (24h Supabase cache)
│   ├── proxy_session.py        # BF-15/15b: Outbound proxy abstraction for Railway IP blocks
│   │                           # apply_proxy_to_session(session) — routes via SCRAPERAPI_KEY
│   │                           # (rotating residential, $29/mo) or FIXIE_URL (static, $25/mo)
│   │                           # ScraperAPI: sets session.verify=False (SSL CONNECT cert not trusted
│   │                           #   by Railway CA bundle — safe for read-only public market data)
│   │                           # Used by: fetchers.py (screener.in) + trendlyne_analyst_fetcher.py
│   │                           # proxy_configured() → bool; get_proxy_dict() → dict|None
│   └── breeze_auth.py          # ICICI Breeze Connect — DEPRECATED (P4-D: scheduled for removal)
│   #                             Superseded by trendlyne_fno_fetcher (current primary).
│   #                             P4-D will replace with Angel One SmartAPI as new live tier-1 source.
│   #                             Angel One credentials needed: ANGEL_ONE_API_KEY, ANGEL_ONE_CLIENT_ID,
│   #                             ANGEL_ONE_PASSWORD, ANGEL_ONE_TOTP_SECRET (see EXECUTION_PLAN P4-D).
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
│   ├── auto_seed_rag.py        # Monthly: fetch India macro news → classify → embed → insert
│   │                           # Sources: Google News RSS (8 queries, 35-day window)
│   │                           # Classification: gpt-4o-mini (LLM) or keyword fallback (no key)
│   │                           # Deduplication: ±7-day window per event_type vs existing DB rows
│   │                           # CLI: python -m db.auto_seed_rag [--run] [--days N] [--max N]
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
│       ├── create_backtest_results.sql         # walk-forward backtest results (P1-A)
│       └── create_paper_portfolio.sql          # ← NEW: paper_portfolio_positions + paper_portfolio_snapshots (P5-B)
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
| `paper_portfolio_positions` | P5-B paper trade log | `rec_id, symbol, yf_symbol, entry_date, entry_price, quantity, allocation_inr, position_label, stoploss_price, target_price, nifty_entry, current_price, current_value, unrealized_pnl, unrealized_pnl_pct, status (OPEN/CLOSED/SKIPPED), exit_date, exit_price, nifty_exit, realized_pnl, realized_pnl_pct, alpha_pct, exit_reason` |
| `paper_portfolio_snapshots` | P5-B daily portfolio P&L | `snapshot_date (unique), total_invested, total_current_value, unrealized_pnl, realized_pnl, total_pnl, total_pnl_pct, open_positions, closed_positions, nifty_value, nifty_return_pct, alpha_pct` |
| `market_digests` | P6-C daily market briefs | `id (UUID PK), digest_type (MORNING/CLOSING), digest_date (DATE), headline_count, top_themes (jsonb), summary, key_events (jsonb), market_mood, nifty_signal, sectors_in_focus (jsonb), raw_headlines (jsonb), created_at` — unique on (digest_type, digest_date) |

> **All migrations applied ✅** (warren_bot_cache, sector_pe_snapshots, discovery_runs, symbol_resolutions, add_yf_symbol_danger_sources, enhancement_proposals, recommendation_outcomes, market_regime, earnings_calendar, portfolio_risk_snapshots, backtest_results, create_paper_portfolio, p5d_live_performance_columns, create_market_digests)

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
| GET | `/api/backtest/summary` | Walk-forward backtest summary from `backtest_results` — `?split=TEST\|TRAIN\|FULL&limit=5` — powers P6-B BacktestPanel |
| GET | `/api/performance/calibration` | P6-A: confidence calibration — buckets composite_score into 5 tiers (50–60, 60–70, 70–80, 80–90, 90+), returns expected vs actual hit rate per tier |
| GET | `/api/paper/portfolio` | P5-B: paper portfolio — open positions, recent closed, summary stats, win rate, avg alpha |
| GET | `/api/paper/history` | P5-B: daily `paper_portfolio_snapshots` for P&L vs Nifty chart — `?days=180` |
| GET | `/api/attribution/agents` | P5-A: per-agent hit rate + avg alpha derived from resolved recommendation_outcomes |
| GET | `/api/performance/live` | P5-D/E: live snapshot of all open (PENDING) recs — avg return/alpha, by-action tiles, per-rec table sorted by alpha_live |
| GET | `/api/attribution/live` | P5-E: per-agent live alpha attribution (before 90d data exists) — avg_bull_alpha_live, positive_rate_live |
| GET | `/api/market/digest` | P6-C: today's market digests — `?digest_type=MORNING\|CLOSING&digest_date=YYYY-MM-DD` — returns `{digests, date, count}` with camelCase keys |
| POST | `/api/analyse` | On-demand full 10-agent analysis for any symbol. Body: `{"symbol": "RELIANCE"}`. Runs full pipeline (dry_run=True — does NOT save to DB). Returns `{symbol, yf_symbol, status, analysis: {action/confidence/synthesis/...}, agents: {...}}`. Server-side 180s timeout. Status = "OK" (rec produced) or "NO_RECOMMENDATION" (suppressed). Powers ARIA /analyse command. |
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
| 1520–2250 | LivePerformancePanel (P5-E), AgentAttributionPanel (P5-A) |
| 2250–2450 | ConfidenceCalibrationChart (P6-A), TopCallsPanel (P6-A), BacktestPanel (P6-B) |
| 2450–2800 | PerformanceTab component — LivePerf + accuracy + calibration + top calls + attribution + backtest |
| 2800–end  | App() root component — state, useEffect, routing |

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
| `BREEZE_TOTP_SECRET` | *(Optional)* Base32 TOTP secret — enables fully automated daily refresh (DEPRECATED, see P4-D) |
| `TRENDLYNE_SESSION` | `.trendlyne` cookie value — required for F&O Excel download + analyst targets scraper |
| `TRENDLYNE_CSRF` | `csrftoken` cookie value — required alongside TRENDLYNE_SESSION |
| `TRENDLYNE_USER` | *(Optional)* Trendlyne login email — enables auto-cookie-refresh when session expires |
| `SCREENER_SESSION` | *(Optional)* screener.in `sessionid` cookie — enables Excel export fallback in `get_screener_history` (DB-10). Get it: log in at screener.in via Google → DevTools → Application → Cookies → screener.in → copy `sessionid` value. Refreshed manually when it expires. |
| `TRENDLYNE_PASS` | *(Optional)* Trendlyne login password — enables auto-cookie-refresh |
| `SCRAPERAPI_KEY` | **Recommended for Railway** — ScraperAPI rotating residential proxy ($29/month, 250k req). Bypasses screener.in + Trendlyne IP blocks. Get key at scraperapi.com. Applied automatically to all screener.in + Trendlyne requests via `data/proxy_session.py`. |
| `FIXIE_URL` | *(Optional, free Railway add-on)* Fixie HTTP proxy URL — alternative to ScraperAPI. Static residential IP. Format: `http://user:pass@proxy.usefixie.com:80`. Business plan ($25/month) needed for 25k req/month. |

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
| (2026-07-22 session) | **ATR-14 stoploss**: `agents/technical.py` now computes `atr_14`, `atr_stoploss` (entry−2×ATR), `atr_stoploss_pct`; synthesis prompt (`prompts/orchestrator_synthesis.txt`) enforces stoploss ≥ ATR floor via `{atr_stoploss}` placeholder; orchestrator injects it from technical agent result. **ARIA /analyse command**: `POST /api/analyse` endpoint — runs full 10-agent pipeline (dry_run=True, no DB write), 180s timeout, returns analysis+agents dict; ARIA detects "analyse SYMBOL" intent → confirm → `<run_analyse>` tag → dashboard calls endpoint and shows result. **Weekly health audit**: `scripts/weekly_audit.py` — 9-check PASS/WARN/FAIL report (kappa, daily_runs, alpha_live, trendlyne, discovery, RAG, agent_performance, forward_poller, outcome_seeder); Sunday 07:45 IST worker job. **Architecture white paper**: `docs/ARCHITECTURE.md` rewritten for business/finance audience — 500-word executive summary + ~200-word elevator pitch per agent. **Fable 5 architect guide**: `docs/FABLE5_REDESIGN_PROMPT.md` updated with Part A structured pain-points prompt. 30 new tests (6 ATR + 14 weekly_audit + 10 on_demand_analyse). LOG_LEVEL=INFO set on Railway worker — kappa now visible at INFO level in Railway logs. |
| (fix macro test) | `tests/test_macro.py`: added `_fetch_india_macro_news` mock to `_mock_analyse_deps` helper — without it, live Google News RSS calls during tests produced a non-zero `news_adj`, breaking `test_score_components_sum` (95 ≠ 100). |
| (data-leakage-audit) | `governance/performance_tracker.py`: `LeakageViolation` + `DataLeakageReport` dataclasses; `_check_technical_temporal_integrity()` (BLOCKING: ohlcv_last_date > signal_ts+1d; WARNING: stale >7d), `_check_fundamental_temporal_integrity()` (WARNING: data_as_of > signal_ts), `_check_rag_temporal_integrity()` (BLOCKING: matched_event.event_date > signal_ts); `audit_data_leakage()` orchestrates all checks. `agents/technical.py`: added `ohlcv_last_date` field. `agents/fundamental.py`: added `data_as_of` field. `scheduler/orchestrator.py`: leakage audit called in `synthesise_node()` pre-consensus-gate, violations stored in `synthesis_data["metadata"]["leakage_violations"]`. 43 tests added. |
| (P6-C/D) | P6-C: `agents/market_digest.py` — Morning/Closing digest via single Haiku call; `db/migrations/create_market_digests.sql`; `GET /api/market/digest`; `MarketDigestPanel` React component (mood colour coding, impact badges, themes, sectors, nifty signal); worker jobs 08:45 IST (MORNING) + 16:20 IST (CLOSING). P6-D: `agents/sentiment.py` upgraded with D-1 BSE announcements (`get_bse_announcements()`), D-2 batch event classifier (Janus-Q: 8 event classes, `_batch_classify_headlines()`), D-3 temporal decay (`_temporal_weight()`, event-specific half-lives 2–48h), D-4 FinBERT ensemble (`_call_finbert_hf()`, 0.6×FinBERT+0.4×Haiku on top-5 headlines). `data/fetchers.py`: `get_bse_announcements()` added. 70 new tests (29 market_digest + 41 sentiment_elite); 4 pre-existing sentiment tests fixed (batch classifier mock + `haiku_calls` count fix). |
| (P6-A/B) | P6-A: `ConfidenceCalibrationChart` — buckets composite_score into 5 tiers, shows expected vs actual hit rate (calibration quality); `TopCallsPanel` — top 5 best/worst calls by t90 alpha. New `/api/performance/calibration` endpoint. P6-B: `BacktestPanel` — TRAIN/TEST/FULL split selector, summary tiles (avg hit rate/alpha/Sharpe/drawdown), per-run monthly table; wires to existing `/api/backtest/summary`. Both panels show proper empty states until data accumulates. |
| (BF-17 sector PE fix) | `_get_sector_pe()` in discovery_screener.py upgraded to three-layer lookup: (1) `compute_rolling_longrun_pe()` from live `sector_pe_snapshots` DB — auto-activates when ≥90 data points accumulated; (2) `SECTOR_LONGRUN_PE` from `sector_valuation.py` (5-yr structural median); (3) `DEFAULT_SECTOR_PE` 22x. Two-static-map architecture documented: `SECTOR_PE_MAP` (fundamental.py) = current-year forward scoring; `SECTOR_LONGRUN_PE` (sector_valuation.py) = structural 5-yr median for regime + discovery. Discovery PE filter (BF-17) uses structural median; fundamental scoring continues to use forward benchmarks. |
| (BF-16 discovery PE + consensus gate) | Filter 2 in discovery pre-screen replaced flat `PE < 50` with sector-relative three-tier logic (Tier A/B/C). Consensus gate added to orchestrator synthesis path + discovery CRITICAL tier (prevents 1-agent BUY promotions). Hallucination false-positive root causes fixed: `fact_check.txt` tolerances now metric-specific (PE ±15%, revenue ±20%, etc.); derived/computed claims (`upside_pct`, `danger_drop_pct`) removed from `_extract_claims()` in `fact_checker.py`. |
| (P5-D/E audit fix) | Interface & DB audit — 3 bugs fixed in `outcome_tracker.py`: (1) removed `progress=False` from `yf.download()` (deprecated yfinance 1.2.x); (2) changed `.in_("outcome_t90", ["PENDING", None])` → `.or_("outcome_t90.eq.PENDING,outcome_t90.is.null")` (Python `None` in `.in_()` generates literal `'None'` string, never matches SQL NULL); (3) all 3 P5-D/E functions now catch PGRST column-not-found errors and fall back gracefully (empty data) instead of HTTP 500 when migration not yet applied. Full audit: 21 dashboard apiFetch() calls verified vs defined routes; all DB column names verified vs code writes; worker imports verified vs exported functions. |
| (P5-D/E) | P5-D: `run_forward_polling()` in outcome_tracker.py — daily 16:30 IST batch live price snapshot (alpha_live/return_live/days_live) + t+30 milestone; `db/migrations/p5d_live_performance_columns.sql`; `job_forward_poller()` in worker.py. P5-E: `/api/performance/live` + `/api/attribution/live` endpoints; `LivePerformancePanel` component in dashboard (open positions table, by-action alpha tiles, avg return/alpha header); `AgentAttributionPanel` upgraded to show live attribution mode before 90d data exists |
| (P5-A/B) | P5-A: `compute_agent_attribution()` + `run_attribution_analysis()` added to `agents/outcome_tracker.py`; `GET /api/attribution/agents` endpoint — per-agent hit rate + avg alpha from resolved recommendation_outcomes. P5-B: `agents/paper_portfolio.py` — auto-follows BUY recs (FULL=₹10k/HALF=₹5k/QUARTER=₹2.5k), SL/target/horizon exits, daily snapshot; `db/migrations/create_paper_portfolio.sql`; worker jobs 07:05/16:15 IST; `GET /api/paper/portfolio` + `GET /api/paper/history` endpoints; PaperPortfolioPanel + AgentAttributionPanel in dashboard; 49 tests |
| (BF-15b) | BF-15b: ScraperAPI SSL cert fix — `session.verify=False` for CONNECT tunnel in Railway container; urllib3 warning suppressed; `test_scraper_connectivity.py` summary fixed (now correctly shows direct✅/proxy✅ separately); OPS-1 reminder added to EXECUTION_PLAN.md |
| (BF-15) | BF-15: Railway IP block fix — `data/proxy_session.py` proxy abstraction; apply_proxy_to_session() wired into screener.in + Trendlyne sessions; SCRAPERAPI_KEY / FIXIE_URL env vars; /api/debug/scraper-health endpoint; scripts/test_scraper_connectivity.py; Trendlyne 405 retry with alt URL patterns |
| (BF-13/14) | BF-13: market pulse dashes — yfinance 1.2.x column format fix (df["Close"][sym]); BF-14: DATA_DEGRADATION status in daily_runs when all symbols suppressed |
| (P5-C) | P5-C: `agents/rec_outcome_seeder.py` — backfill all recs into recommendation_outcomes; `run_seeder(dry_run, resolve_past)`; wired into worker.py at 06:55 IST; 17 tests |
| (DB-10 rewrite) | DB-10 complete rewrite: `_parse_screener_excel()` now parses `Data Sheet` tab (visual sheets use merged cells → all None); extracts years from `datetime` Report Date row, computes OPM% = (PBT+Interest+Depr−OtherIncome)/Sales×100, EPS = NetProfit/AdjustedEquityShares; export triggered via POST to `/user/company/export/{export_id}/` (id from page `formaction`), CSRF from `csrftoken` cookie via `X-CSRFToken` header; 31 tests (all pass); live test: RELIANCE 10yr clean |
| (DB-7/8/9/10) | DB-7: Market tab live news panel (Google News RSS, topic filter); DB-8: Recs tab "My Holdings" filter toggle; DB-9: "What ran today?" ARIA button + daily_run context type; DB-10: `_parse_screener_excel()` scaffold + Excel export wiring in `get_screener_history` |
| (P4-C) | P4-C: Governance numerical grounding — `_numerical_grounding_check` pre-LLM pass; deterministic VERIFIED/CONTRADICTED for PE/ROCE/promoter/RSI/EMA; 40 new tests |
| (P4-B) | P4-B: Symbol cache persistence — already built (`_load_symbol_resolutions` + `_persist_resolution`); marked complete |
| (P4-A) | P4-A: Warren bot commentary grounding — `_validate_commentary` + `_build_grounded_commentary` + JSON-structured Haiku prompt; tone follows signal; 27 new tests (62 total) |
| (P3-C-P5/P6) | P3-C-P5+P6: Promoter/insider signal — data/insider_signal.py shared module; sentiment.py +5/-10 pts; institutional.py +8 pts ACCUMULATING; 67 new tests |
| (P3-C-P2) | P3-C-P2: Earnings calendar enhanced — trendlyne_fetcher.get_upcoming_earnings(); earnings_fetcher.py Trendlyne tier-1.5; worker.py expanded to portfolio+discovery symbols |
| (P3-C-P3) | P3-C-P3: DVM Filter 6 in discovery pre-screen — opt-in via TRENDLYNE_SESSION; 10 tests |
| (P3-C-P1) | P3-C-P1: Trendlyne fundamentals as screener.in tier-2 fallback; data/trendlyne_fetcher.py |
| (P3-A)   | P3-A: Position sizing — agents/position_sizer.py, 4-tier model, wired into orchestrator + discovery + API + dashboard (45 tests) |
| (fix)    | fix: restore FII live data (NSE schema change + brotli encoding) + sentiment news-only fallback |
| (P2-C)   | P2-C: Portfolio concentration alerts — SECTOR_CONCENTRATION + MACRO_CLUSTER (54 tests) |
| (P2-B)   | P2-B: RAG corpus auto-refresh — db/auto_seed_rag.py + worker.py monthly job |
| `414ed30` | docs: add P3-C Comprehensive Trendlyne Integration plan to EXECUTION_PLAN.md |
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
| Single data provider (screener.in) — no fallback if blocked | HIGH | `data/fetchers.py` | ✅ Fixed (P2-A) — Trendlyne tier-2 + yfinance tier-3 fallback chain |
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
| `recommendation_outcomes` table empty — no forward tracking | MEDIUM | `agents/outcome_tracker.py` | ✅ P5-C seeded; P5-D live poller fills alpha_live daily at 16:30 IST; `p5d_live_performance_columns.sql` migration ✅ applied 2026-05-20. |
| ICICI Breeze primary IP update due ~May 18 | MEDIUM | Railway env | 🔲 Update primary IP to `52.5.155.132` on ICICI Direct portal |
| No portfolio-level concentration alerts (sector overlap, macro cluster) | MEDIUM | `scheduler/portfolio_monitor.py` | ✅ Fixed (P2-C) — SECTOR_CONCENTRATION + MACRO_CLUSTER alerts added |
| No correlation-aware alerts (hidden concentration in same-direction movers) | MEDIUM | `scheduler/portfolio_monitor.py` | ✅ Fixed (P3-B) — CORR_CLUSTER alert: 60-day Pearson r>0.75, ≥2 pairs, 7-day dedup |
| screener.in + Trendlyne blocked from Railway (Errno 101 ENETUNREACHABLE / HTTP 405) | CRITICAL | `data/fetchers.py`, `data/trendlyne_analyst_fetcher.py` | ✅ Fixed BF-15 — `data/proxy_session.py` proxy abstraction built; SCRAPERAPI_KEY / FIXIE_URL env vars; ScraperAPI SSL cert fixed (BF-15b: `session.verify=False` for CONNECT tunnel). Direct connection currently working from Railway IP `152.55.180.59`. Proxy fires automatically if direct is blocked. **⏳ OPS-1: Review 2026-05-18/19 runs, buy ScraperAPI $29/month plan if needed.** |
| Market pulse cards showing "—" (all dashes) — yfinance 1.2.x column format change | HIGH | `api/main.py` `_fetch_prices_sync` | ✅ Fixed BF-13 — removed deprecated group_by/progress params, fixed column access pattern |

---

## Interface & DB Audit Checklist (OPS-2 — run weekly)

> **Recurring maintenance task.** Run every Sunday before market open (or after any major build session). Catches mismatches before they cause silent failures in production. Last full audit: 2026-05-20 (found 3 bugs — see P5-D/E audit fix in git history).

### What to check

| Area | Check | Common failure patterns |
|---|---|---|
| **API routes vs dashboard** | Every `apiFetch("/api/...")` in `App.jsx` must match a `@app.get/post(...)` in `api/main.py` | Route renamed in backend but not frontend (silent 404) |
| **DB column names** | Every `.update({...})` / `.insert({...})` key in Python code must match actual Supabase column | Migration added column but code still writes old name; or vice versa |
| **Worker imports** | Every `from agents.X import Y` in `worker.py` must be a real exported function | Function renamed/moved; worker silently skips job |
| **yfinance API** | No `progress=False` in `yf.download()` calls (deprecated yfinance 1.2.x → TypeError) | Added in new code without checking yfinance version compatibility |
| **Supabase NULL in `.in_()`** | Never pass Python `None` inside `.in_()` list → generates `'None'` string, never matches SQL NULL | Use `.or_("col.eq.VALUE,col.is.null")` pattern instead |
| **Migration-gated columns** | Any SELECT/UPDATE referencing a column from a new migration must catch `PGRST` errors and fall back gracefully | New migration not yet applied on prod → HTTP 500 instead of empty data |
| **Field name casing** | Backend DB fields are `snake_case`; React dashboard expects `camelCase`. Verify `_transform_*()` functions in `api/main.py` cover all new fields | New DB column added but not included in transformer → `undefined` in dashboard |

### How to run

```powershell
# 1. Grep all apiFetch calls in dashboard vs all route definitions in API
grep -n "apiFetch(" dashboard/src/App.jsx | grep -oP '"/api/[^"]+' | sort > /tmp/dashboard_routes.txt
grep -n "@app\.(get|post|put|delete)" api/main.py | grep -oP '"/api/[^"]+' | sort > /tmp/api_routes.txt
diff /tmp/dashboard_routes.txt /tmp/api_routes.txt

# 2. Check for progress=False in yf.download calls
grep -rn "progress=False" agents/ data/ api/ scheduler/

# 3. Check for Python None in .in_() Supabase calls
grep -rn "\.in_(" agents/ scheduler/ api/ | grep "None"

# 4. Check worker imports compile cleanly (catches missing/renamed functions)
python -c "import worker; print('worker.py imports OK')"

# 5. Check outcome_tracker migration status
python -c "from agents.outcome_tracker import get_live_performance_summary; r=get_live_performance_summary(); print('has_live_data:', r.get('has_live_data')); print('migration needed:', not r.get('has_live_data') and r.get('total_open',0)==0)"
```

### Known gotchas (recorded from past bugs)

- **yfinance MultiIndex** (1.2.x): use `df.xs("Close", axis=1, level=0)` or `df["Close"][sym]` — NOT `df[sym]["Close"]`
- **Supabase `.in_()` with None**: generates `IN ('PENDING', 'None')` — SQL NULL rows never matched. Always use `.or_()` filter string
- **`_transform_*()`** in `api/main.py`: adding a new DB column without adding it to the transformer means React gets `undefined` silently
- **P5-D live columns**: migration `db/migrations/p5d_live_performance_columns.sql` ✅ applied in Supabase (2026-05-20). `get_live_performance_summary()` now returns live data.

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
- **Phase 2 (P2)** ✅: P2-A (yfinance fallback), P2-B (RAG auto-refresh), P2-C (concentration alerts), P2-D (superseded by P3-C)
- **Phase 3 (P3)** ✅: P3-A ✅ (position sizing), P3-B ✅ (correlation alerts), P3-C ✅ (Trendlyne — all pillars: P1 fundamentals fallback, P2 earnings calendar, P3 DVM filter, P5 insider sentiment, P6 insider institutional)
- **Phase 4 (P4)** ✅ COMPLETE (except P4-D): P4-A commentary grounding ✅; P4-B symbol cache persistence ✅ (was already built); P4-C governance numerical grounding ✅; P4-D Angel One options ⬜ (lowest priority, needs TOTP secret)
- **Dashboard items (DB-6→DB-10)** ✅ ALL DONE: DB-6 PerformanceTab (was already built); DB-7 live news panel; DB-8 holdings filter; DB-9 "What ran today?" ARIA button; DB-10 Excel export fallback
- **Phase 5 (P5)** ✅ ALL DONE: P5-A (outcome tracker + attribution) ✅; P5-B (paper portfolio) ✅; P5-C (outcome seeder) ✅; P5-D (forward poller — batch live prices 16:30 IST, alpha_live/return_live/days_live, t+30 milestone) ✅; P5-E (live attribution dashboard — LivePerformancePanel + upgraded AgentAttributionPanel) ✅. Migration `db/migrations/p5d_live_performance_columns.sql` ✅ applied in Supabase.
- **Phase 6 (P6)**: P6-A ✅ (confidence calibration + top/worst calls in PerformanceTab); P6-B ✅ (backtest panel — TRAIN/TEST/FULL split selector, monthly runs table); P6-C ✅ (morning/closing digest — `agents/market_digest.py`, worker 08:45/16:20 IST, `GET /api/market/digest`, `MarketDigestPanel` component, `market_digests` table); P6-D ✅ D-1/D-2/D-3/D-4 (BSE feeds, batch event classifier, temporal decay, FinBERT ensemble in `agents/sentiment.py`)
- **OPS-2** 🔄 Recurring: Weekly interface + DB audit every Sunday — routes vs dashboard, column names vs code, worker imports, yfinance/Supabase API patterns
- **Session 2026-07-22** ✅: ATR-14 stoploss (technical.py + synthesis prompt constraint); ARIA /analyse on-demand command (POST /api/analyse); weekly health audit (scripts/weekly_audit.py + Sunday 07:45 IST worker job); ARCHITECTURE.md white paper rewrite; FABLE5 architect guide; 30 new tests. LOG_LEVEL=INFO set on Railway worker. KAPPA_SUPPRESS lowered to 0.30 (prior session). Alpha_live backfill for forward poller entry_price fix (prior session).
- **Phase 7 (P7)** ⬜ PENDING (awaits Fable 5 API access): P7-A (Fable 5 synthesis); P7-B (Fable 5 lead judge); P7-C (Data Density Firewall); P7-D (Signal Independence — ATR stoploss done, promote technical entry levels); P7-E (Adversarial debate); P7-F (Partial data alerting). See `EXECUTION_PLAN.md` for full spec.

**Estimated additional monthly cost at full build:** ₹1,039–3,498/month (Quantsapp options feed + Trendlyne fundamentals backup + OpenAI GPT-4o-mini judges)
