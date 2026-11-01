"""Signature verification, replay, and what a Stripe status means for access.

The verification half is security-critical and the entitlement half decides who
reads what, so both are tested against the shapes Stripe actually sends rather
than against a helper's idea of them.
"""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest

from hub import stripe_webhook as sw

SECRET = "whsec_" + "a" * 32
BODY = b'{"id":"evt_1","type":"customer.subscription.updated"}'


def sign(payload: bytes, secret: str, timestamp: int) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256)
    return f"t={timestamp},v1={mac.hexdigest()}"


def test_a_genuine_signature_verifies() -> None:
    now = time.time()
    sw.verify(BODY, sign(BODY, SECRET, int(now)), SECRET, now=now)


def test_a_forged_signature_is_refused() -> None:
    now = time.time()
    header = f"t={int(now)},v1={'0' * 64}"
    with pytest.raises(sw.SignatureError):
        sw.verify(BODY, header, SECRET, now=now)


def test_the_wrong_secret_is_refused() -> None:
    now = time.time()
    with pytest.raises(sw.SignatureError):
        sw.verify(BODY, sign(BODY, "whsec_" + "b" * 32, int(now)), SECRET, now=now)


def test_a_tampered_body_is_refused() -> None:
    """The signature covers the body, so changing one byte must break it. This
    is the property that makes an unauthenticated route safe."""
    now = time.time()
    header = sign(BODY, SECRET, int(now))
    with pytest.raises(sw.SignatureError):
        sw.verify(BODY.replace(b"evt_1", b"evt_2"), header, SECRET, now=now)


def test_an_old_signature_is_refused() -> None:
    """A captured request replayed tomorrow. The ledger stops it being APPLIED
    twice; the tolerance stops it being accepted at all."""
    now = time.time()
    old = int(now) - sw.TOLERANCE_SECONDS - 1
    with pytest.raises(sw.SignatureError):
        sw.verify(BODY, sign(BODY, SECRET, old), SECRET, now=now)


def test_a_future_signature_is_refused() -> None:
    now = time.time()
    ahead = int(now) + sw.TOLERANCE_SECONDS + 1
    with pytest.raises(sw.SignatureError):
        sw.verify(BODY, sign(BODY, SECRET, ahead), SECRET, now=now)


def test_a_second_v1_verifies_during_secret_rotation() -> None:
    """Stripe sends more than one `v1` while a secret is being rotated, which is
    exactly the moment this must not break."""
    now = time.time()
    good = sign(BODY, SECRET, int(now))
    header = f"{good},v1={'0' * 64}"
    sw.verify(BODY, header, SECRET, now=now)


@pytest.mark.parametrize("header", ["", "t=1", "v1=abc", "garbage", "t=notanumber,v1=abc"])
def test_a_malformed_header_is_refused_not_crashed(header: str) -> None:
    with pytest.raises(sw.SignatureError):
        sw.verify(BODY, header, SECRET, now=time.time())


@pytest.mark.parametrize(
    "signature",
    [
        "café" + "a" * 60,  # non-ASCII: `compare_digest` raises TypeError on str
        "\u0000" * 64,  # a null byte
        "🔑" * 64,  # astral plane
        "z" * 64,  # right length, not hex
        "a" * 63,  # one short
        "a" * 65,  # one long
    ],
)
def test_a_hostile_signature_is_refused_not_crashed(signature: str) -> None:
    """The route is UNAUTHENTICATED -- the signature is the authentication -- so
    every byte here is attacker-controlled. `hmac.compare_digest` raises
    TypeError on a str holding any non-ASCII character, and the route catches
    only `SignatureError`, so that escaped as a 500 anybody could trigger by
    sending one accented letter.
    """
    header = f"t={int(time.time())},v1={signature}"
    with pytest.raises(sw.SignatureError):
        sw.verify(BODY, header, SECRET, now=time.time())


def test_a_hostile_candidate_beside_a_valid_one_still_verifies() -> None:
    """Refusing the bad candidate must not refuse the whole header. Stripe sends
    several `v1` values during a secret rotation, and one unparseable value must
    not take the good one down with it."""
    now = time.time()
    header = f"{sign(BODY, SECRET, int(now))},v1={'café' + 'a' * 60}"
    sw.verify(BODY, header, SECRET, now=now)


# ── entitlement ──────────────────────────────────────────────────────


@pytest.mark.parametrize("status", ["trialing", "active", "past_due"])
def test_these_statuses_entitle(status: str) -> None:
    """`past_due` included: Stripe is still retrying the card, and cutting
    somebody off at the first decline is how a recoverable lapse becomes a
    cancellation."""
    out = sw.entitlement_for({"status": status, "current_period_end": 1234})
    assert out == {"status": status, "through": 1234}


@pytest.mark.parametrize("status", ["canceled", "unpaid"])
def test_terminal_statuses_lapse_but_keep_the_ceiling(status: str) -> None:
    out = sw.entitlement_for({"status": status, "current_period_end": 1234})
    assert out == {"status": "lapsed", "through": 1234}


@pytest.mark.parametrize("status", ["incomplete", "incomplete_expired"])
def test_a_failed_first_payment_is_never_started_not_lapsed(status: str) -> None:
    """The distinction that matters: somebody whose card never succeeded must
    not inherit a frozen archive they never paid for."""
    out = sw.entitlement_for({"status": status, "current_period_end": 1234})
    assert out == {"status": "none", "through": None}


def test_no_subscription_is_never_started() -> None:
    assert sw.entitlement_for(None) == {"status": "none", "through": None}


# ── the notice a subscription implies ────────────────────────────────

NOW = 1_700_000_000.0


def test_a_trial_with_days_left_says_so() -> None:
    kind, at = sw.derived_notice({"status": "trialing", "trial_end": NOW + 3 * 86400}, now=NOW)
    assert kind == "trial_active"
    assert at == NOW + 3 * 86400


def test_a_trial_inside_a_day_escalates() -> None:
    """The countdown runs the whole trial, but the last day is the one that
    states the amount and the date. Surprise is what produces chargebacks."""
    kind, _ = sw.derived_notice({"status": "trialing", "trial_end": NOW + 3600}, now=NOW)
    assert kind == "trial_ending"


def test_an_active_subscription_says_nothing() -> None:
    """No banner is the correct banner for somebody who is fine."""
    assert sw.derived_notice({"status": "active"}, now=NOW) == (None, None)


def test_a_scheduled_cancellation_is_calm_not_a_warning() -> None:
    out = sw.derived_notice(
        {"status": "active", "cancel_at_period_end": True, "current_period_end": NOW + 86400},
        now=NOW,
    )
    assert out == ("canceling", NOW + 86400)


def test_an_ended_subscription_is_lapsed_not_canceling() -> None:
    """Order matters: a subscription that has already ended and also carries
    `cancel_at_period_end` is lapsed. Telling somebody their access is 'about to
    end' when it already has is the kind of copy that reads as a broken app."""
    out = sw.derived_notice(
        {"status": "canceled", "cancel_at_period_end": True, "current_period_end": NOW - 86400},
        now=NOW,
    )
    assert out == ("lapsed", NOW - 86400)


def test_the_invoice_driven_kinds_are_not_derivable() -> None:
    """`action_required` and `final_notice` come from invoice events. Nothing
    about a subscription's own status can produce them, which is why they are
    stored rather than recomputed -- and why this function must never return
    one."""
    for status in ("trialing", "active", "past_due", "canceled", "unpaid"):
        kind, _ = sw.derived_notice({"status": status}, now=NOW)
        assert kind not in ("action_required", "final_notice")


def test_cancelling_during_a_trial_does_not_promise_a_charge() -> None:
    """A cancelled trial is still `trialing` to Stripe, so checking the status
    first tells somebody who just cancelled that their card will be charged on
    the third day. It will not. That sentence produces a support email at best
    and a dispute at worst."""
    kind, _ = sw.derived_notice(
        {
            "status": "trialing",
            "trial_end": NOW + 3 * 86400,
            "cancel_at_period_end": True,
            "current_period_end": NOW + 3 * 86400,
        },
        now=NOW,
    )
    assert kind == "canceling"


def test_an_uncancelled_trial_still_says_trial() -> None:
    kind, _ = sw.derived_notice(
        {"status": "trialing", "trial_end": NOW + 3 * 86400, "cancel_at_period_end": False},
        now=NOW,
    )
    assert kind == "trial_active"
