"""
tests/test_data_leakage_audit.py — Unit tests for the data leakage audit
=========================================================================
Tests cover:
  TestCheckTechnicalTemporalIntegrity — future bar, stale bar, missing field, clean
  TestCheckFundamentalTemporalIntegrity — future snapshot, clean, missing field
  TestCheckRagTemporalIntegrity — future event(s), clean, missing date, mixed
  TestAuditDataLeakage — integration: all-clean, blocking violation, warnings only,
                          block_on_leak flag, empty agent results, exception safety
  TestLeakageDataclasses — LeakageViolation + DataLeakageReport field defaults

Run:
    pytest tests/test_data_leakage_audit.py -v
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from governance.performance_tracker import (
    DataLeakageReport,
    LeakageViolation,
    _check_fundamental_temporal_integrity,
    _check_rag_temporal_integrity,
    _check_technical_temporal_integrity,
    audit_data_leakage,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ts(offset_days: int = 0) -> datetime:
    """Return a UTC datetime shifted by offset_days from today."""
    base = datetime(2025, 6, 15, 6, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(days=offset_days)


def _today_str(offset_days: int = 0) -> str:
    d = date(2025, 6, 15) + timedelta(days=offset_days)
    return d.isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# TestLeakageDataclasses
# ─────────────────────────────────────────────────────────────────────────────

class TestLeakageDataclasses:
    def test_leakage_violation_fields(self):
        v = LeakageViolation(
            agent_name="technical",
            leak_type="future_ohlcv",
            details="bar dated 2025-06-16 is 1d after signal_ts 2025-06-15",
            severity="BLOCKING",
        )
        assert v.agent_name == "technical"
        assert v.leak_type == "future_ohlcv"
        assert v.severity == "BLOCKING"

    def test_data_leakage_report_defaults(self):
        ts = _ts()
        report = DataLeakageReport(symbol="TEST.NS", signal_ts=ts)
        assert report.leaks == []
        assert report.block_signal is False

    def test_data_leakage_report_with_leaks(self):
        ts = _ts()
        v = LeakageViolation("historical_rag", "future_rag_event", "...", "BLOCKING")
        report = DataLeakageReport(symbol="INFY.NS", signal_ts=ts, leaks=[v], block_signal=True)
        assert len(report.leaks) == 1
        assert report.block_signal is True


# ─────────────────────────────────────────────────────────────────────────────
# TestCheckTechnicalTemporalIntegrity
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckTechnicalTemporalIntegrity:
    def test_clean_bar_same_day(self):
        """Bar on signal day — clean, no violation."""
        result = {"ohlcv_last_date": _today_str(0)}
        viols = _check_technical_temporal_integrity("SYM", result, _ts(0))
        assert viols == []

    def test_clean_bar_one_day_before(self):
        """Bar 1 day before signal_ts (common weekday case) — clean."""
        result = {"ohlcv_last_date": _today_str(-1)}
        viols = _check_technical_temporal_integrity("SYM", result, _ts(0))
        assert viols == []

    def test_future_bar_two_days_ahead_is_blocking(self):
        """Bar 2 days after signal_ts exceeds 1-day buffer — BLOCKING."""
        result = {"ohlcv_last_date": _today_str(2)}
        viols = _check_technical_temporal_integrity("SYM", result, _ts(0))
        assert len(viols) == 1
        assert viols[0].severity == "BLOCKING"
        assert viols[0].leak_type == "future_ohlcv"

    def test_future_bar_exactly_one_day_ahead_is_clean(self):
        """Bar 1 day ahead is within the allowed buffer — no violation."""
        result = {"ohlcv_last_date": _today_str(1)}
        viols = _check_technical_temporal_integrity("SYM", result, _ts(0))
        assert viols == []

    def test_stale_bar_8_days_is_warning(self):
        """Bar 8 days old exceeds 7-day stale threshold — WARNING."""
        result = {"ohlcv_last_date": _today_str(-8)}
        viols = _check_technical_temporal_integrity("SYM", result, _ts(0))
        assert len(viols) == 1
        assert viols[0].severity == "WARNING"
        assert viols[0].leak_type == "stale_ohlcv"

    def test_stale_bar_7_days_is_clean(self):
        """Bar exactly 7 days old is within threshold — clean."""
        result = {"ohlcv_last_date": _today_str(-7)}
        viols = _check_technical_temporal_integrity("SYM", result, _ts(0))
        assert viols == []

    def test_missing_ohlcv_last_date_returns_empty(self):
        """No ohlcv_last_date field — no check, empty result."""
        result = {"signal": "BUY", "score": 70}
        viols = _check_technical_temporal_integrity("SYM", result, _ts(0))
        assert viols == []

    def test_invalid_date_string_returns_empty(self):
        """Unparseable date string — skip gracefully."""
        result = {"ohlcv_last_date": "not-a-date"}
        viols = _check_technical_temporal_integrity("SYM", result, _ts(0))
        assert viols == []

    def test_future_bar_includes_days_count_in_details(self):
        """Violation details should mention the number of days ahead."""
        result = {"ohlcv_last_date": _today_str(5)}
        viols = _check_technical_temporal_integrity("SYM", result, _ts(0))
        assert len(viols) == 1
        assert "4d" in viols[0].details or "AFTER" in viols[0].details

    def test_empty_result_dict_returns_empty(self):
        viols = _check_technical_temporal_integrity("SYM", {}, _ts(0))
        assert viols == []


# ─────────────────────────────────────────────────────────────────────────────
# TestCheckFundamentalTemporalIntegrity
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckFundamentalTemporalIntegrity:
    def test_snapshot_today_is_clean(self):
        result = {"data_as_of": _today_str(0)}
        viols = _check_fundamental_temporal_integrity("SYM", result, _ts(0))
        assert viols == []

    def test_snapshot_yesterday_is_clean(self):
        result = {"data_as_of": _today_str(-1)}
        viols = _check_fundamental_temporal_integrity("SYM", result, _ts(0))
        assert viols == []

    def test_snapshot_future_is_warning(self):
        """Snapshot dated after signal_ts — WARNING."""
        result = {"data_as_of": _today_str(1)}
        viols = _check_fundamental_temporal_integrity("SYM", result, _ts(0))
        assert len(viols) == 1
        assert viols[0].severity == "WARNING"
        assert viols[0].leak_type == "future_snapshot"

    def test_missing_data_as_of_returns_empty(self):
        result = {"signal": "BUY", "score": 65}
        viols = _check_fundamental_temporal_integrity("SYM", result, _ts(0))
        assert viols == []

    def test_invalid_date_string_returns_empty(self):
        result = {"data_as_of": "invalid"}
        viols = _check_fundamental_temporal_integrity("SYM", result, _ts(0))
        assert viols == []

    def test_empty_result_dict_returns_empty(self):
        viols = _check_fundamental_temporal_integrity("SYM", {}, _ts(0))
        assert viols == []


# ─────────────────────────────────────────────────────────────────────────────
# TestCheckRagTemporalIntegrity
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckRagTemporalIntegrity:
    def _rag(self, event_dates: list[str]) -> dict:
        return {
            "matched_events": [{"event_date": d, "description": "test"} for d in event_dates]
        }

    def test_all_past_events_is_clean(self):
        rag = self._rag([_today_str(-30), _today_str(-7), _today_str(-1)])
        viols = _check_rag_temporal_integrity("SYM", rag, _ts(0))
        assert viols == []

    def test_event_on_signal_day_is_clean(self):
        """Event on the same day as signal_ts is acceptable."""
        rag = self._rag([_today_str(0)])
        viols = _check_rag_temporal_integrity("SYM", rag, _ts(0))
        assert viols == []

    def test_future_event_one_day_ahead_is_blocking(self):
        rag = self._rag([_today_str(1)])
        viols = _check_rag_temporal_integrity("SYM", rag, _ts(0))
        assert len(viols) == 1
        assert viols[0].severity == "BLOCKING"
        assert viols[0].leak_type == "future_rag_event"

    def test_multiple_future_events_each_generates_violation(self):
        rag = self._rag([_today_str(1), _today_str(10), _today_str(-5)])
        viols = _check_rag_temporal_integrity("SYM", rag, _ts(0))
        assert len(viols) == 2
        assert all(v.severity == "BLOCKING" for v in viols)

    def test_no_matched_events_returns_empty(self):
        rag = {"matched_events": []}
        viols = _check_rag_temporal_integrity("SYM", rag, _ts(0))
        assert viols == []

    def test_matched_events_missing_date_skipped(self):
        rag = {"matched_events": [{"description": "no date field"}]}
        viols = _check_rag_temporal_integrity("SYM", rag, _ts(0))
        assert viols == []

    def test_event_with_full_iso_timestamp_parsed_correctly(self):
        """Full datetime string — only first 10 chars (date part) used."""
        rag = {"matched_events": [{"event_date": _today_str(2) + "T09:00:00Z"}]}
        viols = _check_rag_temporal_integrity("SYM", rag, _ts(0))
        assert len(viols) == 1

    def test_alt_field_name_date_used(self):
        """Falls back to 'date' key if 'event_date' missing."""
        rag = {"matched_events": [{"date": _today_str(3)}]}
        viols = _check_rag_temporal_integrity("SYM", rag, _ts(0))
        assert len(viols) == 1
        assert viols[0].severity == "BLOCKING"

    def test_empty_result_dict_returns_empty(self):
        viols = _check_rag_temporal_integrity("SYM", {}, _ts(0))
        assert viols == []


# ─────────────────────────────────────────────────────────────────────────────
# TestAuditDataLeakage
# ─────────────────────────────────────────────────────────────────────────────

class TestAuditDataLeakage:
    def _clean_agents(self) -> dict:
        return {
            "technical": {
                "signal": "BUY",
                "ohlcv_last_date": _today_str(-1),
            },
            "fundamental": {
                "signal": "BUY",
                "data_as_of": _today_str(0),
            },
            "historical_rag": {
                "signal": "BULLISH",
                "matched_events": [
                    {"event_date": _today_str(-90)},
                    {"event_date": _today_str(-30)},
                ],
            },
        }

    def test_all_clean_returns_empty_leaks(self):
        report = audit_data_leakage("SYM.NS", self._clean_agents(), signal_ts=_ts(0))
        assert isinstance(report, DataLeakageReport)
        assert report.leaks == []
        assert report.block_signal is False

    def test_blocking_technical_violation(self):
        agents = self._clean_agents()
        agents["technical"]["ohlcv_last_date"] = _today_str(5)  # 5 days future
        report = audit_data_leakage("SYM.NS", agents, signal_ts=_ts(0))
        assert len(report.leaks) >= 1
        blocking = [v for v in report.leaks if v.severity == "BLOCKING"]
        assert len(blocking) >= 1
        assert any(v.agent_name == "technical" for v in blocking)

    def test_blocking_rag_violation(self):
        agents = self._clean_agents()
        agents["historical_rag"]["matched_events"] = [{"event_date": _today_str(3)}]
        report = audit_data_leakage("SYM.NS", agents, signal_ts=_ts(0))
        blocking = [v for v in report.leaks if v.severity == "BLOCKING"]
        assert len(blocking) >= 1
        assert any(v.agent_name == "historical_rag" for v in blocking)

    def test_warning_fundamental_violation(self):
        agents = self._clean_agents()
        agents["fundamental"]["data_as_of"] = _today_str(2)
        report = audit_data_leakage("SYM.NS", agents, signal_ts=_ts(0))
        warnings = [v for v in report.leaks if v.severity == "WARNING"]
        assert len(warnings) >= 1
        assert any(v.agent_name == "fundamental" for v in warnings)

    def test_block_on_leak_false_does_not_set_block_signal(self):
        """Even with BLOCKING violation, block_signal=False when block_on_leak=False."""
        agents = self._clean_agents()
        agents["technical"]["ohlcv_last_date"] = _today_str(5)
        report = audit_data_leakage("SYM.NS", agents, signal_ts=_ts(0), block_on_leak=False)
        assert report.block_signal is False

    def test_block_on_leak_true_sets_block_signal_on_blocking(self):
        agents = self._clean_agents()
        agents["technical"]["ohlcv_last_date"] = _today_str(5)
        report = audit_data_leakage("SYM.NS", agents, signal_ts=_ts(0), block_on_leak=True)
        assert report.block_signal is True

    def test_block_on_leak_true_with_only_warnings_does_not_block(self):
        """WARNING-only violations should not trigger block_signal even when block_on_leak=True."""
        agents = self._clean_agents()
        agents["fundamental"]["data_as_of"] = _today_str(1)
        report = audit_data_leakage("SYM.NS", agents, signal_ts=_ts(0), block_on_leak=True)
        assert report.block_signal is False

    def test_missing_technical_agent_skipped(self):
        agents = {
            "fundamental": {"signal": "BUY", "data_as_of": _today_str(0)},
        }
        report = audit_data_leakage("SYM.NS", agents, signal_ts=_ts(0))
        assert report.leaks == []

    def test_missing_rag_agent_skipped(self):
        agents = {
            "technical": {"signal": "BUY", "ohlcv_last_date": _today_str(0)},
        }
        report = audit_data_leakage("SYM.NS", agents, signal_ts=_ts(0))
        assert report.leaks == []

    def test_empty_agent_results_returns_clean(self):
        report = audit_data_leakage("SYM.NS", {}, signal_ts=_ts(0))
        assert report.leaks == []
        assert report.block_signal is False

    def test_signal_ts_defaults_to_now(self):
        """When signal_ts is None, defaults to current UTC time without error."""
        report = audit_data_leakage("SYM.NS", self._clean_agents(), signal_ts=None)
        assert isinstance(report.signal_ts, datetime)
        assert report.signal_ts.tzinfo is not None

    def test_report_symbol_preserved(self):
        report = audit_data_leakage("RELIANCE.NS", self._clean_agents(), signal_ts=_ts(0))
        assert report.symbol == "RELIANCE.NS"

    def test_multiple_violations_across_agents(self):
        agents = self._clean_agents()
        agents["technical"]["ohlcv_last_date"] = _today_str(3)      # BLOCKING
        agents["fundamental"]["data_as_of"] = _today_str(1)          # WARNING
        agents["historical_rag"]["matched_events"] = [
            {"event_date": _today_str(2)},                           # BLOCKING
            {"event_date": _today_str(-5)},                          # clean
        ]
        report = audit_data_leakage("SYM.NS", agents, signal_ts=_ts(0))
        blocking = [v for v in report.leaks if v.severity == "BLOCKING"]
        warnings = [v for v in report.leaks if v.severity == "WARNING"]
        assert len(blocking) == 2
        assert len(warnings) == 1

    def test_stale_ohlcv_is_warning_not_blocking(self):
        agents = self._clean_agents()
        agents["technical"]["ohlcv_last_date"] = _today_str(-10)     # stale
        report = audit_data_leakage("SYM.NS", agents, signal_ts=_ts(0), block_on_leak=True)
        # Stale is WARNING — block_signal should stay False
        assert report.block_signal is False
        assert any(v.leak_type == "stale_ohlcv" for v in report.leaks)

    def test_none_agent_result_handled_gracefully(self):
        """None values for agent results should not raise."""
        agents = {"technical": None, "fundamental": None, "historical_rag": None}
        report = audit_data_leakage("SYM.NS", agents, signal_ts=_ts(0))
        assert report.leaks == []
