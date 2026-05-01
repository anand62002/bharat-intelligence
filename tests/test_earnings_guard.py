"""
tests/test_earnings_guard.py
============================
Unit tests for agents/earnings_guard.py

Tests validate:
  - CRITICAL / WARNING / CLEAR classification
  - Symbol normalisation (.NS / .BO stripping)
  - Supabase lookup path (mocked)
  - yfinance live probe fallback (mocked)
  - Graceful failure (exception → CLEAR)
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.earnings_guard import check_pre_earnings


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_supabase_row(symbol: str, days_from_today: int, quarter: str = "Q1FY26") -> dict:
    """Build a fake Supabase earnings_calendar row."""
    edate = date.today() + timedelta(days=days_from_today)
    return {"symbol": symbol, "earnings_date": str(edate), "quarter": quarter, "source": "estimated", "confirmed": False}


def _mock_supabase(rows: list[dict]):
    """Return a mock Supabase client whose earnings_calendar query returns `rows`."""
    mock_execute = MagicMock()
    mock_execute.data = rows

    mock_chain = MagicMock()
    mock_chain.execute.return_value = mock_execute
    mock_chain.limit.return_value = mock_chain
    mock_chain.order.return_value = mock_chain
    mock_chain.lte.return_value = mock_chain
    mock_chain.gte.return_value = mock_chain
    mock_chain.eq.return_value = mock_chain
    mock_chain.select.return_value = mock_chain

    mock_table = MagicMock()
    mock_table.table.return_value = mock_chain

    return mock_table


# ──────────────────────────────────────────────────────────────────────────────
# Symbol normalisation
# ──────────────────────────────────────────────────────────────────────────────

class TestSymbolNormalisation:
    def test_strips_ns_suffix(self):
        with patch.dict("os.environ", {"SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": ""}):
            with patch("data.earnings_fetcher._yfinance_earnings_date", return_value=None):
                result = check_pre_earnings("RELIANCE.NS", days_window=7)
        assert result["symbol"] == "RELIANCE"

    def test_strips_bo_suffix(self):
        with patch.dict("os.environ", {"SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": ""}):
            with patch("data.earnings_fetcher._yfinance_earnings_date", return_value=None):
                result = check_pre_earnings("INFY.BO", days_window=7)
        assert result["symbol"] == "INFY"

    def test_uppercase_plain(self):
        with patch.dict("os.environ", {"SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": ""}):
            with patch("data.earnings_fetcher._yfinance_earnings_date", return_value=None):
                result = check_pre_earnings("tcs", days_window=7)
        assert result["symbol"] == "TCS"


# ──────────────────────────────────────────────────────────────────────────────
# CLEAR path — no upcoming earnings
# ──────────────────────────────────────────────────────────────────────────────

class TestClearPath:
    def test_no_supabase_no_yfinance_returns_clear(self):
        with patch.dict("os.environ", {"SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": ""}):
            with patch("data.earnings_fetcher._yfinance_earnings_date", return_value=None):
                result = check_pre_earnings("WIPRO", days_window=7)
        assert result["has_upcoming_earnings"] is False
        assert result["warning_level"] == "CLEAR"
        assert result["earnings_date"] is None
        assert result["days_until"] is None

    def test_supabase_no_rows_returns_clear(self):
        with patch.dict("os.environ", {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
            with patch("supabase.create_client", return_value=_mock_supabase([])):
                with patch("data.earnings_fetcher._yfinance_earnings_date", return_value=None):
                    result = check_pre_earnings("WIPRO", days_window=7)
        assert result["warning_level"] == "CLEAR"

    def test_earnings_beyond_window_returns_clear(self):
        """Earnings 20 days away with window=7 → CLEAR."""
        rows = [_make_supabase_row("WIPRO", 20)]
        with patch.dict("os.environ", {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
            with patch("supabase.create_client", return_value=_mock_supabase(rows)):
                result = check_pre_earnings("WIPRO", days_window=7)
        assert result["warning_level"] == "CLEAR"
        assert result["has_upcoming_earnings"] is False


# ──────────────────────────────────────────────────────────────────────────────
# WARNING path — 4–7 days away
# ──────────────────────────────────────────────────────────────────────────────

class TestWarningPath:
    @pytest.mark.parametrize("days", [4, 5, 6, 7])
    def test_warning_level_for_4_to_7_days(self, days):
        rows = [_make_supabase_row("HDFCBANK", days)]
        with patch.dict("os.environ", {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
            with patch("supabase.create_client", return_value=_mock_supabase(rows)):
                result = check_pre_earnings("HDFCBANK", days_window=7)
        assert result["warning_level"] == "WARNING"
        assert result["has_upcoming_earnings"] is True
        assert result["days_until"] == days

    def test_warning_returns_quarter(self):
        rows = [_make_supabase_row("HDFCBANK", 5, quarter="Q2FY26")]
        with patch.dict("os.environ", {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
            with patch("supabase.create_client", return_value=_mock_supabase(rows)):
                result = check_pre_earnings("HDFCBANK", days_window=7)
        assert result["quarter"] == "Q2FY26"


# ──────────────────────────────────────────────────────────────────────────────
# CRITICAL path — ≤3 days away
# ──────────────────────────────────────────────────────────────────────────────

class TestCriticalPath:
    @pytest.mark.parametrize("days", [0, 1, 2, 3])
    def test_critical_level_for_0_to_3_days(self, days):
        rows = [_make_supabase_row("RELIANCE", days)]
        with patch.dict("os.environ", {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
            with patch("supabase.create_client", return_value=_mock_supabase(rows)):
                result = check_pre_earnings("RELIANCE", days_window=7)
        assert result["warning_level"] == "CRITICAL"
        assert result["has_upcoming_earnings"] is True
        assert result["days_until"] == days

    def test_critical_returns_earnings_date_string(self):
        rows = [_make_supabase_row("RELIANCE", 2)]
        with patch.dict("os.environ", {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
            with patch("supabase.create_client", return_value=_mock_supabase(rows)):
                result = check_pre_earnings("RELIANCE", days_window=7)
        expected = str(date.today() + timedelta(days=2))
        assert result["earnings_date"] == expected


# ──────────────────────────────────────────────────────────────────────────────
# yfinance fallback path
# ──────────────────────────────────────────────────────────────────────────────

class TestYFinanceFallback:
    def test_yfinance_fallback_warning(self):
        yf_date = date.today() + timedelta(days=5)
        with patch.dict("os.environ", {"SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": ""}):
            with patch("data.earnings_fetcher._yfinance_earnings_date", return_value=yf_date):
                result = check_pre_earnings("INFY", days_window=7)
        assert result["has_upcoming_earnings"] is True
        assert result["warning_level"] == "WARNING"
        assert result["source"] == "yfinance_live"

    def test_yfinance_fallback_critical(self):
        yf_date = date.today() + timedelta(days=1)
        with patch.dict("os.environ", {"SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": ""}):
            with patch("data.earnings_fetcher._yfinance_earnings_date", return_value=yf_date):
                result = check_pre_earnings("INFY", days_window=7)
        assert result["warning_level"] == "CRITICAL"

    def test_yfinance_past_date_ignored(self):
        """Earnings date in the past should be ignored."""
        yf_date = date.today() - timedelta(days=1)
        with patch.dict("os.environ", {"SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": ""}):
            with patch("data.earnings_fetcher._yfinance_earnings_date", return_value=yf_date):
                result = check_pre_earnings("INFY", days_window=7)
        assert result["warning_level"] == "CLEAR"

    def test_yfinance_far_future_ignored(self):
        """yfinance date beyond window → CLEAR."""
        yf_date = date.today() + timedelta(days=30)
        with patch.dict("os.environ", {"SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": ""}):
            with patch("data.earnings_fetcher._yfinance_earnings_date", return_value=yf_date):
                result = check_pre_earnings("INFY", days_window=7)
        assert result["warning_level"] == "CLEAR"


# ──────────────────────────────────────────────────────────────────────────────
# Custom days_window
# ──────────────────────────────────────────────────────────────────────────────

class TestCustomDaysWindow:
    def test_5day_window_excludes_7day_earnings(self):
        rows = [_make_supabase_row("TCS", 6)]
        with patch.dict("os.environ", {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
            with patch("supabase.create_client", return_value=_mock_supabase(rows)):
                result = check_pre_earnings("TCS", days_window=5)
        assert result["warning_level"] == "CLEAR"

    def test_14day_window_includes_10day_earnings(self):
        rows = [_make_supabase_row("TCS", 10)]
        with patch.dict("os.environ", {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
            with patch("supabase.create_client", return_value=_mock_supabase(rows)):
                result = check_pre_earnings("TCS", days_window=14)
        assert result["warning_level"] == "WARNING"
        assert result["days_until"] == 10


# ──────────────────────────────────────────────────────────────────────────────
# Graceful failure
# ──────────────────────────────────────────────────────────────────────────────

class TestGracefulFailure:
    def test_supabase_exception_returns_clear(self):
        with patch.dict("os.environ", {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
            with patch("supabase.create_client", side_effect=RuntimeError("DB down")):
                result = check_pre_earnings("BAJFINANCE")
        assert result["warning_level"] == "CLEAR"
        assert result["source"] == "error"

    def test_yfinance_exception_returns_clear(self):
        with patch.dict("os.environ", {"SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": ""}):
            with patch("data.earnings_fetcher._yfinance_earnings_date", side_effect=Exception("network err")):
                result = check_pre_earnings("BAJFINANCE")
        assert result["warning_level"] == "CLEAR"

    def test_never_raises(self):
        """check_pre_earnings must never propagate exceptions."""
        with patch.dict("os.environ", {"SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": ""}):
            with patch("data.earnings_fetcher._yfinance_earnings_date", side_effect=Exception("boom")):
                try:
                    result = check_pre_earnings("ANY")
                except Exception as exc:
                    pytest.fail(f"check_pre_earnings raised unexpectedly: {exc}")
        assert result is not None


# ──────────────────────────────────────────────────────────────────────────────
# Result dict structure
# ──────────────────────────────────────────────────────────────────────────────

class TestResultStructure:
    EXPECTED_KEYS = {
        "symbol", "has_upcoming_earnings", "earnings_date",
        "days_until", "warning_level", "quarter", "source",
    }

    def test_all_keys_present_clear(self):
        with patch.dict("os.environ", {"SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": ""}):
            with patch("data.earnings_fetcher._yfinance_earnings_date", return_value=None):
                result = check_pre_earnings("ITC")
        assert self.EXPECTED_KEYS <= set(result.keys())

    def test_all_keys_present_critical(self):
        rows = [_make_supabase_row("ITC", 1)]
        with patch.dict("os.environ", {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
            with patch("supabase.create_client", return_value=_mock_supabase(rows)):
                result = check_pre_earnings("ITC")
        assert self.EXPECTED_KEYS <= set(result.keys())

    def test_warning_level_values_are_valid(self):
        valid = {"CLEAR", "WARNING", "CRITICAL"}
        with patch.dict("os.environ", {"SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": ""}):
            with patch("data.earnings_fetcher._yfinance_earnings_date", return_value=None):
                result = check_pre_earnings("ITC")
        assert result["warning_level"] in valid
