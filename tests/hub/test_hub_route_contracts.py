"""Every hub JSON route declares a response_model; the HTML routes are named.

The instance's ratchet (tests/test_route_contracts.py), ported: without a
model, a renamed field ships silently and the consumer finds out. The exempt
table must say WHY a model would break each route, and two reverse checks keep
the table honest -- it may not name a dead route, and it may not name a route
that has since grown a model.
"""

from __future__ import annotations

from fastapi.routing import APIRoute
from hub.assets import FONT_FILES, PUBLIC_CSS_URL
from hub.field import SCRIPT_URL
from hub.routers.brand import FILES as BRAND_FILES
from hub.routers.ops import APP_PATHS

#: (method, path) -> why a response_model would break it.
EXEMPT = {
    **{
        ("GET", f"/{name}"): "a brand file: the bytes `make icons` wrote, not a JSON body"
        for name in BRAND_FILES
    },
    # The front door's stylesheet and its four type files. Imported from the
    # generated module rather than spelled here: the sheet's URL names its own
    # bytes, so a hand-written entry would be stale the first time a colour
    # moved -- and the reverse check below would then fail on a green tree.
    ("GET", PUBLIC_CSS_URL): (
        "the front door's stylesheet: the bytes `make hub-public` wrote, at a "
        "URL naming them, not a JSON body"
    ),
    **{
        ("GET", f"/fonts/{name}"): "a woff2: the typeface itself, not a JSON body"
        for name in FONT_FILES
    },
    ("GET", SCRIPT_URL): (
        "the graph field's canvas: a JavaScript bundle at a URL naming its own "
        "bytes, not a JSON body"
    ),
    ("GET", "/"): "the portal page: HTML in front of a human, not a JSON body",
    ("GET", "/auth/google"): "a 302 to Google carrying the state cookie",
    ("GET", "/auth/callback"): "a 302 on success, an HTML error page otherwise",
    ("POST", "/logout"): "a 302 clearing the cookie",
    ("GET", "/oauth/logout"): (
        "the end-session endpoint: a 302 clearing the cookie, reached by a "
        "top-level navigation from an instance's sign-out"
    ),
    ("GET", "/request-access"): "an HTML form",
    ("GET", "/billing/checkout"): (
        "a 303 to Stripe's hosted Checkout. A navigation rather than a fetch so "
        "the app does not need a permanent cross-origin allowance for a button"
    ),
    ("GET", "/billing/portal"): (
        "a 303 to Stripe's customer portal, minted per click because the session is short-lived"
    ),
    ("GET", "/billing/return"): "a 303 putting the browser back where it started",
    # The operator app's own document, at each path that serves it. DERIVED
    # from the list the router mounts, not six literals: a seventh page would
    # otherwise fail this test for having no model, and the obvious fix is to
    # paste another line rather than to notice the list already exists.
    **{
        ("GET", f"/admin{path}"): (
            "the operator app's own document: index.html behind the session gate, not a JSON body"
        )
        for path in APP_PATHS
    },
    ("GET", "/admin/launch.csv"): (
        "a CSV file with `content-disposition: attachment` -- the launch list "
        "leaves as something a mail tool reads, and a model would make it JSON"
    ),
    ("GET", "/domains/{slug}/start"): "a 303 into Checkout, or back to the queue",
    ("POST", "/domains/{slug}/start"): "a 303 into Checkout, or back to the queue",
    ("POST", "/request-access"): (
        "the non-echoing reply: the same body for every outcome, as HTML for a "
        "form post and JSON for a separate front page's fetch; a model would "
        "invite fields that vary"
    ),
    ("GET", "/oauth/authorize"): (
        "the consent-screen split: 302 with a code for a member, an HTML "
        "error page (never a redirect) for a bad client, the request-access "
        "page for a non-member"
    ),
    ("POST", "/oauth/token"): (
        "TokenResponse on 200 and an RFC 6749 §5.2 error body otherwise -- "
        "one model cannot say both, and the shapes are pinned by "
        "test_oidc_flow.py and the cross-package contract test"
    ),
}


#: The operator bundle, exempted by RULE rather than by name.
#:
#: Every file under it carries a digest of its own contents, so the paths change
#: on every build -- a table naming them would be stale the first time anyone
#: touched a component, and the "EXEMPT names a dead route" check below would
#: fail on a green tree. The rule is what actually holds: nothing served from
#: this prefix is JSON, because `routers/brand.py` only mounts a route here for
#: a file whose extension is in its `MEDIA_TYPES`.
BUNDLE_PREFIX = "/app/"


def _is_bundle(path: str) -> bool:
    return path.startswith(BUNDLE_PREFIX)


def _api_routes(app) -> list[APIRoute]:
    """Flattened: this FastAPI wraps each included router in an
    `_IncludedRouter` whose own `.routes` hold the APIRoutes, so a top-level
    scan sees nothing and passes vacuously -- which is how the first version
    of this file shipped green with zero routes checked."""
    found: list[APIRoute] = []

    def walk(routes) -> None:
        for route in routes:
            if isinstance(route, APIRoute):
                found.append(route)
            elif hasattr(route, "original_router"):
                walk(route.original_router.routes)
            elif hasattr(route, "routes"):
                walk(route.routes)

    walk(app.routes)
    assert found, "no routes found -- the walk is broken, not the app"
    return found


def test_every_json_route_has_a_response_model(hub_app) -> None:
    missing = []
    for route in _api_routes(hub_app):
        for method in route.methods - {"HEAD", "OPTIONS"}:
            if (method, route.path) in EXEMPT or _is_bundle(route.path):
                continue
            if route.response_model is None:
                missing.append(f"{method} {route.path}")
    assert not missing, (
        f"routes without a response_model and without an exemption: {missing}. "
        f"Add a model, or an EXEMPT entry with the reason a model would break it."
    )


def test_the_exempt_table_names_only_live_modelless_routes(hub_app) -> None:
    live = {
        (method, route.path)
        for route in _api_routes(hub_app)
        for method in route.methods - {"HEAD", "OPTIONS"}
    }
    dead = [key for key in EXEMPT if key not in live]
    assert not dead, f"EXEMPT names routes that no longer exist: {dead}"

    modelled = [
        (method, route.path)
        for route in _api_routes(hub_app)
        for method in route.methods - {"HEAD", "OPTIONS"}
        if (method, route.path) in EXEMPT and route.response_model is not None
    ]
    assert not modelled, f"EXEMPT names routes that now have models: {modelled}"
