---
status: accepted
date: 2026-08-04
---

# 2026-08-04 — Stub feed items are skipped, not stored


An item whose extractable text is under ~400 characters does not become a
document at all. A 60-character feed blurb analysed as if it were an article
produces confident nonsense that then enters the graph as assertions.

In the first live fetch this dropped 8 of 72 items — all genuine stubs.
