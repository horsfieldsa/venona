"""The blank-instance skeleton, and the gates that keep "fresh" meaning fresh.

`fixtures/skeleton/` is what `new_local_instance.py` scaffolds an instance
from. Each file must load through its REAL loader -- a skeleton that fails a
loader turns "boot into the wizard" into a stack trace -- and the set must
cover every document the wizard drafts, so a new draftable document cannot
ship without deciding its blank state.
"""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from venona.ontology import parse_ontology
from venona.services import config_service, draft_service, instance_skeleton, setup_service
from venona.services.config_service import ConfigError
from venona.settings import get_settings

REPO_ROOT = Path(__file__).resolve().parents[1]
SKELETON = REPO_ROOT / "fixtures" / "skeleton"


def _sandbox(tmp_path: Path):
    """Settings whose config/profile dirs hold exactly the skeleton."""
    settings = copy.deepcopy(get_settings())
    settings.config_dir = tmp_path / "config"
    settings.profile_dir = tmp_path / "profile"
    settings.data_dir = tmp_path / "data"
    for directory in (settings.config_dir, settings.profile_dir, settings.data_dir):
        directory.mkdir(parents=True, exist_ok=True)
    for path in SKELETON.iterdir():
        target = (
            settings.config_dir
            if path.name in instance_skeleton.SKELETON_CONFIG_FILES
            else settings.profile_dir
        )
        (target / path.name).write_text(path.read_text())
    return settings


def test_the_skeleton_covers_every_editable_document() -> None:
    """A new EDITABLE document must bring its blank state along.

    Pinned against EDITABLE rather than the wizard's ALL_DOCS, because the
    gap this closes was exactly a non-wizard document: the learning prompt
    shipped registered on the Admin rail but absent from the skeleton, so a
    non-defense instance listed an editor over a file that did not exist.
    The named exceptions each have a generator instead of a static file.
    """
    files = {path.name for path in SKELETON.iterdir() if path.is_file()}
    expected = {
        meta["file"]
        for key, meta in config_service.EDITABLE.items()
        # domain's blank state is `setup_service.initial_descriptor`, written
        # by the scaffolder itself -- a static file cannot carry the slug.
        # settings ships as the repo's own config/settings.yaml defaults.
        if key not in ("domain", "settings")
    }
    assert files == expected
    # And the wizard's set stays a subset of it, so ALL_DOCS cannot grow an
    # entry the skeleton lacks either.
    wizard = {
        config_service.EDITABLE[key]["file"] for key in setup_service.ALL_DOCS if key != "domain"
    }
    assert wizard <= files


def test_every_editable_document_ships_a_default() -> None:
    """The defense image's other half of the same guarantee: every EDITABLE
    entry must have a real file in the repo's config/ or profile/, because
    `api-entrypoint.sh` seeds production disks from those defaults and an
    entry without one lists an editor over nothing."""
    for key, meta in config_service.EDITABLE.items():
        if key == "domain":
            continue
        base = REPO_ROOT / ("profile" if meta["dir"] == "profile" else "config")
        assert (base / meta["file"]).exists(), f"{key}: no default at {meta['dir']}/{meta['file']}"


def test_skeletons_pass_their_real_loaders(tmp_path: Path) -> None:
    settings = _sandbox(tmp_path)
    for key in setup_service.ALL_DOCS:
        if key in ("domain", "sources", "ontology"):
            continue
        content = (SKELETON / config_service.EDITABLE[key]["file"]).read_text()
        config_service.validate(settings, key, content)  # must not raise


def test_the_sources_skeleton_is_empty_by_design(tmp_path: Path) -> None:
    """An empty source list REFUSES to load: that is what keeps a pre-setup
    run from silently fetching nothing. The skeleton keeps that property."""
    settings = _sandbox(tmp_path)
    with pytest.raises(ConfigError, match="declares no sources"):
        config_service.validate(settings, "sources", (SKELETON / "sources.yaml").read_text())


def test_the_ontology_skeleton_is_loadable_and_structurally_empty() -> None:
    """Loadable, not editor-savable -- the save validator demands the anchor
    predicates, which is right: nobody saves a blank ontology, the wizard's
    draft (version 1+) applies over this version 0."""
    ontology = parse_ontology(yaml.safe_load((SKELETON / "ontology.yaml").read_text()))
    assert ontology.version == 0
    assert not ontology.entity_types
    assert not ontology.predicates


async def test_the_daily_never_fires_on_an_unconfigured_instance(tmp_path: Path) -> None:
    """Nothing else gates the 06:00 timer: an unconfigured instance left
    running overnight fired a doomed run before this check existed."""
    from venona import scheduler
    from venona.services.setup_service import PLACEHOLDER_SLUG, initial_descriptor

    settings = _sandbox(tmp_path)
    (settings.config_dir / "domain.yaml").write_text(
        yaml.safe_dump(
            initial_descriptor(
                slug=PLACEHOLDER_SLUG,
                display_name="Unconfigured",
                tagline="",
                login_blurb="",
            ),
            sort_keys=False,
        )
    )
    app = SimpleNamespace(state=SimpleNamespace(settings=settings))
    trigger = await scheduler.run_daily(app)
    assert trigger.outcome == "skipped"
    assert "not set up" in (trigger.reason or "")


# ── draft verdicts move with the documents around them ────────────────

STAGED_ONTOLOGY = """\
version: 1
entity_types:
  Model:
    prefix: model
    color_slot: 1
    description: a model
predicates:
  PRODUCES:
    domain: [Model]
    range: [Model]
    description: test predicate
"""

#: Valid under STAGED_ONTOLOGY's `model:` prefix; ill-typed under the live
#: (defense) ontology, which has no such prefix -- the exact shape the toydom
#: instance hit with 'model:gpt-5-6'.
STAGED_ANCHORS = """\
entities:
  - id: model:gpt-x
    type: Model
    name: GPT X
edges: []
"""


def test_an_anchor_draft_is_judged_against_the_staged_ontology(tmp_path: Path) -> None:
    settings = _sandbox(tmp_path)
    draft_service.store_draft(
        settings, "ontology", STAGED_ONTOLOGY, run_id="run:t", task="t", generated_at="now"
    )
    meta = draft_service.store_draft(
        settings, "anchors", STAGED_ANCHORS, run_id="run:t", task="t", generated_at="now"
    )
    assert meta.valid, meta.error


def test_a_draft_verdict_refreshes_when_its_surroundings_change(tmp_path: Path) -> None:
    """The frozen verdict was the bug: it kept reporting a dead error after
    the ontology applied, while masking the draft's real defects behind it."""
    settings = _sandbox(tmp_path)
    meta = draft_service.store_draft(
        settings, "anchors", STAGED_ANCHORS, run_id="run:t", task="t", generated_at="now"
    )
    # No staged ontology and a skeleton live one: 'model:' is unknown.
    assert not meta.valid
    assert "C4" in (meta.error or "")

    draft_service.store_draft(
        settings, "ontology", STAGED_ONTOLOGY, run_id="run:t", task="t", generated_at="now"
    )
    _content, refreshed = draft_service.read_draft(settings, "anchors")
    assert refreshed.valid, refreshed.error
    # And the sidecar was rewritten, so a listing agrees with the read.
    rows = {row["key"]: row for row in draft_service.list_drafts(settings)}
    assert rows["anchors"]["valid"] is True
