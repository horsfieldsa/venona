"""The hub's management surface -- four routes, and a leash that is the point.

Only a `vmgmt_` bearer reaches these (its `Principal.reach` is exactly this
route set, `authz.MGMT_REACH`), and these are ALL it reaches. What the hub can
never do through this credential is written in CONTRACTS.md: no other config,
no run control beyond starting setup, no token minting, no exports. Every
write here goes through the same seams everything else uses --
`config_service.write_doc`, `user_service`, `run_launcher` -- so the hub gets
no private path into anything (docs/adr/0142).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from venona.api import authz
from venona.api.deps import config_audit, get_db
from venona.api.schemas.mgmt import (
    MgmtDomainIn,
    MgmtDomainSaved,
    MgmtSettingsIn,
    MgmtSettingsSaved,
    MgmtSetupStarted,
    MgmtStatus,
    ProvisionResult,
    ProvisionUser,
)
from venona.db import core as db
from venona.db.repositories import curation as curation_repo
from venona.db.repositories import hub_flags as hub_flags_repo
from venona.db.repositories import users as users_repo
from venona.domain import get_domain
from venona.pipeline.types import SystemIdGen, utc_now_iso
from venona.release import COMMIT, RELEASE, STARTED_AT
from venona.services import (
    brief_service,
    config_service,
    draft_service,
    run_launcher,
    run_service,
    run_types,
    setup_service,
    user_service,
)

log = logging.getLogger("venona.api.mgmt")

router = APIRouter(prefix="/mgmt", tags=["mgmt"])


@router.get("/status", response_model=MgmtStatus)
def status(request: Request, conn: db.Conn = Depends(get_db)) -> dict[str, Any]:
    settings = request.app.state.settings
    descriptor = get_domain()
    # Counted by the database. The hub polls this, so pulling every user row
    # across the wire to increment two integers is a cost that grows with the
    # instance for an answer whose size never does.
    active = users_repo.roles(conn)
    roles = {"admin": active.get("admin", 0), "reader": active.get("reader", 0)}
    corpus = curation_repo.row_counts(conn, ["documents", "entities", "assertions"])
    return {
        # Read from the installed package rather than typed here, so the hub
        # sees the version this instance actually runs. A literal is a claim
        # nothing updates, and the hub's whole purpose is telling instances
        # apart. Which is also why the release alone is not enough: it is the
        # same number on every domain until someone bumps it.
        "version": RELEASE,
        "commit": COMMIT,
        "started_at": STARTED_AT,
        "domain_slug": descriptor.slug,
        "display_name": descriptor.display_name,
        "setup": {
            "configured": setup_service.is_configured(settings),
            "slug": descriptor.slug,
            "display_name": descriptor.display_name,
            "has_dossier": setup_service.load_dossier(settings) is not None,
            "drafts": [row["key"] for row in draft_service.list_drafts(settings)],
            "active_run": run_service.registry.active_run_id(run_types.RESEARCH),
        },
        "documents": corpus["documents"],
        "entities": corpus["entities"],
        # `assertions` IS the graph: `venona graph rebuild` reconstructs every
        # edge in Neo4j from this table, so counting it here needs no graph
        # connection and cannot disagree with what the projection holds.
        "relationships": corpus["assertions"],
        "users": {"admins": roles["admin"], "readers": roles["reader"]},
        # WHAT IS TRUE HERE, not what the hub last sent. `billing_enforced()`
        # applies the environment override, so a domain whose operator has
        # forced the gate off reports off -- which is the whole value of the
        # field, because the hub's own page would otherwise show its intent
        # back to itself and call it confirmation.
        "billing_enforced": authz.billing_enforced(),
        # What this domain says it is, and whether it is still saying anything.
        # Both are read by the hub's gallery, which asks a stranger to choose
        # between domains on the strength of them.
        "login_blurb": descriptor.login_blurb,
        "latest_brief_at": brief_service.latest_brief_generated_at(conn),
    }


def _authority_change(
    conn: db.Conn,
    *,
    user_id: str,
    detail: str,
    now_iso: str,
    ids: SystemIdGen,
    role: str | None = None,
    status: str | None = None,
) -> str | None:
    """Apply one hub-driven authority change. Returns the refusal, or None.

    `change_authority` IS the door (`services/user_service.py`), and reaching
    around it with `set_role`/`set_status` skips the rule that matters most here:
    never demote or suspend the last active admin. A hub membership edit could
    otherwise leave an instance with nobody able to administer it, and the hub
    would be told it worked.

    `actor=None` is correct rather than convenient. The hub is the authority on
    this instance's membership, so the two refusals about the ACTOR -- must be an
    active admin, may not target themselves -- have nobody to be about. The
    last-admin refusal checks the TARGET and still applies, which is the point.

    A refusal is RETURNED, never swallowed: the hub and the instance disagreeing
    about who administers this instance is exactly the state an operator needs to
    see, and a silent success would hide it until the day it mattered.
    """
    try:
        with db.transaction(conn, what="change_authority"):
            user_service.change_authority(
                conn,
                user_id=user_id,
                role=role,
                status=status,
                actor=None,
                event_id=f"uevent:{ids.uuid7()}",
                now=now_iso,
                epoch=time.time(),
                note=detail,
            )
    except user_service.AuthorityError as exc:
        log.warning("hub authority change refused for %s: %s", user_id, exc)
        return str(exc)
    return None


@router.post("/provision-user", response_model=ProvisionResult)
def provision_user(
    payload: ProvisionUser, request: Request, conn: db.Conn = Depends(get_db)
) -> dict[str, Any]:
    """The hub's half of membership: add, sync, suspend, reinstate.

    Suspension is the one that closes a real gap -- removing a hub membership
    only bites at the member's next login otherwise, and a fourteen-day cookie
    is a long otherwise. `set_status` bumps the session epoch, so it bites on
    the next request.
    """
    email = payload.email.strip().lower()
    user = user_service.lookup(conn, email)
    now_iso = utc_now_iso()
    ids = SystemIdGen()

    def apply_entitlement(user_id: str) -> None:
        """Store the hub's projection, if it sent one.

        Runs for every action rather than only for `update`, because the hub
        pushes entitlement alongside whatever else it is saying and a suspend
        that silently dropped it would leave a stale projection behind. A push
        that omits the fields changes nothing.

        Called BEFORE the action dispatches, and before any refusal can return.
        The hub's word about a role and its word about money are independent:
        an authority change this instance declines still answers 200, the hub
        stamps the push as delivered, and `pending_pushes` only re-sends what is
        behind -- so an entitlement dropped on the way past that refusal is
        dropped for good.
        """
        if payload.entitlement_status is None:
            return
        users_repo.set_entitlement(
            conn,
            user_id=user_id,
            status=payload.entitlement_status,
            through=payload.entitled_through,
            notice=payload.billing_notice,
            notice_at=payload.billing_notice_at,
        )

    if payload.action == "add":
        if user is not None:
            return {"email": email, "action": "add", "role": user.role, "status": "exists"}
        # A REFUSAL, not a 500. `create` now caps what a wire may grant at
        # `reader` (ADR 0168), and letting that reach the error middleware would
        # tell the hub "something went wrong" about a decision this instance made
        # on purpose. Same shape as the authority refusals below: returned, never
        # swallowed, because the hub and the instance disagreeing about who
        # administers this instance is exactly the state an operator must see.
        try:
            created = user_service.create(
                conn, user_id=f"user:{ids.uuid7()}", email=email, role=payload.role, now=now_iso
            )
        except user_service.AuthorityError as exc:
            log.warning("hub provision refused for %s: %s", email, exc)
            return {
                "email": email,
                "action": "add",
                "role": payload.role,
                "status": "refused",
                "message": str(exc),
            }
        user_service.record_event(
            conn,
            event_id=f"uevent:{ids.uuid7()}",
            user_id=created.id,
            actor=None,
            action="created",
            detail=f"provisioned via hub as {payload.role}",
            now=now_iso,
        )
        apply_entitlement(created.id)
        return {"email": email, "action": "add", "role": payload.role, "status": "created"}

    if user is None:
        raise HTTPException(404, detail=f"no user {email!r} on this instance")

    apply_entitlement(user.id)

    if payload.action == "update":
        if user.role != payload.role:
            # THROUGH `change_authority`, not `set_role`. The direct setter skips
            # every rule that makes an authority change safe -- including "never
            # the last active admin" -- so a hub membership edit could strand an
            # instance with nobody able to administer it, and the hub would
            # report success. `actor=None` is right here (the hub IS the
            # authority, so the self-target and actor-is-admin refusals do not
            # apply) and still enforces the last-admin rule.
            refusal = _authority_change(
                conn,
                user_id=user.id,
                role=payload.role,
                detail=f"hub membership says {payload.role} (was {user.role})",
                now_iso=now_iso,
                ids=ids,
            )
            if refusal is not None:
                return {
                    "email": email,
                    "action": "update",
                    "role": user.role,
                    "status": "refused",
                    "message": refusal,
                }
        return {"email": email, "action": "update", "role": payload.role, "status": "ok"}

    if payload.action == "logout":
        # The hub's sign-out reaching this instance: end this user's sessions
        # here, the same epoch bump the user's own logout performs. No status
        # change -- a signed-out reader is not a suspended one.
        user_service.bump_session_epoch(conn, user_id=user.id, now=time.time())
        user_service.record_event(
            conn,
            event_id=f"uevent:{ids.uuid7()}",
            user_id=user.id,
            actor=None,
            action="logged_out",
            detail="hub sign-out ended this instance's sessions",
            now=now_iso,
        )
        return {"email": email, "action": "logout", "role": user.role, "status": "ok"}

    new_status = user_service.SUSPENDED if payload.action == "suspend" else user_service.ACTIVE
    refusal = _authority_change(
        conn, user_id=user.id, status=new_status, detail="via hub", now_iso=now_iso, ids=ids
    )
    if refusal is not None:
        return {
            "email": email,
            "action": payload.action,
            "role": user.role,
            "status": "refused",
            "message": refusal,
        }
    return {"email": email, "action": payload.action, "role": user.role, "status": "ok"}


@router.post("/domain", response_model=MgmtDomainSaved)
def push_domain(
    payload: MgmtDomainIn, request: Request, conn: db.Conn = Depends(get_db)
) -> dict[str, Any]:
    """The ONE config push. Same validator, same history snapshot, same slug
    freeze as the editor's own save -- the hub holds no privileged pen.

    Same audit row, too, and this is the write that needs it most: the hub's
    credential is not a person, so `actor_id` is NULL and the row is the only
    thing that says a push happened at all. `domain.yaml`'s fragments splice into
    every system prompt (ADR 0166).
    """
    settings = request.app.state.settings
    try:
        config_service.write_doc(
            settings, "domain", payload.content, audit=config_audit(request, conn)
        )
    except config_service.ConfigError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    return {"slug": get_domain().slug}


@router.post("/settings", response_model=MgmtSettingsSaved)
def push_settings(
    payload: MgmtSettingsIn, request: Request, conn: db.Conn = Depends(get_db)
) -> dict[str, Any]:
    """The hub's switches, stored where this instance can read them alone.

    NOT `config/settings.yaml`, and that is contract C12 rather than a
    preference: that file is editable over HTTP by any admin session, and a
    paywall an administrator can switch off is not a paywall (migration 0024).

    The cache refresh is the half that makes this take effect. `billing_enforced`
    is answered from memory on every request, so a write with no refresh is a
    switch that only lands on the next restart -- which is exactly the deploy
    this page exists to avoid.

    Returns what is now TRUE rather than what was sent: `VENONA_BILLING_ENFORCED`
    overrides the hub in both directions, so a domain can honestly answer "you
    said on, I am off" and the operator can see it.
    """
    with db.transaction(conn, what="push_settings"):
        hub_flags_repo.set_billing_enforced(
            conn, enforced=payload.billing_enforced, now=utc_now_iso()
        )
        authz.refresh_billing_enforced(conn)
    return {"billing_enforced": authz.billing_enforced()}


@router.post("/setup/start", status_code=202, response_model=MgmtSetupStarted)
async def start_setup(request: Request) -> dict[str, Any]:
    """Kick the generation run remotely. Same guard, same launcher, same lane
    rules as the wizard's own button; params are the defaults."""
    from venona.api.routers.runs import _refuse_if_research_is_busy

    settings = request.app.state.settings
    _refuse_if_research_is_busy(settings)
    params: dict[str, Any] = {"domain": get_domain().slug, "via": "hub"}

    async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        return await setup_service.run_domain_setup(target, settings, params)

    try:
        started = await run_launcher.launch(request, kind="domain_setup", params=params, work=work)
    except run_launcher.LaneBusy as exc:
        raise HTTPException(409, detail=f"run {exc} is already active") from exc
    return {"run_id": started["run_id"]}
