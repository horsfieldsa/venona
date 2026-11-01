"""Subscribing, managing a subscription, and hearing back from Stripe.

Three of these four routes are TOP-LEVEL NAVIGATIONS that end in a 303, not
JSON. The reader is in an instance's app on a different origin, so a fetch
would need that origin on the hub's CORS allowance -- and the hub grants those
per route, never app-wide, so that an issuer running on a session cookie does
not become cross-origin readable (`routers/portal.py::_allow_origin`). A link
needs none of that, and is how "Log in" already works from a separate front site.

The fourth is Stripe talking to us, authenticated by a signature over the raw
body and nothing else.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib.parse import quote

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from hub import billing_apply, stripe, stripe_webhook
from hub.db.core import Conn
from hub.deps import get_db, session_email
from hub.repositories import billing_events as events_repo
from hub.repositories import instances as instances_repo
from hub.repositories import settings as settings_repo
from hub.repositories import subscriptions as subs_repo
from hub.repositories import users as users_repo
from hub.schemas.billing import WebhookAck

log = logging.getLogger("hub.billing")

router = APIRouter(prefix="/billing", tags=["billing"])


def _stripe_guard(fn):
    """Turn a Stripe refusal into an answer, never a reference code.

    Every route here ends in a button somebody pressed, and an unhandled
    `StripeError` becomes "Something went wrong. Reference: 5b6945e4"
    (`errors.py`) -- which tells the reader nothing and the operator nothing
    until they find the log, at the moment money is involved.

    502 rather than 500: the failure is upstream, and saying so is the
    difference between "we are broken" and "our payment provider refused this".
    The detail stays generic because Stripe's own message can name internal ids.
    """
    from functools import wraps

    @wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except stripe.StripeError as exc:
            log.error("stripe refused: %s", exc)
            raise HTTPException(
                502, detail="our payment provider could not complete that just now"
            ) from exc

    return wrapper


def _settings(request: Request):
    return request.app.state.settings


def _require_billing(request: Request):
    settings = _settings(request)
    if not settings.billing_enabled:
        # 503, not 500: correctly configured for a deployment that does not take
        # money, which is every local hub and every instance before launch.
        raise HTTPException(503, detail="billing is not configured on this hub")
    return settings


def _signed_in_user(request: Request, conn: Conn) -> dict[str, Any]:
    email = session_email(request, conn)
    if email is None:
        raise HTTPException(401, detail="sign in first")
    user = users_repo.by_email(conn, email)
    if user is None or user["status"] != "active":
        # A session with no row is normal here (the request-access page exists
        # for exactly that visitor), but it cannot buy anything: there is no
        # membership for a subscription to be about.
        raise HTTPException(403, detail="this account cannot subscribe")
    return user


def _seat(
    conn: Conn, user: dict[str, Any], slug: str | None, client_id: str | None = None
) -> dict[str, Any]:
    """Which instance is being paid for.

    A subscription buys a seat on ONE instance (ADR 0209), so this has to be
    unambiguous. One membership needs no choice; more than one does, and
    guessing would sell somebody the wrong thing.
    """
    memberships = users_repo.seats_for(conn, user["id"])
    if not memberships:
        # NOT YET A MEMBER, and with self-serve on that is the ordinary way in:
        # the gallery sends somebody here to buy a seat they do not have, and
        # the membership is granted when the payment lands rather than when they
        # click. Naming the domain is required, because there is no membership
        # to infer it from.
        if slug and settings_repo.self_serve(conn):
            public = {row["slug"] for row in instances_repo.list_public(conn)}
            if slug in public:
                row = instances_repo.by_slug(conn, slug)
                if row is not None:
                    return row
        raise HTTPException(403, detail="no access has been granted to this account yet")
    if client_id:
        # The instance names itself by the client id THIS hub minted for it, so
        # the two sides cannot disagree. A slug could, and did: the hub's
        # `instances.slug` and an instance's own domain slug are separate values
        # in separate databases that nothing keeps in step.
        instance = instances_repo.by_client_id(conn, client_id)
        if instance is not None:
            for row in memberships:
                if row["id"] == instance["id"]:
                    return row
        raise HTTPException(404, detail="no access to that instance on this account")
    if slug:
        for row in memberships:
            if row["slug"] == slug:
                return row
        raise HTTPException(404, detail=f"no access to {slug!r} on this account")
    if len(memberships) > 1:
        raise HTTPException(400, detail="say which subscription this is for")
    return memberships[0]


async def _customer_id(settings, conn: Conn, user: dict[str, Any]) -> str:
    """The person's Stripe customer, created once and reused forever.

    Never a second customer for the same person: a resubscriber must land on
    the card and the invoice history they already have, and two customers for
    one email is the state nobody can reconcile afterwards. The idempotency key
    is keyed on the user id, so a double-click cannot make two either.
    """
    if user["stripe_customer_id"]:
        return user["stripe_customer_id"]
    customer = await stripe.create_customer(
        settings.stripe_secret_key,
        email=user["email"],
        name=user["display_name"],
        user_id=user["id"],
        idempotency_key=f"customer:{user['id']}",
    )
    subs_repo.set_stripe_customer(conn, user_id=user["id"], customer_id=customer["id"])
    # Re-read rather than trusting the write: `set_stripe_customer` only fills a
    # NULL, so if a concurrent request won the race the stored id is the one to
    # use and this one is an orphan we must not start billing against.
    fresh = users_repo.by_id(conn, user["id"])
    return (fresh or {}).get("stripe_customer_id") or customer["id"]


def _to_portal(settings, client: str | None) -> RedirectResponse:
    """Send somebody who already has this seat to manage it instead.

    `client` is carried through: without it a reader with memberships on two
    domains lands on a chooser that cannot tell which one they came from, and
    the portal route answers 400.
    """
    url = f"{settings.public_url}/billing/portal"
    if client:
        url += f"?client={quote(client, safe='')}"
    return RedirectResponse(url, status_code=303)


async def _already_subscribed(settings, customer_id: str, instance_id: str) -> bool:
    """Whether Stripe itself already holds a live subscription for this seat.

    Asked because the local table is webhook-populated and therefore behind.
    A failure to reach Stripe answers False: `_stripe_guard` turns a transport
    error into a 502, and refusing to sell to somebody because a GET timed out
    is the wrong way to be careful.
    """
    page = await stripe.list_subscriptions(settings.stripe_secret_key, customer=customer_id)
    for sub in page.get("data") or []:
        if sub.get("status") not in subs_repo.ENTITLING:
            continue
        if (sub.get("metadata") or {}).get("instance_id") == instance_id:
            return True
    return False


@router.get("/checkout")
@_stripe_guard
async def checkout(
    request: Request,
    plan: str = "annual",
    instance: str | None = None,
    client: str | None = None,
    conn: Conn = Depends(get_db),
) -> RedirectResponse:
    """Start a subscription. A navigation, so the reply is a 303 to Stripe.

    `plan` defaults to annual, the primary button on both surfaces that sell
    (`BillingView.vue` and the hub's domain gallery); a default disagreeing
    with them is a quiet way to sell a hand-made link the wrong thing.
    """
    settings = _require_billing(request)
    user = _signed_in_user(request, conn)
    seat = _seat(conn, user, instance, client)

    # THE SEAT'S OWN PRICES, not a hub-wide pair. One product for every domain
    # does not survive the second one: Stripe's portal lists a customer's
    # subscriptions by product name, so two domains sharing a product are
    # indistinguishable there (the hub's migration 0006).
    prices = {
        "monthly": seat["stripe_price_monthly"],
        "annual": seat["stripe_price_annual"],
    }
    if plan not in prices:
        raise HTTPException(404, detail=f"no such plan {plan!r}")
    price_id = prices[plan]
    if not price_id:
        # An instance nobody has priced yet. A refusal rather than a fallback:
        # falling back would sell them somebody else's product.
        raise HTTPException(409, detail="this domain is not taking subscriptions yet")

    # REFUSED, not silently allowed. Two tabs, or a return to the pricing page
    # while already subscribed, would otherwise create a second subscription and
    # bill one person twice for one seat.
    #
    # `ux_subscriptions_live` does NOT catch it: the partial index rejects the
    # second subscription's webhook, which converts a double charge into a
    # permanent inconsistency and an event Stripe retries and then abandons.
    # This has to be caught before Stripe takes a card.
    live = subs_repo.live_for(conn, user_id=user["id"], instance_id=seat["id"])
    if live is not None:
        return _to_portal(settings, client)

    customer_id = await _customer_id(settings, conn, user)

    # AND THEN ASK STRIPE, because the check above reads a table that webhooks
    # populate. Between a completed checkout and its first delivered event the
    # local answer is "not subscribed", which is exactly the window a second tab
    # sits in. One GET, on a path somebody is about to be charged on.
    if await _already_subscribed(settings, customer_id, seat["id"]):
        return _to_portal(settings, client)
    base = seat["public_url"].rstrip("/")
    # THE PICKER, ALWAYS. Checkout's own back arrow lands on `cancel_url`, and
    # the domain being bought is the one place a cancelling buyer may not be
    # able to go: somebody buying their FIRST seat holds no membership there,
    # so the instance answers with its sign-in page -- which reads as the
    # purchase having signed them out, on the screen where that alarms most.
    # The hub's front door works for everybody, and answers a signed-in reader
    # with the chooser.
    session = await stripe.create_checkout_session(
        settings.stripe_secret_key,
        customer_id=customer_id,
        price_id=price_id,
        success_url=f"{settings.public_url}/billing/return?to={base}&seat={seat['slug']}",
        cancel_url=settings.public_url,
        user_id=user["id"],
        instance_id=seat["id"],
        slug=seat["slug"],
        description=seat["display_name"],
        trial_days=settings.trial_days,
        # THE PLAN IS PART OF THE KEY, because monthly and annual are different
        # REQUESTS rather than one intent asked twice. Without it, whichever
        # button somebody pressed first pinned the key to that price and every
        # later press of the other one answered Stripe's "keys for idempotent
        # requests can only be used with the same parameters" -- a 400, an error
        # page, and no way to change your mind for the 24 hours the key lives.
        # This key covers a double CLICK; two subscriptions for one seat are
        # what the two checks above cover.
        idempotency_key=f"checkout:{user['id']}:{seat['id']}:{plan}",
    )
    return RedirectResponse(session["url"], status_code=303)


@router.get("/portal")
@_stripe_guard
async def portal(
    request: Request,
    flow: str | None = None,
    instance: str | None = None,
    client: str | None = None,
    conn: Conn = Depends(get_db),
) -> RedirectResponse:
    """Subscription management, entire. Stripe's page, not ours.

    `flow` is what makes in-app dunning work: a banner that says "update your
    card" lands the reader ON the card form rather than on a portal home page,
    and most of the people who were just persuaded do not survive an extra
    navigation.
    """
    settings = _require_billing(request)
    user = _signed_in_user(request, conn)
    if not user["stripe_customer_id"]:
        # Nothing to manage yet. Send them to buy rather than to a portal that
        # would 400 on a customer that does not exist.
        raise HTTPException(409, detail="no subscription to manage yet")
    seat = _seat(conn, user, instance, client)
    live = subs_repo.live_for(conn, user_id=user["id"], instance_id=seat["id"])

    # A flow that ACTS ON a subscription needs one, and needs it to belong to
    # the customer the portal is being opened for. Stripe refuses both cases
    # with a 400 -- "The session's customer does not match the subscription's
    # customer" -- and a reader who clicked Change plan should not be reading
    # that, or the reference code an unhandled failure turns it into.
    if flow in ("subscription_update", "subscription_cancel"):
        if live is None:
            raise HTTPException(409, detail="there is no subscription to change yet")
        if live["stripe_subscription_id"] and user["stripe_customer_id"]:
            owner = await stripe.get_subscription(
                settings.stripe_secret_key, live["stripe_subscription_id"]
            )
            if owner.get("customer") != user["stripe_customer_id"]:
                # The stored customer and the subscription's have diverged.
                # `set_stripe_customer` fills a NULL and never overwrites, so
                # this cannot fix itself; it needs a person.
                log.error(
                    "customer mismatch for %s: user has %s, subscription %s is on %s",
                    user["email"],
                    user["stripe_customer_id"],
                    live["stripe_subscription_id"],
                    owner.get("customer"),
                )
                raise HTTPException(409, detail="this subscription needs attention from us")

    base = seat["public_url"].rstrip("/")
    session = await stripe.create_portal_session(
        settings.stripe_secret_key,
        customer_id=user["stripe_customer_id"],
        return_url=base,
        flow=flow,
        subscription_id=(live or {}).get("stripe_subscription_id"),
        configuration=settings.stripe_portal_configuration or None,
    )
    return RedirectResponse(session["url"], status_code=303)


@router.get("/return")
async def checkout_return(
    request: Request, to: str = "", seat: str = "", conn: Conn = Depends(get_db)
) -> RedirectResponse:
    """Where Checkout sends the browser back.

    **This is not proof of payment and nothing is fulfilled here.** A reader can
    close the tab before it loads, and the URL can be visited directly by
    anybody; the subscription becomes real when
    `checkout.session.completed` arrives on the webhook. All this does is put
    them back where they started.
    """
    settings = _settings(request)

    # THE ONE THING THIS DOES DO, and it is not fulfilment: it makes sure the
    # person can get in. Self-serve grants the seat when the payment lands
    # (`billing_apply._grant_the_seat_paid_for`), which is a webhook, and a
    # webhook that is thirty seconds late would land somebody who has just paid
    # on a domain that does not know them. `seat` names the instance in the
    # return URL, the grant is idempotent, and whichever arrives first wins.
    # The membership alone entitles nobody: `entitlement_status`, the field the
    # poller pushes to the instance, stays whatever Stripe says.
    email = session_email(request, conn)
    user = users_repo.by_email(conn, email) if email else None
    if user is not None and seat:
        instance = instances_repo.by_slug(conn, seat)
        if instance is not None and settings_repo.self_serve(conn):
            billing_apply._grant_the_seat_paid_for(
                conn, user_id=user["id"], instance_id=instance["id"]
            )

    # Only an instance THIS HUB knows. `to` arrives in a query string, so
    # echoing it into a Location header unchecked is an open redirect -- and an
    # identity provider is the worst place in the system to have one, because
    # it is the origin every instance trusts.
    known = {row["public_url"].rstrip("/") for row in instances_repo.list_pollable(conn)}
    target = to.rstrip("/")
    return RedirectResponse(target if target in known else settings.public_url, status_code=303)


# Deliberately NOT rate limited. The hub's limiter is opt-in per route
# (`ratelimit.allow`, which only `portal.py`'s request-access calls), so this
# needs no exemption -- but it must never GROW one. Stripe delivers in bursts, a
# renewal batch is dozens of events from a handful of addresses, and the bucket
# is `ratelimit.CAPACITY`, refilling at `REFILL_PER_SECOND`. A throttled event is
# retried, so the damage would read as subscriptions randomly not applying rather
# than as a limiter. The signature check authenticates here.
@router.post("/webhook", response_model=WebhookAck)
async def webhook(request: Request, conn: Conn = Depends(get_db)) -> WebhookAck:
    """Stripe, talking to us. Unauthenticated by session and authenticated by
    signature over the RAW body.

    Answers 2xx fast and does no outbound work: Stripe allows about twenty
    seconds and retries on timeout, so pushing to an instance from here would
    turn one slow instance into duplicate deliveries of every event. The push is
    the poller's sweep, which exists for that reason.
    """
    settings = _require_billing(request)
    # BEFORE any parsing. The signature covers bytes, and a JSON round-trip
    # changes whitespace and key order.
    payload = await request.body()
    try:
        stripe_webhook.verify(
            payload,
            request.headers.get("stripe-signature", ""),
            settings.stripe_webhook_secret,
            now=time.time(),
        )
    except stripe_webhook.SignatureError as exc:
        raise HTTPException(400, detail=f"bad signature: {exc}") from None

    try:
        event = json.loads(payload)
    except ValueError:
        raise HTTPException(400, detail="body is not JSON") from None

    event_id = event.get("id") or ""
    event_type = event.get("type") or ""
    if not event_id:
        raise HTTPException(400, detail="event has no id")

    # THE CLAIM AND THE WORK ARE ONE TRANSACTION, and that is the whole design
    # rather than tidiness. The hub's connection is `autocommit=True`
    # (`db/core.py`), so a bare `claim` commits on the spot -- and anything that
    # stops this process between there and `mark_processed` (a SIGTERM during a
    # deploy, a worker timeout, an OOM kill) leaves the row claimed with the work
    # never done. Stripe's retries then find it, take it for a replay, and
    # answer 200. For a `customer.subscription.created` that means a charged
    # card, a live subscription in Stripe, and no row here: the paying customer
    # is shown the subscribe screen, permanently.
    #
    # Inside a transaction the claim only exists if the work did. A hard stop
    # rolls both back and the next retry starts clean, which is why there is no
    # release-on-exception path -- one could only cover the failures Python is
    # still running to see.
    try:
        with conn.transaction():
            # Stripe retries until it gets a 2xx, so a redelivery is routine;
            # answering an error would make it retry forever.
            if not events_repo.claim(
                conn,
                event_id=event_id,
                event_type=event_type,
                payload_digest=stripe_webhook.digest(payload),
            ):
                return WebhookAck(received=True)
            handled = _apply(conn, event)
            events_repo.mark_processed(
                conn, event_id=event_id, status="processed" if handled else "ignored"
            )
    except billing_apply.OutOfOrder as exc:
        # An invoice that outran the subscription it belongs to. The claim rolled
        # back with this transaction, so Stripe's next retry takes the event
        # again -- and by then the subscription event has landed. Info, not an
        # error: nothing is wrong and nothing needs a person.
        log.info("stripe event %s arrived before subscription %s", event_id, exc)
        raise HTTPException(500, detail="waiting for an earlier event") from exc
    except psycopg.errors.UniqueViolation as exc:
        # Two live subscriptions for one seat. USUALLY a resubscribe whose
        # `created` arrived ahead of the cancellation for the subscription it
        # replaces, which the retry resolves once the cancellation lands.
        #
        # 500 rather than a 4xx, because the status is what makes Stripe
        # redeliver and this MUST be redelivered.
        #
        # WARNING RATHER THAN INFO, because the other cause is a genuine double
        # subscription -- one person, one seat, two cards charged -- and that
        # one never resolves: Stripe retries until it gives up, and the second
        # subscription is then simply not recorded here. Two guards on
        # `/checkout` are what stop it happening, so one of these repeating for
        # the same event over hours is a person's money and needs somebody.
        log.warning(
            "stripe event %s (%s) collided with the live subscription for its seat",
            event_id,
            event_type,
        )
        raise HTTPException(500, detail="waiting for an earlier event") from exc
    except Exception as exc:  # noqa: BLE001 - a failure here must be retryable
        log.exception("stripe event %s (%s) failed", event_id, event_type)
        raise HTTPException(500, detail="could not apply the event") from exc

    return WebhookAck(received=True)


def _apply(conn: Conn, event: dict[str, Any]) -> bool:
    """Write what one event says. Returns whether we cared about it.

    Deliberately narrow: `customer.subscription.*` is the source of truth for
    entitlement and the invoice events only contribute a NOTICE. An integration
    that derives access from invoices ends up with two answers to one question.
    """
    return billing_apply.apply_event(conn, event)
