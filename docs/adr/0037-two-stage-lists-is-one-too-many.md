---
status: accepted
date: 2026-08-05
---

# 2026-08-05 — Two stage lists is one too many


`pipeline/types.py` declared a nine-name `STAGES` — fetch, normalize, dedup,
acronyms, analyze, resolve, score, persist, brief — that contradicted
`orchestrator.STAGES` (four). Five of the nine are steps *inside* a stage and
were never separately runnable.

Nothing imported it, which is exactly the danger: `--only` validating against
the wrong list would make `--only persist` a valid-looking no-op. Deleted, with
a test asserting `types.STAGES` stays gone.
