"""The research <-> graph contract, loaded from config/ontology.yaml.

Everything downstream is DERIVED from this module: the JSON Schema enums handed
to the model, the extraction guidance injected into its system prompt, the
writer's whitelist, the projector's filter, the docs, and the /api/meta
endpoints. Nothing may hardcode a predicate or entity type.

The reason this is a typed contract rather than a bare enum: enum membership
does not constrain MEANING. `Anduril --AWARDED_TO--> "$99M OTA"` is a valid
predicate used backwards. `check_relation()` rejects that by consulting the
declared domain and range, so a semantically inverted assertion never reaches
the graph.

Adjusting the ontology over time is deliberately cheap. Because the graph is a
projection of the assertion log (decision 2), the loop is: edit the YAML, bump
`version`, add a changelog entry, run `venona graph rebuild`. Retiring a
predicate simply stops it projecting -- its assertions stay on disk and return
if it is re-added.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Test override ONLY. When set, both the reader (`get_ontology`) and the
#: writer (`config_service._path_for`) resolve here, so a sandboxed test
#: redirects them together. Production resolution is `ontology_path()`, which
#: follows `settings.config_dir` -- one local machine can run several
#: instances, and each must read and write its OWN ontology, not the repo's.
ONTOLOGY_PATH: Path | None = None


def ontology_path() -> Path:
    if ONTOLOGY_PATH is not None:
        return ONTOLOGY_PATH
    from venona.settings import get_settings

    return get_settings().config_dir / "ontology.yaml"


# Predicate names are interpolated straight into Cypher -- `graph/writer.py`
# builds `MERGE (a)-[e:{predicate}]->(b)`, because a relationship type cannot be
# parameterised. `str.isupper()` was the only guard, and it is true for any
# string containing no lowercase CASED characters: `FOO]->() MATCH (n) DETACH
# DELETE n //` passes it. That was reachable only by editing the repo, which is
# already code execution -- but the ontology is editable over HTTP now, so the
# shape is checked here rather than trusted.
PREDICATE_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")

# The same threat model, for the type set now that it is per-domain: a type
# name becomes a Cypher LABEL (`graph/writer.py`) and a prefix is spliced into
# the id-pattern alternation and into `--entity-<prefix>` CSS custom-property
# names. Shape-checked at parse, guarded again at each interpolation site.
TYPE_NAME = re.compile(r"^[A-Z][A-Za-z0-9]*$")
PREFIX_SHAPE = re.compile(r"^[a-z][a-z0-9]{0,11}$")

#: The colour-slot palette size in `web/src/styles/tokens.css`
#: (`--entity-slot-1..N`) -- and therefore the cap on a domain's entity-type
#: count. Raising it is palette design work: a new tier-1 hue family that
#: passes the contrast sweep, then this constant.
MAX_ENTITY_TYPES = 9

# The predicates that attach something to the graph's spine. A subset of the
# ontology, and the one part of it that code depends on by NAME: `llm/schemas.py`
# compiles them into the anchor response schema, which is what actually
# constrains what `venona anchors propose` may suggest.
#
# The list existed twice -- as a literal in `anchor_schema`, in the module whose
# docstring says nothing is hardcoded, and as a constant in `pipeline/anchors.py`
# that nothing read. Renaming one of these through the config editor would have
# broken anchoring with nothing to catch it. One list now, checked on save.
ANCHOR_PREDICATES = ("PART_OF", "BASED_IN", "WORKS_FOR", "MANAGES", "PRODUCES")


class OntologyError(ValueError):
    """Raised at load time. An invalid ontology must fail startup, not a run."""


@dataclass(frozen=True)
class EntityTypeSpec:
    name: str
    prefix: str
    description: str
    guidance: str = ""
    examples: tuple[str, ...] = ()
    org_types: tuple[str, ...] = ()
    platform_types: tuple[str, ...] = ()
    #: Types this one is routinely CONFUSED with, not types it relates to.
    #:
    #: Two entities of the same name and different types are one of two very
    #: different things, and nothing in the names tells them apart: `GE F414`
    #: typed Platform in one article and Technology in another is ONE engine
    #: read twice, while `Havoc` as a Company and as a Platform is a firm and
    #: an unrelated missile. Only the domain knows which of its own type pairs
    #: blur, so it says so here and the curation queue asks the right question.
    #:
    #: Read symmetrically (see `Ontology.confusable`), so declaring it on one
    #: side is enough. An empty list is a statement: this type's boundary is
    #: real, and a shared name across it is a collision.
    confusable_with: tuple[str, ...] = ()
    #: 1-based index into the `--entity-slot-*` palette. Defaults by
    #: declaration order; the app maps `--entity-<prefix>` onto the slot at
    #: boot, so colour needs no build step for a generated domain.
    color_slot: int = 0
    #: Whether a minted entity of this type gets `acronym_variants` written as
    #: aliases.
    #:
    #: True for the types a publication actually abbreviates. False where the
    #: initials are an artefact: a person's name generates one from their title
    #: and initials -- `Adm. Brad Cooper` yields `ABC`, `Bassam al-Hassan`
    #: yields `BAH` -- and those collide with real acronyms while no article
    #: ever writes them. Only the domain knows which of its own types are
    #: abbreviated, so it says so here.
    generate_acronyms: bool = True


@dataclass(frozen=True)
class PredicateSpec:
    name: str
    domain: tuple[str, ...]
    range: tuple[str, ...]
    description: str
    guidance: str = ""
    example: str = ""
    symmetric: bool = False
    kinds: tuple[str, ...] = ()
    #: Keep this predicate out of `allShortestPaths`. For a predicate whose range
    #: is a hub every member of its domain shares, a path through it is true and
    #: says nothing: "these two are both based in the same country" is two hops
    #: and buries the route the reader asked for. This is the flag the ontology's
    #: own argument for splitting such a predicate out of the org chart assumes
    #: exists -- the split made it filterable, and the path query is the one
    #: caller that had no way to filter.
    excluded_from_paths: bool = False


@dataclass(frozen=True)
class RewriteSpec:
    """One row of the `rewrites:` table: an ill-typed shape with exactly one
    safe legal rendering, applied by `analyze` before the domain/range check.

    The loader guarantees the two properties that make the table safe to apply
    blindly: every `when` shape is ill-typed (a legal triple is never
    rewritten) and every `then` result is well-typed. Together those also make
    it acyclic -- an output can never match another row's input.
    """

    predicates: tuple[str, ...]
    subjects: tuple[str, ...]
    objects: tuple[str, ...]
    to_predicate: str
    swap: bool = False
    note: str = ""


@dataclass(frozen=True)
class Ontology:
    version: int
    entity_types: dict[str, EntityTypeSpec]
    predicates: dict[str, PredicateSpec]
    internal_predicates: dict[str, dict[str, str]] = field(default_factory=dict)
    changelog: tuple[dict[str, Any], ...] = ()
    rewrites: tuple[RewriteSpec, ...] = ()

    # ── derived views ────────────────────────────────────────────────
    @property
    def type_names(self) -> tuple[str, ...]:
        return tuple(self.entity_types)

    @property
    def predicate_names(self) -> tuple[str, ...]:
        return tuple(self.predicates)

    @property
    def path_predicate_names(self) -> tuple[str, ...]:
        """What `allShortestPaths` may walk. See `PredicateSpec.excluded_from_paths`."""
        return tuple(name for name, spec in self.predicates.items() if not spec.excluded_from_paths)

    @property
    def type_prefix(self) -> dict[str, str]:
        return {name: spec.prefix for name, spec in self.entity_types.items()}

    @property
    def prefix_type(self) -> dict[str, str]:
        return {spec.prefix: name for name, spec in self.entity_types.items()}

    def generates_acronyms(self, entity_type: str | None) -> bool:
        """May a minted entity of this type carry generated acronym aliases?

        Defaults to true for a type the ontology does not know, because the
        generator's output is the thing being gated and an unrecognised type is
        not a reason to change how it behaves.
        """
        spec = self.entity_types.get(entity_type or "")
        return spec.generate_acronyms if spec else True

    def confusable(self, a: str | None, b: str | None) -> bool:
        """Do these two types blur, so a shared name is likely ONE thing?

        Symmetric by construction: a domain that writes the pair once should not
        have to remember to write it twice, and a one-sided declaration would
        make the answer depend on argument order -- which for a question about
        two entities has no meaningful order.

        Same type is trivially true and never reaches a caller, since a pair of
        one type is a plain duplicate.
        """
        if not a or not b:
            return False
        if a == b:
            return True
        left, right = self.entity_types.get(a), self.entity_types.get(b)
        return bool((left and b in left.confusable_with) or (right and a in right.confusable_with))

    @property
    def symmetric_predicates(self) -> frozenset[str]:
        return frozenset(n for n, s in self.predicates.items() if s.symmetric)

    def org_types(self, entity_type: str | None = None) -> tuple[str, ...]:
        """The `org_type` sub-vocabulary: for one type, or the union of all.

        NOT `entity_types["Organization"].org_types`: v3 split that type into
        four, so it is a `KeyError`. Each of the four declares its own narrower
        vocabulary -- a `program_office` is only ever a GovernmentBody -- so the
        per-type answer is the useful one, and the union is what the response
        schema's enum needs.
        """
        if entity_type is not None:
            spec = self.entity_types.get(entity_type)
            return spec.org_types if spec else ()
        seen: dict[str, None] = {}
        for spec in self.entity_types.values():
            for name in spec.org_types:
                seen.setdefault(name, None)
        return tuple(seen)

    # ── validation ───────────────────────────────────────────────────
    def check_relation(self, predicate: str, subject_type: str, object_type: str) -> str | None:
        """Return an error string, or None if the assertion is well-typed.

        This is the check an enum cannot do: it catches a real predicate used
        with the wrong operands or in the wrong direction.
        """
        spec = self.predicates.get(predicate)
        if spec is None:
            return (
                f"predicate {predicate!r} is not in the ontology "
                f"(v{self.version}); add it to config/ontology.yaml"
            )
        # `spec.example` is labelled as an example on purpose. Unlabelled, it
        # read as the assertion being rejected, so every warning for a given
        # predicate looked identical and named entities that were never in the
        # article. Callers with the surfaces to hand append the real triple.
        if subject_type not in spec.domain:
            return (
                f"{predicate} subject must be one of {list(spec.domain)}, "
                f"got {subject_type} -- correct form e.g. {spec.example}"
            )
        if object_type not in spec.range:
            return (
                f"{predicate} object must be one of {list(spec.range)}, "
                f"got {object_type} -- correct form e.g. {spec.example}"
            )
        return None

    def rewrite_for(
        self, predicate: str, subject_type: str, object_type: str
    ) -> RewriteSpec | None:
        """The `rewrites:` row covering this shape, or None.

        A hit means the triple as asserted is ill-typed but has exactly one
        safe legal rendering -- the loader guarantees applying the row yields a
        triple `check_relation` accepts.
        """
        for row in self.rewrites:
            if (
                predicate in row.predicates
                and subject_type in row.subjects
                and object_type in row.objects
            ):
                return row
        return None

    # ── generated artifacts ──────────────────────────────────────────
    def entity_schema_enum(self) -> list[str]:
        return list(self.type_names)

    def predicate_schema_enum(self) -> list[str]:
        return list(self.predicate_names)

    def extraction_guide(self) -> str:
        """Prose handed to the model in the analyze system prompt.

        Generated so the guidance the model reads and the rules the writer
        enforces can never disagree.
        """
        lines: list[str] = [f"# Ontology v{self.version}", "", "## Entity types", ""]

        for name, spec in self.entity_types.items():
            lines.append(f"### {name}")
            lines.append(spec.description.strip())
            if spec.guidance:
                lines.append(spec.guidance.strip())
            if spec.examples:
                lines.append(f"Examples: {', '.join(spec.examples)}")
            if spec.org_types:
                lines.append(f"`org_type` must be one of: {', '.join(spec.org_types)}")
            if spec.platform_types:
                lines.append(f"`platform_type` must be one of: {', '.join(spec.platform_types)}")
            lines.append("")

        lines += [
            "## Relationships",
            "",
            "Direction matters. Each entry reads subject --PREDICATE--> object.",
            "Assert a relationship only when the article states it; the evidence",
            "quote must contain it.",
            "",
        ]
        for name, spec in self.predicates.items():
            lines.append(f"### {name}")
            lines.append(f"{' | '.join(spec.domain)} --{name}--> {' | '.join(spec.range)}")
            lines.append(spec.description.strip())
            if spec.guidance:
                lines.append(spec.guidance.strip())
            if spec.example:
                lines.append(f"e.g. {spec.example}")
            lines.append("")

        if self.rewrites:
            lines += [
                "## Corrections applied automatically",
                "",
                "These ill-typed shapes are rewritten to the legal edge instead of",
                "rejected. Prefer asserting the corrected form directly:",
                "",
            ]
            for row in self.rewrites:
                shape = (
                    f"{' | '.join(row.subjects)} "
                    f"--{' | '.join(row.predicates)}--> "
                    f"{' | '.join(row.objects)}"
                )
                fix = f"{row.to_predicate}{' (endpoints swapped)' if row.swap else ''}"
                lines.append(f"- {shape} is stored as {fix}")
            lines.append("")

        return "\n".join(lines).strip() + "\n"

    def markdown_reference(self) -> str:
        """Generates docs/graph-schema.md. Never hand-edited."""
        lines = [
            "# Graph schema",
            "",
            "<!-- GENERATED from config/ontology.yaml by `make schema`. Do not edit. -->",
            "",
            f"Ontology version **{self.version}**.",
            "",
            "## Entity types",
            "",
            "| Type | Id prefix | Design token | Description |",
            "|---|---|---|---|",
        ]
        for name, spec in self.entity_types.items():
            lines.append(
                f"| `{name}` | `{spec.prefix}:` | `--entity-{spec.prefix}` | "
                f"{spec.description.strip().replace(chr(10), ' ')} |"
            )

        lines += [
            "",
            "## Predicates",
            "",
            "| Predicate | Domain | Range | Symmetric | Meaning |",
            "|---|---|---|---|---|",
        ]
        for name, spec in self.predicates.items():
            lines.append(
                f"| `{name}` | {', '.join(spec.domain)} | {', '.join(spec.range)} | "
                f"{'yes' if spec.symmetric else ''} | "
                f"{spec.description.strip().replace(chr(10), ' ')} |"
            )

        if self.internal_predicates:
            lines += [
                "",
                "## Infrastructure edges",
                "",
                "Written by the projector, never asserted by a model -- so they are",
                "not in the whitelist `graph/writer.py` enforces and cannot be",
                "produced by extraction. Declared here so the graph's whole shape is",
                "in one document.",
                "",
                "| Predicate | From | To |",
                "|---|---|---|",
            ]
            for name, spec in self.internal_predicates.items():
                lines.append(f"| `{name}` | {spec.get('from', '')} | {spec.get('to', '')} |")

        if self.rewrites:
            lines += [
                "",
                "## Rewrites",
                "",
                "Ill-typed shapes `analyze` rewrites to the legal edge instead of",
                "rejecting (`method='extraction_rewritten'`, counted under",
                "`rewrite_<PREDICATE>`).",
                "",
                "| When (subject → object) | Becomes | Swapped |",
                "|---|---|---|",
            ]
            for row in self.rewrites:
                lines.append(
                    f"| {', '.join(row.subjects)} `{', '.join(row.predicates)}` "
                    f"{', '.join(row.objects)} | `{row.to_predicate}` | "
                    f"{'yes' if row.swap else ''} |"
                )

        lines += ["", "## Examples", ""]
        for name, spec in self.predicates.items():
            if spec.example:
                lines.append(f"- `{name}` — {spec.example}")

        lines += ["", "## Changelog", ""]
        for entry in self.changelog:
            lines.append(
                f"- **v{entry.get('version')}** ({entry.get('date')}) — "
                f"{str(entry.get('change', '')).strip()}"
            )

        return "\n".join(lines) + "\n"


def parse_ontology(raw: dict[str, Any] | None) -> Ontology:
    """Validate a parsed ontology document.

    Split out of `_load` so the save path in `services/config_service.py` can
    reject a document with the SAME errors startup would raise, rather than a
    second implementation that drifts.
    """
    raw = raw or {}

    # Unknown keys are rejected rather than ignored. This parser reads only the
    # keys it knows, so before this check a typo -- `rewrite:` for `rewrites:`
    # -- saved successfully, reported success, and did nothing. Same guard
    # `_validate_settings` has for settings.yaml.
    known_keys = {
        "version",
        "entity_types",
        "predicates",
        "internal_predicates",
        "changelog",
        "rewrites",
    }
    unknown = sorted(set(raw) - known_keys)
    if unknown:
        raise OntologyError(
            f"unknown top-level key(s) {unknown} -- this file is read by key, so an "
            f"unrecognised one is silently dead. Known keys: {sorted(known_keys)}"
        )

    entity_types: dict[str, EntityTypeSpec] = {}
    prefixes: dict[str, str] = {}
    slots: dict[int, str] = {}
    declared_count = len(raw.get("entity_types") or {})
    if declared_count > MAX_ENTITY_TYPES:
        raise OntologyError(
            f"{declared_count} entity types declared; the colour palette has "
            f"{MAX_ENTITY_TYPES} slots. Retire a type, or grow the palette "
            f"(tokens.css + the contrast sweep) first."
        )
    for position, (name, spec) in enumerate((raw.get("entity_types") or {}).items(), start=1):
        if not TYPE_NAME.fullmatch(name):
            raise OntologyError(
                f"entity type {name!r} must match {TYPE_NAME.pattern} -- the name "
                f"is interpolated into Cypher as a label and cannot be parameterised"
            )
        prefix = spec["prefix"]
        if not PREFIX_SHAPE.fullmatch(prefix):
            raise OntologyError(
                f"prefix {prefix!r} must match {PREFIX_SHAPE.pattern} -- it is an id "
                f"namespace, a regex alternation member and a CSS token name"
            )
        if prefix in prefixes:
            raise OntologyError(
                f"prefix {prefix!r} used by both {prefixes[prefix]} and {name}; "
                f"prefixes must be unique -- they are the entity id namespace (C4)"
            )
        prefixes[prefix] = name
        slot = spec.get("color_slot", position)
        if not isinstance(slot, int) or not 1 <= slot <= MAX_ENTITY_TYPES:
            raise OntologyError(
                f"{name}: color_slot must be an integer between 1 and {MAX_ENTITY_TYPES}"
            )
        if slot in slots:
            raise OntologyError(
                f"color_slot {slot} used by both {slots[slot]} and {name}; two types "
                f"sharing a slot would be indistinguishable everywhere colour carries "
                f"the type"
            )
        slots[slot] = name
        entity_types[name] = EntityTypeSpec(
            name=name,
            prefix=prefix,
            description=spec.get("description", ""),
            guidance=spec.get("guidance", ""),
            examples=tuple(spec.get("examples") or ()),
            org_types=tuple(spec.get("org_types") or ()),
            platform_types=tuple(spec.get("platform_types") or ()),
            confusable_with=tuple(spec.get("confusable_with") or ()),
            color_slot=slot,
            generate_acronyms=bool(spec.get("generate_acronyms", True)),
        )

    # After the whole table is built, because a type may name one declared
    # below it. Validated at load for the reason every other reference is: an
    # ontology naming a type that does not exist must fail startup, not surface
    # as a conflict silently classified the wrong way weeks later.
    for name, entity_spec in entity_types.items():
        unknown = [t for t in entity_spec.confusable_with if t not in entity_types]
        if unknown:
            raise OntologyError(
                f"{name}: confusable_with references unknown entity type(s) {unknown}"
            )
        if name in entity_spec.confusable_with:
            raise OntologyError(
                f"{name}: confusable_with names itself; two entities of one type "
                f"are a plain duplicate, which the classifier already handles"
            )

    predicates: dict[str, PredicateSpec] = {}
    for name, spec in (raw.get("predicates") or {}).items():
        if not PREDICATE_NAME.fullmatch(name):
            raise OntologyError(
                f"predicate {name!r} must be UPPER_SNAKE_CASE matching "
                f"{PREDICATE_NAME.pattern} -- it is interpolated into Cypher"
            )
        domain = tuple(spec.get("domain") or ())
        range_ = tuple(spec.get("range") or ())
        for side, types in (("domain", domain), ("range", range_)):
            if not types:
                raise OntologyError(f"{name} has an empty {side}")
            unknown = [t for t in types if t not in entity_types]
            if unknown:
                raise OntologyError(f"{name} {side} references unknown entity type(s) {unknown}")
        symmetric = bool(spec.get("symmetric"))
        if symmetric and set(domain) != set(range_):
            raise OntologyError(
                f"{name} is symmetric so its domain and range must match "
                f"({list(domain)} vs {list(range_)})"
            )
        predicates[name] = PredicateSpec(
            name=name,
            domain=domain,
            range=range_,
            description=spec.get("description", ""),
            guidance=spec.get("guidance", ""),
            example=spec.get("example", ""),
            symmetric=symmetric,
            kinds=tuple(spec.get("kinds") or ()),
            excluded_from_paths=bool(spec.get("excluded_from_paths", False)),
        )

    rewrites = _parse_rewrites(raw.get("rewrites") or (), entity_types, predicates)

    return Ontology(
        version=int(raw.get("version", 0)),
        entity_types=entity_types,
        predicates=predicates,
        internal_predicates=raw.get("internal_predicates") or {},
        changelog=tuple(raw.get("changelog") or ()),
        rewrites=rewrites,
    )


def _parse_rewrites(
    raw_rows: Any,
    entity_types: dict[str, EntityTypeSpec],
    predicates: dict[str, PredicateSpec],
) -> tuple[RewriteSpec, ...]:
    """Validate the `rewrites:` table against the predicates it references.

    Two invariants, enforced per (predicate, subject, object) combination a row
    covers: the `when` shape must be ILL-typed (a legal triple must never be
    rewritten) and the `then` result must be WELL-typed (the point of the
    table). Together they also make the table acyclic -- a well-typed output
    can never match another row's ill-typed input -- which is why no separate
    cycle check exists.
    """

    def as_tuple(value: Any) -> tuple[str, ...]:
        if isinstance(value, str):
            return (value,)
        return tuple(value or ())

    rows: list[RewriteSpec] = []
    for i, spec in enumerate(raw_rows):
        when = spec.get("when") or {}
        then = spec.get("then") or {}
        row = RewriteSpec(
            predicates=as_tuple(when.get("predicate")),
            subjects=as_tuple(when.get("subject")),
            objects=as_tuple(when.get("object")),
            to_predicate=then.get("predicate") or "",
            swap=bool(then.get("swap")),
            note=spec.get("note", ""),
        )
        label = f"rewrites[{i}]"
        if not row.predicates or not row.subjects or not row.objects:
            raise OntologyError(f"{label} needs when.predicate, when.subject and when.object")
        for name in row.predicates:
            if name not in predicates:
                raise OntologyError(f"{label} when.predicate {name!r} is not a predicate")
        target = predicates.get(row.to_predicate)
        if target is None:
            raise OntologyError(f"{label} then.predicate {row.to_predicate!r} is not a predicate")
        for side, types in (("subject", row.subjects), ("object", row.objects)):
            unknown = [t for t in types if t not in entity_types]
            if unknown:
                raise OntologyError(
                    f"{label} when.{side} references unknown entity type(s) {unknown}"
                )

        for name in row.predicates:
            source = predicates[name]
            for subj in row.subjects:
                for obj in row.objects:
                    if subj in source.domain and obj in source.range:
                        raise OntologyError(
                            f"{label}: {subj} --{name}--> {obj} is already legal -- "
                            f"a rewrite may only cover ill-typed shapes"
                        )
                    new_subj, new_obj = (obj, subj) if row.swap else (subj, obj)
                    if new_subj not in target.domain or new_obj not in target.range:
                        raise OntologyError(
                            f"{label}: rewriting {subj} --{name}--> {obj} yields "
                            f"{new_subj} --{row.to_predicate}--> {new_obj}, which is "
                            f"itself ill-typed ({row.to_predicate} is "
                            f"{list(target.domain)} -> {list(target.range)})"
                        )
        rows.append(row)
    return tuple(rows)


def _load(path: Path) -> Ontology:
    return parse_ontology(yaml.safe_load(path.read_text()))


@lru_cache(maxsize=1)
def get_ontology() -> Ontology:
    return _load(ontology_path())
