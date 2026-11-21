"""
tests/test_backtest_summary.py — the backtest is labelled honestly (WP-022, ADR-006).

GET /api/backtest/summary replays one technical rule on a survivorship-biased
universe. The response must say so, and must give the two misleading columns
honest names (worstTrade, alphaMeanOverStd) while keeping the old keys for the
dashboard for one release.
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Imported at collection time: api.main builds its Supabase client at import,
# which the WP-008 guard blocks inside a test.
import api.main as api  # noqa: E402
from agents import backtester  # noqa: E402

ROW = {"run_date": "2026-11-01", "universe": "NIFTY500", "period_start": "2023-01-01",
       "period_end": "2024-12-31", "split_type": "TEST", "total_signals": 412,
       "hit_rate_90d": 64.1, "avg_alpha_90d": 2.3, "avg_alpha_180d": 3.1,
       "sharpe_ratio": 0.21, "max_drawdown": -0.42, "win_loss_ratio": 1.4,
       "created_at": "2026-11-01T02:15:00Z"}


class _Q:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *a): return self
    def eq(self, col, val):
        self.rows = [r for r in self.rows if r.get(col) == val]
        return self
    def order(self, *a, **k): return self
    def limit(self, n):
        self.rows = self.rows[:n]
        return self
    def execute(self):
        return type("R", (), {"data": self.rows})()


class _DB:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        assert name == "backtest_results"
        return _Q(list(self.rows))


def _call(monkeypatch, rows, split="TEST"):
    monkeypatch.setattr(api, "_supabase", _DB(rows))
    return asyncio.run(api.get_backtest_summary(split=split, limit=5, _=None))


def test_response_says_what_the_backtest_is(monkeypatch):
    out = _call(monkeypatch, [ROW])
    assert out["strategy"] == "technical_rule_only"
    assert out["survivorship_bias"] is True
    assert "Survivorship-biased" in out["caveat"] and "not the live" in out["caveat"]


def test_rows_carry_honest_aliases_and_keep_old_keys(monkeypatch):
    (row,) = _call(monkeypatch, [ROW])["results"]
    assert row["worstTrade"] == -0.42 and row["max_drawdown"] == -0.42
    assert row["alphaMeanOverStd"] == 0.21 and row["sharpe_ratio"] == 0.21


def test_all_splits_and_empty_results_still_labelled(monkeypatch):
    rows = [dict(ROW, split_type=s) for s in ("TEST", "TRAIN", "FULL")]
    out = _call(monkeypatch, rows, split="ALL")
    assert out["count"] == 3 and all("worstTrade" in r for r in out["results"])
    empty = _call(monkeypatch, [], split="TEST")
    assert empty["results"] == [] and empty["survivorship_bias"] is True


def test_null_metrics_pass_through():
    row = api._label_backtest_row(dict(ROW, sharpe_ratio=None, max_drawdown=None))
    assert row["worstTrade"] is None and row["alphaMeanOverStd"] is None


def test_backtester_docstring_is_corrected():
    doc = " ".join(backtester.__doc__.split())
    assert "SURVIVORSHIP-BIASED" in doc and "WORST SINGLE TRADE" in doc
    assert "not annualised" in doc
    assert backtester.SURVIVORSHIP_BIAS is True


def test_aggregate_semantics_match_the_labels():
    trades = [{"alpha_90d": a, "alpha_180d": None, "return_pct": r, "hit": a > 0}
              for a, r in ((5.0, 8.0), (-3.0, -12.0), (1.0, 2.0))]
    m = backtester._aggregate(trades)
    assert m["max_drawdown"] == -0.12                     # worst single trade, as a fraction
    mean = (5 - 3 + 1) / 3
    sd = (((5 - mean) ** 2 + (-3 - mean) ** 2 + (1 - mean) ** 2) / 2) ** 0.5
    assert m["sharpe_ratio"] == round(mean / sd, 3)       # mean/std of per-trade alpha
