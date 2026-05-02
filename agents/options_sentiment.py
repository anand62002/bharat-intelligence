"""
agents/options_sentiment.py — Options Market Sentiment Agent
=============================================================
Interprets options market metrics (PCR, max pain, IV skew, VIX)
to derive a directional sentiment signal for an instrument.

Signal logic
------------
  STRONG_BULLISH  — Low PCR (<0.7) + low VIX + price above max pain
  BULLISH         — Moderate PCR (0.7–0.9) + favourable conditions
  NEUTRAL         — PCR 0.9–1.1 or mixed signals
  BEARISH         — PCR 1.1–1.5 or elevated VIX + price below max pain
  STRONG_BEARISH  — PCR > 1.5 or extreme VIX spike (> 25) + put skew

Score breakdown (0–100)
-----------------------
  PCR signal       : 0–35  (PCR < 0.7 → 35, PCR 1.5 → 0)
  Max-pain gap     : 0–25  (price > max_pain → positive)
  VIX regime       : 0–20  (VIX < 15 → 20, VIX > 25 → 0)
  IV skew          : 0–10  (negative skew = call-heavy → bullish)
  IV/HV ratio      : 0–10  (IV/HV < 1.0 → calm → bullish)

Usage
-----
    from agents.options_sentiment import analyse_options
    r = analyse_options("NIFTY")
    # {signal, score, pcr, max_pain, atm_iv, iv_skew,
    #  india_vix, hv20, iv_hv_ratio, commentary, agent_name}

Standalone
----------
    python -m agents.options_sentiment NIFTY
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)
AGENT_NAME = "options_sentiment"

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ──────────────────────────────────────────────────────────────────────────────
# Scoring helpers
# ──────────────────────────────────────────────────────────────────────────────

def _pcr_score(pcr: Optional[float]) -> tuple[int, str]:
    """Returns (score 0-35, comment)."""
    if pcr is None:
        return 17, "PCR data unavailable — neutral"
    if pcr < 0.7:
        return 35, f"PCR {pcr:.2f} — very low, strong call dominance (bullish)"
    if pcr < 0.9:
        return 27, f"PCR {pcr:.2f} — low, call-heavy (moderately bullish)"
    if pcr < 1.1:
        return 17, f"PCR {pcr:.2f} — balanced call/put OI (neutral)"
    if pcr < 1.5:
        return 8,  f"PCR {pcr:.2f} — elevated put OI (bearish bias)"
    return 0, f"PCR {pcr:.2f} — very high put dominance (strongly bearish)"


def _max_pain_score(
    max_pain: Optional[float],
    underlying: Optional[float],
) -> tuple[int, str]:
    """Returns (score 0-25, comment). Price above max pain → bullish."""
    if max_pain is None or underlying is None or max_pain <= 0:
        return 12, "Max pain data unavailable — neutral"
    gap_pct = (underlying - max_pain) / max_pain * 100
    if gap_pct > 3:
        return 25, f"Price {gap_pct:+.1f}% above max pain {max_pain:.0f} (bullish)"
    if gap_pct > 0:
        return 18, f"Price {gap_pct:+.1f}% above max pain (slightly bullish)"
    if gap_pct > -3:
        return 10, f"Price {gap_pct:+.1f}% near max pain (neutral-bearish)"
    return 2,  f"Price {gap_pct:+.1f}% below max pain {max_pain:.0f} (bearish)"


def _vix_score(india_vix: Optional[float]) -> tuple[int, str]:
    """Returns (score 0-20, comment). Low VIX → calm → bullish."""
    if india_vix is None:
        return 10, "India VIX unavailable — neutral"
    if india_vix < 13:
        return 20, f"India VIX {india_vix:.1f} — very low fear (bullish)"
    if india_vix < 18:
        return 15, f"India VIX {india_vix:.1f} — calm market (mildly bullish)"
    if india_vix < 22:
        return 10, f"India VIX {india_vix:.1f} — moderate volatility (neutral)"
    if india_vix < 28:
        return 4,  f"India VIX {india_vix:.1f} — elevated fear (bearish)"
    return 0, f"India VIX {india_vix:.1f} — extreme fear spike (strongly bearish)"


def _skew_score(iv_skew: Optional[float]) -> tuple[int, str]:
    """Returns (score 0-10, comment). Negative skew (call > put IV) → bullish."""
    if iv_skew is None:
        return 5, "IV skew unavailable — neutral"
    if iv_skew < -3:
        return 10, f"IV skew {iv_skew:+.1f}% — calls expensive vs puts (bullish)"
    if iv_skew < 0:
        return 7,  f"IV skew {iv_skew:+.1f}% — mild call premium (slightly bullish)"
    if iv_skew < 5:
        return 5,  f"IV skew {iv_skew:+.1f}% — near-neutral skew"
    return 1, f"IV skew {iv_skew:+.1f}% — heavy put premium (bearish)"


def _iv_hv_score(iv_hv_ratio: Optional[float]) -> tuple[int, str]:
    """Returns (score 0-10, comment). Low IV/HV → options cheap → bullish."""
    if iv_hv_ratio is None:
        return 5, "IV/HV ratio unavailable — neutral"
    if iv_hv_ratio < 0.8:
        return 10, f"IV/HV {iv_hv_ratio:.2f} — options cheap, calm vol regime (bullish)"
    if iv_hv_ratio < 1.2:
        return 7,  f"IV/HV {iv_hv_ratio:.2f} — normal vol premium (neutral)"
    if iv_hv_ratio < 1.5:
        return 3,  f"IV/HV {iv_hv_ratio:.2f} — elevated vol premium (mildly bearish)"
    return 0, f"IV/HV {iv_hv_ratio:.2f} — options very expensive, fear-driven (bearish)"


# ──────────────────────────────────────────────────────────────────────────────
# Signal classification
# ──────────────────────────────────────────────────────────────────────────────

def _classify_signal(score: int) -> str:
    if score >= 78:
        return "STRONG_BULLISH"
    if score >= 60:
        return "BULLISH"
    if score >= 42:
        return "NEUTRAL"
    if score >= 24:
        return "BEARISH"
    return "STRONG_BEARISH"


def _build_commentary(
    signal: str,
    pcr: Optional[float],
    india_vix: Optional[float],
    max_pain: Optional[float],
    underlying: Optional[float],
    source: str,
) -> str:
    parts = []
    if source == "fallback":
        parts.append("⚠ NSE option chain unavailable — signals estimated from India VIX + realized vol.")
    if pcr is not None:
        sentiment = "put-heavy" if pcr > 1.0 else "call-heavy"
        parts.append(f"PCR {pcr:.2f} ({sentiment}).")
    if india_vix is not None:
        parts.append(f"India VIX {india_vix:.1f}.")
    if max_pain is not None and underlying is not None:
        gap = underlying - max_pain
        parts.append(
            f"Max pain ₹{max_pain:,.0f} "
            f"({'above' if gap >= 0 else 'below'} spot by ₹{abs(gap):,.0f})."
        )
    if not parts:
        parts.append("Insufficient options data for detailed commentary.")
    return f"[{signal}] " + " ".join(parts)


# ──────────────────────────────────────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────────────────────────────────────

def analyse_options(symbol: str) -> dict:
    """
    Derive options market sentiment for `symbol`.

    Returns
    -------
    {
      symbol, signal, score,
      pcr, max_pain, atm_iv, iv_skew,
      india_vix, hv20, iv_hv_ratio,
      underlying_price, source, commentary, agent_name
    }
    """
    sym = symbol.upper().replace(".NS", "").replace(".BO", "")

    # Always return a result — never raises
    empty_result = {
        "symbol":           sym,
        "signal":           "NO_DATA",
        "score":            None,
        "pcr":              None,
        "max_pain":         None,
        "atm_iv":           None,
        "iv_skew":          None,
        "india_vix":        None,
        "hv20":             None,
        "iv_hv_ratio":      None,
        "underlying_price": None,
        "source":           "none",
        "commentary":       "No options data available.",
        "agent_name":       AGENT_NAME,
    }

    try:
        from data.options_fetcher import get_option_metrics
        metrics = get_option_metrics(sym)
    except Exception as exc:
        log.warning("options_sentiment(%s): fetcher error: %s", sym, exc)
        return empty_result

    if metrics.get("error"):
        log.debug("options_sentiment(%s): fallback error: %s", sym, metrics["error"])
        return empty_result

    pcr           = metrics.get("pcr")
    max_pain      = metrics.get("max_pain")
    atm_iv        = metrics.get("atm_iv")
    iv_skew       = metrics.get("iv_skew")
    india_vix     = metrics.get("india_vix")
    hv20          = metrics.get("hv20")
    iv_hv_ratio   = metrics.get("iv_hv_ratio")
    underlying    = metrics.get("underlying_price")
    source        = metrics.get("source", "unknown")

    # Score each dimension
    s_pcr,   n_pcr   = _pcr_score(pcr)
    s_pain,  n_pain  = _max_pain_score(max_pain, underlying)
    s_vix,   n_vix   = _vix_score(india_vix)
    s_skew,  n_skew  = _skew_score(iv_skew)
    s_ivhv,  n_ivhv  = _iv_hv_score(iv_hv_ratio)

    total_score = s_pcr + s_pain + s_vix + s_skew + s_ivhv   # max = 100
    signal      = _classify_signal(total_score)
    commentary  = _build_commentary(signal, pcr, india_vix, max_pain, underlying, source)

    return {
        "symbol":           sym,
        "signal":           signal,
        "score":            total_score,
        "pcr":              pcr,
        "max_pain":         max_pain,
        "atm_iv":           atm_iv,
        "iv_skew":          iv_skew,
        "india_vix":        india_vix,
        "hv20":             hv20,
        "iv_hv_ratio":      iv_hv_ratio,
        "underlying_price": underlying,
        "source":           source,
        "commentary":       commentary,
        "score_breakdown": {
            "pcr_score":       s_pcr,
            "max_pain_score":  s_pain,
            "vix_score":       s_vix,
            "skew_score":      s_skew,
            "iv_hv_score":     s_ivhv,
        },
        "score_notes": {
            "pcr":      n_pcr,
            "max_pain": n_pain,
            "vix":      n_vix,
            "skew":     n_skew,
            "iv_hv":    n_ivhv,
        },
        "agent_name": AGENT_NAME,
    }


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    from dotenv import load_dotenv
    load_dotenv()
    sym = sys.argv[1] if len(sys.argv) > 1 else "NIFTY"
    out = analyse_options(sym)
    print(json.dumps(out, indent=2))
