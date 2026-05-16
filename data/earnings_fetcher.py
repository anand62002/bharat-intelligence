"""
data/earnings_fetcher.py — Earnings Calendar Data Fetcher
==========================================================
Fetches upcoming earnings dates for NSE/BSE-listed stocks and maintains
the earnings_calendar Supabase table.

Strategy (tries in order, returns first success):
  1. yfinance Ticker.calendar (works for ~40% of Indian stocks on Yahoo)
  2. Heuristic: last_known_quarter_date + 91 days
  3. None if no data available

Setup (run once in Supabase SQL Editor):
-----------------------------------------
  CREATE TABLE IF NOT EXISTS earnings_calendar (
      id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      symbol          TEXT NOT NULL,
      earnings_date   DATE NOT NULL,
      quarter         TEXT,
      source          TEXT,
      confirmed       BOOLEAN DEFAULT FALSE,
      created_at      TIMESTAMPTZ DEFAULT now(),
      UNIQUE(symbol, earnings_date)
  );
  CREATE INDEX IF NOT EXISTS idx_earnings_cal_date   ON earnings_calendar (earnings_date);
  CREATE INDEX IF NOT EXISTS idx_earnings_cal_symbol ON earnings_calendar (symbol);
  GRANT ALL ON earnings_calendar TO service_role;

Usage
-----
  python -m data.earnings_fetcher                          # fetch & upsert for portfolio symbols
  python -m data.earnings_fetcher --symbol RELIANCE        # single symbol
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ─────────────────────────────────────────────────────────────────────────────
# Supabase helper
# ─────────────────────────────────────────────────────────────────────────────

def _supabase():
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_KEY", "")
    if not (url and key):
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception as exc:
        log.warning("Supabase init failed: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Quarter label helper
# ─────────────────────────────────────────────────────────────────────────────

def _quarter_label(d: date) -> str:
    """Return Indian FY quarter label for a date. e.g. Q1FY26 (Apr-Jun 2025)."""
    # Indian FY: Apr = Q1, Jul = Q2, Oct = Q3, Jan = Q4
    fy_year = d.year if d.month >= 4 else d.year - 1
    q_map   = {4: "Q1", 5: "Q1", 6: "Q1",
               7: "Q2", 8: "Q2", 9: "Q2",
               10: "Q3", 11: "Q3", 12: "Q3",
               1: "Q4",  2: "Q4",  3: "Q4"}
    q_label = q_map.get(d.month, "Q?")
    return f"{q_label}FY{str(fy_year + 1)[-2:]}"


# ─────────────────────────────────────────────────────────────────────────────
# Fetchers
# ─────────────────────────────────────────────────────────────────────────────

def _yfinance_earnings_date(symbol: str) -> date | None:
    """
    Try yfinance Ticker.calendar for next earnings date.
    Returns date or None.
    """
    try:
        import yfinance as yf
        # Use YF_SYMBOL_MAP to resolve correct ticker — handles BSE-only stocks
        # (GEVERNOVA→522275.BO, ELFORGE→ELFORGE.BO) and NSE aliases
        # (IHCL→INDHOTEL.NS, SHAKTIPUMPS→SHAKTIPUMP.NS, etc.)
        if "." in symbol or symbol.startswith("^"):
            yf_sym = symbol
        else:
            try:
                from data.symbol_map import YF_SYMBOL_MAP
                yf_sym = YF_SYMBOL_MAP.get(symbol.upper(), f"{symbol.upper()}.NS")
            except ImportError:
                yf_sym = f"{symbol.upper()}.NS"
        t      = yf.Ticker(yf_sym)
        cal    = t.calendar
        if cal is None:
            return None
        # calendar is a dict: {"Earnings Date": [Timestamp, ...], ...}
        if isinstance(cal, dict):
            dates = cal.get("Earnings Date") or cal.get("earnings_date")
            if dates and len(dates) > 0:
                import pandas as pd
                d = pd.Timestamp(dates[0]).date()
                if d >= date.today():
                    return d
        return None
    except Exception as exc:
        log.debug("yfinance calendar failed for %s: %s", symbol, exc)
        return None


def _heuristic_earnings_date(symbol: str) -> tuple[date | None, str]:
    """
    Heuristic: fetch last screener quarter date and add ~91 days.
    Returns (estimated_date, quarter_label).
    """
    try:
        from data.fetchers import get_screener_data
        data = get_screener_data(symbol)
        if not data:
            return None, ""
        # screener often returns 'latest_quarter' or we can compute from 'quarter_results'
        # Fallback: assume quarterly results are every 91 days from today's quarter boundary
        today = date.today()
        # Figure out the last quarter end date
        m = today.month
        if m in (1, 2, 3):
            last_qend = date(today.year - 1, 12, 31)
        elif m in (4, 5, 6):
            last_qend = date(today.year, 3, 31)
        elif m in (7, 8, 9):
            last_qend = date(today.year, 6, 30)
        else:
            last_qend = date(today.year, 9, 30)

        # Results typically announced 45 days after quarter end
        est_date  = last_qend + timedelta(days=45)
        if est_date < today:
            # Move to next quarter
            est_date = est_date + timedelta(days=91)
        return est_date, _quarter_label(last_qend)
    except Exception as exc:
        log.debug("Heuristic earnings failed for %s: %s", symbol, exc)
        return None, ""


def _trendlyne_earnings_date(symbol: str) -> tuple[date | None, bool]:
    """
    Try Trendlyne equity page for board meeting / results date.
    Returns (date, confirmed) or (None, False) on failure.

    Requires TRENDLYNE_SESSION env var to be set.
    """
    import os
    if not os.getenv("TRENDLYNE_SESSION"):
        return None, False
    try:
        from data.trendlyne_fetcher import get_upcoming_earnings
        result = get_upcoming_earnings(symbol)
        if result and result.get("date"):
            import datetime as _dt
            d = _dt.date.fromisoformat(result["date"])
            return d, result.get("confirmed", False)
    except Exception as exc:
        log.debug("Trendlyne earnings date failed for %s: %s", symbol, exc)
    return None, False


def fetch_upcoming_earnings(symbols: list[str], days_ahead: int = 30) -> list[dict]:
    """
    Fetch upcoming earnings dates for a list of symbols.
    Priority: yfinance → Trendlyne board meeting → heuristic estimate.
    Returns list of {symbol, earnings_date, quarter, source, confirmed}.
    """
    results = []
    cutoff  = date.today() + timedelta(days=days_ahead)

    for symbol in symbols:
        plain = symbol.replace(".NS", "").replace(".BO", "").upper()

        # 1. yfinance (most reliable — direct calendar data)
        yf_date = _yfinance_earnings_date(plain)
        if yf_date and yf_date <= cutoff:
            results.append({
                "symbol":        plain,
                "earnings_date": str(yf_date),
                "quarter":       _quarter_label(yf_date),
                "source":        "yfinance",
                "confirmed":     True,
            })
            continue

        # 1.5. Trendlyne board meeting / results date (P3-C-P2)
        tl_date, tl_confirmed = _trendlyne_earnings_date(plain)
        if tl_date and tl_date <= cutoff:
            results.append({
                "symbol":        plain,
                "earnings_date": str(tl_date),
                "quarter":       _quarter_label(tl_date),
                "source":        "trendlyne_board_meeting" if tl_confirmed else "trendlyne_result_date",
                "confirmed":     tl_confirmed,
            })
            continue

        # 2. Heuristic (fallback — 45 days after last quarter end)
        est_date, quarter = _heuristic_earnings_date(plain)
        if est_date and est_date <= cutoff:
            results.append({
                "symbol":        plain,
                "earnings_date": str(est_date),
                "quarter":       quarter,
                "source":        "heuristic_estimate",
                "confirmed":     False,
            })

    return results


def upsert_earnings_calendar(records: list[dict]) -> int:
    """Upsert earnings_calendar rows. Returns count upserted."""
    client = _supabase()
    if not client or not records:
        return 0
    try:
        client.table("earnings_calendar").upsert(
            records, on_conflict="symbol,earnings_date"
        ).execute()
        log.info("Upserted %d earnings calendar records", len(records))
        return len(records)
    except Exception as exc:
        log.warning("Earnings calendar upsert failed: %s", exc)
        return 0


def get_earnings_within_days(symbols: list[str], days: int = 7) -> list[dict]:
    """
    Returns earnings_calendar rows for given symbols where earnings_date
    is within the next `days` days.
    """
    client = _supabase()
    if not client:
        return []
    try:
        today  = date.today()
        cutoff = str(today + timedelta(days=days))
        rows   = (
            client
            .table("earnings_calendar")
            .select("symbol,earnings_date,quarter,source,confirmed")
            .in_("symbol", [s.replace(".NS","").replace(".BO","").upper() for s in symbols])
            .gte("earnings_date", str(today))
            .lte("earnings_date", cutoff)
            .execute()
            .data or []
        )
        return rows
    except Exception as exc:
        log.warning("get_earnings_within_days failed: %s", exc)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    from dotenv import load_dotenv
    load_dotenv()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="Earnings Calendar Fetcher")
    parser.add_argument("--symbol", type=str, default=None, help="Single symbol to fetch")
    parser.add_argument("--days",   type=int, default=30,   help="Days ahead to look")
    args = parser.parse_args()

    symbols = [args.symbol] if args.symbol else []
    if not symbols:
        # Default: fetch for portfolio holdings
        client = _supabase()
        if client:
            rows    = client.table("portfolio_holdings").select("symbol").eq("status","OPEN").execute().data or []
            symbols = [r["symbol"] for r in rows]

    if not symbols:
        print("No symbols to fetch. Use --symbol RELIANCE or ensure portfolio has holdings.")
        sys.exit(0)

    print(f"Fetching earnings for {len(symbols)} symbols (next {args.days} days)...")
    records = fetch_upcoming_earnings(symbols, days_ahead=args.days)
    print(f"Found {len(records)} upcoming earnings:")
    for r in records:
        conf = "confirmed" if r["confirmed"] else "estimated"
        print(f"  {r['symbol']:20s}  {r['earnings_date']}  {r['quarter']:8s}  [{r['source']} / {conf}]")

    if records:
        n = upsert_earnings_calendar(records)
        print(f"Upserted {n} records to earnings_calendar table.")
