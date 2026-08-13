"""tests/test_run_cache.py — P7-H intra-run memoisation."""

import threading
import time

import pytest

from data import run_cache
from data.run_cache import memoise_run


@pytest.fixture(autouse=True)
def _clean_cache():
    run_cache.clear()
    yield
    run_cache.clear()


class TestBasicMemoisation:
    def test_second_call_does_not_re_execute(self):
        calls = []

        @memoise_run()
        def fetch(symbol):
            calls.append(symbol)
            return {"pe": 24.1}

        assert fetch("TCS") == {"pe": 24.1}
        assert fetch("TCS") == {"pe": 24.1}
        assert calls == ["TCS"]

    def test_distinct_arguments_are_separate_entries(self):
        calls = []

        @memoise_run()
        def fetch(symbol):
            calls.append(symbol)
            return symbol.lower()

        fetch("TCS"); fetch("INFY"); fetch("TCS")
        assert calls == ["TCS", "INFY"]

    def test_none_is_cached(self):
        """A source that just failed will fail again — do not retry 9x per symbol."""
        calls = []

        @memoise_run()
        def fetch(symbol):
            calls.append(symbol)
            return None

        assert fetch("TCS") is None
        assert fetch("TCS") is None
        assert len(calls) == 1

    def test_exceptions_are_not_cached(self):
        calls = []

        @memoise_run()
        def fetch(symbol):
            calls.append(symbol)
            raise RuntimeError("boom")

        for _ in range(2):
            with pytest.raises(RuntimeError):
                fetch("TCS")
        assert len(calls) == 2, "a raising call must be retried, not cached"

    def test_kwargs_participate_in_the_key(self):
        calls = []

        @memoise_run()
        def fetch(symbol, deep=False):
            calls.append((symbol, deep))
            return deep

        fetch("TCS"); fetch("TCS", deep=True); fetch("TCS", deep=True)
        assert calls == [("TCS", False), ("TCS", True)]


class TestMutationSafety:
    def test_caller_mutation_does_not_poison_the_entry(self):
        @memoise_run()
        def fetch(symbol):
            return {"pe": 24.1, "history": [1, 2, 3]}

        first = fetch("TCS")
        first["pe"] = 999
        first["history"].append(4)

        second = fetch("TCS")
        assert second["pe"] == 24.1
        assert second["history"] == [1, 2, 3], "nested structures must be isolated too"


class TestConcurrencyCollapse:
    def test_concurrent_identical_calls_fetch_once(self):
        """
        The core case: fundamental, warren_bot and mgmt_quality run in one
        asyncio.gather, each in its own thread, all calling get_screener_data
        for the same symbol at the same instant. Without per-key locking every
        thread misses and every thread fetches.
        """
        calls = []
        started = threading.Barrier(5)

        @memoise_run()
        def slow_fetch(symbol):
            calls.append(symbol)
            time.sleep(0.15)          # simulate the network round-trip
            return {"pe": 24.1}

        results = []

        def worker():
            started.wait()            # maximise the race
            results.append(slow_fetch("TCS"))

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads: t.start()
        for t in threads: t.join()

        assert len(calls) == 1, f"expected a single fetch, got {len(calls)}"
        assert len(results) == 5
        assert all(r == {"pe": 24.1} for r in results)

        s = run_cache.stats()
        assert s["misses"] == 1
        assert s["collapsed"] + s["hits"] == 4

    def test_different_keys_do_not_serialise(self):
        """Per-key locks — unrelated symbols must fetch in parallel."""
        @memoise_run()
        def slow_fetch(symbol):
            time.sleep(0.2)
            return symbol

        threads = [threading.Thread(target=slow_fetch, args=(f"SYM{i}",)) for i in range(5)]
        t0 = time.monotonic()
        for t in threads: t.start()
        for t in threads: t.join()
        elapsed = time.monotonic() - t0

        # Serialised would be ~1.0s; parallel ~0.2s. Generous bound for CI.
        assert elapsed < 0.7, f"distinct keys serialised ({elapsed:.2f}s)"


class TestTTL:
    def test_entry_expires(self):
        calls = []

        @memoise_run(ttl=0.05)
        def fetch(symbol):
            calls.append(symbol)
            return 1

        fetch("TCS"); fetch("TCS")
        assert len(calls) == 1
        time.sleep(0.08)
        fetch("TCS")
        assert len(calls) == 2


class TestStats:
    def test_counts_and_saved_pct(self):
        @memoise_run()
        def fetch(symbol):
            return 1

        fetch("A"); fetch("A"); fetch("A"); fetch("B")

        s = run_cache.stats()
        assert s["misses"] == 2            # A, B fetched
        assert s["hits"] == 2              # A served twice from cache
        assert s["attempted_calls"] == 4
        assert s["saved_calls"] == 2
        assert s["saved_pct"] == 50.0
        assert s["entries"] == 2

    def test_stats_empty_when_unused(self):
        s = run_cache.stats()
        assert s["attempted_calls"] == 0
        assert s["saved_pct"] == 0.0

    def test_clear_resets_everything(self):
        @memoise_run()
        def fetch(symbol):
            return 1

        fetch("A"); fetch("A")
        run_cache.clear()

        s = run_cache.stats()
        assert s["entries"] == 0 and s["hits"] == 0 and s["misses"] == 0


class TestScope:
    def test_context_manager_clears_on_entry(self):
        calls = []

        @memoise_run()
        def fetch(symbol):
            calls.append(symbol)
            return 1

        with run_cache.scope("run-1"):
            fetch("TCS"); fetch("TCS")
        assert len(calls) == 1

        with run_cache.scope("run-2"):
            fetch("TCS")               # fresh scope — must refetch
        assert len(calls) == 2

    def test_decorator_form(self):
        calls = []

        @memoise_run()
        def fetch(symbol):
            calls.append(symbol)
            return 1

        @run_cache.scoped("job")
        def job():
            fetch("TCS"); fetch("TCS")
            return "done"

        assert job() == "done"
        assert job() == "done"
        assert len(calls) == 2, "each scoped run starts with a cold cache"

    def test_scope_logs_but_does_not_swallow_exceptions(self):
        with pytest.raises(ValueError):
            with run_cache.scope("boom"):
                raise ValueError("propagate me")


class TestIntegration:
    def test_screener_fetchers_are_memoised(self):
        """The two functions P7-H targets must actually carry the decorator."""
        from data.fetchers import get_screener_data, get_screener_history

        for fn in (get_screener_data, get_screener_history):
            assert hasattr(fn, "_run_cache_label"), f"{fn.__name__} is not memoised"
            assert hasattr(fn, "_run_cache_uncached")

    def test_realistic_per_symbol_saving(self):
        """
        Model one symbol's real access pattern and assert the remote-call count
        collapses from 9 to 2.
        """
        fetches = []

        @memoise_run()
        def get_data(symbol):
            fetches.append(("data", symbol))
            return {"pe": 24.1}

        @memoise_run()
        def get_history(symbol):
            fetches.append(("history", symbol))
            return {"revenue": [1, 2, 3]}

        sym = "TCS"
        get_data(sym)                                   # fundamental
        get_data(sym); get_history(sym)                 # warren_bot
        get_data(sym); get_history(sym)                 # mgmt_quality
        get_data(sym); get_history(sym)                 # governance_screener
        get_history(sym)                                # insider via sentiment
        get_history(sym)                                # insider via institutional

        assert len(fetches) == 2, f"expected 2 remote calls, got {fetches}"
        s = run_cache.stats()
        assert s["attempted_calls"] == 9
        assert s["saved_calls"] == 7


class TestMemoryBounds:
    """
    The API process never hits a run boundary, so the cache must bound itself.
    """

    def test_expired_entries_are_swept(self, monkeypatch):
        monkeypatch.setattr(run_cache, "_SWEEP_THRESHOLD", 10)
        monkeypatch.setattr(run_cache, "DEFAULT_TTL_S", 0.05)

        @memoise_run(ttl=0.05)
        def fetch(symbol):
            return symbol

        for i in range(8):
            fetch(f"OLD{i}")
        time.sleep(0.08)                 # everything above is now expired
        for i in range(6):               # crossing the sweep threshold
            fetch(f"NEW{i}")

        assert run_cache.stats()["entries"] <= 10

    def test_hard_ceiling_evicts_oldest(self, monkeypatch):
        monkeypatch.setattr(run_cache, "_SWEEP_THRESHOLD", 20)
        monkeypatch.setattr(run_cache, "_MAX_ENTRIES", 15)

        @memoise_run()
        def fetch(symbol):
            return symbol

        for i in range(40):
            fetch(f"SYM{i}")

        entries = run_cache.stats()["entries"]
        assert entries <= 20, f"cache grew unbounded: {entries} entries"

    def test_key_locks_do_not_leak(self, monkeypatch):
        """Evicted entries must drop their per-key locks too."""
        monkeypatch.setattr(run_cache, "_SWEEP_THRESHOLD", 20)
        monkeypatch.setattr(run_cache, "_MAX_ENTRIES", 15)

        @memoise_run()
        def fetch(symbol):
            return symbol

        for i in range(60):
            fetch(f"SYM{i}")

        assert len(run_cache._KEY_LOCKS) <= 25


class TestNestedScopes:
    def test_inner_scope_does_not_clear_outer(self):
        """
        A scoped entry point called from inside another scoped run must not
        wipe the outer run's cache mid-flight.
        """
        calls = []

        @memoise_run()
        def fetch(symbol):
            calls.append(symbol)
            return 1

        with run_cache.scope("outer"):
            fetch("TCS")
            with run_cache.scope("inner"):
                fetch("TCS")          # still the outer scope's entry
            fetch("TCS")

        assert len(calls) == 1, f"inner scope cleared the outer cache: {calls}"

    def test_sequential_scopes_still_start_cold(self):
        calls = []

        @memoise_run()
        def fetch(symbol):
            calls.append(symbol)
            return 1

        with run_cache.scope("a"):
            fetch("TCS")
        with run_cache.scope("b"):
            fetch("TCS")

        assert len(calls) == 2

    def test_depth_unwinds_on_exception(self):
        @memoise_run()
        def fetch(symbol):
            return 1

        try:
            with run_cache.scope("outer"):
                with run_cache.scope("inner"):
                    raise ValueError("boom")
        except ValueError:
            pass

        # Next top-level scope must behave as outermost again (i.e. clear).
        calls = []

        @memoise_run()
        def fetch2(symbol):
            calls.append(symbol)
            return 1

        with run_cache.scope("next"):
            fetch2("A")
        with run_cache.scope("next2"):
            fetch2("A")
        assert len(calls) == 2, "scope depth leaked after an exception"
