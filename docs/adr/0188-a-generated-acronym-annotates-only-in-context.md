---
status: accepted
date: 2026-09-01
---

# 2026-09-01 — A generated acronym annotates only where the entity is named; a surface two entities claim is annotated for neither

Asking production, through the tool a reader's agent uses:

> The vendor published an **API** for developers, and the **ABC** network
> covered it. Engineers used the **ACE** framework and the **AIP** toolkit.

```
RECOGNISED (4)
  AUKUS Pillar II      prog:aukus-pillar-ii        ← "API"
  Adm. Brad Cooper     person:adm-brad-cooper      ← "ABC"
  Agile Combat Employment                          ← "ACE"
  AE Industrial Partners                           ← "AIP"
```

Every one of those is Venona asserting a defence reading of an ordinary word, on
somebody else's page, with a hover card and a citation behind it. The Chrome
extension splices exactly this into third-party articles.

## What was actually wrong

`resolve.py` and `seed.py` wrote every `acronym_variants()` form into `aliases`
at strictness 2 — the tier reserved for all-caps acronyms, which matches
globally and case-sensitively — gated on nothing but `variant.isupper() and
len >= 3`. 930 such aliases were live, 65 of them derived from people's names.
None had ever been attested: `evidence_article_id` is NULL on all of them,
because no article ever wrote them.

Three separate defects sat in that:

1. **Nobody abbreviates a person.** `Adm. Brad Cooper` yields `ABC`,
   `Bassam al-Hassan` yields `BAH` — which collided with Booz Allen Hamilton.
2. **`"U.S.".isupper()` is True**, so `_variants_from` read it as an
   already-acronym tail and pasted it in whole: `NU.S.`, `SCU.S.`, `IPCU.S.`,
   `MOSA("MOSA")`, `N[C2]`. 113 surfaces no publication prints, and they were
   listed on the entity's own hover card as names it goes by.
3. **A derived acronym was trusted like an attested one.** `API` really is an
   abbreviation of AUKUS Pillar II — and of five thousand other things. The
   letters alone decide nothing, and the index was treating them as decisive.

Separately, and found the same afternoon: 49 normalized surfaces were claimed by
two or more entities, and `ambiguous_surfaces` — the mechanism for exactly this
— knew about none of them, because it is written only by the seed's editorial
lists and by the glossary's two-expansions path. `select_aliases` has no
`ORDER BY` and `_resolve_overlaps` breaks an equal-strictness tie by insertion
order, so which of Army Contracting Command and Air Combat Command owned `ACC`
was heap order. A reader was given an answer that nothing had chosen.

## Decision

**A type declares whether it is abbreviated.** `config/ontology.yaml` carries
`generate_acronyms` per entity type, read through `get_ontology()` at the point
of use. `Person` sets it false. Domain-blind in code, editable per instance.

**A generated variant must look like an acronym as a publication prints it** —
`^[A-Z0-9]+$`, three or more. Filtered at the two WRITE sites, never inside
`acronym_variants`, which also serves as the tier-1 validation set: narrowing it
there would start rejecting real definitions.

**A generated acronym annotates only where the text names the entity some other
way.** `AliasEntry` and `Match` carry `needs_context` for
`added_by = 'acronym_gen'`; `alias_index.refs_present` computes, once per
document, the refs matched by everything that is NOT generated; `annotate_blocks`
drops a generated match whose ref is absent from that set. This is `pick_sense`'s
rule — *the expansion, spelled out, somewhere in the same text* — applied to
entity aliases, and it is stated there in the terms this decision rests on:
declining costs an annotation, guessing costs the reader's trust in every
annotation.

The set is computed by the CALLER and passed down, because four of
`annotate_blocks`' five callers hand it one block at a time. Derived inside, the
rule would silently narrow to "named in the same paragraph".

**A surface two entities both claim is annotated for neither.** `curate`
reconciles `ambiguous_surfaces` against `alias_index.entity_collisions` every
run, filing and releasing in both directions, and only ever touching rows it
filed itself. The surface then gets the treatment `DIA`, `ATLAS` and `TITAN`
already get: demoted to strictness 0, matching nowhere, visible in the queue.

Three details that are the whole correctness of it:

- Counted on the **resolved** ref, so a pair that has been merged is one entity.
- Counted on the **raw** strictness, because a surface this filed reads back as
  strictness 0 — dropping it would release the suppression, re-file it next run
  and flap forever.
- Counted **after** the merges in the same curate pass, and excluding
  `needs_context` claimants. Otherwise every proposed-but-unapplied duplicate
  would blank a real company's underlines, and `BAH` — Booz Allen Hamilton
  against a person's generated initials — would read as a genuine ambiguity
  rather than one entity and one artefact. That filter alone takes the live
  figure from 49 to 23.

## What this costs

A generated acronym no longer annotates a document that mentions the entity only
by that acronym. That is a real loss and it is the intended trade: the same
asymmetry the auto-merge rules are built on. A missed underline is invisible and
recoverable; a wrong one is asserted to the reader with a citation.

Deleting the rows was not enough on its own, and this is the trap worth
recording: the acronyms stage skips a sighted surface only while an entity alias
with `strictness > 0` claims it, so a deleted acronym is *re-minted as an
`undefined` glossary row on the next run* — which annotates at strictness 2 —
and starts competing for paid tier-3 web lookups. Migration 0020 is therefore
paired with a `stoplist:` entry. Measured on this corpus: of the 53 removed
person initials that no entity still claims, exactly two appear in any article.
`ABC` is a broadcaster and is stoplisted. `CSG` is Carrier Strike Group, a real
acronym a person's initials had been quietly sitting on, and it is now free to
ground.

## Alternatives rejected

**Demote generated acronyms to strictness 1.** Does nothing where it matters:
`annotate_service` passes every active entity as `tier_a`, so tiers 1 and 2
accept and reject identically on a third-party page. Strictness 0 — or not
writing the alias — is the only lever.

**A blocklist of common acronyms.** Catches only what somebody thought to list,
and the list is unbounded. Kept as the narrow instrument it is good for: one
stoplist entry, measured, rather than a policy.

**Present both readings of a contested surface to the reader.** Wanted, and not
buildable as it stands: the `ambiguous:` placeholder and `senses()` are both
keyed on the `ambiguous_surfaces` table, `ambiguous:<normalized>` fails
`id_pattern` because `normalize` emits spaces, no `.mention--ambiguous` style
exists, `cards_batch` dispatches only `gloss:` versus entity, and
`annotate.py` hardcodes `"glossary"` as the resolved kind — which would drop an
entity reading out of the C3 partition `POST /graph/brief` reads. Suppression
first; presentation is its own slice.
