"""
tests/test_portfolio_risk.py
=============================
Unit tests for agents/portfolio_risk.py

Tests validate:
  - Portfolio weights calculation
  - Sector weights + HHI
  - VaR / CVaR formulas
  - Sharpe ratio
  - Annualised volatility
  - Max drawdown
  - Concentration risk classification
  - run_portfolio_risk() with mocked holdings + yfinance
  - Graceful failure (no holdings, network error)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.portfolio_risk import (
    _portfolio_weights,
    _sector_weights,
    _hhi,
    _var,
    _cvar,
    _max_drawdown,
    _annualised_vol,
    _sharpe,
    _concentration_risk_level,
    run_portfolio_risk,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_holdings(*args) -> list[dict]:
    """
    Each arg: (symbol, yf_symbol, qty, price, sector).
    """
    return [
        {"symbol": a[0], "yf_symbol": a[1], "qty": a[2],
         "current_price": a[3], "avg_buy": a[3], "sector": a[4]}
        for a in args
    ]


def _make_returns(symbols: list[str], n: int = 252, seed: int = 42) -> pd.DataFrame:
    """Generate random daily return DataFrame."""
    rng = np.random.default_rng(seed)
    data = rng.normal(0.0005, 0.015, size=(n, len(symbols)))
    idx  = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame(data, columns=symbols, index=idx)


# ──────────────────────────────────────────────────────────────────────────────
# Portfolio weights
# ──────────────────────────────────────────────────────────────────────────────

class TestPortfolioWeights:
    def test_weights_sum_to_one(self):
        h = _make_holdings(
            ("A", "A.NS", 10, 100, "IT"),
            ("B", "B.NS", 20, 200, "Finance"),
        )
        w = _portfolio_weights(h)
        assert abs(sum(w.values()) - 1.0) < 1e-9

    def test_equal_value_equal_weight(self):
        h = _make_holdings(
            ("A", "A.NS", 10, 100, "IT"),    # value = 1000
            ("B", "B.NS",  5, 200, "Finance"),  # value = 1000
        )
        w = _portfolio_weights(h)
        assert w["A.NS"] == pytest.approx(0.5)
        assert w["B.NS"] == pytest.approx(0.5)

    def test_empty_holdings(self):
        assert _portfolio_weights([]) == {}

    def test_zero_price_uses_fallback(self):
        """When current_price=0 and avg_buy=0, the fallback is 1 — weight still computed."""
        h = _make_holdings(("A", "A.NS", 10, 0, "IT"))
        w = _portfolio_weights(h)
        # Implementation falls back to 1 when both price and avg_buy are 0
        assert "A.NS" in w


# ──────────────────────────────────────────────────────────────────────────────
# Sector weights + HHI
# ──────────────────────────────────────────────────────────────────────────────

class TestSectorWeights:
    def test_single_sector_weight_one(self):
        h = _make_holdings(
            ("A", "A.NS", 10, 100, "IT"),
            ("B", "B.NS",  5, 200, "IT"),
        )
        sw = _sector_weights(h)
        assert sw == {"IT": pytest.approx(1.0)}

    def test_two_sectors_correct_split(self):
        h = _make_holdings(
            ("A", "A.NS", 10, 100, "IT"),        # 1000
            ("B", "B.NS", 10, 300, "Finance"),   # 3000
        )
        sw = _sector_weights(h)
        assert sw["IT"]      == pytest.approx(0.25)
        assert sw["Finance"] == pytest.approx(0.75)


class TestHHI:
    def test_perfect_diversification(self):
        """Four equal-weight sectors → HHI = 0.25."""
        w = {"A": 0.25, "B": 0.25, "C": 0.25, "D": 0.25}
        assert _hhi(w) == pytest.approx(0.25)

    def test_full_concentration(self):
        """Single sector → HHI = 1.0."""
        assert _hhi({"IT": 1.0}) == pytest.approx(1.0)


class TestConcentrationRisk:
    def test_low(self):
        assert _concentration_risk_level(0.05) == "LOW"

    def test_moderate(self):
        assert _concentration_risk_level(0.15) == "MODERATE"

    def test_high(self):
        assert _concentration_risk_level(0.30) == "HIGH"


# ──────────────────────────────────────────────────────────────────────────────
# VaR / CVaR
# ──────────────────────────────────────────────────────────────────────────────

class TestVaR:
    def test_var_positive_loss(self):
        """VaR should be a positive number (reported as a loss)."""
        rets = pd.Series(np.random.default_rng(0).normal(0, 0.01, 500))
        assert _var(rets, 0.95) > 0

    def test_var_99_greater_than_95(self):
        rets = pd.Series(np.random.default_rng(0).normal(0, 0.01, 500))
        assert _var(rets, 0.99) >= _var(rets, 0.95)

    def test_cvar_greater_than_var(self):
        rets = pd.Series(np.random.default_rng(0).normal(0, 0.01, 500))
        assert _cvar(rets, 0.95) >= _var(rets, 0.95)


# ──────────────────────────────────────────────────────────────────────────────
# Volatility + Sharpe
# ──────────────────────────────────────────────────────────────────────────────

class TestVolatility:
    def test_annualised_vol_positive(self):
        rets = pd.Series(np.random.default_rng(1).normal(0, 0.01, 252))
        assert _annualised_vol(rets) > 0

    def test_annualised_vol_approx(self):
        """Alternating ±1% returns → std ≈ 0.01 → annualised ≈ 15.87%."""
        # Alternate between +1% and -1% so std is non-zero
        rets = pd.Series([0.01 if i % 2 == 0 else -0.01 for i in range(252)])
        # std of alternating ±c is exactly c
        assert _annualised_vol(rets) == pytest.approx(0.01 * 252**0.5, rel=0.01)


class TestSharpe:
    def test_positive_return_positive_sharpe(self):
        rets = pd.Series([0.001] * 252)   # 0.1%/day → ~25% annual
        vol  = _annualised_vol(rets)
        s    = _sharpe(rets, vol)
        assert s is not None and s > 0

    def test_zero_vol_returns_none(self):
        rets = pd.Series([0.0] * 252)
        assert _sharpe(rets, 0.0) is None


# ──────────────────────────────────────────────────────────────────────────────
# Max drawdown
# ──────────────────────────────────────────────────────────────────────────────

class TestMaxDrawdown:
    def test_no_drawdown(self):
        """Monotonically rising prices → drawdown = 0."""
        prices = pd.Series([100 + i for i in range(100)])
        assert _max_drawdown(prices) == pytest.approx(0.0)

    def test_full_loss(self):
        """Price falls to 0 → drawdown = 1.0 (100%)."""
        prices = pd.Series([100, 50, 0.0001])
        assert _max_drawdown(prices) == pytest.approx(1.0, abs=0.001)

    def test_known_drawdown(self):
        """Peak=100, trough=80 → drawdown=20%."""
        prices = pd.Series([100, 90, 80, 85, 90])
        assert _max_drawdown(prices) == pytest.approx(0.20)


# ──────────────────────────────────────────────────────────────────────────────
# Integration: run_portfolio_risk with mocks
# ──────────────────────────────────────────────────────────────────────────────

def _mock_supabase_holdings(holdings: list[dict]):
    mock_exec = MagicMock()
    mock_exec.data = holdings
    chain = MagicMock()
    chain.execute.return_value = mock_exec
    chain.eq.return_value = chain
    chain.select.return_value = chain
    client = MagicMock()
    client.table.return_value = chain
    return client


class TestRunPortfolioRisk:
    def _run(self, holdings, returns_df):
        with patch.dict("os.environ", {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
            with patch("supabase.create_client", return_value=_mock_supabase_holdings(holdings)):
                with patch("agents.portfolio_risk._fetch_returns", return_value=returns_df):
                    return run_portfolio_risk(dry_run=True)

    def test_basic_run_returns_metrics(self):
        h = _make_holdings(
            ("RELIANCE", "RELIANCE.NS", 10, 2800, "Energy"),
            ("INFY",     "INFY.NS",      5, 1500, "IT"),
        )
        rets = _make_returns(["RELIANCE.NS", "INFY.NS"])
        result = self._run(h, rets)
        assert result["error"] is None
        assert result["portfolio_vol"] is not None
        assert result["var_95"] is not None
        assert result["sharpe"] is not None

    def test_sector_weights_populated(self):
        h = _make_holdings(
            ("A", "A.NS", 10, 100, "IT"),
            ("B", "B.NS", 10, 100, "Finance"),
        )
        rets = _make_returns(["A.NS", "B.NS"])
        result = self._run(h, rets)
        assert "IT" in result["sector_weights"]
        assert "Finance" in result["sector_weights"]

    def test_hhi_computed(self):
        h = _make_holdings(
            ("A", "A.NS", 10, 100, "IT"),
            ("B", "B.NS", 10, 100, "IT"),
        )
        rets = _make_returns(["A.NS", "B.NS"])
        result = self._run(h, rets)
        assert result["hhi"] == pytest.approx(1.0)   # both IT → full concentration
        assert result["concentration_risk"] == "HIGH"

    def test_no_holdings_returns_error(self):
        with patch.dict("os.environ", {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
            with patch("supabase.create_client", return_value=_mock_supabase_holdings([])):
                result = run_portfolio_risk(dry_run=True)
        assert result["error"] is not None

    def test_empty_returns_df(self):
        h = _make_holdings(("A", "A.NS", 10, 100, "IT"))
        with patch.dict("os.environ", {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
            with patch("supabase.create_client", return_value=_mock_supabase_holdings(h)):
                with patch("agents.portfolio_risk._fetch_returns", return_value=pd.DataFrame()):
                    result = run_portfolio_risk(dry_run=True)
        assert result["error"] is not None

    def test_result_keys_present(self):
        h = _make_holdings(("A", "A.NS", 10, 100, "IT"))
        rets = _make_returns(["A.NS"])
        result = self._run(h, rets)
        for key in ("portfolio_vol", "var_95", "var_99", "cvar_95", "sharpe",
                    "max_drawdown_pct", "sector_weights", "hhi", "concentration_risk",
                    "warnings", "snapshot_date"):
            assert key in result, f"Missing key: {key}"

    def test_highly_correlated_pair_warning(self):
        """Two perfectly correlated assets should trigger a warning."""
        h = _make_holdings(
            ("A", "A.NS", 10, 100, "IT"),
            ("B", "B.NS", 10, 100, "Finance"),
        )
        # Same returns → correlation = 1.0
        base = np.random.default_rng(7).normal(0, 0.01, 252)
        rets = pd.DataFrame(
            {"A.NS": base, "B.NS": base},
            index=pd.date_range("2024-01-01", periods=252, freq="B"),
        )
        result = self._run(h, rets)
        has_corr_warning = any("correlation" in w.lower() or "correlated" in w.lower()
                               for w in result["warnings"])
        assert has_corr_warning
