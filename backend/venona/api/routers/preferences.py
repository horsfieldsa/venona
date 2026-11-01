"""How a reader wants things shown to them.

WRITE ONLY, deliberately. The stored zone reaches the client on `SessionInfo`,
because it is needed for the FIRST PAINT of the brief and the session probe is
the one request the SPA already makes before mount (`web/src/main.ts`). A `GET`
here would be a second blocking round trip in front of the page to answer a
question the session has already answered.

THE ROUTE CARRIES NO USER ID -- the `PATCH /api/auth/me` structural argument.
The subject is always the acting user, so there is nothing for a caller to
substitute.

Identity is `learning._actor` and NOT `authz.actor_id`, and the difference is
load-bearing: `actor_id` returns None for `via="disabled"`, which is how this
app runs under `make dev`, so an operator setting their own clock locally would
get a 400 from a feature that has no reason to refuse them. `_actor` binds the
well-known local-operator row instead.

`_session_info` resolves the same person through its own `subject` local, and
the two MUST agree -- both read `principal.user_id`, both fall back to
`user:local-operator` with auth off. Should they ever diverge, the symptom is a
reader saving a zone here and the next page load handing back a stale one.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request

from venona import timeutil
from venona.api.deps import get_db
from venona.api.routers.learning import _actor
from venona.api.schemas.preferences import Preferences, PreferencesUpdate
from venona.db import core as db
from venona.services import preferences_service

router = APIRouter(prefix="/preferences", tags=["preferences"])


def _now() -> str:
    return timeutil.format_millis(datetime.now(UTC))


@router.patch("", response_model=Preferences)
def update_preferences(
    payload: PreferencesUpdate, request: Request, conn: db.Conn = Depends(get_db)
) -> Preferences:
    user_id = _actor(request, conn, what="keep display preferences")
    with db.transaction(conn, what="set_timezone"):
        try:
            stored = preferences_service.set_timezone(
                conn, user_id=user_id, timezone=payload.timezone, now=_now()
            )
        except preferences_service.PreferenceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Return what was STORED, not what was asked for: blank normalises to null,
    # so echoing the payload would show a value the database does not hold.
    return Preferences(timezone=stored)
