"""What one brief's worth of cards costs the database.

`cards_batch` is called once on mount with twenty to sixty references and is
the single highest-leverage performance decision in the reader -- after it,
every hover is a Map lookup. It is also where the same work was being done
over and over: the merge map three times per card, and one query per distinct
neighbour of every card. None of that is visible in a response; it is visible
in how long a brief takes to become readable on a corpus with a year in it.
"""

from __future__ import annotations

from venona.db import core as db
from venona.graph import projector
from venona.services import entity_service


def _entity(conn: db.Conn, entity_id: str, name: str, status: str = "active") -> None:
    db.upsert(
        conn,
        "entities",
        {
            "id": entity_id,
            "type": "Company",
            "name": name,
            "canonical_key": name.lower(),
            "status": status,
            "relevance": 0.5,
            "first_seen": "t0",
            "last_seen": "t0",
            "created_at": "t0",
        },
        key=["id"],
    )


def _relation(conn: db.Conn, assertion_id: str, subject: str, obj: str) -> None:
    db.insert(
        conn,
        "assertions",
        {
            "id": assertion_id,
            "run_id": "run:hot",
            "article_id": "doc:hot",
            "cluster_id": "cluster:hot",
            "kind": "relation",
            "predicate": "SUPPLIES",
            "subject_id_raw": subject,
            "object_id_raw": obj,
            "evidence_quote": "one supplies the other.",
            "confidence": 0.9,
            "method": "extraction",
            "created_at": "t0",
        },
    )


def test_a_batch_of_cards_resolves_the_merge_map_once(conn: db.Conn, monkeypatch) -> None:
    """⭐ Each card needed the map three times -- to resolve the id asked for,
    to find the neighbours that arrived via a loser, and to gather the signals
    behind the whole closure.

    So a sixty-reference brief collapsed merge chains a hundred and eighty
    times to reach the same answer, on a table that grows with every merge a
    curator ever approves.
    """
    ids = []
    for i in range(6):
        entity_id = f"company:hot-{i}"
        _entity(conn, entity_id, f"Hot {i}")
        ids.append(entity_id)

    calls = {"n": 0}
    real = projector.resolve_mappings

    def counted(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(projector, "resolve_mappings", counted)

    cards, missing = entity_service.cards_batch(conn, ids)

    assert not missing and len(cards) == len(ids), "the fixture must build every card"
    assert calls["n"] == 1, (
        f"the merge map must be resolved once for the whole batch, not {calls['n']} times"
    )


def test_neighbours_are_read_in_one_query(conn: db.Conn, monkeypatch) -> None:
    """⭐ One query per DISTINCT neighbour, per card. A hub entity has hundreds,
    and each was its own round trip on the reader's hot path."""
    from venona.db.repositories import entity as entity_repo

    _entity(conn, "company:hub", "Hub")
    for i in range(8):
        _entity(conn, f"company:spoke-{i}", f"Spoke {i}")
        _relation(conn, f"assert:hot-{i}", "company:hub", f"company:spoke-{i}")

    calls = {"n": 0}
    real = entity_repo.neighbour_entities

    def counted(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(entity_repo, "neighbour_entities", counted)

    card = entity_service.entity_card(conn, "company:hub")

    assert card is not None
    assert card.neighbor_count == 8, "the fixture must produce neighbours, or this proves nothing"
    assert calls["n"] == 1, f"eight neighbours must be one read, not {calls['n']}"


def test_the_neighbourhood_read_is_bounded(conn: db.Conn) -> None:
    """A hub sits on tens of thousands of relations and the card draws ten, so
    an unbounded read loaded the whole hub to throw almost all of it away."""
    import inspect

    from venona.db.repositories import entity as entity_repo

    source = inspect.getsource(entity_repo.neighbour_assertions)
    assert "LIMIT ?" in source, "the neighbourhood read must be bounded"
    assert "ORDER BY" in source, (
        "an unordered LIMIT keeps whichever rows the heap returned, which is not "
        "the neighbours a reader is looking for"
    )
