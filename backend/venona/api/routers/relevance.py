"""Personal relevance: the reader's emphasis and follows.

Every route's subject is the acting user -- the `PATCH /api/auth/me`
structural argument: no route carries a user id, so there is nothing for a
caller to substitute. Identity resolution is `learning._actor`, reused
rather than copied so the auth-off local-operator binding stays one
implementation.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from venona import timeutil
from venona.api.deps import get_db
from venona.api.routers.learning import _actor
from venona.api.schemas.relevance import (
    FollowEntry,
    FollowList,
    FollowSuggestion,
    FollowSuggestions,
    RelevanceSettings,
    RelevanceSettingsUpdate,
)
from venona.db import core as db
from venona.db.repositories import relevance as repo
from venona.services import personal_relevance as pr

router = APIRouter(prefix="/relevance", tags=["relevance"])


def _now() -> str:
    return timeutil.format_millis(datetime.now(UTC))


def _settings_payload(conn: db.Conn, user_id: str) -> RelevanceSettings:
    row = repo.settings_row(conn, user_id=user_id)
    emphasis = db.jload(row["emphasis"], {}) or {} if row else {}
    return RelevanceSettings(
        emphasis=emphasis,
        tailored=row is not None,
        drifted=bool(emphasis) and pr.is_drifted(emphasis),
    )


@router.get("/settings", response_model=RelevanceSettings)
def get_settings(request: Request, conn: db.Conn = Depends(get_db)) -> RelevanceSettings:
    return _settings_payload(conn, _actor(request, conn))


@router.patch("/settings", response_model=RelevanceSettings)
def update_settings(
    payload: RelevanceSettingsUpdate, request: Request, conn: db.Conn = Depends(get_db)
) -> RelevanceSettings:
    """Full replacement or reset -- never a merge; the tailor screen submits
    the whole map, so an absent axis means normal."""
    user_id = _actor(request, conn)
    if payload.reset:
        repo.delete_settings(conn, user_id=user_id)
        return _settings_payload(conn, user_id)
    if payload.emphasis is None:
        raise HTTPException(422, detail="say what to change: an emphasis, or reset")
    problem = pr.emphasis_problem(payload.emphasis)
    if problem is not None:
        raise HTTPException(422, detail=problem)
    repo.upsert_settings(conn, user_id=user_id, emphasis=db.jdump(payload.emphasis), now=_now())
    return _settings_payload(conn, user_id)


#: How many follows one read returns. A reader follows a handful of names; this
#: bounds the pathological case rather than paging, and `total` reports the rest.
FOLLOW_LIMIT = 200


def _follow_list(conn: db.Conn, user_id: str) -> FollowList:
    return FollowList(
        items=[
            FollowEntry(
                entity_id=row["entity_id"],
                resolved_id=row["resolved_id"],
                name=row["name"],
                type=row["type"],
                status=row["status"],
                created_at=row["created_at"],
            )
            for row in repo.follows(conn, user_id=user_id, limit=FOLLOW_LIMIT)
        ],
        total=repo.follow_count(conn, user_id=user_id),
    )


@router.get("/follows", response_model=FollowList)
def list_follows(request: Request, conn: db.Conn = Depends(get_db)) -> FollowList:
    return _follow_list(conn, _actor(request, conn))


@router.put("/follows/{entity_id}", response_model=FollowList)
def add_follow(entity_id: str, request: Request, conn: db.Conn = Depends(get_db)) -> FollowList:
    user_id = _actor(request, conn)
    if repo.entity_name(conn, entity_id=entity_id) is None:
        raise HTTPException(404, detail="Venona does not know that name")
    repo.add_follow(conn, user_id=user_id, entity_id=entity_id, now=_now())
    return _follow_list(conn, user_id)


@router.delete("/follows/{entity_id}", response_model=FollowList)
def remove_follow(entity_id: str, request: Request, conn: db.Conn = Depends(get_db)) -> FollowList:
    user_id = _actor(request, conn)
    repo.remove_follow(conn, user_id=user_id, entity_id=entity_id)
    return _follow_list(conn, user_id)


@router.get("/suggestions", response_model=FollowSuggestions)
def suggestions(
    request: Request,
    limit: int = Query(24, ge=1, le=100),
    conn: db.Conn = Depends(get_db),
) -> FollowSuggestions:
    """Names worth offering in the picker: most connected first, minus what
    the reader already follows."""
    user_id = _actor(request, conn)
    return FollowSuggestions(
        items=[
            FollowSuggestion(
                id=row["id"],
                name=row["name"],
                type=row["type"],
                mention_count=row["mention_count"] or 0,
                degree=row["degree"] or 0,
            )
            for row in repo.suggestions(conn, user_id=user_id, limit=limit)
        ]
    )
