"""
tests/test_decision.py — numbers from code, narrative from the LLM (WP-018, ADR-001).
"""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data import decision_config as dc  # noqa: E402
from scheduler import decision as d  # noqa: E402


class Cal:
    """Stand-in calibration: a fixed rate per call, with history."""
    has_history = True
    n = 120

    def __init__(self, rate=57.5, band_n=40):
        self.rate, self.bn = rate, band_n

    def confidence(self, s):
        return self.rate

    def band_n(self, s):
        return self.bn


def agents(signals=("BUY", "BUY", "HOLD"), fund_up=30.0, tech_up=10.0, atr=50.0, atr_stop=900.0):
    names = ("technical", "fundamental", "sentiment")
    res = {n: {"signal": s, "score": 70} for n, s in zip(names, signals)}
    res["technical"].update(upside_pct=tech_up, atr_14=atr, atr_stoploss=atr_stop,
                            detail={"indicators": {"current_price": 1000.0}})
    res["fundamental"].update(upside_pct=fund_up, danger_drop_pct=18.0, danger_confidence=40.0)
    return res


# ── the rule ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("comp,act", [(80, "BUY"), (65, "BUY"), (64.9, "HOLD"), (45, "HOLD"),
                                      (44.9, "SELL"), (30.5, "SELL"), (30, "AVOID"), (5, "AVOID")])
def test_action_rule(comp, act):
    assert d.action_for(comp) == act


def test_buy_with_consensus_uses_code_numbers():
    out = d.decide("X.NS", agents(), 80.0, 1000.0, Cal())
    assert out["action"] == "BUY"
    assert out["confidence"] == 57.5 and out["upside_confidence"] == 57.5
    assert (out["entry_low"], out["entry_high"], out["stoploss"]) == (950.0, 1000.0, 900.0)
    assert out["target"] == 1300.0 and out["upside_pct"] == 30.0
    assert out["horizon_days"] == dc.DECISION_HORIZON_DAYS
    assert out["danger_drop_pct"] == 18.0
    b = out["decision_basis"]
    assert b["source"] == "code" and b["confidence_basis"] == "calibrated" and b["band_n"] == 40


def test_upside_capped_at_100_like_discovery():
    out = d.decide("X.NS", agents(fund_up=250.0), 80.0, 1000.0, Cal())
    assert out["upside_pct"] == 100.0 and out["target"] == 2000.0
    assert out["decision_basis"]["upside_capped"] is True
    assert out["decision_basis"]["valuation_gap_pct"] == 250.0


def test_consensus_gate_moves_buy_to_hold_without_touching_confidence():
    out = d.decide("X.NS", agents(signals=("HOLD", "HOLD", "SELL")), 80.0, 1000.0, Cal())
    assert out["action"] == "HOLD"
    assert out["confidence"] == 57.5                         # still the calibrated rate
    assert "CONSENSUS GATE" in out["decision_basis"]["consensus_gate"]


def test_sell_has_no_entry_or_target_but_keeps_the_stop():
    out = d.decide("X.NS", agents(), 40.0, 1000.0, Cal())
    assert out["action"] == "SELL"
    assert out["entry_low"] is None and out["target"] is None and out["upside_pct"] == 0.0
    assert out["stoploss"] == 900.0


def test_no_track_record_is_neutral_and_says_so():
    from agents.call_calibration import Calibration
    out = d.decide("X.NS", agents(), 80.0, 1000.0, Calibration())
    assert out["confidence"] == 50.0
    assert out["decision_basis"]["confidence_basis"] == "no_track_record"


def test_missing_data_never_raises():
    out = d.decide("X.NS", {}, 55.0, None, None)
    assert out["action"] == "HOLD" and out["confidence"] == 50.0
    assert out["entry_low"] is None and out["stoploss"] is None and out["target"] is None


# ── the LLM contract ──────────────────────────────────────────────────────────

def test_llm_cannot_change_a_number():
    dec = d.decide("X.NS", agents(), 80.0, 1000.0, Cal())
    out = d.merge_narrative(dec, {"bull_case": ["a"], "bear_case": ["b"], "synthesis": "s",
                                  "headline": "h", "confidence": 99, "action": "AVOID",
                                  "target": 5000, "upside_confidence": 99})
    assert out["action"] == "BUY" and out["confidence"] == 57.5 and out["target"] == 1300.0
    assert out["synthesis"] == "s" and out["bull_case"] == ["a"]


def test_veto_turns_buy_into_hold_and_is_recorded():
    dec = d.decide("X.NS", agents(), 80.0, 1000.0, Cal())
    out = d.merge_narrative(dec, {"synthesis": "s", "veto": True, "veto_reason": "SEBI order"})
    assert out["action"] == "HOLD"
    assert out["decision_basis"]["veto"] == {"applied": True, "reason": "SEBI order",
                                             "from_action": "BUY"}
    assert out["synthesis"].startswith("⚠ ANALYST VETO")
    assert out["target"] == 1300.0                           # levels unchanged


def test_veto_cannot_upgrade_or_touch_non_buy():
    dec = d.decide("X.NS", agents(), 40.0, 1000.0, Cal())     # SELL
    out = d.merge_narrative(dec, {"veto": True, "veto_reason": "x", "action": "BUY"})
    assert out["action"] == "SELL"
    assert out["decision_basis"]["veto"]["applied"] is False


def test_null_narrative_fields_fall_back_safely():
    dec = d.decide("X.NS", agents(), 80.0, 1000.0, Cal())
    out = d.merge_narrative(dec, {"bull_case": None, "synthesis": None, "veto": None})
    assert out["action"] == "BUY" and "veto" not in out["decision_basis"]


def test_fallback_narrative_keeps_the_decision():
    a = agents()
    dec = d.decide("X.NS", a, 80.0, 1000.0, Cal())
    out = d.fallback_narrative("X.NS", dec, a, "unparseable reply")
    assert out["action"] == "BUY" and out["confidence"] == 57.5
    assert "unparseable reply" in out["synthesis"] and out["headline"].startswith("BUY")
    assert out["decision_basis"]["narrative"] == "template"


def test_prompt_has_no_number_fields_and_renders_fully():
    tpl = (ROOT / "prompts" / "orchestrator_synthesis.txt").read_text(encoding="utf-8")
    assert "upside_confidence" not in tpl
    assert "Confidence range" not in tpl and '"confidence"' not in tpl
    assert '"veto"' in tpl
    dec = d.decide("X.NS", agents(), 80.0, 1000.0, Cal())
    out = d.render_prompt(tpl, symbol="X.NS", agent_text="AGENTS", composite=80.0, decision=dec,
                          current_price=1000.0, atr_entry_zone="₹950.00", data_years="10",
                          data_quality="FULL")
    import re
    leftovers = set(re.findall(r"\{[a-z_]+\}", out))
    assert not leftovers, leftovers
    assert "**BUY**" in out and "57.5%" in out and "₹1,300.00" in out


def test_legacy_prompt_kept_for_rollback():
    assert (ROOT / "prompts" / "orchestrator_synthesis_llm.txt").exists()


# ── synthesise_node end to end ────────────────────────────────────────────────

AG = {
    "technical": {"signal": "BUY", "score": 80, "upside_pct": 12.0, "atr_14": 40.0,
                  "atr_stoploss": 920.0, "detail": {"indicators": {"current_price": 1000.0}}},
    "fundamental": {"signal": "BUY", "score": 82, "upside_pct": 25.0, "data_quality": "FULL",
                    "detail": {"raw_metrics": {"current_price": 1000.0}}},
    "sentiment": {"signal": "BUY", "score": 78}, "institutional": {"signal": "HOLD", "score": 75},
    "macro": {"signal": "NEUTRAL", "score": 72}, "historical_rag": {"signal": "HOLD", "score": 74},
    "commodities": {"signal": "NEUTRAL", "score": 70},
}


def _state(symbols=("TITAN.NS",), dry_run=True):
    return {"symbol_results": {s: AG for s in symbols},
            "agent_weights": dc.equal_weights(), "current_regime": None,
            "errors": [], "dry_run": dry_run}


def _resp(body: str):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=body)], stop_reason="end_turn",
                           usage=SimpleNamespace(input_tokens=1, output_tokens=1))


def _run(monkeypatch, body=None, key=True, symbols=("TITAN.NS",)):
    from scheduler import orchestrator as orch
    if key:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-real")
    else:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(orch, "_supabase", lambda: None)
    client = MagicMock()
    client.messages.create.return_value = _resp(body or "{}")
    with patch("anthropic.Anthropic", return_value=client), \
         patch("agents.earnings_guard.check_pre_earnings",
               return_value={"has_upcoming_earnings": False}), \
         patch("agents.governance_screener.screen_governance", return_value={"flag_count": 0}):
        return asyncio.run(orch.synthesise_node(_state(symbols)))


NARRATIVE = json.dumps({"bull_case": ["b1", "b2", "b3"], "bear_case": ["r1", "r2", "r3"],
                        "synthesis": "Narrative.", "headline": "BUY: TITAN", "veto": False,
                        "veto_reason": None, "confidence": 97, "action": "AVOID"})


def test_node_publishes_code_numbers_with_llm_text(monkeypatch):
    out = _run(monkeypatch, NARRATIVE)
    (rec,) = out["recommendations"]
    assert rec["action"] == "BUY"                        # composite ≈ 75.9, 3 bullish agents
    assert rec["confidence"] == 50.0                     # no track record → neutral, not the LLM's 97
    assert rec["target"] == 1250.0 and rec["stoploss"] == 920.0 and rec["entry_low"] == 960.0
    assert rec["summary"].startswith("Narrative") or "Narrative." in (rec["summary"] or "")
    assert rec["decision_meta"]["decision"]["source"] == "code"


def test_node_unparseable_reply_still_publishes_and_records_fallback(monkeypatch):
    out = _run(monkeypatch, "not json at all")
    (rec,) = out["recommendations"]
    assert rec["action"] == "BUY" and rec["target"] == 1250.0
    assert any("SYNTHESIS_FALLBACK" in e for e in out["errors"])
    assert rec["decision_meta"]["decision"]["narrative"] == "template"


def test_dry_run_three_symbols_without_anthropic_key(monkeypatch):
    """Acceptance: ANTHROPIC_API_KEY unset still yields three recommendations."""
    out = _run(monkeypatch, key=False, symbols=("A.NS", "B.NS", "C.NS"))
    assert len(out["recommendations"]) == 3
    assert all(r["action"] == "BUY" and r["target"] == 1250.0 for r in out["recommendations"])


def test_rollback_to_llm_numbers(monkeypatch):
    monkeypatch.setattr(dc, "DECISION_SOURCE", "llm")
    body = json.dumps({"action": "HOLD", "confidence": 61, "risk_score": 40, "synthesis": "x",
                       "headline": "h", "horizon_days": 180, "target": 1111})
    (rec,) = _run(monkeypatch, body)["recommendations"]
    assert rec["action"] == "HOLD" and rec["confidence"] == 61 and rec["target"] == 1111.0
    assert "decision" not in rec["decision_meta"]


def test_build_recommendation_does_not_backfill_a_deliberately_empty_target():
    from scheduler.orchestrator import _build_recommendation
    dec = d.decide("X.NS", agents(), 40.0, 1000.0, Cal())            # SELL, target None
    syn = d.merge_narrative(dec, {"synthesis": "s"})
    with patch("agents.governance_screener.screen_governance", return_value={"flag_count": 0}):
        a = agents()
        a["technical"]["detail"]["indicators"]["target_price_used"] = 1500.0
        rec = _build_recommendation("X.NS", syn, a, {}, 40.0)
    assert rec["action"] == "SELL" and rec["target"] is None


# ── composite calibration ─────────────────────────────────────────────────────

def test_composite_calibration_bands_by_composite():
    from agents.call_calibration import build_calibration, composite_score_of
    pairs = []
    for i in range(60):
        comp = 80.0 if i % 2 else 50.0
        pairs.append(({"symbol": f"S{i}", "metadata": {}},
                      {"symbol": f"S{i}", "rec_date": "2026-06-01", "composite_score": comp,
                       "alpha_t90": 0.1 if comp == 80.0 else -0.1}))
    cal = build_calibration(pairs, score_fn=composite_score_of, bands=dc.COMPOSITE_BANDS)
    assert cal.has_history and cal.n == 60
    assert cal.confidence(80.0) > 70 > 30 > cal.confidence(50.0)


def test_composite_calibration_without_client_is_neutral():
    from agents.call_calibration import load_composite_calibration
    cal = load_composite_calibration(None)
    assert cal.has_history is False and cal.confidence(80.0) == 50.0


def test_thresholds_are_the_recentred_provisional_values():
    """Owner decision 2026-11-16: 65/45/30 on the equal-weight composite (not validated)."""
    assert (dc.BUY_MIN_COMPOSITE, dc.HOLD_MIN_COMPOSITE, dc.AVOID_MAX_COMPOSITE) == (65.0, 45.0, 30.0)
    assert [lo for lo, _ in dc.COMPOSITE_BANDS] == [0.0, 30.01, 45.0, 65.0]
