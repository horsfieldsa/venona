---
status: accepted
date: 2026-09-11
---

# 2026-09-11 — The window belongs to the brief, not to the reading

`pipeline.types.retrieval_window` has been one helper for two stages since the
day it was written, and its docstring carries the measurement that made it one.
On `run:06a8160f`, `analyze.pending_clusters` had no date predicate while
`brief.build_retrieval` enforced `lookback_hours`, so the run scored 112
clusters of which 8 were inside the brief's window: about $12 of the $13 analyze
spend was structurally unable to reach the artifact it was paying for. Two call
sites reading one setting had drifted, and one function cannot.

That decision stands. **What this record changes is its scope: the coupling
binds a run that writes a brief, and only that run.**

## The argument it was resting on

The justification is one sentence, and it is conditional:

> a cluster outside it cannot reach a brief under any ranking, so scoring it is
> spend that buys nothing.

Both halves are about the brief. In a run that writes none, the premise is not
weakened, it is absent — there is no ranking to fall outside of, and the spend
buys the graph instead, which is the artifact that run exists to produce.

Applying the bound anyway is not conservative. It is the one thing that made
three months of already-fetched reading permanently unreachable.

## What was actually unreachable

Measured on production the day this was written: 823 clusters, **171 of them
never analyzed and never scanned for short forms** — 21% of the corpus, spread
across June (4), July (43), August (98) and September (26). Every one had been
fetched, extracted, deduplicated and stored. They were dropped at the analyze
queue for being older than 36 hours by the time a run reached them.

No command could reach them. `run_analyze` calls `retrieval_window` internally,
so `venona run --only analyze` and `venona analyze-run` were bounded identically
to the nightly run. The knob existed, was editable, and moved nothing, because
raising `lookback_hours` in `config/settings.yaml` widens the brief's retrieval
too and turns the 2026-08 incident back on.

## The decision

`retrieval_window` takes `writes_brief`, defaulting to `True`. A caller that
writes no brief says so and gets the window it asked for; a caller that does
gets the old behaviour with a hard ceiling of `MAX_BRIEF_LOOKBACK_HOURS = 168`,
raising `WindowTooWide` above it.

168 rather than 36, because `first_run_lookback_hours` is 168 and is legitimate:
a cold start has no yesterday. So the ceiling is the widest window a brief has
ever had a reason to use, and anything above it is a run reading for the graph.

The refusal is stated twice on purpose. `TriggerRun` validates first, so a
person gets a 422 naming the field rather than a run that starts and then dies;
`retrieval_window` is the backstop that no future caller can go round.

## The knob is a copy, never an assignment

Every ceiling a deep read widens — `lookback_hours`, `discovery_max_age_hours`,
`max_calls_per_run` — is already read off `settings` at the point of use, so the
whole mechanism is `with_overrides`, one `model_copy` handed to `build_context`.
Nothing downstream needed a parameter.

It is worth naming what this deliberately does not copy. `cli.py::_analyze_run`
sets `settings.llm.max_calls_per_run` by assignment, which is correct there and
only there: the CLI is a one-shot process. In the API that object is
`app.state.settings`, shared with every other run, the scheduler and
`/api/health`, and one run's raised ceiling would silently become everyone's.

## Consequences

- A new run kind, `backfill`, labelled **Deep read**. Its `stages` tuple has no
  `brief` in it, which is what lets the orchestrator claim a reading outcome for
  it and for nothing else — a kind that declares a brief and is told to skip it
  this once still claims nothing, because its `outcomes` table spells
  `delivered` as "brief written".
- The first-run anchor load moves from `if only is None` to
  `if "analyze" in wanted`. The old gate meant any run naming its stages
  resolved cold on a fresh instance, minting duplicates of the whole declared
  trunk. Analyze is the stage that needs the anchors, so analyze is the test.
- `POST /runs` stops warming readers' briefs and announcing an edition for a run
  that wrote neither. Both calls were unconditional; on a deep read they would
  have spent $0.45 a head fanning out from a brief that does not exist.
- The cap will bind and that is the design. Draining the 171 costs roughly 1,900
  model calls against a configured 250. `BudgetExceeded` defers the remainder
  and ends the run `succeeded_partial`, and because every stage keys on state
  rather than on a run id, the next deep read resumes exactly where it stopped.

## A window nobody named is no window

Stated separately because getting it wrong shipped, and the failure was silent.

"Everything waiting" is the default the screen offers and it sends no lookback.
No lookback meant no override, and no override meant the CONFIGURED window --
which is the brief's 36 hours, which the daily has already read. The first real
deep read therefore reported `succeeded`, finished analyze in under two seconds
with `clusters_seen: 0`, and left all 171 waiting clusters exactly where they
were. Every link in the chain was individually correct.

So the rule is explicit: a run reading for the graph that names no window has no
date bound at all. `bounded = writes_brief or lookback_hours is not None`.

It is expressed as a cutoff at the beginning of time rather than as
`cutoff=None`, and that distinction is load-bearing. `None` means "no bounds at
all" and belongs to `only_cluster`: an ingest run was asked for one article by
name, so it suspends the LENGTH floor as well. A deep read wants the date bound
gone and every other guard kept, because it has no use for a two-paragraph stub
and would pay a model call to extract from one. A vacuous date predicate gives
exactly that without moving anything else.

## What this does not license

Widening the window for the brief. The 2026-08 measurement is unchanged and the
ceiling now enforces it, where before it was a convention two call sites shared.

*Reverses nothing in [0014](0014-relevance-is-scored-once-against-the-profile.md);
extends the scope note in [0183](0183-status-is-not-outcome.md) on what a run
may claim to have found.*
