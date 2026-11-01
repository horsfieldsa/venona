"""Every statement the vault writer reads, and nothing else.

Separate from `data_export.py`, which records the archives themselves. Two
jobs, two modules: this one reads the corpus, that one writes one row.

WHOLE-TABLE READS, on purpose. The writer has to allocate every note name
before it can emit a single link, because a wikilink resolves by name across
the whole folder -- so there is no streaming version of this that still
produces a folder with no dead links in it. The caller holds one
`db.snapshot` across all of it, on a connection of its own.
"""

from __future__ import annotations

from venona.db import core as db


def entities(conn: db.Conn) -> list[db.Row]:
    """Everything that gets a note.

    `status = 'active'` only, matching `graph/projector.py`. A provisional
    name is an uncurated guess, and a folder the operator keeps on disk is a
    worse place to leave one than a screen they can re-read tomorrow.
    """
    return list(
        conn.execute(
            "SELECT id, type, name, expansion, org_type, relevance, mention_count, "
            "source_count, first_seen, last_seen "
            "FROM entities WHERE status = 'active' ORDER BY type, name, id"
        ).fetchall()
    )


def aliases(conn: db.Conn) -> list[db.Row]:
    """Every surface, for the `aliases` property that makes a short form link."""
    return list(
        conn.execute(
            "SELECT entity_id, surface, is_acronym FROM aliases ORDER BY entity_id, surface"
        ).fetchall()
    )


def relation_assertions(conn: db.Conn) -> list[db.Row]:
    """Live relationship claims, with the publisher that carried each one.

    `site_domain` rather than `article_id` because a corroboration count is a
    count of PUBLISHERS -- `services/graph_service.py::edge_provenance` says so,
    and two answers to "is this edge corroborated" that disagree is exactly the
    defect the graph-health check exists to find.
    """
    return list(
        conn.execute(
            "SELECT a.subject_id_raw, a.object_id_raw, a.predicate, a.article_id, "
            "a.evidence_quote, a.confidence, d.site_domain "
            "FROM assertions a LEFT JOIN documents d ON d.id = a.article_id "
            "WHERE a.kind = 'relation' AND a.retracted_at IS NULL "
            "AND a.subject_id_raw IS NOT NULL AND a.object_id_raw IS NOT NULL "
            "ORDER BY a.subject_id_raw, a.predicate, a.object_id_raw, a.id"
        ).fetchall()
    )


def entity_definitions(conn: db.Conn) -> list[db.Row]:
    """What a name means, and what says so.

    `status` and `method` travel with it because an undefined row is a real row
    (invariant 3) and the note has to say so rather than say nothing, and
    because a tier-2 synthesis carries no quote and must never be rendered as
    one (contract C8).
    """
    return list(
        conn.execute(
            "SELECT entity_id, definition, method, status, grounding_article_id, "
            "grounding_url, grounding_quote, source_article_ids "
            "FROM entity_definitions ORDER BY entity_id"
        ).fetchall()
    )


def acronym_definitions(conn: db.Conn) -> list[db.Row]:
    """The glossary, on the same terms as `entity_definitions`."""
    return list(
        conn.execute(
            "SELECT id, surface, normalized, expansion, entity_id, method, status, "
            "grounding_article_id, grounding_url, grounding_quote, "
            "corroboration_count, sighting_count "
            "FROM acronym_definitions ORDER BY surface, id"
        ).fetchall()
    )


def documents(conn: db.Conn) -> list[db.Row]:
    """Citation shells. NEVER `text_nfc`.

    The folder carries a source's title, publisher, author, date, address and
    the stored excerpt, plus the verbatim sentences that back a definition or a
    relationship. It does not carry the article. Redistributing a publisher's
    body text is not something an administrator pressing one button has agreed
    to on their behalf, and the column is not selected here so that no later
    caller can reach it through this module by accident.

    `duplicate_of IS NULL` matches the projection -- a duplicate is the same
    story read twice and would become a second note about one article.
    """
    return list(
        conn.execute(
            "SELECT id, url, title, publisher, author, excerpt, published_at, site_domain "
            "FROM documents WHERE duplicate_of IS NULL "
            "ORDER BY published_at, publisher, title, id"
        ).fetchall()
    )


def briefs(conn: db.Conn) -> list[db.Row]:
    """Every brief, with NO archive ceiling.

    Every read path in `db/repositories/brief.py` takes a `ceiling` -- the
    entitlement limit that shortens a lapsed reader's archive. An administrator
    taking their own instance's history out is not a lapsed reader, and
    applying one here would truncate the folder for a reason that has nothing
    to do with the export.
    """
    return list(
        conn.execute(
            "SELECT id, date, title, dek, generated_at FROM briefs ORDER BY date, id"
        ).fetchall()
    )


def brief_items(conn: db.Conn) -> list[db.Row]:
    return list(
        conn.execute(
            "SELECT id, brief_id, section, rank, cluster_id, headline, state, state_note, ord "
            "FROM brief_items ORDER BY brief_id, section, ord, rank"
        ).fetchall()
    )


def brief_blocks(conn: db.Conn) -> list[db.Row]:
    """The prose, as blocks plus offsets.

    `spans` is where the hover layer lives, and it is what becomes a wikilink
    on the way out.
    """
    return list(
        conn.execute(
            "SELECT id, brief_id, section, heading, ordinal, type, depth, text, "
            "spans, marks, item_id, role "
            "FROM brief_blocks ORDER BY brief_id, section, ordinal"
        ).fetchall()
    )
