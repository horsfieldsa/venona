"""The 5-minute instance poll: last_seen, live/offline, and the status the
portal renders.

Direction is hub -> instance, polled (docs/adr/0142): the instance needs zero
knowledge of the hub and no credential that reaches it, and `last_seen_at`
falls out for free. Single replica is load-bearing -- two pollers would race
the miss counter -- and compose.hub.yaml carries the banner.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any

from hub import mgmt_client, notify, seal, site_client
from hub.config import Settings
from hub.db import core as db
from hub.repositories import instances as instances_repo
from hub.repositories import site as site_repo

log = logging.getLogger("hub.poller")

#: Strong references. `asyncio.create_task` keeps only a weak reference; a
#: task whose only reference was the argument to `create_task` can be
#: garbage-collected mid-flight (the instance's `venona/scheduler.py` records
#: the same hazard).
_TASKS: set[asyncio.Task] = set()


def start(settings: Settings) -> asyncio.Task | None:
    if not settings.poll_enabled:
        log.info("poller disabled (HUB_POLL_ENABLED=0)")
        return None
    task = asyncio.create_task(run(settings), name="hub-poller")
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


async def run(settings: Settings) -> None:
    while True:
        try:
            await poll_once(settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            # One bad CYCLE (database restart, mid-deploy weirdness) must not
            # end the loop -- the loop is the only thing that ever marks an
            # instance live again.
            log.exception("poll cycle failed; next one in %ss", settings.poll_interval_seconds)
        await asyncio.sleep(settings.poll_interval_seconds)


#: How many entitlement pushes one cycle may make. Bounded because this loop's
#: only other cost is a single GET per instance: an unbounded fan-out of POSTs
#: hiding inside a status check is a second subsystem, and a slow instance would
#: stall the poll everything else depends on. Whatever does not fit is still
#: pending on the next pass, and the counter means nothing is lost.
PUSH_BUDGET = 25


async def poll_once(settings: Settings) -> dict[str, str]:
    """One pass over everything non-retired. Returns {slug: status-after},
    which is what `hub poll --once` prints."""
    conn = db.connect(settings.database_url)
    results: dict[str, str] = {}
    try:
        for row in instances_repo.list_pollable(conn):
            results[row["slug"]] = await _poll_instance(settings, conn, row)
        # After the status pass, never before: an instance marked offline a
        # moment ago is excluded from the push, so a dead host is not POSTed at
        # every five minutes forever.
        pushed = await push_entitlements(settings, conn)
        # LOGGED WHETHER OR NOT ANYTHING MOVED. `docs/RUNBOOK.md` tells an
        # operator to confirm the hub is pushing before turning enforcement on,
        # and a function that only logs its failures gives them nothing to look
        # at when it is working.
        log.info("entitlement push: %s delivered", pushed)
        await reconcile_subscriptions(settings, conn)
        await _read_site(settings, conn)
        await _send_notices(settings, conn)
    finally:
        conn.close()
    return results


async def _read_site(settings: Settings, conn: db.Conn) -> None:
    """Ask the front site, when one is configured, which build it is serving.

    NEVER RAISES, for the reason `_send_notices` gives one function below and a
    sharper one: everything before this point in the cycle has already done its
    work, but the notice drain has not, so an error escaping here would cost an
    operator their alerts in order to report a version nobody is waiting on.

    A failure leaves the stored row ALONE rather than blanking it -- see
    `repositories/site.record`. The two properties are separate and both are
    wanted: the row survives a bad fetch, and a bad fetch survives the cycle.

    No notice is raised when the site goes quiet, and that is a decision rather
    than an omission. `instance.unreachable` exists because a dark domain locks
    readers out; a static site missing one read does not, the host alerts on a
    service that is actually down, and an alarm that fires on a CDN blip is the
    kind that gets ignored by the time it matters. The page showing a stale
    reading is the signal.
    """
    if not settings.site_url:
        return
    try:
        body = await site_client.fetch_version(settings.site_url)
        site_repo.record(conn, body=body)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - see the docstring
        log.warning("site version read failed", exc_info=True)


async def _send_notices(settings: Settings, conn: db.Conn) -> None:
    """Drain the outbox, on the loop that is already awake.

    LAST IN THE CYCLE, so the messages raised by the status pass a moment ago go
    out in the same tick rather than waiting for the next one. That ordering is
    the whole reason this rides the poller instead of its own timer: the pass
    that discovers a domain is down is the pass that can say so.

    The reclaim comes first and runs every cycle rather than at startup. Every
    deploy is stop then start, so a claim held when the process died is the
    ordinary case, and a startup-only sweep would strand anything claimed by a
    process that died between two deploys.

    NEVER RAISES. It is the last thing in a cycle that must not miss the next
    one, and nothing here is worth losing a poll over.
    """
    try:
        reclaimed = notify.reclaim(conn)
        if reclaimed:
            log.info("notify: put %d stalled deliveries back on the queue", reclaimed)
        delivered = await notify.drain(conn, base=settings.public_url)
        if delivered:
            log.info("notify: %d delivered", delivered)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - see the docstring
        log.warning("notify: delivery pass failed", exc_info=True)


#: How often to read Stripe in full. Not every cycle: at a 300s poll that would
#: be 288 full sweeps a day to catch something that happens when Stripe has been
#: unable to reach us for THREE DAYS. Hourly is far more often than the failure
#: it exists for.
RECONCILE_INTERVAL_SECONDS = 3600

#: When the last full read finished. Module state, which is honest here for the
#: same reason the miss counter is: `numInstances: 1` is load-bearing for this
#: process and the compose file carries the banner. A second replica would
#: double the sweeps, not corrupt anything.
_last_reconcile = 0.0


async def reconcile_subscriptions(settings: Settings, conn: db.Conn) -> int:
    """Read every subscription from Stripe and correct anything that drifted.

    THE SAFETY NET UNDER THE WEBHOOK. Stripe retries a failed delivery for about
    three days and then gives up, so an outage longer than that loses events for
    good -- and event-driven state with no reconciliation is the most common way
    a billing integration is quietly wrong for months.

    Bounded by `RECONCILE_INTERVAL_SECONDS` and skipped entirely when billing is
    not configured, so a hub that takes no money never calls Stripe at all.
    """
    global _last_reconcile
    if not settings.billing_enabled:
        return 0
    now = time.time()
    if now - _last_reconcile < RECONCILE_INTERVAL_SECONDS:
        return 0
    _last_reconcile = now

    from hub import billing_apply, stripe

    seen = 0
    after: str | None = None
    try:
        while True:
            page = await stripe.list_subscriptions(settings.stripe_secret_key, starting_after=after)
            rows = page.get("data") or []
            for sub in rows:
                # PER ROW, and that is the whole point of the inner handler.
                # One seat holding two live subscriptions -- a double checkout
                # completed before the first webhook landed -- raises a unique
                # violation here, and an escape to the outer `except` aborts the
                # sweep at that row: every subscription after it stops being
                # reconciled, every hour, forever. One bad row must not take the
                # safety net down for the whole account.
                try:
                    if billing_apply.reconcile_subscription(conn, sub, now=now):
                        seen += 1
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.warning("could not reconcile subscription %s", sub.get("id"), exc_info=True)
            if not page.get("has_more") or not rows:
                break
            after = rows[-1].get("id")
    except asyncio.CancelledError:
        raise
    except Exception:
        # A failed sweep is not an incident: the webhook is the fast path and
        # this runs again next hour. Re-raising would end the poll loop, which
        # is the only thing that ever marks an instance live again.
        log.warning("subscription reconciliation failed", exc_info=True)
        return seen
    log.info("reconciled %s subscriptions from Stripe", seen)
    await refresh_prices(settings, conn)
    return seen


async def refresh_prices(settings: Settings, conn: db.Conn) -> int:
    """Re-read what each domain's prices actually cost, and correct the quote.

    THE TWO HALVES OF A PRICE LIVE IN DIFFERENT PLACES. Checkout charges
    whatever the Price ID resolves to in Stripe; the gallery renders the amount
    stored beside that id when somebody ran `hub instances pricing`. They agree
    only because one command wrote both, and a price edited in Stripe's
    dashboard parts them silently -- with the wrong half being the one a
    stranger reads before deciding whether to pay it.

    Here rather than in the subscription sweep above, because that one walks
    SUBSCRIPTIONS: a domain nobody has subscribed to yet would never be reached,
    and a domain nobody has subscribed to yet is exactly the one whose price is
    being read and not yet charged.

    One GET per priced domain per hour, and there are a handful of them.
    """
    from hub import stripe

    corrected = 0
    for row in instances_repo.priced(conn):
        try:
            monthly = await stripe.get_price(
                settings.stripe_secret_key, row["stripe_price_monthly"]
            )
            annual = (
                await stripe.get_price(settings.stripe_secret_key, row["stripe_price_annual"])
                if row["stripe_price_annual"]
                else {}
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("could not re-read prices for %s", row["slug"], exc_info=True)
            continue
        amounts = (monthly.get("unit_amount"), annual.get("unit_amount") or None)
        currency = monthly.get("currency")
        if (row["price_monthly_amount"], row["price_annual_amount"]) == amounts and row[
            "price_currency"
        ] == currency:
            continue
        instances_repo.set_price_amounts(
            conn,
            instance_id=row["id"],
            monthly=amounts[0],
            annual=amounts[1],
            currency=currency,
        )
        corrected += 1
        # WORTH A LINE EACH TIME. A price that moved without the command being
        # run is somebody editing money in a dashboard, and the operator who did
        # it is not necessarily the one reading these logs.
        log.warning(
            "corrected the quoted price for %s: it now costs %s %s a month",
            row["slug"],
            amounts[0],
            currency,
        )
    return corrected


async def push_entitlements(settings: Settings, conn: db.Conn) -> int:
    """Tell instances what they have not been told. Returns how many landed.

    This is the repair path, not the fast path -- a webhook bumps the counter
    and this delivers it. Deliberately NOT done inside the webhook handler:
    Stripe allows about twenty seconds and retries on timeout, so one slow
    instance there would turn into duplicate deliveries of every event.

    Failures are left pending rather than raised. The watermark only moves on a
    delivered version, so a push that fails is simply tried again next cycle,
    which is what makes a mid-deploy instance harmless.
    """
    from hub import billing_state
    from hub.repositories import subscriptions as subs_repo

    delivered = 0
    for row in subs_repo.pending_pushes(conn, limit=PUSH_BUDGET):
        state = billing_state.for_seat(conn, user_id=row["user_id"], instance_id=row["instance_id"])
        try:
            token = seal.unseal(settings.seal_key, row["mgmt_token_sealed"])
            status, body = await mgmt_client.provision_user(
                row["public_url"],
                token,
                email=row["email"],
                # THE MEMBERSHIP'S OWN ROLE, never a literal. `action="update"`
                # treats this as authoritative: a fixed "reader" demotes the
                # operator of an instance the moment they subscribe to it, and
                # for a sole administrator the instance refuses at HTTP 200 --
                # which this loop reads as delivered, so the entitlement is
                # dropped and never retried.
                role=row["role"],
                action="update",
                entitlement={
                    "entitlement_status": state["status"],
                    "entitled_through": state["through"],
                    "billing_notice": state["notice"],
                    "billing_notice_at": state["notice_at"],
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("entitlement push to %s failed", row["public_url"], exc_info=True)
            continue
        # 404 is SUCCESS-FOR-NOW. A member who has never signed in to the
        # instance has no row there for an entitlement to sit on, and their
        # login will carry the claim. Treating it as a failure would re-POST
        # forever for somebody who may never arrive.
        if status == 200 and isinstance(body, dict) and body.get("status") == "refused":
            # The entitlement still landed -- the instance applies it before it
            # dispatches -- but a refusal means the hub and the instance
            # disagree about who administers it, which an operator has to see.
            log.warning(
                "instance %s refused the membership for %s: %s",
                row["public_url"],
                row["email"],
                body.get("message"),
            )
        if status in (200, 404):
            subs_repo.record_push(
                conn,
                user_id=row["user_id"],
                instance_id=row["instance_id"],
                version=row["entitlement_version"],
            )
            delivered += 1
        else:
            log.warning("entitlement push to %s answered %s: %s", row["public_url"], status, body)
    return delivered


def _stamp(moment: Any) -> str:
    """An outage's identity, for a dedupe key.

    `last_seen_at` stops moving the moment a domain stops answering, so it names
    the OUTAGE rather than the moment: the demotion and the recovery both key on
    the same value, and a domain that goes down twice in a day is two notices
    rather than one suppressed.
    """
    return "" if moment is None else str(moment)


def _ago(moment: Any) -> str:
    """How long since, in a phrase rather than a timestamp.

    "It last answered 2026-09-11T01:12:04Z" makes the reader do the subtraction,
    at the moment they are least inclined to: something is down and they want to
    know whether it is minutes or days. An empty string means never, and the
    caller has a different sentence for that.
    """
    if moment is None:
        return ""
    try:
        seconds = (datetime.now(UTC) - moment).total_seconds()
    except TypeError:
        return ""
    if seconds < 120:
        return "less than two minutes ago"
    if seconds < 5400:
        return f"about {round(seconds / 60)} minutes ago"
    if seconds < 172800:
        return f"about {round(seconds / 3600)} hours ago"
    return f"about {round(seconds / 86400)} days ago"


async def _poll_instance(settings: Settings, conn: db.Conn, row: dict[str, Any]) -> str:
    try:
        token = seal.unseal(settings.seal_key, row["mgmt_token_sealed"])
        body = await mgmt_client.fetch_status(row["public_url"], token)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # A miss is a miss, whatever its flavour -- unreachable, non-200, a
        # rotated seal key. The flavour goes to the log; the counter decides.
        after = instances_repo.record_poll_miss(conn, row["id"])
        status = after["status"] if after else row["status"]
        misses = after["poll_misses"] if after else 0
        log.warning(
            "poll miss for %s (%s consecutive, now %s): %s",
            row["slug"],
            misses if after else "?",
            status,
            exc,
        )
        # THE CROSSING, not the state. `record_poll_miss` demotes on exactly the
        # third miss and leaves the status alone afterwards, so testing the edge
        # says this once per outage rather than every five minutes until
        # somebody notices. `last_seen_at` is the fact that makes the message
        # worth reading -- "not answering" is only actionable next to "since
        # when".
        if status == "offline" and misses == instances_repo.MISS_LIMIT:
            notify.raise_notice(
                conn,
                "instance.unreachable",
                instance_id=row["id"],
                domain=row["display_name"],
                last_seen=_ago(row["last_seen_at"]),
                since=_stamp(row["last_seen_at"]),
            )
        return status
    was = instances_repo.record_poll_success(conn, row["id"], body)
    # The all clear, so that a quiet channel means it is still down. Only from
    # `offline`: `provisioning` becoming live is a first contact, which nobody
    # was waiting to be reassured about.
    if was == "offline":
        notify.raise_notice(
            conn,
            "instance.answering",
            instance_id=row["id"],
            domain=row["display_name"],
            since=_stamp(row["last_seen_at"]),
        )

    # THE SWITCHES, on the loop that already reached this instance. A separate
    # sweep would need its own budget and its own liveness rules; this one has
    # both, and an instance that just answered a status poll is by definition
    # one that can take a push. The watermark only moves on a delivered push, so
    # a domain that is down simply gets it on the cycle after it comes back.
    await _push_settings(settings, conn, row, token)
    return "live"


async def _push_settings(
    settings: Settings, conn: db.Conn, row: dict[str, Any], token: str
) -> None:
    """Deliver this instance's switches if it is behind.

    Failures are logged and left pending rather than raised: this runs inside
    the status poll, and a refused push must not demote an instance that just
    answered. The version compare is the whole state machine.
    """
    if row["settings_pushed_version"] >= row["settings_version"]:
        return
    try:
        status, body = await mgmt_client.push_settings(
            row["public_url"], token, billing_enforced=bool(row["billing_enforced"])
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        log.warning("settings push to %s failed", row["public_url"], exc_info=True)
        return
    if status != 200:
        log.warning("settings push to %s answered %s: %s", row["public_url"], status, body)
        return
    instances_repo.record_settings_push(
        conn, instance_id=row["id"], version=row["settings_version"]
    )
    log.info(
        "settings delivered to %s (billing_enforced=%s, it reports %s)",
        row["slug"],
        row["billing_enforced"],
        body.get("billing_enforced") if isinstance(body, dict) else "?",
    )
