"""What venona is willing to tell somebody about, declared in one table.

The shape is `services/run_types.py`'s, and for the same reason ADR 0187 gives
it: a type declares what it SAYS, not merely that it exists. Before this, one
`notify.alert(title, body)` call site owned the words at the moment it fired, so
the two quiet failures a clean daily can carry -- a struck-out source and a run
of empty nights -- had to share one title, because there was one channel and no
way to be told about one without the other.

TWO AUDIENCES, ONE MECHANISM. An INSTANCE notice is about the machine and goes
wherever its administrator points it. A READER notice is about one person's
reading and goes wherever that person points it. Nothing else differs: the
registry, the destinations, the outbox and both channels are shared. `audience`
is what keeps a reader from subscribing to the instance's failures and an
administrator from seeing where a reader has their brief sent.

THE WORDS SHIP TO THE BROWSER. `label` and `detail` are what a switch says on
`/admin/notifications` and `/settings/notifications`, and they reach the client
on the wire exactly as `RunType.outcomes` does. There is no map of these keys to
English anywhere in `web/src`, deliberately: four surfaces invented run outcome
words independently and one of them was wrong, which is the scar
`lib/runOutcome.ts` carries.

So the same three constraints apply to every string here, and
`tests/test_notify.py` holds them: no `undefined` (it is
`scripts/ui_smoke.py`'s marker for a JS value rendered into the DOM, and a
legitimate label carrying the word disarms that check for a whole page), no em
or en dashes, and no vocabulary out of this repo. A reader has never heard of a
lane, a corpus or an outbox, and an administrator is a reader too.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

# The shape a rendered notice takes, shared with the hub so both services'
# channels are written against one definition. The REGISTRY below is this
# service's own: it addresses two audiences and the hub addresses one.
from notices.types import Facts, Notice

# The run registry, for the words a run notice needs: what kind of run it was
# and what its status means in a sentence. Safe to import at module scope --
# `run_types` is a leaf that imports nothing from this package, and nothing here
# binds a value from it at import time.
from venona.services import run_types

#: Who a notice is for. An instance notice is about the machine; a reader notice
#: is about one person's reading.
INSTANCE = "instance"
READER = "reader"
AUDIENCES = (INSTANCE, READER)


@dataclass(frozen=True)
class NoticeType:
    """One row of the table."""

    key: str
    #: `INSTANCE` or `READER`.
    audience: str
    #: What the switch says. Reader words, sentence case, no trailing period.
    label: str
    #: The line under the switch. What subscribing actually gets you, and when.
    detail: str
    #: Whether a NEW destination starts subscribed to this.
    #:
    #: True for the instance's failures, because an operator who has just told
    #: venona where to reach them means "tell me when something breaks", and
    #: making them tick eight boxes to get that is a trap. False for both reader
    #: notices: nobody has asked to be written to, and a first delivery nobody
    #: opted into is the worst possible introduction to a feature.
    default_on: bool
    #: What makes a repeat the SAME notice, or None to let every occurrence
    #: stand alone. Returning a key means at most one delivery per destination
    #: for that value, enforced by a unique index rather than by a check.
    dedupe: Callable[[Facts], str | None] = lambda facts: None
    #: Facts to `Notice`. Reads named keys; never `**facts` into a template.
    render: Callable[[Facts], Notice] = field(repr=False, default=lambda facts: Notice(title=""))


def _run_failed(facts: Facts) -> Notice:
    """What a run that ended badly says, whatever kind of run it was.

    THE KIND IS NAMED. Every kind of run can raise this and most of them are not
    reading -- a definition lookup, a glossary sweep, one reader's own brief --
    so the title takes its subject from the registry rather than assuming the
    nightly pipeline.

    THE STATUS IS NOT. `succeeded_partial` is a column value, and a message is
    read by somebody who has never seen the column. `STATUS_LINES` is the table
    every run page already renders from, so a reader meets one vocabulary
    whether the message found them or they opened the run themselves.
    """
    declared = run_types.type_for(str(facts.get("kind") or ""))
    error = (facts.get("error") or "").strip()
    retry = facts.get("retry_at")
    body = [run_types.STATUS_LINES.get(str(facts.get("status") or ""), "The run could not finish.")]
    if error:
        body.append(error)
    if retry:
        body.append(f"The model was unreachable during the run. Venona tries again at {retry}.")
    return Notice(
        title=f"{declared.label} did not finish",
        body=tuple(body),
        link=f"/admin/runs/{facts['run_id']}" if facts.get("run_id") else "/admin/runs",
        link_label="Open the run",
    )


def _could_not_start(facts: Facts) -> Notice:
    return Notice(
        title="A run could not start",
        body=(
            "The daily reading did not begin at all, so there is no run to look at.",
            (facts.get("error") or "").strip() or "No reason was reported.",
        ),
        link="/admin/runs",
        link_label="Open runs",
    )


def _caught_up(facts: Facts) -> Notice:
    return Notice(
        title="Catching up a missed day",
        body=(
            "Venona was not running at its usual time, so it is reading now.",
            f"The last day it finished was {facts.get('stale_for', 'some time')} ago.",
        ),
        link="/admin/runs",
        link_label="Open runs",
    )


def _sources_disabled(facts: Facts) -> Notice:
    ids = tuple(facts.get("source_ids") or ())
    return Notice(
        title=f"{len(ids)} of your sources stopped answering",
        body=(
            "Venona tried each of these three times and has stopped asking. "
            "The run still finished and still looks normal, which is why this "
            "is worth saying out loud.",
            ", ".join(ids),
            "They stay off until someone turns them back on.",
        ),
        link="/admin/config/sources",
        link_label="Open sources",
    )


def _corpus_quiet(facts: Facts) -> Notice:
    nights = facts.get("nights", 0)
    return Notice(
        title=f"Nothing new for {nights} days running",
        body=(
            "The last few runs each found nothing worth writing about.",
            "Every publication may still be answering. Check what you have told "
            "Venona matters, and how far back it reads, before you check the sources.",
        ),
        link="/admin/config/relevance_profile",
        link_label="Open the relevance profile",
    )


def _model_unavailable(facts: Facts) -> Notice:
    return Notice(
        title="The model is not answering",
        body=(
            f"{facts.get('failures', 'Several')} calls failed in a row, so Venona "
            "has stopped trying for a few minutes.",
            (facts.get("error") or "").strip(),
            "Worth knowing: the health check reports a credential as present even "
            "when it has expired, so this page will look fine either way.",
        ),
        link="/admin/runs",
        link_label="Open runs",
    )


def _access_requested(facts: Facts) -> Notice:
    return Notice(
        title="Someone asked for access",
        body=(
            f"{facts.get('email', 'Someone')} asked for {facts.get('role', 'access')}.",
            "They wrote this about themselves:",
        ),
        # Up to 2000 characters a stranger typed into a public form. It stays in
        # `quoted` and out of `body` so no channel can splice it into markup.
        quoted=(facts.get("coverage") or "").strip() or None,
        link="/admin/people",
        link_label="Open the queue",
    )


def _run_succeeded(facts: Facts) -> Notice:
    """The heartbeat, for a pipeline run however it was started.

    THE SCHEDULED ONE IS WORDED APART, and that is the whole value of this
    notice. It exists so SILENCE is the alarm: a watchdog pointed at the nightly
    ping is the only thing that can report a host which never came back, because
    nothing on that host is running to report it. That only works while a reader
    can tell the unattended ping from one somebody pressed a button for. Worded
    the same, a week of hand-started runs reads exactly like a healthy schedule.
    """
    if run_types.type_for(str(facts.get("kind") or "")).trigger == "scheduled":
        return Notice(
            title="The daily run finished",
            body=("Venona read, scored and wrote today's brief.",),
            link="/",
            link_label="Read it",
        )
    return Notice(
        title="A run finished",
        body=("Venona read, scored and wrote the brief you asked for.",),
        link="/",
        link_label="Read it",
    )


def _shared_brief(facts: Facts) -> Notice:
    return Notice(
        title=facts.get("title") or "Today's brief is ready",
        body=(facts.get("dek") or "",),
        link=f"/briefs/{facts['brief_id']}" if facts.get("brief_id") else "/",
        link_label="Read it",
    )


def _personal_brief(facts: Facts) -> Notice:
    return Notice(
        title="Your brief is ready",
        # No link to the document itself: a reader's brief has no address of its
        # own, by the same decision that keeps every personal row off an
        # id-addressable route. The app's front page is where it is read.
        body=("Venona has written your own reading of today, tailored to what you follow.",),
        link="/",
        link_label="Read it",
    )


def _export_finished(facts: Facts) -> Notice:
    """One type for both endings, because they are one question.

    `sources.disabled` and `corpus.quiet` are two types because they are fixed
    in two different places and an operator may reasonably want one and not the
    other. This is the opposite case: "did my copy work" has one answer and one
    place to go and look, so splitting it would be two switches for one thought.
    """
    filename = facts.get("filename")
    if facts.get("failed"):
        reason = (facts.get("error") or "").strip()
        return Notice(
            title="A copy for Obsidian did not finish",
            body=(
                "Venona started writing everything it has read as a folder and stopped.",
                reason or "No reason was reported.",
            ),
            link=f"/admin/runs/{facts['run_id']}" if facts.get("run_id") else "/admin/data",
            link_label="Open the run",
        )
    notes = facts.get("notes")
    written = f"{notes} notes" if notes else "Everything Venona has read"
    return Notice(
        title="A copy for Obsidian is ready",
        body=(
            f"{written}, written as a folder of linked notes"
            + (f" called {filename}." if filename else "."),
            "It is kept on this machine until newer copies push it out.",
        ),
        link="/admin/data",
        link_label="Download it",
    )


#: Every notice type, in the order both settings screens show them.
NOTICE_TYPES: dict[str, NoticeType] = {
    "run.failed": NoticeType(
        key="run.failed",
        audience=INSTANCE,
        label="A run did not finish",
        detail=(
            "Any run that stops partway or stops with an error, whether Venona "
            "started it at its usual time or you did."
        ),
        default_on=True,
        # One per run. A run reports once, but the scheduler's outage retry can
        # report the same run id again after a second attempt.
        dedupe=lambda f: f"run:{f.get('run_id')}:{f.get('status')}" if f.get("run_id") else None,
        render=_run_failed,
    ),
    "export.finished": NoticeType(
        key="export.finished",
        audience=INSTANCE,
        label="A copy for Obsidian finished",
        detail=(
            "Venona wrote everything it has read as a folder you can open in Obsidian, "
            "or stopped before it could."
        ),
        # An operator starts this by hand and then goes and does something else
        # for the several minutes it takes, which is exactly the shape of person
        # this table's `default_on` argument is about.
        default_on=True,
        # One per run. A run reports once either way.
        dedupe=lambda f: f"export:{f.get('run_id')}" if f.get("run_id") else None,
        render=_export_finished,
    ),
    "run.could_not_start": NoticeType(
        key="run.could_not_start",
        audience=INSTANCE,
        label="A run could not start",
        detail="The daily reading did not begin, so there is no run to look at.",
        default_on=True,
        render=_could_not_start,
    ),
    "run.caught_up": NoticeType(
        key="run.caught_up",
        audience=INSTANCE,
        label="A missed day is being caught up",
        detail="Venona was not running at its usual time and has started late.",
        default_on=True,
        render=_caught_up,
    ),
    "sources.disabled": NoticeType(
        key="sources.disabled",
        audience=INSTANCE,
        label="A source stopped answering",
        detail=(
            "A publication failed three times and was dropped. The run still "
            "finishes and still looks normal, so nothing else would tell you."
        ),
        default_on=True,
        # Keyed on WHICH sources are off, not on the run: the same set being
        # still-off tomorrow is not news, and a fourth one going quiet is.
        dedupe=lambda f: "sources:" + ",".join(sorted(f.get("source_ids") or ())),
        render=_sources_disabled,
    ),
    "corpus.quiet": NoticeType(
        key="corpus.quiet",
        audience=INSTANCE,
        label="Several quiet days in a row",
        detail=(
            "Venona found nothing new worth writing about for as many days as "
            "you have set. A single quiet day is normal and is never reported."
        ),
        default_on=True,
        # One per streak length, so a streak that keeps growing says so once per
        # day it grows rather than once per run.
        dedupe=lambda f: f"quiet:{f.get('nights')}",
        render=_corpus_quiet,
    ),
    "model.unavailable": NoticeType(
        key="model.unavailable",
        audience=INSTANCE,
        label="The model stopped answering",
        detail="Calls failed repeatedly, so Venona paused rather than kept paying.",
        default_on=True,
        render=_model_unavailable,
    ),
    "access.requested": NoticeType(
        key="access.requested",
        audience=INSTANCE,
        label="Someone asked for access",
        detail="A person filled in the request form and is waiting on a decision.",
        default_on=True,
        dedupe=lambda f: f"access:{f.get('request_id')}" if f.get("request_id") else None,
        render=_access_requested,
    ),
    "run.succeeded": NoticeType(
        key="run.succeeded",
        audience=INSTANCE,
        label="Every reading that finishes cleanly",
        detail=(
            "A ping on success, for a full reading rather than a lookup. Point "
            "it at a service that watches for the ping to STOP, and silence "
            "becomes the alarm: a machine that never came back cannot send you "
            "anything itself."
        ),
        default_on=True,
        dedupe=lambda f: f"ok:{f.get('run_id')}" if f.get("run_id") else None,
        render=_run_succeeded,
    ),
    "brief.shared.published": NoticeType(
        key="brief.shared.published",
        audience=READER,
        label="When the shared brief is ready",
        detail="Sent once today's brief has been written, whoever it was written for.",
        default_on=False,
        # The shared edition, never the calendar day (ADR 0185). A second run on
        # the same edition delivers nothing twice.
        dedupe=lambda f: f"brief:{f.get('brief_id')}" if f.get("brief_id") else None,
        render=_shared_brief,
    ),
    "brief.personal.ready": NoticeType(
        key="brief.personal.ready",
        audience=READER,
        label="When your own brief is ready",
        detail="Sent once Venona has written your tailored reading of the day.",
        default_on=False,
        dedupe=lambda f: f"mine:{f.get('brief_id')}" if f.get("brief_id") else None,
        render=_personal_brief,
    ),
}


def for_audience(audience: str) -> tuple[NoticeType, ...]:
    """Every type one audience may subscribe to, in table order."""
    return tuple(t for t in NOTICE_TYPES.values() if t.audience == audience)
