"""First-boot provisioning: lay down the blank, loader-valid document set.

A fresh non-defense instance starts from `fixtures/skeleton/` -- not the
repo's own `config/`/`profile/`, which are the DEFENSE instance's documents,
and a new domain that starts with them impersonates it until every wizard
draft is applied. Both scaffolders (`scripts/new_local_instance.py` locally,
`venona instance seed` on a production first boot) and the skeleton tests
share this module so the file-routing rule has exactly one spelling.

This write path is legitimately outside `config_service`: it provisions a
not-yet-configured instance's files before the app exists, only ever creates
missing files, and runtime edits still route exclusively through
`config_service`.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from venona.domain import DEFENSE_DEFAULTS

#: Accepted by every scaffolder. A strict subset of the hub's own slug rule
#: (`^[a-z0-9][a-z0-9-]*$`, 1-64), so a slug minted here always registers.
SLUG_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,31}$")

#: Slugs no scaffolder may mint: the default instance owns the image-defaults
#: seeding path, and the placeholder is what `is_configured` treats as absent.
RESERVED_SLUGS = (
    DEFENSE_DEFAULTS.slug,
    "unconfigured",
)

#: Skeleton files that land in `config/`; every other skeleton file lands in
#: `profile/`.
SKELETON_CONFIG_FILES = ("ontology.yaml", "sources.yaml")

SKELETON_PROFILE_FILES = (
    "relevance_profile.md",
    "research_prompt.md",
    "brief_prompt.md",
    "scoring_rubric.yaml",
    "brief_sections.yaml",
    "lexicon.yaml",
    "anchors.yaml",
    "learning_prompt.md",
)


def placeholder_domain_yaml() -> str:
    """The descriptor a blank instance boots with. Its slug is what gates the
    app into `/setup` -- a static skeleton file cannot carry it, hence code."""
    import yaml

    from venona.services.setup_service import PLACEHOLDER_SLUG, initial_descriptor

    descriptor = initial_descriptor(
        slug=PLACEHOLDER_SLUG,
        display_name="Unconfigured",
        tagline="An unconfigured venona instance.",
        login_blurb="Venona has not been set up here yet.",
    )
    return (
        "# The placeholder descriptor. The setup wizard replaces it -- an\n"
        "# instance with this slug boots into /setup.\n"
        + yaml.safe_dump(descriptor, sort_keys=False, allow_unicode=True, width=100)
    )


def seed(
    config_dir: Path,
    profile_dir: Path,
    *,
    skeleton_dir: Path,
    settings_src: Path,
) -> list[Path]:
    """Write every missing skeleton document; never touch an existing file.

    `cp -n` semantics, so a container restart cannot clobber a document the
    wizard has since applied. Returns the paths actually written, for the
    caller's log line and for the idempotence tests.
    """
    written: list[Path] = []

    def place(target: Path, content_from: Path | None = None, text: str | None = None) -> None:
        if target.exists():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        if content_from is not None:
            shutil.copy(content_from, target)
        else:
            target.write_text(text or "")
        written.append(target)

    # settings.yaml is the one document seeded from the shipped defaults --
    # it is domain-neutral pipeline knobs, not domain content.
    place(config_dir / "settings.yaml", content_from=settings_src)
    for name in SKELETON_CONFIG_FILES:
        place(config_dir / name, content_from=skeleton_dir / name)
    for name in SKELETON_PROFILE_FILES:
        place(profile_dir / name, content_from=skeleton_dir / name)
    place(config_dir / "domain.yaml", text=placeholder_domain_yaml())
    return written
