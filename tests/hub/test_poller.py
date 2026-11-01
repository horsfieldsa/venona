"""The poll cycle: verbatim storage, the miss counter, and its blast radius."""

from __future__ import annotations

import json

import httpx
import pytest
from hub.poller import poll_once
from hub.repositories import instances as instances_repo
from hubmaterial import TEST_SEAL_KEY

from hub import mgmt_client, poller, seal, stripe

MGMT_TOKEN = "vmgmt_" + "c" * 64

#: The shipped MgmtStatus shape (venona/api/schemas/mgmt.py), as a poll would
#: fetch it. The hub stores it verbatim; nothing here parses it.
STATUS_BODY = {
    "version": "0.1.0",
    "domain_slug": "acme",
    "display_name": "Acme Watch",
    "setup": {
        "configured": True,
        "slug": "acme",
        "display_name": "Acme Watch",
        "has_dossier": True,
        "drafts": [],
        "active_run": None,
    },
    "documents": 42,
    "entities": 17,
    "users": {"admins": 1, "readers": 2},
}


def _seed(conn, slug="acme", sealed_token=None) -> str:
    from hub import ids

    instance_id = ids.instance_id()
    instances_repo.create(
        conn,
        instance_id=instance_id,
        slug=slug,
        display_name=slug.title(),
        tagline="",
        public_url=f"https://{slug}.example",
        oidc_client_id=ids.client_id(),
        oidc_client_secret_hash="x",
        mgmt_token_sealed=sealed_token or seal.seal(TEST_SEAL_KEY, MGMT_TOKEN),
    )
    return instance_id


def _serving(monkeypatch, *, fail_slugs: set[str] = frozenset()) -> list[str]:
    """Wire the mgmt transport to answer STATUS_BODY, failing named slugs.
    Returns the list of Authorization headers seen."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization", ""))
        assert request.url.path == "/api/mgmt/status"
        slug = request.url.host.split(".")[0]
        if slug in fail_slugs:
            return httpx.Response(503, json={"detail": "down"})
        return httpx.Response(200, json=STATUS_BODY)

    monkeypatch.setattr(mgmt_client, "MGMT_TRANSPORT", httpx.MockTransport(handler))
    return seen


async def test_success_stores_the_envelope_verbatim(hub_conn, hub_settings, monkeypatch):
    instance_id = _seed(hub_conn)
    seen = _serving(monkeypatch)

    results = await poll_once(hub_settings)
    assert results == {"acme": "live"}
    assert seen == [f"Bearer {MGMT_TOKEN}"]

    row = instances_repo.by_id(hub_conn, instance_id)
    assert row["status"] == "live"
    assert row["last_status"] == STATUS_BODY
    assert row["last_seen_at"] is not None
    assert row["poll_misses"] == 0


async def test_three_misses_offline_then_recovery(hub_conn, hub_settings, monkeypatch):
    instance_id = _seed(hub_conn)
    instances_repo.record_poll_success(hub_conn, instance_id, STATUS_BODY)  # live first

    _serving(monkeypatch, fail_slugs={"acme"})
    assert (await poll_once(hub_settings)) == {"acme": "live"}  # miss 1
    assert (await poll_once(hub_settings)) == {"acme": "live"}  # miss 2
    assert (await poll_once(hub_settings)) == {"acme": "offline"}  # miss 3 demotes

    # Offline rows keep being polled -- that is the recovery path.
    _serving(monkeypatch)
    assert (await poll_once(hub_settings)) == {"acme": "live"}
    row = instances_repo.by_id(hub_conn, instance_id)
    assert row["poll_misses"] == 0


async def test_provisioning_never_demotes(hub_conn, hub_settings, monkeypatch):
    instance_id = _seed(hub_conn)  # status: provisioning, never seen
    _serving(monkeypatch, fail_slugs={"acme"})
    for _ in range(5):
        await poll_once(hub_settings)
    row = instances_repo.by_id(hub_conn, instance_id)
    assert row["status"] == "provisioning"  # not-up-yet is not DOWN
    assert row["poll_misses"] == 5


async def test_retired_rows_are_not_polled(hub_conn, hub_settings, monkeypatch):
    instance_id = _seed(hub_conn)
    instances_repo.set_fields(hub_conn, instance_id, status="retired")
    seen = _serving(monkeypatch)
    assert (await poll_once(hub_settings)) == {}
    assert seen == []


async def test_one_bad_instance_never_kills_the_cycle(hub_conn, hub_settings, monkeypatch):
    # First row's sealed token is garbage -- unseal raises, not just a 503.
    _seed(hub_conn, slug="broken", sealed_token="not-even-fernet")
    good = _seed(hub_conn, slug="acme")
    _serving(monkeypatch)

    results = await poll_once(hub_settings)
    assert results["acme"] == "live"
    assert results["broken"] == "provisioning"
    assert instances_repo.by_id(hub_conn, good)["status"] == "live"


async def test_provision_proxy_passes_the_instance_answer_through(
    hub_client, hub_conn, monkeypatch
):
    admin = {"Authorization": "Bearer " + "a" * 40}
    instance_id = _seed(hub_conn)
    instances_repo.record_poll_success(hub_conn, instance_id, STATUS_BODY)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/mgmt/provision-user"
        assert request.headers["authorization"] == f"Bearer {MGMT_TOKEN}"
        return httpx.Response(
            200,
            json={
                "email": "new@example.com",
                "action": "add",
                "role": "reader",
                "status": "created",
            },
        )

    monkeypatch.setattr(mgmt_client, "MGMT_TRANSPORT", httpx.MockTransport(handler))
    response = await hub_client.post(
        f"/admin/instances/{instance_id}/provision-user",
        json={"email": "new@example.com", "role": "reader", "action": "add"},
        headers=admin,
    )
    assert response.status_code == 200
    assert response.json() == {
        "email": "new@example.com",
        "action": "add",
        "role": "reader",
        "status": "created",
    }

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "no user 'ghost@example.com' on this instance"})

    monkeypatch.setattr(mgmt_client, "MGMT_TRANSPORT", httpx.MockTransport(refuse))
    passthrough = await hub_client.post(
        f"/admin/instances/{instance_id}/provision-user",
        json={"email": "ghost@example.com", "action": "suspend"},
        headers=admin,
    )
    assert passthrough.status_code == 404


@pytest.mark.negative
async def test_bad_action_rejected_before_any_outbound_call(hub_client, hub_conn, monkeypatch):
    admin = {"Authorization": "Bearer " + "a" * 40}
    instance_id = _seed(hub_conn)
    calls: list[str] = []
    monkeypatch.setattr(
        mgmt_client,
        "MGMT_TRANSPORT",
        httpx.MockTransport(lambda request: calls.append(str(request.url)) or httpx.Response(200)),
    )
    response = await hub_client.post(
        f"/admin/instances/{instance_id}/provision-user",
        json={"email": "a@example.com", "action": "obliterate"},
        headers=admin,
    )
    assert response.status_code == 422
    assert calls == []


# ── the switches ─────────────────────────────────────────────────────


async def test_the_poll_carries_a_switch_that_is_behind(hub_conn, hub_settings, monkeypatch):
    """The push rides the status poll rather than a sweep of its own.

    The loop already reaches every live instance and already skips the dead
    ones, and an instance that has just answered a status poll is by definition
    one that can take a push. A second sweep would need its own budget and its
    own liveness rules for no gain.
    """
    instance_id = _seed(hub_conn)
    pushed: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/mgmt/settings":
            pushed.append(json.loads(request.content))
            return httpx.Response(200, json={"billing_enforced": True})
        return httpx.Response(200, json=STATUS_BODY)

    monkeypatch.setattr(mgmt_client, "MGMT_TRANSPORT", httpx.MockTransport(handler))

    # Nothing pending: the versions match, so the poll must not push at all.
    await poll_once(hub_settings)
    assert pushed == []

    instances_repo.set_billing_enforced(hub_conn, instance_id=instance_id, enforced=True)
    await poll_once(hub_settings)
    assert pushed == [{"billing_enforced": True}]

    row = instances_repo.by_id(hub_conn, instance_id)
    assert row["settings_pushed_version"] == row["settings_version"]

    # And it stops. A watermark that did not move would re-send the same switch
    # every five minutes for the life of the instance.
    await poll_once(hub_settings)
    assert len(pushed) == 1


async def test_a_refused_switch_stays_pending(hub_conn, hub_settings, monkeypatch):
    """A domain that will not take it keeps its debt. The watermark only moves
    on a delivered push, so the retry needs no bookkeeping of its own -- and a
    refusal must not demote an instance that just answered a status poll."""
    instance_id = _seed(hub_conn)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/mgmt/settings":
            return httpx.Response(500, json={"detail": "no"})
        return httpx.Response(200, json=STATUS_BODY)

    monkeypatch.setattr(mgmt_client, "MGMT_TRANSPORT", httpx.MockTransport(handler))
    instances_repo.set_billing_enforced(hub_conn, instance_id=instance_id, enforced=True)

    assert await poll_once(hub_settings) == {"acme": "live"}
    row = instances_repo.by_id(hub_conn, instance_id)
    assert row["settings_pushed_version"] < row["settings_version"]


async def test_a_price_edited_in_stripe_corrects_the_quote(hub_conn, hub_settings, monkeypatch):
    """The gallery quotes a stored amount and Checkout charges a Price ID. They
    agree only because one command wrote both, so a price edited in Stripe's
    dashboard parts them -- and the half a stranger reads before paying is the
    stored one."""
    from hub.repositories import instances as repo

    instance_id = _seed(hub_conn)
    repo.set_pricing(
        hub_conn,
        slug="acme",
        product_id="prod_1",
        price_monthly="price_m",
        price_annual="price_a",
        monthly_amount=1200,
        annual_amount=12000,
        currency="usd",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/prices/price_m":
            return httpx.Response(200, json={"unit_amount": 4900, "currency": "usd"})
        if request.url.path == "/v1/prices/price_a":
            return httpx.Response(200, json={"unit_amount": 49000, "currency": "usd"})
        return httpx.Response(200, json={"data": [], "has_more": False})

    monkeypatch.setattr(stripe, "STRIPE_TRANSPORT", httpx.MockTransport(handler))
    assert await poller.refresh_prices(hub_settings, hub_conn) == 1

    row = repo.by_id(hub_conn, instance_id)
    assert row["price_monthly_amount"] == 4900
    assert row["price_annual_amount"] == 49000

    # And it is quiet when nothing moved: a sweep that rewrote every row every
    # hour would bury the one line that says somebody edited money by hand.
    assert await poller.refresh_prices(hub_settings, hub_conn) == 0


# ── a separate front site's version ─────────────────────────────────────

SITE_BODY = {"version": "1.0.0", "commit": "abcdef1", "built_at": "2026-09-17T09:00:00Z"}


def _site(monkeypatch, response: httpx.Response) -> list[str]:
    """Bind the SITE transport, which is deliberately not the mgmt one.

    `mgmt_client._client()` reads `MGMT_TRANSPORT`, and sharing it would make
    every instance-poll mock in this file silently answer the site read too --
    which is the reason `site_client` is a separate module at all.
    """
    from hub import site_client

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return response

    monkeypatch.setattr(site_client, "SITE_TRANSPORT", httpx.MockTransport(handler))
    return seen


async def test_the_site_version_is_stored_on_a_successful_read(hub_conn, hub_settings, monkeypatch):
    from hub.repositories import site as site_repo

    _serving(monkeypatch)
    _seed(hub_conn)
    seen = _site(monkeypatch, httpx.Response(200, json=SITE_BODY))

    await poll_once(hub_settings)

    assert seen == [f"{hub_settings.site_url}/version.json"]
    row = site_repo.read(hub_conn)
    assert row["last_read"] == SITE_BODY
    assert row["last_seen_at"] is not None


async def test_a_failed_site_read_keeps_the_last_good_one(hub_conn, hub_settings, monkeypatch):
    """A site that did not answer this cycle is not a site with no version.

    Nulling the row on a refusal would blank the operator page at the first CDN
    blip and lose the only record of what is actually published.
    """
    from hub.repositories import site as site_repo

    _serving(monkeypatch)
    _seed(hub_conn)

    _site(monkeypatch, httpx.Response(200, json=SITE_BODY))
    await poll_once(hub_settings)
    first = site_repo.read(hub_conn)["last_seen_at"]

    _site(monkeypatch, httpx.Response(500, text="down"))
    await poll_once(hub_settings)

    row = site_repo.read(hub_conn)
    assert row["last_read"] == SITE_BODY, "the reading survived the bad fetch"
    assert row["last_seen_at"] == first, "and it did not pretend to be fresh"


async def test_a_site_read_that_blows_up_does_not_cost_the_cycle(
    hub_conn, hub_settings, monkeypatch
):
    """The separate property, and the important one.

    `poll_once` runs the entitlement push, the Stripe reconcile and the notice
    drain in sequence after the status pass. An error escaping the site read
    would abort all three -- so an operator would lose their alerts in order to
    report a version nobody is waiting on. `hub poll --once` would exit non-zero
    with it.
    """
    from hub import site_client

    _serving(monkeypatch)
    _seed(hub_conn)

    drained: list[bool] = []
    monkeypatch.setattr(poller, "_send_notices", _recording(drained))

    def explode(_url: str) -> None:
        raise RuntimeError("not an httpx error at all")

    monkeypatch.setattr(site_client, "fetch_version", explode)

    results = await poll_once(hub_settings)

    assert results == {"acme": "live"}, "the status pass still landed"
    assert drained == [True], "and the notice drain still ran"


def _recording(into: list[bool]):
    async def _noop(_settings, _conn) -> None:
        into.append(True)

    return _noop
