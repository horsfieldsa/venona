"""The definitions review queue: one SURFACE at a time, with a suggestion.

Converged on the merge queue's model, because it is the same job and the merge
queue does it better. What that queue gets right, and this one did not:

  * **the unit of work is a decision, not a record.** `alias_conflicts` yields
    one pair; the curator answers it and it leaves. The definitions list showed
    one row per EXPANSION, so `FCC` appeared twice and `CPE` five times, and
    "how much is left" was a row count of 604 rather than a question count.
    A surface is the thing you decide about; its readings are what you decide
    between.
  * **it proposes.** The adjudicator's `suggestion` fills the matching button,
    so the common case is one keystroke and the rare case still costs nothing.
    Every signal needed to do the same here already exists -- corroboration
    counts, the corpus check, the shape of the readings -- and none of it
    reached the screen.
  * **it is finite.** A decided pair is dropped. Here `decided_at` existed and
    the list still showed the row, so a reviewed surface looked exactly like
    an untouched one.

Nothing here writes. It reads the state and ranks it; the verdicts stay on
`/glossary/{surface}/…` where the resource is, and the hover card's "look this
up" keeps working because nothing moved.
"""

from __future__ import annotations

import re
from typing import Any

from venona.db import core as db
from venona.db.repositories import definition_queue as repo
from venona.services import corpus_check

# Words that carry no distinguishing weight when deciding whether two readings
# are the same meaning spelled differently.
_NOISE = frozenset({"of", "the", "and", "for", "a", "an", "in", "on", "at", "to", "or", "its", "s"})

# How much of the smaller reading's content must appear in the larger before
# they are called one meaning. Measured against this corpus: "Fiscal Year" /
# "Fiscal Years" / "In fiscal year" all sit at 1.0, while "Capability Program
# Executive" / "customer premise equipment" sit at 0.
SAME_MEANING_OVERLAP = 0.8


def _singular(word: str) -> str:
    """Strip a trailing plural `s`.

    Singular and plural are spelling, not meaning, which is precisely what this
    check is supposed to see through -- and without it the check misses the most
    common shape of the thing it exists for: "Cooperative Research and
    Development Agreement" against "…Agreements" scores 0.75 and reads as two
    meanings. `ss` is left alone so "access" does not become "acces".
    """
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def _content(expansion: str) -> set[str]:
    return {_singular(w) for w in re.findall(r"[a-z0-9]+", expansion.lower()) if w not in _NOISE}


def looks_like_one_meaning(expansions: list[str]) -> bool:
    """Are these spellings of a single meaning rather than distinct readings?

    Over half this corpus's ambiguity is spelling, not ambiguity: `FY` is
    "Fiscal Year", "Fiscal Years" and "In fiscal year". Calling that ambiguous
    and asking a curator to pick is asking the wrong question -- the answer is
    that they are one thing and the extra rows are capture noise.
    """
    if len(expansions) < 2:
        return False
    sets = [_content(e) for e in expansions if _content(e)]
    if len(sets) < 2:
        return False
    for i, a in enumerate(sets):
        for b in sets[i + 1 :]:
            smaller = min(len(a), len(b))
            if not smaller or len(a & b) / smaller < SAME_MEANING_OVERLAP:
                return False
    return True


# How much a reading's evidence is worth, for comparing two readings of one
# surface. The tier dominates -- a parenthetical in a document we hold beats any
# number of web pages -- and corroboration breaks ties within a tier. Deliberately
# NOT `confidence`: that is a display number whose scale differs per tier, so
# comparing 1.0 (in_article) with 0.85 (a web reading agreed with three times)
# would call them close when they are not the same kind of claim at all.
_METHOD_WEIGHT = {"manual": 6, "in_article": 4, "in_corpus": 3, "web_grounded": 0}


#: The ranked queue per database, guarded by the corpus-and-definitions stamp.
#: Keyed on `conn.dsn` and size-capped for the reason `corpus_check._AUDIT_CACHE`
#: records: the suite opens hundreds of throwaway databases per process and two
#: of them can carry an identical stamp.
_QUEUE_CACHE: dict[str, tuple[tuple, tuple[list[dict[str, Any]], int]]] = {}
_QUEUE_CACHE_MAX = 8


def _evidence_rank(reading: dict[str, Any]) -> int:
    return _METHOD_WEIGHT.get(reading["method"], 0) + min(reading["corroboration_count"] - 1, 2)


def _suggest(item: dict[str, Any]) -> dict[str, Any] | None:
    """What this surface probably needs, and why.

    A SUGGESTION. It fills a button; it never presses one. The merge queue's
    works the same way and for the same reason: the curator is the one who
    knows, and a proposal that acts on its own is just an unreviewed decision.

    Ordered by how much evidence stands behind it, strongest first.
    """
    readings = item["readings"]
    expansions = [r["expansion"] for r in readings]

    if item["contradiction"]:
        head = item["contradiction"]["always_followed_by"]
        return {
            "verdict": "attach_entity",
            "why": f"every use of {item['surface']} here is followed by “{head}” — it is a name",
        }
    if len(readings) > 1 and looks_like_one_meaning(expansions):
        return {
            "verdict": "same_thing",
            "why": "these readings are one meaning spelled several ways",
        }
    if len(readings) > 1:
        # One reading standing on much better evidence than the rest is not an
        # ambiguity, it is a winner. `VENOM` is the case: a tier-1 parenthetical
        # from a document we hold ("Viper Experimentation and Next-generation
        # Operations Model", 1.0) against a web page about a 2015 security
        # vulnerability (0.55). Proposing "keep both" there would ask the
        # curator to bless a reading the evidence already outranks.
        ranked = sorted(readings, key=lambda r: -_evidence_rank(r))
        best, runner_up = ranked[0], ranked[1]
        if _evidence_rank(best) - _evidence_rank(runner_up) >= 2:
            return {
                "verdict": "choose",
                "entry_id": best["id"],
                "why": (
                    f"“{best['expansion']}” is grounded "
                    f"{best['method'].replace('_', ' ')}; the others are weaker"
                ),
            }
        return {
            "verdict": "keep_both",
            "why": f"{len(readings)} readings on comparable evidence",
        }
    if len(readings) == 1:
        reading = readings[0]
        if reading["corroboration_count"] > 1 or reading["method"] in ("in_article", "in_corpus"):
            return {
                "verdict": "accept",
                "why": (
                    f"{reading['corroboration_count']} source(s), grounded "
                    f"{reading['method'].replace('_', ' ')}"
                ),
            }
        return {"verdict": "accept", "why": "one grounded reading, from a single page"}
    if not item["attempted"]:
        return {"verdict": "lookup", "why": "nothing grounded and never looked up"}
    return None


def build(conn: db.Conn, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """Surfaces awaiting a decision, most consequential first.

    `decided_at IS NULL` on every reading is what "awaiting" means. A surface a
    curator kept, collapsed, picked or rejected is finished and does not come
    back -- which is the whole difference between a queue and a list.

    Ordering is by what a decision is WORTH, not by how often the letters
    appear: a definition the corpus contradicts is wrong right now, a surface
    with several readings is unusable until it is settled, and only then does
    frequency break the tie. Sorting by sightings alone is what buried the
    twenty-five decisions worth making under five hundred that were not.

    `offset` windows the RANKED list, which is why the rank ends on
    `normalized`: with ties resolved by nothing, two requests could disagree
    about which rows page two holds.

    The ranked list is memoised behind the same stamp the corpus audit uses, so
    paging through the queue ranks it ONCE rather than re-reading and re-sorting
    every definition for each page. Its inputs are exactly what that stamp
    covers: the corpus, the definitions, and the verdicts on them.
    """
    fingerprint = corpus_check.audit_fingerprint(conn)
    cached = _QUEUE_CACHE.get(conn.dsn)
    if cached is not None and cached[0] == fingerprint:
        pending, decided = cached[1]
        return {
            "items": pending[offset : offset + limit],
            "total_pending": len(pending),
            "decided": decided,
        }

    rows = repo.select_acronym_definitions(conn).fetchall()

    contradictions = {
        found.surface: found for found in corpus_check.audit(conn) if found.contradicted
    }

    surfaces: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = surfaces.setdefault(
            row["normalized"],
            {
                "surface": row["surface"],
                "normalized": row["normalized"],
                "sightings": row["sighting_count"],
                "articles": row["article_count"],
                "attempted": bool(row["last_attempt_at"]),
                "attempt_count": row["attempt_count"] or 0,
                "decided": False,
                "readings": [],
                "contradiction": None,
            },
        )
        # A surface is decided when ANY of its rows carries a verdict: every
        # verdict stamps all of them, and a half-stamped surface would mean a
        # decision was interrupted rather than that one reading is pending.
        if row["decided_at"]:
            item["decided"] = True
        item["sightings"] = max(item["sightings"], row["sighting_count"])
        item["attempted"] = item["attempted"] or bool(row["last_attempt_at"])
        if row["expansion"]:
            item["readings"].append(
                {
                    "id": row["id"],
                    "expansion": row["expansion"],
                    "method": row["method"],
                    "confidence": row["confidence"],
                    "corroboration_count": row["corroboration_count"] or 1,
                    "status": row["status"],
                    "grounding": {
                        "quote": row["grounding_quote"],
                        "url": row["grounding_url"],
                        "article_id": row["grounding_article_id"],
                    },
                }
            )
            found = contradictions.get(row["surface"])
            if found is not None and found.expansion == row["expansion"]:
                item["contradiction"] = {
                    "expansion": found.expansion,
                    "always_followed_by": found.name_head,
                    "documents": found.documents,
                }

    pending = [i for i in surfaces.values() if not i["decided"]]
    for item in pending:
        # Strongest evidence first, so "fold the others into this one" and the
        # digit keys both mean something without the curator sorting by eye.
        item["readings"].sort(key=lambda r: (-_evidence_rank(r), r["expansion"]))
        item["suggestion"] = _suggest(item)

    def rank(item: dict[str, Any]) -> tuple[int, int, str]:
        if item["contradiction"]:
            tier = 0  # wrong right now
        elif len(item["readings"]) > 1:
            tier = 1  # unusable until settled
        elif item["readings"]:
            tier = 2  # one reading, wants a nod
        else:
            tier = 3  # undefined backlog
        return (tier, -item["sightings"], item["normalized"])

    pending.sort(key=rank)
    decided = sum(1 for i in surfaces.values() if i["decided"])

    if conn.dsn not in _QUEUE_CACHE and len(_QUEUE_CACHE) >= _QUEUE_CACHE_MAX:
        _QUEUE_CACHE.pop(next(iter(_QUEUE_CACHE)))
    _QUEUE_CACHE[conn.dsn] = (fingerprint, (pending, decided))

    return {
        "items": pending[offset : offset + limit],
        "total_pending": len(pending),
        "decided": decided,
    }
