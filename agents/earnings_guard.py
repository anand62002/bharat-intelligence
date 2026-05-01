"""
agents/earnings_guard.py — Pre-Earnings Risk Guard
===================================================
Checks whether a stock has earnings within a configurable window and returns
a structured warning dict. Used by the orchestrator and discovery screener to
avoid entering new positions before binary earnings events.

Usage
-----
  from agents.earnings_guard import check_pre_earnings
  result = check_pre_earnings("RELIANCE", days_window=7)
  # {'symbol':'RELIANCE','has_upcoming_earnings':True,'earnings_date':'2025-07-28',
  #  'days_until':5,'warning_level':'CRITICAL','quarter':'Q1FY26','source':'yfinance'}
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


def check_pre_earnings(symbol: str, days_window: int = 7) -> dict:
    """
    Check if `symbol` has upcoming earnings within `days_window` days.

    Lookup order:
      1. earnings_calendar Supabase table (most reliable, confirmed/estimated)
      2. yfinance Ticker.calendar (live probe as fallback)

    Returns:
      {
        symbol: str,
        has_upcoming_earnings: bool,
        earnings_date: str | None,
        days_until: int | None,
        warning_level: 'CRITICAL' | 'WARNING' | 'CLEAR',
        quarter: str | None,
        source: str | None,
      }

    warning_level:
      CRITICAL  = earnings in ≤ 3 days
      WARNING   = earnings in 4–7 days (or within days_window)
      CLEAR     = > days_window days away or unknown
    """
    plain = symbol.replace(".NS", "").replace(".BO", "").upper()

    def _result(has: bool, edate: str | None, days: int | None,
                level: str, quarter: str | None = None, source: str | None = None) -> dict:
        return {
            "symbol":               plain,
            "has_upcoming_earnings": has,
            "earnings_date":         edate,
            "days_until":            days,
            "warning_level":         level,
            "quarter":               quarter,
            "source":                source,
        }

    try:
        # ── 1. Supabase earnings_calendar table ──────────────────────────────
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_SERVICE_KEY", "")
        if url and key:
            from supabase import create_client
            client = create_client(url, key)
            today  = date.today()
            cutoff = str(today + timedelta(days=days_window + 7))  # look a bit further

            rows = (
                client
                .table("earnings_calendar")
                .select("symbol,earnings_date,quarter,source,confirmed")
                .eq("symbol", plain)
                .gte("earnings_date", str(today))
                .lte("earnings_date", cutoff)
                .order("earnings_date")
                .limit(1)
                .execute()
                .data or []
            )
            if rows:
                row    = rows[0]
                edate  = date.fromisoformat(row["earnings_date"])
                days_u = (edate - today).days
                if days_u <= days_window:
                    level = "CRITICAL" if days_u <= 3 else "WARNING"
                    return _result(True, str(edate), days_u, level,
                                   row.get("quarter"), row.get("source"))

        # ── 2. yfinance live probe ────────────────────────────────────────────
        from data.earnings_fetcher import _yfinance_earnings_date
        yf_date = _yfinance_earnings_date(plain)
        if yf_date:
            today  = date.today()
            days_u = (yf_date - today).days
            if 0 <= days_u <= days_window:
                level = "CRITICAL" if days_u <= 3 else "WARNING"
                return _result(True, str(yf_date), days_u, level,
                               None, "yfinance_live")

        # ── No earnings found ─────────────────────────────────────────────────
        return _result(False, None, None, "CLEAR")

    except Exception as exc:
        log.debug("check_pre_earnings(%s) failed: %s", plain, exc)
        return _result(False, None, None, "CLEAR", source="error")
