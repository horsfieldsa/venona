"""Who gets told, and the rules about what a person may point venona at.

Separate from `outbox.py` because these are two different kinds of decision.
This module answers "should this be sent, and to whom" and touches no network;
the outbox answers "did it go" and does nothing else.

THE ADDRESSING RULE, and it is structural rather than a convention: a reader
notice is fanned out ONE READER AT A TIME, and `subscribed_destinations` has no
"everybody" case to call. A mistake in a caller can therefore mail one person's
brief to that person twice; it cannot mail it to the readership.
"""

from __future__ import annotations

from venona.db import core as db
from venona.db.repositories import notify as repo
from venona.notify.channels import email as email_channel
from venona.notify.types import NOTICE_TYPES, READER, NoticeType
from venona.sources import urlguard

#: Instance destinations one administrator may add. Not a technical limit: it is
#: the number past which "where does this go" stops being answerable at a glance,
#: and a runaway loop in a script would otherwise fill the table quietly.
MAX_INSTANCE_DESTINATIONS = 10

#: Per reader. Lower, because a reader has two notices to route.
MAX_READER_DESTINATIONS = 5

LABEL_MAX = 60


class DestinationError(ValueError):
    """A destination venona will not store. Carries the sentence shown."""


def check_webhook(url: str) -> str:
    """The save-time half of the SSRF guard. Returns the URL, or refuses.

    `urlguard.check_static` is the same function `PUT /api/config/sources` runs
    over a feed URL, and running it here rather than only at send time is what
    makes a bad address an error in the form with a sentence attached, instead
    of a delivery that quietly never arrives. It performs no DNS: a lookup in a
    save path would make the form depend on the network and the offline suite
    non-hermetic. `check_resolved` plus the pin is the connect-time half, and
    `HttpClient.post_once` carries it.

    HTTPS ONLY, which is stricter than a feed. A notice body carries brief text
    and sometimes a stranger's words, and plaintext to a webhook endpoint is a
    choice nobody would make deliberately.
    """
    cleaned = url.strip()
    if not cleaned:
        return _refuse("Enter the web address the app gave you.")
    try:
        parsed = urlguard.check_static(cleaned)
    except urlguard.BlockedUrl as exc:
        raise DestinationError(str(exc)) from exc
    if parsed.scheme != "https":
        return _refuse("The address has to start with https.")
    return cleaned


def check_label(label: str) -> str:
    cleaned = " ".join(label.split())
    if not cleaned:
        return _refuse("Give it a name so you can tell it apart later.")
    if len(cleaned) > LABEL_MAX:
        return _refuse(f"Keep the name under {LABEL_MAX} characters.")
    return cleaned


def check_email(address: str) -> str:
    cleaned = address.strip().lower()
    problem = email_channel.address_is_plausible(cleaned)
    if problem:
        return _refuse(problem)
    return cleaned


def _refuse(sentence: str):
    raise DestinationError(sentence)


def check_target(kind: str, target: str) -> str:
    if kind == "webhook":
        return check_webhook(target)
    if kind == "email":
        return check_email(target)
    return _refuse("Venona cannot send to that.")


def check_room(conn: db.Conn, *, owner_kind: str, owner_id: str | None) -> None:
    existing = (
        repo.instance_destinations(conn)
        if owner_id is None
        else repo.reader_destinations(conn, user_id=owner_id)
    )
    cap = MAX_INSTANCE_DESTINATIONS if owner_id is None else MAX_READER_DESTINATIONS
    if len(existing) >= cap:
        _refuse("That is as many places as Venona will send to. Remove one first.")


def subscribable(audience: str) -> tuple[NoticeType, ...]:
    return tuple(t for t in NOTICE_TYPES.values() if t.audience == audience)


def check_notice_type(notice_type: str, *, audience: str) -> NoticeType:
    """A key this audience is allowed to subscribe to.

    The audience check is the authorization, not a tidiness rule: without it a
    reader could subscribe their own webhook to `access.requested` and receive
    every stranger's address and self-description as they arrive.
    """
    declared = NOTICE_TYPES.get(notice_type)
    if declared is None or declared.audience != audience:
        _refuse("Venona does not send that.")
    return declared


def target_hint(kind: str, target: str) -> str:
    """What a screen may show of a stored target.

    A webhook URL is a credential -- anyone holding a Slack incoming URL can
    post to that channel -- so no route returns it and this is what stands in
    its place: enough to tell two destinations apart, not enough to use. An
    email address is not a secret and is shown whole, because a reader checking
    where their brief goes needs to actually read it.
    """
    if kind == "email":
        return target
    without_scheme = target.split("://", 1)[-1]
    host = without_scheme.split("/", 1)[0]
    tail = target.rstrip("/")[-4:]
    return f"{host}/…{tail}"


def destinations_for(conn: db.Conn, *, notice_type: str, user_id: str | None) -> list[db.Row]:
    """Every active destination that asked for this notice.

    `user_id` is required for a reader notice and refused for an instance one,
    checked rather than assumed: the two mistakes it prevents are sending a
    reader's brief to the operations channel, and sending an instance failure to
    whoever happens to be reading.
    """
    declared = NOTICE_TYPES.get(notice_type)
    if declared is None:
        return []
    if declared.audience == READER:
        if not user_id:
            return []
        return repo.subscribed_destinations(conn, notice_type=notice_type, owner_id=user_id)
    if user_id:
        return []
    return repo.subscribed_destinations(conn, notice_type=notice_type, owner_id=None)
