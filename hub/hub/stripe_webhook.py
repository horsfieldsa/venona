"""Stripe webhook signature verification, and the entitlement it implies.

Verification is stdlib `hmac` -- the same two calls the SDK makes -- over the
RAW request body. Not the parsed body, not a re-serialisation of it: JSON
round-tripping changes whitespace and key order, and the signature is over
bytes. Any route using this must read `await request.body()` before parsing.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

#: Stripe's own tolerance. Rejecting outside it is what stops a captured request
#: being replayed indefinitely -- the event ledger stops a replay being APPLIED
#: twice, but this stops it being accepted at all.
TOLERANCE_SECONDS = 300


class SignatureError(ValueError):
    """The request did not come from Stripe, or came too long ago."""


def _is_hex(value: str) -> bool:
    """A v1 signature is 64 lowercase hex characters and nothing else.

    Refusing anything else BEFORE the comparison is what keeps a hostile header
    from reaching code that can raise on it. This route is unauthenticated by
    construction -- the signature IS the authentication -- so every value here
    is attacker-controlled and an exception is a 500 anybody can trigger.
    """
    return len(value) == 64 and all(c in "0123456789abcdefABCDEF" for c in value)


def _parse_header(header: str) -> tuple[int, list[str]]:
    """`t=1614556800,v1=deadbeef,v1=...` -- more than one `v1` when a secret is
    being rotated, which is exactly when this must not break."""
    timestamp = -1
    signatures: list[str] = []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError:
                raise SignatureError("malformed timestamp in Stripe-Signature") from None
        elif key == "v1":
            signatures.append(value)
    if timestamp < 0 or not signatures:
        raise SignatureError("Stripe-Signature is missing a timestamp or a v1 signature")
    return timestamp, signatures


def verify(payload: bytes, header: str, secret: str, *, now: float) -> None:
    """Raise unless `payload` carries a valid, fresh signature.

    `now` is a parameter rather than a clock read so this is testable without
    freezing time globally, and so the caller -- which is a route, the layer
    allowed to know the time -- owns the fact.
    """
    if not header:
        raise SignatureError("no Stripe-Signature header")
    timestamp, signatures = _parse_header(header)
    if abs(now - timestamp) > TOLERANCE_SECONDS:
        raise SignatureError("Stripe-Signature timestamp is outside the tolerance")
    signed = f"{timestamp}.".encode() + payload
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    # compare_digest against EVERY candidate, and never short-circuit on the
    # first mismatch: `any(...)` over a generator would still be constant-time
    # per comparison, but writing it as a loop keeps the property obvious.
    #
    # ENCODED FIRST, because `compare_digest` raises TypeError on a str holding
    # any non-ASCII character -- and that is a caller-controlled value on an
    # UNAUTHENTICATED route, so it escapes as a 500 rather than a refusal.
    # Comparing bytes has no such rule, and `latin-1` cannot fail on any str.
    # (`_is_hex` refuses the same input earlier; both are here because either
    # alone would be one edit away from reopening it.)
    ok = False
    for candidate in signatures:
        if not _is_hex(candidate):
            continue
        if hmac.compare_digest(expected.encode(), candidate.encode("latin-1")):
            ok = True
    if not ok:
        raise SignatureError("Stripe-Signature does not match")


def digest(payload: bytes) -> str:
    """What goes in `stripe_events.payload_digest`: enough to tell a redelivery
    from a resend of different content, without keeping customer data."""
    return hashlib.sha256(payload).hexdigest()


# ── what an event means for entitlement ──────────────────────────────


#: Statuses Stripe can put a subscription in. `incomplete_expired` is terminal
#: and means the first payment never succeeded -- they never became a customer,
#: so they are NEVER-STARTED, not lapsed, and must not inherit a frozen archive
#: they never paid for.
NEVER_STARTED = ("incomplete", "incomplete_expired")

#: The notice kinds. The wire carries the KIND; `web/src/lib/billing.ts` is the
#: only place one becomes a sentence. CLAUDE.md's rule about run outcomes,
#: applied to money: a banner assembled from a status at a render site is the
#: same bug with a bill attached.
NOTICE_KINDS = (
    "trial_active",
    "trial_ending",
    "action_required",
    "payment_failed",
    "final_notice",
    "canceling",
    "lapsed",
)


def entitlement_for(subscription: dict[str, Any] | None) -> dict[str, Any]:
    """The one predicate, derived from the Stripe row and nothing else.

    `past_due` entitles: that is Stripe still retrying a card, and cutting
    somebody off at the first decline turns a recoverable lapse into a
    cancellation. `unpaid` does not -- Stripe has given up by then.
    """
    if subscription is None:
        return {"status": "none", "through": None}
    status = subscription.get("status") or "none"
    if status in NEVER_STARTED:
        return {"status": "none", "through": None}
    # Top level OR the item, for the reason `billing_apply._period_end_of`
    # spells out: Stripe moved it, and a null here takes a lapsed reader's
    # whole archive away.
    through = subscription.get("current_period_end") or _item_period_end(subscription)
    if status in ("trialing", "active", "past_due"):
        return {"status": status, "through": through}
    # Terminal: canceled or unpaid. They keep the archive up to what they paid
    # for, which is the period end that has already passed (ADR 0211).
    return {"status": "lapsed", "through": through}


#: A day. `trial_active` becomes `trial_ending` inside this window.
TRIAL_ENDING_WINDOW = 24 * 60 * 60


def _item_period_end(sub: dict[str, Any]) -> Any:
    ends = [
        item.get("current_period_end")
        for item in ((sub.get("items") or {}).get("data") or [])
        if item.get("current_period_end")
    ]
    return max(ends) if ends else None


def derived_notice(subscription: dict[str, Any] | None, *, now: float) -> tuple[str | None, Any]:
    """The notice a subscription's own state implies, and the date it is about.

    Only the derivable ones. `action_required` and `final_notice` are written by
    the invoice handlers and must not be recomputed away here -- they carry
    information the subscription object does not have. The caller keeps a stored
    notice when this returns None for a status that has one.
    """
    if subscription is None:
        return None, None
    status = subscription.get("status")
    end = subscription.get("current_period_end") or _item_period_end(subscription)

    # ORDER IS THE WHOLE FUNCTION, and it is not the obvious one.
    #
    # Terminal first: a subscription that has already ended is lapsed, not
    # "about to be cancelled".
    if status in ("canceled", "unpaid"):
        return "lapsed", end
    # THEN cancellation, ahead of the trial. A trialing subscription that has
    # been cancelled is still `trialing` to Stripe, so testing the status first
    # tells somebody who has just cancelled that their card will be charged on
    # the third day. It will not. That is the sentence that produces a support
    # email at best and a dispute at worst, sent at the moment they are least
    # inclined to be forgiving.
    if subscription.get("cancel_at_period_end"):
        return "canceling", end
    if status == "trialing":
        trial_end = subscription.get("trial_end")
        if trial_end and (float(trial_end) - now) <= TRIAL_ENDING_WINDOW:
            return "trial_ending", trial_end
        return "trial_active", trial_end
    return None, None
