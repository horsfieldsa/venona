"""The in-app guides, parsed for the browser.

`docs/USER-GUIDE.md` (the reader's guide) and `docs/ADMIN-GUIDE.md` (the
operator's) are the sources of truth and stay files: each is read in a
terminal, in an editor and on GitHub as well as in the app, and a copy in
`web/` would drift the moment anyone edited one of them.

WHY NOT `markdown_to_blocks`. That function serves the brief, and its whole
contract is that span offsets point into the exact string it stored -- its
docstring is three paragraphs on why NFC happens before parsing rather than
after. The guide needs no entity annotation at all, and it needs TABLES, which
that parser drops on the floor: `_md` has the table plugin enabled, but there is
no `table_open` branch, so `th`/`td` inline tokens arrive with no pending block
type and are skipped. The guide has 78 table rows. Teaching the brief's parser
about tables to render a help page would put guide requirements inside the one
module that must not have its offsets disturbed.

So this parses separately and REUSES what is safe to reuse: `_inline_to_text`
for emphasis, links and inline code, and `blocks_to_runs` for the run splitting
that contract C3 already tests. Blocks are built with no spans, so the runs come
back carrying formatting only.

NO v-html ANYWHERE. `BriefBlock.vue` says it outright -- "no markdown parsing,
no v-html, no substring" -- and a help page is not a reason to introduce the one
XSS surface the reading path has stayed clear of. The client receives structured
blocks and renders them with real elements, the same way the brief does.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any, Final

from venona.services.annotate import Block, _inline_to_text, _md, blocks_to_runs


def _slug(text: str) -> str:
    """A heading's anchor, matching the in-document links the guide already uses.

    The guide's own contents list links to `#3-reading-the-brief`, which is
    GitHub's slug rule: lowercase, non-word characters dropped, spaces to
    hyphens. Deriving it the same way here is what makes the contents list work
    in the app as well as on GitHub, rather than being 14 dead links.
    """
    slug = unicodedata.normalize("NFC", text).strip().lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    return re.sub(r"[\s_]+", "-", slug).strip("-")


# GitHub's alert syntax: a blockquote whose first line is `> [!NOTE]`.
#
# Chosen over a convention of our own because it is the ONE callout spelling
# that all four of this document's readers understand. GitHub draws a real
# coloured admonition, an editor's markdown preview does the same, a terminal
# shows an ordinary quoted block that still reads correctly, and this parser
# gets a tone to render from. A `:::note` fence or an HTML `<div class="note">`
# would have been a private extension that degrades to literal punctuation or
# to nothing everywhere except here -- and `parse_guide` drops raw HTML on the
# floor, so the second one would have been invisible.
_ALERT = re.compile(r"^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]$", re.IGNORECASE)


#: Alert type -> the tone the client draws. Five types, three tones: the
#: distinctions GitHub draws between note/tip/important are editorial rather
#: than visual, and a help page with five differently coloured boxes teaches the
#: reader a palette instead of a document.
_TONE = {
    "note": "info",
    "tip": "info",
    "important": "info",
    "warning": "warn",
    "caution": "stop",
}


def _take_alert(token: Any) -> str | None:
    """Consume a leading `[!TYPE]` marker, returning its tone.

    STRIPPED FROM THE INLINE CHILDREN rather than from the assembled text.
    markdown-it folds the marker and the sentence after it into one paragraph
    joined by a softbreak, so the marker is not a block of its own to skip --
    and slicing it off the flattened string afterwards would leave every mark
    offset in that paragraph pointing a marker's width to the left, which is
    bold and code landing on the wrong words.
    """
    children = getattr(token, "children", None)
    if not children:
        return None
    head = children[0]
    if head.type != "text":
        return None
    alert = _ALERT.match(head.content.strip())
    if not alert:
        return None
    # The marker, and the line break that separated it from the text.
    drop = 2 if len(children) > 1 and children[1].type == "softbreak" else 1
    del children[:drop]
    return _TONE[alert.group(1).lower()]


def _runs(token: Any) -> list[dict[str, Any]]:
    """Inline tokens -> C3 runs, via the block machinery rather than beside it."""
    text, marks = _inline_to_text(token)
    block = Block(id="g", type="paragraph", depth=0, text=text, marks=marks)
    return blocks_to_runs(block)


def parse_guide(markdown: str) -> list[dict[str, Any]]:
    """Flat blocks, in document order.

    Flat rather than a tree for the same reason the brief's are: a tree has to
    be walked by a recursive component, and every block here is renderable on
    its own given a `depth`. Headings additionally carry a `slug` and `level` so
    the client can build a contents rail without re-parsing anything.
    """
    tokens = _md.parse(unicodedata.normalize("NFC", markdown))
    blocks: list[dict[str, Any]] = []

    list_depth = 0
    ordered_stack: list[bool] = []
    # One counter per open list, so a numbered list renders as `1. 2. 3.` rather
    # than as bullets. The guide's own contents list is numbered and its
    # sections are referred to BY those numbers ("see section 7"), so losing
    # them is losing a cross-reference, not losing decoration.
    counter_stack: list[int] = []
    in_quote = False
    pending: str | None = None
    pending_depth = 0

    # Callout state: the tone the open blockquote is drawing in, and the blocks
    # it has produced so far.
    quote_tone = "info"
    quote_blocks: list[dict[str, Any]] = []

    # Table state. `markdown-it` emits a flat token stream, so a row is
    # assembled between `tr_open` and `tr_close` and a table between
    # `table_open` and `table_close`.
    table: dict[str, Any] | None = None
    row: list[list[dict[str, Any]]] | None = None
    in_header = False

    for token in tokens:
        t = token.type

        if t == "bullet_list_open":
            list_depth += 1
            ordered_stack.append(False)
            counter_stack.append(0)
        elif t == "ordered_list_open":
            list_depth += 1
            ordered_stack.append(True)
            # `start` when the author numbered from something other than 1;
            # markdown-it puts it in attrs and omits it for the ordinary case.
            start = 1
            for key, value in token.attrs.items() if token.attrs else []:
                if key == "start":
                    start = int(value)
            counter_stack.append(start - 1)
        elif t in {"bullet_list_close", "ordered_list_close"}:
            list_depth = max(0, list_depth - 1)
            if ordered_stack:
                ordered_stack.pop()
            if counter_stack:
                counter_stack.pop()
        elif t == "list_item_open":
            if counter_stack:
                counter_stack[-1] += 1
        elif t == "blockquote_open":
            in_quote = True
            # `info` until a `[!TYPE]` marker says otherwise, so an ordinary
            # quoted passage is still a box rather than losing its frame.
            quote_tone = "info"
            quote_blocks = []
        elif t == "blockquote_close":
            in_quote = False
            # The box is drawn by its FIRST and LAST blocks -- one continuous
            # frame around a two-paragraph note rather than two stacked boxes
            # with a seam between them. Marked here because a flat block list
            # cannot see its own neighbours at render time.
            if quote_blocks:
                quote_blocks[0]["first"] = True
                quote_blocks[-1]["last"] = True
            quote_blocks = []

        elif t == "table_open":
            table = {"type": "table", "head": [], "rows": []}
        elif t == "table_close":
            if table is not None:
                blocks.append(table)
            table = None
        elif t == "thead_open":
            in_header = True
        elif t == "thead_close":
            in_header = False
        elif t == "tr_open":
            row = []
        elif t == "tr_close":
            if table is not None and row is not None:
                (table["head"] if in_header else table["rows"]).append(row)
            row = None
        elif t in {"th_open", "td_open"}:
            pending = "cell"

        elif t == "heading_open":
            pending = "heading"
            pending_depth = int(token.tag[1:]) if token.tag[1:].isdigit() else 1
        elif t == "paragraph_open":
            if list_depth:
                pending, pending_depth = "list_item", list_depth
            elif in_quote:
                pending, pending_depth = "blockquote", 0
            else:
                pending, pending_depth = "paragraph", 0
        elif t in {"fence", "code_block"}:
            blocks.append(
                {
                    "type": "code",
                    "depth": 0,
                    "text": token.content.rstrip("\n"),
                    "runs": [{"t": token.content.rstrip("\n")}],
                }
            )
        elif t == "hr":
            blocks.append({"type": "rule", "depth": 0, "text": "", "runs": []})

        elif t == "inline" and pending is not None:
            if pending == "cell":
                if row is not None:
                    row.append(_runs(token))
            else:
                # The marker is the tone, not content. Consumed before the text
                # is assembled -- a box captioned `[!CAUTION]` is the syntax
                # leaking through to the reader.
                if pending == "blockquote":
                    quote_tone = _take_alert(token) or quote_tone
                text, marks = _inline_to_text(token)
                block: dict[str, Any] = {
                    "type": pending,
                    "depth": pending_depth,
                    "text": text,
                    "runs": blocks_to_runs(
                        Block(id="g", type="paragraph", depth=0, text=text, marks=marks)
                    ),
                }
                if pending == "blockquote":
                    block["tone"] = quote_tone
                    quote_blocks.append(block)
                if pending == "heading":
                    block["level"] = pending_depth
                    block["slug"] = _slug(text)
                if pending == "list_item":
                    ordered = bool(ordered_stack and ordered_stack[-1])
                    block["ordered"] = ordered
                    if ordered and counter_stack:
                        block["number"] = counter_stack[-1]
                blocks.append(block)
            pending = None

    return blocks


#: Every file this module will serve. Enumerable so a test can hold the deploy
#: configuration to it: each of these must be COPY'd into the production image
#: and listed in its build filter, or the deployed app renders "the guide is
#: not installed" while every local check passes.
GUIDE_FILES: Final = ("USER-GUIDE.md", "ADMIN-GUIDE.md")


def load_guide(docs_dir: Path, filename: str = "USER-GUIDE.md") -> list[dict[str, Any]]:
    """Parse one of `GUIDE_FILES`, or return nothing if it is not there.

    An ABSENT guide is not an error. The file ships with the repo, but a
    deployment that trimmed `docs/` should lose the help page, not the app --
    and a 500 from a `?` link is a worse outcome than a `?` link that says the
    guide is missing.
    """
    path = docs_dir / filename
    if not path.is_file():
        return []
    return parse_guide(path.read_text(encoding="utf-8"))
