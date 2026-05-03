"""
agents/valuation_scenarios.py — Valuation Sensitivity Analysis
===============================================================
Generates bull / base / bear DCF valuation scenarios for a stock
and computes intrinsic value ranges, margin-of-safety bands, and a
"sensitivity tornado" showing which assumption drives value most.

3-Stage DCF Model (same as warren_bot)
--------------------------------------
  Stage 1 (yr 1–5) : grow at growth_rate, discount at wacc
  Stage 2 (yr 6–10): growth fades linearly to terminal_growth
  Terminal          : Gordon Growth Model — IV = FCF_11 / (wacc - g_terminal)

Scenarios
---------
  BULL  : Revenue CAGR +5pp, EBITDA margin +3pp, WACC –1pp, terminal growth +1pp
  BASE  : Uses warrent_bot-equivalent assumptions from screener data
  BEAR  : Revenue CAGR –5pp, EBITDA margin –3pp, WACC +2pp, terminal growth –1pp

Sensitivity (tornado)
---------------------
  Each assumption shifted ±1σ independently while others stay at BASE.
  Sorted by impact on intrinsic value → shows which lever matters most.

Output keys
-----------
  symbol, current_price, scenarios (BULL/BASE/BEAR each with
  intrinsic_value, margin_of_safety_pct, growth_rate, wacc, terminal_g),
  fair_value_range (low, mid, high),
  margin_of_safety (bull, base, bear),
  upside_pct (bull, base, bear),
  tornado (list of {assumption, low_iv, high_iv, impact}),
  recommendation, agent_name

Usage
-----
    from agents.valuation_scenarios import run_scenarios
    r = run_scenarios("RELIANCE")
    # See output keys above

Standalone
----------
    python -m agents.valuation_scenarios RELIANCE
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)
AGENT_NAME = "valuation_scenarios"

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ──────────────────────────────────────────────────────────────────────────────
# Scenario parameter deltas
# ──────────────────────────────────────────────────────────────────────────────

_SCENARIO_DELTAS = {
    "BULL": {
        "growth_adj":    +0.05,   # +5pp on base revenue CAGR
        "margin_adj":    +0.03,   # +3pp on base EBITDA margin
        "wacc_adj":      -0.01,   # –1pp on WACC
        "terminal_adj":  +0.01,   # +1pp on terminal growth
    },
    "BASE": {
        "growth_adj":    0.0,
        "margin_adj":    0.0,
        "wacc_adj":      0.0,
        "terminal_adj":  0.0,
    },
    "BEAR": {
        "growth_adj":    -0.05,
        "margin_adj":    -0.03,
        "wacc_adj":      +0.02,
        "terminal_adj":  -0.01,
    },
}

# Tornado sensitivity shifts (± from base for each assumption)
_TORNADO_VARS = {
    "Revenue Growth":    ("growth_adj",   0.03),   # ±3pp shift
    "EBITDA Margin":     ("margin_adj",   0.02),   # ±2pp shift
    "WACC":              ("wacc_adj",     0.01),   # ±1pp shift
    "Terminal Growth":   ("terminal_adj", 0.01),   # ±1pp shift
}

# Hard limits
_MIN_GROWTH     = -0.10   # –10%
_MAX_GROWTH     = 0.30    # +30%
_MIN_WACC       = 0.08    # 8%
_MAX_WACC       = 0.20    # 20%
_MIN_TERMINAL   = 0.03    # 3%
_MAX_TERMINAL   = 0.09    # 9%


# ──────────────────────────────────────────────────────────────────────────────
# Core DCF engine
# ──────────────────────────────────────────────────────────────────────────────

def _dcf(
    owner_earnings_cr: float,
    growth_rate: float,
    wacc: float,
    terminal_growth: float,
    shares_cr: float,
) -> Optional[float]:
    """
    3-stage DCF. Returns intrinsic value per share (₹) or None on invalid inputs.

    Parameters
    ----------
    owner_earnings_cr : latest FCF / owner earnings in ₹ Crore
    growth_rate       : Stage-1 (yr 1–5) annual growth rate (decimal)
    wacc              : discount rate (decimal)
    terminal_growth   : Gordon Growth Model terminal rate (decimal)
    shares_cr         : shares outstanding in Crore
    """
    if owner_earnings_cr <= 0 or shares_cr <= 0:
        return None
    if wacc <= terminal_growth:
        return None

    g1 = min(growth_rate, _MAX_GROWTH)
    g1 = max(g1, _MIN_GROWTH)

    # Stage 1: years 1–5
    cf = owner_earnings_cr
    total_dcf = 0.0
    for yr in range(1, 6):
        cf *= (1 + g1)
        total_dcf += cf / ((1 + wacc) ** yr)

    # Stage 2: years 6–10 — fade to terminal growth
    g2_floor = max(terminal_growth, 0.05)
    cf_stage2 = cf
    for step in range(5):
        fade = step / 4.0
        g2 = g1 + fade * (g2_floor - g1)
        cf_stage2 *= (1 + g2)
        total_dcf += cf_stage2 / ((1 + wacc) ** (6 + step))

    # Terminal value
    terminal_cf = cf_stage2 * (1 + terminal_growth)
    tv = terminal_cf / (wacc - terminal_growth)
    total_dcf += tv / ((1 + wacc) ** 10)

    return total_dcf / shares_cr   # ₹ per share


# ──────────────────────────────────────────────────────────────────────────────
# Scenario runner
# ──────────────────────────────────────────────────────────────────────────────

def _run_scenario(
    scenario_name: str,
    owner_earnings_cr: float,
    base_growth: float,
    base_wacc: float,
    base_terminal: float,
    shares_cr: float,
    current_price: float,
) -> dict:
    """Return scenario result dict."""
    d = _SCENARIO_DELTAS[scenario_name]
    g = max(_MIN_GROWTH,   min(_MAX_GROWTH,   base_growth   + d["growth_adj"]))
    w = max(_MIN_WACC,     min(_MAX_WACC,     base_wacc     + d["wacc_adj"]))
    t = max(_MIN_TERMINAL, min(_MAX_TERMINAL, base_terminal + d["terminal_adj"]))

    iv = _dcf(owner_earnings_cr, g, w, t, shares_cr)
    mos = (
        round((iv - current_price) / iv * 100, 1)
        if iv and iv > 0
        else None
    )
    upside = (
        round((iv - current_price) / current_price * 100, 1)
        if iv and current_price > 0
        else None
    )
    return {
        "scenario":            scenario_name,
        "intrinsic_value":     round(iv, 2) if iv else None,
        "margin_of_safety_pct": mos,
        "upside_pct":          upside,
        "growth_rate":         round(g * 100, 2),   # % for display
        "wacc":                round(w * 100, 2),
        "terminal_growth":     round(t * 100, 2),
    }


def _build_tornado(
    owner_earnings_cr: float,
    base_growth: float,
    base_wacc: float,
    base_terminal: float,
    shares_cr: float,
    current_price: float,
) -> list[dict]:
    """
    Tornado sensitivity: shift each variable ±σ independently.
    Returns list sorted by |impact| descending.
    """
    base_iv = _dcf(owner_earnings_cr, base_growth, base_wacc, base_terminal, shares_cr) or 1.0

    rows = []
    for label, (var_key, shift) in _TORNADO_VARS.items():
        d_base = _SCENARIO_DELTAS["BASE"].copy()

        # Low-case: push variable in bearish direction
        d_low = d_base.copy()
        d_low[var_key] = -shift if var_key != "wacc_adj" else +shift
        g_l = max(_MIN_GROWTH,   min(_MAX_GROWTH,   base_growth   + d_low.get("growth_adj", 0)))
        w_l = max(_MIN_WACC,     min(_MAX_WACC,     base_wacc     + d_low.get("wacc_adj", 0)))
        t_l = max(_MIN_TERMINAL, min(_MAX_TERMINAL, base_terminal + d_low.get("terminal_adj", 0)))
        iv_low = _dcf(owner_earnings_cr, g_l, w_l, t_l, shares_cr) or base_iv

        # High-case: push variable in bullish direction
        d_high = d_base.copy()
        d_high[var_key] = +shift if var_key != "wacc_adj" else -shift
        g_h = max(_MIN_GROWTH,   min(_MAX_GROWTH,   base_growth   + d_high.get("growth_adj", 0)))
        w_h = max(_MIN_WACC,     min(_MAX_WACC,     base_wacc     + d_high.get("wacc_adj", 0)))
        t_h = max(_MIN_TERMINAL, min(_MAX_TERMINAL, base_terminal + d_high.get("terminal_adj", 0)))
        iv_high = _dcf(owner_earnings_cr, g_h, w_h, t_h, shares_cr) or base_iv

        impact = abs(iv_high - iv_low)
        rows.append({
            "assumption":  label,
            "low_iv":      round(iv_low, 2),
            "high_iv":     round(iv_high, 2),
            "impact":      round(impact, 2),
            "impact_pct":  round(impact / base_iv * 100, 1),
        })

    rows.sort(key=lambda r: r["impact"], reverse=True)
    return rows


# ──────────────────────────────────────────────────────────────────────────────
# Base parameter extraction
# ──────────────────────────────────────────────────────────────────────────────

def _extract_base_params(symbol: str) -> dict:
    """
    Extract base DCF inputs from screener data + yfinance.

    Returns dict with:
        owner_earnings_cr, base_growth, base_wacc,
        base_terminal, shares_cr, current_price
    """
    base = {
        "owner_earnings_cr": None,
        "base_growth":       0.12,
        "base_wacc":         0.12,
        "base_terminal":     0.07,
        "shares_cr":         None,
        "current_price":     None,
    }

    try:
        from data.fetchers import get_screener_data, get_screener_history
        raw  = get_screener_data(symbol) or {}
        hist = get_screener_history(symbol) or {}

        # Owner earnings proxy: PAT – capex + depreciation
        pat_hist   = [float(v) for v in (hist.get("pat") or []) if v is not None]
        dep_hist   = [float(v) for v in (hist.get("depreciation") or []) if v is not None]
        capex_hist = [float(v) for v in (hist.get("capex") or []) if v is not None]

        if pat_hist and capex_hist:
            n = min(len(pat_hist), len(capex_hist), len(dep_hist) or len(pat_hist), 3)
            oe_list = []
            for i in range(-n, 0):
                pat   = pat_hist[i]
                dep   = dep_hist[i] if dep_hist else 0.0
                capex = abs(capex_hist[i])
                oe_list.append(pat + dep - capex)
            if oe_list:
                base["owner_earnings_cr"] = sum(oe_list) / len(oe_list)

        # Base growth from revenue/EPS CAGR (inline to avoid warren_bot dependency)
        rev_hist = [float(v) for v in (hist.get("revenue") or []) if v is not None]
        eps_hist = [float(v) for v in (hist.get("eps") or []) if v is not None]
        eps_cagr_raw = float(raw.get("eps_cagr_5y") or 0)
        eps_cagr = eps_cagr_raw / 100

        def _cagr_inline(vals: list, years: int) -> Optional[float]:
            if len(vals) < years + 1:
                return None
            start = float(vals[-years - 1])
            end   = float(vals[-1])
            if start <= 0 or end <= 0:
                return None
            return (end / start) ** (1.0 / years) - 1.0

        growth_inputs = []
        rev_cagr = _cagr_inline(rev_hist, 4)
        if rev_cagr is not None:
            growth_inputs.append(rev_cagr)
        e_cagr = _cagr_inline(eps_hist, 4)
        if e_cagr is not None:
            growth_inputs.append(e_cagr)
        if eps_cagr:
            growth_inputs.append(eps_cagr)
        if growth_inputs:
            base["base_growth"] = max(0.03, min(0.25, sum(growth_inputs) / len(growth_inputs)))

        # Shares outstanding from raw screener (market cap / price)
        mcap = float(raw.get("market_cap") or 0)
        price = float(raw.get("current_price") or raw.get("price") or 0)
        if mcap > 0 and price > 0:
            base["shares_cr"] = mcap / price / 100   # Cr shares

        # Current price from yfinance
        try:
            import yfinance as yf
            from data.symbol_map import YF_SYMBOL_MAP
            yf_sym = YF_SYMBOL_MAP.get(symbol, symbol + ".NS")
            t = yf.Ticker(yf_sym)
            info = t.fast_info
            cp = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None)
            if cp and cp > 0:
                base["current_price"] = float(cp)
                if not base["shares_cr"] and mcap > 0:
                    base["shares_cr"] = mcap / cp / 100
        except Exception:
            pass

        if not base["current_price"] and price > 0:
            base["current_price"] = price

    except Exception as exc:
        log.debug("valuation_scenarios(%s): data extraction failed: %s", symbol, exc)

    return base


# ──────────────────────────────────────────────────────────────────────────────
# Recommendation from scenario range
# ──────────────────────────────────────────────────────────────────────────────

def _recommendation(base_mos: Optional[float], bear_mos: Optional[float]) -> str:
    """
    Signal based on BASE margin of safety and BEAR downside protection.
    """
    if base_mos is None:
        return "INSUFFICIENT_DATA"
    if base_mos >= 40 and (bear_mos or 0) >= 10:
        return "STRONG_BUY"
    if base_mos >= 20:
        return "BUY"
    if base_mos >= 0:
        return "HOLD"
    if base_mos >= -20:
        return "AVOID"
    return "SELL"


# ──────────────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────────────

def run_scenarios(symbol: str) -> dict:
    """
    Run bull / base / bear DCF valuation scenarios for `symbol`.

    Returns
    -------
    {
      symbol, current_price,
      scenarios: {BULL: {...}, BASE: {...}, BEAR: {...}},
      fair_value_range: {low, mid, high},
      margin_of_safety:  {bull, base, bear},
      upside_pct:        {bull, base, bear},
      tornado: [{assumption, low_iv, high_iv, impact, impact_pct}],
      recommendation, data_quality, agent_name
    }
    """
    sym = symbol.upper().replace(".NS", "").replace(".BO", "")

    empty = {
        "symbol":           sym,
        "current_price":    None,
        "scenarios":        {},
        "fair_value_range": {"low": None, "mid": None, "high": None},
        "margin_of_safety": {"bull": None, "base": None, "bear": None},
        "upside_pct":       {"bull": None, "base": None, "bear": None},
        "tornado":          [],
        "recommendation":   "INSUFFICIENT_DATA",
        "data_quality":     "NO_DATA",
        "agent_name":       AGENT_NAME,
    }

    try:
        params = _extract_base_params(sym)

        oe    = params["owner_earnings_cr"]
        bg    = params["base_growth"]
        bw    = params["base_wacc"]
        bt    = params["base_terminal"]
        sh    = params["shares_cr"]
        price = params["current_price"]

        # Data quality assessment
        data_ok = (oe is not None and oe > 0 and sh is not None and sh > 0 and price is not None)
        data_quality = "FULL" if data_ok else "PARTIAL"

        # If we have no owner earnings, use a proxy: PE × EPS × shares ÷ PE_assumed
        if (oe is None or oe <= 0) and price and sh:
            # Rough proxy: assume 4% FCF yield on market cap
            mcap_cr = price * sh * 100
            oe_proxy = mcap_cr * 0.04
            log.debug("valuation_scenarios(%s): using FCF proxy OE=%.0f Cr", sym, oe_proxy)
            oe = oe_proxy
            data_quality = "ESTIMATED"

        if oe is None or sh is None or price is None:
            return {**empty, "data_quality": "NO_DATA"}

        # Run three scenarios
        scenarios = {}
        for sc in ("BULL", "BASE", "BEAR"):
            scenarios[sc] = _run_scenario(sc, oe, bg, bw, bt, sh, price)

        # Fair value range
        ivs = [s["intrinsic_value"] for s in scenarios.values() if s["intrinsic_value"]]
        fv_low  = min(ivs) if ivs else None
        fv_high = max(ivs) if ivs else None
        fv_mid  = scenarios["BASE"]["intrinsic_value"]

        # Tornado
        tornado = _build_tornado(oe, bg, bw, bt, sh, price)

        base_s = scenarios["BASE"]
        bear_s = scenarios["BEAR"]
        rec    = _recommendation(base_s["margin_of_safety_pct"], bear_s["margin_of_safety_pct"])

        return {
            "symbol":        sym,
            "current_price": round(price, 2),
            "base_assumptions": {
                "owner_earnings_cr": round(oe, 2),
                "base_growth_pct":   round(bg * 100, 2),
                "base_wacc_pct":     round(bw * 100, 2),
                "terminal_growth_pct": round(bt * 100, 2),
                "shares_cr":         round(sh, 4),
            },
            "scenarios":    scenarios,
            "fair_value_range": {
                "low":  round(fv_low, 2) if fv_low else None,
                "mid":  round(fv_mid, 2) if fv_mid else None,
                "high": round(fv_high, 2) if fv_high else None,
            },
            "margin_of_safety": {
                "bull": scenarios["BULL"]["margin_of_safety_pct"],
                "base": scenarios["BASE"]["margin_of_safety_pct"],
                "bear": scenarios["BEAR"]["margin_of_safety_pct"],
            },
            "upside_pct": {
                "bull": scenarios["BULL"]["upside_pct"],
                "base": scenarios["BASE"]["upside_pct"],
                "bear": scenarios["BEAR"]["upside_pct"],
            },
            "tornado":        tornado,
            "recommendation": rec,
            "data_quality":   data_quality,
            "agent_name":     AGENT_NAME,
        }

    except Exception as exc:
        log.exception("valuation_scenarios(%s): unexpected error: %s", sym, exc)
        return {**empty, "error": str(exc)}


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    from dotenv import load_dotenv
    load_dotenv()
    sym = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    out = run_scenarios(sym)
    print(json.dumps(out, indent=2))
