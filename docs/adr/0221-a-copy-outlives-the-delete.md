---
status: accepted
date: 2026-09-10
---

# 2026-09-10 — A copy outlives the delete

`/admin/data` counted what was stored and deleted it, and both the screen and
the guide said so in the same breath: *"nothing brings it back and there is no
export first."* That sentence was true and it was also a gap, stated as a
feature. An operator who wanted what Venona had learned somewhere other than
Venona had no way to get it.

**Admin, Data now writes the whole reading out as an Obsidian vault** — a
folder of Markdown, one note per name, with the relationships between them as
links. Obsidian rather than CSV or JSON because the corpus is a graph and a
wikilink is a graph edge that survives leaving the building: the folder opens
with a graph view, coloured the way this app colours it, and every brief reads
as it does here with its programmes and short forms followable.

## What is actually being decided

Not "we added an export". Two things.

**`Delete all data` stops being absolute.** `purge_corpus` iterates an
allowlist, so `data_exports` is untouched by construction, and the archives sit
on the data disk after the corpus is gone. The guide and the runbook both
asserted the opposite as a safety property and both are corrected in the same
commit. The trade is deliberate: a delete that cannot be prepared for is not
safer, it is just less useful, and the operator asking for the delete is the
one who has to be able to keep a copy.

The corollary is that the purge now refuses while an export is being written —
otherwise the file is a complete copy of what was just deleted, produced from a
screen where the two buttons are a centimetre apart. The refusal asks for the
KIND and not the lane: `active_run_id(LIGHT)` answers for a definition lookup
too, and refusing a purge because a reader hovered a term is the single-global-
slot damage C11 exists to end, one level down.

**Retention is a rule, not a setting.** Five archives, a module constant. The
reason to keep more than one is to compare two over time; the reason not to keep
all of them is that the same disk carries `config/`, `profile/`, the model cache
and both CLIs' homes. A row survives its file and says `pruned`, because "there
was one and it is gone" is a different fact from "there was never one" and only
the first teaches the rule.

## What the folder carries, and what it will not

An article's headline, publisher, date, address and a capped opening extract,
plus the verbatim sentences that back a definition or a relationship. **Never
`documents.text_nfc`.**

The cap is measured rather than chosen. The median stored extract on the live
corpus is 280 characters and is an ordinary opening quotation — but at the
ninetieth percentile it is the whole article, and the longest was 6,634
characters, because the extractor keeps everything when a page is short or oddly
marked up. Carrying that verbatim would make the archive a redistributable copy
of five publishers' journalism, which is a different product from the one
anybody agreed to by pressing a button. `EXTRACT_LIMIT` is 320. The sentences
that ground a claim are uncapped and separate: they are single sentences, and
invariant 3 is worth more than the bytes.

`tests/test_export_vault.py` asserts this against the stored body text rather
than against the SELECT, so it survives any refactor of how the text got there.

## Two mechanisms that carry the weight

**One pass to name, then one pass to write.** A wikilink resolves by name across
the whole folder, so nothing can emit a link until every note knows what it is
called. `NoteNames` is the only place a link is built and answers `None` for
anything that got no note, and the caller writes plain words instead. That is
what makes a dead link structurally impossible rather than merely unlikely — and
it matters more here than the phrase suggests, because an unresolved link in
Obsidian is not a missing file, it is a **phantom node in the graph view**. A
folder full of them would misrepresent the corpus in the exact surface the
export exists to produce. On the live corpus: 2,747 links, none dead.

**The read is on a second connection.** The export runs a dozen whole-table
reads and holds `db.snapshot` — a new `REPEATABLE READ READ ONLY` transaction in
`db/core.py` — across all of them, because at READ COMMITTED a research run
committing between the names read and the relationships read yields a link to a
note the same folder declined to write. It cannot be the run's own connection:
`run_service.append_event` opens a transaction there, `db.transaction` refuses
to nest, and a snapshot on that connection would turn every progress event, and
then the stage row and the commit after it, into a `RuntimeError`.

## A short form is not a footnote

The first draft put an acronym in the note's frontmatter and nowhere else, so a
note headed `Australian Defence Force` said `ADF` nowhere a person reads. The
one line that could have carried it rendered the LONG form, which for a name
already spelled out IS the title, so the line suppressed itself. Both
directions are now written under the heading, and 210 of 450 notes carry one.

Worse than the omission: a name with no definition of its own can still have a
SOURCED short form, and 26 on the live corpus do. The note said only that
nothing defines this while the database held the sentence saying where the
acronym came from. Every word of that was true and the impression was false, so
the evidence is shown.

A folded short form is also written into the note's `aliases`, which is what
makes typing `[[ADF]]` resolve. The glossary and the alias table are separate
records and one does not imply the other; on the live corpus every attached
acronym happened to have an alias row as well, which is luck, and a test now
holds it rather than the luck.

## Being told, when you started it and walked away

The run takes minutes and is started by hand, which is the exact shape of thing
somebody presses and then goes to do something else. It is announced twice over,
because the two reach different places:

- **A toast**, pushed by the screen on start and on finish. `stores/runs.ts`
  toasts on completion only for the run its single event stream is attached to,
  and this screen deliberately does not take that stream -- there is one per tab
  and claiming it would detach the operator from a research run they were
  watching. So the finish is announced here, with words from
  `lib/runOutcome.ts::verdictFor`, which stays the only place a run becomes a
  phrase.
- **A notice**, `export.finished`, which reaches wherever the operator said to
  reach them. ONE type for both endings, against the grain of `sources.disabled`
  and `corpus.quiet` being two: those are fixed in two different places and an
  operator may want one and not the other, whereas "did my copy work" has one
  answer and one place to go and look.

Because this kind reports its own failure, a generic per-run failure notice must
exclude it or an operator whose disk filled gets two messages, the less useful
of which is the generic one.

**`default_on` alone would not have reached anybody.** It is read at exactly one
moment, when a destination is created, and fan-out asks for a
`notify_subscriptions` row where absence means no. A type added after an
operator wired up their channel is therefore silent there forever, and the
instance that fails on is the one that has been running longest. Migration 0026
backfills a subscription for every active instance destination. The general
problem outlives this change: any notice type added later has it too.

## Consequences worth stating

- The archive is byte-stable, so a golden test means something: fixed member
  timestamps at the zip epoch, pinned compression, byte-order sorting, and no
  wall clock anywhere except the date, which comes from `ctx.clock`. `export/`
  is on `lint_determinism`'s guarded list for exactly this.
- `export` is the first run kind declaring `uses_model=False`, and the first
  with an empty `summary`. Both surfaces filter zero-valued counters, so the
  empty summary renders as nothing rather than as thirteen zeroes.
- The download is the first file response this application has served. It is on
  `test_route_contracts.py`'s exempt list with its reason, and the row it serves
  is modelled instead.
- **`data/` is no longer disposable.** `data/exports/` is the only thing under
  it that cannot be rebuilt from something else. `make reset` does not know that.

## What was not built

No per-export delete: retention is automatic, and a second irreversible control
on the one screen that already destroys the corpus is a hazard nobody asked for.
No options dialog: one button, everything, so two archives are comparable.
