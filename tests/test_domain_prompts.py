"""Byte-identity of the defense prompts across the descriptor refactor.

The golden files under `fixtures/golden/prompts/` were captured from the code
BEFORE the domain descriptor existed, when the defense strings were literals.
Every recorded envelope under `fixtures/llm/` is keyed on the sha of its
system prompt, so a single drifted byte here rekeys them all and the offline
suite fails on "missing fixture" -- these tests exist to make that failure
name the byte instead. If a wording change is ever DELIBERATE, re-capture the
golden file and `make record` the envelopes; that is the existing workflow.

Also holds `DEFENSE_DEFAULTS` equal to the checked-in `config/domain.yaml`, so
the no-file fallback and the file agree.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from venona.domain import DEFENSE_DEFAULTS, DomainError, domain_hash, parse_domain
from venona.llm import prompts as p

REPO = Path(__file__).resolve().parents[1]
GOLDEN = REPO / "fixtures" / "golden" / "prompts"


def _golden(name: str) -> str:
    return (GOLDEN / name).read_text()


def test_every_system_prompt_matches_its_golden_snapshot() -> None:
    assembled = {
        "analyst_instructions.txt": p.analyst_instructions(),
        "discovery_system.txt": p.discovery_system_prompt(),
        "brief_system.txt": p.brief_system_prompt(),
        "acronym_search_system.txt": p.acronym_search_system(),
        "acronym_read_system.txt": p.acronym_read_system(),
        "adjudicate_system.txt": p.adjudicate_system(),
        "anchor_system.txt": p.anchor_system(),
        "define_write_system.txt": p.define_write_system(),
        "define_search_system.txt": p.define_search_system(),
        "define_read_system.txt": p.define_read_system(),
    }
    for name, text in assembled.items():
        golden = _golden(name)
        assert text == golden, (
            f"{name}: assembled prompt drifted from the golden snapshot. This "
            f"rekeys every fixture under fixtures/llm/ whose call uses it. If "
            f"deliberate, re-capture the golden and `make record`."
        )


def test_the_lookup_queries_match_their_goldens() -> None:
    from venona.pipeline.acronyms import AcronymGrounding
    from venona.pipeline.define import EntityGrounding

    assert EntityGrounding().search_prompt("EXAMPLE", known=None) == _golden(
        "define_search_query.txt"
    )
    assert AcronymGrounding({}).search_prompt("EXAMPLE", known=None) == _golden(
        "acronym_search_query.txt"
    )


def test_the_checked_in_file_equals_the_code_defaults() -> None:
    """A deployment without config/domain.yaml falls back to DEFENSE_DEFAULTS;
    one with the checked-in file parses it. Both paths must agree."""
    parsed = parse_domain(yaml.safe_load((REPO / "config" / "domain.yaml").read_text()))
    assert parsed == DEFENSE_DEFAULTS
    assert domain_hash(parsed) == domain_hash(DEFENSE_DEFAULTS)


def _valid_raw() -> dict:
    return yaml.safe_load((REPO / "config" / "domain.yaml").read_text())


def test_parse_rejects_unknown_keys() -> None:
    raw = _valid_raw()
    raw["taglien"] = "typo"
    with pytest.raises(DomainError, match="unknown key"):
        parse_domain(raw)


def test_parse_rejects_unknown_fragments() -> None:
    raw = _valid_raw()
    raw["prompt_fragments"]["mystery_slot"] = "text"
    with pytest.raises(DomainError, match="unknown prompt fragment"):
        parse_domain(raw)


def test_parse_rejects_a_bad_slug() -> None:
    raw = _valid_raw()
    raw["slug"] = "Defense Industry!"
    with pytest.raises(DomainError, match="slug"):
        parse_domain(raw)


def test_parse_rejects_a_missing_fragment() -> None:
    raw = _valid_raw()
    del raw["prompt_fragments"]["anchor_relations"]
    with pytest.raises(DomainError, match="missing prompt fragment"):
        parse_domain(raw)


def test_parse_rejects_fragment_with_leading_blank_line() -> None:
    raw = _valid_raw()
    raw["prompt_fragments"]["anchor_directness"] = (
        "\n" + raw["prompt_fragments"]["anchor_directness"]
    )
    with pytest.raises(DomainError, match="blank line"):
        parse_domain(raw)
