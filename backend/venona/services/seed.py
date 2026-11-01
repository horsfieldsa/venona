"""Load declarative fixtures into Postgres, then project to Neo4j.

`make seed` makes the entire UI developable and demoable without a single model
call or network request, in under ten seconds (measured 2026-08-06).

Everything written here goes in as `assertions` with `method='seed'`, exactly
like pipeline output, so the seed path and the real path exercise the same
projector. Nothing asserts the two produce the same entity id set, so the
declarative seed can drift from what a run produces; the docstring of
`tests/test_orchestrator_smoke.py` carries why that gate is unaffordable.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import psycopg
import yaml

from venona.db import core as db
from venona.db.models import (
    acronym_variants,
    glossary_id,
    is_valid_id,
    is_writable_acronym,
    normalize,
    slugify,
)
from venona.db.repositories import seed as repo
from venona.ontology import Ontology, get_ontology
from venona.pipeline.types import FrozenClock
from venona.services.alias_index import default_strictness
from venona.services.annotate import (
    Block,
    annotate_blocks,
    markdown_to_blocks,
)

SEED_RUN_ID = "run:seed-0001"

# The anchor spine lives under its own run id so `venona anchors load` and
# `venona seed` are independently idempotent -- each deletes only its own
# assertions. `load_anchors` carries the rest of why they stay separate.
ANCHOR_RUN_ID = "run:anchors-0001"

#: The `article_id` on a declared edge that names no article. It resolves to no
#: `documents` row on purpose: a declaration has no publisher. What separates a
#: declaration from a story nobody corroborated is the ABSENCE of a source
#: domain, not this id -- `graph/projector.py` applies that test. ADR 0195.
DECLARED_ARTICLE_ID = "doc:declared"

LEXICON = {
    "next generation": ["next-gen", "nextgen", "ng"],
    "command and control": ["c2"],
}

# Relationship shorthands allowed directly on an entity spec. The value is
# (predicate, predicate_kind, reversed); `reversed` means the DECLARING entity
# is the OBJECT of the assertion, not the subject.
#
# A table rather than a branch at each call site, because DIRECTION is the thing
# most easily got backwards and `MANAGES`/`PRODUCES` inverted silently inverts
# the org chart. One place to read it, and `check_relation` re-checks the result.
#
# Easy to mistake for decoration: 20 entities in `fixtures/seed/entities.yaml`
# carry one of these keys, 11 of them `part_of:` (counted 2026-09-14), and a
# loader that ignores them draws a declared parent as an orphan. `edges.yaml`
# spells all 20 triples out by hand as well, which is the duplication reading
# them makes unnecessary.
INLINE_RELATIONS: dict[str, tuple[str, str | None, bool]] = {
    "part_of": ("PART_OF", "org_hierarchy", False),
    "based_in": ("BASED_IN", None, False),
    "managed_by": ("MANAGES", None, True),
    "produced_by": ("PRODUCES", None, True),
    "works_for": ("WORKS_FOR", None, False),
}


def _split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    _, _, rest = text.partition("---\n")
    front, _, body = rest.partition("\n---")
    return yaml.safe_load(front) or {}, body.lstrip("\n")


def load_seed(conn: db.Conn, fixtures_dir: Path, *, quiet: bool = False) -> dict[str, int]:
    clock = FrozenClock()
    now = clock.now()
    seed_dir = fixtures_dir / "seed"

    counts = {
        "entities": 0,
        "aliases": 0,
        "edges": 0,
        "glossary": 0,
        "documents": 0,
        "blocks": 0,
    }

    with db.transaction(conn):
        repo.delete_assertions_by_run_id(conn, SEED_RUN_ID)

        entities_doc = yaml.safe_load((seed_dir / "entities.yaml").read_text())
        result = _load_entities(conn, entities_doc, now=now, run_id=SEED_RUN_ID)
        inline = result.pop("relations")
        counts.update(result)

        # The explicit `edges.yaml` row FIRST, because `_load_edges` de-dupes
        # first-wins: a row carrying an evidence article id has to beat the bare
        # `part_of:` shorthand naming the same triple, or the article is lost and
        # the edge cites nothing.
        edges_doc = yaml.safe_load((seed_dir / "edges.yaml").read_text())
        counts["edges"] = _load_edges(
            conn,
            {"edges": (edges_doc.get("edges") or []) + inline},
            now=now,
            run_id=SEED_RUN_ID,
        )

        glossary_doc = yaml.safe_load((seed_dir / "glossary.yaml").read_text())
        counts["glossary"] = _load_glossary(conn, glossary_doc, now=now)

        brief_counts = _load_brief(conn, seed_dir / "brief.md", now=now)
        counts["documents"] = brief_counts["documents"]
        counts["blocks"] = brief_counts["blocks"]
        counts["items"] = brief_counts["items"]

    if not quiet:
        print(
            f"seeded {counts['entities']} entities, {counts['aliases']} aliases, "
            f"{counts['edges']} edges, {counts['glossary']} glossary entries, "
            f"{counts['documents']} documents, {counts['items']} brief items, "
            f"{counts['blocks']} brief blocks"
        )
    return counts


def validate_anchors(
    raw: dict[str, Any] | None, *, origin: str, ontology: Ontology | None = None
) -> dict[str, int]:
    """Check an anchor document without touching the database.

    `ontology` defaults to the live one. The wizard's DRAFT flow passes the
    staged ontology instead: an anchors draft is generated against it and will
    be applied onto it, so judging the draft against the live (pre-setup,
    skeleton) ontology reports an environmental mismatch that masks the
    draft's real defects. Apply-time validation always uses the live one.

    `load_anchors` validates as it inserts, inside a transaction that has
    already issued `DELETE FROM assertions WHERE run_id = ANCHOR_RUN_ID`. That
    is fine for a CLI where the rollback is the whole story, but the config
    editor has to answer "is this file good?" BEFORE it writes it to disk, with
    no database in hand and nothing destroyed if the answer is no.

    What cannot be checked here: an edge endpoint that is not declared in this
    document may still be a real entity the pipeline discovered. Those are left
    for `load_anchors` to type-check at apply time. Everything the document can
    be judged on by itself is judged here.
    """
    doc = raw or {}
    ontology = ontology or get_ontology()
    # The C4 pattern from THIS ontology, not `is_valid_id` -- that helper
    # compiles its alternation from the live one, which is the mismatch the
    # `ontology` parameter exists to avoid.
    from venona.db.models import _compiled_id_pattern

    valid_id = _compiled_id_pattern(tuple([*sorted(ontology.type_prefix.values()), "gloss"]))
    declared: dict[str, str] = {}

    for index, spec in enumerate(doc.get("entities") or []):
        if not isinstance(spec, dict):
            raise ValueError(f"{origin}: entity {index + 1} is not a mapping")
        for required in ("id", "type", "name"):
            if not spec.get(required):
                raise ValueError(f"{origin}: entity {index + 1} has no `{required}`")
        entity_id, entity_type = spec["id"], spec["type"]
        if not valid_id.match(entity_id):
            raise ValueError(f"{origin}: entity id {entity_id!r} violates contract C4")
        if entity_type not in ontology.type_names:
            raise ValueError(
                f"{origin}: entity {entity_id} has unknown type {entity_type!r}; "
                f"declared types are {list(ontology.type_names)}"
            )
        expected = ontology.type_prefix[entity_type]
        if entity_id.split(":", 1)[0] != expected:
            raise ValueError(
                f"{origin}: entity {entity_id} is typed {entity_type}, so its id "
                f"must start with {expected!r} (C4 -- the prefix IS the type)"
            )
        if entity_id in declared:
            raise ValueError(f"{origin}: entity {entity_id} is declared twice")
        declared[entity_id] = entity_type

    edges: list[dict[str, Any]] = []
    for spec in doc.get("entities") or []:
        for key, (predicate, _kind, reverse) in INLINE_RELATIONS.items():
            other = spec.get(key)
            if not other:
                continue
            src, dst = (other, spec["id"]) if reverse else (spec["id"], other)
            edges.append({"src": src, "dst": dst, "predicate": predicate})
    edges += list(doc.get("edges") or [])

    for index, spec in enumerate(edges):
        if not isinstance(spec, dict):
            raise ValueError(f"{origin}: edge {index + 1} is not a mapping")
        for required in ("src", "dst", "predicate"):
            if not spec.get(required):
                raise ValueError(f"{origin}: edge {index + 1} has no `{required}`")
        src, dst, predicate = spec["src"], spec["dst"], spec["predicate"]
        if predicate not in ontology.predicate_names:
            raise ValueError(
                f"{origin}: edge {src} -{predicate}-> {dst} uses a predicate that "
                f"is not in the ontology"
            )
        # Only when both endpoints are declared here can the types be known.
        if src in declared and dst in declared:
            problem = ontology.check_relation(predicate, declared[src], declared[dst])
            if problem:
                raise ValueError(f"{origin}: edge {src} -{predicate}-> {dst}: {problem}")

    merges = doc.get("merges") or []
    for index, spec in enumerate(merges):
        if not isinstance(spec, dict) or not spec.get("from") or not spec.get("into"):
            raise ValueError(f"{origin}: merge {index + 1} needs both `from` and `into`")

    ambiguous = doc.get("ambiguous") or []
    for index, spec in enumerate(ambiguous):
        if not isinstance(spec, dict) or not spec.get("surface"):
            raise ValueError(f"{origin}: ambiguous entry {index + 1} has no `surface`")
        readings = spec.get("readings") or []
        if not isinstance(readings, list):
            raise ValueError(f"{origin}: {spec['surface']}: `readings` must be a list of ids")
        # Format only. Whether the id names a live entity cannot be known here
        # -- this function takes no database, the same limit `edges` has -- but
        # a typo is a reading that can never be picked, which is silent, so the
        # shape is checked where the file is on screen.
        for reading in readings:
            if not is_valid_id(str(reading)):
                raise ValueError(
                    f"{origin}: {spec['surface']}: reading {reading!r} is not an entity id"
                )
        if len(set(map(str, readings))) != len(readings):
            raise ValueError(f"{origin}: {spec['surface']}: a reading is listed twice")
        if len(readings) == 1:
            raise ValueError(
                f"{origin}: {spec['surface']}: one reading is not an ambiguity. Declare "
                f"it as an alias of that entity instead, or list the others."
            )

    return {"entities": len(declared), "edges": len(edges), "merges": len(merges)}


def load_anchors(
    conn: db.Conn,
    profile_dir: Path,
    *,
    apply_merges: bool = False,
    dry_run: bool = False,
    quiet: bool = False,
) -> dict[str, int]:
    """Load `profile/anchors.yaml`: the top-level structure of the graph.

    Separate from `load_seed` for one blunt reason: `load_seed` also writes the
    fictional brief and `doc:seed-*` documents of `fixtures/seed/`, so running it
    against a live database injects invented evidence into the system of record.
    Anchors have to be loadable without that, and they carry their own run id so
    the two stay independently re-runnable.

    Everything here is `method='seed'`, which is exactly right for an anchor:
    `promote_provisionals` never demotes it (a country's existence is not a
    matter of corroboration) and `entity_evidence` excludes it, so a national
    anchor inflates no entity's mention count.

    `merges:` is applied only under `apply_merges`. Loading structure and
    collapsing duplicates are different decisions, and the second one should
    never be a side effect of the first.
    """
    path = profile_dir / "anchors.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no anchor file at {path}")

    doc = yaml.safe_load(path.read_text()) or {}
    clock = FrozenClock()
    now = clock.now()

    counts = {"entities": 0, "aliases": 0, "edges": 0, "merges": 0, "skipped": 0}

    if dry_run:
        for spec in doc.get("entities") or []:
            exists = repo.select_entities_by_id_2(conn, spec["id"]).fetchone()
            print(f"  entity {spec['id']:44} {'(exists)' if exists else '(new)'}")
        for spec in doc.get("edges") or []:
            print(f"  edge   {spec['src']:44} {spec['predicate']:14} {spec['dst']}")
        for spec in doc.get("merges") or []:
            print(f"  merge  {spec['from']:44} -> {spec['into']}")
        print("\ndry run: nothing written")
        return counts

    with db.transaction(conn):
        repo.delete_assertions_by_run_id_2(conn, ANCHOR_RUN_ID)

        result = _load_entities(conn, doc, now=now, run_id=ANCHOR_RUN_ID)
        inline = result.pop("relations")
        counts["entities"] = result["entities"]
        counts["aliases"] = result["aliases"]
        counts["withdrawn"] = result["withdrawn"]

        # Explicit rows first, for the reason `load_seed` states: `_load_edges`
        # de-dupes first-wins, so the richer row has to come first to win.
        counts["edges"] = _load_edges(
            conn,
            {"edges": (doc.get("edges") or []) + inline},
            now=now,
            run_id=ANCHOR_RUN_ID,
        )

    # Outside the transaction: `merge_entities` opens its own, and it re-runs the
    # promotion gate afterwards.
    if apply_merges:
        from venona.services.curate import MergeError, merge_entities

        for spec in doc.get("merges") or []:
            from_id, into = spec["from"], spec["into"]
            try:
                merge_entities(
                    conn,
                    from_id=from_id,
                    to_id=into,
                    # Deterministic, so re-running is a true no-op on the
                    # (from_id, merge_id) primary key rather than a second row.
                    merge_id=f"merge:anchor:{from_id}",
                    reason=spec.get("reason", "anchor"),
                    now=now,
                )
                counts["merges"] += 1
            except MergeError as exc:
                # Already merged, or now unmergeable. Not a failure of the load.
                counts["skipped"] += 1
                if not quiet:
                    print(f"  skipped {from_id} -> {into}: {exc}")

    if not quiet:
        print(
            f"anchors: {counts['entities']} entities, {counts['aliases']} aliases, "
            f"{counts['edges']} edges, {counts['merges']} merges"
            + (f", {counts['skipped']} skipped" if counts["skipped"] else "")
            # A withdrawal is the one thing here that REMOVES something a reader
            # could see, so it is never folded into the alias total.
            + (f", {counts['withdrawn']} alias(es) withdrawn" if counts.get("withdrawn") else "")
        )
    return counts


def _declared_status(spec: dict[str, Any], prior: db.Row | None) -> str:
    """The status a re-declared entity keeps.

    A declaration says what a thing IS. Whether it survived a merge is a
    different fact, held in `entity_mappings`, and this loader cannot see it --
    so writing `active` over a tombstone leaves the corpus with two live ids for
    one thing and an active mapping that still folds them together. The two then
    disagree in front of a reader: the projection draws one node, every count
    read off `entities` says two, and the hover reports the survivor's
    corroboration rather than the declared entity's. `venona entity unmerge` is
    the way back.

    Every other status yields to the document, which is the point of editing it.
    """
    from venona.services.curate import MERGED

    if prior is not None and prior["status"] == MERGED:
        return MERGED
    return spec.get("status", "active")


def _load_entities(
    conn: db.Conn,
    doc: dict[str, Any],
    *,
    now: str,
    run_id: str = SEED_RUN_ID,
) -> dict[str, Any]:
    """Upsert declared entities and their aliases.

    Returns the counts plus `relations`: edge specs harvested from the inline
    shorthands (`part_of:` and friends). They are returned rather than inserted
    so `_load_edges` stays the single insert and validation site.
    """
    entities = doc.get("entities") or []
    alias_count = 0
    withdrawn = 0
    inline: list[dict[str, Any]] = []

    for spec in entities:
        entity_id = spec["id"]
        if not is_valid_id(entity_id):
            raise ValueError(f"seed entity id {entity_id!r} violates contract C4")

        name = spec["name"]

        # Preserve provenance on re-declaration. `db.upsert` writes every
        # non-key column from `excluded`, so declaring an id that already exists
        # would otherwise reset `first_seen`/`created_at` to this loader's
        # FrozenClock epoch and throw away whatever the pipeline had learned.
        prior = repo.select_entities_by_id(conn, entity_id).fetchone()
        props = {
            **(db.jload(prior["props"], {}) if prior else {}),
            **(spec.get("props") or {}),
        }
        if spec.get("platform_type"):
            # Declared in the ontology, no column to hold it. Folded into props
            # rather than dropped on the floor.
            props["platform_type"] = spec["platform_type"]

        db.upsert(
            conn,
            "entities",
            {
                "id": entity_id,
                "type": spec["type"],
                "name": name,
                "canonical_key": normalize(name),
                "expansion": spec.get("expansion"),
                "summary": (spec.get("summary") or "").strip() or None,
                "org_type": spec.get("org_type"),
                "status": _declared_status(spec, prior),
                "relevance": float(spec.get("relevance", 0.0)),
                "mention_count": 0,
                "source_count": 0,
                "props": db.jdump(props),
                "first_seen": prior["first_seen"] if prior else now,
                "last_seen": now,
                "created_at": prior["created_at"] if prior else now,
            },
            key=["id"],
        )

        for key, (predicate, kind, reverse) in INLINE_RELATIONS.items():
            other = spec.get(key)
            if not other:
                continue
            src, dst = (other, entity_id) if reverse else (entity_id, other)
            inline.append(
                {
                    "src": src,
                    "dst": dst,
                    "predicate": predicate,
                    "kind": kind,
                    "confidence": 0.95,
                    **({"evidence": spec["evidence"]} if spec.get("evidence") else {}),
                }
            )

        # The canonical name is always an alias; without it the full form in
        # prose would not match.
        #
        # `name_strictness` exists for ANCHORS. The default of 3 for a name of
        # eight characters or more is global case-insensitive matching, right for
        # `Anduril Industries` and wrong for a country, which would then take the
        # highlight off `unit:u-s-army` in "the United States Army awarded…".
        # `profile/anchors.yaml` declares `name_strictness: 0` throughout and
        # says why. (An explicit `aliases:` entry for the same surface also wins
        # -- the upsert below is keyed on (entity_id, surface) and this is first.)
        alias_specs: list[dict[str, Any]] = [
            {
                "surface": name,
                "strictness": spec.get("name_strictness", 3 if len(name) >= 8 else 2),
            }
        ]
        alias_specs += list(spec.get("aliases") or [])

        # Auto-generate acronym forms. This is what makes "NGC2" resolve without
        # anyone hand-listing it.
        if spec.get("expansion"):
            alias_specs.append({"surface": spec["expansion"], "strictness": 2, "is_acronym": True})
        # A surface a person DECLARED -- the name, an `aliases:` entry, or the
        # `expansion` -- is never re-written as a generated one. The specs are
        # upserted in order and the generator runs last, so without this an
        # `expansion: NGC2` sitting in `entities.yaml` lands as
        # `added_by: acronym_gen`: a human's statement recorded as a machine's
        # guess. That column is the provenance the annotator reads to decide
        # whether a surface may match on its own.
        declared_surfaces = {
            (a["surface"] if isinstance(a, dict) else a).upper() for a in alias_specs
        }
        # The same set, un-folded: `aliases.surface` stores the literal text the
        # annotator matches, so a withdrawal has to compare literals too.
        declared_surfaces_exact = {
            (a["surface"] if isinstance(a, dict) else a) for a in alias_specs
        }

        # `get_ontology()` at the point of use, not a snapshot: the type set is
        # editable over HTTP and a UI-triggered run executes in the API process.
        if get_ontology().generates_acronyms(spec["type"]):
            for variant in acronym_variants(name, lexicon=LEXICON):
                if is_writable_acronym(variant) and variant.upper() not in declared_surfaces:
                    alias_specs.append(
                        {
                            "surface": variant,
                            "strictness": 2,
                            "is_acronym": True,
                            "added_by": "acronym_gen",
                        }
                    )

        # Unlike `Resolver._add_alias`, this writes a declared surface even when
        # an entity of the same type already holds it, and files no conflict
        # about the collision. That is deliberate on the write side -- a
        # declaration is a person's statement and outranks a mint, so it takes
        # the surface -- but it means the rival is left unrecorded, which is how
        # a declared entity and a discovered duplicate of it coexisted with no
        # row anywhere. `curate._file_entity_duplicates` reads the collision back
        # out of the data on the next run and files it there, so the pair is
        # asked about exactly once, by one writer, with the merge direction
        # decided in one place.
        for alias in alias_specs:
            surface = alias["surface"] if isinstance(alias, dict) else alias
            added_by = (alias.get("added_by") if isinstance(alias, dict) else None) or "seed"
            is_acronym = bool(alias.get("is_acronym")) if isinstance(alias, dict) else False
            strictness = (
                alias.get("strictness")
                if isinstance(alias, dict) and alias.get("strictness") is not None
                else default_strictness(surface=surface, added_by=added_by, is_acronym=is_acronym)
            )
            # Reuse the row's existing id rather than minting a second one.
            # `db.upsert` keys on (entity_id, surface), but `id` is the PRIMARY
            # KEY and is not covered by that ON CONFLICT clause -- so a fresh id
            # for a surface that already exists raises, and the `except` below
            # would swallow it and drop the alias silently.
            prior_alias = repo.select_aliases_by_entity_id_surface(
                conn, entity_id, surface
            ).fetchone()
            # And a new row's id is derived from the pair it belongs to, never
            # from a counter -- `_row_id` carries the incident that proves why.
            alias_id = prior_alias["id"] if prior_alias else _alias_id(entity_id, surface)
            # A savepoint, because this whole load is one transaction: on
            # Postgres a failed statement aborts it, so a bare `continue` here
            # would not skip one alias -- every statement after it, including
            # the entities and assertions still to load, would raise
            # `InFailedSqlTransaction`, and the seed would land half-written.
            try:
                with db.savepoint(conn, "alias"):
                    db.upsert(
                        conn,
                        "aliases",
                        {
                            "id": alias_id,
                            "entity_id": entity_id,
                            "surface": surface,
                            "normalized": normalize(surface),
                            "strictness": int(strictness),
                            "is_acronym": int(is_acronym),
                            "added_by": added_by,
                            "evidence_article_id": None,
                            "created_at": now,
                        },
                        key=["entity_id", "surface"],
                    )
                alias_count += 1
            except psycopg.IntegrityError:
                continue

        # A surface this entity holds as a DECLARED alias and the document no
        # longer lists is withdrawn. Loading is otherwise upsert-only, which
        # meant a withdrawal removed nothing: the line came out of the file, the
        # row stayed, and the entity went on being called something a person had
        # decided it is not. It took a migration to undo the first time, and
        # applying a stale copy of the document put it straight back.
        #
        # Scoped to `seed` on purpose. `extraction` and `acronym_lookup` rows
        # are the corpus's, `manual` rows are a curator's, and this file speaks
        # for none of them -- only for what it declared itself.
        for row in repo.select_aliases_by_entity_id(conn, entity_id).fetchall():
            if row["added_by"] == "seed" and row["surface"] not in declared_surfaces_exact:
                repo.delete_alias(conn, row["id"])
                withdrawn += 1

        # An entity assertion so the seed path produces the same shape the
        # pipeline does -- the projector reads assertions, not entities.
        db.insert(
            conn,
            "assertions",
            {
                # One entity assertion per declared entity per run.
                "id": _row_id("assert", run_id, "entity", entity_id),
                "run_id": run_id,
                # domain-lint: ok -- the dev seed corpus's article id (fixtures/seed/)
                "article_id": "doc:seed-ngc2-overview",
                "cluster_id": "cluster:seed",
                "kind": "entity",
                "surface": name,
                "entity_type": spec["type"],
                "entity_id_raw": entity_id,
                "evidence_quote": f"Seed declaration for {name}.",
                "salience": float(spec.get("relevance", 0.0)),
                "confidence": 1.0,
                "method": "seed",
                "created_at": now,
            },
        )

    for amb in doc.get("ambiguous") or []:
        # `readings` is what the surface COULD mean, and is optional: without it
        # the resolver declines exactly as it always has. With it, it can pick
        # the one the document names and still decline when the document names
        # none. Stored as JSON because it is written here, read whole, and never
        # queried across.
        readings = [str(r) for r in (amb.get("readings") or [])]
        db.upsert(
            conn,
            "ambiguous_surfaces",
            {
                "normalized": normalize(amb["surface"]),
                "reason": amb.get("reason"),
                "readings": db.jdump(readings) if readings else None,
                "created_at": now,
            },
            key=["normalized"],
        )

    return {
        "entities": len(entities),
        "aliases": alias_count,
        "withdrawn": withdrawn,
        "relations": inline,
    }


def _row_id(prefix: str, *parts: str) -> str:
    """A stable primary key for a row a loader declares.

    Derived from what the row IS, so a reload writes the same key and a
    reordered file changes nothing. These are opaque keys -- nothing reads one.

    Never a positional counter, which couples an id to how many rows happen to
    be written before it. Two incidents, both measured 2026-09-02: adding one
    alias to `profile/anchors.yaml` shifted every later key onto values an
    earlier load already held, so the insert collided and
    `except IntegrityError: continue` discarded a surface a person had declared;
    and a loader's stream shared its first value with any other `SeededIdGen(0)`,
    so its first assertion collided with somebody else's. A key over the row's
    own identity cannot shift and cannot meet another one.
    """
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return f"{prefix}:{digest[:32]}"


def _alias_id(entity_id: str, surface: str) -> str:
    return _row_id("alias", entity_id, surface)


def _kind_for(ontology, predicate: str, spec: dict) -> str | None:
    """Qualifier for a predicate that declares `kinds`, defaulted from the
    ontology. Deliberately the same shape as `pipeline/analyze.py::_kind_for`:
    two answers to "what kind of PART_OF is this" would disagree on the edges
    the trunk and the corpus both assert."""
    declared = ontology.predicates.get(predicate)
    if not declared or not declared.kinds:
        return None
    given = spec.get("kind")
    return given if given in declared.kinds else declared.kinds[0]


def _load_edges(
    conn: db.Conn,
    doc: dict[str, Any],
    *,
    now: str,
    run_id: str = SEED_RUN_ID,
) -> int:
    """Insert relation assertions, validated.

    Validation lives here rather than in the test suite because
    `profile/anchors.yaml` is USER-EDITABLE. A committed fixture can afford to
    have its only check be `test_every_seeded_edge_type_checks`; a file someone
    hand-edits at 11pm cannot. A typo should fail where the file is on screen.

    `validate_anchors` runs the document-only half of this at save time; this
    remains the gate for edges whose endpoints already exist in the database.
    """
    ontology = get_ontology()
    rows = [dict(r) for r in repo.select_entities(conn)]
    types = {r["id"]: r["type"] for r in rows}
    names = {r["id"]: r["name"] for r in rows}

    edges = doc.get("edges") or []
    seen: set[tuple[str, str, str]] = set()
    written = 0

    for spec in edges:
        src, dst, predicate = spec["src"], spec["dst"], spec["predicate"]

        missing = [i for i in (src, dst) if i not in types]
        if missing:
            raise ValueError(
                f"edge {src} -{predicate}-> {dst}: no such entity {missing}. "
                "Declare it in `entities:` or fix the id."
            )
        problem = ontology.check_relation(predicate, types[src], types[dst])
        if problem:
            raise ValueError(f"edge {src} -{predicate}-> {dst}: {problem}")

        # The inline shorthand and an explicit `edges:` row can name the same
        # triple. First one wins; the caller controls which list comes first.
        if (src, predicate, dst) in seen:
            continue
        seen.add((src, predicate, dst))

        db.insert(
            conn,
            "assertions",
            {
                # One relation assertion per declared triple per run; the
                # de-dupe above guarantees the triple is unique in this load.
                "id": _row_id("assert", run_id, src, predicate, dst),
                "run_id": run_id,
                # `fixtures/seed/edges.yaml` carries a real `evidence:` on all 43
                # of its rows; `profile/anchors.yaml` carries none, so those take
                # the sentinel rather than claim a fixture document the anchor
                # loader deliberately never writes. ADR 0195.
                "article_id": spec.get("evidence", DECLARED_ARTICLE_ID),
                "cluster_id": "cluster:seed",
                "kind": "relation",
                "predicate": spec["predicate"],
                # The ontology's declared default when the line omits one, the
                # same rule `analyze` applies to an extracted relation. The
                # setup wizard's schema has no slot for `kind` and a
                # hand-written line often leaves it off, so without this a
                # generated trunk carries a null qualifier on every edge while
                # a hand-written one carries the right value on some.
                "predicate_kind": _kind_for(ontology, spec["predicate"], spec),
                "subject_id_raw": spec["src"],
                "object_id_raw": spec["dst"],
                # NAMES, never ids: `graph_service` serves this string to the
                # reader as the edge's quote, and the panel is where someone
                # goes to ask why an edge exists. The second sentence is the
                # answer, because no article states this and the reader is owed
                # that rather than being left to assume a source.
                "evidence_quote": spec.get(
                    "quote",
                    f"{names.get(spec['src'], spec['src'])} {spec['predicate']} "
                    f"{names.get(spec['dst'], spec['dst'])}. "
                    "Part of the starting structure, not read from an article.",
                ),
                "confidence": float(spec.get("confidence", 0.9)),
                "method": "seed",
                "created_at": now,
            },
        )
        written += 1
    return written


def _load_glossary(conn: db.Conn, doc: dict[str, Any], *, now: str) -> int:
    definitions = doc.get("definitions") or []
    seen_surfaces: dict[str, int] = {}

    for spec in definitions:
        surface = spec["surface"]
        expansion = (spec.get("expansion") or "").strip() or None
        status = spec.get("status", "active")
        seen_surfaces[surface] = seen_surfaces.get(surface, 0) + 1

        # Invariant 3, enforced rather than trusted.
        if status == "active" and not spec.get("grounding_article_id"):
            raise ValueError(f"glossary entry {surface!r} is active but has no grounding article")

        suffix = slugify(expansion) if expansion else ""
        entry_id = (
            f"{glossary_id(surface)}--{suffix}"
            if suffix and status == "ambiguous"
            else glossary_id(surface)
        )

        db.upsert(
            conn,
            "acronym_definitions",
            {
                "id": entry_id,
                "surface": surface,
                "normalized": normalize(surface),
                "expansion": expansion,
                "entity_id": spec.get("entity_id"),
                "method": spec.get("method", "manual"),
                "confidence": float(spec.get("confidence", 0.0)),
                "grounding_article_id": spec.get("grounding_article_id"),
                "grounding_url": spec.get("grounding_url"),
                "grounding_quote": (spec.get("grounding_quote") or "").strip() or None,
                "status": status,
                "sighting_count": int(spec.get("sighting_count", 1)),
                "article_count": int(spec.get("article_count", 1)),
                "first_seen": now,
                "last_seen": now,
            },
            key=["id"],  # id is deterministic from surface+expansion
        )

    # A surface with two accepted expansions must never be auto-applied.
    for surface, count in seen_surfaces.items():
        if count > 1:
            db.upsert(
                conn,
                "ambiguous_surfaces",
                {
                    "normalized": normalize(surface),
                    "reason": f"{count} accepted expansions",
                    "created_at": now,
                },
                key=["normalized"],
            )

    return len(definitions)


def _load_brief(conn: db.Conn, path: Path, *, now: str) -> dict[str, int]:
    from venona.services.alias_index import build_index

    front, body = _split_front_matter(path.read_text())
    brief_id = front["id"]

    for source in front.get("sources") or []:
        db.upsert(
            conn,
            "documents",
            {
                "id": source["id"],
                "run_id": SEED_RUN_ID,
                "source_id": None,
                "url": source["url"],
                "url_key": source["url"],
                "title": source["title"],
                "publisher": source.get("publisher"),
                "text_nfc": source.get("text", source["title"]),
                "text_chars": len(source.get("text", source["title"])),
                "excerpt": source.get("excerpt"),
                "published_at": source.get("published_at"),
                "date_confidence": "feed",
                "fetched_at": now,
                "content_hash": source["id"],
                "site_domain": "example.test",
                "cluster_id": f"cluster:{source['id']}",
                "created_at": now,
            },
            key=["id"],
        )

    db.upsert(
        conn,
        "clusters",
        {
            "id": "cluster:seed",
            # domain-lint: ok -- the dev seed corpus's article id (fixtures/seed/)
            "canonical_doc_id": "doc:seed-ngc2-overview",
            "title": front["title"],
            "published_at": front.get("date"),
            "relevance_overall": 88,
            "tier": "must_read",
            "first_seen": now,
            "last_seen": now,
        },
        key=["id"],
    )

    # ONE CLUSTER PER SOURCE, because the documents and items above already name
    # `cluster:<doc id>` and only `cluster:seed` is declared above -- without this
    # loop three of the four sources have no cluster row at all. That is invisible
    # until something joins through it: `select_brief_items_by_brief_id` reaches
    # `published_at` via `clusters.canonical_doc_id`, so those items carry no age
    # and the rail renders them undated.
    for source in front.get("sources") or []:
        db.upsert(
            conn,
            "clusters",
            {
                "id": f"cluster:{source['id']}",
                "canonical_doc_id": source["id"],
                "title": source["title"],
                "published_at": source.get("published_at"),
                "relevance_overall": 72,
                "tier": "worth_knowing",
                "first_seen": now,
                "last_seen": now,
            },
            key=["id"],
        )

    db.upsert(
        conn,
        "briefs",
        {
            "id": brief_id,
            "date": str(front["date"]),
            "run_id": SEED_RUN_ID,
            "title": front["title"],
            "dek": front.get("dek"),
            "generated_at": now,
            "model": "seed",
            "unresolved_mentions": db.jdump(front.get("unresolved_mentions") or []),
            "source_ids": db.jdump([s["id"] for s in front.get("sources") or []]),
            "created_at": now,
        },
        key=["id"],
    )
    repo.delete_brief_blocks_by_brief_id(conn, brief_id)

    # Both formats, on purpose: `brief_items` is what a brief renders from, and
    # `brief_blocks` is the compatibility path briefs written before it still
    # need. Seed only the blocks and a seeded instance exercises the
    # compatibility path alone, with the item rail absent entirely --
    # `fixtures/seed/brief.md` carries that incident.
    #
    # `ord` is the render order: Postgres has no rowid and `rank` restarts per
    # section, so nothing else stands between the brief and the heap's order.
    items = front.get("items") or []
    if items:
        repo.delete_brief_items_by_brief_id(conn, brief_id)
        per_section: dict[str, int] = {}
        for position, item in enumerate(items, start=1):
            section = item["section"]
            per_section[section] = per_section.get(section, 0) + 1
            db.insert(
                conn,
                "brief_items",
                {
                    "id": f"item:{brief_id}:i{position:02d}",
                    "brief_id": brief_id,
                    "section": section,
                    "rank": per_section[section],
                    "cluster_id": item.get("cluster_id"),
                    "headline": item["headline"],
                    "state": item.get("state") or "new",
                    "state_note": item.get("state_note"),
                    "top_rank": item.get("top_rank"),
                    "top_line": item.get("top_line"),
                    "ord": position,
                    "created_at": now,
                },
            )
        # The clusters this brief claimed. Without these rows `_emit_metrics`
        # counts every one of them as `already_briefed` on the NEXT run, and the
        # funnel stops reconciling.
        for item in items:
            if item.get("cluster_id"):
                db.upsert(
                    conn,
                    "brief_clusters",
                    {
                        "brief_id": brief_id,
                        "cluster_id": item["cluster_id"],
                        "rank": 1,
                        "created_at": now,
                    },
                    key=["brief_id", "cluster_id"],
                )

    index = build_index(conn)
    blocks = _blocks_from_markdown(body)

    # Everything active is in tier A for the seed brief: there is no model
    # mention set to gate against, and we want the fixture to exercise the full
    # annotation path.
    tier_a = frozenset(row["id"] for row in repo.select_entities_2(conn))
    blocks = annotate_blocks(blocks, index, tier_a=tier_a)

    for block in blocks:
        db.insert(
            conn,
            "brief_blocks",
            {
                "id": f"{brief_id}:{block.id}",
                "brief_id": brief_id,
                "section": block.section,
                "heading": block.heading,
                "ordinal": block.ordinal,
                "type": block.type,
                "depth": block.depth,
                "text": block.text,
                "spans": db.jdump(block.spans),
                "marks": db.jdump([m.as_dict() for m in block.marks]),
            },
        )

    return {
        "documents": len(front.get("sources") or []),
        "blocks": len(blocks),
        "items": len(items),
    }


def _blocks_from_markdown(body: str) -> list[Block]:
    """Split on `##` so each section keeps its heading, then flatten."""
    blocks: list[Block] = []
    section = 0
    heading: str | None = None
    buffer: list[str] = []
    ordinal = 0

    def flush() -> None:
        nonlocal ordinal
        if not buffer:
            return
        parsed = markdown_to_blocks(
            "\n".join(buffer), section=section, heading=heading, id_prefix="b"
        )
        for block in parsed:
            ordinal += 1
            block.ordinal = ordinal
            block.id = f"b{ordinal}"
            blocks.append(block)
        buffer.clear()

    for line in body.splitlines():
        if line.startswith("## "):
            flush()
            section += 1
            heading = line[3:].strip()
            continue
        buffer.append(line)
    flush()

    return blocks
