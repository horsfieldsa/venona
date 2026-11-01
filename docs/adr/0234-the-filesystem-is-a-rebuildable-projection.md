---
status: accepted
date: 2026-09-14
amends: 0148
---

# 2026-09-14 — The filesystem is a rebuildable projection

The api's persistent disk is not a requirement. It carries the four writable
locations the entrypoint names, and only two things on them cannot be rebuilt:
the authored documents under `config/` and `profile/`, and the one credential
file inside each model service's home. Everything else is a prompt cache, a
regenerable export, or a CLI's own catalog and session state. Both survivors
become rows, and the directories they are read from are written out of those
rows at boot.

Invariant 1 already says this about the graph. It is the same sentence:

> Postgres is the system of record. The config filesystem is a rebuildable
> projection.

## Why it matters that the disk goes

A disk disables zero-downtime deploys, forbids more than one instance, and
makes blueprint validation reject `maxShutdownDelaySeconds` — the three
constraints `docs/pm/epics/i-render.md` records as settled facts about the
platform. They are facts about *a service with a disk*, not about this service.

Measured on `dep-dajnnrek1f9s7380d7l0`: `Shutting down` at 04:46:20.599, a
listening socket again at 04:46:37.341. **16.7 seconds with nothing answering**,
about twenty times a week.

## The measurement that decided the credential half

The objection to removing the disk is that a subscription credential rotates,
so the CLI must own a directory that survives a deploy. True — but the
directory is enormous and the credential is not. On a workstation,
2026-09-14:

| service | home | the credential | share |
|---|---|---|---|
| Claude Code | `~/.claude`, **1.5 GB** | `.credentials.json`, **2,676 B** | 0.0002% |
| Codex | `~/.codex`, **138 MB** | `auth.json`, **4,178 B** | 0.003% |

Both are single top-level files. The rest is a models catalog fetched over the
network, SQLite session state, logs and history — which the API image already
calls regenerable. So persist the credential and let the home be ephemeral.
Today a lost disk costs a sign-in; after this it costs nothing.

*Marked as inference:* the sizes are from a workstation, not from the
instance-scoped homes under `data_dir`. The file **names** are properties of the
CLI rather than of the host, which is what the seam depends on; the sizes are
indicative only.

## Why this is cheap, and the alternative that lost

Every harness already declares `home_env` — `CLAUDE_CONFIG_DIR`, `CODEX_HOME`,
Grok's equivalent — and every driver already sets it before spawning. The
location of each store has been ours all along; only its durability was
missing. And `config_service` is already the only writer of an editable
document, `harness_service` already the only reader or writer of a credential.
Both changes land inside a module that exists to be the single place.

**Rejected: split the api into a read plane and a run plane, and let the run
plane keep the disk.** It buys the same zero-downtime, costs $25/mo and a
forwarding layer for thirteen POST routes, and *still* needs the config half of
this record, because a stateless web plane has to read `profile/` too. It
remains the right answer to a different problem — a run blocking a reader's
event loop, which the 2026-09-13 `curate` incident showed is real — and it is
epic AA's T3. It is not the answer to the disk.

**Rejected: port `config_service` to SQL.** `settings.py::get_settings`,
`get_ontology`, `get_domain`, `load_rubric`, the file lock, `history()` and the
`.history/` prune all read paths. Materializing keeps every one of them
untouched; porting rewrites the module and its seven callers to buy nothing
extra.

## Consequence

- `config_service.write_doc` upserts a row beside the file write. A
  `materialize()` in the lifespan writes every `EDITABLE` document out of its
  row before anything reads one, falling back per document to the image's
  `/app/defaults/` — replacing the entrypoint's `cp -rn`, which exists for
  exactly that purpose. `.history/` snapshots become rows keyed by version.
- `Harness` gains `credential_paths`: the relative files under its home that
  carry authentication and nothing else. `harness_service` gains restore
  (inside `ensure_dirs`, before any child is pointed at a home) and capture
  (after a call, and on the sixty-second sweep that catches a `codex login`,
  which has no call after it because `Codex.login()` returns None by design).
  The blob sits beside `harness_credentials.secret`, under the same rule: no
  route returns it.
- **A harness that declares no `credential_paths` is not captured**, and
  behaves exactly as it would with no disk. The fallback is never worse than
  the thing being replaced.
- `auth_epoch` becomes a row. The prompt cache moves to the container
  filesystem, which is what it always wanted: it exists because argv has a
  per-element ceiling, and it is rewritten per call.
- **Exports become bytes in Postgres, NOT a regeneration.** This record's first
  draft said regenerate on request, and `data_exports`' own DDL comment refutes
  it — *"A second download must return the bytes somebody already has;
  rebuilding over a corpus that has moved since would hand back a different
  folder under the same name, which is the one thing a dated archive promises
  not to do."* Determinism makes an archive reproducible from the same corpus,
  and the corpus is the thing that moves. Measured: one export exists at 1.89 MB
  on a 98 MB database, so the bytes fit easily and `FileResponse` becomes a
  response over a `bytea` column.
- the deployment config loses the **api's** `disk:` and gains
  `maxShutdownDelaySeconds: 300`; `HOME` reverts to the image's. The Neo4j
  private service keeps its disk. Invariant 1 makes the graph rebuildable, not
  its storage pointless -- a rebuild is hours of model spend, and nothing
  reader-facing waits on that service's deploy.
- **The new hazard, and it would be silent:** two instances overlap for sixty
  seconds during a zero-downtime deploy, and both capture. The upsert is
  idempotent for equal bytes, but a draining instance must never write an empty
  home over a good blob. Restore-then-capture ordering has to make "nothing
  there" mean "write nothing". It gets its own test.

## Sequencing: this is two deploys, not one

The first deploy keeps the disk and starts writing rows — documents on save,
the existing `.history/` imported on boot, credentials captured. The second
removes the disk. Between them, check the rows are populated.

One deploy means the disk is detached at the moment the rows are still empty,
so the instance boots on image defaults with the operator's authored documents
nowhere, and rolling back does not recover them.

**Done 2026-09-14, and the platform half was not what this record assumed.**
Stage one adopted 12 documents and 23 saved versions, each carrying its own
mtime off the live disk. Stage two could not be done by blueprint at all: a sync
carrying the removal AND `maxShutdownDelaySeconds` was refused whole, because a
blueprint is validated against the service as it stands -- and with the setting
held back, the sync still did not detach the disk, silently. The volume was
deleted through the API instead. **A probe every 0.5s across the next deploy
recorded no non-200**, against the 16.7 seconds above.

## What went wrong doing it, 2026-09-14

**The disk was deleted while the credential was still on it, and defense's
Codex sign-in went with it.** No reader saw it -- the daily had run at 13:47
and the next was a day away -- but any spend would have failed until a
re-login.

The record above reasons throughout from `harness: "claude_code"`. That is what
the REPOSITORY's `config/settings.yaml` says. Defense runs **Codex**, and its
own settings document had said so for weeks. The divergence was even measured
during the migration and reported as proof the adoption worked, without anybody
asking the next question.

Codex signs in with `codex login` in a shell, which leaves `auth.json` in the
CLI's default home under `$HOME`. `credential_for` points the child at Venona's
instance-scoped home only once Venona has written there, so on a
shell-signed-in instance that directory is empty -- and `_credential_files` read
only that one. Capture found nothing across twenty minutes and a scheduler
sweep, and `harness_homes` sat at zero, which was read as "nothing to keep".

**An empty table is not evidence that there was nothing to capture.** It is
equally evidence of looking in the wrong place, and the two are
indistinguishable from the outside. `signed_in_home` now falls back the way
`_home_in_use` always did for the confinement; two tests fail against what
shipped.

The verification that would have caught it costs one query: ask the INSTANCE
which service it runs, rather than the repository.

**Reversal trigger.** A harness whose credential cannot be isolated to a
declared file set — entangled with its session store, or spread across files it
rewrites on every call. That harness needs T3's run plane, not a disk back on
the api.
