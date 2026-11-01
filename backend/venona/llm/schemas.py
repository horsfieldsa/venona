"""JSON Schemas handed to `claude --json-schema`, built at runtime.

None of the vocabularies is hardcoded. Entity types and predicates come from
the ontology (C10); scoring axes come from `profile/scoring_rubric.yaml`. Add a
predicate or reweight an axis and the next call picks it up, with no risk of the
schema and the writer's whitelist disagreeing.

Two rules the schemas enforce structurally, rather than by asking nicely:

  * The model returns SURFACES, never entity ids. Id assignment is resolve.py's
    job. A model given the chance to emit `prog:whatever` will invent one, and
    an invented id is indistinguishable from a real one downstream.
  * `evidence` on a relation is required. An assertion with no quote cannot be
    written to the assertion log anyway (C1), so it must be impossible to
    produce one.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import yaml

from venona.ontology import ANCHOR_PREDICATES, get_ontology
from venona.settings import get_settings


class RubricError(ValueError):
    """Raised at load, so a bad document fails on save rather than mid-run."""


@lru_cache(maxsize=1)
def load_rubric() -> dict[str, Any]:
    settings = get_settings()
    path = settings.profile_dir / "scoring_rubric.yaml"
    raw = yaml.safe_load(path.read_text()) or {}

    axes = raw.get("axes") or []
    if not axes:
        raise RubricError(f"{path} declares no axes")

    ids = [axis["id"] for axis in axes]
    if len(set(ids)) != len(ids):
        raise RubricError(f"{path} has duplicate axis ids")

    total = sum(float(axis.get("weight", 0)) for axis in axes)
    if abs(total - 1.0) > 0.001:
        raise RubricError(
            f"{path} weights sum to {total:.3f}, must be 1.0 "
            f"(otherwise `overall` is not on a 0-100 scale)"
        )
    return raw


def axis_ids() -> list[str]:
    return [axis["id"] for axis in load_rubric()["axes"]]


def axis_weights() -> dict[str, float]:
    return {axis["id"]: float(axis["weight"]) for axis in load_rubric()["axes"]}


def tiers() -> dict[str, int]:
    return {k: int(v) for k, v in (load_rubric().get("tiers") or {}).items()}


def compute_overall(scores: dict[str, float], weights: dict[str, float] | None = None) -> int:
    """`overall` is computed HERE, in Python, never by the model.

    Deterministic, immune to the model drifting on what a number means, and
    reweightable for free -- `POST /api/rescore` replays this over stored axis
    scores with zero model calls.

    `weights` is a caller-supplied reweighting on the same axis ids; it must
    sum to 1.0 or `overall` leaves the 0-100 scale. The default is the
    instance rubric, which `load_rubric` has already validated. Per-user
    relevance passes derived weights here -- the whole feature is this one
    parameter over stored axis scores.
    """
    rubric = load_rubric()
    scale_max = float((rubric.get("scale") or {}).get("max", 5))
    if weights is None:
        weights = axis_weights()

    total = 0.0
    for axis_id, weight in weights.items():
        score = float(scores.get(axis_id, 0) or 0)
        total += (score / scale_max) * weight
    return max(0, min(100, round(total * 100)))


def tier_for(overall: int, cuts: dict[str, int] | None = None) -> str:
    if cuts is None:
        cuts = tiers()
    if overall >= cuts.get("must_read", 70):
        return "must_read"
    if overall >= cuts.get("worth_knowing", 45):
        return "worth_knowing"
    return "background"


# ── analyze ──────────────────────────────────────────────────────────


def analyze_schema() -> dict[str, Any]:
    """Fused extraction + scoring, one call per cluster."""
    ontology = get_ontology()
    rubric = load_rubric()
    scale = rubric.get("scale") or {}
    lo, hi = int(scale.get("min", 0)), int(scale.get("max", 5))

    axis_properties = {
        axis["id"]: {
            "type": "object",
            "properties": {
                "score": {
                    "type": "integer",
                    "minimum": lo,
                    "maximum": hi,
                    "description": axis.get("description", "").strip(),
                },
                "why": {"type": "string", "description": "one sentence"},
            },
            "required": ["score", "why"],
            "additionalProperties": False,
        }
        for axis in rubric["axes"]
    }

    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "relevance": {
                "type": "object",
                "properties": {
                    "axes": {
                        "type": "object",
                        "properties": axis_properties,
                        "required": list(axis_properties),
                        "additionalProperties": False,
                    },
                    "note": {
                        "type": "string",
                        "description": (
                            "2-4 sentences tying this item to specific claims in the "
                            "relevance profile. Say which direction it points."
                        ),
                    },
                    "so_what": {
                        "type": "string",
                        "description": "one sentence: what this means for the reader's work",
                    },
                    "profile_hooks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "verbatim phrases copied from the relevance profile that "
                            "this item matched. Copy exactly; do not paraphrase."
                        ),
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["axes", "note", "so_what", "profile_hooks", "confidence"],
                "additionalProperties": False,
            },
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        # SURFACE, not id. The model never names a canonical id.
                        "surface": {
                            "type": "string",
                            "description": "verbatim as written in the article",
                        },
                        "type": {"type": "string", "enum": list(ontology.type_names)},
                        # The extraction guide tells the model `org_type` must
                        # be one of a type's sub-vocabulary, so a schema that
                        # omits the property under `additionalProperties:
                        # false` instructs it to emit a field the schema
                        # forbids: all 346 Organizations on the first full run
                        # landed with `org_type IS NULL` (2026-08-16).
                        #
                        # NULLABLE AND REQUIRED rather than absent-when-unused,
                        # which is the rule for every optional field Venona
                        # asks a model for. ADR 0241.
                        "org_type": {
                            "type": ["string", "null"],
                            "enum": [*ontology.org_types(), None],
                            "description": "for organisation types only; null otherwise",
                        },
                        "salience": {"type": "number", "minimum": 0, "maximum": 1},
                        "context_sentence": {
                            "type": "string",
                            "description": "the sentence containing the mention, verbatim",
                        },
                        # A SECOND sentence, and the distinction is the whole
                        # point of the field: `context_sentence` is where the
                        # name appears, which is usually a contract award or a
                        # quote and defines nothing. The sentence that says
                        # what the thing IS is somewhere else in the document,
                        # and until this existed nothing could reach it --
                        # `define` read context sentences and no article text.
                        #
                        # NULLABLE AND REQUIRED, per ADR 0241: asked for an
                        # optional field under `additionalProperties: false`,
                        # the model omits the key and every row lands null.
                        "definition_sentence": {
                            "type": ["string", "null"],
                            "description": (
                                "the verbatim sentence in THIS document that says what this "
                                "entity is, if the document says so anywhere; null otherwise"
                            ),
                        },
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": [
                        "surface",
                        "type",
                        "org_type",
                        "salience",
                        "context_sentence",
                        "definition_sentence",
                        "confidence",
                    ],
                    "additionalProperties": False,
                },
            },
            "relations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "subject_surface": {"type": "string"},
                        "subject_type": {"type": "string", "enum": list(ontology.type_names)},
                        "predicate": {
                            "type": "string",
                            "enum": list(ontology.predicate_names),
                        },
                        "object_surface": {"type": "string"},
                        "object_type": {"type": "string", "enum": list(ontology.type_names)},
                        # Required, so an unevidenced relation is unrepresentable.
                        "evidence": {
                            "type": "string",
                            "description": (
                                "the verbatim sentence from the article that states this "
                                "relationship. If no single sentence states it, omit the "
                                "relation entirely."
                            ),
                        },
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": [
                        "subject_surface",
                        "subject_type",
                        "predicate",
                        "object_surface",
                        "object_type",
                        "evidence",
                        "confidence",
                    ],
                    "additionalProperties": False,
                },
            },
            "key_facts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "up to 5 specific, attributable facts",
            },
            "flags": {
                "type": "object",
                "properties": {
                    "press_release": {"type": "boolean"},
                    "opinion": {"type": "boolean"},
                    "speculative": {"type": "boolean"},
                    "paywalled": {"type": "boolean"},
                },
                "required": ["press_release", "opinion", "speculative", "paywalled"],
                "additionalProperties": False,
            },
        },
        "required": ["relevance", "entities", "relations", "key_facts", "flags"],
        "additionalProperties": False,
    }
    if not ontology.org_types():
        # An empty enum is invalid JSON Schema, and a domain with no
        # organisation sub-vocabulary has nothing for the field to say -- so
        # the property is omitted entirely rather than declared unusable.
        del schema["properties"]["entities"]["items"]["properties"]["org_type"]
    return schema


# ── brief ────────────────────────────────────────────────────────────


def parse_brief_sections(raw: dict[str, Any] | None, *, origin: str) -> list[dict[str, str]]:
    """Validate a parsed brief-sections document.

    Separate from `load_brief_sections` so the config editor can reject a bad
    document on save rather than at the end of a run that has already spent
    money. The name and duplicate checks matter here: unchecked, `s["name"]`
    raises a bare `KeyError`, and two desks with the same heading merge silently
    in `brief_service.py`, which keys its section index on the name.
    """
    raw = raw or {}
    sections = raw.get("sections") or []
    if not sections:
        raise RubricError(f"{origin} declares no sections")

    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for index, section in enumerate(sections):
        if not isinstance(section, dict):
            raise RubricError(f"{origin}: section {index + 1} is not a mapping")
        name = (section.get("name") or "").strip()
        if not name:
            raise RubricError(f"{origin}: section {index + 1} has no `name`")
        if name in seen:
            raise RubricError(
                f"{origin}: duplicate section name {name!r} -- stored briefs are "
                f"indexed by heading, so two desks with one name become one desk"
            )
        seen.add(name)
        out.append({"name": name, "covers": (section.get("covers") or "").strip()})
    return out


def load_brief_sections() -> list[dict[str, str]]:
    """The standing desks, from `profile/brief_sections.yaml`.

    Compiled into the response schema as an `enum`, so a section the model
    invents is rejected by the validator instead of quietly creating a one-item
    desk that looks like coverage.
    """
    path = get_settings().profile_dir / "brief_sections.yaml"
    return parse_brief_sections(yaml.safe_load(path.read_text()), origin=str(path))


def section_names() -> list[str]:
    return [s["name"] for s in load_brief_sections()]


#: One vocabulary for both brief schemas. The wire twin is
#: `api/schemas/brief.py`'s `ItemState` Literal; the values are load-bearing
#: in storage, schema and render, and must not drift.
ITEM_STATES = ("new", "follow_up", "ongoing")


def personal_brief_schema() -> dict[str, Any]:
    """One reader's brief: the shared item contract, at one reader's scale.

    The stories arrive pre-scored and pre-evidenced in the user content, in
    the reader's own priority order. That order is not thrown away by the
    desks -- code groups by `section` and ranks within each -- so the model is
    asked which desk a story belongs on and nothing else about arrangement.
    Field wording is copied from `brief_schema` verbatim where shared, so the
    two contracts cannot drift apart in what they ask for.
    """
    sections = section_names()
    return {
        "type": "object",
        "properties": {
            "headline": {
                "type": "string",
                "description": "this reader's day in one line. Not a slogan.",
            },
            "dek": {
                "type": "string",
                "description": (
                    "one sentence under the headline; empty string when the headline stands alone"
                ),
            },
            "items": {
                "type": "array",
                # The shared brief's ceiling. The supply side is narrower and
                # is what actually decides: `personal_brief.start_generation`
                # sends `pipeline.brief_top_clusters` stories into the user
                # content.
                "maxItems": 24,
                "items": {
                    "type": "object",
                    "properties": {
                        "cluster_id": {
                            "type": "string",
                            "description": "the id of the supplied story this is about",
                        },
                        "section": {"type": "string", "enum": sections},
                        "headline": {
                            "type": "string",
                            "description": (
                                "states the finding, with the number in it where "
                                "there is one. Not a teaser."
                            ),
                        },
                        "state": {"type": "string", "enum": list(ITEM_STATES)},
                        "state_note": {
                            "type": "string",
                            "description": (
                                "for follow_up/ongoing: what is NEW since last time, "
                                "in under ten words. Empty string for new."
                            ),
                        },
                        "summary": {
                            "type": "string",
                            "description": (
                                "what the article REPORTS: figures, named people and "
                                "their titles, direct quotes, dates, dollar values. "
                                "No interpretation, no significance, no advice. If it "
                                "is not in the article, it does not belong here."
                            ),
                        },
                        "why_it_matters": {
                            "type": "string",
                            "description": (
                                "the judgement, and the ONLY place it may appear. "
                                "Argue it from THIS reader's emphasis and followed "
                                "names, and from the relationships between the "
                                "entities involved. Say plainly when it cuts against "
                                "the reader's thesis."
                            ),
                        },
                        "top_line": {
                            "type": "string",
                            "description": (
                                "if this is one of the three most consequential items "
                                "today, the single sentence for the TOP block. Empty "
                                "string otherwise. EXACTLY three items must set it -- "
                                "or all of them, if there are fewer than three. Every "
                                "brief has a top block; a thin day still has a most "
                                "consequential item."
                            ),
                        },
                    },
                    "required": [
                        "cluster_id",
                        "section",
                        "headline",
                        "state",
                        "state_note",
                        "summary",
                        "why_it_matters",
                        "top_line",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["headline", "dek", "items"],
        "additionalProperties": False,
    }


def brief_schema() -> dict[str, Any]:
    """Items, not narrative -- and fact separated from judgement by construction.

    Two fields carry the whole editorial argument:

      `summary`         what the article states. Figures, names, quotes.
      `why_it_matters`  the reader-specific judgement, in its own field.

    Two fields rather than one `markdown` blob per section: with one blob an
    opinion has nowhere to live except inside the prose, and "summarise more,
    opine less" becomes a losing prompt-engineering battle against the format
    itself. Split, it is a rule the validator half-enforces and the reader can
    see.

    The model never returns attribution. It returns `cluster_id`, and code joins
    publisher, URL and date from `documents` -- so a claim cannot be hung on the
    wrong outlet, which is the one error a brief must never make. That is
    invariant 2 again: the model decides WHICH, code decides the rest, and no
    offsets ever cross the wire. Entity linking stays a mention SET.
    """
    sections = section_names()
    return {
        "type": "object",
        "properties": {
            "headline": {
                "type": "string",
                "description": (
                    "the day in one line, naming the two or three things that "
                    "actually moved. Not a slogan."
                ),
            },
            "items": {
                "type": "array",
                "maxItems": 24,
                "items": {
                    "type": "object",
                    "properties": {
                        "cluster_id": {
                            "type": "string",
                            "description": "the id of the supplied item this is about",
                        },
                        "section": {"type": "string", "enum": sections},
                        "headline": {
                            "type": "string",
                            "description": (
                                "states the finding, with the number in it where "
                                "there is one. Not a teaser."
                            ),
                        },
                        "state": {"type": "string", "enum": list(ITEM_STATES)},
                        "state_note": {
                            "type": "string",
                            "description": (
                                "for follow_up/ongoing: what is NEW since last time, "
                                "in under ten words. Empty string for new."
                            ),
                        },
                        "summary": {
                            "type": "string",
                            "description": (
                                "what the article REPORTS: figures, named people and "
                                "their titles, direct quotes, dates, dollar values. "
                                "No interpretation, no significance, no advice. If it "
                                "is not in the article, it does not belong here."
                            ),
                        },
                        "why_it_matters": {
                            "type": "string",
                            "description": (
                                "the judgement, and the ONLY place it may appear. Tie "
                                "it to the reader's profile and to the relationships "
                                "between the entities involved -- who is now "
                                "positioned against whom, which seam just opened. "
                                "Say plainly when it cuts against the reader's thesis."
                            ),
                        },
                        "top_line": {
                            "type": "string",
                            "description": (
                                "if this is one of the three most consequential items "
                                "today, the single sentence for the TOP block. Empty "
                                "string otherwise. EXACTLY three items must set it -- "
                                "or all of them, if there are fewer than three. Every "
                                "brief has a top block; a thin day still has a most "
                                "consequential item."
                            ),
                        },
                    },
                    "required": [
                        "cluster_id",
                        "section",
                        "headline",
                        "state",
                        "state_note",
                        "summary",
                        "why_it_matters",
                        "top_line",
                    ],
                    "additionalProperties": False,
                },
            },
            "mentioned_entity_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "ids from the supplied entity list that this brief actually "
                    "discusses. Use only ids given to you."
                ),
            },
            "unresolved_mentions": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "names you wrote about that were NOT in the supplied list, so "
                    "they can be curated in"
                ),
            },
        },
        "required": ["headline", "items", "mentioned_entity_ids", "unresolved_mentions"],
        "additionalProperties": False,
    }


def adjudicate_schema(conflict_ids: list[str]) -> dict[str, Any]:
    """One verdict per conflict pair.

    `conflict_id` is an enum of exactly the ids sent, so the model cannot invent
    one or answer about a pair it was not asked about -- the same discipline as
    the brief's `cluster_id`, and for the same reason: a verdict attached to the
    wrong pair is worse than no verdict.
    """
    return {
        "type": "object",
        "properties": {
            "verdicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "conflict_id": {"type": "string", "enum": conflict_ids},
                        "verdict": {
                            "type": "string",
                            "enum": ["merge", "part_of", "variant", "distinct"],
                            "description": (
                                "merge: one thing under two names. "
                                "part_of: the first is a division, subsidiary or "
                                "unit OF the second. "
                                "variant: one is a version, block, mark or numbered "
                                "model of the other's design. "
                                "distinct: unrelated, or related in some other way."
                            ),
                        },
                        "child": {
                            "type": "string",
                            "description": (
                                "for part_of and variant ONLY: the name of the side "
                                "that is the CHILD (the version, for a variant), copied "
                                "exactly from the pair. Empty otherwise."
                            ),
                        },
                        "why": {
                            "type": "string",
                            "description": "one short line a curator can check at a glance",
                        },
                        "confidence": {
                            "type": "string",
                            "enum": ["certain", "likely", "unsure"],
                            "description": (
                                "certain: the two names are unambiguously one thing "
                                "and you would stake the graph on it. "
                                "likely: probably, but a reader could disagree. "
                                "unsure: you are guessing."
                            ),
                        },
                    },
                    "required": ["conflict_id", "verdict", "child", "why", "confidence"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["verdicts"],
        "additionalProperties": False,
    }


# ── discovery ────────────────────────────────────────────────────────


def discovery_schema() -> dict[str, Any]:
    """Candidate URLs ONLY, never content (ADR 0039) -- the same discipline as
    `acronym_search_schema`. It returns links and a one-line reason, the
    ordinary fetch path fetches and extracts each one, and analyze reads the
    real text.

    This also makes discovery dedup for free: a URL that already arrived via RSS
    hits `documents.url_key` and becomes a sighting rather than a duplicate
    document.

    `published_hint` is advisory and NEVER stored as a date. Feeds already lie
    about dates; a model's guess is worse. `normalize.build_document` derives
    the real one from the fetched page.
    """
    return {
        "type": "object",
        "properties": {
            "theme": {
                "type": "string",
                "description": "which standing theme this sweep covered",
            },
            "candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "title": {"type": "string"},
                        # DESCRIBED, because an undescribed optional field is one
                        # the model fills when the outlet is obvious and skips
                        # otherwise -- and the long-tail results a sweep exists
                        # to find are exactly the ones it skipped, each an
                        # omission the brief rendered as the literal word
                        # `source`. Still not authoritative: `resolve_publisher`
                        # ranks this BELOW the page's own `og:site_name`,
                        # because it is a model's assertion where that is an
                        # observation.
                        "publisher": {
                            "type": ["string", "null"],
                            "description": (
                                "the outlet's masthead as it appears on the page, "
                                "e.g. 'Naval News'. Null rather than guess."
                            ),
                        },
                        "why": {
                            "type": "string",
                            "description": "one line: which part of the profile this hits",
                        },
                        "published_hint": {
                            "type": ["string", "null"],
                            "description": (
                                "advisory only, e.g. '2026-08-04' or 'this week'; "
                                "null when the page does not say"
                            ),
                        },
                    },
                    "required": ["url", "title", "publisher", "why", "published_hint"],
                    "additionalProperties": False,
                },
                "maxItems": 12,
            },
        },
        "required": ["theme", "candidates"],
        "additionalProperties": False,
    }


# ── acronym grounding (Tier 3) ───────────────────────────────────────


def acronym_search_schema() -> dict[str, Any]:
    """Step 1: candidate URLs ONLY. No expansion here -- a quote returned at
    this stage comes from WebFetch's summary rather than the page, so it is
    ungroundable by construction (ADR 0003). It is a search tool, not a source.

    `maxItems` is 8 rather than 5 because candidates are consumed, not just
    ranked: `web_grounding.ground` reads down the list until enough pages
    extract, and live lookups lost half their candidates to 403s from .gov/.mil
    WAFs (2026-08-06). Five proposals with three blocked is one page read.
    """
    return {
        "type": "object",
        "properties": {
            "acronym": {"type": "string"},
            "candidate_urls": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "why": {"type": "string", "description": "one line"},
                    },
                    "required": ["url", "why"],
                    "additionalProperties": False,
                },
                "maxItems": 8,
            },
        },
        "required": ["acronym", "candidate_urls"],
        "additionalProperties": False,
    }


def acronym_read_schema() -> dict[str, Any]:
    """Step 2: quote from text WE supplied and fetched.

    `quote` is verified as a substring of our extracted text before anything is
    accepted, and the expansion's initials must generate the acronym.
    """
    return {
        "type": "object",
        "properties": {
            "found": {"type": "boolean"},
            "expansion": {
                "type": ["string", "null"],
                "description": "null if the supplied text does not define the acronym",
            },
            "quote": {
                "type": ["string", "null"],
                "description": (
                    "verbatim sentence from the SUPPLIED TEXT containing the "
                    "expansion. Copy exactly, character for character. Null if not found."
                ),
            },
        },
        "required": ["found", "expansion", "quote"],
        "additionalProperties": False,
    }


def acronym_adjudicate_schema() -> dict[str, Any]:
    """Step 3, reached only when `verify_expansion` has already refused.

    Two fields and no third. The model is being asked to overturn a structural
    check, so the one thing it must hand back besides the verdict is its
    working -- `how` reaches the trace, and a definition accepted on a
    derivation nobody can read is not auditable.
    """
    return {
        "type": "object",
        "properties": {
            "derives": {"type": "boolean"},
            "how": {
                "type": "string",
                "description": (
                    "one short line mapping each part of the acronym to the word it "
                    "came from, or naming the character that has no source"
                ),
            },
        },
        "required": ["derives", "how"],
        "additionalProperties": False,
    }


def anchor_schema(entity_names: list[str], anchor_names: list[str]) -> dict[str, Any]:
    """One anchor per entity, both sides chosen from ENUMS OF NAMES.

    Never ids. `CLAUDE.md` forbids a model emitting a canonical entity id
    because it will invent one; the model names a side and `anchors.py` maps it
    back, exactly as `adjudicate_schema` does with `child`.

    `"none"` is in the anchor enum on purpose. Without an explicit way to
    decline, a required field makes declining unrepresentable and the model
    attaches everything to something.
    """
    return {
        "type": "object",
        "properties": {
            "attachments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "entity": {"type": "string", "enum": entity_names},
                        "anchor": {"type": "string", "enum": [*anchor_names, "none"]},
                        "relation": {
                            "type": "string",
                            "enum": list(ANCHOR_PREDICATES),
                            "description": (
                                "PART_OF: the entity is a division, command or "
                                "subsidiary of the anchor. "
                                "BASED_IN: the entity is an organisation and the "
                                "anchor is its home country. "
                                "WORKS_FOR: the entity is a person employed by the "
                                "anchor."
                            ),
                        },
                        "why": {
                            "type": "string",
                            "description": "one short line a curator can check at a glance",
                        },
                        # Recorded and shown to the curator, never decided on:
                        # `anchors.py`'s `Proposal.confidence` carries what
                        # would have to be measured before anything could be.
                        "confidence": {
                            "type": "string",
                            "enum": ["certain", "likely", "unsure"],
                            "description": (
                                "certain: you would state this as a fact about the "
                                "organisation without looking it up. "
                                "likely: you believe it and could be wrong on a detail. "
                                "unsure: it is a guess. Say unsure rather than "
                                "declining when a curator would still want to see it."
                            ),
                        },
                    },
                    "required": ["entity", "anchor", "relation", "why", "confidence"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["attachments"],
        "additionalProperties": False,
    }


# ── entity definitions ───────────────────────────────────────────────


def define_write_schema() -> dict[str, Any]:
    """Tier 2: one sentence, written from sentences WE supplied.

    `sources` are the NUMBERS the sentences were labelled with, never article
    ids. Asked for ids, the model returned `2ad781870854b5e10dcd0788` for
    `doc:2ad781870854b5e10dcd0788` -- it silently dropped the prefix, and all
    seventeen good definitions were discarded as uncited (2026-08-07). That is
    the failure CLAUDE.md's "never let a model emit a canonical id" rule exists
    for, and `adjudicate_schema` answers it the same way: the model names
    something out of a set we supplied, and code maps it back to a row.

    The numbers are still checked. One outside the supplied range cites
    something we did not provide, so it is dropped; a definition left citing
    nothing is discarded whole, that being the signature of one written from
    memory.
    """
    return {
        "type": "object",
        "properties": {
            "found": {"type": "boolean"},
            "definition": {
                "type": ["string", "null"],
                "description": (
                    "one sentence saying what the entity is. Null if the supplied "
                    "sentences only mention it without saying what it is."
                ),
            },
            "sources": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "the [n] labels of the supplied sentences this rests on",
            },
        },
        "required": ["found", "definition", "sources"],
        "additionalProperties": False,
    }


#: How many names one document re-read may be asked about. A document naming
#: more undefined entities than this is split across calls rather than
#: truncated -- a name dropped silently is a definition nobody ever looks for
#: again, because the entity keeps its row and stops being a candidate.
DEFINE_EXTRACT_BATCH = 20


def define_extract_schema(names: list[str]) -> dict[str, Any]:
    """Re-reading an article we already hold, for the sentences that DEFINE.

    The backfill's half of what `analyze_schema` now asks for inline. New
    documents get `definition_sentence` folded into the extraction call they
    were already paying for; the 1,293 documents read before that field existed
    have to be re-read, and this is the call that does it -- one per document,
    not one per name.

    `name` is an ENUM of the names we supplied, which is the same discipline
    `define_write_schema` applies to its `[n]` labels: the model selects from a
    set we control rather than emitting an identifier, so a name it invents
    cannot reach `resolve.py`. A verbatim sentence is still checked against the
    document by `verify_definition_sentence` -- the enum only settles WHICH
    entity a sentence is offered for.
    """
    return {
        "type": "object",
        "properties": {
            "definitions": {
                "type": "array",
                "maxItems": len(names),
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "enum": list(names)},
                        "sentence": {
                            "type": ["string", "null"],
                            "description": (
                                "the verbatim sentence in the supplied document that says "
                                "what this entity is; null if the document never says"
                            ),
                        },
                    },
                    "required": ["name", "sentence"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["definitions"],
        "additionalProperties": False,
    }


def define_search_schema() -> dict[str, Any]:
    """Tier 3, step 1: candidate URLs ONLY. See `acronym_search_schema`."""
    return {
        "type": "object",
        "properties": {
            "entity": {"type": "string"},
            "candidate_urls": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "why": {"type": "string", "description": "one line"},
                    },
                    "required": ["url", "why"],
                    "additionalProperties": False,
                },
                "maxItems": 8,
            },
        },
        "required": ["entity", "candidate_urls"],
        "additionalProperties": False,
    }


def define_read_schema() -> dict[str, Any]:
    """Tier 3, step 2: quote from text WE supplied and fetched.

    `quote` is verified as a substring of our extracted text, and as naming the
    entity, before anything is accepted.
    """
    return {
        "type": "object",
        "properties": {
            "found": {"type": "boolean"},
            "definition": {
                "type": ["string", "null"],
                "description": "one sentence. Null if the supplied text does not describe it.",
            },
            "quote": {
                "type": ["string", "null"],
                "description": (
                    "verbatim sentence from the SUPPLIED TEXT that the definition "
                    "rests on. Copy exactly, character for character."
                ),
            },
        },
        "required": ["found", "definition", "quote"],
        "additionalProperties": False,
    }


def questions_schema() -> dict[str, Any]:
    """Study-question generation (epic O). `source_index` names an article by
    its position in the supplied batch -- the model never emits an id -- and
    `quote` must be verbatim; the caller verifies it as a substring and
    rejects the item whole otherwise."""
    return {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["choice", "cloze", "qa"]},
                        "question": {"type": "string"},
                        "choices": {
                            "type": ["array", "null"],
                            "items": {"type": "string"},
                            "description": "exactly four options, kind=choice only",
                        },
                        "answer": {"type": "string"},
                        "source_index": {"type": "integer", "minimum": 1},
                        "quote": {
                            "type": "string",
                            "description": "verbatim from the source article",
                        },
                    },
                    "required": ["kind", "question", "choices", "answer", "source_index", "quote"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["questions"],
        "additionalProperties": False,
    }
