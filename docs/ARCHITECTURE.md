# Bharat Intelligence — System Architecture & White Paper

> For technical readers: see the Component Map and Pipeline sections below.
> For business and finance readers: start with the Executive Summary and Agent Profiles.
> Last updated: 2026-07-19

---

## Executive Summary

Indian equity markets produce an overwhelming volume of information every trading day — earnings releases, regulatory filings, FII flow data, commodity price moves, monetary policy signals, and thousands of price charts. A skilled research analyst at a top institution might synthesise all of this for perhaps 15–20 stocks. **Bharat Intelligence does it for an entire universe of ~1,700 listed companies, every single day, before the market opens.**

The system is built around ten specialised AI agents, each modelled on a distinct discipline of professional equity research. One agent thinks like a technical chartist, reading price momentum and volume patterns. Another thinks like a fundamental analyst, scrutinising balance sheets, return on capital, and valuation multiples. A third reads the news the way a seasoned trader does — looking not just at sentiment, but at *which* events move markets and *how quickly* their impact decays. A macro economist agent monitors RBI policy, inflation trends, and currency moves. An institutional flow analyst tracks where the big money — foreign institutional investors and domestic funds — is actually going.

These agents do not simply vote and take an average. Their outputs are fed into a **synthesis engine** — a large language model (Claude Sonnet) that has been given the same context a senior fund manager would have: knowledge of Indian market structure, circuit breaker rules, SEBI regulations, position sizing conventions, and sector-specific risk frameworks. The synthesis engine reads all ten agent reports and produces a single, reasoned recommendation: **BUY, HOLD, SELL, or AVOID** — with a specific entry range, price target, stoploss, and a plain-English rationale.

Before any recommendation reaches the database, it passes through a **three-judge validation panel**: an independent GPT-4o-mini model, a Claude Sonnet model, and a Claude Opus model score the synthesis across five rubrics — factual grounding, market-state alignment, logic coherence, risk disclosure, and constraint awareness. Agreement between judges is measured using **Cohen's Kappa**, a statistical measure of inter-rater reliability used in academic peer review. Only recommendations where the judges substantially agree are published. Those where judges disagree — usually because the underlying data is thin or contradictory — are suppressed rather than guessed at.

The system also runs a **daily discovery engine** that proactively screens the full NSE equity universe, identifying stocks with strong risk-reward setups before they appear on mainstream research radars. Separately, a **portfolio monitor** watches open holdings in real time and fires alerts when stoploss levels are threatened or macro conditions shift against a position.

Recommendations are tracked forward in time: at 30, 90, 180, and 365 days, the system resolves whether each call was a HIT or MISS and computes the **alpha generated** (return above Nifty 50). This closed feedback loop allows the system to continuously measure which agents are adding genuine value and which are producing noise.

The result is a platform that combines the breadth of a quantitative screener, the depth of fundamental research, and the judgment of a senior analyst — running at a fraction of the cost of a traditional research desk.

---

## The Ten Agents — Profiles

---

### 1. Technical Analysis Agent

**What it does:**
The technical agent analyses price and volume history to assess a stock's current momentum, trend strength, and whether it is trading at an attractive entry point relative to its recent range. It does not predict the future — it describes the current supply/demand balance as expressed through market prices.

**Methods used:**

- **Relative Strength Index (RSI):** A momentum oscillator developed by J. Welles Wilder, RSI measures the speed and magnitude of price moves on a 0–100 scale. Readings below 30 signal potential oversold conditions (buyers may step in); above 70 signals overbought (sellers likely to dominate). The agent targets RSI in the 40–65 range for new entries — strong enough to confirm trend, not so extended that upside is exhausted.

- **Exponential Moving Averages (EMA):** Price above the 200-day EMA confirms the stock is in a long-term uptrend. The 20/50-day EMA crossover identifies medium-term momentum shifts. We weight EMAs more heavily than simple moving averages because they respond faster to recent price action — important in Indian markets where sentiment can shift sharply on a single macro event.

- **MACD (Moving Average Convergence Divergence):** Compares short-term and long-term EMAs to identify trend acceleration and divergence. A bullish MACD crossover combined with rising RSI is one of the strongest momentum confirmation signals in the system.

- **Bollinger Bands:** Two standard deviations around a 20-day moving average. Price touching the lower band in an uptrend often represents a mean-reversion entry; a breakout above the upper band signals genuine momentum expansion.

- **Volume confirmation:** Price moves on high volume are weighted more heavily than those on low volume — a fundamental principle of Dow Theory.

**Why it matters:** No valuation story survives a technical breakdown. A stock trading below all major moving averages on falling volume is not "cheap" — it is in distribution. The technical agent ensures the system does not buy into falling knives.

---

### 2. Fundamental Analysis Agent

**What it does:**
The fundamental agent is the backbone of the system's valuation work. It assesses whether a stock is trading below its intrinsic value by examining the quality of the business, its financial health, and what a rational acquirer would pay for it.

**Methods used:**

- **Return on Capital Employed (ROCE):** The gold standard metric in Indian equity research. ROCE measures how efficiently a business converts capital into profit. A company sustaining ROCE above its cost of capital over multiple years is creating genuine economic value. The agent compares ROCE against sector benchmarks and looks for consistency — a single year of high ROCE is noise; ten years is a moat signal.

- **Revenue and earnings CAGR:** Compound annual growth rates over 3, 5, and 10 years establish whether a business is genuinely growing or benefiting from temporary tailwinds. The agent flags when recent growth diverges significantly from long-term trend — either as a quality concern (deceleration) or opportunity (reacceleration).

- **P/E relative to sector:** Rather than applying a flat P/E threshold, the agent uses a sector-relative framework (described in the Discovery Screener section). A bank trading at 12× earnings is expensive relative to peers; an IT company at 12× is a screaming buy.

- **Discounted Cash Flow (DCF):** The agent builds a three-stage DCF model — explicit growth phase (5 years), fade phase (5 years), and terminal value — using sector-appropriate discount rates (WACC) ranging from 10% (FMCG) to 15% (aviation). The margin of safety is computed as the discount to intrinsic value. This feeds directly into the Warren Bot agent's conviction scoring.

- **Screener.in + Trendlyne data pipeline:** The agent pulls 10 years of annual financial data — revenue, operating profit, depreciation, capital expenditure, ROCE, EPS — with a three-tier fallback (screener.in → Trendlyne → yfinance). If fewer than 5 years of data are available, the system caps recommendation confidence at 60% and flags it explicitly.

**Why it matters:** Price is what you pay; value is what you get. Every BUY recommendation in the system must have a positive margin of safety — the stock must be trading below what the business is worth.

---

### 3. Sentiment Analysis Agent

**What it does:**
The sentiment agent reads the market's emotional temperature: not just whether news is positive or negative, but *what kind* of event is driving sentiment and *how long* that sentiment impact will last. Different events have very different half-lives.

**Methods used:**

- **Event taxonomy and multipliers (Janus-Q classifier):** The agent classifies every news headline into one of eight event types — EARNINGS_SURPRISE, REGULATORY_SHOCK, M&A_SIGNAL, MACRO_CATALYST, ANALYST_ACTION, MANAGEMENT_SIGNAL, SECTOR_CATALYST, or ROUTINE. Each type carries a different impact multiplier. An EARNINGS_SURPRISE carries 3× the weight of a ROUTINE update. A REGULATORY_SHOCK is scored negatively regardless of framing. This taxonomy was developed by observing which event types actually moved NSE stocks in historical data.

- **Temporal decay weighting:** A news event from 36 hours ago matters far less than one from 2 hours ago. The agent applies exponential decay to each headline based on its event type — EARNINGS events decay slowly (48-hour half-life), while intraday ANALYST_ACTION events decay quickly (2-hour half-life). This is modelled on the same decay function used in fixed-income credit spread analysis.

- **FinBERT ensemble:** The top 5 most time-weighted headlines are scored by FinBERT, a transformer model pre-trained specifically on financial text (earnings calls, analyst reports, news). The final sentiment score is a weighted ensemble: 60% FinBERT (domain accuracy) + 40% Claude Haiku (contextual reasoning). This ensemble approach reduces both the "overfitting to financial jargon" risk of pure FinBERT and the "general language model misreading" risk of a pure LLM.

- **BSE corporate filings feed:** The agent ingests real-time BSE announcement data — quarterly results, board decisions, promoter activity disclosures — before they appear in general news aggregators. These are often the highest-signal inputs in the system.

**Why it matters:** Markets move on narrative as much as numbers. A technically strong, fundamentally cheap stock can remain cheap for years if sentiment is negative. The sentiment agent helps the system time entries around sentiment inflection points.

---

### 4. Institutional Flow Agent

**What it does:**
This agent answers a simple but powerful question: are the sophisticated, well-resourced market participants — Foreign Institutional Investors (FIIs) and Domestic Institutional Investors (DIIs) — buying or selling?

**Methods used:**

- **FII/DII net flow analysis:** Daily NSE data on FII and DII buy/sell activity is tracked and trended. The key insight is that FII flows are a leading indicator of large-cap index direction, while DII flows (mutual funds, insurance companies) provide a domestic demand floor. Sustained FII buying over 10+ trading days signals structural allocation, not just tactical positioning.

- **Institutional ownership percentage:** At the stock level, the agent checks the percentage of the company owned by institutional investors (sourced from shareholding pattern disclosures). Rising institutional ownership over multiple quarters is a strong quality signal — institutions do their homework.

- **Promoter holding trend (insider signal):** The agent tracks promoter holding percentage changes over 1 and 3 years. A promoter *increasing* their stake is the strongest possible insider signal — they are betting their personal wealth on the company's future. A promoter *decreasing* stake, especially combined with FII selling, is a serious red flag. This signal adds 8 points to the institutional score for ACCUMULATING promoters and penalises DISTRIBUTING ones.

- **DII absorption ratio:** When FIIs are selling but DIIs are absorbing (buying), the net market impact is cushioned. The ratio of DII buying to FII selling predicts whether institutional selling will cascade into broader market weakness.

**Why it matters:** Retail investors can rarely compete with institutional research depth. Rather than fighting the information asymmetry, this agent tracks where institutional capital is flowing and aligns with it.

---

### 5. Macro & Market Environment Agent

**What it does:**
No stock exists in isolation from its macroeconomic environment. This agent assesses whether the broader economic tide is rising or falling, and adjusts the macro component of every recommendation accordingly.

**Methods used:**

- **RBI monetary policy signals:** Interest rate direction is the single most important macro variable for Indian equities. Rising rates compress valuations (especially for growth stocks and NBFCs); falling rates expand them. The agent monitors RBI MPC decisions, repo rate trajectory, and liquidity operations (OMOs, VRRs).

- **Inflation regime analysis:** The WPI/CPI spread matters enormously for margin-sensitive businesses. Wide spreads (raw material inflation outpacing retail price inflation) compress manufacturer margins. The agent tracks both headline and core inflation trends.

- **INR/USD and INR/EUR dynamics:** Currency weakness is a direct headwind for import-dependent businesses (crude oil, electronics, pharmaceuticals raw materials) and a tailwind for IT exporters. The agent computes sector-specific currency sensitivity.

- **Sector-adjusted macro scoring:** The macro score is not identical for all stocks. A falling INR scores negative for a refinery but positive for an IT exporter. The agent applies sector-specific adjustments of ±8 points based on each sector's empirical sensitivity to each macro variable.

- **Google News RSS macro monitor:** Beyond structured data, the agent scans Indian financial news for language patterns associated with macro shocks — budget announcements, geopolitical developments, global risk-off signals. This catches developing situations before they appear in structured data feeds.

**Why it matters:** Even the best stock picks underperform in a macro headwind environment. The macro agent ensures the system does not recommend aggressive buying when the broader environment is deteriorating.

---

### 6. Historical Pattern Recognition Agent (RAG)

**What it does:**
This agent answers: *"Have we seen a situation like this before, and what happened?"* It searches a curated database of 150 significant Indian market events — rate hike cycles, commodity super-cycles, regulatory crises, FII panic episodes — and finds the most similar historical analogue for the current setup.

**Methods used:**

- **Retrieval-Augmented Generation (RAG):** The agent converts the current market situation into a mathematical vector (a list of numbers representing semantic meaning) using OpenAI's text embedding model. It then searches the historical events database using **pgvector cosine similarity** — finding the past scenario whose vector is closest to the current one.

- **Semantic similarity, not keyword matching:** Traditional systems search for keywords ("RBI rate hike"). The RAG approach finds conceptually similar situations even when described differently — "RBI withdrawing accommodation" in 2022 correctly matches the 1995 credit tightening cycle without needing identical terminology.

- **Outcome-weighted scoring:** Each historical event in the database includes what actually happened to the market in the 30, 90, and 180 days following the event. The agent uses these outcomes to inform its signal — a current situation that resembles a historical rally setup scores positive; one that resembles a historical crisis scores negative.

- **Monthly auto-refresh:** The historical database is automatically updated monthly with new events from the past 35 days, ensuring the corpus stays current.

**Why it matters:** Markets are not random — they rhyme. Experienced investors draw on pattern recognition built over decades. The RAG agent gives the system the equivalent of a 7-year institutional memory.

---

### 7. Sector Valuation Agent

**What it does:**
This agent answers whether an entire sector is cheap or expensive relative to history — context that changes the interpretation of an individual stock's valuation significantly.

**Methods used:**

- **Live sector PE from NSE indices:** Every day, the agent pulls real-time PE ratios for all NSE sector indices (Bank Nifty, Nifty IT, Nifty FMCG, Nifty Pharma, etc.) via the NSE allIndices API.

- **5-year structural median comparison:** Each live sector PE is compared against its 5-year structural median (December 2019–December 2024). The premium or discount to this long-run median determines the **regime**: CHEAP (>15% below median), FAIR, ELEVATED (>15% above), or EXPENSIVE (>35% above).

- **Rolling live computation:** As the system accumulates daily sector PE snapshots in its database, it automatically switches to using a rolling 365-day median (once 90+ data points accumulate, approximately 3 months). This makes the regime classification increasingly robust over time.

**Why it matters:** A stock trading at 25× earnings in a sector where the median is 18× is expensive. The same stock in a sector where the median is 30× is a bargain. Absolute valuation without sector context produces systematic mispricing. The sector valuation agent corrects for this.

---

### 8. Commodities Agent

**What it does:**
For an economy as commodity-sensitive as India's, raw material price cycles are a primary driver of corporate profitability and market direction. This agent monitors the three commodities with the broadest impact on Indian equities.

**Methods used:**

- **Gold (MCX):** Gold is both a global risk-off barometer and a direct driver of sentiment for India's jewellery sector and gold finance companies (Muthoot, Manappuram). Rising gold prices signal global risk aversion and support rural spending in gold-holding households.

- **Crude Oil (MCX):** India imports approximately 85% of its crude oil requirement. Rising crude is stagflationary — it widens the current account deficit, weakens the INR, raises transport and manufacturing costs, and pressures fuel subsidy budgets. The crude signal is the single most impactful commodity variable for the broad Indian market.

- **Silver (MCX):** Silver tracks both precious metal demand and industrial consumption (electronics, solar panels). In the Indian context, silver price direction is a useful leading indicator for the capital goods and renewable energy sectors.

The agent computes momentum signals (price relative to 20/50/200-day averages) and trend direction for each commodity, then translates these into sector-specific implications.

**Why it matters:** Many fund managers underestimate commodity pass-through in Indian supply chains. A ₹10 move in crude oil translates into margin compression across aviation, paints, tyres, chemicals, and logistics — sectors that together represent a significant portion of Nifty earnings.

---

### 9. Warren Bot — Long-Term Quality Agent

**What it does:**
Warren Bot applies the investment philosophies of Warren Buffett and Rakesh Jhunjhunwala specifically to the Indian market context. It asks the question every long-term investor should ask before committing capital: *Is this a great business, or just a cheap stock?*

**Methods used:**

- **Moat strength scoring:** Economic moats — sustainable competitive advantages — are assessed across five dimensions: consistent ROCE (return on capital employed), operating margin stability, revenue growth trajectory, promoter holding trend, and brand/sector positioning. A business that has sustained ROCE above 20% for 10 consecutive years almost certainly has a structural competitive advantage.

- **Management quality assessment:** Capital allocation discipline is measured through dividend payout ratios, capex efficiency, and the absence of excessive promoter pledging. Promoter pledge ratios above 40% are an automatic disqualifier — it signals financial stress at the promoter level that often precedes adverse corporate events.

- **Three-stage DCF valuation:** Owner earnings (net profit + depreciation − 60% of capital expenditure, the maintenance component) are discounted using a 5-year explicit growth phase, 5-year fade to terminal growth, and a sector-appropriate WACC. The 60% maintenance capex assumption follows Buffett's owner earnings framework — growth capex is optional; maintenance capex is obligatory.

- **Jhunjhunwala India Lens:** Bonus scoring for characteristics Jhunjhunwala identified as structural Indian growth plays: consumption exposure (FMCG, retail, financials), large underpenetrated addressable markets, and cyclical trough positioning (P/E below 10× in a temporarily depressed sector).

- **Hard disqualifiers:** Any of the following automatically triggers an AVOID signal: fewer than 5 years of financial history, market capitalisation below ₹500 crore, promoter pledging above 40%, or losses in 3 or more of the last 5 years.

**Why it matters:** The market rewards great businesses held through volatility, not clever trading of mediocre ones. Warren Bot is the system's filter for long-term investment quality — it ensures the system does not mistake a cheap valuation for a good business.

---

### 10. Discovery Screener

**What it does:**
While the other nine agents analyse a fixed portfolio of ~24 stocks, the Discovery Screener proactively hunts the entire NSE equity universe — approximately 1,700 listed companies — to find opportunities before they appear on mainstream research radars.

**Methods used:**

- **Rotating daily slice:** Analysing 1,700 stocks every day would be computationally expensive and redundant (most stocks don't change materially day to day). The screener uses a deterministic daily rotation — 200 stocks per day, full universe covered every ~9 days. The rotation is seeded so the same stocks aren't always analysed on the same day of the week.

- **Five-stage pre-screen (fast filters):** Each of the 200 stocks passes through five quick quantitative filters before expensive AI analysis is triggered: RSI in the 40–65 range (momentum without overextension), sector-relative PE within acceptable bounds, institutional ownership above 5% (quality signal), revenue growth above 15%, and price above the 200-day EMA (confirmed uptrend). Stocks failing any filter are skipped.

- **Sector-relative PE tiering:** Rather than a flat PE threshold, the screener uses a three-tier framework:
  - *Tier A:* PE ≤ sector median → clearly undervalued vs peers
  - *Tier B:* PE ≤ sector median × 1.2 and below 80× → fair value vs peers
  - *Tier C:* PE ≤ sector median × 2.0 and revenue growth above 30% → growth premium justified
  - *Hard cap:* PE above 80× fails automatically regardless of growth

- **Full 7-agent deep analysis on up to 25 pre-screened stocks:** Stocks passing the pre-screen receive the same full agent analysis as portfolio holdings. Discovery recommendations are classified as CRITICAL (upside ≥ 40%, confidence ≥ 75%) or STANDARD (upside ≥ 20%, confidence ≥ 65%).

- **Trendlyne DVM score integration:** Each stock is also scored on Trendlyne's proprietary Durability-Valuation-Momentum composite, providing a third-party quantitative cross-check.

**Why it matters:** The best investment opportunities are almost always outside the current portfolio. The Discovery Screener is the system's proactive edge — it surfaces stocks with strong fundamentals and technical setups before institutional consensus develops, potentially providing earlier entry at better prices.

---

## Synthesis Pipeline — How the Agents Work Together

After all ten agents report, the orchestrator follows this sequence for every symbol:

```
10 agent outputs
    │
    ├─ Composite score: weighted sum of agent scores
    │   (weights updated daily based on 90-day outcome accuracy)
    │
    ├─ Claude Sonnet synthesis: reads all 10 agent reports,
    │   resolves contradictions, produces JSON recommendation
    │   {action, confidence, entry, target, stoploss, rationale}
    │
    ├─ Data Density Firewall
    │   < 5 years of financial history → cap confidence at 60%
    │   Data estimated rather than reported → cap at 65%
    │
    ├─ Temporal Leakage Audit
    │   Verifies no future data contaminated the analysis
    │
    ├─ Consensus Gate
    │   Requires ≥ 2 agents agreeing on BUY (prevents single-agent promotion)
    │
    ├─ Earnings Guard
    │   Earnings within 3 days → cap confidence, flag binary event risk
    │
    └─ Three-Judge Validation (Cohen's Kappa gate)
        Judge 1: GPT-4o-mini (OpenAI — independent provider)
        Judge 2: Claude Sonnet (reasoning check)
        Judge 3: Claude Opus (senior review)
        5 rubrics scored 1–5 each
        Kappa < 0.30 → recommendation SUPPRESSED (suppressed > stored)
        Kappa < 0.50 on critical dimensions → QUALIFIED (caveat appended)
        Kappa ≥ 0.50 → PASS → written to database
```

**Why three independent judges from two different AI providers?** If all three judges were Claude models, they would tend to agree with each other simply because they share the same training. Using GPT-4o-mini as the first judge breaks this correlation — it must independently agree for a recommendation to pass. This is the same logic behind using diverse rating agencies or independent audit firms.

---

## Data Flow

```
05:30 IST  Market Digest MORNING — overnight news + global cues
06:00 IST  Orchestrator — 10 agents × 24 portfolio stocks → synthesis → validated recs
06:30 IST  Regime Detector — market regime classification (BULL/BEAR/SIDEWAYS/VOLATILE)
10:30 IST  Discovery Screener — 200-stock NSE slice → up to 25 deep analyses
15:45 IST  Options Snapshot — PCR, max pain, IV skew from Trendlyne F&O
16:00 IST  Portfolio Risk — concentration and correlation alerts
16:15 IST  Paper Portfolio — price refresh, exit checks, daily P&L snapshot
16:20 IST  Market Digest CLOSING — end-of-day market recap
16:30 IST  Forward Poller — updates live P&L on all open recommendations
17:00 IST  Target Updater — ratchets stoplosses, extends targets on winners
18:30 IST  Outcome Tracker — resolves t+90/180/365 milestones (HIT/MISS/PARTIAL)
07:45 IST (Sunday)  Weekly Audit — system health: kappa quality, data freshness, poller recency
```

---

## Technical Architecture (for engineering readers)

### Component Map

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Railway (Backend)                            │
│                                                                     │
│  ┌─────────────────┐        ┌──────────────────────────────────┐   │
│  │   web service   │        │       worker service             │   │
│  │  uvicorn/FastAPI│        │   python worker.py (APScheduler) │   │
│  │  api/main.py    │        │   20 daily jobs (IST schedule)   │   │
│  │  30+ endpoints  │        │                                  │   │
│  └────────┬────────┘        └──────────────┬───────────────────┘   │
│           │                                │                       │
│           │              ┌─────────────────▼──────────────────┐    │
│           │              │   LangGraph Orchestrator Pipeline   │    │
│           │              │   scheduler/orchestrator.py         │    │
│           │              └─────────────────────────────────────┘    │
└───────────┼─────────────────────────────────────────────────────────┘
            │ REST + WebSocket
┌───────────▼──────────────┐       ┌───────────────────────────────┐
│  Vercel (Frontend)        │       │  Supabase (PostgreSQL)        │
│  React SPA (App.jsx)      │       │  16 tables                   │
│  ARIA Chat (Claude Haiku) │       │  pgvector for RAG             │
└───────────────────────────┘       └───────────────────────────────┘
```

### Supabase Database Schema

| Table | Purpose |
|---|---|
| `recommendations` | Agent-generated buy/sell recs with full agent signals and validation data |
| `portfolio_holdings` | User's open positions with live price refresh |
| `portfolio_alerts` | Risk/danger alerts (stoploss proximity, sector concentration) |
| `agent_performance` | Daily agent accuracy log (accuracy_90d, hallucination_rate, trend) |
| `historical_events` | RAG knowledge base (150 events with pgvector embeddings) |
| `institutional_flows` | FII/DII daily data |
| `daily_runs` | Scheduler run log with status and error counts |
| `research_proposals` | AI paper proposals from the governance agent |
| `sector_pe_snapshots` | Daily sector PE readings |
| `recommendation_outcomes` | Forward outcome tracking (alpha at t+30/90/180/365) |
| `market_digests` | Morning and closing market briefs |
| `paper_portfolio_positions` | Simulated paper trade log for performance validation |
| `paper_portfolio_snapshots` | Daily paper P&L vs Nifty 50 benchmark |

### API Endpoints (key routes)

| Endpoint | Purpose |
|---|---|
| `GET /api/recommendations` | Latest recs sorted by upside %, critical first |
| `GET /api/discovery` | Today's discovery recs with live price refresh |
| `GET /api/portfolio` | Open holdings with live prices |
| `GET /api/performance/live` | Live P&L on all open recommendations |
| `GET /api/warren_bot/{symbol}` | On-demand quality score (24h cache) |
| `GET /api/market/digest` | Morning/closing market briefs |
| `GET /api/attribution/agents` | Per-agent hit rate and alpha |
| `GET /api/market/pulse` | Live Nifty, Sensex, Gold, Crude, VIX (60s cache) |

### Key Design Decisions

| Decision | Rationale |
|---|---|
| LangGraph for orchestration | Typed state machine with async node execution; clean error isolation between agents |
| 3 judges from 2 providers | Breaks correlated sampling; GPT-4o-mini independence is essential for honest kappa |
| Prompt caching for semantic layer | 15KB context × 24 symbols × daily = significant token cost without caching |
| Data Density Firewall | < 5yr history means CAGR computed on 2–3 points → statistically meaningless → cap confidence |
| Sector-relative PE, not flat threshold | A 25× PE bank is expensive; a 25× IT company is cheap. Absolute PE is not comparable across sectors |
| Forward outcome tracking | Without measuring actual outcomes, the system has no way to know if it is adding value |
| Paper portfolio simulation | Validates recommendations against a simulated live portfolio before real capital is committed |

---

## Current Limitations and Roadmap

| ID | Limitation | Planned Resolution |
|---|---|---|
| P7-A | Synthesis uses Sonnet 4.6 | Upgrade to Fable 5 with adaptive thinking when available — see `docs/FABLE5_REDESIGN_PROMPT.md` |
| P7-D | Technical agent price targets not surfaced to synthesis | Promote as independent fallback alongside fundamental DCF |
| P7-E | Single-pass synthesis | Two-pass adversarial debate (Devil's Advocate → Final Synthesis) |
| P4-D | Options data via Trendlyne (end-of-day only) | Angel One SmartAPI for real-time intraday option chain |
| OPS-2 | Manual quarterly architectural review | Fable 5 architectural review — see `docs/FABLE5_REDESIGN_PROMPT.md` Part A |
