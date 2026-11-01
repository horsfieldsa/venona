"""Every statement that touches `access_requests`.

WHY THIS MODULE EXISTS. The table's statements were in two repositories, neither
of which is about access: the public INSERT sat in `curation.py` (because the
router that writes it had no other query) and the CLI's read and decide sat in
`cli.py`. "One module per aggregate, named after the table it is mostly about" is
the rule in `db/repositories/__init__.py`, and this table now has enough callers
-- a public form, a CLI, and an admin queue -- for the split to be a real cost
rather than a tidiness point.

WHAT THE PROJECTION GAINS. `id`. The CLI's read never returned it, because the
CLI addresses a request by EMAIL: `venona access decide someone@example.com`.
An HTTP queue cannot do that. The address is caller-supplied, several pending
rows for one address are ordinary (the form permits duplicates by design), and a
route that took an email would let a caller name a row it had never read. Every
decision route below takes an `id` for that reason.

THE PROPERTY THIS TABLE CARRIES, and every function here preserves it:
`0001_baseline.sql` says "Granting access is NOT a row in this table. Nothing in
the request path can grant anything." `insert_access_request` writes `'pending'`
as a SQL literal so the one column that could express a grant is unreachable from
the request body; `decided_by` and `user_id` are written only by the admin
decision path, on a different route with a different credential.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, update

from venona.db import core as db
from venona.db.tables import access_requests, users

PENDING = "pending"
APPROVED = "approved"
DECLINED = "declined"


def _run(conn: db.Conn, statement: Any) -> Any:
    """Compile a Core statement and run it through the existing `Conn`.

    The same bridge `users.py` carries, and it disappears with `Conn` for the
    same reason. See that module's `_run` for why the parameters go separately.
    """
    compiled = statement.compile(dialect=_DIALECT)
    return conn.raw.execute(str(compiled), compiled.params or None)


def _dialect():
    from sqlalchemy.dialects import postgresql

    return postgresql.dialect(paramstyle="pyformat")


_DIALECT = _dialect()


def insert_access_request(
    conn: db.Conn,
    *,
    request_id: str,
    email: str,
    coverage: str | None,
    role: str,
    now: str,
) -> None:
    """Status is `'pending'`, written as a literal and not a parameter.

    Nothing in this path may grant anything -- that is the property that makes an
    unauthenticated write safe -- so the one column that could express a grant is
    not reachable from the request body. `decided_by` and `user_id` are likewise
    absent here rather than defaulted: this statement is the whole of what a
    stranger can cause to be written.
    """
    _run(
        conn,
        access_requests.insert().values(
            id=request_id,
            email=email,
            coverage=coverage,
            role=role,
            status=PENDING,
            created_at=now,
        ),
    )


#: A request with both joined addresses resolved. LEFT JOIN on each: `decided_by`
#: is NULL for every pending row and for every verdict the CLI reached, and
#: `user_id` is NULL for pending, for declined, and for every row decided before
#: migration 0005. An inner join anywhere here would hide the row rather than the
#: missing name -- the same trap `curation.py::CONFLICT_SELECT` records.
def _listing_columns():
    decider = users.alias("decider")
    account = users.alias("account")
    return (
        decider,
        account,
        (
            select(
                access_requests.c.id,
                access_requests.c.email,
                access_requests.c.coverage,
                access_requests.c.role,
                access_requests.c.status,
                access_requests.c.created_at,
                access_requests.c.decided_at,
                access_requests.c.note,
                decider.c.email.label("decided_by_email"),
                account.c.email.label("account_email"),
                account.c.status.label("account_status"),
            ).select_from(
                access_requests.outerjoin(
                    decider, decider.c.id == access_requests.c.decided_by
                ).outerjoin(account, account.c.id == access_requests.c.user_id)
            )
        ),
    )


def listing(conn: db.Conn, *, pending_only: bool, limit: int, offset: int = 0) -> list[db.Row]:
    """The queue, newest first.

    `coverage` comes back whole rather than truncated: it is the only thing
    distinguishing a real analyst from a scraper, and a decision made against the
    first forty characters of it is a decision made against nothing.
    """
    _decider, _account, statement = _listing_columns()
    if pending_only:
        statement = statement.where(access_requests.c.status == PENDING)
    # `id` breaks the tie: `created_at` is ISO-8601 to the second and a burst of
    # requests shares one, so a window over time alone disagrees with itself
    # about which rows page two holds.
    statement = (
        statement.order_by(access_requests.c.created_at.desc(), access_requests.c.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(_run(conn, statement).fetchall())


def listing_count(conn: db.Conn, *, pending_only: bool) -> int:
    """The whole set behind a window, under the same filter."""
    from sqlalchemy import func

    statement = select(func.count().label("n")).select_from(access_requests)
    if pending_only:
        statement = statement.where(access_requests.c.status == PENDING)
    return int(_run(conn, statement).fetchone()["n"])


def by_id(conn: db.Conn, request_id: str) -> db.Row | None:
    _decider, _account, statement = _listing_columns()
    return _run(conn, statement.where(access_requests.c.id == request_id)).fetchone()


def lock_for_decision(conn: db.Conn, request_id: str) -> db.Row | None:
    """The row, locked for update, or None.

    `SELECT ... FOR UPDATE` because deciding is a read-then-write -- is it still
    pending, and only then change it -- and at READ COMMITTED two admins clicking
    Approve on the same request would both read `pending` and both proceed. The
    C12 OAuth addendum uses the same primitive to make an authorization code
    single-use, for the same reason.

    NOT SUFFICIENT ON ITS OWN, and the approve path must not treat it as such:
    this locks one REQUEST, and the resource two approvals actually contend for
    is the ADDRESS. Several pending rows for one address are ordinary, and two
    admins approving two of them lock different rows. `users.lock_authority` is
    what covers that.
    """
    return _run(
        conn,
        select(
            access_requests.c.id,
            access_requests.c.email,
            access_requests.c.status,
            access_requests.c.role,
            access_requests.c.user_id,
        )
        .where(access_requests.c.id == request_id)
        .with_for_update(),
    ).fetchone()


def settle(
    conn: db.Conn,
    *,
    request_id: str,
    status: str,
    now: str,
    note: str | None,
    actor: str | None,
    user_id: str | None,
) -> int:
    """Record a decision on ONE still-pending request. Returns rows changed.

    `AND status = 'pending'` is what makes the return value mean "there was a
    decision to make", the same way `curate.py::settle_open_conflict`'s
    `AND resolved = 0` does -- so a caller that loses a race writes nothing and
    can tell.
    """
    statement = (
        update(access_requests)
        .where(access_requests.c.id == request_id, access_requests.c.status == PENDING)
        .values(status=status, decided_at=now, note=note, decided_by=actor, user_id=user_id)
    )
    return int(_run(conn, statement).rowcount)


def settle_by_email(
    conn: db.Conn, *, email: str, status: str, now: str, note: str | None, actor: str | None
) -> int:
    """Every pending request for one address. Returns how many. THE CLI'S PATH.

    Kept beside the id-keyed `settle` rather than replacing it, because the two
    callers genuinely differ. `venona access decide someone@example.com` names an
    address, has always closed all of that address's pending rows at once, and is
    typed by someone reading the queue in a terminal. An HTTP route cannot work
    that way: the address is caller-supplied and several pending rows for one
    address are ordinary, so a route keyed on email would let a caller decide a
    row it had never read.

    Writes no `user_id`: the CLI records a decision and creates no account. That
    separation is the point -- `venona users add` is the other half.
    """
    statement = (
        update(access_requests)
        .where(
            access_requests.c.email == email.strip().lower(),
            access_requests.c.status == PENDING,
        )
        .values(status=status, decided_at=now, note=note, decided_by=actor)
    )
    return int(_run(conn, statement).rowcount)


def decline_siblings(
    conn: db.Conn, *, email: str, keeping: str, now: str, note: str, actor: str | None
) -> int:
    """Close the other pending rows for one address. Returns how many.

    The form is public and rate limited rather than deduplicated -- a person who
    mistypes their address asks twice, and `access_requests.email` carries no
    unique constraint by design. Approving one row and leaving the rest pending
    would leave the queue permanently showing people who already have accounts,
    which is how a queue stops being read.

    Declined rather than deleted, and carrying a note naming the request that was
    approved, for the reason revocation is a timestamp everywhere else in this
    schema: "what happened to this" is worth being able to answer.
    """
    statement = (
        update(access_requests)
        .where(
            access_requests.c.email == email.strip().lower(),
            access_requests.c.status == PENDING,
            access_requests.c.id != keeping,
        )
        .values(status=DECLINED, decided_at=now, note=note, decided_by=actor)
    )
    return int(_run(conn, statement).rowcount)


def pending_count(conn: db.Conn) -> int:
    """For the dashboard. One number, and the index already covers it."""
    from sqlalchemy import func

    return int(
        _run(
            conn,
            select(func.count().label("n"))
            .select_from(access_requests)
            .where(access_requests.c.status == PENDING),
        ).fetchone()["n"]
    )
