"""Sign-in. Google OIDC primary, bearer token as break-glass.

The session is OURS: Google establishes an identity once, at the callback, and
no Google token is stored or refreshed. That keeps the surface to one HMAC and
means a Google outage costs new logins, not existing ones.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from venona.api import oauth
from venona.api.authz import actor_id
from venona.api.deps import get_db, get_db_optional
from venona.api.schemas.auth import (
    AuthMode,
    BillingState,
    DisplayNameUpdate,
    SessionInfo,
    TokenLogin,
)
from venona.api.security import (
    COOKIE_NAME,
    STATE_COOKIE,
    STATE_TTL_SECONDS,
    Principal,
    email_is_allowed,
    get_auth_config,
    mint_session,
    mint_state,
    verify_bearer,
    verify_state,
)
from venona.db import core as db
from venona.db.repositories import users as users_repo
from venona.pipeline.types import SystemIdGen
from venona.services import user_service
from venona.services.setup_service import is_configured

log = logging.getLogger("venona.api.auth")

router = APIRouter(prefix="/auth", tags=["auth"])

# The one refusal the login page renders as its own state rather than as a
# sentence, because it is the one with somewhere to go next
# (`web/src/views/LoginView.vue` matches this string).
NOT_ALLOWED = "not_allowed"


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _set_session(response: Response, token: str, *, cfg, expires: float) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=int(expires - time.time()),
        httponly=True,
        secure=cfg.cookie_secure,
        # Lax, not Strict. The Google callback arrives as a top-level
        # cross-site GET navigation, and Strict would not send the state cookie
        # on it -- the login would fail with a state mismatch every time. Lax
        # is also what keeps a bookmarked deep link working.
        samesite="lax",
        path="/",
    )


def _safe_next(raw: str | None) -> str:
    """Only a same-site path. An absolute URL here would make the login flow an
    open redirect, which is the classic way a sign-in page becomes a phishing
    primitive.

    THE BACKSLASH IS THE ONE THAT GETS THROUGH, because Python and browsers
    disagree about what a path separator is. `/\\evil.com` starts with a single
    `/` and not with `//`, so a check written in those terms admits it, and every
    major browser normalises `\\` to `/` before resolving and lands on another
    origin. `urlsplit` does not save you: it calls the backslash an ordinary path
    character and reports an empty netloc, so a parser-based check agrees with
    Python and disagrees with the browser that follows the redirect. So the
    backslash is rejected outright, anywhere in the value. Control characters and
    whitespace go the same way -- a browser strips tab, newline and carriage
    return before parsing, so `/\\t/evil.com` is the same attack respelled.

    The query string must SURVIVE. Extension pairing redirects through
    `login?next=/api/oauth/authorize?...` (contract C12 addendum), so stripping
    or rejecting a query would break pairing rather than merely inconvenience it.
    """
    if not raw or not raw.startswith("/") or raw.startswith(("//", "/\\")):
        return "/"
    if "\\" in raw or any(char < " " or char == "\x7f" for char in raw):
        return "/"
    return raw


@router.get("/mode", response_model=AuthMode)
async def mode() -> AuthMode:
    """What the login page should offer. Unauthenticated by necessity."""
    from venona.domain import get_domain

    cfg = get_auth_config()
    d = get_domain()
    return AuthMode(
        enabled=cfg.enabled,
        google=cfg.google_configured,
        token=cfg.token_login_enabled,
        display_name=d.display_name,
        login_blurb=d.login_blurb,
        access_roles=list(d.access_roles),
    )


@router.get("/login")
async def login(request: Request, next: str | None = None) -> RedirectResponse:
    cfg = get_auth_config()
    if not cfg.enabled:
        return RedirectResponse(_safe_next(next), status_code=302)
    if not cfg.google_configured:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "google_not_configured",
                "message": "Google sign-in is not configured on this server.",
            },
        )

    cookie, state, nonce = mint_state(cfg, now=time.time(), next_path=_safe_next(next))
    response = RedirectResponse(oauth.authorize_url(cfg, state=state, nonce=nonce), status_code=302)
    response.set_cookie(
        STATE_COOKIE,
        cookie,
        max_age=STATE_TTL_SECONDS,
        httponly=True,
        secure=cfg.cookie_secure,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/callback")
async def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    conn: db.Conn = Depends(get_db),
):
    """Google's redirect target.

    The state cookie is checked BEFORE the code is exchanged, so this endpoint
    cannot be used by an unauthenticated caller to make the server issue
    outbound requests to Google on demand.

    MEMBERSHIP IS THE `users` TABLE, and the env allow-list is the bootstrap
    (contract C12). Both halves of the boundary have to agree on that: gating
    sign-in on `email_is_allowed` alone, while `AuthMiddleware._lookup_user`
    decides per-request authorization from the row, leaves `venona users add`
    producing a row that cannot obtain a session -- authorized for everything a
    reader may do, and refused at the door.
    """
    cfg = get_auth_config()
    now = time.time()

    claims = verify_state(cfg, request.cookies.get(STATE_COOKIE), state=state or "", now=now)
    if claims is None:
        return _login_failed("this sign-in link has expired or was not started here")

    if not code:
        return _login_failed("Google did not return an authorization code")

    try:
        identity = await oauth.identity_for_code(
            cfg, code=code, nonce=str(claims.get("nonce") or "")
        )
    except oauth.OAuthError as exc:
        return _login_failed(str(exc))
    email = identity.email

    # THE ROW WINS, including when it says `suspended`, and that is an ordering
    # rather than the disjunction `email_is_allowed(...) or user is not None`,
    # which fails open for exactly the address most recently suspended. Only the
    # ABSENCE of a row consults the environment (contract C12 addendum).
    #
    # The refusals are deliberately indistinguishable. An unlisted stranger, a
    # suspended user and a removed one all get the one sentence with somewhere to
    # go next; saying which would turn the callback into an oracle for "does this
    # person have an account here", which `routers/access.py` refuses to be for
    # the same reason.
    user = user_service.lookup(conn, email)
    if user is not None and not user.active:
        # LOCAL SUSPENSION ALWAYS WINS, hub or no hub: the instance's own
        # refusal is the one an operator can rely on when the directory is
        # wrong or compromised (docs/adr/0141).
        return _login_failed(f"{email} is not permitted to sign in here", code=NOT_ALLOWED)

    # A hub id_token asserts MEMBERSHIP, not just identity: the hub refuses to
    # mint a code for a non-member, so `identity.role` arriving here is the
    # membership row. Provision on first login; sync the role on later ones --
    # the hub directory is the authority, and a directory the instance ignores
    # is a directory that lies. The sync goes through `change_authority` with
    # `actor=None`: there is no local actor to check, and the rule that has to
    # survive an automated path is the last-admin one.
    if identity.role is not None and user is None:
        try:
            user = user_service.create(
                conn,
                user_id=f"user:{SystemIdGen().uuid7()}",
                email=email,
                role=identity.role,
                now=_iso(now),
                display_name=identity.display_name,
            )
        except user_service.AuthorityError:
            # THE HUB SAYS ADMIN AND THIS INSTANCE MAY NOT MINT ONE (ADR 0168),
            # so the very first sign-in of an instance's first administrator
            # lands here -- the most likely first interaction anyone has with a
            # new deployment.
            #
            # A REFUSAL, not a 500 and not the quiet degrade the sibling branch
            # below can afford: there the person keeps the role they already
            # had, and here there is no row at all, so creating a reader the hub
            # called an admin would strand them -- `change_authority` refuses to
            # raise anyone over a wire, leaving no way up and no explanation.
            log.warning("hub calls %s an admin; this instance has no row for them", email)
            return _login_failed(
                f"{email} has no account on this instance yet. "
                "An administrator has to create it here first.",
                code=NOT_ALLOWED,
            )
        user_service.record_event(
            conn,
            event_id=f"uevent:{SystemIdGen().uuid7()}",
            user_id=user.id,
            actor=None,
            action="created",
            detail=f"provisioned by hub login as {identity.role}",
            now=_iso(now),
        )
    elif identity.role is not None and user is not None and user.role != identity.role:
        # THROUGH `change_authority`, so a login cannot do what the admin surface
        # is forbidden to do. `set_role` skips the last-active-admin rule, and
        # this runs on every sign-in -- so the sole admin of an instance whose hub
        # membership says `reader` would demote themselves by logging in, and
        # then have no way to put it back.
        #
        # A refusal DEGRADES rather than 500s: the person is signing in, and the
        # role they keep is the one they already had. The event log carries the
        # disagreement.
        try:
            with db.transaction(conn, what="change_authority"):
                user_service.change_authority(
                    conn,
                    user_id=user.id,
                    role=identity.role,
                    actor=None,
                    event_id=f"uevent:{SystemIdGen().uuid7()}",
                    now=_iso(now),
                    epoch=now,
                    note=f"hub membership says {identity.role} (was {user.role})",
                )
        except user_service.AuthorityError as exc:
            log.warning("login role sync refused for %s: %s", user.id, exc)

    if user is None and not email_is_allowed(cfg, email):
        # `code` is carried alongside the prose so the login page can render
        # this one case as its own state -- an unlisted visitor is the one
        # failure here with somewhere to go next (`/request-access`), and the
        # rest are the operator's own misconfiguration. Matching on the sentence
        # would work until somebody improved the wording.
        return _login_failed(f"{email} is not permitted to sign in here", code=NOT_ALLOWED)

    # `iss` + `sub` is the identity, not the address, and this is the only moment
    # the provider tells us. Not on the per-request path: `_lookup_user` already
    # opens a connection on every authenticated request and a write there would
    # double it. (`last_seen_at` rides along here, and is also kept current by
    # `_session_info`'s once-a-day stamp, which is what makes it mean "used
    # Venona recently" rather than "signed in afresh recently".) Skipped for an
    # env admin with no row -- there is nothing to stamp until `ensure_from_env`
    # gives them one on the next boot.
    if user is not None:
        user_service.record_oidc_identity(
            conn,
            user_id=user.id,
            issuer=identity.issuer,
            subject=identity.subject,
            display_name=identity.display_name,
            now=_iso(now),
            picture=identity.picture,
        )
        # THE FAST PATH for entitlement, and on a first login the only one. The
        # hub's push carries a change to somebody already signed in, but a brand
        # new reader has no row for it to land on and it 404s -- so without this
        # a reader who has just paid is told they have not subscribed until the
        # hub's next sweep, up to a full poll interval of the wrong answer at the
        # most expensive moment (300s, `hub/hub/config.py::poll_interval_seconds`,
        # 2026-09-09).
        #
        # None means the hub said nothing (an older hub, or Google), and leaves
        # what is stored alone. Only the mgmt push and this may write it.
        #
        # ALL FOUR, because the push counts a 404 as delivered on the promise
        # that this carries the state in. A reader who has never signed in here
        # while their card was failing would otherwise arrive `past_due` with no
        # notice, and no bump left pending to send them one.
        if identity.entitlement is not None:
            users_repo.set_entitlement(
                conn,
                user_id=user.id,
                status=identity.entitlement,
                through=identity.entitled_through,
                notice=identity.billing_notice,
                notice_at=identity.billing_notice_at,
            )

    token, expires = mint_session(cfg, email=email, now=now)
    response = RedirectResponse(_safe_next(str(claims.get("next") or "/")), status_code=302)
    _set_session(response, token, cfg=cfg, expires=expires)
    # One login, one state cookie. Leaving it behind means a second tab's login
    # overwrites the first and the first fails with an unexplained mismatch.
    response.delete_cookie(STATE_COOKIE, path="/")
    return response


def _login_failed(message: str, *, code: str = "") -> RedirectResponse:
    """Back to the login page carrying the reason.

    A redirect rather than a JSON body because the caller here is a browser
    following Google's redirect, not a fetch -- rendering a raw JSON error at
    `/api/auth/callback` would strand the operator on a dead-end URL.

    `error` stays free text and stays the thing that gets RENDERED; `code` is
    optional and is only for the cases the client can act on differently, so a
    client that ignores it still shows the sentence.
    """
    from urllib.parse import quote

    query = f"error={quote(message, safe='')}"
    if code:
        query += f"&code={quote(code, safe='')}"
    return RedirectResponse(f"/login?{query}", status_code=302)


@router.post("/token-login", response_model=SessionInfo)
async def token_login(body: TokenLogin, response: Response) -> SessionInfo:
    """Exchange the API token for a session cookie.

    Two callers: `scripts/ui_smoke.py`, which cannot drive a Google consent
    screen, and the operator on the day Google sign-in is broken -- which is
    precisely the day they need to get in and fix it.

    Gated with the form that submits here: once an OIDC login is configured,
    this path exists only under VENONA_TOKEN_LOGIN=1 (see
    `AuthConfig.token_login_enabled`). 403, not 404, so a refused break-glass
    attempt reads as "switched off", not "gone".
    """
    cfg = get_auth_config()
    if not cfg.token_login_enabled:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "token_login_disabled",
                "message": "token sign-in is not enabled on this server",
            },
        )
    principal = verify_bearer(cfg, f"Bearer {body.token}")
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail={"error": "bad_token", "message": "that token is not valid"},
        )
    token, expires = mint_session(cfg, email=_token_identity(cfg), now=time.time())
    _set_session(response, token, cfg=cfg, expires=expires)
    return SessionInfo(
        authenticated=True, email=_token_identity(cfg), via="bearer", expires_at=_iso(expires)
    )


def _token_identity(cfg) -> str:
    """A token session still needs a `sub` the allowlist will accept on every
    later request, so it borrows the operator's address."""
    return next(iter(sorted(cfg.allowed_emails)), "operator")


@router.get("/session", response_model=SessionInfo)
async def session(request: Request, conn: db.Conn | None = Depends(get_db_optional)) -> SessionInfo:
    """Who am I.

    Reaching this at all means the middleware already accepted the request, so
    it never returns `authenticated: false` -- **401 is what "logged out"
    looks like**, and `main.ts` reads it that way before mount.

    THREE FIELDS COME FROM THE ROW, not the principal: `display_name`,
    `created_at` and the provider. `Principal` deliberately carries only what the
    boundary needs, and `AccountView` refused to render any of the three for
    months because the API served none of them -- which was honest, since nothing
    WROTE them either until the callback started stamping the OIDC identity. One
    extra read, on an endpoint the SPA calls once before mount.
    """
    return _session_info(request, conn)


def _setup_required(request: Request) -> bool:
    """Whether this instance still needs its domain descriptor written.

    `app.state.settings` is assigned in the lifespan, and this endpoint is the
    one route that must answer without one: `httpx.ASGITransport` runs no
    lifespan, so reading the attribute directly turns the SPA's first call into a
    500 and every assertion about the auth boundary into a failure about
    `State`. Unknown means NOT required -- the gate exists to send a blank
    instance to `/setup`, and bouncing a working one there because a probe could
    not see its settings is the worse of the two wrong answers.
    """
    settings = getattr(request.app.state, "settings", None)
    return settings is not None and not is_configured(settings)


def _session_info(request: Request, conn: db.Conn | None) -> SessionInfo:
    """What `GET /session` answers, reusable by a route that has just changed it.

    A plain function rather than one route calling another: `session` is `async`
    for FastAPI's benefit while `update_me` is sync because it writes, and a
    route invoked directly is a route whose dependencies have not run.

    `conn` may be None -- see `deps.get_db_optional`. The three row-sourced fields
    then come back None, which is a state the account page already renders: it is
    the same answer a bearer principal with no `users` row gets.
    """
    cfg = get_auth_config()
    principal: Principal | None = getattr(request.state, "principal", None)
    row = (
        user_service.profile(conn, principal.user_id)
        if conn is not None and principal is not None and principal.user_id
        else None
    )
    # With auth off there is no principal, but the person at the keyboard IS
    # the local operator (the learning routes' binding) -- without this the
    # first-visit tailor invitation would re-fire on every dev visit.
    from venona.db.repositories import relevance as relevance_repo
    from venona.services import preferences_service as user_prefs

    subject = (
        principal.user_id
        if principal is not None and principal.user_id
        else ("user:local-operator" if not cfg.enabled else None)
    )
    relevance_tailored = bool(
        conn is not None
        and subject
        and relevance_repo.settings_row(conn, user_id=subject) is not None
    )
    # Record the visit. THE SPA CALLS THIS ONCE BEFORE MOUNT, from whichever
    # page was opened, which makes it the one place that sees a reader arrive
    # regardless of route -- and `personal_brief.warm_recent_readers` needs
    # exactly that population. `touch_daily` writes at most once per account per
    # day and swallows its own failures, so this cannot slow or break the probe.
    if conn is not None and subject:
        user_service.touch_daily(conn, user_id=subject, now=_iso(time.time()))
    # Same `subject` as `relevance_tailored`, NOT `row`: `row` is keyed on
    # `principal.user_id`, which is None with auth off, so reading it there
    # would hand back null to the one person whose override was just saved.
    timezone = user_prefs.timezone_for(conn, user_id=subject) if conn is not None else None
    return SessionInfo(
        timezone=timezone,
        relevance_tailored=relevance_tailored,
        authenticated=True,
        email=principal.email if principal else None,
        via=principal.via if principal else None,
        display_name=row["display_name"] if row else None,
        created_at=row["created_at"] if row else None,
        provider=user_service.provider_name(row["oidc_issuer"]) if row else None,
        picture_url=row["picture_url"] if row else None,
        # The ROLE, so the shell can hide surfaces the middleware would refuse.
        # Hiding is UX; `api/authz.py` is the enforcement, and a reader who
        # constructs the request by hand still gets a 403.
        role=principal.role if principal else None,
        user_id=principal.user_id if principal else None,
        logout_url=f"{cfg.hub_issuer}/oauth/logout" if cfg.hub_issuer else None,
        setup_required=_setup_required(request),
        billing=_billing_state(principal, cfg),
    )


def _manage_url(cfg) -> str | None:
    """Where this instance's billing links point, or None when there is no hub.

    None means the app hides every billing surface rather than drawing buttons
    that cannot go anywhere: the hub is the only place a subscription exists, so
    an instance with no issuer has no billing to manage and should not claim to.
    """
    if not cfg.hub_issuer:
        return None
    # THE OIDC CLIENT ID, not this instance's domain slug.
    #
    # A subscription buys a seat on one instance, so the hub has to know which,
    # and asked to guess for a reader who belongs to two it can only refuse. The
    # obvious identifier is the slug -- and it is wrong: the hub's
    # `instances.slug` and this instance's `domain.slug` live in separate
    # databases, are set by separate people, and nothing anywhere makes them
    # agree. They matched in production by coincidence and disagreed on the
    # first machine that had both.
    #
    # The client id is the one identifier the two sides are GUARANTEED to share,
    # because the hub minted it and handed it here. Public by OAuth design, and
    # already travelling in every authorize URL the browser follows.
    return f"{cfg.hub_issuer}/billing/portal?client={cfg.google_client_id}"


def _billing_state(principal: Principal | None, cfg) -> BillingState | None:
    """What the shell shows about this reader's subscription.

    None when this instance takes no money, so every billing surface stays
    hidden on a local or self-hosted box rather than offering a Subscribe button
    that goes nowhere.

    Everything here comes off the PRINCIPAL, which the middleware built from the
    `users` row on this request. No extra query, and no second opinion about
    entitlement -- the value the shell renders is the value that was enforced.
    """
    from venona.api import authz

    if not authz.billing_enforced() or principal is None:
        return None
    return BillingState(
        status=principal.billing_status,
        entitled=principal.entitled,
        entitled_through=principal.billing_through,
        notice=principal.billing_notice,
        notice_at=principal.billing_notice_at,
        enforced=True,
        # The hub owns Stripe, so every link leaves. Built from the issuer the
        # instance already trusts rather than a second configured URL: two
        # settings that must agree eventually will not. `_manage_url` carries
        # which instance the seat is on.
        manage_url=_manage_url(cfg),
    )


@router.patch("/me", response_model=SessionInfo)
def update_me(
    payload: DisplayNameUpdate, request: Request, conn: db.Conn = Depends(get_db)
) -> SessionInfo:
    """Change what this account is called. The only thing a reader may write.

    THE ROUTE CARRIES NO USER ID, and that is the safety property rather than a
    URL-design preference. The subject is always `actor_id(request)`, so there is
    nothing for a caller to substitute: a reader cannot address another user
    whatever they type, and the check cannot be forgotten by whoever adds the
    next field here. `PATCH /api/users/{id}` would have needed an
    is-this-you comparison at every call site instead, which is the shape
    `api/authz.py` rejects for authorization generally.

    Only the display name. `role` and `status` are authority and move through
    `services/user_service.change_authority`, which is admin-only, audited and
    refuses self-targeting -- the three properties this endpoint would quietly
    bypass if the model grew a field.

    400 RATHER THAN A SILENT NO-OP when there is no row. `actor_id` is None for
    the root bearer, for the env break-glass admin (`id=""`), and for
    `via="disabled"` -- which is how `make dev` runs locally, so the first person
    to try this feature would otherwise get a cheerful 200 having changed
    nothing and go looking for the bug in the frontend.
    """
    user_id = actor_id(request)
    if not user_id:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "no_account",
                "message": (
                    "this session is not tied to a user account, so there is "
                    "nothing to rename -- sign in with Google, or add a row with "
                    "`venona users add`"
                ),
            },
        )

    with db.transaction(conn, what="set_display_name"):
        user_service.set_display_name(conn, user_id=user_id, display_name=payload.display_name)

    # Read back rather than echo. `DisplayNameUpdate` normalises blank to None,
    # so the response is the stored value and never the requested one -- the same
    # reason the curation routes return the row instead of the payload.
    return _session_info(request, conn)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, conn: db.Conn = Depends(get_db)) -> Response:
    """Clear the cookie and revoke this USER's sessions, so any that leaked die too.

    PER USER, not per instance. A single shared epoch would mean one person
    signing out ends every session on the box, including sessions of people
    mid-read -- invisible with one principal, a bug the moment there are two.
    The global epoch still exists and still wins; it is the break-glass "log
    everyone out", and `verify_session` takes the later of the two.

    Falls back to the global bump when the caller has no user row -- a root-bearer
    or `via="disabled"` session. Those principals have nothing per-user to revoke,
    and leaving them unable to log out at all would be worse.

    Sync `def`, so FastAPI runs it in the threadpool: it writes.
    """
    from venona.api.security import bump_epoch
    from venona.services import user_service

    cfg = get_auth_config()
    principal: Principal | None = getattr(request.state, "principal", None)
    now = time.time()
    if principal is not None and principal.user_id:
        user_service.bump_session_epoch(conn, user_id=principal.user_id, now=now)
    else:
        bump_epoch(cfg, now=now, conn=conn)
    response.delete_cookie(COOKIE_NAME, path="/")
    response.status_code = 204
    return response
