"""
agents/outcome_tracker.py — Recommendation Outcome Tracker
==========================================================
Daily job that resolves pending recommendation outcomes at 90, 180, and 365
day horizons. For each resolved rec, fetches the stock price and contemporaneous
NIFTY 50 return, computes absolute return and alpha, and upserts the result to
the recommendation_outcomes Supabase table.

Setup (run once in Supabase SQL Editor):
-----------------------------------------
  CREATE TABLE IF NOT EXISTS recommendation_outcomes (
      id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      rec_id              UUID REFERENCES recommendations(id) ON DELETE CASCADE,
      symbol              TEXT NOT NULL,
      action              TEXT NOT NULL,
      entry_price         NUMERIC,
      rec_date            DATE NOT NULL,
      price_t90           NUMERIC,
      nifty_t90           NUMERIC,
      alpha_t90           NUMERIC,
      outcome_t90         TEXT,
      price_t180          NUMERIC,
      nifty_t180          NUMERIC,
      alpha_t180          NUMERIC,
      outcome_t180        TEXT,
      price_t365          NUMERIC,
      nifty_t365          NUMERIC,
      alpha_t365          NUMERIC,
      outcome_t365        TEXT,
      nifty_entry         NUMERIC,
      composite_score     NUMERIC,
      agent_signals       JSONB,
      validation_kappa    NUMERIC,
      last_updated        TIMESTAMPTZ DEFAULT now()
  );
  CREATE INDEX ON recommendation_outcomes (symbol);
  CREATE INDEX ON recommendation_outcomes (rec_date);
  CREATE INDEX ON recommendation_outcomes (outcome_t90);

  CREATE OR REPLACE VIEW agent_accuracy AS
  SELECT
      action,
      COUNT(*) FILTER (WHERE outcome_t90 = 'HIT')  AS hits_t90,
      COUNT(*) FILTER (WHERE outcome_t90 IS NOT NULL AND outcome_t90 != 'PENDING') AS total_t90,
      ROUND(AVG(alpha_t90) * 100, 2)               AS avg_alpha_t90_pct,
      ROUND(AVG(alpha_t180) * 100, 2)              AS avg_alpha_t180_pct
  FROM recommendation_outcomes
  GROUP BY action;

  GRANT ALL ON recommendation_outcomes TO service_role;
  GRANT ALL ON agent_accuracy TO service_role;

Usage
-----
  python -m agents.outcome_tracker          # live run — upserts to DB
  python -m agents.outcome_tracker --dry    # print what would be updated, no DB writes
  python -m agents.outcome_tracker --report # print accuracy scorecard
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yfinance as yf

log = logging.getLogger(__name__)

# ── Project root on sys.path ──────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

HORIZONS         = [90, 180, 365]          # days to evaluate
WINDOW_DAYS      = 4                       # ±days around each horizon
NIFTY_SYMBOL     = "^NSEI"
PRICE_HISTORY    = "2y"                    # yfinance history period for price lookup

# Outcome classification thresholds
HIT_ALPHA_MIN    = 0.0    # alpha > 0 for BUY = HIT
MISS_ABS_MAX     = -0.10  # abs_return < -10% for BUY = MISS
SELL_HIT_MAX     = -0.05  # abs_return < -5% for SELL/AVOID = HIT (correct call)


# ─────────────────────────────────────────────────────────────────────────────
# Supabase helper
# ─────────────────────────────────────────────────────────────────────────────

def _supabase():
    """Return a live Supabase client or None."""
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
# Price helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_price_on_date(yf_symbol: str, target_date: date, window: int = WINDOW_DAYS) -> float | None:
    """
    Returns the closing price of `yf_symbol` on or near `target_date` (±window days).
    Returns None if no data is available in the window.
    """
    try:
        start = target_date - timedelta(days=window + 5)
        end   = target_date + timedelta(days=window + 5)
        hist  = yf.Ticker(yf_symbol).history(start=str(start), end=str(end), auto_adjust=True)
        if hist.empty:
            return None
        # Find closest available date
        hist.index = hist.index.normalize()   # strip time component
        target_dt  = datetime.combine(target_date, datetime.min.time())
        # Filter to window
        mask   = (hist.index >= str(start)) & (hist.index <= str(end))
        subset = hist.loc[mask]
        if subset.empty:
            return None
        # Pick the row closest to target_date
        import pandas as pd
        subset = subset.copy()
        subset["date_obj"] = pd.to_datetime(subset.index)
        subset["delta"]    = (subset["date_obj"] - pd.Timestamp(target_date)).abs()
        closest = subset.sort_values("delta").iloc[0]
        if abs((pd.Timestamp(closest["date_obj"]) - pd.Timestamp(target_date)).days) <= window:
            return float(closest["Close"])
        return None
    except Exception as exc:
        log.debug("Price fetch failed for %s on %s: %s", yf_symbol, target_date, exc)
        return None


def _resolve_yf_symbol(symbol: str) -> str:
    """Convert plain NSE symbol to yfinance format."""
    if "." in symbol or symbol.startswith("^"):
        return symbol
    return f"{symbol}.NS"


# ─────────────────────────────────────────────────────────────────────────────
# Outcome classification
# ─────────────────────────────────────────────────────────────────────────────

def _classify_outcome(action: str, abs_return: float, alpha: float) -> str:
    """
    HIT / MISS / PARTIAL based on action type and return/alpha.

    BUY / HOLD:
      HIT     = alpha > 0 AND abs_return > 0
      MISS    = abs_return < -10%
      PARTIAL = everything else

    SELL / AVOID:
      HIT     = abs_return < -5%  (the call was correct — stock fell)
      MISS    = abs_return > +10% (stock rose despite SELL signal)
      PARTIAL = everything else
    """
    if action in ("BUY", "HOLD"):
        if alpha > HIT_ALPHA_MIN and abs_return > 0:
            return "HIT"
        elif abs_return < MISS_ABS_MAX:
            return "MISS"
        return "PARTIAL"
    elif action in ("SELL", "AVOID"):
        if abs_return < SELL_HIT_MAX:
            return "HIT"
        elif abs_return > 0.10:
            return "MISS"
        return "PARTIAL"
    return "PARTIAL"


# ─────────────────────────────────────────────────────────────────────────────
# PENDING row seed (called from orchestrator save_recs_node)
# ─────────────────────────────────────────────────────────────────────────────

def seed_pending_outcome(
    client,
    rec_id:          str,
    symbol:          str,
    action:          str,
    entry_price:     float | None,
    rec_date:        date,
    composite_score: float | None = None,
    agent_signals:   dict  | None = None,
    validation_kappa: float | None = None,
) -> bool:
    """
    Insert a PENDING row into recommendation_outcomes when a new recommendation
    is saved. Called from orchestrator save_recs_node (non-blocking).

    Also fetches the NIFTY 50 entry price on rec_date so that future alpha
    computations have the benchmark starting point.
    """
    try:
        nifty_entry = _fetch_price_on_date(NIFTY_SYMBOL, rec_date, window=3)
        row = {
            "rec_id":           rec_id,
            "symbol":           symbol,
            "action":           action,
            "entry_price":      entry_price,
            "rec_date":         str(rec_date),
            "nifty_entry":      nifty_entry,
            "composite_score":  composite_score,
            "agent_signals":    agent_signals or {},
            "validation_kappa": validation_kappa,
            "outcome_t90":      "PENDING",
            "outcome_t180":     "PENDING",
            "outcome_t365":     "PENDING",
        }
        client.table("recommendation_outcomes").insert(row).execute()
        log.info("[%s] seeded PENDING outcome row (rec_id=%s)", symbol, rec_id)
        return True
    except Exception as exc:
        log.warning("[%s] Failed to seed pending outcome: %s", symbol, exc)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Main tracking job
# ─────────────────────────────────────────────────────────────────────────────

def run_outcome_tracking(dry_run: bool = False) -> dict:
    """
    Entry point called by worker.py at 18:30 IST.

    For each recommendation_outcomes row with PENDING horizons, check if the
    horizon date (rec_date + N days) is now past. If so:
      1. Fetch current / historical price for the stock
      2. Fetch NIFTY 50 price on the horizon date
      3. Compute abs_return, nifty_return, alpha
      4. Classify outcome (HIT/MISS/PARTIAL)
      5. Upsert the row with filled-in fields

    Returns:
      { tracked: int, updated: int, hits: int, misses: int,
        avg_alpha_90d: float | None, errors: list[str] }
    """
    log.info("=== Outcome Tracker run started (dry_run=%s) ===", dry_run)
    today      = date.today()
    client     = None if dry_run else _supabase()
    errors: list[str] = []

    # ── Fetch all PENDING outcomes ────────────────────────────────────────────
    try:
        if dry_run or not client:
            # In dry-run mode, try to fetch from Supabase for display but don't write
            preview_client = _supabase()
            if preview_client:
                rows = (
                    preview_client
                    .table("recommendation_outcomes")
                    .select("*")
                    .execute()
                    .data or []
                )
            else:
                rows = []
        else:
            rows = (
                client
                .table("recommendation_outcomes")
                .select("*")
                .execute()
                .data or []
            )
    except Exception as exc:
        log.error("Failed to fetch recommendation_outcomes: %s", exc)
        return {"tracked": 0, "updated": 0, "hits": 0, "misses": 0, "avg_alpha_90d": None, "errors": [str(exc)]}

    if not rows:
        log.info("No outcome rows found — nothing to evaluate")
        return {"tracked": 0, "updated": 0, "hits": 0, "misses": 0, "avg_alpha_90d": None, "errors": []}

    log.info("Found %d outcome rows to evaluate", len(rows))

    stats = {"tracked": 0, "updated": 0, "hits": 0, "misses": 0, "alpha_90d_values": []}

    for row in rows:
        try:
            rec_date_str = row.get("rec_date")
            if not rec_date_str:
                continue

            rec_date     = date.fromisoformat(str(rec_date_str)[:10])
            symbol       = row["symbol"]
            action       = row.get("action", "BUY")
            entry_price  = row.get("entry_price")
            nifty_entry  = row.get("nifty_entry")
            row_id       = row["id"]
            yf_sym       = _resolve_yf_symbol(symbol)

            if not entry_price or not nifty_entry:
                log.debug("[%s] Skipping — missing entry_price or nifty_entry", symbol)
                continue

            entry_price = float(entry_price)
            nifty_entry = float(nifty_entry)

            updates: dict = {}
            row_updated   = False

            for horizon in HORIZONS:
                outcome_key = f"outcome_t{horizon}"
                price_key   = f"price_t{horizon}"
                nifty_key   = f"nifty_t{horizon}"
                alpha_key   = f"alpha_t{horizon}"

                current_outcome = row.get(outcome_key)
                # Only resolve if still PENDING
                if current_outcome not in (None, "PENDING"):
                    continue

                horizon_date = rec_date + timedelta(days=horizon)
                # Only evaluate once horizon is past (with a small buffer)
                if horizon_date > today + timedelta(days=2):
                    log.debug("[%s] horizon t%d not yet reached (%s)", symbol, horizon, horizon_date)
                    continue

                stats["tracked"] += 1

                price_on_date = _fetch_price_on_date(yf_sym, horizon_date)
                nifty_on_date = _fetch_price_on_date(NIFTY_SYMBOL, horizon_date)

                if price_on_date is None or nifty_on_date is None:
                    log.warning("[%s] t%d price data unavailable (stock=%s, nifty=%s)",
                                symbol, horizon, price_on_date, nifty_on_date)
                    errors.append(f"{symbol} t{horizon}: price data unavailable")
                    continue

                abs_return  = (price_on_date / entry_price) - 1.0
                nifty_ret   = (nifty_on_date / nifty_entry) - 1.0
                alpha       = abs_return - nifty_ret
                outcome     = _classify_outcome(action, abs_return, alpha)

                updates[price_key]   = round(price_on_date, 4)
                updates[nifty_key]   = round(nifty_on_date, 4)
                updates[alpha_key]   = round(alpha, 6)
                updates[outcome_key] = outcome
                row_updated = True

                log.info(
                    "[%s] t%d: price=%.2f entry=%.2f abs_ret=%.1f%% alpha=%.1f%% → %s",
                    symbol, horizon, price_on_date, entry_price,
                    abs_return * 100, alpha * 100, outcome
                )

                if horizon == 90:
                    stats["alpha_90d_values"].append(alpha)
                    if outcome == "HIT":
                        stats["hits"] += 1
                    elif outcome == "MISS":
                        stats["misses"] += 1

            if row_updated:
                updates["last_updated"] = datetime.now(timezone.utc).isoformat()
                if dry_run:
                    log.info("[DRY RUN] Would update %s: %s", symbol, updates)
                else:
                    try:
                        client.table("recommendation_outcomes").update(updates).eq("id", row_id).execute()
                        stats["updated"] += 1
                        log.info("[%s] outcome row updated (id=%s)", symbol, row_id)
                    except Exception as exc:
                        log.error("[%s] update failed: %s", symbol, exc)
                        errors.append(f"{symbol}: update failed — {exc}")

        except Exception as exc:
            log.error("Error processing outcome row %s: %s", row.get("id"), exc)
            errors.append(f"row {row.get('id')}: {exc}")

    alpha_values = stats.pop("alpha_90d_values", [])
    avg_alpha_90d = (sum(alpha_values) / len(alpha_values)) if alpha_values else None

    result = {
        "tracked":       stats["tracked"],
        "updated":       stats["updated"],
        "hits":          stats["hits"],
        "misses":        stats["misses"],
        "avg_alpha_90d": round(avg_alpha_90d, 4) if avg_alpha_90d is not None else None,
        "errors":        errors,
    }
    log.info(
        "=== Outcome Tracker done: tracked=%d updated=%d hits=%d misses=%d avg_alpha_90d=%s ===",
        result["tracked"], result["updated"], result["hits"], result["misses"],
        f"{result['avg_alpha_90d']:.2%}" if result["avg_alpha_90d"] is not None else "N/A"
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# P5-D: Forward outcome poller — live price snapshot + t+30 milestone
# ─────────────────────────────────────────────────────────────────────────────

def _batch_current_prices(yf_symbols: list[str]) -> dict[str, float]:
    """
    Fetch today's (or most-recent) closing price for a list of yfinance symbols
    in a single batch download. Returns {symbol: price}.
    Uses the last available close to handle weekends / market-closed days.
    """
    if not yf_symbols:
        return {}
    try:
        import pandas as pd
        df = yf.download(yf_symbols, period="5d", interval="1d", auto_adjust=True, progress=False)
        if df.empty:
            return {}
        close = df["Close"] if "Close" in df.columns else df.xs("Close", axis=1, level=0)
        result: dict[str, float] = {}
        for sym in yf_symbols:
            try:
                series = close[sym].dropna() if sym in close.columns else pd.Series(dtype=float)
                if not series.empty:
                    result[sym] = float(series.iloc[-1])
            except Exception:
                pass
        return result
    except Exception as exc:
        log.warning("Batch price fetch failed: %s — falling back to per-symbol", exc)
        return {}


def run_forward_polling(dry_run: bool = False) -> dict:
    """
    P5-D: Daily job at 16:30 IST — runs after market close.

    For every recommendation_outcomes row where outcome_t90 is still PENDING:
      1. Batch-fetch current prices for all symbols + NIFTY in one yfinance call
      2. Compute live abs_return, nifty_return, alpha
      3. Update price_live / nifty_live / alpha_live / return_live / days_live / live_updated_at
      4. If rec_date + 30 days is past → also resolve the t+30 milestone
         (price_t30, nifty_t30, alpha_t30, outcome_t30)

    Returns:
      { polled: int, live_updated: int, t30_resolved: int, errors: list[str] }
    """
    log.info("=== Forward Poller run started (dry_run=%s) ===", dry_run)
    today   = date.today()
    client  = None if dry_run else _supabase()
    errors: list[str] = []

    # ── 1. Fetch all PENDING rows ─────────────────────────────────────────────
    try:
        fetch_client = _supabase() if dry_run else client
        if not fetch_client:
            log.warning("Supabase not configured — forward poller skipped")
            return {"polled": 0, "live_updated": 0, "t30_resolved": 0, "errors": ["Supabase not configured"]}

        rows = (
            fetch_client
            .table("recommendation_outcomes")
            .select("id,symbol,action,entry_price,nifty_entry,rec_date,outcome_t90,outcome_t30,price_t30")
            .in_("outcome_t90", ["PENDING", None])
            .execute()
            .data or []
        )
    except Exception as exc:
        log.error("Forward poller — failed to fetch rows: %s", exc)
        return {"polled": 0, "live_updated": 0, "t30_resolved": 0, "errors": [str(exc)]}

    if not rows:
        log.info("No PENDING rows — nothing to poll")
        return {"polled": 0, "live_updated": 0, "t30_resolved": 0, "errors": []}

    log.info("Forward poller: %d PENDING rows to refresh", len(rows))

    # ── 2. Batch-fetch current prices ────────────────────────────────────────
    yf_symbols_set = {_resolve_yf_symbol(r["symbol"]) for r in rows} | {NIFTY_SYMBOL}
    yf_symbols     = list(yf_symbols_set)

    batch_prices = _batch_current_prices(yf_symbols)
    nifty_now    = batch_prices.get(NIFTY_SYMBOL)

    if not nifty_now:
        log.warning("Could not fetch NIFTY current price — aborting forward poller")
        return {"polled": 0, "live_updated": 0, "t30_resolved": 0, "errors": ["NIFTY price unavailable"]}

    log.info("Batch prices fetched: %d symbols, NIFTY=%.2f", len(batch_prices), nifty_now)

    stats = {"polled": 0, "live_updated": 0, "t30_resolved": 0}

    for row in rows:
        try:
            rec_date_str = row.get("rec_date")
            if not rec_date_str:
                continue

            rec_date    = date.fromisoformat(str(rec_date_str)[:10])
            symbol      = row["symbol"]
            action      = row.get("action", "BUY")
            entry_price = row.get("entry_price")
            nifty_entry = row.get("nifty_entry")
            row_id      = row["id"]
            yf_sym      = _resolve_yf_symbol(symbol)

            if not entry_price or not nifty_entry:
                log.debug("[%s] skipping — missing entry_price or nifty_entry", symbol)
                continue

            entry_price = float(entry_price)
            nifty_entry = float(nifty_entry)
            stats["polled"] += 1

            current_price = batch_prices.get(yf_sym)
            if not current_price:
                log.debug("[%s] no current price in batch — skipping", symbol)
                errors.append(f"{symbol}: no current price")
                continue

            days_elapsed  = (today - rec_date).days
            abs_return    = (current_price / entry_price) - 1.0
            nifty_ret_now = (nifty_now / nifty_entry) - 1.0
            alpha_now     = abs_return - nifty_ret_now

            updates: dict = {
                "price_live":      round(current_price, 4),
                "nifty_live":      round(nifty_now, 4),
                "alpha_live":      round(alpha_now, 6),
                "return_live":     round(abs_return, 6),
                "days_live":       days_elapsed,
                "live_updated_at": datetime.now(timezone.utc).isoformat(),
            }

            # ── t+30 milestone ────────────────────────────────────────────────
            t30_date = rec_date + timedelta(days=30)
            already_resolved_t30 = row.get("outcome_t30") not in (None, "PENDING")

            if not already_resolved_t30 and t30_date <= today + timedelta(days=2):
                price_t30   = _fetch_price_on_date(yf_sym, t30_date)
                nifty_t30   = _fetch_price_on_date(NIFTY_SYMBOL, t30_date)
                if price_t30 and nifty_t30:
                    ret_t30   = (price_t30 / entry_price) - 1.0
                    nifty_t30_ret = (nifty_t30 / nifty_entry) - 1.0
                    alpha_t30 = ret_t30 - nifty_t30_ret
                    outcome_t30 = _classify_outcome(action, ret_t30, alpha_t30)
                    updates["price_t30"]   = round(price_t30, 4)
                    updates["nifty_t30"]   = round(nifty_t30, 4)
                    updates["alpha_t30"]   = round(alpha_t30, 6)
                    updates["outcome_t30"] = outcome_t30
                    stats["t30_resolved"] += 1
                    log.info(
                        "[%s] t30 resolved: price=%.2f ret=%.1f%% alpha=%.1f%% → %s",
                        symbol, price_t30, ret_t30 * 100, alpha_t30 * 100, outcome_t30
                    )

            log.debug(
                "[%s] live: days=%d price=%.2f ret=%+.1f%% alpha=%+.1f%%",
                symbol, days_elapsed, current_price, abs_return * 100, alpha_now * 100
            )

            if dry_run:
                log.info("[DRY RUN] Would update %s: ret=%+.1f%% alpha=%+.1f%%",
                         symbol, abs_return * 100, alpha_now * 100)
            else:
                try:
                    client.table("recommendation_outcomes").update(updates).eq("id", row_id).execute()
                    stats["live_updated"] += 1
                except Exception as exc:
                    log.error("[%s] live update failed: %s", symbol, exc)
                    errors.append(f"{symbol}: update failed — {exc}")

        except Exception as exc:
            log.error("Error in forward poller for row %s: %s", row.get("id"), exc)
            errors.append(f"row {row.get('id')}: {exc}")

    log.info(
        "=== Forward Poller done: polled=%d live_updated=%d t30_resolved=%d errors=%d ===",
        stats["polled"], stats["live_updated"], stats["t30_resolved"], len(errors)
    )
    return {**stats, "errors": errors}


# ─────────────────────────────────────────────────────────────────────────────
# P5-A: Per-agent attribution analysis
# ─────────────────────────────────────────────────────────────────────────────

def compute_agent_attribution(rows: list[dict]) -> list[dict]:
    """
    Unpack agent_signals JSONB from resolved recommendation_outcome rows and
    compute per-agent accuracy metrics.

    Each row's agent_signals looks like:
      { "technical": {"signal": "BULLISH", "score": 72, "weight": 0.25}, ... }

    Returns a list of dicts sorted by hit_rate_90d desc:
      [ { agent_name, signal_count, bullish_count, hit_rate_90d,
          avg_alpha_90d, avg_score_bullish, contribution_score }, ... ]
    """
    from collections import defaultdict

    # Only use rows where we have a resolved 90d outcome and agent_signals
    resolved = [
        r for r in rows
        if r.get("outcome_t90") not in (None, "PENDING")
        and r.get("agent_signals")
        and isinstance(r["agent_signals"], dict)
    ]

    if not resolved:
        return []

    # Accumulate per-agent stats
    agent_stats: dict[str, dict] = defaultdict(lambda: {
        "signals": [],          # list of (was_bullish, outcome, alpha)
        "scores":  [],          # score when bullish
    })

    for row in resolved:
        outcome_90 = row.get("outcome_t90")
        alpha_90   = row.get("alpha_t90")
        signals    = row["agent_signals"]

        for agent_name, sig_data in signals.items():
            if not isinstance(sig_data, dict):
                continue
            signal = (sig_data.get("signal") or "").upper()
            score  = sig_data.get("score")

            # Treat BULLISH / STRONG_BUY / BUY as "bullish"
            is_bullish = signal in ("BULLISH", "STRONG_BUY", "BUY", "STRONG_BULLISH")

            agent_stats[agent_name]["signals"].append({
                "bullish":   is_bullish,
                "outcome":   outcome_90,
                "alpha":     alpha_90,
                "score":     score,
            })
            if is_bullish and score is not None:
                try:
                    agent_stats[agent_name]["scores"].append(float(score))
                except (TypeError, ValueError):
                    pass

    results: list[dict] = []
    for agent_name, data in agent_stats.items():
        sigs        = data["signals"]
        bullish     = [s for s in sigs if s["bullish"]]
        hits_when_bullish = sum(
            1 for s in bullish if s["outcome"] == "HIT"
        )
        alpha_vals  = [
            float(s["alpha"]) * 100
            for s in bullish
            if s.get("alpha") is not None
        ]
        avg_scores  = data["scores"]

        hit_rate    = round(hits_when_bullish / len(bullish) * 100, 1) if bullish else None
        avg_alpha   = round(sum(alpha_vals) / len(alpha_vals), 2) if alpha_vals else None
        avg_score   = round(sum(avg_scores) / len(avg_scores), 1) if avg_scores else None

        # Contribution score: agents that vote bullish AND are right most often rank highest
        # Penalise low signal frequency (< 5 signals = less reliable)
        freq_weight = min(1.0, len(bullish) / 20)
        contribution = (
            (hit_rate or 0) * freq_weight
            + (avg_alpha or 0) * 2
        ) if hit_rate is not None else 0.0

        results.append({
            "agent_name":         agent_name,
            "signal_count":       len(sigs),
            "bullish_count":      len(bullish),
            "hit_rate_90d":       hit_rate,
            "avg_alpha_90d":      avg_alpha,
            "avg_score_bullish":  avg_score,
            "contribution_score": round(contribution, 2),
        })

    return sorted(results, key=lambda x: x["contribution_score"], reverse=True)


def run_attribution_analysis() -> list[dict]:
    """
    Fetch all resolved recommendation_outcome rows and return per-agent
    attribution stats. Entry point for the API endpoint.
    """
    client = _supabase()
    if not client:
        return []
    try:
        rows = (
            client
            .table("recommendation_outcomes")
            .select("outcome_t90,alpha_t90,agent_signals")
            .not_.is_("outcome_t90", "null")
            .execute()
            .data or []
        )
    except Exception as exc:
        log.error("Attribution analysis fetch failed: %s", exc)
        return []
    return compute_agent_attribution(rows)


def run_live_attribution() -> list[dict]:
    """
    P5-E: Per-agent attribution using LIVE alpha (alpha_live) instead of waiting
    for 90-day resolution. Returns same schema as compute_agent_attribution() but
    sourced from price_live / alpha_live columns populated by run_forward_polling().

    This makes attribution immediately useful before any rec reaches 90 days.
    """
    client = _supabase()
    if not client:
        return []
    try:
        rows = (
            client
            .table("recommendation_outcomes")
            .select("outcome_t90,alpha_live,return_live,days_live,agent_signals,action")
            .not_.is_("alpha_live", "null")
            .execute()
            .data or []
        )
    except Exception as exc:
        log.error("Live attribution fetch failed: %s", exc)
        return []

    if not rows:
        return []

    # Build per-agent stats from live alpha (substitute outcome_t90/alpha_t90 with live values)
    # Positive live alpha = "bullish call is working so far"
    from collections import defaultdict
    agent_data: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        signals = row.get("agent_signals") or {}
        if isinstance(signals, str):
            import json
            try:
                signals = json.loads(signals)
            except Exception:
                signals = {}
        alpha_live  = row.get("alpha_live")
        return_live = row.get("return_live")
        days_live   = row.get("days_live", 0)
        if alpha_live is None:
            continue
        for agent_name, sig_data in signals.items():
            if not isinstance(sig_data, dict):
                continue
            agent_data[agent_name].append({
                "signal":       sig_data.get("signal", "NEUTRAL"),
                "score":        sig_data.get("score"),
                "alpha_live":   alpha_live,
                "return_live":  return_live,
                "days_live":    days_live,
                "action":       row.get("action", ""),
            })

    results = []
    for agent_name, sigs in agent_data.items():
        bullish = [s for s in sigs if s["signal"] in ("BULLISH", "POSITIVE", "BUY")]
        alpha_vals = [s["alpha_live"] for s in sigs if s["alpha_live"] is not None]
        bull_alpha = [s["alpha_live"] for s in bullish if s["alpha_live"] is not None]

        avg_alpha_live  = round(sum(alpha_vals)  / len(alpha_vals),  4) if alpha_vals  else None
        avg_bull_alpha  = round(sum(bull_alpha)   / len(bull_alpha),  4) if bull_alpha  else None
        positive_bull   = sum(1 for a in bull_alpha if a > 0)
        positive_rate   = (positive_bull / len(bull_alpha) * 100) if bull_alpha else None
        avg_days        = round(sum(s["days_live"] for s in sigs) / len(sigs)) if sigs else 0

        results.append({
            "agent_name":          agent_name,
            "signal_count":        len(sigs),
            "bullish_count":       len(bullish),
            "avg_alpha_live":      avg_alpha_live,
            "avg_bull_alpha_live": avg_bull_alpha,
            "positive_rate_live":  round(positive_rate, 1) if positive_rate is not None else None,
            "avg_days_held":       avg_days,
            # Keep field names compatible with resolved-outcome attribution schema
            "hit_rate_90d":        None,
            "avg_alpha_90d":       None,
        })

    return sorted(results, key=lambda x: (x["avg_bull_alpha_live"] or -999), reverse=True)


def get_live_performance_summary() -> dict:
    """
    P5-E: Portfolio-level live performance snapshot for all open (PENDING) recs.
    Returns aggregate stats + per-rec list for the /api/performance/live endpoint.
    """
    client = _supabase()
    if not client:
        return {}
    try:
        rows = (
            client
            .table("recommendation_outcomes")
            .select(
                "id,symbol,action,entry_price,rec_date,composite_score,"
                "price_live,nifty_live,alpha_live,return_live,days_live,live_updated_at,"
                "outcome_t30,alpha_t30,"
                "outcome_t90,alpha_t90"
            )
            .execute()
            .data or []
        )
    except Exception as exc:
        log.error("Live performance fetch failed: %s", exc)
        return {}

    open_rows    = [r for r in rows if r.get("outcome_t90") in ("PENDING", None)]
    resolved_90d = [r for r in rows if r.get("outcome_t90") not in ("PENDING", None, "")]

    # ── Aggregate live stats (open recs only) ─────────────────────────────────
    live_alphas   = [r["alpha_live"]  for r in open_rows if r.get("alpha_live")  is not None]
    live_returns  = [r["return_live"] for r in open_rows if r.get("return_live") is not None]

    by_action: dict[str, dict] = {}
    for action in ("BUY", "HOLD", "SELL", "AVOID"):
        grp = [r for r in open_rows if r.get("action") == action]
        if not grp:
            continue
        alphas  = [r["alpha_live"]  for r in grp if r.get("alpha_live")  is not None]
        returns = [r["return_live"] for r in grp if r.get("return_live") is not None]
        by_action[action] = {
            "count":          len(grp),
            "avg_alpha_pct":  round(sum(alphas)  / len(alphas)  * 100, 2) if alphas  else None,
            "avg_return_pct": round(sum(returns) / len(returns) * 100, 2) if returns else None,
            "positive_count": sum(1 for a in alphas if a > 0),
            "has_live_data":  len(alphas) > 0,
        }

    # ── Sort open recs by live alpha desc ─────────────────────────────────────
    def _sort_key(r):
        return r.get("alpha_live") if r.get("alpha_live") is not None else -999

    sorted_open = sorted(open_rows, key=_sort_key, reverse=True)

    return {
        "total_open":          len(open_rows),
        "total_resolved":      len(resolved_90d),
        "avg_live_return_pct": round(sum(live_returns) / len(live_returns) * 100, 2) if live_returns else None,
        "avg_live_alpha_pct":  round(sum(live_alphas)  / len(live_alphas)  * 100, 2) if live_alphas  else None,
        "positive_count":      sum(1 for a in live_alphas if a > 0),
        "has_live_data":       len(live_alphas) > 0,
        "by_action":           by_action,
        "recs":                sorted_open,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Accuracy report helper
# ─────────────────────────────────────────────────────────────────────────────

def _print_accuracy_report() -> None:
    """Print a formatted accuracy scorecard to stdout."""
    client = _supabase()
    if not client:
        print("Supabase not configured — set SUPABASE_URL and SUPABASE_SERVICE_KEY")
        return

    try:
        rows = (
            client
            .table("recommendation_outcomes")
            .select("action,outcome_t90,outcome_t180,alpha_t90,alpha_t180,alpha_t365")
            .execute()
            .data or []
        )
    except Exception as exc:
        print(f"Failed to fetch outcomes: {exc}")
        return

    if not rows:
        print("No outcome data yet. Run the outcome tracker after recommendations are 90+ days old.")
        return

    # Group by action
    from collections import defaultdict
    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        groups[r.get("action", "UNKNOWN")].append(r)

    print("\n" + "=" * 60)
    print("  BHARAT INTELLIGENCE — ACCURACY SCORECARD")
    print("=" * 60)

    total_recs = len(rows)
    print(f"\n  Total tracked recommendations: {total_recs}")
    print()

    for action in ["BUY", "HOLD", "SELL", "AVOID"]:
        grp = groups.get(action, [])
        if not grp:
            continue

        resolved_90  = [r for r in grp if r.get("outcome_t90") not in (None, "PENDING")]
        hits_90      = sum(1 for r in resolved_90 if r.get("outcome_t90") == "HIT")
        misses_90    = sum(1 for r in resolved_90 if r.get("outcome_t90") == "MISS")

        resolved_180 = [r for r in grp if r.get("outcome_t180") not in (None, "PENDING")]
        hits_180     = sum(1 for r in resolved_180 if r.get("outcome_t180") == "HIT")

        alpha_90_vals  = [r["alpha_t90"]  for r in resolved_90  if r.get("alpha_t90")  is not None]
        alpha_180_vals = [r["alpha_t180"] for r in resolved_180 if r.get("alpha_t180") is not None]

        hit_rate_90  = (hits_90  / len(resolved_90))  * 100 if resolved_90  else 0
        hit_rate_180 = (hits_180 / len(resolved_180)) * 100 if resolved_180 else 0
        avg_alpha_90  = (sum(alpha_90_vals)  / len(alpha_90_vals))  * 100 if alpha_90_vals  else None
        avg_alpha_180 = (sum(alpha_180_vals) / len(alpha_180_vals)) * 100 if alpha_180_vals else None

        print(f"  {action}")
        print(f"    Total recs          : {len(grp)}")
        print(f"    Resolved at 90d     : {len(resolved_90)}")
        if resolved_90:
            print(f"    Hit rate (90d)      : {hit_rate_90:.1f}%  ({hits_90} HIT, {misses_90} MISS)")
        if avg_alpha_90 is not None:
            print(f"    Avg alpha (90d)     : {avg_alpha_90:+.2f}% vs NIFTY 50")
        if resolved_180:
            print(f"    Hit rate (180d)     : {hit_rate_180:.1f}%  ({hits_180} HIT)")
        if avg_alpha_180 is not None:
            print(f"    Avg alpha (180d)    : {avg_alpha_180:+.2f}% vs NIFTY 50")
        print()

    print("  Target: BUY hit rate >55%, avg alpha >+3% at 90 days")
    print("=" * 60)


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import logging as _logging
    from dotenv import load_dotenv
    load_dotenv()

    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    parser = argparse.ArgumentParser(description="Bharat Intelligence — Outcome Tracker")
    parser.add_argument("--dry",          action="store_true", help="Dry run — no DB writes")
    parser.add_argument("--report",       action="store_true", help="Print accuracy scorecard and exit")
    parser.add_argument("--forward-poll", action="store_true", help="Run P5-D forward poller (live snapshot + t30)")
    parser.add_argument("--live-report",  action="store_true", help="Print live performance summary and exit")
    args = parser.parse_args()

    if args.report:
        _print_accuracy_report()
        sys.exit(0)

    if args.live_report:
        summary = get_live_performance_summary()
        print(f"\nLive Performance Summary:")
        print(f"  Open recs:     {summary.get('total_open', 0)}")
        print(f"  Avg return:    {summary.get('avg_live_return_pct', 'N/A')}%")
        print(f"  Avg alpha:     {summary.get('avg_live_alpha_pct', 'N/A')}%")
        print(f"  Beating NIFTY: {summary.get('positive_count', 0)}/{summary.get('total_open', 0)}")
        print()
        for r in (summary.get("recs") or []):
            ret = (r.get("return_live") or 0) * 100
            alp = (r.get("alpha_live")  or 0) * 100
            print(f"  {r['symbol']:15s} {r.get('action','?'):5s}  ret={ret:+.1f}%  alpha={alp:+.1f}%  days={r.get('days_live','?')}")
        sys.exit(0)

    if args.forward_poll:
        result = run_forward_polling(dry_run=args.dry)
        print(f"\nForward Poller Result: {result}")
        sys.exit(0)

    result = run_outcome_tracking(dry_run=args.dry)
    print(f"\nResult: {result}")
