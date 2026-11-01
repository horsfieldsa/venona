---
status: accepted
date: 2026-09-09
---

# 2026-09-09 — A gallery to choose from, and the seat is created by the payment

The first thing somebody sees after signing in to the hub is now a gallery of
domains: what each one covers, how much is in it, what it costs, and that three
days are free. Choosing one goes straight to Stripe. **The membership is created
when the payment lands, not when the button is pressed.**

**The page it replaces could not be chosen from.** It was a request form whose
only mention of a domain was its name in a `<select>`, with no description, no
price and no trial. With one domain that is thin; the day a second ships it is
two names and nothing to tell them apart. And since the switches moved to the
hub it was also wrong: with self-serve on, that form granted a membership while
still telling the reader to ask a person.

**Every field on a card is one the domain already publishes about itself.**
`login_blurb` is the sentence it shows on its own login page; the counts and the
last brief ride the status envelope the hub already polls for. A description
written on the hub, about somebody else's corpus, is one that drifts within a
month -- and the hub is the wrong place to hold an opinion about what a domain
covers.

**Figures are quoted, never asserted.** They come from the last poll, and
`live` is not a claim about when that was: a hub with `HUB_POLL_ENABLED=0` shows
month-old numbers with the status column still saying live. So a card whose
status is older than an hour says what it stood at, and one that has stopped
answering says so.

## The seat waits for the payment

Sending somebody to Checkout requires naming the instance, and `_seat` refused
anybody without a membership -- so the obvious build granted first and sold
second. That leaves a membership behind for everybody who opens Stripe and
closes the tab: accounts that never paid, indistinguishable in the directory
from accounts that did, with nothing to prune them by.

So `_seat` admits a non-member when self-serve is on and the domain is public,
and `checkout.session.completed` grants the seat. An abandoned Checkout now
leaves nothing at all.

The cost is that access waits on a webhook, so `/billing/return` performs the
same grant when the browser comes back. Both are idempotent and neither
promotes: `users_repo.grant` upserts, so an unguarded call would demote an
administrator who bought a subscription on their own instance, and there is a
test for exactly that.

**One way in.** `POST /request-access` used to grant on its own rules; it now
hands off to the same route the gallery uses. Two paths granting on two rule
sets is how they end up disagreeing about who may.

## The price is re-read

The gallery quotes `instances.price_monthly_amount` and Checkout charges
whatever the Price id resolves to in Stripe. They agree only because one command
wrote both, and a price edited in the dashboard parts them silently -- with the
visible half being the one somebody reads before deciding to pay it.
`poller.refresh_prices` re-reads each priced domain hourly and corrects the
quote, logging a warning when it has to.

It is a separate sweep from `reconcile_subscriptions` on purpose: that one walks
SUBSCRIPTIONS, so a domain nobody has subscribed to yet would never be reached,
and that is exactly the domain whose price is being read rather than charged.

## Consequences

- **A stranger can now reach Stripe in two clicks from a cold sign-in**, when
  self-serve is on. That is the intent, and it makes the gallery a selling
  surface: ADR 0212 already accepts that nobody reads a brief before being asked
  for a card, so this page is doing the work the product cannot do yet.
- **`_seat` is no longer "the instances you may pay for".** It now also answers
  "the instance you may BUY", gated on self-serve. A future caller that assumes
  a returned row implies membership would be wrong.
- **Nothing here works with self-serve off**, which is production today: every
  card says Request access and the queue is unchanged.

## Reversal trigger

If granting on the webhook ever leaves somebody paid-up and locked out -- the
return handler missed and the webhook lost -- the answer is not to grant on the
click again, it is to grant during the reconcile sweep, which already reads
every subscription and holds the instance id in its metadata.
