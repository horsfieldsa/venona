"""Wire models. A pydantic model at every JSON boundary, the instance
convention -- `response_model` FILTERS, so a field the model omits vanishes
silently and a field typed wrongly is a 500 at serialization.
"""

from __future__ import annotations

from datetime import UTC, datetime


def iso(value: datetime | None) -> str | None:
    """ISO-8601 UTC with Z, second precision -- the repo-wide timestamp shape."""
    if value is None:
        return None
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
