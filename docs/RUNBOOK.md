# Runbook

What to do when it breaks. What the controls do is `docs/USER-GUIDE.md`.

---

## Which release is each surface on

When the hub and several instances run, they are not necessarily all on the
same code. Each one says which it is:

| Where | Shows |
|---|---|
| the hub's `/admin`, "This hub" | the hub's own release and build, and the front site's if one is configured |
| the hub's `/admin`, domains table | every domain's release, at a glance |
| the hub's `/admin/domains` | each domain's build and when its process started |
| an instance's own `/admin` | that instance's release, build and uptime |

A **release** is the number in `VERSION`, one for the whole repo. A **build** is
the commit, and it is the half that answers "is this the code I pushed" —
between bumps every surface reports the same release, which is exactly when they
are most likely to differ. `local` in a build column means nothing deployed
that surface, which in production is a fault worth chasing.

To cut a release: `echo <x.y.z> > VERSION && make version && make lint-version`,
then commit. That rewrites both pyprojects, the extension manifest and the
lockfile.

`Site last read` going stale while `Site version` stays put is the hub failing
to reach `{HUB_SITE_URL}/version.json`, not the site being on an old release: a
failed read deliberately keeps the last good one rather than blanking it. No
alert fires for it, by the same decision that keeps a CDN blip from paging
anyone (ADR 0248).

## `make dev` fails with "Address already in use"

A dev server from a previous run is still holding the port — Ctrl-C at the wrong
moment, a crashed terminal, or a backgrounded `make dev`.

```bash
make ports     # who is listening on 8000 and 5173
make stop      # reclaim them
make dev
```

`make dev` runs `stop` automatically, so you should rarely hit this. When you
do, it means the holder is **not** part of this repo — `make stop` names it and
refuses to kill it. Two options:

```bash
make dev API_PORT=8001    # run somewhere else
# or free the port yourself, then retry
```

`make stop` decides ownership by whether the process (or an ancestor) is rooted
in this repo — cwd or executable under the repo root — not by matching command
names. That covers uvicorn's `--reload` supervisor, its `multiprocessing`
child, and the vite node process. It will never kill a stranger.

Note that a browser tab open on `localhost:5173` is a *client*, not a holder,
and is correctly ignored.

---

## `make health` shows `neo4j FAIL`

```bash
docker compose ps                    # is the container up?
docker compose up -d neo4j
docker compose logs --tail=50 neo4j
```

Common causes:

- **Container not started.** `make up wait`.
- **Out of memory.** The heap is capped at 512 MB deliberately (this machine has
  ~3 GB free with swap already under pressure). If Neo4j is being OOM-killed,
  check `docker stats` and close other things before raising it.
- **Port 7687 taken** by another Neo4j. `docker ps -a`.

Neo4j holds nothing original. If it is corrupt, throw it away:

```bash
docker compose down -v
make up wait
uv run venona graph rebuild
```

---

## `make health` shows `model FAIL`

**First: which service, and how is it signed in?** `/admin/model` answers both,
and `/api/health` → `model.service` names it without a browser. The rest of
this section depends on the answer.

```bash
claude auth status            # llm.harness: claude_code
codex login status            # llm.harness: codex (writes to STDERR, exits 0)
```

For Claude Code, expect `"loggedIn": true` and **`"authMethod": "claude.ai"`**
on a subscription instance. If `authMethod` is anything else, model spend is
going onto metered billing rather than the subscription. That is a supported
mode now, but only a deliberate one: `llm.billing_mode` has to say `api`, and
an instance set to `subscription` refuses to boot with a metered key present.

```bash
env | grep -iE 'anthropic|openai|codex_api'   # empty on a subscription
```

**A pasted credential beats the environment, and only if one is stored.** If
`/admin/model` says the connection came from the environment while you expect a
stored one, the store is empty; connecting one at `/admin/model` stops the
environment's copy being passed to the child at all.

**A sign-in check cannot tell you a credential works.** `claude auth status`
reports `loggedIn: true` for any token value, including a typo. What knows is
whether calls are landing: `/api/health` → `model.circuit`, and the ledger.
Connecting a credential at `/admin/model` does make one real call to check,
which is why it takes a few seconds.

If the version does not match the pin for that service (`llm.pinned_version`,
`llm.codex_pinned_version`), the health strip shows a drift warning. For Claude
Code that matters because `--system-prompt-file` is undocumented and could
disappear in an upgrade. For Codex the pin is weaker than it looks: its base
instructions come from a model catalog it refreshes **over the network**, so
pinning the binary does not pin the prompt. Run `uv run pytest -m live -q` to
confirm the flag set still works, then bump the pin.

**`model.confined`** says whether the service's process is locked to a
filesystem before it runs (ADR 0219). `null` is a service with no filesystem
tool to restrict. `false` on a service that asked for one means the kernel
refused, and the call ran anyway — worth knowing, because nothing else reports
it and the work succeeds either way.

---

## A run produced nothing / every acronym is undefined

**Read both verdicts before anything else.** A run carries two: `status` says
whether the machinery worked, `outcome` says whether there was an answer.

```bash
psql "$VENONA_DATABASE_URL" -c \
  "SELECT kind, status, outcome, error FROM runs ORDER BY started_at DESC LIMIT 10"
uv run venona runs list          # the same thing, in words
```

| what you see | what it means |
|---|---|
| `succeeded` + `nothing_found` | It did the whole job. The answer really is no. Nothing to fix here. |
| `succeeded_partial` + `nothing_found` | It could not do the job. Read the run's **Why** block: usually every candidate host refused us, or the budget ran out. Worth retrying. |
| `succeeded` + `nothing_to_do` | There was nothing to work on. For a daily that is a quiet window; three in a row raises its own alarm. |
| `succeeded_partial` + `delivered` | It produced the artifact and hit trouble on the way. The **Why** block says what. |
| `succeeded_partial`, outcome NULL | A brief was owed and could not be written. `error` says why, and an outage during it earns a retry. |
| `failed`, outcome NULL | It never reached an answer. `error` says why. |

From a script, `venona run` and `venona research` report the same split as exit
codes: **0** delivered, **1** failed, **2** ran cleanly and found nothing.

Then check for silently denied tools — that failure looks like "nothing found"
rather than an error:

```bash
psql "$VENONA_DATABASE_URL" -c \
  "SELECT task, status, error FROM llm_calls ORDER BY rowid DESC LIMIT 10"
```

A call whose tools were denied raises `PermissionDenied` in the executor by
design. If you see that, the invocation lost its `--allowedTools` — see contract
C2.

---

## A run is stuck

```bash
psql "$VENONA_DATABASE_URL" -c "SELECT id, status, stage, started_at FROM runs ORDER BY started_at DESC LIMIT 5"
```

A run left `running` after a crash is marked `failed (interrupted)` on next
startup. Because every stage reads its inputs from the database by **state** --
`analyzed_at IS NULL`, clusters not yet in `brief_clusters` -- you can resume
just the part that failed rather than re-running everything:

```bash
uv run venona run --only brief
```

There is deliberately no `--from RUN_ID`. Filtering by run would need new
predicates in every stage and would fight the state keying that makes resume
work at all.

---

## The site went down while a run was finishing

First correlate it with a run. The gap is visible in the run's own event log:

```bash
psql "$VENONA_DATABASE_URL" -c "SELECT seq, ts, type, stage FROM run_events WHERE run_id='run:...' ORDER BY seq DESC LIMIT 10"
```

A minute or more between the second-to-last event and the stage report is the
stall. Then read the app logs around the time it went quiet (wherever your
host keeps them). Two signatures, and they mean the same thing: `INFO: Shutting
down` with no deploy behind it, and an apscheduler line reading
`Run time of job "_delivery_pass" ... was missed by 0:00:37`. That second one is
the diagnosis: the event loop was blocked for thirty-seven seconds, so
`/api/health/live` went unanswered and the host's health check restarted the
instance for being dead. Memory and CPU will both look fine, because nothing was overloaded.

A run executes as a task INSIDE the API process, so any stage that does a long
stretch of synchronous work stops the API for the length of it. That is what
took a production instance down for ninety seconds on 2026-09-13: the tail of the
Name review stage (`pipeline/curate.py`) ran seventy-seven seconds of Postgres
and index work between its last model verdict and its stage report.

The fix is `await _unblocked(...)` around the section, not a longer health-check
timeout -- see `curate.py::_unblocked`. **Any new stage section that does bulk
database work needs the same treatment**, and `tests/test_curate_yields.py`
holds it for this one.

---

## A run was refused, or sits at `queued`

Both are the lane machinery (contract C11), and they are different answers.

**Refused with a 409.** The `research` lane holds exactly one run. Anything that
writes `documents` and `clusters` is in it: the nightly run, an on-demand run, a
CLI run, an entity research sweep, an article ingested from the extension, and a
brief rewrite. A second one is refused outright rather than queued, because
queueing would turn "start a run" into a silent forty-minute delay.

```bash
uv run venona runs list          # what is actually going
```

**Sitting at `queued`.** Only the `light` lane queues, and that is the contract.
It holds three at once: definition lookups, the definitions refresh, and the
whole-queue acronym sweep. A fourth waits for a slot, which is a different thing
from being slow, and the Runs page says which it is.

---

## Venona keeps saying the nights are quiet

`schedule.quiet_nights_alert` (default 3) fires when that many consecutive
scheduled runs each end `nothing_to_do`. Every feed may still be answering: this
alarm exists precisely for the case where nothing is broken and nothing is
landing either.

```bash
psql "$VENONA_DATABASE_URL" -c \
  "SELECT started_at, outcome FROM runs WHERE kind='daily' AND status LIKE 'succeeded%' \
   ORDER BY started_at DESC LIMIT 7"
```

Check in this order, because they cost nothing in this order:

1. **The window.** `pipeline.lookback_hours` is 36. If the feeds publish slowly,
   a run can genuinely find nothing inside it.
2. **The profile.** Retrieval scores against `profile/relevance_profile.md`. An
   edit that narrowed it will quietly stop matching things.
3. **The sources.** `uv run venona sources` — a feed can still answer 200 and
   have stopped publishing anything in scope.

A single quiet night is normal and is reported as `succeeded`, not as a
degradation. Set `quiet_nights_alert: 0` to turn the alarm off.

Two identical light requests are **coalesced**: the second caller is handed the
first run rather than a second one. Two lookups for the same acronym would cost
twice, write the same row, and the loser's `mark_undefined` could land on top of
the winner's result.

Never ask the registry a global "is anything running" question. Ask the lane:
`registry.active_run_id(RESEARCH)`, `lane_conflict(conn, lane=...)`. A single
global slot made grounding one acronym impossible for the twenty minutes a
research run takes, which is exactly when someone is reading a brief and hits a
term they do not know.

---

## A feed has gone quiet

A source that fails three times in a row is auto-disabled and excluded from every
run. **Rewriting `sources.yaml` does not revive it.** The strike counter is in
the database, not the file.

```bash
uv run venona verify-sources          # probe every declared feed
uv run venona sources                 # the strike counter
uv run venona sources --enable <id>   # clear strikes and re-enable
```

The same list and a retry button appear on **Admin → Sources** in the UI.
Clearing strikes does not claim the source works: if whatever broke it is still
broken it earns three fresh strikes and switches itself off again.

---

## Anchors were saved and nothing changed

Saving is not loading. Nothing in a run re-reads `profile/anchors.yaml`.

In the UI, the Anchors document has an **Apply** button that previews first. From
a terminal:

```bash
uv run venona anchors load --dry-run       # what would be written
uv run venona anchors load                 # write it
uv run venona anchors load --apply-merges  # also the `merges:` block
uv run venona anchors status               # components, isolated nodes, largest
```

---

## The trunk is missing and nothing said so

The other way anchors go absent: a wipe that never loaded them. `make reset` and
`make bootstrap` both reload the trunk, but `make reset-bare` (and a bare
`venona migrate` on a fresh volume) deliberately does not — and a database that
came up that way looks fine, because the first run just mints its own ids and
the graph fills with them. What actually breaks is invisible: the gazetteer is
empty for the whole run (so the US Army mints three ways), and
`ambiguous_surfaces` is empty (so "War on the Rocks" — a publisher, named in the
`ambiguous:` block for exactly this reason — becomes a Company).

It happened on 2026-08-18: run `06a83e69` analyzed 84 clusters against an
unloaded database, minted 610 entities with zero anchors and worked a 44-row
conflict queue that mostly should not have existed.

Two guards now exist. `venona health` prints a `trunk` mark with the active
entity and ambiguous-surface counts and fails when either is zero — check it
before a run, not after paying for one. And the analyze stage itself warns
(degrading the run to `succeeded_partial`) when it starts against an empty
trunk. Either way, the fix is the section above: `venona anchors load`.

Every brief-stage execution mints its OWN brief — `brief:{date}:{uuid7}` — so
running it twice gives you two briefs, not a rewritten one. The second covers
the next-ranked clusters, because the first brief's clusters are now spent.

To iterate on `profile/brief_prompt.md` against the SAME material, delete before
regenerating. Deleting releases that brief's `brief_clusters` rows, which is the
entire point:

```bash
uv run venona briefs list                 # find the id
uv run venona briefs delete brief:2026-08-06:06a750fb-...
uv run venona brief --date 2026-08-06     # same clusters, new prompt
```

Unlike `venona runs`, this really deletes — a brief owns its children outright,
and a soft hide would leave its clusters burned forever. Without the delete you
are tuning against different material each pass and comparing nothing.

The **Rewrite** button on the brief is the same `--only brief` execution with a
button on it, and it has the same property: it mints a new brief over the
next-ranked clusters rather than rewriting the one on screen. Use it to see what
the current prompt does; use the delete-then-regenerate loop above to compare two
prompts over the same material.

---

## The extension underlines nothing

This renders identically to "this page has no entities venona knows", which is
what `make ext-smoke` is for: 25 checks in headless Chrome, read-only, safe to
run anywhere. It needs the API up.

```bash
make ext-build     # regenerates the styles from web/src/styles/ too
make ext-smoke
```

Check in this order:

1. **The API URL in the options page.** It defaults to
   `http://127.0.0.1:5173/api`, which is the **Vite proxy**, not uvicorn. Pointing
   it at :8000 works for annotation but breaks "Open in venona →", because :8000
   has no frontend to send anyone to.
2. **The host.** `matches` is five named hosts, deliberately. It is not
   `https://*.gov/*`; that would be every US government host, login.gov included.
   Adding one is a manifest edit, by name.
3. **The token**, on a deployed instance.
   `uv run venona mcp token new --label "extension" --scope annotate`, pasted into
   the options page. It is stored in `chrome.storage.local` and never syncs. NOT
   `venona auth token` — that prints the root credential, which can start runs,
   spend and purge; the `annotate` scope reaches four paths and everything else is
   a 403 pre-routing. It belongs to a user, so suspending the person disables the
   extension.
4. **The corpus.** An empty alias index underlines nothing, correctly.

It does not need `VENONA_CORS_ORIGINS`. `host_permissions` is what grants an
extension cross-origin access. Measured, not assumed: see `docs/adr/`,
2026-08-10.

---

## Starting over

On a workstation:

```bash
make reset        # wipe volumes + db, re-migrate, re-seed
```

On a deployed instance there is a browser and nothing else, so the same job is
**Admin → Data → Delete all data**. It is a hard delete, not a hide: nothing in
the app brings it back. It states the real row counts, names what survives
(schema version, configured feeds, pending access requests, agent connections,
saved copies), and requires the phrase `delete everything` typed — which the API
demands independently, so a determined click cannot route around it.

**Take a copy first if you want one.** *Admin → Data → Export for Obsidian*
writes the whole corpus as a folder of Markdown and keeps the file, and the
purge deliberately leaves those files alone. That is the only thing in the app
that survives it.

It refuses with a 409 while a research run is in flight, because that run holds
entities and assertions that would land after the delete — and while an export
is being written, because that file would otherwise be a complete copy of what
was just deleted.

`data/` is *nearly* disposable, and the exception is the point: everything under
it is a cache or a re-fetchable credential except `data/exports/`, which is the
one thing on the box that cannot be rebuilt from anything. `make reset` does not
discriminate. `profile/` is not disposable either — it is your configuration.

---

## An export failed, or the file is gone

*Admin → Data* lists every copy that has been written. A row saying **removed to
save room** is retention doing its job: Venona keeps the few most recent and
deletes the rest, so make a new one. A row that offers no download for any other
reason means the file went missing from `data/exports/` without the app being
told, which is what a manual clean-up of the data directory looks like from here.

A run that did not finish says so on its own page — the link from the table, or
*Admin → Runs*. The two failures worth knowing:

- **Not enough room.** The export refuses before it writes rather than filling
  the disk. Free something there and try again.
- **Interrupted.** An export runs inside the API process, so a deploy or a
  restart kills it exactly as it kills a research run. It leaves no half-written
  file: the archive is renamed into place in one step and the record is written
  after, so the wreckage is a stray file that the next export sweeps up.

## Nobody can sign in

`GET /api/auth/mode` says what the server actually offers. Then:

- **A Google account is rejected.** A Google OAuth client authenticates *any*
  Google account, so something has to say which ones are yours. That is the
  `users` table: `venona users add <email> --role reader`, which takes effect on
  their next sign-in with no restart. `VENONA_AUTH_ALLOWED_EMAILS` is the
  bootstrap — it seeds `users` on first boot and gets an address in with no row —
  but it is no longer how you admit someone.
- **Someone was admitted and still cannot sign in.** They are `suspended`.
  `venona users ls` shows it. The row wins over the environment, so an address in
  both is still refused — deliberately, or suspension would be bypassable by
  anyone who had ever been in the allow-list, which is everyone
  `ensure_from_env` created.
- **Someone signed in but the app is missing pages.** They are a `reader`.
  Curation, Runs and Settings are admin. `venona users role <email> admin`.
- **The callback fails.** `VENONA_PUBLIC_URL` must match the authorised redirect
  URI in the Google console exactly, including scheme. Nothing derives it from
  the request: `request.url_for` renders `http://` behind a TLS-terminating
  proxy and Google rejects the mismatch.
- **Someone used `/request-access`.** That writes a row and raises the "Someone
  asked for access" notice to whatever is subscribed at `/admin/notifications`,
  and grants nothing.
- **`too many requests; retry in Ns`.** The sign-in budget, and the app now says
  so in words rather than JSON: a refused navigation lands on the login page with
  the message. `SIGNIN` is 20 per fifteen minutes per address and a round trip
  spends two, so this is a stubborn retry loop, not a stranger. Wait; the log
  line says which bucket refused and on what key, and a key of `*` means the
  global ceiling rather than yours:

  ```bash
  # filter the app logs for the refusal
  rate limited: bucket=signin key=203.0.113.7 path=/api/auth/login retry_after=42s
  ```

  The buckets are in-process, so a restart clears every one of them — but on the
  instance a deploy is stop-then-start and kills an in-flight run, so check the
  research lane before reaching for that. The API itself needs no session:
  `Authorization: Bearer $VENONA_AUTH_API_TOKEN` is charged as ordinary traffic,
  so the root token still reaches every route while sign-in is refused.
  `POST /api/auth/token-login` is a *browser* path and answers 403 unless
  `VENONA_TOKEN_LOGIN=1` is set, which it is not by default once the hub is the
  issuer.
- **The issuer is the hub (`VENONA_OIDC_ISSUER` set) and the hub is down.**
  New logins fail; everything else survives — sessions are instance-signed
  cookies and live out their TTL, runs keep running, and
  `POST /api/auth/token-login` with the root token still works as break-glass.
  Fix the hub (its logs; docs/HUB.md); nothing on the instance needs
  touching. Also: the 300-second OAuth state cookie now spans
  hub sign-in *plus* Google, so a very slow first login fails with "this
  sign-in link has expired" — a retry succeeds, because the hub session
  survives and the second pass skips Google.

```bash
uv run venona access list --all
uv run venona users add someone@example.com --role reader   # the grant
uv run venona access decide someone@example.com approved --note "..."
```

**Two commands, and they stay two.** `decide` records the decision; `users add`
creates the account. Keeping them apart is what makes an unauthenticated public
form safe — nothing in the request path can grant anything, so the worst a
stranger can do is ask.

---

## Turning billing on, and what lives outside this repo

**A meaningful part of billing is Stripe dashboard configuration, and none of it
appears in a diff or is caught by a test.** Shipping perfect code against an
unconfigured portal produces a 400 on the one button that recovers revenue.
Before you turn **Paying to read** on for a domain, work through all of this.

**In the Stripe dashboard:**

- **The customer portal**, enabled, with the features the app links to: update
  payment method, switch plan, cancel, invoice history. The in-app banners
  deep-link into `payment_method_update`, `subscription_update` and
  `subscription_cancel` flows; a flow that is off answers 400.
- **Cancellation at period end**, not immediately. `docs/adr/0211`
  promises access until the period runs out.
- **Retry schedule and dunning emails.** Stripe's are the only billing email
  there is; venona's own notifications stop at the transport. Decide what
  happens after the final retry, because that is what produces the `lapsed`
  state the app renders.
- **Stripe Tax**: origin address, both prices marked taxable, and the
  registrations actually held. Checkout already asks for a tax id, so a business
  buyer can put a VAT or ABN number on the invoice they will expense.
- **Statement descriptor**, something recognisable. An unrecognised descriptor
  causes more disputes than any handler resolves.
- **Receipts and invoice emails on.**
- **The API version pinned on the webhook endpoint**, matching `API_VERSION` in
  `hub/hub/stripe.py`. Unpinned, Stripe upgrades the account on its own schedule
  and payload SHAPES change with no deploy on our side.
- **An alert on repeated webhook delivery failure.** Without it a broken handler
  is invisible until somebody complains about access. Stripe's dashboard is the
  only view of delivery; the hub keeps no failed-event table, because a failed
  apply rolls the whole claim back and Stripe simply redelivers.
- **Events lost beyond the retry window are a different problem**, and Stripe's
  dashboard does not solve it. The hub reads every subscription from Stripe
  hourly (`poller.reconcile_subscriptions`) and corrects anything that drifted,
  which is what covers an outage longer than the three days Stripe retries for.

**Then, in this order, and the order matters:**

1. Set `HUB_STRIPE_SECRET_KEY` and `HUB_STRIPE_WEBHOOK_SECRET` on the hub. Both
   or neither: `config.assert_usable` refuses a partial configuration, because a
   secret key with no webhook secret takes money and never hears that it did.
   Set `HUB_STRIPE_PORTAL_CONFIGURATION` too. It is not part of that pair and
   the hub starts without it, but unset the portal falls back to the Stripe
   account's default configuration -- and on an account shared with anything
   else that default belongs to whoever set it last.
2. Price each instance: `hub instances pricing <slug> --monthly 1200 --annual
   12000`. An instance with no prices refuses checkout rather than selling
   another domain's product, and its card in the hub's gallery falls back to
   Request access rather than offering a trial it cannot start. The hub re-reads
   these amounts hourly, so a price edited in Stripe's dashboard corrects itself
   within the hour and logs that it had to (ADR 0215).
3. Review who is comped. Migration 0023 marked every reader who predates the
   column `comped`, deliberately, so that turning the paywall on could not lock
   out the existing readership. They will never be asked to pay until somebody
   clears them: `venona users ls` shows the column, `venona users uncomp
   --email ...` withdraws it.
4. Deploy the hub. Confirm `/billing/checkout` answers a 303 rather than a 503.
5. Confirm the hub is PUSHING. Its poller logs `entitlement push: N delivered`
   every cycle, whether or not anything moved, so a `0` means "nothing pending"
   rather than "not running". It also logs `reconciled N subscriptions from
   Stripe` hourly. On the instance, `/admin/people` grows a subscription column
   whose rows say something other than "not started".
6. Only then turn on **Paying to read** for that domain, on the hub's
   `/admin/settings`. It pushes immediately and the page's `What it reports`
   column is the domain confirming it (ADR 0214). No deploy, and turning it back
   off is the same switch, which is the point: it is the control you reach for
   when readers who have paid are being refused.

   `VENONA_BILLING_ENFORCED` still overrides it on the instance itself, in both
   directions, for the day the hub is wrong. It is set nowhere in production.
7. Decide how new people get in: **New accounts** on the same page. `They ask,
   and I decide` is the queue on `/admin/requests`. `They pick a domain and get
   it` is self-serve, and it is only safe once paying to read is on above, since
   the card is then what stands between a stranger and the cost of a brief.

**The webhook secret is per endpoint AND per mode.** `stripe listen` prints a
different one from the dashboard's. A test-mode secret in front of live events
refuses every one as a bad signature, and Stripe retries a refused webhook for
days, so it looks like subscriptions randomly not applying rather than like a
bad credential.

**A rehearsal, locally**:

```
stripe listen --forward-to localhost:8180/billing/webhook
```

Stripe's **test clocks** are what make a three-day trial testable in minutes.
Card `4242 4242 4242 4242` succeeds, `4000 0000 0000 0341` attaches and then
declines at charge (the `past_due` path), and `4000 0025 0000 3155` requires
bank authentication (the `action_required` path, which is NOT a failed card and
must not tell anyone to replace one).

---

## Somebody says they have been locked out and they are paying

Ask which screen they see, because three states look similar and mean different
things.

- **"There are no briefs at all"** and they have never subscribed. The router
  sends anyone with `entitlement_status = 'none'` to the subscription page; if
  they reached the app at all they have a membership but no subscription. Check
  the hub: they may have completed Checkout without the webhook landing. `hub`'s
  `/admin` shows unprocessed Stripe events.
- **"My archive stops in March"** and they are paying now. The projection is
  stale: their row says `lapsed` while Stripe says `active`. The poller repairs
  this within one cycle; if it does not, the push is failing and the hub logs
  say why. Nothing is lost, and nothing needs fixing by hand.
- **"It says my card failed and it did not"** — check whether the notice is
  `action_required` rather than `payment_failed`. Those are different events and
  different copy: the first is their bank asking for a tap and the card is fine.

**Nothing here is repaired by editing the instance's database.** Those four
columns are a projection; the hub overwrites them on the next push. Fix the
subscription in Stripe, or the membership on the hub, and let it flow down.

**To let somebody in without payment**: `venona users comp --email ...`, on the
instance. THE INSTANCE, and this is the one exception to the paragraph above.
The hub bills, and a reader it has never sold anything to reads `none` there, so
`comped` is a word the hub cannot say -- which is why the two projection writers
refuse to overwrite it and why nothing the hub pushes will take it away. It
carries no end date and the local expiry brake ignores it. `venona users uncomp
--email ...` withdraws it, and their next sign-in picks up whatever they have
actually paid for.

---

## Rate limited

On a subscription the limit is the binding constraint, not cost. Under
`llm.billing_mode: api` there is no window and the dollars are real, so what
binds instead is `llm.max_spend_usd_per_run`; the behaviour below is otherwise
identical, including ending `succeeded_partial` rather than `failed`. The executor backs
off with jitter and caps concurrency at 2. A run that hits the ceiling ends
`succeeded_partial` with the clusters it managed — **not** `failed` — and the
brief is generated from what it has.

To reduce pressure: lower `llm.concurrency` or `llm.max_calls_per_run` in
**Admin → Settings**. On a deployed instance that document lives on the disk
(`/app/config`, materialized from its row) and is the one the app reads, so a save takes effect at
once except for the few fields the editor reports as needing a restart.
`venona config path` prints the file, and `/api/health` names the directory.

---

## The model provider is down

Check the status page for whichever service `/api/health` → `model.service`
names: <https://status.claude.com>, or <https://status.openai.com>. A 5xx/529/overloaded incident looks
like this from venona's side (ADR 0155):

- `/api/health` → `model.circuit` is `open: true, transient: true`, and the
  Runs page says **provider outage · waiting**. The CLI is fine, and neither
  service's sign-in check will tell you anything.
- A research run in flight **waits** — it sleeps until the breaker's next
  half-open probe and becomes it, for up to `llm.outage_wait_seconds` (30 min
  by default, measured from the start of the outage). Cancelling the run ends
  the wait.
- Lookups and acronym grounding from the UI **fail fast** (the light lane
  never waits); their ledger rows name the service and how many times in a row
  it has failed.
- If the outage outlasts the wait, the run finishes `succeeded_partial` with
  the clusters it got — the brief may be missing — and the **scheduled** daily
  re-fires itself 30 minutes later, up to three times, resuming by state. The
  alert names the retry time, at every destination subscribed to a run that did
  not finish. An on-demand run does not re-fire; press Run again.
- **This is the only thing Venona re-spends on unattended**, so it has an off
  switch that is not a deploy: `schedule.outage_retry_max` (and
  `outage_retry_delay_seconds`) in Admin, Configuration. `outage_retry_max: 0`
  stops it from tonight. A run that finished with nothing to write about never
  earns a retry whatever this is set to; a window is not made less quiet by
  fetching it again in half an hour.

A circuit that is open with `transient: false` is a dead credential, not an
outage: see "`make health` shows `model FAIL`" above.

`model.ok: false` with `circuit.open: false` and an `error` naming a
`version probe` or `auth probe` is the **probe** failing, not the model —
the real calls are landing (the ledger will show `ok` rows). Read the error:
a transport complaint (`the handler is closed`, `Broken pipe`) is the loop
racing a short-lived subprocess and clears itself (ADR 0155 addendum);
`not found on PATH` is the image.

If you see none of this in the logs, check that the logs carry anything from
`venona.*` at all. Alembic's `fileConfig` can
silence the process if something calls it outside `db.core.migrate`.

---

## The graph looks wrong

It is a projection, so it is always safe to rebuild:

```bash
uv run venona graph stats      # before
uv run venona graph rebuild
uv run venona graph stats      # must match, including mention_total
```

If a rebuild *changes* the counts, the bug is in the projector or the assertion
log, not in Neo4j.

Edges silently missing? Check the ontology rejected them for bad domain/range:

```bash
uv run venona graph rebuild    # prints "ontology rejected N edge(s)" with reasons
uv run venona ontology check
```

---

## An entity was split in two / two entities should be one

Never edit the graph. Merges go through the assertion log:

```bash
uv run venona entity merge <loser-id> <survivor-id>
uv run venona graph rebuild
```

Undo is `active = 0` on the `entity_mappings` row plus a rebuild. If the split
happened because an alias was missing, add it to
`fixtures/seed/entities.yaml` — seeding beats fuzzy matching.

---

## Working the conflict queue

`curate` warns `N unresolved alias conflict(s)` every run. Those are pairs the
resolver declined to merge on its own.

```bash
uv run venona conflicts                      # everything open, grouped by kind
uv run venona conflicts --kind duplicate     # the ones that want a merge
uv run venona conflicts --all                # including settled rows
```

Each row is one of five kinds — or none — and they want different things. The
stored value is on the left; the UI shows the label on its right, because
`containment` is this pipeline's word for "one name is a substring of the other"
and a curator who has not read `curate.py` has no way to know that.

A `duplicate` no longer always waits for you. `curate` merges the classes it can
prove — an acronym and its expansion, a trailing `(GLOSS)`, a corporate-form
word, a trailing generic head noun, and a name a person declared an alias of the
other entity — plus a model verdict the names corroborate. Everything below is
what is LEFT after that, which is why the queue is shorter and each remaining row
is more likely to be a real judgement call. `venona entity merges` lists what was
merged and why; `venona entity unmerge` reverses any of it.

| kind | shown as | what it means | what to do |
|---|---|---|---|
| `duplicate` | two spellings | one thing, two spellings | `venona entity merge` |
| `collision` | shared acronym | one acronym, two real things | `--keep-apart`, and consider adding the surface to `ambiguous:` in `fixtures/seed/entities.yaml` |
| `containment` | one name inside the other | one name qualifies the other, and no lexicon explains the extra words | read it — `Thales` / `Thales Australia` stays apart, `Lockheed Martin` / `...Rotary and Mission Systems` is a `PART_OF` |
| `resolver_error` | generator bug | the acronym generator produced a surface neither name supports | our bug; file it |
| `anchor` | proposed placement | a suggested parent for an entity that connects to nothing | PART_OF to accept, or keep apart. **Never merge** — an anchor is a parent, not the same thing |
| `type_conflict` | one thing, filed under two kinds | one thing read as two kinds the ontology calls confusable, such as an engine typed Platform once and Technology once | name the kind it really is; both readings fold into one entry under it, minting that entry if neither side is already it |
| `cross_type` | same name, different kinds of thing | one name held at two entity types, such as a program and the aircraft it delivers | relate them, or keep apart and rename one so a reader can tell which is which. **Never merge** — the ontology draws that line on purpose |
| `NULL` | unclassified | raised before the classifier existed | read both sides on their own terms. About a third of the open queue |

The three verdicts are not equally recoverable, and it is worth knowing which way
to err. A merge is reversible through `entity_mappings`, but until it is reversed
every assertion about one side reads as an assertion about the other, so a wrong
merge is quietly wrong across the whole graph. A wrong keep-apart is a duplicate
that comes back on the next run.

The whole queue is also workable from `/admin/curation` in the UI, one pair at a time
with both sides' evidence on screen and the adjudicator's suggestion pre-selected.
`docs/USER-GUIDE.md` has the keys.

Merging closes the matching conflict automatically. `--keep-apart` is for the
other verdict, and it is not optional bookkeeping: a row nobody closes is
indistinguishable from a row nobody has looked at, which is how the queue got to
79 open rows with no exit.

---

## "There are 700 provisional entities"

Expected, and bounded. Promotion needs two articles from two **publishers**, and
in a real corpus roughly 80% of entities are named exactly once — most of them
people quoted a single time.

Uncorroborated entities that stay silent for `pipeline.provisional_dormant_days`
(default 30) become `dormant`: still hidden, no longer counted as pending. One
new sighting revives them, and nothing is ever deleted, so `venona graph
rebuild` is unaffected. Set the knob to `0` to keep everything provisional
forever.

To see what is actually stuck near the gate:

```sql
SELECT e.name, COUNT(DISTINCT a.article_id) arts, COUNT(DISTINCT d.site_domain) pubs
  FROM entities e
  JOIN assertions a ON a.entity_id_raw = e.id AND a.retracted_at IS NULL
  JOIN documents d ON d.id = a.article_id
 WHERE e.status = 'provisional' GROUP BY e.id HAVING arts >= 2 ORDER BY arts DESC;
```

---

## The graph looks too sparse

Check what is actually hidden before assuming extraction is weak:

```bash
uv run venona research --dry-run --limit 10   # who is blocking what. No spend.
```

Only `active` entities are projected, so a relationship with one uncorroborated
endpoint never reaches Neo4j. On the live corpus that was 371 of 468 triples —
the corroboration gate, not the extractor.

Two ways to release them, cheapest first:

```bash
uv run venona conflicts --kind duplicate      # free: merging consolidates evidence
uv run venona research --limit 6              # ~$0.75: chase independent coverage
uv run venona graph rebuild                   # edges appear once endpoints are active
```

`research` runs the whole loop and reports the delta:

```
promoted    7 entities
graph       97 -> 102 edges  (+5)
```

If it reports new documents but no new edges, the coverage it found came from the
publisher you already had — promotion counts distinct `site_domain`.

---

## Annotations look wrong in the brief

Over-matching (a common word highlighted) is almost always an alias with too low
a `strictness`. See the scale in `docs/DATA-MODEL.md`.

Under-matching (a real mention missed) is usually a missing *literal* surface —
remember the alias table is keyed on `surface`, not `normalized`, precisely so
several spellings can coexist.

Re-annotation is free and needs no model calls. It re-resolves spans against the
**current** alias index, which is the payoff of storing offsets rather than
markup: improving the alias table retro-fixes every historical brief.

```bash
uv run venona reannotate                 # the latest brief
uv run venona reannotate --brief <id>    # a specific one
```

**CLI only.** There was a `POST /api/briefs/<id>/reannotate` and this file told
you to curl it; the route is gone. Nothing in the frontend ever called it, and
re-annotation is a maintenance step you run after a merge or an alias edit, both
of which are CLI-only themselves. `pipeline/brief.py::reannotate` is the
implementation.

---

## Where things live

| What | Locally | In a hosted container |
|---|---|---|
| System of record | Postgres (the `postgres` service; `VENONA_DATABASE_URL`) | managed Postgres (back it up with `pg_dump`) |
| Graph projection | Docker volume, `./data/neo4j` | the neo4j private service's disk (rebuildable: `venona graph rebuild`) |
| Raw fetched payloads | `data/raw/<run_id>/` | `/app/data/raw/<run_id>/`, ephemeral |
| Your editable profile | `profile/` (git-tracked) | **`config_documents` rows**, written to `/app/profile` at boot (ADR 0234) |
| Editable config | `config/` | **`config_documents` rows**, written to `/app/config` at boot |
| The claude CLI's home | `~/.claude` | `/home/venona/.claude`, ephemeral — the credential is a `harness_homes` row, restored into it at boot |
| The codex CLI's home | `~/.codex` | `/home/venona/.codex`, ephemeral — same. Still EXEC-capable (codex extracts helper binaries at startup) and still grows a models catalog and several SQLite databases, all of which regenerate |
| A credential connected in the app | `data/harness/<service>/credentials/` | `harness_credentials` rows |
| Config snapshots before each save | `.history/` under the document's directory | **`config_document_history` rows**; `.history/` is not written at all on an instance with a store |
| Generated exports | `data/exports/` | **`data_exports.content`**, bytes in the row |
| Recorded test fixtures | `fixtures/` (git-tracked) | — |
| Built extension | `extension/dist/` (git-ignored, `make ext-build`) | — |

**`data/` is MOSTLY disposable.** `make reset` wipes and re-seeds it, and
everything under it is rebuildable except one thing: a credential connected at
`/admin/model` lives in `data/harness/<service>/credentials/` and cannot be
regenerated from anywhere. Back that up or be ready to sign in again.
`profile/` is not disposable at all — it is your configuration.
