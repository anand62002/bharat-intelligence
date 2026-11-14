# Work package status

Updated by `/execute-wp` (see `docs/FABLE_ARCHITECT_REVIEW.md`, Stage C).
States: TODO · IN_PROGRESS · PR_OPEN · MERGED · BLOCKED (reason) · NEEDS_HUMAN (action).

Plan: `workplan.yaml` · Review: `REVIEW.md` · Target: `TARGET_ARCHITECTURE.md` (2026-11-12)

| WP | State | PR | Date | Note |
|---|---|---|---|---|
| WP-001 | TODO | — | — | Confirm the portfolio pipeline publishes on the next run |
| WP-002 | NEEDS_HUMAN | — | — | Apply `add_danger_drop_pct_column.sql` in Supabase |
| WP-003 | TODO | — | — | Freeze agent weights to equal in one config file |
| WP-004 | TODO | — | — | Persist score, gate result and source on outcome rows (backfill run is manual) |
| WP-005 | TODO | — | — | Record every worker job run in the job ledger |
| WP-006 | TODO | — | — | Alert when a job is missing or breaks its invariant |
| WP-007 | TODO | — | — | Check the production schema against a code contract |
| WP-008 | TODO | — | — | Remove production credentials from CI test runs |
| WP-009 | TODO | — | — | Add an exception-policy test with a shrinking allowlist |
| WP-010 | TODO | — | — | Make swallowed failures loud on the decision path |
| WP-011 | TODO | — | — | Run the regime detector before the orchestrator |
| WP-012 | NEEDS_HUMAN | — | — | Create the candidates ledger table (migration) |
| WP-013 | TODO | — | — | Write every screened symbol to the candidates ledger |
| WP-014 | TODO | — | — | Label candidate rows with 30- and 90-day alpha |
| WP-015 | TODO | — | — | Store the gate score for every symbol, honestly named |
| WP-016 | TODO | — | — | Define typed agent results with contract tests |
| WP-017 | TODO | — | — | Fit a shadow probability model on the ledger (needs ≥300 labelled rows) |
| WP-018 | TODO | — | — | Move every number out of the synthesis prompt into code |
| WP-019 | TODO | — | — | Size positions from calibrated probability with an unlock rule |
| WP-020 | TODO | — | — | Cut the judge panel to grounding plus one shadow judge (evidence precondition) |
| WP-021 | TODO | — | — | Publish a daily holdings review from the portfolio pipeline |
| WP-022 | TODO | — | — | Label the backtest honestly in the API |
| WP-023 | TODO | — | — | Report ADR signals and ledger evaluation in the evidence pack |
| WP-024 | TODO | — | — | Refresh the docs and remove the deprecated Breeze job |
