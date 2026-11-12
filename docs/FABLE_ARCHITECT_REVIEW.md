# Fable Architect Review — process, prompt, and execution playbook

> Replaces `docs/FABLE5_REDESIGN_PROMPT.md` (2026-07-19), whose pain points and
> synthesis design are obsolete: synthesis already runs on `claude-opus-5-5`,
> the GPT judge is `gpt-6-luna`, confidence is calibrated, and the July list of
> "pain points" has mostly been fixed or superseded.
> Last updated: 2026-11-12.

The idea: use the most capable model (**Claude Fable 5.1**) for the one job where
its depth pays off most — deciding *what the system should become* — and use
Opus 5.5 / Sonnet 5.5 for the many smaller jobs of *building it*. The handoff
between the two is a machine-readable work plan, not a chat transcript.

```
 Stage A          Stage B                   Stage C                      Stage D
 ─────────        ────────────────          ──────────────────────       ───────────
 Evidence   ──►   Fable: architect    ──►   Opus/Sonnet: execute   ──►   Verify &
 pack             review → target           one work package per         re-plan
 (script)         architecture + ADRs       PR, CI-gated, you merge      (monthly)
                  + workplan.yaml
```

---

## Stage A — Build the evidence pack (2 minutes)

```bash
python scripts/build_architect_pack.py
```

Writes `docs/architect_pack/CURRENT_STATE.md` from the **code and the live
database**: measured outcomes, calibration bands, paper-portfolio P&L, backtest,
pipeline health, worker schedule, module map, recent defect patterns and OPEN
findings. Aggregates only — no personal holdings. Regenerate it every time; it
is the reviewer's ground truth, because prose docs drift.

**Documents for the review** (in priority order):

| File | Why | Notes |
|---|---|---|
| `docs/architect_pack/CURRENT_STATE.md` | Facts and measured outcomes | Regenerate first |
| `CLAUDE.md` | Module-by-module technical brief, schema, conventions, history | Current |
| `EXECUTION_PLAN.md` | What has been built, what is pending | Current |
| `prompts/orchestrator_synthesis.txt` + `docs/semantic_layer.md` | The synthesis contract | Current |
| `docs/ARCHITECTURE.md` | Business narrative of the agents | **Stale since 2026-07-19** — read for intent, not for current design |

---

## Stage B — The architect review with Fable

### Where to run it (recommended: Claude Code, read-only)

Run it **in Claude Code, in this repo, with Fable 5.1 selected and Plan mode on**
(read-only — it can read every file but cannot edit). Docs summarise; the code
is the truth, and Fable can open `scheduler/orchestrator.py` to check a claim
instead of trusting a summary. It also writes its outputs straight into
`docs/architecture/` once you approve.

Fallback: claude.ai with Fable 5.1 and the files above attached. Works, but the
review is limited to what the documents say.

Expect a long turn. Fable thinks at length on hard problems; don't interrupt it.

### The prompt

Paste this as the first message:

````
You are the principal architect for Bharat Intelligence, a multi-agent Indian
equity research system. I want an evidence-based architecture review and a
forward-looking target architecture that other models (Claude Opus 5.5 and
Sonnet 5.5) will implement as a series of small, independently shippable
work packages.

Read first, in this order:
1. docs/architect_pack/CURRENT_STATE.md  — generated today from code + live DB
2. CLAUDE.md                              — technical brief, schema, conventions
3. EXECUTION_PLAN.md                      — what is built / pending
4. prompts/orchestrator_synthesis.txt and docs/semantic_layer.md
5. docs/ARCHITECTURE.md                   — business intent only; it is stale
Then open whatever source files you need to verify claims. Where a document and
the code disagree, the code wins — note the disagreement.

## What the system must deliver
- Recommendations a retail investor in India can act on, whose stated
  confidence is honest (calibrated against real outcomes).
- Proactive discovery: 90-day ideas AND rare long-horizon compounders.
- Clear exit guidance on holdings (when to sell / book / trail).
- A system that measures itself and improves from its own track record.
- Every number traceable to its data source.

## What the evidence says (verify, don't trust)
- The daily portfolio pipeline has published no recommendations for months:
  every non-discovery row is SUPPRESSED, yet daily_runs reports success.
- Live discovery calls beat Nifty ~46–50% at 90 days; the raw score used to be
  shown as "confidence" ran inversely to outcomes. A technical-only backtest
  shows ~62–66% (with survivorship bias).
- The 90-day paper book: −1.8% total, −0.4% alpha vs the same trades in Nifty,
  27.5% win rate; every position the same size.
- A recurring defect pattern: silent failures (exceptions swallowed at DEBUG,
  wrong dict keys, docstring-only features, mislabeled metrics).

## Constraints
- Runtime: Railway (one FastAPI web service + one APScheduler worker), Supabase
  Postgres + pgvector, React SPA on Vercel. Adding a service needs justification.
- Budget: keep LLM + data spend under ~$10/day unless a change clearly pays for itself.
- Data: screener.in / Trendlyne scraping (proxy-backed), yfinance, NSE/BSE public
  endpoints. No paid market-data feed today — say if one is worth buying.
- The dashboard is being redesigned separately; treat UI as a consumer of the API.
- One maintainer. Prefer fewer moving parts over cleverness.
- Model IDs live only in data/model_config.py.

## Deliverables (write each as a file under docs/architecture/ when I approve)
1. REVIEW.md
   - Verdict on the current architecture in ≤ 300 words.
   - The 5 most important problems, ranked by impact on decision quality,
     each with evidence (file:line or a number from CURRENT_STATE.md).
   - Root-cause hypothesis for the suppressed-pipeline finding and how to confirm it.
   - What to KEEP — parts that are sound and should not be rebuilt.
2. TARGET_ARCHITECTURE.md
   - The system as it should be in ~3 months: components, data flow, the
     decision/validation design, how confidence is calibrated, how the system
     learns from outcomes, observability (how a silent failure becomes loud).
   - A migration path from today's system — no big-bang rewrite.
3. ADR-NNN-<slug>.md — one Architecture Decision Record per significant
   decision: context, options considered, decision, consequences, how we will
   know it was right (a measurable signal).
4. workplan.yaml — the implementation plan, using exactly this schema per item:

   - id: WP-001
     title: <imperative, ≤ 10 words>
     adr: ADR-NNN            # or null
     why: <one sentence tied to evidence>
     depends_on: []          # WP ids
     risk: low | medium | high
     autonomy: auto | review | manual
       # auto   = an agent may implement + open a PR unattended (low risk,
       #          no migration, no prod data change, fully test-covered)
       # review = agent implements, human reviews before merge
       # manual = needs a human decision or action (migration, spend, prod data)
     model: claude-opus-5-5 | claude-sonnet-5-5 | claude-fable-5-1
     touches: [paths]
     db_migration: null | db/migrations/<file>.sql   # applied manually, never by an agent
     prod_data_change: null | <description + dry-run/backup plan>
     steps: [ordered, concrete steps]
     acceptance:             # each must be checkable by a command or a query
       - <e.g. "pytest tests/test_x.py passes">
       - <e.g. "≥1 non-SUPPRESSED portfolio rec on the next daily run">
     tests: [new or updated test files]
     rollback: <how to undo>
     effort_hours: <estimate>

   Size each work package to ONE pull request (≤ ~400 changed lines). Put the
   suppressed-pipeline investigation first. Order the rest by impact/effort.

## How to work
- Use your full reasoning. Challenge my assumptions and earlier decisions,
  including ones in CLAUDE.md, if the evidence says they're wrong.
- Distinguish what you verified in code from what you are inferring.
- Prefer measurable acceptance criteria over adjectives.
- Before writing files, give me the REVIEW verdict and the top-5 list in chat
  and wait for my go-ahead.
````

### Good follow-up prompts

- *"Stress-test TARGET_ARCHITECTURE.md: what are the three most likely ways it fails in production, and which WP mitigates each?"*
- *"Re-check workplan.yaml for hidden dependencies and anything marked `auto` that can touch production data. Fix the plan."*
- *"Which single change would most improve the honest hit rate of recommendations within 30 days? Show the evidence chain."*
- *"Design the outcome-learning loop end to end: what data, what update rule, what guardrail stops it from overfitting to a hot streak?"*

Commit the outputs (`docs/architecture/`) before Stage C so every executor reads the same plan.

---

## Stage C — Execute the plan (Opus / Sonnet)

Each work package becomes **one branch → one PR**. CI (`.github/workflows/ci.yml`)
runs the test suite on every PR to `main`; Railway and Vercel deploy only what
you merge. That PR gate is the safety boundary for any level of autonomy.

### Option 1 — step by step (start here)

In Claude Code, with Opus 5.5 selected:

```
/execute-wp WP-001
```

The project command (`.claude/commands/execute-wp.md`) makes the agent read the
plan + ADR, refuse anything marked `manual`, implement on a branch `wp/WP-001-…`,
add tests, run the suite, verify each acceptance criterion it can check locally,
open a PR, and update the WP's status in `docs/architecture/STATUS.md`. You
review and merge. Do 3–5 packages this way before trusting more autonomy.

### Option 2 — semi-autonomous (after the first few go well)

Run the `auto`-marked packages unattended, one at a time, each in its own
worktree, headless:

```bash
claude -p "/execute-wp WP-004" --model claude-opus-5-5
```

or schedule them as a Claude Code routine that picks the next `auto` package
whose dependencies are merged. Guardrails that make this safe:

- Only `autonomy: auto` packages — never migrations, prod-data changes or spend.
- One package in flight at a time (no conflicting edits).
- The agent opens PRs; it never pushes to `main` and never merges.
- A failing acceptance check stops the package and records why in STATUS.md.
- You merge — that is the deploy decision.

### Option 3 — Fable as reviewer, Opus as builder

For `risk: high` packages, have Opus implement and then ask Fable in a separate
session: *"Review PR #N against ADR-NNN and the acceptance criteria. Approve or
list blocking issues."* Fable reviewing is far cheaper than Fable building.

---

## Stage D — Verify and re-plan (monthly)

1. Regenerate the evidence pack (`python scripts/build_architect_pack.py`).
2. Compare its numbers with the measurable signals in each ADR.
3. Ask Fable: *"Here is last month's CURRENT_STATE.md and STATUS.md. Which ADRs
   are proving right or wrong? Update workplan.yaml."*

That closes the loop: the architecture is judged by the same outcome data the
system's recommendations are.

---

## Cost and model notes

- Fable 5.1 (`claude-fable-5-1`): thinking is always on; control depth with
  `effort`, not budgets. Use `xhigh`/`max` for the review itself. It is priced
  above Opus — spend it on Stage B and high-risk reviews, not on routine edits.
- Opus 5.5 (`claude-opus-5-5`): default executor for `review`/`auto` packages.
- Sonnet 5.5 (`claude-sonnet-5-5`): fine for small, well-specified packages.
- Whatever the review decides about production models, change them only in
  `data/model_config.py` (enforced by `tests/test_model_ids.py`).
