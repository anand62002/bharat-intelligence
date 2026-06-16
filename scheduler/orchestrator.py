"""
scheduler/orchestrator.py — Bharat Intelligence Daily Orchestrator
==================================================================
LangGraph-based pipeline that coordinates all 7 analysis agents,
runs a Claude Sonnet bull-bear synthesis, and saves recommendations.

Schedule  : 06:00 IST (Asia/Kolkata) daily via APScheduler
Pipeline  : (LangGraph nodes, run sequentially)
  load_symbols  →  load_weights  →  run_agents  →  synthesise
  →  fact_check  →  save_recs  →  monitor       →  log_run

Agents run in two async phases per symbol:
  Phase 1 (parallel): technical, fundamental, sentiment, warren_bot
  Phase 2 (parallel): institutional (+ pledging from fund), historical_rag
  Pre-fetched (once): macro, commodities  (symbol-agnostic)

warren_bot is NON-BLOCKING: its score does not feed into the weighted
confidence calculation. Output is stored under agent_signals["warren_bot"].

Usage:
    python scheduler/orchestrator.py                         # start 06:00 IST scheduler
    python scheduler/orchestrator.py --run-now               # fire once immediately
    python scheduler/orchestrator.py --run-now --dry         # dry run, no DB writes
    python scheduler/orchestrator.py --symbol PREMEXPLN.NS   # ad-hoc single symbol
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, TypedDict

from dotenv import load_dotenv

load_dotenv()

# ── Project root on sys.path ──────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("orchestrator")

# ── Agent imports ─────────────────────────────────────────────────────────────
from agents.technical      import analyse as tech_analyse      # noqa: E402
from agents.fundamental    import analyse as fund_analyse      # noqa: E402
from agents.sentiment      import analyse as sent_analyse      # noqa: E402
from agents.institutional  import analyse as inst_analyse      # noqa: E402
from agents.macro          import analyse as macro_analyse     # noqa: E402
from agents.historical_rag import analyse as rag_analyse       # noqa: E402
from agents.commodities    import analyse as comm_analyse      # noqa: E402
from agents.warren_bot     import analyse as warren_analyse    # noqa: E402
from agents.mgmt_quality   import analyse as mgmt_analyse      # noqa: E402
from agents.discovery_screener import run_discovery             # noqa: E402
from governance.performance_tracker import audit_data_leakage   # noqa: E402

# ── LangGraph ─────────────────────────────────────────────────────────────────
from langgraph.graph import StateGraph, END                    # noqa: E402

# ── Constants ─────────────────────────────────────────────────────────────────
AGENT_NAMES: list[str] = [
    "technical", "fundamental", "sentiment",
    "institutional", "macro", "historical_rag", "commodities",
]
DEFAULT_ACCURACY      = 70.0    # fallback accuracy when agent_performance has no row
CLAUDE_MODEL          = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
CLAUDE_MAX_TOKENS     = 2048
SYNTHESIS_PROMPT_PATH = _ROOT / "prompts" / "orchestrator_synthesis.txt"
SEMANTIC_LAYER_PATH   = _ROOT / "docs"    / "semantic_layer.md"

# ─────────────────────────────────────────────────────────────────────────────
# State
# ─────────────────────────────────────────────────────────────────────────────

class OrchestratorState(TypedDict):
    dry_run:           bool
    symbols:           list[str]
    agent_weights:     dict[str, float]        # normalised weights, sum ≈ 1.0
    current_regime:    Optional[dict]          # today's regime from market_regime table
    symbol_results:    dict[str, dict]         # symbol → {agent_name → result dict}
    recommendations:   list[dict]              # final recommendation dicts
    saved_ids:         list[str]               # Supabase rec IDs saved this run
    errors:            list[str]               # non-fatal error messages
    start_time:        float                   # unix timestamp of pipeline start
    symbols_processed: int


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def _supabase():
    """Return a live Supabase client, or None if credentials are absent."""
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception as exc:
        log.warning("Supabase connect failed: %s", exc)
        return None


def _load_synthesis_prompt() -> str:
    """Load the synthesis prompt template from disk once."""
    try:
        return SYNTHESIS_PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        log.error("Synthesis prompt not found at %s", SYNTHESIS_PROMPT_PATH)
        return ""


def _load_semantic_layer() -> str:
    """
    Load docs/semantic_layer.md — the business-semantics grounding document.

    Injected into every Claude synthesis call as a preamble section so the model
    has explicit definitions for RSI/MACD/PE/ROCE/Debt-Equity, Indian market
    conventions (T+2, circuits, FII/DII), disambiguation rules (promoter vs
    institutional, consolidated vs standalone), and data-source quirks (Screener.in
    quarter lag, NSE bulk deal thresholds, yfinance ticker conventions).

    Research basis: injecting domain-specific semantic context alongside schema
    descriptions has been shown to yield +17–23 pp accuracy gains on financial
    reasoning tasks (analogous to the business-semantics protocol in schema-
    augmented LLM benchmarks) — the model no longer needs to infer what
    "ROCE > 20%" means from the field name alone.

    Returns empty string if the file is missing (prompt degrades gracefully).
    """
    try:
        text = SEMANTIC_LAYER_PATH.read_text(encoding="utf-8")
        log.debug("Semantic layer loaded: %d bytes from %s", len(text), SEMANTIC_LAYER_PATH)
        return text
    except FileNotFoundError:
        log.warning(
            "Semantic layer not found at %s — synthesis will proceed without it. "
            "Run: docs/semantic_layer.md should be present in the repo.",
            SEMANTIC_LAYER_PATH,
        )
        return ""


def _normalise_weights(raw: dict[str, float]) -> dict[str, float]:
    """
    Convert raw accuracy_90d scores per agent into normalised weights
    summing to 1.0. Missing agents receive DEFAULT_ACCURACY.
    """
    scores = {a: max(raw.get(a, DEFAULT_ACCURACY), 1.0) for a in AGENT_NAMES}
    total  = sum(scores.values())
    return {a: round(s / total, 6) for a, s in scores.items()}


def _composite_score(
    agent_results: dict[str, dict],
    weights:       dict[str, float],
    regime:        Optional[str] = None,
) -> float:
    """
    Weighted average of all 7 agent scores (0–100).
    Agents with None, missing, or INSUFFICIENT_DATA scores are excluded from
    the average so a data-starved agent doesn't drag down a valid composite.

    When `regime` is provided, applies regime-specific multipliers to agent
    weights before computing the composite. Weights are re-normalised after
    applying multipliers so they still sum to 1.0.
    """
    # Apply regime multipliers if regime is known
    effective_weights = weights
    if regime:
        try:
            from agents.regime_detector import apply_regime_multipliers
            effective_weights = apply_regime_multipliers(weights, regime)
            log.debug("_composite_score: applied %s regime multipliers", regime)
        except Exception as _re:
            log.debug("_composite_score: regime multipliers unavailable — %s", _re)

    total_w   = 0.0
    total     = 0.0
    default_w = 1.0 / len(AGENT_NAMES)
    for name in AGENT_NAMES:
        res    = agent_results.get(name, {})
        signal = res.get("signal", "")
        # Exclude agents that explicitly reported no usable data
        if signal in ("NO_DATA", "INSUFFICIENT_DATA"):
            log.debug(
                "_composite_score: excluding %s (signal=%s, completeness=%s%%)",
                name, signal, res.get("completeness_score", "?"),
            )
            continue
        score = res.get("score")
        w     = effective_weights.get(name, default_w)
        if score is not None:
            total   += float(score) * w
            total_w += w
    return round(total / total_w, 2) if total_w > 0 else 50.0


def _build_rag_description(symbol: str, results: dict[str, dict]) -> str:
    """Compose a natural-language description of current conditions for RAG lookup."""
    parts  = [f"Stock: {symbol}."]
    tech   = results.get("technical", {}).get("detail", {}) or {}
    fund   = results.get("fundamental", {}).get("detail", {}) or {}
    macro  = results.get("macro", {}).get("detail", {}) or {}

    rsi    = tech.get("rsi", {}).get("value") if isinstance(tech.get("rsi"), dict) else None
    rev    = fund.get("growth", {}).get("revenue_growth") if isinstance(fund.get("growth"), dict) else None
    inr    = macro.get("inr_usd", {}).get("value") if isinstance(macro.get("inr_usd"), dict) else None
    us10y  = macro.get("us10y", {}).get("value") if isinstance(macro.get("us10y"), dict) else None

    if rsi   is not None: parts.append(f"RSI {rsi:.1f}.")
    if rev   is not None: parts.append(f"Revenue growth {rev:.1f}% YoY.")
    if inr   is not None: parts.append(f"INR/USD {inr:.2f}.")
    if us10y is not None: parts.append(f"US 10Y yield {us10y:.2f}%.")
    return " ".join(parts)


def _format_agent_outputs(
    symbol:  str,
    results: dict[str, dict],
    weights: dict[str, float],
) -> str:
    """
    Format all 7 agent outputs as structured text for the Claude synthesis prompt.
    Keeps output concise — max 4 detail sub-fields per agent.
    INSUFFICIENT_DATA agents are rendered as explicit data-gap warnings so
    Claude does not attempt to reason about absent signals.
    """
    lines: list[str] = []
    for name in AGENT_NAMES:
        res    = results.get(name, {})
        signal = res.get("signal", "NO_DATA")
        w      = weights.get(name, 0.0)
        lines.append(f"### {name.upper()} AGENT  (accuracy-weight={w:.4f})")

        if signal == "INSUFFICIENT_DATA":
            score_pct = res.get("completeness_score", 0)
            missing   = res.get("missing_fields") or []
            below     = res.get("below_threshold_fields") or []
            lines.append(f"  Signal : INSUFFICIENT_DATA  (completeness={score_pct}%)")
            lines.append(f"  Score  : EXCLUDED from composite")
            if missing:
                lines.append(f"  Missing fields    : {', '.join(missing)}")
            if below:
                lines.append(f"  Below threshold   : {', '.join(below)}")
            lines.append(f"  NOTE   : Do NOT infer a signal for this agent — treat as data gap.")
        else:
            score  = res.get("score", "N/A")
            detail = res.get("detail") or {}
            lines.append(f"  Signal : {signal}")
            lines.append(f"  Score  : {score}/100")
            if isinstance(detail, dict):
                for k, v in list(detail.items())[:4]:
                    lines.append(f"  {k}: {v}")
            # Key numeric fields that inform the synthesis
            for k in ("upside_pct", "danger_drop_pct", "fii_net_5d", "critical_gold_upside"):
                if k in res and res[k] is not None:
                    lines.append(f"  {k}: {res[k]}")

        lines.append("")
    return "\n".join(lines)


def _extract_json(text: str) -> dict:
    """
    Extract and parse the first complete JSON object from Claude's response.

    Strategy: find the first '{' and the LAST '}' in the text (or fenced block)
    rather than relying on regex greediness, which breaks on nested structures.
    Handles ```json ... ``` fences and bare JSON objects.
    """
    def _parse_between_outer_braces(s: str) -> dict:
        """Slice from first '{' to last '}' and parse as JSON."""
        start = s.find("{")
        end   = s.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("No JSON object delimiters found")
        return json.loads(s[start : end + 1])

    # 1. Prefer a fenced ```json ... ``` block — strip the fence first
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        try:
            return _parse_between_outer_braces(m.group(1))
        except (ValueError, json.JSONDecodeError):
            pass   # fall through to bare-object search

    # 2. Bare JSON object anywhere in the text
    try:
        return _parse_between_outer_braces(text)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"No valid JSON object found in Claude response: {exc}") from exc


def _apply_consensus_gate(
    symbol:         str,
    synthesis_data: dict,
    agent_results:  dict[str, dict],
) -> dict:
    """
    Consensus gate: prevents a single bullish agent from producing a BUY
    recommendation when the broader agent panel disagrees.

    Logic:
      - Count agents with explicit BUY vs AVOID/SELL signals (NEUTRAL excluded).
      - If synthesis action is BUY but fewer than 2 agents are bullish:
          * Only 1 bullish, rest neutral  → reduce confidence 10 pts, add caveat
          * Only 1 bullish, ≥2 bearish   → downgrade action to HOLD, reduce confidence 15 pts
      - If synthesis action is BUY and 0 agents are bullish (pure Claude conviction):
          → downgrade action to HOLD, reduce confidence 20 pts

    Returns modified synthesis_data dict (may be same dict mutated in-place).
    """
    action = str(synthesis_data.get("action", "HOLD")).upper()
    if action not in ("BUY",):
        return synthesis_data  # only gate BUY promotions

    bull_signals  = [
        n for n in AGENT_NAMES
        if str(agent_results.get(n, {}).get("signal", "")).upper() == "BUY"
    ]
    bear_signals  = [
        n for n in AGENT_NAMES
        if str(agent_results.get(n, {}).get("signal", "")).upper() in ("AVOID", "SELL")
    ]

    n_bull = len(bull_signals)
    n_bear = len(bear_signals)

    if n_bull >= 2:
        return synthesis_data  # solid consensus — no adjustment needed

    confidence = float(synthesis_data.get("confidence", 65.0))
    note       = ""

    if n_bull == 0:
        # Pure Claude narrative conviction with no supporting agent signals
        synthesis_data["action"] = "HOLD"
        confidence = max(confidence - 20, 30.0)
        note = (
            f"⚠ CONSENSUS GATE: Downgraded from BUY to HOLD — "
            f"0 of {len(AGENT_NAMES)} agents gave a BUY signal "
            f"(bears: {', '.join(bear_signals) or 'none'}). "
            f"Single-source conviction insufficient for BUY recommendation."
        )
        log.info(
            "[%s] consensus gate: 0 bullish agents → downgraded BUY→HOLD (confidence %.0f→%.0f)",
            symbol, float(synthesis_data.get("confidence", 65)), confidence,
        )

    elif n_bull == 1 and n_bear >= 2:
        # One supporter, two or more active bears — contested, downgrade
        synthesis_data["action"] = "HOLD"
        confidence = max(confidence - 15, 35.0)
        note = (
            f"⚠ CONSENSUS GATE: Downgraded from BUY to HOLD — "
            f"only {bull_signals[0]} is bullish while "
            f"{', '.join(bear_signals)} are bearish. "
            f"Minority conviction insufficient against active bear signals."
        )
        log.info(
            "[%s] consensus gate: 1 bull (%s) vs %d bears → downgraded BUY→HOLD",
            symbol, bull_signals[0], n_bear,
        )

    elif n_bull == 1:
        # One supporter, others neutral — keep BUY but caveat confidence
        confidence = max(confidence - 10, 40.0)
        note = (
            f"⚠ SINGLE-AGENT CONVICTION: BUY driven primarily by {bull_signals[0]} agent "
            f"({len(AGENT_NAMES) - 1} other agents neutral). "
            f"Confidence reduced pending broader signal alignment."
        )
        log.info(
            "[%s] consensus gate: 1 bullish agent (%s), rest neutral — confidence %.0f→%.0f",
            symbol, bull_signals[0], float(synthesis_data.get("confidence", 65)), confidence,
        )

    synthesis_data["confidence"] = round(confidence, 2)
    if note:
        existing = synthesis_data.get("synthesis") or ""
        synthesis_data["synthesis"] = note + ("\n\n" + existing if existing else "")

    return synthesis_data


def _fallback_synthesis(
    symbol:        str,
    composite:     float,
    agent_results: dict[str, dict],
) -> dict:
    """
    Score-based fallback recommendation when the Anthropic API is unavailable
    or the Claude response cannot be parsed.
    """
    # Calibrated thresholds (P1-D): shifted more conservative than original 72/55/35
    # to reduce BUY false positives from low-quality composite scores.
    if   composite >= 75: action = "BUY"
    elif composite >= 58: action = "HOLD"
    elif composite <= 30: action = "AVOID"
    else:                 action = "SELL"

    fund = agent_results.get("fundamental", {})
    tech = agent_results.get("technical", {})

    return {
        "action":            action,
        "confidence":        round(composite, 2),
        "risk_score":        round(100.0 - composite, 2),
        "entry_low":         None,
        "entry_high":        None,
        "target":            None,
        "stoploss":          None,
        "horizon_days":      180,
        "upside_pct":        float(fund.get("upside_pct") or tech.get("upside_pct") or 0),
        "upside_confidence": round(composite, 2),
        "danger_drop_pct":   float(fund.get("danger_drop_pct") or 0),
        "danger_confidence": float(fund.get("danger_confidence") or 0),
        "headline":          (
            f"{action}: {symbol} - composite {composite:.0f}/100"
            " (score-based, Claude unavailable)"
        ),
        "synthesis":         (
            f"Composite score {composite:.1f}/100. "
            "Claude synthesis unavailable; action derived from weighted agent scores."
        ),
        "bull_case": [],
        "bear_case": [],
    }


def _log_suppressed_synthesis(
    symbol:     str,
    synthesis_data: dict,
    outcome,            # ValidationOutcome (avoid circular import — duck-typed)
    dry_run:    bool,
) -> None:
    """
    Persist a SUPPRESSED recommendation to the database for human review.

    The record is written with action='SUPPRESSED' and confidence=0 so it
    is trivially filterable; the full validation breakdown lives in gov_check.
    Does nothing in dry_run mode or when Supabase is unavailable.
    """
    if dry_run:
        log.info("[%s] [DRY RUN] Suppressed rec not written to DB", symbol)
        return

    client = _supabase()
    if not client:
        return

    try:
        row = {
            "symbol":      symbol,
            "action":      "SUPPRESSED",
            "confidence":  0.0,
            "risk_score":  100.0,
            "horizon_days": 0,
            "upside_pct":  0.0,
            "upside_confidence": 0.0,
            # Note: danger_drop_pct / danger_confidence intentionally omitted —
            # SUPPRESSED sentinel rows don't need danger metrics and omitting them
            # avoids PGRST204 schema-cache errors on transient Supabase refreshes.
            "headline":    (
                f"SUPPRESSED: {symbol} — validation gate blocked publication "
                f"(aggregate κ={outcome.aggregate_kappa:.3f})"
            ),
            "summary": synthesis_data.get("synthesis", ""),
            "is_discovery": False,
            "valid_till":   str(date.today()),     # expires today (human-review only)
            "gov_check": {
                "validation": outcome.to_dict(),
                "suppression_reason": outcome.suppression_reason,
            },
            "agent_signals": {},
        }
        client.table("recommendations").insert(row).execute()
        log.info("[%s] Suppressed rec logged to DB for human review", symbol)
    except Exception as exc:
        log.warning("[%s] Failed to log suppressed rec: %s", symbol, exc)


def _build_recommendation(
    symbol:         str,
    synthesis_data: dict,
    agent_results:  dict[str, dict],
    weights:        dict[str, float],
    composite:      float,
) -> dict:
    """
    Merge Claude synthesis output with agent-derived fallback values into
    the final recommendation dict that matches the Supabase schema.
    """
    fund        = agent_results.get("fundamental", {})
    tech        = agent_results.get("technical", {})
    fund_detail = (fund.get("detail") or {})
    valuation   = fund_detail.get("valuation", {}) if isinstance(fund_detail, dict) else {}
    tech_detail = (tech.get("detail") or {})
    targets     = tech_detail.get("targets", {}) if isinstance(tech_detail, dict) else {}

    action     = str(synthesis_data.get("action", "HOLD")).upper()
    confidence = float(synthesis_data.get("confidence", composite))
    risk_score = float(synthesis_data.get("risk_score", 100.0 - composite))
    gov_screen = None   # set below; default None if governance screener fails

    # ── Governance red-flag adjustment ────────────────────────────────────────
    try:
        from agents.governance_screener import screen_governance, adjust_risk_score
        gov_screen = screen_governance(symbol)
        if gov_screen["flag_count"] > 0:
            risk_score = adjust_risk_score(risk_score, gov_screen)
            log.info(
                "[%s] governance flags=%d delta=+%d → risk_score=%.0f",
                symbol, gov_screen["flag_count"],
                gov_screen["risk_score_delta"], risk_score,
            )
    except Exception as exc:
        log.debug("[%s] governance_screener failed (non-blocking): %s", symbol, exc)

    def _f(key: str, *fallbacks) -> Optional[float]:
        v = synthesis_data.get(key)
        if v is not None:
            return float(v)
        for fb in fallbacks:
            if fb is not None:
                return float(fb)
        return None

    entry_low   = _f("entry_low",  valuation.get("entry_low"))
    entry_high  = _f("entry_high", valuation.get("entry_high"))
    target      = _f("target",     valuation.get("target"), targets.get("target"))
    stoploss    = _f("stoploss",   targets.get("stoploss"))
    horizon_days      = int(synthesis_data.get("horizon_days") or 180)
    upside_pct        = float(synthesis_data.get("upside_pct",
                              fund.get("upside_pct") or tech.get("upside_pct") or 0))
    upside_confidence = float(synthesis_data.get("upside_confidence", confidence))
    danger_drop_pct   = float(synthesis_data.get("danger_drop_pct",
                              fund.get("danger_drop_pct") or 0))
    danger_confidence = float(synthesis_data.get("danger_confidence",
                              fund.get("danger_confidence") or 0))

    headline = synthesis_data.get(
        "headline",
        f"{action}: {symbol} — {upside_pct:.0f}% upside, {confidence:.0f}% confidence",
    )
    summary  = synthesis_data.get("synthesis", "")

    agent_signals = {
        name: {
            "signal": agent_results.get(name, {}).get("signal"),
            "score":  agent_results.get(name, {}).get("score"),
            "weight": round(weights.get(name, 0.0), 6),
        }
        for name in AGENT_NAMES
    }

    return {
        "symbol":            symbol,
        "action":            action,
        "confidence":        round(confidence, 2),
        "risk_score":        round(risk_score, 2),
        "entry_low":         round(entry_low, 2)   if entry_low  is not None else None,
        "entry_high":        round(entry_high, 2)  if entry_high is not None else None,
        "target":            round(target, 2)       if target     is not None else None,
        "stoploss":          round(stoploss, 2)     if stoploss   is not None else None,
        "horizon_days":      horizon_days,
        "upside_pct":        round(upside_pct, 2),
        "upside_confidence": round(upside_confidence, 2),
        "danger_drop_pct":   round(danger_drop_pct, 2),
        "danger_confidence": round(danger_confidence, 2),
        "headline":          headline,
        "summary":           summary,
        "agent_signals":     agent_signals,
        "bull_case":         synthesis_data.get("bull_case", []),
        "bear_case":         synthesis_data.get("bear_case", []),
        "composite_score":   round(composite, 2),
        "is_discovery":      False,
        # Governance red flags (from governance_screener)
        "gov_screen":        gov_screen,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Per-symbol parallel agent runner
# ─────────────────────────────────────────────────────────────────────────────

async def _run_agents_for_symbol(
    symbol:      str,
    macro_res:   dict,
    comm_res:    dict,
) -> dict[str, dict]:
    """
    Run all 7 agents for one symbol using two async phases:

    Phase 1 (parallel): technical, fundamental, sentiment
    Phase 2 (parallel): institutional (uses pledging from Phase 1 fund result),
                         historical_rag (uses all Phase 1 context)

    macro and commodities are passed in pre-fetched (symbol-agnostic).
    """
    # ── Phase 1 ──────────────────────────────────────────────────────────────
    # warren_bot and mgmt_quality run in parallel; both are non-blocking
    # (their scores are stored but don't feed into the main confidence calc).
    t_res, f_res, s_res, w_res, mq_res = await asyncio.gather(
        asyncio.to_thread(lambda: tech_analyse(symbol)),
        asyncio.to_thread(lambda: fund_analyse(symbol)),
        asyncio.to_thread(lambda: sent_analyse(symbol)),
        asyncio.to_thread(lambda: warren_analyse(symbol)),
        asyncio.to_thread(lambda: mgmt_analyse(symbol)),
        return_exceptions=True,
    )

    results: dict[str, dict] = {}
    for name, res in (("technical", t_res), ("fundamental", f_res), ("sentiment", s_res)):
        if isinstance(res, Exception):
            log.warning("[%s] %s agent error: %s", symbol, name, res)
            results[name] = {"signal": "NO_DATA", "score": 50, "agent_name": name}
        else:
            results[name] = res

    # warren_bot — stored separately; None on any failure (non-blocking)
    if isinstance(w_res, Exception):
        log.warning("[%s] warren_bot agent error: %s", symbol, w_res)
        results["warren_bot"] = None
    else:
        results["warren_bot"] = w_res

    # mgmt_quality — stored separately; non-blocking
    if isinstance(mq_res, Exception):
        log.warning("[%s] mgmt_quality agent error: %s", symbol, mq_res)
        results["mgmt_quality"] = None
    else:
        results["mgmt_quality"] = mq_res

    # Pre-fetched symbol-agnostic results.
    # Apply sector-specific macro adjustment using the sector from fundamental result
    # so each stock gets a macro score that reflects its own sector's sensitivity
    # (e.g. IT scores higher than Oil&Gas under weak INR, same raw macro score).
    try:
        from agents.macro import get_sector_adjusted_macro_score
        fund_sector = (
            results.get("fundamental", {})
            .get("detail", {})
            .get("sector", "")
        ) or ""
        results["macro"] = get_sector_adjusted_macro_score(macro_res, fund_sector)
        if fund_sector:
            log.debug(
                "[%s] sector-adjusted macro: %s (raw=%s adj=%s outlook=%s)",
                symbol,
                results["macro"].get("signal"),
                results["macro"].get("raw_macro_score"),
                results["macro"].get("score"),
                results["macro"].get("sector_outlook"),
            )
    except Exception as _macro_adj_exc:
        log.debug("[%s] sector macro adjust failed (non-fatal): %s", symbol, _macro_adj_exc)
        results["macro"] = macro_res
    results["commodities"] = comm_res

    # ── Phase 2 ──────────────────────────────────────────────────────────────
    # Extract pledging so institutional has governance context
    pledging: Optional[float] = None
    fund_detail = results["fundamental"].get("detail") or {}
    if isinstance(fund_detail, dict):
        gov = fund_detail.get("governance") or {}
        if isinstance(gov, dict):
            pledging = gov.get("promoter_pledging")

    rag_desc = _build_rag_description(symbol, results)

    i_res, r_res = await asyncio.gather(
        asyncio.to_thread(lambda: inst_analyse(symbol, promoter_pledging=pledging)),
        asyncio.to_thread(lambda: rag_analyse(rag_desc)),
        return_exceptions=True,
    )

    for name, res in (("institutional", i_res), ("historical_rag", r_res)):
        if isinstance(res, Exception):
            log.warning("[%s] %s agent error: %s", symbol, name, res)
            results[name] = {"signal": "NO_DATA", "score": 50, "agent_name": name}
        else:
            results[name] = res

    return results


# ─────────────────────────────────────────────────────────────────────────────
# LangGraph nodes
# ─────────────────────────────────────────────────────────────────────────────

async def load_symbols_node(state: OrchestratorState) -> dict:
    """
    Load the analysis universe from two sources:
    1. WATCHLIST env var (comma-separated symbols)
    2. OPEN positions in Supabase portfolio_holdings table
    Symbols are resolved via symbol_map and deduplicated.

    If state["symbols"] is already populated (via symbols_override in
    run_pipeline), this node is a no-op — it preserves the pre-validated list.
    """
    # Override path: symbols were pre-validated in run_pipeline()
    if state["symbols"]:
        log.info(
            "Symbol override active — skipping env/portfolio load. "
            "Analysing: %s", state["symbols"],
        )
        return {}   # return empty dict = keep existing state unchanged

    symbols: set[str] = set()

    # 1. WATCHLIST env var
    watchlist = os.getenv("WATCHLIST", "")
    for sym in watchlist.split(","):
        sym = sym.strip().upper()
        if sym:
            if not (sym.endswith(".NS") or sym.endswith(".BO")):
                sym += ".NS"
            symbols.add(sym)

    # 2. Portfolio holdings (open positions)
    client = _supabase()
    if client:
        try:
            resp = (
                client.table("portfolio_holdings")
                .select("symbol")
                .eq("status", "OPEN")
                .execute()
            )
            for row in (resp.data or []):
                sym = row.get("symbol", "").strip().upper()
                if sym:
                    if not (sym.endswith(".NS") or sym.endswith(".BO")):
                        sym += ".NS"
                    symbols.add(sym)
        except Exception as exc:
            log.warning("Could not load portfolio symbols from Supabase: %s", exc)

    # Resolve and validate
    from data.symbol_map import is_excluded, resolve_yf  # noqa: E402
    valid: list[str] = []
    for sym in sorted(symbols):
        if is_excluded(sym):
            log.debug("Excluding %s (excluded symbol)", sym)
            continue
        resolved = resolve_yf(sym)
        if resolved:
            valid.append(resolved)

    log.info("Loaded %d symbols to analyse: %s", len(valid), valid)
    return {"symbols": valid}


async def load_weights_node(state: OrchestratorState) -> dict:
    """
    Fetch the latest accuracy_90d for each agent from Supabase agent_performance.
    Normalise so weights sum to 1.0.
    Agents with no performance record receive DEFAULT_ACCURACY (70.0).
    """
    raw: dict[str, float] = {}
    client = _supabase()
    if client:
        try:
            resp = (
                client.table("agent_performance")
                .select("agent_name, accuracy_90d")
                .order("audit_date", desc=True)
                .execute()
            )
            seen: set[str] = set()
            for row in (resp.data or []):
                name = row.get("agent_name", "")
                acc  = row.get("accuracy_90d")
                if name and acc is not None and name not in seen:
                    raw[name] = float(acc)
                    seen.add(name)
        except Exception as exc:
            log.warning("Could not load agent performance weights: %s", exc)

    weights = _normalise_weights(raw)
    log.info(
        "Agent weights: %s",
        {k: f"{v:.4f}" for k, v in weights.items()},
    )

    # ── Load current market regime ───────────────────────────────────��────────
    current_regime = None
    try:
        from agents.regime_detector import load_current_regime
        current_regime = load_current_regime()
        if current_regime:
            log.info(
                "Current regime: %s (confidence=%s%%) — applying weight multipliers",
                current_regime.get("regime"), current_regime.get("confidence"),
            )
        else:
            log.info("No regime data available — using flat weights")
    except Exception as _regime_exc:
        log.warning("Regime load failed (non-fatal): %s", _regime_exc)

    return {"agent_weights": weights, "current_regime": current_regime}


async def run_agents_node(state: OrchestratorState) -> dict:
    """
    Pre-fetch macro and commodities once (symbol-agnostic), then run all
    7 agents in parallel for every symbol concurrently using asyncio.gather().
    """
    symbols = state["symbols"]
    weights = state["agent_weights"]

    if not symbols:
        log.warning("No symbols to analyse — skipping agent execution")
        return {"symbol_results": {}, "symbols_processed": 0, "errors": state["errors"]}

    # ── Pre-fetch symbol-agnostic agents ─────────────────────────────────────
    log.info("Pre-fetching macro and commodities (symbol-agnostic)...")
    macro_res, comm_res = await asyncio.gather(
        asyncio.to_thread(macro_analyse),
        asyncio.to_thread(comm_analyse),
        return_exceptions=True,
    )
    if isinstance(macro_res, Exception):
        log.warning("macro agent pre-fetch failed: %s", macro_res)
        macro_res = {"signal": "NEUTRAL", "score": 50, "agent_name": "macro"}
    if isinstance(comm_res, Exception):
        log.warning("commodities agent pre-fetch failed: %s", comm_res)
        comm_res = {"signal": "NEUTRAL", "score": 50, "agent_name": "commodities"}

    # ── Run all symbols concurrently ─────────────────────────────────────────
    log.info("Running agents for %d symbols concurrently...", len(symbols))

    async def _process(sym: str):
        try:
            results = await _run_agents_for_symbol(sym, macro_res, comm_res)
            return sym, results, None
        except Exception as exc:
            return sym, None, str(exc)

    completed = await asyncio.gather(*[_process(sym) for sym in symbols])

    symbol_results: dict[str, dict] = {}
    errors: list[str] = list(state["errors"])

    for sym, results, err in completed:
        if err:
            log.error("[%s] agent run failed: %s", sym, err)
            errors.append(f"{sym}: {err}")
        else:
            symbol_results[sym] = results
            composite = _composite_score(results, weights)
            log.info("[%s] composite = %.1f", sym, composite)

    return {
        "symbol_results":    symbol_results,
        "symbols_processed": len(symbol_results),
        "errors":            errors,
    }


async def synthesise_node(state: OrchestratorState) -> dict:
    """
    For each symbol, call Claude Sonnet with a structured bull-bear debate prompt
    and parse the JSON response into a final recommendation.
    Falls back to score-based recommendation if Claude is unavailable.
    """
    symbol_results = state["symbol_results"]
    weights        = state["agent_weights"]
    current_regime = state.get("current_regime")
    errors         = list(state["errors"])

    if not symbol_results:
        return {"recommendations": [], "errors": errors}

    prompt_template  = _load_synthesis_prompt()
    semantic_layer   = _load_semantic_layer()
    ant_key          = os.getenv("ANTHROPIC_API_KEY", "")

    if not ant_key:
        log.warning("ANTHROPIC_API_KEY not set — using score-based fallback for all symbols")
    if semantic_layer:
        log.info("Semantic layer injected into synthesis prompt (%d bytes)", len(semantic_layer))
    else:
        log.warning("Semantic layer missing — synthesis accuracy may be reduced")

    # Initialise Anthropic client once
    ant_client = None
    if ant_key and prompt_template:
        try:
            import anthropic
            ant_client = anthropic.Anthropic(api_key=ant_key)
        except ImportError:
            log.warning("anthropic package not installed — pip install anthropic")

    recommendations: list[dict] = []

    # ── Morning Digest context (P6-C wiring) ─────────────────────────────────
    # Fetch today's MORNING digest (or yesterday's CLOSING as fallback) to inject
    # market_mood / nifty_signal / sectors_in_focus into every synthesis prompt.
    # This costs nothing extra — a single DB read shared across all symbols.
    # The morning digest now runs at 05:30 IST, before this orchestrator (06:00),
    # so it will always be available by the time we reach this node.
    _digest_context: str = ""
    try:
        _db = _supabase()
        if _db:
            import datetime as _dt
            _today_str = _dt.date.today().isoformat()
            _yesterday_str = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
            _digest_row = None
            # Try today MORNING first
            _dr = _db.table("market_digests").select(
                "market_mood,nifty_signal,sectors_in_focus,summary,digest_type,digest_date"
            ).eq("digest_type", "MORNING").eq("digest_date", _today_str).limit(1).execute()
            if _dr.data:
                _digest_row = _dr.data[0]
            else:
                # Fallback: yesterday's CLOSING digest
                _dc = _db.table("market_digests").select(
                    "market_mood,nifty_signal,sectors_in_focus,summary,digest_type,digest_date"
                ).eq("digest_type", "CLOSING").eq("digest_date", _yesterday_str).limit(1).execute()
                if _dc.data:
                    _digest_row = _dc.data[0]
            if _digest_row:
                _sectors = _digest_row.get("sectors_in_focus") or []
                if isinstance(_sectors, list):
                    _sectors_str = ", ".join(_sectors) if _sectors else "—"
                else:
                    _sectors_str = str(_sectors)
                _digest_context = (
                    f"\n## TODAY'S MARKET DIGEST "
                    f"({_digest_row.get('digest_type')} — {_digest_row.get('digest_date')})\n"
                    f"Market Mood: **{_digest_row.get('market_mood', 'NEUTRAL')}**\n"
                    f"NIFTY Signal: {_digest_row.get('nifty_signal', 'N/A')}\n"
                    f"Sectors in Focus: {_sectors_str}\n"
                    f"Summary: {_digest_row.get('summary', '')}\n\n"
                    "Use this market context to calibrate your conviction appropriately. "
                    "A BEARISH mood should raise the bar for BUY calls; a BULLISH mood "
                    "should not inflate confidence beyond what the stock fundamentals support.\n\n"
                )
                log.info(
                    "Morning digest injected into synthesis: mood=%s nifty=%s sectors=%s",
                    _digest_row.get("market_mood"), _digest_row.get("nifty_signal"), _sectors_str,
                )
            else:
                log.debug("No morning/closing digest found for %s — synthesis proceeds without digest context", _today_str)
    except Exception as _de:
        log.warning("Morning digest fetch for synthesis failed (non-fatal): %s", _de)

    for symbol, agent_results in symbol_results.items():
        try:
            regime_label = current_regime.get("regime") if current_regime else None
            composite    = _composite_score(agent_results, weights, regime=regime_label)
            agent_text   = _format_agent_outputs(symbol, agent_results, weights)

            synthesis_data: dict = {}

            if ant_client and prompt_template:
                # Resolve current price for the prompt
                fund_detail = (agent_results.get("fundamental", {}).get("detail") or {})
                valuation   = fund_detail.get("valuation", {}) if isinstance(fund_detail, dict) else {}
                current_price = (
                    valuation.get("current_price") if isinstance(valuation, dict) else None
                ) or "N/A"

                # Use explicit .replace() instead of str.format() so that the
                # literal { } braces in the JSON example block inside the prompt
                # template are NOT misinterpreted as Python format placeholders,
                # which would raise KeyError on keys like '\n  "bull_case"'.
                prompt = (
                    prompt_template
                    .replace("{symbol}",          str(symbol))
                    .replace("{agent_outputs}",   agent_text)
                    .replace("{composite_score}", f"{composite:.1f}")
                    .replace("{current_price}",   str(current_price))
                )

                # ── Semantic layer injection ──────────────────────────────────
                # Prepend business-semantics context so the model has explicit
                # definitions for every metric and Indian market convention it
                # encounters in the agent outputs.  Mirrors the protocol that
                # showed +17–23 pp accuracy gains on financial reasoning tasks
                # when domain semantics accompany the schema (analogous to
                # schema-augmented LLM benchmarks for structured data).
                if semantic_layer:
                    prompt = (
                        "## BUSINESS SEMANTICS CONTEXT\n"
                        "The following reference document defines all financial metrics, "
                        "Indian market conventions, disambiguation rules, and data-source "
                        "quirks used in this analysis. Use it to correctly interpret every "
                        "field value in the agent outputs below.\n\n"
                        + semantic_layer
                        + "\n\n---\n\n"
                        + prompt
                    )

                # ── Regime context injection ──────────────────────────────────
                # Prepend current market regime so Claude calibrates conviction
                # appropriately — e.g. RSI oversold in BEAR regime is not the
                # same entry signal as RSI oversold in BULL regime.
                if current_regime:
                    regime_conf = current_regime.get("confidence", 0)
                    regime_line = (
                        f"\n## CURRENT MARKET REGIME\n"
                        f"Regime: **{current_regime.get('regime')}** "
                        f"(confidence: {regime_conf}%)\n"
                        f"  - NIFTY trend: {current_regime.get('nifty_trend')}\n"
                        f"  - India VIX: {current_regime.get('vix_state')}\n"
                        f"  - FII 10d trend: {current_regime.get('fii_trend')}\n"
                        f"  - Market breadth: {current_regime.get('breadth_state')}\n"
                        f"  - Momentum (RSI): {current_regime.get('momentum_state')}\n\n"
                    )
                    prompt = regime_line + prompt

                # ── Morning digest context injection ──────────────────────────
                # Prepend today's market mood / NIFTY signal / sector focus so
                # synthesis calibrates conviction against the current market day.
                # Fetched once before the per-symbol loop (see _digest_context above).
                if _digest_context:
                    prompt = _digest_context + prompt

                log.info("[%s] Calling Claude Sonnet for synthesis...", symbol)
                # Retry up to 3 times on 529 Overloaded / 529-like transient errors.
                # Backoff: 15s → 45s → give up (use fallback synthesis).
                _synthesis_raw: str | None = None
                _last_exc: Exception | None = None
                for _attempt in range(3):
                    try:
                        _resp = await asyncio.to_thread(
                            ant_client.messages.create,
                            model=CLAUDE_MODEL,
                            max_tokens=CLAUDE_MAX_TOKENS,
                            messages=[{"role": "user", "content": prompt}],
                        )
                        _synthesis_raw = _resp.content[0].text
                        break
                    except Exception as _api_exc:
                        _last_exc = _api_exc
                        _err_str = str(_api_exc)
                        if "529" in _err_str or "overload" in _err_str.lower():
                            if _attempt < 2:
                                _wait = 15 * (3 ** _attempt)  # 15s, 45s
                                log.warning(
                                    "[%s] Anthropic 529 Overloaded (attempt %d/3) — retrying in %ds",
                                    symbol, _attempt + 1, _wait,
                                )
                                await asyncio.sleep(_wait)
                            else:
                                log.warning(
                                    "[%s] Anthropic 529 Overloaded after 3 attempts — using fallback synthesis",
                                    symbol,
                                )
                        else:
                            raise  # non-retriable error — bubble up

                if _synthesis_raw is None:
                    # All retries exhausted on 529 — use weighted fallback
                    synthesis_data = _fallback_synthesis(symbol, composite, agent_results)
                else:
                    raw_text = _synthesis_raw
                    try:
                        synthesis_data = _extract_json(raw_text)
                        log.info("[%s] Claude synthesis parsed OK", symbol)
                    except (ValueError, json.JSONDecodeError, KeyError) as parse_err:
                        log.warning(
                            "[%s] Claude JSON parse failed (%s) — using fallback",
                            symbol, parse_err,
                        )
                        log.debug("[%s] Raw Claude response: %.500s", symbol, raw_text)
                        synthesis_data = _fallback_synthesis(symbol, composite, agent_results)
            else:
                synthesis_data = _fallback_synthesis(symbol, composite, agent_results)

            # ── Data leakage audit ────────────────────────────────────────────
            # Pre-synthesis temporal integrity check: verifies technical OHLCV
            # bars, fundamental snapshots, and RAG matched events are not dated
            # after the pipeline start (look-ahead contamination).
            # block_on_leak=False — warnings logged, action never silently
            # downgraded.  Set to True in strict backtesting mode if needed.
            try:
                _signal_ts = datetime.now(timezone.utc)
                _leakage_report = audit_data_leakage(
                    symbol,
                    agent_results,
                    signal_ts=_signal_ts,
                    block_on_leak=False,
                )
                if _leakage_report.leaks:
                    _blocking = [v for v in _leakage_report.leaks if v.severity == "BLOCKING"]
                    _warnings = [v for v in _leakage_report.leaks if v.severity == "WARNING"]
                    if _blocking:
                        log.warning(
                            "[%s] leakage audit: %d BLOCKING + %d WARNING violation(s)",
                            symbol, len(_blocking), len(_warnings),
                        )
                    else:
                        log.info(
                            "[%s] leakage audit: %d WARNING violation(s)",
                            symbol, len(_warnings),
                        )
                    # Store violations in synthesis metadata for downstream reference
                    synthesis_data.setdefault("metadata", {})["leakage_violations"] = [
                        {"agent": v.agent_name, "type": v.leak_type,
                         "severity": v.severity, "details": v.details}
                        for v in _leakage_report.leaks
                    ]
            except Exception as _audit_exc:
                log.debug("[%s] leakage audit error (non-critical): %s", symbol, _audit_exc)

            # ── Consensus gate — prevent single-agent BUY promotions ────────────
            # Applied before earnings guard and validation so adjusted confidence
            # flows through both downstream steps correctly.
            synthesis_data = _apply_consensus_gate(symbol, synthesis_data, agent_results)

            # ── Earnings guard — inject pre-earnings warning ──────────────────
            # If earnings are within 14 days, add context to synthesis prompt
            # and downgrade confidence for CRITICAL events (≤3 days).
            try:
                from agents.earnings_guard import check_pre_earnings
                eg = check_pre_earnings(symbol, days_window=14)
                if eg["has_upcoming_earnings"] and eg["days_until"] is not None:
                    days_u = eg["days_until"]
                    qtr    = eg.get("quarter") or ""
                    eg_line = (
                        f"\n⚠ EARNINGS ALERT: {symbol} reports {qtr} results "
                        f"in {days_u} days ({eg['earnings_date']}).\n"
                    )
                    if eg["warning_level"] == "CRITICAL":
                        eg_line += (
                            "Binary event risk is CRITICAL — do NOT initiate new position. "
                            "Downgrade BUY confidence by 20 points minimum.\n"
                        )
                    else:
                        eg_line += "Consider waiting for results before entry.\n"
                    synthesis_data.setdefault("synthesis", "")
                    synthesis_data["synthesis"] = eg_line + (synthesis_data.get("synthesis") or "")
                    if eg["warning_level"] == "CRITICAL":
                        synthesis_data["confidence"] = max(
                            0, float(synthesis_data.get("confidence", 50)) - 20
                        )
            except Exception as _eg_exc:
                log.debug("[%s] Earnings guard skipped: %s", symbol, _eg_exc)

            # ── Pre-publication validation gate ───────────────────────────────
            # Three independent LLM judges score the synthesis across 5 rubrics.
            # Skipped when: no Anthropic client, dry_run=True, or fallback path.
            # SUPPRESSED → skip this symbol; log for human review.
            # QUALIFIED  → append caveats to synthesis text; mark rec.
            # PASS       → attach validation metadata; proceed normally.
            validation_outcome = None
            _used_claude_synthesis = ant_client and prompt_template
            if _used_claude_synthesis and not state["dry_run"]:
                try:
                    from scheduler.synthesis_validator import validate_synthesis
                    validation_outcome = await validate_synthesis(
                        symbol         = symbol,
                        synthesis_data = synthesis_data,
                        agent_results  = agent_results,
                        ant_client     = ant_client,
                    )
                except Exception as _val_exc:
                    log.warning(
                        "[%s] Validation pipeline error (non-fatal, proceeding): %s",
                        symbol, _val_exc,
                    )

            # Apply validation outcome
            if validation_outcome and validation_outcome.status == "SUPPRESSED":
                log.warning(
                    "[%s] Publication SUPPRESSED — aggregate_κ=%.3f  reason: %s",
                    symbol,
                    validation_outcome.aggregate_kappa,
                    validation_outcome.suppression_reason,
                )
                errors.append(
                    f"{symbol}: SUPPRESSED (κ={validation_outcome.aggregate_kappa:.3f})"
                )
                _log_suppressed_synthesis(
                    symbol, synthesis_data, validation_outcome, state["dry_run"]
                )
                continue   # ← skip _build_recommendation; next symbol

            if validation_outcome and validation_outcome.status == "QUALIFIED":
                if validation_outcome.caveats:
                    existing = synthesis_data.get("synthesis") or ""
                    synthesis_data["synthesis"] = (
                        existing + "  " + "  ".join(validation_outcome.caveats)
                    ).strip()
                log.info(
                    "[%s] Synthesis QUALIFIED — failed dims: %s",
                    symbol, validation_outcome.failed_dimensions,
                )

            rec = _build_recommendation(
                symbol, synthesis_data, agent_results, weights, composite
            )

            # Attach validation metadata into gov_check for audit/dashboard
            if validation_outcome:
                gov = rec.get("gov_check") or {}
                if isinstance(gov, dict):
                    gov["validation"] = validation_outcome.to_dict()
                else:
                    gov = {"validation": validation_outcome.to_dict()}
                rec["gov_check"] = gov

            # ── Warren bot — NON-BLOCKING, stored independently ──────────────
            # warren_bot score does NOT feed into composite / confidence.
            try:
                wb = agent_results.get("warren_bot") or {}
                if wb and wb.get("score") is not None:
                    rec["warren_bot"] = {
                        "score":                   wb["score"],
                        "conviction_rating":        wb["conviction_rating"],
                        "moat_type":               wb["moat_type"],
                        "moat_strength_score":     wb["moat_strength_score"],
                        "roce_score":              wb["roce_score"],
                        "management_score":        wb["management_score"],
                        "earnings_score":          wb["earnings_score"],
                        "valuation_score":         wb["valuation_score"],
                        "intrinsic_value_per_share": wb["intrinsic_value_per_share"],
                        "margin_of_safety_pct":    wb["margin_of_safety_pct"],
                        "ten_year_eps_cagr":       wb["ten_year_eps_cagr"],
                        "roce_avg_10yr":           wb["roce_avg_10yr"],
                        "promoter_quality":        wb["promoter_quality"],
                        "india_consumption_play":  wb["india_consumption_play"],
                        "jhunjhunwala_cyclical_flag": wb["jhunjhunwala_cyclical_flag"],
                        "why_like":                wb["why_buffett_would_like"],
                        "why_pass":                wb["why_buffett_would_pass"],
                        "key_risks":               wb["key_risks"],
                        "data_gaps":               wb.get("data_gaps", []),
                    }
                    log.info(
                        "[%s] warren_bot → score=%d  conviction=%s",
                        symbol, wb["score"], wb["conviction_rating"],
                    )
                else:
                    rec["warren_bot"] = None
                    log.info("[%s] warren_bot → no result (set to None)", symbol)
            except Exception as wb_exc:
                log.warning("[%s] warren_bot attachment failed: %s", symbol, wb_exc)
                rec["warren_bot"] = None

            # ── P3-A: Position sizing ─────────────────────────────────────────
            try:
                from agents.position_sizer import calc_position_size
                wb_attached = rec.get("warren_bot") or {}
                sizing = calc_position_size(
                    upside_pct   = rec.get("upside_pct", 0),
                    confidence   = rec.get("confidence", 0),
                    action       = rec.get("action", "HOLD"),
                    mos_pct      = wb_attached.get("margin_of_safety_pct") if wb_attached else None,
                    warren_score = wb_attached.get("score") if wb_attached else None,
                )
                rec["suggested_position_pct"] = sizing["suggested_position_pct"]
                rec["position_label"]         = sizing["position_label"]
                log.info(
                    "[%s] position sizing → %s (%.2f%%)  MOS=%.1f%% [%s]",
                    symbol, sizing["position_tier"],
                    sizing["suggested_position_pct"],
                    sizing["mos_used"], sizing["mos_source"],
                )
            except Exception as ps_exc:
                log.warning("[%s] position_sizer failed (non-blocking): %s", symbol, ps_exc)
                rec["suggested_position_pct"] = None
                rec["position_label"]         = None

            recommendations.append(rec)
            log.info(
                "[%s] → %s  confidence=%.0f%%  upside=%.1f%%  risk=%.0f  pos=%.2f%%",
                symbol, rec["action"], rec["confidence"], rec["upside_pct"], rec["risk_score"],
                rec.get("suggested_position_pct") or 0,
            )

        except Exception as exc:
            log.error("[%s] synthesis failed: %s", symbol, exc)
            errors.append(f"{symbol} synthesis: {exc}")

    return {"recommendations": recommendations, "errors": errors}


async def fact_check_node(state: OrchestratorState) -> dict:
    """
    Run the governance fact-checker against all synthesised recommendations.
    Verifies 5–8 numerical claims per recommendation using Claude Haiku,
    applies confidence penalties for CONTRADICTED claims, and withholds
    recommendations with too many unverifiable claims.

    In --dry mode, Haiku checks still run (no DB writes from fact_checker itself).
    This node is a no-op if the governance module is unavailable.
    """
    recommendations = state["recommendations"]
    symbol_results  = state["symbol_results"]

    if not recommendations:
        return {}

    try:
        from governance import fact_checker  # noqa: F401
        log.info("Running governance fact-check on %d recommendations...", len(recommendations))
        checked = fact_checker.check_recommendations(
            recommendations,
            symbol_results,
            dry_run=state["dry_run"],
        )
        return {"recommendations": checked}
    except ImportError:
        log.warning("governance.fact_checker not available — skipping fact-check node")
        return {}
    except Exception as exc:
        log.warning("Fact-check node failed: %s", exc)
        return {}


async def save_recs_node(state: OrchestratorState) -> dict:
    """
    Persist recommendations to the Supabase recommendations table.
    In --dry mode, print a formatted summary instead of writing to DB.
    """
    recommendations = state["recommendations"]
    errors          = list(state["errors"])

    if not recommendations:
        log.info("No recommendations to save")
        return {"saved_ids": [], "errors": errors}

    # ── Dry run — print and return ────────────────────────────────────────────
    if state["dry_run"]:
        sep = "-" * 70
        print("\n" + sep)
        print(f"  DRY RUN -- {len(recommendations)} recommendation(s)")
        print(sep)
        for rec in recommendations:
            action = rec["action"]
            sym    = rec["symbol"]
            tag    = {"BUY": "[BUY]", "HOLD": "[HOLD]", "SELL": "[SELL]", "AVOID": "[AVOID]"}.get(action, f"[{action}]")
            print(f"\n{tag}  {sym}")
            print(f"   Composite score : {rec['composite_score']:.1f}/100")
            print(f"   Confidence      : {rec['confidence']:.0f}%")
            print(f"   Risk score      : {rec['risk_score']:.0f}/100")
            print(f"   Upside          : {rec['upside_pct']:.1f}%  "
                  f"(confidence {rec['upside_confidence']:.0f}%)")
            print(f"   Danger drawdown : {rec['danger_drop_pct']:.1f}%  "
                  f"(confidence {rec['danger_confidence']:.0f}%)")
            if rec.get("entry_low") is not None:
                print(f"   Entry zone      : Rs {rec['entry_low']:.2f} - Rs {rec['entry_high']:.2f}")
            if rec.get("target") is not None:
                print(f"   Target          : Rs {rec['target']:.2f}")
            if rec.get("stoploss") is not None:
                print(f"   Stoploss        : Rs {rec['stoploss']:.2f}")
            print(f"   Horizon         : {rec['horizon_days']} days")
            print(f"   Headline        : {rec['headline']}")
            bull = rec.get("bull_case", [])
            bear = rec.get("bear_case", [])
            if bull:
                print("\n   Bull Case:")
                for pt in bull:
                    print(f"      + {pt}")
            if bear:
                print("\n   Bear Case:")
                for pt in bear:
                    print(f"      - {pt}")
            synthesis = rec.get("summary", "")
            if synthesis:
                print(f"\n   Synthesis:\n      {synthesis}")
            # Governance fact-check summary
            gov = rec.get("gov_check")
            if gov and isinstance(gov, dict):
                n_checked     = gov.get("claims_checked", 0)
                n_verified    = gov.get("verified_count", 0)
                n_contradicted= gov.get("contradicted_count", 0)
                n_unverified  = gov.get("unverified_count", 0)
                withheld      = gov.get("withheld", False)
                conf_delta    = gov.get("confidence_delta", 0)
                gov_line = (
                    f"   Gov check       : {n_verified}/{n_checked} verified, "
                    f"{n_contradicted} contradicted, {n_unverified} unverified"
                )
                if conf_delta != 0:
                    gov_line += f"  (conf delta {conf_delta:+.0f})"
                if withheld:
                    gov_line += "  [WITHHELD]"
                print(gov_line)
            # Warren bot summary
            wb = rec.get("warren_bot")
            if wb and isinstance(wb, dict):
                print(f"   Warren Bot      : score={wb.get('score', 'N/A')}/100  "
                      f"conviction={wb.get('conviction_rating', 'N/A')}")
                if wb.get("moat_type"):
                    print(f"   Moat            : {wb['moat_type']}")
                if wb.get("margin_of_safety_pct") is not None:
                    print(f"   Margin of safety: {wb['margin_of_safety_pct']:.1f}%")
                if wb.get("intrinsic_value_per_share") is not None:
                    print(f"   Intrinsic value : Rs {wb['intrinsic_value_per_share']:.2f}")
            elif wb is None:
                print("   Warren Bot      : N/A")
            print()
        print(sep)
        return {"saved_ids": [], "errors": errors}

    # ── Live save ─────────────────────────────────────────────────────────────
    client = _supabase()
    if not client:
        log.error("Supabase unavailable — recommendations not saved")
        errors.append("Supabase unavailable: recommendations not persisted")
        return {"saved_ids": [], "errors": errors}

    # Columns that exist in the recommendations table
    _DB_COLUMNS = {
        "symbol", "action", "confidence", "risk_score",
        "entry_low", "entry_high", "target", "stoploss",
        "horizon_days", "upside_pct", "upside_confidence",
        "danger_drop_pct", "danger_confidence",
        "headline", "summary", "agent_signals", "is_discovery",
        "valid_till", "gov_check",
        "suggested_position_pct", "position_label",   # P3-A position sizing
        "metadata",                                   # discovery context bag (price, sector, risks, etc.)
    }

    saved_ids: list[str] = []
    for rec in recommendations:
        try:
            row = {k: v for k, v in rec.items() if k in _DB_COLUMNS}
            row["valid_till"] = str(
                date.today() + timedelta(days=int(rec.get("horizon_days", 180)))
            )
            # Nest market_constraints inside metadata JSONB (no separate column needed)
            mc = rec.get("market_constraints")
            if mc:
                row.setdefault("metadata", {})
                if isinstance(row["metadata"], dict):
                    row["metadata"]["market_constraints"] = mc
            # Nest warren_bot output inside agent_signals JSONB before saving.
            # warren_bot is not a top-level DB column — it lives under agent_signals["warren_bot"].
            if "agent_signals" in row and isinstance(row["agent_signals"], dict):
                wb_data = rec.get("warren_bot")
                if wb_data is not None:
                    row["agent_signals"]["warren_bot"] = wb_data
            # Ensure agent_signals is JSON-serialisable (remove non-standard keys)
            if "agent_signals" in row and isinstance(row["agent_signals"], dict):
                row["agent_signals"] = json.loads(json.dumps(row["agent_signals"]))

            resp = client.table("recommendations").insert(row).execute()
            if resp.data:
                rec_id = resp.data[0].get("id")
                if rec_id:
                    saved_ids.append(str(rec_id))
                    log.info("[%s] saved → id=%s", rec["symbol"], rec_id)
                    # ── Seed PENDING outcome row so track record starts immediately ──
                    try:
                        from agents.outcome_tracker import seed_pending_outcome
                        entry_low  = rec.get("entry_low")
                        entry_high = rec.get("entry_high")
                        if entry_low and entry_high:
                            entry_price = (float(entry_low) + float(entry_high)) / 2
                        elif entry_low:
                            entry_price = float(entry_low)
                        elif entry_high:
                            entry_price = float(entry_high)
                        else:
                            entry_price = None
                        gov_check = rec.get("gov_check") or {}
                        val_kappa = None
                        if isinstance(gov_check, dict) and "validation" in gov_check:
                            val_kappa = gov_check["validation"].get("aggregate_kappa")
                        seed_pending_outcome(
                            client          = client,
                            rec_id          = str(rec_id),
                            symbol          = rec["symbol"],
                            action          = rec.get("action", "BUY"),
                            entry_price     = entry_price,
                            rec_date        = date.today(),
                            composite_score = rec.get("composite_score"),
                            agent_signals   = rec.get("agent_signals", {}),
                            validation_kappa = val_kappa,
                        )
                    except Exception as ot_exc:
                        log.debug("[%s] outcome seed skipped: %s", rec["symbol"], ot_exc)
        except Exception as exc:
            log.error("[%s] save failed: %s", rec["symbol"], exc)
            errors.append(f"save {rec['symbol']}: {exc}")

    log.info("Saved %d / %d recommendations", len(saved_ids), len(recommendations))

    # ── Save market-wide FII/DII flows once per pipeline run ─────────────────
    # The institutional agent fetches live FII/DII data per symbol, but only
    # one upsert is needed since these are market-wide values (not per-stock).
    # We reuse the already-open Supabase client from this node.
    try:
        from agents.institutional import _save_institutional_flows
        symbol_results = state.get("symbol_results") or {}
        for sym_res in symbol_results.values():
            if not isinstance(sym_res, dict):
                continue
            inst = sym_res.get("institutional")
            if isinstance(inst, dict) and inst.get("today_fii_net") is not None:
                _save_institutional_flows(inst, client)
                break
        else:
            log.debug("No live institutional flow data found in this run's results")
    except Exception as _iff_exc:
        log.warning("institutional_flows save failed (non-fatal): %s", _iff_exc)

    return {"saved_ids": saved_ids, "errors": errors}


async def monitor_node(state: OrchestratorState) -> dict:
    """
    Trigger the portfolio monitor after recommendations are saved so that
    danger/stoploss/target alerts are evaluated against the new data.
    Gracefully skips if portfolio_monitor module is not yet implemented.
    """
    if state["dry_run"]:
        log.info("[DRY RUN] Skipping portfolio monitor trigger")
        return {}

    try:
        from scheduler import portfolio_monitor  # noqa: F401
        await asyncio.to_thread(portfolio_monitor.run)
        log.info("Portfolio monitor triggered successfully")
    except ImportError:
        log.warning("portfolio_monitor module not found — skipping trigger")
    except Exception as exc:
        log.warning("Portfolio monitor trigger failed: %s", exc)

    return {}


async def log_run_node(state: OrchestratorState) -> dict:
    """
    Write run metadata to the Supabase daily_runs table for audit / dashboards.
    Skipped in dry mode.
    """
    duration = round(time.time() - state["start_time"], 2)
    n_recs   = len(state["recommendations"])
    n_err    = len(state["errors"])
    n_syms   = state["symbols_processed"]

    log.info(
        "Pipeline complete — symbols=%d  recs=%d  errors=%d  duration=%.1fs",
        n_syms, n_recs, n_err, duration,
    )

    # Detect full-blackout data-degradation days:
    # when ALL symbols are suppressed and none produced recs, it's almost always
    # because all external data sources (screener.in + Trendlyne) were down.
    suppressed_count = sum(1 for e in state["errors"] if "SUPPRESSED" in e)
    if n_recs == 0 and n_syms > 0 and suppressed_count == n_syms:
        log.critical(
            "DATA DEGRADATION DAY — all %d symbols SUPPRESSED. "
            "Likely cause: screener.in + Trendlyne both unreachable from Railway. "
            "Check: (1) screener.in network access from Railway "
            "(2) TRENDLYNE_SESSION/TRENDLYNE_CSRF cookies still valid. "
            "No recommendations written — this is intentional (low-quality data suppressed).",
            n_syms,
        )

    if state["dry_run"]:
        log.info("[DRY RUN] daily_runs table not updated")
        return {}

    client = _supabase()
    if not client:
        return {}

    # Status: DATA_DEGRADATION when all suppressed (external sources down),
    # WARNING when some errors, OK when recs produced
    if n_recs == 0 and suppressed_count == n_syms and n_syms > 0:
        run_status = "DATA_DEGRADATION"
    elif n_recs > 0:
        run_status = "OK"
    else:
        run_status = "WARNING"

    try:
        client.table("daily_runs").insert({
            "symbols_processed": n_syms,
            "errors":            n_err,
            "duration_seconds":  duration,
            "status":            run_status,
        }).execute()
        log.info("daily_runs row logged (status=%s)", run_status)
    except Exception as exc:
        log.warning("daily_runs log failed: %s", exc)

    # ── Completion sentinel ──────────────────────────────────────────────────
    # Intentionally verbose so it survives Railway's 1000-line log-window cap
    # and confirms the full pipeline ran to completion.
    import datetime as _dt
    log.info(
        "ORCHESTRATOR_COMPLETE run_date=%s pipeline=OK",
        _dt.date.today().isoformat(),
    )
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# Sector PE Snapshot node
# ─────────────────────────────────────────────────────────────────────────────

async def sector_pe_snapshot_node(state: OrchestratorState) -> dict:
    """
    Persist today's sector P/E regime snapshot to sector_pe_snapshots.

    Runs once per daily pipeline, BEFORE symbol loading, so that the fresh
    regime data is available to sector_valuation's in-process cache for all
    subsequent fundamental.analyse() calls in this pipeline run.

    In dry_run mode the snapshot is fetched (warming the cache) but not saved.
    Any failures are non-fatal and only logged — the pipeline continues regardless.

    Regime changes detected vs yesterday are logged at INFO level so they appear
    in the daily run log without needing a separate alerting pass.
    """
    dry_run = state.get("dry_run", False)
    try:
        from scheduler.sector_pe_tracker import run_snapshot
        result = run_snapshot(sectors=None, dry_run=dry_run)
        label = " [DRY RUN]" if dry_run else ""
        log.info(
            "Sector PE snapshot%s: fetched=%d saved=%d fallback=%d changes=%d errors=%d",
            label,
            result.sectors_fetched,
            result.sectors_saved,
            result.sectors_fallback,
            len(result.regime_changes),
            len(result.errors),
        )
        for chg in result.regime_changes:
            log.info(
                "  REGIME CHANGE [%s]: %s → %s  (dev=%s%%)",
                chg["sector_key"],
                chg["from_regime"],
                chg["to_regime"],
                f"{chg.get('deviation_pct'):+.0f}" if chg.get("deviation_pct") is not None else "n/a",
            )
        if result.errors:
            for err in result.errors:
                log.warning("  sector_pe_snapshot error: %s", err)
    except Exception as exc:
        log.warning("sector_pe_snapshot_node failed (non-fatal): %s", exc)

    return {}


# ─────────────────────────────────────────────────────────────────────────────
# Discovery Screener node
# ─────────────────────────────────────────────────────────────────────────────

async def run_discovery_node(state: OrchestratorState) -> dict:
    """
    Run the proactive discovery screener as the final step in the daily pipeline.

    Processes today's rotation slice of the full NSE EQ universe (~1 700 stocks,
    200-symbol slice → 9-day full cycle, ~3× monthly coverage).  The screener
    runs independently of the portfolio-symbol pipeline above — it always scans
    the broader market for new opportunities not already in the user's portfolio.

    In --dry mode the screener still runs but save_to_db is forced False so no
    Supabase writes occur.

    Failures are fully non-fatal: an exception here is logged and the pipeline
    returns normally.
    """
    dry_run = state.get("dry_run", False)
    if dry_run:
        log.info("[DRY RUN] Running discovery screener (save_to_db=False)...")
    else:
        log.info("Running discovery screener (full NSE EQ universe, 200-symbol slice)...")

    try:
        results = await asyncio.to_thread(
            run_discovery,
            save_to_db=not dry_run,
        )
        label = " [DRY RUN]" if dry_run else ""
        log.info(
            "Discovery screener%s complete: %d opportunities found",
            label, len(results),
        )
        for dr in results:
            log.info(
                "  [%s] %s — upside=%.1f%%  conf=%.1f  score=%.1f",
                dr.opportunity_tier, dr.symbol,
                dr.upside_pct, dr.upside_confidence, dr.composite_score,
            )
    except Exception as exc:
        log.warning("run_discovery_node failed (non-fatal): %s", exc, exc_info=True)

    # ── Completion sentinel — always the last log line emitted by the pipeline ──
    # This line is intentionally verbose so it survives Railway's 1000-line
    # log-window cap and confirms the full pipeline ran to completion.
    import datetime as _dt
    log.info(
        "ORCHESTRATOR_COMPLETE run_date=%s pipeline=OK",
        _dt.date.today().isoformat(),
    )
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# Graph construction
# ─────────────────────────────────────────────────────────────────────────────

def _build_graph():
    """Compile the LangGraph orchestration pipeline."""
    builder = StateGraph(OrchestratorState)

    builder.add_node("sector_pe_snapshot", sector_pe_snapshot_node)
    builder.add_node("load_symbols",       load_symbols_node)
    builder.add_node("load_weights",       load_weights_node)
    builder.add_node("run_agents",         run_agents_node)
    builder.add_node("synthesise",         synthesise_node)
    builder.add_node("fact_check",         fact_check_node)
    builder.add_node("save_recs",          save_recs_node)
    builder.add_node("monitor",            monitor_node)
    builder.add_node("log_run",            log_run_node)
    # NOTE: run_discovery_node removed from pipeline (P6 schedule redesign).
    # Discovery now runs as a standalone job at 10:30 IST in worker.py after
    # market open, so it uses live intraday prices instead of yesterday's close.

    builder.set_entry_point("sector_pe_snapshot")
    builder.add_edge("sector_pe_snapshot", "load_symbols")
    builder.add_edge("load_symbols",       "load_weights")
    builder.add_edge("load_weights",       "run_agents")
    builder.add_edge("run_agents",         "synthesise")
    builder.add_edge("synthesise",         "fact_check")
    builder.add_edge("fact_check",         "save_recs")
    builder.add_edge("save_recs",          "monitor")
    builder.add_edge("monitor",            "log_run")
    builder.add_edge("log_run",            END)

    return builder.compile()


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline entry point
# ─────────────────────────────────────────────────────────────────────────────

async def run_pipeline(
    dry_run:          bool       = False,
    symbols_override: list[str]  | None = None,
) -> OrchestratorState:
    """
    Execute the full orchestration pipeline once and return the final state.

    Args:
        dry_run:          If True, skip all Supabase writes and print results.
        symbols_override: If provided, skip env/portfolio loading and analyse
                          exactly these symbols. Useful for ad-hoc testing of
                          a single stock without touching WATCHLIST or the DB.
                          Example: symbols_override=["PREMEXPLN.NS"]
    """
    graph = _build_graph()

    # Resolve and validate any override symbols up-front
    preloaded: list[str] = []
    if symbols_override:
        from data.symbol_map import is_excluded, resolve_yf  # noqa: E402
        for raw in symbols_override:
            sym = raw.strip().upper()
            if not (sym.endswith(".NS") or sym.endswith(".BO")):
                sym += ".NS"
            if is_excluded(sym):
                log.warning("--symbol %s is in excluded list, skipping", sym)
                continue
            resolved = resolve_yf(sym)
            if resolved:
                preloaded.append(resolved)
                log.info("Symbol override: %s -> %s", raw, resolved)
            else:
                log.warning("--symbol %s could not be resolved, skipping", sym)

    initial_state: OrchestratorState = {
        "dry_run":           dry_run,
        # If a pre-validated override list exists, put it directly in symbols
        # so load_symbols_node knows to skip its own loading logic.
        "symbols":           preloaded,
        "agent_weights":     {},
        "symbol_results":    {},
        "recommendations":   [],
        "saved_ids":         [],
        "errors":            [],
        "start_time":        time.time(),
        "symbols_processed": 0,
    }

    return await graph.ainvoke(initial_state)


# ─────────────────────────────────────────────────────────────────────────────
# APScheduler + CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bharat Intelligence Daily Orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scheduler/orchestrator.py                          # start 06:00 IST scheduler
  python scheduler/orchestrator.py --run-now                # run immediately, save to DB
  python scheduler/orchestrator.py --run-now --dry          # full analysis, no DB writes
  python scheduler/orchestrator.py --symbol PREMEXPLN.NS    # ad-hoc dry run, single symbol
  python scheduler/orchestrator.py --symbol RELIANCE.NS --symbol TCS.NS  # multiple symbols
        """,
    )
    parser.add_argument(
        "--run-now", action="store_true",
        help="Execute the pipeline immediately instead of waiting for the schedule",
    )
    parser.add_argument(
        "--dry", action="store_true",
        help="Dry run: full analysis but no Supabase writes; prints results to stdout",
    )
    parser.add_argument(
        "--symbol",
        action="append",
        dest="symbols",
        metavar="SYMBOL",
        help=(
            "Analyse a specific symbol (NSE ticker, e.g. PREMEXPLN.NS). "
            "Can be repeated for multiple symbols. "
            "Implicitly enables --run-now and --dry; "
            "ignores WATCHLIST env var and portfolio holdings."
        ),
    )
    args = parser.parse_args()

    # --symbol implies --run-now and --dry automatically
    if args.symbols:
        args.run_now = True
        args.dry     = True

    if args.run_now:
        dry_label = " [DRY RUN]" if args.dry else ""
        sym_label = f" symbols={args.symbols}" if args.symbols else ""
        log.info("Starting pipeline immediately%s%s...", dry_label, sym_label)
        final = asyncio.run(
            run_pipeline(
                dry_run=args.dry,
                symbols_override=args.symbols or None,
            )
        )
        if final.get("errors"):
            log.warning(
                "%d error(s) during run:\n  %s",
                len(final["errors"]),
                "\n  ".join(final["errors"]),
            )
    else:
        # ── Scheduled mode ────────────────────────────────────────────────────
        try:
            from apscheduler.schedulers.blocking import BlockingScheduler
            from apscheduler.triggers.cron import CronTrigger
        except ImportError:
            log.error("apscheduler not installed — run: pip install apscheduler")
            sys.exit(1)

        try:
            from zoneinfo import ZoneInfo
            IST = ZoneInfo("Asia/Kolkata")
        except ImportError:
            import pytz
            IST = pytz.timezone("Asia/Kolkata")

        def _scheduled_job() -> None:
            log.info("Scheduled trigger firing...")
            asyncio.run(run_pipeline(dry_run=False))

        scheduler = BlockingScheduler(timezone=IST)
        scheduler.add_job(
            _scheduled_job,
            CronTrigger(hour=6, minute=0, timezone=IST),
            id="daily_orchestrator",
            name="Bharat Intelligence Daily Run",
            max_instances=1,   # prevent overlap if a run is still in progress
            coalesce=True,     # fire once even if missed multiple times
        )

        log.info("-" * 60)
        log.info("  Bharat Intelligence Orchestrator - scheduler started")
        log.info("  Next run: 06:00 IST (Asia/Kolkata) daily")
        log.info("  Press Ctrl+C to stop")
        log.info("-" * 60)

        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            log.info("Scheduler stopped cleanly")


if __name__ == "__main__":
    main()
