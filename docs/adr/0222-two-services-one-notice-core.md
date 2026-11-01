---
status: accepted
date: 2026-09-10
---

# 2026-09-10 — Two services, one notice core

A Slack webhook was configured on the production defense instance. Its test
message arrived. Nothing else ever did, including a notice for an access request
submitted twelve minutes later.

The access request had gone to the hub. `POST /request-access` there writes a
row and returns; the hub has no way to tell anybody anything. The notice that
covers it, `access.requested`, lives on the instance and is wired to the
instance's own request form, which is the door nobody uses now that sign-in goes
through the hub.

So the hub has to be able to send. That is the decision, and the interesting
half is what it is allowed to share to do it.

## What is actually being decided

**A third uv workspace member, `notices`, depended on at runtime by both
services.** It holds four things and they were chosen by one test: what is
dangerous to get wrong twice.

- `urlguard` — which addresses Venona will dial. Both services now take a
  webhook URL somebody typed. Two copies of an SSRF guard is not a duplicated
  rule, it is a hole, because copies drift and only one of them gets the next
  fix.
- `untrust.scrub` — making text Venona did not write visible to whoever reads
  it. The same paragraph reaches a model as evidence and a person as the quoted
  half of a notice, and the characters that can hide it are the same set.
- `render` — a notice in the words each channel can carry. `Notice.quoted` holds
  a stranger's words and every channel with markup has to escape them for that
  markup; the copy nobody updated is the injection.
- `channels` — posting it, and saying whether trying again is worth it.

**What deliberately did not move.** The registry of notice types, the
destinations and subscriptions tables, and the outbox. Those are each service's
own. The instance answers to a readership and addresses two audiences; the hub
answers to one operator and has no reader notion at all, so a shared `NoticeType`
would carry an `audience` field that is a lie on one side. The two databases also
speak different placeholder dialects, so the repository layer could not have been
shared without inventing a persistence port to hide the difference — a layer
whose only justification would be the sharing it exists to enable.

`Notice` itself did move, because it is the shape `render` and the channels are
written against and there is nothing service-specific in it.

## The constraint that shaped it

`hub/pyproject.toml` deliberately carries httpx, psycopg, pydantic, cryptography
and uuid7, and nothing else. The instance carries neo4j, trafilatura, selectolax,
sqlalchemy and apscheduler. Making the hub depend on `venona` would drag all of
that into an image that has no use for any of it.

So `notices` declares **no dependencies at all** and the webhook channel takes
its http client as an argument rather than importing one. The instance hands it
`HttpClient`, which resolves the host, re-checks every answer and dials the
address it vetted; the hub hands it its own. The rules are shared, the transport
is not — which is the right split anyway, because the resolve-and-pin logic is
entangled with a 700-line client the notice path has no other use for.

## What this costs

**Two images and two build filters.** `notices/` is copied into both
images. More sharply: both services' `buildFilter` in the deployment config now name
`notices/**`, because a path in neither filter is the silent case — every test
passes, the commit lands, and nothing redeploys. That failure has no error
message anywhere.

**A dependency the hub does not use yet.** It is declared ahead of the hub's own
notifications, in the same slice, rather than left for later.

## What was considered and refused

**A second implementation in the hub.** Faster to write and it needs no
packaging change. It also means two answers to "which status codes are worth
retrying", two escaping routines for a stranger's paragraph, and two SSRF
guards. The first is duplication; the other two are vulnerabilities with a
schedule.

**Sharing only the security-critical parts** — `urlguard`, `untrust`, `render` —
and letting the hub write its own channel. The channel is where the permanent
statuses and the JSON-host list live, and those are as easy to get wrong and as
quiet when wrong.

**Making the hub depend on `venona`.** Ruled out by the dependency set above,
and `pyproject.toml` already records the related hazard: `venona-hub` is
dev-group-only so that the API image can sync `--no-dev` without `hub/`
present. `notices` is the opposite case and both images carry it.

## Consequences

`backend/venona/sources/urlguard.py`, `backend/venona/notify/render.py` and
`backend/venona/notify/channels/__init__.py` are now re-exports under the names
the rest of the package already used, so no call site changed.
`backend/venona/untrust.py` keeps `quote` and `inline` — the `<untrusted>`
envelope is a boundary marker for a model and means nothing to a person — and
takes `scrub` back by name.

`make lint` runs ruff, `lint-comments` and `lint-domain` over `notices` as well.
Not `lint-determinism`: `notify/` is deliberately outside that guard and this is
the same side channel, for the reason `notify/__init__.py` opens with.

See also [0192](0192-a-destination-is-a-row-not-a-document.md), which made a
destination a row, and [0193](0193-a-reader-supplied-url-revokes-the-notify-ssrf-clearance.md),
which is why `urlguard` is on this path at all.
