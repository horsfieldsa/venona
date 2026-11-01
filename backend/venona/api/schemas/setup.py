"""Wire models for the domain-setup wizard."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SetupState(BaseModel):
    """What the wizard renders from: is this instance configured, what has
    been generated, and is a setup run already in flight."""

    configured: bool
    slug: str
    display_name: str
    has_dossier: bool
    drafts: list[str] = Field(default_factory=list)
    active_run: str | None = None


class DescriptorIn(BaseModel):
    """Step 1 of the wizard. Everything else the generation run derives."""

    slug: str = Field(min_length=2, max_length=32)
    display_name: str = Field(min_length=2, max_length=120)
    tagline: str = Field(min_length=2, max_length=400)
    login_blurb: str = Field(min_length=2, max_length=400)


class DescriptorSaved(BaseModel):
    slug: str
    configured: bool


class GenerateIn(BaseModel):
    """Step 2. `docs` scopes a re-run to a subset; absent means everything.
    The persona/priorities/seeds ride into the run's params so the runs table
    says what a generation was generated FROM."""

    docs: list[str] | None = None
    refresh_survey: bool = False
    persona: str = Field(default="", max_length=4000)
    priorities: str = Field(default="", max_length=2000)
    seeds: str = Field(default="", max_length=2000)


class GenerateStarted(BaseModel):
    run_id: str
