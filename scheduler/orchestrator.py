"""
scheduler/orchestrator.py — Bharat Intelligence Daily Orchestrator
==================================================================
LangGraph-based pipeline that coordinates all 7 analysis agents,
runs a Claude Sonnet bull-bear synthesis, and saves recommendations.

Schedule  : 06:00 IST (Asia/Kolkata) daily via APScheduler
Pipeline  : (LangGraph nodes, run sequentially)
  load_symbols  →  load_weights  →  run_agents  →  synthesise
  →  save_recs  →  monitor       →  log_run

Agents run in two async phases per symbol:
  Phase 1 (parallel): technical, fundamental, sentiment
  Phase 2 (parallel): institutional (+ pledging from fund), historical_rag
  Pre-fetched (once): macro, commodities  (symbol-agnostic)

Usage:
    python scheduler/orchestrator.py                   # start 06:00 IST scheduler
    python scheduler/orchestrator.py --run-now         # fire once immediately
    python scheduler/orchestrator.py --run-now --dry   # dry run, no DB writes
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from datetime import date, timedelta
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

# ── LangGraph ─────────────────────────────────────────────────────────────────
from langgraph.graph import StateGraph, END                    # noqa: E402

# ── Constants ─────────────────────────────────────────────────────────────────
AGENT_NAMES: list[str] = [
    "technical", "fundamental", "sentiment",
    "institutional", "macro", "historical_rag", "commodities",
]
DEFAULT_ACCURACY      = 70.0    # fallback accuracy when agent_performance has no row
CLAUDE_MODEL          = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5")
CLAUDE_MAX_TOKENS     = 2048
SYNTHESIS_PROMPT_PATH = _ROOT / "prompts" / "orchestrator_synthesis.txt"

# ─────────────────────────────────────────────────────────────────────────────
# State
# ─────────────────────────────────────────────────────────────────────────────

class OrchestratorState(TypedDict):
    dry_run:           bool
    symbols:           list[str]
    agent_weights:     dict[str, float]        # normalised weights, sum ≈ 1.0
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
) -> float:
    """
    Weighted average of all 7 agent scores (0–100).
    Agents with None or missing scores are excluded from the average.
    """
    total_w = 0.0
    total   = 0.0
    default_w = 1.0 / len(AGENT_NAMES)
    for name in AGENT_NAMES:
        score = agent_results.get(name, {}).get("score")
        w     = weights.get(name, default_w)
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
    """
    lines: list[str] = []
    for name in AGENT_NAMES:
        res    = results.get(name, {})
        signal = res.get("signal", "NO_DATA")
        score  = res.get("score", "N/A")
        w      = weights.get(name, 0.0)
        detail = res.get("detail") or {}
        lines.append(f"### {name.upper()} AGENT  (accuracy-weight={w:.4f})")
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
    Extract and parse the first JSON object from Claude's response.
    Handles ```json ... ``` fences and bare JSON objects.
    """
    # Fenced block
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    # Bare JSON object
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        return json.loads(m.group(0))
    raise ValueError("No JSON object found in Claude response")


def _fallback_synthesis(
    symbol:        str,
    composite:     float,
    agent_results: dict[str, dict],
) -> dict:
    """
    Score-based fallback recommendation when the Anthropic API is unavailable
    or the Claude response cannot be parsed.
    """
    if   composite >= 72: action = "BUY"
    elif composite >= 55: action = "HOLD"
    elif composite <= 35: action = "AVOID"
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
    horizon_days      = int(synthesis_data.get("horizon_days", 180))
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
    t_res, f_res, s_res = await asyncio.gather(
        asyncio.to_thread(lambda: tech_analyse(symbol)),
        asyncio.to_thread(lambda: fund_analyse(symbol)),
        asyncio.to_thread(lambda: sent_analyse(symbol)),
        return_exceptions=True,
    )

    results: dict[str, dict] = {}
    for name, res in (("technical", t_res), ("fundamental", f_res), ("sentiment", s_res)):
        if isinstance(res, Exception):
            log.warning("[%s] %s agent error: %s", symbol, name, res)
            results[name] = {"signal": "NO_DATA", "score": 50, "agent_name": name}
        else:
            results[name] = res

    # Pre-fetched symbol-agnostic results
    results["macro"]       = macro_res
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
    """
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
    return {"agent_weights": weights}


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
    errors         = list(state["errors"])

    if not symbol_results:
        return {"recommendations": [], "errors": errors}

    prompt_template = _load_synthesis_prompt()
    ant_key         = os.getenv("ANTHROPIC_API_KEY", "")

    if not ant_key:
        log.warning("ANTHROPIC_API_KEY not set — using score-based fallback for all symbols")

    # Initialise Anthropic client once
    ant_client = None
    if ant_key and prompt_template:
        try:
            import anthropic
            ant_client = anthropic.Anthropic(api_key=ant_key)
        except ImportError:
            log.warning("anthropic package not installed — pip install anthropic")

    recommendations: list[dict] = []

    for symbol, agent_results in symbol_results.items():
        try:
            composite  = _composite_score(agent_results, weights)
            agent_text = _format_agent_outputs(symbol, agent_results, weights)

            synthesis_data: dict = {}

            if ant_client and prompt_template:
                # Resolve current price for the prompt
                fund_detail = (agent_results.get("fundamental", {}).get("detail") or {})
                valuation   = fund_detail.get("valuation", {}) if isinstance(fund_detail, dict) else {}
                current_price = (
                    valuation.get("current_price") if isinstance(valuation, dict) else None
                ) or "N/A"

                prompt = prompt_template.format(
                    symbol=symbol,
                    agent_outputs=agent_text,
                    composite_score=f"{composite:.1f}",
                    current_price=current_price,
                )

                log.info("[%s] Calling Claude Sonnet for synthesis...", symbol)
                response = await asyncio.to_thread(
                    ant_client.messages.create,
                    model=CLAUDE_MODEL,
                    max_tokens=CLAUDE_MAX_TOKENS,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw_text = response.content[0].text

                try:
                    synthesis_data = _extract_json(raw_text)
                    log.info("[%s] Claude synthesis parsed OK", symbol)
                except (ValueError, json.JSONDecodeError) as parse_err:
                    log.warning(
                        "[%s] Claude JSON parse failed (%s) — using fallback",
                        symbol, parse_err,
                    )
                    synthesis_data = _fallback_synthesis(symbol, composite, agent_results)
            else:
                synthesis_data = _fallback_synthesis(symbol, composite, agent_results)

            rec = _build_recommendation(
                symbol, synthesis_data, agent_results, weights, composite
            )
            recommendations.append(rec)
            log.info(
                "[%s] → %s  confidence=%.0f%%  upside=%.1f%%  risk=%.0f",
                symbol, rec["action"], rec["confidence"], rec["upside_pct"], rec["risk_score"],
            )

        except Exception as exc:
            log.error("[%s] synthesis failed: %s", symbol, exc)
            errors.append(f"{symbol} synthesis: {exc}")

    return {"recommendations": recommendations, "errors": errors}


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
        "valid_till",
    }

    saved_ids: list[str] = []
    for rec in recommendations:
        try:
            row = {k: v for k, v in rec.items() if k in _DB_COLUMNS}
            row["valid_till"] = str(
                date.today() + timedelta(days=int(rec.get("horizon_days", 180)))
            )
            # Ensure agent_signals is JSON-serialisable (remove non-standard keys)
            if "agent_signals" in row and isinstance(row["agent_signals"], dict):
                row["agent_signals"] = json.loads(json.dumps(row["agent_signals"]))

            resp = client.table("recommendations").insert(row).execute()
            if resp.data:
                rec_id = resp.data[0].get("id")
                if rec_id:
                    saved_ids.append(str(rec_id))
                    log.info("[%s] saved → id=%s", rec["symbol"], rec_id)
        except Exception as exc:
            log.error("[%s] save failed: %s", rec["symbol"], exc)
            errors.append(f"save {rec['symbol']}: {exc}")

    log.info("Saved %d / %d recommendations", len(saved_ids), len(recommendations))
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
        from agents import portfolio_monitor  # noqa: F401
        if hasattr(portfolio_monitor, "run"):
            await asyncio.to_thread(portfolio_monitor.run)
            log.info("Portfolio monitor triggered successfully")
        else:
            log.warning("portfolio_monitor.run() not found — skipping trigger")
    except ImportError:
        log.info("portfolio_monitor not yet implemented — skipping")
    except Exception as exc:
        log.warning("portfolio monitor trigger failed: %s", exc)

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

    if state["dry_run"]:
        log.info("[DRY RUN] daily_runs table not updated")
        return {}

    client = _supabase()
    if not client:
        return {}

    try:
        client.table("daily_runs").insert({
            "symbols_processed": n_syms,
            "errors":            n_err,
            "duration_seconds":  duration,
        }).execute()
        log.info("daily_runs row logged")
    except Exception as exc:
        log.warning("daily_runs log failed: %s", exc)

    return {}


# ─────────────────────────────────────────────────────────────────────────────
# Graph construction
# ─────────────────────────────────────────────────────────────────────────────

def _build_graph():
    """Compile the LangGraph orchestration pipeline."""
    builder = StateGraph(OrchestratorState)

    builder.add_node("load_symbols", load_symbols_node)
    builder.add_node("load_weights", load_weights_node)
    builder.add_node("run_agents",   run_agents_node)
    builder.add_node("synthesise",   synthesise_node)
    builder.add_node("save_recs",    save_recs_node)
    builder.add_node("monitor",      monitor_node)
    builder.add_node("log_run",      log_run_node)

    builder.set_entry_point("load_symbols")
    builder.add_edge("load_symbols", "load_weights")
    builder.add_edge("load_weights", "run_agents")
    builder.add_edge("run_agents",   "synthesise")
    builder.add_edge("synthesise",   "save_recs")
    builder.add_edge("save_recs",    "monitor")
    builder.add_edge("monitor",      "log_run")
    builder.add_edge("log_run",      END)

    return builder.compile()


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline entry point
# ─────────────────────────────────────────────────────────────────────────────

async def run_pipeline(dry_run: bool = False) -> OrchestratorState:
    """Execute the full orchestration pipeline once and return the final state."""
    graph = _build_graph()

    initial_state: OrchestratorState = {
        "dry_run":           dry_run,
        "symbols":           [],
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
  python scheduler/orchestrator.py              # start 06:00 IST scheduler
  python scheduler/orchestrator.py --run-now    # run immediately, save to DB
  python scheduler/orchestrator.py --run-now --dry   # full analysis, no DB writes
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
    args = parser.parse_args()

    if args.run_now:
        log.info("Starting pipeline immediately (dry=%s)...", args.dry)
        final = asyncio.run(run_pipeline(dry_run=args.dry))
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
