"""The Stripe event ledger: what arrived, and whether it has already been done.

Stripe retries a webhook until it gets a 2xx, so the same `evt_...` arriving
twice is routine rather than a fault. This table is how the second one is
recognised as a replay -- and it is the only reliable way, because an event is
not identifiable by its contents: two genuine events can carry identical bodies.

What this does NOT do is order two DIFFERENT events racing one subscription.
That is `subscriptions.lock_for_update` plus a `stripe_updated_at` comparison.
Two mechanisms, two problems, neither substituting for the other.

DELIBERATELY SMALL. There is no `release`, no `mark_failed` and no listing here,
because the route claims and applies inside ONE transaction: a failure or a hard
stop rolls the claim back with the work, so there is no stranded row to release
and no failed row to record or display. Earlier versions of this module carried
all three, with docstrings naming an operator page that was never built -- which
is worse than absent, because it reads as a safety net.

Delivery failures are visible in Stripe's own dashboard, which is where
`docs/RUNBOOK.md` points and where the alert belongs. Events LOST beyond
Stripe's retry window are a different problem, and `poller.reconcile_subscriptions`
is what answers it.
"""

from __future__ import annotations

from hub.db.core import Conn


def claim(conn: Conn, *, event_id: str, event_type: str, payload_digest: str) -> bool:
    """Record an event, returning whether THIS caller owns processing it.

    False means a replay: some earlier delivery already claimed it. The caller
    answers 200 and does nothing, which is exactly what Stripe wants -- a replay
    that returns an error is retried again, forever.
    """
    row = conn.execute(
        "INSERT INTO stripe_events (event_id, type, payload_digest)"
        " VALUES (%s, %s, %s)"
        " ON CONFLICT (event_id) DO NOTHING"
        " RETURNING event_id",
        (event_id, event_type, payload_digest),
    ).fetchone()
    return row is not None


def mark_processed(conn: Conn, *, event_id: str, status: str = "processed") -> None:
    """Stamp the claim as done. Runs inside the caller's transaction, so this
    and the claim commit together or not at all."""
    conn.execute(
        "UPDATE stripe_events SET processed_at = now(), status = %s WHERE event_id = %s",
        (status, event_id),
    )
