# venona — agent guide

Defense-industry intelligence. A daily (and on-demand) research run pulls trade
press and government feeds, scores each item against an editable relevance
profile, extracts entities and relationships into a graph, and serves the result
as a brief where every program and acronym is hoverable, plus a graph explorer.

Local-first, single user, single machine. Released under MIT and not
maintained (see `README.md`).

## Invariants

These three are absolutes. Everything else in this file follows from them.

1. **Postgres is the system of record. Neo4j is a rebuildable projection.**
   `venona graph rebuild` must always be able to reconstruct the entire graph
   from the `assertions` table.
2. **The model decides *which* entities appear; code decides *where*.**
   Models cannot count characters. Spans are resolved by Aho-Corasick over the
   alias index, never by asking for offsets.
3. **A definition exists only if it is traceable to a sentence in a document.**
   No acronym expansion from parametric memory, ever. Undefined renders as
   visibly undefined.
4. **The domain is configuration; code is domain-blind.** Every string that
   names the subject domain lives in `config/domain.yaml` (and `profile/`) --
   code splices it in via `get_domain()`. No domain term in a string literal
   or template outside `config/`, `profile/` and `fixtures/`; comments may
   keep their worked examples. `make lint-domain` holds the boundary.

## Commands

```
make setup      # clean checkout -> green health. Installs uv, pins 3.12.
make dev        # one instance: api :8000 + vite :5173. No hub, no sign-in
make dev-hub    # OPTIONAL: + hub :8180 + `stripe listen`. Only for several
                #   instances behind one sign-in; needs .env.hub
make health     # api / neo4j / claude CLI auth
make test       # offline, no network, no model spend
make ui-smoke   # THROWAWAY INSTANCES ONLY. needs VENONA_UI_SMOKE=1 — it WRITES
make ext-build  # browser extension -> extension/dist (regenerates its styles)
make ext-smoke  # loads the extension in headless Chrome. READ-ONLY, safe anywhere
make lint-readonly # proves nothing under api/mcp/ writes (contract C12)
make lint-repositories # proves no SQL lives outside db/
make lint       # ruff + tokens + determinism + repositories + domain + ontology + generated + bundle + vue-tsc
make seed       # declarative fixtures, <10s, no model calls
make record     # capture live envelopes into fixtures/
make reset      # wipe volumes + db, re-migrate, re-seed
make stop       # reclaim :8000/:5173 from stale dev servers
make stop-stack # the same, plus the hub :8180
make ports      # who is listening (read-only)
make new-local-instance SLUG=x  # scaffold a second instance on its own port block
```

**Every lifecycle target takes `INSTANCE=<slug>`** (`dev`,
`up`, `down`, `wait`, `stop`, `ports`, `health`): each scaffolded instance under
`local/<slug>.env` is its own compose project on its own ports, so several
venonas run side by side on this machine. Bare invocations act on the default
(defense) instance exactly as they always have. The two hazards below apply
PER INSTANCE -- and the `.py` hazard applies across all of them at once,
because every instance's uvicorn watches the same working directory.

**Never leave `make dev` running in the background.** It holds :8000 and :5173
(and `dev-hub` :8180 too), and the next `make dev` — including the user's — fails with
"Address already in use". Start it, verify, then stop it.

**The hub is optional and SHARED.** A single instance never needs it. When
several instances sit behind one sign-in, the hub is the OIDC issuer for all of
them: run `make dev-hub` once and `make dev INSTANCE=<slug>` for each other
instance.

**Never write a `.py` file anywhere in the repo while a run is in flight.**
`make dev` starts uvicorn with `--reload`, and uvicorn watches the **working
directory**, not `--app-dir backend` — so this covers `hub/` and `tests/` too,
and `make hub-dev`'s reloader watches the same tree from the other direction. So editing a test, a script, or a backend
module restarts the API — and a run executes as a task *inside* that process, so
it dies mid-flight with `interrupted (process died)`. This killed two
`lookup_all` sweeps and about $1.20 before the pattern was obvious: the first
died on a `backend/` edit, the second on a new file in `tests/` and `scripts/`.
Finish every `.py` change first, then start the run, then touch only `.md`,
`.vue`, `.sql` and `.yaml` until it lands. Reading files, `curl`, and read-only
read-only database queries are all safe.

**Always `uv run`, never bare `python`/`pip`/`pytest`.** Python is pinned to
3.12; the system Python is 3.14 and `lxml`/`trafilatura`/`neo4j` wheels lag.

## Prohibitions

Each of these has cost someone a day somewhere. The reason matters as much as
the rule.

- **No raw color outside `web/src/styles/tokens.css`.** *Light mode becomes a
  rewrite.*
- **No arbitrary dimension in a template** — `p-[7px]`, `h-[460px]`,
  `text-[14px]`. *A scale nothing enforces is a suggestion; this one drifted to
  nine paddings and eight gaps before anything noticed.* `h-(--control-h)` is
  fine — it resolves through a token. Add a token before you add a literal.
- **No `<style>` block in a component.** *Styling is utilities; the two files on
  `lint_tokens.py`'s allowlist say why they are there -- `GraphView.vue`
  transitions a grid track list, which utilities cannot express, and
  `ViewShell.vue` owns the prose grid.*
- **Never put a utility and its conditional override in different attributes.**
  *They tie on specificity, and Tailwind's sort order decides — not yours.* Pick
  one branch per property.
- **Never interpolate a class name** (`bg-${tone}`). *Tailwind scans source
  text; the class is never generated and the failure looks like a data bug.*
- **No tier-1 primitive (`--grey-*`, `--aqua-*`, …) referenced from a
  component.** *Primitives carry no meaning; using one opts out of theming.*
  `lint_tokens.py` derives the family list from `tokens.css`'s own tier-1 block
  now — it was hand-maintained and had been missing `--moss-`, `--plum-` and
  `--rose-` since they were added.
- **Never put the Cytoscape instance in `ref`/`reactive`/Pinia — use `markRaw`
  or a module closure.** *Vue's proxy over its element graph makes it unusable
  at a few hundred nodes, and it is very painful to retrofit.*
- **Never ask a model for character offsets.** *Models cannot count characters;
  drift grows with document length and every miss is a visibly wrong highlight.*
- **Never let a model emit a canonical entity id.** *It will invent one.* Models
  return surfaces; `resolve.py` assigns ids.
- **Never invoke a model service's binary outside `llm/executor.py`.** *Budget,
  the usage ledger, and offline replay all live there, and every
  `create_subprocess_exec` in `llm/` is in that one module -- three harnesses,
  one spawn point.*
- **Never write a user-editable config file outside `services/config_service.py`.**
  *The allowlist, the validate-through-the-real-loader rule, the `.history/`
  snapshot and the cache invalidation all live there.* Adding a document is an
  entry in its `EDITABLE` table; the Admin rail is built from it.
- **Never bind ANY ontology-derived list at import time -- predicates or
  entity types.** *The ontology is editable over HTTP and a UI-triggered run
  executes in the API process, so a snapshot silently drops whatever was just
  saved: a predicate the model was told to use, or -- with per-domain type
  sets -- a type it was told to emit.* Read from `get_ontology()` at the
  point of use; `db/models.py` and `graph/writer.py` are the pattern.
- **Never `UPDATE` an `assertions` row.** *Merges are `entity_mappings` rows.*
  Rewriting assertions destroys invariant 1 and makes unmerge lossy.
- **Never write Cypher `MERGE` outside `graph/writer.py`.** *The predicate
  whitelist is enforced there.*
- **Never write SQL outside `db/repositories/`.** *A raw `conn.execute` looks
  exactly like a repository call at the call site, so the boundary leaks back one
  convenient statement at a time and the leak is invisible in review.* Every
  statement moved; `make lint-repositories` walks every module outside
  `db/repositories/` and fails on the first one that grows a statement back. This is what makes the
  read-scope seam enforceable — a query assembled by `str.format` in a router
  cannot be scoped by anything.
- **Never expose a write or a spend through `api/mcp/`.** *An agent that can
  call it will call it in a loop, and there is no price quote in front of it.*
  The MCP surface is read-only (C12), held by `make lint-readonly`, a
  symbol-level import allowlist and a row-count sweep over the registry. If a
  tool needs to write, what you are adding is a run type.
- **Never accept an acronym expansion without article id + verbatim quote +
  initials check.** *A wrong definition becomes an alias and corrupts entity
  resolution downstream.*
- **Never treat a call with non-empty `permission_denials` as a result.**
  *Verified: `--permission-mode dontAsk` denies WebSearch/WebFetch while exiting
  0 with `is_error: false`. Silently returns "found nothing", forever.*
- **Never check `stop_reason` for success.** *It is `"tool_use"` on success when
  `--json-schema` is used.*
- **Never use `--bare`.** *It forces `ANTHROPIC_API_KEY`, bypassing the
  subscription this project runs on.*
- **Never call `datetime.now()`, `uuid4()`, `random.*`, or construct `httpx`
  clients in `pipeline/`, `services/`, `llm/`, `graph/`.** Use `ctx.clock`,
  `ctx.ids`, `ctx.rng`, `ctx.http`. *Offline determinism.*
- **Never add a second implementation of a `components/ui/` primitive.**
- **Never hand-edit generated files:** `docs/schema.sql`, `docs/graph-schema.md`,
  `web/src/api/schema.d.ts`, the copies of the error pages' graph
  field -- `web/src/components/shared/GraphField.vue`, `hub/hub/field.py`,
  `hub/hub/static/constellation.js` -- and
  `backend/venona/export/palette.py`, which is the entity hues baked for the
  API, whose image ships no stylesheet to read them from. All are
  committed and `make lint` fails on drift -- `lint-ontology` guards the first
  two, `lint-generated` the rest. Regenerate with `make schema` / `make types` /
  `make field`. (`docs/tokens.md` was listed here and has never existed.)
  The field is generated rather than copied because the surfaces name
  colours in different vocabularies; `scripts/graph_field.py` carries the geometry
  and resolves the hub's hex pairs out of `web/src/styles/tokens.css`.
  **Two more belong to the hub's front door**, which ships with no bundle and so
  cannot import anything: `hub/hub/static/public.css` (the whole stylesheet --
  the four `@font-face` rules, the token block resolved out of
  `web/src/styles/tokens.css`, then the rules appended from
  `hub/hub/styles/public.css`, which IS hand-written and is where to edit) and
  `hub/hub/assets.py`, which carries its digest URL. `make hub-public`.
- **Never change a contract in `docs/CONTRACTS.md` without doing its "changing
  this requires touching" list in the same commit.**
- **Never let a partially useful run report as `failed`** — that is
  `succeeded_partial`. **And never let a run that simply FOUND NOTHING report
  as `succeeded_partial`** — that is `outcome`. *The two were one field, and
  44 of 68 lookups read "degraded" with an empty warnings array and a null
  error, because "the web holds no definition for this" had nowhere else to
  go. A status that cries wolf on two rows in three is the same damage as one
  that calls a partial run failed.* `status` is whether the machinery worked;
  `runs.outcome` is whether there was an answer. `LaunchOutcome.status`
  defaults to None and derives from `ctx.degraded`, so leave it unset and state
  the outcome. ADR 0183.
- **Never reach for `ctx.warn` to say something the reader lost nothing by.**
  *`warn` is the only thing that sets `ctx.degraded`, so every use of it for a
  routine fact is a run reporting damage it did not take. The quiet night was
  the worst of them: `_empty_retrieval_reason` wrote into `BriefReport.error`,
  the orchestrator warned on every value of that field, and so a window with
  nothing in it reported "degraded" for months while `docs/RUNBOOK.md`
  documented the same case as `succeeded` + `nothing_to_do`.* Three channels,
  and the test is what the READER lost: `ctx.warn` when they got less than they
  should have, `ctx.todo(text, to=...)` when a person has to rule on it (the
  curation queue), `ctx.note` for everything else. A message carries its own
  level to the wire, so a surface never has to infer one from the run's status.
  ADR 0194.
- **Never name a CLI command in a run message.** *It renders as a list item on
  `/admin/runs/:id`, where the reader is not at a shell, and `venona graph rebuild`
  is not even their job.* Say what happened; if a surface can act on it, that
  is what `todo`'s `to` is for. `tests/test_copy_style.py` holds this, along
  with the internal vocabulary that shipped beside it (`gazetteer`,
  `ambiguous_surfaces`, raw `cluster_id`s) — nothing checked these strings at
  all until 2026-09-01, which is why they were the worst copy in the app.
- **Never ask the run registry a global "is anything running" question — ask the
  lane.** *A single global slot made grounding one acronym impossible for the
  tens of minutes a research run takes, which is exactly when someone is reading
  a brief and hits a term they don't know.* `registry.active_run_id(RESEARCH)`,
  `lane_conflict(conn, lane=...)`. Contract C11.
- **Never start a run outside `services/run_launcher.py`.** *It is the only
  place a lane slot is claimed and released, and a leaked slot strands every run
  queued behind it — permanently, and silently.*
- **Never add middleware assuming first-added is outermost — it is the
  reverse.** *Starlette's `add_middleware` inserts at position 0 and builds the
  stack over `reversed(...)`, so the LAST added is the OUTERMOST. Get it
  backwards and every 401 and 429 ships without security headers, while every
  unit test still passes — the ordering only shows on short-circuited
  responses.* There is a test that a 401 carries `X-Content-Type-Options`.
- **Never stamp a response header for an unhandled exception from user
  middleware.** *`ServerErrorMiddleware` sits OUTSIDE the user stack and is what
  writes that response, so a send-wrapper never sees it.* The handlers in
  `api/errors.py` stamp the request id themselves for exactly this reason.
- **Never assume `db.transaction(conn)` is enough for read-then-write.** *At READ
  COMMITTED two transactions can both read the same value and both write; the
  second either loses the update or violates a primary key.* How a transaction
  BEGINS cannot fix that, so it is fixed at the site: `SELECT ... FOR UPDATE`, an
  atomic `UPDATE ... RETURNING`, or `pg_advisory_xact_lock` keyed on the row.
  `run_service.append_event` is the one to copy. There is no `immediate=`
  parameter to reach for -- it selected `BEGIN IMMEDIATE` on SQLite and has no
  Postgres equivalent; see `db/core.py`.
- **Never nest `db.transaction`, and never `except IntegrityError: continue`
  inside one.** *A second `BEGIN` is ignored with a warning, so the inner
  `COMMIT` ends the OUTER transaction and everything after it commits one
  statement at a time -- atomicity gone, nothing failed. And a failed statement
  aborts the whole transaction, so the `continue` that looks like "skip this
  row" actually makes every later statement raise `InFailedSqlTransaction`.*
  `db.transaction` refuses to nest, and `db.savepoint` is the nested unit of
  work that is allowed to fail.
- **Never let alembic configure logging from inside the process.** *`fileConfig`
  disables every logger that already exists and installs a WARNING-level root
  handler; `db.migrate` runs in the lifespan, at 06:00 and per run, so after the
  first one the API, the scheduler and the circuit breaker logged nothing --
  two days of production logs, four runs, zero application lines.* `db.core.
  migrate` sets `configure_logging=False` and `env.py` honours it; only the
  `alembic` command line gets the ini's logging. `test_migrate_keeps_logging`.
- **Never give a subprocess a stdin pipe it will not read.** *`communicate(b"")`
  still WRITES; `claude --version` exits without reading; under uvloop, on a
  loop busy with four streaming calls, the pipe closes before the write lands
  and the write raises a `RuntimeError` that `communicate()` does not swallow.
  That was "Claude goes down whenever runs overlap": the health probe failed,
  `/api/health` said `ok: false`, and the Runs page disabled itself for the
  length of every concurrent run while every real call succeeded.* Probes use
  `stdin=DEVNULL`; every stdin pump treats `RuntimeError` like
  `BrokenPipeError`; a probe retries once. ADR 0155 addendum.
- **Never let `BreakerOpen` reach a stage.** *It is not an `LlmError` (nothing
  was asked), so every `except LlmError` degrade path lets it through and the
  run aborts at the first refusal -- no brief, `curate` and the projection
  skipped.* `call_and_record` and `analyze_document` turn it into a failed
  result; a refusal then degrades exactly like an `is_error` envelope. And the
  research lane WAITS through a transient outage before refusing at all
  (`llm.outage_wait_seconds`, ADR 0155); only the light lane fails fast.

- **No change-narration in a comment.** *A comment describes the code as it
  stands, to a reader who has never seen an earlier version.* "used to", "no
  longer", "previously", "this replaced", "was three copies" — that is what the
  commit message is for, and it is the one place the sentence stays true. If the
  phrase is describing runtime state rather than an edit, reword it (`the row is
  preserved but stops annotating`) or append `comment-history: ok` with the
  reason. `make lint-comments`.
- **No comment restating the line beneath it.** *It is the same sentence twice,
  and only one of them is checked by anything.*
- **No comment in a file that ships unminified** — `web/index.html`,
  `*/public/**`, `extension/options.html`.
  *`tooling/vite-strip-comments.mjs` strips them at build time and
  `lint-comments` checks the built output, but do not write one expecting the
  build to hide it.*
- **Never invent words for a run outcome at the render site.** *Four surfaces
  decided this independently and one of them was wrong: the curation toast read
  `status === 'succeeded' ? 'lookup finished' : 'lookup found nothing new'`, so
  a lookup that hit its call ceiling told a curator the web held no definition
  — our budget reported as a fact about the world.* The phrase per kind lives in
  `run_types.RunType.outcomes` and reaches the client as `outcome_text`;
  `web/src/lib/runOutcome.ts` is the only place a run becomes a chip.
- **No internal vocabulary on a user-facing surface — and admins are users
  too.** *"Corpus", "entity", "assertion", "gloss", "lane", "projection",
  "freshness", "instance", "MCP" are this repo's words; a reader has never
  heard them, and every /admin and /settings screen is read by a person, not
  a developer.* Say what the thing is in the reader's world (Venona's
  reading, names, relationships, a run in progress). `web/src/lib/
  learnCopy.ts` is the translation-layer pattern when a wire value must
  render. /learn shipped full of schema words and had to be rewritten on
  operator feedback (2026-08-24); write it in reader words the first time. No em or en dashes in any user-facing copy
  either — recast the sentence (`tests/test_copy_style.py` holds that half).

## If you're tempted to…

**…send offsets to the client instead of pre-split runs.** Don't. Python
offsets are code points, JavaScript's are UTF-16 units. One astral-plane
character in a headline shifts every highlight in that block, and the bug will
be blamed on the model.

**…auto-merge two high-similarity entities.** Don't. Merges are proposals the
user approves. An incorrect auto-merge is invisible and poisons every downstream
query.

*Scoped, 2026-08-06.* This bans merging on SIMILARITY. `curate` now auto-merges
provable identity — `curate.py::identical_pairs`, gated by
`pipeline.auto_merge_identical`: names identical once a trailing `(ACRONYM)`
gloss and hyphens are normalised, or one name that IS an acronym the other
generates. Derivational, not fuzzy; 28 of 29 live duplicates, with the one typo
pair (`Partick`/`Patrick`) left for a human. Both premises of the ban are also
gone: `entity_mappings` makes it reversible and `venona entity merges` lists
every one. Collisions and containment are never eligible.

*Widened, 2026-08-27 (ADR 0174).* Similarity is still banned. Three more
derivational classes are eligible, each bounded by a list a human wrote in
`profile/lexicon.yaml` so an incomplete list misses a merge rather than making a
wrong one: `corporate_form` tokens (`Palantir` / `Palantir Technologies`),
`generic_tails` tokens when they TRAIL the longer name (`Maven Smart System
platform`), and one entity's whole name being a surface a person DECLARED an
alias of another. Containment is now eligible when one of those explains it —
the resolver's veto means "do not match on this", not "these are two things".
And separately, a **model** `merge` verdict at `confidence: certain` is applied
without a curator when the names corroborate it structurally, gated by
`pipeline.auto_merge_model_verdicts`. That last one IS a reversal of "merges are
proposals the user approves"; its budget and reversal trigger are in the ADR.
Cross-type pairs are never eligible at all.

**…add a source adapter in Python.** Check first — adding a source should be a
`config/sources.yaml` change and a fixture, nothing more. If you need Python,
the adapter protocol is wrong; fix that instead.

**…remove an argument because its value is unused.** "The value is unused" is
not "the parameter is optional". Four `confidence=0.7` arguments were verified
dead — `confidence_for` discards them for `web_grounded` — and removing them
broke the acronyms stage of a live run, because the parameter was still
required. Check the signature, not just the dataflow.

## Conventions

- `async` throughout `pipeline/` and `api/`.
- **A pydantic model at every API boundary**, enforced by
  `tests/test_route_contracts.py`. A route with no `response_model` fails that
  test unless it is in its `EXEMPT` table with the reason a model would break
  it — today: the SSE stream, two auth redirects and a 204. Note that
  `response_model` **filters**: a field the model omits vanishes from the wire
  silently, and a field typed wrongly is a 500 at serialization. Use the
  `api_client` fixture, which serves the real routers over a throwaway database.
- `snake_case` on the wire, no camelCase mapping layer.
- ISO-8601 UTC with `Z` for every timestamp.
- Ids are strings and always type-prefixed. The prefixes come from
  `config/ontology.yaml` and are per-domain, so never freeze the list — derive it
  from `get_ontology().type_prefix` plus `gloss:`, as `db/models.py::id_pattern`
  does. The defense instance's are `company: govt: unit: research: prog: tech:
  plat: person: country:`; `org:` was retired in ontology v3.
- Stages read their inputs from the database by `run_id`; a stage's return value is
  metadata only. This is what makes `venona run --only <stage>` work.
- Comments explain **why**; the code already says what. Keep one to the sentence
  that carries the reason. A comment that needs a second paragraph to justify
  itself is documentation and belongs in `docs/` — with the load-bearing line
  left behind and a pointer to it. `make lint-comments` holds the density.

  **Run `uv run python scripts/lint_comments.py --budget` BEFORE writing a
  slice's comments, not after.** It prints how many counted lines are going
  spare. The repo has sat within a line or two of the ceiling for months, so
  the budget is usually small and a slice that writes freely and checks at the
  end unpicks itself backwards — landing on whatever prose is easiest to cut
  rather than whatever is least load-bearing. One session spent more than a
  dozen tool calls doing exactly that.

  Three facts that cost time when they are not known. **Only
  `.py .ts .tsx .vue .css .mjs .js` count** — `tests/`, `.sql`, `.yaml`,
  Dockerfiles and `.md` are free, so trimming a test file does nothing and the
  reasoning that will not fit belongs in an ADR. **Trimming means DELETING
  lines**; rewording a paragraph shorter nets zero and the ratio will not move.
  And the check is a **delta**: over the ceiling, a change may not raise the
  ratio, so the lines to cut are the ones this change added.

## Definition of done for a slice

**`make dev` must come up clean before any slice is called finished.** Not
`make test` alone -- the app actually running is the gate. Stop any manually
started servers first (ports 8000 and 5173), then:

```
make dev          # expect: neo4j ok, api on :8000, vite on :5173
curl -s localhost:5173/api/health   # through the proxy, not direct to uvicorn
```

Then load the affected surface in a browser and look at it -- at laptop width
and at 390px (`docs/DESIGN.md` "Responsive": every page on every surface works
on a phone; simpler is fine, overflowing is not). A slice that passes tests but
does not render is not done, and one that renders only at desktop width is not
done either.

## Testing

**`make ui-smoke` needs a throwaway instance and refuses to start without
`VENONA_UI_SMOKE=1`.** It drives the real app against the real database and it
WRITES -- clicks, keys and confirmations are real. It has appended to
`profile/relevance_profile.md` on every run for months, and recorded curation
verdicts on 76 surfaces of a working corpus. `GUARDED_CONFIG` and
`verdict_state` are backstops on that; the fix is a clean instance. Run it
against a fresh checkout and a fresh database, never against a corpus someone
is curating. There is no CI.

**`make ext-smoke` is the opposite and says so.** It only reads — `annotate` and
`cards:batch` write nothing, and it never clicks the button that spends — so it
needs no gate and no clean instance. It loads the built extension into headless
Chrome against `fixtures/ext/article.html` and asserts the things nothing else
can see: that a multi-word entity split across a source newline still resolves,
that `document.body.textContent` is byte-identical before and after splicing,
that the host page's CSS does not erase the underlines, and that the card's
utility layer survived Tailwind's `@source` globs. Every one of those failures
renders as "it looks fine, there are just no mentions".

**What ui-smoke catches, when it does run.** Typecheck,
lint and the offline suite are all blind to a blank canvas, four different
button heights in one bar, or a file marked unsaved the moment you open it --
all three shipped. It drives a headless browser against a running `make dev`.
When a defect gets past you, the fix includes a check in `scripts/ui_smoke.py`.

**Service-level tests do not run FastAPI's response serialization.** Use the
`api_client` fixture in `tests/conftest.py`, which serves the real routers over
a throwaway database — and sandboxes `profile_dir` and `config_dir`, not just
the database. A probe that only moved the database wrote `MOSA` into the real
`profile/lexicon.yaml`, because `config_service` resolves its paths from
settings.

**`HttpClient` is only testable because of the `transport=` seam.**
`build_http_client` returns `ReplayHttpClient` offline, which is a separate
class — so nothing in the offline suite constructs the real one. Use
`httpx.MockTransport`; see `tests/test_http_client.py`.

`make test` is offline and free. `@pytest.mark.live` is opt-in and spends
subscription quota. Regenerate fixtures with `make record`. **A golden-file
update must show its diff in the commit** — an unreviewed golden update is how a
regression gets blessed.

## Layout

```
backend/venona/
  settings.py     layered config; VENONA_OFFLINE=1 flips every determinism knob
  cli.py          `venona` entrypoint
  db/             Postgres: system of record, migrations, the `Conn` shim
  graph/          Neo4j projection: client, schema, writer, projector
  sources/        adapters (rss, scraper, discovery) over a shared http client
  pipeline/       the 6 stages + RunContext
  llm/            the ONLY place `claude` is invoked
  services/       annotate (markdown->blocks->runs), alias index, run registry,
                  config_service (the ONLY write path for editable config)
  api/            routers + pydantic schemas
  api/mcp/        the MCP server for agents: read-only tools over JSON-RPC.
                  `registry.py` is the only place a tool is declared.
hub/              the OIDC issuer and directory: its own uv package, no
                  `venona.*` imports. `hub/hub/` is the server-rendered front
                  door (sign-in, chooser, refusals -- no bundle by design);
                  `hub/web/` is the operator app behind the session gate, its
                  own Vue package on the app's own tokens (`make hub-web`).
extension/        Chrome MV3: annotates third-party pages via POST /api/annotate.
                  All network in the service worker; the content script holds
                  no token. `generated/` and `dist/` are build output.
config/           settings.yaml, sources.yaml, ontology.yaml
profile/          USER-EDITABLE: relevance profile, prompts, rubric, sections,
                  lexicon, anchors
fixtures/         recorded HTTP + LLM envelopes, seed data, golden files
web/src/          Vue 3: components/ui is the only place bare controls are styled
docs/             CONTRACTS, DATA-MODEL, DESIGN, EXTENDING, RUNBOOK, adr/, pm/
```

## Docs to read before changing things

- `docs/CONTRACTS.md` — the twelve frozen interfaces. Read before touching a
  boundary.
- `docs/EXTENDING.md` — numbered checklists for the common changes.
- `docs/adr/` — decision records, one file per decision, indexed in
  `INDEX.md`. Check here before "restoring" something that looks wrong; write
  one (the `/adr` skill) when a decision is made or reversed.
