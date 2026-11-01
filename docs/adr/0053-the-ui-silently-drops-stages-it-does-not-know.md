---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — The UI silently drops stages it does not know


`stores/runs.ts` hardcodes the stage strip and drops events whose stage name is
not in its list. `persist` has been emitted by the orchestrator and dropped by
the UI since S1 with nobody noticing — which is exactly how a new stage becomes
invisible.

Contract C6 already listed the store in its touch list. `test_the_ui_knows_every_stage`
parses the literal out of `runs.ts` and asserts it equals `orchestrator.STAGES`,
which makes that list enforceable rather than aspirational.
