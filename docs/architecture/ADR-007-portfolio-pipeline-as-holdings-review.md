# ADR-007 — The portfolio pipeline becomes a holdings review

**Status:** Proposed · 2026-11-12 · Depends on ADR-001

## Context

The 06:00 pipeline analyses the user's open holdings (24 today) and asks an LLM for a BUY / HOLD /
SELL / AVOID call with an entry zone and a 12-month target (`scheduler/orchestrator.py:785-845`,
`prompts/orchestrator_synthesis.txt`). For a stock already owned, an entry zone is the wrong
question; the user needs to know whether to keep, trim or sell. That guidance already exists
elsewhere: `agents/target_updater.py::exit_signal()` returns EXIT, BOOK_PARTIAL, TRAIL, REVIEW or
HOLD with a reason, deterministically. The pipeline has never saved a published row, so nothing
downstream depends on its current output. Once it does publish, `agents/paper_portfolio.py:319-327`
will open a paper position for each BUY, mixing holdings calls into the discovery track record.

## Options considered

1. **Keep it as a recommendation engine for held stocks.** Wrong question; pollutes the paper book.
2. **Retire the pipeline and rely on the 17:00 target updater.** Loses the daily thesis check from
   the feature agents.
3. **Repurpose it as a daily holdings review.**

## Decision

Option 3. For each holding the pipeline produces one review per day:

- `review_action`: EXIT / BOOK_PARTIAL / TRAIL / REVIEW / HOLD, from `exit_signal()`.
- `thesis_status`: INTACT / WEAKENED / BROKEN, by rule from the feature agents — the same test the
  discovery re-check already applies.
- Current stop and target, which never move against the holder.
- A short narrative from the LLM under ADR-001.

Reviews are stored in `recommendations` with `metadata.kind = "HOLDING_REVIEW"`, one live row per
holding, superseding the previous day's. They are excluded from the 90-day paper book. Fresh BUY
ideas come only from discovery and the long-horizon screen. `POST /api/analyse` keeps its on-demand
single-symbol behaviour.

## Consequences

- The Portfolio tab and the pipeline give one answer per holding instead of two that can disagree.
- "Published nothing" becomes impossible by design: a holding always has a review.
- The paper book measures one strategy.
- Exit advice becomes measurable: record the price at each EXIT or BOOK_PARTIAL signal and compare
  with the price 30 and 90 days later.
- The 06:00 and 17:00 jobs share logic; the target updater stays the writer of stop and target.

## How we will know it was right

- Every trading day: count of live `HOLDING_REVIEW` rows equals count of open holdings.
- Zero `HOLDING_REVIEW` rows referenced by `paper_portfolio_positions`.
- After 90 days: holdings flagged EXIT underperform holdings flagged HOLD over the following 30
  days. If they do not, the exit rule needs revisiting.
