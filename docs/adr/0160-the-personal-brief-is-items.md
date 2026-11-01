---
status: accepted
superseded-in-part-by: 0176 (the no-sections half; the item machinery stands)
date: 2026-08-24
---

# 2026-08-24 — The personal brief is items: one guide, one renderer

The For You brief was one model call returning a markdown narrative, stored
as blocks and rendered flat. `profile/brief_prompt.md` rode its system prompt,
but every structural rule in that guide — the summary/why-it-matters split,
the top block, the follow-up states — was impossible under a
`{headline, dek, body_markdown}` contract. The reader got prose where the
shared brief gets an argued document, and the guide governed only half the
product.

Now the personal brief uses the shared item machinery. Its schema is the
shared item contract minus the section desk (the reader's ranking IS the
order, so there is no `section` field and code keeps the supplied order);
storage is `personal_brief_items` + role-tagged `personal_brief_blocks`;
assembly is `brief_service.assemble_body`, the same function the shared
archive uses — same undefined-to-glossary rewrite, same read-time headline
annotation, same top-block fill. The wire needed nothing: `ForYou.brief` was
already the shared `Brief` model with `top`, `freshness` and items defaulted,
and `BriefItem.vue` renders entirely off the item.

Decisions that survived adversarial review, and why:

- **`PERSONAL_BRIEF_FORMAT` is folded into the inputs hash.** Nothing else
  moves the hash when the format changes underneath a current document, so
  without it a format change strands every reader on the old shape with no
  stale banner and a generate endpoint that answers `already_current`.
- **The reader's "already briefed" diff excludes the date being written.**
  The day's document is replaced in place; without the exclusion a same-day
  regeneration reads its own soon-to-be-deleted items and marks the whole
  document `follow_up` against a predecessor that ceases to exist.
- **Validation precedes the delete in `_persist_personal`.** A refused or
  hallucinated response returns None with the stored document untouched.
- **No `mentioned_entity_ids` / `unresolved_mentions` in the personal
  schema.** A per-reader document must not feed the shared curation queue;
  annotation tier A comes from `active_entities_in_clusters` instead.
- **`personal_brief_items.brief_id` cascades**, unlike `brief_items`'
  deliberate no-FK: regeneration replaces the row and relies on it.

Rejected: a parallel item-assembly implementation in `personal_brief.py`. It
would duplicate the kind-rewrite and the annotation degrade and drift; the
`NULL AS section` alias in the personal item query makes the shared assembly
yield exactly one heading-less section, so extraction was the smaller risk.

**Consequence:** documents written before this render through
`assemble_body`'s legacy branch (narrative blocks, no items) — the archive is
never emptied by a format change. `brief_prompt.md` now governs both surfaces
verbatim and is still byte-pinned by the shared golden; the personal system
prompt carries only a no-sections adapter. C3's touch-list gains
`services/personal_brief.py` and the for-you routes.
