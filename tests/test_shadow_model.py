"""
tests/test_shadow_model.py — shadow probability model (WP-017, ADR-006), on synthetic data.

A planted signal must be recovered; pure noise must not beat the base rate;
walk-forward folds must never leak a label that didn't exist yet.
"""
import math
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents import shadow_model as sm  # noqa: E402

START = date(2026, 1, 5)          # a Monday


def synth(weeks=16, per_week=60, signal=3.0, seed=7, prescreen_effect=0.0):
    """Ledger-shaped rows. y depends on RSI (and optionally on pre-screen pass) only."""
    rng = np.random.default_rng(seed)
    rows, i = [], 0
    for w in range(weeks):
        for k in range(per_week):
            d = START + timedelta(days=7 * w + int(k % 5))
            rsi = float(rng.uniform(30, 70))
            passed = bool(rng.random() < 0.3)
            logit = signal * (rsi - 50) / 10 + (prescreen_effect if passed else 0.0)
            y = rng.random() < 1 / (1 + math.exp(-logit))
            rows.append({
                "run_date": d.isoformat(), "symbol": f"S{i:05d}", "source": "discovery",
                "stage": "PRESCREEN_PASS" if passed else "SCREENED",
                "features": {"rsi": rsi, "passed": passed, "pe": float(rng.uniform(5, 60)),
                             "sector_pe": 25.0, "revenue_growth": None,          # always missing
                             "above_ema200": bool(rng.random() < 0.5)},
                "agent_signals": ({"technical": {"score": float(rng.uniform(0, 100))}}
                                  if passed else {}),
                "signal_score": float(rng.uniform(40, 90)) if passed else None,
                "alpha_t30": (0.05 if y else -0.05) + float(rng.normal(0, 0.01)),
            })
            i += 1
    return rows


# ── refusal ───────────────────────────────────────────────────────────────────

def test_refuses_below_300_rows():
    res = sm.evaluate(synth(weeks=10, per_week=20), horizon=30)        # 200 rows
    assert res["sufficient"] is False
    assert sm.to_markdown(res).splitlines()[2].startswith("insufficient data: 200 rows, 10 weeks")


def test_refuses_below_8_weeks():
    res = sm.evaluate(synth(weeks=7, per_week=60), horizon=30)          # 420 rows, 7 weeks
    assert res["sufficient"] is False and (res["rows"], res["weeks"]) == (420, 7)
    assert "insufficient data: 420 rows, 7 weeks" in sm.to_markdown(res)


def test_unusable_rows_are_dropped_and_counted():
    rows = synth(weeks=2, per_week=5)
    rows[0]["alpha_t30"] = None
    rows[1]["run_date"] = "not-a-date"
    out, dropped = sm.prepare(rows, 30)
    assert dropped == 2 and len(out) == 8


# ── planted signal / noise ────────────────────────────────────────────────────

def test_planted_signal_is_recovered():
    res = sm.evaluate(synth(signal=3.0), horizon=30, dedupe_days=0)
    assert res["sufficient"]
    q = res["model_quintiles"]
    assert q[4][0] - q[0][0] > 0.5                      # top quintile beats bottom by >50 pts
    assert res["brier_diff"][2] < 0                     # better than base rate, 90 % upper bound < 0
    assert res["promotion"]["top_quintile_lower_bound_above_50"] is True
    assert res["promotion"]["brier_below_baseline"] is True


def test_pure_noise_is_no_better_than_baseline():
    res = sm.evaluate(synth(signal=0.0, seed=11), horizon=30, dedupe_days=0)
    assert res["sufficient"]
    d, lo, hi = res["brier_diff"]
    assert hi >= 0                                      # not significantly better than the base rate
    assert res["promotion"]["eligible"] is False


def test_prescreen_pass_vs_fail_difference_is_measured():
    res = sm.evaluate(synth(signal=0.0, prescreen_effect=1.5, seed=3), horizon=30, dedupe_days=0)
    ps = res["incumbents"]["prescreen"]
    assert ps["pass"][0] > ps["fail"][0]
    assert ps["diff"][1] > 0                            # 90 % lower bound of (pass − fail) above 0


# ── leakage ───────────────────────────────────────────────────────────────────

def test_folds_never_leak():
    rows, _ = sm.prepare(synth(weeks=14, per_week=40), 30)
    seen_train: list[list] = []

    class Recording(sm.ShadowModel):
        def fit(self, train):
            seen_train.append(train)
            return super().fit(train)

    folds = sm.walk_forward(rows, 30, model_factory=Recording)
    assert folds and len(folds) == len(seen_train)
    for f, train in zip(folds, seen_train):
        test_ids = {(r.run_date, r.symbol) for r in f.test}
        train_ids = {(r.run_date, r.symbol) for r in train}
        assert not test_ids & train_ids                                  # no row in both
        assert all(sm.week_of(r.run_date) == f.week for r in f.test)    # one test week
        assert all(r.run_date + timedelta(days=30) < f.week for r in train)   # labels known in time
        assert f.train_max_label_date < f.week


def test_dedupe_one_call_per_symbol_per_30_days():
    rows, _ = sm.prepare([
        {"run_date": "2026-01-01", "symbol": "A", "alpha_t30": 0.1},
        {"run_date": "2026-01-10", "symbol": "A", "alpha_t30": 0.1},
        {"run_date": "2026-02-01", "symbol": "A", "alpha_t30": -0.1},
        {"run_date": "2026-01-10", "symbol": "B", "alpha_t30": 0.1},
    ], 30)
    kept = sm.dedupe(rows, 30)
    assert sorted((r.symbol, r.run_date.isoformat()) for r in kept) == [
        ("A", "2026-01-01"), ("A", "2026-02-01"), ("B", "2026-01-10")]


# ── components ────────────────────────────────────────────────────────────────

def test_missing_values_get_an_indicator_not_zero():
    rows, _ = sm.prepare(synth(weeks=1, per_week=10), 30)
    d = sm.Design().fit(rows)
    x = d._assemble(d._raw(rows))
    i = sm.FEATURE_NAMES.index("revenue_growth")
    assert np.all(x[:, len(sm.FEATURE_NAMES) + i] == 1.0)     # indicator set on every row


def test_isotonic_is_monotone_and_calibrated():
    rng = np.random.default_rng(0)
    s = rng.uniform(0, 1, 2000)
    y = (rng.uniform(0, 1, 2000) < s).astype(float)
    iso = sm.Isotonic().fit(s, y)
    p = iso.predict(np.linspace(0, 1, 50))
    assert np.all(np.diff(p) >= -1e-12)
    assert abs(iso.predict(np.array([0.8]))[0] - 0.8) < 0.1


def test_logistic_recovers_sign():
    rng = np.random.default_rng(1)
    x = np.hstack([np.ones((1000, 1)), rng.normal(size=(1000, 2))])
    y = (rng.uniform(size=1000) < 1 / (1 + np.exp(-(2 * x[:, 1] - 1 * x[:, 2])))).astype(float)
    b = sm.fit_logistic(x, y)
    assert b[1] > 1 and b[2] < -0.5


def test_cluster_interval_is_wider_than_naive_for_correlated_weeks():
    weeks = [date(2026, 1, 5) + timedelta(days=7 * (i // 50)) for i in range(500)]
    y = np.array([1.0 if (i // 50) % 2 else 0.0 for i in range(500)])   # whole weeks win or lose
    rate, lo, hi, n = sm.cluster_rate(y, weeks)
    naive = 1.6449 * math.sqrt(0.25 / 500)
    assert hi - lo > 4 * naive


# ── acceptance: script output, no production effect ─────────────────────────

class _Client:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        assert name == "candidates"
        return _Q(self.rows)


class _Q:
    def __init__(self, rows):
        self.rows, self.h, self.rng = rows, None, (0, 10 ** 9)

    def select(self, *a): return self
    def eq(self, *a): return self
    def order(self, *a): return self

    @property
    def not_(self):
        return self

    def is_(self, col, v):
        self.h = col
        return self

    def range(self, a, b):
        self.rng = (a, b)
        return self

    def execute(self):
        data = [r for r in self.rows if r.get(self.h) is not None]
        self.data = data[self.rng[0]:self.rng[1] + 1]
        return self


def test_script_prints_insufficient_line(capsys):
    from scripts.evaluate_shadow_model import main
    assert main(["--horizon", "30"], client=_Client(synth(weeks=3, per_week=10))) == 0
    assert "insufficient data: 30 rows, 3 weeks" in capsys.readouterr().out


def test_script_prints_report_when_enough(capsys):
    from scripts.evaluate_shadow_model import main
    assert main(["--horizon", "30"], client=_Client(synth())) == 0
    out = capsys.readouterr().out
    assert "Beat-Nifty rate by quintile" in out and "ADR-006 promotion rule" in out


def test_no_production_code_imports_the_shadow_model():
    hits = subprocess.run(
        ["git", "grep", "-n", "shadow_model", "--", "scheduler/", "agents/discovery_screener.py",
         "worker.py", "api/"], cwd=ROOT, capture_output=True, text=True).stdout
    assert hits == ""
