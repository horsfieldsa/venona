"""What one Stripe event does to our state.

Separate from the router so it can be tested against real event shapes without
a request, and so the router stays a transport. Every function here is
idempotent and ordering-independent: it writes the state the event DESCRIBES
rather than a delta, and an event older than what we already hold is discarded.

Out-of-order delivery is normal, not exceptional. Stripe sends events
concurrently and retries independently, so `customer.subscription.updated` for
9:00 can land after the one for 9:01. An integration that applies the last
message to arrive is wrong roughly as often as the network is slow.
"""

from __future__ import annotations

import logging
from typing import Any

from hub import ids, notices, notify, stripe_webhook
from hub.db.core import Conn
from hub.repositories import subscriptions as subs_repo

log = logging.getLogger("hub.billing")

#: Events we act on. Everything else is recorded and ignored -- Stripe sends a
#: great many, and an endpoint that tries to be exhaustive grows a handler for
#: something it does not understand.
#:
#: `customer.subscription.*` is the SOURCE OF TRUTH for entitlement. The invoice
#: events contribute only a notice: deriving access from invoices as well would
#: give one question two answers.
HANDLED = (
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "customer.subscription.trial_will_end",
    "invoice.paid",
    "invoice.payment_failed",
    "invoice.payment_action_required",
)


#: Statuses where a payment is still in the air, so an invoice-driven notice is
#: still true and must survive a `customer.subscription.updated`.
#:
#: `incomplete` is the one that matters: it is the status a subscription sits in
#: while the CARDHOLDER'S BANK is asking them to confirm -- exactly when
#: `action_required` is on screen -- so leaving it out lets the next routine
#: update erase the only message telling them what to do.
#:
#: `active` is deliberately absent: the payment went through, and the notice
#: should clear.
UNRESOLVED = ("past_due", "unpaid", "incomplete")


def _epoch(value: Any) -> str | None:
    """Stripe sends unix seconds; the column is TIMESTAMPTZ. psycopg will not
    coerce an int, so this is where the two meet.

    Also accepts a datetime unchanged, because a value round-tripped OUT of the
    database and written back -- which is what preserving an invoice-driven
    notice does -- arrives here already converted.
    """
    from datetime import UTC, datetime

    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return datetime.fromtimestamp(float(value), tz=UTC).isoformat()


def _id_of(value: Any) -> str | None:
    """Stripe sends an id or the expanded object, depending on the event."""
    if isinstance(value, dict):
        return value.get("id")
    return str(value) if value else None


class OutOfOrder(Exception):
    """An event that cannot be applied YET, and must not be treated as applied.

    Raised rather than returned so it rolls the webhook route's transaction
    back: the event ledger's claim and the work commit together, so undoing the
    claim is what lets Stripe's next retry take the event again. Returning False
    would mark it `ignored`, answer 200, and Stripe would never send it twice.
    """


def _seat_from(obj: dict[str, Any]) -> tuple[str | None, str | None]:
    """Who and which instance, from the metadata Checkout stamped.

    Set on the SUBSCRIPTION as well as the session (`stripe.py`), because every
    `customer.subscription.*` event after the first carries only the
    subscription -- metadata on the session alone would leave them
    unattributable.
    """
    meta = obj.get("metadata") or {}
    return meta.get("user_id"), meta.get("instance_id")


def _is_stale(existing: dict[str, Any] | None, incoming_updated: str | None) -> bool:
    """Whether we already hold something newer than this event.

    Compares STRIPE's own timestamps to each other, never ours to theirs: two
    clocks with an unknown offset cannot order events between them.

    Parsed to datetimes rather than compared as strings. Both sides are ISO-8601
    UTC today so a lexicographic compare happens to work, and that is exactly
    the kind of accident that stops working when one side gains a different
    offset or microsecond precision.

    STRICTLY `<`, so an event from the same second as the one held is APPLIED.
    Stripe stamps `created` in whole seconds and gives no sequence number, so
    two events one second apart cannot be ordered from the payload at all --
    and between applying the later arrival and discarding it, applying is the
    one that cannot silently drop a cancellation. The hourly sweep is what
    corrects the case where the arrival order was wrong.
    """
    if existing is None or incoming_updated is None:
        return False
    held = existing.get("stripe_updated_at")
    if held is None:
        return False
    from datetime import datetime

    incoming = datetime.fromisoformat(incoming_updated)
    return incoming < held


def apply_event(conn: Conn, event: dict[str, Any]) -> bool:
    event_type = event.get("type") or ""
    if event_type not in HANDLED:
        return False
    obj = ((event.get("data") or {}).get("object")) or {}
    if event_type == "checkout.session.completed":
        return _checkout_completed(conn, obj)
    if event_type.startswith("customer.subscription."):
        return _subscription_changed(conn, obj, event_type=event_type, created=event.get("created"))
    return _invoice_event(conn, obj, event_type=event_type)


def _checkout_completed(conn: Conn, session: dict[str, Any]) -> bool:
    """Bind the customer to the person. Entitlement is NOT decided here.

    `customer.subscription.created` carries the real state and arrives for the
    same checkout; doing it in both places is how two handlers end up
    disagreeing. Also: a session can complete with `payment_status` unpaid for
    asynchronous methods, so this must not be read as "they have paid".
    """
    user_id, instance_id = _seat_from(session)
    customer_id = session.get("customer")
    if user_id and customer_id:
        subs_repo.set_stripe_customer(conn, user_id=user_id, customer_id=str(customer_id))
        if instance_id:
            _grant_the_seat_paid_for(conn, user_id=user_id, instance_id=instance_id)
        return True
    log.warning("checkout.session.completed with no user_id metadata; ignoring")
    return False


def _grant_the_seat_paid_for(conn: Conn, *, user_id: str, instance_id: str) -> None:
    """The membership, created by the PAYMENT rather than by the click.

    Granting on the click instead would leave a membership behind for everybody
    who opens Checkout and closes the tab: accounts that never paid,
    indistinguishable in the directory from accounts that did, with nothing to
    prune them by. Granting here means an abandoned Checkout leaves NOTHING.

    The cost is that access waits on a webhook, so `/billing/return` performs
    the same grant when the browser comes back -- whichever arrives first wins,
    because `users_repo.grant` upserts.

    Deliberately no role choice: a seat somebody bought is a reader's seat.
    Administrators are granted by a person (ADR 0168).
    """
    from hub.repositories import users as users_repo

    if any(row["id"] == instance_id for row in users_repo.seats_for(conn, user_id)):
        return
    users_repo.grant(conn, user_id=user_id, instance_id=instance_id, role="reader")
    log.info("granted %s a seat on %s: they paid for it", user_id, instance_id)


def reconcile_subscription(conn: Conn, sub: dict[str, Any], *, now: float) -> bool:
    """Apply a subscription READ FROM STRIPE rather than delivered as an event.

    Stripe retries a failed webhook for about three days and then stops, so an
    outage longer than that loses events permanently and this side would keep an
    entitlement it has no right to -- or miss one somebody has paid for. Event
    delivery is a fast path; Stripe is the source of truth.

    `created` is deliberately None. A read reflects the CURRENT state, so it is
    never stale -- and `now` is OUR clock, which must not be written into a
    column holding Stripe's. Stamping it there makes every later `_is_stale`
    a cross-clock comparison, and because Stripe's `created` is whole seconds
    while ours is fractional, the very next genuine event from the same second
    is discarded, acknowledged, and never redelivered.
    """
    return _subscription_changed(conn, sub, event_type="reconcile", created=None, now=now)


def _subscription_changed(
    conn: Conn, sub: dict[str, Any], *, event_type: str, created: Any, now: float | None = None
) -> bool:
    """The source of truth. Write the state described, unless it is stale.

    `created` is STRIPE'S timestamp for the event, or None when this is a read
    rather than an event. `now` is a clock for dating a notice, which is a
    question about what to say and not about ordering.
    """
    stripe_id = sub.get("id")
    user_id, instance_id = _seat_from(sub)
    if not stripe_id or not user_id or not instance_id:
        log.warning("subscription event with no metadata (%s); ignoring", stripe_id)
        return False

    # The lock, then the staleness check, then the write -- all in one
    # transaction. At READ COMMITTED two concurrent handlers otherwise read the
    # same stored row and one write is lost; `numInstances: 1` does not help,
    # because they interleave inside a single async process (CLAUDE.md).
    with conn.transaction():
        existing = subs_repo.lock_for_update(conn, str(stripe_id))
        incoming = _epoch(created)
        if _is_stale(existing, incoming):
            log.info("discarding a stale %s for %s", event_type, stripe_id)
            return True

        status = "canceled" if event_type.endswith(".deleted") else (sub.get("status") or "unknown")
        notice, notice_at = stripe_webhook.derived_notice(
            {**sub, "status": status}, now=float(created or now or 0)
        )
        if notice is None and existing is not None and status in UNRESOLVED:
            # Keep an invoice-driven notice (`action_required`, `final_notice`)
            # that the subscription's own status cannot reproduce. Recomputing
            # it away would tell somebody waiting on their bank to replace a
            # card that works.
            notice, notice_at = existing.get("notice"), existing.get("notice_at")

        # A UNIQUE VIOLATION HERE IS AN ORDERING PROBLEM, NOT A BUG, and it is
        # worth naming rather than letting it surface as an unexplained 500.
        # `ux_subscriptions_live` allows one live subscription per seat, and a
        # resubscribe can deliver the NEW subscription's `created` before the
        # old one's `deleted` -- two live rows for a moment. The transaction
        # rolls back, Stripe retries, and by then the cancellation has landed.
        # Self-healing, but only because the retry exists.
        bought = _price_of(sub)
        subs_repo.upsert(
            conn,
            subscription_id=ids.subscription_id(),
            user_id=user_id,
            instance_id=instance_id,
            stripe_subscription_id=str(stripe_id),
            status=status,
            price_id=bought["price_id"],
            current_period_end=_epoch(_period_end_of(sub)),
            cancel_at_period_end=bool(sub.get("cancel_at_period_end")),
            trial_end=_epoch(sub.get("trial_end")),
            # HELD, not overwritten, when this is a read. The column orders
            # Stripe's events against each other and a reconcile has no Stripe
            # timestamp of its own to contribute.
            stripe_updated_at=incoming
            if incoming is not None
            else (existing or {}).get("stripe_updated_at"),
            # The customer THIS subscription is on, which can differ from the
            # one bound to the person: `set_stripe_customer` fills a NULL and
            # never overwrites. Holding it here makes the divergence a query
            # rather than a round trip to Stripe.
            stripe_customer_id=_id_of(sub.get("customer")),
            unit_amount=bought["unit_amount"],
            currency=bought["currency"],
            interval=bought["interval"],
            canceled_at=_epoch(sub.get("canceled_at")),
        )
        subs_repo.set_notice(
            conn,
            stripe_subscription_id=str(stripe_id),
            notice=notice,
            notice_at=_epoch(notice_at),
        )
        # ONLY WHEN THE INSTANCE WOULD SEE SOMETHING DIFFERENT. The hourly sweep
        # re-applies every subscription on the account, and an unconditional
        # bump makes every one of them pending again -- so a queue bounded at
        # `PUSH_BUDGET` per cycle spends its whole budget re-sending state
        # nothing has changed, while a real change waits behind it.
        if _projection_changed(existing, status, sub, notice, notice_at):
            subs_repo.bump_entitlement(conn, user_id=user_id, instance_id=instance_id)
        _report_billing(
            conn,
            existing=existing,
            status=status,
            stripe_id=str(stripe_id),
            user_id=user_id,
            instance_id=instance_id,
            incoming=incoming,
        )
    return True


def _report_billing(
    conn: Conn,
    *,
    existing: dict[str, Any] | None,
    status: str,
    stripe_id: str,
    user_id: str,
    instance_id: str,
    incoming: str | None,
) -> None:
    """Tell the operator when a subscription needs a person.

    ONLY THE BAD STATUSES. A subscription going `active` or `trialing` is the
    system working, and paging on it teaches whoever reads these to stop reading
    them, which costs the ones that matter.

    ONLY ON A TRANSITION. The sweep re-applies every subscription on the account
    once an hour (`poller.RECONCILE_INTERVAL_SECONDS`), so a stable `past_due`
    passes through here all day. The dedupe key carries Stripe's own updated
    stamp on top of that, which makes a webhook RETRY idempotent while still
    letting a subscription that goes bad, recovers and goes bad again say so
    both times.
    """
    if status in subs_repo.ENTITLING and status != "past_due":
        _report_subscribed(
            conn,
            existing=existing,
            status=status,
            stripe_id=stripe_id,
            user_id=user_id,
            instance_id=instance_id,
        )
        return
    if status not in notices.BILLING_ALARMS:
        return
    if existing is not None and existing.get("status") == status:
        return
    email, domain = _who_and_where(conn, user_id=user_id, instance_id=instance_id)
    notify.raise_notice(
        conn,
        "billing.changed",
        subscription_id=stripe_id,
        status=status,
        email=email,
        domain=domain,
        since=incoming or "",
    )


def _who_and_where(conn: Conn, *, user_id: str, instance_id: str) -> tuple[Any, Any]:
    """The names, which are what make a billing notice worth receiving.

    Two single-row reads. "A subscription needs attention" without a who and a
    where is a message that sends somebody to a screen to find out what it meant.
    """
    from hub.repositories import instances as instances_repo
    from hub.repositories import users as users_repo

    user = users_repo.by_id(conn, user_id)
    instance = instances_repo.by_id(conn, instance_id)
    return (user or {}).get("email"), (instance or {}).get("display_name")


def _report_subscribed(
    conn: Conn,
    *,
    existing: dict[str, Any] | None,
    status: str,
    stripe_id: str,
    user_id: str,
    instance_id: str,
) -> None:
    """Tell the operator when somebody starts paying.

    THE OTHER HALF OF `_report_billing`, and a separate switch because it is a
    separate question. That one is "this needs a person"; this one is "the thing
    the hub exists to do just happened", and an operator who wants one may well
    not want the other.

    ONLY ON THE WAY IN. `past_due` is entitling -- the seat still reads while a
    card is retried -- and arriving at it is a failure `billing.changed` already
    reports, so it is excluded here rather than announced as a new subscriber.
    The hourly sweep re-applies every live subscription, so this fires only when
    the seat was not already paying, and the dedupe key finishes the job: a
    trial converting to `active` is the same person still paying, and saying it
    twice would make the number of subscribers this notice implies wrong.
    """
    if existing is not None and existing.get("status") in subs_repo.ENTITLING:
        return
    email, domain = _who_and_where(conn, user_id=user_id, instance_id=instance_id)
    notify.raise_notice(
        conn,
        "billing.subscribed",
        subscription_id=stripe_id,
        status=status,
        email=email,
        domain=domain,
    )


def _projection_changed(
    existing: dict[str, Any] | None,
    status: str,
    sub: dict[str, Any],
    notice: str | None,
    notice_at: Any,
) -> bool:
    """Whether any of the four fields `billing_state.for_seat` sends has moved.

    A NOTICE-ONLY change counts. Still entitled, now amber, is the case that
    exists entirely to be shown to somebody -- and if it did not bump, in-app
    dunning would silently never fire, which looks exactly like everything
    working.
    """
    if existing is None:
        return True
    return (
        existing.get("status") != status
        or existing.get("current_period_end") != _as_dt(_epoch(_period_end_of(sub)))
        or bool(existing.get("cancel_at_period_end")) != bool(sub.get("cancel_at_period_end"))
        or existing.get("notice") != notice
        or existing.get("notice_at") != _as_dt(_epoch(notice_at))
    )


def _as_dt(value: str | None):
    """An ISO string as psycopg would have handed it back, so the comparison
    above is like for like rather than str against datetime."""
    if value is None:
        return None
    from datetime import datetime

    return datetime.fromisoformat(value)


def _period_end_of(sub: dict[str, Any]) -> Any:
    """When the current period ends, across two API shapes.

    Stripe MOVED this onto the subscription item: `subscription.current_period_end`
    is null on recent versions and the value lives at
    `items.data[].current_period_end`. Reading only the top level yields None
    forever, which is not a cosmetic loss -- `entitled_through` is what a lapsed
    reader's archive ceiling is derived from, so a null one collapses to
    `UserRecord.NOTHING` and takes away every brief they paid for. That is ADR
    0211's promise, broken silently.

    A subscription can hold several items with different periods; the FURTHEST
    is the honest answer, because access should end when the last thing they
    paid for does.
    """
    top = sub.get("current_period_end")
    if top:
        return top
    ends = [
        item.get("current_period_end")
        for item in ((sub.get("items") or {}).get("data") or [])
        if item.get("current_period_end")
    ]
    return max(ends) if ends else None


def _price_of(sub: dict[str, Any]) -> dict[str, Any]:
    """What was bought, as the row records it.

    The amount and the interval as well as the id, so a surface can say what a
    reader is charged without a round trip to the Stripe Price that holds it.
    One dict so the four travel together and cannot half-arrive.
    """
    items = ((sub.get("items") or {}).get("data")) or []
    price = (items[0].get("price") or {}) if items else {}
    recurring = price.get("recurring") or {}
    return {
        "price_id": price.get("id"),
        "unit_amount": price.get("unit_amount"),
        "currency": price.get("currency"),
        "interval": recurring.get("interval"),
    }


def _subscription_of(invoice: dict[str, Any]) -> str | None:
    """Which subscription an invoice is for, across two API shapes.

    Stripe puts it in two different places depending on the API version an
    event was rendered under. `API_VERSION` pins Basil, which carries
    `invoice.parent.subscription_details.subscription`; versions before it carry
    a top-level `invoice.subscription`.

    Reading only one of them is not a partial failure. Every invoice handler
    returns early on a missing subscription, so `payment_failed`,
    `final_notice` and `action_required` would never fire and the whole in-app
    dunning layer would be silently dead.

    Both are read rather than only the pinned shape, because the pin is a
    constant somebody will eventually move, and an integration that breaks
    quietly on a version bump is the thing pinning exists to prevent.
    """
    parent = invoice.get("parent") or {}
    details = parent.get("subscription_details") or {}
    found = details.get("subscription") or invoice.get("subscription")
    if isinstance(found, dict):
        # Stripe returns either an id or the expanded object, depending on what
        # the event carried and what was expanded.
        found = found.get("id")
    return found or None


def _invoice_event(conn: Conn, invoice: dict[str, Any], *, event_type: str) -> bool:
    """Invoices decide what the reader is TOLD, never what they may read.

    Three outcomes worth distinguishing, because sending the wrong one costs
    money. `payment_action_required` is SCA: the card is fine and the bank wants
    a tap, so telling that person to update their card sends them to re-enter a
    card that works and they conclude the product is broken. A failed payment
    with retries left is `payment_failed`; the last retry is `final_notice`, and
    it is the only one that earns a modal.
    """
    stripe_id = _subscription_of(invoice)
    if not stripe_id:
        return False

    if event_type == "invoice.paid":
        notice, at = None, None
    elif event_type == "invoice.payment_action_required":
        notice, at = "action_required", invoice.get("due_date") or invoice.get("created")
    else:
        last = invoice.get("next_payment_attempt") in (None, 0)
        notice = "final_notice" if last else "payment_failed"
        at = invoice.get("next_payment_attempt") or invoice.get("period_end")

    # THE LOCK AND THE ORDERING, both, for the same reason the subscription
    # handler has them: invoices are a second timeline over one row. Stripe
    # redelivers for about three days, so a decline that could not be applied
    # when it happened can land after the `invoice.paid` that resolved it and
    # rewrite a cleared banner back to "your payment failed" -- persistent, and
    # undismissable -- on a subscription that is paid up.
    with conn.transaction():
        existing = subs_repo.lock_for_update(conn, str(stripe_id))
        if existing is None:
            # The subscription event has not landed yet, and this notice is the
            # only place `action_required` and `final_notice` ever come from --
            # the sweep recomputes from status alone and cannot reproduce
            # either. So REFUSE the event rather than dropping it: the claim
            # rolls back with this transaction and Stripe's own retry delivers
            # it again, by which time the subscription it belongs to exists.
            raise OutOfOrder(str(stripe_id))

        source = _epoch(invoice.get("created"))
        held = existing.get("notice_updated_at")
        if source is not None and held is not None:
            from datetime import datetime

            if datetime.fromisoformat(source) < held:
                log.info("discarding a stale %s for %s", event_type, stripe_id)
                return True

        subs_repo.set_notice(
            conn,
            stripe_subscription_id=str(stripe_id),
            notice=notice,
            notice_at=_epoch(at),
            source_at=source,
        )
        subs_repo.bump_entitlement(
            conn, user_id=existing["user_id"], instance_id=existing["instance_id"]
        )
    return True
