"""The admin surface over HTTP. Contract C12's authorization addendum.

`venona users` was CLI-only and the argument was explicit: an HTTP endpoint that
grants a role is reachable by anything holding a session. This file is what says
the narrowed version holds -- authority may be reduced without limit and extended
only as far as `reader` -- and the approve path is where most of the ways to get
it wrong live.

THE ONE WORTH READING is
`test_approving_an_env_listed_address_is_refused`. It is not an obvious rule and
it is not caught by either authority guard: creating a `reader` row for an
address in `VENONA_AUTH_ALLOWED_EMAILS` silently DEMOTES a break-glass
administrator, because a row wins over the environment. The self-check compares
actor to target and the last-admin check counts rows an env admin does not have.
"""

from __future__ import annotations

import pathlib

import pytest
from venona.api import authz
from venona.db import core as db
from venona.db.repositories import access as access_repo
from venona.services import user_service

NOW = "2026-08-17T00:00:00Z"


def _request(conn: db.Conn, request_id: str, email: str, *, coverage: str = "programs") -> str:
    access_repo.insert_access_request(
        conn,
        request_id=request_id,
        email=email,
        coverage=coverage,
        role="analyst",
        now=NOW,
    )
    conn.commit()
    return request_id


@pytest.fixture
def admin(conn: db.Conn):
    user_service.create(
        conn,
        user_id="user:admin",
        email="admin@example.com",
        role=authz.ADMIN,
        now=NOW,
        allow_admin=True,
    )
    user_service.create(
        conn,
        user_id="user:second",
        email="second@example.com",
        role=authz.ADMIN,
        now=NOW,
        allow_admin=True,
    )
    conn.commit()
    return conn


# ── the queue ────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_the_queue_serves_the_coverage_note_whole(api_client, conn: db.Conn) -> None:
    """`cli.py::_access_list` refuses to truncate it, on the grounds that it is
    the only thing distinguishing a real analyst from a scraper. A screen that
    truncated it would make the decision against nothing."""
    long_note = "I cover " + ("undersea cable resilience, " * 20)
    _request(conn, "req:1", "a@example.com", coverage=long_note)

    body = (await api_client.get("/api/access/requests")).json()
    assert body["pending"] == 1
    assert body["items"][0]["coverage"] == long_note
    assert body["items"][0]["id"] == "req:1", "the queue needs the id the CLI never returned"


# ── approval ─────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_approving_creates_a_reader_and_links_the_row(api_client, conn: db.Conn) -> None:
    """The headline. Two rows, one transaction, and the account is a READER --
    approval never produces an administrator."""
    _request(conn, "req:1", "new@example.com")

    response = await api_client.post("/api/access/req:1/approve", json={"note": "known analyst"})
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["status"] == "approved"
    assert body["account_email"] == "new@example.com"
    assert body["note"] == "known analyst"

    created = user_service.lookup(conn, "new@example.com")
    assert created is not None
    assert created.role == authz.READER, "approval minted something other than a reader"
    assert created.active


@pytest.mark.anyio
async def test_approving_records_who_and_what_it_became(api_client, conn: db.Conn) -> None:
    """The audit trail is what makes the HTTP surface reviewable, so a creation
    that left no event would be the same write-only audit trail migration 0002
    shipped and this phase exists to finish."""
    _request(conn, "req:1", "new@example.com")
    await api_client.post("/api/access/req:1/approve", json={})

    events = user_service.events(conn, limit=10)
    assert [e["action"] for e in events] == [user_service.CREATED]
    assert "req:1" in events[0]["detail"]


@pytest.mark.anyio
@pytest.mark.negative
async def test_approving_an_env_listed_address_is_refused(
    api_client, api_settings, conn: db.Conn, monkeypatch
) -> None:
    """⭐ A row WINS over the environment, so creating a `reader` row for a
    break-glass address demotes that administrator on their next request.

    Neither authority guard catches it -- the self-check compares actor to target
    and the last-admin check counts `users` rows, which an env admin has none of.
    It is the "instance stranded" failure arriving through the door this phase
    opens, and nothing about the request looks unusual.
    """
    from venona.api import security

    monkeypatch.setenv("VENONA_AUTH_ALLOWED_EMAILS", "boss@example.com")
    security.get_auth_config.cache_clear()
    _request(conn, "req:1", "boss@example.com")

    response = await api_client.post("/api/access/req:1/approve", json={})
    security.get_auth_config.cache_clear()

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "bootstrap_address"
    assert user_service.lookup(conn, "boss@example.com") is None, (
        "a reader row was created for a break-glass administrator, demoting them"
    )


@pytest.mark.anyio
@pytest.mark.negative
async def test_approving_a_suspended_account_is_refused(api_client, conn: db.Conn) -> None:
    """`upsert_by_email` is `ON CONFLICT DO NOTHING`, so it preserves the row's
    role AND its status. Without this the request would go green and the person
    still could not sign in -- and since the form is PUBLIC, a suspended user can
    file a fresh request indistinguishable from a stranger's."""
    user_service.create(
        conn, user_id="user:gone", email="gone@example.com", role=authz.READER, now=NOW
    )
    user_service.set_status(conn, user_id="user:gone", status=user_service.SUSPENDED, now=1.0)
    conn.commit()
    _request(conn, "req:1", "gone@example.com")

    response = await api_client.post("/api/access/req:1/approve", json={})

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "account_suspended"
    assert not user_service.get(conn, "user:gone").active, "approval reinstated somebody"


@pytest.mark.anyio
async def test_approving_an_existing_active_account_links_rather_than_claims(
    api_client, conn: db.Conn
) -> None:
    """`DO NOTHING` would have discarded the insert silently. Reporting a
    creation that did not happen is the audit lie this guards."""
    user_service.create(
        conn,
        user_id="user:has",
        email="has@example.com",
        role=authz.ADMIN,
        now=NOW,
        allow_admin=True,
    )
    conn.commit()
    _request(conn, "req:1", "has@example.com")

    body = (await api_client.post("/api/access/req:1/approve", json={})).json()

    assert body["account_email"] == "has@example.com"
    # Their role is untouched -- approval never changes an existing row.
    assert user_service.get(conn, "user:has").role == authz.ADMIN
    assert [e["action"] for e in user_service.events(conn, limit=10)] == [], (
        "an event claimed a creation that ON CONFLICT DO NOTHING declined to make"
    )


@pytest.mark.anyio
async def test_approving_one_request_closes_the_others_for_that_address(
    api_client, conn: db.Conn
) -> None:
    """The form is public and permits duplicates by design -- a person who
    mistypes their address asks twice. Leaving the siblings pending would leave
    the queue permanently showing people who already have accounts, which is how
    a queue stops being read."""
    _request(conn, "req:1", "dup@example.com")
    _request(conn, "req:2", "dup@example.com")

    await api_client.post("/api/access/req:1/approve", json={})

    assert (await api_client.get("/api/access/requests")).json()["pending"] == 0
    sibling = access_repo.by_id(conn, "req:2")
    assert sibling["status"] == "declined"
    assert "req:1" in sibling["note"]


@pytest.mark.anyio
@pytest.mark.negative
async def test_a_request_cannot_be_decided_twice(api_client, conn: db.Conn) -> None:
    """`AND status = 'pending'` is what makes a lost race write nothing, and the
    409 is what tells the second admin their click did nothing rather than
    letting the screen imply it worked."""
    _request(conn, "req:1", "new@example.com")
    assert (await api_client.post("/api/access/req:1/approve", json={})).status_code == 200

    again = await api_client.post("/api/access/req:1/decline", json={})
    assert again.status_code == 409
    assert again.json()["detail"]["error"] == "already_decided"


@pytest.mark.anyio
async def test_declining_creates_no_account(api_client, conn: db.Conn) -> None:
    _request(conn, "req:1", "no@example.com")
    body = (await api_client.post("/api/access/req:1/decline", json={"note": "scraper"})).json()

    assert body["status"] == "declined"
    assert body["account_email"] is None
    assert user_service.lookup(conn, "no@example.com") is None


@pytest.mark.anyio
@pytest.mark.negative
async def test_an_unknown_request_is_a_404(api_client) -> None:
    assert (await api_client.post("/api/access/req:nope/approve", json={})).status_code == 404


# ── the user surface ─────────────────────────────────────────────────


@pytest.mark.anyio
async def test_the_user_list_shows_who_has_never_signed_in(api_client, admin) -> None:
    """venona sends no email, so "approved and never arrived" is the only invite
    follow-up the operator gets. It became meaningful when the callback started
    stamping identity; before that it was true of everybody."""
    body = (await api_client.get("/api/admin/users")).json()

    assert body["active_admins"] == 2
    assert all(u["never_signed_in"] for u in body["items"])


@pytest.mark.anyio
async def test_an_admin_can_be_demoted_and_it_is_recorded(api_client, admin) -> None:
    response = await api_client.patch("/api/admin/users/user:second/role", json={"role": "reader"})
    assert response.status_code == 200
    assert response.json()["role"] == authz.READER

    events = (await api_client.get("/api/admin/events")).json()["items"]
    assert events[0]["action"] == user_service.ROLE_CHANGED
    assert "admin -> reader" in events[0]["detail"]


@pytest.mark.anyio
@pytest.mark.negative
async def test_promotion_to_admin_is_refused_by_the_boundary(api_client, admin) -> None:
    """⭐ THE CEILING, and it is a `Literal` rather than a runtime check -- so the
    refusal happens before the handler runs and the route appears in the OpenAPI
    document as accepting `reader` only. The frontend cannot offer a control the
    server would refuse, and no handler has to remember."""
    await api_client.patch("/api/admin/users/user:second/role", json={"role": "reader"})

    response = await api_client.patch("/api/admin/users/user:second/role", json={"role": "admin"})
    assert response.status_code == 422, (
        "HTTP promoted somebody to admin -- the ceiling this phase is built around is gone"
    )


@pytest.mark.anyio
@pytest.mark.negative
async def test_the_last_admin_refusal_reaches_the_wire_as_409(api_client, conn: db.Conn) -> None:
    """409, never 403. The caller IS an administrator; what they asked for is the
    thing that cannot happen. A 403 would fire the SPA's global forbidden toast
    -- "your account cannot do that on this instance" -- which is untrue, and
    would hide the sentence naming the rule."""
    user_service.create(
        conn,
        user_id="user:solo",
        email="solo@example.com",
        role=authz.ADMIN,
        now=NOW,
        allow_admin=True,
    )
    conn.commit()

    response = await api_client.post("/api/admin/users/user:solo/suspend", json={})

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "authority_refused"
    assert "only active administrator" in response.json()["detail"]["message"]


@pytest.mark.anyio
async def test_suspending_records_the_operators_note(api_client, admin) -> None:
    """The note is the half a log cannot reconstruct. Accepting it and dropping
    it would be the accepted-and-ignored-parameter mistake in a new place."""
    await api_client.post(
        "/api/admin/users/user:second/suspend", json={"note": "left the programme"}
    )
    events = (await api_client.get("/api/admin/events")).json()["items"]
    assert "left the programme" in events[0]["detail"]


@pytest.mark.anyio
async def test_the_overview_counts_what_has_no_other_surface(api_client, admin, conn) -> None:
    _request(conn, "req:1", "waiting@example.com")
    body = (await api_client.get("/api/admin/overview")).json()

    assert body["pending_requests"] == 1
    assert body["users_total"] == 2
    assert body["active_admins"] == 2
    assert body["users_never_signed_in"] == 2
    assert body["spend_days"] == 30


@pytest.mark.anyio
async def test_the_overview_counts_suspensions_and_live_credentials(
    api_client, admin, conn: db.Conn
) -> None:
    """The two counters the case above leaves at zero.

    Both are computed by the database now, and a counter that is only ever
    exercised at zero would agree with any query at all.
    """
    from venona.db.repositories import token as token_repo
    from venona.services import token_service

    token_service.mint(conn, label="live", token_id="tok:live", now=NOW, user_id="user:second")
    token_service.mint(conn, label="gone", token_id="tok:gone", now=NOW, user_id="user:second")
    token_repo.update_mcp_tokens_by_revoked_at_id(conn, NOW, "tok:gone")
    conn.commit()

    await api_client.post("/api/admin/users/user:second/suspend", json={})
    body = (await api_client.get("/api/admin/overview")).json()

    assert body["users_suspended"] == 1
    assert body["users_total"] == 2
    assert body["tokens_live"] == 1, "a revoked credential is not a live one"


@pytest.mark.anyio
async def test_the_overview_names_which_build_this_is(api_client, admin, monkeypatch) -> None:
    """The one string on a page of figures, and it had no surface at all.

    `/api/mgmt/status` has carried the build since the hub existed, but only a
    `vmgmt_` bearer may call that -- so the person running the instance was the
    one party who could not see which version they were running. It rides this
    endpoint rather than `/api/health` because health is reader-visible and is
    declared `response_model_exclude_unset=True`, which silently drops a field
    a handler forgets to set.
    """
    from venona.api.routers import admin as admin_router

    monkeypatch.setattr(admin_router, "COMMIT", "abcdef1")
    body = (await api_client.get("/api/admin/overview")).json()

    assert body["version"] == (pathlib.Path(__file__).parents[1] / "VERSION").read_text().strip()
    assert body["commit"] == "abcdef1"
    assert body["started_at"].endswith("Z")


@pytest.mark.anyio
async def test_the_overview_says_nothing_rather_than_guessing_a_build(
    api_client, admin, monkeypatch
) -> None:
    """Null travels, so the screen can say `local`. A blank cell there would
    read as a failed load on a page whose other rows are all numbers."""
    from venona.api.routers import admin as admin_router

    monkeypatch.setattr(admin_router, "COMMIT", None)
    assert (await api_client.get("/api/admin/overview")).json()["commit"] is None


# ── every credential on the instance ─────────────────────────────────


def _token(
    conn: db.Conn, token_id: str, label: str, *, user_id: str | None, now: str = NOW
) -> None:
    """Mint through the service, except for the token that has no owner.

    `token_service.mint` REQUIRES a `user_id` -- there is no "the operator" to
    fall back on any more -- so a row with a NULL owner, which is every token
    minted before identity existed, cannot be made through the front door. The
    repository is the only seam that can write one, and it is the case the LEFT
    JOIN exists for.
    """
    from venona.db.repositories import token as token_repo
    from venona.services import token_service

    if user_id is None:
        token_repo.insert_mcp_tokens(
            conn, token_id, label, "not-a-real-hash", "vmcp_old", now, None, "mcp", None
        )
    else:
        token_service.mint(conn, label=label, token_id=token_id, now=now, user_id=user_id)
    conn.commit()


@pytest.mark.anyio
async def test_the_token_list_names_the_owner_rather_than_their_id(api_client, admin) -> None:
    """⭐ THE DEFECT THIS ROUTE EXISTS FOR. `GET /api/meta/mcp` is filtered to the
    calling user by design, so an admin could not see what credentials existed on
    their own box -- a leaked one was invisible to the only person who could
    revoke it.

    The address rather than the id, because `user:second` on screen answers
    nothing. That is `AuthorityEvent`'s rule and it is a LEFT JOIN in the
    repository, not a lookup per row."""
    _token(admin, "mcp:one", "laptop", user_id="user:admin", now="2026-08-17T00:00:00Z")
    _token(admin, "mcp:two", "the other one", user_id="user:second", now="2026-08-17T00:00:01Z")

    body = (await api_client.get("/api/admin/tokens")).json()

    assert [t["label"] for t in body["items"]] == ["the other one", "laptop"], (
        "newest first, and both owners -- a per-user filter leaked back in"
    )
    assert {t["user_email"] for t in body["items"]} == {
        "admin@example.com",
        "second@example.com",
    }
    assert all("token" not in t and "token_hash" not in t for t in body["items"])


@pytest.mark.anyio
async def test_a_token_with_no_owner_is_still_shown(api_client, admin) -> None:
    """The LEFT JOIN, and why it is not an inner one. `mcp_tokens.user_id` is
    nullable and NULL for every token minted before identity existed -- the
    oldest credentials on the instance, and the ones least likely to be
    remembered. An inner join would drop exactly those, which is the opposite of
    what an operator hunting a leak needs."""
    _token(admin, "mcp:orphan", "who made this", user_id=None)

    body = (await api_client.get("/api/admin/tokens")).json()

    assert len(body["items"]) == 1
    assert body["items"][0]["user_email"] is None
    assert body["items"][0]["user_id"] is None


@pytest.mark.anyio
async def test_a_revoked_token_is_not_a_credential_that_exists(api_client, admin) -> None:
    """Live only, and there is no `include_revoked`. The question is what could be
    used against this instance right now; history is `venona mcp token ls --all`."""
    from venona.services import token_service

    _token(admin, "mcp:gone", "rotated out", user_id="user:admin")
    token_service.revoke(admin, "mcp:gone", now=NOW)
    admin.commit()

    body = (await api_client.get("/api/admin/tokens")).json()
    assert body["items"] == []


async def test_spend_does_not_add_two_kinds_of_number_together(api_client, make_database) -> None:
    """`cost_usd` holds four incompatible quantities and one column.

    A subscription's shadow price is what the work WOULD have cost; a metered
    provider's figure is a bill. Summing them across an instance that switched
    services mid-month produces a total that is not any quantity at all --
    neither what was spent nor what was saved -- and it would be reported to an
    administrator as if it were money.
    """
    from venona.db import core as db

    conn = db.connect(make_database())
    with db.transaction(conn):
        for i, (basis, cost) in enumerate(
            (("list_price", 4.0), ("measured", 1.0), ("list_price", 2.0))
        ):
            db.insert(
                conn,
                "llm_calls",
                {
                    "id": f"call:{i}",
                    "task": "analyze",
                    "model": "m",
                    "cost_usd": cost,
                    "cost_basis": basis,
                    "status": "ok",
                    "created_at": "2099-01-01T00:00:00Z",
                },
            )

    from venona.db.repositories import metrics as metrics_repo

    rows = metrics_repo.cost_by_day(conn, since="2099-01-01T00:00:00Z")
    by_basis = {r["cost_basis"]: (float(r["cost_usd"]), int(r["calls"])) for r in rows}
    assert by_basis == {"list_price": (6.0, 2), "measured": (1.0, 1)}, (
        f"the two kinds of number were not kept apart: {by_basis}"
    )
