---
status: accepted
date: 2026-09-01
---

# 2026-09-01 — The instance's own settings.yaml is the one the app reads, and it is migrated forward like the schema

Production had never read its configuration file. Not a stale copy: none of it,
for its whole life.

`get_settings()` loaded `REPO_ROOT / "config" / "settings.yaml"`, a module
constant, while every sibling document resolved through `settings.config_dir` --
`ontology.py`, `domain.py`, `sources/registry.py`, and all of `profile/`.
`config_service._path_for` wrote the settings document to `config_dir` like the
rest. So `settings.yaml` was the one document whose reader and writer disagreed,
and in production they disagreed completely: `VENONA_CONFIG_DIR=/var/data/config`,
the image ships `config/` to `/app/defaults/config/`, and the API image
creates `/app/config` empty. The path the loader read did not exist, `_load_yaml`
returned `{}` without a word, and every setting was a code default.

The proof is two screens disagreeing: the disk file said `pinned_version:
"2.1.220"`, `/api/health` reported `2.1.251`, and `2.1.251` is the default in
`LlmSettings`.

What that cost, none of which was visible: saving the schedule in Admin wrote the
file, then `_settings_saved` rebound `app.state.settings` from the unread path
and handed *that* to `apply_schedule`, so a changed cron reverted to the code
default while the rail said "saved". Every locally scaffolded instance had the
same split, so `toydom` and `testdom` ran on the defense instance's settings
while correctly reading their own ontology, sources and domain. And three
statements in this repo were false in production: ADR 0081's "the one document
whose save reaches into the running app", `configHints.ts`'s "Most fields apply
as soon as you save", and RUNBOOK's "edit it in Admin -> Settings".

a self-hosted deployment never had it -- it mounts the volume at `/app/config` and
sets no `VENONA_CONFIG_DIR`, so reader and writer coincide -- which is why the
one deployment shape anyone tested locally was the one shape that worked.

## Decisions

**`get_settings()` reads `config_dir / "settings.yaml"`.** `config_dir` is
itself a settings field, so the five path fields move to a `Paths` base that
reads the environment and defaults only, and `settings_path()` is the single
answer both the loader and the rail resolve through. A path field *inside* the
document is now refused rather than honoured: the file is found at a path built
from those, so a copy inside it could only disagree with where it was just read
from. Every load error names the resolved path, which alone would have made this
self-reporting.

**One file per instance, brought forward by numbered migrations.** The
alternative was a merge, and a merge has exactly two inputs and can therefore
express one thing: add what is missing. It cannot tell a value the operator
chose from a default that was current when the instance was scaffolded, so a
changed default drifts silently and permanently. It has no way to say a key is
gone, or renamed -- both of which leave a key behind that `extra="forbid"`
refuses to boot on, which is one release taking every instance down at once. A
numbered series turns each of those into a decision an author makes once and a
reviewer reads, which is the argument `db/migrations/` already makes for the
schema.

**The version marker lives in the document**, as `settings_version`. It cannot
live in Postgres: the DSN comes from `Settings`, which comes from the file being
migrated, so this runs before `db.migrate` and has no database to ask. Three
reasons it belongs there anyway -- a reset or restored database would replay the
series against a file already at head; applying and stamping become one
`os.replace` rather than two commits in two systems; and a file copied to another
instance or restored from `.history/` carries its own version. `ontology.yaml`
already versions itself the same way.

**Every operation is idempotent**, so the marker is an optimisation rather than
the correctness argument. A marker lost to a hand edit costs a redundant pass.
That is why a save which merely omits the marker is allowed, and only a retyped
one refused.

**Operations edit text, checked against a parsed shadow.** A `safe_load` /
`safe_dump` round trip deletes every comment and reorders every block, and the
comments are where each knob explains what it costs -- `add_to_stoplist` reached
the same conclusion for `lexicon.yaml`. So the ops splice lines, and the runner
applies each one twice, once to the text and once to a dict, and refuses to write
unless the two agree. Every way a splice can be wrong lands there, before the
file is touched. Comment ownership walks backwards over contiguous `#` lines and
stops at a blank line, which is what lets `outage_wait_seconds` keep the
explanation sitting between it and the key above it.

**The environment stops layering into the tuning groups.** Deleting
`env_nested_delimiter="__"` is the whole change; nothing in the tree ever set
such a variable. One answer per setting is the point: a knob the rail reports as
saved must not be quietly outranked by something outside the document. The
environment keeps what cannot live in an editable file -- where the files are,
the database, Neo4j, the auth secrets, and the `VENONA_OFFLINE` switch.

**The deploy-owned fields are migrations, not a standing rule.**
`llm.pinned_version` is in `FROZEN_FIELDS` and cannot be changed over HTTP, so an
instance's copy is only ever whichever version shipped with it. Migration 0002
sets it, with a literal rather than a model default, because a historical fact
that reads a moving value means something different every time it is replayed. A
test holds the head state equal to the current default, so the next pin bump
fails the build until its own migration exists.

**`settings.yaml` leaves `scripts/config_sync.py`.** Pushing the repo's copy over
an instance's would discard the values its operator chose and rewind the marker.
It is now the one document a deploy does deliver.

## Prior art, and what we are not

dpkg conffiles record the shipped file's checksum and prompt a human on
conflict: the checksum is a sidecar and the prompt needs someone at a terminal,
and a container restart has neither. Helm ships chart defaults and takes operator
overrides in a separate file, which is the two-file split we did not want. Rails'
`app:update` is an interactive three-way merge. Django, Sentry and Discourse keep
defaults in code and only overrides in the store, which dissolves the add and
changed-default cases entirely -- Discourse goes further and has real
`rename_setting` and `remove_setting` migrations for its settings table.

**We are closest to Discourse, against an annotated file instead of a table, with
the marker inside the document the way `kubectl apply` keeps
`last-applied-configuration`.**

**The road not taken** is the sparse overrides file. It is genuinely less
machinery: an instance holding only what its operator changed makes adds and
changed defaults free, and leaves only renames and removals needing migrations.
We are not doing it because the document should be the complete, readable,
annotated answer for all 57 knobs, and those comments are the documentation for
this system. That is a real trade and the next person deserves to see it stated.

## Consequence

Nothing changes behaviour on the day this deploys except the pin. `venona config
diff` against the captured files from production, toydom and testdom reports
`llm.pinned_version` as the only value any of them holds away from stock, and
migration 0002 answers it.

Rollback is safe, and the defect is why: code rolled back to before this ignores
the instance file entirely, and `config/settings.yaml` is git-tracked so a
checkout rollback brings the old one. No two-step deploy.

`/api/health` now reports which config directory was read and what version it is
at. This was invisible for months because nothing said which file anything was
agreeing about.

Amends 0081. Does not touch the other eleven editable documents, which stay on
`make render-config-push`.
