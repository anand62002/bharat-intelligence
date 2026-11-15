"""
db/contract.py — the columns runtime code WRITES, per table (WP-007, ADR-005).

Why this exists
---------------
`recommendations.danger_drop_pct` was in db/schema.sql but never migrated in
production, so every published-recommendation insert failed with PGRST204 for
months. Migrations are applied by hand; nothing compared what the code writes
with what the database has. scripts/check_schema.py selects every column below
from production (limit 0) and names any that are missing; worker.py runs it at
start-up and the weekly audit runs it on Sundays.

Keep it current: when code starts inserting or updating a new column, add it
here in the same change. tests/test_schema_contract.py fails if a literal key
written to one of these tables is missing from its set.

Sources (2026-11-15): AST scan of every .table(...).insert/update/upsert in
agents/, scheduler/, data/, api/, governance/, worker.py, plus the dynamic
writes the scan cannot see: save_recs_node's _DB_COLUMNS allow-list, the
discovery re-check's update dict, the outcome tracker's horizon columns
(f"price_t{h}" …) and the long-horizon book's position/outcome writes.

The first run against production found three writes to columns that never
existed — portfolio_alerts.portfolio_id (target updater + orchestrator
data-quality alert) and agent_performance.notes (outcome tracker). The code was
fixed to write real columns; they are deliberately not listed here.
"""

from __future__ import annotations

_HORIZONS = (90, 180, 365)
_LH_HORIZONS = (180, 365, 730)


def _horizon_cols(horizons, prefixes) -> set[str]:
    return {f"{p}_t{h}" for h in horizons for p in prefixes}


CONTRACT: dict[str, set[str]] = {
    "recommendations": {
        # save_recs_node._DB_COLUMNS (scheduler/orchestrator.py)
        "symbol", "action", "confidence", "risk_score",
        "entry_low", "entry_high", "target", "stoploss",
        "horizon_days", "upside_pct", "upside_confidence",
        "danger_drop_pct", "danger_confidence",
        "headline", "summary", "agent_signals", "is_discovery",
        "valid_till", "gov_check",
        "suggested_position_pct", "position_label",
        "metadata",
        # governance/performance_tracker.py outcome stamping
        "outcome", "outcome_checked_at", "outcome_price",
    },
    "recommendation_outcomes": {
        "rec_id", "symbol", "action", "entry_price", "rec_date", "nifty_entry",
        "composite_score", "agent_signals", "validation_kappa", "last_updated",
        "price_t30", "nifty_t30", "alpha_t30", "outcome_t30",
        "price_live", "nifty_live", "alpha_live", "return_live", "days_live",
        "live_updated_at",
    } | _horizon_cols(_HORIZONS, ("price", "nifty", "alpha", "outcome")),
    "daily_runs": {
        "run_date", "symbols_processed", "errors", "duration_seconds", "status",
        "pipeline", "details",
    },
    "portfolio_holdings": {
        "symbol", "yf_symbol", "name", "sector", "qty", "avg_buy", "current_price",
        "target_price", "stoploss_price", "notes", "linked_rec_id", "status",
        # P7-A target updater
        "original_target", "target_updated_at", "protect_gains_flag",
        "stoploss_ratchet_level", "last_review_at",
    },
    "portfolio_alerts": {
        "severity", "alert_type", "title", "detail", "resolved",
        "holding_id", "symbol",
    },
    "paper_portfolio_positions": {
        "rec_id", "symbol", "yf_symbol", "action", "entry_date", "entry_price",
        "quantity", "allocation_inr", "position_label", "stoploss_price",
        "target_price", "nifty_entry", "current_price", "current_value",
        "unrealized_pnl", "unrealized_pnl_pct", "status", "exit_date",
        "exit_price", "nifty_exit", "realized_pnl", "realized_pnl_pct",
        "alpha_pct", "exit_reason", "updated_at",
    },
    "paper_portfolio_snapshots": {
        "snapshot_date", "total_invested", "total_current_value", "unrealized_pnl",
        "realized_pnl", "total_pnl", "total_pnl_pct", "open_positions",
        "closed_positions", "nifty_value", "nifty_return_pct", "alpha_pct",
    },
    "agent_performance": {
        "agent_name", "audit_date", "accuracy_90d", "hallucination_rate",
        "trend",
    },
    "discovery_runs": {
        "run_date", "slice_symbols", "passed_symbols", "discovery_symbols",
        "coverage_stats", "total_screened", "total_passed", "total_discoveries",
    },
    "long_horizon_picks": {
        "symbol", "pick_date", "score", "components", "thesis", "entry_price",
        "market_cap_cr", "metadata", "status", "status_reason", "last_reviewed_at",
        "nifty_entry",
    } | _horizon_cols(_LH_HORIZONS, ("price", "nifty", "alpha")),
    "long_horizon_positions": {
        "pick_id", "symbol", "yf_symbol", "entry_date", "entry_price", "quantity",
        "allocation_inr", "tier", "nifty_entry", "current_price", "peak_price",
        "stop_price", "unrealized_pnl_pct", "status", "exit_date", "exit_price",
        "exit_reason", "realized_pnl_pct", "alpha_pct",
    },
}
