"""Instance registration and directory administration.

Auth is `AdminBearerMiddleware` -- every route here is born behind it. JSON in
and out (never forms): Epic E1's scaffolder registers instances with a curl it
can also just PRINT for the offline case.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from hub import grants, ids, mgmt_client, seal, security
from hub.db.core import Conn
from hub.deps import get_db
from hub.repositories import instances as instances_repo
from hub.schemas import iso
from hub.schemas.admin import (
    GrantIn,
    GrantResult,
    InstanceDetail,
    InstanceIn,
    InstanceList,
    InstanceOut,
    InstancePatch,
    ProvisionResult,
    ProvisionUserIn,
    RegisteredInstance,
    RotatedSecret,
)

router = APIRouter(prefix="/admin", tags=["admin"])


def _normalized_url(raw: str) -> str:
    try:
        return security.normalized_public_url(raw)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc


def _require_mgmt_shape(token: str) -> None:
    if not security.looks_like_mgmt_token(token):
        raise HTTPException(
            422,
            detail="mgmt_token must be a vmgmt_-prefixed value minted on the "
            "instance with `venona auth mgmt-token`",
        )


def _out(row: dict[str, Any]) -> InstanceOut:
    return InstanceOut(
        id=row["id"],
        slug=row["slug"],
        display_name=row["display_name"],
        tagline=row["tagline"],
        public_url=row["public_url"],
        status=row["status"],
        oidc_client_id=row["oidc_client_id"],
        last_seen_at=iso(row["last_seen_at"]),
        poll_misses=row["poll_misses"],
        created_at=iso(row["created_at"]),
    )


def _row_or_404(conn: Conn, instance_id: str) -> dict[str, Any]:
    row = instances_repo.by_id(conn, instance_id)
    if row is None:
        raise HTTPException(404, detail=f"no instance {instance_id!r}")
    return row


@router.post("/instances", response_model=RegisteredInstance, status_code=201)
def register(body: InstanceIn, request: Request, conn: Conn = Depends(get_db)) -> dict:
    """Register an instance: seal its mgmt token, mint its OIDC client.

    The client secret is returned EXACTLY ONCE -- only its hash survives, so
    losing it means `rotate-secret`, never a lookup.
    """
    _require_mgmt_shape(body.mgmt_token)
    public_url = _normalized_url(body.public_url)
    if instances_repo.by_slug(conn, body.slug) is not None:
        raise HTTPException(409, detail=f"slug {body.slug!r} is already registered")

    settings = request.app.state.settings
    instance_id = ids.instance_id()
    client_id = ids.client_id()
    client_secret = security.new_secret()
    instances_repo.create(
        conn,
        instance_id=instance_id,
        slug=body.slug,
        display_name=body.display_name,
        tagline=body.tagline,
        public_url=public_url,
        oidc_client_id=client_id,
        oidc_client_secret_hash=security.hash_token(client_secret),
        mgmt_token_sealed=seal.seal(settings.seal_key, body.mgmt_token),
    )
    return {
        "id": instance_id,
        "slug": body.slug,
        "oidc_client_id": client_id,
        "oidc_client_secret": client_secret,
    }


@router.get("/instances", response_model=InstanceList)
def list_instances(conn: Conn = Depends(get_db)) -> dict:
    return {"instances": [_out(row) for row in instances_repo.list_all(conn)]}


@router.get("/instances/{instance_id}", response_model=InstanceDetail)
def show_instance(instance_id: str, conn: Conn = Depends(get_db)) -> InstanceDetail:
    row = _row_or_404(conn, instance_id)
    return InstanceDetail(**_out(row).model_dump(), last_status=row["last_status"])


@router.patch("/instances/{instance_id}", response_model=InstanceOut)
def patch_instance(
    instance_id: str, body: InstancePatch, request: Request, conn: Conn = Depends(get_db)
) -> InstanceOut:
    _row_or_404(conn, instance_id)
    sealed = None
    if body.mgmt_token is not None:
        _require_mgmt_shape(body.mgmt_token)
        sealed = seal.seal(request.app.state.settings.seal_key, body.mgmt_token)
    instances_repo.set_fields(
        conn,
        instance_id,
        display_name=body.display_name,
        tagline=body.tagline,
        public_url=_normalized_url(body.public_url) if body.public_url is not None else None,
        status=body.status,
        mgmt_token_sealed=sealed,
    )
    return _out(_row_or_404(conn, instance_id))


@router.post("/instances/{instance_id}/rotate-secret", response_model=RotatedSecret)
def rotate_secret(instance_id: str, conn: Conn = Depends(get_db)) -> dict:
    row = _row_or_404(conn, instance_id)
    client_secret = security.new_secret()
    instances_repo.set_secret_hash(conn, instance_id, security.hash_token(client_secret))
    return {"oidc_client_id": row["oidc_client_id"], "oidc_client_secret": client_secret}


@router.post("/grants", response_model=GrantResult, status_code=201)
def grant_membership(body: GrantIn, conn: Conn = Depends(get_db)) -> dict:
    """The CLI's `grant` (plus `users add` when the row is new) over the
    admin bearer: user rows are operator-created only, and this route IS the
    operator -- the same authority that runs the CLI, minus needing the
    database URL of a hub it can already reach over HTTP."""
    instance = instances_repo.by_slug(conn, body.slug)
    if instance is None:
        raise HTTPException(404, detail=f"no instance {body.slug!r}")
    user_id, created = grants.grant_access(
        conn,
        email=body.email,
        instance_id=instance["id"],
        role=body.role,
        display_name=body.display_name,
    )
    return {
        "user_id": user_id,
        "email": body.email.strip().lower(),
        "instance_id": instance["id"],
        "slug": instance["slug"],
        "role": body.role,
        "created_user": created,
    }


@router.post("/instances/{instance_id}/provision-user", response_model=ProvisionResult)
async def provision_user(
    instance_id: str, body: ProvisionUserIn, request: Request, conn: Conn = Depends(get_db)
):
    """Proxy to the instance's own mgmt route, through the sealed credential.

    The instance's answer passes through -- including its 404 for an unknown
    user on a non-add action, which is an answer for the admin. Only failing
    to REACH the instance is the hub's own error (502).
    """
    row = _row_or_404(conn, instance_id)
    token = seal.unseal(request.app.state.settings.seal_key, row["mgmt_token_sealed"])
    try:
        status, answer = await mgmt_client.provision_user(
            row["public_url"], token, email=body.email, role=body.role, action=body.action
        )
    except Exception as exc:
        raise HTTPException(502, detail=f"could not reach {row['slug']}: {exc}") from exc
    if status != 200:
        detail = answer.get("detail", answer) if isinstance(answer, dict) else answer
        raise HTTPException(status, detail=detail)
    return answer
