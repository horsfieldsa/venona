---
status: accepted
date: 2026-09-08
---

# 2026-09-08 — The trial takes a card, so the trial is a Stripe object

Three days free, card collected up front, converts automatically to the domain's
monthly or annual price unless cancelled. The trial is
`subscription_data[trial_period_days]=3` on a Checkout Session, and nothing in
either database counts days.

**Not collecting the card was the first decision, and it was wrong.** A no-card
trial means the trial has to live somewhere we own: a `trial_ends_at` on
`memberships`, a clock threaded through `grant_access` and its three callers,
and a second definition of "entitled" that has nothing to do with Stripe. All of
that disappears when Stripe owns the trial. `grant_access` is untouched by this
change, and granting access now means granting *access* rather than granting
three days of model spend.

**A trialist costs real money.** Roughly $1.20 over three days at one tailored
brief a day, and up to $3.60 at `personal_briefs_per_day = 3`. That is
affordable per person and unbounded in aggregate: the moment `HUB_SELF_SERVE`
is on, an open trial is an unauthenticated way to spend our subscription
quota. The card is the cheapest possible control and it needs no counter, no
cap, and no abuse heuristics.

**It also changes who arrives.** A card-gated trial converts several times
better than an open one, because the people who start it have already decided.
Fewer trials, more subscribers, and — the part that matters for a product whose
marginal cost is a model call — far less spend on people who were never going
to pay.

**Two parameters are load-bearing.** `payment_method_collection=always`, because
Stripe's `if_required` will skip card collection on a trialing subscription and
silently hand back the no-card trial this ADR exists to avoid. And
`subscription_data[trial_settings][end_behavior][missing_payment_method]=cancel`,
so a trial with no usable card ends rather than sitting in a state nobody reads.

**The obligation this creates is disclosure.** A card-up-front trial that
auto-converts is the pattern consumer protection regimes police hardest, and
Stripe's own rules require the terms be clear before the card is taken. The
pricing card states, next to the button, in the reader's words: three days free,
card required now, then the monthly price, cancel any time. The trial countdown then runs in the app for all three days
rather than appearing as a warning at the end — surprise is what produces
chargebacks, and a reader who has seen the date every visit is not surprised.

**What would reverse this.** Evidence that the card gate costs more signups than
its conversion rate wins back — measured, not assumed, against a real funnel
with real numbers on both sides.
