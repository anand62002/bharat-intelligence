"""
tests/test_backfill_outcome_scores.py — WP-004 backfill, fact-checker kappa
carry-over, discovery call-time score, paper-book HOLDING_REVIEW skip.
No live DB: every client is a MagicMock.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import backfill_outcome_scores as bf  # noqa: E402


def _outcome(i, score=None, kappa=None):
    return {"id": f"o{i}", "rec_id": f"r{i}", "composite_score": score, "validation_kappa": kappa}


RECS = {
    "r1": {"id": "r1", "is_discovery": True, "metadata": {"signal_score": 81.0}},
    "r2": {"id": "r2", "is_discovery": True,
           "metadata": {"signal_score": 70.0, "last_confirmed_at": "2026-11-12"}},
    "r3": {"id": "r3", "is_discovery": False, "metadata": {},
           "gov_check": {"validation": {"aggregate_kappa": 0.16}}},
    "r4": {"id": "r4", "is_discovery": False, "metadata": {"composite_score": 55.0}},
    "r5": {"id": "r5", "is_discovery": True, "metadata": {}},
}


class TestPlanUpdates:
    def test_fills_only_null_fields(self):
        outs = [_outcome(1), _outcome(4, score=60.0)]
        ups, s = bf.plan_updates(outs, RECS)
        assert ups == [{"id": "o1", "set": {"composite_score": 81.0},
                        "before": {"composite_score": None, "validation_kappa": None}}]
        # o4 already had a score and r4 has no kappa → nothing derivable
        assert s["nothing_derivable"] == 1

    def test_never_overwrites_existing_value(self):
        ups, _ = bf.plan_updates([_outcome(1, score=12.0)], RECS)
        assert ups == []

    def test_kappa_only(self):
        ups, s = bf.plan_updates([_outcome(3)], RECS)
        assert ups[0]["set"] == {"validation_kappa": 0.16}
        assert s["fill_kappa"] == 1 and s["fill_score"] == 0

    def test_reconfirmed_counted_or_excluded(self):
        ups, s = bf.plan_updates([_outcome(2)], RECS)
        assert ups[0]["set"] == {"composite_score": 70.0}
        assert s["score_possibly_after_call"] == 1
        ups, s = bf.plan_updates([_outcome(2)], RECS, exclude_reconfirmed=True)
        assert ups == [] and s["excluded_reconfirmed"] == 1

    def test_missing_rec_and_underivable(self):
        ups, s = bf.plan_updates([_outcome(9), _outcome(5)], RECS)
        assert ups == [] and s["no_rec"] == 1 and s["nothing_derivable"] == 1


def _client(outcomes, recs):
    client = MagicMock()
    client.handles = []

    def table(name):
        t = MagicMock()
        if name == "recommendation_outcomes":
            t.select.return_value.range.return_value.execute.return_value = MagicMock(data=outcomes)
        else:
            t.select.return_value.in_.return_value.execute.return_value = MagicMock(data=recs)
        client.handles.append((name, t))
        return t

    client.table.side_effect = table
    return client


class TestMain:
    def test_dry_run_prints_count_and_writes_nothing(self, capsys, tmp_path):
        c = _client([_outcome(1), _outcome(3)], [RECS["r1"], RECS["r3"]])
        with patch.object(bf, "_client", return_value=c), \
             patch.object(bf, "write_backup") as wb:
            assert bf.main([]) == 0
        out = capsys.readouterr().out
        assert "rows that would be updated           : 2" in out
        assert "nothing written" in out
        wb.assert_not_called()
        for _, t in c.handles:
            for verb in ("insert", "update", "upsert", "delete"):
                assert not getattr(t, verb).called

    def test_run_writes_backup_before_updating(self, tmp_path, capsys):
        c = _client([_outcome(1)], [RECS["r1"]])
        order = []
        real_backup = bf.write_backup

        def backup(updates, _dir):
            order.append("backup")
            return real_backup(updates, tmp_path)

        def apply(client, updates):
            order.append("apply")
            return len(updates), []

        with patch.object(bf, "_client", return_value=c), \
             patch.object(bf, "write_backup", side_effect=backup), \
             patch.object(bf, "apply_updates", side_effect=apply):
            assert bf.main(["--run"]) == 0
        assert order == ["backup", "apply"]
        files = list(tmp_path.glob("outcome_scores_backup_*.json"))
        assert len(files) == 1 and '"id": "o1"' in files[0].read_text()

    def test_apply_updates_is_fill_only(self):
        client = MagicMock()
        q = client.table.return_value.update.return_value.eq.return_value
        q.is_.return_value = q
        done, errs = bf.apply_updates(client, [{"id": "o1", "set": {"composite_score": 81.0}}])
        assert (done, errs) == (1, [])
        q.is_.assert_called_once_with("composite_score", "null")


# ── fact checker keeps the judge result ──────────────────────────────────────

def test_fact_checker_keeps_validation():
    from governance.fact_checker import _keep_validation
    rec = {"gov_check": {"validation": {"aggregate_kappa": 0.52}}}
    new = _keep_validation(rec, {"claims_checked": 5, "flags": []})
    assert new["validation"] == {"aggregate_kappa": 0.52} and new["claims_checked"] == 5
    assert _keep_validation({}, {"flags": []}) == {"flags": []}


def test_fact_check_insufficient_claims_path_keeps_validation():
    from governance import fact_checker as fc
    rec = {"symbol": "TITAN.NS", "gov_check": {"validation": {"aggregate_kappa": 0.52}}}
    with patch.object(fc, "_extract_claims", return_value=[]):
        out, _ = fc._check_one(rec, {}, "", None, {}, True)
    assert out["gov_check"]["skipped_reason"].startswith("Insufficient claims")
    assert out["gov_check"]["validation"]["aggregate_kappa"] == 0.52


def test_fact_check_full_path_keeps_validation():
    from governance import fact_checker as fc
    rec = {"symbol": "TITAN.NS", "confidence": 60.0,
           "gov_check": {"validation": {"aggregate_kappa": 0.52}}}
    claims = [MagicMock() for _ in range(fc.MIN_CLAIMS_FOR_CHECK)]
    with patch.object(fc, "_extract_claims", return_value=claims), \
         patch.object(fc, "_numerical_grounding_check", return_value=0), \
         patch.object(fc, "_verify_claim"), \
         patch.object(fc, "_apply_governance_rules",
                      return_value=({"claims_checked": len(claims), "flags": []}, 0, False)):
        out, _ = fc._check_one(rec, {}, "", None, {}, True)
    assert out["gov_check"]["validation"]["aggregate_kappa"] == 0.52
    assert out["gov_check"]["claims_checked"] == len(claims)


# ── paper book skips HOLDING_REVIEW ──────────────────────────────────────────

def test_paper_book_drops_holding_reviews():
    from agents.paper_portfolio import _drop_holding_reviews
    recs = [{"id": 1, "metadata": {"kind": "HOLDING_REVIEW"}},
            {"id": 2, "metadata": {}}, {"id": 3, "metadata": None}]
    kept, n = _drop_holding_reviews(recs)
    assert [r["id"] for r in kept] == [2, 3] and n == 1


def test_open_new_positions_logs_skip_count(caplog):
    import logging
    from agents import paper_portfolio as pp
    recs = [{"id": "a", "symbol": "TITAN", "action": "BUY", "metadata": {"kind": "HOLDING_REVIEW"}}]
    client = MagicMock()

    def table(name):
        t = MagicMock()
        if name == "recommendations":
            t.select.return_value.in_.return_value.order.return_value.execute.return_value = \
                MagicMock(data=recs)
        else:
            t.select.return_value.execute.return_value = MagicMock(data=[])
        return t

    client.table.side_effect = table
    with caplog.at_level(logging.INFO):
        res = pp.open_new_positions(client, dry_run=True)
    assert "Skipped 1 HOLDING_REVIEW" in caplog.text
    assert res["opened"] == 0 and res["skipped"] >= 1


# ── discovery freezes the call-time score on insert ──────────────────────────

def test_discovery_insert_sets_call_signal_score():
    src = (Path(__file__).resolve().parent.parent / "agents" / "discovery_screener.py").read_text(encoding="utf-8")
    assert '"call_signal_score": result.upside_confidence' in src
    # only the INSERT path sets it; re-confirmation must not refresh it
    assert src.count("call_signal_score") == 1
