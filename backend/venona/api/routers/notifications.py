"""Where the instance's notices go, and where a reader's do.

TWO ROUTERS IN ONE FILE, and the split between them is the whole authorization
story. `/api/notifications/*` is admin by the default-deny in
`api/authz.py::role_for_request`, and every route on it addresses the INSTANCE's
destinations. `/api/preferences/notifications/*` is reader-reachable and every
route on it addresses THE ACTING USER's, resolved through `learning._actor` --
so, exactly as `PATCH /api/preferences` argues, the route carries no user id and
there is nothing for a caller to substitute.

An administrator can therefore not read where a reader has their brief sent, and
a reader cannot point anything at the instance's failures. Both directions are
enforced in `notify/policy.py` rather than here, so a second caller cannot get
it wrong, and both are pinned by tests.

NO ROUTE HERE RETURNS A STORED `target`. See `api/schemas/notifications.py`.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from venona import notify
from venona.api.deps import Window, get_db, page_window
from venona.api.routers.learning import _actor
from venona.api.schemas.notifications import (
    DeliveryList,
    DeliveryOut,
    DestinationCreate,
    DestinationOut,
    NoticeTypeOut,
    NotificationsOut,
    NotificationStatus,
    ReaderNotificationsOut,
    SubscriptionUpdate,
    TargetReplace,
    TestResult,
)
from venona.db import core as db
from venona.db.repositories import notify as repo
from venona.db.repositories import users as users_repo
from venona.notify import outbox, policy
from venona.notify.channels import DeliveryError
from venona.notify.channels import email as email_channel
from venona.notify.types import NOTICE_TYPES, Notice
from venona.pipeline.types import utc_now_iso

log = logging.getLogger("venona.notifications")

router = APIRouter(prefix="/notifications", tags=["notifications"])
reader_router = APIRouter(prefix="/preferences/notifications", tags=["notifications"])

#: How many past deliveries either screen shows. Announced on the wire so the
#: list can say it was capped; a truncated list that does not is read as
#: complete.
HISTORY_LIMIT = 50


def _audience(owner_id: str | None) -> str:
    return "instance" if owner_id is None else "reader"


def _destination_out(conn: db.Conn, row, *, owner_id: str | None) -> DestinationOut:
    subscribed = set(repo.subscriptions(conn, destination_id=row["id"]))
    target = repo.destination_target(conn, destination_id=row["id"]) or ""
    return DestinationOut(
        id=row["id"],
        kind=row["kind"],
        label=row["label"],
        hint=policy.target_hint(row["kind"], target),
        status=row["status"],
        failures=row["failures"],
        last_error=row["last_error"],
        added_by=row["created_by"],
        created_at=row["created_at"],
        notices=[
            NoticeTypeOut(
                key=declared.key,
                label=declared.label,
                detail=declared.detail,
                on=declared.key in subscribed,
            )
            for declared in policy.subscribable(_audience(owner_id))
        ],
    )


def _listing(conn: db.Conn, *, owner_id: str | None) -> list[DestinationOut]:
    rows = (
        repo.instance_destinations(conn)
        if owner_id is None
        else repo.reader_destinations(conn, user_id=owner_id)
    )
    return [_destination_out(conn, row, owner_id=owner_id) for row in rows]


def _owned(conn: db.Conn, destination_id: str, *, owner_id: str | None):
    """The row, or a 404 -- never a 403.

    A 403 on somebody else's destination id confirms that it exists, which
    turns this into an oracle over a table that holds credentials. The scope is
    part of the lookup rather than a check after it.
    """
    row = repo.destination(conn, destination_id=destination_id)
    if row is None or row["owner_id"] != owner_id or row["owner_kind"] != _audience(owner_id):
        raise HTTPException(status_code=404, detail="No such destination.")
    return row


def _create(
    conn: db.Conn,
    payload: DestinationCreate,
    *,
    owner_id: str | None,
    created_by: str | None,
) -> DestinationOut:
    audience = _audience(owner_id)
    try:
        label = policy.check_label(payload.label)
        target = policy.check_target(payload.kind, payload.target)
        policy.check_room(conn, owner_kind=audience, owner_id=owner_id)
    except policy.DestinationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    destination_id = f"dest:{uuid.uuid4().hex[:24]}"
    now = utc_now_iso()
    try:
        with db.transaction(conn, what="notify_destination_create"):
            repo.insert_destination(
                conn,
                destination_id=destination_id,
                owner_kind=audience,
                owner_id=owner_id,
                kind=payload.kind,
                label=label,
                target=target,
                created_by=created_by,
                now=now,
            )
            # An instance destination starts subscribed to the failures: an
            # operator who has just said where to reach them means "tell me when
            # something breaks", and eight boxes to tick before that works is a
            # trap. A reader's starts subscribed to nothing, because nobody has
            # asked to be written to.
            for declared in policy.subscribable(audience):
                if declared.default_on:
                    repo.subscribe(
                        conn,
                        destination_id=destination_id,
                        notice_type=declared.key,
                        now=now,
                    )
    except Exception as exc:  # noqa: BLE001 - the unique index is the only one
        raise HTTPException(status_code=400, detail="Venona is already sending to that.") from exc
    return _destination_out(
        conn, repo.destination(conn, destination_id=destination_id), owner_id=owner_id
    )


def _set_subscription(
    conn: db.Conn, destination_id: str, payload: SubscriptionUpdate, *, owner_id: str | None
) -> DestinationOut:
    row = _owned(conn, destination_id, owner_id=owner_id)
    try:
        declared = policy.check_notice_type(payload.notice_type, audience=_audience(owner_id))
    except policy.DestinationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with db.transaction(conn, what="notify_subscription"):
        if payload.on:
            repo.subscribe(
                conn, destination_id=row["id"], notice_type=declared.key, now=utc_now_iso()
            )
        else:
            repo.unsubscribe(conn, destination_id=row["id"], notice_type=declared.key)
    return _destination_out(conn, row, owner_id=owner_id)


async def _send_test(request: Request, conn: db.Conn, row) -> TestResult:
    """Deliver one message now, and report what actually happened.

    Straight through the channel rather than through the outbox: a test is a
    question with an answer, and queueing it would report "we will try" to
    somebody who wants to know whether it works. It also never counts a strike
    -- a failed test is information, not a reason to stop using an address the
    operator is in the middle of setting up.
    """
    target = repo.destination_target(conn, destination_id=row["id"]) or ""
    notice = Notice(
        title="Test from Venona",
        body=(
            f"This is the test message from {row['label']}.",
            "If you can read it, Venona can reach you here.",
        ),
        link=(
            "/admin/notifications" if row["owner_kind"] == "instance" else "/settings/notifications"
        ),
        link_label="Back to Venona",
    )
    base = notify.public_url()
    preview = outbox.render_preview(notice, kind=row["kind"], target=target, base=base)
    if row["kind"] == "email" and not email_channel.available():
        return TestResult(
            delivered=False, detail=email_channel.unavailable_reason(), preview=preview
        )
    if notify.is_offline():
        return TestResult(
            delivered=False,
            detail="Venona is running offline, so it did not send anything.",
            preview=preview,
        )
    from venona.sources.http import build_http_client

    client = build_http_client(request.app.state.settings)
    try:
        await outbox.deliver_one(client, outbox.as_claim(notice, row, target), base=base)
    except DeliveryError as exc:
        return TestResult(delivered=False, detail=str(exc), preview=preview)
    except Exception as exc:  # noqa: BLE001 - a test reports, it never raises
        return TestResult(delivered=False, detail=f"{type(exc).__name__}: {exc}", preview=preview)
    finally:
        await client.aclose()
    return TestResult(delivered=True, preview=preview)


def _history(conn: db.Conn, *, owner_id: str | None, window: Window) -> DeliveryList:
    rows = repo.recent(conn, limit=window.limit, offset=window.offset, owner_id=owner_id)
    return DeliveryList(
        limit=window.limit,
        total=repo.recent_count(conn, owner_id=owner_id),
        items=[
            DeliveryOut(
                id=row["id"],
                notice_type=row["notice_type"],
                # Resolved here so no client holds a map of keys to English.
                # An unknown key means a type that has been retired: say the
                # key rather than an empty cell, so the row stays readable.
                notice_label=(
                    NOTICE_TYPES[row["notice_type"]].label
                    if row["notice_type"] in NOTICE_TYPES
                    else row["notice_type"]
                ),
                destination=row["label"],
                kind=row["kind"],
                status=row["status"],
                attempts=row["attempts"],
                last_error=row["last_error"],
                created_at=row["created_at"],
                delivered_at=row["delivered_at"],
            )
            for row in rows
        ],
    )


# ── the instance's own ───────────────────────────────────────────────


@router.get("", response_model=NotificationsOut)
def list_notifications(conn: db.Conn = Depends(get_db)) -> NotificationsOut:
    return NotificationsOut(
        destinations=_listing(conn, owner_id=None),
        email_available=email_channel.available(),
        email_unavailable_reason=(
            None if email_channel.available() else email_channel.unavailable_reason()
        ),
    )


@router.get("/status", response_model=NotificationStatus)
def notification_status(conn: db.Conn = Depends(get_db)) -> NotificationStatus:
    """The cheap answer the app polls, so the rail can carry a mark.

    Deliberately NOT on `/api/health`, which every reader polls: what the
    instance sends and where is not a reader's business. It rides the admin-only
    branch of the polling loop instead, beside the curation badge.
    """
    struck = repo.struck_out(conn)
    by_status = repo.pending_counts(conn)
    return NotificationStatus(
        struck_out=len(struck),
        first_struck_out=struck[0]["label"] if struck else None,
        queued=by_status.get("queued", 0),
        blocked=by_status.get("blocked", 0),
    )


@router.get("/history", response_model=DeliveryList)
def history(
    window: Window = Depends(page_window(default=HISTORY_LIMIT, cap=200)),
    conn: db.Conn = Depends(get_db),
) -> DeliveryList:
    return _history(conn, owner_id=None, window=window)


@router.post("", response_model=DestinationOut, status_code=201)
def create_destination(
    payload: DestinationCreate, request: Request, conn: db.Conn = Depends(get_db)
) -> DestinationOut:
    from venona.api.authz import actor_id

    return _create(conn, payload, owner_id=None, created_by=actor_id(request))


@router.patch("/{destination_id}", response_model=DestinationOut)
def update_subscription(
    destination_id: str, payload: SubscriptionUpdate, conn: db.Conn = Depends(get_db)
) -> DestinationOut:
    return _set_subscription(conn, destination_id, payload, owner_id=None)


@router.put("/{destination_id}/target", response_model=DestinationOut)
def replace_target(
    destination_id: str, payload: TargetReplace, conn: db.Conn = Depends(get_db)
) -> DestinationOut:
    """Replace the address. There is no route that reads it back.

    A replaced address clears the strike history with it: a new address is a
    different destination in every way that matters, and a failure count
    carried across would strike out a working URL on its first mistake.
    """
    row = _owned(conn, destination_id, owner_id=None)
    try:
        target = policy.check_target(row["kind"], payload.target)
    except policy.DestinationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with db.transaction(conn, what="notify_target_replace"):
        repo.update_target(conn, destination_id=row["id"], target=target, now=utc_now_iso())
    return _destination_out(conn, repo.destination(conn, destination_id=row["id"]), owner_id=None)


@router.post("/{destination_id}/clear", response_model=DestinationOut)
def clear_strikes(destination_id: str, conn: db.Conn = Depends(get_db)) -> DestinationOut:
    """Start sending again. Deliberately not a claim that anything was
    delivered -- `sources/registry.py::clear_strikes` draws the same line, and
    ADR 0041 is what it cost to have the only way out be a CLI incantation."""
    row = _owned(conn, destination_id, owner_id=None)
    with db.transaction(conn, what="notify_clear"):
        repo.clear_strikes(conn, destination_id=row["id"], now=utc_now_iso())
    return _destination_out(conn, repo.destination(conn, destination_id=row["id"]), owner_id=None)


@router.post("/{destination_id}/test", response_model=TestResult)
async def test_destination(
    destination_id: str, request: Request, conn: db.Conn = Depends(get_db)
) -> TestResult:
    return await _send_test(request, conn, _owned(conn, destination_id, owner_id=None))


@router.delete("/{destination_id}", status_code=204)
def delete_destination(destination_id: str, conn: db.Conn = Depends(get_db)) -> None:
    row = _owned(conn, destination_id, owner_id=None)
    with db.transaction(conn, what="notify_destination_delete"):
        repo.delete_destination(conn, destination_id=row["id"])


# ── one reader's own ─────────────────────────────────────────────────


@reader_router.get("", response_model=ReaderNotificationsOut)
def reader_notifications(
    request: Request, conn: db.Conn = Depends(get_db)
) -> ReaderNotificationsOut:
    user_id = _actor(request, conn, what="keep delivery settings")
    row = users_repo.profile(conn, user_id=user_id)
    return ReaderNotificationsOut(
        destinations=_listing(conn, owner_id=user_id),
        account_email=row["email"] if row else None,
        email_available=email_channel.available(),
        email_unavailable_reason=(
            None if email_channel.available() else email_channel.unavailable_reason()
        ),
    )


@reader_router.get("/history", response_model=DeliveryList)
def reader_history(
    request: Request,
    window: Window = Depends(page_window(default=HISTORY_LIMIT, cap=200)),
    conn: db.Conn = Depends(get_db),
) -> DeliveryList:
    return _history(
        conn,
        owner_id=_actor(request, conn, what="see what was sent to you"),
        window=window,
    )


@reader_router.post("", response_model=DestinationOut, status_code=201)
def reader_create(
    payload: DestinationCreate, request: Request, conn: db.Conn = Depends(get_db)
) -> DestinationOut:
    """Add somewhere to be reached.

    An EMAIL destination ignores whatever address the body carried and uses the
    account's own. The address is established by the identity provider and
    stored `NOT NULL UNIQUE`, so there is nothing to type; accepting a typed one
    would make this a way to have Venona send mail to a third party on request.
    """
    user_id = _actor(request, conn, what="choose where to send your brief")
    if payload.kind == "email":
        row = users_repo.profile(conn, user_id=user_id)
        if row is None or not row["email"]:
            raise HTTPException(status_code=400, detail="Your account has no email address.")
        payload = DestinationCreate(kind="email", label=payload.label, target=row["email"])
    return _create(conn, payload, owner_id=user_id, created_by=user_id)


@reader_router.patch("/{destination_id}", response_model=DestinationOut)
def reader_update_subscription(
    destination_id: str,
    payload: SubscriptionUpdate,
    request: Request,
    conn: db.Conn = Depends(get_db),
) -> DestinationOut:
    user_id = _actor(request, conn, what="choose what you are told about")
    return _set_subscription(conn, destination_id, payload, owner_id=user_id)


@reader_router.post("/{destination_id}/test", response_model=TestResult)
async def reader_test(
    destination_id: str, request: Request, conn: db.Conn = Depends(get_db)
) -> TestResult:
    user_id = _actor(request, conn, what="check where your brief goes")
    return await _send_test(request, conn, _owned(conn, destination_id, owner_id=user_id))


@reader_router.delete("/{destination_id}", status_code=204)
def reader_delete(destination_id: str, request: Request, conn: db.Conn = Depends(get_db)) -> None:
    user_id = _actor(request, conn, what="stop sending your brief somewhere")
    row = _owned(conn, destination_id, owner_id=user_id)
    with db.transaction(conn, what="notify_destination_delete"):
        repo.delete_destination(conn, destination_id=row["id"])
