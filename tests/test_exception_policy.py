"""
tests/test_exception_policy.py — no new silent exception handlers (WP-009, ADR-005).

Why this exists
---------------
Five production outages ran for weeks to months unseen because an
`except Exception` swallowed the error: a tz-naive date subtraction returned
None at DEBUG (Performance tab frozen ~3 months), a non-existent column write
logged at DEBUG, and so on. This test walks the AST of every runtime module and
flags a handler that catches everything (`except Exception`, `except
BaseException`, a bare `except:`, or a tuple containing one of them) and then
stays quiet:

  • its body only passes, continues, breaks, or returns None / an empty literal
    ([], {}, (), "", set(), 0, False), or
  • its only logging calls are below WARNING (debug / info),

and it neither re-raises nor logs at WARNING or above.

Every existing site is listed in tests/exception_allowlist.txt as
`path::function::ordinal` (ordinal = position among the flagged handlers in that
function), so moving code around does not break the list. The test fails when

  • a flagged handler is NOT on the list — fix it: log at WARNING+ or raise; or
  • a listed site no longer exists — delete its line, so the list only shrinks.

Regenerate after fixing sites (never to add new ones):

    python tests/test_exception_policy.py --write
"""

from __future__ import annotations

import ast
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIRS = ("agents", "data", "scheduler", "governance", "api")
ALLOWLIST = Path(__file__).resolve().parent / "exception_allowlist.txt"

_BROAD = {"Exception", "BaseException"}
_LOG_LOW = {"debug", "info"}
_LOG_HIGH = {"warning", "warn", "error", "exception", "critical", "fatal"}
_LOG_NAMES = {"log", "logger", "_log", "_logger", "LOG", "logging"}


# ── classification ───────────────────────────────────────────────────────────

def _is_broad(handler: ast.ExceptHandler) -> bool:
    t = handler.type
    if t is None:
        return True                                   # bare except:
    names = t.elts if isinstance(t, ast.Tuple) else [t]
    for n in names:
        if isinstance(n, ast.Name) and n.id in _BROAD:
            return True
        if isinstance(n, ast.Attribute) and n.attr in _BROAD:
            return True
    return False


def _log_level(call: ast.Call) -> Optional[str]:
    """'low' / 'high' for a logger call, else None."""
    f = call.func
    if not isinstance(f, ast.Attribute):
        return None
    owner = f.value
    owner_name = owner.id if isinstance(owner, ast.Name) else (
        owner.attr if isinstance(owner, ast.Attribute) else None)
    if owner_name not in _LOG_NAMES:
        return None
    if f.attr in _LOG_LOW:
        return "low"
    if f.attr in _LOG_HIGH:
        return "high"
    return None


def _is_empty_value(node: Optional[ast.AST]) -> bool:
    if node is None:
        return True
    if isinstance(node, ast.Constant) and node.value in (None, "", 0, False):
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)) and not node.elts:
        return True
    if isinstance(node, ast.Dict) and not node.keys:
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id in ("set", "list", "dict", "tuple") and not node.args and not node.keywords:
        return True
    return False


def _is_trivial(stmt: ast.stmt) -> bool:
    if isinstance(stmt, (ast.Pass, ast.Continue, ast.Break)):
        return True
    if isinstance(stmt, ast.Return):
        return _is_empty_value(stmt.value)
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
        return True                                   # docstring / ellipsis
    return False


def is_silent(handler: ast.ExceptHandler) -> bool:
    """True if this broad handler swallows the error without a WARNING+ trace."""
    if not _is_broad(handler):
        return False
    levels: list[str] = []
    for node in ast.walk(ast.Module(body=handler.body, type_ignores=[])):
        if isinstance(node, ast.Raise):
            return False
        if isinstance(node, ast.Call):
            lvl = _log_level(node)
            if lvl:
                levels.append(lvl)
    if "high" in levels:
        return False
    if levels:                                        # only debug/info
        return True
    return all(_is_trivial(s) for s in handler.body)


# ── site naming ──────────────────────────────────────────────────────────────

def _sites_in_source(source: str, rel: str) -> list[str]:
    tree = ast.parse(source)
    sites: list[str] = []
    counter: dict[str, int] = defaultdict(int)

    def visit(node: ast.AST, scope: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = child.name if scope == "<module>" else f"{scope}.{child.name}"
                visit(child, name)
                continue
            if isinstance(child, ast.ExceptHandler) and is_silent(child):
                sites.append(f"{rel}::{scope}::{counter[scope]}")
                counter[scope] += 1
            visit(child, scope)

    visit(tree, "<module>")
    return sites


def runtime_files() -> Iterable[Path]:
    for d in RUNTIME_DIRS:
        for p in sorted((ROOT / d).rglob("*.py")):
            if "__pycache__" not in p.parts:
                yield p


def current_sites(extra: Optional[dict[str, str]] = None) -> list[str]:
    """All flagged sites in the tree. `extra` maps rel-path → source to scan instead."""
    extra = extra or {}
    out: list[str] = []
    for p in runtime_files():
        rel = p.relative_to(ROOT).as_posix()
        out += _sites_in_source(extra.pop(rel, None) or p.read_text(encoding="utf-8"), rel)
    for rel, src in extra.items():
        out += _sites_in_source(src, rel)
    return sorted(out)


def read_allowlist() -> list[str]:
    if not ALLOWLIST.exists():
        return []
    return sorted(line.strip() for line in ALLOWLIST.read_text(encoding="utf-8").splitlines()
                  if line.strip() and not line.startswith("#"))


def write_allowlist(sites: list[str]) -> None:
    header = (
        "# Silent broad exception handlers that existed when WP-009 landed (2026-11-15).\n"
        "# This list may only SHRINK. Fix a site (log at WARNING+ or re-raise), then\n"
        "# delete its line — or run: python tests/test_exception_policy.py --write\n"
        "# Never add a line: a new silent handler must be fixed instead.\n"
    )
    ALLOWLIST.write_text(header + "\n".join(sites) + "\n", encoding="utf-8")


# ── the policy ───────────────────────────────────────────────────────────────

def test_no_new_silent_exception_handlers():
    sites, allowed = current_sites(), read_allowlist()
    print(f"\nsilent broad exception handlers: {len(sites)} (allowlist {len(allowed)})")
    new = sorted(set(sites) - set(allowed))
    assert not new, (
        f"{len(new)} new silent exception handler(s). Log at WARNING or above, or re-raise "
        "(ADR-005) — do not add them to the allowlist:\n  " + "\n  ".join(new))


def test_allowlist_only_shrinks():
    sites, allowed = current_sites(), read_allowlist()
    stale = sorted(set(allowed) - set(sites))
    assert not stale, (
        f"{len(stale)} allowlisted site(s) no longer exist — delete these lines from "
        "tests/exception_allowlist.txt (or run python tests/test_exception_policy.py --write):\n  "
        + "\n  ".join(stale))


# ── the classifier itself ────────────────────────────────────────────────────

def _flag(src: str) -> bool:
    return bool(_sites_in_source(src, "x.py"))


def test_flags_the_classic_swallow():
    assert _flag("try:\n    x()\nexcept Exception:\n    pass\n")
    assert _flag("try:\n    x()\nexcept:\n    pass\n")
    assert _flag("try:\n    x()\nexcept (ValueError, Exception):\n    return None\n")
    assert _flag("for i in y:\n    try:\n        x()\n    except Exception:\n        continue\n")
    assert _flag("def f():\n    try:\n        x()\n    except Exception:\n        return []\n")


def test_flags_debug_and_info_only_logging():
    assert _flag("try:\n    x()\nexcept Exception as e:\n    log.debug('skip %s', e)\n")
    assert _flag("try:\n    x()\nexcept Exception as e:\n    logger.info('skip %s', e)\n    y = 1\n")


def test_does_not_flag_loud_or_narrow_handlers():
    assert not _flag("try:\n    x()\nexcept Exception as e:\n    log.warning('x %s', e)\n")
    assert not _flag("try:\n    x()\nexcept Exception:\n    log.exception('x')\n    return None\n")
    assert not _flag("try:\n    x()\nexcept Exception:\n    raise\n")
    assert not _flag("try:\n    x()\nexcept ValueError:\n    pass\n")
    assert not _flag("try:\n    x()\nexcept Exception as e:\n    errors.append(str(e))\n")


def test_site_ids_are_function_scoped_ordinals():
    src = ("class A:\n    def m(self):\n        try: x()\n        except Exception: pass\n"
           "        try: y()\n        except Exception: pass\n"
           "def g():\n    try: z()\n    except Exception: return {}\n")
    assert _sites_in_source(src, "p.py") == ["p.py::A.m::0", "p.py::A.m::1", "p.py::g::0"]


def test_injected_swallow_in_a_runtime_module_fails_the_policy():
    """Acceptance: adding `try: x()\\nexcept Exception: pass` to a runtime module is caught."""
    target = next(runtime_files())
    rel = target.relative_to(ROOT).as_posix()
    poisoned = target.read_text(encoding="utf-8") + (
        "\n\ndef _wp009_probe():\n    try:\n        x()\n    except Exception:\n        pass\n")
    new = set(current_sites({rel: poisoned})) - set(read_allowlist())
    assert new == {f"{rel}::_wp009_probe::0"}


if __name__ == "__main__":
    if "--write" in sys.argv:
        s = current_sites()
        write_allowlist(s)
        print(f"wrote {len(s)} sites to {ALLOWLIST}")
    else:
        print(len(current_sites()))
