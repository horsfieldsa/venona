"""Queue a notice, and get it sent.

THE FIRST DURABLE OUTBOUND STATE IN THIS REPO, and worth saying why it earns
that when nothing before it did. `notify.alert` swallowed every exception by
design, and the design was right: a notifier that can fail a run turns a warning
about a degraded run into a failed one, which is exactly backwards. That
argument covers the RAISING side and says nothing about the sending side, and it
only held while the sole reader of an alert was the person who configured the
alert. Once a reader can ask "did my brief get sent", the question needs an
answer, and an answer needs a row.

So the split: raising a notice still cannot fail anything, and sending it is now
a separate pass that records what happened.

RETRY IS HERE AND NOT IN THE CHANNEL. A channel makes one attempt and says
whether trying again is worth it. Retrying inside the request would send the
same message twice to a receiver that answered slowly, which for a person's
inbox is worse than a delayed message.

STRIKES ARE THE `sources/registry.py` PATTERN, deliberately reused rather than
re-reasoned: consecutive failures, not a rate, because a destination that failed
one send in ten is working; and the way back is a button in the app, which is
what ADR 0041 cost to learn the first time.
"""

from __future__ import annotations

import json
import logging
import random
from datetime import UTC, datetime, timedelta

from venona.db import core as db
from venona.db.repositories import notify as repo
from venona.notify import render
from venona.notify.channels import DeliveryError, webhook
from venona.notify.channels import email as email_channel
from venona.notify.types import Notice

log = logging.getLogger("venona.notify")

#: Consecutive failures before venona stops sending to a destination. Three, the
#: same number and the same reasoning as a feed's: one transient blip disables
#: nothing, a moved endpoint disables itself.
STRIKE_LIMIT = 3

#: How many attempts one delivery gets before it is `failed` for good. Sized
#: against the backoff below so a receiver can be down for roughly an hour and
#: still get the message.
MAX_ATTEMPTS = 6

#: How many deliveries one drain pass takes. Small, because a pass runs inside
#: the api process beside everything else it is doing, and the next one is
#: seconds away.
BATCH = 20

#: A claim older than this belonged to a process that is gone. Every deploy of
#: the api is stop then start, so this is the ordinary case rather than a
#: crash-only path.
STALLED_AFTER = timedelta(minutes=5)

#: First retry after a minute, doubling, capped. Jittered so a receiver coming
#: back does not meet every parked delivery in the same second.
BACKOFF_BASE = 60.0
BACKOFF_CAP = 1800.0


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _now() -> datetime:
    return datetime.now(UTC)


def _next_attempt(attempts: int, now: datetime) -> str:
    delay = min(BACKOFF_BASE * (2 ** max(0, attempts - 1)), BACKOFF_CAP)
    return _iso(now + timedelta(seconds=delay * (0.75 + random.random() * 0.5)))


def _payload(notice: Notice) -> str:
    """The rendered notice, stored as its named fields.

    Stored rather than re-rendered at send time because a notice describes
    something that was true when it was raised. Re-rendering against a corpus
    that has moved on would deliver a different sentence than the one the queue
    accepted, and on a retry an hour later that difference is silent.
    """
    return json.dumps(
        {
            "title": notice.title,
            "body": list(notice.body),
            "quoted": notice.quoted,
            "link": notice.link,
            "link_label": notice.link_label,
        }
    )


def _revive(payload: str) -> Notice:
    data = json.loads(payload)
    return Notice(
        title=data.get("title", ""),
        body=tuple(data.get("body") or ()),
        quoted=data.get("quoted"),
        link=data.get("link"),
        link_label=data.get("link_label"),
    )


def enqueue(
    conn: db.Conn,
    *,
    notice_type: str,
    notice: Notice,
    destinations: list[db.Row],
    dedupe_key: str | None,
    new_id,
) -> int:
    """One row per subscribed destination. Returns how many were queued.

    An email destination is queued `blocked` when there is no transport, rather
    than not queued at all. The row is the honest record that somebody asked to
    be told and venona could not: it shows on the admin screen with a reason,
    and `release_blocked` sends every one of them the day a provider is wired.
    """
    payload = _payload(notice)
    now = _iso(_now())
    queued = 0
    for destination in destinations:
        blocked = destination["kind"] == "email" and not email_channel.available()
        if repo.enqueue(
            conn,
            delivery_id=new_id(),
            notice_type=notice_type,
            destination_id=destination["id"],
            payload=payload,
            dedupe_key=dedupe_key,
            status="blocked" if blocked else "queued",
            now=now,
        ):
            queued += 1
    return queued


async def deliver_one(client, row: db.Row, *, base: str) -> None:
    """Send one claimed delivery, or raise `DeliveryError`."""
    notice = _revive(row["payload"])
    if row["kind"] == "webhook":
        await webhook.send(client, notice, target=row["target"], base=base)
        return
    if row["kind"] == "email":
        try:
            await email_channel.send(notice, target=row["target"], base=base)
        except email_channel.NotConfigured:
            # Not a failure of the address, so it must not count a strike
            # against it. The caller parks the row instead.
            raise
        return
    raise DeliveryError(f"no way to send to a {row['kind']} destination", permanent=True)


async def drain(conn: db.Conn, client, *, base: str = "", limit: int = BATCH) -> int:
    """One pass. Returns how many were delivered.

    Each delivery commits on its own, deliberately. A single transaction around
    the batch would mean one receiver being slow holds a row that another
    receiver already accepted, and a crash halfway would roll back sends that
    genuinely happened -- which is the one lie an outbox must never tell.
    """
    now = _now()
    claimed = repo.claim_batch(conn, limit=limit, now=_iso(now))
    if not claimed:
        return 0
    delivered = 0
    for row in claimed:
        stamp = _iso(_now())
        try:
            await deliver_one(client, row, base=base)
        except email_channel.NotConfigured as exc:
            # Parked rather than failed, and no strike against the destination:
            # the address is fine, there is simply nowhere to hand it to.
            with db.transaction(conn, what="notify_block"):
                repo.mark_blocked(conn, delivery_id=row["id"], error=str(exc), now=stamp)
            continue
        except DeliveryError as exc:
            _record_failure(conn, row, str(exc), permanent=exc.permanent, now=stamp)
            continue
        except Exception as exc:  # noqa: BLE001 - a sender must never end the pass
            log.warning("delivery %s raised", row["id"], exc_info=True)
            _record_failure(conn, row, f"{type(exc).__name__}: {exc}"[:400], False, stamp)
            continue
        with db.transaction(conn, what="notify_delivered"):
            repo.mark_delivered(conn, delivery_id=row["id"], now=stamp)
            repo.record_success(conn, destination_id=row["destination_id"], now=stamp)
        delivered += 1
    return delivered


def _record_failure(conn: db.Conn, row: db.Row, error: str, permanent: bool, now: str) -> None:
    """Count the strike, and decide whether this delivery gets another go."""
    exhausted = permanent or row["attempts"] >= MAX_ATTEMPTS
    with db.transaction(conn, what="notify_failed"):
        if exhausted:
            repo.mark_failed(conn, delivery_id=row["id"], error=error, now=now)
        else:
            repo.mark_retry(
                conn,
                delivery_id=row["id"],
                error=error,
                next_attempt_at=_next_attempt(row["attempts"], _now()),
                now=now,
            )
        strikes = repo.record_failure(
            conn,
            destination_id=row["destination_id"],
            error=error,
            limit=STRIKE_LIMIT,
            now=now,
        )
    if strikes >= STRIKE_LIMIT:
        log.warning(
            "notify: stopped sending to %s after %s consecutive failures: %s",
            row["label"],
            strikes,
            error,
        )


def reclaim(conn: db.Conn) -> int:
    """Put claims nobody is holding back on the queue."""
    now = _now()
    with db.transaction(conn, what="notify_reclaim"):
        return repo.reclaim_stalled(conn, before=_iso(now - STALLED_AFTER), now=_iso(now))


def as_claim(notice: Notice, row, target: str) -> dict:
    """A one-off delivery shaped like a claimed row, for a test send.

    A test goes straight through the channel rather than through the queue: it
    is a question with an answer, and queueing it would report "we will try" to
    somebody who wants to know whether it works. It also counts no strike -- a
    failed test is information, not a reason to stop using an address the
    operator is still setting up.
    """
    return {
        "id": row["id"],
        "payload": _payload(notice),
        "kind": row["kind"],
        "target": target,
        "label": row["label"],
        "attempts": 0,
        "destination_id": row["id"],
    }


def render_preview(notice: Notice, *, kind: str, target: str, base: str = "") -> str:
    """What a destination would actually receive. For a test send's echo."""
    if kind == "webhook":
        content, _ = webhook.body_for(notice, target, base=base)
        return content.decode()
    _, text, _ = render.as_email(notice, base=base)
    return text
