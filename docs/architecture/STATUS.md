# Work package status

Updated by `/execute-wp` (see `docs/FABLE_ARCHITECT_REVIEW.md`, Stage C).
States: TODO · IN_PROGRESS · PR_OPEN · MERGED · BLOCKED (reason) · NEEDS_HUMAN (action).

Plan: `workplan.yaml` · Review: `REVIEW.md` · Target: `TARGET_ARCHITECTURE.md` (2026-11-12)

| WP | State | PR | Date | Note |
|---|---|---|---|---|
| WP-001 | MERGED | — (merged directly to main by owner; no PR) | 2026-11-15 | `scripts/verify_pipeline_publish.py` added. 2026-11-13 and 2026-11-14 → PUBLISHED, 24/24 holdings each, 0 suppressed, 0 errors (output below) |
| WP-002 | MERGED | — | 2026-11-15 | Verified by read: `recommendations.danger_drop_pct` exists in production (written 2026-11-14) and `daily_runs.pipeline/details` exist; no SCHEMA_DRIFT in run errors |
| WP-003 | MERGED | — (merged directly to main by owner; no PR) | 2026-11-15 | Equal weights via `data/decision_config.py`; regime multipliers off; discovery weights routed through config; outcome_tracker writes %. NEEDS-PROD: worker log shows `Agent weights: equal (7 × 0.1429)`; watch discovery pass counts for a week |
| WP-004 | MERGED | — (merged directly to main by owner; no PR) | 2026-11-15 | Code merged. Backfill run by owner 2026-11-15 02:28 (plain `--run`, backup `logs/outcome_scores_backup_20261115_022824.json`); verified: composite_score non-null 283/300, resolved t90 rows with a score 153/155, re-run finds 0 to update. NEEDS-PROD: new portfolio outcome rows from the next 06:00 run carry validation_kappa. Root cause also included the fact-checker overwriting `gov_check.validation` (kappa lost on every published rec). Dry-run backfill on prod: 235 rows fillable (183 with possibly post-call scores). NEEDS_HUMAN after merge: run `python scripts/backfill_outcome_scores.py --run` (or `--run --exclude-reconfirmed`, 52 rows; see `pr/WP-004.md`), then check `count(*) where composite_score is not null` ≥ 150 |
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

## WP-001 — production check output

`python scripts/verify_pipeline_publish.py --date 2026-11-14` (exit 0):

```
daily_runs (orchestrator): status=OK symbols_processed=24 errors=0
  details.reasons = {'agent_run_failed': 0, 'synthesis_failed': 0, 'synthesis_fallback': 0, 'suppressed_by_judges': 0}
  details.errors  = []
recommendations (is_discovery=false): 24 rows
  AVOID        6
  HOLD         16
  SELL         2
VERDICT: PUBLISHED
```

2026-11-13: same shape: 24 rows (AVOID 3, HOLD 17, SELL 4), PUBLISHED, exit 0.
`daily_runs where pipeline='orchestrator'` = 2 rows; non-discovery non-SUPPRESSED recommendations = 48.
