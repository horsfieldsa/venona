"""The id_token: a hand-rolled RS256 JWT encoder, its key, and its JWKS.

Hand-rolled because the surface is two endpoints and one encoder, and authlib's
framework fights a schema this small (docs/adr/0141 -- the same call the
instance made for its OAuth AS). Instances do not verify the signature (the
provenance carve-out: they trust a direct TLS exchange with a constant token
endpoint), so JWKS and the discovery document are published for CORRECTNESS --
any standards-compliant relying party can verify -- not because anything in
this system reads them.
"""

from __future__ import annotations

import base64
import hashlib
import json

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

#: The instance never reads `exp` (it trusts the exchange, not the token), but
#: a short life keeps a leaked token boring for any relying party that does.
ID_TOKEN_TTL_SECONDS = 300


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _int_b64url(value: int) -> str:
    return _b64url(value.to_bytes((value.bit_length() + 7) // 8, "big"))


def generate_signing_key() -> str:
    """A new RSA-2048 signing key, as base64-encoded PKCS8 PEM.

    Base64-wrapped because a multi-line PEM in an env file or a compose
    `environment:` block is a quoting footgun; one opaque line is not.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return base64.b64encode(pem).decode()


def load_signing_key(encoded: str) -> rsa.RSAPrivateKey:
    pem = base64.b64decode(encoded.encode(), validate=True)
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError(f"HUB_OIDC_SIGNING_KEY is a {type(key).__name__}, not an RSA key")
    return key


def kid_for(key: rsa.RSAPrivateKey) -> str:
    """Derived from the public key, never configured -- so it is stable per
    key, changes exactly when the key does, and cannot be misconfigured."""
    spki = key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(spki).hexdigest()[:16]


def mint_id_token(
    key: rsa.RSAPrivateKey,
    *,
    issuer: str,
    audience: str,
    subject: str,
    email: str,
    name: str | None,
    nonce: str,
    role: str,
    now: float,
    picture: str | None = None,
    entitlement: str | None = None,
    entitled_through: str | None = None,
    billing_notice: str | None = None,
    billing_notice_at: str | None = None,
) -> str:
    """Sign the claim set the instance's `identity_for_code` accepts.

    `name` is omitted when null rather than sent as null: the instance treats
    a missing claim as "no display name" and preserves any established one,
    while an explicit null would be a new wire shape nothing has parsed.
    `picture` follows the same rule, and so do the four billing claims: an
    instance that has not deployed billing yet ignores what it does not read,
    and one that has treats an absent claim as "the hub has nothing to say"
    rather than as "no entitlement". That distinction is what makes the two
    packages safe to deploy in either order (ADR 0209).

    THE NOTICE TRAVELS WITH THE ENTITLEMENT, all four together. The push treats
    a 404 from an instance as delivered, on the promise that a member who has
    never signed in there will carry the state in when they do -- and a promise
    that covered only two of the four silently dropped the dunning notice for
    exactly the reader a second instance was still trying to bill.
    """
    header = {"alg": "RS256", "typ": "JWT", "kid": kid_for(key)}
    claims: dict[str, object] = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "email": email,
        "email_verified": True,
        "nonce": nonce,
        "iat": int(now),
        "exp": int(now) + ID_TOKEN_TTL_SECONDS,
        "venona_role": role,
    }
    if name:
        claims["name"] = name
    if picture:
        claims["picture"] = picture
    if entitlement:
        claims["venona_entitlement"] = entitlement
    if entitled_through:
        claims["venona_entitled_through"] = entitled_through
    if billing_notice:
        claims["venona_billing_notice"] = billing_notice
    if billing_notice_at:
        claims["venona_billing_notice_at"] = billing_notice_at
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":"), sort_keys=True).encode())
        + "."
        + _b64url(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    )
    signature = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{_b64url(signature)}"


def jwks(key: rsa.RSAPrivateKey) -> dict:
    numbers = key.public_key().public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": kid_for(key),
                "n": _int_b64url(numbers.n),
                "e": _int_b64url(numbers.e),
            }
        ]
    }


def openid_configuration(issuer: str) -> dict:
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/oauth/authorize",
        "token_endpoint": f"{issuer}/oauth/token",
        "end_session_endpoint": f"{issuer}/oauth/logout",
        "jwks_uri": f"{issuer}/.well-known/jwks.json",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "scopes_supported": ["openid", "email"],
        "token_endpoint_auth_methods_supported": ["client_secret_post"],
        "claims_supported": [
            "iss",
            "aud",
            "sub",
            "email",
            "email_verified",
            "name",
            "picture",
            "nonce",
            "iat",
            "exp",
            "venona_role",
        ],
    }
