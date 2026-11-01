"""`settings.yaml` is migrated forward, the way the schema is.

An instance's configuration file is written once, when the instance is
scaffolded, and edited by its operator from then on. A release that adds a knob
therefore has to reach a file it must not overwrite -- and the only two inputs a
plain merge has are the shipped file and the instance's, which between them can
express "add what is missing" and nothing else. It cannot tell a value the
operator chose from a default that was current when the instance was scaffolded,
so a changed default drifts silently forever; and it has no way to say "this key
is gone now" or "this key is called something else now", both of which leave a
key behind that `extra="forbid"` refuses to boot on. One release, every instance
down.

So: numbered migrations, each one an explicit decision an author made and a
reviewer read, applied in order, with the applied version recorded. Exactly the
argument `db/migrations/` already makes for the schema.

**The version lives in the document**, as `settings_version`. It cannot live in
Postgres: the DSN comes from `Settings`, which comes from the file being
migrated, so this runs before `db.migrate` and has no database to ask. Three
more reasons it belongs here anyway -- a restored or reset database would replay
the series against a file already at head; applying and stamping become one
`os.replace` rather than two commits in two systems; and a file copied to
another instance or restored from `.history/` carries its own version with it.
`config/ontology.yaml` already versions itself the same way.

**Every operation is individually idempotent** -- add if missing, set if forced.
The version is an optimisation, not the correctness argument, so a marker lost
to a hand edit costs a redundant pass rather than a broken file.
"""

from __future__ import annotations

import copy
import importlib
import logging
import pkgutil
from dataclasses import dataclass
from typing import Any, Protocol

import yaml

from venona.services import config_text as ct

log = logging.getLogger(__name__)


class Op(Protocol):
    """One change, applied twice: to the document's text, and to a parsed
    shadow of it. The runner refuses to write unless the two agree, which is
    what turns every possible splice bug into a caught error rather than a
    quietly mangled file."""

    def apply_text(self, text: str) -> str: ...

    def apply_dict(self, parsed: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class AddKey:
    """Add a key, with the comment that explains it, if the file lacks it."""

    group: str
    key: str
    block: tuple[str, ...]
    value: Any

    def apply_text(self, text: str) -> str:
        if ct.has_key(text, self.group, self.key):
            return text
        return ct.insert_key(text, self.group, list(self.block))

    def apply_dict(self, parsed: dict[str, Any]) -> dict[str, Any]:
        parsed.setdefault(self.group, {}).setdefault(self.key, self.value)
        return parsed


@dataclass(frozen=True)
class AddGroup:
    """Add a whole group the file has never had."""

    name: str
    block: tuple[str, ...]
    values: tuple[tuple[str, Any], ...]

    def apply_text(self, text: str) -> str:
        if ct.has_group(text, self.name):
            return text
        return ct.add_group(text, list(self.block))

    def apply_dict(self, parsed: dict[str, Any]) -> dict[str, Any]:
        parsed.setdefault(self.name, dict(self.values))
        return parsed


@dataclass(frozen=True)
class SetValue:
    """Overwrite a value the operator has no say over.

    `force` is not a convenience, it is the whole declaration: these are the
    fields `config_service.FROZEN_FIELDS` already refuses to let anyone change
    over HTTP, so there is no operator intent here to respect. A migration that
    did not say which case it was would not be reviewable, which is why the
    flag has no default.
    """

    group: str
    key: str
    to: Any
    force: bool

    def __post_init__(self) -> None:
        if not self.force:
            raise ValueError(
                f"{self.group}.{self.key}: a SetValue must declare force=True. "
                f"Overwriting a value the operator may have chosen needs a "
                f"condition, and conditions are not built yet."
            )

    def apply_text(self, text: str) -> str:
        return ct.set_scalar(text, self.group, self.key, _render(self.to))

    def apply_dict(self, parsed: dict[str, Any]) -> dict[str, Any]:
        parsed.setdefault(self.group, {})[self.key] = self.to
        return parsed


def _render(value: Any) -> str:
    """A value as this document would write it.

    Strings are quoted rather than left bare, and not for tidiness: an unquoted
    `2.2` is a float, so a version number one release later would stop being a
    version number. The shipped file quotes them for the same reason.
    """
    if isinstance(value, str):
        return yaml.safe_dump(value, default_style='"').strip().removesuffix("...").strip()
    return yaml.safe_dump(value).strip().removesuffix("...").strip()


@dataclass(frozen=True)
class RewriteComment:
    """Replace one exact comment line, wherever it stands.

    Comments are not decoration in this document, they are what tells the
    operator what a knob costs -- so a line that has stopped being true has to
    be correctable in an instance's own copy, not only in the shipped one.
    Matched and replaced whole, so it does nothing on a file that never had the
    line or has already been corrected.
    """

    old: str
    new: str

    def apply_text(self, text: str) -> str:
        lines = text.splitlines()
        return "\n".join(self.new if line.rstrip() == self.old else line for line in lines)

    def apply_dict(self, parsed: dict[str, Any]) -> dict[str, Any]:
        return parsed


@dataclass(frozen=True)
class Baseline:
    """Bring a document up to a known shipped file, adding only what it lacks.

    The merge, used ONCE, at a numbered point, against text pinned inside the
    migration rather than a file that keeps moving. `db/migrations/0001_baseline.sql`
    is the same shape: the starting state stated in full rather than derived.
    A test forbids this op above migration 0001.
    """

    text: str

    def _ops(self, target: str) -> list[Op]:
        source = yaml.safe_load(self.text) or {}
        ops: list[Op] = []
        for group, body in source.items():
            if not isinstance(body, dict):
                continue
            if not ct.has_group(target, group):
                ops.append(
                    AddGroup(
                        name=group,
                        block=tuple(ct.extract_group(self.text, group)),
                        values=tuple(body.items()),
                    )
                )
                continue
            for key, value in body.items():
                if not ct.has_key(target, group, key):
                    ops.append(
                        AddKey(
                            group=group,
                            key=key,
                            block=tuple(ct.extract_block(self.text, group, key)),
                            value=value,
                        )
                    )
        return ops

    def apply_text(self, text: str) -> str:
        for op in self._ops(text):
            text = op.apply_text(text)
        return text

    def apply_dict(self, parsed: dict[str, Any]) -> dict[str, Any]:
        source = yaml.safe_load(self.text) or {}
        for group, body in source.items():
            if not isinstance(body, dict):
                continue
            target = parsed.setdefault(group, {})
            for key, value in body.items():
                target.setdefault(key, value)
        return parsed


@dataclass(frozen=True)
class Migration:
    version: int
    description: str
    ops: tuple[Op, ...]


def _load() -> list[Migration]:
    found = []
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        if not info.name[0].isdigit():
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        found.append(module.MIGRATION)
    return sorted(found, key=lambda m: m.version)


MIGRATIONS: list[Migration] = _load()
HEAD: int = max((m.version for m in MIGRATIONS), default=0)


def pending(text: str) -> list[Migration]:
    at = ct.read_version(text)
    return [m for m in MIGRATIONS if m.version > at]


def apply_ops(text: str, ops: list[Op]) -> tuple[str, dict[str, Any]]:
    """The text and the shadow, advanced together."""
    shadow = copy.deepcopy(yaml.safe_load(text) or {})
    for op in ops:
        text = op.apply_text(text)
        shadow = op.apply_dict(shadow)
    return text, shadow


class MigrationError(RuntimeError):
    pass


def migrate(paths: Any) -> list[int]:
    """Bring this instance's `settings.yaml` to head. Returns what was applied.

    One write for the whole pending series rather than one per migration:
    `.history/` keeps twenty snapshots and a ten-migration catch-up would spend
    half the ring in a single boot, and a failure part-way through then leaves
    the file exactly as it was.
    """
    path = paths.config_dir / "settings.yaml"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    due = pending(text)
    if not due:
        return []

    ct.check(text)
    ops = [op for migration in due for op in migration.ops]
    updated, shadow = apply_ops(text, ops)
    updated = ct.stamp_version(updated, HEAD)
    shadow["settings_version"] = HEAD

    # THE GUARD THAT MAKES LINE SURGERY SAFE TO RUN UNATTENDED. Every way the
    # splice can be wrong -- a swallowed neighbour, a comment block cut in half,
    # the wrong indent -- lands here as a value that does not match what the
    # operations said they were doing, before the file is touched.
    written = yaml.safe_load(updated) or {}
    if written != shadow:
        raise MigrationError(
            f"{path}: the edited text does not match what the migrations said "
            f"they would do, so nothing was written. Applying "
            f"{[m.version for m in due]}."
        )

    from venona.services import config_service

    result = config_service.apply_settings_migration(paths, updated)
    applied = [m.version for m in due]
    log.info(
        "config migrate: applied %s, now at %s, path=%s, previous kept as %s",
        applied,
        HEAD,
        path,
        result.snapshot or "nothing (the file was new)",
    )
    return applied
