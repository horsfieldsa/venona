"""Logging that a person can grep on a machine they are not sitting at.

WHAT WAS THERE. `logging.basicConfig` with a printf format, two loggers, and
five call sites in the whole backend -- all in startup and the scheduler. Plus
one bare `print()` in `graph/projector.py`. Nothing linked an HTTP request to
the run it started, to the model calls that run made, or to the line that failed.

WHAT THIS IS NOT. Not a metrics stack, and not a replacement for the durable
event log. `run_events`, `llm_calls`, `run_stages` and `source_health` remain
where you find out what happened -- they are queryable, they survive restarts,
and the UI is built on them. Logs answer a different question: what was the
process doing at 06:04, and which request caused it.

JSON WHEN NOT A TTY. On a workstation the printf format is more readable and
`make dev` interleaves two processes; in a container the output goes to
`json-file` and then to whatever reads it, where a parseable line is worth more
than an aligned one. So the format follows the destination rather than a flag
somebody has to remember to set.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from contextvars import ContextVar

# Set by the request-id middleware and by the run launcher, so every line
# emitted while handling a request or executing a run carries its id without
# every call site having to pass one.
#
# A ContextVar rather than a thread-local: the app is asyncio, and a thread-local
# would be shared by every coroutine on the loop -- which is to say, wrong in
# exactly the case this exists for.
request_id: ContextVar[str] = ContextVar("request_id", default="")
run_id: ContextVar[str] = ContextVar("run_id", default="")

# `uvicorn.access`: the host's orchestrator hits `/api/health/live` every five
# seconds and the SPA polls `/api/health` every thirty, so the access log is
# almost entirely probes. Errors still arrive through `api/errors.py` with a
# request id; the request volume itself is on the host's metrics.
_QUIET = (
    "httpx",
    "httpcore",
    "neo4j",
    "apscheduler.executors",
    "watchfiles",
    "uvicorn.access",
)


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get("")
        record.run_id = run_id.get("")
        return True


class JsonFormatter(logging.Formatter):
    # UTC, because the `Z` this stamp ends in is a claim and the default
    # converter is `time.localtime`.
    converter = time.gmtime

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        """ISO-8601 with real milliseconds.

        `strftime` has no directive for fractional seconds, so a `%03d` in the
        format string is read by glibc as a width-3 `%d` -- the day of the
        month. Sub-second ordering is what a log is read for when two lines
        share a second, so the milliseconds are appended here instead.
        """
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", self.converter(record.created))
        return f"{stamp}.{int(record.msecs):03d}Z"

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "run_id"):
            value = getattr(record, key, "")
            if value:
                payload[key] = value
        if record.exc_info:
            # The traceback goes HERE and not on the wire. `api/errors.py` hands
            # the caller a request id and nothing else; this is where that id
            # leads.
            payload["traceback"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """The workstation format, with the ids appended only when there are any."""

    def format(self, record: logging.LogRecord) -> str:
        base = f"{record.levelname}:     {record.name}: {record.getMessage()}"
        tags = " ".join(
            f"{key}={getattr(record, key)}"
            for key in ("run_id", "request_id")
            if getattr(record, key, "")
        )
        if tags:
            base = f"{base}  [{tags}]"
        if record.exc_info:
            base = f"{base}\n{self.formatException(record.exc_info)}"
        return base


def configure() -> None:
    """Idempotent. Called from `main.py` and from `cli.py`."""
    as_json = os.environ.get("VENONA_LOG_FORMAT", "").lower() == "json" or not sys.stderr.isatty()

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if as_json else TextFormatter())
    handler.addFilter(ContextFilter())

    root = logging.getLogger()
    # Replace rather than append: uvicorn configures its own, and `make dev`
    # would otherwise print every line twice.
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(os.environ.get("VENONA_LOG_LEVEL", "INFO").upper())

    # These are chatty at INFO and say nothing venona's own logs do not. httpx
    # in particular logs every request, which during a fetch stage is the whole
    # run at one line each.
    for name in _QUIET:
        logging.getLogger(name).setLevel(logging.WARNING)
