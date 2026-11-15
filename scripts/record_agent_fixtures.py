"""
scripts/record_agent_fixtures.py — record one real output per agent (WP-016, ADR-005).

Writes tests/fixtures/agent_outputs/<agent>.json, the recorded outputs that
tests/test_agent_contracts.py checks consumer keys against.

Runs every agent's real analyse() once for one symbol with Supabase and LLM keys
REMOVED from the environment, so no agent can write to the database or call a
paid model; market-data fetches (yfinance, screener.in) are read-only. Agents
that need a key for their best path (sentiment's Haiku classifier, RAG's
embeddings) record their keyless path — the keys consumers read are the same.

Re-record when an agent's output changes on purpose:

    PYTHON_DOTENV_DISABLED=1 python scripts/record_agent_fixtures.py [SYMBOL] [agent,...]

Check the printed signals: a NO_DATA / INSUFFICIENT_DATA run (rate-limited
source) records a degraded path — re-record that agent later.
"""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import date, datetime
from pathlib import Path

for k in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
          "HF_API_TOKEN", "TRENDLYNE_SESSION", "TRENDLYNE_CSRF"):
    os.environ.pop(k, None)
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "tests" / "fixtures" / "agent_outputs"


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_clean(v) for v in o]
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return None
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if hasattr(o, "item"):                       # numpy scalars
        return _clean(o.item())
    if o is None or isinstance(o, (str, int, float, bool)):
        return o
    return str(o)


def main(symbol: str = "TITAN", only: str = "") -> int:
    from agents import (commodities, fundamental, historical_rag, institutional, macro,
                        mgmt_quality, sentiment, technical, warren_bot)
    yf = f"{symbol}.NS"
    runs = {
        "technical":      lambda: technical.analyse(yf),
        "fundamental":    lambda: fundamental.analyse(yf),
        "sentiment":      lambda: sentiment.analyse(yf),
        "institutional":  lambda: institutional.analyse(yf),
        "macro":          macro.analyse,
        "commodities":    commodities.analyse,
        "historical_rag": lambda: historical_rag.analyse(f"{symbol} consumer discretionary India"),
        "warren_bot":     lambda: warren_bot.analyse(symbol),
        "mgmt_quality":   lambda: mgmt_quality.analyse(symbol),
    }
    if only:                                    # e.g. "sentiment,technical"
        runs = {k: v for k, v in runs.items() if k in only.split(",")}
    OUT.mkdir(parents=True, exist_ok=True)
    failed = 0
    for name, fn in runs.items():
        try:
            res = _clean(fn())
        except Exception as exc:
            print(f"{name:16s} FAILED: {exc}")
            failed += 1
            continue
        res["_recorded"] = {"symbol": symbol, "date": date.today().isoformat(),
                            "by": "scripts/record_agent_fixtures.py"}
        (OUT / f"{name}.json").write_text(json.dumps(res, indent=2, sort_keys=True), encoding="utf-8")
        print(f"{name:16s} signal={res.get('signal')!s:12s} keys={len(res)}")
    return 1 if failed else 0


if __name__ == "__main__":
    # python scripts/record_agent_fixtures.py [SYMBOL] [agent1,agent2]
    sys.exit(main(*(sys.argv[1:3] or ["TITAN"])))
