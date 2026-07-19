# Bharat Intelligence — System Architecture

> High-level design document. Last updated: 2026-07-19.

---

## Overview

Multi-agent Indian stock/commodity market intelligence platform.
Produces daily BUY/HOLD/SELL/AVOID recommendations for a portfolio of ~24 symbols
plus proactively discovers new opportunities from the full NSE EQ universe (~1700 symbols).

---

## Component Map

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Railway (Backend)                            │
│                                                                     │
│  ┌─────────────────┐        ┌──────────────────────────────────┐   │
│  │   web service   │        │       worker service             │   │
│  │  uvicorn/FastAPI│        │   python worker.py (APScheduler) │   │
│  │  api/main.py    │        │   17 daily jobs (IST schedule)   │   │
│  │  30+ endpoints  │        │                                  │   │
│  └────────┬────────┘        └──────────────┬───────────────────┘   │
│           │                                │                       │
│           │              ┌─────────────────▼──────────────────┐    │
│           │              │   LangGraph Orchestrator Pipeline   │    │
│           │              │   scheduler/orchestrator.py         │    │
│           │              │                                     │    │
│           │              │  sector_pe_snapshot                 │    │
│           │              │   → load_symbols                    │    │
│           │              │   → load_weights                    │    │
│           │              │   → run_agents (10 agents, async)   │    │
│           │              │   → synthesise (Claude Sonnet 4.6)  │    │
│           │              │   → fact_check (Claude Haiku)       │    │
│           │              │   → save_recs                       │    │
│           │              │   → monitor                         │    │
│           │              │   → log_run                         │    │
│           │              │   → run_discovery                   │    │
│           │              └─────────────────────────────────────┘    │
│           │                                                         │
└───────────┼─────────────────────────────────────────────────────────┘
            │
            │  REST + WebSocket
            │
┌───────────▼──────────────┐       ┌───────────────────────────────┐
│  Vercel (Frontend)        │       │  Supabase (PostgreSQL)        │
│  React SPA (App.jsx)      │◄─────►│  16 tables                   │
│  ~3800 lines, single file │       │  pgvector for RAG             │
│                           │       │  RLS + service_role           │
│  Tabs:                    │       └───────────────────────────────┘
│  · Discovery              │
│  · Portfolio              │       ┌───────────────────────────────┐
│  · Performance            │       │  External Data Sources        │
│  · Governance/Research    │       │  · screener.in (fundamentals) │
│  · Market (digest/news)   │       │  · Trendlyne (F&O, analyst,   │
│                           │       │    DVM, earnings, insider)    │
│  ARIA Chat (Claude Haiku) │       │  · yfinance (prices, OHLCV)   │
│  via Vercel serverless fn │       │  · NSE/BSE APIs (FII/DII,     │
└───────────────────────────┘       │    announcements, indices)    │
                                    │  · Google News RSS (macro)    │
                                    │  · arXiv / HuggingFace (RAG)  │
                                    └───────────────────────────────┘
```

---

## The 10 Agents

| Agent | Domain | Key Output |
|---|---|---|
| `technical` | RSI, EMA, MACD, Bollinger, momentum | signal, score, ohlcv_last_date |
| `fundamental` | Screener.in / Trendlyne / yfinance valuation | signal, score, upside_pct, data_as_of |
| `sentiment` | News NLP + BSE filings + FinBERT ensemble | signal, score, event_type, decay_weight |
| `institutional` | FII/DII flows + promoter holding trend | signal, score, fii_net, dii_net |
| `macro` | RBI, inflation, INR, Google News macro | signal, score, sector_adjusted |
| `historical_rag` | pgvector similarity on 150 historical events | signal, score, matched_event |
| `sector_valuation` | Live sector PE vs 5-yr structural median | signal, regime, premium_pct |
| `commodities` | Gold, crude, silver MCX | signal, score |
| `warren_bot` | Buffett+Jhunjhunwala quality 0–100 | score, conviction_rating, DCF MOS% |
| `discovery_screener` | Full NSE EQ ~1700 universe, 200/day slice | is_discovery=true recs |

All agents extend a common `analyse(symbol) -> dict` pattern. None raises — always returns a result dict with an `error` key on failure.

---

## Synthesis Pipeline (per symbol)

```
agent_results (10 dicts)
    │
    ├─ composite_score = weighted sum of agent scores
    │   weights: loaded from agent_performance table (default 70.0 each)
    │
    ├─ Claude Sonnet 4.6 synthesis call
    │   system: semantic_layer.md  [cached, ~15KB, cache_control=ephemeral]
    │   user:   orchestrator_synthesis.txt template + per-symbol agent outputs
    │   output: JSON {action, confidence, entry_low/high, target, stoploss,
    │                  headline, bull_case, bear_case, synthesis, upside_pct, ...}
    │
    ├─ P7-C Data Density Firewall
    │   years_available < 5  → cap confidence at 60, inject DATA DENSITY WARNING
    │   data_quality=ESTIMATED → cap confidence at 65, inject ESTIMATED DATA note
    │
    ├─ Leakage Audit (governance/performance_tracker.py)
    │   technical ohlcv_last_date > signal_ts+1d → BLOCKING violation
    │   fundamental data_as_of > signal_ts → WARNING
    │
    ├─ Consensus Gate
    │   Requires ≥2 agents agreeing on BUY to prevent single-agent promotion
    │
    ├─ Earnings Guard
    │   earnings ≤3 days → cap confidence, inject CRITICAL binary event warning
    │
    └─ Synthesis Validator (3 LLM judges)
        Judge 1: GPT-4o-mini  (OpenAI — independent provider)
        Judge 2: Claude Sonnet 4.6
        Judge 3: Claude Opus 4.8
        5 rubrics: constraint_awareness, market_state_alignment,
                   data_provenance, logic_coherence, risk_disclosure
        kappa = quality × agreement per rubric; aggregate mean
        κ < 0.30 → SUPPRESSED (skip DB write)
        κ < 0.50 on critical dims → QUALIFIED (append caveat)
        else → PASS
```

---

## Data Flow

```
Daily 06:00 IST (orchestrator):
  NSE symbols (from portfolio_holdings) → 10 agents each → synthesis → fact_check → recommendations table

Daily 10:30 IST (discovery):
  NSE EQ universe 200-symbol slice → pre-screen (5 fast filters) → up to 25 full agent runs
  → discovery synthesis (3-rubric validator, κ≥0.35) → recommendations (is_discovery=true)

Daily 16:30 IST (forward poller):
  recommendation_outcomes PENDING rows → yfinance batch prices → alpha_live, return_live, days_live
  → t+30 milestone resolution when ≥30 days elapsed

Daily 18:30 IST (outcome tracker):
  recommendation_outcomes rows → price at t+90/180/365 → outcome_t90: HIT/MISS/PARTIAL
```

---

## Key Design Decisions

| Decision | Rationale |
|---|---|
| LangGraph for orchestration | Typed state machine, async node execution, clean error propagation |
| Synthesis via Claude, not rule-based | Multi-dimensional agent agreement is hard to codify; LLM synthesis handles contradictions |
| 3-judge kappa gate | Correlated Claude-only sampling risk; GPT-4o-mini as judge 1 breaks correlation |
| Prompt caching for semantic_layer | 15KB static doc × 24 symbols × daily = significant token cost without caching |
| Data density firewall (P7-C) | <5yr history → CAGR computed on 2-3 points → meaningless → cap confidence |
| Two symbol maps | `data/symbol_map.py` (agents) + `api/main.py _NSE_OVERRIDES` (API layer) — both must stay in sync |
| Supabase service_role | RLS bypass for backend writes; dashboard API uses same key server-side only |
| Forward polling at 16:30 IST | Market closes 15:30 IST; 16:30 allows NSE settlement and yfinance data propagation |

---

## Deployment

| Service | Platform | Start Command | Deploy Trigger |
|---|---|---|---|
| web | Railway | `uvicorn api.main:app --host 0.0.0.0 --port $PORT` | git push main |
| worker | Railway | `python worker.py` | git push main |
| frontend | Vercel | CRA build, root dir = `dashboard/` | git push main |
| ARIA serverless | Vercel | `dashboard/api/aria.js` | git push main |

---

## Current Limitations / Planned Improvements

| ID | Issue | Plan |
|---|---|---|
| P7-A | Synthesis uses Sonnet 4.6 (good, not great) | Upgrade to Fable 5 with adaptive thinking when available |
| P7-B | Opus 4.8 judge sometimes expensive | Fable 5 as lead validation judge |
| P7-D | Technical agent price targets not surfaced to synthesis | Promote as independent fallback alongside fundamental |
| P7-E | Single-pass synthesis | Two-pass adversarial debate (Devil's Advocate → Synthesis) |
| P4-D | Options data via Trendlyne (EOD only) | Angel One SmartAPI for real-time option chain |
| OPS-2 | Manual weekly audit | Automate route/column/import checks |
