"""Every statement that touches `config_documents` and its history.

ADR 0234: the files under `VENONA_CONFIG_DIR` and `VENONA_PROFILE_DIR` are a
projection written out of these rows at boot. `services/config_service.py` is
the only caller, for the reason it is the only writer of the files.
"""

from __future__ import annotations

from venona.db import core as db

KEEP = 20


def get(conn: db.Conn, *, key: str) -> db.Row | None:
    row = conn.execute(
        "SELECT key, content, version, updated_at FROM config_documents WHERE key = ?",
        (key,),
    ).fetchone()
    return row


def all_docs(conn: db.Conn) -> list[db.Row]:
    return list(
        conn.execute("SELECT key, content, version, updated_at FROM config_documents").fetchall()
    )


def versions(conn: db.Conn) -> dict[str, str]:
    """The staleness question without the content."""
    rows = conn.execute("SELECT key, version FROM config_documents").fetchall()
    return {row["key"]: row["version"] for row in rows}


def upsert(conn: db.Conn, *, key: str, content: str, version: str, now: str) -> None:
    """The commit point of a save: the file is written after this returns."""
    conn.execute(
        "INSERT INTO config_documents (key, content, version, updated_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT (key) DO UPDATE SET "
        "content = EXCLUDED.content, version = EXCLUDED.version, "
        "updated_at = EXCLUDED.updated_at",
        (key, content, version, now),
    )


def snapshot(conn: db.Conn, *, key: str, content: str, version: str, stamp: str) -> None:
    """Keep the version being replaced.

    All three columns are the key, for the reason `{key}.{stamp}.{version}.bak`
    carried both -- see the DDL, where (key, version) alone is measured losing
    snapshots the live instance had.
    """
    conn.execute(
        "INSERT INTO config_document_history (key, version, content, stamp) "
        "VALUES (?, ?, ?, ?) ON CONFLICT (key, version, stamp) DO NOTHING",
        (key, version, content, stamp),
    )


def prune(conn: db.Conn, *, key: str, keep: int = KEEP) -> None:
    """Drop everything past the newest `keep` for one document.

    Stamp THEN version, a total order where stamp alone is not: two saves in one
    second share a stamp, and the tiebreak decided which snapshot the prune took
    differently on different machines.
    """
    conn.execute(
        "DELETE FROM config_document_history WHERE (key, version, stamp) IN ("
        "  SELECT key, version, stamp FROM config_document_history WHERE key = ?"
        "  ORDER BY stamp DESC, version DESC OFFSET ?"
        ")",
        (key, keep),
    )


def history(conn: db.Conn, *, key: str, limit: int) -> list[db.Row]:
    """Saved versions of one document, newest first and bounded.

    Pruning keeps this table small in the ordinary case, but pruning is a policy
    that runs on write and this is a read: a bound here is what makes the size
    of the answer a property of the query rather than of how well housekeeping
    has kept up.
    """
    return list(
        conn.execute(
            "SELECT key, version, stamp, length(content) AS bytes "
            "FROM config_document_history WHERE key = ? "
            "ORDER BY stamp DESC, version DESC LIMIT ?",
            (key, limit),
        ).fetchall()
    )


def snapshot_content(conn: db.Conn, *, key: str, version: str, stamp: str) -> str | None:
    """Keyed on the stamp: the same content can have been current twice."""
    row = conn.execute(
        "SELECT content FROM config_document_history WHERE key = ? AND version = ? AND stamp = ?",
        (key, version, stamp),
    ).fetchone()
    return row["content"] if row else None
