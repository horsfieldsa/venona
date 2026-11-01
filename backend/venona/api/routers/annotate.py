"""Annotate text venona did not write.

Guarded like everything else under `/api`: `AuthMiddleware` is default-deny by
prefix and this path is not in `EXEMPT_PATHS`, so the route needed no work to be
protected -- which is exactly the property C12 chose default-deny for.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from venona.api.deps import get_db
from venona.api.schemas.annotate import AnnotateBlockOut, AnnotateRequest, AnnotateResponse
from venona.db import core as db
from venona.services import annotate_service

router = APIRouter(tags=["annotate"])


@router.post(
    "/annotate",
    response_model=AnnotateResponse,
    # `TextRun` has nine optional fields and forbids extras. A 400-block page
    # would otherwise ship several thousand `null`s for marks that a plain
    # paragraph does not have.
    response_model_exclude_none=True,
)
def annotate(payload: AnnotateRequest, conn: db.Conn = Depends(get_db)) -> AnnotateResponse:
    """Resolve entity and glossary spans in arbitrary text.

    Sync `def` on purpose: `build_index` is blocking CPU work, and FastAPI runs
    a sync route on the threadpool where it cannot stall the event loop -- and
    with it the `/api/health` the reader polls every three seconds.

    Reads only. This endpoint writes nothing and keeps nothing; see
    `services/annotate_service.py`.
    """
    annotated, refs = annotate_service.annotate_texts(
        conn, [(block.id, block.text, block.hrefs) for block in payload.blocks]
    )
    return AnnotateResponse(
        blocks=[AnnotateBlockOut(id=block_id, runs=runs) for block_id, runs in annotated],
        refs=refs,
        index_version=annotate_service.index_version(conn),
    )
