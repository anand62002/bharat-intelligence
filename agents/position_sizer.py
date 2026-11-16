"""
agents/position_sizer.py — Position Sizing Output for Recommendations

Computes suggested_position_pct for each recommendation using a four-tier
Kelly-inspired sizing model calibrated to Indian equity risk.

Tier table (from EXECUTION_PLAN.md P3-A spec)
─────────────────────────────────────────────────────────────────────────────
 Tier      Position   Condition
─────────────────────────────────────────────────────────────────────────────
 FULL       5.00 %    MOS > 40%  AND  warren_score ≥ 70  AND  conf ≥ 75%
 HALF       2.50 %    MOS > 20%  AND  conf ≥ 65%
 QUARTER    1.25 %    MOS > 0%   AND  conf ≥ 55%
 AVOID      0.00 %    action=AVOID/SELL  OR  conf < 55%  OR  MOS ≤ 0%
─────────────────────────────────────────────────────────────────────────────

MOS source priority
  1. warren_bot margin_of_safety_pct (DCF-backed, most rigorous)
  2. upside_pct as proxy (broker target vs current price)
     — proxy cannot qualify for the FULL tier (quality gate requires DCF)

Usage
  from agents.position_sizer import calc_position_size

  result = calc_position_size(
      upside_pct   = 35.0,
      confidence   = 70.0,
      action       = "BUY",
      mos_pct      = 28.0,   # from warren_bot (optional)
      warren_score = 72,     # from warren_bot (optional)
  )
  # → {suggested_position_pct: 2.5, position_label: "Half position",
  #    position_tier: "HALF", sizing_rationale: "...", mos_source: "warren_dcf"}
"""

from __future__ import annotations

from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

POSITION_TIERS = {
    "FULL":    5.00,
    "HALF":    2.50,
    "QUARTER": 1.25,
    "AVOID":   0.00,
}

POSITION_LABELS = {
    "FULL":    "Full position (5%)",
    "HALF":    "Half position (2.5%)",
    "QUARTER": "Quarter position (1.25%)",
    "AVOID":   "Avoid (0%)",
}

# WP-019 tiers (size_from_probability). Labels start with the tier word so the
# paper book's allocation lookup (substring match) maps them unambiguously.

# Thresholds — kept as module constants so tests can reference them directly
FULL_MOS_MIN        = 40.0   # %
FULL_WARREN_MIN     = 70.0   # score /100
FULL_CONF_MIN       = 75.0   # %
HALF_MOS_MIN        = 20.0   # %
HALF_CONF_MIN       = 65.0   # %
QUARTER_MOS_MIN     = 0.0    # % (any positive upside)
QUARTER_CONF_MIN    = 55.0   # %

# Actions that force AVOID regardless of score
_AVOID_ACTIONS = {"AVOID", "SELL", "STRONG_SELL"}


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def calc_position_size(
    upside_pct:   float,
    confidence:   float,
    action:       str,
    mos_pct:      Optional[float] = None,
    warren_score: Optional[float] = None,
) -> dict:
    """
    Calculate suggested portfolio position size for a recommendation.

    Parameters
    ----------
    upside_pct   : Price-target upside % (always available from recommendations).
    confidence   : Agent confidence % (0-100).
    action       : Recommendation action string (BUY / HOLD / AVOID / SELL …).
    mos_pct      : Warren-bot DCF margin of safety % (optional — enables FULL tier).
    warren_score : Warren-bot composite quality score 0-100 (optional).

    Returns
    -------
    dict with keys:
        suggested_position_pct  float   — 0 / 1.25 / 2.5 / 5.0
        position_label          str     — human-readable tier description
        position_tier           str     — "FULL" | "HALF" | "QUARTER" | "AVOID"
        sizing_rationale        str     — one-line explanation
        mos_used                float   — MOS value that drove the decision
        mos_source              str     — "warren_dcf" | "upside_proxy"
    """
    action_upper = (action or "").strip().upper()

    # ── Hard override: AVOID / SELL actions ──────────────────────────────────
    if action_upper in _AVOID_ACTIONS:
        return _result(
            "AVOID",
            f"Action is {action_upper} — no position allocation.",
            mos_used   = mos_pct if mos_pct is not None else upside_pct,
            mos_source = "warren_dcf" if mos_pct is not None else "upside_proxy",
        )

    # ── Choose MOS ───────────────────────────────────────────────────────────
    # Warren-bot DCF MOS is preferred; fall back to upside_pct as proxy.
    use_warren_mos = mos_pct is not None
    effective_mos  = float(mos_pct) if use_warren_mos else float(upside_pct)
    mos_source     = "warren_dcf" if use_warren_mos else "upside_proxy"

    conf = float(confidence)

    # ── Tier FULL (5%) ───────────────────────────────────────────────────────
    # Requires DCF-backed MOS (proxy cannot qualify — quality gate).
    if (
        use_warren_mos
        and effective_mos  > FULL_MOS_MIN
        and warren_score is not None
        and float(warren_score) >= FULL_WARREN_MIN
        and conf >= FULL_CONF_MIN
    ):
        return _result(
            "FULL",
            (
                f"DCF MOS {effective_mos:.1f}% > {FULL_MOS_MIN}%, "
                f"Warren score {warren_score:.0f} ≥ {FULL_WARREN_MIN:.0f}, "
                f"confidence {conf:.0f}% ≥ {FULL_CONF_MIN:.0f}% — "
                "high-conviction long-term hold."
            ),
            mos_used   = effective_mos,
            mos_source = mos_source,
        )

    # ── Tier HALF (2.5%) ─────────────────────────────────────────────────────
    if effective_mos > HALF_MOS_MIN and conf >= HALF_CONF_MIN:
        return _result(
            "HALF",
            (
                f"{'DCF MOS' if use_warren_mos else 'Upside'} {effective_mos:.1f}% > {HALF_MOS_MIN}%, "
                f"confidence {conf:.0f}% ≥ {HALF_CONF_MIN:.0f}%."
            ),
            mos_used   = effective_mos,
            mos_source = mos_source,
        )

    # ── Tier QUARTER (1.25%) ─────────────────────────────────────────────────
    if effective_mos > QUARTER_MOS_MIN and conf >= QUARTER_CONF_MIN:
        return _result(
            "QUARTER",
            (
                f"{'DCF MOS' if use_warren_mos else 'Upside'} {effective_mos:.1f}% > 0%, "
                f"confidence {conf:.0f}% ≥ {QUARTER_CONF_MIN:.0f}%."
            ),
            mos_used   = effective_mos,
            mos_source = mos_source,
        )

    # ── Tier AVOID (0%) ──────────────────────────────────────────────────────
    reasons = []
    if conf < QUARTER_CONF_MIN:
        reasons.append(f"confidence {conf:.0f}% < {QUARTER_CONF_MIN:.0f}%")
    if effective_mos <= QUARTER_MOS_MIN:
        reasons.append(
            f"{'DCF MOS' if use_warren_mos else 'upside'} {effective_mos:.1f}% ≤ 0%"
        )
    return _result(
        "AVOID",
        "Sizing conditions not met — " + "; ".join(reasons) + ".",
        mos_used   = effective_mos,
        mos_source = mos_source,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Internal helper
# ─────────────────────────────────────────────────────────────────────────────

def _result(
    tier:       str,
    rationale:  str,
    mos_used:   float,
    mos_source: str,
) -> dict:
    return {
        "suggested_position_pct": POSITION_TIERS[tier],
        "position_label":         POSITION_LABELS[tier],
        "position_tier":          tier,
        "sizing_rationale":       rationale,
        "mos_used":               round(float(mos_used), 2),
        "mos_source":             mos_source,
    }


# ─────────────────────────────────────────────────────────────────────────────
# WP-019 (ADR-006): size from calibrated probability, conviction locked by default
# ─────────────────────────────────────────────────────────────────────────────
#
# Why: calc_position_size's confidence input was the raw screening score, which
# for every discovery call is ≥ 65 — so all 334 paper positions got the same
# HALF size, and the size said nothing about the evidence. Now size follows the
# calibrated band: BASE for every call until a band's 90 % Wilson lower bound on
# the beat-Nifty rate clears data.decision_config.SIZING_UNLOCK_LOWER_BOUND with
# ≥ SIZING_UNLOCK_MIN_N resolved calls; CONVICTION unlocks for that band only.
# calc_position_size stays for the legacy DECISION_SOURCE='llm' path.

PROB_LABELS = {
    "BASE":       "Base position (2.5%)",
    "CONVICTION": "Conviction position (5%)",
    "AVOID":      "Avoid (0%)",
}


def size_from_probability(
    hit_rate: Optional[float],
    band_n: Optional[int],
    lower_bound: Optional[float],
    action: str = "BUY",
) -> dict:
    """
    Position size from a calibrated band.

    hit_rate    — band's calibrated P(beat Nifty at 90 d), percent
    band_n      — resolved, independent calls in the band
    lower_bound — band's 90 % Wilson lower bound on the beat-Nifty rate, percent
    action      — SELL / AVOID / STRONG_SELL get 0 %

    Returns the same keys as calc_position_size (suggested_position_pct,
    position_label, position_tier, sizing_rationale) plus `conviction_locked`.
    """
    from data import decision_config as dc

    act = (action or "").strip().upper()
    n = int(band_n or 0)
    lb = float(lower_bound or 0.0)
    rate = float(hit_rate) if hit_rate is not None else None

    def out(tier: str, pct: float, why: str, locked: bool) -> dict:
        return {"suggested_position_pct": pct, "position_label": PROB_LABELS[tier],
                "position_tier": tier, "sizing_rationale": why, "conviction_locked": locked,
                "hit_rate": rate, "band_n": n, "lower_bound": lb}

    if act in _AVOID_ACTIONS:
        return out("AVOID", 0.0, f"Action is {act} — no position allocation.", True)

    if n >= dc.SIZING_UNLOCK_MIN_N and lb > dc.SIZING_UNLOCK_LOWER_BOUND:
        return out("CONVICTION", dc.CONVICTION_POSITION_PCT,
                   f"Conviction unlocked: this band's 90% lower bound {lb:.1f}% > "
                   f"{dc.SIZING_UNLOCK_LOWER_BOUND:.0f}% on n={n} resolved calls"
                   f" (calibrated hit rate {rate:.1f}%)." if rate is not None else
                   f"Conviction unlocked: lower bound {lb:.1f}% on n={n}.", False)

    if n < dc.SIZING_UNLOCK_MIN_N:
        why = (f"only {n} resolved calls in this band (need {dc.SIZING_UNLOCK_MIN_N})")
    else:
        why = (f"this band's 90% lower bound {lb:.1f}% does not exceed "
               f"{dc.SIZING_UNLOCK_LOWER_BOUND:.0f}%")
    return out("BASE", dc.BASE_POSITION_PCT,
               f"Conviction sizing locked — {why}. Every call gets base size until a band "
               f"proves an edge (ADR-006).", True)
