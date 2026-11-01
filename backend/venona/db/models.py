"""Contract C4: the entity id scheme, plus the normalisation and acronym
machinery that entity resolution and acronym grounding BOTH depend on.

`acronym_variants()` is used twice and must never be duplicated:
  * entity resolution, to generate the aliases that let "NGC2",
    "Next Generation Command and Control" and "next-gen C2" collapse to one id;
  * acronym Tier 1, to VERIFY that a parenthetical expansion actually generates
    the acronym it claims to (rejecting fabricated ones).

Ids are minted once from the canonical name and are immutable. They appear in
URLs, sessionStorage keys, every fixture and every test assertion -- renaming a
prefix later is a repo-wide sed plus a fixture regeneration.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

from venona.ontology import get_ontology

# All of these are DERIVED from config/ontology.yaml -- the research<->graph
# contract. Nothing here may be edited directly; change the YAML and everything
# downstream (model schema, writer whitelist, docs, tokens) follows.
#
# Binding at IMPORT is only safe for the entity-type family, and only because
# the config editor freezes it: adding a type needs a design token and seed
# examples, and a prefix is the id namespace (C4), so neither can change under a
# live database.
#
# NOTHING ontology-derived is hoisted here -- no type list, no prefix map, no
# compiled id pattern. The ontology is editable over HTTP and a UI-triggered
# run executes in the API process, so an import-time snapshot silently drops
# whatever was just saved: a predicate the model was told to use, or -- now
# that the type set is per-domain -- a type it was told to emit. Read from
# `get_ontology()` at the point of use; `id_pattern()` memoizes on the prefix
# tuple so the hot path in resolve pays one dict lookup, not a recompile.


def type_prefix() -> dict[str, str]:
    return get_ontology().type_prefix


def prefix_type() -> dict[str, str]:
    return get_ontology().prefix_type


def entity_types():
    return get_ontology().type_names


@lru_cache(maxsize=8)
def _compiled_id_pattern(prefixes: tuple[str, ...]) -> re.Pattern[str]:
    joined = "|".join(prefixes)
    return re.compile(rf"^({joined}):[a-z0-9][a-z0-9-]{{0,63}}$")


def id_pattern() -> re.Pattern[str]:
    # `gloss` is not an ontology entity type -- glossary entries are
    # definitions, not graph nodes -- but it shares the id namespace so it
    # belongs in the pattern.
    return _compiled_id_pattern(tuple([*sorted(get_ontology().type_prefix.values()), "gloss"]))


#: Assertion methods that state a fact rather than report reading one.
#:
#: An edge written by one of these and citing no readable source is a
#: DECLARATION: `profile/anchors.yaml` saying which department a command reports
#: to, or a curator ruling on a proposal. Neither makes any claim about
#: publishers, so neither may be counted as corroborated OR as single-source --
#: see `docs/adr/0195`.
#:
#: `extraction` is deliberately absent. An extracted assertion whose document row
#: has gone missing demonstrably HAD evidence, which is what the `or 1` fallback
#: in `graph/writer.py::upsert_edges` is for.
#:
#: One definition, imported by all three consumers, because two implementations
#: of "is this edge corroborated" that disagree is precisely the defect the
#: graph-health skill exists to catch.
DECLARING_METHODS: frozenset[str] = frozenset({"seed", "manual"})

LEGAL_SUFFIXES = re.compile(
    r"\b(inc|llc|l\.l\.c|corp|corporation|ltd|limited|plc|co|gmbh|s\.a|n\.v|ag|pte)\b\.?",
    re.IGNORECASE,
)
STOPWORDS = frozenset({"of", "and", "the", "for", "to", "in", "on", "at", "a", "an", "&", "or"})


def normalize(text: str) -> str:
    """Canonical comparison form. Used for alias keys and dedup of surfaces."""
    s = unicodedata.normalize("NFKC", text)
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = s.lower().replace("&", " and ")
    s = LEGAL_SUFFIXES.sub(" ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def slugify(text: str, *, max_len: int = 64) -> str:
    """NFKD -> strip marks -> ASCII fold -> lowercase -> collapse -> truncate on
    a hyphen boundary."""
    s = unicodedata.normalize("NFKD", text)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.encode("ascii", "ignore").decode().lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    if len(s) > max_len:
        s = s[:max_len].rsplit("-", 1)[0] or s[:max_len]
    return s.strip("-")


def mint_id(entity_type: str, name: str) -> str:
    """Deterministic, human-readable, immutable. A rename adds an alias; the id
    never changes, so bookmarks and fixtures survive rebuilds."""
    prefix = type_prefix().get(entity_type)
    if prefix is None:
        raise ValueError(f"unknown entity type {entity_type!r}")
    slug = slugify(name)
    if not slug:
        raise ValueError(f"cannot slugify {name!r}")
    entity_id = f"{prefix}:{slug}"
    if not id_pattern().match(entity_id):
        raise ValueError(f"minted invalid id {entity_id!r}")
    return entity_id


def glossary_id(surface: str) -> str:
    slug = slugify(surface)
    if not slug:
        raise ValueError(f"cannot slugify {surface!r}")
    return f"gloss:{slug}"


def id_prefix(entity_id: str) -> str:
    """Drives `var(--entity-${prefix})` on the frontend -- no lookup table."""
    return entity_id.split(":", 1)[0]


def is_valid_id(value: str) -> bool:
    return bool(id_pattern().match(value))


# ── acronyms ─────────────────────────────────────────────────────────

# Leading words that are grammar, not part of a name. Without this,
# "a Modular Open Systems Approach (MOSA)" yields "a Modular Open Systems
# Approach" -- these are stopwords to acronym_variants(), so the longer window
# verifies equally well and wins, storing the same concept twice under
# near-identical strings.
_LEADING_NOISE = frozenset({"a", "an", "the", "its", "their", "our", "this", "that"})

_ACRONYM_SHAPE = re.compile(r"^[A-Z][A-Z0-9]{1,7}(?:-[A-Z0-9]{1,4})?$")

# Roman numerals read as acronyms by shape: "Division I (DI)", "Division II
# (DII)". Defining those teaches the reader nothing and fills the review queue.
#
# Shape alone cannot decide this -- "CIV" and "MIL" are valid numerals AND
# real abbreviations. So the numeral check is applied to the EXPANSION's last
# word instead: a pairing only counts as a numbering scheme when the thing
# being numbered ends in a numeral.
_ROMAN_NUMERAL = re.compile(r"^[IVXLCDM]{1,7}$")


def looks_like_acronym(surface: str) -> bool:
    return bool(_ACRONYM_SHAPE.match(surface.strip()))


def is_numbering_scheme(acronym: str, expansion: str) -> bool:
    """True for "Division I (DI)" and friends.

    Judged on the EXPANSION, not the acronym: "CIV" and "MIL" are valid Roman
    numerals but perfectly real abbreviations, so rejecting by acronym shape
    would throw away good definitions.
    """
    words = expansion.strip().split()
    return bool(words) and bool(_ROMAN_NUMERAL.match(words[-1]))


def _all_words(name: str) -> list[str]:
    return [w for w in re.split(r"[\s\-/]+", name.strip()) if w]


def _significant_words(name: str) -> list[str]:
    return [w for w in _all_words(name) if w.lower() not in STOPWORDS]


def _compound_words(name: str) -> list[str]:
    """Words with hyphenated compounds left WHOLE, stopwords dropped.

    English writes some compounds with a hyphen and still speaks them as one
    word, so "Next-generation" contributes one initial, not two. Splitting on
    the hyphen -- which both lists above do -- makes that impossible to express,
    and the cost was measured 2026-08-09: the corpus contains
    "Viper Experimentation and Next-generation Operations Model (VENOM)", a
    textbook tier-1 parenthetical, and the generator could only produce VENGOM
    and VEANGOM. Tier 1 therefore rejected the correct definition sitting in a
    document we already held, the surface fell through to tier 3, and a paid
    web lookup returned a security vulnerability of the same name.
    """
    return [w for w in re.split(r"[\s/]+", name.strip()) if w and w.lower() not in STOPWORDS]


def _variants_from(words: list[str]) -> set[str]:
    """Every acronym shape one word list can produce."""
    variants: set[str] = set()
    initials = [w[0].upper() for w in words if w[:1].isalnum()]
    if len(initials) < 2:
        return variants

    variants.add("".join(initials))

    # Repeated-initial compression: C C -> C2, C C C C -> C4.
    compressed: list[str] = []
    i = 0
    while i < len(initials):
        j = i
        while j + 1 < len(initials) and initials[j + 1] == initials[i]:
            j += 1
        run = j - i + 1
        compressed.append(initials[i] if run == 1 else f"{initials[i]}{run}")
        i = j + 1
    variants.add("".join(compressed))

    # Trailing all-caps tokens are usually already acronyms (…C4ISR, …ISR).
    tail_acronyms = [w.upper() for w in words if w.isupper() and len(w) >= 2]
    if tail_acronyms:
        head = [w[0].upper() for w in words if not (w.isupper() and len(w) >= 2)]
        if head:
            hc: list[str] = []
            i = 0
            while i < len(head):
                j = i
                while j + 1 < len(head) and head[j + 1] == head[i]:
                    j += 1
                run = j - i + 1
                hc.append(head[i] if run == 1 else f"{head[i]}{run}")
                i = j + 1
            variants.add("".join(hc) + "".join(tail_acronyms))

    return variants


#: An acronym as a publication prints it: capitals and digits, nothing else.
#:
#: `acronym_variants` is deliberately generous because it doubles as the tier-1
#: VALIDATION set -- a parenthetical claim is accepted only if the expansion
#: generates the acronym -- so it keeps shapes that are useful to compare
#: against and useless to write down. A name carrying a dotted token is where
#: the two purposes part: `"U.S.".isupper()` is true, so `_variants_from` reads
#: it as an already-acronym tail and pastes it in whole, yielding `NU.S.` from
#: `U.S. Navy` and `SCU.S.` from `U.S. Space Command`. Nobody writes those.
ACRONYM_SHAPE = re.compile(r"^[A-Z0-9]+$")

#: Shortest generated acronym worth writing. Two letters are far too ambiguous
#: to earn a global match.
ACRONYM_MIN_LENGTH = 3


def is_writable_acronym(variant: str) -> bool:
    """Whether a generated variant may be written to `aliases`.

    The gate belongs at the WRITE sites rather than inside `acronym_variants`,
    which is also read by `verify_expansion` and `curate._is_acronym_of`:
    narrowing the generated set would start rejecting real definitions.
    """
    return len(variant) >= ACRONYM_MIN_LENGTH and bool(ACRONYM_SHAPE.fullmatch(variant))


def acronym_variants(name: str, *, lexicon: dict[str, list[str]] | None = None) -> set[str]:
    """Generate the acronym forms a name can plausibly be written as.

    Handles the defence-specific pattern that generic initialism code misses:
    REPEATED-INITIAL COMPRESSION. "Command and Control" -> C2, and
    "Command, Control, Communications, Computers, ISR" -> C4ISR. That is the
    rule that makes NGC2 fall out of "Next Generation Command and Control"
    automatically instead of needing to be hand-listed.

    Also used in reverse by acronym Tier 1: if a parenthetical claims
    "Fabricated Name (NGC2)", NGC2 will not be in this set and the claim is
    rejected.

    Variants come from BOTH the significant words and ALL the words, because
    English is inconsistent about whether a stopword contributes an initial:
    "Department of Defense" is DoD but "Department of Homeland Security" is DHS.
    Dropping stopwords unconditionally rejected `DOD` -- the single most common
    acronym in the corpus -- along with `RFI` and `SECDEF`, in Tier 1 as well as
    Tier 3. Admitting the extra shape does not weaken the gate: it still
    requires the acronym to equal a structural function of the words, so a
    fabricated expansion fails exactly as before.
    """
    words = _significant_words(name)
    if not words:
        return set()

    variants = (
        _variants_from(words)
        | _variants_from(_all_words(name))
        # ...and the same words with hyphenated compounds kept whole. Admitting
        # the extra shape does not weaken the gate, by the same argument the
        # all-words list is admitted on: the acronym must still equal a
        # structural function of the words, so a fabricated expansion fails
        # exactly as before.
        | _variants_from(_compound_words(name))
    )

    # Lexicon-driven prefix abbreviations: "next generation" -> next-gen, ng, ...
    if lexicon:
        lowered = name.lower()
        for phrase, replacements in lexicon.items():
            if phrase in lowered:
                for replacement in replacements:
                    rewritten = lowered.replace(phrase, replacement)
                    for variant in acronym_variants(rewritten):
                        variants.add(variant)
                    variants.add(rewritten.strip())

    return {v for v in variants if len(v) >= 2}


# Composed from named fragments rather than written out four times -- the
# expansion and acronym shapes are identical across all four patterns, and
# keeping them in one place is what stops the four drifting apart.
_WORD = r"[\w'’\-]"
_PHRASE = rf"[A-Z]{_WORD}*(?:[ \-]{_WORD}+)"
_ACR = r"[A-Z][A-Za-z0-9\-]{1,9}"

_PARENTHETICAL_PATTERNS = [
    # "Next Generation Command and Control (NGC2)"
    re.compile(rf"(?P<expansion>{_PHRASE}{{0,9}})\s*\(\s*(?P<acronym>{_ACR})\s*\)"),
    # "NGC2 (Next Generation Command and Control)"
    re.compile(rf"(?P<acronym>{_ACR})\s*\(\s*(?P<expansion>{_PHRASE}{{1,9}})\s*\)"),
    # "NGC2, or Next Generation Command and Control,"
    re.compile(rf"(?P<acronym>{_ACR}),\s+or\s+(?P<expansion>{_PHRASE}{{1,9}})\s*[,.]"),
    # "Next Generation Command and Control, known as NGC2"
    re.compile(
        rf"(?P<expansion>{_PHRASE}{{1,9}}),\s+(?:known|referred to)\s+as\s+"
        rf"(?P<acronym>{_ACR})"
    ),
]


def verify_expansion(acronym: str, expansion: str, *, lexicon=None) -> bool:
    """Does `expansion` actually generate `acronym`?

    This is the gate that stops a fabricated parenthetical from becoming an
    alias and corrupting entity resolution downstream.

    Note what this deliberately does NOT do: it cannot tell
    "Next Generation Command and Control (NGC2)" from a hypothetical
    "Nebraska Grain Cooperative 2 (NGC2)", because both genuinely produce those
    initials. That is correct. Definitions are scoped to the document that
    grounded them -- if an article says the second thing, then in that article
    that is what NGC2 means. Disambiguation across documents is the
    `ambiguous_surfaces` mechanism's job, not this function's.
    """
    target = acronym.upper().replace("-", "")
    variants = {v.upper().replace("-", "") for v in acronym_variants(expansion, lexicon=lexicon)}
    return target in variants


def _clean_expansion(text: str) -> str:
    """Strip a trailing possessive.

    "Federal Emergency Management Agency's" and the plain name must not become
    two glossary rows for one organisation -- the apostrophe is grammar, not
    part of the name.
    """
    return re.sub(r"[\u2019']s$", "", text.strip()).strip()


def _best_verifying_window(acronym: str, phrase: str, *, lexicon=None) -> str | None:
    """Find the sub-phrase that actually defines the acronym.

    The regexes capture greedily leftwards, so "The Army's Next Generation
    Command and Control (NGC2)" hands us a phrase with two words of lead-in.
    Requiring the whole capture to verify would reject a perfectly good
    definition, so instead we walk trailing word windows longest-first and take
    the first that verifies. Longest-first matters: it prefers the fullest
    correct expansion over a shorter accidental match.
    """
    words = phrase.split()
    if not words:
        return None
    # An acronym of N letters needs at least N significant words, so windows
    # shorter than that cannot verify -- start from the longest.
    for start in range(len(words)):
        # A window may not begin with an article or determiner: those are
        # skipped by acronym_variants() as stopwords, so "a Modular Open
        # Systems Approach" verifies just as well as the real name and would
        # otherwise win by being longer.
        if words[start].lower() in _LEADING_NOISE:
            continue
        candidate = _clean_expansion(" ".join(words[start:]))
        if verify_expansion(acronym, candidate, lexicon=lexicon):
            return candidate
    return None


def find_parentheticals(
    text: str, *, lexicon=None, verified_only: bool = True
) -> list[tuple[str, str, int, int]]:
    """Return (acronym, expansion, start, end) for in-article definitions.

    Verification happens HERE rather than in the caller, so there is no way to
    obtain an unverified pairing by accident. `verified_only=False` exists only
    for tests that need to see what was rejected.
    """
    found: list[tuple[str, str, int, int]] = []
    seen: set[str] = set()

    for pattern in _PARENTHETICAL_PATTERNS:
        for m in pattern.finditer(text):
            acronym = m.group("acronym").strip()
            if not looks_like_acronym(acronym) or acronym in seen:
                continue

            phrase = m.group("expansion").strip()
            expansion = _best_verifying_window(acronym, phrase, lexicon=lexicon)

            if expansion is not None and is_numbering_scheme(acronym, expansion):
                expansion = None

            if expansion is None:
                if not verified_only:
                    found.append((acronym, phrase, m.start("expansion"), m.end("expansion")))
                continue

            # Re-anchor the span onto the verified window, not the greedy capture,
            # so the stored offsets point at the real definition.
            offset = m.group("expansion").find(expansion)
            start = m.start("expansion") + (offset if offset >= 0 else 0)
            seen.add(acronym)
            found.append((acronym, expansion, start, start + len(expansion)))

    return found
