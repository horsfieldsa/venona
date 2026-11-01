---
status: accepted
date: 2026-09-17
---

# 2026-09-17 — A release is a number, plus the commit it actually shipped

`pyproject.toml` said `version = "0.1.0"` from the first commit to this one.
`backend/venona/main.py` said it again as a literal. `hub/pyproject.toml` said
it too. `extension/manifest.json` said `1.0.0` and `extension/package.json`
said `0.2.0`, and nothing compared any of them.

This mattered more than a stale number usually does, because one of those
values was already on a screen. `GET /api/mgmt/status` has reported `version`
since the hub existed, the poller stores the envelope verbatim, and
`hub/web/src/views/DomainsView.vue` renders a **Version** row per domain. The
machinery was complete and correct. It reported `0.1.0` for every domain,
forever.

`api/routers/mgmt.py` had the right instinct and stopped one level short:

> Read from the installed package rather than typed here, so the hub sees the
> version this instance actually runs. A literal is a claim nothing updates.

It read the package instead of a literal. The package's number was the literal.

## Two fields, because neither is sufficient

**A release number alone cannot show drift.** Marketing, the hub and each
instance deploy independently from `master` on their own `buildFilter` paths, so
at any moment they can be running different code. But `VERSION` is one number
for the whole repo, so between bumps every surface reports the same thing —
which is exactly when they are most likely to disagree.

**A commit alone is not something a person can say.** It answers "which code"
and nothing about "which release", and a seven-character hex string is not what
anybody puts in a sentence.

So both travel: `Version 1.0.0 · build a1b2c3d`. The release comes from
`VERSION`, propagated by `scripts/version.py` into the two pyprojects, the
extension manifest and its `package.json`, and held equal by `make
lint-version`. The commit comes from `RENDER_GIT_COMMIT` at runtime, with
`VENONA_BUILD_COMMIT` / `HUB_BUILD_COMMIT` overrides so the production path can
be exercised before a deploy.

**`uv.lock` is the fifth declaration**, and the one that would have been
missed. Both images install `--frozen`, so the lockfile is what actually ships;
and any bare `uv run` after a bump rewrites it, which turns a forgotten `uv
lock` into an uncommitted diff rather than a failure. `make version` runs the
lock and `make lint-version` checks it.

## `started_at`, not `built_at`

The host exposes no build timestamp to a Docker build. The obvious workaround is a
`RUN date -u > /app/BUILD` layer, and it does work — a RUN's cache key includes
its parent layer, so any commit that changes the service invalidates it.

It was still the wrong field. What an operator wants to know is how long the
reported build has been serving, and since an immutable-image deploy restarts
the process, **process start answers that directly** — with no image
change, no layer whose correctness depends on where it sits, and no way for it
to be subtly stale. Stamping it at import is safe here: uvicorn's reloader
re-imports in the child, and neither service runs multiple workers
(`tests/test_single_process_assumptions.py`).

Marketing is the exception and reports `built_at`, because it has no process at
all. Two different words for two different facts, rather than one word stretched
over both.

## The site publishes a file; the hub polls it

A static site cannot answer a question, so the old landing site writes
`/version.json` at build time and the hub's poller reads it.

Read on the cycle, not on the page load. `ops_api.domains` is emphatic about
why, and it applies unchanged here: a read path that fans out takes the slowest
target's timeout as its own and shows a blank screen exactly when something is
down. The reading is stored in `site_status` — a one-row table, not columns on
`hub_settings`, which holds switches an operator **throws** rather than facts
the hub **observed**.

A failed read leaves the stored row alone. A site that did not answer this cycle
is not a site with no version, and blanking the page at the first blip would
lose the only record of what is published. That is a property of the write; the
separate and more important one is that the fetch never takes the cycle down —
`poll_once` runs the entitlement push, the Stripe reconcile and the notice drain
after the status pass, so an error escaping here would cost an operator their
alerts in order to report a version nobody is waiting on.

**No notice fires when the site goes quiet.** `instance.unreachable` exists
because a dark domain locks readers out; a static site missing one read does
not, the host alerts on a service that is actually down, and an alarm that fires
on a CDN blip is the kind that is ignored by the time it matters. A stale
reading on the page is the signal. This is a decision, not an omission.

## `1.0.0`, not `0.2.0`

`docs/pm/epics/f-extension.md` records `1.0.0` as a deliberate store-hygiene
choice for the extension manifest, and Chrome refuses a version that moves
backwards. Unifying downward would have undone that for nothing. `1.0.0` is also
the honest number for a product with production SSO, billing, a hub and a live
front site — `0.1.0` was never a judgement about maturity, it was the
default nobody revisited.

## Where it shows

| Surface | Where | Reports |
|---|---|---|
| Instance | `/admin`, foot of the overview | its own release, build, uptime |
| Hub | `/admin`, "This hub" | its own, and a separate front site's |
| Hub | `/admin`, domains table | each domain's release, at a glance |
| Hub | `/admin/domains` | each domain's build and start, in full |

The instance's own `/admin` is the gap this closes that was least obvious: the
build has been on the wire since the hub existed, but `/api/mgmt/*` admits only
a `vmgmt_` bearer, so the person running the instance was the one party who
could not see which version they were running.

It rides `/api/admin/overview` rather than `/api/health` for two reasons.
Health is in `authz.READER_ROUTES`, so a build identity there would be
reader-visible; and that route is declared `response_model_exclude_unset=True`,
which silently drops a field a handler forgets to set.

## What holds it

- `make lint-version` — `scripts/version.py --check` plus `uv lock --check`.
  Its own target rather than part of `lint-generated`: these are authored files
  with one propagated field, not generated artifacts.
- `tests/test_version.py` — every declaration equals `VERSION`, the check
  **fails** rather than repairing, and the pyproject splice cannot hit
  `[tool.ruff]`'s `target-version`.
- `tests/hub/test_ops.py::test_the_domains_readout_carries_every_field_the_poll_returns`
  — the cross-package ratchet that was already there. It caught both new fields
  the moment they were added to `MgmtStatus`, which is what it was built to do.
- `VERSION` is in all three `buildFilter` lists. Marketing is the one that needs
  it: a bump rewrites the pyprojects and the lockfile, which the api and hub
  filters already name, but touches nothing under the old landing site.
