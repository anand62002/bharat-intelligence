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
HAIKU_MAX_TOKENS        = 300
MAX_CLAIMS_PER_REC      = 8
MIN_CLAIMS_FOR_CHECK    = 3     # skip rec if we can't extract at least this many
CONTRADICTED_PENALTY    = 15.0  # confidence points deducted per contradicted claim
UNVERIFIED_WITHHOLD_N   = 3     # withhold if more than this many unverified claims
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

    # ── Recommendation-level: upside / danger ─────────────────────────────────
    if rec.get("upside_pct") and len(claims) < MAX_CLAIMS_PER_REC:
        ctx = _fetch_source_context("screener_in", symbol, source_cache)
        claims.append(Claim(
            claim_text    = f"stock has {rec['upside_pct']:.1f}% upside potential",
            metric_key    = "upside_pct",
            claimed_value = float(rec["upside_pct"]),
            data_source   = "screener_in",
            agent_name    = "fundamental",
            context_text  = ctx,
        ))

    if rec.get("danger_drop_pct") and rec["danger_drop_pct"] > 0 \
            and len(claims) < MAX_CLAIMS_PER_REC:
        ctx = _fetch_source_context("screener_in", symbol, source_cache)
        claims.append(Claim(
            claim_text    = (
                f"downside risk is {rec['danger_drop_pct']:.1f}% "
                f"(confidence {rec.get('danger_confidence', 0):.0f}%)"
            ),
            metric_key    = "danger_drop_pct",
            claimed_value = float(rec["danger_drop_pct"]),
            data_source   = "screener_in",
            agent_name    = "fundamental",
            context_text  = ctx,
        ))

    return claims[:MAX_CLAIMS_PER_REC]


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
    Falls back to UNVERIFIED if Haiku call fails.
    """
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

        # Strip possible markdown fences
        if raw_text.startswith("```"):
            raw_text = raw_text.strip("`").lstrip("json").strip()

        parsed = json.loads(raw_text)
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

    # Verify each claim via Haiku
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
