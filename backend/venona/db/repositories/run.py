"""Every statement that run_service runs.

Serves `services/run_service.py`. The module keeps its
decisions; this holds the reads and writes they are made of. See
`db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def update_runs_by_id(conn: db.Conn, p0):
    return conn.execute("UPDATE runs SET status='running' WHERE id=? AND status='queued'", (p0,))


def update_runs_by_status_counters(conn: db.Conn, p0, p1, p2, p3, p4, p5, p6, p7, p8):
    return conn.execute(
        "UPDATE runs SET status=?, outcome=?, stage=NULL, counters=?, warnings=?, "
        "       brief_id=?, cost_usd=?, error=?, finished_at=? WHERE id=?",
        (p0, p1, p2, p3, p4, p5, p6, p7, p8),
    )


def update_run_stages_by_finished_at_run_id(conn: db.Conn, p0, p1):
    return conn.execute(
        "UPDATE run_stages SET status='interrupted', finished_at=? WHERE run_id=? "
        "AND status='running'",
        (p0, p1),
    )


def update_runs_by_stage_id(conn: db.Conn, p0, p1):
    return conn.execute("UPDATE runs SET stage = ? WHERE id = ?", (p0, p1))


def select_runs(conn: db.Conn):
    return conn.execute("SELECT id, kind, owner_pid FROM runs WHERE status IN ('running','queued')")


def select_rows(conn: db.Conn, p0):
    return conn.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (p0,))


def insert_run_events(conn: db.Conn, p0, p1, p2, p3, p4, p5):
    return conn.execute(
        "INSERT INTO run_events (run_id, seq, ts, type, stage, data) VALUES (?,?,?,?,?,?)",
        (p0, p1, p2, p3, p4, p5),
    )


def update_runs_by_hidden_at_id(conn: db.Conn, p0, p1):
    return conn.execute("UPDATE runs SET hidden_at = ? WHERE id = ?", (p0, p1))


def update_runs_by_id_2(conn: db.Conn, p0):
    return conn.execute("UPDATE runs SET hidden_at = NULL WHERE id = ?", (p0,))


def update_runs_by_status_error(conn: db.Conn, p0, p1, p2, p3):
    """The reaper's terminal row, for a run whose process died.

    `stage=NULL` like both the other terminators above and below it. Without it
    a reaped run keeps the stage it was inside for ever, and every surface that
    reads `runs.stage` to mean "what is happening right now" reports a stage
    running under a finished header. Observed on production: an `on_demand` run
    settled `succeeded_partial` still carrying `stage='brief'`.
    """
    return conn.execute(
        "UPDATE runs SET status=?, stage=NULL, error=?, finished_at=? WHERE id=?",
        (p0, p1, p2, p3),
    )


def select_run_events_by_run_id(conn: db.Conn, p0, p1, p2):
    return conn.execute(
        "SELECT seq, ts, type, stage, data FROM run_events "
        "WHERE run_id = ? AND seq > ? ORDER BY seq LIMIT ?",
        (p0, p1, p2),
    )


def select_runs_by_id(conn: db.Conn, p0):
    """The whole row, plus who started it as an address.

    `SELECT r.*` rather than a column list: every caller of this reads fields the
    list projection does not carry (`profile_hash`, `owner_pid`), and enumerating
    them here would be a second place to remember a new column.
    """
    return conn.execute(
        "SELECT r.*, u.email AS started_by_email "
        "  FROM runs r LEFT JOIN users u ON u.id = r.started_by WHERE r.id = ?",
        (p0,),
    )


def select_run_stages_by_run_id(conn: db.Conn, p0):
    return conn.execute("SELECT * FROM run_stages WHERE run_id = ? ORDER BY started_at", (p0,))


def select_runs_by_id_2(conn: db.Conn, p0):
    return conn.execute("SELECT * FROM runs WHERE id = ?", (p0,))


def select_runs_2(conn: db.Conn):
    return conn.execute("SELECT id FROM runs WHERE hidden_at IS NULL")


def update_runs_by_hidden_at_id_2(conn: db.Conn, p0, p1):
    return conn.execute("UPDATE runs SET hidden_at = ? WHERE id = ?", (p0, p1))


def select_runs_3(conn: db.Conn):
    return conn.execute("SELECT id, owner_pid FROM runs WHERE status IN ('running','queued')")


def select_run_events_by_run_id_2(conn: db.Conn, p0):
    return conn.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM run_events WHERE run_id = ?", (p0,)
    )


def select_runs_4(conn: db.Conn):
    return conn.execute("SELECT COUNT(*) AS n FROM runs WHERE hidden_at IS NOT NULL")


def select_run_stages_by_run_id_2(conn: db.Conn, p0):
    return conn.execute("SELECT COUNT(*) FROM run_stages WHERE run_id = ? AND status = 'ok'", (p0,))


#: What a run LIST row carries. `params` is here and not only in the detail
#: projection: it is what a row is ABOUT -- the acronym a lookup looked up -- and
#: without it a history of light runs is N identical lines reading "Definition
#: lookup".
#
#: `started_by` is joined back to an address rather than served as an id, the
#: same way every other actor column in the app is (C1). It has been WRITTEN
#: since migration 0002 -- `run_launcher` passes it on every launch -- and read
#: by nothing, so "who started the run that spent this" was a question the
#: database could answer and no surface asked. LEFT JOIN: the scheduler and the
#: CLI start runs too, and NULL is the honest record of that.
_LIST_COLUMNS = (
    "SELECT r.id, r.kind, r.status, r.outcome, r.stage, r.params, r.counters, r.warnings, "
    "       r.brief_id, "
    "       r.cost_usd, r.started_at, r.finished_at, r.error, r.hidden_at, "
    # ON THE LIST, not only the detail. `select_runs_by_id` is `SELECT r.*` and
    # this is not, while `RunDetail` extends `RunSummary` -- so a column left
    # out here is one the history table can never show, and there is no such
    # thing as a detail-only field to fall back on.
    "       r.harness, r.billing_mode, "
    "       u.email AS started_by_email "
    "  FROM runs r LEFT JOIN users u ON u.id = r.started_by"
)


def listing(conn: db.Conn, *, include_hidden: bool, limit: int, offset: int) -> list[db.Row]:
    where = "" if include_hidden else " WHERE r.hidden_at IS NULL"
    return list(
        conn.execute(
            f"{_LIST_COLUMNS}{where} ORDER BY r.started_at DESC LIMIT ? OFFSET ?",  # noqa: S608
            (limit, offset),
        ).fetchall()
    )


def listing_total(conn: db.Conn, *, include_hidden: bool) -> int:
    """Counts what the same filter can reach, so "load more" terminates."""
    where = "" if include_hidden else " WHERE hidden_at IS NULL"
    return int(conn.execute(f"SELECT COUNT(*) AS n FROM runs{where}").fetchone()["n"])  # noqa: S608


#: The advisory-lock namespace every run lease lives in. Any 32-bit constant
#: would do; this one is arbitrary and only has to be stable, because the whole
#: point is that two venona processes agree on it and nothing else in the
#: database uses it.
LEASE_NAMESPACE = 0x56454E4F  # "VENO"


def claim_lease(conn: db.Conn, run_id: str) -> bool:
    """Take the SESSION-level advisory lock naming this run. True if acquired.

    `pg_try_advisory_lock`, not `pg_advisory_xact_lock`: a transaction lock is
    released at COMMIT, and the whole purpose here is a hold that outlives every
    transaction the run makes and ends only when the connection does.

    Not `pg_advisory_lock` either -- the blocking form would wait rather than
    report, and a caller that wanted to know whether someone else owns this run
    would instead hang until they let go.
    """
    return bool(
        conn.execute(
            "SELECT pg_try_advisory_lock(?, hashtext(?)) AS ok", (LEASE_NAMESPACE, run_id)
        ).fetchone()["ok"]
    )


def lease_is_held(conn: db.Conn, run_id: str) -> bool:
    """Is some live session holding this run's lease?

    Read from `pg_locks` rather than by trying to take the lock, because taking
    it would SUCCEED for a caller that already holds it (advisory locks are
    reentrant per session) and would also acquire a lock the caller then has to
    remember to release. This is a pure question.

    `objsubid = 2` selects the two-int form of the key, which is what
    `claim_lease` uses; the bigint form stores its halves in `classid`/`objid`
    and would collide with a namespace read as a key half.
    """
    row = conn.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM pg_locks
             WHERE locktype = 'advisory' AND granted
               AND classid = ? AND objid = hashtext(?)::oid AND objsubid = 2
        ) AS held
        """,
        (LEASE_NAMESPACE, run_id),
    ).fetchone()
    return bool(row["held"])


#: The advisory-lock namespace lane ADMISSION lives in. Separate from
#: `LEASE_NAMESPACE` because the two locks answer different questions and are
#: taken on the same connection: one namespace for both would let a lane name
#: hash onto some run id and make an unrelated launch wait on it.
LANE_LOCK_NAMESPACE = 0x56454E4C  # "VENL"


def lock_lane(conn: db.Conn, lane: str) -> None:
    """Hold `lane`'s admission gate for the rest of the caller's transaction.

    `pg_advisory_xact_lock`, and each half of that name is load-bearing.

    XACT, not session: this lock exists to make "is the lane free" and "write the
    row that occupies it" one indivisible step, which is a span with an end.
    Postgres releases it at COMMIT, so it cannot be leaked by a caller who
    forgets, by a raise, or by a process dying mid-launch -- and a leaked
    admission gate strands every run queued behind it permanently. The run's own
    liveness hold is `claim_lease`, which is session-scoped for the opposite
    reason.

    BLOCKING, not `try`: a launch that loses this race should evaluate the lane
    a moment later and refuse with the truth, rather than read "could not take
    the lock" as "the lane is free" or as an error of its own.
    """
    conn.execute("SELECT pg_advisory_xact_lock(?, hashtext(?))", (LANE_LOCK_NAMESPACE, lane))


def leased_run_ids(conn: db.Conn, run_ids: list[str]) -> set[str]:
    """Which of `run_ids` are currently leased. One round trip.

    `lane_conflict` and `reap_interrupted` both walk every unfinished run, and
    asking per row turned a single sweep into one query per run -- which the
    `/proc` version got away with because a `read()` on `/proc` is not a network
    round trip and this is.
    """
    if not run_ids:
        return set()
    marks = ",".join("?" for _ in run_ids)
    rows = conn.execute(
        f"""
        SELECT r AS run_id FROM unnest(ARRAY[{marks}]::text[]) AS r
         WHERE EXISTS (
            SELECT 1 FROM pg_locks
             WHERE locktype = 'advisory' AND granted
               AND classid = ? AND objid = hashtext(r)::oid AND objsubid = 2
         )
        """,  # noqa: S608 - placeholders only
        (*run_ids, LEASE_NAMESPACE),
    ).fetchall()
    return {row["run_id"] for row in rows}
