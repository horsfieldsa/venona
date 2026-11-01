"""The only place a model-service credential is read or written.

`config_service` owns the editable documents for the same reason this owns
credentials: the allowlist, the validation, the atomic write and the cache
invalidation have to live together, because a second writer that skips one of
them is invisible at the call site.

**Two kinds, stored differently, and the difference is not cosmetic.**

A SUBSCRIPTION credential rotates -- the CLI refreshes its own token -- so the
CLI must own the file and venona must never hold a copy that goes stale. It
lives in an instance-scoped home the child is pointed at, and no route here can
read it back.

An API KEY does not rotate and cannot live in a CLI's own store for every
vendor, so it is a row, following `notify_destinations`: plaintext (encrypting
it would move the problem to where the key lives, which is the environment,
which is where this started), never returned by any route, and shown only as a
hint derived at read time.

**The home is beside the credential, not around it.** A coding agent pointed at
a directory can read what is in it, so the credential directory is a sibling of
the agent's home rather than a child of it.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import os
import stat
from dataclasses import replace
from pathlib import Path
from typing import Any

from venona.db import core as db
from venona.llm.harness import Credential, Harness

log = logging.getLogger(__name__)

#: Cached per harness key so `_child_env` -- which runs on every call and every
#: probe -- does not open a database connection inside a subprocess spawn.
#: Invalidated by the admin route, exactly as `invalidate_health` is.
_SECRETS: dict[str, str] = {}
_LOADED = False


def instance_root(settings: Any, harness: Harness) -> Path:
    return Path(settings.data_dir) / "harness" / harness.key


def home_dir(settings: Any, harness: Harness) -> Path:
    """Where the CLI keeps its own state, and what the child is pointed at.

    Per instance rather than shared: one token across several stacks contends
    on one account's rate limits, which is the condition `docs/pm/PROGRAM.md`
    already records and this closes.
    """
    return instance_root(settings, harness) / "home"


def credentials_dir(settings: Any, harness: Harness) -> Path:
    return instance_root(settings, harness) / "credentials"


def ensure_dirs(settings: Any, harness: Harness) -> Path:
    """Both directories, 0700, created before a child is ever pointed at one.

    A CLI handed a home that does not exist can exit non-zero with empty
    stdout, which reads as a broken install rather than as a missing directory.
    """
    home = home_dir(settings, harness)
    home.mkdir(parents=True, exist_ok=True)
    creds = credentials_dir(settings, harness)
    creds.mkdir(parents=True, exist_ok=True)
    for path in (home, creds):
        os.chmod(path, stat.S_IRWXU)
    return home


# ── the CLI's own store, kept as a row (ADR 0234) ────────────────────


def signed_in_home(settings: Any, harness: Harness) -> Path:
    """WHERE THE CREDENTIAL ACTUALLY IS, which is not always Venona's own home.

    `credential_for` points the child at the instance-scoped home only once
    something is written there, so `codex login` in a shell -- the documented
    way -- leaves the credential in the CLI's DEFAULT home under `$HOME`.
    Reading only Venona's let a live Codex sign-in go uncaptured while
    `harness_homes` sat empty, reading as "nothing to keep"."""
    own = home_dir(settings, harness)
    with contextlib.suppress(OSError):
        if own.exists() and any(own.iterdir()):
            return own
    base = os.environ.get("HOME", "")
    if base:
        home = Path(base).expanduser()
        if home.is_absolute() and home != Path("/"):
            return home / harness.home_dirname
    return own


def _credential_files(settings: Any, harness: Harness) -> dict[str, bytes]:
    """The declared credential files that exist, as bytes -- nothing promises
    they are UTF-8, and a wrong guess corrupts the thing being kept."""
    home = signed_in_home(settings, harness)
    out: dict[str, bytes] = {}
    for name in getattr(harness, "credential_paths", ()):
        # Containment, as `config_service._path_for` has: a `..` in a driver's
        # declared name would reach outside the home.
        candidate = (home / name).resolve()
        if not candidate.is_relative_to(home.resolve()) or not candidate.is_file():
            continue
        with contextlib.suppress(OSError):
            out[name] = candidate.read_bytes()
    return out


def _digest(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(files[name])
        digest.update(b"\0")
    return digest.hexdigest()[:16]


#: The digest this process last saw in each home, so the per-call check costs no
#: round trip. Seeded by `restore`, updated by `capture`; a miss costs one query.
_LAST_DIGEST: dict[str, str] = {}


def capture_if_changed(settings: Any, harness: Harness) -> bool:
    """The hot path's entry point, called after every model call.

    A rotation happens DURING a call, so this runs right after one and must be
    cheap: the staleness question goes to the FILESYSTEM first, and only a real
    change opens a connection. Failures are logged, not raised -- raising would
    fail the call that just succeeded.
    """
    try:
        files = _credential_files(settings, harness)
        if not files:
            return False
        digest = _digest(files)
        if _LAST_DIGEST.get(harness.key) == digest:
            return False

        from venona.db import core as db
        from venona.settings import database_dsn

        conn = db.connect(database_dsn())
        try:
            return capture(conn, settings, harness)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - never fail a call over bookkeeping
        log.warning("could not keep the %s credential", harness.key, exc_info=True)
        return False


def capture(conn: Any, settings: Any, harness: Harness) -> bool:
    """Keep this service's credential, if it changed. Returns whether it wrote.

    **AN EMPTY HOME NEVER OVERWRITES A STORED ONE**, which is the hazard this
    design introduces. Two instances overlap for sixty seconds during a
    zero-downtime deploy, and one with a cleared or not-yet-restored home would
    write "no credential" over the only copy -- the next boot comes up signed out
    with nothing saying why. `tests/test_harness_home.py` holds it.
    """
    files = _credential_files(settings, harness)
    if not files:
        return False

    from venona.db.repositories import harness as repo

    digest = _digest(files)
    if repo.home_digest(conn, harness=harness.key) == digest:
        return False
    payload = {name: base64.b64encode(blob).decode() for name, blob in files.items()}
    repo.upsert_home(conn, harness=harness.key, files=json.dumps(payload), digest=digest)
    _LAST_DIGEST[harness.key] = digest
    log.info("harness home captured: %s (%d file(s))", harness.key, len(files))
    return True


def restore(conn: Any, settings: Any, harness: Harness) -> bool:
    """Write this service's stored credential back into its home. Returns whether
    it wrote.

    Called once from the lifespan, not `ensure_dirs`, which runs on every spawn.

    NEVER OVERWRITES A LIVE FILE: a home already holding the credential is one
    the CLI may have rotated since the row was written, and an older copy over
    it is how a working instance signs itself out.
    """
    from venona.db.repositories import harness as repo

    row = repo.home(conn, harness=harness.key)
    if row is None:
        return False
    try:
        payload = json.loads(row["files"])
    except ValueError:
        log.warning("harness home for %s is not readable; leaving it alone", harness.key)
        return False

    home = home_dir(settings, harness)
    home.mkdir(parents=True, exist_ok=True)
    wrote = False
    for name, encoded in payload.items():
        target = (home / name).resolve()
        if not target.is_relative_to(home.resolve()) or target.exists():
            continue
        with contextlib.suppress(OSError, ValueError):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(encoded))
            # The vendor wrote these 0600 and so does Venona. A credential the
            # rest of the container can read is a credential the model process
            # can read, which is the whole point of `confine.py`.
            os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
            wrote = True
    # Seed the hot path's memory either way, including when nothing was written
    # because the home was already populated -- otherwise a needless capture
    # fires on the very first call.
    with contextlib.suppress(OSError):
        _LAST_DIGEST[harness.key] = _digest(_credential_files(settings, harness))
    if wrote:
        log.info("harness home restored: %s", harness.key)
    return wrote


def has_stored(settings: Any, harness: Harness) -> bool:
    """Whether anything venona itself holds would authenticate this harness.

    Read by the metered-billing guard to decide whether an environment key is
    the operator's intent or a leftover: a key in the environment with nothing
    stored beside it is what a deploy looks like, and refusing that is the
    whole of ADR 0001.
    """
    if _SECRETS.get(harness.key):
        return True
    home = home_dir(settings, harness)
    return home.exists() and any(home.iterdir())


def credential_for(
    settings: Any, harness: Harness, billing_mode: str = "subscription"
) -> Credential:
    """What the child needs set, unset, and pointed at.

    THE VARIABLE COMES FROM THE SERVICE, not from the shape of its name. Which
    one carries a credential depends on how the instance pays, and the two are
    opposites: putting a subscription token into the metered variable either
    fails every call while the sign-in probe stays green, or bills per token on
    an instance whose settings say subscription.

    `suppress` is populated ONLY when something stored supersedes the
    environment. Otherwise a deployment that authenticates by environment
    variable -- which is every instance that exists today -- would have its
    credential removed by the mechanism meant to prefer a newer one.

    THE HOME IS POINTED AT ONLY WHEN VENONA PUT SOMETHING THERE. Pointing at it
    whenever it exists cannot work: the directory starts empty, so it reads as
    absent, so the child is never pointed at it, so the CLI writes its rotating
    token to the shared `$HOME` instead and the directory stays empty forever.
    Pointing at it unconditionally is worse -- it would take a workstation that
    is signed in through the service's own configuration and hand it an empty
    one.
    """
    stored = _SECRETS.get(harness.key)
    home = home_dir(settings, harness)
    populated = home.exists() and any(home.iterdir())

    env: dict[str, str] = {}
    if stored:
        name = harness.env_for(billing_mode)
        if name is not None:
            env = {name: stored}

    # Everything this vendor reads on its own, cleared whenever Venona has an
    # answer of its own -- whether that answer is a variable or a directory.
    suppress = harness.secret_env if (env or populated) else frozenset()
    return Credential(env=env, suppress=suppress, home=home if populated else None)


def source(settings: Any, harness: Harness, *, logged_in: bool = False) -> str:
    """Which credential is actually in force, for the admin page to state.

    Worth reporting because these disagree silently. An operator connects an
    account, this says `stored`; if it said `environment` instead they would
    know the deployment's key is still the one being used, and the sign-in
    probe cannot tell them because it reports a live session either way.

    `logged_in` is what separates "nothing is connected" from "connected by
    something Venona did not put there". The service's own configuration
    directory authenticates it perfectly well, and reporting that as `none`
    would put a "no service connected" banner on an instance whose calls are
    landing -- which is worse than saying nothing at all.
    """
    if _SECRETS.get(harness.key):
        return "stored"
    home = home_dir(settings, harness)
    if home.exists() and any(home.iterdir()):
        return "connected"
    if any(os.environ.get(name) for name in harness.secret_env):
        return "environment"
    return "inherited" if logged_in else "none"


def hint(secret: str) -> str:
    """A credential's last four characters, and nothing else.

    Derived here rather than stored, so it cannot drift from the value it
    describes -- the same reason `notify/policy.py` derives its own.
    """
    tail = secret.strip()[-4:]
    return f"ends {tail}" if tail else ""


# ── the row ──────────────────────────────────────────────────────────


def load(conn: db.Conn) -> None:
    """Populate the in-memory secrets from the database.

    Called at startup and after every write. A miss is not an error: an
    instance with no API key configured is the ordinary case.
    """
    global _LOADED
    from venona.db.repositories import harness as repo

    _SECRETS.clear()
    for row in repo.all_credentials(conn):
        if row["secret"]:
            _SECRETS[row["harness"]] = row["secret"]
    _LOADED = True


def invalidate() -> None:
    """Drop the cache so the next call re-reads. Paired with `load`."""
    global _LOADED
    _SECRETS.clear()
    _LOADED = False


#: The smallest thing a working credential can be asked to do. Strict-clean --
#: every property named in `required` -- because a service that validates
#: schemas strictly rejects anything else, and this must not fail for a reason
#: that has nothing to do with the credential.
_ALIVE_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


async def verify(
    settings: Any, harness: Harness, *, secret: str, billing_mode: str = "subscription"
) -> Any:
    """Ask the service to DO something with this credential, not whether it has one.

    A sign-in probe cannot answer this. `claude auth status` reports a live
    session for any token value including a typo, which `executor.py` records
    as a measured fact -- so a check built on it accepts whatever is pasted,
    stores it, reports success, and the next run 401s. Pointing the probe at a
    scratch home fixed which store it read and not that it cannot fail.

    One real call, at the cheapest tier, with no tools and a two-token answer.
    It costs a fraction of a cent and it is the only thing that distinguishes a
    credential that works from a string that is present.
    """
    import tempfile

    from venona.llm.executor import CliExecutor
    from venona.llm.harness import model_for

    name = harness.env_for(billing_mode)
    with tempfile.TemporaryDirectory(prefix="venona-verify-") as scratch:
        candidate = Credential(
            env={name: secret} if name else {},
            suppress=harness.secret_env,
            home=Path(scratch),
        )
        probe = CliExecutor(settings, harness=harness, credential=candidate)
        health = await probe.health()
        if not health.ok:
            return health

        try:
            result = await probe.call(
                "verify",
                user_content="Reply with ok true.",
                schema=_ALIVE_SCHEMA,
                model=model_for(harness, "fast"),
                effort="low",
                tools=(),
                timeout=90,
            )
        except Exception as exc:  # noqa: BLE001 - any failure is a failed credential
            return replace(health, ok=False, error=str(exc)[:200])
        if not result.ok:
            return replace(health, ok=False, error=result.error or "the service refused the call")
        return health


def store(
    conn: db.Conn,
    settings: Any,
    harness: Harness,
    *,
    credential_id: str,
    billing_mode: str,
    secret: str | None,
    account_label: str | None,
    verified_at: str,
    updated_by: str | None,
    now: str,
) -> None:
    """Record a credential, and drop the cache the executor reads.

    Paired with `load` for the same reason `config_service` pairs its write
    with `_invalidate`: a cache that survives its own write is a save that
    reports success and changes nothing until the next restart.
    """
    from venona.db.repositories import harness as repo

    ensure_dirs(settings, harness)
    repo.upsert(
        conn,
        credential_id=credential_id,
        harness=harness.key,
        billing_mode=billing_mode,
        secret=secret,
        account_label=account_label,
        verified_at=verified_at,
        updated_by=updated_by,
        now=now,
    )
    load(conn)


def forget(conn: db.Conn, harness: Harness, *, billing_mode: str) -> bool:
    from venona.db.repositories import harness as repo

    removed = repo.delete(conn, harness=harness.key, billing_mode=billing_mode) > 0
    load(conn)
    return removed


def stored_row(conn: db.Conn, harness: Harness, billing_mode: str) -> dict | None:
    """The connection record, without its secret. Never raises on a missing row."""
    from venona.db.repositories import harness as repo

    row = repo.get(conn, harness=harness.key, billing_mode=billing_mode)
    return dict(row) if row is not None else None


def stored_hint(conn: db.Conn, harness: Harness, billing_mode: str) -> str | None:
    """What a stored credential looks like, without being it.

    Derived on read rather than kept in a column: a hint stored beside the
    credential can outlive the credential it names.
    """
    secret = _SECRETS.get(harness.key)
    if not secret:
        return None
    return hint(secret)


def env_present(harness: Harness) -> bool:
    """Whether the environment alone would authenticate this service.

    Read before refusing a disconnect: a deployment that authenticates by
    environment variable still works after a stored credential is removed, and
    refusing that would be protecting nothing.
    """
    return any(os.environ.get(name) for name in harness.secret_env)


def has_home(settings: Any, harness: Harness) -> bool:
    home = home_dir(settings, harness)
    return home.exists() and any(home.iterdir())
