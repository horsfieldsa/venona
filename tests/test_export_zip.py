"""The archive: stable bytes, an atomic write, and how many are kept.

A zip is the one artifact in this repo whose diff nobody can read, so its
correctness has to be asserted rather than reviewed. Everything here is about
making two builds of one corpus provably the same file, and about what happens
to a file when the thing writing it is killed halfway.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from venona.db import core as db
from venona.db.repositories import data_export as repo
from venona.export import archive
from venona.export.vault import build_vault

NOW = "2026-09-10T12:00:00.000Z"


def _vault(conn: db.Conn):
    return build_vault(conn, now=NOW)


def test_the_same_corpus_builds_the_same_bytes(conn: db.Conn) -> None:
    vault = _vault(conn)
    assert archive.write_zip(vault) == archive.write_zip(vault)


def test_every_member_carries_the_zip_epoch(conn: db.Conn) -> None:
    """A real mtime is the commonest reason two builds of one corpus differ,
    and it says nothing a reader wanted to know."""
    payload = archive.write_zip(_vault(conn))
    with zipfile.ZipFile(io.BytesIO(payload)) as opened:
        assert {info.date_time for info in opened.infolist()} == {archive.ZIP_EPOCH}
        assert {info.create_system for info in opened.infolist()} == {3}


def test_members_are_sorted_by_bytes_not_by_locale(conn: db.Conn) -> None:
    payload = archive.write_zip(_vault(conn))
    with zipfile.ZipFile(io.BytesIO(payload)) as opened:
        names = opened.namelist()
    assert names == sorted(names, key=lambda name: name.encode("utf-8"))


def test_it_unpacks_to_one_folder_that_obsidian_can_open(conn: db.Conn, tmp_path: Path) -> None:
    """The whole deliverable, asserted end to end: unzip anywhere, open it."""
    vault = _vault(conn)
    with zipfile.ZipFile(io.BytesIO(archive.write_zip(vault))) as opened:
        assert not [name for name in opened.namelist() if name.endswith("/")]
        opened.extractall(tmp_path)
    root = tmp_path / vault.root
    assert root.is_dir()
    assert {entry.name for entry in tmp_path.iterdir()} == {vault.root}
    assert (root / "README.md").exists()
    graph = json.loads((root / ".obsidian" / "graph.json").read_text())
    assert graph["colorGroups"]
    assert json.loads((root / ".obsidian" / "app.json").read_text())["useMarkdownLinks"] is False
    assert "graph" in json.loads((root / ".obsidian" / "core-plugins.json").read_text())


def test_a_note_unpacks_readable(conn: db.Conn, tmp_path: Path) -> None:
    """`mkstemp` creates at 0600 and a zip remembers the mode it was given."""
    with zipfile.ZipFile(io.BytesIO(archive.write_zip(_vault(conn)))) as opened:
        opened.extractall(tmp_path)
    note = next(tmp_path.rglob("README.md"))
    assert note.stat().st_mode & 0o044


# ── storage ──────────────────────────────────────────────────────────


def test_a_second_export_on_one_day_does_not_overwrite_the_first(conn: db.Conn) -> None:
    """The date is the version, so two on one date have to be told apart."""
    assert archive.next_filename(conn, root="venona-x-2026-09-10") == "venona-x-2026-09-10.zip"
    repo.insert(
        conn,
        export_id="export:1",
        run_id=None,
        filename="venona-x-2026-09-10.zip",
        path="/nowhere/venona-x-2026-09-10.zip",
        content=b"x",
        size_bytes=1,
        sha256="a",
        counts="{}",
        created_at=NOW,
        created_by=None,
    )
    assert archive.next_filename(conn, root="venona-x-2026-09-10") == "venona-x-2026-09-10-2.zip"


def test_the_archive_and_its_row_arrive_together(conn: db.Conn) -> None:
    """THE THREE TESTS THAT STOOD HERE ARE RETIRED, and that is the point.

    `test_the_archive_arrives_whole_or_not_at_all` and
    `test_nothing_is_left_behind_by_a_successful_write` covered
    `archive.store`'s temp-file-and-rename: `os.replace` is atomic only within
    one filesystem, so the temporary directory had to sit inside the export
    directory rather than in the system temp. `test_an_archive_nothing_points_at_is_swept`
    covered the wreckage of a run killed between writing its file and recording
    it -- a file with no row, which nothing would ever come back for.

    All three guarded the gap between two writes. The archive is a column now
    (ADR 0234), so there is one write and no gap: no temp file to rename, no
    filesystem to be atomic within, and no way to produce an orphan. What is
    left to assert is that the single statement really is single.
    """
    repo.insert(
        conn,
        export_id="export:whole",
        run_id=None,
        filename="whole.zip",
        path="",
        content=b"payload",
        size_bytes=7,
        sha256="a",
        counts="{}",
        created_at=NOW,
        created_by=None,
    )
    row = repo.get(conn, "export:whole")
    assert row["status"] == repo.READY
    assert row["content_len"] == 7, "a row declared ready without its bytes"
    assert repo.content(conn, "export:whole") == b"payload"


# ── retention ────────────────────────────────────────────────────────


def test_only_the_newest_few_survive(conn: db.Conn) -> None:
    for index in range(7):
        repo.insert(
            conn,
            export_id=f"export:{index}",
            run_id=None,
            filename=f"e{index}.zip",
            path="",
            content=b"x",
            size_bytes=1,
            sha256="a",
            counts="{}",
            created_at=f"2026-09-0{index + 1}T00:00:00.000Z",
            created_by=None,
        )
    assert archive.prune(conn, keep=archive.KEEP, now=NOW) == 7 - archive.KEEP
    holding = {row["id"] for row in repo.listing(conn, limit=50) if row["content_len"]}
    assert holding == {f"export:{index}" for index in range(2, 7)}


def test_a_pruned_row_stays_and_loses_its_bytes(conn: db.Conn) -> None:
    """The row outlives the archive. "There was one and it is gone" is a
    different fact from "there was never one", and only the first teaches the
    rule.

    Dropping the bytes is half the point now that the archive is a column
    (ADR 0234): a row left `pruned` still holding its content keeps every
    megabyte retention was meant to reclaim.
    """
    repo.insert(
        conn,
        export_id="export:old",
        run_id=None,
        filename="old.zip",
        path="",
        content=b"x" * 4096,
        size_bytes=4096,
        sha256="a",
        counts="{}",
        created_at="2026-01-01T00:00:00.000Z",
        created_by=None,
    )
    archive.prune(conn, keep=0, now=NOW)
    row = repo.get(conn, "export:old")
    assert row["status"] == repo.PRUNED
    assert row["pruned_at"] == NOW
    assert not row["content_len"], "a pruned row kept the bytes retention exists to reclaim"
    assert repo.content(conn, "export:old") is None
