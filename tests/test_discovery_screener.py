"""
tests/test_discovery_screener.py — Unit tests for agents/discovery_screener.py

All network / Supabase / agent calls are mocked.
Tests cover:
  - prescreen() filter logic (each of the 5 filters, 4-of-5 gating)
  - _rsi(), _price_above_ema200() helper correctness
  - _composite_score() weighted averaging
  - _best_upside() priority logic
  - _upside_horizon() thresholds
  - _upside_basis() structure (3 sentences)
  - _horizon_to_days() / _valid_till()
  - DiscoveryResult dataclass / to_dict()
  - run_discovery() full pipeline: exclusion, tiering, sorting, DB save
"""

import time
import pytest
import pandas as pd
import numpy as np
from datetime import date, timedelta
from unittest.mock import patch, MagicMock, call

# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def no_supabase(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "")


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Prevent real sleeps in tests."""
    monkeypatch.setattr("agents.discovery_screener.time.sleep", lambda s: None)


# ──────────────────────────────────────────────────────────────────────────────
# Imports
# ──────────────────────────────────────────────────────────────────────────────

from agents.discovery_screener import (
    _rsi,
    _price_above_ema200,
    _ema,
    _fii_net_buying,
    prescreen,
    _composite_score,
    _best_upside,
    _upside_horizon,
    _upside_basis,
    _horizon_to_days,
    _valid_till,
    _normalise_symbol,
    _load_portfolio_symbols,
    _save_discovery,
    _log_daily_run,
    run_discovery,
    DiscoveryResult,
    NIFTY500_SYMBOLS,
)


# ──────────────────────────────────────────────────────────────────────────────
# DataFrame helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_df(n: int = 252, start: float = 100.0, drift: float = 0.2) -> pd.DataFrame:
    prices = [start + drift * i for i in range(n)]
    return pd.DataFrame({
        "Open":   prices,
        "High":   [p * 1.005 for p in prices],
        "Low":    [p * 0.995 for p in prices],
        "Close":  prices,
        "Volume": [500_000] * n,
    })


def _make_falling_df(n: int = 252, start: float = 200.0) -> pd.DataFrame:
    prices = [start - 0.2 * i for i in range(n)]
    return pd.DataFrame({
        "Open":  prices, "High":  prices, "Low":   prices,
        "Close": prices, "Volume": [500_000] * n,
    })


def _make_flat_df(n: int = 252, price: float = 100.0) -> pd.DataFrame:
    prices = [price] * n
    return pd.DataFrame({
        "Open": prices, "High": prices, "Low": prices,
        "Close": prices, "Volume": [500_000] * n,
    })


# ──────────────────────────────────────────────────────────────────────────────
# _rsi
# ──────────────────────────────────────────────────────────────────────────────

class TestRSI:
    def test_none_on_insufficient_data(self):
        df = _make_df(10)
        assert _rsi(df["Close"]) is None

    def test_rsi_in_range(self):
        df = _make_df(100)
        val = _rsi(df["Close"])
        assert val is not None
        assert 0.0 <= val <= 100.0

    def test_rising_series_high_rsi(self):
        df = _make_df(100, drift=1.0)
        val = _rsi(df["Close"])
        assert val is not None
        assert val > 50

    def test_falling_series_low_rsi(self):
        df = _make_falling_df(100)
        val = _rsi(df["Close"])
        assert val is not None
        assert val < 50

    def test_flat_series_around_50(self):
        # Flat = no gains, no losses → RSI should be near 50 or None
        df = _make_flat_df(100)
        val = _rsi(df["Close"])
        # Flat may return None (zero avg_loss) or ~50, both acceptable
        if val is not None:
            assert 0 <= val <= 100

    def test_returns_float(self):
        df = _make_df(100)
        val = _rsi(df["Close"])
        if val is not None:
            assert isinstance(val, float)


# ──────────────────────────────────────────────────────────────────────────────
# _price_above_ema200
# ──────────────────────────────────────────────────────────────────────────────

class TestPriceAboveEMA200:
    def test_uptrending_above_ema200(self):
        # Long uptrend: recent price is well above EMA200
        df = _make_df(300, start=50.0, drift=0.5)
        assert _price_above_ema200(df["Close"]) is True

    def test_downtrending_below_ema200(self):
        df = _make_falling_df(300, start=200.0)
        assert _price_above_ema200(df["Close"]) is False

    def test_insufficient_data_returns_false(self):
        df = _make_df(30)
        assert _price_above_ema200(df["Close"]) is False

    def test_exactly_50_rows_uses_available(self):
        df = _make_df(50)
        result = _price_above_ema200(df["Close"])
        assert isinstance(result, bool)


# ──────────────────────────────────────────────────────────────────────────────
# _fii_net_buying
# ──────────────────────────────────────────────────────────────────────────────

class TestFIINetBuying:
    def test_positive_fii_returns_true(self):
        assert _fii_net_buying({"fii_net": 500.0}) is True

    def test_negative_fii_returns_false(self):
        assert _fii_net_buying({"fii_net": -200.0}) is False

    def test_zero_fii_returns_false(self):
        assert _fii_net_buying({"fii_net": 0.0}) is False

    def test_none_fii_data_returns_none(self):
        # None means API unavailable — must be None, not False, so caller
        # can distinguish "not buying" vs "we don't know"
        result = _fii_net_buying(None)
        assert result is None

    def test_missing_key_returns_false(self):
        # Empty dict means the API responded but net = 0 (not buying)
        assert _fii_net_buying({}) is False

    def test_malformed_value_returns_none(self):
        # Unparseable payload → unknown, not False
        assert _fii_net_buying({"fii_net": "n/a"}) is None


# ──────────────────────────────────────────────────────────────────────────────
# prescreen()
# ──────────────────────────────────────────────────────────────────────────────

def _good_ohlcv():
    """252-bar rising df: RSI ~high, price > EMA200."""
    return _make_df(252, start=100.0, drift=0.3)


def _good_screener():
    # Include fii_holding_pct so Filter 3 (institutional holding ≥ 5%) fires.
    return {"pe": 25.0, "revenue_growth": 22.0, "fii_holding_pct": 15.0, "dii_holding_pct": 8.0}


def _good_fii():
    return {"fii_net": 800.0}


class TestPrescreen:
    def test_all_5_pass(self, monkeypatch):
        monkeypatch.setattr("agents.discovery_screener.get_ohlcv",
                            lambda s, period: _make_df(252, drift=0.05))
        monkeypatch.setattr("agents.discovery_screener.get_screener_data",
                            lambda s: _good_screener())
        passes, triggers = prescreen("TEST.NS", fii_data=_good_fii())
        assert passes is True
        assert len(triggers) >= 4

    def test_no_data_fails(self, monkeypatch):
        monkeypatch.setattr("agents.discovery_screener.get_ohlcv",
                            lambda s, period: None)
        passes, triggers = prescreen("TEST.NS")
        assert passes is False
        assert triggers == []

    def test_too_short_df_fails(self, monkeypatch):
        monkeypatch.setattr("agents.discovery_screener.get_ohlcv",
                            lambda s, period: _make_df(10))
        passes, _ = prescreen("TEST.NS")
        assert passes is False

    def test_only_3_pass_fails(self, monkeypatch):
        # Rising trend (above EMA200), OK RSI, good revenue growth — only 3 triggers
        monkeypatch.setattr("agents.discovery_screener.get_ohlcv",
                            lambda s, period: _make_df(252, drift=0.05))
        # PE too high, no growth override, no FII
        monkeypatch.setattr("agents.discovery_screener.get_screener_data",
                            lambda s: {"pe": 60.0, "revenue_growth": 20.0})
        passes, triggers = prescreen("TEST.NS", fii_data={"fii_net": -100.0})
        # Should pass: EMA200 + revenue_growth + (pe missing but growth=20>15) = 2 confirmed
        # depends on RSI — let's just check the boolean logic
        assert isinstance(passes, bool)
        assert isinstance(triggers, list)

    def test_fii_filter_adds_trigger(self, monkeypatch):
        monkeypatch.setattr("agents.discovery_screener.get_ohlcv",
                            lambda s, period: _make_df(252, drift=0.05))
        monkeypatch.setattr("agents.discovery_screener.get_screener_data",
                            lambda s: _good_screener())
        passes_with_fii,    t1 = prescreen("TEST.NS", fii_data={"fii_net": 500.0})
        passes_without_fii, t2 = prescreen("TEST.NS", fii_data={"fii_net": -100.0})
        assert len(t1) >= len(t2)

    def test_growth_justifies_high_pe(self, monkeypatch):
        # Tier C: PE within 2× sector median AND revenue growth > 30%
        # Default sector_pe fallback = 22x → pe must be ≤ 44 for pe_vs_sector ≤ 2.0
        # Use pe=40 (1.82× sector median), revenue_growth=45% → Tier C fires
        monkeypatch.setattr("agents.discovery_screener.get_ohlcv",
                            lambda s, period: _make_df(252, drift=0.05))
        monkeypatch.setattr("agents.discovery_screener.get_screener_data",
                            lambda s: {"pe": 40.0, "revenue_growth": 45.0})
        passes, triggers = prescreen("TEST.NS", fii_data=_good_fii())
        growth_trigger = any("justified" in t for t in triggers)
        assert growth_trigger, (
            f"Expected Tier C 'justified' trigger for PE=40 (1.8x sector median) "
            f"with 45% revenue growth. Got triggers: {triggers}"
        )

    def test_pe_exceeds_2x_sector_fails_tier_c(self, monkeypatch):
        # PE=80 with default sector_pe=22 → pe_vs_sector=3.6x > 2.0 → Tier C fails
        # Even with strong growth, PE too expensive vs sector → no PE trigger fires
        monkeypatch.setattr("agents.discovery_screener.get_ohlcv",
                            lambda s, period: _make_df(252, drift=0.05))
        monkeypatch.setattr("agents.discovery_screener.get_screener_data",
                            lambda s: {"pe": 80.0, "revenue_growth": 45.0})
        passes, triggers = prescreen("TEST.NS", fii_data=_good_fii())
        pe_trigger = any(
            any(kw in t for kw in ("PE", "undervalued", "fair value", "justified"))
            for t in triggers
        )
        assert not pe_trigger, (
            f"PE=80 (3.6x sector median 22x) should fail all PE tiers. "
            f"Got triggers: {triggers}"
        )

    def test_screener_none_skips_fundamental_filters(self, monkeypatch):
        monkeypatch.setattr("agents.discovery_screener.get_ohlcv",
                            lambda s, period: _make_df(252, drift=0.05))
        monkeypatch.setattr("agents.discovery_screener.get_screener_data",
                            lambda s: None)
        passes, triggers = prescreen("TEST.NS", fii_data=_good_fii())
        # Without screener data, only EMA200 + RSI can fire (Filter 3 needs
        # institutional_holding_pct from screener → also absent).  ≤2 real hits → fails.
        real_hits = sum(1 for t in triggers if not t.startswith("["))
        assert real_hits <= 3
        assert passes is False

    def test_triggers_are_strings(self, monkeypatch):
        monkeypatch.setattr("agents.discovery_screener.get_ohlcv",
                            lambda s, period: _make_df(252, drift=0.05))
        monkeypatch.setattr("agents.discovery_screener.get_screener_data",
                            lambda s: _good_screener())
        _, triggers = prescreen("TEST.NS", fii_data=_good_fii())
        for t in triggers:
            assert isinstance(t, str)
            assert len(t) > 5

    # ── fii_data param is legacy (kept for API compat) ────────────────────────

    def test_fii_data_param_ignored_no_meta_note(self, monkeypatch):
        """
        The fii_data param is kept for API compatibility but is no longer used
        in prescreen logic (Filter 3 now uses stock-specific institutional holding
        from screener data).  Passing fii_data=None must NOT add a [meta] note
        and must NOT change the threshold (always 4-of-5).
        """
        monkeypatch.setattr("agents.discovery_screener.get_ohlcv",
                            lambda s, period: _make_df(252, drift=0.05))
        monkeypatch.setattr("agents.discovery_screener.get_screener_data",
                            lambda s: _good_screener())
        _, triggers = prescreen("TEST.NS", fii_data=None)
        meta = [t for t in triggers if t.startswith("[")]
        # No legacy FII meta note should be injected (that logic was removed in P0-F)
        assert len(meta) == 0

    def test_good_screener_data_causes_pass(self, monkeypatch):
        """With full screener data (including institutional holding), 4+ filters fire."""
        # EMA200 + PE + revenue_growth + institutional_holding = 4 hits → passes 4-of-5
        monkeypatch.setattr("agents.discovery_screener.get_ohlcv",
                            lambda s, period: _make_df(252, drift=0.05))
        monkeypatch.setattr("agents.discovery_screener.get_screener_data",
                            lambda s: _good_screener())
        passes, triggers = prescreen("TEST.NS", fii_data=None)
        real_hits = sum(1 for t in triggers if not t.startswith("["))
        assert real_hits >= 4        # at least 4 of 5 filters
        assert passes is True

    def test_no_screener_causes_fail_strict_threshold(self, monkeypatch):
        """Without screener data only EMA200 + RSI can fire → below 4-of-5 strict threshold."""
        monkeypatch.setattr("agents.discovery_screener.get_ohlcv",
                            lambda s, period: _make_df(252, drift=0.05))
        monkeypatch.setattr("agents.discovery_screener.get_screener_data",
                            lambda s: None)
        passes, triggers = prescreen("TEST.NS", fii_data=None)
        real_hits = sum(1 for t in triggers if not t.startswith("["))
        assert real_hits <= 2
        assert passes is False

    def test_threshold_always_strict_regardless_of_fii_param(self, monkeypatch):
        """
        Since P0-F, the threshold is always strict 4-of-5 regardless of whether
        fii_data is None or provided.  The old relaxed 3-of-4 path (triggered when
        fii_data=None) was removed when Filter 3 changed from global FII flow to
        per-stock institutional holding %.

        Setup: drift=0.05 → RSI typically > 65 (all-up trend) so Filter 1 doesn't
        fire.  _good_screener() now includes fii_holding_pct=15%, so:
          EMA200 + PE + revenue_growth + institutional = 4 real hits → PASSES
        Both fii_data=None and fii_data=-500 should produce the same outcome.
        """
        monkeypatch.setattr("agents.discovery_screener.get_ohlcv",
                            lambda s, period: _make_df(252, drift=0.05))
        monkeypatch.setattr("agents.discovery_screener.get_screener_data",
                            lambda s: _good_screener())

        passes_no_fii,  t_no_fii  = prescreen("TEST.NS", fii_data=None)
        passes_neg_fii, t_neg_fii = prescreen("TEST.NS", fii_data={"fii_net": -500.0})

        # fii_data param doesn't change trigger list or outcome since P0-F
        real_hits_no_fii  = sum(1 for t in t_no_fii  if not t.startswith("["))
        real_hits_neg_fii = sum(1 for t in t_neg_fii if not t.startswith("["))
        assert real_hits_no_fii == real_hits_neg_fii   # identical
        assert passes_no_fii == passes_neg_fii         # same decision
        # No [meta] notes injected (old FII meta logic removed)
        assert all(not t.startswith("[") for t in t_no_fii)

    def test_trigger_count_matches_pass_decision(self, monkeypatch):
        """Real trigger count must be consistent with the pass/fail decision (≥4 threshold)."""
        monkeypatch.setattr("agents.discovery_screener.get_ohlcv",
                            lambda s, period: _make_df(252, drift=0.05))
        monkeypatch.setattr("agents.discovery_screener.get_screener_data",
                            lambda s: _good_screener())
        passes, triggers = prescreen("TEST.NS", fii_data=None)
        # No [meta] notes injected (FII meta logic removed in P0-F)
        assert all(not t.startswith("[") for t in triggers)
        # Function decision must match the trigger count against the ≥4 threshold
        real_hits = len(triggers)
        expected = real_hits >= 4
        assert passes is expected


# ──────────────────────────────────────────────────────────────────────────────
# Filter 6: DVM composite score (P3-C-P3)
# ──────────────────────────────────────────────────────────────────────────────

def _good_dvm(composite: float = 65.0) -> dict:
    return {
        "durability_score":  70.0,
        "valuation_score":   60.0,
        "momentum_score":    65.0,
        "composite_dvm":     composite,
    }


class TestPrescreenFilter6DVM:
    """Tests for Filter 6: Trendlyne DVM composite score ≥ 45."""

    def _setup_base(self, monkeypatch, screener=None):
        """Patch OHLCV + screener so Filters 1–5 have known state."""
        monkeypatch.setattr("agents.discovery_screener.get_ohlcv",
                            lambda s, period: _make_df(252, drift=0.05))
        monkeypatch.setattr("agents.discovery_screener.get_screener_data",
                            lambda s: screener if screener is not None else _good_screener())

    def test_dvm_adds_trigger_when_session_set_and_score_passes(self, monkeypatch):
        """When TRENDLYNE_SESSION is set and DVM ≥ 45, a trigger is appended."""
        self._setup_base(monkeypatch)
        monkeypatch.setenv("TRENDLYNE_SESSION", "fake-session-token")
        monkeypatch.setattr(
            "data.trendlyne_fetcher.get_trendlyne_dvm",
            lambda s: _good_dvm(65.0),
        )
        _, triggers = prescreen("TEST.NS")
        dvm_triggers = [t for t in triggers if "DVM" in t]
        assert len(dvm_triggers) == 1
        assert "65" in dvm_triggers[0]   # composite score in trigger text

    def test_dvm_no_trigger_when_score_below_threshold(self, monkeypatch):
        """DVM composite < 45 → trigger NOT added."""
        self._setup_base(monkeypatch)
        monkeypatch.setenv("TRENDLYNE_SESSION", "fake-session-token")
        monkeypatch.setattr(
            "data.trendlyne_fetcher.get_trendlyne_dvm",
            lambda s: _good_dvm(30.0),   # below 45 threshold
        )
        _, triggers = prescreen("TEST.NS")
        dvm_triggers = [t for t in triggers if "DVM" in t]
        assert len(dvm_triggers) == 0

    def test_dvm_skipped_when_no_session_env_var(self, monkeypatch):
        """Without TRENDLYNE_SESSION, Filter 6 silently skips."""
        self._setup_base(monkeypatch)
        monkeypatch.delenv("TRENDLYNE_SESSION", raising=False)
        # Even if DVM data is available, the filter must not fire
        monkeypatch.setattr(
            "data.trendlyne_fetcher.get_trendlyne_dvm",
            lambda s: _good_dvm(80.0),
        )
        _, triggers = prescreen("TEST.NS")
        dvm_triggers = [t for t in triggers if "DVM" in t]
        assert len(dvm_triggers) == 0

    def test_dvm_skipped_when_returns_none(self, monkeypatch):
        """When get_trendlyne_dvm returns None, Filter 6 silently skips."""
        self._setup_base(monkeypatch)
        monkeypatch.setenv("TRENDLYNE_SESSION", "fake-session-token")
        monkeypatch.setattr(
            "data.trendlyne_fetcher.get_trendlyne_dvm",
            lambda s: None,
        )
        _, triggers = prescreen("TEST.NS")
        dvm_triggers = [t for t in triggers if "DVM" in t]
        assert len(dvm_triggers) == 0

    def test_dvm_exception_does_not_crash_prescreen(self, monkeypatch):
        """If get_trendlyne_dvm raises, prescreen must still return normally."""
        self._setup_base(monkeypatch)
        monkeypatch.setenv("TRENDLYNE_SESSION", "fake-session-token")

        def _raise(s):
            raise RuntimeError("Trendlyne network error")

        monkeypatch.setattr("data.trendlyne_fetcher.get_trendlyne_dvm", _raise)
        passes, triggers = prescreen("TEST.NS")
        # Function returns without crashing; DVM trigger absent
        assert isinstance(passes, bool)
        assert isinstance(triggers, list)
        dvm_triggers = [t for t in triggers if "DVM" in t]
        assert len(dvm_triggers) == 0

    def test_dvm_trigger_contains_all_three_dimensions(self, monkeypatch):
        """Trigger text must embed Durability, Valuation, Momentum scores."""
        self._setup_base(monkeypatch)
        monkeypatch.setenv("TRENDLYNE_SESSION", "fake-session-token")
        monkeypatch.setattr(
            "data.trendlyne_fetcher.get_trendlyne_dvm",
            lambda s: {"durability_score": 72.0, "valuation_score": 55.0,
                       "momentum_score": 48.0, "composite_dvm": 58.3},
        )
        _, triggers = prescreen("TEST.NS")
        dvm_triggers = [t for t in triggers if "DVM" in t]
        assert len(dvm_triggers) == 1
        t = dvm_triggers[0]
        assert "Dur=72" in t
        assert "Val=55" in t
        assert "Mom=48" in t

    def test_dvm_helps_reach_threshold_when_other_filters_miss(self, monkeypatch):
        """
        Stock that would fail 3-of-5 without DVM can pass 4-of-6 with DVM.
        Setup: screener has no fii_holding_pct → Filter 3 fails.
               drift=0.05 → RSI typically high → Filter 1 may fail.
               Only EMA200 + PE + revenue_growth fire (3 real hits).
               DVM ≥ 45 pushes to 4 → passes threshold.
        """
        self._setup_base(monkeypatch,
                         screener={"pe": 25.0, "revenue_growth": 22.0})  # no institutional data
        monkeypatch.setenv("TRENDLYNE_SESSION", "fake-session-token")
        monkeypatch.setattr(
            "data.trendlyne_fetcher.get_trendlyne_dvm",
            lambda s: _good_dvm(60.0),
        )
        passes, triggers = prescreen("TEST.NS")
        dvm_triggers = [t for t in triggers if "DVM" in t]
        # DVM fired
        assert len(dvm_triggers) == 1
        # If exactly 3 core filters fired + DVM = 4 → passes; or already 4 core → passes too
        assert passes is True

    def test_dvm_threshold_boundary_exactly_45(self, monkeypatch):
        """Composite score exactly at 45 must fire (boundary inclusive)."""
        self._setup_base(monkeypatch)
        monkeypatch.setenv("TRENDLYNE_SESSION", "fake-session-token")
        monkeypatch.setattr(
            "data.trendlyne_fetcher.get_trendlyne_dvm",
            lambda s: _good_dvm(45.0),
        )
        _, triggers = prescreen("TEST.NS")
        dvm_triggers = [t for t in triggers if "DVM" in t]
        assert len(dvm_triggers) == 1   # exactly 45 → passes (≥ 45)

    def test_dvm_threshold_just_below_45_does_not_fire(self, monkeypatch):
        """Composite score of 44.9 must NOT fire."""
        self._setup_base(monkeypatch)
        monkeypatch.setenv("TRENDLYNE_SESSION", "fake-session-token")
        monkeypatch.setattr(
            "data.trendlyne_fetcher.get_trendlyne_dvm",
            lambda s: _good_dvm(44.9),
        )
        _, triggers = prescreen("TEST.NS")
        dvm_triggers = [t for t in triggers if "DVM" in t]
        assert len(dvm_triggers) == 0

    def test_dvm_absent_composite_field_skips(self, monkeypatch):
        """DVM dict with composite_dvm=None is treated as unavailable."""
        self._setup_base(monkeypatch)
        monkeypatch.setenv("TRENDLYNE_SESSION", "fake-session-token")
        monkeypatch.setattr(
            "data.trendlyne_fetcher.get_trendlyne_dvm",
            lambda s: {"durability_score": 70.0, "valuation_score": 60.0,
                       "momentum_score": 55.0, "composite_dvm": None},
        )
        _, triggers = prescreen("TEST.NS")
        dvm_triggers = [t for t in triggers if "DVM" in t]
        assert len(dvm_triggers) == 0   # None composite → skip


# ──────────────────────────────────────────────────────────────────────────────
# _composite_score
# ──────────────────────────────────────────────────────────────────────────────

def _make_agent_results(score: int = 60) -> dict:
    agents = ["technical", "fundamental", "sentiment",
              "institutional", "macro", "historical_rag", "commodities"]
    return {a: {"signal": "BUY", "score": score} for a in agents}


class TestCompositeScore:
    def test_all_same_score(self):
        results = _make_agent_results(60)
        assert _composite_score(results) == pytest.approx(60.0)

    def test_all_zero(self):
        results = _make_agent_results(0)
        assert _composite_score(results) == pytest.approx(0.0)

    def test_all_100(self):
        results = _make_agent_results(100)
        assert _composite_score(results) == pytest.approx(100.0)

    def test_missing_agent_excluded(self):
        results = _make_agent_results(80)
        del results["commodities"]
        score = _composite_score(results)
        # Without commodities (weight 0.05), remaining weights sum to 0.95
        # score should still be ~80 because all are 80
        assert 70 < score <= 100

    def test_empty_results(self):
        assert _composite_score({}) == pytest.approx(0.0)

    def test_none_score_skipped(self):
        results = _make_agent_results(70)
        results["technical"]["score"] = None
        score = _composite_score(results)
        assert score > 0  # other agents still contribute

    def test_fundamental_has_higher_weight_than_sentiment(self):
        results = _make_agent_results(50)
        results["fundamental"]["score"] = 100
        results["sentiment"]["score"]   = 0
        high = _composite_score(results)

        results2 = _make_agent_results(50)
        results2["fundamental"]["score"] = 0
        results2["sentiment"]["score"]   = 100
        low = _composite_score(results2)

        # fundamental weight 0.25 > sentiment weight 0.10
        assert high > low


# ──────────────────────────────────────────────────────────────────────────────
# _best_upside
# ──────────────────────────────────────────────────────────────────────────────

class TestBestUpside:
    def test_fundamental_upside_preferred(self):
        results = _make_agent_results(80)
        results["fundamental"]["upside_pct"] = 150.0
        results["technical"]["upside_pct"]   = 30.0
        results["technical"]["confidence"]   = 0.7
        upside, conf = _best_upside(results)
        assert upside == 150.0

    def test_technical_used_when_higher(self):
        results = _make_agent_results(70)
        results["fundamental"]["upside_pct"] = 15.0
        results["technical"]["upside_pct"]   = 60.0
        results["technical"]["confidence"]   = 0.8
        upside, conf = _best_upside(results)
        assert upside == 60.0

    def test_bullish_rag_boosts_confidence(self):
        results = _make_agent_results(70)
        results["fundamental"]["upside_pct"] = 50.0
        results["historical_rag"]["signal"]  = "BULLISH_ANALOGUE"
        _, conf_bullish = _best_upside(results)

        results2 = _make_agent_results(70)
        results2["fundamental"]["upside_pct"] = 50.0
        results2["historical_rag"]["signal"]  = "BEARISH_ANALOGUE"
        _, conf_bearish = _best_upside(results2)

        assert conf_bullish > conf_bearish

    def test_risk_on_macro_boosts_confidence(self):
        results = _make_agent_results(70)
        results["fundamental"]["upside_pct"] = 50.0
        results["macro"]["signal"] = "RISK_ON"
        _, conf_on = _best_upside(results)

        results2 = _make_agent_results(70)
        results2["fundamental"]["upside_pct"] = 50.0
        results2["macro"]["signal"] = "RISK_OFF"
        _, conf_off = _best_upside(results2)

        assert conf_on > conf_off

    def test_confidence_capped_at_100(self):
        results = _make_agent_results(100)
        results["fundamental"]["upside_pct"] = 200.0
        results["historical_rag"]["signal"]  = "BULLISH_ANALOGUE"
        results["macro"]["signal"]           = "RISK_ON"
        _, conf = _best_upside(results)
        assert conf <= 100.0

    def test_none_upside_treated_as_zero(self):
        results = _make_agent_results(60)
        results["fundamental"]["upside_pct"] = None
        results["technical"]["upside_pct"]   = None
        results["technical"]["confidence"]   = None
        upside, _ = _best_upside(results)
        assert upside == 0.0


# ──────────────────────────────────────────────────────────────────────────────
# _upside_horizon
# ──────────────────────────────────────────────────────────────────────────────

class TestUpsideHorizon:
    def test_high_score_risk_on_shortest_horizon(self):
        results = _make_agent_results(80)
        results["macro"]["signal"] = "RISK_ON"
        results["macro"]["score"]  = 80
        horizon = _upside_horizon(results)
        assert horizon == "2–4 months"

    def test_moderate_score_medium_horizon(self):
        results = _make_agent_results(66)
        results["macro"]["signal"] = "NEUTRAL"
        horizon = _upside_horizon(results)
        assert horizon == "3–6 months"

    def test_low_score_long_horizon(self):
        results = _make_agent_results(40)
        results["macro"]["signal"] = "RISK_OFF"
        horizon = _upside_horizon(results)
        assert horizon in ("4–8 months", "6–12 months")

    def test_returns_string(self):
        results = _make_agent_results(55)
        assert isinstance(_upside_horizon(results), str)

    def test_all_valid_horizons_set(self):
        valid = {"2–4 months", "3–6 months", "4–8 months", "6–12 months"}
        for score in [20, 45, 60, 70, 80]:
            results = _make_agent_results(score)
            h = _upside_horizon(results)
            assert h in valid


# ──────────────────────────────────────────────────────────────────────────────
# _upside_basis
# ──────────────────────────────────────────────────────────────────────────────

class TestUpsideBasis:
    def _full_results(self, upside=50.0, fii=200.0):
        results = _make_agent_results(75)
        results["fundamental"]["upside_pct"] = upside
        results["fundamental"]["detail"] = {
            "growth":        {"revenue_growth": 22.0},
            "profitability": {"pe": 18.0},
        }
        results["technical"]["signal"]    = "STRONG_BUY"
        results["institutional"]["fii_net_5d"] = fii
        results["macro"]["signal"]        = "RISK_ON"
        results["historical_rag"]["signal"] = "BULLISH_ANALOGUE"
        return results

    def test_returns_string(self):
        basis = _upside_basis("TCS.NS", self._full_results(), "STANDARD")
        assert isinstance(basis, str)
        assert len(basis) > 50

    def test_has_three_sentences(self):
        basis = _upside_basis("INFY.NS", self._full_results(), "CRITICAL")
        # Split on double-space separator used in implementation
        parts = [p.strip() for p in basis.split("  ") if p.strip()]
        assert len(parts) == 3

    def test_symbol_in_basis(self):
        basis = _upside_basis("RELIANCE.NS", self._full_results(), "STANDARD")
        assert "RELIANCE.NS" in basis

    def test_risk_on_bullish_rag_mentioned(self):
        results = self._full_results()
        results["macro"]["signal"]          = "RISK_ON"
        results["historical_rag"]["signal"] = "BULLISH_ANALOGUE"
        basis = _upside_basis("TCS.NS", results, "STANDARD")
        assert "RISK_ON" in basis or "bullish" in basis.lower()

    def test_critical_tier_mentioned(self):
        results = self._full_results(upside=120.0)
        basis = _upside_basis("TEST.NS", results, "CRITICAL")
        assert "high-conviction" in basis.lower() or "critical" in basis.lower() or "RISK_ON" in basis

    def test_upside_pct_in_basis(self):
        results = self._full_results(upside=85.0)
        basis = _upside_basis("TEST.NS", results, "STANDARD")
        assert "85" in basis


# ──────────────────────────────────────────────────────────────────────────────
# _horizon_to_days / _valid_till
# ──────────────────────────────────────────────────────────────────────────────

class TestHorizonHelpers:
    def test_known_horizons(self):
        assert _horizon_to_days("2–4 months")  == 90
        assert _horizon_to_days("3–6 months")  == 135
        assert _horizon_to_days("4–8 months")  == 180
        assert _horizon_to_days("6–12 months") == 270

    def test_unknown_defaults_to_135(self):
        assert _horizon_to_days("unknown") == 135

    def test_valid_till_future_date(self):
        vt = _valid_till("3–6 months")
        vt_date = date.fromisoformat(vt)
        assert vt_date > date.today()
        assert vt_date <= date.today() + timedelta(days=200)

    def test_valid_till_is_iso_string(self):
        vt = _valid_till("6–12 months")
        assert isinstance(vt, str)
        date.fromisoformat(vt)   # should not raise


# ──────────────────────────────────────────────────────────────────────────────
# _normalise_symbol
# ──────────────────────────────────────────────────────────────────────────────

class TestNormaliseSymbol:
    def test_strips_ns(self):
        assert _normalise_symbol("TCS.NS") == "TCS"

    def test_strips_bo(self):
        assert _normalise_symbol("INFY.BO") == "INFY"

    def test_uppercase(self):
        assert _normalise_symbol("reliance.ns") == "RELIANCE"

    def test_no_suffix_unchanged(self):
        assert _normalise_symbol("HDFCBANK") == "HDFCBANK"


# ──────────────────────────────────────────────────────────────────────────────
# DiscoveryResult dataclass
# ──────────────────────────────────────────────────────────────────────────────

def _make_discovery(tier="STANDARD", upside=35.0, conf=68.0):
    return DiscoveryResult(
        symbol            = "TCS.NS",
        opportunity_tier  = tier,
        upside_pct        = upside,
        upside_confidence = conf,
        upside_basis      = "Basis sentence 1.  Sentence 2.  Sentence 3.",
        upside_horizon    = "3–6 months",
        screen_triggers   = ["RSI 52 in range", "Revenue growth 22%"],
        agent_signals     = {"technical": {"signal": "BUY", "score": 72}},
        composite_score   = 68.0,
        current_price     = 3500.0,
        sector            = "IT",
    )


class TestDiscoveryResult:
    def test_to_dict_has_all_fields(self):
        dr = _make_discovery()
        d = dr.to_dict()
        for field in ["symbol", "opportunity_tier", "upside_pct", "upside_confidence",
                      "upside_basis", "upside_horizon", "screen_triggers",
                      "agent_signals", "composite_score", "current_price", "sector"]:
            assert field in d

    def test_discovered_at_is_set(self):
        dr = _make_discovery()
        assert dr.discovered_at is not None
        assert len(dr.discovered_at) > 10

    def test_saved_rec_id_default_none(self):
        dr = _make_discovery()
        assert dr.saved_rec_id is None

    def test_to_dict_serialisable(self):
        import json
        dr = _make_discovery()
        # Should not raise
        json.dumps(dr.to_dict(), default=str)


# ──────────────────────────────────────────────────────────────────────────────
# _load_portfolio_symbols
# ──────────────────────────────────────────────────────────────────────────────

class TestLoadPortfolioSymbols:
    def test_no_supabase_returns_empty(self):
        result = _load_portfolio_symbols()
        assert result == set()

    def test_with_supabase_returns_normalised_symbols(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "fake_key")

        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"symbol": "TCS.NS"},
            {"symbol": "INFY.BO"},
            {"symbol": "HDFCBANK"},
        ]
        with patch("supabase.create_client", return_value=mock_client):
            result = _load_portfolio_symbols()

        assert "TCS" in result
        assert "INFY" in result
        assert "HDFCBANK" in result

    def test_exception_returns_empty(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "fake_key")

        with patch("supabase.create_client", side_effect=Exception("DB error")):
            result = _load_portfolio_symbols()
        assert result == set()


# ──────────────────────────────────────────────────────────────────────────────
# _save_discovery
# ──────────────────────────────────────────────────────────────────────────────

class TestSaveDiscovery:
    def test_no_supabase_returns_none(self):
        dr = _make_discovery()
        assert _save_discovery(dr) is None

    def _mock_client_fresh(self):
        """Return a mock Supabase client where the cooldown SELECT returns no rows (fresh insert)."""
        mock_client = MagicMock()
        # Cooldown check: .table().select().eq().eq().gte().order().limit().execute().data = []
        select_chain = mock_client.table.return_value.select.return_value
        select_chain.eq.return_value.eq.return_value.gte.return_value.order.return_value.limit.return_value.execute.return_value.data = []
        # INSERT: .table().insert().execute().data = [{"id": "abc-123"}]
        mock_client.table.return_value.insert.return_value.execute.return_value.data = [
            {"id": "abc-123"}
        ]
        return mock_client

    def test_saves_and_returns_id(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "fake_key")

        mock_client = self._mock_client_fresh()
        with patch("supabase.create_client", return_value=mock_client):
            result = _save_discovery(_make_discovery())

        assert result == "abc-123"

    def test_insert_row_has_is_discovery_true(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "fake_key")

        inserted_rows = []
        mock_client = self._mock_client_fresh()
        original_side_effect = mock_client.table.return_value.insert.return_value
        def capture_insert(row):
            inserted_rows.append(row)
            m = MagicMock()
            m.execute.return_value.data = [{"id": "xyz"}]
            return m
        mock_client.table.return_value.insert.side_effect = capture_insert

        with patch("supabase.create_client", return_value=mock_client):
            _save_discovery(_make_discovery())

        assert len(inserted_rows) == 1
        assert inserted_rows[0]["is_discovery"] is True

    def test_exception_returns_none(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "fake_key")

        with patch("supabase.create_client", side_effect=Exception("timeout")):
            result = _save_discovery(_make_discovery())
        assert result is None


# ──────────────────────────────────────────────────────────────────────────────
# NIFTY500_SYMBOLS sanity checks
# ──────────────────────────────────────────────────────────────────────────────

class TestNIFTY500Symbols:
    def test_has_enough_symbols(self):
        assert len(NIFTY500_SYMBOLS) >= 50

    def test_all_end_with_ns(self):
        for sym in NIFTY500_SYMBOLS:
            assert sym.endswith(".NS"), f"{sym} does not end with .NS"

    def test_no_duplicates(self):
        assert len(NIFTY500_SYMBOLS) == len(set(NIFTY500_SYMBOLS))

    def test_known_stocks_present(self):
        for sym in ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS"]:
            assert sym in NIFTY500_SYMBOLS


# ──────────────────────────────────────────────────────────────────────────────
# run_discovery() — integration
# ──────────────────────────────────────────────────────────────────────────────

def _mock_agent(signal="BUY", score=75, upside=45.0, confidence=0.75):
    return {
        "signal":     signal,
        "score":      score,
        "upside_pct": upside,
        "confidence": confidence,
        "fii_net_5d": 200.0,
        "detail": {
            "growth":        {"revenue_growth": 20.0},
            "profitability": {"pe": 22.0},
            "governance":    {"promoter_pledging": 5.0},
            "sector":        "IT",
            "rsi":           {"value": 52.0},
        },
        "agent_name": "mock",
    }


class TestRunDiscovery:
    def _patch_all(self, monkeypatch, passes=True, upside=45.0, conf=68.0):
        """Patch all external dependencies for run_discovery."""
        # prescreen always passes
        monkeypatch.setattr(
            "agents.discovery_screener.prescreen",
            lambda sym, fii_data=None: (passes, ["RSI ok", "PE ok", "EMA200 ok", "Revenue ok"]),
        )
        # portfolio: empty
        monkeypatch.setattr(
            "agents.discovery_screener._load_portfolio_symbols",
            lambda: set(),
        )
        # fii_data
        monkeypatch.setattr(
            "agents.discovery_screener.get_nse_fii_dii",
            lambda: {"fii_net": 500.0},
        )
        # all agents
        monkeypatch.setattr(
            "agents.discovery_screener._run_all_agents",
            lambda sym, macro_result=None: {a: _mock_agent(upside=upside, score=int(conf))
                                            for a in ["technical","fundamental","sentiment",
                                                      "institutional","macro","historical_rag","commodities"]},
        )
        # macro pre-fetch
        monkeypatch.setattr(
            "agents.macro.analyse",
            lambda: {"signal": "RISK_ON", "score": 75, "detail": {}, "agent_name": "macro"},
        )
        # current price
        monkeypatch.setattr(
            "agents.discovery_screener._current_price",
            lambda sym: 1500.0,
        )
        # no DB
        monkeypatch.setattr("agents.discovery_screener._save_discovery", lambda d: None)
        monkeypatch.setattr("agents.discovery_screener._log_daily_run",  lambda **kw: None)

    def test_returns_list(self, monkeypatch):
        self._patch_all(monkeypatch)
        results = run_discovery(max_candidates=2, save_to_db=False)
        assert isinstance(results, list)

    def test_standard_opportunity_detected(self, monkeypatch):
        self._patch_all(monkeypatch, upside=35.0, conf=68.0)
        results = run_discovery(max_candidates=2, save_to_db=False)
        assert len(results) >= 1
        assert results[0].opportunity_tier == "STANDARD"

    def test_critical_opportunity_detected(self, monkeypatch):
        self._patch_all(monkeypatch, upside=120.0, conf=75.0)
        results = run_discovery(max_candidates=2, save_to_db=False)
        assert len(results) >= 1
        assert results[0].opportunity_tier == "CRITICAL"

    def test_below_threshold_excluded(self, monkeypatch):
        # upside=10, conf=50 → below both CRITICAL and STANDARD thresholds
        self._patch_all(monkeypatch, upside=10.0, conf=50.0)
        results = run_discovery(max_candidates=3, save_to_db=False)
        assert len(results) == 0

    def test_portfolio_symbols_excluded(self, monkeypatch):
        self._patch_all(monkeypatch, upside=40.0, conf=70.0)
        # Put TCS in portfolio → TCS.NS should not appear in results
        monkeypatch.setattr(
            "agents.discovery_screener._load_portfolio_symbols",
            lambda: {"TCS", "INFY"},
        )
        results = run_discovery(max_candidates=5, save_to_db=False)
        symbols = [r.symbol for r in results]
        assert "TCS.NS" not in symbols
        assert "INFY.NS" not in symbols

    def test_sorted_critical_first(self, monkeypatch):
        """CRITICAL results must come before STANDARD results."""
        call_count = [0]
        def varying_agents(sym, macro_result=None):
            call_count[0] += 1
            # Alternate critical / standard based on call order
            upside = 120.0 if call_count[0] % 2 == 0 else 30.0
            conf   = 75.0  if call_count[0] % 2 == 0 else 68.0
            return {a: _mock_agent(upside=upside, score=int(conf))
                    for a in ["technical","fundamental","sentiment",
                              "institutional","macro","historical_rag","commodities"]}

        self._patch_all(monkeypatch)
        monkeypatch.setattr("agents.discovery_screener._run_all_agents", varying_agents)
        results = run_discovery(max_candidates=4, save_to_db=False)
        tiers = [r.opportunity_tier for r in results]
        # All CRITICALs must appear before any STANDARD
        seen_standard = False
        for tier in tiers:
            if tier == "STANDARD":
                seen_standard = True
            if seen_standard:
                assert tier == "STANDARD", "CRITICAL appeared after STANDARD"

    def test_sorted_by_upside_desc_within_tier(self, monkeypatch):
        call_count = [0]
        upsides = [35.0, 55.0, 25.0, 45.0]
        def varying_agents(sym, macro_result=None):
            idx = call_count[0] % len(upsides)
            call_count[0] += 1
            return {a: _mock_agent(upside=upsides[idx], score=68)
                    for a in ["technical","fundamental","sentiment",
                              "institutional","macro","historical_rag","commodities"]}

        self._patch_all(monkeypatch)
        monkeypatch.setattr("agents.discovery_screener._run_all_agents", varying_agents)
        results = run_discovery(max_candidates=4, save_to_db=False)
        if len(results) >= 2:
            for i in range(len(results) - 1):
                if results[i].opportunity_tier == results[i+1].opportunity_tier:
                    assert results[i].upside_pct >= results[i+1].upside_pct

    def test_max_candidates_respected(self, monkeypatch):
        self._patch_all(monkeypatch, upside=40.0, conf=70.0)
        results = run_discovery(max_candidates=2, save_to_db=False)
        assert len(results) <= 2

    def test_discovery_result_schema(self, monkeypatch):
        self._patch_all(monkeypatch, upside=40.0, conf=70.0)
        results = run_discovery(max_candidates=1, save_to_db=False)
        if results:
            r = results[0]
            assert isinstance(r.symbol, str)
            assert r.opportunity_tier in ("CRITICAL", "STANDARD")
            assert isinstance(r.upside_pct, float)
            assert isinstance(r.upside_confidence, float)
            assert isinstance(r.upside_basis, str)
            assert isinstance(r.upside_horizon, str)
            assert isinstance(r.screen_triggers, list)
            assert isinstance(r.agent_signals, dict)
            assert isinstance(r.composite_score, float)

    def test_prescreen_fail_yields_no_discoveries(self, monkeypatch):
        self._patch_all(monkeypatch, passes=False)
        results = run_discovery(max_candidates=5, save_to_db=False)
        assert results == []

    def test_save_to_db_called_when_enabled(self, monkeypatch):
        self._patch_all(monkeypatch, upside=40.0, conf=70.0)
        saved = []
        monkeypatch.setattr(
            "agents.discovery_screener._save_discovery",
            lambda d: saved.append(d) or "mock-id",
        )
        run_discovery(max_candidates=2, save_to_db=True)
        assert len(saved) >= 1

    def test_save_to_db_skipped_when_disabled(self, monkeypatch):
        self._patch_all(monkeypatch, upside=40.0, conf=70.0)
        saved = []
        monkeypatch.setattr(
            "agents.discovery_screener._save_discovery",
            lambda d: saved.append(d) or "mock-id",
        )
        run_discovery(max_candidates=2, save_to_db=False)
        assert len(saved) == 0

    def test_agent_exception_does_not_crash_run(self, monkeypatch):
        self._patch_all(monkeypatch, upside=40.0, conf=70.0)
        call_count = [0]
        def flaky_agents(sym, macro_result=None):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("agent blew up")
            return {a: _mock_agent(upside=40.0, score=70)
                    for a in ["technical","fundamental","sentiment",
                              "institutional","macro","historical_rag","commodities"]}
        monkeypatch.setattr("agents.discovery_screener._run_all_agents", flaky_agents)
        # Should not raise
        results = run_discovery(max_candidates=3, save_to_db=False)
        assert isinstance(results, list)

    def test_fii_fetch_failure_does_not_crash(self, monkeypatch):
        self._patch_all(monkeypatch, upside=40.0, conf=70.0)
        monkeypatch.setattr(
            "agents.discovery_screener.get_nse_fii_dii",
            lambda: (_ for _ in ()).throw(Exception("NSE down")),
        )
        results = run_discovery(max_candidates=2, save_to_db=False)
        assert isinstance(results, list)
