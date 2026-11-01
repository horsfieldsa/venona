---
status: accepted
date: 2026-08-04
---

# 2026-08-04 — Prompt cache assertions are intra-run only


**Measured.** Cross-subprocess cache reuse works (`cache_read = 16038` on call
2), but the tier is `ephemeral_1h`. A **daily** cron always exceeds the TTL, so
every run pays full cache-creation on its first call.

Asserting `cache_read > 0` across runs would produce spurious warnings. The
assertion is scoped to call #2 **within** a run.
