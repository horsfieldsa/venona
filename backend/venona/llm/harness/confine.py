"""Restrict a model process to a read set and a write set before it execs.

WHY THIS EXISTS. Claude Code is safe here by accident of how it is invoked:
`--tools ""` on every task but four, so there is nothing to read a file with.
Codex has no equivalent -- its default model's tool mode is code execution, and
`--sandbox read-only` restricts writes and network while permitting READS by
design. So a Codex call can read the repository, the environment, and the
credential this app stores on disk, and the grounding step that must quote a
document verbatim would be reading more than we handed it.

WHY LANDLOCK AND NOT BUBBLEWRAP. bwrap needs `CLONE_NEWUSER`, which moby's
default seccomp profile blocks without `CAP_SYS_ADMIN`, and the production
instance's own logs confirm it is refused there. Landlock is self-applied,
needs no privilege at all, and its syscalls are unconditionally permitted by
that same profile. It is also INHERITED ACROSS EXECVE, which is the whole
trick: a wrapper can restrict itself and then become the CLI, and the CLI
cannot climb back out.

WHY A SEPARATE SCRIPT RATHER THAN `preexec_fn`. `preexec_fn` runs between fork
and exec in a process that has the parent's threads' locks and none of its
threads, and this API serves requests on an event loop with a thread pool
under it. This file imports `ctypes`, `os` and `sys` and nothing else so it can
be handed to any interpreter as a plain path.

Run as: `python3 confine.py <reads> <writes> -- <argv...>`, colon-separated
paths. Exits 97 if the restriction could not be established, so a caller can
tell "the model refused" from "we could not confine it and would not run it
unconfined".
"""

from __future__ import annotations

import ctypes
import os
import struct
import sys

#: The three Landlock syscalls, x86-64/aarch64. They have the same numbers on
#: both, which is why they are constants rather than a lookup.
_CREATE_RULESET = 444
_ADD_RULE = 445
_RESTRICT_SELF = 446

_PR_SET_NO_NEW_PRIVS = 38
_LANDLOCK_CREATE_RULESET_VERSION = 1 << 0
_LANDLOCK_RULE_PATH_BENEATH = 1

_EXECUTE = 1 << 0
_WRITE_FILE = 1 << 1
_READ_FILE = 1 << 2
_READ_DIR = 1 << 3

#: `LANDLOCK_ACCESS_FS_RESOLVE_UNIX`, ABI 9. Governs CONNECTING to a
#: pathname unix socket, which is a different right from reading the path --
#: and leaving it unhandled means permitted. Measured 2026-09-10 on an ABI 9 kernel with
#: this file: a confined process could not read a socket outside its read set
#: and could still connect to it and receive data. On a workstation that
#: reaches the session bus, the ssh agent and the docker socket, so it is an
#: escape rather than a gap.
_RESOLVE_UNIX = 1 << 16

#: What a process may do to a path it can only read. `READ_DIR` is needed to
#: LIST a directory, and a CLI that cannot list its own installation directory
#: fails in ways that read as a corrupt install. Reaching a socket is a READ
#: right here on purpose: the confinement is path-based, so a socket inside a
#: granted directory is reachable and one outside every granted directory is
#: not.
READ_ACCESS = _EXECUTE | _READ_FILE | _READ_DIR | _RESOLVE_UNIX

#: Read, plus every bit that modifies. Spelled out rather than a mask so adding
#: one is a deliberate edit -- and the list must stay COMPLETE, because a bit
#: the ruleset handles and this does not grant is denied silently. `truncate`
#: is the one that proves the point: without it a CLI starts, reads its
#: configuration, reports itself signed in, and then hangs, because SQLite
#: truncates its journal on every write and a refused `ftruncate` surfaces as
#: `disk I/O error` rather than as a permission problem.
WRITE_ACCESS = (
    READ_ACCESS
    | _WRITE_FILE
    | (1 << 4)  # remove_dir
    | (1 << 5)  # remove_file
    | (1 << 6)  # make_char
    | (1 << 7)  # make_dir
    | (1 << 8)  # make_reg
    | (1 << 9)  # make_sock
    | (1 << 10)  # make_fifo
    | (1 << 11)  # make_block
    | (1 << 12)  # make_sym
    | (1 << 13)  # refer: rename or link between two granted directories
    | (1 << 14)  # truncate
    | (1 << 15)  # ioctl_dev
)

#: The rights that mean anything on a FILE. Everything else in the set is
#: directory-only, and `add_rule` returns EINVAL -- failing the entire
#: ruleset -- when a directory right is requested on a regular file. So a read
#: set naming a file (`/proc/cpuinfo`, `/etc/resolv.conf`) rather than a
#: directory takes the whole restriction down, and the symptom is every call
#: exiting 97 with one path named.
FILE_ACCESS = (
    _EXECUTE
    | _WRITE_FILE
    | _READ_FILE
    | (1 << 14)  # truncate
    | (1 << 15)  # ioctl_dev
    | _RESOLVE_UNIX
)

#: Exit code for "the restriction could not be established". Chosen out of the
#: range any of the CLIs use so it cannot be confused with a model failure.
CANNOT_CONFINE = 97


#: The two kernel structs, packed by hand.
#:
#: `struct landlock_path_beneath_attr` is `__attribute__((packed))`, and a
#: ctypes Structure carrying `_pack_` now warns that its layout is
#: ambiguous -- it is slated to become an error. `=` is native byte order with
#: standard sizes and NO alignment padding, which is exactly what packed means,
#: so these say the same thing without depending on how ctypes resolves it.
_RULESET_ATTR = "=Q"  # __u64 handled_access_fs
_PATH_BENEATH_ATTR = "=Qi"  # __u64 allowed_access; __s32 parent_fd


def _buffer(layout: str, *values: int) -> ctypes.Array[ctypes.c_char]:
    """A writable buffer holding one packed struct, ready for `byref`."""
    packed = struct.pack(layout, *values)
    return ctypes.create_string_buffer(packed, len(packed))


def _syscall(number: int, *args: object) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return int(libc.syscall(ctypes.c_long(number), *args))


def abi_version() -> int:
    """The kernel's Landlock ABI, or a negative number if it has none."""
    return _syscall(
        _CREATE_RULESET,
        None,
        ctypes.c_size_t(0),
        ctypes.c_uint32(_LANDLOCK_CREATE_RULESET_VERSION),
    )


def _handled_bits(version: int) -> int:
    """Every filesystem bit THIS kernel knows, and not one more.

    BOTH DIRECTIONS ARE FAILURES, which is why this is a table rather than a
    ceiling. Asking to handle a bit the kernel does not define makes
    `create_ruleset` return EINVAL, so the whole restriction fails rather than
    degrading -- verified on an ABI 9 kernel, where `0x1FFFF` is accepted and
    `0x3FFFF` is EINVAL. And a bit the kernel DOES define that is left
    unhandled is simply permitted, which is a hole with nothing to report it.

    The version-to-bits map, from `linux/landlock.h`:

    | ABI | kernel   | added                    | mask      |
    |-----|----------|--------------------------|-----------|
    | 1   | 5.13     |                          | `0x1FFF`  |
    | 2   | 5.19     | `REFER`                  | `0x3FFF`  |
    | 3   | 6.2      | `TRUNCATE`               | `0x7FFF`  |
    | 4   | 6.7      | network rights only      | `0x7FFF`  |
    | 5   | 6.10     | `IOCTL_DEV`              | `0xFFFF`  |
    | 9   | 6.18     | `RESOLVE_UNIX`           | `0x1FFFF` |

    ABI 4 is the one that reads like an off-by-one and is not: it added
    `handled_access_net`, no filesystem bit. Ubuntu 24.04 LTS ships 6.8, so
    treating it as 5 would have refused every ruleset on the most common
    long-term-support kernel there is.
    """
    if version <= 1:
        return 0x1FFF
    if version == 2:
        return 0x3FFF
    if version <= 4:
        return 0x7FFF
    if version <= 8:
        return 0xFFFF
    return 0x1FFFF


def supported() -> tuple[bool, str]:
    """Whether this kernel would actually accept the ruleset we build.

    A DRY RUN, not a version comparison. The ABI number says which bits exist;
    it does not say that the mask this file composes from them is one the
    kernel will take. Getting that wrong is the worst failure this mechanism
    has, because it is silent in the direction that matters: the wrapper
    refuses, the call dies with an exit code the caller reads as a model
    failure, and every surface still reports the sandbox as working.

    Cheap enough to call on the health path -- one syscall and a close.
    """
    version = abi_version()
    if version < 1:
        return False, "this kernel has no Landlock"
    attr = _buffer(_RULESET_ATTR, _handled_bits(version))
    fd = _syscall(
        _CREATE_RULESET,
        ctypes.byref(attr),
        ctypes.c_size_t(len(attr)),
        ctypes.c_uint32(0),
    )
    if fd < 0:
        return False, f"this kernel refused the ruleset (errno {ctypes.get_errno()})"
    os.close(fd)
    return True, f"Landlock ABI {version}"


def confine(reads: list[str], writes: list[str]) -> tuple[bool, str]:
    """Apply the restriction to THIS process. It survives the exec that follows."""
    version = abi_version()
    if version < 1:
        return False, "this kernel has no Landlock"

    handled = _handled_bits(version)
    attr = _buffer(_RULESET_ATTR, handled)
    ruleset = _syscall(
        _CREATE_RULESET,
        ctypes.byref(attr),
        ctypes.c_size_t(len(attr)),
        ctypes.c_uint32(0),
    )
    if ruleset < 0:
        return False, f"could not create a ruleset (errno {ctypes.get_errno()})"

    try:
        for paths, access in ((reads, READ_ACCESS), (writes, WRITE_ACCESS)):
            for path in paths:
                # A path that is not there is skipped rather than refused. The
                # sets are written for two harnesses and two deployments, and a
                # missing `/lib64` on one of them is not a reason to run
                # unconfined.
                if not os.path.exists(path):
                    continue
                # Directory rights on a file are EINVAL, and EINVAL here
                # fails the whole ruleset rather than this one path.
                allowed = access if os.path.isdir(path) else access & FILE_ACCESS
                try:
                    handle = os.open(path, os.O_PATH | os.O_CLOEXEC)
                except OSError as exc:
                    # Between the check above and here the path can vanish or
                    # turn out to be unreadable. Reported rather than raised:
                    # an exception escaping here exits 1 with a traceback, and
                    # the caller then cannot tell "could not confine" from
                    # "the model failed" -- which is the entire purpose of
                    # having a distinct exit code.
                    return False, f"could not allow {path} ({exc.strerror})"

                rule = _buffer(_PATH_BENEATH_ATTR, allowed & handled, handle)
                code = _syscall(
                    _ADD_RULE,
                    ctypes.c_int(ruleset),
                    ctypes.c_uint32(_LANDLOCK_RULE_PATH_BENEATH),
                    ctypes.byref(rule),
                    ctypes.c_uint32(0),
                )
                os.close(handle)
                if code < 0:
                    return False, f"could not allow {path} (errno {ctypes.get_errno()})"

        # Required before restrict_self for an unprivileged process, and the
        # reason this needs no capability: it promises the kernel that no exec
        # from here can gain privilege, so the restriction cannot be shed by
        # exec'ing something setuid.
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            return False, f"could not set no_new_privs (errno {ctypes.get_errno()})"

        if _syscall(_RESTRICT_SELF, ctypes.c_int(ruleset), ctypes.c_uint32(0)) < 0:
            return False, f"could not apply the ruleset (errno {ctypes.get_errno()})"
    finally:
        os.close(ruleset)

    return True, f"confined (Landlock ABI {version})"


def _main(argv: list[str]) -> int:
    # The length check comes FIRST. Reading argv[1] before it turns a malformed
    # invocation into an IndexError and exit 1, which is the one exit code this
    # program must never produce for its own reasons.
    if len(argv) < 5 or argv[3] != "--":
        print("confine: expected <reads> <writes> -- <argv>", file=sys.stderr)
        return CANNOT_CONFINE
    split = 3

    reads = [p for p in argv[1].split(":") if p]
    writes = [p for p in argv[2].split(":") if p]
    command = argv[split + 1 :]
    if not command:
        print("confine: no command", file=sys.stderr)
        return CANNOT_CONFINE

    ok, detail = confine(reads, writes)
    if not ok:
        print(f"confine: {detail}", file=sys.stderr)
        return CANNOT_CONFINE

    try:
        os.execvp(command[0], command)
    except OSError as exc:
        # We are already restricted here, so an escaping traceback may not be
        # able to read its own source to render itself. Say what happened and
        # keep the exit code meaningful.
        print(f"confine: could not exec {command[0]}: {exc.strerror}", file=sys.stderr)
        return CANNOT_CONFINE
    return CANNOT_CONFINE  # unreachable: execvp either replaces us or raises


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
