"""`db.migrate(conn)` must leave the process's logging alone.

Alembic's `env.py` template calls `logging.config.fileConfig(alembic.ini)`,
which does two things to a process that already configured logging: it
DISABLES every existing logger not named in the ini (`disable_existing_loggers`
defaults to True), and it replaces the root handlers with the ini's console
handler at the ini's root level -- WARNING. The API's lifespan migrates at
startup, the scheduler migrates at 06:00, and `run_launcher` migrates per run;
after the first of those, `venona.scheduler`, `venona.llm` and even
`uvicorn.error` were dead. Two days of production logs in production held four runs
and not one line from the application, while the circuit breaker's
"circuit OPEN" and the scheduler's "could not start" had nowhere to go.
"""

from __future__ import annotations

import logging

from venona.db import core as db


def test_migrate_does_not_disable_existing_loggers_or_replace_the_root(
    database_url: str,
) -> None:
    probe = logging.getLogger("venona.test_probe_logger")
    probe.disabled = False
    root = logging.getLogger()
    handlers_before = list(root.handlers)
    level_before = root.level

    conn = db.connect(database_url)
    try:
        db.migrate(conn)
    finally:
        conn.close()

    assert probe.disabled is False, "fileConfig disabled a pre-existing logger"
    assert logging.getLogger("venona.scheduler").disabled is False
    assert list(root.handlers) == handlers_before, "migrate replaced the root handlers"
    assert root.level == level_before


def test_the_json_timestamp_carries_milliseconds_in_utc() -> None:
    """⭐ `strftime` has no fractional-second directive.

    The formatter asked for `%Y-%m-%dT%H:%M:%S.%03dZ`, and glibc reads `%03d`
    as a width-3 `%d` -- the DAY OF THE MONTH. So every production line ended
    `.026Z` on the 26th, whatever the time, and sub-second ordering was gone
    from the only logs anyone reads remotely. The default converter is
    `time.localtime` besides, so the `Z` was a lie on any host not set to UTC.

    JSON mode is production-only (`as_json` when stderr is not a TTY), which is
    why nothing on a workstation ever showed it.
    """
    import json as json_mod
    import time as time_mod

    from venona.logging import JsonFormatter

    record = logging.LogRecord(
        name="venona.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    # 2026-08-26T09:15:30.123Z, stated as a UTC epoch so the assertion does not
    # depend on the machine's timezone.
    record.created = 1787822130.123456
    record.msecs = 123.456

    payload = json_mod.loads(JsonFormatter().format(record))
    expected = time_mod.strftime("%Y-%m-%dT%H:%M:%S", time_mod.gmtime(record.created)) + ".123Z"
    assert payload["ts"] == expected
    assert payload["ts"].endswith(".123Z"), "the fraction must be milliseconds, not the day"
