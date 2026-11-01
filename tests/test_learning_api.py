"""The learning surface, and the privacy stance that shapes it (ADR 0157).

The load-bearing tests here are the siblings of
`test_annotate_writes_nothing`: every learning GET grows zero rows anywhere,
and the one write grows exactly the two learning tables, keyed to the acting
user. `api_app` runs no auth middleware, so the anonymous client doubles as
the no-account case; the signed-in learner is a `Principal` stamped into the
ASGI scope exactly where `AuthMiddleware` would put it -- the boundary
itself is `tests/test_membership_gate.py`'s job.
"""

from __future__ import annotations

import httpx
import pytest
from helpers import NOW, entity
from venona.api.security import Principal
from venona.db import core as db
from venona.services import learning_cards

LEARNER_ID = "user:learner"
LEARNER_EMAIL = "learner@example.com"


class _StampPrincipal:
    """`AuthMiddleware` stashes the principal at `scope["state"]["principal"]`;
    this does only that, so the routers under test read identity through the
    same slot they do in production."""

    def __init__(self, app, principal: Principal) -> None:
        self.app = app
        self.principal = principal

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope.setdefault("state", {})["principal"] = self.principal
        await self.app(scope, receive, send)


@pytest.fixture
async def learner_client(api_app, api_db):
    from venona.services import user_service

    user_service.create(api_db, user_id=LEARNER_ID, email=LEARNER_EMAIL, role="reader", now=NOW)
    api_db.commit()
    principal = Principal(email=LEARNER_EMAIL, via="session", user_id=LEARNER_ID, role="reader")
    transport = httpx.ASGITransport(
        app=_StampPrincipal(api_app, principal), raise_app_exceptions=False
    )
    async with httpx.AsyncClient(transport=transport, base_url="https://t") as client:
        yield client


def _defined(
    conn: db.Conn,
    entity_id: str,
    name: str,
    *,
    quote: str | None = None,
    definition: str | None = None,
) -> None:
    """An active entity with an active, sourced definition -- deck material."""
    entity(conn, entity_id, name)
    db.upsert(
        conn,
        "entity_definitions",
        {
            "entity_id": entity_id,
            "definition": definition or f"{name} is a thing the corpus explains.",
            "method": "web_grounded",
            "confidence": 0.7,
            "grounding_quote": quote or f"{name} is a thing the corpus explains.",
            "grounding_url": "https://example.com/source",
            "status": "active",
            "first_seen": NOW,
            "last_seen": NOW,
        },
        key=["entity_id"],
    )


def _table_counts(conn: db.Conn) -> dict[str, int]:
    tables = [
        row["name"]
        for row in conn.execute(
            "SELECT table_name AS name FROM information_schema.tables WHERE table_schema = 'public'"
        )
    ]
    return {t: conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"] for t in tables}


# ── privacy: what is recorded, and for whom ──────────────────────────


async def test_learning_reads_write_nothing(learner_client, api_db) -> None:
    """Browsing the deck must not record that anybody browsed."""
    _defined(api_db, "company:anduril", "Anduril")
    api_db.commit()
    before = _table_counts(api_db)

    for path in (
        "/api/learning/queue",
        "/api/learning/progress",
        "/api/learning/overview",
        "/api/learning/counts",
        "/api/learning/settings",
    ):
        response = await learner_client.get(path)
        assert response.status_code == 200, (path, response.text)

    assert _table_counts(api_db) == before


async def test_one_review_grows_only_the_learning_tables_keyed_to_the_actor(
    learner_client, api_db
) -> None:
    _defined(api_db, "company:anduril", "Anduril")
    api_db.commit()
    before = _table_counts(api_db)

    response = await learner_client.post(
        "/api/learning/review", json={"card_key": "card:def:company:anduril", "rating": 3}
    )
    assert response.status_code == 200, response.text

    after = _table_counts(api_db)
    grew = {t for t in after if after[t] != before[t]}
    assert grew == {"learning_cards", "learning_reviews"}, grew
    for table in ("learning_cards", "learning_reviews"):
        rows = api_db.execute(f"SELECT user_id FROM {table}").fetchall()
        assert rows and all(r["user_id"] == LEARNER_ID for r in rows)


async def test_a_smuggled_user_id_is_a_422(learner_client, api_db) -> None:
    """`extra="forbid"` is the structural half of "the subject is always the
    actor" -- a body field that could name someone else does not parse."""
    _defined(api_db, "company:anduril", "Anduril")
    api_db.commit()
    response = await learner_client.post(
        "/api/learning/review",
        json={"card_key": "card:def:company:anduril", "rating": 3, "user_id": "user:victim"},
    )
    assert response.status_code == 422


async def test_a_rowless_credential_is_refused_cleanly(api_app) -> None:
    """A bearer with no user row -- the root token, an env break-glass admin --
    gets a 400 with a named reason, never a 200 over nobody's rows. (No
    principal at all while auth is off is a different case: that binds to the
    local operator, tested below.)"""
    bearer = Principal(email="root", via="bearer")
    transport = httpx.ASGITransport(
        app=_StampPrincipal(api_app, bearer), raise_app_exceptions=False
    )
    async with httpx.AsyncClient(transport=transport, base_url="https://t") as client:
        for method, path in (
            ("GET", "/api/learning/queue"),
            ("POST", "/api/learning/review"),
            ("GET", "/api/learning/progress"),
        ):
            response = await client.request(
                method, path, json={"card_key": "x", "rating": 3} if method == "POST" else None
            )
            assert response.status_code == 400, (path, response.status_code)
            assert response.json()["detail"]["error"] == "no_account"


# ── the deck ─────────────────────────────────────────────────────────


async def test_defined_entities_become_new_cards_and_grading_consumes_them(
    learner_client, api_db
) -> None:
    _defined(api_db, "company:anduril", "Anduril")
    _defined(api_db, "prog:cjadc2", "CJADC2")
    api_db.commit()

    queue = (await learner_client.get("/api/learning/queue")).json()
    keys = {c["card_key"] for c in queue["new"]}
    assert {"card:def:company:anduril", "card:def:prog:cjadc2"} <= keys
    assert queue["due"] == []

    graded = await learner_client.post(
        "/api/learning/review", json={"card_key": "card:def:company:anduril", "rating": 4}
    )
    assert graded.status_code == 200
    assert graded.json()["reps"] == 1

    queue = (await learner_client.get("/api/learning/queue")).json()
    assert "card:def:company:anduril" not in {c["card_key"] for c in queue["new"]}
    assert queue["counts"]["reviewed_today"] == 1
    assert queue["streak"] == 1


async def test_a_resolved_gloss_is_one_card_not_two(learner_client, api_db) -> None:
    """An acronym that resolved to an entity is the entity's card; serving the
    gloss too would put one fact on two schedules."""
    from venona.pipeline.acronyms import store_definition

    _defined(api_db, "tech:mosa", "MOSA")
    store_definition(
        api_db,
        surface="MOSA",
        expansion="Modular Open Systems Approach",
        method="web_grounded",
        confidence=0.7,
        article_id=None,
        quote="The Modular Open Systems Approach (MOSA) is policy.",
        url="https://example.com/mosa",
        now=NOW,
    )
    api_db.execute("UPDATE acronym_definitions SET entity_id = 'tech:mosa'")
    api_db.commit()

    queue = (await learner_client.get("/api/learning/queue")).json()
    keys = [c["card_key"] for c in queue["new"]]
    assert "card:def:tech:mosa" in keys
    assert not any(k.startswith("card:gloss:") for k in keys), keys


async def test_a_rejected_definition_goes_dormant_but_keeps_its_state(
    learner_client, api_db
) -> None:
    _defined(api_db, "company:anduril", "Anduril")
    api_db.commit()
    assert (
        await learner_client.post(
            "/api/learning/review", json={"card_key": "card:def:company:anduril", "rating": 1}
        )
    ).status_code == 200

    api_db.execute("UPDATE entity_definitions SET status = 'rejected'")
    api_db.commit()

    queue = (await learner_client.get("/api/learning/queue")).json()
    every_key = {c["card_key"] for c in queue["new"]} | {c["card_key"] for c in queue["due"]}
    assert "card:def:company:anduril" not in every_key

    row = api_db.execute("SELECT * FROM learning_cards").fetchone()
    assert row is not None and row["card_key"] == "card:def:company:anduril", (
        "dormant means kept, not deleted -- the memory state survives the fact's absence"
    )
    regraded = await learner_client.post(
        "/api/learning/review", json={"card_key": "card:def:company:anduril", "rating": 3}
    )
    assert regraded.status_code == 409


async def test_an_unknown_card_key_is_a_409(learner_client) -> None:
    response = await learner_client.post(
        "/api/learning/review", json={"card_key": "card:def:company:nope", "rating": 3}
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "not_servable"


# ── progress ─────────────────────────────────────────────────────────


async def test_retention_excludes_first_meetings(learner_client, api_db) -> None:
    """A new card's first grade is not a memory test; the day's series row
    must say so rather than flatter the number."""
    _defined(api_db, "company:anduril", "Anduril")
    api_db.commit()
    await learner_client.post(
        "/api/learning/review", json={"card_key": "card:def:company:anduril", "rating": 3}
    )

    progress = (await learner_client.get("/api/learning/progress")).json()
    today = progress["series"][0]
    assert today["reviews"] == 1
    assert today["graded"] == 0
    assert today["retention"] is None
    assert progress["reviewed_today"] == 1
    assert progress["streak"] == 1
    coverage = {c["type"]: c for c in progress["coverage"]}
    assert coverage["Company"]["started"] == 1
    assert coverage["Company"]["total"] == 1


async def test_settings_round_trip_and_merge_partially(learner_client) -> None:
    initial = (await learner_client.get("/api/learning/settings")).json()
    assert initial == {"new_per_day": 10, "daily_goal": 30, "retention_target": 0.9}

    patched = await learner_client.patch("/api/learning/settings", json={"new_per_day": 5})
    assert patched.status_code == 200
    assert patched.json() == {"new_per_day": 5, "daily_goal": 30, "retention_target": 0.9}
    assert (await learner_client.get("/api/learning/settings")).json()["new_per_day"] == 5


async def test_new_per_day_bounds_the_queue(learner_client, api_db) -> None:
    for index in range(4):
        _defined(api_db, f"company:c{index}", f"Corp {index}")
    api_db.commit()
    await learner_client.patch("/api/learning/settings", json={"new_per_day": 2})

    queue = (await learner_client.get("/api/learning/queue")).json()
    assert len(queue["new"]) == 2


# ── the terms overview answers the reader's three questions ──────────


async def test_the_overview_groups_cards_by_what_a_reader_asks(learner_client, api_db) -> None:
    """Worth another look / coming back next / known well -- each row with
    its MEANING beside it, because the page is a personal glossary, not a
    scheduler ledger."""
    for index in range(2):
        _defined(api_db, f"company:c{index}", f"Corp {index}")
    api_db.commit()
    # c0 forgotten on sight -> weak; c1 rated Easy -> healthy but not strong.
    await learner_client.post(
        "/api/learning/review", json={"card_key": "card:def:company:c0", "rating": 1}
    )
    await learner_client.post(
        "/api/learning/review", json={"card_key": "card:def:company:c1", "rating": 4}
    )

    overview = (await learner_client.get("/api/learning/overview")).json()
    assert [c["card_key"] for c in overview["attention"]] == ["card:def:company:c0"]
    assert [c["card_key"] for c in overview["upcoming"]] == ["card:def:company:c1"]
    assert overview["known"] == []
    assert overview["total"] == 2
    assert overview["attention"][0]["detail"] == "Corp 0 is a thing the corpus explains."

    # Grown strong, the card moves to the payoff list -- and stays visible in
    # upcoming when its return is what comes next.
    api_db.execute(
        "UPDATE learning_cards SET stability = 30 WHERE card_key = 'card:def:company:c1'"
    )
    api_db.commit()
    overview = (await learner_client.get("/api/learning/overview")).json()
    assert [c["card_key"] for c in overview["known"]] == ["card:def:company:c1"]


# ── the question bank: proposals, verdicts, and the gate ─────────────


def _proposed(conn: db.Conn, question_id: str, *, prompt: str) -> None:
    from venona.db.repositories import learning as learning_repo

    learning_repo.insert_question(
        conn,
        id=question_id,
        run_id="run:test",
        kind="qa",
        entity_id=None,
        prompt=prompt,
        choices=None,
        answer="An answer.",
        grounding_article_id="doc:1",
        grounding_quote="An answer it is.",
        grounding_span=None,
        now=NOW,
    )


async def test_a_question_reaches_a_deck_only_through_approval(
    learner_client, api_client, api_db
) -> None:
    """The review gate is the feature: `proposed` serves nobody."""
    _proposed(api_db, "question:q1", prompt="What is the answer?")
    api_db.commit()

    queue = (await learner_client.get("/api/learning/queue")).json()
    assert not any(c["card_key"].startswith("card:q:") for c in queue["new"])

    decided = await api_client.post(
        "/api/learning/questions/question:q1/decide", json={"action": "approve"}
    )
    assert decided.status_code == 200
    assert decided.json() == {"id": "question:q1", "status": "active"}

    queue = (await learner_client.get("/api/learning/queue")).json()
    assert any(c["card_key"] == "card:q:question:q1" for c in queue["new"])


async def test_a_second_verdict_is_a_409_not_a_second_decision(api_client, api_db) -> None:
    _proposed(api_db, "question:q1", prompt="What is the answer?")
    api_db.commit()
    assert (
        await api_client.post(
            "/api/learning/questions/question:q1/decide", json={"action": "approve"}
        )
    ).status_code == 200
    again = await api_client.post(
        "/api/learning/questions/question:q1/decide", json={"action": "reject"}
    )
    assert again.status_code == 409


async def test_the_questions_listing_and_preview_serve_the_admin_surface(
    api_client, api_db
) -> None:
    _proposed(api_db, "question:q1", prompt="What is the answer?")
    api_db.commit()
    page = (await api_client.get("/api/learning/questions?limit=10&offset=0")).json()
    assert page["total"] == 1 and page["items"][0]["id"] == "question:q1"

    preview = (await api_client.get("/api/learning/generate/preview")).json()
    # `RunPreview`, the one shape every launch dialog reads. What belongs to
    # this run alone travels in `extra`.
    assert set(preview) >= {"eligible", "will_attempt", "per_item_usd", "cost_sample"}
    assert preview["unit_plural"] == "articles"
    assert preview["extra"]["proposed_open"] == 1


def test_generated_items_are_verified_by_code(api_db) -> None:
    """`_store_question` writes nothing it cannot verify -- a paraphrased
    quote, a wrong index, a choice set without its answer, an echo of a
    question the bank already ruled on."""
    from venona.api.routers.learning import _store_question
    from venona.pipeline.types import FrozenClock, SeededIdGen

    class _Ctx:
        clock = FrozenClock()
        ids = SeededIdGen()

    batch = [{"id": "doc:1", "title": "T", "text_nfc": "The exact sentence is here."}]

    def attempt(item, seen=None):
        return _store_question(
            api_db, item, batch=batch, run_id="run:t", ctx=_Ctx(), seen=seen or set()
        )

    good = {
        "kind": "qa",
        "question": "Where is the sentence?",
        "answer": "Here",
        "source_index": 1,
        "quote": "The exact sentence is here.",
    }
    assert attempt(good) is None
    row = api_db.execute("SELECT * FROM llm_questions").fetchone()
    assert row["status"] == "proposed"
    assert row["grounding_span"] == "[0, 27]"

    assert attempt({**good, "quote": "The exact sentence is elsewhere."}) == "quote_not_found"
    assert attempt({**good, "source_index": 2}) == "bad_source_index"
    assert attempt({**good, "kind": "cloze"}) == "no_blank"
    assert (
        attempt({**good, "kind": "choice", "choices": ["a", "b", "c"], "answer": "z"})
        == "bad_choices"
    )
    assert attempt(good) == "duplicate", "the bank already holds this prompt"
    assert api_db.execute("SELECT COUNT(*) AS n FROM llm_questions").fetchone()["n"] == 1


async def test_an_auth_off_session_studies_as_the_local_operator(api_app, api_db) -> None:
    """`make dev` runs with auth disabled, and the person at the keyboard IS
    the user -- refusing to remember them would make the whole surface a dead
    end on every dev box. The binding is scoped to learning: everything else
    keeps recording NULL for an unauthenticated actor."""
    _defined(api_db, "company:anduril", "Anduril")
    api_db.commit()

    disabled = Principal(email="anonymous", via="disabled")
    transport = httpx.ASGITransport(
        app=_StampPrincipal(api_app, disabled), raise_app_exceptions=False
    )
    async with httpx.AsyncClient(transport=transport, base_url="https://t") as client:
        queue = await client.get("/api/learning/queue")
        assert queue.status_code == 200, queue.text

        graded = await client.post(
            "/api/learning/review", json={"card_key": "card:def:company:anduril", "rating": 3}
        )
        assert graded.status_code == 200, graded.text

    rows = api_db.execute("SELECT user_id FROM learning_cards").fetchall()
    assert [r["user_id"] for r in rows] == ["user:local-operator"]
    row = api_db.execute(
        "SELECT role, email FROM users WHERE id = 'user:local-operator'"
    ).fetchone()
    assert row is not None and row["role"] == "reader", (
        "the local row holds progress; it must not hold authority"
    )


async def test_a_curated_relation_derives_a_card(learner_client, api_db) -> None:
    """The namespace the seeded fixtures never exercise -- which is how a
    live-corpus-only crash in relation derivation once got past the whole
    offline suite (`predicate_names` is a property, and only a real curated
    assertion walks that line)."""
    entity(api_db, "company:anduril", "Anduril")
    entity(api_db, "company:palantir", "Palantir")
    api_db.execute(
        "INSERT INTO assertions (id, run_id, article_id, cluster_id, kind, predicate,"
        "                        subject_id_raw, object_id_raw, evidence_quote,"
        "                        confidence, method, created_at) "
        "VALUES ('a-rel-1', 'curation:test', 'doc:1', 'cluster:1', 'relation',"
        "        'PARTNERS_WITH', 'company:anduril', 'company:palantir',"
        "        'Anduril and Palantir announced a partnership.', 1.0, 'manual', ?)",
        (NOW,),
    )
    api_db.commit()

    queue = (await learner_client.get("/api/learning/queue")).json()
    rel = next((c for c in queue["new"] if c["card_key"] == "card:rel:a-rel-1"), None)
    assert rel is not None, [c["card_key"] for c in queue["new"]]
    assert rel["format"] in ("choice", "flashcard")
    if rel["format"] == "choice":
        assert "PARTNERS_WITH" in rel["choices"]
    assert rel["answer"] == "PARTNERS_WITH" or rel["format"] == "flashcard"
    assert rel["grounding"]["quote"] == "Anduril and Palantir announced a partnership."

    graded = await learner_client.post(
        "/api/learning/review", json={"card_key": "card:rel:a-rel-1", "rating": 3}
    )
    assert graded.status_code == 200, graded.text


async def test_a_choice_card_never_contains_its_answer(learner_client, api_db) -> None:
    """The leak the operator hit live: "Shield AI is a defense contractor
    that…" as the prompt, "Shield AI" as the answer. Masking happens at
    derive time, so it holds for every showing and repairs existing rows
    structurally."""
    for index in range(3):
        _defined(api_db, f"company:d{index}", f"Distractor {index}")
    _defined(api_db, "company:shield", "Shield AI")
    api_db.commit()

    saw_choice = False
    for reps in range(8):
        card = learning_cards.derive(api_db, card_key="card:def:company:shield", reps=reps)
        assert card is not None
        if card.format != "choice":
            continue
        saw_choice = True
        assert "Shield AI" not in (card.front or ""), card.front
        assert "____" in (card.front or "")
        assert card.answer == "Shield AI"
    assert saw_choice, "no rotation reached the choice format in 8 showings"


async def test_a_bank_question_prompt_is_blanked_of_its_answer(learner_client, api_db) -> None:
    """The generator is TOLD not to restate the answer; the derive-time
    blank is what makes that a property rather than a hope -- and it is
    what repairs rows approved before the rule existed."""
    entity(api_db, "company:shield", "Shield AI")
    api_db.execute(
        "INSERT INTO llm_questions (id, run_id, kind, entity_id, prompt, choices, answer,"
        "                           grounding_article_id, grounding_quote, status, created_at) "
        "VALUES ('question:t1', 'run:test', 'choice', 'company:shield',"
        "        'Shield AI is a defense contractor hired by CENTCOM. Which company is this?',"
        '        \'["Boeing", "Shield AI", "CACI International"]\', \'Shield AI\','
        "        'doc:1', 'Shield AI was hired by CENTCOM.', 'active', ?)",
        (NOW,),
    )
    api_db.commit()

    card = learning_cards.derive(api_db, card_key="card:q:question:t1")
    assert card is not None and card.format == "choice"
    assert "Shield AI" not in card.front
    assert "____" in card.front
    assert card.answer == "Shield AI"
    assert "Shield AI" in (card.choices or [])


async def test_a_quotation_is_not_a_definition_card(learner_client, api_db) -> None:
    """A "definition" that is a verbatim quotation describes nothing -- it is
    somebody speaking, and served as a card it becomes who-said-this trivia
    under a what-does-this-describe cue (operator hit one live: the Golden
    Dome funding quote with its director's name blanked). Not deck material
    in any format."""
    _defined(
        api_db,
        "company:quoted",
        "Quoted Corp",
        definition='"We will keep building," a spokesperson for Quoted Corp said Tuesday.',
    )
    _defined(api_db, "company:plain", "Plain Corp")
    api_db.commit()

    assert learning_cards.derive(api_db, card_key="card:def:company:quoted") is None
    plain = learning_cards.derive(api_db, card_key="card:def:company:plain")
    assert plain is not None, "an ordinary definition must stay deck material"

    queue = (await learner_client.get("/api/learning/queue")).json()
    keys = [c["card_key"] for c in queue["new"]]
    assert "card:def:company:quoted" not in keys


async def test_a_gloss_choice_answer_matches_a_served_option(learner_client, api_db) -> None:
    """Gloss options are blanked of the asked surface, and the answer field
    must carry the SAME blanked text -- the client matches the selected
    option verbatim, so a mismatch would make every gloss choice ungradable."""
    queue = (await learner_client.get("/api/learning/queue")).json()
    key = next(
        (c["card_key"] for c in queue["new"] if c["card_key"].startswith("card:gloss:")), None
    )
    assert key is not None, "the seeded corpus should offer a gloss card"
    for reps in range(8):
        card = learning_cards.derive(api_db, card_key=key, reps=reps)
        assert card is not None
        if card.format != "choice":
            continue
        assert card.answer in (card.choices or []), (card.answer, card.choices)
        for choice in card.choices or []:
            assert card.front.lower() not in choice.lower(), (card.front, choice)


async def test_keep_studying_serves_past_the_daily_pace(learner_client, api_db) -> None:
    """The pace is a default, never a wall: once today's new-card budget is
    spent, `?more=true` hands over another full batch, and the deck count
    says how much further a willing learner can go."""
    for index in range(4):
        _defined(api_db, f"company:c{index}", f"Corp {index}")
    api_db.commit()
    await learner_client.patch("/api/learning/settings", json={"new_per_day": 1})

    first = (await learner_client.get("/api/learning/queue")).json()
    assert len(first["new"]) == 1
    # Relative, not absolute: the seeded fixture corpus contributes its own
    # gloss candidates on top of the four entities above.
    deck_before = first["counts"]["deck_remaining"]
    assert deck_before >= 4

    graded = await learner_client.post(
        "/api/learning/review", json={"card_key": first["new"][0]["card_key"], "rating": 3}
    )
    assert graded.status_code == 200

    # The budget is spent; the plain queue offers nothing new...
    assert (await learner_client.get("/api/learning/queue")).json()["new"] == []
    # ...and the learner who asks gets a full further batch anyway.
    extra = (await learner_client.get("/api/learning/queue?more=true")).json()
    assert len(extra["new"]) == 1
    assert extra["new"][0]["card_key"] != first["new"][0]["card_key"]
    assert extra["counts"]["deck_remaining"] == deck_before - 1
