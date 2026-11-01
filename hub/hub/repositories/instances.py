"""The directory: instance rows, their credentials, and poll bookkeeping."""

from __future__ import annotations

from typing import Any

from hub.db.core import Conn, jdump

#: The poller's demotion threshold: consecutive misses before `live` becomes
#: `offline`. Three, so one flaky poll or one instance restart is invisible.
MISS_LIMIT = 3


def create(
    conn: Conn,
    *,
    instance_id: str,
    slug: str,
    display_name: str,
    tagline: str,
    public_url: str,
    oidc_client_id: str,
    oidc_client_secret_hash: str,
    mgmt_token_sealed: str,
) -> None:
    conn.execute(
        "INSERT INTO instances (id, slug, display_name, tagline, public_url,"
        " oidc_client_id, oidc_client_secret_hash, mgmt_token_sealed)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            instance_id,
            slug,
            display_name,
            tagline,
            public_url,
            oidc_client_id,
            oidc_client_secret_hash,
            mgmt_token_sealed,
        ),
    )


def by_id(conn: Conn, instance_id: str) -> dict[str, Any] | None:
    return conn.execute("SELECT * FROM instances WHERE id = %s", (instance_id,)).fetchone()


def by_slug(conn: Conn, slug: str) -> dict[str, Any] | None:
    return conn.execute("SELECT * FROM instances WHERE slug = %s", (slug,)).fetchone()


def by_client_id(conn: Conn, client_id: str) -> dict[str, Any] | None:
    return conn.execute(
        "SELECT * FROM instances WHERE oidc_client_id = %s", (client_id,)
    ).fetchone()


def list_all(conn: Conn) -> list[dict[str, Any]]:
    return conn.execute("SELECT * FROM instances ORDER BY slug").fetchall()


def list_public(conn: Conn) -> list[dict[str, Any]]:
    """The public directory: live AND offline, so extension presets survive an
    instance outage. `provisioning` (not yet real) and `retired` stay hidden."""
    return conn.execute(
        "SELECT slug, display_name, tagline, public_url, status, last_status,"
        "       last_seen_at, price_monthly_amount, price_annual_amount,"
        "       price_currency, stripe_price_monthly, stripe_price_annual"
        " FROM instances WHERE status IN ('live', 'offline') ORDER BY slug"
    ).fetchall()


def list_for_reader(conn: Conn, user_id: str) -> list[dict[str, Any]]:
    """The directory as ONE reader sees it: everything public, plus anything
    they hold that the public list leaves out.

    `list_public` is live-or-offline, while a membership survives on a
    `provisioning` domain -- so the front door built from the public list alone
    would drop a domain somebody actually reads, which looks to them like their
    access being withdrawn. Same columns as `list_public`, because the same page
    renders both.
    """
    return conn.execute(
        "SELECT slug, display_name, tagline, public_url, status, last_status,"
        "       last_seen_at, price_monthly_amount, price_annual_amount,"
        "       price_currency, stripe_price_monthly, stripe_price_annual"
        " FROM instances"
        " WHERE status IN ('live', 'offline')"
        "    OR (status != 'retired'"
        "        AND id IN (SELECT instance_id FROM memberships WHERE user_id = %s))"
        " ORDER BY slug",
        (user_id,),
    ).fetchall()


def list_pollable(conn: Conn) -> list[dict[str, Any]]:
    """Everything non-retired: polling `offline` rows is what lets an instance
    recover to `live` without an admin touch."""
    return conn.execute("SELECT * FROM instances WHERE status != 'retired'").fetchall()


def set_fields(
    conn: Conn,
    instance_id: str,
    *,
    display_name: str | None = None,
    tagline: str | None = None,
    public_url: str | None = None,
    status: str | None = None,
    mgmt_token_sealed: str | None = None,
) -> None:
    updates: list[str] = []
    params: list[Any] = []
    for column, value in (
        ("display_name", display_name),
        ("tagline", tagline),
        ("public_url", public_url),
        ("status", status),
        ("mgmt_token_sealed", mgmt_token_sealed),
    ):
        if value is not None:
            updates.append(f"{column} = %s")
            params.append(value)
    if not updates:
        return
    params.append(instance_id)
    conn.execute(f"UPDATE instances SET {', '.join(updates)} WHERE id = %s", params)


def set_secret_hash(conn: Conn, instance_id: str, secret_hash: str) -> None:
    conn.execute(
        "UPDATE instances SET oidc_client_secret_hash = %s WHERE id = %s",
        (secret_hash, instance_id),
    )


def record_poll_success(conn: Conn, instance_id: str, body: dict[str, Any]) -> str | None:
    """A 200 from the mgmt surface: store the envelope VERBATIM, stamp the
    clock, and promote to `live` -- from `provisioning` (first contact) and
    from `offline` (recovery) alike.

    Returns the status this row held BEFORE the promotion, which is how the
    caller tells a recovery from an ordinary healthy cycle. Taken in the same
    statement through a CTE rather than by reading first: a read-then-write
    would report every cycle as a recovery if two passes ever overlapped, and
    "the domain is back" said twice a minute is worse than not saying it.
    """
    row = conn.execute(
        "WITH before AS (SELECT status FROM instances WHERE id = %s)"
        " UPDATE instances SET last_status = %s::jsonb, last_seen_at = now(),"
        " poll_misses = 0, status = 'live'"
        " WHERE id = %s AND status != 'retired'"
        " RETURNING (SELECT status FROM before) AS was",
        (instance_id, jdump(body), instance_id),
    ).fetchone()
    return row["was"] if row else None


def record_poll_miss(conn: Conn, instance_id: str) -> dict[str, Any] | None:
    """One atomic statement: count the miss and demote when the count says so.
    `provisioning` never demotes -- an instance that has never answered is not
    DOWN, it is not up yet."""
    return conn.execute(
        "UPDATE instances SET poll_misses = poll_misses + 1,"
        " status = CASE WHEN status = 'live' AND poll_misses + 1 >= %s"
        "   THEN 'offline' ELSE status END"
        " WHERE id = %s AND status != 'retired'"
        " RETURNING poll_misses, status",
        (MISS_LIMIT, instance_id),
    ).fetchone()


def set_billing_enforced(conn: Conn, *, instance_id: str, enforced: bool) -> None:
    """Whether this domain's paywall bites, and a version bump so it travels.

    The bump is unconditional rather than compared first: an operator who
    flips a switch back to where it already was has still ASKED for that state,
    and re-sending it is how a domain that missed the last push catches up.
    """
    conn.execute(
        "UPDATE instances SET billing_enforced = %s,"
        " settings_version = settings_version + 1 WHERE id = %s",
        (enforced, instance_id),
    )


def priced(conn: Conn) -> list[dict[str, Any]]:
    """Instances with a price set, for the sweep that keeps the quoted amount
    honest. Nothing else needs this narrow a list."""
    return conn.execute(
        "SELECT id, slug, stripe_price_monthly, stripe_price_annual,"
        "       price_monthly_amount, price_annual_amount, price_currency"
        " FROM instances WHERE stripe_price_monthly IS NOT NULL"
        "   AND status != 'retired'"
    ).fetchall()


def set_price_amounts(
    conn: Conn, *, instance_id: str, monthly: int | None, annual: int | None, currency: str | None
) -> None:
    """Correct the quoted amounts from Stripe, leaving the ids alone.

    Separate from `set_pricing` because they answer different questions:
    that one SETS what a domain sells, this one records what Stripe says it
    currently costs. Merging them would let a reconcile invent a product.
    """
    conn.execute(
        "UPDATE instances SET price_monthly_amount = %s, price_annual_amount = %s,"
        " price_currency = %s WHERE id = %s",
        (monthly, annual, currency, instance_id),
    )


def record_settings_push(conn: Conn, *, instance_id: str, version: int) -> None:
    """Stamp only what was delivered.

    `GREATEST` for the reason `subscriptions.record_push` gives: a push in
    flight can be overtaken by a newer flip, and writing the delivered version
    unconditionally would move the watermark backwards and re-send forever, or
    forward past a version nobody sent.
    """
    conn.execute(
        "UPDATE instances SET settings_pushed_version = GREATEST(settings_pushed_version, %s)"
        " WHERE id = %s",
        (version, instance_id),
    )


def set_pricing(
    conn: Conn,
    *,
    slug: str,
    product_id: str,
    price_monthly: str,
    price_annual: str,
    monthly_amount: int,
    annual_amount: int,
    currency: str,
) -> None:
    """What this instance sells. Empty until somebody sets it, and an instance
    with no prices cannot take money -- deliberately, because the alternative is
    falling back to another domain's product (migration 0006).

    THE AMOUNTS AS WELL AS THE IDS. Checkout needs only the id, but the gallery
    a stranger picks a domain from has to name the price before any subscription
    exists to read it off (migration 0009).
    """
    conn.execute(
        "UPDATE instances SET stripe_product_id = %s,"
        " stripe_price_monthly = %s, stripe_price_annual = %s,"
        " price_monthly_amount = %s, price_annual_amount = %s, price_currency = %s"
        " WHERE slug = %s",
        (
            product_id,
            price_monthly,
            price_annual,
            monthly_amount,
            annual_amount,
            currency,
            slug,
        ),
    )
