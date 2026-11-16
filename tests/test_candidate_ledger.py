"""
tests/test_candidate_ledger.py — the candidate ledger (WP-013, ADR-002).

Covers data/candidate_ledger.py and its two writers: run_discovery() records
one row per screened symbol and updates (never duplicates) the deep-analysed
ones; multibagger picks are recorded with source='multibagger'. A missing
table or a broken client must never fail the screener's run.
"""
import logging
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data import candidate_ledger as cl  # noqa: E402

RUN_DATE = date(2026, 11, 16)


# ── A fake Supabase client that behaves like upsert(on_conflict=...) ──────────

class _Query:
    def __init__(self, client, table):
        self.client, self.table, self.payload, self.on_conflict = client, table, None, None

    def upsert(self, rows, on_conflict=None):
        self.payload, self.on_conflict = rows, on_conflict
        return self

    def execute(self):
        if self.client.error:
            raise self.client.error
        self.client.calls.append((self.table, list(self.payload), self.on_conflict))
        keys = self.on_conflict.split(",")
        for row in self.payload:
            k = tuple(row[c] for c in keys)
            self.client.rows.setdefault(k, {}).update(row)   # merge, like ON CONFLICT DO UPDATE
        return self


class FakeClient:
    def __init__(self, error=None):
        self.rows, self.calls, self.error = {}, [], error

    def table(self, name):
        return _Query(self, name)


@pytest.fixture(autouse=True)
def _reset_warned(monkeypatch):
    monkeypatch.setattr(cl, "_missing_table_warned", False)


# ── record_candidates ─────────────────────────────────────────────────────────

def test_upsert_on_run_date_symbol_source_in_batches():
    c = FakeClient()
    rows = [cl.screen_row(RUN_DATE, f"S{i}.NS", "discovery", passed=False,
                          features={"price": 10 + i}, nifty=24000.0) for i in range(250)]
    assert cl.record_candidates(c, rows) == 250
    assert [len(b) for _, b, _ in c.calls] == [100, 100, 50]
    assert {oc for _, _, oc in c.calls} == {"run_date,symbol,source"}
    assert {t for t, _, _ in c.calls} == {"candidates"}


def test_rows_with_different_keys_are_never_batched_together():
    c = FakeClient()
    a = cl.screen_row(RUN_DATE, "A.NS", "discovery", passed=True, features={}, nifty=1.0)
    b = cl.deep_row(RUN_DATE, "B.NS", "discovery", features={}, agent_signals={},
                    signal_score=70, data_quality="OK", tier=None, rec_id=None)
    cl.record_candidates(c, [a, b])
    assert len(c.calls) == 2
    for _, batch, _ in c.calls:
        assert len({tuple(sorted(r)) for r in batch}) == 1


def test_missing_table_warns_once_and_returns_zero(caplog):
    c = FakeClient(error=RuntimeError("PGRST205 Could not find the table 'public.candidates'"))
    rows = [cl.screen_row(RUN_DATE, "X.NS", "discovery", passed=False, features={}, nifty=None)]
    with caplog.at_level(logging.WARNING):
        assert cl.record_candidates(c, rows) == 0
        assert cl.record_candidates(c, rows) == 0
    msgs = [r.getMessage() for r in caplog.records if "create_candidates.sql" in r.getMessage()]
    assert len(msgs) == 1


def test_other_failure_warns_and_never_raises(caplog):
    c = FakeClient(error=RuntimeError("timeout"))
    with caplog.at_level(logging.WARNING):
        n = cl.record_candidates(c, [cl.screen_row(RUN_DATE, "X.NS", "discovery",
                                                   passed=False, features={}, nifty=None)])
    assert n == 0
    assert any("timeout" in r.getMessage() for r in caplog.records)


def test_no_client_or_no_rows():
    assert cl.record_candidates(None, [{"a": 1}]) == 0
    assert cl.record_candidates(FakeClient(), []) == 0


def test_screen_row_shape():
    r = cl.screen_row(RUN_DATE, "RELIANCE.NS", "discovery", passed=True,
                      features={"price": 2850.5, "rsi": 55.0}, nifty=24100.0)
    assert r["symbol"] == "RELIANCE" and r["yf_symbol"] == "RELIANCE.NS"
    assert r["stage"] == "PRESCREEN_PASS" and r["run_date"] == "2026-11-16"
    assert r["price"] == 2850.5 and r["nifty"] == 24100.0
    assert r["feature_version"] == cl.FEATURE_VERSION
    assert cl.screen_row(RUN_DATE, "X.NS", "discovery", passed=False,
                         features={"price": float("nan")}, nifty=None)["price"] is None


def test_deep_row_stage_follows_rec_id():
    kw = dict(features={}, agent_signals={}, signal_score=70, data_quality="OK", tier="STANDARD")
    assert cl.deep_row(RUN_DATE, "A.NS", "discovery", rec_id=None, **kw)["stage"] == "DEEP"
    assert cl.deep_row(RUN_DATE, "A.NS", "discovery", rec_id="r-1", **kw)["stage"] == "PROMOTED"


# ── run_discovery ─────────────────────────────────────────────────────────────

def _agents(conf):
    return {a: {"signal": "BUY", "score": conf, "upside_pct": 30.0, "confidence": 0.7,
                "data_quality": "OK", "detail": {}}
            for a in ["technical", "fundamental", "sentiment", "institutional",
                      "macro", "historical_rag", "commodities"]}


@pytest.fixture
def discovery_env(monkeypatch):
    """run_discovery with every network/LLM dependency stubbed; 200-symbol universe."""
    from agents import discovery_screener as ds

    universe = [f"SYM{i:03d}.NS" for i in range(200)]
    passing = set(universe[:5])
    client = FakeClient()

    def fake_prescreen(sym, fii_data=None, features=None):
        ok = sym in passing
        if features is not None:
            features.update(price=100.0, rsi=50.0, passed=ok, filters_passed=4 if ok else 1)
        return ok, (["RSI", "PE", "EMA", "REV"] if ok else ["RSI"])

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(ds, "fetch_all_nse_equity_symbols", lambda: universe)
    monkeypatch.setattr(ds, "_load_portfolio_symbols", lambda: set())
    monkeypatch.setattr(ds, "_daily_slice", lambda u, n, run_date=None: list(u)[:n])
    monkeypatch.setattr(ds, "get_nse_fii_dii", lambda: {})
    monkeypatch.setattr("agents.macro.analyse", lambda: {"signal": "NEUTRAL", "score": 50})
    monkeypatch.setattr(ds, "prescreen", fake_prescreen)
    monkeypatch.setattr(ds, "_run_all_agents", lambda sym, macro_result=None: _agents(70))
    monkeypatch.setattr(ds, "_best_upside", lambda ar: (30.0, 70.0))
    monkeypatch.setattr(ds, "_current_price", lambda sym: 100.0)
    monkeypatch.setattr(ds, "_upside_basis", lambda *a, **k: "basis")
    monkeypatch.setattr("data.impact_cost.estimate_impact_cost",
                        lambda *a, **k: {"liquidity_tier": "HIGH", "impact_cost_pct": 0.1})
    monkeypatch.setattr("data.forward_estimates.get_forward_estimates", lambda s: {})
    saved = iter(f"rec-{i}" for i in range(100))
    monkeypatch.setattr(ds, "_save_discovery", lambda d: next(saved))
    logged = {}
    monkeypatch.setattr(ds, "_log_daily_run", lambda **kw: logged.update(kw))
    monkeypatch.setattr(cl, "get_client", lambda: client)
    from agents.call_calibration import Calibration
    monkeypatch.setattr(ds, "_get_calibration", lambda: Calibration())
    nifty_calls = []
    monkeypatch.setattr(cl, "nifty_level", lambda: nifty_calls.append(1) or 24000.0)
    return ds, client, logged, nifty_calls


def test_200_screened_symbols_produce_200_rows(discovery_env):
    ds, client, logged, nifty_calls = discovery_env
    out = ds.run_discovery(max_candidates=25, max_prescreen=200, save_to_db=True, inter_delay=0,
                           _run_date=RUN_DATE)
    assert len(client.rows) == 200
    assert all(k[0] == "2026-11-16" and k[2] == "discovery" for k in client.rows)
    stages = [r["stage"] for r in client.rows.values()]
    assert stages.count("SCREENED") == 195
    assert all(r["nifty"] == 24000.0 for r in client.rows.values())
    assert nifty_calls == [1]                      # once per run, not per symbol
    assert out.ledger_rows == 205                  # 200 screened + 5 deep updates
    assert logged["details"] == {"candidates_written": 205}


def test_deep_analysed_symbol_is_updated_not_duplicated(discovery_env):
    ds, client, _, _ = discovery_env
    ds.run_discovery(max_candidates=25, max_prescreen=200, save_to_db=True, inter_delay=0,
                     _run_date=RUN_DATE)
    row = client.rows[("2026-11-16", "SYM000", "discovery")]
    assert row["stage"] == "PROMOTED" and row["rec_id"].startswith("rec-")
    assert row["tier"] == "STANDARD" and row["signal_score"] == 70.0
    assert row["agent_signals"]["technical"] == {"signal": "BUY", "score": 70}
    assert row["features"]["rsi"] == 50.0 and "composite_score" in row["features"]
    assert row["yf_symbol"] == "SYM000.NS" and row["nifty"] == 24000.0   # kept from screen row
    assert sum(1 for k in client.rows if k[1] == "SYM000") == 1


def test_missing_table_does_not_fail_the_run(discovery_env, monkeypatch):
    ds, _, logged, _ = discovery_env
    monkeypatch.setattr(cl, "get_client",
                        lambda: FakeClient(error=RuntimeError("PGRST205 Could not find the table")))
    out = ds.run_discovery(max_candidates=25, max_prescreen=200, save_to_db=True, inter_delay=0,
                           _run_date=RUN_DATE)
    assert len(out) == 5 and out.ledger_rows == 0
    assert logged["details"] == {"candidates_written": 0}


def test_no_save_writes_no_ledger_rows(discovery_env, monkeypatch):
    ds, client, _, nifty_calls = discovery_env
    monkeypatch.setattr(cl, "get_client", lambda: pytest.fail("dry run must not open a client"))
    out = ds.run_discovery(max_candidates=25, max_prescreen=200, save_to_db=False, inter_delay=0,
                           _run_date=RUN_DATE)
    assert client.rows == {} and nifty_calls == [] and out.ledger_rows == 0


def test_no_save_writes_no_run_log(discovery_env, monkeypatch):
    """--no-save must not add a daily_runs row or overwrite the day's discovery_runs row."""
    ds, _, _, _ = discovery_env
    monkeypatch.setattr(ds, "_log_daily_run", lambda **kw: pytest.fail("dry run wrote the run log"))
    out = ds.run_discovery(max_candidates=25, max_prescreen=200, save_to_db=False, inter_delay=0,
                           _run_date=RUN_DATE)
    assert len(out) == 5


def test_saved_run_still_writes_run_log(discovery_env):
    ds, _, logged, _ = discovery_env
    ds.run_discovery(max_candidates=25, max_prescreen=200, save_to_db=True, inter_delay=0,
                     _run_date=RUN_DATE)
    assert logged["run_date"] == RUN_DATE and len(logged["slice_symbols"]) == 200


def test_prescreen_records_skip_reason(monkeypatch):
    from agents import discovery_screener as ds
    monkeypatch.setattr(ds, "get_ohlcv", lambda *a, **k: None)
    feats = {}
    assert ds.prescreen("X.NS", features=feats) == (False, [])
    assert feats == {"passed": False, "skip_reason": "NO_OHLCV"}


# ── multibagger ───────────────────────────────────────────────────────────────

def test_multibagger_picks_recorded_with_source_multibagger(monkeypatch):
    from agents import multibagger_screener as mb

    class Client(FakeClient):
        def table(self, name):
            q = _Query(self, name)
            if name == "long_horizon_picks":
                class _LH:
                    def select(s, *a): return s
                    def eq(s, *a): return s
                    def insert(s, row): return s
                    def execute(s): return type("R", (), {"data": []})()
                return _LH()
            return q

    client = Client()
    monkeypatch.setattr("data.fetchers.get_screener_data", lambda s: {"x": 1})
    monkeypatch.setattr("agents.warren_bot.analyse",
                        lambda s: {"current_price": 250.0, "signal": "AVOID", "score": 60})
    monkeypatch.setattr(mb, "prefilter", lambda snap: (True, []))
    monkeypatch.setattr(mb, "deep_check", lambda w: (True, "durable"))
    monkeypatch.setattr(mb, "score", lambda snap, w=None: (85.0, {"runway": {"market_cap_cr": 900}}))
    monkeypatch.setattr(mb, "thesis", lambda b, c: "t")
    monkeypatch.setattr(cl, "nifty_level", lambda: 24000.0)
    out = mb.run_screen(symbols=["GABRIEL.NS"], dry_run=False, client=client)
    assert out["candidates_written"] == 1
    (row,) = client.rows.values()
    assert row["source"] == "multibagger" and row["stage"] == "PROMOTED"
    assert row["symbol"] == "GABRIEL" and row["price"] == 250.0 and row["nifty"] == 24000.0
    assert row["signal_score"] == 85.0 and row["agent_signals"]["warren_bot"]["signal"] == "AVOID"


def test_multibagger_dry_run_records_nothing(monkeypatch):
    from agents import multibagger_screener as mb
    monkeypatch.setattr("data.fetchers.get_screener_data", lambda s: {"x": 1})
    monkeypatch.setattr("agents.warren_bot.analyse", lambda s: {"current_price": 1.0})
    monkeypatch.setattr(mb, "prefilter", lambda snap: (True, []))
    monkeypatch.setattr(mb, "deep_check", lambda w: (True, "durable"))
    monkeypatch.setattr(mb, "score", lambda snap, w=None: (85.0, {"runway": {"market_cap_cr": 900}}))
    monkeypatch.setattr(mb, "thesis", lambda b, c: "t")
    monkeypatch.setattr(mb, "_supabase", lambda: None)
    monkeypatch.setattr(cl, "record_candidates", lambda *a: pytest.fail("dry run wrote to the ledger"))
    out = mb.run_screen(symbols=["GABRIEL.NS"], dry_run=True, client=None)
    assert out["candidates_written"] == 0 and len(out["picks"]) == 1
