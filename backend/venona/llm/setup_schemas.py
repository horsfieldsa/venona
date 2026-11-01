"""JSON Schemas for the domain-setup generation run.

Two shapes, deliberately. Structured where CODE assembles the result (survey
facets, source proposals, anchor batches -- deduplicated, verified and
composed in `setup_service`), and `{document: string}` where the result IS a
config document -- commented YAML and prose are load-bearing in this repo
(`add_to_stoplist` refuses a round-trip for the same reason), so the model
writes the file text and the real validator judges it.
"""

from __future__ import annotations

from typing import Any


def setup_survey_schema() -> dict[str, Any]:
    """One facet of the domain dossier."""
    return {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "3-6 sentences on what this facet looks like in the domain",
            },
            "items": {
                "type": "array",
                "maxItems": 40,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "detail": {"type": "string", "description": "one sentence"},
                        "url": {"type": ["string", "null"]},
                    },
                    # Every property named, with absence expressed as null: a
                    # provider that validates strictly rejects a `required` that
                    # omits one, and null reads the same to every consumer here.
                    "required": ["name", "detail", "url"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["summary", "items"],
        "additionalProperties": False,
    }


def setup_sources_schema() -> dict[str, Any]:
    """Candidate feeds and discovery sweeps. Proposals only: the pipeline
    fetches every feed itself before any of them enters sources.yaml."""
    return {
        "type": "object",
        "properties": {
            "feeds": {
                "type": "array",
                "maxItems": 20,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "snake_case, stable"},
                        "name": {"type": "string"},
                        "url": {"type": "string", "description": "the RSS/Atom feed URL"},
                        "publisher": {"type": "string"},
                    },
                    "required": ["id", "name", "url", "publisher"],
                    "additionalProperties": False,
                },
            },
            "sweeps": {
                "type": "array",
                "maxItems": 10,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "snake_case, sweep_-prefixed"},
                        "name": {"type": "string"},
                        "tags": {
                            "type": "array",
                            "maxItems": 6,
                            "items": {"type": "string"},
                            "description": "snake_case search themes for this sweep",
                        },
                        "rationale": {"type": "string"},
                    },
                    "required": ["id", "name", "tags", "rationale"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["feeds", "sweeps"],
        "additionalProperties": False,
    }


def setup_document_schema() -> dict[str, Any]:
    """A whole config document as text. The matching validator judges it."""
    return {
        "type": "object",
        "properties": {
            "document": {
                "type": "string",
                "description": "the complete file text, comments included",
            }
        },
        "required": ["document"],
        "additionalProperties": False,
    }


def setup_anchors_schema(entity_types: list[str], predicates: list[str]) -> dict[str, Any]:
    """One batch of anchor trunk entities.

    Names, never ids -- code mints ids (`slugify`) and resolves edge endpoints
    by name, the same rule the anchor proposal flow follows. Predicates are the
    DRAFTED ontology's own -- the file these anchors will be validated against
    and applied onto. An enum of only the anchor spine's five forced the model
    to shoehorn every natural relation into them (a CEO who LEADS became
    MANAGES, a shipped technology became PRODUCES), so the first generated
    trunk violated its own ontology's domain/range 49 times.
    """
    return {
        "type": "object",
        "properties": {
            "entities": {
                "type": "array",
                "maxItems": 80,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "type": {"type": "string", "enum": entity_types},
                        "summary": {"type": "string", "description": "one sentence"},
                        "aliases": {
                            "type": ["array", "null"],
                            "maxItems": 6,
                            "items": {"type": "string"},
                        },
                        "relevance": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": ["name", "type", "summary", "relevance", "aliases"],
                    "additionalProperties": False,
                },
            },
            "edges": {
                "type": "array",
                "maxItems": 120,
                "items": {
                    "type": "object",
                    "properties": {
                        "src_name": {"type": "string"},
                        "predicate": {"type": "string", "enum": predicates},
                        "dst_name": {"type": "string"},
                    },
                    "required": ["src_name", "predicate", "dst_name"],
                    "additionalProperties": False,
                },
            },
            "ambiguous": {
                "type": "array",
                "maxItems": 20,
                "items": {
                    "type": "object",
                    "properties": {
                        "surface": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["surface", "reason"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["entities", "edges", "ambiguous"],
        "additionalProperties": False,
    }
