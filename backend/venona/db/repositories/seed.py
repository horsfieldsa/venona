"""Every statement that seed runs.

Serves `services/seed.py`. The module keeps its
decisions; this holds the reads and writes they are made of. See
`db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def delete_brief_blocks_by_brief_id(conn: db.Conn, brief_id: str):
    return conn.execute("DELETE FROM brief_blocks WHERE brief_id = ?", (brief_id,))


def delete_brief_items_by_brief_id(conn: db.Conn, brief_id: str):
    """Re-seeding replaces a brief's items rather than adding to them.

    The blocks beside this are deleted for the same reason: `make seed` is run
    repeatedly against the same brief id, and an upsert per item would leave the
    previous run's extras behind whenever the fixture got shorter.
    """
    return conn.execute("DELETE FROM brief_items WHERE brief_id = ?", (brief_id,))


def count_assertions_by_run_id(conn: db.Conn, run_id: str) -> int:
    row = conn.execute(
        "SELECT count(*) AS n FROM assertions WHERE run_id = ?", (run_id,)
    ).fetchone()
    return int(row["n"])


def delete_assertions_by_run_id(conn: db.Conn, p0):
    return conn.execute("DELETE FROM assertions WHERE run_id = ?", (p0,))


def delete_assertions_by_run_id_2(conn: db.Conn, p0):
    return conn.execute("DELETE FROM assertions WHERE run_id = ?", (p0,))


def select_aliases_by_entity_id(conn: db.Conn, p0):
    return conn.execute("SELECT id, surface, added_by FROM aliases WHERE entity_id = ?", (p0,))


def delete_alias(conn: db.Conn, p0):
    return conn.execute("DELETE FROM aliases WHERE id = ?", (p0,))


def select_entities(conn: db.Conn):
    # `name` rides along so `_load_edges` can write an evidence quote naming the
    # two entities. An id triple is what the reader was shown before, and the
    # provenance panel serves that string verbatim.
    return conn.execute("SELECT id, type, name FROM entities")


def select_entities_by_id(conn: db.Conn, p0):
    """What a re-declaration must not overwrite.

    `status` is here for the same reason as the provenance columns beside it:
    the loader writes every column it knows, and a `merged` row is a tombstone
    pointing at a survivor, not a statement the document is entitled to revise.
    """
    return conn.execute(
        "SELECT first_seen, created_at, props, status FROM entities WHERE id = ?", (p0,)
    )


def select_entities_2(conn: db.Conn):
    return conn.execute("SELECT id FROM entities WHERE status='active'")


def select_entities_by_id_2(conn: db.Conn, p0):
    return conn.execute("SELECT 1 FROM entities WHERE id = ?", (p0,))


def select_aliases_by_entity_id_surface(conn: db.Conn, p0, p1):
    return conn.execute("SELECT id FROM aliases WHERE entity_id = ? AND surface = ?", (p0, p1))
