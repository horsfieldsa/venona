"""What the tools actually return, against the seeded corpus.

The protocol tests prove the envelope; these prove the payload is worth having.
The one that matters most is the round trip -- relations prints an `edge_id`,
evidence takes it -- because that is the workflow the whole tool set is shaped
around, and it breaks silently if either end changes its format.
"""

from __future__ import annotations

import pytest
from venona.api.mcp import registry
from venona.api.mcp.render import BUDGET_CHARS
from venona.db import core as db


async def call(client, name, arguments=None):
    response = await client.post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        },
    )
    result = response.json()["result"]
    return result["content"][0]["text"], result["isError"]


@pytest.mark.anyio
async def test_search_resolves_a_surface_to_an_id(api_client) -> None:
    text, is_error = await call(api_client, "venona_search", {"q": "NGC2"})
    assert not is_error
    assert "prog:" in text


@pytest.mark.anyio
async def test_search_says_so_when_it_holds_nothing(api_client) -> None:
    """An empty result must read as a FINDING, not as a broken tool.

    Same honesty rule that makes a run which produced nothing `succeeded_partial`
    rather than `failed`: an agent that reads "no results" as an error retries,
    and an agent that reads it as "this does not exist" is wrong in the other
    direction. The wording says which one it is.
    """
    text, is_error = await call(api_client, "venona_search", {"q": "zzzznotathing"})
    assert not is_error
    assert "defence trade press" in text


@pytest.mark.anyio
async def test_relations_prints_an_edge_id_that_evidence_accepts(
    api_client, api_db: db.Conn
) -> None:
    """⭐ The round trip the tool set is designed around.

    relations -> pick an edge -> evidence -> the verbatim sentence. If either end
    changes its edge-id format this breaks, and it breaks as "the agent cannot
    cite anything" rather than as an exception.
    """
    row = api_db.execute(
        "SELECT subject_id_raw AS s FROM assertions "
        "WHERE kind='relation' AND retracted_at IS NULL AND subject_id_raw IS NOT NULL "
        "AND object_id_raw IS NOT NULL LIMIT 1"
    ).fetchone()
    assert row, "the seed corpus has no relation assertions"

    text, is_error = await call(api_client, "venona_relations", {"entity_id": row["s"]})
    assert not is_error, text
    edge_ids = [
        line.split("edge_id=", 1)[1].strip() for line in text.splitlines() if "edge_id=" in line
    ]
    assert edge_ids, f"no edge_id printed:\n{text}"

    evidence, is_error = await call(api_client, "venona_evidence", {"edge_id": edge_ids[0]})
    assert not is_error, evidence
    assert "<untrusted" in evidence
    assert "</untrusted>" in evidence


@pytest.mark.anyio
async def test_evidence_on_a_relationship_venona_does_not_hold(api_client) -> None:
    text, is_error = await call(
        api_client,
        "venona_evidence",
        {"subject_id": "org:nobody", "predicate": "MANAGES", "object_id": "prog:nothing"},
    )
    assert is_error
    assert "venona_ontology" in text, "the error must say how to check the predicate"


@pytest.mark.anyio
async def test_evidence_refuses_a_predicate_outside_the_whitelist(api_client) -> None:
    """`parse_edge_id` validates against the LIVE ontology per call, so a guessed
    predicate is a clean refusal rather than a misleading empty result."""
    text, is_error = await call(
        api_client,
        "venona_evidence",
        {"subject_id": "org:a", "predicate": "OPERATES", "object_id": "prog:b"},
    )
    assert is_error


@pytest.mark.anyio
async def test_annotate_reports_an_undefined_term_without_expanding_it(api_client) -> None:
    """Invariant 3, at the agent boundary.

    The corpus holds surfaces it has never been able to define. The tool must
    name them AND tell the model not to fill them in, because filling them in
    from parametric memory is exactly what venona exists to stop.
    """
    text, is_error = await call(
        api_client,
        "venona_annotate",
        {"text": "The Army selected MOSA for CJADC2 this week."},
    )
    assert not is_error
    assert "UNDEFINED" in text
    assert "Do not expand them from memory" in text


@pytest.mark.anyio
async def test_annotate_writes_nothing(api_client, api_db: db.Conn) -> None:
    """The privacy claim, for the agent surface.

    `tests/test_annotate_endpoint.py` makes it for the extension; this makes it
    for a tool an agent can call in a loop over a whole draft.
    """
    tables = [
        r["name"]
        for r in api_db.execute(
            "SELECT table_name AS name FROM information_schema.tables WHERE table_schema = 'public'"
        )
    ]
    before = {t: api_db.execute(f'SELECT COUNT(*) AS n FROM "{t}"').fetchone()["n"] for t in tables}
    await call(api_client, "venona_annotate", {"text": "PEO C3N added vendors to NGC2."})
    after = {t: api_db.execute(f'SELECT COUNT(*) AS n FROM "{t}"').fetchone()["n"] for t in tables}
    assert before == after


@pytest.mark.anyio
async def test_path_degrades_readably_without_neo4j(api_client) -> None:
    """`_NoGraph.driver` raises by design. A 500 here would reach the agent as a
    transport failure; what it should get is a sentence naming the tool that
    still works, because `venona_relations` reads SQLite -- the system of record."""
    text, is_error = await call(
        api_client,
        "venona_path",
        {
            "from_entity_id": "govt:defense-department",
            "to_entity_id": "prog:joint-all-domain-command-and-control",
        },
    )
    assert is_error
    assert "venona_relations" in text


@pytest.mark.anyio
async def test_briefs_requires_something_to_search_on(api_client) -> None:
    """It must not be possible to dump the archive by omitting every filter."""
    text, is_error = await call(api_client, "venona_briefs", {})
    assert is_error


@pytest.mark.anyio
async def test_briefs_search_renders_the_hits_the_service_found(api_client, conn) -> None:
    """⭐ The agent-facing archive search rendered EMPTY for every query.

    The tool read `results.results` through a `getattr` default while the model
    declares `items`, so the miss produced `[]` — and `render.briefs([])` says
    "no brief matches that", with a plausible explanation attached. The agent was
    told, unfalsifiably, that the archive held nothing. A default for a field the
    model declares turns a rename into a confident wrong answer.

    Anchored on the service's own result so it cannot pass by rendering the
    empty-state string.
    """
    from venona.services.brief_service import search_briefs

    expected = search_briefs(conn, q="a", entity=None, limit=25)
    assert expected.items, "the fixture corpus has no matching brief; this test needs one"

    text, is_error = await call(api_client, "venona_briefs", {"q": "a", "limit": 25})
    assert not is_error, text
    assert "no brief matches that" not in text, "the search rendered its empty state"
    assert expected.items[0].brief.id in text


@pytest.mark.anyio
async def test_ontology_lists_the_whole_predicate_whitelist(api_client) -> None:
    from venona.ontology import get_ontology

    text, is_error = await call(api_client, "venona_ontology")
    assert not is_error
    for predicate in get_ontology().predicate_names:
        assert predicate in text


@pytest.mark.anyio
@pytest.mark.parametrize("name", sorted(registry.TOOLS))
async def test_every_tool_stays_inside_its_budget(api_client, api_db: db.Conn, name) -> None:
    """A response that blows a context window is a broken tool even when it is a
    correct one. Parameterised over the REGISTRY so a tool added tomorrow is
    covered without anyone remembering."""
    row = api_db.execute("SELECT id FROM entities LIMIT 1").fetchone()
    entity_id = row["id"] if row else "org:none"
    arguments = {
        "venona_search": {"q": "a"},
        "venona_entity": {"entity_id": entity_id},
        "venona_annotate": {"text": "The Army selected MOSA for CJADC2 and NGC2."},
        "venona_relations": {"entity_id": entity_id, "limit": 50},
        "venona_evidence": {"edge_id": f"{entity_id}|MANAGES|{entity_id}", "max_quotes": 20},
        "venona_path": {"from_entity_id": entity_id, "to_entity_id": "org:other"},
        "venona_briefs": {"q": "a", "limit": 25},
        "venona_ontology": {},
    }[name]
    text, _is_error = await call(api_client, name, arguments)
    assert len(text) <= BUDGET_CHARS[name] + 200, (
        f"{name} returned {len(text)} chars against a budget of {BUDGET_CHARS[name]}"
    )


@pytest.mark.anyio
async def test_search_shows_one_card_once_when_two_ids_fold_onto_it(api_client, api_db) -> None:
    """A merge that left its loser `active` made search repeat itself.

    On the live corpus both `company:anduril-industries` and the possessive-form
    entity it had been merged into matched the same query, and `cards_batch`
    resolved each to the same survivor -- so the reader was shown one company,
    under one name, in two consecutive rows. The data fault is the real defect
    and is fixed separately; this is the tool declining to be the thing that
    reveals it by repeating itself.
    """
    from helpers import NOW, entity

    entity(api_db, "company:widget-works", "Widget Works")
    entity(api_db, "company:widget-works-s", "Widget Works's")
    db.upsert(
        api_db,
        "entity_mappings",
        {
            "from_id": "company:widget-works-s",
            "to_id": "company:widget-works",
            "merge_id": "merge:widget",
            "active": 1,
            "reason": "auto: model",
            "created_at": NOW,
        },
        key=["from_id", "merge_id"],
    )
    api_db.commit()

    text, is_error = await call(api_client, "venona_search", {"q": "Widget Works"})

    assert not is_error
    assert text.count("company:widget-works") == 1
