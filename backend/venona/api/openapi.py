"""An OpenAPI document that tells the truth about what a response contains.

THE DISHONESTY THIS REMOVES. In JSON Schema, `required` means "this key is
present", but pydantic marks a field non-required whenever it has a DEFAULT --
which is a statement about constructing the model, not about the payload. A
response field declared `items: list[str] = []` is on every single response and
is nonetheless advertised as optional. `openapi-typescript` believes the
document, `schema.d.ts` types the field `string[] | undefined`, and the client
grows a `?? []` at every read site to satisfy a compiler about a case the server
cannot produce. Roughly a dozen of those accumulated. Each one is dead code that
looks like defensive code, and each one hides the case where the field really
could be missing.

WHY NOT JUST MARK EVERY PROPERTY REQUIRED. Two kinds of schema in the document
would then be lied to in the opposite direction:

  * **Request bodies.** A field with a default there is genuinely optional --
    that IS the meaning of the default -- and forcing it required would make the
    generated client demand values a caller never had to send.
  * **Routes that exclude.** `response_model_exclude_unset` and
    `response_model_exclude_none` drop keys from the payload on purpose (see
    `routers/entities.py`, where it is load-bearing). On those routes a field
    with a default really can be absent.

So eligibility is computed rather than assumed: a schema qualifies only when
every place it appears is a response of a route that excludes nothing, and it
appears in no request body anywhere. A model used both ways is left alone, since
no single `required` list can be honest for both.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute

_PREFIX = "#/components/schemas/"


def _refs_in(node: Any, into: set[str]) -> None:
    """Every `$ref` under `node`, however deeply nested.

    Walked structurally rather than by key name: a ref can sit under
    `properties`, `items`, `additionalProperties`, or inside any of the
    `anyOf`/`allOf`/`oneOf` combinators, and a union of two models nests two
    levels down on its own.
    """
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith(_PREFIX):
            into.add(ref[len(_PREFIX) :])
        for value in node.values():
            _refs_in(value, into)
    elif isinstance(node, list):
        for value in node:
            _refs_in(value, into)


def _expand(names: set[str], schemas: dict[str, Any]) -> set[str]:
    """`names` plus everything they reach. A nested model inherits its parent's
    honesty: if the parent is a response body, so is the child."""
    seen: set[str] = set()
    queue = list(names)
    while queue:
        name = queue.pop()
        if name in seen or name not in schemas:
            continue
        seen.add(name)
        found: set[str] = set()
        _refs_in(schemas[name], found)
        queue.extend(found - seen)
    return seen


def _api_routes(routes: Any, prefix: str = "") -> list[tuple[str, APIRoute]]:
    """Every `APIRoute` under `routes`, with the path the DOCUMENT uses.

    Two traps, and both fail silently rather than loudly.

    `app.routes` is not a flat list: an included router appears as a single
    `_IncludedRouter` holding its own routes on `original_router`. A
    non-recursive walk finds the three routes declared on the app itself and none
    of the twenty routers' -- an empty answer shaped exactly like a complete one.

    And a nested route's own `path` has NO prefix on it: `/auth/mode`, where the
    document says `/api/auth/mode`. Matching on the bare path finds nothing, so
    every route reads as excluding nothing, which is the permissive direction.
    """
    found: list[tuple[str, APIRoute]] = []
    for route in routes or ():
        if isinstance(route, APIRoute):
            found.append((prefix + route.path, route))
            continue
        included = getattr(route, "original_router", None)
        if included is not None:
            context = getattr(route, "include_context", None)
            found.extend(
                _api_routes(included.routes, prefix + (getattr(context, "prefix", "") or ""))
            )
            continue
        nested = getattr(route, "routes", None)
        if nested:
            found.extend(_api_routes(nested, prefix))
    return found


def _documented(path: str) -> str:
    """A route path as the DOCUMENT spells it.

    Starlette's converters live in the path (`{conflict_id:path}`, used by five
    curation routes because an entity id contains slashes); OpenAPI names the
    parameter alone. The two must be reduced to one spelling or those routes
    match nothing.
    """
    return re.sub(r"\{([^}:]+):[^}]+\}", r"{\1}", path)


def _route_index(app: FastAPI) -> dict[tuple[str, str], APIRoute]:
    return {
        (_documented(path), method.lower()): route
        for path, route in _api_routes(app.routes)
        for method in route.methods
    }


def honest_response_schemas(app: FastAPI, document: dict[str, Any]) -> set[str]:
    """Which component schemas may have every property marked present."""
    schemas = document.get("components", {}).get("schemas", {})
    routes = _route_index(app)

    # AN EMPTY OR PARTIAL INDEX MUST FAIL, NOT PASS. Every unmatched path reads
    # as a route that excludes nothing, so a walk that silently stops finding
    # routes would mark fields present on exactly the responses that drop them.
    documented = {
        (path, method.lower())
        for path, operations in document.get("paths", {}).items()
        for method, operation in operations.items()
        if isinstance(operation, dict)
    }
    missing = documented - set(routes)
    if missing:
        raise RuntimeError(
            f"{len(missing)} documented operation(s) matched no route, so their "
            f"exclusion flags could not be read: {sorted(missing)[:5]}"
        )

    honest: set[str] = set()
    excluding: set[str] = set()
    requests: set[str] = set()

    for path, operations in document.get("paths", {}).items():
        for method, operation in operations.items():
            if not isinstance(operation, dict):
                continue
            _refs_in(operation.get("requestBody", {}), requests)

            found: set[str] = set()
            _refs_in(operation.get("responses", {}), found)
            route = routes.get((path, method.lower()))
            drops = route is not None and bool(
                route.response_model_exclude_unset or route.response_model_exclude_none
            )
            (excluding if drops else honest).update(found)

    return _expand(honest, schemas) - _expand(excluding, schemas) - _expand(requests, schemas)


def build(app: FastAPI) -> dict[str, Any]:
    """The document FastAPI would build, with the response schemas corrected.

    Cached on the app the way FastAPI caches its own, so `make types`, the drift
    gate and `/api/openapi.json` all see one document.
    """
    if app.openapi_schema:
        return app.openapi_schema

    document = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version=app.openapi_version,
        description=app.description,
        routes=app.routes,
    )
    schemas = document.get("components", {}).get("schemas", {})
    for name in honest_response_schemas(app, document):
        schema = schemas.get(name, {})
        properties = schema.get("properties")
        if properties:
            # Every property, not the defaulted ones only: the already-required
            # ones are a subset, and rebuilding the whole list keeps this
            # independent of how pydantic ordered them.
            schema["required"] = sorted(properties)

    app.openapi_schema = document
    return document
