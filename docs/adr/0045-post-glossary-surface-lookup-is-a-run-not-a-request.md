---
status: accepted
date: 2026-08-05
---

# 2026-08-05 — `POST /glossary/{surface}/lookup` is a run, not a request


It returns **202**. A lookup is a search call plus up to three page fetches plus
a read call — 25 to 60 seconds observed live, which is past any sane request
timeout.

Making it a first-class run rather than a background task buys the whole S7
apparatus for free: a `runs` row, the durable event log, `/runs/{id}/stream`
with `Last-Event-ID` replay, and a line in the run history showing what it cost.

It **refuses while a run is active** (409, with a structured body naming the
run). It writes `acronym_definitions` and spends against a budget the run
believes it owns.
