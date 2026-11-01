"""ISO-8601 UTC, in one place.

CONVENTION (CLAUDE.md): every timestamp on the wire and in the database is
ISO-8601 UTC with a `Z`. What was never centralised is the PARSING, and the
codebase grew six spellings of it.

THE TWO FORMATS ARE NOT AN INCONSISTENCY TO CLEAN UP. Keep both, and keep them
distinguishable:

    format_seconds()  2026-08-09T06:00:00Z          feed timestamps
    format_millis()   2026-08-09T06:00:00.123Z      anything from ctx.clock

`brief._cutoff_ms` has a nine-line comment about why, and it is worth repeating
here because the failure is silent. These columns are compared as TEXT, and
`'...00.500Z' < '...00Z'` -- because `.` (0x2E) sorts below `Z` (0x5A). So a
second-precision lower bound silently excludes everything generated inside its
own boundary second. Collapsing the two formats to "the tidy one" would
reintroduce that, and nothing would fail: the brief would just quietly miss the
most recent few items.

`parse` is deliberately tolerant of both, and of a missing `Z`, because reading
is where strictness buys nothing and costs a `ValueError` at 6am.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

SECONDS = "%Y-%m-%dT%H:%M:%SZ"
MILLIS = "%Y-%m-%dT%H:%M:%S.%fZ"


def parse(value: str) -> datetime | None:
    """Tolerant. Returns None rather than raising: a malformed timestamp in a
    stored row should degrade a comparison, not take down a request."""
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def format_seconds(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime(SECONDS)


def format_millis(moment: datetime) -> str:
    # `%f` is microseconds; the wire format is milliseconds.
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def cutoff_seconds(now_iso: str, lookback_hours: int) -> str:
    """ISO timestamp `lookback_hours` before `now`, at second precision.

    Computed from a caller-supplied `now` rather than the wall clock so an
    offline run with a FrozenClock produces the same window every time.

    Second precision is correct here and ONLY here: this bound is compared
    against `published_at`, which is whole seconds. Comparing it against
    `generated_at`, which carries millis, needs `brief._cutoff_ms` -- see the
    module docstring for why the difference is silent rather than loud.
    """
    moment = parse(now_iso)
    if moment is None:
        raise ValueError(f"unparseable timestamp: {now_iso!r}")
    return format_seconds(moment - timedelta(hours=lookback_hours))


def elapsed_seconds(start: str, end: str) -> float | None:
    """Seconds between two stored timestamps, or None if either is unreadable.

    Written out by hand in three places before this -- `api/routers/glossary.py`,
    `scripts/lookup_report.py` and `services/brief_service.py` -- with the same
    five lines and the same `try/except ValueError -> None`.
    """
    first, second = parse(start), parse(end)
    if first is None or second is None:
        return None
    return (second - first).total_seconds()


def hours_since(moment: str, *, now: datetime | None = None) -> float | None:
    """How stale something is. `now` is injectable so a test need not wait."""
    parsed = parse(moment)
    if parsed is None:
        return None
    reference = now or datetime.now(UTC)
    return (reference - parsed).total_seconds() / 3600.0
