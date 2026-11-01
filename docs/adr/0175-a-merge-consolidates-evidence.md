---
status: accepted
date: 2026-08-27
---

# 2026-08-27 — A merge consolidates evidence, the two source counts agree, and the footer only promises what a limit can reveal

Four read-path defects found while investigating the entity splits of ADR 0174.
Together they mean the merge work delivers nothing a reader can see, so they land
first.

## A merge discarded the loser's publishers

`edge_provenance` matched `a.subject_id_raw = ? AND a.object_id_raw = ?`
literally. Assertions keep the ids they were written with — invariant 1 forbids
rewriting them — and `services/curate.py::merge_closure` exists as the read-side
counterpart, applied by `pipeline/brief.py`, `services/alias_index.py` and
`graph/projector.py` to the same column. This one call site did not use it.

So merging `company:palantir` into `company:palantir-technologies` collapsed the
node and made the loser's article unreachable. The split made a corroborated fact
read single-source; the merge did not cure it. **Both endpoints are now expanded
to their merge closures.**

## Two tools contradicted each other about the same edge

`entity_service.neighborhood` counted distinct ARTICLES.
`graph_service.edge_provenance` counted distinct site DOMAINS. Verified live, in
one turn, on `company:anduril-industries|PRODUCES|plat:yfq-44a`:

```
venona_relations   2 sources                       (no warning)
venona_evidence    2 article(s) across 1 domain    SINGLE-SOURCE, not corroborated
```

`graph/writer.py` and `graph/queries.py` both carry a written promise that these
count the same thing, naming the exact failure: "the canvas draws solid what this
panel calls single-source". **Reconciled on distinct publisher domain**, which is
the definition the product's corroboration claim rests on. `site_domain` now
rides along on `neighbourhood_assertions`, and ranking is by publishers first and
articles second, because one outlet running a story twice is not corroboration.

## The footer counted edges no limit could reveal

`neighborhood` froze `total` after the confidence filter, then rebuilt `edges`
dropping every one whose far endpoint was not `active`, and never recounted. So
`venona_relations` said "showing 3 of 5" for NGC2 and "7 of 11" for Anduril, and
no `limit` value produced the rest — an endpoint held back for being provisional
is absent at every page size, and the footer told the reader to retry.

**The endpoint filter now runs before the slice**, over every ranked edge rather
than the first page, so `total` means what the footer says it means. This was
also the reason the defect stayed invisible: provisional and split endpoints are
exactly what gets dropped.

## A merged id was a second-class citizen

`entity_service._entity_row` resolves mappings; the readers that follow it did
not. `api/mcp/tools.py` resolved the card and handed the RAW id to
`queries.shortest_paths`, so a loser id had a full card and no paths.
`api/mcp/render.py` compared the raw id against survivor-keyed edges, so every
edge of a merged entity rendered inbound and the org chart printed inverted.
**Both now use the resolved id**, and asking for a path between two ids that are
now one entity says so rather than answering "no path".

## And the agent-facing prefix hint was three prefixes out of date

`tools.py` told an agent that ids are prefixed
`(org:, prog:, tech:, plat:, person:, country:, gloss:)`. `org:` was retired in
ontology v3; `company:`, `govt:`, `unit:` and `research:` were missing. An agent
following it guesses `org:palantir` and concludes venona holds nothing — the one
answer a lookup tool must not invent. **Derived from `get_ontology()` at the
point of use**, as `db/models.py::id_pattern` does, because the type set is
per-domain (ADR 0139) and `docs/CONTRACTS.md` already says a frozen list is
wrong. The same stale list in `CLAUDE.md` and `web/src/lib/entityColour.ts` is
corrected.

## Held by

`tests/test_split_retrieval.py`, which asserts at the level a reader experiences
rather than the unit level. Four of its eight tests fail if the first three fixes
above are reverted, which was checked rather than assumed. It also states the
PRE-merge case explicitly — two split ids genuinely are single-source each, and
that is the correct answer for that data — so nobody later "fixes" the symptom by
loosening the counter.
