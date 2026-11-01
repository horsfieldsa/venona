---
status: accepted
date: 2026-09-14
---

# 2026-09-14 — The daily heartbeat is a dead-man's switch, so only the scheduled reading pings it

`heartbeat=True` is set on exactly three run kinds: `daily`, `on_demand` and
`cli` — the three flavours of the pipeline that write a brief.

**The alarm is pointed at silence, so a ping has to mean the reading happened.**
`run.succeeded` is watched by noticing its absence. That only works while every
kind that pings is a kind whose absence is worth waking somebody for.

**Neither obvious wider rule is correct.** `detail == "pipeline"` also matches
`backfill` and `domain_setup`, which are operator errands: a domain being set up
on a Tuesday is not the daily reading having happened. The research LANE is
wider still, and includes the hand-started `research` sweep, which somebody is
already watching.

**Reversal trigger:** a second scheduled kind. Then heartbeat stops being a
property of the brief-writing flavours and becomes a property of "on the
schedule", and the field moves.

**This matters because the argument lived only in a field comment, and that
comment had drifted to the wrong count** — it said four pipeline flavours when
there are three, so a new kind copying the reasoning would have inherited the
error.

**Consequence.** `services/run_types.py` keeps the load-bearing sentence on the
`heartbeat` field and cites this record. Related: C11 (run types and lanes),
ADR 0183 (`status` says whether it ran, `outcome` what it found).
