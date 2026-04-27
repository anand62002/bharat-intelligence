"""
governance/hallucination_detector.py — Bharat Intelligence Governance: Hallucination Detector
==============================================================================================
Runs weekly (Sunday 08:00 IST) to audit past recommendations and measure
per-agent directional accuracy and hallucination rates.

Algorithm
─────────
  1. Sample up to 20 past recommendations from Supabase where the horizon has
     elapsed (created_at + horizon_days <= today).
  2. For each recommendation, fetch the price at creation date and at the
     horizon end date via yfinance history.
  3. Evaluate whether each agent's individual signal was directionally correct:
       BUY  signal  → price increased over horizon        (actual_return > +2%)
       SELL signal  → price decreased over horizon        (actual_return < -2%)
       HOLD signal  → price stayed within ±10% of entry
       NO_DATA      → excluded from accuracy calculation
  4. Compute accuracy_90d per agent as:
       correct_signals / total_evaluated_signals × 100
  5. Upsert accuracy_90d into agent_performance (one row per agent per audit_date).
  6. Separately compute hallucination_rate from the gov_check data stored on
     recommendations (contradicted_count / claims_checked × 100, averaged over
     the sampled recs that have gov_check populated).
  7. Emit a portfolio_alert (severity=WARNING) for any agent whose
     hallucination_rate exceeds 1.5%.

Entry points
────────────
  run(dry_run) -> dict        Weekly job callable; also invoked by CLI.

Usage
─────
  python governance/hallucination_detector.py --run-now
  python governance/hallucination_detector.py --run-now --dry
"""

from __future__ import annotations

import argparse
import logging
import os
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
MAX_SAMPLE_RECS          = 20
HALLUCINATION_ALERT_PCT  = 1.5   # alert threshold for hallucination_rate
DIRECTIONAL_BUFFER_PCT   = 2.0   # return > +2% = BUY correct, < -2% = SELL correct
HOLD_BAND_PCT            = 10.0  # within ±10% = HOLD correct
IMPROVING_THRESHOLD      = 1.0   # accuracy improvement ≥ 1 pt = IMPROVING
DEGRADING_THRESHOLD      = 1.0   # accuracy drop ≥ 1 pt = DEGRADING

# ── Trust score constants ──────────────────────────────────────────────────────
DEFAULT_ACCURACY_BASELINE = 70.0  # neutral accuracy → trust = 1.0 (matches orchestrator)
TRUST_MIN                 = 0.5   # floor: severely under-performing agent
TRUST_MAX                 = 1.5   # ceiling: highly accurate agent
TRUST_HIGH_THRESHOLD      = 1.2   # trust ≥ this → hallucination is CRITICAL, not WARNING

# ── Multi-stage pipeline constants ────────────────────────────────────────────
CONSISTENCY_RERUNS              = 3      # times to re-run an agent in Stage 1
CONSISTENCY_MIN_AGREEMENT       = 0.67   # flag if < 67% of runs share the majority signal
CONSISTENCY_SCORE_CV_THRESHOLD  = 0.30   # flag if score coefficient of variation > 30%
GROUNDING_TOLERANCE_PCT         = 5.0    # max % deviation before a claim is flagged ungrounded


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


def _fetch_price_on_date(symbol: str, target_date: date) -> Optional[float]:
    """
    Return the adjusted closing price for *symbol* on *target_date*.
    Looks up to 5 calendar days forward to handle weekends/holidays.
    Returns None if no data is available.
    """
    try:
        import yfinance as yf
        from data.fetchers import yf_fetch_with_retry
        # yfinance history end date is exclusive; fetch a 7-day window
        start = target_date
        end   = target_date + timedelta(days=7)
        _t    = yf.Ticker(symbol)
        df    = yf_fetch_with_retry(
            _t.history, start=str(start), end=str(end), auto_adjust=True
        )
        if df.empty:
            return None
        # Return the first available session >= target_date
        return float(df["Close"].iloc[0])
    except Exception as exc:
        log.debug("Price fetch failed (%s, %s): %s", symbol, target_date, exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Directional accuracy helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_signal_correct(signal: str, actual_return_pct: float) -> Optional[bool]:
    """
    Determine whether a single agent signal was directionally correct.

    Returns:
        True  → signal was correct
        False → signal was wrong
        None  → signal is NO_DATA or unknown; exclude from stats
    """
    sig = (signal or "").upper()
    if sig in ("NO_DATA", "", "NEUTRAL"):
        return None   # cannot evaluate

    if sig == "BUY":
        return actual_return_pct > DIRECTIONAL_BUFFER_PCT
    if sig in ("SELL", "AVOID"):
        return actual_return_pct < -DIRECTIONAL_BUFFER_PCT
    if sig == "HOLD":
        return abs(actual_return_pct) <= HOLD_BAND_PCT

    return None  # unrecognised signal


def _evaluate_rec(rec: dict) -> dict[str, Optional[bool]]:
    """
    For one recommendation, fetch entry price and horizon-end price, then
    evaluate each agent signal.

    Returns dict mapping agent_name → True/False/None (correct/wrong/N/A).
    """
    symbol       = rec.get("symbol", "")
    created_at   = rec.get("created_at", "")
    horizon_days = int(rec.get("horizon_days") or 180)
    agent_signals= rec.get("agent_signals") or {}

    # Parse creation date
    try:
        if isinstance(created_at, str):
            # Strip timezone info if present for date extraction
            created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        elif isinstance(created_at, datetime):
            created_dt = created_at
        else:
            log.debug("Unrecognised created_at type for %s", symbol)
            return {}
        entry_date   = created_dt.date()
        horizon_date = entry_date + timedelta(days=horizon_days)
    except (ValueError, AttributeError) as exc:
        log.debug("Date parse failed for rec %s: %s", rec.get("id"), exc)
        return {}

    # Prices
    entry_price   = _fetch_price_on_date(symbol, entry_date)
    horizon_price = _fetch_price_on_date(symbol, horizon_date)

    if entry_price is None or entry_price == 0:
        log.debug("[%s] entry price unavailable for %s", symbol, entry_date)
        return {}
    if horizon_price is None:
        log.debug("[%s] horizon price unavailable for %s", symbol, horizon_date)
        return {}

    actual_return_pct = (horizon_price - entry_price) / entry_price * 100.0
    log.info(
        "[%s] entry=%.2f  horizon=%.2f  return=%.2f%%  (horizon_date=%s)",
        symbol, entry_price, horizon_price, actual_return_pct, horizon_date,
    )

    evaluations: dict[str, Optional[bool]] = {}
    for agent_name, signal_data in agent_signals.items():
        if isinstance(signal_data, dict):
            signal = signal_data.get("signal", "NO_DATA")
        elif isinstance(signal_data, str):
            signal = signal_data
        else:
            signal = "NO_DATA"

        result = _is_signal_correct(signal, actual_return_pct)
        evaluations[agent_name] = result
        if result is not None:
            log.debug(
                "  [%s] agent=%s signal=%s → %s",
                symbol, agent_name, signal,
                "CORRECT" if result else "WRONG",
            )

    return evaluations


# ─────────────────────────────────────────────────────────────────────────────
# Accuracy aggregation
# ─────────────────────────────────────────────────────────────────────────────

def _compute_accuracy(
    all_evaluations: list[dict[str, Optional[bool]]],
) -> dict[str, dict]:
    """
    Aggregate per-agent correctness across all evaluated recommendations.

    Returns:
        {agent_name: {"correct": N, "total": N, "accuracy_90d": float}}
    """
    tallies: dict[str, dict] = {}
    for evals in all_evaluations:
        for agent, result in evals.items():
            if result is None:
                continue  # NO_DATA / unevaluable
            tallies.setdefault(agent, {"correct": 0, "total": 0})
            tallies[agent]["total"] += 1
            if result:
                tallies[agent]["correct"] += 1

    out = {}
    for agent, tally in tallies.items():
        total   = tally["total"]
        correct = tally["correct"]
        acc     = round((correct / total) * 100.0, 2) if total > 0 else 0.0
        out[agent] = {
            "correct":     correct,
            "total":       total,
            "accuracy_90d": acc,
        }

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Hallucination rate from gov_check data
# ─────────────────────────────────────────────────────────────────────────────

def _compute_hallucination_rates(recs: list[dict]) -> dict[str, float]:
    """
    Derive per-agent hallucination rate from the gov_check JSONB stored on
    each recommendation. Aggregates contradicted_count / claims_checked across
    all recs that have gov_check data.

    Returns:
        {agent_name: hallucination_rate_pct}   (only agents with gov_check data)
    """
    # We track per-agent claim totals and contradiction counts
    agent_totals: dict[str, dict] = {}

    for rec in recs:
        gov = rec.get("gov_check")
        if not gov or not isinstance(gov, dict):
            continue

        claim_detail = gov.get("claim_detail") or []
        for entry in claim_detail:
            agent = entry.get("agent", "unknown")
            status= str(entry.get("status", "UNVERIFIED")).upper()
            agent_totals.setdefault(agent, {"total": 0, "contradicted": 0})
            agent_totals[agent]["total"] += 1
            if status == "CONTRADICTED":
                agent_totals[agent]["contradicted"] += 1

    rates = {}
    for agent, counts in agent_totals.items():
        total = counts["total"]
        if total == 0:
            continue
        rates[agent] = round((counts["contradicted"] / total) * 100.0, 2)

    return rates


# ─────────────────────────────────────────────────────────────────────────────
# DB upsert helpers
# ─────────────────────────────────────────────────────────────────────────────

def _prev_accuracy(client, agent_name: str) -> Optional[float]:
    """Fetch the most recent accuracy_90d for an agent from agent_performance."""
    try:
        resp = (
            client.table("agent_performance")
            .select("accuracy_90d")
            .eq("agent_name", agent_name)
            .order("audit_date", desc=True)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if rows:
            return float(rows[0].get("accuracy_90d") or 0)
    except Exception:
        pass
    return None


def _prev_hallucination_rate(client, agent_name: str) -> Optional[float]:
    """Fetch the most recent hallucination_rate for an agent."""
    try:
        resp = (
            client.table("agent_performance")
            .select("hallucination_rate")
            .eq("agent_name", agent_name)
            .order("audit_date", desc=True)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if rows and rows[0].get("hallucination_rate") is not None:
            return float(rows[0]["hallucination_rate"])
    except Exception:
        pass
    return None


def _upsert_agent_performance(
    client,
    agent_name:      str,
    accuracy_90d:    Optional[float],
    hallucination_rate: Optional[float],
    dry_run:         bool,
) -> None:
    """
    Write a new agent_performance row for today's audit.
    Computes trend by comparing new values against the most recent stored row.
    """
    today = date.today().isoformat()

    # Trend for accuracy
    acc_trend = "STABLE"
    if accuracy_90d is not None and client:
        prev_acc = _prev_accuracy(client, agent_name)
        if prev_acc is not None:
            delta = accuracy_90d - prev_acc
            if delta >= IMPROVING_THRESHOLD:
                acc_trend = "IMPROVING"
            elif delta <= -DEGRADING_THRESHOLD:
                acc_trend = "DEGRADING"

    row: dict = {
        "agent_name":  agent_name,
        "audit_date":  today,
        "trend":       acc_trend,
    }
    if accuracy_90d is not None:
        row["accuracy_90d"] = accuracy_90d
    if hallucination_rate is not None:
        row["hallucination_rate"] = hallucination_rate

    if dry_run:
        acc_str  = f"accuracy_90d={accuracy_90d:.2f}%" if accuracy_90d is not None else "accuracy_90d=N/A"
        hall_str = f"hallucination_rate={hallucination_rate:.2f}%" if hallucination_rate is not None else ""
        print(
            f"  [DRY RUN] agent_performance: {agent_name}  "
            f"{acc_str}  {hall_str}  trend={acc_trend}"
        )
        return

    if not client:
        return

    try:
        client.table("agent_performance").insert(row).execute()
        log.info("agent_performance updated: %s %s", agent_name, row)
    except Exception as exc:
        log.warning("agent_performance upsert failed for %s: %s", agent_name, exc)


def _emit_hallucination_alert(
    client,
    agent_name:         str,
    hallucination_rate: float,
    dry_run:            bool,
    trust_score:        float = 1.0,
) -> None:
    """
    Create a portfolio_alert if hallucination_rate exceeds the threshold.

    Severity is trust-weighted:
      • trust ≥ TRUST_HIGH_THRESHOLD (1.2) → CRITICAL  (high-accuracy agent contradicting is alarming)
      • trust <  TRUST_HIGH_THRESHOLD      → WARNING
    """
    if hallucination_rate <= HALLUCINATION_ALERT_PCT:
        return

    severity = "CRITICAL" if trust_score >= TRUST_HIGH_THRESHOLD else "WARNING"

    log.warning(
        "HALLUCINATION ALERT [%s]: agent=%s rate=%.2f%% trust=%.2f (threshold=%.1f%%)",
        severity, agent_name, hallucination_rate, trust_score, HALLUCINATION_ALERT_PCT,
    )

    trust_note = (
        f"Agent has high trust score ({trust_score:.2f}) — "
        "contradictions from this agent are especially significant."
        if trust_score >= TRUST_HIGH_THRESHOLD
        else f"Agent trust score: {trust_score:.2f} (baseline 1.0)."
    )

    if dry_run:
        print(
            f"  [DRY RUN] ALERT [{severity}]: agent={agent_name} "
            f"hallucination_rate={hallucination_rate:.2f}% "
            f"trust={trust_score:.2f} exceeds {HALLUCINATION_ALERT_PCT}%"
        )
        return

    if not client:
        return

    try:
        client.table("portfolio_alerts").insert({
            "symbol":     agent_name,
            "severity":   severity,
            "alert_type": "HIGH_HALLUCINATION_RATE",
            "title": (
                f"Agent {agent_name} hallucination rate {hallucination_rate:.2f}% "
                f"exceeds {HALLUCINATION_ALERT_PCT}% threshold"
            ),
            "detail": (
                f"Weekly hallucination audit found {hallucination_rate:.2f}% "
                f"of fact-checked claims were CONTRADICTED. "
                f"{trust_note} "
                f"Review recent recommendations for {agent_name} agent outputs."
            ),
            "resolved": False,
        }).execute()
        log.info("Hallucination alert [%s] created for agent=%s", severity, agent_name)
    except Exception as exc:
        log.debug("Alert insert failed for %s: %s", agent_name, exc)


# ─────────────────────────────────────────────────────────────────────────────
# Trust score computation
# ─────────────────────────────────────────────────────────────────────────────

def get_agent_trust_scores(client=None) -> dict[str, float]:
    """
    Return a trust multiplier for each agent based on their most recent accuracy_90d.

    Formula:
        trust = max(TRUST_MIN, min(TRUST_MAX, accuracy_90d / DEFAULT_ACCURACY_BASELINE))

    Interpretation:
        • trust = 1.0  → agent is performing at baseline accuracy (70%)
        • trust > 1.0  → above-baseline accuracy; signals carry more weight
        • trust < 1.0  → below-baseline; signals should be discounted
        • Agents with no performance data → trust = 1.0 (neutral)

    Range: [TRUST_MIN=0.5, TRUST_MAX=1.5]

    Args:
        client:  Optional pre-built Supabase client.  Creates one if None.

    Returns:
        {agent_name: trust_score}  — only agents with accuracy_90d rows.
        Empty dict when Supabase is unreachable.
    """
    if client is None:
        client = _supabase()

    if not client:
        return {}

    trust_scores: dict[str, float] = {}
    try:
        resp = (
            client.table("agent_performance")
            .select("agent_name, accuracy_90d")
            .order("audit_date", desc=True)
            .execute()
        )
        seen: set[str] = set()
        for row in (resp.data or []):
            name = row.get("agent_name", "")
            acc  = row.get("accuracy_90d")
            if name and acc is not None and name not in seen:
                trust = max(
                    TRUST_MIN,
                    min(TRUST_MAX, float(acc) / DEFAULT_ACCURACY_BASELINE),
                )
                trust_scores[name] = round(trust, 4)
                seen.add(name)
    except Exception as exc:
        log.warning("get_agent_trust_scores failed: %s", exc)

    return trust_scores


# ─────────────────────────────────────────────────────────────────────────────
# ── MULTI-STAGE HALLUCINATION PIPELINE ────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class ConsistencyResult:
    """Outcome of the Stage-1 self-consistency check for one agent."""
    agent_name:      str
    symbol:          str
    signals:         list   # list[str] — signal returned on each re-run
    scores:          list   # list[float] — score returned on each re-run (may be shorter)
    agreement:       float  # 0.0–1.0  (fraction of runs matching the majority signal)
    score_cv:        float  # coefficient of variation of scores (std / mean)
    is_inconsistent: bool
    flag_reason:     str = ""


@dataclass
class ContradictionResult:
    """Outcome of the Stage-2 cross-agent contradiction check for one pair."""
    agent_a:              str
    agent_b:              str
    signal_a:             str
    signal_b:             str
    severity:             str   # "CRITICAL" | "MODERATE" | "LOW"
    is_hard_contradiction: bool
    reconciled:           bool = False
    reconciliation_note:  str  = ""


@dataclass
class ClaimVerification:
    """Outcome of the Stage-3 claim-grounding check for one numerical claim."""
    agent_name:    str
    claim_name:    str    # "rsi", "pe", "current_price", etc.
    claimed_value: float
    actual_value:  float
    deviation_pct: float
    is_grounded:   bool   # True if deviation_pct <= GROUNDING_TOLERANCE_PCT


@dataclass
class PipelineResult:
    """Aggregated result from all three hallucination-detection stages."""
    symbol:                str
    consistency_results:   list = field(default_factory=list)   # list[ConsistencyResult]
    contradiction_results: list = field(default_factory=list)   # list[ContradictionResult]
    claim_verifications:   list = field(default_factory=list)   # list[ClaimVerification]
    confidence_adjustment: int  = 0      # net negative pts to apply to recommendation confidence
    summary_flags:         list = field(default_factory=list)   # list[str]
    duration_seconds:      float = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Stage 1: Self-consistency check
# ─────────────────────────────────────────────────────────────────────────────

_AGENT_MODULE_MAP: dict[str, str] = {
    "technical":          "agents.technical",
    "fundamental":        "agents.fundamental",
    "sentiment":          "agents.sentiment",
    "macro":              "agents.macro",
    "institutional":      "agents.institutional",
    "sector_valuation":   "agents.sector_valuation",
    "commodities":        "agents.commodities",
    "historical_rag":     "agents.historical_rag",
    "discovery_screener": "agents.discovery_screener",
    "warren_bot":         "agents.warren_bot",
}


def _import_agent_fn(agent_name: str):
    """
    Import and return the analyse(symbol) callable for *agent_name*.
    Returns None if the module cannot be imported or has no analyse() function.
    """
    module_path = _AGENT_MODULE_MAP.get(agent_name)
    if not module_path:
        return None
    try:
        import importlib
        mod = importlib.import_module(module_path)
        return getattr(mod, "analyse", None)
    except Exception as exc:
        log.debug("Could not import agent '%s' (%s): %s", agent_name, module_path, exc)
        return None


def _run_agent_once(agent_fn, symbol: str) -> tuple:
    """
    Call agent_fn(symbol) once.  Returns (signal: str, score: float|None).
    Falls back to ("ERROR", None) on any exception.
    """
    try:
        result = agent_fn(symbol)
        if isinstance(result, dict):
            signal = str(result.get("signal", "NO_DATA")).upper()
            raw_score = result.get("score")
            score  = float(raw_score) if raw_score is not None else None
            return signal, score
        return "NO_DATA", None
    except Exception as exc:
        log.debug("Agent run error in consistency check: %s", exc)
        return "ERROR", None


def check_self_consistency(
    symbol:     str,
    agent_name: str,
    *,
    reruns: int = CONSISTENCY_RERUNS,
) -> Optional["ConsistencyResult"]:
    """
    Re-run *agent_name* up to *reruns* times in parallel threads and compare the
    resulting signals and scores.

    Flags as inconsistent when EITHER:
      • Signal agreement < CONSISTENCY_MIN_AGREEMENT (< 67% match majority signal)
      • Score coefficient of variation > CONSISTENCY_SCORE_CV_THRESHOLD (> 30%)

    Deterministic agents (technical, fundamental) always agree — the check is a
    no-op for them but correctly passes through.  Stochastic / LLM-backed agents
    (sentiment, warren_bot commentary) may genuinely diverge.

    Returns None if the agent cannot be imported or all runs return ERROR/NO_DATA.
    """
    agent_fn = _import_agent_fn(agent_name)
    if agent_fn is None:
        log.debug("Skipping consistency check — agent not importable: %s", agent_name)
        return None

    signals: list[str]  = []
    scores:  list[float] = []

    with ThreadPoolExecutor(max_workers=reruns, thread_name_prefix=f"consist_{agent_name}") as pool:
        futures = [pool.submit(_run_agent_once, agent_fn, symbol) for _ in range(reruns)]
        for fut in as_completed(futures):
            try:
                sig, score = fut.result(timeout=90)
                if sig not in ("ERROR", "NO_DATA"):
                    signals.append(sig)
                if score is not None:
                    scores.append(score)
            except Exception as exc:
                log.debug("Consistency future error [%s/%s]: %s", agent_name, symbol, exc)

    if not signals:
        return None

    # Agreement = fraction of runs matching the plurality signal
    majority_signal, majority_count = Counter(signals).most_common(1)[0]
    agreement = majority_count / len(signals)

    # Coefficient of variation of scores
    score_cv = 0.0
    if len(scores) >= 2:
        mean_s = statistics.mean(scores)
        std_s  = statistics.stdev(scores)
        score_cv = std_s / mean_s if mean_s != 0 else 0.0

    flags: list[str] = []
    if agreement < CONSISTENCY_MIN_AGREEMENT:
        flags.append(
            f"signal agreement {agreement:.0%} < {CONSISTENCY_MIN_AGREEMENT:.0%}  "
            f"(runs: {signals})"
        )
    if score_cv > CONSISTENCY_SCORE_CV_THRESHOLD:
        flags.append(
            f"score CV {score_cv:.2f} > {CONSISTENCY_SCORE_CV_THRESHOLD:.2f}  "
            f"(scores: {[round(s, 1) for s in scores]})"
        )

    return ConsistencyResult(
        agent_name      = agent_name,
        symbol          = symbol,
        signals         = signals,
        scores          = scores,
        agreement       = round(agreement, 4),
        score_cv        = round(score_cv, 4),
        is_inconsistent = len(flags) > 0,
        flag_reason     = "; ".join(flags),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2: Cross-agent contradiction detector
# ─────────────────────────────────────────────────────────────────────────────

# Severity of a contradiction between a known agent pair
_AGENT_PAIR_SEVERITY: dict[frozenset, str] = {
    frozenset({"technical",   "fundamental"}):  "CRITICAL",
    frozenset({"technical",   "macro"}):         "MODERATE",
    frozenset({"sentiment",   "institutional"}): "MODERATE",
    frozenset({"fundamental", "historical_rag"}):"MODERATE",
}

# Signal sets that constitute a "hard" contradiction (directly opposing views)
_HARD_CONTRADICTION_PAIRS: list[frozenset] = [
    frozenset({"BUY",        "AVOID"}),
    frozenset({"BUY",        "SELL"}),
    frozenset({"STRONG_BUY", "AVOID"}),
    frozenset({"STRONG_BUY", "SELL"}),
]


def _llm_reconcile_contradiction(
    symbol:   str,
    agent_a:  str, signal_a: str, detail_a: dict,
    agent_b:  str, signal_b: str, detail_b: dict,
) -> tuple[bool, str]:
    """
    Ask Claude Haiku whether the contradiction between agent_a and agent_b is
    reconcilable (i.e., explainable by different time horizons / evidence scope).

    Returns (reconciled: bool, explanation: str).
    Falls back to (False, reason_str) when the API is unavailable.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return False, "ANTHROPIC_API_KEY not set — LLM reconciliation skipped"

    prompt = (
        f"Stock: {symbol}\n"
        f"Agent '{agent_a}' (signal: {signal_a}) context:\n"
        f"{str(detail_a)[:400]}\n\n"
        f"Agent '{agent_b}' (signal: {signal_b}) context:\n"
        f"{str(detail_b)[:400]}\n\n"
        "These two agents have opposing signals. "
        "Is this contradiction RECONCILABLE — i.e. can both views be correct "
        "if they are analysing different time horizons, risk dimensions, or "
        "non-overlapping evidence? "
        "Reply RECONCILABLE or UNRECONCILABLE on the first line, "
        "then one sentence of explanation."
    )

    try:
        import anthropic
        client_llm = anthropic.Anthropic(api_key=api_key)
        resp = client_llm.messages.create(
            model="claude-haiku-4-5",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        text = (resp.content[0].text or "").strip() if resp.content else ""
        reconciled = text.upper().startswith("RECONCILABLE")
        note_line  = text.split("\n", 1)[-1].strip() if "\n" in text else text
        return reconciled, note_line
    except Exception as exc:
        log.debug("LLM reconciliation error: %s", exc)
        return False, f"LLM unavailable: {exc}"


def detect_cross_agent_contradictions(
    symbol:        str,
    agent_signals: dict,
    *,
    use_llm_reconcile: bool = True,
) -> list[ContradictionResult]:
    """
    Inspect all agent pairs defined in _AGENT_PAIR_SEVERITY and flag those
    where the signals are in direct opposition (BUY vs SELL/AVOID).

    For CRITICAL hard contradictions, optionally calls Claude Haiku to determine
    whether the contradiction is reconcilable.

    Args:
        symbol:            NSE display symbol
        agent_signals:     dict mapping agent_name → signal_data
        use_llm_reconcile: whether to call Haiku for CRITICAL hard contradictions

    Returns:
        List of ContradictionResult — one per flagged pair.
    """
    results: list[ContradictionResult] = []

    _BUY_SIGNALS  = {"BUY", "STRONG_BUY"}
    _SELL_SIGNALS = {"SELL", "AVOID"}

    def _sig(agent_data) -> str:
        if isinstance(agent_data, dict):
            return str(agent_data.get("signal", "NO_DATA")).upper()
        return str(agent_data or "NO_DATA").upper()

    def _detail(agent_data) -> dict:
        return agent_data if isinstance(agent_data, dict) else {}

    for pair_set, severity in _AGENT_PAIR_SEVERITY.items():
        pair_list = list(pair_set)
        if len(pair_list) != 2:
            continue
        agent_a, agent_b = pair_list[0], pair_list[1]

        if agent_a not in agent_signals or agent_b not in agent_signals:
            continue

        signal_a = _sig(agent_signals[agent_a])
        signal_b = _sig(agent_signals[agent_b])

        if signal_a in ("NO_DATA", "ERROR") or signal_b in ("NO_DATA", "ERROR"):
            continue

        # Determine if signals are in direct opposition
        sig_pair = frozenset({signal_a, signal_b})
        is_hard  = sig_pair in _HARD_CONTRADICTION_PAIRS

        # For softer pairs, flag only when one is clearly BUY and the other SELL/AVOID
        is_opposing = (
            (signal_a in _BUY_SIGNALS and signal_b in _SELL_SIGNALS) or
            (signal_b in _BUY_SIGNALS and signal_a in _SELL_SIGNALS)
        )

        if not is_hard and not is_opposing:
            continue   # not a meaningful contradiction (e.g. BUY vs HOLD)

        reconciled = False
        note       = ""

        if is_hard and severity == "CRITICAL" and use_llm_reconcile:
            detail_a = _detail(agent_signals[agent_a])
            detail_b = _detail(agent_signals[agent_b])
            reconciled, note = _llm_reconcile_contradiction(
                symbol,
                agent_a, signal_a, detail_a,
                agent_b, signal_b, detail_b,
            )
            log.debug(
                "[%s] %s↔%s contradiction reconciled=%s  note=%s",
                symbol, agent_a, agent_b, reconciled, note,
            )

        results.append(ContradictionResult(
            agent_a               = agent_a,
            agent_b               = agent_b,
            signal_a              = signal_a,
            signal_b              = signal_b,
            severity              = severity,
            is_hard_contradiction = is_hard,
            reconciled            = reconciled,
            reconciliation_note   = note,
        ))

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3: Claim-grounding verifier
# ─────────────────────────────────────────────────────────────────────────────

def _extract_claims_technical(detail: dict) -> dict[str, float]:
    """
    Pull verifiable numerical claims from a technical agent detail dict.
    Returns {claim_name: claimed_value}; missing / non-numeric fields are skipped.

    Known paths (from agents/technical.py):
        detail["indicators"]["rsi"]
        detail["momentum"]["rsi"]           (fallback)
        detail["indicators"]["current_price"]
        detail["indicators"]["ema20"]
        detail["volume_confirmation"]["volume_vs_avg"]
    """
    claims: dict[str, float] = {}
    indicators  = detail.get("indicators")  or {}
    momentum    = detail.get("momentum")    or {}
    volume_conf = detail.get("volume_confirmation") or {}

    for dest, sources in {
        "rsi":          [indicators.get("rsi"),          momentum.get("rsi")],
        "current_price":[indicators.get("current_price")],
        "ema20":        [indicators.get("ema20")],
        "volume_ratio": [volume_conf.get("volume_vs_avg")],
    }.items():
        for src in sources:
            if src is not None:
                try:
                    claims[dest] = float(src)
                    break
                except (TypeError, ValueError):
                    continue

    return claims


def _extract_claims_fundamental(detail: dict) -> dict[str, float]:
    """
    Pull verifiable numerical claims from a fundamental agent detail dict.
    Returns {claim_name: claimed_value}.

    Known paths (from agents/fundamental.py):
        detail["raw_metrics"]["pe"]          (also detail["profitability"]["pe"])
        detail["raw_metrics"]["current_price"]
        detail["raw_metrics"]["revenue_growth"]
        detail["raw_metrics"]["debt_equity"]
        detail["raw_metrics"]["roce"]
    """
    claims: dict[str, float] = {}
    raw    = detail.get("raw_metrics")   or {}
    profit = detail.get("profitability") or {}

    for dest, sources in {
        "pe":             [raw.get("pe"),             profit.get("pe")],
        "current_price":  [raw.get("current_price")],
        "revenue_growth": [raw.get("revenue_growth")],
        "debt_equity":    [raw.get("debt_equity")],
        "roce":           [raw.get("roce")],
    }.items():
        for src in sources:
            if src is not None:
                try:
                    claims[dest] = float(src)
                    break
                except (TypeError, ValueError):
                    continue

    return claims


def _fetch_technical_actuals(yf_symbol: str) -> dict[str, float]:
    """
    Fetch fresh technical values from yfinance (1-month daily history).
    Returns {claim_name: actual_value}; missing values are omitted.

    Computed here:
        current_price  — most recent daily close
        rsi            — 14-period Wilder's RSI (EWM with com=13)
        ema20          — 20-period EMA of closes
        volume_ratio   — last session volume ÷ 20-day average volume
    """
    actuals: dict[str, float] = {}
    try:
        import yfinance as yf
        df = yf.Ticker(yf_symbol).history(period="1mo", interval="1d", auto_adjust=True)
        if df.empty:
            return actuals

        closes = df["Close"].astype(float)

        # Current price
        actuals["current_price"] = float(closes.iloc[-1])

        # RSI — 14-period Wilder's using exponential moving average
        if len(closes) >= 15:
            delta    = closes.diff()
            gain     = delta.where(delta > 0, 0.0)
            loss     = (-delta).where(delta < 0, 0.0)
            avg_gain = gain.ewm(com=13, adjust=False).mean()
            avg_loss = loss.ewm(com=13, adjust=False).mean()
            rs       = avg_gain / avg_loss.replace(0, float("nan"))
            rsi_s    = 100.0 - (100.0 / (1.0 + rs))
            actuals["rsi"] = round(float(rsi_s.iloc[-1]), 2)

        # EMA20
        if len(closes) >= 20:
            actuals["ema20"] = round(
                float(closes.ewm(span=20, adjust=False).mean().iloc[-1]), 4
            )

        # Volume ratio
        if "Volume" in df.columns and len(df) >= 2:
            last_vol = float(df["Volume"].iloc[-1])
            avg_vol  = float(df["Volume"].tail(20).mean())
            if avg_vol > 0:
                actuals["volume_ratio"] = round(last_vol / avg_vol, 4)

    except Exception as exc:
        log.debug("_fetch_technical_actuals failed for %s: %s", yf_symbol, exc)

    return actuals


def _fetch_fundamental_actuals(yf_symbol: str) -> dict[str, float]:
    """
    Fetch fresh fundamental values from yfinance .info for *yf_symbol*.
    Returns {claim_name: actual_value}; missing fields are omitted.

    Notes:
        • revenue_growth from yf is a decimal (0.18 = 18%); converted to % here.
        • ROCE is not directly exposed by yfinance — claim is skipped if absent.
    """
    actuals: dict[str, float] = {}
    try:
        import yfinance as yf
        info = yf.Ticker(yf_symbol).info or {}

        # PE (trailing preferred, forward as fallback)
        pe = info.get("trailingPE") or info.get("forwardPE")
        if pe is not None:
            actuals["pe"] = float(pe)

        # Current price
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        if price is not None:
            actuals["current_price"] = float(price)

        # Revenue growth — yf decimal → convert to %
        rev_g = info.get("revenueGrowth")
        if rev_g is not None:
            actuals["revenue_growth"] = round(float(rev_g) * 100.0, 2)

        # Debt / equity
        de = info.get("debtToEquity")
        if de is not None:
            actuals["debt_equity"] = float(de)

        # ROCE — not in yfinance .info; skip (will be absent from actuals)

    except Exception as exc:
        log.debug("_fetch_fundamental_actuals failed for %s: %s", yf_symbol, exc)

    return actuals


def verify_claim_grounding(
    symbol:       str,
    agent_name:   str,
    agent_detail: dict,
    yf_symbol:    Optional[str] = None,
    *,
    tolerance_pct: float = GROUNDING_TOLERANCE_PCT,
) -> list[ClaimVerification]:
    """
    Compare numerical claims in *agent_detail* against fresh data fetched from
    yfinance.  Flags claims where the absolute % deviation exceeds *tolerance_pct*.

    Currently supports agents: "technical", "fundamental".
    Returns an empty list for any other agent (silently skipped).

    Args:
        symbol:       NSE display symbol (e.g. "RELIANCE")
        agent_name:   "technical" or "fundamental"
        agent_detail: The detail sub-dict from agent_signals[agent_name]
        yf_symbol:    yfinance ticker — defaults to symbol + ".NS"
        tolerance_pct:Max % deviation before flagging (default 5%)

    Returns:
        List of ClaimVerification (one per verified claim; unverifiable claims omitted).
    """
    verifications: list[ClaimVerification] = []

    if not isinstance(agent_detail, dict):
        return verifications

    _yf = yf_symbol or f"{symbol.upper()}.NS"

    if agent_name == "technical":
        claimed = _extract_claims_technical(agent_detail)
        actuals = _fetch_technical_actuals(_yf)
    elif agent_name == "fundamental":
        claimed = _extract_claims_fundamental(agent_detail)
        actuals = _fetch_fundamental_actuals(_yf)
    else:
        return verifications   # unsupported agent — skip silently

    for claim_name, claimed_val in claimed.items():
        actual_val = actuals.get(claim_name)
        if actual_val is None:
            continue   # no fresh data available for this claim

        # Trivial case: both zero
        if claimed_val == 0 and actual_val == 0:
            verifications.append(ClaimVerification(
                agent_name    = agent_name,
                claim_name    = claim_name,
                claimed_value = 0.0,
                actual_value  = 0.0,
                deviation_pct = 0.0,
                is_grounded   = True,
            ))
            continue

        denom = abs(actual_val) if actual_val != 0 else abs(claimed_val)
        if denom == 0:
            deviation_pct = 0.0
        else:
            deviation_pct = abs(claimed_val - actual_val) / denom * 100.0

        is_grounded = deviation_pct <= tolerance_pct

        verifications.append(ClaimVerification(
            agent_name    = agent_name,
            claim_name    = claim_name,
            claimed_value = round(claimed_val, 4),
            actual_value  = round(actual_val, 4),
            deviation_pct = round(deviation_pct, 2),
            is_grounded   = is_grounded,
        ))

        if not is_grounded:
            log.info(
                "[%s] CLAIM MISMATCH  agent=%-14s  %-16s  "
                "claimed=%.4f  actual=%.4f  dev=%.1f%%",
                symbol, agent_name, claim_name,
                claimed_val, actual_val, deviation_pct,
            )

    return verifications


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline entry point
# ─────────────────────────────────────────────────────────────────────────────

def run_multi_stage_pipeline(
    symbol:        str,
    agent_signals: dict,
    *,
    run_consistency:    bool = True,
    run_contradictions: bool = True,
    run_grounding:      bool = True,
    use_llm_reconcile:  bool = True,
    dry_run:            bool = False,
) -> PipelineResult:
    """
    Execute all three hallucination-detection stages against a single set of
    agent_signals (as produced by the orchestrator).

    Confidence adjustments applied to PipelineResult.confidence_adjustment:
        Stage 1: -5  per inconsistent agent
        Stage 2: -15 per CRITICAL   unreconciled contradiction
                 -5  per MODERATE   unreconciled contradiction
        Stage 3: -3  per ungrounded claim

    Args:
        symbol:             NSE display symbol (e.g. "RELIANCE")
        agent_signals:      dict from recommendation["agent_signals"] (warren_bot excluded)
        run_consistency:    enable Stage 1
        run_contradictions: enable Stage 2
        run_grounding:      enable Stage 3
        use_llm_reconcile:  call Haiku for CRITICAL hard contradictions in Stage 2
        dry_run:            pretty-print the summary table to stdout

    Returns:
        PipelineResult dataclass with all results and net confidence_adjustment.
    """
    import time as _time
    t0 = _time.time()

    result = PipelineResult(symbol=symbol)

    # ── Stage 1: Self-consistency ─────────────────────────────────────────────
    if run_consistency:
        # warren_bot has its own consistency model; skip it here
        agent_names = [k for k in agent_signals if k != "warren_bot"]
        for aname in agent_names:
            cr = check_self_consistency(symbol, aname)
            if cr is None:
                continue
            result.consistency_results.append(cr)
            if cr.is_inconsistent:
                result.confidence_adjustment -= 5
                result.summary_flags.append(
                    f"Stage1/{aname}: inconsistent — {cr.flag_reason}"
                )

    # ── Stage 2: Cross-agent contradictions ──────────────────────────────────
    if run_contradictions:
        contradictions = detect_cross_agent_contradictions(
            symbol, agent_signals, use_llm_reconcile=use_llm_reconcile
        )
        result.contradiction_results.extend(contradictions)
        for c in contradictions:
            if not c.reconciled:
                adj = -15 if c.severity == "CRITICAL" else -5
                result.confidence_adjustment += adj
                result.summary_flags.append(
                    f"Stage2/{c.agent_a}↔{c.agent_b}: {c.severity} contradiction "
                    f"({c.signal_a} vs {c.signal_b}) — unreconciled"
                )

    # ── Stage 3: Claim grounding ──────────────────────────────────────────────
    if run_grounding:
        for aname in ("technical", "fundamental"):
            agent_data = agent_signals.get(aname)
            if not isinstance(agent_data, dict):
                continue
            # The detail payload may be nested under a "detail" key or be the dict itself
            detail = agent_data.get("detail") or agent_data
            verifications = verify_claim_grounding(symbol, aname, detail)
            result.claim_verifications.extend(verifications)
            for v in verifications:
                if not v.is_grounded:
                    result.confidence_adjustment -= 3
                    result.summary_flags.append(
                        f"Stage3/{aname}/{v.claim_name}: "
                        f"claimed {v.claimed_value} vs actual {v.actual_value} "
                        f"(dev {v.deviation_pct:.1f}%)"
                    )

    result.duration_seconds = round(_time.time() - t0, 2)

    if dry_run:
        _print_pipeline_summary(result)

    return result


def _print_pipeline_summary(result: PipelineResult) -> None:
    """Pretty-print a PipelineResult to stdout."""
    W = 70
    print()
    print("=" * W)
    print(f"  Multi-Stage Hallucination Pipeline — {result.symbol}")
    print("=" * W)

    # Stage 1
    n1 = len(result.consistency_results)
    bad1 = sum(1 for c in result.consistency_results if c.is_inconsistent)
    print(f"\n  Stage 1 — Self-Consistency  ({n1} agents, {bad1} inconsistent)")
    if n1:
        for cr in result.consistency_results:
            mark = "⚠  INCONSISTENT" if cr.is_inconsistent else "✓  ok"
            print(
                f"    {cr.agent_name:<22}  agree={cr.agreement:.0%}  "
                f"score_cv={cr.score_cv:.2f}  {mark}"
            )
            if cr.flag_reason:
                print(f"         ↳ {cr.flag_reason}")
    else:
        print("    (no agents checked)")

    # Stage 2
    n2 = len(result.contradiction_results)
    bad2 = sum(1 for c in result.contradiction_results if not c.reconciled)
    print(f"\n  Stage 2 — Cross-Agent Contradictions  ({n2} pairs, {bad2} unreconciled)")
    if n2:
        for c in result.contradiction_results:
            rec = "reconciled" if c.reconciled else "UNRECONCILED"
            print(
                f"    {c.agent_a} ({c.signal_a}) ↔ {c.agent_b} ({c.signal_b})"
                f"  [{c.severity}]  {rec}"
            )
            if c.reconciliation_note:
                print(f"         ↳ {c.reconciliation_note}")
    else:
        print("    (no contradictions found)")

    # Stage 3
    n3 = len(result.claim_verifications)
    bad3 = sum(1 for v in result.claim_verifications if not v.is_grounded)
    print(f"\n  Stage 3 — Claim Grounding  ({n3} claims, {bad3} ungrounded)")
    if n3:
        for v in result.claim_verifications:
            mark = "✓" if v.is_grounded else "⚠ MISMATCH"
            print(
                f"    {mark:<12}  {v.agent_name}/{v.claim_name:<18}  "
                f"claimed={v.claimed_value}  actual={v.actual_value}  "
                f"dev={v.deviation_pct:.1f}%"
            )
    else:
        print("    (no claims verified)")

    # Summary
    print(f"\n  Net confidence adjustment : {result.confidence_adjustment:+d} pts")
    if result.summary_flags:
        print(f"  Flags ({len(result.summary_flags)}) :")
        for flag in result.summary_flags:
            print(f"    • {flag}")
    print(f"\n  Pipeline completed in {result.duration_seconds:.1f}s")
    print("=" * W)
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Main run logic
# ─────────────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False) -> dict:
    """
    Weekly hallucination detection job.

    Steps:
      1. Load up to MAX_SAMPLE_RECS mature recommendations (horizon elapsed).
      2. For each rec, price-check entry vs horizon-end to evaluate agent
         directional accuracy.
      3. Compute accuracy_90d per agent.
      4. Compute per-agent hallucination_rate from stored gov_check data.
      5. Upsert agent_performance; emit alerts where hallucination_rate > 1.5%.

    Returns summary dict with keys:
        recs_sampled, agents_evaluated, errors, duration_seconds
    """
    t0     = time.time()
    errors: list[str] = []

    log.info("Hallucination detector: starting weekly audit (dry_run=%s)", dry_run)

    client = _supabase()
    if not client and not dry_run:
        log.error("Supabase unavailable — cannot run hallucination detector")
        return {
            "recs_sampled":      0,
            "agents_evaluated":  0,
            "trust_scores":      {},
            "errors":            ["Supabase unavailable"],
            "duration_seconds":  round(time.time() - t0, 2),
        }

    # ── Step 1: Fetch mature recommendations ─────────────────────────────────
    today          = date.today()
    cutoff_str     = today.isoformat()  # created_at + horizon_days <= today

    recs: list[dict] = []
    if client:
        try:
            # Fetch recent recs ordered by creation date; filter mature ones in Python
            # (Supabase doesn't support computed column filtering directly)
            resp = (
                client.table("recommendations")
                .select(
                    "id, symbol, created_at, horizon_days, "
                    "agent_signals, gov_check, action"
                )
                .order("created_at", desc=True)
                .limit(MAX_SAMPLE_RECS * 3)   # fetch extra; filter after
                .execute()
            )
            all_recs = resp.data or []
        except Exception as exc:
            log.error("Failed to load recommendations: %s", exc)
            return {
                "recs_sampled":     0,
                "agents_evaluated": 0,
                "errors":           [str(exc)],
                "duration_seconds": round(time.time() - t0, 2),
            }

        # Filter to mature recs (horizon has elapsed)
        for rec in all_recs:
            if len(recs) >= MAX_SAMPLE_RECS:
                break
            try:
                created_str  = rec.get("created_at", "")
                if not created_str:
                    continue
                created_dt   = datetime.fromisoformat(
                    str(created_str).replace("Z", "+00:00")
                )
                horizon_days = int(rec.get("horizon_days") or 180)
                horizon_date = created_dt.date() + timedelta(days=horizon_days)
                if horizon_date <= today:
                    recs.append(rec)
            except (ValueError, TypeError):
                continue
    else:
        # dry_run with no client — nothing to fetch but still report
        log.info("[DRY RUN] No Supabase client; skipping DB fetch")

    log.info("Sampled %d mature recommendations for accuracy audit", len(recs))

    if not recs:
        log.info("No mature recommendations found — audit complete with no updates")
        return {
            "recs_sampled":     0,
            "agents_evaluated": 0,
            "trust_scores":     {},
            "errors":           errors,
            "duration_seconds": round(time.time() - t0, 2),
        }

    # ── Step 2: Evaluate directional accuracy per agent ───────────────────────
    all_evaluations: list[dict[str, Optional[bool]]] = []
    for rec in recs:
        try:
            evals = _evaluate_rec(rec)
            if evals:
                all_evaluations.append(evals)
        except Exception as exc:
            err = f"rec {rec.get('id', '?')} [{rec.get('symbol', '?')}]: {exc}"
            log.warning("Evaluation error — %s", err)
            errors.append(err)

    # ── Step 3: Compute accuracy_90d per agent ────────────────────────────────
    accuracy_by_agent = _compute_accuracy(all_evaluations)

    # ── Step 4: Compute hallucination rates from gov_check data ───────────────
    hallucination_rates = _compute_hallucination_rates(recs)

    # ── Step 5: Collect all agent names encountered in either metric ──────────
    all_agents = sorted(
        set(accuracy_by_agent.keys()) | set(hallucination_rates.keys())
    )

    if not all_agents:
        log.info("No agent data found in sampled recs — nothing to update")
        return {
            "recs_sampled":     len(recs),
            "agents_evaluated": 0,
            "trust_scores":     {},
            "errors":           errors,
            "duration_seconds": round(time.time() - t0, 2),
        }

    # ── Step 6: Compute trust scores for all known agents ────────────────────
    trust_scores = get_agent_trust_scores(client)
    log.info("Agent trust scores: %s", {k: f"{v:.4f}" for k, v in trust_scores.items()})

    # ── Step 7: Upsert and alert ──────────────────────────────────────────────
    print()
    print("-" * 70)
    print(f"  Hallucination Audit — {today}   ({len(recs)} recs sampled)")
    print("-" * 70)

    for agent in all_agents:
        acc_data  = accuracy_by_agent.get(agent)
        hall_rate = hallucination_rates.get(agent)
        trust     = trust_scores.get(agent, 1.0)

        acc_90d = acc_data["accuracy_90d"] if acc_data else None

        # Log summary
        acc_str  = f"{acc_90d:.1f}%" if acc_90d is not None else "N/A"
        hall_str = f"{hall_rate:.2f}%" if hall_rate is not None else "N/A"
        signals  = f"{acc_data['correct']}/{acc_data['total']}" if acc_data else "N/A"

        print(
            f"  {agent:<18}  accuracy_90d={acc_str:<8}  "
            f"signals={signals:<8}  hallucination_rate={hall_str}  trust={trust:.2f}"
        )

        _upsert_agent_performance(
            client, agent, acc_90d, hall_rate, dry_run
        )

        if hall_rate is not None:
            _emit_hallucination_alert(client, agent, hall_rate, dry_run, trust_score=trust)

    print("-" * 70)
    print()

    log.info(
        "Hallucination audit complete — %d agents evaluated in %.1fs",
        len(all_agents), time.time() - t0,
    )

    return {
        "recs_sampled":     len(recs),
        "agents_evaluated": len(all_agents),
        "accuracy_by_agent": {
            a: d["accuracy_90d"] for a, d in accuracy_by_agent.items()
        },
        "hallucination_rates": hallucination_rates,
        "trust_scores":     trust_scores,
        "errors":           errors,
        "duration_seconds": round(time.time() - t0, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# APScheduler + CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bharat Intelligence Hallucination Detector",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python governance/hallucination_detector.py                # start Sunday 08:00 IST scheduler
  python governance/hallucination_detector.py --run-now      # run immediately (writes to DB)
  python governance/hallucination_detector.py --run-now --dry # audit without DB writes
        """,
    )
    parser.add_argument(
        "--run-now", action="store_true",
        help="Execute the weekly accuracy audit immediately instead of waiting for the schedule",
    )
    parser.add_argument(
        "--dry", action="store_true",
        help="Dry run: perform analysis but skip all Supabase writes",
    )
    parser.add_argument(
        "--pipeline", metavar="SYMBOL",
        help=(
            "Run the multi-stage hallucination pipeline for SYMBOL "
            "(e.g. --pipeline RELIANCE).  "
            "Requires REACT_APP_API_URL / agent imports to be available.  "
            "Add --dry to print results only."
        ),
    )
    parser.add_argument(
        "--no-llm", action="store_true",
        help="Disable Claude Haiku LLM reconciliation in Stage 2 (faster, no API cost)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    # ── --pipeline SYMBOL ────────────────────────────────────────────────────
    if args.pipeline:
        symbol = args.pipeline.strip().upper()
        log.info("Running multi-stage hallucination pipeline for %s …", symbol)

        # Build a minimal agent_signals dict by running the two grounding agents live
        # (Stage 1 & 2 also run agents internally; Stage 3 needs actual detail dicts)
        agent_signals: dict = {}

        for aname in ("technical", "fundamental"):
            fn = _import_agent_fn(aname)
            if fn is None:
                log.warning("Agent '%s' not importable — skipping", aname)
                continue
            try:
                res = fn(symbol)
                if isinstance(res, dict):
                    agent_signals[aname] = res
            except Exception as exc:
                log.warning("Agent '%s' failed: %s", aname, exc)

        if not agent_signals:
            log.error("No agents produced results for %s — pipeline aborted", symbol)
            sys.exit(1)

        pipeline_result = run_multi_stage_pipeline(
            symbol,
            agent_signals,
            use_llm_reconcile = not args.no_llm,
            dry_run            = True,    # always print for CLI invocation
        )

        sys.exit(0 if not pipeline_result.summary_flags else 1)

    # ── --run-now ─────────────────────────────────────────────────────────────
    if args.run_now:
        result = run(dry_run=args.dry)
        log.info("Audit result: %s", result)
        if result.get("errors"):
            log.warning(
                "%d error(s): %s",
                len(result["errors"]),
                "; ".join(result["errors"][:5]),
            )
        return

    # ── Scheduled mode: Sunday 08:00 IST ─────────────────────────────────────
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        log.error("apscheduler not installed — run: pip install apscheduler")
        sys.exit(1)

    try:
        from zoneinfo import ZoneInfo
        IST = ZoneInfo("Asia/Kolkata")
    except ImportError:
        import pytz
        IST = pytz.timezone("Asia/Kolkata")

    def _job() -> None:
        log.info("Weekly hallucination audit triggered by scheduler...")
        run(dry_run=False)

    scheduler = BlockingScheduler(timezone=IST)
    scheduler.add_job(
        _job,
        CronTrigger(day_of_week="sun", hour=8, minute=0, timezone=IST),
        id="weekly_hallucination_audit",
        name="Bharat Intelligence Weekly Hallucination Detector",
        max_instances=1,
        coalesce=True,
    )

    log.info("-" * 60)
    log.info("  Bharat Intelligence Hallucination Detector")
    log.info("  Schedule: every Sunday at 08:00 IST")
    log.info("  Press Ctrl+C to stop")
    log.info("-" * 60)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped cleanly")


if __name__ == "__main__":
    main()
