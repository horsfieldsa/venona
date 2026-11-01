"""How a reader wants things shown to them."""

from __future__ import annotations

from pydantic import BaseModel


class Preferences(BaseModel):
    #: An IANA zone name, or null to mean "render in whatever zone this
    #: browser reports". Null is the default and the common case: the client
    #: already knows its own zone, so this field holds an override rather than
    #: a copy of it.
    timezone: str | None = None


class PreferencesUpdate(BaseModel):
    #: Blank clears the override and returns the reader to their browser's
    #: zone, which is why this is not `str`.
    timezone: str | None = None
