"""Every `.sql` migration is replayed by an alembic revision.

THE FAILURE THIS CATCHES, because it has happened. `db/migrate` runs alembic, and
alembic runs revisions -- nothing globs `db/migrations/`. So a `.sql` file added
without a paired revision is on disk, in the diff, in review, and never executed.
The table simply does not exist, and the first symptom is something unrelated
falling over: `0014_config_events.sql` presented as
`test_tables_metadata.py::test_nothing_is_described_that_does_not_exist`, three
files away from the cause, in a concurrent session's test run.

A mechanical gate rather than a convention, for the reason every other sweep in
this suite exists: a missing revision is invisible in review, and a name missing
from a list is not.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from venona.db import core as db

MIGRATIONS = Path(db.__file__).resolve().parent / "migrations"
VERSIONS = Path(db.__file__).resolve().parent / "alembic" / "versions"


def _sql_files() -> set[str]:
    return {p.name for p in MIGRATIONS.glob("*.sql")}


def _replayed() -> set[str]:
    """Every `.sql` filename named by any revision.

    Read as TEXT, not imported: a revision's `upgrade()` needs a live database,
    and the question here is only which filenames the module names. That also
    means this sees the baseline's `FILES` tuple and the later revisions'
    `SQL_FILE` constants through one rule instead of two.
    """
    named: set[str] = set()
    for path in VERSIONS.glob("*.py"):
        named.update(re.findall(r'"(\d{4}_[a-z0-9_]+\.sql)"', path.read_text()))
    return named


def test_every_sql_migration_is_replayed_by_a_revision() -> None:
    missing = sorted(_sql_files() - _replayed())
    assert not missing, (
        f"{len(missing)} .sql migration(s) no alembic revision replays: {missing}.\n"
        f"They will never run. Add a revision under db/alembic/versions/ that "
        f"executes each one -- 0011_acronym_scope.py is the pattern -- and set its "
        f"`down_revision` to the current head."
    )


def test_every_replayed_name_exists_on_disk() -> None:
    """The other direction. A revision naming a file that is not there fails at
    migrate time with a `FileNotFoundError` from inside alembic, which is a much
    worse place to learn it than here."""
    dangling = sorted(_replayed() - _sql_files())
    assert not dangling, f"revision(s) name .sql files that do not exist: {dangling}"


def _executed_through_op_execute() -> set[str]:
    """The `.sql` files run through `op.execute`, which is where the hazard is.

    NOT every migration. `0001_baseline.py` replays its three files with
    `connection.exec_driver_sql`, which hands the string to the driver whole and
    never parses it -- so those files are immune by construction, and
    `0001_baseline.sql` does in fact contain a `:i` that has always been
    harmless. Testing them anyway would report a defect in working code, which is
    how a guard teaches people to switch it off.
    """
    named: set[str] = set()
    for path in VERSIONS.glob("*.py"):
        source = path.read_text()
        if "op.execute(SQL_FILE.read_text())" not in source:
            continue
        named.update(re.findall(r'"(\d{4}_[a-z0-9_]+\.sql)"', source))
    return named


@pytest.mark.parametrize("name", sorted(_executed_through_op_execute()))
def test_no_op_execute_migration_carries_a_bind_parameter_marker(name: str) -> None:
    """A colon followed by word characters -- ANYWHERE, comments included.

    `op.execute` hands the file to SQLAlchemy's `text()`, which scans the whole
    string for bind-parameter markers and does not skip comments. A Python slice
    written in a COMMENT (`sha256(content)[:16]`) is read as a required parameter
    named `16`, and the migration dies with "a value is required for bind
    parameter" -- at migrate time, on every environment at once.

    Checked against `text()` itself rather than a regex of our own, so this
    cannot disagree with the thing that actually parses it.
    """
    from sqlalchemy import text

    found = sorted(text((MIGRATIONS / name).read_text())._bindparams)
    assert not found, (
        f"{name} contains bind-parameter marker(s) {found}. SQLAlchemy reads "
        f"`:word` as a parameter even inside a comment. Reword it -- spell a slice "
        f"out in prose rather than writing the colon."
    )
