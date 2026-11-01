"""Whether a notice is ever RAISED, which nothing tested before this file.

`test_notify.py` covers the subsystem thoroughly -- rendering, dedupe, strikes,
audience, escaping, the offline no-op -- and every one of those tests passed
while the feature was, in production, silent. A webhook was configured, its test
message arrived, and nothing else ever did.

The reason was upstream of everything that suite looks at. `run.succeeded` and
`run.failed` were raised from `scheduler.report_daily`, which is called by the
06:00 timer and nothing else, so a run started from the Runs page said nothing
when it failed and a lookup could not say anything at all. The switch offering
this read "A run did not finish".

So these tests assert the CALL SITE, not the renderer: that finishing a run of
each kind puts the right row in the outbox, and that the kinds which should stay
quiet do. A test send is deliberately not enough to prove any of it -- the last
test here is why.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from venona.db import core as db
from venona.db.repositories import notify as repo
from venona.notify import policy
from venona.services import run_launcher, run_service, run_types

pytestmark = pytest.mark.anyio

NOW = "2026-09-01T00:00:00.000Z"


# ── harness ──────────────────────────────────────────────────────────


class _FakeState:
    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.llm = object()
        self.llm_light = object()
        self.graph = None


class _FakeApp:
    def __init__(self, settings: Any) -> None:
        self.state = _FakeState(settings)


class _FakeRequest:
    def __init__(self, settings: Any) -> None:
        self.app = _FakeApp(settings)


@pytest.fixture
def launchable(conn: db.Conn, database_url: str, monkeypatch):
    """A launchable request over a real migrated database, with delivery faked.

    Delivery is replaced rather than pointed somewhere harmless because
    `deliver_now` builds a real http client and posts to whatever `target` says.
    The recorder it leaves behind is also assertable: "was a send even
    attempted" is its own question, since `_runner` skips delivery entirely when
    nothing was queued.
    """
    from venona import scheduler
    from venona.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(
        type(settings), "database_url", property(lambda s: database_url), raising=False
    )

    sends: list[Any] = []

    async def _fake_deliver(app, *, conn=None) -> int:
        sends.append(app)
        return 0

    monkeypatch.setattr(scheduler, "deliver_now", _fake_deliver)

    original = run_service.registry
    run_service.registry = run_service.RunRegistry()
    try:
        yield _FakeRequest(settings), database_url, sends
    finally:
        run_service.registry = original


def _destination(conn: db.Conn, *subscribe: str) -> str:
    destination_id = "dest:call-sites"
    repo.insert_destination(
        conn,
        destination_id=destination_id,
        owner_kind="instance",
        owner_id=None,
        kind="webhook",
        label="ops",
        target="https://hooks.slack.com/services/T0/B0/abcd",
        created_by=None,
        now=NOW,
    )
    for notice_type in subscribe:
        repo.subscribe(conn, destination_id=destination_id, notice_type=notice_type, now=NOW)
    conn.commit()
    return destination_id


async def _run(request, *, kind: str, fail: bool = False, stage: str | None = None) -> str:
    """Launch one run of `kind` and wait for it to reach a terminal row."""

    async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        if fail:
            raise RuntimeError("the model refused")
        return run_launcher.LaunchOutcome(data={})

    started = await run_launcher.launch(
        request, kind=kind, params={}, work=work, stage=stage or kind
    )
    handle = run_service.registry.handle(started["run_id"])
    if handle and handle.task:
        await asyncio.wait_for(handle.task, timeout=5.0)
    return started["run_id"]


def _queued(database_url: str) -> list[str]:
    probe = db.connect(database_url)
    try:
        return [
            row["notice_type"]
            for row in probe.execute(
                "SELECT notice_type FROM notify_outbox ORDER BY created_at"
            ).fetchall()
        ]
    finally:
        probe.close()


# ── what a finished run says ─────────────────────────────────────────


async def test_a_failed_light_run_says_so(launchable) -> None:
    """The gap this file exists for.

    A lookup or a glossary sweep can fail after having already spent real money,
    and before this it was reported nowhere outbound at all: `report_daily` was
    the only raiser and only the 06:00 timer calls it.
    """
    request, database_url, sends = launchable
    conn = db.connect(database_url)
    try:
        _destination(conn, "run.failed")
    finally:
        conn.close()

    await _run(request, kind="lookup", fail=True)

    assert _queued(database_url) == ["run.failed"]
    assert sends, "a queued notice must be delivered rather than left for the timer"


async def test_a_clean_light_run_says_nothing(launchable) -> None:
    """`run.succeeded` is a dead-man's switch and only the reading may ring it.

    A ping per lookup would make the GAPS meaningless, and the gaps are the
    entire value: silence is what reports a host that never came back.
    """
    request, database_url, sends = launchable
    conn = db.connect(database_url)
    try:
        _destination(conn, "run.succeeded", "run.failed")
    finally:
        conn.close()

    await _run(request, kind="lookup")

    assert _queued(database_url) == []
    assert not sends, "nothing was queued, so nothing should have opened a connection"


async def test_a_clean_pipeline_run_pings_however_it_was_started(launchable) -> None:
    """`on_demand` is the button on the Runs page, and it heartbeats like the
    timer's run because it is the same work."""
    request, database_url, _ = launchable
    conn = db.connect(database_url)
    try:
        _destination(conn, "run.succeeded")
    finally:
        conn.close()

    await _run(request, kind="on_demand", stage="fetch")

    assert _queued(database_url) == ["run.succeeded"]


async def test_a_cancelled_run_says_nothing(launchable) -> None:
    """Somebody pressed the button a moment ago and is still on the page.

    Reporting their own decision back to them is noise, and training a reader to
    ignore these costs the alarms that matter.
    """
    request, database_url, _ = launchable
    conn = db.connect(database_url)
    try:
        _destination(conn, "run.failed", "run.succeeded")
    finally:
        conn.close()

    async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        raise run_launcher.Cancelled

    started = await run_launcher.launch(
        request, kind="lookup", params={}, work=work, stage="lookup"
    )
    handle = run_service.registry.handle(started["run_id"])
    if handle and handle.task:
        await asyncio.wait_for(handle.task, timeout=5.0)

    assert _queued(database_url) == []


async def test_a_kind_that_announces_its_own_ending_is_not_reported_twice(launchable) -> None:
    """`reports_failure=False` is the whole of this, and the export kind is why.

    An export announces both its endings as `export.finished`, with copy that
    can name what the run was for. The generic notice would be a second message
    about one event and the worse-written of the two.
    """
    assert not run_types.type_for("export").reports_failure

    request, database_url, _ = launchable
    conn = db.connect(database_url)
    try:
        _destination(conn, "run.failed")
    finally:
        conn.close()

    await _run(request, kind="export", fail=True)

    assert _queued(database_url) == []


# ── the words that reach the reader ──────────────────────────────────


def test_the_failure_names_the_kind_and_never_the_status() -> None:
    """`succeeded_partial` is a column value, and it was in the message.

    The old wording spliced `status` straight into a sentence, so a partial run
    told whoever read it "Venona started reading and stopped at
    succeeded_partial" -- half schema, half wrong about what the run did.
    """
    from venona.notify.types import NOTICE_TYPES

    notice = NOTICE_TYPES["run.failed"].render(
        {"run_id": "run:1", "kind": "lookup", "status": "succeeded_partial"}
    )
    assert notice.title == "Definition did not finish"
    blob = " ".join((notice.title, *notice.body))
    assert "succeeded_partial" not in blob
    assert blob.count("_") == 0, "no column value may reach a reader"


def test_the_heartbeat_tells_the_unattended_run_apart() -> None:
    """A week of hand-started runs must not read like a healthy schedule."""
    from venona.notify.types import NOTICE_TYPES

    scheduled = NOTICE_TYPES["run.succeeded"].render({"run_id": "run:1", "kind": "daily"})
    asked_for = NOTICE_TYPES["run.succeeded"].render({"run_id": "run:2", "kind": "on_demand"})
    assert scheduled.title != asked_for.title


def test_every_run_kind_renders_a_failure_without_raising() -> None:
    """`type_for` never raises and neither may this: an unknown kind is a real
    row (`services/seed.py` writes runs no registry entry covers)."""
    from venona.notify.types import NOTICE_TYPES

    for kind in (*run_types.RUN_TYPES, "a-kind-from-the-future", ""):
        notice = NOTICE_TYPES["run.failed"].render({"run_id": "run:1", "kind": kind})
        assert notice.title.strip()
        assert notice.body


# ── why a green test send proves nothing ─────────────────────────────


def test_a_struck_out_destination_is_silent_while_its_test_send_still_works(
    conn: db.Conn,
) -> None:
    """The divergence that made the production fault undiagnosable.

    A test send goes straight through the channel (`outbox.as_claim`), so it
    reads `destination_target` and never looks at `status`. Both real paths
    filter on `status = 'active'`. An operator whose channel had struck itself
    out therefore saw a green test and no messages, with nothing on the screen
    connecting the two.

    Pinned rather than fixed here: the fix is on the screen, which has to say a
    destination is struck out. This holds the underlying asymmetry so that a
    later change to either query has to face it.
    """
    destination_id = _destination(conn, "run.failed")
    for _ in range(policy.MAX_INSTANCE_DESTINATIONS):
        strikes = repo.record_failure(
            conn, destination_id=destination_id, error="gone", limit=3, now=NOW
        )
        if strikes >= 3:
            break
    conn.commit()

    assert repo.destination_target(conn, destination_id=destination_id), (
        "the test send still finds a target, which is why it still reports success"
    )
    assert policy.destinations_for(conn, notice_type="run.failed", user_id=None) == [], (
        "every real raise must skip a struck-out destination"
    )


async def test_a_clean_run_is_reported_from_the_outcome_in_hand(launchable) -> None:
    """The branch that cannot use its own local `status`.

    `orchestrator.execute_run` writes its own terminal row and `_runner` returns
    early, so the `status` local in that frame is still the pessimistic "failed"
    it was initialised to. Reading the ROW back is what makes one report cover
    both shapes of run, and getting it wrong is invisible: every daily and every
    run started from the Runs page would report a clean night as a failure.
    """
    request, database_url, _ = launchable
    conn = db.connect(database_url)
    try:
        _destination(conn, "run.succeeded", "run.failed")
    finally:
        conn.close()

    async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        # What `execute_run` does: report what it reached and let the launcher
        # write the row. The status reaches the notice from the outcome in hand.
        return run_launcher.LaunchOutcome(status="succeeded", outcome="delivered")

    started = await run_launcher.launch(request, kind="on_demand", params={}, work=work)
    handle = run_service.registry.handle(started["run_id"])
    if handle and handle.task:
        await asyncio.wait_for(handle.task, timeout=5.0)

    assert _queued(database_url) == ["run.succeeded"], (
        "the pessimistic local would have reported this clean run as a failure"
    )


async def test_the_daily_reports_its_run_once_though_two_places_raise_it(
    conn: db.Conn,
) -> None:
    """`scheduler.report_daily` and `run_launcher` both raise for the daily.

    The scheduler's carries `retry_at`, which the launcher cannot know: a
    provider outage re-fires the daily and the reader wants that fact before
    they go looking for a cause. The launcher's covers every other kind. Both
    firing is intended, and the dedupe key is what makes it harmless -- so this
    pins the collapse rather than the call count. Removing either raise to
    "fix" the duplication would silently drop one of those two properties.
    """
    from venona import notify

    _destination(conn, "run.failed")
    first = notify.raise_run_notice(
        conn,
        run_id="run:daily-1",
        kind="daily",
        status="succeeded_partial",
        error="the model was unreachable",
        retry_at="2026-09-11T06:30:00Z",
    )
    second = notify.raise_run_notice(
        conn, run_id="run:daily-1", kind="daily", status="succeeded_partial"
    )

    assert (first, second) == (1, 0)
    assert conn.execute("SELECT COUNT(*) AS n FROM notify_outbox").fetchone()["n"] == 1
    payload = conn.execute("SELECT payload FROM notify_outbox").fetchone()["payload"]
    assert "2026-09-11T06:30:00Z" in payload, "the richer raise is the one that survives"
