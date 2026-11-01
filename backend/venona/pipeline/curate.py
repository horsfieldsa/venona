"""Curate: the maintenance stage.

Runs last in every pipeline run. Deliberately **a stage and not a second
scheduled job**: as a stage it inherits durability, SSE, cancellation and
`succeeded_partial` for free, and earns none of the machinery a run of its own
would need.

**Propose-only except where the evidence settles a pair without a person.**
Nothing here renames or rejects an entity. What it does change:

  * `status`, via `promote_provisionals` -- a pure function of evidence that
    reverses itself if the evidence goes away.
  * `entity_mappings`, via `identical_pairs`, `declared_type_pairs` and
    `model_merges`.
  * one PART_OF-shaped assertion, via `model_relations`, and a `kept_apart`
    verdict, via `model_distinctions`.

ADRs 0174, 0208 and 0230 carry the budget and the reversal trigger for each.

Almost all of it is a query; `adjudicate` is the only part that asks the model,
and the bulk database work around it is heavy enough to need `_unblocked`.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from venona.db import core as db
from venona.db.repositories import curate as curate_repo
from venona.db.repositories import curation as curation_repo
from venona.db.repositories import pipeline_curate as repo
from venona.graph.projector import resolve_mappings
from venona.ontology import get_ontology
from venona.pipeline.metrics import Funnel
from venona.pipeline.resolve import promote_provisionals
from venona.pipeline.types import RunContext
from venona.services import alias_index
from venona.services.curate import (
    RESOLUTION_MERGED,
    MergeError,
    close_moot_conflicts,
    merge_entities,
    resolved_pair,
)
from venona.untrust import inline


async def _unblocked[T](work: Callable[[], T]) -> T:
    """Run one synchronous chunk of this stage off the event loop.

    A run executes as a task INSIDE the API process, so anything this stage
    does without awaiting is time uvicorn cannot answer `/api/health/live`.
    Observed 2026-09-13: a Name review's last model verdict landed at 20:49:54
    and the stage reported at 20:51:11, with apscheduler logging a job `missed
    by 0:00:37` in between. The host read the silence as a dead instance and
    restarted it, so the site went down for ninety seconds as the run finished.
    Nothing here is slow by accident -- `_classify_open` walks the whole open
    queue with a query per row, `identical_pairs` runs twice, `entity_collisions`
    reads every alias -- and all of it grows with the corpus.

    Per SECTION rather than once around the stage: a thread is not interruptible,
    so a forced cancel (ADR 0231) waits out whatever chunk is in flight. Safe
    because the run owns `ctx.db` and this coroutine is suspended for as long as
    the thread holds it.
    """
    return await asyncio.to_thread(work)


# What an unresolved alias conflict actually is. They need different actions,
# so a queue that cannot tell them apart makes a human re-diagnose every row.
DUPLICATE = "duplicate"
COLLISION = "collision"
RESOLVER_ERROR = "resolver_error"
CONTAINMENT = "containment"
# Not a resolver finding at all: `pipeline/anchors.py` files these to attach an
# unconnected entity to the spine, and it shares the queue, so it has to survive
# the classifier.
ANCHOR = "anchor"
# One name held by two entities of DIFFERENT types -- a Program and the Platform
# it delivers. Never mergeable: the ontology draws that line on purpose.
CROSS_TYPE = "cross_type"
# One name across a boundary the ontology declares `confusable_with`, which is
# the opposite answer: `GE F414` read as a Platform and as a Technology is one
# engine. Nothing in the two names separates that from `Havoc` the company and
# `Havoc` the missile, so the ontology rules on its own boundaries and the
# curator is asked about the rest. ADR 0176.
TYPE_CONFLICT = "type_conflict"

# Above this, two names that both generate the same acronym are one thing spelled
# two ways rather than two things sharing an acronym.
#
# Fitted 2026-08-06 to 21 live rows, so treat it as a triage hint and not an
# oracle. Raw names put the gap in the wrong place: `Next Generation Command and
# Control` against `Next-Generation Command and Control (NGC2)` scores 77.9 on
# `fuzz.token_sort_ratio`, and a hyphen plus a trailing gloss filed the same
# program against itself, twice (NGC2 and NGCC). Scoring `_comparable()` forms
# moves that pair to 100 and opens a gap either side of 80: every true duplicate
# at or above 82, every true collision below 72.
#
# `25th Infantry Divisions` / `25th ID` (40.0) stays misfiled. No threshold
# fixes it -- it scores below every genuine collision -- and that is the honest
# limit of name similarity as a signal.
NAME_VARIANT_SCORE = 80

# A trailing "(NGC2)" is the acronym this conflict is ABOUT, so leaving it in
# the string compares each name against the other's evidence rather than
# against the other's name.
#
# Two shapes, because an all-caps rule cannot see `(PrSM)`, `(DaaS)` or
# `(UxSAI)`, and a defence acronym carries lowercase often. `Precision Strike
# Missile (PrSM)` kept its gloss, took the merge key `precision strike missile
# prsm`, and so never met the declared `Precision Strike Missile`: invisible to
# the duplicate detector, to the merge rule and to `scripts/graph_health.py`,
# all three of which reach this one function.
#
# The second shape is deliberately the narrower one -- a SINGLE token carrying
# at least two capitals -- because a wrong strip makes two different names
# compare equal, which is the direction that costs a false merge. `(Israel)`
# fails on the capitals and `(Block II)` on the space.
_TRAILING_GLOSS = re.compile(
    r"\s*\(("
    r"[A-Z0-9][A-Z0-9\s&/-]{1,20}"
    r"|(?=[^\sA-Z]*[A-Z][^\sA-Z]*[A-Z])[A-Za-z0-9][A-Za-z0-9&/-]{1,20}"
    r")\)\s*$"
)


#: A possessive is a sentence fragment that reached the resolver, not a name.
#: `Anduril's` became an entity and then, being the longer string, won a merge
#: against the company it belongs to.
#:
#: Anchored to the END, and that is the whole distinction. A TRAILING
#: possessive is the same name wearing an apostrophe, so `_comparable` drops it
#: and `RAND’s` compares equal to `RAND`. A LEADING one belongs to a different
#: entity -- `Firefly’s Alpha` is Alpha -- and `_PREFIX` is what removes that.
_POSSESSIVE = re.compile(r"[’']s$", re.IGNORECASE)


def _comparable(name: str) -> str:
    """The form two names should be compared in.

    Hyphenation, a parenthetical gloss and a possessive are spelling, not
    identity: `Next-Generation Command and Control (NGC2)` and `Next Generation
    Command and Control` name one program, and normalising all three away before
    scoring is what lets a single threshold separate spelling from meaning.
    """
    without_gloss = _TRAILING_GLOSS.sub("", name)
    return re.sub(r"[\s\-]+", " ", _POSSESSIVE.sub("", without_gloss)).strip()


#: A single initial: one letter, optionally followed by a stop. Dropped from a
#: person's name before comparison, because it disambiguates only against a
#: namesake -- and a namesake differs in the surname or the given name too, both
#: of which this rule still requires to match entirely.
_INITIAL = re.compile(r"^[A-Za-z]\.?$")

#: A quoted nickname, `Kevin “Stoney” Stonerook`. Bounded by whitespace so an
#: apostrophe inside a surname is never read as a quote.
_NICKNAME = re.compile(r"(?:(?<=\s)|^)[“\"‘][^“”\"‘’]+[”\"’](?=\s|$)")


def _person_core(name: str, titles: set[str], suffixes: set[str]) -> tuple[str, ...]:
    """A person's name with what is not their name taken off.

    Ranks and honorifics lead, post-nominals trail. STRIPPED rather than
    compared: `Lt. Gen.` and `Gen.` are one officer written twice, and a
    promotion changes the rank, so a rule that required them to agree would keep
    that pair open forever. Titles match GREEDILY and multi-word first, so
    `lt gen` is one rank rather than leaving `gen` to be read as a given name.
    """
    cleaned = _NICKNAME.sub(" ", name).replace(",", " ").replace(".", ". ")
    tokens = [t for t in re.split(r"[\s]+", cleaned) if t]
    bare = [t.rstrip(".").lower() for t in tokens]

    start = 0
    while start < len(bare):
        # Longest run first: `vice adm` before `adm`, `lt gen` before `lt`.
        for span in (3, 2, 1):
            if start + span <= len(bare) and " ".join(bare[start : start + span]) in titles:
                start += span
                break
        else:
            break

    end = len(bare)
    while end > start and bare[end - 1] in suffixes:
        end -= 1

    return tuple(t for t in bare[start:end] if not _INITIAL.match(t))


def _person_variant(
    a: str, b: str, *, titles: set[str], suffixes: set[str]
) -> tuple[str, str] | None:
    """`(survivor_name, why)` when two PEOPLE's names differ only by what is not
    a name, else None.

    Shaped like `_name_variant` so it is one more branch in the single pass that
    decides identity, rather than a second merger with its own conventions.

    Derivational, not fuzzy, which is the basis the auto-merge ban was scoped
    on: every token removed appears on a list a person wrote in
    `profile/lexicon.yaml`, which carries the reasoning, and what is left has to
    match ENTIRELY.

    Two tokens minimum. One remaining token is a surname or a mononym, and
    merging on that is the similarity judgement this refuses to make.
    """
    a_core = _person_core(a, titles, suffixes)
    if len(a_core) < 2 or a_core != _person_core(b, titles, suffixes):
        return None
    # A bare name beats one carrying an office: an office expires and a surname
    # does not. Where neither or both carry one, the fuller spelling survives --
    # it is the one a reader can match against an official listing.
    a_titled = _person_core(a, set(), suffixes) != a_core
    b_titled = _person_core(b, set(), suffixes) != _person_core(b, titles, suffixes) or (
        _person_core(b, set(), suffixes) != a_core
    )
    if a_titled != b_titled:
        return (b, "person form") if a_titled else (a, "person form")
    return (a, "person form") if len(a) >= len(b) else (b, "person form")


# A bare letter-plus-digit acronym is the one compression shape that too many
# unrelated phrases produce. `acronym_variants` compresses repeated initials,
# so "Common Controller" yields `C2` exactly as "Command and Control" does --
# and `C2` is a real Program in its own right. Longer compressions (`NGC2`,
# `C4ISR`) carry enough of the phrase with them to stay unambiguous.
_AMBIGUOUS_COMPRESSION = re.compile(r"^[A-Z]\d$")


def _containment(a: str, b: str) -> tuple[str, str, set[str]] | None:
    """`(shorter, longer, extra tokens)` when one name's tokens strictly contain
    the other's, else None.

    Shares `_tokens` with the resolver's own containment guard rather than
    splitting the word rule in two: the two disagreeing about what a token is
    would be worse than either being wrong.
    """
    from venona.pipeline.resolve import _tokens

    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb or ta == tb:
        return None
    if ta < tb:
        return a, b, tb - ta
    if tb < ta:
        return b, a, ta - tb
    return None


def _trailing_tokens(name: str, count: int) -> set[str]:
    from venona.pipeline.resolve import _tokens

    words = [t for t in re.split(r"[^\w]+", name.lower()) if t]
    return _tokens(" ".join(words[-count:])) if count and count <= len(words) else set()


def _name_variant(
    a: str, b: str, *, corporate_form: set[str], generic_tails: set[str]
) -> tuple[str, str] | None:
    """`(survivor_name, why)` when the two names are one thing, else None.

    Two classes, both derivational rather than fuzzy, and both bounded by a
    token list a human wrote in `profile/lexicon.yaml`:

      corporate form  the extra tokens are all company FORM -- `Palantir` and
                      `Palantir Technologies`. The FULLER name survives: it is
                      the one a reader recognises and, in every live case, the
                      declared entity carrying the edges.
      generic tail    the extra tokens are all generic head nouns AND they
                      trail the longer name -- `Maven Smart System platform`.
                      The name WITHOUT the tail survives, by the same rule that
                      prefers the un-glossed form.

    The trailing requirement keeps `Air Force Test Pilot School` out of the
    second class: an extra token buried mid name is qualifying the name, not
    decorating its end.
    """
    contained = _containment(_comparable(a), _comparable(b))
    if contained is None:
        return None
    shorter, longer, extra = contained
    # Map back to the caller's spelling: `_containment` compared `_comparable`
    # forms, and the survivor has to be the name as the entity actually holds it.
    full, brief = (a, b) if _comparable(a) == longer else (b, a)

    if extra <= corporate_form:
        return full, "corporate form"
    if extra <= generic_tails and extra <= _trailing_tokens(longer, len(extra)):
        return brief, "generic tail"
    return None


_INNER_GLOSS = re.compile(r"^(?P<head>[^()]*?)\s*\((?P<gloss>[^()]+)\)\s*(?P<tail>[^()]*)$")


def _gloss_form(name: str) -> str | None:
    """The name written with its own gloss in place of the words it stands for.

    `PAC-3 Missile Segment Enhancement (MSE)` states that MSE abbreviates its
    last three words, so `PAC-3 MSE` is that name spelled short -- a fact read
    off the name itself, not a similarity. None when there is no gloss, when no
    trailing run of words generates it, or when the gloss would be the whole
    result: a bare acronym is `acronym of`'s question, with its own collision
    guard.
    """
    from venona.db.models import acronym_variants

    match = _INNER_GLOSS.match(name.strip())
    if match is None:
        return None
    words = match["head"].split()
    gloss, tail = match["gloss"].strip(), match["tail"].strip()
    for count in range(1, len(words) + 1):
        if gloss.upper() not in {v.upper() for v in acronym_variants(" ".join(words[-count:]))}:
            continue
        kept = words[:-count]
        return " ".join([*kept, gloss, tail]).strip() if kept or tail else None
    return None


def _gloss_variant(
    a: str, b: str, *, corporate_form: set[str], generic_tails: set[str]
) -> tuple[str, str] | None:
    """`(survivor_name, "gloss form")` when one name is the other with a gloss
    standing in for its expansion, possibly with a lexicon tail as well.

    The expansion survives, as it does for `acronym of`, unless the match also
    needed a generic tail stripped from the glossed side: then the tail rule's
    own survivor wins, so a chain of three folds onto one name in any order.
    """
    from venona.db.models import normalize

    for glossed, other in ((a, b), (b, a)):
        short = _gloss_form(glossed)
        if short is None:
            continue
        for rival in {other, _gloss_form(other) or other}:
            # `normalize` drops a `+`, and `A330 MRTT+` is the next airframe,
            # not a spelling of `A330 MRTT`; curators kept `PILICA+` apart too.
            if short.count("+") != rival.count("+"):
                continue
            if normalize(_comparable(short)) == normalize(_comparable(rival)):
                return glossed, "gloss form"
            tailed = _name_variant(
                short, rival, corporate_form=corporate_form, generic_tails=generic_tails
            )
            if tailed is not None:
                return (glossed if tailed[0] == short else other), "gloss form"
    return None


def _gloss_has_namesake(rivals: list[db.Row], pair: set[str], a: str, b: str) -> bool:
    """True when a third entity also abbreviates to the short form, glossed or
    not -- `PAE Fires` from a Program and from a Portfolio Acquisition Executive
    is two readings of one surface, the shape `DIA` takes for `acronym of`."""
    from venona.db.models import acronym_variants, normalize

    for glossed in (a, b):
        match, short = _INNER_GLOSS.match(glossed.strip()), _gloss_form(glossed)
        if match is None or short is None:
            continue
        gloss, target = match["gloss"].strip().upper(), normalize(_comparable(short))
        context = set(target.split()) - {normalize(gloss)}
        for row in rivals:
            words = _comparable(row["name"]).split()
            if row["id"] in pair or not context <= set(normalize(" ".join(words)).split()):
                continue
            spans = ((s, e) for s in range(len(words)) for e in range(s + 1, len(words) + 1))
            for i, j in spans:
                run = " ".join(words[i:j])
                spelled = normalize(" ".join([*words[:i], gloss, *words[j:]]))
                if (
                    spelled == target
                    and run.upper() != gloss
                    and gloss in {v.upper() for v in acronym_variants(run)}
                ):
                    return True
    return False


def _mergeable_acronym(short: str, long_form: str) -> bool:
    """True when `short` is an acronym `long_form` provably generates.

    The shape test is `looks_like_acronym`, not "contains no space": `Lattice`,
    `Sentinel` and `Reaper` are all one word and none of them is an acronym, and
    the loose test absorbs a real product name into any long form whose initials
    spell it -- an auto-merge with no user approval, which CLAUDE.md reserves
    for provable identity.
    """
    from venona.db.models import acronym_variants, looks_like_acronym

    short = short.strip()
    if not looks_like_acronym(short) or _AMBIGUOUS_COMPRESSION.match(short):
        return False
    return short.upper() in {v.upper() for v in acronym_variants(long_form)}


def identical_pairs(
    conn: db.Conn,
    *,
    corporate_form: set[str] | None = None,
    generic_tails: set[str] | None = None,
    person_titles: set[str] | None = None,
    person_suffixes: set[str] | None = None,
) -> list[tuple[str, str, str]]:
    """Open duplicate conflicts whose two entities are provably ONE thing.

    Returns `(loser_id, survivor_id, why)`. The lexicons default to empty so a
    caller that has no settings in hand gets the derivational classes only,
    never a silent widening.

    `CLAUDE.md` forbids auto-merging two HIGH-SIMILARITY entities and that rule
    stands; ADR 0174 scopes what this claims instead. No class here is a
    similarity judgement: identical strings once a trailing "(ACRONYM)" gloss
    and hyphens are normalised, one whole name being an acronym the other
    generates, the two `_name_variant` classes bounded by a token list a
    person wrote, and a name's own gloss written in place of its expansion. An
    incomplete list yields a MISSED merge that lands in the queue rather than a
    wrong one, and that asymmetry is the whole design.

    Measured 2026-08-06 on the live queue: 28 of 29 open duplicates fall in the
    first two classes. The 29th -- `Maj. Gen. Partick Ellis` / `Patrick Ellis`,
    a typo -- is similarity only and stays a human decision, which is the point.

    The dangerous case never reaches here. `DIA` (Defense **Intelligence**
    Agency vs Defense **Investment** Agency) is classified `collision` by
    `_classify` precisely because BOTH names generate the acronym, so it is
    never offered as a duplicate.

    Survivor is the cleanest readable form: the expansion over the bare acronym,
    and the form without the parenthetical gloss. The loser's aliases move with
    the merge, so `DHS` still resolves and still hovers.
    """
    from venona.db.models import normalize

    corporate_form = corporate_form or set()
    generic_tails = generic_tails or set()
    person_titles = person_titles or set()
    person_suffixes = person_suffixes or set()

    pairs: list[tuple[str, str, str]] = []
    rivals: list[db.Row] | None = None
    for row in repo.select_alias_conflicts(conn):
        # Resolve BEFORE reading the names. Following the chain for the ids
        # while comparing the names behind the raw ones judges a stale row on a
        # tombstone's name -- `Army` rather than the `U.S. Army` it had become.
        # It also follows a merge applied earlier in this same pass, so a chain
        # (three CSIS entities) collapses onto one survivor.
        left, right = resolved_pair(conn, row)
        if left == right:
            continue

        rows = {r["id"]: r for r in repo.select_entities_5(conn, left, right)}
        a = rows[left]["name"] if left in rows else None
        b = rows[right]["name"] if right in rows else None
        if not a or not b:
            continue

        # A Program and the Platform it delivers share a name often enough to
        # matter -- `Maven Smart System` is both -- and the ontology draws that
        # line deliberately. `_file_entity_duplicates` files cross-type pairs and
        # `merge_entities` does not compare types, so the refusal belongs here
        # where the pair is judged.
        if rows[left]["type"] != rows[right]["type"]:
            continue

        # A declared-alias claim names its own survivor: the entity a human gave
        # the surface to. Read off the ROW, not the names -- the names say
        # subsidiary and the declaration says otherwise.
        if (row["id"] or "").startswith("claim|"):
            # `resolved_pair` returns the sides in column order, so `left` IS the
            # resolved `existing_entity_id` -- the declaring owner.
            pairs.append((right, left, "declared alias"))
            continue

        if normalize(_comparable(a)) == normalize(_comparable(b)):
            # Prefer the form without the trailing gloss; tie-break shorter.
            survivor, loser = (left, right) if len(a) <= len(b) else (right, left)
            pairs.append((loser, survivor, "same name"))
            continue

        variant = _name_variant(a, b, corporate_form=corporate_form, generic_tails=generic_tails)
        if variant is not None:
            survivor_name, why = variant
            survivor, loser = (left, right) if survivor_name == a else (right, left)
            pairs.append((loser, survivor, why))
            continue

        glossed = _gloss_variant(a, b, corporate_form=corporate_form, generic_tails=generic_tails)
        if glossed is not None:
            if rivals is None:
                rivals = repo.select_unretired_entity_names(conn)
            same_type = [r for r in rivals if r["type"] == rows[left]["type"]]
            if not _gloss_has_namesake(same_type, {left, right}, a, b):
                survivor, loser = (left, right) if glossed[0] == a else (right, left)
                pairs.append((loser, survivor, glossed[1]))
            continue

        # People, whose names carry ranks and offices that are not their names.
        # Gated on the TYPE rather than on the shape of the tokens: `chief` is a
        # rank on a person and part of the name of a thing.
        if rows[left]["type"] == "Person":
            person = _person_variant(a, b, titles=person_titles, suffixes=person_suffixes)
            if person is not None:
                survivor_name, why = person
                survivor, loser = (left, right) if survivor_name == a else (right, left)
                pairs.append((loser, survivor, why))
                continue

        a_is_acronym = _mergeable_acronym(a, b)
        b_is_acronym = _mergeable_acronym(b, a)
        if a_is_acronym and not b_is_acronym:
            pairs.append((left, right, "acronym of"))
        elif b_is_acronym and not a_is_acronym:
            pairs.append((right, left, "acronym of"))

    return pairs


#: The predicate a `variant` verdict means, where the ontology declares it.
VARIANT_PREDICATE = "VARIANT_OF"


def _relation_for(
    child_type: str | None, parent_type: str | None, *, variant: bool = False
) -> str | None:
    """The predicate that expresses "child belongs to parent", or None.

    The adjudicator answers with an ACTION, and `part_of` is plain English, not
    a predicate name: "one is part of the other" is equally the shape of
    BASED_IN (`Government of Canada` / `Canada`), WORKS_FOR and MANAGES. So the
    verdict is translated through the ontology -- PART_OF when legal, the single
    legal alternative when there is exactly one, NULL for the curator otherwise,
    which the UI renders as chips with nothing preselected. ADR 0089.

    Read per call, never bound at import: the ontology is editable over HTTP and
    a UI-triggered run executes in the API process (CLAUDE.md).
    """
    if not child_type or not parent_type:
        return None
    ontology = get_ontology()
    legal = [
        name
        for name in ontology.predicates
        if ontology.check_relation(name, child_type, parent_type) is None
    ]
    if variant:
        return VARIANT_PREDICATE if VARIANT_PREDICATE in legal else None
    # A revision of a design is never what "belongs to" means, so it is not a
    # candidate here and Platform/Platform keeps its one answer.
    legal = [name for name in legal if name != VARIANT_PREDICATE]
    if "PART_OF" in legal:
        return "PART_OF"
    return legal[0] if len(legal) == 1 else None


async def adjudicate(ctx, *, limit: int, batch: int, budget=None) -> int:
    """Ask the model what the unsettled pairs actually are.

    The queue's third verdict -- "different things, and here is how they relate"
    -- should not exist only in the UI. The pipeline sees these pairs every run
    and can do the reading.

    It cannot do it structurally, and that was measured 2026-08-06. Restricting
    to strict token supersets with both sides Organization or Program gave 98
    candidates on the live queue; hand-checking twelve found `GDIT PART_OF
    General Dynamics` correct and `U.S. Army PART_OF Army`, `Anduril Industries
    PART_OF Anduril` and `Northrop Grumman PART_OF Northrop` all wrong -- those
    are merges -- plus `Royal Canadian Air Force PART_OF Air Force`, which is
    not a relationship at all.

    So the model reads the names, with types, aliases and headlines as evidence,
    and the verdict is stored as a SUGGESTION: a wrong suggestion costs the
    curator a keystroke, a wrong assertion costs a bad edge nobody notices.
    Batched, and only for rows never looked at, so a run costs a few calls and a
    settled queue costs none.
    """
    from venona.llm.prompts import adjudicate_system
    from venona.llm.runner import call_and_record
    from venona.llm.schemas import adjudicate_schema

    conn = ctx.db
    rows = repo.select_alias_conflicts_2(conn, limit).fetchall()
    if not rows:
        return 0

    # The pair as it stands TODAY. The query above joins `entities` on the ids
    # the conflict was FILED with, so a row that outlived a merge would ask the
    # model to adjudicate `Army` when the entity has been `U.S. Army` for a week.
    # `close_moot_conflicts` has already removed the rows where BOTH sides
    # collapsed; these are the ones where only one side moved.
    resolved: list[dict] = []
    for row in rows:
        left, right = resolved_pair(conn, row)
        current = {r["id"]: r for r in repo.select_entities_5(conn, left, right)}
        if left not in current or right not in current:
            continue
        item = dict(row)
        item["existing_entity_id"], item["proposed_entity_id"] = left, right
        item["a_name"], item["a_type"] = current[left]["name"], current[left]["type"]
        item["b_name"], item["b_type"] = current[right]["name"], current[right]["type"]
        resolved.append(item)
    rows = resolved
    if not rows:
        return 0

    from venona.llm import budget as budget_mod

    suggested = 0
    for start in range(0, len(rows), batch):
        chunk = rows[start : start + batch]
        ctx.check_cancel()

        # Model spend like any other, and `adjudicate_limit_per_run` caps the
        # rows, not the calls. This stage runs last, so without the run's budget
        # it could spend after analyze had deferred clusters for want of one.
        if budget is not None:
            try:
                budget.check()
            except budget_mod.BudgetExceeded:
                remaining = len(rows) - start
                ctx.warn(budget.deferral(remaining, "names to decide on"))
                break

        lines = []
        for row in chunk:
            aliases = {
                r["entity_id"]: r["surfaces"]
                for r in repo.select_aliases(
                    conn, row["existing_entity_id"], row["proposed_entity_id"]
                )
            }
            titles = [
                r["title"]
                for r in repo.select_assertions(
                    conn, row["existing_entity_id"], row["proposed_entity_id"]
                )
            ]
            # Names, aliases and headlines are all corpus-derived, and `### {id}`
            # is what the verdict schema keys on -- so a name carrying a newline
            # could forge a pair the chunk never contained (ADR 0166).
            lines.append(
                f"### {row['id']}\n"
                f"A: {inline(row['a_name'], limit=200)} ({row['a_type']}) "
                f"[aliases: {inline(aliases.get(row['existing_entity_id']), limit=400) or '-'}]\n"
                f"B: {inline(row['b_name'], limit=200)} ({row['b_type']}) "
                f"[aliases: {inline(aliases.get(row['proposed_entity_id']), limit=400) or '-'}]\n"
                + (
                    "headlines: " + " · ".join(inline(t, limit=300) for t in titles if t)
                    if titles
                    else ""
                )
            )

        result = await call_and_record(
            ctx,
            "adjudicate",
            user_content="Decide each pair.\n\n" + "\n\n".join(lines),
            schema=adjudicate_schema([r["id"] for r in chunk]),
            system_prompt=adjudicate_system(),
            model=ctx.settings.llm.adjudicate_model,
            effort="low",
            budget=budget,
        )
        if not result.ok:
            ctx.warn(f"adjudication failed: {result.error}")
            continue

        by_id = {r["id"]: r for r in chunk}
        now = ctx.clock.now()
        for verdict in (result.data or {}).get("verdicts", []):
            row = by_id.get(verdict.get("conflict_id"))
            if row is None:
                continue
            stored = suggestion_for(verdict, row)
            if stored is None:
                continue
            suggestion, child_id, predicate = stored
            repo.update_alias_conflicts_by_suggestion_suggestion_note(
                conn,
                suggestion,
                (verdict.get("why") or "")[:200],
                child_id,
                predicate,
                verdict.get("confidence"),
                now,
                row["id"],
            )
            suggested += 1

    return suggested


def suggestion_for(verdict: dict, row: dict) -> tuple[str, str | None, str | None] | None:
    """`(suggestion, child_id, predicate)` to store for one verdict, or None
    when it named neither side and is not actionable."""
    action = verdict.get("verdict")
    child_name = (verdict.get("child") or "").strip()
    if action not in ("part_of", "variant") or not child_name:
        return (action, None, None) if action != "variant" else ("distinct", None, None)
    # Match the NAME back to an id ourselves. The model names a side; code
    # decides which row that is -- the same division that keeps it from ever
    # emitting an entity id (CLAUDE.md).
    if child_name == row["a_name"]:
        child_id = row["existing_entity_id"]
        child_type, parent_type = row["a_type"], row["b_type"]
    elif child_name == row["b_name"]:
        child_id = row["proposed_entity_id"]
        child_type, parent_type = row["b_type"], row["a_type"]
    else:
        return None
    predicate = _relation_for(child_type, parent_type, variant=action == "variant")
    if predicate is not None:
        return ("part_of", child_id, predicate)
    if action == "variant":
        # Two revisions of a design, and nothing here can say so: kept apart is
        # what a curator would answer, and the row would cost a call every run.
        return ("distinct", None, None)
    # One contains the other and no predicate can express that between these
    # two types. A real answer, but not one a curator can act on, so it is
    # recorded as a question rather than an instruction. Leaving the suggestion
    # NULL would put the row back in the pool and pay for the call every run.
    return ("part_of", None, None)


def declared_type_pairs(conn: db.Conn) -> list[tuple[str, str, str]]:
    """Type conflicts a person has already settled by declaring one side.

    Returns `(loser_id, survivor_id, why)`.

    The same claim the declared-alias rule makes, asked of the TYPE instead of
    the surface. `Air Force Research Laboratory` is a ResearchOrg because that
    is how it was written into the seed; a single-mention provisional reading
    the same lab as a GovernmentBody is the same lab read less carefully.

    Deliberately NOT symmetric with the rest of auto-merge: this crosses a type
    boundary, which `identical_pairs` refuses on principle. Allowed only where
    the ontology has declared that boundary confusable AND a person has stated
    which side of it the thing sits on. Both halves are required; with neither,
    this is the merge that collapses a company into an unrelated missile.
    ADR 0208.
    """
    from venona.services.seed import ANCHOR_RUN_ID, SEED_RUN_ID

    pairs: list[tuple[str, str, str]] = []
    for row in repo.select_type_conflicts(conn, (SEED_RUN_ID, ANCHOR_RUN_ID)):
        left, right = resolved_pair(conn, row)
        if left == right:
            continue
        # Exactly one side declared. Both declared is two deliberate entities
        # and none declared is two readings, and neither is ours to settle.
        if bool(row["a_declared"]) == bool(row["b_declared"]):
            continue
        survivor, loser = (left, right) if row["a_declared"] else (right, left)
        pairs.append((loser, survivor, "declared type"))
    return pairs


#: What a name can carry in FRONT of itself and still be the same name: an
#: article, which belongs to the sentence, or a possessive, which belongs to a
#: different entity entirely.
_PREFIX = re.compile(
    r"^(?:(?:the|a|an|le|la|les)\s+|l[\u2019']\s*|\S+[\u2019']s\s+)", re.IGNORECASE
)


def _survivor_of(conn: db.Conn, left: str, right: str, sides: dict) -> str:
    """Which of two entities should carry the merged identity.

    The fuller name is the right default -- the form a reader recognises, and
    the abbreviated side keeps matching because aliases move with the merge.
    Two signals outrank it, and one disqualifies:

      declared    an entity written into the seed or the anchors by a person is
                  the one they chose to name the thing. `Anduril Industries` is
                  declared and `Anduril's` is not.
      active      a corroborated entity over a single-sighting provisional.
      possessive  `Anduril's` and `Gripen fighter jets` are longer strings and
                  neither is a name. A possessive is never the survivor.
      prefix      a leading article or a leading possessive, where the name is
                  the other side once it is removed. `DARPA's Commercial
                  Strategy Office` is the office, wearing DARPA.

    Length decides only what none of those do.
    """
    from venona.services.seed import ANCHOR_RUN_ID, SEED_RUN_ID

    declared = {
        r["entity_id"]
        for r in repo.select_declared_entities(conn, (left, right), (SEED_RUN_ID, ANCHOR_RUN_ID))
    }
    if (left in declared) != (right in declared):
        return left if left in declared else right

    a, b = sides[left]["name"], sides[right]["name"]
    if (sides[left]["status"] == "active") != (sides[right]["status"] == "active"):
        return left if sides[left]["status"] == "active" else right

    possessive = {left: bool(_POSSESSIVE.search(a)), right: bool(_POSSESSIVE.search(b))}
    if possessive[left] != possessive[right]:
        return right if possessive[left] else left

    # A PREFIX ADDS LENGTH WITHOUT ADDING IDENTITY, and length is what decides
    # below -- so `DARPA's Commercial Strategy Office` beat the office it names
    # and `The U.S. Army Corps of Engineers` beat the corps. Stripping is gated
    # on the remainder BEING the other name, because a prefix is only provably
    # noise when the name survives without it: ungated, `People's Liberation
    # Army` loses its apostrophe to `Peoples Liberation Army`.
    prefixed = {side: bool(_PREFIX.match(name)) for side, name in ((left, a), (right, b))}
    if prefixed[left] != prefixed[right]:
        carrier, plain = (left, right) if prefixed[left] else (right, left)
        stripped = _PREFIX.sub("", sides[carrier]["name"])
        if _comparable(stripped).casefold() == _comparable(sides[plain]["name"]).casefold():
            return plain

    return left if len(a) >= len(b) else right


def model_distinctions(conn: db.Conn, mergeable: set[tuple[str, str]]) -> list[str]:
    """Conflict ids the model called a certain distinction, and nothing here
    contradicts.

    THE ASYMMETRY RUNS THE OTHER WAY HERE, which is why this needs no structural
    corroboration and `model_merges` does: a wrong distinction leaves two
    entities as two entities, exactly what they already were, and the pair comes
    back the next time either name is seen. ADR 0230.

    `mergeable` is the one veto, and not a similarity check either: a pair the
    DERIVATIONAL rules would merge is provably one thing, so closing it silently
    would lose a merge nobody would ever see again. Measured 2026-09-11 on the
    live queue the two sets do not intersect; the veto is there so a later
    widening of either rule cannot make them disagree in silence.
    """
    return [
        row["id"]
        for row in repo.select_distinct_verdicts(conn)
        if (row["existing_entity_id"], row["proposed_entity_id"]) not in mergeable
        and (row["proposed_entity_id"], row["existing_entity_id"]) not in mergeable
    ]


#: A leading definite article, which no name carries and several aliases do.
#: `the Air Force` is the surface an outlet wrote, and `31st Air Task Force`
#: contains the name inside it but not the article in front.
_LEADING_ARTICLE = re.compile(r"^(the|a|an)\s+", re.IGNORECASE)


def _bare(name: str) -> str:
    """`_comparable`, without a leading article.

    Scoped to this one question on purpose: it confirms a DIRECTION, where a
    false positive costs a single retractable edge. `_comparable` decides
    IDENTITY, and any change there has to hold against every merge rule at once.
    """
    return _LEADING_ARTICLE.sub("", _comparable(name))


def model_relations(conn: db.Conn) -> list[tuple[str, str, str, str]]:
    """`(conflict_id, child_id, parent_id, predicate)` the names corroborate.

    The third verdict, applied rather than suggested. `General Dynamics` and
    `General Dynamics Information Technology` are not one company, and keeping
    them apart throws away the one fact the conflict surfaced: GDIT is PART_OF
    General Dynamics.

    THE GATE IS THE DIRECTION, and it is what makes this safe to apply where a
    bare `part_of` verdict is not. `adjudicate`'s docstring records the model
    inverting exactly this question on three of twelve hand-checked pairs. So
    the child it names must be the name whose tokens CONTAIN the other's: a
    subsidiary's name carries its parent's, never the reverse. A verdict
    pointing the other way is left for a person.

    The ontology is re-asked at the point of use rather than trusted from the
    stored suggestion, which may have been written when a different predicate
    was legal (CLAUDE.md).

    An edge is not a merge. A wrong one is visible in the graph, reversible by
    retracting one assertion, and destroys no id -- which is why a structural
    corroboration is enough here where a merge needs the same gate AND the model
    at `certain`.
    """
    out: list[tuple[str, str, str, str]] = []
    ontology = get_ontology()
    mappings = resolve_mappings(conn)
    for row in repo.select_relation_verdicts(conn):
        left, right = resolved_pair(conn, row)
        if left == right:
            continue
        sides = {r["id"]: r for r in repo.select_entities_5(conn, left, right)}
        if left not in sides or right not in sides:
            continue
        # RESOLVED, like the two sides. `suggestion_child` records the id live
        # when the verdict was written and nothing rewrites it, so comparing the
        # raw id against the resolved pair silently never matches and the verdict
        # is dropped. It cost `U.S. Army Europe-Africa`, merged since the read.
        child_id = mappings.get(row["suggestion_child"], row["suggestion_child"])
        parent_id = right if child_id == left else left
        if child_id not in sides or parent_id not in sides:
            continue

        child, parent = sides[child_id]["name"], sides[parent_id]["name"]
        if not child or not parent:
            continue

        # ANY NAME THE PARENT ANSWERS TO, not only the one it displays. A merge
        # moves the loser's aliases onto the survivor and can lengthen the
        # displayed name: a parent filed as `Army` that is now `U.S. Army` is not
        # contained in `Army Medical Command`, though the relationship holds. Ten
        # correct pairs sat in the live queue on that technicality (2026-09-11).
        # An alias is the same claim the display name makes, so this widens
        # nothing.
        aliases = {
            surface.strip()
            for row_ in repo.select_aliases(conn, parent_id, parent_id)
            for surface in (row_["surfaces"] or "").split(" | ")
            if surface.strip()
        }
        # The child's tokens must contain the parent's, in that order. Direction
        # is the whole point of this check: the model inverts it.
        target = _bare(child)
        if not any(
            (found := _containment(_bare(name), target)) is not None and found[0] == _bare(name)
            for name in ({parent} | aliases)
        ):
            continue

        predicate = row["suggestion_predicate"]
        if ontology.check_relation(predicate, sides[child_id]["type"], sides[parent_id]["type"]):
            continue
        out.append((row["id"], child_id, parent_id, predicate))
    return out


def model_merges(conn: db.Conn) -> list[tuple[str, str, str]]:
    """Pairs the model called a certain merge AND the names corroborate.

    Returns `(loser_id, survivor_id, why)`, the same shape `identical_pairs`
    returns, so the caller applies both through one path.

    THIS IS THE ONE PLACE A MODEL VERDICT CHANGES THE GRAPH, and the gate is
    deliberately an intersection rather than a threshold (ADR 0174). The model
    supplies judgement the derivational rules cannot -- it can read that `Sierra
    Nevada Corp.` and `SNC` are one firm from the headlines around them. What it
    does NOT supply is licence to merge two unrelated names: every pair here must
    ALSO be a token containment or score above `NAME_VARIANT_SCORE` on
    `_comparable` forms. So the worst a wrong verdict can do is merge two
    entities a curator was going to be shown anyway.

    `adjudicate`'s docstring is why the structural half is not optional: three of
    twelve hand-checked containment pairs were wrong in the direction of calling
    one thing two, and a model that confident about the inverse question is not
    one to hand an unguarded merge.

    Survivor: see `_survivor_of`. Not simply the fuller name, because the fuller
    string is sometimes not a name at all.
    """
    pairs: list[tuple[str, str, str]] = []
    for row in repo.select_merge_verdicts(conn):
        left, right = resolved_pair(conn, row)
        if left == right:
            continue

        # Read the names for the RESOLVED ids, never the row's own columns. A
        # side merged since the row was filed resolves elsewhere, so judging on
        # the row's names decides about one pair and merges another. That is how
        # `Anduril Industries`, eleven mentions and eight edges, was merged into
        # `Anduril's`: the row described `Anduril`, and the survivor was chosen
        # for a pair that had stopped existing.
        sides = {r["id"]: r for r in repo.select_entities_5(conn, left, right)}
        if left not in sides or right not in sides:
            continue
        a, b = sides[left]["name"], sides[right]["name"]
        if not a or not b or sides[left]["type"] != sides[right]["type"]:
            continue

        corroborated = _containment(_comparable(a), _comparable(b)) is not None or (
            fuzz.token_sort_ratio(_comparable(a), _comparable(b)) >= NAME_VARIANT_SCORE
        )
        if not corroborated:
            continue
        survivor = _survivor_of(conn, left, right, sides)
        loser = right if survivor == left else left
        pairs.append((loser, survivor, "model"))
    return pairs


@dataclass
class CurateReport:
    merged: int = 0
    suggested: int = 0
    promoted: int = 0
    demoted: int = 0
    dormant: int = 0
    revived: int = 0
    conflicts_open: int = 0
    conflicts_moot: int = 0
    duplicates_filed: int = 0
    merged_by_model: int = 0
    merged_by_declared_type: int = 0
    kept_apart_by_model: int = 0
    related_by_model: int = 0
    conflicts_classified: dict[str, int] = field(default_factory=dict)
    surfaces_contested: int = 0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "merged": self.merged,
            "suggested": self.suggested,
            "promoted": self.promoted,
            "demoted": self.demoted,
            "dormant": self.dormant,
            "revived": self.revived,
            "conflicts_open": self.conflicts_open,
            "surfaces_contested": self.surfaces_contested,
            "duplicates_filed": self.duplicates_filed,
            "merged_by_model": self.merged_by_model,
            "kept_apart_by_model": self.kept_apart_by_model,
            "related_by_model": self.related_by_model,
            "merged_by_declared_type": self.merged_by_declared_type,
            **{f"conflict_{k}": v for k, v in self.conflicts_classified.items()},
        }


def _merge_key(name: str, corporate_form: set[str], generic_tails: set[str]) -> str:
    """The form under which two entities are the same thing, or "".

    `normalize` already folds case, punctuation and legal suffixes; this drops
    the two lexicons on top, so `Palantir Technologies` and `Palantir` share a
    key. Returns "" when nothing is left -- a name made entirely of form words
    is not a key, it is a collision waiting to happen.
    """
    from venona.db.models import normalize

    words = [t for t in re.split(r"[^\w]+", _comparable(name).lower()) if t]
    while words and words[-1] in generic_tails:
        words.pop()
    words = [w for w in words if w not in corporate_form]
    return normalize(" ".join(words))


def _file_entity_duplicates(
    conn: db.Conn,
    *,
    corporate_form: set[str],
    generic_tails: set[str],
    now: str,
    person_titles: set[str] | None = None,
    person_suffixes: set[str] | None = None,
    min_mentions: int = 0,
) -> int:
    """File a conflict for every pair of live entities that share a merge key.

    Asked of the CORPUS rather than of the conflict queue, because the queue
    cannot see these: a row is filed only when the resolver is asked to write a
    surface a rival already owns, and `services/seed.py` writes declared aliases
    straight into the table without going through `Resolver._add_alias`. So two
    entities can hold the same name, the same type and the same aliases with
    nothing filed about them anywhere. Three of the four splits found in the
    live corpus were that shape (2026-08-27).

    Same type files with no `kind`, for `_classify_open` to stamp on the next
    line: one classifier, not two. Different types file as `cross_type`, which
    is never mergeable.

    The net here is deliberately WIDER than the one that merges: filing a row
    only asks the question, and `identical_pairs` acts only on what it can
    prove. So a loose grouping costs a curator one keystroke, never a wrong
    merge. Ids are deterministic and sorted, so a second pass upserts the same
    row rather than filing a mirror image of it.

    ONE KEYSTROKE IS TRUE OF ONE ROW AND FALSE OF THREE HUNDRED, which is what
    `min_mentions` answers. A provisional entity below the bar is not asked
    about until a second sighting corroborates it -- deferred, never dropped,
    because this is asked of the whole corpus every run. Measured on the live
    queue: 383 of 455 open rows name a proposed entity seen fewer than twice.
    """
    from venona.db.models import normalize

    entities = repo.select_entities_6(conn, min_mentions=min_mentions)
    by_id = {row["id"]: row for row in entities}
    groups: dict[tuple[str, str], list[db.Row]] = {}
    by_name: dict[str, list[db.Row]] = {}
    for row in entities:
        key = _merge_key(row["name"], corporate_form, generic_tails)
        if not key:
            continue
        groups.setdefault((key, row["type"]), []).append(row)
        by_name.setdefault(key, []).append(row)

    # A pair already carrying a row is not asked again -- with one exception.
    # `merged` is a claim that the two ids ARE one entity, and a claim can stop
    # being true: a declaration re-applied over a tombstone puts both sides back
    # on their feet while the row still reads `merged`, leaving the pair
    # unaskable forever. `kept_apart` never expires.
    mappings = resolve_mappings(conn)
    known = set()
    for row in repo.select_alias_conflict_pairs(conn):
        left, right = row["existing_entity_id"], row["proposed_entity_id"]
        if row["resolution"] == RESOLUTION_MERGED and mappings.get(left, left) != mappings.get(
            right, right
        ):
            continue
        known.add(frozenset((left, right)))

    filed = 0
    # Declared claims FIRST. A pair gets exactly one row, so this is an order of
    # evidence: a person's declaration outranks anything derived from comparing
    # two strings, and filing it second lets the weaker reading set the
    # merge direction.
    owners: dict[tuple[str, str], set[str]] = {}
    for alias in repo.select_declared_alias_owners(conn):
        owners.setdefault((alias["normalized"], alias["type"]), set()).add(alias["entity_id"])
    for row in entities:
        claimants = owners.get((normalize(row["name"]), row["type"]), set()) - {row["id"]}
        for owner_id in sorted(claimants):
            owner = by_id.get(owner_id)
            if owner is None:
                continue
            # Owner FIRST: the id encodes which side declared the surface, and
            # `identical_pairs` reads that to pick the survivor.
            filed += _file_conflict(conn, owner, row, prefix="claim", now=now, known=known)

    for members in groups.values():
        if len(members) < 2:
            continue
        ordered = sorted(members, key=lambda m: m["id"])
        for other in ordered[1:]:
            filed += _file_conflict(conn, ordered[0], other, prefix="dup", now=now, known=known)

    # A gloss written in place of its expansion keys apart from it -- `PAC-3 MSE`
    # and `PAC-3 Missile Segment Enhancement (MSE)` -- so the short form is
    # looked up as a key of its own.
    for row in entities:
        short = _gloss_form(row["name"])
        if short is None:
            continue
        key = _merge_key(short, corporate_form, generic_tails)
        for other in groups.get((key, row["type"]), []):
            if other["id"] != row["id"]:
                left, right = sorted((row, other), key=lambda m: m["id"])
                filed += _file_conflict(conn, left, right, prefix="dup", now=now, known=known)

    for members in by_name.values():
        if len({m["type"] for m in members}) < 2:
            continue
        # One pair per type boundary, not one per entity: the question is whether
        # these two KINDS of thing share a name, and asking it once per id would
        # ask it again for every sibling.
        seen: set[tuple[str, str]] = set()
        ordered = sorted(members, key=lambda m: (m["type"], m["id"]))
        for index, left in enumerate(ordered):
            for right in ordered[index + 1 :]:
                boundary = (left["type"], right["type"])
                if left["type"] == right["type"] or boundary in seen:
                    continue
                seen.add(boundary)
                filed += _file_conflict(conn, left, right, prefix="crosstype", now=now, known=known)

    return filed


def _file_conflict(
    conn: db.Conn, a: db.Row, b: db.Row, *, prefix: str, now: str, known: set[str]
) -> int:
    """One conflict row, or 0 if the pair already has one.

    Never reopens a settled verdict: a human who kept two entities apart has
    answered permanently, and re-filing it nightly would make the queue argue
    with them. `known` holds every pair whose row still stands.
    """
    from venona.db.models import normalize

    pair = frozenset((a["id"], b["id"]))
    if pair in known:
        return 0
    known.add(pair)
    conflict_id = f"{prefix}|{a['id']}|{b['id']}"
    row = {
        "id": conflict_id,
        "surface": a["name"],
        "normalized": normalize(a["name"]),
        "existing_entity_id": a["id"],
        "proposed_entity_id": b["id"],
        "entity_type": a["type"],
        "created_at": now,
    }
    if prefix == "crosstype":
        row["kind"] = CROSS_TYPE
    elif prefix == "typeconflict":
        row["kind"] = TYPE_CONFLICT
    db.upsert(conn, "alias_conflicts", row, key=["id"])
    return 1


def _classify(
    conn: db.Conn,
    row: db.Row,
    *,
    corporate_form: set[str] | None = None,
    generic_tails: set[str] | None = None,
    person_titles: set[str] | None = None,
    person_suffixes: set[str] | None = None,
) -> str:
    """Decide what kind of conflict this is, from evidence already on disk.

    The ordering matters, and almost nothing is trusted from the id prefix. Only
    `anchor` and `claim` are, because each records something the filer READ that
    cannot be recovered from the two names: a proposed placement, and a person's
    statement that one name is the other's alias.

    Everything else is re-derived on every pass, and that is the property the
    whole design rests on -- a rule or a vocabulary that changes today relabels
    the queue that has been sitting there for weeks. `containment` is inferred
    rather than trusted from `contain|`, because the resolver's veto and the
    curator's verdict answer different questions, and reading the veto as a
    finding is what left `Palantir` and `Palantir Technologies` apart. The two
    cross-type prefixes are re-derived one step further out: which of them a
    pair belongs to is a fact about the ONTOLOGY, and the ontology is editable.
    """
    # An anchor proposal is not a name collision, so none of the inference below
    # applies to it. Without this the curate stage would reclassify it as a
    # `duplicate` on the next run and offer to MERGE an entity into its parent.
    if (row["id"] or "").startswith("anchor|"):
        return ANCHOR
    # A declared-alias claim. The filer read a human's statement that one side's
    # name is the other's surface, and a statement outranks anything inferable
    # from two strings -- `Northcom` and `U.S. Northern Command` share no token
    # at all. Whether the declaration is RIGHT is a separate question, answered
    # by withdrawing it rather than by reclassifying here: ADR 0189 and
    # `fixtures/curation/decisions.yaml::general-atomics-subsidiary`.
    if (row["id"] or "").startswith("claim|"):
        return DUPLICATE

    from venona.db.models import normalize

    left_id, right_id = row.get("existing_entity_id"), row.get("proposed_entity_id")
    sides = (
        {r["id"]: r for r in repo.select_entities_5(conn, left_id, right_id)}
        if left_id and right_id
        else {}
    )
    existing = sides[left_id]["name"] if left_id in sides else ""
    proposed = sides[right_id]["name"] if right_id in sides else ""

    cross_filed = (row["id"] or "").startswith(("crosstype|", "typeconflict|"))
    if cross_filed:
        if left_id not in sides or right_id not in sides:
            # Unreadable sides: the prefix is the filer's own statement and the
            # only evidence left.
            return TYPE_CONFLICT if (row["id"] or "").startswith("typeconflict|") else CROSS_TYPE
        return (
            TYPE_CONFLICT
            if get_ontology().confusable(sides[left_id]["type"], sides[right_id]["type"])
            else CROSS_TYPE
        )

    if not existing or not proposed:
        # A resolver-filed containment keeps its kind when the entities behind it
        # cannot be read: the prefix is the filer's own statement about what it
        # saw, and it is the only evidence left.
        return CONTAINMENT if (row["id"] or "").startswith("contain|") else RESOLVER_ERROR

    # Compare the NAMES to each other before inferring anything from the surface.
    # Everything below reasons about acronym generation, and a pair that differs
    # by a whole word is not an acronym relationship at all -- so `Palantir` /
    # `Palantir Technologies` reaches the final `return` and files as
    # `resolver_error`, which is excluded from auto-merge AND from adjudication:
    # labelled a bug and shown to nobody.
    if normalize(_comparable(existing)) == normalize(_comparable(proposed)):
        return DUPLICATE
    if _name_variant(
        existing,
        proposed,
        corporate_form=corporate_form or set(),
        generic_tails=generic_tails or set(),
    ) or _gloss_variant(
        existing,
        proposed,
        corporate_form=corporate_form or set(),
        generic_tails=generic_tails or set(),
    ):
        return DUPLICATE
    # Two spellings of one person are a DUPLICATE, not a containment. The kind is
    # what `identical_pairs` and `adjudicate` filter on, so a person pair left as
    # `containment` is one the merger never looks at.
    if sides.get(left_id, {}).get("type") == "Person" and _person_variant(
        existing,
        proposed,
        titles=person_titles or set(),
        suffixes=person_suffixes or set(),
    ):
        return DUPLICATE
    if _containment(_comparable(existing), _comparable(proposed)):
        return CONTAINMENT

    surface = (row["surface"] or "").strip()

    from venona.db.models import acronym_variants

    generates_existing = surface.upper() in {v.upper() for v in acronym_variants(existing)}
    generates_proposed = surface.upper() in {v.upper() for v in acronym_variants(proposed)}

    # Both names generating the surface is ambiguous on its own: it covers a
    # COLLISION ("Defense Intelligence Agency" vs "Defense Investment Agency")
    # and a DUPLICATE ("Next Generation C-sUAS Missile" vs "...Counter-sUAS
    # Missile") equally. What separates them is whether the NAMES are
    # near-identical. Measured 2026-08-06 on the live conflicts the gap is wide:
    # spelling variants score 88-91, genuinely different bodies 47-69.
    if generates_existing and generates_proposed:
        score = fuzz.token_sort_ratio(_comparable(existing), _comparable(proposed))
        return DUPLICATE if score >= NAME_VARIANT_SCORE else COLLISION

    # A DUPLICATE is one name being the acronym or long form of the other:
    # "MDA" / "Missile Defense Agency", or a misspelling of it.
    if generates_existing or generates_proposed:
        return DUPLICATE

    # A surface that IS one side's name was never generated, so this is a pair
    # of names for a curator, not our bug: every live row that fell through to
    # here was that shape, `Patriot interceptor` / `Patriot interceptors`.
    if sides[left_id]["type"] == sides[right_id]["type"] and normalize(surface) in {
        normalize(existing),
        normalize(proposed),
    }:
        return DUPLICATE

    # Otherwise the generator produced a surface neither name supports -- "USC"
    # for U.S. Strategic Command. That is our bug, not a curation decision.
    return RESOLVER_ERROR


def _classify_open(
    conn: db.Conn,
    *,
    corporate_form: set[str] | None = None,
    generic_tails: set[str] | None = None,
    person_titles: set[str] | None = None,
    person_suffixes: set[str] | None = None,
) -> dict[str, int]:
    """Stamp `kind` on every unresolved conflict. Pure SQL and string work.

    Idempotent and free, which is what makes it safe to run twice in a pass and
    what makes a classifier change self-applying. Resolved rows are deliberately
    never touched, so a human verdict outlives every future rule change.

    The lexicons are loaded once by the caller rather than per row: this walks
    the whole open queue twice a pass.
    """
    counts: dict[str, int] = {}
    for row in repo.select_alias_conflicts_3(conn).fetchall():
        kind = _classify(
            conn,
            row,
            corporate_form=corporate_form,
            generic_tails=generic_tails,
            person_titles=person_titles,
            person_suffixes=person_suffixes,
        )
        repo.update_alias_conflicts_by_kind_id(conn, kind, row["id"])
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def _reconcile_entity_claims(ctx: RunContext) -> int:
    """File and release the surfaces two entities both claim.

    Both directions, every run, so the suppression states the corpus as it
    stands rather than a sediment of everything ever briefly contested: a
    surface that stops colliding gets its underline back.

    Only rows this stage filed are ever released. A glossary surface with two
    accepted expansions is in the same table for a different reason, and a
    curator's `keep both` verdict lives there too; deleting one of those would
    overturn a person's decision from the wrong side of the system.

    Returns the number contested, for the run's metrics.
    """
    conn = ctx.db
    contested = alias_index.entity_collisions(conn)
    already = {row["normalized"] for row in repo.select_entity_claim_surfaces(conn).fetchall()}
    now = ctx.clock.now()

    for normalized, claimants in contested.items():
        if normalized not in already:
            repo.insert_entity_claim_surface(conn, normalized, len(claimants), now)
    for normalized in already - set(contested):
        repo.delete_entity_claim_surface(conn, normalized)

    return len(contested)


def _apply_merges(ctx: RunContext, report: CurateReport, pairs, mappings=None) -> None:
    """Apply a batch of `(loser, survivor, why)` decisions.

    Shared by the derivational merger and the model one so both record the same
    `auto: {why}` reason, converge the same way and fail the same way. Two loops
    would be two conventions for the row someone reads to understand why two
    entities became one.
    """
    conn = ctx.db
    # Hoisted: `resolve_mappings` reads all of `entity_mappings` and walks every
    # chain, and the only thing that changes it is a merge this loop just made.
    mappings = resolve_mappings(conn) if mappings is None else mappings
    for loser, survivor, why in pairs:
        # Re-resolve against the mappings AS THEY ARE NOW. `identical_pairs`
        # returns a LIST, computed before the first merge is applied, so a pair
        # two earlier merges have collapsed onto one survivor is still in it and
        # `merge_entities` refuses it. Eleven of run `06a80ae3`'s twenty warnings
        # were that refusal, the same OMB and HHS pairs twice over, burying the
        # nine that meant something (2026-08-27). Converging is the SUCCESS case
        # for a duplicate, so it is silent.
        from_id = mappings.get(loser, loser)
        to_id = mappings.get(survivor, survivor)
        if from_id == to_id:
            continue
        try:
            # The RESOLVED pair. `merge_entities` resolves both sides itself
            # (`services/curate.py`), so raw ids land on the right entities --
            # but the `reason` on the mapping row would then describe a pair the
            # merge was not applied to, and that row is what someone reads to
            # understand why two entities are one.
            merge_entities(
                conn,
                from_id=from_id,
                to_id=to_id,
                merge_id=f"merge:{ctx.ids.uuid7()}",
                reason=f"auto: {why}",
                now=ctx.clock.now(),
            )
            # Keep the local view current instead of re-reading the table.
            mappings[from_id] = to_id
            for key, value in mappings.items():
                if value == from_id:
                    mappings[key] = to_id
        except MergeError as exc:
            # A pair that has become unmergeable mid-pass (already merged
            # away, or would close a cycle) is not a run failure.
            report.warnings.append(f"auto-merge skipped {loser} -> {survivor}: {exc}")
            continue
        report.merged += 1


async def run_curate(ctx: RunContext, *, budget=None) -> CurateReport:
    from venona.pipeline.acronyms import load_merge_tokens, load_person_tokens

    conn = ctx.db
    report = CurateReport()
    limits = ctx.settings.pipeline
    corporate_form, generic_tails = load_merge_tokens(ctx.settings)
    person_titles, person_suffixes = load_person_tokens(ctx.settings)
    # ONE dict, threaded to every consumer, so the classifier, the merger and
    # the detector cannot be reading different vocabularies. An empty list is
    # the safe default and yields a MISSED merge, never a wrong one.
    lexicons = {
        "corporate_form": corporate_form,
        "generic_tails": generic_tails,
        "person_titles": person_titles,
        "person_suffixes": person_suffixes,
    }

    # ── file duplicates nothing has a conflict row for ───────────────
    # BEFORE classification, so this run's own findings are stamped by the pass
    # below rather than waiting for tomorrow's.
    report.duplicates_filed = await _unblocked(
        lambda: _file_entity_duplicates(
            conn,
            now=ctx.clock.now(),
            min_mentions=limits.file_duplicates_min_mentions,
            **lexicons,
        )
    )

    # ── classify open conflicts ──────────────────────────────────────
    # FIRST, because `kind` is this stage's own output and both consumers below
    # filter on it. `resolve.py` never sets it on insert, so classifying last
    # leaves a conflict filed by THIS run's analyze stage invisible to
    # `identical_pairs` and `adjudicate` until the following day -- and on a
    # fresh database the first `venona run` auto-merges and adjudicates nothing.
    await _unblocked(lambda: _classify_open(conn, **lexicons))

    # ── settle what has already answered itself ──────────────────────
    # A conflict outlives the entities it names. Merge one side into the other's
    # survivor and the question is moot, but the row stays open, keeps being
    # counted, and offers a decision the queue cannot carry out.
    report.conflicts_moot = await _unblocked(
        lambda: close_moot_conflicts(conn, now=ctx.clock.now())
    )

    ctx.check_cancel()

    # ── auto-merge provable duplicates ───────────────────────────────
    # BEFORE promotion, deliberately. A merge consolidates EVIDENCE across the
    # closure (`promote_provisionals` aggregates over `entity_mappings`), so
    # merging two single-article entities can carry the survivor over the gate in
    # the same pass. Running it after defers every such promotion by a full day.
    if limits.auto_merge_identical:

        def merge_provable() -> None:
            _apply_merges(ctx, report, identical_pairs(conn, **lexicons))
            # After the same-type merges, so a pair that both rules would touch
            # is already collapsed and this one converges silently rather than
            # warning.
            before_declared = report.merged
            _apply_merges(ctx, report, declared_type_pairs(conn))
            report.merged_by_declared_type = report.merged - before_declared
            report.merged = before_declared

        await _unblocked(merge_provable)

    ctx.check_cancel()

    # ── promotion ────────────────────────────────────────────────────
    # Idempotent, and re-run here so the gate still applies on a run that
    # skipped analyze (`venona run --only curate`).
    def promote() -> tuple[dict[str, str], dict[str, str]]:
        was = {r["id"]: r["status"] for r in repo.select_entities_2(conn)}
        promote_provisionals(
            conn,
            now=ctx.clock.now(),
            min_sightings=limits.provisional_promote_sightings,
            min_sources=limits.provisional_promote_sources,
            dormant_after_days=limits.provisional_dormant_days,
        )
        return was, {r["id"]: r["status"] for r in repo.select_entities_3(conn)}

    before, after = await _unblocked(promote)
    for entity_id, status in after.items():
        was = before.get(entity_id)
        if was == status:
            continue
        if status == "active":
            report.promoted += 1
        elif status == "dormant":
            report.dormant += 1
        elif was == "dormant":
            report.revived += 1
        elif was == "active":
            report.demoted += 1

    ctx.check_cancel()

    # ── adjudicate what is left ──────────────────────────────────────
    # After the merges, so pairs `identical_pairs` just settled are already
    # closed and are never paid for. Costs a few calls on a fresh backlog and
    # nothing on a worked one.
    if limits.adjudicate_conflicts:
        report.suggested = await adjudicate(
            ctx,
            limit=limits.adjudicate_limit_per_run,
            batch=limits.adjudicate_batch,
            budget=budget,
        )

    ctx.check_cancel()

    # ── apply the verdicts the names corroborate ─────────────────────
    # AFTER adjudication, so a verdict written this pass is acted on this pass
    # rather than a day later. Counted separately from `merged` because these
    # rest on a different kind of evidence and a curator auditing them wants the
    # cohort -- `venona entity merges` groups them by `auto: model`.
    if limits.auto_merge_model_verdicts:

        def merge_by_verdict() -> None:
            derivational = report.merged
            _apply_merges(ctx, report, model_merges(conn))
            report.merged_by_model = report.merged - derivational
            report.merged = derivational

        await _unblocked(merge_by_verdict)

    # ── and the verdicts that RELATE two things ──────────────────────
    # Before the distinctions below, so a pair answered with an edge is not then
    # settled as merely "kept apart" -- both are true, and the edge is the half
    # that carries the fact the conflict surfaced.
    if limits.auto_relate_model_verdicts:

        def relate() -> int:
            related = model_relations(conn)
            for conflict_id, child_id, parent_id, predicate in related:
                names = {
                    r["id"]: r["name"] for r in repo.select_entities(conn, child_id, parent_id)
                }
                curation_repo.insert_curated_relation(
                    conn,
                    assertion_id=ctx.ids.uuid7(),
                    conflict_id=conflict_id,
                    article_id="doc:curated",
                    predicate=predicate,
                    subject_id=child_id,
                    object_id=parent_id,
                    evidence_quote=(
                        f"Venona: {names.get(child_id, child_id)} {predicate} "
                        f"{names.get(parent_id, parent_id)}. The first name contains the "
                        f"second, which is how a subsidiary is written and never how a "
                        f"parent is."
                    ),
                    now=ctx.clock.now(),
                    # NOT `manual`. A row claiming a curator decided this is the
                    # same lie as a definition claiming a source it does not have.
                    method="auto",
                )
                curation_repo.mark_related(conn, conflict_id=conflict_id, now=ctx.clock.now())
            if related:
                conn.commit()
            return len(related)

        report.related_by_model = await _unblocked(relate)

    # ── and the verdicts that keep two things apart ──────────────────
    # AFTER every merge above, so the veto reads the pairs that are still two
    # entities. Gated separately from the merge half because they are opposite
    # risks: that one off is caution about a change nobody can see, this one off
    # is asking a person to confirm `Swedish Air Force` against `Sudanese Armed
    # Forces`, two names that share an acronym and nothing else. ADR 0230.
    if limits.auto_keep_apart_model_verdicts:

        def keep_apart() -> int:
            mergeable = {
                (loser, survivor) for loser, survivor, _why in identical_pairs(conn, **lexicons)
            }
            settled = model_distinctions(conn, mergeable)
            for conflict_id in settled:
                curate_repo.settle_open_conflict(conn, "kept_apart", ctx.clock.now(), conflict_id)
            if settled:
                conn.commit()
            return len(settled)

        report.kept_apart_by_model = await _unblocked(keep_apart)

    ctx.check_cancel()

    ctx.check_cancel()

    # ── surfaces two entities both claim ─────────────────────────────
    # AFTER every merge above, so a pair that was only PROPOSED as a duplicate
    # has become one entity by now and is not reported as a question. Which of
    # Army Contracting Command and Air Combat Command owns `ACC` is otherwise
    # heap order, and a reader is told an answer nothing chose; the mechanism is
    # in `alias_index.entity_collisions`.
    report.surfaces_contested = await _unblocked(lambda: _reconcile_entity_claims(ctx))

    # ── re-classify, and report ──────────────────────────────────────
    # The merges above resolved rows and changed the names behind others, so the
    # numbers the run reports come from a pass over the FINAL state.
    report.conflicts_classified = await _unblocked(lambda: _classify_open(conn, **lexicons))
    report.conflicts_open = sum(report.conflicts_classified.values())

    # The promotion gate as a funnel. Deliberately NOT a funnel over the conflict
    # queue: `suggested` leaves a row open with a suggestion on it, so conflicts
    # do not partition and a funnel there would reconcile only by accident.
    # Provisional entities do partition, and "why is this entity not active yet"
    # is the question people ask of this stage.
    provisional_before = sum(1 for status in before.values() if status == "provisional")
    funnel = Funnel("curate", offered=provisional_before)
    funnel.keep(report.promoted)
    funnel.drop("dormant", report.dormant)
    funnel.drop("still_provisional", provisional_before - report.promoted - report.dormant)
    funnel.emit(ctx)
    ctx.metric("curate", "merged", report.merged)
    ctx.metric("curate", "merged_by_model", report.merged_by_model)
    ctx.metric("curate", "kept_apart_by_model", report.kept_apart_by_model)
    ctx.metric("curate", "related_by_model", report.related_by_model)
    ctx.metric("curate", "merged_by_declared_type", report.merged_by_declared_type)
    ctx.metric("curate", "duplicates_filed", report.duplicates_filed)
    ctx.metric("curate", "suggested", report.suggested)
    ctx.metric("curate", "demoted", report.demoted)
    ctx.metric("curate", "revived", report.revived)
    ctx.metric("curate", "conflicts_open", report.conflicts_open)
    ctx.metric("curate", "conflicts_moot", report.conflicts_moot)
    ctx.metric("curate", "promote_min_sightings", limits.provisional_promote_sightings)
    ctx.metric("curate", "promote_min_sources", limits.provisional_promote_sources)
    for kind, n in report.conflicts_classified.items():
        ctx.metric("curate", f"conflict_{kind}", n)

    for warning in report.warnings:
        ctx.warn(warning)

    # Work waiting on a PERSON, not a degradation: this is the length of the
    # curation queue, and it is non-zero on every real corpus (86 on 2026-09-01).
    # `warn` makes `succeeded` unreachable on its own and `note` reads as an idle
    # remark with nowhere to go; ADR 0194 has the three channels.
    if report.conflicts_open:
        ctx.todo(
            f"{report.conflicts_open} name(s) to decide on: "
            + ", ".join(f"{v} {k}" for k, v in sorted(report.conflicts_classified.items())),
            to="curation",
        )

    return report
