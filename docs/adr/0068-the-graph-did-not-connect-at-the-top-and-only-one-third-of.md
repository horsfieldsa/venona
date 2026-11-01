---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — The graph did not connect at the top, and only one third of that was schema


*2026-08-06.*

Reported: *"the nodes of our graph don't connect at the top level."* Correct, and
the obvious fix — a `United States` node with things hanging off it — was the
smallest of three causes. Measured before touching anything: **185 active
entities, 482 distinct relationship triples, 91 of them projecting (19%), 109
components, 90 isolated, largest component 30.** Hypothetically promoting every
endpoint gave **0 isolated and a largest component of 192**, which is the whole
argument: the spine was already in the assertion log.

**1. A merge never told the promotion gate.** `services/curate.py::merge_entities`
wrote the mapping and stopped. `promote_provisionals` was called only from
`pipeline/curate.py` and `analyze.py`, so a merge made from the CLI or the
curation API consolidated evidence and told nobody — even though consolidating
evidence is the entire point of a merge, and the gate aggregates over
`entity_mappings`. Fourteen entities sat `provisional` while already clearing
the 2-article/2-publisher gate, `org:palantir-technologies` among them at 7
articles from 7 publishers, blocking six relationships.

The gate now re-runs after a merge, **scoped to the survivor and
promotion-only** (`only=` and `promote_only=` on `promote_provisionals`). A
merge changes the evidence of exactly one entity and can only add to it; running
the full gate as a side effect would demote entities the merge said nothing
about, and a merge that silently demotes is the invisible kind of damage.

**2. The top-level nodes existed, duplicated and unlinked.** The Defense
Department was four active entities — `org:dod` ("DOD"), `org:defense-department`,
`org:department-of-war` (the 2025 rename), `org:pentagon` (the metonym) —
holding two relationships between them. `fixtures/seed/` had the intended spine
and had never been loaded: `SELECT COUNT(*) FROM assertions WHERE method='seed'`
returned **0**. It cannot be loaded either, for two independent reasons: it
writes a fictional brief and four invented documents into the system of record,
and its ids predate the corpus (`org:us-army` versus the live `org:u-s-army`),
so `make seed` would fork every service rather than attach it.

Hence `profile/anchors.yaml` — user-editable per the documented layout, under
its own `run:anchors-0001`, loading structure without the demo fixture. While
wiring it: the `part_of:`/`managed_by:`/`produced_by:` keys on ~20 seed entities
were **dead config read by nothing**, so a declared parent produced an orphan
node. They now emit edges, through `_load_edges` so there is one insert and one
validation site.

**3. There was no country concept, and that one was real.** `org:china` and
`org:taiwan` were active Organizations; `org:united-states`, `org:ukraine`,
`org:united-kingdom`, `org:poland` and `org:iran` were provisional ones. So
domain/range checking was off for what is now the highest-degree class: `China
COMPETES_WITH Anduril` and `Anduril ACQUIRED China` were both well-typed and
unrejectable. Country has the stable canonical surface `Contract` and `Event`
lack, so the v1 deferral reasoning argues *for* admitting it.

`BASED_IN` is a separate predicate rather than an extension of `PART_OF`'s
range, because `PART_OF` is the recursive org chart the path queries walk and a
country inside it turns every route between two US organisations into a two-hop
trip through a supernode. Separate means filterable.

**Result: 109 → 70 components, 90 → 60 isolated, largest 30 → 120.** Roughly
half of that came from Phase 1, which changed no schema at all.

### What is still blocked, and why the number looks worse than the work

372 triples still do not project because an endpoint is uncorroborated. No
anchor fixes that; `venona research` and the containment queue do. Anyone
reading the component count in isolation will conclude the anchoring
underdelivered — it did not, it is bounded by a different problem.

### The transitive lift that had to be thrown away

`anchors propose` originally lifted any two-hop path to a proposal: if X relates
to Y and Y relates to an anchor, propose X → anchor. Its first three live
outputs were `General Atomics PART_OF Northrop Grumman` (competitors),
`Collaborative Combat Aircraft PART_OF RTX` (a program is not part of a company)
and `QinetiQ PART_OF Leonardo`. Relations are not transitive; **`PART_OF` is,
and it is the only one here that composes.** Narrowed to `PART_OF` chains it
finds far less and everything it finds is sound. A plausible wrong proposal is
worse than none, because a curator has to stop and think about it.

### One prompt says the opposite of the others, deliberately

`ANCHOR_SYSTEM` tells the model to use outside knowledge, where every other
prompt forbids it. Invariant 3 governs **definitions** — an acronym expansion
must be traceable to a sentence. A structural edge is a different claim: no
article states that Nvidia is an American company, and the `relate` endpoint
already writes a quote naming the decision rather than a sentence. The safety
here is the keystroke, not the sentence: proposals land in `alias_conflicts` and
reach the graph only when a person presses `R`. On the first live run the model
proposed 11 attachments from 60 candidates and declined the rest, which is the
calibration the prompt asks for.
