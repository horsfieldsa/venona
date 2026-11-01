"""Running an export, from a run's point of view.

THE READ HAPPENS ON A SECOND CONNECTION, and that is not an optimisation.
`run_service.append_event` opens its own `db.transaction`, and `db.transaction`
refuses to nest -- so a snapshot held on the run's own connection turns every
progress note, and then the stage row and the commit that follow it, into a
`RuntimeError`. Reading on a connection of its own leaves the run's free to
report while the read is in flight, which is also what stops a multi-minute
export looking hung.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from venona.db import core as db
from venona.db.repositories import data_export as repo
from venona.export import archive
from venona.export.vault import build_vault

#: What the archive must fit into with room to spare. The disk on a deployed
#: instance also carries the configuration, the model cache and both command
#: line tools' rotating credentials, and filling it damages considerably more
#: than one export.


@dataclass(frozen=True)
class ExportResult:
    export_id: str
    filename: str
    size_bytes: int
    sha256: str
    counts: dict[str, int]


async def run_export(ctx: Any, *, conn: db.Conn, settings: Any, actor: str | None) -> ExportResult:
    """Write the whole reading out, record it, and tidy up behind it.

    `conn` is the RUN's connection and is used only to write. Everything read
    comes through a second one, under a single snapshot, so the folder is one
    coherent moment rather than a dozen of them -- otherwise a research run
    committing between the names and the relationships yields a link to a note
    the same folder declined to write.
    """
    # NO DIRECTORY, NO FREE-SPACE CHECK, NO ORPHAN SWEEP (ADR 0234). The archive
    # is a column now, so there is no file to leave behind and no filesystem to
    # run out of -- the two failure modes those guarded against cannot happen.
    # What replaces them is the keep-N prune below, which is the same policy
    # expressed against rows.
    read_conn = db.connect(settings.database_url)
    try:
        with db.snapshot(read_conn):
            vault = build_vault(read_conn, now=ctx.clock.now())
    finally:
        read_conn.close()

    payload = archive.write_zip(vault)
    filename = archive.next_filename(conn, root=vault.root)

    export_id = f"export:{ctx.ids.uuid7()}"
    now = ctx.clock.now()
    # ONE STATEMENT, bytes and row together, so no crash can leave a row naming
    # an archive that is not there.
    repo.insert(
        conn,
        export_id=export_id,
        run_id=ctx.run_id,
        filename=filename,
        path="",
        content=payload,
        size_bytes=len(payload),
        sha256=archive.digest(payload),
        counts=db.jdump(vault.counts),
        created_at=now,
        created_by=actor,
    )
    removed = archive.prune(conn, keep=archive.KEEP, now=now)
    if removed:
        ctx.note(f"Removed {removed} older export(s) to save room.")

    return ExportResult(
        export_id=export_id,
        filename=filename,
        size_bytes=len(payload),
        sha256=archive.digest(payload),
        counts=dict(vault.counts),
    )
