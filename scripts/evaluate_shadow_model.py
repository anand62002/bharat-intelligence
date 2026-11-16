"""
scripts/evaluate_shadow_model.py — print the shadow-model evaluation as markdown (WP-017, ADR-006).

Reads labelled rows from the candidates ledger and runs the walk-forward
evaluation in agents/shadow_model.py. Writes nothing to the database.

Prints the report for each horizon, or the explicit refusal line
"insufficient data: N rows, W weeks" while the ledger holds fewer than 300
labelled rows across 8 distinct weeks.

    python scripts/evaluate_shadow_model.py                 # t+30 (provisional) and t+90
    python scripts/evaluate_shadow_model.py --horizon 90
    python scripts/evaluate_shadow_model.py --source portfolio
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents import shadow_model as sm  # noqa: E402


def main(argv: list[str] | None = None, client=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--horizon", type=int, choices=(30, 90), action="append",
                    help="label horizon in days (repeatable; default both)")
    ap.add_argument("--source", default="discovery", choices=("discovery", "portfolio", "multibagger"))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    if client is None:
        from data.candidate_ledger import get_client
        client = get_client()
    if client is None:
        print("cannot evaluate: no Supabase client (SUPABASE_URL / SUPABASE_SERVICE_KEY not set)")
        return 2

    print(f"## Shadow model evaluation — source={args.source}\n")
    for h in args.horizon or [30, 90]:
        try:
            rows = sm.load_labelled(client, h, args.source)
        except Exception as exc:
            print(f"### Shadow model — t+{h}d\n\ncannot read the candidates ledger: {exc}\n")
            return 1
        print(sm.to_markdown(sm.evaluate(rows, horizon=h)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
