"""Agent tokens: the Postgres half of the MCP credential.

The stdlib half -- minting, hashing, the prefix test -- lives in
`api/security.py`, which holds no `venona.*` import at module level so that
`tests/test_auth.py` can exercise the whole auth boundary with no database. This
module is the other side of that seam and is the ONLY place `mcp_tokens` is
read or written, in the same spirit as `llm/executor.py` being the only place
`claude` is invoked.

What may cross the wire is decided by DIRECTION, not by operation: over HTTP
access can be taken away and never handed out (C12). So revocation, ownership
and both listings have routes (ADR 0167), while `mint` has none and is reached
only by the code exchange a person signs in for (C12's OAuth addendum).
`listing` is the CLI's unscoped read and answers no route at all.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import psycopg

from venona.api import authz
from venona.api.security import hash_token, new_mcp_token
from venona.db import core as db
from venona.db.repositories import token as repo

# `last_used_at` is telemetry, not accounting. One write per agent read would be
# real write amplification: a research run (forty minutes, measured 2026-08-17)
# is appending to `run_events` throughout, and an agent reading in a loop would
# add a row-level lock and a WAL record per tool call for a field nobody reads in
# real time. So a token's timestamp is refreshed at most this often, from an
# in-process guard.
TOUCH_INTERVAL_SECONDS = 60.0

_last_touch: dict[str, float] = {}


@dataclass(frozen=True)
class TokenRow:
    id: str
    label: str
    prefix: str
    created_at: str
    last_used_at: str | None
    revoked_at: str | None


@dataclass(frozen=True)
class OwnedTokenRow:
    """A token, plus the address of whoever owns it.

    SEPARATE FROM `TokenRow` rather than an optional field on it. `TokenRow` is
    what the mint returns and what the CLI prints, and widening it would make
    every caller carry a column only one read populates -- the same argument
    `users.profile` makes for not widening `_AUTH_COLUMNS`.

    `user_email` is None for a token whose `user_id` is NULL, which is every one
    minted before identity existed. That is a fact worth rendering, not a gap.

    `client_name` is the same kind of fact from the other direction: the name a
    client registered under, or None for a token typed in at a terminal. It says
    WHERE a connection came from, and it is attacker-supplied text --
    registration is unauthenticated -- so every render of it escapes.
    """

    id: str
    label: str
    prefix: str
    scope: str
    created_at: str
    last_used_at: str | None
    expires_at: str | None
    user_id: str | None
    user_email: str | None
    client_name: str | None


def _row(row: db.Row) -> TokenRow:
    return TokenRow(
        id=row["id"],
        label=row["label"],
        prefix=row["prefix"],
        created_at=row["created_at"],
        last_used_at=row["last_used_at"],
        revoked_at=row["revoked_at"],
    )


def mint(
    conn: db.Conn,
    *,
    label: str,
    token_id: str,
    now: str,
    user_id: str,
    scope: str = "mcp",
    expires_at: str | None = None,
) -> tuple[TokenRow, str]:
    """Create a token. Returns `(row, secret)` -- the secret exists ONCE.

    There is deliberately no `secret=` parameter. The whole argument for storing
    a bare SHA-256 rather than a KDF is that this value is 32 bytes of
    `secrets.token_hex` and therefore has no dictionary to attack; a
    caller-supplied secret would falsify that premise silently.
    `tests/test_mcp_tokens.py::test_mint_takes_no_caller_supplied_secret` pins it.
    """
    label = label.strip()
    if not label:
        raise ValueError(
            "a token needs a label -- it is the only thing that makes "
            "`venona mcp token ls` answerable six weeks later"
        )

    # A space-delimited list (RFC 6749 §3.3), and every name must be known here.
    # `authz.grantable` DROPS an unrecognised one, which is right for a client
    # asking, and would mint a credential narrower than the caller believes.
    granted = authz.grantable(scope)
    if not granted or len(granted) != len(set(scope.split())):
        raise ValueError(
            f"unknown scope {scope!r}; expected one or more of {sorted(authz.SCOPES)}. "
            f"A scope this does not recognise would mint a token with an EMPTY "
            f"reach set, which is admissible nowhere and looks like a broken "
            f"instance rather than a bad argument."
        )
    scope = " ".join(granted)

    secret = new_mcp_token()
    prefix = secret[: len("vmcp_") + 6]
    with db.transaction(conn, what="mint mcp token"):
        repo.insert_mcp_tokens(
            conn, token_id, label, hash_token(secret), prefix, now, user_id, scope, expires_at
        )
    row = repo.select_mcp_tokens_by_id(conn, token_id).fetchone()
    return _row(row), secret


def verify(conn: db.Conn, presented: str, *, now: str | None = None) -> db.Row | None:
    """The read path. Returns the token row, or None.

    A single indexed lookup on a UNIQUE column -- constant work, and no
    comparison loop for a timing side channel to live in. The partial index
    (`WHERE revoked_at IS NULL`) means a revoked token cannot be a hit at all
    rather than being filtered afterwards.

    RETURNS A ROW, not an id: the caller needs `user_id` to know whose role to
    apply and `scope` to know what the credential may reach. An id alone leaves
    `AuthMiddleware` with no owner to take a role from, and the nearest thing to
    a guess available to it is the alphabetically-first allow-listed address --
    every agent on the instance then acting as one arbitrary person.

    TWO REFUSALS HERE THAT ARE NOT REVOCATION, and both fail closed. A third
    lives in `AuthMiddleware._verify_mcp`, which refuses an inactive owner, so
    suspending somebody also unplugs their agents:

      * `user_id IS NULL` -- an ORPHAN, minted before identity existed. Guessing
        an owner would hand a pre-identity credential whatever role the guess
        had, so it is refused and the operator re-mints it.
      * `expires_at` in the past. Compared as TEXT, which is correct because
        every timestamp here is fixed-width ISO-8601 UTC; `now` is passed rather
        than read so the comparison is testable. `None` skips the check, which is
        every historical token and remains the default -- an expiry that
        surprises an agent mid-run is worse than none.
    """
    row = repo.select_mcp_tokens_by_token_hash(conn, hash_token(presented)).fetchone()
    if row is None or row["user_id"] is None:
        return None
    if now is not None and row["expires_at"] is not None and row["expires_at"] <= now:
        return None
    return row


def touch(conn: db.Conn, token_id: str, *, now: str) -> None:
    """Refresh `last_used_at`, at most once a minute, and never fatally.

    A bare UPDATE OUTSIDE `db.transaction`, deliberately: a transaction is for a
    read-then-write block (CLAUDE.md), and this is a blind write that would take
    a write lock on the request path of a read endpoint for nothing.

    Swallows `OperationalError` for the same reason `bump_epoch` swallows a
    failed epoch write: a lost telemetry write must never fail the request it is
    describing. `tests/test_mcp_tokens.py::test_touch_never_fails_a_request`.
    """
    monotonic = time.monotonic()
    last = _last_touch.get(token_id)
    if last is not None and monotonic - last < TOUCH_INTERVAL_SECONDS:
        return
    _last_touch[token_id] = monotonic
    try:
        repo.update_mcp_tokens_by_last_used_at_id(conn, now, token_id)
        conn.commit()
    except psycopg.OperationalError:
        pass


def listing(
    conn: db.Conn, *, include_revoked: bool = False, user_id: str | None = None
) -> list[TokenRow]:
    """`user_id` SCOPES the list to one owner. The one caller is the CLI.

    Unscoped is the default because `venona mcp token ls` is the operator's own
    tool and is expected to show the whole box. The parameter is here so that
    this can never quietly become the read behind a route: unscoped over HTTP is
    one reader learning the label, prefix and last-used time of another's
    credential -- not the secret, but enough to know who is connected with what.
    `connections` is the scoped HTTP read; `instance_listing` is the admin one.
    """
    return [_row(r) for r in repo.listing(conn, include_revoked=include_revoked, user_id=user_id)]


def _owned(row: db.Row) -> OwnedTokenRow:
    return OwnedTokenRow(
        id=row["id"],
        label=row["label"],
        prefix=row["prefix"],
        scope=row["scope"],
        created_at=row["created_at"],
        last_used_at=row["last_used_at"],
        expires_at=row["expires_at"],
        user_id=row["user_id"],
        user_email=row["user_email"],
        client_name=row["client_name"],
    )


def attach_to_client(conn: db.Conn, *, token_id: str, client_id: str) -> None:
    """Record which OAuth client a token was minted for.

    Here rather than in the route because every other write to `mcp_tokens`
    goes through this module, and a direct repository call from a router is
    how that stops being true one convenient statement at a time.
    """
    repo.attach_token_to_client(conn, token_id=token_id, client_id=client_id)


#: How many of one person's connections a single read returns. Nobody holds this
#: many agent credentials; it is a ceiling on an unbounded read, not a page.
CONNECTIONS_LIMIT = 200


def connections(
    conn: db.Conn, *, user_id: str, limit: int = CONNECTIONS_LIMIT
) -> list[OwnedTokenRow]:
    """One person's live connections, for `GET /api/meta/mcp`.

    SCOPED, ALWAYS, and the parameter is required rather than defaulted so the
    scoping cannot be forgotten at a call site. An unscoped version of this read
    is one reader learning the label, prefix and last-used time of another's
    credential -- and, since the screen carries a Disconnect button beside each
    row, an id they could try to spend.

    An empty `user_id` returns nothing rather than everything, which is what the
    root bearer and a `via="disabled"` principal on a `make dev` box get: they
    have no user row, so they own no connections, and the safe direction is the
    true one.

    Shares `OwnedTokenRow` and its query with `instance_listing` on purpose; the
    owner columns are simply unread here. See `repositories/token`.
    """
    return [_owned(r) for r in repo.listing_with_owner(conn, user_id=user_id, limit=limit)]


def owns(conn: db.Conn, token_id: str, *, user_id: str) -> bool:
    """Whether this person may disconnect this token.

    THE CHECK THAT MAKES `DELETE` SAFE TO EXPOSE TO A READER. Token ids are not
    secret -- they are printed by the CLI and rendered on a page -- so the route
    cannot treat possession of one as authority over it. Reads the row directly
    rather than scanning `connections`, because the answer must not depend on a
    listing's filters: a revoked or expired token still belongs to somebody, and
    revoking it again is a no-op rather than somebody else's business.

    An empty `user_id` is never an owner. `mcp_tokens.user_id` is NULL for tokens
    minted before identity existed, and `"" == None` must not become true by
    accident -- that would hand every pre-identity credential to any principal
    without a user row.
    """
    if not user_id:
        return False
    row = repo.select_mcp_tokens_by_id(conn, token_id).fetchone()
    return row is not None and row["user_id"] == user_id


def instance_listing(
    conn: db.Conn, *, limit: int | None = None, offset: int = 0
) -> list[OwnedTokenRow]:
    """Every live token on the instance, with the owner's address on each row.

    THE UNSCOPED READ. `/api/meta/mcp` is scoped to its caller, which leaves the
    person who owns the box unable to see what credentials exist on it -- a
    leaked token invisible to the only person who could revoke it. This is that
    view, admin-only by `authz`'s default-deny, and a separate route rather than
    a widening of the per-user one because "my agents" and "every agent on this
    instance" are different questions and only the second is privileged.

    Live only, and there is no `include_revoked`. A revoked token is not a
    credential that exists; the question this answers is what could be used
    against the instance right now. The CLI (`venona mcp token ls --all`) is
    still the place to read history.

    Never the secret, and it could not be: venona stores a SHA-256.
    """
    return [
        _owned(r)
        for r in repo.listing_with_owner(conn, include_revoked=False, limit=limit, offset=offset)
    ]


def instance_count(conn: db.Conn) -> int:
    return repo.count_live(conn)


def exists(conn: db.Conn, token_id: str) -> bool:
    """Whether any row carries this id, revoked or not.

    `revoke` cannot answer this: it returns False both for "already revoked" and
    for "never existed", which is the right shape for a caller that only wants
    the token gone and the wrong shape for the admin route, which asks here
    first so that a mistyped id reads as a mistyped id.
    """
    return repo.select_mcp_tokens_by_id(conn, token_id).fetchone() is not None


def revoke(conn: db.Conn, token_id: str, *, now: str) -> bool:
    """Returns True if this call revoked it. Idempotent, and never an error.

    Revocation is independent of the session epoch, deliberately. `bump_epoch`
    kills every session everywhere, which is the honest thing for a browser --
    but if an agent token consulted it, one logout on the operator's phone would
    unplug every connected agent and the symptom would be "it just stopped
    working", days later, with nothing naming the cause.
    `tests/test_mcp_tokens.py::test_logout_does_not_revoke_agent_tokens`.

    Reachable by a reader whose subscription has lapsed (ADR 0235): somebody
    must always be able to disconnect an assistant they have come to distrust.
    """
    with db.transaction(conn, what="revoke mcp token"):
        cur = repo.update_mcp_tokens_by_revoked_at_id(conn, now, token_id)
        changed = cur.rowcount > 0
    _last_touch.pop(token_id, None)
    return changed


def reset_touch_cache() -> None:
    """Tests only. The throttle is process-global, so without this the order
    tests happen to run in decides whether a `last_used_at` write occurs."""
    _last_touch.clear()
