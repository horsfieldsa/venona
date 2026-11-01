"""OIDC wire shapes: the token response, RFC 6749 §5.2 errors, and the two
published-for-correctness documents (nothing in this system reads them)."""

from __future__ import annotations

from pydantic import BaseModel


class TokenResponse(BaseModel):
    id_token: str
    # Opaque and accepted nowhere: present because OIDC Core requires the
    # field, while the instance reads only `id_token`.
    access_token: str
    token_type: str = "Bearer"
    expires_in: int


class TokenError(BaseModel):
    error: str
    error_description: str


class Jwk(BaseModel):
    kty: str
    use: str
    alg: str
    kid: str
    n: str
    e: str


class Jwks(BaseModel):
    keys: list[Jwk]


class OpenIdConfiguration(BaseModel):
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    response_types_supported: list[str]
    grant_types_supported: list[str]
    subject_types_supported: list[str]
    id_token_signing_alg_values_supported: list[str]
    scopes_supported: list[str]
    token_endpoint_auth_methods_supported: list[str]
    claims_supported: list[str]
