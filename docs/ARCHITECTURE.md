# Architecture

This document used to be a long prose description (last rewritten 2026-07-19). It drifted from the code:
it still described Sonnet synthesis, GPT-4o-mini judges and uncalibrated confidence long after they changed.

The current architecture lives in **`docs/architecture/`**:

- `REVIEW.md` — the Fable architecture review (2026-11-12): what the system is, where it fails
- `TARGET_ARCHITECTURE.md` — the target design
- `ADR-001` … `ADR-008` — the decisions (numbers from code, candidate ledger, equal weights, judge panel,
  fail-loud contract, evaluation protocol, holdings review, no paid data feed yet)
- `workplan.yaml` + `STATUS.md` — the work packages and their state

Facts about the running system (measured outcomes, run ledger, candidate ledger, schedule, code map) are
generated, not written: `python scripts/build_architect_pack.py` → `docs/architect_pack/CURRENT_STATE.md`.

`CLAUDE.md` at the repo root is the working brief for code sessions.
