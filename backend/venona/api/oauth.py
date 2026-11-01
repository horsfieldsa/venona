"""Google OIDC, authorization-code flow, server side.

WHY THERE IS NO JWT SIGNATURE VERIFICATION HERE, AND WHY THAT IS NOT A SHORTCUT
-----------------------------------------------------------------------------
The `id_token` is not accepted from the browser. It is obtained by POSTing the
authorization code straight to Google's token endpoint over TLS, authenticating
ourselves with the client secret. Google documents exactly this case: when the
token comes to you over a direct, intermediary-free HTTPS channel that you
authenticated to, you may skip verifying its signature, because the channel has
already established where it came from.

That carve-out is CONDITIONAL, and both conditions are enforced below rather
than assumed:

  * `trust_env=False` on the client. httpx honours `HTTPS_PROXY`/`ALL_PROXY`
    from the environment by default, and a proxy is precisely the intermediary
    the carve-out excludes. In a container the environment comes from a file
    someone else wrote.
  * The token endpoint is a constant. No `.well-known` discovery -- that would
    let a remote document choose where we send the client secret.

The claims below (`iss`, `aud`, `exp`, `nonce`, `email_verified`, `email`) are
all forgeable by anyone who can hand us a token. The ONLY thing making them
trustworthy is provenance, and provenance is a property of this call graph, not
of the data. Hence `_claims_from_trusted_exchange`: the name is the warning.
If venona ever accepts an `id_token` from a client -- Google One Tap is the
obvious way that happens -- this file is a total bypass and must grow real
signature verification first. `tests/test_auth.py` pins that no route takes an
`id_token` parameter.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from venona.api.security import AuthConfig

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

GOOGLE_ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})


def _auth_endpoint(cfg: AuthConfig) -> str:
    """Derived from the configured issuer, never discovered. A hub issuer's
    endpoints are fixed paths on its origin -- the same no-`.well-known` rule
    the Google constants embody: no remote document chooses where the client
    secret goes."""
    return f"{cfg.hub_issuer}/oauth/authorize" if cfg.hub_issuer else GOOGLE_AUTH_ENDPOINT


def _token_endpoint(cfg: AuthConfig) -> str:
    return f"{cfg.hub_issuer}/oauth/token" if cfg.hub_issuer else GOOGLE_TOKEN_ENDPOINT


def _valid_issuers(cfg: AuthConfig) -> frozenset[str]:
    return frozenset({cfg.hub_issuer}) if cfg.hub_issuer else GOOGLE_ISSUERS


EXCHANGE_TIMEOUT = 15.0


class OAuthError(Exception):
    """The login could not be completed. The message is shown to the operator."""


def authorize_url(cfg: AuthConfig, *, state: str, nonce: str) -> str:
    return (
        _auth_endpoint(cfg)
        + "?"
        + urlencode(
            {
                "client_id": cfg.google_client_id or "",
                "redirect_uri": cfg.redirect_uri,
                "response_type": "code",
                "scope": "openid email",
                "state": state,
                "nonce": nonce,
                # One principal, so there is nothing to switch between -- but an
                # account chooser beats silently reusing whichever Google account
                # the browser happens to be signed into.
                "prompt": "select_account",
            }
        )
    )


def _decode_without_verification(id_token: str) -> dict[str, Any]:
    """Read the claims. Trustworthy ONLY because of how the token was obtained
    -- see this module's docstring."""
    try:
        payload = id_token.split(".")[1]
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        claims = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        raise OAuthError(f"could not read the id_token: {exc}") from exc
    if not isinstance(claims, dict):
        raise OAuthError("id_token payload was not an object")
    return claims


async def _claims_from_trusted_exchange(cfg: AuthConfig, *, code: str) -> dict[str, Any]:
    """Exchange the code for claims. Module-private on purpose."""
    async with httpx.AsyncClient(timeout=EXCHANGE_TIMEOUT, trust_env=False) as client:
        try:
            response = await client.post(
                _token_endpoint(cfg),
                data={
                    "code": code,
                    "client_id": cfg.google_client_id or "",
                    "client_secret": cfg.google_client_secret or "",
                    "redirect_uri": cfg.redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise OAuthError(f"could not reach the identity provider: {exc}") from exc

    if response.status_code != 200:
        # Google's error bodies are short and genuinely diagnostic
        # ("redirect_uri_mismatch" is the one everybody hits first).
        raise OAuthError(
            f"the identity provider refused the code exchange "
            f"({response.status_code}): {response.text[:300]}"
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise OAuthError(f"the token response was not JSON: {exc}") from exc

    id_token = body.get("id_token")
    if not id_token:
        raise OAuthError("the token response carried no id_token")
    return _decode_without_verification(id_token)


@dataclass(frozen=True)
class OidcIdentity:
    """Who Google says this is.

    `issuer` + `subject` is THE identity, not the address: an address can be
    reassigned inside a tenant and a subject cannot, which is why migration 0002
    keys `ux_users_oidc` on the pair. This carried only the email until `users`
    existed, so both were verified here and then discarded -- and
    `users.oidc_issuer` / `oidc_subject` were columns nothing could ever fill.

    `display_name` is None when the provider omitted the claim, rather than "",
    because `record_oidc_identity` writes it only when it is truthy: a login
    without the claim must not blank a name an earlier one established.
    """

    email: str
    issuer: str
    subject: str
    display_name: str | None
    #: The hub's membership assertion for THIS instance -- `admin` or
    #: `reader`. None from Google, which asserts identity only; a hub id_token
    #: always carries it, because the hub refuses to mint a code for a
    #: non-member (docs/adr/0141).
    role: str | None = None
    #: The hub's entitlement assertion for THIS instance, and the date it runs
    #: to. None from Google, and None from a hub that has nothing to say -- which
    #: is NOT the same as `none`, meaning "never subscribed". An absent claim
    #: leaves whatever is stored alone, so a hub deployed before billing cannot
    #: wipe an entitlement, and the mgmt push remains the authority.
    #:
    #: The FAST PATH. The push is what carries a change to somebody already
    #: signed in, but on a FIRST login there is no row for it to land on and it
    #: 404s -- so without these claims a reader who has just paid is told they
    #: have not subscribed until the next poll.
    entitlement: str | None = None
    entitled_through: str | None = None
    #: What to TELL them, which is a different question from what they may read:
    #: a first card decline leaves somebody fully entitled and changes only the
    #: banner. Carried on the same claim set for one reason -- the hub's push
    #: counts a 404 from an instance as delivered, so a reader who has never
    #: signed in here arrives with the notice or never gets it at all.
    billing_notice: str | None = None
    billing_notice_at: str | None = None
    #: The `picture` claim, https-only -- the account menu's avatar. None when
    #: the provider omitted it, so an established picture is preserved.
    picture: str | None = None


async def identity_for_code(cfg: AuthConfig, *, code: str, nonce: str) -> OidcIdentity:
    """The whole flow's payload: a verified identity, or an exception."""
    claims = await _claims_from_trusted_exchange(cfg, code=code)

    issuer = str(claims.get("iss") or "")
    if issuer not in _valid_issuers(cfg):
        raise OAuthError(f"unexpected issuer {claims.get('iss')!r}")
    if str(claims.get("aud") or "") != (cfg.google_client_id or ""):
        raise OAuthError("id_token was issued for a different client")
    if str(claims.get("nonce") or "") != nonce:
        # Binds this token to the login WE started.
        raise OAuthError("nonce mismatch -- start the login again")
    if not claims.get("email_verified"):
        raise OAuthError("that Google account has no verified email address")

    email = str(claims.get("email") or "").strip().lower()
    if not email:
        raise OAuthError("id_token carried no email address")

    # `sub` is REQUIRED of an OIDC id_token, but this is the pair a unique index
    # is keyed on, so an absent one must not become the empty string shared by
    # every provider-less row. Falling back to the address keeps the pair
    # distinct per person, which is the property the index is protecting.
    subject = str(claims.get("sub") or "") or email
    role = str(claims.get("venona_role") or "").strip() or None
    if cfg.hub_issuer and role not in ("admin", "reader"):
        # The hub never mints a code for a non-member, so a hub id_token
        # without a legible role is a broken hub, not an open door.
        raise OAuthError(f"the hub's id_token carried no usable venona_role ({role!r})")
    picture = str(claims.get("picture") or "").strip()
    # Only a recognised word. A hub that sends something this build has never
    # heard of is treated as having said nothing, which leaves the stored value
    # alone rather than writing a status no gate can interpret.
    entitlement = str(claims.get("venona_entitlement") or "").strip() or None
    if entitlement not in ("none", "trialing", "active", "past_due", "lapsed", "comped"):
        entitlement = None
    return OidcIdentity(
        email=email,
        issuer=issuer,
        subject=subject,
        display_name=str(claims.get("name") or "").strip() or None,
        role=role if cfg.hub_issuer else None,
        entitlement=entitlement if cfg.hub_issuer else None,
        entitled_through=(
            (str(claims.get("venona_entitled_through") or "").strip() or None)
            if cfg.hub_issuer
            else None
        ),
        billing_notice=(
            (str(claims.get("venona_billing_notice") or "").strip() or None)
            if cfg.hub_issuer
            else None
        ),
        billing_notice_at=(
            (str(claims.get("venona_billing_notice_at") or "").strip() or None)
            if cfg.hub_issuer
            else None
        ),
        # Scheme-checked because it lands in an <img src>: anything not
        # plainly https is dropped rather than sanitised.
        picture=picture if picture.startswith("https://") else None,
    )
