"""The cross-package drift alarm.

The hub's REAL token endpoint produces a response; the INSTANCE's real claim
parser (`venona.api.oauth.identity_for_code`) consumes it. Nothing is stubbed
in between except the network: the instance side constructs its httpx client
inline with no transport seam, so the client CLASS is wrapped to inject an
`httpx.MockTransport` serving the hub-produced bytes. If either package moves
-- a claim renamed, an endpoint path changed, the role vocabulary widened --
this is the test that goes red.
"""

from __future__ import annotations

import time

import httpx
import pytest
from hub.repositories import instances as instances_repo
from hub.repositories import users as users_repo

from hub import ids, security

INSTANCE_URL = "https://acme.example"
HUB_URL = "https://hub.test"
CLIENT_SECRET = "client-secret-value"
NONCE = "instance-nonce"


@pytest.fixture
def instance_auth_config():
    """The AuthConfig a hub-flipped instance runs with (VENONA_OIDC_* set)."""
    from venona.api.security import AuthConfig

    def build(client_id: str) -> AuthConfig:
        return AuthConfig(
            enabled=True,
            session_secret=b"s" * 32,
            allowed_emails=frozenset(),
            api_token=None,
            token_login=False,
            mgmt_token=None,
            oidc_issuer=HUB_URL,
            google_client_id=client_id,
            google_client_secret=CLIENT_SECRET,
            public_url=INSTANCE_URL,
            cookie_secure=True,
            session_ttl=14 * 24 * 60 * 60,
        )

    return build


async def test_hub_minted_token_satisfies_the_instance_parser(
    hub_client, hub_conn, hub_settings, instance_auth_config, monkeypatch
):
    # The hub side, for real: registered instance, member, authorize, token.
    instance_id = ids.instance_id()
    client_id = ids.client_id()
    instances_repo.create(
        hub_conn,
        instance_id=instance_id,
        slug="acme",
        display_name="Acme Watch",
        tagline="",
        public_url=INSTANCE_URL,
        oidc_client_id=client_id,
        oidc_client_secret_hash=security.hash_token(CLIENT_SECRET),
        mgmt_token_sealed="sealed",
    )
    user_id = ids.user_id()
    users_repo.create(hub_conn, user_id=user_id, email="ada@example.com", display_name="Ada")
    users_repo.grant(hub_conn, user_id=user_id, instance_id=instance_id, role="reader")

    token, _ = security.mint_session(hub_settings, email="ada@example.com", now=time.time())
    hub_client.cookies.set(security.COOKIE_NAME, token, domain="hub.test", path="/")
    authorize = await hub_client.get(
        "/oauth/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": f"{INSTANCE_URL}/api/auth/callback",
            "response_type": "code",
            "scope": "openid email",
            "state": "s",
            "nonce": NONCE,
        },
    )
    assert authorize.status_code == 302
    code = httpx.URL(authorize.headers["location"]).params["code"]

    from venona.api import oauth as instance_oauth

    # The instance would POST this form to {issuer}/oauth/token. Capture what
    # it sends, serve it through the hub's REAL endpoint, return those bytes.
    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{HUB_URL}/oauth/token"
        hub_response = await hub_client.post(
            "/oauth/token", data=dict(httpx.QueryParams(request.content.decode()))
        )
        return httpx.Response(
            hub_response.status_code,
            content=hub_response.content,
            headers={"content-type": "application/json"},
        )

    real_client = httpx.AsyncClient

    def client_with_mock_transport(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(instance_oauth.httpx, "AsyncClient", client_with_mock_transport)

    identity = await instance_oauth.identity_for_code(
        instance_auth_config(client_id), code=code, nonce=NONCE
    )
    assert identity.email == "ada@example.com"
    assert identity.subject == user_id
    assert identity.issuer == HUB_URL
    assert identity.role == "reader"
    assert identity.display_name == "Ada"
