"""Request-scoped dependencies."""

from __future__ import annotations

import time
from collections.abc import Iterator

from fastapi import Request

from hub import security
from hub.db import core as db
from hub.repositories import users as users_repo


def get_db(request: Request) -> Iterator[db.Conn]:
    """One connection per request, closed on the way out -- the instance's
    `deps.get_db` shape. No pool: the hub serves a handful of humans."""
    conn = db.connect(request.app.state.settings.database_url)
    try:
        yield conn
    finally:
        conn.close()


def session_email(request: Request, conn: db.Conn) -> str | None:
    """The signed-in email, or None.

    Takes the connection because the session-epoch watermark is checked HERE,
    the one place every route already trusts, so no route can forget it: a
    token issued before the user's last sign-out is refused in every browser,
    not just the one that signed out (`users.bump_session_epoch`). A
    known-email token costs one row read; the hub serves a handful of humans.

    Whether the email may DO anything -- suspended, provisioned, member of
    what -- stays the route's database question, re-read per request so
    suspension bites immediately. An email with no row passes: it holds no
    memberships, and refusing it would break the request-access page for the
    one kind of visitor it exists for."""
    settings = request.app.state.settings
    identity = security.verify_session_issued(
        settings, request.cookies.get(security.COOKIE_NAME), now=time.time()
    )
    if identity is None:
        return None
    email, issued = identity
    user = users_repo.by_email(conn, email)
    if user is not None and issued < float(user["session_epoch"] or 0):
        return None
    return email
