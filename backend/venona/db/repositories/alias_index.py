"""Every statement that alias_index runs.

Serves `services/alias_index.py`. The module keeps its
decisions; this holds the reads and writes they are made of. See
`db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def select_index_fingerprint(conn: db.Conn):
    """A stamp over every VALUE `build_index` reads.

    A count is not enough and never was. The mutations that change this index
    are UPDATEs -- `acronym_definitions.status` going to `active`, which is
    exactly what grounding a term from the extension does; `entity_id` being
    set; an entity's status swapping; an alias's strictness being changed --
    and a row count is blind to every one of them. A cache keyed on counts
    would keep serving `undefined` for a term the reader had just grounded,
    permanently, which is the failure invariant 3 exists to prevent.

    So this hashes the COLUMNS, not the rows: `SUM(hashtext(...))` over exactly
    the values the three index queries select. It cannot miss an update to a
    value it covers, and adding a column to those queries without adding it
    here is the one way to break that -- which is why the column lists below
    are written out rather than `*`.

    It scans the same rows `build_index` does, and that is fine: the 84ms this
    exists to save is the Aho-Corasick automaton build in Python, not the read.

    `COALESCE` on every nullable column because `||` with a NULL operand is
    NULL, which would quietly collapse a row's whole contribution to nothing.
    """
    return conn.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM aliases a JOIN entities e ON e.id = a.entity_id
            WHERE e.status IN ('active', 'merged', 'provisional')) AS alias_rows,
          (SELECT COALESCE(SUM(hashtext(
                     a.surface || '|' || a.strictness || '|' || a.is_acronym
                     || '|' || a.normalized || '|' || COALESCE(a.added_by, '')
                     || '|' || e.id
                     || '|' || COALESCE(e.relevance::text, '')
                     || '|' || e.status))::text, '')
             FROM aliases a JOIN entities e ON e.id = a.entity_id
            WHERE e.status IN ('active', 'merged', 'provisional')) AS alias_hash,
          (SELECT COUNT(*) FROM ambiguous_surfaces) AS ambiguous_rows,
          (SELECT COALESCE(SUM(hashtext(normalized))::text, '')
             FROM ambiguous_surfaces) AS ambiguous_hash,
          (SELECT COUNT(*) FROM acronym_definitions
            WHERE status IN ('active', 'undefined', 'ambiguous')
              AND entity_id IS NULL) AS glossary_rows,
          (SELECT COALESCE(SUM(hashtext(
                     id || '|' || surface || '|' || normalized || '|' || status))::text, '')
             FROM acronym_definitions
            WHERE status IN ('active', 'undefined', 'ambiguous')
              AND entity_id IS NULL) AS glossary_hash,
          (SELECT COUNT(*) FROM entities
            WHERE status IN ('active', 'provisional')) AS tier_a_rows,
          (SELECT COALESCE(SUM(hashtext(id))::text, '') FROM entities
            WHERE status IN ('active', 'provisional')) AS tier_a_hash
        """
    )


def select_ambiguous_surfaces(conn: db.Conn):
    return conn.execute("SELECT normalized FROM ambiguous_surfaces")


def select_entities(conn: db.Conn):
    return conn.execute("SELECT id FROM entities WHERE status IN ('active', 'provisional')")


def select_acronym_definitions(conn: db.Conn):
    return conn.execute(
        """
        SELECT g.id, g.normalized, g.expansion, g.confidence
          FROM acronym_definitions g
          JOIN ambiguous_surfaces a ON a.normalized = g.normalized
         WHERE g.expansion IS NOT NULL AND g.status IN ('active', 'ambiguous')
         ORDER BY g.confidence DESC, g.expansion
        
        """
    )


def select_acronym_definitions_2(conn: db.Conn):
    return conn.execute(
        "SELECT surface FROM acronym_definitions "
        "WHERE status IN ('active','undefined','ambiguous') AND entity_id IS NULL"
    )


def select_entities_2(conn: db.Conn):
    return conn.execute("SELECT id, status FROM entities")


def select_ambiguous_surfaces_2(conn: db.Conn):
    return conn.execute("SELECT normalized FROM ambiguous_surfaces")


def select_aliases(conn: db.Conn):
    return conn.execute(
        """
        SELECT a.surface, a.strictness, a.is_acronym, a.normalized, a.added_by,
               e.id AS ref, e.relevance, e.status
        FROM aliases a
        JOIN entities e ON e.id = a.entity_id
        WHERE e.status IN ('active', 'merged', 'provisional')
    
        """
    )


def select_acronym_definitions_glossary(conn: db.Conn):
    """Definitions the index may hold in their own right.

    `entity_id IS NULL` is what keeps a surface from matching twice: where a
    definition names an entity, that entity's alias annotates it.
    """
    return conn.execute(
        """
        SELECT id, surface, normalized, status
        FROM acronym_definitions
        WHERE status IN ('active','undefined','ambiguous') AND entity_id IS NULL
    
        """
    )


def select_acronym_definitions_ambiguous(conn: db.Conn):
    """Every live definition, for the caller to pick the ambiguous surfaces out of.

    Unfiltered by `entity_id`, which is the whole point of a second query: a
    surface a person has declared ambiguous is demoted to strictness 0 in every
    ENTITY alias holding it, so the placeholder is the only thing that can
    annotate it and the query above will not supply one for a definition naming
    an entity. `SDA` sits in that gap otherwise -- active, grounded, in-article,
    and reaching no reader.
    """
    return conn.execute(
        """
        SELECT id, surface, normalized, status
        FROM acronym_definitions
        WHERE status IN ('active','undefined','ambiguous')
    
        """
    )
