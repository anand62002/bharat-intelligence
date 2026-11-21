"""
tests/test_holdings_review.py — the portfolio pipeline publishes a daily holdings review (WP-021, ADR-007).

For a stock already owned, the question is keep / trim / sell. The 06:00
pipeline now saves one HOLDING_REVIEW per open holding, supersedes yesterday's,
never opens a paper position from it, and leaves /api/analyse unchanged.
"""
import asyncio
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# api.main builds its Supabase client at import — import at collection (WP-008 guard).
import api.main as api  # noqa: E402
from scheduler import decision as d  # noqa: E402
from scheduler import invariants as inv  # noqa: E402
from scheduler import orchestrator as orch  # noqa: E402

TODAY = date.today()


# ── an in-memory Supabase good enough for save_recs_node ──────────────────────

class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.op, self.payload = db, table, [], "select", None

    def select(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a): return self

    def eq(self, col, val):
        self.filters.append(lambda r, c=col, v=val: _get(r, c) == v)
        return self

    def gte(self, col, val):
        self.filters.append(lambda r, c=col, v=val: str(_get(r, c) or "") >= str(v))
        return self

    def in_(self, col, vals):
        self.filters.append(lambda r, c=col, v=tuple(vals): _get(r, c) in v)
        return self

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    def update(self, vals):
        self.op, self.payload = "update", vals
        return self

    def execute(self):
        rows = self.db.setdefault(self.table, [])
        if self.op == "insert":
            row = dict(self.payload, id=str(uuid.uuid4()))
            rows.append(row)
            return type("R", (), {"data": [row]})()
        hit = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "update":
            for r in hit:
                r.update(self.payload)
        return type("R", (), {"data": hit, "count": len(hit)})()


def _get(row, col):
    if "->>" in col:
        a, b = col.split("->>")
        return (row.get(a) or {}).get(b)
    return row.get(col)


class FakeDB:
    def __init__(self, holdings):
        self.tables = {"portfolio_holdings": holdings, "recommendations": []}

    def table(self, name):
        return _Q(self.tables, name)

    def live_reviews(self):
        return [r for r in self.tables["recommendations"]
                if (r.get("metadata") or {}).get("kind") == "HOLDING_REVIEW"
                and r["valid_till"] >= str(TODAY)]


def holding(sym, avg=100.0, px=110.0, sl=90.0, tgt=150.0):
    return {"id": f"h-{sym}", "symbol": sym, "yf_symbol": f"{sym}.NS", "status": "OPEN",
            "avg_buy": avg, "current_price": px, "stoploss_price": sl, "target_price": tgt,
            "created_at": "2026-10-01T00:00:00Z"}


def rec(sym, action="BUY"):
    """A code-decided portfolio rec, as synthesise_node produces it."""
    return {"symbol": f"{sym}.NS", "action": action, "confidence": 50.0, "risk_score": 40.0,
            "entry_low": 95.0, "entry_high": 100.0, "target": 140.0, "stoploss": 85.0,
            "horizon_days": 90, "upside_pct": 30.0, "upside_confidence": 50.0,
            "danger_drop_pct": 10.0, "danger_confidence": 40.0, "headline": f"{action}: {sym}",
            "summary": "Narrative.", "agent_signals": {}, "is_discovery": False, "gov_check": {},
            "suggested_position_pct": 2.5, "position_label": "Base position (2.5%)",
            "composite_score": 66.0,
            "decision_meta": {"data_quality": "FULL",
                              "decision": {"source": "code", "rule_action": action, "price": 110.0}}}


AGENTS_INTACT = {"fundamental": {"signal": "BUY", "score": 80, "upside_pct": 40.0},
                 "technical": {"signal": "BUY", "score": 70, "upside_pct": 10.0,
                               "detail": {"indicators": {"current_price": 110.0}}}}


def _save(db, recs, symbol_results=None):
    state = {"recommendations": recs, "errors": [], "dry_run": False,
             "symbol_results": symbol_results or {}}
    with patch.object(orch, "_supabase", lambda: db), \
         patch("agents.outcome_tracker.seed_pending_outcome", return_value=True):
        return asyncio.run(orch.save_recs_node(state))


# ── acceptance: one live review per holding; reruns supersede ─────────────────

def test_three_holdings_yield_three_live_reviews():
    db = FakeDB([holding("AAA"), holding("BBB"), holding("CCC")])
    # CCC has no analysis today → a stop/target-only review still appears
    _save(db, [rec("AAA"), rec("BBB")], {"AAA.NS": AGENTS_INTACT})
    live = db.live_reviews()
    assert sorted(r["symbol"] for r in live) == ["AAA.NS", "BBB.NS", "CCC.NS"]
    assert all(r["action"] in ("HOLD", "SELL") for r in live)      # never BUY
    assert all(r["entry_low"] is None and r["entry_high"] is None for r in live)
    a = next(r for r in live if r["symbol"] == "AAA.NS")
    assert a["stoploss"] == 90.0 and a["target"] == 150.0           # from the holding
    assert a["metadata"]["thesis_status"] == "INTACT"
    assert a["metadata"]["review_action"] == "HOLD" and a["valid_till"] == str(TODAY + timedelta(days=1))
    assert a["suggested_position_pct"] is None


def test_second_run_supersedes_rather_than_duplicates():
    db = FakeDB([holding("AAA"), holding("BBB"), holding("CCC")])
    _save(db, [rec("AAA"), rec("BBB"), rec("CCC")])
    _save(db, [rec("AAA"), rec("BBB"), rec("CCC")])
    reviews = [r for r in db.tables["recommendations"]
               if (r.get("metadata") or {}).get("kind") == "HOLDING_REVIEW"]
    assert len(reviews) == 6 and len(db.live_reviews()) == 3
    assert sum(r["valid_till"] == str(TODAY - timedelta(days=1)) for r in reviews) == 3


def test_watchlist_symbols_keep_the_normal_recommendation():
    db = FakeDB([holding("AAA")])
    _save(db, [rec("AAA"), rec("WATCH")])
    watch = [r for r in db.tables["recommendations"] if r["symbol"] == "WATCH.NS"]
    assert len(watch) == 1 and watch[0]["action"] == "BUY"
    assert (watch[0].get("metadata") or {}).get("kind") is None


def test_no_review_is_opened_as_a_paper_position():
    from agents import paper_portfolio as pp
    db = FakeDB([holding("AAA")])
    _save(db, [rec("AAA")])
    # even a review mislabelled BUY must not become a paper trade
    for r in db.tables["recommendations"]:
        r["action"] = "BUY"
        r.setdefault("created_at", f"{TODAY}T06:00:00Z")
    db.tables["paper_portfolio_positions"] = []
    out = pp.open_new_positions(db, dry_run=True)
    assert out["opened"] == 0


# ── the review rule ──────────────────────────────────────────────────────────

def test_review_exit_below_stop_maps_to_sell():
    out = d.review_holding(holding("X", px=85.0), None)
    assert out["review_action"] == "EXIT" and out["action"] == "SELL"


def test_broken_thesis_turns_hold_into_review():
    weak = {"fundamental": {"signal": "SELL", "score": 30, "upside_pct": 2.0},
            "technical": {"signal": "SELL", "score": 30, "upside_pct": 0.0}}
    out = d.review_holding(holding("X"), weak, rule_action="SELL")
    assert out["thesis_status"] == "BROKEN"
    assert out["review_action"] == "REVIEW" and out["action"] == "HOLD"
    assert "thesis broken" in out["review_reason"]


def test_thesis_weakened_without_bearish_rule():
    weak = {"fundamental": {"signal": "HOLD", "score": 50, "upside_pct": 5.0},
            "technical": {"signal": "HOLD", "score": 50, "upside_pct": 0.0}}
    assert d.thesis_status(weak, "HOLD")[0] == "WEAKENED"
    assert d.thesis_status(AGENTS_INTACT, "HOLD")[0] == "INTACT"
    assert d.thesis_status(None)[0] == "UNKNOWN"


def test_agent_price_overrides_stale_holding_price():
    out = d.review_holding(holding("X", px=85.0), None, price=120.0)
    assert out["review_action"] == "HOLD" and out["price"] == 120.0


# ── API ───────────────────────────────────────────────────────────────────────

def test_recommendations_exclude_reviews_and_stale_holding_recs(monkeypatch):
    rows = [
        {"id": "1", "symbol": "AAA.NS", "action": "HOLD", "is_discovery": False,
         "created_at": "2026-11-21T06:00:00Z", "metadata": {"kind": "HOLDING_REVIEW"}},
        {"id": "2", "symbol": "AAA.NS", "action": "BUY", "is_discovery": False,
         "created_at": "2026-11-14T06:00:00Z", "metadata": {}},          # stale pre-review rec
        {"id": "3", "symbol": "DISC.NS", "action": "BUY", "is_discovery": True,
         "created_at": "2026-11-20T10:30:00Z", "metadata": {}},
    ]

    class Q:
        def select(self, *a): return self
        def order(self, *a, **k): return self
        def limit(self, *a): return self
        def execute(self): return type("R", (), {"data": rows})()

    monkeypatch.setattr(api, "_db", lambda: type("C", (), {"table": lambda self, n: Q()})())
    default = asyncio.run(api.get_recommendations(limit=50, include_reviews=False, _=None))
    assert [r["symbol"] for r in default] == ["DISC.NS"]
    full = asyncio.run(api.get_recommendations(limit=50, include_reviews=True, _=None))
    assert {r["symbol"] for r in full} == {"AAA.NS", "DISC.NS"}


def test_portfolio_transformer_carries_the_review():
    row = {"id": "h1", "symbol": "AAA", "qty": 1, "avg_buy": 100, "current_price": 110,
           "_exit": {"signal": "HOLD", "reason": "within plan"},
           "_review": {"action": "REVIEW", "thesis": "WEAKENED", "reason": "x", "date": "2026-11-21"}}
    h = api._transform_holding(row)
    assert (h["reviewAction"], h["thesisStatus"], h["reviewReason"]) == ("REVIEW", "WEAKENED", "x")
    assert h["exitSignal"] == "HOLD"                                      # kept for the dashboard


# ── invariant, on-demand path ─────────────────────────────────────────────────

def test_invariant_live_reviews_must_equal_open_holdings():
    row = {"status": "OK", "details": {"recommendations": 24}}
    assert inv._orchestrator(row, inv.Context(open_holdings=24, live_reviews=24)) is None
    msg = inv._orchestrator(row, inv.Context(open_holdings=24, live_reviews=21))
    assert msg and "21 live holding reviews for 24 open holdings" in msg


def test_on_demand_analyse_graph_has_no_save_step():
    g = orch._build_graph(on_demand=True)
    nodes = set(getattr(g, "nodes", {}) or getattr(getattr(g, "builder", None), "nodes", {}) or {})
    assert nodes and "save_recs" not in nodes
