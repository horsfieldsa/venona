---
status: accepted
date: 2026-08-05
---

# 2026-08-05 — Every model call goes through `call_and_record`


The usage ledger lived inside `runner.py::analyze_document`, so every other call
site bypassed it. `lookup_web` spent real money on WebSearch and WebFetch and
appeared nowhere in `llm_calls` — the budget could not see it, and neither could
the cost figure the run reported.

`call_and_record(ctx, task, ...)` owns `record_call`, both counters and
`budget.charge`. It deliberately does **not** catch `PermissionDenied`: a denied
tool must reach the caller as a failure (contract C2).

Acronym search, acronym read and brief now route through it. `grep -c
"ctx.llm.call" backend/venona/pipeline/*.py` returns 0 for every module, which
is the check that keeps a fourth bypass from appearing.
