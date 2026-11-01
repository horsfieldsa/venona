"""Sessions, state cookies, token hashing and minting.

The instance's `venona.api.security` primitives, replicated rather than
imported (no `venona.*` imports here -- docs/adr/0141). Same shapes on
purpose: an HMAC-SHA256 signed `payload.sig` cookie, sha256-hexdigest storage
for anything presented as a bearer, `secrets.compare_digest` on every secret
equality, and a signed short-lived state cookie for the OAuth leg so a login
survives a process restart mid-flight.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets

from hub.config import Settings

COOKIE_NAME = "hub_session"
STATE_COOKIE = "hub_oauth_state"
STATE_TTL_SECONDS = 300

#: What the instance mints with `venona auth mgmt-token`. The hub never mints
#: these -- registration ACCEPTS one -- but it refuses a value that plainly
#: is not one before sealing it into the directory.
MGMT_TOKEN_PREFIX = "vmgmt_"
MGMT_TOKEN_CHARS = len(MGMT_TOKEN_PREFIX) + 64


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign(secret: str, payload: bytes) -> str:
    return _b64(hmac.new(secret.encode(), payload, hashlib.sha256).digest())


def _pack(secret: str, claims: dict) -> str:
    payload = json.dumps(claims, separators=(",", ":"), sort_keys=True).encode()
    return f"{_b64(payload)}.{_sign(secret, payload)}"


def _unpack(secret: str, token: str) -> dict | None:
    if token.count(".") != 1:
        return None
    body, signature = token.split(".")
    try:
        payload = _unb64(body)
    except Exception:
        return None
    if not secrets.compare_digest(_sign(secret, payload), signature):
        return None
    try:
        claims = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return claims if isinstance(claims, dict) else None


def new_secret() -> str:
    return _b64(secrets.token_bytes(48))


def new_admin_token() -> str:
    return secrets.token_hex(32)


def hash_token(presented: str) -> str:
    """The stored form of any bearer. Deliberately not a KDF -- same trade the
    instance records in its `0001_baseline.sql`: these are 256-bit random
    values, not passwords, so there is nothing for a stretcher to stretch."""
    return hashlib.sha256(presented.encode()).hexdigest()


def looks_like_mgmt_token(value: str) -> bool:
    return value.startswith(MGMT_TOKEN_PREFIX) and len(value) == MGMT_TOKEN_CHARS


def admin_token_ok(settings: Settings, presented: str) -> bool:
    if not settings.admin_token:
        return False
    return secrets.compare_digest(presented, settings.admin_token)


def safe_next(raw: str | None) -> str:
    """Same-origin path+query or nothing. The mirror of the instance's
    `_safe_next`: an identity provider with an open redirect launders every
    phishing page through a trusted origin -- and the hub is the origin every
    instance trusts, so this is the worse of the two places to get it wrong.

    A BACKSLASH IS A SEPARATOR TO A BROWSER AND NOT TO PYTHON. `/\\evil.com`
    passes "starts with / and not //" and every browser reads it as
    `//evil.com`. Control characters are the same attack differently spelled: a
    browser strips tab, newline and carriage return before parsing. Both are
    refused here, and `venona.api.routers.auth._safe_next` carries the full
    argument.
    """
    if not raw or not raw.startswith("/") or raw.startswith(("//", "/\\")):
        return "/"
    if "\\" in raw or any(char < " " or char == "\x7f" for char in raw):
        return "/"
    return raw


def normalized_public_url(raw: str) -> str:
    """The origin the hub sends an instance's mgmt bearer to on every poll.

    https only. Over http that bearer is `Authorization: Bearer vmgmt_...` in the
    clear, and it grants create-admin, overwrite-config and start-a-paid-run on
    the instance -- one sniffed header is instance takeover, and because the hub
    is also the OIDC issuer, a self-issued id_token on top. http is allowed for
    loopback alone, where there is no network to sniff, so a local dev stack still
    registers. Raises ValueError so both the admin API and the CLI can translate
    it to their own refusal.
    """
    url = raw.strip().rstrip("/")
    if url.startswith("https://"):
        return url
    if url.startswith("http://"):
        authority = url[len("http://") :].split("/", 1)[0]
        if authority.startswith("[") and "]" in authority:
            host = authority[1 : authority.index("]")]
        else:
            host = authority.split(":", 1)[0]
        if host in ("localhost", "127.0.0.1", "::1"):
            return url
        raise ValueError("public_url must be https:// (http is allowed only for localhost)")
    raise ValueError("public_url must be an https:// origin")


# -- sessions -----------------------------------------------------------


def mint_session(settings: Settings, *, email: str, now: float) -> tuple[str, float]:
    expires = now + settings.session_ttl_seconds
    token = _pack(settings.session_secret, {"sub": email, "iat": now, "exp": expires})
    return token, expires


def verify_session_issued(
    settings: Settings, token: str | None, *, now: float
) -> tuple[str, float] | None:
    """(email, iat) for a well-signed, unexpired token, else None.

    The issue time rides along because it is what the session-epoch check in
    `deps.session_email` compares: a token issued before the user's last
    sign-out is dead in every browser, not just the one that signed out.
    Whether the email may do anything -- suspended, provisioned, member of
    what -- stays the caller's database question, re-read per request so
    suspension bites immediately."""
    if not token:
        return None
    claims = _unpack(settings.session_secret, token)
    if not claims:
        return None
    if now >= float(claims.get("exp", 0)):
        return None
    email = str(claims.get("sub", "")).strip().lower()
    if not email:
        return None
    return email, float(claims.get("iat", 0))


def verify_session(settings: Settings, token: str | None, *, now: float) -> str | None:
    """The signed-in email, or None -- `verify_session_issued` without the
    issue time, for callers with no epoch to compare against."""
    identity = verify_session_issued(settings, token, now=now)
    return identity[0] if identity else None


# -- the Google leg's state cookie --------------------------------------


def mint_state(settings: Settings, *, now: float, next_path: str) -> tuple[str, str, str]:
    """(cookie_value, state, nonce) for a login we are starting."""
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    cookie = _pack(
        settings.session_secret,
        {
            "state": state,
            "nonce": nonce,
            "exp": now + STATE_TTL_SECONDS,
            "next": safe_next(next_path),
        },
    )
    return cookie, state, nonce


def verify_state(settings: Settings, cookie: str | None, *, state: str, now: float) -> dict | None:
    if not cookie:
        return None
    claims = _unpack(settings.session_secret, cookie)
    if not claims:
        return None
    if now >= float(claims.get("exp", 0)):
        return None
    if not secrets.compare_digest(str(claims.get("state", "")), state):
        return None
    return claims
