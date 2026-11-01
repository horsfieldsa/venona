"""Markdown -> blocks -> spans -> runs. Contract C3.

The correctness core of the brief reader. Three properties matter, and each has
a test that would catch its loss:

  1. Offsets are computed by code, never requested from a model (invariant 2).
  2. Runs concatenate back to the block's plain text, byte for byte.
  3. Spans never overlap, never fall inside code blocks or link URLs.

Storage keeps OFFSETS because they are canonical and recomputable; the API
serves pre-split RUNS because Python offsets are code points and JavaScript's
are UTF-16 units. One astral-plane character in a headline would otherwise shift
every highlight in the block -- and the bug would look like a model error.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any, Literal

from markdown_it import MarkdownIt

from venona.services import alias_index
from venona.services.alias_index import AliasIndex, Match

BlockType = Literal["heading", "paragraph", "list_item", "blockquote", "code"]


@dataclass
class Mark:
    start: int
    end: int
    type: str  # b | i | code | href
    href: str | None = None

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"start": self.start, "end": self.end, "type": self.type}
        if self.href:
            d["href"] = self.href
        return d


@dataclass
class Block:
    id: str
    type: BlockType
    depth: int
    text: str
    section: int = 0
    heading: str | None = None
    ordinal: int = 0
    marks: list[Mark] = field(default_factory=list)
    spans: list[dict[str, Any]] = field(default_factory=list)


_md = MarkdownIt("commonmark").enable("table").enable("strikethrough")


def _inline_to_text(token: Any) -> tuple[str, list[Mark]]:
    """Flatten an inline token tree to plain text plus flat marks.

    Marks are deliberately flat rather than nested: the wire format gives a run
    boolean flags, so overlapping emphasis is representable and nesting is not
    needed.

    `cursor` counts code points in the string this returns, so the caller must
    NOT change that string afterwards. `markdown_to_blocks` normalises the
    source before parsing precisely so there is nothing left to change here.
    """
    text_parts: list[str] = []
    marks: list[Mark] = []
    open_stack: list[tuple[str, int, str | None]] = []
    cursor = 0

    for child in token.children or []:
        t = child.type
        if t == "text":
            text_parts.append(child.content)
            cursor += len(child.content)
        elif t == "code_inline":
            marks.append(Mark(cursor, cursor + len(child.content), "code"))
            text_parts.append(child.content)
            cursor += len(child.content)
        elif t == "softbreak" or t == "hardbreak":
            text_parts.append(" ")
            cursor += 1
        elif t in {"strong_open", "em_open", "s_open"}:
            kind = {"strong_open": "b", "em_open": "i", "s_open": "s"}[t]
            open_stack.append((kind, cursor, None))
        elif t in {"strong_close", "em_close", "s_close"}:
            kind = {"strong_close": "b", "em_close": "i", "s_close": "s"}[t]
            for i in range(len(open_stack) - 1, -1, -1):
                if open_stack[i][0] == kind:
                    _, start, _ = open_stack.pop(i)
                    if cursor > start:
                        marks.append(Mark(start, cursor, kind))
                    break
        elif t == "link_open":
            href = dict(child.attrs or {}).get("href")
            open_stack.append(("href", cursor, href))
        elif t == "link_close":
            for i in range(len(open_stack) - 1, -1, -1):
                if open_stack[i][0] == "href":
                    _, start, href = open_stack.pop(i)
                    if cursor > start:
                        marks.append(Mark(start, cursor, "href", href))
                    break
        elif t == "image":
            alt = child.content or ""
            text_parts.append(alt)
            cursor += len(alt)

    return "".join(text_parts), marks


def markdown_to_blocks(
    markdown: str, *, section: int = 0, heading: str | None = None, id_prefix: str = "b"
) -> list[Block]:
    """Parse markdown into flat blocks with NFC-normalised text.

    NFC is applied once, here, and the result is what gets stored. Every span
    offset in the database points into this exact string.

    BEFORE parsing, not after. Normalising the flattened text was the same
    class of bug this module exists to prevent: NFC changes a string's LENGTH
    (`"Café"` decomposed is five code points, four composed), so every mark
    `_inline_to_text` had just measured shifted. Bold and italic landed on the
    wrong words, and -- worse, because it is silent -- the `href_ranges` that
    `annotate_blocks` uses to suppress entity spans inside links pointed at the
    wrong text, so mentions inside links got click targets and mentions outside
    them were dropped. Normalising the source instead means the offsets are
    measured against the string that is ultimately stored.
    """
    tokens = _md.parse(unicodedata.normalize("NFC", markdown))
    blocks: list[Block] = []
    ordinal = 0

    list_depth = 0
    in_quote = False
    pending: BlockType | None = None
    pending_depth = 0

    for token in tokens:
        t = token.type

        if t == "bullet_list_open" or t == "ordered_list_open":
            list_depth += 1
        elif t == "bullet_list_close" or t == "ordered_list_close":
            list_depth = max(0, list_depth - 1)
        elif t == "blockquote_open":
            in_quote = True
        elif t == "blockquote_close":
            in_quote = False
        elif t == "heading_open":
            pending, pending_depth = "heading", int(token.tag[1:]) if token.tag[1:].isdigit() else 1
        elif t == "paragraph_open":
            if list_depth:
                pending, pending_depth = "list_item", list_depth
            elif in_quote:
                pending, pending_depth = "blockquote", 0
            else:
                pending, pending_depth = "paragraph", 0
        elif t in {"fence", "code_block"}:
            ordinal += 1
            blocks.append(
                Block(
                    id=f"{id_prefix}{ordinal}",
                    type="code",
                    depth=0,
                    text=token.content.rstrip("\n"),  # source was normalised above
                    section=section,
                    heading=heading,
                    ordinal=ordinal,
                )
            )
        elif t == "inline" and pending is not None:
            text, marks = _inline_to_text(token)
            if text.strip():
                ordinal += 1
                blocks.append(
                    Block(
                        id=f"{id_prefix}{ordinal}",
                        type=pending,
                        depth=pending_depth,
                        text=text,
                        section=section,
                        heading=heading,
                        ordinal=ordinal,
                        marks=marks,
                    )
                )
            pending = None

    return blocks


def annotate_blocks(
    blocks: list[Block],
    index: AliasIndex,
    *,
    tier_a: frozenset[str] | None = None,
    senses: dict[str, list[dict]] | None = None,
    context: str | None = None,
    present_refs: frozenset[str] | None = None,
    strict_case: bool = False,
) -> list[Block]:
    """Attach entity spans. Skips code blocks and link interiors.

    `senses` + `context` resolve AMBIGUOUS acronyms in situ. An acronym with
    two grounded readings is not a defect in the data -- `FCC` really is both a
    fire control center and Futures and Concepts Command -- it is a question
    the letters cannot answer. The index therefore enters such a surface as a
    placeholder rather than picking, and this is where the surrounding text
    gets a vote: a reading whose expansion is spelled out somewhere in the same
    document wins; anything else stays unmarked, exactly as before.

    `context` defaults to the blocks themselves. A brief is the natural unit:
    an acronym introduced in full in one paragraph and used bare in the next is
    the ordinary way people write, and resolving per-block would miss it.

    Omit both and behaviour is unchanged -- every ambiguous surface drops, which
    is what every caller got before this existed.

    `present_refs` is the same idea for GENERATED acronyms. `AUKUS Pillar II`
    generates `API`, which nobody has ever written for it, so that alias may
    only match where the text names the entity some other way. Callers pass the
    set computed once over the whole document by `alias_index.refs_present` --
    once, because four of this function's five callers hand it ONE block at a
    time, and deriving the set from `blocks` here would silently narrow the rule
    to "named in the same paragraph".

    `strict_case` is forwarded to `AliasIndex.find`; see its docstring for when
    it is required. Briefs leave it off; annotating a third-party page turns it
    on.
    """
    if context is None:
        context = "\n".join(b.text for b in blocks if b.type != "code")
    if present_refs is None:
        # Correct standalone, at the cost of a scan. A caller holding one block
        # of a longer document MUST pass its own, or this derives the set from
        # that block alone and the rule silently narrows to the paragraph.
        present_refs = alias_index.refs_present(
            index, context, tier_a=tier_a, strict_case=strict_case
        )

    for block in blocks:
        if block.type == "code":
            block.spans = []
            continue

        matches = index.find(block.text, tier_a=tier_a, strict_case=strict_case)
        href_ranges = [(m.start, m.end) for m in block.marks if m.type == "href"]

        kept: list[Match] = []
        resolved: dict[int, tuple[str, str]] = {}
        for match in matches:
            # A mention inside a link would produce two competing click targets.
            if any(start <= match.start and match.end <= end for start, end in href_ranges):
                continue
            # A generated acronym with nothing in the document to corroborate
            # it. Dropping rather than demoting: the overlap resolution already
            # happened inside `find`, so a shorter well-attested alias this one
            # beat does not come back -- the text goes unannotated, which is the
            # honest answer when the letters alone decide nothing.
            if match.needs_context and match.ref not in present_refs:
                continue
            if match.kind == "ambiguous":
                # `ambiguous:<normalized>` is a placeholder, never a real ref --
                # it must be resolved to a `gloss:` id here or dropped. Letting
                # one reach `spans` would put an id in the wire format that no
                # card endpoint can answer for.
                normalized = match.ref.split(":", 1)[1]
                choice = (
                    alias_index.pick_sense(senses.get(normalized, []), context) if senses else None
                )
                if choice is None:
                    continue
                resolved[id(match)] = (choice["ref"], "glossary")
            kept.append(match)

        block.spans = [
            {
                "start": m.start,
                "end": m.end,
                "ref": resolved.get(id(m), (m.ref, m.kind))[0],
                "kind": resolved.get(id(m), (m.ref, m.kind))[1],
                "confidence": m.confidence,
                "single_source": m.single_source,
            }
            for m in kept
        ]
    return blocks


def blocks_to_runs(block: Block) -> list[dict[str, Any]]:
    """Split a block into the wire-format runs (contract C3).

    Guarantees `"".join(r["t"] for r in runs) == block.text` exactly. That
    invariant is asserted by a contract test because violating it silently
    corrupts the reading surface.
    """
    text = block.text
    spans = sorted(block.spans, key=lambda s: s["start"])

    boundaries: set[int] = {0, len(text)}
    for span in spans:
        boundaries.add(span["start"])
        boundaries.add(span["end"])
    for mark in block.marks:
        boundaries.add(max(0, min(len(text), mark.start)))
        boundaries.add(max(0, min(len(text), mark.end)))

    ordered = sorted(b for b in boundaries if 0 <= b <= len(text))

    runs: list[dict[str, Any]] = []
    for start, end in zip(ordered, ordered[1:], strict=False):
        if start >= end:
            continue
        run: dict[str, Any] = {"t": text[start:end]}

        for span in spans:
            if span["start"] <= start and end <= span["end"]:
                run["r"] = span["ref"]
                run["k"] = span["kind"]
                run["c"] = span["confidence"]
                # Emitted only when true. C3 invariant 5 accepted a ~1.4x payload
                # cost for runs-over-offsets and pays for it with short names and
                # absent-by-default flags; a `"u": false` on every one of a
                # 500-mention brief's runs spends that budget on nothing. Spans
                # written before this field exists simply lack it, which reads as
                # false without a migration.
                if span.get("single_source"):
                    run["u"] = True
                break

        for mark in block.marks:
            if mark.start <= start and end <= mark.end:
                if mark.type in {"b", "i", "code", "s"}:
                    run[mark.type] = True
                elif mark.type == "href" and mark.href:
                    run["href"] = mark.href

        runs.append(run)

    return runs or [{"t": text}]


def verify_runs(block: Block, runs: list[dict[str, Any]]) -> bool:
    """Contract C3 invariant 1. Cheap enough to assert in production."""
    return "".join(r["t"] for r in runs) == block.text
