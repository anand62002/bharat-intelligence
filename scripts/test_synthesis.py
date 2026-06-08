"""
scripts/test_synthesis.py — Pre-Launch Synthesis Smoke Test
============================================================
Run this before the 06:00 IST orchestrator job to verify the full synthesis
pipeline works for a sample symbol. Catches:

  - Import errors in newly deployed code
  - Anthropic / OpenAI API connectivity
  - Claude synthesis JSON parse failures
  - Synthesis-validator kappa score (would this publish?)
  - Morning digest Supabase read
  - Suppressed-rec logging (PGRST204 schema-cache errors)

Usage (local or Railway shell):
  python scripts/test_synthesis.py                    # test GAIL.NS (default)
  python scripts/test_synthesis.py --symbol RELIANCE  # test RELIANCE.NS
  python scripts/test_synthesis.py --symbol GAIL --no-validate   # skip judge panel
  python scripts/test_synthesis.py --fast             # skip warren_bot (slow)

Exit codes:
  0 — all checks passed (synthesis would publish)
  1 — synthesis would be SUPPRESSED (kappa too low) or hard failure
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

# ── Project root ──────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("test_synthesis")

# ── ANSI colours ─────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def ok(msg):   print(f"  {GREEN}✓{RESET} {msg}")
def warn(msg): print(f"  {YELLOW}⚠{RESET} {msg}")
def fail(msg): print(f"  {RED}✗{RESET} {msg}")
def hdr(msg):  print(f"\n{BOLD}{msg}{RESET}")


# ─────────────────────────────────────────────────────────────────────────────
# Step helpers
# ─────────────────────────────────────────────────────────────────────────────

def check_env() -> bool:
    hdr("1. Environment variables")
    required = ["SUPABASE_URL", "SUPABASE_SERVICE_KEY", "ANTHROPIC_API_KEY"]
    optional = ["OPENAI_API_KEY", "HF_API_TOKEN"]
    all_ok = True
    for k in required:
        if os.getenv(k):
            ok(f"{k} set")
        else:
            fail(f"{k} MISSING — required")
            all_ok = False
    for k in optional:
        if os.getenv(k):
            ok(f"{k} set (optional)")
        else:
            warn(f"{k} not set (optional — GPT judge will fall back to Haiku)")
    return all_ok


def check_imports() -> bool:
    hdr("2. Import checks")
    modules = [
        ("agents.technical",          "analyse"),
        ("agents.fundamental",        "analyse"),
        ("agents.sentiment",          "analyse"),
        ("agents.macro",              "analyse"),
        ("agents.institutional",      "analyse"),
        ("agents.historical_rag",     "analyse"),
        ("agents.warren_bot",         "analyse"),
        ("scheduler.synthesis_validator", "validate_synthesis"),
        ("scheduler.orchestrator",    "_supabase"),
    ]
    all_ok = True
    for mod, attr in modules:
        try:
            m = __import__(mod, fromlist=[attr])
            getattr(m, attr)
            ok(f"{mod}.{attr}")
        except Exception as exc:
            fail(f"{mod}.{attr}: {exc}")
            all_ok = False
    return all_ok


def check_morning_digest() -> dict | None:
    hdr("3. Morning digest (Supabase read)")
    try:
        from scheduler.orchestrator import _supabase
        client = _supabase()
        if not client:
            warn("Supabase not available — digest check skipped")
            return None
        today = date.today().isoformat()
        row = (client.table("market_digests")
               .select("market_mood,nifty_signal,sectors_in_focus,digest_type,digest_date")
               .eq("digest_type", "MORNING")
               .eq("digest_date", today)
               .limit(1)
               .execute()
               .data)
        if row:
            d = row[0]
            ok(f"Today's MORNING digest found: mood={d.get('market_mood')} nifty={str(d.get('nifty_signal',''))[:50]}")
            return d
        else:
            warn("No MORNING digest for today — orchestrator will try yesterday's CLOSING")
            # Try yesterday
            from datetime import timedelta
            yesterday = (date.today() - timedelta(days=1)).isoformat()
            row2 = (client.table("market_digests")
                    .select("market_mood,nifty_signal,digest_type,digest_date")
                    .eq("digest_type", "CLOSING")
                    .eq("digest_date", yesterday)
                    .limit(1)
                    .execute()
                    .data)
            if row2:
                d2 = row2[0]
                ok(f"Yesterday's CLOSING digest found as fallback: mood={d2.get('market_mood')}")
                return d2
            else:
                warn("No fallback digest either — synthesis will run without market context")
                return None
    except Exception as exc:
        fail(f"Digest read failed: {exc}")
        return None


def run_agents(symbol: str, skip_warren: bool = False) -> dict:
    hdr(f"4. Running agents for {symbol}")
    from agents.technical     import analyse as tech_analyse
    from agents.fundamental   import analyse as fund_analyse
    from agents.sentiment     import analyse as sent_analyse
    from agents.macro         import analyse as macro_analyse
    from agents.institutional import analyse as inst_analyse
    from agents.historical_rag import analyse as rag_analyse

    results: dict = {}

    for name, fn, args in [
        ("technical",      tech_analyse,  (symbol,)),
        ("fundamental",    fund_analyse,  (symbol,)),
        ("sentiment",      sent_analyse,  (symbol,)),
        ("macro",          macro_analyse, ()),
        ("institutional",  inst_analyse,  (symbol,)),
        ("historical_rag", rag_analyse,   (symbol,)),
    ]:
        t0 = time.time()
        try:
            r = fn(*args)
            elapsed = time.time() - t0
            sig   = r.get("signal", "?")
            score = r.get("score", "?")
            ok(f"{name}: signal={sig} score={score}  ({elapsed:.1f}s)")
            results[name] = r
        except Exception as exc:
            warn(f"{name} failed: {exc}")
            results[name] = {}

    if not skip_warren:
        try:
            from agents.warren_bot import analyse as warren_analyse
            t0 = time.time()
            r = warren_analyse(symbol.replace(".NS", "").replace(".BO", ""))
            elapsed = time.time() - t0
            ok(f"warren_bot: signal={r.get('signal')} score={r.get('score')}  ({elapsed:.1f}s)")
            results["warren_bot"] = r
        except Exception as exc:
            warn(f"warren_bot failed (non-blocking): {exc}")
            results["warren_bot"] = {}

    return results


def run_synthesis(symbol: str, agent_results: dict) -> dict | None:
    hdr("5. Claude synthesis")
    try:
        import anthropic
        from scheduler.orchestrator import (
            _load_synthesis_prompt,
            _load_semantic_layer,
            _format_agent_outputs,
            _composite_score,
            CLAUDE_MODEL, CLAUDE_MAX_TOKENS,
        )
    except ImportError as exc:
        fail(f"Cannot import synthesis helpers: {exc}")
        return None

    ant_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not ant_key:
        fail("ANTHROPIC_API_KEY not set — synthesis cannot run")
        return None

    try:
        prompt_template = _load_synthesis_prompt()
        semantic_layer  = _load_semantic_layer()
        weights: dict   = {}

        if not prompt_template:
            fail("Synthesis prompt template missing — check prompts/orchestrator_synthesis.txt")
            return None
        ok(f"Prompt template loaded ({len(prompt_template)} chars)")
        if semantic_layer:
            ok(f"Semantic layer loaded ({len(semantic_layer)} chars)")
        else:
            warn("Semantic layer missing (prompts/docs/semantic_layer.md)")

        composite = _composite_score(agent_results, weights, regime=None)
        agent_text = _format_agent_outputs(symbol, agent_results, weights)

        prompt = (
            prompt_template
            .replace("{symbol}",          str(symbol))
            .replace("{agent_outputs}",   agent_text)
            .replace("{composite_score}", f"{composite:.1f}")
            .replace("{current_price}",   "N/A")
        )
        if semantic_layer:
            prompt = (
                "## BUSINESS SEMANTICS CONTEXT\n"
                "The following reference document defines all financial metrics, "
                "Indian market conventions, disambiguation rules, and data-source "
                "quirks used in this analysis. Use it to correctly interpret every "
                "field value in the agent outputs below.\n\n"
                + semantic_layer + "\n\n---\n\n" + prompt
            )

        log.info("[%s] Synthesis prompt built (%d chars)", symbol, len(prompt))

        ant_client = anthropic.Anthropic(api_key=ant_key)
        t0 = time.time()
        resp = ant_client.messages.create(
            model      = CLAUDE_MODEL,
            max_tokens = CLAUDE_MAX_TOKENS,
            messages   = [{"role": "user", "content": prompt}],
        )
        elapsed = time.time() - t0
        raw_text = resp.content[0].text.strip()

        # Parse JSON (same logic as orchestrator)
        import re
        m = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if not m:
            fail(f"Claude response has no JSON block ({elapsed:.1f}s)")
            print(f"     Raw response snippet: {raw_text[:300]}")
            return None

        synthesis_data = json.loads(m.group(0))
        action     = synthesis_data.get("action", "?")
        confidence = synthesis_data.get("confidence", "?")
        upside     = synthesis_data.get("upside_pct", "?")
        ok(f"Synthesis parsed OK: action={action} confidence={confidence}% upside={upside}%  ({elapsed:.1f}s)")
        return synthesis_data

    except json.JSONDecodeError as exc:
        fail(f"JSON parse failed: {exc}")
        return None
    except Exception as exc:
        fail(f"Synthesis call failed: {exc}")
        return None


async def run_validation(symbol: str, synthesis_data: dict, agent_results: dict) -> bool:
    hdr("6. Synthesis validation (judge panel)")
    try:
        import anthropic
        from scheduler.synthesis_validator import validate_synthesis, KAPPA_SUPPRESS
    except ImportError as exc:
        fail(f"Cannot import validator: {exc}")
        return False

    ant_key = os.getenv("ANTHROPIC_API_KEY", "")
    ant_client = anthropic.Anthropic(api_key=ant_key) if ant_key else None

    t0 = time.time()
    try:
        outcome = await validate_synthesis(symbol, synthesis_data, agent_results, ant_client)
        elapsed = time.time() - t0

        kappa = outcome.aggregate_kappa
        status = outcome.status

        dim_line = "  ".join(
            f"{n[:10]}={d.kappa:.3f}" for n, d in outcome.dimensions.items()
        )
        print(f"     κ breakdown: {dim_line}")
        print(f"     Aggregate κ={kappa:.3f}  threshold={KAPPA_SUPPRESS}  →  {status}  ({elapsed:.1f}s)")

        if status == "SUPPRESSED":
            fail(f"Would be SUPPRESSED tomorrow — kappa {kappa:.3f} < {KAPPA_SUPPRESS}")
            print(f"     Reason: {outcome.suppression_reason}")
            if outcome.judge_errors:
                print(f"     Judge errors: {outcome.judge_errors[:3]}")
            return False
        elif status == "QUALIFIED":
            warn(f"Would publish as QUALIFIED (κ={kappa:.3f} ≥ {KAPPA_SUPPRESS}, caveats added)")
            for c in outcome.caveats:
                print(f"     Caveat: {c[:80]}…")
            return True
        else:
            ok(f"Would PASS validation (κ={kappa:.3f} ≥ {KAPPA_SUPPRESS})")
            return True

    except Exception as exc:
        fail(f"Validation failed: {exc}")
        return False


def check_suppressed_rec_log(symbol: str, synthesis_data: dict) -> bool:
    hdr("7. Suppressed-rec logging (PGRST204 check)")
    try:
        from scheduler.orchestrator import _log_suppressed_synthesis, _supabase
        from scheduler.synthesis_validator import ValidationOutcome, DimensionResult

        client = _supabase()
        if not client:
            warn("Supabase not available — logging check skipped")
            return True

        # Build a dummy suppressed outcome
        dim = DimensionResult(
            name="test", scores={}, rationales={}, quality=0.0, agreement=0.0, kappa=0.0
        )
        dummy_outcome = ValidationOutcome(
            status="SUPPRESSED",
            aggregate_kappa=0.30,
            dimensions={"test": dim},
            failed_dimensions=["test"],
            caveats=[],
            suppression_reason="dry-run test",
            judge_errors=[],
            elapsed_seconds=0.1,
        )
        _log_suppressed_synthesis(f"_TESTDRYRUN_{symbol}", synthesis_data, dummy_outcome, dry_run=False)
        ok("Suppressed-rec log insert succeeded (no PGRST204)")

        # Clean up the test row
        try:
            client.table("recommendations").delete().eq("symbol", f"_TESTDRYRUN_{symbol}").execute()
        except Exception:
            pass

        return True

    except Exception as exc:
        fail(f"Suppressed-rec log failed: {exc}")
        if "PGRST204" in str(exc):
            fail("→ Schema cache error — Supabase may need schema cache refresh or column is missing")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Pre-launch synthesis smoke test")
    parser.add_argument("--symbol",      default="GAIL",    help="NSE symbol to test (default: GAIL)")
    parser.add_argument("--no-validate", action="store_true", help="Skip judge validation panel (faster)")
    parser.add_argument("--fast",        action="store_true", help="Skip warren_bot (saves ~20s)")
    args = parser.parse_args()

    symbol = args.symbol.upper().strip()
    if "." not in symbol:
        symbol = f"{symbol}.NS"

    print(f"\n{'='*60}")
    print(f"  Bharat Intelligence — Synthesis Pre-Launch Test")
    print(f"  Symbol: {symbol}   Date: {date.today()}  Time: {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*60}")

    failures: list[str] = []

    # Step 1: env vars
    if not check_env():
        failures.append("env vars missing")

    # Step 2: imports
    if not check_imports():
        failures.append("import errors")
        print(f"\n{RED}ABORT: Import errors prevent further testing.{RESET}")
        sys.exit(1)

    # Step 3: morning digest
    check_morning_digest()

    # Step 4: run agents
    agent_results = run_agents(symbol, skip_warren=args.fast)
    live_agents = sum(1 for v in agent_results.values() if v)
    if live_agents < 3:
        warn(f"Only {live_agents}/7 agents returned data — synthesis quality may be low")

    # Step 5: synthesis
    synthesis_data = run_synthesis(symbol, agent_results)
    if synthesis_data is None:
        failures.append("synthesis failed")
    else:
        # Step 7: suppressed-rec log test (before validation so we catch PGRST204 early)
        check_suppressed_rec_log(symbol, synthesis_data)

        # Step 6: validation
        if not args.no_validate:
            passed = asyncio.run(run_validation(symbol, synthesis_data, agent_results))
            if not passed:
                failures.append("validation suppressed")
        else:
            warn("Validation skipped (--no-validate)")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    if not failures:
        print(f"  {GREEN}{BOLD}ALL CHECKS PASSED{RESET} — tomorrow's 06:00 IST run should work ✓")
        print(f"{'='*60}\n")
        sys.exit(0)
    else:
        print(f"  {RED}{BOLD}ISSUES FOUND:{RESET}")
        for f_ in failures:
            print(f"    • {f_}")
        print(f"\n  Review the output above and fix before the 06:00 IST run.")
        print(f"{'='*60}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
