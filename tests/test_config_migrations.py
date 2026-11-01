"""Configuration is carried forward, and the carrying does not eat the prose.

An instance's `settings.yaml` is written once and edited by its operator from
then on, so a release that adds a knob has to reach a file it must not
overwrite. These hold both halves of that: the line surgery keeps every comment
and every chosen value, and the series says explicitly what each release does
rather than inferring it from a diff.

The fleet cases at the bottom run the real series against the real files from
production, toydom and testdom, captured on 2026-09-01. They are the ones that
would have caught this defect.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from venona.services import config_migrations as cm
from venona.services import config_text as ct
from venona.settings import LlmSettings, Settings

REPO = Path(__file__).resolve().parents[1]
SHIPPED = (REPO / "config" / "settings.yaml").read_text()
INSTANCES = REPO / "fixtures" / "config_migrations" / "instances"


def _paths(tmp_path: Path, text: str) -> SimpleNamespace:
    config = tmp_path / "config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "settings.yaml").write_text(text)
    return SimpleNamespace(config_dir=config, profile_dir=tmp_path / "profile")


def _settings_file(paths: SimpleNamespace) -> str:
    return (paths.config_dir / "settings.yaml").read_text()


# ── the splice ───────────────────────────────────────────────────────────


def test_a_key_arrives_with_the_comment_that_explains_it() -> None:
    stripped = ct.extract_block(SHIPPED, "llm", "outage_wait_seconds")
    assert stripped[-1].strip().startswith("outage_wait_seconds:")
    assert any("provider to recover" in line for line in stripped)


def test_a_comment_belongs_to_the_key_below_it_not_the_one_above() -> None:
    """THE RULE THE REAL DOCUMENT TURNS ON. `outage_wait_seconds`' explanation
    sits between two keys with no blank line above it. Ownership by "contiguous
    comments directly above" hands it to the key below, so moving the key above
    cannot carry it off."""
    above = ct.extract_block(SHIPPED, "llm", "max_calls_per_run")
    assert not any("provider to recover" in line for line in above)
    assert any("Runaway guard" in line for line in above)


def test_a_blank_line_ends_comment_ownership() -> None:
    block = ct.extract_block(SHIPPED, "llm", "discover_model")
    assert any("Opus for judgment-heavy" in line for line in block)
    assert not any("max_calls_per_run" in line for line in block)


def test_an_insert_disturbs_nothing_else_in_the_file() -> None:
    """Remove a key WITH ITS COMMENT, put it back, and nothing else moved.

    The unit removed has to be the unit `extract_block` moves -- comment, name
    and value -- or the test is not round-tripping anything. Thinning by "every
    line mentioning the key" instead passed only while the example key happened
    to carry no comment: the moment one was written above it, the restore put
    the comment back beside the copy the thinning had left behind, and the
    failure read as a bug in the surgery rather than in the test.
    """
    lines = SHIPPED.splitlines()
    span = ct.key_span(lines, "llm", "gazetteer_size")
    assert span is not None
    block = ct.extract_block(SHIPPED, "llm", "gazetteer_size")
    remaining = lines[: span.comment_start] + lines[span.value_end + 1 :]
    thinned = "\n".join(remaining)

    restored = ct.insert_key(thinned, "llm", block)
    assert yaml.safe_load(restored)["llm"]["gazetteer_size"] == 400
    # Every line the file already had, still there and still in order.
    kept = [line for line in restored.splitlines() if line not in block or not line.strip()]
    assert [line for line in kept if line.strip()] == [line for line in remaining if line.strip()]


def test_a_value_spanning_lines_moves_whole() -> None:
    doc = "llm:\n  note: |\n    one\n    # two\n  after: 1\n"
    assert ct.extract_block(doc, "llm", "note") == ["  note: |", "    one", "    # two"]


def test_a_group_the_file_has_never_had_is_added_whole(tmp_path: Path) -> None:
    """An instance old enough to predate a whole group, which is what an
    instance scaffolded before `schedule:` existed would look like."""
    lines = (INSTANCES / "toydom.yaml").read_text().splitlines()
    start, end = ct.group_span(lines, "schedule")
    without = "\n".join(lines[:start] + lines[end:])
    assert "schedule:" not in without
    paths = _paths(tmp_path, without)
    cm.migrate(paths)
    assert yaml.safe_load(_settings_file(paths))["schedule"]["cron"] == "0 6 * * *"


def test_a_document_shaped_like_something_else_is_refused() -> None:
    for bad in ("llm:\n  a: &anchor 1\n", "---\nllm:\n  a: 1\n", "llm:\n  a: 1\n  a: 2\n"):
        with pytest.raises(ct.ConfigTextError):
            ct.check(bad)


def test_the_text_and_the_shadow_must_agree(tmp_path: Path, monkeypatch) -> None:
    """THE GUARD THAT MAKES LINE SURGERY SAFE TO RUN UNATTENDED. A splice that
    edited the wrong lines is caught by comparing the parsed result against what
    the operations said they were doing, before anything is written."""

    def sabotage(text: str, group: str, block: list[str]) -> str:
        return text

    monkeypatch.setattr(ct, "insert_key", sabotage)
    paths = _paths(tmp_path, (INSTANCES / "toydom.yaml").read_text())
    before = _settings_file(paths)
    with pytest.raises(cm.MigrationError):
        cm.migrate(paths)
    assert _settings_file(paths) == before


# ── the series ───────────────────────────────────────────────────────────


def test_the_series_is_idempotent(tmp_path: Path) -> None:
    paths = _paths(tmp_path, (INSTANCES / "toydom.yaml").read_text())
    assert cm.migrate(paths) == [m.version for m in cm.MIGRATIONS]
    once = _settings_file(paths)
    assert cm.migrate(paths) == []
    assert _settings_file(paths) == once


def test_every_operation_survives_being_applied_twice(tmp_path: Path) -> None:
    """The version marker is an optimisation, not the correctness argument. A
    marker lost to a hand edit costs a redundant pass, never a broken file."""
    paths = _paths(tmp_path, (INSTANCES / "toydom.yaml").read_text())
    cm.migrate(paths)
    once = _settings_file(paths)
    (paths.config_dir / "settings.yaml").write_text(
        "\n".join(line for line in once.splitlines() if not line.startswith("settings_version"))
    )
    cm.migrate(paths)
    assert yaml.safe_load(_settings_file(paths)) == yaml.safe_load(once)


def test_a_file_with_no_marker_reads_as_zero() -> None:
    assert ct.read_version("llm:\n  a: 1\n") == 0
    assert ct.read_version(SHIPPED) == cm.HEAD


def test_the_shipped_file_is_at_head(tmp_path: Path) -> None:
    """A freshly seeded instance is stamped head by construction, because the
    file it is seeded from already says so. No stamping step to forget."""
    assert cm.pending(SHIPPED) == []
    paths = _paths(tmp_path, SHIPPED)
    assert cm.migrate(paths) == []


def test_replaying_from_nothing_reproduces_the_shipped_values(tmp_path: Path) -> None:
    """ALSO THE GUARD ON THE SERIES ITSELF. A key added to the shipped file
    without a migration to carry it is a key the series cannot produce, so this
    goes red naming the difference rather than the knob quietly never reaching
    an instance that already exists."""
    paths = _paths(tmp_path, "")
    cm.migrate(paths)
    assert yaml.safe_load(_settings_file(paths)) == yaml.safe_load(SHIPPED)


def test_the_previous_version_is_kept(tmp_path: Path) -> None:
    paths = _paths(tmp_path, (INSTANCES / "toydom.yaml").read_text())
    cm.migrate(paths)
    kept = list((paths.config_dir / ".history").glob("settings.*.bak"))
    assert len(kept) == 1
    assert yaml.safe_load(kept[0].read_text())["llm"]["pinned_version"] == "2.1.220"


# ── every instance that exists ───────────────────────────────────────────


@pytest.mark.parametrize("name", ["prod", "toydom", "testdom"])
def test_the_series_brings_a_real_instance_to_a_loadable_document(
    name: str, tmp_path: Path
) -> None:
    """The captured files from production, toydom and testdom. A migration that
    would refuse to boot one of them fails here instead."""
    paths = _paths(tmp_path, (INSTANCES / f"{name}.yaml").read_text())
    cm.migrate(paths)
    parsed = yaml.safe_load(_settings_file(paths))
    settings = Settings(**parsed)
    assert settings.settings_version == cm.HEAD
    assert settings.llm.pinned_version == LlmSettings().pinned_version


@pytest.mark.parametrize("name", ["prod", "toydom", "testdom"])
def test_a_real_instance_keeps_every_value_it_chose(name: str, tmp_path: Path) -> None:
    before = yaml.safe_load((INSTANCES / f"{name}.yaml").read_text())
    paths = _paths(tmp_path, (INSTANCES / f"{name}.yaml").read_text())
    cm.migrate(paths)
    after = yaml.safe_load(_settings_file(paths))
    # Fields the operator has no say over, so a migration may overwrite one
    # without this test calling it a lost value. Both are already in
    # `config_service.FROZEN_FIELDS`, which is what makes that true: neither
    # can be changed over HTTP, so there is no choice here to respect.
    #
    # `backend` is here because it used to answer two questions at once -- how
    # the model is reached, and whose CLI that is -- and the second became a
    # setting of its own. The old spelling has no meaning under the new pair.
    forced = {("llm", "pinned_version"), ("llm", "backend")}
    for group, body in before.items():
        if not isinstance(body, dict):
            continue
        for key, value in body.items():
            if (group, key) not in forced:
                assert after[group][key] == value, f"{group}.{key} was changed"


# ── the guards that keep the series honest ───────────────────────────────


def test_the_shipped_file_says_what_the_code_defaults_say() -> None:
    """The document is the readable statement of the defaults, so the two must
    agree or one of them is lying to whoever reads it."""
    parsed = yaml.safe_load(SHIPPED)
    stock = Settings()
    for group in ("llm", "schedule", "pipeline"):
        for key, value in parsed[group].items():
            assert value == getattr(getattr(stock, group), key), (
                f"{group}.{key}: the shipped document and the code defaults disagree. "
                f"On the default instance `config/` IS the repository, so editing "
                f"anything in the app -- switching the model service at /admin/model "
                f"included -- diverges a tracked file. Revert it, or change the code "
                f"default deliberately."
            )


def test_a_forced_field_ends_at_the_current_model_default(tmp_path: Path) -> None:
    """The deploy-owned fields cannot be edited over HTTP, so an instance's copy
    is only ever whichever value shipped with it. Bump the pin without writing
    the migration and this is the build that goes red."""
    from venona.services.config_service import FROZEN_FIELDS

    paths = _paths(tmp_path, (INSTANCES / "prod.yaml").read_text())
    cm.migrate(paths)
    settings = Settings(**yaml.safe_load(_settings_file(paths)))
    stock = Settings()
    for group, field in FROZEN_FIELDS:
        if field in yaml.safe_load(SHIPPED).get(group, {}):
            assert getattr(getattr(settings, group), field) == getattr(
                getattr(stock, group), field
            ), f"{group}.{field} needs a migration; see 0002"


def test_the_baseline_is_only_the_first_migration() -> None:
    for migration in cm.MIGRATIONS:
        for op in migration.ops:
            assert not isinstance(op, cm.Baseline) or migration.version == 1


def test_the_versions_are_contiguous_and_unique() -> None:
    versions = [m.version for m in cm.MIGRATIONS]
    assert versions == list(range(1, len(versions) + 1))


def test_an_overwrite_must_declare_itself() -> None:
    """A migration that does not say whether it is overriding an operator's
    choice is not reviewable, so it cannot be written."""
    with pytest.raises(ValueError, match="force=True"):
        cm.SetValue(group="llm", key="analyze_model", to="opus", force=False)
