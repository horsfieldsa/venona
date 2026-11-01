"""The public directory, and the healthcheck."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from hub.db.core import Conn
from hub.deps import get_db
from hub.repositories import instances as instances_repo
from hub.schemas.public import Health, PublicInstance, PublicInstances

router = APIRouter(tags=["public"])


@router.get("/public/instances", response_model=PublicInstances)
def public_instances(conn: Conn = Depends(get_db)) -> dict:
    return {
        "instances": [
            PublicInstance(
                slug=row["slug"],
                display_name=row["display_name"],
                tagline=row["tagline"],
                public_url=row["public_url"],
                status=row["status"],
            )
            for row in instances_repo.list_public(conn)
        ]
    }


@router.get("/healthz", response_model=Health)
def healthz() -> dict:
    """Liveness only -- no database probe, so the compose healthcheck reports
    "the process serves" and Postgres being down shows as 503s, not a
    restart loop."""
    return {"ok": True}
