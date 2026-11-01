---
status: accepted
date: 2026-08-16
---

# 2026-08-16 — The call cap was not checked on any first attempt


**Measured.** `run:06a8160f` charged **152 calls against a cap of 120** — a 27%
overshoot — while the same run deferred 107 clusters, 8 acronym lookups and 9
definitions *for being out of budget*.

`call_and_record` read `if attempt and budget is not None: budget.check()`. The
`if attempt` guard was written for the brief: one call, and a near-cap run
skipping the brief entirely is the worst degradation available, so it should not
be refused. But `call_and_record` is the path for `define_write`, `adjudicate`,
`acronym_read` and `discovery` too, and none of those check the budget in their
own loop before dispatch. `define_write` alone made 31 uncapped calls.

Retries were never the leak. First attempts were.

**Consequence:** `exempt: bool = False`, passed by the brief call site alone.
The carve-out is now something one caller asks for by name rather than something
every caller gets by accident.
