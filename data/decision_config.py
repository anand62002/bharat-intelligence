"""
Decision configuration — the ONLY place agent weights are defined.

Why this file exists (ADR-003, docs/architecture/ADR-003-equal-agent-weights.md):
  The composite score used to weight each agent by its latest
  agent_performance.accuracy_90d. In production that table held six stale rows,
  so the technical agent carried ~0.2% of the composite and institutional ~22%.
  "Accuracy" was absolute-return direction (not alpha), written by three
  modules with different definitions, one of them as a fraction. Per-agent
  accuracy cannot be estimated from the few independent outcomes we have, so
  adaptive weights were noise with a learning-loop label.

Decision: weights are EQUAL across agents. agent_performance is for reporting
only and no longer influences any decision. Regime multipliers are switched off
too — they multiplied weights that were never validated.

Changing this: any move away from equal weights (or turning regime multipliers
back on) is a pull request that edits this file and cites ledger evidence that
clears the promotion rule in ADR-006 (docs/architecture/ADR-006-evaluation-protocol.md).

tests/test_decision_config.py fails if any other runtime module defines an
agent-weight table.

Rollback: set AGENT_WEIGHTS_MODE = "accuracy" to restore the old
agent_performance-driven weights in the portfolio pipeline (discovery then falls
back to its legacy fixed table, LEGACY_DISCOVERY_WEIGHTS).
"""
from __future__ import annotations

# The seven scored agents that feed the composite (warren_bot / mgmt_quality are
# advisory and not part of the composite).
AGENT_NAMES: tuple[str, ...] = (
    "technical", "fundamental", "sentiment",
    "institutional", "macro", "historical_rag", "commodities",
)

# "equal"    → every agent gets 1/len(AGENT_NAMES)   (ADR-003, default)
# "accuracy" → legacy: portfolio pipeline weights from agent_performance.accuracy_90d,
#              discovery uses LEGACY_DISCOVERY_WEIGHTS. Rollback only.
AGENT_WEIGHTS_MODE: str = "equal"

# Regime-specific weight multipliers (agents/regime_detector.py). Off until tested.
REGIME_MULTIPLIERS_ENABLED: bool = False

# Pre-ADR-003 fixed discovery weights, kept only so the rollback is a one-line change.
LEGACY_DISCOVERY_WEIGHTS: dict[str, float] = {
    "technical":      0.20,
    "fundamental":    0.25,
    "sentiment":      0.10,
    "institutional":  0.20,
    "macro":          0.10,
    "historical_rag": 0.10,
    "commodities":    0.05,
}


def equal_weights() -> dict[str, float]:
    """1/N for every agent in AGENT_NAMES."""
    w = 1.0 / len(AGENT_NAMES)
    return {a: w for a in AGENT_NAMES}


def weights_are_equal() -> bool:
    return AGENT_WEIGHTS_MODE == "equal"


def discovery_weights() -> dict[str, float]:
    """Weights used by the discovery screener's composite."""
    return equal_weights() if weights_are_equal() else dict(LEGACY_DISCOVERY_WEIGHTS)


def describe_weights() -> str:
    """Log line for every run, e.g. 'Agent weights: equal (7 × 0.1429)'."""
    if weights_are_equal():
        n = len(AGENT_NAMES)
        return f"Agent weights: equal ({n} × {1.0 / n:.4f})"
    return f"Agent weights: {AGENT_WEIGHTS_MODE}"


# ─────────────────────────────────────────────────────────────────────────────
# Portfolio decision (WP-018, ADR-001): numbers come from code, narrative from the LLM
# ─────────────────────────────────────────────────────────────────────────────
# "code" → scheduler/decision.py computes action, confidence, entry, target, stop,
#          upside, horizon and danger; the LLM writes only bull/bear case, synthesis,
#          headline and an optional veto (BUY → HOLD only).          (default)
# "llm"  → legacy: the LLM returns every number (prompts/orchestrator_synthesis_llm.txt).
#          Rollback only.
DECISION_SOURCE: str = "code"

# Action rule on the equal-weight composite (the P1-D thresholds the fallback has
# used since 2026-07; not yet validated — ADR-006 governs any change).
BUY_MIN_COMPOSITE: float = 75.0
HOLD_MIN_COMPOSITE: float = 58.0
AVOID_MAX_COMPOSITE: float = 30.0     # at or below → AVOID; between this and HOLD_MIN → SELL

# Composite bands for the calibrated confidence (agents/call_calibration.py). The
# edges are the action thresholds above, so a band never straddles two actions.
COMPOSITE_BANDS: list[tuple[float, float]] = [
    (0.0, AVOID_MAX_COMPOSITE + 0.01), (AVOID_MAX_COMPOSITE + 0.01, HOLD_MIN_COMPOSITE),
    (HOLD_MIN_COMPOSITE, BUY_MIN_COMPOSITE), (BUY_MIN_COMPOSITE, 101.0),
]

UPSIDE_CAP_PCT: float = 100.0         # same cap discovery applies (_UPSIDE_CAP)
DECISION_HORIZON_DAYS: int = 90       # the only horizon with outcome data (CALL_HORIZON_DAYS)
ENTRY_ATR_MULTIPLE: float = 1.0       # entry_low = price − 1×ATR14; stop = technical atr_stoploss (2×ATR)
