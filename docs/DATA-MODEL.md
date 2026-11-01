# Data model

Two stores with a strict hierarchy: **Postgres is the system of record, Neo4j is a
rebuildable projection.** If they ever disagree, Postgres is right and
`venona graph rebuild` fixes it.

`docs/schema.sql` is generated (`make schema`) and is the authoritative DDL.
This document explains the *why*.

---

## The assertion log

Everything the pipeline learns lands in one append-only table.

```
assertions(id, run_id, article_id, cluster_id, kind,
           surface, entity_type, entity_id_raw,
           predicate, subject_id_raw, object_id_raw,
           evidence_quote, evidence_span, salience, confidence,
           method, model, created_at, retracted_at)
```

Two columns carry most of the design weight:

**`evidence_quote` is `NOT NULL`.** An assertion nobody can trace to a sentence
is a rumour. This is what makes the provenance panel possible and what stops
the graph from accumulating claims with no origin.

**`entity_id_raw` is never updated.** It records what resolution decided *at the
time*. Merges do not rewrite it — see below. Retraction is a `retracted_at`
timestamp, never a `DELETE`.

`evidence_span` is `[start, end]` in **Python code points into
`documents.text_nfc`**. `text_nfc` is stored verbatim and never re-normalised;
every span and every acronym grounding offset in the database points into it, so
mutating it would silently invalidate all of them.

### Why this shape

Because the graph is derived rather than authored, four things become cheap that
are otherwise expensive:

| Operation | Cost |
|---|---|
| Merge two entities | one row + rebuild |
| Undo a merge | flip `active = 0` + rebuild |
| Retune scoring weights | pure Python, **zero model calls** |
| Change the ontology | edit YAML + rebuild |
| Prove idempotency | rebuild and diff `graph stats` |

---

## Merges

```
entity_mappings(from_id, to_id, merge_id, active, reason, created_at)
```

The projector collapses active chains to a fixed point (with a cycle guard, so a
bad merge cannot hang a rebuild) and rewrites ids **during projection only**.

Merges are manual EXCEPT where identity is provable. An incorrect auto-merge is
invisible and poisons every downstream query, so what `curate` acts on alone is
bounded by derivation and by a token list a person wrote: an acronym its
expansion generates, a trailing `(GLOSS)`, a corporate-form word, a trailing
generic head noun, or a name a person declared an alias of the other entity. A
model `merge` verdict is applied only where the names corroborate it too. Every
one is an `entity_mappings` row tagged with its reason and reversible by
`venona entity unmerge`. See ADR 0174.

The queue is `alias_conflicts`, filed by the resolver whenever it declines a
match it could not make safely and by `curate._file_entity_duplicates` for pairs
the resolver never saw, then classified by the `curate` stage into `duplicate` /
`collision` / `containment` / `resolver_error` / `anchor` / `cross_type` /
`type_conflict`. A row
leaves the queue two ways, and only one of them touches the graph:

```
venona entity merge <loser> <survivor>   the pair was one thing  -> resolution='merged'
venona conflicts --keep-apart <id>       the pair is two things  -> resolution='kept_apart'
```

`resolution` records which, because the two demand opposite follow-up and a
closed queue that cannot tell them apart is not auditable. Unmerging reopens a
`merged` closure; a `kept_apart` verdict stands on its own.

There is a third exit, `related`: two different things, and here is how they
relate. It writes an append-only `assertions` row and is undone by retracting
it. The pipeline's `suggestion` for such a row is an ACTION (`merge` /
`part_of` / `distinct`) and `suggestion_predicate` is which predicate
`part_of` actually meant — PART_OF between two organisations, BASED_IN between
an organisation and a country, NULL where several fit and the choice is the
curator's. The two are separate columns because "one is part of the other" is
plain English and the ontology has fifteen predicates that can express it.

(An earlier design had a `merge_proposals` table for a similarity sweep that was
never built. It held no rows and had no code referencing it; migration 0006
drops it. `alias_conflicts` does this job with real evidence attached.)

---

## Entity ids (contract C4)

```
^(country|gloss|org|person|plat|prog|tech):[a-z0-9][a-z0-9-]{0,63}$
```

Minted once from the canonical name, immutable thereafter. A rename adds an
alias. Prefixes come from `config/ontology.yaml`, so the id namespace and the
ontology can never disagree.

`gloss:` shares the namespace but is not an ontology type — glossary entries are
definitions, not graph nodes.

---

## Aliases and `strictness`

```
aliases(id, entity_id, surface, normalized, strictness, is_acronym,
        added_by, evidence_article_id, created_at)
UNIQUE (entity_id, surface)
```

**The unique key is `surface`, not `normalized`, and that matters.** Resolution
looks up by `normalized`; the annotator matches **literal** text. Multiple
spellings that normalise identically are legitimately distinct —
`Next Generation Command and Control` and `next-generation command and control`
must both exist, or the full form silently stops highlighting in prose. (It did.
See `docs/adr/`.)

### The strictness scale

Assigned at **write** time from `added_by` plus surface shape, so no backfill is
ever needed when the annotation tiers start consulting it.

| Level | Where it may match | Default for |
|---|---|---|
| **0** | never auto-matched | ambiguous surfaces, aliases < 3 chars, the stoplist |
| **1** | tier A only — the entity must already be in the brief's retrieval set | `added_by='extraction'`, lowercase aliases |
| **2** | global, **case-sensitive** | all-caps acronyms ≥ 3 chars |
| **3** | global, case-insensitive | multiword proper nouns ≥ 8 chars |

Level 1 is the mechanism that stops `Army`, `Shield`, `Sentinel` and `Vision` —
all plausible entity names *and* ordinary English — from lighting up every
paragraph. Level 2's case-sensitivity is why `MOSA` matches and `mosa` does not.

`alias_conflicts` records a surface already owned by a different entity of the
same type. It is rejected, never merged, and reviewed by hand.

`ambiguous_surfaces` holds surfaces with two or more plausible owners (`SDA`,
`TITAN`, `EW`). They are forced to strictness 0 at index-build time and the
hover card shows a disambiguation list rather than guessing.

---

## Documents and clusters

`documents` is one row per fetched article. Dedup runs in two layers:

1. **URL canonicalisation** → `url_key` (redirects followed, `utm_*`/`fbclid`/
   fragments stripped, params sorted). `sha256(url_key)` is the document id.
2. **Content hash** over NFKC-normalised, whitespace-collapsed text — catches
   wire reprints under different URLs.

A third SimHash layer is deferred; the column is reserved. See `docs/adr/`
for why Hamming ≤ 3 turned out to be redundant with layer 2.

**Clusters are the downstream unit, not documents.** A story carried by five
outlets is one brief item with a `sightings` count as its salience signal —
counting documents would just inflate wire reprints.

The **canonical document** of a cluster is the earliest `published_at`,
tie-broken by source order in `sources.yaml`. `analyze` runs on that document's
text. Acronym grounding records the *specific* article the parenthetical
appeared in, which may not be the canonical one.

`date_confidence` (`feed` / `parsed` / `header` / `unknown`) exists because
feeds lie about dates and silently defaulting to `now()` would corrupt every
recency ranking.

---

## Glossary

```
acronym_definitions(id, surface, normalized, expansion, entity_id, method,
                    confidence, corroboration_count, corroborating_sources,
                    grounding_article_id, grounding_url,
                    grounding_quote, grounding_span, status, ...)
```

Invariant 3 is enforced at load time: **`status = 'active'` requires a
grounding article.** Methods, in descending trust:

| Method | Confidence | How |
|---|---|---|
| `in_article` | 1.0 | Parenthetical in the same document, verified by initials |
| `in_corpus` | 0.9 | Grounded in a different already-ingested document |
| `web_grounded` | 0.55 → 0.85 | We fetched the page; the model quoted from *our* text. Rises with corroboration |
| `manual` | — | Human entry |
| `undefined` | 0.0 | Nothing grounded. Renders as visibly undefined. |

### Confidence is evidence, not a constant (migration 0018)

Tier 3 is the only method whose confidence moves, and it is the only one that
should. A parenthetical either verifies against the initials or it does not, and
a second document saying the same thing does not make the first more true. A web
page is different: it is one publisher's assertion, and independent agreement is
exactly what makes it more credible.

So `web_grounded` opens at **0.55** — one page, four checks passed, provisional
— and gains 0.1 per independent source to a ceiling of **0.85**, deliberately
below `in_corpus`. A page on the internet agreed with three times is still
weaker evidence than a sentence in the corpus this system ingested and can
re-read.

Two rules make the number trustworthy rather than merely present:

- **`corroborating_sources` is a list, not a tally.** The nightly run re-reads
  documents it has already ingested; a counter would climb on re-analysis of one
  article and manufacture confidence out of nothing. Independence is decided by
  comparing source keys — `documents.id` for tiers 1–2, the URL for tier 3 —
  which is only possible if they are kept.
- **Evidence may only move a row UP `TIER_RANK`.** `store_definition` used to
  overwrite `method` and `confidence` with whatever the latest caller passed, so
  a tier-1 definition could be rewritten as `web_grounded` by a later page that
  *agreed* with it — the row recording weaker provenance because more evidence
  had arrived. The stronger tier's quote and URL stay on the row; the weaker
  source still counts as agreement.

A human verdict outranks all of it: corroboration never reinstates a rejection.

A grounded expansion that resolves to a known entity becomes an **alias**; one
that doesn't (`MOSA`, `TRL`, `OTA`) stays a glossary entry, so the graph isn't
polluted with non-entities.

---

## Briefs

`briefs` + `brief_blocks`. Blocks store `text` plus `spans` and `marks` as JSON
offset lists. The API converts to pre-split runs (contract C3) on read.

Storing offsets rather than runs is deliberate: offsets are canonical and
**recomputable**. Re-annotating every historical brief after improving the alias
table is a pure function of (stored blocks, current alias index) and costs zero
model calls.

`brief_blocks.spans` is also the only record of which entities a brief named,
and so the source for the brief-scoped graph (`POST /graph/brief`). Neo4j
carries no brief linkage at all — `brief_service.brief_entities` reads the spans
and resolves the merge chain, because a brief written before a merge names an id
the projector has since collapsed onto its survivor.

---

## Runs

`runs` / `run_stages` / `run_events` / `llm_calls` / `run_metrics` /
`run_source_stats`, plus `harness_credentials` for the model service.

`runs.harness` and `runs.billing_mode` are stamped when the executor is CHOSEN,
not when the row is written — those are different moments, and for a queued
light run they can be minutes apart. A run cancelled before admission records
neither, correctly.

`run_events` is an append-only log with a `(run_id, seq)` composite key; `seq` is
allocated inside the insert transaction. The SSE stream is a *view* over this
log, which is what makes reconnect-and-replay lossless — close the laptop
mid-run, come back, see everything.

`llm_calls` stores `models` as a JSON map because a Claude Code invocation
returns a `modelUsage` map containing a phantom haiku entry alongside the
requested model. Cost is the sum over that map, not the requested model's
slice. A service whose envelope carries plain token counts fills the same
column with a single entry; the shape is the ledger's, not one vendor's.

**`llm_calls.harness` and `llm_calls.billing_mode`** say which service ran the
call and how it was paid for. Nullable, no default, never backfilled — the same
rule `runs.outcome` follows, and for the same reason: a guessed value on a
historical row is worse than an absent one.

**`llm_calls.cost_basis` is what makes `cost_usd` summable.** One column holds
four incompatible kinds of number, and adding them together means nothing:

| basis | what the number is |
|---|---|
| `list_price` | a subscription's shadow price — what the work WOULD have cost (ADR 0180) |
| `measured` | what a metered provider actually billed |
| `computed` | tokens multiplied by a price table this repo maintains |
| `none` | a service that reports neither, and the column is not a figure at all |

Anything that aggregates cost — `metrics.cost_by_day`, the Overview panel, the
price quoted in front of **Look up all** — either filters to one basis or says
which it is showing. `lookup_run_costs` filters by `harness` for the same
reason: after a switch, averaging the last N lookups would quote the other
service's price for a sweep that may now cost real money.

**A run carries TWO verdicts, and neither may be spelled through the other.**

`status` is whether the machinery worked. A run that DEGRADED ends
`succeeded_partial`, never `failed` — a partially useful run must not be
reported as a failure. Degraded means `ctx.warn`, which sets `ctx.degraded`, and
not simply "warnings is not empty": two of the three message channels do not
touch the status. `ctx.todo` is work waiting on a person (the curation queue,
which re-fires every run until somebody rules on it) and `ctx.note` is a remark
with nothing to do about it, so a clean run can carry plenty of messages and
still be `succeeded`.

Each item in `runs.warnings` carries its own `level` -- `warning`, `todo` or
`note` -- plus its text and, for a todo, a symbolic `to` naming the surface
that can act on it. The level is per MESSAGE because a run routinely has all
three at once: with only the run-level flag, a surface can pick just one
heading for the whole array, and a degraded run then renders its curation
backlog as a reason it degraded. Rows written before the level existed are a
bare `list[str]`; `run_service.hydrate_messages` derives their level from the
row's own status rather than guessing. ADR 0194.

`outcome` is whether there was an answer: `delivered`, `some`, `nothing_found`,
`nothing_to_do`, or NULL for a run that never reached one — every `failed` and
every `cancelled`. It is nullable and carries no CHECK constraint, matching
`status`; the vocabulary is held in `run_service.finish_run` instead, so a row
written by an older release still reads.

The pair is what a reader reads. `succeeded` + `nothing_found` is a lookup that
did its whole job and the answer was no. `succeeded_partial` + `nothing_found`
is one that could not reach a single page, with the warning saying why.
`succeeded_partial` + `delivered` is a nightly run that wrote its brief with two
feeds dead. One field could say none of those, and 44 of 68 lookups on the
defence corpus read `succeeded_partial` with an empty `warnings` array and a
null `error` as a result. ADR 0183.

`RunType.outcomes` gives each value that kind's own words, resolved per row as
`outcome_text`: `nothing_to_do` is "nothing new" on the daily and "already had
it" on an ingest.

`kind` is one of the values in `services/run_types.py`, which also decides the
lane a run executes in and how it renders (contract C11). The column has no
CHECK constraint and the `-- daily | on_demand` comment in migration 0001 has
been wrong since `lookup` shipped — read the registry, not the comment.

`status` includes `queued`: a run whose lane was full. It is in flight, so it
blocks its lane, cannot be hidden, and is reaped if its owner dies.

Liveness is NOT a column. A run holds a session-level Postgres advisory lock,
keyed on its id, claimed by `create_run` on the connection the run will use and
released by Postgres when that connection ends — by close, by SIGKILL, or by the
machine going away. So "is this run executing" is one question with a definite
answer, in `pg_locks`, and a run with no lock is provably not running. This is why
an unleased `running` row does not block its lane; keeping the old "unknown counts
as live" rule would wedge a lane on every crash.

`owner_pid` survives as pure diagnostics — which process, for a human reading a
row. Nothing decides anything from it. It used to be the mechanism, paired with
`owner_start_ticks` (field 22 of `/proc/<pid>/stat`) because a pid alone is not an
identity and in a container the next deploy's uvicorn is very likely to *be* the
pid the last one recorded. That worked on one host and could not work across two.
Migration 0004 drops the start ticks; migrations 0008 and 0020 carry the history;
contract C11.

A run reaped by `reap_interrupted` is `succeeded_partial` if any `run_stages`
row reached `ok`, and `failed` only if none did — the same rule a degraded run
follows, for the same reason.

### Measurement (migration 0023)

`run_metrics` is flat `(run_id, stage, key, value, unit)` and deliberately
schemaless: a stage names its own keys, so instrumenting stage seven is a
`Funnel` and no migration. `run_source_stats` is the one shaped table, because
its columns are the same question asked of every feed.

**`run_metrics` rows are UPSERTed, not appended.** Two reasons, and both are
load-bearing: a stage records incrementally while it runs, which is what makes
the live view possible; and `venona run --only analyze` re-enters a stage whose
rows already exist. An append-only metrics table would report the sum of every
attempt.

Key names are a convention, not a constraint. `offered` and `kept` are the ends
of a funnel, `drop_*` is a named reason one item did not survive, and
`reconciled` is `0` or `1` — the invariant `offered == kept + sum(drop_*)`,
recorded as DATA rather than asserted. A stage that fails it is losing items it
does not account for, which is a finding about that stage; the run still
succeeds, because instrumentation must never be able to fail a run.

`quota_samples` records what a service reported about its own subscription
windows. It carries a `harness` column and its unique index is
`(harness, window_name, resets_at, utilization) NULLS NOT DISTINCT` — without
the first column a switch would leave two services' windows in
`latest_per_window` forever, two rows marked representative, and the strip
drawing four bars of which two never move again. A service that reports no
window at all writes no rows, and the strip says that rather than promising the
first run will fill it in.

`llm_calls.stage` and `llm_calls.subject` are the same migration. The ledger has
carried `duration_ms`, the four token columns and cost per task since it
shipped and no endpoint read it; `subject` (a cluster id, an entity id, a
surface) is what makes "which item cost the most" answerable at all.

Freshness lives on `run_source_stats` and counts `fresh` / `stale` / `undated`
against the BRIEF's own `lookback_hours`, so "stale" means "could not have been
briefed" rather than an arbitrary age. `undated` is counted apart from `stale`
and is not a loss: retrieval keeps `published_at IS NULL`, so an undated feed
bypasses the freshness gate entirely.

---

## Exports

`data_exports` is one row per folder written out by an `export` run: `id`
(`export:` prefixed), `filename`, the absolute `path` under
`<data_dir>/exports/`, `bytes`, `sha256`, the `counts` blob the run's result
panel reads, and `status` ∈ `ready | pruned | missing`.

**It carries no foreign key to `runs`, and that is the design.** `runs` is in
`purge.CORPUS_TABLES` and this table is not, so a reference would either take an
operator's archive away with the run that made it (`CASCADE`) or refuse the
purge (`RESTRICT`), and both are wrong. `run_id` is kept as provenance, and the
listing never joins through it — after a purge every value in that column names
a row that is gone. Everything the screen renders is stored on this row rather
than derived, for the same reason.

`pruned` and `missing` are deliberately different. Retention keeps the five most
recent and marks the rest `pruned`, file first and row second, so a crash
between the two leaves an archive that the next export's orphan sweep collects.
`missing` is written when a download went looking and the file was not there,
which is what a hand-cleaned data directory looks like from inside the app.
Telling an operator that retention removed something it did not would be a
plausible lie, which is the worst kind.

The unique index on `filename` is what makes the same-day counter correct: two
exports on one date differ by a suffix, and the writer asks this table which
names are taken rather than the directory, which can hold an orphan.

---

## Identity and authority

`users` is the membership list — at the door as well as after it. One row per
person: `id` (uuid7, `user:` prefixed), `email` lowercased and unique, `role` ∈
`{admin, reader}`, `status` ∈ `{active, suspended}`, `session_epoch`,
`display_name`, and the OIDC identity (`oidc_issuer`, `oidc_subject`) stamped by
the sign-in callback. `last_seen_at` is written at sign-in, which is what makes
its ABSENCE mean "approved and never arrived" — the only signal an operator has
that somebody was let in and never told, because venona sends no mail.

**There is no delete.** The five actor columns below are bare
`REFERENCES users(id)` — `ON DELETE NO ACTION` — so Postgres refuses to remove
anyone who has decided anything and permits removing anyone who never used the
system. A delete feature built on that passes its own tests and fails on every
real case. Suspension is the answer: it ends sessions on the next request,
refuses every agent token the person owns, and keeps the row that says when.

**`session_epoch` is per user.** A logout-everywhere bumps one row rather than a
global stamp, so one person signing out on their phone does not end everybody
else's sessions. Agent tokens deliberately do NOT consult it (C12): one logout
would otherwise unplug every connected agent days later with nothing naming the
cause.

**`user_events` is the audit log** — one row per authority change, with
`actor_id` NULL when `venona users` made it, because shell access is not a
`users` row. It is what makes an HTTP surface for roles reviewable, and therefore
what justifies that surface existing. Written inside the same transaction and the
same advisory lock as the change itself, so a change without its event is not a
state the database can be in.

**`access_requests` is a funnel, never a grant.** The public form writes
`status = 'pending'` as a SQL literal unreachable from the request body;
approval is a different route reached with a different credential, takes a
REQUEST ID rather than an address, and its ceiling is `reader`. `decided_by` and
`user_id` are written only by that route — the second is what joins a request to
the account it became, and is NULL for every row decided before migration 0005.

**The actor columns, and which are live.** `alias_conflicts.resolved_by`,
`entity_mappings.merged_by`, `acronym_definitions.decided_by`,
`access_requests.decided_by` and `runs.started_by` are all written and all read.
`entity_definitions.decided_by` is written by nothing on purpose: no endpoint
settles an entity definition by a human verdict, and stamping an actor on a
web-grounded lookup would attribute a model's finding to whoever pressed the
button. NULL is never backfilled anywhere — see C1.

---

## The Neo4j projection

Holds nothing original. Entity nodes carry `:Entity` plus a type label, so one
uniqueness constraint covers everything and generic explorer queries stay
simple.

Community Edition has unique constraints only — node keys are Enterprise. The
design deliberately needs nothing beyond uniqueness.

Writes are batched `UNWIND` + `MERGE`, one query per label and per predicate.
`mention_count` and `evidence` are guarded by article-id set membership, so
re-running a day is a strict no-op on counters rather than inflating them.

Predicate direction and meaning: `docs/graph-schema.md` (generated from the
ontology). Domain and range are enforced in `graph/writer.py`.
