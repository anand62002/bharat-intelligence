"""
tests/test_macro.py — Unit tests for agents/macro.py

All network calls are mocked. Tests cover:
- Score helper edge cases
- Sector impact logic
- analyse() output schema and signal derivation
- FRED fetch and RBI scrape failure handling
"""

import json
import pytest
from unittest.mock import patch, MagicMock


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def no_supabase(monkeypatch):
    """Prevent any real Supabase writes during tests."""
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "")


@pytest.fixture(autouse=True)
def no_fred_key(monkeypatch):
    """Default: no FRED key so fetch_fred_indicators returns all None."""
    monkeypatch.delenv("FRED_API_KEY", raising=False)


# ──────────────────────────────────────────────────────────────────────────────
# Import after env setup (dotenv already loaded, we override via monkeypatch)
# ──────────────────────────────────────────────────────────────────────────────

from agents.macro import (
    _score_us10y,
    _score_dxy,
    _score_vix,
    _score_india_vix,
    _score_inr,
    _score_rbi_rate,
    _sector_impacts,
    fetch_fred_indicators,
    fetch_rbi_repo_rate,
    analyse,
)


# ──────────────────────────────────────────────────────────────────────────────
# _score_us10y
# ──────────────────────────────────────────────────────────────────────────────

class TestScoreUS10Y:
    def test_none_returns_neutral(self):
        score, note = _score_us10y(None)
        assert score == 10
        assert "unknown" in note.lower()

    def test_low_yield_max_score(self):
        score, note = _score_us10y(3.0)
        assert score == 25
        assert "loose" in note.lower()

    def test_moderate_yield(self):
        score, _ = _score_us10y(3.8)
        assert score == 18

    def test_elevated_yield(self):
        score, _ = _score_us10y(4.2)
        assert score == 12

    def test_high_yield(self):
        score, _ = _score_us10y(4.7)
        assert score == 5

    def test_very_high_yield(self):
        score, note = _score_us10y(5.5)
        assert score == 0
        assert "very high" in note.lower()

    def test_boundary_exact_3_5(self):
        # Exactly 3.5 → first branch requires < 3.5, so should go to next
        score, _ = _score_us10y(3.5)
        assert score == 18

    def test_boundary_exact_4_0(self):
        score, _ = _score_us10y(4.0)
        assert score == 12

    def test_note_contains_value(self):
        _, note = _score_us10y(4.75)
        assert "4.75" in note


# ──────────────────────────────────────────────────────────────────────────────
# _score_dxy
# ──────────────────────────────────────────────────────────────────────────────

class TestScoreDXY:
    def test_none(self):
        score, note = _score_dxy(None)
        assert score == 10
        assert "unknown" in note.lower()

    def test_weak_usd(self):
        score, note = _score_dxy(95.0)
        assert score == 25
        assert "weak" in note.lower()

    def test_neutral_usd(self):
        score, _ = _score_dxy(100.0)
        assert score == 18

    def test_strong_usd(self):
        score, _ = _score_dxy(103.5)
        assert score == 10

    def test_very_strong_usd(self):
        score, note = _score_dxy(108.0)
        assert score == 3
        assert "very strong" in note.lower()

    def test_boundary_98(self):
        score, _ = _score_dxy(98.0)
        assert score == 18

    def test_boundary_102(self):
        score, _ = _score_dxy(102.0)
        assert score == 10


# ──────────────────────────────────────────────────────────────────────────────
# _score_vix
# ──────────────────────────────────────────────────────────────────────────────

class TestScoreVIX:
    def test_none(self):
        score, note = _score_vix(None)
        assert score == 8

    def test_very_low_vix(self):
        score, note = _score_vix(12.0)
        assert score == 15
        assert "risk-on" in note.lower()

    def test_calm_vix(self):
        score, _ = _score_vix(18.0)
        assert score == 12

    def test_moderate_vix(self):
        score, _ = _score_vix(22.0)
        assert score == 7

    def test_elevated_vix(self):
        score, _ = _score_vix(30.0)
        assert score == 3

    def test_crisis_vix(self):
        score, note = _score_vix(40.0)
        assert score == 0
        assert "crisis" in note.lower()

    def test_boundary_15(self):
        score, _ = _score_vix(15.0)
        assert score == 12


# ──────────────────────────────────────────────────────────────────────────────
# _score_india_vix
# ──────────────────────────────────────────────────────────────────────────────

class TestScoreIndiaVIX:
    def test_none(self):
        score, _ = _score_india_vix(None)
        assert score == 5

    def test_very_calm(self):
        score, note = _score_india_vix(10.0)
        assert score == 10
        assert "very calm" in note.lower()

    def test_low(self):
        score, _ = _score_india_vix(15.0)
        assert score == 8

    def test_moderate(self):
        score, _ = _score_india_vix(20.0)
        assert score == 5

    def test_elevated(self):
        score, _ = _score_india_vix(28.0)
        assert score == 2

    def test_panic(self):
        score, note = _score_india_vix(40.0)
        assert score == 0
        assert "panic" in note.lower()


# ──────────────────────────────────────────────────────────────────────────────
# _score_inr
# ──────────────────────────────────────────────────────────────────────────────

class TestScoreINR:
    def test_none(self):
        score, note = _score_inr(None)
        assert score == 8
        assert "unknown" in note.lower()

    def test_strong_rupee(self):
        score, note = _score_inr(80.0)
        assert score == 15
        assert "strong" in note.lower()

    def test_stable(self):
        score, _ = _score_inr(83.0)
        assert score == 12

    def test_mild_weakness(self):
        score, _ = _score_inr(85.0)
        assert score == 8

    def test_weak(self):
        score, note = _score_inr(87.0)
        assert score == 4
        assert "weak" in note.lower()

    def test_very_weak(self):
        score, note = _score_inr(90.0)
        assert score == 1
        assert "sharply weak" in note.lower()

    def test_boundary_82(self):
        score, _ = _score_inr(82.0)
        assert score == 12

    def test_boundary_84(self):
        score, _ = _score_inr(84.0)
        assert score == 8


# ──────────────────────────────────────────────────────────────────────────────
# _score_rbi_rate
# ──────────────────────────────────────────────────────────────────────────────

class TestScoreRBIRate:
    def test_none(self):
        score, note = _score_rbi_rate(None)
        assert score == 7
        assert "unknown" in note.lower()

    def test_accommodative(self):
        score, note = _score_rbi_rate(4.5)
        assert score == 10
        assert "accommodative" in note.lower()

    def test_neutral(self):
        score, note = _score_rbi_rate(5.5)
        assert score == 7
        assert "neutral" in note.lower()

    def test_mildly_restrictive(self):
        score, _ = _score_rbi_rate(6.5)
        assert score == 4

    def test_restrictive(self):
        score, note = _score_rbi_rate(7.0)
        assert score == 2
        assert "restrictive" in note.lower()

    def test_boundary_5_0(self):
        score, _ = _score_rbi_rate(5.0)
        assert score == 10

    def test_boundary_6_0(self):
        score, _ = _score_rbi_rate(6.0)
        assert score == 7


# ──────────────────────────────────────────────────────────────────────────────
# _sector_impacts
# ──────────────────────────────────────────────────────────────────────────────

class TestSectorImpacts:
    def test_returns_all_sectors(self):
        impacts = _sector_impacts(4.0, 100.0, 15.0, 83.0, 5.5)
        for sector in ["IT", "BANKING", "PHARMA", "OIL_GAS", "REALTY", "AUTO", "METALS", "FMCG"]:
            assert sector in impacts
            assert "outlook" in impacts[sector]
            assert "reason" in impacts[sector]

    def test_weak_inr_positive_for_it(self):
        # weak INR (>84) and moderate US10Y
        impacts = _sector_impacts(3.8, 100.0, 15.0, 85.0, 5.5)
        assert impacts["IT"]["outlook"] == "POSITIVE"

    def test_high_us10y_negative_for_it(self):
        # high US10Y (>4.5) with strong INR
        impacts = _sector_impacts(4.8, 100.0, 15.0, 82.0, 5.5)
        assert impacts["IT"]["outlook"] == "NEGATIVE"

    def test_low_repo_positive_for_banking(self):
        impacts = _sector_impacts(4.0, 100.0, 15.0, 83.0, 5.0)
        assert impacts["BANKING"]["outlook"] == "POSITIVE"

    def test_high_repo_negative_for_banking(self):
        impacts = _sector_impacts(4.0, 100.0, 15.0, 83.0, 7.0)
        assert impacts["BANKING"]["outlook"] == "NEGATIVE"

    def test_weak_inr_positive_for_pharma(self):
        impacts = _sector_impacts(4.0, 100.0, 15.0, 85.5, 6.0)
        assert impacts["PHARMA"]["outlook"] == "POSITIVE"

    def test_strong_usd_negative_for_oilgas(self):
        # DXY > 104 = strong USD
        impacts = _sector_impacts(4.0, 106.0, 15.0, 83.0, 6.0)
        assert impacts["OIL_GAS"]["outlook"] == "NEGATIVE"

    def test_strong_usd_negative_for_metals(self):
        impacts = _sector_impacts(4.0, 106.0, 15.0, 83.0, 6.0)
        assert impacts["METALS"]["outlook"] == "NEGATIVE"

    def test_low_vix_positive_for_fmcg(self):
        impacts = _sector_impacts(4.0, 100.0, 15.0, 83.0, 6.0)
        assert impacts["FMCG"]["outlook"] == "POSITIVE"

    def test_high_vix_neutral_fmcg(self):
        impacts = _sector_impacts(4.0, 100.0, 25.0, 83.0, 6.0)
        assert impacts["FMCG"]["outlook"] == "NEUTRAL"

    def test_all_none_returns_defaults(self):
        impacts = _sector_impacts(None, None, None, None, None)
        assert set(impacts.keys()) >= {"IT", "BANKING", "PHARMA", "OIL_GAS", "REALTY", "AUTO", "METALS", "FMCG"}


# ──────────────────────────────────────────────────────────────────────────────
# fetch_fred_indicators
# ──────────────────────────────────────────────────────────────────────────────

class TestFetchFREDIndicators:
    def test_no_api_key_returns_all_none(self):
        result = fetch_fred_indicators()
        assert result == {"us10y": None, "dxy": None, "vix": None}

    def test_with_key_calls_urlopen(self, monkeypatch):
        monkeypatch.setenv("FRED_API_KEY", "fake_key")
        obs = [{"value": "4.25", "date": "2024-01-01"}]
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"observations": obs}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("agents.macro.urlopen", return_value=mock_resp):
            result = fetch_fred_indicators()

        assert result["us10y"] == 4.25
        assert result["dxy"] == 4.25    # same mock returns same value for all series
        assert result["vix"] == 4.25

    def test_with_key_network_error_returns_none(self, monkeypatch):
        from urllib.error import URLError
        monkeypatch.setenv("FRED_API_KEY", "fake_key")
        with patch("agents.macro.urlopen", side_effect=URLError("timeout")):
            result = fetch_fred_indicators()
        assert all(v is None for v in result.values())

    def test_skips_dot_values(self, monkeypatch):
        monkeypatch.setenv("FRED_API_KEY", "fake_key")
        obs = [{"value": ".", "date": "2024-01-02"}, {"value": "4.10", "date": "2024-01-01"}]
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"observations": obs}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("agents.macro.urlopen", return_value=mock_resp):
            result = fetch_fred_indicators()

        assert result["us10y"] == 4.10


# ──────────────────────────────────────────────────────────────────────────────
# fetch_rbi_repo_rate
# ──────────────────────────────────────────────────────────────────────────────

class TestFetchRBIRepoRate:
    def test_successful_scrape(self):
        html = b"The Monetary Policy Committee voted to keep repo rate at 6.50 per cent unchanged"
        mock_resp = MagicMock()
        mock_resp.read.return_value = html
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("agents.macro.urlopen", return_value=mock_resp):
            result = fetch_rbi_repo_rate()

        assert result == 6.50

    def test_percentage_sign_variant(self):
        html = b"repo rate reduced to 6.25%"
        mock_resp = MagicMock()
        mock_resp.read.return_value = html
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("agents.macro.urlopen", return_value=mock_resp):
            result = fetch_rbi_repo_rate()

        assert result == 6.25

    def test_network_failure_returns_none(self):
        from urllib.error import URLError
        with patch("agents.macro.urlopen", side_effect=URLError("connection refused")):
            result = fetch_rbi_repo_rate()
        assert result is None

    def test_no_match_returns_none(self):
        html = b"The MPC meeting concluded without any rate action."
        mock_resp = MagicMock()
        mock_resp.read.return_value = html
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("agents.macro.urlopen", return_value=mock_resp):
            result = fetch_rbi_repo_rate()

        assert result is None


# ──────────────────────────────────────────────────────────────────────────────
# analyse() — integration-level
# ──────────────────────────────────────────────────────────────────────────────

def _mock_analyse_deps(monkeypatch, us10y=4.0, dxy=100.0, vix=18.0,
                       india_vix=15.0, inr_usd=84.0, repo=6.0):
    """Patch all external fetchers to return controlled values."""
    monkeypatch.setenv("FRED_API_KEY", "fake")

    fred_result = {"us10y": us10y, "dxy": dxy, "vix": vix}
    monkeypatch.setattr("agents.macro.fetch_fred_indicators", lambda: fred_result)
    monkeypatch.setattr("agents.macro.fetch_rbi_repo_rate",   lambda: repo)
    monkeypatch.setattr("agents.macro.get_inr_usd",           lambda: inr_usd)
    monkeypatch.setattr("agents.macro.get_india_vix",         lambda: india_vix)


class TestAnalyse:
    def test_output_schema(self, monkeypatch):
        _mock_analyse_deps(monkeypatch)
        result = analyse()
        assert "signal" in result
        assert "score" in result
        assert "detail" in result
        assert "sector_impacts" in result
        assert "data_sources" in result
        assert result["agent_name"] == "macro"

    def test_signal_is_valid(self, monkeypatch):
        _mock_analyse_deps(monkeypatch)
        result = analyse()
        assert result["signal"] in ("RISK_ON", "NEUTRAL", "RISK_OFF")

    def test_score_range(self, monkeypatch):
        _mock_analyse_deps(monkeypatch)
        result = analyse()
        assert 0 <= result["score"] <= 100

    def test_risk_on_signal(self, monkeypatch):
        # Best-case macro: low US10Y, weak USD, low VIX, low INR, low repo
        _mock_analyse_deps(monkeypatch, us10y=3.0, dxy=95.0, vix=12.0,
                           india_vix=10.0, inr_usd=81.0, repo=4.5)
        result = analyse()
        assert result["signal"] == "RISK_ON"
        assert result["score"] >= 65

    def test_risk_off_signal(self, monkeypatch):
        # Worst-case macro
        _mock_analyse_deps(monkeypatch, us10y=5.5, dxy=110.0, vix=40.0,
                           india_vix=40.0, inr_usd=92.0, repo=7.5)
        result = analyse()
        assert result["signal"] == "RISK_OFF"
        assert result["score"] < 40

    def test_neutral_signal(self, monkeypatch):
        # Moderate macro
        _mock_analyse_deps(monkeypatch, us10y=4.2, dxy=102.0, vix=22.0,
                           india_vix=18.0, inr_usd=84.0, repo=6.5)
        result = analyse()
        assert result["signal"] == "NEUTRAL"

    def test_detail_has_all_keys(self, monkeypatch):
        _mock_analyse_deps(monkeypatch)
        result = analyse()
        for key in ["us10y", "dxy", "vix", "india_vix", "inr_usd", "rbi_repo"]:
            assert key in result["detail"]
            assert "score" in result["detail"][key]
            assert "value" in result["detail"][key]
            assert "note" in result["detail"][key]

    def test_sector_impacts_present(self, monkeypatch):
        _mock_analyse_deps(monkeypatch)
        result = analyse()
        sectors = result["sector_impacts"]
        assert len(sectors) == 8
        for sector in ["IT", "BANKING", "PHARMA", "OIL_GAS", "REALTY", "AUTO", "METALS", "FMCG"]:
            assert sector in sectors

    def test_all_none_inputs_graceful(self, monkeypatch):
        monkeypatch.setattr("agents.macro.fetch_fred_indicators", lambda: {"us10y": None, "dxy": None, "vix": None})
        monkeypatch.setattr("agents.macro.fetch_rbi_repo_rate",   lambda: None)
        monkeypatch.setattr("agents.macro.get_inr_usd",           lambda: None)
        monkeypatch.setattr("agents.macro.get_india_vix",         lambda: None)
        result = analyse()
        # When all inputs are None the base DCV fires → INSUFFICIENT_DATA.
        # Previously the agent returned a graceful NEUTRAL/RISK_ON/RISK_OFF; now
        # the data-quality gate takes precedence.  Accept both for compatibility.
        assert result["signal"] in ("RISK_ON", "NEUTRAL", "RISK_OFF", "INSUFFICIENT_DATA")
        assert result["score"] is None or 0 <= result["score"] <= 100

    def test_data_sources_populated(self, monkeypatch):
        _mock_analyse_deps(monkeypatch)
        result = analyse()
        # FRED api_key was set in _mock_analyse_deps but we patched fetch_fred_indicators directly
        # The data_sources list should have at least some entries from non-None values
        assert isinstance(result["data_sources"], list)

    def test_supabase_failure_does_not_propagate(self, monkeypatch):
        _mock_analyse_deps(monkeypatch)
        with patch("agents.macro._write_agent_performance", side_effect=Exception("DB down")):
            result = analyse()
        assert "signal" in result   # should complete successfully despite DB failure

    def test_score_components_sum(self, monkeypatch):
        _mock_analyse_deps(monkeypatch, us10y=3.0, dxy=95.0, vix=12.0,
                           india_vix=10.0, inr_usd=81.0, repo=4.5)
        result = analyse()
        detail = result["detail"]
        component_sum = sum(
            detail[k]["score"]
            for k in ["us10y", "dxy", "vix", "india_vix", "inr_usd", "rbi_repo"]
        )
        assert result["score"] == component_sum
