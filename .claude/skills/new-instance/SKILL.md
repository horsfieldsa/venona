---
name: new-instance
description: Create a new venona instance — a fresh local stack for a new domain. Use when asked to "add a domain", "spin up another venona", "create an instance", or to exercise the setup wizard against a clean stack.
---

# New instance

One venona instance = one domain = one full stack (app + Postgres + Neo4j).
docs/adr/0137.

## Local (side by side with the default stack)

1. `make new-local-instance SLUG=<slug>` — allocates the next +10 port block,
   writes `local/<slug>.env`, and lays down placeholder config with the
   PLACEHOLDER-slug descriptor so the stack boots into the wizard.
2. Migrate its fresh database:
   `set -a && . local/<slug>.env && set +a && uv run venona migrate`
3. `make dev INSTANCE=<slug>` — runs beside the default `make dev`. Every
   lifecycle target (`up/down/wait/stop/ports/health`) takes the same
   `INSTANCE=` argument.
4. Open `http://127.0.0.1:<WEB_PORT>/setup` (the scaffolder prints the port)
   and run the wizard: describe the domain → the `domain_setup` run drafts
   every document → review and apply in the config editor (ontology before
   anchors; anchors then need their separate Apply).
5. **The `make dev` hazards apply per instance**: never leave a dev server
   running, and never write a `.py` file while ANY instance's run is in
   flight — uvicorn's --reload watches the working directory for all of them.
6. Tear down: `make stop INSTANCE=<slug>` (dev servers) and
   `make down INSTANCE=<slug>` (containers). The scaffold under `local/` is
   gitignored; delete it to forget the instance entirely.

## Verify before calling it done

Local: both stacks healthy at once (`make health`, `make health
INSTANCE=<slug>`), the new one reporting `setup_required: true` on
`/api/health` until the descriptor is written, and a browser on its port
landing in `/setup`.
