"""OAuth 2.1 endpoints for agents. Contract C12.

Three endpoints and two metadata documents (the documents are mounted at the root
in `main.py`, because RFC 9728 fixes their paths there):

    POST /api/oauth/register    RFC 7591 dynamic client registration
    GET  /api/oauth/authorize   the consent screen, and the redirect back
    POST /api/oauth/token       code + PKCE verifier -> an `mcp_tokens` row

WHY THE CONSENT SCREEN IS SERVER-RENDERED HTML. Everything else the browser
touches is the SPA, and this deliberately is not. The page has to be readable and
trustworthy at the moment an agent is asking for access to the corpus, and a
route inside the SPA would mean the approval depends on the bundle loading, the
router resolving and a store hydrating -- three things that can fail into a blank
page with an Approve button somewhere off-screen. It is also the one page whose
content includes ATTACKER-SUPPLIED TEXT (the client's own name), so keeping it in
one small, escaped template beats spreading that concern across components.

WHAT MAKES THIS SAFE IS THE SESSION, NOT THE CLIENT. Registration is
unauthenticated (RFC 7591, and it grants nothing). `authorize` requires a signed-in
human, and the token it eventually mints inherits THAT user's role -- so an agent
registered by anyone gets, at most, exactly what the person who approved it has.
"""

from __future__ import annotations

import html
from typing import Any
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from venona.api import authz
from venona.api.deps import get_db
from venona.api.middleware import content_security_policy
from venona.api.schemas.oauth import ClientRegistration, RegisteredClient, TokenResponse
from venona.api.security import Principal, get_auth_config
from venona.db import core as db
from venona.services import oauth_service, token_service, user_service

router = APIRouter(prefix="/oauth", tags=["oauth"])


def _issuer() -> str:
    """Fixed, from configuration, never derived from the request.

    The same rule as `AuthConfig.redirect_uri` and for the same reasons: behind a
    TLS-terminating proxy `request.url` renders `http://` unless forwarded headers
    are trusted, and deriving it from the `Host` header takes the value from the
    caller. An issuer taken from the caller is an issuer an attacker chooses.
    """
    return get_auth_config().public_url.rstrip("/")


def _now() -> str:
    from venona.pipeline.types import utc_now_iso

    return utc_now_iso()


def _error(code: str, message: str, status: int = 400) -> JSONResponse:
    """RFC 6749 §5.2 shape. `error` is what a client library branches on."""
    return JSONResponse({"error": code, "error_description": message}, status_code=status)


# ── registration ─────────────────────────────────────────────────────


@router.post("/register", response_model=RegisteredClient, status_code=201)
def register(
    payload: ClientRegistration, conn: db.Conn = Depends(get_db)
) -> RegisteredClient | JSONResponse:
    """RFC 7591. Unauthenticated, and it grants nothing.

    What a client gets is an identifier it can use to ASK. The asking ends at a
    consent screen in front of a signed-in human who is shown the name below and
    the redirect URI beside it -- the name being the easy thing to lie about and
    the destination being the thing with consequences.

    Rate-limited as a WRITE by `bucket_for`, which is the right bucket: it is not
    a credential path (nothing here can be brute-forced into access) but it does
    insert a row.
    """
    from venona.pipeline.types import SystemIdGen

    try:
        client = oauth_service.register(
            conn,
            client_id=f"client:{SystemIdGen().uuid7()}",
            client_name=payload.client_name,
            redirect_uris=list(payload.redirect_uris),
            now=_now(),
        )
    except oauth_service.OAuthError as exc:
        return _error(exc.code, exc.message)

    return RegisteredClient(
        client_id=client.client_id,
        client_name=client.client_name,
        redirect_uris=list(client.redirect_uris),
        # Public client: there is no secret, and saying so explicitly is better
        # than omitting the field and letting a client guess.
        token_endpoint_auth_method="none",
    )


# ── the consent screen ───────────────────────────────────────────────


# THE TOKEN VALUES ARE COPIED, AND THE COPY IS THE POINT OF THIS COMMENT.
#
# `web/src/styles/tokens.css` is the source; the names below are its names and
# the pairs are its pairs. A `<link>` to the built stylesheet was the obvious
# alternative and is wrong twice: Vite hashes that filename, and this page has to
# render on the day the SPA build is broken or absent -- it is the screen someone
# reaches while wiring up a client, which is exactly when the rest of the app may
# not be working. It is nine values and is guarded by `tests/test_oauth.py`.
#
# `color-scheme: light dark` plus `light-dark()` is the WHOLE theming design, not
# an approximation of it: tokens.css carries one palette and lets the pairs
# resolve, and `[data-theme]` sets `color-scheme` and nothing else. So the three
# rules below are the entire switch, and the page follows the reader wherever
# they chose -- from the `venona.theme` cookie, read server-side because this
# page carries no script and is not going to start (docs/adr/0177).
_PAGE = """<!doctype html>
<html lang="en"{theme_attr}><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Authorize {name} · Venona</title>
<link rel="icon" href="/favicon.ico" sizes="32x32">
<link rel="icon" type="image/svg+xml" href="/venona-mark.svg">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="manifest" href="/manifest.json">
<style>
  :root[data-theme='light'] {{ color-scheme: light; }}
  :root[data-theme='dark'] {{ color-scheme: dark; }}
  :root {{
    color-scheme: light dark;
    --surface-base: light-dark(#e4e9ee, #0a0d11);
    --surface-raised: light-dark(#f7f9fb, #141920);
    --border-subtle: light-dark(#c8d0d9, #1a2029);
    --text-primary: light-dark(#0a0d11, #eef2f5);
    --text-body: light-dark(#212934, #c8d0d9);
    --text-tertiary: light-dark(#4e5a68, #8794a3);
    --accent-primary: light-dark(#184953, #3fbdd6);
    --text-inverse: light-dark(#ffffff, #05070a);
    --radius-lg: 6px;
    --font-ui: 'JetBrains Mono Variable', ui-monospace, SFMono-Regular, Menlo, monospace;
    --font-prose: 'Inter Variable', ui-sans-serif, system-ui, -apple-system, sans-serif;
  }}
  body {{ margin:0; min-height:100vh; display:grid; place-items:center;
         padding:1rem; box-sizing:border-box;
         background:var(--surface-base); color:var(--text-primary);
         font:15px/1.55 var(--font-prose); }}
  main {{ width:min(30rem,100%); box-sizing:border-box;
         background:var(--surface-raised); border:1px solid var(--border-subtle);
         border-radius:var(--radius-lg); padding:1.75rem; }}
  h1 {{ font-size:1.05rem; margin:0 0 1rem; font-weight:600; }}
  dl {{ margin:0 0 1.5rem; display:grid; grid-template-columns:auto 1fr;
       gap:.4rem 1rem; font-size:.86rem; }}
  dt {{ color:var(--text-tertiary); }}
  dd {{ margin:0; word-break:break-all; font-family:var(--font-ui); }}
  p {{ color:var(--text-body); font-size:.86rem; }}
  .row {{ display:flex; gap:.6rem; margin-top:1.5rem; }}
  button {{ flex:1; padding:.6rem 1rem; border-radius:var(--radius-lg); font:inherit;
           font-size:.9rem; cursor:pointer; border:1px solid var(--border-subtle);
           min-height:44px; }}
  .no {{ background:transparent; color:var(--text-body); }}
  .yes {{ background:var(--accent-primary); border-color:var(--accent-primary);
         color:var(--text-inverse); font-weight:600; }}
  /* One column under the narrowest tier. Two 44px targets plus a gap do not fit
     across a 320px screen without the labels wrapping mid-word, and a consent
     screen is the last place to make Cancel and Authorize hard to tell apart. */
  @media (max-width: 22rem) {{ .row {{ flex-direction:column; }} }}
</style></head>
<body><main>
  <h1>{heading}</h1>
  <dl>
    <dt>Sends you back to</dt><dd>{redirect}</dd>
    <dt>Signed in as</dt><dd>{email}</dd>
  </dl>
  <p>{blurb}</p>
  <form method="post" action="/api/oauth/authorize" class="row">
    {hidden}
    <button class="no" name="decision" value="deny" type="submit">Cancel</button>
    <button class="yes" name="decision" value="allow" type="submit">Authorize</button>
  </form>
</main></body></html>"""


#: The reader's choice, shared across every Venona property by a cookie scoped
#: to the registrable domain. Spelled the same in `web/src/stores/theme.ts`, every
#: `theme-boot.js` copy and `hub/hub/pages.py`, none of which can import it
#: from another; `tests/test_theme_boot.py` holds them equal. docs/adr/0177.
THEME_COOKIE = "venona.theme"


def _theme_attr(request: Request) -> str:
    """`data-theme` for the two explicit choices, nothing for System.

    The absence of the attribute is what makes the page follow the operating
    system, so `system` and an unrecognised value are the same answer here: no
    attribute, no third rule to write. The value is a reader's cookie and is
    never trusted past this comparison.
    """
    choice = request.cookies.get(THEME_COOKIE)
    return f' data-theme="{choice}"' if choice in ("light", "dark") else ""


def _consent_headers(redirect_uri: str) -> dict[str, str]:
    """The consent page's own CSP, naming where its form is allowed to end up.

    Chrome checks `form-action` against every hop of the submission, and this
    form's second hop is the 302 to the client's callback -- so under the
    site-wide `form-action 'self'` the Authorize button silently does nothing.
    See `middleware.content_security_policy`.

    REBUILT FROM PARTS, never interpolated: `redirect_uri` is attacker-supplied,
    and a header carrying a raw one is a header-injection primitive. Taking only
    scheme, host and port means nothing with a space, semicolon or newline in it
    can reach the policy. It is also the exact destination shown on the page
    below, so the widening is never larger than what the human approves.
    """
    parts = urlsplit(redirect_uri)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return {}
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    port = f":{parts.port}" if parts.port else ""
    return {
        "content-security-policy": content_security_policy(f"'self' {parts.scheme}://{host}{port}")
    }


def _consent_copy(scope: str) -> dict[str, str]:
    """What the consent screen says, keyed on what is being GRANTED.

    The scopes are read by different people and all of them are readers, so no
    branch spends a word on internals. `mcp` is approved by somebody connecting
    an assistant; `annotate` by somebody pairing the browser extension; a client
    that asks for both gets a page naming both, because a screen that describes
    less than it grants is worse than no screen. Each says what the thing will
    be able to do, in the reader's words, and names Settings, Agents as where a
    connection is ended -- a URL path or a shell command means nothing to the
    person looking at this page, and signing in is how anybody reaches it.

    `{name}` survives into the heading for `authorize` to fill with the ESCAPED
    client name.
    """
    days = oauth_service.TOKEN_TTL_SECONDS // 86400
    names = set(scope.split())
    if names == {"annotate", "mcp"}:
        return {
            "heading": "{name} wants to read what Venona knows",
            "blurb": (
                "It will be able to look a name up, ask what a program connects to, read "
                "the sentence a definition came from, and show what Venona knows about "
                "the names on pages you visit. It can read, and only read: nothing it "
                "asks for can start a run, spend anything, or change what Venona holds. "
                f"The connection ends on its own after {days} days, and you can end it "
                "sooner under Settings, Agents."
            ),
        }
    if scope == "annotate":
        return {
            "heading": "{name} wants to highlight Venona's reading on pages you visit",
            "blurb": (
                "It can show what Venona knows about the names on articles you read, "
                "and see which sites Venona covers. It cannot change anything, start "
                f"anything, or spend anything. The connection ends on its own after "
                f"{days} days; reconnect any time."
            ),
        }
    return {
        "heading": "{name} wants to read what Venona knows",
        "blurb": (
            "It will be able to look a name up, ask what a program connects to, and "
            "read the sentence a definition came from. It can read, and only read: "
            "nothing it asks for can start a run, spend anything, or change what "
            "Venona holds. The connection ends on its own after "
            f"{days} days, and you can end it sooner under Settings, Agents."
        ),
    }


def _hidden(fields: dict[str, str]) -> str:
    return "".join(
        f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">'
        for k, v in fields.items()
    )


def _redirect_with(uri: str, params: dict[str, str]) -> RedirectResponse:
    joiner = "&" if "?" in uri else "?"
    return RedirectResponse(f"{uri}{joiner}{urlencode(params)}", status_code=302)


@router.get("/authorize")
def authorize(
    request: Request,
    client_id: str = "",
    redirect_uri: str = "",
    response_type: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "",
    scope: str = "mcp",
    state: str = "",
    resource: str = "",
    conn: db.Conn = Depends(get_db),
) -> Any:
    """Show the consent screen. REQUIRES A SESSION -- it is not in `EXEMPT_PATHS`.

    So an unauthenticated agent-driven browser lands on a 401 from the middleware
    rather than here, which is correct: the SPA's own 401 handling sends the human
    to sign in, and they arrive back at this URL with a cookie.

    TWO CLASSES OF FAILURE, ANSWERED DIFFERENTLY, and the split is the security
    property rather than a nicety:

      * A bad `client_id` or a `redirect_uri` that is not registered for it is
        answered HERE, as an error page, and NEVER by redirecting. Redirecting to
        an unvalidated URI is how an authorization server becomes an open
        redirector, and it would hand the `state` to whoever asked.
      * Anything else -- an unsupported `response_type`, a missing PKCE challenge
        -- is answered by redirecting to the VALIDATED uri with an `error`
        parameter, which is what the client is waiting for (RFC 6749 §4.1.2.1).
    """
    client = oauth_service.get_client(conn, client_id)
    if client is None:
        return HTMLResponse(
            "<h1>Unknown client</h1><p>That client_id is not registered here.</p>",
            status_code=400,
        )
    if redirect_uri not in client.redirect_uris:
        return HTMLResponse(
            "<h1>Redirect URI mismatch</h1><p>That redirect_uri is not registered "
            "for this client. Nothing has been approved.</p>",
            status_code=400,
        )

    def refuse(code: str, description: str) -> RedirectResponse:
        params = {"error": code, "error_description": description}
        if state:
            params["state"] = state
        return _redirect_with(redirect_uri, params)

    if response_type != "code":
        return refuse("unsupported_response_type", "only response_type=code is supported")
    if code_challenge_method != oauth_service.CODE_CHALLENGE_METHOD:
        return refuse(
            "invalid_request",
            "code_challenge_method must be S256; `plain` is forbidden by OAuth 2.1",
        )
    if not code_challenge:
        return refuse("invalid_request", "PKCE is required: send a code_challenge")
    # RFC 6749 §3.3: a space-delimited LIST. A client that reads
    # `scopes_supported` off the discovery document asks for every name there,
    # so refusing anything but one known value refused the Claude Code SDK
    # outright. `grantable` drops what this server does not recognise and the
    # granted set is what the human then approves.
    granted = " ".join(authz.grantable(scope))
    if not granted:
        return refuse("invalid_scope", f"unknown scope {scope!r}")
    # RFC 8707. Refused HERE rather than at the token endpoint so the human is
    # never shown a consent screen for access that cannot be granted: a client
    # asking for a resource this server does not serve is misconfigured, and the
    # only honest place to say so is before somebody approves something.
    if resource and not oauth_service.resource_matches(resource, issuer=_issuer()):
        return refuse("invalid_target", "this server does not issue access for that resource")

    principal: Principal | None = getattr(request.state, "principal", None)
    # ESCAPED. `client_name` is attacker-supplied -- it arrives at registration
    # with no authentication at all -- and this is the one page in the app that
    # renders it. Escaping happens BEFORE the value enters `.format`: values are
    # never re-scanned for placeholders, so braces inside a client name stay
    # literal.
    name = html.escape(client.client_name)
    copy = _consent_copy(granted)
    return HTMLResponse(
        headers=_consent_headers(redirect_uri),
        content=_PAGE.format(
            theme_attr=_theme_attr(request),
            name=name,
            heading=copy["heading"].format(name=name),
            blurb=copy["blurb"],
            redirect=html.escape(redirect_uri),
            email=html.escape(principal.email if principal else "unknown"),
            hidden=_hidden(
                {
                    "client_id": client_id,
                    "redirect_uri": redirect_uri,
                    "code_challenge": code_challenge,
                    "scope": granted,
                    "state": state,
                }
            ),
        ),
    )


@router.post("/authorize")
def authorize_decision(
    request: Request,
    decision: str = Form(""),
    client_id: str = Form(""),
    redirect_uri: str = Form(""),
    code_challenge: str = Form(""),
    scope: str = Form("mcp"),
    state: str = Form(""),
    conn: db.Conn = Depends(get_db),
) -> Any:
    """The human answered. Mint a code, or send them back empty-handed.

    Re-validates `client_id` and `redirect_uri` against the database rather than
    trusting the hidden fields, even though they were validated on the way in: the
    form is a round trip through a browser and the fields come back from the
    client. Anything that arrives from outside is checked where it is used.
    """
    client = oauth_service.get_client(conn, client_id)
    if client is None or redirect_uri not in client.redirect_uris:
        return HTMLResponse("<h1>Unknown client</h1>", status_code=400)

    if decision != "allow":
        params = {"error": "access_denied", "error_description": "the request was declined"}
        if state:
            params["state"] = state
        return _redirect_with(redirect_uri, params)

    principal: Principal | None = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else None
    if not user_id:
        # A root-bearer or `via="disabled"` principal has no user row, and the
        # token minted from this code would have no owner to inherit a role from.
        # Refusing is the only honest answer: `mcp_tokens.user_id` is NOT NULL in
        # effect (`token_service.verify` refuses an orphan), so the alternative is
        # minting a credential that can never be used.
        return HTMLResponse(
            "<h1>No account</h1><p>This session is not tied to a user, so there is "
            "nobody for the token to belong to. Sign in with your own account.</p>",
            status_code=400,
        )

    now = _now()
    code = oauth_service.new_code()
    oauth_service.issue_code(
        conn,
        code=code,
        client_id=client_id,
        user_id=user_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        scope=" ".join(authz.grantable(scope)) or "mcp",
        now=now,
        expires_at=_iso_in(oauth_service.CODE_TTL_SECONDS),
    )
    params = {"code": code}
    if state:
        params["state"] = state
    return _redirect_with(redirect_uri, params)


def _iso_in(seconds: int) -> str:
    from datetime import UTC, datetime, timedelta

    from venona import timeutil

    return timeutil.format_seconds(datetime.now(UTC) + timedelta(seconds=seconds))


# ── the token endpoint ───────────────────────────────────────────────


@router.post("/token")
def token(
    grant_type: str = Form(""),
    code: str = Form(""),
    redirect_uri: str = Form(""),
    client_id: str = Form(""),
    code_verifier: str = Form(""),
    resource: str = Form(""),
    conn: db.Conn = Depends(get_db),
) -> Any:
    """Exchange a code for an ordinary `mcp_tokens` row.

    UNAUTHENTICATED, and correctly so: this is a public client, so there is no
    secret to present. What authenticates the exchange is possession of the code
    AND the PKCE verifier that matches the challenge recorded when the human
    approved -- which is precisely the pair a stolen code does not include.

    The result is a `vmcp_` token indistinguishable from one minted at the CLI.
    `token_service.verify` is still the only read path, `venona mcp token revoke`
    still works on it, and it is still admissible only on the paths its scope
    names.
    """
    from venona.pipeline.types import SystemIdGen

    if grant_type != "authorization_code":
        return _error("unsupported_grant_type", "only authorization_code is supported")
    # Checked again here, and not only at `authorize`, because the two requests
    # are independent: nothing carries the earlier answer forward, and a client
    # that names a different audience on the exchange than the one a human
    # approved for must not be handed a token that says otherwise.
    if resource and not oauth_service.resource_matches(resource, issuer=_issuer()):
        return _error("invalid_target", "this server does not issue access for that resource")

    now = _now()
    try:
        row = oauth_service.redeem(
            conn,
            code=code,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_verifier=code_verifier,
            now=now,
        )
    except oauth_service.OAuthError as exc:
        return _error(exc.code, exc.message)

    owner = user_service.get(conn, row["user_id"])
    if owner is None or not owner.active:
        # Approved, then suspended before the exchange. Refusing here means a
        # revocation that lands mid-flow is honoured rather than racing it.
        return _error("invalid_grant", "that authorization code is not usable")

    client = oauth_service.get_client(conn, row["client_id"])
    expires_at = _iso_in(oauth_service.TOKEN_TTL_SECONDS)
    minted, secret = token_service.mint(
        conn,
        label=f"{client.client_name if client else row['client_id']} (oauth)",
        token_id=f"mcp_{SystemIdGen().uuid7()}",
        now=now,
        user_id=owner.id,
        scope=row["scope"],
        expires_at=expires_at,
    )
    token_service.attach_to_client(conn, client_id=row["client_id"], token_id=minted.id)
    oauth_service.touch_client(conn, row["client_id"], now=now)

    return JSONResponse(
        TokenResponse(
            access_token=secret,
            token_type="Bearer",
            expires_in=oauth_service.TOKEN_TTL_SECONDS,
            scope=row["scope"],
        ).model_dump(),
        # RFC 6749 §5.1: a token response must never be cached.
        #
        # `Cache-Control` is NOT set here even though the RFC names it, because
        # `SecurityHeadersMiddleware` already stamps `no-store` on every non-SSE
        # response app-wide -- setting it again produced a literal
        # `cache-control: no-store, no-store`, which is valid and looks like a bug.
        # `Pragma` is ours: it is the HTTP/1.0 half and nothing else sets it.
        headers={"Pragma": "no-cache"},
    )
