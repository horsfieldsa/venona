---
status: accepted
date: 2026-08-05
---

# 2026-08-05 — `venona run --from RUN_ID` was cut, not deferred


`docs/RUNBOOK.md`, `docs/CONTRACTS.md` (C7) and `pipeline/types.py` all
advertised `venona run --only brief --from run:01J...`. None of it existed.

Every stage reads its inputs from SQLite by **state** — `analyzed_at IS NULL`,
clusters absent from `brief_clusters` — never by run id. That is precisely what
makes `--only` work without any new machinery. `--from` would need a new
predicate in every stage to filter by a column they do not consult, to answer a
question ("re-run exactly what that run saw") nobody asked.

`--only` shipped; `--from` is struck from all three documents.
