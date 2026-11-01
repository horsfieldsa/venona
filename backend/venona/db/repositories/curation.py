"""Every statement behind the curation queues.

Serves `api/routers/curation.py` (18) plus the two smaller routers'
reads. The routers keep the verdict logic -- what a resolution means, which
predicates are well-typed for a pair, when a rebuild is pending; this holds the
reads and writes those decisions are made of.

`CONFLICT_SELECT` is here because four endpoints return the same shape and
`_conflict()` depends on both joined names being present. A projection four
callers share is a fact about the table.
"""

from __future__ import annotations

from typing import Any

from venona.db import core as db

#: A conflict with both entity names resolved, plus who decided it. LEFT JOIN on
#: every side: a conflict can name an entity that was since merged away, and
#: `resolved_by` is NULL for every open row and for every verdict the CLI or the
#: pipeline reached -- an INNER join anywhere here would hide the row rather than
#: the missing name.
CONFLICT_SELECT = """
    SELECT c.*, e.name AS existing_name, p.name AS proposed_name,
           u.email AS resolved_by_email
      FROM alias_conflicts c
      LEFT JOIN entities e ON e.id = c.existing_entity_id
      LEFT JOIN entities p ON p.id = c.proposed_entity_id
      LEFT JOIN users u ON u.id = c.resolved_by
"""


def conflict(conn: db.Conn, conflict_id: str) -> db.Row | None:
    return conn.execute(CONFLICT_SELECT + " WHERE c.id = ?", (conflict_id,)).fetchone()


def conflict_row(conn: db.Conn, conflict_id: str) -> db.Row | None:
    """The bare row, without the joined names."""
    return conn.execute("SELECT * FROM alias_conflicts WHERE id = ?", (conflict_id,)).fetchone()


def conflict_endpoints(conn: db.Conn, conflict_id: str) -> db.Row | None:
    return conn.execute(
        "SELECT existing_entity_id, proposed_entity_id FROM alias_conflicts WHERE id = ?",
        (conflict_id,),
    ).fetchone()


def conflict_resolution(conn: db.Conn, conflict_id: str) -> db.Row | None:
    return conn.execute(
        "SELECT resolution FROM alias_conflicts WHERE id = ?", (conflict_id,)
    ).fetchone()


def reopen_conflict(conn: db.Conn, conflict_id: str) -> None:
    """Undo a verdict. `resolved_at = NULL` matters as much as `resolved = 0`:
    a row that still carries a decision timestamp reads as decided to everything
    that looks at the stamp rather than the flag."""
    conn.execute(
        "UPDATE alias_conflicts SET resolved = 0, resolution = NULL, resolved_at = NULL "
        "WHERE id = ?",
        (conflict_id,),
    )


def mark_related(conn: db.Conn, *, conflict_id: str, now: str) -> None:
    conn.execute(
        "UPDATE alias_conflicts SET resolved = 1, resolution = 'related', resolved_at = ? "
        "WHERE id = ?",
        (now, conflict_id),
    )


def retract_run_assertions(conn: db.Conn, *, run_id: str, now: str) -> None:
    """Withdraw everything one curation run asserted.

    An UPDATE of `retracted_at` rather than a DELETE, because contract C1 says
    assertions are append-only: a retracted claim must stay readable, or the
    evidence panel for an edge that was withdrawn shows nothing rather than
    showing why.
    """
    conn.execute(
        "UPDATE assertions SET retracted_at = ? WHERE run_id = ? AND retracted_at IS NULL",
        (now, run_id),
    )


def entity_names(conn: db.Conn, *ids: str) -> list[db.Row]:
    marks = ",".join("?" for _ in ids)
    return list(
        conn.execute(
            f"SELECT id, name FROM entities WHERE id IN ({marks})",  # noqa: S608 - marks are `?`
            ids,
        ).fetchall()
    )


def entity_types(conn: db.Conn, *ids: str) -> dict[str, str]:
    marks = ",".join("?" for _ in ids)
    return {
        r["id"]: r["type"]
        for r in conn.execute(
            f"SELECT id, type FROM entities WHERE id IN ({marks})",  # noqa: S608 - marks are `?`
            ids,
        )
    }


# ── what a rebuild would change ──────────────────────────────────────


def merged_entity_ids(conn: db.Conn) -> list[str]:
    return [r["id"] for r in conn.execute("SELECT id FROM entities WHERE status = 'merged'")]


def curated_relations(conn: db.Conn) -> list[dict[str, Any]]:
    """Relations a curator asserted, with both endpoints' status.

    `run_id LIKE 'curation:%'` selects the runs a person caused. `retracted_at IS
    NULL` matters for the same reason it does in `graph_service`: a brief that
    only ever cited a withdrawn claim must not be listed as provenance for an
    edge that does not rest on it.
    """
    return [
        dict(r)
        for r in conn.execute(
            """
            SELECT a.subject_id_raw AS s, a.predicate AS p, a.object_id_raw AS o,
                   (SELECT status FROM entities WHERE id = a.subject_id_raw) AS s_status,
                   (SELECT status FROM entities WHERE id = a.object_id_raw) AS o_status
              FROM assertions a
             WHERE a.run_id LIKE 'curation:%' AND a.retracted_at IS NULL
            """
        )
    ]


def undecided_definitions_by_status(conn: db.Conn) -> dict[str, int]:
    return {
        r["status"]: r["n"]
        for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM acronym_definitions "
            "WHERE decided_at IS NULL GROUP BY status"
        )
    }


def pending_definition_surfaces(conn: db.Conn) -> int:
    """Distinct SURFACES with nothing decided about them -- what the queue serves.

    `entity_id IS NULL` mirrors the queue's own filter: a surface attached to an
    entity is not a definition question any more.
    """
    return int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM (SELECT normalized FROM acronym_definitions "
            "  WHERE status IN ('active','ambiguous','undefined') AND entity_id IS NULL "
            "  GROUP BY normalized "
            "  HAVING SUM(CASE WHEN decided_at IS NOT NULL THEN 1 ELSE 0 END) = 0) pending"
        ).fetchone()["n"]
    )


# ── graph coverage ───────────────────────────────────────────────────


def entity_statuses(conn: db.Conn) -> list[db.Row]:
    return list(conn.execute("SELECT id, status FROM entities").fetchall())


def distinct_relations(conn: db.Conn) -> list[db.Row]:
    return list(
        conn.execute(
            "SELECT DISTINCT subject_id_raw AS s, predicate AS p, object_id_raw AS o "
            "FROM assertions WHERE kind = 'relation' AND retracted_at IS NULL"
        ).fetchall()
    )


def provisional_count(conn: db.Conn) -> int:
    return int(
        conn.execute("SELECT COUNT(*) AS n FROM entities WHERE status = 'provisional'").fetchone()[
            "n"
        ]
    )


# ── source health, and the sites the extension may annotate ──────────


def source_health(conn: db.Conn) -> list[db.Row]:
    return list(
        conn.execute(
            "SELECT source_id, consecutive_failures, auto_disabled, last_success_at, last_error "
            "FROM source_health ORDER BY source_id"
        ).fetchall()
    )


def annotatable_sites(conn: db.Conn, source_ids: list[str]) -> list[db.Row]:
    """Domains this corpus has actually pulled documents from.

    `duplicate_of IS NULL` so a syndicated reprint does not inflate a domain's
    document count -- the number is meant to answer "is this outlet worth
    enabling", and a wire story carried five times is one story.
    """
    if not source_ids:
        return []
    marks = ",".join("?" for _ in source_ids)
    return list(
        conn.execute(
            f"""
            SELECT site_domain,
                   COUNT(*)          AS documents,
                   MAX(fetched_at)   AS last_seen_at,
                   MAX(publisher)    AS publisher
            FROM documents
            WHERE site_domain IS NOT NULL AND site_domain != ''
              AND duplicate_of IS NULL
              AND source_id IN ({marks})
            GROUP BY site_domain
            """,  # noqa: S608 - marks are `?` placeholders
            tuple(source_ids),
        ).fetchall()
    )


# ── corpus counts, for the data screen ───────────────────────────────


def row_count(conn: db.Conn, table: str) -> int:
    """`table` comes from `purge_service.CORPUS_TABLES`, a module constant, and
    is checked against `information_schema` first -- never from a caller."""
    return int(
        conn.execute(f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()["n"]  # noqa: S608
    )


def row_counts(conn: db.Conn, tables: list[str]) -> dict[str, int]:
    """Count several tables in one round trip, skipping any that are absent.

    The per-table form costs two statements each -- an existence probe and a
    count -- and the purge dialog asks about every corpus table at once. One
    `information_schema` read then one `UNION ALL` answers the same question
    without the round trips.

    Interpolation is safe for the reason the single-table count gives: the names
    are a module constant, and they are filtered through `information_schema`
    here before any of them reaches a statement. Result order follows `tables`,
    because a set has none and the caller renders a list.
    """
    present = {
        row["table_name"]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
    }
    wanted = [table for table in tables if table in present]
    if not wanted:
        return {}

    counted = " UNION ALL ".join(
        f"SELECT '{table}' AS name, COUNT(*) AS n FROM \"{table}\"" for table in wanted
    )
    found = {row["name"]: int(row["n"]) for row in conn.execute(counted)}  # noqa: S608
    return {table: found[table] for table in wanted}


# ── one-statement reads and writes from the smaller routers ──────────
#
# Kept here rather than in four one-function modules: each is the only statement
# its router had, and a repository package with four files holding one query each
# is filing rather than structure.


def latest_brief_id(conn: db.Conn) -> db.Row | None:
    """`generated_at`, not `date`: several briefs can share a day, and a rewrite
    must target the most recently GENERATED one."""
    return conn.execute("SELECT id FROM briefs ORDER BY generated_at DESC LIMIT 1").fetchone()


def entity_name(conn: db.Conn, entity_id: str) -> db.Row | None:
    return conn.execute("SELECT id, name FROM entities WHERE id = ?", (entity_id,)).fetchone()


def insert_curated_relation(
    conn: db.Conn,
    *,
    assertion_id: str,
    conflict_id: str,
    article_id: str,
    predicate: str,
    subject_id: str,
    object_id: str,
    evidence_quote: str,
    now: str,
    method: str = "manual",
) -> None:
    """A relation a curator asserted, written as an ordinary assertion.

    `method` DEFAULTS to manual and is passed explicitly by the one caller that
    is not a person. A row claiming a curator decided something the pipeline
    decided is the same lie as a definition claiming a source it does not have,
    and this column is what a reader of the graph is trusting.

    `confidence` is 1.0 and `method` is `manual` because a person said so, and
    `run_id` carries the conflict it was decided from -- which is what lets
    `curated_relations` find them again by prefix.
    """
    db.insert(
        conn,
        "assertions",
        {
            "id": assertion_id,
            "run_id": f"curation:{conflict_id}",
            "article_id": article_id,
            "cluster_id": "cluster:curated",
            "kind": "relation",
            "predicate": predicate,
            "subject_id_raw": subject_id,
            "object_id_raw": object_id,
            "evidence_quote": evidence_quote,
            "confidence": 1.0,
            "method": method,
            "created_at": now,
        },
    )


def retract_assertion(conn: db.Conn, *, assertion_id: str, now: str) -> int:
    """Withdraw ONE claim, by id. Returns how many rows it touched.

    The same `UPDATE retracted_at` as `retract_run_assertions` and for the same
    reason (C1: assertions are append-only, so a withdrawn claim has to stay
    readable), narrowed to a single row because the population is different. A
    curation run's assertions are withdrawn together because the whole verdict
    is being undone. This one is for a claim the extractor made that a person
    has read and found the evidence does not carry, which arrives one at a time.

    `retracted_at IS NULL` in the predicate makes a second call a no-op rather
    than moving the timestamp, so the row keeps saying when it was withdrawn.
    """
    return conn.execute(
        "UPDATE assertions SET retracted_at = ? WHERE id = ? AND retracted_at IS NULL",
        (now, assertion_id),
    ).rowcount


def assertion(conn: db.Conn, assertion_id: str) -> db.Row | None:
    """One assertion, for a caller that must refuse cleanly when it is absent."""
    return conn.execute(
        "SELECT id, kind, predicate, subject_id_raw, object_id_raw, evidence_quote, "
        "       retracted_at "
        "  FROM assertions WHERE id = ?",
        (assertion_id,),
    ).fetchone()
