---
status: accepted
date: 2026-08-17
---

# 2026-08-17 — Tuning rationale moved out of `config/settings.yaml`


`settings.yaml` had reached 168 comment lines against 55 lines of actual
configuration — three quarters of the file was prose, and several settings
carried a paragraph each. A config file that has to be read rather than scanned
stops being usable as a control panel, so per-setting comments are now capped at
two lines and the measurements live here.

Nothing below changes a value. This is the reasoning, verbatim in substance,
for the settings whose justification did not fit in two lines.

**`llm.light_concurrency: 2`** — a SEPARATE semaphore, not a bigger shared one.
Raising `concurrency` to 4 would let `analyze` take all four and put an
on-demand lookup back where it was: admitted instantly, then parked behind
twenty minutes of clustering. Someone is watching a spinner for every one of
these, and "started but stuck" and "refused" look identical from a hover card.

**`llm.max_calls_per_run: 250`, not 120** — the cap is a runaway guard, and what
made 120 right was that `analyze` was unbounded: it walked the whole backlog
newest-first and this was the only thing that stopped it. Analyze is bounded by
`lookback_hours` now, so 120 had quietly become a cap on the WRONG stage.
Measured on the first bounded run: analyze 91 + discovery 8 + acronyms 21 = 120
exactly, so `define` got zero of its 100 and deferred 83 of 87 entities. Raising
`define_cap_per_run` bought nothing because the global cap was already spent.
250 covers a first run honestly, and the extra calls are the cheap ones
(`define_write` measured at $0.014), so it is roughly +$2, not +$12. A per-stage
RESERVATION would be better than a bigger shared pool; see `docs/pm/NOT-DOING.md` for
the trigger.

**`llm.discover_model: sonnet`** — this is the FALLBACK for sweeps that declare
no `model:`. `sources.yaml` says "the two areas closest to the thesis get opus;
the rest get sonnet", but only those two carry an override, so an opus default
silently gave all eight sweeps opus: 8 calls, $7.69, **67% of the first full
run's cost**. Sonnet is the documented intent rather than a downgrade — a sweep
returns candidate URLs and nothing else, and `analyze` is where the judgement
happens. Roughly half of each sweep's cost is the haiku WebSearch sub-model,
which this does not change.

**`pipeline.lookback_hours: 36`** — one helper resolves it for both analyze and
the brief because the two disagreeing is what produced the "A thin day" brief.
Analyze had no date predicate at all, so `run:06a8160f` analysed 112 clusters of
which 8 were inside the window: ~$12 of its $13 analyze spend could not have
reached a brief under any ranking.

**`pipeline.first_run_lookback_hours: 168`** — a reader with an empty database
has no yesterday. At 36 hours a cold start briefs whatever the last day and a
half contained, which on the first real run was five items, while a `must_read`
scoring 76 and another scoring 60 sat just outside, analysed and paid for.

**`pipeline.discovery_max_age_hours: 48`** — the sweep prompt has always ASKED
for recent results and nothing enforced it, so a sweep returned a PR Newswire
release from ten months earlier. An unbounded sweep also spends its budget
re-finding our own archive: of 47 sweep documents on the first run, the 7 from
domains we already subscribe to via RSS were 100% stale (p50 409h) and none
reached the brief. The two that did reach it came from domains RSS does not
carry, and both were under a day old. Source coverage and time depth are
orthogonal, and bounding time is what forces a sweep to reach for a source we do
not already have. RSS is untouched; `lookback_hours` bounds it at analyze.

**`pipeline.fetch_limit_per_source: null`** — this knob used to live on the Runs
page as "Run defaults", in localStorage, applied only to runs a person started
by hand. The nightly run passed nothing and was therefore uncapped, so the two
could differ with nothing on screen saying so. One file governs both now.

**`pipeline.acronym_ordinary_min_docs: 2`** — this was effectively 1 (a set, not
a count), so one title-cased word anywhere in the corpus suppressed an acronym
everywhere: a Haitian ex-parliamentarian named `Prophane Victor` is why the
Army's VICTOR programme was never detected. At 2 every measured win survives
(HAVE 246 docs, AIR 117, HEAT 13, LONDON 8, DAYTON 7, PARIS 6, SENTINEL 3) and
20 tokens change side, most of them real acronyms — DEVCOM, RUSI, USNI, SDA,
MANTECH, ASCA. The cost is WARSAW and ANDOVER landing in the undefined queue,
where they are visible and free.

**`pipeline.acronym_min_articles: 1`, not 2** — this reads like a correctness
guard but only ever bounded SPEND, which is already bounded twice. At 2 a term
was held to a stricter standard exactly when it was hardest to define: tier 1
(in-article) applies no article threshold at all, so the glossary is full of
`article_count=1` rows, while a term that needed the web BECAUSE no article
expands it was refused for appearing in one article. A false candidate still has
to survive tier 3's quote and initials checks.

**`pipeline.acronym_lookup_all_cap: 100`** — uncapped was the first answer, on
the grounds that "look up all of them" is the whole request. Measured against a
real backlog that is 558 surfaces, six and a half hours, and one of three light
slots held for all of it. A batch is the same work in pieces you can stop
between: it lands in about forty minutes and pressing the button again takes the
next hundred. Blank is still uncapped and the modal quotes a price for it. Note
it is per RUN, not per press — the run stops at `max_calls_per_run` whatever
this says.

**`pipeline.define_cap_per_run: 100`, not 25** — at 25 this was the binding
constraint on whether hover cards work at all: the first real run ended with 47
of 727 entities defined, so 93% of cards were empty against a product that
promises "every program and acronym is hoverable". Tier 1 covers less than it
looks like it should — over a brief's 180 entities, 31 carry a sentence that
defines them and 149 do not. At the measured $0.014 per call this is ~$1.40 a
run.
