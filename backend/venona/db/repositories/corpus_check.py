"""Every statement that corpus_check runs.

Serves `services/corpus_check.py`. The module keeps its
decisions; this holds the reads and writes they are made of. See
`db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def select_documents(conn: db.Conn):
    """Every body, in a fixed order.

    `ORDER BY id` because `Support.phrase_documents` is built by appending in row
    order and is compared against a golden file: without it Postgres may hand
    back the same set in a different sequence and the comparison flaps.
    """
    return conn.execute("SELECT id, text_nfc FROM documents WHERE text_nfc IS NOT NULL ORDER BY id")


def select_acronym_definitions_by_method(conn: db.Conn, p0):
    """The definitions of one tier, most-sighted first.

    `surface, expansion` breaks ties on `sighting_count`, which repeats heavily:
    an order ambiguous among equal counts makes the audit list disagree with
    itself between two identical calls.
    """
    return conn.execute(
        "SELECT surface, expansion FROM acronym_definitions WHERE expansion IS NOT NULL "
        "AND method = ? AND status IN ('active','ambiguous') "
        "ORDER BY sighting_count DESC, surface, expansion",
        (p0,),
    )


def select_audit_fingerprint(conn: db.Conn, p0):
    """A cheap stamp over BOTH of `audit`'s inputs.

    The audit reads every document body AND the method-scoped definitions, so
    the stamp must move when either does: a lookup adds a `web_grounded` row
    without touching `documents`, and a memo keyed on the corpus alone would go
    stale on exactly the surface the curator just grounded. `MAX(created_at)`
    is a sequential scan (no index on the column) — cheap only relative to the
    audit itself, which reads every body.
    """
    return conn.execute(
        """
        SELECT (SELECT COUNT(*) FROM documents WHERE text_nfc IS NOT NULL) AS docs,
               (SELECT MAX(created_at) FROM documents) AS newest_doc,
               (SELECT COUNT(*) FROM acronym_definitions
                 WHERE expansion IS NOT NULL AND method = ?
                   AND status IN ('active','ambiguous')) AS defs,
               (SELECT MAX(last_seen) FROM acronym_definitions) AS newest_seen,
               (SELECT MAX(decided_at) FROM acronym_definitions) AS newest_verdict
        """,
        (p0,),
    )
