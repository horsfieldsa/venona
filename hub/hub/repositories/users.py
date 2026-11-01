"""Users and memberships. One aggregate: a membership is meaningless without
both of its ends, and every consumer reads them joined."""

from __future__ import annotations

from typing import Any

from hub.db.core import Conn


def create(conn: Conn, *, user_id: str, email: str, display_name: str | None) -> None:
    conn.execute(
        "INSERT INTO users (id, email, display_name) VALUES (%s, %s, %s)",
        (user_id, email.strip().lower(), display_name),
    )


def by_email(conn: Conn, email: str) -> dict[str, Any] | None:
    return conn.execute("SELECT * FROM users WHERE email = %s", (email.strip().lower(),)).fetchone()


def by_id(conn: Conn, user_id: str) -> dict[str, Any] | None:
    return conn.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()


def list_all(conn: Conn) -> list[dict[str, Any]]:
    return conn.execute("SELECT * FROM users ORDER BY email").fetchall()


def count(conn: Conn, *, status: str | None = None) -> int:
    """How many accounts, for the operator dashboard. A count is its own query
    rather than `len()` of a listing: the listing is a page of fifty now, and
    counting a page would report the page size as the total."""
    sql = "SELECT count(*) AS n FROM users"
    params: tuple[Any, ...] = ()
    if status is not None:
        sql += " WHERE status = %s"
        params = (status,)
    return conn.execute(sql, params).fetchone()["n"]


def list_with_memberships(
    conn: Conn, *, limit: int | None = None, offset: int = 0
) -> list[dict[str, Any]]:
    """The accounts page: users, memberships folded to one
    `slug:role · status, slug:role · status` string -- a rendering, not an
    aggregate anyone parses.

    THE SUBSCRIPTION SITS INSIDE THE MEMBERSHIP, not in a column beside it. A
    subscription buys a seat on ONE instance, so somebody with two memberships
    and one subscription has to show which of the two is paid for; two parallel
    lists would leave the operator matching them up by position and getting it
    wrong the first time the orders differ.

    Absent means never subscribed, which is why the status is omitted rather
    than written as "none": a column of "none" against every instance nobody
    pays for is noise, and the states worth spotting -- `past_due`, `lapsed` --
    are the ones that then stand out.

    LEFT JOIN LATERAL rather than a plain join: a seat can hold several
    subscription rows over time (cancel, resubscribe), and the newest is the one
    that describes it. A plain join would multiply the membership row by its
    whole history.

    `ORDER BY u.email` is what makes paging stable. Without a total order two
    pages can share a row and skip another, which reads as an account that
    vanished.
    """
    sql = (
        "SELECT u.*,"
        " string_agg("
        "   i.slug || ':' || m.role || coalesce(' · ' || s.status, ''),"
        "   ', ' ORDER BY i.slug) AS memberships"
        " FROM users u"
        " LEFT JOIN memberships m ON m.user_id = u.id"
        " LEFT JOIN instances i ON i.id = m.instance_id"
        " LEFT JOIN LATERAL ("
        "   SELECT status FROM subscriptions"
        "    WHERE user_id = m.user_id AND instance_id = m.instance_id"
        "    ORDER BY created_at DESC LIMIT 1"
        " ) s ON true"
        " GROUP BY u.id ORDER BY u.email"
    )
    params: tuple[Any, ...] = ()
    if limit is not None:
        sql += " LIMIT %s OFFSET %s"
        params = (limit, offset)
    return conn.execute(sql, params).fetchall()


def set_status(conn: Conn, user_id: str, status: str) -> None:
    conn.execute("UPDATE users SET status = %s WHERE id = %s", (status, user_id))


def record_login(
    conn: Conn,
    user_id: str,
    *,
    google_subject: str,
    display_name: str | None,
    picture: str | None = None,
) -> None:
    """Stamp what a completed Google login proves. The display name only fills
    a gap -- an established name is the user's, not Google's to overwrite. The
    picture is the opposite: Google's to refresh, so the newest one wins and a
    login without the claim keeps the last."""
    conn.execute(
        "UPDATE users SET google_subject = %s, last_login_at = now(),"
        " display_name = COALESCE(display_name, %s),"
        " picture_url = COALESCE(%s, picture_url)"
        " WHERE id = %s",
        (google_subject, display_name, picture, user_id),
    )


# -- memberships --------------------------------------------------------


def grant(conn: Conn, *, user_id: str, instance_id: str, role: str) -> None:
    conn.execute(
        "INSERT INTO memberships (user_id, instance_id, role) VALUES (%s, %s, %s)"
        " ON CONFLICT (user_id, instance_id) DO UPDATE SET role = excluded.role",
        (user_id, instance_id, role),
    )


def revoke(conn: Conn, *, user_id: str, instance_id: str) -> None:
    conn.execute(
        "DELETE FROM memberships WHERE user_id = %s AND instance_id = %s",
        (user_id, instance_id),
    )


def role_for(conn: Conn, *, user_id: str, instance_id: str) -> str | None:
    row = conn.execute(
        "SELECT role FROM memberships WHERE user_id = %s AND instance_id = %s",
        (user_id, instance_id),
    ).fetchone()
    return row["role"] if row else None


def instances_for(conn: Conn, user_id: str) -> list[dict[str, Any]]:
    """One user's instances, with role, live-ness first. What `hub memberships`
    prints for a person."""
    return conn.execute(
        "SELECT i.slug, i.display_name, i.tagline, i.public_url, i.status, m.role"
        " FROM memberships m JOIN instances i ON i.id = m.instance_id"
        " WHERE m.user_id = %s AND i.status != 'retired'"
        " ORDER BY (i.status != 'live'), i.slug",
        (user_id,),
    ).fetchall()


def seats_for(conn: Conn, user_id: str) -> list[dict[str, Any]]:
    """Memberships as BILLING sees them: the instance id a subscription is keyed
    on, plus what a redirect needs.

    Separate from `instances_for` rather than widening it. That one is printed;
    this one carries an internal id, and a query whose result is half rendered
    and half not is how an id ends up on a page.
    """
    return conn.execute(
        "SELECT i.id, i.slug, i.display_name, i.tagline, i.public_url, m.role,"
        " i.stripe_product_id, i.stripe_price_monthly, i.stripe_price_annual"
        " FROM memberships m JOIN instances i ON i.id = m.instance_id"
        " WHERE m.user_id = %s AND i.status != 'retired'"
        " ORDER BY i.slug",
        (user_id,),
    ).fetchall()


def bump_session_epoch(conn: Conn, *, user_id: str, now: float) -> None:
    """Every hub session this user holds, in every browser, dies at its next
    request: `deps.session_email` refuses a token issued before this mark.
    The instance's `users.session_epoch` is the same design."""
    conn.execute("UPDATE users SET session_epoch = %s WHERE id = %s", (now, user_id))


def instances_to_sign_out(conn: Conn, user_id: str) -> list[dict[str, Any]]:
    """Everywhere a hub sign-out must also sign out: this user's instances,
    with the sealed credential the mgmt call needs. A retired instance is
    excluded -- retirement already refuses its whole OIDC surface."""
    return conn.execute(
        "SELECT i.slug, i.public_url, i.mgmt_token_sealed"
        " FROM memberships m JOIN instances i ON i.id = m.instance_id"
        " WHERE m.user_id = %s AND i.status != 'retired'",
        (user_id,),
    ).fetchall()


def members_of(conn: Conn, instance_id: str) -> list[dict[str, Any]]:
    return conn.execute(
        "SELECT u.email, u.display_name, u.status, m.role"
        " FROM memberships m JOIN users u ON u.id = m.user_id"
        " WHERE m.instance_id = %s ORDER BY u.email",
        (instance_id,),
    ).fetchall()
