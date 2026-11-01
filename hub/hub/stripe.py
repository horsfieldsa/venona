"""Outbound calls to Stripe. Five endpoints, hand-rolled (ADR 0210).

Not the `stripe` package: it carries its own HTTP transport, which
`scripts/lint_determinism.py` matches on source text and therefore cannot see,
and the offline suite is hermetic only because nothing in it can construct a
live client. Same seam as `google.EXCHANGE_TRANSPORT` and
`mgmt_client.MGMT_TRANSPORT`, so there is one pattern for every outbound call
the hub makes. `docs/pm/NOT-DOING.md` carries the measurement.
"""

from __future__ import annotations

import hashlib
from typing import Any

import httpx

BASE = "https://api.stripe.com"
TIMEOUT = 20.0

#: PINNED, and pinned again on the webhook endpoint in the dashboard. Stripe
#: upgrades an account's default version on its own schedule, and an unpinned
#: integration therefore gets new payload SHAPES with no deploy on our side --
#: the failure arrives as a KeyError in a webhook handler on a Tuesday. Moving
#: this is a deliberate act: read the changelog between the two versions, move
#: the dashboard endpoint to match, and say here when and against what.
#:
#: Last moved 2026-09-08, the version this integration was written against.
API_VERSION = "2025-08-27.basil"

#: Test seam. None means the real network.
STRIPE_TRANSPORT: httpx.AsyncBaseTransport | None = None


class StripeError(RuntimeError):
    """A Stripe call failed. The message is operator-safe and carries no key."""


def _client(secret_key: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=BASE,
        timeout=TIMEOUT,
        trust_env=False,
        transport=STRIPE_TRANSPORT,
        auth=(secret_key, ""),
        headers={"Stripe-Version": API_VERSION},
    )


def _form(payload: dict[str, Any], prefix: str = "") -> dict[str, str]:
    """Stripe takes form encoding with bracketed paths, not JSON.

    `{"subscription_data": {"trial_period_days": 3}}` has to go out as
    `subscription_data[trial_period_days]=3`. Booleans are `true`/`false`, not
    `True`/`False`, and None is omitted rather than sent empty -- an empty
    string is a value to Stripe and would clear a field.

    Returns a DICT rather than a list of pairs, and that is not cosmetic: httpx
    builds a SYNC byte stream from a list, and an `AsyncClient` then refuses to
    send it ("Attempted to send an sync request with an AsyncClient instance").
    Every bracketed path this produces is unique, so a mapping loses nothing.
    """
    out: dict[str, str] = {}
    for key, value in payload.items():
        path = f"{prefix}[{key}]" if prefix else key
        if value is None:
            continue
        if isinstance(value, dict):
            out.update(_form(value, path))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, dict):
                    out.update(_form(item, f"{path}[{index}]"))
                else:
                    out[f"{path}[{index}]"] = str(item)
        elif isinstance(value, bool):
            out[path] = "true" if value else "false"
        else:
            out[path] = str(value)
    return out


def _scoped_key(key: str, payload: dict[str, Any] | None) -> str:
    """A caller's idempotency key, narrowed to the exact request it was used for.

    STRIPE COMPARES PARAMETERS, NOT INTENT. Reusing a key with anything
    different answers `Keys for idempotent requests can only be used with the
    same parameters they were first used with` -- a 400, for the twenty-four
    hours the key lives. So a key naming only who and what (`checkout:{user}:
    {seat}:{plan}`) breaks for every reader who clicked in the last day the
    moment any field of that request changes, and a deploy is exactly such a
    moment: changing `cancel_url` took the whole paywall down that way.

    The digest is over the FORM ENCODING, which is what goes on the wire and
    what Stripe compares. Sorted, so a dict's order cannot change the key
    without the request changing.

    This does not weaken the protection the key exists for. A double click
    sends identical parameters, so it produces an identical key and still gets
    one session. What it stops is a key outliving the request it described.
    """
    if not payload:
        return key
    body = _form(payload)
    digest = hashlib.sha256(
        "\n".join(f"{name}={body[name]}" for name in sorted(body)).encode()
    ).hexdigest()[:12]
    return f"{key}:{digest}"


async def _call(
    secret_key: str,
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    headers = {}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = _scoped_key(idempotency_key, payload)
    try:
        async with _client(secret_key) as client:
            response = await client.request(
                method,
                path,
                data=_form(payload or {}) if payload else None,
                headers=headers,
            )
    except httpx.HTTPError as exc:
        # A TIMEOUT OR A RESET IS A STRIPE FAILURE TOO. Every caller sits behind
        # a decorator that catches `StripeError` and answers 502 with words a
        # reader can act on; an httpx exception walks straight past it to the
        # error middleware, and the person who just clicked Subscribe gets a
        # reference code. The class of failure the decorator exists for.
        #
        # `exc` rather than the URL: the message reaches an operator log, and
        # the path can carry a customer id.
        raise StripeError(f"could not reach Stripe: {type(exc).__name__}") from exc
    try:
        body = response.json()
    except ValueError:
        raise StripeError(f"Stripe answered {response.status_code} with a non-JSON body") from None
    if response.status_code >= 400:
        # Stripe's own message is safe to surface: it describes the request we
        # made, never the key we made it with.
        detail = (body.get("error") or {}).get("message", "no message")
        raise StripeError(f"Stripe answered {response.status_code}: {detail}")
    return body


# ── the five ─────────────────────────────────────────────────────────


async def create_customer(
    secret_key: str, *, email: str, name: str | None, user_id: str, idempotency_key: str
) -> dict[str, Any]:
    """`metadata[user_id]` so a customer-level event can be attributed without a
    lookup table, and so a person staring at the Stripe dashboard can tell who
    this is."""
    return await _call(
        secret_key,
        "POST",
        "/v1/customers",
        payload={"email": email, "name": name, "metadata": {"user_id": user_id}},
        idempotency_key=idempotency_key,
    )


async def create_product(
    secret_key: str, *, name: str, description: str, instance_id: str, slug: str
) -> dict[str, Any]:
    """One product per INSTANCE, named after it.

    Stripe's customer portal lists a customer's subscriptions by product name,
    and a reader subscribed to two domains has to be able to tell them apart
    before cancelling one. The name is what they read there and on the invoice
    they expense, so it carries the domain rather than the company.

    `metadata[slug]` beside the id because an operator reading the Stripe
    dashboard should not have to resolve `inst:06a8...` against another system
    to know which domain they are looking at.
    """
    return await _call(
        secret_key,
        "POST",
        "/v1/products",
        payload={
            "name": name,
            "description": description,
            "metadata": {"app": "Venona", "instance_id": instance_id, "slug": slug},
        },
        idempotency_key=f"product:{instance_id}",
    )


async def create_price(
    secret_key: str,
    *,
    product_id: str,
    unit_amount: int,
    interval: str,
    currency: str,
    nickname: str,
    instance_id: str,
    slug: str,
) -> dict[str, Any]:
    """A recurring price on that product. `nickname` shows in the dashboard
    only; the portal reads the product's name."""
    return await _call(
        secret_key,
        "POST",
        "/v1/prices",
        payload={
            "product": product_id,
            "unit_amount": unit_amount,
            "currency": currency,
            "recurring": {"interval": interval},
            "nickname": nickname,
            "metadata": {"app": "Venona", "instance_id": instance_id, "slug": slug},
        },
        idempotency_key=f"price:{instance_id}:{interval}:{unit_amount}:{currency}",
    )


async def get_customer(secret_key: str, customer_id: str) -> dict[str, Any]:
    return await _call(secret_key, "GET", f"/v1/customers/{customer_id}")


async def create_checkout_session(
    secret_key: str,
    *,
    customer_id: str,
    price_id: str,
    success_url: str,
    cancel_url: str,
    user_id: str,
    instance_id: str,
    slug: str,
    description: str,
    trial_days: int,
    idempotency_key: str,
) -> dict[str, Any]:
    """The subscribe flow. Three parameters here are load-bearing.

    `payment_method_collection=always` -- without it Stripe's `if_required`
    SKIPS card collection on a trialing subscription, silently handing back the
    no-card trial ADR 0212 exists to avoid.

    `trial_settings[end_behavior][missing_payment_method]=cancel` -- so a trial
    with no usable card ends rather than sitting in a state nobody reads.

    `metadata` on BOTH the session and the subscription. The session's carries
    `checkout.session.completed`; the subscription's carries every
    `customer.subscription.*` event afterwards, which is where entitlement
    actually comes from. Setting only the first leaves every later event
    unattributable.
    """
    # `slug` alongside the ids: every one of these lands in the Stripe
    # dashboard, where an operator reading a subscription should not have to
    # resolve `inst:06a8...` against another system to know which domain it is.
    metadata = {"user_id": user_id, "instance_id": instance_id, "slug": slug}
    return await _call(
        secret_key,
        "POST",
        "/v1/checkout/sessions",
        payload={
            "mode": "subscription",
            "customer": customer_id,
            "line_items": [{"price": price_id, "quantity": 1}],
            "success_url": success_url,
            "cancel_url": cancel_url,
            "payment_method_collection": "always",
            "automatic_tax": {"enabled": True},
            "customer_update": {"address": "auto", "name": "auto"},
            "tax_id_collection": {"enabled": True},
            "metadata": metadata,
            "subscription_data": {
                # SHOWS ON THE INVOICE, which is the document a business buyer
                # expenses. "Venona" twice on one invoice is unreadable; the
                # domain is the thing that distinguishes them.
                "description": description,
                "trial_period_days": trial_days,
                "trial_settings": {"end_behavior": {"missing_payment_method": "cancel"}},
                "metadata": metadata,
            },
        },
        idempotency_key=idempotency_key,
    )


#: Portal deep links. A dunning banner that lands somebody on a portal HOME page
#: has lost most of the people it just persuaded; these put them on the form.
PORTAL_FLOWS = ("payment_method_update", "subscription_cancel", "subscription_update")


async def create_portal_session(
    secret_key: str,
    *,
    customer_id: str,
    return_url: str,
    flow: str | None = None,
    subscription_id: str | None = None,
    configuration: str | None = None,
) -> dict[str, Any]:
    """Subscription management, entire. Deliberately not reimplemented.

    Sessions are short-lived, so this is minted per click and never cached.
    `subscription_update` additionally needs the subscription it is updating,
    which is why that argument exists.

    `configuration` pins WHICH portal the reader sees. Left unset, Stripe uses
    the account default -- fine on a dedicated account, and on a shared one that
    is somebody else's configuration, mutable by them.
    """
    payload: dict[str, Any] = {"customer": customer_id, "return_url": return_url}
    if configuration:
        payload["configuration"] = configuration
    if flow in PORTAL_FLOWS:
        payload["flow_data"] = {"type": flow}
        if flow in ("subscription_cancel", "subscription_update") and subscription_id:
            payload["flow_data"][flow] = {"subscription": subscription_id}
    return await _call(secret_key, "POST", "/v1/billing_portal/sessions", payload=payload)


async def get_subscription(secret_key: str, subscription_id: str) -> dict[str, Any]:
    return await _call(secret_key, "GET", f"/v1/subscriptions/{subscription_id}")


async def get_price(secret_key: str, price_id: str) -> dict[str, Any]:
    """One Price, to check the amount we quote is still the amount we charge.

    The gallery renders `instances.price_monthly_amount`, and Checkout charges
    whatever the Price ID says -- two values that agree only because one command
    wrote both. Somebody editing the price in Stripe's dashboard breaks that
    silently, and the visible half is the one a stranger reads before deciding.
    """
    return await _call(secret_key, "GET", f"/v1/prices/{price_id}")


async def list_subscriptions(
    secret_key: str,
    *,
    customer: str | None = None,
    starting_after: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Reconciliation (`poller.py`). Stripe retries a failed webhook for about
    three days and then stops, so an outage longer than that loses events
    permanently and we would keep an entitlement we have no right to. Event
    delivery is a fast path, not a source of truth."""
    query = f"?limit={limit}&status=all"
    if customer:
        # Scoped to one person, for the checkout guard: local state is
        # webhook-populated, so it cannot answer "are they already subscribed"
        # in the seconds between a completed checkout and its first event.
        query += f"&customer={customer}"
    if starting_after:
        query += f"&starting_after={starting_after}"
    return await _call(secret_key, "GET", f"/v1/subscriptions{query}")
