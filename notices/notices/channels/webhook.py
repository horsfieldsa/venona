"""Posting a notice to an address somebody typed.

THE SHAPE IS CHOSEN FROM THE HOST, not configured. A Slack or Discord endpoint
wants JSON and renders a card; ntfy and healthchecks.io want a body they can
show as-is. Asking the person adding a destination to also classify it is a
question they should not have to answer about a URL they pasted, and getting it
wrong shows them a wall of JSON in a chat channel. The plain-text branch sets
ntfy's `Title` header unconditionally, which a receiver that does not know it
ignores.

EVERY REQUEST GOES THROUGH `HttpClient.post_once`, never a bare client. See its
docstring for the reason: `docs/security/REVIEW-2026-08.md` clears an outbound
client that bypasses `urlguard` only where its URL is operator-set and not
runtime-editable, and a destination row is typed by a reader through an API.
`check_static` runs at save time, so a refusal arrives as a sentence in the
form rather than as a failed delivery hours later; this is the connect-time
half.
"""

from __future__ import annotations

from urllib.parse import urlparse

from notices import render
from notices.channels import DeliveryError
from notices.types import Notice

#: Hosts whose endpoints take a JSON card. Suffix-matched against the
#: registrable-looking tail so `hooks.slack.com` and a workspace subdomain both
#: land, and an unknown host falls through to plain text, which every receiver
#: can display.
JSON_HOSTS = ("slack.com", "discord.com", "discordapp.com", "office.com", "webhook.office.com")

#: A 2xx is delivered. Everything else is not, and these are the ones where
#: asking again is pointless: the endpoint is gone, revoked, or refusing us
#: specifically. 429 is deliberately absent -- that one means "later".
PERMANENT_STATUSES = frozenset({400, 401, 403, 404, 405, 410, 422})


def wants_json(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in JSON_HOSTS)


def body_for(notice: Notice, target: str, *, base: str) -> tuple[bytes, dict[str, str]]:
    """The bytes and the headers this receiver expects."""
    if wants_json(target):
        return render.as_slack(notice, base=base).encode(), {
            "content-type": "application/json; charset=utf-8"
        }
    return render.as_text(notice, base=base).encode(), {
        "content-type": "text/plain; charset=utf-8",
        # ntfy reads the title from a header rather than the first line, and a
        # receiver that does not know the header ignores it.
        "Title": render.title_header(notice),
    }


async def send(client, notice: Notice, *, target: str, base: str = "") -> None:
    """Deliver, or raise `DeliveryError` saying whether trying again is worth it.

    `client` is an `HttpClient`, passed in rather than built here: the offline
    suite hands over a stand-in, and the drainer owns one client for a whole
    pass rather than one per message.
    """
    content, headers = body_for(notice, target, base=base)
    try:
        response = await client.post_once(target, content=content, headers=headers)
    except Exception as exc:  # noqa: BLE001 - transport, DNS and the guard alike
        # A `BlockedUrl` lands here too, and it is deliberately NOT permanent in
        # the retry sense while still being worth reporting: a host that resolves
        # to a private address today may be a misconfiguration someone fixes.
        # What it must not do is behave like a transport failure downstream --
        # `pipeline/fetch.py` makes the same carve-out so a blocked URL never
        # earns a source a strike.
        raise DeliveryError(f"{type(exc).__name__}: {exc}"[:400]) from exc

    if 200 <= response.status < 300:
        return
    detail = (response.text or "").strip()[:200]
    raise DeliveryError(
        f"the endpoint answered {response.status}" + (f": {detail}" if detail else ""),
        permanent=response.status in PERMANENT_STATUSES,
    )
