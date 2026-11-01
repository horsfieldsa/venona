"""What the hub is willing to tell its operator about, declared in one table.

The shape is the instance's `notify/types.py`, and for the reason ADR 0187 gives
it there: a type declares what it SAYS, not merely that it exists. The words
live beside the key so the console's switch and the message a person receives
cannot drift apart.

ONE AUDIENCE, which is the whole difference from the instance's registry. Every
notice here is about the hub's own working and goes wherever the operator
pointed it, so there is no `audience` field and no addressing to get wrong. The
instance carries one because it also writes to readers about their own reading,
and a shared `NoticeType` would put a field on this side whose only value is a
constant.

WHAT IS SHARED IS THE DANGEROUS HALF: `notices.render` escapes a stranger's
words for the markup they are about to land in, and `notices.urlguard` decides
which addresses the hub will dial. Two copies of either is not a duplicated
rule, it is a hole. ADR 0222.

THE WORDS ARE READ BY A PERSON, so the same three constraints the instance's
registry carries apply here and `tests/hub/test_hub_notifications.py` holds
them: no `undefined`, no em or en dashes, and no vocabulary out of this repo.
An operator is a reader too.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from notices.types import Facts, Notice
from notices.urlguard import BlockedUrl, check_static

#: Destinations one operator may add. Not a technical limit: it is the number
#: past which "where does this go" stops being answerable at a glance.
MAX_DESTINATIONS = 10

LABEL_MAX = 60


@dataclass(frozen=True)
class NoticeType:
    """One row of the table."""

    key: str
    #: What the switch says. Reader words, sentence case, no trailing period.
    label: str
    #: The line under the switch. What subscribing actually gets you, and when.
    detail: str
    #: Whether a NEW destination starts subscribed to this.
    #:
    #: True for all of them. An operator who has just told the hub where to
    #: reach them means "tell me when something needs me", and there is a short
    #: row of switches, not fifty. Absence still means no afterwards, so
    #: unticking one sticks -- and `notify_offered` is what keeps that true when
    #: a later release adds a switch this destination has never been shown.
    default_on: bool = True
    #: What makes a repeat the SAME notice, or None to let every occurrence
    #: stand alone.
    dedupe: Callable[[Facts], str | None] = lambda facts: None
    #: Facts to `Notice`. Reads named keys; never `**facts` into a template.
    render: Callable[[Facts], Notice] = field(repr=False, default=lambda facts: Notice(title=""))


class DestinationError(ValueError):
    """A destination the hub will not store. Carries the sentence shown."""


def check_webhook(url: str) -> str:
    """The save-time half of the SSRF guard. Returns the URL, or refuses.

    `check_static` is the shared predicate, so the hub and the instance answer
    this question the same way and a fix to either reaches both. It performs no
    DNS: a lookup in a save path would make the form depend on the network.
    Resolving and dialling only the vetted address is the connect-time half,
    and `notify.post_once` carries it.

    HTTPS ONLY. A notice body carries a stranger's words and sometimes an
    address, and plaintext to a webhook endpoint is a choice nobody would make
    deliberately.
    """
    cleaned = url.strip()
    if not cleaned:
        raise DestinationError("Enter the web address the app gave you.")
    try:
        parsed = check_static(cleaned)
    except BlockedUrl as exc:
        raise DestinationError(str(exc)) from exc
    if parsed.scheme != "https":
        raise DestinationError("The address has to start with https.")
    return cleaned


def check_label(label: str) -> str:
    cleaned = " ".join(label.split())
    if not cleaned:
        raise DestinationError("Give it a name so you can tell it apart later.")
    if len(cleaned) > LABEL_MAX:
        raise DestinationError(f"Keep the name under {LABEL_MAX} characters.")
    return cleaned


def target_hint(target: str) -> str:
    """What a page may show of a stored address.

    A webhook URL is a credential: anyone holding a Slack incoming address can
    post to that channel. This is what stands in its place, and it is enough to
    tell two destinations apart without being enough to use.
    """
    without_scheme = target.split("://", 1)[-1]
    host = without_scheme.split("/", 1)[0]
    tail = target.rstrip("/")[-4:]
    return f"{host}/…{tail}"


# ── what each notice says ────────────────────────────────────────────


def _access_requested(facts: Facts) -> Notice:
    wanted = (facts.get("domain") or "").strip()
    asked_for = f"asked for {wanted}" if wanted else "asked for access"
    return Notice(
        title="Someone asked for access",
        body=(
            f"{facts.get('email', 'Someone')} {asked_for} and is waiting on a decision.",
            "They wrote this about themselves:",
        ),
        # Up to two thousand characters a stranger typed into a public form. It
        # stays in `quoted` and out of `body` so no channel can splice it into
        # its own markup.
        quoted=(facts.get("note") or "").strip() or None,
        link="/admin/requests",
        link_label="Open the queue",
    )


def _launch_signup(facts: Facts) -> Notice:
    return Notice(
        title="Someone left an address for launch",
        body=(
            f"{facts.get('email', 'Someone')} asked to be told when there is a door.",
            "There is nothing to decide here. They are on the list.",
        ),
        link="/admin/launch",
        link_label="Open the list",
    )


def _instance_unreachable(facts: Facts) -> Notice:
    seen = (facts.get("last_seen") or "").strip()
    return Notice(
        title=f"{facts.get('domain', 'A domain')} is not answering",
        body=(
            "Three checks in a row went unanswered, so the hub has stopped "
            "treating it as live. Nobody can read there while this lasts.",
            f"It last answered {seen}." if seen else "It has not answered since the hub started.",
            # THE HONEST CAVEAT, and it belongs in the message rather than in a
            # comment nobody receiving this can read. The check asks the domain
            # to count what it holds, so a domain that is up with a slow
            # database fails it exactly like one that is down.
            "The check asks it to count what it holds, so a slow database looks "
            "the same as a machine that is gone.",
        ),
        link="/admin",
        link_label="Open the domains",
    )


def _instance_answering(facts: Facts) -> Notice:
    return Notice(
        title=f"{facts.get('domain', 'A domain')} is answering again",
        body=("It came back on its own and reading has resumed.",),
        link="/admin",
        link_label="Open the domains",
    )


def _account_created(facts: Facts) -> Notice:
    return Notice(
        title="Someone new signed in",
        body=(
            f"{facts.get('email', 'Someone')} signed in for the first time. "
            "Venona has never seen this address before.",
            # WHAT THEY CAN AND CANNOT DO, because that is the only thing the
            # operator would go and look up. A stranger with a session holds
            # nothing until they buy a domain or somebody grants them one.
            "They hold nothing yet. They can buy a domain if one is on sale, "
            "or ask for one if it is not.",
        ),
        link="/admin/accounts",
        link_label="Open accounts",
    )


def _billing_subscribed(facts: Facts) -> Notice:
    trialing = str(facts.get("status") or "") == "trialing"
    return Notice(
        title="Someone subscribed",
        body=(
            f"{facts.get('email', 'A reader')} is now paying for "
            f"{facts.get('domain', 'a domain')}.",
            "They are in a free trial, so the first payment has not been taken yet."
            if trialing
            else "The first payment went through.",
        ),
        link="/admin/accounts",
        link_label="Open accounts",
    )


#: What a subscription status means to somebody who does not work at Stripe.
_BILLING_WORDS = {
    "past_due": "a payment did not go through",
    "unpaid": "a payment has been missed more than once",
    "incomplete": "a payment was started and never finished",
    "incomplete_expired": "a payment was started and ran out of time",
    "canceled": "the subscription was cancelled",
}


def _billing_changed(facts: Facts) -> Notice:
    status = str(facts.get("status") or "")
    return Notice(
        title="A subscription needs attention",
        body=(
            f"For {facts.get('email', 'a reader')} on {facts.get('domain', 'a domain')}: "
            f"{_BILLING_WORDS.get(status, 'the subscription changed')}.",
            "Stripe writes to them about this. This is so you know before they do.",
        ),
        link="/admin/accounts",
        link_label="Open accounts",
    )


#: Every notice type, in the order the console shows them.
NOTICE_TYPES: dict[str, NoticeType] = {
    "access.requested": NoticeType(
        key="access.requested",
        label="Someone asked for access",
        detail="A person filled in the form and is waiting on a decision.",
        dedupe=lambda f: f"access:{f.get('request_id')}" if f.get("request_id") else None,
        render=_access_requested,
    ),
    "instance.unreachable": NoticeType(
        key="instance.unreachable",
        label="A domain stopped answering",
        detail=(
            "Three checks in a row went unanswered. Nobody can read there while "
            "it lasts, and nothing else would tell you."
        ),
        # Keyed on the OUTAGE, not the domain: the status column crosses from
        # live to offline exactly once per outage, so this says it once rather
        # than every five minutes until somebody notices.
        dedupe=lambda f: f"down:{f.get('instance_id')}:{f.get('since')}",
        render=_instance_unreachable,
    ),
    "instance.answering": NoticeType(
        key="instance.answering",
        label="A domain started answering again",
        detail="The all clear, so a quiet channel means it is still down.",
        dedupe=lambda f: f"up:{f.get('instance_id')}:{f.get('since')}",
        render=_instance_answering,
    ),
    "launch.signup": NoticeType(
        key="launch.signup",
        label="Someone left an address for launch",
        detail=(
            "A person asked to be told when a domain opens. Nothing is waiting "
            "on you, so this one is worth turning off if the list is busy."
        ),
        dedupe=lambda f: f"launch:{f.get('request_id')}" if f.get("request_id") else None,
        render=_launch_signup,
    ),
    "account.created": NoticeType(
        key="account.created",
        label="Someone new signed in",
        detail="A person signed in with an address Venona has not seen before.",
        # Keyed on the ADDRESS rather than on any row, because there is no row:
        # somebody who signs in and buys nothing stays a session and a cookie,
        # so every later visit would look like a first one.
        dedupe=lambda f: f"signup:{f.get('email')}" if f.get("email") else None,
        render=_account_created,
    ),
    "billing.subscribed": NoticeType(
        key="billing.subscribed",
        label="Someone subscribed",
        detail="A reader started paying for a domain. The good half of the billing news.",
        # ONE PER SUBSCRIPTION, not per status: a trial that converts to active
        # is the same person still paying, and saying it twice would make the
        # count of subscribers this notice implies wrong.
        dedupe=lambda f: f"subscribed:{f.get('subscription_id')}",
        render=_billing_subscribed,
    ),
    "billing.changed": NoticeType(
        key="billing.changed",
        label="A subscription needs attention",
        detail=(
            "A payment failed, or a subscription was cancelled. Stripe writes "
            "to the reader about these; this is so you hear about it too."
        ),
        dedupe=lambda f: f"bill:{f.get('subscription_id')}:{f.get('status')}:{f.get('since')}",
        render=_billing_changed,
    ),
}

#: The statuses worth saying out loud. A subscription going `active` or
#: `trialing` is the system working, and paging on it would train whoever reads
#: these to stop reading them.
BILLING_ALARMS = frozenset(_BILLING_WORDS)


def all_types() -> tuple[NoticeType, ...]:
    """Every type, in table order. What the console renders switches from."""
    return tuple(NOTICE_TYPES.values())
