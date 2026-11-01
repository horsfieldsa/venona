"""Does the corpus agree with the definition we bought?

Tier 3's four checks all ask the same question -- *does this page define these
letters this way* -- and a page can answer yes while being irrelevant to every
article we hold. Measured 2026-08-09 on this corpus:

  * `FCC` grounded as "Fire Control Center" from a Kongsberg product page. The
    phrase appears in **0 of the 4** documents that use FCC; "Federal
    Communications Commission" appears in one.
  * `BAE` grounded as "brigade aviation element" off a military dictionary. The
    phrase appears in **0 of 9**; `BAE` is followed by "Systems" in **16 of 16**
    occurrences.
  * `III` grounded as "Interstate Identification Index". 0 of 7 -- it is a
    Roman numeral.

Each passed every gate, because each is true about the letters. Nothing
compared the answer to the corpus the definition is for.

**The asymmetry is the whole design.** Presence of the phrase is strong
evidence; ABSENCE IS NOT EVIDENCE OF ANYTHING. Measured over 37 web-grounded
definitions, 22 had no occurrence of their expansion -- and that set includes
`USC` = United States Code, `AWACS`, `DVIDS`, `USV` and `CAD`, all correct. The
corpus does not spell out most acronyms; that is why tier 3 exists. So:

  * phrase present  -> corroboration, and the document id becomes a source
  * phrase absent   -> nothing said, nothing done
  * surface is the head of a NAME -> contradiction, worth a person's attention

The last is the only negative signal here, and it is deliberately narrow: a
surface that is almost always followed by the same capitalised word is not an
acronym in this corpus, it is the first token of a name. Over the same 37
definitions it fires on exactly one -- BAE -- with no false positives.

Nothing in this module deletes, rejects or rewrites a definition. It reports.
An incorrect auto-verdict here would be invisible in exactly the way the
definitions it is checking were.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import ahocorasick

from venona.db import core as db
from venona.db.repositories import corpus_check as repo

# How many uses of a surface before "always followed by X" means anything.
# Two mentions that happen to share a next word is a coincidence; the check is
# about a pattern, not an instance.
NAME_HEAD_MIN_USES = 3
# What share of those uses must share one capitalised follower. Not 1.0: a
# stray "BAE said" or a line break inside the name should not clear a company
# whose name appears sixteen times.
NAME_HEAD_SHARE = 0.8


@dataclass
class Support:
    """What the corpus has to say about one expansion of one surface."""

    surface: str
    expansion: str
    documents: int = 0
    """Documents that use the surface at all."""
    phrase_documents: list[str] = field(default_factory=list)
    """Documents that ALSO spell the expansion out -- corroboration."""
    name_head: str | None = None
    """The capitalised word that almost always follows the surface, if any."""
    name_head_share: float = 0.0

    @property
    def corroborated(self) -> bool:
        return bool(self.phrase_documents)

    @property
    def contradicted(self) -> bool:
        """The surface is the head of a name AND the corpus never spells this
        expansion out. Both halves are required: `DISA` is followed by nothing
        in particular and `PC-C6` is spelled out where it appears, and neither
        is in question."""
        return self.name_head is not None and not self.phrase_documents

    @property
    def verdict(self) -> str:
        if self.contradicted:
            return "contradicted"
        if self.corroborated:
            return "corroborated"
        return "unsupported"


def _normalise(text: str) -> str:
    """Case, whitespace and hyphenation are formatting, not meaning."""
    return re.sub(r"[\s‐-―-]+", " ", text).strip().lower()


def _name_head(result: Support, followers: dict[str, int], uses: int) -> None:
    if uses >= NAME_HEAD_MIN_USES and followers:
        word, count = max(followers.items(), key=lambda kv: kv[1])
        share = count / uses
        if share >= NAME_HEAD_SHARE:
            result.name_head = word
            result.name_head_share = round(share, 2)


def _weigh(
    surface: str,
    expansion: str,
    documents: Iterable[tuple[str, str]],
    normalised: Callable[[str, str], str],
) -> Support:
    """One expansion against the documents it could possibly be supported by.

    `documents` may be narrowed to the bodies that contain `surface` at all --
    see `_containing`. The regex below is still what decides a match, so the
    narrowing cannot change an answer; it only removes bodies that could not
    have matched.

    Case-sensitive on the surface: acronyms are upper-case, and lower-casing
    first is how an earlier version of this analysis matched nothing at all
    while reporting confidently that nothing was corroborated.
    """
    pattern = re.compile(r"\b" + re.escape(surface) + r"\b")
    needle = _normalise(expansion)

    result = Support(surface=surface, expansion=expansion)
    followers: dict[str, int] = {}
    uses = 0

    for doc_id, text in documents:
        if not pattern.search(text):
            continue
        result.documents += 1
        if needle and needle in normalised(doc_id, text):
            result.phrase_documents.append(doc_id)
        # The optional group CONSUMES the token after the surface, so `uses`
        # counts non-overlapping surface-plus-separator matches rather than
        # occurrences -- a second surface immediately after the first is eaten by
        # the first one's follower slot. `NAME_HEAD_SHARE` is a ratio over this
        # denominator, so counting occurrences instead moves the verdict.
        for match in re.finditer(pattern.pattern + r"[ \t]+([A-Z][a-zA-Z]+)?", text):
            uses += 1
            if match.group(1):
                followers[match.group(1)] = followers.get(match.group(1), 0) + 1

    _name_head(result, followers, uses)
    return result


def _normaliser() -> Callable[[str, str], str]:
    """`_normalise` memoised per document.

    The phrase test needs the folded body, and the fold is a regex sub over the
    whole text: shared across the definitions checked in one pass it runs once
    per body instead of once per body per definition.
    """
    cache: dict[str, str] = {}

    def normalised(doc_id: str, text: str) -> str:
        folded = cache.get(doc_id)
        if folded is None:
            folded = _normalise(text)
            cache[doc_id] = folded
        return folded

    return normalised


def _containing(documents: Sequence[tuple[str, str]], surfaces: set[str]) -> dict[str, list[int]]:
    """Which bodies mention each surface at all, in ONE pass over the corpus.

    Asking per definition means reading every body once per definition, and an
    audit checks hundreds against a whole corpus. One automaton over every
    surface answers for all of them in a single read.

    A raw substring hit is a CANDIDATE, never a verdict: `_weigh` still applies
    the word-boundary regex, which is what keeps `C2` out of `x_C2_y`. Indices
    rather than rows, so the caller keeps one copy of the text.
    """
    hits: dict[str, list[int]] = {surface: [] for surface in surfaces}
    if not surfaces:
        return hits

    automaton = ahocorasick.Automaton()
    for surface in surfaces:
        automaton.add_word(surface, surface)
    automaton.make_automaton()

    for index, (_doc_id, text) in enumerate(documents):
        for surface in {found for _end, found in automaton.iter(text)}:
            hits[surface].append(index)
    return hits


def support(
    conn: db.Conn,
    surface: str,
    expansion: str,
    *,
    documents: list[tuple[str, str]] | None = None,
) -> Support:
    """Weigh one expansion against every document that uses the surface.

    `documents` is injectable so a caller checking hundreds of definitions
    reads the corpus once rather than once per definition -- the scan is the
    expensive part and it is identical every time.
    """
    if documents is None:
        documents = [(row["id"], row["text_nfc"] or "") for row in repo.select_documents(conn)]
    return _weigh(surface, expansion, documents, _normaliser())


#: Finished audits, keyed per database and per method, each guarded by the
#: fingerprint of the inputs it was computed from. Keyed by `conn.dsn` and not
#: process-wide because the fingerprint alone cannot tell two databases apart:
#: the test suite seeds every throwaway database through a `FrozenClock`, so two
#: of them carry an IDENTICAL `(count, max-timestamp)` stamp, and a global memo
#: would serve one test's audit into another's database. Size-capped as a
#: backstop for that same suite, which opens hundreds of databases per process.
_AUDIT_CACHE: dict[tuple[str, str], tuple[tuple[Any, ...], list[Support]]] = {}
_AUDIT_CACHE_MAX = 8

#: Individual weighings, guarded by the CORPUS half of the stamp alone, because
#: that is all one of them depends on. See `audit` for why the two layers are
#: not one. Capped for the same reason as the audits above.
_SUPPORT_CACHE: dict[str, tuple[tuple[Any, ...], dict[tuple[str, str], Support]]] = {}
_SUPPORT_CACHE_MAX = 8


def _evict(cache: dict[Any, Any], key: Any, cap: int) -> None:
    if key not in cache and len(cache) >= cap:
        cache.pop(next(iter(cache)))


def audit_fingerprint(conn: db.Conn, *, method: str = "web_grounded") -> tuple[Any, ...]:
    """A cheap stamp over the corpus AND the definitions read against it.

    Shared, because anything that re-reads every body to compare it with every
    definition is asking the same question and can be skipped by the same
    answer. See `select_audit_fingerprint` for why both halves are needed.

    The five fields are a published shape: `POST /glossary/audit/corroborate`
    uses this as its own skip guard, so narrowing it sends that endpoint back
    through a full corpus sweep on every press.
    """
    stamp = repo.select_audit_fingerprint(conn, method).fetchone()
    return (
        stamp["docs"],
        stamp["newest_doc"],
        stamp["defs"],
        stamp["newest_seen"],
        stamp["newest_verdict"],
    )


def _weighings(dsn: str, corpus: tuple[Any, ...]) -> dict[tuple[str, str], Support]:
    """This database's weighings, emptied when the CORPUS moves."""
    held = _SUPPORT_CACHE.get(dsn)
    if held is not None and held[0] == corpus:
        return held[1]
    _evict(_SUPPORT_CACHE, dsn, _SUPPORT_CACHE_MAX)
    fresh: dict[tuple[str, str], Support] = {}
    _SUPPORT_CACHE[dsn] = (corpus, fresh)
    return fresh


def audit(conn: db.Conn, *, method: str = "web_grounded") -> list[Support]:
    """Every definition of a given tier, weighed against the corpus.

    Tier 3 by default, because it is the only tier whose evidence comes from
    outside the corpus and therefore the only one that can disagree with it. A
    tier-1 definition was read out of a document we hold; there is nothing to
    check.

    **Two memos, because there are two questions.** Which rows get audited is a
    fact about the definitions -- a verdict changes a status, so the LIST moves
    and `_AUDIT_CACHE` is stamped on all five fields. What the corpus says about
    one (surface, expansion) is a fact about the corpus alone: ruling on `FY`
    tells us nothing new about `BAE`, so the weighings are stamped on the corpus
    half and survive the verdict. Folding the two would mean a curator re-read
    every body in the corpus on each decision they made -- which is the one
    thing a review queue does over and over.

    The bodies are read only for pairs that miss, so a session of curating pays
    for the surfaces it grounds and nothing else.
    """
    fingerprint = audit_fingerprint(conn, method=method)
    key = (conn.dsn, method)
    cached = _AUDIT_CACHE.get(key)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]

    rows = repo.select_acronym_definitions_by_method(conn, method).fetchall()
    pairs = [(row["surface"], row["expansion"]) for row in rows]

    weighed = _weighings(conn.dsn, fingerprint[:2])
    missing = [pair for pair in dict.fromkeys(pairs) if pair not in weighed]
    if missing:
        documents = [(row["id"], row["text_nfc"] or "") for row in repo.select_documents(conn)]
        containing = _containing(documents, {surface for surface, _ in missing})
        normalised = _normaliser()
        for surface, expansion in missing:
            weighed[(surface, expansion)] = _weigh(
                surface,
                expansion,
                (documents[index] for index in containing[surface]),
                normalised,
            )

    results = [weighed[pair] for pair in pairs]

    _evict(_AUDIT_CACHE, key, _AUDIT_CACHE_MAX)
    _AUDIT_CACHE[key] = (fingerprint, results)
    return results
