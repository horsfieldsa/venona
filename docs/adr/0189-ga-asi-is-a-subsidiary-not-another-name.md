---
status: accepted
date: 2026-09-01
---

# 2026-09-01 — General Atomics Aeronautical Systems is a subsidiary, not another name for its parent (reverses part of 0174)

Two human statements about the same pair, pointing opposite ways.

`profile/anchors.yaml` declared both `GA-ASI` and `General Atomics Aeronautical
Systems` as aliases of `company:general-atomics`. A curator, working the live
queue, ruled the same pair `related` — `alias_conflicts` carries
`resolution = 'related'`, `kind = containment`, resolved 2026-08-18.

`fixtures/curation/decisions.yaml` sided with the declaration, and said so in as
many words: *"The deciding evidence is not in the names: a person declared
`General Atomics Aeronautical Systems` an alias of `company:general-atomics`, so
a second entity holding that name is a duplicate by their own statement."*

`scripts/graph_health.py` has a section for exactly this — *declared alias
CONTRADICTED by a recorded verdict* — and it reported the pair on every run,
which is how it surfaced.

## Decision

The curator is right. GA-ASI is a subsidiary with its own programs, its own
contracts and its own trade press — the same shape as
`company:lockheed-martin-rotary-and-mission-systems`, which
`decisions.yaml::lockheed-business-unit` already records as `relate` /
`PART_OF`. The alias declaration is the statement that was wrong.

- The two alias entries are withdrawn from `profile/anchors.yaml`, with the
  reason left in place of the lines.
- `decisions.yaml::general-atomics-declared-alias` becomes
  `general-atomics-subsidiary`: `filed_by: containment`, `verdict: relate`,
  `predicate: PART_OF`, `child: b`, `containment_veto: true`. `declared_alias`
  is removed — leaving it would plant the alias the fixture is meant to be free
  of, file a `claim|` row, and merge the pair the table now says to keep apart.
- Migration 0020 deletes both alias rows, because withdrawing a declaration
  removes nothing: anchor loading is upsert-only.

## Why the rule that got it wrong stays

`_classify` still returns `DUPLICATE` for any `claim|` row, and that is still
right. A declared alias is the only evidence that can join `Northcom` to
`U.S. Northern Command` — two names sharing no token, which no comparison of
strings could ever pair. The rule was not the defect; one declaration was.

What this does change is the standing of a declaration relative to a curator's
verdict on the same pair. The declaration is an input made once, often at seed
time, about a domain the writer was still learning. The verdict is a decision
made against real evidence, by the person the graph is for. **Where they
conflict, the verdict wins and the declaration is withdrawn** — withdrawn, not
overridden in place, so the two never disagree again.

## The cost, stated

Every mention of GA-ASI stops being evidence for General Atomics. The parent's
source counts drop, and a fact that read as corroborated across the two may now
read single-source on one of them. That is the correct reading: they are two
companies, and the merge had been manufacturing corroboration by adding a
subsidiary's coverage to its parent's.

`PART_OF` is the relationship that carries the connection, and the graph is
where a reader should see it.
