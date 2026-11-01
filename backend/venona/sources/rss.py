"""RSS/Atom adapter.

Feeds lie about dates constantly, so every date carries a `date_confidence`
label rather than being silently coerced. Nothing here ever defaults a missing
date to "now" -- that would corrupt every recency ranking downstream, invisibly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import feedparser

from venona.sources.base import RawItem, SourceResult, SourceSpec


def _to_iso(value: Any) -> str | None:
    """feedparser gives a struct_time; normalise to ISO-8601 UTC with Z."""
    if not value:
        return None
    try:
        return datetime(*value[:6], tzinfo=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None


def diagnose_empty(body: str) -> str:
    """Why a feed produced no items, in words that point at the real cause.

    This exists because the guess it replaces cost three days of a dead source.
    The message was "feed parsed but yielded no items (moved or reformatted?)"
    for DefenseScoop, Defense One and War on the Rocks -- none of which had
    moved or reformatted anything. Their CDNs served Brotli, `httpx` had no
    Brotli decoder, and the adapter was handed compressed bytes. feedparser
    KNEW: it set `bozo` and a `SAXParseException('not well-formed (invalid
    token)')`. `parse_feed` threw both away and the pipeline invented a
    publisher-side explanation for a client-side bug.

    So: report what feedparser actually said, and distinguish the three cases
    that need different actions -- fix the client, fix the URL, or wait.
    """
    parsed = feedparser.parse(body)

    if parsed.entries:
        return "every entry in the feed was missing a link or a title"

    if parsed.bozo:
        exception = parsed.bozo_exception
        head = body.lstrip()[:1]
        if head not in ("<", "{"):
            # Not markup at all. Overwhelmingly a content-encoding we asked for
            # and cannot decode, which is silent in httpx: an unknown
            # `Content-Encoding` falls back to identity rather than raising.
            return (
                "the response was not readable text, so nothing could be parsed "
                "-- most likely a compression format we asked for and cannot "
                f"decode ({type(exception).__name__})"
            )
        return f"the response was not valid XML ({exception})"

    # A clean parse with no entries -- the only case where "moved or
    # reformatted" is a fair guess.
    return "the feed is valid but currently lists no articles (moved, or a quiet day)"


def parse_feed(spec: SourceSpec, body: str) -> list[RawItem]:
    parsed = feedparser.parse(body)
    items: list[RawItem] = []

    for entry in parsed.entries[: spec.max_items]:
        link = (entry.get("link") or "").strip()
        title = (entry.get("title") or "").strip()
        if not link or not title:
            continue

        published = _to_iso(entry.get("published_parsed")) or _to_iso(entry.get("updated_parsed"))
        confidence = "feed" if published else "unknown"

        summary = (entry.get("summary") or "").strip() or None

        # A few feeds carry the full article; most carry a stub. Take the
        # content when it is genuinely there so we can skip a fetch.
        body_html = None
        contents = entry.get("content") or []
        if contents and isinstance(contents, list):
            candidate = (contents[0].get("value") or "").strip()
            if len(candidate) > 800:
                body_html = candidate

        items.append(
            RawItem(
                source_id=spec.id,
                url=link,
                title=title,
                publisher=spec.publisher or spec.name,
                published_at=published,
                date_confidence=confidence,
                summary=summary,
                body_html=body_html,
            )
        )

    return items


async def collect_rss(
    spec: SourceSpec,
    http,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
) -> SourceResult:
    """One conditional GET, then parse. Never raises -- a dead feed must not
    take a run down with it."""
    try:
        response = await http.get(spec.url, etag=etag, last_modified=last_modified)
    except Exception as exc:  # noqa: BLE001 - failure isolation is the point
        return SourceResult(source_id=spec.id, ok=False, error=f"{type(exc).__name__}: {exc}")

    if response.not_modified:
        return SourceResult(
            source_id=spec.id,
            not_modified=True,
            etag=etag,
            last_modified=last_modified,
        )

    if not response.ok:
        return SourceResult(source_id=spec.id, ok=False, error=f"HTTP {response.status}")

    items = parse_feed(spec, response.text)
    if not items:
        # An empty parse is indistinguishable from a quiet news day unless we
        # say so -- and saying the WRONG thing is worse than saying nothing.
        return SourceResult(
            source_id=spec.id,
            ok=False,
            error=f"no articles: {diagnose_empty(response.text)}",
        )

    return SourceResult(
        source_id=spec.id,
        items=items,
        etag=response.headers.get("etag"),
        last_modified=response.headers.get("last-modified"),
    )
