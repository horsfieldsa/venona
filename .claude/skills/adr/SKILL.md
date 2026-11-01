---
name: adr
description: Create, supersede, or look up decision records in docs/adr/. Use when a design decision is made or reversed, when asked "why is X this way", or before changing something that looks wrong (check for the record first).
---

# Decision records

`docs/adr/NNNN-slug.md`, numbered in the order decisions are made, indexed in
`docs/adr/INDEX.md`. Records 0001–0136 were migrated from the old
`DECISIONS.md`; the voice and bar are theirs.

## Before "fixing" anything that looks wrong

Search `docs/adr/` first (`grep -ril <topic> docs/adr/`). Several records are
measured, not reasoned — restoring a superseded design that looks correct in
isolation is the exact failure this system exists to prevent.

## Creating a record

1. Number = highest existing + 1. Slug: short kebab from the title.
2. Frontmatter:

   ```
   ---
   status: accepted        # or proposed, while still under discussion
   date: YYYY-MM-DD
   ---
   ```

3. H1: `# YYYY-MM-DD — <title>`. The title states the decision, not the topic
   ("The hub pulls, through a vmgmt_-scoped surface", not "Management
   channel").
4. Body, in the house voice:
   - The decision and the **reason** — enough that nobody re-litigates it.
     If it was measured, show the measurement; mark inference as inference.
   - The rejected alternative and why it lost, when one was seriously held.
   - **Consequence:** what this commits the codebase to; name files/contracts.
   - Short. A record needing many pages is documentation and belongs in
     `docs/`, with the record keeping the decision and a pointer.
5. Add the INDEX.md row: `- [NNNN](file.md) · date — title`.
6. If the decision was made inside an epic, link the record from the epic file.

## Superseding

Records are never edited to say something new. Write a new record; set the old
one's frontmatter to `status: superseded` and add `superseded-by: NNNN`; the
new record carries `supersedes: NNNN` and says what changed and why. The old
text stays — it is the history of why the earlier answer was believed.
