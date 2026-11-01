"""OAuth 2.1 authorization-server state: clients and authorization codes.

The only write path for `oauth_clients` and `oauth_codes`, for the reason
`config_service` and `user_service` are the only write paths for theirs.

WHAT THIS DELIBERATELY DOES NOT OWN: access tokens. An authorization code is
exchanged for an ordinary `mcp_tokens` row through `token_service.mint`, so there
is one credential type, one verification path and one revocation story. See
`db/migrations/0003_oauth.sql`.

OAuth 2.1 RATHER THAN 2.0, which is a set of specific refusals rather than a
version number:

  * PKCE is REQUIRED, and `S256` only. `plain` is permitted by RFC 7636 and
    forbidden by 2.1 -- with `plain` the verifier is the challenge, so it travels
    in the same browser redirect and protects nothing.
  * No implicit grant. The only response type is `code`.
  * No password grant.
  * Redirect URIs are compared by EXACT STRING EQUALITY. Not prefix, not origin,
    not "starts with" -- every relaxation of that comparison is a documented way
    for an authorization code to arrive somewhere the operator did not approve.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from typing import Any

from venona.api import authz
from venona.api.security import hash_token
from venona.db import core as db
from venona.db.repositories import oauth as repo
from venona.timeutil import cutoff_seconds

#: Ten minutes is RFC 6749's ceiling for an authorization code and there is no
#: reason to sit at it: the code is a one-time bearer that has just travelled
#: through a browser redirect, and every second it stays valid is a second it can
#: be replayed from a history entry or a proxy log.
CODE_TTL_SECONDS = 120

#: The default life of an access token minted through this flow. Unlike a
#: CLI-minted token, which defaults to never expiring, an OAuth token is obtained
#: by software the operator may never look at again -- so it expires by default
#: and the client is expected to come back through the flow.
TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60

#: The one code challenge method. See the module docstring.
CODE_CHALLENGE_METHOD = "S256"


class OAuthError(ValueError):
    """An RFC 6749 §5.2 error. `code` is the machine-readable half.

    Carries the code separately because the token endpoint must answer with it
    verbatim: a client library branches on `invalid_grant` to restart the flow,
    and on anything else to give up. Collapsing them all to one string turns a
    recoverable state into a dead end.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Client:
    client_id: str
    client_name: str
    redirect_uris: tuple[str, ...]


def _client(row: db.Row) -> Client:
    return Client(
        client_id=row["client_id"],
        client_name=row["client_name"],
        redirect_uris=tuple(db.jload(row["redirect_uris"], []) or ()),
    )


# ── dynamic client registration (RFC 7591) ───────────────────────────


def register(
    conn: db.Conn,
    *,
    client_id: str,
    client_name: str,
    redirect_uris: list[str],
    now: str,
) -> Client:
    """Register a public client. UNAUTHENTICATED, by design and by the RFC.

    That sounds alarming and is not: registration grants nothing. It hands back
    an identifier for ASKING for authorization, and the asking still
    ends at a consent screen in front of a signed-in human who sees the client's
    name and its redirect URI. What a hostile registration gets is a row in a
    table and a chance to be refused by a person.

    The refusals below are the ones that matter, and both are about the redirect
    URI, because that is the only field with any power in it.
    """
    uris = [u.strip() for u in redirect_uris if u and u.strip()]
    if not uris:
        raise OAuthError("invalid_redirect_uri", "at least one redirect_uri is required")
    for uri in uris:
        _assert_safe_redirect(uri)
    if not client_name.strip():
        raise OAuthError("invalid_client_metadata", "client_name is required")

    db.upsert(
        conn,
        "oauth_clients",
        {
            "client_id": client_id,
            "client_name": client_name.strip()[:200],
            "redirect_uris": db.jdump(uris),
            "created_at": now,
        },
        key=["client_id"],
    )
    return Client(
        client_id=client_id, client_name=client_name.strip()[:200], redirect_uris=tuple(uris)
    )


def _assert_safe_redirect(uri: str) -> None:
    """Where an authorization code is allowed to be delivered.

    LOOPBACK OR HTTPS, and nothing else. An MCP client is a desktop app or a CLI,
    so `http://127.0.0.1:<port>/...` is the normal case (RFC 8252 §7.3) and a
    plain-`http` URI pointing anywhere else would put a live credential on the
    wire in clear.

    `localhost` counts as loopback too. The literal address is better -- the name
    resolves through the host file and DNS, and loopback is only trustworthy
    because it cannot leave the machine -- but RFC 8252 §8.3 puts that on the
    client, not the server, and the clients that matter here register
    `http://localhost:<port>/callback`. Refusing it fails registration outright,
    which is the whole connection, to defend against an attacker who already
    controls name resolution on this machine.
    """
    from urllib.parse import urlsplit

    parts = urlsplit(uri)
    if parts.fragment:
        raise OAuthError("invalid_redirect_uri", f"{uri!r} must not carry a fragment")
    if parts.scheme == "https":
        return
    if parts.scheme == "http" and parts.hostname in ("127.0.0.1", "::1", "localhost"):
        return
    raise OAuthError(
        "invalid_redirect_uri",
        f"{uri!r} must be https, or http on the loopback address for a native client",
    )


def get_client(conn: db.Conn, client_id: str) -> Client | None:
    row = repo.select_oauth_clients_by_client_id(conn, client_id).fetchone()
    return _client(row) if row else None


def touch_client(conn: db.Conn, client_id: str, *, now: str) -> None:
    repo.update_oauth_clients_by_last_used_at_client_id(conn, now, client_id)


# ── the authorization code ───────────────────────────────────────────


def new_code() -> str:
    return secrets.token_urlsafe(32)


def issue_code(
    conn: db.Conn,
    *,
    code: str,
    client_id: str,
    user_id: str,
    redirect_uri: str,
    code_challenge: str,
    scope: str,
    now: str,
    expires_at: str,
) -> None:
    """Record an approved authorization. The code itself is never stored."""
    if not authz.grantable(scope):
        raise OAuthError("invalid_scope", f"unknown scope {scope!r}")
    db.upsert(
        conn,
        "oauth_codes",
        {
            "code_hash": hash_token(code),
            "client_id": client_id,
            "user_id": user_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "scope": scope,
            "expires_at": expires_at,
            "created_at": now,
        },
        key=["code_hash"],
    )


def verify_challenge(verifier: str, challenge: str) -> bool:
    """PKCE S256: `challenge == base64url(sha256(verifier))`, unpadded.

    `compare_digest` rather than `==` -- this compares a value the caller chose
    against one stored on our side, which is the shape a timing oracle needs.
    """
    import base64

    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return secrets.compare_digest(expected, challenge)


def redeem(
    conn: db.Conn,
    *,
    code: str,
    client_id: str,
    redirect_uri: str,
    code_verifier: str,
    now: str,
) -> db.Row:
    """Turn a code into the facts needed to mint a token, or raise `OAuthError`.

    EVERY CHECK FAILS WITH `invalid_grant` AND THE SAME SENTENCE, deliberately.
    An unknown code, a code belonging to another client, a mismatched redirect
    URI, a wrong PKCE verifier and an expired code are five different facts, and
    telling them apart is exactly what an attacker holding one of the five wants.
    The operator gets the detail from the row and the logs; the client gets
    "that code is not usable".

    SINGLE USE IS ENFORCED HERE, and by recording rather than deleting: a replay
    is a signal worth keeping. `redeemed_at` is written in the same transaction
    as the check, so two simultaneous redemptions cannot both pass -- the second
    reads the row the first committed.
    """
    generic = "that authorization code is not usable"

    with db.transaction(conn, what="oauth redeem"):
        # `FOR UPDATE` is what makes single-use real under concurrency: without
        # it, two token requests carrying the same code both read `redeemed_at IS
        # NULL` and both mint a token. This is the one read-then-write on the
        # OAuth path and it is worth the row lock.
        row = repo.select_oauth_codes_by_code_hash(conn, hash_token(code)).fetchone()
        if row is None:
            raise OAuthError("invalid_grant", generic)
        if row["redeemed_at"] is not None:
            raise OAuthError("invalid_grant", generic)
        if row["client_id"] != client_id:
            raise OAuthError("invalid_grant", generic)
        if row["redirect_uri"] != redirect_uri:
            raise OAuthError("invalid_grant", generic)
        if row["expires_at"] <= now:
            raise OAuthError("invalid_grant", generic)
        if not verify_challenge(code_verifier, row["code_challenge"]):
            raise OAuthError("invalid_grant", generic)

        repo.update_oauth_codes_by_redeemed_at_code_hash(conn, now, hash_token(code))
    return row


# How long an unredeemed registration is kept. Long enough that a person who
# started pairing, walked away and came back the same day still finds their
# client; short enough that a script pointed at `/register` cannot leave much
# behind. A pairing that reached consent has a `last_used_at` and is exempt
# whatever its age.
UNUSED_CLIENT_TTL_HOURS = 24


def purge_expired(conn: db.Conn, *, now: str) -> dict[str, int]:
    """Housekeeping for the two tables `/register` and `/authorize` fill.

    Redeemed codes are KEPT -- see `redeem` on the replay signal -- so only
    codes that expired without ever being used are removed.

    Registrations are the unbounded half: `/register` is unauthenticated,
    mints a fresh `client:uuid7` on every call, and nothing ever removed one.
    """
    codes = repo.delete_oauth_codes(conn, now).rowcount or 0
    cutoff = cutoff_seconds(now, UNUSED_CLIENT_TTL_HOURS)
    clients = repo.delete_oauth_clients_never_used(conn, cutoff).rowcount or 0
    return {"codes": codes, "clients": clients}


# ── the resource this server issues for (RFC 8707) ───────────────────


def canonical_resource(issuer: str) -> str:
    """The one resource identifier a token from here is good for.

    Every instance is its OWN authorization server -- discovery hands a client
    that instance's origin -- so this set has exactly one member and audience
    binding is a string comparison rather than a table.
    """
    return f"{issuer.rstrip('/')}/api/mcp"


def _comparable(value: str) -> str:
    """RFC 8707 §2 asks for the canonical form and RFC 9728 §7.4 asks servers to
    be forgiving about how a client spells it. Scheme and host are
    case-insensitive per RFC 3986 §6.2.2.1 and the path is not, so only the
    origin is folded; one trailing slash goes because both spellings are valid
    absolute URIs and the difference is never meaningful here.
    """
    stripped = value.strip().rstrip("/")
    scheme, sep, rest = stripped.partition("://")
    if not sep:
        return stripped
    host, slash, path = rest.partition("/")
    return f"{scheme.lower()}://{host.lower()}{slash}{path}"


def resource_matches(presented: str, *, issuer: str) -> bool:
    """Whether a client's `resource` names this server.

    An ABSENT value is not this function's business and is admitted by the
    callers: the browser extension pairs through the same endpoints and sends
    none, and RFC 8707 §2 makes the parameter optional at the protocol level
    even though MCP requires its own clients to send one.
    """
    return _comparable(presented) == _comparable(canonical_resource(issuer))


# ── the metadata documents ───────────────────────────────────────────


def protected_resource_metadata(*, issuer: str, resource: str) -> dict[str, Any]:
    """RFC 9728. What an MCP client fetches after a 401 tells it where to look.

    This is the whole discovery story: the 401 from `/api/mcp` carries
    `WWW-Authenticate: Bearer resource_metadata="<url>"`, the client fetches that
    URL, and this document points it at the authorization server. Nothing has to
    be configured in the client beyond the venona URL itself.
    """
    return {
        "resource": resource,
        "authorization_servers": [issuer],
        "scopes_supported": sorted(authz.SCOPES),
        "bearer_methods_supported": ["header"],
    }


def authorization_server_metadata(*, issuer: str) -> dict[str, Any]:
    """RFC 8414. The endpoints, and the refusals that make this 2.1 rather than 2.0.

    `code_challenge_methods_supported` lists `S256` ALONE. A client library that
    reads this document and finds no `plain` will not offer it, which is the
    polite half of the same refusal `redeem` enforces.
    """
    base = issuer.rstrip("/")
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/api/oauth/authorize",
        "token_endpoint": f"{base}/api/oauth/token",
        "registration_endpoint": f"{base}/api/oauth/register",
        "scopes_supported": sorted(authz.SCOPES),
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": [CODE_CHALLENGE_METHOD],
        "token_endpoint_auth_methods_supported": ["none"],
    }
