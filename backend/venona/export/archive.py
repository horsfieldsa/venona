"""The folder as one file on disk, and how many of them are kept.

BYTE-STABLE ON PURPOSE. Two builds of an unchanged corpus produce identical
archives, which is what lets a test assert the folder's shape without anybody
having to read a diff of compressed bytes. Everything that would otherwise vary
is pinned here rather than left to a default -- the member timestamps, the
compression level, the host attributes and the Zip64 decision -- because each
of those defaults is a property of the machine or the interpreter rather than
of the corpus.
"""

from __future__ import annotations

import contextlib
import hashlib
import tempfile
import zipfile
from pathlib import Path

from venona.db import core as db
from venona.db.repositories import data_export as repo
from venona.export.vault import Vault

#: How many archives survive. A constant rather than a setting: the reason to
#: keep more than one is to compare two of them over time, and a knob nobody
#: turns is a configuration document with a support cost attached.
KEEP = 5

#: The earliest moment the zip format can represent. Chosen so that NOTHING
#: derived from wall-clock time enters the archive at all -- a real mtime would
#: make two builds of one corpus differ, which is the whole thing this module
#: is arranged to prevent.
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def write_zip(vault: Vault) -> bytes:
    """The archive, in memory, with one top-level folder.

    One folder rather than loose files, so unzipping anywhere yields something
    that can be opened as a vault straight away instead of scattering a
    thousand notes across whatever directory the reader was in.
    """
    buffer = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    with zipfile.ZipFile(buffer, "w", allowZip64=True) as archive:
        for member in vault.members:
            info = zipfile.ZipInfo(f"{vault.root}/{member.path}", date_time=ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            # Pinned rather than defaulted: the header records the host that
            # wrote it, and a mode of 0 unpacks as an unreadable file on some
            # tools. 3 is Unix.
            info.create_system = 3
            info.external_attr = 0o644 << 16
            archive.writestr(info, member.text.encode("utf-8"), compresslevel=9)
    buffer.seek(0)
    return buffer.read()


def next_filename(conn: db.Conn, *, root: str) -> str:
    """`<root>.zip`, or the next free counter if that date already has one.

    The date IS the version, so a second export on one day must not overwrite
    the first and must not be given a name that hides which came first.
    """
    taken = repo.taken_filenames(conn, stem=root)
    candidate = f"{root}.zip"
    counter = 2
    while candidate in taken:
        candidate = f"{root}-{counter}.zip"
        counter += 1
    return candidate


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def prune(conn: db.Conn, *, keep: int, now: str) -> int:
    """Keep the newest `keep`, and say so honestly about the rest.

    ONE STATEMENT PER ROW (ADR 0234): the archive is a column, so dropping the
    bytes and marking the row pruned is the same write. The `unlink` is for rows
    carrying a `path`, best effort, absence not an error.
    comment-history: ok -- describes rows the table still holds.
    """
    removed = 0
    for row in repo.prunable(conn, keep=keep):
        if row["path"]:
            with contextlib.suppress(OSError):
                Path(row["path"]).unlink(missing_ok=True)
        repo.mark_pruned(conn, row["id"], now=now)
        removed += 1
    return removed
