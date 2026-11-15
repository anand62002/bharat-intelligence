"""
scripts/check_schema.py — does production have every column the code writes? (WP-007)

Read-only. For each table in db/contract.py it selects the contract columns
with limit 0. PostgREST names only the first missing column per error, so the
checker drops it and retries until the select succeeds, collecting every gap.

    python scripts/check_schema.py          # one line per table; exit 1 if anything is missing

Also used by worker.py at start-up (CRITICAL log per missing column) and by
scripts/weekly_audit.py.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from db.contract import CONTRACT  # noqa: E402

TABLE_MISSING = "<table missing>"

_COL_PATTERNS = (
    re.compile(r"column \"?(?:[\w]+\.)?([\w]+)\"? does not exist"),    # 42703
    re.compile(r"Could not find the '([\w]+)' column"),                # PGRST204
)
_TABLE_PATTERNS = ("PGRST205", "42P01", "Could not find the table", "relation")


def _missing_column(msg: str) -> str | None:
    for pat in _COL_PATTERNS:
        m = pat.search(msg)
        if m:
            return m.group(1)
    return None


def check_table(client: Any, table: str, columns: set[str]) -> list[str]:
    """Contract columns production lacks for one table (sorted); [TABLE_MISSING] if the table is absent."""
    remaining = sorted(columns)
    missing: list[str] = []
    for _ in range(len(columns) + 1):
        if not remaining:
            break
        try:
            client.table(table).select(",".join(remaining)).limit(0).execute()
            return sorted(missing)
        except Exception as exc:
            msg = str(exc)
            col = _missing_column(msg)
            if col and col in remaining:
                missing.append(col)
                remaining.remove(col)
                continue
            if any(t in msg for t in _TABLE_PATTERNS) and "column" not in msg:
                return [TABLE_MISSING]
            raise
    return sorted(missing)


def check_schema(client: Any, contract: dict[str, set[str]] | None = None) -> dict[str, list[str]]:
    """{table: [missing columns]} for every contract table. Errors other than a
    missing column/table are reported as `<error: ...>` rather than raised."""
    out: dict[str, list[str]] = {}
    for table, cols in (contract or CONTRACT).items():
        try:
            out[table] = check_table(client, table, cols)
        except Exception as exc:
            out[table] = [f"<error: {str(exc)[:120]}>"]
    return out


def missing_only(result: dict[str, list[str]]) -> dict[str, list[str]]:
    return {t: m for t, m in result.items() if m}


def format_report(result: dict[str, list[str]], contract: dict[str, set[str]] | None = None) -> str:
    contract = contract or CONTRACT
    lines = []
    width = max(len(t) for t in result) if result else 0
    for table, miss in result.items():
        if miss:
            lines.append(f"{table:<{width}}  MISSING: {', '.join(miss)}")
        else:
            lines.append(f"{table:<{width}}  OK ({len(contract.get(table, ()))} columns)")
    return "\n".join(lines)


def _client():
    try:
        from dotenv import load_dotenv
        load_dotenv(_ROOT / ".env", override=True)
    except Exception:
        pass
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not (url and key):
        print("FAIL: SUPABASE_URL / SUPABASE_SERVICE_KEY not set")
        sys.exit(2)
    from supabase import create_client
    return create_client(url, key)


def main() -> int:
    result = check_schema(_client())
    print(format_report(result))
    gaps = missing_only(result)
    if gaps:
        print(f"\n{sum(len(v) for v in gaps.values())} missing column(s) in {len(gaps)} table(s) — "
              "apply the matching file in db/migrations/.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
