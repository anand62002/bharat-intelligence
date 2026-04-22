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
