#!/usr/bin/env python3
"""Free the dev ports, but only from processes that belong to this repo.

A dev server orphaned by a previous run -- Ctrl-C at the wrong moment, a crashed
terminal, a backgrounded `make dev` -- leaves :8000 bound, and uvicorn's
"Address already in use" does not say who is holding it. That is a small paper
cut that recurs forever, so `make dev` reclaims its own ports first.

Ownership is decided by whether the process is ROOTED IN THIS REPO -- its cwd or
its executable lives under the repo root -- not by pattern-matching the command
line. Pattern matching is too weak in both directions: uvicorn's `--reload`
supervisor spawns a `multiprocessing.spawn` child whose argv mentions neither
uvicorn nor venona, and conversely a random unrelated process could easily
contain the word "vite".

Anything not rooted here is reported and left alone. Killing a stranger's
process because it happened to want port 8000 would be far worse than failing.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def listeners(port: int) -> set[int]:
    """PIDs *listening* on the port.

    `-sTCP:LISTEN` is essential. Without it lsof also returns every CLIENT with
    an open connection to the port -- so a browser tab sitting on
    localhost:5173, or a curl in flight, gets reported as holding the port and
    blocks the dev server. (Observed: a headless Chrome doing screenshots.)
    """
    try:
        out = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return set()
    return {int(line) for line in out.split() if line.strip().isdigit()}


def _link(pid: int, name: str) -> Path | None:
    try:
        return Path(os.readlink(f"/proc/{pid}/{name}"))
    except OSError:
        return None


def cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return " ".join(raw.decode(errors="replace").split("\0")).strip()


def ppid_of(pid: int) -> int | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[-1].split()
        return int(fields[1])  # PPid is the 2nd field after the comm field
    except (OSError, IndexError, ValueError):
        return None


def _rooted_here(pid: int) -> bool:
    for name in ("cwd", "exe"):
        target = _link(pid, name)
        if target is None:
            continue
        try:
            target.relative_to(REPO_ROOT)
            return True
        except ValueError:
            continue
    return False


def owned_by_repo(pid: int) -> bool:
    """True if the process, or any ancestor, is rooted in this repo.

    Covers the uvicorn reloader, its `multiprocessing.spawn` child, the vite
    node process, and anything `make dev` launched -- without relying on argv
    text. The ancestor walk matters because a spawned child can have a cwd or
    exe outside the repo while plainly belonging to our server.
    """
    seen: set[int] = set()
    current: int | None = pid
    while current and current > 1 and current not in seen:
        seen.add(current)
        if _rooted_here(current):
            return True
        current = ppid_of(current)
    return False


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate(pid: int) -> None:
    """SIGTERM, then SIGKILL if it does not go quietly."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    for _ in range(20):
        time.sleep(0.1)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def main(argv: list[str]) -> int:
    ports = [int(a) for a in argv[1:]] or [8000, 5173]

    # Snapshot ownership for EVERY holder before killing anything. Terminating a
    # parent takes its children with it, and a dead process has no readable
    # /proc links -- inspecting afterwards would misreport those children as
    # strangers and abort the run. This bug was real; do not reorder.
    snapshot: list[tuple[int, int, bool, str]] = []
    for port in ports:
        for pid in sorted(listeners(port)):
            if pid == os.getpid():
                continue
            snapshot.append((port, pid, owned_by_repo(pid), cmdline(pid) or "<unknown>"))

    reclaimed = 0
    for port, pid, owned, _ in snapshot:
        if owned and alive(pid):
            print(f"reclaiming :{port} from stale venona process {pid}")
            terminate(pid)
            reclaimed += 1

    if reclaimed:
        time.sleep(0.7)  # let the kernel release the sockets

    # Only report a stranger if it is STILL holding the port. A child that died
    # alongside its reclaimed parent is not a blocker.
    still_held = {p: listeners(p) for p in ports}
    blocked = [
        (port, pid, cmd)
        for port, pid, owned, cmd in snapshot
        if not owned and pid in still_held.get(port, set()) and alive(pid)
    ]

    if blocked:
        print()
        for port, pid, cmd in blocked:
            print(f"port {port} is held by a process that is NOT part of this repo:")
            print(f"    pid {pid}: {cmd[:160]}")
        print()
        print("Refusing to kill it. Either free the port yourself, or run on another:")
        print("    make dev API_PORT=8001")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
