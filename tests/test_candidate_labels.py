"""
tests/test_candidate_labels.py — 30/90-day alpha labels on candidate rows (WP-014, ADR-002).

alpha = symbol return − Nifty return over the row's own window, as a fraction
(the recommendation_outcomes formula). A row with no price is counted as
failed and stays null — never labelled 0.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import outcome_tracker as ot  # noqa: E402
from data import candidate_ledger as cl  # noqa: E402

TODAY = date(2026, 12, 17)


class FakeClient:
    """Implements the select chain label_candidates uses and upsert on the ledger key."""

    def __init__(self, rows):
        self.rows = {(r["run_date"], r["symbol"], r["source"]): dict(r) for r in rows}
        self.selects = []

    def table(self, name):
        assert name == "candidates"
        return _Q(self)


class _Q:
    def __init__(self, c):
        self.c, self.filters, self.rng, self.payload = c, [], None, None

    def select(self, cols):
        return self

    def is_(self, col, val):
        assert val == "null"
        self.filters.append(lambda r, col=col: r.get(col) is None)
        return self

    def lte(self, col, v):
        self.filters.append(lambda r, col=col, v=v: r[col] <= v)
        return self

    def gte(self, col, v):
        self.filters.append(lambda r, col=col, v=v: r[col] >= v)
        return self

    def order(self, *a, **k):
        return self

    def range(self, a, b):
        self.rng = (a, b)
        return self

    def upsert(self, rows, on_conflict=None):
        self.payload = (rows, on_conflict)
        return self

    def execute(self):
        if self.payload:
            rows, oc = self.payload
            for r in rows:
                self.c.rows.setdefault(tuple(r[k] for k in oc.split(",")), {}).update(r)
            return self
        hits = sorted((r for r in self.c.rows.values() if all(f(r) for f in self.filters)),
                      key=lambda r: (r["run_date"], r["symbol"]))
        a, b = self.rng
        self.c.selects.append(self.rng)
        self.data = hits[a:b + 1]
        return self


def _row(symbol, days_ago, price=100.0, nifty=20000.0, **extra):
    r = {"id": f"id-{symbol}", "run_date": (TODAY - timedelta(days=days_ago)).isoformat(),
         "symbol": symbol, "yf_symbol": f"{symbol}.NS", "source": "discovery",
         "stage": "SCREENED", "price": price, "nifty": nifty}
    r.update({f"{p}_t{h}": None for h in (30, 90) for p in ("price", "nifty", "alpha")})
    r.update(extra)
    return r


@pytest.fixture
def prices(monkeypatch):
    """Batch prices by symbol; the Nifty is +5% over every window."""
    table = {"^NSEI": 21000.0}
    calls = []

    def fake_batch(syms, target, window=4):
        calls.append((tuple(sorted(syms)), target))
        return {s: table[s] for s in syms if s in table}

    monkeypatch.setattr(ot, "_batch_prices_on_date", fake_batch)
    monkeypatch.setattr(ot, "_fetch_price_on_date", lambda s, d, window=4: 20000.0)
    return table, calls


def test_31_day_old_row_gets_alpha_t30_not_t90(prices):
    table, _ = prices
    table["AAA.NS"] = 120.0                     # +20% vs Nifty +5% → alpha 0.15
    c = FakeClient([_row("AAA", 31)])
    r30 = cl.label_candidates(c, 30, today=TODAY)
    r90 = cl.label_candidates(c, 90, today=TODAY)
    row = c.rows[((TODAY - timedelta(days=31)).isoformat(), "AAA", "discovery")]
    assert (r30["due"], r30["labelled"], r30["failed"]) == (1, 1, 0)
    assert (r90["due"], r90["labelled"]) == (0, 0)
    assert row["alpha_t30"] == pytest.approx(0.15)
    assert row["price_t30"] == 120.0 and row["nifty_t30"] == 21000.0
    assert row["alpha_t90"] is None and row["labelled_at"]
    assert row["stage"] == "SCREENED"


def test_same_formula_as_recommendation_outcomes(prices):
    table, _ = prices
    table["BBB.NS"] = 90.0
    c = FakeClient([_row("BBB", 40, price=100.0, nifty=20000.0)])
    cl.label_candidates(c, 30, today=TODAY)
    (row,) = c.rows.values()
    abs_return, nifty_ret = 90.0 / 100.0 - 1.0, 21000.0 / 20000.0 - 1.0
    assert row["alpha_t30"] == round(abs_return - nifty_ret, 6)


def test_symbol_with_no_price_is_failed_not_zero(prices):
    table, _ = prices
    table["OK.NS"] = 105.0
    c = FakeClient([_row("OK", 35), _row("GONE", 35)])
    r = cl.label_candidates(c, 30, today=TODAY)
    assert (r["due"], r["labelled"], r["failed"]) == (2, 1, 1)
    gone = c.rows[((TODAY - timedelta(days=35)).isoformat(), "GONE", "discovery")]
    assert gone["alpha_t30"] is None and gone["price_t30"] is None


def test_row_without_entry_price_is_failed(prices):
    prices[0]["NOPX.NS"] = 100.0
    c = FakeClient([_row("NOPX", 35, price=None)])
    assert cl.label_candidates(c, 30, today=TODAY)["failed"] == 1


def test_missing_entry_nifty_uses_nifty_on_run_date(prices):
    prices[0]["CCC.NS"] = 110.0
    c = FakeClient([_row("CCC", 33, nifty=None)])
    cl.label_candidates(c, 30, today=TODAY)
    (row,) = c.rows.values()
    assert row["alpha_t30"] == pytest.approx(0.10 - 0.05)    # fallback entry Nifty 20000


def test_one_batch_download_per_run_date_including_nifty(prices):
    table, calls = prices
    table.update({"A.NS": 1.0, "B.NS": 1.0, "C.NS": 1.0})
    c = FakeClient([_row("A", 31), _row("B", 31), _row("C", 32)])
    cl.label_candidates(c, 30, today=TODAY)
    assert len(calls) == 2
    assert all("^NSEI" in syms for syms, _ in calls)
    assert {t for _, t in calls} == {TODAY - timedelta(days=1), TODAY - timedelta(days=2)}


def test_already_labelled_and_too_old_rows_are_not_due(prices):
    prices[0]["X.NS"] = 1.0
    c = FakeClient([_row("DONE", 40, alpha_t30=0.1),
                    _row("OLD", 30 + cl.LABEL_GIVE_UP_DAYS + 1),
                    _row("FRESH", 29)])
    assert cl.label_candidates(c, 30, today=TODAY)["due"] == 0


def test_due_rows_are_paged_in_500s(prices, monkeypatch):
    rows = [_row(f"S{i:04d}", 31) for i in range(1203)]
    c = FakeClient(rows)
    monkeypatch.setattr(cl, "record_candidates", lambda client, ups: len(ups))
    r = cl.label_candidates(c, 30, today=TODAY)
    assert r["due"] == 1203 and c.selects == [(0, 499), (500, 999), (1000, 1499)]


def test_no_client_and_select_failure_never_raise():
    assert cl.label_candidates(None, 30, today=TODAY)["errors"]

    class Broken:
        def table(self, n):
            raise RuntimeError("PGRST205 Could not find the table")
    r = cl.label_candidates(Broken(), 30, today=TODAY)
    assert r["labelled"] == 0 and "create_candidates.sql" in r["errors"][0]


# ── price helpers ─────────────────────────────────────────────────────────────

def _tz_series(start, closes):
    idx = pd.date_range(start, periods=len(closes), freq="D", tz="Asia/Kolkata")
    return pd.Series(closes, index=idx, name="Close")


def test_close_near_handles_tz_aware_index():
    s = _tz_series("2026-11-10", [100.0, 101.0, 102.0, float("nan")])
    assert ot._close_near(s, date(2026, 11, 11)) == 101.0
    assert ot._close_near(s, date(2026, 11, 13)) == 102.0          # NaN bar skipped
    assert ot._close_near(s, date(2026, 12, 1)) is None            # outside ±window


def test_batch_prices_on_date_tz_aware_multiindex(monkeypatch):
    idx = pd.date_range("2026-11-08", periods=6, freq="D", tz="Asia/Kolkata")
    cols = pd.MultiIndex.from_product([["Close", "Volume"], ["AAA.NS", "^NSEI"]])
    df = pd.DataFrame([[10 + i, 20000 + i, 1, 1] for i in range(6)], index=idx, columns=cols,
                      dtype=float)
    df[("Close", "AAA.NS")] = [10, 11, 12, float("nan"), float("nan"), float("nan")]
    monkeypatch.setattr(ot.yf, "download", lambda *a, **k: df)
    out = ot._batch_prices_on_date(["AAA.NS", "^NSEI", "MISSING.NS"], date(2026, 11, 10))
    assert out["AAA.NS"] == 12.0 and out["^NSEI"] == 20002.0
    assert "MISSING.NS" not in out


def test_batch_prices_on_date_download_failure_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("rate limited")
    monkeypatch.setattr(ot.yf, "download", boom)
    assert ot._batch_prices_on_date(["AAA.NS"], date(2026, 11, 10)) == {}


# ── worker wiring ─────────────────────────────────────────────────────────────

def test_outcome_tracker_job_reports_candidate_labels(monkeypatch):
    import worker
    monkeypatch.setattr("agents.outcome_tracker.run_outcome_tracking",
                        lambda dry_run=False: {"updated": 0, "errors": []})
    monkeypatch.setattr("agents.outcome_tracker.run_sentiment_validation",
                        lambda dry_run=False: {"validated": 0})
    monkeypatch.setattr(cl, "get_client", lambda: object())
    monkeypatch.setattr(cl, "label_candidates",
                        lambda client, h, today=None: {"horizon": h, "due": 3, "labelled": 2,
                                                       "failed": 1, "errors": []})
    monkeypatch.setattr("data.run_log._supabase_client", lambda: None)   # job ledger: no DB
    out = worker.job_outcome_tracker()
    assert out["candidates_labelled"] == 4 and out["candidates_label_failed"] == 2
    assert [l["horizon"] for l in out["candidate_labels"]] == [30, 90]
