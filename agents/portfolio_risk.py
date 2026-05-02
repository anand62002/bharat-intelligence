"""
agents/portfolio_risk.py — Portfolio-Level Risk Framework
==========================================================
Computes portfolio-wide risk metrics for the open holdings in Supabase:

  • Correlation matrix   — pairwise Pearson correlation of 1-yr daily returns
  • Sector concentration — % of portfolio value in each GICS sector
  • Value-at-Risk (VaR)  — 95 % and 99 % historical VaR (1-day horizon)
  • Conditional VaR (CVaR / Expected Shortfall)
  • Portfolio volatility  — annualised (√252)
  • Sharpe estimate      — (portfolio return - Rf) / portfolio_vol; Rf = 6.5 % p.a.
  • Max drawdown         — per holding and portfolio
  • Herfindahl–Hirschman Index (HHI) — sector concentration measure

Supabase migration:
-------------------
    CREATE TABLE IF NOT EXISTS portfolio_risk_snapshots (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        snapshot_date   DATE NOT NULL,
        portfolio_id    TEXT NOT NULL DEFAULT 'default',
        metrics         JSONB NOT NULL,
        correlation     JSONB,
        sector_weights  JSONB,
        created_at      TIMESTAMPTZ DEFAULT now(),
        UNIQUE(snapshot_date, portfolio_id)
    );
    GRANT ALL ON portfolio_risk_snapshots TO service_role;

Usage
-----
    from agents.portfolio_risk import run_portfolio_risk
    result = run_portfolio_risk()
    # result['var_95'], result['cvar_95'], result['portfolio_vol'],
    # result['sharpe'], result['sector_weights'], result['hhi'],
    # result['correlation_matrix'], result['max_drawdown_pct'], ...

    # Save snapshot to Supabase
    from agents.portfolio_risk import run_portfolio_risk, save_risk_snapshot
    metrics = run_portfolio_risk()
    save_risk_snapshot(metrics)

Standalone
----------
    python -m agents.portfolio_risk
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_RISK_FREE_RATE = 0.065     # 6.5% p.a. (approx Indian 10-yr G-sec)
_TRADING_DAYS   = 252


# ──────────────────────────────────────────────────────────────────────────────
# Data loading
# ──────────────────────────────────────────────────────────────────────────────

def _load_holdings() -> list[dict]:
    """Load open portfolio holdings from Supabase."""
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        return []
    from supabase import create_client
    rows = (
        create_client(url, key)
        .table("portfolio_holdings")
        .select("symbol,yf_symbol,qty,avg_buy,current_price,sector")
        .eq("status", "OPEN")
        .execute()
        .data or []
    )
    return rows


def _fetch_returns(yf_symbols: list[str], period: str = "1y") -> "pd.DataFrame":
    """Return daily pct-change return DataFrame for the given symbols."""
    import yfinance as yf
    import pandas as pd

    if not yf_symbols:
        return pd.DataFrame()

    raw = yf.download(
        tickers   = yf_symbols,
        period    = period,
        interval  = "1d",
        auto_adjust = True,
        progress  = False,
    )

    # yfinance returns MultiIndex columns when >1 ticker, single level for 1
    if isinstance(raw.columns, pd.MultiIndex):
        closes = raw["Close"]
    else:
        closes = raw[["Close"]].rename(columns={"Close": yf_symbols[0]})

    returns = closes.pct_change().dropna(how="all")
    return returns


# ──────────────────────────────────────────────────────────────────────────────
# Risk calculations
# ──────────────────────────────────────────────────────────────────────────────

def _portfolio_weights(holdings: list[dict]) -> dict[str, float]:
    """Return {yf_symbol: weight} — weight = (qty × current_price) / total_value."""
    values = {
        h["yf_symbol"]: float(h["qty"]) * float(h.get("current_price") or h.get("avg_buy") or 1)
        for h in holdings
    }
    total = sum(values.values())
    if total == 0:
        return {}
    return {sym: v / total for sym, v in values.items()}


def _sector_weights(holdings: list[dict]) -> dict[str, float]:
    """Return {sector: weight} — weight = sector_value / total_value."""
    from collections import defaultdict
    sector_val: dict = defaultdict(float)
    total = 0.0
    for h in holdings:
        val  = float(h["qty"]) * float(h.get("current_price") or h.get("avg_buy") or 1)
        sect = (h.get("sector") or "Unknown").strip() or "Unknown"
        sector_val[sect] += val
        total += val
    if total == 0:
        return {}
    return {s: round(v / total, 4) for s, v in sorted(sector_val.items(), key=lambda x: -x[1])}


def _hhi(weights: dict[str, float]) -> float:
    """Herfindahl–Hirschman Index (0–1). >0.25 = highly concentrated."""
    return round(sum(w ** 2 for w in weights.values()), 4)


def _portfolio_returns(returns: "pd.DataFrame", weights: dict[str, float]) -> "pd.Series":
    """Compute weighted portfolio daily returns."""
    cols = [c for c in returns.columns if c in weights]
    if not cols:
        import pandas as pd
        return pd.Series(dtype=float)
    w_arr = [weights[c] for c in cols]
    # Re-normalise weights for available symbols
    total_w = sum(w_arr)
    w_norm  = [w / total_w for w in w_arr]
    import numpy as np
    port_ret = returns[cols].fillna(0).values @ w_norm
    import pandas as pd
    return pd.Series(port_ret, index=returns[cols].dropna(how="all").index)


def _var(port_returns: "pd.Series", confidence: float = 0.95) -> float:
    """Historical VaR at `confidence` level (positive = loss)."""
    import numpy as np
    q = 1.0 - confidence
    return float(-np.percentile(port_returns.dropna(), q * 100))


def _cvar(port_returns: "pd.Series", confidence: float = 0.95) -> float:
    """Conditional VaR (Expected Shortfall) at `confidence` level."""
    import numpy as np
    cutoff = _var(port_returns, confidence)
    tail   = port_returns[port_returns <= -cutoff]
    return float(-tail.mean()) if len(tail) > 0 else cutoff


def _max_drawdown(price_series: "pd.Series") -> float:
    """Max drawdown from peak (positive = loss)."""
    import numpy as np
    cummax = price_series.cummax()
    dd     = (price_series - cummax) / cummax
    return float(-dd.min())


def _annualised_vol(port_returns: "pd.Series") -> float:
    """Annualised portfolio volatility."""
    import numpy as np
    return float(port_returns.std() * (252 ** 0.5))


def _sharpe(port_returns: "pd.Series", vol: float) -> Optional[float]:
    """Annualised Sharpe ratio."""
    import numpy as np
    ann_return = float(port_returns.mean() * 252)
    if vol == 0:
        return None
    return round((ann_return - _RISK_FREE_RATE) / vol, 2)


def _correlation_matrix(returns: "pd.DataFrame") -> dict:
    """Return correlation matrix as a dict-of-dicts."""
    corr = returns.corr()
    return {
        col: {row: round(float(corr.loc[row, col]), 4)
              for row in corr.index}
        for col in corr.columns
    }


def _max_drawdown_per_holding(returns: "pd.DataFrame") -> dict[str, float]:
    """Return {yf_symbol: max_drawdown_pct} for each holding."""
    result = {}
    for col in returns.columns:
        cum_price = (1 + returns[col].fillna(0)).cumprod()
        result[col] = round(_max_drawdown(cum_price) * 100, 2)
    return result


def _concentration_risk_level(hhi: float) -> str:
    """Classify portfolio concentration."""
    if hhi < 0.10:
        return "LOW"
    elif hhi < 0.25:
        return "MODERATE"
    else:
        return "HIGH"


# ──────────────────────────────────────────────────────────────────────────────
# Main function
# ──────────────────────────────────────────────────────────────────────────────

def run_portfolio_risk(dry_run: bool = False) -> dict:
    """
    Compute portfolio risk metrics for all OPEN holdings.

    Returns
    -------
    dict with keys:
      holdings_count, total_value_inr, portfolio_vol, sharpe,
      var_95, var_99, cvar_95, max_drawdown_pct,
      sector_weights, hhi, concentration_risk,
      correlation_matrix, max_drawdown_per_holding,
      top_correlated_pairs, warnings, snapshot_date, error
    """
    import numpy as np

    result: dict = {
        "holdings_count":           0,
        "total_value_inr":          0.0,
        "portfolio_vol":            None,
        "sharpe":                   None,
        "var_95":                   None,
        "var_99":                   None,
        "cvar_95":                  None,
        "max_drawdown_pct":         None,
        "sector_weights":           {},
        "hhi":                      None,
        "concentration_risk":       "UNKNOWN",
        "correlation_matrix":       {},
        "max_drawdown_per_holding": {},
        "top_correlated_pairs":     [],
        "warnings":                 [],
        "snapshot_date":            str(date.today()),
        "error":                    None,
    }

    try:
        # ── Load holdings ──────────────────────────────────────────────────────
        holdings = _load_holdings()
        if not holdings:
            result["error"] = "no open holdings"
            return result

        result["holdings_count"] = len(holdings)
        total_val = sum(
            float(h["qty"]) * float(h.get("current_price") or h.get("avg_buy") or 1)
            for h in holdings
        )
        result["total_value_inr"] = round(total_val, 2)

        # ── Sector weights + HHI ───────────────────────────────────────────────
        sect_w = _sector_weights(holdings)
        result["sector_weights"] = sect_w
        hhi = _hhi(sect_w)
        result["hhi"]                = hhi
        result["concentration_risk"] = _concentration_risk_level(hhi)

        if hhi >= 0.25:
            top_sector = max(sect_w, key=sect_w.get)
            result["warnings"].append(
                f"HIGH sector concentration: {top_sector} = {sect_w[top_sector]*100:.1f}% of portfolio"
            )

        # ── Price returns ──────────────────────────────────────────────────────
        yf_syms = [h["yf_symbol"] for h in holdings if h.get("yf_symbol")]
        if not yf_syms:
            result["error"] = "no yf_symbols in holdings"
            return result

        returns = _fetch_returns(yf_syms, period="1y")
        if returns.empty:
            result["error"] = "could not fetch return data"
            return result

        # ── Portfolio weights + returns ────────────────────────────────────────
        weights    = _portfolio_weights(holdings)
        port_rets  = _portfolio_returns(returns, weights)

        if port_rets.empty or port_rets.std() == 0:
            result["error"] = "insufficient return data"
            return result

        # ── Core metrics ───────────────────────────────────────────────────────
        vol = _annualised_vol(port_rets)
        result["portfolio_vol"]    = round(vol * 100, 2)        # as %
        result["sharpe"]           = _sharpe(port_rets, vol)
        result["var_95"]           = round(_var(port_rets, 0.95) * 100, 2)
        result["var_99"]           = round(_var(port_rets, 0.99) * 100, 2)
        result["cvar_95"]          = round(_cvar(port_rets, 0.95) * 100, 2)

        # ── Max drawdown ───────────────────────────────────────────────────────
        cum_portfolio = (1 + port_rets.fillna(0)).cumprod()
        result["max_drawdown_pct"] = round(_max_drawdown(cum_portfolio) * 100, 2)

        # ── Per-holding drawdown ───────────────────────────────────────────────
        result["max_drawdown_per_holding"] = _max_drawdown_per_holding(returns)

        # ── Correlation matrix ─────────────────────────────────────────────────
        if len(returns.columns) > 1:
            result["correlation_matrix"] = _correlation_matrix(returns)

            # ── Top correlated pairs ───────────────────────────────────────────
            corr_df = returns.corr()
            pairs = []
            cols  = list(corr_df.columns)
            for i in range(len(cols)):
                for j in range(i + 1, len(cols)):
                    c = float(corr_df.iloc[i, j])
                    pairs.append({"a": cols[i], "b": cols[j], "correlation": round(c, 4)})
            # Sort by absolute correlation desc, keep top 5
            pairs.sort(key=lambda x: abs(x["correlation"]), reverse=True)
            result["top_correlated_pairs"] = pairs[:5]

            # Warn on highly correlated holdings
            for p in pairs[:5]:
                if abs(p["correlation"]) > 0.85:
                    result["warnings"].append(
                        f"High correlation {p['a']} ↔ {p['b']}: {p['correlation']:.2f} — limited diversification"
                    )

        # ── VaR warnings ──────────────────────────────────────────────────────
        if result["var_99"] is not None and result["var_99"] > 3.0:
            result["warnings"].append(
                f"Elevated tail risk: 99% VaR = {result['var_99']:.2f}% daily loss"
            )

        # ── Persist snapshot ───────────────────────────────────────────────────
        if not dry_run:
            save_risk_snapshot(result)

    except Exception as exc:
        log.error("portfolio_risk failed: %s", exc, exc_info=True)
        result["error"] = str(exc)

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Supabase persistence
# ──────────────────────────────────────────────────────────────────────────────

def save_risk_snapshot(metrics: dict, portfolio_id: str = "default") -> None:
    """Upsert risk metrics into portfolio_risk_snapshots table."""
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        return
    try:
        from supabase import create_client
        client = create_client(url, key)

        top_metrics = {
            k: metrics[k]
            for k in (
                "holdings_count", "total_value_inr", "portfolio_vol",
                "sharpe", "var_95", "var_99", "cvar_95",
                "max_drawdown_pct", "hhi", "concentration_risk",
                "top_correlated_pairs", "warnings", "error",
            )
            if k in metrics
        }
        top_metrics["max_drawdown_per_holding"] = metrics.get("max_drawdown_per_holding", {})

        (
            client
            .table("portfolio_risk_snapshots")
            .upsert(
                {
                    "snapshot_date":  metrics["snapshot_date"],
                    "portfolio_id":   portfolio_id,
                    "metrics":        top_metrics,
                    "correlation":    metrics.get("correlation_matrix", {}),
                    "sector_weights": metrics.get("sector_weights", {}),
                },
                on_conflict="snapshot_date,portfolio_id",
            )
            .execute()
        )
        log.info("portfolio_risk: snapshot saved for %s", metrics["snapshot_date"])
    except Exception as exc:
        log.warning("portfolio_risk: save_snapshot failed: %s", exc)


def load_latest_snapshot(portfolio_id: str = "default") -> Optional[dict]:
    """Load the most recent portfolio risk snapshot from Supabase."""
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        rows = (
            create_client(url, key)
            .table("portfolio_risk_snapshots")
            .select("*")
            .eq("portfolio_id", portfolio_id)
            .order("snapshot_date", desc=True)
            .limit(1)
            .execute()
            .data or []
        )
        if not rows:
            return None
        row = rows[0]
        return {
            "snapshot_date":   row["snapshot_date"],
            "sector_weights":  row.get("sector_weights") or {},
            "correlation":     row.get("correlation") or {},
            **(row.get("metrics") or {}),
        }
    except Exception as exc:
        log.warning("load_latest_snapshot failed: %s", exc)
        return None


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    result = run_portfolio_risk(dry_run=True)
    # Remove large correlation dict for CLI readability
    print(json.dumps({k: v for k, v in result.items() if k != "correlation_matrix"}, indent=2))
    if result.get("warnings"):
        print("\n⚠ WARNINGS:")
        for w in result["warnings"]:
            print(" •", w)
