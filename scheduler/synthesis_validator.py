"""
scheduler/synthesis_validator.py — Pre-publication Agreement-Gated Validation
==============================================================================
Three independent LLM judges (GPT-4o-mini, Claude Sonnet, Claude Opus) score a
synthesised recommendation across 5 rubrics before it is published to the database.

Judge diversity (P1-C):
  Judge 1 — GPT-4o-mini   (OpenAI)    — independent provider, different training
  Judge 2 — Claude Sonnet (Anthropic) — primary synthesis model
  Judge 3 — Claude Opus   (Anthropic) — highest-quality Anthropic judge

Using three Claude variants was a known gap (correlated sampling — all three could
agree on the same hallucination).  GPT-4o-mini as judge 1 breaks this correlation.

A quality-weighted inter-rater kappa is computed per dimension and in aggregate:

    quality   = (mean_judge_score − 1) / 4        normalised 1-5 → 0–1
    agreement = mean pairwise(1 − |si − sj| / 4)  linear-weighted agreement 0–1
    kappa_d   = quality × agreement                combined quality+reliability 0–1

  aggregate_kappa = unweighted mean of 5 dimension kappas

Publication rules (evaluated in priority order):
  1. aggregate_kappa < 0.50        → SUPPRESSED  (skip DB write; log for human review)
  2. constraint_awareness < 0.50   → QUALIFIED   (append constraint caveat)
  3. data_provenance < 0.50        → QUALIFIED   (append provenance caveat)
  4. otherwise                     → PASS

Note on single-judge fallback:
  If only 1 judge responds for a dimension, agreement cannot be measured.
  A 50 % reliability penalty is applied: kappa_d = quality × 0.5.
  If 0 judges respond, kappa_d = 0.0 (treated as failed).

Entry point:
    from scheduler.synthesis_validator import validate_synthesis, ValidationOutcome
    outcome: ValidationOutcome = await validate_synthesis(
        symbol, synthesis_data, agent_results, ant_client
    )
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger(__name__)

# ── Model identifiers (overridable via env vars) ──────────────────────────────
# Judge 1: GPT-4o-mini — independent provider (requires OPENAI_API_KEY)
#   Falls back to claude-haiku if OPENAI_API_KEY is not set.
# Judge 2: Claude Sonnet — mid-tier Anthropic
# Judge 3: Claude Opus   — top-tier Anthropic
JUDGE_MODELS: dict[str, str] = {
    "gpt":    os.getenv("JUDGE_MODEL_GPT",    "gpt-4o-mini"),
    "sonnet": os.getenv("JUDGE_MODEL_SONNET", "claude-sonnet-4-5"),
    "opus":   os.getenv("JUDGE_MODEL_OPUS",   "claude-opus-4-5"),
}
JUDGE_MAX_TOKENS = 150   # JSON score + one-sentence rationale
JUDGE_TIMEOUT    = 45    # seconds per judge call


def _is_openai_model(model_id: str) -> bool:
    """True when the model should be called via OpenAI SDK (not Anthropic)."""
    return model_id.startswith(("gpt-", "o1-", "o3-", "o4-"))

# ── Kappa gate thresholds ─────────────────────────────────────────────────────
KAPPA_SUPPRESS    = 0.50   # aggregate below this  → SUPPRESSED
KAPPA_DIM_QUALIFY = 0.50   # critical-dim below this → QUALIFIED
CRITICAL_DIMS     = frozenset({"constraint_awareness", "data_provenance"})

# ── Rubric definitions ────────────────────────────────────────────────────────
# Each rubric is the full text passed to every judge so they score the same
# criterion identically regardless of which model they are.
RUBRICS: dict[str, str] = {
    "constraint_awareness": (
        "Does the recommendation demonstrate awareness of relevant market constraints? "
        "Look for explicit acknowledgment of: (1) circuit breaker tier for this stock "
        "(±5/10/20% daily limit); (2) liquidity constraints if daily volume is low "
        "(<50 000 shares = illiquid, widen entry zone); (3) FII ownership limits if the "
        "stock is near its sector cap; (4) position-sizing proportionality to market cap "
        "(micro-cap BUY at full position = constraint failure); (5) T+2 settlement impact "
        "on entry/exit timing around dividends or corporate actions."
    ),
    "market_state_alignment": (
        "Is the recommended action coherent with the macro and market-state signals present "
        "in the agent outputs? Flag contradictions such as: BUY with high conviction while "
        "India VIX > 20 and FII net-selling; AVOID while VIX < 15 and FII strongly buying; "
        "premium sector PE (>15 % above 5-yr avg) cited as a BUY catalyst; or RBI rate hike "
        "cycle cited as positive for rate-sensitive NBFCs/housing finance."
    ),
    "data_provenance": (
        "Are all specific factual claims (prices, growth rates, PE, ROCE, FII flows) "
        "traceable to the provided agent outputs? Penalise: (1) numbers that appear nowhere "
        "in any agent's data; (2) figures that directly contradict agent-reported values; "
        "(3) qualitative assertions that require external data not visible in the agent "
        "outputs. Pure inferential reasoning ('given PE is X, fair value is Y') is "
        "acceptable. Fabricated or hallucinated data points are not."
    ),
    "logic_coherence": (
        "Is the bull/bear case internally self-consistent, and does the action follow logically? "
        "Check: (1) bull and bear cases do not use the same data point to support opposing "
        "conclusions; (2) action (BUY/HOLD/SELL/AVOID) is proportionate to the balance of "
        "evidence presented; (3) confidence level is calibrated — high confidence requires "
        "strong signal consensus, not conflicting agents; (4) for BUY: entry < current price "
        "≤ target and stoploss < entry; (5) horizon is consistent with the investment thesis "
        "(event-driven = short horizon, structural = long horizon)."
    ),
    "risk_disclosure": (
        "Are material downside risks quantified and disclosed? Require: (1) danger_drop_pct "
        "is populated with a specific number (not null); (2) stoploss level is provided and "
        "rationale is stated; (3) if promoter pledging > 30 %, it appears in the bear case; "
        "(4) if D/E > 1.5 for a non-financial, non-infra company, leverage risk is noted; "
        "(5) sector-specific risks are named (regulatory for pharma/banks, commodity for "
        "metals, demand cycles for auto). Upside-only recommendations score 1."
    ),
}

# ── Caveats appended to synthesis text for QUALIFIED recommendations ──────────
DIMENSION_CAVEATS: dict[str, str] = {
    "constraint_awareness": (
        "⚠ QUALIFIED — Constraint-awareness not fully established: verify position sizing, "
        "circuit-limit exposure, and intraday liquidity before placing entry orders."
    ),
    "data_provenance": (
        "⚠ QUALIFIED — One or more factual claims could not be fully verified against "
        "agent data. Treat specific numerical forecasts with independent scrutiny before acting."
    ),
}

# ── Judge scoring prompt template ─────────────────────────────────────────────
_JUDGE_PROMPT = """\
You are a financial recommendation quality auditor. Score the synthesis below on ONE rubric only.

RUBRIC: {rubric_name}
DEFINITION: {rubric_definition}

---
AGENT DATA (ground truth provided to the synthesis model):
{agent_summary}

---
RECOMMENDATION UNDER REVIEW:
Symbol    : {symbol}
Action    : {action}   Confidence: {confidence}%
Headline  : {headline}
Bull Case : {bull_case}
Bear Case : {bear_case}
Synthesis : {synthesis_text}
Entry zone: ₹{entry_low} – ₹{entry_high}   Target: ₹{target}   Stoploss: ₹{stoploss}
Upside    : {upside_pct}%   Danger drawdown: {danger_drop_pct}%

---
SCORING SCALE:
5 = Fully satisfies rubric — no notable gaps
4 = Mostly satisfies — minor gaps only
3 = Adequate — notable but not critical gaps
2 = Significantly deficient on this rubric
1 = Completely fails this rubric

Respond ONLY with valid JSON — no other text, no markdown:
{{"score": <integer 1-5>, "rationale": "<one concise sentence explaining the score>"}}"""


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DimensionResult:
    """Per-rubric scoring result across all responding judges."""
    name:        str
    scores:      dict[str, int]    # judge_name → 1–5 integer score
    rationales:  dict[str, str]    # judge_name → one-sentence rationale
    quality:     float             # normalised mean score  [0, 1]
    agreement:   float             # pairwise linear-weighted agreement  [0, 1]
    kappa:       float             # quality × agreement  [0, 1]

    @property
    def failed(self) -> bool:
        """True when kappa is below the QUALIFIED trigger threshold."""
        return self.kappa < KAPPA_DIM_QUALIFY

    @property
    def n_judges(self) -> int:
        return len(self.scores)

    def summary(self) -> str:
        scores_str = ", ".join(f"{m}={s}" for m, s in self.scores.items())
        return (
            f"{self.name}: κ={self.kappa:.3f} "
            f"(quality={self.quality:.3f} agreement={self.agreement:.3f}) "
            f"[{scores_str}]"
        )


@dataclass
class ValidationOutcome:
    """Full validation result for one synthesised recommendation."""
    status:             str                        # "PASS" | "QUALIFIED" | "SUPPRESSED"
    aggregate_kappa:    float
    dimensions:         dict[str, DimensionResult]
    failed_dimensions:  list[str]                  # dims where kappa < threshold
    caveats:            list[str]                  # non-empty when QUALIFIED
    suppression_reason: Optional[str]              # populated when SUPPRESSED
    judge_errors:       list[str]
    elapsed_seconds:    float

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a dict safe for Supabase JSONB storage."""
        return {
            "status":              self.status,
            "aggregate_kappa":     round(self.aggregate_kappa, 4),
            "dimension_kappas":    {d: round(r.kappa,     4) for d, r in self.dimensions.items()},
            "dimension_quality":   {d: round(r.quality,   4) for d, r in self.dimensions.items()},
            "dimension_agreement": {d: round(r.agreement, 4) for d, r in self.dimensions.items()},
            "judge_scores":        {d: r.scores           for d, r in self.dimensions.items()},
            "judge_rationales":    {d: r.rationales       for d, r in self.dimensions.items()},
            "failed_dimensions":   self.failed_dimensions,
            "caveats":             self.caveats,
            "suppression_reason":  self.suppression_reason,
            "judge_errors":        self.judge_errors,
            "elapsed_seconds":     round(self.elapsed_seconds, 2),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Kappa mathematics
# ─────────────────────────────────────────────────────────────────────────────

def _pairwise_agreement(scores: list[int]) -> float:
    """
    Linear-weighted pairwise inter-rater agreement for ordinal 1-5 scores.

    Formula: mean over all distinct pairs (i, j) of  1 − |si − sj| / 4
    Returns [0, 1]: 1.0 = perfect agreement; 0.0 = maximum disagreement every pair.

    With 3 judges the denominator is 3 pairs; with 2 judges it is 1 pair.
    """
    n = len(scores)
    if n < 2:
        return 1.0
    pairs = [
        (scores[i], scores[j])
        for i in range(n)
        for j in range(i + 1, n)
    ]
    return sum(1.0 - abs(a - b) / 4.0 for a, b in pairs) / len(pairs)


def _compute_dimension_kappa(scores: list[int]) -> tuple[float, float, float]:
    """
    Given a list of 1–5 judge scores for one rubric, return (quality, agreement, kappa).

    quality   = (mean_score − 1) / 4                ∈ [0, 1]  higher = better synthesis
    agreement = _pairwise_agreement(scores)           ∈ [0, 1]  higher = judges agree more
    kappa     = quality × agreement                   ∈ [0, 1]  combined reliability metric

    Single-judge handling:
      n=1 → agreement = 0.5 (50 % reliability penalty; no cross-check possible)
      n=0 → all values 0.0

    kappa → 1.0 means judges *strongly agree* the synthesis is *high quality*.
    kappa → 0.0 means quality is poor, OR judges disagree strongly, OR both.
    """
    if not scores:
        return 0.0, 0.0, 0.0

    mean_score = sum(scores) / len(scores)
    quality    = max(0.0, min(1.0, (mean_score - 1.0) / 4.0))

    if len(scores) == 1:
        agreement = 0.5    # single judge: no inter-rater check, halve confidence
    else:
        agreement = _pairwise_agreement(scores)

    kappa = round(quality * agreement, 4)
    return round(quality, 4), round(agreement, 4), kappa


# ─────────────────────────────────────────────────────────────────────────────
# Prompt helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_agent_summary(agent_results: dict[str, dict]) -> str:
    """
    Compact one-line-per-agent summary of agent signals for judge context.
    Capped at 20 lines (~600 chars) to stay within judge token budget.
    """
    lines: list[str] = []
    for name, res in agent_results.items():
        if not res:
            continue
        sig   = res.get("signal", "N/A")
        score = res.get("score",  "N/A")
        line  = f"{name}: {sig}  score={score}"
        extras: list[str] = []
        for k in ("upside_pct", "danger_drop_pct", "fii_net_5d"):
            if res.get(k) is not None:
                extras.append(f"{k}={res[k]}")
        # One detail field from the detail dict (e.g. rsi value)
        det = res.get("detail") or {}
        if isinstance(det, dict):
            for dk, dv in list(det.items())[:1]:
                extras.append(f"{dk}={dv}")
        if extras:
            line += f"  [{', '.join(extras)}]"
        lines.append(line)
    return "\n".join(lines[:20])


def _synthesis_fields(symbol: str, sd: dict) -> dict[str, str]:
    """
    Extract string-safe fields from synthesis_data for .format() interpolation
    into the judge prompt.  All None values are replaced with 'N/A'.
    """
    def _s(v: Any, default: str = "N/A") -> str:
        if v is None:
            return default
        if isinstance(v, list):
            return "; ".join(str(x) for x in v)[:300]
        return str(v)[:300]

    return {
        "symbol":         symbol,
        "action":         _s(sd.get("action")),
        "confidence":     _s(sd.get("confidence")),
        "headline":       _s(sd.get("headline"), ""),
        "bull_case":      _s(sd.get("bull_case"),    "Not provided"),
        "bear_case":      _s(sd.get("bear_case"),    "Not provided"),
        "synthesis_text": _s(sd.get("synthesis"),    "Not provided"),
        "entry_low":      _s(sd.get("entry_low")),
        "entry_high":     _s(sd.get("entry_high")),
        "target":         _s(sd.get("target")),
        "stoploss":       _s(sd.get("stoploss")),
        "upside_pct":     _s(sd.get("upside_pct")),
        "danger_drop_pct": _s(sd.get("danger_drop_pct")),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Individual judge call
# ─────────────────────────────────────────────────────────────────────────────

async def _call_anthropic_judge(
    judge_name:  str,
    model_id:    str,
    rubric_name: str,
    prompt:      str,
    ant_client:  Any,
) -> tuple[int, str]:
    """Call a Claude model via Anthropic SDK. Returns (score 1-5, rationale)."""
    try:
        response = await asyncio.wait_for(
            asyncio.to_thread(
                ant_client.messages.create,
                model      = model_id,
                max_tokens = JUDGE_MAX_TOKENS,
                messages   = [{"role": "user", "content": prompt}],
            ),
            timeout=JUDGE_TIMEOUT,
        )
        text = response.content[0].text.strip()
        m = re.search(r"\{.*?\}", text, re.DOTALL)
        raw_json = m.group(0) if m else text
        data      = json.loads(raw_json)
        score     = max(1, min(5, int(data.get("score", 3))))
        rationale = str(data.get("rationale", ""))[:200]
        return score, rationale

    except asyncio.TimeoutError:
        raise RuntimeError(f"{judge_name}/{rubric_name} timed out after {JUDGE_TIMEOUT}s")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{judge_name}/{rubric_name} returned non-JSON: {exc}")
    except Exception as exc:
        raise RuntimeError(f"{judge_name}/{rubric_name} failed: {exc}")


async def _call_openai_judge(
    judge_name:  str,
    model_id:    str,
    rubric_name: str,
    prompt:      str,
) -> tuple[int, str]:
    """
    Call a GPT model via OpenAI SDK.  Returns (score 1-5, rationale).

    Requires OPENAI_API_KEY env var.  If not set, raises RuntimeError
    so the caller falls back to treating this judge as absent.
    """
    oai_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not oai_key:
        raise RuntimeError(
            f"{judge_name}/{rubric_name}: OPENAI_API_KEY not set — "
            "GPT judge unavailable; set OPENAI_API_KEY in Railway env vars"
        )
    try:
        import openai  # type: ignore[import]

        client = openai.OpenAI(api_key=oai_key)
        response = await asyncio.wait_for(
            asyncio.to_thread(
                client.chat.completions.create,
                model      = model_id,
                max_tokens = JUDGE_MAX_TOKENS,
                messages   = [{"role": "user", "content": prompt}],
                temperature= 0.1,   # near-deterministic for scoring consistency
            ),
            timeout=JUDGE_TIMEOUT,
        )
        text = response.choices[0].message.content.strip()
        m = re.search(r"\{.*?\}", text, re.DOTALL)
        raw_json = m.group(0) if m else text
        data      = json.loads(raw_json)
        score     = max(1, min(5, int(data.get("score", 3))))
        rationale = str(data.get("rationale", ""))[:200]
        return score, rationale

    except asyncio.TimeoutError:
        raise RuntimeError(f"{judge_name}/{rubric_name} timed out after {JUDGE_TIMEOUT}s")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{judge_name}/{rubric_name} returned non-JSON: {exc}")
    except Exception as exc:
        raise RuntimeError(f"{judge_name}/{rubric_name} failed: {exc}")


async def _call_judge(
    judge_name:  str,
    model_id:    str,
    rubric_name: str,
    prompt:      str,
    ant_client:  Any,
) -> tuple[int, str]:
    """
    Route to the correct LLM provider based on model_id prefix.
    GPT models → OpenAI SDK.  Claude models → Anthropic SDK.
    Returns (score: int 1-5, rationale: str).
    """
    if _is_openai_model(model_id):
        return await _call_openai_judge(judge_name, model_id, rubric_name, prompt)
    return await _call_anthropic_judge(judge_name, model_id, rubric_name, prompt, ant_client)


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

async def validate_synthesis(
    symbol:         str,
    synthesis_data: dict,
    agent_results:  dict[str, dict],
    ant_client:     Any,
) -> ValidationOutcome:
    """
    Run all 15 judge calls (5 rubrics × 3 models) concurrently, compute
    per-dimension and aggregate kappas, then apply publication rules.

    Individual judge failures are non-fatal: the dimension kappa is computed
    from however many judges responded (minimum 0; kappa = 0 if none respond).

    Args:
        symbol:         NSE/BSE ticker string (e.g. 'RELIANCE.NS').
        synthesis_data: Parsed JSON dict from Claude synthesis step.
        agent_results:  Raw per-agent result dicts from run_agents_node.
        ant_client:     Initialised anthropic.Anthropic() client instance.

    Returns:
        ValidationOutcome with .status ∈ {'PASS', 'QUALIFIED', 'SUPPRESSED'}.
    """
    t0            = time.time()
    judge_errors: list[str] = []

    agent_summary = _build_agent_summary(agent_results)
    syn_fields    = _synthesis_fields(symbol, synthesis_data)

    # ── Build & launch all 15 coroutines concurrently ─────────────────────────
    # Key: (rubric_name, judge_name) → coroutine result or exception
    task_keys:   list[tuple[str, str]] = []
    coroutines:  list                  = []

    for rubric_name, rubric_def in RUBRICS.items():
        prompt = _JUDGE_PROMPT.format(
            rubric_name       = rubric_name,
            rubric_definition = rubric_def,
            agent_summary     = agent_summary,
            **syn_fields,
        )
        for judge_name, model_id in JUDGE_MODELS.items():
            task_keys.append((rubric_name, judge_name))
            coroutines.append(
                _call_judge(judge_name, model_id, rubric_name, prompt, ant_client)
            )

    judge_providers = {
        name: ("openai" if _is_openai_model(mid) else "anthropic")
        for name, mid in JUDGE_MODELS.items()
    }
    log.info(
        "[%s] Validation: running %d judge calls (%d rubrics × %d models: %s)...",
        symbol, len(coroutines), len(RUBRICS), len(JUDGE_MODELS),
        ", ".join(f"{n}={m}({p})" for (n, m), p in zip(JUDGE_MODELS.items(), judge_providers.values())),
    )

    gather_results = await asyncio.gather(*coroutines, return_exceptions=True)

    # ── Parse gathered results ─────────────────────────────────────────────────
    raw: dict[tuple[str, str], tuple[int, str] | None] = {}
    for (rubric_name, judge_name), result in zip(task_keys, gather_results):
        if isinstance(result, Exception):
            err_msg = str(result)
            log.warning("[%s] %s", symbol, err_msg)
            judge_errors.append(err_msg)
            raw[(rubric_name, judge_name)] = None
        else:
            raw[(rubric_name, judge_name)] = result

    # ── Compute per-dimension kappa ────────────────────────────────────────────
    dimensions: dict[str, DimensionResult] = {}

    for rubric_name in RUBRICS:
        scores:     dict[str, int] = {}
        rationales: dict[str, str] = {}

        for judge_name in JUDGE_MODELS:
            result = raw.get((rubric_name, judge_name))
            if result is not None:
                score, rationale             = result
                scores[judge_name]           = score
                rationales[judge_name]       = rationale

        quality, agreement, kappa = _compute_dimension_kappa(list(scores.values()))

        dimensions[rubric_name] = DimensionResult(
            name       = rubric_name,
            scores     = scores,
            rationales = rationales,
            quality    = quality,
            agreement  = agreement,
            kappa      = kappa,
        )
        log.debug("[%s] %s", symbol, dimensions[rubric_name].summary())

    # ── Aggregate kappa ────────────────────────────────────────────────────────
    aggregate_kappa = round(
        sum(d.kappa for d in dimensions.values()) / max(len(dimensions), 1),
        4,
    )

    # ── Identify failed dimensions ─────────────────────────────────────────────
    failed_dims = [name for name, d in dimensions.items() if d.failed]

    # ── Apply publication rules (in priority order) ───────────────────────────
    caveats:            list[str] = []
    status:             str       = "PASS"
    suppression_reason: Optional[str] = None

    if aggregate_kappa < KAPPA_SUPPRESS:
        # Rule 1: aggregate too low → suppress regardless of individual dimensions
        status = "SUPPRESSED"
        dim_detail = ", ".join(
            f"{n}={d.kappa:.3f}" for n, d in dimensions.items()
        )
        suppression_reason = (
            f"Aggregate kappa {aggregate_kappa:.3f} < suppression threshold "
            f"{KAPPA_SUPPRESS}. Dimension breakdown: [{dim_detail}]. "
            f"Failed dims: {failed_dims or 'none (uniformly low quality)'}."
        )
    else:
        # Rule 2/3: check critical dimensions for QUALIFIED
        for dim_name in sorted(CRITICAL_DIMS):   # deterministic order
            if dim_name in failed_dims:
                status = "QUALIFIED"
                if dim_name in DIMENSION_CAVEATS:
                    caveats.append(DIMENSION_CAVEATS[dim_name])

    # ── Log consolidated summary ───────────────────────────────────────────────
    elapsed = round(time.time() - t0, 2)
    dim_summary = "  ".join(f"{n}={d.kappa:.3f}" for n, d in dimensions.items())
    log.info(
        "[%s] Validation %s  aggregate_κ=%.3f  [%s]  %.1fs",
        symbol, status, aggregate_kappa, dim_summary, elapsed,
    )
    if status == "SUPPRESSED":
        log.warning("[%s] SUPPRESSED: %s", symbol, suppression_reason)
    elif status == "QUALIFIED":
        log.warning("[%s] QUALIFIED: failed dims=%s", symbol, failed_dims)
    if judge_errors:
        log.debug(
            "[%s] %d judge error(s): %s",
            symbol, len(judge_errors), judge_errors[:3],
        )

    return ValidationOutcome(
        status             = status,
        aggregate_kappa    = aggregate_kappa,
        dimensions         = dimensions,
        failed_dimensions  = failed_dims,
        caveats            = caveats,
        suppression_reason = suppression_reason,
        judge_errors       = judge_errors,
        elapsed_seconds    = elapsed,
    )
