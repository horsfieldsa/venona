---
status: accepted
date: 2026-08-18
---

# 2026-08-18 — The shipped design is canonical; the handoff kit is an ancestor

Where the design handoff kit (untracked)
and the shipped site disagree, **the shipped site wins**. The kit is dark-only,
predates light mode, names a 56 px nav the site ships at 48, and its
`#d2ecf9` wordmark fill is already a pinned failure signal in
`tests/test_brand_lockup.py`. Nothing is restored from it.

Two corollaries:

1. **Docs describe the code, not the other way round.** `docs/DESIGN.md`'s
   published spacing scale (4/8/12/16/24/32/40/48) is corrected to what
   `tokens.css` declares (4/8/12/18/26/36/48/64), its `components/ui/`
   inventory is completed, and the "44px header" prose becomes 48.
2. **The brand is domain-invariant.** A new domain instance changes copy
   (through the descriptor, 0138) — never the marks, colours or typefaces.
   The brand rules, currently scattered across `Wordmark.vue`, `Mark.vue`,
   `AppHeader.vue` and tests, get one home: `docs/design/BRAND.md`.

Design work runs under the `/design` skill so the patterns are applied
consistently instead of rediscovered per task.
