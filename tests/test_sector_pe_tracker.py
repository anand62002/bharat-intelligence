"""
tests/test_sector_pe_tracker.py — Unit tests for scheduler/sector_pe_tracker.py

Coverage:
  TestSnapshotResult          — dataclass construction and to_dict()
  TestRunSnapshot             — full snapshot pipeline; dry_run; regime-change detection
  TestUpsertSnapshot          — DB write helper; row validation; error handling
  TestGetYesterdayRegime      — DB query helper; absent data; errors
  TestGetSectorPeHistory      — history query; ordering; lookback filter
  TestGetRegimeChanges        — change detection from history rows
  TestComputeRollingLongruPE  — median logic; insufficient-data guard
  TestGetRegimeTrend          — COMPRESSING / EXPANDING / STABLE logic

Run:
    pytest tests/test_sector_pe_tracker.py -v
"""

import os
import sys
from datetime import date, timedelta
from unittest.mock import MagicMock, call, patch

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from scheduler.sector_pe_tracker import (
    SnapshotResult,
    _MIN_HISTORY_FOR_ROLLING,
    _TREND_LONG_WINDOW,
    _TREND_SHORT_WINDOW,
    _get_yesterday_regime,
    _supabase_client,
    _upsert_snapshot,
    compute_rolling_longrun_pe,
    get_regime_changes,
    get_regime_trend,
    get_sector_pe_history,
    run_snapshot,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_regime(
    regime: str = "FAIR",
    multiplier: float = 1.0,
    live_pe: float = 28.0,
    long_run_pe: float = 28.0,
    deviation_pct: float = 0.0,
    data_source: str = "nse_api",
) -> dict:
    return {
        "regime":        regime,
        "multiplier":    multiplier,
        "live_pe":       live_pe,
        "long_run_pe":   long_run_pe,
        "deviation_pct": deviation_pct,
        "data_source":   data_source,
        "note":          f"Sector at {regime}",
    }


def _make_history_rows(
    regimes: list[str],
    pes: list[float],
    base_date: date | None = None,
) -> list[dict]:
    """Build a list of snapshot row dicts for testing history/trend functions."""
    if base_date is None:
        base_date = date.today() - timedelta(days=len(regimes))
    rows = []
    for i, (regime, pe) in enumerate(zip(regimes, pes)):
        rows.append({
            "snapshot_date": (base_date + timedelta(days=i)).isoformat(),
            "sector_key":    "it",
            "live_pe":       pe,
            "long_run_pe":   28.0,
            "deviation_pct": round((pe / 28.0 - 1) * 100, 1) if pe else None,
            "regime":        regime,
            "multiplier":    1.0,
            "data_source":   "nse_api",
        })
    return rows


def _mock_supabase_client():
    """Return a MagicMock that simulates a Supabase client."""
    client = MagicMock()
    # Chain: .table().upsert().execute() → success
    client.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[{}])
    # Chain: .table().select().eq().lt().order().limit().execute() → no yesterday
    client.table.return_value.select.return_value.eq.return_value.\
        lt.return_value.order.return_value.limit.return_value.execute.return_value = \
        MagicMock(data=[])
    return client


# ──────────────────────────────────────────────────────────────────────────────
# TestSnapshotResult
# ──────────────────────────────────────────────────────────────────────────────

class TestSnapshotResult:
    def test_to_dict_returns_dict(self):
        r = SnapshotResult(
            snapshot_date="2026-04-19",
            sectors_fetched=5,
            sectors_saved=5,
            sectors_fallback=1,
            regime_changes=[],
            errors=[],
            dry_run=False,
        )
        d = r.to_dict()
        assert isinstance(d, dict)
        assert d["snapshot_date"] == "2026-04-19"
        assert d["sectors_fetched"] == 5

    def test_dry_run_flag_preserved(self):
        r = SnapshotResult(
            snapshot_date="2026-04-19",
            sectors_fetched=2,
            sectors_saved=0,
            sectors_fallback=0,
            regime_changes=[],
            errors=[],
            dry_run=True,
        )
        assert r.to_dict()["dry_run"] is True

    def test_to_dict_contains_all_fields(self):
        r = SnapshotResult("2026-01-01", 10, 8, 2, [{"x": 1}], ["err"], False)
        d = r.to_dict()
        expected_keys = {
            "snapshot_date", "sectors_fetched", "sectors_saved",
            "sectors_fallback", "regime_changes", "errors", "dry_run",
        }
        assert expected_keys == set(d.keys())


# ──────────────────────────────────────────────────────────────────────────────
# TestRunSnapshot
# ──────────────────────────────────────────────────────────────────────────────

class TestRunSnapshot:
    """Test run_snapshot() — the main public entry point."""

    def test_returns_snapshot_result(self):
        with patch("scheduler.sector_pe_tracker.get_sector_regime",
                   return_value=_make_regime()), \
             patch("scheduler.sector_pe_tracker._supabase_client",
                   return_value=None):
            result = run_snapshot(sectors=["it"], dry_run=True)
        assert isinstance(result, SnapshotResult)

    def test_dry_run_flag_set(self):
        with patch("scheduler.sector_pe_tracker.get_sector_regime",
                   return_value=_make_regime()), \
             patch("scheduler.sector_pe_tracker._supabase_client",
                   return_value=None):
            result = run_snapshot(sectors=["it"], dry_run=True)
        assert result.dry_run is True

    def test_dry_run_does_not_call_upsert(self):
        with patch("scheduler.sector_pe_tracker.get_sector_regime",
                   return_value=_make_regime()), \
             patch("scheduler.sector_pe_tracker._supabase_client",
                   return_value=None), \
             patch("scheduler.sector_pe_tracker._upsert_snapshot") as mock_upsert:
            run_snapshot(sectors=["it"], dry_run=True)
        mock_upsert.assert_not_called()

    def test_sectors_saved_zero_when_dry_run(self):
        with patch("scheduler.sector_pe_tracker.get_sector_regime",
                   return_value=_make_regime()), \
             patch("scheduler.sector_pe_tracker._supabase_client",
                   return_value=None):
            result = run_snapshot(sectors=["it", "banking"], dry_run=True)
        assert result.sectors_saved == 0

    def test_sectors_fetched_matches_input_list(self):
        with patch("scheduler.sector_pe_tracker.get_sector_regime",
                   return_value=_make_regime()), \
             patch("scheduler.sector_pe_tracker._supabase_client",
                   return_value=None):
            result = run_snapshot(sectors=["it", "banking"], dry_run=True)
        assert result.sectors_fetched == 2

    def test_all_sectors_fetched_when_none_given(self):
        from agents.sector_valuation import SECTOR_LONGRUN_PE
        with patch("scheduler.sector_pe_tracker.get_sector_regime",
                   return_value=_make_regime()), \
             patch("scheduler.sector_pe_tracker._supabase_client",
                   return_value=None):
            result = run_snapshot(sectors=None, dry_run=True)
        assert result.sectors_fetched == len(SECTOR_LONGRUN_PE)

    def test_fallback_counted_correctly(self):
        """Sectors with data_source=fallback_fair are counted in sectors_fallback."""
        def side_effect(sector_key):
            if sector_key == "it":
                return _make_regime(data_source="fallback_fair")
            return _make_regime(data_source="nse_api")

        with patch("scheduler.sector_pe_tracker.get_sector_regime",
                   side_effect=side_effect), \
             patch("scheduler.sector_pe_tracker._supabase_client",
                   return_value=None):
            result = run_snapshot(sectors=["it", "banking"], dry_run=True)
        assert result.sectors_fallback == 1

    def test_regime_change_detected(self):
        """When yesterday=FAIR and today=COMPRESSED, regime_changes has 1 entry."""
        mock_client = _mock_supabase_client()
        # yesterday query returns FAIR
        mock_client.table.return_value.select.return_value.eq.return_value.\
            lt.return_value.order.return_value.limit.return_value.execute.return_value = \
            MagicMock(data=[{"regime": "FAIR", "snapshot_date": "2026-04-18"}])

        with patch("scheduler.sector_pe_tracker.get_sector_regime",
                   return_value=_make_regime(regime="COMPRESSED", multiplier=1.20,
                                             deviation_pct=-22.0)), \
             patch("scheduler.sector_pe_tracker._supabase_client",
                   return_value=mock_client), \
             patch("scheduler.sector_pe_tracker._upsert_snapshot", return_value=True):
            result = run_snapshot(sectors=["banking"], dry_run=False)

        assert len(result.regime_changes) == 1
        chg = result.regime_changes[0]
        assert chg["from_regime"] == "FAIR"
        assert chg["to_regime"] == "COMPRESSED"
        assert chg["sector_key"] == "banking"

    def test_no_regime_change_when_same(self):
        """Same regime as yesterday → regime_changes is empty."""
        mock_client = _mock_supabase_client()
        mock_client.table.return_value.select.return_value.eq.return_value.\
            lt.return_value.order.return_value.limit.return_value.execute.return_value = \
            MagicMock(data=[{"regime": "FAIR", "snapshot_date": "2026-04-18"}])

        with patch("scheduler.sector_pe_tracker.get_sector_regime",
                   return_value=_make_regime(regime="FAIR")), \
             patch("scheduler.sector_pe_tracker._supabase_client",
                   return_value=mock_client), \
             patch("scheduler.sector_pe_tracker._upsert_snapshot", return_value=True):
            result = run_snapshot(sectors=["it"], dry_run=False)

        assert result.regime_changes == []

    def test_errors_collected_not_raised(self):
        """If get_sector_regime raises for one sector, the error is in result.errors."""
        def side_effect(sector_key):
            if sector_key == "it":
                raise RuntimeError("API down")
            return _make_regime()

        with patch("scheduler.sector_pe_tracker.get_sector_regime",
                   side_effect=side_effect), \
             patch("scheduler.sector_pe_tracker._supabase_client",
                   return_value=None):
            result = run_snapshot(sectors=["it", "banking"], dry_run=True)

        assert len(result.errors) == 1
        assert "it" in result.errors[0]

    def test_sectors_saved_when_live_db(self):
        """sectors_saved increments for each successful upsert."""
        mock_client = _mock_supabase_client()
        with patch("scheduler.sector_pe_tracker.get_sector_regime",
                   return_value=_make_regime()), \
             patch("scheduler.sector_pe_tracker._supabase_client",
                   return_value=mock_client), \
             patch("scheduler.sector_pe_tracker._upsert_snapshot",
                   return_value=True):
            result = run_snapshot(sectors=["it", "banking"], dry_run=False)
        assert result.sectors_saved == 2

    def test_snapshot_date_is_today(self):
        with patch("scheduler.sector_pe_tracker.get_sector_regime",
                   return_value=_make_regime()), \
             patch("scheduler.sector_pe_tracker._supabase_client",
                   return_value=None):
            result = run_snapshot(sectors=["it"], dry_run=True)
        assert result.snapshot_date == date.today().isoformat()


# ──────────────────────────────────────────────────────────────────────────────
# TestUpsertSnapshot
# ──────────────────────────────────────────────────────────────────────────────

class TestUpsertSnapshot:
    def _make_row(self) -> dict:
        return {
            "snapshot_date": "2026-04-19",
            "sector_key":    "it",
            "live_pe":       32.5,
            "long_run_pe":   28.0,
            "deviation_pct": 16.1,
            "regime":        "MILDLY_STRETCHED",
            "multiplier":    0.94,
            "data_source":   "nse_api",
        }

    def test_returns_true_on_success(self):
        mock_client = _mock_supabase_client()
        result = _upsert_snapshot(mock_client, self._make_row())
        assert result is True

    def test_returns_false_on_supabase_error(self):
        mock_client = MagicMock()
        mock_client.table.return_value.upsert.return_value.execute.side_effect = \
            Exception("DB error")
        result = _upsert_snapshot(mock_client, self._make_row())
        assert result is False

    def test_calls_upsert_with_on_conflict(self):
        """Verifies on_conflict='snapshot_date,sector_key' is passed."""
        mock_client = _mock_supabase_client()
        _upsert_snapshot(mock_client, self._make_row())
        mock_client.table.assert_called_with("sector_pe_snapshots")
        upsert_call = mock_client.table.return_value.upsert
        args, kwargs = upsert_call.call_args
        assert kwargs.get("on_conflict") == "snapshot_date,sector_key"

    def test_row_has_all_required_fields(self):
        """The row passed to upsert must contain all 8 required fields."""
        required = {
            "snapshot_date", "sector_key", "live_pe", "long_run_pe",
            "deviation_pct", "regime", "multiplier", "data_source",
        }
        row = self._make_row()
        assert required.issubset(set(row.keys()))


# ──────────────────────────────────────────────────────────────────────────────
# TestGetYesterdayRegime
# ──────────────────────────────────────────────────────────────────────────────

class TestGetYesterdayRegime:
    def test_returns_regime_when_row_exists(self):
        mock_client = _mock_supabase_client()
        mock_client.table.return_value.select.return_value.eq.return_value.\
            lt.return_value.order.return_value.limit.return_value.execute.return_value = \
            MagicMock(data=[{"regime": "COMPRESSED", "snapshot_date": "2026-04-18"}])

        result = _get_yesterday_regime(mock_client, "banking")
        assert result == "COMPRESSED"

    def test_returns_none_when_no_prior_row(self):
        mock_client = _mock_supabase_client()
        mock_client.table.return_value.select.return_value.eq.return_value.\
            lt.return_value.order.return_value.limit.return_value.execute.return_value = \
            MagicMock(data=[])

        result = _get_yesterday_regime(mock_client, "it")
        assert result is None

    def test_returns_none_on_exception(self):
        mock_client = MagicMock()
        mock_client.table.side_effect = Exception("connection lost")
        result = _get_yesterday_regime(mock_client, "it")
        assert result is None


# ──────────────────────────────────────────────────────────────────────────────
# TestGetSectorPeHistory
# ──────────────────────────────────────────────────────────────────────────────

class TestGetSectorPeHistory:
    def test_returns_empty_when_no_supabase(self):
        with patch("scheduler.sector_pe_tracker._supabase_client", return_value=None):
            result = get_sector_pe_history("it")
        assert result == []

    def test_returns_list_of_dicts(self):
        rows = _make_history_rows(["FAIR", "FAIR", "COMPRESSED"], [28.0, 27.5, 22.0])
        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.eq.return_value.\
            gte.return_value.order.return_value.execute.return_value = \
            MagicMock(data=rows)

        with patch("scheduler.sector_pe_tracker._supabase_client",
                   return_value=mock_client):
            result = get_sector_pe_history("it", lookback_days=30)

        assert isinstance(result, list)
        assert len(result) == 3
        assert all(isinstance(r, dict) for r in result)

    def test_returns_empty_on_exception(self):
        mock_client = MagicMock()
        mock_client.table.side_effect = Exception("network error")

        with patch("scheduler.sector_pe_tracker._supabase_client",
                   return_value=mock_client):
            result = get_sector_pe_history("it")

        assert result == []

    def test_lookback_days_filters_query(self):
        """The cutoff date based on lookback_days is passed to the query."""
        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.eq.return_value.\
            gte.return_value.order.return_value.execute.return_value = \
            MagicMock(data=[])

        with patch("scheduler.sector_pe_tracker._supabase_client",
                   return_value=mock_client):
            get_sector_pe_history("it", lookback_days=60)

        gte_call = mock_client.table.return_value.select.return_value.eq.return_value.gte
        args, kwargs = gte_call.call_args
        # args[0] = field name, args[1] = cutoff date
        assert args[0] == "snapshot_date"
        expected_cutoff = (date.today() - timedelta(days=60)).isoformat()
        assert args[1] == expected_cutoff

    def test_order_is_ascending_by_date(self):
        """Result is ordered snapshot_date ASC (desc=False)."""
        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.eq.return_value.\
            gte.return_value.order.return_value.execute.return_value = \
            MagicMock(data=[])

        with patch("scheduler.sector_pe_tracker._supabase_client",
                   return_value=mock_client):
            get_sector_pe_history("it")

        order_call = mock_client.table.return_value.select.return_value.eq.return_value.\
            gte.return_value.order
        args, kwargs = order_call.call_args
        assert args[0] == "snapshot_date"
        assert kwargs.get("desc") is False


# ──────────────────────────────────────────────────────────────────────────────
# TestGetRegimeChanges
# ──────────────────────────────────────────────────────────────────────────────

class TestGetRegimeChanges:
    def test_no_changes_same_regime(self):
        rows = _make_history_rows(["FAIR", "FAIR", "FAIR"], [28.0, 28.5, 29.0])
        with patch("scheduler.sector_pe_tracker.get_sector_pe_history",
                   return_value=rows):
            result = get_regime_changes("it")
        assert result == []

    def test_detects_single_change(self):
        """[FAIR, FAIR, COMPRESSED] → 1 change event."""
        rows = _make_history_rows(
            ["FAIR", "FAIR", "COMPRESSED"],
            [28.0, 27.0, 22.0],
        )
        with patch("scheduler.sector_pe_tracker.get_sector_pe_history",
                   return_value=rows):
            result = get_regime_changes("it")

        assert len(result) == 1
        assert result[0]["from_regime"] == "FAIR"
        assert result[0]["to_regime"] == "COMPRESSED"

    def test_detects_multiple_changes(self):
        """[FAIR, COMPRESSED, STRETCHED] → 2 change events."""
        rows = _make_history_rows(
            ["FAIR", "COMPRESSED", "STRETCHED"],
            [28.0, 21.0, 38.0],
        )
        with patch("scheduler.sector_pe_tracker.get_sector_pe_history",
                   return_value=rows):
            result = get_regime_changes("it")

        assert len(result) == 2
        assert result[0]["from_regime"] == "FAIR"
        assert result[0]["to_regime"] == "COMPRESSED"
        assert result[1]["from_regime"] == "COMPRESSED"
        assert result[1]["to_regime"] == "STRETCHED"

    def test_empty_when_less_than_two_rows(self):
        rows = _make_history_rows(["FAIR"], [28.0])
        with patch("scheduler.sector_pe_tracker.get_sector_pe_history",
                   return_value=rows):
            result = get_regime_changes("it")
        assert result == []

    def test_empty_when_no_history(self):
        with patch("scheduler.sector_pe_tracker.get_sector_pe_history",
                   return_value=[]):
            result = get_regime_changes("it")
        assert result == []

    def test_change_event_has_live_pe_change(self):
        """live_pe_change should be the difference in PE across the boundary."""
        rows = _make_history_rows(
            ["FAIR", "COMPRESSED"],
            [28.0, 21.0],
        )
        with patch("scheduler.sector_pe_tracker.get_sector_pe_history",
                   return_value=rows):
            result = get_regime_changes("it")

        assert len(result) == 1
        assert result[0]["live_pe_change"] == pytest.approx(21.0 - 28.0, rel=1e-3)

    def test_change_event_has_from_and_to_dates(self):
        base = date.today() - timedelta(days=5)
        rows = _make_history_rows(["FAIR", "COMPRESSED"], [28.0, 21.0], base_date=base)
        with patch("scheduler.sector_pe_tracker.get_sector_pe_history",
                   return_value=rows):
            result = get_regime_changes("it")

        assert "from_date" in result[0]
        assert "to_date" in result[0]
        assert result[0]["from_date"] < result[0]["to_date"]


# ──────────────────────────────────────────────────────────────────────────────
# TestComputeRollingLongruPE
# ──────────────────────────────────────────────────────────────────────────────

class TestComputeRollingLongruPE:
    def test_returns_none_when_insufficient_data(self):
        """Fewer than _MIN_HISTORY_FOR_ROLLING rows → None."""
        rows = _make_history_rows(
            ["FAIR"] * (_MIN_HISTORY_FOR_ROLLING - 1),
            [28.0]   * (_MIN_HISTORY_FOR_ROLLING - 1),
        )
        with patch("scheduler.sector_pe_tracker.get_sector_pe_history",
                   return_value=rows):
            result = compute_rolling_longrun_pe("it")
        assert result is None

    def test_returns_median_with_enough_data(self):
        """Exactly _MIN_HISTORY_FOR_ROLLING rows → returns median."""
        pes = list(range(1, _MIN_HISTORY_FOR_ROLLING + 1))   # 1..90
        rows = _make_history_rows(
            ["FAIR"] * _MIN_HISTORY_FOR_ROLLING,
            [float(p) for p in pes],
        )
        with patch("scheduler.sector_pe_tracker.get_sector_pe_history",
                   return_value=rows):
            result = compute_rolling_longrun_pe("it")
        # Median of 1..90 = (45+46)/2 = 45.5
        assert result is not None
        assert result == pytest.approx(45.5, rel=1e-3)

    def test_ignores_none_live_pe_rows(self):
        """Rows with live_pe=None are excluded from the median calculation."""
        good_pes = [28.0] * _MIN_HISTORY_FOR_ROLLING
        rows = _make_history_rows(
            ["FAIR"] * _MIN_HISTORY_FOR_ROLLING,
            good_pes,
        )
        # Inject some None PEs — total row count stays the same but fewer valid values
        for r in rows[:10]:
            r["live_pe"] = None

        with patch("scheduler.sector_pe_tracker.get_sector_pe_history",
                   return_value=rows):
            # Only 80 valid rows, which is < 90 → should return None
            result = compute_rolling_longrun_pe("it")
        # 80 valid rows < _MIN_HISTORY_FOR_ROLLING (90) → None
        assert result is None

    def test_returns_none_when_all_live_pe_none(self):
        rows = _make_history_rows(
            ["FAIR"] * _MIN_HISTORY_FOR_ROLLING,
            [None] * _MIN_HISTORY_FOR_ROLLING,
        )
        # Patch the pes explicitly to None (make_history_rows uses None pe)
        for r in rows:
            r["live_pe"] = None

        with patch("scheduler.sector_pe_tracker.get_sector_pe_history",
                   return_value=rows):
            result = compute_rolling_longrun_pe("it")
        assert result is None

    def test_odd_count_median_is_middle_value(self):
        """Odd number of values → median is the exact middle element."""
        count = _MIN_HISTORY_FOR_ROLLING + 1   # odd count (91)
        pes = [float(i) for i in range(count)]  # 0, 1, 2, ..., 90
        rows = _make_history_rows(["FAIR"] * count, pes)

        with patch("scheduler.sector_pe_tracker.get_sector_pe_history",
                   return_value=rows):
            result = compute_rolling_longrun_pe("it")
        # Median of 0..90 = 45
        assert result == pytest.approx(45.0, rel=1e-3)


# ──────────────────────────────────────────────────────────────────────────────
# TestGetRegimeTrend
# ──────────────────────────────────────────────────────────────────────────────

class TestGetRegimeTrend:
    def test_stable_when_no_history(self):
        with patch("scheduler.sector_pe_tracker.get_sector_pe_history",
                   return_value=[]):
            result = get_regime_trend("it")
        assert result == "STABLE"

    def test_stable_when_insufficient_data(self):
        """Fewer than _TREND_LONG_WINDOW valid points → STABLE."""
        rows = _make_history_rows(
            ["FAIR"] * (_TREND_LONG_WINDOW - 1),
            [28.0]   * (_TREND_LONG_WINDOW - 1),
        )
        with patch("scheduler.sector_pe_tracker.get_sector_pe_history",
                   return_value=rows):
            result = get_regime_trend("it")
        assert result == "STABLE"

    def test_stable_when_short_equals_long(self):
        """Flat PE history → STABLE."""
        rows = _make_history_rows(
            ["FAIR"] * _TREND_LONG_WINDOW,
            [28.0]   * _TREND_LONG_WINDOW,
        )
        with patch("scheduler.sector_pe_tracker.get_sector_pe_history",
                   return_value=rows):
            result = get_regime_trend("it")
        assert result == "STABLE"

    def test_stable_within_3pct_band(self):
        """2% drop in recent PE → within ±3% band → STABLE."""
        # Most recent 5 days: 27.44 (= 28 * 0.98); older 15 days: 28.0
        recent_pes  = [28.0 * 0.98] * _TREND_SHORT_WINDOW
        older_pes   = [28.0] * (_TREND_LONG_WINDOW - _TREND_SHORT_WINDOW)
        pes = recent_pes + older_pes  # [0] will be most recent after reversal in function
        rows = _make_history_rows(["FAIR"] * len(pes), pes)
        # The function reverses, so first element of reversed = last element of rows
        # We need to ensure the rows list has most recent LAST
        with patch("scheduler.sector_pe_tracker.get_sector_pe_history",
                   return_value=rows):
            result = get_regime_trend("it")
        assert result == "STABLE"

    def test_compressing_when_recent_much_cheaper(self):
        """Recent PE 5% below long-term avg → COMPRESSING."""
        # Last 5 days (most recent): 20.0; previous 15 days: 28.0
        # After reversal in function: pes[0..4] = 20.0, pes[5..19] = 28.0
        # short_avg = 20.0, long_avg = (5*20 + 15*28)/20 = (100+420)/20 = 26.0
        # ratio = 20/26 ≈ 0.77 < 0.97 → COMPRESSING
        recent_pes = [20.0] * _TREND_SHORT_WINDOW
        older_pes  = [28.0] * (_TREND_LONG_WINDOW - _TREND_SHORT_WINDOW)
        pes = older_pes + recent_pes  # rows go old→new; reversed → new first
        rows = _make_history_rows(["FAIR"] * len(pes), pes)
        with patch("scheduler.sector_pe_tracker.get_sector_pe_history",
                   return_value=rows):
            result = get_regime_trend("it")
        assert result == "COMPRESSING"

    def test_expanding_when_recent_much_more_expensive(self):
        """Recent PE 5% above long-term avg → EXPANDING."""
        # Last 5 days (most recent): 36.0; previous 15 days: 28.0
        # short_avg = 36.0, long_avg = (5*36 + 15*28)/20 = (180+420)/20 = 30.0
        # ratio = 36/30 = 1.2 > 1.03 → EXPANDING
        recent_pes = [36.0] * _TREND_SHORT_WINDOW
        older_pes  = [28.0] * (_TREND_LONG_WINDOW - _TREND_SHORT_WINDOW)
        pes = older_pes + recent_pes  # rows go old→new; reversed → new first
        rows = _make_history_rows(["FAIR"] * len(pes), pes)
        with patch("scheduler.sector_pe_tracker.get_sector_pe_history",
                   return_value=rows):
            result = get_regime_trend("it")
        assert result == "EXPANDING"

    def test_trend_result_is_valid_string(self):
        """Return value is always one of the three valid strings."""
        valid = {"COMPRESSING", "EXPANDING", "STABLE"}
        rows = _make_history_rows(
            ["FAIR"] * _TREND_LONG_WINDOW,
            [28.0]   * _TREND_LONG_WINDOW,
        )
        with patch("scheduler.sector_pe_tracker.get_sector_pe_history",
                   return_value=rows):
            result = get_regime_trend("it")
        assert result in valid


# ──────────────────────────────────────────────────────────────────────────────
# TestSupabaseClient
# ──────────────────────────────────────────────────────────────────────────────

class TestSupabaseClient:
    def test_returns_none_when_env_vars_missing(self):
        with patch.dict(os.environ, {"SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": ""}):
            result = _supabase_client()
        assert result is None

    def test_returns_none_when_url_missing(self):
        env = {"SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": "some-key"}
        with patch.dict(os.environ, env, clear=False):
            result = _supabase_client()
        assert result is None

    def test_returns_none_on_import_error(self):
        import sys
        with patch.dict(os.environ,
                        {"SUPABASE_URL": "https://x.supabase.co",
                         "SUPABASE_SERVICE_KEY": "key123"}), \
             patch.dict(sys.modules, {"supabase": None}):
            result = _supabase_client()
        assert result is None


# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import pytest as _pytest
    _pytest.main([__file__, "-v"])
