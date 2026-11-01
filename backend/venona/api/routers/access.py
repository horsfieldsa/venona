"""Request access. The only unauthenticated WRITE in the application.

That sentence is the whole design brief for this file, and every decision below
follows from it.

WHY IT EXISTS. A Google OAuth client authenticates any Google account, so
something has to say which ones are ours -- the `users` table, seeded from
`VENONA_AUTH_ALLOWED_EMAILS`. A visitor arriving from a separate front site's one
call to action is otherwise answered with "not permitted to sign in here" and
given nowhere to go. This is where that lands.

WHAT IT CANNOT DO. It cannot grant anything, and that is a property of this path
rather than of the mechanism behind it. Approving somebody is a separate,
authenticated, admin-only action that creates a `users` row -- `venona users
add`, or the approve endpoint below -- and it names a REQUEST ID rather than an
address, so no address a stranger types ever reaches `users`. A request path that
could grant would make this endpoint the way in rather than the way to ask.

WHY IT DOES NOT ECHO. The response is identical for a first-time request, a
duplicate, and an address already on the allow-list. Anything else turns an
unauthenticated endpoint into an oracle for "does this person have an account
here", which is exactly the question an attacker enumerating a small, named user
base wants answered.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from venona import notify, scheduler
from venona.api import authz
from venona.api.authz import actor_id
from venona.api.deps import Window, get_db, page_window
from venona.api.schemas.access import (
    AccessDecision,
    AccessRequest,
    AccessRequestIn,
    AccessRequestList,
    AccessRequestOut,
)
from venona.api.security import get_auth_config
from venona.db import core as db
from venona.db.repositories import access as access_repo
from venona.db.repositories import users as users_repo
from venona.pipeline.types import SystemIdGen, utc_now_iso
from venona.services import user_service

log = logging.getLogger("venona.access")

# No prefix: the path is `/api/request-access`, which is what a separate front site
# links to and what `middleware.EXEMPT_PATHS` names. A `/access/request` under a
# router prefix would read better and would mean changing a published URL.
router = APIRouter(tags=["access"])

# One string, returned whatever happened. See the module docstring. The
# marketing page and `/request-access` render the same sentence, so a
# visitor who reaches the no-script reply is told what everyone else is.
ACKNOWLEDGED = "Thanks. We'll be in touch within a working day."

#: The reply a browser gets when it posts the form itself, with no JavaScript.
#:
#: Deliberately plain: no CSS, no script, nothing to load. It is served from the
#: app origin under the app's own security headers, and a page that needs an
#: asset is a page that can fail to arrive looking broken.
_HTML_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title></head>
<body><h1>{heading}</h1><p>{body}</p></body></html>
"""


def _page(*, title: str, heading: str, body: str, status: int) -> HTMLResponse:
    return HTMLResponse(
        _HTML_PAGE.format(title=title, heading=heading, body=body), status_code=status
    )


async def _submitted(request: Request) -> tuple[AccessRequestIn, bool]:
    """The request's fields, and whether a browser form sent them.

    ONE ENDPOINT, TWO CALLERS. A separate front page posts JSON when its JavaScript
    runs and posts the form itself when it does not, and both have to land here:
    the module docstring calls this the one funnel, and a second endpoint for the
    no-script path would be a second thing to keep in step with the first.

    The two are told apart by content type, not by guessing, and BOTH are
    validated by the same model -- so the no-script path cannot become a way to
    write a row the JSON path would have refused.
    """
    content_type = request.headers.get("content-type", "")
    if content_type.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
        form = await request.form()
        raw = {key: form.get(key) for key in ("email", "coverage", "role")}
        return AccessRequestIn.model_validate({k: v for k, v in raw.items() if v is not None}), True
    return AccessRequestIn.model_validate(await request.json()), False


@router.post("/request-access", response_model=AccessRequestOut, status_code=201)
async def request_access(request: Request, conn: db.Conn = Depends(get_db)):
    """Record an access request, and tell the operator someone is waiting.

    201 rather than 200 because a row is created, and because the form treats
    any 2xx identically -- the status code is for the log, not the caller.

    Returning an `HTMLResponse` bypasses `response_model`, which is what makes
    the no-script reply possible on a modelled route. The declared model still
    describes the JSON contract, which is the one every programmatic caller uses.
    """
    try:
        payload, from_form = await _submitted(request)
    except ValidationError as exc:
        if request.headers.get("content-type", "").startswith("application/json"):
            # `jsonable_encoder`, because a validator that raised a `ValueError`
            # puts the exception object itself in the error's context -- which
            # serialises as a 500 and reports a client mistake as a server one.
            raise HTTPException(422, detail=jsonable_encoder(exc.errors())) from exc
        # A person is looking at this one, so it says what to do rather than
        # printing the shape of the failure.
        return _page(
            title="Check that again",
            heading="Something in the form was not right",
            body="Go back and check the address and the role you picked, then send it "
            "again. If it keeps refusing, ask the person who runs this site.",
            status=422,
        )

    row_id = SystemIdGen().uuid7()
    now = utc_now_iso()

    # A plain transaction, because this reads nothing before it writes. How a
    # transaction BEGINS is not the tool for a read-then-write on Postgres --
    # see `db/core.py::transaction`. The decision routes below do read then
    # write, and they take `SELECT ... FOR UPDATE` plus an advisory lock.
    with db.transaction(conn):
        access_repo.insert_access_request(
            conn,
            request_id=row_id,
            email=payload.email,
            coverage=payload.coverage,
            role=payload.role,
            now=now,
        )

    # After the commit, and never in front of it: a notifier that can fail the
    # request would turn "we lost your webhook" into "your request was
    # rejected". `raise_notice` swallows everything by design, but the ordering
    # is what makes that guarantee load-bearing rather than incidental.
    #
    # `coverage` is up to 2000 characters a stranger typed into a public form.
    # It travels as `Notice.quoted` and never inside a sentence, which is what
    # lets every channel escape it. A plain-text destination is safe by
    # accident; a Slack block and an HTML body are not.
    notify.raise_notice(
        conn,
        "access.requested",
        request_id=row_id,
        email=payload.email,
        role=payload.role,
        coverage=payload.coverage,
    )
    await scheduler.deliver_now(request.app, conn=conn)
    log.info("access requested by %s (%s)", payload.email, payload.role)

    if from_form:
        return _page(
            title="Request received",
            heading="Thanks",
            body=ACKNOWLEDGED,
            status=201,
        )
    return AccessRequestOut(message=ACKNOWLEDGED)


# ── the admin side ───────────────────────────────────────────────────
#
# Everything below is admin by default-deny -- none of it appears in
# `authz.READER_ROUTES`, so `role_for_request` answers `admin` for all of it.
#
# THE SEPARATION THE TABLE PROMISES, and how these keep it. `0001_baseline.sql`
# says "Granting access is NOT a row in this table. Nothing in the request path
# can grant anything." The public route above still writes `'pending'` as a SQL
# literal unreachable from the body. Approval is a DIFFERENT route, reached with
# a DIFFERENT credential, and it takes a REQUEST ID rather than an address -- so
# the address that reaches `users` is one an administrator read off a row, never
# one a caller typed. And the ceiling is `reader`.


@router.get("/access/requests", response_model=AccessRequestList)
def list_requests(
    all: bool = Query(False),
    window: Window = Depends(page_window(default=100, cap=500)),
    conn: db.Conn = Depends(get_db),
) -> AccessRequestList:
    """The queue. `venona access list`, on a screen.

    `total` counts under the same `all` filter the page was selected with, so
    the two cannot disagree about what is being windowed; `pending` is the
    badge's number and stays unfiltered.
    """
    rows = access_repo.listing(conn, pending_only=not all, limit=window.limit, offset=window.offset)
    return AccessRequestList(
        items=[AccessRequest(**dict(row)) for row in rows],
        pending=access_repo.pending_count(conn),
        total=access_repo.listing_count(conn, pending_only=not all),
    )


@router.post("/access/{request_id}/approve", response_model=AccessRequest)
def approve(
    request_id: str,
    payload: AccessDecision,
    request: Request,
    conn: db.Conn = Depends(get_db),
) -> AccessRequest:
    """Turn a request into a reader account. Two rows, one transaction, one click.

    FIVE THINGS HERE PRODUCE A SILENT WRONG ANSWER IF GOT WRONG, and each has a
    guard below rather than a comment somewhere else.

    1. **An env-listed address must be refused.** A `users` row WINS over
       `VENONA_AUTH_ALLOWED_EMAILS` (`middleware.py::_lookup_user`), so creating
       a `reader` row for an address in that list permanently DEMOTES a
       break-glass administrator on their next request. Neither authority guard
       catches it: the self-check compares actor to target, and the last-admin
       check counts `users` rows while an env admin has none. It is the
       "instance stranded" failure arriving through the one door this phase
       opens.
    2. **The lock is on the ADDRESS, not the request.** `SELECT ... FOR UPDATE`
       on the row serialises two approvals of the SAME id and does nothing for
       two approvals of two rows for one address -- and duplicates are ordinary,
       because the public form permits them by design and `access_requests.email`
       carries no unique constraint. Each would mint a different `user_id`, the
       email unique index would keep one, `ON CONFLICT DO NOTHING` would silently
       discard the other, and both would report a creation.
    3. **`upsert_by_email` is `DO NOTHING`**, so it preserves an existing row's
       role AND its status. Approving an address whose account is suspended would
       move the request to approved, show green, and leave someone who still
       cannot sign in -- and since the form is public, a suspended user can file
       a fresh request indistinguishable from a stranger's. Approval never
       changes an existing row; reinstating is a separate, visible act.
    4. **Report what was read back**, never what was intended, so the screen
       cannot claim a creation the database declined to make.
    5. **The siblings get closed.** Leaving the other pending rows for one
       address would leave the queue permanently showing people who already have
       accounts, which is how a queue stops being read.
    """
    actor = actor_id(request)
    now = utc_now_iso()

    with db.transaction(conn, what="approve_access"):
        # (2) The address is the contended resource. This is the same lock every
        # authority change takes, so an approval and a demotion cannot interleave
        # either.
        users_repo.lock_authority(conn)

        row = access_repo.lock_for_decision(conn, request_id)
        if row is None:
            raise HTTPException(404, detail={"error": "no_request", "message": "no such request"})
        if row["status"] != access_repo.PENDING:
            raise HTTPException(
                409,
                detail={
                    "error": "already_decided",
                    "message": f"this request was already {row['status']}",
                },
            )

        email = row["email"]

        # (1) The break-glass address.
        if email in get_auth_config().allowed_emails:
            raise HTTPException(
                409,
                detail={
                    "error": "bootstrap_address",
                    "message": (
                        f"{email} is in VENONA_AUTH_ALLOWED_EMAILS and can already sign "
                        f"in as an administrator. Creating a reader account for it "
                        f"would demote them. Use `venona users` from a shell."
                    ),
                },
            )

        existing = user_service.lookup(conn, email)
        if existing is not None and not existing.active:
            # (3) Approval never reinstates.
            raise HTTPException(
                409,
                detail={
                    "error": "account_suspended",
                    "message": (
                        f"{email} already has a suspended account. Approving this "
                        f"would not let them in -- reinstate them instead, which is "
                        f"a separate and deliberate act."
                    ),
                },
            )

        if existing is None:
            account = user_service.create(
                conn,
                user_id=f"user:{SystemIdGen().uuid7()}",
                # THE CEILING. Approval never produces an administrator; that
                # needs shell access. See `api/routers/admin.py`.
                role=authz.READER,
                email=email,
                now=now,
            )
            user_service.record_event(
                conn,
                event_id=f"uevent:{SystemIdGen().uuid7()}",
                user_id=account.id,
                actor=actor,
                action=user_service.CREATED,
                detail=f"approved from access request {request_id}",
                now=now,
            )
        else:
            # (4) An address that already had an account links to it rather than
            # claiming a creation that `DO NOTHING` would have discarded.
            account = existing

        access_repo.settle(
            conn,
            request_id=request_id,
            status=access_repo.APPROVED,
            now=now,
            note=payload.note,
            actor=actor,
            user_id=account.id,
        )
        # (5)
        access_repo.decline_siblings(
            conn,
            email=email,
            keeping=request_id,
            now=now,
            note=f"superseded by approved request {request_id}",
            actor=actor,
        )

    settled = access_repo.by_id(conn, request_id)
    assert settled is not None  # noqa: S101 - just written
    return AccessRequest(**dict(settled))


@router.post("/access/{request_id}/decline", response_model=AccessRequest)
def decline(
    request_id: str,
    payload: AccessDecision,
    request: Request,
    conn: db.Conn = Depends(get_db),
) -> AccessRequest:
    """Record a no. Writes `access_requests` and nothing else -- there is no
    account to create and none to remove."""
    with db.transaction(conn, what="decline_access"):
        row = access_repo.lock_for_decision(conn, request_id)
        if row is None:
            raise HTTPException(404, detail={"error": "no_request", "message": "no such request"})
        if row["status"] != access_repo.PENDING:
            raise HTTPException(
                409,
                detail={
                    "error": "already_decided",
                    "message": f"this request was already {row['status']}",
                },
            )
        access_repo.settle(
            conn,
            request_id=request_id,
            status=access_repo.DECLINED,
            now=utc_now_iso(),
            note=payload.note,
            actor=actor_id(request),
            user_id=None,
        )

    settled = access_repo.by_id(conn, request_id)
    assert settled is not None  # noqa: S101 - just written
    return AccessRequest(**dict(settled))
