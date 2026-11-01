---
status: accepted
date: 2026-08-04
---

# 2026-08-04 — Recording shares the replay code path


`VENONA_LLM_RECORD=1` writes the **raw stdout bytes** of each successful call to
`fixtures/llm/<task>/<cache_key>.json`, and `ReplayExecutor` feeds those same
bytes back through `parse_envelope`. The offline suite therefore exercises
exactly the production parsing path.

Storing only `structured_output` would have made the cost ledger, the cache
assertions and the refusal path untestable — which is the code most likely to be
wrong. Live probing already proved hand-written envelopes would have been wrong
in two ways (`stop_reason: "tool_use"`, the phantom haiku `modelUsage` entry).

Four envelopes are hand-crafted rather than recorded, because you cannot provoke
them on demand: refusal, 429, permission-denied, and schema-violation. See
`fixtures/llm/_errors/README.md`.
