# Extending venona

Numbered file-touch checklists, each ending in the test that proves it. This is
the doc that keeps future work on-rails — if a change isn't described here and
isn't in `CONTRACTS.md`, think about whether the architecture is fighting you.

---

## Add a news source

**No Python.** If you find yourself writing an adapter, the protocol is wrong —
fix the protocol instead.

1. Add a block in **Admin → Sources**, or edit `config/sources.yaml` directly
   (schema in C9 — both go through the same validator):
   ```yaml
   - id: my_source
     name: My Source
     kind: rss            # rss | discovery
     url: https://example.com/feed/
     publisher: My Source
     enabled: true
   ```
   `kind: discovery` takes no `url` — it searches rather than fetching an
   endpoint. Its `tags:` become the themes it rotates through.

   There is no `scraper` kind. Every candidate site was a JS-rendered SPA
   serving no content links; they need a JSON-payload adapter, which is not
   built (see `docs/adr/`).
2. `make verify-sources` — confirms it returns 200 and parses. Discovery
   sources are reported as `search` and not probed; there is nothing to GET.
3. Record a fixture: `make record`, then commit the new files under
   `fixtures/http/` (flat, one `<sha>.meta` + `<sha>.body` pair per URL — the
   sha is the cache key, so there is no per-source subdirectory).
4. `make test` — the offline suite now covers it.

**Steps 2-4 are terminal-only, and the UI cannot do them for you.** A source
added in Settings is live on the next run and has no fixture, so the offline
suite silently stops covering the set. Adding one in the browser is finishing
step 1, not finishing the checklist.

**The browser extension picks it up on its own.** `GET /api/sources/sites`
unions this file's enabled `rss` hosts with the domains the corpus has produced
documents from, so a new source appears in the extension's options page within
one catalog refresh (~6 hours, or immediately on Refresh) with no manifest edit
and no rebuild. It appears as an **ungranted** row: the list is offered, never
applied, and the host still needs a permission the user approves. A
`kind: discovery` sweep carries no `url`, so its finds show up only once they
have actually produced a document — which is the only place they are visible at
all.

**Removing a source:** set `enabled: false` rather than deleting. Its documents
and assertions stay, and the graph is unaffected. Note that a source
auto-disabled by three consecutive failures is a different state, held in
`source_health` and not in this file — editing the YAML does not revive it, and
`venona sources --enable <id>` is what clears the strikes.

---

## Add an entity type

The ontology is the source; almost everything follows automatically.

1. Add it to `config/ontology.yaml` under `entity_types`, with a unique
   `prefix`, a unique `color_slot` (1..`ontology.MAX_ENTITY_TYPES` — the slot
   picks its colour from the `--entity-slot-*` palette; the app maps
   `--entity-<prefix>` onto it at boot, so no CSS change is needed), a
   `description`, `guidance` and `examples`. Write the guidance for the
   *model* — it is injected verbatim into the analyze system prompt.
2. Add any predicates that reference it (see below), or extend the `domain` /
   `range` of existing ones.
3. Bump `version` and add a `changelog` entry — a type-set change is a
   contract change (C10) and the editor refuses it without one.
4. `uv run venona ontology check` — validates prefixes, slots and that every
   domain/range resolves.
5. For the checked-in defense ontology only: seed at least three examples in
   `fixtures/seed/entities.yaml` so the dev corpus has something to render.
   Generated domains skip this — their trunk is `profile/anchors.yaml`.
6. `make schema` regenerates `docs/graph-schema.md`; commit it.
7. `uv run venona graph rebuild && make test`. An added type begins
   projecting once entities exist; a removed one stops projecting, its nodes
   are pruned on the next projection, and its assertions stay on disk and
   return if the type is re-added.

No changes to `db/models.py`, `graph/writer.py`, `graph/projector.py`, the API,
or the frontend — all of them read the ontology.

**If the type already exists in the graph under another type** — as countries
did, minted as Organizations — ids are immutable (C4), so retyping is a merge
into a new id, not a rename. Declare the new entity in `profile/anchors.yaml`
and add a `merges:` row; `venona entity unmerge` reverses it. Expect a few
assertions to stop type-checking and show up in the projector's `rejected`
count. Read them: some will be claims that were always wrong and that the
ontology could not see until the types were right.

---

## Add a top-level anchor

`profile/anchors.yaml` declares the trunk of the graph — 264 entities as of
ontology v3: countries, departments, services, program offices, the primes and
defense-tech companies, tracked programs, and the technologies the profile bets
on. None of it is extractable, because no article states that Anduril is a
company and the Army is not, or that PEO C3N buys and RTX sells. It is
user-editable and loaded separately from `fixtures/seed/`.

Identity only. Nothing here carries a definition — invariant 3 — so a declared
entity renders as visibly undefined until a real document defines it.

1. Add the entity under `entities:`, **only if the id does not already exist**.
   Set `name_strictness: 0` on anything whose name is a common phrase: an
   anchor is structure, not an annotation target, and at the default a
   13-character name matches globally and steals highlights from more specific
   entities.
2. Attach existing entities under `edges:` by id. `PART_OF` for the org chart
   (child → parent), `BASED_IN` for nationality (organisation → country).
3. `uv run venona anchors load --dry-run`, read it, then without the flag.
   Every edge is type-checked and a dangling endpoint is an error.
4. `uv run venona anchors load --apply-merges` if you added a `merges:` row.
   Separate on purpose — loading structure and collapsing entities are
   different decisions.
5. `uv run venona graph rebuild && uv run venona anchors status`.

**Never run `venona seed` against a live database.** It also loads a fictional
brief and four invented documents, and a reader's first brief must not be able
to cite one. `make bootstrap` is the real first-run path — wipe plus anchors,
nothing invented; `make reset` adds `seed` on top and is for developing the UI.

The id-collision hazard this warning also carried (`org:us-army` in the fixture
where the pipeline minted `org:u-s-army`) is gone: seeding a CLEAN database
lands the declared ids before anything is minted, so they become the canonical
ones, which is the whole point of seeding. It returns the moment you seed on top
of a corpus, so the rule stands.

To attach the long tail, `uv run venona anchors propose` files proposals into
the existing curation queue as `kind='anchor'` rows. Nothing reaches the graph
without a keystroke. Run it with `--no-model` first and read ten by hand.

---

## Add or change a predicate

1. Add it under `predicates:` in `config/ontology.yaml` with `domain`, `range`,
   `description`, `guidance` and a concrete `example`. The example is what the
   model reads to get the direction right — make it unambiguous.
2. Set `symmetric: true` only if the relationship genuinely has no direction;
   symmetric edges are stored once with endpoints in lexicographic id order,
   and domain must equal range.
3. Bump `version` and add a `changelog` entry.
4. `uv run venona ontology check && make schema`.
5. `uv run venona graph rebuild`.

**Retiring a predicate:** remove it from the YAML. Its assertions stay on disk
and simply stop projecting — re-adding it brings them back. This is only safe
because the graph is a projection (C1).

**Changing a domain/range:** existing assertions that no longer type-check are
reported by the projector as `rejected` and excluded. Check that count after a
rebuild; a large number means the change was wrong.

---

## Add a scoring axis

**No code.**

1. Add a block to `profile/scoring_rubric.yaml` with an `id`, `weight` and
   `description`.
2. Make the weights sum to 1.0 — validation rejects otherwise.
3. `POST /api/rescore` — reweighting is a pure Python rollup, so it costs
   **zero model calls**. Changing an axis *description* changes what the model
   is asked and does require re-analysis.

The JSON Schema handed to the model is generated from this file at runtime, so
axis names are never hardcoded.

---

## Add an LLM task

1. Add the system prompt to `llm/prompts.py` — a constant, or a builder if it
   composes profile files and generated material. This is the FULL system
   prompt: `--system-prompt-file` replaces Claude Code's default rather than
   appending to it, which is where the ~9.2k-token saving comes from.

   (There is no `prompts/` directory in practice. `Settings.prompts_dir` is
   declared and read nowhere, and the executor writes the assembled prefix to
   `data/cache/system_prompts/<sha>.md` itself.)
2. Define the output schema in `llm/schemas.py`. Use enums wherever the
   ontology already constrains the value, and generate them from the ontology
   rather than typing them out.
3. Call it: `await ctx.llm.call("<task>", user_content=..., schema=...)`.
   **Never invoke the `claude` binary directly** — budget, ledger and replay all
   live in the executor.
4. Add the model/effort row to `config/settings.yaml` under `llm:`, with a
   comment saying why that tier. (This step used to point at
   `docs/ARCHITECTURE.md`, which has never existed.)

   **A key added to the shipped file needs a migration to carry it**, or every
   instance that already exists never receives it. Write one under
   `services/config_migrations/` (`AddKey`, with the comment lines in its
   `block` — indented two spaces, as the file will hold them) and bump
   `settings_version`. `test_replaying_from_nothing_reproduces_the_shipped_values`
   is the guard, and it names the difference rather than letting the knob go
   quietly missing.

   If the call is a long one, do NOT raise `llm.timeout_seconds` — that is
   every lane's ceiling and the only thing that reclaims a wedged subprocess.
   Add a `TASK_TIMEOUT_DEFAULTS` entry in `settings.py` with the measurement in
   the commit; ADR 0190.
5. Add it to `config_service.ASSEMBLED` so Settings can show what the model is
   actually told. Read-only, and it calls your builder rather than restating it.
6. `make record` to capture a live envelope into `fixtures/llm/<task>/`, and
   commit it. Store the **full** envelope, not just `structured_output`.

If the task needs tools, ask for a CAPABILITY rather than a vendor's spelling:
`tools=(WEB_SEARCH, WEB_FETCH)` from `llm/harness`. Each service maps those to
its own flags, and a service that lacks one says so — a required capability it
cannot serve refuses the call, a preferred one is dropped with a `ctx.note`.
Claude Code's spelling still works and resolves to the same thing.

Step 6's fixture directory is **per service**: `fixtures/llm/<task>/` for
Claude Code and `fixtures/llm/_codex/<task>/` for Codex. See
`fixtures/llm/README.md` for which envelopes replay by key and which are parsed
by path.

---

## Add a model service

The table is the interface. Adding one is a module, a row, an install line and
a fixture directory — and no change to any call site, stage, `RunContext`,
ledger, admin page or run surface. If you find yourself editing one of those,
the protocol is missing a method; add it there instead.

1. Write `llm/harness/<name>.py`. Implement every method on the `Harness`
   protocol in `llm/harness/__init__.py`. The ones people forget are the five
   that exist because the logic was Claude-shaped in code that looked general:
   `classify` (error vocabularies are symbolic, and substring-matching `500`
   against error text hits a request id roughly one time in sixteen),
   `is_transcript_event` (nothing is typed `result` outside Claude Code, so an
   unfiltered stream persists a whole brief into `run_events`), `summarize`,
   `adapt_schema`, and `parse_probe` (`codex --version` prints
   `codex-cli 0.151.0`, so `split()[0]` yields the wrong half and the pin check
   fails forever).
2. Add the row to `_table()` in `llm/harness/__init__.py`.
3. Declare `wants_confinement`. `True` if the service can run code or open a
   file; `False` is a claim you have to be able to defend. See ADR 0219 —
   the path sets are shared, so this is usually the only decision.
4. Decide `reports_usage_windows`. A service that will report a subscription
   window has simply not been asked yet, and the quota strip says so. One that
   never will must not be described that way; it is a promise nothing can keep,
   on the strip a reader consults before starting another run.
5. Add `<name>_cli_path` and `<name>_pinned_version` to `LlmSettings`, both in
   `config_service.FROZEN_FIELDS`. **The binary is a NAME held in code**, never
   a path from the wire — that is what makes the service selector editable over
   HTTP while the path is not.
6. Write the alias table. Tier words AND effort words, pass-through for
   anything absent. **Check the models against the billing mode you actually
   run**: measured, a ChatGPT subscription refuses `gpt-5.4-mini` outright
   while answering on `luna`, `sol` and `terra`, so the cheapest-looking model
   was one no subscription could call.
7. Install the binary wherever you run the API — the WHOLE layout, not just
   the entry point, and assert on something the entry point alone does not
   prove.
8. Record fixtures into `fixtures/llm/<fixture_root>/<task>/`, and make sure
   `test_llm_envelope.py`'s sweep reaches them.
9. Docs: a line in `docs/ADMIN-GUIDE.md` §9, the `docs/RUNBOOK.md` health
   section, and an ADR if anything here was decided rather than discovered.

---

## Add an editable config document

Everything the user can edit goes through one write path, which gives it the
allowlist, `.history/` snapshots, atomic writes and conflict detection for free.

1. Add an entry to `EDITABLE` in `services/config_service.py`: `dir`
   (`profile` | `config` | `ontology`), `file`, `title`, `description`, `group`
   and a `rescore_hint` key. `group` is a key into `GROUPS` in the same file,
   which carries each heading's title, blurb and position; a key nothing
   declares fails `test_every_group_named_by_a_document_exists`. Admin →
   Configuration is generated from these two tables, so there is no frontend
   change.

   `title` and `description` are rendered on that page, so they are read by a
   person: reader words, no em or en dashes.
2. **Validate through the real loader.** Add a `_validate_*` that calls the same
   `parse_*` the startup path calls, and register it in `VALIDATORS`. Do not
   write a second implementation — the point is that a save fails with the error
   boot would have raised.
3. If the value is cached anywhere (`lru_cache`, a module constant, something
   captured in `lifespan`), invalidate it in `_invalidate` — or, if you cannot,
   report it in `restart_required` and say so on screen. A setting that reads as
   applied and is not is the failure this whole path exists to avoid.
4. Add a hint string to `HINTS` in `web/src/lib/configHints.ts` saying what a
   change costs and when it takes effect. It is the Save button's `title`, and
   deliberately only that -- the same sentence standing above the editor on
   every open is explanation between the reader and the file they came to edit.
5. Cover the refusals in `tests/test_config_docs.py`. Every validator there is a
   save that must fail; that is the part worth testing.

---

## Have the setup wizard write that document

The wizard (`services/setup_service.py`) drafts a domain's whole configuration
into `profile_dir/.drafts/`, where the ordinary editor reviews and applies it.

1. Add the document to `DOCUMENT_SPECS` in `llm/setup_prompts.py` — an
   `instruction` and the exemplar the model matches for shape. The exemplar is
   a fixture, never code (ADR 0147).
2. Add it to `PROFILE_DOCS` in `setup_service.py` so the profile stage drafts
   it, and draft it through `_draft_document`, which validates the result
   through the real validator and takes ONE repair retry with the complaint
   attached. Nothing should call the model and store what comes back: the
   ontology stage did, and a single unquoted scalar carrying a colon stored an
   invalid draft, which then refused anchors behind it (ADR 0190's sibling
   finding, epic C).
3. A document whose prompt is its own rather than `DOCUMENT_SPECS`' passes
   `system=` to `_draft_document`. Do not rebuild the prompt inside the repair
   branch from `key` — `DOCUMENT_SPECS` will not have it, and the `KeyError`
   lands inside an `except ConfigError` where nothing expects it.
4. Extend `tests/test_setup_wizard.py`'s task-keyed stub, and
   `tests/test_setup_replay.py` if the document should replay from recorded
   envelopes. Note that the survey stage cannot replay by construction: it
   appends the dateline only when live, so its envelope is keyed on a prompt an
   offline run never assembles.

**Checking the first-boot gate.** A fresh scaffold's first page IS the wizard,
and that redirect has shipped broken once. `scripts/ui_smoke.py`'s
`setup_gate_checks` asserts it, and it can only run against an instance that
has never been configured — every other check in that suite needs a corpus, so
the two never run in the same process. Scaffold one
(`make new-local-instance SLUG=<slug>`), bring it up, and point the suite at its
web port:

    VENONA_UI_SMOKE=1 VENONA_UI_SMOKE_BASE=http://localhost:<WEB_PORT> make ui-smoke

The gate checks run and the suite stops there, reporting only them.

---

## Add a pipeline stage

1. Implement `async def my_stage(ctx: RunContext) -> StageResult` in
   `pipeline/`. Read inputs **from the database by `ctx.run_id`**; the return value is
   metadata only (C7).
2. Register it in `pipeline/orchestrator.py` and add its name to `STAGES`
   **there** — that tuple is the only stage list. (`pipeline/types.py` had a
   second, contradictory one; it was deleted and
   `test_there_is_exactly_one_stage_list` asserts it stays gone.)
3. Add any new counters to `RunCounters` in `pipeline/types.py` — the names are
   frozen by C6.
3b. Do the frontend half, which C6 requires and this list used to omit: the
   `STAGES` literal in `web/src/stores/runs.ts` (scraped by
   `tests/test_cli_run.py::test_the_ui_knows_every_stage`), `COUNTER_LABELS` in
   `web/src/views/RunsView.vue`, and the stage-list copy in
   `services/run_types.py` (`RESEARCH_STAGES`, pinned by
   `tests/test_run_lanes.py`). The stage strip lives in `RunsView.vue`; there
   has never been a `RunProgress.vue`.
4. Use `ctx.clock`, `ctx.ids`, `ctx.rng`, `ctx.http` — **never** `datetime.now()`,
   `uuid4()`, `random.*`, or a bare `httpx` client. `make lint-determinism`
   fails otherwise, and offline replay would stop being reproducible.
5. Emit progress with `await ctx.emit(...)` and call `ctx.check_cancel()`
   between items.
6. Instrument it with a `Funnel` from `pipeline/metrics.py`. This is the whole
   reason that module is small: declare the population, name each drop, and
   storage, the API shape, the UI rendering and a self-checking invariant all
   come for free.

   ```python
   funnel = Funnel("my_stage", offered=len(rows))
   funnel.drop("over_limit", len(rows) - len(kept_rows))   # zero is ignored
   funnel.keep()                                           # per surviving item
   funnel.emit(ctx)                                        # writes run_metrics
   ```

   `offered` is set at CONSTRUCTION, not accumulated — the two truncations that
   matter most (`fetch.py`'s `items[: cap]` and `analyze.py`'s SQL `LIMIT`)
   happen before anything is iterated, so a funnel that counted its own input
   would miss exactly them.

   Anything that is not a population — a cap, a threshold, a ratio — is
   `ctx.metric(stage, key, value, unit=...)` instead, and renders as context
   under the funnel. **Do not** add a `RunCounters` field for it; those thirteen
   names are frozen by C6 and `ctx.counter` raises `KeyError` on anything else
   precisely so new measurements land here.

   No API, schema or migration change is needed. `run_metrics` is flat
   key/value and `MetricFunnel.vue` renders `drop_*` keys it has never heard of.

---

## Add a run type

Contract C11. A run type is a table entry and a `work` closure — if yours needs
more than that, the framework is wrong and should be fixed rather than worked
around. `rewrite` and `definitions` are the worked examples.

1. Add a `RunType` to `RUN_TYPES` in `backend/venona/services/run_types.py`:
   `kind` (the `runs.kind` value), `label` (what a reader sees), `lane`,
   `stages`, `summary` (which counters are worth showing), and `detail`
   (`pipeline` for a stage spine, `single` for a one-line result — and it must
   agree with `len(stages)`, which a test asserts: a one-segment spine states
   something false).
   **Also `facts` and `lines`** — what your run produced and what it says about
   each outcome it can reach. Without them its detail page renders a chip and an
   empty panel; `tests/test_run_facts.py` fails until both are there. ADR 0187.
2. **Pick the lane deliberately.** `research` if it writes `documents`,
   `clusters`, `assertions` or a brief, or if two of them racing would produce
   two answers to one question. `light` if it writes a definition row and little
   else. Getting this wrong the safe way — research — costs responsiveness;
   getting it wrong the other way costs correctness.
3. Write the endpoint as a `work` closure over `run_launcher.launch`:

   ```python
   async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
       ...                                    # target.ctx, target.conn, target.emit
       return run_launcher.LaunchOutcome(status="succeeded", data={...})

   return await run_launcher.launch(request, kind="…", params={…}, work=work, stage="…")
   ```

   `stage="…"` is sugar for "wrap the whole closure in this one stage", which is
   what a single-stage kind wants. A closure that has real phases leaves it off
   and opens its own through `target.scope.stage(name)` — `setup_service` is the
   worked example. Either way `services/run_scope.py` owns the sequence, and the
   launcher writes the terminal row and the `done`: a `work` closure never
   settles its own run.

   From a process with no web server — the CLI — pass
   `run_launcher.host(settings, llm=…, graph=…)` in place of the request.

   `stage=` wraps the work in `stage_started`/`stage_finished` and a `run_stages`
   row. Pass `self_terminating=True` **only** for `execute_run`, which writes its
   own terminal row and `done` event.
4. **Never claim or release the registry yourself.** `launch` is the only place
   that happens; a leaked slot strands every run queued behind it, silently and
   permanently.
5. Put the target in `params` so a history row says what it was about. A queue
   of nine rows all reading "Definition lookup" is not a history.
6. Honour the status rules. **A run that simply found nothing is `succeeded`
   plus an `outcome`, never `succeeded_partial`** — `succeeded_partial` means
   the run DEGRADED, which only `ctx.warn` decides. Leave `LaunchOutcome.status`
   unset so it derives from `ctx.degraded`, and state the outcome instead. A
   `PermissionDenied` is `failed`, never "found nothing" (C2); `launch` handles
   that one for you. ADR 0183.
7. A research-lane type must call `_refuse_if_research_is_busy(settings)` before
   launching — that is the 409, and it also does the cross-process pre-flight.
8. **No frontend change is needed**, once step 1 is complete. The label, stage
   list, counters and layout come from `GET /api/runs/types`, and what the page
   SAYS — the facts panel and the sentence under the verdict — is resolved
   server-side from `facts` and `lines` and arrives on `GET /api/runs/{id}`
   already in words. You only touch a component to add the button that starts
   it.
9. Add a case to `tests/test_run_lanes.py`. The declaration tests in
   `tests/test_run_facts.py` are parametrised over the whole table, so they pick
   your kind up on their own.

### A route whose body is a FILE

Three things the JSON path does for you and this one does not:

1. It cannot carry a `response_model` -- the bytes ARE the response -- so it
   needs an entry in `tests/test_route_contracts.py`'s `EXEMPT` table with the
   reason. Model the ROW it is served from instead, and put that on the listing
   route beside it.
2. Let `FileResponse(path, media_type=..., filename=...)` write
   `Content-Disposition`. Starlette percent-encodes and chooses the quoted or
   the extended form; hand-writing the header is how a name with a space arrives
   truncated. Bound the filename where it becomes a header anyway.
3. On the client, a real `<a href download>` and NOT `apiGet`. The session
   cookie is `SameSite=Lax` and rides a top-level navigation, the SPA is
   same-origin with `/api`, and a navigation is exempt from `client.ts`'s 30s
   timeout that a large file would otherwise hit. Do not reach for
   `Button href`: it forces `target="_blank"`.

`GET /api/data/exports/{id}/download` is the worked example.

## Add an MCP tool

Contract C12. Every tool here is a READ, and that is not a convention you have
to remember: `AuthMiddleware` refuses an agent token on any path but `/api/mcp`,
so a tool that wanted to write would have nowhere to write from. If yours needs
to write, what you are adding is a run type — see above, and read the DECISIONS
entry before proposing it.

1. Add a `Tool` to `TOOLS` in `backend/venona/api/mcp/registry.py`: `name`,
   `title`, `description`, `args_model`, `handler`, and `is_async` if it needs
   the Neo4j driver. **The description is the interface** — it is what a model
   routes on, so say what the tool answers and what an empty result means. If it
   can emit corpus text, append `_UNTRUSTED`.
2. Add its input model to `api/mcp/schemas.py` and its budget to `BUDGET_CHARS`
   in `api/mcp/render.py`. `model_json_schema()` becomes the wire `inputSchema`,
   so bounds go on the model, not in the handler.
3. Write the handler in `api/mcp/tools.py`. **Read through an existing service,
   never raw SQL** — the tools are a second face on the queries the REST API
   already serves, and a private query here is a second definition of what an
   entity is. Add every imported name to `ALLOWED_IMPORTS` in
   `tests/test_mcp_readonly.py`; that allowlist is at SYMBOL level because
   `brief_service` holds `delete_brief` beside the reads.
4. **Return sources with the answer, not on request.** Invariant 3 says a
   definition exists only if it is traceable to a sentence. A tool that returns a
   definition without its quote hands a model a fact it cannot attribute, and the
   model will attribute it anyway.
5. **Wrap every corpus-derived string in `untrust.quote()` or `untrust.inline()`.**
   Not just quotes — an entity name is model-extracted from scraped prose and is
   just as reachable.
6. **A "found nothing" is a result, not a failure.** Raise `ToolError` with a
   sentence saying what to try instead; the dispatcher turns it into
   `isError: true` with the message intact, which is what a model can act on. A
   found-nothing that reads like a broken tool gets retried forever.
7. `render.py` reads NAMED fields. No `model_dump()`, no `dict()`, no `**` — a
   test greps for them, so a field added to `Card` for the reading surface cannot
   leak into a context window by default.
8. Nothing to change on the frontend: **Settings → Agents** renders the tool list
   from `GET /api/meta/mcp`, so a tool that ships is documented on screen.
9. Add its arguments to the sweeps in `tests/test_mcp_readonly.py` and
   `tests/test_mcp_tools.py` — both assert every registered tool is covered, so
   they fail until you do.
10. `make lint-readonly && make test`, then `make dev` and call it from a real
    client.

## Add an API route

Contract C12. The route is protected before you write it: `AuthMiddleware`
guards everything under `/api`, and `authz.role_for_request` answers `admin` for
any route it does not recognise — so the failure mode of forgetting this
checklist is a reader who cannot reach a page, never a write a reader can make.

1. **Write the pydantic response model first**, in `api/schemas/`.
   `tests/test_route_contracts.py` fails a route with no `response_model` unless
   it is in that file's `EXEMPT` table with the reason a model would break it.
   Remember `response_model` **filters**: a field the model omits vanishes from
   the wire silently, and a field typed wrongly is a 500 at serialization.
   `typing.Any` on the handler's return does NOT count as modelled — three OAuth
   routes satisfied the ratchet while modelling nothing, because `Any` is truthy.
2. **Classify it in `api/authz.py`** if a reader may reach it. Only READER and
   PUBLIC routes are listed; everything else is admin by not being listed.
   Classify by ROUTE, never by verb — `POST /api/graph/query` is a read.
3. **Take `conn: db.Conn = Depends(get_db)`.** Opening your own connection is
   for the three places that cannot use `Depends` (the lifespan, the middleware,
   the CLI), and each says why.
4. **Reach for identity with `authz.actor_id(request)` and nothing else**, then
   pass it down as a plain `actor: str | None`. A service that can reach a
   `Request` is a service two callers cannot share (C1, C7).
5. **Write no SQL.** Everything goes through `db/repositories/` — see below.
6. **Refusals are 409 when the caller is allowed to be there and the thing
   cannot happen**, 403 only when their ROLE is the problem. A 403 fires the
   SPA's global forbidden toast ("your account cannot do that on this
   instance"), which is untrue for a refusal and hides the sentence that says
   which rule stopped them.
7. `make types` to regenerate `web/src/api/schema.d.ts` — it is committed and
   `make lint-generated` fails on drift.

---

## Add a paginated list endpoint

Any route that returns rows from a table that grows needs a window from day
one — an unbounded list works until the month it doesn't, and the failure
looks like a slow page rather than a missing LIMIT. The convention (see
`docs/adr/` for why offset over keyset):

1. **Take the window through `deps.page_window(default, cap)`.** It validates
   `limit` (`ge=1` — a non-positive limit reaches Postgres as a negative LIMIT
   and 500s — and `le=cap`) and `offset` once, instead of per-route clamps.
   The default page size is a fact about your surface; pick one.
2. **Return `items: list[…]` plus `total: int`** on the response model, where
   `total` is the WHOLE set matching the current filter, not the page. Surface
   extras (`counts`, `hidden`, `total_pending`) ride alongside; there is no
   generic `Page[T]` because every list model carries different ones.
3. **Give the ORDER BY a unique tiebreak** (`id` — ids are uuid7 and sort by
   creation). A window over an ambiguous order disagrees with itself about
   page two.
4. **Any filter param moves server-side with the paging.** A filter applied
   client-side searches only the rows that happen to be loaded, and a missing
   row reads as missing data.
5. **Client side, build the store on `lib/paging.ts::createPagedList`** —
   append/de-dupe/in-flight-guard/stale-guard live there — and the scroll
   trigger on `composables/useInfiniteScroll.ts` with an explicit root
   (`window` never scrolls in this app) plus a visible "Load more" button as
   the keyboard path. Freshness handlers call `revalidate()` (re-reads the
   loaded window), never `refresh()` (truncates it to one page under the
   reader).
6. **Add the route — with a `limit`/`offset` variant — to
   `tests/test_route_contracts.py::READ_ROUTES`**, and a page-disjointness
   case to `tests/test_pagination.py`. Then `make types`.

**You will be told if you forget.** `test_a_list_over_a_growing_table_takes_a_window`
fails for any GET whose response model has an `items` field and whose handler
takes no `Window`. If the answer genuinely cannot grow — a ranked shortlist, one
row per ontology shape, a list bounded by a table in the code — name the path in
`BOUNDED_ELSEWHERE` with the reason, the same way `EXEMPT` records why a route
carries no `response_model`. An entry there is a claim that the SIZE is bounded,
not a note that nobody has got round to it.

**A cap is sometimes the better answer.** Several lists are dozens of rows and
belong to one screen: two independent lists in one payload (`EntityRecord`),
a surface's readings, a reader's follows. Bounding the query and returning a
count beside it keeps the read honest without an envelope change or a Load-more
nobody scrolls. When you do, any derived flag has to be computed whole-set —
`GET /glossary/{surface}` counts `ambiguous` with its own query precisely
because the entries it returns are a slice.

---

## Add a migration

Alembic owns migrations and EXECUTES the authored `.sql` file rather than
restating it as `op.create_table`, so the reasoning stays in one place and
`venona dump-schema` keeps working.

1. Write `backend/venona/db/migrations/000N_name.sql`. **The comments are the
   point**: this file is where a column's argument lives, and the next person
   reads it before they read the code.
2. Add `backend/venona/db/alembic/versions/000N_name.py` that executes it, with
   `down_revision` set to the previous one.
3. **Mirror every table into `db/tables.py`** — Core metadata, held to the
   migrations by a drift test in BOTH directions, so a column added in one and
   not the other fails rather than diverging.
4. `make schema` to regenerate `docs/schema.sql` (committed, and
   `make lint-ontology` fails on drift).
5. **A nullable actor column is never backfilled.** NULL means the CLI decided,
   or the pipeline did, or the row predates anyone being asked to sign; putting a
   name on a verdict nobody signed invents provenance, which invariant 3 forbids
   everywhere else.
6. `make reset` on a throwaway database, then `make test`.

---

## Add a repository

`make lint-repositories` walks every backend module and fails on the first one
that grows a SQL statement back. That ratchet is what makes the read-scope seam
enforceable — a query assembled by `str.format` in a router cannot be scoped by
anything.

1. One module per table or per closely-joined group, in `db/repositories/`.
   Functions take `conn` first and everything else keyword-only.
2. **A projection several callers share belongs here as a module constant** —
   `ENTRY_SELECT`, `CONFLICT_SELECT`. That is a fact about the table, not about
   any one route. And every read that feeds a row-to-schema function must go
   through it: a bare `SELECT *` beside a joined projection is invisible until
   somebody adds a joined column, at which point the field serves `null` and
   nothing raises.
3. **LEFT JOIN an actor back to an address here**, not per row in the caller — a
   `users` lookup per row is `glossary.py`'s 201-queries-for-200-rows again — and
   LEFT, because the CLI and the pipeline decide things too.
4. **Read-then-write needs more than a transaction.** At READ COMMITTED two
   transactions can both read and both write. Use `SELECT … FOR UPDATE`, an
   atomic `UPDATE … RETURNING`, or `pg_advisory_xact_lock` keyed on the row;
   `run_service.append_event` is the one to copy.
5. `make lint-repositories`.

---

## Add a view

Every route renders exactly one `ViewShell`. There is no second page scaffold,
and adding one is a review failure for the same reason a second `Button` is.

1. **Add the route** in `web/src/router/index.ts` with `meta: { title }` — the
   title becomes the tab title, which is how the back button stays usable.
2. **Pick `width`**: `prose` (the reading measure, `--measure`), `wide` (1100px
   centred), or `full` (edge to edge, the view owns its own grid).
3. **Pick `scroll`, and get this one right.** `page` means the shell scrolls.
   `none` means the shell is a fixed frame and something INSIDE it scrolls —
   used by Curation, whose action bar must never leave the screen, and Graph,
   whose canvas is `position: absolute` against an unbroken `height: 100%`
   chain. Under `scroll="none"` the default slot renders with **no wrapper**, so
   your root element is a direct flex child; use `<Panel fill>` if you want a
   panel whose body scrolls.
4. **Fill the header.** `ViewShell`'s slots are `controls`, `actions`, `notice`,
   `search`, `toggles`, `side`, `loading` and `error`, and they render into
   **`ContextBar`** — the second chrome row — not into `AppHeader`, which is identical on every route.
   (This step said `#meta` and `AppHeader` for several phases. There is no
   `#meta` slot and never was: **identity is a PROP**, `identity="runs · 9 total"`,
   lowercase because it is rendered uppercase with label tracking.)
   - **`controls` is centred on the window**: tabs, then scope chips. The
     trailing edge takes the rest, in order — the filter's glyph (`#search`),
     switches (`#toggles`), then verbs (`#actions`). Tabs are a `SegmentedControl` (never a row of
     chips), the filter is a `FilterField` (never a bare `Input`), and both are
     covered by `tests/test_frontend_drift.py::TestChromeGrammar`. A filter that
     narrows ONE panel rather than the route goes on that panel's `#header`.
   - **At most two actions**, matching `docs/DESIGN.md`'s "three zones, three
     jobs", and at most one of them filled. Beyond that, the overflow belongs in
     the body. (This said three and DESIGN said two; two wins, because `actions`
     is the only place in the chrome a filled button appears.)
   - **`actions` hold verbs.** A link back to where this page hangs off is
     `ViewShell`'s `parent` prop, which renders in the identity zone; a link
     forward to a sibling view is a control, because it changes what you see.
   - **No view title.** The nav already says which view this is; a heading that
     repeats it is the redundancy the single bar exists to remove. Route-level
     identity (a brief's date, a run id) goes in the `identity` prop.
   - **If the route is admin-only, mark it `meta: { operator: true }` AND add the
     matching `operator: true` to its `AppHeader::NAV` and
     `CommandPalette::ROUTES` entries.** Three files, one fact;
     `tests/test_frontend_drift.py::TestOperatorNav` is what stops them drifting.
     If the view polls anything admin-only, gate it in `useAppPolling` too — the
     loop runs every three seconds during a light run, and a reader collects a
     toast per tick for a request they never made.
   - **Add the route's `name` to the owning `AppHeader::NAV` entry's `names`.**
     `isActive` is `names.includes(route.name)`, so a route no entry claims
     renders the whole nav unlit. If the route is reached from the palette, the
     gear or a rail instead, say so in `TestOperatorNav::UNLISTED`; one of the
     two is required and the test names which.
   - Anything longer than a chip — an explanation, a banner with a button —
     goes in `#notice`, which renders full width between the bar and the body.
5. **Pass `:loading` and `:error`.** `ViewShell` renders the standard states, so
   the app degrades one way rather than one way per view. Use `EmptyState` (with
   its hint slot) for "nothing here", never a bare sentence.
6. **Register keys through `useKeyBindings`**, never a `window` listener. The
   registry owns a single capture-phase listener, and the `?` overlay is
   generated from it — so a binding that is not registered does not appear, and
   one that is registered cannot be silently wrong. `/` is not yours to
   register: pass `hotkey` to `FilterField` and it owns the binding, so the row
   leaves the overlay with the field.
7. **If the route is a child of another, pass `parent`.** `{ label, to }`, where
   the label is the parent's name rather than a sentence.
8. **Look at it at 390px** (`docs/DESIGN.md` "Responsive"). No horizontal
   document scroll, controls at `--control-h-touch`, tables in `Table.vue`'s
   scroll container. Anything the generic overflow probe cannot see gets a
   line in `scripts/ui_smoke.py`'s phone pass.
9. `cd web && npm run typecheck && cd .. && make lint-tokens`.

---

## Add a graph scope

A "scope" is the graph's answer to *what is on the canvas* — today `all`, `focus`
and `brief`. The plumbing is deliberately shaped so a fourth is an added entry
rather than an edited branch: `applyVisibility` consumes a set of in-scope ids
and knows nothing about modes at all.

1. **Name it** in `web/src/router/graphRoute.ts`: add the value to `GraphMode`
   and an entry to `GRAPH_MODES` (label + hint). The mode chips are rendered
   from that array, so the control needs no edit.
2. **Decide its query keys.** If it needs a parameter, add it to `GraphIntent`,
   to `fromGraphQuery`/`toGraphQuery`, and to `graphIntentKey`. Encode and decode
   must stay inverses — that property is what stops the view feeding its own
   `router.replace` back into its watcher. Add it to `graphShapeKey` only if
   changing it requires a FETCH; filters and the brief breadcrumb deliberately
   do not.
3. **Decide its orphan default** in `hidesIsolatedByDefault`. Hiding the
   unconnected is right where their count is what makes the view unreadable, and
   wrong in a view small enough to have no legibility problem.
4. **Produce a scope set.** Either a `Set<string>` handed to `applyVisibility`
   (as `focus` does, via the pure BFS in `web/src/lib/graphFocus.ts`), or a
   loader that replaces the store's contents (as `brief` does, via
   `graph.loadBrief`). The first is instant and exact over the resident corpus;
   the second is for a scope the client cannot derive.
5. **Add one branch** to `applyIntent`'s scope dispatch in `GraphView`, and one
   to `setMode`. Nothing else in the view should need to learn the name.
6. **Add a check to `scripts/ui_smoke.py`** asserting the canvas narrows and
   that leaving restores. Neither typecheck nor the offline suite can see a
   scope that renders nothing.

If your new scope needs a server-side query, derive it from
`graph/queries.py::subgraph` rather than from `neighborhood`: an induced query
must bind each relationship exactly once, which the directed pattern gives for
free and the undirected one does not.

## Add a UI component

1. **Check `components/ui/` first.** A second implementation of an existing
   primitive is a review failure. If a primitive nearly fits, add a prop.
2. Shared across brief / graph / runs? It goes in `components/shared/`.
3. Semantic tokens only — no raw colours, no tier-1 primitives. For entity
   colours use `var(--entity-${id.split(':')[0]})`.
4. Scoped SFC styles. ~25 layout utilities exist in `utilities.css`; anything
   that encodes a visual decision belongs in the component.
5. `cd web && npm run typecheck && cd .. && make lint-tokens`.

---

## Change the brief's look

Prose styling lives in `web/src/styles/prose.css`. Entity mention styling is
there too — note the deliberate choice of a dotted underline and **no background
fill**: fills turn a dense paragraph into a highlighter accident.

The reading measure is `--measure` / `max-width: 70ch`. The brief body never
compresses, even if panel chrome gains a density setting.

---

## Regenerate everything

```bash
make schema        # docs/schema.sql + docs/graph-schema.md
make types         # web/src/api/schema.d.ts (no server needed)
make lint          # ruff + tokens + determinism + ontology + generated drift
make reset         # wipe, migrate, re-seed
```

Generated files are committed and `make lint` fails on drift. Never hand-edit
them.

Both corrections are recent. `make types` built from the app object rather than
a running server some time ago and this still said otherwise; and "CI fails on
drift" was never true, because there is no CI — `lint-generated` is what makes
the sentence honest.
