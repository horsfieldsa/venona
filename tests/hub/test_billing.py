"""Webhooks applied: replay, out-of-order, notices, and what never leaks.

The signature half lives in `test_stripe_webhook.py`. This is what happens
AFTER a signature verifies, which is where a billing integration actually goes
wrong: an event applied twice, an event applied in the wrong order, or a card
form offered to somebody whose card is fine.
"""

from __future__ import annotations

import time

import pytest
from hub.repositories import billing_events as events_repo
from hub.repositories import subscriptions as subs_repo
from hub.repositories import users as users_repo

from hub import billing_apply, ids

SUB = "sub_live_1"
NOW = 1_700_000_000


@pytest.fixture
def seat(hub_conn):
    """A person with a membership on one instance -- the thing a subscription
    is about."""
    from hub.repositories import instances as instances_repo

    instance_id = ids.instance_id()
    instances_repo.create(
        hub_conn,
        instance_id=instance_id,
        slug="defense",
        display_name="Defense",
        tagline="t",
        public_url="https://defense.example",
        oidc_client_id="cid",
        oidc_client_secret_hash="hash",
        mgmt_token_sealed="sealed",
    )
    user_id = ids.user_id()
    users_repo.create(hub_conn, user_id=user_id, email="a@example.com", display_name=None)
    users_repo.grant(hub_conn, user_id=user_id, instance_id=instance_id, role="reader")
    # An instance sells its OWN product. Without prices it cannot take money at
    # all, which is deliberate -- falling back to a hub-wide pair is the bug
    # migration 0006 removes.
    instances_repo.set_pricing(
        hub_conn,
        slug="defense",
        product_id="prod_defense",
        price_monthly="price_defense_monthly",
        price_annual="price_defense_annual",
        monthly_amount=1200,
        annual_amount=12000,
        currency="usd",
    )
    return {"user_id": user_id, "instance_id": instance_id}


def sub_event(seat, *, status="active", created=NOW, **extra):
    return {
        "id": f"evt_{created}_{status}",
        "type": "customer.subscription.updated",
        "created": created,
        "data": {
            "object": {
                "id": SUB,
                "status": status,
                "current_period_end": NOW + 30 * 86400,
                "cancel_at_period_end": False,
                "metadata": {"user_id": seat["user_id"], "instance_id": seat["instance_id"]},
                "items": {"data": [{"price": {"id": "price_monthly"}}]},
                **extra,
            }
        },
    }


def test_a_subscription_event_creates_the_row(hub_conn, seat) -> None:
    assert billing_apply.apply_event(hub_conn, sub_event(seat)) is True
    row = subs_repo.by_stripe_id(hub_conn, SUB)
    assert row["status"] == "active"
    assert row["user_id"] == seat["user_id"]


def test_applying_the_same_event_twice_leaves_one_row(hub_conn, seat) -> None:
    """Idempotence at the apply layer, independent of the ledger. Both exist
    because they fail differently: the ledger stops a redelivery reaching here,
    and this stops a redelivery that does from duplicating."""
    event = sub_event(seat)
    billing_apply.apply_event(hub_conn, event)
    billing_apply.apply_event(hub_conn, event)
    rows = hub_conn.execute("SELECT id FROM subscriptions").fetchall()
    assert len(rows) == 1


def test_a_stale_event_does_not_overwrite_a_newer_one(hub_conn, seat) -> None:
    """The failure this prevents: Stripe sends events concurrently, so
    `updated` for 9:00 can arrive after `updated` for 9:01. Applying the last
    message to arrive would cancel a subscription that was just reinstated."""
    billing_apply.apply_event(hub_conn, sub_event(seat, status="active", created=NOW + 60))
    billing_apply.apply_event(hub_conn, sub_event(seat, status="canceled", created=NOW))
    assert subs_repo.by_stripe_id(hub_conn, SUB)["status"] == "active"


def test_a_newer_event_does_overwrite(hub_conn, seat) -> None:
    billing_apply.apply_event(hub_conn, sub_event(seat, status="active", created=NOW))
    billing_apply.apply_event(hub_conn, sub_event(seat, status="canceled", created=NOW + 60))
    assert subs_repo.by_stripe_id(hub_conn, SUB)["status"] == "canceled"


def test_cancel_then_resubscribe_keeps_both(hub_conn, seat) -> None:
    """The win-back path. A full UNIQUE(user_id, instance_id) would reject this
    insert at the exact moment somebody hands us money again."""
    billing_apply.apply_event(hub_conn, sub_event(seat, status="canceled"))
    event = sub_event(seat, status="active", created=NOW + 100)
    event["data"]["object"]["id"] = "sub_live_2"
    billing_apply.apply_event(hub_conn, event)
    rows = hub_conn.execute("SELECT status FROM subscriptions ORDER BY created_at").fetchall()
    assert [r["status"] for r in rows] == ["canceled", "active"]
    assert (
        subs_repo.live_for(hub_conn, user_id=seat["user_id"], instance_id=seat["instance_id"])[
            "stripe_subscription_id"
        ]
        == "sub_live_2"
    )


# ── the entitlement counter ──────────────────────────────────────────


def test_any_change_bumps_the_version(hub_conn, seat) -> None:
    billing_apply.apply_event(hub_conn, sub_event(seat))
    row = hub_conn.execute("SELECT entitlement_version FROM memberships").fetchone()
    assert row["entitlement_version"] > 0


def test_reapplying_the_same_state_does_not_bump_the_version(hub_conn, seat) -> None:
    """The hourly sweep re-applies every subscription on the account. An
    unconditional bump would make every one of them pending again, so a push
    queue bounded per cycle spends its whole budget re-sending state nothing has
    changed while a real change waits behind it."""
    billing_apply.apply_event(hub_conn, sub_event(seat))
    before = hub_conn.execute("SELECT entitlement_version FROM memberships").fetchone()[
        "entitlement_version"
    ]
    billing_apply.reconcile_subscription(hub_conn, sub_event(seat)["data"]["object"], now=NOW + 500)
    after = hub_conn.execute("SELECT entitlement_version FROM memberships").fetchone()[
        "entitlement_version"
    ]
    assert after == before


def test_a_notice_only_change_still_bumps_the_version(hub_conn, seat) -> None:
    """THE failure that looks like everything working. A first decline leaves
    the reader entitled and changes only the banner; if the counter tracked
    access alone, in-app dunning would silently never fire."""
    billing_apply.apply_event(hub_conn, sub_event(seat, status="past_due", created=NOW))
    before = hub_conn.execute("SELECT entitlement_version FROM memberships").fetchone()[
        "entitlement_version"
    ]
    billing_apply.apply_event(
        hub_conn,
        {
            "id": "evt_inv_1",
            "type": "invoice.payment_failed",
            "created": NOW + 10,
            "data": {"object": {"subscription": SUB, "next_payment_attempt": NOW + 3 * 86400}},
        },
    )
    after = hub_conn.execute("SELECT entitlement_version FROM memberships").fetchone()[
        "entitlement_version"
    ]
    assert after > before


# ── notices ──────────────────────────────────────────────────────────


def test_a_failed_payment_with_retries_left_is_not_the_final_notice(hub_conn, seat) -> None:
    billing_apply.apply_event(hub_conn, sub_event(seat, status="past_due"))
    billing_apply.apply_event(
        hub_conn,
        {
            "id": "evt_i1",
            "type": "invoice.payment_failed",
            "created": NOW,
            "data": {"object": {"subscription": SUB, "next_payment_attempt": NOW + 86400}},
        },
    )
    assert subs_repo.by_stripe_id(hub_conn, SUB)["notice"] == "payment_failed"


def test_the_last_retry_escalates_to_final_notice(hub_conn, seat) -> None:
    billing_apply.apply_event(hub_conn, sub_event(seat, status="past_due"))
    billing_apply.apply_event(
        hub_conn,
        {
            "id": "evt_i2",
            "type": "invoice.payment_failed",
            "created": NOW,
            "data": {"object": {"subscription": SUB, "next_payment_attempt": None}},
        },
    )
    assert subs_repo.by_stripe_id(hub_conn, SUB)["notice"] == "final_notice"


def test_sca_is_not_a_failed_payment(hub_conn, seat) -> None:
    """The distinction most integrations miss. The card is fine and the bank
    wants a tap; telling this person to update their card sends them to
    re-enter a card that works, and they conclude the product is broken."""
    billing_apply.apply_event(hub_conn, sub_event(seat, status="past_due"))
    billing_apply.apply_event(
        hub_conn,
        {
            "id": "evt_i3",
            "type": "invoice.payment_action_required",
            "created": NOW,
            "data": {"object": {"subscription": SUB, "due_date": NOW + 86400}},
        },
    )
    assert subs_repo.by_stripe_id(hub_conn, SUB)["notice"] == "action_required"


def test_a_subscription_update_does_not_erase_an_invoice_notice(hub_conn, seat) -> None:
    """A `customer.subscription.updated` arriving while past_due must not
    recompute `action_required` away -- the subscription's own status cannot
    reproduce it, so recomputing means losing it."""
    billing_apply.apply_event(hub_conn, sub_event(seat, status="past_due", created=NOW))
    billing_apply.apply_event(
        hub_conn,
        {
            "id": "evt_i4",
            "type": "invoice.payment_action_required",
            "created": NOW + 1,
            "data": {"object": {"subscription": SUB, "due_date": NOW + 86400}},
        },
    )
    billing_apply.apply_event(hub_conn, sub_event(seat, status="past_due", created=NOW + 2))
    assert subs_repo.by_stripe_id(hub_conn, SUB)["notice"] == "action_required"


def test_paying_clears_the_notice(hub_conn, seat) -> None:
    billing_apply.apply_event(hub_conn, sub_event(seat, status="past_due"))
    billing_apply.apply_event(
        hub_conn,
        {
            "id": "evt_i5",
            "type": "invoice.payment_failed",
            "created": NOW,
            "data": {"object": {"subscription": SUB, "next_payment_attempt": NOW + 86400}},
        },
    )
    billing_apply.apply_event(
        hub_conn,
        {
            "id": "evt_i6",
            "type": "invoice.paid",
            "created": NOW + 10,
            "data": {"object": {"subscription": SUB}},
        },
    )
    assert subs_repo.by_stripe_id(hub_conn, SUB)["notice"] is None


# ── the invoice shape ────────────────────────────────────────────────
#
# `API_VERSION` pins Basil, which MOVED the subscription off the top level of an
# invoice. Reading only the old field is not a partial failure: every invoice
# handler returns early, so no notice ever fires and in-app dunning is silently
# dead. These fixtures are the shapes Stripe actually sends, not the shape the
# parser happens to want.


def basil_invoice_event(event_id="evt_basil", kind="invoice.payment_failed", **obj):
    """Basil: `invoice.parent.subscription_details.subscription`."""
    return {
        "id": event_id,
        "type": kind,
        "created": NOW,
        "data": {
            "object": {
                "parent": {"subscription_details": {"subscription": SUB}},
                "next_payment_attempt": NOW + 86400,
                **obj,
            }
        },
    }


def legacy_invoice_event(event_id="evt_legacy", kind="invoice.payment_failed", **obj):
    """Pre-Basil: a top-level `invoice.subscription`."""
    return {
        "id": event_id,
        "type": kind,
        "created": NOW,
        "data": {"object": {"subscription": SUB, "next_payment_attempt": NOW + 86400, **obj}},
    }


def test_a_basil_invoice_is_understood(hub_conn, seat) -> None:
    """The pinned version's shape. Without this the dunning banner never
    appears for anybody, and nothing anywhere reports a problem."""
    billing_apply.apply_event(hub_conn, sub_event(seat, status="past_due"))
    assert billing_apply.apply_event(hub_conn, basil_invoice_event()) is True
    assert subs_repo.by_stripe_id(hub_conn, SUB)["notice"] == "payment_failed"


def test_a_pre_basil_invoice_is_still_understood(hub_conn, seat) -> None:
    """Both shapes, because the pin is a constant somebody will move, and an
    integration that breaks quietly on a version bump is what pinning was
    supposed to prevent."""
    billing_apply.apply_event(hub_conn, sub_event(seat, status="past_due"))
    assert billing_apply.apply_event(hub_conn, legacy_invoice_event()) is True
    assert subs_repo.by_stripe_id(hub_conn, SUB)["notice"] == "payment_failed"


def test_an_expanded_subscription_object_is_understood(hub_conn, seat) -> None:
    """Stripe sends the id or the whole object, depending on what was expanded."""
    billing_apply.apply_event(hub_conn, sub_event(seat, status="past_due"))
    event = basil_invoice_event(event_id="evt_expanded")
    event["data"]["object"]["parent"]["subscription_details"]["subscription"] = {"id": SUB}
    assert billing_apply.apply_event(hub_conn, event) is True
    assert subs_repo.by_stripe_id(hub_conn, SUB)["notice"] == "payment_failed"


def test_an_invoice_with_no_subscription_is_ignored(hub_conn, seat) -> None:
    """A one-off invoice is not about a subscription and must not be guessed at."""
    billing_apply.apply_event(hub_conn, sub_event(seat, status="active"))
    event = {"id": "evt_oneoff", "type": "invoice.paid", "created": NOW, "data": {"object": {}}}
    assert billing_apply.apply_event(hub_conn, event) is False


# ── the ledger ───────────────────────────────────────────────────────


def test_the_ledger_recognises_a_replay(hub_conn) -> None:
    assert events_repo.claim(hub_conn, event_id="evt_x", event_type="t", payload_digest="d")
    assert not events_repo.claim(hub_conn, event_id="evt_x", event_type="t", payload_digest="d")


def test_a_failed_apply_leaves_no_claim_behind(hub_conn) -> None:
    """THE reason claim and apply share one transaction.

    The connection is `autocommit=True`, so a bare claim commits on the spot and
    anything that stops the process before the work is done -- a SIGTERM during
    a deploy, an OOM kill -- would leave the row claimed and the work undone.
    Stripe's retries would then take it for a replay and answer 200, and a
    charged customer would hold no subscription row at all.

    Rolled back, the next retry starts clean.
    """
    try:
        with hub_conn.transaction():
            assert events_repo.claim(hub_conn, event_id="evt_y", event_type="t", payload_digest="d")
            raise RuntimeError("the process died here")
    except RuntimeError:
        pass
    assert events_repo.claim(hub_conn, event_id="evt_y", event_type="t", payload_digest="d"), (
        "the claim survived a rollback, so a crash mid-apply would strand the event"
    )


# ── the push watermark ───────────────────────────────────────────────


def test_a_pending_push_appears_once_pushed(hub_conn, seat) -> None:
    billing_apply.apply_event(hub_conn, sub_event(seat))
    hub_conn.execute("UPDATE instances SET status = 'live'")
    pending = subs_repo.pending_pushes(hub_conn, limit=10)
    assert len(pending) == 1
    subs_repo.record_push(
        hub_conn,
        user_id=seat["user_id"],
        instance_id=seat["instance_id"],
        version=pending[0]["entitlement_version"],
    )
    assert subs_repo.pending_pushes(hub_conn, limit=10) == []


def test_the_watermark_never_moves_backwards(hub_conn, seat) -> None:
    """A push in flight can be overtaken by a newer bump. Writing the delivered
    version unconditionally would move the watermark backwards and re-send
    forever, or forward past a version nobody sent."""
    billing_apply.apply_event(hub_conn, sub_event(seat))
    subs_repo.record_push(
        hub_conn, user_id=seat["user_id"], instance_id=seat["instance_id"], version=5
    )
    subs_repo.record_push(
        hub_conn, user_id=seat["user_id"], instance_id=seat["instance_id"], version=2
    )
    row = hub_conn.execute("SELECT entitlement_pushed_version FROM memberships").fetchone()
    assert row["entitlement_pushed_version"] == 5


def test_an_offline_instance_is_not_pushed_at_forever(hub_conn, seat) -> None:
    """Re-POSTing at a dead host every five minutes achieves nothing and buries
    the live ones behind it."""
    billing_apply.apply_event(hub_conn, sub_event(seat))
    hub_conn.execute("UPDATE instances SET status = 'live', poll_misses = 9")
    assert subs_repo.pending_pushes(hub_conn, limit=10) == []


# ── the routes ───────────────────────────────────────────────────────
#
# Checkout and the portal are top-level NAVIGATIONS ending in a 303, so these
# assert on Location rather than on a body.


import httpx  # noqa: E402

from hub import security, stripe  # noqa: E402


@pytest.fixture
def paying_settings(hub_settings):
    """A hub configured to take money. All five variables or none: a secret key
    with no webhook secret would take money and never hear that it had."""
    return hub_settings.model_copy(
        update={
            "stripe_secret_key": "sk_test_" + "x" * 24,
            "stripe_webhook_secret": "whsec_" + "y" * 32,
        }
    )


@pytest.fixture
def stripe_calls(monkeypatch):
    """Script Stripe with `httpx.MockTransport`, through the module seam. The
    offline suite constructs no live client and cannot reach the network."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/customers":
            return httpx.Response(200, json={"id": "cus_1"})
        if request.url.path == "/v1/checkout/sessions":
            return httpx.Response(200, json={"id": "cs_1", "url": "https://checkout.stripe/x"})
        if request.url.path == "/v1/billing_portal/sessions":
            return httpx.Response(200, json={"id": "bps_1", "url": "https://portal.stripe/y"})
        if request.url.path == "/v1/subscriptions":
            # The checkout guard's authoritative read. Empty by default: these
            # tests are about somebody who has not subscribed yet.
            return httpx.Response(200, json={"data": [], "has_more": False})
        return httpx.Response(404, json={"error": {"message": "unexpected"}})

    monkeypatch.setattr(stripe, "STRIPE_TRANSPORT", httpx.MockTransport(handler))
    return seen


@pytest.fixture
async def paying_client(paying_settings, seat, hub_conn):
    from hub.main import build_app

    app = build_app(paying_settings)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://hub.test") as client:
        token, _ = security.mint_session(paying_settings, email="a@example.com", now=time.time())
        client.cookies.set(security.COOKIE_NAME, token, domain="hub.test", path="/")
        yield client


async def test_checkout_sends_the_browser_to_stripe(paying_client, stripe_calls) -> None:
    response = await paying_client.get("/billing/checkout?plan=annual")
    assert response.status_code == 303
    assert response.headers["location"] == "https://checkout.stripe/x"


async def test_checkout_asks_for_a_card_and_a_trial(paying_client, stripe_calls) -> None:
    """`payment_method_collection=always` is load-bearing: Stripe's
    `if_required` SKIPS card collection on a trialing subscription, silently
    handing back the no-card trial ADR 0212 exists to avoid."""
    await paying_client.get("/billing/checkout?plan=annual")
    body = next(r for r in stripe_calls if r.url.path == "/v1/checkout/sessions").content.decode()
    assert "payment_method_collection=always" in body
    assert "subscription_data%5Btrial_period_days%5D=3" in body
    assert "automatic_tax%5Benabled%5D=true" in body


async def test_checkout_stamps_metadata_on_the_subscription_too(
    paying_client, stripe_calls
) -> None:
    """Session metadata carries `checkout.session.completed` only. Every
    `customer.subscription.*` event afterwards carries the SUBSCRIPTION, and
    that is where entitlement comes from -- stamping only the session leaves
    every later event unattributable."""
    await paying_client.get("/billing/checkout?plan=annual")
    body = next(r for r in stripe_calls if r.url.path == "/v1/checkout/sessions").content.decode()
    assert "subscription_data%5Bmetadata%5D%5Buser_id%5D=" in body
    assert "subscription_data%5Bmetadata%5D%5Binstance_id%5D=" in body


async def test_a_second_checkout_is_refused_to_the_portal(
    paying_client, stripe_calls, hub_conn, seat
) -> None:
    """Two tabs, or a return to the pricing page while subscribed, would
    otherwise create a second subscription and bill one person twice."""
    billing_apply.apply_event(hub_conn, sub_event(seat, status="active"))
    response = await paying_client.get("/billing/checkout?plan=annual")
    assert response.status_code == 303
    assert response.headers["location"].endswith("/billing/portal")
    assert not [r for r in stripe_calls if r.url.path == "/v1/checkout/sessions"]


async def test_an_unknown_plan_is_a_404_not_a_default(paying_client, stripe_calls) -> None:
    assert (await paying_client.get("/billing/checkout?plan=free")).status_code == 404


async def test_the_portal_deep_links_to_the_card_form(
    paying_client, stripe_calls, hub_conn
) -> None:
    """What makes in-app dunning work: 'update your card' has to land ON the
    card form. Most of the people just persuaded do not survive an extra
    navigation."""
    hub_conn.execute("UPDATE users SET stripe_customer_id = 'cus_1'")
    response = await paying_client.get("/billing/portal?flow=payment_method_update")
    assert response.status_code == 303
    body = next(
        r for r in stripe_calls if r.url.path == "/v1/billing_portal/sessions"
    ).content.decode()
    assert "flow_data%5Btype%5D=payment_method_update" in body


async def test_the_portal_pins_its_configuration_when_one_is_set(
    paying_settings, seat, hub_conn, stripe_calls, monkeypatch
) -> None:
    """On a SHARED Stripe account the default portal configuration belongs to
    whoever created it, and carries their cancel policy, their plan list and
    their branding -- mutable by them, so Venona's cancel link can start
    answering 400 because somebody else turned a feature off."""
    import httpx
    from hub.main import build_app

    settings = paying_settings.model_copy(update={"stripe_portal_configuration": "bpc_venona"})
    app = build_app(settings)
    hub_conn.execute("UPDATE users SET stripe_customer_id = 'cus_1'")
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://hub.test") as client:
        token, _ = security.mint_session(settings, email="a@example.com", now=time.time())
        client.cookies.set(security.COOKIE_NAME, token, domain="hub.test", path="/")
        await client.get("/billing/portal")
    body = next(
        r for r in stripe_calls if r.url.path == "/v1/billing_portal/sessions"
    ).content.decode()
    assert "configuration=bpc_venona" in body


async def test_the_portal_sends_no_configuration_when_none_is_set(
    paying_client, stripe_calls, hub_conn
) -> None:
    """A dedicated account can rely on its own default, and sending an empty
    string would be a 400 rather than a fallback."""
    hub_conn.execute("UPDATE users SET stripe_customer_id = 'cus_1'")
    await paying_client.get("/billing/portal")
    body = next(
        r for r in stripe_calls if r.url.path == "/v1/billing_portal/sessions"
    ).content.decode()
    assert "configuration" not in body


async def test_the_portal_refuses_before_there_is_anything_to_manage(paying_client) -> None:
    assert (await paying_client.get("/billing/portal")).status_code == 409


async def test_signing_out_refuses_checkout(paying_client, stripe_calls) -> None:
    paying_client.cookies.clear()
    assert (await paying_client.get("/billing/checkout")).status_code == 401


async def test_an_unconfigured_hub_says_so_rather_than_crashing(hub_client) -> None:
    """A hub with no Stripe account is a valid hub -- every local one, and the
    production one before launch. 503, not 500."""
    assert (await hub_client.get("/billing/checkout")).status_code == 503


async def test_the_return_url_cannot_be_an_open_redirect(paying_client) -> None:
    """`to` arrives in a query string, so echoing it into a Location header
    unchecked would hand anybody a redirect off our own domain."""
    response = await paying_client.get("/billing/return?to=https://evil.example/steal")
    assert "evil.example" not in response.headers.get("location", "")


async def test_the_return_url_is_not_proof_of_payment(paying_client, hub_conn) -> None:
    """A reader can close the tab before it loads, and anybody can visit it.
    Fulfilment is the webhook's job and nothing here may write."""
    await paying_client.get("/billing/return?to=https://defense.example")
    assert hub_conn.execute("SELECT count(*) AS n FROM subscriptions").fetchone()["n"] == 0


async def test_a_forged_webhook_is_refused(paying_client, hub_conn) -> None:
    response = await paying_client.post(
        "/billing/webhook",
        content=b'{"id":"evt_forged","type":"customer.subscription.updated"}',
        headers={"stripe-signature": f"t={int(time.time())},v1={'0' * 64}"},
    )
    assert response.status_code == 400
    assert hub_conn.execute("SELECT count(*) AS n FROM stripe_events").fetchone()["n"] == 0


async def test_a_replayed_webhook_is_accepted_and_applied_once(
    paying_client, paying_settings, hub_conn, seat
) -> None:
    """Stripe retries until it gets a 2xx, so a redelivery must answer 200 --
    an error would make it retry forever -- while changing nothing."""
    import json

    from test_stripe_webhook import sign

    payload = json.dumps(sub_event(seat)).encode()
    header = sign(payload, paying_settings.stripe_webhook_secret, int(time.time()))
    for _ in range(2):
        response = await paying_client.post(
            "/billing/webhook", content=payload, headers={"stripe-signature": header}
        )
        assert response.status_code == 200
    assert hub_conn.execute("SELECT count(*) AS n FROM subscriptions").fetchone()["n"] == 1


async def test_no_response_anywhere_carries_the_stripe_secret(
    paying_client, paying_settings, stripe_calls, hub_conn
) -> None:
    """The shape `tests/test_notifications_api.py` uses. A credential that
    reaches a browser is a credential that is gone."""
    hub_conn.execute("UPDATE users SET stripe_customer_id = 'cus_1'")
    secret = paying_settings.stripe_secret_key
    webhook_secret = paying_settings.stripe_webhook_secret
    for path in ("/billing/checkout", "/billing/portal", "/billing/return", "/billing/nope"):
        response = await paying_client.get(path)
        blob = response.text + str(dict(response.headers))
        assert secret not in blob
        assert webhook_secret not in blob


# ── reconciliation ───────────────────────────────────────────────────
#
# Stripe retries a failed webhook for about three days and then stops, so an
# outage longer than that loses events for good. These assert the safety net is
# WIRED, not merely written -- the first version of this shipped as a function
# with a docstring naming a caller that did not exist.


@pytest.fixture
def stripe_subscriptions(monkeypatch, seat):
    """Stripe holding one active subscription this hub has never heard of."""
    from hub import poller

    monkeypatch.setattr(poller, "_last_reconcile", 0.0)
    payload = {
        "data": [
            {
                "id": SUB,
                "status": "active",
                "current_period_end": NOW + 30 * 86400,
                "cancel_at_period_end": False,
                "metadata": {"user_id": seat["user_id"], "instance_id": seat["instance_id"]},
                "items": {"data": [{"price": {"id": "price_annual"}}]},
            }
        ],
        "has_more": False,
    }
    monkeypatch.setattr(
        stripe, "STRIPE_TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    )


async def test_the_poll_cycle_reconciles_against_stripe(
    paying_settings, hub_conn, stripe_subscriptions
) -> None:
    """A subscription Stripe holds and no webhook ever delivered."""
    from hub import poller

    assert subs_repo.by_stripe_id(hub_conn, SUB) is None
    await poller.reconcile_subscriptions(paying_settings, hub_conn)
    row = subs_repo.by_stripe_id(hub_conn, SUB)
    assert row is not None and row["status"] == "active"


async def test_reconciliation_is_reached_from_the_poll_cycle(
    paying_settings, hub_conn, stripe_subscriptions, monkeypatch
) -> None:
    """The wiring itself. `poll_once` is what runs every five minutes, and a
    sweep nothing calls is not a safety net."""
    from hub import poller

    called: list[bool] = []
    real = poller.reconcile_subscriptions

    async def spy(settings, conn):
        called.append(True)
        return await real(settings, conn)

    monkeypatch.setattr(poller, "reconcile_subscriptions", spy)
    await poller.poll_once(paying_settings)
    assert called, "poll_once never reconciles, so a lost event is lost for good"


async def test_reconciliation_is_bounded_by_its_interval(
    paying_settings, hub_conn, stripe_subscriptions
) -> None:
    """At a 300s poll an unbounded sweep is 288 full reads of Stripe a day, to
    catch something that needs a three-day outage first."""
    from hub import poller

    assert await poller.reconcile_subscriptions(paying_settings, hub_conn) == 1
    assert await poller.reconcile_subscriptions(paying_settings, hub_conn) == 0


async def test_a_hub_that_takes_no_money_never_calls_stripe(hub_settings, hub_conn, monkeypatch):
    """No credential, no call. Every local hub is in this state."""
    from hub import poller

    monkeypatch.setattr(poller, "_last_reconcile", 0.0)
    monkeypatch.setattr(
        stripe,
        "STRIPE_TRANSPORT",
        httpx.MockTransport(lambda r: pytest.fail("a hub with no billing called Stripe")),
    )
    assert await poller.reconcile_subscriptions(hub_settings, hub_conn) == 0


async def test_a_stripe_outage_does_not_end_the_poll_loop(
    paying_settings, hub_conn, monkeypatch
) -> None:
    """The loop is the only thing that ever marks an instance live again."""
    from hub import poller

    monkeypatch.setattr(poller, "_last_reconcile", 0.0)
    monkeypatch.setattr(
        stripe, "STRIPE_TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(503, json={}))
    )
    assert await poller.reconcile_subscriptions(paying_settings, hub_conn) == 0


def test_an_sca_notice_survives_the_incomplete_status(hub_conn, seat) -> None:
    """`incomplete` is the status a subscription sits in while the cardholder's
    BANK is asking them to confirm -- exactly when `action_required` is on
    screen. Leaving it out of the preserved set meant the next routine update
    erased the only message telling them what to do."""
    billing_apply.apply_event(hub_conn, sub_event(seat, status="incomplete", created=NOW))
    billing_apply.apply_event(
        hub_conn,
        {
            "id": "evt_sca",
            "type": "invoice.payment_action_required",
            "created": NOW + 1,
            "data": {"object": {"parent": {"subscription_details": {"subscription": SUB}}}},
        },
    )
    assert subs_repo.by_stripe_id(hub_conn, SUB)["notice"] == "action_required"

    billing_apply.apply_event(hub_conn, sub_event(seat, status="incomplete", created=NOW + 2))
    assert subs_repo.by_stripe_id(hub_conn, SUB)["notice"] == "action_required"


def test_going_active_does_clear_the_notice(hub_conn, seat) -> None:
    """The other half: the payment went through, so the message must go."""
    billing_apply.apply_event(hub_conn, sub_event(seat, status="incomplete", created=NOW))
    billing_apply.apply_event(
        hub_conn,
        {
            "id": "evt_sca2",
            "type": "invoice.payment_action_required",
            "created": NOW + 1,
            "data": {"object": {"parent": {"subscription_details": {"subscription": SUB}}}},
        },
    )
    billing_apply.apply_event(hub_conn, sub_event(seat, status="active", created=NOW + 2))
    assert subs_repo.by_stripe_id(hub_conn, SUB)["notice"] is None


def test_an_out_of_order_resubscribe_does_not_corrupt_the_seat(hub_conn, seat) -> None:
    """`ux_subscriptions_live` allows one live subscription per seat, and a
    resubscribe can deliver the new subscription's `created` BEFORE the old
    one's `deleted`. That is an ordering problem, not corruption: the write is
    refused, the transaction rolls back, and the retry succeeds once the
    cancellation lands. What must never happen is two live rows, or the old one
    silently replaced.
    """
    import psycopg

    billing_apply.apply_event(hub_conn, sub_event(seat, status="active", created=NOW))

    second = sub_event(seat, status="active", created=NOW + 10)
    second["data"]["object"]["id"] = "sub_live_2"
    with pytest.raises(psycopg.errors.UniqueViolation):
        billing_apply.apply_event(hub_conn, second)

    live = hub_conn.execute(
        "SELECT stripe_subscription_id FROM subscriptions WHERE status = 'active'"
    ).fetchall()
    assert [r["stripe_subscription_id"] for r in live] == [SUB], "the seat kept exactly one"

    # The cancellation lands, then the retry succeeds.
    billing_apply.apply_event(
        hub_conn, {**sub_event(seat, created=NOW + 20), "type": "customer.subscription.deleted"}
    )
    billing_apply.apply_event(hub_conn, second)
    assert (
        subs_repo.live_for(hub_conn, user_id=seat["user_id"], instance_id=seat["instance_id"])[
            "stripe_subscription_id"
        ]
        == "sub_live_2"
    )


# ── the period end ───────────────────────────────────────────────────
#
# Stripe MOVED `current_period_end` onto the subscription ITEM. Reading only the
# top level yields None forever -- and `entitled_through` is what a lapsed
# reader's archive ceiling comes from, so a null one collapses to
# `UserRecord.NOTHING` and takes away every brief they paid for. ADR 0211's
# promise, broken silently, with nothing anywhere reporting a problem.


def test_the_period_end_is_read_from_the_item(hub_conn, seat) -> None:
    """The shape Stripe actually sends now: null at the top, real on the item."""
    event = sub_event(seat, status="active")
    event["data"]["object"]["current_period_end"] = None
    event["data"]["object"]["items"] = {
        "data": [{"price": {"id": "price_annual"}, "current_period_end": NOW + 365 * 86400}]
    }
    billing_apply.apply_event(hub_conn, event)
    row = subs_repo.by_stripe_id(hub_conn, SUB)
    assert row["current_period_end"] is not None, (
        "entitled_through would be null, and a lapsed reader would lose the whole archive"
    )


def test_the_top_level_period_end_still_wins_when_present(hub_conn, seat) -> None:
    billing_apply.apply_event(hub_conn, sub_event(seat, status="active"))
    assert subs_repo.by_stripe_id(hub_conn, SUB)["current_period_end"] is not None


def test_several_items_take_the_furthest_period_end(hub_conn, seat) -> None:
    """Access should end when the last thing they paid for does."""
    event = sub_event(seat, status="active")
    event["data"]["object"]["current_period_end"] = None
    event["data"]["object"]["items"] = {
        "data": [
            {"price": {"id": "a"}, "current_period_end": NOW + 86400},
            {"price": {"id": "b"}, "current_period_end": NOW + 30 * 86400},
        ]
    }
    billing_apply.apply_event(hub_conn, event)
    row = subs_repo.by_stripe_id(hub_conn, SUB)
    assert row["current_period_end"].timestamp() == pytest.approx(NOW + 30 * 86400, abs=2)


def test_a_lapsed_reader_keeps_a_ceiling_from_the_item_shape(hub_conn, seat) -> None:
    """The end of the chain, and the thing that actually matters: the date has
    to reach `billing_state`, which is what the instance is told."""
    from hub import billing_state

    event = sub_event(seat, status="canceled")
    event["type"] = "customer.subscription.deleted"
    event["data"]["object"]["current_period_end"] = None
    event["data"]["object"]["items"] = {
        "data": [{"price": {"id": "p"}, "current_period_end": NOW + 86400}]
    }
    billing_apply.apply_event(hub_conn, event)
    state = billing_state.for_seat(
        hub_conn, user_id=seat["user_id"], instance_id=seat["instance_id"]
    )
    assert state["status"] == "lapsed"
    assert state["through"] is not None, "a lapsed reader would be told they never subscribed"


async def test_the_seat_is_resolved_by_client_id_not_slug(
    paying_client, stripe_calls, hub_conn, seat
) -> None:
    """The hub's `instances.slug` and an instance's own domain slug are separate
    values in separate databases that nothing keeps in step. They matched in
    production by coincidence and disagreed on the first machine that had both,
    where the manage link 404'd. The client id is the one identifier the two
    sides are guaranteed to share, because this hub minted it."""
    hub_conn.execute("UPDATE users SET stripe_customer_id = 'cus_1'")
    client_id = hub_conn.execute(
        "SELECT oidc_client_id FROM instances WHERE id = %s", (seat["instance_id"],)
    ).fetchone()["oidc_client_id"]

    ok = await paying_client.get(f"/billing/portal?client={client_id}")
    assert ok.status_code == 303

    # And a slug that does not exist here is still refused, rather than guessed.
    assert (await paying_client.get("/billing/portal?instance=not-a-slug")).status_code == 404
    assert (await paying_client.get("/billing/portal?client=hubc:nobody")).status_code == 404


# ── a Stripe refusal is an answer, not a reference code ──────────────


@pytest.fixture
def stripe_refuses(monkeypatch):
    """Stripe saying no, for any reason."""
    monkeypatch.setattr(
        stripe,
        "STRIPE_TRANSPORT",
        httpx.MockTransport(
            lambda r: httpx.Response(400, json={"error": {"message": "no", "type": "invalid"}})
        ),
    )


async def test_a_stripe_refusal_is_not_an_unhandled_500(
    paying_client, hub_conn, stripe_refuses
) -> None:
    """Every route here ends in a button somebody pressed. An unhandled
    `StripeError` becomes "Something went wrong. Reference: 5b6945e4", which
    tells the reader nothing and looks like the product is broken at the exact
    moment money is involved."""
    hub_conn.execute("UPDATE users SET stripe_customer_id = 'cus_1'")
    response = await paying_client.get("/billing/portal")
    assert response.status_code == 502, response.text
    assert "reference" not in response.text.lower()


async def test_changing_a_plan_that_does_not_exist_is_refused_before_stripe(
    paying_client, hub_conn, stripe_calls
) -> None:
    """`subscription_update` without a subscription is a 400 from Stripe --
    'The session's customer does not match the subscription's customer' -- and
    the reader who clicked Change plan should never see that."""
    hub_conn.execute("UPDATE users SET stripe_customer_id = 'cus_1'")
    response = await paying_client.get("/billing/portal?flow=subscription_update")
    assert response.status_code == 409
    assert not [r for r in stripe_calls if r.url.path == "/v1/billing_portal/sessions"], (
        "it should refuse without calling Stripe at all"
    )


async def test_a_subscription_on_another_customer_is_refused(
    paying_client, hub_conn, seat, monkeypatch
) -> None:
    """The stored customer and the subscription's can diverge --
    `set_stripe_customer` fills a NULL and never overwrites, so it cannot fix
    itself. Stripe answers 400; this catches it first and says it needs us."""
    billing_apply.apply_event(hub_conn, sub_event(seat, status="active"))
    hub_conn.execute("UPDATE users SET stripe_customer_id = 'cus_ours'")
    monkeypatch.setattr(
        stripe,
        "STRIPE_TRANSPORT",
        httpx.MockTransport(
            lambda r: httpx.Response(200, json={"id": SUB, "customer": "cus_somebody_else"})
        ),
    )
    response = await paying_client.get("/billing/portal?flow=subscription_cancel")
    assert response.status_code == 409


# ── two instances, one person ────────────────────────────────────────


async def test_checkout_returns_to_the_instance_being_bought(
    paying_settings, hub_conn, seat, stripe_calls
) -> None:
    """A subscription buys a seat on ONE instance, and the reader has to land
    back on THAT one. A single configured return URL could only ever name one
    of them, so it sent everybody to whichever was configured however many they
    subscribed to."""
    import httpx
    from hub.main import build_app

    hub_conn.execute(
        "UPDATE instances SET public_url = 'https://aitech.example' WHERE id = %s",
        (seat["instance_id"],),
    )
    app = build_app(paying_settings)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://hub.test") as client:
        token, _ = security.mint_session(paying_settings, email="a@example.com", now=time.time())
        client.cookies.set(security.COOKIE_NAME, token, domain="hub.test", path="/")
        await client.get("/billing/checkout?plan=annual")
    body = next(r for r in stripe_calls if r.url.path == "/v1/checkout/sessions").content.decode()
    assert "aitech.example" in body, body[:400]


async def test_the_return_hop_only_trusts_a_known_instance(paying_client, seat) -> None:
    """An identity provider is the worst place in the system to hold an open
    redirect: it is the origin every instance trusts."""
    response = await paying_client.get("/billing/return?to=https://evil.example/steal")
    assert "evil.example" not in response.headers.get("location", "")


async def test_the_return_hop_accepts_a_real_instance(paying_client, hub_conn, seat) -> None:
    hub_conn.execute(
        "UPDATE instances SET public_url = 'https://aitech.example', status = 'live' WHERE id = %s",
        (seat["instance_id"],),
    )
    response = await paying_client.get("/billing/return?to=https://aitech.example")
    assert response.headers["location"].rstrip("/") == "https://aitech.example"


# ── one product per instance ─────────────────────────────────────────
#
# Stripe's portal lists a customer's subscriptions by PRODUCT NAME, so two
# domains sharing a product are indistinguishable to a reader deciding which to
# cancel. A hub-wide pair of prices can only ever name one domain.


async def test_an_unpriced_instance_refuses_rather_than_selling_another(
    paying_client, hub_conn, seat, stripe_calls
) -> None:
    """A fallback here would sell somebody another domain's product."""
    hub_conn.execute(
        "UPDATE instances SET stripe_price_monthly = NULL, stripe_price_annual = NULL"
        " WHERE id = %s",
        (seat["instance_id"],),
    )
    response = await paying_client.get("/billing/checkout?plan=annual")
    assert response.status_code == 409
    assert not [r for r in stripe_calls if r.url.path == "/v1/checkout/sessions"]


async def test_checkout_buys_the_seat_s_own_price(paying_client, seat, stripe_calls) -> None:
    await paying_client.get("/billing/checkout?plan=annual")
    body = next(r for r in stripe_calls if r.url.path == "/v1/checkout/sessions").content.decode()
    assert "price_defense_annual" in body, body[:300]


async def test_the_subscription_carries_the_domain_to_stripe(
    paying_client, seat, stripe_calls
) -> None:
    """`description` lands on the INVOICE, which is the document a business
    buyer expenses; `metadata[slug]` lands in the dashboard, where an operator
    should not have to resolve `inst:06a8...` against another system."""
    await paying_client.get("/billing/checkout?plan=annual")
    body = next(r for r in stripe_calls if r.url.path == "/v1/checkout/sessions").content.decode()
    assert "subscription_data%5Bdescription%5D=" in body
    assert "metadata%5Bslug%5D=defense" in body


def test_the_row_remembers_what_was_bought(hub_conn, seat) -> None:
    """The amount, so a surface can say "your next charge is $12 on 30
    September" -- it could only ever render the date, because the amount lived
    in a Stripe Price nothing on this side had read."""
    event = sub_event(seat, status="active")
    event["data"]["object"]["customer"] = "cus_theirs"
    event["data"]["object"]["items"] = {
        "data": [
            {
                "price": {
                    "id": "price_x",
                    "unit_amount": 1200,
                    "currency": "usd",
                    "recurring": {"interval": "month"},
                },
                "current_period_end": NOW + 30 * 86400,
            }
        ]
    }
    billing_apply.apply_event(hub_conn, event)
    row = subs_repo.by_stripe_id(hub_conn, SUB)
    assert (row["unit_amount"], row["currency"], row["interval"]) == (1200, "usd", "month")
    assert row["stripe_customer_id"] == "cus_theirs"


def test_a_cancellation_records_when_it_happened(hub_conn, seat) -> None:
    """Scheduled for the period end and already ended are different facts, and
    the operator page has to tell them apart."""
    event = sub_event(seat, status="canceled")
    event["type"] = "customer.subscription.deleted"
    event["data"]["object"]["canceled_at"] = NOW
    billing_apply.apply_event(hub_conn, event)
    assert subs_repo.by_stripe_id(hub_conn, SUB)["canceled_at"] is not None


# ── the seat is created by the payment ───────────────────────────────


def _checkout_completed(seat, **over):
    return {
        "id": "evt_checkout",
        "type": "checkout.session.completed",
        "created": NOW,
        "data": {
            "object": {
                "id": "cs_1",
                "customer": "cus_1",
                "metadata": {
                    "user_id": seat["user_id"],
                    "instance_id": seat["instance_id"],
                    **over,
                },
            }
        },
    }


async def test_paying_is_what_creates_the_seat(hub_conn, seat) -> None:
    """Self-serve sends somebody to Checkout WITHOUT a membership, so this is
    the event that grants one. Before, the button granted and every abandoned
    Checkout left an account behind that had never paid."""
    from hub.repositories import users as users_repo

    users_repo.revoke(hub_conn, user_id=seat["user_id"], instance_id=seat["instance_id"])
    assert users_repo.seats_for(hub_conn, seat["user_id"]) == []

    billing_apply.apply_event(hub_conn, _checkout_completed(seat))
    rows = users_repo.seats_for(hub_conn, seat["user_id"])
    assert [row["id"] for row in rows] == [seat["instance_id"]]
    assert rows[0]["role"] == "reader", "a seat somebody bought is a reader's seat"


async def test_the_grant_does_not_promote_anybody(hub_conn, seat) -> None:
    """A replay, or a second checkout, must not touch a role a person set.
    `grant` upserts, so an unguarded call would quietly demote an admin who
    bought a subscription on their own instance."""
    from hub.repositories import users as users_repo

    users_repo.grant(
        hub_conn, user_id=seat["user_id"], instance_id=seat["instance_id"], role="admin"
    )
    billing_apply.apply_event(hub_conn, _checkout_completed(seat))
    rows = users_repo.seats_for(hub_conn, seat["user_id"])
    assert rows[0]["role"] == "admin"


async def test_each_plan_gets_its_own_idempotency_key(paying_client, stripe_calls) -> None:
    """⭐ The plan is part of the key, because monthly and annual are different
    REQUESTS rather than one intent asked twice.

    Without this, whichever button somebody pressed first pinned the key to that
    price, and pressing the other one answered Stripe's `Keys for idempotent
    requests can only be used with the same parameters they were first used
    with` -- a 400, an error page reading "our payment provider could not
    complete that", and no way to change your mind for the 24 hours the key
    lives. Live on 2026-09-11, on the first day the gallery offered both.
    """
    await paying_client.get("/billing/checkout?plan=annual")
    await paying_client.get("/billing/checkout?plan=monthly")
    keys = [
        r.headers["idempotency-key"] for r in stripe_calls if r.url.path == "/v1/checkout/sessions"
    ]
    assert len(keys) == 2
    assert keys[0] != keys[1], (
        "annual and monthly reused one key, so Stripe refuses the second with a 400 "
        "and the reader cannot change plan for 24 hours"
    )
    # The plan is in the key; what follows it is the payload digest that keeps
    # the key from outliving the request it described.
    assert ":annual:" in keys[0] and ":monthly:" in keys[1]
    # Still one key per plan, so a double CLICK is still idempotent.
    await paying_client.get("/billing/checkout?plan=annual")
    again = [
        r.headers["idempotency-key"] for r in stripe_calls if r.url.path == "/v1/checkout/sessions"
    ]
    assert again[2] == keys[0]


async def test_cancelling_checkout_returns_to_the_picker(paying_client, stripe_calls) -> None:
    """⭐ Checkout's own back arrow lands on `cancel_url`, and the domain being
    bought is the one place a cancelling buyer may not be able to go: somebody
    buying their FIRST seat holds no membership there, so the instance answers
    with its sign-in page. That reads as the purchase having signed them out, on
    the screen where that alarms most (2026-09-11). The hub's front door is the
    one address that works for every buyer.
    """
    await paying_client.get("/billing/checkout?plan=annual")
    body = next(r for r in stripe_calls if r.url.path == "/v1/checkout/sessions").content.decode()
    assert "cancel_url=https%3A%2F%2Fhub.test" in body, body
    assert "cancel_url=https%3A%2F%2Fdefense.example" not in body
    # The success path still hands off through the hub, which grants the seat
    # before forwarding to the domain they paid for.
    assert "success_url=https%3A%2F%2Fhub.test%2Fbilling%2Freturn" in body


def test_an_idempotency_key_is_scoped_to_the_request_it_described() -> None:
    """⭐ Stripe compares PARAMETERS, not intent: reusing a key with anything
    different is a 400 for the twenty-four hours that key lives.

    A key naming only who and what therefore breaks for every reader who clicked
    in the last day the moment any field of that request changes -- and a deploy
    is such a moment. Changing `cancel_url` took the whole paywall down that way
    on 2026-09-11, hours after adding the plan to the key had fixed the same
    class of failure between monthly and annual.
    """
    from hub.stripe import _scoped_key

    base = {"mode": "subscription", "cancel_url": "https://defense.example"}
    moved = {"mode": "subscription", "cancel_url": "https://hub.example"}
    assert _scoped_key("checkout:u:i:annual", base) != _scoped_key("checkout:u:i:annual", moved)
    # A double click sends identical parameters, so it still collapses to one
    # session -- which is the whole reason the key is there.
    assert _scoped_key("checkout:u:i:annual", base) == _scoped_key(
        "checkout:u:i:annual", dict(base)
    )
    # Key order is not a parameter change.
    assert _scoped_key("k", {"a": "1", "b": "2"}) == _scoped_key("k", {"b": "2", "a": "1"})
    # A nested field counts, because Stripe sees it flattened on the wire.
    assert _scoped_key("k", {"subscription_data": {"trial_period_days": 3}}) != _scoped_key(
        "k", {"subscription_data": {"trial_period_days": 7}}
    )
    assert _scoped_key("k", None) == "k"
