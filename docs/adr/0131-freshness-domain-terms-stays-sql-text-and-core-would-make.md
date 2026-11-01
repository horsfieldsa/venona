---
status: accepted
date: 2026-08-17
---

# 2026-08-17 — `freshness.DOMAIN_TERMS` stays SQL text, and Core would make it worse


The Postgres plan named this as "the natural first thing to express in
SQLAlchemy Core" — it is a dict of SQL fragment strings flattened into one
`SELECT` at runtime, which makes it the closest thing in the codebase to a query
builder, and the repository boundary landed everywhere else. Not doing it is a
decision, so here it is.

**The plan gave two reasons and one of them is already spent.** The `rowid`
fingerprint terms did need replacing — Postgres has no `rowid` — and they were,
during the port: the `runs` domain's `MAX(rowid)` became `COUNT(*)` on an
append-only table, with a comment recording that the two are equally sensitive
there and that this one is a sequential count rather than an index peek. That work
is done and it did not need Core.

**The remaining reason was "it looks like a builder", and what it actually is is a
TABLE OF REASONS.** Each domain pairs an insert-sensitive term with an
update-sensitive one, and the update-sensitive half differs per domain because
that is the entire content of the module: `alias_conflicts` moves on
`resolved = 0` because a verdict resolves a row without changing the count;
`entities` needs `MAX(last_seen)` because `promote_provisionals` flips a status
invisibly to any count, plus a third term on `entity_mappings` because a merge
changes what every id RESOLVES to without touching the rows a reader is looking
at. The docstring says a generic "count every table" helper would quietly lose
that, and a Core rewrite is that helper with types.

What Core buys is a column that no longer exists failing where the query is built.
These fragments name five tables and nine columns, all of them counted or
`MAX`-ed, none of them selected into a shape anything unpacks — so the failure it
prevents is one this code cannot have, while the comments it would displace are
the only reason anybody can safely edit the table.

**The repository rule is still satisfied**, and that is the part worth being
precise about: the fragments do not execute here. `repo.domain_stamps` runs them,
inside `db/repositories/`, with every value bound as a parameter. What crosses the
boundary is composition, not execution.

**Recorded as a hole in the linter rather than hidden by it.**
`scripts/lint_repositories.py` reads the argument to `execute`, so a fragment a
repository assembles is invisible to it. Its docstring now names the two that
exist — this and `pipeline/brief.py`'s funnel `stages`, where each stage must be
counted against the ones before it — with why each is genuinely a per-caller shape.
Tightening the check to ban the word SELECT anywhere would catch these two and
every docstring that explains a query.

*Trigger to revisit:* a second reader of these aggregates — a per-domain endpoint,
or scoping them per workspace. A fragment that has to be filtered by a
`workspace_id` is a fragment that has to be composed by something that knows about
scope, and that is Core's argument rather than tidiness.
