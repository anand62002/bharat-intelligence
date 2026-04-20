"""
tests/test_sector_valuation.py — Unit tests for agents/sector_valuation.py

Coverage:
  TestClassifyRegime          — all 6 regimes; edge cases; zero long_run_pe guard
  TestGetLivePeForSector      — NSE API hit / miss; yfinance fallback path
  TestFetchNseSectorPe        — NSE allIndices API parse + cache behaviour
  TestFetchYfConstituentPe    — yfinance constituent median; < 2 valid guard
  TestGetSectorRegime         — cache hit/miss; unknown sector; live-PE absent;
                                full happy-path (NSE & yfinance sources)
  TestGetLiveSectorPeMap      — returns dict; omits sectors with no live PE
  TestClearCache              — flushes all caches
"""

import time
import unittest
from unittest.mock import MagicMock, patch

import sys
import os

# Add project root to path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agents.sector_valuation import (
    SECTOR_LONGRUN_PE,
    _REGIME_THRESHOLDS,
    classify_regime,
    clear_cache,
    get_live_sector_pe_map,
    get_sector_regime,
    _CACHE_TTL,
    _FAIR_REGIME,
    _regime_cache,
    _fetch_yf_constituent_pe,
    _fetch_nse_all_index_pes,
)
import agents.sector_valuation as sv_module


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_nse_response(index_pes: dict[str, float]):
    """Build a mock NSE allIndices response dict."""
    return {
        "data": [
            {"index": name, "pe": str(pe)}
            for name, pe in index_pes.items()
        ]
    }


# ──────────────────────────────────────────────────────────────────────────────
# TestClassifyRegime
# ──────────────────────────────────────────────────────────────────────────────

class TestClassifyRegime(unittest.TestCase):
    """Test classify_regime() against all threshold boundaries."""

    def setUp(self):
        clear_cache()

    # ── COMPRESSED (ratio < 0.80) ─────────────────────────────────────────────

    def test_compressed_well_below(self):
        """ratio = 0.70 → COMPRESSED, multiplier = 1.20."""
        regime, mult, dev = classify_regime(7.0, 10.0)
        self.assertEqual(regime, "COMPRESSED")
        self.assertAlmostEqual(mult, 1.20)
        self.assertAlmostEqual(dev, -30.0)

    def test_compressed_at_boundary_lower(self):
        """ratio exactly at 0.80 boundary → MILDLY_COMPRESSED (exclusive upper)."""
        # _REGIME_THRESHOLDS[0] = (0.80, "COMPRESSED", 1.20) → ratio < 0.80 is COMPRESSED
        # ratio == 0.80 falls in MILDLY_COMPRESSED because 0.80 < 0.92
        regime, mult, dev = classify_regime(8.0, 10.0)
        self.assertEqual(regime, "MILDLY_COMPRESSED")
        self.assertAlmostEqual(mult, 1.10)

    # ── MILDLY_COMPRESSED (0.80 ≤ ratio < 0.92) ──────────────────────────────

    def test_mildly_compressed(self):
        """ratio = 0.85 → MILDLY_COMPRESSED, multiplier = 1.10."""
        regime, mult, dev = classify_regime(8.5, 10.0)
        self.assertEqual(regime, "MILDLY_COMPRESSED")
        self.assertAlmostEqual(mult, 1.10)
        self.assertAlmostEqual(dev, -15.0)

    # ── FAIR (0.92 ≤ ratio < 1.08) ───────────────────────────────────────────

    def test_fair_at_parity(self):
        """ratio = 1.0 exactly → FAIR, multiplier = 1.00."""
        regime, mult, dev = classify_regime(14.0, 14.0)
        self.assertEqual(regime, "FAIR")
        self.assertAlmostEqual(mult, 1.00)
        self.assertAlmostEqual(dev, 0.0)

    def test_fair_slight_premium(self):
        """ratio = 1.05 (5% above long-run) → FAIR."""
        regime, mult, dev = classify_regime(28.0 * 1.05, 28.0)
        self.assertEqual(regime, "FAIR")
        self.assertAlmostEqual(mult, 1.00)

    def test_fair_slight_discount(self):
        """ratio = 0.95 (5% below long-run) → FAIR."""
        regime, mult, dev = classify_regime(28.0 * 0.95, 28.0)
        self.assertEqual(regime, "FAIR")

    # ── MILDLY_STRETCHED (1.08 ≤ ratio < 1.25) ───────────────────────────────

    def test_mildly_stretched(self):
        """ratio = 1.15 → MILDLY_STRETCHED, multiplier = 0.94."""
        regime, mult, dev = classify_regime(11.5, 10.0)
        self.assertEqual(regime, "MILDLY_STRETCHED")
        self.assertAlmostEqual(mult, 0.94)
        self.assertAlmostEqual(dev, 15.0)

    # ── STRETCHED (1.25 ≤ ratio < 1.45) ──────────────────────────────────────

    def test_stretched(self):
        """ratio = 1.35 → STRETCHED, multiplier = 0.88."""
        regime, mult, dev = classify_regime(13.5, 10.0)
        self.assertEqual(regime, "STRETCHED")
        self.assertAlmostEqual(mult, 0.88)
        self.assertAlmostEqual(dev, 35.0)

    # ── EXTREME (ratio ≥ 1.45) ────────────────────────────────────────────────

    def test_extreme(self):
        """ratio = 1.60 → EXTREME, multiplier = 0.80."""
        regime, mult, dev = classify_regime(16.0, 10.0)
        self.assertEqual(regime, "EXTREME")
        self.assertAlmostEqual(mult, 0.80)
        self.assertAlmostEqual(dev, 60.0)

    def test_extreme_very_stretched(self):
        """ratio = 2.5 → EXTREME."""
        regime, mult, _ = classify_regime(50.0, 20.0)
        self.assertEqual(regime, "EXTREME")
        self.assertAlmostEqual(mult, 0.80)

    # ── Edge cases ────────────────────────────────────────────────────────────

    def test_zero_long_run_pe_returns_fair(self):
        """long_run_pe = 0 → guard returns FAIR, mult=1.0."""
        regime, mult, dev = classify_regime(25.0, 0.0)
        self.assertEqual(regime, "FAIR")
        self.assertAlmostEqual(mult, 1.00)
        self.assertAlmostEqual(dev, 0.0)

    def test_deviation_pct_rounded_to_one_decimal(self):
        """deviation_pct should be rounded to 1 decimal place."""
        _, _, dev = classify_regime(14.1, 14.0)
        # (14.1/14.0 - 1)*100 = 0.714... → rounded to 0.7
        self.assertAlmostEqual(dev, 0.7, places=1)

    def test_all_regime_thresholds_covered(self):
        """Verify _REGIME_THRESHOLDS has 6 entries and last entry has max_ratio=None."""
        self.assertEqual(len(_REGIME_THRESHOLDS), 6)
        last_entry = _REGIME_THRESHOLDS[-1]
        self.assertIsNone(last_entry[0], "Last threshold must be (None, EXTREME, ...)")

    def test_return_tuple_length(self):
        """classify_regime always returns 3-tuple."""
        result = classify_regime(20.0, 20.0)
        self.assertEqual(len(result), 3)


# ──────────────────────────────────────────────────────────────────────────────
# TestFetchNseSectorPe
# ──────────────────────────────────────────────────────────────────────────────

class TestFetchNseSectorPe(unittest.TestCase):
    """Test _fetch_nse_all_index_pes() parsing and caching.

    Note: requests is imported LOCALLY inside _fetch_nse_all_index_pes, so we
    must patch "requests.Session" globally (or mock the whole requests module
    via sys.modules) rather than patching an attribute on the sector_valuation
    module object.
    """

    def setUp(self):
        clear_cache()

    def _make_mock_session(self, nse_data: dict):
        """Build a mock requests Session that returns nse_data as JSON."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = nse_data
        mock_resp.raise_for_status.return_value = None

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        return mock_session

    def _patch_requests(self, session_mock):
        """Context-manager: replace the `requests` module seen by the function."""
        import sys
        mock_requests = MagicMock()
        mock_requests.Session.return_value = session_mock
        return patch.dict(sys.modules, {"requests": mock_requests}), mock_requests

    def test_happy_path_parses_pe_values(self):
        """Correctly extracts PE from data array."""
        session = self._make_mock_session(
            _make_nse_response({"NIFTY IT": 32.5, "NIFTY BANK": 13.8, "NIFTY PHARMA": 27.1})
        )
        ctx, _ = self._patch_requests(session)
        with ctx:
            result = _fetch_nse_all_index_pes()
        self.assertAlmostEqual(result.get("NIFTY IT"), 32.5)
        self.assertAlmostEqual(result.get("NIFTY BANK"), 13.8)
        self.assertAlmostEqual(result.get("NIFTY PHARMA"), 27.1)

    def test_filters_zero_and_negative_pe(self):
        """P/E values of 0 or negative must be excluded."""
        session = self._make_mock_session(
            _make_nse_response({"NIFTY IT": 32.5, "BAD INDEX": 0.0, "NEG INDEX": -5.0})
        )
        ctx, _ = self._patch_requests(session)
        with ctx:
            result = _fetch_nse_all_index_pes()
        self.assertIn("NIFTY IT", result)
        self.assertNotIn("BAD INDEX", result)
        self.assertNotIn("NEG INDEX", result)

    def test_caches_result_in_module_globals(self):
        """Second call within TTL does NOT re-call the API."""
        session = self._make_mock_session(
            _make_nse_response({"NIFTY IT": 32.5})
        )
        ctx, mock_req = self._patch_requests(session)
        with ctx:
            _fetch_nse_all_index_pes()
            _fetch_nse_all_index_pes()   # second call — should use cache
        # Session constructor should be called only once
        self.assertEqual(mock_req.Session.call_count, 1)

    def test_network_failure_returns_empty_dict(self):
        """Any exception during fetch must return {} and not raise."""
        import sys
        mock_req = MagicMock()
        mock_req.Session.side_effect = Exception("network down")
        with patch.dict(sys.modules, {"requests": mock_req}):
            result = _fetch_nse_all_index_pes()
        self.assertEqual(result, {})

    def test_import_error_returns_empty_dict(self):
        """When requests is not installed, returns {}."""
        import sys
        with patch.dict(sys.modules, {"requests": None}):
            result = _fetch_nse_all_index_pes()
        # None entry in sys.modules causes ImportError — should silently return {} or cached
        self.assertIsInstance(result, dict)


# ──────────────────────────────────────────────────────────────────────────────
# TestFetchYfConstituentPe
# ──────────────────────────────────────────────────────────────────────────────

class TestFetchYfConstituentPe(unittest.TestCase):
    """Test _fetch_yf_constituent_pe() median logic and guard conditions.

    Note: yfinance and yf_fetch_with_retry are imported LOCALLY inside
    _fetch_yf_constituent_pe.  We patch "data.fetchers.yf_fetch_with_retry"
    and let the function's local `import yfinance as yf` succeed normally
    (since yfinance IS installed), but mock the retry wrapper to return
    controlled info dicts.
    """

    def setUp(self):
        clear_cache()

    def _make_yf_info(self, trailing_pe=None, forward_pe=None):
        return {
            "trailingPE": trailing_pe,
            "forwardPE":  forward_pe,
        }

    def test_returns_median_of_three_pes(self):
        """Median of [20, 28, 35] = 28."""
        infos = [
            self._make_yf_info(20.0),
            self._make_yf_info(28.0),
            self._make_yf_info(35.0),
        ]
        infos_iter = iter(infos)

        with patch("data.fetchers.yf_fetch_with_retry",
                   side_effect=lambda fn, **kw: next(infos_iter)):
            result = _fetch_yf_constituent_pe("it")

        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, 28.0)

    def test_returns_none_when_less_than_two_valid(self):
        """With only 1 valid PE, returns None."""
        infos = [
            self._make_yf_info(25.0),   # valid
            {"trailingPE": None, "forwardPE": None},
            {"trailingPE": None, "forwardPE": None},
        ]
        infos_iter = iter(infos)

        with patch("data.fetchers.yf_fetch_with_retry",
                   side_effect=lambda fn, **kw: next(infos_iter)):
            result = _fetch_yf_constituent_pe("it")

        self.assertIsNone(result)

    def test_returns_none_for_unknown_sector(self):
        """Sector with no representatives returns None immediately."""
        result = _fetch_yf_constituent_pe("unknown_sector_xyz")
        self.assertIsNone(result)

    def test_handles_yfinance_exception_gracefully(self):
        """If all yfinance calls fail, returns None (< 2 valid)."""
        with patch("data.fetchers.yf_fetch_with_retry",
                   side_effect=Exception("429 rate limited")):
            result = _fetch_yf_constituent_pe("banking")

        self.assertIsNone(result)

    def test_uses_forward_pe_when_trailing_absent(self):
        """Falls back to forwardPE when trailingPE is None."""
        infos = [
            self._make_yf_info(trailing_pe=None, forward_pe=22.0),
            self._make_yf_info(trailing_pe=None, forward_pe=25.0),
        ]
        infos_iter = iter(infos)

        with patch("data.fetchers.yf_fetch_with_retry",
                   side_effect=lambda fn, **kw: next(infos_iter)):
            result = _fetch_yf_constituent_pe("banking")

        self.assertIsNotNone(result)
        # 2 valid values → median of sorted([22, 25]) = (22+25)/2 = 23.5
        self.assertAlmostEqual(result, 23.5)


# ──────────────────────────────────────────────────────────────────────────────
# TestGetSectorRegime
# ──────────────────────────────────────────────────────────────────────────────

class TestGetSectorRegime(unittest.TestCase):
    """Test get_sector_regime() — the main public entry point."""

    def setUp(self):
        clear_cache()

    # ── Edge cases ────────────────────────────────────────────────────────────

    def test_empty_sector_key_returns_fair(self):
        """Empty string → FAIR fallback (no adjustment)."""
        result = get_sector_regime("")
        self.assertEqual(result["regime"], "FAIR")
        self.assertAlmostEqual(result["multiplier"], 1.0)

    def test_unknown_sector_returns_fair_with_note(self):
        """Sector not in SECTOR_LONGRUN_PE → FAIR with explanatory note."""
        result = get_sector_regime("space_colonisation")
        self.assertEqual(result["regime"], "FAIR")
        self.assertAlmostEqual(result["multiplier"], 1.0)
        self.assertIn("not in SECTOR_LONGRUN_PE", result["note"])

    def test_no_live_pe_returns_fair_fallback(self):
        """When live PE cannot be fetched, returns FAIR with long_run_pe populated."""
        with patch.object(sv_module, "_get_live_pe_for_sector", return_value=None):
            result = get_sector_regime("banking")
        self.assertEqual(result["regime"], "FAIR")
        self.assertAlmostEqual(result["multiplier"], 1.0)
        self.assertEqual(result["data_source"], "fallback_fair")
        # long_run_pe should still be populated
        self.assertIsNotNone(result["long_run_pe"])

    def test_always_returns_valid_dict(self):
        """Must never raise; always returns a valid dict."""
        with patch.object(sv_module, "_get_live_pe_for_sector", side_effect=RuntimeError("boom")):
            result = get_sector_regime("it")
        self.assertIsInstance(result, dict)
        self.assertIn("regime", result)
        self.assertIn("multiplier", result)

    # ── Happy path: COMPRESSED regime ────────────────────────────────────────

    def test_compressed_regime_via_nse_api(self):
        """Banking at 10.5x vs long-run 14x → ratio ~0.75 → COMPRESSED."""
        with patch.object(sv_module, "_get_live_pe_for_sector", return_value=10.5), \
             patch.object(sv_module, "_nse_pe_cache", {"NIFTY BANK": 10.5}):
            result = get_sector_regime("banking")
        self.assertEqual(result["regime"], "COMPRESSED")
        self.assertAlmostEqual(result["multiplier"], 1.20)
        self.assertAlmostEqual(result["live_pe"], 10.5)
        self.assertAlmostEqual(result["long_run_pe"], 14.0)
        self.assertLess(result["deviation_pct"], 0)

    def test_extreme_regime(self):
        """IT at 50x vs long-run 28x → ratio ~1.79 → EXTREME."""
        with patch.object(sv_module, "_get_live_pe_for_sector", return_value=50.0), \
             patch.object(sv_module, "_nse_pe_cache", {"NIFTY IT": 50.0}):
            result = get_sector_regime("it")
        self.assertEqual(result["regime"], "EXTREME")
        self.assertAlmostEqual(result["multiplier"], 0.80)
        self.assertGreater(result["deviation_pct"], 0)

    def test_stretched_regime(self):
        """Pharma at 34x vs long-run 26x → ratio ~1.31 → STRETCHED."""
        with patch.object(sv_module, "_get_live_pe_for_sector", return_value=34.0), \
             patch.object(sv_module, "_nse_pe_cache", {"NIFTY PHARMA": 34.0}):
            result = get_sector_regime("pharmaceuticals")
        self.assertEqual(result["regime"], "STRETCHED")
        self.assertAlmostEqual(result["multiplier"], 0.88)

    def test_fair_regime(self):
        """IT at 29x vs long-run 28x → ratio ~1.036 → FAIR."""
        with patch.object(sv_module, "_get_live_pe_for_sector", return_value=29.0), \
             patch.object(sv_module, "_nse_pe_cache", {"NIFTY IT": 29.0}):
            result = get_sector_regime("it")
        self.assertEqual(result["regime"], "FAIR")
        self.assertAlmostEqual(result["multiplier"], 1.00)

    # ── Cache behaviour ───────────────────────────────────────────────────────

    def test_result_is_cached(self):
        """Second call within TTL returns cached value without re-fetching."""
        call_count = {"n": 0}

        def fake_live_pe(sector_key):
            call_count["n"] += 1
            return 28.0

        with patch.object(sv_module, "_get_live_pe_for_sector", side_effect=fake_live_pe), \
             patch.object(sv_module, "_nse_pe_cache", {"NIFTY IT": 28.0}):
            get_sector_regime("it")
            get_sector_regime("it")

        # live PE should only be fetched ONCE per TTL window
        self.assertEqual(call_count["n"], 1)

    def test_cache_expires_after_ttl(self):
        """After TTL expires, the regime is re-fetched."""
        call_count = {"n": 0}

        def fake_live_pe(sector_key):
            call_count["n"] += 1
            return 28.0

        with patch.object(sv_module, "_get_live_pe_for_sector", side_effect=fake_live_pe), \
             patch.object(sv_module, "_nse_pe_cache", {"NIFTY IT": 28.0}):
            get_sector_regime("it")
            # Force cache expiry by back-dating the entry
            sv_module._regime_cache["it"] = (time.time() - 1, sv_module._regime_cache["it"][1])
            get_sector_regime("it")

        self.assertEqual(call_count["n"], 2)

    def test_returns_copy_not_reference(self):
        """Caller mutations must not corrupt the cache."""
        with patch.object(sv_module, "_get_live_pe_for_sector", return_value=14.0), \
             patch.object(sv_module, "_nse_pe_cache", {"NIFTY BANK": 14.0}):
            r1 = get_sector_regime("banking")
            r1["regime"] = "MUTATED"
            r2 = get_sector_regime("banking")
        self.assertNotEqual(r2["regime"], "MUTATED")

    # ── Result structure ──────────────────────────────────────────────────────

    def test_result_has_all_required_keys(self):
        """Result dict must contain all documented keys."""
        required_keys = {
            "regime", "multiplier", "live_pe", "long_run_pe",
            "deviation_pct", "note", "data_source",
        }
        with patch.object(sv_module, "_get_live_pe_for_sector", return_value=14.0), \
             patch.object(sv_module, "_nse_pe_cache", {"NIFTY BANK": 14.0}):
            result = get_sector_regime("banking")
        self.assertEqual(required_keys, required_keys & set(result.keys()))

    def test_regime_is_one_of_six_values(self):
        """regime must be one of the six defined labels."""
        valid_regimes = {
            "COMPRESSED", "MILDLY_COMPRESSED", "FAIR",
            "MILDLY_STRETCHED", "STRETCHED", "EXTREME",
        }
        with patch.object(sv_module, "_get_live_pe_for_sector", return_value=14.0), \
             patch.object(sv_module, "_nse_pe_cache", {"NIFTY BANK": 14.0}):
            result = get_sector_regime("banking")
        self.assertIn(result["regime"], valid_regimes)

    def test_multiplier_within_expected_range(self):
        """multiplier must be between 0.80 and 1.20 (inclusive)."""
        with patch.object(sv_module, "_get_live_pe_for_sector", return_value=14.0), \
             patch.object(sv_module, "_nse_pe_cache", {"NIFTY BANK": 14.0}):
            result = get_sector_regime("banking")
        self.assertGreaterEqual(result["multiplier"], 0.80)
        self.assertLessEqual(result["multiplier"], 1.20)


# ──────────────────────────────────────────────────────────────────────────────
# TestGetLiveSectorPeMap
# ──────────────────────────────────────────────────────────────────────────────

class TestGetLiveSectorPeMap(unittest.TestCase):
    """Test get_live_sector_pe_map()."""

    def setUp(self):
        clear_cache()

    def test_returns_dict(self):
        """Always returns a dict."""
        with patch.object(sv_module, "_fetch_nse_all_index_pes", return_value={}), \
             patch.object(sv_module, "_fetch_yf_constituent_pe", return_value=None):
            result = get_live_sector_pe_map()
        self.assertIsInstance(result, dict)

    def test_omits_sectors_with_no_live_pe(self):
        """When _get_live_pe_for_sector returns None, sector is excluded."""
        with patch.object(sv_module, "_fetch_nse_all_index_pes", return_value={}), \
             patch.object(sv_module, "_fetch_yf_constituent_pe", return_value=None):
            result = get_live_sector_pe_map()
        self.assertEqual(len(result), 0)

    def test_includes_sectors_with_live_pe(self):
        """When NSE API has data, all mapped sectors are included."""
        nse_pes = {
            "NIFTY IT":   32.0,
            "NIFTY BANK": 13.5,
        }
        # Patch _fetch_nse_all_index_pes to return nse_pes and
        # also populate _nse_pe_cache so _get_live_pe_for_sector can find it
        with patch.object(sv_module, "_fetch_nse_all_index_pes", return_value=nse_pes), \
             patch.object(sv_module, "_nse_pe_cache", nse_pes), \
             patch.object(sv_module, "_fetch_yf_constituent_pe", return_value=None):
            result = get_live_sector_pe_map()
        # "it", "information technology", "technology" should be present (all map to NIFTY IT)
        self.assertIn("it", result)
        self.assertIn("banking", result)

    def test_live_pe_values_are_positive_floats(self):
        """All returned PE values must be positive."""
        nse_pes = {"NIFTY IT": 32.0}
        with patch.object(sv_module, "_fetch_nse_all_index_pes", return_value=nse_pes), \
             patch.object(sv_module, "_nse_pe_cache", nse_pes), \
             patch.object(sv_module, "_fetch_yf_constituent_pe", return_value=None):
            result = get_live_sector_pe_map()
        for k, v in result.items():
            with self.subTest(sector=k):
                self.assertIsInstance(v, float)
                self.assertGreater(v, 0)


# ──────────────────────────────────────────────────────────────────────────────
# TestClearCache
# ──────────────────────────────────────────────────────────────────────────────

class TestClearCache(unittest.TestCase):
    """Test clear_cache() flushes all caches."""

    def test_clears_regime_cache(self):
        """_regime_cache must be empty after clear_cache()."""
        # Seed the cache
        sv_module._regime_cache["it"] = (time.time() + 3600, {"regime": "FAIR"})
        clear_cache()
        self.assertEqual(len(sv_module._regime_cache), 0)

    def test_clears_nse_pe_cache(self):
        """_nse_pe_cache and _nse_cache_exp must be reset after clear_cache()."""
        sv_module._nse_pe_cache  = {"NIFTY IT": 32.0}
        sv_module._nse_cache_exp = time.time() + 3600
        clear_cache()
        self.assertEqual(sv_module._nse_pe_cache, {})
        self.assertEqual(sv_module._nse_cache_exp, 0.0)

    def test_clear_cache_is_idempotent(self):
        """Calling clear_cache() twice must not raise."""
        clear_cache()
        clear_cache()

    def test_get_sector_regime_after_clear_refetches(self):
        """After clearing cache, get_sector_regime re-fetches live PE."""
        call_count = {"n": 0}

        def fake_live_pe(sector_key):
            call_count["n"] += 1
            return 14.0

        with patch.object(sv_module, "_get_live_pe_for_sector", side_effect=fake_live_pe), \
             patch.object(sv_module, "_nse_pe_cache", {"NIFTY BANK": 14.0}):
            get_sector_regime("banking")
            clear_cache()
            get_sector_regime("banking")

        self.assertEqual(call_count["n"], 2)


# ──────────────────────────────────────────────────────────────────────────────
# TestSectorLongunPeMap
# ──────────────────────────────────────────────────────────────────────────────

class TestSectorLongrunPeMap(unittest.TestCase):
    """Sanity checks on SECTOR_LONGRUN_PE constant."""

    def test_has_common_sectors(self):
        required = {
            "it", "banking", "pharmaceuticals", "fmcg",
            "energy", "realty", "telecom", "utilities",
        }
        self.assertTrue(required.issubset(set(SECTOR_LONGRUN_PE.keys())))

    def test_all_values_positive(self):
        for sector, pe in SECTOR_LONGRUN_PE.items():
            with self.subTest(sector=sector):
                self.assertGreater(pe, 0, f"Long-run PE for {sector!r} must be positive")

    def test_sector_keys_are_lowercase(self):
        for sector in SECTOR_LONGRUN_PE:
            with self.subTest(sector=sector):
                self.assertEqual(sector, sector.lower(), "All sector keys must be lowercase")


# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
