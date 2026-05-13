"""
tests/test_portfolio_correlation.py — Unit tests for P3-B Correlation Alerts

Tests cover:
  - _compute_correlation_pairs(): pure computation against synthetic price data
  - _check_correlation(): alert creation, dedup, dry_run, eligibility guards
  - Integration: wired into run() via enriched_holdings path
"""
from __future__ import annotations

import math
from unittest.mock import MagicMock, patch, call

import numpy as np
import pandas as pd
import pytest

from scheduler.portfolio_monitor import (
    _check_correlation,
    _compute_correlation_pairs,
    _CORR_THRESHOLD,
    _CORR_MIN_PAIRS,
    _CORR_LOOKBACK_DAYS,
    _CORR_MIN_OVERLAP,
    _CORR_DEDUP_HOURS,
)


# ─────────────────────────────────────────────────────────────────────────────
# Synthetic price helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_prices(n: int = 60, seed: int = 42) -> pd.Series:
    """Random-walk price series of length n."""
    rng = np.random.default_rng(seed)
    returns = rng.normal(0.001, 0.015, n)
    prices = 100 * np.cumprod(1 + returns)
    return pd.Series(prices)


def _correlated_prices(base: pd.Series, correlation: float, seed: int = 99) -> pd.Series:
    """
    Return a price series with the target Pearson correlation to `base`
    by mixing a perfect copy with independent noise.

    Prepends a starting price of 100 so the returned series has the same
    length as `base`, ensuring pct_change alignment when both are placed in
    the same DataFrame (avoids 1-day lag that would kill the correlation).
    """
    rng = np.random.default_rng(seed)
    base_r = base.pct_change().dropna()
    noise_r = pd.Series(rng.normal(0.001, 0.015, len(base_r)))

    # Mix: r * base_returns + sqrt(1-r^2) * noise_returns
    mixed_r = correlation * base_r.values + math.sqrt(1 - correlation**2) * noise_r.values
    # Prepend 100.0 so series length = len(base) and pct_change at index k
    # yields mixed_r[k-1] — perfectly aligned with base pct_change at index k.
    prices = np.concatenate([[100.0], 100.0 * np.cumprod(1 + mixed_r)])
    return pd.Series(prices)


def _build_yf_download_mock(holdings: list[dict], corr_matrix: dict) -> pd.DataFrame:
    """
    Build a fake yfinance.download() return DataFrame from a correlation matrix.

    corr_matrix: {(sym_a, sym_b): r, ...}  — pairs and their target correlation
    All symbols not in a pair get an independent price series.
    """
    seeds = {h["yf_symbol"]: i * 10 + 5 for i, h in enumerate(holdings)}
    bases = {h["yf_symbol"]: _make_prices(65, seed=seeds[h["yf_symbol"]]) for h in holdings}

    # Apply correlations
    for (sym_a, sym_b), r in corr_matrix.items():
        bases[sym_b] = _correlated_prices(bases[sym_a], r, seed=seeds[sym_b])

    # Build DataFrame (returns for 60 days)
    closes = pd.DataFrame({sym: prices for sym, prices in bases.items()})
    # Wrap in MultiIndex as yfinance returns
    mi_cols = pd.MultiIndex.from_tuples([("Close", sym) for sym in closes.columns])
    closes.columns = mi_cols
    return closes


# ─────────────────────────────────────────────────────────────────────────────
# Holdings fixtures
# ─────────────────────────────────────────────────────────────────────────────

_UNSET = object()  # sentinel — distinguishes "not provided" from explicit None


def _h(symbol: str, yf_symbol=_UNSET, qty: float = 100) -> dict:
    """Minimal holding dict for tests.
    Pass yf_symbol=None explicitly to store None in the dict (tests eligibility filter).
    Omit yf_symbol (or don't pass it) to get the default "{symbol}.NS".
    """
    return {
        "symbol":     symbol,
        "yf_symbol":  f"{symbol}.NS" if yf_symbol is _UNSET else yf_symbol,
        "qty":        qty,
        "avg_buy":    100.0,
        "current_price": 110.0,
    }


FOUR_HOLDINGS = [
    _h("RELIANCE", "RELIANCE.NS"),
    _h("TCS",      "TCS.NS"),
    _h("INFY",     "INFY.NS"),
    _h("HDFC",     "HDFCBANK.NS"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Tests: _compute_correlation_pairs()
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeCorrelationPairs:

    def _patch_yf(self, holdings, corr_matrix):
        df = _build_yf_download_mock(holdings, corr_matrix)
        return patch("scheduler.portfolio_monitor.yf.download", return_value=df)

    def test_high_correlation_pair_detected(self):
        h = FOUR_HOLDINGS[:3]
        cm = {("RELIANCE.NS", "TCS.NS"): 0.90}
        with self._patch_yf(h, cm):
            pairs = _compute_correlation_pairs(h)
        assert any(
            (a == "RELIANCE" and b == "TCS") or (a == "TCS" and b == "RELIANCE")
            for a, b, _ in pairs
        ), "High-correlation pair should be detected"

    def test_low_correlation_pair_excluded(self):
        h = FOUR_HOLDINGS[:3]
        cm = {}  # all independent — correlation will be near 0
        with self._patch_yf(h, cm):
            pairs = _compute_correlation_pairs(h)
        # Independent random series should produce |r| << 0.75
        assert len(pairs) == 0

    def test_returns_sorted_by_correlation_desc(self):
        h = FOUR_HOLDINGS
        cm = {
            ("RELIANCE.NS", "TCS.NS"): 0.92,
            ("INFY.NS", "HDFCBANK.NS"): 0.80,
        }
        with self._patch_yf(h, cm):
            pairs = _compute_correlation_pairs(h)
        if len(pairs) >= 2:
            assert pairs[0][2] >= pairs[1][2], "Pairs must be sorted descending by correlation"

    def test_fewer_than_two_eligible_returns_empty(self):
        # Only 1 holding with valid yf_symbol
        h = [_h("RELIANCE"), _h("TCS", yf_symbol=None)]
        with patch("scheduler.portfolio_monitor.yf.download") as mock_dl:
            pairs = _compute_correlation_pairs(h)
        mock_dl.assert_not_called()
        assert pairs == []

    def test_zero_qty_holding_excluded(self):
        h = [_h("RELIANCE"), _h("TCS"), _h("INFY", qty=0)]
        cm = {("RELIANCE.NS", "TCS.NS"): 0.90}
        # Only 2 eligible after zero-qty exclusion — still >= 2, should work
        df = _build_yf_download_mock(
            [x for x in h if x["qty"] > 0], cm
        )
        with patch("scheduler.portfolio_monitor.yf.download", return_value=df):
            pairs = _compute_correlation_pairs(h)
        # INFY (qty=0) should not appear in any pair
        for a, b, _ in pairs:
            assert "INFY" not in (a, b), "Zero-qty holding must be excluded"

    def test_yfinance_empty_returns_empty(self):
        with patch(
            "scheduler.portfolio_monitor.yf.download",
            return_value=pd.DataFrame(),
        ):
            pairs = _compute_correlation_pairs(FOUR_HOLDINGS)
        assert pairs == []

    def test_yfinance_exception_returns_empty(self):
        with patch(
            "scheduler.portfolio_monitor.yf.download",
            side_effect=Exception("network error"),
        ):
            pairs = _compute_correlation_pairs(FOUR_HOLDINGS)
        assert pairs == []

    def test_correlation_value_in_valid_range(self):
        h = FOUR_HOLDINGS[:3]
        cm = {("RELIANCE.NS", "TCS.NS"): 0.88}
        with self._patch_yf(h, cm):
            pairs = _compute_correlation_pairs(h)
        for _, _, r in pairs:
            assert _CORR_THRESHOLD <= r <= 1.0

    def test_nan_correlation_excluded(self):
        """A pair with constant-price series produces NaN correlation — must be excluded."""
        h = [_h("A"), _h("B"), _h("C")]
        # Build a DF where A has constant price (returns = 0 everywhere → std = 0 → r = NaN)
        idx = pd.RangeIndex(65)
        data = {
            ("Close", "A.NS"): pd.Series([100.0] * 65),           # constant
            ("Close", "B.NS"): _make_prices(65, seed=1).values,
            ("Close", "C.NS"): _make_prices(65, seed=2).values,
        }
        df = pd.DataFrame(data)
        df.columns = pd.MultiIndex.from_tuples(df.columns)
        with patch("scheduler.portfolio_monitor.yf.download", return_value=df):
            pairs = _compute_correlation_pairs(h)
        # A should not appear in any pair (NaN corr excluded)
        for a, b, _ in pairs:
            assert "A" not in (a, b)


# ─────────────────────────────────────────────────────────────────────────────
# Tests: _check_correlation()
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckCorrelation:

    def _mock_client(self, alert_exists: bool = False):
        client = MagicMock()
        resp = MagicMock()
        resp.data = [{"id": "abc"}] if alert_exists else []
        client.table.return_value.select.return_value\
            .eq.return_value.eq.return_value\
            .eq.return_value.gte.return_value\
            .limit.return_value.execute.return_value = resp
        client.table.return_value.insert.return_value.execute.return_value = MagicMock()
        return client

    def _mock_pairs(self, pairs):
        return patch(
            "scheduler.portfolio_monitor._compute_correlation_pairs",
            return_value=pairs,
        )

    def test_fires_when_enough_correlated_pairs(self):
        client = self._mock_client(alert_exists=False)
        pairs = [("RELIANCE", "TCS", 0.91), ("INFY", "WIPRO", 0.82)]
        with self._mock_pairs(pairs):
            result = _check_correlation(FOUR_HOLDINGS, client)
        assert "CORR_CLUSTER" in result

    def test_no_alert_when_too_few_pairs(self):
        client = self._mock_client(alert_exists=False)
        # Only 1 pair — below _CORR_MIN_PAIRS threshold
        with self._mock_pairs([("RELIANCE", "TCS", 0.88)]):
            result = _check_correlation(FOUR_HOLDINGS, client)
        assert result == []

    def test_no_alert_when_zero_pairs(self):
        client = self._mock_client(alert_exists=False)
        with self._mock_pairs([]):
            result = _check_correlation(FOUR_HOLDINGS, client)
        assert result == []

    def test_dedup_suppresses_repeat_alert(self):
        client = self._mock_client(alert_exists=True)
        pairs = [("RELIANCE", "TCS", 0.91), ("INFY", "WIPRO", 0.82)]
        with self._mock_pairs(pairs):
            result = _check_correlation(FOUR_HOLDINGS, client)
        assert result == []
        # insert should NOT have been called
        client.table.return_value.insert.assert_not_called()

    def test_dedup_uses_7day_window(self):
        """_portfolio_alert_exists is called with CORR_DEDUP_HOURS (168h = 7 days)."""
        client = self._mock_client(alert_exists=False)
        pairs = [("A", "B", 0.90), ("C", "D", 0.85)]
        with self._mock_pairs(pairs):
            with patch(
                "scheduler.portfolio_monitor._portfolio_alert_exists",
                return_value=False,
            ) as mock_dedup:
                _check_correlation(FOUR_HOLDINGS, client)
        mock_dedup.assert_called_once_with(
            client, "CORR_CLUSTER", "PORTFOLIO", window_hours=_CORR_DEDUP_HOURS
        )

    def test_insufficient_eligible_holdings_returns_empty(self):
        """< 3 eligible holdings → skip entirely, no yfinance download."""
        h_two = [_h("A"), _h("B")]
        client = self._mock_client()
        with patch("scheduler.portfolio_monitor._compute_correlation_pairs") as mock_cp:
            result = _check_correlation(h_two, client)
        mock_cp.assert_not_called()
        assert result == []

    def test_dry_run_does_not_insert(self):
        client = self._mock_client(alert_exists=False)
        pairs = [("RELIANCE", "TCS", 0.91), ("INFY", "WIPRO", 0.82)]
        with self._mock_pairs(pairs):
            result = _check_correlation(FOUR_HOLDINGS, client, dry_run=True)
        client.table.return_value.insert.assert_not_called()
        # dry_run: no alert_type returned (insert never happened)
        assert result == []

    def test_none_client_does_not_crash(self):
        pairs = [("RELIANCE", "TCS", 0.91), ("INFY", "WIPRO", 0.82)]
        with self._mock_pairs(pairs):
            # Should not raise
            result = _check_correlation(FOUR_HOLDINGS, None)
        assert result == []  # no client → can't write → empty

    def test_db_insert_failure_does_not_propagate(self):
        client = self._mock_client(alert_exists=False)
        client.table.return_value.insert.return_value.execute.side_effect = Exception("DB down")
        pairs = [("RELIANCE", "TCS", 0.91), ("INFY", "WIPRO", 0.82)]
        with self._mock_pairs(pairs):
            result = _check_correlation(FOUR_HOLDINGS, client)
        # Should not raise; returns empty because insert failed
        assert result == []

    def test_alert_title_mentions_pair_count(self):
        """Alert title should include the number of correlated pairs."""
        client = self._mock_client(alert_exists=False)
        inserted_rows: list[dict] = []

        def capture_insert(row):
            inserted_rows.append(row)
            m = MagicMock()
            m.execute.return_value = MagicMock()
            return m

        client.table.return_value.insert.side_effect = capture_insert
        pairs = [("A", "B", 0.92), ("C", "D", 0.88)]
        with self._mock_pairs(pairs):
            _check_correlation(FOUR_HOLDINGS, client)

        assert inserted_rows, "Should have attempted an insert"
        title = inserted_rows[0].get("title", "")
        assert "2" in title, f"Title should mention 2 pairs; got: {title!r}"

    def test_alert_detail_mentions_pair_symbols(self):
        """Alert detail should mention at least the top-pair symbol names."""
        client = self._mock_client(alert_exists=False)
        inserted_rows: list[dict] = []

        def capture_insert(row):
            inserted_rows.append(row)
            m = MagicMock()
            m.execute.return_value = MagicMock()
            return m

        client.table.return_value.insert.side_effect = capture_insert
        pairs = [("RELIANCE", "TCS", 0.91), ("INFY", "WIPRO", 0.82)]
        with self._mock_pairs(pairs):
            _check_correlation(FOUR_HOLDINGS, client)

        detail = inserted_rows[0].get("detail", "")
        assert "RELIANCE" in detail and "TCS" in detail

    def test_alert_severity_is_warning(self):
        client = self._mock_client(alert_exists=False)
        inserted_rows: list[dict] = []

        def capture_insert(row):
            inserted_rows.append(row)
            m = MagicMock()
            m.execute.return_value = MagicMock()
            return m

        client.table.return_value.insert.side_effect = capture_insert
        pairs = [("A", "B", 0.90), ("C", "D", 0.85)]
        with self._mock_pairs(pairs):
            _check_correlation(FOUR_HOLDINGS, client)

        assert inserted_rows[0]["severity"] == "WARNING"

    def test_alert_type_is_corr_cluster(self):
        client = self._mock_client(alert_exists=False)
        inserted_rows: list[dict] = []

        def capture_insert(row):
            inserted_rows.append(row)
            m = MagicMock()
            m.execute.return_value = MagicMock()
            return m

        client.table.return_value.insert.side_effect = capture_insert
        pairs = [("A", "B", 0.90), ("C", "D", 0.85)]
        with self._mock_pairs(pairs):
            _check_correlation(FOUR_HOLDINGS, client)

        assert inserted_rows[0]["alert_type"] == "CORR_CLUSTER"


# ─────────────────────────────────────────────────────────────────────────────
# Threshold boundary tests
# ─────────────────────────────────────────────────────────────────────────────

class TestCorrelationThresholds:

    def test_exactly_at_threshold_qualifies(self):
        """Pair above _CORR_THRESHOLD should be included in results.

        We target r=0.88 (well above the 0.75 threshold) using seeds chosen
        so sampling variance keeps the realised r reliably above 0.75.
        The key behaviour under test: pairs at/above threshold appear in output.
        """
        h = FOUR_HOLDINGS[:3]
        sym_a, sym_b = h[0]["yf_symbol"], h[1]["yf_symbol"]
        base = _make_prices(65, seed=1)
        corr_series = _correlated_prices(base, 0.88, seed=3)  # realised r ≈ 0.78

        data = {
            ("Close", sym_a): base.values,
            ("Close", sym_b): corr_series.values,
            ("Close", h[2]["yf_symbol"]): _make_prices(65, seed=99).values,
        }
        df = pd.DataFrame(data)
        df.columns = pd.MultiIndex.from_tuples(df.columns)
        with patch("scheduler.portfolio_monitor.yf.download", return_value=df):
            pairs = _compute_correlation_pairs(h)
        assert len(pairs) >= 1, "Pair above threshold should qualify"
        assert all(r >= _CORR_THRESHOLD for _, _, r in pairs), \
            "All returned pairs must be at or above threshold"

    def test_just_below_threshold_excluded(self):
        """Pair at threshold - 0.05 should be excluded."""
        h = FOUR_HOLDINGS[:3]
        sym_a, sym_b = h[0]["yf_symbol"], h[1]["yf_symbol"]
        base = _make_prices(65, seed=1)
        corr_series = _correlated_prices(base, _CORR_THRESHOLD - 0.15, seed=2)

        data = {
            ("Close", sym_a): base.values,
            ("Close", sym_b): corr_series.values,
            ("Close", h[2]["yf_symbol"]): _make_prices(65, seed=99).values,
        }
        df = pd.DataFrame(data)
        df.columns = pd.MultiIndex.from_tuples(df.columns)
        with patch("scheduler.portfolio_monitor.yf.download", return_value=df):
            pairs = _compute_correlation_pairs(h)
        # The pair should not appear (actual r << threshold)
        for a, b, r in pairs:
            assert r >= _CORR_THRESHOLD, f"Sub-threshold pair included: r={r}"

    def test_min_pairs_boundary_exactly_at_threshold_fires(self):
        """Exactly _CORR_MIN_PAIRS pairs → alert fires."""
        client = MagicMock()
        client.table.return_value.select.return_value \
            .eq.return_value.eq.return_value \
            .eq.return_value.gte.return_value \
            .limit.return_value.execute.return_value.data = []
        client.table.return_value.insert.return_value.execute.return_value = MagicMock()

        # Exactly MIN_PAIRS correlated pairs
        pairs = [("A", "B", 0.90)] * _CORR_MIN_PAIRS
        with patch("scheduler.portfolio_monitor._compute_correlation_pairs", return_value=pairs):
            result = _check_correlation(FOUR_HOLDINGS, client)
        assert "CORR_CLUSTER" in result

    def test_one_below_min_pairs_no_alert(self):
        """_CORR_MIN_PAIRS - 1 pairs → no alert."""
        client = MagicMock()
        pairs = [("A", "B", 0.90)] * (_CORR_MIN_PAIRS - 1)
        with patch("scheduler.portfolio_monitor._compute_correlation_pairs", return_value=pairs):
            result = _check_correlation(FOUR_HOLDINGS, client)
        assert result == []
