---
status: accepted
date: 2026-08-04
---

# 2026-08-04 — Dedup layer 3 (SimHash) deferred


At Hamming ≤ 3 on a 64-bit SimHash, matching requires ~95% shingle overlap —
which layer 2 (content hash) already catches. Catching genuinely re-led wire
stories needs distance 8–16, which needs ≥9 bands and breaks the 4-band scheme
entirely.

The `documents.simhash` column is reserved so adding it later is not a
migration.
