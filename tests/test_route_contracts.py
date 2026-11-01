"""Every API boundary has a pydantic model, and every model serializes.

CLAUDE.md says "a pydantic model at every API boundary". That was aspirational
for most of the project's life — it was true of 21 of 69 routes — and nothing
checked, so the gap grew silently. These tests make it enforceable:

  * `test_every_route_is_modelled_or_exempt` is the ratchet. A new route with no
    `response_model` fails here unless it is on `EXEMPT` with a reason.
  * the serialization sweep actually CALLS the routes, because a model can be
    wrong in two ways and the type error only shows at serialization time. That
    is not theoretical: `OntologyResponse.version` typed `str` against an `int`
    served a 500 from the endpoint the graph legend is built from, and
    `QueueSuggestion` was typed as a bare string against a `{verdict, why}`
    object. Both passed every service-level test.

The Neo4j-backed graph routes cannot be served from the fixture app and are
listed in `NEEDS_GRAPH`; they were verified by diffing real responses against
the live corpus.
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute
from venona.api.routers import (
    access,
    admin,
    annotate,
    auth,
    briefs,
    config,
    curation,
    data,
    entities,
    glossary,
    graph,
    ingest,
    learning,
    mcp,
    meta,
    mgmt,
    notifications,
    oauth,
    preferences,
    relevance,
    runs,
    setup,
)

# Routes that must NOT carry a `response_model`, and why. Each one would be
# broken by having one, so this list is a design record rather than a backlog.
EXEMPT: dict[tuple[str, str], str] = {
    ("GET", "/runs/{run_id}/stream"): (
        "SSE. A response model would make FastAPI validate and serialise the "
        "generator as one value, ending the stream this endpoint exists to hold "
        "open. The frame shape is `schemas/run.py::RunEvent`, and `/events` "
        "serves the same rows as a modelled list for pollers."
    ),
    ("GET", "/data/exports/{export_id}/download"): (
        "The archive itself. A response model would make FastAPI validate and "
        "serialise a zip as one JSON value; the bytes ARE the response. The row "
        "it is served from IS modelled -- `schemas/data.py::DataExport` -- and "
        "the listing route carries it."
    ),
    ("GET", "/auth/login"): "302 to Google. A redirect has no body to model.",
    ("GET", "/auth/callback"): "302 back to the app, or to /login on failure.",
    ("POST", "/auth/logout"): "204. No content is the point.",
    ("DELETE", "/notifications/{destination_id}"): (
        "204. Removing a destination has nothing to return: the caller already "
        "holds the row it asked to delete, and the list is re-read on the next "
        "render."
    ),
    ("DELETE", "/preferences/notifications/{destination_id}"): (
        "204, as the operator's twin above. A reader removing their own "
        "destination is the same operation on a different owner."
    ),
    ("POST", "/mcp"): (
        "JSON-RPC. One wire shape serves every method and `result` is a union "
        "over `server/discover`, `initialize`, `tools/list`, `tools/call`, "
        "`resources/read` and `prompts/get`. A `response_model` over that union "
        "would FILTER -- a field one method sets and the union does not name "
        "would vanish from the wire silently, which is the hazard CLAUDE.md "
        "warns about. The per-method payloads are modelled in `api/mcp/` and "
        "asserted end to end by `tests/test_mcp_protocol.py`."
    ),
    ("GET", "/oauth/authorize"): (
        "The consent screen. An HTML page in front of a signed-in human, not a "
        "JSON body -- C12's OAuth addendum requires an unregistered redirect URI "
        "to be answered on OUR page rather than by redirecting, which is what "
        "keeps an authorization server from being an open redirector."
    ),
    ("POST", "/oauth/authorize"): (
        "302 carrying the authorization code, or the same page again on refusal."
    ),
    ("POST", "/oauth/token"): (
        "RFC 6749 fixes both bodies and they are different shapes: §5.1 for the "
        "grant and §5.2 for the error, which carries `error` and no token. A "
        "model over the pair would FILTER one of them -- and every redemption "
        "failure is required to say the same sentence, so a dropped field there "
        "is a distinguishable failure, which is what the addendum forbids."
    ),
}

# Cypher-backed, so the fixture app cannot serve them: `_NoGraph.driver` raises
# rather than pretending. All six carry response models; this list exists so
# their absence from the serialization sweep is deliberate and visible.
NEEDS_GRAPH = {
    "/graph/query",
    "/graph/overview",
    "/graph/brief",
    "/graph/path",
    "/graph/search",
    "/edges/{edge_id:path}/provenance",
}

# Every router `main.py` mounts. `conftest`'s `api_app` was extended with
# `annotate` and `ingest` when they shipped and this list was not, which is the
# way a ratchet stops ratcheting without anything going red: the fixture served
# both routers, so their tests passed, while the contract sweep never saw them.
ROUTERS = [
    access,
    admin,
    annotate,
    auth,
    briefs,
    config,
    curation,
    data,
    entities,
    glossary,
    graph,
    ingest,
    learning,
    mcp,
    meta,
    mgmt,
    notifications,
    oauth,
    preferences,
    relevance,
    runs,
    setup,
]


def _routes():
    for module in ROUTERS:
        # `reader_router` where a module has one: `notifications` serves the
        # operator's destinations and the reader's own from two routers in one
        # file, and sweeping only the first left half the endpoints unchecked.
        for attr in ("router", "reader_router"):
            router = getattr(module, attr, None)
            if router is None:
                continue
            for route in router.routes:
                if isinstance(route, APIRoute):
                    for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                        yield method, route.path, route


def _is_modelled(route) -> bool:
    """A `response_model` that actually models something.

    `typing.Any` IS NOT ONE, and this predicate exists because it is truthy.
    Three OAuth routes declared it, so `route.response_model is None` -- the test
    this replaces -- reported them as modelled while `Any` filtered nothing,
    validated nothing and put nothing in the OpenAPI document. That is the exact
    shape of a ratchet that has stopped ratcheting: green, and checking nothing.
    """
    import typing

    return route.response_model is not None and route.response_model is not typing.Any


def test_every_route_is_modelled_or_exempt() -> None:
    """The ratchet. Adding an untyped route is now a failing test.

    An untyped response is why several components hand-declare their own wire
    types, and a hand-declared type is one nothing checks against the server.
    """
    untyped = [
        f"{method} {path}"
        for method, path, route in _routes()
        if not _is_modelled(route) and (method, path) not in EXEMPT
    ]

    assert not untyped, (
        f"{len(untyped)} route(s) have no `response_model`: {untyped}. Add one in "
        f"`api/schemas/`, or add the route to EXEMPT above with the reason it "
        f"cannot have one."
    )


def test_the_graph_routes_are_modelled_and_still_exist() -> None:
    """`NEEDS_GRAPH` has to assert something, or it is a comment shaped like a
    guard.

    These six are excluded from the serialization sweep because the fixture app
    serves no Neo4j — which means nothing else in this file would notice if one
    lost its model or was renamed out from under the list. This checks both:
    every path is live, and every one carries a `response_model`.
    """
    by_path = {path: route for _method, path, route in _routes()}

    missing = sorted(path for path in NEEDS_GRAPH if path not in by_path)
    assert not missing, f"NEEDS_GRAPH names route(s) that no longer exist: {missing}"

    untyped = sorted(path for path in NEEDS_GRAPH if not _is_modelled(by_path[path]))
    assert not untyped, f"Cypher-backed route(s) lost their model unnoticed: {untyped}"


def test_the_exempt_list_has_no_stale_entries() -> None:
    """An exemption for a route that no longer exists, or that has since been
    given a model, is a claim nobody is checking."""
    live = {(method, path) for method, path, _ in _routes()}
    gone = sorted(f"{m} {p}" for m, p in EXEMPT if (m, p) not in live)
    assert not gone, f"EXEMPT names route(s) that no longer exist: {gone}"

    # `_is_modelled`, not `is not None`, and for the same reason the ratchet
    # above uses it: `typing.Any` is truthy, so the old spelling would report an
    # `Any`-annotated route as having "grown a model" and demand its exemption be
    # removed -- turning the one honest way to record "this cannot have a model"
    # into a failing test.
    modelled = [
        f"{method} {path}"
        for method, path, route in _routes()
        if (method, path) in EXEMPT and _is_modelled(route)
    ]
    assert not modelled, f"EXEMPT names route(s) that now have a model: {modelled}"


# ── serialization, against real seeded data ──────────────────────────


READ_ROUTES = [
    "/api/health",
    "/api/health/live",
    # Read against `_NoLlm`, which deliberately has no `health` attribute and
    # no harness -- the same degradation `/api/health` is swept for. This page
    # is the one an administrator opens when nothing works, so a required field
    # the handler cannot fill would be a 500 exactly then.
    "/api/admin/model",
    "/api/meta/ontology",
    "/api/sources/sites",
    "/api/briefs",
    "/api/runs",
    "/api/runs/types",
    "/api/config",
    "/api/config/assembled",
    "/api/config/assembled/analyze",
    "/api/config/settings",
    "/api/config/relevance_profile",
    "/api/config/settings/history",
    "/api/curation/status",
    "/api/curation/conflicts",
    "/api/curation/conflicts?kind=containment&limit=5&offset=5",
    "/api/curation/merges",
    "/api/curation/merges?active_only=false&limit=5&offset=5",
    "/api/access/requests",
    "/api/curation/definitions",
    "/api/curation/definitions?limit=5&offset=5",
    "/api/glossary",
    # The paged/filtered variant serializes through the same model but a
    # different repository path (WHERE + OFFSET); both must survive the sweep.
    "/api/glossary?limit=5&offset=5&q=a&status=active,ambiguous",
    "/api/briefs?limit=5&offset=5",
    "/api/glossary/lookup-all/preview",
    "/api/glossary/audit/corpus",
    # Aggregates four tables including the whole ledger; 404s on a seeded db
    # with no such run, which is why READ_ROUTES asserts < 500 rather than 200.
    "/api/runs/run:none/metrics",
    "/api/search?q=army",
    "/api/search?q=army&include_provisional=true",
    # The entity page's three reads, each in its bare and its windowed form.
    # A seeded database may hold none of these ids, which is why the sweep
    # asserts < 500 rather than 200: what is being checked is that the response
    # MODEL survives serialization, and an empty page exercises it as well as a
    # full one.
    "/api/entities/company:anduril-industries/connections",
    "/api/entities/company:anduril-industries/connections?limit=5&offset=5",
    "/api/entities/company:anduril-industries/sources",
    "/api/entities/company:anduril-industries/sources?limit=5&offset=5",
    "/api/entities/company:anduril-industries/record",
    # No principal under `api_app` + the suite's auth-off env = the local
    # operator binding, so these serialize that user's (empty) state.
    "/api/learning/queue",
    "/api/learning/progress",
    "/api/learning/overview",
    "/api/learning/counts",
    "/api/learning/settings",
    # Personal relevance rides the same local-operator binding.
    "/api/relevance/settings",
    "/api/relevance/follows",
    "/api/relevance/suggestions",
    "/api/meta/rubric",
    "/api/briefs/for-you",
    # Both guides parse a real repo file into C3 runs; neither was exercised
    # over HTTP when the production image shipped without the files at all.
    "/api/meta/guide",
    "/api/meta/guide/admin",
    # The windowed admin and reader lists, bare and paged. Each carries a
    # `total` alongside its page, and a model that omits a field drops it from
    # the wire silently -- so both forms are swept.
    "/api/admin/users",
    "/api/admin/users?limit=5&offset=5",
    "/api/admin/events",
    "/api/admin/events?limit=5&offset=5",
    "/api/admin/tokens",
    "/api/admin/tokens?limit=5&offset=5",
    "/api/admin/overview",
    "/api/access/requests?all=true&limit=5&offset=5",
    "/api/data/stats",
    "/api/data/exports",
    "/api/data/exports?limit=5&offset=5",
    "/api/notifications",
    "/api/notifications/history",
    "/api/notifications/history?limit=5&offset=5",
    "/api/relevance/follows?limit=5",
    "/api/config/ontology/rejections",
    "/api/meta/mcp",
    "/api/mgmt/status",
]


@pytest.mark.parametrize("path", READ_ROUTES)
async def test_a_read_route_serializes(api_client, path: str) -> None:
    """A 500 here is a `response_model` that does not match its handler.

    Asserted as `< 500` rather than `== 200` because some of these legitimately
    404 against a seeded corpus; what must never happen is the handler doing its
    work and then failing to serialise the answer.
    """
    response = await api_client.get(path)

    assert response.status_code < 500, f"{path} -> {response.status_code}: {response.text[:400]}"


async def test_the_search_shape_is_unchanged_without_provisionals(api_client) -> None:
    """`entity_service.search` POPS `status` unless provisionals were asked for,
    and calls that "unchanged shape for every existing caller".

    A response model with `status: str | None = None` would put it back as an
    explicit null — a new key on a response that had none — which is why that
    route sets `response_model_exclude_unset`.
    """
    plain = (await api_client.get("/api/search?q=army")).json()["entities"]
    with_provisional = (await api_client.get("/api/search?q=army&include_provisional=true")).json()[
        "entities"
    ]

    assert plain, "the seed corpus should contain an Army entity"
    assert "status" not in plain[0]
    assert "status" in with_provisional[0]


async def test_the_definitions_queue_suggestion_is_an_object(api_client) -> None:
    """The regression. `suggestion` is `{verdict, why}` and was modelled as a
    bare string, which serialised as a 500 on the queue's own endpoint.

    The `why` is not decoration: this proposes an action to a curator with a
    hundred of these to get through, and a verdict alone cannot be checked
    before it is agreed with.
    """
    items = (await api_client.get("/api/curation/definitions")).json()["items"]
    pending = [item for item in items if item.get("suggestion")]

    assert pending, "the seed corpus should leave some surface undecided"
    assert set(pending[0]["suggestion"]) <= {"verdict", "why", "entry_id"}
    assert pending[0]["suggestion"]["why"]


async def test_hide_and_unhide_agree_about_a_missing_run(api_client) -> None:
    """Both 404. They share `RunHidden`, and a response model that makes two
    endpoints look like a matched pair while only one validates its target is
    worse than no model: `unhide` returned a well-formed 200 for a run that
    never existed."""
    hide = await api_client.post("/api/runs/run:does-not-exist/hide")
    unhide = await api_client.post("/api/runs/run:does-not-exist/unhide")

    assert hide.status_code == 404
    assert unhide.status_code == 404


async def test_enabling_a_source_reads_the_sandboxed_config(api_client, api_settings) -> None:
    """`enable_source` took its sources from `get_settings()` rather than
    `app.state.settings`, so it read the real `config/sources.yaml` straight
    through this fixture — and in production missed the rebind `save_config`
    performs after a settings save.

    The sandbox's `config_dir` is a copy, so an id only present there proves the
    endpoint is reading app state.
    """
    import yaml

    path = api_settings.config_dir / "sources.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["sources"].append(
        {
            "id": "sandbox_only_feed",
            "name": "Sandbox only",
            "kind": "rss",
            "url": "https://example.com/feed.xml",
            "publisher": "Sandbox",
            "enabled": False,
        }
    )
    path.write_text(yaml.safe_dump(doc, sort_keys=False))

    response = await api_client.post("/api/sources/sandbox_only_feed/enable")

    # 404 "no health record" means the source WAS found in sources.yaml; the
    # "not in sources.yaml" 404 is the one that would prove it read the real file.
    assert response.status_code == 404
    assert "health record" in response.json()["detail"]


async def test_health_serializes_without_a_probe(api_client) -> None:
    """The replay branch: `_NoLlm` has no `health`, so the handler returns the
    two-field `{ok, detail}` shape. Modelling the `claude_cli` fields as
    required would 500 `/health` exactly when `VENONA_OFFLINE=1` — on the one
    endpoint that has to answer when things are broken."""
    payload = (await api_client.get("/api/health")).json()

    assert payload["claude"]["ok"] is False
    assert payload["claude"]["detail"]
    assert payload["neo4j"]["ok"] is False, "the fixture serves no graph"
    assert payload["api"] == "ok", "a degraded projection is not a degraded app"


# ── the second ratchet: a list over a growing table takes a window ───

#: GET routes that read a table which grows with use and do NOT take a
#: `limit`/`offset` pair, with the reason each is bounded some other way. As
#: with EXEMPT above, this is a design record: an entry here is a claim that the
#: answer's SIZE cannot grow, not that nobody has got round to paging it.
#:
#: The rule is deliberately narrow. "Any route with a list field" would catch
#: `Brief.blocks`, `OntologyResponse.entity_types` and a hundred more, and an
#: exemption table nobody can read is the shape of a ratchet that has stopped
#: ratcheting.
BOUNDED_ELSEWHERE: dict[str, str] = {
    "/glossary/{surface}": (
        "One surface's readings, capped at SURFACE_ENTRY_LIMIT. A surface "
        "carries a handful and `ambiguous` is counted whole-set, so a window "
        "would page a list that is never longer than a card."
    ),
    "/entities/{entity_id}/record": (
        "Two independent lists (merges and surfaces), each capped at "
        "RECORD_LIMIT with its own total. One items/total envelope cannot "
        "express two lists, and ADR 0154 refuses a generic Page[T]."
    ),
    "/relevance/follows": (
        "One reader's follows, capped at FOLLOW_LIMIT with a total. Also the "
        "response of the PUT and DELETE beside it, which have no window to take."
    ),
    "/meta/mcp": (
        "One person's agent connections, capped at CONNECTIONS_LIMIT. Nobody "
        "holds enough credentials to page."
    ),
    "/meta/guide": "The user guide, parsed from a file in the image. Static per release.",
    "/meta/guide/admin": "As /meta/guide.",
    "/sources/sites": "One row per distinct site in the corpus, which the source list bounds.",
    "/config/{key}/history": (
        "Saved versions of one document, capped at config_service.HISTORY_LIMIT."
    ),
    "/config/ontology/rejections": (
        "Grouped in SQL to one row per shape, so the answer is bounded by the "
        "ontology rather than by how many runs have rejected anything."
    ),
    "/notifications": "Destinations an operator added by hand. There is no volume here.",
    "/preferences/notifications": "As /notifications, scoped to one reader.",
    "/briefs/search": (
        "Rows are dropped after the SQL window, so an offset would skip at the "
        "page edge -- the conflicts-queue problem in ADR 0154. Bounded by `limit`."
    ),
    "/curation/conflicts": "ADR 0154: bounded at limit == cap and reloaded when worked dry.",
    "/briefs/for-you": (
        "One reader's ranked window over recent clusters, capped server-side at "
        "LIST_CAP. It is a selection, not an archive: paging past the cut would "
        "be reading items the ranking already rejected."
    ),
    "/config/drafts": (
        "One draft per editable document, and the editable set is a table in "
        "`config_service`. Bounded by the code, not by use."
    ),
    "/relevance/suggestions": (
        "Names worth offering in a picker, bounded by its own `limit` (cap 100). "
        "Like /briefs/for-you this is a ranked shortlist, and page two of a "
        "suggestion list is a contradiction."
    ),
}

#: Response models whose list fields are not rows from a growing table. Keeps
#: the rule above readable without weakening it.
_NOT_A_LISTING = {
    "Brief",
    "BriefSummary",
    "Card",
    "CardBatchResponse",
    "ConfigList",
    "DataStats",
    "Guide",
    "HealthResponse",
    "LearningQueue",
    "LearningOverview",
    "LearningProgress",
    "Neighborhood",
    "OntologyResponse",
    "RubricMeta",
    "SetupState",
    "MgmtStatus",
    "AdminOverview",
}


def test_a_list_over_a_growing_table_takes_a_window() -> None:
    """The defect this file could not see: a route with a perfectly good model
    that returns every row there has ever been.

    Twenty endpoints predated the `page_window` convention, and each was fine
    until the month it was not -- the failure renders as a slow page rather than
    an error, which is exactly the kind nothing notices. A window is cheap to
    add on day one and awkward to retrofit once a client assumes the whole set.
    """
    import inspect

    def takes_a_window(route) -> bool:
        """Compared by NAME, because every router carries
        `from __future__ import annotations` and hands back the string."""
        try:
            params = inspect.signature(route.endpoint).parameters
        except (TypeError, ValueError):  # pragma: no cover - builtins, C callables
            return False
        return any(
            getattr(p.annotation, "__name__", p.annotation) == "Window" for p in params.values()
        )

    unwindowed = []
    for method, path, route in _routes():
        if method != "GET" or path in BOUNDED_ELSEWHERE or (method, path) in EXEMPT:
            continue
        model = getattr(route, "response_model", None)
        name = getattr(model, "__name__", "")
        if not name or name in _NOT_A_LISTING:
            continue
        fields = getattr(model, "model_fields", {})
        if "items" not in fields or takes_a_window(route):
            continue
        unwindowed.append(f"{method} {path} -> {name}")

    assert not unwindowed, (
        f"{len(unwindowed)} route(s) return an `items` list with no window: "
        f"{unwindowed}. Take `Window = Depends(page_window(default, cap))` and "
        f"return a whole-set `total` (docs/EXTENDING.md), or add the path to "
        f"BOUNDED_ELSEWHERE with the reason its size cannot grow."
    )


def test_the_window_exemptions_have_no_stale_entries() -> None:
    """Same argument as the EXEMPT sweep: an exemption for a route that has gone
    is a claim nobody is checking."""
    live = {path for method, path, _ in _routes() if method == "GET"}
    gone = sorted(path for path in BOUNDED_ELSEWHERE if path not in live)
    assert not gone, f"BOUNDED_ELSEWHERE names route(s) that no longer exist: {gone}"
