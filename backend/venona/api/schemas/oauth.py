"""Wire models for the OAuth 2.1 endpoints.

A pydantic model at every API boundary, per `tests/test_route_contracts.py`. The
field names are RFC 7591 and RFC 6749 spellings rather than this codebase's
`snake_case` convention -- which they happen to share, but the reason they are
what they are is the RFC, not the house style. A client library will not look for
anything else.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ClientRegistration(BaseModel):
    """RFC 7591 §2. Only the two fields venona acts on.

    `extra` is IGNORED rather than forbidden, which is the opposite of the rule
    everywhere else in this app. RFC 7591 clients send a dozen optional metadata
    fields (`grant_types`, `application_type`, `logo_uri`, ...) and rejecting a
    registration for carrying one would refuse conformant clients for being
    conformant. What venona does not act on, it does not store.
    """

    client_name: str = Field(min_length=1, max_length=200)
    redirect_uris: list[str] = Field(min_length=1, max_length=10)


class RegisteredClient(BaseModel):
    client_id: str
    client_name: str
    redirect_uris: list[str]
    #: `none` -- a public client with no secret. Stated rather than omitted so a
    #: client does not have to infer it from the absence of `client_secret`.
    token_endpoint_auth_method: str = "none"


class TokenResponse(BaseModel):
    """RFC 6749 §5.1.

    No `refresh_token`, deliberately. A refresh token is a long-lived credential
    held by software the operator may never look at again, and its whole purpose
    is to avoid asking the human -- which is the one thing worth asking for here.
    The access token lasts thirty days; after that the client runs the flow again
    and a person approves it again.
    """

    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    scope: str
