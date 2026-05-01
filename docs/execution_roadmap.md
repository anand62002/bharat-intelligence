# Bharat Intelligence — Gap Closure Execution Roadmap

> Source: Professional system audit (May 2026).  
> This document defines 12 sequential build tasks, each with a self-contained  
> Claude Code prompt, required setup/integrations, and acceptance criteria.  
> Execute one task at a time. Mark status as you go.

---

## Execution Order & Status Tracker

| # | Gap | Priority | Effort | Status |
|---|-----|----------|--------|--------|
| 1 | Backtesting Outcome Tracker | **Critical** | M | ✅ |
| 2 | Regime Detection Engine | **Critical** | M | ⬜ |
| 3 | Earnings Calendar Integration | High | S | ⬜ |
| 4 | Impact Cost Model | High | S | ⬜ |
| 5 | Forward Earnings Estimates | High | L | ⬜ |
| 6 | Portfolio-Level Risk Framework | High | L | ⬜ |
| 7 | Volume Profile Analysis | Medium | M | ⬜ |
| 8 | Management Quality Scoring | Medium | M | ⬜ |
| 9 | Corporate Governance Red Flags | Medium | M | ⬜ |
| 10 | Options Market Signals | Medium | L | ⬜ |
| 11 | Historical RAG Enrichment | Medium | M | ⬜ |
| 12 | Valuation Sensitivity Analysis | Low-Medium | M | ⬜ |

**Effort key:** S = 1 session · M = 2–3 sessions · L = 3–5 sessions

---

---

## Gap 1 — Backtesting Outcome Tracker

**Why first:** Every other gap improves signal generation. Without an outcome tracker you cannot measure whether any improvement actually worked. Build this before everything else so that from today, every recommendation generates a measurable track record.

**What it does:**  
Daily job that finds recommendations that are now 90, 180, or 365 days old, fetches the current price and the contemporaneous NIFTY 50 return, computes absolute return and alpha (excess return vs benchmark), and writes to a `recommendation_outcomes` table. The dashboard shows a live accuracy scorecard.

### Setup & Integrations Needed

**Supabase migration** — run in SQL Editor before building:
```sql
CREATE TABLE IF NOT EXISTS recommendation_outcomes (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rec_id              UUID REFERENCES recommendations(id) ON DELETE CASCADE,
    symbol              TEXT NOT NULL,
    action              TEXT NOT NULL,          -- BUY/SELL/HOLD/AVOID
    entry_price         NUMERIC,                -- price at recommendation date
    rec_date            DATE NOT NULL,
    -- Horizons
    price_t90           NUMERIC,
    nifty_t90           NUMERIC,
    alpha_t90           NUMERIC,                -- (price_t90/entry_price - 1) - (nifty_t90/nifty_entry - 1)
    outcome_t90         TEXT,                   -- HIT / MISS / PARTIAL / PENDING
    price_t180          NUMERIC,
    nifty_t180          NUMERIC,
    alpha_t180          NUMERIC,
    outcome_t180        TEXT,
    price_t365          NUMERIC,
    nifty_t365          NUMERIC,
    alpha_t365          NUMERIC,
    outcome_t365        TEXT,
    -- Context
    nifty_entry         NUMERIC,                -- NIFTY 50 price on rec_date
    composite_score     NUMERIC,
    agent_signals       JSONB,
    validation_kappa    NUMERIC,
    last_updated        TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX ON recommendation_outcomes (symbol);
CREATE INDEX ON recommendation_outcomes (rec_date);
CREATE INDEX ON recommendation_outcomes (outcome_t90);

-- Agent-level accuracy view
CREATE OR REPLACE VIEW agent_accuracy AS
SELECT
    action,
    COUNT(*) FILTER (WHERE outcome_t90 = 'HIT')  AS hits_t90,
    COUNT(*) FILTER (WHERE outcome_t90 IS NOT NULL AND outcome_t90 != 'PENDING') AS total_t90,
    ROUND(AVG(alpha_t90) * 100, 2)               AS avg_alpha_t90_pct,
    ROUND(AVG(alpha_t180) * 100, 2)              AS avg_alpha_t180_pct
FROM recommendation_outcomes
GROUP BY action;

GRANT ALL ON recommendation_outcomes TO service_role;
GRANT ALL ON agent_accuracy TO service_role;
```

**New env vars:** None — uses existing SUPABASE credentials and yfinance.

**worker.py** — add daily 6:30 PM IST job after markets close.

### Claude Code Prompt

```
Build a recommendation outcome tracking system for the Bharat Intelligence platform.

CONTEXT:
- Repo root: Stock analysis/
- Existing Supabase tables: recommendations, portfolio_holdings, agent_performance
- recommendations table has: id, symbol, action, entry_low, entry_high, target, confidence, created_at, agent_signals
- yfinance is available for price fetching (TICKER.NS format)
- worker.py runs scheduled jobs via APScheduler
- CLAUDE.md has full architecture context

BUILD:

1. `agents/outcome_tracker.py`
   - Entry point: `run_outcome_tracking(dry_run=False) -> dict`
   - Fetch all recommendations from Supabase that are 90, 180, or 365 days old (±3 day window to handle weekends)
   - For each, fetch current price via yfinance and NIFTY 50 price (^NSEI) at the same date
   - Also fetch the NIFTY 50 price on the original rec_date to compute benchmark return
   - Compute: abs_return = (current_price / entry_price) - 1
   - Compute: nifty_return = (nifty_current / nifty_entry) - 1
   - Compute: alpha = abs_return - nifty_return
   - Determine outcome: for BUY recs, HIT = alpha > 0 AND abs_return > 0; MISS = abs_return < -10%; PARTIAL = otherwise
   - For SELL/AVOID recs, HIT = abs_return < -5% (recommendation was correct)
   - Upsert to recommendation_outcomes table
   - Return summary: {tracked: N, hits: N, misses: N, avg_alpha_90d: float}

2. `api/main.py` additions:
   - GET /api/performance/outcomes — returns last 90 days of outcome records, grouped by action
   - GET /api/performance/accuracy — returns accuracy scorecard: hit rate by action, average alpha by horizon
   - GET /api/performance/alpha_chart — returns time-series of weekly average alpha for charting

3. `dashboard/src/App.jsx` additions:
   - New "Performance" tab in the main navigation
   - Accuracy scorecard: hit rate % and average alpha for BUY/HOLD/SELL/AVOID at each horizon
   - Alpha chart: rolling 90-day average alpha as a line chart
   - Recent outcomes table: last 20 resolved recommendations with outcome badge (HIT green / MISS red / PARTIAL yellow)

4. `worker.py`:
   - Add job: run_outcome_tracking() at 18:30 IST daily (after NSE close)

5. `scheduler/orchestrator.py`:
   - In save_recs_node, when saving a recommendation, also create a PENDING row in recommendation_outcomes with entry_price, nifty_entry, rec_date, agent_signals

ACCEPTANCE CRITERIA:
- outcome_tracker can be run standalone: `python -m agents.outcome_tracker`
- Dry run prints what would be updated without writing to DB
- All 3 horizons (90/180/365d) tracked in a single row per recommendation
- Dashboard performance tab shows when no data yet (EmptyState component)
- Test: `tests/test_outcome_tracker.py` with mocked yfinance and Supabase
```

---

---

## Gap 2 — Regime Detection Engine

**Why second:** Market regime (bull/bear/sideways/high-volatility) is the single biggest determinant of which signals work. RSI oversold in a bear market is a death trap; in a bull market it is a gift. The regime label should condition every other agent's output weight in the composite score. Build this before improving individual agents.

**What it does:**  
Daily agent that classifies the current Indian equity market regime using five independent indicators, assigns a composite regime label, and stores it in Supabase. The orchestrator reads the current regime and uses it to re-weight agent scores (e.g., in HIGH_VOLATILITY regime, macro and institutional agents get 2× weight, technical gets 0.5×).

### Setup & Integrations Needed

**Supabase migration:**
```sql
CREATE TABLE IF NOT EXISTS market_regime (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    regime_date     DATE UNIQUE NOT NULL,
    regime          TEXT NOT NULL,   -- BULL / BEAR / SIDEWAYS / HIGH_VOLATILITY
    confidence      NUMERIC,         -- 0-100
    nifty_trend     TEXT,            -- UPTREND / DOWNTREND / SIDEWAYS
    vix_state       TEXT,            -- CALM / NORMAL / ELEVATED / STRESSED
    fii_trend       TEXT,            -- NET_BUYER / NET_SELLER / NEUTRAL (7-day rolling)
    breadth_state   TEXT,            -- BROAD_ADVANCE / BROAD_DECLINE / MIXED
    momentum_state  TEXT,            -- OVERBOUGHT / NEUTRAL / OVERSOLD
    raw_signals     JSONB,
    created_at      TIMESTAMPTZ DEFAULT now()
);
GRANT ALL ON market_regime TO service_role;
```

**Data sources (all free):**
- NIFTY 50 price history: `^NSEI` via yfinance
- India VIX: `^INDIAVIX` via yfinance  
- NIFTY 500 for breadth: `^CNXMIDCAP`, `^CNXSMALLCAP` vs `^NSEI`
- FII flow: existing `institutional_flows` Supabase table

### Claude Code Prompt

```
Build a market regime detection engine for the Bharat Intelligence platform.

CONTEXT:
- Repo: Stock analysis/ (see CLAUDE.md for full architecture)
- New Supabase table `market_regime` already created (see schema above)
- yfinance available for ^NSEI, ^INDIAVIX price history
- institutional_flows Supabase table has fii_net column with daily FII data
- agents/macro.py and agents/technical.py exist as reference patterns

BUILD:

1. `agents/regime_detector.py`
   Entry point: `detect_regime(run_date=None) -> dict`

   Five independent indicators (each scored separately):

   a. NIFTY TREND (price vs EMA-50 vs EMA-200):
      - Price > EMA-50 > EMA-200: UPTREND (bullish)
      - Price < EMA-50 < EMA-200: DOWNTREND (bearish)
      - Mixed: SIDEWAYS

   b. VIX STATE (India VIX current level):
      - VIX < 13: CALM
      - 13–18: NORMAL
      - 18–25: ELEVATED
      - > 25: STRESSED

   c. FII 10-DAY TREND (rolling net FII flow from institutional_flows table):
      - 10-day cumulative > +5000 Cr: NET_BUYER
      - 10-day cumulative < -5000 Cr: NET_SELLER
      - Otherwise: NEUTRAL

   d. MARKET BREADTH (NIFTY Midcap 100 vs NIFTY 50 relative performance, 20d):
      - Midcap outperforming NIFTY > 2%: BROAD_ADVANCE
      - Midcap underperforming > 2%: BROAD_DECLINE
      - Otherwise: MIXED

   e. NIFTY 50 RSI (14-day):
      - RSI > 65: OVERBOUGHT
      - RSI < 40: OVERSOLD
      - Otherwise: NEUTRAL

   Composite regime classification logic:
      - BULL:            UPTREND + (CALM or NORMAL) + FII NET_BUYER or NEUTRAL
      - BEAR:            DOWNTREND + (ELEVATED or STRESSED) + FII NET_SELLER
      - HIGH_VOLATILITY: VIX STRESSED OR (VIX ELEVATED + FII NET_SELLER + DOWNTREND)
      - SIDEWAYS:        Everything else

   Confidence score (0-100): count of indicators agreeing with the composite label × 20.

   Returns dict with: regime, confidence, nifty_trend, vix_state, fii_trend,
   breadth_state, momentum_state, raw_signals (all indicator values), regime_date

   Upsert to market_regime table on each run.

2. `scheduler/orchestrator.py` modification:
   - In load_weights_node, after loading agent weights, also load today's regime from market_regime table
   - Add `current_regime` to OrchestratorState TypedDict
   - In _composite_score(), apply regime multipliers to agent weights:
     BULL regime:            technical ×1.2, fundamental ×1.0, macro ×0.8
     BEAR regime:            macro ×1.5, institutional ×1.5, technical ×0.6, fundamental ×1.0
     HIGH_VOLATILITY regime: macro ×2.0, institutional ×2.0, technical ×0.4, historical_rag ×1.5
     SIDEWAYS regime:        fundamental ×1.3, warren_bot feeds in but weights unchanged
   - Log the regime and weight adjustments at INFO level
   - Include current_regime in the synthesis prompt: add a line "CURRENT MARKET REGIME: {regime} (confidence: {confidence}%)" to the agent outputs section

3. `api/main.py`:
   - GET /api/market/regime — returns today's regime + last 30 days of regime history
   - Include regime in GET /api/market/pulse response

4. `dashboard/src/App.jsx`:
   - Add regime badge to market ticker strip: colour-coded pill (BULL=green, BEAR=red, SIDEWAYS=grey, HIGH_VOLATILITY=orange)
   - Regime tooltip showing the 5 contributing indicators

5. `worker.py`:
   - Add regime detection job at 06:30 IST (before orchestrator at 07:00 IST so fresh regime is available)

ACCEPTANCE CRITERIA:
- `python -m agents.regime_detector` runs standalone and prints current regime
- Works when FII data is stale (graceful fallback to NEUTRAL for that indicator)
- Regime multipliers in _composite_score() sum to approximately the same total weight (normalise after applying multipliers)
- Tests: `tests/test_regime_detector.py` — test all 4 regime classifications
```

---

---

## Gap 3 — Earnings Calendar Integration

**Why third:** Entering a position 3 days before an earnings announcement is one of the most avoidable risks in equity investing. This is a quick win — small build, high protection value.

**What it does:**  
Fetches upcoming earnings dates for NSE/BSE stocks and flags any recommendation or portfolio holding where earnings are within 7 days. Blocks discovery screener from adding new positions pre-earnings. Adds an alert to the dashboard.

### Setup & Integrations Needed

**Free data sources:**
- NSE website: `https://www.nseindia.com/companies-listing/corporate-filings-financial-results` (scrapable)
- BSE website: `https://www.bseindia.com/corporates/corporate_results.html`
- Screener.in quarterly result dates (visible in stock pages)
- yfinance `Ticker.calendar` attribute (unreliable for Indian stocks but worth trying)

**Supabase migration:**
```sql
CREATE TABLE IF NOT EXISTS earnings_calendar (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol          TEXT NOT NULL,
    earnings_date   DATE NOT NULL,
    quarter         TEXT,               -- Q1FY26, Q2FY26, etc.
    source          TEXT,               -- nse / bse / screener / manual
    confirmed       BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE(symbol, earnings_date)
);
CREATE INDEX ON earnings_calendar (earnings_date);
GRANT ALL ON earnings_calendar TO service_role;
```

### Claude Code Prompt

```
Build an earnings calendar integration for the Bharat Intelligence platform.

CONTEXT:
- Repo: Stock analysis/ (see CLAUDE.md)
- New Supabase table `earnings_calendar` already created
- NSE website and BSE website are publicly accessible
- urllib.request is available (no external HTTP libs needed)
- The platform tracks portfolio_holdings (OPEN positions) and recommendations

BUILD:

1. `data/earnings_fetcher.py`
   - `fetch_upcoming_earnings(days_ahead=30) -> list[dict]`
     Tries in order:
     a. NSE corporate filings page scrape for board meeting dates (results announcements)
     b. yfinance Ticker(symbol).calendar for each symbol in portfolio + watchlist
     c. Heuristic fallback: estimate earnings date from last known quarter + 91 days
     Returns list of {symbol, earnings_date, quarter, source, confirmed}
   
   - `upsert_earnings_calendar(records: list[dict]) -> int`
     Upserts to Supabase earnings_calendar table. Returns count upserted.
   
   - `get_earnings_within_days(symbols: list[str], days=7) -> list[dict]`
     Returns earnings_calendar rows where earnings_date is within `days` days.

2. `agents/earnings_guard.py`
   - `check_pre_earnings(symbol: str, days_window=7) -> dict`
     Returns: {symbol, has_upcoming_earnings, earnings_date, days_until, warning_level}
     warning_level: 'CRITICAL' (≤3 days), 'WARNING' (4-7 days), 'CLEAR' (>7 days or unknown)
   
   - Integrate into `scheduler/orchestrator.py` synthesise_node:
     After synthesis_data is built (before validation gate), call check_pre_earnings.
     If CRITICAL: downgrade confidence by 20 points, add bear case point: "Earnings in X days — binary event risk"
     If WARNING: add synthesis caveat: "⚠ Earnings in X days — consider waiting for results"
     Include earnings_date in synthesis prompt context if within 14 days

3. `agents/discovery_screener.py` modification:
   - In prescreen(), add earnings guard check after DataCompletenessValidator
   - If earnings within 5 days: skip symbol (log at DEBUG level), return False, []

4. `api/main.py`:
   - GET /api/earnings/upcoming?days=14 — returns upcoming earnings for all portfolio symbols
   - Integrate into GET /api/portfolio response: add `earnings_alert` field to each holding

5. `dashboard/src/App.jsx`:
   - In portfolio holdings table: show red calendar icon 🗓 next to stocks with earnings ≤7 days
   - Alert banner in PortfolioTab: "Earnings Alert: SYMBOL reports in X days — review position"

6. `worker.py`:
   - Add earnings calendar refresh job at 08:00 IST daily

ACCEPTANCE CRITERIA:
- `python -m data.earnings_fetcher` fetches and prints upcoming earnings
- check_pre_earnings() always returns a valid dict (never raises, handles missing data)
- Discovery screener correctly skips pre-earnings symbols (verified in dry-run logs)
- Tests: `tests/test_earnings_guard.py`
```

---

---

## Gap 4 — Impact Cost Model

**Why fourth:** The discovery screener surfaces small/mid-cap stocks. Many of those discoveries cannot actually be traded — a 1 lakh buy order in a stock with 20,000 daily volume moves the price 3-5%. Without an impact cost estimate, discovery recommendations are incomplete. This is a small build with high practical value.

**What it does:**  
Estimates the market impact cost of entering a position of a given size in a given stock, based on its average daily volume and bid-ask spread proxy. Appended to every recommendation and used to filter out un-tradeable discoveries.

### Setup & Integrations Needed
No new DB tables. No new env vars. Uses yfinance volume data already being fetched.

### Claude Code Prompt

```
Build an impact cost model for the Bharat Intelligence platform.

CONTEXT:
- Repo: Stock analysis/
- yfinance available; agents already fetch OHLCV data
- recommendations table has metadata JSONB column (add impact_cost there)
- discovery screener in agents/discovery_screener.py uses prescreen() function

BUILD:

1. `data/impact_cost.py`
   
   `estimate_impact_cost(symbol: str, trade_value_inr: float = 100_000) -> dict`
   
   Algorithm (Amihud illiquidity proxy adapted for Indian markets):
   a. Fetch 20-day OHLCV via yfinance
   b. avg_daily_volume_inr = mean(Close × Volume) over 20 days
   c. participation_rate = trade_value_inr / avg_daily_volume_inr
   d. impact_cost_pct = sqrt(participation_rate) × 0.5 × 100
      (square-root market impact model, standard in equity microstructure)
   e. Liquidity tier:
      avg_daily_volume_inr > 50 Cr:  HIGHLY_LIQUID    (impact <0.1% for 1L trade)
      10–50 Cr:                       LIQUID           (impact 0.1–0.3%)
      1–10 Cr:                        SEMI_LIQUID      (impact 0.3–1.5%)
      < 1 Cr:                         ILLIQUID         (impact >1.5%)
   
   Returns: {
     symbol, trade_value_inr, avg_daily_volume_inr,
     participation_rate_pct, impact_cost_pct,
     liquidity_tier, max_tradeable_1pct_impact_inr,
     warning: str or None   # "Position size exceeds 5% of daily volume" etc.
   }
   
   `get_position_size_limit(symbol: str, max_impact_pct: float = 0.5) -> float`
   Returns maximum position size in INR that stays within max_impact_pct market impact.

2. `agents/discovery_screener.py` modification:
   - In prescreen(), after data completeness check, call estimate_impact_cost()
   - If liquidity_tier == 'ILLIQUID': skip symbol, log at DEBUG
   - If impact_cost_pct > 2.0 for default 1L trade: add to metadata but don't skip (human should decide)
   - Add `impact_cost` dict to discovery recommendation metadata

3. `scheduler/orchestrator.py` modification:
   - In synthesise_node, after building synthesis_data, call estimate_impact_cost() for the symbol
   - Add impact cost summary to synthesis prompt: "LIQUIDITY: {tier}, impact cost for ₹1L = {pct}%"
   - Append impact_cost to rec['metadata'] or rec['agent_signals']['impact_cost']

4. `api/main.py`:
   - GET /api/symbol/liquidity?symbol=TICKER&size=100000 — returns impact cost estimate
   - Add impact_cost_pct to recommendations API response

5. `dashboard/src/App.jsx`:
   - In discovery recommendations card: show liquidity badge (🟢 Liquid / 🟡 Semi / 🔴 Illiquid)
   - Tooltip showing: daily volume, impact cost for ₹1L, max tradeable size for <0.5% impact

ACCEPTANCE CRITERIA:
- `python -c "from data.impact_cost import estimate_impact_cost; print(estimate_impact_cost('RELIANCE.NS'))"` works
- ILLIQUID stocks correctly filtered in discovery dry run
- NIFTY 50 stocks always show HIGHLY_LIQUID tier
- Tests: `tests/test_impact_cost.py` with mocked yfinance data
```

---

---

## Gap 5 — Forward Earnings Estimates

**Why fifth:** Trailing PE is a lagging indicator. A stock at 30× trailing PE with 50% earnings growth is cheap; at 30× with 5% growth it is expensive. Without forward estimates the fundamental agent systematically misprices growth and value.

**What it does:**  
Fetches analyst consensus EPS estimates and revenue forecasts for the next 12 months from free sources. Computes forward PE and PEG ratio. Integrates into fundamental agent.

### Setup & Integrations Needed

**Free data sources (in priority order):**
1. **yfinance** — `yf.Ticker(symbol).analyst_price_targets`, `.earnings_estimate`, `.revenue_estimate` — works for some Indian stocks with NSE/BSE coverage
2. **Tickertape API** — `https://api.tickertape.in/stocks/{slug}/financials` — has analyst estimates for ~800 Indian stocks (requires reverse-engineering their API; no official docs but publicly accessible)
3. **Screener.in** — forward PE sometimes visible in stock page scraping
4. **Fallback heuristic** — extrapolate from 3-year EPS CAGR: `forward_eps = trailing_eps × (1 + eps_cagr_3yr/100)`

No new Supabase table — store forward estimates in existing `recommendations` agent_signals or new `fundamental_estimates` cache.

```sql
CREATE TABLE IF NOT EXISTS forward_estimates_cache (
    symbol          TEXT PRIMARY KEY,
    forward_eps     NUMERIC,
    forward_pe      NUMERIC,
    forward_revenue_growth NUMERIC,
    peg_ratio       NUMERIC,
    source          TEXT,           -- yfinance / tickertape / heuristic
    estimate_year   TEXT,           -- FY26, FY27
    cached_at       TIMESTAMPTZ DEFAULT now()
);
GRANT ALL ON forward_estimates_cache TO service_role;
```

### Claude Code Prompt

```
Build a forward earnings estimates fetcher for the Bharat Intelligence platform.

CONTEXT:
- Repo: Stock analysis/ (CLAUDE.md for architecture)
- agents/fundamental.py currently uses only trailing PE from screener.in
- data/fetchers.py has get_screener_data() returning trailing metrics
- New Supabase table forward_estimates_cache already created

BUILD:

1. `data/forward_estimates.py`
   
   `get_forward_estimates(symbol: str) -> dict`
   
   Try in order, return first successful result:
   
   a. yfinance approach:
      ticker = yf.Ticker(symbol)
      earnings_est = ticker.earnings_estimate  (DataFrame with 0q, +1q, 0y, +1y rows)
      revenue_est  = ticker.revenue_estimate
      Extract +1y (next fiscal year) EPS and revenue growth estimates
      forward_pe = current_price / forward_eps if forward_eps > 0
      peg_ratio  = forward_pe / expected_eps_growth_pct
   
   b. Heuristic fallback (always available):
      Use get_screener_history(symbol) eps_history to compute 3-yr CAGR
      forward_eps = last_eps × (1 + eps_cagr_3yr / 100)
      forward_pe  = current_price / forward_eps
      peg_ratio   = forward_pe / eps_cagr_3yr if eps_cagr_3yr > 0 else None
      Mark source = "heuristic_extrapolation"
   
   Returns: {
     symbol, forward_eps, trailing_eps, forward_pe, trailing_pe,
     pe_expansion_flag: bool (forward_pe > trailing_pe × 1.2),
     forward_revenue_growth_pct, peg_ratio,
     peg_interpretation: "GROWTH_AT_DISCOUNT" (<1) / "FAIR" (1-2) / "GROWTH_AT_PREMIUM" (>2) / "NEGATIVE_GROWTH",
     source, estimate_year, cached_at
   }
   
   Cache result to forward_estimates_cache table (24h TTL, same pattern as warren_bot_cache).
   Never raises — returns dict with all None values and error key on failure.

2. `agents/fundamental.py` modification:
   - After existing screener data fetch, call get_forward_estimates(symbol)
   - Add to the _snapshot for DataCompletenessValidator: "forward_pe" (optional field)
   - Add forward estimates to detail dict:
     detail["valuation"]["forward_pe"] = ...
     detail["valuation"]["peg_ratio"] = ...
     detail["valuation"]["pe_vs_forward"] = "PREMIUM" / "DISCOUNT" / "INLINE"
   - Adjust upside_pct calculation: if forward_pe is available, use it as the primary valuation anchor
     instead of trailing PE. Weight: 60% forward PE, 40% trailing PE.
   - Add peg_interpretation to bull/bear signal logic:
     PEG < 1.0: add to bullish signals (growth at discount)
     PEG > 2.5: add to bearish signals (expensive for growth rate)

3. `api/main.py`:
   - GET /api/fundamental/estimates/{symbol} — returns forward estimates with cache timestamp
   - Include forward_pe and peg_ratio in recommendations API response

4. `dashboard/src/App.jsx`:
   - In recommendation card: show "Fwd PE: {x}× (PEG: {y})" alongside trailing PE
   - Colour code: PEG < 1 = green, 1-2 = amber, > 2 = red

ACCEPTANCE CRITERIA:
- Works for RELIANCE.NS, HDFCBANK.NS (liquid stocks with yfinance coverage)
- Heuristic fallback works for any screener.in-covered stock with 3yr history
- Cache prevents repeated API calls within 24h
- Forward PE displayed in dashboard recommendation cards
- Tests: tests/test_forward_estimates.py with mocked yfinance and screener responses
```

---

---

## Gap 6 — Portfolio-Level Risk Framework

**Why sixth:** Individual stock risk is necessary but not sufficient. Ten BUY recommendations all in IT services creates concentrated sector risk. Two stocks that are 80% correlated provide almost no diversification. This framework provides portfolio-level visibility.

**What it does:**  
Computes correlation matrix of open portfolio holdings, sector concentration, portfolio beta, Value-at-Risk (historical simulation), and flags dangerously correlated or concentrated positions.

### Setup & Integrations Needed

```sql
CREATE TABLE IF NOT EXISTS portfolio_risk_snapshots (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_date       DATE UNIQUE NOT NULL,
    portfolio_beta      NUMERIC,
    portfolio_var_5pct  NUMERIC,    -- 5% daily VaR as % of portfolio
    max_sector_concentration NUMERIC, -- % of portfolio in single sector
    max_pairwise_correlation NUMERIC,
    n_holdings          INTEGER,
    risk_level          TEXT,       -- LOW / MODERATE / HIGH / CRITICAL
    risk_flags          JSONB,      -- list of specific flags
    correlation_matrix  JSONB,      -- symbol × symbol correlations
    sector_weights      JSONB,
    created_at          TIMESTAMPTZ DEFAULT now()
);
GRANT ALL ON portfolio_risk_snapshots TO service_role;
```

**No new external APIs needed** — uses yfinance historical prices of current holdings.

### Claude Code Prompt

```
Build a portfolio-level risk framework for the Bharat Intelligence platform.

CONTEXT:
- Repo: Stock analysis/
- Supabase portfolio_holdings table has: symbol, yf_symbol, sector, qty, avg_buy, current_price
- portfolio_risk_snapshots table already created (schema above)
- yfinance available for 1yr daily price history
- scheduler/portfolio_monitor.py exists as reference for portfolio-level monitoring patterns

BUILD:

1. `agents/portfolio_risk.py`
   Entry point: `compute_portfolio_risk(dry_run=False) -> dict`
   
   Steps:
   a. Load all OPEN holdings from Supabase (symbol, yf_symbol, sector, qty, current_price)
   b. Compute position values and weights (weight_i = position_value_i / total_portfolio_value)
   c. Fetch 252-day daily returns for all holdings + NIFTY 50 (^NSEI) via yfinance
   d. Compute correlation matrix (pandas corr() on returns DataFrame)
   e. Compute portfolio beta: weighted sum of individual betas (each vs ^NSEI, 252-day regression)
   f. Compute historical VaR: 
      - Compute daily portfolio P&L using weights and individual returns
      - 5th percentile of daily P&L distribution = 5% daily VaR
      - Annualised: VaR_annual = VaR_daily × sqrt(252)
   g. Sector concentration: group holdings by sector, sum weights per sector
   h. Risk flags (check each):
      - "SECTOR_CONCENTRATION": any sector > 40% of portfolio
      - "HIGH_CORRELATION": any pair with |correlation| > 0.85
      - "HIGH_BETA": portfolio beta > 1.5
      - "HIGH_VAR": daily VaR > 3%
      - "SINGLE_STOCK_OVERWEIGHT": any single stock > 25% of portfolio
   i. Risk level: 0 flags=LOW, 1-2=MODERATE, 3-4=HIGH, 5+=CRITICAL
   j. Upsert to portfolio_risk_snapshots
   
   Returns full risk dict including correlation_matrix (symbol pairs → correlation float).

2. `api/main.py`:
   - GET /api/portfolio/risk — returns latest portfolio_risk_snapshots row
   - GET /api/portfolio/risk/history?days=30 — time series of risk metrics
   - Enhance GET /api/portfolio/alerts: include risk_flags as DANGER-level alerts when risk_level=HIGH or CRITICAL

3. `dashboard/src/App.jsx`:
   - New "Portfolio Risk" section in PortfolioTab (collapsible)
   - Risk level badge: LOW (green) / MODERATE (amber) / HIGH (orange) / CRITICAL (red)
   - Sector concentration donut chart (lightweight, inline SVG or simple CSS)
   - Correlation heatmap: table of holdings × holdings with colour-coded cells (green=uncorrelated, red=highly correlated)
   - Risk flags list with icons
   - Portfolio beta and VaR displayed prominently
   - "Last updated: X hours ago" with refresh button

4. `worker.py`:
   - Add portfolio risk job at 19:00 IST daily (after markets close and after outcome tracker)

ACCEPTANCE CRITERIA:
- Works with 1 holding (trivial case: beta = stock beta, no pair correlations)
- Works when some holdings have missing yfinance data (exclude that holding, log warning)
- Risk flags fire correctly: test with manually constructed correlated positions
- Correlation matrix is JSON-serialisable (numpy floats → Python floats)
- Tests: tests/test_portfolio_risk.py with 3-stock mock portfolio
```

---

---

## Gap 7 — Volume Profile Analysis

**Why seventh:** Volume is the market's polygraph. Price can be manipulated in illiquid stocks; volume cannot be faked at scale. Integrating VWAP, volume-at-price distribution, and volume momentum into the technical agent significantly improves entry timing signals.

**What it does:**  
Adds Point of Control (POC), Value Area High/Low (VAH/VAL), VWAP, and volume divergence signals to the technical agent. These tell you *where the market has done most of its business* — a powerful support/resistance concept.

### Setup & Integrations Needed
No new DB tables, no new env vars. Enhances existing `agents/technical.py`.

### Claude Code Prompt

```
Enhance the technical analysis agent with volume profile signals.

CONTEXT:
- Repo: Stock analysis/
- agents/technical.py — the existing technical agent (read it first)
- It already fetches OHLCV via data/fetchers.py get_ohlcv()
- Returns a detail dict with rsi, macd, ema, bollinger sub-dicts
- DataCompletenessValidator already checks ohlcv_rows, volume_avg

BUILD — modify `agents/technical.py`:

1. New function `_volume_profile(df: pd.DataFrame, bins: int = 20) -> dict`:
   - Takes last 60 trading days of OHLCV
   - Divide price range (low to high) into `bins` price buckets
   - Accumulate volume in each bucket (simple: assign each candle's volume to its midpoint bucket)
   - Point of Control (POC): price bucket with highest accumulated volume — strongest support/resistance
   - Value Area: price range containing 70% of total volume
     - Value Area High (VAH): upper bound of value area
     - Value Area Low (VAL): lower bound of value area
   - Returns: {poc, vah, val, price_vs_poc: "ABOVE" / "BELOW" / "AT" (within 1%)}

2. New function `_vwap_signal(df: pd.DataFrame, lookback_days: int = 20) -> dict`:
   - VWAP = sum(close × volume) / sum(volume) over lookback_days
   - Signal: price vs VWAP
     - Price > VWAP by >2%: VWAP_BULLISH
     - Price < VWAP by >2%: VWAP_BEARISH
     - Within 2%: VWAP_NEUTRAL
   - VWAP slope (VWAP today vs VWAP 5 days ago): RISING / FALLING / FLAT
   Returns: {vwap, price_vs_vwap_pct, vwap_signal, vwap_slope}

3. New function `_volume_momentum(df: pd.DataFrame) -> dict`:
   - Volume ratio: last 5-day avg volume / 20-day avg volume
     - > 1.5: VOLUME_SURGE (confirms breakout or breakdown)
     - < 0.5: VOLUME_DROUGHT (weak conviction move)
     - Otherwise: VOLUME_NORMAL
   - Volume divergence: 
     - Price up 3%+ last 5 days but volume_ratio < 0.7: BEARISH_DIVERGENCE (weak breakout)
     - Price down 3%+ last 5 days but volume_ratio < 0.7: BULLISH_DIVERGENCE (weak breakdown = potential reversal)
   Returns: {volume_ratio, volume_state, divergence: None / "BEARISH_DIVERGENCE" / "BULLISH_DIVERGENCE"}

4. Integrate into `analyse()` function:
   - Call all three functions after OHLCV is fetched
   - Add to detail dict: detail["volume_profile"] = {...}, detail["vwap"] = {...}, detail["volume_momentum"] = {...}
   - Add to signal scoring:
     VOLUME_SURGE + price above POC: +5 points to technical score
     VOLUME_SURGE + price below POC: -5 points (selling pressure)
     BEARISH_DIVERGENCE: -8 points
     BULLISH_DIVERGENCE: +5 points
     Price in Value Area (VAL to VAH): neutral (consolidation zone)
     Price above VAH: +3 points (price discovery)
     Price below VAL: -3 points (distribution)

5. Update DataCompletenessValidator `has_volume` FieldSpec description to note it gates volume profile

ACCEPTANCE CRITERIA:
- analyse() returns identical structure for stocks with no volume data (graceful degradation)
- POC is always within the 60-day high-low range
- Volume profile signals appear in orchestrator agent_text output
- Tests: tests/test_technical_volume_profile.py
```

---

---

## Gap 8 — Management Quality Scoring

**Why eighth:** Management is the single variable most consistently associated with long-term compounding. A mediocre business with excellent capital allocators outperforms a great business with extractive management. This agent scores capital allocation quality, cash conversion, and historical commitment.

**What it does:**  
New agent that scores management on five dimensions: capital allocation efficiency (ROCE trend vs capex), cash conversion (operating cash flow vs reported profit), dividend/buyback consistency, capex discipline (capex as % of depreciation signals over-investment), and promoter commitment trend.

### Setup & Integrations Needed
Uses `get_screener_history()` already in `data/fetchers.py`. No new external APIs.

### Claude Code Prompt

```
Build a management quality scoring agent for the Bharat Intelligence platform.

CONTEXT:
- Repo: Stock analysis/
- data/fetchers.py has get_screener_history(symbol) returning 10yr annual time series:
  revenue_history, pat_history, eps_history, capex_history, depreciation_history,
  roce_history, roe_history, dividend_payout_history, promoter_holding_history
- agents/warren_bot.py covers long-term quality — mgmt_quality agent is a separate, narrower signal
  focused specifically on capital allocation behaviour
- Follow the same agent pattern: analyse(symbol) -> dict, never raises

BUILD:

1. `agents/mgmt_quality.py`
   Entry point: `analyse(symbol: str) -> dict`
   
   Five scoring dimensions (0-20 points each, total 0-100):
   
   a. CAPITAL ALLOCATION EFFICIENCY (0-20):
      - Capex efficiency: ROCE(last 3yr avg) / Capex intensity (capex/revenue %)
      - If ROCE improving while capex moderate (<20% of revenue): 15-20 pts
      - If ROCE declining while capex high (>30% of revenue): 0-5 pts (value-destroying capex)
      - Metric name: "capex_efficiency_score"
   
   b. CASH CONVERSION QUALITY (0-20):
      - CFO/PAT ratio over 5 years (operating cash flow vs reported profit)
      - Use: operating_cashflow = PAT + depreciation - working_capital_change (approximate)
      - Since we only have PAT and depreciation from screener, compute:
        cash_earnings_proxy = PAT + depreciation
        cash_conversion = cash_earnings_proxy / revenue (cash margin proxy)
      - Consistent cash margin > 8%: high score; declining or negative: low score
      - Metric name: "cash_conversion_score"
   
   c. CAPEX DISCIPLINE (0-20):
      - Maintenance capex proxy: capex/depreciation ratio
      - Ratio 1.0-2.5: growth capex at reasonable level (15-20 pts)
      - Ratio > 4.0: over-investment, likely poor returns ahead (0-5 pts)
      - Ratio < 0.8: underinvestment, milking the asset base (5-10 pts)
      - Metric name: "capex_discipline_score"
   
   d. DIVIDEND AND RETURN TRACK RECORD (0-20):
      - Consistent dividend payment (>5yr streak): 10 pts
      - Growing dividend + buybacks: additional 5 pts
      - Payout ratio 20-50% (sustainable growth + return): 5 pts
      - Zero dividends in profitable company (retention only): 10 pts (neutral, growth-oriented)
      - Metric name: "return_to_shareholders_score"
   
   e. PROMOTER COMMITMENT (0-20):
      - Promoter holding trend over 5yr: increasing = 15-20 pts, stable = 10 pts, declining = 0-5 pts
      - Any pledging > 20%: cap this dimension at 5 pts
      - Metric name: "promoter_commitment_score"
   
   Overall: mgmt_score = sum of 5 dimensions (0-100)
   Signal:
     score ≥ 70: "MANAGEMENT_STRONG"
     score 50-69: "MANAGEMENT_ADEQUATE"
     score 30-49: "MANAGEMENT_WEAK"
     score < 30: "MANAGEMENT_POOR"
   
   Returns full agent dict with signal, score, all 5 dimension scores, key metrics,
   data_quality, years_available, agent_name="mgmt_quality"

2. `scheduler/orchestrator.py`:
   - Add mgmt_quality as a Phase 1 parallel agent (alongside technical, fundamental, sentiment)
   - Add to AGENT_NAMES list
   - mgmt_quality score feeds into composite (weight similar to warren_bot — i.e., informational but moderate weight)

3. `agents/warren_bot.py` integration:
   - Warren bot already scores "Management Quality" dimension. When mgmt_quality agent result is available,
     optionally log if the two scores diverge by more than 20 points (potential signal conflict)

4. Register in DataCompletenessValidator (agents/base.py):
   - Add "mgmt_quality" entry to AGENT_FIELD_SPECS:
     years_available (critical, ≥3), revenue_history (critical, ≥3), roce_history (optional, ≥3)

ACCEPTANCE CRITERIA:
- analyse() works for any NSE stock in screener.in
- Returns INSUFFICIENT_DATA when <3 years of screener history available
- Score 0-100 for companies with full 10yr history
- Tests: tests/test_mgmt_quality.py with mock screener history data
```

---

---

## Gap 9 — Corporate Governance Red Flags

**Why ninth:** Governance failures (related party transactions, poor audit quality, board composition) are the leading predictor of blow-up risk in Indian small/mid-caps. These are often visible in public filings before the fraud materialises.

**What it does:**  
Screens for seven governance red flags using publicly available data. Any triggered flag is appended to the recommendation's bear case and raises the risk_score.

### Setup & Integrations Needed

**Free data sources:**
- BSE corporate filings (publicly accessible)
- Screener.in (auditor name, contingent liabilities sometimes present)
- MCA (Ministry of Corporate Affairs) — company filing status
- NSE bulk deal disclosures

### Claude Code Prompt

```
Build a corporate governance red flag screener for the Bharat Intelligence platform.

CONTEXT:
- Repo: Stock analysis/
- agents/warren_bot.py already checks promoter pledging and market cap
- agents/fundamental.py checks debt/equity
- This agent focuses on governance structure, not just financials
- Screener.in get_screener_data() returns: promoter_pledging, market_cap, pe, roce, etc.
- BSE website has corporate announcements and filings

BUILD:

1. `agents/governance_screener.py`
   Entry point: `analyse(symbol: str) -> dict`
   
   Check seven red flags (each is True/False with severity: CRITICAL / WARNING / INFO):

   FLAG 1 — PLEDGING_CRITICAL (CRITICAL):
     promoter_pledging > 50%
   
   FLAG 2 — PLEDGING_WARNING (WARNING):
     promoter_pledging 30-50%
   
   FLAG 3 — PROMOTER_EXITING (CRITICAL):
     promoter_holding declined > 5 percentage points in last 2 years
     (use get_screener_history promoter_holding_history)
   
   FLAG 4 — UNSUSTAINABLE_DEBT_GROWTH (WARNING):
     debt grew faster than revenue for 3 consecutive years
     (use screener history: revenue_history, estimate debt from interest/ROCE)
   
   FLAG 5 — EARNINGS_QUALITY_CONCERN (WARNING):
     PAT growing but operating cash flow flat/declining for 2+ years
     Proxy: if EPS CAGR 3yr > 15% but revenue CAGR 3yr < 5%, flag as potential accruals issue
   
   FLAG 6 — AUDITOR_CONCERN (INFO):
     Try to fetch auditor name from screener.in or BSE filing page
     Flag if: auditor is a very small unknown firm for a large company (basic heuristic:
     if market_cap > 1000 Cr but auditor name not in known_major_auditors list)
     known_major_auditors = ["Deloitte", "Price Waterhouse", "KPMG", "BSR", "S.R. Batliboi",
                              "Walker Chandiok", "B S R", "Haribhakti", "Chaturvedi"]
   
   FLAG 7 — CONTINGENT_LIABILITY_LARGE (WARNING):
     If screener.in data includes contingent_liabilities field AND > 50% of net worth: flag
     (this data is not always available — mark as DATA_UNAVAILABLE if missing)
   
   Scoring:
     CRITICAL flags: risk_score_adjustment += 20 each
     WARNING flags: risk_score_adjustment += 10 each
     INFO flags: risk_score_adjustment += 5 each
   
   Returns: {
     signal: "GOVERNANCE_CLEAN" / "GOVERNANCE_CONCERNS" / "GOVERNANCE_RED_FLAGS",
     score: 100 - risk_score_adjustment (capped at 0),
     flags: list of {flag_name, severity, detail, triggered: bool},
     risk_score_adjustment: int,
     critical_flag_count: int,
     agent_name: "governance_screener"
   }

2. `scheduler/orchestrator.py`:
   - Add governance_screener to Phase 1 parallel agents
   - Add to AGENT_NAMES
   - In synthesise_node synthesis prompt: if critical_flag_count > 0, prepend GOVERNANCE WARNING
   - In _build_recommendation(): if governance risk_score_adjustment > 0, apply it:
     rec["risk_score"] = min(100, rec["risk_score"] + governance_result["risk_score_adjustment"])
   - Append triggered flags to rec bear_case list

3. `agents/base.py`:
   - Add governance_screener to AGENT_FIELD_SPECS:
     pe (optional), promoter_pledging (optional), years_available (critical ≥2)

ACCEPTANCE CRITERIA:
- Works when most data is unavailable (returns flags as DATA_UNAVAILABLE, not crashes)
- CRITICAL flags are visible in recommendation bear case
- risk_score is correctly elevated for flagged companies
- Tests: tests/test_governance_screener.py
```

---

---

## Gap 10 — Options Market Signals

**Why tenth:** The options market is the most honest expression of institutional positioning. Put/call open interest, IV skew, and unusual options activity reveal what sophisticated money is doing before it shows up in FII/DII flow data.

**What it does:**  
Fetches NSE options chain for a stock, computes put/call OI ratio, implied volatility skew (put IV vs call IV), and flags unusual OI build-up as a signal.

### Setup & Integrations Needed

**Free data source:** NSE options chain is publicly accessible via unofficial NSE API endpoints. No authentication required for standard options chain data.

```
NSE Options Chain endpoint:
https://www.nseindia.com/api/option-chain-equities?symbol=SYMBOL
(Requires browser-like headers: User-Agent, Referer, etc.)
```

**New env var:** None required  
**New DB table:** Optional cache

### Claude Code Prompt

```
Build an options market signals agent for the Bharat Intelligence platform.

CONTEXT:
- Repo: Stock analysis/
- NSE options chain is publicly accessible (requires browser-like headers to avoid bot detection)
- Only liquid optionable stocks have meaningful options data (Nifty 200 roughly)
- This agent should gracefully return NO_DATA for stocks without active options

BUILD:

1. `data/options_fetcher.py`
   
   `fetch_options_chain(symbol: str) -> dict | None`
   - Fetch NSE options chain for symbol (strip .NS suffix for NSE API)
   - URL: https://www.nseindia.com/api/option-chain-equities?symbol={SYMBOL}
   - Use urllib.request with headers:
     User-Agent: Mozilla/5.0 ...
     Referer: https://www.nseindia.com
     Accept: application/json
   - Parse response: extract CE (call) and PE (put) data by strike price and expiry
   - Focus on near-month expiry (next monthly expiry, last Thursday of month)
   - Return: {symbol, expiry, underlying_price, strikes: [{strike, call_oi, put_oi, call_iv, put_iv, call_volume, put_volume}]}
   - Return None if fetch fails or symbol has no options (handle gracefully)

2. `agents/options_sentiment.py`
   Entry point: `analyse(symbol: str) -> dict`
   
   Metrics to compute from options chain:
   
   a. PUT/CALL OI RATIO (PCR):
      pcr = total_put_oi / total_call_oi (near-month, all strikes)
      PCR > 1.5: heavily put-loaded (bearish sentiment / hedging)
      PCR > 2.0: extreme bearish positioning (often contrarian bullish)
      PCR 0.8-1.5: neutral
      PCR < 0.8: call-loaded (bullish positioning or complacency)
   
   b. MAX PAIN PRICE:
      max_pain = strike price where total options value (all open interest) is minimised
      (market makers' preferred settlement price)
      price_vs_max_pain: current_price - max_pain (positive = above, negative = below)
   
   c. IV SKEW:
      otm_put_iv = average IV of puts 5-10% out-of-the-money (below spot)
      otm_call_iv = average IV of calls 5-10% out-of-the-money (above spot)
      skew = otm_put_iv - otm_call_iv
      skew > 5: put skew (bearish; investors paying premium for downside protection)
      skew < -2: call skew (bullish; institutions buying upside exposure)
   
   d. OI CONCENTRATION SIGNAL:
      Largest single strike OI (call or put) as % of total OI
      If > 30%: strong support/resistance at that strike (max pain effect)
   
   Signal:
     BULLISH_OPTIONS:  PCR > 2.0 (contrarian) OR (PCR 0.8-1.3 AND call skew)
     BEARISH_OPTIONS:  PCR < 0.7 OR (PCR < 1.0 AND strong put skew > 5)
     NEUTRAL_OPTIONS:  PCR 1.3-2.0, skew within ±3
     NO_DATA:          Options unavailable for this stock
   
   Score: 50 baseline, adjust by PCR signal and skew
   
   Returns full agent dict with signal, score, pcr, max_pain, iv_skew, detail, agent_name

3. `scheduler/orchestrator.py`:
   - Add options_sentiment as a Phase 2 parallel agent (alongside institutional, historical_rag)
   - Add to AGENT_NAMES
   - Handle NO_DATA gracefully (already handled by existing NO_DATA exclusion in _composite_score)

4. `agents/base.py`:
   - Add options_sentiment to AGENT_FIELD_SPECS (minimal: just "options_available" as non-critical)

NOTES FOR IMPLEMENTATION:
- NSE bot detection may require session-based cookies; implement a session refresh mechanism
- If NSE blocks: fall back to yfinance options (yf.Ticker(sym).option_chain(date)) which works for some liquid stocks
- Never crash if options data unavailable — always return {"signal": "NO_DATA", "score": 50, "agent_name": "options_sentiment"}

ACCEPTANCE CRITERIA:
- Works for RELIANCE.NS (highly liquid, active options)
- Returns NO_DATA for small/mid-cap stocks without options
- PCR and max pain computed correctly
- Tests: tests/test_options_sentiment.py with mock NSE API response
```

---

---

## Gap 11 — Historical RAG Enrichment

**Why eleventh:** The RAG agent's quality is directly proportional to the quality and coverage of the historical_events database. Currently it has fewer than 20 events. At that size it produces noise signals. This task builds the data foundation the RAG agent needs.

**What it does:**  
Creates a comprehensive seed dataset of 150+ well-structured Indian market historical events (2000–2025), and builds an automated event extractor that can continuously enrich the database from news.

### Setup & Integrations Needed

**Requires:** `OPENAI_API_KEY` (for embeddings — already optional in historical_rag.py)  
**Supabase match_historical_events RPC** must be created (see db/schema.sql for pgvector function).

### Claude Code Prompt

```
Enrich the historical RAG knowledge base for the Bharat Intelligence platform.

CONTEXT:
- Repo: Stock analysis/
- agents/historical_rag.py uses Supabase historical_events table with pgvector embeddings
- Table schema: event_type, description, event_date, affected_sectors, market_impact, outcome, embedding
- Current DB has <20 events — too sparse for reliable similarity matching
- db/seed_historical_events.py may exist (check if so, extend it)

BUILD:

1. `db/seed_historical_events_comprehensive.py`
   
   Standalone script that seeds 150+ historical events into the historical_events table.
   
   Each event must have:
   - event_type: MACRO_SHOCK / POLICY_CHANGE / GLOBAL_CONTAGION / SECTOR_DISRUPTION / LIQUIDITY_CRISIS / GOVERNANCE_FAILURE / COMMODITY_SHOCK
   - description: 2-3 sentences describing the market setup/context at the time (written as if observing in real-time)
   - event_date: approximate date
   - affected_sectors: list of sectors impacted
   - market_impact: SEVERE_NEGATIVE / MODERATE_NEGATIVE / SECTOR_NEGATIVE / MILD_NEGATIVE / NEUTRAL / MILD_POSITIVE / LONG_TERM_POSITIVE / STRONG_POSITIVE
   - outcome: 1-2 sentences describing what actually happened to markets in 6-12 months after

   Required events to include (minimum coverage):
   
   GLOBAL SHOCKS: 2000 dot-com bust, 2001 9/11, 2003 SARS, 2004 election shock,
   2008 Lehman / subprime, 2010 European debt crisis, 2013 taper tantrum,
   2015 China devaluation, 2016 Brexit, 2018 IL&FS crisis, 2020 COVID crash,
   2020 COVID recovery, 2022 Russia-Ukraine / FII outflow, 2023 Adani crisis,
   2024 election results volatility
   
   INDIA-SPECIFIC: 2008 Satyam fraud, 2016 demonetisation, 2017 GST implementation,
   2019 NBFC/HFC liquidity crisis, 2020 Yes Bank bailout, RBI surprise rate cuts
   (2015, 2020), every major RBI repo rate cycle (2004-2008 hike, 2009 cut,
   2014 cut cycle, 2022-2023 hike cycle)
   
   SECTOR-SPECIFIC: 2012 coal block cancellations (power/mining), 2014 pharma USFDA
   warning letters surge, 2017-18 telecom sector disruption (Jio entry),
   2018-19 auto sector slowdown, 2021-22 IT sector supercycle, 2022 specialty
   chemicals correction, PSU bank recapitalisation 2017, 2023 small/midcap bubble

   Use AI assistance to write good descriptions — each description should sound like
   a real-time market commentary note. The more specific and factual the description,
   the better the embedding similarity will work.
   
   Script should:
   - Check if event_date already exists for that event type before inserting (avoid duplicates)
   - If OPENAI_API_KEY is set: compute and store embeddings for each event
   - If not: store events without embeddings (keyword fallback will handle them)
   - Print progress and summary at end

2. `agents/historical_rag.py` enhancement:
   - Add `_enrich_with_context(market_description, matched_events) -> str` function
     that generates a richer context by asking Claude Haiku to extract the most
     relevant parallel between today's setup and each matched historical event
   - This runs only when ANTHROPIC_API_KEY is set and adds 1-2 sentences of 
     "The parallel to today is: ..." to each matched event's lesson
   - Keep it lightweight (Haiku, 50 tokens max, non-blocking)

3. `db/auto_event_extractor.py` (bonus — for ongoing enrichment):
   - Monthly job that reads recent news headlines from the system's sentiment agent data
   - Uses Claude Haiku to identify if any recent news constitutes a new historical event
     worth adding (criteria: market impact > 2% NIFTY move + identifiable cause)
   - Drafts a structured event record and inserts with market_impact = PENDING_REVIEW
   - Human can review PENDING_REVIEW events in dashboard

ACCEPTANCE CRITERIA:
- `python db/seed_historical_events_comprehensive.py` seeds 150+ events
- Script is idempotent (can be run multiple times without duplicating events)
- After seeding, historical_rag agent returns MIXED_ANALOGUE or better for common market descriptions
- Tests: tests/test_historical_rag_enriched.py — test with 5 different market descriptions
```

---

---

## Gap 12 — Valuation Sensitivity Analysis

**Why twelfth:** A single-point valuation is misleading. The useful question is not "what is the fair value?" but "what assumptions does the current price imply, and how sensitive is the thesis to those assumptions?" This analysis converts the fundamental agent's output from a number to a scenario framework.

**What it does:**  
Runs bull/base/bear case DCF scenarios with explicit assumptions, computes at what growth rate or multiple the current price is fairly valued (the "implied expectations" question), and generates a structured sensitivity table for the Claude synthesis prompt.

### Setup & Integrations Needed
No new APIs, no new tables. Enhances `agents/fundamental.py` and `agents/warren_bot.py`.

### Claude Code Prompt

```
Add valuation sensitivity analysis to the Bharat Intelligence platform.

CONTEXT:
- Repo: Stock analysis/
- agents/fundamental.py performs basic valuation (PE vs sector, ROCE vs threshold)
- agents/warren_bot.py has a DCF function (3-stage) — read it for reference
- data/fetchers.py get_screener_history() provides 10yr historical data
- The goal: replace single-point "fair value = X" with scenario analysis

BUILD:

1. `agents/valuation_scenarios.py`
   Entry point: `analyse(symbol: str) -> dict`
   
   Three DCF scenarios using owner earnings (PAT + Depreciation - Maintenance Capex):
   
   BULL CASE: revenue_cagr = hist_cagr × 1.3, margin_expansion = +2%, discount = 11%
   BASE CASE: revenue_cagr = hist_cagr × 1.0, margin = current_avg, discount = 12%
   BEAR CASE: revenue_cagr = hist_cagr × 0.6, margin_compression = -2%, discount = 13%
   
   For each scenario:
   - 5-year explicit forecast + 5-year fade to terminal growth (4%)
   - Owner earnings in year 10 × (1 + terminal_growth) / (discount - terminal_growth) = terminal value
   - DCF sum = PV of 10yr + PV of terminal
   - Margin of Safety = (DCF value - current_price) / DCF value × 100
   
   Implied expectations (reverse DCF):
   - At what revenue CAGR does current_price equal fair value? (binary search on base case)
   - At what terminal PE does current_price equal fair value?
   - These "implied" numbers tell you what the market is pricing in
   
   Sensitivity table (7×7):
   - Rows: revenue CAGR from (hist_cagr - 10%) to (hist_cagr + 10%) in 2pt steps
   - Cols: discount rate from 10% to 16% in 1pt steps
   - Cell: implied upside/downside at that combination
   
   Break-even analysis:
   - break_even_cagr: minimum revenue CAGR for 0% return at current price (base margins/discount)
   - break_even_pe: exit PE needed for 15% annual return over 5 years
   
   Returns: {
     signal: "DEEPLY_UNDERVALUED" / "UNDERVALUED" / "FAIRLY_VALUED" / "OVERVALUED" / "RICHLY_PRICED",
     score: (base case MOS mapped to 0-100),
     bull_case: {intrinsic_value, mos_pct, key_assumption},
     base_case: {intrinsic_value, mos_pct, key_assumption},
     bear_case: {intrinsic_value, mos_pct, key_assumption},
     implied_revenue_cagr: float (what market is pricing),
     implied_exit_pe: float,
     break_even_cagr: float,
     sensitivity_table: dict (compact: just 3 key points from the matrix),
     data_quality, agent_name: "valuation_scenarios"
   }

2. `scheduler/orchestrator.py`:
   - Add valuation_scenarios as Phase 2 parallel agent
   - Add to AGENT_NAMES
   - In synthesis prompt agent_text: include bull/base/bear intrinsic values and implied CAGR
     "VALUATION SCENARIOS: Bull ₹{bull_iv} (+{bull_mos}%) | Base ₹{base_iv} (+{base_mos}%) | Bear ₹{bear_iv} ({bear_mos}%) | Market implies {implied_cagr}% revenue CAGR"

3. `agents/base.py`:
   - Add valuation_scenarios to AGENT_FIELD_SPECS:
     current_price (critical), years_available (critical ≥3), revenue_history (critical ≥3)

4. `dashboard/src/App.jsx`:
   - In recommendation card: show scenario bar (Bear / Base / Bull) with current price marker
   - Simple visual: ──●──[BASE]──[BULL] with current price as ●
   - Show "Market implies X% CAGR" as a key insight line

ACCEPTANCE CRITERIA:
- Bull case intrinsic value > base case > bear case always
- Works when capex data is missing (use depreciation as maintenance capex proxy)
- Returns INSUFFICIENT_DATA if < 3 years of history
- Implied CAGR calculation converges (handles edge cases where DCF = 0)
- Tests: tests/test_valuation_scenarios.py with known input → verify DCF math
```

---

---

## General Implementation Notes

### Before Starting Each Task

1. Read `CLAUDE.md` first — it is the canonical architecture reference
2. Run existing tests: `python -m pytest tests/ -q --tb=short` — ensure baseline passes
3. Check SUPABASE_URL and SUPABASE_SERVICE_KEY are set in `.env`

### Database Migration Protocol

For each gap that requires a new Supabase table:
1. Run the SQL in Supabase Dashboard → SQL Editor
2. Run `db/migrations/grant_service_role_rls.sql` to ensure service_role access
3. Verify with: `python -c "from supabase import create_client; c = create_client(...); print(c.table('NEW_TABLE').select('*').limit(1).execute())"`

### Testing Protocol

Each gap should produce at minimum:
- Unit tests in `tests/test_{component}.py`
- Standalone CLI test: `python -m agents.{new_agent}` with a real symbol
- Dry-run verification: all new features must respect `dry_run=True` (no DB writes)

### Dependency Order for Integration

Some agents depend on others being built first:
- **Gap 6 (Portfolio Risk)** needs Gap 3 (Earnings Calendar) for complete holdings picture
- **Gap 7 (Volume Profile)** is self-contained, can be done any time after Gap 1
- **Gap 12 (Valuation Scenarios)** improves with Gap 5 (Forward Earnings) but works standalone
- **Gap 11 (RAG Enrichment)** must be done before RAG agent produces reliable signals in production

### API Key Requirements Summary

| Gap | New Keys Required |
|-----|-------------------|
| 1–4 | None (uses existing SUPABASE + yfinance) |
| 5 | None (yfinance + heuristic fallback) |
| 6 | None |
| 7 | None |
| 8–9 | None |
| 10 | None (NSE public API) |
| 11 | OPENAI_API_KEY (optional — embeddings; keyword fallback works without it) |
| 12 | None |

### Measuring Success (Return to After 90 Days)

After completing all 12 gaps, the benchmark test is:
```
python -m agents.outcome_tracker --report
```
This should show:
- BUY recommendations: hit rate > 55%, average alpha > +3% at 90 days vs NIFTY 50
- AVOID recommendations: accuracy > 60% (stocks avoided underperformed NIFTY)
- SUPPRESSED recommendations: if they were published, would they have performed worse than PASS? (validation layer ROI)

If the hit rate is below 50% or alpha is negative at 90 days across 30+ BUY recommendations, the fundamental signal generation — not the architecture — requires rethinking. That is the honest feedback loop this roadmap creates.
