"""Wire models for ingesting one article by URL."""

from __future__ import annotations

from pydantic import BaseModel, Field

from venona.api.schemas.run import RunStarted
from venona.api.schemas.weburl import WebUrl


class IngestRequest(BaseModel):
    # The fetcher would reject a non-http scheme itself, several layers down and
    # as a failed run rather than a refused request. Saying so at the boundary
    # costs the caller nothing and is the difference between a 422 and a run
    # that appears in the history having achieved nothing.
    url: WebUrl = Field(min_length=8, max_length=2048)


class IngestStarted(RunStarted):
    """The 202 envelope, plus the URL so a client watching several can tell
    which run is which without keeping its own map."""

    url: str
