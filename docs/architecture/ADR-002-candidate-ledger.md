# ADR-002 — The candidate ledger is the single learning dataset

**Status:** Proposed · 2026-11-12

## Context

The system learns only from promoted calls: 154 resolved rows in six months, about 94 independent.
That is too few to distinguish any score band from a coin flip (`CURRENT_STATE.md:24-25`). Those
rows also lack the inputs needed for analysis: `composite_score` and `validation_kappa` are null on
all 154. Meanwhile discovery screens 200 symbols every day and discards what it computed for the
ones it rejects. Nothing records what the system knew at decision time, so no result can be
reproduced or re-scored.

## Options considered

1. **Keep learning from `recommendation_outcomes` only.** Roughly 15 independent labels a month.
2. **Extend the backtester to replay the full pipeline.** Fundamentals are not available
   point-in-time from current sources, so a replay would leak future data.
3. **Log every screened candidate with its features at decision time, and label it later.**

## Decision

Option 3. Add an append-only `candidates` table. Discovery writes one row per screened symbol per
day — passed or not — with price, Nifty level, pre-screen features, the stage reached
(`SCREENED`, `PRESCREEN_PASS`, `DEEP`, `PROMOTED`), agent features where computed, and a
`feature_version`. The existing poller and outcome tracker write 30- and 90-day alpha back onto
each row. Portfolio reviews and long-horizon picks write to the same table with their own `source`.
Rows are never updated except to add labels.

## Consequences

- About 200 rows a day: roughly 12,000 in a quarter, with 30-day labels arriving from week five.
- Rows on the same day are correlated, so evaluation is walk-forward by date and intervals are
  computed on weekly clusters, not on raw row counts.
- The counterfactual becomes measurable: do symbols that pass the pre-screen outperform those that
  fail it?
- Storage is small (JSONB features, a few KB per row) and well within Supabase limits.
- Requires one migration, applied by hand.
- Labelling 200 extra symbols a day adds yfinance batch calls to the 16:30 job.

## How we will know it was right

- ≥5,000 labelled rows by 2027-02-15 (`select count(*) from candidates where alpha_t30 is not null`).
- The evidence pack reports pre-screen pass vs fail alpha with an interval.
- Any promoted calls can be re-scored from stored features alone, with no live fetch.
