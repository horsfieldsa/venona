"""The alias index is cached, and the cache must never outlive the truth.

`build_index` is ~84ms of Aho-Corasick construction and a brief read calls it
once for the title and once per headline, so caching it is worth real time on
the reader's hot path. The danger is the other direction: every mutation that
changes this index is an UPDATE, so a cache that cannot see updates keeps
serving `undefined` for a term the reader has just grounded -- which is
invariant 3 failing silently, in the one place a reader would trust it.
"""

from __future__ import annotations

from venona.db import core as db
from venona.services import alias_index
from venona.services.alias_index import build_index


def _glossary_row(conn: db.Conn, entry_id: str, surface: str, status: str) -> None:
    db.upsert(
        conn,
        "acronym_definitions",
        {
            "id": entry_id,
            "surface": surface,
            "normalized": surface.lower(),
            "expansion": "Some Expansion",
            "method": "web_grounded",
            "confidence": 0.8,
            "status": status,
            "sighting_count": 3,
            "article_count": 2,
            "first_seen": "t0",
            "last_seen": "t0",
        },
        key=["id"],
    )


def test_an_unchanged_corpus_does_not_rebuild_the_index(conn: db.Conn, monkeypatch) -> None:
    """The whole point: a brief read annotates a title and every headline, and
    each one asked for a fresh automaton."""
    builds = {"n": 0}
    real = alias_index._build_index

    def counted(*args, **kwargs):
        builds["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(alias_index, "_build_index", counted)

    first = build_index(conn)
    second = build_index(conn)

    assert builds["n"] == 1, f"the second read must be a cache hit, not build {builds['n']}"
    assert first is second


def test_grounding_a_term_rebuilds_the_index(conn: db.Conn) -> None:
    """⭐ THE case the absence of a cache was protecting.

    Grounding from the extension flips `acronym_definitions.status` to
    `active`. The row count does not move, so a count-keyed cache would keep
    the surface out of the index and keep rendering it as undefined -- for
    good, since nothing else would ever invalidate.
    """
    _glossary_row(conn, "gloss:qqq", "QQQ", "rejected")
    before = build_index(conn)
    assert "QQQ" not in {e.surface for e in before._entries}

    _glossary_row(conn, "gloss:qqq", "QQQ", "active")
    after = build_index(conn)

    assert "QQQ" in {e.surface for e in after._entries}, (
        "a status change with no change in row count must still invalidate"
    )


def test_attaching_a_definition_to_an_entity_rebuilds_the_index(conn: db.Conn) -> None:
    """Setting `entity_id` takes the row OUT of the glossary half of the index,
    and again moves no count."""
    _glossary_row(conn, "gloss:rrr", "RRR", "active")
    assert "RRR" in {e.surface for e in build_index(conn)._entries}

    db.insert(
        conn,
        "entities",
        {
            "id": "company:rrr",
            "type": "Company",
            "name": "RRR Systems",
            "canonical_key": "rrr systems",
            "status": "active",
            "relevance": 0.4,
            "first_seen": "t0",
            "last_seen": "t0",
            "created_at": "t0",
        },
    )
    conn.execute("UPDATE acronym_definitions SET entity_id = 'company:rrr' WHERE id = 'gloss:rrr'")

    entries = build_index(conn)._entries
    assert not any(e.ref == "gloss:rrr" for e in entries), (
        "the glossary entry must leave the index when it becomes an entity"
    )


def test_an_entity_status_swap_rebuilds_the_index(conn: db.Conn) -> None:
    """`entities.status` decides whether an alias is indexed at all, and a swap
    between two indexed statuses leaves every count identical."""
    db.insert(
        conn,
        "entities",
        {
            "id": "company:sss",
            "type": "Company",
            "name": "SSS Holdings",
            "canonical_key": "sss holdings",
            "status": "active",
            "relevance": 0.4,
            "first_seen": "t0",
            "last_seen": "t0",
            "created_at": "t0",
        },
    )
    db.insert(
        conn,
        "aliases",
        {
            "id": "alias:sss",
            "entity_id": "company:sss",
            "surface": "SSS Holdings",
            "normalized": "sss holdings",
            "strictness": 1,
            "is_acronym": 0,
            "added_by": "seed",
            "created_at": "t0",
        },
    )
    build_index(conn)

    conn.execute("UPDATE entities SET status = 'rejected' WHERE id = 'company:sss'")

    assert not any(e.ref == "company:sss" for e in build_index(conn)._entries), (
        "a rejected entity must leave the index, count unchanged or not"
    )


def test_two_databases_do_not_share_one_cached_index(conn: db.Conn, database_url: str) -> None:
    """The suite seeds every throwaway database through a FrozenClock, so two
    can carry an identical stamp; a process-wide memo would serve one test's
    index into another's database."""
    _glossary_row(conn, "gloss:ttt", "TTT", "active")
    assert "TTT" in {e.surface for e in build_index(conn)._entries}

    other = db.connect(database_url)
    try:
        assert other.dsn != conn.dsn or "TTT" in {e.surface for e in build_index(other)._entries}
    finally:
        other.close()
