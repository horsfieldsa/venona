"""ASGI middleware: authentication and response headers.

BOTH ARE RAW ASGI, NOT `BaseHTTPMiddleware`. The usual reason given for that --
"BaseHTTPMiddleware buffers streaming responses" -- has not been true for years;
its `call_next` forwards chunks as they arrive. The real reasons are specific to
this app:

  * `/api/runs/{id}/stream` is a `StreamingResponse` held open for the length of
    a run, which is up to forty minutes. `BaseHTTPMiddleware` wraps every
    request in an anyio task group and a memory object stream, so each open
    stream would carry that machinery for its whole life, and client-disconnect
    propagation -- which the stream route's `finally: conn.close()` depends on --
    goes through an extra hop that has to be right.
  * It sits OUTSIDE `ExceptionMiddleware`, so `raise HTTPException(401)` inside
    a `dispatch` becomes a 500. A raw ASGI app returns the response itself and
    the question does not arise.

ORDERING. Starlette's `add_middleware` does `user_middleware.insert(0, ...)` and
then builds the stack over `reversed(...)`, so **the LAST one added is the
OUTERMOST**. `SecurityHeadersMiddleware` must therefore be added AFTER
`AuthMiddleware`, or it wraps nothing and every 401 and 429 -- the responses
most worth having headers on, because they are the ones an attacker sees --
ships bare. `tests/test_auth.py` asserts a 401 carries them, because this is
exactly the kind of mistake that leaves every unit test passing.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from functools import partial
from typing import Any
from urllib.parse import quote

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from venona.api import authz, ratelimit
from venona.api.ratelimit import GLOBAL_BACKSTOP, bucket_for
from venona.api.security import (
    AuthConfig,
    Principal,
    get_auth_config,
    principal_for,
)

log = logging.getLogger("venona.api.ratelimit")

# The complete set of unauthenticated paths. Kept here, asserted in
# `tests/test_auth.py`, and documented as contract C12 -- three places, so
# widening it is always a deliberate diff and never an accident.
#
#   /api/health/live   the container healthcheck. No DB, no dependencies.
#   /api/auth/mode     which login methods exist. The browser has to know
#                      whether to draw a Google button BEFORE it can log in,
#                      and the answer is visible from the login page anyway.
#   /api/auth/login    starts the Google redirect; nothing to authenticate yet.
#   /api/auth/callback Google calls this; the state cookie is what protects it.
#   /api/auth/token-login  exchanges a bearer token for a session (break-glass
#                          and `ui_smoke`). Rate-limited as a login, not a write.
#   /api/request-access    a separate front site's call to action. It can neither
#                          read nor grant anything -- see `api/routers/access.py`
#                          -- and has its own rate-limit bucket
#                          (`ratelimit.SIGNUP`) rather than falling into `write`.
#   /api/oauth/register    RFC 7591 dynamic client registration. Unauthenticated
#                          BY THE SPEC, and it grants nothing: what a client gets
#                          is an identifier it can use to ASK, and the asking ends
#                          at a consent screen in front of a signed-in human.
#   /api/oauth/token       RFC 6749 §4.1.3. The client is PUBLIC, so it has no
#                          secret to present; what authenticates the exchange is
#                          the authorization code plus the PKCE verifier matching
#                          the challenge a human approved. It falls to the WRITE
#                          bucket, which `ratelimit.py` backstops globally for
#                          exactly this reason: a stranger can reach it.
#
# `/api/oauth/authorize` is deliberately NOT here. It is the one endpoint in the
# flow that must have a human behind it, and a session is how we know there is
# one. A session-less GET is sent to sign in rather than refused (see
# `AUTHORIZE_PATH` and the branch in `__call__`) -- which changes the shape of
# the refusal and not the rule: no session still means no consent screen and no
# code.
EXEMPT_PATHS = frozenset(
    {
        "/api/health/live",
        "/api/auth/mode",
        "/api/auth/login",
        "/api/auth/callback",
        "/api/auth/token-login",
        "/api/request-access",
        "/api/oauth/register",
        "/api/oauth/token",
    }
)

#: The consent screen. Named here rather than written inline because it is a
#: THIRD kind of entry in this file's path vocabulary -- neither exempt nor
#: reachable by a restricted credential, but the one guarded path whose refusal
#: is a redirect. Asserted in `tests/test_oauth.py`.
AUTHORIZE_PATH = "/api/oauth/authorize"

# Only `/api` is served by this process in production -- the reverse proxy serves the SPA
# and proxies `/api` here -- so anything outside it is a dev-server convenience.
# `docs_url` and `openapi_url` are moved UNDER `/api` in `main.py` precisely so
# that this prefix is the whole guard and nothing hides behind it.
GUARDED_PREFIX = "/api"

# The complete set of paths a RESTRICTED credential may reach. The mirror image
# of EXEMPT_PATHS: that one opens a path to every caller, this one closes every
# path to one caller. Both are exact-match frozensets, both are asserted by
# equality in a test -- `tests/test_auth.py` for this file's exemptions,
# `tests/test_mcp_auth.py` for the reach set -- and both are written down as
# contract C12, so widening either is always a deliberate diff.
#
# EXACT MATCH, NEVER A PREFIX. `ratelimit.py` records what prefix matching cost
# in the other direction (`/api/auth/` swept in `/auth/mode` and locked the
# operator out of signing in). Here the failure is worse and silent: a prefix on
# `/api/mcp` would admit an `/api/mcp-admin` added a year from now.
#
# This is what makes a route added next month safe by default. Default-deny at
# GUARDED_PREFIX protects it from an anonymous caller; `reach` protects it from
# an agent. Neither requires the route's author to have remembered anything,
# which is the property `Depends()` could never give.
MCP_PATHS = frozenset({"/api/mcp"})


def _subscription_message(status: str) -> str:
    """What to tell somebody the paywall just refused.

    TWO STATES, and one sentence cannot serve both. A reader whose subscription
    ENDED has an archive and is owed the reassurance that it is still theirs.
    Somebody who never subscribed has no archive at all, and telling them their
    subscription "has ended" is a sentence about a thing that never happened --
    which reads as the product being confused about who they are, at the exact
    moment they are deciding whether to trust it with a card.

    The words live here rather than at a render site because this is a wire
    message, not a rendered one; `web/src/lib/billing.ts` owns everything the
    app draws (CLAUDE.md's rule about run outcomes).
    """
    if status == "none":
        return "This is part of a subscription. Your reading starts when you subscribe."
    return (
        "Your subscription has ended, so this is not available. "
        "The briefs you already had are still here."
    )


def _normalised(path: str) -> str:
    """One trailing slash removed, for the reach check only.

    Starlette's Router sets `redirect_slashes=True` and issues the redirect
    ITSELF (`starlette/routing.py`), which is inside the app and therefore
    AFTER this middleware. So `POST /api/mcp/` arrives here as `/api/mcp/`,
    misses an exact-match MCP_PATHS, and an agent that appended a slash gets
    403 `restricted_credential` -- a security-shaped error for a URL typo, which
    is reported as "venona blocked my token".

    Deliberately NOT applied to EXEMPT_PATHS: failing closed there is correct and
    is the behaviour that already exists.
    """
    return path[:-1] if len(path) > 1 and path.endswith("/") else path


async def _send_json(send: Send, status: int, body: dict, extra: dict | None = None) -> None:
    payload = json.dumps(body).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(payload)).encode()),
    ]
    for key, value in (extra or {}).items():
        headers.append((key.lower().encode(), str(value).encode()))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": payload})


async def _send_redirect(send: Send, location: str, extra: dict | None = None) -> None:
    headers = [
        (b"location", location.encode()),
        (b"content-length", b"0"),
        (b"cache-control", b"no-store"),
    ]
    for key, value in (extra or {}).items():
        headers.append((key.lower().encode(), str(value).encode()))
    await send({"type": "http.response.start", "status": 302, "headers": headers})
    await send({"type": "http.response.body", "body": b""})


def _retry_phrase(seconds: int) -> str:
    """How long to wait, in the units a person waits in."""
    if seconds < 90:
        return "a minute"
    return f"about {round(seconds / 60)} minutes"


async def _refuse_rate_limited(
    send: Send, *, bucket: str, key: str, path: str, verdict: ratelimit.Verdict, html: bool = False
) -> None:
    """The one place a 429 is written, so that no refusal is silent.

    EVERY OTHER REFUSAL HERE IS SELF-EXPLANATORY TO THE PERSON WHO GETS IT; this
    one is not. A rate-limited operator is locked out of the surface they would
    diagnose it from, and the only account of what happened is this line: which
    bucket refused, and whether the key was their address or the `*` that means
    somebody else spent it.
    """
    log.warning(
        "rate limited: bucket=%s key=%s path=%s retry_after=%ss",
        bucket,
        key,
        path,
        verdict.retry_after,
    )
    retry = {"Retry-After": verdict.retry_after}
    if html:
        # A browser NAVIGATED here, so a JSON body is the end of the road: it
        # renders as raw text on a page with no way back and no way forward,
        # which is what a refused sign-in looked like from the outside. The
        # login page renders `?error=` and, seeing one, offers its button
        # instead of starting another round trip.
        wait = _retry_phrase(verdict.retry_after)
        reason = f"too many sign-in attempts just now; try again in {wait}"
        await _send_redirect(send, f"/login?error={quote(reason)}", extra=retry)
        return
    await _send_json(
        send,
        429,
        {
            "detail": {
                "error": "rate_limited",
                "message": f"too many requests; retry in {verdict.retry_after}s",
            }
        },
        extra=retry,
    )


class AuthMiddleware:
    """Default-deny, by construction.

    A `Depends(require_auth)` would be opt-in per route across every router in
    `api/routers/`, and a route added without it is a hole that looks exactly
    like a route added with it. Here a new router is protected the moment it is
    included, and the only way to open a path is to name it in `EXEMPT_PATHS` --
    which a test pins.

    The cost is that middleware runs before routing, so it cannot use `Depends`
    or the request-scoped database connection. Auth needs neither.
    """

    def __init__(
        self, app: ASGIApp, *, database_url: str | None = None, edge: bool = False
    ) -> None:
        self.app = app
        self._limiter = ratelimit.limiter
        # Which forwarded hop `_client_key` may believe. A constructor argument
        # rather than an environment read inside the key function, so a test can
        # exercise both deployments and the two middlewares that need the flag
        # cannot disagree about it.
        self._edge = edge
        # `database_url` is passed at construction rather than read from
        # `scope["app"].state` because `httpx.ASGITransport` does not run
        # `lifespan`, so `app.state.settings` is unset under every test client in
        # this repo. `None` means "ask `get_settings()` when you first need it",
        # which is the same cached object `main.py` assigns.
        self._database_url = database_url

    def _lookup_user(self, email: str, source: Any = None):
        """Resolve a session's address to its `users` row.

        Opens a connection outside `deps.py`, for the reason `_verify_mcp` below
        does: the check must happen before routing or it is not default-deny,
        and middleware cannot use `Depends(get_db)`.

        Injected into `principal_for` rather than imported by `api/security.py`,
        because that module holds no `venona.*` imports at all -- which is what
        lets `tests/test_auth.py` exercise the whole boundary with no database.

        THREE OUTCOMES, and the middle one is the interesting one:

          1. A row exists -> IT DECIDES, including `suspended`. This is what makes
             revocation reach a fourteen-day cookie on the next request.
          2. No row, but the address is in `VENONA_AUTH_ALLOWED_EMAILS` -> a
             synthetic ADMIN. The env allow-list is the BOOTSTRAP: `users` starts
             empty, `ensure_from_env` fills it in the lifespan, and
             `httpx.ASGITransport` never runs a lifespan -- so without this every
             test client and every first boot would refuse the operator from a
             correctly configured instance.
          3. Neither -> None, and the request is refused.

        Suspension still wins, because it requires a ROW: an address that is both
        suspended and in the env list takes branch 1 and is refused. Only the
        ABSENCE of a row reaches branch 2.

        AN UNREACHABLE DATABASE IS OUTCOME 3, NOT OUTCOME 2. Do not answer a
        connect failure with the env list: that reads it WITHOUT reading the row,
        so anyone demoted or suspended but still named in the environment --
        and nothing removes an address from it -- gets their admin back for as
        long as they can keep connections exhausted, which an authenticated
        caller can do at the 600-a-minute `API` ceiling, one connection taken
        here and another by the route. The synthetic record also carries `id=""`,
        so `actor_id` returns None: that principal evades the self-target guard
        on authority changes and signs every audit row as nobody.

        Nothing is lost by failing closed. Break-glass for an unreachable
        database is `VENONA_AUTH_API_TOKEN`, which `verify_bearer` resolves
        without touching Postgres at all.
        """
        from venona.services import user_service
        from venona.settings import get_settings

        def _env_admin():
            if email.strip().lower() not in get_auth_config().allowed_emails:
                return None
            return user_service.UserRecord(
                id="",  # No row, so no id. `Principal.user_id` stays falsy.
                email=email.strip().lower(),
                role=authz.ADMIN,
                status=user_service.ACTIVE,
                session_epoch=0.0,
            )

        database_url = self._database_url or get_settings().database_url
        stack = ExitStack()
        try:
            conn = stack.enter_context(_borrowed(source, database_url))
        except Exception:  # noqa: BLE001 - fail CLOSED; see the docstring
            return None
        with stack:
            return user_service.lookup(conn, email) or _env_admin()

    def _verify_mcp(self, presented: str, source: Any = None) -> Principal | None:
        """Resolve a `vmcp_` bearer against `mcp_tokens`.

        Opens its own connection outside `deps.py`, and has to: the check must
        happen before routing or it is not default-deny, and middleware cannot
        use `Depends(get_db)`.

        Only ever called for a bearer that already starts `vmcp_`, because
        `principal_for` routes on the prefix -- so a root-token request never
        reaches Postgres at all, which is exactly the property a later refactor
        loses without noticing.
        """
        from venona.pipeline.types import utc_now_iso
        from venona.services import token_service, user_service
        from venona.settings import get_settings

        now = utc_now_iso()
        database_url = self._database_url or get_settings().database_url
        with _borrowed(source, database_url) as conn:
            token = token_service.verify(conn, presented, now=now)
            if token is None:
                return None
            # The OWNER decides the role. An agent token is one user's
            # credential and inherits that user's role -- not a leash on
            # everybody, and not the alphabetically-first allow-listed address.
            #
            owner = user_service.get(conn, token["user_id"])
            if owner is None or not owner.active:
                return None
            token_service.touch(conn, token["id"], now=now)
        return Principal(
            email=owner.email,
            via="mcp",
            # The scope names the reach set, and a token may carry more than one
            # -- `reach_for` unions them. An unrecognised value produces an empty
            # frozenset -- admissible nowhere -- which fails closed, and
            # `token_service.mint` refuses to write one in the first place.
            reach=authz.reach_for(token["scope"]),
            token_id=token["id"],
            user_id=owner.id,
            role=owner.role,
            # THE SAME SIX `verify_session` carries, from the same record. The
            # entitlement axis is a property of the reader, not of how they
            # arrived, and `Principal` defaults `entitled=True` -- so omitting
            # them here does not leave an agent token unentitled, it exempts it
            # from the paywall and from the archive ceiling entirely.
            entitled=owner.entitled,
            archive_ceiling=owner.archive_ceiling,
            billing_status=owner.entitlement_status,
            billing_through=owner.entitled_through,
            billing_notice=owner.billing_notice,
            billing_notice_at=owner.billing_notice_at,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        cfg: AuthConfig = get_auth_config()
        path: str = scope.get("path", "")

        if not cfg.enabled or not path.startswith(GUARDED_PREFIX):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)

        # Rate limiting runs BEFORE the auth check and covers exempt paths too:
        # `/api/auth/token-login` is exempt by necessity, and an exempt endpoint
        # that turns a credential into a session is precisely the one that must
        # not be brute-forceable.
        method = scope.get("method", "GET")
        bucket = bucket_for(path, method)
        client = _client_key(scope, edge=self._edge)
        # `charged` and `key` name whichever bucket actually answers, so the
        # refusal below can say whether this caller spent the budget or found it
        # already spent. They are the same question with different remedies.
        charged, key = bucket, client
        verdict = self._limiter.check(charged, key)
        if verdict.allowed and bucket in GLOBAL_BACKSTOP:
            # A second, global bucket keyed "*". For the unauthenticated paths
            # (login, signup) per-IP is weak -- an attacker with many addresses
            # defeats it -- and for WRITE it is belt-and-braces, an aggregate
            # ceiling so no write path is ever unbounded whatever the per-IP key
            # resolves to. `ratelimit.GLOBAL_BACKSTOP` names which and what.
            charged, key = GLOBAL_BACKSTOP[bucket], "*"
            verdict = self._limiter.check(charged, key)
        if not verdict.allowed:
            await _refuse_rate_limited(
                send,
                bucket=charged,
                key=key,
                path=path,
                verdict=verdict,
                # Only the sign-in round trip, and only when a browser asked for
                # a page: `token-login` is a fetch that renders the JSON itself,
                # and an agent on the MCP surface needs the error, not a page.
                html=bucket == ratelimit.SIGNIN
                and method in ("GET", "HEAD")
                and "text/html" in headers.get("accept", ""),
            )
            return

        if path in EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return
        # The pool is read off the app per request rather than held on the
        # middleware: `add_middleware` runs at import, and the pool is created in
        # the lifespan, so a value captured at construction would always be None.
        source = getattr(scope["app"].state, "db_pool", None)
        principal = principal_for(
            cfg,
            cookie=_cookie(headers, "venona_session"),
            authorization=headers.get("authorization"),
            verify_mcp=partial(self._verify_mcp, source=source),
            lookup_user=partial(self._lookup_user, source=source),
        )
        if principal is None:
            # A SESSION-LESS `GET /api/oauth/authorize` IS SENT TO SIGN IN, NOT
            # REFUSED. The endpoint stays out of `EXEMPT_PATHS` and still does
            # nothing without a session -- only the SHAPE of the refusal changes,
            # and only for the one method that a person's browser arrives with.
            #
            # An MCP client opens `authorization_endpoint` verbatim out of the
            # RFC 8414 document; it has no way to know venona wants it entered
            # through `/api/auth/login?next=...` the way the extension does
            # (ADR 0161). So without this, every operator whose browser has no
            # live cookie meets a JSON body where a sign-in should be, and the
            # flow they were promised dead-ends. `_refuse_rate_limited` makes the
            # same argument for the same reason one screen earlier.
            #
            # GET only. The consent POST mints a code, so its 401 stands -- and a
            # redirect would drop the form anyway. No `Accept` test: unlike the
            # sign-in round trip, this path has exactly one legitimate caller
            # shape, and answering a browser that omitted the header with raw
            # JSON is the bug being fixed here.
            if method == "GET" and _normalised(path) == AUTHORIZE_PATH:
                query = scope.get("query_string", b"").decode("latin-1")
                target = path + (f"?{query}" if query else "")
                # `login` runs this back through `_safe_next`, which is where a
                # hostile query string is answered; it is quoted whole here so
                # the client's own parameters survive the round trip intact.
                await _send_redirect(send, f"/api/auth/login?next={quote(target, safe='')}")
                return

            # RFC 9728 §5.1 on the MCP path ONLY. This header is the entire
            # discovery story: a client that gets it fetches the metadata
            # document, finds the authorization server, registers itself and runs
            # the flow -- with nothing configured but the venona URL. Without it
            # an MCP client sees an ordinary 401 and stops.
            #
            # Not added to every 401, deliberately. On a browser path it would
            # invite a human-facing client to start an OAuth flow instead of
            # showing the login page, and the SPA's own 401 handling is what
            # should run there.
            extra = None
            if _normalised(path) in MCP_PATHS:
                issuer = cfg.public_url.rstrip("/")
                extra = {
                    "WWW-Authenticate": (
                        f'Bearer resource_metadata="{issuer}/.well-known/oauth-protected-resource"'
                    )
                }
            await _send_json(
                send,
                401,
                {"detail": {"error": "unauthenticated", "message": "sign in to continue"}},
                extra=extra,
            )
            return

        # The reach check. AFTER the 401, and that ordering is deliberate:
        #
        #   * `reach` lives on the Principal, which does not exist until
        #     `principal_for` has run.
        #   * A caller holding an INVALID credential must not learn which paths a
        #     valid one reaches. Keeping the 401 uniform means reachability is
        #     only ever disclosed to someone who already holds a working token.
        #
        # 403, never 401. A 401 means "present a credential", so an agent that
        # gets one on /api/data/purge re-authenticates and retries until its
        # context window is gone. `restricted_credential` is a sentence a model
        # can act on. `WWW-Authenticate` per RFC 6750 3.1 -- one header now, and
        # what any future OAuth tier needs anyway.
        if principal.reach is not None and _normalised(path) not in principal.reach:
            await _send_json(
                send,
                403,
                {
                    "detail": {
                        "error": "restricted_credential",
                        "message": (
                            "this is an agent token; it is admissible on "
                            f"{', '.join(sorted(principal.reach))} and nothing else"
                        ),
                    }
                },
                extra={"WWW-Authenticate": 'Bearer error="insufficient_scope"'},
            )
            return

        # The ROLE check. After the reach check, and for the same two reasons: the
        # role lives on the Principal, and an invalid credential must not learn
        # what a valid one may do.
        #
        # `authz.role_for_request` defaults to `admin` for any route it does not
        # recognise, so the failure mode of forgetting to classify a new route is
        # a reader who cannot reach it -- never a write a reader can. Contract C12.
        needed = authz.role_for_request(method, path)
        if needed == authz.ADMIN and principal.role != authz.ADMIN:
            await _send_json(
                send,
                403,
                {
                    "detail": {
                        "error": "forbidden",
                        # WHAT IS TRUE FOR THEM, and nothing about the other
                        # side of the boundary. A refusal that explains who CAN
                        # do this, or names what they do, tells a reader about
                        # a room they cannot enter -- and every reader meets
                        # this message, so it is the most-read sentence the
                        # authorization layer has.
                        "message": "This account can read Venona, but not change it",
                    }
                },
            )
            return

        # The ENTITLEMENT check. Third axis, after reach and role, and it only
        # ever narrows the reader set -- an admin operates the instance and is
        # never asked. `authz.billing_enforced` is the gate and ships OFF, for
        # the deploy-window reason it documents.
        #
        # `authz.entitlement_required` decides which routes are paid, and a
        # lapsed reader keeps every route onto their own account (ADR 0235).
        # Covers MCP tokens and the extension for free, because both arrive as a
        # Principal carrying the owning user's entitlement.
        if (
            authz.billing_enforced()
            and needed != authz.ADMIN
            and not principal.entitled
            and authz.entitlement_required(method, path)
        ):
            await _send_json(
                send,
                403,
                {
                    "detail": {
                        "error": "subscription_required",
                        "message": _subscription_message(principal.billing_status),
                    }
                },
            )
            return

        # A second, post-auth bucket keyed on the TOKEN rather than the address.
        # Different question from the pre-auth check above: that one asks who may
        # reach the door, this one asks how fast a given credential may work.
        # Keying on the token is only possible because a restricted credential
        # has a row -- per-IP would lump every hosted agent together.
        #
        # `is not None` is correct HERE and would be wrong one block down: a
        # `token_id` is either an id or absent, while `user_id` has a third
        # state -- the env break-glass admin carries `""`, and `is not None`
        # would put every such principal in one shared bucket keyed on the empty
        # string.
        if principal.token_id is not None:
            verdict = self._limiter.check(ratelimit.MCP, principal.token_id)
            if not verdict.allowed:
                await _refuse_rate_limited(
                    send,
                    bucket=ratelimit.MCP,
                    key=principal.token_id,
                    path=path,
                    verdict=verdict,
                )
                return

        # A third bucket: writes, charged to the PERSON. The pre-auth check above
        # is keyed on the address, which is the one key a signed-in caller can
        # change -- so a laptop moving between a hotspot and an office had a
        # fresh 60-a-minute budget at each. This can only tighten, never loosen:
        # anything the address bucket already refused never reaches this line.
        #
        # `if principal.user_id:` rather than `is not None`, matching how the
        # rest of the codebase reads it (`routers/auth.py`, `authz.py`). The env
        # break-glass admin has no row and carries `""`; `is not None` would
        # charge every one of them to a single bucket keyed on the empty string,
        # which is a shared budget for exactly the credential that exists for
        # when everything else is broken.
        if bucket == ratelimit.WRITE and principal.user_id:
            verdict = self._limiter.check(ratelimit.USER_WRITE, principal.user_id)
            if not verdict.allowed:
                await _refuse_rate_limited(
                    send,
                    bucket=ratelimit.USER_WRITE,
                    key=principal.user_id,
                    path=path,
                    verdict=verdict,
                )
                return

        scope["state"] = dict(scope.get("state") or {})
        scope["state"]["principal"] = principal
        await self.app(scope, receive, send)


@contextmanager
def _borrowed(source: Any, database_url: str) -> Iterator[Any]:
    """A connection from the request pool, or an owned one when there is none.

    Middleware runs before routing and cannot use `Depends(get_db)`, so the
    checks here open their own -- one per authenticated request on top of the
    route's, which is the multiplier that makes an unbounded request path
    exhaust the server. They draw from the same pool the routes do.

    `source` is None under `httpx.ASGITransport`, which runs no lifespan and so
    has no pool: every API test in this repo is that case.
    """
    from venona.db import core as db

    if source is None:
        conn = db.connect(database_url)
        try:
            yield conn
        finally:
            conn.close()
        return
    with db.from_pool(source, database_url) as conn:
        yield conn


def _client_key(scope: Scope, *, edge: bool = False) -> str:
    """Who to charge this request to: the address the trusted edge observed.

    WHICH HOP IS TRUSTWORTHY DEPENDS ON WHAT SITS IN FRONT, and getting it wrong
    in either direction voids every per-IP limit in `ratelimit.py`. Off the edge
    path the reverse proxy is one hop ahead of an unpublished API port, so the rightmost
    `X-Forwarded-For` entry is the one a client cannot forge. On the edge path
    the rightmost hop is the platform's own router -- shared infrastructure, the same
    address for every visitor on the platform -- so keying on it collapses every
    per-IP bucket into one, and five failed logins from anybody lock out
    everybody. `CF-Connecting-IP` is trustworthy only because Cloudflare
    overwrites it on every request it proxies, which is a property of Cloudflare
    and not of the header: a caller who reaches this process directly can write
    it too, so it is read only when `edge` says Cloudflare is in front. ADR 0162
    carries both halves of the argument, including why the leftmost hop is never
    the answer.

    With neither header -- a direct local connection, a test client -- there is no
    proxy in front, so the socket peer is the client.
    """
    headers = Headers(scope=scope)
    if edge:
        cf = headers.get("cf-connecting-ip", "").strip()
        if cf:
            return cf
    forwarded = headers.get("x-forwarded-for")
    if forwarded:
        for hop in reversed(forwarded.split(",")):
            hop = hop.strip()
            if hop:
                return hop
    client = scope.get("client")
    return client[0] if client else "unknown"


def _cookie(headers: Headers, name: str) -> str | None:
    """One cookie, without paying for a full `Request` object per call."""
    raw = headers.get("cookie")
    if not raw:
        return None
    for part in raw.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return None


def content_security_policy(form_action: str = "'self'") -> str:
    """The one policy. `form_action` is the only directive anybody may widen.

    CHROME ENFORCES `form-action` ACROSS THE SUBMISSION'S REDIRECT CHAIN, not
    just the form's action URL. The OAuth consent screen posts to this origin
    and answers with a 302 to the client's callback, so under a flat
    `form-action 'self'` the browser blocks the submission and reports the
    same-origin action URL as the violation -- which reads as "the Authorize
    button does nothing" and points at the wrong URL while doing it. That
    screen passes the destination a human is being shown, and nothing else
    widens anything.

    Verified 2026-08-27 rather than reasoned about: an isolated page under
    `form-action 'self'` submits fine to a same-origin 302 and is blocked by a
    cross-origin one.
    """
    return (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https://*.googleusercontent.com; "
        "font-src 'self'; connect-src 'self'; "
        f"frame-ancestors 'none'; base-uri 'none'; form-action {form_action}; "
        "object-src 'none'"
    )


class SecurityHeadersMiddleware:
    """Response headers for `/api` -- or for the whole origin when `edge=True`.

    `edge` is for deployments where this process IS the public edge (a managed host:
    TLS terminates upstream and nothing of ours sits in front): every path
    gets the headers, plus HSTS and the CSP that otherwise belong to the reverse proxy.

    Off-edge, HSTS is deliberately NOT set. The app sits behind a proxy and
    does not know its own scheme; emitting `Strict-Transport-Security` over
    plain http on localhost pins the dev browser to https for a year, for
    every port. The reverse proxy knows the scheme and owns that header.
    """

    HEADERS = {
        b"x-content-type-options": b"nosniff",
        b"referrer-policy": b"no-referrer",
        b"x-frame-options": b"DENY",
        b"cross-origin-opener-policy": b"same-origin",
    }

    # A reverse proxy in front that stamps these too must carry the same values,
    # or the two drift.
    EDGE_HEADERS = {
        b"strict-transport-security": b"max-age=31536000; includeSubDomains",
        b"content-security-policy": content_security_policy().encode(),
    }

    def __init__(self, app: ASGIApp, edge: bool = False) -> None:
        self.app = app
        self.edge = edge

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or not (self.edge or path.startswith(GUARDED_PREFIX)):
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for key, value in self.HEADERS.items():
                    headers.append(key.decode(), value.decode())
                if self.edge:
                    for key, value in self.EDGE_HEADERS.items():
                        # A route that set its own CSP owns it. Appending ours
                        # would leave TWO policies on the response and a browser
                        # enforces the intersection, so a deliberate widening
                        # becomes a silent block.
                        if key == b"content-security-policy" and headers.get(key.decode()):
                            continue
                        headers.append(key.decode(), value.decode())
                # `no-store` on JSON keeps a brief out of any intermediary's
                # cache -- /api only: the SPA route sets its own cache policy
                # per file. The SSE response sets its own `no-cache,
                # no-transform` (`routers/runs.py`) and must not be touched --
                # overwriting it is how a stream starts arriving in one lump.
                content_type = headers.get("content-type", "")
                if path.startswith(GUARDED_PREFIX) and "text/event-stream" not in content_type:
                    headers.append("cache-control", "no-store")
            await send(message)

        await self.app(scope, receive, send_with_headers)
