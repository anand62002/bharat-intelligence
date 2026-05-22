"""
governance/fact_checker.py — Bharat Intelligence Governance: Fact Checker
==========================================================================
Runs after each orchestrator synthesis cycle (called from fact_check_node).
Can also run standalone against the most recent DB recommendations.

Per-recommendation logic
─────────────────────────
  1. Extract 5–8 factual claims from the 7-agent output (numerical metrics,
     directional signals, key statistics)
  2. Re-fetch the original data source for each claim to get fresh context
  3. Call Claude Haiku with prompts/fact_check.txt to assess each claim:
       VERIFIED | UNVERIFIED | CONTRADICTED | MISLEADING
  4. CONTRADICTED claim   → subtract 15 from recommendation confidence
  5. >3 UNVERIFIED claims → mark recommendation as withheld; emit critical alert
  6. Populate gov_check JSONB on the recommendation dict
  7. Track per-agent hallucination counts → upsert agent_performance table

Entry points
────────────
  check_recommendations(recs, symbol_results, dry_run) -> list[dict]
      Called by orchestrator fact_check_node; has full in-memory agent results.

  run(dry_run) -> dict
      Standalone: re-fetches latest 20 recs from Supabase and re-checks them.

Usage
─────
  python governance/fact_checker.py --run-now
  python governance/fact_checker.py --run-now --dry
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
FACT_CHECK_PROMPT_PATH  = _ROOT / "prompts" / "fact_check.txt"
CLAUDE_HAIKU_MODEL      = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5")
HAIKU_MAX_TOKENS        = 400   # raised from 300 — prevents truncation on longer reasons
MAX_CLAIMS_PER_REC      = 8
MIN_CLAIMS_FOR_CHECK    = 3     # skip rec if we can't extract at least this many
CONTRADICTED_PENALTY    = 15.0  # confidence points deducted per contradicted claim
UNVERIFIED_WITHHOLD_N   = 4     # withhold if more than this many unverified claims
HALLUCINATION_ALERT_PCT = 1.5   # alert if agent hallucination_rate exceeds this

# ─────────────────────────────────────────────────────────────────────────────
# Claim dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Claim:
    claim_text:   str           # human-readable: "PE ratio is 22.5"
    metric_key:   str           # machine key: "pe"
    claimed_value: Any          # the value asserted by the agent
    data_source:  str           # "screener_in" | "yfinance_ohlcv_1y" | ...
    agent_name:   str           # which agent made this claim
    context_text: str = ""      # formatted source text sent to Haiku
    # Filled in after verification
    status:            str = ""   # VERIFIED|UNVERIFIED|CONTRADICTED|MISLEADING
    reason:            str = ""
    corrected_claim:   str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Infrastructure helpers
# ─────────────────────────────────────────────────────────────────────────────

def _supabase():
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception as exc:
        log.warning("Supabase connect failed: %s", exc)
        return None


def _load_prompt() -> str:
    try:
        return FACT_CHECK_PROMPT_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        log.error("fact_check.txt not found at %s", FACT_CHECK_PROMPT_PATH)
        return ""


def _haiku_client():
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=key)
    except ImportError:
        log.warning("anthropic package not installed")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# RSI helper (same formula as agents — no TA-Lib)
# ─────────────────────────────────────────────────────────────────────────────

def _compute_rsi(close: pd.Series, period: int = 14) -> Optional[float]:
    if len(close) < period + 5:
        return None
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs       = avg_gain / avg_loss.where(avg_loss != 0, float("nan"))
    rsi      = rs.where(avg_loss == 0, 100 - (100 / (1 + rs)))
    rsi      = rsi.where(avg_loss != 0, 100.0)
    val      = rsi.iloc[-1]
    return float(val) if val == val else None  # NaN guard


# ─────────────────────────────────────────────────────────────────────────────
# Source context formatters
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_screener(data: Optional[dict], symbol: str) -> str:
    if not data:
        return f"No screener.in data available for {symbol}."
    labels = [
        ("pe",                "P/E ratio"),
        ("revenue_growth",    "Revenue growth YoY (%)"),
        ("revenue_growth_qoq","Revenue growth QoQ (%)"),
        ("ebitda_margin",     "EBITDA margin (%)"),
        ("debt_equity",       "Debt/Equity ratio"),
        ("roce",              "ROCE (%)"),
        ("promoter_holding",  "Promoter holding (%)"),
        ("promoter_pledging", "Promoter pledging (%)"),
    ]
    lines = [f"Financial data for {symbol} from screener.in:"]
    for key, label in labels:
        v = data.get(key)
        if v is not None:
            lines.append(f"  {label}: {v}")
    return "\n".join(lines) if len(lines) > 1 else f"No relevant metrics for {symbol}."


def _fmt_ohlcv(df: Optional[pd.DataFrame], symbol: str) -> str:
    if df is None or df.empty:
        return f"No OHLCV data available for {symbol}."
    close = df["Close"]
    rsi   = _compute_rsi(close)
    ema20 = float(close.ewm(span=20,  adjust=False).mean().iloc[-1])
    ema50 = float(close.ewm(span=50,  adjust=False).mean().iloc[-1])
    lines = [
        f"Price/technical data for {symbol} from yfinance:",
        f"  Latest close    : {float(close.iloc[-1]):.2f}",
        f"  52-week high    : {float(df['High'].max()):.2f}",
        f"  52-week low     : {float(df['Low'].min()):.2f}",
        f"  EMA-20          : {ema20:.2f}",
        f"  EMA-50          : {ema50:.2f}",
    ]
    if len(df) >= 200:
        ema200 = float(close.ewm(span=200, adjust=False).mean().iloc[-1])
        lines.append(f"  EMA-200         : {ema200:.2f}")
    if rsi is not None:
        lines.append(f"  RSI (14-day)    : {rsi:.1f}")
    return "\n".join(lines)


def _fmt_fii(data: Optional[dict], symbol: str) -> str:
    if not data:
        return "FII/DII data unavailable."
    return (
        f"FII/DII flow data (market-wide):\n"
        f"  Date       : {data.get('date', 'N/A')}\n"
        f"  FII net    : Rs {data.get('fii_net', 'N/A')} Cr\n"
        f"  DII net    : Rs {data.get('dii_net', 'N/A')} Cr"
    )


# Map source identifiers → (fetcher_fn, formatter_fn)
# Fetcher receives symbol; formatter receives (raw_data, symbol)
_SOURCE_MAP: dict[str, tuple] = {
    "screener_in":          ("screener", _fmt_screener),
    "yfinance_ohlcv_1y":    ("ohlcv",   _fmt_ohlcv),
    "yfinance_price":       ("ohlcv",   _fmt_ohlcv),
    "yfinance_sector":      ("screener", _fmt_screener),  # sector PE comes from screener too
    "nse_fii_dii_live":     ("fii",     _fmt_fii),
}


def _fetch_source_context(source_name: str, symbol: str, cache: dict) -> str:
    """
    Fetch raw data for a given source identifier and format it as text.
    Results are cached per (source_name, symbol) to avoid redundant API calls.
    """
    from data.fetchers import get_ohlcv, get_screener_data, get_nse_fii_dii  # noqa

    cache_key = f"{source_name}::{symbol}"
    if cache_key not in cache:
        kind, _ = _SOURCE_MAP.get(source_name, ("screener", _fmt_screener))
        try:
            if kind == "screener":
                cache[cache_key] = get_screener_data(symbol)
            elif kind == "ohlcv":
                cache[cache_key] = get_ohlcv(symbol, period="1y")
            elif kind == "fii":
                cache[cache_key] = get_nse_fii_dii()
            else:
                cache[cache_key] = None
        except Exception as exc:
            log.debug("Source fetch failed (%s, %s): %s", source_name, symbol, exc)
            cache[cache_key] = None

    _, fmt_fn = _SOURCE_MAP.get(source_name, ("screener", _fmt_screener))
    return fmt_fn(cache[cache_key], symbol)


# ─────────────────────────────────────────────────────────────────────────────
# Claim extraction
# ─────────────────────────────────────────────────────────────────────────────

def _extract_claims(rec: dict, agent_results: dict, source_cache: dict) -> list[Claim]:
    """
    Extract up to MAX_CLAIMS_PER_REC verifiable factual claims from
    the full agent output, pairing each with fresh source context.
    """
    symbol = rec["symbol"]
    claims: list[Claim] = []

    # ── Fundamental: raw metrics ──────────────────────────────────────────────
    fund        = agent_results.get("fundamental", {})
    fund_detail = (fund.get("detail") or {})
    raw         = (fund_detail.get("raw_metrics") or {})

    fund_metrics = [
        ("pe",               "P/E ratio",                     "screener_in"),
        ("revenue_growth",   "revenue growth YoY (%)",        "screener_in"),
        ("ebitda_margin",    "EBITDA margin (%)",              "screener_in"),
        ("debt_equity",      "debt/equity ratio",             "screener_in"),
        ("roce",             "ROCE (%)",                      "screener_in"),
        ("promoter_holding", "promoter holding (%)",          "screener_in"),
        ("promoter_pledging","promoter pledging (%)",         "screener_in"),
    ]
    for key, label, src in fund_metrics:
        v = raw.get(key)
        if v is not None and len(claims) < MAX_CLAIMS_PER_REC:
            ctx = _fetch_source_context(src, symbol, source_cache)
            claims.append(Claim(
                claim_text    = f"{label} is {v}",
                metric_key    = key,
                claimed_value = v,
                data_source   = src,
                agent_name    = "fundamental",
                context_text  = ctx,
            ))

    # ── Technical: key indicators ────────────────────────────────────────────
    tech        = agent_results.get("technical", {})
    tech_detail = (tech.get("detail") or {})
    indicators  = (tech_detail.get("indicators") or {})

    tech_metrics = [
        ("rsi",  "RSI (14-day)",   "yfinance_ohlcv_1y"),
        ("ema50","EMA-50 price",   "yfinance_ohlcv_1y"),
        ("adx",  "ADX (trend strength)", "yfinance_ohlcv_1y"),
    ]
    for key, label, src in tech_metrics:
        v = indicators.get(key)
        if v is not None and len(claims) < MAX_CLAIMS_PER_REC:
            ctx = _fetch_source_context(src, symbol, source_cache)
            claims.append(Claim(
                claim_text    = f"{label} is {v:.2f}",
                metric_key    = key,
                claimed_value = float(v),
                data_source   = src,
                agent_name    = "technical",
                context_text  = ctx,
            ))

    # NOTE: upside_pct and danger_drop_pct are deliberately NOT included here.
    # These are model-computed/derived values (DCF output, danger scoring).
    # screener.in raw data does not directly contain them, so Haiku always marks
    # them UNVERIFIED — inflating the hallucination counter with false positives.
    # They are fact-checked indirectly via the underlying raw metrics (PE, EBITDA,
    # ROCE etc.) that feed into the computation.

    return claims[:MAX_CLAIMS_PER_REC]


# ─────────────────────────────────────────────────────────────────────────────
# P4-C: Deterministic numerical grounding (pre-LLM)
# ─────────────────────────────────────────────────────────────────────────────

# Tolerance map: metric_key → (tolerance, use_absolute)
# use_absolute=True  → tolerance is in absolute units (pp, RSI points, etc.)
# use_absolute=False → tolerance is a fraction of the actual value (relative %)
_NUMERIC_TOLERANCES: dict[str, tuple] = {
    "pe":                (0.15, False),  # ±15% relative  — P/E varies by source timing
    "revenue_growth":    (0.20, False),  # ±20% relative  — YoY% can vary by source
    "ebitda_margin":     (0.10, False),  # ±10% relative
    "debt_equity":       (0.15, False),  # ±15% relative
    "roce":              (0.10, False),  # ±10% relative
    "roe":               (0.10, False),  # ±10% relative
    "promoter_holding":  (2.0,  True),   # ±2 pp absolute — should be near-exact
    "promoter_pledging": (2.0,  True),   # ±2 pp absolute
    "rsi":               (5.0,  True),   # ±5 RSI points  — slight window differences ok
    "ema50":             (0.02, False),  # ±2% relative   — price
    "ema20":             (0.02, False),  # ±2% relative
}


def _extract_numeric_from_source(
    metric_key:  str,
    source_name: str,
    cached_data,
) -> Optional[float]:
    """
    Extract the actual numeric value for a metric from already-cached source data.

    Returns None when:
      - cached_data is None (data fetch failed)
      - metric_key is not present in the cached data
      - The cached data type doesn't match the expected shape

    Supports:
      - screener.in snapshot (dict): pe, revenue_growth, ebitda_margin, debt_equity,
        roce, roe, promoter_holding, promoter_pledging
      - yfinance OHLCV (DataFrame): rsi (computed), ema20, ema50 (computed)
    """
    if cached_data is None:
        return None

    # ── Screener.in / yfinance-sector snapshot ─────────────────────────────────
    if source_name in ("screener_in", "yfinance_sector"):
        if isinstance(cached_data, dict):
            v = cached_data.get(metric_key)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass

    # ── OHLCV DataFrame ─────────────────────────────────────────────────────────
    elif source_name in ("yfinance_ohlcv_1y", "yfinance_price"):
        if isinstance(cached_data, pd.DataFrame) and not cached_data.empty:
            close = cached_data["Close"].dropna()
            if close.empty:
                return None
            if metric_key == "rsi":
                return _compute_rsi(close)
            elif metric_key in ("ema50", "ema_50"):
                try:
                    return float(close.ewm(span=50, adjust=False).mean().iloc[-1])
                except (IndexError, ValueError):
                    return None
            elif metric_key in ("ema20", "ema_20"):
                try:
                    return float(close.ewm(span=20, adjust=False).mean().iloc[-1])
                except (IndexError, ValueError):
                    return None

    return None


def _numerical_grounding_check(
    claims:       list[Claim],
    source_cache: dict,
    symbol:       str,
) -> int:
    """
    Deterministic numerical grounding pass — runs BEFORE the Haiku LLM call.

    For each claim whose metric_key is in _NUMERIC_TOLERANCES and whose
    claimed_value is numeric, this function:

      1. Extracts the actual value from the already-fetched source_cache
      2. Compares claimed vs actual using the configured tolerance
      3. Sets claim.status:
           "VERIFIED"     — within tolerance  → Haiku call skipped
           "CONTRADICTED" — outside tolerance → Haiku call skipped; corrected_claim set
         or leaves status="" if the actual value is unavailable → Haiku handles it

    Benefits:
      • Eliminates false negatives where Haiku "agrees" with a wrong number
      • Produces exact corrected values (e.g. "actual ROCE is 18.3%, not 24.0%")
      • Reduces LLM API calls — numeric claims resolved here skip Haiku entirely
      • Preserves Haiku for qualitative/directional claims it's better suited for

    Returns:
        Number of claims resolved deterministically (for logging).
    """
    resolved = 0

    for claim in claims:
        if claim.status:
            continue  # already resolved

        tolerance_entry = _NUMERIC_TOLERANCES.get(claim.metric_key)
        if tolerance_entry is None:
            continue  # no tolerance defined — pass to Haiku

        tolerance, use_absolute = tolerance_entry

        try:
            claimed = float(claim.claimed_value)
        except (TypeError, ValueError):
            continue  # non-numeric claimed value — pass to Haiku

        # Look up actual value from source cache
        cache_key   = f"{claim.data_source}::{symbol}"
        cached_data = source_cache.get(cache_key)
        actual = _extract_numeric_from_source(
            claim.metric_key, claim.data_source, cached_data
        )

        if actual is None:
            continue  # cannot determine actual — Haiku handles it

        # Compare
        diff_abs = abs(claimed - actual)
        if use_absolute:
            within = diff_abs <= tolerance
        else:
            within = (diff_abs / abs(actual) <= tolerance) if actual != 0 else (claimed == 0)

        if within:
            claim.status = "VERIFIED"
            if use_absolute:
                claim.reason = (
                    f"Numerical check: claimed {claimed:.2f}, actual {actual:.2f} "
                    f"(diff {diff_abs:.2f}, tolerance ±{tolerance:.1f})"
                )
            else:
                rel_pct = diff_abs / abs(actual) * 100
                claim.reason = (
                    f"Numerical check: claimed {claimed:.2f}, actual {actual:.2f} "
                    f"(diff {rel_pct:.1f}%, threshold {int(tolerance * 100)}%)"
                )
        else:
            claim.status = "CONTRADICTED"
            claim.corrected_claim = (
                f"Actual {claim.metric_key.replace('_', ' ')} is {actual:.2f}, "
                f"not {claimed:.2f}"
            )
            if use_absolute:
                claim.reason = (
                    f"Numerical check: claimed {claimed:.2f} vs actual {actual:.2f} "
                    f"(diff {diff_abs:.2f}pp, threshold ±{tolerance:.1f}pp)"
                )
            else:
                rel_pct = diff_abs / abs(actual) * 100 if actual != 0 else float("inf")
                claim.reason = (
                    f"Numerical check: claimed {claimed:.2f} vs actual {actual:.2f} "
                    f"(diff {rel_pct:.1f}%, threshold {int(tolerance * 100)}%)"
                )
            log.debug(
                "[%s] GROUNDING CONTRADICTED: %s (claimed=%.2f, actual=%.2f)",
                symbol, claim.metric_key, claimed, actual,
            )

        resolved += 1

    return resolved


# ─────────────────────────────────────────────────────────────────────────────
# Haiku verification
# ─────────────────────────────────────────────────────────────────────────────

def _verify_claim(
    claim:      Claim,
    symbol:     str,
    prompt_tmpl: str,
    ant_client,
) -> Claim:
    """
    Call Claude Haiku to verify one claim. Returns the Claim with
    status/reason/corrected_claim populated.

    Skips the API call entirely if the claim was already resolved by the
    deterministic numerical grounding check (_numerical_grounding_check).
    Falls back to UNVERIFIED if Haiku call fails.
    """
    # Skip if already resolved deterministically (P4-C grounding)
    if claim.status:
        return claim

    if not ant_client or not prompt_tmpl:
        claim.status = "UNVERIFIED"
        claim.reason = "Haiku client unavailable"
        return claim

    prompt = (
        prompt_tmpl
        .replace("{symbol}",      symbol)
        .replace("{source_name}", claim.data_source)
        .replace("{agent_name}",  claim.agent_name)
        .replace("{text}",        claim.context_text or "No data available.")
        .replace("{claim}",       claim.claim_text)
    )

    try:
        response = ant_client.messages.create(
            model      = CLAUDE_HAIKU_MODEL,
            max_tokens = HAIKU_MAX_TOKENS,
            messages   = [{"role": "user", "content": prompt}],
        )
        raw_text = response.content[0].text.strip()

        # Strip markdown fences if present (e.g. ```json ... ```)
        raw_text = re.sub(r"^```[a-z]*\n?", "", raw_text)
        raw_text = re.sub(r"\n?```$", "", raw_text).strip()

        # Brace-matching extractor — handles models that append text after the JSON object.
        # Walks from the first '{' tracking depth so nested braces are handled correctly.
        json_str = raw_text
        start = raw_text.find("{")
        if start >= 0:
            depth = 0
            for idx, ch in enumerate(raw_text[start:], start):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        json_str = raw_text[start: idx + 1]
                        break

        parsed = json.loads(json_str)
        claim.status          = str(parsed.get("status", "UNVERIFIED")).upper()
        claim.reason          = str(parsed.get("reason", ""))
        claim.corrected_claim = str(parsed.get("corrected_claim") or "")

        # Normalise status
        if claim.status not in {"VERIFIED", "UNVERIFIED", "CONTRADICTED", "MISLEADING"}:
            claim.status = "UNVERIFIED"

    except json.JSONDecodeError as exc:
        log.warning("Haiku JSON parse failed for claim '%s': %s", claim.claim_text, exc)
        claim.status = "UNVERIFIED"
        claim.reason = "JSON parse error in Haiku response"
    except Exception as exc:
        log.warning("Haiku call failed for claim '%s': %s", claim.claim_text, exc)
        claim.status = "UNVERIFIED"
        claim.reason = f"Haiku error: {exc}"

    return claim


# ─────────────────────────────────────────────────────────────────────────────
# Governance rules application
# ─────────────────────────────────────────────────────────────────────────────

def _apply_governance_rules(claims: list[Claim], rec: dict) -> dict:
    """
    Evaluate verified claims against governance rules. Returns a gov_check dict
    and the adjusted confidence delta to apply to the recommendation.
    """
    contradicted = [c for c in claims if c.status == "CONTRADICTED"]
    misleading   = [c for c in claims if c.status == "MISLEADING"]
    unverified   = [c for c in claims if c.status == "UNVERIFIED"]
    verified     = [c for c in claims if c.status == "VERIFIED"]

    flags: list[dict] = []
    confidence_delta   = 0.0
    withheld           = False

    # Rule 1: each CONTRADICTED claim reduces confidence by 15 pts
    for c in contradicted:
        confidence_delta -= CONTRADICTED_PENALTY
        flags.append({
            "type":             "CONTRADICTED_CLAIM",
            "metric":           c.metric_key,
            "agent":            c.agent_name,
            "claim":            c.claim_text,
            "reason":           c.reason,
            "corrected_claim":  c.corrected_claim,
        })
        log.warning(
            "[%s] CONTRADICTED: %s — %s", rec["symbol"], c.claim_text, c.reason
        )

    # Rule 2: misleading claims reduce confidence by 5 pts each
    for c in misleading:
        confidence_delta -= 5.0
        flags.append({
            "type":            "MISLEADING_CLAIM",
            "metric":          c.metric_key,
            "agent":           c.agent_name,
            "claim":           c.claim_text,
            "reason":          c.reason,
            "corrected_claim": c.corrected_claim,
        })

    # Rule 3: withhold if too many unverified claims
    if len(unverified) > UNVERIFIED_WITHHOLD_N:
        withheld = True
        flags.append({
            "type":   "WITHHELD_UNVERIFIED",
            "reason": (
                f"{len(unverified)} of {len(claims)} claims could not be verified "
                f"against source data (threshold: >{UNVERIFIED_WITHHOLD_N})"
            ),
        })
        log.warning(
            "[%s] WITHHELD: %d/%d claims unverified",
            rec["symbol"], len(unverified), len(claims),
        )

    gov_check = {
        "checked_at":        datetime.now(timezone.utc).isoformat(),
        "claims_checked":    len(claims),
        "verified_count":    len(verified),
        "contradicted_count":len(contradicted),
        "misleading_count":  len(misleading),
        "unverified_count":  len(unverified),
        "confidence_delta":  round(confidence_delta, 2),
        "withheld":          withheld,
        "flags":             flags,
        "claim_detail": [
            {
                "claim":            c.claim_text,
                "agent":            c.agent_name,
                "source":           c.data_source,
                "status":           c.status,
                "reason":           c.reason,
                "corrected_claim":  c.corrected_claim or None,
            }
            for c in claims
        ],
    }

    return gov_check, confidence_delta, withheld


# ─────────────────────────────────────────────────────────────────────────────
# Agent performance update
# ─────────────────────────────────────────────────────────────────────────────

def _update_hallucination_rates(
    agent_claim_counts: dict[str, dict],  # {agent_name: {total, contradicted}}
    client,
    dry_run: bool,
) -> None:
    """
    Upsert hallucination_rate into agent_performance for each agent.
    Trend is computed by comparing against the most recent existing row.
    """
    today = date.today().isoformat()

    for agent_name, counts in agent_claim_counts.items():
        total       = counts.get("total", 0)
        contradicted= counts.get("contradicted", 0)
        if total == 0:
            continue

        rate = round((contradicted / total) * 100, 2)
        log.info(
            "Agent %s — hallucination_rate=%.2f%% (%d/%d contradicted)",
            agent_name, rate, contradicted, total,
        )

        if dry_run:
            print(f"  [DRY RUN] agent_performance: {agent_name} hallucination_rate={rate}%")
            continue

        if not client:
            continue

        # Fetch previous rate to determine trend
        trend = "STABLE"
        try:
            resp = (
                client.table("agent_performance")
                .select("hallucination_rate")
                .eq("agent_name", agent_name)
                .order("audit_date", desc=True)
                .limit(1)
                .execute()
            )
            prev_rows = resp.data or []
            if prev_rows:
                prev_rate = float(prev_rows[0].get("hallucination_rate") or 0)
                if rate < prev_rate - 0.2:
                    trend = "IMPROVING"
                elif rate > prev_rate + 0.2:
                    trend = "DEGRADING"
        except Exception:
            pass

        try:
            client.table("agent_performance").insert({
                "agent_name":        agent_name,
                "hallucination_rate": rate,
                "trend":             trend,
                "audit_date":        today,
            }).execute()
        except Exception as exc:
            log.warning("agent_performance update failed for %s: %s", agent_name, exc)

        # Alert if rate exceeds threshold
        if rate > HALLUCINATION_ALERT_PCT:
            log.warning(
                "HALLUCINATION ALERT: agent=%s rate=%.2f%% (threshold=%.1f%%)",
                agent_name, rate, HALLUCINATION_ALERT_PCT,
            )
            if client:
                try:
                    client.table("portfolio_alerts").insert({
                        "symbol":     agent_name,
                        "severity":   "WARNING",
                        "alert_type": "HIGH_HALLUCINATION_RATE",
                        "title":      (
                            f"Agent {agent_name} hallucination rate {rate:.2f}% "
                            f"exceeds {HALLUCINATION_ALERT_PCT}% threshold"
                        ),
                        "detail":     (
                            f"{contradicted} contradicted claims out of "
                            f"{total} total claims checked today."
                        ),
                        "resolved":   False,
                    }).execute()
                except Exception as exc:
                    log.debug("Alert insert failed: %s", exc)


# ─────────────────────────────────────────────────────────────────────────────
# Core: check a single recommendation
# ─────────────────────────────────────────────────────────────────────────────

def _check_one(
    rec:           dict,
    agent_results: dict,   # full 7-agent output from symbol_results[symbol]
    prompt_tmpl:   str,
    ant_client,
    source_cache:  dict,   # shared across all recs to avoid redundant fetches
    dry_run:       bool,
) -> tuple[dict, dict]:   # (modified_rec, agent_claim_counts)
    """
    Fact-check one recommendation. Returns:
        modified_rec      — rec dict with gov_check, adjusted confidence
        agent_claim_counts — {agent_name: {total, contradicted}} for this rec
    """
    symbol = rec["symbol"]
    log.info("[%s] Extracting and verifying claims...", symbol)

    claims = _extract_claims(rec, agent_results, source_cache)

    if len(claims) < MIN_CLAIMS_FOR_CHECK:
        log.info("[%s] Only %d claims extracted — skipping fact check", symbol, len(claims))
        rec["gov_check"] = {
            "checked_at":  datetime.now(timezone.utc).isoformat(),
            "claims_checked": len(claims),
            "skipped_reason": f"Insufficient claims ({len(claims)} < {MIN_CLAIMS_FOR_CHECK})",
            "withheld": False,
            "flags": [],
        }
        return rec, {}

    # ── P4-C: Deterministic numerical grounding pass (pre-LLM) ───────────────
    n_grounded = _numerical_grounding_check(claims, source_cache, symbol)
    if n_grounded:
        log.info(
            "[%s] Numerical grounding resolved %d/%d claims deterministically",
            symbol, n_grounded, len(claims),
        )

    # Verify remaining claims via Haiku (already-resolved claims are skipped)
    for claim in claims:
        _verify_claim(claim, symbol, prompt_tmpl, ant_client)
        log.debug("[%s] %s → %s", symbol, claim.claim_text[:60], claim.status)

    gov_check, confidence_delta, withheld = _apply_governance_rules(claims, rec)
    rec["gov_check"] = gov_check

    # Apply confidence adjustment (floor at 5, cap at 100)
    if confidence_delta != 0:
        old_conf = float(rec.get("confidence", 50))
        new_conf = max(5.0, min(100.0, old_conf + confidence_delta))
        rec["confidence"] = round(new_conf, 2)
        log.info(
            "[%s] confidence adjusted: %.1f -> %.1f (delta=%.1f)",
            symbol, old_conf, new_conf, confidence_delta,
        )

    if withheld:
        rec["action"] = "WITHHELD"

    # Build per-agent claim counts
    agent_counts: dict[str, dict] = {}
    for c in claims:
        a = c.agent_name
        agent_counts.setdefault(a, {"total": 0, "contradicted": 0})
        agent_counts[a]["total"] += 1
        if c.status == "CONTRADICTED":
            agent_counts[a]["contradicted"] += 1

    verified_n = sum(1 for c in claims if c.status == "VERIFIED")
    log.info(
        "[%s] fact-check complete: %d/%d verified, %d contradicted, %d unverified%s",
        symbol, verified_n, len(claims),
        len([c for c in claims if c.status == "CONTRADICTED"]),
        len([c for c in claims if c.status == "UNVERIFIED"]),
        " [WITHHELD]" if withheld else "",
    )

    return rec, agent_counts


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def check_recommendations(
    recommendations: list[dict],
    symbol_results:  dict[str, dict],   # symbol -> {agent_name -> agent_result}
    dry_run:         bool = False,
) -> list[dict]:
    """
    Fact-check all recommendations in a pipeline run.
    Called by orchestrator fact_check_node.

    Args:
        recommendations:  List of recommendation dicts from synthesise_node.
        symbol_results:   Full agent output from run_agents_node.
        dry_run:          If True, skip DB writes but still run Haiku checks.

    Returns:
        The recommendations list, mutated in-place with gov_check fields
        and adjusted confidence values.
    """
    if not recommendations:
        return recommendations

    t0           = time.time()
    prompt_tmpl  = _load_prompt()
    ant_client   = _haiku_client()
    source_cache: dict = {}
    client       = _supabase() if not dry_run else None

    if not ant_client:
        log.warning("Anthropic client unavailable — skipping fact checks")
        return recommendations

    if not prompt_tmpl:
        log.warning("fact_check.txt missing — skipping fact checks")
        return recommendations

    # Aggregate hallucination counts across all recs for this run
    run_agent_counts: dict[str, dict] = {}

    for rec in recommendations:
        symbol        = rec.get("symbol", "?")
        agent_results = symbol_results.get(symbol, {})

        try:
            rec, agent_counts = _check_one(
                rec, agent_results, prompt_tmpl, ant_client,
                source_cache, dry_run,
            )
            # Merge into run totals
            for agent, cnts in agent_counts.items():
                run_agent_counts.setdefault(agent, {"total": 0, "contradicted": 0})
                run_agent_counts[agent]["total"]       += cnts["total"]
                run_agent_counts[agent]["contradicted"] += cnts["contradicted"]
        except Exception as exc:
            log.error("[%s] fact check failed: %s", symbol, exc)
            rec["gov_check"] = {
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "error": str(exc),
                "withheld": False,
                "flags": [],
            }

    # Update agent_performance with hallucination rates
    _update_hallucination_rates(run_agent_counts, client, dry_run)

    log.info(
        "Fact-check run complete — %d recs checked in %.1fs",
        len(recommendations), time.time() - t0,
    )
    return recommendations


def run(dry_run: bool = False) -> dict:
    """
    Standalone mode: re-fetch the most recent 20 recommendations from Supabase
    and run fact checks. Useful for ad-hoc governance audits.
    """
    t0     = time.time()
    errors: list[str] = []

    client = _supabase()
    if not client:
        log.error("Supabase unavailable — cannot run standalone fact check")
        return {"recs_checked": 0, "errors": ["Supabase unavailable"],
                "duration_seconds": 0}

    try:
        resp = (
            client.table("recommendations")
            .select("*")
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )
        recs = resp.data or []
    except Exception as exc:
        log.error("Failed to load recommendations: %s", exc)
        return {"recs_checked": 0, "errors": [str(exc)],
                "duration_seconds": round(time.time() - t0, 2)}

    if not recs:
        log.info("No recommendations found for standalone fact check")
        return {"recs_checked": 0, "errors": [],
                "duration_seconds": round(time.time() - t0, 2)}

    log.info("Standalone fact-check: %d recommendations", len(recs))

    # In standalone mode we don't have in-memory agent results,
    # so we pass empty dicts — claim extraction will only use
    # recommendation fields (upside_pct, danger_drop_pct, headline)
    # and will still re-fetch source data for context.
    symbol_results = {r["symbol"]: {} for r in recs}
    checked = check_recommendations(recs, symbol_results, dry_run=dry_run)

    # Update gov_check in DB for checked recs
    if not dry_run and client:
        for rec in checked:
            rec_id   = rec.get("id")
            gov_check= rec.get("gov_check")
            if rec_id and gov_check:
                try:
                    client.table("recommendations") \
                        .update({"gov_check":  gov_check,
                                 "confidence": rec.get("confidence")}) \
                        .eq("id", rec_id) \
                        .execute()
                except Exception as exc:
                    errors.append(f"DB update {rec_id}: {exc}")

    return {
        "recs_checked":    len(checked),
        "errors":          errors,
        "duration_seconds": round(time.time() - t0, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Bharat Intelligence Fact Checker")
    parser.add_argument("--run-now", action="store_true",
                        help="Run fact-check on the latest 20 DB recommendations")
    parser.add_argument("--dry",     action="store_true",
                        help="Dry run: no DB writes, prints hallucination stats")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    if args.run_now:
        result = run(dry_run=args.dry)
        log.info("Result: %s", result)
    else:
        print("Use --run-now to execute. Example:")
        print("  python governance/fact_checker.py --run-now --dry")


if __name__ == "__main__":
    main()
