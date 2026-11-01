# The hub

One place that knows every venona instance, routes people to theirs, asserts
identity and membership to instances, and reads/provisions through a narrow,
named surface. It is its own package (`hub/`, no `venona.*` imports) with its
own Postgres database, and it is OPTIONAL: it is only for running several
instances behind one sign-in with central management. A single instance runs
without it, with no sign-in or with Google configured directly. Locally,
`make dev-hub` (or `make hub-dev` alone) serves it on :8180, and needs
`.env.hub` (copy `.env.hub.example`).

Decisions: [ADR 0141](adr/0141-the-hub-is-the-oidc-issuer-and-role-authority.md)
(the hub is the OIDC issuer; its role claim is authoritative; local suspension
always wins) and [ADR 0142](adr/0142-the-hub-pulls-through-a-vmgmt-scoped-surface.md)
(the hub pulls; instances never call it).

## The four concerns

1. **Directory** — `instances` rows with slug, display name, tagline, public
   URL, and a status the poller owns. `GET /public/instances` serves the live
   and offline rows (offline stays listed so extension presets survive an
   outage; provisioning and retired never appear).
2. **Portal** — sign in with Google, land on a chooser of *your* instances.
   Zero memberships lands on a request-access form that answers with the same
   bytes whether the email is new, repeated, or belongs to an account — an
   account-existence oracle is the one thing this page must never be.
3. **Identity (SSO)** — the hub is the OIDC issuer instances point
   `VENONA_OIDC_ISSUER` at. Details below.
4. **Management channel** — a 5-minute poll of each instance's
   `GET /api/mgmt/status` with that instance's sealed `vmgmt_` token, plus a
   provision-user proxy. The instance's leash (CONTRACTS.md, C12 addendum) is
   the whole vocabulary; the hub can express nothing outside it.

## The OIDC contract with instances

The instance derives both endpoints from the issuer and never fetches
`.well-known` or JWKS (the provenance carve-out: it trusts a direct TLS
exchange with a constant token endpoint, `backend/venona/api/oauth.py`):

- `GET {issuer}/oauth/authorize` — instance sends `client_id, redirect_uri,
  response_type=code, scope, state, nonce, prompt`. No hub session → the
  portal sign-in, with the whole request riding along as `next`. Non-member →
  the request-access page, and **no code is minted**. Bad client or redirect
  target → an error page, never a redirect.
- `POST {issuer}/oauth/token` — `client_secret_post`, form-encoded. Single-use
  code, membership re-read at redemption, RFC 6749 §5.2 error bodies.
- The id_token carries `iss` (this hub, byte-for-byte), `aud`, `sub`
  (`user:<uuid7>`), `email`, `email_verified`, `name` (omitted when unset),
  `nonce`, `iat`, `exp` (300s), and **`venona_role`** — the membership claim
  the instance enforces. RS256; JWKS and `openid-configuration` are published
  for correctness, not because anything here reads them.
- The redirect URI is never stored: always
  `public_url.rstrip("/") + "/api/auth/callback"`, the same string the
  instance's `AuthConfig.redirect_uri` computes.
- `tests/hub/test_id_token_contract.py` holds the two packages together: the
  hub's real token endpoint feeding the instance's real claim parser.

**Blast radius when the hub is down: new logins only.** Instance sessions are
instance-signed cookies and live out their TTL; running runs, the API, and
`VENONA_AUTH_API_TOKEN` break-glass login are all unaffected (RUNBOOK).

**One timing note:** the instance's OAuth state cookie lives 300 seconds, and
with the hub in the path that window now covers hub sign-in *plus* Google. A
very slow first login fails with "this sign-in link has expired"; retrying
succeeds (the hub session survives, so the second pass is instant).

## What the hub can never do

The mgmt credential's reach is pinned to four routes on the instance
(`status`, `provision-user`, `domain`, `setup/start`). No other config reads
or writes, no run control beyond `setup/start`, no token minting, no exports,
no sessions — `verify_bearer` refuses the `vmgmt_` prefix outright. The
instance's CONTRACTS.md owns that clause; this page just points at it.

## Running it somewhere other than localhost

Nothing deployment-specific ships in this repository. The hub is a plain ASGI
app (`uvicorn hub.main:app --app-dir hub`) over Postgres, configured entirely by
the `HUB_*` variables `.env.hub.example` documents.

1. Secrets: `make hub-secrets` prints the generators.
2. A Google OAuth client for the hub itself (the hub authenticates people
   with Google): redirect URI exactly `{HUB_PUBLIC_URL}/auth/callback`.
3. Migrations run in the lifespan; watch `/healthz` go green.
4. **The `hub` CLI is DB-local**: it opens the database directly. Where you can
   only reach the hub over HTTP, the admin surface is the routes under
   `HUB_ADMIN_TOKEN` (below).
5. Set `HUB_EDGE_HEADERS=1` when the hub process is the public edge (TLS
   terminated by a platform router with nothing of yours in front), so the app
   stamps HSTS and the CSP itself.
6. Backups: `pg_dump` the hub database, and back up `HUB_SEAL_KEY`
   separately: the seal key is deliberately not in the database, so a dump
   alone cannot impersonate the hub to any instance. **Losing the seal key
   is recoverable**: re-run `hub instances set-mgmt-token` (or re-register
   the mgmt token over HTTP) per instance.

**Run exactly one hub process.** The poller's miss counter and the startup
migrations both assume one; two replicas double-count misses and race the
ledger, silently.

## Registering an instance

On the instance: `uv run venona auth mgmt-token` → set as `VENONA_MGMT_TOKEN`
there. Then either, on the hub box:

```
hub instances add --slug acme --name "Acme Watch" \
  --public-url https://acme.example --mgmt-token vmgmt_...
```

or over HTTP (what Epic E1's scaffolder does; offline mode prints this curl):

```
curl -s https://hub.example.com/admin/instances \
  -H "Authorization: Bearer $HUB_ADMIN_TOKEN" \
  -H "content-type: application/json" \
  -d '{"slug":"acme","display_name":"Acme Watch",
       "public_url":"https://acme.example","mgmt_token":"vmgmt_..."}'
```

Both return `VENONA_OIDC_CLIENT_ID` and — **exactly once** —
`VENONA_OIDC_CLIENT_SECRET`; only the hash survives, so a lost secret means
`hub instances rotate-secret`, never a lookup. Flip the instance by setting
`VENONA_OIDC_ISSUER/CLIENT_ID/CLIENT_SECRET` and redeploying. Rollback is
restoring `VENONA_GOOGLE_*`: sessions are untouched either way, and users
re-link by email on first login.

Seed people: `hub users add EMAIL`, `hub grant EMAIL SLUG ROLE` — or, when
all you can reach is HTTP, the same operation over the
admin bearer:

```
curl -s https://hub.example.com/admin/grants \
  -H "Authorization: Bearer $HUB_ADMIN_TOKEN" \
  -H "content-type: application/json" \
  -d '{"email":"you@example.com","slug":"acme","role":"admin"}'
```

`POST /admin/grants` creates the user row if it is new (`display_name`
optional) and grants the membership — the CLI's `grant` plus `users add`,
minus needing the database URL. Access requests are triaged two ways:
`hub requests ls` / `hub requests grant ID SLUG ROLE` on a box with the
database, or the operator page below. There is deliberately no admin HTTP
route for requests — granting is the one write worth making a person look at
a screen for.

## Two doors onto one queue

`access_requests.kind` says which door a row came through, and the operator
page keeps them in separate sections because they are graded differently.

- **`access`** — someone signed in, has no membership, and is standing at a
  locked door. They are waiting on a decision. Grant or dismiss.
- **`launch`** — someone left an address on a separate front site, which promises
  one message when it opens and nothing else. **There is nothing to grant.**
  The form asked for an address and nothing else, so a launch signup has named
  no domain, and granting one would be picking a domain on their behalf — an
  account, on a guess, for somebody who only asked to be told. The section
  offers Remove and nothing else; the way in is still for them to ask, which
  raises an `access` row in the section above.

The refusal is enforced, not just unoffered: `POST /admin/requests/{id}/grant`
answers 409 for a launch row and `hub requests grant` refuses one, because a
control that is merely absent from a page is still a request anybody can post,
and the CLI takes an id off a listing that shows both kinds.

`GET /admin/launch.csv` emits the pending launch rows as `email,added` with
`content-disposition: attachment`, for pasting into whatever sends the mail.
It is the **whole list, not the page on screen** — a download that silently
carried fifty of nine hundred addresses would look exactly like a complete one.

A separate front site can post to `POST /request-access` here, cross-origin, and
that is the only cross-origin write the hub answers. Two things have to agree
or it fails in production only:

- `HUB_CORS_ORIGINS` (this service) — who the hub answers. The allowance is
  stamped by the route itself, never by middleware: every other page here runs
  on the session cookie that is the SSO master key, and an app-wide allowance
  makes the chooser and this operator page cross-origin readable.
  `Access-Control-Allow-Credentials` is never sent.
- The front site's `Content-Security-Policy` — who the
  browser may ask. It must name the hub in **both** `connect-src` (the fetch,
  when scripts run) and `form-action` (the browser posting the markup itself,
  when they do not).

`kind` is part of the dedup key, so an address on the launch list can still
raise an access request later. Before that it hit `ON CONFLICT DO NOTHING`
and vanished, and the endpoint is non-echoing by design, so nothing could
report the loss.

## The operator surface (browser, `/admin`)

A Vue app in `hub/web`, served at six paths and fed by JSON at `/admin/data/*`.
It is the hub's own package -- its own `package.json`, its own Vite config, its
own `vue-tsc` -- and it imports `web/src/styles/{tokens,theme,base}.css` across
the boundary, so its components are written in the app's exact utility
vocabulary rather than in an approximation of it. `make hub-web` builds it
straight into `hub/hub/static/app/`; until that has run, `/admin` answers 503
and says so rather than 404ing, which would look like the gate refusing.

Why this half is an app and the front door is not: auth pages must not depend on
a bundle loading, and the public pages are the only ones here whose theme
survives JavaScript being switched off. Neither is true of a surface only a
signed-in operator reaches. ADR 0223.

| Page | Shows | Writes |
|---|---|---|
| `/admin` | four figures, the domains table with each one's version, and what the hub and the front site are themselves running | none |
| `/admin/domains` | every domain, and everything the last poll returned about it | none |
| `/admin/requests` | access requests, 50 a page | grant, dismiss |
| `/admin/launch` | launch signups, 50 a page, plus the download | remove |
| `/admin/accounts` | every account and what it can reach, 50 a page | none |
| `/admin/settings` | paying to read, per domain and what each reports back; new accounts | two switches |
| `/admin/notifications` | where the hub sends what it notices, and the last 25 deliveries | add, remove, switch, test, clear a strike |

**The hub and the front site are on that page too, and nothing else could put
them there.** Any of them can be a release behind the others. The hub's own
reading is taken at the request; the site's is the poller's last successful read
of `{HUB_SITE_URL}/version.json`, because a static site has no process to ask
(no `HUB_SITE_URL`, no read). A read
that fails leaves the last good one in place and lets `Site last read` go
stale, rather than blanking a version that is still published out there.

**The domains readout is deliberately complete and deliberately unranked.** It
shows every field `GET /api/mgmt/status` returns beside the hub's own columns,
in two labelled halves that are NOT reconciled against each other: the hub's
half is what it decided and has observed, the domain's half is that domain's own
answer stored verbatim at the last poll. A version, a name or a paywall setting
disagreeing across the two is the reason to open the page, so nothing resolves
them into one figure.

Everything on it is up to five minutes stale, and it says so. The alternative --
asking every instance on load -- takes the slowest one's timeout as the page's
own and shows a blank screen exactly when a domain is down, which is when an
operator most needs to read it.

Three states that look alike and are not: a domain that has never answered
(every field of its own half is a dash), one that answered with something
unreadable (the same dashes, plus a notice saying so), and one reporting
genuine zeroes. A dash is not a zero anywhere on that page.

Figures rather than lists on the overview: requests, launch signups and accounts
all only arrive, so a front page that renders every row gets slower and less
readable exactly as the thing it reports on succeeds. The domains table stays
whole -- they are registered by hand, one at a time, and whether they are all
still answering is the question that page exists to settle.

**Paging is clamped, never refused.** A page number arrives in a URL, so it can
be zero, negative, past the end, or a bookmark to a page that emptied since;
every one of those lands on a real page. A 404 on `?page=9` after a queue
drained is a wrong answer to an obvious question. Listings are ordered on a
total key (`created_at, id`; `email` for accounts) -- without one, two pages can
share a row and skip another, which reads as a request that disappeared.

### Three categories behind one prefix

`/admin` is shared, and `middleware.py` keeps the split exact. The page paths
are an exact set and not a prefix, because a prefix would hand the session gate
whatever is added under it next; `/admin/data/` is the one prefix here, and it is
safe as one only because it cannot collide with `/admin/instances*` or
`/admin/grants`.

| | Gate | Refused with |
|---|---|---|
| the six pages | hub session + `HUB_OPERATOR_EMAILS` | 302 to the front door |
| `/admin/data/*` | the same gate | 401 with the JSON envelope |
| `/admin/instances*`, `/admin/grants` | `HUB_ADMIN_TOKEN` | bare 401 + `WWW-Authenticate` |

The two 401s differ by that header, and `tests/hub/test_ops.py`'s partition
sweep asserts its presence and its absence -- the status alone cannot tell them
apart. The sweep fails the day a route is added to either operator router
without being named in the gate.

A `fetch` gets a status rather than a redirect on purpose: a 302 reaches it as
an opaque hop to HTML, which a client cannot tell from success.

Who may enter is `HUB_OPERATOR_EMAILS` (env, comma-separated; empty refuses
everyone -- the CLI and the admin API are unaffected). It is an env allowlist
rather than a role column so the surface that edits the hub's database cannot be
reached by editing the hub's database. A signed-in non-operator gets the same
404 a stranger gets, byte for byte, because whether the surface exists for an
email is not something they get to learn.

**Writes are cookie-authed JSON.** `SameSite=Lax` withholds the session on every
cross-site request that is not a top-level GET navigation, and
`application/json` is not a CORS-simple content type, so a cross-origin write
must preflight -- which the hub grants nowhere. Every side effect stays on POST;
a side-effecting GET is reachable by top-level navigation, which is the one
thing Lax still allows.

Instance registration, rotation and suspension stay CLI and bearer'd API on
purpose. The launch list's CSV stays a real link rather than something the
bundle assembles: it arrives as an attachment with a filename, and a download
built in the page would lose both.

## Telling the operator something

Everything the hub knows happens where nobody is looking. A request arrives at
02:00 and sits `pending` until somebody opens `/admin`. A domain misses three
status polls, is demoted to `offline`, and the only record is a column and a log
line. Reading on that domain has already stopped by then.

So the hub sends. Seven notices, declared in `hub/notices.py` with the words the
switch says and the words a person receives in the same place:

| Notice | When |
|---|---|
| `access.requested` | somebody asked for access and is waiting on a decision |
| `account.created` | an address signed in that the hub had never seen |
| `instance.unreachable` | a domain crossed from `live` to `offline` |
| `instance.answering` | it crossed back |
| `launch.signup` | an address was left against the day a domain opens |
| `billing.subscribed` | a reader started paying for a domain |
| `billing.changed` | a payment failed, or a subscription was cancelled |

**The two billing notices divide the statuses between them.** `billing.changed`
is "this needs a person" and says nothing when a subscription is working, because
paging on success teaches whoever reads these to stop reading them. That left the
thing the hub exists to do as the one event nobody was told about, so
`billing.subscribed` is its own switch: an operator who wants the good news and
not the bad can have exactly that. `past_due` belongs to the first even though it
is entitling, because arriving at it is a failure rather than a new subscriber.

**`account.created` is raised from the Google callback, not from a table.**
Signing in creates no `users` row -- a session is a cookie carrying an address,
and a stranger who buys nothing never gets one -- so there is nothing whose
growth would reveal a new arrival. The dedupe key is the address for the same
reason: a rowless person's tenth visit looks exactly like their first.

**A destination is a row, a subscription is a row, a delivery is a row** —
migration `0010`, the same shape the instance uses and for the same two
questions: "tell me, but not about that", and "did it actually go out". The one
difference is that there is no `owner`. The instance addresses an administrator
AND a reader and carries `owner_kind`/`owner_id` to keep one from receiving the
other's messages; the hub has one audience, and a column whose only value is
`operator` is not a safeguard, it is a lie that reads like one.

**Both crossings are reported, and only the crossings.** `record_poll_miss`
demotes on exactly the third miss and leaves the status alone afterwards, so
testing the edge says it once per outage rather than every five minutes until
somebody notices. `record_poll_success` returns the status the row HELD, through
a CTE in the same statement, which is the only way to tell a recovery from an
ordinary healthy cycle.

**`instance.unreachable` says "not answering", never "down",** and the message
says why in its own words: the poll asks the domain to count what it holds, so a
domain that is up with a slow database fails it exactly like one that is gone.
That caveat belongs in the message, because the person receiving it cannot read
a comment.

**Sending rides the poller**, last in the cycle (`_send_notices`), so a notice
raised by the status pass a moment earlier goes out in the same tick. The
five-minute interval is therefore the delivery latency floor, which is the right
trade for all of them: an access request arriving five minutes late is not the
failure this fixes.

**A switch has to be OFFERED before absence can mean no.** A subscription row
means yes, so absence alone cannot tell "switched off on purpose" from "did not
exist when this destination was made" -- and every notice added after a
destination was created reached nobody. `notify_offered` (migration `0011`) is
the second bit: `notify.offer_new_types` runs in the lifespan, shows each
destination any type it has never been shown, and ticks the ones that default on.
It is the OFFER that is recorded rather than the answer, so a deliberate no is
never handed back at the next boot.

**The escaping and the SSRF rules are shared with the instance**, in the
`notices` package — ADR 0222. Two copies of "which addresses may be dialled" is
not a duplicated rule, it is a hole. `notices.check_webhook` runs at save time so
a refusal is a sentence in the form; `notify.post_once` resolves, re-checks every
answer and dials the address it vetted, so the destination's DNS owner has no
rebinding window.

**A webhook address is a credential.** No route returns it. The console shows
`notices.target_hint` in its place: enough to tell two destinations apart, not
enough to post with.

**A test send proves less than it looks like it does.** It goes straight down
the channel and never reads `status`, so a destination that struck itself out
after three failures still answers a test while sending nothing real. That
asymmetry is why the console says **Not sending** beside such a destination in
words, and offers the button back. It is also exactly how the fault that
prompted all of this stayed invisible.

## Operating

- **Statuses**: `provisioning` (registered, never yet answered a poll — never
  demoted), `live` (answered recently), `offline` (three consecutive misses;
  still polled, recovers to live by itself), `retired` (out of every listing
  and the poll cycle; `hub instances reinstate` returns it).
- `hub poll --once` runs one cycle now and prints per-slug status — the
  verification step after registering.
- `hub instances show SLUG` includes the instance's last status envelope
  verbatim (corpus counts, setup state, user counts).
- **Suspension**: `hub users suspend EMAIL` bites on the next request (status
  is re-read per request). It blocks new SSO logins everywhere; to also kill
  an instance's live 14-day session, suspend there too
  (`POST /admin/instances/{id}/provision-user` with `action: suspend` bumps
  the instance's session epoch).
- **Rotations**: session secret = hub-wide logout; seal key = re-run
  `hub instances set-mgmt-token` per instance; signing key = nothing breaks
  (instances don't verify signatures), the JWKS just changes.

## Refusals (who gets a page, who gets the envelope)

The hub answers browsers and machines on the same paths, so `hub/errors.py`
negotiates on `Accept` (ADR 0191):

- `text/html` in the header gets a rendered page: the graph field, the lockup
  and one sentence, in the reader's own theme from the `venona.theme` cookie.
  `pages.error()` is the single call site, so every refusal already routed
  through it picks the page up.
- Anything else keeps the JSON envelope byte for byte, which is the instance's
  `venona.api.errors` shape. An absent `Accept` is a script that did not
  bother, so it keeps the envelope too.
- `/admin/instances/*` is **never** negotiated, whatever it asks for. It is
  bearer-gated and read by a script, and a browser `Accept` costs nothing to
  send. `AdminBearerMiddleware`'s own 401 is untouched and stays JSON.

Covered: 403, 404, 405, 422, 429, 500, 503. Words per status live in
`errors.PAGES`; 405 deliberately says "not found", because whether a path exists
for a method it refuses is not something an unauthenticated caller learns from
an identity provider. The 500 keeps stamping its own security headers on the
HTML branch as well: `ServerErrorMiddleware` renders it outside the user stack
where `SecurityHeadersMiddleware` cannot reach it.

The field is generated (`make field`); never hand-edit `hub/hub/field.py`.

## Local rehearsal (the D1 definition of done)

Finish every `.py` edit first — uvicorn's `--reload` watches the whole
working tree, for every instance at once.

1. `make up` (dev postgres), then `HUB_COOKIE_SECURE=0 make hub-dev` (:8180 —
   8100 belongs to the 10th scaffolded instance's port block). `.env.hub` with
   dev values of every secret; a real Google client with redirect URI
   `http://localhost:8180/auth/callback`.
2. Register the dev instance: `uv run venona auth mgmt-token`, then
   `uv run hub instances add --slug dev --name Dev --public-url
   http://localhost:5173 --mgmt-token vmgmt_...` — it prints the three
   `VENONA_OIDC_*` lines for the instance's `.env`.
3. `uv run hub users add YOU@example.com` and `uv run hub grant
   YOU@example.com dev admin`.
4. Restart `make dev` with the `VENONA_OIDC_*` env set and
   `VENONA_MGMT_TOKEN` from step 2. Browser: `localhost:5173` → sign in →
   hub → Google → chooser → instance callback → a session whose role the hub
   asserted.
5. `uv run hub poll --once` → `dev  live`; `hub instances show dev` carries
   the instance's real status envelope.
