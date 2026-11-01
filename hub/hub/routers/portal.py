"""The portal: sign-in, the domain gallery, and request-access.

Server-rendered HTML, a redirect, or the one JSON acknowledgement the marketing
form reads -- never a modelled body, and `tests/hub/test_hub_route_contracts.py`
carries the reason per route in its `EXEMPT` table.

A GOOGLE LOGIN PROVISIONS NOTHING. Rows are made by the operator (`hub users
add`, `hub requests grant`) and by `/domains/{slug}/start`, which needs one for
Checkout to hang a Stripe customer on. Signing in makes none, so an unknown
account gets a session -- the request-access page works signed-in -- with no
row, no listing in `hub users ls`, and nothing to suspend.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from hub import billing_state, config, google, ids, notify, pages, ratelimit, security, signout
from hub.db.core import Conn
from hub.deps import get_db, session_email
from hub.repositories import access_requests as requests_repo
from hub.repositories import instances as instances_repo
from hub.repositories import settings as settings_repo
from hub.repositories import users as users_repo

router = APIRouter(tags=["portal"])


def _set_session(response, request: Request, *, token: str, expires: float, now: float) -> None:
    settings = request.app.state.settings
    response.set_cookie(
        security.COOKIE_NAME,
        token,
        max_age=int(expires - now),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


@router.get("/")
def portal(request: Request, next: str = "", conn: Conn = Depends(get_db)) -> HTMLResponse:
    theme = pages.theme_of(request)
    email = session_email(request, conn)
    if email is None:
        return HTMLResponse(
            pages.sign_in(
                next_path=security.safe_next(next),
                on_sale=_anything_on_sale(request, conn),
                theme=theme,
            )
        )
    if next:
        # A signed-in arrival with a destination -- the authorize endpoint
        # bounced them here for a session they turn out to have (or have just
        # acquired). Send them on; `safe_next` keeps it same-origin.
        return RedirectResponse(security.safe_next(next), status_code=302)

    user = users_repo.by_email(conn, email)
    # Signed in but unprovisioned, suspended, or memberless: the same page for
    # all three -- which of the three it is stays a server-side fact. Once a
    # request for the session's email is pending, the page is its status
    # rather than the catalog (the pending check keys on request rows, not
    # account state, so it distinguishes none of the three).
    # A PENDING REQUEST IS ONLY THE WHOLE STORY WHILE THEY CANNOT ACT. Somebody
    # waiting on a person, on a hub that would sell them the domain today, is
    # being shown a queue instead of the button that ends it.
    if requests_repo.has_pending(conn, email) and not _anything_on_sale(request, conn):
        return HTMLResponse(pages.request_pending(email=email, theme=theme))
    # ONE FRONT DOOR FOR EVERYBODY: what a reader holds and what they could add
    # are the same question asked about different rows, so they are one page
    # (ADR 0225).
    settings = request.app.state.settings
    return HTMLResponse(
        pages.domains(
            email=email,
            display_name=user["display_name"] if user else None,
            rows=_gallery_rows(conn, user),
            self_serve=settings_repo.self_serve(conn),
            billing_enabled=settings.billing_enabled,
            trial_days=settings.trial_days,
            operator=email.strip().lower() in config.operator_set(settings),
            now=time.time(),
            theme=theme,
        )
    )


def _gallery_rows(conn: Conn, user: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Every public domain, with the role this person holds on it or none.

    THEIRS FIRST. Somebody who already reads one domain and is looking at this
    page is looking for a SECOND, and burying the one they have under the ones
    they do not is how they conclude their account is broken.
    """
    if user is None:
        seats: dict[str, str] = {}
        rows = [dict(row) for row in instances_repo.list_public(conn)]
    else:
        seats = {row["slug"]: row["role"] for row in users_repo.seats_for(conn, user["id"])}
        # THEIR OWN DOMAINS TOO: a membership outlives a domain going back to
        # `provisioning`, which the public list hides, and a front door that
        # dropped it reads as access withdrawn.
        rows = [dict(row) for row in instances_repo.list_for_reader(conn, user["id"])]
    for row in rows:
        row["role"] = seats.get(row["slug"])
    rows.sort(key=lambda row: (row["role"] is None, row["slug"]))
    return rows


@router.post("/domains/{slug}/start")
@router.get("/domains/{slug}/start")
def start_domain(
    slug: str,
    request: Request,
    plan: str = "annual",
    conn: Conn = Depends(get_db),
) -> RedirectResponse:
    """Take a domain and go straight to paying for it.

    THE GATE IS HERE, not on the page that drew the button. The gallery decides
    what to SHOW from the same facts, but a page is a rendering and this is what
    acts -- so self-serve and the account's standing are both re-checked, exactly
    as `submit_request` does.

    Both verbs: the gallery's buttons are form posts, and `submit_request`
    answers 303 to this path, which a browser follows as a GET.
    """
    email = session_email(request, conn)
    if email is None:
        return RedirectResponse("/", status_code=303)

    wanted = instances_repo.by_slug(conn, slug)
    if wanted is None or wanted["status"] not in ("live", "offline"):
        return RedirectResponse("/", status_code=303)

    if not settings_repo.self_serve(conn):
        # Not on offer. The queue is what this person actually needs, and
        # sending them to Checkout for a seat nobody has granted would fail
        # further along with less to say.
        return RedirectResponse(f"/request-access?instance={quote(slug, safe='')}", status_code=303)

    # NOTHING IS GRANTED HERE. The seat is created by the PAYMENT
    # (`billing_apply._grant_the_seat_paid_for`), so an abandoned Checkout
    # leaves no membership and no half-account behind. All this checks is that
    # this person is allowed to buy one -- the same question `submit_request`
    # asks, and for the same reason: a suspended account must not be able to buy
    # its way back in.
    if not _may_serve_self(conn, email):
        return RedirectResponse(f"/request-access?instance={quote(slug, safe='')}", status_code=303)

    # THE ROW, NOT THE SEAT. Checkout hangs a Stripe customer on a `users` row
    # and `billing._signed_in_user` answers 403 to a session that has none.
    #
    # A row with no membership grants NOTHING: it is the state every signed-in
    # stranger is already in, and the portal has a page for it. What it does is
    # give the purchase something to belong to, so the seat can still wait for
    # the payment.
    if users_repo.by_email(conn, email) is None:
        users_repo.create(conn, user_id=ids.user_id(), email=email, display_name=None)

    plan = plan if plan in ("annual", "monthly") else "annual"
    return RedirectResponse(
        f"/billing/checkout?plan={plan}&instance={quote(slug, safe='')}", status_code=303
    )


@router.get("/auth/google")
def auth_google(request: Request, next: str = "") -> RedirectResponse:
    settings = request.app.state.settings
    now = time.time()
    cookie, state, nonce = security.mint_state(settings, now=now, next_path=next)
    response = RedirectResponse(google.authorize_url(settings, state=state, nonce=nonce), 302)
    response.set_cookie(
        security.STATE_COOKIE,
        cookie,
        max_age=security.STATE_TTL_SECONDS,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/auth/callback")
async def auth_callback(
    request: Request, code: str = "", state: str = "", conn: Conn = Depends(get_db)
):
    settings = request.app.state.settings
    now = time.time()
    claims = security.verify_state(
        settings, request.cookies.get(security.STATE_COOKIE), state=state, now=now
    )
    if claims is None or not code:
        return HTMLResponse(
            pages.error(
                "Sign-in expired",
                "This sign-in link has expired or was not started here.",
                status=400,
                theme=pages.theme_of(request),
            ),
            status_code=400,
        )

    try:
        identity = await google.identity_for_code(settings, code=code, nonce=claims["nonce"])
    except google.GoogleError as exc:
        return HTMLResponse(
            pages.error("Sign-in failed", str(exc), status=400, theme=pages.theme_of(request)),
            status_code=400,
        )

    user = users_repo.by_email(conn, identity.email)
    if user is None:
        # A FIRST ARRIVAL, and the only place the hub can see one. Signing in
        # creates no row -- a session is a cookie carrying an address, and a
        # stranger who buys nothing never gets one -- so there is no table to
        # notice a new name appearing in. The dedupe key is the address itself
        # for that reason: every later visit by the same rowless person looks
        # exactly like this one.
        notify.raise_notice(conn, "account.created", email=identity.email)
    else:
        if user["status"] != "active":
            # Indistinguishable from every other refusal by design.
            return HTMLResponse(
                pages.error(
                    "Not signed in",
                    "This account cannot sign in here.",
                    status=403,
                    theme=pages.theme_of(request),
                ),
                status_code=403,
            )
        users_repo.record_login(
            conn,
            user["id"],
            google_subject=identity.subject,
            display_name=identity.display_name,
            picture=identity.picture,
        )

    token, expires = security.mint_session(settings, email=identity.email, now=now)
    response = RedirectResponse(security.safe_next(claims.get("next")), status_code=302)
    _set_session(response, request, token=token, expires=expires, now=now)
    response.delete_cookie(security.STATE_COOKIE, path="/")
    return response


@router.post("/logout")
async def logout(request: Request, conn: Conn = Depends(get_db)) -> RedirectResponse:
    """Sign out EVERYWHERE, not just here. The hub signs people into
    instances without a prompt, so its sign-out ends those instance sessions
    too -- server-side, before the hub cookie goes (see `hub/signout.py`)."""
    await signout.end_all_sessions(request, conn, session_email(request, conn))
    response = RedirectResponse("/", status_code=302)
    response.delete_cookie(security.COOKIE_NAME, path="/")
    return response


def _anything_on_sale(request: Request, conn: Conn) -> bool:
    """Whether any public domain could be bought on the spot right now.

    The gallery's own test, asked across every card rather than about one:
    `billing_state.blockers` is what decides which button that page draws, so
    this cannot come to a different answer than the page it is protecting.
    """
    if not settings_repo.self_serve(conn):
        return False
    takes_payment = request.app.state.settings.billing_enabled
    return any(
        not billing_state.blockers(
            self_serve=True,
            takes_payment=takes_payment,
            price_id=row["stripe_price_monthly"],
        )
        for row in instances_repo.list_public(conn)
    )


@router.get("/request-access")
def request_access(request: Request, instance: str = "", conn: Conn = Depends(get_db)) -> Response:
    """The queue, for somebody who cannot simply buy what they came for.

    ASKING IS NOT ON OFFER WHILE BUYING IS. Somebody who can take a domain on
    the spot has no use for a form promising that a person will decide, and
    reaching it by a stale link, a bookmark or the back button out of Checkout
    reads as the purchase having failed. They go to the gallery instead.

    KEYED ON `_may_serve_self`, NOT MERELY ON BEING SIGNED IN, and that is what
    keeps this from looping. A suspended account is sent HERE by
    `/domains/{slug}/start`; bouncing it back to a gallery whose buttons return
    it here is a redirect loop on the one page that exists to catch it.
    """
    email = session_email(request, conn)
    if email and _may_serve_self(conn, email) and _anything_on_sale(request, conn):
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(
        pages.request_access_form(
            email=email,
            instances=instances_repo.list_public(conn),
            signed_in=email is not None,
            self_serve=settings_repo.self_serve(conn),
            # Carried from the gallery's Request access link, so somebody who
            # picked a domain does not have to pick it twice.
            wanted=instance or None,
            theme=pages.theme_of(request),
        )
    )


#: A separate front site is a different origin (example.com; the hub is
#: hub.example.com), so its form needs an answer a browser will hand back to the
#: page. Allowed origins are configuration, never a wildcard, and the headers
#: are stamped by THIS ROUTE rather than by middleware: an app-wide CORS
#: allowance on an OIDC issuer whose every other page runs on a session cookie
#: is how the chooser and the operator page become cross-origin readable.
#:
#: `Access-Control-Allow-Credentials` is deliberately absent. Without it the
#: browser sends no cookie here and accepts no answer that depends on one, so
#: this route cannot be turned into a cross-origin write carrying a visitor's
#: hub session.
def _allow_origin(request: Request, response: Response) -> None:
    origin = request.headers.get("origin", "")
    if origin and origin in config.cors_origin_set(request.app.state.settings):
        response.headers["access-control-allow-origin"] = origin
        # The body does not vary by Origin, but the HEADERS do, and a cache
        # that missed that would hand one origin's allowance to another.
        response.headers.append("vary", "Origin")


@router.options("/request-access")
def request_access_preflight(request: Request) -> Response:
    """A JSON post preflights (`content-type: application/json` is not a CORS
    simple type), and a preflight that 405s reads on the page as the form
    being down. Answered for every caller; the allowance itself is what is
    scoped, and an origin off the list gets a 204 with no allowance, which is
    what the browser then refuses on."""
    response = Response(status_code=204)
    _allow_origin(request, response)
    if "access-control-allow-origin" in response.headers:
        response.headers["access-control-allow-methods"] = "POST"
        response.headers["access-control-allow-headers"] = "content-type"
        response.headers["access-control-max-age"] = "600"
    return response


#: Checked in the body rather than declared in the signature, because one
#: route serves two content types and a typed parameter can only describe one
#: of them. Both paths are held to these numbers: an over-long address comes
#: back as the person-shaped 422 `errors.py` renders, never as a silently
#: truncated row, and the JSON caller cannot write what the form would refuse.
EMAIL_BOUNDS = (3, 254)
NOTE_MAX = 2000
INSTANCE_MAX = 64


async def _submitted(request: Request) -> tuple[str, str, str, str, bool]:
    """`(email, note, instance, kind, from_form)`.

    TWO CALLERS, ONE ROUTE. The hub's own page posts the form itself; the
    front site posts JSON and reads the status. Both land here so there is
    one queue and one rate limit, and a second endpoint for the second caller
    would be a second thing to keep in step with the first.

    Told apart by content type, not by guessing, and validated identically --
    so the JSON path cannot become a way to write a row the form would have
    refused.
    """
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        fields = {key: body.get(key) for key in ("email", "note", "instance", "kind")}
        from_form = False
    else:
        form = await request.form()
        fields = {key: form.get(key) for key in ("email", "note", "instance", "kind")}
        from_form = True

    def text(key: str) -> str:
        value = fields.get(key)
        return value.strip() if isinstance(value, str) else ""

    def refuse(field: str, message: str) -> None:
        raise RequestValidationError(
            [{"type": "value_error", "loc": ("body", field), "msg": message, "input": None}]
        )

    email = text("email")
    if not (EMAIL_BOUNDS[0] <= len(email) <= EMAIL_BOUNDS[1]):
        refuse("email", "an address this form accepts")
    note = text("note")
    if len(note) > NOTE_MAX:
        refuse("note", f"at most {NOTE_MAX} characters")
    instance = text("instance")
    if len(instance) > INSTANCE_MAX:
        refuse("instance", f"at most {INSTANCE_MAX} characters")

    # An unknown `kind` is read as `access` rather than refused: it is not
    # something a person typed, and the safe reading of "I could not tell what
    # this was" is the bucket a human looks at.
    kind = text("kind")
    return (email, note, instance, kind if kind in requests_repo.KINDS else "access", from_form)


@router.post("/request-access")
async def submit_request(request: Request, conn: Conn = Depends(get_db)) -> Response:
    """Non-echoing, byte-identical for every outcome (ADR 0141): first
    request, duplicate, known account, unknown instance -- one reply."""
    email, note, instance, kind, from_form = await _submitted(request)

    def reply(status: int, *, slow: bool = False) -> Response:
        if from_form:
            body = (
                pages.error(
                    "Slow down",
                    "Too many requests. Try again in a minute.",
                    status=429,
                    theme=pages.theme_of(request),
                )
                if slow
                else pages.request_access_done(theme=pages.theme_of(request))
            )
            response: Response = HTMLResponse(body, status_code=status)
        else:
            response = JSONResponse({"ok": not slow}, status_code=status)
        _allow_origin(request, response)
        return response

    client = ratelimit.client_key(request, edge=request.app.state.settings.edge_headers)
    if not ratelimit.allow(f"request-access:{client}"):
        return reply(429, slow=True)

    wanted = instances_repo.by_slug(conn, instance) if instance else None

    # SELF-SERVE: the same submission, sent to buy rather than queued.
    #
    # Keyed on the SESSION, never on the address in the body. The two are the
    # same for somebody filling in their own form, and where they differ the
    # body is the one an attacker controls: keying on it would let any
    # signed-in person hand a seat to any address they can type.
    #
    # `_may_serve_self` is deliberately not the portal's memberless test. That
    # one collapses unprovisioned, suspended and memberless into one page on
    # purpose, and hanging this on it would let somebody an operator had just
    # suspended buy their way back in.
    #
    # HANDED TO `/domains/{slug}/start`, which is the gallery's route, so there
    # is ONE way in and one place that decides what it does. Granting here as
    # well would be two paths acting on two rule sets, which is how they end up
    # disagreeing about who may.
    signed_in = session_email(request, conn)
    if wanted is not None and signed_in and settings_repo.self_serve(conn):
        if _may_serve_self(conn, signed_in):
            # A DIFFERENT REPLY, and the only one in this route. ADR 0141 makes
            # every other outcome byte-identical so this endpoint cannot be used
            # to test whether an address is known -- but this branch requires a
            # session, so it tells the person holding it something they already
            # knew about themselves. The JSON caller is a separate front page,
            # which is signed out and never reaches here.
            if from_form:
                return RedirectResponse(
                    f"/domains/{quote(wanted['slug'], safe='')}/start", status_code=303
                )
            return reply(200)

    request_id = ids.request_id()
    written = requests_repo.submit(
        conn,
        request_id=request_id,
        email=email,
        note=note,
        wanted_instance_id=wanted["id"] if wanted else None,
        kind=kind,
    )
    # AFTER the write, and never in front of it: a notifier that could fail this
    # request would turn "we could not reach the operator" into "your request
    # was refused". `raise_notice` never raises; the ordering is the other half.
    #
    # ONLY ON A REAL ROW. A duplicate is somebody pressing the button twice, and
    # notifying on it would hand a stranger a way to ring an operator's phone as
    # often as they liked. The reply is byte-identical either way (ADR 0141), so
    # nothing here is observable from outside.
    #
    # `note` is up to two thousand characters a stranger typed into a public
    # form. It travels as `Notice.quoted` and never inside a sentence, which is
    # what lets every channel escape it.
    if written:
        notify.raise_notice(
            conn,
            "access.requested" if kind == "access" else "launch.signup",
            request_id=request_id,
            email=email,
            note=note,
            domain=wanted["display_name"] if wanted else None,
        )
    return reply(200)


def _may_serve_self(conn: Conn, email: str) -> bool:
    """Whether this signed-in person may take a domain without a person deciding.

    Standing, and nothing else. A suspended account has been refused BY
    somebody, and letting it back in through the front door makes suspension a
    suggestion. An address with no row at all is fine: signing in with Google is
    what created the session, and having no row yet is what a first visit looks
    like.

    HOLDING A DOMAIN ALREADY IS NOT A REASON TO REFUSE (ADR 0225). This path
    grants nothing -- it ends at Checkout, the seat is created by the payment
    (`billing_apply._grant_the_seat_paid_for`), and what stands between a
    membership and reading anything is `entitlement_status`.
    """
    user = users_repo.by_email(conn, email)
    if user is None:
        return True
    return user["status"] == "active"
