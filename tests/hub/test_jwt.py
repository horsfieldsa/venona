"""RS256 round-trip with a fixed key and an injected clock.

The signature is verified against the JWKS the hub itself publishes -- n and e
from the route document, not the private key object -- so this test holds the
two ends of the published contract together.
"""

from __future__ import annotations

import base64
import json

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from hubmaterial import TEST_KID, TEST_SIGNING_KEY

from hub import jwt

NOW = 1_755_000_000.0


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _mint(**overrides) -> str:
    key = jwt.load_signing_key(TEST_SIGNING_KEY)
    kwargs = dict(
        issuer="https://hub.test",
        audience="hubc:client-1",
        subject="user:u1",
        email="a@example.com",
        name="Ada",
        nonce="nonce-1",
        role="admin",
        now=NOW,
    )
    kwargs.update(overrides)
    return jwt.mint_id_token(key, **kwargs)


def test_round_trip_claims_and_kid() -> None:
    token = _mint()
    header_b64, payload_b64, _sig = token.split(".")
    header = json.loads(_unb64(header_b64))
    claims = json.loads(_unb64(payload_b64))
    assert header == {"alg": "RS256", "typ": "JWT", "kid": TEST_KID}
    assert claims == {
        "iss": "https://hub.test",
        "aud": "hubc:client-1",
        "sub": "user:u1",
        "email": "a@example.com",
        "email_verified": True,
        "name": "Ada",
        "nonce": "nonce-1",
        "iat": int(NOW),
        "exp": int(NOW) + jwt.ID_TOKEN_TTL_SECONDS,
        "venona_role": "admin",
    }


def test_name_omitted_when_null() -> None:
    claims = json.loads(_unb64(_mint(name=None).split(".")[1]))
    assert "name" not in claims


def test_signature_verifies_against_published_jwks() -> None:
    key = jwt.load_signing_key(TEST_SIGNING_KEY)
    token = _mint()
    signing_input, sig_b64 = token.rsplit(".", 1)

    document = jwt.jwks(key)
    (entry,) = document["keys"]
    assert entry["kid"] == TEST_KID
    public = rsa.RSAPublicNumbers(
        e=int.from_bytes(_unb64(entry["e"]), "big"),
        n=int.from_bytes(_unb64(entry["n"]), "big"),
    ).public_key()
    # Raises on mismatch; that IS the assertion.
    public.verify(_unb64(sig_b64), signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())


def test_openid_configuration_matches_derived_endpoints() -> None:
    document = jwt.openid_configuration("https://hub.test")
    # The instance derives these two paths from the issuer and never reads
    # this document; publishing anything else would be a standing lie.
    assert document["authorization_endpoint"] == "https://hub.test/oauth/authorize"
    assert document["token_endpoint"] == "https://hub.test/oauth/token"
    assert document["issuer"] == "https://hub.test"
    assert document["id_token_signing_alg_values_supported"] == ["RS256"]
