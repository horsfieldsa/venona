---
status: accepted
date: 2026-09-10
---

# 2026-09-10 — A model that runs code gets a filesystem of its own

Claude Code is safe here by accident of how it is invoked. `--tools ""` on
every task but the ones that ask for the web, and those get search and fetch
only, so there is nothing in the process that can open a file.

Codex has no equivalent. Its default model's tool mode runs code, and
`--sandbox read-only` restricts writes and network while permitting **reads by
design**. Measured on this machine, with the real executor and a real
subscription, a Codex call asked to read the repository did so:

```
[confinement off]  {'first_line': '# Non-secret configuration. Secrets go in .env.',
                    'reachable': True}
```

Two things follow. A run could read the credential this app now keeps on disk
(ADR 0217). And the grounding step whose whole contract is a verbatim quote
from a document we supplied (invariant 3) is no longer reading only what we
supplied.

## Landlock, not bubblewrap

bwrap needs `CLONE_NEWUSER`, which moby's default seccomp profile blocks
without `CAP_SYS_ADMIN`. That is not a theory about the profile: the production
instance's own logs refuse it. Landlock needs no privilege at all, its three
syscalls are unconditionally permitted by that same profile, and it is
**inherited across execve** — which is the whole mechanism. A wrapper restricts
itself and then becomes the CLI, and the CLI cannot climb back out.

The wrapper is a separate script rather than `preexec_fn` because `preexec_fn`
runs between fork and exec in a process holding the parent's threads' locks and
none of its threads, and this API serves requests on an event loop with a
thread pool under it. `llm/harness/confine.py` imports `ctypes`, `os`, `struct`
and `sys` and nothing else, so any interpreter can be handed its path.

Same call, same account, confinement on:

```
[confinement auto] {'first_line': None, 'reachable': False}
```

## Opportunistic, and it says so

A kernel without Landlock is a deployment fact, not a misconfiguration.
Refusing to think at all on one would take the app down for a defence the
active harness may not even need, so the call runs unconfined instead.

What must never happen quietly is the reverse — believing a call was confined
when the kernel refused. That failure is invisible from everywhere else,
because the call succeeds either way. So `HarnessHealth` carries `confined` and
`confinement_detail`, `/api/health` serves the first, and `/admin/model` has a
section headed *what it can reach* that is drawn in all three states, including
the one where everything worked.

`llm.confine` is `auto` or `off`, and it is **frozen** against HTTP writes for
the same reason `cli_path` is: it is a safety property, and the page that would
edit it is reachable over the network.

## The bit table is measured, and both ways of getting it wrong are silent

`handled_access_fs` has to name exactly the filesystem rights the running
kernel defines. Both errors are quiet and they fail in opposite directions.

Ask for a bit the kernel does not define and `create_ruleset` returns EINVAL:
the restriction does not degrade, it refuses. Leave a bit the kernel does
define unhandled and it is simply permitted, and nothing reports the hole.

| ABI | kernel | added | mask |
|---|---|---|---|
| 1 | 5.13 | | `0x1FFF` |
| 2 | 5.19 | `REFER` | `0x3FFF` |
| 3 | 6.2 | `TRUNCATE` | `0x7FFF` |
| 4 | 6.7 | **network rights only** | `0x7FFF` |
| 5 | 6.10 | `IOCTL_DEV` | `0xFFFF` |
| 9 | 6.18 | `RESOLVE_UNIX` | `0x1FFFF` |

ABI 4 reads like an off-by-one and is not. Ubuntu 24.04 LTS ships 6.8, so
treating it as 5 refuses every ruleset on the most common long-term-support
kernel there is — and the refusal arrives as exit 97, which `classify` reads
as terminal, which opens the breaker permanently.

**`RESOLVE_UNIX` is the one that mattered.** Connecting to a pathname unix
socket is a different right from reading its path, and until ABI 9 there was
no way to govern it. Measured on this machine, against the real file, with the
socket outside every granted directory:

```
before   CONNECTED, got: b'SECRET'      READ denied
after    CONNECT DENIED [Errno 13]      READ denied
```

A confined model could not read the socket and could still talk to it. On a
workstation that reaches the session bus, the ssh agent and the docker socket.
A socket *inside* a granted directory still connects, which is the semantics
this wants: the confinement is path-based.

**`supported()` is a dry run, not a version comparison.** The ABI number says
which bits exist; only building the ruleset says the kernel takes the mask this
composes from them. `health()` reports what that dry run established, because
the alternative failed in the worst available direction — nothing running while
every surface said the sandbox was working.

**Directory rights on a file are EINVAL**, and EINVAL fails the whole ruleset
rather than the one path. A read set naming `/proc/cpuinfo` rather than a
directory therefore takes the entire restriction down. Non-directories get a
file-only mask.

## Two things the path sets cost, both found by running it

**`truncate` is not in `WRITE_ACCESS` by accident.** The ruleset handles every
filesystem bit the kernel knows, so any bit not granted is denied. Without
`LANDLOCK_ACCESS_FS_TRUNCATE` the CLI starts, reads its configuration, reports
itself signed in, and then hangs — SQLite truncates its journal on every write,
and a refused `ftruncate` surfaces as `disk I/O error`, not as a permission
problem. `refer` and `ioctl_dev` are granted for the same reason.

**`/run` is in the read set for DNS.** Under systemd-resolved
`/etc/resolv.conf` is a symlink to `/run/systemd/resolve/stub-resolv.conf`, so
granting `/etc` grants the link and not the file behind it. Every name lookup
then returns `EAI_AGAIN`, which the CLI reports as a dropped stream and retries
five times before giving up — a failure that reads as the provider being
unreachable rather than as a restriction we applied.

Both were found by running a real call and reading its log. Neither is
inferable from the documentation of either project.

## The adjacency this depends on

`data_dir` is never granted whole — only the prompt cache under it — so the
credential directory beside the agent's home is unreachable even though it is
one level away. That adjacency is why ADR 0217 put the credential *beside* the
agent's home rather than inside it, and `tests/test_confinement.py` asserts it
by starting a process and asking the process what it can reach.

## Consequences

- Adding a harness now includes deciding `wants_confinement`, and a harness
  that runs code and says `False` is a claim someone has to defend.
- `_SYSTEM_READS` grants `/proc/self` and a named handful of files, never
  `/proc` whole: the model runs as the same uid as the API, so on a host with
  `kernel.yama.ptrace_scope=0` the whole of `/proc` would put
  `/proc/<uvicorn pid>/environ` in reach — the database URL, the session
  secret and the management token, which is exactly the list the child
  environment allowlist exists to withhold.
- The path sets are shared (`confinement_for`) rather than per driver. What
  differs between the two is only which configuration directory is writable.
- A CLI that Venona has not pointed anywhere writes to its own default home, so
  the write set follows the directory the child will **actually** use, not the
  one Venona would prefer. Getting this wrong stops the CLI before it reaches
  the question of who it is signed in as. That directory is read off **the
  environment the child receives**, never this process's: a harness whose
  `child_env` is an allowlist drops the variable an operator set, and a plan
  built from the parent's environment then grants a directory nobody uses.
- A home that cannot be established — no `HOME` in the child's environment, or
  a relative path, or `/` — yields no plan at all and the call runs
  unconfined. A relative one is the dangerous case: it would resolve against
  this process's working directory, which is the repository.
