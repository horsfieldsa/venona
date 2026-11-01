# Running the payment loop locally

A hub, an instance and Stripe in test mode, wired together so the whole
subscription lifecycle can be driven without touching production. Everything
here is test mode: `sk_test_`, a test clock, and Stripe's published test cards.

The go-live checklist for the REAL thing is `docs/RUNBOOK.md`, "Turning billing
on". This file is only about the rehearsal.

## What is already set up

Test-mode objects, created once and reusable. Ids live in
`.env.hub` (git-ignored):

| Object | What |
|---|---|
| Product | **one per instance**, named after it |
| Prices | monthly and annual, on that instance's product |
| Portal configuration | Venona's own, **not** the account default |

**One product per instance, and that is not tidiness.** Stripe's customer
portal lists a subscriber's subscriptions by PRODUCT NAME, and the product name
is also what appears on the invoice they expense. A reader subscribed to two
domains saw "Venona monthly $12" twice, indistinguishable, with no way to tell
which was which before cancelling one.

Create them per instance, which stores the ids on the instance row:

```
uv run hub instances pricing <slug> --monthly 1200 --annual 12000
```

An instance with no prices refuses checkout rather than falling back to another
domain's product. There is deliberately no hub-wide default.

**The portal configuration is pinned deliberately.** This Stripe account is
shared with other projects, and the account-default configuration belongs to
one of them: their cancel policy, their plan list, their branding, mutable by
them. `HUB_STRIPE_PORTAL_CONFIGURATION` is what stops Venona's readers seeing
it. Same argument as pinning the API version.

## Starting it

```
make dev-hub
```

That is the whole stack (it needs `.env.hub`; see `docs/HUB.md`): the hub on :8180, this instance on :8000 and :5173,
and `stripe listen` forwarding webhooks to the hub. The
Stripe lane is `scripts/dev_stripe_listen.sh`, which checks the signing secret
against `.env.hub` and says so loudly when they differ -- and never exits, so a
machine with no Stripe CLI still gets the other three.

`make dev` is one instance alone, with no hub, for a single reader or for a
second instance beside a stack that is already up.

`--latest` on purpose: the account default is older than the version the code
pins, and `--latest` is newer. Testing against the newest shape is what caught
two moved fields.

The instance's auth and billing settings live in `.env`, so `make dev-hub` starts a
stack that really signs in through the hub and really enforces the paywall. It
needs `VENONA_OIDC_ISSUER=http://127.0.0.1:8180`,
`VENONA_GOOGLE_CLIENT_ID` / `_SECRET` set to the OIDC client the hub minted,
`VENONA_MGMT_TOKEN`, and `VENONA_BILLING_ENFORCED=1`.

That last one is the OVERRIDE rather than the switch (ADR 0214). The switch is
`Paying to read` on `http://127.0.0.1:8180/admin/settings`, and it reaches the
instance through the hub's own push. Keep the variable set while rehearsing the
paywall and the local box stays enforced whatever the hub says; clear it to
rehearse the switch itself.

**`stripe listen` prints a new secret each session.** Put it in `.env.hub` and
restart the hub, or every event is refused as a bad signature while Stripe
retries for days -- which looks like subscriptions randomly not applying.

## Registering the instance

```
uv run venona auth mgmt-token                      # mint it
uv run hub instances add --slug dev --name Dev \
  --public-url http://127.0.0.1:5173 --mgmt-token <token>
uv run hub users add reader@venona.test
uv run hub grant reader@venona.test dev reader
```

The hub's `instances.slug` and the instance's own domain slug are SEPARATE
values in separate databases and nothing keeps them in step. Do not assume they
match: they did in production by coincidence and disagreed on the first machine
that had both. Everything that has to name an instance across the boundary uses
the OIDC client id instead.

## Signing in without Google

The hub authenticates with Google, which a script cannot do. Mint a hub session
cookie directly, then walk the OIDC redirects with a cookie jar:

```
uv run python -c "
import sys,time; sys.path.insert(0,'hub')
from hub.config import get_settings
from hub import security
print(security.mint_session(get_settings(), email='reader@venona.test', now=time.time())[0])"
```

Use ONE host throughout (`127.0.0.1`, not a mix with `localhost`) or curl scopes
the cookies to different hosts and the callback refuses the state.

## Driving the lifecycle

The hub pushes entitlement on its poll cycle. `uv run hub poll --once` runs it
now rather than waiting five minutes.

```
# cancel at period end, then change your mind
stripe subscriptions update <sub> -d cancel_at_period_end=true
stripe subscriptions update <sub> -d cancel_at_period_end=false
# end it
stripe subscriptions cancel <sub>
```

**Trial conversion needs a test clock**, because the trial is three days long
and nothing else can make three days pass:

```
stripe test_helpers test_clocks create --frozen-time $(date +%s)
# create the customer WITH test_clock=<id>, then the subscription
stripe test_helpers test_clocks advance <id> --frozen-time <trial_end + 3600>
```

Advancing past the trial end charges the card and moves the subscription to
`active`. That is the product's central promise and it is worth re-running
whenever the billing code changes.

## Cards

`tok_visa` succeeds. `4000 0000 0000 0341` attaches and then declines at charge,
which is the `past_due` path. `4000 0025 0000 3155` requires bank
authentication, which is `action_required` -- a different message from a
declined card, and telling those readers to replace a card that works is how
they conclude the product is broken.

## Cleaning up

Test clocks, and every customer and subscription on them, are deleted together:

```
stripe test_helpers test_clocks delete <id>
```

The product, prices and portal configuration are the rehearsal environment and
are meant to persist.
