"""Every statement that curate runs.

Serves `pipeline/curate.py`. The module keeps its
decisions; this holds the reads and writes they are made of. See
`db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def select_alias_conflicts(conn: db.Conn):
    return conn.execute("SELECT * FROM alias_conflicts WHERE resolved = 0 AND kind = 'duplicate'")


def update_alias_conflicts_by_kind_id(conn: db.Conn, p0, p1):
    return conn.execute("UPDATE alias_conflicts SET kind = ? WHERE id = ?", (p0, p1))


def select_alias_conflicts_2(conn: db.Conn, p0):
    """Open rows nobody has read yet, for the adjudicator.

    `cross_type` and `type_conflict` are in the list because they were the only
    kinds arriving with no reading at all: the UI refuses Merge on both, so the
    question a curator is left with is whether one belongs to the other or the
    two are unrelated, and that is exactly what the adjudicator answers. The
    ontology's boundary is untouched by this -- nothing auto-applies across a
    type, and a suggestion is still only a suggestion.

    `anchor` stays out: those rows carry a proposal already.
    """
    return conn.execute(
        """
        SELECT c.*, e.name AS a_name, e.type AS a_type, p.name AS b_name, p.type AS b_type
          FROM alias_conflicts c
          JOIN entities e ON e.id = c.existing_entity_id
          JOIN entities p ON p.id = c.proposed_entity_id
         WHERE c.resolved = 0 AND c.suggestion IS NULL
           AND c.kind IN ('containment', 'collision', 'duplicate',
                          'cross_type', 'type_conflict')
      ORDER BY c.created_at
         LIMIT ?
        
        """,
        (p0,),
    )


def update_alias_conflicts_by_suggestion_suggestion_note(conn: db.Conn, p0, p1, p2, p3, p4, p5, p6):
    return conn.execute(
        "UPDATE alias_conflicts SET suggestion = ?, suggestion_note = ?, suggestion_child = ?, "
        "suggestion_predicate = ?, suggestion_confidence = ?, suggested_at = ? WHERE id = ?",
        (p0, p1, p2, p3, p4, p5, p6),
    )


def select_type_conflicts(conn: db.Conn, declared_run_ids: tuple[str, ...]):
    """Open type conflicts, saying which side a person DECLARED.

    `declared` is the seed and anchor run ids: an entity carrying an assertion
    from one of those was written into `fixtures/seed/entities.yaml` or
    `profile/anchors.yaml` by hand, and its type is therefore a statement rather
    than a reading. That is the whole basis on which one of these resolves
    without asking.
    """
    marks = ",".join("?" for _ in declared_run_ids)
    return list(
        conn.execute(
            f"""
        SELECT c.id, c.existing_entity_id, c.proposed_entity_id,
               e.name AS a_name, e.type AS a_type, p.name AS b_name, p.type AS b_type,
               EXISTS (SELECT 1 FROM assertions x WHERE x.entity_id_raw = e.id
                        AND x.run_id IN ({marks})) AS a_declared,
               EXISTS (SELECT 1 FROM assertions y WHERE y.entity_id_raw = p.id
                        AND y.run_id IN ({marks})) AS b_declared
          FROM alias_conflicts c
          JOIN entities e ON e.id = c.existing_entity_id
          JOIN entities p ON p.id = c.proposed_entity_id
         WHERE c.resolved = 0 AND c.kind = 'type_conflict'
      ORDER BY c.created_at
            """,  # noqa: S608 - marks only
            (*declared_run_ids, *declared_run_ids),
        ).fetchall()
    )


def select_declared_entities(conn: db.Conn, ids: tuple[str, ...], run_ids: tuple[str, ...]):
    """Which of these entities a person wrote into the seed or the anchors."""
    id_marks = ",".join("?" for _ in ids)
    run_marks = ",".join("?" for _ in run_ids)
    return conn.execute(
        f"SELECT DISTINCT entity_id_raw AS entity_id FROM assertions "  # noqa: S608 - marks only
        f"WHERE entity_id_raw IN ({id_marks}) AND run_id IN ({run_marks})",
        (*ids, *run_ids),
    ).fetchall()


def select_merge_verdicts(conn: db.Conn):
    """Unsettled pairs the model called a certain merge.

    Narrow on purpose. `collision` is excluded because both names generating one
    acronym is the case a wrong merge is unrecoverable in; `cross_type` because
    the ontology drew that line deliberately; and anything short of `certain`
    because the whole point of asking for a confidence is to let the model keep
    a pair in the queue.
    """
    return conn.execute(
        """
        SELECT c.*, e.name AS a_name, e.type AS a_type, p.name AS b_name, p.type AS b_type
          FROM alias_conflicts c
          JOIN entities e ON e.id = c.existing_entity_id
          JOIN entities p ON p.id = c.proposed_entity_id
         WHERE c.resolved = 0 AND c.suggestion = 'merge'
           AND c.suggestion_confidence = 'certain'
           AND c.kind IN ('containment', 'duplicate')
      ORDER BY c.created_at
        """
    ).fetchall()


def select_distinct_verdicts(conn: db.Conn):
    """Unsettled pairs the model called a certain DISTINCTION.

    The mirror of `select_merge_verdicts`, and the kinds are wider on purpose.
    That one excludes `collision` and `cross_type` because a wrong merge is
    unrecoverable there; this one INCLUDES them, because those are the kinds
    most likely to be a genuine distinction and keeping two things apart is the
    direction that costs nothing when it is wrong -- the pair stays two
    entities, which is what it already was.

    `anchor` and `resolver_error` stay out. Neither asks whether two things are
    one: the first proposes a placement and the second reports that the
    resolver fell over, and settling either as "kept apart" would answer a
    question nobody asked.

    `likely` COUNTS HERE AND NOT IN THE MERGE QUERY, and the asymmetry is the
    reason rather than an inconsistency. A confidence short of certain is the
    model keeping a pair in the queue, which is the right instinct when the act
    is irreversible and the wrong one when it is not: keeping two entities apart
    leaves them as the two entities they already were. Measured on the live
    queue, `certain` alone left thirty distinctions waiting for somebody to
    agree with them.
    """
    return conn.execute(
        """
        SELECT c.*, e.name AS a_name, e.type AS a_type, p.name AS b_name, p.type AS b_type
          FROM alias_conflicts c
          JOIN entities e ON e.id = c.existing_entity_id
          JOIN entities p ON p.id = c.proposed_entity_id
         WHERE c.resolved = 0 AND c.suggestion = 'distinct'
           AND c.suggestion_confidence IN ('certain', 'likely')
           AND c.kind IN ('containment', 'duplicate', 'collision', 'cross_type', 'type_conflict')
      ORDER BY c.created_at
        """
    ).fetchall()


def select_relation_verdicts(conn: db.Conn):
    """Unsettled pairs the model called a certain `part_of`, with a predicate.

    `suggestion_predicate IS NOT NULL` is half the gate and it is the ontology's
    own answer: `_relation_for` writes NULL when no declared predicate is legal
    between the two types, and a row like that cannot be applied by anybody --
    the API refuses it with the very message that function exists to prevent.
    Offering it to a curator is offering a button that 422s.

    `certain` only, and `containment` only. The direction matters here in a way
    it does not for a merge: this writes an edge that says one thing belongs to
    another, and a containment pair is the one shape where the names corroborate
    which way round that goes.
    """
    return conn.execute(
        """
        SELECT c.*, e.name AS a_name, e.type AS a_type, p.name AS b_name, p.type AS b_type
          FROM alias_conflicts c
          JOIN entities e ON e.id = c.existing_entity_id
          JOIN entities p ON p.id = c.proposed_entity_id
         WHERE c.resolved = 0 AND c.suggestion = 'part_of'
           AND c.suggestion_confidence = 'certain'
           AND c.suggestion_predicate IS NOT NULL
           AND c.suggestion_child IS NOT NULL
           AND c.kind = 'containment'
      ORDER BY c.created_at
        """
    ).fetchall()


def select_entities(conn: db.Conn, p0, p1):
    return conn.execute("SELECT id, name FROM entities WHERE id IN (?, ?)", (p0, p1))


def select_alias_conflicts_3(conn: db.Conn):
    return conn.execute("SELECT * FROM alias_conflicts WHERE resolved = 0")


def select_entities_2(conn: db.Conn):
    return conn.execute("SELECT id, status FROM entities WHERE status != 'merged'")


def select_entities_3(conn: db.Conn):
    return conn.execute("SELECT id, status FROM entities WHERE status != 'merged'")


def select_entities_6(conn: db.Conn, *, min_mentions: int = 0):
    """Every entity a duplicate could still be filed about.

    `merged` is excluded because it is a tombstone the mapping already resolves,
    and `rejected` because a curator has already said it is not a thing.

    `dormant` is excluded too: ADR 0054's drain means uncorroborated AND silent
    for a whole window, the projection already leaves it out, and a question
    about something that is not in the graph is not a question anybody needs
    answered. A revival puts it back in scope, because this is asked every run.

    `min_mentions` is the bar a PROVISIONAL entity has to clear. Measured on the
    live queue: of 455 open conflicts, 383 name a proposed entity seen fewer than
    twice and only 23 have two active sides. A surface that appeared once and
    never came back drains on its own, and half of every human keep-apart on a
    containment pair was spent on one of those. A declared or corroborated entity
    is never held back by this -- the bar applies to `provisional` alone.
    """
    return conn.execute(
        """
        SELECT id, type, name FROM entities
         WHERE status NOT IN ('merged', 'rejected', 'dormant')
           AND (status <> 'provisional' OR mention_count >= ?)
        """,
        (int(min_mentions),),
    ).fetchall()


def select_unretired_entity_names(conn: db.Conn):
    """Every entity that could still be what a name means, `dormant` INCLUDED.

    The rival set for a guard against merging into the wrong one of two
    namesakes, which is the opposite question to `select_entities_6`'s: a
    dormant entity is not worth filing a question about, but it is still a
    reading of the name.
    """
    return conn.execute(
        "SELECT id, type, name FROM entities WHERE status NOT IN ('merged', 'rejected')"
    ).fetchall()


def select_alias_conflict_pairs(conn: db.Conn):
    """Which entity pairs already have a conflict row, and how it was settled.

    The pair, not the id: the resolver, the anchor loader and the duplicate
    detector each file under their own prefix, and a pair that already has a row
    under one of them must not collect a second under another. Two rows about
    one pair can carry OPPOSITE merge directions, and which one wins is then
    decided by iteration order.

    `resolution` rides along because one verdict can go stale. `kept_apart` is a
    standing human answer, but `merged` is a claim that two ids ARE one entity,
    and the caller can check whether that is still true.
    """
    return conn.execute(
        "SELECT existing_entity_id, proposed_entity_id, resolution FROM alias_conflicts"
    ).fetchall()


def select_declared_alias_owners(conn: db.Conn):
    """Who DECLARED each alias surface, and at what type.

    `seed` and `manual` only. Those two are a person stating that a surface
    names an entity -- in `fixtures/seed/entities.yaml`, `profile/anchors.yaml`
    or through the glossary's attach action. The rest are derived: `acronym_gen`
    manufactures initialisms and `extraction` records what a model read, and
    neither is evidence about identity strong enough to file a duplicate on.

    Joined to `entities` so a tombstone's aliases do not claim a surface: a
    merged loser keeps its aliases on purpose, and counting them here would file
    a duplicate against the survivor of a merge that already happened.
    """
    return conn.execute(
        "SELECT a.normalized, a.entity_id, e.type "
        "FROM aliases a JOIN entities e ON e.id = a.entity_id "
        "WHERE a.added_by IN ('seed', 'manual') "
        "AND e.status NOT IN ('merged', 'rejected')"
    ).fetchall()


def select_entities_4(conn: db.Conn, p0, p1):
    return conn.execute("SELECT id, name FROM entities WHERE id IN (?, ?)", (p0, p1))


def select_entities_5(conn: db.Conn, p0, p1):
    # `status` rides along because choosing a survivor weighs a corroborated
    # entity over a single-sighting provisional, and the callers that only want
    # the name and type are indifferent to an extra column.
    return conn.execute("SELECT id, name, type, status FROM entities WHERE id IN (?, ?)", (p0, p1))


def select_aliases(conn: db.Conn, p0, p1):
    return conn.execute(
        "SELECT entity_id, string_agg(surface, ' | ' ORDER BY surface) AS surfaces "
        "FROM aliases WHERE entity_id IN (?, ?) GROUP BY entity_id",
        (p0, p1),
    )


def select_assertions(conn: db.Conn, p0, p1):
    return conn.execute(
        "SELECT DISTINCT d.title FROM assertions a JOIN documents d ON d.id = a.article_id WHERE "
        "a.entity_id_raw IN (?, ?) LIMIT 3",
        (p0, p1),
    )


#: Marks an `ambiguous_surfaces` row this stage owns. The glossary writes rows
#: for its own reason, and releasing one of those on an entity's behalf would
#: silently overturn a curator's verdict.
ENTITY_CLAIM_REASON = "entities claim this surface"


def select_entity_claim_surfaces(conn: db.Conn):
    """The ambiguous surfaces filed for an entity collision, not a glossary one."""
    return conn.execute(
        "SELECT normalized FROM ambiguous_surfaces WHERE reason LIKE ?",
        (f"%{ENTITY_CLAIM_REASON}",),
    )


def insert_entity_claim_surface(conn: db.Conn, normalized: str, claimants: int, now: str) -> None:
    conn.execute(
        """
        INSERT INTO ambiguous_surfaces (normalized, reason, created_at)
        VALUES (?, ?, ?)
        ON CONFLICT (normalized) DO NOTHING
        """,
        (normalized, f"{claimants} {ENTITY_CLAIM_REASON}", now),
    )


def delete_entity_claim_surface(conn: db.Conn, normalized: str) -> None:
    conn.execute(
        "DELETE FROM ambiguous_surfaces WHERE normalized = ? AND reason LIKE ?",
        (normalized, f"%{ENTITY_CLAIM_REASON}"),
    )
