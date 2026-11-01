"""Users: the only write path for the `users` table.

`config_service` is the only write path for editable config documents and
`run_launcher` is the only place a lane slot is claimed, for the same reason this
exists: the rule that makes a boundary real is that there is exactly one door.

WHAT A USER IS FOR. Two things, and only two:

  1. `role` — admin or reader, resolved on every request by `api/authz.py`.
  2. `status` + `session_epoch` — revocation, checked on every request.

Everything else about a person (what they cover, why they were let in) lives in
`access_requests`, which is deliberately NOT this table: nothing in the
request-access path can grant anything, and approval is a separate admin action.
That separation is worth keeping — it is what makes the public form safe.

THE ALLOW-LIST MIGRATION. Before this, membership was a frozenset parsed out of
`VENONA_AUTH_ALLOWED_EMAILS` and re-read per request, and the property that
mattered was that removing an address ended its sessions immediately rather than
waiting out a fourteen-day cookie. `ensure_from_env` carries the addresses over
on first boot; `lookup` preserves the property, because it is called on every
request and a `suspended` row fails the same way a removed address did.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

import psycopg

from venona.api import authz
from venona.db import core as db
from venona.db.repositories import users as repo

ACTIVE = "active"
SUSPENDED = "suspended"

#: `{user_id: "YYYY-MM-DD"}` — who `touch_daily` has already stamped today, so
#: the common case costs no round trip. Process-local and unbounded by design:
#: it holds one short string per account that has signed in since boot, which is
#: the same population `users` itself holds, and a restart simply re-stamps.
_seen_today: dict[str, str] = {}


@dataclass(frozen=True)
class UserRecord:
    """What the auth boundary needs, and nothing else.

    Deliberately not the whole row. `api/security.py` holds no `venona.*` imports
    at all — which is what lets `tests/test_auth.py` exercise the entire boundary
    with no database — so this crosses that line as a plain value, injected the
    way `verify_mcp` already is.
    """

    id: str
    email: str
    role: str
    status: str
    session_epoch: float
    display_name: str | None = None
    # The hub's projection (migration 0023). Defaults are the SAFE end of each:
    # a record built without them -- the break-glass admin, a test -- reads
    # everything rather than nothing, because the credential that exists for
    # when all else has failed must not be the one the paywall refuses.
    entitlement_status: str = "comped"
    entitled_through: str | None = None
    billing_notice: str | None = None
    billing_notice_at: str | None = None

    @property
    def active(self) -> bool:
        return self.status == ACTIVE

    @property
    def entitled(self) -> bool:
        """Whether this reader may have the paid product.

        AN INSTANCE THAT TAKES NO MONEY ENTITLES EVERYBODY, and this is the
        first check rather than a detail. The column defaults to `none`, so
        every account created after migration 0023 on a non-charging instance --
        every local box, every self-hosted one -- reads as unentitled. Without
        this line their `archive_ceiling` excludes every brief and they get an
        empty product: no refusal, no error, just 200s with nothing in them.
        The migration backfilled the rows that existed; nothing could backfill
        the ones that had not been created yet.

        It also has to live HERE rather than at each gate. The route check, the
        warm pass and the notification fan-out were each given this answer
        separately and the ceiling was missed, which is exactly the drift one
        predicate prevents.

        Admins are always entitled: an admin operates the instance, and locking
        the operator out of the surface they came to fix is the worst possible
        moment for a paywall. `past_due` is entitled too -- Stripe is still
        retrying the card, and cutting somebody off at the first decline turns a
        recoverable lapse into a cancellation (ADR 0212).
        """
        if not authz.billing_enforced():
            return True
        if self.role == authz.ADMIN:
            return True
        return self.entitlement_status in ("trialing", "active", "past_due", "comped")

    #: A ceiling no real brief date can satisfy, for somebody who has never
    #: subscribed. `date <= ''` is false for every date Postgres holds, because
    #: the empty string sorts before all of them.
    #:
    #: NEEDED because "never started" and "lapsed" both fail `entitled` while
    #: meaning opposite things about the archive. A lapsed reader has an
    #: `entitled_through` and keeps everything up to it; somebody who never paid
    #: has a NULL one, and reading that as "no limit" would hand them the entire
    #: archive for free -- the brief routes are deliberately open at the route
    #: level, so this property is the only thing standing there.
    NOTHING: ClassVar[str] = ""

    @property
    def archive_ceiling(self) -> str | None:
        """The newest brief date this reader may open, or None for no limit.

        None is unrestricted: an entitled reader, an admin, anyone with no row.
        A lapsed reader keeps everything up to the day their subscription ended,
        forever (ADR 0211). Somebody who never subscribed gets `NOTHING`.
        """
        if self.entitled:
            return None
        return self.entitled_through or self.NOTHING

    @property
    def is_admin(self) -> bool:
        return self.role == authz.ADMIN


def _epoch_to_float(value: str | None) -> float:
    """`users.session_epoch` is TEXT holding a float, the shape `auth_state` uses.

    Stored as text for the reason every timestamp here is text, and parsed
    tolerantly: an unparseable epoch must not lock a user out of their own
    account, because the failure it would cause is indistinguishable from a
    revocation nobody performed.
    """
    if not value:
        return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def _to_record(row: db.Row) -> UserRecord:
    role = row["role"] if row["role"] in authz.ROLES else authz.READER
    return UserRecord(
        id=row["id"],
        email=row["email"],
        # A role outside `authz.ROLES` degrades to READER, never to ADMIN. A typo
        # in a column, or a role from a future version rolled back, must not be a
        # privilege escalation.
        role=role,
        status=row["status"],
        session_epoch=_epoch_to_float(row["session_epoch"]),
        display_name=row["display_name"],
        # `.get`, not `[...]`: `Row` is a dict subclass and these four arrive
        # with migration 0023, so a connection to a database mid-migration --
        # the API starts, the lifespan migrates -- would otherwise raise on the
        # first authenticated request. The fallback is `comped`, which reads
        # everything: an instance that cannot yet tell who is paying must not
        # refuse the readers it already has.
        #
        # That fallback also FAILS OPEN, so it hides a query that forgot to
        # select the column -- which is precisely what happened once, because
        # `_AUTH_COLUMNS` is a named list rather than `select(users)`. The guard
        # is `test_the_auth_columns_carry_the_entitlement`, not this line.
        entitlement_status=row.get("entitlement_status") or "comped",
        entitled_through=row.get("entitled_through"),
        billing_notice=row.get("billing_notice"),
        billing_notice_at=row.get("billing_notice_at"),
    )


def lookup(conn: db.Conn, email: str) -> UserRecord | None:
    """By address, lowercased. Called on EVERY authenticated request."""
    row = repo.by_email(conn, email)
    return _to_record(row) if row else None


def get(conn: db.Conn, user_id: str) -> UserRecord | None:
    row = repo.by_id(conn, user_id)
    return _to_record(row) if row else None


def listing(conn: db.Conn, *, limit: int | None = None, offset: int = 0) -> list[dict[str, Any]]:
    return [dict(r) for r in repo.listing(conn, limit=limit, offset=offset)]


def count(conn: db.Conn) -> int:
    return repo.count_users(conn)


#: `users.oidc_issuer` -> what to call it on screen. An issuer we do not
#: recognise renders as itself rather than as "Unknown": it is a URL the operator
#: configured, so showing it is more useful than hiding it, and a second provider
#: added later works with no change here.
PROVIDER_NAMES = {
    "accounts.google.com": "Google",
    "https://accounts.google.com": "Google",
}


def provider_name(issuer: str | None) -> str | None:
    """None means "has never completed an OIDC sign-in", which is honestly
    different from "signed in via something we cannot name"."""
    if not issuer:
        return None
    return PROVIDER_NAMES.get(issuer, issuer)


def profile(conn: db.Conn, user_id: str) -> dict[str, Any] | None:
    """The account page's row. Not on the authentication path."""
    row = repo.profile(conn, user_id)
    return dict(row) if row else None


def set_display_name(conn: db.Conn, *, user_id: str, display_name: str | None) -> bool:
    """A person renaming themselves. False when no such row.

    The caller passes its OWN principal id -- there is no user id in the route,
    which is what makes this safe to expose to a reader. False therefore means
    the principal had no row (a bearer, or the env break-glass admin), not that
    somebody addressed a stranger.
    """
    return bool(repo.set_display_name(conn, user_id=user_id, display_name=display_name).rowcount)


def create(
    conn: db.Conn,
    *,
    user_id: str,
    email: str,
    role: str,
    now: str,
    display_name: str | None = None,
    allow_admin: bool = False,
) -> UserRecord:
    """Add a user. The id is minted by the caller, from `ctx.ids` or `uuid7`.

    Refuses an unknown role rather than storing it: `_to_record` would silently
    degrade it to reader on read, and a role that means something different when
    written than when read is the kind of thing nobody finds until it matters.

    `allow_admin` IS THE CEILING, and it defaults to closed for the reason every
    other default in this codebase's authority layer does: a caller added next
    month that forgets it creates a reader, not an administrator. Only the CLI
    passes it, because C12's rule is that crossing into `admin` requires shell
    access. Provisioning a user is the OTHER half of `change_authority`'s ceiling
    -- a first hub login goes through here rather than through there, so a cap on
    one alone would be a cap on nothing (ADR 0168).
    """
    if role not in authz.ROLES:
        raise ValueError(f"unknown role {role!r}; expected one of {sorted(authz.ROLES)}")
    if role == authz.ADMIN and not allow_admin:
        raise AuthorityError(
            "an administrator cannot be created over HTTP -- that needs shell "
            "access on the instance (`venona users add --role admin`)"
        )
    repo.upsert_by_email(
        conn,
        user_id=user_id,
        email=email,
        role=role,
        status=ACTIVE,
        now=now,
        display_name=display_name,
    )
    record = lookup(conn, email)
    assert record is not None  # noqa: S101 - just written, in the same transaction
    return record


class AuthorityError(Exception):
    """A role or status change that must not happen.

    A distinct type rather than `ValueError` because the caller has to answer
    differently: a router turns this into a 409 with the sentence on screen, and
    the CLI prints it. `ValueError` here would be caught by the same `except` as
    "unknown role", which is a caller mistake rather than a refusal.
    """


#: `user_events.action` values. Not a CHECK constraint on the column, for the
#: reason `users.role` is not one -- the set is a product decision -- but named
#: here so the strings exist once and a typo is an ImportError.
ROLE_CHANGED = "role_changed"
SUSPENDED_EVENT = "suspended"
REINSTATED_EVENT = "reinstated"
CREATED = "created"


def change_authority(
    conn: db.Conn,
    *,
    user_id: str,
    role: str | None = None,
    status: str | None = None,
    actor: str | None,
    event_id: str,
    now: str,
    epoch: float,
    note: str | None = None,
    force: bool = False,
    allow_admin: bool = False,
) -> UserRecord:
    """Change what someone may do, and record who changed it. THE ONLY DOOR.

    Every rule that makes an HTTP authority surface safe lives here rather than
    in the router, because `venona users` reaches the same rules from another
    process and a check in a route is a check the CLI does not run.

    THE TRANSACTION IS THE CALLER'S, and it must be one -- `lock_authority` takes
    `pg_advisory_xact_lock`, which is released at COMMIT, so calling this outside
    a transaction would take a lock and drop it before the write.

    FIVE REFUSALS:

    0. **Nothing reached over a wire may grant `admin`.** C12: authority may be
       reduced without limit and extended only as far as `reader`; crossing into
       `admin` requires shell access. The ceiling belongs HERE and not on a
       request model, for this docstring's own second-paragraph reason -- a check
       in one route is a check the hub's login sync, `provision-user` and the CLI
       do not run. `allow_admin` defaults to closed, so a caller added next month
       is capped unless somebody types it; only `cli.py` passes it (ADR 0168,
       superseding 0141 in part).
    1. **The actor must still be an active admin.** Re-read INSIDE the lock, not
       trusted from the principal: `AuthMiddleware` resolved that before routing
       and this commits after, so an admin suspended in between would otherwise
       complete the change. `actor=None` skips it -- that is the CLI, whose
       credential is shell access rather than a row.
    2. **Nobody changes their own role or status.** Not because an admin cannot
       already do everything -- they can -- but because the two ways to get it
       wrong are demoting yourself and stranding the instance, and suspending
       yourself and being logged out mid-click.
    3. **Never the last active admin.** Checked against the TARGET, never the
       actor: "the actor is not the last admin" is the natural misreading and it
       fails open. `force` is for the CLI only, and exists so `venona users
       suspend` can still suspend a compromised SOLE admin -- the default shape
       of an instance, and the path that must work under pressure.
    4. **Only known roles.** An unknown one is refused rather than stored, since
       `_to_record` would silently degrade it to reader on the way back out.

    Returns the row as it stands, read back rather than assembled, so a caller
    cannot report a change the database declined to make.
    """
    if role is not None and role not in authz.ROLES:
        raise ValueError(f"unknown role {role!r}; expected one of {sorted(authz.ROLES)}")
    # BEFORE the lock and before every other refusal: this one is about what the
    # caller may ask for at all, not about the state of the row it names.
    if role == authz.ADMIN and not allow_admin:
        raise AuthorityError(
            "authority cannot be raised to administrator over HTTP -- that needs "
            "shell access on the instance (`venona users role <email> admin`)"
        )
    if status is not None and status not in (ACTIVE, SUSPENDED):
        raise ValueError(f"unknown status {status!r}")
    if role is None and status is None:
        raise ValueError("change_authority was asked to change nothing")

    repo.lock_authority(conn)

    if actor is not None:
        signer = get(conn, actor)
        if signer is None or not signer.active or not signer.is_admin:
            raise AuthorityError(
                "your own account is no longer an active administrator, so this "
                "change was not applied"
            )
        if actor == user_id:
            raise AuthorityError(
                "you cannot change your own role or status -- ask another "
                "administrator, or use `venona users` from a shell"
            )

    target = get(conn, user_id)
    if target is None:
        raise AuthorityError(f"no user with id {user_id!r}")

    # Would this leave nobody able to administer the instance? Only demotion and
    # suspension can, so a promotion or a reinstatement never asks.
    losing_admin = (
        target.is_admin
        and target.active
        and ((role is not None and role != authz.ADMIN) or status == SUSPENDED)
    )
    if losing_admin and not force:
        if repo.active_admin_count(conn, excluding=user_id) == 0:
            raise AuthorityError(
                f"{target.email} is the only active administrator -- promote "
                f"somebody else first, or use `venona users` from a shell"
            )

    action, detail = _describe(target, role=role, status=status)
    if note:
        # The operator's own words, kept beside the mechanical description rather
        # than instead of it: "why" is the half a log cannot reconstruct, and the
        # before-and-after is the half a person will not type.
        detail = f"{detail} — {note.strip()}"
    if role is not None:
        repo.set_role(conn, user_id=user_id, role=role)
    if status is not None:
        # Suspension bumps the session epoch in the same statement, so it takes
        # effect on their next request rather than when a fourteen-day cookie
        # happens to expire. Reinstating clears a stale revocation the same way.
        repo.set_status(conn, user_id=user_id, status=status, epoch=f"{epoch:.3f}")

    repo.insert_user_event(
        conn,
        event_id=event_id,
        user_id=user_id,
        actor_id=actor,
        action=action,
        detail=detail,
        now=now,
    )

    changed = get(conn, user_id)
    assert changed is not None  # noqa: S101 - just written, same transaction
    return changed


def _describe(target: UserRecord, *, role: str | None, status: str | None) -> tuple[str, str]:
    """What to call this change, and one line a person can read six weeks later.

    The detail carries the BEFORE and after, because "role_changed" on its own
    answers the least interesting half of the question.
    """
    parts: list[str] = []
    action = ROLE_CHANGED
    if role is not None:
        parts.append(f"role {target.role} -> {role}")
    if status is not None:
        parts.append(f"status {target.status} -> {status}")
        action = SUSPENDED_EVENT if status == SUSPENDED else REINSTATED_EVENT
    return action, "; ".join(parts)


def record_event(
    conn: db.Conn,
    *,
    event_id: str,
    user_id: str,
    actor: str | None,
    action: str,
    detail: str | None,
    now: str,
) -> None:
    """Append an event for something `change_authority` did not do.

    Creation is the case: a user coming into existence is an authority change in
    every sense that matters to a reader of the log, but it has none of the four
    refusals to run -- there is no prior row to protect and nothing to leave
    without an administrator.
    """
    repo.insert_user_event(
        conn,
        event_id=event_id,
        user_id=user_id,
        actor_id=actor,
        action=action,
        detail=detail,
        now=now,
    )


def events(
    conn: db.Conn, *, limit: int = 50, offset: int = 0, user_id: str | None = None
) -> list[dict[str, Any]]:
    return [dict(r) for r in repo.recent_events(conn, limit=limit, offset=offset, user_id=user_id)]


def set_role(conn: db.Conn, *, user_id: str, role: str) -> None:
    """Unaudited, and only for callers that have no actor and no invariant to
    protect -- which is now none of them. Kept because `ensure_from_env` and the
    tests predate `change_authority`; new callers want that."""
    if role not in authz.ROLES:
        raise ValueError(f"unknown role {role!r}; expected one of {sorted(authz.ROLES)}")
    repo.set_role(conn, user_id=user_id, role=role)


def set_status(conn: db.Conn, *, user_id: str, status: str, now: float) -> None:
    """Suspend or reinstate.

    A suspension also bumps the session epoch, so it takes effect on the next
    request rather than when a fourteen-day cookie happens to expire. Doing one
    without the other is the bug: `status` alone is checked per request too, so
    both are belt and braces — but a reinstated user should not still be carrying
    a revocation, and a suspended one must not depend on a single check.
    """
    repo.set_status(conn, user_id=user_id, status=status, epoch=f"{now:.3f}")


def bump_session_epoch(conn: db.Conn, *, user_id: str, now: float) -> None:
    """Log this ONE user out everywhere, which an instance-wide epoch cannot.

    That file held a single float for a single principal, so one person's logout
    ended every session on the instance. It still exists and still wins — it is
    the break-glass "log everyone out" — and `verify_session` takes the later of
    the two.
    """
    repo.set_session_epoch(conn, user_id=user_id, epoch=f"{now:.3f}")


def touch_daily(conn: db.Conn, *, user_id: str, now: str) -> None:
    """Record that this account used Venona today, at most once a day, never fatally.

    `token_service.touch` is the same shape and the reasoning transfers whole: a
    bare UPDATE outside `db.transaction`, because a blind write with no read to
    be consistent with does not need one and would take a write lock on the
    request path of a read endpoint.

    SWALLOWING MATTERS MORE HERE THAN IT DOES FOR TOKENS. The caller is
    `_session_info`, which backs the probe `web/src/main.ts` awaits before it
    mounts the app. An access-log write that raised would take the whole SPA
    down with it, so every database error here is dropped.

    The in-process date guard is what keeps this off the hot path: the write is
    attempted once per process per user per day, and every other probe returns
    without a round trip.
    """
    today = now[:10]
    if _seen_today.get(user_id) == today:
        return
    try:
        repo.touch_daily(conn, user_id=user_id, now=now, today=today)
        conn.commit()
    except psycopg.Error:
        # Deliberately NOT recorded in `_seen_today`: a failed write should be
        # retried by the next probe rather than suppressed for the rest of the
        # day by a cache entry the database never earned.
        return
    _seen_today[user_id] = today


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
    """Stamp `iss` + `sub` on first successful sign-in.

    That pair, not the email, is the identity: an address can be reassigned
    inside a tenant and a subject cannot. It is recorded rather than required,
    because a row can be created by an admin before its owner has ever signed in.

    `display_name` is only written when the provider sent one, so a provider that
    omits the claim cannot blank a name an earlier login established.
    """
    repo.record_oidc_identity(
        conn,
        user_id=user_id,
        issuer=issuer,
        subject=subject,
        display_name=display_name,
        now=now,
        picture=picture,
    )


def ensure_from_env(conn: db.Conn, *, emails: frozenset[str], now: str, ids) -> list[str]:
    """Carry `VENONA_AUTH_ALLOWED_EMAILS` into `users` on boot. Idempotent.

    THE FIRST ADDRESS BECOMES AN ADMIN, and the rest become admins too. That is
    deliberate and it is the conservative choice: every address on that list could
    already do everything, so promoting them all to `admin` changes nobody's
    privileges. Demoting one to `reader` would silently take away access somebody
    already had, which is a worse first impression of a new permission system than
    having to demote someone by hand.

    Runs in the lifespan, so it must never raise on a fresh database: an empty
    allow-list is legal (a Google-only instance with no addresses is refused by
    `assert_usable`, not here).
    """
    created: list[str] = []
    for email in sorted(emails):
        if lookup(conn, email) is not None:
            continue
        create(
            conn,
            user_id=f"user:{ids.uuid7()}" if ids else f"user:{email}",
            email=email,
            role=authz.ADMIN,
            now=now,
            # THE OTHER SHELL PATH, and the reason `allow_admin` is a parameter
            # rather than a check on "is this a request". C12 calls this list the
            # bootstrap and the break-glass: it is read from the process
            # environment, which is set by whoever deploys the instance, so it is
            # the same authority the CLI has and not a wire at all.
            #
            # Capping it would be a self-inflicted outage rather than a control.
            # This runs in the lifespan and is how an instance gets its FIRST
            # admin; refuse it and a fresh deployment has nobody who can
            # administer it, and no way to fix that from any surface (ADR 0168).
            allow_admin=True,
        )
        created.append(email)
    return created
