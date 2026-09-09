"""
tests/conftest.py — shared pytest configuration.

Registers custom markers so pytest --co doesn't warn about unknown marks.
Integration tests (marked with @pytest.mark.integration) make real network
calls and are skipped in normal CI runs.  Run them explicitly:

    pytest -m integration -v
    pytest -m integration --tb=short -s    # -s shows print() output
"""

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: marks tests that make real network/API calls "
        "(skipped unless -m integration is passed explicitly)",
    )


@pytest.fixture(autouse=True)
def _reset_run_cache(monkeypatch):
    """
    Isolate both fundamentals cache layers for every test.

    P7-H (in-process memo): `get_screener_data` / `get_screener_history` are
    memoised per run. Tests routinely call them with the same symbol but
    different mocked HTML, so without clearing, the second test in a class is
    served the first test's result and fails for reasons unrelated to its
    subject.

    P7-I (persistent, Supabase): disabled outright during tests. Left on, every
    fetcher-touching test makes live database round-trips — which slowed the
    suite badly — and, worse, tests that call get_screener_data("TEST") with
    mocked HTML WRITE that fixture into the production cache table. A `TEST`
    row was found there on 2026-09-09 and removed.

    Tests that want to exercise the persistent cache re-enable it explicitly;
    tests/test_fundamentals_cache.py drives it through mocks instead.
    """
    from data import run_cache, fundamentals_cache

    monkeypatch.setattr(fundamentals_cache, "_ENABLED", False, raising=False)
    run_cache.clear()
    yield
    run_cache.clear()
