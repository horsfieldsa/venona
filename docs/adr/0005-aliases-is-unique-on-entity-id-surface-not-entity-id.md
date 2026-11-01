---
status: accepted
date: 2026-08-04
---

# 2026-08-04 — `aliases` is unique on `(entity_id, surface)`, not `(entity_id, normalized)`


**Found by testing.** With the normalized key, the seed alias
`next-generation command and control` overwrote the canonical surface
`Next Generation Command and Control` — they normalise identically. The
annotator matches **literal** text, so the full form silently stopped
highlighting in prose.

Two different jobs were conflated: resolution looks up by `normalized`;
annotation matches by `surface`. Multiple spellings that normalise the same are
legitimately distinct and must both exist.
