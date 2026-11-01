"""Drafts: staged generated documents, applied only through the one write path."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from venona.services import config_service as cs
from venona.services import draft_service as ds
from venona.settings import get_settings


@pytest.fixture
def settings(tmp_path: Path):
    real = get_settings()
    scratch = copy.deepcopy(real)
    scratch.profile_dir = tmp_path / "profile"
    scratch.config_dir = tmp_path / "config"
    scratch.profile_dir.mkdir()
    scratch.config_dir.mkdir()
    for meta in cs.EDITABLE.values():
        if meta["dir"] == "ontology":
            continue
        source = (real.profile_dir if meta["dir"] == "profile" else real.config_dir) / meta["file"]
        target = (scratch.profile_dir if meta["dir"] == "profile" else scratch.config_dir) / meta[
            "file"
        ]
        if source.exists():
            target.write_text(source.read_text())
    return scratch


PROVENANCE = {
    "run_id": "run:test-0001",
    "task": "setup_brief_prompt",
    "generated_at": "2026-08-18T00:00:00Z",
}


def test_a_valid_draft_stores_lists_and_applies(settings) -> None:
    content = "New voice.\n\nWrite the brief plainly.\n"
    meta = ds.store_draft(settings, "brief_prompt", content, **PROVENANCE)
    assert meta.valid and meta.error is None

    rows = ds.list_drafts(settings)
    assert [r["key"] for r in rows] == ["brief_prompt"]
    assert rows[0]["run_id"] == "run:test-0001"

    result = ds.apply_draft(settings, "brief_prompt")
    assert result.doc.key == "brief_prompt"
    assert (settings.profile_dir / "brief_prompt.md").read_text() == content
    # applied -> cleared, and the apply snapshotted history like any save
    assert not ds.has_draft(settings, "brief_prompt")
    assert list((settings.profile_dir / ".history").glob("brief_prompt.*.bak"))


def test_an_invalid_draft_is_kept_and_flagged_and_refuses_to_apply(settings) -> None:
    broken = "axes: []\n"  # rubric with no axes fails its validator
    meta = ds.store_draft(settings, "scoring_rubric", broken, **PROVENANCE)
    assert not meta.valid
    assert meta.error

    with pytest.raises(cs.ConfigError):
        ds.apply_draft(settings, "scoring_rubric")
    # a failed apply keeps the draft for another round
    assert ds.has_draft(settings, "scoring_rubric")


def test_unknown_keys_and_traversal_are_refused(settings) -> None:
    with pytest.raises(cs.ConfigError):
        ds.store_draft(settings, "not_a_doc", "x", **PROVENANCE)
    with pytest.raises(cs.ConfigError):
        ds.store_draft(settings, "../evil", "x", **PROVENANCE)


def test_discard_removes_the_draft_and_its_meta(settings) -> None:
    ds.store_draft(settings, "brief_prompt", "text\n", **PROVENANCE)
    ds.discard_draft(settings, "brief_prompt")
    assert not ds.has_draft(settings, "brief_prompt")
    assert not list((settings.profile_dir / ".drafts").iterdir())
    with pytest.raises(cs.ConfigError):
        ds.discard_draft(settings, "brief_prompt")
