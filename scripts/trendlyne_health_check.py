#!/usr/bin/env python3
"""
scripts/trendlyne_health_check.py
──────────────────────────────────
Pre-start health check for all three Trendlyne modules.

Usage:
  python scripts/trendlyne_health_check.py          # exits 0 even on failure (warn-only)
  python scripts/trendlyne_health_check.py --strict  # exits 1 if any check fails

Railway pre-start command:
  python scripts/trendlyne_health_check.py || true

Checks:
  1. trendlyne_fno_fetcher   — F&O Excel download (PCR, max pain, OI buildup)
  2. trendlyne_fetcher        — Fundamentals / DVM score (tier-2 screener.in fallback)
  3. trendlyne_analyst_fetcher — Analyst targets + consensus rating

Sample stock: RELIANCE (large-cap, covered by all three modules, reliable test case)
Falls back to TCS if RELIANCE returns no data.
"""

import io
import os
import sys
import time
import logging
import argparse

# Ensure project root (parent of this scripts/ dir) is on sys.path so that
# `from data.xxx import ...` works when the script is run directly or via
# `python scripts/trendlyne_health_check.py` from the project root.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Force UTF-8 stdout so Unicode check-marks render on Railway (Linux) and
# Windows terminals that default to cp1252.
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── Logging: concise single-line format ──────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,   # suppress verbose library logs
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("trendlyne_health")
log.setLevel(logging.DEBUG)

# Suppress noisy sub-module logs during health check
for noisy in ("data.trendlyne_fno_fetcher", "data.trendlyne_fetcher",
              "data.trendlyne_analyst_fetcher", "data.proxy_session",
              "urllib3", "requests"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

SAMPLE_SYMBOLS = ["RELIANCE", "TCS", "INFY"]   # tried in order until one passes
PASS = "✅ PASS"
FAIL = "❌ FAIL"
WARN = "⚠️  WARN"


# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────

def _fmt(value, unit="") -> str:
    """Format a numeric value or None for display."""
    if value is None:
        return "None"
    if isinstance(value, float):
        return f"{value:.2f}{unit}"
    return f"{value}{unit}"


def _env_summary() -> None:
    """Print which Trendlyne env vars are configured (without revealing values)."""
    vars_checked = {
        "TRENDLYNE_SESSION": "session cookie (required)",
        "TRENDLYNE_CSRF":    "CSRF token (required)",
        "TRENDLYNE_USER":    "login email (optional — enables auto-refresh)",
        "TRENDLYNE_PASS":    "login password (optional — enables auto-refresh)",
    }
    print("\n── Trendlyne env vars ──────────────────────────────────────────")
    for var, desc in vars_checked.items():
        val = os.environ.get(var, "")
        status = f"SET ({len(val)} chars)" if val else "NOT SET"
        marker = "  " if val else "  ⚠️ "
        print(f"  {marker}{var:<25} {status:<22} ({desc})")


# ─────────────────────────────────────────────────────────────────────────────
# Module 1 — F&O fetcher
# ─────────────────────────────────────────────────────────────────────────────

def check_fno_fetcher() -> tuple[bool, str]:
    """
    Downloads the Trendlyne daily F&O Excel from S3.
    Returns (passed, detail_string).
    """
    print("\n── Module 1: trendlyne_fno_fetcher (F&O / options) ────────────")
    try:
        from data.trendlyne_fno_fetcher import get_fno_universe, get_option_metrics, get_buildup_signals

        t0 = time.time()
        universe = get_fno_universe()
        elapsed = time.time() - t0

        if not universe:
            print(f"  {FAIL}  get_fno_universe() returned empty list")
            print("         Possible causes: TRENDLYNE_SESSION/CSRF expired, S3 URL changed,")
            print("         or no F&O file published yet today (runs post-market).")
            return False, "empty universe"

        print(f"  {PASS}  F&O universe: {len(universe)} symbols loaded in {elapsed:.1f}s")

        # Check a specific stock
        found_sym = None
        for sym in SAMPLE_SYMBOLS:
            metrics = get_option_metrics(sym)
            if metrics.get("error") and "not in F&O universe" in str(metrics["error"]):
                continue   # not an F&O stock, try next
            found_sym = sym
            pcr     = _fmt(metrics.get("pcr"))
            max_pain = _fmt(metrics.get("max_pain"), "₹")
            atm_iv  = _fmt(metrics.get("atm_iv"), "%")
            buildup = metrics.get("buildup") or "N/A"
            err     = metrics.get("error")
            if err:
                print(f"  {WARN}  get_option_metrics({sym}): error={err}")
            else:
                print(f"  {PASS}  {sym}: PCR={pcr}  MaxPain={max_pain}  ATM_IV={atm_iv}  Buildup={buildup}")
            break

        if found_sym is None:
            print(f"  {WARN}  None of {SAMPLE_SYMBOLS} found in F&O universe — universe may be partial")

        # Buildup signals
        signals = get_buildup_signals(min_oi_change_pct=5.0)
        print(f"  {PASS}  get_buildup_signals(): {len(signals)} stocks with OI change ≥5%")

        return True, f"{len(universe)} F&O symbols"

    except Exception as exc:
        print(f"  {FAIL}  Exception: {exc}")
        log.debug("FNO fetcher exception", exc_info=True)
        return False, str(exc)


# ─────────────────────────────────────────────────────────────────────────────
# Module 2 — Fundamentals / DVM fetcher
# ─────────────────────────────────────────────────────────────────────────────

def check_fundamentals_fetcher() -> tuple[bool, str]:
    """
    Scrapes fundamentals and DVM score from Trendlyne equity page.
    Returns (passed, detail_string).
    """
    print("\n── Module 2: trendlyne_fetcher (fundamentals / DVM tier-2) ────")
    try:
        from data.trendlyne_fetcher import get_trendlyne_fundamentals, get_trendlyne_dvm

        passed = False
        last_error = "no symbols tried"

        for sym in SAMPLE_SYMBOLS:
            t0 = time.time()
            fundamentals = get_trendlyne_fundamentals(sym)
            elapsed = time.time() - t0

            if fundamentals is None:
                print(f"  {WARN}  get_trendlyne_fundamentals({sym}): returned None ({elapsed:.1f}s)")
                last_error = f"{sym} returned None"
                continue

            # Show a few key fields
            pe     = _fmt(fundamentals.get("pe"))
            roe    = _fmt(fundamentals.get("roe"), "%")
            debt_eq = _fmt(fundamentals.get("debt_equity"))
            rev_gr = _fmt(fundamentals.get("revenue_growth"), "%")
            print(f"  {PASS}  {sym} fundamentals ({elapsed:.1f}s): PE={pe}  ROE={roe}  D/E={debt_eq}  RevGrowth={rev_gr}")

            # Non-zero field count (data quality indicator)
            non_none = sum(1 for v in fundamentals.values() if v is not None)
            total    = len(fundamentals)
            print(f"         Data completeness: {non_none}/{total} fields populated")
            if non_none < 5:
                print(f"  {WARN}  Very low completeness ({non_none}/{total}) — HTML structure may have changed")

            # DVM score
            dvm = get_trendlyne_dvm(sym)
            if dvm:
                composite = _fmt(dvm.get("composite_dvm"))
                durability = _fmt(dvm.get("durability"))
                valuation  = _fmt(dvm.get("valuation"))
                momentum   = _fmt(dvm.get("momentum"))
                print(f"  {PASS}  {sym} DVM: composite={composite}  D={durability}  V={valuation}  M={momentum}")
            else:
                print(f"  {WARN}  get_trendlyne_dvm({sym}): returned None (DVM scores not critical)")

            passed = True
            break

        if not passed:
            print(f"  {FAIL}  All sample symbols returned None — Trendlyne fundamentals scraper is broken")
            print("         Check: TRENDLYNE_SESSION/CSRF valid? Trendlyne HTML structure changed?")
            return False, last_error

        return True, f"{sym} fundamentals OK"

    except Exception as exc:
        print(f"  {FAIL}  Exception: {exc}")
        log.debug("Fundamentals fetcher exception", exc_info=True)
        return False, str(exc)


# ─────────────────────────────────────────────────────────────────────────────
# Module 3 — Analyst targets fetcher
# ─────────────────────────────────────────────────────────────────────────────

def check_analyst_fetcher() -> tuple[bool, str]:
    """
    Scrapes analyst consensus targets and ratings from Trendlyne.
    Returns (passed, detail_string).
    """
    print("\n── Module 3: trendlyne_analyst_fetcher (analyst targets) ───────")
    try:
        from data.trendlyne_analyst_fetcher import get_analyst_targets, interpret_analyst_targets

        passed = False
        last_error = "no symbols tried"

        for sym in SAMPLE_SYMBOLS:
            t0 = time.time()
            targets = get_analyst_targets(sym, force_refresh=True)   # bypass 6h cache for health check
            elapsed = time.time() - t0

            err = targets.get("error")
            if err:
                print(f"  {WARN}  get_analyst_targets({sym}): error={err} ({elapsed:.1f}s)")
                last_error = str(err)
                continue

            consensus_target = _fmt(targets.get("consensus_target"), "₹")
            analyst_count    = targets.get("analyst_count")
            buy_pct          = _fmt(targets.get("buy_pct"), "%")
            rating           = targets.get("consensus_rating") or "None"
            upside           = _fmt(targets.get("upside_to_consensus"), "%")
            price_tl         = _fmt(targets.get("current_price_tl"), "₹")

            # Validate: warn if ALL key fields are None (cookie expired / HTML changed)
            key_fields = [targets.get("consensus_target"), targets.get("analyst_count"),
                          targets.get("buy_pct"), targets.get("consensus_rating")]
            non_none_count = sum(1 for f in key_fields if f is not None)

            if non_none_count == 0:
                print(f"  {WARN}  {sym}: all analyst fields are None ({elapsed:.1f}s)")
                print("         __NEXT_DATA__ parser found no matching keys.")
                print("         The cookies may be valid but Trendlyne changed their data schema.")
                last_error = f"{sym} all None"
                continue

            print(f"  {PASS}  {sym} ({elapsed:.1f}s): target={consensus_target}  "
                  f"analysts={analyst_count}  buy%={buy_pct}  rating={rating}  "
                  f"upside={upside}  price={price_tl}")

            # Fake-SELL guard: if buy_pct==0.0 with a valid analyst_count, that's the old bug
            buy_pct_raw = targets.get("buy_pct")
            if buy_pct_raw is not None and buy_pct_raw == 0.0 and analyst_count and analyst_count > 5:
                print(f"  {FAIL}  BUG DETECTED: buy_pct=0.0 with {analyst_count} analysts "
                      f"— this is the fake-SELL bug (all stocks would show SELL)")
                return False, "fake-SELL bug still present"

            # Interpret signal
            interpretation = interpret_analyst_targets(targets, our_upside_pct=15.0)
            signal  = interpretation.get("signal", "N/A")
            summary = interpretation.get("summary", "")
            print(f"         Signal: {signal} — {summary}")

            # EPS estimates (used by fundamental agent)
            eps_cur  = _fmt(targets.get("eps_current_yr"), "₹")
            eps_next = _fmt(targets.get("eps_next_yr"), "₹")
            print(f"         EPS estimates: current_yr={eps_cur}  next_yr={eps_next}")

            passed = True
            break

        if not passed:
            # All symbols had errors — check if it's a cookie/auth issue
            print(f"  {FAIL}  All sample symbols failed")
            print("         Most likely cause: TRENDLYNE_SESSION / TRENDLYNE_CSRF expired")
            print("         Fix: log in at trendlyne.com → DevTools → Application → Cookies")
            print("              copy '.trendlyne' → TRENDLYNE_SESSION")
            print("              copy 'csrftoken'  → TRENDLYNE_CSRF")
            print("         Or set TRENDLYNE_USER + TRENDLYNE_PASS for auto-refresh")
            return False, last_error

        return True, f"{sym} analyst targets OK"

    except Exception as exc:
        print(f"  {FAIL}  Exception: {exc}")
        log.debug("Analyst fetcher exception", exc_info=True)
        return False, str(exc)


# ─────────────────────────────────────────────────────────────────────────────
# Cookie freshness probe (fast — just GET homepage)
# ─────────────────────────────────────────────────────────────────────────────

def check_cookie_freshness() -> tuple[bool, str]:
    """
    Quick probe: can we reach trendlyne.com with the current session cookies?
    Returns (passed, detail_string).
    """
    print("\n── Cookie freshness probe ──────────────────────────────────────")
    try:
        from data.trendlyne_analyst_fetcher import _get_session, _TL_SESS, _TL_CSRF
        import requests

        sess = os.environ.get("TRENDLYNE_SESSION", "")
        csrf = os.environ.get("TRENDLYNE_CSRF", "")

        if not sess or not csrf:
            print(f"  {FAIL}  TRENDLYNE_SESSION or TRENDLYNE_CSRF not set")
            print("         All Trendlyne modules require both cookies to authenticate.")
            return False, "missing credentials"

        session = _get_session()
        t0 = time.time()
        resp = session.get("https://trendlyne.com/", timeout=15, allow_redirects=True)
        elapsed = time.time() - t0

        if resp.status_code == 200:
            # Check if we're logged in (look for user-specific element or login form)
            logged_in = (
                "logout" in resp.text.lower() or
                "my-portfolio" in resp.text.lower() or
                "dashboard" in resp.text.lower()
            )
            if logged_in:
                print(f"  {PASS}  Trendlyne reachable + logged in ({elapsed:.1f}s)")
            else:
                # Reached site but may be anonymous (cookie expired)
                login_page = "login" in resp.url.lower() or "csrfmiddlewaretoken" in resp.text
                if login_page:
                    print(f"  {WARN}  Trendlyne redirected to login — session cookie likely expired ({elapsed:.1f}s)")
                    print("         Update TRENDLYNE_SESSION + TRENDLYNE_CSRF or set TRENDLYNE_USER+PASS")
                    return False, "session expired"
                else:
                    print(f"  {WARN}  Trendlyne reachable but login status unclear ({elapsed:.1f}s)")
            return True, f"HTTP {resp.status_code}"
        elif resp.status_code in (401, 403):
            print(f"  {FAIL}  HTTP {resp.status_code} — session cookie rejected ({elapsed:.1f}s)")
            return False, f"HTTP {resp.status_code}"
        else:
            print(f"  {WARN}  HTTP {resp.status_code} from trendlyne.com ({elapsed:.1f}s)")
            return resp.status_code < 500, f"HTTP {resp.status_code}"

    except Exception as exc:
        print(f"  {FAIL}  Exception reaching trendlyne.com: {exc}")
        return False, str(exc)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Trendlyne health check")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 if any check fails (default: always exit 0 — warn-only)",
    )
    parser.add_argument(
        "--fno-only",
        action="store_true",
        help="Only run the F&O fetcher check (fastest, no cookie needed)",
    )
    args = parser.parse_args()

    print("=" * 65)
    print("  Trendlyne Health Check")
    print(f"  Sample symbols: {', '.join(SAMPLE_SYMBOLS)}")
    print("=" * 65)

    _env_summary()

    results: dict[str, tuple[bool, str]] = {}

    # Always run cookie probe first (fast)
    if not args.fno_only:
        results["cookies"] = check_cookie_freshness()

    # Module 1: F&O (independent of session cookies — uses S3 presigned URL)
    results["fno"] = check_fno_fetcher()

    if not args.fno_only:
        # Module 2: Fundamentals (requires valid session)
        results["fundamentals"] = check_fundamentals_fetcher()

        # Module 3: Analyst targets (requires valid session)
        results["analyst"] = check_analyst_fetcher()

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 65)
    print("  Summary")
    print("=" * 65)

    all_passed = True
    labels = {
        "cookies":      "Cookie freshness      ",
        "fno":          "F&O / options         ",
        "fundamentals": "Fundamentals / DVM    ",
        "analyst":      "Analyst targets       ",
    }
    for key, (passed, detail) in results.items():
        icon   = PASS if passed else FAIL
        label  = labels.get(key, key)
        print(f"  {icon}  {label}  {detail}")
        if not passed:
            all_passed = False

    print("=" * 65)
    if all_passed:
        print("  All checks passed — Trendlyne is healthy ✅")
    else:
        failed = [k for k, (p, _) in results.items() if not p]
        print(f"  {len(failed)} check(s) failed: {', '.join(failed)}")
        if not args.strict:
            print("  Worker will start anyway (use --strict to block on failure).")

    print("=" * 65 + "\n")

    if args.strict and not all_passed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
