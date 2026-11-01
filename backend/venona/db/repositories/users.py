"""Every statement that touches `users`.

A repository answers "what does the database say"; `services/user_service.py`
answers "what does that mean". The service is the door every role and status
change goes through, and the place that degrades an unknown role to reader and
refuses to store one it does not recognise; collapsing the two would put policy
in a place with no name for it. The entitlement writes are the exception (ADR
0239): they record what the hub already decided, so they are called from here
directly by its push (`api/routers/mgmt.py`, `api/routers/auth.py`) and by
`venona users comp`.

STATEMENTS ARE BUILT FROM `db/tables.py`, not from strings. `users.c.session_epoch`
is checked when the module imports; `"session_epoch"` inside a SQL string is
checked when a user signs in, which is what the three `conn.execute` writes below
settle for.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy import insert as core_insert
from sqlalchemy.dialects.postgresql import insert

from venona.api import authz
from venona.db import core as db
from venona.db.tables import user_events, users

#: The columns the auth boundary reads on EVERY request. Named rather than
#: `select(users)` so a column added to the table does not silently widen what is
#: pulled across on the hot path -- and so that adding one to this list is a
#: visible decision.
_AUTH_COLUMNS = (
    users.c.id,
    users.c.email,
    users.c.role,
    users.c.status,
    users.c.display_name,
    users.c.session_epoch,
    # The hub's entitlement projection. On the hot path deliberately: the
    # middleware decides the paywall from the same row it already reads for
    # role and status, so a subscription that ended an hour ago narrows the
    # archive now rather than when a fortnight-old cookie expires.
    users.c.entitlement_status,
    users.c.entitled_through,
    users.c.billing_notice,
    users.c.billing_notice_at,
)


def _run(conn: db.Conn, statement: Any) -> Any:
    """Compile a Core statement and run it through the existing `Conn`.

    THE BRIDGE, and it is temporary. `Conn` wraps a raw psycopg connection, so a
    Core statement has to be compiled to SQL and parameters before it can go
    through it. When `Conn` is retired in favour of a SQLAlchemy connection this
    function is what disappears and every caller below is unchanged, which is the
    point of putting it here rather than at each call site.

    Values stay bound and are passed separately: rendering them INTO the SQL
    would be string interpolation with extra steps, which is the thing this
    package exists to stop.
    """
    # `render_postcompile` is what makes `IN (...)` work. Without it an
    # `in_()` compiles to a single `__[POSTCOMPILE_param]` placeholder that
    # SQLAlchemy normally expands at EXECUTION time -- and execution here is
    # psycopg, which has never heard of it and reports `column "__" does not
    # exist`. It expands to one bound parameter per value, not to literals, so
    # the docstring's argument about never rendering values into the SQL holds.
    compiled = statement.compile(dialect=_DIALECT, compile_kwargs={"render_postcompile": True})
    # `Conn.execute` translates `?` into psycopg's paramstyle
    # (`db/core.py::_convert`); the raw connection is called so that translation
    # is bypassed. A compiled Core statement carries pyformat placeholders
    # already and never a `?`.
    cursor = conn.raw.execute(str(compiled), compiled.params or None)
    return cursor


def _dialect():
    from sqlalchemy.dialects import postgresql

    return postgresql.dialect(paramstyle="pyformat")


_DIALECT = _dialect()


#: The advisory-lock namespace every AUTHORITY change serialises on, and its one
#: key. Two-argument form with a namespace of its own, deliberately:
#: `run_service.append_event` takes the ONE-argument
#: `pg_advisory_xact_lock(hashtext(run_id))`, whose key space is every 32-bit
#: value `hashtext` can produce -- so a single-argument constant here could
#: collide with some run id and make two unrelated writers wait on each other. A
#: namespace nothing else uses cannot.
AUTHORITY_NAMESPACE = 0x56454E41  # "VENA"
AUTHORITY_KEY = 1


def lock_authority(conn: db.Conn) -> None:
    """Serialise every role and status change on the instance, cluster-wide.

    WHY A LOCK AND NOT A GUARDED UPDATE. The rule being enforced is "never demote
    or suspend the last active admin", which is a read-then-write: count the
    admins, then change one. At READ COMMITTED two transactions demoting the last
    TWO admins each see the other still active, both pass, both commit, and the
    instance has no administrator and no way to get one back short of the CLI.
    A `WHERE EXISTS (another admin)` guard does not fix it -- the guard runs
    inside each transaction's own snapshot, which is exactly the problem. This is
    the case `db/core.py::transaction` says cannot be fixed by how a transaction
    BEGINS and has to be fixed at the site.

    ONE KEY FOR ALL OF THEM, not one per user, because the invariant is about the
    SET of admins rather than about any row -- two changes to two different users
    are precisely the pair that races. The cost is that authority changes
    serialise instance-wide, which for a handful of writes a month is nothing.

    `pg_advisory_xact_lock` releases at COMMIT or ROLLBACK, so there is no unlock
    to leak; `run_service.append_event` takes it the same way and for the same
    reason. Cluster-wide, so it covers `venona users` in another process racing
    the admin page, which is a real pair.
    """
    conn.execute("SELECT pg_advisory_xact_lock(?, ?)", (AUTHORITY_NAMESPACE, AUTHORITY_KEY))


def active_admin_count(conn: db.Conn, *, excluding: str | None = None) -> int:
    """How many admins could still administer this instance, ignoring one row.

    `excluding` is the user about to be changed, so the question is "would anyone
    be left". Counts ROWS, which means an env allow-list admin with no row is
    invisible here -- correct in production, where `ensure_from_env` gives every
    listed address a row at boot, and conservative everywhere else: the failure
    is refusing a demotion that was safe, not permitting one that was not.
    """
    # Labelled, because an unlabelled `func.count()` compiles to `count_1` and
    # `Row` is keyed by the name the server sent back.
    statement = (
        select(func.count().label("n"))
        .select_from(users)
        .where(users.c.role == "admin", users.c.status == "active")
    )
    if excluding is not None:
        statement = statement.where(users.c.id != excluding)
    return int(_run(conn, statement).fetchone()["n"])


def population(conn: db.Conn) -> dict[str, int]:
    """How many people, and how many of them are in each state worth reporting.

    Three counters from one scan, rather than every row shipped to Python to be
    counted there: the overview asks for numbers and nothing on that page reads
    a user's other columns.
    """
    statement = select(
        func.count().label("total"),
        func.count().filter(users.c.status != "active").label("suspended"),
        # Empty string as well as NULL: the column is free-form text, and a
        # blank stamp means the same thing to a reader as a missing one.
        func.count()
        .filter(or_(users.c.last_seen_at.is_(None), users.c.last_seen_at == ""))
        .label("never_signed_in"),
    ).select_from(users)
    row = _run(conn, statement).fetchone()
    return {
        "total": int(row["total"]),
        "suspended": int(row["suspended"]),
        "never_signed_in": int(row["never_signed_in"]),
    }


def roles(conn: db.Conn) -> dict[str, int]:
    """Active people per role, for the management view's two counters."""
    statement = (
        select(users.c.role, func.count().label("n"))
        .select_from(users)
        .where(users.c.status == "active")
        .group_by(users.c.role)
    )
    return {row["role"]: int(row["n"]) for row in _run(conn, statement).fetchall()}


def insert_user_event(
    conn: db.Conn,
    *,
    event_id: str,
    user_id: str,
    actor_id: str | None,
    action: str,
    detail: str | None,
    now: str,
) -> None:
    """Append-only. There is no update and there must not be one."""
    _run(
        conn,
        core_insert(user_events).values(
            id=event_id,
            user_id=user_id,
            actor_id=actor_id,
            action=action,
            detail=detail,
            created_at=now,
        ),
    )


#: An event with both addresses resolved. LEFT JOIN on the actor and INNER on the
#: subject: `actor_id` is NULL for every change the CLI made, so an inner join
#: there would hide exactly the rows whose provenance is most worth reading,
#: while `user_id` is NOT NULL and a missing one would be a broken foreign key.
#: Joined in the query rather than looked up per row for the reason
#: `curation.py::CONFLICT_SELECT` gives -- a `users` read per event is
#: `glossary.py`'s 201-queries-for-200-rows again (C1).
def recent_events(
    conn: db.Conn, *, limit: int = 50, offset: int = 0, user_id: str | None = None
) -> list[db.Row]:
    subject = users.alias("subject")
    actor = users.alias("actor")
    statement = (
        select(
            user_events.c.id,
            user_events.c.user_id,
            user_events.c.action,
            user_events.c.detail,
            user_events.c.created_at,
            subject.c.email.label("user_email"),
            actor.c.email.label("actor_email"),
        )
        .select_from(
            user_events.join(subject, subject.c.id == user_events.c.user_id).outerjoin(
                actor, actor.c.id == user_events.c.actor_id
            )
        )
        # `id` as the tiebreak, not decoration. Timestamps here are ISO-8601 to
        # the second and two changes made in one action share one -- suspending
        # somebody and recording it, or a script moving several people at once --
        # so ordering by time alone leaves those rows in whatever order the
        # planner returns them, and "when did I turn this off" gets a different
        # answer on different reads. The ids are uuid7, so they sort by creation.
        .order_by(user_events.c.created_at.desc(), user_events.c.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if user_id is not None:
        statement = statement.where(user_events.c.user_id == user_id)
    return list(_run(conn, statement).fetchall())


def event_count(conn: db.Conn, *, user_id: str | None = None) -> int:
    """How many audit rows match, so a window can say what it is a window of."""
    statement = select(func.count().label("n")).select_from(user_events)
    if user_id is not None:
        statement = statement.where(user_events.c.user_id == user_id)
    return int(_run(conn, statement).fetchone()["n"])


def by_email(conn: db.Conn, email: str) -> db.Row | None:
    """Lowercased on the way in, because that is how the column is written."""
    return _run(
        conn, select(*_AUTH_COLUMNS).where(users.c.email == email.strip().lower())
    ).fetchone()


def by_id(conn: db.Conn, user_id: str) -> db.Row | None:
    return _run(conn, select(*_AUTH_COLUMNS).where(users.c.id == user_id)).fetchone()


def profile(conn: db.Conn, user_id: str) -> db.Row | None:
    """Everything the account page shows about one person.

    SEPARATE FROM `_AUTH_COLUMNS` ON PURPOSE. That tuple is read on every
    authenticated request; `created_at` and `oidc_issuer` are read by the account
    page and the admin screens, a few times a session. Widening the hot path to
    serve them would be paying per request for a per-session answer.
    """
    statement = select(
        users.c.id,
        users.c.email,
        users.c.role,
        users.c.status,
        users.c.display_name,
        users.c.picture_url,
        users.c.created_at,
        users.c.last_seen_at,
        users.c.oidc_issuer,
        # The projection, because `admin.py::_row_to_user` reads it from BOTH
        # this row and `listing`'s. Missing here it falls open to `none`, which
        # the admin guide defines as "never subscribed" -- so every role change,
        # suspend and reinstate answered with a paying subscriber reported as
        # not started, and the row flipped back on the next refresh.
        users.c.entitlement_status,
        users.c.entitled_through,
    ).where(users.c.id == user_id)
    return _run(conn, statement).fetchone()


def set_display_name(conn: db.Conn, *, user_id: str, display_name: str | None) -> Any:
    """A person renaming themselves.

    Distinct from `record_oidc_identity`, which writes the name only when the
    PROVIDER sent one, because the cases are opposites: a provider omitting the
    claim must not blank an established name, while a person clearing the field
    must. Returning the cursor lets the caller tell "no such user" from "wrote
    it", which matters because the subject here is never a path parameter -- it
    is the caller's own id, and a zero-row update means the principal had none.
    """
    return _run(conn, update(users).where(users.c.id == user_id).values(display_name=display_name))


def listing(conn: db.Conn, *, limit: int | None = None, offset: int = 0) -> list[db.Row]:
    """`limit=None` (the CLI, the overview's aggregates) reads everyone; the
    HTTP list passes the bound its route declares, with a `total` beside it."""
    statement = select(
        users.c.id,
        users.c.email,
        users.c.role,
        users.c.status,
        users.c.display_name,
        users.c.created_at,
        users.c.last_seen_at,
        # The subscription, so an operator can see who is in dunning without
        # opening the hub. At this readership a direct message recovers more
        # cards than any automation, and that is the honest reason it is here.
        users.c.entitlement_status,
        users.c.entitled_through,
    ).order_by(users.c.email)
    if limit is not None:
        statement = statement.limit(limit).offset(offset)
    return list(_run(conn, statement).fetchall())


def set_entitlement(
    conn: db.Conn,
    *,
    user_id: str,
    status: str,
    through: str | None,
    notice: str | None,
    notice_at: str | None,
) -> None:
    """Store what the hub says this reader has paid for.

    A whole-projection write rather than a patch: the hub computes all four
    together from one subscription, and applying them piecemeal is how a stale
    notice outlives the status that explained it.

    NEVER OVERWRITES `comped`, for the reason `set_comped` gives -- including
    the notice, because a reader who pays nothing has no card to warn about.
    """
    conn.execute(
        "UPDATE users SET entitlement_status = ?, entitled_through = ?,"
        " billing_notice = ?, billing_notice_at = ? WHERE id = ?"
        " AND entitlement_status <> 'comped'",
        (status, through, notice, notice_at, user_id),
    )


def set_comped(conn: db.Conn, *, user_id: str, comped: bool) -> None:
    """Grant or withdraw complimentary access: the one LOCAL entitlement write.

    `comped` is this instance's word and the hub has no column for it. The hub
    bills, and a reader it has never sold anything to reads `none` there -- so
    the projection cannot carry a comp, and both projection writers above refuse
    to overwrite one. Without that refusal the backfill in
    `migrations/0023_entitlement.sql`, which exists so a deploy window cannot
    lock out the whole readership, is undone by each reader's first sign-in.

    Withdrawing writes `none` rather than guessing at Stripe. Their next sign-in
    carries the hub's real answer, which is the same path that corrects anyone
    else.
    """
    conn.execute(
        "UPDATE users SET entitlement_status = ?, entitled_through = NULL,"
        " billing_notice = NULL, billing_notice_at = NULL WHERE id = ?",
        ("comped" if comped else "none", user_id),
    )


def recently_seen(
    conn: db.Conn, *, since: str, now: str | None = None, entitled_only: bool = False
) -> list[db.Row]:
    """Active, ENTITLED accounts seen since `since` -- the personal-brief warm
    pass's population. Suspended accounts are excluded: warming a brief nobody
    can sign in to read is pure spend.

    A lapsed reader is the same case and worse. They CAN still sign in, to read
    the archive they paid for, and every visit refreshes `last_seen_at` -- so
    without this predicate a churned account stays in the warm pass for as long
    as they keep looking at what they already have, at the roughly $0.40 a day
    per reader ADR 0211 costed. This is the line that stops the spend.

    `now` is the LOCAL BRAKE, and it is why this takes a second date. The status
    column is a projection pushed by the hub; if the hub is down, or a push is
    stuck, an expired trial keeps reading `trialing` here forever and the fanout
    never stops. `entitled_through` is a date this instance holds, so comparing
    it locally means a stalled projection degrades to "stop spending" rather
    than "spend indefinitely". Passed in rather than read: a repository never
    asks what time it is.
    """
    predicates = [users.c.status == "active", users.c.last_seen_at >= since]
    if entitled_only:
        predicates.append(_entitled_predicate(now))
    statement = select(users.c.id).where(*predicates).order_by(users.c.id)
    return list(_run(conn, statement).fetchall())


def _entitled_predicate(now: str | None):
    """Entitled, and not merely PROJECTED as entitled: the local brake
    `recently_seen` describes, written as a column predicate."""
    # ADMINS ARE ALWAYS ENTITLED, matching `UserRecord.entitled` -- and it has to
    # be said again in SQL, because a column predicate cannot see a role. An
    # administrator whose row says `none` (the default in
    # `migrations/0023_entitlement.sql`, which backfilled only the rows that
    # existed) would otherwise be dropped from the warm pass and from brief
    # delivery on their own instance, which is how the operator becomes the
    # first person to lose the product.
    entitled = (users.c.role == authz.ADMIN) | users.c.entitlement_status.in_(
        ("trialing", "active", "past_due", "comped")
    )
    if now is not None:
        # `comped` carries no end date, so it must not be caught by the brake.
        entitled = entitled & (
            (users.c.role == authz.ADMIN)
            | (users.c.entitlement_status == "comped")
            | users.c.entitled_through.is_(None)
            | (users.c.entitled_through >= now)
        )
    return entitled


def count_users(conn: db.Conn) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    return int(row["n"]) if row else 0


def upsert_by_email(
    conn: db.Conn,
    *,
    user_id: str,
    email: str,
    role: str,
    status: str,
    now: str,
    display_name: str | None = None,
) -> None:
    """Insert, or leave an existing row alone.

    `DO NOTHING` rather than `DO UPDATE`, and the difference matters: this backs
    `ensure_from_env`, which runs on EVERY boot from the allow-list. An update
    would reset a role an operator had deliberately changed -- demoting an admin
    back to whatever the environment said -- once per restart, silently.
    """
    statement = (
        insert(users)
        .values(
            id=user_id,
            email=email.strip().lower(),
            role=role,
            status=status,
            display_name=display_name,
            created_at=now,
        )
        .on_conflict_do_nothing(index_elements=[users.c.email])
    )
    _run(conn, statement)


def set_role(conn: db.Conn, *, user_id: str, role: str) -> None:
    _run(conn, update(users).where(users.c.id == user_id).values(role=role))


def set_status(conn: db.Conn, *, user_id: str, status: str, epoch: str) -> None:
    _run(
        conn,
        update(users).where(users.c.id == user_id).values(status=status, session_epoch=epoch),
    )


def set_session_epoch(conn: db.Conn, *, user_id: str, epoch: str) -> None:
    _run(conn, update(users).where(users.c.id == user_id).values(session_epoch=epoch))


def touch_daily(conn: db.Conn, *, user_id: str, now: str, today: str) -> int:
    """Stamp `last_seen_at`, at most once per user per day. Returns rows written.

    THE DATE GUARD IS IN THE `WHERE`, not in a read the caller does first. That
    makes this a blind write with nothing to be consistent with, so it needs no
    transaction and cannot lose an update at READ COMMITTED: two concurrent
    probes both match or both miss, and either way the column ends up with a
    stamp from today. A `SELECT` then an `UPDATE` would be the read-then-write
    shape CLAUDE.md warns about, for no gain.

    `last_seen_at < today` compares an ISO-8601 timestamp against a bare
    `YYYY-MM-DD`, which is exactly the lexicographic comparison the format
    exists to make safe: every stamp from today sorts at or after the date, and
    every earlier one sorts before it.

    WHAT READS THIS. `recently_seen` is the personal-brief warm population, and
    it means "used Venona recently" only if something stamps on use. The sign-in
    callback alone cannot answer that -- a long-lived session reads for weeks
    without a second callback, and with auth off there is no callback at all.
    """
    cursor = _run(
        conn,
        update(users)
        .where(
            users.c.id == user_id,
            or_(users.c.last_seen_at.is_(None), users.c.last_seen_at < today),
        )
        .values(last_seen_at=now),
    )
    return int(cursor.rowcount or 0)


def record_oidc_identity(
    conn: db.Conn,
    *,
    user_id: str,
    issuer: str,
    subject: str,
    display_name: str | None,
    now: str,
    picture: str | None = None,
) -> None:
    """`display_name` is only written when the provider sent one.

    One statement built from a dict rather than two: a provider that omits the
    claim must not blank a name an earlier login established, and "omit the key"
    expresses that more clearly than "run the other query" does. `picture` gets
    the same omit-when-absent treatment with the opposite intent: the newest wins
    whenever the provider sends it, because avatars are the provider's to
    keep current.
    """
    values: dict[str, Any] = {
        "oidc_issuer": issuer,
        "oidc_subject": subject,
        "last_seen_at": now,
    }
    if display_name:
        values["display_name"] = display_name
    if picture:
        values["picture_url"] = picture
    _run(conn, update(users).where(users.c.id == user_id).values(**values))
