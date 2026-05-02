"""
tests/test_forward_estimates.py
================================
Unit tests for data/forward_estimates.py

Tests validate:
  - yfinance fetch + field derivation
  - PEG / forward PE derivation when missing
  - interpret_estimates() signal logic
  - Supabase cache read/write (mocked)
  - TTL expiry
  - Graceful failure
  - Symbol normalisation
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from data.forward_estimates import (
    get_forward_estimates,
    interpret_estimates,
    _plain,
    _to_yf,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_info(
    forward_eps: float = 100.0,
    forward_pe:  float = 20.0,
    peg_ratio:   float = 1.5,
    earnings_growth: float = 0.15,
    current_price:   float = 2000.0,
    n_analysts:      int   = 10,
) -> dict:
    return {
        "forwardEps":                 forward_eps,
        "forwardPE":                  forward_pe,
        "pegRatio":                   peg_ratio,
        "earningsGrowth":             earnings_growth,
        "currentPrice":               current_price,
        "numberOfAnalystOpinions":    n_analysts,
        "revenuePerShare":            None,
    }


def _mock_ticker(info: dict):
    t = MagicMock()
    t.info = info
    return t


# ──────────────────────────────────────────────────────────────────────────────
# Symbol helpers
# ──────────────────────────────────────────────────────────────────────────────

class TestSymbolHelpers:
    def test_plain_strips_ns(self):
        assert _plain("RELIANCE.NS") == "RELIANCE"

    def test_plain_strips_bo(self):
        assert _plain("INFY.BO") == "INFY"

    def test_plain_uppercases(self):
        assert _plain("tcs") == "TCS"

    def test_to_yf_appends_ns(self):
        assert _to_yf("RELIANCE") == "RELIANCE.NS"
        assert _to_yf("INFY.NS")  == "INFY.NS"


# ──────────────────────────────────────────────────────────────────────────────
# yfinance fetch
# ──────────────────────────────────────────────────────────────────────────────

class TestYFinanceFetch:
    def _run(self, info: dict, symbol: str = "RELIANCE") -> dict:
        with patch.dict("os.environ", {"SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": ""}):
            with patch("yfinance.Ticker", return_value=_mock_ticker(info)):
                return get_forward_estimates(symbol, force_refresh=True)

    def test_basic_fields_populated(self):
        info = _make_info()
        r = self._run(info)
        assert r["symbol"] == "RELIANCE"
        assert r["eps_current_yr"] == pytest.approx(100.0)
        assert r["forward_pe"]    == pytest.approx(20.0)
        assert r["peg_ratio"]     == pytest.approx(1.5)
        assert r["current_price"] == pytest.approx(2000.0)
        assert r["analyst_count"] == 10

    def test_eps_growth_pct_converted(self):
        info = _make_info(earnings_growth=0.20)
        r = self._run(info)
        assert r["eps_growth_pct"] == pytest.approx(20.0)

    def test_next_yr_eps_derived(self):
        info = _make_info(forward_eps=100.0, earnings_growth=0.10)
        r = self._run(info)
        assert r["eps_next_yr"] == pytest.approx(110.0)

    def test_forward_pe_derived_when_missing(self):
        """When forwardPE is absent but price and EPS are available, derive it."""
        info = _make_info(forward_pe=None, forward_eps=100.0, current_price=2000.0)
        r = self._run(info)
        assert r["forward_pe"] == pytest.approx(20.0)

    def test_peg_derived_when_missing(self):
        """PEG = forward_PE / eps_growth_pct when not provided by yfinance."""
        info = _make_info(peg_ratio=None, forward_pe=20.0, earnings_growth=0.20)
        r = self._run(info)
        assert r["peg_ratio"] == pytest.approx(20.0 / 20.0)

    def test_source_is_yfinance(self):
        r = self._run(_make_info())
        assert r["source"] == "yfinance"

    def test_error_is_none_on_success(self):
        r = self._run(_make_info())
        assert r["error"] is None


# ──────────────────────────────────────────────────────────────────────────────
# interpret_estimates
# ──────────────────────────────────────────────────────────────────────────────

class TestInterpretEstimates:
    def _est(self, forward_pe=None, peg=None, eps_growth=None):
        return {
            "forward_pe":     forward_pe,
            "peg_ratio":      peg,
            "eps_growth_pct": eps_growth,
        }

    def test_cheap_forward_pe(self):
        r = interpret_estimates(self._est(forward_pe=8.0))
        assert r["valuation_signal"] in ("CHEAP", "UNDERVALUED", "FAIR")

    def test_expensive_forward_pe(self):
        r = interpret_estimates(self._est(forward_pe=60.0))
        assert r["valuation_signal"] == "EXPENSIVE"

    def test_undervalued_peg(self):
        r = interpret_estimates(self._est(peg=0.4))
        assert r["valuation_signal"] == "UNDERVALUED"

    def test_expensive_peg(self):
        r = interpret_estimates(self._est(peg=3.0))
        assert r["valuation_signal"] == "EXPENSIVE"

    def test_peg_takes_precedence_over_pe(self):
        """When both PE and PEG available, PEG determines the signal."""
        r = interpret_estimates(self._est(forward_pe=50.0, peg=0.4))
        assert r["valuation_signal"] == "UNDERVALUED"

    def test_unknown_when_no_data(self):
        r = interpret_estimates(self._est())
        assert r["valuation_signal"] == "UNKNOWN"

    def test_summary_includes_growth(self):
        r = interpret_estimates(self._est(forward_pe=20.0, eps_growth=18.0))
        assert "18.0% EPS growth" in r["summary"]

    def test_all_keys_present(self):
        r = interpret_estimates(self._est(forward_pe=25.0, peg=1.2, eps_growth=15.0))
        assert {"valuation_signal", "forward_pe_comment", "peg_comment", "summary"} <= set(r.keys())


# ──────────────────────────────────────────────────────────────────────────────
# Supabase cache path (mocked)
# ──────────────────────────────────────────────────────────────────────────────

def _make_cache_row(symbol: str = "RELIANCE", hours_ago: float = 1.0) -> dict:
    cached_at = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return {
        "symbol":          symbol,
        "eps_current_yr":  80.0,
        "eps_next_yr":     95.0,
        "rev_current_yr":  None,
        "rev_next_yr":     None,
        "eps_growth_pct":  18.75,
        "forward_pe":      25.0,
        "peg_ratio":       1.33,
        "current_price":   2000.0,
        "analyst_count":   8,
        "cached_at":       cached_at,
    }


def _mock_supabase_with_row(row: dict):
    mock_execute = MagicMock()
    mock_execute.data = [row]
    chain = MagicMock()
    chain.execute.return_value = mock_execute
    chain.limit.return_value = chain
    chain.eq.return_value = chain
    chain.select.return_value = chain
    client = MagicMock()
    client.table.return_value = chain
    return client


class TestSupabaseCache:
    def test_cache_hit_within_ttl(self):
        row = _make_cache_row(hours_ago=2.0)
        with patch.dict("os.environ", {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
            with patch("supabase.create_client", return_value=_mock_supabase_with_row(row)):
                r = get_forward_estimates("RELIANCE")
        assert r["source"] == "cache"
        assert r["forward_pe"] == pytest.approx(25.0)

    def test_cache_miss_when_expired(self):
        """Cache row older than 24h → should re-fetch via yfinance."""
        row = _make_cache_row(hours_ago=25.0)  # expired
        with patch.dict("os.environ", {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
            with patch("supabase.create_client", return_value=_mock_supabase_with_row(row)):
                with patch("yfinance.Ticker", return_value=_mock_ticker(_make_info())):
                    r = get_forward_estimates("RELIANCE")
        assert r["source"] == "yfinance"

    def test_force_refresh_skips_cache(self):
        row = _make_cache_row(hours_ago=1.0)
        with patch.dict("os.environ", {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
            with patch("supabase.create_client", return_value=_mock_supabase_with_row(row)):
                with patch("yfinance.Ticker", return_value=_mock_ticker(_make_info())):
                    r = get_forward_estimates("RELIANCE", force_refresh=True)
        assert r["source"] == "yfinance"

    def test_empty_cache_falls_through_to_yfinance(self):
        mock_execute = MagicMock()
        mock_execute.data = []
        chain = MagicMock()
        chain.execute.return_value = mock_execute
        chain.limit.return_value = chain
        chain.eq.return_value = chain
        chain.select.return_value = chain
        client = MagicMock()
        client.table.return_value = chain
        with patch.dict("os.environ", {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
            with patch("supabase.create_client", return_value=client):
                with patch("yfinance.Ticker", return_value=_mock_ticker(_make_info())):
                    r = get_forward_estimates("TCS")
        assert r["source"] == "yfinance"


# ──────────────────────────────────────────────────────────────────────────────
# Error handling
# ──────────────────────────────────────────────────────────────────────────────

class TestErrorHandling:
    def test_yfinance_exception_returns_error_dict(self):
        with patch.dict("os.environ", {"SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": ""}):
            with patch("yfinance.Ticker", side_effect=RuntimeError("network err")):
                r = get_forward_estimates("CRASH")
        assert r["source"] == "error"
        assert r["error"] is not None
        assert r["forward_pe"] is None

    def test_never_raises(self):
        with patch.dict("os.environ", {"SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": ""}):
            with patch("yfinance.Ticker", side_effect=Exception("boom")):
                try:
                    r = get_forward_estimates("ANY")
                except Exception as exc:
                    pytest.fail(f"get_forward_estimates raised: {exc}")
        assert r is not None

    def test_all_keys_present_on_error(self):
        expected_keys = {
            "symbol", "eps_current_yr", "eps_next_yr", "rev_current_yr",
            "rev_next_yr", "eps_growth_pct", "forward_pe", "peg_ratio",
            "current_price", "analyst_count", "cached_at", "source", "error",
        }
        with patch.dict("os.environ", {"SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": ""}):
            with patch("yfinance.Ticker", side_effect=Exception("boom")):
                r = get_forward_estimates("FAIL")
        assert expected_keys <= set(r.keys())
