"""The OIDC provider: authorize, token, and the two published documents.

The relying parties are venona instances, whose client is
`venona/api/oauth.py`: endpoints DERIVED from the issuer, `client_secret_post`
form encoding, and claims read from a trusted exchange rather than a verified
signature (docs/adr/0141). The documents at the bottom exist for correctness
-- any standards-compliant relying party could verify -- not because anything
in this system fetches them.

The consent screen's security split applies to `/oauth/authorize`: an unknown
`client_id` or a `redirect_uri` that is not the derived one is answered on OUR
page and never by redirecting -- honouring an unvalidated redirect target is
what turns an authorization server into an open redirector.
"""

from __future__ import annotations

import secrets
import time
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from hub import billing_state, jwt, pages, security, signout
from hub.db.core import Conn
from hub.deps import get_db, session_email
from hub.repositories import auth_codes as codes_repo
from hub.repositories import instances as instances_repo
from hub.repositories import users as users_repo
from hub.schemas.oidc import Jwks, OpenIdConfiguration

router = APIRouter(tags=["oidc"])


def _derived_redirect_uri(instance: dict) -> str:
    """The ONE redirect URI an instance can have -- the same string its own
    `AuthConfig.redirect_uri` computes. Never stored, so it cannot drift."""
    return instance["public_url"].rstrip("/") + "/api/auth/callback"


def _refuse(request: Request, message: str) -> HTMLResponse:
    return HTMLResponse(
        pages.error("Cannot sign you in", message, status=400, theme=pages.theme_of(request)),
        status_code=400,
    )


@router.get("/oauth/authorize")
def authorize(
    request: Request,
    response_type: str = "",
    client_id: str = "",
    redirect_uri: str = "",
    state: str = "",
    nonce: str = "",
    conn: Conn = Depends(get_db),
):
    # Client and redirect target first, and refusals are PAGES: nothing below
    # may redirect anywhere that is not the derived URI of a known instance.
    instance = instances_repo.by_client_id(conn, client_id) if client_id else None
    if instance is None or instance["status"] == "retired":
        return _refuse(
            request, "Unknown client. The instance that sent you here is not registered."
        )
    if redirect_uri != _derived_redirect_uri(instance):
        return _refuse(request, "The instance asked for a redirect target it does not own.")
    if response_type != "code" or not state or not nonce:
        return _refuse(request, "Malformed authorization request.")

    email = session_email(request, conn)
    if email is None:
        # To the portal's sign-in, carrying this whole request as `next` so the
        # Google leg lands the user right back here. Path+query only -- it must
        # survive `safe_next`.
        next_path = request.url.path + "?" + str(request.url.query)
        return RedirectResponse(f"/?next={quote(next_path, safe='')}", status_code=302)

    user = users_repo.by_email(conn, email)
    role = (
        users_repo.role_for(conn, user_id=user["id"], instance_id=instance["id"])
        if user is not None and user["status"] == "active"
        else None
    )
    if role is None:
        # Signed in, not a member (or not provisioned, or suspended): the
        # request-access page, one page for every reason, and NO code minted.
        return HTMLResponse(
            pages.request_access_form(
                email=email,
                instances=instances_repo.list_public(conn),
                signed_in=True,
                theme=pages.theme_of(request),
            )
        )

    code = secrets.token_urlsafe(32)
    codes_repo.mint(
        conn,
        code_hash=security.hash_token(code),
        user_id=user["id"],
        instance_id=instance["id"],
        nonce=nonce,
    )
    return RedirectResponse(
        f"{redirect_uri}?{urlencode({'code': code, 'state': state})}", status_code=302
    )


def _token_error(error: str, description: str, status: int = 400) -> JSONResponse:
    """RFC 6749 §5.2. The instance surfaces the description to its operator."""
    return JSONResponse(
        status_code=status,
        content={"error": error, "error_description": description},
        headers={"Pragma": "no-cache"},
    )


@router.get("/oauth/logout")
async def end_session(request: Request, conn: Conn = Depends(get_db)) -> RedirectResponse:
    """RP-initiated logout: an instance's sign-out lands here so the hub
    session dies with the instance session, and the browser arrives at the
    hub's own sign-in page -- signed out everywhere, one entry point.
    "Everywhere" includes the user's OTHER instances: the fan-out in
    `hub/signout.py` ends their sessions server-side, so signing out of one
    instance is signing out of all of them.

    GET, like every end_session_endpoint: the instance sends a top-level
    navigation, not a fetch. The worst a forged request can do is sign
    someone out, which is a nuisance and not a breach, and the same
    trade-off every OIDC provider's end-session URL makes. No redirect
    parameter on purpose -- accepting one is how an authorization server
    becomes an open redirector, and "/" already shows sign-in to a browser
    with no session.
    """
    await signout.end_all_sessions(request, conn, session_email(request, conn))
    response = RedirectResponse("/", status_code=302)
    response.delete_cookie(security.COOKIE_NAME, path="/")
    return response


@router.post("/oauth/token")
def token(
    request: Request,
    grant_type: str = Form(default=""),
    code: str = Form(default=""),
    client_id: str = Form(default=""),
    client_secret: str = Form(default=""),
    redirect_uri: str = Form(default=""),
    conn: Conn = Depends(get_db),
):
    """client_secret_post, form-encoded -- exactly what the instance sends.

    Sync `def` on purpose: `Form()` needs python-multipart either way, and the
    database work stays off the event loop (the instance's own OAuth routes
    record the same reasoning).
    """
    if grant_type != "authorization_code":
        return _token_error("unsupported_grant_type", "only authorization_code is supported")

    instance = instances_repo.by_client_id(conn, client_id) if client_id else None
    # Retired is unknown here exactly as it is at /oauth/authorize (a code minted
    # seconds before retirement must not still redeem to a fresh id_token). The
    # two endpoints have to agree on what retired means, or retirement only closes
    # the front door.
    if instance is None or instance["status"] == "retired":
        return _token_error("invalid_client", "unknown client_id", status=401)
    presented = security.hash_token(client_secret)
    if not secrets.compare_digest(presented, instance["oidc_client_secret_hash"]):
        return _token_error("invalid_client", "wrong client secret", status=401)
    if redirect_uri != _derived_redirect_uri(instance):
        return _token_error("invalid_grant", "redirect_uri mismatch")

    # The atomic single-use redeem burns the code even when a later check
    # refuses -- a code that reached the wrong hands is spent, not retryable.
    row = codes_repo.redeem(conn, security.hash_token(code)) if code else None
    if row is None:
        return _token_error("invalid_grant", "code is unknown, expired, or already used")
    if row["instance_id"] != instance["id"]:
        return _token_error("invalid_grant", "code was minted for a different client")

    # Membership re-read AT REDEMPTION: revoked (or suspended) between
    # authorize and token means no id_token, not a stale one.
    user = users_repo.by_id(conn, row["user_id"])
    if user is None or user["status"] != "active":
        return _token_error("invalid_grant", "the account can no longer sign in")
    role = users_repo.role_for(conn, user_id=user["id"], instance_id=instance["id"])
    if role is None:
        return _token_error("invalid_grant", "the account is no longer a member")

    # Entitlement is re-read HERE for the same reason membership is: a
    # subscription that lapsed between authorize and redemption must not mint a
    # token that says otherwise. This is the fast path -- the mgmt push is what
    # carries a change to a reader who is already signed in.
    entitlement = billing_state.for_seat(conn, user_id=user["id"], instance_id=instance["id"])

    settings = request.app.state.settings
    id_token = jwt.mint_id_token(
        jwt.load_signing_key(settings.oidc_signing_key),
        issuer=settings.public_url,
        audience=client_id,
        subject=user["id"],
        email=user["email"],
        name=user["display_name"],
        nonce=row["nonce"],
        role=role,
        entitlement=entitlement["status"],
        entitled_through=entitlement["through"],
        billing_notice=entitlement["notice"],
        billing_notice_at=entitlement["notice_at"],
        now=time.time(),
        picture=user["picture_url"],
    )
    return JSONResponse(
        content={
            "id_token": id_token,
            "access_token": secrets.token_urlsafe(32),
            "token_type": "Bearer",
            "expires_in": jwt.ID_TOKEN_TTL_SECONDS,
        },
        headers={"Pragma": "no-cache"},
    )


@router.get("/.well-known/openid-configuration", response_model=OpenIdConfiguration)
def openid_configuration(request: Request) -> dict:
    return jwt.openid_configuration(request.app.state.settings.public_url)


@router.get("/.well-known/jwks.json", response_model=Jwks)
def jwks(request: Request) -> dict:
    return jwt.jwks(jwt.load_signing_key(request.app.state.settings.oidc_signing_key))
