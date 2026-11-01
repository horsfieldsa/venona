"""Billing wire shapes.

Nothing here carries a Stripe secret, a customer id or a card detail. What the
app needs is what to show a reader, and a URL to send them to; the identifiers
stay on this side. `tests/hub/test_billing.py` greps responses for the secret,
in the shape `tests/test_notifications_api.py` uses.
"""

from __future__ import annotations

from pydantic import BaseModel


class CheckoutStarted(BaseModel):
    """Where to send the browser. A URL, minted per click and short-lived."""

    url: str


class BillingState(BaseModel):
    """What a reader is entitled to, and what to tell them about it.

    `notice` is a KIND, never a sentence: the words live in
    `web/src/lib/billing.ts` and nowhere else, for the reason CLAUDE.md gives
    about run outcomes -- four surfaces once decided the wording of a run
    independently and one of them was wrong about the world.
    """

    status: str  # none | trialing | active | past_due | lapsed | comped
    entitled: bool
    entitled_through: str | None = None
    notice: str | None = None
    notice_at: str | None = None
    cancel_at_period_end: bool = False
    price_monthly: str | None = None
    price_annual: str | None = None


class WebhookAck(BaseModel):
    """Stripe only reads the status code. This exists so the route can declare a
    response_model like every other JSON route (tests/hub/test_hub_route_contracts.py)
    without the body becoming something a caller might start parsing."""

    received: bool
