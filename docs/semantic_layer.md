# Bharat Intelligence — Semantic Layer
Injected into every Claude synthesis prompt. Defines what each field *means* so the model never infers semantics from field names alone.

---

## 1. Technical Indicators
- **RSI (14-day):** 0–100. <30 = oversold; >70 = overbought; 40–60 = neutral. Do not use RSI alone in a trending market.
- **MACD (12/26/9):** Histogram above zero and expanding = bullish. Shrinking/below zero = weakening. Signal-line crossover = directional trigger.
- **EMA-50/200:** Price > EMA-200 = long-term uptrend. EMA-50 crossing EMA-200 up = Golden Cross (bull); down = Death Cross (bear).
- **OBV:** Rising OBV with rising price confirms trend. OBV diverging (flat/falling while price rises) = early reversal warning.

## 2. Fundamental Metrics
- **PE Ratio (TTM):** Price ÷ trailing 12-month EPS. Screener.in; ~45-day quarter lag. Negative = loss-making; never use for valuation. Medians: IT 25–35×, Pvt Banks 15–22×, PSU Banks 8–12×, FMCG 45–60×, Metals 6–10×.
- **ROCE:** EBIT ÷ (Assets − Current Liabilities) × 100. >20% = excellent; >15% = quality; <10% = inefficient. Asset-heavy industries (steel, infra) structurally lower — compare within sector only.
- **Debt/Equity:** Financial debt ÷ book equity. Flag: >1.5 for non-financial, non-infra. NBFCs and infra are structurally high; banks use CAR + GNPA% instead.
- **Revenue Growth (YoY %):** Use consolidated, same-quarter prior-year (removes seasonality). Inflated when prior year was abnormally weak (COVID, write-off).
- **Promoter Holding %:** % held by founding group. >50% = strong control; <30% declining = distribution risk; >75% limits free-float liquidity.

## 3. Indian Market Conventions
- **T+2 Settlement:** Trades settle 2 business days post-trade. Ex-dividend = T+1 before record date. F&O monthly expiry = last Thursday of month.
- **Circuit Breakers:** SEBI-assigned stock limits: ±5%, ±10%, or ±20% per day. Limit hit = trading halts for day. SME stocks ±5%; NIFTY 50 ±20%. Index: NIFTY 10% → 45-min halt; 15% → 105-min halt; 20% → full-day close.
- **FII / FPI:** SEBI-registered foreign portfolio investors. Net flow (₹ Cr/day) = Purchases − Sales. Published post 4 PM IST; prior-day data available by next morning. Sector limits: 24–74% of paid-up capital.
- **DII:** Indian MFs, insurance, EPFO. Counter-cyclical to FIIs. Heavy DII buying during FII outflows = price support.
- **Bulk Deals:** Single transaction ≥ 0.5% of total listed shares; disclosed same-day EOD. Bulk institutional buy = short-term demand signal.
- **India VIX:** NSE 30-day implied vol. <15 = calm; 15–20 = normal; >20 = elevated; >25 = stress. Rising VIX + FII selling = heightened drawdown risk.
- **RBI Repo Rate:** Set 6× per year by MPC. Cuts positive for banks, NBFCs, housing finance. Agent fetch may lag 24 hrs post-announcement.

## 4. Disambiguation Rules
- **Promoter vs Institutional:** Mutually exclusive. Promoter = founding family/group. Institutional = FII + DII. Promoter % + FII % + DII % + Public % = 100%. Never conflate.
- **Promoter Pledging %:** Fraction of promoter's *own* shares pledged as collateral. Sub-metric within promoter holding; does not add to it. >30% = governance yellow flag; >50% = red flag (forced-selling cascade risk).
- **Consolidated vs Standalone:** Always use consolidated (parent + subsidiaries) for revenue, profit, debt, ROCE. Screener.in defaults to consolidated — verify the label. Standalone understates when subsidiaries are operationally material.
- **TTM PE distortion:** One-time exceptional gains inflate TTM EPS and deflate PE. Always check "Exceptional Items" before treating a PE spike or collapse as a valuation signal.

## 5. Data Source Peculiarities
- **Screener.in lags:** Q1→mid-Aug; Q2→mid-Nov; Q3→mid-Feb; Q4→late May. Annual: 60–90 days after 31 March FY-end. Null metric = loss-making or listing <2 years, not a data error. Pledging lags up to 90 days (quarterly SEBI filing).
- **yfinance:** NSE `TICKER.NS`; BSE `TICKER.BO`; `^NSEI` (NIFTY 50); `^BSESN` (SENSEX). `INR=X` = USD per 1 INR (invert for ₹/USD). Weekend/holiday = last trading day close, not live. Volume in shares; <50 000/day = illiquid. Use adjusted close for CAGR.
- **NSE Bulk Deal threshold:** ≥ 0.5% of listed shares per transaction; published EOD same day — not subject to the 90-day shareholding lag.
- **FII/DII flow freshness:** `institutional_flows` updated EOD T+0; agents read T+1 onward. `session_date` >2 trading days old = stale; reduce institutional signal weight.
