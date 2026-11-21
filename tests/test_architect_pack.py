"""
tests/test_architect_pack.py — the evidence pack carries each ADR's signals (WP-023, ADR-006).

The pack used to label discovery's daily_runs rows as orchestrator runs and
report suppression as a share of saved rows. It now reads runs by pipeline,
reports suppression per symbol analysed, adds the job-ledger and
candidate-ledger sections, splits the paper book by rec source, and puts an n
and an interval next to every rate.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import build_architect_pack as pack  # noqa: E402


# ── a permissive fake Supabase: any chain, rows per table ─────────────────────

class _Q:
    def __init__(self, rows):
        self.rows = list(rows)

    def __getattr__(self, name):            # select / eq / gte / order / limit / range / not_ …
        def chain(*a, **k):
            if name == "eq" and len(a) == 2:
                self.rows = [r for r in self.rows if r.get(a[0], a[1]) == a[1]]
            return self
        return chain

    @property
    def not_(self):
        return self

    def execute(self):
        return type("R", (), {"data": self.rows, "count": len(self.rows)})()


class FakeDB:
    def __init__(self, **tables):
        self.tables = tables

    def table(self, name):
        return _Q(self.tables.get(name, []))


TODAY = date.today()


# ── rates carry n and an interval ─────────────────────────────────────────────

def test_rate_has_n_and_interval():
    out = pack._rate(47, 100)
    assert out.startswith("47.0% [") and out.endswith("(n=100)")
    assert pack._rate(0, 0) == "— (n=0)"
    lo, hi = pack._wilson(47, 100)
    assert 0.38 < lo < 0.47 < hi < 0.56


# ── runs by pipeline ──────────────────────────────────────────────────────────

RUNS = ([{"pipeline": "orchestrator"}] * 3 + [{"pipeline": "discovery"}] * 5
        + [{"pipeline": "outcome_tracker"}] * 2 + [{"pipeline": None}] * 4)


def test_runs_by_pipeline_counts_match_group_by():
    assert pack.runs_by_pipeline(RUNS) == {"orchestrator": 3, "discovery": 5, "outcome_tracker": 2,
                                           "(untagged, pre-WP-005)": 4}


def test_pipeline_section_reports_orchestrator_and_discovery_on_separate_lines():
    db = FakeDB(daily_runs=RUNS, recommendations=[])
    text = pack.section_pipeline_health(db)
    lines = text.splitlines()
    assert "- orchestrator runs: 3" in lines and "- discovery runs: 5" in lines
    assert "outcome_tracker 2" in text


def test_suppression_is_a_share_of_symbols_analysed():
    rows = [{"symbols_processed": 24, "details": {"reasons": {"suppressed_by_judges": 3, "synthesis_failed": 0}}},
            {"symbols_processed": 24, "details": {"reasons": {"suppressed_by_judges": 1}}}]
    out = pack.suppression_share(rows)
    assert "Symbols analysed by the orchestrator: 48 over 2 runs" in out
    assert "suppressed_by_judges 8.3% [" in out and "(n=48)" in out


# ── job ledger ────────────────────────────────────────────────────────────────

def test_ledger_coverage_names_the_missing_jobs():
    d1, d2 = TODAY - timedelta(days=2), TODAY - timedelta(days=1)
    rows = [{"run_date": d1.isoformat(), "pipeline": "orchestrator"},
            {"run_date": d1.isoformat(), "pipeline": "discovery"},
            {"run_date": d2.isoformat(), "pipeline": "orchestrator"}]
    cov = pack.ledger_coverage(rows, [d1, d2], due_fn=lambda d: {"orchestrator", "discovery"})
    assert cov[0] == (d1, 2, 2, [])
    assert cov[1] == (d2, 2, 1, ["discovery"])


def test_due_jobs_come_from_the_real_schedule():
    monday = TODAY - timedelta(days=TODAY.weekday())
    due = pack.due_jobs(monday)
    assert {"orchestrator", "regime_detector", "outcome_tracker"} <= due


def test_allowlist_count_matches_the_file():
    lines = [l for l in (ROOT / "tests" / "exception_allowlist.txt").read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.startswith("#")]
    assert pack.allowlist_count() == len(lines)


def test_llm_tokens_per_pipeline():
    rows = [{"pipeline": "market_digest_morning",
             "details": {"llm_usage": {"calls": 1, "input_tokens": 900, "output_tokens": 100}}},
            {"pipeline": "market_digest_morning",
             "details": {"llm_usage": {"calls": 1, "input_tokens": 1900, "output_tokens": 100}}},
            {"pipeline": "x", "details": {"llm_usage": {"calls": 0}}}]
    assert pack.llm_tokens(rows) == ["  - market_digest_morning: median 1,500 tokens/run over 2 runs"]


# ── paper book by source ──────────────────────────────────────────────────────

def test_paper_split_by_rec_source():
    recs = [{"id": "d1", "is_discovery": True, "metadata": {}},
            {"id": "p1", "is_discovery": False, "metadata": {}}]
    pos = [{"rec_id": "d1", "status": "CLOSED", "realized_pnl_pct": 5.0, "alpha_pct": 2.0,
            "position_label": "Half position (2.5%)"},
           {"rec_id": "d1", "status": "OPEN", "position_label": "Base position (2.5%)"},
           {"rec_id": "p1", "status": "CLOSED", "realized_pnl_pct": -3.0, "alpha_pct": -4.0,
            "position_label": "Half position (2.5%)"}]
    out = pack.paper_by_source(pos, recs)
    assert "| discovery | 2 | 1 | 1 | 100.0% [" in out
    assert "| portfolio | 1 | 0 | 1 | 0.0% [" in out
    assert "Base position (2.5%)" in out


# ── candidate ledger ─────────────────────────────────────────────────────────

def _cand(i, stage, alpha, gate=None, src="discovery"):
    d = TODAY - timedelta(days=40 + (i % 50))
    return {"run_date": d.isoformat(), "symbol": f"S{i}", "source": src, "stage": stage,
            "data_quality": "FULL" if stage != "SCREENED" else None, "gate_score": gate,
            "signal_score": 70.0, "features": {"rsi": 50.0}, "agent_signals": {},
            "alpha_t30": alpha, "alpha_t90": None}


def test_candidate_sections_on_fixture_rows():
    rows = ([_cand(i, "PRESCREEN_PASS", 0.05 if i % 3 else -0.02) for i in range(30)]
            + [_cand(100 + i, "SCREENED", -0.03 if i % 3 else 0.01) for i in range(60)]
            + [_cand(200 + i, "PROMOTED", 0.04 if i >= 10 else -0.04, gate=i / 20, src="portfolio")
               for i in range(20)])
    out = pack.candidate_sections(rows)
    assert "- Rows: 110" in out and "'discovery': 90" in out
    assert "Labelled: t+30 110, t+90 0" in out
    assert "- passed: 66.7% [" in out and "- failed: 33.3% [" in out and "weekly clusters" in out
    assert "- top half: 100.0% [" in out and "- bottom half: 0.0% [" in out
    assert "insufficient data: 90 rows" in out          # WP-017 report, refusing honestly


def test_pack_builds_with_an_empty_candidates_table(tmp_path, monkeypatch):
    """Acceptance: the pack builds and has 'Job ledger' and 'Candidate ledger' headings."""
    monkeypatch.setattr(pack, "OUT", tmp_path / "CURRENT_STATE.md")
    monkeypatch.setattr(pack, "_db", lambda: FakeDB(daily_runs=RUNS))
    pack.main()
    text = (tmp_path / "CURRENT_STATE.md").read_text(encoding="utf-8")
    assert "## Job ledger" in text and "## Candidate ledger" in text
    assert "- No candidate rows yet." in text
    assert "- orchestrator runs: 3" in text and "- discovery runs: 5" in text


def test_ledger_start_ignores_pre_ledger_pipelines():
    rows = [{"run_date": "2026-11-01", "pipeline": "discovery"},
            {"run_date": "2026-11-03", "pipeline": "orchestrator"},
            {"run_date": "2026-11-15", "pipeline": "regime_detector"},
            {"run_date": "2026-11-16", "pipeline": "outcome_tracker"}]
    assert pack.ledger_start(rows) == date(2026, 11, 15)
    assert pack.ledger_start([{"run_date": "2026-11-01", "pipeline": "discovery"}]) is None
