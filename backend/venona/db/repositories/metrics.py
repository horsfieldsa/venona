"""Every statement that metrics_service runs.

Serves `services/metrics_service.py`. The module keeps its
decisions; this holds the reads and writes they are made of. See
`db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def cost_by_day(conn: db.Conn, *, since: str) -> list[db.Row]:
    """What this instance has spent, per day, newest first.

    EVERY OTHER READ IN THIS MODULE IS `*_by_run_id`, which is the gap: the
    `llm_calls` ledger has carried `cost_usd` and `created_at` since it existed
    and nothing has ever asked it an instance-wide question, so "what has this
    cost me this month" had no answer anywhere, CLI included.

    `created_at` is ISO-8601 TEXT, so the day is its first ten characters rather
    than a `date_trunc` -- the column is not a timestamp and casting it would
    make the index unusable for the range. `since` is an ISO-8601 floor computed
    by the CALLER, not a day count computed here: this package is not allowed to
    ask what time it is, and a repository that did would be untestable without
    freezing the clock. Bounded at all because a ledger grows forever and a
    dashboard should not read all of it to draw thirty bars.

    A READ, and it stays one. `llm/budget.py` argues at length that measuring is
    not managing; nothing downstream of this caps, alerts or refuses.

    GROUPED BY `cost_basis` AS WELL AS BY DAY, because one column holds four
    incompatible kinds of number and adding them together means nothing: a
    subscription's shadow price (what the work WOULD have cost), a metered
    provider's actual bill, tokens multiplied by a price table this repo
    maintains, and a service that reports no figure at all. Summing across an
    instance that switched mid-month produces a total that is not any
    quantity -- not what was spent, not what was saved. A row whose basis is
    NULL predates the column and is reported under `unknown` rather than folded
    into whichever basis happens to be first.
    """
    return list(
        conn.execute(
            "SELECT substr(created_at, 1, 10) AS day, "
            "       COALESCE(cost_basis, 'unknown') AS cost_basis, "
            "       SUM(cost_usd) AS cost_usd, "
            "       COUNT(*) AS calls "
            "  FROM llm_calls "
            " WHERE created_at >= ? "
            " GROUP BY substr(created_at, 1, 10), COALESCE(cost_basis, 'unknown') "
            " ORDER BY day DESC, cost_basis",
            (since,),
        ).fetchall()
    )


def select_run_metrics_by_run_id(conn: db.Conn, p0):
    return conn.execute("SELECT stage, key, value FROM run_metrics WHERE run_id = ?", (p0,))


def select_run_stages_by_run_id(conn: db.Conn, p0):
    return conn.execute("SELECT * FROM run_stages WHERE run_id = ? ORDER BY started_at", (p0,))


def select_llm_calls_by_run_id(conn: db.Conn, p0):
    return conn.execute(
        "SELECT task, stage, duration_ms, cost_usd, status, input_tokens, "
        "       output_tokens, cache_read_input_tokens "
        "FROM llm_calls WHERE run_id = ?",
        (p0,),
    )


def select_run_source_stats_by_run_id(conn: db.Conn, p0):
    return conn.execute("SELECT * FROM run_source_stats WHERE run_id = ?", (p0,))


def delete_rejected_shapes_by_run_id(conn: db.Conn, p0) -> None:
    """A re-entered analyze pass REPLACES its shapes rather than accumulating:
    under upsert alone, a shape recorded before the ontology was widened would
    linger on the run forever."""
    conn.execute("DELETE FROM run_rejected_shapes WHERE run_id = ?", (p0,))


def select_rejected_shapes_by_run_id(conn: db.Conn, p0):
    return conn.execute("SELECT * FROM run_rejected_shapes WHERE run_id = ? ORDER BY n DESC", (p0,))


def select_rejected_shapes(conn: db.Conn):
    """One row per SHAPE, already totalled, carrying its most recent example.

    Grouped in SQL because the table gains a row per rejecting run for ever,
    while the number of distinct shapes is bounded by the ontology -- so this
    answers in a handful of rows no matter how long the instance has been
    running. The totals have to be computed across every row before any bound
    can be applied, which is why a plain LIMIT would be wrong here.

    `DISTINCT ON` keeps the newest row of each partition (the `at DESC` in the
    ORDER BY), while the window sums span the whole partition -- window
    functions run before `DISTINCT ON` picks.
    """
    return conn.execute(
        """
        SELECT DISTINCT ON (predicate, subject_type, object_type)
               predicate, subject_type, object_type,
               SUM(n) OVER shape AS n,
               COUNT(*) OVER shape AS runs,
               example_subject, example_object,
               run_id, at
          FROM run_rejected_shapes
        WINDOW shape AS (PARTITION BY predicate, subject_type, object_type)
         ORDER BY predicate, subject_type, object_type, at DESC
        """
    )


def select_llm_calls_by_run_id_2(conn: db.Conn, p0, p1):
    return conn.execute(
        "SELECT subject, task, duration_ms, cost_usd FROM llm_calls WHERE run_id = ? "
        "AND subject IS NOT NULL AND status = 'ok' ORDER BY duration_ms DESC LIMIT ?",
        (p0, p1),
    )


def select_llm_calls_by_run_id_3(conn: db.Conn, p0):
    return conn.execute("SELECT COUNT(*) AS n FROM llm_calls WHERE run_id = ?", (p0,))
