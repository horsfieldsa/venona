---
status: accepted
date: 2026-08-16
---

# 2026-08-16 — Analyze was serial against a semaphore of two


**Measured.** The analyze stage's wall clock was 77.21 minutes; the sum of its
112 call durations was 77.18. Effective parallelism **1.00**, against
`Semaphore(llm.concurrency)` with `concurrency: 2`.

The semaphore was real and correctly sized — `fetch.py` gets ~1.7-2× out of it
on the same executor. `run_analyze` was a plain `for row in rows: await
analyze_document(...)`, which never gives it two things to hold. Half the
stage's 77 minutes was the loop shape.

**Consequence:** batched `asyncio.gather` of `llm.concurrency`, with results
persisted in ROW ORDER after each batch. Deliberately not a free-running worker
pool: stages must be byte-reproducible offline, and a pool that writes as calls
land makes the SQLite write order depend on model latency. Ordering the writes
costs the tail of each batch and buys determinism.

A concurrent stage also cannot bound itself with `budget.check()`, which answers
"may I make one more" and would be overshot by up to N-1 per batch. `Budget.remaining`
exists for that.
