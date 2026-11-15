"""
agents/contracts.py — what other modules read from each agent's result (WP-016, ADR-005).

Why this exists
---------------
Agents return plain dicts, and consumers read them by string key. A wrong key
fails silently: `target_updater` read warren_bot's `intrinsic_value`, but the
agent emits `intrinsic_value_per_share`. Every lookup returned 0 and target
extension never ran for months. The tests mocked the same wrong key, so they
passed.

This module writes those reads down. tests/test_agent_contracts.py checks every
consumed key against a REAL recorded output of the producing agent
(tests/fixtures/agent_outputs/*.json, recorded by
scripts/record_agent_fixtures.py). It does not check against a mock.

When you add a consumer that reads a new key from an agent result, add it to
CONSUMED_KEYS in the same change. When you rename an agent output key, the test
names every consumer that still reads the old one.

Nothing here changes agent behaviour; the TypedDicts are documentation and
type-checker hints. Agents still return plain dicts.

Sources (2026-11-16): an AST scan of scheduler/, agents/, api/, governance/ and
data/ for reads on variables bound to an agent's result
(`x.get("warren_bot")…`, `results["technical"]…`, `tech_analyse(...)`), plus
functions that take an agent result as a parameter (multibagger `deep_check`,
`score`, `_eps_growth`; target updater `_intrinsic`, `_maybe_review_laggard`).
"""

from __future__ import annotations

from typing import Any, Optional, TypedDict


# ─────────────────────────────────────────────────────────────────────────────
# Typed views — only the keys some other module reads
# ─────────────────────────────────────────────────────────────────────────────

class _Base(TypedDict, total=False):
    signal: str                 # every agent: BUY / HOLD / SELL / NEUTRAL / NO_DATA …
    score: float                # 0–100
    agent_name: str
    detail: dict[str, Any]


class TechnicalResult(_Base, total=False):
    upside_pct: float
    confidence: float
    atr_14: float
    atr_stoploss: float
    atr_stoploss_pct: float
    ohlcv_last_date: str


class FundamentalResult(_Base, total=False):
    upside_pct: float
    data_quality: str           # FULL | FALLBACK | ESTIMATED | PARTIAL | NO_DATA
    danger_drop_pct: float
    danger_confidence: float
    data_as_of: str


class SentimentResult(_Base, total=False):
    pass


class InstitutionalResult(_Base, total=False):
    fii_net_5d: float
    today_fii_net: float


class MacroResult(_Base, total=False):
    raw_macro_score: float      # added by get_sector_adjusted_macro_score()
    sector_outlook: str         # added by get_sector_adjusted_macro_score()


class HistoricalRagResult(_Base, total=False):
    completeness_score: float


class CommoditiesResult(_Base, total=False):
    pass


class MgmtQualityResult(_Base, total=False):
    pass


class WarrenBotResult(_Base, total=False):
    current_price: float
    intrinsic_value_per_share: float    # NOT "intrinsic_value" — see module docstring
    margin_of_safety_pct: float
    conviction_rating: str
    moat_type: str
    moat_strength_score: float
    roce_score: float
    management_score: float
    earnings_score: float
    valuation_score: float
    roce_avg_10yr: float
    ten_year_eps_cagr: float
    promoter_quality: str
    india_consumption_play: bool
    jhunjhunwala_cyclical_flag: bool
    why_buffett_would_like: list
    why_buffett_would_pass: list
    key_risks: list
    data_gaps: list
    error: Optional[str]                # failure path only


AGENT_RESULT_TYPES: dict[str, type] = {
    "technical": TechnicalResult,
    "fundamental": FundamentalResult,
    "sentiment": SentimentResult,
    "institutional": InstitutionalResult,
    "macro": MacroResult,
    "historical_rag": HistoricalRagResult,
    "commodities": CommoditiesResult,
    "mgmt_quality": MgmtQualityResult,
    "warren_bot": WarrenBotResult,
}

# Agents whose signal/score feed the composite and the consensus gate
# (scheduler/orchestrator.py::_composite_score, agents/discovery_screener.py).
DECISION_AGENTS = ("technical", "fundamental", "sentiment", "institutional",
                   "macro", "historical_rag", "commodities")

# ─────────────────────────────────────────────────────────────────────────────
# CONSUMED_KEYS: agent → consumer → keys that consumer reads.
# A key may be a dotted path into nested dicts ("detail.indicators.current_price").
# ─────────────────────────────────────────────────────────────────────────────

_O = "scheduler/orchestrator.py"
_D = "agents/discovery_screener.py"

CONSUMED_KEYS: dict[str, dict[str, frozenset[str]]] = {
    "technical": {
        f"{_O}::_composite_score": frozenset({"signal", "score"}),
        f"{_O}::_build_recommendation": frozenset({"upside_pct", "detail",
                                                   "detail.indicators.current_price"}),
        f"{_O}::_fallback_synthesis": frozenset({"upside_pct"}),
        f"{_O}::synthesise_node": frozenset({"atr_14", "atr_stoploss", "detail"}),
        f"{_O}::save_recs_node": frozenset({"atr_14", "atr_stoploss", "atr_stoploss_pct"}),
        f"{_O}::_portfolio_ledger_row": frozenset({"detail.indicators.current_price"}),
        f"{_O}::_build_rag_description": frozenset({"detail"}),
        f"{_D}::_best_upside": frozenset({"upside_pct", "confidence"}),
        f"{_D}::_upside_basis": frozenset({"signal"}),
        f"{_D}::_save_discovery": frozenset({"atr_stoploss"}),
        f"{_D}::_run_all_agents": frozenset({"detail"}),
        "governance/fact_checker.py::_extract_claims": frozenset({"detail"}),
        "governance/performance_tracker.py::_check_technical_temporal_integrity":
            frozenset({"ohlcv_last_date"}),
    },
    "fundamental": {
        f"{_O}::_composite_score": frozenset({"signal", "score"}),
        f"{_O}::_build_recommendation": frozenset({"upside_pct", "danger_drop_pct",
                                                   "danger_confidence", "detail",
                                                   "detail.valuation.current_price",
                                                   "detail.valuation.entry_low",
                                                   "detail.valuation.entry_high",
                                                   "detail.valuation.target"}),
        f"{_O}::_fallback_synthesis": frozenset({"upside_pct", "danger_drop_pct",
                                                 "danger_confidence"}),
        f"{_O}::synthesise_node": frozenset({"data_quality", "detail",
                                             "detail.valuation.current_price",
                                             "detail.years_available", "detail.data_quality"}),
        f"{_O}::save_recs_node": frozenset({"data_quality", "detail.years_available"}),
        f"{_O}::_portfolio_ledger_row": frozenset({"data_quality",
                                                   "detail.valuation.current_price"}),
        f"{_D}::_best_upside": frozenset({"upside_pct", "score"}),
        f"{_D}::_upside_basis": frozenset({"signal", "upside_pct", "detail"}),
        f"{_D}::run_discovery": frozenset({"data_quality"}),
        f"{_D}::_sector_from_fundamental": frozenset({"detail"}),
        "api/main.py::_transform_recommendation": frozenset({"data_quality"}),
        "governance/performance_tracker.py::_check_fundamental_temporal_integrity":
            frozenset({"data_as_of"}),
    },
    "sentiment": {
        f"{_O}::_composite_score": frozenset({"signal", "score"}),
        "agents/outcome_tracker.py::run_sentiment_validation":
            frozenset({"signal", "score", "dominant_event_class"}),
    },
    "institutional": {
        f"{_O}::_composite_score": frozenset({"signal", "score"}),
        f"{_O}::save_recs_node": frozenset({"today_fii_net"}),
        f"{_D}::_upside_basis": frozenset({"fii_net_5d"}),
    },
    "macro": {
        f"{_O}::_composite_score": frozenset({"signal", "score"}),
        f"{_O}::_run_agents_for_symbol": frozenset({"signal", "score", "raw_macro_score",
                                                    "sector_outlook"}),
        f"{_O}::_build_rag_description": frozenset({"detail"}),
        f"{_D}::run_discovery": frozenset({"signal", "score"}),
        f"{_D}::_best_upside": frozenset({"signal"}),
        f"{_D}::_upside_basis": frozenset({"signal"}),
        f"{_D}::_run_all_agents": frozenset({"detail"}),
    },
    "historical_rag": {
        f"{_O}::_composite_score": frozenset({"signal", "score", "completeness_score"}),
        f"{_D}::_best_upside": frozenset({"signal"}),
        f"{_D}::_upside_basis": frozenset({"signal"}),
    },
    "commodities": {
        f"{_O}::_composite_score": frozenset({"signal", "score"}),
    },
    # mgmt_quality's result is stored with the recommendation, but no module
    # reads a key from it today — nothing to contract.
    "mgmt_quality": {},
    "warren_bot": {
        f"{_O}::synthesise_node": frozenset({
            "score", "conviction_rating", "moat_type", "moat_strength_score", "roce_score",
            "management_score", "earnings_score", "valuation_score",
            "intrinsic_value_per_share", "margin_of_safety_pct", "ten_year_eps_cagr",
            "roce_avg_10yr", "promoter_quality", "india_consumption_play",
            "jhunjhunwala_cyclical_flag", "why_buffett_would_like", "why_buffett_would_pass",
            "key_risks", "data_gaps", "years_available"}),
        f"{_O}::save_recs_node": frozenset({"score", "conviction_rating", "moat_type",
                                            "intrinsic_value_per_share",
                                            "margin_of_safety_pct", "years_available"}),
        f"{_D}::_save_discovery": frozenset({"signal", "score", "margin_of_safety_pct",
                                             "error"}),
        "agents/target_updater.py::_intrinsic": frozenset({"intrinsic_value_per_share"}),
        "agents/target_updater.py::_maybe_review_laggard": frozenset({"signal"}),
        "agents/target_updater.py::_warren_analyse": frozenset({"error"}),
        "agents/multibagger_screener.py::deep_check": frozenset({"error", "signal",
                                                                 "roce_avg_10yr",
                                                                 "ten_year_eps_cagr"}),
        "agents/multibagger_screener.py::score": frozenset({"roce_avg_10yr"}),
        "agents/multibagger_screener.py::_eps_growth": frozenset({"ten_year_eps_cagr"}),
        "agents/multibagger_screener.py::run_screen": frozenset({"current_price", "score",
                                                                 "moat_type"}),
        "agents/multibagger_screener.py::_ledger_row": frozenset({"signal", "score"}),
    },
}

# _format_agent_outputs builds the synthesis prompt from every decision agent.
# (It reads `detail` with an {} default, so commodities — which has no detail — is fine.)
for _a in DECISION_AGENTS:
    CONSUMED_KEYS[_a][f"{_O}::_format_agent_outputs"] = (
        frozenset({"signal", "score"}) if _a == "commodities"
        else frozenset({"signal", "score", "detail"}))
del _a

# ─────────────────────────────────────────────────────────────────────────────
# Keys that exist only on some paths. The contract test accepts these when
# absent from the recorded output, but each needs a reason.
# ─────────────────────────────────────────────────────────────────────────────

PATH_DEPENDENT: dict[tuple[str, str], str] = {
    ("warren_bot", "error"):
        "set only on warren_bot's failure path; consumers treat absence as success",
    ("macro", "raw_macro_score"):
        "added per symbol by agents.macro.get_sector_adjusted_macro_score(), not by analyse()",
    ("macro", "sector_outlook"):
        "added per symbol by agents.macro.get_sector_adjusted_macro_score(), not by analyse()",
}

# ─────────────────────────────────────────────────────────────────────────────
# KNOWN_MISSING — consumers read these but NO agent emits them. Each one is a
# live bug that this package records rather than fixes (WP-016 changes no
# behaviour). The list may only shrink: the test fails if a key here starts
# appearing in the recorded output (then delete the entry) and fails on any
# NEW consumed key that is missing.
# ─────────────────────────────────────────────────────────────────────────────

KNOWN_MISSING: dict[tuple[str, str], str] = {
    ("fundamental", "detail.valuation.current_price"):
        "fundamental never emits detail.valuation, so the synthesis prompt's {current_price} "
        "is 'N/A' for every holding and _build_recommendation's valuation fallbacks are empty",
    ("fundamental", "detail.valuation.entry_low"): "no detail.valuation (see current_price)",
    ("fundamental", "detail.valuation.entry_high"): "no detail.valuation (see current_price)",
    ("fundamental", "detail.valuation.target"): "no detail.valuation (see current_price)",
    ("fundamental", "detail.years_available"):
        "P7-C data-density firewall reads it from fundamental.detail or warren_bot; neither "
        "emits it, so the <5-year confidence cap never fires",
    ("warren_bot", "years_available"): "see fundamental detail.years_available",
    ("fundamental", "detail.data_quality"):
        "data_quality is top-level on the fundamental result; synthesise_node's detail read "
        "falls through to the top-level key, so this one is harmless",
    ("sentiment", "dominant_event_class"):
        "run_sentiment_validation reads it from stored agent_signals; sentiment never emits "
        "it, so event_class is always NULL in the validation rows",
}


def lookup(result: Any, path: str) -> tuple[bool, Any]:
    """(present, value) for a dotted path into nested dicts."""
    cur = result
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False, None
        cur = cur[part]
    return True, cur


def missing_keys(agent: str, output: dict) -> dict[str, list[str]]:
    """Consumed keys absent from `output` → the consumers that read them (allowlists ignored)."""
    out: dict[str, list[str]] = {}
    for consumer, keys in CONSUMED_KEYS.get(agent, {}).items():
        for k in keys:
            if not lookup(output, k)[0]:
                out.setdefault(k, []).append(consumer)
    return out


def all_consumed(agent: str) -> set[str]:
    return set().union(*CONSUMED_KEYS.get(agent, {}).values()) if CONSUMED_KEYS.get(agent) else set()
