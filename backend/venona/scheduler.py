"""The daily run.

APScheduler in-process rather than cron or Celery: this is a single-user
application, and the scheduled path must be *exactly* the same code as the
manual one. A separate cron entry invoking the CLI would drift from the API path
and only diverge when something breaks at 6am.

"Exactly the same code" has to mean it: the daily goes through
`run_launcher.launch` like every other caller rather than writing the run row,
submitting to the registry and calling `execute_run` itself. See `run_daily`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from venona import notify
from venona.db import core as db
from venona.db.repositories import pipeline_scheduler as repo
from venona.pipeline.orchestrator import execute_run
from venona.services import run_launcher, run_service, run_types
from venona.settings import Settings, is_production
from venona.timeutil import format_millis, format_seconds

log = logging.getLogger("venona.scheduler")

DAILY_JOB_ID = "venona-daily"
RETRY_JOB_ID = "venona-daily-retry"
DELIVERY_JOB_ID = "venona-delivery"
CREDENTIAL_JOB_ID = "venona-credential-sweep"

# How often the outbox is drained. A minute is the longest an alert should wait
# on the timer alone, and every path that raises one also asks for a send
# immediately -- so this is the backstop for a retry and for anything raised by
# a process that did not get to deliver it, not the normal route.
DELIVERY_INTERVAL_SECONDS = 60
#: The same minute, and for a reason of its own -- see `_credential_pass`.
CREDENTIAL_INTERVAL_SECONDS = 60


@dataclass
class DailyTrigger:
    """What happened when the daily was asked to fire.

    Three-valued rather than an exception: `fire()` and `catch_up` run as bare
    tasks with nobody awaiting them, so a raise from here would surface only as
    an unretrieved-task-exception warning. The trigger endpoint is the caller
    that actually wants the distinction.
    """

    outcome: str  # "started" | "skipped" | "error"
    run_id: str | None = None
    reason: str | None = None


# Every reference we hold, so the event loop does not collect a running task.
# `asyncio.create_task` keeps only a weak reference; a task whose only reference
# was the argument to `create_task` can be garbage-collected mid-flight, and the
# documented symptom is work that silently stops happening.
_tasks: set[asyncio.Task] = set()


def _housekeeping(conn) -> None:
    """The daily sweep of rows nothing else deletes.

    Here rather than in the lifespan because a container that stays up for a
    month never runs the lifespan again, and `/register` is unauthenticated:
    the table it writes to grows for as long as the process lives. Before the
    lane check, so it happens on the nights a run is skipped too -- housekeeping
    that only runs when everything else went well is housekeeping that stops
    the first time it is needed.
    """
    from venona.services import oauth_service

    try:
        purged = oauth_service.purge_expired(conn, now=format_seconds(datetime.now(UTC)))
    except Exception:  # noqa: BLE001 - housekeeping must never stop the daily
        log.warning("oauth housekeeping failed", exc_info=True)
        return
    if purged["codes"] or purged["clients"]:
        log.info(
            "oauth housekeeping: removed %d expired code(s), %d unused registration(s)",
            purged["codes"],
            purged["clients"],
        )


async def run_daily(app, *, request=None, retry: int = 0) -> DailyTrigger:
    """Fire a scheduled run, unless one is already going.

    `request` is the real HTTP request when the trigger endpoint is the caller,
    so `run_launcher._actor` can attribute the run to its principal; the timer
    path has no principal and passes nothing.

    `retry` is how many times this is a re-fire after a provider outage; see
    `outage_retry_due` for what earns one.

    Skipping rather than queueing is deliberate: if yesterday's run is somehow
    still going at 06:00 tomorrow, starting a second one makes the problem
    worse. That reasoning is about the RESEARCH lane specifically -- a light run
    in flight, a lookup someone left open in a tab, is no reason to skip the
    night's work. C11.

    TWO THINGS ONLY BITE UNATTENDED, and both are why the body reads as it does.
    The conflict question is `run_service.lane_conflict`, the cross-process one
    `api/routers/runs.py` and `cli.py` both ask, rather than
    `registry.active_run_id`, which is an in-process lock a `venona run` in a
    terminal walks straight past: two pipelines, two Budgets, one `documents`
    table. And the start goes through `run_launcher.launch`, which has no gap
    between writing the `runs` row and claiming the lane slot -- anything
    raising in such a gap leaves the row `running` owned by the LIVE uvicorn
    pid, which reaping skips by design and `lane_conflict` treats as busy by
    design, so every later run 409s until the process restarts.
    """
    settings = app.state.settings

    # An unconfigured instance has nothing to research -- no sources, no
    # profile, only skeletons -- and nothing gated the 06:00 timer on that
    # until a scaffolded instance fired a doomed run overnight. Checked at
    # FIRE time, so the daily resumes by itself the moment setup writes the
    # real descriptor; nobody has to remember a settings flag.
    from venona.services import setup_service

    if not setup_service.is_configured(settings):
        reason = "instance is not set up yet (placeholder descriptor)"
        log.info("skipping scheduled run: %s", reason)
        return DailyTrigger("skipped", reason=reason)

    conn = db.connect(settings.database_url)
    try:
        db.migrate(conn)
        _housekeeping(conn)
        conflict = run_service.lane_conflict(conn, lane=run_types.RESEARCH)
    finally:
        conn.close()

    if conflict:
        reason = f"{conflict['id']} ({conflict['kind']}) is still active"
        log.warning("skipping scheduled run: %s", reason)
        return DailyTrigger("skipped", reason=reason)

    from venona.llm.prompts import load_profile

    llm = getattr(app.state, "llm", None)
    breaker = getattr(llm, "breaker", None)
    opens_before = breaker.opens if breaker is not None else 0

    async def work(ctx: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        outcome = await execute_run(
            scope=ctx.scope,
            run_id=ctx.run_id,
            settings=settings,
            conn=ctx.conn,
            graph=app.state.graph,
            kind="daily",
        )
        status = getattr(outcome, "status", "unknown")
        verdict = getattr(outcome, "outcome", None)
        tripped = breaker is not None and breaker.opens > opens_before
        retry_at = None
        if outage_retry_due(
            tripped=tripped, status=status, outcome=verdict, retry=retry, settings=settings
        ):
            retry_at = schedule_outage_retry(app, retry=retry + 1, settings=settings)
        # Nobody is looking at 06:00, so the run says something outward however
        # it went; `report_daily` carries why success reports too.
        await report_daily(
            app,
            run_id=ctx.run_id,
            status=status,
            outcome=verdict,
            error=getattr(outcome, "error", None),
            retry_at=retry_at,
        )
        # Warm the personal briefs of recently seen readers, so the morning
        # visit finds theirs already written. Light lane, so the fan-out is
        # paced by `light_concurrency` and cannot hold the research slot;
        # best-effort, because a failed warm pass degrades to lazy generation
        # on first visit, never to a failed daily.
        try:
            from venona.services import personal_brief

            await personal_brief.warm_recent_readers(
                app,
                settings=settings,
                now_iso=format_millis(datetime.now(UTC)),
                scheduled=True,
            )
        except Exception:  # noqa: BLE001 - the daily's own report must not depend on this
            log.exception("personal brief warm pass failed")
        # And tell whoever asked to be told that it landed. After the warm pass,
        # so a reader whose own brief was written in it gets both messages in
        # the order the two documents actually appeared.
        await announce_brief(app)
        return run_launcher.LaunchOutcome(
            status=outcome.status,
            outcome=outcome.outcome,
            brief_id=outcome.brief_id,
            error=outcome.error,
        )

    params: dict[str, Any] = {"scheduled": True}
    if retry:
        params["retry"] = retry

    try:
        started = await run_launcher.launch(
            request if request is not None else SimpleNamespace(app=app),
            kind="daily",
            params=params,
            work=work,
            profile_hash=load_profile(settings).short_hash,
            migrate=True,
            context={"graph": app.state.graph, "profile": load_profile(settings)},
        )
    except run_launcher.LaneBusy as exc:
        # SAID OUT LOUD, not merely logged. A skipped daily and a failed daily
        # cost the reader exactly the same thing -- there is no brief this
        # morning -- and the only difference is that this one had a reason
        # nobody was there to read. It reuses `run.could_not_start` rather than
        # earning a type of its own: from the far end these are one event, "the
        # reading did not begin", and splitting them would be a second switch
        # for one thought.
        log.warning("skipping scheduled run: %s", exc)
        await _report(app, "run.could_not_start", error=str(exc))
        return DailyTrigger("skipped", reason=str(exc))
    except Exception as exc:  # noqa: BLE001 - a failure to START must be visible
        log.exception("scheduled run could not start")
        await _report(app, "run.could_not_start", error=f"{type(exc).__name__}: {exc}")
        return DailyTrigger("error", reason=f"{type(exc).__name__}: {exc}")

    log.info("scheduled run %s starting", started.get("run_id"))
    return DailyTrigger("started", run_id=started.get("run_id"))


def outage_retry_due(
    *, tripped: bool, status: str, outcome: str | None, retry: int, settings: Settings
) -> bool:
    """Whether a finished daily earns a re-fire.

    Four conditions, deliberately: the circuit must have opened DURING this run
    (a run that never met the outage has nothing to redo), the run must have
    failed to finish clean, there must be something a second attempt could get,
    and the retries must not be spent.

    The status pair is spelled out rather than written as `!= "succeeded"`,
    which would re-fire -- and re-spend -- on the first daily that ran perfectly
    and found nothing. `failed` is in the pair as well as `succeeded_partial`: a
    daily that failed under a provider outage is the strongest case there is for
    a second attempt.

    The `nothing_to_do` condition is the one that costs real money. A quiet
    window is not made less quiet by fetching it again half an hour later.

    The ceiling comes from `settings.schedule.outage_retry_max`, read HERE
    rather than bound at import: this is the one scheduled behaviour that
    spends unattended, `config/settings.yaml` is editable over HTTP, and a
    module constant would mean the operator who just set it to 0 in Admin still
    gets billed for tonight.
    """
    return (
        tripped
        and status in ("succeeded_partial", "failed")
        and outcome != "nothing_to_do"
        and retry < settings.schedule.outage_retry_max
    )


def schedule_outage_retry(app, *, retry: int, settings: Settings) -> str | None:
    """Re-fire the daily once, later. Returns when, ISO-8601, or None if there
    is no scheduler to do it (offline, or an app that never ran the lifespan).

    One job id with `replace_existing`, so a retry that itself earns a retry
    moves the one pending fire rather than stacking a second.
    """
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler is None:
        return None
    when = datetime.now(UTC) + timedelta(seconds=settings.schedule.outage_retry_delay_seconds)

    async def fire() -> None:
        task = asyncio.create_task(run_daily(app, retry=retry))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)

    scheduler.add_job(
        fire,
        DateTrigger(run_date=when),
        id=RETRY_JOB_ID,
        replace_existing=True,
        misfire_grace_time=3600,
    )
    stamp = format_seconds(when)
    log.warning("daily run met a provider outage; retry %d scheduled for %s", retry, stamp)
    return stamp


def _quiet_streak(conn, *, limit: int) -> int:
    """How many of the most recent finished dailies found nothing to write.

    Counts from the newest backwards and stops at the first run that delivered,
    so one good night resets it. Reads at most `limit` rows because that is the
    only number the answer is compared against.
    """
    streak = 0
    for row in repo.select_recent_daily_outcomes(conn, limit).fetchall():
        if row["outcome"] != "nothing_to_do":
            break
        streak += 1
    return streak


async def report_daily(
    app,
    *,
    run_id: str,
    status: str,
    error: str | None,
    outcome: str | None = None,
    retry_at: str | None = None,
) -> None:
    """The unattended half: say something outward when nobody is watching.

    A heartbeat on success, so that SILENCE is the alarm -- a crashed container
    or a host that never came back produces no alert of its own, because nothing
    is running to send one. `status` is the health question and it is the one
    that decides between heartbeat and alarm, so a clean run pings whatever it
    found: a quiet day is a correct night's work, and paging on it would train
    whoever reads these to stop reading them, which is the same damage as
    calling a partial run failed. ADR 0183.

    AUTO-DISABLED SOURCES ARE REPORTED EVEN ON A CLEAN RUN, and that is the
    point. `sources/registry.py` strikes a feed out after three consecutive
    failures and `auto_disabled` is STICKY until somebody puts that feed back in
    the rotation. A run with two dead feeds still succeeds, still produces a
    brief, and still looks entirely normal -- so three quiet nights is all it
    takes for the corpus to hollow out with nothing having said so.

    AND SO IS A RUN OF QUIET NIGHTS, which is the other half of that same
    worry. A rotation can be entirely healthy -- no strikes, no warnings, every
    feed answering -- and simply stop producing anything worth writing about,
    because the profile drifted, because a source changed what it publishes, or
    because the window is wrong. No single run can tell that from a genuinely
    quiet Tuesday; `schedule.quiet_nights_alert` of them in a row is not a
    quiet Tuesday. `runs.outcome` is what makes the question askable in SQL.
    """
    settings = app.state.settings
    quiet_limit = settings.schedule.quiet_nights_alert
    disabled: list[str] = []
    quiet = 0
    conn = None
    try:
        conn = db.connect(settings.database_url)
        disabled = sorted(row["source_id"] for row in repo.select_source_health(conn))
        if quiet_limit and outcome == "nothing_to_do":
            quiet = _quiet_streak(conn, limit=quiet_limit)
    except Exception:  # noqa: BLE001 - reporting must never fail a run
        # Swallowed so the run still reports, but not silently: this read IS
        # the auto-disabled-sources alarm, and a heartbeat that quietly lost it
        # says "clean night" on a corpus that is hollowing out.
        log.warning("could not read source health for the daily report", exc_info=True)
    if conn is None:
        return

    try:
        # TWO QUIET FAILURES, TWO NOTICES, which is most of why a registry
        # exists. A struck-out feed is a source problem; a healthy rotation
        # finding nothing is a profile or window problem. They are fixed in
        # different places and worth different amounts to different operators,
        # so an operator can mute one and keep the other. A single alert
        # carrying both has to name which it is in its own title, and cannot
        # be subscribed to by halves.
        if disabled:
            notify.raise_notice(
                conn,
                "sources.disabled",
                source_ids=disabled,
            )
        if quiet >= quiet_limit > 0:
            notify.raise_notice(conn, "corpus.quiet", nights=quiet)

        # The run's own verdict, RAISED HERE AS WELL AS IN `run_launcher`, for
        # one fact the launcher does not have: `retry_at`. A provider outage
        # re-fires the daily, and whoever reads the failure wants to know that
        # before they go looking for a cause. The launcher raises for this same
        # run a moment later and the dedupe key makes that second raise a no-op,
        # which is precisely what the key is for. Do not resolve the apparent
        # duplication by deleting one of them: the launcher's covers every other
        # kind of run, and this one carries the retry.
        notify.raise_run_notice(
            conn,
            run_id=run_id,
            kind="daily",
            status=status,
            error=error,
            retry_at=retry_at,
        )
        await deliver_now(app, conn=conn)
    finally:
        conn.close()


async def catch_up(app) -> None:
    """Run once at startup if the last daily is older than a whole interval.

    APScheduler uses the default `MemoryJobStore` and recomputes `next_run_time`
    from *now* at every `add_job`, and `misfire_grace_time` only helps a
    scheduler that was ALIVE through the missed window. So a container that was
    down over 06:00 -- a redeploy, an OOM, a host reboot -- does not run, does
    not misfire, and does not log. On a laptop you notice; on a VPS the first
    symptom is a brief that is quietly a day stale.
    """
    # PRODUCTION ONLY, and this is a SPEND gate rather than a correctness one.
    #
    # Everything below is right for the case it was written for: a container
    # that was down over 06:00 does not misfire, does not log, and needs
    # catching up. What it cannot tell is that a developer's laptop coming up
    # looks identical -- so `make dev`, which CLAUDE.md's definition of done
    # tells everyone to run, started a full research pipeline against the
    # subscription on any machine whose daily was over a day old. Measured
    # 2026-08-25: $0.20 before it was noticed, on a boot check nobody asked to
    # spend anything.
    #
    # `venona run` is how you deliberately catch up locally. It says so, it is
    # in front of a person, and it does not happen because a server started.
    if not is_production():
        log.info("catch-up skipped: not a production deployment (`venona run` to do it by hand)")
        return

    settings = app.state.settings
    conn = db.connect(settings.database_url)
    try:
        row = repo.select_runs(conn).fetchone()
    finally:
        conn.close()

    if row is None:
        log.info("no successful daily run on record; leaving the schedule to fire normally")
        return

    from venona.timeutil import hours_since

    stale_for = hours_since(row["started_at"])
    if stale_for is None or stale_for < CATCH_UP_AFTER_HOURS:
        return

    log.warning("last successful daily run was %.1fh ago; catching up", stale_for)
    await _report(app, "run.caught_up", stale_for=f"{stale_for:.1f} hours")
    await run_daily(app)


# A day plus slack. Tight enough that a missed night is caught the moment the
# container comes back, loose enough that an ordinary restart at 09:00 does not
# re-run the work that already happened at 06:00.
CATCH_UP_AFTER_HOURS = 30.0


def _add_daily_job(scheduler: AsyncIOScheduler, app, settings) -> None:
    """Point the daily job at the schedule currently in settings.

    Must stay loop-free: `apply_schedule` calls this from a threadpool thread,
    where `add_job` is safe (it takes the jobstore lock and wakes the loop via
    `call_soon_threadsafe`) but anything touching `asyncio` directly is not.

    `fire` MUST be a coroutine. `AsyncIOScheduler` runs only coroutine jobs on
    the loop; a plain function goes to the default ThreadPoolExecutor, where
    `asyncio.create_task` finds no running loop and the job dies with
    `RuntimeError` at exactly 06:00 -- which is how the first scheduled fire on
    The host failed while the boot-time `catch_up` (awaited on the loop) kept
    working and masked it.
    """

    async def fire() -> None:
        task = asyncio.create_task(run_daily(app))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)

    scheduler.add_job(
        fire,
        CronTrigger.from_crontab(settings.schedule.cron, timezone=settings.schedule.timezone),
        id=DAILY_JOB_ID,
        replace_existing=True,
        # A missed window (laptop asleep) runs late rather than being skipped,
        # but only within the hour -- a two-day-old run is not worth firing.
        # All three constrain only a scheduler that stayed alive, and `fire`
        # returns immediately, so `max_instances=1` cannot see an overlap:
        # `lane_conflict` in `run_daily` is what prevents one.
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )


async def announce_brief(app) -> None:
    """Say the shared brief has landed, and send it.

    Its own connection because both callers are inside a launcher closure whose
    connection belongs to the run, and this outlives the run's own reporting by
    however long a handful of POSTs take.
    """
    if notify.is_offline():
        return
    try:
        conn = db.connect(app.state.settings.database_url)
    except Exception:  # noqa: BLE001 - announcing must never fail a run
        log.warning("could not reach the database to announce the brief", exc_info=True)
        return
    try:
        if notify.announce_shared_brief(conn):
            await deliver_now(app, conn=conn)
    except Exception:  # noqa: BLE001 - same
        log.warning("could not announce the brief", exc_info=True)
    finally:
        conn.close()


async def _report(app, notice_type: str, **facts) -> None:
    """Raise one notice and send it, for a caller that holds no connection.

    `report_daily` already has one open and passes it in; these two are the
    paths where the thing being reported is precisely that the machinery did not
    get far enough to have one.
    """
    if notify.is_offline():
        return
    try:
        conn = db.connect(app.state.settings.database_url)
    except Exception:  # noqa: BLE001 - reporting must never fail its caller
        log.warning("could not reach the database to report %s", notice_type, exc_info=True)
        return
    try:
        notify.raise_notice(conn, notice_type, **facts)
        await deliver_now(app, conn=conn)
    finally:
        conn.close()


async def deliver_now(app, *, conn=None) -> int:
    """Send whatever is queued, right now. Returns how many went.

    Called straight after a notice is raised so an alert is not a minute late,
    and by the interval job so nothing that failed the first time is stranded.
    One function for both, which is the same argument this module opens with
    for the daily: the scheduled path has to be exactly the code the manual one
    runs, or the two only disagree at 6am.

    NEVER RAISES. Every caller -- the daily report, `run_launcher`, a router,
    the interval job -- is reporting on something that has already happened.

    Its own HTTP client per pass, closed in the `finally`. A long-lived one
    would have to be owned by app state and handed to a timer that outlives any
    single request, and a pass is a handful of POSTs a minute at most.
    """
    if notify.is_offline():
        return 0
    from venona.sources.http import build_http_client

    settings = app.state.settings
    owned = conn is None
    client = None
    try:
        if owned:
            conn = db.connect(settings.database_url)
        client = build_http_client(settings)
        return await notify.outbox.drain(conn, client, base=notify.public_url())
    except Exception:  # noqa: BLE001 - delivery must never fail its caller
        log.warning("could not deliver queued notices", exc_info=True)
        return 0
    finally:
        if client is not None:
            await client.aclose()
        if owned and conn is not None:
            conn.close()


async def _delivery_pass(app) -> None:
    """One tick: reclaim what a dead process was holding, then send.

    The reclaim comes first and runs every tick rather than at startup. Every
    deploy of the api is stop then start, so a claim held when the process died
    is the ordinary case rather than a crash path -- and a startup-only sweep
    would strand anything claimed by a worker that died between two deploys.
    """
    if notify.is_offline():
        return
    try:
        conn = db.connect(app.state.settings.database_url)
    except Exception:  # noqa: BLE001 - a timer must never take the loop down
        log.warning("delivery pass could not reach the database", exc_info=True)
        return
    try:
        reclaimed = notify.outbox.reclaim(conn)
        if reclaimed:
            log.info("notify: put %d stalled deliveries back on the queue", reclaimed)
        await deliver_now(app, conn=conn)
    except Exception:  # noqa: BLE001 - same
        log.warning("delivery pass failed", exc_info=True)
    finally:
        conn.close()


async def _credential_pass(app) -> None:
    """Keep a credential that no model call is going to notice (ADR 0234).

    Every other rotation is caught right after the call that caused it. This is
    for the one with no call after it: a `codex login` in a shell, the only way
    that service signs in, which otherwise lives in the container filesystem
    until the next deploy throws it away.

    Its own job rather than a line in `_delivery_pass`, which returns early when
    notifications are offline; a credential must be kept either way.
    """
    try:
        from venona.llm.harness import known, resolve
        from venona.services import harness_service
        from venona.settings import Paths

        for key in known():
            harness_service.capture_if_changed(Paths(), resolve(key))
    except Exception:  # noqa: BLE001 - a timer must never take the loop down
        log.warning("credential sweep failed", exc_info=True)


def _add_credential_job(scheduler: AsyncIOScheduler, app) -> None:
    scheduler.add_job(
        _credential_pass,
        "interval",
        seconds=CREDENTIAL_INTERVAL_SECONDS,
        args=[app],
        id=CREDENTIAL_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )


def _add_delivery_job(scheduler: AsyncIOScheduler, app) -> None:
    scheduler.add_job(
        _delivery_pass,
        "interval",
        seconds=DELIVERY_INTERVAL_SECONDS,
        args=[app],
        id=DELIVERY_JOB_ID,
        replace_existing=True,
        # A pass that overruns must not stack behind itself: the claim is
        # atomic, so a second pass would take different rows and double the
        # outbound rate exactly when a receiver is already slow.
        max_instances=1,
        coalesce=True,
    )


def _build_scheduler(timezone: str) -> AsyncIOScheduler:
    """Never let a hand-edited timezone take the whole process down.

    `settings.yaml` is user-editable and this call happens in the lifespan, so
    a typo in `schedule.timezone` is the difference between one wrong schedule
    and an instance that will not boot. UTC and a warning is the recoverable
    half of that trade; `/api/health` still reports the timezone as written, so
    the mismatch is visible to whoever has to fix it.
    """
    try:
        return AsyncIOScheduler(timezone=timezone)
    except Exception:  # noqa: BLE001 - any tz library rejection, not one class
        log.warning("schedule.timezone %r is not a timezone; using UTC", timezone, exc_info=True)
        return AsyncIOScheduler(timezone=UTC)


def start_scheduler(app) -> AsyncIOScheduler | None:
    """Start the scheduler; add the daily job only if the schedule is enabled.

    The scheduler is constructed and started even when `schedule.enabled` is
    false, so that a later config save can enable it with `add_job` alone:
    `save_config` runs in a threadpool thread, and `AsyncIOScheduler.start()`
    needs a running loop there is no way to reach from one. Offline is the
    exception -- it sits in `FROZEN_TOP`, so it can never flip at runtime and
    `None` stays `None` for the life of the process.
    """
    settings = app.state.settings
    if settings.offline:
        # Offline mode replays fixtures; a timer firing against them is noise.
        log.info("scheduler disabled in offline mode")
        return None

    scheduler = _build_scheduler(settings.schedule.timezone)
    scheduler.start()

    # BEFORE the enabled check, and not beside the daily. Turning the daily off
    # says nothing about wanting to stop being told: an on-demand run still
    # fails, a source still strikes out, and somebody still fills in the access
    # form. A drainer that only ran on instances with a schedule would go quiet
    # on exactly the instances somebody is driving by hand.
    _add_delivery_job(scheduler, app)
    _add_credential_job(scheduler, app)

    if not settings.schedule.enabled:
        log.info("scheduler idle: schedule disabled in settings")
        return scheduler

    _add_daily_job(scheduler, app, settings)
    log.info("scheduler started: %s (%s)", settings.schedule.cron, settings.schedule.timezone)

    catch_up_task = asyncio.create_task(catch_up(app))
    _tasks.add(catch_up_task)
    catch_up_task.add_done_callback(_tasks.discard)

    return scheduler


def apply_schedule(app) -> None:
    """Re-point the daily job at whatever the settings now say.

    Sync and threadpool-safe on purpose: the caller is `save_config`, a sync
    route. Enabling via a save does NOT run `catch_up` -- a settings edit that
    silently starts a forty-minute pipeline is worse than waiting for the next
    window, and the trigger endpoint exists for the impatient case.
    """
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler is None:
        # Offline, or an app that never ran the lifespan (tests).
        return
    settings = app.state.settings
    if settings.schedule.enabled:
        _add_daily_job(scheduler, app, settings)
        log.info("schedule applied: %s (%s)", settings.schedule.cron, settings.schedule.timezone)
    else:
        with contextlib.suppress(JobLookupError):
            scheduler.remove_job(DAILY_JOB_ID)
        log.info("schedule removed: disabled in settings")


def schedule_status(app) -> dict[str, Any]:
    """The schedule as `/api/health` reports it. Must never raise.

    `next_run_at` prefers the live job -- the thing that will actually fire --
    and falls back to computing from the cron string when there is no job:
    tests without a lifespan, offline, or a hosted deployment where the
    schedule is disabled and an external cron drives the trigger endpoint. In
    that last case the yaml cron is the operator's statement of intent, and
    showing it is the point.
    """
    settings = app.state.settings
    schedule = settings.schedule

    next_run: datetime | None = None
    scheduler = getattr(app.state, "scheduler", None)
    job = scheduler.get_job(DAILY_JOB_ID) if scheduler is not None else None
    if job is not None:
        next_run = job.next_run_time
    else:
        try:
            trigger = CronTrigger.from_crontab(schedule.cron, timezone=schedule.timezone)
            # Can be None even for a valid cron: a date the calendar never
            # reaches, `0 0 30 2 *`, exhausts the search (apscheduler 3.11.3).
            next_run = trigger.get_next_fire_time(None, datetime.now(UTC))
        except Exception:  # noqa: BLE001 - a bad cron string must not fail /health
            next_run = None

    return {
        "enabled": schedule.enabled,
        "cron": schedule.cron,
        "timezone": schedule.timezone,
        "next_run_at": format_seconds(next_run) if next_run is not None else None,
    }
