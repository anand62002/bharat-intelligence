"""
tests/test_call_calibration.py — calibrated discovery confidence.

The raw discovery score was published as "confidence" but behaved inversely to
outcomes (stated 90%+ beat Nifty 40% of the time). Confidence is now the
historical hit rate of the score's band; these tests pin that contract.
"""

from datetime import date, timedelta

from agents.call_calibration import (
    MIN_RESOLVED, NEUTRAL, Calibration, build_calibration, signal_score_of,
)


def _pair(symbol, day_offset, score, alpha):
    d = (date(2026, 1, 1) + timedelta(days=day_offset)).isoformat()
    return ({"symbol": symbol, "metadata": {"signal_score": score}},
            {"symbol": symbol, "rec_date": d, "alpha_t90": alpha})


def _many(n, score, hit_every):
    """n distinct symbols in one band; every `hit_every`-th beats Nifty."""
    return [_pair(f"S{score}_{i}", i, score, 0.05 if i % hit_every == 0 else -0.05) for i in range(n)]


def test_no_track_record_is_neutral():
    cal = build_calibration(_many(MIN_RESOLVED - 1, 85, 2))
    assert not cal.has_history
    assert cal.confidence(95) == NEUTRAL


def test_bands_reflect_outcomes_not_the_raw_score():
    # High raw scores that mostly lose, mid scores that mostly win.
    pairs = _many(40, 95, 4) + _many(40, 75, 1)
    cal = build_calibration(pairs)
    assert cal.has_history
    assert cal.confidence(95) < 50 < cal.confidence(75)


def test_thin_band_is_shrunk_toward_overall():
    pairs = _many(60, 75, 2) + [_pair("ONLY", 500, 95, 0.1)]   # 1 call in the 90+ band, a win
    cal = build_calibration(pairs)
    assert cal.confidence(95) < 100          # one lucky call can't claim 100%
    assert abs(cal.confidence(95) - cal.overall) < 10


def test_repeat_calls_on_one_symbol_count_once_per_window():
    # INFY re-discovered 12 times within days must not count as 12 independent bets.
    repeats = [_pair("INFY", i, 85, -0.05) for i in range(12)]
    cal = build_calibration(_many(MIN_RESOLVED, 75, 2) + repeats)
    assert next(b for b in cal.bands if b["lo"] == 80)["n"] == 1


def test_unresolved_outcomes_are_ignored():
    pairs = _many(MIN_RESOLVED, 75, 2) + [({"symbol": "X", "metadata": {"signal_score": 75}},
                                          {"symbol": "X", "rec_date": "2026-05-01", "alpha_t90": None})]
    assert build_calibration(pairs).n == MIN_RESOLVED


def test_signal_score_prefers_metadata_then_legacy_columns():
    assert signal_score_of({"metadata": {"signal_score": 81}, "upside_confidence": 41}) == 81
    # Rows written before 2026-11-10 kept the raw score in upside_confidence.
    assert signal_score_of({"metadata": {}, "upside_confidence": 88}) == 88
    assert signal_score_of({"metadata": {}, "confidence": 70}) == 70
    assert signal_score_of({}) is None


def test_calibration_never_raises_on_bad_client():
    from agents.call_calibration import load_calibration

    class Boom:
        def table(self, *_):
            raise RuntimeError("db down")

    cal = load_calibration(Boom())
    assert isinstance(cal, Calibration) and cal.confidence(90) == NEUTRAL
