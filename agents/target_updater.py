"""
agents/target_updater.py — Dynamic Target & Stoploss Manager (P7-A)
=====================================================================
Runs daily after market close (17:00 IST) via worker.py.

Three independent mechanisms
─────────────────────────────
1. STOPLOSS RATCHET  (runs for every OPEN holding)
   ≥ +25% gain from avg_buy  →  raise SL to breakeven  (BREAKEVEN level)
   ≥ +40% gain from avg_buy  →  raise SL to lock in 20% gain  (LOCK_20 level)
   Rule: ratchet is one-way — SL only moves up, never down.
   Triggers an INFO alert once per ratchet level.

2. TARGET EXTENSION  (triggers at ≥ 80% progress from entry to target)
   - Runs warren_bot re-analysis (24 h Supabase cache → fast if pipeline ran)
   - Steam check: RSI > 72  AND  (PE > sector×1.8  OR  price > DCF×0.95)
   - No steam   →  extend target to new intrinsic value; increment target_update_count
   - Steam      →  set protect_gains_flag; create WARNING alert; do NOT extend
   - Cap: extension never exceeds 2× original entry→target range above target
   - original_target is recorded once on first extension and never overwritten

3. LAGGARD REVIEW  (> 60 days held AND > 10% below entry, monthly cooldown)
   - Runs warren_bot re-analysis (30-day cooldown via last_review_at)
   - AVOID signal  OR  intrinsic value < current × 1.10  →  WARNING alert
   - User decides — no auto-exit, just surfaces the concern

Prerequisites
─────────────
Run db/migrations/add_target_tracking_columns.sql before deploying.

Entry point
───────────
run_target_updates(dry_run=False) → dict  — called from worker.py
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from typing import Optional

log = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────
_RATCHET_BREAKEVEN_PCT  = 25.0   # gain% needed to move SL to breakeven
_RATCHET_LOCK_PCT       = 40.0   # gain% needed to lock in 20% gain
_LOCK_GAIN_FLOOR        = 0.20   # fraction of avg_buy locked as minimum profit

_TARGET_REVIEW_PROGRESS = 0.80   # 80% of entry→target distance triggers review
_TARGET_EXT_CAP_X       = 2.0    # can't extend more than 2× original range above target

_STEAM_RSI_THRESHOLD    = 72.0
_STEAM_PE_SECTOR_MULT   = 1.8    # PE > sector × 1.8 → stretched valuation
_STEAM_DCF_PROXIMITY    = 0.95   # price > dcf × 0.95 → near full value

_LAGGARD_DAYS           = 60     # days held before laggard check activates
_LAGGARD_LOSS_PCT       = -10.0  # % below entry to qualify as laggard
_LAGGARD_COOLDOWN_DAYS  = 30     # re-check no more than monthly
_LAGGARD_UPSIDE_MIN     = 1.10   # intrinsic must be ≥ 110% of current price to hold


# ── Supabase helper ───────────────────────────────────────────────────────────
def _supabase():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception as e:
        log.warning("target_updater: Supabase init failed: %s", e)
        return None


# ── RSI computation (pandas only, no TA library required) ────────────────────
def _compute_rsi(prices, period: int = 14) -> Optional[float]:
    """Wilder RSI from a pandas Series. Returns None if insufficient data."""
    import pandas as pd
    if not isinstance(prices, pd.Series):
        prices = pd.Series(prices)
    prices = prices.dropna()
    if len(prices) < period + 1:
        return None
    delta = prices.diff().dropna()
    gain  = delta.clip(lower=0)
    loss  = (-delta.clip(upper=0))
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs   = avg_gain / avg_loss.replace(0, float("inf"))
    rsi  = 100 - (100 / (1 + rs))
    val  = float(rsi.iloc[-1])
    return None if (val != val) else val   # guard NaN


# ── Quick technicals (RSI + trailing PE) via yfinance ─────────────────────────
def _quick_technicals(yf_sym: str) -> dict:
    """Fetch RSI-14 and trailing PE without running the full technical agent."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(yf_sym)
        info   = ticker.info or {}
        pe     = info.get("trailingPE") or info.get("forwardPE")

        hist = ticker.history(period="30d")
        rsi  = None
        if not hist.empty and "Close" in hist.columns:
            rsi = _compute_rsi(hist["Close"])

        return {"pe": pe, "rsi": rsi}
    except Exception as e:
        log.debug("_quick_technicals(%s) failed: %s", yf_sym, e)
        return {}


# ── Sector PE lookup ──────────────────────────────────────────────────────────
def _sector_pe(sector: Optional[str]) -> float:
    """5-yr structural sector median from SECTOR_LONGRUN_PE. Falls back to 22×."""
    if not sector:
        return 22.0
    try:
        from agents.sector_valuation import SECTOR_LONGRUN_PE
        key = sector.lower()
        for k, v in SECTOR_LONGRUN_PE.items():
            if k in key or key in k:
                return float(v)
    except Exception:
        pass
    return 22.0


# ── Warren bot analysis (uses 24 h cache) ─────────────────────────────────────
def _warren_analyse(symbol: str) -> Optional[dict]:
    """Run warren_bot analysis. Returns None on error."""
    try:
        from agents.warren_bot import analyse as warren_analyse
        result = warren_analyse(symbol)
        if result.get("error"):
            log.debug("warren_bot(%s) returned error: %s", symbol, result["error"])
            return None
        return result
    except Exception as e:
        log.warning("warren_bot(%s) failed: %s", symbol, e)
        return None


# ── Steam detection ────────────────────────────────────────────────────────────
def _is_steam(
    yf_sym:       str,
    sector:       Optional[str],
    current_price: float,
    warren_result: Optional[dict],
) -> tuple[bool, str]:
    """
    Return (is_steam, reason).

    Steam = RSI > 72  AND  (PE > sector×1.8  OR  price > DCF×0.95)
    Both conditions must be true — RSI alone is not enough.
    """
    techs     = _quick_technicals(yf_sym)
    rsi       = techs.get("rsi")
    pe        = techs.get("pe")
    sec_pe    = _sector_pe(sector)
    intrinsic = (warren_result or {}).get("intrinsic_value")

    reasons = []

    if rsi is None:
        return False, "RSI unavailable — steam check skipped"

    if rsi <= _STEAM_RSI_THRESHOLD:
        return False, f"RSI {rsi:.1f} ≤ {_STEAM_RSI_THRESHOLD} — not overbought"

    # RSI is high — check valuation stretch
    valuation_stretched = False
    if pe and sec_pe and pe > sec_pe * _STEAM_PE_SECTOR_MULT:
        valuation_stretched = True
        reasons.append(f"PE {pe:.1f}× > {_STEAM_PE_SECTOR_MULT}× sector ({sec_pe:.1f}×)")

    if intrinsic and current_price and current_price > intrinsic * _STEAM_DCF_PROXIMITY:
        valuation_stretched = True
        reasons.append(f"price ₹{current_price:.0f} > {_STEAM_DCF_PROXIMITY*100:.0f}% of DCF ₹{intrinsic:.0f}")

    if valuation_stretched:
        reason = f"RSI {rsi:.1f} + " + " + ".join(reasons)
        return True, reason

    return False, f"RSI {rsi:.1f} high but valuation not stretched (PE={pe}, DCF={intrinsic})"


# ── Load OPEN holdings ─────────────────────────────────────────────────────────
def _load_open_holdings(client) -> list[dict]:
    try:
        resp = client.table("portfolio_holdings").select(
            "id,symbol,yf_symbol,sector,qty,avg_buy,current_price,"
            "target_price,stoploss_price,status,buy_date,created_at,"
            "original_target,target_updated_at,target_update_count,"
            "protect_gains_flag,stoploss_ratchet_level,last_review_at"
        ).eq("status", "OPEN").execute()
        return resp.data or []
    except Exception as e:
        log.error("_load_open_holdings failed: %s", e)
        return []


# ── Create portfolio alert ─────────────────────────────────────────────────────
def _create_alert(client, symbol: str, holding_id: str, severity: str,
                  alert_type: str, title: str, detail: str, dry_run: bool) -> None:
    if dry_run:
        log.info("[DRY RUN] Alert: [%s] %s — %s", severity, title, detail)
        return
    try:
        client.table("portfolio_alerts").insert({
            "severity":    severity,
            "alert_type":  alert_type,
            "title":       title,
            "detail":      detail,
            "resolved":    False,
            "portfolio_id": holding_id,
        }).execute()
    except Exception as e:
        log.warning("_create_alert(%s) failed: %s", symbol, e)


# ── Mechanism 1: Stoploss ratchet ─────────────────────────────────────────────
def _maybe_ratchet_stoploss(holding: dict, client, dry_run: bool) -> Optional[dict]:
    """
    Raise stoploss if gain threshold is crossed. Returns update dict or None.
    """
    avg_buy       = float(holding.get("avg_buy") or 0)
    current_price = float(holding.get("current_price") or avg_buy)
    stoploss      = float(holding.get("stoploss_price") or 0)
    ratchet_level = holding.get("stoploss_ratchet_level") or "ORIGINAL"
    symbol        = holding["symbol"]
    holding_id    = holding["id"]

    if avg_buy <= 0:
        return None

    gain_pct = (current_price - avg_buy) / avg_buy * 100

    new_sl    = None
    new_level = None
    msg       = None

    if gain_pct >= _RATCHET_LOCK_PCT and ratchet_level != "LOCK_20":
        # Lock in 20% gain — regardless of previous level
        candidate_sl = avg_buy * (1 + _LOCK_GAIN_FLOOR)
        if candidate_sl > stoploss:
            new_sl    = round(candidate_sl, 2)
            new_level = "LOCK_20"
            msg = (
                f"Stoploss ratcheted to LOCK_20 (+20% gain floor) for {symbol}. "
                f"Gain is +{gain_pct:.1f}% — SL raised from ₹{stoploss:.0f} → ₹{new_sl:.0f}. "
                f"Protects at least 20% profit even if the stock pulls back."
            )

    elif gain_pct >= _RATCHET_BREAKEVEN_PCT and ratchet_level == "ORIGINAL":
        # Move to breakeven
        candidate_sl = avg_buy
        if candidate_sl > stoploss:
            new_sl    = round(candidate_sl, 2)
            new_level = "BREAKEVEN"
            msg = (
                f"Stoploss ratcheted to BREAKEVEN for {symbol}. "
                f"Gain is +{gain_pct:.1f}% — SL raised from ₹{stoploss:.0f} → ₹{new_sl:.0f} (avg buy). "
                f"Position now protected against loss."
            )

    if new_sl is None or new_level is None:
        return None

    log.info(
        "[%s] Stoploss ratchet: %s → %s  SL ₹%.0f → ₹%.0f  gain=%.1f%%",
        symbol, ratchet_level, new_level, stoploss, new_sl, gain_pct,
    )

    if not dry_run:
        try:
            client.table("portfolio_holdings").update({
                "stoploss_price":        new_sl,
                "stoploss_ratchet_level": new_level,
            }).eq("id", holding_id).execute()
        except Exception as e:
            log.warning("[%s] Stoploss ratchet DB update failed: %s", symbol, e)
            return None

    _create_alert(
        client, symbol, holding_id,
        severity   = "info",
        alert_type = "STOPLOSS_RATCHET",
        title      = f"🔒 Stoploss raised to {new_level} — {symbol}",
        detail     = msg or "",
        dry_run    = dry_run,
    )

    return {
        "symbol":     symbol,
        "old_sl":     stoploss,
        "new_sl":     new_sl,
        "level":      new_level,
        "gain_pct":   gain_pct,
    }


# ── Mechanism 2: Target extension ─────────────────────────────────────────────
def _maybe_extend_target(holding: dict, client, dry_run: bool) -> Optional[dict]:
    """
    If stock is 80%+ of the way to its target, run warren_bot and potentially
    raise the target price. Returns update dict or None.
    """
    symbol        = holding["symbol"]
    yf_sym        = holding.get("yf_symbol") or f"{symbol}.NS"
    sector        = holding.get("sector") or ""
    avg_buy       = float(holding.get("avg_buy") or 0)
    current_price = float(holding.get("current_price") or avg_buy)
    target        = float(holding.get("target_price") or 0)
    orig_target   = float(holding.get("original_target") or 0) or target
    upd_count     = int(holding.get("target_update_count") or 0)
    holding_id    = holding["id"]

    if avg_buy <= 0 or target <= avg_buy or current_price <= 0:
        return None

    # How far along the original journey are we?
    progress = (current_price - avg_buy) / (target - avg_buy)
    if progress < _TARGET_REVIEW_PROGRESS:
        return None   # not close enough yet

    log.info("[%s] Target review triggered — progress=%.1f%% (current ₹%.0f, target ₹%.0f)",
             symbol, progress * 100, current_price, target)

    warren = _warren_analyse(symbol)
    if not warren:
        log.warning("[%s] Warren bot failed — skipping target extension", symbol)
        return None

    intrinsic = float(warren.get("intrinsic_value") or 0)
    if intrinsic <= 0:
        log.debug("[%s] Warren returned no intrinsic value — skipping", symbol)
        return None

    # ── Steam check ──────────────────────────────────────────────────────────
    steam, steam_reason = _is_steam(yf_sym, sector, current_price, warren)

    if steam:
        log.info("[%s] Steam detected (%s) — protecting gains, not extending target",
                 symbol, steam_reason)

        if not dry_run:
            try:
                client.table("portfolio_holdings").update({
                    "protect_gains_flag": True,
                    "target_updated_at":  datetime.utcnow().isoformat(),
                }).eq("id", holding_id).execute()
            except Exception as e:
                log.warning("[%s] protect_gains_flag update failed: %s", symbol, e)

        _create_alert(
            client, symbol, holding_id,
            severity   = "warning",
            alert_type = "PROTECT_GAINS",
            title      = f"🛡 Protect Gains — {symbol} valuation extended",
            detail     = (
                f"{symbol} is {progress*100:.0f}% of the way to its target but "
                f"steam detected: {steam_reason}. "
                f"Target NOT raised. Consider partial profit booking or tighten SL. "
                f"Warren DCF intrinsic: ₹{intrinsic:.0f} | Current: ₹{current_price:.0f}."
            ),
            dry_run = dry_run,
        )
        return {"symbol": symbol, "action": "PROTECT_GAINS", "reason": steam_reason}

    # ── No steam — extend if intrinsic is genuinely higher ───────────────────
    if intrinsic <= target:
        log.info("[%s] Intrinsic ₹%.0f ≤ current target ₹%.0f — no extension needed",
                 symbol, intrinsic, target)
        return None

    # Cap: extension ≤ target + 2× original entry→target range
    orig_range    = orig_target - avg_buy
    max_extension = target + _TARGET_EXT_CAP_X * orig_range
    new_target    = min(round(intrinsic, 2), round(max_extension, 2))

    if new_target <= target:
        log.info("[%s] Extension capped at ₹%.0f ≤ current target — no change", symbol, new_target)
        return None

    log.info(
        "[%s] Extending target ₹%.0f → ₹%.0f  (intrinsic=₹%.0f, cap=₹%.0f)",
        symbol, target, new_target, intrinsic, max_extension,
    )

    update_payload: dict = {
        "target_price":       new_target,
        "target_updated_at":  datetime.utcnow().isoformat(),
        "target_update_count": upd_count + 1,
        "protect_gains_flag": False,
    }
    # Record original target the first time only
    if not holding.get("original_target"):
        update_payload["original_target"] = target

    if not dry_run:
        try:
            client.table("portfolio_holdings").update(update_payload).eq("id", holding_id).execute()
        except Exception as e:
            log.warning("[%s] Target extension DB update failed: %s", symbol, e)
            return None

    _create_alert(
        client, symbol, holding_id,
        severity   = "info",
        alert_type = "TARGET_RAISED",
        title      = f"📈 Target raised #{upd_count + 1} — {symbol} ₹{target:.0f} → ₹{new_target:.0f}",
        detail     = (
            f"Warren DCF intrinsic value updated to ₹{intrinsic:.0f} "
            f"(MOS: {warren.get('margin_of_safety_pct', 0):.1f}%, "
            f"signal: {warren.get('signal', '?')}). "
            f"No steam detected — RSI and valuation support further upside. "
            f"Original target when you entered: ₹{orig_target:.0f}. "
            f"This is target revision #{upd_count + 1}."
        ),
        dry_run = dry_run,
    )

    return {
        "symbol":     symbol,
        "action":     "EXTENDED",
        "old_target": target,
        "new_target": new_target,
        "intrinsic":  intrinsic,
        "revision":   upd_count + 1,
    }


def _needs_target_review(holding: dict) -> bool:
    """True if holding qualifies for a target extension review."""
    avg_buy  = float(holding.get("avg_buy") or 0)
    current  = float(holding.get("current_price") or avg_buy)
    target   = float(holding.get("target_price") or 0)

    if avg_buy <= 0 or target <= avg_buy or current <= 0:
        return False
    if holding.get("protect_gains_flag"):
        # Already in protect mode — don't keep re-running until flag clears
        # Re-check at most every 14 days in case conditions change
        last = holding.get("target_updated_at")
        if last:
            try:
                last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
                if (datetime.utcnow().replace(tzinfo=last_dt.tzinfo) - last_dt).days < 14:
                    return False
            except Exception:
                pass

    progress = (current - avg_buy) / (target - avg_buy)
    return progress >= _TARGET_REVIEW_PROGRESS


# ── Mechanism 3: Laggard review ───────────────────────────────────────────────
def _is_laggard(holding: dict) -> bool:
    """True if holding has been held > 60 days and is > 10% below entry."""
    avg_buy  = float(holding.get("avg_buy") or 0)
    current  = float(holding.get("current_price") or avg_buy)
    if avg_buy <= 0:
        return False
    gain_pct = (current - avg_buy) / avg_buy * 100
    if gain_pct > _LAGGARD_LOSS_PCT:   # not down enough
        return False

    # Compute days held using buy_date → created_at → fallback 0
    days_held = 0
    for date_field in ("buy_date", "created_at"):
        raw = holding.get(date_field)
        if raw:
            try:
                if isinstance(raw, str):
                    # ISO date or datetime string
                    dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
                elif hasattr(raw, "date"):
                    dt = raw.date()
                else:
                    dt = date.fromisoformat(str(raw)[:10])
                days_held = (date.today() - dt).days
                break
            except Exception:
                pass

    return days_held >= _LAGGARD_DAYS


def _maybe_review_laggard(holding: dict, client, dry_run: bool) -> Optional[dict]:
    """
    Monthly review for struggling holdings. Creates a WARNING alert if
    warren_bot sees no path to recovery.
    """
    symbol    = holding["symbol"]
    holding_id = holding["id"]

    # 30-day cooldown
    last_review = holding.get("last_review_at")
    if last_review:
        try:
            last_dt = date.fromisoformat(str(last_review)[:10])
            if (date.today() - last_dt).days < _LAGGARD_COOLDOWN_DAYS:
                return None
        except Exception:
            pass

    log.info("[%s] Laggard review triggered", symbol)

    warren = _warren_analyse(symbol)
    intrinsic = float((warren or {}).get("intrinsic_value") or 0)
    signal    = (warren or {}).get("signal", "UNKNOWN")
    current   = float(holding.get("current_price") or holding.get("avg_buy") or 0)
    avg_buy   = float(holding.get("avg_buy") or 0)
    gain_pct  = (current - avg_buy) / avg_buy * 100 if avg_buy > 0 else 0

    needs_alert = (
        signal in ("AVOID", "SELL")
        or (intrinsic > 0 and current > 0 and intrinsic < current * _LAGGARD_UPSIDE_MIN)
        or warren is None   # couldn't analyse → flag for manual review
    )

    # Record review date
    if not dry_run:
        try:
            client.table("portfolio_holdings").update({
                "last_review_at": date.today().isoformat(),
            }).eq("id", holding_id).execute()
        except Exception as e:
            log.warning("[%s] last_review_at update failed: %s", symbol, e)

    if not needs_alert:
        log.info("[%s] Laggard review OK — intrinsic ₹%.0f, signal=%s", symbol, intrinsic, signal)
        return {"symbol": symbol, "action": "OK", "signal": signal}

    upside_from_current = (intrinsic / current - 1) * 100 if current > 0 and intrinsic > 0 else 0
    detail = (
        f"{symbol} has been down {abs(gain_pct):.1f}% from your entry for "
        f"{'more than 60 days' if intrinsic > 0 else 'an extended period'}. "
    )
    if warren is None:
        detail += "Fundamental data unavailable for fresh analysis — manual review recommended."
    else:
        detail += (
            f"Warren DCF signal: {signal} (score: {warren.get('score', '?')}/100). "
            f"Intrinsic value ₹{intrinsic:.0f} vs current ₹{current:.0f} "
            f"({'+' if upside_from_current >= 0 else ''}{upside_from_current:.1f}% upside from here). "
        )
        if signal in ("AVOID", "SELL"):
            detail += "Fundamental thesis may have weakened. Consider exiting or revisiting entry thesis."
        else:
            detail += "Upside from current level is thin. Review whether thesis is still valid."

    _create_alert(
        client, symbol, holding_id,
        severity   = "warning",
        alert_type = "THESIS_REVIEW",
        title      = f"⚠ Thesis Review — {symbol} down {abs(gain_pct):.1f}% for 60+ days",
        detail     = detail,
        dry_run    = dry_run,
    )

    return {
        "symbol":   symbol,
        "action":   "REVIEW_NEEDED",
        "gain_pct": gain_pct,
        "signal":   signal,
        "intrinsic": intrinsic,
    }


# ── Main entry point ──────────────────────────────────────────────────────────
def run_target_updates(dry_run: bool = False) -> dict:
    """
    Run all three mechanisms on every OPEN portfolio holding.

    Returns:
        dict with keys: stoploss_ratchets, targets_extended, protect_gains,
                         laggard_reviews, skipped, errors, total_holdings
    """
    client = _supabase()
    if not client:
        return {"error": "Supabase not configured", "total_holdings": 0}

    holdings = _load_open_holdings(client)
    log.info(
        "target_updater: loaded %d OPEN holdings%s",
        len(holdings),
        " [DRY RUN]" if dry_run else "",
    )

    results: dict = {
        "stoploss_ratchets": [],
        "targets_extended":  [],
        "protect_gains":     [],
        "laggard_reviews":   [],
        "skipped":           [],
        "errors":            [],
        "total_holdings":    len(holdings),
    }

    for holding in holdings:
        symbol = holding.get("symbol", "?")
        try:
            # ── 1. Stoploss ratchet (always run) ─────────────────────────────
            ratchet = _maybe_ratchet_stoploss(holding, client, dry_run)
            if ratchet:
                results["stoploss_ratchets"].append(ratchet)

            # ── 2. Target extension (when at ≥80% of journey) ────────────────
            if _needs_target_review(holding):
                ext = _maybe_extend_target(holding, client, dry_run)
                if ext:
                    if ext.get("action") == "EXTENDED":
                        results["targets_extended"].append(ext)
                    elif ext.get("action") == "PROTECT_GAINS":
                        results["protect_gains"].append(ext)
                else:
                    results["skipped"].append(f"{symbol}: target review ran, no change")
            else:
                results["skipped"].append(f"{symbol}: not at review threshold")

            # ── 3. Laggard review (>60d, >10% below entry, monthly) ──────────
            if _is_laggard(holding):
                review = _maybe_review_laggard(holding, client, dry_run)
                if review and review.get("action") != "OK":
                    results["laggard_reviews"].append(review)

        except Exception as exc:
            log.error("[%s] target_updater error: %s", symbol, exc, exc_info=True)
            results["errors"].append(f"{symbol}: {exc}")

    log.info(
        "target_updater done — ratchets=%d extended=%d protect=%d reviews=%d errors=%d",
        len(results["stoploss_ratchets"]),
        len(results["targets_extended"]),
        len(results["protect_gains"]),
        len(results["laggard_reviews"]),
        len(results["errors"]),
    )
    return results
