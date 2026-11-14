# ADR-006 — Evaluation protocol and promotion rule

**Status:** Proposed · 2026-11-12 · Depends on ADR-002

## Context

Decisions about thresholds, weights and models have been made on impressions or on evidence that
did not test the thing being changed. The CRITICAL threshold moved from 100%/70 to 40%/75 on
reasoning alone. The kappa threshold moved from 0.40 to 0.30 to stop total suppression. The
backtest that reports 62–66% tests a technical rule on a survivorship-biased universe, not the live
pipeline. The one place the system does this well is the long-horizon book, whose conviction tier
stays locked until ≥20 picks resolve with ≥60% beating the Nifty.

## Options considered

1. **Continue case-by-case judgement.**
2. **A/B test changes on live calls.** Too few calls to split.
3. **Shadow evaluation on the candidate ledger with a rule fixed in advance.**

## Decision

Option 3. One protocol for every change that affects what a user sees.

**Metrics.**
- Primary: beat-Nifty rate at 90 days, one call per symbol per 30 days.
- Secondary: median alpha; Brier score of the stated probability against the base-rate baseline;
  quintile spread (top fifth minus bottom fifth by score).
- Early read: the same metrics at 30 days, labelled as provisional.

**Method.** Walk-forward only: fit on rows labelled before a date, evaluate on rows created after
it. Intervals are computed on weekly clusters. No in-sample figure appears in the evidence pack.

**Promotion rule.** A candidate score, model, gate, weight or threshold replaces the incumbent only
when, on ≥300 labelled ledger rows spanning ≥8 distinct weeks:
- its top-quintile beat-Nifty rate has a 90% lower bound above 50%, **and**
- it beats the incumbent on the primary metric out of sample, **and**
- for probability models, its Brier score is below the base-rate baseline.

**Demotion rule.** A promoted model that loses to the base-rate baseline for four consecutive
weekly evaluations reverts automatically to the banded rate.

**Change budget.** At most one promotion per calendar month.

**Product language.** Where a band's interval includes 50%, the API returns
`edge_status: "UNPROVEN"` and the dashboard shows that instead of a percentage. CRITICAL requires
the band's 90% lower bound to exceed 55%.

## Consequences

- Nothing can be promoted for roughly the first ten weeks; that is the cost of honest evidence.
- Thresholds stop moving in response to a bad week.
- The technical backtest is relabelled as what it is and stops being cited as system validation.
- "No measured edge" becomes an acceptable, visible state of the product.

## How we will know it was right

- Every change to `data/decision_config.py` in git history cites an evidence-pack section.
- The evidence pack contains the walk-forward table weekly from the first labelled week.
- No more than one promotion per month in `STATUS.md`.
- Twelve months on, promoted changes show the same sign of effect live as in shadow.
