# ADR-004 — Replace the 15-call judge gate with grounding checks and one shadow judge

**Status:** Proposed · 2026-11-12 · Depends on ADR-001

## Context

Before publication, three models score each synthesis on five rubrics: 15 calls per symbol, five
of them on Opus. The result, named "kappa", is mean rubric score × pairwise agreement
(`scheduler/synthesis_validator.py:321-348`) — a quality score, not an agreement statistic. Three
of the five rubrics check whether the text mentions constraints the prompt told the model to
mention. The threshold has been moved by hand (0.40 → 0.30) to stop it suppressing everything.
After six months no outcome row carries a gate score, so whether it predicts returns is unknown.
Once ADR-001 lands, the LLM no longer produces the numbers the rubrics on logic and risk were
written to police.

## Options considered

1. **Keep the panel, retune thresholds.** Continues an unmeasured cost.
2. **Remove all LLM judging now.** Discards a signal before measuring it.
3. **Measure first, then cut.** Store the gate score for every symbol; replace suppression with a
   deterministic grounding check; keep one judge in shadow; remove it if it shows no outcome lift.

## Decision

Option 3, in two steps.

- **Now:** rename the metric `gate_score`, store it for every symbol whether published or not, and
  write it to the candidate ledger.
- **After ≥60 labelled rows carry a gate score:** publication is decided by the deterministic
  grounding check that already exists (`governance/fact_checker.py::_numerical_grounding_check`),
  extended to every number in the narrative. One judge on the `data_provenance` rubric continues in
  shadow. If the top half by gate score does not beat the bottom half on 90-day alpha, the shadow
  judge is removed as well.

A grounding failure regenerates the narrative once and then falls back to the templated
explanation. It never drops the symbol.

## Consequences

- LLM calls per symbol fall from 16 to 2, then possibly to 1.
- "Suppressed" stops being a normal outcome; a recommendation with clean numbers always publishes.
- Model diversity among judges (the reason for the GPT judge) is no longer needed, which removes
  the OpenAI dependency from the daily decision path.
- Hallucinated numbers in prose are caught by comparison with data, which is cheaper and stricter
  than asking a model.

## How we will know it was right

- `daily_runs.details.reasons.suppressed_by_judges` is 0 on every run after the switch.
- Judge calls per symbol ≤ 1, read from the per-run LLM usage recorded by WP-005.
- Grounding check: 0 published narratives containing a number absent from agent data, on a weekly
  sample of 20.
- The evidence pack shows the gate-score-versus-alpha comparison that justified the cut.
