---
status: accepted
date: 2026-08-04
---

# 2026-08-04 — Scoring axes live in the rubric file, not in code


`llm/schemas.py` builds the analyze JSON Schema at runtime from
`profile/scoring_rubric.yaml`. Axis ids appear nowhere in Python.

Two edit costs, deliberately different: changing a **weight** is free
(`POST /api/rescore` replays stored per-axis scores with zero model calls);
changing a **description** changes what the model is asked and needs
re-analysis.

`overall` is computed in Python, never returned by the model — deterministic,
reweightable for free, and immune to the model drifting on what "78" means.
