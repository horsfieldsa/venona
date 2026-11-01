"""Aho-Corasick alias matching with strictness tiers.

This is where invariant 2 lands: the model tells us WHICH entities a brief talks
about; this module decides WHERE, deterministically, by scanning text we already
have. Offsets are therefore always exactly right, and re-annotating an old brief
after improving the alias table costs nothing.

Strictness is frozen: 0 never auto-matches, 1 matches only inside `tier_a`, 2 is
global and case-SENSITIVE, 3 global and case-insensitive. Which aliases land in
which tier, and why tier 1 exists: docs/DATA-MODEL.md, "The strictness scale".
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

import ahocorasick

from venona.db import core as db
from venona.db.repositories import alias_index as repo

WORD_CHARS = re.compile(r"[A-Za-z0-9]")


@dataclass(frozen=True)
class AliasEntry:
    ref: str  # entity id or gloss: id
    surface: str
    strictness: int
    is_acronym: bool
    kind: str  # entity | glossary | undefined
    relevance: float = 0.0
    # One publisher has said this exists and nothing has corroborated it. A
    # MODIFIER on `kind`, never a fourth kind -- contract C3.
    single_source: bool = False
    # Nobody has ever written this surface down for this entity: the acronym
    # generator derived it from the name. It may only annotate where the text
    # itself names the entity some other way (`annotate_blocks`, gated on
    # `present_refs`), because `API` is a real abbreviation of `AUKUS Pillar II`
    # and of five thousand other things, so the letters alone decide nothing.
    needs_context: bool = False


@dataclass(frozen=True)
class Match:
    start: int
    end: int
    ref: str
    kind: str
    confidence: float
    surface: str
    single_source: bool = False
    #: Copied from the entry, because a `Match` carries no link back to one and
    #: `annotate_blocks` is where the gate is applied. Never reaches the wire.
    needs_context: bool = False


def common_name_stoplist() -> frozenset[str]:
    """Common English words that are also real entity names in this domain.

    Never matched globally; they need tier-A gating. The words are the domain
    descriptor's (`config/domain.yaml::common_name_words`) -- "army" and "titan"
    are a defense fact, not an indexing fact.
    """
    from venona.domain import get_domain

    return frozenset(get_domain().common_name_words)


def default_strictness(*, surface: str, added_by: str, is_acronym: bool) -> int:
    """Assigned at alias WRITE time from shape + provenance, so that no backfill
    is ever needed when the annotation tiers start being consulted."""
    normalized = surface.strip()
    if len(normalized) < 3:
        return 0
    if normalized.lower() in common_name_stoplist():
        return 0
    if is_acronym or (normalized.isupper() and len(normalized) >= 3):
        return 2
    if added_by == "seed" and " " in normalized and len(normalized) >= 8:
        return 3
    if added_by in {"seed", "manual"}:
        return 2 if len(normalized) >= 6 else 1
    return 1


def _fold(text: str) -> str:
    """Lowercase without changing length.

    `str.lower()` is not length-preserving: `'İ'.lower()` is two code points
    (`i` + U+0307). The folded string is only ever an Aho-Corasick haystack whose
    match offsets are applied to the ORIGINAL text, so a single Turkish dotted
    capital anywhere in a headline shifted every entity highlight after it by one
    -- and the highlight would look like a model error, not an encoding one.

    Characters that lower to more than one code point keep only the first, so
    `İstanbul` still folds to something `istanbul` matches. No character lowers
    to nothing, so a length-preserving result means the mapping was 1:1.
    """
    lowered = text.lower()
    if len(lowered) == len(text):
        return lowered
    return "".join(ch.lower()[:1] for ch in text)


class AliasIndex:
    """Two automatons: one case-sensitive (acronyms), one folded (everything
    else). Splitting them is what lets "MOSA" match while "mosa" doesn't."""

    def __init__(self, entries: list[AliasEntry]) -> None:
        self._entries = entries
        self._by_ref: dict[str, list[AliasEntry]] = {}
        for entry in entries:
            self._by_ref.setdefault(entry.ref, []).append(entry)

        self._cs = ahocorasick.Automaton()
        self._ci = ahocorasick.Automaton()
        cs_n = ci_n = 0

        for entry in entries:
            if entry.strictness == 0:
                continue  # never auto-matched
            if entry.strictness == 2 and entry.surface.isupper():
                self._cs.add_word(entry.surface, entry)
                cs_n += 1
            else:
                # `_fold`, not `.lower()`: the pattern's length has to stay equal
                # to `len(entry.surface)`, which `find` subtracts from the match
                # end to get the start offset.
                self._ci.add_word(_fold(entry.surface), entry)
                ci_n += 1

        if cs_n:
            self._cs.make_automaton()
        if ci_n:
            self._ci.make_automaton()
        self._cs_ready, self._ci_ready = bool(cs_n), bool(ci_n)

    def __len__(self) -> int:
        return len(self._entries)

    def aliases_for(self, ref: str) -> list[str]:
        return [e.surface for e in self._by_ref.get(ref, [])]

    def find(
        self,
        text: str,
        *,
        tier_a: frozenset[str] | None = None,
        strict_case: bool = False,
    ) -> list[Match]:
        """Scan `text`, returning non-overlapping matches.

        `tier_a` is the set of refs the model said this brief mentions. Aliases
        with strictness 1 only match if their ref is in it.

        `strict_case` additionally requires a strictness-1 match to agree with
        its alias on case. OFF by default, because on a brief `tier_a` is one
        brief's retrieval set, so a strictness-1 alias is already gated to an
        entity the pipeline decided this document is about.

        Turn it ON when `tier_a` is the whole corpus, which is what annotating
        an arbitrary web page needs. Strictness 1 is the DEFAULT tier for an
        extracted entity name, and strictness-1 entries live in the FOLDED
        automaton (see `__init__`) -- so widening `tier_a` to every active entity
        quietly makes most of them looser than strictness 2, which is reserved
        for all-caps acronyms (2026-08-16: 525 of 1003 indexed aliases). The
        corpus then held `Neutron`, `Horizon`, `Lattice`, `Foundry`, `Fury`,
        `Patriot` and `Raft`, none in `common_name_stoplist()` and all ordinary
        English:

            'A neutron star was observed.'  ->  plat:neutron @ 0.9

        0.9 is above the reader's 0.7 dimming threshold (`TextRuns.vue`), so it
        renders as a confident underline on a page about astronomy. Requiring
        case is the narrowest fix that keeps `Lockheed Martin` and drops `a
        neutron star`; an empty `tier_a` silently drops all 525 instead,
        Raytheon and Anduril included.
        """
        tier_a = tier_a or frozenset()
        candidates: list[Match] = []

        if self._cs_ready:
            for end_idx, entry in self._cs.iter(text):
                start = end_idx - len(entry.surface) + 1
                candidates.append(self._to_match(text, start, entry, tier_a, 1.0, strict_case))

        if self._ci_ready:
            # Offsets from this scan index into `text`, so the fold must not
            # change the length. See `_fold`.
            for end_idx, entry in self._ci.iter(_fold(text)):
                start = end_idx - len(entry.surface) + 1
                candidates.append(self._to_match(text, start, entry, tier_a, 0.9, strict_case))

        return _resolve_overlaps([c for c in candidates if c is not None])

    def _to_match(
        self,
        text: str,
        start: int,
        entry: AliasEntry,
        tier_a: frozenset[str],
        base_confidence: float,
        strict_case: bool = False,
    ) -> Match | None:
        end = start + len(entry.surface)

        if not _is_word_bounded(text, start, end):
            return None
        if entry.strictness == 1 and entry.ref not in tier_a:
            return None
        # Case is a property of the ALIAS, not of the tier it sits in: gating
        # this on strictness 1 would leave every other folded entry uncased, and
        # strictness 3 plus strictness 2 whose surface is not all-upper both
        # route to `self._ci`. `fixtures/seed/entities.yaml` declares
        # `{ surface: Lattice, strictness: 3 }` -- a false positive by shape.
        #
        # A no-op for the case-sensitive automaton, where the found text equals
        # the surface by construction and `_case_agrees` returns True at once.
        if strict_case and not _case_agrees(text[start:end], entry.surface):
            return None

        confidence = base_confidence if entry.ref in tier_a else base_confidence - 0.3
        return Match(
            start=start,
            end=end,
            ref=entry.ref,
            kind=entry.kind,
            confidence=round(min(1.0, max(0.0, confidence)), 2),
            surface=text[start:end],
            single_source=entry.single_source,
            needs_context=entry.needs_context,
        )


def _case_agrees(found: str, surface: str) -> bool:
    """Does `found` differ from `surface` only by sentence capitalisation?

    Exact equality would be very slightly too strict, and in a way that shows
    immediately: `unit:u-s-army` carries the alias `the Army`, so a sentence
    beginning "The Army selected..." would not match it. Capitalising a sentence
    only ever ADDS case, so the relaxation is one-directional: a lowercase
    initial in the alias may appear uppercase in the text.

    Doing it symmetrically would undo the whole point -- `Neutron` and `neutron`
    differ in exactly one character's case too, and re-admitting that pair is
    the false positive this function exists to stop.
    """
    if found == surface:
        return True
    if not found or not surface or len(found) != len(surface):
        return False
    if not surface[0].islower():
        return False
    return found[0] == surface[0].upper() and found[1:] == surface[1:]


def _is_word_bounded(text: str, start: int, end: int) -> bool:
    """Reject matches inside larger words: 'C2' must not match in 'C2PA', and
    'Army' must not match in 'Armysurplus'."""
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    return not (WORD_CHARS.match(before) or WORD_CHARS.match(after))


def _resolve_overlaps(matches: list[Match]) -> list[Match]:
    """Longest wins; on a tie, higher confidence. Entity spans never overlap --
    the wire format has no way to represent nesting."""
    if not matches:
        return []
    ordered = sorted(matches, key=lambda m: (m.start, -(m.end - m.start), -m.confidence))

    kept: list[Match] = []
    for match in ordered:
        if kept and match.start < kept[-1].end:
            previous = kept[-1]
            if (match.end - match.start) > (previous.end - previous.start) or (
                (match.end - match.start) == (previous.end - previous.start)
                and match.confidence > previous.confidence
            ):
                kept[-1] = match
            continue
        kept.append(match)
    return kept


def _resolved_entity_aliases(conn: db.Conn) -> Iterator[tuple[db.Row, str, int]]:
    """Entity aliases the index will match, each with its resolved ref and its
    EFFECTIVE strictness (after the ambiguity demotion).

    **The single definition of "this surface already resolves to an entity".**
    `build_index` builds entries from it and the acronyms stage asks
    `indexed_entity_surfaces` the same question, so the two cannot disagree.
    A second query is how they come to disagree: an unfiltered
    `SELECT surface FROM aliases` lets an alias on a PROVISIONAL entity suppress
    the glossary entry while not being in the index, leaving the surface neither
    grounded nor annotatable. VICTOR sat in that gap, along with 80 others.

    Strictness is resolved HERE, not in `build_index`, for the same reason: a
    demoted alias is in the table and in this query but matches nothing, so a
    caller that skipped on membership alone would reopen the gap for `ATLAS`,
    `TITAN` and every other `common_name_stoplist()` surface.
    """
    # `merged` is included deliberately. Excluding it would make a merge STRICTLY
    # WORSE than no merge: the loser's aliases ("MDA", "FBI") would stop matching
    # entirely, so annotation would lose the very surfaces the merge was meant to
    # unify. They stay in the index and are repointed at the survivor below.
    #
    # `provisional` is included too, and what makes it SAFE is `tier_a`, not a
    # quality bar -- three were tried and none separates the cases (2026-08-16:
    # `entities.relevance` had 16 non-zero rows in 2277; `assertions.salience`
    # put `Air` at 0.40, alongside `FUSE Inc.` and above `Govini` at 0.20;
    # `strictness` put `Air` and `Govini` both at 1). What separates them is the
    # SURFACE, and `_to_match` already expresses it: on a brief `tier_a` is that
    # brief's retrieval set, and on a third-party page `annotate_service` sets it
    # to the ACTIVE entities only -- so a provisional strictness-1 alias is never
    # in either, and ordinary English like `Air`, `Hunter` and `Fury` stays out
    # of arbitrary prose by construction.
    #
    # What DOES reach a third-party page is a provisional alias at strictness 2
    # -- an all-caps acronym such as `MH-60R` -- which is not ordinary English
    # and is the thing a reader most needs there. `_to_match` docks 0.3 outside
    # `tier_a`, so it lands at 0.7 or 0.6: visibly a guess, which it is.
    from venona.graph.projector import resolve_mappings  # noqa: PLC0415

    ambiguous = {row["normalized"] for row in repo.select_ambiguous_surfaces(conn)}
    mappings = resolve_mappings(conn)
    # The same set the query admits, because a merged alias is repointed INTO
    # it. Checking `active` alone here would drop a merge's aliases and keep the
    # survivor's own -- the same "strictly worse than no merge" failure again.
    annotatable = {r["id"] for r in repo.select_entities(conn)}

    for row in repo.select_aliases(conn):
        ref = mappings.get(row["ref"], row["ref"])
        # A merged alias only survives if its SURVIVOR is still annotatable;
        # otherwise the index points at an entity nothing can open.
        if ref not in annotatable:
            continue
        yield row, ref, (0 if row["normalized"] in ambiguous else row["strictness"])


def entity_collisions(conn: db.Conn) -> dict[str, set[str]]:
    """Surfaces that two different entities both claim, keyed by normalized.

    `_resolve_overlaps` breaks a tie between two equal-strictness entries by
    INSERTION ORDER, and `select_aliases` has no `ORDER BY`, so which of `Army
    Contracting Command` and `Air Combat Command` owns `ACC` is heap order. One
    of them is being asserted to a reader as the answer; nothing chose it.

    Counted on the RESOLVED ref, so a pair actually merged is one entity and not
    a collision. A pair merely PROPOSED for merging still counts: the
    `entity_mappings` row arrives when the merge is applied, and until then they
    are two entities as far as everything downstream can tell.

    Counted on the RAW strictness rather than the effective one, because a
    surface this function's own output has already suppressed reads back as
    strictness 0 -- and dropping it here would release the suppression, re-file
    it on the next run, and flap forever.

    `needs_context` claimants are excluded: a generated acronym cannot take a
    surface from anyone, so it cannot make one contested. Booz Allen Hamilton
    against a person's generated `BAH` is one entity and one artefact.
    """
    from venona.graph.projector import resolve_mappings  # noqa: PLC0415

    mappings = resolve_mappings(conn)
    annotatable = {r["id"] for r in repo.select_entities(conn)}
    glossary_surfaces = {row["surface"].upper() for row in repo.select_acronym_definitions_2(conn)}
    statuses = {r["id"]: r["status"] for r in repo.select_entities_2(conn)}

    claims: dict[str, set[str]] = {}
    for row in repo.select_aliases(conn):
        ref = mappings.get(row["ref"], row["ref"])
        if ref not in annotatable:
            continue
        if not row["strictness"]:
            continue
        if row["added_by"] == "acronym_gen":
            continue
        # The same drop `_entity_entries` applies: a provisional entity's alias
        # loses to a glossary row that already annotates the surface, so it is
        # not a claimant and cannot contest anything.
        if statuses.get(ref) == "provisional" and row["surface"].upper() in glossary_surfaces:
            continue
        claims.setdefault(row["normalized"], set()).add(ref)

    return {surface: refs for surface, refs in claims.items() if len(refs) > 1}


def senses(conn: db.Conn) -> dict[str, list[dict]]:
    """Every grounded reading of every ambiguous surface, keyed by normalized.

    The raw material for context resolution. The index refuses to annotate an
    ambiguous surface at all, because marking `FCC` with one of its meanings is a
    coin flip that reads as a fact. With the readings in hand a caller can do
    better than that floor: pick the one the surrounding text supports, and fall
    back to unmarked when it supports none.
    """
    rows = repo.select_acronym_definitions(conn).fetchall()
    out: dict[str, list[dict]] = {}
    for row in rows:
        out.setdefault(row["normalized"], []).append(
            {"ref": row["id"], "expansion": row["expansion"], "confidence": row["confidence"]}
        )
    return out


def pick_sense(readings: list[dict], context: str) -> dict | None:
    """Which reading the surrounding text supports, or None.

    **The whole point is that it usually returns None.** `FCC` means the Federal
    Communications Commission in one article and a fire control center in
    another, and the letters alone cannot tell you which. Declining costs an
    annotation; guessing costs the reader's trust in every annotation.

    The evidence it accepts is deliberately narrow: the expansion, spelled out,
    somewhere in the same text -- the writer defining their own acronym, which is
    the same evidence tier 1 accepts from a document. Not a similarity score, not
    a model call, and nothing that could rank two readings neither of which is
    present.

    Ties decline too. Text that spells out BOTH readings is text about the
    ambiguity itself, and picking the higher-confidence one there would be
    picking by prior rather than by context.
    """
    if not readings or not context:
        return None
    haystack = _normalise(context)
    present = [r for r in readings if _normalise(r["expansion"]) in haystack]
    return present[0] if len(present) == 1 else None


def _normalise(text: str) -> str:
    """Case, whitespace and hyphenation are formatting, not meaning."""
    return re.sub(r"[\s‐-―-]+", " ", text).strip().lower()


def indexed_entity_surfaces(conn: db.Conn) -> set[str]:
    """Upper-cased surfaces that already resolve to an entity in the index.

    The acronyms stage skips these: they are annotated as entities, so a
    glossary entry for the same surface would match twice. Strictness 0 is
    excluded because it never auto-matches -- skipping on it would leave the
    surface unannotated AND ungrounded, which is the VICTOR bug.

    Derived from `_entity_entries` rather than from a second query: the
    invariant the two callers share is "claimed exactly when matchable", and a
    filter written twice is a filter that drifts on one side. `provisional`
    entities are annotated, so they are claimed here too -- skipped BECAUSE they
    are annotated, not skipped and unannotated.
    """
    return {entry.surface.upper() for entry in _entity_entries(conn) if entry.strictness > 0}


def _entity_entries(conn: db.Conn) -> list[AliasEntry]:
    """Every ENTITY entry the index will hold. The one place they are built.

    `build_index` indexes these and `indexed_entity_surfaces` claims them, so
    the two cannot disagree about which surfaces resolve to an entity.
    """
    # Surfaces the glossary already annotates. A provisional entity's alias is
    # dropped where it collides with one, because BOTH would enter the automaton
    # with the same text, the same length and strictness 2, and
    # `_resolve_overlaps` breaks that tie by INSERTION ORDER -- so which of the
    # two a span resolved to would depend on row order. 2026-08-16: 204 live
    # surfaces were in exactly this state (`A400M`, `ACE`, `AIR`, …), every one a
    # provisional entity minted from an acronym that already had a glossary row.
    #
    # The glossary wins because it is the established direction: the acronyms
    # stage already asks `indexed_entity_surfaces` before minting, so a surface
    # that HAS a row got it while nothing else claimed the surface. Reversing it
    # here would silently retire every one of those grounded and undefined
    # acronyms. The cost is that a provisional entity whose name is an acronym
    # (MH-60R) renders as an undefined acronym rather than as a Platform;
    # collapsing the two is a separate decision with its own evidence.
    glossary_surfaces = {row["surface"].upper() for row in repo.select_acronym_definitions_2(conn)}

    # The RESOLVED ref's status, so a merged alias reports its survivor's.
    statuses = {r["id"]: r["status"] for r in repo.select_entities_2(conn)}

    entries: list[AliasEntry] = []
    for row, ref, strictness in _resolved_entity_aliases(conn):
        # The RESOLVED survivor's status, not `row["status"]`. A merged alias's
        # own row always reads `merged`, so testing it lets an alias whose
        # survivor is provisional past the guard and straight into the
        # insertion-order tie this exists to prevent -- `MADIS` and `RCT30` were
        # the live pair, both all-caps at strictness 2, the shape that collides.
        if statuses.get(ref) == "provisional" and row["surface"].upper() in glossary_surfaces:
            continue
        entries.append(
            AliasEntry(
                ref=ref,
                surface=row["surface"],
                strictness=strictness,
                is_acronym=bool(row["is_acronym"]),
                kind="entity",
                relevance=row["relevance"] or 0.0,
                # The survivor's status, for the same reason: a merged alias is
                # repointed at its survivor, and it is the SURVIVOR's
                # corroboration that the reader is being told about.
                single_source=(statuses.get(ref) == "provisional"),
                needs_context=(row["added_by"] == "acronym_gen"),
            )
        )
    return entries


def refs_present(
    index: AliasIndex,
    context: str,
    *,
    tier_a: frozenset[str] | None = None,
    strict_case: bool = False,
) -> frozenset[str]:
    """Which entities this text names in a way somebody actually wrote down.

    The corroboration set for `needs_context` entries. Computed from the WHOLE
    text -- a page for the extension, a brief for a brief -- because an acronym
    introduced in one paragraph and used bare in the fifth is the ordinary way
    people write, which is why `pick_sense` reads the document too.

    `tier_a` and `strict_case` must be the caller's real ones, or the gate would
    admit a ref the annotating pass could never have matched.
    """
    return frozenset(
        match.ref
        for match in index.find(context, tier_a=tier_a, strict_case=strict_case)
        if not match.needs_context
    )


#: Built indexes, one per database, each guarded by the fingerprint of the
#: values it was built from. Keyed on `conn.dsn` and size-capped for the reason
#: `corpus_check._AUDIT_CACHE` records: throwaway test databases seeded through
#: a `FrozenClock` can carry identical stamps, and there are hundreds per
#: process.
_INDEX_CACHE: dict[str, tuple[tuple, AliasIndex]] = {}
_INDEX_CACHE_MAX = 8


def build_index(conn: db.Conn) -> AliasIndex:
    """Assemble the index from entities, their aliases, and the glossary.

    MEMOISED BEHIND A VALUE FINGERPRINT. The automaton build measured ~84ms on a
    1900-entry index (2026-08-26) and every brief read pays it once for the title
    and once for each headline, so the read path was rebuilding the same index a
    dozen times to render one page.

    The guard is `select_index_fingerprint`, which hashes the columns rather than
    counting the rows -- see its docstring for why counting cannot be safe. If
    you add a column to one of the three queries this reads, add it to the
    fingerprint in the same change.
    """
    fingerprint = tuple(repo.select_index_fingerprint(conn).fetchone().values())
    cached = _INDEX_CACHE.get(conn.dsn)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]

    index = _build_index(conn)

    if conn.dsn not in _INDEX_CACHE and len(_INDEX_CACHE) >= _INDEX_CACHE_MAX:
        _INDEX_CACHE.pop(next(iter(_INDEX_CACHE)))
    _INDEX_CACHE[conn.dsn] = (fingerprint, index)
    return index


def _build_index(conn: db.Conn) -> AliasIndex:
    entries: list[AliasEntry] = list(_entity_entries(conn))

    ambiguous = {row["normalized"] for row in repo.select_ambiguous_surfaces_2(conn)}

    seen_ambiguous: set[str] = set()

    # A declared-ambiguous surface takes its placeholder from EVERY definition,
    # including one that names an entity. That entity's alias cannot cover it:
    # `_resolved_entity_aliases` demotes an ambiguous surface to strictness 0, so
    # it sits in the table, in the index, and matched by nothing. Read only the
    # unbound definitions here and there is no placeholder either, leaving the
    # surface as plain text with a grounded reading right there -- the shape
    # `SDA`, `EW` and `XM30` had on the corpus (2026-09-08).
    for row in repo.select_acronym_definitions_ambiguous(conn):
        if row["normalized"] in ambiguous:
            # Not indexed under its own id -- which reading would it be? It
            # enters once, as a placeholder the annotator resolves against the
            # surrounding text, and is dropped there if the text supports no
            # single reading. Indexing every reading instead would put two
            # entries with the same surface in the automaton and let
            # `_resolve_overlaps` decide what FCC means by row order.
            if row["normalized"] not in seen_ambiguous:
                seen_ambiguous.add(row["normalized"])
                entries.append(
                    AliasEntry(
                        ref=f"ambiguous:{row['normalized']}",
                        surface=row["surface"],
                        strictness=default_strictness(
                            surface=row["surface"], added_by="glossary", is_acronym=True
                        ),
                        is_acronym=True,
                        kind="ambiguous",
                    )
                )

    # Glossary entries annotate in their own right too, but only where they
    # don't already resolve to an entity -- otherwise MOSA would match twice.
    for row in repo.select_acronym_definitions_glossary(conn):
        if row["normalized"] in ambiguous:
            continue  # handled above, as a placeholder
        if row["status"] == "ambiguous":
            # An `ambiguous` ROW whose surface is not in `ambiguous_surfaces`.
            # The two drift, because `PATCH /glossary/{id}` sets a status without
            # touching that table. Falling through would index the row under its
            # own id, so a surface with two of them puts two entries with the
            # same text in the automaton and `_resolve_overlaps` decides what it
            # means by insertion order. Skipping is what the surrounding code
            # already does with an unsettled surface.
            continue
        surface = row["surface"]
        entries.append(
            AliasEntry(
                ref=row["id"],
                surface=surface,
                strictness=default_strictness(
                    surface=surface, added_by="glossary", is_acronym=True
                ),
                is_acronym=True,
                kind="undefined" if row["status"] == "undefined" else "glossary",
            )
        )

    return AliasIndex(entries)
