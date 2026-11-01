"""Load and validate `config/sources.yaml`, and track per-source health.

A silently empty feed is indistinguishable from a quiet news day, which is why
health tracking exists at all: publishers move feed paths without notice, and
without a strike counter the pipeline would keep "succeeding" while returning
nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from venona.db import core as db
from venona.db.repositories import registry as repo
from venona.sources.base import SourceSpec

# Three consecutive failures disables a source and raises a banner. Chosen so a
# single transient blip does not disable anything, but a moved feed does.
STRIKE_LIMIT = 3


class SourceConfigError(ValueError):
    """Raised at startup. An invalid source config must never reach a run."""


@dataclass
class SourceHealth:
    source_id: str
    consecutive_failures: int = 0
    auto_disabled: bool = False
    etag: str | None = None
    last_modified: str | None = None
    last_success_at: str | None = None
    last_item_at: str | None = None
    last_error: str | None = None


def parse_sources(raw: dict[str, Any] | None, *, origin: str) -> list[SourceSpec]:
    """Validate a parsed sources document.

    Split out of `load_sources` so the save path in
    `services/config_service.py` rejects a document with the SAME errors startup
    would raise. `origin` is threaded through rather than dropped because all
    three messages below name the file, and an error that does not say which
    file is wrong is most of the way to being useless.
    """
    raw = raw or {}
    defaults: dict[str, Any] = raw.get("defaults") or {}

    specs: list[SourceSpec] = []
    seen: set[str] = set()

    for entry in raw.get("sources") or []:
        merged = {**defaults, **entry}
        try:
            spec = SourceSpec(**merged)
        except ValidationError as exc:
            raise SourceConfigError(
                f"invalid source {entry.get('id', '<no id>')!r} in {origin}:\n{exc}"
            ) from exc
        if spec.id in seen:
            raise SourceConfigError(f"duplicate source id {spec.id!r} in {origin}")
        seen.add(spec.id)
        specs.append(spec)

    if not specs:
        raise SourceConfigError(f"{origin} declares no sources")
    return specs


def load_sources(config_dir: Path) -> list[SourceSpec]:
    path = config_dir / "sources.yaml"
    return parse_sources(yaml.safe_load(path.read_text()), origin=str(path))


def sync_sources(conn: db.Conn, specs: list[SourceSpec], *, now: str) -> None:
    """Mirror the config into Postgres so documents can reference a source row."""
    for spec in specs:
        db.upsert(
            conn,
            "sources",
            {
                "id": spec.id,
                "name": spec.name,
                "kind": spec.kind,
                "url": spec.url,
                "publisher": spec.publisher,
                "enabled": int(spec.enabled),
                "tags": db.jdump(spec.tags),
                "created_at": now,
            },
            key=["id"],
        )
        repo.insert_source_health(conn, spec.id)


def get_health(conn: db.Conn, source_id: str) -> SourceHealth:
    row = repo.select_source_health_by_source_id(conn, source_id).fetchone()
    if row is None:
        return SourceHealth(source_id=source_id)
    return SourceHealth(
        source_id=source_id,
        consecutive_failures=row["consecutive_failures"] or 0,
        auto_disabled=bool(row["auto_disabled"]),
        etag=row["etag"],
        last_modified=row["last_modified"],
        last_success_at=row["last_success_at"],
        last_item_at=row["last_item_at"],
        last_error=row["last_error"],
    )


def record_success(
    conn: db.Conn,
    source_id: str,
    *,
    now: str,
    etag: str | None,
    last_modified: str | None,
    item_count: int,
) -> None:
    repo.update_source_health_by_last_success_at_last_attempt_at(
        conn, now, now, etag, last_modified, item_count, now, source_id
    )


def clear_strikes(conn: db.Conn, source_id: str) -> bool:
    """Put a source back in the rotation. Returns False if there is no such row.

    The one way out of `auto_disabled`, and it needs to be reachable from the
    app rather than only from a terminal. `active_specs` excludes a disabled
    source from every run, so the source is shut out of the very run that would
    clear its counter -- which makes this a one-way door until someone
    intervenes, and requiring them to know a CLI incantation printed in a UI
    banner is not reachable enough.

    Deliberately NOT a success: it clears the counter and the last error, but it
    does not stamp `last_success_at`. Nothing has succeeded yet -- the next run
    decides that, and pretending otherwise would hide a source that is still
    broken behind a fresh-looking timestamp.
    """
    cursor = repo.update_source_health_by_source_id(conn, source_id)
    conn.commit()
    return cursor.rowcount > 0


def record_failure(conn: db.Conn, source_id: str, *, now: str, error: str) -> int:
    """Returns the new strike count. At STRIKE_LIMIT the source auto-disables."""
    repo.update_source_health_by_last_attempt_at_last_error(
        conn, now, error[:500], STRIKE_LIMIT, source_id
    )
    row = repo.select_source_health_by_source_id_2(conn, source_id).fetchone()
    return row["consecutive_failures"] if row else 0


def active_specs(conn: db.Conn, specs: list[SourceSpec]) -> list[SourceSpec]:
    """Config-enabled AND not auto-disabled by the strike counter."""
    disabled = {row["source_id"] for row in repo.select_source_health(conn)}
    return [s for s in specs if s.enabled and s.id not in disabled]


def source_order(specs: list[SourceSpec]) -> dict[str, int]:
    """Declaration order, which breaks ties when choosing a cluster's canonical
    document. Deterministic by construction."""
    return {spec.id: index for index, spec in enumerate(specs)}
