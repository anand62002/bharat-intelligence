"""
agents/multibagger_screener.py — long-horizon "multibagger" screen (P8-E).

Why a separate track
--------------------
90-day discovery looks for stocks trading cheap against their sector PE. Across
333 paper positions not one doubled (best +47%), and its highest-conviction
calls did worst. A 10x is a different animal: a small or mid-sized business
compounding earnings at a high return on capital for years, usually while the
market still under-owns it. That takes 3–5 years to show and needs different
signals, so it gets its own screen, its own 1–3 year paper book
(agents/long_horizon_book.py) and its own track record.

Honesty about the rules
-----------------------
The gates and weights below follow what published multibagger studies keep
finding (small size, high and *consistent* ROCE, sustained earnings growth,
reasonable price for that growth, promoter skin in the game, low institutional
ownership). They have NOT been validated on this system's data — that is what
the paper book's t+365 outcomes are for. Sizing above the base allocation stays
locked until they prove out.

Pipeline (worker job, 12:30 IST — after the 10:30 discovery run)
-----------------------------------------------------------------
  1. Take today's discovery slice (discovery_runs.slice_symbols) — the
     screener already fetched those fundamentals, so this costs no new network
     calls (fundamentals_cache). Every symbol is visited once per ~12-day cycle.
  2. prefilter() — hard gates on the snapshot.
  3. deep_check() — warren_bot's 10-year ROCE / EPS CAGR must confirm the
     quality is durable, not one good year. warren_bot's BUY/AVOID verdict is
     NOT used: it is DCF-valuation driven and rates most compounders AVOID
     (GABRIEL: 10y ROCE 27%, 10y EPS CAGR 18.8%, verdict AVOID on −76% MOS).
  4. score() — 0–100 across five components; ≥ PICK_THRESHOLD (80) becomes a pick.
     Dry run on the 2026-11-10 slice: 200 → 18 prefilter → 15 deep → 2 picks.

CLI:
    python -m agents.multibagger_screener              # dry run on today's slice
    python -m agents.multibagger_screener --run        # save picks
    python -m agents.multibagger_screener --symbols GABRIEL,ELFORGE
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
from typing import Any, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(_ROOT, ".env"))

log = logging.getLogger(__name__)

# ── Hard gates (snapshot) ─────────────────────────────────────────────────────
MCAP_MIN_CR      = 300       # below this: liquidity/governance risk dominates
MCAP_MAX_CR      = 15_000    # above this a 10x needs a ₹1.5 lakh Cr outcome — rare
ROCE_MIN         = 15.0
GROWTH_MIN       = 12.0      # revenue growth % (3y CAGR preferred, else YoY)
DEBT_EQUITY_MAX  = 1.0
PLEDGE_MAX       = 10.0
PROMOTER_MIN     = 35.0
PE_MAX           = 60.0

# ── Durability gates (warren_bot, 10-year) ────────────────────────────────────
ROCE_10Y_MIN     = 12.0
EPS_CAGR_10Y_MIN = 10.0

PICK_THRESHOLD   = 80        # score needed to become a pick. 70 passed 5 of the
                             # 2026-11-10 slice (2.5% → ~60 picks per universe pass) —
                             # too loose for rare finds; 80 kept KPEL 91 and NINSYS 83.
EPS_GROWTH_CAP   = 40.0      # growth above this is rarely sustained; usually a small
                             # base (TERASOFT 85%) — capped so it can't fake a cheap PEG
MAX_DEEP_PER_RUN = 15        # warren_bot runs per day (cost guard)


def _cr(market_cap: Optional[float]) -> Optional[float]:
    """screener market_cap is in rupees; return ₹ crore."""
    return round(float(market_cap) / 1e7, 1) if market_cap else None


def _growth(snap: dict) -> Optional[float]:
    for k in ("revenue_cagr_3y", "revenue_growth"):
        if snap.get(k) is not None:
            return float(snap[k])
    return None


def _eps_growth(snap: dict, warren: Optional[dict] = None) -> Optional[float]:
    """Slower of recent and long-run EPS growth — rewards consistency."""
    vals = [v for v in (snap.get("eps_cagr_3y"), snap.get("eps_cagr_5y"),
                        (warren or {}).get("ten_year_eps_cagr")) if v is not None]
    return min(float(min(vals)), EPS_GROWTH_CAP) if vals else None


def prefilter(snap: Optional[dict]) -> tuple[bool, list[str]]:
    """Hard gates on the fundamentals snapshot. Returns (passed, failures)."""
    if not snap:
        return False, ["no fundamentals"]
    fails: list[str] = []
    mcap = _cr(snap.get("market_cap"))
    if mcap is None or not (MCAP_MIN_CR <= mcap <= MCAP_MAX_CR):
        fails.append(f"market cap {mcap} Cr outside {MCAP_MIN_CR}–{MCAP_MAX_CR}")
    roce = snap.get("roce")
    if roce is None or roce < ROCE_MIN:
        fails.append(f"ROCE {roce} < {ROCE_MIN}")
    g = _growth(snap)
    if g is None or g < GROWTH_MIN:
        fails.append(f"revenue growth {g} < {GROWTH_MIN}")
    de = snap.get("debt_equity")
    if de is not None and de > DEBT_EQUITY_MAX:
        fails.append(f"debt/equity {de} > {DEBT_EQUITY_MAX}")
    pledge = snap.get("promoter_pledging")
    if pledge is not None and pledge > PLEDGE_MAX:
        fails.append(f"pledging {pledge}% > {PLEDGE_MAX}%")
    prom = snap.get("promoter_holding")
    if prom is not None and prom < PROMOTER_MIN:
        fails.append(f"promoter holding {prom}% < {PROMOTER_MIN}%")
    pe = snap.get("pe")
    if pe is None or pe <= 0 or pe > PE_MAX:
        fails.append(f"PE {pe} not in (0, {PE_MAX}]")
    return not fails, fails


def deep_check(warren: Optional[dict]) -> tuple[bool, str]:
    """Is the quality durable over ~10 years? Valuation verdict deliberately ignored."""
    if not warren or warren.get("error"):
        return False, "warren_bot unavailable"
    if warren.get("signal") == "INSUFFICIENT_DATA":
        return False, "insufficient history"
    roce10 = warren.get("roce_avg_10yr")
    eps10 = warren.get("ten_year_eps_cagr")
    if roce10 is None or roce10 < ROCE_10Y_MIN:
        return False, f"10y ROCE {roce10} < {ROCE_10Y_MIN}"
    if eps10 is None or eps10 < EPS_CAGR_10Y_MIN:
        return False, f"10y EPS CAGR {eps10} < {EPS_CAGR_10Y_MIN}"
    return True, f"10y ROCE {roce10:.0f}%, 10y EPS CAGR {eps10:.1f}%"


def score(snap: dict, warren: Optional[dict] = None) -> tuple[float, dict]:
    """
    0–100 multibagger score, five components:
      runway 20 · quality 25 · growth 25 · valuation 15 · ownership 15
    Returns (score, components) with each component's points and inputs.
    """
    mcap = _cr(snap.get("market_cap")) or 0
    runway = 20 if mcap <= 1_000 else 16 if mcap <= 3_000 else 12 if mcap <= 7_000 else 8

    # Quality = the weaker of today's ROCE and the 10-year average (consistency).
    rvals = [v for v in (snap.get("roce"), (warren or {}).get("roce_avg_10yr")) if v is not None]
    roce = min(rvals) if rvals else 0
    quality = 25 if roce >= 30 else 20 if roce >= 22 else 15 if roce >= 18 else 10 if roce >= 15 else 0

    eps_g = _eps_growth(snap, warren) or 0
    growth = 25 if eps_g >= 30 else 20 if eps_g >= 20 else 15 if eps_g >= 15 else 8 if eps_g >= 10 else 0

    pe = snap.get("pe") or 0
    peg = (pe / eps_g) if (pe > 0 and eps_g > 0) else None
    valuation = (15 if peg <= 0.8 else 11 if peg <= 1.2 else 7 if peg <= 1.8 else 2) if peg else 0

    prom = snap.get("promoter_holding") or 0
    inst = (snap.get("fii_holding_pct") or 0) + (snap.get("dii_holding_pct") or 0)
    ownership = 15 if (prom >= 50 and inst <= 15) else 10 if (prom >= 40 and inst <= 25) else 5

    comps = {
        "runway":    {"points": runway,    "max": 20, "market_cap_cr": mcap},
        "quality":   {"points": quality,   "max": 25, "roce_used": round(roce, 1)},
        "growth":    {"points": growth,    "max": 25, "eps_growth_used": round(eps_g, 1)},
        "valuation": {"points": valuation, "max": 15, "pe": pe, "peg": round(peg, 2) if peg else None},
        "ownership": {"points": ownership, "max": 15, "promoter_pct": prom, "institutional_pct": round(inst, 1)},
    }
    return float(runway + quality + growth + valuation + ownership), comps


def thesis(symbol: str, comps: dict) -> str:
    c = comps
    return (
        f"{symbol}: ₹{c['runway']['market_cap_cr']:,.0f} Cr business compounding EPS ~{c['growth']['eps_growth_used']:.0f}%/yr "
        f"at ≥{c['quality']['roce_used']:.0f}% ROCE"
        + (f", PEG {c['valuation']['peg']}" if c['valuation']['peg'] else "")
        + f"; promoters {c['ownership']['promoter_pct']:.0f}%, institutions {c['ownership']['institutional_pct']:.0f}%."
    )


# ─────────────────────────────────────────────────────────────────────────────
# DB-backed run
# ─────────────────────────────────────────────────────────────────────────────

def _supabase():
    url, key = os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        return None
    from supabase import create_client
    return create_client(url, key)


def _todays_slice(client: Any) -> list[str]:
    """Same symbols discovery screened today; recompute the slice if it hasn't run."""
    try:
        rows = (client.table("discovery_runs").select("slice_symbols")
                .eq("run_date", date.today().isoformat()).limit(1).execute().data or [])
        if rows and rows[0].get("slice_symbols"):
            return list(rows[0]["slice_symbols"])
    except Exception as exc:
        log.warning("multibagger: discovery_runs read failed: %s", exc)
    from agents.discovery_screener import _daily_slice, fetch_all_nse_equity_symbols
    return _daily_slice(fetch_all_nse_equity_symbols(), 200)


def is_missing_table(exc: Exception) -> bool:
    s = str(exc)
    return "PGRST205" in s or "does not exist" in s or "Could not find the table" in s


def run_screen(symbols: Optional[list[str]] = None, dry_run: bool = True, client: Any = None) -> dict:
    """Screen symbols (default: today's discovery slice) and save new picks."""
    from data.fetchers import get_screener_data
    from agents.warren_bot import analyse as warren_analyse

    client = client or _supabase()
    symbols = symbols or (_todays_slice(client) if client else [])
    summary = {"screened": 0, "prefilter_passed": 0, "deep_checked": 0, "picks": [],
               "near_misses": [], "errors": [], "dry_run": dry_run}

    active: set[str] = set()
    if client:
        try:
            active = {r["symbol"] for r in (client.table("long_horizon_picks").select("symbol")
                                             .eq("status", "ACTIVE").execute().data or [])}
        except Exception as exc:
            if is_missing_table(exc):
                summary["errors"].append("migration needed: db/migrations/create_long_horizon.sql")
                log.warning("multibagger: long_horizon_picks missing — run create_long_horizon.sql")
                if not dry_run:
                    return summary
            else:
                raise

    candidates: list[tuple[str, dict]] = []
    for sym in symbols:
        base = sym.replace(".NS", "").replace(".BO", "")
        summary["screened"] += 1
        try:
            snap = get_screener_data(base)
        except Exception as exc:
            summary["errors"].append(f"{base}: {exc}")
            continue
        ok, _fails = prefilter(snap)
        if ok and base not in active:
            candidates.append((base, snap))
    summary["prefilter_passed"] = len(candidates)

    # Deep-check the most promising first (score without warren as a pre-rank).
    candidates.sort(key=lambda c: -score(c[1])[0])
    for base, snap in candidates[:MAX_DEEP_PER_RUN]:
        summary["deep_checked"] += 1
        try:
            warren = warren_analyse(base)
        except Exception as exc:
            summary["errors"].append(f"{base} warren: {exc}")
            continue
        durable, why = deep_check(warren)
        s, comps = score(snap, warren)
        if not durable or s < PICK_THRESHOLD:
            summary["near_misses"].append({"symbol": base, "score": s,
                                           "reason": why if not durable else f"score {s:.0f} < {PICK_THRESHOLD}"})
            continue
        pick = {
            "symbol": base, "pick_date": date.today().isoformat(), "score": s,
            "components": comps, "thesis": thesis(base, comps),
            "entry_price": warren.get("current_price"),
            "market_cap_cr": comps["runway"]["market_cap_cr"],
            "status": "ACTIVE", "last_reviewed_at": date.today().isoformat(),
            "metadata": {"durability": why, "warren_score": warren.get("score"),
                         "moat_type": warren.get("moat_type")},
        }
        summary["picks"].append(pick)
        if not dry_run and client:
            try:
                client.table("long_horizon_picks").insert(pick).execute()
            except Exception as exc:
                summary["errors"].append(f"{base} insert: {exc}")

    log.info("multibagger screen%s — screened=%d prefilter=%d deep=%d picks=%d errors=%d",
             " (DRY RUN)" if dry_run else "", summary["screened"], summary["prefilter_passed"],
             summary["deep_checked"], len(summary["picks"]), len(summary["errors"]))
    return summary


def review_picks(dry_run: bool = True, client: Any = None) -> dict:
    """
    Weekly: re-check every ACTIVE pick. The thesis is broken when long-run quality
    no longer holds (deep_check fails) or the snapshot breaks a hard gate other
    than market cap / PE — a pick that grew past ₹15,000 Cr or re-rated above
    PE 60 has *succeeded*, it hasn't broken.
    """
    from data.fetchers import get_screener_data
    from agents.warren_bot import analyse as warren_analyse

    client = client or _supabase()
    out = {"reviewed": 0, "broken": [], "errors": []}
    if not client:
        return out
    picks = client.table("long_horizon_picks").select("*").eq("status", "ACTIVE").execute().data or []
    for p in picks:
        out["reviewed"] += 1
        try:
            snap = get_screener_data(p["symbol"])
            _ok, fails = prefilter(snap)
            fails = [f for f in fails if not f.startswith(("market cap", "PE "))]
            durable, why = deep_check(warren_analyse(p["symbol"]))
            reason = None if (durable and not fails) else (why if not durable else "; ".join(fails))
            upd = {"last_reviewed_at": date.today().isoformat()}
            if reason:
                upd.update(status="THESIS_BROKEN", status_reason=reason)
                out["broken"].append({"symbol": p["symbol"], "reason": reason})
            if not dry_run:
                client.table("long_horizon_picks").update(upd).eq("id", p["id"]).execute()
        except Exception as exc:
            out["errors"].append(f"{p['symbol']}: {exc}")
    log.info("multibagger review — reviewed=%d broken=%d", out["reviewed"], len(out["broken"]))
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser(description="Long-horizon multibagger screen")
    ap.add_argument("--run", action="store_true", help="save picks (default: dry run)")
    ap.add_argument("--symbols", help="comma-separated symbols instead of today's slice")
    a = ap.parse_args()
    syms = [s.strip().upper() for s in a.symbols.split(",")] if a.symbols else None
    r = run_screen(symbols=syms, dry_run=not a.run)
    print(f"\nscreened {r['screened']} → prefilter {r['prefilter_passed']} → deep {r['deep_checked']} → picks {len(r['picks'])}")
    for p in r["picks"]:
        print(f"  PICK {p['symbol']:<12} {p['score']:.0f}  {p['thesis']}")
    for m in sorted(r["near_misses"], key=lambda m: -m["score"])[:10]:
        print(f"  near {m['symbol']:<12} {m['score']:.0f}  {m['reason']}")
    for e in r["errors"][:10]:
        print("  ERROR", e)
