"""The domain-setup wizard's transport.

Three endpoints: what state the wizard is in, write the initial descriptor,
and start the generation run. The run itself is `services/setup_service.py`;
the descriptor write goes through `config_service.write_doc` like every other
config document; the launch goes through `run_launcher` like every other run.
Nothing here has a second opinion about any of that.
"""

from __future__ import annotations

import logging
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request

from venona.api.deps import config_audit, get_db
from venona.api.routers.runs import _refuse_if_research_is_busy
from venona.api.schemas.setup import (
    DescriptorIn,
    DescriptorSaved,
    GenerateIn,
    GenerateStarted,
    SetupState,
)
from venona.db import core as db
from venona.domain import SLUG, get_domain
from venona.services import (
    config_service,
    draft_service,
    run_launcher,
    run_service,
    run_types,
    setup_service,
)

log = logging.getLogger("venona.api.setup")

router = APIRouter(prefix="/setup", tags=["setup"])


@router.get("", response_model=SetupState)
def setup_state(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    descriptor = get_domain()
    return {
        "configured": setup_service.is_configured(settings),
        "slug": descriptor.slug,
        "display_name": descriptor.display_name,
        "has_dossier": setup_service.load_dossier(settings) is not None,
        "drafts": [row["key"] for row in draft_service.list_drafts(settings)],
        "active_run": run_service.registry.active_run_id(run_types.RESEARCH),
    }


@router.post("/descriptor", response_model=DescriptorSaved)
def save_descriptor(
    payload: DescriptorIn, request: Request, conn: db.Conn = Depends(get_db)
) -> dict[str, Any]:
    """Step 1: the initial descriptor, derived phrases and neutral fragments.

    The generation run drafts the refined version; this write is what turns
    the first-boot gate off, because from here the instance knows who it is.
    """
    settings = request.app.state.settings
    if not SLUG.fullmatch(payload.slug):
        raise HTTPException(
            400,
            detail="the short name must start with a lowercase letter and may use "
            "lowercase letters, digits and hyphens (2 to 32 characters)",
        )

    raw = setup_service.initial_descriptor(
        slug=payload.slug,
        display_name=payload.display_name,
        tagline=payload.tagline,
        login_blurb=payload.login_blurb,
    )
    content = (
        "# The domain descriptor -- written by the setup wizard; the generation\n"
        "# run drafts a refined version for review.\n"
        + yaml.safe_dump(raw, sort_keys=False, allow_unicode=True, width=100)
    )
    try:
        config_service.write_doc(settings, "domain", content, audit=config_audit(request, conn))
    except config_service.ConfigError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    return {"slug": payload.slug, "configured": setup_service.is_configured(settings)}


@router.post("/generate", status_code=202, response_model=GenerateStarted)
async def generate(payload: GenerateIn, request: Request) -> dict[str, Any]:
    """Step 2: the research-lane generation run. 409 while the lane is busy."""
    settings = request.app.state.settings

    unknown = set(payload.docs or []) - set(setup_service.ALL_DOCS)
    if unknown:
        raise HTTPException(
            400,
            detail=f"unknown document(s) {', '.join(sorted(unknown))}; "
            f"known documents: {', '.join(setup_service.ALL_DOCS)}",
        )

    _refuse_if_research_is_busy(settings)

    params: dict[str, Any] = {
        "docs": payload.docs,
        "refresh_survey": payload.refresh_survey,
        "persona": payload.persona,
        "priorities": payload.priorities,
        "seeds": payload.seeds,
        "domain": get_domain().slug,
    }

    async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        return await setup_service.run_domain_setup(target, settings, params)

    try:
        started = await run_launcher.launch(request, kind="domain_setup", params=params, work=work)
    except run_launcher.LaneBusy as exc:
        raise HTTPException(409, detail=f"run {exc} is already active") from exc
    return {"run_id": started["run_id"]}
