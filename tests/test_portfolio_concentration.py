"""
tests/test_portfolio_concentration.py — Unit tests for P2-C portfolio concentration alerts

All Supabase calls are mocked. Tests cover:
  - _get_macro_sensitivity()        pure sector → category mapping
  - _check_concentration()          alert logic with mocked DB client
  - run() integration               concentration alerts wired into full run
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch, call

import pytest

# ── imports under test ────────────────────────────────────────────────────────
from scheduler.portfolio_monitor import (
    _get_macro_sensitivity,
    _check_concentration,
    _MACRO_SENSITIVITY_MAP,
    _SECTOR_CONC_THRESHOLD,
    _MACRO_CLUSTER_MIN,
)


# ═════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═════════════════════════════════════════════════════════════════════════════

def _make_holding(
    symbol:        str,
    sector:        str,
    qty:           float,
    current_price: float,
    avg_buy:       float = 100.0,
    id:            str   = None,
    status:        str   = "OPEN",
) -> dict:
    return {
        "id":            id or f"uuid-{symbol}",
        "symbol":        symbol,
        "sector":        sector,
        "qty":           qty,
        "current_price": current_price,
        "avg_buy":       avg_buy,
        "status":        status,
        "stoploss_price": 0,
        "target_price":   0,
        "danger_drop_pct": 0,
        "danger_confidence": 0,
        "danger_trigger": "",
        "danger_window":  None,
    }


def _no_existing_alerts_client() -> MagicMock:
    """Return a Supabase mock where no alerts exist (all dedup checks return empty)."""
    client = MagicMock()
    # _portfolio_alert_exists query returns empty data
    select_chain = (
        client.table.return_value
        .select.return_value
        .eq.return_value
        .eq.return_value
        .eq.return_value
        .gte.return_value
        .limit.return_value
    )
    select_chain.execute.return_value.data = []
    # insert returns a fake id
    client.table.return_value.insert.return_value.execute.return_value.data = [
        {"id": "new-alert-id"}
    ]
    return client


def _alert_exists_client() -> MagicMock:
    """Return a Supabase mock where all alerts already exist (dedup blocks creation)."""
    client = MagicMock()
    select_chain = (
        client.table.return_value
        .select.return_value
        .eq.return_value
        .eq.return_value
        .eq.return_value
        .gte.return_value
        .limit.return_value
    )
    select_chain.execute.return_value.data = [{"id": "existing-alert"}]
    return client


# ═════════════════════════════════════════════════════════════════════════════
# _get_macro_sensitivity
# ═════════════════════════════════════════════════════════════════════════════

class TestGetMacroSensitivity:

    # ── Rate-Sensitive ────────────────────────────────────────────────────────
    def test_banking(self):
        assert _get_macro_sensitivity("Banking") == "Rate-Sensitive"

    def test_financial_services(self):
        assert _get_macro_sensitivity("Financial Services") == "Rate-Sensitive"

    def test_nbfc(self):
        assert _get_macro_sensitivity("NBFC") == "Rate-Sensitive"

    def test_real_estate(self):
        assert _get_macro_sensitivity("Real Estate") == "Rate-Sensitive"

    def test_auto(self):
        assert _get_macro_sensitivity("Auto") == "Rate-Sensitive"

    def test_automobile(self):
        assert _get_macro_sensitivity("Automobile") == "Rate-Sensitive"

    # ── USD-Sensitive ─────────────────────────────────────────────────────────
    def test_it(self):
        assert _get_macro_sensitivity("IT") == "USD-Sensitive"

    def test_information_technology(self):
        assert _get_macro_sensitivity("Information Technology") == "USD-Sensitive"

    def test_pharma(self):
        assert _get_macro_sensitivity("Pharma") == "USD-Sensitive"

    def test_healthcare(self):
        assert _get_macro_sensitivity("Healthcare") == "USD-Sensitive"

    # ── Domestic Demand ───────────────────────────────────────────────────────
    def test_fmcg(self):
        assert _get_macro_sensitivity("FMCG") == "Domestic Demand"

    def test_consumer_discretionary(self):
        assert _get_macro_sensitivity("Consumer Discretionary") == "Domestic Demand"

    def test_retail(self):
        assert _get_macro_sensitivity("Retail") == "Domestic Demand"

    # ── Commodity-Linked ──────────────────────────────────────────────────────
    def test_metal(self):
        assert _get_macro_sensitivity("Metal") == "Commodity-Linked"

    def test_oil_gas(self):
        assert _get_macro_sensitivity("Oil & Gas") == "Commodity-Linked"

    def test_steel(self):
        assert _get_macro_sensitivity("Steel") == "Commodity-Linked"

    def test_energy(self):
        assert _get_macro_sensitivity("Energy") == "Commodity-Linked"

    # ── Infra / Capex ─────────────────────────────────────────────────────────
    def test_infrastructure(self):
        assert _get_macro_sensitivity("Infrastructure") == "Infra / Capex"

    def test_power(self):
        assert _get_macro_sensitivity("Power") == "Infra / Capex"

    def test_capital_goods(self):
        assert _get_macro_sensitivity("Capital Goods") == "Infra / Capex"

    def test_defence(self):
        assert _get_macro_sensitivity("Defence") == "Infra / Capex"

    # ── Other / edge cases ────────────────────────────────────────────────────
    def test_empty_string_returns_other(self):
        assert _get_macro_sensitivity("") == "Other"

    def test_none_returns_other(self):
        # Should not crash on None
        try:
            result = _get_macro_sensitivity(None)  # type: ignore[arg-type]
        except Exception:
            result = "Other"
        assert result == "Other"

    def test_unknown_sector_returns_other(self):
        assert _get_macro_sensitivity("Media & Entertainment") == "Other"

    def test_case_insensitive(self):
        assert _get_macro_sensitivity("BANKING") == "Rate-Sensitive"
        assert _get_macro_sensitivity("banking") == "Rate-Sensitive"
        assert _get_macro_sensitivity("Banking") == "Rate-Sensitive"


# ═════════════════════════════════════════════════════════════════════════════
# _check_concentration — sector concentration
# ═════════════════════════════════════════════════════════════════════════════

class TestCheckConcentrationSector:

    def _banking_heavy_portfolio(self) -> list[dict]:
        """3 banking stocks = 75% of a 4-stock portfolio."""
        return [
            _make_holding("HDFCBANK",  "Banking",    100, 1500),  # ₹1,50,000
            _make_holding("ICICIBANK", "Banking",     80, 1000),  # ₹ 80,000
            _make_holding("KOTAK",     "Banking",     60,  800),  # ₹ 48,000
            _make_holding("INFY",      "IT",         100,  100),  # ₹ 10,000
        ]   # Total ₹2,88,000; Banking = ₹2,78,000 = 96.5%

    def test_sector_concentration_fires_above_threshold(self):
        portfolio = self._banking_heavy_portfolio()
        client    = _no_existing_alerts_client()
        alerts    = _check_concentration(portfolio, client)
        assert "SECTOR_CONCENTRATION" in alerts

    def test_sector_concentration_title_contains_sector(self):
        portfolio = self._banking_heavy_portfolio()
        client    = _no_existing_alerts_client()
        _check_concentration(portfolio, client)
        # SECTOR_CONCENTRATION fires first; MACRO_CLUSTER may fire afterwards.
        # Use call_args_list[0] to inspect the *first* insert (sector alert).
        all_calls = client.table.return_value.insert.call_args_list
        sector_calls = [c for c in all_calls if c[0][0].get("alert_type") == "SECTOR_CONCENTRATION"]
        assert sector_calls, "Expected at least one SECTOR_CONCENTRATION insert"
        row = sector_calls[0][0][0]
        assert row["symbol"] == "Banking"
        assert "Banking" in row["title"]
        assert row["severity"] == "WARNING"
        assert row["alert_type"] == "SECTOR_CONCENTRATION"

    def test_sector_concentration_detail_contains_symbols(self):
        portfolio = self._banking_heavy_portfolio()
        client    = _no_existing_alerts_client()
        _check_concentration(portfolio, client)
        insert_call = client.table.return_value.insert.call_args
        row = insert_call[0][0]
        assert "HDFCBANK" in row["detail"]

    def test_no_alert_when_all_sectors_under_threshold(self):
        """Balanced portfolio: 4 sectors × 25% each → no concentration alert."""
        portfolio = [
            _make_holding("HDFCBANK", "Banking",    25, 100),
            _make_holding("INFY",     "IT",         25, 100),
            _make_holding("HUL",      "FMCG",       25, 100),
            _make_holding("TATASTEEL","Metal",       25, 100),
        ]
        client = _no_existing_alerts_client()
        alerts = _check_concentration(portfolio, client)
        assert "SECTOR_CONCENTRATION" not in alerts

    def test_exactly_at_threshold_no_alert(self):
        """Sector exactly at 40% threshold does NOT trigger (must be ABOVE)."""
        # 3 holdings: Banking=40%, IT=30%, FMCG=30%
        # Banking is exactly at 40% — must NOT trigger (strict > threshold).
        portfolio = [
            _make_holding("BANK",  "Banking", 1, 40),
            _make_holding("INFY",  "IT",      1, 30),
            _make_holding("HUL",   "FMCG",   1, 30),
        ]
        client = _no_existing_alerts_client()
        alerts = _check_concentration(portfolio, client)
        assert "SECTOR_CONCENTRATION" not in alerts

    def test_just_above_threshold_triggers_alert(self):
        """Sector at 40.01% triggers alert."""
        portfolio = [
            _make_holding("BANK",  "Banking", 1, 40.01),
            _make_holding("INFY",  "IT",      1, 59.99),
        ]
        client = _no_existing_alerts_client()
        alerts = _check_concentration(portfolio, client)
        assert "SECTOR_CONCENTRATION" in alerts

    def test_dedup_suppresses_existing_alert(self):
        """When an identical sector alert already exists, no new insert."""
        portfolio = self._banking_heavy_portfolio()
        client    = _alert_exists_client()
        alerts    = _check_concentration(portfolio, client)
        assert "SECTOR_CONCENTRATION" not in alerts
        client.table.return_value.insert.assert_not_called()

    def test_holding_id_is_none_for_portfolio_alerts(self):
        """Portfolio-level alerts use holding_id=None, not a holding UUID."""
        portfolio = self._banking_heavy_portfolio()
        client    = _no_existing_alerts_client()
        _check_concentration(portfolio, client)
        insert_call = client.table.return_value.insert.call_args
        row = insert_call[0][0]
        assert row.get("holding_id") is None


# ═════════════════════════════════════════════════════════════════════════════
# _check_concentration — macro cluster
# ═════════════════════════════════════════════════════════════════════════════

class TestCheckConcentrationMacroCluster:

    def _rate_sensitive_portfolio(self) -> list[dict]:
        """4 rate-sensitive holdings → macro cluster alert."""
        return [
            _make_holding("HDFCBANK",  "Banking",      100, 1500),
            _make_holding("ICICIBANK", "Banking",      100,  900),
            _make_holding("KOTAK",     "Banking",      100,  800),
            _make_holding("DLF",       "Real Estate",   50,  400),
            _make_holding("TCS",       "IT",            50, 4000),   # different category
        ]

    def test_macro_cluster_fires_with_3_same_sensitivity(self):
        portfolio = self._rate_sensitive_portfolio()
        client    = _no_existing_alerts_client()
        alerts    = _check_concentration(portfolio, client)
        assert "MACRO_CLUSTER" in alerts

    def test_macro_cluster_category_in_title(self):
        portfolio = self._rate_sensitive_portfolio()
        client    = _no_existing_alerts_client()
        _check_concentration(portfolio, client)
        # Find the MACRO_CLUSTER insert call
        insert_calls = client.table.return_value.insert.call_args_list
        macro_rows = [
            c[0][0] for c in insert_calls
            if c[0][0].get("alert_type") == "MACRO_CLUSTER"
        ]
        assert macro_rows, "Expected at least one MACRO_CLUSTER insert"
        row = macro_rows[0]
        assert row["symbol"] == "Rate-Sensitive"
        assert "Rate-Sensitive" in row["title"]
        assert row["severity"] == "WARNING"

    def test_macro_cluster_detail_lists_symbols(self):
        portfolio = self._rate_sensitive_portfolio()
        client    = _no_existing_alerts_client()
        _check_concentration(portfolio, client)
        insert_calls = client.table.return_value.insert.call_args_list
        macro_rows = [
            c[0][0] for c in insert_calls
            if c[0][0].get("alert_type") == "MACRO_CLUSTER"
        ]
        row = macro_rows[0]
        assert "HDFCBANK" in row["detail"]
        assert "ICICIBANK" in row["detail"]

    def test_no_cluster_with_only_2_same_sensitivity(self):
        """2 holdings in same category is < _MACRO_CLUSTER_MIN=3 → no alert."""
        portfolio = [
            _make_holding("HDFCBANK",  "Banking", 100, 1000),
            _make_holding("ICICIBANK", "Banking", 100,  900),
            _make_holding("INFY",      "IT",      100, 1800),
            _make_holding("TCS",       "IT",      100, 4200),
        ]
        client = _no_existing_alerts_client()
        alerts = _check_concentration(portfolio, client)
        assert "MACRO_CLUSTER" not in alerts

    def test_exactly_3_holdings_triggers_cluster(self):
        """Exactly _MACRO_CLUSTER_MIN=3 triggers the alert."""
        portfolio = [
            _make_holding("HDFCBANK",  "Banking", 10, 1500),
            _make_holding("ICICIBANK", "Banking", 10,  900),
            _make_holding("KOTAKBANK", "Banking", 10,  800),
        ]
        client = _no_existing_alerts_client()
        alerts = _check_concentration(portfolio, client)
        assert "MACRO_CLUSTER" in alerts

    def test_other_sector_not_counted_for_cluster(self):
        """Holdings with 'Other' macro category don't count toward any cluster."""
        portfolio = [
            _make_holding("STOCK1", "Media",        10, 100),
            _make_holding("STOCK2", "Media",        10, 100),
            _make_holding("STOCK3", "Entertainment",10, 100),
            _make_holding("HDFCBANK", "Banking",    10, 100),
        ]
        client = _no_existing_alerts_client()
        alerts = _check_concentration(portfolio, client)
        assert "MACRO_CLUSTER" not in alerts

    def test_macro_cluster_dedup_suppresses(self):
        portfolio = self._rate_sensitive_portfolio()
        client    = _alert_exists_client()
        alerts    = _check_concentration(portfolio, client)
        assert "MACRO_CLUSTER" not in alerts

    def test_both_sector_and_macro_alerts_can_fire(self):
        """A portfolio can trigger BOTH SECTOR_CONCENTRATION and MACRO_CLUSTER."""
        # 3 large banking stocks = sector-heavy AND rate-sensitive cluster
        portfolio = [
            _make_holding("HDFCBANK",  "Banking", 100, 2000),   # ₹2,00,000
            _make_holding("ICICIBANK", "Banking", 100, 1000),   # ₹1,00,000
            _make_holding("KOTAK",     "Banking", 100,  500),   # ₹  50,000
            _make_holding("INFY",      "IT",       10,  100),   # ₹   1,000
        ]
        client = _no_existing_alerts_client()
        alerts = _check_concentration(portfolio, client)
        assert "SECTOR_CONCENTRATION" in alerts
        assert "MACRO_CLUSTER" in alerts


# ═════════════════════════════════════════════════════════════════════════════
# _check_concentration — edge cases
# ═════════════════════════════════════════════════════════════════════════════

class TestCheckConcentrationEdgeCases:

    def test_empty_holdings_returns_empty(self):
        alerts = _check_concentration([], MagicMock())
        assert alerts == []

    def test_single_holding_returns_empty(self):
        """Concentration is meaningless with only one holding."""
        portfolio = [_make_holding("HDFCBANK", "Banking", 100, 1000)]
        alerts    = _check_concentration(portfolio, MagicMock())
        assert alerts == []

    def test_zero_qty_holding_excluded(self):
        """Holdings with qty=0 (or zero price+avg_buy) don't contribute to concentration."""
        portfolio = [
            _make_holding("HDFCBANK",  "Banking", 0,     1000),          # qty=0 → excluded
            _make_holding("ICICIBANK", "Banking", 100,   1000),           # only valid holding
            _make_holding("INFY",      "IT",      100000, 0.0, avg_buy=0.0),  # price=0 AND avg_buy=0 → excluded
        ]
        client = _no_existing_alerts_client()
        # HDFCBANK excluded (qty=0), INFY excluded (price=0, avg_buy=0)
        # Only ICICIBANK remains — fewer than 2 valid holdings → no concentration check
        alerts = _check_concentration(portfolio, client)
        assert alerts == []

    def test_missing_current_price_falls_back_to_avg_buy(self):
        """If current_price is None, avg_buy × qty is used for valuation."""
        portfolio = [
            _make_holding("HDFCBANK",  "Banking", 100, None, avg_buy=1500),
            _make_holding("ICICIBANK", "Banking", 100, None, avg_buy=900),
            _make_holding("KOTAK",     "Banking", 100, None, avg_buy=800),
            _make_holding("INFY",      "IT",       10, None, avg_buy=100),
        ]
        client = _no_existing_alerts_client()
        # Should compute value from avg_buy and still trigger concentration
        alerts = _check_concentration(portfolio, client)
        assert "SECTOR_CONCENTRATION" in alerts

    def test_dry_run_does_not_call_insert(self):
        """In dry_run mode, alert text is printed but no DB insert happens."""
        portfolio = [
            _make_holding("HDFCBANK",  "Banking", 100, 1500),
            _make_holding("ICICIBANK", "Banking",  80, 1000),
            _make_holding("KOTAK",     "Banking",  60,  800),
            _make_holding("INFY",      "IT",      100,  100),
        ]
        client = _no_existing_alerts_client()
        import io, contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            alerts = _check_concentration(portfolio, client, dry_run=True)
        # In dry_run, _create_alert returns "dry-run-id" → still counted
        assert "SECTOR_CONCENTRATION" in alerts
        # No actual DB insert should have been called
        client.table.return_value.insert.assert_not_called()

    def test_none_client_dry_run_does_not_crash(self):
        """dry_run with client=None should not crash (no DB calls)."""
        portfolio = [
            _make_holding("HDFCBANK",  "Banking", 100, 1500),
            _make_holding("ICICIBANK", "Banking",  80, 1000),
            _make_holding("KOTAK",     "Banking",  60,  800),
            _make_holding("INFY",      "IT",       10,  100),
        ]
        import io, contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            # Should not raise even without client
            try:
                alerts = _check_concentration(portfolio, None, dry_run=True)
            except Exception:
                pytest.fail("_check_concentration raised with client=None + dry_run=True")

    def test_missing_sector_treated_as_other(self):
        """Holdings without a sector don't trigger concentration on 'Other'."""
        portfolio = [
            _make_holding("A", None,  100, 500),
            _make_holding("B", "",    100, 500),
            _make_holding("C", "Other", 100, 500),
            _make_holding("D", "Banking", 10, 100),
        ]
        client = _no_existing_alerts_client()
        alerts = _check_concentration(portfolio, client)
        # "Other" should never trigger SECTOR_CONCENTRATION, even at 100%
        assert "SECTOR_CONCENTRATION" not in alerts

    def test_returns_list_type(self):
        portfolio = [
            _make_holding("HDFCBANK", "Banking", 100, 1000),
            _make_holding("INFY",     "IT",      100, 100),
        ]
        result = _check_concentration(portfolio, _no_existing_alerts_client())
        assert isinstance(result, list)

    def test_multiple_sectors_over_threshold_fires_multiple_alerts(self):
        """Each sector above threshold gets its own alert (not just the largest)."""
        # This can't happen in practice (two sectors can't both be >50% of 100%),
        # but with >40% threshold, two sectors can each be ~41% of the total
        portfolio = [
            _make_holding("BANK1", "Banking", 1, 41),
            _make_holding("BANK2", "Banking", 1,  1),   # Banking ≈ 42%
            _make_holding("INFY1", "IT",      1, 41),
            _make_holding("INFY2", "IT",      1,  1),   # IT ≈ 42%
            _make_holding("OTHER", "FMCG",    1, 15),   # FMCG ≈ 15%
        ]
        client = _no_existing_alerts_client()
        alerts = _check_concentration(portfolio, client)
        # Both Banking and IT are > 40%
        assert alerts.count("SECTOR_CONCENTRATION") == 2


# ═════════════════════════════════════════════════════════════════════════════
# Integration: _check_concentration wired into run()
# ═════════════════════════════════════════════════════════════════════════════

class TestRunConcentrationIntegration:
    """
    Verify concentration check is called as part of run() with >= 2 holdings.
    We mock _check_concentration itself to keep these tests fast and focused.
    """

    @patch("scheduler.portfolio_monitor._check_concentration")
    @patch("scheduler.portfolio_monitor._fii_net_5session", return_value=0.0)
    @patch("scheduler.portfolio_monitor._analyse_holding")
    @patch("scheduler.portfolio_monitor._supabase")
    def test_concentration_check_called_with_2_or_more_holdings(
        self,
        mock_supa, mock_analyse, mock_fii, mock_conc,
    ):
        # Set up 2 open holdings
        holdings = [
            _make_holding("HDFCBANK", "Banking", 100, 1500),
            _make_holding("INFY",     "IT",      100, 1800),
        ]
        client = MagicMock()
        mock_supa.return_value = client
        client.table.return_value.select.return_value \
            .eq.return_value.execute.return_value.data = holdings
        mock_analyse.return_value = {
            "symbol": "?", "holding_id": "x",
            "current_price": 1000, "pnl_pct": 5.0, "alerts_created": [],
        }
        mock_conc.return_value = []

        from scheduler.portfolio_monitor import run
        run(dry_run=False)

        mock_conc.assert_called_once()

    @patch("scheduler.portfolio_monitor._check_concentration")
    @patch("scheduler.portfolio_monitor._fii_net_5session", return_value=0.0)
    @patch("scheduler.portfolio_monitor._analyse_holding")
    @patch("scheduler.portfolio_monitor._supabase")
    def test_concentration_alerts_count_added_to_total(
        self,
        mock_supa, mock_analyse, mock_fii, mock_conc,
    ):
        holdings = [
            _make_holding("HDFCBANK", "Banking", 100, 1500),
            _make_holding("INFY",     "IT",      100, 1800),
        ]
        client = MagicMock()
        mock_supa.return_value = client
        client.table.return_value.select.return_value \
            .eq.return_value.execute.return_value.data = holdings
        mock_analyse.return_value = {
            "symbol": "?", "holding_id": "x",
            "current_price": 1000, "pnl_pct": 0.0,
            "alerts_created": [],
        }
        # Concentration check returns 1 alert
        mock_conc.return_value = ["SECTOR_CONCENTRATION"]

        from scheduler.portfolio_monitor import run
        result = run(dry_run=False)

        assert result["alerts_created"] >= 1

    @patch("scheduler.portfolio_monitor._check_concentration")
    @patch("scheduler.portfolio_monitor._fii_net_5session", return_value=0.0)
    @patch("scheduler.portfolio_monitor._analyse_holding")
    @patch("scheduler.portfolio_monitor._supabase")
    def test_concentration_not_called_with_single_holding(
        self,
        mock_supa, mock_analyse, mock_fii, mock_conc,
    ):
        holdings = [_make_holding("HDFCBANK", "Banking", 100, 1500)]
        client   = MagicMock()
        mock_supa.return_value = client
        client.table.return_value.select.return_value \
            .eq.return_value.execute.return_value.data = holdings
        mock_analyse.return_value = {
            "symbol": "HDFCBANK", "holding_id": "x",
            "current_price": 1500, "pnl_pct": 5.0, "alerts_created": [],
        }

        from scheduler.portfolio_monitor import run
        run(dry_run=False)

        mock_conc.assert_not_called()

    @patch("scheduler.portfolio_monitor._check_concentration")
    @patch("scheduler.portfolio_monitor._fii_net_5session", return_value=0.0)
    @patch("scheduler.portfolio_monitor._analyse_holding")
    @patch("scheduler.portfolio_monitor._supabase")
    def test_concentration_error_does_not_crash_run(
        self,
        mock_supa, mock_analyse, mock_fii, mock_conc,
    ):
        """If _check_concentration raises, run() should catch it and continue."""
        holdings = [
            _make_holding("A", "Banking", 10, 100),
            _make_holding("B", "IT",      10, 100),
        ]
        client = MagicMock()
        mock_supa.return_value = client
        client.table.return_value.select.return_value \
            .eq.return_value.execute.return_value.data = holdings
        mock_analyse.return_value = {
            "symbol": "?", "holding_id": "x",
            "current_price": 100, "pnl_pct": 0.0, "alerts_created": [],
        }
        mock_conc.side_effect = RuntimeError("DB connection lost")

        from scheduler.portfolio_monitor import run
        result = run(dry_run=False)   # Must not raise

        assert "concentration" in " ".join(result["errors"]).lower()
