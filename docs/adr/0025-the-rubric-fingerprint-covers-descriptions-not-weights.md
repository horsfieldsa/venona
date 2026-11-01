---
status: accepted
date: 2026-08-05
---

# 2026-08-05 — The rubric fingerprint covers descriptions, not weights


`rubric_fingerprint()` hashes axis **ids and descriptions** only.

A weight change must NOT invalidate stored scores — applying it is free, which
is the entire reason the model never returns an overall. A description change
must invalidate them, because the model then answered a different question and
the stored 0-5 values no longer mean what the new rubric says.
