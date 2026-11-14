# ADR-001 — Numbers come from code, narrative from the LLM

**Status:** Proposed · 2026-11-12

## Context

The synthesis prompt asks the LLM for `confidence`, `upside_confidence`, `target`, `upside_pct`,
`danger_drop_pct` and the action, and gives it a table mapping composite score to a confidence
range (`prompts/orchestrator_synthesis.txt:43-52`, `:94-99`). Discovery had the same defect until
2026-11-10: its stated confidence ran inversely to outcomes. LLM-emitted nulls have already crashed
the pipeline once (`_num()` fix, 2026-08-14), and a truncated response drops the whole symbol.

## Options considered

1. **Keep LLM numbers, calibrate them afterwards.** Needs hundreds of resolved portfolio calls that
   do not exist; the mapping would shift with every model upgrade.
2. **Code computes every number; the LLM writes the explanation and may veto downward.**
3. **Remove the LLM from the decision path entirely.** Loses the readable bull/bear case, which is
   useful to a retail reader, and the ability to catch something the rules miss.

## Decision

Option 2. Deterministic code produces action, confidence, entry, target, stop, upside and size.
The LLM receives those values as fixed inputs and returns bull case, bear case, summary, headline
and an optional `veto` with a reason. A veto can only move BUY to HOLD. The LLM cannot change a
number or upgrade an action.

## Consequences

- A synthesis failure costs the prose, never the recommendation; `agents/rationale.py` supplies a
  templated explanation.
- Model upgrades no longer change the meaning of "confidence".
- The portfolio and discovery pipelines share one definition of every number.
- The prompt and its JSON contract get smaller, so truncation is less likely.
- Vetoes must be logged with outcomes so their value can be measured.

## How we will know it was right

- Zero symbols dropped per run for synthesis reasons (`daily_runs.details.reasons.synthesis_failed`
  and `synthesis_fallback` both 0 over 20 consecutive runs).
- `grep -c "confidence" prompts/orchestrator_synthesis.txt` shows the field only as an input.
- After 90 days, vetoed calls have a lower beat-Nifty rate than non-vetoed calls; if not, remove
  the veto.
