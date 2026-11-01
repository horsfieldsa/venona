"""The paywall: who is refused, who keeps their archive, and what stops costing.

Three separate mechanisms, and the tests are grouped that way because they fail
differently. The MATCHER decides which routes need a subscription and has one
regex hazard that would silently make the personal brief free. The CEILING
decides which briefs a lapsed reader may open, at the row rather than the route.
The SPEND GATES decide who we keep paying for, and neither is reachable from a
page, so nothing else in the suite would notice them regressing.
"""

from __future__ import annotations

import pytest
from venona.api import authz, security
from venona.db import core as db
from venona.db.repositories import users as users_repo
from venona.services import user_service

pytestmark = pytest.mark.anyio


#: Whether this instance charges. Most of the file is about the PAYWALL, so it
#: has to be on -- `entitled` short-circuits to True when nobody is charged, and
#: without saying so these tests would assert the paywall works while measuring
#: an instance that has no paywall.
@pytest.fixture
def charging(monkeypatch):
    monkeypatch.setenv("VENONA_BILLING_ENFORCED", "1")
    assert authz.billing_enforced() is True


@pytest.fixture
def not_charging(monkeypatch):
    monkeypatch.setenv("VENONA_BILLING_ENFORCED", "")
    assert authz.billing_enforced() is False


# ── the matcher ──────────────────────────────────────────────────────
#
# `/api/briefs/{brief_id}` compiles to `^/api/briefs/[^/]+$`, which ALSO matches
# `/api/briefs/latest` and `/api/briefs/for-you`. Sorting only happens within a
# set, so a literal in one frozenset cannot shadow a wildcard in another -- both
# are compiled into one ordered table for exactly this reason.


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/briefs/for-you"),
        ("POST", "/api/briefs/for-you/generate"),
        ("POST", "/api/mcp"),
        ("POST", "/api/annotate"),
        ("POST", "/api/graph/query"),
        ("GET", "/api/learning/queue"),
        ("GET", "/api/search"),
    ],
)
def test_these_reader_routes_need_a_subscription(method: str, path: str) -> None:
    assert authz.entitlement_required(method, path) is True


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/briefs"),
        ("GET", "/api/briefs/latest"),
        ("GET", "/api/briefs/search"),
        ("GET", "/api/briefs/brief:2026-01-01"),
        ("GET", "/api/auth/session"),
        ("PATCH", "/api/preferences"),
        ("GET", "/api/data/stats"),
    ],
)
def test_these_survive_a_lapse(method: str, path: str) -> None:
    assert authz.entitlement_required(method, path) is False


def test_the_wildcard_does_not_swallow_for_you() -> None:
    """THE defect this table exists to prevent. `for-you` shares a shape with
    `{brief_id}`, and losing that race would make the only per-reader model
    spend a reader can trigger free to anyone who ever paid once."""
    assert authz.entitlement_required("GET", "/api/briefs/for-you") is True
    assert authz.entitlement_required("GET", "/api/briefs/brief:x") is False


def test_an_unclassified_route_is_treated_as_paid() -> None:
    """Default-deny, matching `role_for_request`. Forgetting to classify a new
    reader feature must fail towards a refusal, never towards a giveaway."""
    assert authz.entitlement_required("GET", "/api/something-invented-tomorrow") is True


# ── the principal ────────────────────────────────────────────────────


def _record(**over) -> user_service.UserRecord:
    base = {
        "id": "user:r",
        "email": "r@example.com",
        "role": authz.READER,
        "status": user_service.ACTIVE,
        "session_epoch": 0.0,
    }
    return user_service.UserRecord(**{**base, **over})


@pytest.mark.parametrize("status", ["trialing", "active", "past_due", "comped"])
def test_these_statuses_read(charging, status: str) -> None:
    """`charging` IS THE ASSERTION. `UserRecord.entitled` returns True at its
    first line when the instance takes no money, and the suite runs with billing
    off -- so without this fixture the tuple that decides who may read a paid
    product could be emptied and both this and `test_past_due_still_reads` would
    still pass."""
    assert _record(entitlement_status=status).entitled is True


@pytest.mark.parametrize("status", ["none", "lapsed"])
def test_these_do_not(charging, status: str) -> None:
    assert _record(entitlement_status=status).entitled is False


def test_past_due_still_reads(charging) -> None:
    """Stripe is still retrying the card. Cutting somebody off at the first
    decline is how a recoverable lapse becomes a cancellation."""
    assert _record(entitlement_status="past_due").entitled is True


def test_an_admin_is_always_entitled(charging) -> None:
    """An admin operates the instance. A paywall between the operator and the
    surface they came to fix fires at the worst possible moment."""
    assert _record(role=authz.ADMIN, entitlement_status="lapsed").entitled is True


def test_a_record_built_without_the_projection_reads_everything(charging) -> None:
    """The break-glass admin, and anything else constructed without a row. The
    credential that exists for when all else has failed must not be the one the
    paywall refuses."""
    assert _record().entitled is True
    assert _record().archive_ceiling is None


def test_a_lapsed_reader_keeps_a_ceiling_and_an_entitled_one_has_none(charging) -> None:
    lapsed = _record(entitlement_status="lapsed", entitled_through="2026-03-01")
    assert lapsed.archive_ceiling == "2026-03-01"
    assert (
        _record(entitlement_status="active", entitled_through="2026-03-01").archive_ceiling is None
    )


def test_the_default_principal_is_entitled() -> None:
    """`Principal` defaults, for the root bearer, the mgmt token and auth-off."""
    assert security.Principal(email="x", via="bearer").entitled is True
    assert security.Principal(email="x", via="disabled").archive_ceiling is None


# ── the archive ceiling ──────────────────────────────────────────────


@pytest.fixture
def briefs(database_url: str):
    """Three briefs a month apart, so a ceiling can sit between them."""
    conn = db.connect(database_url)
    try:
        conn.execute("DELETE FROM brief_blocks")
        conn.execute("DELETE FROM brief_items")
        conn.execute("DELETE FROM briefs")
        for day in ("2026-01-01", "2026-02-01", "2026-03-01"):
            conn.execute(
                "INSERT INTO briefs (id, date, title, generated_at, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (f"brief:{day}", day, f"Brief {day}", f"{day}T06:00:00Z", f"{day}T06:00:00Z"),
            )
        yield conn
    finally:
        conn.close()


def test_no_ceiling_sees_everything(briefs) -> None:
    from venona.services import brief_service

    items, total = brief_service.list_briefs(briefs, limit=10, offset=0)
    assert total == 3


def test_a_ceiling_narrows_the_archive_and_its_total(briefs) -> None:
    """The count as well as the page, or a lapsed reader is told the archive
    holds three briefs and shown two."""
    from venona.services import brief_service

    items, total = brief_service.list_briefs(briefs, limit=10, offset=0, ceiling="2026-02-01")
    assert total == 2
    assert [i.id for i in items] == ["brief:2026-02-01", "brief:2026-01-01"]


def test_latest_returns_the_last_covered_brief_not_a_refusal(briefs) -> None:
    """`/` renders this. Refusing would bounce a lapsed reader to the paywall
    instead of to the archive they paid for (ADR 0211)."""
    from venona.services import brief_service

    assert brief_service.latest_brief_id(briefs) == "brief:2026-03-01"
    assert brief_service.latest_brief_id(briefs, ceiling="2026-02-01") == "brief:2026-02-01"


def test_a_bookmarked_id_past_the_ceiling_is_not_served(briefs) -> None:
    """The direct fetch is the way past a filtered archive if it is missed."""
    from venona.services import brief_service

    assert brief_service.get_brief(briefs, "brief:2026-03-01") is not None
    assert brief_service.get_brief(briefs, "brief:2026-03-01", ceiling="2026-02-01") is None


def test_the_arrows_do_not_point_past_the_ceiling(briefs) -> None:
    """A lapsed reader on their last brief must not be handed a working link to
    the one immediately past it, which is the one they most want."""
    from venona.db.repositories import brief as brief_repo

    row = brief_repo.select_brief_neighbours(
        briefs, "brief:2026-02-01", ceiling="2026-02-01"
    ).fetchone()
    assert row["newer_id"] is None
    assert row["older_id"] == "brief:2026-01-01"

    unbounded = brief_repo.select_brief_neighbours(briefs, "brief:2026-02-01").fetchone()
    assert unbounded["newer_id"] == "brief:2026-03-01"


def test_search_respects_the_ceiling(briefs) -> None:
    from venona.services import brief_service

    assert brief_service.search_briefs(briefs, q="Brief").total == 3
    assert brief_service.search_briefs(briefs, q="Brief", ceiling="2026-02-01").total == 2


# ── the spend gates ──────────────────────────────────────────────────
#
# Neither is reachable from a page. A regression here costs money silently.


@pytest.fixture
def readers(database_url: str):
    conn = db.connect(database_url)
    try:
        for uid, status, through in (
            ("user:paid", "active", None),
            ("user:trial", "trialing", "2026-04-01T00:00:00Z"),
            ("user:lapsed", "lapsed", "2026-01-01T00:00:00Z"),
            ("user:stale", "trialing", "2026-01-01T00:00:00Z"),
            ("user:never", "none", None),
        ):
            conn.execute(
                "INSERT INTO users (id, email, role, status, created_at, last_seen_at,"
                " entitlement_status, entitled_through)"
                " VALUES (?, ?, 'reader', 'active', ?, ?, ?, ?)",
                (
                    uid,
                    f"{uid}@example.com",
                    "2026-03-01T00:00:00Z",
                    "2026-03-01T00:00:00Z",
                    status,
                    through,
                ),
            )
        yield conn
    finally:
        conn.close()


SINCE = "2026-02-01T00:00:00Z"
NOW = "2026-03-02T00:00:00Z"


def test_without_enforcement_every_active_reader_is_warmed(readers) -> None:
    """An instance that takes no money has every account reading `none`.
    Filtering unconditionally would silently stop writing the product."""
    ids = {r["id"] for r in users_repo.recently_seen(readers, since=SINCE)}
    assert "user:never" in ids and "user:lapsed" in ids


def test_a_lapsed_reader_is_dropped_from_the_warm_pass(readers) -> None:
    """THE line that stops the spend. A lapsed reader can still sign in to read
    their frozen archive, and every visit refreshes `last_seen_at` -- so without
    this they stay in the fanout at roughly $0.40 a day forever."""
    ids = {r["id"] for r in users_repo.recently_seen(readers, since=SINCE, entitled_only=True)}
    assert "user:lapsed" not in ids
    assert "user:never" not in ids
    assert "user:paid" in ids


def test_a_stalled_projection_stops_the_spend_rather_than_continuing_it(readers) -> None:
    """`user:stale` still reads `trialing` because the hub went down before it
    could push. `entitled_through` has passed, and this instance holds that date
    itself -- so the local brake stops the fanout without the hub's help."""
    without = {r["id"] for r in users_repo.recently_seen(readers, since=SINCE, entitled_only=True)}
    assert "user:stale" in without

    with_brake = {
        r["id"] for r in users_repo.recently_seen(readers, since=SINCE, now=NOW, entitled_only=True)
    }
    assert "user:stale" not in with_brake
    assert "user:paid" in with_brake, "an account with no end date must not be caught by the brake"


def test_a_comped_account_is_never_caught_by_the_brake(readers) -> None:
    """`comped` carries no end date by design: the operator, a design partner."""
    readers.execute("UPDATE users SET entitlement_status = 'comped' WHERE id = 'user:lapsed'")
    ids = {
        r["id"] for r in users_repo.recently_seen(readers, since=SINCE, now=NOW, entitled_only=True)
    }
    assert "user:lapsed" in ids


def test_the_auth_columns_carry_the_entitlement() -> None:
    """`_AUTH_COLUMNS` is a NAMED list, deliberately, so a column added to the
    table does not silently widen the hot path. The cost is that a column the
    boundary needs can be left out of it -- and `_to_record` defaults a missing
    one to `comped`, which reads everything, so the paywall would simply never
    fire and nothing else would notice.
    """
    selected = {column.name for column in users_repo._AUTH_COLUMNS}
    assert {
        "entitlement_status",
        "entitled_through",
        "billing_notice",
        "billing_notice_at",
    } <= selected


#: Every query whose row reaches a reader of the entitlement columns. All three
#: name their columns explicitly, which is the point -- and also the hazard.
ENTITLEMENT_READERS = ("listing", "profile")


@pytest.mark.parametrize("query", ENTITLEMENT_READERS)
def test_every_admin_query_carries_the_entitlement(database_url: str, query: str) -> None:
    """The `_AUTH_COLUMNS` hazard, generalised -- because it happened twice.

    `admin.py::_row_to_user` reads these off rows from BOTH `listing` and
    `profile`, and defaults a missing column to `none`. `none` is not a neutral
    fallback: `docs/ADMIN-GUIDE.md` defines it as "never subscribed". So a query
    that forgets the columns reports every paying subscriber as not started, and
    only on the screens fed by that query -- which is why the list looked right
    and a mutation response did not.
    """
    conn = db.connect(database_url)
    try:
        conn.execute(
            "INSERT INTO users (id, email, role, status, created_at, entitlement_status)"
            " VALUES ('user:cols', 'cols@example.com', 'reader', 'active', ?, 'active')",
            ("2026-01-01T00:00:00Z",),
        )
        if query == "listing":
            row = next(r for r in users_repo.listing(conn) if r["id"] == "user:cols")
        else:
            row = users_repo.profile(conn, "user:cols")
        assert row is not None
        assert "entitlement_status" in row, f"`{query}` does not select entitlement_status"
        assert row["entitlement_status"] == "active", (
            f"`{query}` lost the entitlement, so admin surfaces report a "
            f"subscriber as never having subscribed"
        )
    finally:
        conn.close()


def test_a_reader_who_never_subscribed_gets_no_archive(charging) -> None:
    """ "Never started" and "lapsed" both fail `entitled` and mean OPPOSITE
    things about the archive. A null `entitled_through` read as "no limit" would
    hand the whole archive to somebody who has not paid at all -- and the brief
    routes are deliberately open at the route level, so this property is the
    only thing standing there."""
    never = _record(entitlement_status="none")
    assert never.archive_ceiling == user_service.UserRecord.NOTHING
    assert never.archive_ceiling is not None


def test_the_nothing_ceiling_actually_matches_no_brief(briefs) -> None:
    from venona.services import brief_service

    ceiling = user_service.UserRecord.NOTHING
    assert brief_service.latest_brief_id(briefs, ceiling=ceiling) is None
    items, total = brief_service.list_briefs(briefs, limit=10, offset=0, ceiling=ceiling)
    assert total == 0 and items == []
    assert brief_service.get_brief(briefs, "brief:2026-01-01", ceiling=ceiling) is None


def test_a_new_reader_reads_everything_when_nobody_is_charged(not_charging) -> None:
    """THE bug this guards. Without it a fresh account on a local or
    self-hosted instance gets an empty product -- no refusal, no error, just
    200s with nothing in them."""
    fresh = _record(entitlement_status="none")
    assert fresh.entitled is True
    assert fresh.archive_ceiling is None


def test_even_a_lapsed_row_reads_everything_when_nobody_is_charged(not_charging) -> None:
    """A stale row from an instance that has since stopped charging must not
    keep enforcing a paywall nobody is behind."""
    stale = _record(entitlement_status="lapsed", entitled_through="2026-01-01")
    assert stale.entitled is True
    assert stale.archive_ceiling is None


def test_the_same_rows_are_gated_once_the_instance_charges(charging) -> None:
    """The other half: turning it on has to actually turn it on."""
    assert _record(entitlement_status="none").entitled is False
    assert _record(entitlement_status="lapsed", entitled_through="2026-01-01").entitled is False


@pytest.mark.parametrize("enforced", [True, False])
def test_the_ceiling_always_follows_the_predicate(monkeypatch, enforced: bool) -> None:
    """`entitled` is the ONE answer, and the ceiling is derived from it.

    The route check, the warm pass and the notification fan-out were each given
    this answer separately and the ceiling was missed -- so it read the COLUMN
    while the other three read the predicate. This asserts they cannot diverge
    again, under either setting.
    """
    monkeypatch.setenv("VENONA_BILLING_ENFORCED", "1" if enforced else "0")
    for status in ("none", "lapsed", "trialing", "active", "past_due", "comped"):
        record = _record(entitlement_status=status, entitled_through="2026-01-01")
        assert (record.archive_ceiling is None) is record.entitled


def test_an_admin_is_never_dropped_from_the_warm_pass(readers) -> None:
    """`UserRecord.entitled` exempts admins; a column predicate cannot see a
    role, so it had to be said again in SQL. Without it the operator's own row
    -- which defaults to `none` like everyone else's -- drops them from their
    own instance's personal brief."""
    readers.execute("UPDATE users SET role='admin' WHERE id='user:never'")
    ids = {r["id"] for r in users_repo.recently_seen(readers, since=SINCE, entitled_only=True)}
    assert "user:never" in ids


def test_an_admin_is_not_caught_by_the_local_expiry_brake(readers) -> None:
    readers.execute("UPDATE users SET role='admin' WHERE id='user:stale'")
    ids = {
        r["id"] for r in users_repo.recently_seen(readers, since=SINCE, now=NOW, entitled_only=True)
    }
    assert "user:stale" in ids


def test_an_admin_still_receives_brief_notifications(readers) -> None:
    """The delivery half. Same argument, different query, and no page reaches
    either of them."""
    from venona.db.repositories import notify as notify_repo

    readers.execute("UPDATE users SET role='admin' WHERE id='user:never'")
    now = "2026-01-01T00:00:00Z"
    readers.execute(
        "INSERT INTO notify_destinations"
        " (id, owner_kind, owner_id, kind, label, target, status, created_at, updated_at)"
        " VALUES ('dest:a', 'reader', 'user:never', 'webhook', 'theirs', 'https://x',"
        " 'active', ?, ?)",
        (now, now),
    )
    readers.execute(
        "INSERT INTO notify_subscriptions (destination_id, notice_type, created_at)"
        " VALUES (?, ?, ?)",
        ("dest:a", "brief.shared.published", now),
    )
    owners = notify_repo.readers_subscribed(
        readers, notice_type="brief.shared.published", entitled_only=True
    )
    assert "user:never" in owners


# ── the reader's own account survives a lapse ────────────────────────
#
# These were paid by omission, and an unsubscribed reader could not open
# Relevance, Notifications or Agents -- three of the five entries in their own
# settings rail -- and got the global refusal toast on each. That is the person
# we are asking to subscribe, locked out of the screens where they would manage
# the account and the card.


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/preferences/notifications"),
        ("POST", "/api/preferences/notifications"),
        ("GET", "/api/preferences/notifications/history"),
        ("DELETE", "/api/preferences/notifications/dest:1"),
        ("PATCH", "/api/preferences"),
        ("GET", "/api/relevance/settings"),
        ("PATCH", "/api/relevance/settings"),
        ("GET", "/api/relevance/follows"),
        ("GET", "/api/relevance/suggestions"),
        ("GET", "/api/meta/rubric"),
        ("GET", "/api/meta/mcp"),
        ("PATCH", "/api/auth/me"),
    ],
)
def test_account_surfaces_do_not_need_a_subscription(method: str, path: str) -> None:
    assert authz.entitlement_required(method, path) is False, (
        f"{method} {path} is the reader's own account, not the product"
    )


def test_disconnecting_an_assistant_never_needs_a_subscription() -> None:
    """Revocation especially: somebody must always be able to disconnect an
    assistant they no longer trust, subscription or no subscription."""
    assert authz.entitlement_required("DELETE", "/api/meta/mcp/tokens/tok:1") is False


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/briefs/for-you"),
        ("POST", "/api/briefs/for-you/generate"),
        ("POST", "/api/graph/query"),
        ("GET", "/api/learning/queue"),
        ("GET", "/api/search"),
        ("POST", "/api/mcp"),
        ("POST", "/api/annotate"),
    ],
)
def test_the_product_itself_still_does(method: str, path: str) -> None:
    """The other half. Widening the account surfaces must not have opened the
    reading, the graph, learning, or the two reader-reachable spends."""
    assert authz.entitlement_required(method, path) is True


# ── where the answer comes from ──────────────────────────────────────
#
# THREE SOURCES AND AN ORDER (ADR 0214). The environment overrides in both
# directions, then the hub's switch as the mgmt push last left it, then off.
# Every one of these is a way for a paywall to be wrong about a real reader, so
# each is asserted rather than reasoned about.


@pytest.fixture
def no_env(monkeypatch):
    """Neither on nor off in the environment, which is production's state: the
    variable is declared nowhere in the deployment config.

    SET TO EMPTY, NEVER DELETED, and that distinction has now cost time twice.
    `load_dotenv_into_environ` runs with `override=False` on import, so a key
    that is ABSENT is refilled from the developer's own `.env` -- and a test
    that deleted this one asserted against whatever that file happened to say.
    `conftest.py`'s suite-wide fixture makes the same point about the same
    variable.
    """
    monkeypatch.setenv("VENONA_BILLING_ENFORCED", "")
    monkeypatch.setattr(authz, "_HUB_BILLING", None)


def test_the_environment_wins_when_it_says_on(monkeypatch, no_env) -> None:
    monkeypatch.setattr(authz, "_HUB_BILLING", False)
    monkeypatch.setenv("VENONA_BILLING_ENFORCED", "1")
    assert authz.billing_enforced() is True


def test_the_environment_wins_when_it_says_OFF(monkeypatch, no_env) -> None:
    """The half that did not exist. `VENONA_BILLING_ENFORCED=0` used to be
    indistinguishable from unset, so an operator whose hub had gone wrong had no
    way to force the gate open on the box in front of them -- which is the
    moment a break-glass is for."""
    monkeypatch.setattr(authz, "_HUB_BILLING", True)
    monkeypatch.setenv("VENONA_BILLING_ENFORCED", "0")
    assert authz.billing_enforced() is False


def test_the_hub_decides_when_the_environment_is_silent(monkeypatch, no_env) -> None:
    monkeypatch.setattr(authz, "_HUB_BILLING", True)
    assert authz.billing_enforced() is True


def test_nothing_anywhere_is_off(no_env) -> None:
    """The state every instance ships in, and the one a fresh local box is in
    forever. Charging by default would take the product away from every
    self-hosted reader on the release that added a column."""
    assert authz.billing_enforced() is False


def test_a_refresh_reads_the_row_and_caches_it(database_url, no_env) -> None:
    """`billing_enforced()` answers from memory on every request, so the write
    and the refresh are two halves of one act: a push that stored the value and
    did not refresh would take effect on the next restart."""
    from venona.db import core as db
    from venona.db.repositories import hub_flags as flags_repo

    conn = db.connect(database_url)
    try:
        db.migrate(conn)
        with db.transaction(conn, what="test"):
            flags_repo.set_billing_enforced(conn, enforced=True, now="2026-09-09T00:00:00Z")
        assert authz.billing_enforced() is False, "not until something reads it"
        assert authz.refresh_billing_enforced(conn) is True
        assert authz.billing_enforced() is True
    finally:
        conn.close()
