---
name: comments
description: Audit and rewrite comments across the codebase — check every factual claim a comment makes, cut what restates the code, and move overflow reasoning into an ADR with a pointer left behind. Use for "clean up the comments", "are the comments still true", before trusting a comment that looks load-bearing, and after any change that moves a file, retires a symbol or supersedes a decision.
---

# Comments

This codebase treats a comment as an argument, not a label: most carry a
measurement, a scar, or the reason an obvious alternative is wrong. That makes
them worth keeping and it makes them dangerous, because **a comment is the one
artifact nothing executes.** A stale one reads exactly like a true one and is
believed on sight.

Three from a single session, each of which changed what somebody did:

- `api/security.py` argued the session epoch must stay a FILE because it has to
  work when the database is what you are worried about. Correct when written.
  After the disk went, a file lived only until the next deploy, so the comment
  was arguing for silent data loss.
- `CLAUDE.md` says `lint-repositories` "walks all 125 backend modules". It walks
  more than twice that. Nothing failed; the number simply stopped being true —
  and a count pinned in prose goes stale every time a module is added, so the
  fix was to stop quoting one.

## How it is invoked

| you type | what happens |
|---|---|
| `/comments` | a pass over the current diff, or the files you name |
| `/comments <path>` | that file, whole — the unit everything else is built from |
| `/comments status` | the queue: swept, stale, unswept, lines of unanchored prose |
| `/comments sweep` | the next batch off the queue, fanned out over agents |
| `/comments sweep 20` | the same, that many files |

`sweep` is the only form that spawns agents, and typing it is what authorizes
them. A batch is: take `--next N`, one agent per file, then the gate, then
`make test`, then `--budget`, then `--done` for the files that landed. Nothing
is recorded swept until the gate has said `comments only`.

## Do not start by rewriting

Rewriting is the cheap half and it is where a pass goes wrong: prose gets
shorter, claims survive unexamined, and the result reads better while being
just as false. **Check the claims first, in a whole file at a time, and rewrite
only what you have verified.**

## The claim classes, and how each is checked

Measured across `backend/venona` — these are what comments here actually
assert, most common first. Every one is checkable without judgement.

| class | count | check |
|---|---|---|
| names a file or path | 840 | does it resolve? `db/sqlite.py` is named three times in `db/core.py` and has not existed since the Postgres port |
| cites a contract `C1`–`C12` | 196 | is the clause still in `docs/CONTRACTS.md`, and does it still say that? |
| cites an ADR | 139 | does it exist, and is its frontmatter `accepted` rather than `superseded`? A superseded record cited as live is an argument for the reversed decision |
| claims a measured FIGURE | 106 | does it carry a date, or a version pin, and name what produced it? An undated measurement cannot be re-taken or aged out |
| names a symbol `foo()` | 103 | does the symbol still exist, with that name, in that module? |
| carries a number (ms, MB, %) | 69 | is the source re-runnable, and is the figure plausible now? |
| pins a version | 32 | does it match the actual pin in `pyproject.toml` / `package.json`? |
| counts things ("all 314 statements", "125 modules") | 11 | **run the thing that counts.** These go stale silently and are the highest-yield class per line |
| names a worked entity id (`org:dod`) | — | is the PREFIX still live? `org:` was retired in ontology v3 and turned up in five files. Derive the live set from `get_ontology().type_prefix`. A DATED INCIDENT keeps its old id (`org:canada` really was stored); a worked EXAMPLE does not |
| says a test pins it ("`test_x` asserts this") | — | **open the test file.** Three comments this repo cited a test that has never existed, one of them in `docs/CONTRACTS.md`. A comment inventing its own coverage is worse than an undated figure: it tells the next reader the invariant is guarded |

A claim that cannot be checked is a finding, not a pass. Record it.

The first four classes are mechanical, so a script does them:

```
uv run python scripts/check_comment_claims.py                 # whole repo
uv run python scripts/check_comment_claims.py backend/venona/db
```

It narrows the field and never clears it — it cannot tell whether "the event
loop blocks here" is still true, which is the class worth a person. It exits 0
by design: making an undated measurement fail `make lint` is how one gets
deleted at 5pm to get a push through.

**Its first three findings were false positives, and that is the standard to
hold it to.** `repositories/relevance.py` resolved from a sibling package,
`routers/curation.py` elided its middle segment, and `starlette/routing.py` is
a dependency's file. A checker that cries wolf is one nobody runs, so each was
fixed rather than tolerated. If it flags something you can see is fine, fix the
script — do not learn to skim past it.

Baseline, 2026-09-14: **224 claims** across the repo — 212 undated
measurements, 9 dead paths, 3 citations of a superseded ADR. Swept the same
day: every dead path and superseded citation fixed, and 48 measurements dated.
**16 remain**, all of them uses of the word in ordinary prose ("a verified
identity", "the measured width") rather than claims.

The checker was tightened in that sweep, on its own standard. It read one `#`
line at a time, so a claim and the date above it never met; runs of `#` lines
are now one comment. And it flagged the word wherever it appeared, which was
133 findings of noise out of 213 — a measurement is only worth chasing if it
carries a FIGURE somebody could go and re-take, and a version pin (`verified
against 2.1.220`) ages a claim the way a date does.

## The rewrite rules, in the order they bite

1. **Cut what restates the line beneath it.** Same sentence twice, and only one
   of them is checked by anything.
2. **Cut change-narration.** "used to", "no longer", "previously", "this
   replaced". `make lint-comments` catches these; a reader who never saw the
   earlier version is the audience. If the phrase describes runtime state,
   reword it or append `comment-history: ok` with the reason.
3. **One comment, one reason.** A second paragraph justifying the first is
   documentation. It belongs in an ADR or `docs/`, with the load-bearing
   sentence left behind and a pointer to where the rest went.
4. **Point rather than repeat.** `ADR 0234` beats re-arguing ADR 0234. The
   record is versioned, dated and supersedable; a paraphrase in a docstring is
   none of those.
5. **Date every measurement**, and name the command or query. "Measured
   2026-09-14: peak 471 MB of 2,048 across a $197 research run" can be re-taken
   and can age out loudly. "This is memory-hungry" cannot.

   For a measurement already in the tree, the date is recoverable rather than
   guessable — but take the OLDEST entry, not `-1`:

   ```
   git log --format=%ad --date=short -L N,N:path | grep ^20 | tail -1
   ```

   `git log -1 -L` gives the most recent commit that touched the line, so a
   paragraph reflowed weeks after the reading gets stamped with the reflow and
   the claim reads fresher than it is — an error in the one direction that
   matters. Say in the commit that a sweep stamped dates this way: the date is
   when the claim was written down, not a re-measurement.

   **When the LINE is older than the FIGURE, date the figure instead** — `-S` on
   the number itself, which finds the commit that introduced that text:

   ```
   git log --format=%ad --date=short -S '131072' -- path | tail -1
   ```

   A line that was edited in place carries its own first-written date, not the
   claim's, and `-L` reports it: `executor.py`'s `MAX_ARG_STRLEN` reading was
   stamped 2026-09-10 by `-L` and `-S` puts it at 2026-08-06. Both directions of
   error have been found in this repo, so use `-L` for a line that has only ever
   said one thing and `-S` when a figure moved into an older line.

## Where overflow goes

Long reasoning is not a comment problem, it is a filing problem.

- A DECISION, or a reversal → an ADR (`/adr`). Cite it and delete the argument.
- A MEASUREMENT with a method → `docs/`, in the shape of `GRAPH-PERF.md`: the
  numbers, the query that produced them, the date.
- A behaviour a reader must not break → a TEST, which is the only comment that
  cannot go stale.

Rule 4 exists because `.md` is **free** against the density ceiling and code is
not. Moving prose into an ADR is not hiding it; it is putting it where it can
be dated, superseded, and found.

## The budget, before you write

```
uv run python scripts/lint_comments.py --budget
```

Only `.py .ts .tsx .vue .css .mjs .js` count — `tests/`, `.sql`, `.yaml`,
Dockerfiles and `.md` are free. Trimming means DELETING lines: rewording a
paragraph shorter nets zero, which is how one session spent a dozen tool calls
moving nothing. A cleanup pass should drive the ratio DOWN, so the delta check
is not a constraint here, it is the scoreboard.

## Working a pass

**One file at a time, whole.** A comment's context is its module; sweeping by
regex across the repo finds phrasing and misses falsehood.

1. `--budget` for the scoreboard.
2. Pick the target. Densest first is a reasonable order:
   `uv run python scripts/lint_comments.py --top 25`.
3. For each comment in the file, classify its claims against the table above
   and CHECK them. Cheap checks first: paths and symbols resolve, ADRs exist
   and are accepted, counts are re-run.
4. Rewrite only what you verified. Cut per the rules. File overflow.
5. `make lint` and `make test`. Both matter, and the second is not optional —
   several tests read source with `inspect.getsource` and assert on its shape.
6. Record every unverifiable claim. That list is the real output of a pass.

## What must not happen

- **Never "correct" a comment to match the code when the COMMENT might be
  right.** Several here document a bug that was fixed and would come back;
  check `docs/adr/` before deciding which of the two is wrong. The comment
  saying "this looks wrong and is deliberate" is the one most likely to be
  deleted by a tidying pass and most expensive to lose.
- **Never delete a scar.** "This killed two `lookup_all` sweeps and about
  $1.20" is the most valuable line in its file. Shorten the prose around it;
  keep the incident.
- **Never trust a comment about another file** without opening that file. The
  three failures at the top of this page were all comments describing something
  one module away.
- **Never let a pass touch behaviour.** Comments only. A diff from this skill
  that changes a statement is a different change and needs its own review.

## Sweeping the whole tree

A pass over one file is the unit. Sweeping all 697 is the same unit run 697
times, and everything below exists so that it can stop and resume rather than
having to be one heroic sitting.

**The queue is ranked by unanchored PROSE, not by comment volume.** A block of
six or more lines citing no test, ADR, doc or date is both the bloat and the
claim nobody can check, so the ceiling and the first owed item share one
worklist. It stands at 31,847 lines.

```
uv run python scripts/comment_sweep.py --state      # swept / stale / unswept
uv run python scripts/comment_sweep.py --next 12    # the next batch, ranked
uv run python scripts/comment_sweep.py --done <paths…>
```

A file is recorded swept **at a content hash**, so editing it later puts it
back in the queue on its own. After the first full pass the standing worklist
is only what has changed since, which is the state this is meant to reach.

**Every batch ends at the gate.**

```
uv run python scripts/check_comments_only.py     # working tree vs HEAD
```

It blanks every comment on both sides and compares what is left, so a statement
that moved inside a few hundred files of prose churn is caught rather than
reviewed for. A sweep batch that does not print `comments only` is not a sweep
batch. Then `make test` — several tests read source with `inspect.getsource`
and assert on its shape — and `--budget`, which should have gone DOWN.

**And `make lint`, because some comments are not private.** A router's docstring
is its OpenAPI `description` and ships into the committed
`web/src/api/schema.d.ts`; rewriting one leaves that file stale and
`lint-generated` red. `make types` regenerates it, and the regenerated file is
part of the batch. The same trap is waiting in anything else generated out of
prose — check the "never hand-edit generated files" list in `CLAUDE.md` before
assuming a docstring stays where you wrote it.

### Running it as a fan-out

One agent per file, a dozen or so per batch. Each gets the file, this skill,
and one instruction that is easy to get wrong: **check every claim before
rewriting anything**, and report the ones that could not be checked rather than
quietly leaving them.

Two things the agents must not do, because both serialize:

- **Never create an ADR.** Numbers collide the moment two agents file at once.
  An agent REPORTS the record it would write — title, the argument, the file
  the load-bearing sentence stays in — and the operator files them one at a
  time afterwards with `/adr`, then the citations go in.
- **Never touch a file another agent holds.** The queue hands out paths; a
  claim about a neighbouring module is reported, not chased, or two agents
  rewrite the same comment from different directions.

What comes back per file: lines deleted, claims checked, claims that could not
be, and ADRs proposed. The middle number is the one that matters — a file
reported as "tidied" with no claims checked was not swept, it was rewritten,
which is the failure this skill opens by warning about.

## Keeping the result

A pass that finds nothing is worth recording as much as one that finds ten:
the value is in the date. Note in the epic what was swept and when, so the next
pass starts where this one stopped rather than re-reading the whole tree.
