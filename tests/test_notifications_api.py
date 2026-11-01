"""The two notification surfaces, over the real routers.

`api_client` rather than the services directly, because the properties under
test here are properties of the WIRE. `response_model` filters, so a field the
model omits vanishes silently -- which is the mechanism that has to be trusted
when the whole point is that one field never appears.
"""

from __future__ import annotations

import pytest
from venona.db import core as db
from venona.db.repositories import notify as repo

pytestmark = pytest.mark.anyio

SECRET = "https://hooks.slack.com/services/T00/B00/zzzzSECRETzzzz"


async def _add(api_client, *, kind="webhook", target=SECRET, label="ops"):
    resp = await api_client.post(
        "/api/notifications", json={"kind": kind, "label": label, "target": target}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── the credential ───────────────────────────────────────────────────


async def test_no_response_anywhere_carries_the_stored_target(api_client) -> None:
    """The grep the design rests on.

    A webhook URL is a bearer token in URL clothing. `api/security.py` refuses
    to serve the root credential for this reason and `AccountView.vue` refuses
    to render it; venona ACCEPTS this one rather than minting it, which runs the
    other way, but "once stored it is never rendered back" is the same rule. An
    Edit control pre-filled from a response is how it would leak, so no response
    can carry it.
    """
    created = await _add(api_client)
    bodies = [
        (await api_client.get("/api/notifications")).text,
        (await api_client.get("/api/notifications/status")).text,
        (await api_client.get("/api/notifications/history")).text,
        (
            await api_client.patch(
                f"/api/notifications/{created['id']}",
                json={"notice_type": "run.failed", "on": True},
            )
        ).text,
        (await api_client.post(f"/api/notifications/{created['id']}/clear")).text,
    ]
    for body in bodies:
        assert "zzzzSECRET" not in body, body[:400]
    # The hint identifies without revealing.
    assert created["hint"].startswith("hooks.slack.com/")
    assert "zzzzSECRET" not in created["hint"]


async def test_replacing_an_address_takes_a_new_one_and_never_shows_the_old(
    api_client,
) -> None:
    created = await _add(api_client)
    resp = await api_client.put(
        f"/api/notifications/{created['id']}/target",
        json={"target": "https://hooks.slack.com/services/T1/B1/replaced"},
    )
    assert resp.status_code == 200
    assert "zzzzSECRET" not in resp.text
    assert resp.json()["hint"].endswith("aced")


# ── the words come from the server ───────────────────────────────────


async def test_the_switches_carry_their_own_labels(api_client) -> None:
    """No client holds a map of notice keys to English.

    `RunType.outcomes` ships the same way and for the same reason: a second copy
    in TypeScript drifts inside a release, and four surfaces once invented run
    outcome words independently with one of them wrong.
    """
    created = await _add(api_client)
    assert created["notices"], "a destination has to say what it can be told about"
    for notice in created["notices"]:
        assert notice["label"] and notice["detail"]
        assert "undefined" not in notice["label"]
    # An instance destination starts subscribed to the failures.
    assert any(n["on"] for n in created["notices"])


async def test_a_new_destination_is_subscribed_to_the_failures(api_client) -> None:
    created = await _add(api_client)
    on = {n["key"] for n in created["notices"] if n["on"]}
    assert "run.failed" in on
    assert "sources.disabled" in on


# ── refusals arrive as sentences ─────────────────────────────────────


@pytest.mark.parametrize(
    "target",
    ["http://hooks.slack.com/x", "https://127.0.0.1/x", "not a url", ""],
)
async def test_a_bad_address_is_refused_in_the_form(api_client, target: str) -> None:
    """With a sentence, at save time, rather than as a delivery that quietly
    never arrives. Same guard `PUT /api/config/sources` runs over a feed."""
    resp = await api_client.post(
        "/api/notifications", json={"kind": "webhook", "label": "x", "target": target}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]
    assert not resp.json()["detail"].startswith("Blocked")  # a sentence, not a class name


async def test_an_unknown_destination_is_a_404_not_a_403(api_client) -> None:
    """A 403 confirms the id exists, which turns this into an oracle over a
    table of credentials."""
    resp = await api_client.patch(
        "/api/notifications/dest:nope", json={"notice_type": "run.failed", "on": True}
    )
    assert resp.status_code == 404


# ── email, with no transport ─────────────────────────────────────────


async def test_the_screen_says_email_does_not_work_yet(api_client) -> None:
    """The state is on the wire, so the screen can render an honest sentence
    instead of two switches that would flip and do nothing."""
    body = (await api_client.get("/api/notifications")).json()
    assert body["email_available"] is False
    assert body["email_unavailable_reason"]


async def test_a_test_send_to_email_reports_the_reason(api_client) -> None:
    created = await _add(api_client, kind="email", target="ops@example.test", label="inbox")
    resp = await api_client.post(f"/api/notifications/{created['id']}/test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["delivered"] is False
    assert "email" in body["detail"].lower()
    # And it still shows what would have been sent, so the shape is checkable
    # before a provider exists.
    assert body["preview"]


# ── the reader's own ─────────────────────────────────────────────────


async def test_a_reader_never_types_their_own_address(api_client) -> None:
    """The account's address is used whatever the body said.

    Accepting a typed one would make this a way to have Venona send mail to a
    third party on request.
    """
    resp = await api_client.post(
        "/api/preferences/notifications",
        json={"kind": "email", "label": "my inbox", "target": "someone-else@evil.test"},
    )
    assert resp.status_code == 201
    assert resp.json()["hint"] != "someone-else@evil.test"


async def test_a_reader_sees_only_reader_switches(api_client) -> None:
    created = (
        await api_client.post(
            "/api/preferences/notifications",
            json={"kind": "email", "label": "mine", "target": "x@y.test"},
        )
    ).json()
    keys = {n["key"] for n in created["notices"]}
    assert keys == {"brief.shared.published", "brief.personal.ready"}
    # And none of them is on: nobody has asked to be written to.
    assert not any(n["on"] for n in created["notices"])


async def test_a_reader_cannot_subscribe_to_an_instance_notice(api_client) -> None:
    created = (
        await api_client.post(
            "/api/preferences/notifications",
            json={"kind": "email", "label": "mine", "target": "x@y.test"},
        )
    ).json()
    resp = await api_client.patch(
        f"/api/preferences/notifications/{created['id']}",
        json={"notice_type": "access.requested", "on": True},
    )
    assert resp.status_code == 400


async def test_an_admin_route_cannot_reach_a_reader_destination(api_client) -> None:
    """The privacy stance `0010_relevance.sql` and `0019_user_preferences.sql`
    both state: rows created by a route whose subject is the acting user, and
    NO admin surface over them."""
    created = (
        await api_client.post(
            "/api/preferences/notifications",
            json={"kind": "email", "label": "mine", "target": "x@y.test"},
        )
    ).json()

    listed = (await api_client.get("/api/notifications")).json()["destinations"]
    assert created["id"] not in {d["id"] for d in listed}

    for method, path in (
        ("patch", f"/api/notifications/{created['id']}"),
        ("delete", f"/api/notifications/{created['id']}"),
        ("post", f"/api/notifications/{created['id']}/clear"),
    ):
        call = getattr(api_client, method)
        resp = await (
            call(path, json={"notice_type": "run.failed", "on": True})
            if method == "patch"
            else call(path)
        )
        assert resp.status_code == 404, (method, path, resp.text)


async def test_the_history_a_reader_sees_is_their_own(api_client, api_app) -> None:
    from venona import notify

    reader = (
        await api_client.post(
            "/api/preferences/notifications",
            json={"kind": "email", "label": "mine", "target": "x@y.test"},
        )
    ).json()
    subscribed = await api_client.patch(
        f"/api/preferences/notifications/{reader['id']}",
        json={"notice_type": "brief.shared.published", "on": True},
    )
    assert subscribed.status_code == 200, subscribed.text
    assert any(
        n["key"] == "brief.shared.published" and n["on"] for n in subscribed.json()["notices"]
    ), subscribed.text
    instance = await _add(api_client)

    conn = db.connect(api_app.state.settings.database_url)
    try:
        owner = repo.destination(conn, destination_id=reader["id"])["owner_id"]
        notify.raise_notice(
            conn, "brief.shared.published", user_id=owner, brief_id="brief:1", title="T"
        )
        notify.raise_notice(conn, "run.failed", run_id="run:1", status="failed")
    finally:
        conn.close()

    mine = (await api_client.get("/api/preferences/notifications/history")).json()
    theirs = (await api_client.get("/api/notifications/history")).json()
    assert {i["destination"] for i in mine["items"]} == {"mine"}
    assert {i["destination"] for i in theirs["items"]} == {instance["label"]}
    # Every cap is announced.
    assert mine["limit"] and theirs["limit"]
    # And a row says WHAT it was in the registry's words, not its key.
    assert mine["items"][0]["notice_label"] != mine["items"][0]["notice_type"]


# ── the rail's mark ──────────────────────────────────────────────────


async def test_status_reports_a_struck_out_destination(api_client, api_app) -> None:
    """The chicken-and-egg case: the thing that broke is the telling, so the
    app has to carry it where somebody will see it without navigating."""
    created = await _add(api_client)
    conn = db.connect(api_app.state.settings.database_url)
    try:
        repo.set_status(
            conn,
            destination_id=created["id"],
            status="struck_out",
            now="2026-09-01T00:00:00.000Z",
        )
        conn.commit()
    finally:
        conn.close()
    status = (await api_client.get("/api/notifications/status")).json()
    assert status["struck_out"] == 1
    assert status["first_struck_out"] == "ops"


async def test_clearing_a_strike_is_reachable_over_http(api_client, api_app) -> None:
    """ADR 0041: the way back from auto-disabled must not be a CLI incantation
    printed in a banner."""
    created = await _add(api_client)
    conn = db.connect(api_app.state.settings.database_url)
    try:
        repo.set_status(
            conn,
            destination_id=created["id"],
            status="struck_out",
            now="2026-09-01T00:00:00.000Z",
        )
        conn.commit()
    finally:
        conn.close()
    resp = await api_client.post(f"/api/notifications/{created['id']}/clear")
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"
    assert resp.json()["failures"] == 0


async def test_deleting_a_destination_takes_its_queue_with_it(api_client, api_app) -> None:
    from venona import notify

    created = await _add(api_client)
    conn = db.connect(api_app.state.settings.database_url)
    try:
        notify.raise_notice(conn, "run.failed", run_id="run:1", status="failed")
        assert conn.execute("SELECT COUNT(*) AS n FROM notify_outbox").fetchone()["n"] == 1
    finally:
        conn.close()

    assert (await api_client.delete(f"/api/notifications/{created['id']}")).status_code == 204

    conn = db.connect(api_app.state.settings.database_url)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM notify_outbox").fetchone()["n"] == 0
    finally:
        conn.close()
