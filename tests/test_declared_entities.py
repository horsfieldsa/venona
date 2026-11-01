"""`fixtures/seed/` and `profile/anchors.yaml` must not declare one entity twice.

WHAT WENT WRONG. `fixtures/seed/entities.yaml` declared `company:booz-allen` and
`profile/anchors.yaml` declared `company:booz-allen-hamilton` — same name, same
`org_type`, same relevance, byte-identical alias set, two ids. Which one an
annotation resolved to depended on load order, so `make reset` (seed, then
anchors) and any other order produced different graphs from the same inputs. The
symptom is not an error: it is two half-populated entities, each with some of the
mentions, and a hover card that is missing evidence nobody can find.

**Booz Allen was one of fifteen.** The sweep that found it also found
`unit:us-army` vs `unit:u-s-army` — the most-referenced entity in the corpus —
plus DARPA, DIU, IVAS, JADC2, MOSA, RAND, SDA, JHU/APL, Maven, RTX and four more
service branches. Every collision was seed-vs-anchors with one id on each side, so
they were resolved by making anchors canonical: it holds 264 entities against the
fixture's 40 and its edge lists are keyed by those ids.

WHY NOT ASSERT `id == mint_id(type, name)`. That was the first idea and it is
wrong. 63 of the 304 declared ids differ from what `mint_id` produces, and
deliberately: `govt:darpa` is a better key than
`govt:defense-advanced-research-projects-agency`, and a hand-authored id is a
stable human choice, not a derived one. `mint_id` exists for surfaces a MODEL
returned, where nobody is available to choose. Invariant 2 is about who assigns
ids, not about them all being generated.

So what is checked is the thing that is actually forbidden: two ids for one
entity. Names first, then alias surfaces — an alias collision is the same bug one
step removed, because the alias index is what an annotation resolves through.
"""

from __future__ import annotations

import collections
from pathlib import Path
from typing import Any

import pytest
import yaml
from venona.db.models import normalize

ROOT = Path(__file__).resolve().parents[1]
SOURCES = (ROOT / "fixtures/seed/entities.yaml", ROOT / "profile/anchors.yaml")

#: Normalised names that legitimately belong to more than one entity, each with
#: the reason. Same shape as `test_route_contracts.py::EXEMPT`: an exemption with
#: a written reason is reviewable, a check that quietly permits a whole class is
#: not.
#:
#: A REAL COLLISION IS STILL RECORDED HERE, not resolved. `profile/anchors.yaml`
#: declares `prog:maven-smart-system` (Program, aliases `Project Maven`, `MSS`)
#: and `plat:maven-smart-system-platform` (Platform), both named "Maven Smart
#: System". A Program and the Platform it delivers are genuinely two things and the
#: ontology distinguishes them, so this is not the booz-allen bug — but the shared
#: NAME does mean the literal text "Maven Smart System" in an article has two
#: candidates, and something has to win.
#:
#: Left as the operator has it, because the fix is a domain call about their own
#: profile rather than a code change: in the trade press "Maven Smart System"
#: (MSS) is the platform and "Project Maven" is the programme, so renaming the
#: Program entity to `Project Maven` would remove the ambiguity — and only its
#: owner should decide that.
ALLOWED_SHARED_NAMES: dict[str, str] = {
    "maven smart system": (
        "a Program and the Platform it delivers, both so named in "
        "profile/anchors.yaml; see the note above"
    ),
}


def _declared() -> list[tuple[Path, dict[str, Any]]]:
    out: list[tuple[Path, dict[str, Any]]] = []
    for path in SOURCES:
        doc = yaml.safe_load(path.read_text()) or {}
        for entity in doc.get("entities") or []:
            if entity.get("id"):
                out.append((path, entity))
    return out


def _where(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


class TestNoEntityIsDeclaredTwice:
    def test_no_two_ids_share_a_name(self) -> None:
        """The booz-allen shape exactly: one company, two keys, load order wins."""
        by_name: dict[str, dict[str, set[str]]] = collections.defaultdict(
            lambda: collections.defaultdict(set)
        )
        for path, entity in _declared():
            by_name[normalize(entity["name"])][entity["id"]].add(_where(path))

        clashes = {
            name: {i: sorted(w) for i, w in ids.items()}
            for name, ids in by_name.items()
            if len(ids) > 1 and name not in ALLOWED_SHARED_NAMES
        }
        assert not clashes, (
            "one entity declared under several ids -- whichever loads last wins an "
            f"annotation, so `make reset` and any other order disagree:\n{clashes}"
        )

    def test_no_two_ids_share_an_alias_surface(self) -> None:
        """The same bug through the alias index, which is what resolution reads.

        Two entities offering `Army` is not a near-miss the resolver adjudicates:
        Aho-Corasick finds the surface and something has to win, so the mention
        lands on one of them for reasons nobody can see from the brief.
        """
        by_alias: dict[str, dict[str, set[str]]] = collections.defaultdict(
            lambda: collections.defaultdict(set)
        )
        for path, entity in _declared():
            for alias in entity.get("aliases") or []:
                surface = alias["surface"] if isinstance(alias, dict) else alias
                by_alias[normalize(surface)][entity["id"]].add(_where(path))

        clashes = {
            surface: {i: sorted(w) for i, w in ids.items()}
            for surface, ids in by_alias.items()
            if len(ids) > 1 and surface not in ALLOWED_SHARED_NAMES
        }
        assert not clashes, f"one alias surface claimed by several ids:\n{clashes}"

    def test_every_allowlisted_name_is_still_shared(self) -> None:
        """An exemption for a collision that no longer exists is worse than none:
        it silently permits the next one that lands on the same name."""
        by_name: dict[str, set[str]] = collections.defaultdict(set)
        for _, entity in _declared():
            by_name[normalize(entity["name"])].add(entity["id"])
        stale = [name for name in ALLOWED_SHARED_NAMES if len(by_name.get(name, set())) < 2]
        assert not stale, f"allowlisted but no longer shared -- delete the entry: {stale}"

    def test_no_id_is_declared_twice_with_a_different_shape(self) -> None:
        """Same id in both files is FINE and is how anchors extends a fixture --
        as long as the two agree on what the entity is. A type that differs is a
        silent overwrite by whichever loads second."""
        by_id: dict[str, set[tuple[str, str]]] = collections.defaultdict(set)
        for _, entity in _declared():
            by_id[entity["id"]].add((entity["name"], entity["type"]))
        conflicting = {i: sorted(v) for i, v in by_id.items() if len(v) > 1}
        assert not conflicting, f"one id declared with different shapes:\n{conflicting}"


class TestEveryEdgeEndpointExists:
    def test_seed_edges_name_declared_entities(self) -> None:
        """A retired id in an edge list is the other half of a rename, and it fails
        as an edge that silently never projects rather than as an error."""
        declared = {entity["id"] for _, entity in _declared()}
        edges_file = ROOT / "fixtures/seed/edges.yaml"
        doc = yaml.safe_load(edges_file.read_text()) or {}
        edges = doc.get("edges") or doc.get("relations") or []
        if not edges:
            pytest.skip("no seed edges declared")

        missing = sorted(
            {
                end
                for edge in edges
                for end in (
                    edge.get("src") or edge.get("source"),
                    edge.get("dst") or edge.get("target"),
                )
                if end and end not in declared
            }
        )
        assert not missing, f"seed edges point at ids nothing declares: {missing}"
