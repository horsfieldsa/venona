"""How sure we are, and why the two kinds of definition disagree.

The numbers live here so the next code path writing one is a lookup rather than
a guess -- `run_launcher.py` records this drift biting once already, two paths
writing the same kind of row with `confidence=0.7` and `0.5`.

WHY ACRONYMS AND ENTITIES DO NOT SHARE A SCALE. They are not the same claim:

    acronym web_grounded   0.55 base, +0.10 per corroborating source, cap 0.85
    entity  web_grounded   0.50 flat

An acronym's tier-3 gate has FOUR checks, one of them arithmetic that cannot be
wrong about itself: the article id exists, the quote appears verbatim in the page
we fetched, the acronym appears in the quote, and the initials of the expansion
generate the acronym. An entity's gate has TWO -- the quote appears in the page,
the entity name appears in the quote -- because a name generates nothing.

Same asymmetry at tier 2 (`in_corpus`): an acronym's is a verified parenthetical
expansion, 0.9; an entity's is a definition the model WROTE from sentences it was
shown, 0.7.

Raising either to match would be a data change with nothing behind it.
"""

from __future__ import annotations

# The evidence ladder for entity definitions. A re-grounding may only move a row
# UP it: `store_entity_definition` refuses to let a `web_grounded` lookup
# overwrite an `in_article` sentence, which is what a hover card's "look this up"
# button would otherwise do to the best definition we had.
#
# Deliberately a SEPARATE table from `acronyms.TIER_RANK` rather than an import,
# even though the values coincide: they answer the same question about two
# different row types, and coupling them means a change to one silently changes
# the other. `tests/test_define.py` pins that they agree while they agree.
ENTITY_TIER_RANK: dict[str, int] = {
    "manual": 4,
    "in_article": 3,
    "in_corpus": 2,
    "web_grounded": 1,
    "undefined": 0,
}

# What each tier is worth for an ENTITY. See the module docstring for why these
# are not the acronym numbers.
ENTITY_CONFIDENCE: dict[str, float] = {
    "in_article": 1.0,
    "in_corpus": 0.7,
    "web_grounded": 0.5,
}
