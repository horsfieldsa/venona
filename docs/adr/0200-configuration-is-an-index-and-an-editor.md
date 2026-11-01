---
status: accepted
date: 2026-09-03
---

# 2026-09-03 — Configuration is an index and an editor, the editor is CodeMirror, and saved versions are reachable

`/admin/config` was one route: a rail of seven `<details>` groups over twenty
documents, and a `<textarea>`. It is two now — `/admin/config` lists every
document, `/admin/config/:key` edits one, full width, with no rail at all.

**The rail's own comment was the argument for moving them.** It recorded that
the groups are collapsed by default because expanded they *are* the whole rail,
pushing Overview, People and Data below a fold. That is a rail admitting it is
the wrong container: a destination list cannot also be a library. The cost was
paid twice — two clicks to reach any document, through a group name from the
pipeline's vocabulary (`Prompts`, `Graph`, `Pipeline`), and no room for anything
about a document but a truncated title.

**Three fields were already on the wire and rendered nowhere**: `description` (a
real sentence per document, written years before anything could show it),
`updated_at`, and the group. The index exists mostly to spend them. Groups are
now a declared `GROUPS` table keyed by purpose — what you are trying to change,
not which stage consumes it — so order and a per-group blurb have somewhere to
live, and `lexicon` moved out of `Prompts`, where it never belonged.

**The row is a link, not a row with an Edit button.** A document is a
destination with exactly one action, so a separate control would be a second tab
stop to the same place and twenty more outlined rectangles on a page — which
DESIGN.md already records as what made `/runs` unreadable. As a link the row
also clears the 44px tap rung without `Button` having to, which it still does
not.

**The editor drops the rail rather than shrinking it.** ⌘K is what moves between
documents now, and that required a fix: `CommandPalette` returned no rows for an
empty query, so the documents only appeared once you typed. On `/admin/config/*`
they are the list. Everywhere else the single Configuration row still stands for
them, for the reason that cap was added — twenty rows would push entities and
briefs below the fold on every open.

**Rejected: a document switcher in the context bar.** It is the rail again,
smaller, and it would have to earn its width against the 29KB of YAML that is
the reason the rail went.

### The editor is CodeMirror, and the header folds rather than moving

0081 argued that "comment-preserving round-trip is a dependency and a class of
subtle bug; a textarea over a validated file is neither." CodeMirror **is** a
dependency — seven `@codemirror/*` and `@lezer/*` packages against a
`package.json` that had eight runtime entries. What makes it safe where a
round-trip serialiser is not is the property the whole of 0081 rests on: **it
never parses and reprints the document.** The buffer is the same string a
textarea held, byte for byte, comments and ordering intact. Measured, so the
weight is a fact rather than a hope: the entry chunk is unchanged at 66.8KB
gzipped and the editor lands wholly in `ConfigView`'s lazy chunk, which is
121.3KB. `lint_bundle.py` grew a prefix rule to keep it there.

**The file's own header is FOLDED, not lifted out of the buffer.** The first
design pulled the leading comment block into a disclosure above the editor. That
desynchronises every line number on screen by a different amount per document —
sixteen on `ontology.yaml`, twenty-one on `anchors.yaml`, none on the markdown
ones — while three things on that same screen count lines over the whole file:
the gutter, `ConfigEditor`'s "first difference at line N", and the `line N,
column M` PyYAML puts in a refused save. That last one is the screen where a
YAML error gets repaired. Folding leaves all three true and makes the fold
boundary a cosmetic mistake rather than a corrupting one. `configPreamble.ts`
stops at the first blank line, because `settings.yaml`'s second comment block is
the warning attached to `settings_version`, and folds nothing when the header is
most of the file, because a fresh instance's skeletons are eight lines long and
the setup wizard opens them first.

Syntax colour needed the entity families one rung darker on paper (`X-800`): the
slots owe 3:1, and text you read a file through owes 4.5:1 on `--surface-inset`,
where `X-700` lands at 4.06:1. CodeMirror's `dark` flag is deliberately not the
theme switch — it is resolved once at construction, and this palette is
`light-dark()` swapped at runtime with a System default, so every base rule that
carries a colour is repainted with a token instead.

### Saved versions are reachable

`.history/` has kept twenty snapshots per document since the editor shipped and
`GET /api/config/{key}/history` has never been called. Restoring meant finding
the file in a shell. The panel lists them and one click loads a version into the
editor; **Save is still the only write**, so a restore is validated, snapshotted
and conflict-checked like any other edit.

Three things the data forced. A row is a date and time, not "27 days ago": seven
snapshots of this instance's ontology were cut across two days and a relative
label renders them as one sentence seven times. It carries the content hash,
because two of them share a timestamp and three share a byte count. And it
carries a **delta against the current file**, not a size, because the newest
ontology snapshot is 9KB against a 29KB file and an absolute number hides that
restoring it drops two thirds of the document.

`restoreSnapshot` lives in `useConfigDocs` and not in the view, because
`activeBase` never leaves that module and the correctness of a restore is in
that one line: anchored to the version on disk, or the pane warns that saving
would overwrite a file the buffer has never seen — about a version the reader
just chose — and the watcher persists the snapshot as though it were typed. It
is a third origin for the buffer beside your own edits and a staged draft, so it
gets the branch ADR 0140's machine did not have, and a confirmation when
restoring would destroy either. `discardDraft` has no dialog on the argument
that a discarded buffer is one click to reproduce by retyping; unsaved work and
a run's generated draft are on the other side of that line.

### Two things found on the way through

`ConfigView` had its own `apiPost('/curation/rebuild')` beside the curation
store's `rebuild()`, and only the store's copy knows that a 409 of `rebuilding`
is a run's projection pass in flight — a wait, in the server's words — rather
than `rebuild failed` under a red banner. The view calls the store now, and the
"anchors applied, graph not rebuilt" flag moved there with it, because the
reader who needs it is the one who walked away to look at the graph.

The assembled prompts' group was a literal written twice, in `assembled_list`
and again in `assembled_doc`. A key that drifted in one of them would drop the
entire read-only block off the index without failing anything, so it is
`ASSEMBLED_GROUP` once, and `test_every_group_named_by_a_document_exists` pins
every group both tables emit.

**Consequence.** `config_service.GROUPS` is the source of headings, their order
and their blurbs; `EDITABLE[*]["group"]` is a key into it, and `ConfigList`
carries `groups` beside `items`. `EXTENDING.md`'s "Add an editable config
document" step 1 is the checklist. Adding a document still needs no frontend
change. `AdminRail` lists destinations only. The banner rule on the editor is
*does this block hold a control* — the staged-draft choice, the rebuild and the
switched-off sources all do and stay expanded; the ontology's rejected shapes do
not and fold with their count.
