"""
agents/shadow_model.py — shadow probability model on the candidate ledger (WP-017, ADR-006).

Why this exists
---------------
The calibration bands behind discovery's confidence (41.6 / 53.3 / 43.9 /
37.3 % on n = 13–38) can't separate good calls from bad. The candidates ledger
(WP-012..014) records every screened symbol with its decision-time features and
labels it with 30/90-day alpha. That gives roughly ten times more rows to learn from.

This is a SHADOW model. Nothing in production reads it: no scheduler job, no
discovery code, no API. It exists to be evaluated under ADR-006, and it can only
replace anything through that ADR's promotion rule (≥300 labelled rows across
≥8 weeks; top-quintile 90 % lower bound above 50 %; beats the incumbent out of
sample; Brier below the base-rate baseline).

Method
------
- Target: beat the Nifty at the horizon (alpha_tN > 0).
- Features: a small FIXED vector from the ledger's pre-screen features and the
  agents' scores. A missing value is imputed with the training median, and a
  separate indicator column records that it was missing. Missing data is
  information here (e.g. no fundamentals), never silently zero.
- Model: L2-regularised logistic regression (Newton / IRLS, numpy only; the
  intercept is not penalised), then isotonic calibration (pool-adjacent-
  violators) fitted on the later 25 % of the training window.
- Evaluation: walk-forward by week. Each test fold is one calendar week (by
  run_date). Training uses only rows whose label already existed before that
  week started (run_date + horizon < week start), so no future outcome leaks
  in. A row is never in both sets.
- Intervals: 90 % intervals use a cluster-robust ratio estimator with weekly
  clusters. Rows from the same week are correlated (same market), so counting
  them as independent would overstate certainty.
- Incumbents get the same treatment: the live signal_score, the equal-weight
  agent composite, and pre-screen pass vs fail.

Never raises on bad rows: unusable rows are dropped and counted.
"""

from __future__ import annotations

import logging
import math
import warnings
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Iterable, Optional

import numpy as np

log = logging.getLogger(__name__)

MIN_ROWS = 300            # ADR-006 promotion rule
MIN_WEEKS = 8
MIN_TRAIN_WEEKS = 4       # don't test a fold until at least this many labelled training weeks exist
MIN_TRAIN_ROWS = 50
L2 = 1.0                  # ridge strength on standardised features
Z90 = 1.6449              # two-sided 90 %

AGENTS = ("technical", "fundamental", "sentiment", "institutional", "macro",
          "historical_rag", "commodities")

# Fixed feature vector: name → extractor(row) → float | None.
FEATURES: dict[str, Callable[[dict], Optional[float]]] = {}


def _f(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _feat(row: dict, key: str) -> Optional[float]:
    return _f((row.get("features") or {}).get(key))


def _pe_vs_sector(row: dict) -> Optional[float]:
    pe, spe = _feat(row, "pe"), _feat(row, "sector_pe")
    return pe / spe if pe and spe and pe > 0 and spe > 0 else None


def _agent_score(name: str) -> Callable[[dict], Optional[float]]:
    return lambda row: _f(((row.get("agent_signals") or {}).get(name) or {}).get("score"))


FEATURES.update({
    "rsi": lambda r: _feat(r, "rsi"),
    "above_ema200": lambda r: _feat(r, "above_ema200"),
    "pe_vs_sector": _pe_vs_sector,
    "revenue_growth": lambda r: _feat(r, "revenue_growth"),
    "institutional_pct": lambda r: _feat(r, "institutional_pct"),
    "filters_passed": lambda r: _feat(r, "filters_passed"),
    "dvm_composite": lambda r: _feat(r, "dvm_composite"),
    **{f"agent_{a}": _agent_score(a) for a in AGENTS},
})
FEATURE_NAMES = tuple(FEATURES)


# ─────────────────────────────────────────────────────────────────────────────
# Rows
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Row:
    run_date: date
    symbol: str
    y: int                     # 1 = beat the Nifty
    alpha: float
    raw: dict = field(repr=False)


def week_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def prepare(rows: Iterable[dict], horizon: int) -> tuple[list[Row], int]:
    """Labelled ledger rows → Row objects. Returns (rows, dropped)."""
    out, dropped = [], 0
    for r in rows:
        a = _f(r.get(f"alpha_t{horizon}"))
        try:
            d = r["run_date"] if isinstance(r["run_date"], date) else date.fromisoformat(str(r["run_date"])[:10])
        except (KeyError, ValueError, TypeError):
            d = None
        if a is None or d is None or not r.get("symbol"):
            dropped += 1
            continue
        out.append(Row(d, str(r["symbol"]), int(a > 0), a, r))
    out.sort(key=lambda x: (x.run_date, x.symbol))
    if dropped:
        log.warning("shadow_model: %d labelled rows unusable (no alpha/date/symbol)", dropped)
    return out, dropped


def dedupe(rows: list[Row], days: int = 30) -> list[Row]:
    """ADR-006 primary metric: one call per symbol per `days` (the first one counts)."""
    last: dict[str, date] = {}
    keep = []
    for r in rows:
        prev = last.get(r.symbol)
        if prev is None or (r.run_date - prev).days >= days:
            keep.append(r)
            last[r.symbol] = r.run_date
    return keep


def sufficiency(rows: list[Row]) -> tuple[bool, int, int]:
    weeks = len({week_of(r.run_date) for r in rows})
    return len(rows) >= MIN_ROWS and weeks >= MIN_WEEKS, len(rows), weeks


# ─────────────────────────────────────────────────────────────────────────────
# Model
# ─────────────────────────────────────────────────────────────────────────────

class Design:
    """Feature matrix with explicit missing indicators; imputation and scaling fitted on train only."""

    def __init__(self, names: tuple[str, ...] = FEATURE_NAMES):
        self.names = names
        self.median: Optional[np.ndarray] = None
        self.mean: Optional[np.ndarray] = None
        self.std: Optional[np.ndarray] = None

    def _raw(self, rows: list[Row]) -> np.ndarray:
        def val(n: str, r: Row) -> float:
            v = FEATURES[n](r.raw)
            return np.nan if v is None else v
        return np.array([[val(n, r) for n in self.names] for r in rows],
                        dtype=float).reshape(len(rows), len(self.names))

    def fit(self, rows: list[Row]) -> "Design":
        raw = self._raw(rows)
        with warnings.catch_warnings():      # an all-missing column is expected → median 0
            warnings.simplefilter("ignore", RuntimeWarning)
            med = np.nanmedian(raw, axis=0) if len(rows) else np.zeros(len(self.names))
        self.median = np.where(np.isfinite(med), med, 0.0)
        x = self._assemble(raw)
        self.mean = x.mean(axis=0)
        sd = x.std(axis=0)
        self.std = np.where(sd > 1e-9, sd, 1.0)
        return self

    def _assemble(self, raw: np.ndarray) -> np.ndarray:
        miss = np.isnan(raw)
        vals = np.where(miss, self.median, raw)
        return np.hstack([vals, miss.astype(float)])

    def transform(self, rows: list[Row]) -> np.ndarray:
        x = (self._assemble(self._raw(rows)) - self.mean) / self.std
        return np.hstack([np.ones((len(rows), 1)), x])          # intercept first


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))


def fit_logistic(x: np.ndarray, y: np.ndarray, l2: float = L2, iters: int = 50) -> np.ndarray:
    """L2-regularised logistic regression by Newton/IRLS. Intercept (column 0) unpenalised."""
    beta = np.zeros(x.shape[1])
    pen = np.full(x.shape[1], l2)
    pen[0] = 0.0
    for _ in range(iters):
        p = _sigmoid(x @ beta)
        w = p * (1 - p) + 1e-9
        grad = x.T @ (p - y) + pen * beta
        hess = (x * w[:, None]).T @ x + np.diag(pen) + 1e-9 * np.eye(len(beta))
        step = np.linalg.solve(hess, grad)
        beta -= step
        if np.max(np.abs(step)) < 1e-8:
            break
    return beta


class Isotonic:
    """Monotone step-function calibration by pool-adjacent-violators."""

    def fit(self, s: np.ndarray, y: np.ndarray) -> "Isotonic":
        order = np.argsort(s, kind="mergesort")
        s, y = s[order], y[order].astype(float)
        blocks: list[list[float]] = []          # [sum_y, n, max_s]
        for si, yi in zip(s, y):
            blocks.append([yi, 1.0, si])
            while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
                sy, n, ms = blocks.pop()
                blocks[-1][0] += sy
                blocks[-1][1] += n
                blocks[-1][2] = ms
        self.edges = np.array([b[2] for b in blocks])
        self.values = np.array([b[0] / b[1] for b in blocks])
        return self

    def predict(self, s: np.ndarray) -> np.ndarray:
        idx = np.searchsorted(self.edges, s, side="left")
        return self.values[np.clip(idx, 0, len(self.values) - 1)]


class ShadowModel:
    def __init__(self, l2: float = L2, calib_share: float = 0.25):
        self.l2, self.calib_share = l2, calib_share

    def fit(self, rows: list[Row]) -> "ShadowModel":
        dates = sorted({r.run_date for r in rows})
        cut = dates[int(len(dates) * (1 - self.calib_share))] if len(dates) >= 4 else None
        fit_rows = [r for r in rows if cut is None or r.run_date < cut] or rows
        cal_rows = [r for r in rows if cut is not None and r.run_date >= cut]
        self.design = Design().fit(fit_rows)
        y = np.array([r.y for r in fit_rows], dtype=float)
        self.beta = fit_logistic(self.design.transform(fit_rows), y, self.l2)
        self.iso = None
        if len(cal_rows) >= 20 and len({r.y for r in cal_rows}) == 2:
            s = _sigmoid(self.design.transform(cal_rows) @ self.beta)
            self.iso = Isotonic().fit(s, np.array([r.y for r in cal_rows]))
        return self

    def predict(self, rows: list[Row]) -> np.ndarray:
        p = _sigmoid(self.design.transform(rows) @ self.beta)
        return np.clip(self.iso.predict(p) if self.iso is not None else p, 0.001, 0.999)


# ─────────────────────────────────────────────────────────────────────────────
# Walk-forward evaluation
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Fold:
    week: date
    train_n: int
    test: list[Row]
    pred: np.ndarray
    base_rate: float
    train_max_label_date: date


def walk_forward(rows: list[Row], horizon: int, min_train_weeks: int = MIN_TRAIN_WEEKS,
                 model_factory: Callable[[], ShadowModel] = ShadowModel) -> list[Fold]:
    """Weekly test folds; train only on rows whose label existed before the fold's week."""
    folds = []
    for wk in sorted({week_of(r.run_date) for r in rows}):
        train = [r for r in rows if r.run_date + timedelta(days=horizon) < wk]
        test = [r for r in rows if week_of(r.run_date) == wk]
        if len({week_of(r.run_date) for r in train}) < min_train_weeks or len(train) < MIN_TRAIN_ROWS \
                or len({r.y for r in train}) < 2 or not test:
            continue
        m = model_factory().fit(train)
        folds.append(Fold(wk, len(train), test, m.predict(test),
                          float(np.mean([r.y for r in train])),
                          max(r.run_date for r in train) + timedelta(days=horizon)))
    return folds


# ─────────────────────────────────────────────────────────────────────────────
# Statistics with weekly clusters
# ─────────────────────────────────────────────────────────────────────────────

def cluster_rate(y: np.ndarray, weeks: list[date]) -> tuple[float, float, float, int]:
    """Rate with a 90 % interval from a cluster-robust ratio estimator (clusters = weeks)."""
    n = len(y)
    if n == 0:
        return float("nan"), float("nan"), float("nan"), 0
    rate = float(np.mean(y))
    groups: dict[date, list[float]] = {}
    for yi, w in zip(y, weeks):
        groups.setdefault(w, []).append(float(yi))
    g = len(groups)
    if g < 2:
        return rate, 0.0, 1.0, n
    resid = sum((sum(v) - rate * len(v)) ** 2 for v in groups.values())
    se = math.sqrt(g / (g - 1) * resid) / n
    return rate, max(0.0, rate - Z90 * se), min(1.0, rate + Z90 * se), n


def cluster_mean(d: np.ndarray, weeks: list[date]) -> tuple[float, float, float]:
    """Mean of d with a 90 % weekly-cluster interval."""
    if len(d) == 0:
        return float("nan"), float("nan"), float("nan")
    m = float(np.mean(d))
    groups: dict[date, list[float]] = {}
    for di, w in zip(d, weeks):
        groups.setdefault(w, []).append(float(di))
    g = len(groups)
    if g < 2:
        return m, float("-inf"), float("inf")
    resid = sum((sum(v) - m * len(v)) ** 2 for v in groups.values())
    se = math.sqrt(g / (g - 1) * resid) / len(d)
    return m, m - Z90 * se, m + Z90 * se


def quintiles(score: np.ndarray, y: np.ndarray, weeks: list[date]) -> list[tuple[float, float, float, int]]:
    """Beat-Nifty rate (with interval) per score quintile, lowest first. Ties share a quintile."""
    if len(score) < 5:
        return []
    ranks = np.argsort(np.argsort(score, kind="mergesort"), kind="mergesort")
    q = np.minimum((ranks * 5) // len(score), 4)
    return [cluster_rate(y[q == k], [w for w, qq in zip(weeks, q) if qq == k]) for k in range(5)]


# ─────────────────────────────────────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────────────────────────────────────

def _incumbent_scores(rows: list[Row]) -> dict[str, list[Optional[float]]]:
    def comp(r: Row) -> Optional[float]:
        vals = [v for v in (_agent_score(a)(r.raw) for a in AGENTS) if v is not None]
        return float(np.mean(vals)) if vals else None
    return {
        "signal_score": [_f(r.raw.get("signal_score")) for r in rows],
        "equal_weight_composite": [comp(r) for r in rows],
    }


def _pct(x: float) -> str:
    return "—" if x != x else f"{100 * x:.1f}%"


def _ci(t: tuple) -> str:
    rate, lo, hi, n = t
    return f"{_pct(rate)} [{_pct(lo)}–{_pct(hi)}] n={n}"


def evaluate(raw_rows: Iterable[dict], horizon: int = 30, dedupe_days: int = 30) -> dict:
    """Full ADR-006 evaluation. Returns a dict; `sufficient` is False with counts when refused."""
    rows, dropped = prepare(raw_rows, horizon)
    ok, n, w = sufficiency(rows)
    out: dict[str, Any] = {"horizon": horizon, "rows": n, "weeks": w, "dropped": dropped,
                           "sufficient": ok}
    if not ok:
        return out
    rows = dedupe(rows, dedupe_days) if dedupe_days else rows
    out["rows_after_dedupe"] = len(rows)
    folds = walk_forward(rows, horizon)
    out["folds"] = folds
    if not folds:
        out["sufficient"] = False
        out["reason"] = "no fold had enough labelled training weeks yet"
        return out
    test = [r for f in folds for r in f.test]
    y = np.array([r.y for r in test], dtype=float)
    wk = [week_of(r.run_date) for r in test]
    p = np.concatenate([f.pred for f in folds])
    base = np.concatenate([np.full(len(f.test), f.base_rate) for f in folds])
    out["overall"] = cluster_rate(y, wk)
    out["brier_model"] = float(np.mean((p - y) ** 2))
    out["brier_base"] = float(np.mean((base - y) ** 2))
    out["brier_diff"] = cluster_mean((p - y) ** 2 - (base - y) ** 2, wk)
    out["model_quintiles"] = quintiles(p, y, wk)

    inc = {}
    for name, sc in _incumbent_scores(test).items():
        idx = [i for i, v in enumerate(sc) if v is not None]
        if len(idx) >= 25:
            inc[name] = {"n": len(idx),
                         "quintiles": quintiles(np.array([sc[i] for i in idx]), y[idx],
                                                [wk[i] for i in idx]),
                         "model_quintiles_same_rows": quintiles(p[idx], y[idx], [wk[i] for i in idx])}
        else:
            inc[name] = {"n": len(idx)}
    passed = np.array([bool((r.raw.get("features") or {}).get("passed"))
                       or r.raw.get("stage") not in (None, "SCREENED") for r in test])
    inc["prescreen"] = {
        "pass": cluster_rate(y[passed], [w_ for w_, k in zip(wk, passed) if k]),
        "fail": cluster_rate(y[~passed], [w_ for w_, k in zip(wk, passed) if not k]),
        "diff": cluster_mean(_pass_fail_diff(y, passed), wk),
    }
    out["incumbents"] = inc

    top = out["model_quintiles"][-1] if out["model_quintiles"] else (float("nan"),) * 3 + (0,)
    sig = inc.get("signal_score", {})
    beats = None
    if sig.get("quintiles") and sig.get("model_quintiles_same_rows"):
        beats = sig["model_quintiles_same_rows"][-1][0] > sig["quintiles"][-1][0]
    out["promotion"] = {
        "top_quintile_lower_bound_above_50": bool(top[1] > 0.5),
        "beats_signal_score_top_quintile": beats,
        "brier_below_baseline": bool(out["brier_diff"][2] < 0),
    }
    out["promotion"]["eligible"] = all(v is True for v in out["promotion"].values())
    return out


def _pass_fail_diff(y: np.ndarray, passed: np.ndarray) -> np.ndarray:
    """Per-row contributions whose mean is (pass rate − fail rate), for a cluster interval."""
    n_p, n_f = max(int(passed.sum()), 1), max(int((~passed).sum()), 1)
    n = len(y)
    return np.where(passed, y * n / n_p, -y * n / n_f)


def insufficient_line(rows: int, weeks: int) -> str:
    return f"insufficient data: {rows} rows, {weeks} weeks"


def to_markdown(res: dict) -> str:
    h = res["horizon"]
    if not res.get("sufficient"):
        line = insufficient_line(res["rows"], res["weeks"])
        need = f"(needs ≥{MIN_ROWS} labelled rows across ≥{MIN_WEEKS} distinct weeks, ADR-006)"
        extra = f" — {res['reason']}" if res.get("reason") else ""
        return f"### Shadow model — t+{h}d\n\n{line} {need}{extra}\n"
    L = [f"### Shadow model — t+{h}d" + (" (provisional, ADR-006 early read)" if h < 90 else ""), ""]
    L.append(f"Labelled rows {res['rows']} across {res['weeks']} weeks; "
             f"{res['rows_after_dedupe']} after one-call-per-symbol-per-30-days; "
             f"{len(res['folds'])} weekly walk-forward folds. 90 % intervals on weekly clusters.")
    L += ["", f"Base beat-Nifty rate (test folds): {_ci(res['overall'])}", "",
          "| Fold week | Train rows | Test rows | Train base rate | Test beat rate | Brier model | Brier base |",
          "|---|---|---|---|---|---|---|"]
    for f in res["folds"]:
        y = np.array([r.y for r in f.test], dtype=float)
        L.append(f"| {f.week} | {f.train_n} | {len(f.test)} | {_pct(f.base_rate)} | {_pct(y.mean())} | "
                 f"{np.mean((f.pred - y) ** 2):.4f} | {np.mean((f.base_rate - y) ** 2):.4f} |")
    d, lo, hi = res["brier_diff"]
    L += ["", f"Pooled Brier: model {res['brier_model']:.4f} vs base-rate {res['brier_base']:.4f} "
              f"(difference {d:+.4f}, 90 % [{lo:+.4f}, {hi:+.4f}]; negative = model better)", "",
          "**Beat-Nifty rate by quintile (Q1 lowest → Q5 highest)**", "",
          "| Score | Q1 | Q2 | Q3 | Q4 | Q5 |", "|---|---|---|---|---|---|"]
    L.append("| shadow model | " + " | ".join(_ci(q) for q in res["model_quintiles"]) + " |")
    for name in ("signal_score", "equal_weight_composite"):
        inc = res["incumbents"].get(name, {})
        if inc.get("quintiles"):
            L.append(f"| {name} | " + " | ".join(_ci(q) for q in inc["quintiles"]) + " |")
            L.append(f"| shadow model, same {inc['n']} rows | "
                     + " | ".join(_ci(q) for q in inc["model_quintiles_same_rows"]) + " |")
        else:
            L.append(f"| {name} | too few scored rows (n={inc.get('n', 0)}) | | | | |")
    ps = res["incumbents"]["prescreen"]
    dd, dlo, dhi = ps["diff"]
    L += ["", f"Pre-screen pass {_ci(ps['pass'])} vs fail {_ci(ps['fail'])}; "
              f"difference {100 * dd:+.1f} pts, 90 % [{100 * dlo:+.1f}, {100 * dhi:+.1f}]", "",
          "**ADR-006 promotion rule** (shadow only — nothing is promoted automatically)", ""]
    for k, v in res["promotion"].items():
        L.append(f"- {k.replace('_', ' ')}: {'yes' if v else ('n/a' if v is None else 'no')}")
    return "\n".join(L) + "\n"


# ─────────────────────────────────────────────────────────────────────────────
# Loading (read-only)
# ─────────────────────────────────────────────────────────────────────────────

_COLS = "run_date,symbol,source,stage,features,agent_signals,signal_score,alpha_t30,alpha_t90"


def load_labelled(client: Any, horizon: int, source: str = "discovery", page: int = 1000) -> list[dict]:
    """Labelled candidate rows for one horizon. Read-only; raises on a DB error (the script reports it)."""
    rows: list[dict] = []
    offset = 0
    while True:
        batch = (client.table("candidates").select(_COLS)
                 .eq("source", source)
                 .not_.is_(f"alpha_t{horizon}", "null")
                 .order("run_date").order("symbol")
                 .range(offset, offset + page - 1).execute().data or [])
        rows.extend(batch)
        if len(batch) < page:
            return rows
        offset += page
