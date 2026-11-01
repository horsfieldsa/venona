"""Acronym grounding: the negative cases.

Invariant 3 says a definition exists only if traceable to a sentence in a
document. What matters most is therefore what the system REFUSES to accept — a
wrong expansion becomes an alias and corrupts entity resolution downstream,
silently and permanently.

These are the four rejections named in the plan, plus the ambiguity case.
"""

from __future__ import annotations

import pytest
from helpers import NOW
from venona.db import core as db
from venona.db.models import verify_expansion
from venona.pipeline.acronyms import (
    collapse_equivalent,
    detect,
    ground_in_article,
    known_definition,
    link_to_entities,
    mark_undefined,
    reconcile_ambiguity,
    store_definition,
)
from venona.pipeline.web_grounding import comparable

LEXICON = {"next generation": ["next-gen", "nextgen", "ng"]}


# ── detection ────────────────────────────────────────────────────────


def test_detects_acronym_shapes() -> None:
    text = "The NGC2 program uses C4ISR data and MQ-9 feeds under DDIL conditions."
    found = detect(text, stoplist=set(), short_whitelist=set())
    assert {"NGC2", "C4ISR", "MQ-9", "DDIL"} <= set(found)


@pytest.mark.negative
def test_stoplist_and_length_filter_apply() -> None:
    """`US`, `AI`, `IT` are noise; two-character tokens need whitelisting."""
    text = "The US and AI teams met. C2 was discussed. IT was fine."
    found = detect(text, stoplist={"US", "AI", "IT"}, short_whitelist=set())
    assert "US" not in found and "AI" not in found and "IT" not in found
    assert "C2" not in found, "2 chars, not whitelisted"

    found = detect(text, stoplist={"US", "AI", "IT"}, short_whitelist={"C2"})
    assert "C2" in found


# ── tier 1: in-article ───────────────────────────────────────────────


def test_in_article_grounding_stores_the_sentence(conn: db.Conn) -> None:
    text = (
        "The Army continues work on the program. The service is mandating a "
        "Modular Open Systems Approach (MOSA) so components can be replaced. "
        "Other work continues."
    )
    grounded = ground_in_article(conn, article_id="doc:x", text=text, lexicon=LEXICON, now="t")
    assert ("MOSA", "Modular Open Systems Approach") in grounded

    row = conn.execute(
        "SELECT * FROM acronym_definitions WHERE surface = 'MOSA' AND method='in_article'"
    ).fetchone()
    assert row["confidence"] == 1.0
    assert row["grounding_article_id"] == "doc:x"
    # The evidence is the SENTENCE, not the bare match.
    assert "mandating" in row["grounding_quote"]
    assert row["status"] in {"active", "ambiguous"}


@pytest.mark.negative
def test_fabricated_parenthetical_is_rejected(conn: db.Conn) -> None:
    """REJECTION 1: initials that do not generate the acronym.

    This is the gate that stops a hallucinated or coincidental parenthetical
    becoming an alias.
    """
    text = "Critics cite the Fabricated Widget Program (NGC2) claim repeatedly."
    grounded = ground_in_article(conn, article_id="doc:y", text=text, lexicon=LEXICON, now="t")
    assert not any(a == "NGC2" for a, _ in grounded)

    row = conn.execute("SELECT * FROM acronym_definitions WHERE surface = 'NGC2'").fetchone()
    assert row is None, "an unverifiable pairing must not be stored at all"


# ── tier 2: in-corpus ────────────────────────────────────────────────


def test_in_corpus_reuse_keeps_the_original_provenance(conn: db.Conn) -> None:
    """The grounding must point at the document where the definition actually
    appeared, not at whichever article is being read now."""
    text = "Officials described a Modular Open Systems Approach (MOSA) baseline."
    ground_in_article(conn, article_id="doc:origin", text=text, lexicon=LEXICON, now="t")

    found = known_definition(conn, "MOSA")
    assert found is not None
    assert found["grounding_article_id"] == "doc:origin"


# ── tier 3: web lookup verification ──────────────────────────────────


@pytest.mark.negative
def test_quote_must_come_from_text_we_fetched() -> None:
    """REJECTION 2: the model quoting itself.

    Measured during design: WebFetch gives the model a sub-model SUMMARY, not
    page text, so anything it 'quotes' is ungroundable. Tier 3 therefore
    verifies the quote is a substring of text WE extracted.
    """
    our_text = "The program uses a Modular Open Systems Approach for interfaces."
    invented = "MOSA stands for Modular Open Systems Approach, per DoD policy."
    assert comparable(invented) not in comparable(our_text)

    genuine = "uses a Modular Open Systems Approach for interfaces"
    assert comparable(genuine) in comparable(our_text)


@pytest.mark.negative
def test_expansion_must_appear_inside_the_quote() -> None:
    """REJECTION 3: a real quote that does not actually contain the expansion."""
    quote = "The service confirmed the approach at a briefing last week."
    expansion = "Modular Open Systems Approach"
    assert comparable(expansion) not in comparable(quote)


@pytest.mark.negative
def test_web_expansion_still_faces_the_initials_check() -> None:
    """REJECTION 4: a grounded quote whose expansion is not the acronym.

    All four checks are independent -- passing three is not enough.
    """
    assert not verify_expansion("MOSA", "Modular Open Systems")
    assert verify_expansion("MOSA", "Modular Open Systems Approach")


# ── tier 4: undefined ────────────────────────────────────────────────


def test_undefined_is_recorded_not_guessed(conn: db.Conn) -> None:
    """Visibly undefined beats confidently wrong, and surfaces a curation gap."""
    mark_undefined(conn, "CJADC2", now="t", sightings=4)

    row = conn.execute("SELECT * FROM acronym_definitions WHERE surface = 'CJADC2'").fetchone()
    assert row["status"] == "undefined"
    assert row["expansion"] is None
    assert row["confidence"] == 0.0
    assert row["sighting_count"] == 4


@pytest.mark.negative
def test_undefined_never_downgrades_a_grounded_entry(conn: db.Conn) -> None:
    text = "It relies on a Modular Open Systems Approach (MOSA) design."
    ground_in_article(conn, article_id="doc:z", text=text, lexicon=LEXICON, now="t")

    mark_undefined(conn, "MOSA", now="t", sightings=9)

    row = conn.execute(
        "SELECT status, expansion FROM acronym_definitions WHERE surface='MOSA'"
    ).fetchone()
    assert row["status"] != "undefined"
    assert row["expansion"] == "Modular Open Systems Approach"


# ── ambiguity ────────────────────────────────────────────────────────


@pytest.mark.negative
def test_second_expansion_makes_a_surface_ambiguous(conn: db.Conn) -> None:
    """REJECTION 5: never pick between two grounded meanings.

    SDA is Space Development Agency AND software-defined architecture. Both are
    kept, neither is auto-applied, and the card disambiguates -- choosing one
    would be a silent, unrecoverable error.
    """
    store_definition(
        conn,
        surface="SDA",
        expansion="Space Development Agency",
        method="in_article",
        confidence=1.0,
        article_id="doc:a",
        quote="The Space Development Agency (SDA) said.",
        now="t",
    )
    store_definition(
        conn,
        surface="SDA",
        expansion="Software Defined Architecture",
        method="in_article",
        confidence=1.0,
        article_id="doc:b",
        quote="A Software Defined Architecture (SDA) is used.",
        now="t",
    )

    rows = conn.execute(
        "SELECT status, expansion FROM acronym_definitions WHERE normalized = 'sda'"
    ).fetchall()
    assert len(rows) == 2, "both meanings survive"
    assert all(r["status"] == "ambiguous" for r in rows)

    # And the annotator must stop matching it.
    assert conn.execute("SELECT 1 FROM ambiguous_surfaces WHERE normalized = 'sda'").fetchone()


# ── entity linking ───────────────────────────────────────────────────


def test_definition_matching_an_entity_gets_linked(conn: db.Conn) -> None:
    """A definition that names a real program becomes a link; jargon stays a
    glossary entry so the graph is not polluted with non-entities."""
    store_definition(
        conn,
        surface="NGCC",
        expansion="Next Generation Command and Control",
        method="in_article",
        confidence=1.0,
        article_id="doc:c",
        quote="The Next Generation Command and Control (NGCC) effort continues.",
        now="t",
    )
    assert link_to_entities(conn) >= 1

    row = conn.execute(
        "SELECT entity_id FROM acronym_definitions WHERE surface = 'NGCC'"
    ).fetchone()
    assert row["entity_id"] == "prog:next-generation-command-and-control"


def test_jargon_stays_unlinked(conn: db.Conn) -> None:
    store_definition(
        conn,
        surface="TRL",
        expansion="Technology Readiness Level",
        method="in_article",
        confidence=1.0,
        article_id="doc:d",
        quote="Most cleared Technology Readiness Level (TRL) 6.",
        now="t",
    )
    link_to_entities(conn)

    row = conn.execute("SELECT entity_id FROM acronym_definitions WHERE surface = 'TRL'").fetchone()
    assert row["entity_id"] is None, "TRL is a process term, not a graph node"


# ── counts survive grounding ─────────────────────────────────────────


def test_grounding_an_acronym_does_not_reset_its_sighting_count(conn) -> None:
    """The review queue orders by sighting count, so overwriting it with 1
    buries a freshly grounded entry under every undefined one.

    Found live: `UAS` had been seen 11 times, was grounded from an FAA page,
    and came back reading `1×`. Grounding an acronym does not un-see it.
    """
    from venona.pipeline.acronyms import mark_undefined, store_definition

    mark_undefined(conn, "UAS", now="2026-08-05T00:00:00.000Z", sightings=11)

    store_definition(
        conn,
        surface="UAS",
        expansion="unmanned aircraft system",
        method="web_grounded",
        confidence=0.7,
        article_id=None,
        quote="What is an unmanned aircraft system (UAS)?",
        url="https://www.faa.gov/faq/what-unmanned-aircraft-system-uas",
        now="2026-08-05T00:00:00.000Z",
    )

    row = conn.execute(
        "SELECT expansion, sighting_count FROM acronym_definitions WHERE normalized = 'uas'"
    ).fetchone()
    assert row["expansion"] == "unmanned aircraft system"
    assert row["sighting_count"] == 11


# ── tier 3: a lookup that is starved of pages ────────────────────────
#
# Two live lookups (USC, IDIQ) reported `succeeded` and grounded nothing. The
# four gates above were not at fault: HALF the candidate URLs were never fetched
# at all, because .gov/.mil WAFs answered 403, and `candidates[:3]` counted each
# refusal as one of its three attempts.


class _Resp:
    def __init__(self, status: int, text: str = "") -> None:
        self.status, self.text = status, text

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class _Http:
    """Serves canned pages and records what was actually requested."""

    def __init__(self, pages: dict[str, _Resp]) -> None:
        self._pages, self.requested = pages, []

    async def get(self, url: str, **_kw) -> _Resp:
        self.requested.append(url)
        return self._pages.get(url, _Resp(404))


def _page(body: str) -> _Resp:
    # trafilatura needs real markup and >200 chars of it to extract anything.
    return _Resp(
        200,
        f"<html><body><article><p>{body} {'filler words here. ' * 40}</p></article></body></html>",
    )


def _ctx(http: _Http):
    from types import SimpleNamespace

    return SimpleNamespace(
        http=http,
        settings=SimpleNamespace(llm=SimpleNamespace(acronym_model="sonnet", acronym_effort="low")),
    )


def _fake_calls(monkeypatch, *, searches: list[list[str]], reads: list[dict]):
    """Patch `call_and_record`: `searches` per acronym_search, `reads` per read.

    Patched on `web_grounding`, which is where the calls are now made. Both
    grounding kinds share that drive loop, so these tests cover `define.py`'s
    retry behaviour too -- which is new, because its copy had no retry round.
    """
    from types import SimpleNamespace

    import venona.pipeline.web_grounding as mod

    state = {"search": 0, "read": 0}
    prompts: list[str] = []

    async def fake(ctx, task, *, user_content, **_kw):
        if task == "acronym_search":
            prompts.append(user_content)
            i = state["search"]
            state["search"] += 1
            urls = searches[i] if i < len(searches) else []
            return SimpleNamespace(
                ok=True,
                error=None,
                data={"acronym": "X", "candidate_urls": [{"url": u, "why": "x"} for u in urls]},
            )
        i = state["read"]
        state["read"] += 1
        return SimpleNamespace(
            ok=True, error=None, data=reads[i] if i < len(reads) else {"found": False}
        )

    monkeypatch.setattr(mod, "call_and_record", fake)
    return state, prompts


@pytest.mark.asyncio
async def test_a_refused_fetch_costs_a_candidate_not_a_read(monkeypatch) -> None:
    """A 403 must not consume one of the three chances to find a definition.

    Live, IDIQ proposed three URLs and two answered 403 -- so exactly one page
    was ever read, and the lookup ended undefined off a single source.
    """
    from venona.pipeline.acronyms import READS_PER_LOOKUP, lookup_web

    blocked, readable = "https://blocked.mil/a", "https://ok.org/"
    http = _Http(
        {
            blocked: _Resp(403),
            "https://blocked2.mil/b": _Resp(403),
            f"{readable}1": _page("Nothing defined here."),
            f"{readable}2": _page("Still nothing."),
            f"{readable}3": _page("Nor here."),
        }
    )
    state, _ = _fake_calls(
        monkeypatch,
        searches=[
            [blocked, "https://blocked2.mil/b", f"{readable}1", f"{readable}2", f"{readable}3"],
            [],
        ],
        reads=[{"found": False}] * 3,
    )

    trace: list[dict] = []
    assert await lookup_web(_ctx(http), "IDIQ", lexicon={}, trace=trace) is None

    assert state["read"] == READS_PER_LOOKUP, "three pages READ, refusals not counted"
    assert [t["reason"] for t in trace if t["reason"] == "fetch_status"] == ["fetch_status"] * 2
    assert len(http.requested) >= 5, "it worked past the blocked hosts"


@pytest.mark.asyncio
async def test_a_starved_lookup_retries_once_and_only_once(monkeypatch) -> None:
    """A bad first search used to end the attempt outright."""
    from venona.pipeline.acronyms import lookup_web

    http = _Http({"https://a.org/1": _page("Uses USC but never spells it out.")})
    state, prompts = _fake_calls(
        monkeypatch,
        searches=[["https://a.org/1"], ["https://a.org/1"], ["https://c.org/3"]],
        reads=[{"found": False}] * 4,
    )

    trace: list[dict] = []
    assert await lookup_web(_ctx(http), "USC", lexicon={}, trace=trace) is None

    assert state["search"] == 2, "exactly one retry, never a third round"
    assert "already tried" in prompts[1], "the retry is told what failed"
    assert "not_defined_in_page" in prompts[1]
    # The retry proposed only a URL round one had already burned.
    assert any(t["reason"] == "retry_proposed_nothing_new" for t in trace)


@pytest.mark.asyncio
async def test_the_retry_can_ground_what_the_first_round_missed(monkeypatch) -> None:
    from venona.pipeline.acronyms import lookup_web

    quote = "The United States Code (USC) is the official compilation of federal statutes."
    http = _Http(
        {
            "https://title10.example/": _page("Title 10 covers the armed forces."),
            "https://ref.example/usc": _page(quote),
        }
    )
    _fake_calls(
        monkeypatch,
        searches=[["https://title10.example/"], ["https://ref.example/usc"]],
        reads=[
            {"found": False},
            {"found": True, "expansion": "United States Code", "quote": quote},
        ],
    )

    result = await lookup_web(_ctx(http), "USC", lexicon={}, trace=[])
    assert result is not None
    expansion, got_quote, url = result
    assert expansion == "United States Code"
    assert url == "https://ref.example/usc"
    assert "official compilation" in got_quote


# ── the attempt is recorded ──────────────────────────────────────────


def test_an_attempted_lookup_is_distinguishable_from_an_untried_one(conn) -> None:
    """`lookupState` in the card resets on the next hover, so without this the
    user is offered a first-time lookup for a question already answered -- and
    pays for it again."""
    from venona.pipeline.acronyms import mark_undefined

    mark_undefined(conn, "SEEN", now=NOW, sightings=3)
    row = conn.execute("SELECT * FROM acronym_definitions WHERE normalized = 'seen'").fetchone()
    assert row["last_attempt_at"] is None, "the stage merely saw it; nothing was tried"
    assert row["attempt_count"] == 0

    mark_undefined(conn, "TRIED", now=NOW, sightings=3, attempted_run_id="run:1")
    mark_undefined(conn, "TRIED", now=NOW, sightings=3, attempted_run_id="run:2")
    row = conn.execute("SELECT * FROM acronym_definitions WHERE normalized = 'tried'").fetchone()
    assert row["last_attempt_at"] == NOW
    assert row["last_attempt_run_id"] == "run:2", "the newest run holds the trace"
    assert row["attempt_count"] == 2


# ── the human escape hatch ───────────────────────────────────────────
#
# Invariant 3 waives the SENTENCE requirement for a person -- a curator who
# knows the term should not have to find a page that spells it out -- but not
# the audit trail, and not the typo guard.


def _patch_request(settings):
    from types import SimpleNamespace

    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))


def _undefined_row(conn, surface: str) -> str:
    from venona.pipeline.acronyms import mark_undefined

    mark_undefined(conn, surface, now=NOW, sightings=2)
    conn.commit()
    return conn.execute(
        "SELECT id FROM acronym_definitions WHERE normalized = ?",
        (surface.lower(),),
    ).fetchone()["id"]


@pytest.mark.negative
def test_a_manual_expansion_without_a_source_is_refused(conn) -> None:
    from fastapi import HTTPException
    from venona.api.routers.glossary import GlossaryPatch, patch_entry
    from venona.settings import get_settings

    entry_id = _undefined_row(conn, "USC")
    with pytest.raises(HTTPException) as exc:
        patch_entry(
            entry_id,
            GlossaryPatch(expansion="United States Code"),
            _patch_request(get_settings()),
            conn,
        )
    assert exc.value.status_code == 400
    assert "source URL" in str(exc.value.detail)


@pytest.mark.negative
def test_a_manual_expansion_that_fails_the_initials_check_is_refused(conn) -> None:
    """The one gate a human does NOT get to skip, because it costs nothing and
    catches the paste error rather than the judgement."""
    from fastapi import HTTPException
    from venona.api.routers.glossary import GlossaryPatch, patch_entry
    from venona.settings import get_settings

    entry_id = _undefined_row(conn, "USC")
    with pytest.raises(HTTPException) as exc:
        patch_entry(
            entry_id,
            GlossaryPatch(
                expansion="Unified Combatant Command",
                grounding_url="https://example.gov/x",
            ),
            _patch_request(get_settings()),
            conn,
        )
    assert exc.value.status_code == 400
    assert "lexicon.yaml" in str(exc.value.detail), "it names the escape hatch"


def test_a_sourced_manual_expansion_is_stored_as_a_human_decision(conn) -> None:
    from venona.api.routers.glossary import GlossaryPatch, patch_entry
    from venona.settings import get_settings

    entry_id = _undefined_row(conn, "USC")
    result = patch_entry(
        entry_id,
        GlossaryPatch(
            expansion="United States Code",
            grounding_url="https://en.wikipedia.org/wiki/United_States_Code",
            status="active",
        ),
        _patch_request(get_settings()),
        conn,
    )

    assert result["expansion"] == "United States Code"
    assert result["method"] == "manual", "distinguishable from an extraction"
    assert result["confidence"] == 1.0
    assert result["grounding"]["url"].startswith("https://en.wikipedia.org")
    # `decided_at` is what stops the next acronyms stage overturning it.
    assert result["decided_at"] is not None


@pytest.mark.negative
def test_a_quote_that_is_merely_the_expansion_is_not_evidence() -> None:
    """REJECTION 6, found live.

    A widened candidate pool grounded `USC` on the quote "United States Code" --
    eighteen characters lifted from a page heading, containing no "USC" at all.
    It passed every other gate: substring of the page, contains the expansion
    (it IS the expansion), initials generate the acronym. The answer happened to
    be right and the grounding proved nothing, which is the shape of the error
    invariant 3 exists to prevent.
    """
    from venona.pipeline.acronyms import mentions_acronym

    assert not mentions_acronym("United States Code", "USC")
    assert mentions_acronym("The United States Code, abbreviated USC, is...", "USC")
    # Periods are a styling of the same token.
    assert mentions_acronym("codified at 10 U.S.C. 4025", "USC")
    # Word boundaries matter: collapsing punctuation would find "usc" here.
    assert not mentions_acronym("a bonus certificate was issued", "USC")
    assert mentions_acronym("commonly called IDIQ contracts", "IDIQ")


@pytest.mark.negative
def test_a_hyphenated_surface_is_found_in_its_own_quote() -> None:
    """The letters-only pattern could not match a surface carrying punctuation.

    `AMP-HEL` generated `A[.\\s]?M[.\\s]?P[.\\s]?H[.\\s]?E[.\\s]?L`, which the
    literal string `AMP-HEL` fails on the hyphen -- so production rejected a
    textbook parenthetical, twice, at about $0.25 a time, and did the same for
    every other hyphenated or alphanumeric designation. These six quotes are
    verbatim from the traces of lookups that failed this way.
    """
    from venona.pipeline.acronyms import mentions_acronym

    live = [
        (
            "AMP-HEL",
            "as part of the first increment of the Army Multi-Purpose "
            "High Energy Laser (AMP-HEL) prototyping effort.",
        ),
        (
            "AIM-424",
            "The AIM-424 Long Range Air-to-Air Missile (LRAAM), also identified "
            "by the United States Navy as AIM-424 LRAAM - Malice",
        ),
        ("GBU-39", "The GBU-39/B Small Diameter Bomb (SDB) is a 250-pound glide bomb"),
        ("JTF-WHEM", "Joint Task Force Western Hemisphere (JTF-WHEM) was stood up"),
        ("LCI-X", "the Layered Counter-UAS Initiative-X (LCI-X) programme"),
        ("SAG-U", "the Security Assistance Group-Ukraine (SAG-U) reflections"),
    ]
    for surface, sentence in live:
        assert mentions_acronym(sentence, surface), surface

    # The gate still refuses what it exists to refuse. A suffix is a different
    # token, so the word boundary has to survive the literal form.
    assert not mentions_acronym("a MK-70mm round was fired", "MK-70")


@pytest.mark.negative
def test_typography_is_not_a_reason_to_throw_a_definition_away() -> None:
    """One writer's hyphen against another's space, and a page's own furniture.

    The expansion gate compares the model's answer against the sentence it
    quoted; the quote gate compares that sentence against what trafilatura
    extracted. Both were exact modulo whitespace, and both lost real answers to
    characters nobody chose deliberately.
    """
    quote = "the Army Multi-Purpose High Energy Laser (AMP-HEL) prototyping effort"
    assert comparable("Army Multi-Purpose High-Energy Laser") in comparable(quote)
    assert comparable("AV\u2019s 20kW-class LOCUST\u2122 LWS") in comparable(
        "AV's 20kW\u2013class LOCUST LWS integrated on the vehicle"
    )
    # Folding separators cannot admit a DIFFERENT expansion: the words and
    # their order still have to be there.
    assert comparable("Army Multi-Purpose Laser Cannon") not in comparable(quote)


@pytest.mark.negative
def test_only_the_initials_check_may_be_adjudicated() -> None:
    """The three gates that keep a model's memory out of the corpus are final.

    `adjudicable` is the whole of what an appeal can reach. If a check that
    verifies provenance ever joins it, a model gains the ability to overturn
    the evidence rather than the arithmetic (ADR 0178).
    """
    from venona.pipeline.acronyms import AcronymGrounding
    from venona.pipeline.define import EntityGrounding

    assert AcronymGrounding({}).adjudicable == frozenset({"initials_mismatch"})
    assert EntityGrounding.adjudicable == frozenset(), "entity grounding has no appeal"


@pytest.mark.negative
def test_a_sentence_fragment_is_not_a_second_meaning(conn: db.Conn) -> None:
    """The DARPA incident.

    An extraction produced `and the Defense Advanced Research Projects Agency`
    beside the real expansion. Compared on `normalize` those are two strings, so
    the surface went `ambiguous` -- and an ambiguous surface is not annotated AT
    ALL until a curator settles it. DARPA stopped being underlined everywhere,
    in the app and the extension, over two stopwords. The `reason` recorded
    against it read "2 accepted expansions" while one of them was a fragment a
    human had already rejected.

    `expansion_key` drops stopwords, so this is one reading seen twice. The
    SHORTER spelling is what the row keeps: the extra words are the glue, never
    the name.
    """
    for expansion, article in (
        ("Defense Advanced Research Projects Agency", "doc:a"),
        ("and the Defense Advanced Research Projects Agency", "doc:b"),
    ):
        store_definition(
            conn,
            surface="DARPA",
            expansion=expansion,
            method="in_article",
            confidence=1.0,
            article_id=article,
            quote=f"{expansion} (DARPA) said so.",
            now="t",
        )

    rows = conn.execute(
        "SELECT expansion, status FROM acronym_definitions WHERE normalized='darpa'"
    ).fetchall()
    assert len(rows) == 1, "one reading, not two"
    assert rows[0]["expansion"] == "Defense Advanced Research Projects Agency"
    assert rows[0]["status"] != "ambiguous"
    assert not conn.execute("SELECT 1 FROM ambiguous_surfaces WHERE normalized='darpa'").fetchone()


@pytest.mark.negative
def test_a_spelling_variant_stays_ambiguous_for_the_curator(conn: db.Conn) -> None:
    """The other half, and the line the fix must not cross.

    `MOSA` was marked ambiguous over `Systems` against `System`. That looks like
    the same bug and is not: `definition_queue` inspects readings that differ
    only in spelling and proposes `same_thing` for a curator to collapse. Folding
    plurals here would settle by machine what that flow exists to have settled by
    a person, and would delete the proposal rather than answer it.
    """
    for expansion, article in (
        ("Modular Open Systems Approach", "doc:a"),
        ("Modular Open System Approach", "doc:b"),
    ):
        store_definition(
            conn,
            surface="MOSA",
            expansion=expansion,
            method="in_article",
            confidence=1.0,
            article_id=article,
            quote=f"A {expansion} (MOSA) design.",
            now="t",
        )

    assert conn.execute("SELECT 1 FROM ambiguous_surfaces WHERE normalized='mosa'").fetchone()


# ── equivalent readings collapse onto one row ────────────────────────


def test_a_sighting_of_a_pre_split_reading_does_not_crash(conn: db.Conn) -> None:
    """The `IntegrityError` that killed the acronyms stage on a live run.

    Two rows saying the same thing is a state `store_definition` no longer
    creates but historically did -- 19 surfaces in the live corpus are still in
    it, `gloss:nro` beside `gloss:nro--and-the-national-reconnaissance-office`.

    Re-sighting one of them then crashed. `entry_id` was assigned on every
    iteration of the equivalence loop while `expansion` was reassigned only
    under the length test, so the two came from DIFFERENT rows: the upsert keys
    on `id`, so it wrote the shortest expansion onto the LAST row and collided
    with the row already holding it under `UNIQUE (normalized, expansion)`. Not
    a silent overwrite -- a hard crash that took the stage down with it.
    """
    for entry_id, expansion in (
        ("gloss:nro", "National Reconnaissance Office"),
        ("gloss:nro--and-the", "and the National Reconnaissance Office"),
    ):
        conn.execute(
            "INSERT INTO acronym_definitions "
            "  (id, surface, normalized, expansion, method, confidence, first_seen, last_seen) "
            "VALUES (?, 'NRO', 'nro', ?, 'in_article', 1.0, 't', 't')",
            (entry_id, expansion),
        )

    # The sighting that used to crash: equivalent to both, identical to neither.
    store_definition(
        conn,
        surface="NRO",
        expansion="of the National Reconnaissance Office",
        method="in_article",
        confidence=1.0,
        article_id="doc:nro",
        quote="A unit of the National Reconnaissance Office (NRO) said.",
        now="t",
    )

    rows = conn.execute(
        "SELECT id, expansion FROM acronym_definitions WHERE normalized = 'nro' ORDER BY id"
    ).fetchall()
    # The winner keeps the shortest spelling, and it keeps it on its OWN row.
    winner = [r for r in rows if r["expansion"] == "National Reconnaissance Office"]
    assert len(winner) == 1, "the clean spelling lands on exactly one row"
    assert winner[0]["id"] == "gloss:nro", "and on the row that already held it"


def test_collapse_folds_a_pre_split_reading(conn: db.Conn) -> None:
    """`collapse_equivalent` repairs the rows already in that state.

    Two of them in one automaton is the failure it exists to stop:
    `build_index` adds every glossary row under its surface and
    `Automaton.add_word` on a repeated key silently REPLACES, so which expansion
    a hover card showed came down to SQLite row order.
    """
    for entry_id, expansion in (
        ("gloss:thaad", "Terminal High Altitude Area Defense"),
        ("gloss:thaad--the", "the Terminal High Altitude Area Defense"),
    ):
        conn.execute(
            "INSERT INTO acronym_definitions "
            "  (id, surface, normalized, expansion, method, confidence, "
            "   sighting_count, article_count, first_seen, last_seen) "
            "VALUES (?, 'THAAD', 'thaad', ?, 'in_article', 1.0, 3, 2, 't', 't')",
            (entry_id, expansion),
        )

    folded, contested = collapse_equivalent(conn)
    assert folded == ["thaad"] and not contested

    rows = conn.execute(
        "SELECT id, expansion, sighting_count FROM acronym_definitions WHERE normalized = 'thaad'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["expansion"] == "Terminal High Altitude Area Defense"
    # Evidence is folded, never reset -- collapsing must not make a
    # well-attested definition look freshly seen.
    assert rows[0]["sighting_count"] == 3


def test_collapse_never_touches_a_reading_a_curator_ruled_on(conn: db.Conn) -> None:
    """Where a verdict sits on one of two still-indexed rows, the clean
    expansion and the ruling can be on different rows. Rewriting or deleting
    either edits the verdict, so these are reported instead of guessed at --
    five surfaces in the live corpus are exactly this."""
    for entry_id, expansion, decided in (
        ("gloss:csis", "Center for Strategic and International Studies", None),
        ("gloss:csis--at-the", "at the Center for Strategic and International Studies", "t"),
    ):
        conn.execute(
            "INSERT INTO acronym_definitions "
            "  (id, surface, normalized, expansion, method, confidence, decided_at, "
            "   first_seen, last_seen) "
            "VALUES (?, 'CSIS', 'csis', ?, 'in_article', 1.0, ?, 't', 't')",
            (entry_id, expansion, decided),
        )

    folded, contested = collapse_equivalent(conn)
    assert folded == [] and contested == ["csis"], "reported, not silently skipped"
    kept = conn.execute(
        "SELECT COUNT(*) c FROM acronym_definitions WHERE normalized = 'csis'"
    ).fetchone()
    assert kept["c"] == 2


# ── releasing an ambiguous surface ───────────────────────────────────


def test_an_editorial_ambiguity_is_never_released(conn: db.Conn) -> None:
    """Seed-declared ambiguity has no expansions at all, and reading that as
    "one meaning" deleted NINE of the ten on the first run after `make seed`.

    `fixtures/seed/entities.yaml` marks `TITAN`, `Sentinel`, `the Army` and the
    rest with a reason and no `acronym_definitions` rows -- they are not
    acronyms with two readings, they are surfaces a person said to leave alone.
    `resolve.py` reads this table as its ambiguity guard, so releasing one lets
    the next assertion naming Titan auto-resolve to a single owner.
    """
    # Already present: the fixture is seeded from the same `ambiguous:` block,
    # which is the state this guards.
    assert conn.execute("SELECT 1 FROM ambiguous_surfaces WHERE normalized='titan'").fetchone()
    assert not conn.execute(
        "SELECT 1 FROM acronym_definitions WHERE normalized='titan' AND expansion IS NOT NULL"
    ).fetchone(), "no expansions at all -- that is the point"

    assert "titan" not in reconcile_ambiguity(conn)
    assert conn.execute("SELECT 1 FROM ambiguous_surfaces WHERE normalized='titan'").fetchone()


def test_a_curators_keep_both_is_never_released(conn: db.Conn) -> None:
    """`POST /glossary/{surface}/keep-both` stamps `decided_at` with
    `status='ambiguous'`. Two readings differing only by a stopword fold to one
    `expansion_key`, so without the check this reverses an explicit verdict on
    the next nightly run -- 37 rows in the live corpus carry that stamp."""
    for entry_id, expansion in (
        ("gloss:rf", "Radio Frequency"),
        ("gloss:rf--on", "on Radio Frequency"),
    ):
        conn.execute(
            "INSERT INTO acronym_definitions "
            "  (id, surface, normalized, expansion, method, confidence, status, decided_at, "
            "   first_seen, last_seen) "
            "VALUES (?, 'RF', 'rf', ?, 'in_article', 1.0, 'ambiguous', 't', 't', 't')",
            (entry_id, expansion),
        )
    conn.execute(
        "INSERT INTO ambiguous_surfaces (normalized, reason, created_at) "
        "VALUES ('rf', '2 meanings, kept by a curator', 't')"
    )

    assert reconcile_ambiguity(conn) == []
    assert conn.execute("SELECT 1 FROM ambiguous_surfaces WHERE normalized='rf'").fetchone()
    assert all(
        r["status"] == "ambiguous"
        for r in conn.execute("SELECT status FROM acronym_definitions WHERE normalized='rf'")
    )


def test_a_surface_with_one_reading_is_still_released(conn: db.Conn) -> None:
    """The case the function exists for: ambiguity decided on WRITE by an older,
    stricter rule, with nothing to re-examine it. `MOSA` and `DARPA` were both
    stuck there with no way out but a curator."""
    conn.execute(
        "INSERT INTO acronym_definitions "
        "  (id, surface, normalized, expansion, method, confidence, status, first_seen, last_seen) "
        "VALUES ('gloss:vtol', 'VTOL', 'vtol', 'Vertical Take-Off and Landing', "
        "        'in_article', 1.0, 'ambiguous', 't', 't')"
    )
    conn.execute(
        "INSERT INTO ambiguous_surfaces (normalized, reason, created_at) "
        "VALUES ('vtol', '2 accepted expansions', 't')"
    )

    assert "vtol" in reconcile_ambiguity(conn)
    assert not conn.execute("SELECT 1 FROM ambiguous_surfaces WHERE normalized='vtol'").fetchone()
    assert (
        conn.execute("SELECT status FROM acronym_definitions WHERE id='gloss:vtol'").fetchone()[
            "status"
        ]
        == "active"
    )


# ── the nightly sweep stops re-paying for the undefinable ────────────


def test_a_surface_a_lookup_already_failed_on_is_not_offered_again(conn: db.Conn) -> None:
    """⭐ The cap spent on the same doomed set, every night, forever.

    Tier 3 selects the HIGHEST-sighted ungrounded surfaces, so a surface a
    lookup already failed to ground sorts to the front of the queue again the
    next night — and the night after. Most of them are undefinable (a typo, an
    OCR artefact, a string that was never an acronym), so the whole per-run cap
    goes on them and the terms that arrived today are never reached.
    """
    from venona.db.repositories import pipeline_acronyms as repo
    from venona.pipeline.acronyms import mark_undefined
    from venona.pipeline.resolve import _days_before

    mark_undefined(conn, "ZQXJ", now=NOW, sightings=9, attempted_run_id="run:1")
    mark_undefined(conn, "SEEN", now=NOW, sightings=9)
    conn.commit()

    window = _days_before(NOW, 30)
    attempted = repo.recently_attempted(conn, window)

    assert "zqxj" in attempted
    assert "seen" not in attempted, (
        "a surface the stage merely saw was counted as attempted; no lookup was paid for it"
    )


def test_the_refusal_expires_so_a_term_can_become_definable(conn: db.Conn) -> None:
    """A retry WINDOW, not a permanent refusal. Some surfaces fail tier 3
    because the web has not caught up yet, and "never again" would bury a term
    that becomes definable next month."""
    from venona.db.repositories import pipeline_acronyms as repo
    from venona.pipeline.acronyms import mark_undefined
    from venona.pipeline.resolve import _days_before

    long_ago = _days_before(NOW, 400)
    mark_undefined(conn, "OLDTRY", now=long_ago, sightings=9, attempted_run_id="run:0")
    conn.commit()

    assert "oldtry" not in repo.recently_attempted(conn, _days_before(NOW, 30))
    assert "oldtry" in repo.recently_attempted(conn, _days_before(NOW, 500))


def test_the_candidate_filter_reads_the_attempt_stamp() -> None:
    """Asserted against the source, in this file's own style: driving the whole
    stage would need a model. What must not come back is the unfiltered
    `ungrounded` list feeding the cap."""
    import inspect

    from venona.pipeline import acronyms

    source = inspect.getsource(acronyms.run_acronyms)
    assert "recently_attempted" in source
    assert "acronym_retry_after_days" in source


# ── routine maintenance is not a degradation ─────────────────────────


def test_folding_and_releasing_do_not_degrade_the_run() -> None:
    """⭐ The mirror of "never report a partially useful run as failed".

    Folding two readings of one surface together, and releasing a surface that
    has stopped being ambiguous, are this stage DOING ITS JOB — and they happen
    on a healthy corpus most nights. Routed through `ctx.warn` they set
    `degraded`, so a run that did everything asked of it reported
    `succeeded_partial`. A status that says "degraded" on every row teaches a
    reader to ignore it, which is the same damage from the other direction.
    """
    from venona.pipeline.types import FrozenClock, RunContext, SeededIdGen
    from venona.settings import get_settings

    ctx = RunContext(
        run_id="run:t",
        db=None,
        llm=None,
        settings=get_settings(),
        clock=FrozenClock(),
        ids=SeededIdGen(0),
    )

    ctx.note("MOSA: folded duplicate readings into one")
    ctx.note("SDA: no longer ambiguous, released for annotation")
    assert not ctx.degraded, "routine maintenance made the run report succeeded_partial"
    # Nothing is HIDDEN by that: a notice still reaches the run's warnings.
    assert len(ctx.warnings) == 2

    ctx.warn("TITAN: two indexed readings say the same thing -- needs a person")
    assert ctx.degraded, "the one that needs a person must still degrade the run"


def test_the_stage_routes_each_message_to_the_right_channel() -> None:
    """Asserted against the source: driving the whole stage needs a model. What
    must not come back is a blanket `ctx.warn` over every message."""
    import inspect

    from venona.pipeline import acronyms

    source = inspect.getsource(acronyms.run_acronyms)
    assert "report.notices.append" in source
    assert "ctx.note(notice)" in source
    # And the contested case keeps its warning.
    assert "report.warnings.append" in source
