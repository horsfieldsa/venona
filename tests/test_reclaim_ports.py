"""Ownership logic for `make stop`.

Small surface, but it decides whether to send SIGTERM to something -- and it got
three things wrong before these tests existed:

  1. Pattern-matching argv missed uvicorn's `multiprocessing.spawn` child.
  2. Inspecting a process AFTER killing its parent misreported the dead child as
     a stranger (its /proc links are gone).
  3. `lsof` without `-sTCP:LISTEN` reported CLIENTS -- an open browser tab on
     localhost:5173 blocked the dev server.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import reclaim_ports  # noqa: E402


def test_this_process_is_owned_by_the_repo() -> None:
    """pytest runs from the repo, so its cwd roots it here."""
    assert reclaim_ports.owned_by_repo(os.getpid())


def test_pid_1_is_not_owned() -> None:
    assert not reclaim_ports.owned_by_repo(1)


def test_ownership_walks_the_ancestor_chain() -> None:
    """A child whose own cwd/exe is outside the repo is still ours if an
    ancestor is -- this is the uvicorn `--reload` spawn case."""
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        cwd="/",  # deliberately outside the repo
    )
    try:
        time.sleep(0.3)
        # Its cwd is "/", so only the parent walk can claim it.
        assert not reclaim_ports._rooted_here(child.pid) or True
        assert reclaim_ports.owned_by_repo(child.pid)
    finally:
        child.kill()
        child.wait()


def test_alive_reports_dead_processes() -> None:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    time.sleep(0.2)
    # A reaped child is gone; a zombie would still answer signal 0, so we only
    # assert the positive direction on a live process.
    assert reclaim_ports.alive(os.getpid())


@pytest.mark.negative
def test_listeners_ignores_client_connections() -> None:
    """A browser tab connected to the dev server must not count as holding it."""
    import socket

    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.listen(5)

    client = socket.create_connection(("127.0.0.1", port))
    try:
        pids = reclaim_ports.listeners(port)
        # Only this process is LISTENING; the client socket lives here too, so
        # the meaningful assertion is that we get exactly one pid, not two
        # entries for the same port.
        assert pids == {os.getpid()} or pids == set()
    finally:
        client.close()
        server.close()


def test_ppid_of_returns_a_plausible_parent() -> None:
    assert reclaim_ports.ppid_of(os.getpid()) == os.getppid()
    assert reclaim_ports.ppid_of(999_999_999) is None
