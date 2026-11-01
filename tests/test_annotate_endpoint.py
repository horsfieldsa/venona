"""`POST /api/annotate` -- contract C3's second producer.

The thing under test is not really "does it find MOSA". It is the pair of
properties the browser extension's DOM surgery is built on:

  * the runs reassemble into EXACTLY the text that was submitted, for any input,
    so a client can walk them by length and splice them back into the same text
    nodes;
  * a strictness-1 alias does not match an ordinary English word, because on a
    page venona did not write there is no retrieval set to gate it.

Neither is visible from the app, which is why they are pinned here.
"""

from __future__ import annotations

import pytest
from venona.db import core as db
from venona.services import annotate_service
from venona.services.alias_index import build_index

pytestmark = pytest.mark.anyio


# ── run reassembly ──────────────────────────────────────────────────

REASSEMBLY_CASES = [
    ("plain", "The Army selected MOSA for CJADC2 integration."),
    ("no matches at all", "Nothing here resembles a defence programme."),
    # Python offsets are code points, JavaScript's are UTF-16 units. The client
    # never uses a server offset, but if the runs did not concatenate exactly it
    # would have nothing to walk either.
    ("astral plane", "A 🛰 satellite and MOSA in one line."),
    ("astral before and after", "🛰🛰 MOSA 🛰🛰 DARPA 🛰"),
    # NFC is deliberately NOT applied: it changes a string's LENGTH, and the
    # caller splices these runs into the DOM nodes the text came from.
    ("decomposed", "Café and MOSA in the same sentence."),
    ("composed", "Café and MOSA in the same sentence."),
    ("mixed forms", "Café then Café then MOSA."),
    ("tabs and newlines", "The Army\tselected\nMOSA today."),
    ("leading and trailing space", "   MOSA sits between spaces   "),
]


@pytest.mark.parametrize("label,text", REASSEMBLY_CASES, ids=[c[0] for c in REASSEMBLY_CASES])
async def test_runs_reassemble_the_submitted_text(api_client, label: str, text: str) -> None:
    res = await api_client.post("/api/annotate", json={"blocks": [{"id": "b0", "text": text}]})
    assert res.status_code == 200, res.text
    runs = res.json()["blocks"][0]["runs"]
    assert "".join(run["t"] for run in runs) == text


async def test_every_block_comes_back_under_its_own_id(api_client) -> None:
    blocks = [{"id": f"b{i}", "text": f"Block {i} mentions MOSA."} for i in range(5)]
    res = await api_client.post("/api/annotate", json={"blocks": blocks})
    body = res.json()

    assert [b["id"] for b in body["blocks"]] == [b["id"] for b in blocks]
    for sent, got in zip(blocks, body["blocks"], strict=True):
        assert "".join(run["t"] for run in got["runs"]) == sent["text"]


async def test_a_run_carries_at_most_one_reference(api_client) -> None:
    """Contract C3: runs never nest and never carry two refs."""
    res = await api_client.post(
        "/api/annotate",
        json={"blocks": [{"id": "b0", "text": "MOSA and DARPA and DDIL together."}]},
    )
    for run in res.json()["blocks"][0]["runs"]:
        if run.get("r"):
            assert run.get("k") in {"entity", "glossary", "undefined"}


# ── generated acronyms: the entity has to be here somewhere ─────────


def _add_generated_acronym(conn: db.Conn, surface: str, entity_id: str) -> None:
    """What `acronym_variants` writes: an all-caps surface at the acronym tier,
    provenance `acronym_gen`, and no article anywhere that used it."""
    conn.execute(
        """
        INSERT INTO aliases (id, entity_id, surface, normalized, strictness,
                             is_acronym, added_by, created_at)
        VALUES (?, ?, ?, ?, 2, 1, 'acronym_gen', '2026-01-01T00:00:00Z')
        """,
        (f"alias:gen:{surface.lower()}", entity_id, surface, surface.lower()),
    )
    conn.commit()


async def test_a_generated_acronym_does_not_annotate_prose_that_never_names_it(
    api_client, conn: db.Conn
) -> None:
    """The defect this gate exists for, as a reader met it.

    `AUKUS Pillar II` generates `API`. Nobody has ever written `API` for it, so
    on a page about software the letters are the ordinary word and the underline
    is simply false -- and the extension puts it on somebody else's article.
    """
    _add_generated_acronym(conn, "API", "prog:next-generation-command-and-control")

    res = await api_client.post(
        "/api/annotate",
        json={"blocks": [{"id": "b0", "text": "The team shipped a REST API this quarter."}]},
    )
    refs = [run.get("r") for run in res.json()["blocks"][0]["runs"] if run.get("r")]
    assert "prog:next-generation-command-and-control" not in refs


async def test_a_generated_acronym_annotates_where_the_page_names_the_entity(
    api_client, conn: db.Conn
) -> None:
    """And the other half, which is why this is a gate and not a delete: in a
    document that names the entity, the bare acronym is the reader's question
    and the underline is the answer.

    Two blocks, because the whole point is that the naming may be paragraphs
    away -- the annotate path handles blocks one at a time, so a set derived
    per block would fail exactly here."""
    _add_generated_acronym(conn, "API", "prog:next-generation-command-and-control")

    res = await api_client.post(
        "/api/annotate",
        json={
            "blocks": [
                {"id": "b0", "text": "Next Generation Command and Control cleared its review."},
                {"id": "b1", "text": "The API contract lands next quarter."},
            ]
        },
    )
    blocks = {b["id"]: b for b in res.json()["blocks"]}
    refs = [run.get("r") for run in blocks["b1"]["runs"] if run.get("r")]
    assert "prog:next-generation-command-and-control" in refs


# ── strict_case: the reason this endpoint is not just `reannotate` ──


def _add_strictness_one_alias(conn: db.Conn, surface: str, entity_id: str) -> None:
    """Strictness 1 is the DEFAULT tier for an extracted entity name, but the
    seed fixture happens to contain none -- every alias in it is 0, 2 or 3. So
    the case the flag exists for has to be built explicitly."""
    conn.execute(
        """
        INSERT INTO aliases (id, entity_id, surface, normalized, strictness,
                             is_acronym, added_by, created_at)
        VALUES (?, ?, ?, ?, 1, 0, 'extraction', '2026-01-01T00:00:00Z')
        """,
        (f"alias:{surface.lower()}", entity_id, surface, surface.lower()),
    )
    conn.commit()


def _tier_a(conn: db.Conn) -> frozenset[str]:
    return frozenset(r["id"] for r in conn.execute("SELECT id FROM entities WHERE status='active'"))


def test_strict_case_rejects_an_ordinary_word(conn: db.Conn) -> None:
    """The failure this flag exists for, in miniature.

    Measured against the live corpus before it existed: 'a lattice of horizon
    effects on the foundry floor' produced three confident underlines at 0.9,
    none of them real.
    """
    _add_strictness_one_alias(conn, "Bellwether", "company:anduril-industries")
    index = build_index(conn)
    tier_a = _tier_a(conn)
    text = "The team built a bellwether of sensors."

    assert [m.ref for m in index.find(text, tier_a=tier_a)] == ["company:anduril-industries"]
    assert index.find(text, tier_a=tier_a, strict_case=True) == []


def test_strict_case_keeps_the_real_thing(conn: db.Conn) -> None:
    _add_strictness_one_alias(conn, "Bellwether", "company:anduril-industries")
    index = build_index(conn)
    matches = index.find("Bellwether shipped in March.", tier_a=_tier_a(conn), strict_case=True)
    assert [m.ref for m in matches] == ["company:anduril-industries"]


def test_a_sentence_may_capitalise_a_lowercase_alias(conn: db.Conn) -> None:
    """`company:u-s-army` really does carry the alias `the Army`, so exact equality
    would drop every sentence that happens to start with it."""
    _add_strictness_one_alias(conn, "the Agency", "govt:defense-advanced-research-projects-agency")
    index = build_index(conn)
    tier_a = _tier_a(conn)

    assert index.find("The Agency said so.", tier_a=tier_a, strict_case=True)
    assert index.find("Officials at the Agency said so.", tier_a=tier_a, strict_case=True)


def test_capitalisation_relaxation_is_one_directional(conn: db.Conn) -> None:
    """`Neutron` and `neutron` also differ by one character's case. Admitting
    that pair is exactly the false positive being prevented."""
    _add_strictness_one_alias(conn, "Neutron", "company:anduril-industries")
    index = build_index(conn)
    tier_a = _tier_a(conn)

    assert index.find("Neutron flew.", tier_a=tier_a, strict_case=True)
    assert index.find("A neutron star was seen.", tier_a=tier_a, strict_case=True) == []


def test_briefs_are_not_affected(conn: db.Conn) -> None:
    """`strict_case` defaults off, so the brief path is byte-identical to what
    it was. `tier_a` there is one brief's retrieval set, which is the gate."""
    _add_strictness_one_alias(conn, "Bellwether", "company:anduril-industries")
    index = build_index(conn)
    matches = index.find("a bellwether of sensors", tier_a=_tier_a(conn))
    assert [m.ref for m in matches] == ["company:anduril-industries"]


# ── what may and may not reach the wire ─────────────────────────────


async def test_no_ambiguous_placeholder_reaches_the_wire(api_client) -> None:
    """`ambiguous:<normalized>` is a placeholder that must resolve to a `gloss:`
    id or drop. Letting one through would put an id on the wire that no card
    endpoint can answer for. The seed has SDA with two readings."""
    res = await api_client.post(
        "/api/annotate",
        json={"blocks": [{"id": "b0", "text": "The SDA awarded a contract this week."}]},
    )
    body = res.json()
    for block in body["blocks"]:
        for run in block["runs"]:
            assert not (run.get("r") or "").startswith("ambiguous:")
    assert not any(ref.startswith("ambiguous:") for ref in body["refs"])


async def test_undefined_is_reported_as_undefined(api_client) -> None:
    """Invariant 3, on the wire. The seed carries CJADC2 as `undefined`."""
    res = await api_client.post(
        "/api/annotate",
        json={"blocks": [{"id": "b0", "text": "Work on PNT continues this year."}]},
    )
    kinds = {run.get("k") for run in res.json()["blocks"][0]["runs"] if run.get("r")}
    assert "undefined" in kinds


async def test_refs_cover_every_span_exactly_once(api_client) -> None:
    res = await api_client.post(
        "/api/annotate",
        json={
            "blocks": [
                {"id": "b0", "text": "MOSA and DARPA."},
                {"id": "b1", "text": "MOSA again, plus DDIL."},
            ]
        },
    )
    body = res.json()
    on_wire = {run["r"] for block in body["blocks"] for run in block["runs"] if run.get("r")}

    assert set(body["refs"]) == on_wire
    assert len(body["refs"]) == len(set(body["refs"])), "refs must be deduped"


async def test_marks_are_not_invented(api_client) -> None:
    """The client sends plain text and no marks, so no run should claim any.
    `response_model_exclude_none` is what keeps a 400-block page from shipping
    several thousand nulls."""
    res = await api_client.post(
        "/api/annotate", json={"blocks": [{"id": "b0", "text": "MOSA is an approach."}]}
    )
    for run in res.json()["blocks"][0]["runs"]:
        assert not {"b", "i", "code", "s", "href"} & run.keys()


# ── the endpoint keeps nothing ──────────────────────────────────────


async def test_annotate_writes_nothing(api_client, api_db: db.Conn) -> None:
    """Reading an article must not leave a trace in the corpus.

    The privacy argument for annotating server-side rests on this: the page text
    is computed over and dropped. If a table ever grows here, that argument is
    gone and `docs/DEPLOYMENT.md` is wrong.
    """
    tables = [
        row["name"]
        for row in api_db.execute(
            "SELECT table_name AS name FROM information_schema.tables WHERE table_schema = 'public'"
        )
    ]
    before = {t: api_db.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"] for t in tables}

    res = await api_client.post(
        "/api/annotate",
        json={
            "url": "https://defensescoop.com/some-article",
            "blocks": [{"id": "b0", "text": "The Army selected MOSA for CJADC2."}],
        },
    )
    assert res.status_code == 200

    after = {t: api_db.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"] for t in tables}
    assert before == after


# ── limits ──────────────────────────────────────────────────────────


async def test_too_many_blocks_is_refused(api_client) -> None:
    blocks = [{"id": f"b{i}", "text": "MOSA."} for i in range(401)]
    res = await api_client.post("/api/annotate", json={"blocks": blocks})
    assert res.status_code == 422


async def test_too_much_text_is_refused(api_client) -> None:
    """No single block exceeds its own cap; the total does."""
    blocks = [{"id": f"b{i}", "text": "x" * 9_000} for i in range(40)]
    res = await api_client.post("/api/annotate", json={"blocks": blocks})
    assert res.status_code == 422


async def test_an_empty_request_is_fine(api_client) -> None:
    res = await api_client.post("/api/annotate", json={"blocks": []})
    assert res.status_code == 200
    assert res.json()["blocks"] == []
    assert res.json()["refs"] == []


# ── index_version ───────────────────────────────────────────────────


def test_index_version_is_stable_when_nothing_changes(conn: db.Conn) -> None:
    assert annotate_service.index_version(conn) == annotate_service.index_version(conn)


def test_index_version_moves_when_an_alias_is_added(conn: db.Conn) -> None:
    before = annotate_service.index_version(conn)
    _add_strictness_one_alias(conn, "Bellwether", "company:anduril-industries")
    assert annotate_service.index_version(conn) != before
