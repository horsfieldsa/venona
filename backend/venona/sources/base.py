"""Source adapter contract (C9).

Adding a source must be a change to `config/sources.yaml` and a recorded
fixture, with no Python. If you find yourself writing an adapter for an ordinary
feed, the protocol is wrong -- fix the protocol.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, field_validator, model_validator

from venona.sources.urlguard import check_static, is_https

# There was a `scraper` kind here, with a `Selectors` model. It is gone.
#
# Every site that motivated it -- Anduril, DIU, PEO C3N -- turned out to be a
# JS-rendered SPA whose served HTML contains no content links at all (see the
# `removed:` block in `config/sources.yaml`). CSS selectors cannot read any of
# them; they need a JSON-payload adapter, which is a different protocol.
#
# Advertising a kind the code cannot serve is worse than not offering it: it
# reads as "add a scraper source and it will work", and the failure arrives at
# fetch time as an empty result rather than at startup as a config error.
SourceKind = Literal["rss", "discovery"]


def _validated_url(value: str | None) -> str | None:
    """Refuse a source URL we could never safely fetch, at SAVE time.

    `PUT /api/config/sources` runs this, so a bad entry fails with a sentence
    naming the problem rather than at 6am as a connection timeout that costs the
    source a strike.

    https only. All fifteen entries already are, so this breaks nothing -- and a
    feed fetched over plain http can be rewritten in flight by anything on the
    path, which for a document set whose whole value is provenance is not a
    trade worth making.

    Static checks only: no DNS. This runs inside `make test` and inside a config
    save, and neither may depend on the network.
    """
    if value is None:
        return None
    check_static(value)
    if not is_https(value):
        raise ValueError(f"source url must be https: {value}")
    return value


class SourceSpec(BaseModel):
    """Validated at startup, so an unknown `kind` is a startup failure rather
    than a mid-run surprise."""

    id: str
    name: str
    kind: SourceKind
    # Optional because `kind: discovery` has no endpoint -- it searches. The
    # validator below still requires it for every kind that fetches one.
    url: str | None = None

    _check_url = field_validator("url")(classmethod(lambda cls, v: _validated_url(v)))
    publisher: str | None = None
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)
    parser: str | None = None
    # Per-source model override for `kind: discovery`. Depth costs money, and
    # which areas deserve opus is a judgement about the reader's thesis, not
    # about code -- so it belongs in config next to the sweep it applies to.
    model: str | None = None
    max_items: int = 40
    # Some publishers put usable text in the feed; most do not and need the
    # article fetched. Declared rather than guessed.
    fetch_body: bool = True
    note: str | None = None

    @field_validator("id")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not value.replace("_", "").replace("-", "").isalnum():
            raise ValueError(f"source id {value!r} must be alphanumeric/_/-")
        return value

    @field_validator("model")
    @classmethod
    def _model_shape(cls, value: str | None) -> str | None:
        """The same shape check `config_service` applies to `llm.*_model`, and
        deliberately not an allowlist -- a list of known names would have to
        track a vendor's release calendar and would refuse a new model on the day
        someone tried to adopt it. This catches the typo, which otherwise fails
        one call at a time on the model's meter."""
        from venona.services.config_service import MODEL_NAME

        if value is not None and not MODEL_NAME.fullmatch(value):
            raise ValueError(f"model {value!r} is not a model name -- one word, no spaces")
        return value

    @model_validator(mode="after")
    def _url_required_for_fetching_kinds(self) -> SourceSpec:
        """A missing `url` on an `rss` source must fail at startup, not become a
        request to `None` halfway through a run."""
        if self.kind == "rss" and not self.url:
            raise ValueError(f"source {self.id!r} is kind=rss and needs a url")
        return self


@dataclass
class RawItem:
    """What an adapter yields: enough to identify and fetch, nothing resolved."""

    source_id: str
    url: str
    title: str
    publisher: str | None = None
    #: `publisher` is a MODEL'S GUESS rather than something we configured.
    #: One field, two provenances: a hand-written name from `sources.yaml` for a
    #: feed, an unverified assertion from a discovery sweep. `resolve_publisher`
    #: ranks them differently -- a configured name beats the page's own
    #: `og:site_name`, a guess does not -- and cannot tell them apart without
    #: this.
    publisher_asserted: bool = False
    published_at: str | None = None
    date_confidence: str = "unknown"
    summary: str | None = None
    body_html: str | None = None
    body_text: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceResult:
    source_id: str
    items: list[RawItem] = field(default_factory=list)
    ok: bool = True
    error: str | None = None
    not_modified: bool = False
    etag: str | None = None
    last_modified: str | None = None


class SourceAdapter(Protocol):
    spec: SourceSpec

    async def collect(self, ctx: Any) -> AsyncIterator[RawItem]: ...
