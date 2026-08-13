"""
agents/rationale.py — Human-readable "why did this agent vote that way?" text
=============================================================================
Builds a 50–100 word plain-English explanation of an agent's score, for the
Discovery-tab deep-dive panel (and anywhere else a vote needs justifying).

Design decisions
----------------
1. **No LLM call.** Every sentence is templated directly from numbers the agent
   already computed. This means:
     - zero marginal cost and zero added latency on every screened stock
     - the text can never hallucinate a figure the agent did not actually produce
   Agents already emit human-written ``notes`` / ``note`` strings per sub-score;
   we reuse those verbatim where they exist rather than re-deriving prose.

2. **Data provenance is part of the explanation.** When an agent ran on
   fallback/estimated data (screener.in blocked → yfinance), the rationale says
   so. A confident-looking score built on degraded inputs is exactly the case a
   reader needs flagged.

3. **Never raises.** A rationale is cosmetic; a failure here must never break a
   recommendation. Every builder is wrapped and falls back to a generic summary.

Usage
-----
    from agents.rationale import build_rationale

    text = build_rationale("fundamental", fundamental_result)
    # "Scores 72/100 on fundamentals. Revenue grew 18.4% YoY with ROCE at
    #  21.3%. Trades at 24.1x earnings vs a 31.0x sector benchmark — a
    #  discount to peers. Debt/equity of 0.34 is comfortable. Promoters hold
    #  62.1% with no pledging. Based on screener.in data."
"""

from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger(__name__)

_MAX_WORDS = 100


# ──────────────────────────────────────────────────────────────────────────────
# Small formatting helpers
# ──────────────────────────────────────────────────────────────────────────────

def _num(val: Any, suffix: str = "", decimals: int = 1) -> Optional[str]:
    """Format a number, or return None when it is missing / not numeric."""
    if val is None or isinstance(val, bool):
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if f != f:                      # NaN
        return None
    return f"{f:,.{decimals}f}{suffix}"


def _get(d: Any, *path: str) -> Any:
    """Safely walk nested dicts; returns None if any hop is missing."""
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _clip(sentences: list[str], max_words: int = _MAX_WORDS) -> str:
    """Join sentences, dropping trailing ones once the word budget is spent."""
    out: list[str] = []
    used = 0
    for s in sentences:
        if not s:
            continue
        n = len(s.split())
        if out and used + n > max_words:
            break
        out.append(s)
        used += n
    return " ".join(out)


def _quality_note(result: dict) -> str:
    """One short sentence naming the data source / quality caveat."""
    dq = (result.get("data_quality") or "").upper()
    sources = result.get("data_sources") or []
    src = sources[0] if sources else None

    if dq == "ESTIMATED":
        return "Figures are estimated — treat this score as indicative only."
    if dq in ("FALLBACK", "PARTIAL"):
        return "Built on fallback data (screener.in unavailable), so ratios may be incomplete."
    if dq == "NO_DATA":
        return "No reliable data was available for this check."
    if src == "screener_in":
        return "Based on screener.in fundamentals."
    if src == "yfinance_fundamentals":
        return "Based on yfinance fundamentals (screener.in unavailable)."
    return ""


# ──────────────────────────────────────────────────────────────────────────────
# Per-agent builders
# ──────────────────────────────────────────────────────────────────────────────

def _fundamental(r: dict) -> list[str]:
    d = r.get("detail") or {}
    out: list[str] = []

    growth = _get(d, "growth_quality") or {}
    rev = _num(growth.get("revenue_growth_yoy"), "%")
    roce = _num(growth.get("roce"), "%")
    if rev and roce:
        out.append(f"Revenue grew {rev} YoY with ROCE at {roce}.")
    elif rev:
        out.append(f"Revenue grew {rev} YoY.")
    elif roce:
        out.append(f"ROCE stands at {roce}.")

    prof = _get(d, "profitability") or {}
    pe = _num(prof.get("pe"), "x")
    sector_pe = _num(prof.get("sector_pe_effective") or prof.get("sector_pe_static"), "x")
    if pe and sector_pe:
        try:
            cheap = float(prof["pe"]) < float(prof.get("sector_pe_effective")
                                              or prof.get("sector_pe_static"))
            verdict = "a discount to peers" if cheap else "a premium to peers"
            out.append(f"Trades at {pe} earnings versus a {sector_pe} sector benchmark — {verdict}.")
        except (TypeError, ValueError):
            out.append(f"Trades at {pe} earnings versus a {sector_pe} sector benchmark.")
    elif pe:
        out.append(f"Trades at {pe} earnings.")

    margin = _num(prof.get("ebitda_margin"), "%")
    if margin:
        out.append(f"Operating margin is {margin}.")

    bs = _get(d, "balance_sheet") or {}
    de = _num(bs.get("debt_equity"), "", 2)
    if de is not None:
        try:
            tone = "comfortable" if float(bs["debt_equity"]) < 1.0 else "elevated"
            out.append(f"Debt/equity of {de} is {tone}.")
        except (TypeError, ValueError):
            out.append(f"Debt/equity stands at {de}.")

    gov = _get(d, "governance") or {}
    ph = _num(gov.get("promoter_holding"), "%")
    pledge = gov.get("promoter_pledging")
    if ph:
        pledge_num = _num(pledge, "%")
        if pledge_num and float(pledge or 0) > 0:
            out.append(f"Promoters hold {ph} with {pledge_num} pledged.")
        else:
            out.append(f"Promoters hold {ph} with no pledging.")

    danger = _get(d, "danger") or {}
    triggers = danger.get("triggers") or []
    if triggers:
        out.append(f"Risk flags: {', '.join(str(t) for t in triggers[:2])}.")

    return out


def _technical(r: dict) -> list[str]:
    d = r.get("detail") or {}
    out: list[str] = []

    ind = _get(d, "indicators") or {}
    rsi = _num(ind.get("rsi") or _get(d, "momentum", "rsi"))
    if rsi:
        try:
            rv = float(ind.get("rsi") or _get(d, "momentum", "rsi"))
            zone = ("oversold territory" if rv < 30
                    else "overbought territory" if rv > 70
                    else "neutral territory")
            out.append(f"RSI at {rsi} puts it in {zone}.")
        except (TypeError, ValueError):
            out.append(f"RSI reads {rsi}.")

    trend = _get(d, "trend_alignment") or {}
    if trend.get("ema_aligned") is True:
        out.append("The 20/50/200-day moving averages are stacked bullishly.")
    elif trend.get("ema_aligned") is False:
        out.append("Moving averages are not in a bullish alignment.")

    adx = _num(trend.get("adx"))
    if adx:
        try:
            strength = "a strong trend" if float(trend["adx"]) > 25 else "a weak or ranging trend"
            out.append(f"ADX of {adx} indicates {strength}.")
        except (TypeError, ValueError):
            pass

    mom = _get(d, "momentum") or {}
    if mom.get("macd_bullish") is True:
        out.append("MACD is above its signal line.")
    elif mom.get("macd_bullish") is False:
        out.append("MACD sits below its signal line.")

    vol = _get(d, "volume_confirmation") or {}
    vratio = _num(vol.get("volume_vs_avg"), "x", 2)
    if vratio:
        out.append(f"Volume is running {vratio} its average.")

    patterns = _get(d, "pattern", "detected") or []
    if patterns:
        out.append(f"Pattern detected: {', '.join(str(p) for p in patterns[:2])}.")

    return out


def _sentiment(r: dict) -> list[str]:
    d = r.get("detail") or {}
    out: list[str] = []

    n = d.get("headlines_analysed")
    breakdown = _get(d, "sentiment_breakdown") or {}
    bull = breakdown.get("bullish")
    bear = breakdown.get("bearish")
    if n:
        if bull is not None and bear is not None:
            out.append(f"Read {n} recent headlines — {bull} bullish, {bear} bearish.")
        else:
            out.append(f"Read {n} recent headlines.")

    events = d.get("event_class_breakdown") or {}
    notable = [k for k in events if k and k != "ROUTINE"]
    if notable:
        out.append(f"Notable events: {', '.join(notable[:2]).replace('_', ' ').lower()}.")

    insider = d.get("insider_signal")
    if insider and insider != "NEUTRAL":
        out.append(f"Promoter activity reads {str(insider).lower()}.")

    if d.get("finbert_used"):
        out.append("Scores blend FinBERT with an LLM classifier on the freshest headlines.")

    if d.get("news_only_mode"):
        out.append("FII flow data was unavailable, so this is news-only.")

    return out


def _institutional(r: dict) -> list[str]:
    d = r.get("detail") or {}
    out: list[str] = []

    fii_net = _get(d, "fii", "net_5d_cr")
    fii_str = _num(fii_net, " Cr", 0)
    if fii_str is not None:
        try:
            direction = "bought" if float(fii_net) >= 0 else "sold"
            out.append(f"FIIs net {direction} Rs {_num(abs(float(fii_net)), ' Cr', 0)} over five sessions.")
        except (TypeError, ValueError):
            pass

    streak = _get(d, "fii", "buy_streak")
    if streak and isinstance(streak, (int, float)) and streak >= 2:
        out.append(f"That is a {int(streak)}-session buying streak.")

    dii_net = _get(d, "dii", "net_5d_cr")
    if dii_net is not None:
        try:
            direction = "buying" if float(dii_net) >= 0 else "selling"
            out.append(f"Domestic institutions were net {direction}.")
        except (TypeError, ValueError):
            pass

    deals = _get(d, "bulk_deals", "total_deals")
    if deals:
        out.append(f"{deals} bulk/block deal(s) were recorded.")

    pct_inst = _num(_get(d, "institutional_snapshot", "pct_institutions"), "%")
    if pct_inst:
        out.append(f"Institutions hold about {pct_inst} of the company.")

    return out


def _macro(r: dict) -> list[str]:
    d = r.get("detail") or {}
    out: list[str] = []

    # Surface the two indicators with the most extreme scores — those are the
    # ones actually moving this stock's macro score.
    indicators = {
        "india_vix": "India VIX",
        "inr_usd":   "the rupee",
        "us10y":     "the US 10-year yield",
        "rbi_repo":  "the RBI repo rate",
        "dxy":       "the dollar index",
    }
    scored = []
    for key, label in indicators.items():
        node = _get(d, key) or {}
        if isinstance(node.get("score"), (int, float)):
            scored.append((abs(float(node["score"]) - 50), label, node))
    scored.sort(reverse=True)

    for _, label, node in scored[:2]:
        note = node.get("note")
        val = _num(node.get("value"), "", 2)
        if note:
            out.append(f"{label.capitalize()}: {note}.".replace("..", "."))
        elif val:
            out.append(f"{label.capitalize()} at {val}.")

    news = _get(d, "macro_news") or {}
    if news.get("signal") and news["signal"] != "NEUTRAL":
        events = news.get("key_events") or []
        if events:
            out.append(f"Macro news is {str(news['signal']).lower()}: {str(events[0])[:90]}.")
        else:
            out.append(f"Macro news flow reads {str(news['signal']).lower()}.")

    if r.get("sector_adjusted"):
        out.append("Score is adjusted for this stock's sector sensitivity.")

    return out


def _historical_rag(r: dict) -> list[str]:
    d = r.get("detail") or {}
    out: list[str] = []

    reasoning = d.get("reasoning")
    if reasoning:
        out.append(f"{str(reasoning).rstrip('.')}.")

    matched = r.get("matched_events") or []
    if matched:
        top = matched[0]
        desc = str(top.get("description") or "")[:90]
        if desc:
            out.append(f"Closest analogue: {desc}.")

    if d.get("score_floor_applied"):
        out.append("The event library is not yet balanced, so the score is floored to avoid a false bearish reading.")
    elif d.get("db_balanced") is False:
        out.append("The historical event library is still thin for this pattern.")

    return out


def _commodities(r: dict) -> list[str]:
    d = r.get("detail") or {}
    out: list[str] = []
    for key, label in (("gold", "Gold"), ("crude", "Crude"), ("silver", "Silver")):
        node = d.get(key) if isinstance(d, dict) else None
        if isinstance(node, dict) and node.get("signal"):
            price = _num(node.get("price"), "", 0)
            price_str = f" at {price}" if price else ""
            out.append(f"{label}{price_str} reads {str(node['signal']).replace('_', ' ').lower()}.")
    return out


_BUILDERS = {
    "fundamental":    _fundamental,
    "technical":      _technical,
    "sentiment":      _sentiment,
    "institutional":  _institutional,
    "macro":          _macro,
    "historical_rag": _historical_rag,
    "commodities":    _commodities,
}

_FRIENDLY_NAME = {
    "fundamental":    "fundamentals",
    "technical":      "technicals",
    "sentiment":      "news sentiment",
    "institutional":  "institutional flows",
    "macro":          "the macro backdrop",
    "historical_rag": "historical analogues",
    "commodities":    "commodity exposure",
}


# ──────────────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────────────

def build_rationale(agent_name: str, result: Optional[dict]) -> str:
    """
    Return a 50–100 word plain-English explanation of `result`'s score.

    Never raises — returns "" when there is genuinely nothing to say, so callers
    can store the value unconditionally.
    """
    if not isinstance(result, dict):
        return ""

    try:
        score  = result.get("score")
        signal = result.get("signal")

        # The completeness gate sets a top-level `reason` when it blocks an
        # agent. That is already the most accurate explanation available.
        if signal in ("NO_DATA", "INSUFFICIENT_DATA"):
            gate = result.get("reason") or _get(result, "detail", "error")
            label = _FRIENDLY_NAME.get(agent_name, agent_name)
            if gate:
                return f"No usable {label} signal: {str(gate).rstrip('.')}."
            return f"No usable {label} data was available for this stock."

        label = _FRIENDLY_NAME.get(agent_name, agent_name)
        opener = (
            f"Scores {int(score)}/100 on {label}"
            if isinstance(score, (int, float))
            else f"Reads {signal} on {label}"
        )
        if signal:
            opener += f" ({str(signal).replace('_', ' ').lower()})."
        else:
            opener += "."

        builder = _BUILDERS.get(agent_name)
        body = builder(result) if builder else []

        sentences = [opener] + body
        quality = _quality_note(result)
        if quality:
            sentences.append(quality)

        text = _clip(sentences)

        # If the agent gave us nothing beyond the opener, say so honestly rather
        # than shipping a bare score restatement.
        if len(sentences) == 1:
            return f"{opener} No component-level detail was recorded for this run."
        return text

    except Exception as exc:                       # pragma: no cover - defensive
        log.debug("build_rationale(%s) failed: %s", agent_name, exc)
        return ""


def attach_rationales(agent_results: dict) -> dict:
    """
    Build ``{agent_name: {signal, score, reason}}`` from a full agent-results
    dict. Used when persisting a recommendation so the dashboard can render the
    explanation without re-running anything.
    """
    out: dict[str, dict] = {}
    for name, res in (agent_results or {}).items():
        if not isinstance(res, dict):
            continue
        out[name] = {
            "signal": res.get("signal"),
            "score":  res.get("score"),
            "reason": build_rationale(name, res),
        }
    return out
