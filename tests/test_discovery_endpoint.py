"""
tests/test_discovery_endpoint.py — GET /api/discovery and /api/recommendations
return what is CURRENT, not what had the biggest number months ago.

2026-11-10: both endpoints took the top-N rows by upside_pct. With 233 overlapping
"valid" discovery rows, May's +762% SPARC and +234% ITC held every slot and the
Discovery tab looked frozen while the daily-runs panel kept showing new finds.
"""

import os
import sys
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TODAY = date.today()


def _row(rid, sym, confirmed, upside, conf=56.3, status=None, headline="✅ STANDARD DISCOVERY: X",
         created=None):
    meta = {"last_confirmed_at": confirmed, "call_date": confirmed, "discovery_dates": [confirmed]}
    if status:
        meta["call_status"] = status
    return {
        "id": rid, "symbol": sym, "action": "BUY", "is_discovery": True,
        "created_at": f"{created or confirmed}T05:00:00", "valid_till": (TODAY + timedelta(days=60)).isoformat(),
        "upside_pct": upside, "upside_confidence": conf, "confidence": conf,
        "headline": headline, "metadata": meta, "agent_signals": {}, "gov_check": {},
        "entry_low": 1.0, "entry_high": 1.0, "target": 2.0, "stoploss": 0.9,
    }


def _fake_db(rows):
    db = MagicMock()
    q = db.table.return_value.select.return_value
    q.eq.return_value.gte.return_value.range.return_value.execute.return_value.data = rows
    q.order.return_value.limit.return_value.execute.return_value.data = rows
    return db


@pytest.fixture(autouse=True)
def _fresh_cache():
    import api.main as m
    m._discovery_cache.update(ts=0.0, data=None)
    yield
    m._discovery_cache.update(ts=0.0, data=None)


async def _discovery(rows):
    import api.main as m
    with patch.object(m, "_db", return_value=_fake_db(rows)), \
         patch("agents.outcome_tracker._batch_current_prices", return_value={}):
        return await m.get_discovery(None)


@pytest.mark.anyio
async def test_new_finds_are_not_crowded_out_by_old_extreme_upsides():
    old = [_row(f"o{i}", f"OLD{i}.NS", "2026-05-29", 700.0 - i) for i in range(60)]
    new = [_row("n1", "JIOFIN.NS", TODAY.isoformat(), 26.0)]
    out = await _discovery(old + new)
    symbols = [r["symbol"] for r in out]
    assert "JIOFIN.NS" in symbols
    assert symbols[0] == "JIOFIN.NS"          # most recently confirmed first


@pytest.mark.anyio
async def test_daily_recheck_does_not_make_old_finds_look_new():
    """The re-check stamps last_confirmed_at=today on every intact call; order
    must still follow when the screener last FOUND the stock."""
    old = _row("o", "SBILIFE.NS", "2026-08-03", 30.0)
    old["metadata"]["last_confirmed_at"] = TODAY.isoformat()
    new = _row("n", "JIOFIN.NS", (TODAY - timedelta(days=1)).isoformat(), 26.0)
    out = await _discovery([old, new])
    assert [r["symbol"] for r in out] == ["JIOFIN.NS", "SBILIFE.NS"]


@pytest.mark.anyio
async def test_one_call_per_symbol_most_recently_confirmed():
    rows = [_row("a", "INFY.NS", "2026-06-01", 150.0), _row("b", "INFY.NS", "2026-11-09", 40.0)]
    out = await _discovery(rows)
    assert [r["id"] for r in out] == ["b"]


@pytest.mark.anyio
async def test_closed_calls_are_hidden():
    rows = [_row("s", "SWIGGY.NS", "2026-10-01", 30.0, status="STOPPED_OUT"),
            _row("t", "SAREGAMA.NS", "2026-10-01", 20.0, status="TARGET_HIT"),
            _row("a", "TCS.NS", "2026-11-09", 40.0, status="ACTIVE")]
    out = await _discovery(rows)
    assert [r["symbol"] for r in out] == ["TCS.NS"]


@pytest.mark.anyio
async def test_critical_tier_sorts_first_then_recency():
    rows = [_row("s", "NEW.NS", TODAY.isoformat(), 30.0),
            _row("c", "CRIT.NS", "2026-10-01", 50.0, conf=62.0, headline="⚡ CRITICAL DISCOVERY: CRIT")]
    out = await _discovery(rows)
    assert [r["symbol"] for r in out] == ["CRIT.NS", "NEW.NS"]


@pytest.mark.anyio
async def test_lifecycle_fields_reach_the_dashboard():
    r = _row("a", "TCS.NS", "2026-11-01", 40.0, status="WEAKENED")
    r["metadata"].update(signal_score=91, calibration_n=14, return_since_call_pct=3.2,
                         days_active=9, status_reason="upside to fair value now 11% (< 20%)")
    out = (await _discovery([r]))[0]
    assert out["callStatus"] == "WEAKENED" and out["statusReason"].startswith("upside")
    assert out["signalScore"] == 91 and out["calibrationN"] == 14
    assert out["returnSinceCallPct"] == 3.2 and out["daysActive"] == 9


@pytest.mark.anyio
async def test_recommendations_newest_per_symbol_not_biggest_upside():
    import api.main as m
    rows = [  # arrive newest first, as the query orders them
        _row("n", "ITC.NS", TODAY.isoformat(), 20.0),
        _row("x", "SWIGGY.NS", TODAY.isoformat(), 30.0, status="STOPPED_OUT"),
        _row("o", "ITC.NS", "2026-05-29", 234.0),
        _row("p", "SPARC.NS", "2026-05-29", 762.0),
    ]
    with patch.object(m, "_db", return_value=_fake_db(rows)):
        out = await m.get_recommendations(limit=50, _=None)
    assert [r["id"] for r in out] == ["n", "p"]   # ITC's newest row; SWIGGY closed
