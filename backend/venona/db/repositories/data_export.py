"""Every statement about `data_exports`, and nothing else.

`make lint-repositories` walks the backend and fails on the first SQL that
grows back outside `db/`. The reason is the read-scope seam: a query assembled
in a router looks exactly like a repository call at the call site, so the
boundary leaks one convenient statement at a time and the leak is invisible in
review.

NOTHING HERE JOINS `runs`. `data_exports.run_id` carries no foreign key
because `runs` is purged and these rows are not, so after a purge every value
in that column dangles. A listing that joined through it would lose every
export the moment somebody pressed delete -- which is the one moment the
feature exists for.
"""

from __future__ import annotations

from venona.db import core as db

#: Written and read by name rather than `*`, so a column added later has to be
#: named by whoever wants it served.
#: `content` is ABSENT and `content_len` stands in: a listing of twenty rows
#: would otherwise pull twenty archives through the pool to answer "is there a
#: file". `content()` is the one function that returns the bytes.
_COLUMNS = (
    "id, run_id, filename, path, bytes, sha256, counts, status, created_at, "
    "created_by, pruned_at, octet_length(content) AS content_len"
)

#: The file is on disk as far as anything knows. `pruned` and `missing` are
#: both absences and are deliberately different ones.
READY = "ready"
PRUNED = "pruned"
MISSING = "missing"


def insert(
    conn: db.Conn,
    *,
    export_id: str,
    run_id: str | None,
    filename: str,
    path: str,
    size_bytes: int,
    sha256: str,
    counts: str,
    created_at: str,
    created_by: str | None,
    content: bytes,
) -> None:
    """Record an archive, bytes and all (ADR 0234).

    THE BYTES ARE THE ROW, not a file it points at. This table promises a second
    download returns what somebody already has, and rebuilding over a corpus that
    has moved hands back a different folder under the same name -- so the archive
    can neither be regenerated nor live on a disk that ends at the next deploy.

    One statement, so no crash separates the bytes from the row. `path` is
    written empty. comment-history: ok -- describes rows we still hold.
    """
    conn.execute(
        "INSERT INTO data_exports "
        "(id, run_id, filename, path, bytes, sha256, counts, status, created_at, "
        " created_by, content) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            export_id,
            run_id,
            filename,
            path,
            size_bytes,
            sha256,
            counts,
            READY,
            created_at,
            created_by,
            content,
        ),
    )


def content(conn: db.Conn, export_id: str) -> bytes | None:
    """Its own function so the listing never drags megabytes per row."""
    row = conn.execute("SELECT content FROM data_exports WHERE id = ?", (export_id,)).fetchone()
    return bytes(row["content"]) if row and row["content"] is not None else None


def listing(conn: db.Conn, *, limit: int, offset: int = 0) -> list[db.Row]:
    """Newest first, pruned rows included.

    A row whose file has gone still appears. "There was one and it is gone" is
    a different fact from "there was never one", and the retention rule is only
    learnable from seeing the first.
    """
    return list(
        conn.execute(
            f"SELECT {_COLUMNS} FROM data_exports "
            "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    )


def listing_count(conn: db.Conn) -> int:
    """How many archives exist, pruned ones included."""
    return int(conn.execute("SELECT COUNT(*) AS n FROM data_exports").fetchone()["n"])


def get(conn: db.Conn, export_id: str) -> db.Row | None:
    return conn.execute(
        f"SELECT {_COLUMNS} FROM data_exports WHERE id = ?", (export_id,)
    ).fetchone()


def taken_filenames(conn: db.Conn, *, stem: str) -> set[str]:
    """Which names on this date are already spoken for.

    Asked of the TABLE and not the directory. A run killed between writing its
    archive and recording it leaves a file with no row, and picking the next
    free name by looking at the filesystem would step around that orphan
    forever.
    """
    rows = conn.execute(
        "SELECT filename FROM data_exports WHERE filename LIKE ?", (f"{stem}%",)
    ).fetchall()
    return {row["filename"] for row in rows}


def prunable(conn: db.Conn, *, keep: int) -> list[db.Row]:
    """The files past the keep count, oldest first."""
    return list(
        conn.execute(
            "SELECT id, path FROM data_exports WHERE status = ? "
            "ORDER BY created_at DESC, id DESC OFFSET ?",
            (READY, keep),
        ).fetchall()
    )


def mark_pruned(conn: db.Conn, export_id: str, *, now: str) -> None:
    """Retire an archive: the bytes go with the status, in one statement.

    Dropping `content` is the whole point of retention now that the archive is a
    column -- a row left `pruned` with its bytes still in it keeps every
    megabyte retention was meant to reclaim.
    """
    conn.execute(
        "UPDATE data_exports SET status = ?, pruned_at = ?, content = NULL WHERE id = ?",
        (PRUNED, now, export_id),
    )


def mark_missing(conn: db.Conn, export_id: str) -> None:
    """Record that we looked and it was not there.

    Separate from `mark_pruned` so the next reader is not told retention took a
    file that something else did.
    """
    conn.execute("UPDATE data_exports SET status = ? WHERE id = ?", (MISSING, export_id))
