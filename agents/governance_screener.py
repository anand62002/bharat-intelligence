"""
agents/governance_screener.py — Corporate Governance Red Flag Screener
=======================================================================
Screens for 7 corporate governance risk factors that the existing agents
don't explicitly model. Returns a structured risk assessment used by the
orchestrator synthesise node to adjust the `risk_score` of recommendations.

The 7 Flags
-----------
1. PLEDGING_RISK      — Promoter pledging > 20% of their holding
2. HIGH_LEVERAGE      — Debt/Equity > 3.0x
3. AUDITOR_CHANGE     — Auditor changed within last 2 years (data permitting)
4. RELATED_PARTY_HIGH — Related-party transactions > 15% of revenue
5. PROMOTER_SELLING   — Promoter holding declining ≥ 5pp over 3 years
6. CONTINGENT_LIAB    — Contingent liabilities > 50% of net worth
7. NEGATIVE_CFO       — Operating cash flow negative ≥ 2 of last 3 years

Risk impact on orchestrator risk_score
---------------------------------------
  Each flag adds:
    CRITICAL flag (pledging >40%, leverage >5x, negative CFO 3/3yr) → +20
    HIGH flag (pledging 20–40%, leverage 3–5x, promoter selling 5–10pp) → +10
    MEDIUM flag (others) → +5
  risk_score is capped at 100.

Usage
-----
    from agents.governance_screener import screen_governance
    result = screen_governance("RELIANCE")
    # {
    #   symbol, flags: [{flag, level, detail}],
    #   flag_count, risk_score_delta, risk_level,
    #   clean: bool, agent_name
    # }

Standalone
----------
    python -m agents.governance_screener RELIANCE
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)
AGENT_NAME = "governance_screener"

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Risk delta per flag level
_DELTA = {"CRITICAL": 20, "HIGH": 10, "MEDIUM": 5}


def _safe(v, default=None):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _trend_change(values: list, lookback: int = 3) -> Optional[float]:
    """Change between first and last `lookback` values (positive = increasing)."""
    vals = [_safe(v) for v in values if v is not None]
    if len(vals) < 2:
        return None
    subset = vals[-lookback:]
    return float(subset[-1]) - float(subset[0]) if len(subset) >= 2 else None


# ──────────────────────────────────────────────────────────────────────────────
# Flag detectors
# ──────────────────────────────────────────────────────────────────────────────

def _flag_pledging(raw: dict) -> Optional[dict]:
    pledge = _safe(raw.get("promoter_pledging") or raw.get("pledged_pct"))
    if pledge is None or pledge <= 0:
        return None
    if pledge > 40:
        return {"flag": "PLEDGING_RISK", "level": "CRITICAL",
                "detail": f"Promoter pledging {pledge:.1f}% — extremely high risk"}
    if pledge > 20:
        return {"flag": "PLEDGING_RISK", "level": "HIGH",
                "detail": f"Promoter pledging {pledge:.1f}% — elevated risk"}
    return None


def _flag_leverage(raw: dict) -> Optional[dict]:
    de = _safe(raw.get("debt_equity"))
    if de is None:
        return None
    if de > 5:
        return {"flag": "HIGH_LEVERAGE", "level": "CRITICAL",
                "detail": f"D/E {de:.1f}x — dangerously leveraged"}
    if de > 3:
        return {"flag": "HIGH_LEVERAGE", "level": "HIGH",
                "detail": f"D/E {de:.1f}x — highly leveraged"}
    return None


def _flag_related_party(raw: dict) -> Optional[dict]:
    """Related-party transactions as % of revenue — proxy via raw screener if available."""
    rpt = _safe(raw.get("related_party_pct") or raw.get("rpt_pct"))
    if rpt is None:
        return None
    if rpt > 25:
        return {"flag": "RELATED_PARTY_HIGH", "level": "CRITICAL",
                "detail": f"Related-party transactions {rpt:.1f}% of revenue — very high"}
    if rpt > 15:
        return {"flag": "RELATED_PARTY_HIGH", "level": "HIGH",
                "detail": f"Related-party transactions {rpt:.1f}% of revenue — elevated"}
    return None


def _flag_promoter_selling(history: dict) -> Optional[dict]:
    ph_hist = history.get("promoter_holding") or []
    change = _trend_change(ph_hist, lookback=4)
    if change is None:
        return None
    if change < -10:
        return {"flag": "PROMOTER_SELLING", "level": "CRITICAL",
                "detail": f"Promoter stake dropped {abs(change):.1f}pp — significant exit"}
    if change < -5:
        return {"flag": "PROMOTER_SELLING", "level": "HIGH",
                "detail": f"Promoter stake dropped {abs(change):.1f}pp over 3+ years"}
    if change < -3:
        return {"flag": "PROMOTER_SELLING", "level": "MEDIUM",
                "detail": f"Promoter stake modestly declining ({change:.1f}pp)"}
    return None


def _flag_negative_cfo(history: dict, raw: dict) -> Optional[dict]:
    """
    Negative operating cash flow in recent years.
    Proxy: PAT minus capex as crude CFO proxy if direct CFO not available.
    """
    # Try direct CFO data first
    cfo_hist = history.get("cfo") or history.get("operating_cash_flow") or []

    if not cfo_hist:
        # Proxy: PAT - capex
        pat_hist   = history.get("pat")   or []
        capex_hist = history.get("capex") or []
        n = min(len(pat_hist), len(capex_hist))
        if n < 2:
            return None
        cfo_hist = [
            (_safe(p, 0) - abs(_safe(c, 0)))
            for p, c in zip(pat_hist[-n:], capex_hist[-n:])
        ]

    if len(cfo_hist) < 2:
        return None

    recent = cfo_hist[-3:]
    neg_count = sum(1 for c in recent if _safe(c, 0) < 0)

    if neg_count >= 3:
        return {"flag": "NEGATIVE_CFO", "level": "CRITICAL",
                "detail": f"Negative operating cash flow {neg_count}/3 recent years"}
    if neg_count >= 2:
        return {"flag": "NEGATIVE_CFO", "level": "HIGH",
                "detail": f"Negative operating cash flow {neg_count}/3 recent years"}
    return None


def _flag_contingent_liab(raw: dict) -> Optional[dict]:
    """Contingent liabilities > 50% of net worth."""
    cl      = _safe(raw.get("contingent_liabilities"))
    net_w   = _safe(raw.get("net_worth") or raw.get("book_value"))
    if cl is None or net_w is None or net_w <= 0:
        return None
    ratio = cl / net_w
    if ratio > 1.0:
        return {"flag": "CONTINGENT_LIAB", "level": "CRITICAL",
                "detail": f"Contingent liabilities {ratio:.1f}x net worth — very high off-balance risk"}
    if ratio > 0.5:
        return {"flag": "CONTINGENT_LIAB", "level": "HIGH",
                "detail": f"Contingent liabilities {ratio*100:.0f}% of net worth — elevated"}
    return None


def _flag_auditor_change(raw: dict) -> Optional[dict]:
    """Auditor changed recently — proxy via raw field if available."""
    changed = raw.get("auditor_changed") or raw.get("auditor_change_recent")
    if changed:
        return {"flag": "AUDITOR_CHANGE", "level": "MEDIUM",
                "detail": "Auditor changed within last 2 years — governance concern"}
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Risk level classification
# ──────────────────────────────────────────────────────────────────────────────

def _classify_risk(delta: int) -> str:
    if delta >= 30:
        return "HIGH_RISK"
    elif delta >= 15:
        return "MODERATE_RISK"
    elif delta > 0:
        return "LOW_RISK"
    return "CLEAN"


# ──────────────────────────────────────────────────────────────────────────────
# Main function
# ──────────────────────────────────────────────────────────────────────────────

def screen_governance(symbol: str) -> dict:
    """
    Run all 7 governance flag checks for `symbol`.

    Returns
    -------
    {
      symbol, flags, flag_count, risk_score_delta,
      risk_level, clean, agent_name
    }
    """
    plain = symbol.replace(".NS", "").replace(".BO", "").upper()

    raw:     dict = {}
    history: dict = {}
    try:
        from data.fetchers import get_screener_data, get_screener_history
        raw     = get_screener_data(plain)    or {}
        history = get_screener_history(plain) or {}
    except Exception as exc:
        log.debug("governance_screener(%s): data fetch failed: %s", plain, exc)

    # Run all detectors
    detectors = [
        _flag_pledging(raw),
        _flag_leverage(raw),
        _flag_related_party(raw),
        _flag_promoter_selling(history),
        _flag_negative_cfo(history, raw),
        _flag_contingent_liab(raw),
        _flag_auditor_change(raw),
    ]

    flags = [f for f in detectors if f is not None]

    # Compute risk_score delta
    delta = sum(_DELTA.get(f["level"], 5) for f in flags)

    return {
        "symbol":           plain,
        "flags":            flags,
        "flag_count":       len(flags),
        "risk_score_delta": min(delta, 60),   # cap total delta at 60
        "risk_level":       _classify_risk(delta),
        "clean":            len(flags) == 0,
        "agent_name":       AGENT_NAME,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Orchestrator hook — adjusts risk_score in recommendation row
# ──────────────────────────────────────────────────────────────────────────────

def adjust_risk_score(base_risk_score: float, gov_result: dict) -> float:
    """
    Apply governance risk delta to the base risk_score.
    Returns adjusted score capped at 100.
    """
    delta = gov_result.get("risk_score_delta", 0)
    return min(100.0, float(base_risk_score) + delta)


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    from dotenv import load_dotenv
    load_dotenv()
    sym = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    out = screen_governance(sym)
    print(json.dumps(out, indent=2))
