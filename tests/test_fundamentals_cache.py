"""tests/test_fundamentals_cache.py — P7-I persistent fundamentals cache."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from data import fundamentals_cache as fc
from data.fundamentals_cache import (
    payload_quality,
    payload_source,
    persist_cache,
    is_fresh,
    write_entry,
)


def _ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


SCREENER = {"pe": 24.1, "roce": 21.3}                       # no data_source -> screener
TRENDLYNE = {"pe": 24.0, "data_source": "trendlyne_fallback"}
YFIN = {"pe": 23.9, "data_source": "yfinance_fallback"}


@pytest.fixture(autouse=True)
def _reset():
    fc.reset_stats()
    yield
    fc.reset_stats()


class TestQualityRanking:
    def test_absent_marker_means_screener(self):
        assert payload_quality(SCREENER) == 3
        assert payload_source(SCREENER) == "screener_in"

    def test_fallbacks_rank_lower(self):
        assert payload_quality(TRENDLYNE) == 2
        assert payload_quality(YFIN) == 1

    def test_empty_is_unusable(self):
        for bad in (None, {}, "x", 5):
            assert payload_quality(bad) == 0


class TestFreshness:
    def test_within_ttl_is_fresh(self):
        assert is_fresh({"fetched_at": _ago(3)}, "snapshot")

    def test_beyond_ttl_is_stale(self):
        assert not is_fresh({"fetched_at": _ago(10)}, "snapshot")

    def test_history_has_a_longer_ttl_than_snapshot(self):
        entry = {"fetched_at": _ago(10)}
        assert is_fresh(entry, "history")
        assert not is_fresh(entry, "snapshot")

    def test_missing_timestamp_is_stale(self):
        assert not is_fresh({}, "snapshot")

    def test_earnings_since_fetch_invalidates(self):
        client = MagicMock()
        (client.table.return_value.select.return_value.eq.return_value
               .gte.return_value.lte.return_value.limit.return_value
               .execute.return_value.data) = [{"earnings_date": "2026-09-01"}]
        assert not is_fresh({"fetched_at": _ago(3)}, "snapshot", "TCS", client)

    def test_no_earnings_keeps_it_fresh(self):
        client = MagicMock()
        (client.table.return_value.select.return_value.eq.return_value
               .gte.return_value.lte.return_value.limit.return_value
               .execute.return_value.data) = []
        assert is_fresh({"fetched_at": _ago(3)}, "snapshot", "TCS", client)


class TestDowngradeProtection:
    """A 403 storm must not be able to degrade a good cache."""

    def test_yfinance_does_not_overwrite_screener(self):
        existing = {"quality": 3, "fetched_at": _ago(2)}
        with patch.object(fc, "_client", return_value=MagicMock()):
            assert write_entry("TCS", "snapshot", YFIN, existing) is False
        assert fc.stats()["skipped_writes"] == 1

    def test_screener_overwrites_yfinance(self):
        existing = {"quality": 1, "fetched_at": _ago(2)}
        with patch.object(fc, "_client", return_value=MagicMock()):
            assert write_entry("TCS", "snapshot", SCREENER, existing) is True

    def test_very_old_cache_may_be_downgraded(self):
        existing = {"quality": 3, "fetched_at": _ago(fc.STALE_FLOOR_DAYS + 5)}
        with patch.object(fc, "_client", return_value=MagicMock()):
            assert write_entry("TCS", "snapshot", YFIN, existing) is True

    def test_unusable_payload_never_written(self):
        with patch.object(fc, "_client", return_value=MagicMock()):
            assert write_entry("TCS", "snapshot", None) is False


class TestDecorator:
    def _wrap(self, live_result, entry=None, client=None):
        calls = []

        @persist_cache(kind="snapshot")
        def fetch(symbol):
            calls.append(symbol)
            return live_result

        cl = client if client is not None else MagicMock()
        # earnings_calendar lookup returns nothing by default
        (cl.table.return_value.select.return_value.eq.return_value
           .gte.return_value.lte.return_value.limit.return_value
           .execute.return_value.data) = []
        with patch.object(fc, "_client", return_value=cl), \
             patch.object(fc, "read_entry", return_value=entry), \
             patch.object(fc, "write_entry", return_value=True) as w:
            out = fetch("TCS")
        return out, calls, w

    def test_fresh_cache_short_circuits_the_fetch(self):
        entry = {"payload": SCREENER, "quality": 3, "fetched_at": _ago(2)}
        out, calls, _ = self._wrap(SCREENER, entry)
        assert calls == [], "a fresh cache entry must not hit the network"
        assert out["served_from_cache"] is True
        assert out["cache_age_days"] == pytest.approx(2, abs=0.1)

    def test_stale_cache_triggers_live_fetch(self):
        entry = {"payload": SCREENER, "quality": 3, "fetched_at": _ago(30)}
        out, calls, w = self._wrap(SCREENER, entry)
        assert calls == ["TCS"]
        w.assert_called_once()

    def test_no_cache_fetches_and_writes(self):
        out, calls, w = self._wrap(SCREENER, None)
        assert calls == ["TCS"] and out == SCREENER
        w.assert_called_once()

    def test_outage_serves_cached_rather_than_degraded_live(self):
        """
        The core P7-I behaviour: screener 403s, the chain degrades to yfinance,
        and we return the older-but-real screener payload instead.
        """
        entry = {"payload": SCREENER, "quality": 3, "fetched_at": _ago(30)}
        out, calls, w = self._wrap(YFIN, entry)
        assert calls == ["TCS"], "it should still attempt a live fetch"
        assert out["served_from_cache"] is True
        assert out["cache_reason"] == "live fetch degraded"
        assert out["pe"] == SCREENER["pe"], "must return the cached screener values"
        w.assert_not_called()
        assert fc.stats()["stale_served"] == 1

    def test_degraded_live_used_when_cache_is_ancient(self):
        entry = {"payload": SCREENER, "quality": 3,
                 "fetched_at": _ago(fc.STALE_FLOOR_DAYS + 10)}
        out, calls, _ = self._wrap(YFIN, entry)
        assert out == YFIN, "beyond the stale floor, live data wins"

    def test_equal_quality_live_result_is_kept(self):
        entry = {"payload": SCREENER, "quality": 3, "fetched_at": _ago(30)}
        fresh = {"pe": 99.0}
        out, _, w = self._wrap(fresh, entry)
        assert out == fresh
        w.assert_called_once()

    def test_disabled_flag_bypasses_everything(self, monkeypatch):
        monkeypatch.setattr(fc, "_ENABLED", False)
        calls = []

        @persist_cache(kind="snapshot")
        def fetch(symbol):
            calls.append(symbol)
            return SCREENER

        assert fetch("TCS") == SCREENER
        assert calls == ["TCS"]

    def test_cache_failure_degrades_to_live_fetch(self):
        calls = []

        @persist_cache(kind="snapshot")
        def fetch(symbol):
            calls.append(symbol)
            return SCREENER

        with patch.object(fc, "_client", side_effect=RuntimeError("db down")):
            try:
                out = fetch("TCS")
            except Exception:
                pytest.fail("cache failure must never propagate")
        assert calls == ["TCS"]


class TestWiring:
    def test_fetchers_are_wrapped_in_both_layers(self):
        import inspect
        import data.fetchers as F

        src = inspect.getsource(F)
        for fn_name, kind in (("get_screener_data", "snapshot"),
                              ("get_screener_history", "history")):
            block = (f'@memoise_run(key_fn=symbol_key)\n'
                     f'@persist_cache(kind="{kind}")\n'
                     f'def {fn_name}(')
            assert block in src, f"{fn_name} is not wrapped in memo + persistent cache"

    def test_memo_is_outermost(self):
        """
        Order matters: the in-process memo must sit OUTSIDE the persistent
        cache, so a repeat call within one run costs neither a network request
        nor a database round-trip.
        """
        from data.fetchers import get_screener_data
        assert hasattr(get_screener_data, "_run_cache_label")


class TestStats:
    def test_counts_and_hit_rate(self):
        fc._STATS.update({"hits": 3, "misses": 1})
        s = fc.stats()
        assert s["lookups"] == 4 and s["hit_rate_pct"] == 75.0

    def test_empty_stats_safe(self):
        assert fc.stats()["hit_rate_pct"] == 0.0
