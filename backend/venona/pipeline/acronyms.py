"""Acronym grounding. Invariant 3: grounded or absent, never guessed.

The defence press is dense with acronyms that are not entities -- MOSA, TRL,
OTA, DDIL. The reader needs them defined on hover; the system must never invent
a definition, because a wrong expansion becomes an alias and corrupts entity
resolution downstream.

Four tiers, cheapest first. **Every tier either produces provenance or produces
nothing.**

  1. in_article  (1.0)      parenthetical in THIS document, verified by initials.
  2. in_corpus   (0.9)      Tier-1 grounded in a different ingested document.
  3. web_grounded(0.55-0.85) the only tier that leaves the corpus. See below.
  4. undefined   (0.0)      renders as visibly undefined, with a look-up button.

Tier 3 is deliberately not "ask the model what it means". Measured 2026-08-06:
`WebFetch` hands the model a sub-model *summary*, not page text, so a "verbatim
quote" it returns is not in the source and re-fetch verification rejects even
correct answers. So the flow is:

    model returns candidate URLs  ->  WE fetch and extract  ->
    second call with NO tools quotes from OUR text  ->  we verify the substring

The model is a search tool and a reader over text we control. It is never a
source of facts.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from venona.db import core as db
from venona.db.models import (
    STOPWORDS,
    find_parentheticals,
    glossary_id,
    looks_like_acronym,
    normalize,
    slugify,
    verify_expansion,
)
from venona.db.repositories import pipeline_acronyms as repo
from venona.llm import budget as budget_mod
from venona.llm.executor import PermissionDenied
from venona.llm.prompts import (
    acronym_adjudicate_system,
    acronym_read_system,
    acronym_search_system,
)
from venona.llm.runner import call_and_record
from venona.llm.schemas import (
    acronym_adjudicate_schema,
    acronym_read_schema,
    acronym_search_schema,
)
from venona.pipeline import web_grounding
from venona.pipeline.metrics import Funnel
from venona.pipeline.resolve import _days_before
from venona.pipeline.types import RunContext
from venona.services import alias_index
from venona.untrust import inline, quote

# Acronym-shaped tokens: NGC2, C4ISR, MQ-9, DDIL.
ACRONYM_TOKEN = re.compile(r"\b[A-Z][A-Z0-9]{1,7}(?:-[A-Z0-9]{1,4})?\b")

# The mechanical limits of one tier-3 lookup live in `web_grounding`, which owns
# the loop that reads them; two copies of a limit is how the two lookups came to
# differ. Re-exported because `tests/test_acronyms_grounding.py` reads
# `READS_PER_LOOKUP` from here. Tier-3 GATING is separate and lives in
# `config/settings.yaml` (`pipeline.acronym_*`) -- shadowing those values here
# makes editing the config do nothing.
CANDIDATES_PER_ROUND = web_grounding.CANDIDATES_PER_ROUND
MAX_FETCH_CHARS = web_grounding.MAX_FETCH_CHARS
MAX_SUBJECT_CHARS = web_grounding.MAX_SUBJECT_CHARS
READS_PER_LOOKUP = web_grounding.READS_PER_LOOKUP


@dataclass
class AcronymReport:
    surfaces_seen: int = 0
    # Documents read THIS run -- after the first run, the new ones only.
    # Reported because `surfaces_seen` counts the whole corpus, so without it
    # the stage reads as though every document were re-read every night.
    documents_scanned: int = 0
    grounded_in_article: int = 0
    # Skipped because the alias index already resolves them. An outcome, not a
    # non-event: without it a surface vanishes between `surfaces_seen` and the
    # tiers with nothing to say so, which is how the VICTOR miss stayed hidden.
    resolved_by_entity: int = 0
    grounded_in_corpus: int = 0
    grounded_web: int = 0
    undefined: int = 0
    rejected: int = 0
    lookups_attempted: int = 0
    lookups_deferred: int = 0
    warnings: list[str] = field(default_factory=list)
    # Surfaced, but not status-affecting. Folding two readings together, or
    # releasing a surface that has stopped being ambiguous, is this stage DOING
    # ITS JOB; through `warn` it reported `succeeded_partial` most nights on a
    # healthy corpus, and a status that says "degraded" on every row is ignored.
    # ADR 0194.
    notices: list[str] = field(default_factory=list)
    # Work waiting on a PERSON: it re-fires every run until somebody rules on
    # it, which is what a curation queue is. Drained through `ctx.todo`.
    todos: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if isinstance(v, int)}

    def reconciles(self) -> bool:
        """`surfaces_seen` partitions into exactly these four, and nothing else.

        Adding up every field of `run_stages.counts` that looks like an outcome
        is wrong three ways: `grounded_in_article` counts EVENTS and so
        double-counts, `rejected` is a SUBSET of `undefined`, and the
        `lookups_*` pair are diagnostics. The conclusion "the counters are
        noisy" is how a surface going missing stays invisible.
        """
        return self.surfaces_seen == (
            self.resolved_by_entity + self.grounded_in_corpus + self.grounded_web + self.undefined
        )


def load_lexicon(settings) -> tuple[dict[str, list[str]], set[str], set[str]]:
    """Expansions, stoplist and short-whitelist from `profile/lexicon.yaml`."""
    import yaml

    path = settings.profile_dir / "lexicon.yaml"
    if not path.exists():
        return {}, set(), set()
    raw = yaml.safe_load(path.read_text()) or {}
    return (
        raw.get("expansions") or {},
        {s.upper() for s in (raw.get("stoplist") or [])},
        {s.upper() for s in (raw.get("short_whitelist") or [])},
    )


def load_non_distinguishing(settings) -> set[str]:
    """Tokens that do not distinguish one organisation from another.

    Read separately from `load_lexicon` because the resolver needs it and the
    acronym stage does not; returning a 4-tuple would have made every caller
    unpack a value it ignores.
    """
    import yaml

    path = settings.profile_dir / "lexicon.yaml"
    if not path.exists():
        return set()
    raw = yaml.safe_load(path.read_text()) or {}
    return {str(t).lower() for t in (raw.get("non_distinguishing") or [])}


def parse_merge_tokens(raw: dict) -> tuple[set[str], set[str]]:
    """The two merge lexicons out of an already-loaded lexicon document.

    Split from the loader below because the document is a row (ADR 0234) and a
    reader auditing a remote corpus has the content without having the file --
    `scripts/graph_health.py` reads the row off the connection it was pointed
    at, so that the vocabulary and the entities come from the same instance.
    """
    return (
        {str(t).lower() for t in (raw.get("corporate_form") or [])},
        {str(t).lower() for t in (raw.get("generic_tails") or [])},
    )


def load_merge_tokens(settings) -> tuple[set[str], set[str]]:
    """`(corporate_form, generic_tails)` -- the curator's two merge lexicons.

    A separate loader for the same reason as the one above: a different
    consumer. The classifier reads these to decide two names are one thing; the
    resolver reads that one to decide whether to look further before minting,
    and blurring them costs the distinction the design depends on -- the
    resolver stays paranoid, the curator gets to be clever.
    """
    import yaml

    path = settings.profile_dir / "lexicon.yaml"
    if not path.exists():
        return set(), set()
    return parse_merge_tokens(yaml.safe_load(path.read_text()) or {})


def load_person_tokens(settings) -> tuple[set[str], set[str]]:
    """`(person_titles, person_suffixes)` -- what a person's name carries that is
    not their name.

    A third loader, again for a different consumer. These cannot share a token
    list with the merge lexicons: `chief` is a rank on a person and part of the
    name of a thing.
    """
    import yaml

    path = settings.profile_dir / "lexicon.yaml"
    if not path.exists():
        return set(), set()
    raw = yaml.safe_load(path.read_text()) or {}
    return (
        {str(t).lower() for t in (raw.get("person_titles") or [])},
        {str(t).lower() for t in (raw.get("person_suffixes") or [])},
    )


def ordinary_words(text: str) -> set[str]:
    """Words appearing as ORDINARY words in `text`, upper-cased.

    `ACRONYM_TOKEN` is a shape regex, so every capitalised word in a dateline or
    a headline matches it. Measured 2026-08-06 over the live corpus, 46 surfaces
    cleared the tier-3 gate and roughly a third were not acronyms at all:

        LONDON  PARIS  WARSAW  DAYTON      wire-service datelines
        HAVE  AIR  HEAT  ATLAS  VISTA      headline capitals

    Each would have spent a WebSearch and a WebFetch defining an English word.
    The corpus answers this without a wordlist: a token that also appears as
    `London` or `have` is a word being shouted.

    Title-case and lower-case only. Mixed case is deliberately NOT evidence --
    `DoD` is a styling of an acronym, and counting it would suppress `DOD`.

    Accumulated over every analysed document rather than one at a time, because
    the evidence is usually elsewhere: a story datelined `WARSAW —` rarely also
    says `Warsaw`, but another story that night does.

    **It counts DOCUMENTS, and one is not enough** (`acronym_ordinary_min_docs`).
    The claim is about RECURRENCE: as a set, a single title-cased word anywhere
    kills the shouted form everywhere, forever, and a Haitian ex-parliamentarian
    named `Prophane Victor` is why the Army's `VICTOR` programme was never once
    detected.
    """
    return {
        word.upper()
        for word in re.findall(r"\b[A-Za-z][A-Za-z-]*\b", text)
        if word.istitle() or word.islower()
    }


def detect(
    text: str,
    *,
    stoplist: set[str],
    short_whitelist: set[str],
    ordinary: set[str] | None = None,
) -> dict[str, int]:
    """Acronym-shaped tokens and how often each appears.

    `ordinary` is the corpus-wide shouted-word set; it defaults to this document
    alone, which is right for a single-document caller and merely weaker.
    """
    ordinary = ordinary_words(text) if ordinary is None else ordinary
    counts: dict[str, int] = {}
    for match in ACRONYM_TOKEN.finditer(text):
        token = match.group(0)
        if token in stoplist:
            continue
        if len(token) < 3 and token not in short_whitelist:
            continue
        if not looks_like_acronym(token):
            continue
        if token in ordinary:
            continue
        counts[token] = counts.get(token, 0) + 1
    return counts


# ── tier 1: in-article ───────────────────────────────────────────────


def ground_in_article(
    conn: db.Conn,
    *,
    article_id: str,
    text: str,
    lexicon: dict,
    now: str,
) -> list[tuple[str, str]]:
    """Parentheticals in THIS document, verified by initials.

    `find_parentheticals` already rejects a pairing whose expansion does not
    generate the acronym -- a fabricated "Widget Program (NGC2)" never gets out.
    """
    grounded: list[tuple[str, str]] = []

    for acronym, expansion, start, end in find_parentheticals(text, lexicon=lexicon):
        sentence = _sentence_around(text, start, end)
        store_definition(
            conn,
            surface=acronym,
            expansion=expansion,
            method="in_article",
            confidence=1.0,
            article_id=article_id,
            quote=sentence,
            span=[start, end],
            now=now,
        )
        grounded.append((acronym, expansion))

    return grounded


def _sentence_around(text: str, start: int, end: int) -> str:
    """The sentence containing the definition -- that is the evidence."""
    left = max(text.rfind(".", 0, start), text.rfind("\n", 0, start)) + 1
    right = text.find(".", end)
    right = len(text) if right == -1 else right + 1
    return text[left:right].strip()[:1000]


# ── tier 2: in-corpus ────────────────────────────────────────────────


def known_definition(conn: db.Conn, surface: str) -> db.Row | None:
    """An accepted definition from any already-ingested article.

    Returns the row that ACTUALLY grounded it: provenance points at the document
    where the parenthetical appeared, not at the article under analysis now.
    """
    return repo.select_acronym_definitions_by_normalized(conn, normalize(surface)).fetchone()


def undefined_candidates(
    conn: db.Conn,
    *,
    retry: bool = False,
    cap: int | None = None,
) -> list[db.Row]:
    """Every surface a bulk tier-3 sweep would attempt, most-seen first.

    The `lookup_all` queue, as a list. Split out of the endpoint so the modal
    quoting a price and the run spending it ask ONE question -- a preview that
    counted differently would misprice it.

    Three exclusions, each of which is money:

      * **`status = 'undefined'` only.** `rejected` is a human verdict; paying to
        overturn it is the mistake `decided_at` exists to stop.
      * **Nothing whose surface is already grounded.** `mark_undefined` will not
        write an undefined row when a definition exists, but the two are separate
        rows keyed `(normalized, expansion)` and only the JOIN proves it -- and
        this sweep would pay for all of them at once.
      * **Nothing already attempted, unless `retry`.** A tier-3 lookup that
        grounded nothing rejected every candidate it found; running it again over
        the same web costs the same and fails the same way. `retry=True` is for
        after new sources land.

    Ordered by sighting count so a cap drops the surfaces a reader is least
    likely to meet, `surface` breaking ties -- determinism, not taste.
    """
    rows = repo.undefined_backlog(conn, retry=retry)
    return rows[:cap] if cap is not None else rows


# ── tier 4: undefined ────────────────────────────────────────────────


def mark_undefined(
    conn: db.Conn,
    surface: str,
    *,
    now: str,
    sightings: int,
    articles: int = 1,
    attempted_run_id: str | None = None,
) -> None:
    """Visibly undefined beats confidently wrong -- and it surfaces a gap worth
    curating.

    `attempted_run_id` records that a tier-3 web lookup RAN and grounded
    nothing, which is different from never having been tried; pass it only from
    a real lookup.

    Pass the REAL `articles` count: the review queue orders by it, so hardcoding
    1 buried `RFI` (5 articles) among 295 genuine one-offs (2026-08-18). Tier-3
    gating reads the live count, not this column.
    """
    entry_id = glossary_id(surface)
    # Any grounded row for this SURFACE wins, whatever its id or status --
    # keying on `id == active` alone misses the ambiguous case and rows stored
    # under a suffixed id.
    grounded = repo.select_acronym_definitions_by_normalized_2(conn, normalize(surface)).fetchone()
    if grounded:
        return

    # Only a real lookup stamps an attempt. The stage also calls this for every
    # surface it merely saw, and counting those would tell the card a web lookup
    # had been tried when none had -- the confusion this column exists to remove.
    if attempted_run_id is not None:
        repo.upsert_undefined_attempt(
            conn,
            entry_id=entry_id,
            surface=surface,
            normalized=normalize(surface),
            sighting_count=sightings,
            article_count=articles,
            now=now,
            run_id=attempted_run_id,
        )
        return

    db.upsert(
        conn,
        "acronym_definitions",
        {
            "id": entry_id,
            "surface": surface,
            "normalized": normalize(surface),
            "expansion": None,
            "method": "undefined",
            "confidence": 0.0,
            "status": "undefined",
            "sighting_count": sightings,
            "article_count": articles,
            "first_seen": now,
            "last_seen": now,
        },
        key=["id"],
    )


# ── storage ──────────────────────────────────────────────────────────

# Evidence quality, strongest first: a re-grounding may only move a row UP it.
# Without the rank, a later `web_grounded` lookup that AGREED with a tier-1
# parenthetical rewrites `method` and `confidence`, recording weaker provenance
# because more evidence arrived.
TIER_RANK: dict[str, int] = {
    "manual": 4,
    "in_article": 3,
    "in_corpus": 2,
    "web_grounded": 1,
    "undefined": 0,
}

# What one web page buys, and what agreement adds. Tier 3 passes four
# independent checks -- our own fetch, the quote in our text, the acronym in the
# quote, the initials -- but it is still ONE page asserting something, so the
# base says a provisional reading further documents can confirm. `confidence.py`
# carries why the acronym and entity scales differ.
WEB_BASE_CONFIDENCE = 0.55
WEB_CORROBORATION_STEP = 0.1
# Below `in_corpus`'s 0.9 on purpose: a page agreed with three times is still
# weaker evidence than a sentence in the corpus we ingested and can re-read.
WEB_CONFIDENCE_CAP = 0.85


def confidence_for(method: str, corroboration: int, *, declared: float) -> float:
    """The confidence a row should carry, given its tier and its evidence count.

    Only tier 3 is computed. A parenthetical either checks out against the
    initials or it does not, so a second one saying the same thing adds nothing;
    a web page is one publisher's assertion, and independent agreement is what
    makes it more credible.
    """
    if method != "web_grounded":
        return declared
    steps = max(0, corroboration - 1)
    return min(WEB_CONFIDENCE_CAP, round(WEB_BASE_CONFIDENCE + WEB_CORROBORATION_STEP * steps, 2))


def contradicted_by_corpus(
    conn: db.Conn,
    surface: str,
    expansion: str,
    *,
    documents: list[tuple[str, str]] | None = None,
) -> bool:
    """Does OUR corpus say this expansion is the wrong reading of this surface?

    The relevance guard, and it asks a narrower question than "is this term in
    our domain". A correct definition of something the domain does not care
    about costs a reader nothing; a confident WRONG definition becomes an alias
    and corrupts entity resolution, which is the harm invariant 3 exists for.

    So the test is `Support.contradicted`, which needs both halves: the surface
    is the head of a name the corpus uses, AND the corpus never once spells this
    expansion out. ADR 0094 (2026-08-08) holds the two live cases it was built
    on, `BAE` and `FCC`.

    An expansion the corpus is merely SILENT about is not refused. `EMEA` and
    `TOE` both grounded correctly at zero corpus sources, and a guard that
    treated silence as contradiction would have thrown away both.
    """
    from venona.services import corpus_check

    return corpus_check.support(conn, surface, expansion, documents=documents).contradicted


def corroborate_from_corpus(
    conn: db.Conn,
    surface: str,
    expansion: str,
    *,
    now: str,
    documents: list[tuple[str, str]] | None = None,
) -> int:
    """Count documents that spell this expansion out, as corroborating sources.

    A tier-3 answer plus a document of ours saying the same thing is the
    "independent agreement" the confidence ladder was built for -- free,
    deterministic, already in `documents`. `FCC` = "Fire Control Center" gets
    nothing (0 of 4 documents); `PC-C6` = "Project Convergence – Capstone 6" gets
    17. That difference is the signal a flat number cannot express.

    Returns how many NEW sources were added. Idempotent: the document ids join
    the row's source list, so running it twice does not raise confidence twice.

    **It does NOT go through `store_definition`, and it does not raise the
    tier.** `store_definition` would overwrite `grounding_quote` and
    `grounding_url` -- the real sentence on the real page that passed four
    checks -- with whatever this weaker check had to hand; and this is not tier
    2, which means a parenthetical checked against the initials, where this only
    knows the phrase occurs in a document that also uses the letters. So it adds
    SOURCES and recomputes confidence on the row's existing tier.
    """
    from venona.services import corpus_check

    row = repo.select_acronym_definitions_by_normalized_expansion(
        conn, normalize(surface), expansion
    ).fetchone()
    if row is None:
        return 0
    # A human verdict outranks the evidence, here as everywhere else.
    if row["decided_at"] and row["status"] == "rejected":
        return 0

    # `documents` is threaded through so a caller checking a hundred surfaces
    # reads the corpus ONCE rather than running a full scan per surface.
    found = corpus_check.support(conn, surface, expansion, documents=documents)
    sources = [s for s in db.jload(row["corroborating_sources"], []) if s]
    added = [doc_id for doc_id in found.phrase_documents if doc_id not in sources]
    if not added:
        return 0

    sources.extend(added)
    repo.update_acronym_definitions_by_corroborating_sources_corrobor(
        conn,
        db.jdump(sources),
        len(sources),
        confidence_for(row["method"], len(sources), declared=row["confidence"]),
        now,
        row["id"],
    )
    return len(added)


def _source_key(article_id: str | None, url: str | None) -> str | None:
    """What counts as ONE source of evidence.

    The article id when we hold the document, else the URL. Not both: tier 1
    grounds from `documents.id` and tier 3 from a page, and they are answering
    the same question -- "is this evidence we have already counted?"
    """
    return article_id or url or None


def expansion_key(expansion: str) -> str:
    """Comparison form for "is this the SAME expansion, said differently".

    `normalize` alone is the wrong tool: built for alias keys, it preserves the
    ways two expansions of one thing routinely differ:

        Modular Open Systems Approach  /  Modular Open System Approach
        and the Defense Advanced …     /  Defense Advanced …

    Both were live. An ambiguous surface is not annotated AT ALL until a curator
    settles it, so a single `s` silently removed every `MOSA` underline in the
    app and the extension and broke nine extension smoke checks (2026-08-18).

    ONE fold on top of `normalize`: stopwords, which is what makes `and the
    Defense Advanced …` one reading with a sentence fragment glued on.

    **Plurals are deliberately NOT folded.** `definition_queue` already proposes
    the `same_thing` verdict for readings differing only in spelling
    (`test_spelling_variants_are_proposed_as_one_meaning` pins it on `CRADA`), so
    folding here settles by machine what that flow exists to have settled by a
    person. What it must NOT fold is a real difference: `Defense Intelligence
    Agency` against `Defense Investment Agency` is the `DIA` case the ambiguity
    machinery exists for.
    """
    return " ".join(w for w in normalize(expansion).split() if w not in STOPWORDS)


def store_definition(
    conn: db.Conn,
    *,
    surface: str,
    expansion: str,
    method: str,
    confidence: float | None = None,
    article_id: str | None,
    quote: str,
    span: list[int] | None = None,
    url: str | None = None,
    now: str,
) -> str:
    """Write a grounded definition.

    `confidence` is REQUIRED for tiers 1 and 2 and must be OMITTED for tier 3.
    `confidence_for` computes the web-grounded number from the corroboration
    count and ignores anything declared, so a tier-3 caller passing one states a
    fact that is discarded.

    Optional rather than removed: an `in_article` expansion's 1.0 is a real
    decision its caller makes, and the check below means neither tier gets it
    wrong silently.

    A surface that acquires a SECOND accepted expansion becomes ambiguous: both
    are kept, neither is auto-applied, and the hover card shows a disambiguation
    list. Picking one would be a silent, unrecoverable error.
    """
    key = normalize(surface)
    suffix = slugify(expansion)
    base_id = glossary_id(surface)

    # Only a genuinely DIFFERENT meaning creates ambiguity: re-grounding the
    # same expansion from a second article is corroboration, and treating that
    # as a conflict would mark every repeated acronym ambiguous and stop the
    # annotator matching it. Rejected rows are deliberately still counted -- a
    # curator rejecting one reading does not certify the other
    # (`test_a_human_rejection_survives_a_second_expansion`).
    siblings = [
        row
        for row in repo.select_acronym_definitions_by_normalized_3(conn, key)
        if expansion_key(row["expansion"]) != expansion_key(expansion)
    ]

    ambiguous = bool(siblings)
    # Reuse the existing row for this expansion if there is one, so a repeat
    # sighting updates rather than duplicating.
    existing = repo.select_acronym_definitions_by_normalized_expansion_2(
        conn, key, expansion
    ).fetchone()
    entry_id = existing["id"] if existing else (f"{base_id}--{suffix}" if ambiguous else base_id)

    # Two spellings of ONE reading land on one row, and last-write-wins is the
    # wrong answer: the parenthetical finder yields both `Modular Open Systems
    # Approach` and `on a Modular Open Systems Approach` from one sentence, so
    # the fragment would overwrite the clean form purely by arriving second.
    # Shortest wins, the tie-break `curate.identical_pairs` uses for a merge
    # survivor -- the extra words are the glue, never the name.
    #
    # The id and the expansion MUST come from the same row. Taken from different
    # ones, the upsert writes the shortest expansion onto the LAST row and
    # collides under `UNIQUE (normalized, expansion)` -- a hard `IntegrityError`
    # that kills the stage.
    if not existing and not ambiguous:
        equivalent = [
            row
            for row in repo.select_acronym_definitions_by_normalized_5(conn, key)
            if expansion_key(row["expansion"]) == expansion_key(expansion)
        ]
        if equivalent:
            # Tie broken by id, not row order: `Artificial Intelligence` and
            # `artificial intelligence` are both 23 characters, and the
            # database's order is the last-write-wins this block exists to remove.
            winner = min(equivalent, key=lambda row: (len(row["expansion"]), row["id"]))
            entry_id = winner["id"]
            if len(winner["expansion"]) <= len(expansion):
                expansion = winner["expansion"]

    if ambiguous:
        # Same exemption the upsert below applies: this blanket update reaches
        # every row for the surface, so without it a second expansion turns a
        # definition a person REJECTED back into a live `ambiguous` one.
        repo.update_acronym_definitions_by_normalized(conn, key)
        db.upsert(
            conn,
            "ambiguous_surfaces",
            {
                "normalized": key,
                "reason": f"{len(siblings) + 1} accepted expansions",
                "created_at": now,
            },
            key=["normalized"],
        )

    # Counts are EVIDENCE, not bookkeeping: the review queue orders by them, so
    # overwriting with 1 buries a freshly grounded acronym under every
    # undefined one. Grounding an acronym does not un-see it 11 times.
    prior = repo.select_acronym_definitions_by_normalized_4(conn, key).fetchone()

    # A HUMAN VERDICT OUTRANKS RE-GROUNDING. This upsert keys on `id`, so
    # writing `status` unconditionally flips a surface someone rejected back to
    # `active` and into briefs on the next nightly run, with the curator's only
    # recourse being to reject it again, every night, forever. `decided_at`
    # records that a person ruled: when it is set and the verdict was
    # `rejected`, counts and provenance may be refreshed, the status may not.
    decided = repo.select_acronym_definitions_by_id(conn, entry_id).fetchone()
    human_rejected = bool(decided and decided["decided_at"] and decided["status"] == "rejected")

    # ── evidence accumulates; it does not overwrite ──────────────────
    #
    # Re-grounding the same expansion from a source NOT already counted is
    # corroboration; writing a constant `confidence` over the top throws it away.
    incoming = _source_key(article_id, url)
    sources: list[str] = []
    if decided is not None:
        stored = db.jload(decided["corroborating_sources"], [])
        if stored:
            sources = [s for s in stored if s]
        else:
            # A row with no recorded sources seeds from its own provenance:
            # starting empty counts evidence already recorded as though new.
            seed = _source_key(decided["grounding_article_id"], decided["grounding_url"])
            sources = [seed] if seed else []
    if incoming and incoming not in sources:
        sources.append(incoming)
    corroboration = max(1, len(sources))

    # A re-grounding may only move a row UP the evidence ladder. See TIER_RANK:
    # without this, more evidence could record weaker provenance.
    stored_method = decided["method"] if decided else None
    if stored_method and TIER_RANK.get(stored_method, 0) > TIER_RANK.get(method, 0):
        method_final = stored_method
        declared = decided["confidence"]
        # The stronger tier's own evidence stands: quote, URL and span. The SPAN
        # especially -- it points into the document that grounded this and is
        # the only one of the four not recoverable from the row, so letting the
        # upsert write NULL throws away tier 1's offsets the first time any
        # weaker source agrees with it.
        article_id = decided["grounding_article_id"]
        url = decided["grounding_url"]
        quote = decided["grounding_quote"]
        span = db.jload(decided["grounding_span"], None)
    else:
        method_final = method
        if confidence is None and method != "web_grounded":
            raise ValueError(
                f"store_definition(method={method!r}) requires an explicit confidence; "
                f"only web_grounded computes its own from the corroboration count"
            )
        declared = confidence

    db.upsert(
        conn,
        "acronym_definitions",
        {
            "id": entry_id,
            "surface": surface,
            "normalized": key,
            "expansion": expansion,
            "method": method_final,
            "confidence": confidence_for(method_final, corroboration, declared=declared),
            "corroboration_count": corroboration,
            "corroborating_sources": db.jdump(sources) if sources else None,
            "grounding_article_id": article_id,
            "grounding_url": url,
            "grounding_quote": quote,
            "grounding_span": db.jdump(span) if span else None,
            "status": "rejected" if human_rejected else ("ambiguous" if ambiguous else "active"),
            "sighting_count": max(1, (prior["s"] if prior else 0) or 0),
            "article_count": max(1, (prior["a"] if prior else 0) or 0),
            "first_seen": now,
            "last_seen": now,
        },
        key=["id"],
        # The counts came from a MAX read a few lines up, and the light
        # grounding lane can raise one between that read and this write -- so
        # the write may raise them and never lower them.
        accumulate=("sighting_count", "article_count", "corroboration_count"),
    )
    return entry_id


def link_to_entities(conn: db.Conn) -> int:
    """Attach a glossary entry to an entity when the expansion IS one.

    Jargon (MOSA, TRL, OTA) stays a glossary entry so the graph is not polluted
    with non-entities; a definition that names a real program becomes a link.
    """
    linked = 0
    for row in repo.select_acronym_definitions(conn).fetchall():
        match = repo.select_entities_by_canonical_key(conn, normalize(row["expansion"])).fetchone()
        if match:
            repo.update_acronym_definitions_by_entity_id_id(conn, match["id"], row["id"])
            linked += 1

    # An UNDEFINED row whose surface has since become an entity steps aside.
    # `build_index` admits glossary rows only `WHERE entity_id IS NULL`, but
    # that guard keys on the expansion and an undefined row has none -- so
    # provisional -> undefined glossary row -> promoted to active leaves two
    # index entries for one surface, tied on every rank, and `_resolve_overlaps`
    # breaks the tie by insertion order. VICTOR is exactly this sequence.
    # `expansion IS NULL` keeps this off every grounded definition and every
    # human verdict; the row is preserved but stops annotating.
    claimed = repo.update_acronym_definitions(conn).rowcount
    return linked + max(claimed, 0)


# ── tier 3: grounded web lookup ──────────────────────────────────────


class AcronymGrounding(web_grounding.WebGrounding):
    """The acronym half of tier 3: the prompts, and four independent checks.

      1. the quote is a substring of text WE fetched and extracted
      2. the quote contains the expansion
      3. the expansion's initials generate the acronym
      4. the URL is one the model actually proposed (the drive loop only ever
         fetches URLs a search round returned)

    The model never quotes itself. See the module docstring for why.
    """

    search_task = "acronym_search"
    read_task = "acronym_read"

    @property
    def search_system(self) -> str:
        return acronym_search_system()

    @property
    def read_system(self) -> str:
        return acronym_read_system()

    not_found_reason = "not_defined_in_page"

    def __init__(self, lexicon: dict) -> None:
        self.lexicon = lexicon

    def search_schema(self) -> dict:
        return acronym_search_schema()

    def read_schema(self) -> dict:
        return acronym_read_schema()

    def search_prompt(self, subject: str, *, known: list[str] | None) -> str:
        from venona.domain import get_domain

        prompt = (
            f"Find authoritative pages that define the acronym {subject!r} "
            f"{get_domain().search_context}."
        )
        if known:
            # Naming what we hold turns "define this" into "what ELSE does this
            # mean", the question a re-lookup is asking. Without it the same
            # search returns the same best page and the same expansion, and the
            # user who pressed "look again" is charged for a duplicate.
            #
            # ONE SENTENCE, deliberately. A four-clause instruction block here
            # made the model garble its own tool call, emitting `<parameter
            # name="url">` inside the JSON five times until the call died --
            # after 181 of 182 `acronym_search` calls had succeeded (2026-08-09).
            # This prompt sits under a system prompt that already says exactly
            # what to return, and piling instructions on top competes with it.
            already = ", ".join(sorted(set(known))[:4])
            prompt += (
                f"\n\n{subject} already has these meanings on file: {already}. "
                f"Find pages that define a DIFFERENT meaning of the same letters."
            )
        return prompt

    def retry_prompt(self, attempted: str) -> str:
        return (
            f"\n\nThese were already tried and did NOT work:\n{attempted}\n\n"
            f"Propose DIFFERENT pages on different hosts. `fetch_status` means the "
            f"server refused us, so that host is unlikely to work at all; "
            f"`not_defined_in_page` means the page was readable but never spells the "
            f"acronym out, so look for a page ABOUT the term rather than one using it."
        )

    def read_prompt(self, subject: str, extracted: str) -> str:
        # See `define.py::read_prompt` -- same shape, same reason (ADR 0166).
        return (
            f"ACRONYM: {inline(subject, limit=MAX_SUBJECT_CHARS)}\n\n"
            f"TEXT:\n{quote(extracted, kind='fetched-page', limit=None, keep_lines=True)}"
        )

    def answer_of(self, data: dict) -> str:
        return (data.get("expansion") or "").strip()

    def incomplete(self, answer: str) -> dict:
        return {"expansion": answer or None}

    def reject(
        self, subject: str, answer: str, quote: str, extracted: str
    ) -> web_grounding.Rejection | None:
        where = {"expansion": answer, "quote": quote[:200]}
        # (1) the quote must come from OUR text, not the model's memory
        if web_grounding.comparable(quote) not in web_grounding.comparable(extracted):
            return web_grounding.Rejection("quote_not_in_page", where)
        # (2) the quote must actually contain the expansion
        if web_grounding.comparable(answer) not in web_grounding.comparable(quote):
            return web_grounding.Rejection("expansion_not_in_quote", where)
        # (2b) ...and the acronym, or it is not evidence of a definition. See
        # `mentions_acronym`.
        if not mentions_acronym(quote, subject):
            return web_grounding.Rejection("acronym_not_in_quote", where)
        # (3) the initials must generate the acronym
        if not verify_expansion(subject, answer, lexicon=self.lexicon):
            return web_grounding.Rejection("initials_mismatch", where)
        return None

    #: Only the initials check. Gates 1 and 2 keep the model's memory out of the
    #: corpus and are not appealable by the model that produced the answer;
    #: gate 3 is a claim about how English builds a short form, and
    #: `acronym_variants` only knows one way.
    adjudicable = frozenset({"initials_mismatch"})

    async def adjudicate(self, ctx, subject, answer, sentence, *, budget):
        """Does `answer` derive `subject`, when the variant generator says no?

        `verify_expansion` takes first initials and cannot express any other
        construction, so it refuses every syllabic and clipped short form. The
        corpus paid for that six times over: six independent pages returned
        "Sinking Exercise" for SINKEX and all six were discarded, and MANPADS
        was bought three times for an answer it can never accept (2026-08-29).

        This does NOT put a model in the position invariant 3 keeps it out of:
        the definition still comes from a sentence on a page we fetched, still
        checked as our own text and still checked to contain both halves. What
        is asked here is narrower and independent of that. ADR 0178.
        """
        call = await call_and_record(
            ctx,
            "acronym_adjudicate",
            user_content=(
                f"ACRONYM: {inline(subject, limit=MAX_SUBJECT_CHARS)}\n"
                f"EXPANSION: {inline(answer, limit=MAX_SUBJECT_CHARS)}\n\n"
                f"SENTENCE:\n{quote(sentence, kind='fetched-page')}"
            ),
            schema=acronym_adjudicate_schema(),
            system_prompt=acronym_adjudicate_system(),
            model=ctx.settings.llm.acronym_model,
            effort="low",
            tools=(),  # no tools: it may only read what we supplied
            timeout=60,
            budget=budget,
        )
        if not call.ok:
            # A failed appeal is not an acquittal. The rejection stands, and
            # the caller records it with no verdict attached.
            return None
        data = call.data or {}
        return bool(data.get("derives")), (data.get("how") or "").strip()


async def lookup_web(
    ctx: RunContext,
    surface: str,
    *,
    lexicon: dict,
    trace: list[dict] | None = None,
    budget: budget_mod.Budget | None = None,
    collect: bool = False,
    known: list[str] | None = None,
) -> tuple[str, str, str] | list[tuple[str, str, str]] | None:
    """Returns (expansion, quote, url), or None.

    The drive loop -- retries, budget checks, fetch/extract, the trace -- is
    `web_grounding.ground`, shared with `define.py`. What is acronym-specific is
    `AcronymGrounding` above.
    """
    return await web_grounding.ground(
        ctx,
        surface,
        AcronymGrounding(lexicon),
        trace=trace,
        budget=budget,
        collect=collect,
        known=known,
    )


def mentions_acronym(quote: str, surface: str) -> bool:
    """Does `quote` actually contain the acronym it claims to define?

    Found live (2026-08-06): a widened candidate pool grounded `USC` on the
    quote "United States Code", a page heading containing no "USC" at all. It
    passed every other check -- a substring of the page, containing the
    expansion (it IS the expansion), initials generating the acronym. Invariant
    3 asks for a SENTENCE that defines the acronym, and a bare noun phrase is
    not one.

    Two patterns, because the letters alone are not the whole surface. The
    spaced form joins the alphanumerics with optional periods, so `U.S.C.`
    counts for `USC`, but it cannot match a surface carrying its own
    punctuation: `AMP-HEL` yields `A[.\\s]?M[.\\s]?P[.\\s]?H[.\\s]?E[.\\s]?L`,
    which the literal `AMP-HEL` fails, and the corpus rejected "the Army
    Multi-Purpose High Energy Laser (AMP-HEL) prototyping effort" on exactly
    that. So the surface as WRITTEN is tried first -- hyphenated and
    alphanumeric designations are most of the defence acronym space.

    Word boundaries are load-bearing on both: collapsing punctuation instead
    would find "usc" inside "bonus certificate".
    """
    letters = [c for c in surface if c.isalnum()]
    if not letters:
        return False
    patterns = (
        r"\b" + re.escape(surface.strip()) + r"\b",
        r"\b" + r"[.\s]?".join(re.escape(c) for c in letters) + r"\b",
    )
    return any(re.search(p, quote, re.IGNORECASE) for p in patterns)


# ── the stage ────────────────────────────────────────────────────────


def collapse_equivalent(conn: db.Conn) -> tuple[list[str], list[str]]:
    """Fold rows that say the same thing into one, keeping the shortest.

    `store_definition` picks a single winner per reading, so it cannot create
    these; 19 surfaces in the live corpus arrived here already split
    (2026-08-16) -- `gloss:ai` / `gloss:ai--artificial-intelligence`, and so on.

    Two of them in one automaton is the failure. `build_index` adds every
    glossary row under its surface, and `Automaton.add_word` on a repeated key
    silently REPLACES -- so which expansion a hover card shows comes down to the
    database's row order, and can be the sentence fragment. It also blocks
    `reconcile_ambiguity`, which must not release a surface while two rows for
    it are still live.

    Evidence is folded, never reset: counts take the maximum and corroborating
    sources are unioned, so collapsing cannot make a well-attested definition
    look freshly seen.

    Only INDEXED rows are eligible, and only groups where none carries a human
    verdict. `build_index` selects `status IN ('active','undefined','ambiguous')`,
    so a group whose extra rows are `rejected` never had a collision -- nine of
    the nineteen were that shape, and touching them would undo the settling. And
    where a curator HAS ruled on a still-indexed row, the clean expansion and the
    ruling can sit on different rows (`csis`), so rewriting or deleting one edits
    a verdict; those are reported instead.

    Returns `(collapsed, contested)`, the second being the surfaces a person
    still has to look at. A silent skip here reads exactly like "there was
    nothing to do".
    """
    collapsed: list[str] = []
    contested: list[str] = []
    groups: dict[tuple[str, str], list[db.Row]] = {}
    for row in repo.select_acronym_definitions_2(conn).fetchall():
        groups.setdefault((row["normalized"], expansion_key(row["expansion"])), []).append(row)

    for (key, _), group in groups.items():
        rows = [r for r in group if r["status"] in ("active", "undefined", "ambiguous")]
        if len(rows) < 2:
            continue
        if any(r["decided_at"] is not None for r in rows):
            contested.append(key)
            continue
        winner = min(rows, key=lambda r: (len(r["expansion"]), r["id"]))
        losers = [r for r in rows if r["id"] != winner["id"]]

        sources: set[str] = set()
        for r in rows:
            sources.update(s for s in db.jload(r["corroborating_sources"], []) if s)

        repo.update_acronym_definitions_by_sighting_count_article_count(
            conn,
            max(r["sighting_count"] for r in rows),
            max(r["article_count"] for r in rows),
            max(len(sources), max(r["corroboration_count"] for r in rows)),
            db.jdump(sorted(sources)),
            winner["id"],
        )
        repo.delete_acronym_definitions_by_id(conn, [(r["id"],) for r in losers])
        collapsed.append(key)
    return collapsed, contested


def reconcile_ambiguity(conn: db.Conn) -> list[str]:
    """Clear surfaces that have stopped being ambiguous under the current rule.

    Ambiguity is decided on WRITE, so a surface marked under a stricter rule
    stays marked for ever -- nothing re-examines it, and an ambiguous surface is
    not annotated at all. `MOSA` and `DARPA` were both sitting in that state
    when `expansion_key` widened (2026-08-16), with no way out but a curator.

    Idempotent and free: one query, no model calls. Returns the surfaces
    released, so the stage can report them rather than fixing things silently.

    Releases only what THIS rule can have marked. Two kinds of ambiguity are not
    derived from expansions at all and must survive:

    * **Editorial.** `fixtures/seed/entities.yaml`'s `ambiguous:` block declares
      surfaces like `TITAN`, `Sentinel` and `the Army` with a reason and no
      `acronym_definitions` rows whatsoever. Reading "no expansions" as "one
      meaning" deleted nine of the ten on the first run after `make seed`, and
      `resolve.py` reads this table as its ambiguity guard -- so the next
      assertion naming Titan quietly auto-resolved to one owner.
    * **Curatorial.** `POST /glossary/{surface}/keep-both` stamps `decided_at`
      with `status = 'ambiguous'`. Two readings that differ only by a stopword
      fold to one `expansion_key`, so without the check below a curator's
      explicit "these are genuinely different" is reversed on the next run.
    """
    released: list[str] = []
    for row in repo.select_ambiguous_surfaces(conn).fetchall():
        key = row["normalized"]
        keys = {
            expansion_key(r["expansion"])
            for r in repo.select_acronym_definitions_by_normalized_6(conn, key)
        }
        # Exactly one reading is the only releasable state. Zero means the
        # ambiguity was never about expansions -- see the docstring.
        if len(keys) != 1:
            continue
        decided = repo.select_acronym_definitions_by_normalized_7(conn, key).fetchone()
        if decided is not None:
            continue
        repo.delete_ambiguous_surfaces_by_normalized(conn, key)
        # Only the rows this rule demoted: `rejected` is excluded by the status
        # test itself, and a decided `ambiguous` one was skipped above.
        repo.update_acronym_definitions_by_normalized_2(conn, key)
        released.append(key)
    return released


async def run_acronyms(
    ctx: RunContext, *, allow_web: bool = True, budget: budget_mod.Budget | None = None
) -> AcronymReport:
    report = AcronymReport()
    conn = ctx.db
    now = ctx.clock.now()
    # Collapse BEFORE reconciling: releasing a surface whose duplicate rows are
    # still live would flip both to `active` and put two entries for one surface
    # into the automaton, where insertion order decides which the reader sees.
    folded, contested = collapse_equivalent(conn)
    for surface in folded:
        report.notices.append(f"{surface}: folded duplicate readings into one")
    for surface in contested:
        report.todos.append(
            f"{surface} has two readings that mean the same thing, and you have already "
            f"ruled on one of them"
        )
    # Before anything is grounded: a surface released here becomes annotatable
    # for the rest of this run rather than the next one.
    for surface in reconcile_ambiguity(conn):
        report.notices.append(f"{surface}: no longer ambiguous, released for annotation")
    lexicon, stoplist, short_whitelist = load_lexicon(ctx.settings)

    # Read at CALL time, never bound at import: a snapshot makes editing the
    # config silently do nothing.
    limits = ctx.settings.pipeline

    # Surfaces the alias index already resolves need no glossary entry. Ask the
    # index itself: `SELECT surface FROM aliases` is a different question, and
    # includes aliases on provisional entities the index does NOT match -- those
    # surfaces are skipped here AND absent there, so they are never grounded and
    # never annotated. See `alias_index._resolved_entity_aliases`.
    known_aliases = alias_index.indexed_entity_surfaces(conn)

    # ONLY WHAT HAS NOT BEEN READ. What a document says about a surface cannot
    # change once it has been read. The corpus-wide totals that DO change --
    # sighting counts, ordinary-word document frequencies -- are persisted
    # rather than recomputed, which is what makes the scan safe to scope.
    # Migration 0013.
    docs = repo.select_documents(conn).fetchall()
    report.documents_scanned = len(docs)

    # The ordinary-word frequencies are updated BEFORE anything is detected, so
    # tonight's documents can contribute to the judgement made about tonight's
    # documents. DOCUMENT FREQUENCY, not membership: `ordinary_words` returns a
    # set per document, so counting it gives how many documents use the token as
    # an ordinary word. See `ordinary_words` for why one is not enough.
    new_df: Counter[str] = Counter()
    for doc in docs:
        new_df.update(ordinary_words(doc["text_nfc"]))
    if new_df:
        repo.bump_ordinary_word_df(conn, [(word, n) for word, n in sorted(new_df.items())])
    corpus_words = {
        row["word"] for row in repo.select_ordinary_words(conn, limits.acronym_ordinary_min_docs)
    }

    fresh: dict[str, dict] = {}
    for doc in docs:
        ctx.check_cancel()
        text = doc["text_nfc"]

        for acronym, _expansion in ground_in_article(
            conn, article_id=doc["id"], text=text, lexicon=lexicon, now=now
        ):
            report.grounded_in_article += 1
            await ctx.counter("acronyms_grounded")
            fresh.setdefault(acronym, {"count": 0, "articles": set()})

        for token, count in detect(
            text, stoplist=stoplist, short_whitelist=short_whitelist, ordinary=corpus_words
        ).items():
            entry = fresh.setdefault(token, {"count": 0, "articles": set()})
            entry["count"] += count
            entry["articles"].add(doc["id"])

    if fresh:
        repo.bump_surface_sightings(
            conn,
            [
                (normalize(surface), surface, info["count"], len(info["articles"]), now, now)
                for surface, info in sorted(fresh.items())
            ],
        )

    # A TOKEN THAT HAS BECOME AN ORDINARY WORD MUST LEAVE. Without this a
    # scoped scan keeps counting sightings recorded back when the token still
    # looked like an acronym, and a surface `detect` suppresses today sits in
    # the totals forever on evidence gathered before the rule applied to it.
    # Document frequency only rises, so the correction runs one way.
    if corpus_words:
        retired = [
            (row["normalized"],)
            for row in repo.select_surface_sightings(conn)
            if row["surface"] in corpus_words
        ]
        if retired:
            repo.delete_surface_sightings(conn, retired)

    if docs:
        repo.mark_clusters_scanned(conn, [(now, doc["cluster_id"]) for doc in docs])

    # The corpus-wide totals, which is what every tier gate below reads. The
    # surfaces in `fresh` are a subset: a term first seen last week and not
    # mentioned tonight is still a candidate, and still has to be counted.
    seen: dict[str, dict] = {
        row["surface"]: {"count": row["sighting_count"], "articles": row["article_count"]}
        for row in repo.select_surface_sightings(conn)
    }

    report.surfaces_seen = len(seen)

    # Tier 2, then decide what is worth a web lookup.
    ungrounded: list[tuple[str, dict]] = []
    for surface, info in seen.items():
        if surface.upper() in known_aliases:
            report.resolved_by_entity += 1
            continue
        if known_definition(conn, surface):
            report.grounded_in_corpus += 1
            continue
        ungrounded.append((surface, info))

    # Tier 3, gated: repeated across articles, and capped per run. All three
    # thresholds come from `config/settings.yaml`, read through `limits` above.
    #
    # A surface a web lookup already failed to ground is not worth paying for
    # again tonight. Most are undefinable -- a typo, an OCR artefact, a string
    # that was never an acronym -- and they are the HIGHEST-sighted ungrounded
    # surfaces by construction, because sighting count is what selects them. So
    # they sort to the front every night, consume the whole per-run cap, and the
    # terms that arrived today never get looked at. A window rather than a
    # permanent refusal: some surfaces fail because the web has not caught up.
    attempted_recently = repo.recently_attempted(
        conn, _days_before(now, limits.acronym_retry_after_days)
    )
    candidates = sorted(
        (
            (s, i)
            for s, i in ungrounded
            if i["count"] >= limits.acronym_min_sightings
            and i["articles"] >= limits.acronym_min_articles
            and normalize(s) not in attempted_recently
        ),
        key=lambda pair: -pair[1]["count"],
    )[: limits.acronym_lookup_cap_per_run]

    looked_up: set[str] = set()
    # Surfaces a tier-3 lookup ran on and failed to ground. Distinct from
    # `ungrounded`, which is every surface the cheap tiers could not define and
    # is mostly things no lookup was ever paid for.
    attempted: set[str] = set()
    trace: list[dict] = []
    if allow_web:
        for index, (surface, _info) in enumerate(candidates, start=1):
            ctx.check_cancel()

            # Tier 3 spends against the RUN's allowance, not a private one.
            # `acronym_lookup_cap_per_run` caps how many surfaces are attempted;
            # each is a search plus up to three reads, so a full cap of 8 is up
            # to 32 calls, and unchecked they are spent after analyze has
            # already deferred work for being out of budget.
            if budget is not None:
                try:
                    budget.check()
                except budget_mod.BudgetExceeded:
                    remaining = len(candidates) - index + 1
                    report.lookups_deferred += remaining
                    report.warnings.append(budget.deferral(remaining, "terms to look up"))
                    break

            report.lookups_attempted += 1
            try:
                # `trace` is why a candidate URL was refused, in the named
                # reasons of `web_grounding.REASON_STAGE`. Aggregated below it
                # is the only evidence for whether tier 3 is failing on
                # paywalls, on initials mismatches, or on nothing being there.
                result = await lookup_web(ctx, surface, lexicon=lexicon, budget=budget, trace=trace)
            except PermissionDenied:
                # MUST propagate. `PermissionDenied` subclasses `LlmError`
                # subclasses `RuntimeError`, so a bare `except Exception` here
                # turns the one failure that must never be degraded into a
                # warning string. A denied tool means the answer was produced
                # without the capability it needed (contract C2).
                raise
            except Exception as exc:  # noqa: BLE001 - one dead page is not a run failure
                report.warnings.append(f"{surface}: lookup failed ({exc})")
                continue

            if result is None:
                report.rejected += 1
                # The lookup RAN and grounded nothing, which is a different fact
                # from never having been tried -- and it is the fact that stops
                # this surface consuming the cap again tomorrow night.
                attempted.add(surface)
                continue

            expansion, quote, url = result
            # Its own document scan, affordable HERE: this loop is capped at
            # `acronym_lookup_cap_per_run` (8), and each surface it reaches has
            # already paid for a web search, up to three fetches and four model
            # calls. The sweep is where a per-surface scan would matter, and it
            # threads the corpus instead.
            if contradicted_by_corpus(conn, surface, expansion):
                # A page said it and our own documents say otherwise. Not
                # stored, and recorded on the trace so the refusal is as
                # visible as an acceptance.
                trace.append({"url": url, "reason": "off_domain", "expansion": expansion})
                report.rejected += 1
                attempted.add(surface)
                continue
            store_definition(
                conn,
                surface=surface,
                expansion=expansion,
                method="web_grounded",
                article_id=None,
                quote=quote,
                url=url,
                now=now,
            )
            looked_up.add(surface)
            report.grounded_web += 1
            await ctx.counter("acronyms_grounded")

    # Tier 4.
    for surface, info in ungrounded:
        if surface in looked_up:
            continue
        mark_undefined(
            conn,
            surface,
            now=now,
            sightings=info["count"],
            articles=info["articles"],
            # Only for a surface a lookup actually ran on; see `mark_undefined`.
            attempted_run_id=ctx.run_id if surface in attempted else None,
        )
        report.undefined += 1
        await ctx.counter("acronyms_undefined")

    link_to_entities(conn)

    # `surfaces_seen` partitions into the four tier outcomes -- the invariant
    # `reconciles()` asserts, recorded as data so a violation shows up in the UI
    # and not only in a test that never runs against a live corpus.
    funnel = Funnel("acronyms", offered=report.surfaces_seen)
    funnel.keep(report.resolved_by_entity + report.grounded_in_corpus + report.grounded_web)
    funnel.drop("undefined", report.undefined)
    funnel.emit(ctx)
    ctx.metric("acronyms", "candidates", len(candidates))
    ctx.metric("acronyms", "lookups_attempted", report.lookups_attempted)
    ctx.metric("acronyms", "lookups_deferred", report.lookups_deferred)
    ctx.metric("acronyms", "lookups_rejected", report.rejected)
    ctx.metric("acronyms", "grounded_in_article", report.grounded_in_article)
    counts = Counter(str(entry.get("reason") or "unknown") for entry in trace)
    # An overturned check is an ACCEPTANCE. Filing it under `web_reject_` would
    # report the definitions adjudication recovers as a new kind of failure.
    overturned = counts.pop("adjudicated", 0)
    if overturned:
        ctx.metric("acronyms", "web_overturned", overturned)
    for reason, n in counts.items():
        ctx.metric("acronyms", f"web_reject_{reason}", n)

    for warning in report.warnings:
        ctx.warn(warning)
    # `contested` is the one of the three that needs a person, so it goes to
    # the channel that says so and links to where the ruling is made.
    for todo in report.todos:
        ctx.todo(todo, to="curation")
    for notice in report.notices:
        ctx.note(notice)
    return report
