"""Every statement that define runs.

Serves `pipeline/define.py`. The module keeps its
decisions; this holds the reads and writes they are made of. See
`db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def update_entity_definitions_by_last_seen_entity_id(conn: db.Conn, p0, p1):
    return conn.execute("UPDATE entity_definitions SET last_seen = ? WHERE entity_id = ?", (p0, p1))


def select_entity_definitions_by_entity_id(conn: db.Conn, p0):
    return conn.execute(
        "SELECT decided_at, first_seen, method FROM entity_definitions WHERE entity_id = ?", (p0,)
    )


def select_entity_definitions_by_entity_id_2(conn: db.Conn, p0):
    return conn.execute(
        "SELECT decided_at, attempt_count, first_seen, last_attempt_at, last_attempt_run_id FROM "
        "entity_definitions WHERE entity_id = ?",
        (p0,),
    )


def select_briefs_by_run_id(conn: db.Conn, p0):
    return conn.execute("SELECT 1 FROM briefs WHERE run_id = ? LIMIT 1", (p0,))


def select_briefs_by_run_id_2(conn: db.Conn, p0, p1):
    """Undefined entities reachable by hovering the briefs of this run's day.

    THE SCOPE IS BOTH KINDS OF BRIEF. `briefs` is the shared one; every reader
    also gets a `personal_briefs` row built from its own retrieval, and on
    2026-09-14 the two agreed on 5 clusters out of 19 and 12. Scoped to the
    shared brief alone this stage never saw two thirds of what the reader was
    actually looking at: `plat:vectis` sat at `ord` 7 of the personal brief
    with a definition quotable from an article we held, and had no
    `entity_definitions` row at all -- not even an undefined one, because
    nothing ever offered it.

    Keyed on the DATE rather than on the run. A personal brief is written by
    its own run minutes after the daily one, so there is no run id the two
    share, and the day is the unit a reader opens anyway.
    """
    return conn.execute(
        """
        WITH scope AS (
          SELECT bi.cluster_id
            FROM briefs b
            JOIN brief_items bi ON bi.brief_id = b.id
           WHERE b.run_id = ?
           UNION
          SELECT pbi.cluster_id
            FROM briefs b
            JOIN personal_briefs pb ON pb.date = b.date
            JOIN personal_brief_items pbi ON pbi.brief_id = pb.id
           WHERE b.run_id = ?
        )
        SELECT e.id, e.name, e.type, MAX(COALESCE(a.salience, 0)) AS salience
          FROM scope
          JOIN documents d ON d.cluster_id = scope.cluster_id
          JOIN assertions a ON a.article_id = d.id
          JOIN entities e ON e.id = a.entity_id_raw
     LEFT JOIN entity_definitions ed ON ed.entity_id = e.id
         WHERE a.kind = 'entity'
           AND a.retracted_at IS NULL
           AND COALESCE(a.salience, 0) >= ?
           AND (ed.entity_id IS NULL OR ed.status = 'undefined')
      GROUP BY e.id, e.name, e.type
      ORDER BY salience DESC, e.id
        """,
        (p0, p0, p1),
    )


def select_briefs(conn: db.Conn):
    return conn.execute(
        "SELECT run_id FROM briefs WHERE run_id IS NOT NULL ORDER BY generated_at DESC LIMIT 1"
    )


def evidence_for_closure(conn: db.Conn, members: list[str]) -> list[db.Row]:
    """Sentences already held about an entity, across its merge closure.

    NULLS LAST is explicit: SQLite sorted NULLs first on ASC and so last on DESC,
    Postgres does the opposite, and an undated document must not count as the most
    recent evidence.
    """
    marks = ",".join("?" for _ in members)
    return list(
        conn.execute(
            f"""
            SELECT a.evidence_quote, a.definition_quote, a.article_id, a.salience,
                   d.title, d.url, d.published_at, d.publisher
              FROM assertions a
              JOIN documents d ON d.id = a.article_id
             WHERE a.kind = 'entity'
               AND a.retracted_at IS NULL
               AND a.entity_id_raw IN ({marks})
             ORDER BY d.published_at DESC NULLS LAST
             LIMIT 12
            """,  # noqa: S608 - placeholders only, one per closure member
            tuple(members),
        ).fetchall()
    )


#: Methods a free tier-1 sentence may REPLACE.
#:
#: The candidate queries below asked only for entities with no definition or an
#: undefined one, on the reasoning `targets()` still carries: re-grounding a
#: settled row costs money and can only make it worse. That was true while tier
#: 3 was the only re-grounding. A sentence quoted verbatim out of an article we
#: already hold costs nothing and outranks every one of these, so the premise
#: does not reach it, and holding to it froze 1,687 `in_corpus` syntheses and 20
#: web paraphrases where no better definition could ever land -- which is most
#: of the shallowness the re-read exists to fix. ADR 0244.
#:
#: `manual` and `in_article` are absent, and that is the whole safety of it:
#: nothing here can displace a human verdict or another verbatim sentence.
#: `store_entity_definition` refuses every downgrade independently, and
#: `decided_at` is checked in the queries as well as there.
UPGRADEABLE_METHODS: tuple[str, ...] = ("in_corpus", "web_grounded")


def backfill_documents(conn: db.Conn, min_salience: float, limit: int | None):
    """Documents worth re-reading, most unexplained names first.

    Ranked by how many eligible entities a document backs, because the call
    costs the same whether it answers for one name or twenty and the budget is
    the scarce thing. `text_chars` filters out stubs that cannot carry a
    description of anything.

    `definition_quote IS NULL` on the assertion is what makes the pass
    resumable: a document re-read once has its answers stored on its rows, so a
    second run skips it without keeping any state of its own.
    """
    marks = ",".join("?" for _ in UPGRADEABLE_METHODS)
    return conn.execute(
        f"""
        SELECT d.id, count(DISTINCT a.entity_id_raw) AS pending
          FROM documents d
          JOIN assertions a ON a.article_id = d.id
          JOIN entities e ON e.id = a.entity_id_raw
     LEFT JOIN entity_definitions ed ON ed.entity_id = e.id
         WHERE a.kind = 'entity'
           AND a.retracted_at IS NULL
           AND a.definition_quote IS NULL
           AND COALESCE(a.salience, 0) >= ?
           AND d.text_chars > 0
           AND ed.decided_at IS NULL
           AND (ed.entity_id IS NULL
                OR ed.status = 'undefined'
                OR ed.method IN ({marks}))
      GROUP BY d.id
      ORDER BY pending DESC, d.id
         LIMIT ?
        """,  # noqa: S608 - placeholders only, one per upgradeable method
        (min_salience, *UPGRADEABLE_METHODS, limit if limit is not None else 1000000),
    )


def backfill_document_text(conn: db.Conn, article_id: str):
    return conn.execute("SELECT id, text_nfc, url FROM documents WHERE id = ?", (article_id,))


def backfill_entities_for_document(conn: db.Conn, article_id: str, min_salience: float):
    """The names this document asserts that a verbatim sentence could improve.

    Same eligibility as `backfill_documents` -- unexplained, or explained only
    by a synthesis or a web paraphrase. See `UPGRADEABLE_METHODS`.
    """
    marks = ",".join("?" for _ in UPGRADEABLE_METHODS)
    return conn.execute(
        f"""
        SELECT DISTINCT ON (e.id) e.id, e.name, a.id AS assertion_id,
               COALESCE(a.salience, 0) AS salience
          FROM assertions a
          JOIN entities e ON e.id = a.entity_id_raw
     LEFT JOIN entity_definitions ed ON ed.entity_id = e.id
         WHERE a.article_id = ?
           AND a.kind = 'entity'
           AND a.retracted_at IS NULL
           AND a.definition_quote IS NULL
           AND COALESCE(a.salience, 0) >= ?
           AND ed.decided_at IS NULL
           AND (ed.entity_id IS NULL
                OR ed.status = 'undefined'
                OR ed.method IN ({marks}))
      ORDER BY e.id, salience DESC
        """,  # noqa: S608 - placeholders only, one per upgradeable method
        (article_id, min_salience, *UPGRADEABLE_METHODS),
    )


def set_definition_quote(conn: db.Conn, sentence: str | None, assertion_id: str):
    """Stamp a re-read sentence onto the assertion it was found for.

    THE ONE EXCEPTION to "never UPDATE an assertions row", and it is narrow
    enough to stay one: `definition_quote` is not part of the assertion. The
    claim -- surface, entity, evidence quote, confidence, method -- is untouched
    and stays append-only, so invariant 1 and the unmerge path are unaffected.
    What moves is a cache of where in this article the description lives, which
    a run written before the column existed could not have filled.

    An EMPTY STRING, not null, for a name the re-read found no description of,
    and it is what makes the pass resumable. Null means nobody has looked; `''`
    means this document has been read for this entity and does not describe it.
    Writing null there would re-offer the same document to the next run
    forever, paying the same call to learn the same nothing. Every reader of
    the column tests it for truthiness, so the two absences stay one case
    everywhere but here.
    """
    return conn.execute(
        "UPDATE assertions SET definition_quote = ? WHERE id = ?", (sentence, assertion_id)
    )


def definitions_run_costs(conn: db.Conn, *, limit: int, harness: str) -> list[db.Row]:
    """Recent `definitions` sweeps that spent, for the quote in front of a big one.

    Per ENTITY, not per run, which is why `grounded` comes back with the cost:
    a sweep of ten and a sweep of two hundred are the same work repeated, and
    dividing is the only way one measures the other. The counts live on the
    stage row because that is where `LaunchOutcome.data` lands.

    Scoped to one service and admitting NULL only for `claude_code`, for the
    reason `glossary.lookup_run_costs` gives in full: two services do not cost
    the same, and `runs.harness` was never backfilled, so on this instance the
    whole history is NULL and excluding it would drop the quote back to a
    constant on exactly the machine with the most evidence.
    """
    where = "(r.harness = ? OR r.harness IS NULL)" if harness == "claude_code" else "r.harness = ?"
    return list(
        conn.execute(
            "SELECT r.cost_usd, r.started_at, r.finished_at, s.counts "
            "FROM runs r JOIN run_stages s ON s.run_id = r.id "
            f"WHERE r.kind = 'definitions' AND r.cost_usd > 0 AND {where} "  # noqa: S608
            "ORDER BY r.started_at DESC LIMIT ?",
            (harness, limit),
        ).fetchall()
    )


def free_pass_candidates(conn: db.Conn, min_salience: float) -> list[db.Row]:
    """Every name a sentence we already hold could explain or explain better.

    ENTITY-SCOPED, not document-scoped, and that is the whole point of it being
    its own query. The re-read walks documents because a model call reads one
    document at a time; the free pass reads `evidence_for`, which gathers an
    entity's sentences from wherever they are. Nesting it inside the document
    loop bounded it by document eligibility as well as its own -- so a name
    whose only defining sentence sat in an article that had already been
    re-read was skipped, and the pass that costs nothing was the one with the
    narrower reach.

    Ordered by prominence so a run somebody stops halfway has done the names
    most likely to be hovered. See `UPGRADEABLE_METHODS` for what may be
    replaced and what may not.
    """
    marks = ",".join("?" for _ in UPGRADEABLE_METHODS)
    return list(
        conn.execute(
            f"""
            SELECT e.id, e.name, MAX(COALESCE(a.salience, 0)) AS salience
              FROM assertions a
              JOIN entities e ON e.id = a.entity_id_raw
         LEFT JOIN entity_definitions ed ON ed.entity_id = e.id
             WHERE a.kind = 'entity'
               AND a.retracted_at IS NULL
               AND COALESCE(a.salience, 0) >= ?
               -- NOT `= 'active'`. A provisional entity is one seen too few
               -- times to be promoted yet, and it is rendered and hoverable
               -- on today's brief exactly like any other: 4,654 of them were
               -- eligible here and 1,157 were on a brief, against 854 active
               -- ones, so an `active` filter would have excluded most of the
               -- names a reader can actually open. `merged` is excluded
               -- because its card resolves to the survivor, which
               -- `ground_in_corpus_quote` reaches through the merge closure
               -- anyway, so a row written against the loser is one nothing
               -- reads.
               AND e.status <> 'merged'
               AND ed.decided_at IS NULL
               AND (ed.entity_id IS NULL
                    OR ed.status = 'undefined'
                    OR ed.method IN ({marks}))
          GROUP BY e.id, e.name
          ORDER BY salience DESC, e.id
            """,  # noqa: S608 - placeholders only, one per upgradeable method
            (min_salience, *UPGRADEABLE_METHODS),
        ).fetchall()
    )
