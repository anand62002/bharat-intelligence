"""
tests/test_screener_excel.py
============================
Tests for _parse_screener_excel() — DB-10 screener.in Excel export fallback.

screener.in exports a 'Data Sheet' tab with a flat row-label layout.
The visual sheets (Profit & Loss, Balance Sheet, …) use merged cells and are
not machine-readable.  These tests build minimal in-memory workbooks that mimic
the Data Sheet structure to verify the parser end-to-end.

Row layout used in test fixtures (minimal version of real Data Sheet):
  Row 1:   "PROFIT & LOSS"
  Row 2:   "Report Date"  + datetime objects for each year column
  Row 3:   "Sales"
  Row 4:   "Other Income"
  Row 5:   "Depreciation"
  Row 6:   "Interest"
  Row 7:   "Profit before tax"
  Row 8:   "Net profit"
  Row 9:   "CASH FLOW:"
  Row 10:  "Report Date"  (cash-flow section — same dates, parser uses P&L one)
  Row 11:  "Cash from Investing Activity"
  Row 12:  "DERIVED:"
  Row 13:  "Adjusted Equity Shares in Cr"
"""

from __future__ import annotations

import io
from datetime import datetime

import pytest

openpyxl = pytest.importorskip("openpyxl", reason="openpyxl not installed")

from data.fetchers import _parse_screener_excel


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_data_sheet_wb(
    report_dates: list,
    sales: list,
    pbt: list,
    interest: list,
    depreciation: list,
    net_profit: list | None = None,
    other_income: list | None = None,
    cash_investing: list | None = None,
    equity_shares: list | None = None,
) -> bytes:
    """
    Build an in-memory .xlsx workbook with a single 'Data Sheet' tab that
    mirrors the screener.in export format used by _parse_screener_excel().
    """
    n = len(report_dates)
    net_profit    = net_profit    or pbt
    other_income  = other_income  or [0] * n
    cash_investing = cash_investing or [None] * n
    equity_shares = equity_shares or [None] * n

    wb = openpyxl.Workbook()
    default = wb.active
    wb.remove(default)
    ws = wb.create_sheet(title="Data Sheet")

    ws.append(["PROFIT & LOSS"])                             # row 1 — section header
    ws.append(["Report Date"]      + report_dates)           # row 2 — year column headers
    ws.append(["Sales"]            + sales)                  # row 3
    ws.append(["Other Income"]     + other_income)           # row 4
    ws.append(["Depreciation"]     + depreciation)           # row 5
    ws.append(["Interest"]         + interest)               # row 6
    ws.append(["Profit before tax"]+ pbt)                    # row 7
    ws.append(["Net profit"]       + net_profit)             # row 8
    ws.append(["CASH FLOW:"])                                # row 9 — section header
    ws.append(["Report Date"]      + report_dates)           # row 10 — CF dates (ignored)
    ws.append(["Cash from Investing Activity"] + cash_investing)  # row 11
    ws.append(["DERIVED:"])                                  # row 12 — section header
    ws.append(["Adjusted Equity Shares in Cr"] + equity_shares)  # row 13

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _make_wb_no_data_sheet(sheets: dict[str, list[list]]) -> bytes:
    """Build a workbook with arbitrary sheets (not a Data Sheet)."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sheet_name, rows in sheets.items():
        ws = wb.create_sheet(title=sheet_name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── Shared fixtures ───────────────────────────────────────────────────────────
_DATES3 = [datetime(2021, 3, 31), datetime(2022, 3, 31), datetime(2023, 3, 31)]
_YEARS3 = ["Mar 2021", "Mar 2022", "Mar 2023"]

_DATES5 = [
    datetime(2019, 3, 31), datetime(2020, 3, 31), datetime(2021, 3, 31),
    datetime(2022, 3, 31), datetime(2023, 3, 31),
]
_YEARS5 = ["Mar 2019", "Mar 2020", "Mar 2021", "Mar 2022", "Mar 2023"]


# ─── Year extraction ──────────────────────────────────────────────────────────

class TestYearExtraction:
    """datetime Report Date values are converted to 'Mon YYYY' strings."""

    def test_three_years(self):
        xlsx = _make_data_sheet_wb(
            _DATES3, [1000, 1100, 1200], [100, 110, 120],
            [20, 22, 24], [50, 55, 60],
        )
        result = _parse_screener_excel(xlsx, "TEST")
        assert result is not None
        assert result["years"] == _YEARS3

    def test_five_years(self):
        xlsx = _make_data_sheet_wb(
            _DATES5, [1000]*5, [100]*5, [20]*5, [50]*5,
        )
        result = _parse_screener_excel(xlsx, "TEST")
        assert result["years"] == _YEARS5

    def test_years_available_count(self):
        xlsx = _make_data_sheet_wb(
            _DATES5, [1000]*5, [100]*5, [20]*5, [50]*5,
        )
        result = _parse_screener_excel(xlsx, "TEST")
        assert result["years_available"] == 5

    def test_june_quarter_dates(self):
        """Works with non-March report dates (e.g. June year-end)."""
        dates = [datetime(2021, 6, 30), datetime(2022, 6, 30)]
        xlsx = _make_data_sheet_wb(dates, [500, 600], [50, 60], [10, 12], [25, 28])
        result = _parse_screener_excel(xlsx, "TEST")
        assert result is not None
        assert result["years"] == ["Jun 2021", "Jun 2022"]


# ─── Revenue extraction ───────────────────────────────────────────────────────

class TestRevenueExtraction:
    def test_sales_extracted(self):
        xlsx = _make_data_sheet_wb(
            _DATES3, [1000, 1100, 1200], [100, 110, 120],
            [20, 22, 24], [50, 55, 60],
        )
        result = _parse_screener_excel(xlsx, "TEST")
        assert result["revenue_history"] == [1000, 1100, 1200]

    def test_none_cell_preserved(self):
        """A blank cell in Sales becomes None in the output list."""
        xlsx = _make_data_sheet_wb(
            _DATES3, [1000, None, 1200], [100, None, 120],
            [20, None, 24], [50, None, 60],
        )
        result = _parse_screener_excel(xlsx, "TEST")
        assert result["revenue_history"][1] is None


# ─── OPM computation ──────────────────────────────────────────────────────────

class TestOpmComputation:
    """
    OPM% = (PBT + Interest + Depreciation − Other Income) / Sales × 100
    """

    def test_opm_no_other_income(self):
        # PBT=100, Interest=20, Depr=50, OtherIncome=0, Sales=1000
        # OPM = (100+20+50-0)/1000*100 = 17.0
        xlsx = _make_data_sheet_wb(
            [datetime(2023, 3, 31)],
            sales=[1000], pbt=[100], interest=[20], depreciation=[50],
            other_income=[0],
        )
        result = _parse_screener_excel(xlsx, "TEST")
        assert result is not None
        assert result["ebitda_margins"] == [17.0]

    def test_opm_with_other_income(self):
        # PBT=100, Interest=20, Depr=50, OtherIncome=30, Sales=1000
        # OPM = (100+20+50-30)/1000*100 = 14.0
        xlsx = _make_data_sheet_wb(
            [datetime(2023, 3, 31)],
            sales=[1000], pbt=[100], interest=[20], depreciation=[50],
            other_income=[30],
        )
        result = _parse_screener_excel(xlsx, "TEST")
        assert result["ebitda_margins"] == [14.0]

    def test_opm_multiple_years(self):
        # Year1: (80+10+30-0)/500*100 = 24.0
        # Year2: (110+15+40-0)/600*100 = 27.5
        xlsx = _make_data_sheet_wb(
            [datetime(2022, 3, 31), datetime(2023, 3, 31)],
            sales=[500, 600], pbt=[80, 110], interest=[10, 15],
            depreciation=[30, 40], other_income=[0, 0],
        )
        result = _parse_screener_excel(xlsx, "TEST")
        assert result["ebitda_margins"] == [24.0, 27.5]

    def test_opm_none_when_missing_inputs(self):
        """If any required field is None for a year, OPM for that year is None."""
        xlsx = _make_data_sheet_wb(
            [datetime(2022, 3, 31), datetime(2023, 3, 31)],
            sales=[500, None], pbt=[80, 110], interest=[10, 15],
            depreciation=[30, 40], other_income=[0, 0],
        )
        result = _parse_screener_excel(xlsx, "TEST")
        assert result["ebitda_margins"][1] is None


# ─── PAT / Net Profit extraction ──────────────────────────────────────────────

class TestPatExtraction:
    def test_net_profit_extracted(self):
        xlsx = _make_data_sheet_wb(
            _DATES3,
            sales=[1000, 1100, 1200], pbt=[100, 110, 120],
            interest=[20, 22, 24], depreciation=[50, 55, 60],
            net_profit=[80, 90, 100],
        )
        result = _parse_screener_excel(xlsx, "TEST")
        assert result["pat_history"] == [80, 90, 100]

    def test_pat_none_preserved(self):
        xlsx = _make_data_sheet_wb(
            _DATES3,
            sales=[1000, 1100, 1200], pbt=[100, 110, 120],
            interest=[20, 22, 24], depreciation=[50, 55, 60],
            net_profit=[80, None, 100],
        )
        result = _parse_screener_excel(xlsx, "TEST")
        assert result["pat_history"][1] is None


# ─── EPS computation ──────────────────────────────────────────────────────────

class TestEpsComputation:
    """EPS = Net Profit (Cr) / Adjusted Equity Shares (Cr)."""

    def test_eps_computed(self):
        # Net profit=80 Cr, Shares=4 Cr → EPS = 80/4 = 20.0
        xlsx = _make_data_sheet_wb(
            [datetime(2023, 3, 31)],
            sales=[1000], pbt=[100], interest=[20], depreciation=[50],
            net_profit=[80], equity_shares=[4],
        )
        result = _parse_screener_excel(xlsx, "TEST")
        assert result["eps_history"] == [20.0]

    def test_eps_multiple_years(self):
        xlsx = _make_data_sheet_wb(
            [datetime(2022, 3, 31), datetime(2023, 3, 31)],
            sales=[800, 1000], pbt=[80, 100], interest=[15, 20],
            depreciation=[40, 50],
            net_profit=[60, 80], equity_shares=[4, 4],
        )
        result = _parse_screener_excel(xlsx, "TEST")
        assert result["eps_history"] == [15.0, 20.0]

    def test_eps_empty_when_no_equity_shares(self):
        """If equity shares not provided, eps_history is an empty list."""
        xlsx = _make_data_sheet_wb(
            _DATES3,
            sales=[1000, 1100, 1200], pbt=[100, 110, 120],
            interest=[20, 22, 24], depreciation=[50, 55, 60],
            net_profit=[80, 90, 100],
            # equity_shares not provided → defaults to [None, None, None]
        )
        result = _parse_screener_excel(xlsx, "TEST")
        # None equity_shares → EPS cannot be computed → empty or all-None
        assert result["eps_history"] == [] or all(v is None for v in result["eps_history"])


# ─── Depreciation extraction ──────────────────────────────────────────────────

class TestDepreciationExtraction:
    def test_depreciation_extracted(self):
        xlsx = _make_data_sheet_wb(
            _DATES3, [1000, 1100, 1200], [100, 110, 120],
            [20, 22, 24], [50, 55, 60],
        )
        result = _parse_screener_excel(xlsx, "TEST")
        assert result["depreciation_history"] == [50, 55, 60]


# ─── Capex extraction (Cash from Investing Activity) ─────────────────────────

class TestCapexExtraction:
    def test_capex_extracted_as_absolute(self):
        """Cash from Investing Activity is negative; capex is stored as absolute."""
        xlsx = _make_data_sheet_wb(
            _DATES3, [1000, 1100, 1200], [100, 110, 120],
            [20, 22, 24], [50, 55, 60],
            cash_investing=[-80, -90, -100],
        )
        result = _parse_screener_excel(xlsx, "TEST")
        assert result["capex_history"] == [80, 90, 100]

    def test_capex_positive_input_also_abs(self):
        """Parser uses abs() regardless of sign."""
        xlsx = _make_data_sheet_wb(
            _DATES3, [1000, 1100, 1200], [100, 110, 120],
            [20, 22, 24], [50, 55, 60],
            cash_investing=[80, -90, 100],
        )
        result = _parse_screener_excel(xlsx, "TEST")
        assert result["capex_history"] == [80, 90, 100]

    def test_capex_none_when_not_provided(self):
        xlsx = _make_data_sheet_wb(
            _DATES3, [1000, 1100, 1200], [100, 110, 120],
            [20, 22, 24], [50, 55, 60],
        )
        result = _parse_screener_excel(xlsx, "TEST")
        # cash_investing defaults to [None, None, None]
        assert all(v is None for v in result["capex_history"])


# ─── Fields not in export ─────────────────────────────────────────────────────

class TestMissingFields:
    """ROCE, ROE, Promoter Holding are absent from the export — always empty."""

    def _base_result(self):
        xlsx = _make_data_sheet_wb(
            _DATES3, [1000, 1100, 1200], [100, 110, 120],
            [20, 22, 24], [50, 55, 60],
        )
        return _parse_screener_excel(xlsx, "TEST")

    def test_roce_empty(self):
        assert self._base_result()["roce_history"] == []

    def test_roe_empty(self):
        assert self._base_result()["roe_history"] == []

    def test_dividend_payout_empty(self):
        assert self._base_result()["dividend_payout_history"] == []

    def test_promoter_holding_empty(self):
        assert self._base_result()["promoter_holding_history"] == []

    def test_promoter_quarters_empty(self):
        assert self._base_result()["promoter_holding_quarters"] == []


# ─── Edge cases ───────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_bytes_returns_none(self):
        assert _parse_screener_excel(b"", "TEST") is None

    def test_invalid_bytes_returns_none(self):
        assert _parse_screener_excel(b"not an excel file", "TEST") is None

    def test_no_data_sheet_returns_none(self):
        """Workbook with only visual sheets (no 'Data Sheet') returns None."""
        xlsx = _make_wb_no_data_sheet({
            "Profit & Loss": [["", "Mar 2021"], ["Sales", 1000]],
        })
        assert _parse_screener_excel(xlsx, "TEST") is None

    def test_no_report_date_row_returns_none(self):
        """Data Sheet without a 'Report Date' row returns None."""
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        ws = wb.create_sheet(title="Data Sheet")
        ws.append(["PROFIT & LOSS"])
        ws.append(["Sales", 1000, 1100])   # no Report Date row
        buf = io.BytesIO()
        wb.save(buf)
        assert _parse_screener_excel(buf.getvalue(), "TEST") is None

    def test_empty_data_sheet_returns_none(self):
        """A completely empty Data Sheet tab returns None."""
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        wb.create_sheet(title="Data Sheet")   # no rows written
        buf = io.BytesIO()
        wb.save(buf)
        assert _parse_screener_excel(buf.getvalue(), "TEST") is None

    def test_result_has_all_expected_keys(self):
        """Return dict contains all keys expected by warren_bot and other agents."""
        xlsx = _make_data_sheet_wb(
            _DATES3, [1000, 1100, 1200], [100, 110, 120],
            [20, 22, 24], [50, 55, 60],
        )
        result = _parse_screener_excel(xlsx, "TEST")
        assert result is not None
        expected_keys = {
            "years", "revenue_history", "ebitda_margins", "pat_history",
            "eps_history", "depreciation_history", "capex_history",
            "roce_history", "roe_history", "dividend_payout_history",
            "promoter_holding_history", "promoter_holding_quarters",
            "years_available",
        }
        assert expected_keys.issubset(result.keys())

    def test_none_cells_preserved(self):
        """Blank cells in Sales and Net profit are preserved as None."""
        xlsx = _make_data_sheet_wb(
            _DATES3,
            sales=[1000, None, 1200], pbt=[100, None, 120],
            interest=[20, None, 24], depreciation=[50, None, 60],
            net_profit=[80, None, 100],
        )
        result = _parse_screener_excel(xlsx, "TEST")
        assert result is not None
        assert result["revenue_history"][1] is None
        assert result["pat_history"][1] is None
