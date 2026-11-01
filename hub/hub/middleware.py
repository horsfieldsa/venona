"""Security headers on everything; a bearer gate in front of `/admin`.

Both raw ASGI, like the instance's. Assembly-order warning, replicated from
CLAUDE.md because it bites silently: Starlette's `add_middleware` inserts at
position 0 and builds the stack over `reversed(...)`, so the LAST middleware
added is the OUTERMOST. `main.py` adds the admin gate first (innermost) and
`RequestIdMiddleware` last -- get it backwards and every 401 ships without
security headers while every unit test still passes.
"""

from __future__ import annotations

import json

from starlette.datastructures import MutableHeaders

from hub import security


#: WHAT THIS POLICY ADMITS, on an identity provider, and why each one.
#:
#: `default-src 'none'` is the floor, and every directive below narrows back to
#: `'self'`. No third-party origin appears anywhere, and no source list carries
#: `unsafe-inline` or `unsafe-eval` for script. That matters here more than
#: anywhere else in the project: this is the issuer whose session cookie is the
#: SSO master key for every instance, and the hub renders no user-supplied
#: content into any page.
#:
#:   script-src 'self'    the graph field's canvas on the public pages (ADR
#:                        0202), and the operator bundle behind the session gate
#:   connect-src 'self'   the operator bundle calling this origin's own JSON.
#:                        Absent, it falls to `default-src 'none'` and every
#:                        fetch is refused, which is not a styling failure --
#:                        the surface simply does not function
#:   style-src            `'self'` for the bundle's stylesheet and the public
#:                        pages'; `'unsafe-inline'` for the field's per-page
#:                        block and the theme attribute
#:   font-src 'self'      the two self-hosted faces, so the front door and the
#:                        operator surface are set in the same type as the app
#:
#: `tests/hub/test_security.py` pins this list and asserts the absence of each
#: name that would have to be added before an injected script could run, so a
#: later relaxation is a deliberate diff rather than a quiet one. ADR 0223.
class SecurityHeadersMiddleware:
    """Every hub response, HTML and JSON alike -- there is no SPA and no
    static tier, so nothing else stamps them. HSTS and the CSP belong to
    whatever owns the scheme: a reverse proxy in front, this middleware with
    `edge=True` in production, where TLS terminates upstream and this process is
    the public edge."""

    #: The posture, on every answer. Unconditional: a route cannot opt out of
    #: these by setting its own, because "may this be framed" is not a question
    #: an individual response gets a different answer to.
    HEADERS = {
        b"x-content-type-options": b"nosniff",
        b"referrer-policy": b"no-referrer",
        b"x-frame-options": b"DENY",
        b"cross-origin-opener-policy": b"same-origin",
    }

    #: Caching, where the response has not already answered for itself. Auth
    #: pages and directory answers alike: nothing the hub RENDERS is cacheable
    #: without a correctness bug (a stale chooser, a replayed one-time secret
    #: page), and every page carries `data-theme` from the reader's own
    #: `venona.theme` cookie (docs/adr/0177), so a body cached under a bare URL
    #: is a body in somebody else's palette.
    #:
    #: A default rather than a stamp, because a header appended over one the
    #: route already set does not replace it -- the two both ship, and
    #: `no-store` is the one that wins. `routers/brand.py` serves the canvas at
    #: a URL naming its own bytes and says so; it is the only route that does.
    #:
    #: All or nothing, keyed on `cache-control`: caching is one decision, and a
    #: response that has answered it does not want half of somebody else's
    #: answer. `vary: Cookie` on a URL that names its own content is a shared
    #: cache told to keep a copy per reader of a file that is the same for
    #: everyone -- which is most of what the year was bought for.
    DEFAULTS = {
        b"cache-control": b"no-store",
        b"vary": b"Cookie",
    }

    # A reverse proxy in front that stamps these too must carry the same values,
    # or the two drift.
    EDGE_HEADERS = {
        b"strict-transport-security": b"max-age=31536000; includeSubDomains",
        b"content-security-policy": (
            b"default-src 'none'; style-src 'self' 'unsafe-inline'; "
            b"img-src 'self' data:; font-src 'self'; connect-src 'self'; "
            b"script-src 'self'; manifest-src 'self'; "
            # STRIPE'S TWO HOSTS, and this is the one relaxation ADR 0203
            # anticipated. `form-action` is checked against every hop of a
            # form submission's REDIRECT CHAIN, not just its action, so
            # `'self'` alone silently killed the whole paywall: the trial
            # button posts to `/domains/{slug}/start`, which redirects through
            # `/billing/checkout` to `checkout.stripe.com`, and Safari and
            # Chrome refuse that last hop with no error anywhere. The server
            # log is perfect -- a Checkout Session is created every time -- and
            # nothing happens on screen. `billing.stripe.com` for the same
            # reason: an already-subscribed reader is bounced through
            # `/billing/portal` from that same submission.
            b"form-action 'self' https://checkout.stripe.com https://billing.stripe.com; "
            b"frame-ancestors 'none'; base-uri 'none'"
        ),
    }

    def __init__(self, app, edge: bool = False) -> None:
        self.app = app
        self.edge = edge

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for key, value in self.HEADERS.items():
                    headers.append(key.decode(), value.decode())
                if "cache-control" not in headers:
                    for key, value in self.DEFAULTS.items():
                        headers.append(key.decode(), value.decode())
                if self.edge:
                    for key, value in self.EDGE_HEADERS.items():
                        headers.append(key.decode(), value.decode())
            await send(message)

        await self.app(scope, receive, send_with_headers)


#: The GETs the ops router serves. An exact set rather than a prefix, because
#: `/admin` is shared with the bearer'd JSON surface and a prefix would hand
#: the session gate whatever is added under it next.
OPERATOR_PAGES = frozenset(
    {
        "/admin",
        "/admin/domains",
        "/admin/requests",
        "/admin/launch",
        "/admin/launch.csv",
        "/admin/accounts",
        "/admin/settings",
        "/admin/notifications",
    }
)

#: The POSTs it serves that have no id in the path. An EXACT set for the same
#: reason as the GETs above; the one prefix below is a prefix only because a
#: request id sits in the middle of it.
OPERATOR_POSTS = frozenset(
    {
        "/admin/settings/billing",
        "/admin/settings/accounts",
        "/admin/notifications/add",
        "/admin/notifications/remove",
        "/admin/notifications/switch",
        "/admin/notifications/clear",
        "/admin/notifications/test",
    }
)


#: The operator app's own data, on the session rather than the bearer.
#:
#: A PREFIX, and the only one here that is not forced by an id in the path. It
#: is safe as a prefix for one reason: it cannot collide with the machine
#: surface, which is `/admin/instances*` and `/admin/grants`. Anything added
#: under `/admin/data/` answers to `ops_api.router`'s own `require_operator`,
#: which is the same gate the pages carry -- so a new route here is born guarded
#: exactly as a new page is. `ops_api.PREFIX` is the same string; the partition
#: sweep holds them equal.
OPERATOR_DATA = "/admin/data/"


def _is_operator_page(scope) -> bool:
    """The routes `routers/ops.py` and `routers/ops_api.py` serve under /admin.
    Listed HERE, in the gate, because a pattern this predicate matches is a
    pattern the bearer check never sees: widen it and whatever it newly covers
    answers to the session gate instead. `test_ops.py`'s partition sweep proves
    the match is exactly those two routers and nothing else."""
    path = scope.get("path", "").rstrip("/")
    if path.startswith(OPERATOR_DATA) or path == OPERATOR_DATA.rstrip("/"):
        return True
    if scope.get("method") == "GET" and path in OPERATOR_PAGES:
        return True
    if scope.get("method") != "POST":
        return False
    return path in OPERATOR_POSTS or path.startswith("/admin/requests/")


class AdminBearerMiddleware:
    """Default-deny in front of `/admin`: the check runs before any admin
    route exists, so a new one is born guarded rather than remembered into
    the guard. The one pass-through is `_is_operator_page` -- the
    server-rendered operator surface, which carries its own default-deny
    session gate. Everything outside `/admin` resolves its own auth -- the
    portal from the session cookie, the token endpoint from the client
    secret -- because "who may see this" is a database question there, not
    a header comparison."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/admin"):
            await self.app(scope, receive, send)
            return

        if _is_operator_page(scope):
            await self.app(scope, receive, send)
            return

        settings = scope["app"].state.settings
        presented = ""
        for key, value in scope.get("headers", []):
            if key == b"authorization":
                presented = value.decode()
                break
        token = presented.removeprefix("Bearer ").strip() if presented.startswith("Bearer ") else ""
        if not (token and security.admin_token_ok(settings, token)):
            body = json.dumps(
                {"detail": {"error": "unauthorized", "message": "admin bearer required"}}
            ).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"www-authenticate", b"Bearer"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        await self.app(scope, receive, send)
