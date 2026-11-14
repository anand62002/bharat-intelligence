# ADR-008 — No paid market-data feed yet

**Status:** Proposed · 2026-11-12 · Revisit 2027-02-15

## Context

Data comes from scraping screener.in and Trendlyne through a proxy, plus yfinance and public
NSE/BSE endpoints. The execution plan budgets for an options feed and a fundamentals backup. The
review brief asks whether a paid feed is worth buying.

The evidence does not point at data as the constraint. The fundamentals cache already serves
last-known-good data through source outages. The score built on this data shows no relationship to
outcomes, but nothing recorded lets us say whether better data would change that: the features
used at decision time were never stored. The options agent runs on a VIX proxy and the Breeze
integration is deprecated, yet no analysis shows the options signal affecting a decision.

## Options considered

1. **Buy a fundamentals feed now.** Removes scraping fragility; cost recurring; benefit unmeasured.
2. **Buy an options feed now (P4-D or Quantsapp).** Same objection, and the signal is unproven.
3. **Buy point-in-time fundamentals for backtesting.** The most valuable data product for this
   system, because it would allow an honest pipeline backtest — but expensive, and the ledger will
   produce equivalent forward evidence for free within a quarter.
4. **Buy nothing; measure first.**

## Decision

Option 4. No new data spend this quarter. P4-D (Angel One options) and the Quantsapp line are
deferred. The Breeze token-refresh job and `data/breeze_auth.py` are removed as dead weight in the
docs-and-cleanup package.

Revisit when the candidate ledger can answer two questions:
- What share of candidate rows were scored on FALLBACK or ESTIMATED fundamentals, and do those rows
  have worse outcomes or worse calibration than FULL rows?
- Does the options feature, as it is today, rank outcomes at all?

If degraded-data rows are more than 20% of the ledger and measurably worse, a fundamentals feed is
justified. If the options feature shows a quintile spread, a real options chain is worth pricing.

## Consequences

- Scraping fragility remains; ADR-005's invariants make an outage visible the same day.
- One scheduled job and one deprecated module are removed.
- The ScraperAPI subscription question (OPS-1) is settled by the same data: buy it only if the
  degraded share exceeds 20%.

## How we will know it was right

- By 2027-02-15 the evidence pack reports the degraded-data share and the outcome gap between FULL
  and degraded rows.
- Data spend stays at its current level through the quarter.
- The decision on 2027-02-15 is made from those two numbers, in either direction.
