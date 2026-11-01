"""The hub telling its operator something, from the call site outward.

THE GAP THIS CLOSES. A Slack webhook was configured on the production instance,
its test message arrived, and no notice for an access request ever did -- because
the request had gone to the HUB, which had no way to tell anybody anything. The
instance's own `access.requested` was wired to its own request form, the door
nobody uses now that sign-in goes through here.

So these tests are about whether a notice is RAISED, and by the real handler.
A green test send proves the channel and nothing upstream of it, which is the
mistake that made the original fault undiagnosable; the last test here pins that
asymmetry rather than letting the next person rediscover it.
"""

from __future__ import annotations

import json
import time

import httpx
import pytest
from hub.repositories import instances as instances_repo
from hub.repositories import notifications as repo

from hub import ids, notices, notify, security

OPERATOR = "op@example.com"  # conftest's hub_settings names this one

WEBHOOK = "https://hooks.slack.com/services/T0/B0/abcd"


def _sign_in(hub_client, hub_settings, email: str = OPERATOR) -> None:
    token, _ = security.mint_session(hub_settings, email=email, now=time.time())
    hub_client.cookies.set(security.COOKIE_NAME, token, domain="hub.test", path="/")


def _destination(conn, *subscribe: str, target: str = WEBHOOK, label: str = "Ops") -> str:
    destination_id = ids.destination_id()
    repo.insert(
        conn,
        destination_id=destination_id,
        kind="webhook",
        label=label,
        target=target,
        created_by=OPERATOR,
    )
    for notice_type in subscribe or tuple(notices.NOTICE_TYPES):
        repo.subscribe(conn, destination_id=destination_id, notice_type=notice_type)
    return destination_id


def _queued(conn) -> list[str]:
    return [
        row["notice_type"]
        for row in conn.execute(
            "SELECT notice_type FROM notify_outbox ORDER BY created_at"
        ).fetchall()
    ]


def _payloads(conn) -> list[dict]:
    return [
        json.loads(row["payload"])
        for row in conn.execute("SELECT payload FROM notify_outbox ORDER BY created_at").fetchall()
    ]


def _seed_instance(conn, slug="acme", name="Acme Watch") -> str:
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
    return instance_id


# ── the door that actually gets used ─────────────────────────────────


async def test_the_request_access_form_tells_the_operator(hub_client, hub_conn) -> None:
    """The fault this whole change exists for.

    A separate front site and the sign-in page both post here. Before this the row
    was written and nobody was told, so a request sat `pending` until somebody
    thought to open the console.
    """
    _destination(hub_conn)

    response = await hub_client.post(
        "/request-access", data={"email": "stranger@example.com", "note": "I cover naval programs"}
    )

    assert response.status_code == 200
    assert _queued(hub_conn) == ["access.requested"]
    body = _payloads(hub_conn)[0]
    assert "stranger@example.com" in " ".join(body["body"])
    assert body["quoted"] == "I cover naval programs"


async def test_a_launch_signup_is_its_own_notice(hub_client, hub_conn) -> None:
    """Graded differently because there is nothing to grant, so an operator can
    mute this one and keep the requests that need a decision."""
    _destination(hub_conn)

    await hub_client.post(
        "/request-access", data={"email": "someone@example.com", "kind": "launch"}
    )

    assert _queued(hub_conn) == ["launch.signup"]


async def test_pressing_the_button_twice_tells_the_operator_once(hub_client, hub_conn) -> None:
    """Otherwise a stranger can ring an operator's phone as often as they like.

    The reply stays byte-identical either way (ADR 0141), so nothing about this
    is observable from outside.
    """
    _destination(hub_conn)
    form = {"email": "stranger@example.com", "note": "hello"}

    first = await hub_client.post("/request-access", data=form)
    second = await hub_client.post("/request-access", data=form)

    assert first.status_code == second.status_code
    assert first.content == second.content, "ADR 0141: every outcome answers identically"
    assert _queued(hub_conn) == ["access.requested"]


async def test_a_stranger_cannot_reach_slacks_markup(hub_client, hub_conn) -> None:
    """`note` is two thousand characters of somebody else's text.

    It travels in `quoted` and never inside a sentence, which is what lets the
    channel escape it. A plain-text POST neutralises it by accident; a Slack
    block would not.
    """
    _destination(hub_conn)
    await hub_client.post(
        "/request-access",
        data={"email": "x@example.com", "note": '"}],"text":"pwned","blocks":[{"x":"'},
    )

    payload = _payloads(hub_conn)[0]
    from notices.render import as_slack
    from notices.types import Notice

    rendered = as_slack(
        Notice(
            title=payload["title"],
            body=tuple(payload["body"]),
            quoted=payload["quoted"],
        ),
        base="https://hub.test",
    )
    assert json.loads(rendered)["blocks"], "the card must still parse as one message"
    assert "pwned" not in json.dumps(json.loads(rendered)["blocks"][0])


# ── a domain that stops answering ────────────────────────────────────


async def test_a_domain_going_quiet_says_so_once(hub_conn) -> None:
    """Three misses demote it, and the CROSSING is what is reported.

    Keyed on the crossing rather than the state because the poller runs every
    five minutes: a domain that is down for a day would otherwise say so 288
    times, which is the same damage as saying nothing.
    """
    _destination(hub_conn)
    instance_id = _seed_instance(hub_conn)
    instances_repo.record_poll_success(hub_conn, instance_id, {"ok": True})

    seen: list[str] = []
    for _ in range(5):
        after = instances_repo.record_poll_miss(hub_conn, instance_id)
        if after["status"] == "offline" and after["poll_misses"] == instances_repo.MISS_LIMIT:
            notify.raise_notice(
                hub_conn,
                "instance.unreachable",
                instance_id=instance_id,
                domain="Acme Watch",
                since="fixed",
            )
        seen.append(after["status"])

    assert seen == ["live", "live", "offline", "offline", "offline"]
    assert _queued(hub_conn) == ["instance.unreachable"]


async def test_coming_back_is_reported_and_a_healthy_cycle_is_not(hub_conn) -> None:
    """The all clear, so that a quiet channel means it is still down.

    `record_poll_success` returns the status the row HELD, which is the only way
    to tell a recovery from the ordinary case. A version that reported every
    success would say "it is back" every five minutes forever.
    """
    _destination(hub_conn)
    instance_id = _seed_instance(hub_conn)
    instances_repo.record_poll_success(hub_conn, instance_id, {"ok": True})

    assert instances_repo.record_poll_success(hub_conn, instance_id, {"ok": True}) == "live"

    for _ in range(instances_repo.MISS_LIMIT):
        instances_repo.record_poll_miss(hub_conn, instance_id)
    assert instances_repo.record_poll_success(hub_conn, instance_id, {"ok": True}) == "offline"


# ── a subscription that needs a person ───────────────────────────────


def test_only_the_statuses_worth_waking_somebody_for(hub_conn) -> None:
    """A subscription going active is the system working.

    Paging on it teaches whoever reads these to stop reading them, which costs
    the ones that matter.
    """
    assert "active" not in notices.BILLING_ALARMS
    assert "trialing" not in notices.BILLING_ALARMS
    assert {"past_due", "canceled", "unpaid"} <= set(notices.BILLING_ALARMS)


# ── the console ──────────────────────────────────────────────────────


async def test_an_address_is_checked_before_it_is_stored(hub_client, hub_settings) -> None:
    """The save-time half of the SSRF guard, arriving as a sentence the operator
    can act on.

    The alternative is a destination that saves cleanly and silently never
    delivers, which is indistinguishable from nothing having happened. 422
    rather than a rendered page now: the surface keeps the form on screen and
    puts the sentence beside it, so the refusal has to survive as DATA.
    """
    _sign_in(hub_client, hub_settings)

    response = await hub_client.post(
        "/admin/data/notifications/add",
        json={"label": "Internal", "url": "https://127.0.0.1/hook"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["message"], "the refusal has to say why"


async def test_http_is_refused(hub_client, hub_settings) -> None:
    """A notice carries a stranger's words and sometimes an address."""
    _sign_in(hub_client, hub_settings)
    response = await hub_client.post(
        "/admin/data/notifications/add", json={"label": "Plain", "url": "http://example.com/hook"}
    )
    assert response.status_code == 422
    assert "https" in response.json()["detail"]["message"]


async def test_a_new_destination_starts_subscribed_to_everything(
    hub_client, hub_settings, hub_conn
) -> None:
    """Five switches, not fifty. An operator who has just said where to reach
    them means "tell me when something needs me"."""
    _sign_in(hub_client, hub_settings)

    await hub_client.post("/admin/data/notifications/add", json={"label": "Ops", "url": WEBHOOK})

    rows = repo.destinations(hub_conn)
    assert len(rows) == 1
    assert repo.subscriptions(hub_conn, rows[0]["id"]) == set(notices.NOTICE_TYPES)


async def test_no_route_ever_carries_the_address(hub_client, hub_settings, hub_conn) -> None:
    """⭐ A webhook URL is a bearer token in URL clothing.

    Anyone holding a Slack incoming address can post to that channel, so the
    surface shows a hint derived from it and no route returns the value. The
    check is on the JSON rather than on rendered markup, which is stricter:
    markup could omit what the payload carried, and a bundle that received the
    address would have it in memory whatever it chose to draw.
    """
    _sign_in(hub_client, hub_settings)
    _destination(hub_conn)

    response = await hub_client.get("/admin/data/notifications")

    assert response.status_code == 200
    assert WEBHOOK not in response.text
    assert response.json()["destinations"][0]["hint"] == "hooks.slack.com/…abcd"


async def test_unticking_a_switch_sticks(hub_client, hub_settings, hub_conn) -> None:
    _sign_in(hub_client, hub_settings)
    destination_id = _destination(hub_conn)

    await hub_client.post(
        "/admin/data/notifications/switch",
        json={"destination_id": destination_id, "notice_type": "launch.signup", "on": False},
    )

    assert "launch.signup" not in repo.subscriptions(hub_conn, destination_id)
    assert notify.raise_notice(hub_conn, "launch.signup", request_id="r1", email="a@b.test") == 0


async def test_the_operator_gate_covers_every_new_route(hub_client) -> None:
    """⭐ Signed out, every one of these is refused by the SESSION gate and
    never by the bearer gate.

    The middleware keeps two exact sets and a prefix; a route missing from all
    three falls through to the bearer surface, which answers a bare 401 to a
    person who is simply not signed in yet. The page and the data are refused
    differently on purpose -- a browser gets sent to the front door, a `fetch`
    gets a status it can act on -- and `www-authenticate` is what tells a
    session refusal from a bearer one.
    """
    hub_client.cookies.clear()

    page = await hub_client.get("/admin/notifications", follow_redirects=False)
    assert page.status_code in (302, 303), page.status_code

    for method, path in (
        ("GET", "/admin/data/notifications"),
        ("POST", "/admin/data/notifications/add"),
        ("POST", "/admin/data/notifications/remove"),
        ("POST", "/admin/data/notifications/switch"),
        ("POST", "/admin/data/notifications/clear"),
        ("POST", "/admin/data/notifications/test"),
    ):
        response = await hub_client.request(method, path, follow_redirects=False)
        assert response.status_code == 401, f"{method} {path} answered {response.status_code}"
        assert "www-authenticate" not in response.headers, (
            f"{method} {path} was refused by the bearer gate, not the session gate"
        )


# ── why a green test send proves nothing ─────────────────────────────


async def test_a_struck_out_destination_is_silent_while_its_test_still_works(hub_conn) -> None:
    """The asymmetry that made the production fault undiagnosable.

    A test send goes straight down the channel, so it reads the address and
    never looks at `status`. Every real path filters on `status = 'active'`. An
    operator whose channel had struck itself out saw a green test and no
    messages with nothing connecting the two, which is why the console now says
    so in words beside the destination.
    """
    destination_id = _destination(hub_conn)
    for _ in range(repo.STRIKE_LIMIT):
        repo.record_failure(hub_conn, destination_id=destination_id, error="410 gone")

    assert repo.destination(hub_conn, destination_id)["status"] == "struck_out"
    assert repo.destination_target(hub_conn, destination_id) == WEBHOOK, (
        "the test send still finds an address, which is why it still reports success"
    )
    assert repo.subscribed(hub_conn, "access.requested") == []
    assert notify.raise_notice(hub_conn, "access.requested", request_id="r", email="a@b.test") == 0


async def test_the_surface_says_a_destination_has_stopped_sending(
    hub_client, hub_settings, hub_conn
) -> None:
    """The fix for the asymmetry above, pinned where it is decided.

    `struck_out` is the whole of it: the words beside it are the client's, but
    an operator can only be told a channel has stopped if this field says so.
    """
    _sign_in(hub_client, hub_settings)
    destination_id = _destination(hub_conn)
    for _ in range(repo.STRIKE_LIMIT):
        repo.record_failure(hub_conn, destination_id=destination_id, error="410 gone")

    response = await hub_client.get("/admin/data/notifications")

    assert response.json()["destinations"][0]["status"] == "struck_out"
    assert response.json()["destinations"][0]["last_error"] == "410 gone"


# ── delivery ─────────────────────────────────────────────────────────


async def test_a_delivered_row_records_it_and_clears_the_strikes(hub_conn, monkeypatch) -> None:
    destination_id = _destination(hub_conn)
    repo.record_failure(hub_conn, destination_id=destination_id, error="a blip")
    notify.raise_notice(hub_conn, "access.requested", request_id="r1", email="a@b.test", note="x")

    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200)

    monkeypatch.setattr(notify, "NOTIFY_TRANSPORT", httpx.MockTransport(handler))
    delivered = await notify.drain(hub_conn, base="https://hub.test")

    assert delivered == 1
    assert len(sent) == 1
    row = hub_conn.execute("SELECT status FROM notify_outbox").fetchone()
    assert row["status"] == "delivered"
    assert repo.destination(hub_conn, destination_id)["failures"] == 0


async def test_a_permanent_status_does_not_retry(hub_conn, monkeypatch) -> None:
    """A revoked webhook answers 404 forever. Retrying it for an hour is how a
    destination stays struck out with a misleading error."""
    _destination(hub_conn)
    notify.raise_notice(hub_conn, "access.requested", request_id="r1", email="a@b.test", note="x")

    monkeypatch.setattr(
        notify, "NOTIFY_TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(404))
    )
    assert await notify.drain(hub_conn, base="") == 0

    row = hub_conn.execute("SELECT status FROM notify_outbox").fetchone()
    assert row["status"] == "failed"


async def test_three_failures_stop_the_sending(hub_conn, monkeypatch) -> None:
    destination_id = _destination(hub_conn)
    monkeypatch.setattr(
        notify, "NOTIFY_TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(500))
    )
    for n in range(repo.STRIKE_LIMIT):
        notify.raise_notice(
            hub_conn, "access.requested", request_id=f"r{n}", email="a@b.test", note="x"
        )
        await notify.drain(hub_conn, base="")

    assert repo.destination(hub_conn, destination_id)["status"] == "struck_out"


# ── the two ends of the funnel ───────────────────────────────────────


def _google_transport(email: str, nonce: str) -> httpx.MockTransport:
    """Google's token endpoint, answering with an unsigned but well-shaped
    id_token. The hub reads it without verifying the signature (the provenance
    carve-out), so a test token needs valid segments rather than a valid key."""
    import base64

    from hub import google

    claims = {
        "iss": "accounts.google.com",
        "aud": "test-google-client",
        "sub": f"google-{email}",
        "email": email,
        "email_verified": True,
        "name": "A Reader",
        "nonce": nonce,
    }
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == google.GOOGLE_TOKEN_ENDPOINT
        return httpx.Response(200, json={"id_token": f"e30.{body}.sig"})

    return httpx.MockTransport(handler)


async def _sign_in_with_google(hub_client, hub_settings, monkeypatch, email: str) -> None:
    from hub import google

    cookie, state, nonce = security.mint_state(hub_settings, now=time.time(), next_path="/")
    hub_client.cookies.set(security.STATE_COOKIE, cookie, domain="hub.test", path="/")
    monkeypatch.setattr(google, "EXCHANGE_TRANSPORT", _google_transport(email, nonce))
    await hub_client.get(f"/auth/callback?code=c&state={state}")


async def test_a_first_sign_in_tells_the_operator(
    hub_client, hub_conn, hub_settings, monkeypatch
) -> None:
    """⭐ The only place the hub can see a new person arrive.

    Signing in creates no row: a session is a cookie carrying an address, and a
    stranger who never buys anything never gets one. So there is no table whose
    growth would reveal this, and without the notice the first anybody hears of
    a new reader is a payment.
    """
    _destination(hub_conn)

    await _sign_in_with_google(hub_client, hub_settings, monkeypatch, "new@example.com")

    assert _queued(hub_conn) == ["account.created"]
    assert "new@example.com" in " ".join(_payloads(hub_conn)[0]["body"])


async def test_the_same_person_signing_in_again_is_not_new(
    hub_client, hub_conn, hub_settings, monkeypatch
) -> None:
    """The dedupe key is the ADDRESS, because there is no row to key on and a
    rowless person's tenth visit looks exactly like their first."""
    _destination(hub_conn)

    await _sign_in_with_google(hub_client, hub_settings, monkeypatch, "new@example.com")
    await _sign_in_with_google(hub_client, hub_settings, monkeypatch, "new@example.com")

    assert _queued(hub_conn) == ["account.created"]


async def test_a_known_account_signing_in_says_nothing(
    hub_client, hub_conn, hub_settings, monkeypatch
) -> None:
    """Somebody the hub already holds is not a new arrival, and an operator
    getting one of these per login would stop reading them."""
    from hub.repositories import users as users_repo

    from hub import ids as hub_ids

    _destination(hub_conn)
    users_repo.create(
        hub_conn, user_id=hub_ids.user_id(), email="known@example.com", display_name=None
    )

    await _sign_in_with_google(hub_client, hub_settings, monkeypatch, "known@example.com")

    assert _queued(hub_conn) == []


def _seat(conn) -> dict:
    """A person with a membership on one instance, which is what a subscription
    is about. Deliberately its own copy rather than an import from
    `test_billing.py`: these tests are about the NOTICE, and the seat is a
    fixture for both."""
    from hub.repositories import instances as instances_repo
    from hub.repositories import users as users_repo

    instance_id = ids.instance_id()
    instances_repo.create(
        conn,
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
    users_repo.create(conn, user_id=user_id, email="reader@example.com", display_name=None)
    users_repo.grant(conn, user_id=user_id, instance_id=instance_id, role="reader")
    return {"user_id": user_id, "instance_id": instance_id}


def _sub_event(seat, *, status="active", created=1_700_000_000) -> dict:
    return {
        "id": f"evt_{created}_{status}",
        "type": "customer.subscription.updated",
        "created": created,
        "data": {
            "object": {
                "id": "sub_live_1",
                "status": status,
                "current_period_end": created + 30 * 86400,
                "cancel_at_period_end": False,
                "metadata": {"user_id": seat["user_id"], "instance_id": seat["instance_id"]},
                "items": {"data": [{"price": {"id": "price_monthly"}}]},
            }
        },
    }


async def test_a_new_subscription_tells_the_operator(hub_conn) -> None:
    """⭐ The good half of the billing news, and its own switch.

    `billing.changed` is "this needs a person" and deliberately says nothing
    when a subscription is working, because paging on success teaches whoever
    reads these to stop reading them. That reasoning is right and it left the
    thing the hub exists to do as the one event nobody was told about.
    """
    from hub import billing_apply

    _destination(hub_conn)
    seat = _seat(hub_conn)

    billing_apply.apply_event(hub_conn, _sub_event(seat, status="active"))

    assert _queued(hub_conn) == ["billing.subscribed"]
    body = " ".join(_payloads(hub_conn)[0]["body"])
    assert "reader@example.com" in body and "Defense" in body


async def test_a_trial_converting_is_the_same_subscriber(hub_conn) -> None:
    """One notice per subscription, not per status. A trial that becomes active
    is the same person still paying, and saying it twice makes the number of
    subscribers this notice implies wrong."""
    from hub import billing_apply

    _destination(hub_conn)
    seat = _seat(hub_conn)

    billing_apply.apply_event(hub_conn, _sub_event(seat, status="trialing"))
    billing_apply.apply_event(hub_conn, _sub_event(seat, status="active", created=1_700_000_600))

    assert _queued(hub_conn) == ["billing.subscribed"]


async def test_a_card_failing_is_not_a_new_subscriber(hub_conn) -> None:
    """`past_due` is ENTITLING -- the seat still reads while the card is retried
    -- so a rule keyed on entitlement alone would announce a failed payment as a
    new subscriber. It is `billing.changed`'s to report, and only that."""
    from hub import billing_apply

    _destination(hub_conn)
    seat = _seat(hub_conn)

    billing_apply.apply_event(hub_conn, _sub_event(seat, status="past_due"))

    assert _queued(hub_conn) == ["billing.changed"]


async def test_a_cancellation_is_still_the_attention_notice(hub_conn) -> None:
    """The two switches divide the statuses between them and neither claims
    one the other is reporting."""
    from hub import billing_apply

    _destination(hub_conn)
    seat = _seat(hub_conn)

    billing_apply.apply_event(hub_conn, _sub_event(seat, status="active"))
    billing_apply.apply_event(hub_conn, _sub_event(seat, status="canceled", created=1_700_000_600))

    assert _queued(hub_conn) == ["billing.subscribed", "billing.changed"]


# ── a switch nobody has ever been shown ──────────────────────────────


def test_a_new_notice_type_reaches_a_destination_that_predates_it(hub_conn) -> None:
    """⭐ The gap `notify_offered` closes, and the reason these two switches
    would otherwise have shipped to nobody.

    A subscription row means yes and its absence means no, so a type added after
    a destination was made is indistinguishable from one that destination turned
    off. Every existing destination would have been silently opted out of every
    notice added from now on.
    """
    destination_id = _destination(hub_conn, "access.requested")
    repo.record_offer(hub_conn, destination_id=destination_id, notice_type="access.requested")

    offered = notify.offer_new_types(hub_conn)

    assert offered == len(notices.NOTICE_TYPES) - 1
    assert "billing.subscribed" in repo.subscriptions(hub_conn, destination_id)
    assert "account.created" in repo.subscriptions(hub_conn, destination_id)


def test_a_deliberate_no_survives_the_offer(hub_conn) -> None:
    """It is the OFFER that is recorded, never the answer. Reconciling against
    the subscriptions instead would hand every unticked switch back at the next
    boot, which is worse than the gap it fixes: a notice somebody turned off is
    one they have a reason to be rid of."""
    destination_id = _destination(hub_conn)
    for declared in notices.all_types():
        repo.record_offer(hub_conn, destination_id=destination_id, notice_type=declared.key)
    repo.unsubscribe(hub_conn, destination_id=destination_id, notice_type="launch.signup")

    assert notify.offer_new_types(hub_conn) == 0
    assert "launch.signup" not in repo.subscriptions(hub_conn, destination_id)


def test_offering_twice_changes_nothing(hub_conn) -> None:
    """It runs in the lifespan and every deploy is a boot."""
    destination_id = _destination(hub_conn)

    first = notify.offer_new_types(hub_conn)
    second = notify.offer_new_types(hub_conn)

    assert first == len(notices.NOTICE_TYPES)
    assert second == 0
    assert repo.subscriptions(hub_conn, destination_id) == set(notices.NOTICE_TYPES)


# ── the words ────────────────────────────────────────────────────────


@pytest.mark.parametrize("declared", list(notices.NOTICE_TYPES.values()))
def test_every_notice_type_is_written_for_a_reader(declared) -> None:
    """An operator is a reader too.

    `undefined` is `scripts/ui_smoke.py`'s marker for a JavaScript value
    rendered into the DOM, and a legitimate label carrying it would disarm that
    check for a whole page. The dashes are the house rule.
    """
    for text in (declared.label, declared.detail):
        assert "undefined" not in text.lower()
        assert "—" not in text and "–" not in text
        assert text == text.strip()
        assert not text.endswith(".") or text is declared.detail


@pytest.mark.parametrize("declared", list(notices.NOTICE_TYPES.values()))
def test_every_notice_type_renders_from_empty_facts(declared) -> None:
    """A render that raises would be swallowed by `raise_notice` and the notice
    would simply never arrive, which is the failure this whole change is about."""
    notice = declared.render({})
    assert notice.title.strip()
    assert "—" not in notice.title and "–" not in notice.title
    for line in notice.body:
        assert "—" not in line and "–" not in line
