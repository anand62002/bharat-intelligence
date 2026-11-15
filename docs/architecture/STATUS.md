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
| WP-005 | MERGED | — (merged directly to main by owner; no PR) | 2026-11-15 | `tracked_job` wraps all 26 worker jobs (OK/WARNING/FAILED rows + output + LLM tokens); orchestrator/discovery log only crashes. 22 daily jobs scheduled. NEEDS-PROD: distinct pipelines per day ≥ 18 |
| WP-006 | MERGED | — (merged directly to main by owner; no PR) | 2026-11-15 | `scheduler/invariants.py` (7 jobs) + 19:00 `job_watchdog` → CRITICAL `JOB_INVARIANT` alerts, deduped per job/day; on `/api/system/health` and the weekly audit. NEEDS-PROD: live drill |
| WP-007 | MERGED | — (merged directly to main by owner; no PR) | 2026-11-15 | `db/contract.py` + `scripts/check_schema.py` (worker start-up + weekly audit). First prod run found 3 writes to columns that never existed (target-updater alerts, data-quality alert, sentiment flag) — fixed; check now exits 0 |
| WP-008 | MERGED | — (merged directly to main by owner; no PR) | 2026-11-15 | CI uses dummy keys + `-m "not integration"`; conftest guard fails any test building a live Supabase/Anthropic/OpenAI client; 80 offending tests fixed. Suite passes with keys unset (3612). NEEDS-HUMAN: delete repo secrets `SUPABASE_SERVICE_KEY`, `ANTHROPIC_API_KEY`, `SUPABASE_URL`; optionally delete 3 test rows from `forward_estimates_cache` |
| WP-009 | MERGED | — (merged directly to main by owner; no PR) | 2026-11-15 | AST policy test + allowlist of 173 silent broad handlers (only shrinks); new silent handlers fail CI. Test-only |
| WP-010 | MERGED | — (merged directly to main by owner; no PR) | 2026-11-15 | 29 silent handlers → WARNING (orchestrator, discovery, target updater, outcome tracker, paper book, portfolio monitor); synthesis failures reach the run ledger; allowlist 173 → 144 |
| WP-011 | MERGED | — (merged directly to main by owner; no PR) | 2026-11-16 | Regime detector 06:30 → 05:45 IST so the 06:00 orchestrator reads today's regime; schedule-order test added. NEEDS-PROD: today's market_regime row exists at the 06:00 run |
| WP-012 | MERGED | — (merged directly to main by owner; no PR) | 2026-11-16 | `candidates` table: migration applied by owner 2026-11-16 (count 0); mirrored in schema.sql; added to db/contract.py — check_schema exits 0 |
| WP-013 | MERGED | — (merged directly to main by owner; no PR) | 2026-11-16 | `data/candidate_ledger.py`; discovery writes one row per screened symbol (features from the pre-screen) and updates deep-analysed rows; multibagger picks recorded. NEEDS-PROD: ≥150 discovery rows after the next 10:30 run |
| WP-014 | IN_PROGRESS | — (branch `wp/WP-014-label-candidates`, local) | 2026-11-16 | `label_candidates()` at 18:30: t30/t90 alpha (same formula as recommendation_outcomes), one batch download per run date via a shared price-on-date helper; no-price rows counted failed. NEEDS-PROD: details.output.candidates_labelled; ≥100 labelled from ~2026-12-17 |
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
