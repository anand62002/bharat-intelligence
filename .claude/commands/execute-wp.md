---
description: Implement one work package from docs/architecture/workplan.yaml as a single PR
argument-hint: WP-001
---

Implement work package **$ARGUMENTS** from `docs/architecture/workplan.yaml`.

## 1. Load the contract
- Read the work package `$ARGUMENTS` in `docs/architecture/workplan.yaml`, its ADR
  in `docs/architecture/` (if any), and `docs/architecture/STATUS.md`.
- Check every id in `depends_on` is marked MERGED in STATUS.md. If not, stop and
  report which dependency is missing.
- If `autonomy: manual`, or it has a `db_migration` or `prod_data_change`: do NOT
  implement. List exactly what the human must do (SQL to run, data to change,
  spend to approve) in STATUS.md and stop.
- Read CLAUDE.md conventions. Model IDs only in `data/model_config.py`.

## 2. Implement
- Create branch `wp/$ARGUMENTS-<short-slug>` from an up-to-date `main`. Never
  commit to `main`.
- Touch only the files the package needs (`touches` is a guide, not a fence —
  if you must go wider, say why in the PR).
- Match surrounding code style. No silent failures: log at WARNING or raise —
  never swallow an exception at DEBUG.
- Add or update the tests listed in `tests`, plus a test for every acceptance
  criterion that can be checked offline. Tests must not call live APIs or the
  live database (mock them).
- Do NOT edit `dashboard/src/App.jsx` unless the package requires it — it is
  being redesigned separately; prefer a new component file plus a minimal mount.

## 3. Verify
- Run `python -m pytest tests/ -q --tb=short --ignore=tests/test_research_agent.py --ignore=tests/test_fetchers_integration.py`.
- For each acceptance criterion, record PASS / FAIL / NEEDS-PROD (can only be
  checked after deploy) with the command or query used.
- If anything FAILs and you cannot fix it within the package's scope, stop:
  do not open a PR. Record the failure and your diagnosis in STATUS.md.

## 4. Ship
- Commit with a message that explains why, referencing $ARGUMENTS and the ADR.
- Push the branch and open a PR to `main` whose body contains: the work package
  summary, the acceptance table (PASS / NEEDS-PROD), risk, and the rollback.
  If the `gh` CLI is unavailable, push the branch anyway, write the PR body to
  `docs/architecture/pr/$ARGUMENTS.md`, and report the GitHub compare URL
  (`https://github.com/<owner>/<repo>/compare/main...<branch>`, owner/repo from
  `git remote get-url origin`) so the human can open the PR.
- Never merge, never push to `main`, never apply migrations or change production data.
- Update `docs/architecture/STATUS.md`: `$ARGUMENTS | PR #<n> | <date> | <one-line result>`.

## 5. Report
Reply with: the PR link, the acceptance table, anything NEEDS-PROD the human
should check after merging, and any follow-up work packages you discovered.
