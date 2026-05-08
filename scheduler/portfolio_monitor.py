"""
scheduler/portfolio_monitor.py — Bharat Intelligence Portfolio Monitor
=======================================================================
Runs in two modes:
  1. Called by orchestrator.py after every daily analysis cycle
  2. Standalone APScheduler job every 2 hours during market hours (09:00–15:30 IST)

Per-holding logic
─────────────────
  • Fetch current price via yfinance; update portfolio_holdings.current_price
  • CRITICAL DANGER detection  → severity='CRITICAL', alert_type='CRITICAL_DANGER'
      Trigger:  latest rec for symbol has danger_drop_pct >= 70
                AND danger_confidence >= 65
      Also check: FII sold > ₹500 Cr in last 5 sessions
                  AND regulatory/negative news detected in RSS headlines
  • STOPLOSS_PROXIMITY alert    → severity='DANGER',  <10% above stoploss_price
  • TARGET_PROXIMITY alert      → severity='INFO',    <12% below target_price
  • REC_MILESTONE alert         → severity='INFO',    holding gained >15% vs avg_buy

De-duplication
──────────────
  An alert is suppressed if an unresolved alert of the same
  (holding_id, alert_type) already exists in the last 24 hours.

Telegram
────────
  CRITICAL alerts are sent immediately via Telegram Bot API.
  Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env.

Usage
─────
  python scheduler/portfolio_monitor.py              # start 2h IST scheduler
  python scheduler/portfolio_monitor.py --run-now    # run once immediately
  python scheduler/portfolio_monitor.py --run-now --dry   # no DB/Telegram writes
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()

# ── Project root on sys.path ──────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("portfolio_monitor")

# ── Thresholds ────────────────────────────────────────────────────────────────
_CRITICAL_DANGER_DROP_PCT   = 70.0   # danger_drop_pct >= this → CRITICAL DANGER
_CRITICAL_DANGER_CONF       = 65.0   # danger_confidence >= this (AND condition)
_FII_SELL_THRESHOLD_CR      = -500.0 # net FII < -500 Cr over 5 sessions
_STOPLOSS_PROXIMITY_PCT     = 10.0   # price within 10% above stoploss
_TARGET_PROXIMITY_PCT       = 12.0   # price within 12% below target
_MILESTONE_GAIN_PCT         = 15.0   # holding gained >15% vs avg_buy
_DEDUP_WINDOW_HOURS         = 24     # suppress duplicate alert within this window

# ── Market hours (IST, 24h) ───────────────────────────────────────────────────
_MARKET_OPEN_H  = 9
_MARKET_OPEN_M  = 0
_MARKET_CLOSE_H = 15
_MARKET_CLOSE_M = 30


# ─────────────────────────────────────────────────────────────────────────────
# Supabase helper
# ─────────────────────────────────────────────────────────────────────────────

def _supabase():
    """Return a live Supabase client, or None if credentials are absent."""
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception as exc:
        log.warning("Supabase connect failed: %s", exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Price fetching
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_current_price(symbol: str) -> Optional[float]:
    """
    Fetch latest close price for a symbol via yfinance.
    Returns None on failure.
    """
    try:
        from data.symbol_map import resolve_yf, is_excluded  # noqa: E402
        if is_excluded(symbol):
            return None
        resolved = resolve_yf(symbol)
        if not resolved:
            return None
        from data.fetchers import yf_fetch_with_retry
        ticker = yf.Ticker(resolved)
        hist   = yf_fetch_with_retry(ticker.history, period="2d")
        if not hist.empty:
            return round(float(hist["Close"].iloc[-1]), 2)
        # Fallback: info dict
        info  = yf_fetch_with_retry(lambda: ticker.info)
        price = info.get("regularMarketPrice") or info.get("previousClose")
        return round(float(price), 2) if price else None
    except Exception as exc:
        log.warning("Price fetch failed for %s: %s", symbol, exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# FII & news helpers for CRITICAL DANGER secondary conditions
# ─────────────────────────────────────────────────────────────────────────────

def _fii_net_5session(client) -> float:
    """
    Return the sum of FII net flow over the last 5 trading sessions
    from the institutional_flows table. Returns 0.0 on any failure.
    """
    try:
        resp = (
            client.table("institutional_flows")
            .select("fii_net")
            .order("session_date", desc=True)
            .limit(5)
            .execute()
        )
        rows = resp.data or []
        return sum(float(r.get("fii_net") or 0) for r in rows)
    except Exception as exc:
        log.debug("FII 5-session fetch failed: %s", exc)
        return 0.0


def _has_negative_news(symbol: str) -> bool:
    """
    Return True if RSS headlines for this symbol contain regulatory /
    negative sentiment keywords in the last 48 hours.
    """
    _NEGATIVE_KEYWORDS = [
        "sebi", "ed probe", "enforcement", "fraud", "scam", "default",
        "ban", "suspension", "penalty", "fine", "raid", "investigation",
        "insolvency", "nclt", "downgrade", "delist", "blow", "crisis",
    ]
    try:
        from data.fetchers import get_rss_headlines  # noqa: E402
        headlines = get_rss_headlines(symbol) or []
        for h in headlines:
            title = (h.get("title") or "").lower()
            if any(kw in title for kw in _NEGATIVE_KEYWORDS):
                return True
    except Exception as exc:
        log.debug("News check failed for %s: %s", symbol, exc)
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Alert de-duplication
# ─────────────────────────────────────────────────────────────────────────────

def _alert_exists(
    client,
    holding_id: str,
    alert_type: str,
    window_hours: int = _DEDUP_WINDOW_HOURS,
) -> bool:
    """
    Return True if an unresolved alert of this (holding_id, alert_type)
    was already created in the last `window_hours` hours.
    """
    try:
        since = (
            datetime.now(timezone.utc) - timedelta(hours=window_hours)
        ).isoformat()
        resp = (
            client.table("portfolio_alerts")
            .select("id")
            .eq("holding_id", holding_id)
            .eq("alert_type", alert_type)
            .eq("resolved", False)
            .gte("created_at", since)
            .limit(1)
            .execute()
        )
        return bool(resp.data)
    except Exception as exc:
        log.debug("Alert dedup check failed: %s", exc)
        return False  # fail open: let the alert through if we can't check


# ─────────────────────────────────────────────────────────────────────────────
# Alert creation
# ─────────────────────────────────────────────────────────────────────────────

def _create_alert(
    client,
    holding_id: str,
    symbol:     str,
    severity:   str,
    alert_type: str,
    title:      str,
    detail:     str,
    dry_run:    bool = False,
) -> Optional[str]:
    """
    Insert a row into portfolio_alerts. Returns the new alert id, or None.
    In dry_run mode prints the alert and returns a fake id.
    """
    if dry_run:
        tag = {"CRITICAL": "!!", "DANGER": "! ", "WARNING": "* ", "INFO": "  "}.get(severity, "  ")
        print(f"  [{tag}] [{severity}] [{alert_type}] {title}")
        print(f"       {detail}")
        return "dry-run-id"

    try:
        resp = (
            client.table("portfolio_alerts")
            .insert({
                "holding_id": holding_id,
                "symbol":     symbol,
                "severity":   severity,
                "alert_type": alert_type,
                "title":      title,
                "detail":     detail,
                "resolved":   False,
            })
            .execute()
        )
        if resp.data:
            return str(resp.data[0]["id"])
    except Exception as exc:
        log.error("Alert insert failed (%s / %s): %s", symbol, alert_type, exc)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Telegram notification
# ─────────────────────────────────────────────────────────────────────────────

def _send_telegram(message: str, dry_run: bool = False) -> bool:
    """
    Send a message via Telegram Bot API.
    Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in environment.
    Returns True on success.
    """
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    if dry_run:
        print(f"\n  [TELEGRAM DRY RUN]\n{message}\n")
        return True

    if not token or not chat_id:
        log.warning(
            "Telegram not configured — set TELEGRAM_BOT_TOKEN and "
            "TELEGRAM_CHAT_ID in .env to enable critical alerts"
        )
        return False

    try:
        url  = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(
            url,
            json={
                "chat_id":    chat_id,
                "text":       message,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        resp.raise_for_status()
        log.info("Telegram alert sent OK (chat_id=%s)", chat_id)
        return True
    except Exception as exc:
        log.error("Telegram send failed: %s", exc)
        return False


def _build_telegram_message(
    symbol:          str,
    danger_drop_pct: float,
    danger_window:   Optional[int],
    danger_confidence: float,
    trigger_summary: str,
) -> str:
    """Build the critical danger Telegram message in the required format."""
    window_str = f"{danger_window} days" if danger_window else "near term"
    # Plain ASCII for the base; HTML bold tags for Telegram's parse_mode=HTML
    return (
        f"<b>CRITICAL DANGER -- {symbol}</b>\n"
        f"{danger_drop_pct:.1f}% drop predicted in {window_str}\n"
        f"Confidence: {danger_confidence:.0f}%\n\n"
        f"{trigger_summary}\n\n"
        f"Open dashboard for full analysis."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Per-holding analysis
# ─────────────────────────────────────────────────────────────────────────────

def _analyse_holding(
    holding:     dict,
    client,
    fii_5session: float,
    dry_run:     bool = False,
) -> dict:
    """
    Run all alert checks for a single holding.

    Returns a summary dict:
        {symbol, holding_id, current_price, pnl_pct, alerts_created: list[str]}
    """
    holding_id = holding["id"]
    symbol     = holding["symbol"]
    avg_buy    = float(holding.get("avg_buy") or 0)
    stoploss   = float(holding.get("stoploss_price") or 0) or None
    target     = float(holding.get("target_price") or 0) or None

    # Danger fields stored on the holding (synced from latest rec)
    h_danger_drop = float(holding.get("danger_drop_pct") or 0)
    h_danger_conf = float(holding.get("danger_confidence") or 0)
    h_danger_trig = holding.get("danger_trigger") or ""
    h_danger_win  = holding.get("danger_window")

    alerts_created: list[str] = []

    # ── 1. Fetch & update current price ──────────────────────────────────────
    current_price = _fetch_current_price(symbol)

    if current_price is not None and not dry_run:
        try:
            client.table("portfolio_holdings") \
                .update({"current_price": current_price}) \
                .eq("id", holding_id) \
                .execute()
        except Exception as exc:
            log.warning("[%s] current_price update failed: %s", symbol, exc)

    price = current_price or float(holding.get("current_price") or avg_buy)
    pnl_pct = ((price - avg_buy) / avg_buy * 100) if avg_buy > 0 else 0.0

    log.info(
        "[%s] price=%.2f  avg_buy=%.2f  pnl=%.1f%%",
        symbol, price, avg_buy, pnl_pct,
    )

    # ── 2. Fetch latest recommendation for this symbol ───────────────────────
    # NOTE: only select columns that exist in the recommendations table.
    # danger_drop_pct / danger_confidence / danger_trigger / danger_window live
    # on portfolio_holdings, NOT on recommendations — querying them causes HTTP 400.
    latest_rec: dict = {}
    try:
        resp = (
            client.table("recommendations")
            .select("action, target, stoploss")
            .eq("symbol", symbol)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if resp.data:
            latest_rec = resp.data[0]
    except Exception as exc:
        log.debug("[%s] latest rec fetch failed: %s", symbol, exc)

    # Effective danger values — sourced entirely from portfolio_holdings row
    # (written there by the orchestrator after each agent run).
    eff_danger_drop = h_danger_drop
    eff_danger_conf = h_danger_conf
    eff_danger_trig = h_danger_trig or "Multi-agent danger signal"
    eff_danger_win  = h_danger_win

    # ── 3. CRITICAL DANGER alert ─────────────────────────────────────────────
    is_critical = (
        eff_danger_drop >= _CRITICAL_DANGER_DROP_PCT
        and eff_danger_conf >= _CRITICAL_DANGER_CONF
    )

    # Secondary confirmation: FII heavy selling + negative news
    fii_selling_heavy = fii_5session < _FII_SELL_THRESHOLD_CR
    has_neg_news      = _has_negative_news(symbol) if is_critical else False

    # CRITICAL requires primary signal; secondary conditions raise severity further
    # but are not required (defence-in-depth: don't miss critical on FII data lag)
    if is_critical:
        trigger_parts = [eff_danger_trig]
        if fii_selling_heavy:
            trigger_parts.append(
                f"FII net sold Rs {abs(fii_5session):.0f} Cr over last 5 sessions"
            )
        if has_neg_news:
            trigger_parts.append("Negative regulatory/news signals detected")
        trigger_summary = " | ".join(filter(None, trigger_parts))

        if not _alert_exists(client, holding_id, "CRITICAL_DANGER"):
            alert_id = _create_alert(
                client, holding_id, symbol,
                severity   = "CRITICAL",
                alert_type = "CRITICAL_DANGER",
                title      = (
                    f"CRITICAL DANGER: {symbol} — "
                    f"{eff_danger_drop:.0f}% drawdown predicted "
                    f"(confidence {eff_danger_conf:.0f}%)"
                ),
                detail     = trigger_summary,
                dry_run    = dry_run,
            )
            if alert_id:
                alerts_created.append("CRITICAL_DANGER")
                log.warning(
                    "[%s] CRITICAL DANGER alert created — "
                    "drop=%.0f%% conf=%.0f%% fii_heavy=%s neg_news=%s",
                    symbol, eff_danger_drop, eff_danger_conf,
                    fii_selling_heavy, has_neg_news,
                )

                # Telegram — fire immediately, don't wait for daily run
                tg_msg = _build_telegram_message(
                    symbol           = symbol,
                    danger_drop_pct  = eff_danger_drop,
                    danger_window    = eff_danger_win,
                    danger_confidence= eff_danger_conf,
                    trigger_summary  = trigger_summary,
                )
                _send_telegram(tg_msg, dry_run=dry_run)
        else:
            log.info("[%s] CRITICAL_DANGER already open — suppressed (dedup)", symbol)

    # ── 4. STOPLOSS_PROXIMITY alert ───────────────────────────────────────────
    if stoploss and stoploss > 0 and price > 0:
        pct_above_sl = (price - stoploss) / stoploss * 100
        if 0 <= pct_above_sl < _STOPLOSS_PROXIMITY_PCT:
            if not _alert_exists(client, holding_id, "STOPLOSS_PROXIMITY"):
                alert_id = _create_alert(
                    client, holding_id, symbol,
                    severity   = "DANGER",
                    alert_type = "STOPLOSS_PROXIMITY",
                    title      = (
                        f"Stoploss proximity: {symbol} is "
                        f"{pct_above_sl:.1f}% above stoploss Rs {stoploss:.2f}"
                    ),
                    detail     = (
                        f"Current price Rs {price:.2f} | "
                        f"Stoploss Rs {stoploss:.2f} | "
                        f"Buffer {pct_above_sl:.1f}%"
                    ),
                    dry_run    = dry_run,
                )
                if alert_id:
                    alerts_created.append("STOPLOSS_PROXIMITY")
        elif price <= stoploss:
            # Price has actually hit or breached stoploss
            if not _alert_exists(client, holding_id, "STOPLOSS_HIT"):
                alert_id = _create_alert(
                    client, holding_id, symbol,
                    severity   = "CRITICAL",
                    alert_type = "STOPLOSS_HIT",
                    title      = (
                        f"Stoploss HIT: {symbol} at Rs {price:.2f} "
                        f"— below stoploss Rs {stoploss:.2f}"
                    ),
                    detail     = (
                        f"Current Rs {price:.2f} | "
                        f"Stoploss Rs {stoploss:.2f} | "
                        f"PnL {pnl_pct:.1f}% vs avg buy"
                    ),
                    dry_run    = dry_run,
                )
                if alert_id:
                    alerts_created.append("STOPLOSS_HIT")
                    tg_msg = (
                        f"<b>STOPLOSS HIT -- {symbol}</b>\n"
                        f"Price Rs {price:.2f} breached stoploss Rs {stoploss:.2f}\n"
                        f"PnL: {pnl_pct:.1f}% vs avg buy\n\n"
                        f"Review position immediately."
                    )
                    _send_telegram(tg_msg, dry_run=dry_run)

    # ── 5. TARGET_PROXIMITY alert ─────────────────────────────────────────────
    if target and target > 0 and price > 0:
        pct_to_target = (target - price) / target * 100
        if 0 < pct_to_target < _TARGET_PROXIMITY_PCT:
            if not _alert_exists(client, holding_id, "TARGET_PROXIMITY"):
                alert_id = _create_alert(
                    client, holding_id, symbol,
                    severity   = "INFO",
                    alert_type = "TARGET_PROXIMITY",
                    title      = (
                        f"Near target: {symbol} is "
                        f"{pct_to_target:.1f}% below target Rs {target:.2f}"
                    ),
                    detail     = (
                        f"Current Rs {price:.2f} | "
                        f"Target Rs {target:.2f} | "
                        f"Gap {pct_to_target:.1f}%"
                    ),
                    dry_run    = dry_run,
                )
                if alert_id:
                    alerts_created.append("TARGET_PROXIMITY")
        elif price >= target:
            # Target reached
            if not _alert_exists(client, holding_id, "TARGET_HIT"):
                alert_id = _create_alert(
                    client, holding_id, symbol,
                    severity   = "INFO",
                    alert_type = "TARGET_HIT",
                    title      = (
                        f"Target reached: {symbol} at Rs {price:.2f} "
                        f"— hit target Rs {target:.2f}"
                    ),
                    detail     = (
                        f"Current Rs {price:.2f} | "
                        f"Target Rs {target:.2f} | "
                        f"PnL {pnl_pct:.1f}% vs avg buy. Consider booking profits."
                    ),
                    dry_run    = dry_run,
                )
                if alert_id:
                    alerts_created.append("TARGET_HIT")

    # ── 6. REC_MILESTONE alert (>15% gain vs avg_buy) ─────────────────────────
    if avg_buy > 0 and pnl_pct >= _MILESTONE_GAIN_PCT:
        if not _alert_exists(client, holding_id, "REC_MILESTONE"):
            alert_id = _create_alert(
                client, holding_id, symbol,
                severity   = "INFO",
                alert_type = "REC_MILESTONE",
                title      = (
                    f"Milestone: {symbol} up {pnl_pct:.1f}% vs avg buy "
                    f"Rs {avg_buy:.2f}"
                ),
                detail     = (
                    f"Current Rs {price:.2f} | "
                    f"Avg buy Rs {avg_buy:.2f} | "
                    f"Gain {pnl_pct:.1f}%"
                ),
                dry_run    = dry_run,
            )
            if alert_id:
                alerts_created.append("REC_MILESTONE")

    return {
        "symbol":         symbol,
        "holding_id":     holding_id,
        "current_price":  price,
        "pnl_pct":        round(pnl_pct, 2),
        "alerts_created": alerts_created,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main run function
# ─────────────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False) -> dict:
    """
    Execute a full portfolio monitor cycle.

    Called by orchestrator.monitor_node() after each daily pipeline run
    and directly by the 2-hour APScheduler job.

    Returns:
        {
            "holdings_checked": int,
            "prices_updated":   int,
            "alerts_created":   int,
            "errors":           list[str],
            "duration_seconds": float,
        }
    """
    t0     = time.time()
    errors: list[str] = []

    client = _supabase()
    if not client and not dry_run:
        log.error("Supabase unavailable — portfolio monitor cannot run")
        return {
            "holdings_checked": 0, "prices_updated": 0,
            "alerts_created": 0, "errors": ["Supabase unavailable"],
            "duration_seconds": round(time.time() - t0, 2),
        }

    # ── Load open holdings ────────────────────────────────────────────────────
    holdings: list[dict] = []
    if client:
        try:
            resp = (
                client.table("portfolio_holdings")
                .select("*")
                .eq("status", "OPEN")
                .execute()
            )
            holdings = resp.data or []
        except Exception as exc:
            log.error("Failed to load portfolio holdings: %s", exc)
            errors.append(f"Holdings load: {exc}")

    if not holdings:
        log.info("No open holdings found — nothing to monitor")
        return {
            "holdings_checked": 0, "prices_updated": 0,
            "alerts_created": 0, "errors": errors,
            "duration_seconds": round(time.time() - t0, 2),
        }

    log.info("Monitoring %d open holding(s)...", len(holdings))

    # ── Pre-fetch FII 5-session net once (shared across all holdings) ─────────
    fii_5session = _fii_net_5session(client) if client else 0.0
    log.info("FII net 5-session: Rs %.0f Cr", fii_5session)

    # ── Dry run header ────────────────────────────────────────────────────────
    if dry_run:
        print("\n" + "-" * 65)
        print(f"  PORTFOLIO MONITOR DRY RUN -- {len(holdings)} holding(s)")
        print(f"  FII 5-session net: Rs {fii_5session:.0f} Cr")
        print("-" * 65)

    # ── Analyse each holding ──────────────────────────────────────────────────
    total_alerts   = 0
    prices_updated = 0

    for holding in holdings:
        symbol = holding.get("symbol", "?")
        try:
            result = _analyse_holding(
                holding      = holding,
                client       = client,
                fii_5session = fii_5session,
                dry_run      = dry_run,
            )
            if result["current_price"] is not None:
                prices_updated += 1
            total_alerts += len(result["alerts_created"])

        except Exception as exc:
            log.error("[%s] analyse_holding failed: %s", symbol, exc)
            errors.append(f"{symbol}: {exc}")

    duration = round(time.time() - t0, 2)

    if dry_run:
        print("-" * 65)
        print(
            f"  Summary: {len(holdings)} checked | "
            f"{prices_updated} prices updated | "
            f"{total_alerts} alerts | "
            f"{len(errors)} errors | "
            f"{duration}s"
        )
        print("-" * 65 + "\n")

    log.info(
        "Monitor complete — checked=%d  prices_updated=%d  "
        "alerts=%d  errors=%d  duration=%.1fs",
        len(holdings), prices_updated, total_alerts, len(errors), duration,
    )

    return {
        "holdings_checked": len(holdings),
        "prices_updated":   prices_updated,
        "alerts_created":   total_alerts,
        "errors":           errors,
        "duration_seconds": duration,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Market-hours guard
# ─────────────────────────────────────────────────────────────────────────────

def _is_market_hours() -> bool:
    """
    Return True if current IST time is within NSE market hours
    (09:00 – 15:30, Monday–Friday).
    """
    try:
        from zoneinfo import ZoneInfo
        IST = ZoneInfo("Asia/Kolkata")
    except ImportError:
        import pytz
        IST = pytz.timezone("Asia/Kolkata")

    now = datetime.now(tz=IST)
    if now.weekday() >= 5:          # Saturday=5, Sunday=6
        return False
    open_t  = now.replace(hour=_MARKET_OPEN_H,  minute=_MARKET_OPEN_M,  second=0, microsecond=0)
    close_t = now.replace(hour=_MARKET_CLOSE_H, minute=_MARKET_CLOSE_M, second=0, microsecond=0)
    return open_t <= now <= close_t


# ─────────────────────────────────────────────────────────────────────────────
# APScheduler + CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bharat Intelligence Portfolio Monitor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scheduler/portfolio_monitor.py              # start 2h scheduler (market hours)
  python scheduler/portfolio_monitor.py --run-now    # run once immediately
  python scheduler/portfolio_monitor.py --run-now --dry   # dry run, no DB/Telegram writes
        """,
    )
    parser.add_argument(
        "--run-now", action="store_true",
        help="Execute one monitor cycle immediately",
    )
    parser.add_argument(
        "--dry", action="store_true",
        help="Dry run: no Supabase writes, no Telegram messages; prints to stdout",
    )
    args = parser.parse_args()

    if args.run_now:
        log.info("Running portfolio monitor immediately (dry=%s)...", args.dry)
        result = run(dry_run=args.dry)
        if result["errors"]:
            log.warning(
                "%d error(s):\n  %s",
                len(result["errors"]),
                "\n  ".join(result["errors"]),
            )
        return

    # ── Scheduled mode: every 2 hours during market hours ────────────────────
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron       import CronTrigger
    except ImportError:
        log.error("apscheduler not installed — run: pip install apscheduler")
        sys.exit(1)

    try:
        from zoneinfo import ZoneInfo
        IST = ZoneInfo("Asia/Kolkata")
    except ImportError:
        import pytz
        IST = pytz.timezone("Asia/Kolkata")

    def _scheduled_job() -> None:
        if not _is_market_hours():
            log.info("Outside market hours — skipping monitor cycle")
            return
        log.info("Scheduled monitor cycle firing...")
        run(dry_run=False)

    scheduler = BlockingScheduler(timezone=IST)

    # Fire at 09:15, 11:15, 13:15, 15:15 — 4 times during market hours
    # (avoids open/close auction noise at exact 09:00 and 15:30)
    for hour in [9, 11, 13, 15]:
        scheduler.add_job(
            _scheduled_job,
            CronTrigger(hour=hour, minute=15, day_of_week="mon-fri", timezone=IST),
            id              = f"portfolio_monitor_{hour}h",
            name            = f"Portfolio Monitor {hour}:15 IST",
            max_instances   = 1,
            coalesce        = True,
            misfire_grace_time = 300,   # 5 min grace — don't skip if slightly late
        )

    log.info("-" * 60)
    log.info("  Portfolio Monitor — scheduler started")
    log.info("  Schedule: 09:15 / 11:15 / 13:15 / 15:15 IST (Mon-Fri)")
    log.info("  Press Ctrl+C to stop")
    log.info("-" * 60)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Portfolio monitor scheduler stopped cleanly")


if __name__ == "__main__":
    main()
