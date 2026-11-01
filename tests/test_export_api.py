"""The three routes, and what they refuse.

The download is the first file response this application has ever served, so
the things asserted here are the ones nothing else in the suite can see: that a
zip comes back as a zip, that a file which has gone says so honestly, and that
the two buttons a centimetre apart on one screen cannot run at the same time.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from venona.db import core as db
from venona.db.repositories import data_export as repo
from venona.db.repositories import notify as notify_repo
from venona.export import archive
from venona.export.service import run_export
from venona.services import run_service, run_types

NOW = "2026-09-10T12:00:00.000Z"

#: The shipped DDL, so a test asserts what actually runs at deploy rather than
#: a paraphrase of it.
MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "backend"
    / "venona"
    / "db"
    / "migrations"
    / "0026_data_exports.sql"
).read_text()


class _Clock:
    def now(self) -> str:
        return NOW


class _Ids:
    """Shared across contexts, so two exports in one test do not mint one id."""

    n = 0

    def uuid7(self) -> str:
        _Ids.n += 1
        return f"0000-{_Ids.n}"


class _Ctx:
    """The three things `run_export` asks of a run, and nothing else."""

    def __init__(self, conn: db.Conn) -> None:
        self.run_id = "run:export-test"
        self.clock = _Clock()
        self.ids = _Ids()
        self.db = conn
        self.notes: list[str] = []

    def note(self, text: str) -> None:
        self.notes.append(text)


@pytest.fixture
def settings_for(api_settings):
    return api_settings


# ── the work itself ──────────────────────────────────────────────────


async def test_an_export_records_itself_with_its_bytes(conn: db.Conn, api_settings) -> None:
    """The archive is a row, not a file a row points at (ADR 0234) -- the api
    runs with no persistent disk, and this table's promise is that a second
    download returns exactly what somebody already has."""
    ctx = _Ctx(conn)
    result = await run_export(ctx, conn=conn, settings=api_settings, actor=None)

    assert repo.content(conn, result.export_id) is not None
    assert repo.get(conn, result.export_id)["content_len"] == result.size_bytes
    assert result.filename.endswith(".zip")
    assert result.counts["notes"] > 0

    row = repo.get(conn, result.export_id)
    assert row["status"] == repo.READY
    assert row["sha256"] == result.sha256
    assert row["run_id"] == "run:export-test"


async def test_the_run_reads_on_its_own_connection(conn: db.Conn, api_settings) -> None:
    """The trap, asserted rather than remembered.

    `append_event` opens a transaction on the run's connection, and a snapshot
    held on that same connection would make every progress note raise. The
    export must therefore leave the run's connection usable throughout -- which
    is what this proves by writing on it straight after.
    """
    ctx = _Ctx(conn)
    await run_export(ctx, conn=conn, settings=api_settings, actor=None)
    with db.transaction(conn):
        conn.execute("SELECT 1")


async def test_a_second_export_the_same_day_is_a_second_archive(
    conn: db.Conn, api_settings
) -> None:
    first = await run_export(_Ctx(conn), conn=conn, settings=api_settings, actor=None)
    second = await run_export(_Ctx(conn), conn=conn, settings=api_settings, actor=None)
    assert first.filename != second.filename
    held = [row for row in repo.listing(conn, limit=50) if row["content_len"]]
    assert len(held) == 2, "the second export replaced the first instead of joining it"


# ── the routes ───────────────────────────────────────────────────────


async def test_the_listing_names_the_file_and_what_is_in_it(
    api_client, conn: db.Conn, api_settings
) -> None:
    result = await run_export(_Ctx(conn), conn=conn, settings=api_settings, actor=None)
    res = await api_client.get("/api/data/exports")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["keep"] == archive.KEEP
    row = next(item for item in body["items"] if item["id"] == result.export_id)
    assert row["filename"] == result.filename
    assert row["bytes"] == result.size_bytes
    assert row["counts"]["names"] > 0
    assert row["download_url"] == f"/api/data/exports/{result.export_id}/download"


async def test_the_download_is_a_zip_with_a_name_on_it(
    api_client, conn: db.Conn, api_settings
) -> None:
    result = await run_export(_Ctx(conn), conn=conn, settings=api_settings, actor=None)
    res = await api_client.get(f"/api/data/exports/{result.export_id}/download")
    assert res.status_code == 200, res.text
    assert res.headers["content-type"] == "application/zip"
    disposition = res.headers["content-disposition"]
    assert "attachment" in disposition
    assert result.filename in disposition
    assert res.content[:2] == b"PK"
    assert len(res.content) == result.size_bytes


async def test_an_export_nobody_ever_made_is_not_found(api_client) -> None:
    res = await api_client.get("/api/data/exports/export:nope/download")
    assert res.status_code == 404


async def test_a_file_that_has_gone_says_so_and_records_it(
    api_client, conn: db.Conn, api_settings
) -> None:
    """410, not 404. The row exists and knows what happened; a 404 would send
    somebody looking for a typo they did not make."""
    result = await run_export(_Ctx(conn), conn=conn, settings=api_settings, actor=None)
    # What retention, or a restore from a dump that dropped large objects, looks
    # like: the row is still READY and the bytes are not there.
    conn.execute("UPDATE data_exports SET content = NULL WHERE id = ?", (result.export_id,))

    res = await api_client.get(f"/api/data/exports/{result.export_id}/download")
    assert res.status_code == 410
    assert res.json()["detail"]["error"] == "export_gone"
    assert repo.get(conn, result.export_id)["status"] == repo.MISSING

    listed = await api_client.get("/api/data/exports")
    row = next(i for i in listed.json()["items"] if i["id"] == result.export_id)
    assert row["download_url"] is None, "the screen would offer a control that cannot work"


async def test_starting_an_export_is_a_run(api_client, monkeypatch) -> None:
    """The house pattern: the launcher is spied on, because what matters here is
    the kind, the params and the stage the router opens."""
    from venona.services import run_launcher

    seen: dict[str, object] = {}

    async def spy(request, **kwargs):
        seen.update(kwargs)
        return {"run_id": "run:x", "status": "running", "lane": run_types.LIGHT}

    monkeypatch.setattr(run_launcher, "launch", spy)
    res = await api_client.post("/api/data/exports")

    assert res.status_code == 202, res.text
    assert seen["kind"] == "export"
    assert seen["stage"] == "export", "no stage means the whole result is discarded"
    assert seen["stage"] in run_types.RUN_TYPES["export"].stages
    # Identical every time, which is what lets the light lane hand a second
    # press the run that is already going instead of a second identical file.
    assert seen["params"] == {"format": "obsidian"}


# ── being told, when you have walked away ────────────────────────────


def _subscribed(conn: db.Conn) -> None:
    """A destination that asked to hear about exports."""
    notify_repo.insert_destination(
        conn,
        destination_id="dest:ops",
        owner_kind="instance",
        owner_id=None,
        kind="webhook",
        label="ops",
        target="https://hooks.slack.com/services/T0/B0/abcd",
        created_by=None,
        now=NOW,
    )
    notify_repo.subscribe(conn, destination_id="dest:ops", notice_type="export.finished", now=NOW)
    conn.commit()


async def test_a_finished_export_tells_whoever_asked(
    api_client, conn: db.Conn, monkeypatch
) -> None:
    """End to end through the REAL notify path, to the outbox row.

    The run takes minutes and is started by hand, so the person who started it
    is the one most likely to be somewhere else when it lands. Asserted on the
    queued delivery rather than on a stubbed call, because the thing that breaks
    is the wiring between them.
    """
    from venona.services import run_launcher

    _subscribed(conn)
    captured: dict[str, object] = {}

    async def spy(request, **kwargs):
        captured.update(kwargs)
        return {"run_id": "run:x", "status": "running", "lane": run_types.LIGHT}

    monkeypatch.setattr(run_launcher, "launch", spy)
    await api_client.post("/api/data/exports")

    work = captured["work"]
    target = run_launcher.LaunchContext(
        run_id="run:x", conn=conn, ctx=_Ctx(conn), emit=None, check_cancel=lambda: None
    )
    outcome = await work(target)
    assert outcome.outcome == "delivered"

    queued = conn.execute("SELECT notice_type, payload FROM notify_outbox ORDER BY id").fetchall()
    assert [row["notice_type"] for row in queued] == ["export.finished"]
    assert "ready" in db.jload(queued[0]["payload"])["title"]


def _a_reader(conn: db.Conn) -> str:
    """A real user row, because a reader's channel is keyed to one."""
    conn.execute(
        "INSERT INTO users (id, email, role, status, created_at, entitlement_status) "
        "VALUES (?, ?, 'reader', 'active', ?, 'comped') ON CONFLICT (id) DO NOTHING",
        ("user:r", "r@example.test", NOW),
    )
    return "user:r"


def test_a_destination_that_predates_this_notice_is_subscribed_to_it(conn: db.Conn) -> None:
    """`default_on` is read at exactly one moment -- when a destination is
    CREATED -- and fan-out asks for a subscription row, where absence means no.
    So a notice type added after an operator wired up their channel would be
    silent there forever, and the instance that fails on is the one that has
    been running longest.

    The shipped statement is re-run rather than described, and it is idempotent
    by construction (`ON CONFLICT DO NOTHING`). What could actually be wrong is
    the predicate, so that is what is put under it: an instance destination is
    subscribed, a suspended one and a reader's own are not.
    """
    for destination_id, owner_kind, owner_id, target in (
        ("dest:ops", "instance", None, "https://hooks.slack.com/services/T0/B0/aaa"),
        ("dest:off", "instance", None, "https://hooks.slack.com/services/T0/B0/bbb"),
        ("dest:reader", "reader", _a_reader(conn), "https://hooks.slack.com/services/T0/B0/ccc"),
    ):
        notify_repo.insert_destination(
            conn,
            destination_id=destination_id,
            owner_kind=owner_kind,
            owner_id=owner_id,
            kind="webhook",
            label=destination_id,
            target=target,
            created_by=None,
            now=NOW,
        )
    conn.execute("UPDATE notify_destinations SET status = 'suspended' WHERE id = 'dest:off'")
    conn.commit()

    backfill = next(
        statement
        for statement in MIGRATION.split(";")
        if "notify_subscriptions" in statement and "INSERT" in statement
    )
    conn.execute(backfill)
    conn.commit()

    subscribed = {
        row["destination_id"]
        for row in conn.execute(
            "SELECT destination_id FROM notify_subscriptions WHERE notice_type = 'export.finished'"
        ).fetchall()
    }
    assert "dest:ops" in subscribed
    assert "dest:off" not in subscribed, "a suspended channel was subscribed"
    assert "dest:reader" not in subscribed, "an instance notice reached a reader's own channel"


def test_the_notice_says_which_ending_it_is() -> None:
    """One switch, two endings. A destination subscribed to "a copy finished"
    is subscribed to the one it did not."""
    from venona.notify.types import NOTICE_TYPES

    declared = NOTICE_TYPES["export.finished"]
    ready = declared.render({"filename": "venona-x-2026-09-10.zip", "notes": 12})
    assert "ready" in ready.title
    assert ready.link == "/admin/data"
    assert any("venona-x-2026-09-10.zip" in line for line in ready.body)

    failed = declared.render({"run_id": "run:x", "failed": True, "error": "no room"})
    assert "did not finish" in failed.title
    assert failed.link == "/admin/runs/run:x"
    assert any("no room" in line for line in failed.body)

    # One per run, either way -- the same run must not report twice.
    assert declared.dedupe({"run_id": "run:x"}) == declared.dedupe(
        {"run_id": "run:x", "failed": True}
    )


async def test_a_failed_export_is_announced_too(api_client, conn: db.Conn, monkeypatch) -> None:
    """The silent case: the disk filled while nobody was looking at the tab."""
    from venona.api.routers import data as data_router
    from venona.services import run_launcher

    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        data_router.notify, "raise_notice", lambda c, t, **f: seen.append((t, f)) or 1
    )

    async def boom(*args, **kwargs):
        raise RuntimeError("there is not enough room")

    monkeypatch.setattr("venona.export.service.run_export", boom)

    captured: dict[str, object] = {}

    async def spy(request, **kwargs):
        captured.update(kwargs)
        return {"run_id": "run:x", "status": "running", "lane": run_types.LIGHT}

    monkeypatch.setattr(run_launcher, "launch", spy)
    await api_client.post("/api/data/exports")

    work = captured["work"]
    target = run_launcher.LaunchContext(
        run_id="run:x", conn=conn, ctx=_Ctx(conn), emit=None, check_cancel=lambda: None
    )
    with pytest.raises(RuntimeError):
        await work(target)

    assert seen, "a failed export told nobody"
    kind, facts = seen[-1]
    assert kind == "export.finished"
    assert facts["failed"] is True
    assert "not enough room" in facts["error"]


# ── the two buttons on one screen ────────────────────────────────────


async def test_a_purge_will_not_run_while_an_export_is_being_written(api_client) -> None:
    """Otherwise the archive is a complete copy of data just deleted."""
    handle = await run_service.registry.submit("run:exporting", "export", {})
    try:
        res = await api_client.post("/api/data/purge", json={"confirm": "delete everything"})
        assert res.status_code == 409, res.text
        assert res.json()["detail"]["error"] == "export_active"
    finally:
        await run_service.registry.release(handle.run_id)


async def test_a_purge_is_not_refused_because_a_reader_looked_something_up(
    api_client,
) -> None:
    """The lane holds lookups too. Refusing a purge because somebody hovered a
    term is the global-slot damage C11 exists to end, one level down."""
    handle = await run_service.registry.submit("run:looking", "lookup", {})
    try:
        res = await api_client.post("/api/data/purge", json={"confirm": "delete everything"})
        assert res.status_code == 200, res.text
    finally:
        await run_service.registry.release(handle.run_id)


async def test_an_export_survives_a_purge(api_client, conn: db.Conn, api_settings) -> None:
    """The whole point of the feature. `runs` is purged and this is not, which
    is why the row carries no foreign key to it."""
    result = await run_export(_Ctx(conn), conn=conn, settings=api_settings, actor=None)

    res = await api_client.post("/api/data/purge", json={"confirm": "delete everything"})
    assert res.status_code == 200, res.text

    assert repo.content(conn, result.export_id) is not None, (
        "the archive was deleted by the thing it exists to survive"
    )
    listed = await api_client.get("/api/data/exports")
    row = next(i for i in listed.json()["items"] if i["id"] == result.export_id)
    assert row["download_url"], "the listing lost the file because it joined a purged table"

    download = await api_client.get(f"/api/data/exports/{result.export_id}/download")
    assert download.status_code == 200


async def test_the_screen_is_told_what_survives(api_client) -> None:
    res = await api_client.get("/api/data/stats")
    assert "data_exports" in res.json()["preserved"]
