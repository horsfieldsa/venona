# Contracts

Twelve interfaces. Each is consumed by three or more modules, so changing one
ripples. Every entry carries a **"changing this requires touching"** list — do
that list in the same commit, or the change is incomplete.

Adding a field is usually safe. Renaming, removing, or re-keying is not.

---

## C1 — `assertions` + `entity_mappings`

**Lives in** `backend/venona/db/migrations/0001_baseline.sql`

Postgres is the system of record; Neo4j is a projection. Two rules make that real:

- **Assertions are append-only.** Never `UPDATE entity_id_raw`. A merge is a row
  in `entity_mappings`, and the projector resolves through the active chain.
  Rewriting assertions destroys reversibility and makes unmerge lossy.
- **`evidence_quote` is `NOT NULL`.** An assertion nobody can trace to a
  sentence is not evidence.

`evidence_span` offsets are Python **code points into `documents.text_nfc`**,
and `text_nfc` is stored verbatim and never re-normalised — every span and every
acronym grounding offset points into it.

**Who decided is recorded, and it is now VISIBLE — on three of the four
columns.** `alias_conflicts.resolved_by`, `entity_mappings.merged_by`,
`acronym_definitions.decided_by` and `entity_definitions.decided_by` are
`users.id`, nullable, added by migration 0002. NULL is honest and common: it
means the CLI decided, or the pipeline did, or the row predates anyone being
asked to sign. Never backfilled — putting a name on a verdict nobody signed would
invent provenance, which invariant 3 forbids everywhere else.

`entity_definitions.decided_by` is the fourth and is deliberately still unwritten:
no endpoint settles an entity definition by a HUMAN verdict. `POST
/entities/{id}/define` grounds one from the web through the pipeline, and stamping
an actor on that would attribute a model's finding to whoever pressed the button.
It stays until there is a verdict to record. (This paragraph claimed all four were
visible for two phases; two of them were not. `entities.status_changed_by`, which
the same claim named elsewhere, has never existed as a column.)

`runs.started_by` is the same column in a different table and is now written and
read too — `run_launcher` had passed it on every launch since identity existed and
`run_service.create_run` accepted the parameter and dropped it before the INSERT,
so the column was NULL on every row ever written. A dead write and a dead read hide
each other perfectly.

The actor reaches a service as a plain `actor: str | None`, resolved by
`api/authz.py::actor_id` in the router and nowhere else. Identity does not enter
`services/` — a service that can reach a `Request` is a service two callers cannot
share, the same reason C7 keeps `RunContext` identity-free. On the way out it is
LEFT JOINed to an email in the repository rather than looked up per row, because a
`users` lookup per conflict is `glossary.py`'s 201-queries-for-200-rows again.

> Changing this requires touching: `db/core.py`, `db/migrations/`, `pipeline/resolve.py`,
> `graph/projector.py`, `services/seed.py`, `services/entity_service.py`,
> `services/brief_service.py` (`brief_entities` resolves the chain so the
> brief-scoped graph does not silently drop whatever a merge touched), every
> fixture under `fixtures/seed/`, `docs/schema.sql`, `docs/DATA-MODEL.md`.
>
> Changing WHO-DECIDED requires touching: `api/authz.py` (`actor_id`,
> `actor_email`), `api/routers/curation.py`, `services/curate.py`,
> `db/repositories/curate.py` and `db/repositories/curation.py` (the JOIN in
> `CONFLICT_SELECT`), `api/schemas/curation.py`, `web/src/api/schema.d.ts` via
> `make types`, `web/src/components/curation/MergeHistory.vue`, and
> `tests/test_curation_api.py::TestTheVerdictRecordsWhoMadeIt` — which pins both
> directions, signed and unsigned, because a verdict that FAILS on attribution is
> worse than one that records NULL.
>
> The glossary half of the same path, added when `acronym_definitions.decided_by`
> stopped being dead: `api/routers/glossary.py` (seven verdict endpoints),
> `db/repositories/glossary.py` (the JOIN in `ENTRY_SELECT`, and every read that
> feeds `_row_to_entry` going through it), `api/schemas/glossary.py`,
> `web/src/views/LookupView.vue`, and
> `tests/test_ambiguity_verdicts.py::TestTheVerdictRecordsWhoMadeIt`. The runs
> half: `services/run_service.py`, `db/repositories/run.py`,
> `api/schemas/run.py`, `web/src/views/RunDetailView.vue`, `tests/test_runs.py`.

---

## C2 — `LlmExecutor.call()` → `LlmResult`

**Lives in** `backend/venona/llm/executor.py`, with the per-service half in
`backend/venona/llm/harness/`

The only place a model service's binary is invoked. Budget, the usage ledger and
offline replay all hang off this seam.

**One seam, several services.** `executor.py` owns everything true of any
agentic CLI -- the semaphore, the circuit breaker, the outage wait, the pumps,
reaping, the health cache, fixture recording. A driver under `llm/harness/`
owns argv, the envelope, the error vocabulary, the probe, the transcript shape
and what it must subtract from a schema. `_table()` is the table; adding a
service is a module, a row, an install line and a fixture directory.

**A driver may only SUBTRACT from a schema** -- a keyword its provider rejects
-- never add, rename or reshape. The cache key is taken from the schema the
CALLER wrote, so what a driver cannot express never rekeys a fixture.

**`binary` is a NAME, never a path from the wire.** That is what lets
`llm.harness` be editable over HTTP while `llm.cli_path` stays frozen: nothing
an administrator can type reaches argv. `tests/test_harness_table.py` holds it.

Returns a frozen dataclass, not a tuple — a tuple cannot carry `refused`,
`error`, or the per-model cost split, and widening it later touches every call
site.

**Success** = `is_error == false` **and** `api_error_status == null` **and**
`permission_denials == []` **and** `structured_output` is a dict.

Verified behaviours encoded here (measured against Claude Code, not inferred — see `docs/adr/`). Every row is a fact about ONE service, which is why they live in its driver rather than in the executor:

| Behaviour | Consequence |
|---|---|
| `--permission-mode dontAsk` denies `WebSearch`/`WebFetch` while exiting 0 | tool-using stages must pass `--allowedTools`; non-empty `permission_denials` is a hard failure |
| `stop_reason` is `"tool_use"` on success with `--json-schema` | never gate on `end_turn` |
| `modelUsage` carries a phantom haiku entry every call | cost is the sum over the map |
| `usage.server_tool_use` is present and always **0**; the real counts are per-model in `modelUsage` (`webSearchRequests`, `webFetchRequests`) | server-tool counts are summed over the map too — same trap as cost, missed once |
| `--bare` forces `ANTHROPIC_API_KEY` | never use it; it bypasses the subscription |
| `--exclude-dynamic-system-prompt-sections` is a no-op with a replaced prompt | dropped |
| `--system-prompt-file` works but is absent from `--help` | probe once, fall back to `--system-prompt` |
| a single argv element is capped at `MAX_ARG_STRLEN` (131072 on Linux, measured) | **`user_content` goes on stdin, never in argv** — 192k verified through `claude -p` with the full flag set |
| an exec-time `OSError` happens before the process exists | caught in `_run` and re-raised as `LlmError`, so `is_retryable` and the budget can see it |

`cache_key` = `sha256` of canonical JSON over
`{task, system_prompt_sha, user_content, schema, model, effort, sorted(tools)}`,
first 32 chars. **Frozen before the first fixture was recorded** — changing the
recipe rekeys every file under `fixtures/llm/`.

The **transport** (argv vs stdin vs prompt-file) is deliberately not part of
the cache key — `cache_key_for` is a function of the call arguments alone. So
moving content between argv and stdin leaves every recorded fixture valid, and
this contract's touch list does not fire for a transport change. Anything that
alters the recipe above does.

> Changing this requires touching: every `ctx.llm.call()` site, `llm/types.py`,
> `llm/harness/` (the protocol AND every driver), `llm/budget.py`,
> `llm/usage.py`, every fixture under `fixtures/llm/`,
> `tests/test_llm_envelope.py`, `tests/test_harness_table.py`.

---

## C3 — Brief wire format

**Lives in** `backend/venona/api/schemas/brief.py` → generated `web/src/api/schema.d.ts`

Blocks are stored with **offsets**; the API serves pre-split **runs**. Python
offsets are code points and JavaScript's are UTF-16 units — splitting
server-side makes that mismatch structurally impossible and removes the markdown
parser and `v-html` from the client.

Invariants, asserted by contract tests:

1. `"".join(r.t for r in runs)` equals the block's plain text, exactly.
2. Runs never nest; at most one reference per run.
3. `k ∈ {entity, glossary, undefined}` — **all three exist from day one.**
   Adding a kind later touches the component, the card, the store and the tokens.
   `u` is a MODIFIER on `k`, not a fourth kind: an entity named by one publisher
   and not yet corroborated. Deliberately not a kind, so the `k` partition
   `brief_service.brief_entities` reads for `POST /graph/brief` is untouched and
   the graph-scope question below does not arise. Emitted only when true —
   `"u": false` on every run of a 500-mention brief spends the payload budget
   invariant 5 exists to protect on nothing, and a span written before the field
   existed reads as false without a migration.
4. Marks are flat booleans (`b`, `i`, `code`, `s`) plus `href`.
5. Field names are short (`t`/`r`/`k`/`c`): a 500-mention brief is the payload
   case that runs-over-offsets accepted a ~1.4× cost for.
6. `snake_case` on the wire. No camelCase mapping layer.

### Items (since the brief format change)

A brief is a list of ITEMS grouped into standing sections, not a narrative. Each
item separates the two things the old `markdown` blob interleaved:

```jsonc
{"id": "brief:2026-08-06:<uuid7>:i03", "headline": "...", "state": "new|follow_up|ongoing",
 "state_note": "what is new, <10 words",
 "publisher": "...", "url": "...", "published_at": "...Z", "age_hours": 21,
 "summary": [Block], "why": [Block]}
```

- `summary` is what the article reports. `why` is the judgement. Separate
  FIELDS, because with one prose blob an opinion has nowhere else to live and
  "summarise more, opine less" is unwinnable by prompting.
- **Attribution is joined from `documents` via `cluster_id`.** The model never
  restates a publisher, URL or date, so a claim cannot be hung on the wrong
  outlet.
- `age_hours` is computed at GENERATION time and stored. Recomputing on read
  would relabel last Tuesday's "3H" item as "5D" and rewrite what the reader
  was told.
- Both halves are annotated by the same machinery, so entity hovers and acronym
  definitions work in the judgement exactly as in the facts.
- `BriefSection.blocks` is retained and empty for item-format briefs. It renders
  the pre-item archive; removing it would empty every historical brief.

### Brief ids are opaque, and unique per GENERATION

`briefs.id` is `brief:{date}:{uuid7}`, not `brief:{date}`. The date was once the
whole key, which made a second run in a day an UPSERT over the first rather than
a second brief. Nothing may parse a date back out of an id — read the `date`
COLUMN. Item and block ids are `{brief_id}:i03` / `{brief_id}:{block_id}`, so
they inherit the shape; `brief_service._row_to_block` takes `id.split(":")[-1]`,
which is safe only because a uuid7 contains hyphens and no colons.

Rows written before the id carried a uuid7 keep their `brief:YYYY-MM-DD` form.
Both are valid and coexist; the id is a string, never a structure.

### A section heading is denormalised, so renaming one is not a migration

`brief_items.section` stores the heading as free text at generation time, and
`brief_service` builds its section index from that string without consulting
`brief_sections.yaml`. Renaming a section therefore leaves every stored brief
rendering under its old heading, forever — the archive ends up carrying both,
and there is no repair path. That is the right trade (a brief is a record of
what was said on a day) but it is a one-way door, and the editor says so.

**`TextRun` has two producers, not one.** A brief is the first; `POST /api/annotate`
(`api/routers/annotate.py`, `services/annotate_service.py`) is the second, and it
puts the same runs over text venona did not write, so a browser extension can
splice them into a third-party page. That is why the extension reuses
`BriefBlock.vue`'s class derivation and the app's mention CSS without a
translation layer, and why a change to this format now moves two surfaces.

Two rules follow from the second producer, both load-bearing:

* **The annotate path must not normalise.** `markdown_to_blocks` applies NFC
  because it owns what gets stored. `annotate_service` owns nothing — its caller
  splices the runs back into the DOM nodes the text came from, so
  `"".join(runs) == text` has to hold for the exact string submitted. NFC
  changes a string's LENGTH; applying it there would land every splice after a
  composed character on the wrong word.
* **`strict_case` exists for it.** `tier_a` on a brief is that brief's retrieval
  set. On a page nobody retrieved, `tier_a` is the whole corpus, and strictness-1
  aliases live in the folded automaton — so 525 of 1003 aliases become looser
  than the tier reserved for all-caps acronyms. Measured: *"a lattice of horizon
  effects on the foundry floor"* produced three confident underlines at 0.9.
  See `AliasIndex.find`.

> Changing this requires touching: `services/annotate.py`,
> `services/annotate_service.py`, `services/brief_service.py`,
> `services/personal_brief.py`, `pipeline/brief.py`, `llm/schemas.py`,
> `profile/brief_sections.yaml`, `services/config_service.py`,
> `api/schemas/annotate.py`, `api/routers/briefs.py` (the for-you routes),
> `web/src/types/brief.ts`,
> `components/brief/BriefBlock.vue`, `BriefBody.vue`, `BriefItem.vue`,
> `views/BriefView.vue`, `api/routers/graph.py`,
> `extension/src/content/splice.ts`, `extension/src/shared/types.ts`,
> and `make types` (`web/src/api/schema.d.ts` is generated and gated by
> `make lint-generated`).
>
> The PERSONAL brief carries the same item shape through the same wire model,
> the same desks from `profile/brief_sections.yaml`, and the same Sources
> footer. Two deliberate differences: it carries no `unresolved_mentions` (a
> per-reader document must not feed the shared curation queue) and its ids are
> `pbrief:{date}:{uuid7}` with the same `:iNN` / block-suffix scheme. Its
> arrangement is the desks in the profile's declared order with the reader's
> ranking inside each -- see ADR 0176. Both
> documents assemble through `brief_service.assemble_body`, so a change to
> the item shape lands on both at once; the personal storage twin is
> `personal_brief_items` / `personal_brief_blocks`.
>
> A span's `kind` partition has a second reader:
> `brief_service.brief_entities` feeds `POST /graph/brief`, the subgraph a brief
> induces. A fourth `kind` therefore has to decide whether it belongs in the
> graph's scope. `tests/test_brief.py` pins that function's output against
> `get_brief`'s so the two cannot answer the same question differently.
>
> This list once dropped `make types` and `fixtures/golden/brief_annotated.json`
> as naming files that did not exist. That was true of both when it was written
> and is now true of only one: `web/src/api/schema.d.ts` IS generated, is
> committed, and `make lint-generated` fails on drift, so it is back above.
> `fixtures/golden/` is still empty — restore that one alongside the files.

---

## C4 — Entity id scheme

**Lives in** `backend/venona/db/models.py` (pattern derived from the ontology)

```
^(<prefix>|...|gloss):[a-z0-9][a-z0-9-]{0,63}$
```

The alternation is the live ontology's prefixes plus `gloss`, compiled per
call by `id_pattern()` — never a frozen list, because the type set is
per-domain (docs/adr/0139). The defense instance's prefixes are `company
country govt person plat prog research tech unit`.

Slug: NFKD → strip combining marks → ASCII fold → lowercase → `[^a-z0-9]+`→`-`
→ collapse → truncate 64 on a hyphen boundary.

**Minted once at first sight, never recomputed.** A rename adds an alias; the id
does not change. These strings are in URLs, `sessionStorage` keys, every fixture
and every test assertion.

Prefixes come from `config/ontology.yaml` (C10). `gloss:` is not an ontology
type — glossary entries are definitions, not graph nodes — but shares the
namespace.

`org:` was retired in ontology v3, which split `Organization` into `company:`,
`govt:`, `unit:` and `research:`. Nothing needed renaming, because the split
landed in the same change that deleted every row — the only cheap moment to do
it. On a live corpus a retype is a MERGE into a new id and never a rename,
exactly as the v2 Country migration did it; `profile/anchors.yaml` records where
that machinery went and why it was not needed here.

> Changing this requires touching: `config/ontology.yaml`, `db/models.py`,
> `styles/tokens.css` (the `--entity-slot-*` palette and the defense alias
> family), `web/src/lib/entityTokens.ts`, `scripts/lint_tokens.py`,
> every fixture, every stored brief (spans reference ids).

---

## C5 — Semantic token names

**Lives in** `web/src/styles/tokens.css` (values) and `web/src/styles/theme.css` (which of them Tailwind may generate a utility for)

Two tiers. Tier 1 primitives are referenced only by tier 2; components may
reference **only** tier 2.

**Two palettes, one block.** Every tier-2 colour is `light-dark(light, dark)`,
switched by `color-scheme` — `light dark` on the bare `:root` (System, the
default) and overridden by `:root[data-theme='light'|'dark']`. Those two rules
declare `color-scheme` and nothing else: a colour in either would be a second
palette, and two copies drift. **Light is the first argument**, inverted against
the rest of the codebase, because that is the order `light-dark()` takes.

A consequence with teeth: a custom property's *computed value* is its declared
token stream, so `getComputedStyle(root).getPropertyValue('--surface-base')`
returns the literal text `light-dark(...)`. Anything that needs a resolved
colour — a canvas, a badge — must go through a probe element or a generated
per-theme map. `lint_tokens.py` refuses a literal `getPropertyValue` on a colour
token.

Entity colour tokens are named after the C4 id prefix, so every consumer
resolves a colour with `var(--entity-${id.split(':')[0]})` and there is no
lookup table anywhere.

`make lint-tokens` fails on: a raw colour outside `tokens.css`; an arbitrary dimension (`p-[7px]`, `h-[460px]`) or arbitrary colour in a template; a `<style>` block outside the two-file allowlist; a tier-1
reference in a component; a tier-2 name not declared; **and a dynamic family
member missing** — the family's members are derived from the ontology, so adding
an entity type without its colour token is a build failure rather than a
transparent element.

> Changing this requires touching: `styles/tokens.css`, `styles/theme.css`,
> `scripts/lint_tokens.py`, the Cytoscape stylesheet (S6),
> `components/entity/EntityMiniGraph.vue`, `web/src/lib/entityTokens.ts`,
> `api/routers/meta.py` (slot assignments on the wire), `docs/DESIGN.md`,
> `tests/test_contracts.py` (the contrast sweeps parse this file and resolve
> both sides), `extension/scripts/emit-styles.mjs` (it generates the extension's
> stylesheets and its per-theme token maps from it),
> `extension/scripts/finish.mjs` (its needles name tokens by value),
> `web/public/theme-boot.js` and `hub/web/public/theme-boot.js` (the switch,
> two render-blocking copies), `web/src/stores/theme.ts` (the one that WRITES
> the shared choice), `hub/hub/pages.py` and `api/routers/oauth.py` (the two that
> answer it on the server, each carrying its own copy of the two `[data-theme]`
> rules), and `hub/web/src/styles/app.css` (the hub's operator app, which
> imports this file rather than copying it -- so a token RENAMED here reaches it
> silently and a token REMOVED here fails its build).

Four of those were missing from this list, and every one of them fails *quietly*
rather than loudly: the contrast sweeps go vacuous, the extension's stylesheets
flatten to one palette, a needle stops matching for the wrong reason, and the
boot script pins the document.

The last four are the same kind of quiet. The theme choice is one cookie, optionally
scoped to a shared domain, and read by five files that cannot import from each other
(docs/adr/0177), so a rename or a changed accepted value leaves each of them
answering the old question and every surface disagreeing about which palette the
reader picked. `tests/test_theme_boot.py` is the only thing holding them equal.

A token is not reachable from a template until `theme.css` names it — either in
`@theme inline` (so Tailwind generates `bg-`/`text-`/`p-` for it) or via the
`(--var)` shorthand at the call site. Adding a value to `tokens.css` alone
leaves it invisible.

---

## C6 — `RunEvent` + SSE framing

**Lives in** `backend/venona/api/schemas/run.py`, `services/run_service.py` (S7)

```jsonc
{"seq": 41, "run_id": "run:…", "ts": "…Z",
 "type": "stage_started|stage_progress|stage_finished|counter|log|warning|error|model_message|done",
 "stage": "fetch|analyze|acronyms|brief|define|curate",
 "data": {}}
```

- Frame: `id: {seq}\nevent: {type}\ndata: {json}\n\n`; `:ping` every 15s.
- `X-Accel-Buffering: no` — the Vite dev proxy buffers the stream without it.
- `run_events(run_id, seq)` composite PK; `seq` allocated `MAX(seq)+1` **inside
  the insert transaction**, and PER RUN, dense, from 1 -- which is why it is not a
  Postgres sequence: a sequence is global and leaves gaps.
- **The allocation is serialised by `pg_advisory_xact_lock(hashtext(run_id))`.**
  Read-then-write at READ COMMITTED lets two writers see the same `MAX(seq)` and
  the second violate the primary key. This replaced SQLite's `BEGIN IMMEDIATE`,
  which bought the same thing by locking the whole database; the advisory lock
  scopes it to one run, so concurrent runs no longer wait on each other.
  Deliberately NOT a retry loop on `23505`: a retry treats a collision as normal
  and would equally mask a real duplicate-seq bug, in the log the SSE stream is a
  view over. Pinned by `tests/test_db_concurrency.py`.
- **Persist before publish, always.** Publishing first loses events on replay,
  and the bug only appears when someone reconnects at the wrong moment.
- Terminal event is `done` with `status ∈ {succeeded, succeeded_partial, failed,
  cancelled}` **and `outcome ∈ {delivered, some, nothing_found, nothing_to_do} |
  null`**. The client must `.close()` or `EventSource` reconnects forever.
- **The two are different questions and neither substitutes for the other.**
  `status` is whether the machinery worked; `outcome` is whether there was an
  answer. A run that found nothing is `succeeded` + `nothing_found`, never
  `succeeded_partial`; a run that degraded and still wrote its brief is
  `succeeded_partial` + `delivered`. `outcome` is null exactly when there is no
  answer to report, which is every `failed` and every `cancelled`. ADR 0183.
- `data` is additive-only. New keys yes, renamed keys no.
- **`stage` is per RUN TYPE, not per pipeline.** The names above are the
  pipeline's. A lookup emits `lookup`, which `orchestrator.STAGES` has never
  contained and `venona run --only` must never accept. C11 has the table; the
  store must key its stage strip off the run's type rather than a literal, or a
  one-stage run renders the pipeline's six segments dark beside its own.


**Frozen counter names** (`pipeline/types.py::RunCounters`):
`documents_fetched, documents_new, clusters, acronyms_grounded,
acronyms_undefined, entities_new, entities_seen, entities_defined,
edges_written, entities_promoted, curation_open, llm_calls, cost_usd`.

> Adding a stage or a counter requires touching: `services/run_scope.py` (the
> one place a stage is opened and closed), `pipeline/orchestrator.py`
> (`STAGES`), `pipeline/types.py` (`RunCounters`), `web/src/stores/runs.ts`
> (its `STAGES` literal — the store SILENTLY DROPS events for names it does not
> know), `web/src/components/runs/LiveRunPanel.vue` (the spine's column count and
> `COUNTER_LABELS`), `services/run_types.py` (a `summary` tuple, if the counter
> should render), `tests/test_cli_run.py`, which pins the stage tuple and asserts
> backend/frontend parity, and `tests/test_frontend_drift.py`, which pins the
> counter labels against `RunCounters` in both directions.
>
> *This list named `web/src/views/RunsView.vue` for months after `COUNTER_LABELS`
> moved to `LiveRunPanel.vue`, which is the ordinary way a touch list rots — the
> file it names still exists and still renders runs, so the reference looks fine.
> `tests/test_frontend_drift.py` is what makes this half of the list enforceable
> rather than aspirational.*

### `model_message` — the live model session

The CLI streams its session, so each message becomes an event and a run can be
watched rather than guessed at. A run spends nearly all its wall clock inside
subprocesses; without this the stage strip cannot distinguish "analyze, working"
from "analyze, wedged".

The `kind` vocabulary is FROZEN and shared:
`session_started | text | thinking | tool_use | tool_result | rate_limit`.

**The translation into it is per service**, because each names its own events.
`Harness.summarize` owns it and `Harness.is_transcript_event` decides which
lines are transcript at all -- the line carrying the call's own outcome is the
return value, not an entry, and which line that is differs by vendor.

Asking the wrong service is SILENT: every event maps to None, nothing is
emitted, and the run page shows a run that plainly did work with an empty
transcript. There is no error to notice, which is why
`tests/test_transcript_per_service.py` asserts the wiring rather than trusting
it.

A service need not fill every field. One reports the model it chose and the
tools it granted on `session_started`; another reports only a thread id, and one
has no `tool_result` at all. The render site shows what it was given rather than
drawing a zero for what it was not.

> Changing this requires touching: `llm/transcript.py`, EVERY driver's
> `summarize` and `is_transcript_event`, `llm/runner.py::transcript_emitter`,
> `web/src/components/runs/ModelTranscript.vue`,
> `tests/test_transcript_per_service.py`, `tests/test_transcript.py`.

Three rules, all load-bearing:

- **Message-level, never token-level.** `--include-partial-messages` is not
  passed. Every event is persisted before it is published, and token deltas
  would multiply `run_events` by ~100x per call for a transcript nobody reads
  back.
- **`summarize` returns None for most events.** Dropping is the common case.
- **Tool results carry size and a preview, never the body.** A fetched page is
  already in `documents`; copying it into the event log doubles the database.

Additive by construction: a client that does not subscribe to `model_message`
behaves exactly as before, which is why the stage strip and the frozen counter
list are untouched.

> Changing this requires touching: `services/run_service.py`,
> `pipeline/orchestrator.py`, `pipeline/types.py`, `llm/transcript.py`,
> `web/src/api/sse.ts`, `web/src/types/run.ts`, `stores/runs.ts`,
> `components/runs/ModelTranscript.vue`.
>
> Adding an OUTCOME value requires touching: `services/run_service.py`
> (`OUTCOMES`), `api/schemas/run.py` (the `RunOutcome` Literal, which is what
> reaches the generated client), `services/run_types.py` (a phrase per kind
> that can reach it), `web/src/lib/runOutcome.ts` (`OUTCOME_TONE`),
> `db/migrations/0018_run_outcome.sql` if old rows can be derived into it, and
> `tests/test_run_outcomes.py`, which pins the vocabulary in both directions.
>
> (This list previously named `components/runs/RunProgress.vue`, which has never
> existed — the stage strip lives in `views/RunsView.vue`. A touch list naming a
> phantom file is worse than a short one: it reads as covered.)

---

## C7 — `RunContext` + stage signature

**Lives in** `backend/venona/pipeline/types.py`

```python
Stage = Callable[[RunContext], Awaitable[StageResult]]
```

Two rules:

1. **Every nondeterminism source arrives through the context** — `clock`, `ids`,
   `rng`, `http`, `llm`. `VENONA_OFFLINE=1` swaps them all at once.
   `make lint-determinism` fails the build if `pipeline/`, `services/`, `llm/`
   or `graph/` calls `datetime.now()`, `uuid4()`, `random.*`, or constructs an
   `httpx` client directly.

   **`llm` is where the model service hides**, and a stage must never look
   behind it. Which service is active, what it is called, what it can do and
   how its errors are spelled are all C2's business; a stage that branched on
   `ctx.llm.harness.key` would be a second place the answer lives. What a
   stage may do is ask for a CAPABILITY (`web_search`, `web_fetch`) and be
   told, because "this service cannot fetch a named page" changes what the
   stage should attempt. `llm/runner.py::narrow_to_capabilities` is that seam,
   and it notes what it dropped rather than running quietly narrower.
2. **Stages read inputs from the database by `run_id`.** The return value is metadata
   only. This is what makes `venona run --only brief` work, and it
   turns an 8-minute alias-tuning loop into a 5-second one.
3. **`ctx.stage` is attribution, and a stage must never branch on it.**
   `services/run_scope.py` sets it on the way into a stage and clears it on the
   way out, and the three recorders read it so none can be handed a name that
   disagrees with the others. Rule 2 holds only while no stage behaves
   differently for knowing what it is called; `ctx.on_wait`, `ctx.wait_abort`
   and `ctx.budget` are the run's own for the same reason, because the executor
   they would otherwise hang off is shared by a whole lane.

`run_brief`'s `rewrite_of` is the one parameter that names an artefact, and it
does not weaken rule 2: the stage still reads clusters by state, and the id only
widens which state is eligible by releasing that one brief's claim in
`brief_clusters`. It exists because keying on state alone made `--only brief`
unable to re-brief at all — "unbriefed clusters in the window" is empty the
moment a brief has claimed them, so the stage `--only` was built for was the one
it could not run twice. See `build_retrieval`.

> Changing this requires touching: every module under `pipeline/`,
> `services/run_scope.py`, `services/run_service.py`, `services/seed.py`,
> `tests/conftest.py`.

---

## C8 — Card payload

**Lives in** `backend/venona/api/schemas/entity.py`

One shape discriminated by `kind`, feeding the hover card, the brief rail and
the graph node panel. One store, one cache, one TTL, one component.

`POST /entities/cards:batch` returns a **map**, not a list. The reader calls it
once on mount with 20–60 ids; every subsequent hover is a client-side Map
lookup. This is the highest-leverage performance decision in the reader.

`mini_graph` is precomputed server-side with neighbours already sorted by
weight, so the card renders inline SVG at deterministic angles — no force
simulation, no graph library on the reading surface, ~1ms, stable across
renders.

**Every kind carries a definition, or says it has none.** `definition`,
`method`, `confidence` and `grounding` were glossary-only; entity cards fill
them too, from `entity_definitions` (see `pipeline/define.py`). That is what
lets one component draw all three kinds — the whole point of a single payload
discriminated by `kind`, which had drifted.

Glossary cards carry `grounding` (quote + article). A card with
`kind: "glossary"` and no grounding is a bug — that combination violates
invariant 3.

`method` distinguishes a QUOTATION from a SYNTHESIS and the card must not blur
them. `in_article` and `web_grounded` carry `grounding.quote`, a verbatim
sentence, and render as a blockquote. `in_corpus` is written from several
evidence sentences, carries `definition_sources` and no quote, and renders as
"drawn from N articles". A synthesis shown as a quotation is exactly the
failure invariant 3 exists to prevent.

> Changing this requires touching: `services/entity_service.py`,
> `web/src/types/brief.ts`, `stores/entities.ts`,
> `components/entity/EntityHoverCard.vue`, `EntityMiniGraph.vue`.

---

## C9 — `sources.yaml` schema + generated artifacts

**Lives in** `config/sources.yaml`, validated at startup

```yaml
{id, name, kind: rss|discovery, url?, publisher, enabled, tags?, parser?,
 max_items?, fetch_body?}
```

An unknown `kind` is a **startup** failure, not a runtime one. Adding a source
must be a data change with zero Python — if you find yourself writing Python,
the adapter protocol is wrong; fix that instead.

`url` is required for `rss` and omitted for `discovery`, which has no endpoint —
it searches. Enforced by a model validator, so a `kind: rss` block missing its
url fails at startup rather than issuing a request to `None` mid-run.

There was a third kind, `scraper`, with a `selectors` block. It is **gone**:
every site that motivated it is a JS-rendered SPA serving no content links, so
CSS selectors cannot read any of them (`config/sources.yaml`, `removed:`).
Advertising a kind the code cannot serve is worse than not offering it.

`SourceSpec.model` takes a **tier word or a native model name**, and both stay
legal under either model service. Resolution is pass-through
(`Harness.aliases`, C2), so `model: opus` in a discovery block keeps working
after a switch to a service that has never heard of opus — it resolves through
that service's own table — and a native name nobody has an alias for is passed
to the provider unchanged. A code-side allowlist here would have to track two
vendors' release calendars and would fail in the worst direction when it
lagged, refusing the model an operator is trying to adopt on the day they try.

**Changing `SourceKind` requires touching:** `sources/base.py`,
`sources/registry.py`, `pipeline/fetch.py::_collect_one`,
`cli.py::_verify_sources`, `docs/CONTRACTS.md`, `docs/EXTENDING.md`, and
`db/migrations/0001_baseline.sql`'s column comment.

**Generated, committed, `make lint` fails on drift, never hand-edited:**
`web/src/api/schema.d.ts`, `docs/schema.sql`, `docs/graph-schema.md`. API
reference is FastAPI's `/docs`.

This said "CI fails on drift" for the life of the project, and there is no CI —
so for the life of the project nothing checked. `lint-ontology` covers
`graph-schema.md` and `lint-generated` covers the other two; both regenerate
into a scratch directory and diff, so a stale file fails rather than being
silently rewritten. Regenerate deliberately with `make schema` / `make types`.

**Editable over HTTP.** `PUT /api/config/sources` validates through
`registry.parse_sources` — the same function `load_sources` calls — so a save
fails with the error startup would have raised. Adding a source in Settings
still leaves steps 2-4 of the EXTENDING checklist undone: nothing records a
fixture for it, so the offline suite does not cover it.

**Read by `GET /api/sources/sites`**, which the browser extension asks for the
hosts it may request permission to annotate. It unions the enabled `kind: rss`
hosts here with `documents.site_domain`, so a discovery sweep's finds — which
have no `url` in this file to derive a host from — are visible too, and derives
the feed host with `pipeline/normalize.py::canonical_url`, the same function
that produced `site_domain`, so the two halves cannot disagree about `www.`.

Two properties of that endpoint are load-bearing and easy to remove by accident:
it filters to documents whose `source_id` is a source declared **here** (a
toolbar ingest is `source_id = 'manual'`, and offering those hosts would put
every intranet and webmail domain someone ingested in front of a one-click
permission grant), and it validates each host through `sources/urlguard.py::
check_static` before minting a match pattern from it.

> Changing this requires touching: `sources/registry.py`, `sources/base.py`,
> `services/config_service.py`, `api/routers/config.py`,
> `fixtures/http/index.json`, `docs/EXTENDING.md`.

---

## C10 — Ontology (the research ↔ graph contract)

**Lives in** `config/ontology.yaml`, loaded by `backend/venona/ontology.py`

The single source of truth for what the pipeline may assert. Everything
downstream is **generated**, never hand-maintained:

- JSON Schema enums handed to the model (`--json-schema`)
- the extraction guide injected into the analyze system prompt
- the predicate whitelist enforced in `graph/writer.py`
- the projection filter in `graph/projector.py`
- `docs/graph-schema.md` and `GET /api/meta/ontology`
- the `--entity-<prefix>` token family checked by `lint-tokens`

**Why a typed contract rather than an enum.** An enum enforces membership, not
meaning. A live probe during design produced `Anduril —AWARDED_TO→ "$99M OTA"` —
a whitelisted predicate used backwards. Declaring `domain` and `range` lets
`check_relation()` reject that at write time instead of letting it rot in the
graph. The projector collects rejections and reports them; one badly-typed
assertion never aborts a rebuild, but it never silently enters either.

**Changing it is deliberately cheap**, because the graph is a projection:

```bash
$EDITOR config/ontology.yaml     # edit, bump `version`, add a changelog entry
uv run venona ontology check     # validates prefixes, domain/range, symmetry
make schema                      # regenerates docs/graph-schema.md
uv run venona graph rebuild      # re-project from the assertion log
```

No migration, no destructive Cypher. **Retiring a predicate simply stops it
projecting** — its assertions stay on disk and come back if it is re-added.
Adding one is additive: existing assertions are unaffected, and new extractions
can use it immediately.

Validation refuses to load an ontology with duplicate id prefixes, an empty or
unknown domain/range, a predicate name outside `^[A-Z][A-Z0-9_]*$`, or a
symmetric predicate whose domain and range differ.

The name pattern is not style. `graph/writer.py` builds
`MERGE (a)-[e:{predicate}]->(b)` by interpolation, because a relationship type
cannot be parameterised — and the previous guard, `str.isupper()`, is `True` for
`FOO]->() MATCH (n) DETACH DELETE n //`. That was reachable only by editing the
repo; the ontology is editable over HTTP now.

**Editable over HTTP, with one thing frozen.** `PUT /api/config/ontology`
refuses a prefix rename on a surviving type: a prefix is the id namespace
(C4). The entity-type SET is editable (docs/adr/0139) — each type declares a
unique `color_slot` into the `--entity-slot-*` palette and colours with no
build — but a set change demands a `version` bump and changelog entry, the
same discipline as a predicate-contract change. Predicates are otherwise
free, except that the five in
`ontology.ANCHOR_PREDICATES` may not be removed — `llm/schemas.py` compiles them
into the anchor response schema, which is what constrains what `venona anchors
propose` may suggest. A change to a predicate's domain, range or symmetry must
bump `version`.

**Read the predicate list per call, never at import.** A run triggered from the
UI executes in the API process, so an import-time snapshot drops a predicate the
model was just told it could use — silently, in `graph/projector.py`, before the
writer's whitelist is even consulted.

> Changing this requires touching: `config/ontology.yaml` (source) or Admin →
> Ontology, then run `venona ontology check` and `make schema`. Code changes are
> only needed for a NEW entity type: add its `--entity-<prefix>` token to
> `tokens.css`, and seed at least three examples in `fixtures/seed/entities.yaml`.
> Changing the save policy also touches `services/config_service.py` and
> `api/routers/config.py`.

**A cost this used to overstate, corrected 2026-08-18.** The C2 cache key hashes
the system prompt and the full schema, and both embed the ontology — its version
string via `extraction_guide()`, its names via `analyze_schema()`. This section
used to conclude that any ontology edit therefore forces a `make record`. It does
not: the system prompt also ends with `gazetteer(conn)`, which reads the entity
table, so the key moves on **every run** regardless — which is why the offline
suite does not replay `fixtures/llm/analyze/*` at all (see
`tests/test_orchestrator_smoke.py`'s docstring on the abandoned `test_seed_parity`
gate). An ontology edit costs no re-record; `make record` is a full paid,
corpus-mutating run and should only be spent to refresh fixtures for tooling that
actually reads them.

**Rewrites are part of the contract.** The `rewrites:` table (v4) maps ill-typed
shapes the model keeps producing to their one safe legal rendering; `analyze`
applies it before `check_relation` and stamps `method='extraction_rewritten'`.
The loader enforces that every `when` shape is ill-typed and every `then` result
is well-typed — which also makes the table acyclic — and rejects unknown
top-level keys, so a typo'd `rewrite:` cannot save as a silent no-op. A change to
the table forces a version bump for the same reason domain/range do: a stored
assertion must be attributable to the rules in force when it was made.


A predicate's DOMAIN and RANGE are now also read by the curation queue:
`pipeline/curate.py::_relation_for` translates the adjudicator's `part_of`
verdict into whichever predicate the ontology permits between those two entity
types, and `api/routers/curation.py::relate_options` generates the curator's
choices the same way. Narrowing a range therefore changes what the queue offers
as well as what `check_relation` accepts — which is the point, since the two
disagreeing is what produced `PART_OF object must be one of ['Organization',
'Program'], got Country` on a pair the queue had itself proposed.

---

## C11 — Run types and lanes

**Lives in** `backend/venona/services/run_types.py`,
`services/run_service.py` (`RunRegistry`), `services/run_launcher.py`,
`api/schemas/run.py`

Every run declares a `kind`. The kind decides two things nothing else may
decide for it: which **lane** it executes in, and how it **renders**.

```python
RunType(kind, label, lane, stages, summary, detail, trigger, outcomes,
        facts, lines, uses_model, subject)
LANES = {"research": 1, "light": 3}
OUTCOMES = ("delivered", "some", "nothing_found", "nothing_to_do")
```

| lane | slots | kinds | why |
|---|---|---|---|
| `research` | 1 | `daily`, `on_demand`, `cli`, `research`, `rewrite` | Two pipelines write the same `documents`/`clusters` with two independent `Budget` objects, and whichever finishes last decides what the corpus looks like. |
| `light` | 3 | `lookup`, `definitions`, `lookup_all`, `questions`, `personal_brief`, `export` | Definition rows, generated questions, one reader's own brief. None touches `documents`/`clusters`. `export` writes no corpus row at all -- the lane asks what a run WRITES, and a third lane for it would be a mechanism change costing the list below for a property this one already gives. |

**A run records which service ran it, and how it was paid for.** `runs.harness`
and `runs.billing_mode` are stamped when the executor is CHOSEN, not when the
row is written: `create_run_guarded` writes the row before admission and
`executor_for` runs after it, which for a queued light run is minutes later. A
run cancelled while queued records neither, correctly — a guessed value on a
historical row is the dishonesty `runs.outcome` exists to avoid. Both columns
are on `_LIST_COLUMNS` as well as the detail select, because the run LIST is
not `SELECT r.*` and `RunDetail` extends `RunSummary`, so a detail-only column
is not a thing that can exist.

`domain_setup` is RESEARCH, not light. It is long, budget-carrying, and its
output once applied redefines what every later run does, so two racing would
produce two answers to one question.

`lookup_all` is the exception that proves the lane rule: it sweeps the whole
undefined acronym queue and can hold a light slot for hours. It is still light,
because **the lane is about what a run WRITES, not how long it takes** — it
writes `acronym_definitions` and nothing else, so it cannot collide with a
pipeline the way a second `documents`/`clusters` writer would, and in the
research lane a glossary sweep and the nightly run would refuse each other for
no reason either could give. What it does cost is one of three light slots while
it runs, which is why the control in front of it counts the queue and quotes a
measured price before it starts.

Nine rules:

- **The kind declares what its page SAYS, not just how it is laid out.**
  `facts` is what a run of this kind produced (`counts.` from its own returned
  metadata, `params.` from its launch, `run.` from the row) and `lines` maps
  each reachable outcome to candidate sentences. Both resolve **server-side**
  into `RunDetail.facts` and `RunDetail.result_line`, for the reason
  `outcome_text` does: a second copy of the rules in TypeScript drifts inside a
  release. Three rules make it total: an absent key is omitted rather than
  zeroed, a counted slot resolving to zero does not fill, and the last candidate
  for an outcome carries no slots. `lines` keys on `(status, outcome)` because a
  succeeding run may have no outcome at all. **Never `counters.` in a fact** --
  the thirteen frozen counters are `summary`'s job, and naming one would render
  the same number twice under two labels. ADR 0187.

- **`status` says whether a run RAN; `outcome` says what it FOUND; a MESSAGE
  says which of the two it is evidence for.** Neither field may be spelled
  through the other. A lookup that grounded nothing is `succeeded` +
  `nothing_found`; only `ctx.warn` makes a run `succeeded_partial`. Its two
  siblings do not: `ctx.todo(text, to=...)` is work waiting on a person (the
  curation queue, which re-fires every run until somebody rules on it) and
  `ctx.note` is an observation with nothing to do about it. Every message
  carries its own `level` on the wire, because a run routinely has all three at
  once and a surface reading only the run's status can pick just one heading
  for the lot. `to` is SYMBOLIC (`curation`, `sources`, `config`), resolved by
  `web/src/lib/runMessages.ts`: a route string in a Python message goes stale
  silently the day the router is edited. `LaunchOutcome.status` defaults to None, meaning derive
  from `ctx.degraded`, so a new run type gets that by construction — a site
  overriding it is claiming something the context cannot know. `RunType.outcomes`
  maps each value to that kind's own words, resolved per row as `outcome_text`,
  because "not found" and "nothing new" are the same stored value seen by two
  kinds of reader. ADR 0183.
- **No caller asks a global "is anything running?"** Ask the lane. A single
  global slot meant grounding one acronym was refused for the tens of minutes a
  research run takes — which is exactly the window in which someone is reading a
  brief and hits a term they do not know.
- **The research lane refuses (409); the light lane queues.** A lookup someone
  asked for is still wanted 30 seconds later, so refusing it only makes them
  click again. A second forty-minute pipeline almost never is —
  `scheduler.run_daily` has taken that view since it was written.
- **`queued` is IN FLIGHT.** It blocks its lane, it is protected from hiding,
  and it is reaped on a crash. Any client testing `status !== 'running'` for
  terminality is wrong, and will report "found nothing" before anything has been
  looked up.
- **Every run starts through `run_launcher.launch`, and every run ends there
  too.** It is the only place a lane slot is claimed and released, and the only
  writer of a terminal `runs` row and a `done` event. A leaked slot strands
  every run queued behind it, forever and silently; a second terminal writer
  needs a second set of rules for when the first stands back, and each of those
  is a way for a run to end unsettled. A process with no web server reaches it
  through `run_launcher.host(settings, **state)`, which is what the CLI uses.
- **A stage is opened and closed by `services/run_scope.py`, never by hand.**
  `RunScope.stage()` is the sequence — the `runs.stage` pointer, a `running`
  row, `stage_started`, a cancel check, then the terminal row, `stage_finished`
  and a counter snapshot — and `open_stage`/`close_stage` are the same sequence
  for a body that cannot be a `with` block. It also sets `RunContext.stage`,
  which is the ONE source the three recorders read: the event log,
  `run_metrics` and the `llm_calls` ledger. A name handed to any of them
  separately is a name that can disagree.
- **`RunType.detail` agrees with `len(stages)`.** More than one stage is
  `pipeline` and exactly one is `single`; `LiveRunPanel` draws a spine for the
  first and a status line for the second, and a one-segment spine states
  something false. Pinned by `test_detail_says_nothing_the_stage_count_does_not`.
- **`RunType.stages` is NOT `orchestrator.STAGES`.** They answer different
  questions — "what can the pipeline be asked to do" versus "what will a run of
  this kind emit". Merging them would make `venona run --only lookup` a
  valid-looking no-op, which is the failure the 2026-08-05 "two stage lists is
  one too many" decision exists to prevent. `RESEARCH_STAGES` is a deliberate
  copy (importing would close a cycle) pinned by
  `test_the_research_stage_list_matches_the_orchestrator`.
- **An identical light request in flight is handed the run that already
  exists.** Two lookups for one acronym cost twice, write the same row, and let
  the loser's `mark_undefined` land on top of the winner's result. That was
  impossible under a single global slot — the second was refused — so it is a
  hazard the lane split introduces and has to answer. Keyed on `kind` **and**
  `params`; research runs are never coalesced, because turning "start a run"
  into a silent no-op is worse than a 409.
- **`type_for` never raises.** The `runs` table outlives any release's idea of
  what a run is: `seed.py` writes assertions under a run id with no `runs` row
  at all, and two kinds have been added without a migration. An unknown kind
  falls back to research/pipeline — the conservative answer to both questions,
  since an unknown run should not be handed a light slot nor assumed to have
  exactly one stage.

**Cross-process.** `RunRegistry` is an in-process `asyncio.Lock` and `venona
run` is a separate pid, so the lane question is asked of the database by
`run_service.lane_conflict`, filtered by lane. Never `registry.active_run_id` —
that is migration 0008's bug one layer up.

**Ownership is a LEASE: a session-level Postgres advisory lock keyed on the run
id.** `create_run` claims it with `pg_try_advisory_lock` on the connection the
run will use — `run_launcher`'s `run_conn`, or the CLI's `_db()` block, both of
which outlive the work — and Postgres releases it when that connection ends,
however it ends: a clean close, a SIGKILL, a container going away, a machine
going away. Nothing times out and nothing heartbeats, so there is no window in
which a dead run still looks alive, and no risk of calling a live one dead
because a heartbeat was late. `lease_is_held` / `leased_run_ids` read `pg_locks`
rather than trying to take the lock, because advisory locks are reentrant per
session and taking one to ask a question would both answer wrongly and acquire
something the caller must remember to release.

*This replaced `(owner_pid, owner_start_ticks)` and field 22 of
`/proc/<pid>/stat`.* That pair was not wrong — a pid alone is not an identity,
and migrations 0008 and 0020 record exactly why: in a container the API is pid 1,
or ~7 under an init shim, every `owner_pid` the previous container wrote lands on
the new one's own processes, the cmdline check passes because it really is
uvicorn, and the research lane wedges for good. The start time fixed that *on one
host*. It could not be made correct across two, because `/proc/<pid>` is a
statement about this kernel and a pid read from a row another machine wrote means
nothing. Migration 0004 drops `owner_start_ticks`; `owner_pid` stays as pure
diagnostics, because "which process is running this" is the first thing an
operator asks and a lock id cannot answer it.

**One rule inverted with it: a run with no lease is NOT live.** It used to read
the other way — unknown ownership counted as live, since a NULL `owner_pid` could
mean "a row predating 0008" as easily as "nothing is running", and wrongly
deciding a live run is dead is the expensive direction. The lease removes the
ambiguity rather than resolving it: "no lock" is positive evidence that no
session is executing the row. Keeping the old asymmetry would now mean a lane
wedged permanently by every crash. `tests/test_run_lanes.py::
test_an_unleased_running_row_does_not_block` and its cross-session twin pin both
directions.

**Still not enough for a second API replica**, and the deployment file still says
one. The lease is what makes a second *writer* identifiable across hosts; rate
limit buckets remain process-local and `RunRegistry` is still an in-process
`asyncio.Lock`, so two replicas would admit two runs to the same lane before
either wrote a row.

**A reaped run is `succeeded_partial` when any stage completed**, and `failed`
only when none did. Reaping used to follow a crash and this was nearly
theoretical; on a container every deploy SIGKILLs what is in flight, so a
pipeline killed at minute 35 — with documents, clusters and a brief already
written — would otherwise report `failed`. See the prohibition in CLAUDE.md.

**Model headroom.** `app.state.llm_light` is a second `ClaudeCliExecutor` with
its own semaphore (`llm.light_concurrency`). Same class, same ledger, same
replay, so C2 still holds and the binary is still invoked in one place. A
shared semaphore would let `analyze` take every slot, and a lookup admitted
instantly then parked behind twenty minutes of clustering is — from a hover card
— indistinguishable from one that was refused.

**Adding a KIND is not changing this contract.** Every consumer derives from
`RUN_TYPES`: the lane guards ask `kinds_in_lane`, `GET /api/runs/types` is generated,
row decoration is computed, and the frontend loads the table at mount. A new
kind is one entry plus a test —
`test_adding_a_kind_needs_no_change_outside_the_table` fails the day that stops
being true, which is the day the list below applies again. (`lookup_all` was
added this way; the only file outside the table it touched was
`components/runs/LiveRunPanel.vue`'s `COUNTER_LABELS`, and only because it reports counters no
other kind does.)

> Changing the MECHANISM — lanes, slots, the queue, coalescing, how a kind
> renders — requires touching: `services/run_types.py`,
> `services/run_service.py`, `services/run_launcher.py`, `api/schemas/run.py`,
> `api/routers/runs.py`, `api/routers/meta.py` (the `/health` runs block),
> `cli.py` (the research-lane pre-flight), `web/src/stores/runTypes.ts`,
> `web/src/types/run.ts`, `web/src/views/RunsView.vue`,
> `web/src/views/RunDetailView.vue`, and `tests/test_run_lanes.py`.
>
> Changing what a kind SAYS — `facts`, `lines`, `subject`, `uses_model` —
> requires touching: `services/run_types.py`, `services/run_facts.py`,
> `api/schemas/run.py` (`ResolvedFact`, `decorate`), `web/src/types/run.ts`,
> `web/src/components/runs/RunFacts.vue`,
> `web/src/components/runs/RunResultHeader.vue`, and `tests/test_run_facts.py`.
>
> Changing OWNERSHIP — the lease, who counts as live, reaping — requires
> touching: `db/repositories/run.py` (`claim_lease`, `lease_is_held`,
> `leased_run_ids`, `LEASE_NAMESPACE`), `services/run_service.py`
> (`create_run`, `_assert_not_live`, `lane_conflict`, `reap_interrupted`,
> `stream_events`), `db/migrations/0004_run_leases.sql`, `docs/schema.sql`,
> `docs/DATA-MODEL.md`, `docs/adr/`, `tests/test_runs.py`,
> `tests/test_run_lanes.py`, and `tests/test_cli_run.py` — the last three all
> construct live runs, and a test that fakes ownership by writing a column is
> exactly what the lease makes impossible.

---

## C12 — The authentication boundary

**Lives in** `backend/venona/api/middleware.py` (`EXEMPT_PATHS`,
`AuthMiddleware`), `api/security.py`, `main.py`'s middleware stack

Cited as C12 by `middleware.py` and `tests/test_auth.py` since auth shipped,
and written down here late — the two of them were pointing at a contract that
did not exist, which is the drift this file exists to stop.

**Default-deny, by construction.** `AuthMiddleware` guards everything under
`/api`, so a new router is protected the moment it is included. A
`Depends(require_auth)` was rejected precisely because a route added without it
looks identical to a route added with it. The only way to open a path is to name
it in `EXEMPT_PATHS`, which is asserted in `tests/test_auth.py` — so widening it
is always a deliberate diff.

```python
EXEMPT_PATHS = {"/api/health/live", "/api/auth/mode", "/api/auth/login",
                "/api/auth/callback", "/api/auth/token-login",
                "/api/request-access", "/api/oauth/register",
                "/api/oauth/token"}
```

`/api/request-access` has to be reachable by someone who has no session by
definition. It is rate limited hard rather than authenticated —
`ratelimit.py`'s `SIGNUP` bucket is 3/hour per client with a 40/hour global
backstop, because it is one of only two paths a stranger can reach at all.

A separate front site should not post here. Its form belongs on the HUB's
`POST /request-access`, because granting a request creates the account and the
membership and only the hub can do that — an instance can make a reader of
somebody the hub already knows, and nothing else, so a queue filled here was a
queue nobody could answer from. This route stays open and exempt: it is how an
instance takes a request from someone who reached the instance directly, and
the rows already in `access_requests` are still triaged at `/admin/people`.
See `docs/HUB.md`, "Two doors onto one queue".

**Three credentials, and a presented bearer IS the credential.**
`principal_for` tries `Authorization: Bearer` before the `venona_session`
cookie, because it is the explicit machine-driven path and an expired cookie
must not mask a valid token. A `vmcp_`-prefixed bearer is resolved against
`mcp_tokens`; anything else is compared against the root token. A `Principal`
records which was used (`via` ∈ `session|bearer|mcp|disabled`), so a log line
can tell a browser from a script from an agent.

**It does not fall through.** This was `verify_bearer(...) or
verify_session(...)`, and the `or` was wrong in two ways that only appear once a
second bearer class exists:

- A wrong, revoked or foreign `vmcp_` token presented **from a browser** fell
  through to the cookie and authenticated as the operator, unrestricted. The
  case was found through the connection test `/settings/agents` used to carry,
  which would have reported "connected" for every credential it was given; that
  control is gone (2026-08-27) and the property is not. Anything that presents
  an agent token from a browser meets it, and `tests/test_mcp_auth.py` holds it
  rather than the page.
- More quietly, any request carrying a dead agent token *and* a live cookie was
  silently upgraded from `reach=MCP_PATHS` to unrestricted.

So if a bearer is offered at all, it decides the request; the cookie is
consulted only when none was. The prefix test also keeps the database off the
hot path for a ROOT-TOKEN request, which never opens a connection in
middleware. A session request does: `verify_session` calls the injected
`lookup_user`, which is `AuthMiddleware._lookup_user`.

**`reach`, and `MCP_PATHS` as the mirror of `EXEMPT_PATHS`.**

```
EXEMPT_PATHS   opens a path to NO credential
MCP_PATHS      opens a credential to NO path but these
```

`Principal.reach` is a frozenset of admissible paths, or `None` for
unrestricted — and `None` is the DEFAULT, so session, root-bearer and disabled
principals are untouched and nothing that worked before changed. Both sets are
exact-match, live in `api/middleware.py`, are asserted by equality in
`tests/test_auth.py` and `tests/test_mcp_auth.py`, and are written here. Three
places each, so widening either is always a deliberate diff.

Exact match, never a prefix, for the reason `ratelimit.py` records in the other
direction: a prefix on `/api/mcp` would silently admit an `/api/mcp-admin`
added a year later. One trailing slash is normalised before the check, because
Starlette's Router issues its slash redirect *itself* — inside the app, after
this middleware — so `/api/mcp/` would otherwise 403 as `restricted_credential`,
which reads as a blocked credential rather than as a typed slash.

**This is what makes a route added next month safe by default.** Default-deny at
`GUARDED_PREFIX` protects it from an anonymous caller; `reach` protects it from
an agent. Neither requires the route's author to have remembered anything — the
property `Depends()` could never give, rejected here for the second time and for
the same reason.

**The check sits after the 401 and answers 403.** After, because `reach` lives
on the `Principal` and there is none until `principal_for` has run — and it
*should* sit after, because a caller holding an invalid credential must not
learn which paths a valid one reaches. 403 rather than 401 because 401 means
"present a credential": an agent that gets one on `/api/data/purge`
re-authenticates and retries until its context window is gone.
`WWW-Authenticate: Bearer error="insufficient_scope"` rides along (RFC 6750
§3.1). The 403 short-circuits inside `AuthMiddleware`, so it needs
`SecurityHeadersMiddleware` wrapping it for the same reason the 401 does, and
there is a test.

**One escalation path, closed only incidentally.**
`POST /api/auth/token-login` is in `EXEMPT_PATHS`, so the reach check never runs
on it — an agent that could exchange its token there would walk out with an
unrestricted session cookie. It is refused today *only* because
`routers/auth.py` calls `verify_bearer` directly rather than `principal_for`; a
natural-looking refactor to the latter would open it. `tests/test_mcp_auth.py`
names that test, where an MCP author will read it. Relatedly, `assert_usable`
refuses to start if `VENONA_AUTH_API_TOKEN` holds a `vmcp_` value, because
`scripts/ui_smoke.py` POSTs that variable to exactly this endpoint.

**Auth material is never in `Settings`, and over HTTP it moves one way only.**
`config/settings.yaml` is writable *and* readable over HTTP, so anything on the
`Settings` model is both attacker-writable and operator-visible. Session secret,
root API token, Google credentials and the CORS allowlist are all read from
`os.environ`. `config_service.FROZEN_TOP` closes the same door from the other
side, and `config_service.EDITABLE` is a fixed dict of file names gated by
`SAFE_KEY.match(key) and key in EDITABLE` — so no value of `key` reaches the database.

**The second user-supplied path component is `GET /api/config/{key}/history/{name}`**,
and it is allowlisted by MEMBERSHIP rather than by shape: `config_service.snapshot`
accepts only a filename `history()` has just listed for that key, then resolves it
and re-checks containment against the document's own `.history/` directory. A regex
would have to re-derive what `_write_doc` builds and can be wrong in the safe
direction as easily as the unsafe one — the first one written for it matched `\d+`
for a field that is a hex digest, and so matched no real snapshot at all. The route
is read-only; restoring a version is the client loading it into the editor and
pressing Save, which goes through `write_doc` like any other edit.

"Env-only" was the shorthand. The property it stands for is **not reachable
through an HTTP read primitive or an HTTP write primitive**, which the session
epoch satisfies as a row in `auth_state` (`0029_auth_state.sql`, ADR 0234)
without being an environment variable. **Agent tokens satisfy it too**:
`mcp_tokens` stores a SHA-256 of a 256-bit secret, so a full table read yields
no credential, and nothing under `/api` writes that table.

The rule as amended: **auth material that GRANTS is env-only; auth material that
only ever restricts, or that is the hash of a path-limited credential, may live
in the data directory. Over HTTP, access can be taken away and never handed
out.**

- **Minting is CLI-only** — `venona mcp token new`. There is no
  `POST /api/tokens` and there must not be: an HTTP mint would let a compromised
  credential manufacture a *persistent* one that outlives rotation of the
  credential that made it, which is an escalation in duration even though not in
  scope. `web/src/views/AccountView.vue` has argued the same about a Rotate
  button since before this existed, and that argument stands unchanged.
- **Revoking is HTTP, and that is the rule rather than an exception to it**
  (2026-08-27, ADR 0167). "Over HTTP, access can be taken away and never handed
  out" always permitted this; ADR 0128 put both halves in a terminal only
  because both were terminal-only at the time. Once connecting became a browser
  flow, `venona mcp token revoke <id>` was asking people for an id nobody had
  shown them — a person who signs in on the consent screen never sees one, and
  on a hosted instance they are not the one with a shell.

  Two routes, and the difference between them is the whole design:

  - `DELETE /api/meta/mcp/tokens/{token_id}` — the caller's own, on the reader
    allowlist. **Ownership is checked in the route**, because token ids are
    printed by the CLI and rendered on a page and so are not secret; without
    that check the button is a way to end somebody else's connection by typing
    an id. Answers **404, not 403**, for one that is not theirs, for the reason
    `/api/auth/callback` makes its refusals indistinguishable: a 403 confirms
    the id exists.
  - `DELETE /api/admin/tokens/{token_id}` — anybody's, admin by default-deny.
    No ownership check, deliberately: it is the counterpart of the unscoped
    LIST, and a list that names a leaked credential beside a revoke that refuses
    it leaves the hole exactly where it was.

  Both are outside `MCP_PATHS`, so an agent token can end neither its own
  connection nor its siblings'. Both are idempotent — `{"ended": false}` for one
  already gone, which a page acting on a stale list must be able to tell from
  success.
- **Listing is HTTP, and it is TWO endpoints.** `GET /api/meta/mcp` is
  reader-reachable and **filtered to the calling user** — an unscoped one would
  be one reader reading the label, prefix and last-used time of another's
  credential. `GET /api/admin/tokens` is the unscoped view, admin by
  `authz`'s default-deny, and it exists because the scoping left the operator
  unable to see what credentials existed on their own instance: a leaked token
  was invisible to the only person who could revoke it. It names the OWNER'S
  ADDRESS, LEFT JOINed in `db/repositories/token.py`, and shows a token with a
  NULL `user_id` rather than dropping it. Both are outside `MCP_PATHS`, so an
  agent token cannot enumerate its siblings through either. No secret is stored,
  so none can be served by either.
- **SHA-256, not a KDF.** A password hash defends a *chosen* secret against a
  dictionary; this is `secrets.token_hex(32)` and `mint` takes no caller-supplied
  secret, so there is no dictionary. A 50–100 ms KDF on every request of an agent
  doing 200 lookups is a real cost, and it would need the `cryptography`
  dependency this module opens by refusing. The reasoning inverts the day a token
  can be chosen by a human; a test pins the premise.
- **Revocation is independent of the session epoch.** `bump_epoch` kills every
  session everywhere, which is right for a browser. If an agent token consulted
  it, one logout on the operator's phone would unplug every connected agent, days
  later, with nothing naming the cause.

**Middleware order is load-bearing and counter-intuitive.**
`add_middleware` inserts at position 0 and the stack is built over
`reversed(...)`, so **the last added is the outermost**:

```
RequestId  →  SecurityHeaders  →  CORS  →  Auth  →  app
```

- SecurityHeaders must wrap Auth, or every 401 and 429 — the responses an
  attacker most reliably sees — ships bare, with every unit test still passing.
- **CORS must wrap Auth.** A browser sends an unauthenticated `OPTIONS`
  preflight before any cross-origin request carrying `Authorization`. Inside
  Auth, that preflight is a 401 and the real request is never sent; the symptom
  is "CORS error" against a healthy API, and nothing about it points at
  ordering. Before CORS existed the preflight was a 405.
- CORS must sit *inside* SecurityHeaders, because it short-circuits the
  preflight and answers it itself.

**CORS is off unless an origin is named, and `*` is refused at startup.**
`VENONA_CORS_ORIGINS` is empty by default and the middleware is then not
installed at all — the web app is same-origin in both development (Vite proxies
`/api`) and production (the reverse proxy serves both). The browser extension is the first
caller that is not.

**`allow_credentials=False`, permanently.** The extension authenticates with the
bearer token, so the browser never needs to attach `venona_session` to a
cross-origin request — and with credentials off it cannot. That removes CSRF by
construction rather than by handling it carefully, which is the version that
survives someone adding a route. An allowed origin is *not* a credential: CORS
decides who may READ a response, never who may make the request, and
`tests/test_cors.py` pins that an allowed origin with no token still gets a 401.

**MCP is a route, not a mount, and that is a security decision.**
`POST /api/mcp` is an ordinary `APIRoute`. Mounting an ASGI sub-app would still
be *authenticated* — the guard is the `/api` prefix either way — but a `Mount`
contributes nothing to `app.openapi()["paths"]`, which is what
`test_every_api_route_requires_auth` walks, and it is not an `APIRoute`, which is
what `tests/test_route_contracts.py` walks. Both sweeps would pass over the
largest new attack surface in the app while still reporting forty-plus
operations checked. Measured, a `Mount` also compiles to
`^/api/mcp/(?P<path>.*)$` and 307s the bare path a client is given.
`tests/test_mcp_auth.py` asserts nothing under `/api` is mounted at all.

**MCP adds zero entries to `EXEMPT_PATHS`.** A bearer on every request is all the
transport needs. The OAuth 2.1 + RFC 9728 + dynamic-client-registration flow that
claude.ai's connector directory requires would need five, two of them at the site
root and therefore outside `GUARDED_PREFIX`, outside this contract's guard and
outside the sweep. The price of MCP OAuth here is measured in exempt paths, not
in lines of code.

**MCP needs no CORS.** An agent is not a browser: it sends no `Origin` and
enforces no same-origin policy. `VENONA_CORS_ORIGINS` must not be widened for
it, still less to `*`.

> Changing this requires touching: `api/middleware.py`, `api/security.py`,
> `api/ratelimit.py`, `api/routers/mcp.py`, `api/mcp/*`,
> `api/routers/meta.py` and `api/routers/admin.py` (the two listings and the two
> revokes), `api/schemas/meta.py` and `api/schemas/admin.py`,
> `services/token_service.py`, `db/repositories/token.py`,
> `db/migrations/0024_mcp_tokens.sql`,
> `docs/schema.sql` (generated — `make schema`), `cli.py` (`venona mcp token`),
> `main.py` (the middleware stack), `docs/CONTRACTS.md`,
> `docs/USER-GUIDE.md` §12, `docs/ADMIN-GUIDE.md`,
> `web/src/views/AccountView.vue` (its docblock argues no HTTP mint, and now has
> to say *which* token it means), `web/src/views/AgentsView.vue`,
> `web/src/lib/agentCopy.ts`, `web/src/api/schema.d.ts` (generated —
> `make types`), `scripts/ui_smoke.py` (`agents_checks`, which holds both the
> absent mint button and the present Disconnect),
> `tests/test_auth.py`, `tests/test_cors.py`, `tests/test_mcp_auth.py`,
> `tests/test_mcp_tokens.py`, `tests/test_authz.py`, and
> `tests/test_mcp_readonly.py`.


### C12 addendum — OAuth 2.1 for agents

An authorization code is exchanged for an ORDINARY `mcp_tokens` row. That is the
whole design and every other property follows from it: one credential type, one
verification path (`token_service.verify`), one revocation story (the routes
ADR 0167 added, and `venona mcp token revoke`), and read-only-ness untouched
because it was never a property of how a credential was obtained.

- **Discovery is two documents at the SITE ROOT** — RFC 9728 and RFC 8414 fix the
  paths there. They are the only routes outside `/api`, therefore the only ones
  outside `AuthMiddleware`, and any reverse proxy in front must forward them or the SPA
  answers a discovery fetch with `index.html` and a 200.
- **The 401 on `/api/mcp` carries `WWW-Authenticate: Bearer resource_metadata=…`**
  and no other 401 does. On a browser path it would invite a human-facing client
  to start an OAuth flow instead of showing the login page.
- **A session-less `GET /api/oauth/authorize` is sent to sign in, not refused**
  (2026-08-27, ADR 0167). `middleware.AUTHORIZE_PATH` names it and one branch
  answers 302 to `/api/auth/login?next=<path+query>`. An MCP client opens
  `authorization_endpoint` verbatim out of the RFC 8414 document and cannot know
  to enter through `login` the way the extension does, so the JSON 401 was a
  dead end for every operator without a live cookie.

  **`EXEMPT_PATHS` is NOT widened to get this**, and the distinction is the
  contract: the endpoint still requires a session to do anything, and only the
  SHAPE of the refusal changed. **GET only** — the consent `POST` is what mints
  a code, a redirect would drop the form, and exempting the *path* would take
  the middleware off that request. `tests/test_auth.py`'s sweep asserts the
  redirect target rather than skipping the route, so it still counts as covered.
- **RFC 8707 `resource` is validated at BOTH endpoints when present, and absence
  is allowed.** `authorize` refuses a mismatch by redirecting with
  `invalid_target` — before a human is shown a consent screen for access that
  cannot be granted — and `token` refuses again, because the two requests are
  independent and nothing carries the earlier answer forward. Absent is
  admitted: the extension pairs through these endpoints and sends none, and
  RFC 8707 makes the parameter optional at the protocol level even though MCP
  requires its own clients to send one.
- **The consent screen copies nine values out of `web/src/styles/tokens.css`**
  and declares `color-scheme: light dark`. It cannot `<link>` the built
  stylesheet — Vite hashes that filename, and this page has to render on the day
  the SPA build is broken, which is exactly when somebody is wiring up a client.
  `tests/test_oauth.py` resolves the tokens through their primitives and pins
  the copy.
- **PKCE `S256` is required.** `plain` is permitted by RFC 7636 and forbidden by
  2.1; with `plain` the verifier IS the challenge and rides the same redirect.
- **Redirect URIs are compared by exact string equality**, and an unregistered one
  is answered on OUR page, never by redirecting — that is what keeps an
  authorization server from being an open redirector with the client's `state`.
- **Codes are single-use, enforced with `SELECT … FOR UPDATE`** and by recording
  `redeemed_at` rather than deleting the row: a replay is a signal worth keeping.
- **Every redemption failure says the same sentence.** Five distinguishable facts
  would tell an attacker holding one of them which one they hold.
- **No refresh token.** Its purpose is to avoid asking the human, which is the one
  thing worth asking for here.
- **The browser extension is the second client class of this flow** (2026-08-24).
  It pairs with `scope=annotate` from `chrome.identity.launchWebAuthFlow`,
  entering at `GET /api/auth/login?next=<authorize path+query>` because
  `authorize` deliberately 401s without a session; `_safe_next` passing a
  same-site path WITH its query intact is therefore load-bearing for pairing.
  The redirect URI is `https://<extension-id>.chromiumapp.org/`, an ordinary
  https URI to `_assert_safe_redirect` — no allowlist. The extension registers a
  fresh client per pairing attempt (the redirect derives from the runtime
  extension id), so `oauth_clients` rows accumulate by design.
- **Consent copy is per-scope and reader-worded.** `mcp` is approved by an
  operator and shows the path list and the CLI revoke command; `annotate` is
  approved by a reader and says what the extension can and cannot do in reader
  words, with expiry as the revocation story. `tests/test_copy_style.py`
  sweeps the consent template's strings like any other user-facing surface.

> Changing this requires touching: `api/routers/oauth.py`,
> `api/schemas/oauth.py`, `services/oauth_service.py`, `api/middleware.py`
> (`EXEMPT_PATHS`, `AUTHORIZE_PATH`, the redirect branch and the
> `WWW-Authenticate` branch), `api/authz.py`, `api/ratelimit.py`, `main.py` (the
> two root routes), `web/vite.config.ts`,
> `db/migrations/0003_oauth.sql`, `docs/schema.sql` (generated — `make schema`),
> `docs/USER-GUIDE.md` §12, `extension/src/shared/pairing.ts`
> (the second client class), `web/src/views/AgentsView.vue` and
> `web/src/lib/agentCopy.ts` (what a reader is told and how a connection is
> named), and `tests/test_oauth.py` + `tests/test_auth.py` +
> `tests/test_mcp_auth.py` + `tests/test_authz.py` + `tests/test_copy_style.py`.

### C12 addendum — authorization

Authentication answers *are you anyone*. Authorization answers *are you allowed*,
and it is enforced in the same place and the same way, for the same reason.

- **`api/authz.py` holds the table.** Only the READER and PUBLIC routes are
  listed; everything else is `admin`. `role_for_request` returns `admin` for any
  route it does not recognise, so forgetting to classify a new one fails closed.
- **Classify by ROUTE, never by verb.** `POST /api/graph/query`,
  `/api/graph/overview`, `/api/graph/brief` and `/api/entities/cards:batch` are
  READS that use POST for body size. "GET is reader, POST is admin" breaks the
  graph explorer and every hover card.
- **`tests/test_authz.py::test_every_route_is_classified` is the ratchet.**
  Runtime already fails safe; the test is how you find out at review time rather
  than from a reader who cannot open a page.
- **Two roles, no third one for agents.** An `mcp_tokens` row belongs to a
  `users.id` and inherits that user's role, narrowed by `scope` →
  `authz.SCOPES` → `Principal.reach`. Read-only-ness is structural, not a
  permission.
- **Membership and status are re-checked on EVERY request**, not just at login.
  That property predates the `users` table — removing an address from
  `VENONA_AUTH_ALLOWED_EMAILS` ended its sessions immediately — and had to
  survive becoming one.
- **`users` IS the membership list, at the door as well as after it.** For a
  while it was only after it: `AuthMiddleware._lookup_user` decided every request
  from the row while `routers/auth.py::callback` decided sign-in from the env
  allow-list and never read the table. The two halves of one boundary disagreed
  about what membership meant, and the visible cost was that `venona users add`
  produced a row that could not obtain a session — authorized for everything a
  reader may do, and refused at the door. Nothing tested the callback, which is
  how they drifted that far apart.
- **The env allow-list is the bootstrap and the break-glass**, in both places.
  An address in it with no `users` row resolves to a synthetic admin, because
  `ensure_from_env` runs in the lifespan and `httpx.ASGITransport` never runs a
  lifespan. **A row always wins**, so suspension is not bypassable this way —
  and that is an ordering, not a disjunction. Written as
  `email_is_allowed(...) or user is not None` it reads correctly and fails open
  for precisely the person most recently suspended, since every address
  `ensure_from_env` ever created is still in the environment. Only the ABSENCE
  of a row consults `os.environ`. `tests/test_membership_gate.py` pins both
  directions and the ordering between them.
- **Over HTTP, authority may be REDUCED without limit, and EXTENDED only as far
  as `reader`.** Crossing into `admin` requires shell access. This is the rule
  that let user management move off the CLI at all: `cli.py::_users` kept it
  there because "an HTTP endpoint that grants a role is reachable by anything
  holding a session", and the ceiling is what survives of that argument.
  Granting a reader is not minting a credential — the credential is the identity
  provider's, and the address still has to be one its owner can authenticate as.
  Granting an admin hands out model spend, every `config/*` read including the
  operator's own relevance profile, and `POST /api/data/purge`.

  **Enforced in `user_service`, by `allow_admin` defaulting to closed on both
  `create` and `change_authority`; only `cli.py` passes it** (ADR 0168). It was
  enforced by a `Literal["reader"]` on the admin router's request model alone,
  and that guarded one door out of several: the hub's login sync and
  `provision-user` both reached `change_authority` and `create` directly, so a
  `venona_role` claim of `admin` minted an administrator on every instance that
  trusted the hub. A ceiling on one caller reads as a ceiling on the surface and
  is not one — which is the next bullet's rule, arriving late to its own file.
  `create` carries it too because a FIRST hub login provisions through `create`
  and never through `change_authority`.

  The `Literal["reader"]` STAYS on the request model. It is no longer the
  enforcement, but it is what keeps the refusal in the OpenAPI document so the
  frontend cannot offer a control the server would refuse.

  The ceiling is ONE-DIRECTIONAL. The hub still demotes and still suspends, and
  a refused elevation does not refuse the sign-in: the person keeps the role they
  had and the disagreement goes to the log. `ensure_from_env` is not a wire —
  `VENONA_AUTH_ALLOWED_EMAILS` is set by whoever deploys the instance, and it is
  how an instance gets its first admin at all.
- **The rules live in `user_service.change_authority`, not in the router.** The
  advisory lock, the self-target refusal, the last-admin refusal, the admin
  ceiling and the audit write are all there, because `venona users` reaches the
  same rules from another process and a check in a route is a check the CLI does
  not run. The actor is re-read INSIDE the lock: `AuthMiddleware` resolved it
  before routing and the change commits after, so an admin suspended in between
  would otherwise complete it. The ceiling was the one rule that lived in a
  router instead, and it failed exactly as this bullet predicts (ADR 0168) —
  which is worth remembering the next time a rule looks like it belongs on a
  request model.
- **Approval never changes an existing row's role or status.** `upsert_by_email`
  is `DO NOTHING`, deliberately. The request form is public, so a suspended user
  can file a fresh request indistinguishable from a stranger's; approving it must
  not quietly reinstate them, and it must not demote a break-glass admin by
  creating a `reader` row for an address in `VENONA_AUTH_ALLOWED_EMAILS` — a row
  always wins, so that would strand the instance through the one door this phase
  opens. Both are refused, and reinstating is a separate, visible act.
- **Suspension reaches a credential already in use.** Every route re-checks on
  the next request, which is enough everywhere except the one that holds a
  connection: `GET /api/runs/{run_id}/stream` clears the middleware once and then
  yields `model_message` frames for up to forty minutes. It re-resolves the
  viewer's row on a thirty-second timer and simply ENDS when the answer changes;
  a synthesised `done` would be a false statement about the run. An
  authorization code approved before a suspension is refused at exchange for the
  same reason — minting the token would leave a credential that silently starts
  working again on reinstatement.
- **Writes are rate limited per PERSON as well as per address.** Every bucket in
  `ratelimit.py` is keyed on `scope["client"]`, which is the one key a signed-in
  caller can change by moving networks. `USER_WRITE` is charged after the per-IP
  check, on `principal.user_id`, so it can only tighten. It tests the id for
  TRUTHINESS, not `is not None`: the env break-glass admin has no row and carries
  `""`.
- **`SameSite` is a SITE, not an origin.** `allow_credentials=False` plus
  `samesite="lax"` removes cross-origin CSRF by construction, and neither stops a
  sibling subdomain: `evil.example.com` and `venona.example.com` are the same
  site, so a cookie set for one rides a request to the other. That was academic
  when the app had no authenticated writes worth forging. It is not now — seven
  admin writes and the first reader write exist — so any deployment that puts
  another application on a sibling subdomain of this one needs a CSRF token, and
  this contract is where that decision has to be recorded.
- **An unreachable database refuses the request; it does not mint an admin.**
  `_lookup_user` used to answer a connect failure with the synthetic env admin,
  which reads the environment *without* reading the row — so its own docstring's
  "only the ABSENCE of a row reaches branch 2" was true of the fallback and false
  of the exception handler. Anyone demoted or suspended who was still named in
  the environment got admin back for as long as they could keep connections
  exhausted, and the synthetic record's empty `id` makes `actor_id` return None,
  so that principal also evades the self-target guard and signs every audit row
  as nobody. Break-glass for a database outage is `VENONA_AUTH_API_TOKEN`, which
  `verify_bearer` resolves without touching Postgres, so failing closed costs
  nothing.

> Changing this requires touching: `api/authz.py`, `api/middleware.py`,
> `api/security.py`, `api/ratelimit.py` (`USER_WRITE`), `api/routers/auth.py`
> (the callback is where membership is decided at the door), `api/routers/admin.py`,
> `api/routers/access.py` (approval is where a request becomes a user),
> `api/schemas/admin.py` (the `reader` ceiling is a `Literal` there — for the
> OpenAPI document; the ENFORCEMENT is `allow_admin` in `services/user_service.py`,
> ADR 0168), `api/routers/mgmt.py` (`provision-user` returns a refusal rather
> than a 500 when the ceiling declines a hub grant),
> `api/oauth.py` (`identity_for_code` — `iss` + `sub` is the identity and was
> being verified and discarded), `api/routers/oauth.py` (the owner check before
> minting), `services/user_service.py` (`change_authority` and `create` — THE
> door, and `allow_admin` is its ceiling; `ensure_from_env` passes it because the
> environment is not a wire),
> `services/run_service.py` (the stream's revocation gate),
> `services/token_service.py`, `db/repositories/access.py`,
> `db/migrations/0002_identity.sql` and `db/migrations/0005_authority.sql`, which
> supersedes it — 0005 adds `user_events`, the access-request join to `users`, and
> is the migration this contract's authority rules are written against,
> `docs/schema.sql` (generated — `make schema`),
> `cli.py` (`venona users`, `venona access`, `venona mcp token`),
> `web/src/stores/session.ts`, `web/src/router/index.ts`,
> `web/src/views/PeopleView.vue`, `web/src/views/AdminView.vue`,
> `docs/RUNBOOK.md` ("Nobody can sign in"), and
> `tests/test_authz.py` + `tests/test_auth.py` + `tests/test_mcp_auth.py` +
> `tests/test_membership_gate.py` + `tests/test_authority.py` +
> `tests/test_admin_api.py` + `tests/test_revocation_reach.py` +
> `tests/test_ratelimit.py` + `tests/test_mgmt_api.py`.
>
> A test that needs an administrator to EXIST must arrange one with
> `allow_admin=True`, standing in for shell access — never through a route, which
> is now refused. `tests/test_mgmt_api.py::_shell_admin` is the pattern.

### C12 addendum — entitlement

A THIRD axis, asked after reach and after role. Authentication answers *are you
anyone*; authorization answers *are you allowed*; this answers *have you paid*.
It is enforced in the same middleware, before routing, for the same reason.

- **`api/authz.py` holds the table, and it is DEFAULT-DENY for readers.** A
  reader route absent from `UNENTITLED_ROUTES` needs a subscription, so a reader
  feature added next month is paid by omission rather than free by omission.
  `entitlement_required` returns True for anything it does not recognise.
- **BOTH sets compile into ONE ordered table.** `/api/briefs/{brief_id}` becomes
  `^/api/briefs/[^/]+$`, which also matches `/api/briefs/latest` and
  `/api/briefs/for-you`. `_specificity` sorts within a set, so a literal in one
  frozenset can never shadow a wildcard in another. Two tables would have made
  the personal brief — the only per-reader model spend a reader can trigger —
  free to anyone who had ever paid.
- **Admins are never asked.** An admin operates the instance, and a paywall
  between the operator and a broken surface fires at the worst possible moment.
  Nor is any principal without a `users` row: the break-glass env admin, the
  root bearer, the hub's `vmgmt_` token and `via="disabled"` are all entitled by
  construction (`Principal.entitled` defaults True).
- **The brief family is gated by ROW, not by route.** `GET /api/briefs/latest`
  returns the newest brief the caller may open rather than refusing, because `/`
  renders it and a 403 there would bounce a lapsed reader to the paywall instead
  of to the archive they paid for. The ceiling threads router →
  `services/brief_service.py` → `db/repositories/brief.py`, including the by-id
  fetch and the prev/next arrows (ADR 0211).
- **"Never subscribed" and "lapsed" are different states.** Both fail
  `entitled`; the first has a NULL `entitled_through` and gets
  `UserRecord.NOTHING`, an empty-string ceiling no date satisfies. Reading a
  NULL as "no limit" would hand the whole archive to somebody who never paid.
- **The gate has three sources and an order, and ships OFF** (ADR 0214).
  `VENONA_BILLING_ENFORCED` wins when set, in EITHER direction, and is the
  break-glass for a day the hub is wrong; otherwise the hub's switch as the mgmt
  push last left it; otherwise off. `config/settings.yaml` is still never
  consulted, for the same reason `AuthConfig` is env-only: a paywall an admin
  session could switch off through the HTTP config editor is not a paywall. The
  hub is a different authority, and its switch arrives through
  `POST /api/mgmt/settings` into `hub_flags`, a table with no editor.
- **`billing_enforced()` answers from memory, and the refresh is half the
  feature.** It is asked on every request and again inside `UserRecord.entitled`,
  which holds no connection. `refresh_billing_enforced(conn)` is called at
  startup and by the mgmt write; there is no TTL, and that is correct only while
  the API is ONE process. NOTHING PINS IT: run one API process. A second worker makes the
  value stale in whichever process did not take the push, and a zero-downtime
  deploy overlaps two for about sixty seconds.
  `tests/test_single_process_assumptions.py` is what holds this now.
- **`UserRecord.entitled` is the ONLY place that gate is applied, and it is the
  first line of it.** The column defaults to `none`, so every account created
  after migration 0023 reads as unentitled — and an instance that does not
  charge must entitle everybody, or a fresh account on a local or self-hosted
  box gets an empty product with no refusal and no error. Four gates ask this
  question; asking it four times is how the ceiling ended up reading the column
  while the other three read the predicate.
- **A role cannot be seen from a column, so the admin exemption is stated twice.**
  `recently_seen` and `readers_subscribed` carry `role = 'admin'` beside the
  status test, matching `UserRecord.entitled`. Without it an administrator whose
  row says `none` is dropped from their own instance's warm pass and brief
  delivery — the operator is the first person to lose the product.
- **Two spend gates are NOT routes and no page reaches them.**
  `users_repo.recently_seen` bounds the personal-brief warm pass, and
  `notify_repo.readers_subscribed` bounds brief DELIVERY —
  `scheduler.announce_brief` sends the brief, so an unentitled reader with an
  address would keep receiving the product in full. Both take the same
  `billing_enforced()` answer, and `recently_seen` additionally compares
  `entitled_through` against a caller-supplied `now`, so a stalled hub push
  degrades to "stop spending" rather than "spend indefinitely".
- **The instance holds a projection and never a credential.** Stripe lives in
  the hub (ADR 0209). Entitlement arrives as id_token claims at login and as
  optional EXTRA FIELDS on the existing `provision-user` `update` action —
  never a new action, because `ProvisionUser.action` is a pydantic pattern and
  an unknown value is a 422 before any handler runs. `authz.MGMT_REACH` is
  unchanged by this contract.
- **ALL FOUR FIELDS travel together on both paths**, and the notice is one of
  them. The push counts a 404 from an instance as delivered, on the promise that
  a member who has never signed in there carries the state in when they do — so
  a login that carried only status and date dropped the dunning notice for
  exactly the reader a second instance was still trying to bill, with no bump
  left pending to send another.
- **`comped` is the instance's own word and the projection cannot overwrite it**
  (ADR 0213). The hub bills; a reader it has never sold anything to is `none`
  there, so the hub has no way to express a complimentary account. Both
  projection writers carry `AND entitlement_status <> 'comped'`, and
  `venona users comp` is the only thing that sets it. Without that refusal
  migration 0023's backfill — which exists so that turning the paywall on cannot
  lock out the readership — is undone by each reader's first sign-in.
- **The client's redirect table must agree with `UNENTITLED_ROUTES`.**
  `web/src/router/guards.ts` holds the paywall bounce, and a route excluded
  there that the server keeps open is a screen the API would have served and the
  app refuses, with nothing in the network tab to explain it. The whole settings
  rail is open, revocation especially.
- **Every reader route is NAMED on this axis, not left to the default.**
  `tests/test_authz.py` sweeps the real route list. Default-deny is a backstop
  for a path no template matches, and `^/api/briefs/[^/]+$` matches a great many
  paths nobody has written yet — a future `GET /api/briefs/export` would be free
  by accident rather than by decision.

> Changing this requires touching: `api/authz.py` (`UNENTITLED_ROUTES`,
> `ENTITLED_ROUTES`, `_entitlement_table`, `billing_enforced`,
> `refresh_billing_enforced`, `MGMT_REACH`),
> `api/routers/mgmt.py` (`POST /api/mgmt/settings`, and `billing_enforced` on
> the status reply), `db/repositories/hub_flags.py`,
> `db/migrations/0024_hub_flags.sql` + `db/alembic/versions/0022_hub_flags.py`,
> `main.py` (the startup refresh),
> `api/middleware.py` (the third check), `api/security.py` (`Principal`),
> `services/user_service.py` (`UserRecord.entitled`, `archive_ceiling`,
> `NOTHING`), `db/repositories/users.py` (`_AUTH_COLUMNS` — a column missing
> there defaults to `comped` and the paywall silently never fires — plus
> `set_entitlement` and `set_comped`),
> `api/routers/mgmt.py` + `api/schemas/mgmt.py`, `api/routers/auth.py` +
> `api/schemas/auth.py` (`BillingState` on the session), `api/routers/briefs.py`,
> `services/brief_service.py`, `db/repositories/brief.py`,
> `db/repositories/notify.py`, `notify/__init__.py`,
> `services/personal_brief.py`, `db/migrations/0023_entitlement.sql` and
> `db/alembic/versions/0021_entitlement.py`, `db/tables.py`,
> `docs/schema.sql` (generated — `make schema`),
> `web/src/api/schema.d.ts` (generated — `make types`),
> `web/src/lib/billing.ts` (THE only place a notice kind becomes words, and
> `billingUrl`, which is why `manage_url`'s query cannot be appended to by hand),
> `web/src/composables/useBillingNotice.ts`,
> `web/src/components/ui/BillingNotice.vue`, `web/src/components/ui/AppShell.vue`,
> `web/src/views/BillingView.vue`, `web/src/router/index.ts`,
> `web/src/components/nav/SettingsRail.vue`, `web/src/views/PeopleView.vue`,
> `web/src/lib/people.ts`, `web/src/router/guards.ts`,
> `web/src/api/client.ts` + `web/src/main.ts` (the 403 seam carries the refusal
> CODE, so a paywall and a role refusal cannot share one sentence),
> `docs/RUNBOOK.md`,
> `docs/ADMIN-GUIDE.md`, `docs/USER-GUIDE.md`, `scripts/ui_smoke.py`, and
> `tests/test_entitlement.py` + `tests/test_authz.py`.
>
> And on the hub: `hub/hub/routers/ops.py` (`/admin/settings`), `pages.py`
> (`ops_settings`), `middleware.py` (`OPERATOR_PAGES`, `OPERATOR_POSTS` -- the
> bearer gate's allowlist, which a new operator page must join or it answers
> 401), `repositories/settings.py`, `db/migrations/0008_hub_settings.sql`,
> `hub/hub/stripe.py`, `stripe_webhook.py`, `billing_apply.py`,
> `billing_state.py`, `routers/billing.py`, `repositories/subscriptions.py`,
> `repositories/billing_events.py`, `db/migrations/0005_billing.sql` +
> `0006_instance_pricing.sql` + `0007_notice_ordering.sql`, `jwt.py`
> (the FOUR claims), `routers/oidc.py`, `poller.py` (the push sweep),
> `mgmt_client.py`, `config.py`, and `tests/hub/test_billing.py` +
> `tests/hub/test_stripe_webhook.py` + `tests/hub/test_billing_config.py` (the all-or-nothing rule
> `assert_usable` enforces).

### C12 addendum — the hub's management credential

The hub (docs/adr/0141, 0142) holds one credential on this instance:
`VENONA_MGMT_TOKEN`, a `vmgmt_`-prefixed env value with `reach` fixed to
`authz.MGMT_REACH` — exactly `/api/mgmt/status`, `/api/mgmt/provision-user`,
`/api/mgmt/domain`, `/api/mgmt/settings` and `/api/mgmt/setup/start`. The leash
is the contract:

- **What the hub may do**: read the status envelope; add/update/suspend/
  reinstate users (suspension bumps the session epoch, so it bites on the next
  request, not the cookie's expiry); end a user's sessions (the `logout`
  provisioning action bumps the same per-user epoch with no status change —
  it is how a hub sign-out reaches every instance, `hub/signout.py`); write
  the DOMAIN DESCRIPTOR through `config_service.write_doc` (same validator,
  same history, same slug freeze); read `login_blurb` and `latest_brief_at` off
  the status reply, which is how the hub's gallery describes a domain in that
  domain's OWN words rather than in an opinion held on the hub; read the
  instance's `version`, `commit` and `started_at` off the same reply, which is
  the only way an operator can see that three properties deploying from one
  branch on separate build filters are not all on the same code (the release
  alone cannot show it -- it is one number for the whole repo until somebody
  bumps `VERSION`, which is why `commit` travels beside it); throw the
  SWITCHES it owns into `hub_flags`
  (today one: whether this instance charges to read — ADR 0214, and deliberately
  not a config document, because that surface is editable by an admin session);
  start a `domain_setup` run through `run_launcher`.
- **What it may never do**: read or write any other config document, control
  or cancel any run, mint or revoke any credential, export corpus data, or
  obtain a session — `verify_bearer` refuses the prefix, exactly as it
  refuses `vmcp_`, and `assert_usable` refuses a `vmgmt_` in the root slot
  and a non-`vmgmt_` in the mgmt slot.
- Revocation is rotate-and-redeploy: env-only, no DB row, by design at this
  scale (docs/adr/0142 records the trade).

The instance-side OIDC generalization rides with this: `VENONA_OIDC_ISSUER`
(+`_CLIENT_ID`/`_CLIENT_SECRET`) points the login flow at the hub instead of
Google, endpoints derived from the issuer's origin with no discovery, and the
`venona_role` claim is the hub's membership assertion — provisioned on first
login, synced on later ones, with local suspension always winning.

> Changing this requires touching: `api/security.py` (`MGMT_TOKEN_PREFIX`,
> `verify_mgmt`, the two `assert_usable` refusals, the `AuthConfig` OIDC
> fields), `api/authz.py` (`MGMT_REACH`), `api/routers/mgmt.py`,
> `api/schemas/mgmt.py`, `api/oauth.py` (issuer-derived endpoints, the
> `venona_role` claim), `api/routers/auth.py` (the callback's provisioning
> branch), `cli.py` (`venona auth mgmt-token`), and
> `tests/test_mgmt_api.py` + `tests/test_membership_gate.py`.
