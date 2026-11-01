---
status: accepted
date: 2026-09-11
---

# 2026-09-11 — `form-action` names Stripe, because it says where the reader goes rather than what may run (relaxes 0203)

ADR 0203 pinned the hub's CSP to `'self'` on every directive and said a later
relaxation had to be a deliberate diff. This is that diff, and it is one
directive: `form-action`.

**The bug it fixes took no money and logged nothing wrong.** On the day billing
went live the gallery's trial button did nothing at all when clicked. The server
side was perfect and said so five times over:

```
POST https://api.stripe.com/v1/customers          200
POST https://api.stripe.com/v1/checkout/sessions  200   (x5)
```

A Checkout Session was created on every click. Nothing appeared on screen, no
error reached the console the reader could see, and no log line anywhere was
anything but `200`.

**`form-action` is checked against every hop of a form submission's redirect
chain, not just its `action`.** The trial button is
`<form method="post" action="/domains/{slug}/start">`. That POST is same-origin
and allowed; it then redirects through `/billing/checkout` to
`checkout.stripe.com`, and Safari and Chrome refuse that final hop against a
policy of `form-action 'self'`. The refusal is silent to everything except the
browser's own console. Firefox historically did not check redirects here, so
this is also a bug that reproduces on one machine and not the next.

**Both hosts, not just Checkout.** `billing.stripe.com` is reachable from the
same submission: a reader who already holds the seat is bounced by
`/billing/checkout` through `/billing/portal` to the customer portal, so a policy
naming only Checkout would break exactly the double-purchase guard that exists
to protect somebody's money.

**Why this is not a hole.** Every other directive governs what this origin may
LOAD or EXECUTE, and the hub is the OIDC issuer whose session cookie is the SSO
master key for every instance -- which is why 0203 pinned them. `form-action`
governs where the reader is allowed to GO. Nothing it admits can run here, read
a cookie here, or reach anything here. The two hosts named are the payment
processor the paywall is built on, and without them there is no paywall.

The alternative was making the trial button a link, since the route already
answers GET for the monthly one. Rejected: a GET that creates a Checkout Session
is reachable by a link prefetcher, and the money button is the wrong place to
take that on.

**Changing this requires touching:** `hub/hub/middleware.py`'s `EDGE_HEADERS`
and the reverse proxy config, which `tests/test_header_parity.py` holds
byte-identical because a reverse-proxy deployment serves these from the reverse proxy and the host
serves them from the app. `tests/hub/test_security.py` states the rule that
replaced 0203's blanket one: no origin outside this one on any directive that
loads or executes, `form-action` named as the sole exception with its two hosts
pinned.
