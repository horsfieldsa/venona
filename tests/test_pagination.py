"""The pagination contract, endpoint by endpoint.

Every paginated list returns `items` plus a whole-set `total` and takes
`limit`/`offset` through `deps.page_window` (docs/EXTENDING.md). What these
prove is the part `test_route_contracts`' serialization sweep cannot: that two
pages are disjoint, that `total` describes the filtered set rather than the
page, and that the filters a client moves server-side actually constrain the
rows -- a filter that silently ignores its params looks exactly like a corpus
that happens to match everything.
"""

from __future__ import annotations

import helpers
from helpers import NOW
from venona.db import core as db
from venona.pipeline.acronyms import store_definition


def _definition(conn: db.Conn, surface: str, expansion: str) -> None:
    store_definition(
        conn,
        surface=surface,
        expansion=expansion,
        method="web_grounded",
        confidence=0.7,
        article_id=None,
        quote=f"The {expansion} ({surface}) is a thing.",
        url=f"https://example.com/{surface.lower()}",
        now=NOW,
    )


def _seed_glossary(conn: db.Conn, count: int) -> None:
    for index in range(count):
        # Two-letter tail keeps every generated surface's initials honest.
        _definition(conn, f"Q{index}A", f"Quotient {index} Authority")
    conn.commit()


# ── GET /api/glossary ────────────────────────────────────────────────


async def test_glossary_pages_are_disjoint_and_share_one_total(api_client, api_db) -> None:
    _seed_glossary(api_db, 8)

    first = (await api_client.get("/api/glossary?limit=3&offset=0")).json()
    second = (await api_client.get("/api/glossary?limit=3&offset=3")).json()

    assert len(first["items"]) == 3
    assert len(second["items"]) == 3
    assert not {r["id"] for r in first["items"]} & {r["id"] for r in second["items"]}
    # `total` is the whole filtered set, identical from every page of it.
    assert first["total"] == second["total"]
    assert first["total"] >= 8


async def test_glossary_q_filters_server_side_and_total_follows(api_client, api_db) -> None:
    _seed_glossary(api_db, 5)

    response = (await api_client.get("/api/glossary?q=Quotient 3")).json()

    assert [r["surface"] for r in response["items"]] == ["Q3A"]
    assert response["total"] == 1
    # `counts` stays whole-table: the status chips must read true from inside
    # any filter, or filtering to one status would zero every other chip.
    assert sum(response["counts"].values()) > 1


async def test_glossary_status_takes_a_comma_separated_set(api_client, api_db) -> None:
    # The UI's "grounded" chip is two statuses at once.
    _seed_glossary(api_db, 3)

    both = (await api_client.get("/api/glossary?status=active,ambiguous")).json()
    active = (await api_client.get("/api/glossary?status=active")).json()

    assert {r["status"] for r in both["items"]} <= {"active", "ambiguous"}
    assert active["total"] <= both["total"]
    assert both["total"] == both["counts"].get("active", 0) + both["counts"].get("ambiguous", 0)


# ── GET /api/curation/definitions ────────────────────────────────────


async def test_definitions_queue_offset_windows_the_ranked_list(api_client, api_db) -> None:
    _seed_glossary(api_db, 6)

    whole = (await api_client.get("/api/curation/definitions?limit=50")).json()
    first = (await api_client.get("/api/curation/definitions?limit=2&offset=0")).json()
    second = (await api_client.get("/api/curation/definitions?limit=2&offset=2")).json()

    surfaces = [i["surface"] for i in whole["items"]]
    assert [i["surface"] for i in first["items"]] == surfaces[:2]
    assert [i["surface"] for i in second["items"]] == surfaces[2:4]
    # `total_pending` describes the whole queue from any window of it.
    assert first["total_pending"] == whole["total_pending"]
    assert second["total_pending"] == whole["total_pending"]


def test_corpus_audit_memo_keys_on_the_database_and_both_inputs(conn) -> None:
    """The audit reads documents AND method-scoped definitions; a stamp over
    either alone goes stale on the other's writes. Keyed per-DSN because seeded
    test databases share a frozen clock and are indistinguishable by stamp."""
    from venona.services import corpus_check

    first = corpus_check.audit(conn)
    assert corpus_check.audit(conn) is first
    assert (conn.dsn, "web_grounded") in corpus_check._AUDIT_CACHE

    # A lookup grounds a new definition without touching `documents`.
    _definition(conn, "ZQB", "Zebra Quotient Boundary")
    conn.commit()
    second = corpus_check.audit(conn)
    assert second is not first

    # A verdict changes the audited set without changing the corpus.
    conn.execute(
        "UPDATE acronym_definitions SET status = 'rejected', decided_at = ? WHERE surface = 'ZQB'",
        ("2026-08-21T00:00:00Z",),
    )
    conn.commit()
    assert corpus_check.audit(conn) is not second


# ── GET /api/curation/merges ─────────────────────────────────────────


async def test_merges_wears_an_envelope_and_windows(api_client, api_db) -> None:
    """Was a bare array — the paging convention needs a `total` beside the
    page, and a bare list has nowhere to put one."""
    for index in range(4):
        api_db.execute(
            "INSERT INTO entity_mappings (from_id, to_id, merge_id, active, reason, created_at) "
            "VALUES (?, ?, ?, 1, 'test', ?)",
            (
                f"org:loser-{index}",
                "org:survivor",
                f"merge:{index}",
                f"2026-08-0{index + 1}T00:00:00Z",
            ),
        )
    api_db.commit()

    whole = (await api_client.get("/api/curation/merges?active_only=false")).json()
    page = (await api_client.get("/api/curation/merges?active_only=false&limit=2&offset=2")).json()

    assert whole["total"] >= 4
    assert [m["merge_id"] for m in page["items"]] == [m["merge_id"] for m in whole["items"][2:4]]
    assert page["total"] == whole["total"]


# ── GET /api/briefs/{id} neighbours ──────────────────────────────────


async def test_a_brief_names_its_archive_neighbours(api_client) -> None:
    """`older_id`/`newer_id` are server-computed so the reader's arrows work
    however little of the (now paged) archive index the client has loaded."""
    import pytest

    listing = (await api_client.get("/api/briefs?limit=3")).json()
    if len(listing["items"]) < 2:
        pytest.skip("seed corpus holds fewer than two briefs")

    newest, second = listing["items"][0], listing["items"][1]
    top = (await api_client.get(f"/api/briefs/{newest['id']}")).json()
    below = (await api_client.get(f"/api/briefs/{second['id']}")).json()

    assert top.get("newer_id") is None
    assert top.get("older_id") == second["id"]
    assert below.get("newer_id") == newest["id"]


async def test_glossary_rejects_a_window_postgres_would_choke_on(api_client) -> None:
    # `ge=1`: a non-positive limit reaches Postgres as a negative LIMIT and
    # 500s; the shared dependency turns it into a 422 at the boundary instead.
    assert (await api_client.get("/api/glossary?limit=0")).status_code == 422
    assert (await api_client.get("/api/glossary?offset=-1")).status_code == 422
    assert (await api_client.get("/api/glossary?limit=501")).status_code == 422


# ── GET /api/entities/{id}/connections and /sources ──────────────────


def _seed_neighbourhood(conn: db.Conn, count: int) -> None:
    """One hub with `count` distinct neighbours, one document each."""
    helpers.entity(conn, "company:hub", "Hub Industries")
    for index in range(count):
        other = f"prog:p{index}"
        doc = f"doc{index}"
        helpers.entity(conn, other, f"Programme {index}")
        helpers.document(conn, doc, domain=f"outlet{index}.test")
        helpers.relation(conn, f"a{index}", "company:hub", "COMPETES_FOR", other, doc=doc)
    conn.commit()


async def test_connection_pages_are_disjoint_and_share_one_total(api_client, api_db) -> None:
    _seed_neighbourhood(api_db, 8)

    first = (await api_client.get("/api/entities/company:hub/connections?limit=3&offset=0")).json()
    second = (await api_client.get("/api/entities/company:hub/connections?limit=3&offset=3")).json()

    assert len(first["items"]) == 3
    assert len(second["items"]) == 3
    assert not {r["edge_id"] for r in first["items"]} & {r["edge_id"] for r in second["items"]}
    assert first["total"] == second["total"] == 8


async def test_connections_carry_the_other_end_already_named(api_client, api_db) -> None:
    _seed_neighbourhood(api_db, 1)

    row = (await api_client.get("/api/entities/company:hub/connections")).json()["items"][0]

    # The point of the flat envelope: no second lookup to render one row.
    assert row["other_id"] == "prog:p0"
    assert row["other_name"] == "Programme 0"
    assert row["direction"] == "out"
    # And the id the evidence route takes comes from the server, in the order
    # the assertion was written rather than the order this entity was asked for.
    assert row["edge_id"] == "company:hub|COMPETES_FOR|prog:p0"


async def test_a_symmetric_connection_is_one_row_not_two(api_client, api_db) -> None:
    """`PARTNERS_WITH` is written from both sides and counted from one.

    `graph/writer.py` normalises the endpoints before it MERGEs; nothing in
    `pipeline/` does, so both directions really are on disk. Reading without the
    same swap split one partnership into two rows carrying half the publishers
    each -- and `edge_provenance` matches both directions, so it then reported
    the full count underneath a row claiming half of it.
    """
    helpers.entity(api_db, "company:a", "Alpha")
    helpers.entity(api_db, "company:b", "Beta")
    helpers.document(api_db, "d1", domain="one.test")
    helpers.document(api_db, "d2", domain="two.test")
    helpers.relation(api_db, "s1", "company:a", "PARTNERS_WITH", "company:b", doc="d1")
    helpers.relation(api_db, "s2", "company:b", "PARTNERS_WITH", "company:a", doc="d2")
    api_db.commit()

    body = (await api_client.get("/api/entities/company:a/connections")).json()

    assert body["total"] == 1, body["items"]
    # Both publishers land on the one row rather than one each on two.
    assert body["items"][0]["publisher_count"] == 2


async def test_connections_filter_by_predicate_server_side(api_client, api_db) -> None:
    _seed_neighbourhood(api_db, 3)
    helpers.entity(api_db, "govt:owner", "An Office")
    helpers.document(api_db, "dm", domain="manager.test")
    helpers.relation(api_db, "am", "govt:owner", "MANAGES", "prog:p0", doc="dm")
    api_db.commit()

    body = (await api_client.get("/api/entities/prog:p0/connections?predicate=MANAGES")).json()

    assert body["total"] == 1
    assert body["items"][0]["predicate"] == "MANAGES"
    assert body["items"][0]["direction"] == "in"


async def test_connections_refuse_to_be_walked_past_the_ceiling(api_client, api_db) -> None:
    """409, not 403: the caller is allowed to be here, the window is not."""
    _seed_neighbourhood(api_db, 1)

    response = await api_client.get("/api/entities/company:hub/connections?offset=500")

    assert response.status_code == 409


async def test_source_pages_are_disjoint_and_share_one_total(api_client, api_db) -> None:
    _seed_neighbourhood(api_db, 6)

    first = (await api_client.get("/api/entities/company:hub/sources?limit=2&offset=0")).json()
    second = (await api_client.get("/api/entities/company:hub/sources?limit=2&offset=2")).json()

    assert len(first["items"]) == 2
    assert len(second["items"]) == 2
    assert not {r["article_id"] for r in first["items"]} & {
        r["article_id"] for r in second["items"]
    }
    # `total` counts DISTINCT documents, so it matches what paging can return.
    assert first["total"] == second["total"] == 6


async def test_every_source_page_is_stable_when_dates_tie(api_client, api_db) -> None:
    """`helpers.document` stamps them all with NOW, which is the hard case.

    Ordering on `published_at` alone leaves ties to whatever the heap returned,
    and a window over that order repeats a row on one page and drops it from the
    next. The walk below has to see each document exactly once.
    """
    _seed_neighbourhood(api_db, 6)

    seen: list[str] = []
    for offset in range(0, 6, 2):
        page = (
            await api_client.get(f"/api/entities/company:hub/sources?limit=2&offset={offset}")
        ).json()
        seen += [r["article_id"] for r in page["items"]]

    assert len(seen) == len(set(seen)) == 6


# ── the operator's lists ─────────────────────────────────────────────


def _people(conn: db.Conn, count: int) -> None:
    from venona.services import user_service

    for index in range(count):
        user_service.create(
            conn,
            user_id=f"user:p{index:02d}",
            email=f"p{index:02d}@example.com",
            role="reader",
            now=NOW,
        )
    conn.commit()


async def test_the_people_list_pages_and_reports_the_whole_population(api_client, api_db) -> None:
    """The stat block reads `total`, not the page.

    It read `items.length` before this endpoint had a window, which was the same
    number until the day it was not -- and a user count that silently means
    "users on page one" is worse than no count.
    """
    _people(api_db, 6)

    first = (await api_client.get("/api/admin/users?limit=2&offset=0")).json()
    second = (await api_client.get("/api/admin/users?limit=2&offset=2")).json()

    assert len(first["items"]) == 2
    assert not {u["id"] for u in first["items"]} & {u["id"] for u in second["items"]}
    assert first["total"] == second["total"] >= 6
    assert first["total"] > len(first["items"]), "a page is not the population"


async def test_the_audit_trail_pages_without_repeating_a_row(api_client, api_db) -> None:
    """`created_at` is ISO-8601 to the second and one action writes several rows,
    so the window needs its `id` tiebreak or page two disagrees with page one."""
    from venona.services import user_service

    user_service.create(
        api_db, user_id="user:audited", email="audited@example.com", role="reader", now=NOW
    )
    for index in range(6):
        user_service.record_event(
            api_db,
            event_id=f"ev:{index:02d}",
            user_id="user:audited",
            actor=None,
            action="suspended" if index % 2 else "reinstated",
            detail=f"change {index}",
            now=NOW,
        )
    api_db.commit()

    first = (await api_client.get("/api/admin/events?limit=3&offset=0")).json()
    second = (await api_client.get("/api/admin/events?limit=3&offset=3")).json()

    assert len(first["items"]) == 3
    assert not {e["id"] for e in first["items"]} & {e["id"] for e in second["items"]}
    assert first["total"] == second["total"] >= 6


async def test_a_window_refuses_a_limit_it_cannot_serve(api_client) -> None:
    """The `page_window` boundaries, on the routes that just gained one.

    A non-positive limit reaches Postgres as a negative LIMIT and 500s, which is
    why these refuse at the edge rather than clamping: a silent clamp answers a
    smaller question than the one asked.
    """
    for path in ("/api/admin/users", "/api/admin/events", "/api/data/exports"):
        assert (await api_client.get(f"{path}?limit=0")).status_code == 422, path
        assert (await api_client.get(f"{path}?offset=-1")).status_code == 422, path

    assert (await api_client.get("/api/admin/users?limit=501")).status_code == 422
    assert (await api_client.get("/api/notifications/history?limit=201")).status_code == 422
