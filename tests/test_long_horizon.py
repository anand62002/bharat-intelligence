"""
tests/test_long_horizon.py — long-horizon multibagger track (P8-E).

Pins the screen's gates and scoring, and the paper book's evidence-gated sizing
and multi-year exit rules.
"""

from unittest.mock import MagicMock

import pytest

from agents import long_horizon_book as book
from agents import multibagger_screener as mb


def _snap(**kw):
    s = {"market_cap": 2_000 * 1e7, "roce": 26.0, "revenue_cagr_3y": 22.0, "revenue_growth": 20.0,
         "debt_equity": 0.2, "promoter_pledging": 0.0, "promoter_holding": 62.0, "pe": 24.0,
         "eps_cagr_3y": 28.0, "fii_holding_pct": 4.0, "dii_holding_pct": 6.0}
    s.update(kw)
    return s


def _warren(**kw):
    w = {"signal": "AVOID", "score": 50, "roce_avg_10yr": 24.0, "ten_year_eps_cagr": 21.0,
         "current_price": 500.0}
    w.update(kw)
    return w


# ── prefilter ────────────────────────────────────────────────────────────────

def test_prefilter_passes_a_small_quality_compounder():
    assert mb.prefilter(_snap()) == (True, [])


@pytest.mark.parametrize("field,value,fragment", [
    ("market_cap", 50_000 * 1e7, "market cap"),     # too big to 10x easily
    ("market_cap", 100 * 1e7, "market cap"),        # too small
    ("roce", 9.0, "ROCE"),
    ("revenue_cagr_3y", 5.0, "revenue growth"),
    ("debt_equity", 1.8, "debt/equity"),
    ("promoter_pledging", 25.0, "pledging"),
    ("promoter_holding", 20.0, "promoter holding"),
    ("pe", -4.0, "PE"),                              # loss-maker
    ("pe", 95.0, "PE"),
])
def test_prefilter_gates(field, value, fragment):
    ok, fails = mb.prefilter(_snap(**{field: value}))
    assert not ok and any(fragment in f for f in fails)


def test_prefilter_tolerates_missing_optional_fields():
    ok, _ = mb.prefilter(_snap(debt_equity=None, promoter_pledging=None, promoter_holding=None))
    assert ok


# ── deep_check ignores the DCF verdict, needs durable quality ───────────────

def test_deep_check_ignores_valuation_driven_avoid():
    # GABRIEL-like: AVOID purely on valuation, but 10y quality is strong
    assert mb.deep_check(_warren(signal="AVOID"))[0] is True


@pytest.mark.parametrize("w,frag", [
    (_warren(roce_avg_10yr=8.0), "10y ROCE"),
    (_warren(ten_year_eps_cagr=4.0), "10y EPS"),
    (_warren(signal="INSUFFICIENT_DATA"), "insufficient"),
    (None, "unavailable"),
])
def test_deep_check_rejects_undurable_quality(w, frag):
    ok, why = mb.deep_check(w)
    assert not ok and frag in why


# ── score ────────────────────────────────────────────────────────────────────

def test_score_components_sum_and_cap_at_100():
    s, comps = mb.score(_snap(), _warren())
    assert s == sum(c["points"] for c in comps.values())
    assert s <= 100 and sum(c["max"] for c in comps.values()) == 100


def test_quality_uses_weaker_of_today_and_ten_year_roce():
    _, high = mb.score(_snap(roce=35.0), _warren(roce_avg_10yr=35.0))
    _, one_good_year = mb.score(_snap(roce=35.0), _warren(roce_avg_10yr=16.0))
    assert high["quality"]["points"] > one_good_year["quality"]["points"]


def test_smaller_and_cheaper_for_growth_scores_higher():
    small_cheap, _ = mb.score(_snap(market_cap=800 * 1e7, pe=15.0), _warren())
    big_dear, _ = mb.score(_snap(market_cap=12_000 * 1e7, pe=55.0), _warren())
    assert small_cheap > big_dear


def test_thesis_mentions_the_numbers():
    _, comps = mb.score(_snap(), _warren())
    t = mb.thesis("ABC", comps)
    assert "ABC" in t and "ROCE" in t and "Cr" in t


# ── book: evidence-gated sizing ──────────────────────────────────────────────

def _picks(n, hit_every):
    return [{"alpha_t365": 0.1 if i % hit_every == 0 else -0.1} for i in range(n)]


def test_conviction_locked_without_track_record():
    rec = book.track_record([])
    assert not rec["conviction_unlocked"]
    assert book.allocation_for(95, rec) == ("BASE", book.BASE_ALLOCATION)


def test_conviction_locked_when_record_is_weak():
    rec = book.track_record(_picks(30, 2))           # 50% beat Nifty
    assert not rec["conviction_unlocked"]


def test_conviction_unlocks_only_with_enough_good_outcomes():
    rec = book.track_record(_picks(25, 1))           # 100% beat Nifty, 25 resolved
    assert rec["conviction_unlocked"]
    assert book.allocation_for(90, rec) == ("CONVICTION", book.CONVICTION_ALLOCATION)
    assert book.allocation_for(75, rec) == ("BASE", book.BASE_ALLOCATION)   # score still matters


def test_too_few_resolved_stays_locked_even_if_perfect():
    assert not book.track_record(_picks(5, 1))["conviction_unlocked"]


# ── book: exits ──────────────────────────────────────────────────────────────

def test_hard_stop_is_wide():
    assert book.next_stop(100.0, 100.0, None) == 65.0
    assert book.exit_reason(64.0, 100.0, 65.0, "ACTIVE") == "HARD_STOP"
    assert book.exit_reason(70.0, 100.0, 65.0, "ACTIVE") is None


def test_trailing_starts_only_after_doubling_and_never_lowers():
    assert book.next_stop(100.0, 190.0, 65.0) == 65.0            # not doubled yet
    assert book.next_stop(100.0, 300.0, 65.0) == 210.0           # 30% below a 3× peak
    assert book.next_stop(100.0, 250.0, 210.0) == 210.0          # peak record keeps it
    assert book.exit_reason(205.0, 100.0, 210.0, "ACTIVE") == "TRAILING_STOP"


def test_broken_thesis_exits_regardless_of_price():
    assert book.exit_reason(500.0, 100.0, 65.0, "THESIS_BROKEN") == "THESIS_BROKEN"


# ── graceful when the migration hasn't been applied ──────────────────────────

def test_summary_flags_missing_migration():
    client = MagicMock()
    client.table.return_value.select.return_value.order.return_value.limit.return_value.execute.side_effect = \
        Exception("PGRST205 Could not find the table 'public.long_horizon_picks'")
    out = book.get_summary(client)
    assert out["migration_needed"] is True and out["picks"] == []


def test_screen_does_not_write_when_migration_missing():
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.execute.side_effect = \
        Exception("PGRST205 Could not find the table")
    out = mb.run_screen(symbols=["ABC"], dry_run=False, client=client)
    assert any("migration needed" in e for e in out["errors"])
    client.table.return_value.insert.assert_not_called()


def test_small_base_growth_is_capped():
    # 85%/yr EPS growth off a tiny base must not score better than a sustained 40%
    explosive, c1 = mb.score(_snap(eps_cagr_3y=85.0), _warren(ten_year_eps_cagr=85.0))
    sustained, c2 = mb.score(_snap(eps_cagr_3y=40.0), _warren(ten_year_eps_cagr=40.0))
    assert explosive == sustained
    assert c1["growth"]["eps_growth_used"] == mb.EPS_GROWTH_CAP


def test_pick_threshold_is_strict():
    assert mb.PICK_THRESHOLD >= 80
