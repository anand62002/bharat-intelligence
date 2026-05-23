"""
governance/performance_tracker.py — Bharat Intelligence: Weekly Outcome Tracker
================================================================================
Runs weekly (Sunday 09:00 IST) to evaluate open recommendations against live
market prices, track real-world outcomes, update per-agent accuracy scores, and
auto-generate enhancement proposals when accuracy drops below threshold for two
consecutive weeks.

Algorithm
─────────
  1. Fetch all recommendations older than MIN_AGE_DAYS (30) from Supabase where
     outcome is NULL or IN_PROGRESS (still open / not yet resolved).
  2. For each rec, fetch the current market price via yfinance.
  3. Determine outcome:
       SUCCESS      → current_price >= target            (price hit target)
       PARTIAL_FAIL → current_price <= stoploss          (stop-loss triggered)
       EXPIRED      → horizon elapsed, neither hit       (time ran out)
       IN_PROGRESS  → horizon not elapsed, neither hit   (still open)
  4. Write outcome back to recommendations (outcome, outcome_price, outcome_checked_at).
  5. For resolved recs (SUCCESS / PARTIAL_FAIL / EXPIRED), evaluate which agent
     signals were predictive and update accuracy scores in agent_performance.
  6. For each agent, check if accuracy_90d < ACCURACY_THRESHOLD for the last
     CONSECUTIVE_WEEKS consecutive audit weeks. If so, generate an enhancement
     proposal and save it to enhancement_proposals (deduplication on title).
  7. Emit portfolio_alerts for recs that just hit SUCCESS or PARTIAL_FAIL.

Outcome ↔ signal mapping
────────────────────────
  BUY  on SUCCESS      → correct     SELL on PARTIAL_FAIL → correct
  BUY  on PARTIAL_FAIL → wrong       SELL on SUCCESS      → wrong
  BUY  on EXPIRED      → wrong       HOLD on EXPIRED      → correct (stable)
  HOLD on SUCCESS      → wrong       HOLD on PARTIAL_FAIL → wrong
  NO_DATA / NEUTRAL    → excluded

Entry points
────────────
  run(dry_run) -> dict          Weekly job callable; also invoked by CLI.

Usage
─────
  python governance/performance_tracker.py --run-now
  python governance/performance_tracker.py --run-now --dry
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
MIN_AGE_DAYS         = 30     # only evaluate recs at least this old
MAX_RECS_PER_RUN     = 100    # cap per weekly run
ACCURACY_THRESHOLD   = 70.0   # below this triggers proposal consideration
CONSECUTIVE_WEEKS    = 2      # weeks below threshold required to generate proposal

# Outcome labels
SUCCESS      = "SUCCESS"
PARTIAL_FAIL = "PARTIAL_FAIL"
IN_PROGRESS  = "IN_PROGRESS"
EXPIRED      = "EXPIRED"


# ── Enhancement proposal templates ────────────────────────────────────────────

_PROPOSAL_TEMPLATES: dict[str, dict] = {
    "technical": {
        "title":       "Recalibrate technical agent: adjust indicator weights and lookback windows",
        "rationale":   (
            "Technical agent accuracy dropped to {accuracy:.1f}% for {weeks} consecutive audit "
            "weeks (threshold: {threshold}%). Indicator weights, EMA periods, or RSI thresholds "
            "may be mis-calibrated for the current volatility regime."
        ),
        "cost_impact": "low",
        "is_paid":     False,
        "steps": [
            "Review RSI thresholds — consider 75/25 for trending markets vs default 70/30.",
            "Audit EMA crossover periods (20/50) against recent Nifty 500 backtests.",
            "Gate BUY/SELL signals on ADX > 25 to filter noise in sideways markets.",
            "Backtest proposed changes against last 90 days of closed recommendations.",
            "Deploy to shadow branch, run 2 weeks in parallel, compare accuracy delta.",
        ],
    },
    "fundamental": {
        "title":       "Expand fundamental agent: refresh sector PE benchmarks and scoring weights",
        "rationale":   (
            "Fundamental agent accuracy of {accuracy:.1f}% (threshold: {threshold}%) suggests "
            "sector PE benchmarks may be stale, or scoring logic may need rebalancing for "
            "the current earnings cycle."
        ),
        "cost_impact": "low",
        "is_paid":     False,
        "steps": [
            "Update SECTOR_PE_MAP with latest 5-year median PEs from NSE sector indices.",
            "Review EBITDA margin thresholds per sector (services vs manufacturing differ).",
            "Audit debt/equity weight — may over-penalise capital-intensive sectors.",
            "Add trailing 12-month revenue momentum as a positive scoring factor.",
            "Validate changes against 20 recent closed recommendations.",
        ],
    },
    "sentiment": {
        "title":       "Enhance sentiment agent: add social signal sources and tune decay weights",
        "rationale":   (
            "Sentiment agent accuracy of {accuracy:.1f}% suggests news signals are not reliably "
            "predicting outcomes. Additional sources or exponential decay weighting may help."
        ),
        "cost_impact": "medium",
        "is_paid":     True,
        "steps": [
            "Integrate Reddit India investing community sentiment (free API).",
            "Add management tone analysis from Screener.in earnings call transcripts.",
            "Replace binary headline sentiment with a 7-day exponentially-decayed score.",
            "Evaluate paid Bloomberg/Refinitiv feed for institutional-grade news flow.",
            "A/B test new sentiment score vs current over 30 trading days.",
        ],
    },
    "institutional": {
        "title":       "Improve institutional agent: extend flow window and add F&O open interest",
        "rationale":   (
            "Institutional agent accuracy of {accuracy:.1f}% (threshold: {threshold}%). "
            "The current 5/10-session FII/DII window may miss longer-term flow cycles."
        ),
        "cost_impact": "low",
        "is_paid":     False,
        "steps": [
            "Extend FII/DII window from 10 to 22 trading sessions (≈1 calendar month).",
            "Add NSE F&O open interest change as a directional confirmation signal.",
            "Incorporate NSE bulk/block deal data for large-cap positioning signals.",
            "Weight DII flows more heavily for small/mid-cap signals (DII dominates there).",
            "Backtest on last 60 days of resolved recommendations.",
        ],
    },
    "macro": {
        "title":       "Refresh macro agent: add RBI policy cycle signals and global risk proxies",
        "rationale":   (
            "Macro agent accuracy of {accuracy:.1f}% indicates macro signals are not well "
            "correlated with outcomes. Policy-rate cycle and global risk proxies need updating."
        ),
        "cost_impact": "low",
        "is_paid":     False,
        "steps": [
            "Add RBI repo rate trend (rising/falling cycle) as a scored macro factor.",
            "Include India VIX level and 30-day trend in macro risk score.",
            "Add USD/INR 20-day momentum as a currency risk factor.",
            "Incorporate 10Y G-Sec yield spread vs repo rate.",
            "Consider increasing macro weight in composite score from 10% to 15%.",
        ],
    },
    "historical_rag": {
        "title":       "Enrich RAG database: add sector PE timeline and earnings-surprise events",
        "rationale":   (
            "Historical RAG agent accuracy of {accuracy:.1f}% suggests retrieved events lack "
            "relevance to current recommendations. The vector store needs richer recent data."
        ),
        "cost_impact": "medium",
        "is_paid":     False,
        "steps": [
            "Backfill sector_pe_snapshots for the last 3 years from NSE historical PE data.",
            "Add earnings-surprise events (beat/miss > 10%) for all Nifty 500 stocks.",
            "Ingest SEBI enforcement actions and regulatory changes as structured events.",
            "Re-embed all events with the latest embedding model for better retrieval.",
            "Increase top-k retrieval from 3 to 5 events per query.",
        ],
    },
    "commodities": {
        "title":       "Recalibrate commodities agent: update commodity-sector impact mappings",
        "rationale":   (
            "Commodities agent accuracy of {accuracy:.1f}% suggests commodity signals are not "
            "translating well to stock outcomes. Sector-impact mappings may need updating."
        ),
        "cost_impact": "low",
        "is_paid":     False,
        "steps": [
            "Differentiate crude oil impact for upstream vs downstream stocks.",
            "Update gold/silver sensitivity for jewellery vs financial-sector stocks.",
            "Add natural gas as an input-cost signal for fertiliser and chemicals.",
            "Recalibrate commodity weighting in composite score by sector.",
            "Validate new mappings against energy and materials sector recommendations.",
        ],
    },
    "_default": {
        "title":       "Review and improve {agent} agent accuracy",
        "rationale":   (
            "{agent} agent accuracy dropped to {accuracy:.1f}% for {weeks} consecutive weeks "
            "(threshold: {threshold}%). A detailed review of signal logic is recommended."
        ),
        "cost_impact": "medium",
        "is_paid":     False,
        "steps": [
            "Audit recent {agent} agent outputs against actual price outcomes.",
            "Identify systematic biases (e.g. consistently BUY-biased in bull markets).",
            "Review data source freshness and API reliability.",
            "Propose specific threshold or weight adjustments.",
            "Test changes in shadow mode for 2 weeks before deploying.",
        ],
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class OutcomeResult:
    """Summary returned by run()."""
    run_date:            str
    recs_evaluated:      int
    outcomes:            dict = field(default_factory=dict)   # {SUCCESS:N, PARTIAL_FAIL:N, ...}
    agents_updated:      int  = 0
    proposals_generated: int  = 0
    errors:              list = field(default_factory=list)
    dry_run:             bool = False
    duration_seconds:    float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


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


def _send_telegram(message: str, dry_run: bool = False) -> bool:
    """Send Telegram notification (re-uses same pattern as portfolio_monitor)."""
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    if dry_run:
        print(f"\n  [TELEGRAM DRY RUN]\n{message}\n")
        return True
    if not token or not chat_id:
        log.debug("Telegram not configured — skipping notification")
        return False
    try:
        url  = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        log.warning("Telegram send failed: %s", exc)
        return False


def _fetch_current_price(symbol: str) -> Optional[float]:
    """Fetch latest closing price for *symbol* via yfinance."""
    try:
        import yfinance as yf
        from data.fetchers import yf_fetch_with_retry
        t  = yf.Ticker(symbol)
        df = yf_fetch_with_retry(t.history, period="2d", auto_adjust=True)
        if df.empty:
            return None
        return float(df["Close"].iloc[-1])
    except Exception as exc:
        log.debug("Price fetch failed (%s): %s", symbol, exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Outcome determination
# ─────────────────────────────────────────────────────────────────────────────

def _determine_outcome(rec: dict, current_price: float) -> str:
    """
    Classify the recommendation outcome based on current price vs target/stoploss.

    Returns one of: SUCCESS, PARTIAL_FAIL, EXPIRED, IN_PROGRESS.
    """
    target       = rec.get("target")
    stoploss     = rec.get("stoploss")
    horizon_days = int(rec.get("horizon_days") or 180)

    # Check whether the horizon window has elapsed
    horizon_elapsed = False
    try:
        created_str = str(rec.get("created_at", ""))
        if created_str:
            created_dt   = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            horizon_date = created_dt.date() + timedelta(days=horizon_days)
            horizon_elapsed = date.today() >= horizon_date
    except (ValueError, AttributeError):
        pass

    if target is not None and current_price >= float(target):
        return SUCCESS
    if stoploss is not None and current_price <= float(stoploss):
        return PARTIAL_FAIL
    if horizon_elapsed:
        return EXPIRED
    return IN_PROGRESS


# ─────────────────────────────────────────────────────────────────────────────
# Agent signal evaluation
# ─────────────────────────────────────────────────────────────────────────────

def _evaluate_agent_signals(
    agent_signals: dict,
    outcome: str,
) -> dict[str, Optional[bool]]:
    """
    For a resolved recommendation, determine whether each agent's signal was correct.

    Signal → outcome mapping:
      BUY  + SUCCESS      → True    BUY  + PARTIAL_FAIL/EXPIRED → False
      SELL + PARTIAL_FAIL → True    SELL + SUCCESS               → False
      HOLD + EXPIRED      → True    HOLD + SUCCESS/PARTIAL_FAIL  → False
      NO_DATA / IN_PROGRESS         → None (excluded)

    Returns {agent_name: True/False/None}.
    """
    if outcome == IN_PROGRESS:
        return {}  # cannot evaluate yet

    results: dict[str, Optional[bool]] = {}
    for agent_name, signal_data in (agent_signals or {}).items():
        if isinstance(signal_data, dict):
            signal = signal_data.get("signal", "NO_DATA")
        elif isinstance(signal_data, str):
            signal = signal_data
        else:
            signal = "NO_DATA"

        sig = (signal or "").upper()
        if sig in ("NO_DATA", "", "NEUTRAL"):
            results[agent_name] = None
            continue

        if outcome == SUCCESS:
            if sig == "BUY":
                results[agent_name] = True
            elif sig in ("SELL", "AVOID"):
                results[agent_name] = False
            else:  # HOLD on SUCCESS = missed opportunity = wrong
                results[agent_name] = False

        elif outcome == PARTIAL_FAIL:
            if sig in ("SELL", "AVOID"):
                results[agent_name] = True
            elif sig == "BUY":
                results[agent_name] = False
            else:  # HOLD + price fell = wrong
                results[agent_name] = False

        elif outcome == EXPIRED:
            if sig == "HOLD":
                results[agent_name] = True   # staying out was right
            else:
                results[agent_name] = False  # BUY/SELL on an expiry = wrong

    return results


# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────

def _write_outcome(
    client,
    rec_id:        str,
    outcome:       str,
    current_price: float,
    dry_run:       bool,
) -> None:
    """Persist outcome back to the recommendations row."""
    now_str = datetime.now(timezone.utc).isoformat()
    if dry_run:
        log.debug("[DRY RUN] outcome %s → rec %s @ %.2f", outcome, rec_id, current_price)
        return
    if not client:
        return
    try:
        client.table("recommendations").update({
            "outcome":            outcome,
            "outcome_price":      round(current_price, 2),
            "outcome_checked_at": now_str,
        }).eq("id", rec_id).execute()
    except Exception as exc:
        log.warning("outcome write failed rec=%s: %s", rec_id, exc)


def _emit_outcome_alert(
    client,
    rec: dict,
    outcome: str,
    current_price: float,
    dry_run: bool,
) -> None:
    """Emit a portfolio_alert for newly-resolved recommendations."""
    if outcome == IN_PROGRESS:
        return

    severity   = "INFO" if outcome == SUCCESS else "WARNING"
    alert_type = "TARGET_HIT" if outcome == SUCCESS else (
        "STOPLOSS_HIT" if outcome == PARTIAL_FAIL else "REC_EXPIRED"
    )
    symbol = rec.get("symbol", "?")
    target   = rec.get("target")
    stoploss = rec.get("stoploss")
    target_str   = f"₹{float(target):.2f}" if target else "N/A"
    stoploss_str = f"₹{float(stoploss):.2f}" if stoploss else "N/A"

    title = {
        SUCCESS:      f"{symbol} hit target {target_str} ✅",
        PARTIAL_FAIL: f"{symbol} hit stop-loss {stoploss_str} ⚠️",
        EXPIRED:      f"{symbol} recommendation expired without resolution",
    }.get(outcome, f"{symbol} outcome: {outcome}")

    detail = (
        f"Current price: ₹{current_price:.2f}  "
        f"Target: {target_str}  Stop-loss: {stoploss_str}  "
        f"Action: {rec.get('action', '?')}"
    )

    if dry_run:
        print(f"  [DRY RUN] ALERT [{severity}] {title}")
        return
    if not client:
        return
    try:
        client.table("portfolio_alerts").insert({
            "symbol":     symbol,
            "severity":   severity,
            "alert_type": alert_type,
            "title":      title,
            "detail":     detail,
            "resolved":   False,
        }).execute()
    except Exception as exc:
        log.debug("alert insert failed: %s", exc)


def _upsert_accuracy(
    client,
    agent_stats: dict[str, dict],   # {agent: {correct, total, accuracy}}
    dry_run: bool,
) -> int:
    """Write weekly accuracy scores to agent_performance. Returns count written."""
    if not agent_stats:
        return 0
    today   = date.today().isoformat()
    written = 0
    for agent, stats in agent_stats.items():
        acc = stats.get("accuracy")
        if acc is None:
            continue
        if dry_run:
            print(
                f"  [DRY RUN] agent_performance: {agent}  "
                f"accuracy_90d={acc:.2f}%  "
                f"({stats.get('correct',0)}/{stats.get('total',0)} correct)"
            )
            written += 1
            continue
        if not client:
            continue
        try:
            client.table("agent_performance").insert({
                "agent_name":   agent,
                "audit_date":   today,
                "accuracy_90d": acc,
                "trend":        "STABLE",
            }).execute()
            log.info("agent_performance: %s accuracy_90d=%.2f%%", agent, acc)
            written += 1
        except Exception as exc:
            log.warning("agent_performance insert failed %s: %s", agent, exc)
    return written


def _consecutive_low_weeks(client, agent_name: str) -> tuple[bool, float]:
    """
    Return (below_threshold_for_consecutive_weeks, latest_accuracy).
    Reads the last CONSECUTIVE_WEEKS audit rows for agent_name.
    """
    if not client:
        return False, 100.0
    try:
        resp = (
            client.table("agent_performance")
            .select("accuracy_90d, audit_date")
            .eq("agent_name", agent_name)
            .order("audit_date", desc=True)
            .limit(CONSECUTIVE_WEEKS)
            .execute()
        )
        rows = resp.data or []
        if len(rows) < CONSECUTIVE_WEEKS:
            return False, 100.0   # not enough history yet

        accuracies = [float(r.get("accuracy_90d") or 100.0) for r in rows]
        latest_acc = accuracies[0]
        all_below  = all(a < ACCURACY_THRESHOLD for a in accuracies)
        return all_below, latest_acc
    except Exception as exc:
        log.debug("consecutive_low_weeks check failed for %s: %s", agent_name, exc)
        return False, 100.0


def _proposal_exists(client, agent_name: str) -> bool:
    """True if an open (PENDING or IN_PROGRESS) proposal already exists for this agent."""
    if not client:
        return False
    try:
        resp = (
            client.table("enhancement_proposals")
            .select("id")
            .eq("trigger_agent", agent_name)
            .in_("status", ["PENDING", "IN_PROGRESS"])
            .limit(1)
            .execute()
        )
        return bool(resp.data)
    except Exception:
        return False


def _build_proposal(agent_name: str, accuracy: float) -> dict:
    """Build an enhancement proposal dict from the template for *agent_name*."""
    tmpl = _PROPOSAL_TEMPLATES.get(agent_name, _PROPOSAL_TEMPLATES["_default"])
    fmt  = dict(
        agent     = agent_name,
        accuracy  = accuracy,
        threshold = ACCURACY_THRESHOLD,
        weeks     = CONSECUTIVE_WEEKS,
    )

    # Format any {placeholder} tokens in title / rationale / steps
    def _fmt(v):
        if isinstance(v, str):
            try:
                return v.format(**fmt)
            except KeyError:
                return v
        if isinstance(v, list):
            return [_fmt(item) for item in v]
        return v

    return {
        "title":            _fmt(tmpl["title"]),
        "proposed_by":      "governance/performance_tracker",
        "rationale":        _fmt(tmpl["rationale"]),
        "impacted_agents":  [agent_name],
        "cost_impact":      tmpl["cost_impact"],
        "is_paid":          tmpl["is_paid"],
        "steps":            _fmt(tmpl["steps"]),
        "status":           "PENDING",
        "trigger_agent":    agent_name,
        "trigger_accuracy": round(accuracy, 2),
    }


def _save_proposal(client, proposal: dict, dry_run: bool) -> bool:
    """Insert proposal into enhancement_proposals. Returns True on success."""
    if dry_run:
        print(
            f"\n  [DRY RUN] ENHANCEMENT PROPOSAL\n"
            f"  Agent   : {proposal['trigger_agent']}\n"
            f"  Accuracy: {proposal['trigger_accuracy']:.1f}%\n"
            f"  Title   : {proposal['title']}\n"
            f"  Steps   : {len(proposal.get('steps', []))} steps\n"
        )
        return True
    if not client:
        return False
    try:
        client.table("enhancement_proposals").insert(proposal).execute()
        log.warning(
            "ENHANCEMENT PROPOSAL saved: agent=%s accuracy=%.1f%%  '%s'",
            proposal["trigger_agent"], proposal["trigger_accuracy"], proposal["title"],
        )
        return True
    except Exception as exc:
        log.warning("proposal insert failed: %s", exc)
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Data Leakage Audit
# ─────────────────────────────────────────────────────────────────────────────
#
# These functions detect *temporal* data leakage — i.e. agent signals that
# accidentally incorporate price or fundamental data dated AFTER the signal
# generation timestamp.  Two common failure modes in backtesting / live runs:
#
#   1. Look-ahead in technical signals:  yfinance returns a bar whose date is
#      the current day while the market is still open; indicators computed on
#      that bar implicitly "know" the intraday close.
#
#   2. Future-dated RAG events:  a historical_rag match whose `event_date` is
#      AFTER signal_ts means we are conditioning the synthesis on news that has
#      not yet occurred at signal time.
#
# Severity levels:
#   BLOCKING  — the leakage would directly bias the signal; action is
#               downgraded to HOLD when block_on_leak=True.
#   WARNING   — possible leakage or stale data; logged but never blocks.
#
# Usage (pre-synthesis hook):
#   report = audit_data_leakage(symbol, agent_results, signal_ts,
#                               block_on_leak=False)
#   if report.leaks:
#       log.warning("[%s] leakage audit: %d violations", symbol, len(report.leaks))
# ─────────────────────────────────────────────────────────────────────────────

_OHLCV_MAX_FUTURE_BUFFER_DAYS = 1   # allow 1-day tolerance (T+0 bars)
_OHLCV_MAX_STALE_DAYS         = 7   # flag if latest bar is >7d old


@dataclass
class LeakageViolation:
    """Single leakage violation detected for one agent."""
    agent_name: str
    leak_type:  str      # e.g. "future_ohlcv", "future_rag_event", "stale_ohlcv"
    details:    str
    severity:   str      # "BLOCKING" | "WARNING"


@dataclass
class DataLeakageReport:
    """Full leakage audit result for one symbol."""
    symbol:       str
    signal_ts:    datetime
    leaks:        list = field(default_factory=list)   # list[LeakageViolation]
    block_signal: bool = False


def _check_technical_temporal_integrity(
    symbol: str,
    tech_result: dict,
    signal_ts: datetime,
) -> list:
    """
    Check that the most-recent OHLCV bar used by the technical agent is not
    dated after the signal generation timestamp (look-ahead) and is not
    excessively stale (data staleness).

    Returns a list of LeakageViolation (empty = clean).
    """
    violations: list = []
    ohlcv_last = tech_result.get("ohlcv_last_date")
    if not ohlcv_last:
        return violations  # field missing — no check possible

    try:
        last_bar_date = date.fromisoformat(ohlcv_last)
    except ValueError:
        return violations

    signal_date = signal_ts.date() if isinstance(signal_ts, datetime) else signal_ts

    # BLOCKING: bar dated more than 1 day after signal_ts
    if last_bar_date > signal_date + timedelta(days=_OHLCV_MAX_FUTURE_BUFFER_DAYS):
        violations.append(LeakageViolation(
            agent_name="technical",
            leak_type="future_ohlcv",
            details=(
                f"OHLCV last bar {ohlcv_last} is "
                f"{(last_bar_date - signal_date).days}d AFTER signal_ts "
                f"{signal_date.isoformat()} — look-ahead leakage"
            ),
            severity="BLOCKING",
        ))

    # WARNING: bar is more than 7 days old (data staleness)
    elif signal_date - last_bar_date > timedelta(days=_OHLCV_MAX_STALE_DAYS):
        violations.append(LeakageViolation(
            agent_name="technical",
            leak_type="stale_ohlcv",
            details=(
                f"OHLCV last bar {ohlcv_last} is "
                f"{(signal_date - last_bar_date).days}d old — indicators may "
                f"not reflect current market conditions"
            ),
            severity="WARNING",
        ))

    return violations


def _check_fundamental_temporal_integrity(
    symbol: str,
    fund_result: dict,
    signal_ts: datetime,
) -> list:
    """
    Check that the fundamental snapshot was fetched on or before signal_ts.
    Screener.in snapshots are fetched at run-time; this should only fail if
    the agent result was cached across a date boundary.

    Returns a list of LeakageViolation (empty = clean).
    """
    violations: list = []
    data_as_of = fund_result.get("data_as_of")
    if not data_as_of:
        return violations

    try:
        snapshot_date = date.fromisoformat(data_as_of)
    except ValueError:
        return violations

    signal_date = signal_ts.date() if isinstance(signal_ts, datetime) else signal_ts

    # WARNING: snapshot dated after signal_ts
    # (BLOCKING not used here — screener.in has no sub-day timestamps so we
    # cannot distinguish deliberate future fetch from a benign T+0 race)
    if snapshot_date > signal_date:
        violations.append(LeakageViolation(
            agent_name="fundamental",
            leak_type="future_snapshot",
            details=(
                f"Fundamental snapshot date {data_as_of} is "
                f"{(snapshot_date - signal_date).days}d AFTER signal_ts "
                f"{signal_date.isoformat()} — possible cross-date cache hit"
            ),
            severity="WARNING",
        ))

    return violations


def _check_rag_temporal_integrity(
    symbol: str,
    rag_result: dict,
    signal_ts: datetime,
) -> list:
    """
    Verify that no matched historical event in the RAG result is dated after
    signal_ts.  A future-dated RAG match means the model conditioned on news
    that had not yet occurred — a direct look-ahead violation.

    Returns a list of LeakageViolation (empty = clean).
    """
    violations: list = []
    matched = rag_result.get("matched_events") or []
    if not matched:
        return violations

    signal_date_str = (
        signal_ts.date().isoformat()
        if isinstance(signal_ts, datetime)
        else signal_ts.isoformat() if hasattr(signal_ts, "isoformat")
        else str(signal_ts)
    )

    for evt in matched:
        event_date = evt.get("event_date") or evt.get("date")
        if not event_date:
            continue
        try:
            # Normalise — event_date may be "YYYY-MM-DD" or full ISO timestamp
            evt_date_str = str(event_date)[:10]
            if evt_date_str > signal_date_str:
                violations.append(LeakageViolation(
                    agent_name="historical_rag",
                    leak_type="future_rag_event",
                    details=(
                        f"RAG matched event dated {evt_date_str} is AFTER signal_ts "
                        f"{signal_date_str} — look-ahead in historical context"
                    ),
                    severity="BLOCKING",
                ))
        except Exception:
            continue

    return violations


def audit_data_leakage(
    symbol: str,
    agent_results: dict,
    signal_ts: Optional[datetime] = None,
    block_on_leak: bool = False,
) -> "DataLeakageReport":
    """
    Run temporal data-leakage checks across all agent results for a symbol.

    Parameters
    ----------
    symbol        : NSE/yfinance symbol string for logging
    agent_results : dict mapping agent_name → result dict
                    (as produced by _run_agents_for_symbol in orchestrator)
    signal_ts     : datetime when the synthesis pipeline started; defaults to
                    utcnow() when not provided
    block_on_leak : if True, any BLOCKING violation sets report.block_signal=True

    Returns
    -------
    DataLeakageReport  with .leaks list and .block_signal flag
    """
    if signal_ts is None:
        signal_ts = datetime.now(timezone.utc)

    report = DataLeakageReport(symbol=symbol, signal_ts=signal_ts)

    # ── Technical agent ───────────────────────────────────────────────────────
    tech_result = agent_results.get("technical") or {}
    if tech_result:
        for v in _check_technical_temporal_integrity(symbol, tech_result, signal_ts):
            report.leaks.append(v)
            if v.severity == "BLOCKING":
                log.warning(
                    "[%s] DATA LEAKAGE [BLOCKING] technical/%s: %s",
                    symbol, v.leak_type, v.details,
                )
            else:
                log.info(
                    "[%s] DATA LEAKAGE [WARNING] technical/%s: %s",
                    symbol, v.leak_type, v.details,
                )

    # ── Fundamental agent ─────────────────────────────────────────────────────
    fund_result = agent_results.get("fundamental") or {}
    if fund_result:
        for v in _check_fundamental_temporal_integrity(symbol, fund_result, signal_ts):
            report.leaks.append(v)
            log.info(
                "[%s] DATA LEAKAGE [WARNING] fundamental/%s: %s",
                symbol, v.leak_type, v.details,
            )

    # ── Historical RAG agent ──────────────────────────────────────────────────
    rag_result = agent_results.get("historical_rag") or {}
    if rag_result:
        for v in _check_rag_temporal_integrity(symbol, rag_result, signal_ts):
            report.leaks.append(v)
            if v.severity == "BLOCKING":
                log.warning(
                    "[%s] DATA LEAKAGE [BLOCKING] historical_rag/%s: %s",
                    symbol, v.leak_type, v.details,
                )
            else:
                log.info(
                    "[%s] DATA LEAKAGE [WARNING] historical_rag/%s: %s",
                    symbol, v.leak_type, v.details,
                )

    # ── Determine whether to block the signal ────────────────────────────────
    if block_on_leak:
        has_blocking = any(v.severity == "BLOCKING" for v in report.leaks)
        if has_blocking:
            report.block_signal = True
            log.warning(
                "[%s] audit_data_leakage: BLOCKING violation(s) found — "
                "signal will be downgraded to HOLD",
                symbol,
            )

    return report


# ─────────────────────────────────────────────────────────────────────────────
# Main run logic
# ─────────────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False) -> dict:
    """
    Weekly outcome tracker.

    Steps:
      1. Fetch open recommendations older than MIN_AGE_DAYS.
      2. Fetch current price per symbol via yfinance.
      3. Determine outcome (SUCCESS / PARTIAL_FAIL / EXPIRED / IN_PROGRESS).
      4. Write outcome back to recommendations table.
      5. Emit portfolio_alerts for newly-resolved recs.
      6. Aggregate per-agent accuracy from resolved outcomes.
      7. Upsert accuracy scores to agent_performance.
      8. Generate enhancement proposals for agents with 2 consecutive low weeks.

    Returns a summary dict.
    """
    t0     = time.time()
    today  = date.today()
    errors: list[str] = []

    log.info("Outcome tracker: starting weekly run (dry_run=%s)", dry_run)

    client = _supabase()
    if not client and not dry_run:
        log.error("Supabase unavailable — cannot run outcome tracker")
        return {
            "run_date":            today.isoformat(),
            "recs_evaluated":      0,
            "outcomes":            {},
            "agents_updated":      0,
            "proposals_generated": 0,
            "errors":              ["Supabase unavailable"],
            "duration_seconds":    round(time.time() - t0, 2),
        }

    # ── Step 1: Fetch open recommendations older than MIN_AGE_DAYS ────────────
    cutoff = (today - timedelta(days=MIN_AGE_DAYS)).isoformat()
    recs: list[dict] = []
    if client:
        try:
            resp = (
                client.table("recommendations")
                .select(
                    "id, symbol, action, created_at, horizon_days, "
                    "target, stoploss, agent_signals, outcome"
                )
                .lte("created_at", cutoff + "T23:59:59Z")
                .or_("outcome.is.null,outcome.eq.IN_PROGRESS")
                .order("created_at", desc=False)
                .limit(MAX_RECS_PER_RUN)
                .execute()
            )
            recs = resp.data or []
        except Exception as exc:
            log.error("Failed to load recommendations: %s", exc)
            return {
                "run_date":            today.isoformat(),
                "recs_evaluated":      0,
                "outcomes":            {},
                "agents_updated":      0,
                "proposals_generated": 0,
                "errors":              [str(exc)],
                "duration_seconds":    round(time.time() - t0, 2),
            }
    else:
        log.info("[DRY RUN] No Supabase client; skipping DB fetch")

    log.info("Loaded %d open recommendations to evaluate", len(recs))

    if not recs:
        return {
            "run_date":            today.isoformat(),
            "recs_evaluated":      0,
            "outcomes":            {},
            "agents_updated":      0,
            "proposals_generated": 0,
            "errors":              errors,
            "duration_seconds":    round(time.time() - t0, 2),
        }

    # ── Steps 2–5: Price-check each rec and determine / write outcome ─────────
    outcome_counts: dict[str, int] = {SUCCESS: 0, PARTIAL_FAIL: 0, IN_PROGRESS: 0, EXPIRED: 0}
    all_evaluations: list[dict[str, Optional[bool]]] = []

    print()
    print("-" * 72)
    print(f"  Outcome Tracker — {today}   ({len(recs)} open recs)")
    print("-" * 72)

    for rec in recs:
        symbol = rec.get("symbol", "?")
        try:
            current_price = _fetch_current_price(symbol)
            if current_price is None:
                log.debug("[%s] price unavailable — skipped", symbol)
                errors.append(f"{symbol}: price unavailable")
                continue

            outcome = _determine_outcome(rec, current_price)
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1

            target_str   = f"₹{float(rec['target']):.2f}" if rec.get("target") else "—"
            stoploss_str = f"₹{float(rec['stoploss']):.2f}" if rec.get("stoploss") else "—"
            print(
                f"  {symbol:<18}  {rec.get('action','?'):<5}  "
                f"price=₹{current_price:.2f}  target={target_str}  "
                f"sl={stoploss_str}  → {outcome}"
            )

            _write_outcome(client, rec["id"], outcome, current_price, dry_run)

            # Alert on newly resolved recs (not just still IN_PROGRESS)
            if outcome != IN_PROGRESS:
                _emit_outcome_alert(client, rec, outcome, current_price, dry_run)

            # Collect evaluations for resolved recs
            if outcome != IN_PROGRESS:
                evals = _evaluate_agent_signals(rec.get("agent_signals") or {}, outcome)
                if evals:
                    all_evaluations.append(evals)

        except Exception as exc:
            err = f"{symbol} [{rec.get('id','?')}]: {exc}"
            log.warning("Evaluation error — %s", err)
            errors.append(err)

    print("-" * 72)
    print()

    # ── Steps 6–7: Aggregate accuracy and upsert to agent_performance ─────────
    # Aggregate across all resolved evaluations
    tallies: dict[str, dict] = {}
    for evals in all_evaluations:
        for agent, result in evals.items():
            if result is None:
                continue
            tallies.setdefault(agent, {"correct": 0, "total": 0})
            tallies[agent]["total"] += 1
            if result:
                tallies[agent]["correct"] += 1

    agent_stats: dict[str, dict] = {}
    for agent, tally in tallies.items():
        total   = tally["total"]
        correct = tally["correct"]
        acc     = round((correct / total) * 100.0, 2) if total > 0 else 0.0
        agent_stats[agent] = {"correct": correct, "total": total, "accuracy": acc}

    agents_updated = _upsert_accuracy(client, agent_stats, dry_run)

    # ── Step 8: Check for consecutive low-accuracy → generate proposals ───────
    proposals_generated = 0
    for agent_name, stats in agent_stats.items():
        below_threshold, latest_acc = _consecutive_low_weeks(client, agent_name)
        if not below_threshold:
            continue

        # Don't generate a duplicate if one is already open
        if _proposal_exists(client, agent_name):
            log.info(
                "Proposal already open for %s — skipping duplicate generation",
                agent_name,
            )
            continue

        proposal = _build_proposal(agent_name, latest_acc)
        if _save_proposal(client, proposal, dry_run):
            proposals_generated += 1
            # Telegram notification for the proposal
            tg_msg = (
                f"⚠️ <b>Enhancement Proposal Generated</b>\n"
                f"Agent: <code>{agent_name}</code>\n"
                f"Accuracy: {latest_acc:.1f}% (threshold: {ACCURACY_THRESHOLD}%)\n"
                f"Title: {proposal['title']}\n"
                f"Steps: {len(proposal.get('steps', []))}"
            )
            _send_telegram(tg_msg, dry_run=dry_run)

    log.info(
        "Outcome tracker done — %d recs evaluated, %d agents updated, "
        "%d proposals generated in %.1fs",
        len(recs), agents_updated, proposals_generated, time.time() - t0,
    )

    return {
        "run_date":            today.isoformat(),
        "recs_evaluated":      len(recs),
        "outcomes":            outcome_counts,
        "agents_updated":      agents_updated,
        "proposals_generated": proposals_generated,
        "errors":              errors,
        "duration_seconds":    round(time.time() - t0, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# APScheduler + CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bharat Intelligence Weekly Outcome Tracker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python governance/performance_tracker.py                # start Sunday 09:00 IST scheduler
  python governance/performance_tracker.py --run-now      # run immediately
  python governance/performance_tracker.py --run-now --dry
        """,
    )
    parser.add_argument("--run-now", action="store_true",
                        help="Execute immediately instead of waiting for schedule")
    parser.add_argument("--dry",     action="store_true",
                        help="Dry run: no DB writes, prints what would happen")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    if args.run_now:
        result = run(dry_run=args.dry)
        log.info("Run result: %s", result)
        if result.get("errors"):
            log.warning(
                "%d error(s): %s",
                len(result["errors"]), "; ".join(result["errors"][:5]),
            )
        return

    # ── Scheduled mode: Sunday 09:00 IST ─────────────────────────────────────
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
        log.info("Weekly outcome tracker triggered by scheduler...")
        run(dry_run=False)

    scheduler = BlockingScheduler(timezone=IST)
    scheduler.add_job(
        _job,
        CronTrigger(day_of_week="sun", hour=9, minute=0, timezone=IST),
        id="weekly_outcome_tracker",
        name="Bharat Intelligence Weekly Outcome Tracker",
        max_instances=1,
        coalesce=True,
    )

    log.info("-" * 60)
    log.info("  Bharat Intelligence Outcome Tracker")
    log.info("  Schedule: every Sunday at 09:00 IST")
    log.info("  Press Ctrl+C to stop")
    log.info("-" * 60)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped cleanly")


if __name__ == "__main__":
    main()
