"""
tests/test_symbol_alias_resolution.py

Every module that turns an NSE symbol into a yfinance ticker must consult
YF_SYMBOL_MAP. Naively appending ".NS" is wrong for brand-name aliases, and the
failure is silent: yfinance answers "No data found, symbol may be delisted",
the caller turns that into None, and the row is skipped forever.

Found in the Railway worker log for 2026-09-09, where a batch download asked for
['IHCL.NS', 'HITACHIENERGYINDIA.NS', 'BHARATSEAT.NS'] — all three invalid.
"""

import pytest

from agents.outcome_tracker import _resolve_yf_symbol as resolve_outcome
from agents.paper_portfolio import _resolve_yf_symbol as resolve_paper
from data.symbol_map import YF_SYMBOL_MAP

RESOLVERS = [
    pytest.param(resolve_outcome, id="outcome_tracker"),
    pytest.param(resolve_paper,   id="paper_portfolio"),
]

ALIASES = [
    ("IHCL",               "INDHOTEL.NS"),
    ("BHARATSEAT",         "BHARATSE.NS"),
    ("HITACHIENERGYINDIA", "POWERINDIA.NS"),
]


@pytest.mark.parametrize("resolve", RESOLVERS)
@pytest.mark.parametrize("plain,expected", ALIASES)
def test_brand_aliases_resolve(resolve, plain, expected):
    assert resolve(plain) == expected


@pytest.mark.parametrize("resolve", RESOLVERS)
@pytest.mark.parametrize("plain,expected", ALIASES)
def test_already_suffixed_wrong_form_is_corrected(resolve, plain, expected):
    """A stored 'IHCL.NS' must still map to INDHOTEL.NS, not pass through."""
    assert resolve(f"{plain}.NS") == expected


@pytest.mark.parametrize("resolve", RESOLVERS)
def test_ordinary_symbols_unchanged(resolve):
    assert resolve("RELIANCE") == "RELIANCE.NS"
    assert resolve("TCS.NS") == "TCS.NS"


@pytest.mark.parametrize("resolve", RESOLVERS)
def test_index_and_suffixed_symbols_pass_through(resolve):
    assert resolve("^NSEI") == "^NSEI"
    assert resolve("522275.BO") == "522275.BO"


@pytest.mark.parametrize("resolve", RESOLVERS)
def test_every_map_alias_is_honoured(resolve):
    """Guard the whole map, not just the three that happened to fail."""
    for plain, expected in YF_SYMBOL_MAP.items():
        if not isinstance(plain, str) or not isinstance(expected, str):
            continue
        assert resolve(plain) == expected, f"{plain} did not resolve to {expected}"
