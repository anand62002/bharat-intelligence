"""
tests/test_discovery_rescorer.py — daily re-check of live discovery calls.

Pins the behaviours that fixed the frozen Discovery tab: one live call per
symbol, levels anchored to the original call price, stop/target closes, and
the thesis re-check deciding whether a call is extended.
"""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from agents import discovery_rescorer as dr
from agents.call_calibration import Calibration, SIGNAL_BANDS


def _cal(rate=45.0):
    return Calibration(
        bands=[{"lo": lo, "hi": hi, "n": 30, "hits": 13, "rate": rate} for lo, hi in SIGNAL_BANDS],
        overall=rate, n=120, has_history=True,
    )


# ── pure helpers ─────────────────────────────────────────────────────────────

def test_pick_primary_keeps_most_recently_confirmed():
    rows = [
        {"id": "may",  "created_at": "2026-05-29T05:00", "metadata": {}},
        {"id": "oct",  "created_at": "2026-10-18T05:00", "metadata": {"last_confirmed_at": "2026-10-20"}},
        {"id": "jun",  "created_at": "2026-06-11T05:00", "metadata": {"last_confirmed_at": "2026-11-09"}},
    ]
    primary, dups = dr.pick_primary(rows)
    assert primary["id"] == "jun"
    assert {d["id"] for d in dups} == {"may", "oct"}


def test_effective_levels_anchor_to_call_price():
    # Stored stop slid to −40% via re-confirmations; target was uncapped (+762%).
    sl, tg = dr.effective_levels(100.0, 60.0, 862.0)
    assert sl == 85.0       # never looser than −15% from the call
    assert tg == 200.0      # never beyond +100%


def test_effective_levels_keep_tighter_stop_and_derive_missing_target():
    sl, tg = dr.effective_levels(100.0, 92.0, None, upside_pct=762.0)
    assert sl == 92.0       # a tighter stored stop is kept
    assert tg == 200.0      # missing target derived from the CAPPED upside


@pytest.mark.parametrize("price,expected", [(84.0, dr.STOPPED_OUT), (201.0, dr.TARGET_HIT), (120.0, None)])
def test_price_status(price, expected):
    assert dr.price_status(price, 85.0, 200.0) == expected


def test_thesis_check_uses_screener_thresholds():
    strong = {"fundamental": {"upside_pct": 45.0, "score": 78}, "technical": {}}
    weak   = {"fundamental": {"upside_pct": 8.0,  "score": 78}, "technical": {}}
    assert dr.thesis_check(strong)[0] is True
    intact, _signal, upside, _gap, reason = dr.thesis_check(weak)
    assert intact is False and upside == 8.0 and "upside" in reason


def test_thesis_check_caps_upside_but_reports_raw_gap():
    _intact, _s, upside, gap, _r = dr.thesis_check({"fundamental": {"upside_pct": 300.0, "score": 80}})
    assert upside == 100.0 and gap == 300.0


def test_tier_needs_calibrated_hit_rate_not_raw_score():
    assert dr.tier_for(45.0, 80.0) == "STANDARD"
    assert dr.tier_for(62.0, 45.0) == "CRITICAL"


# ── run_rescore with a fake client ───────────────────────────────────────────

class _FakeClient:
    """Serves the live rows / outcome entries and records updates."""

    def __init__(self, rows, entries):
        self.rows, self.entries, self.updates = rows, entries, []

    def table(self, name):
        t = MagicMock()
        if name == "recommendations":
            sel = t.select.return_value.eq.return_value.gte.return_value.range.return_value
            sel.execute.return_value.data = self.rows

            def _update(payload):
                u = MagicMock()
                u.eq.side_effect = lambda _c, rec_id: (self.updates.append((rec_id, payload)), MagicMock())[1]
                return u
            t.update.side_effect = _update
        else:  # recommendation_outcomes
            t.select.return_value.in_.return_value.execute.return_value.data = [
                {"rec_id": k, "entry_price": v} for k, v in self.entries.items()
            ]
        return t


def _row(rid, sym, confirmed, conf=88.0, target=200.0, stop=85.0):
    return {"id": rid, "symbol": sym, "created_at": f"{confirmed}T05:00", "confidence": conf,
            "upside_confidence": conf, "target": target, "stoploss": stop, "upside_pct": 100.0,
            "headline": "✅ STANDARD DISCOVERY: X", "valid_till": "2027-01-01",
            "metadata": {"last_confirmed_at": confirmed, "discovery_count": 1}}


def _run(rows, entries, prices, agents=None, rerun=True):
    client = _FakeClient(rows, entries)
    with patch("agents.outcome_tracker._batch_current_prices", return_value=prices), \
         patch("agents.outcome_tracker._resolve_yf_symbol", side_effect=lambda s: s), \
         patch.object(dr, "_run_thesis_agents", side_effect=lambda s: (agents or {}).get(s, {})):
        summary = dr.run_rescore(dry_run=False, rerun_agents=rerun, client=client, calibration=_cal())
    return summary, dict(client.updates)


def test_duplicates_superseded_and_primary_rescored():
    rows = [_row("old", "INFY.NS", "2026-06-01"), _row("new", "INFY.NS", "2026-11-09")]
    strong = {"INFY.NS": {"fundamental": {"upside_pct": 40.0, "score": 75}}}
    summary, upd = _run(rows, {"new": 100.0}, {"INFY.NS": 110.0}, strong)

    assert summary["superseded"] == 1 and summary["ACTIVE"] == 1
    old = upd["old"]
    assert old["metadata"]["call_status"] == dr.SUPERSEDED
    assert old["valid_till"] < date.today().isoformat()

    new = upd["new"]
    assert new["confidence"] == 45.0                       # calibrated, not 88
    assert new["metadata"]["signal_score"] == 75           # today's raw score kept
    assert new["valid_till"] == (date.today() + timedelta(days=90)).isoformat()
    assert new["metadata"]["return_since_call_pct"] == 10.0


def test_stop_measured_from_call_price_closes_the_call():
    # Stored stop had slid to 50; the call was made at 100 and is now at 69.
    rows = [_row("r1", "SWIGGY.NS", "2026-10-01", stop=50.0)]
    summary, upd = _run(rows, {"r1": 100.0}, {"SWIGGY.NS": 69.0})
    assert summary["STOPPED_OUT"] == 1
    assert upd["r1"]["metadata"]["call_status"] == dr.STOPPED_OUT
    assert upd["r1"]["stoploss"] == 85.0 and upd["r1"]["metadata"]["prev_stoploss"] == 50.0


def test_weakened_thesis_is_flagged_but_not_extended():
    rows = [_row("r1", "ITC.NS", "2026-10-01")]
    weak = {"ITC.NS": {"fundamental": {"upside_pct": 5.0, "score": 80}}}
    summary, upd = _run(rows, {"r1": 100.0}, {"ITC.NS": 104.0}, weak)
    assert summary["WEAKENED"] == 1
    assert upd["r1"]["metadata"]["call_status"] == dr.WEAKENED
    assert "valid_till" not in upd["r1"]                   # ages out unless re-confirmed


def test_dry_run_writes_nothing():
    rows = [_row("r1", "TCS.NS", "2026-10-01")]
    client = _FakeClient(rows, {"r1": 100.0})
    with patch("agents.outcome_tracker._batch_current_prices", return_value={"TCS.NS": 101.0}), \
         patch("agents.outcome_tracker._resolve_yf_symbol", side_effect=lambda s: s):
        dr.run_rescore(dry_run=True, rerun_agents=False, client=client, calibration=_cal())
    assert client.updates == []
