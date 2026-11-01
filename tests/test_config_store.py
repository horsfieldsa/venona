"""The authored documents round-trip through Postgres (ADR 0234).

The api runs with no persistent disk, so the files every loader reads are a
projection: written out of rows at boot, written back on save. A boot with an
empty TABLE adopts what the disk already had, which is the migration path and
the reason the disk goes in a second deploy.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from venona.db import core as db
from venona.db.repositories import config_documents as docs_repo
from venona.services import config_service as cs
from venona.settings import get_settings

KEY = "relevance_profile"


@pytest.fixture
def scratch(tmp_path: Path):
    """Settings whose config and profile dirs are throwaway copies.

    The same arrangement as `tests/test_config_docs.py::settings` -- the
    documents are the user's, and `conftest._tree_state` fails any test that
    writes to the working tree.
    """
    real = get_settings()
    out = copy.deepcopy(real)
    out.profile_dir = tmp_path / "profile"
    out.config_dir = tmp_path / "config"
    out.profile_dir.mkdir()
    out.config_dir.mkdir()
    for meta in cs.EDITABLE.values():
        if meta["dir"] == "ontology":
            continue
        source = (real.profile_dir if meta["dir"] == "profile" else real.config_dir) / meta["file"]
        target = (out.profile_dir if meta["dir"] == "profile" else out.config_dir) / meta["file"]
        if source.exists():
            target.write_text(source.read_text())
    return out


def test_materialize_adopts_what_the_disk_already_had(scratch, conn: db.Conn) -> None:
    """The first boot after this release, with the disk still attached: the file
    that is there becomes the row, and this must run BEFORE the disk goes."""
    original = (scratch.profile_dir / "relevance_profile.md").read_text()

    cs.materialize(conn, scratch)

    row = docs_repo.get(conn, key=KEY)
    assert row is not None, "materialize did not adopt the document already on disk"
    assert row["content"] == original
    assert (scratch.profile_dir / "relevance_profile.md").read_text() == original


def test_a_save_writes_the_row(scratch, conn: db.Conn) -> None:
    cs.materialize(conn, scratch)
    cs.write_doc(scratch, KEY, "# Mine\n\nWhat matters.\n", conn=conn)

    row = docs_repo.get(conn, key=KEY)
    assert row["content"] == "# Mine\n\nWhat matters.\n"
    assert row["version"] == cs._version("# Mine\n\nWhat matters.\n")


def test_a_boot_with_no_files_rebuilds_them_from_rows(scratch, conn: db.Conn, tmp_path) -> None:
    """The deploy after the disk is removed: an empty container, and the
    operator's documents come back."""
    cs.materialize(conn, scratch)
    cs.write_doc(scratch, KEY, "# Survives\n", conn=conn)

    fresh = copy.deepcopy(scratch)
    fresh.profile_dir = tmp_path / "fresh-profile"
    fresh.config_dir = tmp_path / "fresh-config"
    fresh.profile_dir.mkdir()
    fresh.config_dir.mkdir()

    written = cs.materialize(conn, fresh)

    assert KEY in written
    assert (fresh.profile_dir / "relevance_profile.md").read_text() == "# Survives\n"


def test_history_is_rows_and_a_version_can_be_read_back(scratch, conn: db.Conn) -> None:
    cs.materialize(conn, scratch)
    first = (scratch.profile_dir / "relevance_profile.md").read_text()
    cs.write_doc(scratch, KEY, "# Second\n", conn=conn)

    listed = cs.history(scratch, KEY, conn)
    assert listed, "the replaced version was not kept"
    assert listed[0]["version"] == cs._version(first)

    got = cs.snapshot(scratch, KEY, listed[0]["file"], conn)
    assert got["content"] == first


def test_the_same_content_current_twice_is_two_saved_versions(scratch, conn: db.Conn) -> None:
    """Revert, then save again. The row key carries stamp AND version for the
    reason the filename did: keying on (key, version) alone was measured
    dropping 2 of 7 ontology and 2 of 13 settings snapshots on the live
    instance's first import.
    """
    import os

    cs.materialize(conn, scratch)
    path = scratch.profile_dir / "relevance_profile.md"
    original = path.read_text()

    # The stamp is the REPLACED file's mtime, at one-second resolution, so a
    # test that saves three times in one millisecond is testing the collision
    # the filename had too. Days apart is the case that matters and the one the
    # live instance actually produced.
    cs.write_doc(scratch, KEY, "# A\n", conn=conn)
    os.utime(path, (1_600_000_000, 1_600_000_000))
    cs.write_doc(scratch, KEY, original, conn=conn)
    os.utime(path, (1_600_086_400, 1_600_086_400))
    cs.write_doc(scratch, KEY, "# A\n", conn=conn)

    # Replacing A with the original and then the original with A again snapshots
    # the ORIGINAL twice, at two different stamps -- that is the pair that must
    # both survive.
    versions = [row["version"] for row in cs.history(scratch, KEY, conn)]
    assert versions.count(cs._version(original)) == 2, (
        f"the two moments the original was current collapsed into one: {versions}"
    )


def test_adopting_carries_the_files_own_edit_time(scratch, conn: db.Conn) -> None:
    """Otherwise every document on an instance edited for months reads as never
    touched, from the moment the disk goes."""
    import os

    path = scratch.profile_dir / "relevance_profile.md"
    os.utime(path, (1_600_000_000, 1_600_000_000))

    cs.materialize(conn, scratch)

    row = docs_repo.get(conn, key=KEY)
    assert row["updated_at"].startswith("2020-09-13"), row["updated_at"]


def test_an_unbound_store_keeps_the_save_on_the_filesystem(scratch) -> None:
    """What a unit test and a pre-materialize instance both look like.

    Load-bearing rather than incidental: without it, every filesystem-only
    config test in the suite would need a database, and `write_doc` would reach
    for whatever DSN it could find -- which is how twenty-five validation tests
    would have written rows into the developer's own instance.
    """
    assert cs._STORE_DSN == "", "the store leaked from another test"
    cs.write_doc(scratch, KEY, "# No database here\n")
    assert (scratch.profile_dir / "relevance_profile.md").read_text() == "# No database here\n"
