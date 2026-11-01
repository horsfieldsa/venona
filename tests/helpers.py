"""Builders and stubs the suite was copying instead of importing.

Not fixtures. `conftest.py` holds things pytest injects; these are plain
callables a test reaches for by name, which is what the seven hand-rolled
`_entity` copies wanted to be. `tests/` has no `__init__.py`, so pytest puts it
on `sys.path` and `from helpers import ...` works from any test module.

What was here before, and why it is worth one file:

  * **`_entity` was written seven times**, two pairs of them byte-identical.
    Five went through `db.upsert`, two wrote raw SQL, and they disagreed about
    whether `status` had a default -- so "add an entity" meant reading whichever
    copy was nearest.
  * **`NOW` appeared in fifteen files with four different values.** None of the
    four meant anything: no test asserts on the instant itself, they just needed
    *a* fixed one. Four arbitrary constants read as three deliberate
    distinctions that do not exist.
  * **The three-class Neo4j driver stub existed three times** -- twice inside
    `test_graph_api.py` alone, once in `test_review_regressions.py` -- differing
    only in what `single()` answered and how the call was recorded.
"""

from __future__ import annotations

from typing import Any

from venona.db import core as db
from venona.ontology import get_ontology

# One fixed instant for the whole suite. The value is arbitrary and nothing
# asserts on it; what matters is that it is the SAME arbitrary value everywhere,
# so a test comparing two rows written by two helpers is not accidentally
# comparing 2026-08-06 with 2026-08-08.
#
# A test that genuinely needs a second instant should derive it from this one
# and say what the gap is for -- `freshness`, `last_seen` ordering -- rather than
# introducing another bare constant.
NOW = "2026-08-08T00:00:00.000Z"


def entity(
    conn: db.Conn,
    entity_id: str,
    name: str,
    status: str = "active",
    *,
    entity_type: str | None = None,
    now: str = NOW,
) -> None:
    """One row in `entities`, upserted.

    `status` defaults to `active` because that is what the projector and every
    graph query filter on; a test that wants `provisional` is making a point and
    says so at the call site.

    `entity_type` is derived from the id prefix, which is not a convenience --
    ids are type-prefixed by contract C4 and the ontology owns the mapping, so
    inferring it is the only way the two cannot disagree. Five of the seven
    copies this replaces hardcoded `"Company"`, which is how
    `test_acronyms.py` ended up with seven `prog:` ids typed `Organization`.
    Pass it explicitly for a deliberate mismatch.

    Binding the map at import time is safe here for the reason CLAUDE.md gives:
    the prohibition is about PREDICATES, and the editor freezes entity types.
    """
    if entity_type is None:
        prefix = entity_id.split(":", 1)[0]
        entity_type = get_ontology().prefix_type.get(prefix, "Company")
    db.upsert(
        conn,
        "entities",
        {
            "id": entity_id,
            "type": entity_type,
            "name": name,
            "canonical_key": name.lower(),
            "status": status,
            "first_seen": now,
            "last_seen": now,
            "created_at": now,
        },
        key=["id"],
    )


# ── the Neo4j driver, faked ──────────────────────────────────────────
#
# `make test` is offline and there is no Neo4j to answer a query, so what these
# verify is what the Cypher SAYS -- an undirected pattern, a missing id bound on
# the second endpoint, a node query driven by the edge set. That is exactly
# where the interesting mistakes live.


class FakeNeo4jResult:
    def __init__(self, rows: list[dict[str, Any]], single: dict[str, Any] | None) -> None:
        self._rows = rows
        self._single = single

    async def data(self) -> list[dict[str, Any]]:
        return self._rows

    async def single(self) -> dict[str, Any] | None:
        # A fixed `single` answers EVERY call -- that is the "the writer reports
        # what it wrote" shape, where each statement returns the same counter
        # dict. Without one, fall back to the head of this call's queued rows.
        if self._single is not None:
            return self._single
        return self._rows[0] if self._rows else None


class FakeNeo4jSession:
    def __init__(self, driver: FakeNeo4jDriver) -> None:
        self._driver = driver

    async def run(self, query: str, **kwargs: Any) -> FakeNeo4jResult:
        self._driver.calls.append((query, kwargs))
        rows = self._driver._rows.pop(0) if self._driver._rows else []
        return FakeNeo4jResult(rows, self._driver._single)

    async def __aenter__(self) -> FakeNeo4jSession:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class FakeNeo4jDriver:
    """Records every Cypher statement and answers from a queue.

    Two answering modes, because the three stubs this replaces used both:

      * `FakeNeo4jDriver(rows_a, rows_b, ...)` -- each `run` pops the next
        payload, so a route issuing an edge query then a node query gets
        different rows for each. `single()` takes the head of that payload.
      * `FakeNeo4jDriver(single={"written": 2})` -- every `run` gets the same
        fixed answer, which is what a writer asserting its own reported counts
        needs.

    Read the recording through `calls`, `queries` or `params`. The stub in
    `test_review_regressions.py` merged the query into the kwargs as
    `{"query": ..., **kwargs}`, which reads fine until a Cypher parameter is
    itself named `query`.
    """

    def __init__(
        self,
        *responses: list[dict[str, Any]],
        single: dict[str, Any] | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._rows = list(responses)
        self._single = single

    @property
    def queries(self) -> list[str]:
        return [query for query, _ in self.calls]

    @property
    def params(self) -> list[dict[str, Any]]:
        return [kwargs for _, kwargs in self.calls]

    def session(self) -> FakeNeo4jSession:
        return FakeNeo4jSession(self)


# ── principals, for a direct call into a router ──────────────────────
#
# Router functions called directly have no middleware behind them, so nothing
# sets `request.state.principal`. Both halves have to work and both are worth
# exercising: `authz.actor_id` must answer None rather than raise for the
# unsigned case, because a CLI or pipeline verdict that failed on ATTRIBUTION
# would trade a working feature for a column.
#
# Lived in `test_curation_api.py` until the glossary verdicts needed the same
# two, which is exactly the copying this module exists to stop.


class Anonymous:
    """A request with no principal at all -- what a direct call is."""


class Signed:
    """A request carrying a principal, mirroring what `AuthMiddleware` sets."""

    class state:  # noqa: N801 - mirrors `request.state`, not a class in our style
        class principal:  # noqa: N801
            user_id = "user:curator"
            email = "curator@example.com"


ANON = Anonymous()
SIGNED = Signed()

CURATOR_ID = Signed.state.principal.user_id
CURATOR_EMAIL = Signed.state.principal.email


def curator(conn: db.Conn) -> str:
    """The `users` row `SIGNED` names, so a LEFT JOIN back to an email resolves."""
    from venona.services import user_service

    # `allow_admin` because this is test SETUP standing in for shell access, not
    # a request: these suites need an admin to exist before they can exercise
    # what an admin may do (ADR 0168).
    user_service.create(
        conn,
        user_id=CURATOR_ID,
        email=CURATOR_EMAIL,
        role="admin",
        now=NOW,
        allow_admin=True,
    )
    return CURATOR_ID


def document(
    conn: db.Conn,
    document_id: str,
    *,
    domain: str = "example.test",
    title: str | None = None,
    now: str = NOW,
) -> None:
    """One row in `documents`, upserted.

    `site_domain` is the parameter that matters and the reason this exists: an
    edge's strength is its number of distinct PUBLISHERS, so a test about
    corroboration is really a test about which domains its documents carry.
    Three modules had already hand-rolled this, each rediscovering which of the
    dozen NOT NULL columns it had to invent a value for.
    """
    db.upsert(
        conn,
        "documents",
        {
            "id": document_id,
            "url": f"https://{domain}/{document_id}",
            "url_key": f"{domain}/{document_id}",
            "title": title or document_id,
            "site_domain": domain,
            "publisher": domain,
            "text_nfc": "",
            "text_chars": 0,
            "published_at": now,
            "date_confidence": "feed",
            "fetched_at": now,
            "content_hash": document_id,
            "cluster_id": f"cluster:{document_id}",
            "created_at": now,
        },
        key=["id"],
    )


def relation(
    conn: db.Conn,
    assertion_id: str,
    subject: str,
    predicate: str,
    obj: str,
    *,
    doc: str,
    quote: str = "asserted here.",
    confidence: float = 0.8,
    now: str = NOW,
) -> None:
    """One `relation` assertion, the raw ids kept as written.

    `subject_id_raw` and `object_id_raw` are never rewritten by a merge
    (invariant 1), so a test about reading across a merge has to write them the
    way the pipeline did and let the read path resolve them.
    """
    db.upsert(
        conn,
        "assertions",
        {
            "id": assertion_id,
            "run_id": "run:test",
            "article_id": doc,
            "cluster_id": f"cluster:{doc}",
            "kind": "relation",
            "predicate": predicate,
            "subject_id_raw": subject,
            "object_id_raw": obj,
            "evidence_quote": quote,
            "confidence": confidence,
            "method": "extraction",
            "created_at": now,
        },
        key=["id"],
    )
