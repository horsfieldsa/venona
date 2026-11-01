"""Ingest one article, named by URL.

The only endpoint in the system that takes a URL from outside and fetches it, so
the guard is here as well as in `pipeline/ingest.py`: rejecting a bad scheme
before a run row exists means the caller gets a sentence instead of a run that
fails thirty seconds later for reasons it has to go and read.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from venona.api.schemas.ingest import IngestRequest, IngestStarted
from venona.services import run_launcher
from venona.sources import urlguard

router = APIRouter(tags=["ingest"])


@router.post(
    "/ingest",
    status_code=202,
    response_model=IngestStarted,
    # `coalesced` is set only when an identical request was already in flight.
    response_model_exclude_unset=True,
)
async def ingest(request: Request, payload: IngestRequest) -> dict[str, Any]:
    """Fetch, store and analyse the page at `url`.

    202 with a run id, never a result: the fetch plus one analyze call takes
    tens of seconds, and a request that holds a connection open for that long is
    a request that times out somewhere in the middle and leaves the caller
    unable to tell whether the work happened. Watch it on `/api/runs/{id}` or
    its SSE stream, the same as every other run.

    Research lane (C11), so this is refused with 409 while a pipeline run is in
    flight rather than queued behind it -- see `pipeline/ingest.py` for why
    those two must not write `documents` concurrently.
    """
    from venona.pipeline.ingest import IngestFailed, ingest_url

    try:
        urlguard.check_static(payload.url)
    except urlguard.BlockedUrl as exc:
        raise HTTPException(400, detail=str(exc)) from exc

    async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        try:
            report = await ingest_url(target.ctx, payload.url)
        except IngestFailed as exc:
            return run_launcher.LaunchOutcome(
                status="failed", data={"error": str(exc)}, error=str(exc)
            )

        data = {
            "url": report.url,
            "document_id": report.document_id,
            "title": report.title,
            "is_new": report.is_new,
            "analyzed": report.analyzed,
            "entities_new": report.entities_minted,
            "entities_seen": report.entities_resolved,
            "cost_usd": report.cost_usd,
            "warnings": report.warnings,
        }
        # A page that was already in the corpus is not a failure, not a clean
        # delivery, and not a DEGRADED run: nothing was added because there was
        # nothing to add. Reporting `succeeded` alone would tell the reader
        # their click did something it did not; reporting `succeeded_partial`
        # told them something broke.
        verdict = "delivered" if report.is_new and report.analyzed else "nothing_to_do"
        return run_launcher.LaunchOutcome(outcome=verdict, data=data)

    try:
        started = await run_launcher.launch(
            request,
            kind="ingest",
            params={"url": payload.url},
            work=work,
            # WITHOUT a stage, `_runner` gates `record_stage`, `stage_finished`
            # and the counter flush behind `if stage:` -- so every field `work`
            # builds above was computed and dropped, no `run_stages` row was
            # written, and `RunDetailView` (detail='single', reads `stages[0]`)
            # rendered a panel whose only fact was "grounded: no".
            stage="ingest",
        )
    except run_launcher.LaneBusy as exc:
        # C11: the research lane REFUSES, it does not queue. Without this the
        # `Exception` backstop in `api/errors.py` answers a routine refusal with
        # an opaque 500 and a stack trace, and the extension shows a bare cross
        # for what is really "wait for the nightly run".
        raise HTTPException(409, detail=str(exc)) from None
    return {**started, "url": payload.url}
