"""Transport over `services/config_service.py`. Nothing here decides anything.

Route ordering matters: `/config/assembled` is declared BEFORE `/config/{key}`,
or the path parameter swallows it and the assembled list 404s as an unknown
document.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from venona.api.deps import config_audit, get_db
from venona.api.schemas.config import (
    AnchorsApplied,
    AssembledDocOut,
    ConfigDocOut,
    ConfigHistory,
    ConfigList,
    ConfigSaved,
    ConfigSnapshotOut,
    DraftDiscarded,
    DraftList,
    DraftOut,
    OntologyRejections,
)
from venona.db import core as db
from venona.services import config_service, draft_service, metrics_service

log = logging.getLogger("venona.api.config")

router = APIRouter(prefix="/config", tags=["config"])


class SaveDoc(BaseModel):
    content: str
    base_version: str | None = None


@router.get(
    "",
    response_model=ConfigList,
    # The list mixes editable documents with assembled prompts, and only the
    # editable ones carry `rescore_hint`/`version`/`bytes`/`updated_at`. Without
    # this, all four appear as nulls on all seven assembled entries.
    response_model_exclude_unset=True,
)
def list_config(request: Request) -> dict[str, Any]:
    """Editable documents and read-only assembled prompts, in one list.

    One request rather than two: the index renders them together, and a second
    round trip would only let it render half of itself first.
    """
    settings = request.app.state.settings
    editable = [
        {**doc, "has_draft": draft_service.has_draft(settings, doc["key"])}
        for doc in config_service.list_docs(settings)
    ]
    return {
        "items": editable + config_service.assembled_list(),
        "groups": config_service.groups(),
    }


# ── assembled prompts (read-only) ────────────────────────────────────


@router.get("/assembled", response_model=ConfigList)
def list_assembled() -> dict[str, Any]:
    return {"items": config_service.assembled_list()}


@router.get("/assembled/{key}", response_model=AssembledDocOut)
def get_assembled(key: str, conn: db.Conn = Depends(get_db)) -> dict[str, Any]:
    try:
        return config_service.assembled_doc(key, conn)
    except config_service.ConfigError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


# ── anchors (the one document whose save is not enough) ──────────────


@router.post("/anchors/apply", response_model=AnchorsApplied)
def apply_anchors(
    request: Request,
    dry_run: bool = False,
    apply_merges: bool = False,
    conn: db.Conn = Depends(get_db),
) -> dict[str, Any]:
    """Load `profile/anchors.yaml` into Postgres.

    Saving the document does nothing on its own -- nothing in a run re-reads it
    -- so the editor ships this alongside. Destructive: it deletes and rewrites
    the anchor run's assertions, which is why the UI puts a confirm in front of
    it and calls `dry_run` first for the preview.
    """
    import yaml

    from venona.services.seed import load_anchors, validate_anchors

    settings = request.app.state.settings
    path = settings.profile_dir / "anchors.yaml"

    try:
        if dry_run:
            # NOT `load_anchors(dry_run=True)`: that prints its preview to
            # stdout and returns a zeroed counter, so the dialog would offer
            # "0 entities · 0 edges" and read as an empty file. `validate_anchors`
            # returns the real shape and touches no database.
            counts = {
                "entities": 0,
                "aliases": 0,
                "edges": 0,
                "merges": 0,
                "skipped": 0,
                **validate_anchors(yaml.safe_load(path.read_text()), origin=str(path)),
            }
        else:
            counts = load_anchors(conn, settings.profile_dir, apply_merges=apply_merges, quiet=True)
    except FileNotFoundError as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc

    return {
        **counts,
        "dry_run": dry_run,
        # Neo4j is a projection; anchors that exist only in Postgres are not in
        # the graph yet. `cli.py` prints the same follow-up.
        "rebuild_required": not dry_run,
    }


# ── editable documents ───────────────────────────────────────────────


# ── drafts (generated documents awaiting review) ─────────────────────
#
# Declared before `/{key}` for the same reason `/assembled` is: the path
# parameter would swallow "drafts" and report it as an unknown document.


@router.get("/drafts", response_model=DraftList)
def list_drafts(request: Request) -> dict[str, Any]:
    return {"items": draft_service.list_drafts(request.app.state.settings)}


@router.get("/ontology/rejections", response_model=OntologyRejections)
def ontology_rejections(conn: db.Conn = Depends(get_db)) -> dict[str, Any]:
    """The shapes runs keep asserting that the current ontology refuses.

    Rendered beside the ontology editor: a rewrite can never widen a contract
    (its results must be well-typed), so these are exactly the rejections only
    a human edit can absorb -- and the evidence for that edit otherwise dies
    as run-warning scroll.
    """
    return {"shapes": metrics_service.ontology_rejections(conn)}


@router.get("/{key}/draft", response_model=DraftOut)
def get_draft(key: str, request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    try:
        content, meta = draft_service.read_draft(settings, key)
    except config_service.ConfigError as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    return {
        **asdict(meta),
        "title": config_service.EDITABLE[key]["title"],
        "bytes": len(content.encode(config_service.ENCODING)),
        "content": content,
    }


@router.post("/{key}/draft/apply", response_model=ConfigSaved)
def apply_draft(key: str, request: Request, conn: db.Conn = Depends(get_db)) -> dict[str, Any]:
    """The draft through the one write path; the draft clears only on success.

    No `base_version`: the reviewer just read the draft against the current
    file in the editor, and the draft itself is the thing being consented to.
    A validator refusal comes back 400 with the draft still staged.

    Audited like any other save, and the author recorded is the person who
    clicked apply. A draft is model-generated, so this is the write where "who
    consented" and "who composed" differ -- the row records the one that can be
    held to it.
    """
    settings = request.app.state.settings
    try:
        result = draft_service.apply_draft(settings, key, audit=config_audit(request, conn))
    except config_service.ConfigError as exc:
        raise HTTPException(400, detail=str(exc)) from exc

    return {
        **asdict(result.doc),
        "readonly": False,
        "restart_required": _settings_saved(request, key),
        "history": config_service.history(settings, key),
    }


@router.delete("/{key}/draft", response_model=DraftDiscarded)
def discard_draft(key: str, request: Request) -> dict[str, Any]:
    try:
        draft_service.discard_draft(request.app.state.settings, key)
    except config_service.ConfigError as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    return {"key": key}


@router.get("/{key}", response_model=ConfigDocOut)
def get_config(key: str, request: Request) -> dict[str, Any]:
    try:
        doc = config_service.read_doc(request.app.state.settings, key)
    except config_service.ConfigError as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    return {**asdict(doc), "readonly": False}


@router.put("/{key}", response_model=ConfigSaved)
def save_config(
    key: str, payload: SaveDoc, request: Request, conn: db.Conn = Depends(get_db)
) -> dict[str, Any]:
    """Save, after validating through the same loader startup uses.

    A rubric whose weights do not sum to 1.0, a source with an unknown `kind`,
    or an ontology that drops a predicate the anchor spine needs are all
    rejected here rather than at the end of a run that has already spent money.
    """
    settings = request.app.state.settings
    try:
        result = config_service.write_doc(
            settings,
            key,
            payload.content,
            base_version=payload.base_version,
            audit=config_audit(request, conn),
        )
    except config_service.ConfigError as exc:
        if str(exc) == "CONFLICT":
            current = config_service.read_doc(settings, key)
            raise HTTPException(
                409,
                detail={
                    "message": "the file changed on disk since you loaded it",
                    "current_version": current.version,
                    "current_content": current.content,
                },
            ) from exc
        raise HTTPException(400, detail=str(exc)) from exc

    return {
        **asdict(result.doc),
        "readonly": False,
        "restart_required": _settings_saved(request, key),
        "history": config_service.history(settings, key),
    }


def switch_is_safe(request: Request) -> str | None:
    """Whether the model service may be changed right now, and why not if it cannot.

    Rebuilding the executors under a live run leaves that run holding the
    semaphore, budget and circuit breaker it started with, while new work uses
    a fresh pair --
    so the configured concurrency is silently doubled for the overlap, and an
    open breaker resets to closed mid-outage.

    The registry alone is not enough, and `runs.py` already knows it: it is
    in-process, and `venona run` is a different pid. Queued runs count too,
    because a queued light-lane run is admitted minutes later onto whichever
    executor is bound by then.
    """
    from venona.services import run_service

    if run_service.registry.active() or run_service.registry.queued():
        return "a run is in flight"
    return None


def rebuild_executors(request: Request, settings: Any) -> None:
    """Re-point both lanes at the configured service.

    Rebinding `app.state.settings` does not reach `build_executor`, which is
    called once in `lifespan` -- so without this a saved change to the service
    or the plan takes effect on the next process start, and the route that
    wrote it returns 200 having changed nothing anybody can see.
    """
    from venona.llm.executor import build_executor

    request.app.state.llm = build_executor(settings)
    request.app.state.llm_light = build_executor(
        settings,
        concurrency=settings.llm.light_concurrency,
        outage_wait_seconds=0,
    )


def _settings_saved(request: Request, key: str) -> list[str]:
    """The half of a `settings` save that lives outside the file.

    `app.state.settings` is bound once in `lifespan` and every router reads it
    from there, so clearing the lru_cache is only half the job. Runs for every
    path that lands a document -- the editor's PUT and a draft apply alike.

    RETURNS WHAT THE SAVE DID NOT REACH, in the reader's words, which is what
    `restart_required` carries to the editor. Each of the three conditions
    below leaves the file holding a value the running process is not using, and
    a save that reports only "saved" in that state is the defect ADR 0081
    records: a setting that reads as applied and is not.
    """
    if key != "settings":
        return []
    from venona.settings import get_settings

    settings = get_settings()
    request.app.state.settings = settings
    # The CLI probe is cached for a minute, so without this `/api/health`
    # keeps reporting the superseded binary and pin after a save that changed
    # them -- which reads as the save not having taken.
    for executor in (request.app.state.llm, getattr(request.app.state, "llm_light", None)):
        if hasattr(executor, "invalidate_health"):
            executor.invalidate_health()
    # GUARDED, like the switch on the model page. Rebinding under a live run
    # leaves it holding the semaphore, budget and breaker it started with while
    # new work gets a fresh pair -- so the configured concurrency silently
    # doubles for the overlap and an open breaker resets mid-outage. A save
    # that cannot rebuild says so rather than doing the damage; the values take
    # effect on the next start, which is what `restart_required` reports.
    deferred: list[str] = []
    blocked = switch_is_safe(request)
    if blocked is None:
        try:
            rebuild_executors(request, settings)
        except Exception:  # noqa: BLE001 - the file is already written and valid
            log.exception("executors not rebuilt; they take effect on restart")
            deferred.append("the model services could not be rebuilt")
    else:
        log.info("executors not rebuilt (%s); they take effect on restart", blocked)
        deferred.append(f"{blocked}, so the model services keep the settings it started with")
    # Re-point the daily job at the saved schedule. Best-effort by design:
    # the file is written and validated by now, so a scheduler hiccup must
    # degrade to a log line, not fail a save that already happened.
    from venona.scheduler import apply_schedule

    try:
        apply_schedule(request.app)
    except Exception:  # noqa: BLE001
        log.exception("schedule not applied; it takes effect on restart")
        deferred.append("the daily reading could not be re-pointed at the new time")

    return deferred


@router.get("/{key}/history", response_model=ConfigHistory)
def get_history(key: str, request: Request) -> dict[str, Any]:
    try:
        return {"items": config_service.history(request.app.state.settings, key)}
    except config_service.ConfigError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


# Three segments, so it cannot collide with `/{key}` however the file is
# ordered. It sits here because it reads the same directory `get_history` lists,
# and the two have to agree about what is in it.
@router.get("/{key}/history/{name}", response_model=ConfigSnapshotOut)
def get_snapshot(key: str, name: str, request: Request) -> dict[str, Any]:
    """One saved version of a document, in full.

    `name` reaches the filesystem, which is why `config_service.snapshot`
    accepts only a name `history()` just listed. See C12.
    """
    try:
        return config_service.snapshot(request.app.state.settings, key, name)
    except config_service.ConfigError as exc:
        raise HTTPException(404, detail=str(exc)) from exc
