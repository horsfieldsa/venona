"""A reader reads. Contract C12's authorization half.

`tests/test_auth.py` proves a stranger reaches nothing. `tests/test_mcp_auth.py`
proves an agent token reaches one path. This proves the third thing, which is the
one the tenancy work exists for: an authenticated READER can use the whole reading
product and cannot start a run, record a verdict, edit the profile, rebuild the
graph or purge the corpus.

Style follows both of those: the real `venona.main.app` with its real middleware
stack, because `tests/conftest.py::api_app` builds a bare FastAPI with no
middleware at all and could not exercise a boundary.

THE RATCHET IS `test_every_route_is_classified`. `authz.role_for_request` defaults
to `admin`, so forgetting to classify a new route fails safe at runtime — a reader
cannot reach it. That is the wrong way to FIND OUT, though: the symptom is a reader
reporting a 403 on a page that should work. So the admin set is written down here
and asserted by equality, and adding any route fails this test until somebody
decides which side it is on.
"""

from __future__ import annotations

import httpx
import pytest
from venona.api import authz, security

# Every route that requires `admin`, asserted by equality below.
#
# Written HERE rather than in `api/authz.py` on purpose: the module's runtime rule
# is "admin unless proven otherwise", and duplicating that as a list inside the
# module would be a second source of truth for the same fact. This is a test
# fixture — a snapshot of the decision — and a diff to it is the review.
ADMIN_ROUTES = frozenset(
    {
        # Where the INSTANCE's notices go. Admin for the writes and for the
        # reads alike: the list names every operations channel this box can
        # reach, and a destination is a credential even where the hint is not.
        # A reader's own delivery settings are a different surface entirely
        # -- `/api/preferences/notifications`, in `READER_ROUTES`.
        ("GET", "/api/notifications"),
        ("GET", "/api/notifications/status"),
        ("GET", "/api/notifications/history"),
        ("POST", "/api/notifications"),
        ("PATCH", "/api/notifications/{destination_id}"),
        ("PUT", "/api/notifications/{destination_id}/target"),
        ("POST", "/api/notifications/{destination_id}/clear"),
        ("POST", "/api/notifications/{destination_id}/test"),
        ("DELETE", "/api/notifications/{destination_id}"),
        # WHICH SERVICE DOES THE THINKING, and what it is signed in as.
        #
        # Admin for the read as well as the writes, and the read is the one
        # worth arguing. `/api/health` already tells a reader whether the model
        # is answering, which is everything the reading surface needs. This
        # route additionally carries the ACCOUNT the instance is signed in as
        # and a hint of the stored credential -- an address and a fingerprint
        # that belong to the operator, not to the readership. The same split
        # `AdminToken` makes: the fact of a credential is operational, the
        # credential is not.
        ("GET", "/api/admin/model"),
        ("PUT", "/api/admin/model"),
        ("PUT", "/api/admin/model/credential"),
        ("DELETE", "/api/admin/model/credential"),
        # Spend, and the runs that do it.
        ("POST", "/api/runs"),
        # Starting a corroboration sweep is starting a run: research lane, model
        # spend, and it writes documents and assertions like any other reading.
        ("POST", "/api/runs/second-source"),
        # The quote in front of it. Admin for the same reason the run is: it
        # reads the ledger, which is what this instance has spent.
        ("GET", "/api/runs/second-source/preview"),
        ("POST", "/api/runs/schedule/trigger"),
        ("POST", "/api/runs/hide"),
        ("POST", "/api/runs/{run_id}/cancel"),
        ("POST", "/api/runs/{run_id}/hide"),
        ("POST", "/api/runs/{run_id}/unhide"),
        ("POST", "/api/briefs/rewrite"),
        ("POST", "/api/ingest"),
        ("POST", "/api/rescore"),
        # The question bank (epic O). Generation SPENDS, and verdicts are
        # curation -- both admin for the same reasons as their twins above.
        # The learner-facing learning routes are READER_ROUTES; the split is
        # the privacy design (docs/adr/0157): readers touch only their own
        # progress, admins touch only the shared bank.
        ("GET", "/api/learning/questions"),
        ("POST", "/api/learning/questions/{question_id}/decide"),
        ("GET", "/api/learning/generate/preview"),
        ("POST", "/api/learning/generate"),
        # Run *viewing* is admin too, and that is a decision rather than an
        # oversight: `/api/runs/{run_id}/stream` carries `model_message` events,
        # which are the live Claude session including its prompts. A reader learns
        # whether something is running from `/api/health`, which is all the reading
        # surface needs.
        ("GET", "/api/runs"),
        ("GET", "/api/runs/types"),
        ("GET", "/api/runs/{run_id}"),
        ("GET", "/api/runs/{run_id}/events"),
        ("GET", "/api/runs/{run_id}/metrics"),
        ("GET", "/api/runs/{run_id}/stream"),
        # Curation. Admin-owned by design: the corpus is curated for readers, not
        # by them, which is the tenancy model this whole slice implements.
        ("GET", "/api/curation/status"),
        ("GET", "/api/curation/conflicts"),
        ("GET", "/api/curation/conflicts/{conflict_id}/options"),
        ("GET", "/api/curation/definitions"),
        ("GET", "/api/curation/merges"),
        ("POST", "/api/curation/merges"),
        ("POST", "/api/curation/merges/{merge_id}/unmerge"),
        # Withdrawing a claim is a curation verdict like the rest of this
        # block, and the one here whose damage is silent: an edge that
        # vanishes reads as an edge nothing ever asserted.
        ("POST", "/api/curation/assertions/{assertion_id}/retract"),
        ("POST", "/api/curation/conflicts/{conflict_id}/keep-apart"),
        ("POST", "/api/curation/conflicts/{conflict_id}/correct-type"),
        ("POST", "/api/curation/conflicts/{conflict_id}/relate"),
        ("POST", "/api/curation/conflicts/{conflict_id}/reopen"),
        ("POST", "/api/curation/rebuild"),
        # Suggests where unattached entities belong, and SPENDS to do it. Admin
        # for both halves of that: it starts a run, and its output is a queue
        # only an admin can rule on.
        ("POST", "/api/curation/placements"),
        # The definitions queue, and the two lookups that SPEND.
        ("PATCH", "/api/glossary/{entry_id}"),
        ("POST", "/api/glossary/{surface}/attach-entity"),
        ("POST", "/api/glossary/{surface}/choose"),
        ("POST", "/api/glossary/{surface}/keep-both"),
        ("POST", "/api/glossary/{surface}/lookup"),
        ("POST", "/api/glossary/{surface}/not-an-acronym"),
        ("POST", "/api/glossary/{surface}/reopen"),
        ("POST", "/api/glossary/{surface}/same-thing"),
        ("POST", "/api/glossary/lookup-all"),
        ("GET", "/api/glossary/lookup-all/preview"),
        ("GET", "/api/glossary/audit/corpus"),
        ("POST", "/api/glossary/audit/corroborate"),
        ("POST", "/api/entities/definitions/refresh"),
        ("POST", "/api/entities/definitions/reread"),
        ("GET", "/api/entities/definitions/preview"),
        ("POST", "/api/entities/{entity_id}/define"),
        # Where a name came from: its merges, and the origin of every surface it
        # is matched on. An operator's question, not a reader's.
        ("GET", "/api/entities/{entity_id}/record"),
        # Config. Every read included: `profile/relevance_profile.md` is the
        # operator's own thesis about what matters and who they compete with, and
        # `GET /api/config/{key}` returns it whole.
        ("GET", "/api/config"),
        ("GET", "/api/config/assembled"),
        ("GET", "/api/config/assembled/{key}"),
        ("GET", "/api/config/{key}"),
        ("PUT", "/api/config/{key}"),
        ("GET", "/api/config/{key}/history"),
        ("GET", "/api/config/{key}/history/{name}"),
        ("POST", "/api/config/anchors/apply"),
        ("GET", "/api/config/ontology/rejections"),
        ("GET", "/api/config/drafts"),
        ("GET", "/api/config/{key}/draft"),
        ("POST", "/api/config/{key}/draft/apply"),
        ("DELETE", "/api/config/{key}/draft"),
        ("GET", "/api/setup"),
        ("POST", "/api/setup/descriptor"),
        ("POST", "/api/setup/generate"),
        # The admin guide. Its reader twin is on READER_ROUTES; this one
        # describes spend controls and the purge, which are not a reader's.
        ("GET", "/api/meta/guide/admin"),
        ("GET", "/api/mgmt/status"),
        ("POST", "/api/mgmt/provision-user"),
        ("POST", "/api/mgmt/domain"),
        ("POST", "/api/mgmt/settings"),
        ("POST", "/api/mgmt/setup/start"),
        ("POST", "/api/sources/{source_id}/enable"),
        # Destructive.
        ("POST", "/api/data/purge"),
        # Taking the whole corpus off the machine. Admin for the writes and for
        # the READS alike: the listing names every archive on disk and the
        # download hands over the reading entire, which is more than any reader
        # route serves and more than the hub is ever allowed to ask for (C12).
        ("GET", "/api/data/exports"),
        ("POST", "/api/data/exports"),
        ("GET", "/api/data/exports/{export_id}/download"),
        # Who may reach the instance. Admin-only including the READS: the users
        # list is the membership of a small named group and the audit trail says
        # who changed it, both of which are exactly what an attacker enumerating
        # a private instance wants. The access queue carries coverage notes
        # people wrote expecting one reader.
        ("GET", "/api/admin/overview"),
        ("GET", "/api/admin/users"),
        ("GET", "/api/admin/events"),
        # Every agent credential on the instance. `GET /api/meta/mcp` is the
        # reader-reachable one and is scoped to the caller; this is the unscoped
        # view, which is the operator's question and nobody else's.
        ("GET", "/api/admin/tokens"),
        # Ending anybody's connection, and admin for the reason the list above is:
        # a list that names a leaked credential beside a revoke that refuses it
        # would leave the hole exactly where it was. A reader ends their OWN
        # through `DELETE /api/meta/mcp/tokens/{id}`, which checks ownership.
        ("DELETE", "/api/admin/tokens/{token_id}"),
        ("PATCH", "/api/admin/users/{user_id}/role"),
        ("POST", "/api/admin/users/{user_id}/suspend"),
        ("POST", "/api/admin/users/{user_id}/reinstate"),
        ("GET", "/api/access/requests"),
        ("POST", "/api/access/{request_id}/approve"),
        ("POST", "/api/access/{request_id}/decline"),
    }
)


def _openapi_routes(app) -> set[tuple[str, str]]:
    return {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }


# ── the table itself, no app required ────────────────────────────────


def test_the_two_spellings_of_admin_cannot_drift() -> None:
    """`api/security.py` holds no `venona.*` imports, so `Principal.role` defaults
    to the literal `"admin"` rather than to `authz.ADMIN`. That is deliberate and
    it is exactly the kind of duplication that rots silently."""
    assert security.Principal(email="x", via="bearer").role == authz.ADMIN


def test_a_reader_route_resolves_to_reader() -> None:
    assert authz.role_for_request("GET", "/api/briefs") == authz.READER
    assert authz.role_for_request("GET", "/api/briefs/brief:2026-08-17") == authz.READER
    assert authz.role_for_request("POST", "/api/entities/cards:batch") == authz.READER


def test_an_unclassified_route_resolves_to_admin() -> None:
    """The safety property, and it lives in `role_for_request` rather than in its
    caller. An earlier draft returned None for an unmatched path so the caller
    could produce a 404 -- which would have let every admin route through
    unchecked, since admin routes are precisely the ones absent from the table."""
    assert authz.role_for_request("POST", "/api/data/purge") == authz.ADMIN
    assert authz.role_for_request("GET", "/api/config/relevance_profile") == authz.ADMIN
    assert authz.role_for_request("GET", "/api/some/route/added/next/month") == authz.ADMIN


@pytest.mark.negative
def test_a_reader_template_does_not_swallow_an_admin_path() -> None:
    """⭐ The bug the classification sweep cannot see.

    Both routes below are classified correctly in the table; what would be wrong
    is the MATCH. `^/api/glossary/[^/]+$` must not capture
    `/api/glossary/audit/corpus`, or an admin-only audit endpoint that precedes
    spend becomes the reader-visible glossary read it happens to resemble.
    """
    assert authz.role_for_request("GET", "/api/glossary/MOSA") == authz.READER
    assert authz.role_for_request("GET", "/api/glossary/audit/corpus") == authz.ADMIN
    assert authz.role_for_request("GET", "/api/glossary/lookup-all/preview") == authz.ADMIN


def test_a_verb_is_never_the_classifier() -> None:
    """Four reads use POST because a graph query does not fit in a query string.
    A "GET is reader, POST is admin" rule would break the graph explorer and every
    hover card for every reader, which is the biggest trap in `api/authz.py`."""
    for path in ("/api/graph/query", "/api/graph/overview", "/api/graph/brief"):
        assert authz.role_for_request("POST", path) == authz.READER
    assert authz.role_for_request("POST", "/api/entities/cards:batch") == authz.READER


def test_every_route_is_classified() -> None:
    """⭐ THE RATCHET. Adding a route fails this until somebody decides.

    Runtime already fails safe -- an unclassified route is admin-only -- so this is
    not about safety. It is about finding out at review time rather than from a
    reader who cannot open a page.
    """
    from venona.main import app

    routes = _openapi_routes(app)
    classified = authz.READER_ROUTES | authz.PUBLIC_ROUTES | ADMIN_ROUTES

    unclassified = routes - classified
    assert not unclassified, (
        f"{len(unclassified)} route(s) are not classified: {sorted(unclassified)}.\n"
        f"Add each to `authz.READER_ROUTES` if a reader may use it, or to "
        f"`ADMIN_ROUTES` in this file if not. There is no third answer, and "
        f"leaving it out means readers get a 403 nobody predicted."
    )

    stale = classified - routes - authz.PUBLIC_ROUTES
    assert not stale, (
        f"{len(stale)} classified route(s) no longer exist: {sorted(stale)}. "
        f"A stale entry is a permission granted to nothing, which is how a "
        f"resurrected path inherits a decision nobody revisited."
    )


def test_every_reader_route_is_classified_on_the_entitlement_axis() -> None:
    """⭐ THE SECOND RATCHET, and it does not fail safe on its own.

    The role axis defaults to admin, so forgetting one refuses somebody. This
    axis defaults to paid -- but only for a path no template matches, and
    `/api/briefs/{brief_id}` compiles to `^/api/briefs/[^/]+$`. A future
    `GET /api/briefs/export` would match that unentitled template and be free,
    silently, without anybody having decided it.

    So the ratchet is stricter here than there: every reader route must be
    NAMED, and the default is only a backstop.
    """
    reader = authz.READER_ROUTES
    classified = authz.UNENTITLED_ROUTES | authz.ENTITLED_ROUTES

    unclassified = reader - classified
    assert not unclassified, (
        f"{len(unclassified)} reader route(s) have no entitlement decision: "
        f"{sorted(unclassified)}.\n"
        f"Add each to `authz.ENTITLED_ROUTES` if it needs a subscription, or to "
        f"`authz.UNENTITLED_ROUTES` if it survives a lapse. Leaving one out "
        f"means a wildcard in the other set may quietly decide it."
    )

    stale = classified - reader - authz.PUBLIC_ROUTES
    assert not stale, (
        f"{len(stale)} entitlement entr(y/ies) name no reader route: "
        f"{sorted(stale)}. A stale entry is a decision attached to nothing."
    )


def test_a_brief_id_reads_but_an_unwritten_briefs_route_does_not() -> None:
    """The wildcard is compiled as `brief:[^/]+`, and both halves matter.

    A real brief id must stay free -- that is the archive ADR 0211 promises a
    lapsed reader, gated by row rather than by route. And a segment that is not
    an id must fall through to default-deny, because on THIS axis an
    over-matching wildcard gives something away rather than refusing it: any
    `GET /api/briefs/<literal>` written next month would otherwise be free
    without anybody deciding.

    `brief:{date}:{uuid7}` is the format, from `pipeline/brief.py`. If that ever
    changes, this is what says so.
    """
    real = "/api/briefs/brief:2026-09-01:06a97085-ef38-77d5-8000-b22f1d5ad5a6"
    assert authz.entitlement_required("GET", real) is False
    assert authz.entitlement_required("GET", "/api/briefs/export") is True
    assert authz.entitlement_required("GET", "/api/briefs/latest") is False
    assert authz.entitlement_required("GET", "/api/briefs/for-you") is True


def test_the_entitlement_sets_do_not_overlap() -> None:
    overlap = authz.UNENTITLED_ROUTES & authz.ENTITLED_ROUTES
    assert not overlap, (
        f"classified as both paid and free: {sorted(overlap)}. The ordered table "
        f"would resolve it by specificity, which is not a decision anybody made."
    )


def test_the_reader_set_and_the_admin_set_do_not_overlap() -> None:
    overlap = authz.READER_ROUTES & ADMIN_ROUTES
    assert not overlap, f"classified twice, and the reader entry wins at runtime: {sorted(overlap)}"


# ── behaviour, against the real middleware stack ─────────────────────


@pytest.fixture
def env(monkeypatch, tmp_path, database_url: str):
    """Auth ON, with an admin and a reader in the database.

    `.env` sets `VENONA_AUTH_ENABLED=0` for local development, so without this
    every assertion below would pass for the wrong reason -- the middleware
    returns before it ever looks at a credential.

    The allow-list is deliberately EMPTY of the reader's address: an env-listed
    address with no row is a synthetic admin (the bootstrap path in
    `AuthMiddleware._lookup_user`), which would make the reader an admin and this
    whole file vacuous.
    """
    from venona.db import core as db
    from venona.services import user_service

    monkeypatch.setenv("VENONA_AUTH_ENABLED", "1")
    monkeypatch.setenv("VENONA_AUTH_SESSION_SECRET", "x" * 48)
    monkeypatch.setenv("VENONA_AUTH_API_TOKEN", "r" * 64)
    monkeypatch.setenv("VENONA_AUTH_ALLOWED_EMAILS", "admin@example.com")
    monkeypatch.setenv("VENONA_DATA_DIR", str(tmp_path))
    security.get_auth_config.cache_clear()

    conn = db.connect(database_url)
    try:
        user_service.create(
            conn,
            user_id="user:admin",
            email="admin@example.com",
            role=authz.ADMIN,
            now="2026-08-17T00:00:00Z",
            allow_admin=True,
        )
        user_service.create(
            conn,
            user_id="user:reader",
            email="reader@example.com",
            role=authz.READER,
            now="2026-08-17T00:00:00Z",
        )
    finally:
        conn.close()

    from venona.settings import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("VENONA_DATABASE_URL", database_url)
    yield
    security.get_auth_config.cache_clear()
    get_settings.cache_clear()


class _NoGraph:
    """Answers `health()`, refuses `driver`.

    The same shape as `conftest._NoGraph`, duplicated rather than imported because
    that one is private to the fixture module and this file deliberately builds the
    REAL app instead of using `api_app`.
    """

    class _Health:
        ok = False
        version = None
        edition = None
        error = "no driver in tests"

    async def health(self):
        return self._Health()

    @property
    def driver(self):
        raise AssertionError("this test serves no Neo4j")


class _NoLlm:
    """Deliberately has no `health` attribute -- that is the replay branch in
    `routers/meta.py`, and the one most likely to break."""


def _client(database_url: str, monkeypatch, *, email: str) -> httpx.AsyncClient:
    import importlib

    from venona.api.middleware import AuthMiddleware
    from venona.settings import get_settings

    monkeypatch.setattr(AuthMiddleware, "_database_url", database_url, raising=False)
    import venona.main as main

    importlib.reload(main)
    main.app.state.settings = get_settings()
    # `httpx.ASGITransport` never runs `lifespan`, so the state the lifespan would
    # have set has to be set here. `/api/health` reads `state.graph` and must
    # report a DEGRADED projection rather than fail with it (invariant 1), so a
    # stub that answers is the point rather than a convenience.
    main.app.state.graph = _NoGraph()
    main.app.state.llm = _NoLlm()

    cfg = security.get_auth_config()
    token, _expires = security.mint_session(cfg, email=email, now=1_800_000_000.0)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
        base_url="https://t",
        cookies={security.COOKIE_NAME: token},
    )


#: One per admin group, chosen because each is a different KIND of harm: model
#: spend, a curation verdict, the operator's own profile, the graph projection,
#: and the corpus itself.
NAMED_ADMIN_WRITES = [
    ("POST", "/api/runs"),
    ("POST", "/api/curation/rebuild"),
    ("PUT", "/api/config/relevance_profile"),
    ("POST", "/api/glossary/MOSA/lookup"),
    ("POST", "/api/data/purge"),
    ("GET", "/api/runs"),
    ("GET", "/api/config/relevance_profile"),
    # Two more kinds of harm, both new: changing who may do what, and admitting
    # somebody to the instance.
    #
    # NOTE THE IDS. `test_the_same_routes_are_open_to_an_admin` below actually
    # CALLS every route in this list against a real database, so these name rows
    # that do not exist -- the admin half asserts "not 403", which a 404 or a 409
    # satisfies. An approval with a real id would create a user as a side effect
    # of a permission test.
    ("PATCH", "/api/admin/users/user:nobody/role"),
    ("POST", "/api/access/req:nobody/approve"),
    ("GET", "/api/admin/users"),
    # And reading every credential on the instance. Classification alone is not
    # enough for this one: the whole point of `GET /api/meta/mcp` being scoped to
    # the caller is that one reader must not see another's token metadata, and
    # this route is the unscoped view of exactly that. So it is refused against a
    # real reader through the real middleware, not merely listed as admin.
    ("GET", "/api/admin/tokens"),
    # And ENDING anybody's connection, which is the same argument one step
    # further: a reader ends their own through `/api/meta/mcp/tokens/{id}`, which
    # checks ownership, and must not reach the route that does not. The id names
    # nothing on purpose -- see the note above about calling these for real.
    ("DELETE", "/api/admin/tokens/mcp_nobody"),
]


@pytest.mark.anyio
@pytest.mark.negative
@pytest.mark.parametrize(("method", "path"), NAMED_ADMIN_WRITES)
async def test_a_reader_is_refused_every_admin_route(
    env, database_url, monkeypatch, method, path
) -> None:
    async with _client(database_url, monkeypatch, email="reader@example.com") as client:
        response = await client.request(method, path)
        assert response.status_code == 403, (
            f"{method} {path} returned {response.status_code}, not 403. "
            f"A reader reached an admin route."
        )
        assert response.json()["detail"]["error"] == "forbidden"


@pytest.mark.anyio
async def test_the_same_routes_are_open_to_an_admin(env, database_url, monkeypatch) -> None:
    """The other half. Without it, a rule that refused EVERYONE would pass above."""
    async with _client(database_url, monkeypatch, email="admin@example.com") as client:
        for method, path in NAMED_ADMIN_WRITES:
            response = await client.request(method, path)
            assert response.status_code != 403, (
                f"{method} {path} refused an ADMIN with 403 -- the role check is "
                f"refusing the wrong principal."
            )


@pytest.mark.anyio
async def test_a_reader_can_read_the_product(env, database_url, monkeypatch) -> None:
    """A reader must reach the whole reading surface. This is the failure that
    would make the tenancy work useless rather than unsafe."""
    async with _client(database_url, monkeypatch, email="reader@example.com") as client:
        for path in (
            "/api/health",
            "/api/briefs",
            "/api/search?q=army",
            "/api/meta/ontology",
            "/api/meta/rubric",
            "/api/data/stats",
            "/api/glossary",
            "/api/relevance/settings",
            "/api/relevance/follows",
            "/api/relevance/suggestions",
            "/api/briefs/for-you",
        ):
            response = await client.get(path)
            assert response.status_code == 200, (
                f"a reader was refused {path}: {response.status_code}"
            )

        # Reads over POST, the trap `api/authz.py` warns about twice.
        #
        # Asserted as NOT-403 rather than as 2xx, deliberately. `/api/graph/*`
        # needs Neo4j and this harness serves none, so it answers 500 -- and a 500
        # from an absent driver still proves what this test is about: the request
        # reached the route, so the ROLE check let it through. Demanding 2xx here
        # would be testing Neo4j.
        for path, body in (
            ("/api/graph/overview", {}),
            ("/api/graph/query", {}),
            ("/api/entities/cards:batch", {"ids": []}),
        ):
            response = await client.post(path, json=body)
            assert response.status_code != 403, (
                f"a reader was REFUSED {path}. This is a READ that uses POST for "
                f"body size -- classifying by verb instead of by route breaks the "
                f"graph explorer and every hover card."
            )


@pytest.mark.anyio
async def test_a_403_carries_the_security_headers(env, database_url, monkeypatch) -> None:
    """Short-circuited responses are exactly where middleware ordering bugs hide.
    `add_middleware` inserts at index 0 and the stack is built over `reversed(...)`,
    so the LAST added is the OUTERMOST -- and a role refusal that ships bare is the
    same bug `tests/test_auth.py` already pins for 401."""
    async with _client(database_url, monkeypatch, email="reader@example.com") as client:
        response = await client.post("/api/data/purge")
        assert response.status_code == 403
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"


@pytest.mark.anyio
async def test_suspending_a_user_ends_their_session_now(env, database_url, monkeypatch) -> None:
    """Not in fourteen days. This is the property the env allow-list already had --
    removing an address ended its sessions immediately -- and the one that had to
    survive membership becoming a table."""
    from venona.db import core as db
    from venona.services import user_service

    async with _client(database_url, monkeypatch, email="reader@example.com") as client:
        assert (await client.get("/api/briefs")).status_code == 200

        conn = db.connect(database_url)
        try:
            user_service.set_status(
                conn, user_id="user:reader", status=user_service.SUSPENDED, now=1_800_000_001.0
            )
        finally:
            conn.close()

        assert (await client.get("/api/briefs")).status_code == 401


# ── ending a connection over HTTP ────────────────────────────────────


def _connection_for(database_url: str, *, user_id: str, token_id: str):
    from venona.db import core as db
    from venona.services import token_service

    conn = db.connect(database_url)
    try:
        row, _secret = token_service.mint(
            conn,
            label="an assistant",
            token_id=token_id,
            now="2026-08-17T00:00:00Z",
            user_id=user_id,
        )
        return row
    finally:
        conn.close()


def _is_live(database_url: str, token_id: str) -> bool:
    from venona.db import core as db

    conn = db.connect(database_url)
    try:
        row = conn.execute("SELECT revoked_at FROM mcp_tokens WHERE id = ?", (token_id,)).fetchone()
        return row is not None and row["revoked_at"] is None
    finally:
        conn.close()


@pytest.mark.anyio
async def test_a_reader_can_end_their_own_connection(env, database_url, monkeypatch) -> None:
    """⭐ Why the route exists at all.

    A person who signed an assistant in through the consent screen never sees a
    token id, so `venona mcp token revoke <id>` asks them for a value they were
    never shown -- and on a hosted instance the reader is not the person with a
    terminal. Contract C12 permits this and always did: over HTTP access can be
    TAKEN AWAY and never handed out. Minting stays in a terminal (ADR 0128).
    """
    _connection_for(database_url, user_id="user:reader", token_id="mcp_readers")
    async with _client(database_url, monkeypatch, email="reader@example.com") as client:
        response = await client.delete("/api/meta/mcp/tokens/mcp_readers")

    assert response.status_code == 200, response.text
    assert response.json() == {"ended": True}
    assert not _is_live(database_url, "mcp_readers")


@pytest.mark.anyio
@pytest.mark.negative
async def test_one_reader_cannot_end_another_readers_connection(
    env, database_url, monkeypatch
) -> None:
    """⭐ THE REASON THE ROUTE CHECKS OWNERSHIP.

    Token ids are printed by the CLI and rendered on the Agents page, so they are
    not secret and possession of one is not authority over it. Without this check
    the Disconnect button would be a way to unplug somebody else's assistant by
    typing an id.

    404, not 403: a 403 confirms the id exists, which is the one thing a caller
    guessing ids wants to learn.
    """
    _connection_for(database_url, user_id="user:admin", token_id="mcp_admins")
    async with _client(database_url, monkeypatch, email="reader@example.com") as client:
        response = await client.delete("/api/meta/mcp/tokens/mcp_admins")

    assert response.status_code == 404
    assert _is_live(database_url, "mcp_admins"), "somebody else's connection was ended"


@pytest.mark.anyio
async def test_ending_an_already_ended_connection_is_not_an_error(
    env, database_url, monkeypatch
) -> None:
    """`ended: false` rather than a failure. A page acting on a stale list should
    say "already gone", and a double click is not a problem to report."""
    _connection_for(database_url, user_id="user:reader", token_id="mcp_twice")
    async with _client(database_url, monkeypatch, email="reader@example.com") as client:
        assert (await client.delete("/api/meta/mcp/tokens/mcp_twice")).json() == {"ended": True}
        again = await client.delete("/api/meta/mcp/tokens/mcp_twice")

    assert again.status_code == 200
    assert again.json() == {"ended": False}


@pytest.mark.anyio
async def test_an_admin_can_end_anybodys_connection(env, database_url, monkeypatch) -> None:
    """The other half of the unscoped list. A list that names a leaked credential
    beside a revoke that refuses it leaves the hole exactly where it was."""
    _connection_for(database_url, user_id="user:reader", token_id="mcp_someone")
    async with _client(database_url, monkeypatch, email="admin@example.com") as client:
        response = await client.delete("/api/admin/tokens/mcp_someone")

    assert response.status_code == 200, response.text
    assert response.json() == {"ended": True}
    assert not _is_live(database_url, "mcp_someone")


@pytest.mark.anyio
@pytest.mark.negative
async def test_an_agent_token_cannot_end_a_connection(env, database_url, monkeypatch) -> None:
    """Neither route is in `MCP_PATHS`, so an agent credential reaches neither.

    Worth asserting rather than inferring: a leaked token that could revoke its
    siblings would be a way to lock the operator out of their own instance, and
    the reach check is the only thing standing in the way.
    """
    import importlib

    from venona.api.middleware import AuthMiddleware
    from venona.db import core as db
    from venona.services import token_service
    from venona.settings import get_settings

    conn = db.connect(database_url)
    try:
        _row, secret = token_service.mint(
            conn,
            label="an assistant",
            token_id="mcp_agent",
            now="2026-08-17T00:00:00Z",
            user_id="user:admin",
        )
    finally:
        conn.close()

    monkeypatch.setattr(AuthMiddleware, "_database_url", database_url, raising=False)
    import venona.main as main

    importlib.reload(main)
    main.app.state.settings = get_settings()
    headers = {"authorization": f"Bearer {secret}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
        base_url="https://t",
    ) as client:
        for path in ("/api/meta/mcp/tokens/mcp_agent", "/api/admin/tokens/mcp_agent"):
            response = await client.delete(path, headers=headers)
            assert response.status_code == 403, f"{path} answered {response.status_code}"
            assert response.json()["detail"]["error"] == "restricted_credential"

    assert _is_live(database_url, "mcp_agent")


# ── the entitlement axis, through the real middleware ────────────────
#
# The unit tests for the matcher and the ceiling are in `test_entitlement.py`.
# These are the two that need the whole stack: that a lapsed reader is actually
# refused, and that the refusal does not reach the people it must never reach.


def _set_entitlement(
    database_url: str, email: str, status: str, through: str | None = None
) -> None:
    from venona.db import core as db
    from venona.db.repositories import users as users_repo
    from venona.services import user_service

    conn = db.connect(database_url)
    try:
        user = user_service.lookup(conn, email)
        users_repo.set_entitlement(
            conn, user_id=user.id, status=status, through=through, notice=None, notice_at=None
        )
    finally:
        conn.close()


@pytest.mark.anyio
async def test_a_lapsed_reader_is_refused_the_paid_surface(env, database_url, monkeypatch) -> None:
    monkeypatch.setenv("VENONA_BILLING_ENFORCED", "1")
    _set_entitlement(database_url, "reader@example.com", "lapsed", "2026-01-01")
    async with _client(database_url, monkeypatch, email="reader@example.com") as client:
        response = await client.get("/api/briefs/for-you")
        assert response.status_code == 403
        assert response.json()["detail"]["error"] == "subscription_required"


@pytest.mark.anyio
async def test_the_refusal_does_not_tell_a_stranger_their_subscription_ended(
    env, database_url, monkeypatch
) -> None:
    """Two states, and one sentence cannot serve both. Somebody who never
    subscribed has no archive, and being told theirs "has ended" reads as the
    product being confused about who they are -- at the moment they are deciding
    whether to trust it with a card."""
    monkeypatch.setenv("VENONA_BILLING_ENFORCED", "1")
    _set_entitlement(database_url, "reader@example.com", "none", None)
    async with _client(database_url, monkeypatch, email="reader@example.com") as client:
        message = (await client.get("/api/briefs/for-you")).json()["detail"]["message"]
        assert "ended" not in message.lower(), message
        assert "already had" not in message.lower(), message

    _set_entitlement(database_url, "reader@example.com", "lapsed", "2026-01-01")
    async with _client(database_url, monkeypatch, email="reader@example.com") as client:
        message = (await client.get("/api/briefs/for-you")).json()["detail"]["message"]
        assert "ended" in message.lower(), message


@pytest.mark.anyio
async def test_a_lapsed_reader_keeps_the_archive(env, database_url, monkeypatch) -> None:
    """The other half, and the one that makes the refusal above honest. A
    paywall that also took away what they paid for would be a different and
    much worse product (ADR 0211)."""
    monkeypatch.setenv("VENONA_BILLING_ENFORCED", "1")
    _set_entitlement(database_url, "reader@example.com", "lapsed", "2026-01-01")
    async with _client(database_url, monkeypatch, email="reader@example.com") as client:
        assert (await client.get("/api/briefs")).status_code == 200


@pytest.mark.anyio
async def test_nothing_is_refused_while_billing_is_off(env, database_url, monkeypatch) -> None:
    """The deploy-safety property. The hub and this instance ship from one
    commit through separate build filters, so there is a window where this code
    is live and nothing has been pushed; enforcing then would refuse everybody."""
    monkeypatch.setenv("VENONA_BILLING_ENFORCED", "")
    _set_entitlement(database_url, "reader@example.com", "lapsed", "2026-01-01")
    async with _client(database_url, monkeypatch, email="reader@example.com") as client:
        assert (await client.get("/api/briefs/for-you")).status_code != 403


@pytest.mark.anyio
async def test_the_break_glass_admin_is_never_paywalled(env, database_url, monkeypatch) -> None:
    """`admin@example.com` is in VENONA_AUTH_ALLOWED_EMAILS and has a row, but
    the synthetic path builds a `UserRecord` with no projection at all. The
    credential that exists for when everything else is broken must not be the
    one the paywall refuses."""
    monkeypatch.setenv("VENONA_BILLING_ENFORCED", "1")
    _set_entitlement(database_url, "admin@example.com", "lapsed", "2026-01-01")
    async with _client(database_url, monkeypatch, email="admin@example.com") as client:
        assert (await client.get("/api/briefs/for-you")).status_code != 403
