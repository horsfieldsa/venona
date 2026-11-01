"""The restriction actually restricts, proven by running something under it.

A confinement that silently fails open is worse than none: every surface says
the model process is locked to a filesystem, and it is not. So this does not
assert on the ruleset it built -- it starts a process and asks the process what
it can reach.

Skipped where the kernel has no Landlock. That is a real deployment state
rather than a broken one (the executor runs the call unconfined and `health()`
reports `confined: false`), and a suite that failed there would be asserting
something about the machine instead of about this code.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from venona.llm.harness import confine, confinement_for, resolve

#: What the confined child needs merely to BE a Python interpreter. Under `uv
#: run` the interpreter is a virtualenv pointing into a downloaded toolchain,
#: none of which is under `/usr` -- so a ruleset granting only system paths
#: cannot exec it, and the failure looks exactly like the denial this file is
#: trying to prove. Granting the interpreter's own roots keeps the assertions
#: about the ruleset rather than about the machine.
#:
#: The executor needs no equivalent: its wrapper starts unconfined, restricts
#: itself, and only then becomes the CLI.
INTERPRETER_READS = [
    "/usr",
    "/lib",
    "/lib64",
    "/etc",
    sys.prefix,
    sys.base_prefix,
    str(Path(os.path.realpath(sys.executable)).parent.parent),
]

pytestmark = pytest.mark.skipif(confine.abi_version() < 1, reason="this kernel has no Landlock")

REPORT = textwrap.dedent(
    """
    import os, sys
    for target in sys.argv[1:]:
        try:
            if os.path.isdir(target):
                os.listdir(target)
            else:
                open(target, "rb").read(1)
            print("ALLOWED", target)
        except PermissionError:
            print("DENIED", target)
        except FileNotFoundError:
            print("MISSING", target)
    """
)


def _under_confinement(reads: list[str], writes: list[str], targets: list[str]) -> str:
    script = Path(writes[0]) / "report.py"
    script.write_text(REPORT)
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [
            sys.executable,
            confine.__file__,
            ":".join(reads),
            ":".join(writes),
            "--",
            sys.executable,
            str(script),
            *targets,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != confine.CANNOT_CONFINE, result.stderr
    return result.stdout


def test_a_confined_process_cannot_read_outside_its_read_set(tmp_path) -> None:
    """The whole point. Under Codex the default model's tool mode runs code and
    `--sandbox read-only` permits reads by design, so without this a call could
    read the repository it is running inside."""
    home = tmp_path / "home"
    home.mkdir()
    secret = tmp_path / "credential.json"
    secret.write_text('{"token": "sk-not-yours"}')

    out = _under_confinement(INTERPRETER_READS, [str(home)], ["/usr/bin", str(secret)])
    assert "ALLOWED /usr/bin" in out, out
    assert f"DENIED {secret}" in out, out


def test_a_confined_process_can_only_write_where_it_was_allowed(tmp_path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [
            sys.executable,
            confine.__file__,
            ":".join(INTERPRETER_READS),
            str(home),
            "--",
            sys.executable,
            "-c",
            textwrap.dedent(
                f"""
                open({str(home / "ok.txt")!r}, "w").write("x")
                print("WROTE home")
                try:
                    open({str(elsewhere / "no.txt")!r}, "w").write("x")
                    print("WROTE elsewhere")
                except PermissionError:
                    print("DENIED elsewhere")
                """
            ),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "WROTE home" in result.stdout, result.stderr
    assert "DENIED elsewhere" in result.stdout, result.stdout


def test_the_credential_directory_is_not_reachable_from_the_agent_home(tmp_path) -> None:
    """The adjacency this design depends on.

    The credential lives BESIDE the agent's home rather than inside it, and the
    path sets grant `data_dir` one subdirectory at a time. So one level of
    parent traversal is all that separates them, and nothing but the ruleset
    stops it.
    """

    class _Settings:
        data_dir = tmp_path

    home = tmp_path / "harness" / "codex" / "home"
    home.mkdir(parents=True)
    credentials = tmp_path / "harness" / "codex" / "credentials"
    credentials.mkdir(parents=True)
    (credentials / "auth.json").write_text('{"token": "sk-not-yours"}')

    plan = confinement_for(
        _Settings(),
        resolve("codex"),
        child_env={"CODEX_HOME": str(home), "HOME": str(tmp_path)},
        binary=sys.executable,
    )
    assert str(credentials) not in plan.writes
    assert str(credentials) not in plan.reads
    assert str(tmp_path) not in plan.reads, "data_dir must never be granted whole"

    out = _under_confinement(
        [*plan.reads, *INTERPRETER_READS], [str(home)], [str(credentials / "auth.json")]
    )
    assert f"DENIED {credentials / 'auth.json'}" in out, out


def test_the_harness_that_needs_no_restriction_asks_for_none() -> None:
    """Claude Code is invoked with `--tools ""` on every task but four, so it
    has nothing to read a file with. Confining it would buy nothing and could
    only break an install that works."""
    assert resolve("claude_code").wants_confinement is False
    assert resolve("codex").wants_confinement is True


def test_the_handled_bits_match_what_this_kernel_actually_accepts() -> None:
    """Both directions of getting the mask wrong are failures, and both are quiet.

    Ask for a bit the kernel does not define and `create_ruleset` returns
    EINVAL -- the whole restriction refuses rather than degrading, so every
    call dies with an exit code the caller reads as a model failure. Leave a
    bit the kernel DOES define unhandled and it is simply permitted, which is a
    hole with nothing to report it.

    ABI 4 is the one that reads like an off-by-one and is not: it added network
    rights and no filesystem bit. Ubuntu 24.04 LTS ships the 6.8 kernel, so
    treating it as 5 refuses every ruleset on the most common long-term-support
    kernel there is.
    """
    assert confine._handled_bits(1) == 0x1FFF
    assert confine._handled_bits(2) == 0x3FFF
    assert confine._handled_bits(3) == 0x7FFF
    assert confine._handled_bits(4) == 0x7FFF, "ABI 4 added network rights, not an FS bit"
    assert confine._handled_bits(5) == 0xFFFF
    assert confine._handled_bits(8) == 0xFFFF
    assert confine._handled_bits(9) == 0x1FFFF, "ABI 9 added RESOLVE_UNIX"

    # And the running kernel agrees: the mask this composes is one it takes.
    ok, detail = confine.supported()
    assert ok, detail


def test_reaching_a_unix_socket_is_a_right_the_ruleset_governs() -> None:
    """Connecting to a socket is not the same right as reading its path.

    Measured before this was handled: a confined process could not read a
    socket outside its read set and could still connect to it and receive
    data. On a workstation that reaches the session bus, the ssh agent and the
    docker socket, so it is an escape rather than a gap -- and the model
    service this confinement exists for is the one whose default tool mode
    runs code.
    """
    if confine.abi_version() < 9:
        pytest.skip("RESOLVE_UNIX needs Landlock ABI 9")
    assert confine.READ_ACCESS & (1 << 16), "RESOLVE_UNIX is handled but never granted"
    assert confine.WRITE_ACCESS & (1 << 16)


def test_a_kernel_that_would_refuse_the_ruleset_is_reported_as_unconfined() -> None:
    """`supported()` is a DRY RUN, not a version comparison.

    The ABI number says which bits exist; only building the ruleset says the
    kernel will accept the one this file composes. Getting that distinction
    wrong is the worst failure the mechanism has, because it is silent in the
    direction that matters: nothing runs, and every surface says the sandbox
    is working.
    """
    import venona.llm.harness.confine as module

    real = module._handled_bits
    try:
        module._handled_bits = lambda _v: 0x3FFFF  # a bit no kernel defines
        ok, detail = module.supported()
        assert not ok, "a ruleset the kernel refuses was reported as supported"
        assert "refused" in detail
    finally:
        module._handled_bits = real
