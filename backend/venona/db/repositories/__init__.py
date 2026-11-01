"""Repositories: the only place SQL is written.

WHY, and it is not "clean architecture". Three concrete things a boundary buys
that a scattered `conn.execute` cannot:

  1. **The read-scope seam.** A `workspace_id` on the corpus tables is
     enforceable only if every read goes through a place that can apply it -- a
     query assembled by `str.format` in a router cannot be scoped by anything.
  2. **The `Conn` shim comes out.** `db/core.py::Conn` translates SQLite's `?`
     paramstyle at execute time and is throwaway by its own docstring.
  3. **Statements become objects.** `db/tables.py` describes the schema to
     SQLAlchemy Core, so a dropped column is an error where the query is built
     rather than a `psycopg.errors.UndefinedColumn` at 3am.

`make lint-repositories` walks every backend module outside this package and
fails on the first one that grows a statement back.

THE RULES:

  * A repository takes `conn` first and returns rows or plain values. It never
    takes a `Request`, never reads `Settings`, and never decides authorization --
    a repository that knows about a caller is one two callers cannot share.
  * SQL lives HERE and nowhere else. Core or plain string is a second question
    with a practical answer: Core where it buys something -- a dynamic column
    set, a scoped read, a composed filter -- and plain SQL where the statement is
    static and clearer than the builder that would produce it.
  * One module per aggregate, named after the table it is mostly about.

Many function names read like the statement they wrap
(`update_alias_conflicts_by_resolution_resolved_at`). Rename them when you are
next in the module for another reason, not in a diff nobody can review.
"""
