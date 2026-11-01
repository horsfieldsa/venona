"""Contract C6's two frozen vocabularies, and the copies of them.

`api/schemas/run.py` spells the run statuses and event types out as `Literal`
unions so they reach the OpenAPI document as enums — without that the wire types
them as bare strings, the generated client cannot narrow them, and every
consumer re-declares the same union by hand.

Spelling them out means a duplicate. `run_types` makes the same trade for
`RESEARCH_STAGES` and gives the reason: importing would close a cycle, and an
ENFORCED duplicate beats one nothing checks. This is the enforcement.
"""

from __future__ import annotations

from typing import get_args

import pytest
from venona.api.schemas import run as schema
from venona.db import core as db
from venona.services import run_service


def test_the_status_union_matches_what_the_service_writes() -> None:
    """A status the service can write and the schema does not name is a 500 at
    serialization — on the runs list, which is where someone goes to find out
    what went wrong."""
    assert set(get_args(schema.RunStatus)) == {
        "queued",
        "running",
        *run_service.TERMINAL_STATUSES,
    }


def test_the_event_union_matches_what_the_log_accepts() -> None:
    assert set(get_args(schema.RunEventType)) == set(run_service.EVENT_TYPES)


def test_the_started_union_is_the_two_a_launch_can_return() -> None:
    """A launch returns `running` or `queued` and nothing else: a refusal raises
    `LaneBusy` and never becomes a payload."""
    assert set(get_args(schema.RunStarted.model_fields["status"].annotation)) == {
        "running",
        "queued",
    }


@pytest.mark.negative
def test_an_unknown_event_type_is_refused_at_the_write(conn: db.Conn) -> None:
    """⭐ `EVENT_TYPES` was declared and read by nothing, so C6's frozen list was
    a comment. A type outside it reaches a client with no branch to run — the
    event is silently dropped from the transcript, which reads as a stage that
    went quiet rather than as a bug."""
    conn.execute(
        "INSERT INTO runs (id, kind, status, started_at) VALUES ('run:v','cli','running','n')"
    )
    with pytest.raises(ValueError, match="frozen"):
        run_service.append_event(
            conn, run_id="run:v", type_="stage_skipped", stage=None, data={}, now="n"
        )


def test_every_declared_event_type_is_writable(conn: db.Conn) -> None:
    """The other direction: a validation that refuses a type the pipeline emits
    would kill runs. Every name in the frozen list has to pass."""
    conn.execute(
        "INSERT INTO runs (id, kind, status, started_at) VALUES ('run:w','cli','running','n')"
    )
    for index, type_ in enumerate(run_service.EVENT_TYPES):
        event = run_service.append_event(
            conn, run_id="run:w", type_=type_, stage=None, data={}, now="n"
        )
        assert event["type"] == type_
        assert event["seq"] == index + 1
