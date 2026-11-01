"""The portal: the Google leg, the chooser, and non-echoing request-access."""

from __future__ import annotations

import base64
import json
import time

import httpx
import pytest
from hub.repositories import access_requests as requests_repo
from hub.repositories import instances as instances_repo
from hub.repositories import settings as settings_repo
from hub.repositories import users as users_repo

from hub import billing_state, google, ids, mgmt_client, middleware, pages, seal, security


def _fake_google_id_token(**overrides) -> str:
    """Unsigned but well-shaped: the hub reads Google's id_token without
    verifying the signature (provenance carve-out), so a test token needs
    valid segments, not a valid signature."""
    claims = {
        "iss": "accounts.google.com",
        "aud": "test-google-client",
        "sub": "google-subject-1",
        "email": "ada@example.com",
        "email_verified": True,
        "name": "Ada Lovelace",
        "nonce": "unset",
    }
    claims.update(overrides)
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"e30.{body}.sig"


def _google_transport(**claim_overrides) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == google.GOOGLE_TOKEN_ENDPOINT
        return httpx.Response(200, json={"id_token": _fake_google_id_token(**claim_overrides)})

    return httpx.MockTransport(handler)


@pytest.fixture
def _state(hub_client, hub_settings):
    """Start a login the way the browser would: mint the state cookie and set
    it on the client. Returns (state, nonce)."""

    def start(next_path: str = "/") -> tuple[str, str]:
        cookie, state, nonce = security.mint_state(
            hub_settings, now=time.time(), next_path=next_path
        )
        hub_client.cookies.set(security.STATE_COOKIE, cookie, domain="hub.test", path="/")
        return state, nonce

    return start


def _seed_user(conn, email="ada@example.com", name=None) -> str:
    user_id = ids.user_id()
    users_repo.create(conn, user_id=user_id, email=email, display_name=name)
    return user_id


def _seed_instance(conn, slug="acme", name="Acme Watch", live=True) -> str:
    instance_id = ids.instance_id()
    instances_repo.create(
        conn,
        instance_id=instance_id,
        slug=slug,
        display_name=name,
        tagline=f"the {slug} watch",
        public_url=f"https://{slug}.example",
        oidc_client_id=ids.client_id(),
        oidc_client_secret_hash="x",
        mgmt_token_sealed="y",
    )
    if live:
        instances_repo.record_poll_success(conn, instance_id, {"version": "0.1.0"})
    return instance_id


async def test_callback_signs_in_a_provisioned_user(hub_client, hub_conn, monkeypatch, _state):
    user_id = _seed_user(hub_conn)
    state, nonce = _state(next_path="/somewhere")
    monkeypatch.setattr(google, "EXCHANGE_TRANSPORT", _google_transport(nonce=nonce))

    response = await hub_client.get(f"/auth/callback?code=c&state={state}")
    assert response.status_code == 302
    assert response.headers["location"] == "/somewhere"
    assert security.COOKIE_NAME in response.cookies

    row = users_repo.by_id(hub_conn, user_id)
    assert row["google_subject"] == "google-subject-1"
    assert row["display_name"] == "Ada Lovelace"  # filled the gap
    assert row["last_login_at"] is not None


async def test_callback_refuses_suspended_and_wrong_nonce(
    hub_client, hub_conn, monkeypatch, _state
):
    user_id = _seed_user(hub_conn)
    users_repo.set_status(hub_conn, user_id, "suspended")
    state, nonce = _state()
    monkeypatch.setattr(google, "EXCHANGE_TRANSPORT", _google_transport(nonce=nonce))
    refused = await hub_client.get(f"/auth/callback?code=c&state={state}")
    assert refused.status_code == 403
    assert security.COOKIE_NAME not in refused.cookies

    state, _nonce = _state()
    monkeypatch.setattr(google, "EXCHANGE_TRANSPORT", _google_transport(nonce="other"))
    mismatched = await hub_client.get(f"/auth/callback?code=c&state={state}")
    assert mismatched.status_code == 400

    # No state cookie at all: refused before any outbound exchange.
    hub_client.cookies.clear()
    bare = await hub_client.get("/auth/callback?code=c&state=whatever")
    assert bare.status_code == 400


async def test_unknown_google_account_gets_session_but_no_row(
    hub_client, hub_conn, monkeypatch, _state
):
    state, nonce = _state()
    monkeypatch.setattr(
        google,
        "EXCHANGE_TRANSPORT",
        _google_transport(nonce=nonce, email="stranger@example.com"),
    )
    response = await hub_client.get(f"/auth/callback?code=c&state={state}")
    assert response.status_code == 302
    assert users_repo.by_email(hub_conn, "stranger@example.com") is None

    # Signed in and memberless: the gallery, which on a hub with no domains
    # registered says so rather than rendering an empty list.
    page = (await hub_client.get("/")).text
    assert "Domains" in page
    assert "nothing to read here yet" in page
    assert "stranger@example.com" in page


async def test_the_catalog_marks_only_this_readers_own_domains(hub_client, hub_conn, hub_settings):
    user_id = _seed_user(hub_conn)
    mine = _seed_instance(hub_conn, "acme", "Acme Watch")
    _seed_instance(hub_conn, "other", "Other Watch")
    users_repo.grant(hub_conn, user_id=user_id, instance_id=mine, role="reader")

    token, _ = security.mint_session(hub_settings, email="ada@example.com", now=time.time())
    hub_client.cookies.set(security.COOKIE_NAME, token, domain="hub.test", path="/")

    page = (await hub_client.get("/")).text
    assert "Acme Watch" in page
    assert "https://acme.example" in page
    # The catalog lists every domain, so the invariant is not that the other one
    # is absent -- it is that this reader's ROLE is on theirs alone. A role badge
    # against a domain somebody does not hold is one account's memberships
    # leaking into another's page.
    assert "Other Watch" in page
    acme = page[page.index("Acme Watch") : page.index("Other Watch")]
    assert "reader" in acme
    assert "reader" not in page[page.index("Other Watch") :]

    # Signed-out: the sign-in page, no directory contents.
    hub_client.cookies.clear()
    anonymous = (await hub_client.get("/")).text
    assert "Sign in with Google" in anonymous
    assert "Acme Watch" not in anonymous


async def test_signed_in_arrival_with_next_is_forwarded(hub_client, hub_conn, hub_settings):
    token, _ = security.mint_session(hub_settings, email="ada@example.com", now=time.time())
    hub_client.cookies.set(security.COOKIE_NAME, token, domain="hub.test", path="/")
    response = await hub_client.get("/?next=/oauth/authorize%3Fclient_id%3Dx")
    assert response.status_code == 302
    assert response.headers["location"] == "/oauth/authorize?client_id=x"

    offsite = await hub_client.get("/?next=https://evil.example")
    assert offsite.status_code == 302
    assert offsite.headers["location"] == "/"


async def test_memberless_flow_has_a_path_forward(hub_client, hub_conn, hub_settings):
    """The request-access dead-end (reported 2026-08-19): a signed-in unknown
    user submits and must not be stuck. Before submitting, the form offers
    sign-out; after, `/` is a standing pending page with a re-check; after the
    operator grants, that same `/` is the chooser -- no new login required,
    because memberships are re-read per request."""
    _seed_instance(hub_conn, "acme", "Acme Watch")
    token, _ = security.mint_session(hub_settings, email="stranger@example.com", now=time.time())
    hub_client.cookies.set(security.COOKIE_NAME, token, domain="hub.test", path="/")

    form = (await hub_client.get("/")).text
    assert "Request access" in form
    assert "Sign out" in form

    await hub_client.post(
        "/request-access", data={"email": "stranger@example.com", "note": "", "instance": ""}
    )
    pending = (await hub_client.get("/")).text
    assert "Request pending" in pending
    assert "Check again" in pending
    assert "Sign out" in pending
    assert "Send request" not in pending

    # /request-access itself still offers the form: requesting ANOTHER
    # instance stays possible while one request is pending.
    assert "Send request" in (await hub_client.get("/request-access")).text

    # The grant, as the CLI performs it: row, membership, request settled.
    user_id = _seed_user(hub_conn, email="stranger@example.com")
    instance_id = instances_repo.by_slug(hub_conn, "acme")["id"]
    users_repo.grant(hub_conn, user_id=user_id, instance_id=instance_id, role="reader")
    for row in requests_repo.list_pending(hub_conn):
        requests_repo.set_status(hub_conn, row["id"], "granted")

    granted = (await hub_client.get("/")).text
    assert "Acme Watch" in granted
    assert "Request pending" not in granted


async def test_request_access_is_byte_identical_for_every_outcome(hub_client, hub_conn):
    _seed_user(hub_conn, email="known@example.com")
    _seed_instance(hub_conn)

    async def submit(email: str, instance: str = "") -> bytes:
        response = await hub_client.post(
            "/request-access", data={"email": email, "note": "please", "instance": instance}
        )
        assert response.status_code == 200
        return response.content

    first = await submit("new@example.com")
    duplicate = await submit("new@example.com")
    known = await submit("known@example.com")
    bogus_instance = await submit("third@example.com", instance="does-not-exist")
    assert first == duplicate == known == bogus_instance

    rows = requests_repo.list_pending(hub_conn)
    assert len(rows) == 3  # the duplicate inserted nothing


async def test_logout_ends_instance_sessions_too(hub_client, hub_conn, hub_settings, monkeypatch):
    """Signing out of the hub signs out of every instance: the fan-out sends
    each of the user's instances the `logout` provisioning action with its own
    unsealed credential -- and stays best-effort, so one instance answering
    500 does not keep the person signed in at the door."""
    from hubmaterial import TEST_SEAL_KEY

    user_id = _seed_user(hub_conn)
    mine = _seed_instance(hub_conn, "acme", "Acme Watch")
    flaky = _seed_instance(hub_conn, "dark", "Dark Watch")
    _seed_instance(hub_conn, "other", "Other Watch")  # no membership: never called
    users_repo.grant(hub_conn, user_id=user_id, instance_id=mine, role="reader")
    users_repo.grant(hub_conn, user_id=user_id, instance_id=flaky, role="reader")
    instances_repo.set_fields(
        hub_conn, mine, mgmt_token_sealed=seal.seal(TEST_SEAL_KEY, "vmgmt_acme")
    )
    instances_repo.set_fields(
        hub_conn, flaky, mgmt_token_sealed=seal.seal(TEST_SEAL_KEY, "vmgmt_dark")
    )

    calls: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            (
                str(request.url),
                request.headers.get("authorization", ""),
                json.loads(request.content),
            )
        )
        if "dark" in str(request.url):
            return httpx.Response(500, json={"detail": "boom"})
        return httpx.Response(
            200, json={"email": "ada@example.com", "action": "logout", "status": "ok"}
        )

    monkeypatch.setattr(mgmt_client, "MGMT_TRANSPORT", httpx.MockTransport(handler))

    token, _ = security.mint_session(hub_settings, email="ada@example.com", now=time.time())
    hub_client.cookies.set(security.COOKIE_NAME, token, domain="hub.test", path="/")

    response = await hub_client.post("/logout")
    assert response.status_code == 302
    assert security.COOKIE_NAME in response.headers.get("set-cookie", "")

    urls = sorted(url for url, _, _ in calls)
    assert urls == [
        "https://acme.example/api/mgmt/provision-user",
        "https://dark.example/api/mgmt/provision-user",
    ]
    by_url = {url: (auth, body) for url, auth, body in calls}
    auth, body = by_url["https://acme.example/api/mgmt/provision-user"]
    assert auth == "Bearer vmgmt_acme"
    assert body["email"] == "ada@example.com" and body["action"] == "logout"


async def test_logout_revokes_the_users_other_hub_sessions(
    hub_client, hub_conn, hub_settings, monkeypatch
):
    """The hub cookie is the SSO master key, so sign-out must kill every copy
    of it: a second browser's session -- minted earlier, never signed out --
    stops verifying the moment one browser signs out. Same per-user epoch
    design as the instances (`users.session_epoch`)."""
    _seed_user(hub_conn)
    monkeypatch.setattr(
        mgmt_client,
        "MGMT_TRANSPORT",
        httpx.MockTransport(lambda request: httpx.Response(200, json={"status": "ok"})),
    )

    minted_at = time.time() - 1
    laptop, _ = security.mint_session(hub_settings, email="ada@example.com", now=minted_at)
    phone, _ = security.mint_session(hub_settings, email="ada@example.com", now=minted_at)

    hub_client.cookies.set(security.COOKIE_NAME, laptop, domain="hub.test", path="/")
    response = await hub_client.post("/logout")
    assert response.status_code == 302

    # The phone's cookie was never deleted, but its session is dead: the
    # portal answers it with the sign-in page, not the chooser.
    hub_client.cookies.clear()
    hub_client.cookies.set(security.COOKIE_NAME, phone, domain="hub.test", path="/")
    page = (await hub_client.get("/")).text
    assert "Sign in with Google" in page

    # A fresh sign-in AFTER the sign-out mints a token past the watermark.
    fresh, _ = security.mint_session(hub_settings, email="ada@example.com", now=time.time())
    hub_client.cookies.clear()
    hub_client.cookies.set(security.COOKIE_NAME, fresh, domain="hub.test", path="/")
    assert "Sign in with Google" not in (await hub_client.get("/")).text


async def test_logout_without_a_row_touches_no_instance(hub_client, hub_settings, monkeypatch):
    """A drive-by Google session has no user row, so there is nothing to fan
    out to -- and nothing must be attempted."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected mgmt call to {request.url}")

    monkeypatch.setattr(mgmt_client, "MGMT_TRANSPORT", httpx.MockTransport(handler))
    token, _ = security.mint_session(hub_settings, email="stranger@example.com", now=time.time())
    hub_client.cookies.set(security.COOKIE_NAME, token, domain="hub.test", path="/")

    response = await hub_client.post("/logout")
    assert response.status_code == 302


async def test_end_session_kills_the_hub_cookie(hub_client):
    """The instances' sign-out lands here (SessionInfo.logout_url), so both
    doors close together: after this GET, `/` shows sign-in again."""
    response = await hub_client.get("/oauth/logout")
    assert response.status_code == 302
    assert response.headers["location"] == "/"
    cleared = response.headers.get("set-cookie", "")
    assert security.COOKIE_NAME in cleared and "Max-Age=0" in cleared


# ── a separate front site's door ────────────────────────────────────────
#
# The front page is a different origin (example.com; this is hub.example.com) and
# posts JSON, so these cover the two halves that only exist for it: the
# allowance a browser needs before it will hand the answer back, and the
# bucket that keeps a launch signup out of the grant queue.

MARKETING = "https://example.com"


@pytest.mark.anyio
async def test_the_marketing_form_reaches_the_hub_queue(hub_client, hub_conn) -> None:
    """⭐ The whole point of pointing the form here. It used to post to an
    instance, whose queue cannot create an account: granting is the hub's, so
    a request answered anywhere else is a request nobody can answer."""
    reply = await hub_client.post(
        "/request-access",
        json={"email": "Stranger@Example.com", "kind": "launch"},
        headers={"origin": MARKETING},
    )
    assert reply.status_code == 200
    assert reply.headers["access-control-allow-origin"] == MARKETING
    assert "Origin" in reply.headers["vary"]

    rows = requests_repo.list_pending(hub_conn, kind="launch")
    assert [row["email"] for row in rows] == ["stranger@example.com"]


@pytest.mark.anyio
async def test_a_launch_signup_is_not_in_the_grant_queue(hub_client, hub_conn) -> None:
    """The two doors stay apart. An address left on the front page was
    promised one message, not an account, and mixing it into the requests an
    operator grants is how somebody gets let in who never asked to be."""
    await hub_client.post(
        "/request-access", json={"email": "waiting@example.com", "kind": "launch"}
    )
    await hub_client.post("/request-access", json={"email": "asking@example.com", "kind": "access"})
    assert [r["email"] for r in requests_repo.list_pending(hub_conn, kind="access")] == [
        "asking@example.com"
    ]
    assert [r["email"] for r in requests_repo.list_pending(hub_conn, kind="launch")] == [
        "waiting@example.com"
    ]
    # Undivided for callers that want the whole queue.
    assert len(requests_repo.list_pending(hub_conn)) == 2


@pytest.mark.anyio
async def test_an_unknown_kind_lands_where_a_person_will_see_it(hub_client, hub_conn) -> None:
    """Not something anyone typed, so it is not refused -- but the safe
    reading of an unrecognised value is the bucket that gets looked at."""
    await hub_client.post("/request-access", json={"email": "odd@example.com", "kind": "whatever"})
    assert [r["email"] for r in requests_repo.list_pending(hub_conn, kind="access")] == [
        "odd@example.com"
    ]


@pytest.mark.anyio
async def test_only_configured_origins_are_answered(hub_client) -> None:
    """⭐ The allowance is scoped to this route AND to a list. The hub is the
    OIDC issuer and every other page here runs on the session cookie that is
    the SSO master key, so an origin nobody configured gets the row written
    and no header back -- which is a browser refusal, not a write it could
    have read the answer to."""
    reply = await hub_client.post(
        "/request-access",
        json={"email": "elsewhere@example.com"},
        headers={"origin": "https://not-ours.example"},
    )
    assert reply.status_code == 200
    assert "access-control-allow-origin" not in reply.headers


@pytest.mark.anyio
async def test_the_preflight_is_answered(hub_client) -> None:
    """A JSON post preflights, and a preflight that 405s reads on the page as
    the form being down rather than as a configuration mistake."""
    reply = await hub_client.options(
        "/request-access",
        headers={"origin": MARKETING, "access-control-request-method": "POST"},
    )
    assert reply.status_code == 204
    assert reply.headers["access-control-allow-origin"] == MARKETING
    assert reply.headers["access-control-allow-methods"] == "POST"
    assert "content-type" in reply.headers["access-control-allow-headers"]


@pytest.mark.anyio
async def test_the_allowance_never_carries_credentials(hub_client) -> None:
    """Without this header the browser sends no cookie here and accepts no
    answer that needed one, so the one cross-origin write on an identity
    provider cannot be aimed at a signed-in visitor's session."""
    for reply in (
        await hub_client.options("/request-access", headers={"origin": MARKETING}),
        await hub_client.post(
            "/request-access", json={"email": "a@example.com"}, headers={"origin": MARKETING}
        ),
    ):
        assert "access-control-allow-credentials" not in reply.headers


@pytest.mark.anyio
async def test_the_json_path_refuses_what_the_form_path_refuses(hub_client, hub_conn) -> None:
    """One route, two content types, one set of bounds -- or the JSON caller
    becomes a way to write a row the typed form would have turned away."""
    reply = await hub_client.post(
        "/request-access", json={"email": "a" * 300}, headers={"origin": MARKETING}
    )
    assert reply.status_code == 422
    assert requests_repo.list_pending(hub_conn) == []


@pytest.mark.anyio
async def test_the_no_script_post_still_gets_its_page(hub_client, hub_conn) -> None:
    """The marketing form carries `method="post"` and an absolute action so a
    visitor with scripts off still lands somewhere real. That path posts form
    encoding and must get HTML, not the JSON the fetch path reads."""
    reply = await hub_client.post(
        "/request-access",
        data={"email": "noscript@example.com", "kind": "launch"},
        headers={"accept": "text/html"},
    )
    assert reply.status_code == 200
    assert reply.headers["content-type"].startswith("text/html")
    assert [r["email"] for r in requests_repo.list_pending(hub_conn, kind="launch")] == [
        "noscript@example.com"
    ]


# ── self-serve ───────────────────────────────────────────────────────
#
# The switch on /admin/settings. Off, a signed-in stranger's submission is a
# request an operator decides; on, it is a grant.


def _open_the_door(conn) -> None:
    from hub.repositories import settings as settings_repo

    settings_repo.set_self_serve(conn, enabled=True, by="operator@example.com")


def _signed_in(hub_client, hub_settings, email: str) -> None:
    token, _ = security.mint_session(hub_settings, email=email, now=time.time())
    hub_client.cookies.set(security.COOKIE_NAME, token, domain="hub.test", path="/")


async def test_self_serve_sends_them_to_buy_instead_of_queueing(hub_client, hub_conn, hub_settings):
    """One way in. The form hands off to the gallery's own route rather than
    granting on a second set of rules."""
    from hub.repositories import access_requests as requests_repo

    _seed_instance(hub_conn, "acme", "Acme Watch")
    _open_the_door(hub_conn)
    _signed_in(hub_client, hub_settings, "stranger@example.com")

    reply = await hub_client.post(
        "/request-access",
        data={"email": "stranger@example.com", "note": "", "instance": "acme"},
    )
    assert reply.status_code == 303
    assert reply.headers["location"] == "/domains/acme/start"
    assert not requests_repo.has_pending(hub_conn, "stranger@example.com")


async def test_nothing_is_granted_until_the_payment_lands(hub_client, hub_conn, hub_settings):
    """THE ABANDONED CHECKOUT. Pressing the button used to create a membership,
    so everybody who opened Stripe and closed the tab left an account behind
    that had never paid and could not be told from one that had."""
    from hub.repositories import users as users_repo

    _seed_instance(hub_conn, "acme", "Acme Watch")
    _open_the_door(hub_conn)
    _signed_in(hub_client, hub_settings, "stranger@example.com")

    started = await hub_client.post("/domains/acme/start")
    assert started.status_code == 303
    assert started.headers["location"].startswith("/billing/checkout?plan=annual")

    user = users_repo.by_email(hub_conn, "stranger@example.com")
    assert user is None or users_repo.seats_for(hub_conn, user["id"]) == []


async def test_the_door_is_shut_by_default(hub_client, hub_conn, hub_settings):
    from hub.repositories import access_requests as requests_repo

    _seed_instance(hub_conn, "acme", "Acme Watch")
    _signed_in(hub_client, hub_settings, "stranger@example.com")

    await hub_client.post(
        "/request-access",
        data={"email": "stranger@example.com", "note": "", "instance": "acme"},
    )
    assert requests_repo.has_pending(hub_conn, "stranger@example.com")


async def test_self_serve_never_re_admits_a_suspended_account(hub_client, hub_conn, hub_settings):
    """THE TRAP. The portal's memberless branch collapses unprovisioned,
    suspended and memberless into one page on purpose, and an auto-grant hung on
    that predicate would let somebody an operator had just suspended back in
    through the front door, repeatably."""
    from hub.repositories import access_requests as requests_repo
    from hub.repositories import users as users_repo

    _seed_instance(hub_conn, "acme", "Acme Watch")
    _open_the_door(hub_conn)

    user_id = ids.user_id()
    users_repo.create(hub_conn, user_id=user_id, email="banned@example.com", display_name=None)
    users_repo.set_status(hub_conn, user_id=user_id, status="suspended")

    _signed_in(hub_client, hub_settings, "banned@example.com")
    await hub_client.post(
        "/request-access",
        data={"email": "banned@example.com", "note": "", "instance": "acme"},
    )
    assert users_repo.seats_for(hub_conn, user_id) == []
    assert requests_repo.has_pending(hub_conn, "banned@example.com")


async def test_self_serve_keys_on_the_session_not_the_body(hub_client, hub_conn, hub_settings):
    """A signed-in person may take a domain for THEMSELVES. The address in the
    body is the half an attacker controls, and keying on it would let anybody
    with an account hand a seat to any address they can type."""
    from hub.repositories import users as users_repo

    _seed_instance(hub_conn, "acme", "Acme Watch")
    _open_the_door(hub_conn)
    _signed_in(hub_client, hub_settings, "me@example.com")

    reply = await hub_client.post(
        "/request-access",
        data={"email": "somebody.else@example.com", "note": "", "instance": "acme"},
    )
    # Sent to buy a seat for THEMSELVES, and nothing created for the address
    # they typed.
    assert reply.headers["location"] == "/domains/acme/start"
    assert users_repo.by_email(hub_conn, "somebody.else@example.com") is None


async def test_a_reader_can_buy_a_second_domain_without_asking(hub_client, hub_conn, hub_settings):
    """⭐ Holding one domain is not a reason to refuse another. The old rule
    refused anyone with a membership, because a grant used to be free -- so a
    paying reader wanting a second domain was sent to a form asking a person to
    decide.

    WHAT MUST STILL HOLD: nothing is granted on the way. The seat is created by
    the payment, so the reader still holds exactly the one they had.
    """
    from hub.repositories import access_requests as requests_repo
    from hub.repositories import users as users_repo

    first = _seed_instance(hub_conn, "acme", "Acme Watch")
    _seed_instance(hub_conn, "other", "Other Watch")
    _open_the_door(hub_conn)

    user_id = ids.user_id()
    users_repo.create(hub_conn, user_id=user_id, email="member@example.com", display_name=None)
    users_repo.grant(hub_conn, user_id=user_id, instance_id=first, role="reader")

    _signed_in(hub_client, hub_settings, "member@example.com")
    await hub_client.post(
        "/request-access",
        data={"email": "member@example.com", "note": "", "instance": "other"},
    )
    # Sent to buy, not queued -- and still holding only the domain they had.
    assert [row["slug"] for row in users_repo.seats_for(hub_conn, user_id)] == ["acme"]
    assert not requests_repo.has_pending(hub_conn, "member@example.com")

    started = await hub_client.post("/domains/other/start")
    assert started.headers["location"] == "/billing/checkout?plan=annual&instance=other"
    assert [row["slug"] for row in users_repo.seats_for(hub_conn, user_id)] == ["acme"]


async def test_the_marketing_form_cannot_self_serve(hub_client, hub_conn, hub_settings):
    """The JSON caller is a separate front page and is signed out, so it queues --
    which also means the switch cannot change what that endpoint reveals to a
    stranger (ADR 0141)."""
    from hub.repositories import access_requests as requests_repo

    _seed_instance(hub_conn, "acme", "Acme Watch")
    _open_the_door(hub_conn)
    hub_client.cookies.clear()

    reply = await hub_client.post(
        "/request-access",
        json={"email": "stranger@example.com", "note": "", "instance": "acme"},
    )
    assert reply.status_code == 200
    assert reply.json() == {"ok": True}
    assert requests_repo.has_pending(hub_conn, "stranger@example.com")


def test_blockers_names_everything_missing_at_once() -> None:
    """Reported one at a time, an operator fixes the price, learns the keys are
    missing, fixes those, and only then learns about the switch. The list is
    what makes that one trip."""
    assert billing_state.blockers(self_serve=True, takes_payment=True, price_id="price_x") == []
    assert billing_state.blockers(self_serve=False, takes_payment=False, price_id=None) == [
        billing_state.NO_PAYMENTS,
        billing_state.NO_PRICE,
        billing_state.INVITE_ONLY,
    ]
    assert billing_state.blockers(self_serve=True, takes_payment=True, price_id=None) == [
        billing_state.NO_PRICE
    ]
    # An empty string is what an unset column reads as through some drivers, and
    # it buys nothing: `price_id=""` must block exactly as `None` does.
    assert billing_state.blockers(self_serve=True, takes_payment=True, price_id="") == [
        billing_state.NO_PRICE
    ]


@pytest.mark.parametrize(
    ("self_serve", "billing_enabled", "price", "sells"),
    [
        (True, True, "price_x", True),
        (True, True, None, False),
        (True, False, "price_x", False),
        (False, True, "price_x", False),
    ],
)
def test_the_gallery_offers_a_trial_only_when_all_three_hold(
    self_serve, billing_enabled, price, sells
) -> None:
    """The defect this guards (2026-09-11): self-serve was on in production, the
    hub held no Stripe keys and no domain had a price, so every card read
    `Request access` while the operator page said the door was open. Nothing
    covered this branch at all."""
    row = {
        "slug": "acme",
        "display_name": "Acme Watch",
        "tagline": "the acme watch",
        "public_url": "https://acme.example",
        "status": "live",
        "last_status": {},
        "last_seen_at": None,
        "price_monthly_amount": 1200,
        "price_annual_amount": 12000,
        "price_currency": "usd",
        "stripe_price_monthly": price,
        "stripe_price_annual": price,
        "role": None,
    }
    html = pages.domains(
        email="stranger@example.com",
        rows=[row],
        self_serve=self_serve,
        billing_enabled=billing_enabled,
        trial_days=3,
        now=time.time(),
        theme="dark",
    )
    # A plan button, whatever it is priced at, is what selling looks like here.
    assert ("/start?plan=monthly" in html) is sells
    assert (">Request access<" in html) is not sells


def _gallery(*, self_serve=True, billing_enabled=True, price="price_x", role=None) -> str:
    row = {
        "slug": "acme",
        "display_name": "Acme Watch",
        "tagline": "the acme watch",
        "public_url": "https://acme.example",
        "status": "live",
        "last_status": {},
        "last_seen_at": None,
        "price_monthly_amount": 1200,
        "price_annual_amount": 12000,
        "price_currency": "usd",
        "stripe_price_monthly": price,
        "stripe_price_annual": price,
        "role": role,
    }
    return pages.domains(
        email="stranger@example.com",
        rows=[row],
        self_serve=self_serve,
        billing_enabled=billing_enabled,
        trial_days=3,
        now=time.time(),
        theme="dark",
    )


def test_asking_a_person_is_offered_only_where_it_buys_something() -> None:
    """With every domain buyable on the spot, an invitation to ask a person is a
    slower way to do what the button already does. It has to STAY wherever a
    card is a dead end, or a reader who can neither open nor buy the only domain
    on offer has no path forward -- which is the bug
    `test_memberless_flow_has_a_path_forward` exists for."""
    assert "Ask for something else" not in _gallery()
    # A seat they already hold is not a dead end either: the card says Open.
    assert "Ask for something else" not in _gallery(role="reader")
    # Every way a card can fail to sell keeps the way out.
    assert "Ask for something else" in _gallery(price=None)
    assert "Ask for something else" in _gallery(self_serve=False)
    assert "Ask for something else" in _gallery(billing_enabled=False)


def test_sign_out_stays_on_the_right_when_it_is_alone() -> None:
    """`.footer--row` is `space-between`, which puts a LONE child on the left --
    so dropping the link beside Sign out would walk it across the card."""
    assert "footer--row footer--end" in _gallery()
    assert "footer--end" not in _gallery(price=None)


def test_the_csp_lets_a_form_reach_stripe() -> None:
    """`form-action` is checked against every hop of a form submission's REDIRECT
    CHAIN. With `'self'` alone the trial button posted fine, the hub created a
    Checkout Session, and the browser then refused the redirect to Stripe with
    no error anywhere -- a paywall that took no money and logged nothing wrong
    (2026-09-11). Both hosts: an already-subscribed reader is bounced through
    `/billing/portal` from that same submission.
    """
    csp = middleware.SecurityHeadersMiddleware.EDGE_HEADERS[b"content-security-policy"].decode()
    directive = next(d for d in csp.split(";") if d.strip().startswith("form-action"))
    assert "https://checkout.stripe.com" in directive
    assert "https://billing.stripe.com" in directive


def test_both_plans_are_buttons_with_the_saving_beside_the_year() -> None:
    """One price and a grey `or pay monthly` link made the cheaper-per-month
    option look like the only one. A reader deciding between two prices needs to
    see two prices, and both are POSTs: a GET that creates a Checkout Session is
    reachable by a link prefetcher."""
    html = _gallery()
    assert "$120 a year" in html and "$12 a month" in html
    assert 'action="/domains/acme/start?plan=annual"' in html
    assert 'action="/domains/acme/start?plan=monthly"' in html
    assert "or pay monthly" not in html
    assert html.count('method="post"') >= 2
    # CONTAINMENT IS WHAT TIES IT. The note is a child of the year's own column,
    # so no width can wrap it onto the month: it is inside `div.plan`, and the
    # month's form is outside. The star repeats that in text for a reader
    # skimming the buttons alone.
    assert "$120 a year*" in html
    assert "$12 a month*" not in html
    plan = html[
        html.index('<div class="plan">') : html.index("</div>", html.index('<div class="plan">'))
    ]
    assert "$120 a year" in plan and "* two months free" in plan
    assert "a month" not in plan
    assert "3 days free on either plan" in html


def test_a_domain_priced_only_by_the_month_shows_one_button() -> None:
    html = _gallery_row_without_annual()
    assert "$12 a month" in html
    assert "a year" not in html
    assert "months free" not in html
    assert "3 days free." in html and "either plan" not in html


def _gallery_row_without_annual() -> str:
    return pages.domains(
        email="stranger@example.com",
        rows=[
            {
                "slug": "acme",
                "display_name": "Acme Watch",
                "tagline": "the acme watch",
                "public_url": "https://acme.example",
                "status": "live",
                "last_status": {},
                "last_seen_at": None,
                "price_monthly_amount": 1200,
                "price_annual_amount": None,
                "price_currency": "usd",
                "stripe_price_monthly": "price_m",
                "stripe_price_annual": None,
                "role": None,
            }
        ],
        self_serve=True,
        billing_enabled=True,
        trial_days=3,
        now=time.time(),
        theme="dark",
    )


def test_the_saving_is_computed_from_the_prices_not_written_down() -> None:
    """Marketing's "Two months free" is prose beside a hardcoded figure. This is
    the same claim made from what the domain actually charges, so a repriced
    domain cannot advertise a discount it stopped giving."""
    assert pages._annual_saving(1200, 12000) == "two months free"
    assert pages._annual_saving(1200, 13200) == "one month free"
    # No discount at all, and a yearly price that is dearer than twelve months.
    assert pages._annual_saving(1200, 14400) == ""
    assert pages._annual_saving(1200, 15000) == ""
    # Not a whole number of months: silence beats "1.87 months free".
    assert pages._annual_saving(1200, 12500) == ""
    assert pages._annual_saving(None, 12000) == ""
    assert pages._annual_saving(1200, None) == ""


async def _price(conn, slug="acme"):
    instances_repo.set_pricing(
        conn,
        slug=slug,
        product_id="prod_x",
        price_monthly="price_m",
        price_annual="price_a",
        monthly_amount=1200,
        annual_amount=12000,
        currency="usd",
    )


async def test_asking_is_not_offered_to_somebody_who_could_just_buy(
    hub_client, hub_conn, hub_settings, monkeypatch
):
    """Reached by a stale link, a bookmark, or the back button out of Checkout,
    a form promising that a person will decide reads as the purchase having
    failed. Somebody who can take the domain on the spot goes to the gallery."""
    _seed_instance(hub_conn, "acme", "Acme Watch")
    await _price(hub_conn)
    settings_repo.set_self_serve(hub_conn, enabled=True, by=None)
    monkeypatch.setattr(type(hub_settings), "billing_enabled", property(lambda self: True))
    token, _ = security.mint_session(hub_settings, email="stranger@example.com", now=time.time())
    hub_client.cookies.set(security.COOKIE_NAME, token, domain="hub.test", path="/")

    bounced = await hub_client.get("/request-access")
    assert bounced.status_code == 303
    assert bounced.headers["location"] == "/"


async def test_asking_stays_open_where_nothing_can_be_bought(hub_client, hub_conn, hub_settings):
    """The form is the only way forward on a hub that sells nothing, which is
    the dead end `test_memberless_flow_has_a_path_forward` exists for. The
    fixture holds no Stripe keys, so this is also the state a hub ships in."""
    _seed_instance(hub_conn, "acme", "Acme Watch")
    settings_repo.set_self_serve(hub_conn, enabled=True, by=None)
    token, _ = security.mint_session(hub_settings, email="stranger@example.com", now=time.time())
    hub_client.cookies.set(security.COOKIE_NAME, token, domain="hub.test", path="/")

    assert "<h1>Request access</h1>" in (await hub_client.get("/request-access")).text
    # And signed out, whatever else is true: the marketing form posts here.
    hub_client.cookies.clear()
    assert "<h1>Request access</h1>" in (await hub_client.get("/request-access")).text


async def test_a_suspended_account_is_not_bounced_into_a_loop(
    hub_client, hub_conn, hub_settings, monkeypatch
):
    """⭐ `/domains/{slug}/start` sends a suspended account HERE. Bouncing it back
    to a gallery whose buttons return it here is an infinite redirect on the one
    page that exists to catch it, which is why the bounce is keyed on
    `_may_serve_self` rather than merely on holding a session."""
    instance_id = _seed_instance(hub_conn, "acme", "Acme Watch")
    await _price(hub_conn)
    settings_repo.set_self_serve(hub_conn, enabled=True, by=None)
    monkeypatch.setattr(type(hub_settings), "billing_enabled", property(lambda self: True))
    user_id = _seed_user(hub_conn, "banned@example.com")
    users_repo.set_status(hub_conn, user_id, "suspended")
    token, _ = security.mint_session(hub_settings, email="banned@example.com", now=time.time())
    hub_client.cookies.set(security.COOKIE_NAME, token, domain="hub.test", path="/")

    assert "<h1>Request access</h1>" in (await hub_client.get("/request-access")).text
    # The route that sends them here still does, rather than selling them a seat.
    start = await hub_client.post("/domains/acme/start")
    assert start.status_code == 303
    assert start.headers["location"] == "/request-access?instance=acme"
    assert instance_id


async def test_the_front_door_shows_what_they_hold_beside_what_they_could_add(
    hub_client, hub_conn, hub_settings, monkeypatch
):
    """⭐ One page. A reader who holds one domain sees it as theirs, and every
    other domain as something they can buy on the spot -- rather than a list of
    memberships with a link to a form."""
    mine = _seed_instance(hub_conn, "acme", "Acme Watch")
    _seed_instance(hub_conn, "other", "Other Watch")
    instances_repo.set_pricing(
        hub_conn,
        slug="other",
        product_id="p",
        price_monthly="price_m",
        price_annual="price_a",
        monthly_amount=1200,
        annual_amount=12000,
        currency="usd",
    )
    _open_the_door(hub_conn)
    monkeypatch.setattr(type(hub_settings), "billing_enabled", property(lambda self: True))
    user_id = _seed_user(hub_conn, "member@example.com")
    users_repo.grant(hub_conn, user_id=user_id, instance_id=mine, role="reader")
    _signed_in(hub_client, hub_settings, "member@example.com")

    page = (await hub_client.get("/")).text
    assert "<h1>Your domains</h1>" in page
    # Theirs: a way in. The other: a price.
    assert "/api/auth/login" in page
    assert "$120 a year" in page and "$12 a month" in page
    # The chooser's standing invitation to ask a person is gone, because
    # everything on the page is either theirs or buyable.
    assert "Request access to another domain" not in page
    assert "Ask for something else" not in page


async def test_the_sign_in_page_stops_offering_the_queue_once_a_domain_sells(
    hub_client, hub_conn, hub_settings, monkeypatch
):
    """⭐ The same rule as the chooser, one page earlier. `/request-access`
    bounces a stranger who could buy straight back to the front door, so while
    anything is on sale the offer to ask a person is a link to nowhere."""
    _seed_instance(hub_conn, "acme", "Acme Watch")
    instances_repo.set_pricing(
        hub_conn,
        slug="acme",
        product_id="p",
        price_monthly="price_m",
        price_annual="price_a",
        monthly_amount=1200,
        annual_amount=12000,
        currency="usd",
    )

    # Invite-only: asking a person IS the only way in, so the offer stands.
    assert "Request access" in (await hub_client.get("/")).text

    _open_the_door(hub_conn)
    monkeypatch.setattr(type(hub_settings), "billing_enabled", property(lambda self: True))
    page = (await hub_client.get("/")).text
    assert "Sign in with Google" in page
    assert "Request access" not in page


async def test_a_domain_they_hold_survives_going_back_to_provisioning(
    hub_client, hub_conn, hub_settings
):
    """`list_public` is live-or-offline, but a membership outlives a domain
    being taken back to `provisioning`. A front door built from the public list
    alone would drop it, which to that reader is their access disappearing."""
    mine = _seed_instance(hub_conn, "acme", "Acme Watch", live=False)
    user_id = _seed_user(hub_conn, "member@example.com")
    users_repo.grant(hub_conn, user_id=user_id, instance_id=mine, role="reader")
    _signed_in(hub_client, hub_settings, "member@example.com")

    assert instances_repo.by_slug(hub_conn, "acme")["status"] == "provisioning"
    assert not [row["slug"] for row in instances_repo.list_public(hub_conn)]
    assert "Acme Watch" in (await hub_client.get("/")).text


async def test_a_suspended_member_is_still_refused(hub_client, hub_conn, hub_settings):
    """The half of `_may_serve_self` that is the point of it. A suspended
    account has been refused BY somebody, and buying its way back in would make
    suspension a suggestion."""
    _seed_instance(hub_conn, "acme", "Acme Watch")
    other = _seed_instance(hub_conn, "other", "Other Watch")
    _open_the_door(hub_conn)
    user_id = _seed_user(hub_conn, "banned@example.com")
    users_repo.grant(hub_conn, user_id=user_id, instance_id=other, role="reader")
    users_repo.set_status(hub_conn, user_id, "suspended")
    _signed_in(hub_client, hub_settings, "banned@example.com")

    started = await hub_client.post("/domains/acme/start")
    assert started.headers["location"] == "/request-access?instance=acme"
    assert [row["slug"] for row in users_repo.seats_for(hub_conn, user_id)] == ["other"]
