"""The admin surface: registration, the one-time secret, and the bearer gate."""

from __future__ import annotations

from hub.repositories import instances as instances_repo

from hub import seal, security

ADMIN = {"Authorization": "Bearer " + "a" * 40}
MGMT_TOKEN = "vmgmt_" + "b" * 64

BODY = {
    "slug": "acme",
    "display_name": "Acme Watch",
    "tagline": "who is buying anvils",
    "public_url": "https://acme.example/",
    "mgmt_token": MGMT_TOKEN,
}


async def test_register_returns_one_time_secret_and_seals_the_token(
    hub_client, hub_conn, hub_settings
):
    response = await hub_client.post("/admin/instances", json=BODY, headers=ADMIN)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["slug"] == "acme"
    assert body["oidc_client_id"].startswith("hubc:")
    secret = body["oidc_client_secret"]

    row = instances_repo.by_slug(hub_conn, "acme")
    assert row is not None
    assert row["status"] == "provisioning"
    assert row["public_url"] == "https://acme.example"  # trailing slash stripped
    assert row["oidc_client_secret_hash"] == security.hash_token(secret)
    assert secret not in row.values()  # plaintext nowhere
    assert seal.unseal(hub_settings.seal_key, row["mgmt_token_sealed"]) == MGMT_TOKEN

    # Never re-shown: neither listing nor detail carries a secret in any form.
    listed = (await hub_client.get("/admin/instances", headers=ADMIN)).json()
    detail = (await hub_client.get(f"/admin/instances/{row['id']}", headers=ADMIN)).json()
    for payload in (listed, detail):
        text = str(payload)
        assert secret not in text
        assert MGMT_TOKEN not in text
        assert row["mgmt_token_sealed"] not in text


async def test_register_refusals(hub_client):
    wrong_bearer = {"Authorization": "Bearer " + "z" * 40}
    assert (await hub_client.post("/admin/instances", json=BODY)).status_code == 401
    assert (
        await hub_client.post("/admin/instances", json=BODY, headers=wrong_bearer)
    ).status_code == 401

    bad_token = dict(BODY, mgmt_token="not-a-mgmt-token".ljust(70, "x"))
    response = await hub_client.post("/admin/instances", json=bad_token, headers=ADMIN)
    assert response.status_code == 422

    assert (await hub_client.post("/admin/instances", json=BODY, headers=ADMIN)).status_code == 201
    assert (
        await hub_client.post("/admin/instances", json=BODY, headers=ADMIN)
    ).status_code == 409  # duplicate slug


async def test_admin_401_carries_security_headers(hub_client):
    """The middleware-ordering scar: a refused request must still ship
    headers, which only holds when the gate sits INSIDE the header stamp."""
    response = await hub_client.get("/admin/instances")
    assert response.status_code == 401
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


async def test_edge_headers_when_configured(hub_settings):
    """In production the hub IS the public edge -- no the reverse proxy in front -- so HSTS
    and the CSP must come from the app itself, gated on HUB_EDGE_HEADERS."""
    import httpx
    from hub.main import build_app

    app = build_app(hub_settings.model_copy(update={"edge_headers": True}))
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://hub.test") as client:
        response = await client.get("/healthz")
    assert response.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"
    assert response.headers["content-security-policy"].startswith("default-src 'none';")


async def test_no_edge_headers_off_edge(hub_client):
    """Behind the reverse proxy, HSTS is the reverse proxy's: the app does not know its own scheme,
    and stamping it over plain http pins a dev browser to https."""
    response = await hub_client.get("/healthz")
    assert "strict-transport-security" not in response.headers
    assert "content-security-policy" not in response.headers


async def test_grant_creates_user_and_membership(hub_client, hub_conn):
    from hub.repositories import users as users_repo

    created = (await hub_client.post("/admin/instances", json=BODY, headers=ADMIN)).json()

    granted = await hub_client.post(
        "/admin/grants",
        json={"email": " Op@Example.com", "slug": "acme", "role": "admin", "display_name": "Op"},
        headers=ADMIN,
    )
    assert granted.status_code == 201, granted.text
    body = granted.json()
    assert body["created_user"] is True
    assert body["email"] == "op@example.com"  # normalized like `hub users add`
    assert body["instance_id"] == created["id"]

    # Re-granting is an upsert: role updates, no second user row.
    again = (
        await hub_client.post(
            "/admin/grants",
            json={"email": "op@example.com", "slug": "acme", "role": "reader"},
            headers=ADMIN,
        )
    ).json()
    assert again["created_user"] is False
    assert again["user_id"] == body["user_id"]

    user = users_repo.by_email(hub_conn, "op@example.com")
    assert users_repo.role_for(hub_conn, user_id=user["id"], instance_id=created["id"]) == "reader"

    missing = await hub_client.post(
        "/admin/grants",
        json={"email": "x@y.dev", "slug": "nope", "role": "admin"},
        headers=ADMIN,
    )
    assert missing.status_code == 404

    unauthed = await hub_client.post(
        "/admin/grants", json={"email": "x@y.dev", "slug": "acme", "role": "admin"}
    )
    assert unauthed.status_code == 401


async def test_patch_retire_and_rotate(hub_client, hub_conn):
    created = (await hub_client.post("/admin/instances", json=BODY, headers=ADMIN)).json()
    instance_id = created["id"]

    patched = await hub_client.patch(
        f"/admin/instances/{instance_id}",
        json={"tagline": "new tagline", "status": "retired"},
        headers=ADMIN,
    )
    assert patched.status_code == 200
    assert patched.json()["tagline"] == "new tagline"
    assert patched.json()["status"] == "retired"

    rotated = (
        await hub_client.post(f"/admin/instances/{instance_id}/rotate-secret", headers=ADMIN)
    ).json()
    assert rotated["oidc_client_secret"] != created["oidc_client_secret"]
    row = instances_repo.by_id(hub_conn, instance_id)
    assert row["oidc_client_secret_hash"] == security.hash_token(rotated["oidc_client_secret"])

    missing = await hub_client.patch(
        "/admin/instances/inst:nope", json={"tagline": "x"}, headers=ADMIN
    )
    assert missing.status_code == 404


async def test_public_directory_lists_live_and_offline_only(hub_client, hub_conn):
    created = (await hub_client.post("/admin/instances", json=BODY, headers=ADMIN)).json()
    # provisioning: hidden.
    assert (await hub_client.get("/public/instances")).json() == {"instances": []}

    instances_repo.record_poll_success(hub_conn, created["id"], {"version": "0.1.0"})
    listed = (await hub_client.get("/public/instances")).json()["instances"]
    assert listed == [
        {
            "slug": "acme",
            "display_name": "Acme Watch",
            "tagline": "who is buying anvils",
            "public_url": "https://acme.example",
            "status": "live",
        }
    ]

    instances_repo.set_fields(hub_conn, created["id"], status="retired")
    assert (await hub_client.get("/public/instances")).json() == {"instances": []}


async def test_well_known_documents(hub_client):
    config = (await hub_client.get("/.well-known/openid-configuration")).json()
    assert config["issuer"] == "https://hub.test"
    assert config["authorization_endpoint"] == "https://hub.test/oauth/authorize"
    assert config["token_endpoint"] == "https://hub.test/oauth/token"
    jwks = (await hub_client.get("/.well-known/jwks.json")).json()
    assert jwks["keys"][0]["kid"]
    assert (await hub_client.get("/healthz")).json() == {"ok": True}
