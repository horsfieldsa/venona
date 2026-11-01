"""The scheduler's editable half: `apply_schedule`, `schedule_status`, and the
trigger endpoint.

What is NOT here: the cron actually firing at 06:00 -- that is APScheduler's
test suite's problem. What is ours is everything that reads or re-points the
job at runtime, because every path here has a mode where the scheduler object
is absent (offline, or an app that never ran the lifespan) and the absence must
read as a state, not an exception.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from helpers import NOW
from venona import scheduler as sched
from venona.db import core as db
from venona.db.repositories import run as run_repo
from venona.settings import get_settings
from venona.timeutil import format_seconds

#: The shipped ceiling, read rather than restated: a test that hardcodes 3 stops
#: testing "retries spent" the day the default moves.
_RETRY_MAX = get_settings().schedule.outage_retry_max


def _app(scheduler=None, *, cron="0 6 * * *", timezone="UTC", enabled=True):
    state = SimpleNamespace(
        settings=SimpleNamespace(
            schedule=SimpleNamespace(cron=cron, timezone=timezone, enabled=enabled)
        )
    )
    if scheduler is not None:
        state.scheduler = scheduler
    return SimpleNamespace(state=state)


@pytest.fixture
async def running_scheduler():
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.start()
    yield scheduler
    scheduler.shutdown(wait=False)


# ── apply_schedule ───────────────────────────────────────────────────


async def test_enabling_the_schedule_adds_the_job(running_scheduler) -> None:
    app = _app(running_scheduler, enabled=True)
    assert running_scheduler.get_job(sched.DAILY_JOB_ID) is None
    sched.apply_schedule(app)
    assert running_scheduler.get_job(sched.DAILY_JOB_ID) is not None


async def test_the_daily_job_is_a_coroutine(running_scheduler) -> None:
    """⭐ How the first scheduled fire in production died at exactly 06:00.

    `AsyncIOScheduler` runs only COROUTINE jobs on the loop; a plain function
    goes to the default ThreadPoolExecutor, where `asyncio.create_task` finds
    no running loop and the job raises `RuntimeError` -- while the boot-time
    `catch_up` path (awaited on the loop) keeps working and masks it.
    """
    import inspect

    app = _app(running_scheduler, enabled=True)
    sched.apply_schedule(app)

    job = running_scheduler.get_job(sched.DAILY_JOB_ID)
    assert inspect.iscoroutinefunction(job.func), (
        "a sync job runs in a threadpool thread, not on the loop"
    )


async def test_disabling_the_schedule_removes_the_job(running_scheduler) -> None:
    app = _app(running_scheduler, enabled=True)
    sched.apply_schedule(app)
    app.state.settings.schedule.enabled = False
    sched.apply_schedule(app)
    assert running_scheduler.get_job(sched.DAILY_JOB_ID) is None


async def test_disabling_an_already_absent_job_is_not_an_error(running_scheduler) -> None:
    sched.apply_schedule(_app(running_scheduler, enabled=False))
    assert running_scheduler.get_job(sched.DAILY_JOB_ID) is None


async def test_a_cron_change_moves_the_next_fire_time(running_scheduler) -> None:
    app = _app(running_scheduler, cron="0 6 * * *")
    sched.apply_schedule(app)
    before = running_scheduler.get_job(sched.DAILY_JOB_ID).next_run_time
    app.state.settings.schedule.cron = "30 14 * * *"
    sched.apply_schedule(app)
    after = running_scheduler.get_job(sched.DAILY_JOB_ID).next_run_time
    assert after != before
    assert (after.hour, after.minute) == (14, 30)


def test_apply_schedule_without_a_scheduler_is_a_no_op() -> None:
    """Offline, or a test app that never ran the lifespan. `save_config` calls
    this unconditionally, so the absent attribute must be a state."""
    sched.apply_schedule(_app())


# ── schedule_status ──────────────────────────────────────────────────


def test_status_computes_the_next_fire_from_the_cron_when_there_is_no_job() -> None:
    """The hosted shape: `enabled: false`, an external cron drives the trigger
    endpoint, and the yaml cron is still what "next run" should read."""
    status = sched.schedule_status(_app(cron="0 6 * * *", timezone="UTC", enabled=False))
    expected = CronTrigger.from_crontab("0 6 * * *", timezone="UTC").get_next_fire_time(
        None, datetime.now(UTC)
    )
    assert status["enabled"] is False
    assert status["cron"] == "0 6 * * *"
    assert status["timezone"] == "UTC"
    assert status["next_run_at"] == format_seconds(expected)


def test_a_bad_cron_reports_no_next_run_rather_than_raising() -> None:
    """This dict feeds `/api/health`, the one endpoint that must answer when
    things are broken."""
    status = sched.schedule_status(_app(cron="not a cron"))
    assert status["next_run_at"] is None


async def test_status_prefers_the_live_job(running_scheduler) -> None:
    """The job is what will actually fire; the yaml is only intent. When both
    exist and disagree -- an edit that has not been applied -- report the job."""
    app = _app(running_scheduler, cron="0 6 * * *")
    sched.apply_schedule(app)
    app.state.settings.schedule.cron = "0 12 * * *"
    status = sched.schedule_status(app)
    job_time = running_scheduler.get_job(sched.DAILY_JOB_ID).next_run_time
    assert status["next_run_at"] == format_seconds(job_time)


# ── the wire ─────────────────────────────────────────────────────────


async def test_health_carries_the_schedule_block(api_client) -> None:
    """Pins the field names: `response_model` filters, so a typo'd key here
    would vanish from the wire silently. Also exercises the no-scheduler path
    -- the fixture app never ran the lifespan."""
    resp = await api_client.get("/api/health")
    assert resp.status_code == 200
    schedule = resp.json()["schedule"]
    assert set(schedule) == {"enabled", "cron", "timezone", "next_run_at", "last_daily_at"}
    assert isinstance(schedule["enabled"], bool)
    assert schedule["cron"]


async def test_the_trigger_endpoint_reports_a_started_run(api_client, api_app, monkeypatch) -> None:
    async def fake_run_daily(app, *, request=None):
        assert app is api_app
        assert request is not None
        return sched.DailyTrigger("started", run_id="run:x")

    monkeypatch.setattr(sched, "run_daily", fake_run_daily)
    resp = await api_client.post("/api/runs/schedule/trigger")
    assert resp.status_code == 202
    assert resp.json() == {"outcome": "started", "run_id": "run:x"}


async def test_the_trigger_endpoint_reports_a_skip_as_success(api_client, monkeypatch) -> None:
    """202, not 409: the caller is a cron, and a non-2xx is a failed job worth
    retrying -- straight back into the same busy lane."""

    async def fake_run_daily(app, *, request=None):
        return sched.DailyTrigger("skipped", reason="run:elsewhere is still active")

    monkeypatch.setattr(sched, "run_daily", fake_run_daily)
    resp = await api_client.post("/api/runs/schedule/trigger")
    assert resp.status_code == 202
    assert resp.json() == {"outcome": "skipped", "reason": "run:elsewhere is still active"}


async def test_the_trigger_endpoint_maps_a_start_failure_to_500(api_client, monkeypatch) -> None:
    async def fake_run_daily(app, *, request=None):
        return sched.DailyTrigger("error", reason="RuntimeError: boom")

    monkeypatch.setattr(sched, "run_daily", fake_run_daily)
    resp = await api_client.post("/api/runs/schedule/trigger")
    assert resp.status_code == 500
    assert "boom" in resp.json()["detail"]


# ── run_daily's own skip path, unpatched ─────────────────────────────


async def test_run_daily_skips_when_the_research_lane_is_leased(
    conn: db.Conn, database_url: str, monkeypatch
) -> None:
    """The cross-process guard, exercised for real: a leased `running` research
    row in another connection's session is exactly what a run in another
    process looks like."""
    from venona.settings import get_settings

    conn.execute(
        "INSERT INTO runs (id, kind, status, started_at) VALUES (?,?,?,?)",
        ("run:elsewhere", "on_demand", "running", NOW),
    )
    run_repo.claim_lease(conn, "run:elsewhere")
    conn.commit()

    settings = get_settings()
    monkeypatch.setattr(type(settings), "database_url", property(lambda s: database_url))
    result = await sched.run_daily(SimpleNamespace(state=SimpleNamespace(settings=settings)))
    assert result.outcome == "skipped"
    assert "run:elsewhere" in result.reason


# ── re-firing after a provider outage ────────────────────────────────


@pytest.mark.parametrize(
    ("tripped", "status", "outcome", "retry", "due"),
    [
        (True, "succeeded_partial", "delivered", 0, True),
        (True, "failed", None, 2, True),
        # The executor waited the outage out entirely: nothing to redo.
        (True, "succeeded", "delivered", 0, False),
        # Degraded for some other reason; the circuit never opened on this run.
        (False, "succeeded_partial", "delivered", 0, False),
        # Retries spent; the next scheduled run picks up the backlog.
        (True, "succeeded_partial", "delivered", _RETRY_MAX, False),
        # ⭐ A quiet window is not made less quiet by fetching it again in half
        # an hour. Before `runs.outcome` this run was indistinguishable from
        # the degraded one above and re-fired, and re-spent, for nothing.
        (True, "succeeded_partial", "nothing_to_do", 0, False),
    ],
)
def test_a_daily_earns_a_retry_only_when_the_outage_was_on_its_watch(
    tripped, status, outcome, retry, due
) -> None:
    assert (
        sched.outage_retry_due(
            tripped=tripped,
            status=status,
            outcome=outcome,
            retry=retry,
            settings=get_settings(),
        )
        is due
    )


def test_the_retry_ceiling_is_config_and_zero_turns_it_off() -> None:
    """The one scheduled behaviour that spends unattended has an off switch.

    A module constant meant the only way to stop an unattended re-spend was a
    deploy. `config/settings.yaml` is editable over HTTP, and the ceiling is
    read at the point of use, so an operator who sets it to 0 in Admin is not
    billed for tonight.
    """
    settings = get_settings()
    always_otherwise_due = {
        "tripped": True,
        "status": "failed",
        "outcome": None,
        "retry": 0,
    }
    assert sched.outage_retry_due(**always_otherwise_due, settings=settings)

    off = settings.model_copy(
        update={"schedule": settings.schedule.model_copy(update={"outage_retry_max": 0})}
    )
    assert not sched.outage_retry_due(**always_otherwise_due, settings=off)


@pytest.mark.asyncio
async def test_an_outage_retry_is_one_pending_job_not_a_stack(running_scheduler) -> None:
    """A retry that itself meets the outage moves the one pending fire rather
    than queueing a second pipeline behind the first."""
    app = _app(running_scheduler)

    settings = get_settings()
    first = sched.schedule_outage_retry(app, retry=1, settings=settings)
    second = sched.schedule_outage_retry(app, retry=2, settings=settings)

    assert first is not None and second is not None
    jobs = [j for j in running_scheduler.get_jobs() if j.id == sched.RETRY_JOB_ID]
    assert len(jobs) == 1
    delay = (jobs[0].next_run_time - datetime.now(UTC)).total_seconds()
    ceiling = get_settings().schedule.outage_retry_delay_seconds
    assert ceiling - 60 < delay <= ceiling


def test_an_outage_retry_without_a_scheduler_is_a_no_op() -> None:
    """Offline, or an app that never ran the lifespan: nothing to fire from."""
    assert sched.schedule_outage_retry(_app(), retry=1, settings=get_settings()) is None


# ── catch-up is a production behaviour ───────────────────────────────


async def test_catch_up_does_nothing_outside_production(monkeypatch, database_url) -> None:
    """⭐ A boot check that spends money.

    `catch_up` fires a full research run whenever the last successful daily is
    over a day old, which is right for the case it was written for: a container
    down over 06:00 does not misfire and does not log. What it cannot tell is
    that a developer's laptop coming up looks identical — so `make dev`, the
    step CLAUDE.md's definition of done tells everyone to run, started a paid
    pipeline on any machine whose daily was stale. Measured 2026-08-25: $0.20
    on a boot nobody asked to spend anything.
    """
    from types import SimpleNamespace

    from venona import scheduler

    monkeypatch.delenv("VENONA_ENV", raising=False)
    started: list[str] = []

    async def _should_not_run(_app, **_kw):
        started.append("daily")
        raise AssertionError("catch_up started a run outside production")

    monkeypatch.setattr(scheduler, "run_daily", _should_not_run)
    # A STALE SUCCESSFUL DAILY MUST EXIST, or this passes for the wrong reason:
    # with no row at all `catch_up` returns early on "nothing on record" and
    # never reaches the decision under test.
    monkeypatch.setattr(scheduler, "CATCH_UP_AFTER_HOURS", 0.0)
    conn = db.connect(database_url)
    try:
        conn.execute(
            "INSERT INTO runs (id, kind, status, started_at) "
            "VALUES ('run:stale','daily','succeeded','2020-01-01T00:00:00.000Z')"
        )
        conn.commit()
    finally:
        conn.close()

    app = SimpleNamespace(
        state=SimpleNamespace(settings=SimpleNamespace(database_url=database_url))
    )

    await scheduler.catch_up(app)

    assert not started


async def test_catch_up_still_runs_on_a_real_deployment(monkeypatch, database_url) -> None:
    """The gate must not disable the behaviour it is bounding. A container that
    was down over 06:00 still catches up — that is what `catch_up` is for, and a
    brief silently a day stale is the failure it exists to prevent."""
    from types import SimpleNamespace

    from venona import scheduler

    monkeypatch.setenv("VENONA_ENV", "production")
    started: list[str] = []

    async def _record(_app, **_kw):
        started.append("daily")

    monkeypatch.setattr(scheduler, "run_daily", _record)

    # `_report` opens its own connection and sends: the fact under test is that
    # the catch-up FIRES on a real deployment, and reporting it is a separate
    # path with its own tests.
    async def _no_report(*_a, **_kw):
        return None

    monkeypatch.setattr(scheduler, "_report", _no_report)
    monkeypatch.setattr(scheduler, "CATCH_UP_AFTER_HOURS", 0.0)

    conn = db.connect(database_url)
    try:
        conn.execute(
            "INSERT INTO runs (id, kind, status, started_at) "
            "VALUES ('run:old','daily','succeeded','2020-01-01T00:00:00.000Z')"
        )
        conn.commit()
    finally:
        conn.close()

    app = SimpleNamespace(
        state=SimpleNamespace(settings=SimpleNamespace(database_url=database_url))
    )
    await scheduler.catch_up(app)

    assert started == ["daily"]
