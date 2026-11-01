"""Outbound notices: what venona tells people, and how it reaches them.

THE GAP THIS CLOSES, unchanged from the thirty-line version this grew out of.
Everything venona knows about its own failures lives in Postgres and is rendered
in the app: a failed run, a source struck out, an open circuit, a `claude` token
that expired. All of it is discoverable by opening the brief and looking. That
was fine when the app and the operator were on the same machine and the run
happened while they watched. On a server the run happens at 06:00 and nobody is
there, and the sharp case is still the quiet one -- `sources/registry.py`
auto-disables a feed after three consecutive failures, `auto_disabled` is sticky
until someone clears it, and three quiet nights leave a thin brief with nothing
anywhere saying why.

WHAT CHANGED. There was one destination, in one environment variable, subscribed
to everything by construction. Two questions that shape could not answer: "tell
me, but not about that", and "did my brief get sent". Both are now rows -- a
destination, a subscription, a delivery -- which also makes a second audience
free. An administrator points the instance's failures at an operations channel;
a reader points their own brief at their own inbox. Same registry, same queue,
same channels.

THE OFFLINE NO-OP IS THE FIRST THING `raise_notice` DOES, and it returns before
QUEUING rather than merely before sending. `VENONA_OFFLINE=1` must reach no
network and produce byte-identical output; a replayed run that queued mail is
neither offline nor reproducible, and a row is output.

WHY THIS IS NOT UNDER `services/`. That package is guarded by
`lint-determinism`, which forbids constructing an httpx client, reading the
wall clock, minting a uuid4 and jittering with `random` -- and delivery does all
four. This is a side channel that touches no run output, but the guard is a
deliberately dumb grep and the right answer to "my file is special" is almost
never an exemption. It lives at the top level, beside `scheduler.py`, which is
outbound-facing for the same reason.

A NOTIFIER MUST NEVER FAIL THE THING IT REPORTS ON. `raise_notice` swallows
everything, exactly as `alert` always did: a notifier that can fail a run turns a
warning about a degraded run into a failed one, which is backwards. That
argument covers RAISING and not SENDING, which is why the outbox is allowed to
record a failure and say so.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

from venona.db import core as db
from venona.db.repositories import notify as repo
from venona.notify import outbox, policy
from venona.notify.types import NOTICE_TYPES, Notice, NoticeType
from venona.services import run_types

log = logging.getLogger("venona.notify")

__all__ = [
    "Notice",
    "NoticeType",
    "NOTICE_TYPES",
    "adopt_legacy_url",
    "is_offline",
    "outbox",
    "policy",
    "raise_notice",
    "raise_run_notice",
    "repo",
]


def is_offline() -> bool:
    """Empty means do nothing, which is also what offline means.

    Kept as its own function, and read from the environment rather than from
    `Settings`, because this runs from the scheduler, from a router and from
    inside `llm/` -- three callers with three different ideas about whether they
    hold a settings object, and none of them should have to.
    """
    return os.environ.get("VENONA_OFFLINE", "").strip().lower() in ("1", "true", "yes", "on")


def _new_id() -> str:
    return f"ntf:{uuid.uuid4().hex[:24]}"


def public_url() -> str:
    """Where this instance answers, for resolving a notice's link.

    Read at SEND time, not at raise time, and that is not a detail: a notice
    stores its link as a path, so an instance that moves to a real domain starts
    sending working links for notices queued before the move. Read from the
    environment for the same reason `is_offline` is -- the drainer holds no
    settings object, and `VENONA_PUBLIC_URL` is where every other consumer of
    this fact reads it.
    """
    return os.environ.get("VENONA_PUBLIC_URL", "").strip()


def raise_notice(
    conn: db.Conn,
    notice_type: str,
    *,
    user_id: str | None = None,
    **facts: Any,
) -> int:
    """Tell whoever asked to be told. Returns how many deliveries were queued.

    NEVER RAISES. Every caller is reporting something that has already happened,
    usually inside a `finally` or after a commit, and an exception here would
    convert "we could not tell you" into "the thing you were being told about
    failed".

    `user_id` addresses a reader notice and must be absent for an instance one;
    `policy.destinations_for` enforces that rather than trusting the call site.
    """
    if is_offline():
        return 0
    try:
        declared = NOTICE_TYPES.get(notice_type)
        if declared is None:
            log.warning("notify: no such notice type %r", notice_type)
            return 0
        destinations = policy.destinations_for(conn, notice_type=notice_type, user_id=user_id)
        if not destinations:
            return 0
        notice = declared.render(facts)
        with db.transaction(conn, what="notify_enqueue"):
            return outbox.enqueue(
                conn,
                notice_type=notice_type,
                notice=notice,
                destinations=destinations,
                dedupe_key=declared.dedupe(facts),
                new_id=_new_id,
            )
    except Exception:  # noqa: BLE001 - see the docstring
        log.warning("notify: could not raise %r", notice_type, exc_info=True)
        return 0


def announce_shared_brief(conn: db.Conn) -> int:
    """Tell every reader who asked that today's brief has been written.

    Reads the brief itself rather than taking it as an argument: both callers
    are the tail of a run that has just delivered one, and neither holds the
    row. The dedupe key is the brief id, so a rewrite of the same edition
    delivers nothing twice (ADR 0185: the unit of currency is the edition, never
    the calendar day).

    ONE READER AT A TIME, because `policy.destinations_for` has no "everybody"
    case to call. A mistake here can tell one person twice; it cannot mail one
    reader's settings to the readership.
    """
    if is_offline():
        return 0
    try:
        from venona.services import brief_service

        brief_id = brief_service.latest_brief_id(conn)
        if not brief_id:
            return 0
        from venona.db.repositories import brief as brief_repo

        row = brief_repo.select_briefs_by_id(conn, brief_id).fetchone()
        queued = 0
        # `entitled_only` closes the delivery-side paywall bypass: this function
        # does not merely announce, it SENDS THE BRIEF, so an unentitled reader
        # with an address configured would keep receiving the product in full.
        # No amount of front-end gating finds that, because it is not a page.
        from venona.api import authz
        from venona.pipeline.types import utc_now_iso

        for user_id in repo.readers_subscribed(
            conn,
            notice_type="brief.shared.published",
            entitled_only=authz.billing_enforced(),
            # The local brake. Read here rather than in the repository, which
            # never asks what time it is.
            now=utc_now_iso(),
        ):
            queued += raise_notice(
                conn,
                "brief.shared.published",
                user_id=user_id,
                brief_id=brief_id,
                title=row["title"] if row else None,
                dek=row["dek"] if row else None,
            )
        return queued
    except Exception:  # noqa: BLE001 - see `raise_notice`
        log.warning("notify: could not announce the shared brief", exc_info=True)
        return 0


def raise_run_notice(
    conn: db.Conn,
    *,
    run_id: str,
    kind: str,
    status: str,
    error: str | None = None,
    retry_at: str | None = None,
) -> int:
    """Queue whatever a finished run has earned. Returns deliveries queued.

    WHY THE DECISION IS HERE AND NOT AT THE CALL SITE. There are a dozen places
    a run is launched from and one place every run ENDS, so this is reachable
    from the ending and the launchers never have to remember it. A rule that
    each caller has to apply is a rule that holds only for the callers somebody
    remembered: the expensive silences are a glossary sweep that died having
    already spent the money, and one reader's own brief failing while the switch
    offering both reads "A run did not finish".

    THREE KINDS OF SILENCE, each deliberate:

      * A clean run pings only if its kind is a `heartbeat` -- the four pipeline
        flavours. The ping's whole value is that a watchdog can alarm on its
        ABSENCE, and a ping per lookup makes the gaps meaningless.
      * A cancelled run says nothing. Somebody pressed the button a moment ago
        and is still looking at the screen; telling them is noise, and training
        a reader to ignore these costs the alarms that matter.
      * A kind that announces its own ending (`reports_failure=False`) is left
        to do it. Its copy can name what the run was for, which this one cannot.

    Sync, and it takes a connection rather than opening one, because both
    callers already hold one. Delivery is deliberately NOT here: the two callers
    disagree about when it is safe to hold a lane slot open for a POST. The
    COUNT is what they act on -- zero means nobody asked to hear about this, so
    a caller can skip opening a connection and an http client per finished run.
    """
    declared = run_types.type_for(kind)
    if status == "succeeded":
        if declared.heartbeat:
            return raise_notice(conn, "run.succeeded", run_id=run_id, kind=kind)
        return 0
    if status == "cancelled" or not declared.reports_failure:
        return 0
    return raise_notice(
        conn,
        "run.failed",
        run_id=run_id,
        kind=kind,
        status=status,
        error=error,
        retry_at=retry_at,
    )


def announce_personal_brief(
    conn: db.Conn, *, user_id: str, brief_id: str, reader_started: bool
) -> int:
    """Tell one reader their own brief is written.

    NOT WHEN THEY STARTED IT THEMSELVES. A reader who pressed the button is
    watching the page render; a message about it arrives while they are already
    reading the thing it announces, which reads as noise and teaches them to
    ignore the next one. The warm pass and the auto path are the cases somebody
    is not watching, and those are the ones worth a message.
    """
    if reader_started:
        return 0
    return raise_notice(conn, "brief.personal.ready", user_id=user_id, brief_id=brief_id)


def adopt_legacy_url(conn: db.Conn, *, url: str) -> str | None:
    """Take `VENONA_NOTIFY_URL` into a destination row, once.

    The variable was the whole of this system and is now one way to have
    configured it, so an instance that had it set must not go quiet on the
    release that moves the setting into the app. Adopting is idempotent by the
    only test that matters here: if the instance already has a webhook
    destination, someone has been to the screen and their choices win.

    Deliberately subscribed to every instance notice regardless of `default_on`.
    The variable meant "tell me everything", and silently narrowing what an
    operator already had is a worse surprise than a message they can turn off.
    """
    cleaned = (url or "").strip()
    if not cleaned:
        return None
    if any(d["kind"] == "webhook" for d in repo.instance_destinations(conn)):
        return None
    try:
        target = policy.check_webhook(cleaned)
    except policy.DestinationError as exc:
        log.warning("notify: VENONA_NOTIFY_URL was not adopted: %s", exc)
        return None
    now = outbox._iso(outbox._now())
    destination_id = f"dest:{uuid.uuid4().hex[:24]}"
    with db.transaction(conn, what="notify_adopt"):
        repo.insert_destination(
            conn,
            destination_id=destination_id,
            owner_kind="instance",
            owner_id=None,
            kind="webhook",
            label="Carried over from the server settings",
            target=target,
            created_by=None,
            now=now,
        )
        for declared in policy.subscribable("instance"):
            repo.subscribe(conn, destination_id=destination_id, notice_type=declared.key, now=now)
    log.info(
        "notify: adopted VENONA_NOTIFY_URL into a destination. "
        "Manage it at /admin/notifications; the variable is no longer read."
    )
    return destination_id
