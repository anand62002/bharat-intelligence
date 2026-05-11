"""
agents/backtester.py — Walk-Forward Historical Backtest Framework
=================================================================
Measures historical performance of the system's technical signal logic on
the NIFTY 500 quality universe over 2020–2024, using pure yfinance OHLCV.

Methodology
-----------
1. Universe     : NIFTY 500 constituents (NSE CSV), filtered by market cap > ₹500 Cr
2. Signal logic : BUY  = RSI(14) in 40–65 AND price > EMA(200) AND MACD bullish crossover
                  EXIT = RSI(14) > 75  OR  price < entry × 0.85 (15% SL)  OR  90 days elapsed
3. Performance  : abs_return_90d and alpha vs NIFTY 50 (^NSEI) at 90d and 180d
4. Walk-forward : TRAIN 2020–2022 (in-sample) | TEST 2023–2024 (out-of-sample)
5. Output       : Saved to `backtest_results` Supabase table; returned as summary dict

DB migration (run once in Supabase SQL Editor before first run):
    db/migrations/create_backtest_results.sql

CLI
---
    python -m agents.backtester
    python -m agents.backtester --start 2021-01-01 --end 2024-12-31
    python -m agents.backtester --max-symbols 30 --dry-run
    python -m agents.backtester --dry-run --verbose
"""
from __future__ import annotations

import argparse
import logging
import math
import os
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
_DEFAULT_START   = "2020-01-01"
_DEFAULT_END     = "2024-12-31"
_TRAIN_CUTOFF    = "2023-01-01"    # TRAIN: 2020–2022 | TEST: 2023–2024

# Indicator params
_RSI_PERIOD      = 14
_EMA_PERIOD      = 200
_MACD_FAST       = 12
_MACD_SLOW       = 26
_MACD_SIGNAL_P   = 9

# Entry / exit thresholds
_RSI_ENTRY_LOW   = 40.0
_RSI_ENTRY_HIGH  = 65.0
_RSI_EXIT_HIGH   = 75.0
_STOPLOSS_PCT    = 0.15            # 15% hard stoploss
_HOLD_DAYS       = 90              # max hold period
_HOLD_DAYS_180   = 125             # ~180 calendar days in trading days

# Universe
_MCAP_MIN_CR     = 500             # ₹500 Cr minimum market cap
_MAX_SYMBOLS     = 80              # monthly job cap — ~20–30 min runtime
_MIN_DATA_DAYS   = 252             # skip if < 252 trading days of history

# Benchmark
_BENCHMARK_YF    = "^NSEI"        # NIFTY 50

# NSE NIFTY 500 CSV (public, no auth needed)
_NIFTY500_URL    = (
    "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
)


# ── Technical Indicators ───────────────────────────────────────────────────────

def _rsi(close, period: int = _RSI_PERIOD):
    """RSI using Wilder's EMA smoothing."""
    import pandas as pd
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_l = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs    = avg_g / avg_l.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


def _ema(close, period: int):
    return close.ewm(span=period, adjust=False).mean()


def _macd_lines(close):
    """Returns (macd_line, signal_line)."""
    fast   = _ema(close, _MACD_FAST)
    slow   = _ema(close, _MACD_SLOW)
    macd   = fast - slow
    signal = _ema(macd, _MACD_SIGNAL_P)
    return macd, signal


def _add_indicators(df):
    """Attach RSI, EMA200, MACD columns in-place on a Close-indexed OHLCV df."""
    import pandas as pd
    df = df.copy()
    close            = df["Close"]
    df["rsi"]        = _rsi(close)
    df["ema200"]     = _ema(close, _EMA_PERIOD)
    macd, sig        = _macd_lines(close)
    df["macd"]       = macd
    df["macd_sig"]   = sig
    # Bullish MACD crossover: macd crosses above signal line
    df["macd_cross"] = (df["macd"] > df["macd_sig"]) & (
        df["macd"].shift(1) <= df["macd_sig"].shift(1)
    )
    return df


# ── Universe Building ──────────────────────────────────────────────────────────

def _load_nifty500_symbols() -> list[str]:
    """
    Download NIFTY 500 constituent list from NSE archives.
    Falls back to YF_SYMBOL_MAP keys if the download fails.
    Returns a list of NSE ticker strings (without .NS suffix).
    """
    import requests
    import pandas as pd
    from io import StringIO

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; BharatIntelligence/1.0)",
        "Referer": "https://www.nseindia.com/",
        "Accept": "text/csv,*/*",
    }
    try:
        resp = requests.get(_NIFTY500_URL, headers=headers, timeout=20)
        resp.raise_for_status()
        df   = pd.read_csv(StringIO(resp.text))
        col  = next((c for c in df.columns if c.strip().upper() == "SYMBOL"), None)
        if col:
            syms = df[col].str.strip().dropna().tolist()
            log.info("NIFTY 500 universe: %d symbols loaded from NSE", len(syms))
            return syms
    except Exception as exc:
        log.warning("NIFTY 500 CSV download failed (%s) — using YF_SYMBOL_MAP fallback", exc)

    try:
        from data.symbol_map import YF_SYMBOL_MAP
        syms = [k for k in YF_SYMBOL_MAP if not k.startswith("^") and "=" not in k]
        log.info("Fallback universe: %d symbols from YF_SYMBOL_MAP", len(syms))
        return syms
    except Exception:
        log.error("No universe available")
        return []


def _to_yf(symbol: str) -> str:
    """Convert NSE symbol to yfinance ticker, using YF_SYMBOL_MAP alias where available."""
    try:
        from data.symbol_map import YF_SYMBOL_MAP
        if symbol in YF_SYMBOL_MAP:
            return YF_SYMBOL_MAP[symbol]
    except Exception:
        pass
    return f"{symbol}.NS"


# ── Data Fetching ──────────────────────────────────────────────────────────────

def _fetch_ohlcv(ticker: str, start: str, end: str):
    """
    Download daily OHLCV from yfinance for a single ticker.
    Returns a timezone-naive DatetimeIndex DataFrame, or empty DataFrame on failure.
    """
    import yfinance as yf
    import pandas as pd

    try:
        df = yf.download(
            ticker,
            start=start,
            end=end,
            interval="1d",
            auto_adjust=True,
        )
        if df.empty:
            return pd.DataFrame()

        # Flatten MultiIndex columns (defensive, yfinance sometimes returns these)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        if "Close" not in df.columns:
            return pd.DataFrame()

        # Ensure timezone-naive DatetimeIndex (yfinance returns tz-aware for some tickers)
        df.index = pd.to_datetime(df.index).tz_localize(None)

        return df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])

    except Exception as exc:
        log.debug("OHLCV fetch failed for %s: %s", ticker, exc)
        return pd.DataFrame()


def _get_market_cap_cr(ticker: str) -> Optional[float]:
    """Return market cap in Crores via yfinance fast_info. Returns None on failure."""
    import yfinance as yf
    try:
        info = yf.Ticker(ticker).fast_info
        mcap = getattr(info, "market_cap", None)
        if mcap and mcap > 0:
            return mcap / 1e7   # 1 Crore = 10^7 rupees
    except Exception:
        pass
    return None


# ── Signal Generation ──────────────────────────────────────────────────────────

def _generate_signals(df, window_start: str, window_end: str) -> list[dict]:
    """
    Scan each day in [window_start, window_end] for BUY entry conditions.
    Entry: RSI 40–65 AND Close > EMA200 AND MACD bullish crossover.
    Returns list of {date, price, rsi, ema200}.
    """
    import pandas as pd

    if df.empty or len(df) < _MIN_DATA_DAYS:
        return []

    start_ts = pd.Timestamp(window_start)
    end_ts   = pd.Timestamp(window_end)
    mask     = (df.index >= start_ts) & (df.index <= end_ts)
    window   = df[mask]

    signals = []
    for ts, row in window.iterrows():
        rsi   = row.get("rsi")
        close = row.get("Close")
        ema200 = row.get("ema200")
        cross  = row.get("macd_cross", False)

        # Skip rows with NaN indicators (early in the series)
        if any(v is None or (isinstance(v, float) and math.isnan(v))
               for v in [rsi, close, ema200]):
            continue

        if (_RSI_ENTRY_LOW <= float(rsi) <= _RSI_ENTRY_HIGH
                and float(close) > float(ema200)
                and bool(cross)):
            signals.append({
                "date":   ts.strftime("%Y-%m-%d"),
                "price":  round(float(close), 2),
                "rsi":    round(float(rsi), 1),
                "ema200": round(float(ema200), 2),
            })

    return signals


# ── Trade Simulation ───────────────────────────────────────────────────────────

def _simulate_trade(
    entry_date: str,
    entry_price: float,
    df,           # full OHLCV+indicators DataFrame for this symbol
    bench_df,     # NIFTY 50 OHLCV
) -> dict:
    """
    Simulate a single trade from entry_date.
    Holds for up to _HOLD_DAYS trading days. Exits early on RSI > 75 or 15% stoploss.
    Returns a trade result dict, or {} if insufficient forward data.
    """
    import pandas as pd

    entry_ts   = pd.Timestamp(entry_date)
    stoploss   = entry_price * (1 - _STOPLOSS_PCT)

    # Forward price series after entry
    future = df[df.index > entry_ts].head(_HOLD_DAYS * 2)  # 2× buffer for calendar gaps
    if len(future) < 2:
        return {}

    exit_price  = float(future["Close"].iloc[-1])
    exit_date   = future.index[-1].strftime("%Y-%m-%d")
    exit_reason = "90D"
    days_held   = 0

    for i, (ts, row) in enumerate(future.iterrows()):
        days_held = i + 1
        close = float(row["Close"])
        rsi   = float(row.get("rsi", 50) or 50)
        if math.isnan(rsi):
            rsi = 50.0

        if close < stoploss:
            exit_price  = close
            exit_date   = ts.strftime("%Y-%m-%d")
            exit_reason = "STOPLOSS"
            break
        if rsi > _RSI_EXIT_HIGH:
            exit_price  = close
            exit_date   = ts.strftime("%Y-%m-%d")
            exit_reason = "RSI_EXIT"
            break
        if i >= _HOLD_DAYS - 1:
            exit_price  = close
            exit_date   = ts.strftime("%Y-%m-%d")
            exit_reason = "90D"
            break

    ret_90d = (exit_price - entry_price) / entry_price

    # ── Benchmark return for same period ──────────────────────────────────────
    bench_ret_90d = _benchmark_return(bench_df, entry_ts, pd.Timestamp(exit_date))
    alpha_90d     = ret_90d - bench_ret_90d

    # ── 180-day alpha (only if enough forward data) ───────────────────────────
    alpha_180d = None
    future_180 = df[df.index > entry_ts].head(_HOLD_DAYS_180)
    if len(future_180) >= _HOLD_DAYS_180 - 5:   # tolerate a few missing days
        price_180       = float(future_180["Close"].iloc[-1])
        exit_180_ts     = future_180.index[-1]
        ret_180d        = (price_180 - entry_price) / entry_price
        bench_ret_180d  = _benchmark_return(bench_df, entry_ts, exit_180_ts)
        alpha_180d      = round((ret_180d - bench_ret_180d) * 100, 2)

    return {
        "entry_date":     entry_date,
        "entry_price":    round(entry_price, 2),
        "exit_date":      exit_date,
        "exit_price":     round(exit_price, 2),
        "exit_reason":    exit_reason,
        "days_held":      days_held,
        "return_pct":     round(ret_90d * 100, 2),
        "nifty_ret_pct":  round(bench_ret_90d * 100, 2),
        "alpha_90d":      round(alpha_90d * 100, 2),
        "alpha_180d":     alpha_180d,
        "hit":            alpha_90d > 0,
    }


def _benchmark_return(bench_df, entry_ts, exit_ts) -> float:
    """Return NIFTY 50 return between entry_ts and exit_ts. Returns 0.0 on failure."""
    try:
        import pandas as pd
        entry_ts = pd.Timestamp(entry_ts)
        exit_ts  = pd.Timestamp(exit_ts)

        # Find closest benchmark price at/after entry
        bench_on_entry = bench_df[bench_df.index >= entry_ts]
        if bench_on_entry.empty:
            return 0.0
        entry_px = float(bench_on_entry["Close"].iloc[0])

        # Find closest benchmark price at/before exit
        bench_on_exit = bench_df[bench_df.index <= exit_ts]
        if bench_on_exit.empty:
            return 0.0
        exit_px = float(bench_on_exit["Close"].iloc[-1])

        return (exit_px - entry_px) / entry_px if entry_px > 0 else 0.0
    except Exception:
        return 0.0


# ── Metrics Aggregation ───────────────────────────────────────────────────────

def _aggregate(trades: list[dict]) -> dict:
    """
    Compute summary performance metrics from a list of completed trades.
    All return/alpha values in percent (%).
    """
    if not trades:
        return {
            "total_signals":  0,
            "hit_rate_90d":   0.0,
            "avg_alpha_90d":  0.0,
            "avg_alpha_180d": None,
            "sharpe_ratio":   None,
            "max_drawdown":   None,
            "win_loss_ratio": None,
        }

    alphas_90  = [t["alpha_90d"] for t in trades]
    alphas_180 = [t["alpha_180d"] for t in trades if t.get("alpha_180d") is not None]
    returns    = [t["return_pct"] for t in trades]
    hits       = [t["hit"] for t in trades]

    n          = len(trades)
    hit_rate   = sum(hits) / n * 100
    avg_a90    = sum(alphas_90) / n

    # Sharpe: mean(alpha_90d) / std(alpha_90d)  — cross-trade, not time-series
    if n > 1:
        var_a90 = sum((x - avg_a90) ** 2 for x in alphas_90) / (n - 1)
        std_a90 = math.sqrt(var_a90)
        sharpe  = (avg_a90 / std_a90) if std_a90 > 0 else None
    else:
        sharpe = None

    # Max drawdown: worst single trade return (as decimal fraction, not %)
    max_dd = min(returns) / 100 if returns else None

    # Win / loss ratio
    wins   = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    wl_ratio = None
    if wins and losses:
        avg_win  = sum(wins)  / len(wins)
        avg_loss = sum(losses) / len(losses)
        wl_ratio = round(avg_win / abs(avg_loss), 3)

    avg_a180 = round(sum(alphas_180) / len(alphas_180), 4) if alphas_180 else None

    return {
        "total_signals":  n,
        "hit_rate_90d":   round(hit_rate, 2),
        "avg_alpha_90d":  round(avg_a90, 4),
        "avg_alpha_180d": avg_a180,
        "sharpe_ratio":   round(sharpe, 3) if sharpe is not None else None,
        "max_drawdown":   round(max_dd, 4) if max_dd is not None else None,
        "win_loss_ratio": wl_ratio,
    }


# ── DB Persistence ─────────────────────────────────────────────────────────────

def _save_to_supabase(row: dict) -> bool:
    """Insert a backtest result row into the backtest_results table."""
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        log.warning("Supabase not configured — skipping backtest save")
        return False
    try:
        from supabase import create_client
        client = create_client(url, key)
        client.table("backtest_results").insert(row).execute()
        log.info(
            "Backtest saved | split=%-5s signals=%3d hit_rate=%.1f%% "
            "avg_alpha=%.2f%% sharpe=%s",
            row.get("split_type"), row.get("total_signals", 0),
            row.get("hit_rate_90d", 0), row.get("avg_alpha_90d", 0),
            row.get("sharpe_ratio"),
        )
        return True
    except Exception as exc:
        log.error("backtest save failed: %s", exc)
        return False


# ── Main Entry Point ───────────────────────────────────────────────────────────

def run_backtest(
    period_start: str = _DEFAULT_START,
    period_end:   str = _DEFAULT_END,
    train_cutoff: str = _TRAIN_CUTOFF,
    max_symbols:  int = _MAX_SYMBOLS,
    dry_run:     bool = False,
) -> dict:
    """
    Run the full walk-forward backtest and return a summary dict.

    Args:
        period_start: ISO date string, start of backtest window (inclusive)
        period_end:   ISO date string, end of backtest window (inclusive)
        train_cutoff: ISO date string, boundary between TRAIN and TEST splits
        max_symbols:  Maximum symbols to process (caps runtime)
        dry_run:      If True, compute results but do not save to Supabase

    Returns:
        {symbols_processed, period_start, period_end, train: {...}, test: {...}, full: {...}}
    """
    import pandas as pd

    log.info(
        "=== Backtest starting: %s → %s  train_cutoff=%s  max=%d symbols  dry_run=%s ===",
        period_start, period_end, train_cutoff, max_symbols, dry_run,
    )

    # ── 1. Build quality universe ──────────────────────────────────────────────
    nse_symbols = _load_nifty500_symbols()
    if not nse_symbols:
        return {"error": "Could not load NIFTY 500 universe"}

    # Convert to yfinance tickers, deduplicate, take 2× cap to allow for attrition
    candidates = list(dict.fromkeys(_to_yf(s) for s in nse_symbols[:max_symbols * 2]))
    log.info("Candidate pool: %d tickers (targeting %d after quality filter)", len(candidates), max_symbols)

    # ── 2. Download NIFTY 50 benchmark ────────────────────────────────────────
    log.info("Downloading NIFTY 50 benchmark (%s → %s)...", period_start, period_end)
    bench_df = _fetch_ohlcv(_BENCHMARK_YF, period_start, period_end)
    if bench_df.empty:
        return {"error": "Could not fetch NIFTY 50 benchmark data"}
    log.info("Benchmark ready: %d trading days", len(bench_df))

    # ── 3. Process each symbol ─────────────────────────────────────────────────
    all_train: list[dict] = []
    all_test:  list[dict] = []
    processed = 0
    skipped   = 0

    for ticker in candidates:
        if processed >= max_symbols:
            break

        # Download OHLCV
        df = _fetch_ohlcv(ticker, period_start, period_end)
        if df.empty or len(df) < _MIN_DATA_DAYS:
            skipped += 1
            log.debug("Skip %s — insufficient data (%d days)", ticker, len(df))
            continue

        # Market cap quality gate
        mcap_cr = _get_market_cap_cr(ticker)
        if mcap_cr is not None and mcap_cr < _MCAP_MIN_CR:
            skipped += 1
            log.debug("Skip %s — market cap ₹%.0f Cr < ₹%d Cr", ticker, mcap_cr, _MCAP_MIN_CR)
            continue

        # Compute technical indicators (once, on full history)
        df = _add_indicators(df)

        # Generate signals for each split window
        train_sigs = _generate_signals(df, period_start, train_cutoff)
        test_sigs  = _generate_signals(df, train_cutoff, period_end)

        log.debug("%s: %d train signals, %d test signals", ticker, len(train_sigs), len(test_sigs))

        # Simulate each trade
        for sig in train_sigs:
            trade = _simulate_trade(sig["date"], sig["price"], df, bench_df)
            if trade:
                trade["symbol"] = ticker
                all_train.append(trade)

        for sig in test_sigs:
            trade = _simulate_trade(sig["date"], sig["price"], df, bench_df)
            if trade:
                trade["symbol"] = ticker
                all_test.append(trade)

        processed += 1
        if processed % 10 == 0:
            log.info(
                "Progress: %d/%d symbols — train_trades=%d test_trades=%d",
                processed, max_symbols, len(all_train), len(all_test),
            )

    log.info(
        "Processing complete: %d symbols (skipped %d) — "
        "TRAIN trades: %d | TEST trades: %d",
        processed, skipped, len(all_train), len(all_test),
    )

    # ── 4. Aggregate metrics ───────────────────────────────────────────────────
    train_metrics = _aggregate(all_train)
    test_metrics  = _aggregate(all_test)
    full_metrics  = _aggregate(all_train + all_test)

    # ── 5. Build DB rows (cap signal_details at 500 trades to avoid huge JSONB) ─
    today = datetime.utcnow().date().isoformat()

    def _build_row(metrics: dict, split: str, p_start: str, p_end: str,
                   details: list) -> dict:
        return {
            "run_date":       today,
            "universe":       "NIFTY500_QUALITY",
            "period_start":   p_start,
            "period_end":     p_end,
            "split_type":     split,
            "signal_details": details[:500] if details else None,
            **metrics,
        }

    train_row = _build_row(train_metrics, "TRAIN", period_start, train_cutoff, all_train)
    test_row  = _build_row(test_metrics,  "TEST",  train_cutoff, period_end,   all_test)
    full_row  = _build_row(full_metrics,  "FULL",  period_start, period_end,   [])

    # ── 6. Persist ─────────────────────────────────────────────────────────────
    if not dry_run:
        _save_to_supabase(train_row)
        _save_to_supabase(test_row)
        _save_to_supabase(full_row)

    summary = {
        "symbols_processed": processed,
        "symbols_skipped":   skipped,
        "period_start":      period_start,
        "period_end":        period_end,
        "train_cutoff":      train_cutoff,
        "dry_run":           dry_run,
        "train":             train_metrics,
        "test":              test_metrics,
        "full":              full_metrics,
    }

    log.info(
        "=== Backtest complete | TEST: signals=%d hit_rate=%.1f%% "
        "avg_alpha=%.2f%% sharpe=%s ===",
        test_metrics["total_signals"],
        test_metrics["hit_rate_90d"],
        test_metrics["avg_alpha_90d"],
        test_metrics.get("sharpe_ratio"),
    )

    return summary


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    parser = argparse.ArgumentParser(description="Bharat Intelligence — Historical Backtest")
    parser.add_argument("--start",        default=_DEFAULT_START,
                        help="Backtest start date (YYYY-MM-DD)")
    parser.add_argument("--end",          default=_DEFAULT_END,
                        help="Backtest end date (YYYY-MM-DD)")
    parser.add_argument("--train-cutoff", default=_TRAIN_CUTOFF,
                        help="Train/test split date (YYYY-MM-DD)")
    parser.add_argument("--max-symbols",  type=int, default=_MAX_SYMBOLS,
                        help="Max symbols to process")
    parser.add_argument("--dry-run",      action="store_true",
                        help="Compute but do not save to Supabase")
    parser.add_argument("--verbose",      action="store_true",
                        help="Set log level to DEBUG")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    result = run_backtest(
        period_start=args.start,
        period_end=args.end,
        train_cutoff=args.train_cutoff,
        max_symbols=args.max_symbols,
        dry_run=args.dry_run,
    )

    if "error" in result:
        print(f"\n❌ Error: {result['error']}")
    else:
        print("\n╔══════════════════════════════════════════╗")
        print("║       BACKTEST SUMMARY                   ║")
        print("╠══════════════════════════════════════════╣")
        print(f"║  Symbols processed : {result['symbols_processed']:<21}║")
        print(f"║  Period            : {result['period_start']} → {result['period_end']} ║")
        print(f"║  Dry run           : {str(result['dry_run']):<21}║")
        print("╠══════════════════════════════════════════╣")
        for split in ["train", "test", "full"]:
            m = result[split]
            label = split.upper()
            print(f"║  [{label}]")
            print(f"║    Signals    : {m['total_signals']:<26}║")
            print(f"║    Hit rate   : {m['hit_rate_90d']:.1f}%{'':<23}║")
            print(f"║    Avg alpha  : {m['avg_alpha_90d']:.2f}%{'':<22}║")
            if m.get("avg_alpha_180d") is not None:
                print(f"║    Alpha 180d : {m['avg_alpha_180d']:.2f}%{'':<22}║")
            print(f"║    Sharpe     : {str(m.get('sharpe_ratio') or 'N/A'):<26}║")
            print(f"║    Max DD     : {str(m.get('max_drawdown') or 'N/A'):<26}║")
            print(f"║    Win/Loss   : {str(m.get('win_loss_ratio') or 'N/A'):<26}║")
            print("║                                          ║")
        print("╚══════════════════════════════════════════╝")
