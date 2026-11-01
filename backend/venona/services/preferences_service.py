"""How a reader wants things shown to them. The only write path for
`user_preferences`.

One preference today: the zone every timestamp on the reading surface is
rendered in. NULL means "whatever the reader's browser reports", which is the
right answer for almost everybody and the only one available before anyone has
opened a settings page -- so this table holds overrides, not a copy of what the
client already knows.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

from venona.db import core as db
from venona.db.repositories import preferences as repo


class PreferenceError(ValueError):
    """A value a reader may not store. Carries the sentence they see."""


def timezone_for(conn: db.Conn, *, user_id: str | None) -> str | None:
    """The reader's override, or None to mean "ask the browser".

    None for a principal with no row of their own, which is a state the client
    already renders: it is the same answer everybody got before this table
    existed.
    """
    if not user_id:
        return None
    row = repo.preferences_row(conn, user_id=user_id)
    return row["timezone"] if row else None


def set_timezone(conn: db.Conn, *, user_id: str, timezone: str | None, now: str) -> str | None:
    """Store an IANA zone name, or clear it. Returns what was stored.

    Validated through `ZoneInfo` rather than a regex, for the reason
    `config_service` validates `schedule.timezone` the same way: the loader that
    will actually resolve the name is the only thing that knows which names
    resolve. Blank normalises to None so "clear this" and "never set it" are one
    state in the database rather than two that render identically.
    """
    cleaned = (timezone or "").strip() or None
    if cleaned is not None:
        try:
            ZoneInfo(cleaned)
        except Exception as exc:  # noqa: BLE001 - any tz library rejection, not one class
            raise PreferenceError(
                f"{cleaned!r} is not a time zone name. Use an IANA name "
                f'such as "Europe/London" or "America/Los_Angeles".'
            ) from exc
    repo.upsert_timezone(conn, user_id=user_id, timezone=cleaned, now=now)
    return cleaned
