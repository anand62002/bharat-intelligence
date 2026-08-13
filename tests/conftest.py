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
def _reset_run_cache():
    """
    Clear the P7-H fundamentals memo cache between tests.

    `get_screener_data` / `get_screener_history` are memoised per run
    (data/run_cache.py). Tests routinely call them with the same symbol but
    different mocked HTML, so without this the second test in a class is served
    the first test's cached result and fails for reasons unrelated to its
    subject. Production is unaffected — nothing there re-fetches one symbol
    expecting different data inside the TTL — but tests must stay isolated.
    """
    from data import run_cache
    run_cache.clear()
    yield
    run_cache.clear()
