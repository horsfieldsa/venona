---
status: accepted
date: 2026-08-05
---

# 2026-08-05 — SQLite connections and async endpoints


`GET /graph/path` is `async` (it awaits Neo4j) but takes the sync `get_db`
dependency. FastAPI runs sync dependencies in a threadpool while an `async def`
body runs on the event loop thread, so the connection was created in one thread
and used in another — `sqlite3.ProgrammingError`.

`check_same_thread=False` is now set for API connections only. That is safe
*here specifically*: `get_db` hands every request its own connection and the
create/use/close sequence is strictly ordered within one request. What the check
guards against is two threads using one connection **concurrently**, which
cannot happen. Sharing a connection between requests would reintroduce the
hazard.
