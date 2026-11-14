# ADR-003 — Equal agent weights until evidence clears a pre-registered bar

**Status:** Proposed · 2026-11-12

## Context

The composite score weights each agent by its latest non-null `agent_performance.accuracy_90d`
(`scheduler/orchestrator.py:858-870`). In production that table holds six non-null rows. The latest
give technical 0.0 and institutional 100.0; the other agents fall back to 70. After the floor of
1.0 at `scheduler/orchestrator.py:207`, technical carries about 0.2% of the composite and
institutional about 22%. "Accuracy" is absolute-return direction (`scheduler/performance_tracker.py:141-146`),
not alpha, and at least three modules write the column with different definitions, one as a
fraction (`agents/outcome_tracker.py:1176`).

## Options considered

1. **Repair the accuracy job and keep adaptive weights.** Per-agent accuracy cannot be estimated
   from fewer than 100 independent outcomes; the weights would be noise with a learning-loop label.
2. **Equal weights now; adaptive weights later, only through the promotion rule in ADR-006.**
3. **Hand-set weights from the backtest.** The backtest covers only the technical rule.

## Decision

Option 2. Weights are equal across agents and live in `data/decision_config.py`. Regime multipliers
are also switched off until tested, because they multiply weights that were never validated.
`agent_performance` keeps one writer, defines accuracy as beat-Nifty rate, stores percentages, and
is used for reporting only.

## Consequences

- The technical agent returns to the composite at full weight; composite scores will shift on the
  first run after the change.
- `agent_performance` stops influencing decisions, so a bad write there can no longer zero an
  agent.
- The "self-improving weights" claim is withdrawn until earned.
- Any future weighting is a config change in a pull request that cites ledger evidence.

## How we will know it was right

- Worker log on every run: `Agent weights: equal (7 × 0.1429)`.
- `tests/test_decision_config.py` fails if any module other than `data/decision_config.py` defines
  an agent weight.
- After 300 labelled ledger rows, the evidence pack compares the equal-weight composite's
  top-quintile beat rate with each single agent's. If one agent alone beats the composite out of
  sample, that is the trigger to revisit this ADR.
