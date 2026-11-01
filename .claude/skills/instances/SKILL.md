---
name: instances
description: Operate the venona instances on this machine — list them with ports and status, start or stop a local stack, check health, run smoke checks against a chosen one. Use for "which instances exist", "is X running", "stop everything", or any per-instance lifecycle question.
---

# Instances

The default instance is the repo checkout itself (`make dev`, ports
8000/5173/5432/7474/7687). Every other local instance is a `local/<slug>.env`
file plus a `local/<slug>/{config,profile,data}` tree; its ports are stated in
the env file and nowhere else.

## Listing

- `ls local/*.env 2>/dev/null` — the scaffolded instances; `grep PORT
  local/<slug>.env` for each one's block.
- `make ports` and `make ports INSTANCE=<slug>` — who is actually listening.
- `docker ps --format '{{.Names}}'` — running stacks; a compose project
  `venona-<slug>` is that instance's containers.

## Lifecycle (every target takes `INSTANCE=<slug>`; omitting it means the default)

- `make dev INSTANCE=<slug>` — API + web. **Never leave one running in the
  background**; start, verify, stop. The uvicorn --reload hazard (no `.py`
  writes while a run is in flight) applies across ALL running instances at
  once — they share the working directory.
- `make stop INSTANCE=<slug>` — reclaim its dev-server ports.
- `make up/down/wait INSTANCE=<slug>` — its containers.
- `make health INSTANCE=<slug>` — API/Neo4j/CLI health with its env.
- Migrations: `set -a && . local/<slug>.env && set +a && uv run venona migrate`.

## Health and smoke

- `curl -s localhost:<WEB_PORT>/api/health` through the vite proxy . `setup_required: true` means the wizard has not run.
- `make ext-smoke` runs against the DEFAULT instance's :5173 only.
- `make ui-smoke` WRITES and needs a throwaway instance — a scaffolded local
  instance with a fresh database is exactly what it wants; never point it at
  a corpus someone is curating.
