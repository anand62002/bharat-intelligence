"""
GET /api/backtest/summary?split=ALL returns every split.

The dashboard used to fetch only split=TEST, so the TRAIN and FULL toggles on
the Walk-Forward Backtest panel always showed the "run the script" empty state
even though six monthly runs of each were in the table.
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _client(rows_by_split):
    c = MagicMock()

    def table(_name):
        t = MagicMock()

        def eq(_col, sp):
            q = MagicMock()
            q.order.return_value.limit.return_value.execute.return_value.data = rows_by_split.get(sp, [])
            return q
        t.select.return_value.eq.side_effect = eq
        return t
    c.table.side_effect = table
    return c


@pytest.mark.anyio
async def test_split_all_returns_all_three():
    import api.main as m
    data = {sp: [{"split_type": sp, "run_date": "2026-11-01"}] for sp in ("TEST", "TRAIN", "FULL")}
    with patch.object(m, "_supabase", _client(data)):
        out = await m.get_backtest_summary(split="all", limit=12, _=None)
    assert {r["split_type"] for r in out["results"]} == {"TEST", "TRAIN", "FULL"}


@pytest.mark.anyio
async def test_single_split_still_filters():
    import api.main as m
    data = {sp: [{"split_type": sp}] for sp in ("TEST", "TRAIN", "FULL")}
    with patch.object(m, "_supabase", _client(data)):
        out = await m.get_backtest_summary(split="TRAIN", limit=5, _=None)
    assert [r["split_type"] for r in out["results"]] == ["TRAIN"]
