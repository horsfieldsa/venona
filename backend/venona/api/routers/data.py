"""Data management. One read, one irreversible write.

The write is the only endpoint in the application that destroys data the user
cannot get back by any means -- a merge is reversible through `entity_mappings`,
a hidden run is still there, a rebuilt graph re-projects from Postgres. This does
not come back. Three things guard it, and all three are deliberate:

  authentication  it is under `/api`, so `AuthMiddleware` default-denies it.
                  It is NOT in `EXEMPT_PATHS` and must never be.
  a phrase        `confirm` has to be the exact words, so a mis-aimed curl or a
                  replayed request body cannot do it.
  a lane check    a purge while a run is writing is a corrupt half-state: the
                  run holds entities and assertions in flight and will insert
                  them after the delete, leaving rows with no documents behind
                  them. `409` and say which run.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse

from venona import notify
from venona.api.deps import Window, get_db, page_window
from venona.api.schemas.data import DataExport, DataExportList, DataStats, PurgeRequest, PurgeResult
from venona.api.schemas.run import RunStarted
from venona.db import core as db
from venona.db.repositories import curation as repo
from venona.db.repositories import data_export as export_repo
from venona.db.repositories import users as users_repo
from venona.export import archive
from venona.services import purge as purge_service
from venona.services import run_launcher, run_service, run_types

log = logging.getLogger("venona.data")

router = APIRouter(prefix="/data", tags=["data"])


@router.get("/stats", response_model=DataStats)
def data_stats(conn: db.Conn = Depends(get_db)) -> DataStats:
    """Row counts for everything a purge would delete.

    Read from the tables rather than from a cached summary so the number in the
    confirmation dialog is the number that will actually be deleted a second
    later.
    """
    counts = repo.row_counts(conn, list(purge_service.CORPUS_TABLES))

    return DataStats(
        counts=counts,
        total=sum(counts.values()),
        preserved=list(purge_service.PRESERVED_TABLES),
    )


@router.post("/purge", response_model=PurgeResult)
async def purge(
    payload: PurgeRequest,
    request: Request,
    conn: db.Conn = Depends(get_db),
) -> PurgeResult:
    """Delete the corpus. Irreversible.

    `payload` is unused past validation and that is the point: the model's only
    job is to refuse a request that did not type the phrase.
    """
    _ = payload

    # C11: ask the LANE, never a global "is anything running". A light lookup is
    # not a reason to refuse, but a research run is -- it is the one that writes
    # entities and assertions.
    active = run_service.registry.active_run_id(run_types.RESEARCH)
    if active:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "run_active",
                "message": f"{active} is running; wait for it to finish, or cancel it, then delete",
            },
        )

    # AND an export, for the opposite reason. An export reads the whole corpus
    # under one snapshot, so a purge landing mid-read produces a file that is a
    # complete copy of data the operator has just deleted -- from a screen where
    # the two buttons are a centimetre apart. Asked of the KIND and not of the
    # lane: `active_run_id(LIGHT)` answers for a definition lookup too, and
    # refusing a purge because a reader hovered a term is the global-slot
    # damage C11 exists to end.
    exporting = _running_export()
    if exporting:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "export_active",
                "message": "An export is being written; wait for it to finish, then delete",
            },
        )

    deleted = purge_service.purge_corpus(conn)
    total = sum(deleted.values())
    log.warning("corpus purged: %d rows across %d tables", total, len(deleted))

    # The graph LAST, and never inside the database transaction. Invariant 1: it
    # is a rebuildable projection, so an empty database with a stale graph is
    # recoverable (`venona graph rebuild` produces an empty graph) while a
    # cleared graph with a failed delete is a projection of rows that still
    # exist. Postgres is what has to be right.
    graph_cleared = False
    graph = getattr(request.app.state, "graph", None)
    if graph is not None:
        try:
            async with graph.driver.session() as session:
                await session.run("MATCH (n) DETACH DELETE n")
            graph_cleared = True
        except Exception:  # noqa: BLE001 - the purge succeeded; this is cleanup
            # Deliberately swallowed and REPORTED rather than raised. The
            # destructive half already committed, so failing the request here
            # would tell the operator the purge did not happen when it did.
            log.exception("corpus purged, but the graph could not be cleared")

    return PurgeResult(deleted=deleted, total=total, graph_cleared=graph_cleared)


def _running_export() -> str | None:
    """The export in flight, if there is one. By KIND, never by lane."""
    for handle in run_service.registry.active(run_types.LIGHT):
        if handle.kind == "export":
            return handle.run_id
    return None


def _row_to_export(row: db.Row, conn: db.Conn) -> DataExport:
    """One stored archive, in the words a screen can render.

    `download_url` is filled in ONLY for a file the machine still holds, so the
    client never decides whether a control is live. Never joins `runs`: that
    table is purged and this one is not, so after a delete every `run_id` here
    names a row that is gone.
    """
    # READY MEANS THE BYTES ARE HERE (ADR 0234), asked with `content_len` so a
    # listing of twenty rows does not pull twenty archives through the pool. The
    # file check is for rows that still name a `path`.
    held = bool(row["content_len"]) or bool(row["path"] and Path(row["path"]).exists())
    ready = row["status"] == export_repo.READY and held
    who = users_repo.by_id(conn, row["created_by"]) if row["created_by"] else None
    return DataExport(
        id=row["id"],
        filename=row["filename"],
        bytes=int(row["bytes"] or 0),
        sha256=row["sha256"] or "",
        counts=db.jload(row["counts"] or "{}"),
        status=export_repo.READY if ready else (row["status"] or export_repo.MISSING),
        created_at=row["created_at"] or "",
        created_by=(who["display_name"] or who["email"]) if who else None,
        download_url=f"/api/data/exports/{row['id']}/download" if ready else None,
    )


@router.get("/exports", response_model=DataExportList)
def list_exports(
    window: Window = Depends(page_window(default=archive.KEEP * 4, cap=200)),
    conn: db.Conn = Depends(get_db),
) -> DataExportList:
    """Every archive this machine has written, newest first.

    A pruned one still appears. "There was one and it is gone" is a different
    fact from "there was never one", and only the first teaches the reader that
    older files are removed -- which is also why the row count is not bounded by
    the retention rule and needs a window of its own.
    """
    rows = export_repo.listing(conn, limit=window.limit, offset=window.offset)
    return DataExportList(
        items=[_row_to_export(row, conn) for row in rows],
        keep=archive.KEEP,
        total=export_repo.listing_count(conn),
    )


@router.post(
    "/exports", status_code=202, response_model=RunStarted, response_model_exclude_unset=True
)
async def start_export(request: Request) -> Any:
    """Write everything Venona has read as a folder of linked notes.

    No body and no options: every export carries everything, so two of them are
    comparable and there is nothing to get wrong.

    NO REFUSAL WHEN ONE IS ALREADY GOING, deliberately. The light lane hands an
    identical request the run that already exists, so a second press attaches
    to the first rather than writing a second identical file -- which is the
    same answer, arrived at without a second way to be wrong.
    """
    from venona.api import authz
    from venona.export.service import run_export

    actor = authz.actor_id(request)
    settings = request.app.state.settings

    async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        # BOTH ENDINGS ARE ANNOUNCED, because the shape of this run is that
        # somebody presses it and walks away for several minutes. A toast
        # reaches them only while the tab is open; the notice reaches wherever
        # they said to reach them. `raise_notice` never raises, so neither
        # branch can turn "we could not tell you" into a failed export.
        #
        # THIS KIND REPORTS ITS OWN FAILURE, so a generic per-run failure notice
        # must not report it a second time. An operator whose export ran out of
        # disk should get one message naming the copy, not that one plus "a run
        # did not finish" -- and the second would be the less useful of the two.
        # If a generic path is added, exclude this kind at the declaration
        # rather than here, so the exclusion is visible beside the run type.
        try:
            result = await run_export(target.ctx, conn=target.conn, settings=settings, actor=actor)
        except Exception as exc:
            notify.raise_notice(
                target.conn,
                "export.finished",
                run_id=target.run_id,
                failed=True,
                error=str(exc),
            )
            raise
        data: dict[str, Any] = {"export_name": result.filename, "size": result.size_bytes}
        data.update(result.counts)
        notify.raise_notice(
            target.conn,
            "export.finished",
            run_id=target.run_id,
            filename=result.filename,
            notes=result.counts.get("notes"),
        )
        # `nothing_to_do` is a corpus with nothing in it yet, which is a real
        # answer rather than a run that went wrong. Anything written is
        # `delivered`; there is no half of this to report.
        outcome = "delivered" if result.counts.get("notes") else "nothing_to_do"
        return run_launcher.LaunchOutcome(outcome=outcome, data=data)

    return await run_launcher.launch(
        request,
        kind="export",
        params={"format": "obsidian"},
        work=work,
        stage="export",
    )


@router.get("/exports/{export_id}/download")
def download_export(export_id: str, conn: db.Conn = Depends(get_db)) -> Response:
    """The archive itself.

    THE FIRST FILE RESPONSE IN THIS APPLICATION, which is why it is on
    `tests/test_route_contracts.py`'s exempt list: the bytes ARE the response
    and a model would serialise them as one value. `DataExport` models the row
    it is served from, and the listing carries it.

    410 RATHER THAN 404 for a file that has gone. The row exists and says what
    happened to it; a 404 reads as a wrong address and sends somebody looking
    for a typo they did not make.
    """
    row = export_repo.get(conn, export_id)
    if row is None:
        raise HTTPException(status_code=404, detail={"error": "no_export"})
    payload = export_repo.content(conn, export_id) if row["content_len"] else None
    path = Path(row["path"]) if row["path"] else None
    if row["status"] != export_repo.READY or (payload is None and not (path and path.exists())):
        if row["status"] == export_repo.READY:
            # Looked, and it was not there. Recorded so the next reader is not
            # told the same thing twice, and is not told retention did it.
            export_repo.mark_missing(conn, export_id)
            conn.commit()
        raise HTTPException(
            status_code=410,
            detail={
                "error": "export_gone",
                "message": "That file is no longer kept. Export again to make a new one.",
            },
        )
    # `filename=` rather than a hand-written header: Starlette percent-encodes
    # and picks the quoted or extended form, and getting that wrong by hand is
    # how a name with a space arrives truncated.
    disposition = {"content-disposition": f'attachment; filename="{_header_safe(row["filename"])}"'}
    if payload is not None:
        return Response(payload, media_type="application/zip", headers=disposition)
    # A row naming a file this instance can still read. Kept rather than
    # migrated: copying it into the database to serve one download buys nobody
    # anything.
    return FileResponse(path, media_type="application/zip", filename=_header_safe(row["filename"]))


def _header_safe(filename: str) -> str:
    """A stored value about to become a response header, bounded where it lands.

    The name is built from a slug and a date and cannot be anything else, but
    `quote()` leaves a slash alone by default -- so the guarantee is kept local
    rather than left as a fact about a column three files away.
    """
    import re

    return filename if re.fullmatch(r"[A-Za-z0-9._-]{1,120}", filename) else "export.zip"
