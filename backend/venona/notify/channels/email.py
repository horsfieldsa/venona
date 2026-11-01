"""Email, built as far as the transport and no further.

THERE IS NO PROVIDER YET, and this module is the shape of the hole rather than a
stub that pretends. Everything an email needs except the sending exists: the
address (every account has one, established by the identity provider and stored
`NOT NULL UNIQUE`), both bodies, the subscription, the queue row and both
screens. What is missing is a service to hand the message to, a domain to send
it from, and the DNS records that stop it landing in a spam folder. Those are a
decision with a bill attached, not code.

SO A SUBSCRIPTION IS HONOURED, NOT REFUSED. An email delivery with no transport
is queued and then parked as `blocked`, which is a state and not a failure: the
row exists, the admin screen says why it has not moved, and the moment a
transport is configured `release_blocked` puts every parked row back on the
queue. The alternative -- skipping the enqueue when no provider is set -- makes
a reader's switch look persisted and do nothing, which is the thing
`AccountView.vue` refused to ship a control for in the first place.

WHERE THE CREDENTIAL WILL COME FROM, when there is one: `os.environ`, never
`Settings`. `api/security.py` opens with the reason and it applies unchanged --
`config/settings.yaml` is readable AND writable over HTTP, so an API key on the
settings model is both operator-visible and attacker-writable.
"""

from __future__ import annotations

import os
from typing import Protocol

from venona.notify import render
from venona.notify.types import Notice


class Transport(Protocol):
    """The one method a provider has to supply.

    Deliberately narrow. A provider integration is expected to be an adapter of
    a few dozen lines over an HTTP API, in the shape `sources/` uses for feeds:
    if it needs more of venona than this, the seam is in the wrong place.
    """

    async def send(self, *, to: str, subject: str, text: str, html: str) -> None: ...


class NoTransport:
    """What is wired today. Reports the absence rather than swallowing it."""

    available = False
    reason = "Venona has no way to send email on this domain yet."

    async def send(self, *, to: str, subject: str, text: str, html: str) -> None:
        raise NotConfigured(self.reason)


class NotConfigured(RuntimeError):
    """No transport. Distinct from a delivery failure, and handled differently:
    the outbox parks the row rather than counting a strike against the address,
    because nothing is wrong with the address."""


def transport() -> NoTransport:
    """The configured transport, or the one that says there isn't one.

    A function rather than a module constant so that configuring a provider is a
    one-line change here and an environment variable, with nothing to re-import.
    """
    return NoTransport()


def available() -> bool:
    """Whether an email subscription can currently be delivered.

    Read by `GET /api/notifications` so the admin screen can say so, and by the
    reader's screen, which renders an honest sentence in place of switches when
    this is False. A switch that flips and does nothing is worse than a
    sentence saying why there is no switch.
    """
    return transport().available


def unavailable_reason() -> str:
    return transport().reason


async def send(notice: Notice, *, target: str, base: str = "") -> None:
    """Hand one notice to whatever transport exists. Raises `NotConfigured`
    while none does."""
    subject, text, body_html = render.as_email(notice, base=base)
    await transport().send(to=target, subject=subject, text=text, html=body_html)


def address_is_plausible(address: str) -> str | None:
    """A refusal sentence, or None if the address looks like one.

    Deliberately shallow. Every address venona sends to today comes from the
    identity provider rather than from a form, so this guards a typo in a future
    field, not a hostile input, and a regex claiming to validate an email
    address is a well-known way to refuse a real one. What it does catch is the
    shape that would break the transport call itself.

    IT DOES NOT REQUIRE A DOT IN THE DOMAIN, and that is a correction rather
    than an oversight. A first draft did, and it refused `operator@localhost` --
    the well-known row `learning._actor` binds on an auth-off development box,
    which is to say every local instance. A bare hostname is a legal domain
    part; demanding a dot is precisely the well-known way to refuse a real
    address that the paragraph above warns about, arriving one function later.
    """
    cleaned = address.strip()
    if not cleaned or cleaned.count("@") != 1:
        return "That does not look like an email address."
    local, _, domain = cleaned.partition("@")
    if not local or not domain or domain.startswith(".") or domain.endswith("."):
        return "That does not look like an email address."
    if any(ch.isspace() for ch in cleaned):
        return "An email address cannot contain a space."
    return None


def _env(name: str) -> str:
    """Every provider setting will read through here, and every one of them from
    the environment. Present now so the first provider does not have to decide
    it, and so this module states the rule where it will be broken."""
    return os.environ.get(name, "").strip()
