"""Health and metadata. `/health` is the first thing to check when anything
looks wrong -- it distinguishes "Neo4j is down" from "you're logged out" from
"the pipeline is stuck"."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request

from venona import scheduler
from venona.api.deps import get_db
from venona.api.schemas.meta import (
    AnnotatableSite,
    AnnotatableSites,
    Disconnected,
    EnableSourceResponse,
    Guide,
    HealthResponse,
    LivenessResponse,
    McpInfo,
    OntologyResponse,
    RescoreResponse,
    SourcesHealth,
)
from venona.api.schemas.relevance import RubricMeta
from venona.db import core as db
from venona.db.repositories import curation as repo
from venona.db.repositories import pipeline_scheduler as scheduler_repo
from venona.db.repositories import quota as quota_repo
from venona.services import freshness, guide_service, run_service, run_types


def _run_brief(handle: run_service.RunHandle) -> dict[str, Any]:
    """Just enough for the UI to label a run in flight without a second call.

    `subject` because three simultaneous rows all reading "Definition"
    say less than none -- the whole question a reader has is *which* term.
    The fallback chain is for stored rows: an older one carries `surface` or
    `entity_id` and no `target_kind`.
    """
    params = handle.params or {}
    subject = params.get("name") or params.get("surface") or params.get("entity_id")
    return {
        "run_id": handle.run_id,
        "kind": handle.kind,
        "lane": handle.lane,
        "label": run_types.type_for(handle.kind).label,
        "subject": str(subject) if subject else None,
        "target_kind": params.get("target_kind"),
        # Asked to stop, and not stopped yet. Reported per run rather than only
        # for the research lane, because a person can cancel either and the row
        # in flight is where they are looking.
        "cancelling": handle.cancelled,
    }


def _model_reason(probe: Any, llm: Any) -> str | None:
    """Why the model surface is not answering, when it is not.

    Three states a reader must not be shown as one. `waiting` is a provider
    incident the research lane sits out; `not_connected` is a deliberate act by
    an administrator and no amount of waiting fixes it; `down` is everything
    else. Telling a reader Venona keeps checking, when nothing will change
    until a person acts, is the sentence this exists to stop.
    """
    breaker = getattr(llm, "breaker", None)
    state = breaker.state() if breaker is not None else None
    if state and state.get("open"):
        return "waiting" if state.get("transient") else "down"
    if probe.ok:
        return None
    return "not_connected" if not probe.logged_in else "down"


router = APIRouter(tags=["meta"])


@router.get("/health/live", response_model=LivenessResponse)
async def live() -> dict[str, str]:
    """Liveness only, and the only unauthenticated endpoint that touches the app.

    Deliberately has NO `Depends(get_db)`. A database dependency here would turn
    "the database is briefly unreachable" into "container unhealthy", and the
    orchestrator's
    answer to unhealthy is to restart -- which kills whatever run is in flight.
    A liveness probe that can take the process down is worse than none.

    It also reveals nothing. `/health` carries the operator's subscription type,
    auth method, CLI version and Neo4j edition, and stays behind auth.
    """
    return {"status": "ok"}


@router.get(
    "/health",
    response_model=HealthResponse,
    # The replay backend returns `{ok, detail}` and `claude_cli` returns the
    # probe. Without this each branch carries the other's fields as nulls --
    # including `detail: null` on every healthy response.
    response_model_exclude_unset=True,
)
async def health(request: Request, db: db.Conn = Depends(get_db)) -> dict[str, Any]:
    settings = request.app.state.settings
    graph = request.app.state.graph
    llm = request.app.state.llm

    graph_health = await graph.health()

    harness = getattr(llm, "harness", None)
    cli: dict[str, Any] = {"ok": False, "detail": "replaying recorded data"}
    if harness is not None:
        cli["service"] = harness.key
        cli["service_label"] = harness.label
        cli["billing_mode"] = settings.llm.billing_mode
    if hasattr(llm, "health"):
        probe = await llm.health()
        cli = {
            "ok": probe.ok,
            "service": harness.key if harness else None,
            "service_label": harness.label if harness else None,
            "billing_mode": settings.llm.billing_mode,
            "reason": _model_reason(probe, llm),
            "version": probe.version,
            "subscription": probe.subscription,
            "pinned": settings.llm.pinned_version_for(harness.key)
            if harness
            else settings.llm.pinned_version,
            "version_matches_pin": probe.version_matches_pin,
            "error": probe.error,
            # `ok` means a credential is present -- `HarnessHealth` says why --
            # and this means calls are actually landing.
            "circuit": llm.breaker.state() if hasattr(llm, "breaker") else None,
            # Asked-and-got, asked-and-refused, or never asked. The middle one
            # is confining's one silent failure: the call runs either way.
            "confined": probe.confined,
        }
        # `account` is deliberately ABSENT. `GET /api/health` is in
        # `authz.READER_ROUTES`, so the address the instance is signed in as
        # would reach every reader; the admin route serves it instead.

    from venona.services.setup_service import is_configured

    _active_run = run_service.registry.active_run_id(run_types.RESEARCH)
    # A run that has been asked to stop is still ACTIVE until it notices, so
    # both facts are reported rather than one standing in for the other.
    _handle = run_service.registry.handle(_active_run) if _active_run else None
    _cancelling = bool(_handle and _handle.cancelled)

    return {
        "api": "ok",
        "offline": settings.offline,
        "config": {
            "dir": str(settings.config_dir),
            "version": settings.settings_version,
        },
        # The first-boot gate's signal: a freshly provisioned instance carries
        # the placeholder descriptor and the SPA redirects admins to /setup.
        "setup_required": not is_configured(settings),
        "neo4j": {
            "ok": graph_health.ok,
            "version": graph_health.version,
            "edition": graph_health.edition,
            "error": graph_health.error,
        },
        # BOTH KEYS, for one release. A tab left open across a deploy keeps
        # polling this route every thirty seconds on the bundle it loaded, and
        # the bundle that read the block unchained threw inside a computed
        # during render until somebody reloaded (`useServiceHealth.ts`).
        # `claude` goes in the release after this one.
        "claude": cli,
        "model": cli,
        # Beside the CLI because the CLI is what reports it. No extra call and
        # no extra spend: the reading rides the model calls the instance was
        # making anyway, and lands in `quota_samples` as they happen.
        "quota": _quota(
            db,
            harness=harness.key if harness else None,
            reported=harness.reports_usage_windows if harness else True,
        ),
        # The RESEARCH-lane run. `useRunStream` follows this, and the pipeline
        # is the only run worth watching stage by stage; pointing it at a
        # lookup would yank the Runs page off the run someone was reading.
        # Light runs are reported below instead. Read from the registry, which
        # is the only thing that knows a lane.
        "active_run": _active_run,
        "active_run_cancelling": _cancelling,
        # Contract C11. Every run in flight, so the UI can show light runs
        # resolving and tell "queued behind a full lane" from "running".
        "runs": {
            "active": [_run_brief(h) for h in run_service.registry.active()],
            "queued": [_run_brief(h) for h in run_service.registry.queued()],
        },
        # An auto-disabled source is silent by design -- it just stops appearing
        # in results. Without surfacing it here, "the brief got thin" has no
        # visible cause anywhere in the product.
        "sources": _source_health(db),
        # Always emitted: `response_model` filters, so a branch that skipped
        # this key would silently drop the block from the wire.
        "schedule": {
            **scheduler.schedule_status(request.app),
            "last_daily_at": _last_daily_at(db),
        },
        # Rides the poll that already exists, and is the whole transport for
        # app-wide freshness: a store refetches when its own stamp moves, which
        # lets a view that loaded once notice a nightly run, a CLI command or a
        # second tab. `services/freshness.py` carries the cost and why it stays
        # a hint. `config` is file-backed, one dict on the wire either way.
        "stamps": {**freshness.stamps(db), "config": freshness.config_stamp(settings)},
    }


def _quota(conn: db.Conn, *, harness: str | None = None, reported: bool = True) -> dict[str, Any]:
    """The latest reading per window, for the service that reported them.

    Scoped by service for the reason `quota_repo.latest_per_window` documents:
    a window belongs to whoever measured it.

    `available` is False on an instance that has never observed one, which is
    both a fresh install and a credential without the scope to see them. The
    strip then writes a sentence rather than drawing a bar at zero, which reads
    as an untouched subscription -- the opposite of unknown.
    """
    rows = quota_repo.latest_per_window(conn, harness=harness)
    if not rows:
        # `reported` separates "not asked yet" from "never will be". The
        # strip promises a first run will fill this in, which is true of one
        # service and a promise nothing can keep for another.
        return {"available": False, "windows": [], "status": None, "reported": reported}
    return {
        "available": True,
        # Newest observation across the set. Every window in one reading shares
        # a status, so any row's is the reading's.
        "status": max(rows, key=lambda r: r["observed_at"])["status"],
        "windows": [
            {
                "name": row["window_name"],
                "used": row["utilization"],
                "resets_at": row["resets_at"],
                "representative": row["representative"],
                "observed_at": row["observed_at"],
            }
            for row in rows
        ],
    }


def _last_daily_at(conn: db.Conn) -> str | None:
    row = scheduler_repo.select_runs(conn).fetchone()
    return row["started_at"] if row else None


def _source_health(conn: db.Conn) -> SourcesHealth:
    rows = repo.source_health(conn)
    return SourcesHealth(
        total=len(rows),
        auto_disabled=[
            {
                "id": r["source_id"],
                "strikes": r["consecutive_failures"],
                "last_success_at": r["last_success_at"],
                "last_error": r["last_error"],
            }
            for r in rows
            if r["auto_disabled"]
        ],
        striking=[
            {"id": r["source_id"], "strikes": r["consecutive_failures"]}
            for r in rows
            if r["consecutive_failures"] and not r["auto_disabled"]
        ],
    )


@router.post("/sources/{source_id}/enable", response_model=EnableSourceResponse)
def enable_source(
    source_id: str, request: Request, conn: db.Conn = Depends(get_db)
) -> dict[str, Any]:
    """Put an auto-disabled source back in the rotation.

    This endpoint exists so the Settings banner does not have to end with "run
    `venona sources --enable <id>`" -- a UI telling its reader to leave the app
    and know a command. `active_specs` excludes a disabled source from every
    run, so nothing but this clears the counter.

    It does NOT claim the source is fixed. It clears the strikes so the next run
    tries again; if the cause is still there the source earns three fresh
    strikes and disables itself again, which is the machinery working.

    Reads `app.state.settings`, NOT `get_settings()`. Every other router takes
    it from app state, and this one calling the module-level cache directly had
    two consequences: it escaped the test fixture's sandbox and read the real
    `config/sources.yaml`, and in production it missed the rebind
    `config.py::_settings_saved` performs after a settings save -- the same
    split that function's own comment means by "clearing the lru_cache is only
    half the job".
    """
    from venona.sources.registry import clear_strikes, load_sources

    known = {spec.id for spec in load_sources(request.app.state.settings.config_dir)}
    if source_id not in known:
        raise HTTPException(404, detail=f"no source {source_id!r} in sources.yaml")
    if not clear_strikes(conn, source_id):
        raise HTTPException(404, detail=f"no health record for {source_id!r}")
    return {"id": source_id, "auto_disabled": False, "strikes": 0}


def _match_for(domain: str) -> str | None:
    """`https://*.{domain}/*`, or None if the host is not one we would fetch.

    Validated through `urlguard.check_static` rather than a character blacklist,
    so this endpoint's idea of a safe host is the fetcher's idea of one rather
    than a second one free to drift. That function lists what it refuses, and
    says it is safe to call from validation and from the request path.

    The `*.` prefix is what recovers the `www.` that `canonical_url` strips on
    the way into `site_domain`: Chrome matches `*.example.com` against
    `example.com` itself as well as its subdomains.
    """
    from venona.sources.urlguard import BlockedUrl, check_static

    candidate = f"https://{domain}/"
    try:
        check_static(candidate)
    except BlockedUrl:
        return None
    # `check_static` accepts a host with a port; a match pattern must not carry
    # one, and `site_domain` is a raw netloc, so this is reachable.
    if ":" in domain or "/" in domain or "*" in domain or "." not in domain:
        return None
    return f"https://*.{domain}/*"


@router.get("/sources/sites", response_model=AnnotatableSites)
def annotatable_sites(request: Request, conn: db.Conn = Depends(get_db)) -> AnnotatableSites:
    """The sites the browser extension may ask permission to annotate.

    The union of two things, and it needs both. The corpus half
    (`documents.site_domain`) is the only place a discovery sweep's finds are
    visible at all — a `kind: discovery` source carries no `url`. The configured
    half means a fresh checkout that has never run still offers its publishers
    rather than an empty page.

    **`source_id IN (known ids)` is a security control, not tidiness.** The
    extension's toolbar button POSTs *any* http(s) page to `/ingest` with no
    allowlist — that is the point of it, the article worth adding is usually
    from a source `sources.yaml` has never heard of — and `pipeline/ingest.py`
    stores the result under `MANUAL_SOURCE_ID`. Without this filter every page
    anyone ever clicked that button on becomes a row whose checkbox grants
    `https://*.<that-host>/*`: permission to inject into, and POST the text of,
    every page on an intranet or a webmail domain. The hostname validator cannot
    catch that — those hostnames are perfectly well formed. Binding the id list
    from `sources.yaml` also means a source removed from the file stops being
    offered.

    Reads `app.state.settings`, NOT `get_settings()`, for the reason
    `enable_source` documents at length: the module-level cache escapes the test
    fixture's sandbox and misses the rebind `save_config` performs.
    """
    import yaml

    from venona.pipeline.normalize import canonical_url
    from venona.sources.registry import SourceConfigError, load_sources

    # A malformed or missing sources.yaml must degrade, not 500. Three distinct
    # failures reach here and they are easy to under-catch: `OSError` for a file
    # that is gone, `yaml.YAMLError` for one that does not parse, and
    # `SourceConfigError` for one that parses but is not a valid source list.
    # Unlike `enable_source` — a mutation nobody polls — this endpoint is on the
    # extension's refresh alarm, so it answers with what it has.
    try:
        specs = list(load_sources(request.app.state.settings.config_dir))
    except (SourceConfigError, OSError, yaml.YAMLError):
        specs = []

    known_ids = {spec.id for spec in specs}

    # Feed host -> publisher, derived with the SAME function the corpus used, so
    # the two halves of the union agree instead of splitting `defenseone.com`
    # from `www.defenseone.com`.
    configured: dict[str, str | None] = {}
    for spec in specs:
        if spec.kind != "rss" or not spec.enabled or not spec.url:
            continue
        host = urlsplit(canonical_url(spec.url)).netloc
        if host:
            configured.setdefault(host, spec.publisher)

    rows: dict[str, dict[str, Any]] = {}
    for row in repo.annotatable_sites(conn, sorted(known_ids)):
        rows[row["site_domain"]] = {
            "documents": row["documents"],
            "last_seen_at": row["last_seen_at"],
            "publisher": row["publisher"],
        }

    sites: list[AnnotatableSite] = []
    for domain in sorted(set(rows) | set(configured)):
        match = _match_for(domain)
        if match is None:
            continue
        seen = rows.get(domain, {})
        sites.append(
            AnnotatableSite(
                domain=domain,
                match=match,
                # A configured source's own `publisher` is authored; the
                # aggregate is whatever the page happened to claim. Prefer the
                # authored one, and fall back for discovery-only domains, which
                # have no spec at all and are the case this feature exists for.
                publisher=configured.get(domain) or seen.get("publisher"),
                documents=seen.get("documents", 0),
                configured=domain in configured,
                last_seen_at=seen.get("last_seen_at"),
            )
        )

    sites.sort(key=lambda s: (-s.documents, s.domain))
    return AnnotatableSites(sites=sites)


@router.get("/meta/ontology", response_model=OntologyResponse)
async def ontology() -> dict[str, Any]:
    """The research<->graph contract, served to the UI.

    Filter chips, the graph legend and the entity colour mapping are all built
    from this rather than a hardcoded frontend list -- so adding an entity type
    in config/ontology.yaml surfaces everywhere without a frontend change.
    """
    from venona.ontology import get_ontology

    onto = get_ontology()
    return {
        "version": onto.version,
        "entity_types": [
            {
                "name": name,
                "prefix": spec.prefix,
                "token": f"--entity-{spec.prefix}",
                "color_slot": spec.color_slot,
                "slot_token": f"--entity-slot-{spec.color_slot}",
                "description": spec.description.strip(),
                "org_types": list(spec.org_types),
                "platform_types": list(spec.platform_types),
                "confusable_with": list(spec.confusable_with),
            }
            for name, spec in onto.entity_types.items()
        ],
        "predicates": [
            {
                "name": name,
                "domain": list(spec.domain),
                "range": list(spec.range),
                "symmetric": spec.symmetric,
                "description": spec.description.strip(),
                "example": spec.example,
            }
            for name, spec in onto.predicates.items()
        ],
        "rewrites": [
            {
                "predicates": list(row.predicates),
                "subjects": list(row.subjects),
                "objects": list(row.objects),
                "to_predicate": row.to_predicate,
                "swap": row.swap,
                "note": row.note.strip() or None,
            }
            for row in onto.rewrites
        ],
    }


@router.get("/meta/rubric", response_model=RubricMeta)
async def rubric_meta() -> dict[str, Any]:
    """What relevance is scored against, projected for a reader.

    The tailor screen and the settings page are built from this rather than a
    hardcoded frontend list -- the `ontology` pattern. Only the rubric's
    reader-facing halves are served: `label` and `plain`. The `description`
    is the model's scoring contract ("Score 5 for...") and stays off every
    reader surface -- rendered at a person it reads as the machine's homework,
    and the prose relevance profile stays admin for the same reason.
    """
    from venona.llm.schemas import load_rubric
    from venona.services.personal_relevance import EMPHASIS_FACTORS

    rubric = load_rubric()
    return {
        "axes": [
            {
                "id": axis["id"],
                "label": (axis.get("label") or "").strip() or None,
                "plain": (axis.get("plain") or "").strip() or None,
                "default_weight": float(axis.get("weight", 0)),
            }
            for axis in rubric["axes"]
        ],
        "emphasis_levels": list(EMPHASIS_FACTORS),
    }


@router.post("/rescore", response_model=RescoreResponse)
def rescore(conn: db.Conn = Depends(get_db)) -> dict[str, Any]:
    """Reweight every stored cluster from its per-axis scores.

    ZERO model calls -- this is why the model is never asked for an overall
    score. Changing a weight in `profile/scoring_rubric.yaml` re-ranks history
    instantly and for free. Changing an axis DESCRIPTION is different: the
    stored scores then answer a different question and re-analysis is required.
    """
    from venona.services.rescore import rescore_all

    report = rescore_all(conn)
    return {**report.as_dict(), "model_calls": 0}


@router.get("/meta/guide", response_model=Guide, response_model_exclude_none=True)
def guide(request: Request) -> dict[str, Any]:
    """`docs/USER-GUIDE.md`, parsed into blocks for the `?` in the header.

    Served rather than bundled so the file stays the single source of truth --
    it is read in a terminal and on GitHub as well as in the app, and a copy
    under `web/` would drift the first time anyone edited one of them.

    `response_model_exclude_none` for the reason `/health` sheds unset fields:
    every run would otherwise carry `b: null, i: null, s: null, code: null,
    href: null`, which is five nulls per run across a 200-block document.
    """
    return {"blocks": guide_service.load_guide(request.app.state.settings.docs_dir)}


@router.get("/meta/guide/admin", response_model=Guide, response_model_exclude_none=True)
def guide_admin(request: Request) -> dict[str, Any]:
    """`docs/ADMIN-GUIDE.md`, the same way -- but admin-only.

    A separate route rather than a role branch inside `/meta/guide` because
    authz classifies BY ROUTE (contract in `api/authz.py`): the reader route
    stays on the reader allowlist and this one falls to the admin default. The
    split is real, not cosmetic -- the admin guide describes spend controls and
    the data purge, which a reader cannot reach and should not study.
    """
    return {
        "blocks": guide_service.load_guide(request.app.state.settings.docs_dir, "ADMIN-GUIDE.md")
    }


@router.get("/meta/mcp", response_model=McpInfo)
def mcp_info(request: Request, conn: db.Conn = Depends(get_db)) -> dict[str, Any]:
    """What the MCP server exposes, and which agents hold a credential for it.

    NOT reachable by an agent token. `/api/meta/mcp` is outside `MCP_PATHS`, so a
    `vmcp_` credential gets a 403 here -- which is right: the list of connected
    agents is the operator's, and a leaked token should not enumerate its
    siblings. This is for the browser.

    The tool list comes from `registry.TOOLS` rather than a literal, so
    `Settings -> Agents` cannot disagree with `tools/list` about what exists.
    """
    from venona.api.mcp import protocol
    from venona.api.mcp.registry import TOOLS
    from venona.api.security import Principal, get_auth_config
    from venona.services import token_service

    principal: Principal | None = getattr(request.state, "principal", None)

    return {
        "path": "/api/mcp",
        "protocol_versions": list(protocol.SUPPORTED),
        "auth_required": get_auth_config().enabled,
        "tools": [
            {
                "name": tool.name,
                "title": tool.title,
                "summary": tool.summary,
            }
            for tool in (TOOLS[name] for name in sorted(TOOLS))
        ],
        "tokens": [
            {
                "id": row.id,
                "label": row.label,
                "prefix": row.prefix,
                "scope": row.scope,
                "created_at": row.created_at,
                "last_used_at": row.last_used_at,
                "expires_at": row.expires_at,
                "client_name": row.client_name,
            }
            # SCOPED TO THE CALLER: unscoped, this is one reader reading
            # another's credential metadata. A principal with no user row -- the
            # root bearer, or `via="disabled"` on a `make dev` box -- sees
            # nothing rather than everything, which is the safe direction; the
            # operator's unscoped view is `GET /api/admin/tokens`. `connections`
            # requires the argument rather than defaulting it, so the scoping
            # cannot be dropped here by accident.
            for row in token_service.connections(
                conn, user_id=(principal.user_id if principal else None) or ""
            )
        ],
    }


@router.delete("/meta/mcp/tokens/{token_id}", response_model=Disconnected)
def disconnect_token(
    token_id: str, request: Request, conn: db.Conn = Depends(get_db)
) -> Disconnected:
    """End one of the caller's own connections.

    WHY A WRITE IS ALLOWED HERE WHEN MINTING IS NOT (contract C12). The rule is
    not "no credential operations over HTTP", it is that over HTTP access can be
    TAKEN AWAY and never handed out. Minting through a route would let a
    compromised credential manufacture a persistent one that outlives rotation of
    the credential that made it; revoking through a route can only ever reduce
    what exists. ADR 0128 drew the line at the terminal for both because both
    were terminal-only; the half that stands is the mint.

    Revoking has to be reachable from a browser now that connecting is. A person
    who signed an assistant in through the consent screen never sees a token id,
    so `venona mcp token revoke <id>` asks them for a value they were never
    shown -- and the operator of a hosted instance is not necessarily the reader
    who connected.

    OWNERSHIP IS CHECKED, NOT ASSUMED. Token ids are printed by the CLI and
    rendered on a page, so they are not secret and possession of one is not
    authority over it. `token_service.owns` is the gate; without it this route
    would be one reader disconnecting another's assistant by typing an id.

    404 rather than 403 for somebody else's, because a 403 would confirm the id
    exists -- the same reason `/api/auth/callback` makes all of its refusals
    indistinguishable.

    Idempotent: revoking an already-revoked connection reports `ended: false`
    and is not an error. A page that just did this on a stale list should say
    "already gone", not fail.
    """
    from venona.api.security import Principal
    from venona.pipeline.types import utc_now_iso
    from venona.services import token_service

    principal: Principal | None = getattr(request.state, "principal", None)
    user_id = (principal.user_id if principal else None) or ""
    if not token_service.owns(conn, token_id, user_id=user_id):
        raise HTTPException(status_code=404, detail="no such connection")
    return Disconnected(ended=token_service.revoke(conn, token_id, now=utc_now_iso()))
