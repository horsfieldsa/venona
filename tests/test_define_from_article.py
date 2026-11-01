"""Reaching the sentence in an article that says what a thing IS.

`assertions.evidence_quote` is the sentence a mention appeared in, which is
usually a contract award and defines nothing. The sentence that describes the
thing is somewhere else in the same document, and until `definition_quote`
existed nothing in the pipeline could read it -- `plat:vectis` rendered as
undefined on a brief while an article we held called it `a large "Group 5"
stealth drone`.

Three seams are what these cover: the widened shapes tier 1 can parse for free,
the gate every model-supplied sentence passes through, and the scope the stage
looks at.
"""

from __future__ import annotations

from venona.db import core as db
from venona.pipeline.define import (
    defining_sentence,
    ground_in_corpus_quote,
    verify_definition_sentence,
)

VECTIS_ARTICLE = (
    "NATIONAL HARBOR, Md. — Lockheed Martin is doubling down on its internal "
    "investments for its Vectis stealth drone, announcing today that the company "
    "will build an additional four prototypes.\n"
    "Lockheed has described Vectis as a large “Group 5” stealth drone, with "
    "flexibility as its major selling point."
)


# ── the shapes tier 1 can read for free ──


def test_a_dash_opens_an_appositive_like_a_comma() -> None:
    """House style, not semantics. Half the trade press sets an appositive with
    an em dash, and this one was rejected on the punctuation alone while the
    hover card told the reader nothing on the web defined Vectis."""
    sentence = (
        "Last year, Skunk Works unveiled Vectis—a concept for a group 5 drone "
        "that is capable of a wide range of missions."
    )
    assert defining_sentence(sentence, "Vectis") == sentence


def test_a_parenthesis_opens_one_too() -> None:
    sentence = "Vectis (a Group 5 stealth drone) will fly by the end of 2027 at the earliest."
    assert defining_sentence(sentence, "Vectis") == sentence


def test_a_describing_verb_carries_as_a() -> None:
    """The shape the corpus actually leans on, and the one worth the most: the
    description is the object of `described ... as`, not an appositive."""
    sentence = (
        "Lockheed has described Vectis as a large “Group 5” stealth drone, "
        "with flexibility as its major selling point."
    )
    assert defining_sentence(sentence, "Vectis") == sentence


def test_as_a_without_a_describing_verb_is_not_a_definition() -> None:
    """`as a <kind noun>` on its own is a construction, not a description, and
    `effort` is in the kind list. Requiring the verb is what separates them."""
    sentence = "The Army awarded Vectis as a sole-source effort covering five aircraft."
    assert defining_sentence(sentence, "Vectis") is None


def test_a_quoted_classification_no_longer_breaks_the_noun_phrase() -> None:
    """A gap that admitted only word characters stopped at the opening quote,
    so the whole description was unreachable over one typographic mark."""
    sentence = "Fury is a “Group 5” autonomous fighter aircraft built for the Air Force."
    assert defining_sentence(sentence, "Fury") == sentence


def test_the_widened_gate_still_refuses_a_subject_of_the_next_clause() -> None:
    """The regression the lookahead exists for, re-checked against the wider
    gap: a comma before a determiner is the commonest punctuation in English."""
    sentence = (
        "The Army has invested in Next Generation Command and Control, the service "
        "has emphasized repeatedly since the award."
    )
    assert defining_sentence(sentence, "Next Generation Command and Control") is None


# ── the gate in front of every model-supplied sentence ──


def test_a_sentence_the_document_does_not_contain_is_refused() -> None:
    """The failure invariant 3 exists for. A paraphrase reads exactly like a
    quotation, so the only defence is checking it against the document."""
    paraphrase = "Vectis is a Group 5 stealth drone that Lockheed Martin is developing."
    assert verify_definition_sentence(paraphrase, "Vectis", VECTIS_ARTICLE) is None


def test_a_sentence_that_never_names_the_entity_is_refused() -> None:
    """Present in the document, and about something else. This is the miss that
    looks most like a hit, because every other check passes."""
    other = (
        "NATIONAL HARBOR, Md. — Lockheed Martin is doubling down on its internal "
        "investments for its Vectis stealth drone, announcing today that the company "
        "will build an additional four prototypes."
    )
    assert verify_definition_sentence(other, "Fury", VECTIS_ARTICLE) is None


def test_a_real_sentence_survives_typography() -> None:
    """A model retyping a sentence normalises curly quotes and dashes the
    extractor left alone. Rejecting on that would throw away good evidence."""
    retyped = 'Lockheed has described Vectis as a large "Group 5" stealth drone, with '
    retyped += "flexibility as its major selling point."
    assert verify_definition_sentence(retyped, "Vectis", VECTIS_ARTICLE) == retyped


def test_a_fragment_is_refused() -> None:
    assert verify_definition_sentence("Vectis.", "Vectis", VECTIS_ARTICLE) is None


# ── which sentence wins ──


def _article(conn: db.Conn, article_id: str, *, evidence: str, definition: str | None) -> None:
    db.insert(
        conn,
        "documents",
        {
            "id": article_id,
            "url": f"https://example.test/{article_id}",
            "url_key": f"example.test/{article_id}",
            "title": "t",
            "text_nfc": VECTIS_ARTICLE,
            "text_chars": len(VECTIS_ARTICLE),
            "content_hash": article_id,
            "cluster_id": "cluster:v",
            "published_at": "2026-09-14T00:00:00Z",
            "fetched_at": "2026-09-14T00:00:00Z",
            "created_at": "2026-09-14T00:00:00Z",
        },
    )
    db.insert(
        conn,
        "entities",
        {
            "id": "plat:vectis",
            "name": "Vectis",
            "type": "Platform",
            "canonical_key": "vectis",
            "first_seen": "2026-09-14T00:00:00Z",
            "last_seen": "2026-09-14T00:00:00Z",
            "created_at": "2026-09-14T00:00:00Z",
        },
    )
    db.insert(
        conn,
        "assertions",
        {
            "id": f"a:{article_id}",
            "run_id": "run:x",
            "article_id": article_id,
            "cluster_id": "cluster:v",
            "kind": "entity",
            "surface": "Vectis",
            "entity_type": "Platform",
            "entity_id_raw": "plat:vectis",
            "evidence_quote": evidence,
            "definition_quote": definition,
            "salience": 1.0,
            "confidence": 0.9,
            "method": "exact_alias",
            "created_at": "2026-09-14T00:00:00Z",
        },
    )


def test_a_marked_sentence_beats_a_thin_appositive(conn: db.Conn) -> None:
    """The shallow half of the defect. A passing mention that happens to parse
    as an appositive must not outrank the sentence the article devotes to the
    subject -- both are `in_article`, so the ladder cannot separate them and
    the order of the two passes is the only thing that can.
    """
    thin = "The contract covers Vectis, the drone, through 2030 under a five-year award."
    real = (
        "Lockheed has described Vectis as a large “Group 5” stealth drone, "
        "with flexibility as its major selling point."
    )
    _article(conn, "doc:v1", evidence=thin, definition=real)

    assert ground_in_corpus_quote(conn, entity_id="plat:vectis", name="Vectis", now="2026-09-14")
    row = conn.execute("SELECT * FROM entity_definitions").fetchone()
    assert row["definition"] == real
    assert row["method"] == "in_article"
    assert row["grounding_quote"] == real


def test_the_regex_still_carries_a_row_nobody_marked(conn: db.Conn) -> None:
    """Rows written before the column existed, which is most of the corpus."""
    sentence = (
        "Last year, Skunk Works unveiled Vectis—a concept for a group 5 drone "
        "that is capable of a wide range of missions."
    )
    _article(conn, "doc:v2", evidence=sentence, definition=None)

    assert ground_in_corpus_quote(conn, entity_id="plat:vectis", name="Vectis", now="2026-09-14")
    row = conn.execute("SELECT * FROM entity_definitions").fetchone()
    assert row["definition"] == sentence


def test_a_read_that_found_nothing_does_not_define(conn: db.Conn) -> None:
    """`''` is the marker for "this article was read and describes nothing",
    and it must stay distinguishable from a definition at every reader."""
    _article(conn, "doc:v3", evidence="Vectis flew again on Tuesday morning.", definition="")

    assert not ground_in_corpus_quote(
        conn, entity_id="plat:vectis", name="Vectis", now="2026-09-14"
    )
    assert conn.execute("SELECT count(*) AS n FROM entity_definitions").fetchone()["n"] == 0


# ── what the stage is allowed to look at ──


def _hoverable(conn: db.Conn, *, cluster: str, entity_id: str, name: str) -> None:
    db.insert(
        conn,
        "documents",
        {
            "id": f"doc:{cluster}",
            "url": f"https://example.test/{cluster}",
            "url_key": f"example.test/{cluster}",
            "title": "t",
            "text_nfc": VECTIS_ARTICLE,
            "text_chars": len(VECTIS_ARTICLE),
            "content_hash": cluster,
            "cluster_id": cluster,
            "published_at": "2026-09-14T00:00:00Z",
            "fetched_at": "2026-09-14T00:00:00Z",
            "created_at": "2026-09-14T00:00:00Z",
        },
    )
    db.insert(
        conn,
        "entities",
        {
            "id": entity_id,
            "name": name,
            "type": "Platform",
            "canonical_key": name.lower(),
            "first_seen": "2026-09-14T00:00:00Z",
            "last_seen": "2026-09-14T00:00:00Z",
            "created_at": "2026-09-14T00:00:00Z",
        },
    )
    db.insert(
        conn,
        "assertions",
        {
            "id": f"a:{cluster}",
            "run_id": "run:day",
            "article_id": f"doc:{cluster}",
            "cluster_id": cluster,
            "kind": "entity",
            "surface": name,
            "entity_type": "Platform",
            "entity_id_raw": entity_id,
            "evidence_quote": f"{name} flew on Tuesday.",
            "salience": 0.9,
            "confidence": 0.9,
            "method": "exact_alias",
            "created_at": "2026-09-14T00:00:00Z",
        },
    )


def test_the_stage_sees_the_personal_brief_too(conn: db.Conn) -> None:
    """The scope defect, and the one that left `plat:vectis` with no row at all.

    A reader opens their own brief, which is built from its own retrieval: on
    2026-09-14 the shared brief and the personal one agreed on 5 clusters out
    of 19 and 12. Scoped to `briefs` alone this stage never offered two thirds
    of what the reader could actually hover.
    """
    from venona.pipeline.define import targets

    db.insert(
        conn,
        "briefs",
        {
            "id": "brief:2026-09-14",
            "date": "2026-09-14",
            "run_id": "run:day",
            "title": "t",
            "generated_at": "2026-09-14T13:00:00Z",
            "created_at": "2026-09-14T13:00:00Z",
        },
    )
    db.insert(
        conn,
        "brief_items",
        {
            "id": "item:1",
            "brief_id": "brief:2026-09-14",
            "section": "s",
            "rank": 1,
            "ord": 1,
            "cluster_id": "cluster:shared",
            "headline": "h",
            "state": "new",
            "created_at": "2026-09-14T13:00:00Z",
        },
    )
    db.insert(
        conn,
        "users",
        {"id": "user:1", "email": "reader@example.test", "created_at": "2026-09-14T00:00:00Z"},
    )
    db.insert(
        conn,
        "personal_briefs",
        {
            "id": "pbrief:2026-09-14",
            "user_id": "user:1",
            "date": "2026-09-14",
            "run_id": "run:personal",
            "inputs_hash": "h",
            "title": "t",
            "generated_at": "2026-09-14T13:45:00Z",
            "created_at": "2026-09-14T13:45:00Z",
        },
    )
    db.insert(
        conn,
        "personal_brief_items",
        {
            "id": "pitem:1",
            "brief_id": "pbrief:2026-09-14",
            "cluster_id": "cluster:personal",
            "headline": "h",
            "state": "new",
            "ord": 1,
            "created_at": "2026-09-14T13:45:00Z",
        },
    )

    _hoverable(conn, cluster="cluster:shared", entity_id="plat:shared", name="Shared")
    _hoverable(conn, cluster="cluster:personal", entity_id="plat:vectis", name="Vectis")

    found = {row["id"] for row in targets(conn, "run:day", min_salience=0.3)}
    assert found == {"plat:shared", "plat:vectis"}


# ── the run the menu starts ──


async def test_the_free_reread_runs_without_touching_the_model(api_client, conn: db.Conn) -> None:
    """`POST /entities/definitions/reread?use_model=false` is what the runs
    menu offers, and the whole point of offering it there is that it cannot
    spend. A menu item that quietly reads the corpus on the model's meter is
    not a button, so the free half has to be reachable on its own.
    """
    import asyncio

    from venona.services import run_service

    response = await api_client.post("/api/entities/definitions/reread?use_model=false")
    assert response.status_code == 202
    started = response.json()
    assert started["lane"] == "light"

    handle = run_service.registry.handle(started["run_id"])
    if handle is not None and handle.task is not None:
        await asyncio.wait_for(handle.task, timeout=20)

    row = conn.execute(
        "SELECT kind, status FROM runs WHERE id = ?", (started["run_id"],)
    ).fetchone()
    assert row["kind"] == "redefine"
    # `succeeded`, not `succeeded_partial`: a pass that reads what is stored
    # and finds nothing new has not been damaged, and only `ctx.warn` says it
    # has. ADR 0183.
    assert row["status"] == "succeeded"
    spend = conn.execute(
        "SELECT count(*) AS n FROM llm_calls WHERE run_id = ?", (started["run_id"],)
    ).fetchone()
    assert spend["n"] == 0


async def test_the_free_pass_reaches_a_name_the_document_loop_would_skip(
    api_client, conn: db.Conn
) -> None:
    """The free pass is scoped to NAMES, not to articles worth re-reading.

    Nested inside the document loop it inherited that loop's eligibility, so a
    name whose only defining sentence sat in an article already re-read
    (`definition_quote` stamped) was skipped by the pass that costs nothing --
    the cheapest tier had the narrowest reach. Here the assertion carries an
    empty `definition_quote`, which is exactly the marker that takes its
    article out of `backfill_documents`.
    """
    sentence = (
        "Lockheed has described Vectis as a large “Group 5” stealth drone, "
        "with flexibility as its major selling point."
    )
    _article(conn, "doc:v9", evidence=sentence, definition="")
    conn.commit()

    response = await api_client.post(
        "/api/entities/definitions/reread?use_model=false&min_salience=0"
    )
    assert response.status_code == 202

    import asyncio

    from venona.services import run_service

    handle = run_service.registry.handle(response.json()["run_id"])
    if handle is not None and handle.task is not None:
        await asyncio.wait_for(handle.task, timeout=20)

    row = conn.execute(
        "SELECT method, definition FROM entity_definitions WHERE entity_id = 'plat:vectis'"
    ).fetchone()
    assert row is not None, "the free pass never reached the name"
    assert row["method"] == "in_article"
    assert row["definition"] == sentence


async def test_a_free_pass_that_explained_something_does_not_say_nothing_to_do(
    api_client, conn: db.Conn
) -> None:
    """`documents_seen` is 0 on a free-only pass because it never reaches the
    document loop, and keying the outcome on it made a run that had just
    explained 25 names report "everything already explained"."""
    _article(
        conn, "doc:v10", evidence="Vectis (a Group 5 stealth drone) flew in 2027.", definition=None
    )
    conn.commit()

    started = (
        await api_client.post("/api/entities/definitions/reread?use_model=false&min_salience=0")
    ).json()

    import asyncio

    from venona.services import run_service

    handle = run_service.registry.handle(started["run_id"])
    if handle is not None and handle.task is not None:
        await asyncio.wait_for(handle.task, timeout=20)

    run = conn.execute("SELECT outcome FROM runs WHERE id = ?", (started["run_id"],)).fetchone()
    assert run["outcome"] in {"delivered", "some"}


async def test_the_free_pass_does_not_hold_the_event_loop(conn: db.Conn) -> None:
    """A UI-triggered run executes inside the API process, so a loop that never
    yields is an outage rather than a slow job.

    The free pass is the only stage that has to yield deliberately: every other
    one awaits a model call every few hundred milliseconds and so yields by
    construction, while this one is pure database work and `db` is synchronous.
    6,193 names held the loop for 51 seconds on 2026-09-15, the scheduler
    logged both its jobs "missed by 0:00:50", and every request in that window
    timed out at the proxy as a 502 while the run itself succeeded.

    Measured as a watchdog's worst gap, which is what a health check
    experiences, rather than by counting yields -- the number of awaits is an
    implementation detail and the stall is the defect.
    """
    import asyncio
    import time

    from venona.pipeline.define import YIELD_EVERY, run_backfill
    from venona.pipeline.types import build_context

    now = "2026-09-15T00:00:00Z"
    db.insert(
        conn,
        "documents",
        {
            "id": "doc:many",
            "url": "https://example.test/many",
            "url_key": "example.test/many",
            "title": "t",
            "text_nfc": VECTIS_ARTICLE,
            "text_chars": len(VECTIS_ARTICLE),
            "content_hash": "many",
            "cluster_id": "cluster:many",
            "published_at": now,
            "fetched_at": now,
            "created_at": now,
        },
    )
    for n in range(200):
        db.insert(
            conn,
            "entities",
            {
                "id": f"plat:bulk{n}",
                "name": f"Bulk{n}",
                "type": "Platform",
                "canonical_key": f"bulk{n}",
                "first_seen": now,
                "last_seen": now,
                "created_at": now,
            },
        )
        db.insert(
            conn,
            "assertions",
            {
                "id": f"a:bulk{n}",
                "run_id": "run:bulk",
                "article_id": "doc:many",
                "cluster_id": "cluster:many",
                "kind": "entity",
                "surface": f"Bulk{n}",
                "entity_type": "Platform",
                "entity_id_raw": f"plat:bulk{n}",
                # A MENTION, not a description. `ground_in_corpus_quote`
                # returns False, so the iteration never reaches
                # `await ctx.counter` -- which is the only await in the loop
                # and is exactly why a corpus of mostly-undefinable names is
                # the shape that stalls.
                "evidence_quote": f"Bulk{n} was mentioned again at the show on Tuesday.",
                "salience": 0.9,
                "confidence": 0.9,
                "method": "exact_alias",
                "created_at": now,
            },
        )
    conn.commit()

    worst = 0.0
    stop = False
    # Seeded OUT HERE. Initialised inside the coroutine it is first set when the
    # watchdog first runs, which under the defect is after the stall is over --
    # so the measurement came back 0.00s on the code that caused the outage.
    last = time.perf_counter()

    async def watchdog() -> None:
        nonlocal worst, last
        while not stop:
            await asyncio.sleep(0)
            gap = time.perf_counter() - last
            worst = max(worst, gap)
            last = time.perf_counter()

    from venona.settings import get_settings

    ctx = build_context(run_id="run:bulk", db=conn, llm=None, settings=get_settings())
    began = time.perf_counter()
    watcher = asyncio.ensure_future(watchdog())
    # One turn so the watchdog is actually running before the work starts.
    await asyncio.sleep(0)
    last = time.perf_counter()
    try:
        report = await run_backfill(ctx, min_salience=0.0, use_model=False)
    finally:
        stop = True
        await watcher

    elapsed = time.perf_counter() - began
    assert report.entities_seen >= 200, report.as_dict()

    # RELATIVE, not a constant in seconds. The absolute figure scales with the
    # machine and the corpus -- 51 seconds over 6,193 names in production,
    # a twentieth of a second over 200 against a local database -- so a
    # threshold in seconds either flakes or passes on the defect, and the first
    # one written here passed on the defect. What is invariant is the SHAPE:
    # with the loop yielding every `YIELD_EVERY` names no single stall can be
    # most of the pass, and without it the single stall IS the pass.
    assert worst < elapsed * 0.5, (
        f"one stall of {worst:.3f}s in a {elapsed:.3f}s pass: the free pass is "
        f"holding the event loop instead of yielding every {YIELD_EVERY} names"
    )
