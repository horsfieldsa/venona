"""Postgres access. The system of record (invariant 1).

WHY A WRAPPER RATHER THAN psycopg's CONNECTION DIRECTLY. Every statement is
written in SQLite's `?` paramstyle, and the end state for them is repository
functions built on SQLAlchemy Core. Rewriting them all in the same change that
swapped the engine would have meant that when something broke, nothing would say
which decision broke it. So `Conn` is a deliberate, throwaway shim: it converts
`?` to `%s` at execute time. The SQL has since moved into `db/repositories/`;
what retires the shim now is those statements becoming Core
(`docs/pm/NOT-DOING.md`, "Postgres").

THE THREE FACTS IT IS BUILT ON, all measured against psycopg 3.3.4 rather than
recalled, because each one is silent when you get it wrong:

  1. `?` is not a placeholder. Passing parameters with one raises
     `ProgrammingError: the query has 0 placeholders but 1 parameters`.
  2. A literal `%` is fine when `params is None`, and raises
     `ProgrammingError: only '%s', '%b', '%t' are allowed as placeholders`
     when parameters are passed -- INCLUDING an empty tuple. So `()` and `None`
     are not interchangeable, and `_convert` normalises the former to the latter.
     `repositories/pipeline_scheduler.py::select_runs` depends on it: `status
     LIKE 'succeeded%'`, no parameters, sent verbatim.
  3. psycopg's placeholder scan is a regex over the whole string and is NOT
     SQL-aware -- it flags a `%` inside a single-quoted literal. So when
     parameters ARE passed, every literal `%` must be doubled, wherever it sits.

AUTOCOMMIT IS LOAD-BEARING, NOT A PREFERENCE. Most write sites sit outside a
`transaction()` block and depend on committing on their own. psycopg does not
default to autocommit: without `autocommit=True` here, each of those would open
an implicit transaction that is never committed and is discarded when
`api/deps.py::get_db` closes the connection in its `finally` -- silent, total
write loss on most write paths, and invisible to any test that uses a single
connection, because it reads its own uncommitted writes back.
`services/curate.py` records three `conn.commit()` calls that committed nothing
for exactly this reason.

Rows come back as `Row`, a `dict` subclass, rather than psycopg's `dict_row`.
See its docstring: it exists to keep `row[0]` working alongside `row["col"]`, the
way `sqlite3.Row` did, and it records the one place the two genuinely differ in
a silent way.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg_pool import ConnectionPool

log = logging.getLogger("venona.db")

#: Where the authored DDL lives. Alembic's `0001_baseline` executes these and
#: `venona dump-schema` concatenates them into `docs/schema.sql`; nothing in
#: this module reads them any more, and the constant stays because those two
#: are the reason the directory is not an implementation detail of one script.
MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class Row(dict[str, Any]):
    """A result row. A drop-in for the `sqlite3.Row` it replaces.

    `row["col"]`, `dict(row)`, `row.keys()` and `"col" in row.keys()` come free
    from `dict`. Integer indexing is added because `sqlite3.Row` supported it and
    roughly twenty-five call sites use it -- almost all of them reading a single
    column, `SELECT COUNT(*) ... .fetchone()[0]`.

    Supporting both is a deliberate choice over converting those sites. The
    principle in the module this replaces -- index by NAME, because positional
    access across a schema this wide is how silent column-order bugs happen --
    was always cultural rather than enforced, since `sqlite3.Row` allowed both
    too. Keeping the same latitude keeps the engine swap mechanical; narrowing it
    is a separate change, and the right time is when SQL moves into repositories
    and the rows stop being anonymous tuples of whatever the query listed.

    Two columns of the same name still collapse into one key, exactly as
    `dict_row` would. `services/freshness.py` is the one place that builds a
    query of anonymous scalar subqueries, and it aliases every one.

    ONE HAZARD, because it is silent rather than loud. A row here is a dict, and
    a dict iterates its KEYS -- so `dict(rows)` over a two-column result yields
    `{"col1_name": "col2_name"}`, one entry and no exception, where a 2-tuple row
    would have built `{col1: col2}`. Write the comprehension out.
    `tests/test_glossary_meanings.py` is where this was found.
    """

    __slots__ = ()

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


def _row_factory(cursor: psycopg.Cursor[Any]):
    """psycopg row factory producing `Row`. See `Row` for why not `dict_row`."""
    description = cursor.description
    names = [column.name for column in description] if description else []

    def make(values: Sequence[Any]) -> Row:
        return Row(zip(names, values, strict=True))

    return make


def _convert(sql: str, params: Sequence[Any] | None) -> tuple[str, Sequence[Any] | None]:
    """Translate SQLite `?` paramstyle to psycopg `%s`. See the module docstring.

    Returns `(sql, None)` untouched when there is nothing to bind, because an
    empty tuple still triggers psycopg's placeholder scan and would reject a
    query containing a literal `%`.

    The walk tracks single-quoted literals, double-quoted identifiers, `--` line
    comments and `/* */` block comments, so a `?` inside any of them is left
    alone. `%` doubling is applied everywhere, including inside literals, because
    psycopg's own scan does not respect them.

    Dollar-quoting (`$$ ... $$`) is deliberately not handled. It cannot be
    reached: it appears only in function and DO bodies, which live in migrations,
    and migrations pass no parameters -- so they return above this walk entirely.
    """
    if not params:
        return sql, None

    out: list[str] = []
    i, n = 0, len(sql)
    quote: str | None = None  # "'", '"', "$tag$", "--" or "/*"

    while i < n:
        ch = sql[i]

        if quote is None:
            if ch == "'" or ch == '"':
                quote = ch
                out.append(ch)
                i += 1
                continue
            if sql.startswith("--", i):
                quote = "--"
                out.append("--")
                i += 2
                continue
            if sql.startswith("/*", i):
                quote = "/*"
                out.append("/*")
                i += 2
                continue
            if ch == "?":
                out.append("%s")
                i += 1
                continue
            if ch == "%":
                out.append("%%")
                i += 1
                continue
            out.append(ch)
            i += 1
            continue

        # Inside something. Only `%` is still special (psycopg's scan ignores
        # quoting); everything else is copied until the terminator.
        if ch == "%":
            out.append("%%")
            i += 1
            continue

        if quote == "'" or quote == '"':
            out.append(ch)
            i += 1
            if ch == quote:
                # A doubled quote is an escaped quote, not a terminator.
                if i < n and sql[i] == quote:
                    out.append(quote)
                    i += 1
                else:
                    quote = None
            continue

        if quote == "--":
            out.append(ch)
            i += 1
            if ch == "\n":
                quote = None
            continue

        if quote == "/*":
            if sql.startswith("*/", i):
                out.append("*/")
                i += 2
                quote = None
                continue
            out.append(ch)
            i += 1
            continue

        # Dollar-quoted.
        if sql.startswith(quote, i):
            out.append(quote)
            i += len(quote)
            quote = None
            continue
        out.append(ch)
        i += 1

    return "".join(out), params


class Conn:
    """A psycopg connection presenting the `?`-paramstyle call surface.

    Not a general-purpose abstraction and not a `Conn` Protocol over two
    dialects -- there is one dialect. It exists so the engine swap and the move
    of SQL into repositories stayed two reviewable changes rather than one
    unreviewable one.
    """

    __slots__ = ("dsn", "raw")

    def __init__(self, raw: psycopg.Connection[Any], dsn: str = "") -> None:
        self.raw = raw
        #: The DSN this connection was opened with.
        #:
        #: Carried rather than re-derived because `migrate()` needs it and psycopg
        #: will not give it back: `conn.info.get_parameters()` omits the password,
        #: so a DSN rebuilt from the connection cannot be reconnected with. Without
        #: this, `migrate(conn)` silently migrated whatever `get_settings()`
        #: pointed at instead of the database it was handed -- which in the test
        #: suite is the DEV database, and the symptom was 617 errors reading
        #: "relation assertions does not exist" from a template that was never
        #: built.
        self.dsn = dsn

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> psycopg.Cursor[Any]:
        text, args = _convert(sql, params)
        try:
            return self.raw.execute(text, args)
        except psycopg.Error as exc:
            # psycopg's message names the problem and not the statement --
            # "could not determine data type of parameter $1" with no way to tell
            # which of the repositories' statements it came from. Only the SQL is
            # attached, never `params`: the statement is static text, while the
            # parameters are corpus content and would end up in logs.
            raise type(exc)(f"{exc}\n--- while executing ---\n{sql.strip()}") from exc

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> None:
        rows = list(seq)
        if not rows:
            return
        # Converted against the FIRST row only, then reused. Every row in an
        # executemany shares one statement by definition, so a second conversion
        # would be identical work; taking the first row's shape also means a
        # caller passing rows of differing arity fails loudly in psycopg rather
        # than silently binding the wrong count.
        text, _ = _convert(sql, rows[0])
        # ALL-OR-NOTHING WITHOUT A TRANSACTION HERE, and that is measured
        # rather than assumed: psycopg 3.3.4 runs the whole batch as one
        # operation, so a violation on row 900 of 1000 leaves none of the 899
        # before it written, even under autocommit. Adding a BEGIN/COMMIT
        # around this would buy nothing and would nest inside a caller's own
        # transaction. `tests/test_transaction_boundaries.py` pins the
        # behaviour so a driver upgrade that changes it is a failing test
        # rather than a partial write nobody notices.
        with self.raw.cursor() as cur:
            cur.executemany(text, rows)

    def executescript(self, sql: str) -> None:
        """Run a multi-statement script.

        psycopg sends multiple `;`-separated statements in one round trip only
        when no parameters are passed -- which is exactly the migration case, and
        the reason `_convert` returns the text verbatim for `params=None`.
        """
        self.raw.execute(sql)

    @property
    def in_transaction(self) -> bool:
        return self.raw.info.transaction_status != psycopg.pq.TransactionStatus.IDLE

    def commit(self) -> None:
        self.raw.commit()

    def rollback(self) -> None:
        self.raw.rollback()

    def close(self) -> None:
        self.raw.close()


def connect(dsn: str) -> Conn:
    """Open an autocommit connection. See the module docstring on autocommit.

    There was a `same_thread` parameter here through the port, accepted and
    ignored. It was SQLite's `check_same_thread`, which existed because a
    request's connection is created in a threadpool worker and used on the event
    loop thread; psycopg connections are safe for that pattern. It is gone, along
    with the sixteen call sites that passed it.
    """
    raw = psycopg.connect(dsn, autocommit=True, row_factory=_row_factory)
    return Conn(raw, dsn)


def pool(dsn: str, *, max_size: int) -> ConnectionPool:
    """A bounded pool of autocommit connections, for the REQUEST path only.

    WHAT EXHAUSTION LOOKS LIKE, which is why this is bounded rather than large:
    an authenticated request opens two connections of its own before it reaches a
    route (the session lookup and the route's `get_db`), so concurrency multiplies
    straight into the server's `max_connections`. Past it, `psycopg.connect`
    raises, `_lookup_user` fails CLOSED by design, and every request in flight
    renders as a signed-out browser. The database is fine and the app says the
    user's session is gone -- a failure mode that reads as an auth bug and sends
    you looking in the wrong module entirely.

    A bound turns that into waiting. A caller that cannot get a connection blocks
    for one instead of taking the last free slot on the server, so a burst is slow
    rather than a fleet-wide sign-out.

    `autocommit=True` and `_row_factory` are passed through EXACTLY as `connect`
    sets them. The module docstring's warning applies with full force here: ~52
    write sites depend on autocommit, and a pooled connection without it discards
    them silently on return.

    WHAT DOES NOT BELONG HERE. Two kinds of holder must keep their own dedicated
    connection, and both would drain this pool if they used it:

      * **A run.** It takes a SESSION-level advisory lease that must live and die
        with one physical connection (`repo.claim_lease`). A pooled connection is
        returned and reused, which would either release the lease early or leak
        it onto whoever borrows it next.
      * **An SSE stream.** It holds its connection for the length of the stream,
        which is the length of a research run -- tens of minutes. Two open run
        pages would hold two pool slots for the afternoon.
    """
    return ConnectionPool(
        dsn,
        min_size=1,
        max_size=max_size,
        kwargs={"autocommit": True, "row_factory": _row_factory},
        # Opened by the caller that owns its lifetime, so a failure to reach the
        # database surfaces there rather than from a constructor.
        open=False,
    )


@contextmanager
def from_pool(source: ConnectionPool, dsn: str = "") -> Iterator[Conn]:
    """Borrow a pooled connection, presented as the same `Conn` everything takes.

    The shim is what lets the pool drop in behind ~125 modules that know only
    `Conn`: repositories, services and routes cannot tell a borrowed connection
    from an owned one, and must not be able to.
    """
    with source.connection() as raw:
        yield Conn(raw, dsn)


def migrate(conn: Conn) -> list[str]:
    """Bring the database up to head. Idempotent.

    ALEMBIC OWNS THIS NOW, and this function is the seam that hid the change from
    its eight callers -- the API lifespan, the scheduler, `run_launcher`, and five
    `cli.py` commands all still call `db.migrate(conn)`.

    Why Alembic at all, given the file-based ledger worked: a downgrade path, and
    an ordering that is a graph rather than a lexical filename sort. Why the
    `.sql` files SURVIVE: they carry the reasoning, and `0001_baseline` executes
    them rather than restating them as `op.create_table` calls. Alembic versions
    the schema; it does not have to author it to do that.

    Returns the revisions applied: the lifespan logs it, and "nothing to do" is
    worth being able to see.
    """
    from alembic import command
    from alembic.config import Config

    # Read the ledger with plain SQL rather than through
    # `MigrationContext.configure`, which wants a SQLAlchemy Connection and would
    # mean building a second engine here purely to ask a one-column question.
    before = _current_revision(conn)

    root = Path(__file__).resolve().parents[3]
    cfg = Config(str(root / "alembic.ini"))
    # THE CONNECTION'S OWN DATABASE, not `get_settings()`. `env.py` falls back to
    # settings when this is absent -- which is right for `alembic` on the command
    # line -- but a programmatic `migrate(conn)` must act on the database it was
    # given, or every per-test database in the suite is migrated by migrating the
    # developer's.
    if conn.dsn:
        cfg.set_main_option("sqlalchemy.url", conn.dsn)
    # The script location is resolved against the repo root rather than the
    # current working directory: `venona migrate` is run from anywhere, and a
    # relative path in `alembic.ini` would resolve against the shell's cwd.
    cfg.set_main_option("script_location", str(Path(__file__).parent / "alembic"))
    # The process's logging is not alembic's to reconfigure: `env.py` skips
    # `fileConfig` on this flag, which would otherwise disable every existing
    # logger and put a WARNING-level root handler in front of the rest of the
    # process -- eight callers, the API lifespan and the 06:00 scheduler among
    # them, and every one of them went silent in production.
    cfg.attributes["configure_logging"] = False
    command.upgrade(cfg, "head")

    after = _current_revision(conn)
    return [] if before == after else [after or "head"]


def _current_revision(conn: Conn) -> str | None:
    """The applied revision, or None on a database Alembic has never touched."""
    exists = conn.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = 'alembic_version'"
    ).fetchone()
    if not exists:
        return None
    row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    return row["version_num"] if row else None


@contextmanager
def transaction(conn: Conn, *, what: str = "") -> Iterator[Conn]:
    """A transaction.

    HOW A TRANSACTION BEGINS BUYS NOTHING HERE. A Postgres transaction takes a
    snapshot at its first statement and holds it; there is no reader-to-writer
    upgrade step to lose, which is what SQLite's `BEGIN IMMEDIATE` was for. The
    hazard that remains is the lost update -- two transactions read the same
    value and both write it -- and that is fixed at the SITE, with `SELECT ...
    FOR UPDATE`, an atomic `UPDATE ... RETURNING`, or an advisory lock.
    `run_service.append_event` is the one to copy.

    NESTING IS REFUSED rather than flattened. A `with transaction` inside
    another one would issue a second `BEGIN`, which Postgres answers with a
    warning and ignores -- so the inner block's `COMMIT` ends the OUTER
    transaction, and every statement the outer block runs afterwards commits
    one at a time under autocommit. The failure is invisible: everything
    succeeds, and atomicity is simply gone. Use `savepoint` for a nested unit
    of work that is allowed to fail on its own.
    """
    if conn.in_transaction:
        raise RuntimeError(
            "db.transaction is already open on this connection -- a nested BEGIN would "
            "end the outer transaction at the inner COMMIT and silently drop its "
            "atomicity. Use db.savepoint for a nested unit of work."
        )
    started = time.monotonic()
    conn.execute("BEGIN")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
    finally:
        _warn_if_slow(started, what=what)


@contextmanager
def snapshot(conn: Conn) -> Iterator[Conn]:
    """One snapshot across many reads. The read-only counterpart to `transaction`.

    `transaction` above answers a different question, and it is right about the
    one it answers: how a transaction BEGINS buys nothing against a lost update.
    This is the other hazard. A reader that runs a dozen full-table selects at
    READ COMMITTED sees a dozen snapshots, so a write landing between two of
    them yields a document that refers to a row the reader never saw. For the
    export that renders as a link to a note the same export declined to write.

    READ ONLY as well as REPEATABLE READ, so a stray write inside is an error
    rather than a rollback somebody has to notice.

    ⚠️ NEVER ON A CONNECTION A RUN IS EMITTING ON. `run_service.append_event`
    opens its own `transaction`, and that one refuses to nest -- so a snapshot
    held on a run's connection turns every progress event, and then the stage
    row and the commit that follow it, into a `RuntimeError`. Open a second
    connection for the read and leave the run's own free to report.

    NO SLOW-TRANSACTION WARNING, unlike `transaction`. Holding this open for
    minutes is the design rather than a symptom: it takes no locks, and a
    reader cannot block a writer. What it does cost is holding back vacuum for
    as long as it lives, so scope it to the reads and close it.
    """
    if conn.in_transaction:
        raise RuntimeError(
            "db.snapshot cannot open inside another transaction -- a nested BEGIN would "
            "end the outer one at the inner COMMIT. If this is a run's connection, the "
            "read belongs on a second connection so `append_event` keeps working."
        )
    conn.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        # COMMIT rather than ROLLBACK: nothing was written either way, and a
        # rollback in the log reads as something having gone wrong.
        conn.execute("COMMIT")


@contextmanager
def savepoint(conn: Conn, name: str = "sp") -> Iterator[Conn]:
    """A nested unit of work that may fail without aborting its transaction.

    This is the only way to write "try the insert, skip it if it collides"
    inside a transaction on Postgres. A failed statement puts the whole
    transaction into the aborted state, where every following statement raises
    `InFailedSqlTransaction` -- so a bare `except IntegrityError: continue` in
    a loop does not skip one row, it destroys everything the loop had done and
    everything it was going to do, while reading like it handled the case.

    Outside a transaction this is a no-op rather than an error, because the
    statement is already its own unit of work under autocommit and the caller
    should not have to know which it is in.
    """
    if not conn.in_transaction:
        yield conn
        return
    # Not interpolated from anything a caller derives from data: every name in
    # the tree is a literal, and an identifier cannot be parameterised.
    if not name.isidentifier():
        raise ValueError(f"savepoint name {name!r} is not an identifier")
    conn.execute(f"SAVEPOINT {name}")
    try:
        yield conn
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {name}")
        raise
    else:
        conn.execute(f"RELEASE SAVEPOINT {name}")


# A write transaction on a REQUEST or STAGE path does microseconds of work: the
# largest is twelve statements and none of them awaits. So elapsed time above this
# threshold is usually all waiting on another transaction, and the block that
# reports it is the one holding the database up.
#
# The exception, which is real and fires: a bulk load. `venona anchors load` puts
# 264 entities, 663 aliases and 329 assertions through one transaction and takes
# ~0.6s doing honest work. The warning says so rather than asserting lock wait,
# because a diagnostic that is confidently wrong once is not trusted again.
SLOW_TRANSACTION_S = 0.5


def _warn_if_slow(started: float, *, what: str) -> None:
    elapsed = time.monotonic() - started
    if elapsed < SLOW_TRANSACTION_S:
        return
    # Resolved LAZILY, on the slow path only. The caller's frame is still on the
    # stack here, which is what lets the warning name a SITE rather than name
    # this helper.
    label = what
    if not label:
        frame = sys._getframe(2)
        label = f"{Path(frame.f_code.co_filename).name}:{frame.f_lineno}"
    log.warning(
        "slow postgres transaction: %s took %.2fs -- usually lock wait, so look "
        "first for another transaction holding a conflicting lock; a bulk load "
        "(anchors, seed) legitimately takes this long on its own.",
        label,
        elapsed,
    )


def insert(conn: Conn, table: str, row: dict[str, Any]) -> None:
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(row.values()))


def upsert(
    conn: Conn,
    table: str,
    row: dict[str, Any],
    *,
    key: Iterable[str],
    accumulate: Iterable[str] = (),
) -> None:
    """Insert, or update the columns given.

    `accumulate` names columns that may only RISE: the conflict arm takes the
    greater of what is stored and what is offered. Use it for a count the
    caller derived from a read, where plain assignment is a lost update -- a
    second writer that read the same value first would otherwise revert
    evidence the first one recorded.
    """
    keys = list(key)
    rising = set(accumulate)
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    updates = ", ".join(
        (
            f"{c} = GREATEST(COALESCE({table}.{c}, 0), excluded.{c})"
            if c in rising
            else f"{c} = excluded.{c}"
        )
        for c in row
        if c not in keys
    )
    conflict = ", ".join(keys)
    sql = f"INSERT INTO {table} ({cols}) VALUES ({marks}) ON CONFLICT({conflict}) DO "
    sql += f"UPDATE SET {updates}" if updates else "NOTHING"
    conn.execute(sql, tuple(row.values()))


def jload(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def jdump(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
