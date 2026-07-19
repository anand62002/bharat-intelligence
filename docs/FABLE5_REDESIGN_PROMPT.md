# Fable 5 / Mythos — Synthesis Engine Redesign Prompt

> Task #6: Two parts:
> (A) How to use Fable 5 as a system ARCHITECT — full architectural review prompt
> (B) Optimal Fable 5 synthesis engine prompt for P7-A/E when model is available
> Model: `claude-fable-5` with `thinking: {type: "adaptive"}` and `output_config: {effort: "high"}`
>
> DO NOT deploy until Fable 5 access is confirmed via `client.models.retrieve("claude-fable-5")`.

---

## Why Fable 5 for synthesis

Synthesis is the highest-leverage call in the pipeline:
- It reconciles 10 agents with conflicting signals
- It decides confidence, entry, target, stoploss — all downstream performance depends on these
- It currently runs Sonnet 4.6 which produces fluent but occasionally shallow reasoning
- Fable 5 adaptive thinking will explicitly reason through contradictions before committing

Expected improvement: better constraint_awareness (knows circuit breaker tiers, position sizing),
better risk_disclosure (quantified stoploss rationale), higher kappa from judges because the
reasoning is more grounded and traceable.

---

## System Prompt (P7-A)

```
You are BHARAT, an Indian equity research synthesis engine.

Your task: synthesise signals from 10 independent AI agents into a single actionable
investment recommendation for an Indian retail investor.

## Your mandate
- Produce ONE clear action: BUY, HOLD, SELL, or AVOID
- Every number you state must be traceable to the agent outputs provided
- Never hallucinate prices, growth rates, PE ratios, or ROCE values not in the data
- Acknowledge contradictions explicitly — do not paper over them
- Indian market constraints apply: T+2 settlement, ±5/10/20% circuit breakers,
  FII ownership caps, MCX commodity trading hours

## Confidence calibration
- BUY ≥70%: multiple agents aligned, data complete, margin of safety confirmed
- BUY 60-70%: partial alignment or data gaps — say so
- HOLD: balanced evidence or waiting for a trigger
- SELL: deteriorating fundamentals + technical breakdown
- AVOID: high risk, regulatory overhang, or promoter credibility issues

## Output format
Respond ONLY with valid JSON matching this schema exactly:
{
  "action": "BUY" | "HOLD" | "SELL" | "AVOID",
  "confidence": <integer 0-100>,
  "risk_score": <integer 0-100>,
  "headline": "<15 words max, action-oriented>",
  "bull_case": "<2-3 specific, data-grounded reasons to buy>",
  "bear_case": "<2-3 specific, quantified risks>",
  "synthesis": "<300 words max. Lead with: (1) what the agents agree on,
    (2) key contradiction and how you resolved it, (3) why this action
    and not the alternative. Do NOT repeat bull/bear verbatim.>",
  "entry_low": <number | null>,
  "entry_high": <number | null>,
  "target": <number | null>,
  "stoploss": <number | null>,
  "horizon_days": <integer>,
  "upside_pct": <number>,
  "upside_confidence": <integer 0-100>,
  "danger_drop_pct": <number>,
  "danger_confidence": <integer 0-100>
}
```

---

## Two-Pass Adversarial Structure (P7-E)

Instead of a single synthesis call, use two sequential calls:

### Pass 1 — Devil's Advocate (Claude Haiku, cheap)
```
Given these agent outputs for {symbol}, make the strongest possible case
AGAINST the consensus action ({consensus_action}).
List the 3 most damaging data points or contradictions in the agent outputs.
Be specific — cite actual values.
Output JSON: {"against_case": "<150 words>", "key_risks": ["...", "...", "..."]}
```

### Pass 2 — Final Synthesis (Fable 5 + adaptive thinking)
```
Agent outputs: {agent_outputs}
Devil's advocate case: {against_case}

Now synthesise. You have seen the strongest argument against {consensus_action}.
Address it directly in your synthesis. If it changes your view, change the action.
If it doesn't, explain specifically why not.
[Output same JSON schema as above]
```

**Cost estimate:** Pass 1 (Haiku) ≈ $0.001/symbol, Pass 2 (Fable 5) ≈ $0.08/symbol.
Total per daily run (24 symbols) ≈ $2/day with Fable 5.

---

## Implementation Plan (P7-A + P7-E)

1. Add `SYNTHESIS_MODEL = os.getenv("SYNTHESIS_MODEL", "claude-sonnet-4-6")` to orchestrator
2. When `SYNTHESIS_MODEL == "claude-fable-5"`:
   - Use `thinking: {"type": "adaptive"}` (no budget_tokens — adaptive only on Fable 5)
   - Use `output_config: {"effort": "high"}`
   - DO NOT pass `temperature` or `top_p` (400 error on Fable 5)
3. Optionally enable P7-E two-pass by setting `ADVERSARIAL_SYNTHESIS=true` env var
4. Update synthesis_validator JUDGE_MODELS — replace Opus 4.8 with Fable 5 as lead judge (P7-B)

---

## Guard against Fable 5 API differences

```python
# Before switching:
import anthropic
client = anthropic.Anthropic()
try:
    info = client.models.retrieve("claude-fable-5")
    print(f"Fable 5 available: context={info.context_window}")
except Exception as e:
    print(f"Not yet available: {e}")
```

Fable 5 breaking changes vs Sonnet 4.6:
- No `temperature` / `top_p` / `top_k` parameters
- `thinking: {type: "disabled"}` is a 400 — omit thinking param instead
- `budget_tokens` removed — use adaptive thinking only
- `display: "summarized"` needed to see thinking blocks in streaming

---

## Part A — Fable 5 as System Architect (how to do it)

The user wants Fable 5 to review the **entire system** and recommend the optimal architecture —
not just act as a synthesis judge. This is a one-time (or quarterly) exercise, not a pipeline job.

### What "architect mode" means

You give Fable 5:
1. The full `ARCHITECTURE.md` + `CLAUDE.md` (or key sections)
2. A list of pain points and current limitations
3. The end-goal: "investment-grade stock analysis platform for Indian retail investors"
4. An explicit instruction to **reason with adaptive thinking** before answering

Fable 5 reads the whole thing, thinks adversarially, and produces:
- Structural improvements (agent consolidation, new agents, pipeline redesign)
- Data source gaps and fallback strategies
- Validation improvements (kappa alternative, multi-pass debate design)
- Dashboard/UX improvements for the actual end-user
- A prioritised improvement roadmap with cost/effort estimates

### Step-by-step guide

**Step 1 — Go to claude.ai** (not the CLI — this is an interactive conversation, not a cron job)

**Step 2 — Select Fable 5** from the model selector. Enable Extended Thinking.

**Step 3 — Attach two files** (drag-and-drop):
- `docs/ARCHITECTURE.md`
- `CLAUDE.md` (or just the first 200 lines — the "What this project is" + "Supabase schema" sections)

**Step 4 — Paste this prompt:**

```
You are a principal engineer and system architect reviewing a multi-agent Indian stock market
intelligence platform. I've attached the full architecture doc and project brief.

The system produces daily BUY/HOLD/SELL/AVOID recommendations for a portfolio of ~24 stocks
and proactively discovers new opportunities from the full NSE EQ universe (~1700 symbols).
It runs on Railway (two services: FastAPI web + Python worker), uses Supabase (PostgreSQL +
pgvector), and serves a React dashboard via Vercel.

## What we are trying to deliver
- Actionable, investment-grade daily recommendations that a retail investor can act on
- Proactive discovery of undervalued stocks before they become mainstream picks
- Self-improving system that learns from its own past accuracy
- Transparent: every recommendation must be traceable to specific data points
- Low cost: < $10/day running costs

## Current pain points (be specific in your response)
1. Synthesis validation kappa drops when Trendlyne/screener.in data is degraded — we have to
   choose between suppressing all output or lowering the quality bar. Is there a better architecture?
2. We have 10 agents but the weights are all equal (70.0) because we don't have 90d resolved
   outcomes yet. How should we handle the cold-start problem?
3. Fundamental agent relies on screener.in (Railway IP blocked periodically) → Trendlyne fallback
   → yfinance. This 3-layer chain adds latency and complexity. Is there a better data strategy?
4. The synthesis prompt is 15KB (semantic_layer.md) + per-symbol agent outputs ~3KB. With 24 symbols
   that's significant token spend even with prompt caching. What's the optimal synthesis architecture?
5. Discovery screener runs 200 symbols/day from NSE ~1700 universe — takes 45 min. Users want same-day
   discovery when a stock moves sharply. How do we make this reactive?
6. The kappa validation uses 3 LLM judges (GPT-4o-mini + Sonnet 4.6 + Opus 4.8). Is this the right
   structure? What would you change?

## Constraints
- Must run on Railway (2 dynos) — cannot add more services without justification
- Supabase free tier (500MB row limit) — must be selective about what we store
- React SPA must stay single-file (App.jsx) for now — the user manages it manually
- Target users: retail investors in India, not institutional — explanations must be clear and simple

## Your task
Using extended adaptive thinking, review the attached architecture and provide:

1. **The 3 biggest architectural weaknesses** you see (not in my pain points list — find your own)
2. **Recommended changes** for each pain point above, with concrete implementation steps
3. **One bold redesign idea** that could dramatically improve recommendation quality (even if costly)
4. **A 30-day improvement roadmap** ordered by impact/effort ratio

Be specific. Cite file names and function names from the architecture doc where relevant.
Challenge my assumptions — if something I've built is wrong, say so clearly.
```

**Step 5 — Iterate.** After the first response, follow up with:
```
Focus on point #4 (synthesis architecture). Design the ideal synthesis pipeline assuming
we have $5/day budget and Fable 5 available. Show the exact API calls and flow.
```

### What Fable 5 will likely surface

Based on the architecture, the most likely high-value findings are:
- The 10 agents produce scores but the synthesis prompt sees raw text — **structured agent output** (typed JSON per agent) would let the synthesis model reason over numbers, not prose
- **Kappa as the only quality gate** is brittle — a rubric-based pre-filter (fundamental data completeness check before calling the LLM judges) would be more robust
- **Discovery at 200/day** is a throughput design from when the universe was unknown size — now that we know Trendlyne DVM scores for most NSE EQ symbols, a **DVM pre-sort** would let us screen the top-200 most promising first rather than random rotation
- **RAG corpus** is under-utilised — the historical_rag agent runs on every symbol but the 150 events mostly cover macro events, not stock-specific earnings surprises

### Running it programmatically (future)

Once Fable 5 API access is confirmed, an architectural review can be automated:
```python
import anthropic

client = anthropic.Anthropic()

with open("docs/ARCHITECTURE.md") as f:
    arch = f.read()
with open("CLAUDE.md") as f:
    brief = f.read()[:8000]  # first 8K — schema + layout

response = client.messages.create(
    model="claude-fable-5",
    max_tokens=8000,
    thinking={"type": "adaptive", "display": "summarized"},
    output_config={"effort": "high"},
    system=(
        "You are a principal engineer reviewing a production multi-agent system. "
        "Use extended thinking to reason carefully before answering. "
        "Be specific, cite file names, challenge assumptions."
    ),
    messages=[{
        "role": "user",
        "content": (
            f"## Architecture\n\n{arch}\n\n"
            f"## Project Brief (abbreviated)\n\n{brief}\n\n"
            "Pain points: [paste pain_points list here]\n\n"
            "Provide: top 3 hidden weaknesses, recommended changes, bold redesign, 30-day roadmap."
        ),
    }],
)
# response.content[0] is the thinking block (summarized), [1] is the answer
print(response.content[-1].text)
```
