"""
tests/test_governance_screener.py
===================================
Unit tests for agents/governance_screener.py

Tests validate all 7 flags + risk_score_delta + adjust_risk_score.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.governance_screener import (
    screen_governance,
    adjust_risk_score,
    _flag_pledging,
    _flag_leverage,
    _flag_related_party,
    _flag_promoter_selling,
    _flag_negative_cfo,
    _flag_contingent_liab,
    _flag_auditor_change,
    _classify_risk,
)


# ──────────────────────────────────────────────────────────────────────────────
# Individual flag detectors
# ──────────────────────────────────────────────────────────────────────────────

class TestFlagPledging:
    def test_critical_above_40(self):
        f = _flag_pledging({"promoter_pledging": 45.0})
        assert f is not None and f["level"] == "CRITICAL"

    def test_high_between_20_40(self):
        f = _flag_pledging({"promoter_pledging": 25.0})
        assert f is not None and f["level"] == "HIGH"

    def test_no_flag_below_20(self):
        assert _flag_pledging({"promoter_pledging": 10.0}) is None

    def test_no_flag_zero(self):
        assert _flag_pledging({"promoter_pledging": 0.0}) is None

    def test_no_flag_absent(self):
        assert _flag_pledging({}) is None


class TestFlagLeverage:
    def test_critical_above_5(self):
        f = _flag_leverage({"debt_equity": 6.0})
        assert f is not None and f["level"] == "CRITICAL"

    def test_high_between_3_5(self):
        f = _flag_leverage({"debt_equity": 4.0})
        assert f is not None and f["level"] == "HIGH"

    def test_no_flag_below_3(self):
        assert _flag_leverage({"debt_equity": 2.9}) is None

    def test_no_flag_absent(self):
        assert _flag_leverage({}) is None


class TestFlagRelatedParty:
    def test_critical_above_25(self):
        f = _flag_related_party({"related_party_pct": 30.0})
        assert f is not None and f["level"] == "CRITICAL"

    def test_high_between_15_25(self):
        f = _flag_related_party({"related_party_pct": 20.0})
        assert f is not None and f["level"] == "HIGH"

    def test_no_flag_below_15(self):
        assert _flag_related_party({"related_party_pct": 10.0}) is None

    def test_no_flag_absent(self):
        assert _flag_related_party({}) is None


class TestFlagPromoterSelling:
    def test_critical_drop_above_10pp(self):
        f = _flag_promoter_selling({"promoter_holding": [65, 60, 55, 50]})
        assert f is not None and f["level"] == "CRITICAL"

    def test_high_drop_5_to_10pp(self):
        f = _flag_promoter_selling({"promoter_holding": [65, 63, 61, 59]})
        assert f is not None and f["level"] in ("HIGH", "CRITICAL")

    def test_no_flag_stable(self):
        assert _flag_promoter_selling({"promoter_holding": [65, 65, 66, 65]}) is None

    def test_no_flag_increasing(self):
        assert _flag_promoter_selling({"promoter_holding": [60, 61, 62, 63]}) is None

    def test_no_flag_insufficient_data(self):
        assert _flag_promoter_selling({"promoter_holding": [60]}) is None


class TestFlagNegativeCFO:
    def test_critical_3_of_3_negative(self):
        history = {"cfo": [-100, -50, -80]}
        f = _flag_negative_cfo(history, {})
        assert f is not None and f["level"] == "CRITICAL"

    def test_high_2_of_3_negative(self):
        history = {"cfo": [50, -100, -80]}
        f = _flag_negative_cfo(history, {})
        assert f is not None and f["level"] == "HIGH"

    def test_no_flag_positive_cfo(self):
        history = {"cfo": [100, 120, 140]}
        assert _flag_negative_cfo(history, {}) is None

    def test_proxy_via_pat_capex(self):
        """Falls back to PAT - capex when direct CFO not available."""
        history = {
            "pat":   [50, 40, 30],
            "capex": [200, 200, 200],
        }
        f = _flag_negative_cfo(history, {})
        assert f is not None   # PAT - capex < 0 all three years


class TestFlagContingentLiab:
    def test_critical_above_networth(self):
        f = _flag_contingent_liab({"contingent_liabilities": 150, "net_worth": 100})
        assert f is not None and f["level"] == "CRITICAL"

    def test_high_50_to_100_pct(self):
        f = _flag_contingent_liab({"contingent_liabilities": 70, "net_worth": 100})
        assert f is not None and f["level"] == "HIGH"

    def test_no_flag_below_50_pct(self):
        assert _flag_contingent_liab({"contingent_liabilities": 40, "net_worth": 100}) is None

    def test_no_flag_missing(self):
        assert _flag_contingent_liab({}) is None


class TestFlagAuditorChange:
    def test_medium_when_changed(self):
        f = _flag_auditor_change({"auditor_changed": True})
        assert f is not None and f["level"] == "MEDIUM"

    def test_no_flag_not_changed(self):
        assert _flag_auditor_change({"auditor_changed": False}) is None

    def test_no_flag_absent(self):
        assert _flag_auditor_change({}) is None


# ──────────────────────────────────────────────────────────────────────────────
# Risk level classification
# ──────────────────────────────────────────────────────────────────────────────

class TestClassifyRisk:
    def test_high_risk(self):
        assert _classify_risk(35) == "HIGH_RISK"

    def test_moderate_risk(self):
        assert _classify_risk(20) == "MODERATE_RISK"

    def test_low_risk(self):
        assert _classify_risk(5) == "LOW_RISK"

    def test_clean(self):
        assert _classify_risk(0) == "CLEAN"


# ──────────────────────────────────────────────────────────────────────────────
# adjust_risk_score
# ──────────────────────────────────────────────────────────────────────────────

class TestAdjustRiskScore:
    def test_adds_delta(self):
        gov = {"risk_score_delta": 15}
        assert adjust_risk_score(40.0, gov) == pytest.approx(55.0)

    def test_capped_at_100(self):
        gov = {"risk_score_delta": 60}
        assert adjust_risk_score(80.0, gov) == pytest.approx(100.0)

    def test_zero_delta_unchanged(self):
        gov = {"risk_score_delta": 0}
        assert adjust_risk_score(50.0, gov) == pytest.approx(50.0)


# ──────────────────────────────────────────────────────────────────────────────
# Integration: screen_governance with mocked data
# ──────────────────────────────────────────────────────────────────────────────

class TestScreenGovernance:
    def _run(self, raw, history):
        with patch("data.fetchers.get_screener_data", return_value=raw):
            with patch("data.fetchers.get_screener_history", return_value=history):
                return screen_governance("TESTCO")

    def test_clean_company_no_flags(self):
        raw = {"promoter_pledging": 0.0, "debt_equity": 0.3}
        hist = {"promoter_holding": [65, 66, 67, 68]}
        r = self._run(raw, hist)
        assert r["clean"] is True
        assert r["flag_count"] == 0
        assert r["risk_score_delta"] == 0

    def test_multiple_flags_accumulate_delta(self):
        raw = {
            "promoter_pledging": 45.0,   # CRITICAL +20
            "debt_equity": 4.0,           # HIGH +10
        }
        hist = {"promoter_holding": [60, 55, 50, 45]}  # CRITICAL +20
        r = self._run(raw, hist)
        assert r["flag_count"] >= 2
        assert r["risk_score_delta"] >= 30

    def test_delta_capped_at_60(self):
        raw = {
            "promoter_pledging": 50.0,
            "debt_equity": 6.0,
            "contingent_liabilities": 200, "net_worth": 100,
            "related_party_pct": 30.0,
        }
        hist = {
            "promoter_holding": [60, 55, 50, 45],
            "cfo": [-100, -100, -100],
        }
        r = self._run(raw, hist)
        assert r["risk_score_delta"] <= 60

    def test_result_structure(self):
        r = self._run({}, {})
        for key in ("symbol", "flags", "flag_count", "risk_score_delta",
                    "risk_level", "clean", "agent_name"):
            assert key in r

    def test_symbol_normalised(self):
        raw = {}
        hist = {}
        with patch("data.fetchers.get_screener_data", return_value=raw):
            with patch("data.fetchers.get_screener_history", return_value=hist):
                r = screen_governance("RELIANCE.NS")
        assert r["symbol"] == "RELIANCE"

    def test_fetch_failure_returns_clean(self):
        with patch("data.fetchers.get_screener_data", side_effect=Exception("down")):
            with patch("data.fetchers.get_screener_history", side_effect=Exception("down")):
                r = screen_governance("CRASH")
        assert r["clean"] is True   # graceful failure → no flags

    def test_never_raises(self):
        with patch("data.fetchers.get_screener_data", side_effect=Exception("boom")):
            with patch("data.fetchers.get_screener_history", side_effect=Exception("boom")):
                try:
                    r = screen_governance("ANY")
                except Exception as exc:
                    pytest.fail(f"screen_governance raised: {exc}")
        assert r is not None
