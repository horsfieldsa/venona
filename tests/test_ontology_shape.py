"""Names interpolated into Cypher must be anchored at BOTH ends.

Entity-type labels and predicate relationship-types cannot be parameterised, so
their names are f-string-spliced into `MERGE`. Entity types were validated with
`.fullmatch`; predicates were validated with `.match`, which accepts a trailing
newline because `$` matches before a final one. The newline is inert Cypher
whitespace, not an injection -- but the two-gate arrangement `writer.py` claims
for predicates has to actually hold, and one anchored-validation test closes the
whole `.match`/`.fullmatch` class.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml
from venona.ontology import OntologyError, parse_ontology

_BASE = yaml.safe_load(
    (Path(__file__).resolve().parents[1] / "config" / "ontology.yaml").read_text()
)


def test_the_base_ontology_parses() -> None:
    assert parse_ontology(copy.deepcopy(_BASE)) is not None


def test_a_predicate_name_with_a_trailing_newline_is_refused() -> None:
    raw = copy.deepcopy(_BASE)
    name, spec = next(iter(raw["predicates"].items()))
    raw["predicates"][f"{name}\n"] = spec

    with pytest.raises(OntologyError):
        parse_ontology(raw)


def test_an_entity_type_name_with_a_trailing_newline_is_refused() -> None:
    raw = copy.deepcopy(_BASE)
    name, spec = next(iter(raw["entity_types"].items()))
    raw["entity_types"][f"{name}\n"] = spec

    with pytest.raises(OntologyError):
        parse_ontology(raw)
