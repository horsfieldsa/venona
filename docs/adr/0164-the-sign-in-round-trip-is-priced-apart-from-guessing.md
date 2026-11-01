---
status: accepted
date: 2026-08-26
---

# 2026-08-26 — The sign-in round trip is priced apart from credential guessing, and its global ceiling sits far above one operator

`ratelimit.py` splits the old `login` bucket in two. **`LOGIN`** keeps 5/900s and
now covers only `/api/auth/token-login`, the one route where a caller presents a
secret that can be wrong. **`SIGNIN`** is new, covers `/api/auth/login` and
`/api/auth/callback` at 20/900s, and is backstopped by `global-signin` at
600/hour rather than by `global-login`'s 20.

**Why.** Measured on production, 2026-08-26: the operator was locked out of
`defense.example.com` with `too many requests; retry in 80s`, on an instance that
was otherwise healthy and answering. Three things compound, and the bucket
composition is the one that makes the other two fatal.

- **A sign-in is two requests, not one.** The 302 out to the hub and the callback
  back were both `login`, so a 5-token budget buys two and a half attempts.
- **The budget refills at one token per 180s**, so the `Retry-After` is a trap:
  80s buys the token for `/auth/login` and leaves nothing for the callback, which
  is refused with the round trip half finished.
- **A retry is the documented ordinary outcome.** `docs/RUNBOOK.md` already said
  a slow first login through the hub fails with "this sign-in link has expired"
  and that a retry succeeds. That advice cost two of five tokens.

`web/src/views/LoginView.vue` then removes the human from the loop entirely: with
OIDC configured and no token form, it calls `location.replace` on mount, so a
reload, a second tab or the back button spends a sign-in token with nobody
choosing to. The refusal rendered as a raw JSON body on a page with no way
forward, which is where the operator read it from.

**Why the guessing price is the wrong price.** Neither half of the round trip
accepts a secret that can be wrong. `/auth/login` mints a state cookie and
redirects; `/auth/callback` verifies that cookie's signature before it will talk
to the provider at all. What a tight bucket buys on those two paths is not
resistance to guessing — there is nothing to guess — it is a lockout.

**Why the global ceiling changes size and not just name.** `global-login` is
keyed `"*"`, so the operator's own attempts and a stranger's come out of one
20/hour pot: anyone who found `/api/auth/login` could lock the operator out of
their own instance for an hour, and the surface for turning it off is the one
being refused. `global-write`'s comment already states the rule this violated —
a global ceiling must never become the operator's own outage — so `global-signin`
is sized above any real person (600/hour is roughly 300 round trips) and bounds
only what per-IP cannot reach: a distributed flood, where a caller holding a
state cookie can make the callback spend one outbound request on the provider's
token endpoint. A ceiling on guessing is meant to bind; a ceiling on the way in
is not.

**Rejected: raise `LOGIN` to a dozen and leave one bucket.** It buys time and
keeps the wrong shape — the break-glass token path, which is the only thing on
that list a stranger can usefully attack, would get the loosened budget too.

**Rejected: exempt `/auth/callback` entirely, since it carries a signed state
cookie.** The signature is checked in the handler, and the middleware runs before
routing; an unauthenticated path with no ceiling at all is how the callback
becomes an outbound-request amplifier for anyone willing to fetch a state cookie
first.

**Consequence.** Load-bearing sites: `api/ratelimit.py` (`SIGNIN`,
`SIGNIN_PATHS`, `TOKEN_LOGIN_PATHS`, `GLOBAL_BACKSTOP`, `bucket_for`) and
`api/middleware.py`, where every 429 now goes through one `_refuse_rate_limited`
that logs the refusing bucket and its key — `"*"` distinguishes "you spent it"
from "somebody else did", and there was previously no log line at all for the one
refusal whose victim is locked out of the surface they would diagnose it from. A
refused sign-in **navigation** (a GET that asked for `text/html`) answers 302 to
`/login?error=…` instead of JSON, and `LoginView` renders rather than bounces
when it sees an `?error=`; a fetch still gets the 429. `LoginView` also holds a
60-second cooldown in `sessionStorage` so a second automatic bounce becomes the
button. Pinned by `test_the_oidc_round_trip_is_not_priced_as_guessing`,
`test_the_signin_ceiling_is_never_the_operators_outage`,
`test_a_refused_sign_in_navigation_lands_on_the_login_page`,
`test_a_refused_sign_in_fetch_still_gets_the_error` and
`test_every_refusal_is_logged`. Contract C12's exempt-path inventory is
unchanged: this moves what a path is charged, not what it is open to.
