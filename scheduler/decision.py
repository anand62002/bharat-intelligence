"""
scheduler/decision.py — the portfolio decision is computed in code (WP-018, ADR-001).

Why this exists
---------------
The synthesis prompt used to ask the LLM for the action, confidence, target,
upside and danger, with a table mapping composite score to a "confidence range".
Every number was model opinion. The meaning of "confidence" shifted with each
model upgrade, an explicit JSON null once crashed the run, and a truncated reply
lost the whole symbol.

Now:
- decide() computes every number deterministically:
  - action: rule on the equal-weight composite, then the consensus gate
  - confidence: calibrated P(beat Nifty at 90d) for the composite's band
  - entry and stop: from the technical agent's ATR
  - target: the larger valuation gap, capped at 100 % (as discovery does)
- The LLM gets those values as FIXED inputs and writes only bull_case,
  bear_case, synthesis, headline, market_constraints and an optional veto.
  merge_narrative() enforces that: the LLM can't change a number, and a veto
  can only move BUY → HOLD.
- If the LLM fails, fallback_narrative() writes the text from agents/rationale.py,
  so a synthesis failure costs the prose, never the recommendation.

Thresholds live in data/decision_config.py. The legacy LLM-numbers path stays
behind DECISION_SOURCE = "llm".
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from data import decision_config as dc

log = logging.getLogger(__name__)

# Keys the LLM may set. Everything else in its JSON is ignored.
NARRATIVE_KEYS = ("bull_case", "bear_case", "synthesis", "headline", "market_constraints")
DECISION_KEYS = ("action", "confidence", "upside_confidence", "risk_score", "entry_low",
                 "entry_high", "target", "stoploss", "upside_pct", "horizon_days",
                 "danger_drop_pct", "danger_confidence")


def _f(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and x not in (float("inf"), float("-inf")) else None


def action_for(composite: float) -> str:
    if composite >= dc.BUY_MIN_COMPOSITE:
        return "BUY"
    if composite >= dc.HOLD_MIN_COMPOSITE:
        return "HOLD"
    if composite <= dc.AVOID_MAX_COMPOSITE:
        return "AVOID"
    return "SELL"


def _upside(agent_results: dict) -> tuple[Optional[float], str]:
    """Larger of the fundamental and technical upside, as discovery's _best_upside picks it."""
    f = _f((agent_results.get("fundamental") or {}).get("upside_pct"))
    t = _f((agent_results.get("technical") or {}).get("upside_pct"))
    if f is None and t is None:
        return None, "none"
    if t is None or (f is not None and f >= t):
        return f, "fundamental"
    return t, "technical"


def decide(
    symbol: str,
    agent_results: dict,
    composite: float,
    price: Optional[float],
    calibration: Any = None,
) -> dict:
    """
    The recommendation's numbers, from code only. Never raises on missing agent data.

    Returns a synthesis-shaped dict (the same keys the old LLM JSON carried) plus
    `decision_basis`, an audit record of where each number came from.
    """
    from scheduler.orchestrator import _apply_consensus_gate      # one definition of the gate

    composite = float(composite)
    action = action_for(composite)

    # Consensus gate decides the action only; confidence stays the calibrated rate.
    gated = _apply_consensus_gate(symbol, {"action": action, "confidence": 50.0, "synthesis": ""},
                                  agent_results)
    gate_note = (gated.get("synthesis") or "").strip()
    action = str(gated.get("action") or action).upper()

    if calibration is not None:
        confidence = float(calibration.confidence(composite))
        band_n = int(calibration.band_n(composite))
        has_history = bool(getattr(calibration, "has_history", False))
    else:
        confidence, band_n, has_history = 50.0, 0, False

    tech = agent_results.get("technical") or {}
    fund = agent_results.get("fundamental") or {}
    atr = _f(tech.get("atr_14"))
    atr_stop = _f(tech.get("atr_stoploss"))

    upside_raw, upside_src = _upside(agent_results)
    upside = min(upside_raw, dc.UPSIDE_CAP_PCT) if upside_raw is not None else None

    entry_low = entry_high = target = None
    if price and action in ("BUY", "HOLD"):
        entry_high = round(price, 2)
        if atr:
            entry_low = round(price - dc.ENTRY_ATR_MULTIPLE * atr, 2)
        if upside is not None and upside > 0:
            target = round(price * (1 + upside / 100.0), 2)
    stoploss = round(atr_stop, 2) if atr_stop else None

    danger = _f(fund.get("danger_drop_pct"))
    if danger is None and stoploss and price:
        danger = round((price - stoploss) / price * 100.0, 2)

    out = {
        "action": action,
        "confidence": round(confidence, 1),
        "upside_confidence": round(confidence, 1),
        "risk_score": round(100.0 - composite, 2),
        "entry_low": entry_low,
        "entry_high": entry_high,
        "target": target,
        "stoploss": stoploss,
        "upside_pct": round(upside, 2) if (upside is not None and target) else 0.0,
        "horizon_days": dc.DECISION_HORIZON_DAYS,
        "danger_drop_pct": danger or 0.0,
        "danger_confidence": _f(fund.get("danger_confidence")) or 0.0,
        "decision_basis": {
            "source": "code",
            "composite": round(composite, 2),
            "rule_action": action_for(composite),
            "consensus_gate": gate_note or None,
            "confidence_basis": ("calibrated" if has_history else "no_track_record"),
            "band_n": band_n,
            "upside_source": upside_src,
            "valuation_gap_pct": round(upside_raw, 2) if upside_raw is not None else None,
            "upside_capped": bool(upside_raw is not None and upside_raw > dc.UPSIDE_CAP_PCT),
            "price": price,
            "atr_14": atr,
        },
    }
    if gate_note:
        out["synthesis_note"] = gate_note
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Prompt and narrative
# ─────────────────────────────────────────────────────────────────────────────

def _fmt(v: Any, money: bool = False, pct: bool = False) -> str:
    x = _f(v)
    if x is None:
        return "none"
    if money:
        return f"₹{x:,.2f}"
    if pct:
        return f"{x:.1f}%"
    return f"{x:g}"


def render_prompt(template: str, *, symbol: str, agent_text: str, composite: float,
                  decision: dict, current_price: Optional[float], atr_entry_zone: str,
                  data_years: str, data_quality: str) -> str:
    """Fill the narrative prompt. The decision's numbers go in as fixed facts."""
    b = decision.get("decision_basis") or {}
    conf_line = (f"{decision['confidence']:.1f}% (historical beat-Nifty rate for this composite band, "
                 f"n={b.get('band_n', 0)})" if b.get("confidence_basis") == "calibrated"
                 else f"{decision['confidence']:.1f}% (no track record yet — neutral prior)")
    repl = {
        "{symbol}": str(symbol),
        "{agent_outputs}": agent_text,
        "{composite_score}": f"{composite:.1f}",
        "{current_price}": _fmt(current_price, money=True),
        "{action}": decision["action"],
        "{confidence_line}": conf_line,
        "{entry_low}": _fmt(decision.get("entry_low"), money=True),
        "{entry_high}": _fmt(decision.get("entry_high"), money=True),
        "{target}": _fmt(decision.get("target"), money=True),
        "{stoploss}": _fmt(decision.get("stoploss"), money=True),
        "{upside_pct}": _fmt(decision.get("upside_pct"), pct=True),
        "{danger_drop_pct}": _fmt(decision.get("danger_drop_pct"), pct=True),
        "{horizon_days}": str(decision.get("horizon_days")),
        "{consensus_note}": b.get("consensus_gate") or "none",
        "{atr_entry_zone}": atr_entry_zone,
        "{data_years}": data_years,
        "{data_quality_label}": data_quality,
    }
    out = template
    for k, v in repl.items():
        out = out.replace(k, v)
    return out


def merge_narrative(decision: dict, llm: dict) -> dict:
    """
    Decision numbers + the LLM's text. The LLM cannot change a number; a veto
    only turns BUY into HOLD (recorded in decision_basis).
    """
    merged = {k: v for k, v in decision.items() if k != "synthesis_note"}
    for k in NARRATIVE_KEYS:
        v = llm.get(k)
        if v not in (None, "", []):
            merged[k] = v
    ignored = sorted(k for k in DECISION_KEYS if k in llm)
    if ignored:
        log.info("LLM returned decision fields %s — ignored (numbers come from code)", ignored)
    basis = dict(merged.get("decision_basis") or {})
    veto = bool(llm.get("veto")) and str(llm.get("veto")).lower() not in ("false", "0", "no")
    if veto:
        reason = str(llm.get("veto_reason") or "").strip() or "no reason given"
        basis["veto"] = {"applied": merged["action"] == "BUY", "reason": reason,
                         "from_action": merged["action"]}
        if merged["action"] == "BUY":
            merged["action"] = "HOLD"
            merged["synthesis"] = (f"⚠ ANALYST VETO: BUY held back to HOLD — {reason}\n\n"
                                   + (merged.get("synthesis") or ""))
    merged["decision_basis"] = basis
    if decision.get("synthesis_note"):
        merged["synthesis"] = decision["synthesis_note"] + "\n\n" + (merged.get("synthesis") or "")
    return merged


def fallback_narrative(symbol: str, decision: dict, agent_results: dict, reason: str) -> dict:
    """Templated text from agents/rationale.py when the LLM is unavailable or unusable."""
    from agents.rationale import build_rationale
    from data.decision_config import AGENT_NAMES

    scored = []
    for name in AGENT_NAMES:
        r = agent_results.get(name)
        s = _f((r or {}).get("score"))
        if s is not None:
            scored.append((s, name, build_rationale(name, r)))
    scored.sort(reverse=True)
    bull = [f"{n}: {t}" for s, n, t in scored[:3] if t and s >= 55]
    bear = [f"{n}: {t}" for s, n, t in sorted(scored)[:3] if t and s < 50]
    b = decision.get("decision_basis") or {}
    text = (f"{decision['action']} on an equal-weight composite of {b.get('composite', 0):.1f}/100 "
            f"(rule: BUY ≥ {dc.BUY_MIN_COMPOSITE:g}, HOLD ≥ {dc.HOLD_MIN_COMPOSITE:g}). "
            f"Narrative unavailable ({reason}); levels and confidence are computed in code.")
    merged = merge_narrative(decision, {
        "bull_case": bull, "bear_case": bear, "synthesis": text,
        "headline": f"{decision['action']}: {symbol} — composite {b.get('composite', 0):.0f}/100",
    })
    merged["decision_basis"] = {**merged.get("decision_basis", {}), "narrative": "template",
                                "narrative_reason": reason}
    return merged
