---
status: accepted
date: 2026-09-01
---

# 2026-09-01 — Where a notice goes is a database row, and a notice type declares its own words

`VENONA_NOTIFY_URL` was the whole of venona's outward-facing surface: one
environment variable, one POST, fire and forget, six callers. It was the right
shape for the problem ADR 0057's neighbours describe -- one operator, one box,
a run at 06:00 that nobody watches -- and `docs/pm/NOT-DOING.md` still rejects a
metrics stack on the strength of it: *"what was actually missing was a way to be
told, and that is thirty lines and no dependency."*

Two questions that shape cannot answer, and one fact that made it worse.

**"Tell me, but not about that."** A clean daily can carry two quiet failures: a
feed struck out, and a healthy rotation that has found nothing worth writing
about for three nights. One channel meant one alert, so the title had to name
which it was, and an operator could not mute one without losing the other. They
are fixed in different places -- one is a source problem, the other a profile or
window problem -- and they are worth different amounts to different people.

**"Did my brief get sent?"** Unanswerable, because nothing wrote anything down.
Fire and forget is correct while the only reader of an alert is the person who
configured the alert. It stops being correct the moment a reader is on the other
end.

**And it was dark.** The variable is `sync: false` in the deployment config and has never
been set on the production instance. ADR 0155 records the cost: four failed runs
over two days, and the success heartbeat -- the dead man's switch, the half that
catches a container that never came back -- was a no-op the whole time.

## Decisions

**A destination is a row, not a document.** The obvious home for editable
configuration is `config/settings.yaml`, and it is the wrong one here.
`api/security.py` opens by explaining that the settings document is readable AND
writable over HTTP, so anything on the `Settings` model is both
operator-visible and attacker-writable -- which is why the session secret and
the root token are environment-only, and why `config_service.FROZEN_TOP` closes
the same door from the other side. A Slack incoming webhook URL has exactly that
property: anyone holding it can post to that channel. It is a bearer token in
URL clothing.

So destinations live in `notify_destinations`, and the same rule follows them
onto the wire: **no route returns a stored target.** Responses carry a hint --
host plus last four. The control is `Replace`, which opens an empty field, never
`Edit`, which would pre-fill it. An edit control populated from a response is
precisely how this page would hand the credential to anyone holding a session,
and it is the paragraph `AccountView.vue` already wrote about the root token,
arriving at a second door.

The other half of the argument is that half of this could never have been a
file: a per-reader list of addresses is relational, and `user_preferences` and
`relevance_settings` already establish that per-reader state is rows with no
admin surface over them.

**A notice type declares its own words.** `notify/types.py` is
`services/run_types.py`'s shape, for ADR 0187's reason: a type declares what it
SAYS, not merely that it exists. `label` and `detail` ship to both settings
screens on the wire, and there is no map of notice keys to English anywhere in
`web/src`. `lib/runOutcome.ts` carries the scar that makes this non-negotiable
-- four surfaces invented run-outcome words independently and one of them was
wrong, telling a curator the web held no definition when what had actually
happened was that venona hit its own call ceiling.

The registry is also what makes the first question answerable: `sources.disabled`
and `corpus.quiet` are two types now, subscribable separately.

**Two audiences, one mechanism.** An instance notice is about the machine and
goes wherever its administrator points it; a reader notice is about one person's
reading and goes wherever that person points it. Registry, destinations, outbox
and both channels are shared; `audience` is the whole of the difference. It is
authorization rather than tidiness: without it a reader could point a webhook at
`access.requested` and collect every stranger's address and self-description as
they arrive.

The addressing is structural. `policy.destinations_for` has no "everybody" case
to call, and a reader notice fans out one reader at a time, so a mistake in a
caller can tell one person twice and cannot mail one reader's brief to the
readership.

**Delivery is durable, and it is not a run.** `notify_outbox` is the first
durable outbound state in this repo. It is not a run type: C11's rule is that a
lane is about what a run WRITES, and delivery writes neither
`documents`/`clusters` nor definitions, so neither lane's justification covers
it -- and `OUTCOMES` is the vocabulary of finding, not sending. A third lane
would be a twelve-file mechanism change to model a thing that makes no model
call. The drainer is an interval job beside the daily, which is safe because the
api may never exceed one replica.

Raising still cannot fail anything. `raise_notice` swallows everything, exactly
as `alert` did, because a notifier that can fail a run turns a warning about a
degraded run into a failed one. That argument covers RAISING and says nothing
about SENDING, which is why the outbox is allowed to record a failure and show
it.

**A failing destination strikes out, and the way back is in the app.**
`sources/registry.py`'s pattern, reused rather than re-reasoned: three
consecutive failures, not a rate, because a destination that failed one send in
ten is working. ADR 0041 is what it cost to have the only exit from
auto-disabled be a CLI incantation printed in a banner, so `Try this one again`
is a button.

**The chicken-and-egg is the design problem, not a detail.** `SourcesHealth`'s
docstring states the same failure for feeds: an auto-disabled source is silent
by design, so without a surface "the brief got thin" has no visible cause
anywhere in the product. A struck-out destination is worse, because the thing
that broke is the telling. So `/api/notifications/status` rides the app's
existing admin-gated poll, the Admin rail carries a mark, and the page that owns
the fix carries the banner. It is deliberately NOT on `/api/health`, which every
reader polls: what this instance sends and where is not a reader's business.

**The environment variable is adopted once and retired.** On boot, if it is set
and no webhook destination exists, it becomes one, subscribed to every instance
notice regardless of `default_on` -- it meant "tell me everything", and silently
narrowing what an operator already had is a worse surprise than a message they
can turn off. Skipped entirely once any webhook destination exists, so somebody
who has been to the screen keeps their own choices.

## What this does not do

**It does not send email.** The channel is built to the transport boundary: the
address (every account has one, established by the identity provider), both
bodies, the subscription, the queue row and both screens. What is missing is a
service to hand a message to, a domain to send from, and the DNS records that
stop it landing in spam. Those are a decision with a bill attached.

A subscription is therefore honoured and parked as `blocked` rather than
refused. The alternative -- skipping the enqueue when no provider is set -- makes
a switch look persisted and do nothing, which is the control `AccountView.vue`
refused to ship in the first place. On the reader's screen the email switches do
not render at all while `email_available` is false; the honest sentence takes
their place. `Toggle` has no `disabled` prop and should not grow one for this.

**It does not give a reader's brief an address.** `brief.personal.ready` links
to the app's front page, because a personal brief has no id-addressable route,
by the same decision that keeps every per-reader row off one.

## Reversal triggers

An email transport arriving is not a reversal; it is the `Transport` protocol
being implemented and `release_blocked` running once.

Reverse the row-not-a-document decision if destinations ever need to be
reviewable in a diff the way `sources.yaml` is -- at which point the credential
has to move somewhere else first, and that is the whole problem again.

Reverse "delivery is not a run" if delivery ever spends model budget or writes
corpus state. Both would put it inside C11's actual rule rather than beside it.
