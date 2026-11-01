---
status: accepted
date: 2026-08-08
---

# 2026-08-08 — Some surfaces are not acronyms, and the queue has to be able to say so


The definitions queue could offer five verdicts — accept, reject, edit, look
again, choose a meaning — and every one of them assumes the answer is a
*different definition*. For three surfaces in this corpus there is no right
definition:

| Surface | What it is | What was missing |
|---|---|---|
| `BAE` | the company **BAE Systems** | no way to say "this is an entity" |
| `III` | a Roman numeral, *"battleship Alexander III"* | no way to say "this is not an acronym" |
| `VENOM` | already correctly defined **in the corpus** | a bug in `acronym_variants` |

Three fixes, one per cause.

**"It's an entity"** (`POST /glossary/{surface}/attach-entity`) is the first
user-facing alias write in the codebase — until now the only way to attach a
surface to an entity was editing `profile/anchors.yaml` and running a CLI
command, to answer a question the curation screen puts in front of you.
`added_by='manual'` had been in the schema's enum and honoured by
`default_strictness` since migration 0001, and written by nothing.

Two non-obvious parts:

- **It promotes.** `_resolved_entity_aliases` admits only `active`/`merged`
  entities, so attaching to a provisional one changes nothing visible.
  `BAE Systems` is provisional here while two of its subsidiaries are active —
  which is exactly why `BAE` never resolved.
- **The promotion must outlast the next run.** `promote_provisionals` re-gates
  every provisional/active/dormant entity on each analyze run and *writes* the
  verdict; `BAE Systems` has one asserting article against a gate of two, so a
  bare status write is reverted and the surface silently stops annotating. It
  already spared seeded entities — *"active by declaration, not by evidence"* —
  and a manual alias is the same kind of declaration.

**"Not an acronym"** stoplists the surface *and* rejects the row. Neither half
is sufficient: the stoplist stops `detect()` ever counting the token again,
while for a row with no expansion the rejection alone is not even durable
(`mark_undefined`'s grounded test requires `expansion IS NOT NULL`). It edits
`lexicon.yaml` as **text**, never a YAML round-trip — that file is a document a
person wrote and annotated, and `safe_dump` would delete every comment in it.

**The `acronym_variants` bug** is the one worth remembering. `_all_words` splits
on `[\s\-/]+`, so a hyphenated compound always contributes two initials:
`Viper Experimentation and Next-generation Operations Model` generated `VENGOM`
and `VEANGOM`, never `VENOM`. Tier 1 therefore rejected a textbook parenthetical
sitting in a document we already held, the surface fell through to tier 3, and a
paid lookup returned a 2015 security vulnerability of the same name. A character
class cost a wrong definition and the money to buy it. The third word list keeps
compounds whole.
