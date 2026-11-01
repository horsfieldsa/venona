"""No SQL outside `db/`. Enforced for the layers that are done.

WHY A LINTER AND NOT A CONVENTION. `db/repositories/` exists so that a query which
must be scoped can be scoped in one place -- the read-scope seam the tenancy work
deferred a `workspace_id` in favour of. A boundary nothing checks is a boundary
that leaks back one convenient `conn.execute` at a time, and the leak is invisible
in review because a raw statement looks exactly like a repository call at the call
site.

NOW WHOLE-BACKEND. This started scoped to `api/routers` while the other layers were
still migrating, and ratcheted one package at a time. Every layer has since landed,
so `CLEAN` is the backend and `ALLOWED` is the exception list -- which is the
direction that stays honest: a new package is covered the moment it exists, rather
than the moment someone remembers to add it here.

Detection is AST-based rather than grep: a `conn.execute("...")` split across four
source lines is one call to the parser and four false negatives to a regex.

WHAT IT DOES NOT CATCH, and why that is the right line. It sees the argument to
`execute`, so a SQL FRAGMENT that a repository function composes and runs is
invisible to it -- `services/freshness.py::DOMAIN_TERMS` and `pipeline/brief.py`'s
funnel `stages` are the two that exist. Both are genuinely per-caller shapes no
fixed query expresses (one aggregate per domain; each stage counted against the
ones before it), both bind their values as parameters, and both still execute
inside a repository. Tightening this to ban the word SELECT anywhere would catch
those two and every docstring that explains a query.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: (package root, prefixes where SQL is allowed to live). For the instance:
#: the repositories themselves, the shim that runs them, and Alembic's
#: baseline, which executes the authored DDL. For the hub: its repositories
#: and its whole `db/` (the ledger runner's own DDL lives in `db/migrate.py`).
PACKAGES = (
    (ROOT / "backend" / "venona", ("db/repositories", "db/core.py", "db/alembic")),
    (ROOT / "hub" / "hub", ("repositories", "db/")),
    # `scripts/` writes SQL too, and it is the half nobody reviews as
    # application code -- so a query that would be refused in a router lives
    # here uninspected. Nothing under it is a repository, hence no exemptions.
    (ROOT / "scripts", ()),
)

#: Writers that take a table NAME and a dict, so the SQL literal lives inside
#: `db/core.py` and this file's AST walk sees only a call. They are repository
#: work wearing a helper's clothes: reached from a router body they leak the
#: read-scope seam exactly as a raw `conn.execute` would, and just as
#: invisibly. Allowed where SQL is allowed; refused in a router.
TABLE_WRITERS = ("insert", "upsert")

#: Where a `db.insert`/`db.upsert` call is a leak rather than a repository.
#: Narrow on purpose -- pipeline stages and services legitimately write through
#: them, and widening this to the whole package is a different, larger change.
WRITER_GUARDED = ("api/routers",)

SQL_START = (
    "select ",
    "insert ",
    "update ",
    "delete ",
    "with ",
    "create ",
    "alter ",
    "drop ",
)


def _looks_like_sql(text: str) -> bool:
    head = " ".join(text.split()).lower()
    return head.startswith(SQL_START)


def _sql_strings(tree: ast.AST) -> dict[str, str]:
    """Module- and function-level names bound to something that looks like SQL.

    `q = "SELECT ..."` then `conn.execute(q)` passed this linter completely:
    the call's first argument is a `Name`, and the walk below only ever read
    literals. One variable is all it takes to move a query out of a repository
    and have the guard agree.
    """
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        text = _literal_text(node.value)
        if text and _looks_like_sql(text):
            bound[target.id] = text
    return bound


def _literal_text(arg: ast.AST) -> str | None:
    """A literal, or a literal concatenated with something (`SELECT + " WHERE"`)."""
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    if isinstance(arg, ast.BinOp) and isinstance(arg.left, ast.Constant):
        return str(arg.left.value)
    if isinstance(arg, ast.JoinedStr):
        return "".join(v.value for v in arg.values if isinstance(v, ast.Constant))
    return None


def offences(path: Path, *, guard_writers: bool = False) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    tree = ast.parse(path.read_text())
    bound = _sql_strings(tree)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if guard_writers and node.func.attr in TABLE_WRITERS and len(node.args) >= 2:
            table = _literal_text(node.args[1])
            if table and table.isidentifier():
                found.append((node.lineno, f"db.{node.func.attr}(conn, {table!r}, ...)"))
            continue
        if node.func.attr not in ("execute", "executemany", "executescript"):
            continue
        if not node.args:
            continue
        arg = node.args[0]
        text = _literal_text(arg)
        if text is None and isinstance(arg, ast.Name):
            text = bound.get(arg.id)
        if text and _looks_like_sql(text):
            found.append((node.lineno, " ".join(text.split())[:70]))
    return found


def main() -> int:
    bad: list[str] = []
    checked = 0
    for package_root, allowed in PACKAGES:
        for path in sorted(package_root.rglob("*.py")):
            rel = path.relative_to(package_root).as_posix()
            if any(rel.startswith(a) for a in allowed):
                continue
            checked += 1
            guard_writers = any(rel.startswith(g) for g in WRITER_GUARDED)
            for lineno, snippet in offences(path, guard_writers=guard_writers):
                bad.append(f"  {path.relative_to(ROOT).as_posix()}:{lineno}  {snippet}")

    if bad:
        print(f"SQL outside db/ in {len(bad)} place(s):", file=sys.stderr)
        print("\n".join(bad), file=sys.stderr)
        print(
            "\nMove it into the package's repositories directory "
            "(`backend/venona/db/repositories/` or `hub/hub/repositories/`). "
            "See the instance package's docstring for the rules.",
            file=sys.stderr,
        )
        return 1

    print(f"lint-repositories ok ({checked} modules hold no SQL)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
