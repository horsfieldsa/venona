"""The OpenAPI document says which response keys are actually present.

`required` in JSON Schema means "this key is in the payload". Pydantic marks a
field non-required whenever it has a DEFAULT, which is a fact about constructing
the model rather than about the response — so a field on every single response
was advertised as optional, `schema.d.ts` typed it `| undefined`, and the client
grew a coalesce per read site for a case the server cannot produce.

The correction has to be selective, and these tests pin both directions: the
schemas that may say "every key is present", and the ones that must not.
"""

from __future__ import annotations

import pytest
from venona.api import openapi
from venona.main import app


@pytest.fixture(scope="module")
def document() -> dict:
    return app.openapi()


def _required(document: dict, name: str) -> set[str]:
    return set(document["components"]["schemas"][name].get("required", []))


def _properties(document: dict, name: str) -> set[str]:
    return set(document["components"]["schemas"][name].get("properties", {}))


def test_every_documented_operation_matches_a_route(document: dict) -> None:
    """⭐ The guard on the guard. An unmatched path reads as a route that
    excludes nothing, so a walk that quietly stopped finding routes would mark
    fields present on exactly the responses that drop them — the permissive
    direction, and invisible.

    Two ways it has already broken: `app.routes` nests included routers rather
    than flattening them, and a nested route's path carries no prefix and keeps
    its starlette converter (`{conflict_id:path}`).
    """
    routes = openapi._route_index(app)  # noqa: SLF001 - the thing under test
    documented = {
        (path, method.lower())
        for path, operations in document["paths"].items()
        for method, operation in operations.items()
        if isinstance(operation, dict)
    }
    assert documented, "no operations in the document at all"
    assert not documented - set(routes)


def test_a_response_only_model_says_every_key_is_present(document: dict) -> None:
    """`BriefSummary` is returned and never accepted, and every one of its fields
    is written on every response — including the ones with defaults."""
    assert _required(document, "BriefSummary") == _properties(document, "BriefSummary")
    assert "block_count" in _required(document, "BriefSummary")


def test_a_nested_response_model_is_corrected_too(document: dict) -> None:
    """Honesty is inherited: a model reached only through a response body is
    itself only ever a response body."""
    assert _required(document, "BriefList") == _properties(document, "BriefList")


@pytest.mark.negative
@pytest.mark.parametrize("name", ["EntitySearchResults", "RunStarted"])
def test_a_model_from_an_excluding_route_is_left_alone(document: dict, name: str) -> None:
    """`response_model_exclude_unset` drops keys on purpose — in
    `routers/entities.py` it is load-bearing, keeping `status` off the responses
    the service deliberately hides it from. Marking those present would be a new
    lie in the opposite direction."""
    assert _required(document, name) != _properties(document, name)


@pytest.mark.negative
def test_a_request_body_keeps_its_optional_fields(document: dict) -> None:
    """A default on a request field IS optionality — that is what the default
    means. Forcing it required would make the generated client demand values a
    caller never had to send."""
    schemas = document["components"]["schemas"]
    request_only = [
        name
        for name in ("AccessRequestIn", "AnnotateRequest", "AttachEntityPayload")
        if name in schemas
    ]
    assert request_only, "no request models found; this test is checking nothing"
    for name in request_only:
        properties = _properties(document, name)
        defaulted = {
            key for key, schema in schemas[name]["properties"].items() if "default" in schema
        }
        if defaulted:
            assert _required(document, name) != properties, name


def test_the_brief_summary_field_is_named_for_what_it_carries(document: dict) -> None:
    """It counts BLOCKS. The archive column has always been labelled "blocks";
    the field name is what disagreed with it, and a client reading
    `entity_count` had every reason to render it as a number of entities."""
    properties = _properties(document, "BriefSummary")
    assert "block_count" in properties
    assert "entity_count" not in properties
