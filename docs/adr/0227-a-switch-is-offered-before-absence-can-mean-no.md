---
status: accepted
date: 2026-09-11
---

# 2026-09-11 — A switch is offered before absence can mean no

The hub gained two notices: `account.created`, for an address signing in that it
has never seen, and `billing.subscribed`, for a reader who starts paying. Both
would have shipped to nobody.

**One bit was doing two jobs.** `notify_subscriptions` holds a row per
destination per notice type: a row means yes, and absence means no. Absence
therefore cannot distinguish "switched off on purpose" from "did not exist when
this destination was made", so every type added after a destination was created
was silently off for it -- and a reconcile that filled the gap in would have
undone every deliberate no at the same time. Migration `0010` names this
precisely and leaves it: *"The fix is a record of what a destination has been
OFFERED, and it is not built here."*

It is built here. `notify_offered` is the second bit. A destination is offered
every type that exists when it is created, and offered any new type once, at the
first boot that knows about it; `default_on` then decides whether that offer
arrives ticked. **It is the OFFER that is recorded, never the answer** -- a type
already offered is skipped whether or not it is subscribed -- which is what makes
`notify.offer_new_types` safe to run in the lifespan, where every deploy is a
boot.

The insert IS the question (`ON CONFLICT DO NOTHING RETURNING`), so two boots
racing cannot both decide a type is new and both tick it back on over a no
somebody meant.

**The backfill names the five keys literally** rather than deriving them from
`notify_subscriptions`, because deriving would read a deliberate no as "never
offered" and hand it back ticked at the next boot, which is the exact silent undo
this table exists to prevent. The cost runs the other way and is the survivable
one: a destination made before `launch.signup` shipped is recorded as having been
offered it, so it stays off until somebody ticks it. One switch, found where an
operator would look for it anyway.

## The two notices

**`billing.subscribed` is its own switch, not a widening of `billing.changed`.**
That one is "this needs a person", and it deliberately says nothing when a
subscription is working: paging on success teaches whoever reads these to stop
reading them, which costs the ones that matter. The reasoning is right, and it
left the thing the hub exists to do as the single event nobody was told about. An
operator who wants the good news and not the bad can now have exactly that.

`past_due` stays with `billing.changed` even though it is entitling. The seat
still reads while a card is retried, so a rule keyed on entitlement alone would
announce a failed payment as a new subscriber. And the dedupe key is the
subscription rather than the status, because a trial converting to `active` is
the same person still paying -- saying it twice would make the number of
subscribers this notice implies wrong.

**`account.created` is raised from the Google callback, because there is nowhere
else it could be.** Signing in creates no `users` row: a session is a cookie
carrying an address, and a stranger who buys nothing never gets one. So no table
grows when a new person arrives, and the first anybody would otherwise hear of a
new reader is a payment. The dedupe key is the ADDRESS for the same reason -- a
rowless person's tenth visit is indistinguishable from their first, so keying on
anything else would report every one of them.

It is not raised from `grants.grant_access`. An account the operator just created
is not news to the operator, and in an invite-only hub `access.requested` already
reports the arrival.

## Consequence

Migration `0011`. `notify.offer_new_types` in `main.lifespan`, after `migrate`.
`ops_api.add_destination` records an offer for every type including the ones that
default off, so a later boot knows this destination has already seen them.
`docs/HUB.md`'s table has seven rows.
