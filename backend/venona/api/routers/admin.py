"""Who may reach this instance, and what has been done about it.

EVERY ROUTE HERE IS ADMIN BY DEFAULT-DENY, and none of them appears in
`authz.READER_ROUTES`. That is the whole authorization story for this file:
`role_for_request` answers `admin` for any route it does not recognise, so these
are protected by not being listed rather than by anything written here. A route
added to this module next year inherits the same protection without its author
remembering anything, which is the property `Depends()` could never give.
`tests/test_authz.py::ADMIN_ROUTES` is the ratchet that fails when a route is
added without a decision either way.

WHAT THIS SURFACE MAY DO, and the argument for the line. `cli.py::_users` kept
user management off HTTP entirely, because "an HTTP endpoint that grants a role
is reachable by anything holding a session, so the first admin would be minted by
whoever got there first". That failure cannot occur here -- every route is
admin-gated, so the surface is unreachable until an administrator already
exists, and `ensure_from_env` plus `venona users add` remain the only routes to
the first one. What survives of the argument is the ceiling:

    Over HTTP, authority may be REDUCED without limit, and EXTENDED only as far
    as `reader`. Crossing into `admin` requires shell access.

Granting a reader is not minting a credential -- the credential is the identity
provider's, and the address still has to be one its owner can authenticate as.
Granting an admin hands out model spend, every document in
`config_service.EDITABLE` including the operator's own relevance profile, and
`POST /api/data/purge`.

THE RULES ARE NOT IN THIS FILE. `user_service.change_authority` holds the lock,
the self-target refusal, the last-admin refusal and the audit write, because
`venona users` reaches the same rules from another process -- and a check that
lived in a router is a check the CLI does not run.
"""

from __future__ import annotations

import contextlib
import time
from datetime import UTC

from fastapi import APIRouter, Depends, HTTPException, Request

from venona.api.authz import actor_id
from venona.api.deps import Window, config_audit, get_db, page_window
from venona.api.schemas.admin import (
    AdminOverview,
    AdminToken,
    AdminTokenList,
    AdminUser,
    AdminUserList,
    AuthorityEvent,
    AuthorityEventList,
    ConnectionEnded,
    CredentialInput,
    ModelConnection,
    ModelPlan,
    ModelRole,
    ModelSelection,
    ModelService,
    ModelSettings,
    RoleChange,
    SpendDay,
    SuspensionChange,
)
from venona.api.schemas.meta import Disconnected
from venona.db import core as db
from venona.db.repositories import access as access_repo
from venona.db.repositories import metrics as metrics_repo
from venona.db.repositories import token as token_repo
from venona.db.repositories import users as users_repo
from venona.pipeline.types import SystemIdGen, utc_now_iso
from venona.release import COMMIT, RELEASE, STARTED_AT
from venona.services import harness_service, token_service, user_service

router = APIRouter(prefix="/admin", tags=["admin"])

#: How much of the ledger the dashboard draws. Enough bars to see a trend in a
#: month of nightly runs, few enough to read at a glance.
SPEND_DAYS = 30


def _row_to_user(row: dict) -> AdminUser:
    return AdminUser(
        id=row["id"],
        email=row["email"],
        role=row["role"],
        status=row["status"],
        display_name=row.get("display_name"),
        created_at=row.get("created_at"),
        last_seen_at=row.get("last_seen_at"),
        # `last_seen_at` is stamped at sign-in, so its absence is the same fact
        # and is one fewer column to serve.
        never_signed_in=not row.get("last_seen_at"),
        entitlement_status=row.get("entitlement_status") or "none",
        entitled_through=row.get("entitled_through"),
    )


def _since(days: int) -> str:
    """An ISO-8601 floor `days` before now.

    Computed here rather than in the repository because `db/` must not ask what
    time it is, and rather than in `services/` because `lint-determinism` forbids
    a wall clock there. `api/` is the layer that is allowed to know.
    """
    from datetime import datetime, timedelta

    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


@router.get("/overview", response_model=AdminOverview)
def overview(conn: db.Conn = Depends(get_db)) -> AdminOverview:
    """The five things an operator cannot see anywhere else.

    Health is deliberately absent -- see the schema. Every number here answers a
    question that today has either a CLI command or no answer at all.
    """
    # Counted in the database rather than by reading every row and measuring the
    # list here. This page reports numbers; the tables behind them grow with the
    # instance, and nothing on the screen wants a single person's columns.
    people = users_repo.population(conn)
    spend = metrics_repo.cost_by_day(conn, since=_since(SPEND_DAYS))

    return AdminOverview(
        version=RELEASE,
        commit=COMMIT,
        started_at=STARTED_AT,
        pending_requests=access_repo.pending_count(conn),
        users_total=people["total"],
        users_suspended=people["suspended"],
        users_never_signed_in=people["never_signed_in"],
        active_admins=users_repo.active_admin_count(conn),
        tokens_live=token_repo.count_live(conn),
        spend=[
            SpendDay(
                day=row["day"],
                cost_usd=float(row["cost_usd"] or 0),
                calls=int(row["calls"] or 0),
                cost_basis=row["cost_basis"] or "unknown",
            )
            for row in spend
        ],
        spend_days=SPEND_DAYS,
    )


@router.get("/users", response_model=AdminUserList)
def list_users(
    window: Window = Depends(page_window(default=100, cap=500)),
    conn: db.Conn = Depends(get_db),
) -> AdminUserList:
    """Everyone who can reach this instance. `venona users ls`, on a screen.

    Bounded at the route, not buried in the repository — the same query feeds
    the CLI and the overview's aggregates, which read everyone. `total` is
    what makes the bound honest: a capped page announces itself.
    """
    return AdminUserList(
        items=[
            _row_to_user(row)
            for row in user_service.listing(conn, limit=window.limit, offset=window.offset)
        ],
        active_admins=users_repo.active_admin_count(conn),
        total=user_service.count(conn),
    )


@router.get("/events", response_model=AuthorityEventList)
def list_events(
    window: Window = Depends(page_window(default=50, cap=200)),
    conn: db.Conn = Depends(get_db),
) -> AuthorityEventList:
    """The audit trail. What makes this whole surface reviewable.

    A CLI change is attributable to shell access, which is already the highest
    credential on the box. An HTTP change is attributable to nothing unless
    something writes it down -- so this is the endpoint that justifies the rest
    of the file existing, rather than a nice-to-have bolted onto it.

    `ge=1` because a non-positive limit reaches Postgres as a negative LIMIT
    and 500s; `le=` refuses loudly where a silent clamp answered a smaller
    question than the one asked.
    """
    return AuthorityEventList(
        items=[
            AuthorityEvent(**row)
            for row in user_service.events(conn, limit=window.limit, offset=window.offset)
        ],
        total=users_repo.event_count(conn),
    )


@router.get("/tokens", response_model=AdminTokenList)
def list_tokens(
    window: Window = Depends(page_window(default=100, cap=500)),
    conn: db.Conn = Depends(get_db),
) -> AdminTokenList:
    """Every agent credential on the instance, and whose it is.

    THE ONE READ `GET /api/meta/mcp` CANNOT DO. That endpoint is scoped to the
    calling user and should be -- an unscoped one is a reader reading the label,
    prefix and last-used time of somebody else's credential. The cost of that
    scoping is that the person who owns the box cannot see what exists on it, so
    a leaked token is invisible to the only person who could revoke it. This is
    the other half, and it is a SEPARATE route rather than a widening of the
    per-user one: "my agents" and "every agent on this instance" are different
    questions, and only the second is privileged.

    Admin by not being in `authz.READER_ROUTES`, like everything else in this
    module. There is no owner-scoped variant here and there must not be one --
    a `?user_id=` parameter would put the scoping decision in a query string.

    Revoking is `DELETE /api/admin/tokens/{id}` below, or
    `venona mcp token revoke <id>`. Making one is still the CLI alone, and that
    asymmetry is contract C12 rather than an unfinished screen: over HTTP,
    access can be taken away and never handed out.
    """
    return AdminTokenList(
        items=[
            AdminToken(
                id=row.id,
                label=row.label,
                prefix=row.prefix,
                scope=row.scope,
                user_id=row.user_id,
                user_email=row.user_email,
                client_name=row.client_name,
                created_at=row.created_at,
                last_used_at=row.last_used_at,
                expires_at=row.expires_at,
            )
            for row in token_service.instance_listing(
                conn, limit=window.limit, offset=window.offset
            )
        ],
        total=token_service.instance_count(conn),
    )


@router.delete("/tokens/{token_id}", response_model=Disconnected)
def revoke_token(token_id: str, conn: db.Conn = Depends(get_db)) -> Disconnected:
    """End anybody's connection. The other half of the unscoped list.

    NO OWNERSHIP CHECK, deliberately, and that is the whole difference from
    `DELETE /api/meta/mcp/tokens/{id}`. The reason the unscoped LIST exists is
    that a leaked credential is invisible to the only person who could revoke
    it; a list that names one and a revoke that refuses it would have left the
    hole exactly where it was.

    Admin by not being in `authz.READER_ROUTES`, like everything else in this
    module.

    404 for an id that names nothing, so a typo reads as a typo. There is no
    hiding to do here -- this caller may already enumerate every token on the
    instance through the route above.
    """
    if not token_service.exists(conn, token_id):
        raise HTTPException(status_code=404, detail="no such connection")
    return Disconnected(ended=token_service.revoke(conn, token_id, now=utc_now_iso()))


def _apply(conn: db.Conn, request: Request, *, user_id: str, **change) -> AdminUser:
    """One authority change, with the refusals mapped to status codes.

    409 rather than 403 for every refusal, and they are refusals rather than
    permission failures: the caller IS an administrator and is allowed to be
    here. What they asked for is the thing that cannot happen. A 403 would send
    the SPA's global forbidden toast -- "your account cannot do that on this
    instance" -- which is untrue and unhelpful, and would hide the sentence that
    says which rule stopped them.
    """
    try:
        with db.transaction(conn, what="change_authority"):
            changed = user_service.change_authority(
                conn,
                user_id=user_id,
                actor=actor_id(request),
                event_id=f"uevent:{SystemIdGen().uuid7()}",
                now=utc_now_iso(),
                epoch=time.time(),
                **change,
            )
    except user_service.AuthorityError as exc:
        raise HTTPException(
            status_code=409, detail={"error": "authority_refused", "message": str(exc)}
        ) from exc

    row = user_service.profile(conn, changed.id)
    assert row is not None  # noqa: S101 - just written, same request
    return _row_to_user(row)


@router.patch("/users/{user_id}/role", response_model=AdminUser)
def set_role(
    user_id: str, payload: RoleChange, request: Request, conn: db.Conn = Depends(get_db)
) -> AdminUser:
    """Demote an administrator. THE ONLY DIRECTION THIS ROUTE GOES.

    `RoleChange.role` is `Literal["reader"]`, so a request for `admin` is refused
    by the boundary before this function runs and appears in the OpenAPI document
    as unavailable -- the frontend cannot offer a control the server would refuse.
    Promotion is `venona users role --email <email> --role admin`, and the
    module docstring argues why.
    """
    return _apply(conn, request, user_id=user_id, role=payload.role)


@router.post("/users/{user_id}/suspend", response_model=AdminUser)
def suspend(
    user_id: str, payload: SuspensionChange, request: Request, conn: db.Conn = Depends(get_db)
) -> AdminUser:
    """Stop someone signing in, now rather than in fourteen days.

    Suspension bumps their session epoch in the same statement, so a cookie
    already in a browser stops working on its next request, and
    `AuthMiddleware._verify_mcp` refuses every agent token they own. There is no
    delete: the five actor columns are `ON DELETE NO ACTION`, so Postgres refuses
    to remove anyone who has decided anything and permits removing anyone who has
    not -- a feature that would pass its own tests and fail on every real case.
    """
    return _apply(conn, request, user_id=user_id, status=user_service.SUSPENDED, note=payload.note)


@router.post("/users/{user_id}/reinstate", response_model=AdminUser)
def reinstate(
    user_id: str, payload: SuspensionChange, request: Request, conn: db.Conn = Depends(get_db)
) -> AdminUser:
    """Let them back in. Clears the revocation as well as the status, because a
    reinstated user should not still be carrying one."""
    return _apply(conn, request, user_id=user_id, status=user_service.ACTIVE, note=payload.note)


# ── the model service ────────────────────────────────────────────────

#: Every job that reads a model, and the words a reader has for it. Server-side
#: because a render site that invents English for a wire value is a second
#: place the vocabulary can drift, and only one of the two is checked.
ROLE_LABELS: tuple[tuple[str, str], ...] = (
    ("discover", "Finding stories"),
    ("analyze", "Reading each story"),
    ("acronym", "Grounding a term"),
    ("questions", "Writing study questions"),
    ("research", "Corroborating a name"),
    ("adjudicate", "Ruling on a conflict"),
    ("brief", "Writing the brief"),
    ("personal_brief", "Writing a reader's brief"),
    ("setup", "Setting up a domain"),
)


def _roles(settings) -> list[ModelRole]:
    from venona.llm.harness import model_for, resolve

    harness = resolve(settings.llm.harness)
    out: list[ModelRole] = []
    for role, label in ROLE_LABELS:
        configured = getattr(settings.llm, f"{role}_model", None)
        if configured is None:
            continue
        out.append(
            ModelRole(
                role=role,
                label=label,
                configured=configured,
                resolved=model_for(harness, configured),
                effort=getattr(settings.llm, f"{role}_effort", None),
            )
        )
    return out


async def _can_authenticate(settings, harness) -> bool:
    """Whether this service could actually run, asked THREE ways.

    Stored, inherited from the environment, or signed in through the service's
    own configuration -- and the last one is the ordinary case on a workstation
    and the one that keeps getting forgotten. Answering from what Venona
    happens to hold refuses a switch that would have worked, and does it with a
    message telling the operator to connect something they already connected.
    """
    if harness_service.has_stored(settings, harness) or harness_service.env_present(harness):
        return True
    if getattr(settings, "offline", False):
        return False
    from venona.llm.executor import CliExecutor

    with contextlib.suppress(Exception):
        return bool((await CliExecutor(settings, harness=harness).health()).logged_in)
    return False


def _plans(conn: db.Conn, harness) -> list[ModelPlan]:
    """This service's plans with their human half, and which one is connected.

    `connected` is asked PER PLAN rather than per service. The two are stored
    separately and mean opposite things -- one is a subscription, the other is
    billed per token -- so a service reported as connected without saying on
    which plan answers the wrong question for the person about to choose.
    """
    from venona.db.repositories import harness as harness_repo

    plans: list[ModelPlan] = []
    for mode in sorted(harness.billing_modes):
        help_ = harness.plan_help(mode)
        plans.append(
            ModelPlan(
                mode=mode,  # type: ignore[arg-type]
                label="API key" if mode == "api" else "Subscription",
                credential=help_.credential,
                how=help_.how,
                pasteable=help_.pasteable,
                metered=help_.metered,
                command=help_.command,
                connected=harness_repo.get(conn, harness=harness.key, billing_mode=mode)
                is not None,
            )
        )
    return plans


async def _services(settings, conn: db.Conn) -> list[ModelService]:
    """Every service this instance could use, and whether it could run today.

    Listed rather than hidden behind the change control: an administrator
    cannot weigh an alternative they cannot see, and a button labelled with a
    verb is a poor way to find out one exists. One probe per service, on a page
    nothing polls -- `_can_authenticate` argues why each one is asked.
    """
    import shutil

    from venona.llm.executor import CliExecutor
    from venona.llm.harness import known, resolve

    # NO SUBPROCESS UNDER REPLAY. Each installed service is asked over a fresh
    # `CliExecutor`, whose health cache is empty, so every probe really spawns
    # -- and in the offline suite it would ask whatever the developer's machine
    # happens to be logged into, which is neither hermetic nor free.
    probing = not settings.offline

    out: list[ModelService] = []
    for key in known():
        harness = resolve(key)
        binary = settings.llm.cli_path_for(key)
        installed = shutil.which(binary) is not None
        connected = False
        version = None
        if installed and probing:
            connected = await _can_authenticate(settings, harness)
            with contextlib.suppress(Exception):
                version = (await CliExecutor(settings, harness=harness).health()).version
        elif installed:
            connected = harness_service.has_stored(
                settings, harness
            ) or harness_service.env_present(harness)
        out.append(
            ModelService(
                key=key,
                label=harness.label,
                installed=installed,
                version=version,
                connected=connected,
                billing_modes=sorted(harness.billing_modes),
                plans=_plans(conn, harness),
                capabilities=sorted(harness.capabilities),
            )
        )
    return out


@router.get("/model", response_model=ModelSettings)
async def model_settings(request: Request, conn: db.Conn = Depends(get_db)) -> ModelSettings:
    """What is doing the thinking, what it is signed in as, and what each job asks for.

    Must answer when things are broken, which is the one time it is read: the
    probe, the breaker and the stored credential are all reached defensively,
    because a required field the handler cannot fill is a 500 on the page an
    administrator opened to find out why nothing works.
    """
    from venona.api.routers.config import switch_is_safe
    from venona.llm.harness import resolve
    from venona.services import config_service

    settings = request.app.state.settings
    llm = request.app.state.llm
    harness = resolve(settings.llm.harness)

    harness_service.load(conn)
    probe = await llm.health() if hasattr(llm, "health") else None
    breaker = getattr(llm, "breaker", None)
    state = breaker.state() if breaker is not None else None
    row = harness_service.stored_row(conn, harness, settings.llm.billing_mode)

    try:
        doc = config_service.read_doc(settings, "settings")
        base_version = doc.version
    except Exception:  # noqa: BLE001 - the page still renders without it
        base_version = None

    return ModelSettings(
        service=harness.key,
        service_label=harness.label,
        billing_mode=settings.llm.billing_mode,
        version=probe.version if probe else None,
        pinned=settings.llm.pinned_version_for(harness.key),
        version_matches_pin=probe.version_matches_pin if probe else True,
        connection=ModelConnection(
            source=harness_service.source(
                settings, harness, logged_in=bool(probe and probe.logged_in)
            ),
            account=(probe.account if probe else None) or (row or {}).get("account_label"),
            hint=harness_service.stored_hint(conn, harness, settings.llm.billing_mode),
            verified_at=(row or {}).get("verified_at"),
            # A probe reporting a live session is not the same as calls
            # landing. The breaker is what knows the second, so the page says
            # what each of them established rather than blending them.
            working=None if state is None else not state.get("open"),
        ),
        services=await _services(settings, conn),
        roles=_roles(settings),
        confined=probe.confined if probe else None,
        confinement_detail=probe.confinement_detail if probe else None,
        change_blocked=switch_is_safe(request),
        base_version=base_version,
    )


@router.put("/model", response_model=ModelSettings)
async def set_model(
    payload: ModelSelection, request: Request, conn: db.Conn = Depends(get_db)
) -> ModelSettings:
    """Change which service does the thinking, or how it is paid for.

    REFUSED BEFORE THE WRITE, never after. A pairing this instance cannot serve
    -- a service with nothing to authenticate with, a plan it does not offer --
    must not reach the document, because a saved setting the app then refuses
    to honour is a state nobody can see the cause of.

    409 rather than 403 throughout: the caller IS an administrator and is
    allowed to be here. What they asked for is the thing that cannot happen,
    and a 403 fires the global forbidden toast, which would be untrue.
    """
    from venona.api.routers.config import rebuild_executors, switch_is_safe
    from venona.llm.harness import known, resolve
    from venona.services import config_service, config_text
    from venona.services.config_migrations import _render

    settings = request.app.state.settings
    service = payload.service or settings.llm.harness
    mode = payload.billing_mode or settings.llm.billing_mode

    if service not in known():
        raise HTTPException(
            status_code=422,
            detail={"error": "unknown_service", "message": f"{service!r} is not a model service."},
        )
    harness = resolve(service)
    if mode not in harness.billing_modes:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "unsupported_plan",
                "message": f"{harness.label} cannot be used on a {mode} plan.",
            },
        )

    harness_service.load(conn)
    if not await _can_authenticate(settings, harness):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "no_credential",
                "message": f"Connect {harness.label} before switching to it.",
            },
        )

    blocked = switch_is_safe(request)
    if blocked is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "busy",
                "message": f"Cannot change the model service while {blocked}.",
            },
        )

    doc = config_service.read_doc(settings, "settings")
    # Spliced, not re-serialised. The comments in this document are the
    # documentation, and `ConfigView` refuses to be a form for exactly that
    # reason -- a route that round-trips the YAML would become one by the back
    # door and take the explanations with it.
    text = config_text.set_scalar(doc.content, "llm", "harness", _render(service))
    text = config_text.set_scalar(text, "llm", "billing_mode", _render(mode))
    try:
        config_service.write_doc(
            settings,
            "settings",
            text,
            base_version=payload.base_version,
            audit=config_audit(request, conn),
        )
    except config_service.ConfigError as exc:
        if str(exc) == "CONFLICT":
            current = config_service.read_doc(settings, "settings")
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "conflict",
                    "message": "The settings document changed while this page was open.",
                    "current_version": current.version,
                },
            ) from exc
        raise HTTPException(
            status_code=400, detail={"error": "invalid", "message": str(exc)}
        ) from exc

    from venona.settings import get_settings

    get_settings.cache_clear()
    fresh = get_settings()
    request.app.state.settings = fresh
    rebuild_executors(request, fresh)
    return await model_settings(request, conn)


@router.put("/model/credential", response_model=ModelSettings)
async def connect_credential(
    payload: CredentialInput, request: Request, conn: db.Conn = Depends(get_db)
) -> ModelSettings:
    """Verify a pasted credential against the service, then store it.

    VERIFIED FIRST. A credential that does not work is refused where it was
    typed, which is the only place an administrator can do anything about it --
    the alternative is discovering it on the next run, from a stack of envelope
    errors that name a 401 rather than a paste.
    """
    from venona.llm.harness import known, resolve

    settings = request.app.state.settings
    # THE SERVICE NAMED, not the one in use. Connecting exists so that a
    # switch becomes possible, so requiring the switch first is a loop with no
    # entrance -- and the refusal an operator meets when switching says
    # "connect it first".
    target = payload.service or settings.llm.harness
    if target not in known():
        raise HTTPException(
            status_code=422,
            detail={"error": "unknown_service", "message": f"{target!r} is not a model service."},
        )
    harness = resolve(target)
    if harness.env_for(payload.billing_mode) is None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "not_pasteable",
                "message": (
                    f"{harness.label} keeps its {payload.billing_mode} credential in its own "
                    f"files and refreshes it there, so there is nothing to paste. Sign in with "
                    f"its own command on a machine with a browser."
                ),
            },
        )
    if payload.billing_mode not in harness.billing_modes:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "unsupported_plan",
                "message": f"{harness.label} cannot be used on a {payload.billing_mode} plan.",
            },
        )

    probe = await harness_service.verify(
        settings, harness, secret=payload.secret, billing_mode=payload.billing_mode
    )
    if not probe.ok:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "credential_rejected",
                "message": probe.error or f"{harness.label} did not accept that credential.",
            },
        )

    now = utc_now_iso()
    harness_service.store(
        conn,
        settings,
        harness,
        credential_id=f"hcred:{SystemIdGen().uuid7()}",
        billing_mode=payload.billing_mode,
        secret=payload.secret,
        account_label=probe.account,
        verified_at=now,
        updated_by=actor_id(request),
        now=now,
    )
    for executor in (request.app.state.llm, getattr(request.app.state, "llm_light", None)):
        if hasattr(executor, "invalidate_health"):
            executor.invalidate_health()
    return await model_settings(request, conn)


@router.delete("/model/credential", response_model=ConnectionEnded)
async def disconnect_credential(
    request: Request, conn: db.Conn = Depends(get_db)
) -> ConnectionEnded:
    """Forget a stored credential.

    Refused while it is the only thing authenticating the active service: an
    instance that disconnects its last credential can start no run, write no
    brief and ground no term, and on a headless host there may be no way back
    in from the app.
    """
    from venona.llm.harness import resolve

    settings = request.app.state.settings
    harness = resolve(settings.llm.harness)
    harness_service.load(conn)
    if not harness_service.env_present(harness) and not harness_service.has_home(settings, harness):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "last_credential",
                "message": (
                    f"That is the only credential {harness.label} has. Connect another "
                    f"before removing it, or Venona cannot run at all."
                ),
            },
        )
    ended = harness_service.forget(conn, harness, billing_mode=settings.llm.billing_mode)
    for executor in (request.app.state.llm, getattr(request.app.state, "llm_light", None)):
        if hasattr(executor, "invalidate_health"):
            executor.invalidate_health()
    return ConnectionEnded(ended=ended)
