"""The decision table, loaded.

`fixtures/curation/decisions.yaml` says what the right answer is for a pair of
entities. This turns each row into `pytest.param`s and plants it in a database
the way the pipeline would have.

WHY THE PLANTING MATTERS AS MUCH AS THE TABLE. Three hand-rolled conflict
builders already existed -- `_conflict` in `test_curate.py` and again with a
DIFFERENT signature in `test_curation_api.py`, and `_dup` a third time -- and
each invents a conflict id like `"c1"` and then sets `kind` by hand. `_classify`
branches on the id PREFIX and `_classify_open` is what stamps `kind` in the real
stage, so those tests assert the merger against a kind a human typed rather than
one the classifier produced. The two halves can disagree with the suite green.
`plant()` writes the id formats the pipeline actually writes and lets
`_classify_open` do the stamping.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from helpers import NOW, entity
from venona.db import core as db
from venona.db.models import normalize
from venona.ontology import get_ontology

ROOT = Path(__file__).resolve().parents[1]
TABLE = ROOT / "fixtures/curation/decisions.yaml"

VERDICTS = frozenset({"merge", "keep_apart", "relate", "escalate"})


@dataclass(frozen=True)
class Side:
    id: str
    name: str
    type: str


@dataclass(frozen=True)
class Case:
    key: str
    surface: str
    a: Side
    b: Side
    verdict: str
    reason: str
    classify: str | None = None
    survivor: str | None = None
    why: str | None = None
    predicate: str | None = None
    child: str | None = None
    declared_alias: str | None = None
    containment_veto: bool | None = None
    filed_by: str = "detector"

    def side(self, which: str) -> Side:
        return self.a if which == "a" else self.b

    @property
    def survivor_id(self) -> str:
        return self.side(self.survivor).id

    @property
    def loser_id(self) -> str:
        return self.side("b" if self.survivor == "a" else "a").id


def _side(raw: dict[str, Any]) -> Side:
    entity_id = raw["id"]
    prefix = entity_id.split(":", 1)[0]
    return Side(
        id=entity_id,
        name=raw["name"],
        # Derived from the prefix for the reason `helpers.entity` derives it:
        # ids are type-prefixed by contract and the ontology owns the mapping,
        # so inferring it is the only way the table and the database cannot
        # disagree about what type a row is.
        type=raw.get("type") or get_ontology().prefix_type[prefix],
    )


def _case(raw: dict[str, Any]) -> Case:
    key = raw.get("key", "?")
    for required in ("key", "surface", "a", "b", "verdict", "reason"):
        if not raw.get(required):
            raise ValueError(f"decision row {key}: no `{required}`")
    if raw["verdict"] not in VERDICTS:
        raise ValueError(f"{key}: unknown verdict {raw['verdict']!r}")
    if raw["verdict"] == "merge" and not (raw.get("survivor") and raw.get("why")):
        raise ValueError(f"{key}: a merge row must name `survivor` and `why`")
    if raw["verdict"] == "relate" and not (raw.get("predicate") and raw.get("child")):
        raise ValueError(f"{key}: a relate row must name `predicate` and `child`")
    if raw.get("filed_by") not in (None, "alias_collision", "containment", "detector"):
        raise ValueError(f"{key}: unknown filed_by {raw['filed_by']!r}")
    return Case(
        key=key,
        surface=raw["surface"],
        a=_side(raw["a"]),
        b=_side(raw["b"]),
        verdict=raw["verdict"],
        reason=raw["reason"],
        classify=raw.get("classify"),
        survivor=raw.get("survivor"),
        why=raw.get("why"),
        predicate=raw.get("predicate"),
        child=raw.get("child"),
        declared_alias=raw.get("declared_alias"),
        containment_veto=raw.get("containment_veto"),
        filed_by=raw.get("filed_by") or "detector",
    )


def load() -> list[Case]:
    doc = yaml.safe_load(TABLE.read_text()) or {}
    cases = [_case(raw) for raw in (doc.get("cases") or [])]
    keys = [c.key for c in cases]
    duplicates = sorted({k for k in keys if keys.count(k) > 1})
    if duplicates:
        raise ValueError(f"duplicate decision keys: {duplicates}")
    return cases


CASES: list[Case] = load()


def params(*, verdict: set[str] | None = None, needs: str | None = None) -> list[Any]:
    """`pytest.param` per matching case, ided by key so a failure names the row."""
    out = []
    for case in CASES:
        if verdict is not None and case.verdict not in verdict:
            continue
        if needs is not None and getattr(case, needs) is None:
            continue
        out.append(pytest.param(case, id=case.key))
    return out


def plant(conn: db.Conn, case: Case) -> None:
    """Both entities and their aliases, as the pipeline would have written them.

    A conflict row too, in the format `filed_by` names -- but never with `kind`
    set. Which filer writes the row is part of the judgement the table records:
    the resolver files an alias collision or a containment DURING analyze, while
    the detector reads a pair back out of the corpus afterwards, and only the
    detector can see a pair whose conflict was never filed at write time. A row
    planted by the wrong filer would test the classifier against a queue no run
    could produce. `kind` is always left for `_classify_open`, so the two halves
    of the stage have to agree on their own.

    Aliases are not decoration. The resolver's fuzzy candidate set is built by
    joining `aliases`, and `_file_entity_duplicates`'s strongest rule reads
    declared ones, so an entity without one is invisible to both.
    """
    for side in (case.a, case.b):
        entity(conn, side.id, side.name, entity_type=side.type)
        _alias(conn, side.id, side.name, added_by="extraction")
    if case.declared_alias:
        # A person stating that this surface names this entity -- the evidence
        # the General Atomics row turns on, and the only kind
        # `select_declared_alias_owners` counts.
        owner = case.side(case.declared_alias)
        other = case.side("b" if case.declared_alias == "a" else "a")
        _alias(conn, owner.id, other.name, added_by="seed")

    if case.filed_by == "alias_collision":
        # Both sides own the contested surface, and the row carries the id
        # `Resolver._add_alias` writes.
        for side in (case.a, case.b):
            _alias(conn, side.id, case.surface, added_by="extraction")
        _conflict(
            conn,
            f"{normalize(case.surface)}|{case.a.type}|{case.b.id}",
            case,
            existing=case.a,
            proposed=case.b,
        )
    elif case.filed_by == "containment":
        # `_contained` mints the new entity and files against the candidate it
        # declined to match, so the id names the minted side first.
        longer, shorter = sorted((case.a, case.b), key=lambda s: -len(s.name))
        _conflict(
            conn,
            f"contain|{shorter.id}|{longer.id}",
            case,
            existing=longer,
            proposed=shorter,
        )
    conn.commit()


def _conflict(
    conn: db.Conn, conflict_id: str, case: Case, *, existing: Side, proposed: Side
) -> None:
    db.upsert(
        conn,
        "alias_conflicts",
        {
            "id": conflict_id,
            "surface": case.surface,
            "normalized": normalize(case.surface),
            "existing_entity_id": existing.id,
            "proposed_entity_id": proposed.id,
            "entity_type": existing.type,
            "created_at": NOW,
        },
        key=["id"],
    )


def _alias(conn: db.Conn, entity_id: str, surface: str, *, added_by: str) -> None:
    # Reuse the row's existing id rather than minting a second one. `id` is the
    # PRIMARY KEY and the table also carries UNIQUE(entity_id, surface), so a
    # fresh id for a surface the seeded corpus already holds violates the second
    # constraint while the ON CONFLICT clause is watching the first. This is the
    # same trap `services/seed.py` documents at its own alias write.
    prior = conn.execute(
        "SELECT id FROM aliases WHERE entity_id = ? AND surface = ?", (entity_id, surface)
    ).fetchone()
    db.upsert(
        conn,
        "aliases",
        {
            "id": prior["id"] if prior else f"{entity_id}|{surface}",
            "entity_id": entity_id,
            "surface": surface,
            "normalized": normalize(surface),
            "strictness": 2,
            "is_acronym": int(surface.isupper()),
            "added_by": added_by,
            "created_at": NOW,
        },
        key=["id"],
    )


class _RepoProfile:
    """`profile/` as it is committed, which is the document this table is about.

    NOT `get_settings()`, which several modules point at a sandbox and do not
    put back -- `test_instance_skeleton` and `test_new_instance` both leave it
    on a tree seeded from `fixtures/exemplars/lexicon.yaml`. Reading through it
    made these assertions depend on collection order, and silently: the
    exemplar is a mature instance for another domain and happens to share the
    first ten tails, so every row passed on the wrong list until one was added
    that the exemplar does not carry.
    """

    profile_dir = ROOT / "profile"


def lexicons() -> dict[str, set[str]]:
    """The merge lexicons as the stage loads them, from `profile/`.

    Read from the real document rather than hardcoded, so a test fails when the
    operator edits the list out from under a decision this table records.
    """
    from venona.pipeline.acronyms import load_merge_tokens, load_person_tokens

    settings = _RepoProfile()
    corporate_form, generic_tails = load_merge_tokens(settings)
    person_titles, person_suffixes = load_person_tokens(settings)
    return {
        "corporate_form": corporate_form,
        "generic_tails": generic_tails,
        "person_titles": person_titles,
        "person_suffixes": person_suffixes,
    }
