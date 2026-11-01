"""Subscriptions, and the entitlement counter that carries them to an instance.

One row per STRIPE subscription, never one per seat: somebody who cancels and
resubscribes gets a new `sub_...` id, and the history is what makes a win-back
possible to reason about. `ux_subscriptions_live` (migration 0005) is what holds
the invariant that actually matters, which is at most one live subscription per
seat.
"""

from __future__ import annotations

from typing import Any

from hub.db.core import Conn

#: Statuses that entitle. `past_due` is in here on purpose: it is Stripe still
#: retrying a card, and cutting somebody off at the first decline turns a
#: recoverable lapse into a cancellation. `unpaid` is NOT -- that is Stripe
#: having given up.
ENTITLING = ("trialing", "active", "past_due")

#: Statuses that mean a live seat, matching `ux_subscriptions_live`. Wider than
#: `ENTITLING` because an `unpaid` subscription still occupies the seat: a second
#: one alongside it would bill one person twice.
LIVE = ("trialing", "active", "past_due", "unpaid")


def by_stripe_id(conn: Conn, stripe_subscription_id: str) -> dict[str, Any] | None:
    return conn.execute(
        "SELECT * FROM subscriptions WHERE stripe_subscription_id = %s",
        (stripe_subscription_id,),
    ).fetchone()


def lock_for_update(conn: Conn, stripe_subscription_id: str) -> dict[str, Any] | None:
    """Take the row lock BEFORE deciding whether an event is stale.

    Webhooks arrive concurrently and the hub is async, so two events for one
    subscription interleave at every `await` inside a single process --
    `numInstances: 1` does not prevent this and the event ledger does not either
    (that catches a replay of ONE event, not a race between two). Read, compare
    and write have to sit inside one transaction holding this lock, or the later
    event's write is lost. CLAUDE.md's READ COMMITTED rule, applied verbatim.
    """
    return conn.execute(
        "SELECT * FROM subscriptions WHERE stripe_subscription_id = %s FOR UPDATE",
        (stripe_subscription_id,),
    ).fetchone()


def live_for(conn: Conn, *, user_id: str, instance_id: str) -> dict[str, Any] | None:
    """The subscription that decides entitlement, or None.

    Ordered by `created_at DESC` rather than trusting the partial index to
    return one row: the index guarantees at most one live subscription, and this
    query answers correctly even while a migration or a manual repair has left
    two.
    """
    return conn.execute(
        "SELECT * FROM subscriptions"
        " WHERE user_id = %s AND instance_id = %s AND status = ANY(%s)"
        " ORDER BY created_at DESC LIMIT 1",
        (user_id, instance_id, list(LIVE)),
    ).fetchone()


def latest_for(conn: Conn, *, user_id: str, instance_id: str) -> dict[str, Any] | None:
    """The newest subscription of any status. This is what a lapsed reader's
    archive ceiling comes from, so it must see terminal rows too."""
    return conn.execute(
        "SELECT * FROM subscriptions"
        " WHERE user_id = %s AND instance_id = %s"
        " ORDER BY created_at DESC LIMIT 1",
        (user_id, instance_id),
    ).fetchone()


def upsert(
    conn: Conn,
    *,
    subscription_id: str,
    user_id: str,
    instance_id: str,
    stripe_subscription_id: str,
    status: str,
    price_id: str | None,
    current_period_end: str | None,
    cancel_at_period_end: bool,
    trial_end: str | None,
    stripe_updated_at: str | None,
    stripe_customer_id: str | None = None,
    unit_amount: int | None = None,
    currency: str | None = None,
    interval: str | None = None,
    canceled_at: str | None = None,
) -> None:
    """Write the state an event describes. `subscription_id` is only used when
    the row is new, so a caller may mint one unconditionally."""
    conn.execute(
        "INSERT INTO subscriptions"
        " (id, user_id, instance_id, stripe_subscription_id, status, price_id,"
        "  current_period_end, cancel_at_period_end, trial_end, stripe_updated_at,"
        "  stripe_customer_id, unit_amount, currency, interval, canceled_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (stripe_subscription_id) DO UPDATE SET"
        "   status = EXCLUDED.status,"
        "   price_id = EXCLUDED.price_id,"
        "   current_period_end = EXCLUDED.current_period_end,"
        "   cancel_at_period_end = EXCLUDED.cancel_at_period_end,"
        "   trial_end = EXCLUDED.trial_end,"
        "   stripe_updated_at = EXCLUDED.stripe_updated_at,"
        "   stripe_customer_id = EXCLUDED.stripe_customer_id,"
        "   unit_amount = EXCLUDED.unit_amount,"
        "   currency = EXCLUDED.currency,"
        "   interval = EXCLUDED.interval,"
        "   canceled_at = EXCLUDED.canceled_at,"
        "   updated_at = now()",
        (
            subscription_id,
            user_id,
            instance_id,
            stripe_subscription_id,
            status,
            price_id,
            current_period_end,
            cancel_at_period_end,
            trial_end,
            stripe_updated_at,
            stripe_customer_id,
            unit_amount,
            currency,
            interval,
            canceled_at,
        ),
    )


def set_notice(
    conn: Conn,
    *,
    stripe_subscription_id: str,
    notice: str | None,
    notice_at: str | None,
    source_at: str | None = None,
) -> None:
    """What the reader is told, as a kind. Never a sentence -- the words live in
    `web/src/lib/billing.ts`, for the reason CLAUDE.md gives about run
    outcomes.

    `source_at` is WHEN the thing being reported happened, in Stripe's clock,
    and it is what orders two invoice events against each other. Omitted by the
    subscription handler, which orders itself on `stripe_updated_at` and holds
    the row lock while it does.
    """
    conn.execute(
        "UPDATE subscriptions SET notice = %s, notice_at = %s,"
        " notice_updated_at = COALESCE(%s, notice_updated_at), updated_at = now()"
        " WHERE stripe_subscription_id = %s",
        (notice, notice_at, source_at, stripe_subscription_id),
    )


def set_stripe_customer(conn: Conn, *, user_id: str, customer_id: str) -> None:
    """Bind a Stripe customer to a person, once.

    `WHERE stripe_customer_id IS NULL` rather than an unconditional update:
    `checkout.session.completed` can be redelivered, and a person who somehow
    reached two customers must not have the binding flip between them on every
    replay. First one wins, and the mismatch is then visible rather than
    oscillating.
    """
    conn.execute(
        "UPDATE users SET stripe_customer_id = %s WHERE id = %s AND stripe_customer_id IS NULL",
        (customer_id, user_id),
    )


def user_by_customer(conn: Conn, customer_id: str) -> dict[str, Any] | None:
    return conn.execute(
        "SELECT * FROM users WHERE stripe_customer_id = %s", (customer_id,)
    ).fetchone()


# ── the entitlement counter ──────────────────────────────────────────
#
# Bumped for ANYTHING the instance renders, not just for access changes. A first
# card decline leaves the reader entitled and changes only the banner; if this
# tracked entitlement alone, in-app dunning would never fire and the failure
# would look exactly like everything working.


def bump_entitlement(conn: Conn, *, user_id: str, instance_id: str) -> None:
    conn.execute(
        "UPDATE memberships SET entitlement_version = entitlement_version + 1"
        " WHERE user_id = %s AND instance_id = %s",
        (user_id, instance_id),
    )


def bump_all_for_user(conn: Conn, user_id: str) -> None:
    """Every membership this person holds. A customer-level event (a dispute, a
    deleted customer) is not about one seat."""
    conn.execute(
        "UPDATE memberships SET entitlement_version = entitlement_version + 1 WHERE user_id = %s",
        (user_id,),
    )


def pending_pushes(conn: Conn, *, limit: int) -> list[dict[str, Any]]:
    """Memberships the instance has not been told about, newest debt first.

    `m.role` IS SELECTED AND IT MATTERS. The push rides `action="update"`, whose
    handler treats the role as authoritative and changes it when it differs --
    so sending a fixed `"reader"` here demotes every instance administrator who
    has a subscription, silently, once per cycle.

    Bounded by `limit` because this runs inside the poller, whose only other
    cost is one GET per instance -- an unbounded fan-out of POSTs there would be
    a second subsystem hiding in a status check. Offline instances are excluded
    by `poll_misses`: re-POSTing at a dead host every five minutes forever
    achieves nothing and buries the live ones behind it.
    """
    return conn.execute(
        "SELECT m.user_id, m.instance_id, m.entitlement_version, m.role,"
        "       u.email, i.public_url, i.mgmt_token_sealed"
        " FROM memberships m"
        " JOIN users u ON u.id = m.user_id"
        " JOIN instances i ON i.id = m.instance_id"
        " WHERE m.entitlement_pushed_version < m.entitlement_version"
        "   AND i.status = 'live' AND i.poll_misses < 3"
        " ORDER BY m.entitlement_version - m.entitlement_pushed_version DESC"
        " LIMIT %s",
        (limit,),
    ).fetchall()


def record_push(conn: Conn, *, user_id: str, instance_id: str, version: int) -> None:
    """Stamp only what was actually delivered.

    `GREATEST` because a push in flight can be overtaken by a newer bump, and
    writing the delivered version unconditionally would move the watermark
    BACKWARDS and re-send forever -- or, worse, forward past a version nobody
    sent.
    """
    conn.execute(
        "UPDATE memberships"
        " SET entitlement_pushed_version = GREATEST(entitlement_pushed_version, %s)"
        " WHERE user_id = %s AND instance_id = %s",
        (version, user_id, instance_id),
    )
