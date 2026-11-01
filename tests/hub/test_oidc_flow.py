"""The auth-code flow, end to end over ASGI, and every refusal on the way."""

from __future__ import annotations

import base64
import json
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from hub.repositories import instances as instances_repo
from hub.repositories import users as users_repo

from hub import ids, security

INSTANCE_URL = "https://acme.example"
REDIRECT_URI = f"{INSTANCE_URL}/api/auth/callback"
CLIENT_SECRET = "client-secret-value"


@pytest.fixture
def seeded(hub_conn):
    """A registered instance, a provisioned member, and their ids."""
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
    users_repo.grant(hub_conn, user_id=user_id, instance_id=instance_id, role="admin")
    return {"instance_id": instance_id, "client_id": client_id, "user_id": user_id}


def _sign_in(hub_client, hub_settings, email="ada@example.com") -> None:
    token, _ = security.mint_session(hub_settings, email=email, now=time.time())
    hub_client.cookies.set(security.COOKIE_NAME, token, domain="hub.test", path="/")


def _authorize_query(client_id: str, **overrides) -> dict:
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email",
        "state": "instance-state",
        "nonce": "instance-nonce",
        "prompt": "select_account",  # the instance sends it; the hub tolerates it
    }
    params.update(overrides)
    return params


async def _get_code(hub_client, client_id: str) -> str:
    response = await hub_client.get("/oauth/authorize", params=_authorize_query(client_id))
    assert response.status_code == 302, response.text
    target = urlsplit(response.headers["location"])
    assert f"{target.scheme}://{target.netloc}{target.path}" == REDIRECT_URI
    query = parse_qs(target.query)
    assert query["state"] == ["instance-state"]
    return query["code"][0]


def _claims(id_token: str) -> dict:
    payload = id_token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def _token_form(client_id: str, code: str, **overrides) -> dict:
    form = {
        "code": code,
        "client_id": client_id,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    form.update(overrides)
    return form


async def test_authorize_then_token_happy_path(hub_client, hub_settings, seeded):
    _sign_in(hub_client, hub_settings)
    code = await _get_code(hub_client, seeded["client_id"])

    response = await hub_client.post("/oauth/token", data=_token_form(seeded["client_id"], code))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "Bearer"
    assert body["access_token"]

    claims = _claims(body["id_token"])
    # The exact set the instance's identity_for_code checks.
    assert claims["iss"] == "https://hub.test"
    assert claims["aud"] == seeded["client_id"]
    assert claims["nonce"] == "instance-nonce"
    assert claims["email"] == "ada@example.com"
    assert claims["email_verified"] is True
    assert claims["venona_role"] == "admin"
    assert claims["sub"] == seeded["user_id"]
    assert claims["name"] == "Ada"


async def test_anonymous_authorize_bounces_to_sign_in_with_next(hub_client, seeded):
    response = await hub_client.get(
        "/oauth/authorize", params=_authorize_query(seeded["client_id"])
    )
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("/?next=%2Foauth%2Fauthorize")
    # The ride-along must reproduce the instance's whole request.
    assert "instance-state" in location and "instance-nonce" in location


async def test_bad_client_or_redirect_is_a_page_never_a_redirect(hub_client, seeded):
    unknown = await hub_client.get("/oauth/authorize", params=_authorize_query("hubc:unknown"))
    assert unknown.status_code == 400
    assert "location" not in unknown.headers

    wrong_uri = await hub_client.get(
        "/oauth/authorize",
        params=_authorize_query(seeded["client_id"], redirect_uri="https://evil.example/cb"),
    )
    assert wrong_uri.status_code == 400
    assert "location" not in wrong_uri.headers


async def test_non_member_gets_request_access_and_no_code(
    hub_client, hub_conn, hub_settings, seeded
):
    _sign_in(hub_client, hub_settings, email="stranger@example.com")
    response = await hub_client.get(
        "/oauth/authorize", params=_authorize_query(seeded["client_id"])
    )
    assert response.status_code == 200
    assert "Request access" in response.text
    count = hub_conn.execute("SELECT count(*) AS n FROM auth_codes").fetchone()
    assert count["n"] == 0


async def test_code_is_single_use(hub_client, hub_settings, seeded):
    _sign_in(hub_client, hub_settings)
    code = await _get_code(hub_client, seeded["client_id"])
    form = _token_form(seeded["client_id"], code)
    assert (await hub_client.post("/oauth/token", data=form)).status_code == 200
    replay = await hub_client.post("/oauth/token", data=form)
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"


async def test_expired_code_refused(hub_client, hub_conn, hub_settings, seeded):
    _sign_in(hub_client, hub_settings)
    code = await _get_code(hub_client, seeded["client_id"])
    hub_conn.execute("UPDATE auth_codes SET expires_at = now() - interval '1 second'")
    response = await hub_client.post("/oauth/token", data=_token_form(seeded["client_id"], code))
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


async def test_wrong_secret_and_wrong_redirect_refused(hub_client, hub_settings, seeded):
    _sign_in(hub_client, hub_settings)
    code = await _get_code(hub_client, seeded["client_id"])

    wrong_secret = await hub_client.post(
        "/oauth/token", data=_token_form(seeded["client_id"], code, client_secret="wrong")
    )
    assert wrong_secret.status_code == 401
    assert wrong_secret.json()["error"] == "invalid_client"

    wrong_uri = await hub_client.post(
        "/oauth/token",
        data=_token_form(seeded["client_id"], code, redirect_uri="https://evil.example/cb"),
    )
    assert wrong_uri.status_code == 400
    assert wrong_uri.json()["error"] == "invalid_grant"

    # Neither refusal burned the code; the honest exchange still works.
    ok = await hub_client.post("/oauth/token", data=_token_form(seeded["client_id"], code))
    assert ok.status_code == 200


async def test_retired_instance_is_refused_at_the_token_endpoint(
    hub_client, hub_conn, hub_settings, seeded
):
    """authorize refuses a retired instance ('Unknown client'); the token endpoint
    must agree. Otherwise a code minted seconds before retirement still redeems to
    a fresh, fully-signed id_token for the whole 5-minute code TTL -- retirement
    would only close the front door."""
    _sign_in(hub_client, hub_settings)
    code = await _get_code(hub_client, seeded["client_id"])
    hub_conn.execute(
        "UPDATE instances SET status = 'retired' WHERE id = %s", (seeded["instance_id"],)
    )

    response = await hub_client.post("/oauth/token", data=_token_form(seeded["client_id"], code))
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


async def test_membership_revoked_between_authorize_and_token(
    hub_client, hub_conn, hub_settings, seeded
):
    _sign_in(hub_client, hub_settings)
    code = await _get_code(hub_client, seeded["client_id"])
    users_repo.revoke(hub_conn, user_id=seeded["user_id"], instance_id=seeded["instance_id"])
    response = await hub_client.post("/oauth/token", data=_token_form(seeded["client_id"], code))
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


async def test_suspension_between_authorize_and_token(hub_client, hub_conn, hub_settings, seeded):
    _sign_in(hub_client, hub_settings)
    code = await _get_code(hub_client, seeded["client_id"])
    users_repo.set_status(hub_conn, seeded["user_id"], "suspended")
    response = await hub_client.post("/oauth/token", data=_token_form(seeded["client_id"], code))
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"
