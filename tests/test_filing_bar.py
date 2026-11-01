"""What is worth asking a curator about, and when.

The filing net was widened on the argument that asking costs a curator one
keystroke. Measured on the live corpus that had become 455 open rows, of which
383 named an entity seen fewer than twice and 23 had two active sides -- and half
of every human keep-apart on a containment pair was spent on something that
drained on its own afterwards.
"""

from __future__ import annotations

from helpers import NOW, entity
from venona.db import core as db
from venona.db.repositories import curate as curate_repo
from venona.db.repositories import pipeline_curate as repo
from venona.pipeline.curate import _classify_open, _file_entity_duplicates
from venona.services.curate import list_conflicts, merge_entities, open_conflict_counts

LEX = {"corporate_form": {"technologies"}, "generic_tails": {"system"}}


def _seen(conn: db.Conn, entity_id: str, name: str, *, status: str, mentions: int) -> None:
    entity(conn, entity_id, name, status)
    conn.execute("UPDATE entities SET mention_count = ? WHERE id = ?", (mentions, entity_id))


def _file(conn: db.Conn, *, min_mentions: int) -> None:
    """The seeded template carries its own corpus, so every assertion here is
    about THIS pair rather than about a total."""
    _file_entity_duplicates(conn, now=NOW, min_mentions=min_mentions, **LEX)
    conn.commit()


def _asked_about(conn: db.Conn, a: str, b: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM alias_conflicts
         WHERE (existing_entity_id = ? AND proposed_entity_id = ?)
            OR (existing_entity_id = ? AND proposed_entity_id = ?)
        """,
        (a, b, b, a),
    ).fetchone()
    return row is not None


def _in_queue(conn: db.Conn, a: str, b: str, *, include_resolved: bool = False) -> bool:
    rows = curate_repo.select_conflicts(conn, kind=None, include_resolved=include_resolved)
    return any({r["existing_entity_id"], r["proposed_entity_id"]} == {a, b} for r in rows)


# ── the bar ──────────────────────────────────────────────────────────


def test_a_surface_seen_once_is_not_asked_about(conn: db.Conn):
    """It drains on its own; the question was never worth putting."""
    _seen(conn, "company:a", "Anduril", status="active", mentions=40)
    _seen(conn, "company:b", "Anduril Technologies", status="provisional", mentions=1)

    _file(conn, min_mentions=2)
    assert not _asked_about(conn, "company:a", "company:b")


def test_a_second_sighting_files_it(conn: db.Conn):
    """DEFERRED, never dropped: filing is asked of the whole corpus every run."""
    _seen(conn, "company:a", "Anduril", status="active", mentions=40)
    _seen(conn, "company:b", "Anduril Technologies", status="provisional", mentions=1)
    _file(conn, min_mentions=2)
    assert not _asked_about(conn, "company:a", "company:b")

    conn.execute("UPDATE entities SET mention_count = 2 WHERE id = 'company:b'")
    conn.commit()

    _file(conn, min_mentions=2)
    assert _asked_about(conn, "company:a", "company:b")


def test_a_corroborated_entity_is_never_held_back(conn: db.Conn):
    """The bar applies to `provisional` alone -- an active entity with one
    mention is one a person or a second source already stood behind."""
    _seen(conn, "company:a", "Anduril", status="active", mentions=1)
    _seen(conn, "company:b", "Anduril Technologies", status="active", mentions=1)

    _file(conn, min_mentions=2)
    assert _asked_about(conn, "company:a", "company:b")


def test_a_bar_of_zero_keeps_the_behaviour_it_had(conn: db.Conn):
    _seen(conn, "company:a", "Anduril", status="active", mentions=40)
    _seen(conn, "company:b", "Anduril Technologies", status="provisional", mentions=1)

    _file(conn, min_mentions=0)
    assert _asked_about(conn, "company:a", "company:b")


# ── the drain ────────────────────────────────────────────────────────


def test_a_drained_entity_is_not_asked_about(conn: db.Conn):
    """ADR 0054: dormant is uncorroborated AND silent for a whole window, and the
    projection already leaves it out. A question about something that is not in
    the graph is not a question."""
    _seen(conn, "company:a", "Anduril", status="active", mentions=40)
    _seen(conn, "company:b", "Anduril Technologies", status="dormant", mentions=5)

    _file(conn, min_mentions=0)
    assert not _asked_about(conn, "company:a", "company:b")


def test_a_conflict_whose_side_drained_leaves_the_queue(conn: db.Conn):
    _seen(conn, "company:a", "Anduril", status="active", mentions=40)
    _seen(conn, "company:b", "Anduril Technologies", status="active", mentions=5)
    _file(conn, min_mentions=0)
    _classify_open(conn, **LEX)
    conn.commit()
    assert _in_queue(conn, "company:a", "company:b")

    conn.execute("UPDATE entities SET status = 'dormant' WHERE id = 'company:b'")
    conn.commit()

    assert not _in_queue(conn, "company:a", "company:b")


def test_a_revival_puts_the_question_back(conn: db.Conn):
    """Hidden rather than resolved, because nothing was decided."""
    _seen(conn, "company:a", "Anduril", status="active", mentions=40)
    _seen(conn, "company:b", "Anduril Technologies", status="active", mentions=5)
    _file(conn, min_mentions=0)
    _classify_open(conn, **LEX)
    conn.execute("UPDATE entities SET status = 'dormant' WHERE id = 'company:b'")
    conn.commit()
    assert not _in_queue(conn, "company:a", "company:b")

    conn.execute("UPDATE entities SET status = 'active' WHERE id = 'company:b'")
    conn.commit()

    assert _in_queue(conn, "company:a", "company:b")


def test_the_badge_counts_only_what_the_queue_shows(conn: db.Conn):
    """737 on the badge against 344 rows the queue could show, measured on
    production 2026-10-25: the count read the table, the list applied the drain."""
    _seen(conn, "company:a", "Anduril", status="active", mentions=40)
    _seen(conn, "company:b", "Anduril Technologies", status="active", mentions=5)
    _file(conn, min_mentions=0)
    _classify_open(conn, **LEX)
    conn.commit()
    assert _in_queue(conn, "company:a", "company:b")
    filed = sum(open_conflict_counts(conn).values())

    conn.execute("UPDATE entities SET status = 'dormant' WHERE id = 'company:b'")
    conn.commit()

    assert sum(open_conflict_counts(conn).values()) == filed - 1
    assert sum(open_conflict_counts(conn).values()) == len(list_conflicts(conn))


def test_a_side_merged_into_a_drained_survivor_leaves_the_queue(conn: db.Conn):
    """The stored side is `merged`, not dormant, so only the resolved pair shows it."""
    _seen(conn, "company:a", "Anduril", status="active", mentions=40)
    _seen(conn, "company:b", "Anduril Technologies", status="active", mentions=5)
    _seen(conn, "company:c", "Anduril Industries", status="active", mentions=3)
    _file(conn, min_mentions=0)
    _classify_open(conn, **LEX)
    conn.commit()
    merge_entities(conn, from_id="company:b", to_id="company:c", merge_id="m1", reason="t", now=NOW)
    conn.commit()
    pairs = [{r["existing_entity_id"], r["proposed_entity_id"]} for r in list_conflicts(conn)]
    assert {"company:a", "company:c"} in pairs
    conn.execute("UPDATE entities SET status = 'dormant' WHERE id = 'company:c'")
    conn.commit()

    pairs = [{r["existing_entity_id"], r["proposed_entity_id"]} for r in list_conflicts(conn)]
    assert {"company:a", "company:c"} not in pairs


def test_a_page_whose_every_row_has_drained_reads_as_empty(conn: db.Conn):
    """Production answered 500 here: the one row the SQL kept was hidden once
    resolved, and the name lookup then ran with no ids."""
    _seen(conn, "company:a", "Anduril", status="active", mentions=40)
    _seen(conn, "company:b", "Anduril Technologies", status="active", mentions=5)
    _seen(conn, "company:c", "Anduril Industries", status="active", mentions=3)
    _file(conn, min_mentions=0)
    _classify_open(conn, **LEX)
    conn.commit()
    merge_entities(conn, from_id="company:b", to_id="company:c", merge_id="m2", reason="t", now=NOW)
    conn.execute("UPDATE entities SET status = 'dormant' WHERE id = 'company:c'")
    conn.commit()
    kind = next(
        r["kind"]
        for r in curate_repo.select_conflicts(conn, kind=None, include_resolved=False)
        if {r["existing_entity_id"], r["proposed_entity_id"]} == {"company:a", "company:b"}
    )
    others = [
        r["id"]
        for r in curate_repo.select_conflicts(conn, kind=kind, include_resolved=False)
        if {r["existing_entity_id"], r["proposed_entity_id"]} != {"company:a", "company:b"}
    ]
    for other in others:
        conn.execute("UPDATE alias_conflicts SET resolved = 1 WHERE id = ?", (other,))
    conn.commit()

    assert list_conflicts(conn, kind=kind) == []


def test_a_resolved_conflict_is_still_history(conn: db.Conn):
    """The filter is on the QUEUE, not on the record: a decision somebody made
    about an entity that later drained is still a decision they made."""
    _seen(conn, "company:a", "Anduril", status="active", mentions=40)
    _seen(conn, "company:b", "Anduril Technologies", status="active", mentions=5)
    _file(conn, min_mentions=0)
    _classify_open(conn, **LEX)
    conn.execute("UPDATE alias_conflicts SET resolved = 1, resolution = 'kept_apart'")
    conn.execute("UPDATE entities SET status = 'dormant' WHERE id = 'company:b'")
    conn.commit()

    assert _in_queue(conn, "company:a", "company:b", include_resolved=True)


# ── the population the wide net was built for ────────────────────────


def test_two_declared_entities_are_still_caught(conn: db.Conn):
    """The reason the net is wide: `services/seed.py` writes declared aliases
    straight into the table without going through the resolver, so two entities
    can hold one name with nothing filed about them. Three of the four splits
    found in the live corpus were that shape, and all of them were ACTIVE."""
    _seen(conn, "company:a", "Palantir", status="active", mentions=12)
    _seen(conn, "company:b", "Palantir Technologies", status="active", mentions=9)

    _file(conn, min_mentions=2)
    assert _asked_about(conn, "company:a", "company:b")
    ids = {r["id"] for r in repo.select_entities_6(conn, min_mentions=2)}
    assert {"company:a", "company:b"} <= ids
