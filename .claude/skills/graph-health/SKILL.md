---
name: graph-health
description: Audit the corpus, the curation queue, the merge rules and the runs for defects that fail silently, and judge whether recent runs found the RIGHT things. Use for "is the graph healthy", "why does this read single-source", "audit the corpus", "are the runs any good", "did the last few runs find anything worth having", "what should curation be deciding for us", before widening any auto-merge rule, and after any change to resolution, merging, scoring or the read path.
---

# Graph health

Nothing here is about a stage that errored. Runs report `succeeded`, tests pass,
and every counter agrees with itself while a reader is being told a corroborated
fact is uncorroborated. This skill is for the defects that do not raise.

## The method that finds them

The four defects behind ADRs 0174 and 0175 were all found the same way, and it
is worth doing deliberately rather than waiting to trip over them.

**1. Ask the corpus a real question through the reader's surface.** Not a query
— a question a person would ask, answered with the tools a person uses. "What is
NGC2 and how does it relate to Palantir" is what surfaced one company held as two
entities. A synthetic probe would have passed.

**2. Then ask the same thing a second way, and check the two agree.** This is the
highest-yield move in the whole file. Any two surfaces that describe the same
fact are a free assertion:

| ask | and also ask | they must agree on |
|---|---|---|
| `venona_relations` | `venona_evidence` on the edge_id it prints | the source count, and the SINGLE-SOURCE flag |
| a card's neighbour count | the graph explorer around that node | how many edges exist |
| `showing N of M` | the same call at a higher `limit` | M being reachable |
| the brief's entity rail | `venona_entity` on each id | one entity per thing |
| `venona entity merges` | the cards for both sides | the survivor being the one on screen |
| a brief's items | the clusters `brief_clusters` holds for it | every retrieved cluster being accounted for. These are NOT the same set and are not meant to be: `brief_clusters` is what retrieval consumed, `brief_items` is what got written. The gap is the finding, not the divergence |
| the hook table in `effectiveness` | the instance's relevance profile | every passage that states what matters appearing in both |

A divergence is never cosmetic. Two counters disagreeing means one of them is
lying to a reader right now, and the one you happen to be looking at may be the
honest one.

**3. Ask what a count is promising.** `total`, `neighbor_count`, `source_count`
and "showing N of M" each promise something a query can return. When the number
is computed before a filter, it promises rows nothing can produce — and the
reader is told to retry with a bigger limit that changes nothing. Whenever you
see an aggregate near a filter, check which ran first.

**4. Ask who would ever notice.** A wrong merge is invisible until someone reads
the graph. A missed merge sits in a queue. A split entity looks exactly like two
real entities. If a defect has no reader who would complain, it needs a check
here, because it will not be reported.

## The report

```
make graph-health                            # all sections
make graph-health SECTIONS="splits merges"   # some
make graph-health SECTIONS="effectiveness --deep"   # a wider sample to read
```

Read-only, no model spend. Sections: `splits`, `queue`, `merges`,
`corroboration`, `candidates`, `dangling`, `runs`, `effectiveness`.

**It reads the database in `VENONA_DATABASE_URL`**, which is the default
instance's unless you export another (`set -a && . local/<slug>.env && set +a`).
Read the host it names in its first line before reading anything else.

**Audit the instance that has readers.** Every finding in this skill is a
statement about what a reader is being told. A scratch copy's splits are
whatever a seed run last produced and its queue is whatever nobody has worked,
so a clean report there says nothing about the corpus someone reads. Step 2
cross-checks against `api/mcp/`, so point those tools at the same instance.

**The vocabulary travels with the corpus.** `corporate_form` and
`generic_tails` decide which splits the report even shows, and the editable
documents are rows (ADR 0234) that `materialize` writes to disk at startup — so
`profile/lexicon.yaml` in this checkout belongs to whichever instance last
started here. `graph_health.py` reads the `lexicon` row off the connection it
was given for exactly that reason. If you ever point it somewhere by hand,
point the words at the same place as the entities.

Its queries live in `db/repositories/diagnostics.py` — `scripts/` is SQL-free and
`make lint-repositories` enforces that.

### Reading it

**splits** — one thing held as two ids, which halves its own evidence because
source counts are per id. Three shapes, and they want different answers:

- *same name, same type* — a duplicate. `curate` merges the provable ones on the
  next run; anything left here is a judgement call.
- *same name, different types* — never an automatic merge, but **read the
  evidence before deciding anything**, because one label sits over three
  different questions and the counts do not separate them. Measured on the live
  corpus, the ontological reading was the MINORITY case:

  The queue now separates the first two for you: a pair the ontology calls
  `confusable_with` is filed as **`type_conflict`** (one thing, pick the kind);
  every other pair is **`cross_type`** (two things, keep apart). Read the
  evidence anyway on anything surprising — the split is only as good as the
  ontology's declarations, and an empty `confusable_with` is a claim about the
  domain that may need revising.

  1. **Collision.** Two unrelated things that share a name. `Havoc` is a company
     "leader in all-domain collaborative autonomy" and, separately, "a new
     hypersonic missile system named HAVOC" from another firm. `SAFE` is the
     EU's €150bn loan instrument and, separately, "Sedaro's SAFE edge safety
     framework". Keep apart, and put the surface on the ambiguous list so the
     resolver stops trying — the treatment `DIA` gets. Filed as `cross_type`.
  2. **Typing inconsistency.** One thing the extractor typed two ways in two
     articles. `GE F414` is one engine, read as Platform once and Technology
     once. `Air Force Research Laboratory` is one lab, GovernmentBody once and
     ResearchOrg once. These ARE duplicates, and the same-type guard refuses
     them for a good reason: the rule that would merge these would also merge
     Havoc. Filed as `type_conflict`, and the verdict is to NAME THE KIND: both
     readings fold into one entity of that kind, minting it when the right
     answer is a kind neither side holds. Where one side is seed-declared this
     resolves itself as `auto: declared type`.
  3. **Genuine program and platform.** The case the ontology draws the line
     for. Rarer than it sounds, and worth checking against the seed: where one
     side carries `Seed declaration for …`, a human already stated what type
     the thing is, and the other side is usually a mint that read it
     differently rather than a second real thing.

  The tell is the shape of the pair, not its types: **one active or
  seed-declared entity beside a single-mention provisional** is class 2 or 3;
  **two entities each with their own distinct evidence** is class 1. Read one
  quote from each side and it resolves in seconds.
- *name is another entity's declared alias* — the strongest signal, and the one
  no name comparison can see. A person wrote that the surface names the other
  entity, so a second entity holding it is a duplicate by their own statement.

**queue** — a queue that only grows is a queue nobody works. A queue full of ONE
shape is a rule waiting to be written: that is exactly what fourteen
`containment` rows were before the corporate-form and generic-tail lexicons
existed. Look at the shape before the size.

**merges** — the only check on an auto-merge rule that exists. Every automatic
merge records its rule in `entity_mappings.reason` (`auto: same name`,
`auto: corporate form`, `auto: model`, …) and `unmerge` deactivates rather than
deletes, so the undo rate per cohort is answerable. A cohort a curator keeps
undoing is a rule that is wrong. **A rate of zero means either the rule is right
or nobody has looked** — read `venona entity merges` to tell those apart, because
the report cannot.

**corroboration** — the share of edges resting on one publisher. This is a
property of the corpus, not a bug: most facts are reported once. Watch the
direction it moves, and watch the edges carrying several ARTICLES from a single
outlet, which are the ones most likely to read as corroborated to someone
skimming the wrong number.

**dangling** — assertions naming an entity that no longer exists. Counted by
aggregates, drawn by nothing. This is the shape of every "showing N of M" defect.

**runs** — `succeeded_partial` is not a failure but is never nothing: something
was skipped and said so. Repeated `interrupted (process died)` means a `.py` file
was written while a run was in flight, which is the hazard CLAUDE.md opens with.

**effectiveness** — whether the runs found the RIGHT things, which is a
different question from every other section here and the only one no query can
answer. A daily that chose the wrong three stories, minted a dozen names that
are not things and read a feed that has been silent for a month reports
`succeeded` / `delivered` every morning. So this section rules on nothing: it
assembles a sample with its evidence attached, and a person decides. Five
blocks, and the order is deliberate.

- *what each brief retrieved, and what it went on to tell* — **the one to read
  first, because it is about what nobody saw.** A brief SPENDS every cluster it
  retrieves: `brief_clusters` is written before the model chooses anything, and
  every later brief excludes those ids. A cluster retrieved and not written
  about is therefore not waiting for tomorrow, it is gone. Equal counts are the
  healthy state and the gap is the finding; `venona briefs delete` is the only
  thing that gives one back.
- *the profile phrases that actually moved a score* — `profile_hooks` holds the
  phrases a score cited, verified against the profile before storage. Read
  downward it is mildly interesting. Read the other way it is the most
  actionable thing in this skill: **a passage of the profile that has never
  been cited is a sentence the operator believes is steering their brief and
  that is not.** Scoped to clusters analysed since 2026-08-27, when
  `verified_hooks` landed; before that the column holds unchecked model output
  and counting it would present a fabricated phrase as evidence. Headings are
  filtered out, because nothing can cite one.
- *names these runs brought into existence* — a mint, with the sentence that
  named it. Read the surface inside its own quote and say whether it is a
  thing. Fewest sightings first: those are both the likeliest junk and the rows
  `curate` holds provisional indefinitely, which on the live corpus is
  thousands nobody has ever read.
- *relationships these runs asserted* — the quote either supports the
  relationship or it does not, and only reading decides. Grouped by predicate,
  because one bad row is bad luck and four under one predicate is an ontology
  question. Three distinct verdicts, and they want opposite treatment: a wrong
  edge should be retracted, a **right edge resting on the wrong sentence**
  should be re-evidenced, and retracting one of those throws away a fact.
- *where these runs looked* — a run cannot find the right things in a place it
  is not reading. Rows exist only for runs that ran `fetch`, so a rewrite shows
  nothing and says so. A feed offering zero across every run in the sample is
  the thing to chase.

### The protocol

The report picks the sample. You do not, or you will pick the rows that look
wrong and learn nothing.

1. **Read all of it before ruling on any of it.** The default sample is sized
   to be finishable in one sitting for exactly this reason. `--deep` is for an
   audit, not for a weekly pass, and a sample nobody finishes is worse than
   none: it produces verdicts with a reader's confidence behind them.
2. **Judge what the runs did not surface first.** What got published is visible
   to anyone; what was consumed and dropped is visible only here.
3. **Record every verdict**, including the ones where the machine was right.
   A name ruled `real` stops occupying the top of a sample that is ordered by
   fewest sightings, which for a minor supplier is otherwise forever.

## Recording what you find

A finding is worth a row in **`fixtures/curation/decisions.yaml`** the moment you
have made a judgement about a specific pair. That file is the source of truth for
identity decisions, asserted against `_classify`, `identical_pairs` and the
resolver's containment veto at once, so one row keeps three consumers honest.
Every row carries a written `reason` and the loader refuses one without it —
these are judgement calls about real organisations and a future reader has to be
able to audit them without re-deriving the domain knowledge.

Add a row when the answer is clear and the code gets it wrong. That is the
difference between a corpus that is wrong and one that is wrong and knows it.

**An editorial verdict goes in `fixtures/curation/editorial.yaml` instead**, and
that file does a different job. `decisions.yaml` is asserted against three
deterministic consumers; nothing deterministic decides whether a quote supports
a relationship, so this one's job is MEMORY. The `effectiveness` section reads
it and keeps a ruled row out of the next sample, which is what stops the report
re-raising settled work and teaching you to skim it. Every row still carries a
written `reason`, and `check` names the deterministic consequence where there
is one (`retracted`, `ambiguous`, `merged`, `none`).

**Where the rest of a verdict goes, and this is the part that is easy to get
wrong.** Three of the obvious destinations are editable config DOCUMENTS, which
are rows -- the same fact this file already states about `profile/lexicon.yaml`.
Editing the file in this checkout changes nothing on the instance you just
audited and may be overwritten at its next start.

| verdict | where the change goes |
|---|---|
| a story should have been told | the relevance profile, through the instance's Admin rail or `PUT /api/config/relevance_profile`. The hook table names the passage to edit |
| a passage of the profile cites nothing | the same document: it is not saying what it thinks it says |
| a minted name is not a thing | `ambiguous_surfaces` on the instance, or a `decisions.yaml` row where it is really an identity call |
| a relationship is unsupported | retract it; if a predicate keeps producing them, the ontology document |
| a source has gone dark | the sources document, not `config/sources.yaml` in this checkout |

## Before widening an auto-merge rule

The asymmetry is the whole design and it is easy to erode:

> A wrong merge is invisible and poisons every downstream query. A missed merge
> lands in a queue.

So a new class is eligible only when the difference between two names is fully
explained by a token list a person wrote, or by a model verdict a structural
signal corroborates. Never by a similarity score.

1. Add the case to `fixtures/curation/decisions.yaml` first, with its reason.
2. Add the token to `profile/lexicon.yaml` — **never to `non_distinguishing`**,
   which the RESOLVER reads. A token there makes it accept a fuzzy containment
   match silently: alias written, surface taken, nothing filed, nobody shown.
   `corporate_form` and `generic_tails` are read by the curator instead, which is
   the layer allowed to be clever.
3. Run `scripts/graph_health.py splits` and `venona conflicts --kind duplicate`
   and READ the pairs the rule would now merge. Not the count.
4. After it has run, check the undo rate on its cohort.

Anything that reverses "merges are proposals the user approves" needs its own
ADR with a stated false-merge budget and a reversal trigger. ADR 0174 is the
worked example.

## When the instance is not the defense one

Everything above is per-instance, and two of the checks are only as good as a
list somebody wrote for THAT domain. The rule from "The report" holds here too:
audit the instance that has readers, and point `VENONA_DATABASE_URL` at that
instance's database rather than reading a copy of its domain.

`fixtures/curation/editorial.yaml` is per instance in the same way and for the
same reason: its rows name cluster, entity and assertion ids from one corpus, so
against another they simply never match. That is the right failure. A verdict
going inert is better than a verdict ruling on something it has not seen.

Before trusting this skill on a new instance, confirm its lexicons are
populated. The report prints what it used, but to ask directly, ask the instance
rather than the checkout — the document is a row and the Admin rail edits it, so
a file here can be months behind:

```
curl -fsS $VENONA_INSTANCE_URL/api/config/lexicon -H "Authorization: Bearer $VENONA_TOKEN"
```

Empty lists are the safe default and the correct starting state, but they mean
the corporate-form and generic-tail classes never fire. Exact-name duplicates
and declared-alias claims still work, because those need no vocabulary. So a new
domain starts with the two detectors that are domain-blind, and earns the other
two as the queue tells it which words keep coming up.

**These words invert between domains, which is why none of them is in code.**
The defense instance omits `systems` and `services` because a company there is
actually named with them; a domain whose firms carry those words as pure form
would include them and omit others. Read the queue before adding a token, add
one at a time, and check the undo rate on its cohort afterwards.

Three places have to agree when the vocabulary is domain-specific, and it is
worth checking all three on a new instance:

| file | holds | reached by |
|---|---|---|
| `config/domain.yaml` | domain identity, `common_name_words` | `get_domain()`, closed key set, refuses unknown keys |
| `profile/lexicon.yaml` | `non_distinguishing`, `corporate_form`, `generic_tails` | the resolver and the curation classifier; editable in the Admin rail |
| `fixtures/skeleton/lexicon.yaml` | the same keys, empty | what a new instance starts from when the wizard's draft does not land |

`fixtures/exemplars/lexicon.yaml` is the fourth: the setup wizard shows it to the
model as a worked example of a MATURE instance. Its comments are the part that
transfers, not its words. If you find yourself copying its tokens into a new
domain, that is the mistake the prompt warns against.

**`make lint-domain` is the guard, and it is worth provoking deliberately.** Any
domain term in a code string literal fails it, including inside a prompt. If you
are writing guidance about naming conventions and it passes only because you
avoided giving an example, the example belonged in `config/` or `profile/`
anyway.

**Relevance is not affected by any of this**, and that is worth knowing rather
than rediscovering: scoring is per CLUSTER against the profile rubric
(`services/rescore.py`, `services/personal_relevance.py` — "derived, never
re-scored"), and `entities.relevance` is a seed-declared node weight that is read
and never updated. So merging two entities changes what a reader sees in the
brief rail, the hover card and the graph, and changes no score. A merge cannot
promote or bury a story.

## Where the answers live

- `docs/adr/0174` — what auto-merge may decide alone, and why each class is safe.
- `docs/adr/0175` — the read-path invariants: closures on merge, publishers not
  articles, a total that only promises what a limit can reveal.
- `docs/RUNBOOK.md` — the conflict-kind table and what to do about each.
- `tests/test_split_retrieval.py` — the same checks as assertions. If you find a
  defect this skill's method catches, it belongs here too, or it comes back.
- `scripts/lookup_report.py` — the sibling report for acronym grounding, and the
  temperament both follow: print the rows for a human, because a count cannot
  tell a right answer from a wrong one.
