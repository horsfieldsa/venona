"""Cards derived from the corpus at read time (epic O, docs/adr/0157).

Nothing here is stored. A card key names a source row -- an entity
definition, an unresolved gloss, a curated assertion, an approved generated
question -- and `derive` renders it or returns None, so "card exists <=>
sourced fact exists" is structural: a rejected definition, a merged-away
entity or a retired question simply stops deriving, and the learner's FSRS
row for it goes dormant.

Determinism: format rotation, distractor picks and choice order are all
hashes over `(card_key, reps)`. The same showing is the same card, every
time, on every machine -- which is also what makes any of this testable
offline.
"""

from __future__ import annotations

import hashlib
import re

from venona.api.schemas.entity import Card, Grounding
from venona.api.schemas.learning import DeckCard
from venona.db import core as db
from venona.db.repositories import learning as repo
from venona.services import entity_service

BLANK = "____"
#: How many distractors a choice card wants; fewer than 2 and the format is
#: ineligible -- a two-option "multiple" choice is a coin flip.
DISTRACTORS = 3
_POOL = 24  # candidates fetched before the deterministic pick

NAMESPACES = ("def", "gloss", "rel", "q")


def new_candidates(conn: db.Conn, *, user_id: str, limit: int) -> list[str]:
    """Card keys the user has never graded, interleaved round-robin across
    namespaces so a corpus rich in curated relations does not flood the first
    week with nothing else. Within a namespace the repository's ranking holds
    (centrality for entities, sighting weight for glosses)."""
    lanes = [
        [r["card_key"] for r in repo.new_def_candidates(conn, user_id=user_id, limit=limit)],
        [r["card_key"] for r in repo.new_gloss_candidates(conn, user_id=user_id, limit=limit)],
        [r["card_key"] for r in repo.new_relation_candidates(conn, user_id=user_id, limit=limit)],
        [r["card_key"] for r in repo.new_question_candidates(conn, user_id=user_id, limit=limit)],
    ]
    out: list[str] = []
    i = 0
    while len(out) < limit and any(lanes):
        lane = lanes[i % len(lanes)]
        if lane:
            out.append(lane.pop(0))
        i += 1
        if i > limit * len(lanes) + len(NAMESPACES):
            break
    return out


def derive(conn: db.Conn, *, card_key: str, reps: int = 0) -> DeckCard | None:
    """The card behind a key, format chosen for this showing, or None when
    the underlying fact is not servable."""
    parts = card_key.split(":", 2)
    if len(parts) != 3 or parts[0] != "card" or parts[1] not in NAMESPACES:
        return None
    namespace, source_id = parts[1], parts[2]
    if namespace == "def":
        return _def_card(conn, card_key, source_id, reps)
    if namespace == "gloss":
        return _gloss_card(conn, card_key, source_id, reps)
    if namespace == "rel":
        return _rel_card(conn, card_key, source_id, reps)
    return _question_card(conn, card_key, source_id)


# ── per-namespace derivation ─────────────────────────────────────────────


def _def_card(conn: db.Conn, card_key: str, entity_id: str, reps: int) -> DeckCard | None:
    card = entity_service.entity_card(conn, entity_id)
    if card is None or card.status != "active" or not card.definition:
        return None
    if card.id != entity_id:
        # The key names a merged-away id. `entity_card` resolved the survivor,
        # whose own key is the servable one -- serving it here too would put
        # one fact on two schedules.
        return None
    if repo.def_status(conn, entity_id=card.id) != "active":
        # The Card still carries the definition text, but a rejected or
        # undefined DEFINITION ROW is not deck material -- the entity's own
        # status cannot say so, which is why this is asked separately.
        return None
    if _is_reported_speech(card.definition):
        # A "definition" that is a verbatim quotation describes nothing: it
        # is somebody speaking. Served as a choice card it becomes
        # who-said-this trivia under a "what does this describe" cue, with
        # every name in the quote a red herring. Not deck material in any
        # format.
        return None

    surface = _surface_in_quote(card)
    eligible = ["flashcard"]
    distractors = _pick(
        repo.distractor_names(conn, entity_type=card.type or "", exclude_id=entity_id, limit=_POOL),
        n=DISTRACTORS,
        seed=f"{card_key}:{reps}",
    )
    if len(distractors) >= 2 and card.definition:
        eligible.append("choice")
    if surface:
        eligible.append("cloze")
    format = _rotate(eligible, card_key, reps)

    name = card.name or entity_id
    if format == "choice":
        return DeckCard(
            card_key=card_key,
            namespace="def",
            format="choice",
            front=_blank_answer(card.definition, [name, card.expansion, *(card.aliases or [])]),
            choices=_order([name, *distractors], f"{card_key}:{reps}"),
            answer=name,
            card=card,
            entity_id=card.id,
        )
    if format == "cloze" and surface and card.grounding and card.grounding.quote:
        return DeckCard(
            card_key=card_key,
            namespace="def",
            format="cloze",
            front=_blank(card.grounding.quote, surface),
            answer=surface,
            card=card,
            entity_id=card.id,
        )
    return DeckCard(
        card_key=card_key,
        namespace="def",
        format="flashcard",
        front=name,
        card=card,
        entity_id=card.id,
    )


def _gloss_card(conn: db.Conn, card_key: str, gloss_id: str, reps: int) -> DeckCard | None:
    # `gloss_id` is the row's full id -- gloss ids already carry their
    # `gloss:` prefix, so the key reads `card:gloss:gloss:<slug>`.
    full_id = gloss_id
    card = entity_service.glossary_card(conn, full_id)
    if card is None or card.kind != "glossary" or not card.definition:
        return None

    eligible = ["flashcard"]
    distractors = _pick(
        repo.gloss_distractor_expansions(conn, exclude_id=full_id, limit=_POOL),
        n=DISTRACTORS,
        seed=f"{card_key}:{reps}",
    )
    if len(distractors) >= 2:
        eligible.append("choice")
    format = _rotate(eligible, card_key, reps)

    surface = card.surface or gloss_id
    if format == "choice":
        # The surface blanked out of every option, the answer included: an
        # expansion that writes "the ATO (air tasking order)" names the very
        # acronym being asked. The answer field carries the SAME blanked
        # text, because the client matches the selected option verbatim.
        answer = _blank(card.definition, surface)
        return DeckCard(
            card_key=card_key,
            namespace="gloss",
            format="choice",
            front=surface,
            choices=_order(
                [answer, *(_blank(d, surface) for d in distractors)], f"{card_key}:{reps}"
            ),
            answer=answer,
            card=card,
        )
    return DeckCard(
        card_key=card_key, namespace="gloss", format="flashcard", front=surface, card=card
    )


def _rel_card(conn: db.Conn, card_key: str, assertion_id: str, reps: int) -> DeckCard | None:
    row = repo.relation_source(conn, assertion_id=assertion_id)
    if row is None:
        return None

    from venona.ontology import get_ontology

    ontology = get_ontology()
    # Distractor predicates must be type-compatible with the pair, or the
    # right answer is whichever option is not nonsense.
    compatible = [
        p
        for p in ontology.predicate_names  # a cached property, not a method
        if p != row["predicate"]
        and ontology.check_relation(p, row["subject_type"], row["object_type"]) is None
    ]
    distractors = _pick(sorted(compatible), n=DISTRACTORS, seed=f"{card_key}:{reps}")
    grounding = Grounding(quote=row["evidence_quote"]) if row["evidence_quote"] else None
    front = f"{row['subject_name']} · {row['object_name']}"

    if len(distractors) >= 2:
        return DeckCard(
            card_key=card_key,
            namespace="rel",
            format="choice",
            front=front,
            choices=_order([row["predicate"], *distractors], f"{card_key}:{reps}"),
            answer=row["predicate"],
            grounding=grounding,
            entity_id=row["subject_id"],
        )
    return DeckCard(
        card_key=card_key,
        namespace="rel",
        format="flashcard",
        front=front,
        answer=row["predicate"],
        grounding=grounding,
        entity_id=row["subject_id"],
    )


def _question_card(conn: db.Conn, card_key: str, question_id: str) -> DeckCard | None:
    # Like glosses, `question_id` is the row's full `question:`-prefixed id.
    row = repo.question_source(conn, question_id=question_id)
    if row is None:
        return None
    grounding = Grounding(quote=row["grounding_quote"], article_id=row["grounding_article_id"])
    card: Card | None = None
    if row["entity_id"]:
        card = entity_service.entity_card(conn, row["entity_id"])
    # The generator is TOLD not to restate the answer in the prompt, and this
    # does not rely on it listening: the answer is blanked out of the prompt
    # at derive time, which also repairs every already-approved row.
    front = _blank(row["prompt"], row["answer"]) if row["answer"] else row["prompt"]
    if row["kind"] == "choice":
        choices = db.jload(row["choices"], []) or []
        if row["answer"] not in choices:
            return None
        return DeckCard(
            card_key=card_key,
            namespace="q",
            format="choice",
            front=front,
            choices=list(choices),
            answer=row["answer"],
            card=card,
            grounding=grounding,
            entity_id=row["entity_id"],
        )
    # cloze prompts arrive already blanked by the generator; qa renders as a
    # flashcard. Either way the stored answer is the reveal.
    format = "cloze" if row["kind"] == "cloze" else "flashcard"
    return DeckCard(
        card_key=card_key,
        namespace="q",
        format=format,
        front=front,
        answer=row["answer"],
        card=card,
        grounding=grounding,
        entity_id=row["entity_id"],
    )


# ── deterministic choices ────────────────────────────────────────────────


def _hash_unit(seed: str) -> int:
    return int.from_bytes(hashlib.sha256(seed.encode()).digest()[:4], "big")


def _rotate(eligible: list[str], card_key: str, reps: int) -> str:
    return eligible[_hash_unit(f"{card_key}:{reps}:format") % len(eligible)]


def _pick(candidates: list[str], *, n: int, seed: str) -> list[str]:
    unique = [c for c in dict.fromkeys(candidates) if c]
    unique.sort(key=lambda c: _hash_unit(f"{seed}:{c}"))
    return unique[:n]


def _order(choices: list[str], seed: str) -> list[str]:
    return sorted(dict.fromkeys(choices), key=lambda c: _hash_unit(f"{seed}:order:{c}"))


def _surface_in_quote(card: Card) -> str | None:
    """The longest of the entity's surfaces that actually appears in its
    grounding quote, word-bounded -- the precondition for a cloze that blanks
    something real."""
    if not card.grounding or not card.grounding.quote:
        return None
    quote = card.grounding.quote
    surfaces = [s for s in [card.name, *(card.aliases or [])] if s]
    for surface in sorted(set(surfaces), key=len, reverse=True):
        if re.search(rf"(?<!\w){re.escape(surface)}(?!\w)", quote, re.IGNORECASE):
            return surface
    return None


def _blank(quote: str, surface: str) -> str:
    return re.sub(rf"(?<!\w){re.escape(surface)}(?!\w)", BLANK, quote, flags=re.IGNORECASE)


def _is_reported_speech(definition: str | None) -> bool:
    """A definition that OPENS with a quotation mark, or that carries a
    quotation plus a speech attribution, is an article sentence about
    someone talking -- not a description of anything. Embedded scare
    quotes (`the "backbone" constellation`) stay eligible."""
    if not definition:
        return False
    text = definition.strip()
    if text[:1] in ('"', "“"):
        return True
    has_quote = any(mark in text for mark in ('"', "“", "”"))
    return has_quote and " said " in f" {text} "


def _blank_answer(text: str, surfaces: list[str]) -> str:
    """Every surface the answer goes by, blanked out of a prompt. A
    definition NAMES its entity ("Shield AI is a defense contractor…"),
    so serving it unmasked as a choice card hands the answer over."""
    for surface in sorted({s for s in surfaces if s}, key=len, reverse=True):
        text = _blank(text, surface)
    return text
