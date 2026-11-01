"""Response models validated against what the handlers actually return.

`response_model` fails in two directions and neither one is loud:

  * a field the model omits is **filtered out** of the response — no error, it
    simply stops arriving, and the symptom appears in whatever component read it;
  * a field typed wrongly raises `ResponseValidationError`, which is a **500 at
    serialization time** — after the handler has done all its work.

The second is what these exist for. `OntologyResponse.version` was typed `str`
against an `Ontology.version` that is `int`, and it served a 500 from the
endpoint the graph legend, the filter chips and the entity colour mapping are
all built from. The whole suite passed: nothing here calls these endpoints with
real data, so nothing serialized a real payload.

So each test below builds the payload the way the router builds it and puts it
through the model. Offline and free — the Neo4j-backed shapes (`/graph/query`,
`/graph/overview`, `/graph/path`, `/graph/search`) need a live driver and are
not covered here; those were verified by diffing real responses against the live
corpus before and after the models landed.
"""

from __future__ import annotations

import asyncio

from venona.api.routers.graph import _coverage
from venona.api.routers.meta import _source_health
from venona.api.routers.meta import ontology as ontology_route
from venona.api.schemas.graph import EdgeProvenanceResponse, GraphCoverage
from venona.api.schemas.meta import OntologyResponse, RescoreResponse, SourcesHealth
from venona.db import core as db


def _ontology_payload() -> dict:
    """What the HANDLER returns — called, not re-implemented.

    A hand-copied duplicate of the handler's body would make the
    drops-no-field test compare the model against its own copy: add a key to
    `routers/meta.py::ontology` without adding it to `OntologyResponse` and the
    field is filtered off the wire while this still passes, because the fixture
    it diffs against never had the key either. A test that cannot fail for the
    reason it names is worse than no test.

    `ontology()` is a plain `async def` with no `Request` dependency, so calling
    it directly costs nothing.
    """
    return asyncio.run(ontology_route())


def test_the_real_ontology_serializes() -> None:
    """The regression. `version` is an int, and typing it `str` was a 500."""
    payload = _ontology_payload()
    model = OntologyResponse.model_validate(payload)

    assert isinstance(payload["version"], int)
    assert model.entity_types, "the ontology defines entity types"
    assert model.predicates, "the ontology defines predicates"


def test_the_ontology_model_drops_no_field() -> None:
    """The other failure direction: a filtered field leaves no trace at all.

    Compared key-for-key rather than by count, so a rename reads as what it is.
    """
    payload = _ontology_payload()
    served = OntologyResponse.model_validate(payload).model_dump()

    assert set(served) == set(payload)
    assert set(served["entity_types"][0]) == set(payload["entity_types"][0])
    assert set(served["predicates"][0]) == set(payload["predicates"][0])


def test_edge_provenance_serializes(conn: db.Conn) -> None:
    """`model` is null for the deterministic methods and the document columns
    are null for a pruned article, so this is the shape most likely to have a
    required field that should not be."""
    from venona.services.graph_service import edge_provenance

    payload = edge_provenance(conn, "govt:peo-c3n|PART_OF|unit:u-s-army")
    assert payload is not None

    served = EdgeProvenanceResponse.model_validate(payload).model_dump()
    assert set(served) == set(payload)
    assert set(served["assertions"][0]) == set(payload["assertions"][0])
    assert served["assertions"][0]["quote"], "the sentence is the point of the endpoint"


def test_coverage_serializes(conn: db.Conn) -> None:
    assert isinstance(_coverage(conn), GraphCoverage)


def test_source_health_serializes(conn: db.Conn) -> None:
    assert isinstance(_source_health(conn), SourcesHealth)


def test_rescore_serializes(conn: db.Conn) -> None:
    """`as_dict()` plus the `model_calls: 0` the router adds — a report whose
    keys come from a dataclass in another module, which is exactly the kind that
    drifts out from under a hand-written model."""
    from venona.services.rescore import rescore_all

    payload = {**rescore_all(conn).as_dict(), "model_calls": 0}
    served = RescoreResponse.model_validate(payload).model_dump()
    assert set(served) == set(payload)
