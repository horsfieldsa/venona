"""One projection at a time, across processes.

Every pass stamps what it writes and then deletes everything not carrying its
own stamp. Two overlapping passes therefore delete each other's work: the one
that finishes second prunes against its own stamp, and whatever the first wrote
in the meantime is gone. The graph is then missing edges Postgres still implies
— recoverable by invariant 1, and silent, because nothing compares the two.

The registry cannot gate this. It is an `asyncio.Lock` in one event loop, and
`venona run` is a different process.
"""

from __future__ import annotations

import pytest
from venona.db import core as db
from venona.db.repositories import graph as repo
from venona.graph.projector import ProjectionBusy, project


def test_a_second_projection_cannot_start_while_one_is_running(
    conn: db.Conn, database_url: str
) -> None:
    """⭐ The overlap. The holder is a genuinely separate connection, which is
    what makes this the cross-PROCESS guarantee and not an in-process one."""
    owner = db.connect(database_url)
    try:
        assert repo.lock_projection(owner, wait=False) is True
        assert repo.lock_projection(conn, wait=False) is False, (
            "two projections would prune each other's writes"
        )
    finally:
        repo.unlock_projection(owner)
        owner.close()


def test_the_gate_reopens_when_the_holder_releases(conn: db.Conn, database_url: str) -> None:
    """A gate that does not reopen is worse than no gate: it strands every later
    projection, and the graph then never converges at all."""
    owner = db.connect(database_url)
    try:
        assert repo.lock_projection(owner, wait=False) is True
        repo.unlock_projection(owner)
        assert repo.lock_projection(conn, wait=False) is True
        repo.unlock_projection(conn)
    finally:
        owner.close()


def test_a_dead_holder_does_not_wedge_the_gate(conn: db.Conn, database_url: str) -> None:
    """The lease's guarantee, for the same reason: a session lock dies with its
    connection, however it dies. Nothing to time out, nothing to heartbeat."""
    owner = db.connect(database_url)
    assert repo.lock_projection(owner, wait=False) is True
    owner.close()
    assert repo.lock_projection(conn, wait=False) is True
    repo.unlock_projection(conn)


async def test_project_refuses_rather_than_queueing_when_asked_not_to_wait(
    conn: db.Conn, database_url: str
) -> None:
    """The curation rebuild button has a person waiting on it. Queueing behind a
    run's own projection would hold the request open for minutes.

    The driver is never touched: the gate is checked first, which is also why
    passing None here is safe.
    """
    owner = db.connect(database_url)
    try:
        repo.lock_projection(owner, wait=False)
        with pytest.raises(ProjectionBusy):
            await project(conn, None, wait=False)
    finally:
        repo.unlock_projection(owner)
        owner.close()


def test_the_projection_gate_has_its_own_namespace() -> None:
    """Three advisory locks can be held on one connection at once — the run
    lease, the lane gate and this. A shared namespace would let a lane name hash
    onto this constant and make two unrelated writers wait on each other."""
    from venona.db.repositories import run as run_repo

    namespaces = {
        repo.PROJECTION_NAMESPACE,
        run_repo.LEASE_NAMESPACE,
        run_repo.LANE_LOCK_NAMESPACE,
    }
    assert len(namespaces) == 3
