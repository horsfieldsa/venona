"""The user guide, parsed for the browser.

The reason this has tests at all is that the obvious implementation -- reuse
`markdown_to_blocks` -- silently drops every table, and the guide is 78 table
rows. A help page that loses its keyboard reference and its "where do I find X"
table looks like it rendered fine.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from venona.services.guide_service import GUIDE_FILES, load_guide, parse_guide

REPO = Path(__file__).resolve().parents[1]


def _types(blocks: list[dict]) -> list[str]:
    return [b["type"] for b in blocks]


def test_a_table_survives_parsing() -> None:
    """`markdown_to_blocks` drops these entirely: `_md` has the table plugin
    enabled, but with no `table_open` branch the `th`/`td` inline tokens arrive
    with no pending block type and are skipped."""
    blocks = parse_guide(
        "| Where | What it answers |\n"
        "|---|---|\n"
        "| **Brief** | What happened |\n"
        "| Graph | How things connect |\n"
    )
    tables = [b for b in blocks if b["type"] == "table"]
    assert len(tables) == 1
    table = tables[0]
    assert table["head"] == [[[{"t": "Where"}], [{"t": "What it answers"}]]]
    assert len(table["rows"]) == 2
    # A cell holds RUNS, not a string, so emphasis and links work inside one.
    assert table["rows"][0][0] == [{"t": "Brief", "b": True}]


def test_heading_slugs_match_the_guide_s_own_contents_links() -> None:
    """The guide links to `#3-reading-the-brief` in its own contents list. If
    the slug rule here disagreed, the contents would be fourteen dead links --
    and they would look like a styling bug, not a parsing one."""
    blocks = parse_guide("## 3. Reading the brief\n")
    assert blocks[0]["slug"] == "3-reading-the-brief"
    assert blocks[0]["level"] == 2


def test_inline_formatting_becomes_runs() -> None:
    blocks = parse_guide("A **bold** word, `code`, and a [link](https://example.com).\n")
    runs = blocks[0]["runs"]
    assert "".join(r["t"] for r in runs) == "A bold word, code, and a link."
    assert any(r.get("b") for r in runs)
    assert any(r.get("code") for r in runs)
    assert any(r.get("href") == "https://example.com" for r in runs)


def test_nested_list_depth_is_carried() -> None:
    blocks = parse_guide("- one\n  - nested\n- two\n")
    items = [b for b in blocks if b["type"] == "list_item"]
    assert [b["text"] for b in items] == ["one", "nested", "two"]
    assert items[1]["depth"] > items[0]["depth"]


def test_ordered_lists_are_distinguishable_from_bullets() -> None:
    blocks = parse_guide("1. first\n2. second\n")
    assert all(b["ordered"] for b in blocks if b["type"] == "list_item")
    blocks = parse_guide("- first\n")
    assert not any(b["ordered"] for b in blocks if b["type"] == "list_item")


def test_code_fences_and_rules_survive() -> None:
    blocks = parse_guide("```\nmake dev\n```\n\n---\n\ntext\n")
    assert "code" in _types(blocks)
    assert "rule" in _types(blocks)
    assert [b for b in blocks if b["type"] == "code"][0]["text"] == "make dev"


def test_a_missing_guide_is_empty_rather_than_an_error(tmp_path: Path) -> None:
    """A deployment that trimmed `docs/` should lose the help page, not the app.
    A 500 from a `?` link is a worse outcome than a `?` that explains itself."""
    assert load_guide(tmp_path) == []


@pytest.mark.parametrize("filename", GUIDE_FILES)
def test_each_real_guide_parses_with_every_block_kind(filename: str) -> None:
    """Against the shipped files, so a rewrite of a guide that breaks the
    parser fails here rather than in the browser."""
    blocks = load_guide(REPO / "docs", filename)
    assert blocks, f"docs/{filename} should be present in the repo"
    kinds = set(_types(blocks))
    assert {"heading", "paragraph", "list_item", "table"} <= kinds
    # Every heading is addressable, or the contents rail cannot link to it.
    assert all(b.get("slug") for b in blocks if b["type"] == "heading")


def test_a_numbered_list_keeps_its_numbers() -> None:
    """The guide's contents list is numbered and its sections are referred to by
    those numbers ("see section 7"). Rendered as bullets, the cross-reference is
    gone and nothing looks broken."""
    blocks = parse_guide("1. first\n2. second\n3. third\n")
    items = [b for b in blocks if b["type"] == "list_item"]
    assert [b["number"] for b in items] == [1, 2, 3]


def test_a_list_numbered_from_something_other_than_one_is_respected() -> None:
    blocks = parse_guide("7. seventh\n8. eighth\n")
    items = [b for b in blocks if b["type"] == "list_item"]
    assert [b["number"] for b in items] == [7, 8]


def test_bullets_carry_no_number() -> None:
    blocks = parse_guide("- a\n- b\n")
    assert all("number" not in b for b in blocks if b["type"] == "list_item")


# ── callouts ─────────────────────────────────────────────────────────


def test_an_alert_marker_becomes_a_tone_and_is_not_rendered() -> None:
    """The marker IS the tone. A box captioned `[!CAUTION]` is the syntax
    leaking through to the reader, and it is the failure that looks most like
    "the guide is fine" from a block count."""
    blocks = parse_guide("> [!CAUTION]\n> A merge is reversible.\n")
    quotes = [b for b in blocks if b["type"] == "blockquote"]
    assert len(quotes) == 1
    assert quotes[0]["tone"] == "stop"
    assert quotes[0]["text"] == "A merge is reversible."
    assert "[!" not in quotes[0]["text"]


def test_the_five_alert_types_collapse_to_three_tones() -> None:
    """note/tip/important differ editorially, not visually. Five differently
    coloured boxes in a help page teach a palette instead of a document."""
    tones = {}
    for kind in ("NOTE", "TIP", "IMPORTANT", "WARNING", "CAUTION"):
        block = parse_guide(f"> [!{kind}]\n> Body.\n")[0]
        tones[kind] = block["tone"]
    assert tones == {
        "NOTE": "info",
        "TIP": "info",
        "IMPORTANT": "info",
        "WARNING": "warn",
        "CAUTION": "stop",
    }


def test_stripping_the_marker_leaves_the_formatting_where_it_was() -> None:
    """THE REGRESSION THIS EXISTS FOR. markdown-it folds the marker and the
    sentence after it into ONE paragraph joined by a softbreak, so slicing the
    marker off the flattened text afterwards shifts every mark offset in that
    paragraph left by the marker's width -- bold and code landing on the wrong
    words, in a box whose whole job is to be read carefully."""
    block = parse_guide("> [!NOTE]\n> **Bold lead.** Then `code` after it.\n")[0]
    runs = block["runs"]
    assert runs[0]["t"] == "Bold lead."
    assert runs[0].get("b") is True, "the bold must still cover the lead-in"
    code = [r for r in runs if r.get("code")]
    assert [r["t"] for r in code] == ["code"]


def test_a_two_paragraph_callout_is_one_box() -> None:
    """`first`/`last` are what let a flat block list draw one continuous frame.
    Without them a two-paragraph note renders as two stacked boxes with a seam
    down the middle, which reads as two unrelated asides."""
    blocks = parse_guide("> [!NOTE]\n> First para.\n>\n> Second para.\n")
    quotes = [b for b in blocks if b["type"] == "blockquote"]
    assert len(quotes) == 2
    assert all(b["tone"] == "info" for b in quotes)
    assert (quotes[0].get("first"), quotes[0].get("last")) == (True, None)
    assert (quotes[1].get("first"), quotes[1].get("last")) == (None, True)


def test_an_unmarked_blockquote_is_still_a_box() -> None:
    """No marker is not "no callout": the guide quotes nobody, so an ordinary
    blockquote is still an aside and must keep its frame."""
    block = parse_guide("> Just a quoted aside.\n")[0]
    assert block["tone"] == "info"
    assert block["first"] and block["last"]


def test_the_real_guides_carry_their_callouts() -> None:
    """Against the shipped files. The callouts were flattened into plain
    paragraphs when the guide was converted to markdown, which is how three
    warnings -- including the one about an irreversible delete -- lost the frame
    that made them unskimmable.

    Tones are asserted over the UNION of the two guides: the one `stop`-tone
    aside (the data purge) lives in the admin guide, because the reader guide
    no longer describes controls a reader cannot reach.
    """
    per_file = {f: load_guide(REPO / "docs", f) for f in GUIDE_FILES}
    quotes = {f: [b for b in bl if b["type"] == "blockquote"] for f, bl in per_file.items()}
    for filename, file_quotes in quotes.items():
        assert len(file_quotes) >= 3, f"{filename}'s asides should be callouts"
    combined = [q for file_quotes in quotes.values() for q in file_quotes]
    assert len(combined) >= 10, "the guides' asides should be callouts"
    tones = {b["tone"] for b in combined}
    assert {"info", "warn", "stop"} == tones, "all three tones should be in use"
    for blocks in per_file.values():
        assert not any("[!" in b.get("text", "") for b in blocks), "a marker leaked"
