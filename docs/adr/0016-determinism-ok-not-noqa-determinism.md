---
status: accepted
date: 2026-08-04
---

# 2026-08-04 — `# determinism: ok`, not `# noqa: determinism`


The determinism linter's suppression marker originally reused ruff's `noqa`
syntax, which made ruff emit `Invalid # noqa directive` warnings on every
suppressed line. Renamed to a marker of our own.
