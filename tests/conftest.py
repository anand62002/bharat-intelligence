"""
tests/conftest.py — shared pytest configuration.

Registers custom markers so pytest --co doesn't warn about unknown marks.
Integration tests (marked with @pytest.mark.integration) make real network
calls and are skipped in normal CI runs.  Run them explicitly:

    pytest -m integration -v
    pytest -m integration --tb=short -s    # -s shows print() output
"""

import sys

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


# ─────────────────────────────────────────────────────────────────────────────
# WP-008 (ADR-005): unit tests never build a live Supabase / Anthropic / OpenAI client
# ─────────────────────────────────────────────────────────────────────────────
# CI used to run every test with the production service-role key and Anthropic
# key. A test that forgot to mock a client then read — or, as the P7-I cache
# incident showed, WROTE — production data. This guard replaces the client
# constructors for every test not marked `integration`: the attempt raises, is
# recorded, and the test fails at teardown even if the code under test caught
# the exception and carried on (most of this codebase degrades gracefully, so a
# raise alone would be swallowed and the test would pass silently).
#
# Tests that need a client mock it (patch("supabase.create_client"), the
# module's _supabase helper, or anthropic.Anthropic) — those patches are applied
# after this fixture and take precedence.

_GUARDED = [
    ("supabase", "create_client"),
    ("anthropic", "Anthropic"),
    ("anthropic", "AsyncAnthropic"),
    ("openai", "OpenAI"),
    ("openai", "AsyncOpenAI"),
    ("api.main", "create_client"),   # imported at module level there
]


@pytest.fixture(autouse=True)
def _no_live_clients(request, monkeypatch):
    if request.node.get_closest_marker("integration"):
        yield
        return

    attempts: list[str] = []

    def _caller() -> str:
        """Innermost project frame outside tests/ — where the client was built."""
        import traceback
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        for fr in reversed(traceback.extract_stack()[:-2]):
            p = Path(fr.filename).resolve()
            if root in p.parents and "tests" not in p.relative_to(root).parts[:1]:
                return f"{p.relative_to(root).as_posix()}:{fr.lineno}"
        return "?"

    def _blocker(name: str):
        def _blocked(*args, **kwargs):
            attempts.append(f"{name} at {_caller()}")
            raise RuntimeError(
                f"{name}() blocked in unit tests (WP-008): mock it, or mark the test "
                "@pytest.mark.integration")
        return _blocked

    for mod_name, attr in _GUARDED:
        if mod_name == "api.main" and mod_name not in sys.modules:
            continue          # importing api.main here would build its client
        try:
            module = __import__(mod_name, fromlist=[attr])
        except ImportError:
            continue
        if hasattr(module, attr):
            monkeypatch.setattr(module, attr, _blocker(f"{mod_name}.{attr}"))

    yield

    if attempts:
        pytest.fail(
            "Test tried to build a live client: " + ", ".join(sorted(set(attempts)))
            + ". Mock it or mark the test @pytest.mark.integration (WP-008).",
            pytrace=False,
        )
