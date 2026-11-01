---
status: accepted
date: 2026-08-06
---

# 2026-08-06 — Screenshots are not verification


Three defects reached the user in one session, and all three were obvious the
moment anybody opened the app:

- the graph canvas rendered 229 nodes at a zoom where every one was sub-pixel,
- four different control heights sat in one 44px header bar,
- every prompt file was permanently marked "unsaved" because the draft watcher
  wrote a `localStorage` key on *every* change to `draft` — including the
  assignment inside `select()`, where the draft is by definition identical to
  the file just loaded.

None was catchable by `npm run typecheck`, `make lint` or the offline suite, and
static screenshots caught none of them either — the first because a blank canvas
and a correctly-rendered one at 0.05 zoom look the same, the others because
nobody was interacting.

**Consequence:** `scripts/ui_smoke.py` and `make ui-smoke`. It drives a headless
Chromium over CDP against a running `make dev` and asserts behaviour: that keys
fire, that Esc pops the right thing, that the graph draws nodes *inside the
viewport*, that header controls share a height, that a file opened is not a file
edited. Deliberately outside `make test`, which stays offline and free.

The rule that comes with it: **when a defect gets past you, the fix includes a
check here.** The first run of the third pass immediately found one nobody had
reported — see below.
