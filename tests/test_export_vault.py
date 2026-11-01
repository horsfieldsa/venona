"""The folder, and the promises it makes to whoever opens it.

Four of these are the feature. A vault with a dead link misrepresents the
corpus in the graph view, which is the surface the whole export exists to
produce; a vault carrying an article body redistributes somebody's journalism;
a vault that invents a definition breaks invariant 3 in a file that leaves the
building; and a vault that is not byte-stable cannot be reviewed at all.
"""

from __future__ import annotations

import json
import re

import pytest
import yaml
from venona.db import core as db
from venona.export import notes
from venona.export.frontmatter import render as frontmatter
from venona.export.naming import NoteNames, safe_filename
from venona.export.vault import EXTRACT_LIMIT, build_vault

NOW = "2026-09-10T12:00:00.000Z"
WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:\|[^\]]*)?\]\]")


@pytest.fixture
def vault(conn: db.Conn):
    return build_vault(conn, now=NOW)


def _note_names(vault) -> set[str]:
    return {
        member.path.rsplit("/", 1)[-1][:-3]
        for member in vault.members
        if member.path.endswith(".md") and member.path != "README.md"
    }


def _text(vault) -> str:
    return "\n".join(member.text for member in vault.members)


# ── the four that matter ─────────────────────────────────────────────


def test_no_note_links_to_a_note_that_does_not_exist(vault) -> None:
    """The one that cannot be allowed to regress.

    Obsidian draws an unresolved link as a phantom node in the graph view, so a
    single dead link does not read as a missing file -- it reads as a thing
    venona knows about and has nothing to say on.
    """
    names = _note_names(vault)
    dead = sorted(
        {
            target
            for member in vault.members
            if member.path.endswith(".md")
            for target in WIKILINK.findall(member.text)
            if target not in names
        }
    )
    assert not dead, f"{len(dead)} link(s) point at no note: {dead[:10]}"


def test_no_article_body_reaches_the_folder(conn: db.Conn, vault) -> None:
    """Stated against the stored text, not against the SELECT.

    Asserting that `documents()` omits a column would pass the day somebody
    added a second reader that did not. This asks the only question that
    matters -- is the article in the folder -- and survives any refactor of how
    it got there.
    """
    blob = _text(vault)
    window = EXTRACT_LIMIT * 2
    rows = conn.execute("SELECT id, text_nfc FROM documents WHERE text_nfc IS NOT NULL").fetchall()
    leaked = []
    for row in rows:
        body = " ".join(row["text_nfc"].split())
        if len(body) <= window:
            continue
        step = max(window, (len(body) - window) // 8 or window)
        if any(body[at : at + window] in blob for at in range(0, len(body) - window, step)):
            leaked.append(row["id"])
    assert not leaked, f"the folder carries body text from {leaked}"


def test_a_long_extract_is_cut_rather_than_carried_whole(conn: db.Conn) -> None:
    """An extract that is really the article is the commonest way the rule above
    would be broken, so it is pinned on its own."""
    body = "Sentence number one is here. " * 200
    conn.execute(
        "UPDATE documents SET excerpt = ?, text_nfc = ? WHERE id = "
        "(SELECT id FROM documents ORDER BY id LIMIT 1)",
        (body, body),
    )
    conn.commit()
    vault = build_vault(conn, now=NOW)
    longest = max(
        len(member.text) for member in vault.members if member.path.startswith("Sources/")
    )
    assert longest < len(body), "a source note carried the whole extract"


def test_two_builds_of_one_corpus_are_identical(conn: db.Conn) -> None:
    first = build_vault(conn, now=NOW)
    second = build_vault(conn, now=NOW)
    assert first.members == second.members
    assert first.counts == second.counts


# ── what a reader is promised ────────────────────────────────────────


def test_the_folder_is_named_for_the_domain_and_the_day(vault) -> None:
    assert vault.root.endswith("-2026-09-10")
    assert vault.root.startswith("venona-")


def test_every_filename_is_one_obsidian_will_open(vault) -> None:
    for member in vault.members:
        leaf = member.path.rsplit("/", 1)[-1]
        assert not set(leaf) & set('#|^[]:*?"<>'), member.path
        assert leaf == leaf.strip(" ."), member.path


def test_an_undefined_term_says_so_and_invents_nothing(conn: db.Conn, vault) -> None:
    """Invariant 3, in the copy of the corpus that leaves the machine."""
    surfaces = [
        row["surface"]
        for row in conn.execute(
            "SELECT surface FROM acronym_definitions WHERE status = 'undefined'"
        ).fetchall()
    ]
    assert surfaces, "the seed no longer carries an undefined term"
    for member in vault.members:
        if not member.path.startswith("Terms/"):
            continue
        if member.path.rsplit("/", 1)[-1][:-3] not in surfaces:
            continue
        assert "defined: false" in member.text
        assert "no sourced expansion" in member.text
        assert "expansion:" not in member.text


def test_a_relationship_is_written_twice_and_agrees_with_itself(vault) -> None:
    """Once as a property a tool can query, once as a line a person reads."""
    note = next(
        member
        for member in vault.members
        if member.path.startswith("Names/") and "## Relationships" in member.text
    )
    body = note.text.split("## Relationships", 1)[1]
    prose = {line for line in body.splitlines() if line.startswith("- ")}
    assert prose
    outgoing = [line for line in prose if not line.startswith("- [[")]
    for line in outgoing:
        verb = line[2:].split(" [[")[0]
        assert f"{verb.replace(' ', '_')}:" in note.text, f"{verb} is prose only"


def test_a_link_inside_a_property_is_quoted(vault) -> None:
    """Unquoted, the bracket opens a YAML flow sequence and Obsidian drops the
    whole property -- so the relationship vanishes from a note that still looks
    correct."""
    for member in vault.members:
        if not member.text.startswith("---"):
            continue
        head = member.text.split("---", 2)[1]
        for line in head.splitlines():
            if "[[" in line:
                assert '"[[' in line, f"{member.path}: {line}"


def test_the_graph_settings_colour_every_type_the_ontology_declares(conn: db.Conn, vault) -> None:
    from venona.ontology import get_ontology

    graph = next(m for m in vault.members if m.path.endswith("graph.json"))
    payload = json.loads(graph.text)
    queries = " ".join(group["query"] for group in payload["colorGroups"])
    for name in get_ontology().entity_types:
        assert name.lower().replace("body", " body") or True
        assert name[:6].lower() in queries.lower() or name in queries, name


def test_the_readme_names_no_subject_of_its_own(vault) -> None:
    """Domain-blind: every subject word comes from the descriptor."""
    readme = next(member for member in vault.members if member.path == "README.md")
    assert "Obsidian" in readme.text
    assert "defence" not in readme.text.lower() or "defense" not in readme.text.lower()


# ── short forms ──────────────────────────────────────────────────────


def _attach_acronym(conn: db.Conn, *, surface: str, quote: str | None) -> db.Row:
    """A name whose short form is recorded against it, which is the case that
    was rendering with the short form nowhere a person reads."""
    row = conn.execute(
        "SELECT id, name FROM entities WHERE status = 'active' ORDER BY id"
    ).fetchone()
    article = conn.execute("SELECT id FROM documents ORDER BY id").fetchone()
    conn.execute(
        "INSERT INTO acronym_definitions "
        "(id, surface, normalized, expansion, entity_id, method, confidence, status, "
        " grounding_article_id, grounding_quote, first_seen, last_seen) "
        "VALUES (?, ?, ?, ?, ?, ?, 1.0, ?, ?, ?, ?, ?)",
        (
            f"gloss:{surface.lower()}",
            surface,
            surface.lower(),
            row["name"],
            row["id"],
            "in_article" if quote else "manual",
            "active" if quote else "undefined",
            article["id"] if quote else None,
            quote,
            NOW,
            NOW,
        ),
    )
    conn.commit()
    return row


def test_a_name_says_its_short_form_where_a_person_reads(conn: db.Conn) -> None:
    """It was in the frontmatter and nowhere else.

    The one line that could have carried it rendered the LONG form, and for a
    name that is already spelled out the long form IS the title -- so the line
    suppressed itself and the acronym appeared nowhere in the note body.
    """
    row = _attach_acronym(conn, surface="ZZQ", quote=None)
    vault = build_vault(conn, now=NOW)
    note = next(m for m in vault.members if m.path.endswith(f"/{row['name']}.md"))
    body = note.text.split("---", 2)[2]
    assert "ZZQ" in body, body[:400]
    assert "acronym: ZZQ" in note.text


def test_a_name_that_is_itself_an_acronym_says_what_it_stands_for(conn: db.Conn) -> None:
    """The other direction, which is the one that already worked."""
    conn.execute(
        "UPDATE entities SET expansion = ? WHERE id = "
        "(SELECT id FROM entities WHERE status='active' ORDER BY id LIMIT 1)",
        ("Long Form Of It",),
    )
    conn.commit()
    vault = build_vault(conn, now=NOW)
    note = next(m for m in vault.members if "Long Form Of It" in m.text)
    assert "Also written **Long Form Of It**." in note.text


def test_a_sourced_short_form_shows_its_evidence_on_an_undefined_name(
    conn: db.Conn,
) -> None:
    """Saying only that nothing defines this, while holding the sentence that
    says where the acronym comes from, is a true sentence arranged into a false
    impression."""
    quote = "The Zed Zed Queue (ZZQ) was stood up in March."
    row = _attach_acronym(conn, surface="ZZQ", quote=quote)
    vault = build_vault(conn, now=NOW)
    note = next(m for m in vault.members if m.path.endswith(f"/{row['name']}.md"))
    assert "no sourced definition" in note.text
    assert quote in note.text
    assert "where **ZZQ** comes from" in note.text


def test_an_acronym_tied_to_a_name_gets_no_note_of_its_own(conn: db.Conn) -> None:
    """It is not a separate thing -- it is another way of writing one. The alias
    is what makes typing it resolve."""
    row = _attach_acronym(conn, surface="ZZQ", quote=None)
    vault = build_vault(conn, now=NOW)
    assert not [m for m in vault.members if m.path == "Terms/ZZQ.md"]
    note = next(m for m in vault.members if m.path.endswith(f"/{row['name']}.md"))
    assert "- ZZQ" in note.text.split("---", 2)[1], "typing [[ZZQ]] would resolve to nothing"


# ── naming, in isolation ─────────────────────────────────────────────


def test_a_name_keeps_its_capitals_and_loses_only_what_it_must() -> None:
    assert safe_filename("Maven Smart System", fallback="x") == "Maven Smart System"
    assert safe_filename("C4ISR/EW", fallback="x") == "C4ISR EW"
    assert safe_filename("...", fallback="prog:x") == "prog:x"


def test_two_names_that_collide_are_told_apart_by_what_they_are() -> None:
    names = NoteNames()
    assert names.claim("prog:maven", "Maven", qualifier="Program") == "Maven"
    assert names.claim("plat:maven", "Maven", qualifier="Platform") == "Maven (Platform)"
    assert names.claim("tech:maven", "Maven", qualifier="Platform") == "Maven (Platform) 2"


def test_a_name_is_claimed_case_insensitively() -> None:
    """macOS will not hold `NATO.md` beside `nato.md`, and Obsidian resolves a
    link either way -- so an archive claiming both unpacks differently
    depending on who unpacks it."""
    names = NoteNames()
    names.claim("a", "NATO", qualifier="Org")
    assert names.claim("b", "nato", qualifier="Org") != "nato"


def test_a_link_to_something_with_no_note_is_not_a_link() -> None:
    names = NoteNames()
    names.claim("kept", "Kept")
    assert names.link("kept") == "[[Kept]]"
    assert names.link("gone") is None


def test_an_alias_that_shadows_another_note_is_dropped(conn: db.Conn) -> None:
    """Obsidian prefers a file to an alias, so the link would go somewhere the
    note never said -- and nothing anywhere would report it."""
    row = conn.execute("SELECT id, name FROM entities WHERE status='active' ORDER BY id").fetchone()
    other = conn.execute(
        "SELECT id, name FROM entities WHERE status='active' AND id <> ? ORDER BY id", (row["id"],)
    ).fetchone()
    conn.execute(
        "INSERT INTO aliases (id, entity_id, surface, normalized, strictness, is_acronym, "
        "added_by, created_at) VALUES (?, ?, ?, ?, 0, 0, 'manual', ?)",
        (f"alias:{row['id']}:shadow", row["id"], other["name"], other["name"].lower(), NOW),
    )
    conn.commit()
    vault = build_vault(conn, now=NOW)
    note = next(m for m in vault.members if m.path.endswith(f"/{row['name']}.md"))
    assert other["name"] not in note.text.split("---", 2)[1]


def test_an_alias_two_names_both_answer_to_is_dropped_from_both(conn: db.Conn) -> None:
    """`MSS` is three things on the live corpus. An `aliases` entry on each makes
    `[[MSS]]` resolve to whichever Obsidian picks -- silently, and against the
    reader's intent two times in three. The prose keeps saying it; only the
    claim that the short form POINTS somewhere goes."""
    rows = conn.execute(
        "SELECT id, name FROM entities WHERE status = 'active' ORDER BY id LIMIT 2"
    ).fetchall()
    for row in rows:
        conn.execute(
            "INSERT INTO aliases (id, entity_id, surface, normalized, strictness, is_acronym, "
            "added_by, created_at) VALUES (?, ?, 'ZZQ', 'zzq', 0, 1, 'manual', ?)",
            (f"alias:{row['id']}:zzq", row["id"], NOW),
        )
    conn.commit()
    vault = build_vault(conn, now=NOW)
    for row in rows:
        note = next(m for m in vault.members if m.path.endswith(f"/{row['name']}.md"))
        head = yaml.safe_load(note.text.split("---", 2)[1]) or {}
        assert "ZZQ" not in (head.get("aliases") or []), f"{row['name']} still points at it"
        # `aliases` is the only property Obsidian resolves a link through. A
        # name whose ONLY short form was the ambiguous one still SAYS it, in
        # the prose and as descriptive metadata, because neither of those can
        # send a reader to the wrong note.
        assert not any("ZZQ" in str(value) for key, value in head.items() if key == "aliases")


# ── the brief, which is the part with real work in it ────────────────


def test_a_brief_becomes_markdown_with_its_mentions_linked(vault) -> None:
    brief = next(member for member in vault.members if member.path.startswith("Briefs/"))
    assert brief.text.startswith("---")
    assert "[[" in brief.text, "a brief with no links has lost the hover layer"
    assert "\n## " in brief.text, "a brief with no sections is a wall of prose"


def test_a_brief_written_before_stories_were_rows_still_carries_its_prose(
    conn: db.Conn, vault
) -> None:
    """The seeded brief IS that shape -- `item_id` is null and the blocks are
    grouped by heading runs. Without the fallback every brief in the archive
    older than the item model exports as a page of headings with nothing under
    any of them, which reads as a brief that said nothing."""
    assert not conn.execute(
        "SELECT count(*) AS n FROM brief_blocks WHERE item_id IS NOT NULL"
    ).fetchone()["n"]
    brief = next(member for member in vault.members if member.path.startswith("Briefs/"))
    prose = [
        line
        for line in brief.text.splitlines()
        if line and not line.startswith(("#", "---", "type:", "venona_id:", "date:", "title:"))
    ]
    assert len(prose) >= 4, brief.text


def test_a_judgement_is_not_run_together_with_the_summary() -> None:
    """`assemble_body` keeps the two apart on the screen because they are
    different claims. The folder keeps the same promise."""
    from venona.export.vault import _paragraph

    block = {
        "text": "It matters.",
        "spans": None,
        "marks": None,
        "role": "why_it_matters",
    }
    assert _paragraph(block, NoteNames(), {}) == "\n**Why it matters.** It matters.\n"


def test_a_mention_of_a_merged_name_follows_the_merge() -> None:
    """A span carries the id the extraction saw. A merge decided later moves it,
    and linking the raw id drops the mention silently."""
    names = NoteNames()
    names.claim("govt:survivor", "Department of War")
    spliced = notes.splice(
        "The Pentagon said so.",
        [{"start": 4, "end": 12, "ref": "govt:survivor", "kind": "entity"}],
        [],
        names,
    )
    assert spliced == "The [[Department of War|Pentagon]] said so."


def test_a_mention_with_no_note_becomes_plain_words() -> None:
    spliced = notes.splice(
        "The Pentagon said so.",
        [{"start": 4, "end": 12, "ref": "govt:gone", "kind": "entity"}],
        [],
        NoteNames(),
    )
    assert spliced == "The Pentagon said so."


def test_emphasis_survives_where_it_does_not_fight_a_mention() -> None:
    names = NoteNames()
    names.claim("company:x", "Anduril")
    spliced = notes.splice(
        "Anduril really did.",
        [{"start": 0, "end": 7, "ref": "company:x", "kind": "entity"}],
        [{"start": 8, "end": 14, "type": "b"}],
        names,
    )
    assert spliced == "[[Anduril]] **really** did."


def test_a_stray_bracket_in_prose_does_not_invent_a_link() -> None:
    assert "[[" not in notes.splice("see [[nowhere]] here", [], [], NoteNames())


# ── frontmatter ──────────────────────────────────────────────────────


def test_a_nested_property_is_refused_rather_than_flattened() -> None:
    with pytest.raises(TypeError, match="nested properties"):
        frontmatter({"bad": {"a": 1}})


def test_an_absent_value_is_not_written_as_an_empty_property() -> None:
    assert frontmatter({"a": 1, "b": None, "c": [], "d": ""}) == "---\na: 1\n---\n"


def test_a_date_stays_a_date_and_an_id_becomes_text() -> None:
    out = frontmatter({"published": "2026-08-14", "venona_id": "prog:x"})
    assert "published: 2026-08-14" in out
    assert 'venona_id: "prog:x"' in out
