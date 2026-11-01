"""The hub's own Google login leg.

The instance's `api/oauth.py` pattern, replicated: a server-to-server code
exchange over a client we construct with `trust_env=False` (no proxy can
insert itself) against a CONSTANT endpoint -- which is what justifies reading
the id_token without verifying its signature. The claims come from a direct
TLS response from the token endpoint we chose, not from an assertion someone
handed us.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from hub.config import Settings

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})
EXCHANGE_TIMEOUT = 15.0

#: Test seam. The offline suite sets an `httpx.MockTransport` here; None means
#: the real network. A module attribute rather than a parameter because the
#: caller (the portal router) has no business choosing transports.
EXCHANGE_TRANSPORT: httpx.AsyncBaseTransport | None = None


class GoogleError(RuntimeError):
    """The exchange or the claims failed. The message is operator-safe."""


@dataclass(frozen=True)
class GoogleIdentity:
    email: str
    subject: str
    display_name: str | None
    #: The `picture` claim, https-only. None when Google omits it.
    picture: str | None


def redirect_uri(settings: Settings) -> str:
    return f"{settings.public_url}/auth/callback"


def authorize_url(settings: Settings, *, state: str, nonce: str) -> str:
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri(settings),
        "response_type": "code",
        # `profile` as well as `email`: the chooser greets people by name, and
        # without the scope Google omits the `name` claim entirely.
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}"


def _decode_without_verification(id_token: str) -> dict:
    try:
        payload = id_token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except Exception as exc:
        raise GoogleError("Google returned an id_token that does not parse") from exc


async def identity_for_code(settings: Settings, *, code: str, nonce: str) -> GoogleIdentity:
    async with httpx.AsyncClient(
        timeout=EXCHANGE_TIMEOUT, trust_env=False, transport=EXCHANGE_TRANSPORT
    ) as client:
        response = await client.post(
            GOOGLE_TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": redirect_uri(settings),
                "grant_type": "authorization_code",
            },
            headers={"Accept": "application/json"},
        )
    if response.status_code != 200:
        raise GoogleError(f"Google's token endpoint answered {response.status_code}")
    id_token = str(response.json().get("id_token") or "")
    if not id_token:
        raise GoogleError("Google's token response carried no id_token")

    claims = _decode_without_verification(id_token)
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise GoogleError(f"unexpected issuer {claims.get('iss')!r}")
    if claims.get("aud") != settings.google_client_id:
        raise GoogleError("the id_token was minted for a different client")
    if str(claims.get("nonce") or "") != nonce:
        raise GoogleError("the id_token does not answer the login we started")
    if not claims.get("email_verified"):
        raise GoogleError("Google reports the email as unverified")
    email = str(claims.get("email") or "").strip().lower()
    if not email:
        raise GoogleError("the id_token carried no email")

    picture = str(claims.get("picture") or "").strip()
    return GoogleIdentity(
        email=email,
        subject=str(claims.get("sub") or email),
        display_name=str(claims.get("name") or "").strip() or None,
        # Scheme-checked because this string ends up in an <img src> on the
        # instances; anything not plainly https is dropped, not sanitised.
        picture=picture if picture.startswith("https://") else None,
    )
