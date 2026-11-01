---
status: accepted
date: 2026-08-05
---

# 2026-08-05 — Interrupted runs are reaped on startup


A killed process leaves a run at status `running` forever, and the UI spins on
it indefinitely. `reap_interrupted` marks those `failed (interrupted)` during
lifespan startup.

Related: `logging.basicConfig` is called in `main.py` because uvicorn configures
the root logger for *its own* loggers only. Without it `venona.scheduler` logged
nothing and "did the scheduler start?" could only be answered by introspecting
`app.state` — exactly the question a startup log should answer.
