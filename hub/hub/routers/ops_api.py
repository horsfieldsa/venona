"""What the operator surface reads and writes, as JSON over the hub session.

WHY THIS IS A THIRD CATEGORY UNDER `/admin`, and what it is not.

`/admin/instances*` and `/admin/grants` are the MACHINE surface: a bearer token,
read by a script, never a browser. `/admin` and its five siblings are the pages
a signed-in operator opens. This router is the data behind those pages, and it
is gated by the SAME `require_operator` the pages are -- the hub session cookie
plus `HUB_OPERATOR_EMAILS`. Nothing here widens who may enter; it changes what
they are answered with.

NO BEARER, DELIBERATELY. `HUB_ADMIN_TOKEN` in a bundle is a token every reader
of the page holds, and the operator app runs in a browser.

WHY THE WRITES ARE SAFE FROM ANOTHER ORIGIN. The session cookie is
`SameSite=Lax`, so it is withheld from every cross-site request that is not a
top-level GET navigation -- which covers `fetch` and a cross-site form post
alike. The bodies below are `application/json`, which is not a CORS-simple
content type, so a cross-origin caller must preflight; and the hub answers
`Access-Control-Allow-Credentials` on no route at all, so the preflight has
nothing to grant. Keep every side effect on POST: a side-effecting GET is
reachable by top-level navigation, which is the one thing Lax still allows.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from notices.channels import webhook
from notices.types import Notice

from hub import billing_state, grants, ids, notices, notify
from hub.db.core import Conn
from hub.deps import get_db
from hub.release import COMMIT, RELEASE, STARTED_AT
from hub.repositories import access_requests as requests_repo
from hub.repositories import instances as instances_repo
from hub.repositories import notifications as notify_repo
from hub.repositories import settings as settings_repo
from hub.repositories import site as site_repo
from hub.repositories import users as users_repo
from hub.routers.ops import HISTORY_LIMIT, require_operator
from hub.schemas import ops as schema

log = logging.getLogger("hub.ops")

#: The prefix, in one place. `middleware.py` passes it through to this router's
#: own gate and `errors.py` refuses to negotiate it into HTML, so the spelling
#: is load-bearing in three files and `tests/hub/test_ops.py` holds them equal.
PREFIX = "/admin/data"

#: Rows per page. The same number the server-rendered lists used, because it is
#: the reader's expectation rather than a tuning parameter.
PAGE_SIZE = 50

router = APIRouter(prefix=PREFIX, tags=["ops"], dependencies=[Depends(require_operator)])


def _window(total: int, page: int, *, size: int = PAGE_SIZE) -> tuple[int, int, int]:
    """Clamp a page number and return `(page, pages, offset)`.

    CLAMPED, NEVER REFUSED. A page number arrives in a URL, so it can be zero,
    negative, past the end, or a bookmark to a page that emptied since. A 404 on
    `?page=9` after a queue drained is a wrong answer to an obvious question.
    """
    pages = max(1, (total + size - 1) // size)
    page = min(max(page, 1), pages)
    return page, pages, (page - 1) * size


def _stamp(value: Any) -> str | None:
    """ISO-8601 or nothing. The client decides how a time reads; this only has
    to avoid inventing one where there is none."""
    return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value else None)


# ── reads ────────────────────────────────────────────────────────────


@router.get("/session", response_model=schema.Session)
def session(
    conn: Conn = Depends(get_db),
    email: str = Depends(require_operator),
) -> schema.Session:
    """Who you are, and the two counts the rail annotates.

    The rail draws these on every page, so they are answered once here rather
    than by whichever view happens to be open.
    """
    stricken = sum(1 for row in notify_repo.destinations(conn) if row["status"] == "struck_out")
    return schema.Session(
        email=email,
        pending=requests_repo.count_pending(conn, kind="access"),
        stricken_out=stricken,
    )


@router.get("/overview", response_model=schema.Overview)
def overview(conn: Conn = Depends(get_db)) -> schema.Overview:
    # The site's reading is the poller's, stored; the hub's own is read here and
    # now. Both are on the page because the hub and the site are two of the
    # three things that deploy separately, and a domains table that reports
    # every release but the reader's own is the one comparison it cannot make.
    site = site_repo.read(conn) or {}
    told = site.get("last_read") if isinstance(site.get("last_read"), dict) else {}
    return schema.Overview(
        version=RELEASE,
        commit=COMMIT,
        started_at=STARTED_AT,
        site_version=told.get("version"),
        site_commit=told.get("commit"),
        site_built_at=told.get("built_at"),
        site_seen_at=_stamp(site.get("last_seen_at")),
        pending=requests_repo.count_pending(conn, kind="access"),
        waiting=requests_repo.count_pending(conn, kind="launch"),
        accounts=users_repo.count(conn, status="active"),
        domains=[
            schema.Domain(
                slug=row["slug"],
                display_name=row["display_name"],
                status=row["status"],
                last_seen_at=_stamp(row["last_seen_at"]),
                poll_misses=row["poll_misses"],
                version=_told(row).get("version"),
            )
            for row in instances_repo.list_all(conn)
        ],
    )


@router.get("/domains", response_model=schema.Domains)
def domains(conn: Conn = Depends(get_db)) -> schema.Domains:
    """Every domain the hub knows, and everything it knows about each.

    TWO SOURCES IN ONE OBJECT, and neither is reconciled against the other. The
    hub's own columns say what it decided and what it has observed; `last_status`
    is the domain's own answer, stored verbatim at the last successful poll. The
    two disagreeing -- about a version, a name, whether the paywall is on -- is
    the thing worth seeing, so nothing here resolves it into a single truth.

    EVERYTHING IS FIVE MINUTES STALE BY CONSTRUCTION. This reads the poller's
    last result rather than asking the domains now: a page that fans out to
    every instance on load takes the slowest one's timeout as its own, and shows
    an operator a blank screen when a domain is down -- which is the moment they
    most need the page. `polled_at` is the newest answer across all of them, so it
    goes stale when every domain is down -- which tells a reader nothing here
    has been confirmed recently, rather than that nothing has changed.

    RETIRED DOMAINS ARE INCLUDED. `list_all` returns them and they are not
    filtered out here: a retired domain that is still answering is a fact an
    operator wants, and hiding the row is how it goes unnoticed.
    """
    rows = instances_repo.list_all(conn)
    seen = [row["last_seen_at"] for row in rows if row["last_seen_at"] is not None]
    return schema.Domains(
        domains=[_health(row) for row in rows],
        polled_at=_stamp(max(seen)) if seen else None,
    )


def _told(row: dict[str, Any]) -> dict[str, Any]:
    """What a domain said about itself at the last poll, or nothing.

    A `last_status` that is not an object yields an empty dict rather than
    raising, which is what lets both readers treat "never answered" and
    "answered unreadably" as absent fields. `_health` reports the difference
    separately; the overview's table has no room to and does not need to.
    """
    status = row.get("last_status")
    return status if isinstance(status, dict) else {}


def _health(row: dict[str, Any]) -> schema.DomainHealth:
    """One row, with the domain's own answer unpacked beside the hub's columns.

    A `last_status` that is not an object is reported as `unreadable` rather
    than dropped. It means the domain answered a poll with something this hub
    could not read, which is a different state from never having answered, and a
    card that silently showed the hub's columns alone would say the second.
    """
    status = row.get("last_status")
    told = _told(row)
    setup = told.get("setup")
    users = told.get("users")
    return schema.DomainHealth(
        id=row["id"],
        slug=row["slug"],
        display_name=row["display_name"],
        tagline=row.get("tagline"),
        public_url=row["public_url"],
        status=row["status"],
        last_seen_at=_stamp(row["last_seen_at"]),
        poll_misses=row["poll_misses"],
        registered_at=_stamp(row.get("created_at")),
        settings_version=row["settings_version"],
        settings_pushed_version=row["settings_pushed_version"],
        version=told.get("version"),
        commit=told.get("commit"),
        started_at=told.get("started_at"),
        reported_slug=told.get("domain_slug"),
        reported_name=told.get("display_name"),
        setup=schema.DomainSetup(**_setup(setup)) if isinstance(setup, dict) else None,
        documents=told.get("documents"),
        entities=told.get("entities"),
        relationships=told.get("relationships"),
        admins=users.get("admins") if isinstance(users, dict) else None,
        readers=users.get("readers") if isinstance(users, dict) else None,
        billing_enforced=told.get("billing_enforced"),
        latest_brief_at=told.get("latest_brief_at"),
        login_blurb=told.get("login_blurb"),
        unreadable=status is not None and not isinstance(status, dict),
    )


def _setup(setup: dict[str, Any]) -> dict[str, Any]:
    """Only the keys this hub renders. The body is stored verbatim and a newer
    instance may report more; passing the whole dict through would put a field
    on the wire that no schema here describes."""
    drafts = setup.get("drafts")
    return {
        "configured": setup.get("configured"),
        "has_dossier": setup.get("has_dossier"),
        "drafts": drafts if isinstance(drafts, list) else [],
        "active_run": setup.get("active_run"),
    }


@router.get("/requests", response_model=schema.Requests)
def requests_list(page: int = 1, conn: Conn = Depends(get_db)) -> schema.Requests:
    total = requests_repo.count_pending(conn, kind="access")
    page, pages, offset = _window(total, page)
    rows = requests_repo.list_pending(conn, kind="access", limit=PAGE_SIZE, offset=offset)
    return schema.Requests(
        rows=[
            schema.Request_(
                id=row["id"],
                email=row["email"],
                note=row["note"] or "",
                wanted_slug=row["wanted_slug"],
                created_at=_stamp(row["created_at"]) or "",
            )
            for row in rows
        ],
        page=page,
        pages=pages,
        total=total,
        domains=[row["slug"] for row in instances_repo.list_all(conn)],
    )


@router.get("/launch", response_model=schema.Signups)
def launch_list(page: int = 1, conn: Conn = Depends(get_db)) -> schema.Signups:
    total = requests_repo.count_pending(conn, kind="launch")
    page, pages, offset = _window(total, page)
    rows = requests_repo.list_pending(conn, kind="launch", limit=PAGE_SIZE, offset=offset)
    return schema.Signups(
        rows=[
            schema.Signup(
                id=row["id"], email=row["email"], created_at=_stamp(row["created_at"]) or ""
            )
            for row in rows
        ],
        page=page,
        pages=pages,
        total=total,
    )


@router.get("/accounts", response_model=schema.Accounts)
def accounts_list(page: int = 1, conn: Conn = Depends(get_db)) -> schema.Accounts:
    """Memberships as rows, not as a rendered string.

    The repository hands back a `slug:role` list assembled in SQL. Sending that
    through would be the server deciding a typographic question the client is
    the only one placed to answer.
    """
    total = users_repo.count(conn)
    page, pages, offset = _window(total, page)
    rows = users_repo.list_with_memberships(conn, limit=PAGE_SIZE, offset=offset)
    return schema.Accounts(
        rows=[
            schema.Account(
                email=row["email"],
                display_name=row["display_name"],
                status=row["status"],
                memberships=_memberships(row.get("memberships")),
                last_login_at=_stamp(row["last_login_at"]),
            )
            for row in rows
        ],
        page=page,
        pages=pages,
        total=total,
    )


def _memberships(value: Any) -> list[schema.Membership]:
    """`["acme:reader", ...]` or a comma-joined string, whichever the repository
    hands back, split back into the pair it always was."""
    if not value:
        return []
    items = value if isinstance(value, list) else str(value).split(",")
    out: list[schema.Membership] = []
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        slug, _, role = text.partition(":")
        out.append(schema.Membership(slug=slug, role=role or "reader"))
    return out


@router.get("/settings", response_model=schema.SettingsState)
def settings_state(request: Request, conn: Conn = Depends(get_db)) -> schema.SettingsState:
    """What we decided, what each domain says back, and what that adds up to.

    `reported` is the point of this route. A surface that shows only the saved
    value cannot tell a switch that has not arrived from a domain that has been
    down since Tuesday, and both of those are things an operator has to act on
    differently.

    `blockers` is the same argument one level up. Three things have to be true
    before a stranger can subscribe and only one of them was ever on this page,
    so an operator could turn self-serve on, be told it saved, and still watch
    every card offer `Request access` with nothing anywhere naming the gap.
    It comes from `billing_state.blockers`, which the gallery itself calls.
    """
    takes_payment = request.app.state.settings.billing_enabled
    self_serve = settings_repo.self_serve(conn)
    domains = []
    for row in instances_repo.list_all(conn):
        status = row["last_status"] if isinstance(row.get("last_status"), dict) else {}
        reported = status.get("billing_enforced")
        domains.append(
            schema.SettingsDomain(
                id=row["id"],
                slug=row["slug"],
                display_name=row["display_name"],
                billing_enforced=bool(row["billing_enforced"]),
                reported=None if reported is None else bool(reported),
                last_seen_at=_stamp(row["last_seen_at"]),
                blockers=billing_state.blockers(
                    self_serve=self_serve,
                    takes_payment=takes_payment,
                    price_id=row["stripe_price_monthly"],
                ),
                price_monthly_amount=row["price_monthly_amount"],
                price_currency=row["price_currency"],
            )
        )
    return schema.SettingsState(domains=domains, self_serve=self_serve, takes_payment=takes_payment)


@router.get("/notifications", response_model=schema.Notifications)
def notifications_state(conn: Conn = Depends(get_db)) -> schema.Notifications:
    destinations = []
    for row in notify_repo.destinations(conn):
        destinations.append(
            schema.Destination(
                id=row["id"],
                label=row["label"],
                hint=notices.target_hint(notify_repo.destination_target(conn, row["id"]) or ""),
                status=row["status"],
                last_error=row["last_error"],
                subscribed=sorted(notify_repo.subscriptions(conn, row["id"])),
            )
        )
    return schema.Notifications(
        destinations=destinations,
        types=[
            schema.NoticeType(key=t.key, label=t.label, detail=t.detail)
            for t in notices.all_types()
        ],
        history=[
            schema.Delivery(
                created_at=_stamp(row["created_at"]) or "",
                notice_type=row["notice_type"],
                label=row["label"],
                status=row["status"],
                delivered_at=_stamp(row["delivered_at"]),
                attempts=row["attempts"],
                last_error=row["last_error"],
            )
            for row in notify_repo.recent(conn, HISTORY_LIMIT)
        ],
    )


# ── writes ───────────────────────────────────────────────────────────


def _pending_or_404(conn: Conn, request_id: str) -> dict:
    row = requests_repo.by_id(conn, request_id)
    if row is None or row["status"] != "pending":
        raise HTTPException(404, detail="no such request")
    return row


@router.post("/requests/{request_id}/grant", response_model=schema.Ok)
def grant(
    request_id: str,
    body: schema.GrantIn,
    conn: Conn = Depends(get_db),
) -> schema.Ok:
    """Create the membership AND settle the row, in one call.

    The machine surface's `POST /admin/grants` does only the first half, so
    composing the two from a browser would be two requests that can half fail --
    a granted person whose request still reads pending, or the reverse.
    """
    row = _pending_or_404(conn, request_id)
    # A launch signup names no domain, because the form that took it asked for
    # nothing but an address -- so there is no such thing as granting one, only
    # picking a domain on their behalf. The refusal is here rather than in the
    # client because a control that is merely absent is still a request anybody
    # can post.
    if row["kind"] == "launch":
        raise HTTPException(409, detail="a launch signup has not asked for a domain")
    instance = instances_repo.by_slug(conn, body.slug)
    if instance is None:
        raise HTTPException(404, detail=f"no domain {body.slug!r}")
    grants.grant_access(conn, email=row["email"], instance_id=instance["id"], role=body.role)
    requests_repo.set_status(conn, request_id, "granted")
    return schema.Ok()


@router.post("/requests/{request_id}/dismiss", response_model=schema.Ok)
def dismiss(request_id: str, conn: Conn = Depends(get_db)) -> schema.Ok:
    _pending_or_404(conn, request_id)
    requests_repo.set_status(conn, request_id, "dismissed")
    return schema.Ok()


@router.post("/launch/{request_id}/remove", response_model=schema.Ok)
def remove_signup(request_id: str, conn: Conn = Depends(get_db)) -> schema.Ok:
    """The same settle as a dismissal, under the name the launch list uses.

    One route per list rather than one shared route: the two pages mean
    different things by it, and a shared endpoint would have to be told which,
    which is the same fact spelled twice.
    """
    _pending_or_404(conn, request_id)
    requests_repo.set_status(conn, request_id, "dismissed")
    return schema.Ok()


@router.post("/settings/billing", response_model=schema.Ok)
async def set_billing(
    request: Request,
    body: schema.BillingIn,
    conn: Conn = Depends(get_db),
) -> schema.Ok:
    """Throw the switch, then try to deliver it before answering.

    THE SAVE NEVER FAILS ON THE DELIVERY. The decision is ours and it is
    recorded either way; a domain that is offline is the ordinary case, and an
    error there would leave an operator unsure whether the switch was thrown at
    all. What the delivery buys is that the common case takes effect NOW rather
    than on the next poll, which is up to five minutes of a reader seeing the
    wrong answer about their own subscription.
    """
    from hub.routers.ops import _deliver

    if instances_repo.by_id(conn, body.instance_id) is None:
        raise HTTPException(404, detail="no such domain")
    instances_repo.set_billing_enforced(conn, instance_id=body.instance_id, enforced=body.enforced)
    await _deliver(request.app.state.settings, conn, body.instance_id)
    return schema.Ok()


@router.post("/settings/accounts", response_model=schema.Ok)
def set_accounts(
    body: schema.SelfServeIn,
    conn: Conn = Depends(get_db),
    email: str = Depends(require_operator),
) -> schema.Ok:
    """Hub-wide, so there is nothing to deliver: the front door is this
    process's own, and `portal.py` reads the row on the request that needs it."""
    settings_repo.set_self_serve(conn, enabled=body.self_serve, by=email)
    return schema.Ok()


@router.post("/notifications/add", response_model=schema.Ok)
def add_destination(
    body: schema.DestinationIn,
    conn: Conn = Depends(get_db),
    email: str = Depends(require_operator),
) -> schema.Ok:
    """Check the address before storing it, and say why if it is refused.

    `check_webhook` is the save-time half of the SSRF guard and it is the same
    predicate the instance runs, so a refusal arrives as a sentence the operator
    can act on rather than as a delivery that quietly never happens hours later.
    """
    if len(notify_repo.destinations(conn)) >= notices.MAX_DESTINATIONS:
        raise HTTPException(
            409, detail="That is as many places as Venona will send to. Remove one first."
        )
    try:
        clean_label = notices.check_label(body.label)
        clean_target = notices.check_webhook(body.url)
    except notices.DestinationError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    destination_id = ids.destination_id()
    if not notify_repo.insert(
        conn,
        destination_id=destination_id,
        kind="webhook",
        label=clean_label,
        target=clean_target,
        created_by=email,
    ):
        raise HTTPException(409, detail="Venona already sends to that address.")
    # An operator who has just said where to reach them means "tell me when
    # something needs me". Making them tick a row of boxes to get that is a
    # trap, and absence still means no afterwards, so unticking one sticks.
    #
    # THE OFFER IS RECORDED FOR EVERY TYPE, including the ones that default off:
    # it is what tells a later boot that this destination has already seen the
    # switch, so `notify.offer_new_types` can tick a genuinely new one without
    # undoing a no somebody meant.
    for declared in notices.all_types():
        notify_repo.record_offer(conn, destination_id=destination_id, notice_type=declared.key)
        if declared.default_on:
            notify_repo.subscribe(conn, destination_id=destination_id, notice_type=declared.key)
    return schema.Ok()


@router.post("/notifications/remove", response_model=schema.Ok)
def remove_destination(body: schema.DestinationRef, conn: Conn = Depends(get_db)) -> schema.Ok:
    """The subscriptions and anything still queued for it go too, by CASCADE."""
    notify_repo.delete(conn, body.destination_id)
    return schema.Ok()


@router.post("/notifications/switch", response_model=schema.Ok)
def switch_subscription(body: schema.SwitchIn, conn: Conn = Depends(get_db)) -> schema.Ok:
    if body.notice_type not in notices.NOTICE_TYPES:
        raise HTTPException(404, detail="Venona does not send that")
    if body.on:
        notify_repo.subscribe(
            conn, destination_id=body.destination_id, notice_type=body.notice_type
        )
    else:
        notify_repo.unsubscribe(
            conn, destination_id=body.destination_id, notice_type=body.notice_type
        )
    return schema.Ok()


@router.post("/notifications/clear", response_model=schema.Ok)
def clear_strikes(body: schema.DestinationRef, conn: Conn = Depends(get_db)) -> schema.Ok:
    """The way back from a strike-out is a button.

    ADR 0041 is what this cost to learn the first time: a thing that disables
    itself and cannot be re-enabled from the app is a thing somebody edits the
    database to fix.
    """
    notify_repo.clear_strikes(conn, body.destination_id)
    return schema.Ok()


@router.post("/notifications/test", response_model=schema.Ok)
async def test_destination(
    request: Request,
    body: schema.DestinationRef,
    conn: Conn = Depends(get_db),
) -> schema.Ok:
    """Send one message now and report what actually happened.

    STRAIGHT DOWN THE CHANNEL rather than through the queue: a test is a
    question with an answer, and queueing it would report "we will try" to
    somebody who wants to know whether it works. It counts no strike either --
    a failed test is information, not a reason to stop using an address the
    operator is still setting up.

    AND IT PROVES LESS THAN IT LOOKS LIKE IT DOES, which is why the page says so
    beside a struck-out destination: this path never reads `status`, so a
    destination Venona has stopped sending to still answers a test.
    """
    target = notify_repo.destination_target(conn, body.destination_id)
    if target is None:
        raise HTTPException(404, detail="no such destination")
    notice = Notice(
        title="Test from Venona",
        body=("If you can read this, Venona can reach you here.",),
        link="/admin/notifications",
        link_label="Back to Venona",
    )
    base = request.app.state.settings.public_url
    content, headers = webhook.body_for(notice, target, base=base)
    try:
        status, _body = await notify.post_once(target, content=content, headers=headers)
    except Exception as exc:  # noqa: BLE001 - a test reports, it never raises
        raise HTTPException(502, detail=f"It did not arrive. {type(exc).__name__}: {exc}") from exc
    if not 200 <= status < 300:
        raise HTTPException(502, detail=f"It did not arrive. The receiver answered {status}.")
    return schema.Ok()
