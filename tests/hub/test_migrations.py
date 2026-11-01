"""The ledger runner: applies once, in order, and a rerun is a no-op."""

from __future__ import annotations

from hub.db import core as hubdb
from hub.db.migrate import migrate


def test_baseline_applies_and_reruns_are_noops(hub_database_url: str) -> None:
    conn = hubdb.connect(hub_database_url)
    try:
        # The fixture already migrated; a second run must find nothing to do.
        assert migrate(conn) == []
        names = {
            row["table_name"]
            for row in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            ).fetchall()
        }
        assert {
            "schema_migrations",
            "instances",
            "users",
            "memberships",
            "auth_codes",
            "access_requests",
            "subscriptions",
            "stripe_events",
            "hub_settings",
            "notify_destinations",
            "notify_subscriptions",
            "notify_outbox",
            "notify_offered",
            "site_status",
        } <= names
        ledger = conn.execute("SELECT name FROM schema_migrations ORDER BY name").fetchall()
        assert [row["name"] for row in ledger] == [
            "0001_baseline.sql",
            "0002_user_picture.sql",
            "0003_session_epoch.sql",
            "0004_request_kind.sql",
            "0005_billing.sql",
            "0006_instance_pricing.sql",
            "0007_notice_ordering.sql",
            "0008_hub_settings.sql",
            "0009_instance_prices.sql",
            "0010_notifications.sql",
            "0011_notify_offered.sql",
            "0012_site_version.sql",
        ]
    finally:
        conn.close()


INSERT_REQUEST = (
    "INSERT INTO access_requests (id, email, kind) VALUES (%s, %s, %s) "
    "ON CONFLICT (email, wanted_instance_id, kind) DO NOTHING"
)


def test_request_dedup_treats_null_instance_as_equal(hub_conn: hubdb.Conn) -> None:
    """UNIQUE NULLS NOT DISTINCT: the unauthenticated no-instance case must
    dedupe, or the table grows without bound while the reply looks like a dedup."""
    hub_conn.execute(INSERT_REQUEST, ("req:1", "a@example.com", "access"))
    hub_conn.execute(INSERT_REQUEST, ("req:2", "a@example.com", "access"))
    count = hub_conn.execute("SELECT count(*) AS n FROM access_requests").fetchone()
    assert count["n"] == 1


def test_the_launch_list_does_not_swallow_a_later_access_request(
    hub_conn: hubdb.Conn,
) -> None:
    """⭐ Why `kind` joined the dedup key (migration 0004). Both rows carry a
    NULL `wanted_instance_id`, so under the old key the second insert hit ON
    CONFLICT DO NOTHING and disappeared -- and the endpoint is non-echoing by
    design, so nothing anywhere could report that the request needing a
    decision was the one thrown away."""
    hub_conn.execute(INSERT_REQUEST, ("req:1", "a@example.com", "launch"))
    hub_conn.execute(INSERT_REQUEST, ("req:2", "a@example.com", "access"))
    kinds = hub_conn.execute("SELECT kind FROM access_requests ORDER BY kind").fetchall()
    assert [row["kind"] for row in kinds] == ["access", "launch"]


def test_rows_that_predate_the_column_are_access_requests(hub_conn: hubdb.Conn) -> None:
    """The backfill default. Every row written before 0004 came through the
    portal, which is the door a person knocks on, so reading them as launch
    signups would bury real requests under a heading that says nobody is
    owed an answer."""
    hub_conn.execute(
        "INSERT INTO access_requests (id, email) VALUES (%s, %s)",
        ("req:legacy", "old@example.com"),
    )
    row = hub_conn.execute(
        "SELECT kind FROM access_requests WHERE id = %s", ("req:legacy",)
    ).fetchone()
    assert row["kind"] == "access"
