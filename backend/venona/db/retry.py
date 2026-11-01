"""Retrying the Postgres failures that are worth retrying.

CLASSIFIED BY SQLSTATE, NOT BY SUBSTRING. `psycopg.Error` carries `sqlstate`, a
five-character code the server assigns, and matching on it means this does not
quietly stop working when a message is reworded — the mistake `llm/budget.py`
makes with its retryable-error list, where the string `"500"` matches any message
containing those three digits.

Three codes matter:

    40001  serialization_failure   two transactions touched the same rows and the
                                   server could not order them. Retrying is the
                                   documented response, not a workaround.
    40P01  deadlock_detected       a cycle; the server already chose a victim.
    55P03  lock_not_available      `NOWAIT`, or `lock_timeout` elapsed.

WHAT IS DELIBERATELY NOT HERE. `23505` (unique_violation) is not retryable, even
though the read-then-write races that produce it look transient. Retrying it would
treat a collision as normal and would equally mask a real duplicate-key bug —
`run_service.append_event` takes an advisory lock so the collision cannot happen
instead, which is the fix rather than the mitigation.

NOT a general-purpose retry. It is only correct around a block that is safe to run
twice — which a transaction that rolled back is, by definition, because nothing it
did survived.

WHERE IT IS USED. `pipeline/analyze.persist_cluster`, which is the write two
concurrent lanes are most likely to deadlock on -- both stream entities and
assertions into the same tables, and a lock cycle there costs a cluster's whole
analysis, which is a model call already paid for. Nothing runs at an isolation
level that raises `40001`, so `40P01` is the code that actually fires.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import psycopg

log = logging.getLogger("venona.db")

#: SQLSTATE codes, not exception classes: psycopg maps several of these onto
#: `OperationalError` and the mapping is not the thing worth depending on.
RETRYABLE = frozenset(
    {
        "40001",  # serialization_failure
        "40P01",  # deadlock_detected
        "55P03",  # lock_not_available
    }
)

MAX_ATTEMPTS = 4
BASE_DELAY = 0.05


def is_retryable(exc: BaseException) -> bool:
    """True only for a server-assigned SQLSTATE in `RETRYABLE`.

    No message fallback, deliberately. The SQLite version had one for drivers
    that did not populate `sqlite_errorname`; psycopg always sets `sqlstate` on an
    error that came from the server, and an error that did not come from the
    server (a closed connection, a broken pipe) is not one of these three.
    """
    if not isinstance(exc, psycopg.Error):
        return False
    return (exc.sqlstate or "") in RETRYABLE


def with_retry[T](operation: Callable[[], T], *, what: str = "write") -> T:
    """Run `operation`, retrying the transient serialization failures.

    Linear backoff on purpose. These contend for milliseconds — a writer holds its
    locks for one statement — so exponential backoff would spend more time
    sleeping than the contention lasts. The point is to get out of the way
    briefly, not to shed load.
    """
    for attempt in range(MAX_ATTEMPTS):
        try:
            return operation()
        except psycopg.Error as exc:
            if not is_retryable(exc) or attempt == MAX_ATTEMPTS - 1:
                raise
            delay = BASE_DELAY * (attempt + 1)
            log.warning(
                "postgres %s contended (SQLSTATE %s), retry %d/%d in %.0fms",
                what,
                exc.sqlstate,
                attempt + 1,
                MAX_ATTEMPTS - 1,
                delay * 1000,
            )
            time.sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover
