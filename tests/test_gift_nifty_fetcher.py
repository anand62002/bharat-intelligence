"""
tests/test_gift_nifty_fetcher.py — GIFT Nifty from NSE IX, never faked from ^NSEI.

The old fallback read ^NSEI (the Nifty 50 index) as "GIFT Nifty", so the
pre-market premium was always 0.00% FLAT and the worker job logged OK daily.
No network: every source and the API helpers are mocked.
"""
import asyncio
import inspect
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data import gift_nifty_fetcher as g  # noqa: E402

NSEIX_ROWS = [  # trimmed from the live response on 2026-11-15
    {"INSTRUMENTTYPE": "FUTIDX", "SYMBOL": "NIFTY", "EXPIRYDATE": "29-Dec-2026",
     "LASTPRICE": "25332.50", "TIMESTMP": "13-Nov-2026 23:37:06"},
    {"INSTRUMENTTYPE": "FUTIDX", "SYMBOL": "NIFTY", "EXPIRYDATE": "23-Nov-2026",
     "LASTPRICE": "25266.00", "TIMESTMP": "14-Nov-2026 02:44:30"},
    {"INSTRUMENTTYPE": "FUTSTK", "SYMBOL": "RELIANCE", "EXPIRYDATE": "20-Nov-2026",
     "LASTPRICE": "1500", "TIMESTMP": "x"},
    {"INSTRUMENTTYPE": "FUTIDX", "SYMBOL": "NIFTY", "EXPIRYDATE": "16-Nov-2026",
     "LASTPRICE": "0", "TIMESTMP": "x"},
]


@pytest.fixture(autouse=True)
def _no_cache():
    g._cache, g._cache_ts = {}, 0.0
    yield
    g._cache, g._cache_ts = {}, 0.0


def test_pick_near_month_skips_zero_price_and_other_symbols():
    q = g._pick_near_month(NSEIX_ROWS)
    assert q == {"price": 25266.0, "expiry": "2026-11-23", "traded_at": "14-Nov-2026 02:44:30"}
    assert g._pick_near_month([]) is None
    assert g._pick_near_month([{"INSTRUMENTTYPE": "FUTIDX", "SYMBOL": "NIFTY",
                                "EXPIRYDATE": "bad", "LASTPRICE": "1"}]) is None


def test_fetch_nseix_parses_live_shape():
    resp = MagicMock(status_code=200)
    resp.json.return_value = {"data": NSEIX_ROWS}
    with patch("requests.Session.get", return_value=resp), \
         patch("data.proxy_session.proxy_configured", return_value=False):
        assert g._fetch_nseix()["price"] == 25266.0


def test_fetch_nseix_http_error_returns_none():
    with patch("requests.Session.get", return_value=MagicMock(status_code=403)), \
         patch("data.proxy_session.proxy_configured", return_value=False):
        assert g._fetch_nseix() is None


def test_signal_uses_nseix_premium():
    with patch.object(g, "_get_prev_nifty_close", return_value=25144.3), \
         patch.object(g, "_fetch_nseix", return_value=g._pick_near_month(NSEIX_ROWS)):
        s = g.get_gift_nifty_signal(force_refresh=True)
    assert s["source"] == "nseix"
    assert s["gift_price"] == 25266.0 and s["premium_pts"] == 121.7
    assert s["premium_pct"] == pytest.approx(0.484, abs=1e-3)
    assert s["gift_traded_at"] == "14-Nov-2026 02:44:30"
    assert "last trade 14-Nov-2026 02:44:30 IST" in s["market_note"]


def test_strong_premium_classified():
    with patch.object(g, "_get_prev_nifty_close", return_value=25000.0), \
         patch.object(g, "_fetch_nseix", return_value={"price": 25300.0, "expiry": None, "traded_at": None}):
        s = g.get_gift_nifty_signal(force_refresh=True)
    assert (s["signal"], s["signal_strength"]) == ("POSITIVE_OPEN", "STRONG")


def test_all_sources_failing_is_unavailable_not_zero(caplog):
    with patch.object(g, "_get_prev_nifty_close", return_value=25144.3), \
         patch.object(g, "_fetch_nseix", return_value=None), \
         patch.object(g, "_fetch_nseindia", return_value=None), \
         patch.object(g, "_fetch_moneycontrol", return_value=None):
        s = g.get_gift_nifty_signal(force_refresh=True)
    assert s["signal"] == "UNAVAILABLE"
    assert s["premium_pct"] is None and s["gift_price"] is None
    assert "every source failed" in caplog.text


def test_falls_back_to_next_source():
    with patch.object(g, "_get_prev_nifty_close", return_value=25000.0), \
         patch.object(g, "_fetch_nseix", return_value=None), \
         patch.object(g, "_fetch_nseindia", return_value={"price": 25100.0, "expiry": None, "traded_at": None}):
        s = g.get_gift_nifty_signal(force_refresh=True)
    assert s["source"] == "nseindia" and s["premium_pts"] == 100.0


def test_no_nifty_index_stand_in_source():
    """The ^NSEI 'yfinance_intraday' fallback must never come back."""
    assert not hasattr(g, "_fetch_yfinance_futures")
    src = inspect.getsource(g.get_gift_nifty_signal)
    assert "yfinance_intraday" not in src


def _hist(dates, closes):
    import pandas as pd
    return pd.DataFrame({"Close": closes}, index=pd.DatetimeIndex(dates))


@pytest.mark.parametrize("now,expected", [
    (datetime(2026, 11, 16, 11, 0), 25144.3),    # Mon during NSE hours → previous session
    (datetime(2026, 11, 16, 16, 0), 25300.0),    # Mon after close → today's close
])
def test_prev_close_skips_todays_unfinished_bar(now, expected):
    hist = _hist(["2026-11-13", "2026-11-16"], [25144.3, 25300.0])
    ticker = MagicMock()
    ticker.history.return_value = hist
    with patch("yfinance.Ticker", return_value=ticker), patch.object(g, "_ist_now", return_value=now):
        assert g._get_prev_nifty_close() == expected


# ── worker job: a broken feed is WARNING, not OK ─────────────────────────────

@pytest.fixture
def ledger():
    from data import run_log
    c = MagicMock()
    with patch.object(run_log, "_supabase_client", return_value=c):
        yield c


def _row(c):
    return c.table.return_value.insert.call_args_list[-1].args[0]


def test_job_unavailable_is_warning(ledger):
    import worker
    with patch("data.gift_nifty_fetcher.get_gift_nifty_signal",
               return_value={"signal": "UNAVAILABLE", "source": "unavailable"}):
        out = worker.job_gift_nifty_fetch()
    assert out["count"] == 0
    assert _row(ledger)["status"] == "WARNING"


def test_job_stale_quote_is_warning(ledger):
    import worker
    with patch("data.gift_nifty_fetcher.get_gift_nifty_signal",
               return_value={"signal": "FLAT", "signal_strength": "WEAK", "premium_pct": 0.1,
                             "premium_pts": 25, "source": "nseix",
                             "gift_traded_at": "01-Nov-2026 02:44:30"}):
        worker.job_gift_nifty_fetch()
    assert _row(ledger)["status"] == "WARNING"


def test_job_fresh_quote_is_ok(ledger):
    import worker
    from datetime import datetime as dt
    fresh = dt.now(worker.IST).strftime("%d-%b-%Y %H:%M:%S")
    with patch("data.gift_nifty_fetcher.get_gift_nifty_signal",
               return_value={"signal": "FLAT", "signal_strength": "WEAK", "premium_pct": 0.48,
                             "premium_pts": 121.7, "source": "nseix", "gift_traded_at": fresh}):
        out = worker.job_gift_nifty_fetch()
    assert out["count"] == 1 and _row(ledger)["status"] == "OK"


# ── API: GIFT card sits right after NIFTY 50 (inside the ticker's first six) ─

def test_pulse_places_gift_after_nifty():
    from api import main
    base = [{"key": k, "value": "1", "change": "+0.1%", "up": True}
            for k in ("NIFTY 50", "SENSEX", "NIFTY BANK", "GOLD MCX", "CRUDE MCX", "INR/USD",
                      "INDIA VIX", "FII NET")]
    gift = {"signal": "FLAT", "signal_strength": "WEAK", "premium_pct": 0.484,
            "gift_price": 25266.0, "market_note": "note"}
    main._market_cache, main._market_cache_ts = [], 0.0
    with patch.object(main, "_fetch_prices_sync", return_value={}), \
         patch.object(main, "_build_pulse", return_value=list(base)), \
         patch.object(main, "_supabase", None, create=True), \
         patch("data.gift_nifty_fetcher.get_gift_nifty_signal", return_value=gift):
        pulse = asyncio.run(main._get_market_pulse())
    main._market_cache, main._market_cache_ts = [], 0.0
    keys = [m["key"] for m in pulse]
    assert keys.index("GIFT NIFTY") == 1
    card = pulse[1]
    assert card["value"] == "25,266" and card["change"] == "+0.48%" and card["up"] is True


def test_pulse_omits_gift_when_unavailable():
    from api import main
    base = [{"key": "NIFTY 50", "value": "1", "change": "+0.1%", "up": True}]
    main._market_cache, main._market_cache_ts = [], 0.0
    with patch.object(main, "_fetch_prices_sync", return_value={}), \
         patch.object(main, "_build_pulse", return_value=list(base)), \
         patch.object(main, "_supabase", None, create=True), \
         patch("data.gift_nifty_fetcher.get_gift_nifty_signal",
               return_value={"signal": "UNAVAILABLE", "premium_pct": None}):
        pulse = asyncio.run(main._get_market_pulse())
    main._market_cache, main._market_cache_ts = [], 0.0
    assert [m["key"] for m in pulse] == ["NIFTY 50"]
