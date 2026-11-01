---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — Settings is the config workspace; Prompts folded into it


`/prompts` edited six `profile/` files behind its own nav item. `/settings` held
three localStorage run defaults, a duplicate hidden-runs checkbox, a read-only
source table, a generated keyboard list, diagnostics, and an essay about what
was not built. Neither was where you would go to change what venona *does* —
`config/ontology.yaml`, `config/sources.yaml` and `config/settings.yaml` had no
write path at all outside `$EDITOR`.

One rail now holds every editable document plus a read-only view of the
assembled system prompts. The nav dropped to four. The old settings content was
deleted rather than moved twice: the keyboard list is generated into the `?`
overlay, diagnostics are in the footer `HealthStrip` (which gained the offline
chip it never had), and run defaults sit next to the Run now button that sends
them.

*Superseded in part, 2026-08-17.* The rail described here is `Admin → Config`
now, not Settings — see "Settings is the reader's account; the instance lives
under /admin" at the foot of this file. Everything below about raw YAML, the
frozen entity-type set and what was deleted rather than moved still holds; only
the address changed.

**Raw YAML, not forms.** The obvious build is a table of sources with toggles
and a predicate editor with domain/range pickers. Both config files carry
load-bearing comments — `sources.yaml` documents six dead feeds and *why* in its
`removed:` block, `ontology.yaml` annotates every `org_type` — and any form that
re-serialises the parsed document destroys all of it. Comment-preserving
round-trip is a dependency and a class of subtle bug; a textarea over a validated
file is neither.

**The entity-type set is frozen from the UI, predicates are not.** Adding a type
needs an `--entity-<prefix>` token (without it `lint:tokens` fails and the type
renders transparent) and seed examples; a prefix is the id namespace under
already-minted ids. Predicates are genuinely cheap per C10 — except the five in
`ANCHOR_PREDICATES`, which existed twice: as a literal inside `anchor_schema`,
in the module whose docstring says nothing is hardcoded, and as a constant in
`pipeline/anchors.py` that nothing actually read. The schema enum was the real
enforcement. One list now, in `ontology.py`, and a save that drops one is
refused.

**System prompts are read-only, deliberately.** Most of what they contain is
already editable one group up. What is left is the hard-rules preamble, and
rules 1 and 2 of it *are* invariant 3 — "extract only what the document says;
never add entities from your own knowledge". A textarea over that is a textarea
that switches off the guarantee acronym grounding and entity resolution rest on.
Showing the assembled text is the useful half and costs nothing.

**`extraction_guide` was deleted from the editor, not fixed.** It had been in the
allowlist since the prompts view shipped and nothing ever read it: `load_profile`
reads four files, and the extraction guide the model sees is generated from the
ontology. Per-type `guidance:` — now editable — is where that text belongs.

### The finding that mattered more than the feature

`db/models.py` bound `PREDICATES` and `SYMMETRIC_PREDICATES` at **import**, and
`graph/writer.py`, `graph/projector.py` and `graph/queries.py` imported the names
directly. Harmless while the ontology could only change by editing the repo and
restarting. Not harmless once it is editable over HTTP: a run triggered from the
UI executes *in the API process* (`asyncio.create_task` in the runs router), so
adding a predicate gave you a model told it could use it, a JSON-Schema enum
regenerated live that accepted it, `/api/meta/ontology` advertising it to the
graph filters — and then a silent drop in `projector._edge_rows`, before the
writer's whitelist was ever consulted.

Both constants are gone; the three call sites read `get_ontology()` per call.
`TYPE_PREFIX` and `ID_PATTERN` stay import-time, and are only safe because the
save policy above freezes them.

The regression test asserts through `_edge_rows`, **not** `upsert_edges`. The
first version of the fix touched only the writer and would have gone green
against a still-broken path — the projector filters first.

Two smaller things fell out of making these files browser-reachable:

- **Predicate names are interpolated into Cypher** (`MERGE (a)-[e:{predicate}]->(b)`;
  a relationship type cannot be parameterised) and the only shape guard was
  `str.isupper()`, which is `True` for `FOO]->() MATCH (n) DETACH DELETE n //`.
  Now `^[A-Z][A-Z0-9_]*$`, which hardens the CLI path too.
- **`config/settings.yaml` is the one document whose save reaches into the
  running app** — `get_settings()` is `lru_cache`d and routers read
  `app.state.settings`, bound once in `lifespan`. Clearing and rebinding covers
  most of it, but `llm.*` is captured at startup by `build_executor`. Rather
  than pretend, the response carries `restart_required` naming exactly those
  fields and the editor shows it. (`schedule.*` was on that list until
  `apply_schedule` made a save re-point the live job — see the 2026-08-18
  scheduling entry.) This project already has an entry about a setting that
  read as applied and was not (`provisional_promote_sightings`); that is the
  failure being avoided.

### What the editor cannot do

`profile/anchors.yaml` is read only by `venona anchors load` — nothing in a run
re-reads it — so saving it is inert. Rather than ship a file whose edits do
nothing, the Anchors document carries an **Apply** action with a dry-run preview
and a destructive-confirm, because applying deletes and rewrites the anchor run's
assertions. It leaves the Neo4j projection stale, so the response says so and
offers the rebuild.

And one cost the C10 "changing it is cheap" line understates: the C2 cache key
hashes the system prompt *and* the schema, both of which embed the ontology. Any
ontology save — including a prose-only one — invalidates every
`fixtures/llm/analyze/*`, and the replay executor hard-fails on a missing
fixture. That was always true of editing the file; it is now one click away, so
the editor says it.

**Amended by 0186.** "The one document whose save reaches into the running app"
was true in development and false in production for the life of the hosted deployment
deployment: `get_settings()` read a path built from `REPO_ROOT` while the rail
wrote one built from `config_dir`, and in production those are different files. The
loader follows `config_dir` now, and the document is carried forward by numbered
migrations rather than by a deploy that must not overwrite it.

**Amended by 0200.** Two halves went. "One rail now holds every editable
document": the documents are an index page at `/admin/config` and the editor has
no rail. And "a textarea over a validated file is neither [a dependency nor a
class of subtle bug]": the editor is CodeMirror. The clause that argument
actually rests on is untouched, and is why the swap is safe — CodeMirror never
parses and reprints the document, so no comment and no ordering can be lost the
way a round-trip serialiser loses them. Everything above about raw YAML, the
frozen entity-type set, read-only system prompts and what was deleted rather
than moved still holds.
