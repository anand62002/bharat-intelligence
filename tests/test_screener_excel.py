"""
tests/test_screener_excel.py
============================
Tests for _parse_screener_excel() — DB-10 screener.in Excel export fallback.

These tests create minimal in-memory Excel workbooks (using openpyxl) to verify
the parser extracts the correct data without hitting screener.in.
"""

from __future__ import annotations

import io
import pytest

# Skip entire file if openpyxl is not installed
openpyxl = pytest.importorskip("openpyxl", reason="openpyxl not installed")

from data.fetchers import _parse_screener_excel


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_workbook(sheets: dict[str, list[list]]) -> bytes:
    """Build an in-memory .xlsx file with the given sheets."""
    wb = openpyxl.Workbook()
    # Remove default sheet
    default = wb.active
    wb.remove(default)
    for sheet_name, rows in sheets.items():
        ws = wb.create_sheet(title=sheet_name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ─── Standard P&L extraction ─────────────────────────────────────────────────

class TestPnlExtraction:
    """Parser correctly reads revenue, OPM, PAT, EPS, depreciation."""

    YEARS = ["Mar 2015", "Mar 2016", "Mar 2017", "Mar 2018", "Mar 2019"]

    def _make_pnl_sheet(self):
        header = [""] + self.YEARS
        return [
            header,
            ["Sales", 1000, 1100, 1200, 1350, 1500],
            ["OPM %", 20, 21, 22, 23, 24],
            ["Net Profit", 80, 90, 100, 120, 140],
            ["EPS in Rs", 8, 9, 10, 12, 14],
            ["Depreciation", 50, 55, 60, 65, 70],
        ]

    def test_years_extracted(self):
        xlsx = _make_workbook({"Profit & Loss": self._make_pnl_sheet()})
        result = _parse_screener_excel(xlsx, "TESTCO")
        assert result is not None
        assert result["years"] == self.YEARS

    def test_revenue_extracted(self):
        xlsx = _make_workbook({"Profit & Loss": self._make_pnl_sheet()})
        result = _parse_screener_excel(xlsx, "TESTCO")
        assert result["revenue_history"] == [1000, 1100, 1200, 1350, 1500]

    def test_opm_extracted(self):
        xlsx = _make_workbook({"Profit & Loss": self._make_pnl_sheet()})
        result = _parse_screener_excel(xlsx, "TESTCO")
        assert result["ebitda_margins"] == [20, 21, 22, 23, 24]

    def test_pat_extracted(self):
        xlsx = _make_workbook({"Profit & Loss": self._make_pnl_sheet()})
        result = _parse_screener_excel(xlsx, "TESTCO")
        assert result["pat_history"] == [80, 90, 100, 120, 140]

    def test_eps_extracted(self):
        xlsx = _make_workbook({"Profit & Loss": self._make_pnl_sheet()})
        result = _parse_screener_excel(xlsx, "TESTCO")
        assert result["eps_history"] == [8, 9, 10, 12, 14]

    def test_depreciation_extracted(self):
        xlsx = _make_workbook({"Profit & Loss": self._make_pnl_sheet()})
        result = _parse_screener_excel(xlsx, "TESTCO")
        assert result["depreciation_history"] == [50, 55, 60, 65, 70]

    def test_years_available_count(self):
        xlsx = _make_workbook({"Profit & Loss": self._make_pnl_sheet()})
        result = _parse_screener_excel(xlsx, "TESTCO")
        assert result["years_available"] == 5


# ─── Ratios sheet extraction ──────────────────────────────────────────────────

class TestRatiosExtraction:
    YEARS = ["Mar 2018", "Mar 2019", "Mar 2020"]

    def _make_pnl_minimal(self):
        return [
            [""] + self.YEARS,
            ["Sales", 1000, 1100, 1200],
        ]

    def _make_ratios_sheet(self):
        return [
            [""] + self.YEARS,
            ["ROCE %", 18, 20, 22],
            ["ROE %", 15, 16, 17],
            ["Dividend Payout %", 30, 35, 40],
        ]

    def test_roce_extracted(self):
        xlsx = _make_workbook({
            "Profit & Loss": self._make_pnl_minimal(),
            "Ratios": self._make_ratios_sheet(),
        })
        result = _parse_screener_excel(xlsx, "TESTCO")
        assert result is not None
        assert result["roce_history"] == [18, 20, 22]

    def test_roe_extracted(self):
        xlsx = _make_workbook({
            "Profit & Loss": self._make_pnl_minimal(),
            "Ratios": self._make_ratios_sheet(),
        })
        result = _parse_screener_excel(xlsx, "TESTCO")
        assert result["roe_history"] == [15, 16, 17]

    def test_dividend_payout_extracted(self):
        xlsx = _make_workbook({
            "Profit & Loss": self._make_pnl_minimal(),
            "Ratios": self._make_ratios_sheet(),
        })
        result = _parse_screener_excel(xlsx, "TESTCO")
        assert result["dividend_payout_history"] == [30, 35, 40]


# ─── Cash Flow / capex extraction ─────────────────────────────────────────────

class TestCashFlowExtraction:
    YEARS = ["Mar 2018", "Mar 2019", "Mar 2020"]

    def _make_pnl_minimal(self):
        return [
            [""] + self.YEARS,
            ["Sales", 1000, 1100, 1200],
        ]

    def _make_cf_sheet(self):
        return [
            [""] + self.YEARS,
            ["Operating activities", 150, 160, 170],
            ["Investing activities (capex / fixed assets)", -100, -120, -130],
            ["Financing activities", -30, -25, -20],
        ]

    def test_capex_extracted_as_absolute(self):
        xlsx = _make_workbook({
            "Profit & Loss": self._make_pnl_minimal(),
            "Cash Flows": self._make_cf_sheet(),
        })
        result = _parse_screener_excel(xlsx, "TESTCO")
        assert result is not None
        assert result["capex_history"] == [100, 120, 130]  # absolute values


# ─── Shareholding extraction ──────────────────────────────────────────────────

class TestShareholdingExtraction:
    YEARS = ["Mar 2018", "Mar 2019"]
    QUARTERS = ["Jun 2022", "Sep 2022", "Dec 2022", "Mar 2023"]

    def _make_pnl_minimal(self):
        return [[""] + self.YEARS, ["Sales", 1000, 1100]]

    def _make_sh_sheet(self):
        return [
            [""] + self.QUARTERS,
            ["Promoters", 65, 64, 63, 62],
            ["FIIs", 10, 11, 12, 13],
            ["DIIs", 8, 8, 9, 9],
        ]

    def test_promoter_history_extracted(self):
        xlsx = _make_workbook({
            "Profit & Loss": self._make_pnl_minimal(),
            "Shareholding": self._make_sh_sheet(),
        })
        result = _parse_screener_excel(xlsx, "TESTCO")
        assert result is not None
        assert result["promoter_holding_history"] == [65, 64, 63, 62]

    def test_promoter_quarters_extracted(self):
        xlsx = _make_workbook({
            "Profit & Loss": self._make_pnl_minimal(),
            "Shareholding": self._make_sh_sheet(),
        })
        result = _parse_screener_excel(xlsx, "TESTCO")
        assert result["promoter_holding_quarters"] == self.QUARTERS


# ─── Edge cases ───────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_bytes_returns_none(self):
        result = _parse_screener_excel(b"", "TESTCO")
        assert result is None

    def test_invalid_bytes_returns_none(self):
        result = _parse_screener_excel(b"not an excel file", "TESTCO")
        assert result is None

    def test_no_year_header_returns_none(self):
        """Excel with no recognisable year headers returns None."""
        xlsx = _make_workbook({
            "Profit & Loss": [
                ["Label", "A", "B", "C"],
                ["Sales", 1, 2, 3],
            ]
        })
        result = _parse_screener_excel(xlsx, "TESTCO")
        assert result is None

    def test_missing_ratios_sheet_returns_partial(self):
        """Missing Ratios sheet is OK — returns empty lists for ROCE/ROE."""
        xlsx = _make_workbook({
            "Profit & Loss": [
                ["", "Mar 2021", "Mar 2022"],
                ["Sales", 500, 600],
                ["Net Profit", 50, 60],
            ]
        })
        result = _parse_screener_excel(xlsx, "TESTCO")
        assert result is not None
        assert result["years"] == ["Mar 2021", "Mar 2022"]
        assert result["roce_history"] == []

    def test_none_values_in_cells_handled(self):
        """Cells with None (blank) are converted to None in output lists."""
        xlsx = _make_workbook({
            "Profit & Loss": [
                ["", "Mar 2021", "Mar 2022", "Mar 2023"],
                ["Sales", 1000, None, 1200],
                ["Net Profit", 80, 90, None],
            ]
        })
        result = _parse_screener_excel(xlsx, "TESTCO")
        assert result is not None
        assert result["revenue_history"][1] is None
        assert result["pat_history"][2] is None
