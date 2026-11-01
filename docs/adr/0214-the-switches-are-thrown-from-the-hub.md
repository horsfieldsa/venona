---
status: accepted
date: 2026-09-09
---

# 2026-09-09 — The launch switches are thrown from the hub, not from the environment

Two decisions moved out of the host's environment and onto `/admin/settings`:

- **Whether a domain charges to read.** Was `VENONA_BILLING_ENFORCED` on each
  instance. Now a column on the hub's `instances` row, pushed to the instance
  through `POST /api/mgmt/settings` and kept in `hub_flags`.
- **Whether a signed-in stranger is granted a domain.** Was `HUB_SELF_SERVE`.
  Now a row in `hub_settings`, read on the request that needs it.

**Because one of them is an emergency control.** If subscriptions go wrong and
readers who have paid are being refused, the fix is to stop enforcing while it
is sorted out. As an environment variable that is: edit YAML, push, wait for a
deploy, on the service whose readers are already locked out. A switch is the
right interface for a decision you have to make in a hurry, and the wrong
interface is the one that needs a git commit.

**The hub is a different authority from an instance admin, and that is what
makes this safe.** Contract C12 says a paywall an admin session could switch off
through the HTTP config editor is not a paywall, and that is still true: the
flag does NOT go into `config/settings.yaml`, which any instance administrator
can edit. It goes into `hub_flags`, a table with no editor and one writer. The
hub operator is the person who sells the product; an instance administrator is
somebody they gave a domain to. Those are not the same person and the boundary
between them is the whole point of the hub.

**The environment still wins, in both directions.** `VENONA_BILLING_ENFORCED=1`
and `=0` both override the hub. Before this, only the truthy value was
recognised, so `0` was indistinguishable from unset — which meant there was no
way to force the gate open on the box in front of you. That is exactly the
capability an operator needs on the day the hub is wrong or unreachable, and it
is what keeps a local instance independent of a hub it has never heard of.

**The answer is cached in memory and refreshed by whoever knows it changed.**
`billing_enforced()` is asked on every request through `AuthMiddleware` and again
inside `UserRecord.entitled`, which holds no database connection. So it reads a
module value, refreshed at startup and by the mgmt write. There is no TTL, and
that is correct only while the API runs as one process (`numInstances: 1`).

**`HUB_SELF_SERVE` had never been implemented.** It was declared in `config.py`
and read nowhere, so this change had to build the behaviour before it could
surface a switch for it: a signed-in, active, memberless person who names a
domain is granted it instead of queued. Shipping the toggle alone would have put
a control on an operator page that did nothing.

## Consequences

- **A stolen operator session can now turn every paywall off.** That is new. It
  is smaller than the hole already there — the hub can provision an instance
  administrator, and is the OIDC issuer — but it is worth stating rather than
  discovering. The mitigation is the one that already applies to `/admin`:
  `require_operator` against an env-declared allowlist.
- **The settings page shows what each domain REPORTS, not what was decided.**
  `/api/mgmt/status` carries `billing_enforced`, so a switch that has not
  arrived, and a domain deliberately overriding it, both look different from one
  that took. A page that only echoed our own intent back would call an
  undelivered switch a success.
- **The push rides the status poll.** No new sweep: that loop already reaches
  every live instance and already skips dead ones, and the version pair means a
  failure needs no bookkeeping of its own. The operator's own save also pushes
  inline, so the common case takes effect immediately rather than within five
  minutes.
- **A save never fails because a domain is down.** The decision is recorded, the
  page says the domain has not confirmed, and the poller retries.

## What this does NOT move

## Reversal trigger

If the hub ever runs more than one process, the cache in `authz` needs a TTL or
an invalidation channel — a paywall stale in one worker and not the other is
invisible from outside. And if operator sessions ever extend beyond the handful
of people who could already deploy, the "stolen session" consequence above stops
being smaller than the hole beside it.
