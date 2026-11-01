"""Every statement that scheduler runs.

Serves `scheduler.py`. The module keeps its
decisions; this holds the reads and writes they are made of. See
`db/repositories/__init__.py` for the rules.
"""

from __future__ import annotations

from venona.db import core as db


def select_runs(conn: db.Conn):
    return conn.execute(
        "SELECT started_at FROM runs WHERE kind = 'daily' AND status LIKE 'succeeded%' "
        "ORDER BY started_at DESC LIMIT 1"
    )


def select_source_health(conn: db.Conn):
    return conn.execute("SELECT source_id FROM source_health WHERE auto_disabled = 1")


def select_recent_daily_outcomes(conn: db.Conn, p0):
    """The last N finished dailies, newest first, for the quiet-nights alarm.

    `status LIKE 'succeeded%'` for the same reason `select_runs` uses it: a run
    that failed or was cancelled says nothing about how quiet the news was, and
    counting it as a quiet night would raise the alarm on an outage instead of
    on a hollowing corpus.
    """
    return conn.execute(
        "SELECT outcome FROM runs WHERE kind = 'daily' AND status LIKE 'succeeded%' "
        "ORDER BY started_at DESC LIMIT ?",
        (p0,),
    )
