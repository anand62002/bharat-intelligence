"""
tests/test_fetchers_integration.py -- Live integration tests for data/fetchers.py

These tests make REAL network calls.  Run them explicitly:

    pytest -m integration -v -s
    pytest -m integration --tb=short

They are excluded from the default pytest run (no -m flag) so CI never
breaks on flaky network conditions.

Each test:
  1. Calls the actual fetcher function
  2. Asserts the response has the correct structure and plausible values
  3. Prints which data source succeeded (visible with -s flag)
  4. Does NOT assert specific numeric values (those change daily)
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_reasonable_crore(value: object) -> bool:
    """Sanity check: Indian market FII/DII net flow is typically -20000 to +20000 Cr."""
    try:
        v = float(value)
        return -50_000 <= v <= 50_000
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# FII/DII daily flow
# ---------------------------------------------------------------------------

class TestFiiDiiIntegration:
    """Live integration tests for get_nse_fii_dii() and its sub-functions."""

    @pytest.mark.integration
    def test_get_nse_fii_dii_returns_data(self):
        """get_nse_fii_dii() must return a dict with fii_net and dii_net."""
        from data.fetchers import get_nse_fii_dii
        result = get_nse_fii_dii()
        print(f"\n[get_nse_fii_dii] result = {result}")
        assert result is not None, (
            "All four FII/DII sources failed. Check network connectivity "
            "and whether the source URLs are still valid."
        )
        assert "fii_net" in result, f"Missing fii_net in: {result}"
        assert "dii_net" in result, f"Missing dii_net in: {result}"
        assert "source"  in result, f"Missing source in: {result}"
        assert isinstance(result["fii_net"], float), f"fii_net not float: {result}"
        assert isinstance(result["dii_net"], float), f"dii_net not float: {result}"
        assert _is_reasonable_crore(result["fii_net"]), (
            f"fii_net={result['fii_net']} is outside plausible range (-50000..50000 Cr)"
        )
        assert _is_reasonable_crore(result["dii_net"]), (
            f"dii_net={result['dii_net']} is outside plausible range"
        )
        print(f"  [OK] source={result['source']}  "
              f"FII={result['fii_net']:.0f} Cr  DII={result['dii_net']:.0f} Cr  "
              f"date={result.get('date', 'N/A')}")

    @pytest.mark.integration
    def test_nse_direct_source(self):
        """NSE direct endpoint -- may fail (returns None), documents the result."""
        from data.fetchers import _try_nse_fii_dii
        result = _try_nse_fii_dii()
        print(f"\n[_try_nse_fii_dii] result = {result}")
        if result is None:
            pytest.skip("NSE direct endpoint unavailable (cookie/anti-bot block) -- expected")
        assert "fii_net" in result
        print(f"  [OK] NSE direct succeeded: FII={result['fii_net']:.0f} Cr")

    @pytest.mark.integration
    def test_bse_source(self):
        """BSE categorywise_turnover page -- primary fallback after NSE."""
        from data.fetchers import _try_bse_fii_dii
        result = _try_bse_fii_dii()
        print(f"\n[_try_bse_fii_dii] result = {result}")
        if result is None:
            pytest.skip("BSE source unavailable -- check URL or page structure")
        assert "fii_net" in result
        assert result["source"] == "bse"
        print(f"  [OK] BSE succeeded: FII={result['fii_net']:.0f} Cr  DII={result['dii_net']:.0f} Cr")

    @pytest.mark.integration
    def test_trendlyne_source(self):
        """Trendlyne macro FII/DII page -- confirmed working Apr 2026."""
        from data.fetchers import _try_trendlyne_fii_dii
        result = _try_trendlyne_fii_dii()
        print(f"\n[_try_trendlyne_fii_dii] result = {result}")
        if result is None:
            pytest.skip("Trendlyne source unavailable -- check URL or page structure")
        assert "fii_net" in result
        assert result["source"] == "trendlyne"
        print(f"  [OK] Trendlyne succeeded: FII={result['fii_net']:.0f} Cr  DII={result['dii_net']:.0f} Cr")

    @pytest.mark.integration
    def test_moneycontrol_source(self):
        """Moneycontrol FII/DII page -- last resort, sometimes 403."""
        from data.fetchers import _try_moneycontrol_fii_dii
        result = _try_moneycontrol_fii_dii()
        print(f"\n[_try_moneycontrol_fii_dii] result = {result}")
        if result is None:
            pytest.skip("Moneycontrol source unavailable (403) -- expected intermittently")
        assert "fii_net" in result
        print(f"  [OK] Moneycontrol succeeded: FII={result['fii_net']:.0f} Cr")

    @pytest.mark.integration
    def test_source_audit(self):
        """
        Audit test: tries all four sources independently and reports which
        ones are working.  Never fails -- purely diagnostic.
        """
        from data.fetchers import (
            _try_nse_fii_dii,
            _try_bse_fii_dii,
            _try_trendlyne_fii_dii,
            _try_moneycontrol_fii_dii,
        )
        sources = {
            "NSE direct":   _try_nse_fii_dii,
            "BSE HTML":     _try_bse_fii_dii,
            "Trendlyne":    _try_trendlyne_fii_dii,
            "Moneycontrol": _try_moneycontrol_fii_dii,
        }
        print("\n\n=== FII/DII Source Audit ===")
        working = []
        for name, fn in sources.items():
            try:
                result = fn()
                if result:
                    print(f"  [OK]   {name:15s}  FII={result['fii_net']:>10.1f} Cr  "
                          f"DII={result['dii_net']:>10.1f} Cr  date={result.get('date','?')}")
                    working.append(name)
                else:
                    print(f"  [FAIL] {name:15s}  returned None")
            except Exception as exc:
                print(f"  [FAIL] {name:15s}  raised {type(exc).__name__}: {exc}")

        print(f"\n  Working sources: {working or 'NONE'}")
        print("=== End Audit ===\n")
        # Don't assert -- this is purely diagnostic


# ---------------------------------------------------------------------------
# OHLCV price data
# ---------------------------------------------------------------------------

class TestOhlcvIntegration:

    @pytest.mark.integration
    def test_get_ohlcv_maruti(self):
        """yfinance OHLCV for MARUTI.NS should return at least 200 rows."""
        from data.fetchers import get_ohlcv
        df = get_ohlcv("MARUTI.NS", period="1y")
        print(f"\n[get_ohlcv MARUTI.NS] rows={len(df) if df is not None else 'None'}")
        assert df is not None, "get_ohlcv returned None for MARUTI.NS"
        assert len(df) >= 200, f"Expected >=200 rows, got {len(df)}"
        assert "Close" in df.columns
        last_valid = df["Close"].dropna().iloc[-1]
        assert float(last_valid) > 0
        print(f"  [OK] {len(df)} rows, latest close={last_valid:.2f}")

    @pytest.mark.integration
    def test_get_ohlcv_invalid_symbol(self):
        """Invalid symbol should return None gracefully, not raise."""
        from data.fetchers import get_ohlcv
        result = get_ohlcv("DEFINITELY_NOT_A_REAL_SYMBOL_XYZ.NS", period="1y")
        print(f"\n[get_ohlcv invalid] result={result}")
        assert result is None or (hasattr(result, "empty") and result.empty), (
            "Expected None or empty DataFrame for invalid symbol"
        )
        print("  [OK] Returned None/empty gracefully")


# ---------------------------------------------------------------------------
# Screener fundamentals
# ---------------------------------------------------------------------------

class TestScreenerIntegration:

    @pytest.mark.integration
    def test_get_screener_data_maruti(self):
        """Screener.in should return fundamental data for MARUTI."""
        from data.fetchers import get_screener_data
        result = get_screener_data("MARUTI.NS")
        print(f"\n[get_screener_data MARUTI.NS] result keys={list(result.keys()) if result else None}")
        assert result is not None, "get_screener_data returned None for MARUTI"
        # At least PE or ROCE should be populated
        has_data = any(
            result.get(k) is not None
            for k in ("pe", "roce", "promoter_holding", "revenue_growth")
        )
        assert has_data, f"All key metrics are None in screener result: {result}"
        print(f"  [OK] PE={result.get('pe')}  ROCE={result.get('roce')}  "
              f"Promoter={result.get('promoter_holding')}%  "
              f"FII={result.get('fii_holding_pct')}%  "
              f"DII={result.get('dii_holding_pct')}%")

    @pytest.mark.integration
    def test_screener_fii_dii_shareholding(self):
        """Screener should now return fii_holding_pct and dii_holding_pct."""
        from data.fetchers import get_screener_data
        result = get_screener_data("RELIANCE.NS")
        print(f"\n[screener shareholding RELIANCE.NS]")
        print(f"  fii_holding_pct = {result.get('fii_holding_pct') if result else 'N/A'}")
        print(f"  dii_holding_pct = {result.get('dii_holding_pct') if result else 'N/A'}")
        assert result is not None, "Screener returned None for RELIANCE"
        # These might be None if screener layout changed -- just document
        if result.get("fii_holding_pct") is not None:
            assert 0 <= result["fii_holding_pct"] <= 100
            print(f"  [OK] FII holding parsed: {result['fii_holding_pct']}%")
        else:
            print("  [WARN] fii_holding_pct not found -- screener layout may have changed")


# ---------------------------------------------------------------------------
# NSE FII/DII unit helpers (no network)
# ---------------------------------------------------------------------------

class TestParseHelpers:
    """Unit tests for the parsing helpers -- no network required."""

    def test_parse_fii_crore_plain(self):
        from data.fetchers import _parse_fii_crore
        assert _parse_fii_crore("1234.56") == pytest.approx(1234.56)

    def test_parse_fii_crore_comma(self):
        from data.fetchers import _parse_fii_crore
        assert _parse_fii_crore("1,234.56") == pytest.approx(1234.56)

    def test_parse_fii_crore_negative_endash(self):
        from data.fetchers import _parse_fii_crore
        assert _parse_fii_crore("\u22121500.00") == pytest.approx(-1500.0)

    def test_parse_fii_crore_brackets(self):
        from data.fetchers import _parse_fii_crore
        assert _parse_fii_crore("(300.00)") == pytest.approx(-300.0)

    def test_parse_fii_crore_rupee_symbol(self):
        from data.fetchers import _parse_fii_crore
        assert _parse_fii_crore("\u20b92,078.36") == pytest.approx(2078.36)

    def test_parse_fii_crore_empty(self):
        from data.fetchers import _parse_fii_crore
        assert _parse_fii_crore("") is None

    def test_parse_fii_crore_non_numeric(self):
        from data.fetchers import _parse_fii_crore
        assert _parse_fii_crore("N/A") is None

    def test_scrape_fii_table_generic(self):
        """_scrape_fii_table should parse a well-formed HTML table."""
        from bs4 import BeautifulSoup
        from data.fetchers import _scrape_fii_table
        html = """
        <table>
          <tr><th>Date</th><th>FII Net</th><th>DII Net</th></tr>
          <tr><td>22-04-2026</td><td>-2078.36</td><td>-1048.17</td></tr>
          <tr><td>21-04-2026</td><td>1500.00</td><td>800.00</td></tr>
        </table>
        """
        soup = BeautifulSoup(html, "html.parser")
        result = _scrape_fii_table(soup)
        assert result is not None
        assert result["fii_net"] == pytest.approx(-2078.36)
        assert result["dii_net"] == pytest.approx(-1048.17)
        assert result["date"] == "22-04-2026"

    def test_scrape_fii_table_buy_sell_columns(self):
        """_scrape_fii_table should compute net from buy and sell columns."""
        from bs4 import BeautifulSoup
        from data.fetchers import _scrape_fii_table
        html = """
        <table>
          <tr><th>Date</th><th>FII Buy</th><th>FII Sell</th><th>DII Buy</th><th>DII Sell</th></tr>
          <tr><td>22-04-2026</td><td>10000</td><td>12078.36</td><td>5000</td><td>6048.17</td></tr>
        </table>
        """
        soup = BeautifulSoup(html, "html.parser")
        result = _scrape_fii_table(soup)
        assert result is not None
        assert result["fii_net"] == pytest.approx(10000 - 12078.36)
        assert result["dii_net"] == pytest.approx(5000 - 6048.17)

    def test_scrape_fii_table_no_match(self):
        """_scrape_fii_table returns None when no FII/DII table is present."""
        from bs4 import BeautifulSoup
        from data.fetchers import _scrape_fii_table
        html = "<table><tr><th>Company</th><th>Revenue</th></tr></table>"
        soup = BeautifulSoup(html, "html.parser")
        assert _scrape_fii_table(soup) is None
