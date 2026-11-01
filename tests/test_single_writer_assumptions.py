"""Writes that were correct while one process wrote at a time.

Every case here is a read-then-write that two writers can interleave, on a
deployment that now has a light grounding lane, a research lane, a scheduler
and a browser all live at once. None of them raises when it goes wrong -- a
count is quietly low, a registration is quietly kept, a stale link is quietly
rendered -- which is why each one needs a test rather than an incident.
"""

from __future__ import annotations

import threading

import pytest
from venona.db import core as db
from venona.pipeline import acronyms
from venona.services import oauth_service

# ── acronym attempt counting ─────────────────────────────────────────


def test_two_lookups_of_one_surface_count_two_attempts(database_url: str) -> None:
    """⭐ `attempt_count` decides whether the nightly sweep pays to try a
    surface again. It was read, incremented in Python and written back, so two
    lanes that both read 3 both wrote 4 -- and the column under-counted exactly
    the surfaces being attempted most often, which are the ones the retry
    window exists to hold back.
    """
    setup = db.connect(database_url)
    try:
        acronyms.mark_undefined(
            setup, "ZZZ", now="t0", sightings=1, articles=1, attempted_run_id="run:a"
        )
    finally:
        setup.close()

    start = threading.Barrier(2)
    errors: list[BaseException] = []

    def attempt(run_id: str) -> None:
        conn = db.connect(database_url)
        try:
            start.wait(timeout=5)
            acronyms.mark_undefined(
                conn, "ZZZ", now="t1", sightings=1, articles=1, attempted_run_id=run_id
            )
        except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=attempt, args=(r,)) for r in ("run:b", "run:c")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f"concurrent attempts raised: {errors[:2]}"

    check = db.connect(database_url)
    try:
        row = check.execute(
            "SELECT attempt_count FROM acronym_definitions WHERE id = ?",
            (acronyms.glossary_id("ZZZ"),),
        ).fetchone()
    finally:
        check.close()
    assert row["attempt_count"] == 3, "both concurrent attempts must have been counted"


def test_an_attempt_does_not_move_first_seen(conn: db.Conn) -> None:
    """`first_seen` is the first time this surface was seen, and a column that
    moves on every write is not that."""
    acronyms.mark_undefined(conn, "YYY", now="t0", sightings=1, articles=1, attempted_run_id="r1")
    acronyms.mark_undefined(conn, "YYY", now="t9", sightings=2, articles=1, attempted_run_id="r2")

    row = conn.execute(
        "SELECT first_seen, last_seen, attempt_count FROM acronym_definitions WHERE id = ?",
        (acronyms.glossary_id("YYY"),),
    ).fetchone()
    assert row["first_seen"] == "t0"
    assert row["last_seen"] == "t9"
    assert row["attempt_count"] == 2


# ── OAuth registrations ──────────────────────────────────────────────


def _register(conn: db.Conn, client_id: str, created_at: str, last_used_at: str | None) -> None:
    db.insert(
        conn,
        "oauth_clients",
        {
            "client_id": client_id,
            "client_name": "a client",
            "redirect_uris": '["https://example.test/cb"]',
            "created_at": created_at,
            "last_used_at": last_used_at,
        },
    )


def test_registrations_that_were_never_used_are_purged(conn: db.Conn) -> None:
    """⭐ `/register` is unauthenticated by RFC 7591 and nothing ever removed a
    row it wrote, so the table grew for as long as the process lived."""
    _register(conn, "client:stale", "2026-08-20T00:00:00.000Z", None)
    purged = oauth_service.purge_expired(conn, now="2026-08-26T00:00:00.000Z")

    assert purged["clients"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM oauth_clients").fetchone()["n"] == 0


def test_a_paired_client_survives_the_purge(conn: db.Conn) -> None:
    """Contract C12: the extension registers a fresh client per pairing and
    those rows accumulate BY DESIGN. `last_used_at` is stamped only on a
    successful redemption, so it is what tells a live pairing from spam."""
    _register(conn, "client:paired", "2020-01-01T00:00:00.000Z", "2020-01-01T00:00:01.000Z")
    purged = oauth_service.purge_expired(conn, now="2026-08-26T00:00:00.000Z")

    assert purged["clients"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM oauth_clients").fetchone()["n"] == 1


def test_a_registration_inside_the_window_survives(conn: db.Conn) -> None:
    """Someone who started pairing and walked away must still find their client
    when they come back."""
    _register(conn, "client:fresh", "2026-08-25T23:00:00.000Z", None)
    purged = oauth_service.purge_expired(conn, now="2026-08-26T00:00:00.000Z")

    assert purged["clients"] == 0


# ── the schedule ─────────────────────────────────────────────────────


def test_an_unusable_timezone_does_not_take_the_process_down(caplog) -> None:
    """⭐ `settings.yaml` is hand-editable and this runs in the lifespan, so a
    typo in `schedule.timezone` is the difference between one wrong schedule
    and an instance that will not boot."""
    from venona.scheduler import _build_scheduler

    scheduler = _build_scheduler("Not/AZone")
    assert scheduler is not None, "a bad timezone must still yield a scheduler"
    assert any("not a timezone" in r.message for r in caplog.records), (
        "falling back silently would leave the wrong schedule with nothing said"
    )


def test_a_real_timezone_is_used_as_written() -> None:
    from venona.scheduler import _build_scheduler

    scheduler = _build_scheduler("Europe/London")
    assert "London" in str(scheduler.timezone)


# ── URL safety ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "data:text/html;base64,PHNjcmlwdD4=",
        "java\nscript:alert(1)",
        " javascript:alert(1)",
        "//evil.test/path",
        "vbscript:msgbox(1)",
        "relative/path",
        "",
    ],
)
def test_a_non_web_url_is_refused(value: str) -> None:
    """⭐ A card's `url` is not typed by the reader: it comes from a fetched
    document, a model's grounding, or a curator's paste box, and the client
    renders it as an `href`."""
    from venona.api.schemas.weburl import is_web_url

    assert not is_web_url(value)


@pytest.mark.parametrize(
    "value",
    [
        "https://example.test/a?b=c#d",
        "http://example.test",
        "HTTPS://EXAMPLE.TEST/x",
    ],
)
def test_a_web_url_is_kept(value: str) -> None:
    from venona.api.schemas.weburl import is_web_url

    assert is_web_url(value)


def test_a_response_model_drops_a_dangerous_url_rather_than_500ing() -> None:
    """A response model that RAISED would turn one bad row in the corpus into a
    500 for the whole card batch. The link disappears; the card renders."""
    from venona.api.schemas.entity import Grounding

    assert Grounding(url="javascript:alert(1)").url is None
    assert Grounding(url="https://example.test/a").url == "https://example.test/a"


def test_a_request_model_refuses_a_dangerous_url() -> None:
    """The curator can be told what is wrong, so 422 is the honest answer."""
    from pydantic import ValidationError
    from venona.api.schemas.ingest import IngestRequest

    with pytest.raises(ValidationError):
        IngestRequest(url="javascript:alert(1)")
    assert IngestRequest(url="https://example.test/story").url == "https://example.test/story"
