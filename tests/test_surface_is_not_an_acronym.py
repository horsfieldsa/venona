"""The two verdicts the definitions queue could not express.

Accept, reject, edit, look again and choose-a-meaning all assume the answer is a
different DEFINITION. Two surfaces in this corpus have no right definition:

  * `BAE` is a company. Those letters appear sixteen times and are followed by
    "Systems" all sixteen; the correct outcome is that the surface resolves to
    the entity like any other name.
  * `III` is a Roman numeral -- "battleship Alexander III". The system spent a
    lookup deciding it meant the FBI's Interstate Identification Index.

The queue diagnosed the first ("it is a name, not an acronym") and offered
nothing to do about it.
"""

from __future__ import annotations

import copy
import difflib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from helpers import NOW, entity
from venona.api.routers import glossary
from venona.db import core as db
from venona.pipeline.acronyms import store_definition
from venona.services import alias_index, config_service
from venona.settings import get_settings


def _definition(conn: db.Conn, surface: str, expansion: str) -> str:
    return store_definition(
        conn,
        surface=surface,
        expansion=expansion,
        method="web_grounded",
        confidence=0.7,
        article_id=None,
        quote=f"The {expansion} ({surface}) is a thing.",
        url="https://example.mil/x",
        now=NOW,
    )


@pytest.fixture
def empty(conn: db.Conn) -> db.Conn:
    conn.execute("DELETE FROM acronym_definitions")
    conn.execute("DELETE FROM aliases")
    conn.execute("DELETE FROM entities")
    conn.commit()
    return conn


def _request(settings=None):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))


# ── "this is an entity" ──────────────────────────────────────────────


def test_attaching_writes_the_first_manual_alias_in_the_codebase(empty: db.Conn) -> None:
    """`added_by='manual'` has been in the schema's enum and honoured by
    `default_strictness` since migration 0001, and never written by anything —
    the only way to attach a surface was `profile/anchors.yaml` plus a CLI
    command."""
    entity(empty, "company:bae-systems", "BAE Systems", "provisional")
    _definition(empty, "BAE", "brigade aviation element")

    result = glossary.attach_entity(
        "BAE",
        glossary.AttachEntityPayload(entity_id="company:bae-systems"),
        _request(),
        conn=empty,
    )
    assert result["entity"]["id"] == "company:bae-systems"

    alias = empty.execute("SELECT * FROM aliases WHERE normalized = 'bae'").fetchone()
    assert alias["added_by"] == "manual"
    # All-caps and 3+ chars, so `default_strictness` gives 2: global and
    # case-SENSITIVE. `BAE` matches, `bae` does not.
    assert alias["strictness"] == 2
    assert alias["is_acronym"] == 1


def test_a_provisional_entity_is_promoted_or_the_attach_does_nothing(
    empty: db.Conn,
) -> None:
    """`_resolved_entity_aliases` admits only `active`/`merged` entities, so a
    provisional entity's aliases never enter the automaton. Attaching without
    promoting would look like it worked and change nothing on the page."""
    entity(empty, "company:bae-systems", "BAE Systems", "provisional")
    _definition(empty, "BAE", "brigade aviation element")

    result = glossary.attach_entity(
        "BAE", glossary.AttachEntityPayload(entity_id="company:bae-systems"), _request(), conn=empty
    )

    assert result["promoted"] is True
    status = empty.execute("SELECT status FROM entities WHERE id='company:bae-systems'").fetchone()[
        0
    ]
    assert status == "active"
    assert "BAE" in alias_index.indexed_entity_surfaces(empty)


def test_the_definition_is_retired_not_left_competing(empty: db.Conn) -> None:
    """`entity_id` set drops the row out of `build_index` (whose glossary query
    requires `entity_id IS NULL`), so the surface cannot match twice; `rejected`
    + `decided_at` is what stops re-grounding reinstating it."""
    entity(empty, "company:bae-systems", "BAE Systems", "active")
    entry = _definition(empty, "BAE", "brigade aviation element")

    glossary.attach_entity(
        "BAE", glossary.AttachEntityPayload(entity_id="company:bae-systems"), _request(), conn=empty
    )

    row = empty.execute("SELECT * FROM acronym_definitions WHERE id=?", (entry,)).fetchone()
    assert row["status"] == "rejected"
    assert row["decided_at"]
    assert row["entity_id"] == "company:bae-systems"
    # The evidence survives -- it was a real sentence on a real page.
    assert row["expansion"] == "brigade aviation element"
    assert row["grounding_quote"]


@pytest.mark.negative
def test_a_surface_already_owned_by_another_entity_is_refused(empty: db.Conn) -> None:
    """Two entities claiming one surface is what `_resolve_overlaps` would then
    settle by insertion order. `Resolver._add_alias` refuses the same case and
    files a conflict; merging is the remedy and it already exists."""
    entity(empty, "company:bae-systems", "BAE Systems", "active")
    entity(empty, "company:other", "Brigade Aviation Element", "active")
    empty.execute(
        "INSERT INTO aliases (id, entity_id, surface, normalized, strictness, is_acronym, "
        "added_by, created_at) VALUES (?,?,?,?,?,?,?,?)",
        ("company:other|BAE", "company:other", "BAE", "bae", 2, 1, "extraction", NOW),
    )
    empty.commit()

    with pytest.raises(HTTPException) as raised:
        glossary.attach_entity(
            "BAE",
            glossary.AttachEntityPayload(entity_id="company:bae-systems"),
            _request(),
            conn=empty,
        )
    assert raised.value.status_code == 409
    assert "Brigade Aviation Element" in raised.value.detail


@pytest.mark.negative
def test_attaching_to_an_unknown_entity_is_refused(empty: db.Conn) -> None:
    _definition(empty, "BAE", "brigade aviation element")
    with pytest.raises(HTTPException) as raised:
        glossary.attach_entity(
            "BAE", glossary.AttachEntityPayload(entity_id="company:nope"), _request(), conn=empty
        )
    assert raised.value.status_code == 404


def test_the_promotion_survives_the_next_run(empty: db.Conn) -> None:
    """The step most likely to be wrong, and silently.

    `promote_provisionals` re-gates every provisional/active/dormant entity on
    each analyze run and WRITES the verdict. Without the manual-alias exclusion
    an entity promoted by hand drops back to `provisional` on the next run and
    the surface stops annotating -- the curator sees it work, then sees it stop,
    with nothing to point at.
    """
    from venona.pipeline.resolve import promote_provisionals

    entity(empty, "company:bae-systems", "BAE Systems", "provisional")
    _definition(empty, "BAE", "brigade aviation element")
    glossary.attach_entity(
        "BAE", glossary.AttachEntityPayload(entity_id="company:bae-systems"), _request(), conn=empty
    )

    # No assertions exist for this entity, so the evidence gate cannot pass it.
    promote_provisionals(empty, now=NOW)

    status = empty.execute("SELECT status FROM entities WHERE id='company:bae-systems'").fetchone()[
        0
    ]
    assert status == "active", "a human declaration must outlast the evidence gate"


# ── "not an acronym" ─────────────────────────────────────────────────


@pytest.fixture
def sandbox(tmp_path: Path):
    """A throwaway `profile/` with a lexicon of this fixture's own making.

    Deliberately NOT a copy of `profile/lexicon.yaml`. That was the first
    version and it failed the moment someone stoplisted `III` for real: the
    tests asserted "adding III changes the file", which stops being true as soon
    as the product is used. A test that breaks when a curator curates is
    measuring the corpus, not the code.

    It keeps the shape that matters -- a commented block with entries under it,
    and a second top-level key after it, which is what the insertion has to find
    the end of.
    """
    real = get_settings()
    scratch = copy.deepcopy(real)
    scratch.profile_dir = tmp_path / "profile"
    scratch.profile_dir.mkdir()
    (scratch.profile_dir / "lexicon.yaml").write_text(
        "\n".join(
            [
                "# Abbreviation expansions and the acronym stoplist. USER-EDITABLE.",
                "",
                "# Phrase -> the shorter ways it gets written.",
                "expansions:",
                "  next generation:",
                "    - next-gen",
                "",
                "# Never treated as an acronym worth defining or matching. Either too",
                "# common, too short, or universally understood.",
                "stoplist:",
                "  - US",
                "  - USA",
                "  # Fiscal years. A new one every year, never a definition.",
                "  - FY26",
                "  - FY27",
                "",
                "# Two-character acronyms that ARE meaningful here, so they survive the",
                "# minimum-length rule.",
                "short_whitelist:",
                "  - C2",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return scratch


def test_stoplisting_appends_one_line(sandbox) -> None:
    before = (sandbox.profile_dir / "lexicon.yaml").read_text()
    assert config_service.add_to_stoplist(sandbox, "III") is True

    after = (sandbox.profile_dir / "lexicon.yaml").read_text()
    # A real line diff. Counting occurrences is not one: `str.count("")` is
    # len+1, so every blank line reads as "added".
    diff = [
        line[2:]
        for line in difflib.ndiff(before.splitlines(), after.splitlines())
        if line.startswith(("+ ", "- "))
    ]
    assert diff == ["  - III"], diff


def test_the_users_comments_survive(sandbox) -> None:
    """The reason this edits TEXT rather than round-tripping the YAML.

    `lexicon.yaml` is a document a person wrote and annotated -- the stoplist
    block carries the reasoning for every entry in it. `safe_load` then
    `safe_dump` would write back a file with every comment deleted and every
    block reordered: a silent, total loss of the user's own writing, in a file
    the app told them was theirs to edit.
    """
    before = (sandbox.profile_dir / "lexicon.yaml").read_text()
    comments = [line for line in before.splitlines() if line.strip().startswith("#")]
    assert len(comments) >= 5, "fixture should be a commented document"
    # Including one INSIDE the block being edited, which is the one a naive
    # append-after-the-header or a YAML round-trip would lose.
    assert any("Fiscal years" in line for line in comments)

    config_service.add_to_stoplist(sandbox, "III")

    after = (sandbox.profile_dir / "lexicon.yaml").read_text()
    assert [line for line in after.splitlines() if line.strip().startswith("#")] == comments


def test_the_surface_stops_being_detected(sandbox) -> None:
    """The point of the stoplist: `detect()` never counts the token again, so it
    is never re-queued and never eligible for a paid lookup."""
    from venona.pipeline.acronyms import detect, load_lexicon

    text = "The battleship Alexander III submitted inaccurate coal consumption reports."
    _lex, stoplist, whitelist = load_lexicon(sandbox)
    assert "III" in detect(text, stoplist=stoplist, short_whitelist=whitelist)

    config_service.add_to_stoplist(sandbox, "III")
    _lex, stoplist, whitelist = load_lexicon(sandbox)
    assert "III" not in detect(text, stoplist=stoplist, short_whitelist=whitelist)


@pytest.mark.negative
def test_stoplisting_twice_does_not_write_twice(sandbox) -> None:
    assert config_service.add_to_stoplist(sandbox, "III") is True
    assert config_service.add_to_stoplist(sandbox, "III") is False
    assert (sandbox.profile_dir / "lexicon.yaml").read_text().count("  - III") == 1


@pytest.mark.negative
def test_an_already_present_entry_is_recognised_whatever_its_case(sandbox) -> None:
    """`US` is in the shipped stoplist. Asking again must be a no-op, not a
    duplicate line."""
    assert config_service.add_to_stoplist(sandbox, "us") is False
