"""`purge_corpus` — the one endpoint that cannot be undone.

WHY THIS FILE EXISTS. Nothing exercised `purge_corpus` before Postgres landed.
On SQLite that was survivable: foreign keys were not enforced by default, so the
delete order in `CORPUS_TABLES` was, as its own comment said, "only for
readability". Postgres enforces all twelve of the schema's foreign keys
unconditionally, so the order is now load-bearing and the comment was a stale
claim sitting directly on top of the destructive path.

It turns out to be safe, and for a reason worth pinning rather than
rediscovering: every corpus foreign key is either `ON DELETE CASCADE` from a
parent that is itself purged, or points at a table that is PRESERVED. The one
shape that would break -- a `NO ACTION` parent deleted before its child -- does
not occur. `test_no_parent_is_deleted_before_its_children` asserts that against
the live catalogue rather than against a list someone maintains by hand, so a
migration that adds a foreign key in the dangerous direction fails here instead
of at the moment an operator clicks Purge.
"""

from __future__ import annotations

from venona.db import core as db
from venona.services import purge


def _fk_edges(conn: db.Conn) -> list[tuple[str, str, str]]:
    """(child, parent, delete_rule) for every foreign key in the schema."""
    rows = conn.execute(
        """
        SELECT tc.table_name AS child, ccu.table_name AS parent, rc.delete_rule AS rule
          FROM information_schema.table_constraints tc
          JOIN information_schema.constraint_column_usage ccu
            ON ccu.constraint_name = tc.constraint_name
          JOIN information_schema.referential_constraints rc
            ON rc.constraint_name = tc.constraint_name
         WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'
        """
    ).fetchall()
    return [(r["child"], r["parent"], r["rule"]) for r in rows]


class TestTheDeleteOrderIsSafe:
    def test_no_parent_is_deleted_before_its_children(self, conn: db.Conn) -> None:
        """The failure this catches is a 23503 mid-purge, inside one transaction.

        `CASCADE` is exempt because Postgres removes the children itself, and a
        parent outside `CORPUS_TABLES` is exempt because it is never deleted at
        all. What is left is the dangerous shape, and there should be none of it.
        """
        order = {name: i for i, name in enumerate(purge.CORPUS_TABLES)}
        offences = [
            f"{parent} (position {order[parent]}) is deleted before its child "
            f"{child} (position {order[child]}), and the constraint is {rule} "
            f"rather than CASCADE"
            for child, parent, rule in _fk_edges(conn)
            if rule != "CASCADE"
            and parent in order
            and child in order
            and order[parent] < order[child]
        ]
        assert not offences, "\n".join(offences)

    def test_every_corpus_table_actually_exists(self, conn: db.Conn) -> None:
        """`purge_corpus` skips a table it cannot find, so a typo is silent.

        A misspelled entry would leave that table's rows in place while the UI
        reported a successful purge -- which is the failure mode this whole file
        is about, in miniature.
        """
        present = {
            r["table_name"]
            for r in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            ).fetchall()
        }
        missing = [t for t in purge.CORPUS_TABLES if t not in present]
        assert not missing, f"named in CORPUS_TABLES but not in the schema: {missing}"

    def test_the_two_lists_do_not_overlap(self) -> None:
        """`DataView` renders one as "Removed" and the other as "Kept"."""
        both = set(purge.CORPUS_TABLES) & set(purge.PRESERVED_TABLES)
        assert not both, f"claimed as both purged and kept: {sorted(both)}"


class TestItRuns:
    def test_it_empties_the_corpus_and_reports_what_it_deleted(self, conn: db.Conn) -> None:
        """End to end against real rows, which is the only way the FK order is
        genuinely exercised -- an empty database cannot violate a constraint."""
        before = {
            t: conn.execute(f'SELECT COUNT(*) AS n FROM "{t}"').fetchone()["n"]  # noqa: S608
            for t in purge.CORPUS_TABLES
        }
        assert sum(before.values()) > 0, "the seeded fixture holds no corpus rows"

        deleted = purge.purge_corpus(conn)

        assert deleted == before, "the returned counts are what the UI reports"
        for table in purge.CORPUS_TABLES:
            n = conn.execute(f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()["n"]  # noqa: S608
            assert n == 0, f"{table} still holds {n} rows"

    def test_it_keeps_what_it_promises_to_keep(self, conn: db.Conn) -> None:
        """`sources` in particular: losing it turns the next run into a no-op."""
        kept = {
            t: conn.execute(f'SELECT COUNT(*) AS n FROM "{t}"').fetchone()["n"]  # noqa: S608
            for t in purge.PRESERVED_TABLES
            if t != "schema_migrations"
        }
        purge.purge_corpus(conn)
        for table, n in kept.items():
            after = conn.execute(f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()["n"]  # noqa: S608
            assert after == n, f"{table} lost rows: {n} -> {after}"

    def test_it_is_idempotent(self, conn: db.Conn) -> None:
        """A second purge reports zeroes rather than failing."""
        purge.purge_corpus(conn)
        again = purge.purge_corpus(conn)
        assert set(again.values()) == {0}
