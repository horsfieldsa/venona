"""The SQL boundary linter sees what it claims to see.

`make lint-repositories` passing means nothing if the detector has holes, and
it had two: a query held in a variable, and a write reached through
`db.insert`/`db.upsert` from a router body. Both look like ordinary calls to an
AST walk that only reads literals, so the guard agreed the boundary was intact
while eleven scripts and four routers sat outside it.

Fed synthetic sources rather than the tree, so a fix that narrows detection
fails here rather than the next time someone leaks a statement.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def linter():
    spec = importlib.util.spec_from_file_location(
        "lint_repositories", ROOT / "scripts" / "lint_repositories.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["lint_repositories"] = module
    spec.loader.exec_module(module)
    return module


def _write(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "sample.py"
    path.write_text(source)
    return path


def test_a_literal_statement_is_flagged(linter, tmp_path: Path) -> None:
    path = _write(tmp_path, 'conn.execute("SELECT 1 FROM entities")\n')
    assert linter.offences(path), "the baseline case must still be caught"


def test_a_statement_held_in_a_variable_is_flagged(linter, tmp_path: Path) -> None:
    """⭐ One variable was all it took to move a query out of a repository and
    have the guard agree it had not moved."""
    path = _write(
        tmp_path,
        'q = "SELECT id FROM entities WHERE status = ?"\nconn.execute(q, ("active",))\n',
    )
    assert linter.offences(path), "a name bound to SQL is still SQL"


def test_a_write_through_db_upsert_is_flagged_in_a_router(linter, tmp_path: Path) -> None:
    """⭐ The SQL literal lives in `db/core.py`, so the walk sees only a call --
    but a router reaching it leaks the read-scope seam exactly as a raw
    statement would, and just as invisibly."""
    path = _write(tmp_path, 'db.upsert(conn, "aliases", row, key=["id"])\n')

    assert linter.offences(path, guard_writers=True), "a router write must be caught"
    assert not linter.offences(path, guard_writers=False), (
        "the same call in a pipeline stage is ordinary, not a leak"
    )


def test_a_docstring_that_explains_a_query_is_not_flagged(linter, tmp_path: Path) -> None:
    """The line this linter draws: it reads what is EXECUTED, so prose about a
    query stays prose."""
    path = _write(tmp_path, '"""This function runs SELECT * FROM entities."""\nx = 1\n')
    assert not linter.offences(path)


def test_the_scripts_package_is_walked(linter) -> None:
    """`scripts/` is the half nobody reviews as application code, so a query
    written there was a query nothing looked at."""
    roots = [root for root, _ in linter.PACKAGES]
    assert ROOT / "scripts" in roots
