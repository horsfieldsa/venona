"""Raising a notice, and getting it sent.

TWO HALVES WITH DIFFERENT PROMISES, the split the instance's outbox argues for
and this reuses. RAISING can never fail its caller: every call site is reporting
something that has already happened, inside a request handler that has already
committed or a poller cycle that must not die, and an exception here would turn
"we could not tell you" into "the thing being reported failed". SENDING is
allowed to fail and to say so, because once an operator can ask "did it go out"
the question needs an answer and an answer needs a row.

THE SSRF GUARD IS IN TWO PLACES AND BOTH ARE NEEDED. `notices.check_webhook`
runs at save time so a bad address arrives as a sentence in the form rather than
as a delivery that quietly never happens. `post_once` below runs the other half
at connect time: resolve the name, check every answer, then dial the address
that was checked. Without the second, a host that passes the first can answer
with a private address a moment later and the check bought nothing.

RESOLUTION IS THIS SERVICE'S OWN, and deliberately not shared. What is dangerous
to have two copies of is the POLICY -- which addresses are refused -- and that
is `notices.urlguard`, imported by both. A `getaddrinfo` call is not a rule; the
instance's version is entangled with its own retry and strike semantics, and
lifting it would drag those here where they do not apply. ADR 0222.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import random
import socket
from typing import Any
from urllib.parse import urlparse

import httpx
from notices.channels import DeliveryError, webhook
from notices.types import Notice
from notices.urlguard import BlockedUrl, check_resolved, check_static

from hub.db.core import Conn
from hub.ids import notice_id
from hub.notices import NOTICE_TYPES
from hub.repositories import notifications as repo

log = logging.getLogger("hub.notify")

#: Test seam, like `mgmt_client.MGMT_TRANSPORT` and `google.EXCHANGE_TRANSPORT`:
#: the offline suite injects an `httpx.MockTransport`; None means the network.
NOTIFY_TRANSPORT: httpx.AsyncBaseTransport | None = None

TIMEOUT = 10.0

#: How many deliveries one pass takes. Small, because a pass runs inside the
#: poller cycle beside everything else it does, and the next one is minutes away
#: rather than hours.
BATCH = 20

#: How many attempts one delivery gets before it is `failed` for good. Sized
#: against the backoff so a receiver can be down for roughly an hour and still
#: get the message.
MAX_ATTEMPTS = 6

BACKOFF_BASE = 60.0
BACKOFF_CAP = 1800.0

#: A claim older than this belonged to a process that is gone. Every deploy is
#: stop then start, so this is the ordinary case rather than a crash path.
STALLED_AFTER_SECONDS = 300


# ── raising ──────────────────────────────────────────────────────────


def offer_new_types(conn: Conn) -> int:
    """Show every destination the switches it has never been shown, and tick the
    ones that default on. Returns how many offers were made.

    WHY THIS EXISTS AT ALL. A subscription row means yes and its absence means
    no, which leaves a notice type shipped after a destination was made
    indistinguishable from one that destination turned off -- so a new type
    reached nobody, and the operator's own reason for adding the destination
    ("tell me when something needs me") quietly stopped applying to anything new.
    `notify_offered` is the second bit that makes the two distinguishable, and
    this is the only thing that writes it outside a destination's creation.

    A DELIBERATE NO IS NEVER UNDONE, because it is the OFFER that is recorded
    rather than the answer: a type already offered is skipped whether or not it
    is subscribed. Which also makes this idempotent -- it runs in the lifespan,
    and every deploy is a boot.

    Never raises, for the reason at the top of this module. A hub that would not
    start because it could not tick a checkbox is a worse hub.
    """
    try:
        offered = 0
        for destination_id in repo.destination_ids(conn):
            for declared in NOTICE_TYPES.values():
                if not repo.record_offer(
                    conn, destination_id=destination_id, notice_type=declared.key
                ):
                    continue
                offered += 1
                if declared.default_on:
                    repo.subscribe(conn, destination_id=destination_id, notice_type=declared.key)
        return offered
    except Exception:  # noqa: BLE001 - see the module docstring
        log.warning("notify: could not offer new notice types", exc_info=True)
        return 0


def raise_notice(conn: Conn, notice_type: str, **facts: Any) -> int:
    """Tell whoever asked to be told. Returns how many deliveries were queued.

    NEVER RAISES. See the module docstring: every caller is reporting something
    that already happened.
    """
    try:
        declared = NOTICE_TYPES.get(notice_type)
        if declared is None:
            log.warning("notify: no such notice type %r", notice_type)
            return 0
        destinations = repo.subscribed(conn, notice_type)
        if not destinations:
            return 0
        notice = declared.render(facts)
        payload = json.dumps(
            {
                "title": notice.title,
                "body": list(notice.body),
                "quoted": notice.quoted,
                "link": notice.link,
                "link_label": notice.link_label,
            }
        )
        dedupe = declared.dedupe(facts)
        queued = 0
        for destination in destinations:
            if repo.enqueue(
                conn,
                delivery_id=notice_id(),
                notice_type=notice_type,
                destination_id=destination["id"],
                payload=payload,
                dedupe_key=dedupe,
            ):
                queued += 1
        return queued
    except Exception:  # noqa: BLE001 - see the module docstring
        log.warning("notify: could not raise %r", notice_type, exc_info=True)
        return 0


def _revive(payload: str) -> Notice:
    data = json.loads(payload)
    return Notice(
        title=data.get("title", ""),
        body=tuple(data.get("body") or ()),
        quoted=data.get("quoted"),
        link=data.get("link"),
        link_label=data.get("link_label"),
    )


# ── sending ──────────────────────────────────────────────────────────


async def _resolve(host: str) -> list[Any]:
    """Every A and AAAA answer for `host`, deduplicated, v4 first.

    v4 first because the pin dials exactly ONE address, and `getaddrinfo` often
    returns AAAA ahead of A: on a host with no v6 route a pinned AAAA fails a
    request happy-eyeballs would have rescued. `AI_ADDRCONFIG` asks only for
    families this machine can route, for the same reason.

    A failed lookup raises `DeliveryError`, never `BlockedUrl`. A transient
    NXDOMAIN has to earn the same retry a refused connection does; `BlockedUrl`
    means "resolved fine, to an address the hub refuses to talk to", which is
    permanent.
    """
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(
            host, None, type=socket.SOCK_STREAM, flags=socket.AI_ADDRCONFIG
        )
    except OSError as exc:
        raise DeliveryError(f"could not look up {host}: {exc}") from exc
    seen: dict[str, Any] = {}
    for info in infos:
        address = info[4][0]
        if address not in seen:
            seen[address] = ipaddress.ip_address(address)
    if not seen:
        raise DeliveryError(f"could not look up {host}")
    return sorted(seen.values(), key=lambda ip: ip.version)


async def post_once(url: str, *, content: bytes, headers: dict[str, str]) -> tuple[int, str]:
    """One POST to an address somebody typed. No redirects, no retry.

    NO REDIRECTS. A webhook does not move: an endpoint answering 302 is either
    misconfigured or steering us somewhere unvetted, and neither earns a second
    request.

    NO RETRY EITHER, and that is the outbox's job rather than an omission.
    Retrying here would send the same message twice to a receiver that answered
    slowly, which for a channel a person reads is worse than a late message.

    THE PIN. The request dials the address just vetted instead of letting the
    transport resolve the name again, because that re-resolution IS the
    rebinding window. The hostname survives in an explicit `Host` header and, on
    https, in `sni_hostname`, so certificate verification still runs against the
    NAME. The IP exists only on the wire.
    """
    parsed = check_static(url)
    hostname = parsed.hostname or ""
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        answers = await _resolve(hostname)
        check_resolved(url, answers)
        pin: str | None = str(answers[0])
    else:
        # `check_static` refuses private literals outright, so anything reaching
        # here is public: dial it as-is and let the certificate be verified
        # against its IP SAN.
        pin = None

    target = httpx.URL(url)
    extensions = None
    if pin is not None:
        target = target.copy_with(host=pin)
        headers = {**headers, "Host": urlparse(url).netloc}
        if parsed.scheme == "https":
            extensions = {"sni_hostname": hostname}
    async with httpx.AsyncClient(
        timeout=TIMEOUT, trust_env=False, transport=NOTIFY_TRANSPORT, follow_redirects=False
    ) as client:
        response = await client.post(
            target, content=content, headers=headers, extensions=extensions
        )
    return response.status_code, response.text[:400]


async def deliver_one(row: dict[str, Any], *, base: str) -> None:
    """Send one claimed delivery, or raise `DeliveryError`."""
    if row["kind"] != "webhook":
        raise DeliveryError(f"no way to send to a {row['kind']} destination", permanent=True)
    notice = _revive(row["payload"])
    content, headers = webhook.body_for(notice, row["target"], base=base)
    try:
        status, body = await post_once(row["target"], content=content, headers=headers)
    except BlockedUrl as exc:
        # Resolved fine, to an address the hub refuses to dial. Asking again
        # changes nothing until somebody changes the destination.
        raise DeliveryError(str(exc), permanent=True) from exc
    except httpx.HTTPError as exc:
        raise DeliveryError(f"{type(exc).__name__}: {exc}") from exc
    if 200 <= status < 300:
        return
    raise DeliveryError(
        f"the receiver answered {status}: {body}".strip(),
        permanent=status in webhook.PERMANENT_STATUSES,
    )


def _backoff(attempts: int) -> float:
    """First retry after a minute, doubling, capped. Jittered so a receiver
    coming back does not meet every parked delivery in the same second."""
    delay = min(BACKOFF_BASE * (2 ** max(0, attempts - 1)), BACKOFF_CAP)
    return delay * (0.75 + random.random() * 0.5)


async def drain(conn: Conn, *, base: str = "", limit: int = BATCH) -> int:
    """One pass. Returns how many were delivered.

    Each delivery commits on its own, deliberately: one slow receiver must not
    hold a row another receiver already accepted, and a crash halfway must not
    roll back sends that genuinely happened. That is the one lie an outbox can
    never tell.
    """
    claimed = repo.claim_batch(conn, limit)
    if not claimed:
        return 0
    delivered = 0
    for row in claimed:
        try:
            await deliver_one(row, base=base)
        except DeliveryError as exc:
            _record_failure(conn, row, str(exc), permanent=exc.permanent)
            continue
        except Exception as exc:  # noqa: BLE001 - a sender must never end the pass
            log.warning("delivery %s raised", row["id"], exc_info=True)
            _record_failure(conn, row, f"{type(exc).__name__}: {exc}", permanent=False)
            continue
        repo.mark_delivered(conn, row["id"])
        repo.record_success(conn, row["destination_id"])
        delivered += 1
    return delivered


def _record_failure(conn: Conn, row: dict[str, Any], error: str, *, permanent: bool) -> None:
    """Count the strike, and decide whether this delivery gets another go."""
    if permanent or row["attempts"] >= MAX_ATTEMPTS:
        repo.mark_failed(conn, delivery_id=row["id"], error=error)
    else:
        repo.mark_retry(
            conn, delivery_id=row["id"], error=error, delay_seconds=_backoff(row["attempts"])
        )
    strikes = repo.record_failure(conn, destination_id=row["destination_id"], error=error)
    if strikes >= repo.STRIKE_LIMIT:
        log.warning(
            "notify: stopped sending to %s after %s consecutive failures: %s",
            row["label"],
            strikes,
            error,
        )


def reclaim(conn: Conn) -> int:
    """Put claims nobody is holding back on the queue."""
    return repo.reclaim_stalled(conn, STALLED_AFTER_SECONDS)
