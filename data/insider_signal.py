"""
data/insider_signal.py — Promoter / Insider Signal Module
==========================================================
Computes promoter buying/selling signal from historical holding trend data.
Used by both sentiment.py (P3-C-P5) and institutional.py (P3-C-P6).

Data strategy (tries in order):
  1. get_screener_history(symbol) — 10-yr annual series incl. promoter holding
  2. get_screener_data(symbol)    — snapshot promoter_holding (no trend)
  3. get_trendlyne_fundamentals() — snapshot promoter_holding (no trend)

If only a snapshot is available (no history), returns NEUTRAL to avoid
false signals from a single data point.

Signal definitions:
  ACCUMULATING — promoter holding increased ≥1 pp over last 1 year
                 OR ≥2 pp over last 3 years  (consistent buying pressure)
  DISTRIBUTING — promoter holding decreased ≥2 pp over last 1 year
                 OR ≥5 pp over last 3 years  (sustained distribution)
  NEUTRAL      — change within noise band, or insufficient history

Public API:
  get_promoter_signal(symbol) -> dict
      Returns {signal, current_holding, change_1y, change_3y, source, note}
"""

from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)

# Module-level imports so tests can patch data.insider_signal.get_screener_history etc.
# Lazy try/except protects against circular imports in unusual load orders.
try:
    from data.fetchers import get_screener_history, get_screener_data
except ImportError:  # pragma: no cover
    get_screener_history = None  # type: ignore[assignment]
    get_screener_data    = None  # type: ignore[assignment]

# Thresholds (percentage point changes)
_ACCUM_1Y_PP  =  1.0   # ≥1 pp increase over 1 year → ACCUMULATING
_ACCUM_3Y_PP  =  2.0   # ≥2 pp increase over 3 years → ACCUMULATING
_DISTRIB_1Y_PP = -2.0  # ≥2 pp decrease over 1 year → DISTRIBUTING
_DISTRIB_3Y_PP = -5.0  # ≥5 pp decrease over 3 years → DISTRIBUTING


def get_promoter_signal(symbol: str) -> dict:
    """
    Return promoter holding trend signal for `symbol`.

    Returns:
        {
            signal:          "ACCUMULATING" | "DISTRIBUTING" | "NEUTRAL"
            current_holding: float | None    — most recent promoter holding %
            change_1y:       float | None    — pp change vs 1 year ago
            change_3y:       float | None    — pp change vs 3 years ago
            source:          str             — "screener_history" | "screener_snapshot" | "trendlyne_snapshot" | "none"
            note:            str             — human-readable summary
        }
    """
    clean = symbol.replace(".NS", "").replace(".BO", "").upper()

    _result: dict = {
        "signal":          "NEUTRAL",
        "current_holding": None,
        "change_1y":       None,
        "change_3y":       None,
        "source":          "none",
        "note":            "No promoter holding data available",
    }

    # ── Strategy 1: screener.in annual history ────────────────────────────────
    try:
        history = get_screener_history(clean)  # type: ignore[misc]
        if history:
            # history is a dict of lists: {"promoter_holding": [v1, v2, ...]}
            # Values are ordered oldest-first (index 0 = oldest year)
            ph_series = history.get("promoter_holding") or []
            # Filter out None values
            ph_valid = [(i, v) for i, v in enumerate(ph_series) if v is not None]
            if len(ph_valid) >= 2:
                _result["source"] = "screener_history"
                current_idx, current_val = ph_valid[-1]
                _result["current_holding"] = round(float(current_val), 2)

                # 1-year change: compare last two data points
                prev_1y_val = float(ph_valid[-2][1])
                change_1y = round(current_val - prev_1y_val, 2)
                _result["change_1y"] = change_1y

                # 3-year change: compare last to the one 3 positions back (if available)
                if len(ph_valid) >= 4:
                    prev_3y_val = float(ph_valid[-4][1])
                    change_3y = round(current_val - prev_3y_val, 2)
                    _result["change_3y"] = change_3y
                else:
                    change_3y = None

                # Determine signal
                signal = _classify(change_1y, change_3y)
                _result["signal"] = signal
                _result["note"] = _build_note(
                    signal, _result["current_holding"], change_1y, change_3y
                )
                log.debug(
                    "insider_signal(%s): %s (holding=%.1f%% Δ1y=%.1f Δ3y=%s) [history]",
                    clean, signal, current_val, change_1y,
                    f"{change_3y:.1f}" if change_3y is not None else "N/A",
                )
                return _result
    except Exception as exc:
        log.debug("insider_signal(%s): screener_history failed: %s", clean, exc)

    # ── Strategy 2: screener.in snapshot (no trend — NEUTRAL only) ────────────
    try:
        snap = get_screener_data(clean)  # type: ignore[misc]
        if snap and snap.get("promoter_holding") is not None:
            _result["current_holding"] = round(float(snap["promoter_holding"]), 2)
            _result["source"] = "screener_snapshot"
            _result["note"] = (
                f"Promoter holding {_result['current_holding']:.1f}% (snapshot, no trend data)"
            )
            log.debug(
                "insider_signal(%s): NEUTRAL (snapshot only, holding=%.1f%%)",
                clean, _result["current_holding"],
            )
            return _result
    except Exception as exc:
        log.debug("insider_signal(%s): screener_snapshot failed: %s", clean, exc)

    # ── Strategy 3: Trendlyne snapshot ────────────────────────────────────────
    try:
        import os
        if os.getenv("TRENDLYNE_SESSION"):
            from data.trendlyne_fetcher import get_trendlyne_fundamentals
            tl = get_trendlyne_fundamentals(clean)
            if tl and tl.get("promoter_holding") is not None:
                _result["current_holding"] = round(float(tl["promoter_holding"]), 2)
                _result["source"] = "trendlyne_snapshot"
                _result["note"] = (
                    f"Promoter holding {_result['current_holding']:.1f}% "
                    "(Trendlyne snapshot, no trend data)"
                )
                log.debug(
                    "insider_signal(%s): NEUTRAL (trendlyne snapshot, holding=%.1f%%)",
                    clean, _result["current_holding"],
                )
                return _result
    except Exception as exc:
        log.debug("insider_signal(%s): trendlyne_snapshot failed: %s", clean, exc)

    # No data available
    log.debug("insider_signal(%s): no promoter data found", clean)
    return _result


def _classify(change_1y: Optional[float], change_3y: Optional[float]) -> str:
    """
    Classify the promoter holding trend as ACCUMULATING / DISTRIBUTING / NEUTRAL.
    Uses conservative thresholds to avoid false signals.
    """
    if change_1y is None:
        return "NEUTRAL"

    # Distribution: significant selling in 1 year OR sustained 3-year selling
    if change_1y <= _DISTRIB_1Y_PP:
        return "DISTRIBUTING"
    if change_3y is not None and change_3y <= _DISTRIB_3Y_PP:
        return "DISTRIBUTING"

    # Accumulation: meaningful buying in 1 year OR consistent 3-year buying
    if change_1y >= _ACCUM_1Y_PP:
        return "ACCUMULATING"
    if change_3y is not None and change_3y >= _ACCUM_3Y_PP:
        return "ACCUMULATING"

    return "NEUTRAL"


def _build_note(
    signal: str,
    current: Optional[float],
    change_1y: Optional[float],
    change_3y: Optional[float],
) -> str:
    parts = []
    if current is not None:
        parts.append(f"holding {current:.1f}%")
    if change_1y is not None:
        direction = "↑" if change_1y > 0 else "↓"
        parts.append(f"Δ1y {direction}{abs(change_1y):.1f}pp")
    if change_3y is not None:
        direction = "↑" if change_3y > 0 else "↓"
        parts.append(f"Δ3y {direction}{abs(change_3y):.1f}pp")

    detail = ", ".join(parts) if parts else "data limited"
    label = {"ACCUMULATING": "Promoter buying", "DISTRIBUTING": "Promoter selling",
             "NEUTRAL": "Promoter holding stable"}[signal]
    return f"{label} — {detail}"
