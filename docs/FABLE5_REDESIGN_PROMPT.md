# Fable 5 / Mythos — Synthesis Engine Redesign Prompt

> Task #6: Optimal Claude Fable 5 system prompt for P7-A/E when model becomes available.
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
