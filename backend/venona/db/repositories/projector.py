"""Every statement that projector runs.

Serves `graph/projector.py`. The module keeps its
decisions; this holds the reads and writes they are made of. See
`db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def select_entity_mappings(conn: db.Conn):
    return conn.execute("SELECT from_id, to_id FROM entity_mappings WHERE active = 1")


def select_mapping_fingerprint(conn: db.Conn):
    """A cheap stamp over the merges `resolve_mappings` collapses.

    THREE fields, because two of the obvious ones each miss a real edit. A merge
    inserts a row, so `total` moves. An unmerge does NOT: it keeps the row and
    clears `active` (`repositories/curate.py::unmerge`), which only `live` sees.
    And `newest` alone is blind under a frozen clock, which is how every seeded
    test database is written.
    """
    return conn.execute(
        """
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE active = 1) AS live,
               MAX(created_at) AS newest
          FROM entity_mappings
        """
    )


def select_aliases(conn: db.Conn):
    return conn.execute("SELECT entity_id, surface FROM aliases ORDER BY surface")


def select_entities(conn: db.Conn):
    return conn.execute("SELECT id, type FROM entities WHERE status = 'active'")


def select_entities_for_projection(conn: db.Conn):
    """Every entity the canvas may draw, corroborated or not (ADR 0247).

    `provisional` is here so a reader can ask to see what Venona has read and
    not yet confirmed; the node carries its `status` and the canvas draws an
    unconfirmed name hollow. `dormant` is NOT: it means uncorroborated AND
    silent for the whole window, which is ADR 0054's drain on a pool that
    otherwise only grows, and drawing it undoes the mechanism.

    This widens what is DRAWN and nothing else. Promotion still decides what the
    brief rail, the hover card and the alias index treat as real.
    """
    return conn.execute(
        """
        SELECT id, type, name, canonical_key, status, summary, expansion,
               org_type, relevance, mention_count, first_seen, last_seen
        FROM entities
        WHERE status IN ('active', 'provisional')
        """
    )


def select_assertions(conn: db.Conn):
    return conn.execute(
        """
        SELECT a.predicate, a.predicate_kind, a.subject_id_raw, a.object_id_raw,
               a.article_id, a.confidence, a.created_at, a.method, d.site_domain
        FROM assertions a
        LEFT JOIN documents d ON d.id = a.article_id
        WHERE a.kind = 'relation' AND a.retracted_at IS NULL
          AND a.subject_id_raw IS NOT NULL AND a.object_id_raw IS NOT NULL
        ORDER BY a.created_at
    
        """
    )


def select_documents(conn: db.Conn):
    return conn.execute(
        """
        SELECT d.id, d.url, d.title, d.publisher, d.published_at, d.site_domain,
               d.cluster_id, c.relevance_overall, c.tier
        FROM documents d
        LEFT JOIN clusters c ON c.id = d.cluster_id
        WHERE d.duplicate_of IS NULL
    
        """
    )


def select_assertions_2(conn: db.Conn):
    return conn.execute(
        """
        SELECT DISTINCT article_id, entity_id_raw, salience, created_at
        FROM assertions
        WHERE kind = 'entity' AND retracted_at IS NULL AND entity_id_raw IS NOT NULL
        ORDER BY article_id, entity_id_raw, created_at, salience
        """
    )
