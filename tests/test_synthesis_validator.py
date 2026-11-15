"""
tests/test_synthesis_validator.py — the gate score, honestly named (WP-015, ADR-004).

"aggregate_kappa" is mean quality × agreement, not a kappa statistic. It is now
also emitted as `gate_score`, surfaced to the API as `gateScore`, and written
to the candidate ledger for every gated holding — published or suppressed — so
it can later be tested against outcomes. Thresholds and suppression are
unchanged.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scheduler import synthesis_validator as sv  # noqa: E402
# Imported at collection time, like tests/test_transform_research.py: api.main
# builds its Supabase client at import, which the WP-008 guard blocks inside a test.
from api.main import _gate_score, _transform_recommendation  # noqa: E402


def _outcome(status="PASS", gate=0.5123456):
    dims = {
        "data_provenance": sv.DimensionResult(
            name="data_provenance", scores={"gpt": 4, "sonnet": 4, "opus": 3},
            rationales={}, quality=0.6667, agreement=0.8333, kappa=0.5556),
    }
    return sv.ValidationOutcome(
        status=status, aggregate_kappa=gate, dimensions=dims, failed_dimensions=[],
        caveats=[], suppression_reason="low" if status == "SUPPRESSED" else None,
        judge_errors=[], elapsed_seconds=1.0,
    )


# ── ValidationOutcome ─────────────────────────────────────────────────────────

def test_to_dict_has_gate_score_and_aggregate_kappa_with_same_value():
    d = _outcome().to_dict()
    assert d["gate_score"] == d["aggregate_kappa"] == 0.5123
    assert d["dimension_quality"] == {"data_provenance": 0.6667}


def test_gate_is_quality_times_agreement_not_chance_corrected():
    quality, agreement, gate = sv._compute_dimension_kappa([4, 4, 4])
    assert (quality, agreement, gate) == (0.75, 1.0, 0.75)
    # A real kappa would be undefined/0 for zero variance; the gate is not a kappa.


def test_thresholds_unchanged():
    """WP-015 renames; it must not move the gate."""
    assert sv.KAPPA_SUPPRESS == 0.30
    assert sv.KAPPA_SUPPRESS_DISCOVERY == 0.35


# ── API transformer ───────────────────────────────────────────────────────────

def test_api_gate_score_new_and_legacy_rows():
    assert _gate_score({"validation": {"gate_score": 0.41, "aggregate_kappa": 0.41}}) == 0.41
    assert _gate_score({"validation": {"aggregate_kappa": 0.37}}) == 0.37      # pre-WP-015 row
    assert _gate_score({}) is None and _gate_score(None) is None
    assert _gate_score({"validation": "garbage"}) is None


def test_transform_recommendation_exposes_gate_score():
    out = _transform_recommendation({"id": "r1", "symbol": "TITAN",
                                     "gov_check": {"validation": {"aggregate_kappa": 0.52}}})
    assert out["gateScore"] == 0.52 and out["govCheck"]["validation"]["aggregate_kappa"] == 0.52


# ── Ledger row ────────────────────────────────────────────────────────────────

AGENTS = {
    "fundamental": {"signal": "HOLD", "score": 55, "data_quality": "FULL",
                    "detail": {"valuation": {"current_price": 3250.0}}},
    "technical": {"signal": "BUY", "score": 62},
}


def test_portfolio_ledger_row_suppressed_and_published():
    from scheduler.orchestrator import _portfolio_ledger_row
    sup = _portfolio_ledger_row("TITAN", AGENTS, 58.4, {"action": "HOLD"},
                                _outcome("SUPPRESSED", 0.21), published=False)
    pub = _portfolio_ledger_row("TITAN", AGENTS, 58.4, {"action": "HOLD"},
                                _outcome("PASS", 0.55), published=True)
    assert sup["source"] == "portfolio" and sup["stage"] == "DEEP" and sup["gate_score"] == 0.21
    assert sup["features"]["gate_status"] == "SUPPRESSED" and sup["features"]["published"] is False
    assert sup["features"]["dimension_quality"] == {"data_provenance": 0.6667}
    assert pub["stage"] == "PROMOTED" and pub["gate_score"] == 0.55
    assert sup["price"] == 3250.0 and sup["yf_symbol"] == "TITAN.NS" and sup["signal_score"] == 58.4
    assert set(sup) == set(pub)                       # one key set → one batch


def test_portfolio_ledger_row_without_gate():
    from scheduler.orchestrator import _portfolio_ledger_row
    row = _portfolio_ledger_row("X", AGENTS, 50.0, {}, None, published=True)
    assert row["gate_score"] is None and row["features"]["gate_status"] == "NOT_VALIDATED"


# ── synthesise_node end to end: a suppressed symbol still writes its row ──────

def _state(dry_run=False):
    return {
        "symbol_results": {"TITAN.NS": AGENTS},
        "agent_weights": {"fundamental": 0.5, "technical": 0.5},
        "current_regime": None, "errors": [], "dry_run": dry_run,
    }


def _claude_response():
    body = json.dumps({"action": "HOLD", "confidence": 55, "risk_score": 40,
                       "synthesis": "x", "headline": "h", "horizon_days": 90})
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=body)],
                           stop_reason="end_turn",
                           usage=SimpleNamespace(input_tokens=1, output_tokens=1))


@pytest.fixture
def run_node(monkeypatch):
    import asyncio
    from scheduler import orchestrator as orch

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-real")
    monkeypatch.setattr(orch, "_supabase", lambda: None)
    monkeypatch.setattr(orch, "_log_suppressed_synthesis", lambda *a, **k: None)
    client = MagicMock()
    client.messages.create.return_value = _claude_response()
    writes = []
    with patch("anthropic.Anthropic", return_value=client), \
         patch("agents.earnings_guard.check_pre_earnings",
               return_value={"has_upcoming_earnings": False}), \
         patch("data.candidate_ledger.get_client", return_value=object()), \
         patch("data.candidate_ledger.nifty_level", return_value=24000.0), \
         patch("data.candidate_ledger.record_candidates",
               side_effect=lambda c, rows: writes.extend(rows) or len(rows)):

        def run(outcome, dry_run=False):
            async def fake_validate(**kw):
                return outcome
            with patch("scheduler.synthesis_validator.validate_synthesis", fake_validate):
                return asyncio.run(orch.synthesise_node(_state(dry_run))), writes
        yield run


def test_suppressed_symbol_still_produces_ledger_write(run_node):
    out, writes = run_node(_outcome("SUPPRESSED", 0.21))
    assert out["recommendations"] == []                       # suppression unchanged
    assert len(writes) == 1
    w = writes[0]
    assert (w["symbol"], w["source"], w["stage"]) == ("TITAN", "portfolio", "DEEP")
    assert w["gate_score"] == 0.21 and w["nifty"] == 24000.0


def test_published_symbol_writes_promoted_row(run_node):
    out, writes = run_node(_outcome("PASS", 0.6))
    assert len(out["recommendations"]) == 1
    assert writes[0]["stage"] == "PROMOTED" and writes[0]["gate_score"] == 0.6


def test_dry_run_writes_no_ledger_rows(run_node):
    _, writes = run_node(_outcome("PASS", 0.6), dry_run=True)
    assert writes == []
