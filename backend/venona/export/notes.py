"""Blocks and rows become Markdown a person reads.

THE INVERSE NOBODY HAD WRITTEN. `services/annotate.py::markdown_to_blocks`
turns prose into blocks plus offsets; `api/mcp/render.py::_blocks_text`
flattens blocks back to plain text for a model. Neither puts the hover layer
back, and the hover layer is the whole reason a brief is worth exporting: every
programme and acronym the reader can ask about becomes a link they can follow.

Offsets stay in Python and are never sent anywhere, so the UTF-16 hazard that
governs the wire does not apply here -- `spans` are code-point offsets into
`block.text` and Python slices by code point.
"""

from __future__ import annotations

from typing import Any

from venona.export.naming import NoteNames

#: What a mark means, as `services/annotate.py` records it.
_WRAPPERS = {"b": "**", "i": "*", "s": "~~", "code": "`"}


def _decorate_mark(mark: dict[str, Any], surface: str) -> str:
    kind = mark.get("type")
    if kind == "href":
        href = mark.get("href")
        return f"[{surface}]({href})" if href else surface
    wrapper = _WRAPPERS.get(str(kind))
    return f"{wrapper}{surface}{wrapper}" if wrapper else surface


def _overlaps(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    return any(not (end <= other_start or start >= other_end) for other_start, other_end in ranges)


def splice(
    text: str,
    spans: list[dict[str, Any]],
    marks: list[dict[str, Any]],
    names: NoteNames,
) -> str:
    """One block's text with its mentions turned into links.

    A span whose target got no note renders as the PLAIN WORDS. That is the
    rule the whole folder rests on: an unresolved wikilink is a phantom node in
    Obsidian's graph view, so a name this export declined to write must not
    leave a link behind pointing at it.

    A mark overlapping a span is dropped rather than nested. Contract C3
    already guarantees spans do not overlap each other and never land inside a
    link's address, so the only thing this can discard is emphasis that happened
    to sit across a mention, and a lost bold is cheaper than a broken link.
    """
    span_ranges = [(int(span["start"]), int(span["end"])) for span in spans]
    ordered: list[tuple[int, int, str, dict[str, Any]]] = [
        (int(span["start"]), int(span["end"]), "span", span) for span in spans
    ]
    ordered.extend(
        (int(mark["start"]), int(mark["end"]), "mark", mark)
        for mark in marks
        if not _overlaps(int(mark["start"]), int(mark["end"]), span_ranges)
    )
    ordered.sort(key=lambda item: (item[0], item[1]))

    out: list[str] = []
    cursor = 0
    for start, end, kind, payload in ordered:
        if start < cursor or end > len(text):
            continue
        out.append(_plain(text[cursor:start]))
        surface = text[start:end]
        if kind == "span":
            link = names.link(str(payload.get("ref") or ""), text=surface)
            out.append(link if link else _plain(surface))
        else:
            out.append(_decorate_mark(payload, _plain(surface)))
        cursor = end
    out.append(_plain(text[cursor:]))
    return "".join(out)


def _plain(text: str) -> str:
    """Text that is not meant to be a link, kept from becoming one.

    Only the two brackets. Escaping every Markdown character would fight the
    prose -- an asterisk in a quoted headline is far commoner than a reader who
    minds one -- but a literal `[[` in an article title silently invents a link
    to a note that does not exist, which is the one failure this whole module is
    arranged to prevent.
    """
    return text.replace("[[", "[ [").replace("]]", "] ]")


def heading(level: int, text: str) -> str:
    return f"{'#' * level} {text}\n"


def bullet(text: str, *, depth: int = 0) -> str:
    return f"{'  ' * depth}- {text}\n"


def quote(text: str, *, attribution: str | None = None) -> str:
    """A verbatim sentence, with the note it came from under it.

    Used ONLY where a quote exists. A tier-2 definition is drawn from several
    sources and carries no sentence, and rendering its prose in a blockquote
    would present a synthesis as something somebody wrote (contract C8).
    """
    lines = [f"> {line}" for line in text.strip().splitlines()]
    if attribution:
        lines.append(f"> {attribution}")
    return "\n".join(lines) + "\n"
