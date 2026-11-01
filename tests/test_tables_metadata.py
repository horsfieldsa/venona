"""`db/tables.py` and `db/migrations/*.sql` describe the same schema.

Two sources of truth for one schema is normally a mistake. This is the thing that
makes it not one: the migrations own the DDL and carry the reasoning, `tables.py`
describes the result so queries can be built against typed objects, and this test
reflects the migrated database and compares them.

WITHOUT THIS, `tables.py` IS A LIABILITY. A column added by a migration and
forgotten here produces a repository that cannot see it; a column removed produces
a query that fails at runtime against a table that no longer has it. Both are
silent at import time, which is the whole reason a generated file needs a gate
rather than a docstring asking people to remember.
"""

from __future__ import annotations

import pytest
from sqlalchemy import MetaData, create_engine
from venona.db import tables


@pytest.fixture
def reflected(database_url: str) -> MetaData:
    """The schema as MIGRATED, read back from a real database.

    Function-scoped, matching `database_url`: each test gets its own database from
    the template, so a module-scoped reflection would be describing a database
    that no longer exists by the second test.
    """
    url = database_url
    metadata = MetaData()
    metadata.reflect(bind=create_engine(url.replace("postgresql://", "postgresql+psycopg://")))
    return metadata


def test_every_migrated_table_is_described(reflected) -> None:
    migrated = set(reflected.tables) - {"schema_migrations", "alembic_version"}
    described = set(tables.metadata.tables)
    missing = migrated - described
    assert not missing, (
        f"{len(missing)} table(s) exist in the database and not in `db/tables.py`: "
        f"{sorted(missing)}. A repository cannot query what is not described here."
    )


def test_nothing_is_described_that_does_not_exist(reflected) -> None:
    migrated = set(reflected.tables)
    stale = set(tables.metadata.tables) - migrated
    assert not stale, (
        f"{len(stale)} table(s) are described in `db/tables.py` and do not exist: "
        f"{sorted(stale)}. `merge_proposals` was exactly this -- created by "
        f"`0001_init.sql` and dropped by `0006`, but still visible in the "
        f"concatenated `docs/schema.sql`, which is how it ended up in a migration "
        f"written eighteen versions later."
    )


def test_every_column_matches(reflected) -> None:
    """Column-level, because a table that exists with the wrong shape is worse
    than one that is missing: the import succeeds and the query does not."""
    problems: list[str] = []
    for name, described in sorted(tables.metadata.tables.items()):
        actual = reflected.tables.get(name)
        if actual is None:
            continue  # covered above
        described_cols = {c.name for c in described.columns}
        actual_cols = {c.name for c in actual.columns}
        for column in sorted(actual_cols - described_cols):
            problems.append(f"{name}.{column} is in the database and not in tables.py")
        for column in sorted(described_cols - actual_cols):
            problems.append(f"{name}.{column} is in tables.py and not in the database")
    assert not problems, "\n".join(problems)


def test_primary_keys_match(reflected) -> None:
    """The composite ones are the point. `run_events` is `(run_id, seq)` and C6
    depends on it; `entity_mappings` is `(from_id, merge_id)` and C1 does."""
    problems: list[str] = []
    for name, described in sorted(tables.metadata.tables.items()):
        actual = reflected.tables.get(name)
        if actual is None:
            continue
        want = [c.name for c in described.primary_key]
        got = [c.name for c in actual.primary_key]
        if sorted(want) != sorted(got):
            problems.append(f"{name}: tables.py says {want}, the database says {got}")
    assert not problems, "\n".join(problems)


def test_the_contracts_that_depend_on_a_composite_key_still_do() -> None:
    """Named rather than derived, so the two contracts fail by name.

    A generated file regenerated against a broken database would agree with it;
    these two assertions do not depend on reflection at all.
    """
    assert [c.name for c in tables.run_events.primary_key] == ["run_id", "seq"]
    assert sorted(c.name for c in tables.entity_mappings.primary_key) == [
        "from_id",
        "merge_id",
    ]
