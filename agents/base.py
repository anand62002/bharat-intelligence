"""
agents/base.py — Bharat Intelligence: Data Completeness Validator
=================================================================
DataCompletenessValidator enforces minimum data-quality standards before any
agent generates a trading signal.  It catches the class of hallucination where
an agent receives partial or None data from a fetcher (network error, scraping
failure, newly-listed company with sparse history) and still produces a
confident BUY/SELL.

Every agent builds a lightweight "snapshot" dict from its fetched data, then
calls validator.validate(snapshot, agent_name).  When critical fields are
absent or below quality thresholds the agent returns an INSUFFICIENT_DATA
signal instead of guessing.

Signal: "INSUFFICIENT_DATA"
    completeness_score   int   0-100 data quality score
    missing_fields       list  labels of absent fields
    below_threshold_fields list labels of present-but-low-quality fields

Usage
-----
    from agents.base import DataCompletenessValidator, insufficient_data_result

    validator = DataCompletenessValidator()

    snapshot = {
        "pe":             22.3,
        "revenue_growth": 15.0,
        "roce":           None,   # fetcher returned nothing — critical field
        "debt_equity":    0.4,
        "ebitda_margin":  18.5,
    }
    result = validator.validate(snapshot, "fundamental")
    if not result.is_sufficient:
        return insufficient_data_result("fundamental", result,
                                        upside_pct=None,
                                        danger_drop_pct=None,
                                        danger_confidence=0.0)

Per-agent field specs
---------------------
Each spec entry has:
    name        — key the agent puts in its snapshot dict
    label       — human-readable field name (shown in error messages)
    critical    — True → absent value blocks the agent regardless of overall score
    check(v)    — returns True when the value meets the quality bar
    weight      — contribution to the 0-100 completeness score
                  (auto-set: 20 for critical, 10 for non-critical if weight=0)
    description — brief note on why the field matters

Scoring
-------
    earned   = sum(weight × credit)   where credit = 1.0 (pass) | 0.5 (below threshold) | 0.0 (absent)
    possible = sum(weight)
    completeness_score = round(earned / possible × 100)

is_sufficient = True  when:
    • no critical fields are absent (value is not None AND the field key is present)
    • completeness_score >= MIN_COMPLETENESS_SCORE
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
INSUFFICIENT_DATA       = "INSUFFICIENT_DATA"
MIN_COMPLETENESS_SCORE  = 40   # below this → INSUFFICIENT_DATA even if no critical field is missing
_DEFAULT_CRITICAL_WEIGHT = 20
_DEFAULT_OPTIONAL_WEIGHT = 10


# ──────────────────────────────────────────────────────────────────────────────
# FieldSpec
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class FieldSpec:
    """
    One data-quality requirement for an agent.

    Attributes
    ----------
    name        Key the agent stores in its snapshot dict.
    label       Human-readable name used in error / audit messages.
    critical    When True, a missing/None value makes is_sufficient=False
                regardless of the overall completeness score.
    check       Callable(value) → bool.  Receives the raw value from the
                snapshot dict.  Should never raise — any exception is caught
                and treated as a threshold failure (not a missing value).
    weight      Contribution to completeness_score (0 = auto from critical).
    description Short note explaining why the field matters.
    """
    name:        str
    label:       str
    critical:    bool
    check:       Callable[[Any], bool]
    weight:      int  = 0
    description: str  = ""

    def effective_weight(self) -> int:
        if self.weight > 0:
            return self.weight
        return _DEFAULT_CRITICAL_WEIGHT if self.critical else _DEFAULT_OPTIONAL_WEIGHT


# ──────────────────────────────────────────────────────────────────────────────
# ValidationResult
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    """
    Returned by DataCompletenessValidator.validate().

    Attributes
    ----------
    is_sufficient               True → agent should proceed with analysis.
    completeness_score          0-100 data quality score.
    critical_missing            Critical fields whose value is absent (None or key not in snapshot).
    critical_below_threshold    Critical fields that are present but fail their quality check.
                                These also block analysis — an agent cannot run reliably when a
                                critical field is present but unusable (e.g. 10 OHLCV rows for
                                an indicator that needs 50+).
    missing_fields              Non-critical fields whose value is absent.
    below_threshold_fields      Non-critical fields present but not passing their check().
    warnings                    Advisory messages (non-blocking).
    """
    is_sufficient:               bool
    completeness_score:          int
    critical_missing:            list[str] = field(default_factory=list)
    critical_below_threshold:    list[str] = field(default_factory=list)
    missing_fields:              list[str] = field(default_factory=list)
    below_threshold_fields:      list[str] = field(default_factory=list)
    warnings:                    list[str] = field(default_factory=list)

    @property
    def all_missing(self) -> list[str]:
        """All fields that blocked analysis: absent critical + below-threshold critical."""
        return self.critical_missing + self.critical_below_threshold + self.missing_fields

    def summary(self) -> str:
        parts = [f"completeness={self.completeness_score}%"]
        if self.critical_missing:
            parts.append(f"critical_missing=[{', '.join(self.critical_missing)}]")
        if self.critical_below_threshold:
            parts.append(f"critical_below_threshold=[{', '.join(self.critical_below_threshold)}]")
        if self.missing_fields:
            parts.append(f"missing=[{', '.join(self.missing_fields)}]")
        if self.below_threshold_fields:
            parts.append(f"below_threshold=[{', '.join(self.below_threshold_fields)}]")
        return "  ".join(parts)


# ──────────────────────────────────────────────────────────────────────────────
# Per-agent field specifications
# ──────────────────────────────────────────────────────────────────────────────

def _safe_check(check_fn: Callable[[Any], bool], value: Any) -> bool:
    """Run check_fn(value) without raising — exceptions return False."""
    try:
        return bool(check_fn(value))
    except Exception:
        return False


def _is_positive(v: Any) -> bool:
    return v is not None and float(v) > 0


def _is_not_none(v: Any) -> bool:
    return v is not None


def _is_nonneg(v: Any) -> bool:
    return v is not None and float(v) >= 0


def _min_int(minimum: int) -> Callable[[Any], bool]:
    return lambda v: isinstance(v, (int, float)) and int(v) >= minimum


def _in_set(*values) -> Callable[[Any], bool]:
    return lambda v: v in values


# Agent-level field registry
AGENT_FIELD_SPECS: dict[str, list[FieldSpec]] = {

    # ── technical ─────────────────────────────────────────────────────────────
    # Needs raw OHLCV rows.  Snapshot keys are populated by the agent after
    # get_ohlcv() returns.
    "technical": [
        FieldSpec("ohlcv_rows",   "OHLCV row count",       critical=True,
                  check=_min_int(50),
                  description="≥50 bars needed for EMA-50, MACD, RSI"),
        FieldSpec("close",        "Current close price",   critical=True,
                  check=_is_positive,
                  description="Live market price required for signal generation"),
        FieldSpec("volume_avg",   "Average daily volume",  critical=True,
                  check=lambda v: v is not None and float(v) > 1_000,
                  description="OBV and volume confirmation require traded volume"),
        FieldSpec("has_volume",   "Volume column present", critical=False,
                  check=lambda v: bool(v),
                  description="Volume column present in OHLCV DataFrame"),
        FieldSpec("ema200_rows",  "EMA-200 availability",  critical=False,
                  check=_min_int(200),
                  description="≥200 bars for full trend-alignment scoring"),
    ],

    # ── fundamental ───────────────────────────────────────────────────────────
    # Needs screener.in snapshot (get_screener_data).
    "fundamental": [
        FieldSpec("pe",               "PE ratio",            critical=True,
                  check=_is_positive,
                  description="Core valuation anchor"),
        FieldSpec("revenue_growth",   "Revenue growth %",    critical=True,
                  check=_is_not_none,
                  description="Growth trajectory (can be negative)"),
        FieldSpec("roce",             "ROCE %",              critical=True,
                  check=_is_not_none,
                  description="Capital efficiency — core quality metric"),
        FieldSpec("debt_equity",      "Debt/Equity",         critical=True,
                  check=_is_nonneg,
                  description="Balance sheet health check"),
        FieldSpec("ebitda_margin",    "EBITDA margin %",     critical=False,
                  check=_is_not_none,
                  description="Operating profitability"),
        FieldSpec("promoter_holding", "Promoter holding %",  critical=False,
                  check=lambda v: v is not None and float(v) >= 0,
                  description="Promoter skin-in-the-game gauge"),
        FieldSpec("eps_cagr_3y",      "EPS CAGR 3-yr",       critical=False,
                  check=_is_not_none,
                  description="Earnings consistency signal"),
    ],

    # ── sentiment ─────────────────────────────────────────────────────────────
    # Primary signal: news headlines (critical).
    # FII data enriches danger detection but is NOT critical — NSE/BSE scraping
    # can fail on holidays or after API changes.  Sentiment falls back gracefully
    # to a news-only signal when FII is unavailable.
    "sentiment": [
        FieldSpec("headline_count",  "News headline count",  critical=True,
                  check=_min_int(1),
                  description="At least 1 headline required for sentiment"),
        FieldSpec("fii_net",         "FII net flow (₹Cr)",   critical=False,
                  check=_is_not_none,
                  description="Institutional flow enrichment (optional — news-only fallback when absent)"),
        FieldSpec("min_headlines",   "Sufficient headlines", critical=False,
                  check=_min_int(3),
                  description="≥3 headlines for a robust average score"),
    ],

    # ── macro ─────────────────────────────────────────────────────────────────
    # Needs global macro indicators (FRED + RBI + yfinance).
    "macro": [
        FieldSpec("indicators_available", "Macro indicator count", critical=True,
                  check=_min_int(2),
                  description="≥2 of [US10Y, DXY, VIX, India VIX, INR/USD, RBI repo] required"),
        FieldSpec("inr_usd",         "INR/USD rate",         critical=False,
                  check=_is_positive,
                  description="Currency impact on equity returns"),
        FieldSpec("india_vix",       "India VIX",            critical=False,
                  check=_is_not_none,
                  description="Domestic market fear gauge"),
    ],

    # ── institutional ─────────────────────────────────────────────────────────
    # Needs FII/DII flow data.
    "institutional": [
        FieldSpec("fii_net",      "FII net flow (₹Cr)",  critical=True,
                  check=_is_not_none,
                  description="Primary institutional flow signal"),
        FieldSpec("dii_net",      "DII net flow (₹Cr)",  critical=False,
                  check=_is_not_none,
                  description="Domestic institutional confirmation"),
        FieldSpec("data_quality", "Data quality tier",   critical=False,
                  check=_in_set("FULL", "PARTIAL"),
                  description="FULL or PARTIAL — not NO_DATA"),
    ],

    # ── commodities ───────────────────────────────────────────────────────────
    # Needs at least one commodity price (gold/crude/silver).
    "commodities": [
        FieldSpec("commodities_fetched", "Commodity prices fetched", critical=True,
                  check=_min_int(1),
                  description="At least one of gold/crude/silver price required"),
        FieldSpec("inr_usd",       "INR/USD rate",          critical=False,
                  check=_is_positive,
                  description="Needed for USD→INR commodity price conversion"),
    ],

    # ── historical_rag ────────────────────────────────────────────────────────
    # Needs a non-trivial market description + DB connectivity.
    "historical_rag": [
        FieldSpec("market_description_len", "Query description length", critical=True,
                  check=_min_int(10),
                  description="≥10 characters for a meaningful similarity search"),
        FieldSpec("db_available",   "Vector DB available",  critical=True,
                  check=lambda v: bool(v),
                  description="pgvector / Supabase connection required for RAG"),
    ],

    # ── warren_bot ────────────────────────────────────────────────────────────
    # Needs screener.in snapshot + multi-year history.
    "warren_bot": [
        FieldSpec("pe",              "PE ratio",            critical=True,
                  check=_is_positive,
                  description="Valuation anchor for MOS calculation"),
        FieldSpec("years_available", "Years of history",    critical=True,
                  check=_min_int(3),
                  description="≥3 years required to compute CAGR trends"),
        FieldSpec("revenue_history", "Revenue data points", critical=True,
                  check=_min_int(3),
                  description="Multi-year revenue needed for quality scoring"),
        FieldSpec("roce_history",    "ROCE data points",    critical=False,
                  check=_min_int(3),
                  description="≥3 years for capital-efficiency trend"),
        FieldSpec("current_price",   "Current market price",critical=False,
                  check=_is_positive,
                  description="Needed for margin-of-safety calculation"),
        FieldSpec("market_cap",      "Market cap (₹Cr)",    critical=False,
                  check=lambda v: v is not None and float(v) >= 200,
                  description="Min ₹200 Cr to pass quality filter"),
    ],

    # ── discovery_screener ────────────────────────────────────────────────────
    # Lightweight OHLCV check used in the pre-screen loop.
    "discovery_screener": [
        FieldSpec("symbol",     "Stock symbol",        critical=True,
                  check=lambda v: isinstance(v, str) and len(v) > 0,
                  description="Valid NSE symbol required"),
        FieldSpec("ohlcv_rows", "OHLCV row count",     critical=True,
                  check=_min_int(20),
                  description="≥20 bars for basic momentum pre-screen"),
        FieldSpec("close",      "Current close price", critical=True,
                  check=_is_positive,
                  description="Live price required for pre-screen filters"),
        FieldSpec("volume_avg", "Avg daily volume",    critical=False,
                  check=lambda v: v is not None and float(v) > 500,
                  description="Minimum liquidity check"),
    ],
}


# ──────────────────────────────────────────────────────────────────────────────
# Core validator
# ──────────────────────────────────────────────────────────────────────────────

class DataCompletenessValidator:
    """
    Validates an agent's data snapshot against per-agent field specifications.

    Thread-safe — all state is in local variables; no instance state is mutated.

    Example
    -------
    >>> v = DataCompletenessValidator()
    >>> r = v.validate({"pe": 22.3, "revenue_growth": None, "roce": 18.0,
    ...                 "debt_equity": 0.5}, "fundamental")
    >>> r.is_sufficient
    False
    >>> r.critical_missing
    ['Revenue growth %']
    >>> r.completeness_score
    62
    """

    def validate(
        self,
        snapshot:   dict[str, Any],
        agent_name: str,
    ) -> ValidationResult:
        """
        Validate *snapshot* against the field specs for *agent_name*.

        Parameters
        ----------
        snapshot    Dict mapping field names (as defined in AGENT_FIELD_SPECS)
                    to their fetched values.  None = field was not retrievable.
        agent_name  One of: technical, fundamental, sentiment, macro,
                    institutional, commodities, historical_rag, warren_bot,
                    discovery_screener.

        Returns
        -------
        ValidationResult with is_sufficient, completeness_score, and lists of
        missing / below-threshold fields.
        """
        specs = AGENT_FIELD_SPECS.get(agent_name)
        if not specs:
            log.warning(
                "DataCompletenessValidator: no spec for agent '%s' — passing through",
                agent_name,
            )
            return ValidationResult(is_sufficient=True, completeness_score=100)

        earned                    = 0
        total_possible            = 0
        critical_missing:            list[str] = []
        critical_below_threshold:    list[str] = []
        missing_fields:              list[str] = []
        below_threshold_fields:      list[str] = []
        warnings:                    list[str] = []

        for spec in specs:
            w     = spec.effective_weight()
            total_possible += w

            value = snapshot.get(spec.name)   # None if key absent OR explicitly None

            if value is None:
                # Field is absent
                if spec.critical:
                    critical_missing.append(spec.label)
                else:
                    missing_fields.append(spec.label)
                # earned += 0  (implicit)
                continue

            # Field is present — run quality check
            if _safe_check(spec.check, value):
                earned += w                   # full credit
            else:
                # Present but below quality threshold
                if spec.critical:
                    critical_below_threshold.append(spec.label)   # blocks analysis
                else:
                    below_threshold_fields.append(spec.label)     # reduces score only
                earned += w // 2              # partial credit for present-but-low-quality

        completeness_score = (
            round(earned / total_possible * 100)
            if total_possible > 0
            else 100
        )
        completeness_score = max(0, min(100, completeness_score))

        is_sufficient = (
            len(critical_missing) == 0
            and len(critical_below_threshold) == 0
            and completeness_score >= MIN_COMPLETENESS_SCORE
        )

        result = ValidationResult(
            is_sufficient             = is_sufficient,
            completeness_score        = completeness_score,
            critical_missing          = critical_missing,
            critical_below_threshold  = critical_below_threshold,
            missing_fields            = missing_fields,
            below_threshold_fields    = below_threshold_fields,
            warnings                  = warnings,
        )

        if not is_sufficient:
            log.warning(
                "[%s] DataCompletenessValidator: INSUFFICIENT_DATA — %s",
                agent_name, result.summary(),
            )
        else:
            log.debug(
                "[%s] DataCompletenessValidator: OK — %s",
                agent_name, result.summary(),
            )

        return result


# ──────────────────────────────────────────────────────────────────────────────
# Helper: build the standard INSUFFICIENT_DATA return dict
# ──────────────────────────────────────────────────────────────────────────────

def insufficient_data_result(
    agent_name:   str,
    result:       ValidationResult,
    data_sources: Optional[list[str]] = None,
    **extra_fields: Any,
) -> dict:
    """
    Build the standard return dict agents use when data quality is too low.

    The dict is compatible with the orchestrator's _composite_score() (score=None
    means this agent is excluded from the weighted average) and
    _format_agent_outputs() (INSUFFICIENT_DATA signal is rendered as a warning).

    Parameters
    ----------
    agent_name      Name of the calling agent (e.g. "fundamental").
    result          ValidationResult from DataCompletenessValidator.validate().
    data_sources    List of data feed strings attempted (may be empty).
    **extra_fields  Agent-specific fields to merge in (e.g. upside_pct=None,
                    danger_drop_pct=None, danger_confidence=0.0 for fundamental).

    Returns
    -------
    Dict with signal="INSUFFICIENT_DATA", score=None, completeness_score,
    missing_fields, below_threshold_fields, reason, data_sources, agent_name,
    confidence=0.0, detail, plus any extra_fields provided.
    """
    if result.critical_missing:
        reason = (
            f"Critical data missing: {', '.join(result.critical_missing)}"
        )
    elif result.critical_below_threshold:
        reason = (
            f"Critical data below quality threshold: {', '.join(result.critical_below_threshold)}"
        )
    elif result.completeness_score < MIN_COMPLETENESS_SCORE:
        reason = (
            f"Data quality too low — completeness score {result.completeness_score}% "
            f"(minimum {MIN_COMPLETENESS_SCORE}%)"
        )
    else:
        reason = "Insufficient data quality for reliable signal generation"

    base: dict[str, Any] = {
        "signal":                  INSUFFICIENT_DATA,
        "score":                   None,      # excluded from composite weighted average
        "completeness_score":      result.completeness_score,
        "missing_fields":          result.all_missing,
        "below_threshold_fields":  result.below_threshold_fields,
        "reason":                  reason,
        "data_sources":            data_sources or [],
        "agent_name":              agent_name,
        "confidence":              0.0,
        "detail": {
            "error":               reason,
            "completeness_score":  result.completeness_score,
            "missing_fields":      result.all_missing,
            "below_threshold":     result.below_threshold_fields,
        },
    }
    base.update(extra_fields)
    return base
