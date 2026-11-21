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
        beat_rate = float(calibration.confidence(composite))
        band_n = int(calibration.band_n(composite))
        has_history = bool(getattr(calibration, "has_history", False))
    else:
        beat_rate, band_n, has_history = 50.0, 0, False
    # The band rate is P(beat Nifty). A SELL/AVOID is right when the stock lags,
    # so its confidence is the complement (Fable review of WP-018).
    bearish = action in ("SELL", "AVOID")
    confidence = 100.0 - beat_rate if bearish else beat_rate

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
    stop_note = None
    # The technical agent's stop is anchored to ITS price; the decision's price may come
    # from the fundamental snapshot. A stop at/above the entry or price is unusable —
    # rebuild it from this price, or drop it.
    floor = entry_low if entry_low else price
    if stoploss and floor and stoploss >= floor:
        if price and atr:
            stoploss, stop_note = round(price - 2 * atr, 2), "rebuilt: agent stop was above entry"
        else:
            stoploss, stop_note = None, "dropped: agent stop was above entry"

    danger = _f(fund.get("danger_drop_pct"))
    if not danger and stoploss and price:
        danger = round((price - stoploss) / price * 100.0, 2)
    danger_conf = _f(fund.get("danger_confidence")) or 0.0
    if 0 < danger_conf <= 1.0:          # fundamental emits 0–1; the column is 0–100
        danger_conf *= 100.0

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
        "danger_confidence": round(danger_conf, 1),
        "decision_basis": {
            "source": "code",
            "symbol": symbol,
            "composite": round(composite, 2),
            "rule_action": action_for(composite),
            "consensus_gate": gate_note or None,
            "confidence_basis": ("calibrated" if has_history else "no_track_record"),
            "confidence_meaning": ("P(underperform Nifty)" if bearish else "P(beat Nifty)"),
            "band_beat_rate": round(beat_rate, 1),
            "stop_note": stop_note,
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
    meaning = b.get("confidence_meaning", "P(beat Nifty)")
    conf_line = (f"{decision['confidence']:.1f}% — {meaning} at 90 days, historical rate for this "
                 f"composite band (n={b.get('band_n', 0)})" if b.get("confidence_basis") == "calibrated"
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
        "{stoploss}": (f"{_fmt(decision.get('stoploss'), money=True)} (2× ATR-14 below price)"
                       if decision.get("stoploss") else "none (no ATR data)"),
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


def _as_text(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        v = " ".join(str(x) for x in v if x is not None)
    elif isinstance(v, dict):
        v = " ".join(str(x) for x in v.values() if x is not None)
    t = str(v).strip()
    return t or None


def _as_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if x not in (None, "")]
    if isinstance(v, dict):
        return [str(x).strip() for x in v.values() if x not in (None, "")]
    t = str(v).strip()
    return [t] if t else []


def _is_veto(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "yes", "1")


def _headline(action: str, symbol: Optional[str], tail: Optional[str]) -> str:
    """'ACTION: SYMBOL — tail'. The prefix is code; any action the LLM led with is stripped."""
    import re
    t = re.sub(r"^\s*(BUY|HOLD|SELL|AVOID|WITHHELD)\b\s*[:\-—–]?\s*", "", tail or "",
               flags=re.I).strip()
    if symbol:
        t = re.sub(rf"^{re.escape(symbol.split('.')[0])}(\.NS|\.BO)?\s*[:\-—–]\s*", "", t,
                   flags=re.I).strip()
    head = f"{action}: {symbol}" if symbol else action
    return f"{head} — {t}"[:120] if t else head


def merge_narrative(decision: dict, llm: dict) -> dict:
    """
    Decision numbers + the LLM's text. The LLM cannot change a number; a veto
    only turns BUY into HOLD (recorded in decision_basis). Malformed text types
    are coerced, never allowed to break the run.
    """
    if not isinstance(llm, dict):
        llm = {}
    merged = {k: v for k, v in decision.items() if k != "synthesis_note"}
    for k in ("bull_case", "bear_case"):
        lst = _as_list(llm.get(k))
        if lst:
            merged[k] = lst
    for k in ("synthesis", "market_constraints", "headline"):
        t = _as_text(llm.get(k))
        if t:
            merged[k] = t
    ignored = sorted(k for k in DECISION_KEYS if k in llm)
    if ignored:
        log.info("LLM returned decision fields %s — ignored (numbers come from code)", ignored)
    basis = dict(merged.get("decision_basis") or {})
    if _is_veto(llm.get("veto")):
        reason = _as_text(llm.get("veto_reason")) or "no reason given"
        basis["veto"] = {"applied": merged["action"] == "BUY", "reason": reason,
                         "from_action": merged["action"]}
        if merged["action"] == "BUY":
            merged["action"] = "HOLD"
            merged["synthesis"] = (f"⚠ ANALYST VETO: BUY held back to HOLD — {reason}\n\n"
                                   + (merged.get("synthesis") or ""))
    merged["decision_basis"] = basis
    if decision.get("synthesis_note"):
        merged["synthesis"] = decision["synthesis_note"] + "\n\n" + (merged.get("synthesis") or "")
    # Headline prefix from code, after any veto, so it can never contradict the action.
    merged["headline"] = _headline(merged["action"], basis.get("symbol"), merged.get("headline"))
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
        "headline": f"composite {b.get('composite', 0):.0f}/100, narrative unavailable",
    })
    merged["decision_basis"] = {**merged.get("decision_basis", {}), "narrative": "template",
                                "narrative_reason": reason}
    return merged


# ─────────────────────────────────────────────────────────────────────────────
# Holdings review (WP-021, ADR-007)
# ─────────────────────────────────────────────────────────────────────────────
# For a stock already owned the question is keep / trim / sell, not "where to
# enter". The review answers it from two deterministic rules: the target
# updater's exit_signal() (EXIT / BOOK_PARTIAL / TRAIL / REVIEW / HOLD) and a
# thesis status from the discovery re-check's thesis_check(). Stop and target
# are READ from portfolio_holdings: the 17:00 target updater stays their only writer.

KIND_HOLDING_REVIEW = "HOLDING_REVIEW"
# The recommendations.action column only allows BUY/HOLD/SELL/AVOID/SUPPRESSED.
# A review is never a BUY — so it can never open a paper position.
REVIEW_TO_ACTION = {"EXIT": "SELL", "BOOK_PARTIAL": "HOLD", "TRAIL": "HOLD",
                    "REVIEW": "HOLD", "HOLD": "HOLD"}


def thesis_status(agent_results: Optional[dict], rule_action: Optional[str] = None) -> tuple[str, str]:
    """
    INTACT / WEAKENED / BROKEN (or UNKNOWN with no agent data).

    INTACT   — still passes discovery on fundamental + technical (the same
               test agents/discovery_rescorer.py applies to live calls)
    WEAKENED — no longer passes it
    BROKEN   — no longer passes it AND the composite rule says SELL/AVOID
    """
    if not agent_results:
        return "UNKNOWN", "no agent results today"
    try:
        from agents.discovery_rescorer import thesis_check
        intact, _signal, _upside, _gap, why = thesis_check(agent_results)
    except Exception as exc:
        log.warning("thesis_check failed: %s", exc)
        return "UNKNOWN", f"thesis check failed: {exc}"
    if intact:
        return "INTACT", why
    if (rule_action or "").upper() in ("SELL", "AVOID"):
        return "BROKEN", f"{why}; composite rule says {rule_action.upper()}"
    return "WEAKENED", why


def review_holding(
    holding: dict,
    agent_results: Optional[dict],
    fair_value: Optional[float] = None,
    *,
    price: Optional[float] = None,
    rule_action: Optional[str] = None,
) -> dict:
    """
    One review for one held stock. `price` (today's agent price) overrides the
    holding's stored current_price when given. Never raises.
    """
    from agents.target_updater import HOLD, REVIEW, exit_signal

    h = dict(holding)
    if _f(price):
        h["current_price"] = _f(price)
    review_action, reason = exit_signal(h, _f(fair_value))
    thesis, thesis_reason = thesis_status(agent_results, rule_action)
    if thesis == "BROKEN" and review_action == HOLD:
        review_action, reason = REVIEW, f"thesis broken — {thesis_reason}"
    return {
        "review_action": review_action,
        "review_reason": reason,
        "thesis_status": thesis,
        "thesis_reason": thesis_reason,
        "action": REVIEW_TO_ACTION.get(review_action, "HOLD"),
        "stop": _f(holding.get("stoploss_price")),
        "target": _f(holding.get("target_price")),
        "price": _f(h.get("current_price")),
        "fair_value": _f(fair_value),
    }
