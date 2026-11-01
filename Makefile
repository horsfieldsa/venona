.PHONY: setup dev instance-dev dev-hub dev-hub-stack stop stop-stack ports up down wait new-local-instance health test ui-smoke ext-build ext-smoke hub-web typecheck-hub-web icons field hub-public ext-package lint reset-bare lint-tokens lint-determinism lint-readonly lint-repositories lint-ontology lint-version version lint-generated lint-bundle lint-comments lint-web typecheck-web web-deps types schema seed reset verify-sources record clean \
        graph-health hub-dev

# Which local instance the lifecycle targets act on. `defense` is the repo
# checkout itself -- bare `make dev` is byte-for-byte what it always was. A
# second instance lives under `local/<slug>.env` + `local/<slug>/{config,profile}`
# (written by `make new-local-instance SLUG=<slug>`) and runs beside the
# default on its own port block and its own compose project, which is what
# scopes its containers and volumes (docs/adr/0143). The `make dev` hazards
# apply PER INSTANCE: never leave one running, and uvicorn's --reload watches
# the whole working directory for every instance at once.
INSTANCE ?= defense
ifeq ($(INSTANCE),defense)
COMPOSE := docker compose
else
include local/$(INSTANCE).env
export
COMPOSE := docker compose -p venona-$(INSTANCE) --env-file local/$(INSTANCE).env
endif

API_PORT ?= 8000
WEB_PORT ?= 5173
VENONA_NEO4J_HTTP_PORT ?= 7474
# The local hub (`make hub-dev`). :8100 belongs to the 10th scaffolded
# instance's port block, hence 8180.
HUB_DEV_PORT := 8180

# ── setup ────────────────────────────────────────────────────────────
setup:
	@command -v uv >/dev/null || (echo "installing uv..." && curl -LsSf https://astral.sh/uv/install.sh | sh)
	uv python pin 3.12
	uv sync
	cd web && npm ci
	$(MAKE) up wait
	uv run venona migrate
	@echo
	@$(MAKE) health

# ── run ──────────────────────────────────────────────────────────────
# A dev server orphaned by a previous run (Ctrl-C at the wrong moment, a crashed
# terminal, an agent that backgrounded one) leaves :8000 bound, and uvicorn's
# "Address already in use" does not say what is holding it. `stop` reclaims our
# own processes; anything else we refuse to touch and name instead.
# `dev` is ONE instance: the API and the UI that reads it, and with
# `VENONA_AUTH_ENABLED=0` no sign-in at all. That is everything a single reader
# needs. `instance-dev` is the older name for the same thing.
#
# `dev-hub` adds the hub, which is OPTIONAL: it earns its place only when several
# instances sit behind one sign-in or are managed centrally (docs/HUB.md). It
# needs `.env.hub` (the secrets and a Google OAuth client), and it is a SHARED
# SINGLETON -- the OIDC issuer for every instance -- so it refuses anything but
# the default instance. Run it once and `make dev INSTANCE=<slug>` beside it.
# uvicorn's --reload watches the WORKING DIRECTORY, so with the hub running too,
# editing any `.py` anywhere restarts both processes at once.
#
# The `pay` lane forwards Stripe's test webhooks to the hub. It is a script
# rather than a command because `concurrently -k` kills every other lane the
# moment one exits, and a machine with no Stripe CLI would otherwise take the
# whole stack down with it. It also compares the signing secret against
# `.env.hub` and says so loudly when they differ.
dev: stop up wait
	@npx --yes concurrently -n api,web -c cyan,magenta -k \
	  "uv run uvicorn venona.main:app --reload --port $(API_PORT) --app-dir backend" \
	  "cd web && npm run dev -- --port $(WEB_PORT)"

instance-dev: dev

dev-hub:
ifneq ($(INSTANCE),defense)
	@echo "The hub is shared: starting a second one takes sign-in down for every"
	@echo "instance. Run make dev-hub once, then: make dev INSTANCE=$(INSTANCE)"
	@exit 1
endif
	@test -f .env.hub || { echo "make dev-hub needs .env.hub: copy .env.hub.example,"; \
	  echo "fill in the secrets and a Google OAuth client (docs/HUB.md)."; exit 2; }
	@$(MAKE) dev-hub-stack

dev-hub-stack: stop-stack up wait
	uv run hub migrate
	@npx --yes concurrently -n hub,api,web,pay -c yellow,cyan,magenta,blue -k \
	  "uv run uvicorn hub.main:app --reload --port $(HUB_DEV_PORT) --app-dir hub" \
	  "uv run uvicorn venona.main:app --reload --port $(API_PORT) --app-dir backend" \
	  "cd web && npm run dev -- --port $(WEB_PORT)" \
	  "scripts/dev_stripe_listen.sh $(HUB_DEV_PORT)"

stop:
	@uv run python scripts/reclaim_ports.py $(API_PORT) $(WEB_PORT)

# The stack's three. Scoped wider than `stop` deliberately: the hub is not
# per-instance, so `make stop INSTANCE=x` must not reach for it -- it would take
# down the issuer every other instance is signing in through.
stop-stack:
	@uv run python scripts/reclaim_ports.py $(API_PORT) $(WEB_PORT) $(HUB_DEV_PORT)

# Report what is listening, without changing anything.
ports:
	@for port in $(API_PORT) $(WEB_PORT) $(HUB_DEV_PORT); do \
	  pid=$$(lsof -ti tcp:$$port -sTCP:LISTEN 2>/dev/null | head -1); \
	  if [ -n "$$pid" ]; then \
	    echo "$$port: pid $$pid  $$(ps -o args= -p $$pid 2>/dev/null | head -c 70)"; \
	  else echo "$$port: free"; fi; \
	done

up:
	$(COMPOSE) up -d postgres neo4j

# Postgres FIRST, and it is the one that must be up before `venona migrate`.
# Both loops are bounded and print, rather than `up -d --wait`, so a hang says
# which service it is waiting on instead of one opaque compose timeout.
wait:
	@printf "waiting for postgres"; \
	ok=0; \
	for i in $$(seq 1 60); do \
	  if $(COMPOSE) exec -T postgres pg_isready -U venona -d venona >/dev/null 2>&1; then \
	    echo " ok"; ok=1; break; fi; \
	  printf "."; sleep 1; \
	done; \
	if [ $$ok -eq 0 ]; then echo " TIMEOUT"; exit 1; fi
	@printf "waiting for neo4j"; \
	for i in $$(seq 1 60); do \
	  if curl -sf localhost:$(VENONA_NEO4J_HTTP_PORT) >/dev/null 2>&1; then echo " ok"; exit 0; fi; \
	  printf "."; sleep 1; \
	done; \
	echo " TIMEOUT"; exit 1

down:
	$(COMPOSE) down

health:
	@uv run venona health

# ── quality ──────────────────────────────────────────────────────────
# Drive the running app and assert it behaves. NOT part of `make test`, which is
# offline and free -- this needs `make dev` up. It exists because typecheck,
# lint and the offline suite are all blind to a blank canvas.
#
# THROWAWAY INSTANCES ONLY, and it refuses to start without `VENONA_UI_SMOKE=1`.
# It WRITES: it clicks, types and confirms against the real database, and it has
# recorded curation verdicts on a working corpus and appended to
# `profile/relevance_profile.md` on every run for months.
#
# This said "CI ONLY" and there is no CI (see ROADMAP's "Not doing"), which made
# the constraint unenforceable and easy to read as "someone else's problem". The
# real rule never needed CI: run it against a fresh checkout and a fresh
# database, never against a corpus someone is curating.
ui-smoke:
	uv run python scripts/ui_smoke.py

# ── the browser extension ────────────────────────────────────────────
#
# `ext-build` regenerates `extension/generated/` from the app's own token and
# prose stylesheets, so restyling a mention or adding an entity type propagates
# here without anyone copying a colour. `finish.mjs` then refuses to ship a
# bundle whose utility layer failed to generate -- the one failure mode of the
# `@source` globs `card.css` needs and `theme.css` warns about.
ext-build:
	cd extension && npm install --silent && npm run build

# The hub's operator surface. It builds STRAIGHT into `hub/hub/static/app/`,
# the package the hub ships, so there is no copy step and nothing to forget --
# `routers/brand.py` enumerates that directory at import and serves one
# explicit route per file. Until this has run, `/admin` answers 503 and says
# the surface is not built rather than 404ing, which would look like the gate
# refusing.
hub-web:
	cd hub/web && npm install --silent && npm run build

# What `hub-web` does in the image, checked here: `npm run build` chains
# `vue-tsc --noEmit`, so this is also the typecheck.
typecheck-hub-web:
	@cd hub/web && npm run typecheck --silent

# Unlike `ui-smoke`, this is safe anywhere: it only reads. It needs the API up
# and `extension/dist` built.
ext-smoke: ext-build
	uv run python scripts/ext_smoke.py

# Every icon every surface ships -- favicon set for the app, a separate front site
# and the hub, plus the toolbar and Web Store PNGs -- rasterised from the mark.
#
# COMMITTED, not built. Chrome will not take an SVG for `icons`, iOS and Safari
# want PNG and .ico, and making every build shell out to librsvg would mean
# nothing could be built on a machine without it -- for files that change when
# the logo does, which is approximately never. So this is run deliberately and
# its output is checked in; `finish.mjs` verifies the PNG signature of what it
# copies and `tests/test_brand_lockup.py` holds the three surface sets equal.
icons:
	uv run python scripts/brand_icons.py

# The graph field behind every error page, emitted into all three surfaces.
#
# COMMITTED, like the icons and for the same reason: the drawing changes when
# someone redesigns it, which is approximately never, and the alternative is
# three build steps on three unrelated toolchains (vite, vite, and a hub that
# has no build at all). The three copies cannot be one file -- they name their
# colours in three vocabularies and the hub has no token pipeline -- so
# `lint-generated` diffs them instead. See scripts/graph_field.py.
field:
	uv run python scripts/graph_field.py

# The front door's stylesheet and its four type files, resolved out of the
# app's own `tokens.css` and copied from the fontsource packages. Committed,
# because these pages ship with no build step and must not depend on one.
hub-public:
	uv run python scripts/hub_public_styles.py

# A store-ready archive. Three things the obvious one-liner gets wrong:
#
#   1. The Web Store wants `manifest.json` at the ARCHIVE ROOT, so this zips the
#      directory's CONTENTS (`cd dist && zip -r ../x.zip .`) rather than the
#      directory, which would nest everything under `dist/`.
#   2. The output goes OUTSIDE `dist/`. That directory is git-ignored and
#      `emptyOutDir: true` wipes it on the next content build, so a release
#      artifact written into it is both invisible to git and destroyed by the
#      next `make ext-build`.
#   3. `key.pem` is the extension's identity and lives outside `dist`, but it is
#      checked for anyway -- shipping it would hand over the id itself.
#
# Built on the STORE channel, not via `ext-build`: the packaged default points
# at production where the dev default points at the Vite proxy, and
# `dist/CHANNEL` marks the result so `ext-smoke` refuses to drive it. The next
# plain `make ext-build` rebuilds dev and removes the marker.
ext-package:
	cd extension && npm install --silent && VITE_EXT_CHANNEL=store npm run build
	@test ! -f extension/dist/key.pem || { \
	  echo "refusing to package: key.pem is inside dist/"; exit 1; }
	@version=$$(node -p "require('./extension/manifest.json').version"); \
	  out="extension/venona-$$version.zip"; \
	  rm -f "$$out"; \
	  (cd extension/dist && zip -qr "../venona-$$version.zip" . -x CHANNEL); \
	  echo "$$out"; \
	  unzip -l "$$out" | grep -q ' manifest.json$$' || { \
	    echo "manifest.json is not at the archive root"; exit 1; }

# Both halves. The frontend suite is unit-only and takes under a second; it
# covers what `vue-tsc --noEmit` cannot see -- an out-of-order response
# overwriting a newer one, a timeout that never fires -- and what
# `make ui-smoke` should not have to, since that WRITES and needs a throwaway
# instance.
test:
	uv run pytest -q
	cd web && npm test --silent
	cd extension && npm test --silent

# Every frontend gate needs web/node_modules, and each tool fails differently
# when it is missing. One check, one sentence, one command that fixes it.
#
# `make lint` was never runnable on a bare checkout anyway -- `lint-tokens` is
# `uv run` and needs `uv sync`. So the Python half hard-failed while the JS half
# soft-passed, and that asymmetry WAS the bug: a machine with no npm install
# silently stopped checking the frontend instead of saying so.
# Source-level, so it needs no build and cannot be broken by a bundler upgrade.
# See the module docstring for why a chunk-size threshold would be worse.
lint-bundle:
	@uv run python scripts/lint_bundle.py

lint-comments:
	@uv run python scripts/lint_comments.py

web-deps:
	@[ -d web/node_modules ] || { \
	  echo "web/node_modules is missing -- run 'cd web && npm ci' (or 'make setup')."; \
	  exit 1; }

# `vue-tsc --noEmit` ran NOWHERE. `npm run build` chains it and no make target
# calls `npm run build`, so a type error in web/src passed both `make lint` and
# `make test` and only surfaced at build time.
#
# It belongs in `lint` rather than `test`: lint is "does this satisfy its stated
# rules without running it", test is "does it behave". AFTER lint-generated,
# deliberately -- a stale schema and the type errors it causes are one fact, and
# reporting the drift first sends you to `make types` rather than to the call
# sites.
typecheck-web: web-deps
	@cd web && npm run typecheck --silent

lint-web: web-deps
	@cd web && npm run lint --silent

lint: lint-tokens lint-determinism lint-readonly lint-repositories lint-domain lint-ontology lint-version lint-generated lint-bundle lint-comments typecheck-web typecheck-hub-web lint-web
	uv run ruff check backend hub notices tests
	uv run ruff format --check backend hub notices tests

# One release in VERSION, and every manifest that declares one following it.
# SEPARATE FROM lint-generated: these are authored files with one propagated
# field, not generated artifacts, and folding them into that target would
# mislabel them and muddy its "do NOT run the generator" failure messages.
#
# `uv lock --check` is the half nothing edits by hand. Both images install
# --frozen, and a bump leaves the lockfile behind until something resolves.
lint-version:
	@uv run python scripts/version.py --check
	@uv lock --check

# Propagate a bumped VERSION. The lock comes after the splice, because the
# manifests are what it resolves from.
version:
	@uv run python scripts/version.py
	@uv lock

# Contract C5: components may reference tier-2 semantic tokens only.
lint-tokens:
	@uv run python scripts/lint_tokens.py

# The research<->graph contract must load, validate, and match its generated
# docs. Drift here means the model's schema and the writer's rules disagree.
lint-ontology:
	@uv run venona ontology check >/dev/null
	@uv run venona ontology docs >/dev/null
	@git diff --quiet docs/graph-schema.md 2>/dev/null || \
	  (echo "docs/graph-schema.md is stale -- commit the regenerated file"; exit 1)
	@echo "lint-ontology ok"

# Decision 7: offline determinism. Nothing in pipeline/services/llm may reach
# for wall-clock time, randomness, or the network directly.
lint-determinism:
	@uv run python scripts/lint_determinism.py

# The repository boundary. No SQL outside `db/`, anywhere in the backend: a raw
# `conn.execute` looks exactly like a repository call at the call site, so this is
# checked rather than remembered. AST-based -- see the script for what it cannot
# see and why that is the right line.
lint-repositories:
	@uv run python scripts/lint_repositories.py

# Invariant 4: the domain is configuration; code is domain-blind. A defense
# string in a router looks exactly like any other string, so the boundary is
# swept rather than remembered -- comments keep their worked examples, string
# literals and template copy must read the descriptor. docs/adr/0144.
lint-domain:
	@uv run python scripts/lint_domain.py

# Contract C12: nothing under `api/mcp/` writes. The MCP surface is handed to
# autonomous agents reading adversary-controllable trade press, so "it is
# read-only" is a property to CHECK rather than to remember -- a tool that
# quietly spends the subscription does not announce itself.
lint-readonly:
	@uv run python scripts/lint_readonly.py

# `docs/schema.sql` and `web/src/api/schema.d.ts` are committed and must match
# what the code generates. `lint-ontology` above already guards
# `docs/graph-schema.md`; this is the same gate for the other two.
#
# It exists because CONTRACTS.md C9 and EXTENDING.md have both said "CI fails on
# drift" for the life of the project and there is no CI -- so nothing has ever
# checked. Regeneration goes to a scratch directory and the tree is never
# written: a stale file must FAIL rather than be silently fixed, or the drift
# just moves into an unreviewed diff. `make schema` and `make types` are how you
# update them on purpose.
#
# Every generator's exit status is checked before its output is diffed. A
# generator that dies leaves an EMPTY scratch file, which diffs as "the whole
# committed artifact was deleted" -- so an unchecked failure reports as drift and
# sends you to `make schema`, which would then truncate the real file. Wrong
# diagnosis first, destructive fix second.
#
# `openapi-typescript` is a pinned devDependency and is invoked from
# `node_modules/.bin`, NOT `npx --yes`. Unpinned, `make lint` would resolve the
# latest release on every run: a new version that formats its output differently
# fails the drift check with nothing in this repo changed, and rewrites six
# thousand committed lines as an unrelated diff. It also means the check does not
# need the network, so it cannot quietly degrade to not checking -- which is the
# failure this whole target was added to fix.
lint-generated:
	@tmp=$$(mktemp -d); \
	trap 'rm -rf $$tmp' EXIT; \
	uv run venona dump-schema > $$tmp/schema.sql || { \
	  echo "lint-generated: 'venona dump-schema' FAILED -- this is not drift."; \
	  echo "Do NOT run 'make schema': it would truncate docs/schema.sql."; exit 1; }; \
	diff -u docs/schema.sql $$tmp/schema.sql > $$tmp/schema.diff || { \
	  echo "docs/schema.sql is stale -- run 'make schema' and commit the result:"; \
	  head -40 $$tmp/schema.diff; exit 1; }; \
	uv run venona dump-openapi > $$tmp/openapi.json || { \
	  echo "lint-generated: 'venona dump-openapi' FAILED -- this is not drift."; \
	  echo "Do NOT run 'make types': it would truncate web/src/api/schema.d.ts."; exit 1; }; \
	if [ ! -x web/node_modules/.bin/openapi-typescript ]; then \
	  echo "lint-generated: openapi-typescript is not installed -- run 'cd web && npm ci'."; \
	  echo "This used to SKIP and pass. It does not any more: this target exists"; \
	  echo "because three documents claimed 'CI fails on drift' about artifacts"; \
	  echo "nothing checked, and a conditional skip is that same failure one level"; \
	  echo "down -- a green lint that checked nothing."; \
	  exit 1; \
	else \
	  (cd web && ./node_modules/.bin/openapi-typescript $$tmp/openapi.json -o $$tmp/schema.d.ts) > $$tmp/gen.log 2>&1 || { \
	    echo "lint-generated: openapi-typescript FAILED -- this is not drift:"; \
	    cat $$tmp/gen.log; exit 1; }; \
	  diff -u web/src/api/schema.d.ts $$tmp/schema.d.ts > $$tmp/ts.diff || { \
	    echo "web/src/api/schema.d.ts is stale -- run 'make types' and commit the result:"; \
	    head -40 $$tmp/ts.diff; exit 1; }; \
	fi; \
	uv run python scripts/graph_field.py --check || { \
	  echo "One of the graph-field copies is stale -- run 'make field' and commit the result."; \
	  exit 1; }; \
	uv run python scripts/hub_public_styles.py --check || { \
	  echo "The hub's front-door stylesheet is stale -- run 'make hub-public' and commit the result."; \
	  exit 1; }; \
	echo "lint-generated ok"

# ── generated artifacts (committed; `make lint` fails on drift) ──────
# Built from the app object, NOT from a running server. The old target curled
# http://localhost:8000/openapi.json, which is why it had never once been run:
# it needed `make dev` up, and behind auth it would need a session too. This
# works with no server, no Neo4j and no database -- which is what lets
# `lint-generated` above use it as a drift gate.
# Both write through a temp file and `mv` only on success. `cmd > committed.file`
# truncates the target BEFORE the command runs, so a generator that fails leaves
# a zero-byte artifact where a checked-in one used to be -- and the message
# `lint-generated` prints when a generator dies is "run make schema", which is
# exactly when you must not.
# Chained with `&&`, not `;`. The `mv` must be unreachable when the generator
# fails -- with `;` it still runs and moves the EMPTY temp file over the
# committed one, which is the same zero-byte artifact the redirect produced,
# just one step later. Verified both ways.
types:
	@tmp=$$(mktemp -d); trap 'rm -rf $$tmp' EXIT; \
	uv run venona dump-openapi > $$tmp/openapi.json \
	  && (cd web && ./node_modules/.bin/openapi-typescript $$tmp/openapi.json -o $$tmp/schema.d.ts) \
	  && mv $$tmp/schema.d.ts web/src/api/schema.d.ts \
	  && echo "web/src/api/schema.d.ts written" \
	  || { echo "make types FAILED -- web/src/api/schema.d.ts left untouched"; exit 1; }

schema:
	@tmp=$$(mktemp -d); trap 'rm -rf $$tmp' EXIT; \
	uv run venona dump-schema > $$tmp/schema.sql \
	  && mv $$tmp/schema.sql docs/schema.sql \
	  && echo "docs/schema.sql written" \
	  || { echo "make schema FAILED -- docs/schema.sql left untouched"; exit 1; }
	@uv run venona ontology docs

# ── data ─────────────────────────────────────────────────────────────
seed:
	uv run venona seed

# The trunk of the graph, from `profile/anchors.yaml`. SEPARATE from `seed`
# because the two are for different things and only one is safe on a live
# database: `venona seed` also loads a fictional brief and four invented
# documents, and `venona anchors load` loads neither.
#
# It is wired into `reset` because it was not, and that was a trap. `make reset`
# ran `seed` alone, so a wipe silently discarded every declared entity, alias
# and structural edge -- and the loss is invisible, because the pipeline just
# starts minting its own ids again and the graph looks populated. The corpus is
# simply worse, in a way nothing reports.
#
# `--apply-merges` is deliberately NOT here. Loading structure and collapsing
# entities are different decisions; run it by hand when a `merges:` row is added.
anchors:
	uv run venona anchors load

verify-sources:
	uv run venona verify-sources

record:
	VENONA_LLM_RECORD=1 VENONA_HTTP_RECORD=1 uv run venona run --record

# `data/neo4j` is a BIND MOUNT (see compose.yaml) that the container writes as
# root, so `docker compose down -v` leaves it behind -- `-v` removes named
# volumes, not bind mounts -- and a plain `rm -rf` fails with "Permission
# denied" for every file the container created. This target could never
# complete; it deleted venona.db and then aborted, leaving a half-reset state.
#
# A throwaway root container removes what a root container created. Nothing else
# here needs privileges. Postgres deliberately uses a NAMED volume, so `down -v`
# is the whole reset for the system of record and needs no such workaround.
reset:
	$(MAKE) reset-bare
	$(MAKE) seed
	$(MAKE) anchors

# What a REAL instance starts from: the trunk, and nothing invented.
#
# `reset` is the development path and runs `seed`, which is the right default
# for working on the UI -- but `seed` also loads a fictional brief and four
# invented documents, and a reader's first brief must not be able to cite one.
# `bootstrap` is the same wipe with only `profile/anchors.yaml` on top: 264
# declared entities, their aliases, and the structural edges between them.
#
# No definitions are seeded by either path. Invariant 3 holds: an entity here is
# an identity, and it renders as visibly undefined until a real document defines
# it.
bootstrap:
	$(MAKE) reset-bare
	$(MAKE) anchors

# Empty schema, no fixtures. What a first-ever run on a new machine sees.
#
# `make reset` seeds 37 entities, 147 aliases, 10 glossary entries, 4 documents
# and a fixture brief, which is the right default for developing the UI -- but
# it means the resolver, the annotator and the brief have never been exercised
# cold. This target is that test.
#
# Know what it removes. `ambiguous_surfaces` is seeded (SDA, TITAN, EW,
# Defence, "the Navy" ...), and it is the resolver's FIRST guard -- surfaces
# with more than one plausible owner are declined outright. Without a seed that
# guard starts empty, so a cold run will mint entities for surfaces a seeded run
# refuses. That is the honest cold-start behaviour, not a bug, but it is why the
# two runs are not comparable.
#
# Every editable config is a FILE (`profile/`, `config/`), never a database row,
# so all of it is unaffected either way.
# `down -v` takes the postgres named volume with it, which IS the wipe of the
# system of record -- there is no `venona.db` to remove any more. The neo4j
# bind-mount workaround stays; see the comment above `reset`.
# Scaffold a second local instance: the next free port block, its own env
# file, and a blank-descriptor config/profile pair so the setup wizard runs
# against it. `make dev INSTANCE=<slug>` from then on.
new-local-instance:
	@test -n "$(SLUG)" || (echo "usage: make new-local-instance SLUG=<slug>"; exit 2)
	@uv run python scripts/new_local_instance.py $(SLUG)

reset-bare:
	docker compose down -v
	docker run --rm -v "$(PWD)/data/neo4j:/target" alpine:3 \
		sh -c 'rm -rf /target/data /target/logs' || true
	rm -rf data/neo4j data/raw
	$(MAKE) up wait
	uv run venona migrate

clean:
	rm -rf .venv web/node_modules .pytest_cache .ruff_cache

# The graph audit (`.claude/skills/graph-health`). Read-only end to end:
# `graph_health.py` opens the database and never writes. Sections narrow it:
# `make graph-health SECTIONS="splits merges"`.
graph-health:
	uv run python scripts/graph_health.py $(SECTIONS)

# ── the hub (docs/HUB.md) ─────────────────────────────────────────────
# Local, against the dev postgres from `make up`, on $(HUB_DEV_PORT) --
# declared with the other fixed ports at the top, where `make ports` reads it.
# Safe to leave reloading: the hub runs nothing a reload could kill. The
# instance hazard still applies in REVERSE -- editing hub/*.py restarts every
# instance's uvicorn too, because --reload watches the whole working tree.
hub-dev:
	uv run hub migrate
	uv run uvicorn hub.main:app --reload --port $(HUB_DEV_PORT) --app-dir hub
