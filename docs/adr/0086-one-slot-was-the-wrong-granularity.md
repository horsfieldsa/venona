---
status: accepted
date: 2026-08-07
---

# 2026-08-07 — One slot was the wrong granularity


`RunRegistry` held a single `_active` handle, so every entry point asked
`is_busy()` and every kind of work refused every other kind. That was right
about the hazard and wrong about its scope.

The hazard is specific: two full pipelines writing `documents` and `clusters`
with two independent `Budget` objects, where whichever finishes last decides
what the corpus looks like. It says nothing about grounding one acronym.

**What it cost.** `POST /glossary/{surface}/lookup` and
`POST /entities/{id}/define` answered 409 for the entire duration of a research
run — tens of minutes — with the message *"a lookup spends against the run's
budget"*. Two things wrong with that sentence: it never did spend against the
run's budget (it has always constructed its own), and the window it blocked is
exactly the window in which someone is reading a brief and hits a term they do
not know. The feature was unavailable precisely when it was wanted.

So: lanes. `research` keeps one slot for the reason the global one had it;
`light` gets three. Contract C11 has the table.

**Three things this had to get right, each of which had a scar to read:**

- **Release, exactly once, in one place.** `glossary.py` carries a comment about
  a `load_lexicon` raise that sat outside the runner's `try`: the task died
  before `finally`, the registry was never released, and every later run
  answered 409 until the server was restarted. Under lanes that failure is
  worse — a leaked slot strands every run queued behind it, permanently, with
  nothing on screen saying so. Hence `run_launcher.launch`, which is now the
  only path from an endpoint to a running task, and which collapsed the two
  lookup endpoints that had already drifted apart while calling themselves
  "a near-copy, and deliberately so".
- **Key the streams by `run_id`.** `publish`/`subscribe`/`cancel` returned early
  unless the id matched the one active run. With concurrent runs that silently
  routes every light run's events into the 1 Hz log-tail fallback: the
  transcript reads as laggy and nothing says why. `orchestrator.py` had the same
  assumption, reading `registry.active.cancelled` off whatever handle held the
  slot — correct only because nothing else could exist.
- **Scope the CROSS-PROCESS guard too.** `venona run`'s pre-flight asked
  `WHERE status = 'running'` with no further test. Left alone, a background
  lookup would have refused a full research run from a terminal — a regression
  introduced by the fix, invisible until someone hit it. It filters by lane now,
  and ignores rows whose `owner_pid` is provably dead, so a stale row no longer
  wedges the CLI either. The API gained the same pre-flight, which it had never
  had: it would happily start a pipeline on top of a CLI run.

**`queued` finally means something.** The column has advertised it in its schema
comment since migration 0001 and nothing ever wrote it, because with one slot
there was nothing to queue. The light lane queues rather than refusing — a
lookup someone asked for is still wanted 30 seconds later, so a 409 only makes
them click again. `_assert_not_live` and `reap_interrupted` already matched on
`('running','queued')`, so a queued row was protected and reaped correctly
without further change.

**Reserved model headroom, not a bigger pool.** The binding resource is
`ClaudeCliExecutor`'s semaphore, and `analyze` saturates it. Raising
`llm.concurrency` from 2 to 4 would have let the pipeline take all four and put
the lookup back where it started: admitted instantly, then parked for minutes.
From a hover card, "started but stuck" and "refused" look identical. So the
light lane gets its own instance with its own semaphore — same class, same
ledger, same replay, so C2 is untouched and the binary is still invoked in one
place.

**The UI had the same conflation.** The live panel was titled "Research run"
whatever was running, and drew six spine segments from a hardcoded literal, so a
one-call lookup rendered as a mostly-empty pipeline with five segments that
never lit — and segments that stay dark read as stages that failed. Run types
are declarative and served over HTTP now (`GET /api/runs/types`), so the label,
the stage list, the counters worth showing and whether there is a spine at all
all come from the run. Adding a type is a table entry, which is how `rewrite`
and `definitions` were added without touching a component.

**Not done here.** `POST /api/curation/rebuild` still runs inline in the request
rather than as a run; it projects the graph, spends nothing, and takes seconds.
The "curation is stage five" reasoning applies — it earns none of the machinery.
