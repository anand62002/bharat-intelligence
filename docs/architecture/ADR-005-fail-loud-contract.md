# ADR-005 — Fail-loud contract: job ledger, invariants, schema check, exception policy

**Status:** Proposed · 2026-11-12

## Context

Five long outages share one shape: something failed, an `except Exception` absorbed it, and a
status somewhere still said success.

| Failure | Hidden for |
|---|---|
| Published-rec insert failing on a missing column | ~6 months |
| tz-naive/aware subtraction in outcome tracking | ~3 months |
| Wrong warren_bot key disabling target extension | months |
| Every Opus judge reply truncated | weeks |
| Orchestrator run-log insert failing | ~6 months |

The runtime code has 473 `except Exception` handlers and 251 `log.debug` calls. `db/schema.sql`
and production have drifted with no check. Agents exchange untyped dicts, and tests mock the same
wrong keys the code reads.

## Options considered

1. **Fix each instance as found.** This is the current approach; the pattern recurs.
2. **Add an external monitoring service (Sentry or similar).** Catches raised exceptions, not
   swallowed ones or missing output; adds a service and a cost.
3. **Make absence of expected output the alarm, inside the existing stack.**

## Decision

Option 3, as four mechanisms.

1. **Job ledger.** Every worker job runs inside `tracked_job(name)`, which writes one `daily_runs`
   row per run through `data/run_log.py`: status, duration, output count, exception text, LLM
   token usage.
2. **Expected-output invariants.** `scheduler/invariants.py` declares, per job, what a healthy day
   looks like. A watchdog at 19:00 IST checks the day's rows and writes a CRITICAL
   `portfolio_alerts` row for any missing run or broken invariant.
3. **Schema contract.** `db/contract.py` lists the table columns the code writes.
   `scripts/check_schema.py` selects each one and reports those production lacks. It runs at worker
   start and in the weekly audit.
4. **Exception policy.** `tests/test_exception_policy.py` walks the AST and fails if an
   `except Exception` body only passes, returns an empty value, continues, or logs below WARNING.
   Existing sites go on an allowlist whose size may only decrease. `agents/contracts.py` defines
   typed agent results, and contract tests assert the keys consumers read.

Telegram delivery stays optional; the alert row and the health endpoint are the baseline.

## Consequences

- A broken job is visible within one day instead of months.
- The allowlist starts large; working it down is several small packages, not one.
- Some handlers that were deliberately quiet will become noisy at first; each needs a decision.
- No new service, no new spend.
- The schema contract must be updated when a column is added, which is the point.

## How we will know it was right

- Every scheduled job has a `daily_runs` row for every day it was due
  (`select pipeline, count(*) from daily_runs where run_date = current_date group by 1`).
- A drill: disable one job's output for a day; a CRITICAL alert appears by 19:05 IST.
- `scripts/check_schema.py` exits 0 against production.
- The exception allowlist count falls each month and is reported in the evidence pack.
- Time from defect introduced to defect detected, for the next silent failure found, is under
  two days.
