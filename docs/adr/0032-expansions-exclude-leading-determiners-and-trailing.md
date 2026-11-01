---
status: accepted
date: 2026-08-05
---

# 2026-08-05 — Expansions exclude leading determiners and trailing possessives


Found on the real corpus:

- `"a Modular Open Systems Approach (MOSA)"` yielded `"a Modular Open Systems
  Approach"`. Articles are stopwords to `acronym_variants`, so the longer window
  verifies equally well and wins by being longer.
- `"Federal Emergency Management Agency's (FEMA)"` stored the possessive.

Both produce two glossary rows for one concept — exactly the duplication the
alias system exists to prevent.
