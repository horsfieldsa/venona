"""Server-side drafts: generated config documents awaiting review.

The domain-setup run proposes whole documents -- a relevance profile, an
ontology, an anchors trunk -- and none of them may land unreviewed
(docs/adr/0140). A draft is the staging shape: a file beside the documents it
proposes to replace, carried until an admin applies it through the editor or
discards it.

Files, not rows, and in ONE place: `<profile_dir>/.drafts/<key>` plus
`<key>.meta.json`, whatever directory the target document lives in. This is
the `.history/` pattern run forward -- already sandboxed in tests via
`profile_dir` redirection, diffable with ordinary tools, and it survives a
database reset. The staging location need not mirror the destination because
**apply routes through `config_service.write_doc`**, which resolves the real
path, validates through the real loader, snapshots history and invalidates
caches. Nothing here writes a live document.

Callers supply `generated_at` (from `ctx.clock`); this module never asks the
wall clock for anything.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import yaml

from venona.ontology import parse_ontology
from venona.services import config_service
from venona.services.config_service import ConfigError
from venona.settings import Settings

DRAFTS_DIRNAME = ".drafts"


@dataclass(frozen=True)
class DraftMeta:
    key: str
    run_id: str
    task: str
    generated_at: str
    valid: bool
    error: str | None = None


def drafts_dir(settings: Settings) -> Path:
    return settings.profile_dir / DRAFTS_DIRNAME


def _paths(settings: Settings, key: str) -> tuple[Path, Path]:
    """The draft body and meta paths for one key, allowlist-checked.

    The same two refusals as `config_service._path_for`: only declared
    documents, and nothing that could traverse. A draft for a key the editor
    cannot open would be unreviewable, which defeats the entire mechanism.
    """
    if not config_service.SAFE_KEY.fullmatch(key) or key not in config_service.EDITABLE:
        raise ConfigError(f"unknown document {key!r}")
    base = drafts_dir(settings)
    return base / key, base / f"{key}.meta.json"


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding=config_service.ENCODING) as handle:
            handle.write(content)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def store_draft(
    settings: Settings,
    key: str,
    content: str,
    *,
    run_id: str,
    task: str,
    generated_at: str,
) -> DraftMeta:
    """Stage a generated document. Validity is judged here, once, through the
    real validator -- an invalid draft is stored anyway, flagged, because a
    flawed proposal a human can fix beats a silently dropped one."""
    body_path, meta_path = _paths(settings, key)
    valid, error = _draft_verdict(settings, key, content)

    meta = DraftMeta(
        key=key, run_id=run_id, task=task, generated_at=generated_at, valid=valid, error=error
    )
    _write_atomic(body_path, content)
    _write_atomic(meta_path, json.dumps(asdict(meta), indent=2) + "\n")
    return meta


def _draft_verdict(settings: Settings, key: str, content: str) -> tuple[bool, str | None]:
    """Validate a draft the way the wizard says it will be APPLIED.

    Anchors are the one cross-document case: their ids and types are judged
    against an ontology, and the wizard's instruction is "apply the ontology
    draft first" -- so while an ontology draft is staged, the anchors draft is
    judged against THAT, the ontology it will actually land on. Judged against
    the live one instead, a pre-setup instance reports an environmental
    mismatch ('model:gpt-5-6' violates C4) that inverts the moment the
    ontology applies -- and masks the draft's real defects behind it.
    Apply-time validation (`write_doc`) always uses the live ontology, which
    is what will actually execute.
    """
    if key == "anchors":
        try:
            ontology_draft, _ = read_draft(settings, "ontology", revalidate=False)
            staged = parse_ontology(yaml.safe_load(ontology_draft))
        except Exception:  # noqa: BLE001 -- no usable staged ontology; judge live
            staged = None
        if staged is not None:
            from venona.services.seed import validate_anchors

            try:
                validate_anchors(yaml.safe_load(content), origin="anchors.yaml", ontology=staged)
                return True, None
            except Exception as exc:  # noqa: BLE001 -- the verdict IS the catch
                return False, str(exc)
    try:
        config_service.validate(settings, key, content)
        return True, None
    except ConfigError as exc:
        return False, str(exc)


def read_draft(settings: Settings, key: str, *, revalidate: bool = True) -> tuple[str, DraftMeta]:
    body_path, meta_path = _paths(settings, key)
    if not body_path.exists():
        raise ConfigError(f"no draft for {key!r}")
    content = body_path.read_text(encoding=config_service.ENCODING)
    if meta_path.exists():
        meta = DraftMeta(**json.loads(meta_path.read_text(encoding=config_service.ENCODING)))
    else:
        # A body without its sidecar (a crash between the two writes) is still
        # reviewable; the provenance is what is lost.
        meta = DraftMeta(key=key, run_id="", task="", generated_at="", valid=True)
    if revalidate:
        # The verdict is refreshed on every read rather than trusted from
        # store time, because validity is relative to the documents AROUND the
        # draft and those move: applying the ontology draft changes what the
        # anchors draft is judged against. A frozen verdict kept showing a
        # dead C4 error after the ontology landed -- while masking the
        # draft's 49 real domain/range defects behind it.
        valid, error = _draft_verdict(settings, key, content)
        if (valid, error) != (meta.valid, meta.error):
            meta = replace(meta, valid=valid, error=error)
            _write_atomic(meta_path, json.dumps(asdict(meta), indent=2) + "\n")
    return content, meta


def has_draft(settings: Settings, key: str) -> bool:
    try:
        body_path, _ = _paths(settings, key)
    except ConfigError:
        return False
    return body_path.exists()


def list_drafts(settings: Settings) -> list[dict[str, object]]:
    """Every staged draft, in the EDITABLE table's order (the rail's order)."""
    rows: list[dict[str, object]] = []
    for key, spec in config_service.EDITABLE.items():
        if not has_draft(settings, key):
            continue
        content, meta = read_draft(settings, key)
        rows.append(
            {
                "key": key,
                "title": spec["title"],
                "run_id": meta.run_id,
                "task": meta.task,
                "generated_at": meta.generated_at,
                "bytes": len(content.encode(config_service.ENCODING)),
                "valid": meta.valid,
                "error": meta.error,
            }
        )
    return rows


def apply_draft(
    settings: Settings,
    key: str,
    *,
    base_version: str | None = None,
    audit: config_service.WriteAudit | None = None,
) -> config_service.SaveResult:
    """Write the draft through the one write path, then clear it.

    The draft is deleted only after `write_doc` succeeds -- a failed apply
    (validator refusal, version conflict) leaves it in place for another try.

    `audit` rides through unchanged: applying a draft is a config write like any
    other, and a model-generated one is the write whose author is MOST worth
    recording -- the person who clicked apply is the author, not the model that
    proposed it (ADR 0166).
    """
    content, _ = read_draft(settings, key)
    result = config_service.write_doc(
        settings, key, content, base_version=base_version, audit=audit
    )
    discard_draft(settings, key)
    return result


def discard_draft(settings: Settings, key: str) -> None:
    body_path, meta_path = _paths(settings, key)
    if not body_path.exists():
        raise ConfigError(f"no draft for {key!r}")
    body_path.unlink()
    meta_path.unlink(missing_ok=True)
