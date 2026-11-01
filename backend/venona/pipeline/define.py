"""Entity definitions. Invariant 3, applied to entities: grounded or absent.

A reader hovering `NGC2` or `Anduril Industries` wants one line saying what the
thing IS. `entities.summary` is in the schema and nothing writes it, so the
material for that line comes from elsewhere: `analyze` asks the model for a
verbatim `context_sentence` per entity mention and stores it in
`assertions.evidence_quote` against an `article_id`. Measured 2026-08-07: every
entity had at least one, most exactly one.

Four tiers, cheapest first, and the structure mirrors `pipeline/acronyms.py` on
purpose -- the two are the same job on two kinds of reference, and reading one
should teach you the other. **Every tier either produces provenance or produces
nothing.**

  1. in_article  (1.0)  a sentence we already hold that DEFINES the entity
                        rather than merely mentioning it. Free, deterministic,
                        quoted verbatim with the article it came from.
  2. in_corpus   (0.7)  one call, no tools, over the evidence sentences we
                        hold. The model writes one sentence and names the
                        articles it drew from; we check every id it names is
                        one we actually gave it.
  3. web_grounded(0.5)  on demand only, never in a run -- see `lookup_web`.
  4. undefined   (0.0)  renders as visibly undefined, with a look-up button.

Tier 2 is a SYNTHESIS, not a quotation, and that is the one place this is looser
than the acronym pipeline. It is kept visibly distinct everywhere it surfaces:
stored with `source_article_ids` and no `grounding_quote`, and rendered as
"drawn from N articles" rather than as a quote. A synthesis presented as a quote
would be the exact failure invariant 3 exists to prevent.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

from venona.db import core as db
from venona.db.repositories import pipeline_define as repo
from venona.llm import budget as budget_mod
from venona.llm.prompts import (
    define_extract_system,
    define_read_system,
    define_search_system,
    define_write_system,
)
from venona.llm.runner import call_and_record
from venona.llm.schemas import (
    DEFINE_EXTRACT_BATCH,
    define_extract_schema,
    define_read_schema,
    define_search_schema,
    define_write_schema,
)
from venona.pipeline import web_grounding
from venona.pipeline.confidence import ENTITY_CONFIDENCE, ENTITY_TIER_RANK
from venona.pipeline.metrics import Funnel
from venona.pipeline.types import RunContext
from venona.untrust import inline, quote

# Shared with `acronyms.py` through `web_grounding`, which owns the loop that
# reads them.
CANDIDATES_PER_ROUND = web_grounding.CANDIDATES_PER_ROUND
MAX_FETCH_CHARS = web_grounding.MAX_FETCH_CHARS
MAX_SUBJECT_CHARS = web_grounding.MAX_SUBJECT_CHARS
READS_PER_LOOKUP = web_grounding.READS_PER_LOOKUP

#: How many names the free pass works through between yields to the event loop.
#: Small enough that a health check waits milliseconds rather than seconds, big
#: enough that the yield is not most of the work. See `run_backfill`.
YIELD_EVERY = 25

#: One evidence quote, as one citable sentence in the `define_write` corpus.
MAX_SENTENCE_CHARS = 600

# A definition is one sentence. Longer is a paragraph, and a hover card is not
# a place to read one.
MAX_DEFINITION_CHARS = 400


@dataclass
class DefineReport:
    entities_seen: int = 0
    defined_in_article: int = 0
    defined_in_corpus: int = 0
    defined_web: int = 0
    undefined: int = 0
    already_defined: int = 0
    deferred: int = 0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if isinstance(v, int)}

    def reconciles(self) -> bool:
        """`entities_seen` partitions into exactly these five and nothing else.

        Same warning as `AcronymReport.reconciles`: `deferred` is a diagnostic,
        not an outcome -- an entity whose tier-2 slot was capped still falls
        through to `mark_entity_undefined` and is counted there.
        """
        return self.entities_seen == (
            self.already_defined
            + self.defined_in_article
            + self.defined_in_corpus
            + self.defined_web
            + self.undefined
        )


# ── tier 1: a sentence that defines rather than mentions ─────────────
#
# Three shapes carry a definition in defence prose, and the difference between
# them and an ordinary mention is the whole gate:
#
#   appositive   "...developing VICTOR, a mobile-first AI platform that..."
#   copular      "NGC2 is the Army's next-generation command and control..."
#   role         "Anduril Industries, the defense technology company, said..."
#
# What must NOT pass is "Thales Australia said the contract runs through 2027",
# which mentions the entity and defines nothing. Requiring a determiner after
# the comma is what separates them: an appositive introduces a noun phrase
# ("a company", "the prime"), while a mention continues a clause ("said", "won",
# "will deliver").

_DETERMINER = r"(?:a|an|the|its|their|his|her)"

# The nouns that name what a thing IS. This is the anchor every shape below
# hangs on, and without it the gate is useless: "Next Generation Command and
# Control, the service has emphasized..." and "AUKUS partners Australia, the UK,
# and the UK" both matched a bare `, the` and were stored as definitions. A
# comma before a determiner is not an appositive -- it is the commonest
# punctuation in English.
#
# Deliberately nouns for what something IS, never what it does or has: `contract`
# and `partnership` are absent on purpose, because a definition of a contract is
# not a definition of the company that won it.
_KIND_NOUN = (
    r"(?:company|firm|manufacturer|maker|contractor|supplier|prime|integrator|startup|"
    r"agency|office|command|service|department|ministry|directorate|bureau|"
    r"institute|laboratory|lab|university|consortium|cluster|venture|division|unit|"
    r"program|programme|project|initiative|effort|platform|system|architecture|"
    r"aircraft|drone|vehicle|vessel|ship|submarine|missile|munition|satellite|sensor|"
    r"software|tool|toolkit|framework|network|constellation|prototype|demonstrator|"
    r"radar|munitions|payload|airframe|helicopter|fighter|bomber|interceptor)"
)

# Words of adjectives between the determiner and the head noun -- "a
# mobile-first AI platform", "an advanced AI-based software platform". More
# than six and the noun has stopped describing the name.
#
# The class carries quotation marks and slashes because defence prose puts a
# classification in quotes inside the noun phrase: `a large "Group 5" stealth
# drone` is the whole description of Vectis and it failed on the quote
# character alone, in a document we held, while the hover card said the web
# knew of no definition.
_GAP = r"(?:[\w'\u2019\u201c\u201d\"&./-]+\s+){0,6}"

# A true appositive RENAMES the entity; a comma followed by a noun phrase that
# then takes a finite verb is the subject of the next clause and describes
# nothing. "…Next Generation Command and Control, the service has emphasized…"
# is the second, and it read as a definition of NGC2. The lookahead is what
# tells them apart: an appositive is followed by a participle, a relative
# pronoun or a comma -- never by `has`, `is` or `will`.
_FINITE = (
    r"(?:is|are|was|were|has|have|had|will|would|shall|should|can|could|may|might|"
    r"does|did|do|said|says|announced|plans|aims|expects|awarded|received|began)"
)
# A comma is not the only thing that opens an appositive, and the other two
# cost real definitions. An em or en dash does the same work and is the house
# style of half the trade press -- "Skunk Works unveiled Vectis—a concept for a
# group 5 drone" is a definition this rejected for punctuation. A parenthesis
# does it too, and there the determiner is usually dropped: "Vectis (a Group 5
# stealth drone)".
_OPENER = r"(?:,|\s*[—–]\s*|\s*\(\s*)"
_APPOSITIVE = re.compile(
    rf"{_OPENER}\s*{_DETERMINER}?\s*{_GAP}{_KIND_NOUN}s?\b(?!\s+{_FINITE}\b)", re.IGNORECASE
)
_COPULAR = re.compile(
    rf"\b(?:is|are|was|were)\s+{_DETERMINER}\s+{_GAP}{_KIND_NOUN}s?\b", re.IGNORECASE
)

# The fourth shape, and the one the corpus actually leans on: the description
# is the object of a describing verb rather than an appositive at all.
# "Lockheed has described Vectis as a large Group 5 stealth drone."
#
# The verb has to be present in the text BEFORE the name, which is what keeps
# this from swallowing "awarded the contract as a sole-source effort" -- `as a
# <kind noun>` on its own is a construction, not a definition, and `effort` and
# `programme` are in the kind list.
_DESCRIBING_VERB = re.compile(
    r"\b(?:describ(?:e|es|ed|ing)|call(?:s|ed)?|know(?:n)?|billed|market(?:s|ed)?|"
    r"position(?:s|ed)?|pitch(?:es|ed)?|unveil(?:s|ed)?|introduc(?:es|ed)|"
    r"tout(?:s|ed)?|present(?:s|ed)?)\s+(?:[\w'’-]+\s+){0,4}$",
    re.IGNORECASE,
)
_AS_KIND = re.compile(rf"^\s+as\s+{_DETERMINER}\s+{_GAP}{_KIND_NOUN}s?\b", re.IGNORECASE)

# The third shape, and the one that costs the most to miss: the description
# arrives BEFORE the name. "The American company Shield AI, known for its V-BAT
# drones, ...", "a delegation from the Ukrainian defense technology cluster
# Brave1". The kind noun must be immediately before the name, or "the Army's
# V-BAT contract" defines V-BAT.
_PREMODIFIER = re.compile(rf"\b{_KIND_NOUN}s?\s*$", re.IGNORECASE)


def defining_sentence(sentence: str, name: str) -> str | None:
    """The sentence if it DEFINES `name`, else None.

    The sentence must name the entity and then say what it is, in that order:
    an appositive or a copular phrase has to appear AFTER the surface, or the
    clause is about something else entirely. "Anduril, which supplies Lattice,
    said the Army is a demanding customer" would otherwise pass on the trailing
    "is a".
    """
    if not sentence or not name:
        return None

    clean = _normalise_ws(sentence)
    if len(clean) < 30:
        # A fragment cannot define anything, and the annotator's context window
        # occasionally yields a headline.
        return None

    where = clean.find(_normalise_ws(name))
    if where < 0:
        return None

    after = clean[where + len(_normalise_ws(name)) :]
    before = clean[:where]
    if (
        _APPOSITIVE.match(after)
        or _COPULAR.search(after)
        or _PREMODIFIER.search(before)
        or (_AS_KIND.match(after) and _DESCRIBING_VERB.search(before))
    ):
        return sentence.strip()[:MAX_DEFINITION_CHARS]
    return None


def verify_definition_sentence(sentence: str | None, name: str, text: str) -> str | None:
    """The sentence if the document really contains it and it really names `name`.

    THE ONE GATE in front of every model-supplied definition sentence, whether
    it arrived folded into an `analyze` call or from a re-read of an article we
    already hold. Invariant 3 says a definition exists only if it is traceable
    to a sentence in a document; this is the function that decides traceable,
    and both writers go through it so there is one answer rather than two.

    Two checks and no third. The sentence must be IN the supplied document --
    compared through `web_grounding.comparable`, because a model retyping a
    sentence normalises curly quotes and en dashes that the extractor left
    alone, and rejecting on typography would throw away correct evidence. And
    it must NAME the entity, or it is a sentence about something else in the
    same article, which is the failure that looks most like success.

    Deliberately NOT checked: whether the sentence looks definitional.
    `defining_sentence`'s regexes are the free tier's way of finding a
    definition with no model in the loop; here a model has already said this
    sentence defines the thing, and re-imposing the regex would discard exactly
    the shapes the regex cannot spell -- which is the whole reason to ask.
    """
    if not sentence or not name or not text:
        return None

    clean = sentence.strip()
    if len(_normalise_ws(clean)) < 30:
        return None

    haystack = web_grounding.comparable(text)
    if web_grounding.comparable(clean) not in haystack:
        return None
    if web_grounding.comparable(name) not in web_grounding.comparable(clean):
        return None
    return clean[:MAX_DEFINITION_CHARS]


def evidence_for(conn: db.Conn, entity_id: str) -> list[db.Row]:
    """Every sentence we hold about this entity, newest article first.

    Follows the merge closure for the same reason `_signals` does: assertions
    written before a merge carry the loser's id, and an entity that has just
    absorbed another must be able to define itself from what the other knew.
    """
    from venona.services.curate import merge_closure

    members = merge_closure(conn, entity_id)
    return repo.evidence_for_closure(conn, list(members))


def ground_in_corpus_quote(conn: db.Conn, *, entity_id: str, name: str, now: str) -> bool:
    """Tier 1. Quote a sentence we already hold, or return False.

    Free and deterministic: no model call, no network. This is expected to carry
    most entities, which is why tier 2 is capped rather than the default.

    TWO PASSES, MARKED SENTENCES FIRST. `definition_quote` is a sentence the
    extractor picked out of the whole article as saying what the thing is, and
    `verify_definition_sentence` has already proved it is in that article. The
    regex over `evidence_quote` is the fallback for rows written before that
    field existed, and for entities no extractor marked. Running the fallback
    first would let a thin appositive in a passing mention beat the sentence
    the article actually devotes to the subject -- which is the shallow half of
    what this change is for, not just the missing half.
    """
    rows = evidence_for(conn, entity_id)

    for row in rows:
        sentence = (row["definition_quote"] or "").strip()
        if not sentence:
            continue
        store_entity_definition(
            conn,
            entity_id=entity_id,
            definition=sentence[:MAX_DEFINITION_CHARS],
            method="in_article",
            confidence=ENTITY_CONFIDENCE["in_article"],
            article_id=row["article_id"],
            url=row["url"],
            quote=sentence[:MAX_DEFINITION_CHARS],
            now=now,
        )
        return True

    for row in rows:
        sentence = defining_sentence(row["evidence_quote"], name)
        if sentence is None:
            continue
        store_entity_definition(
            conn,
            entity_id=entity_id,
            definition=sentence,
            method="in_article",
            confidence=ENTITY_CONFIDENCE["in_article"],
            article_id=row["article_id"],
            url=row["url"],
            quote=sentence,
            now=now,
        )
        return True
    return False


# ── tier 2: written from evidence we hold ────────────────────────────


async def write_from_corpus(
    ctx: RunContext,
    *,
    entity_id: str,
    name: str,
    entity_type: str,
    budget: budget_mod.Budget | None = None,
) -> tuple[str, list[str]] | None:
    """One sentence, written from OUR sentences. Returns (definition, article_ids).

    No tools, exactly like the acronym reader: the model may only use the text
    we supply. The article ids it returns are checked against the ids we sent,
    so a definition cannot claim provenance we did not give it -- the same
    discipline as the acronym quote having to be a substring of our page.
    """
    rows = evidence_for(ctx.db, entity_id)
    if not rows:
        return None

    # Numbered, not id-tagged. See `define_write_schema`: asked for ids, the
    # model returns them without the `doc:` prefix and every citation fails.
    usable = [row for row in rows if row["evidence_quote"]]
    if not usable:
        return None
    # The citation contract is the sentence NUMBER, so each one has to stay one
    # addressable unit: `inline` collapses any newline an evidence quote carries
    # and would otherwise let a quote forge a `[2]` of its own.
    corpus = "\n\n".join(
        f"[{i}] {inline(row['evidence_quote'], limit=MAX_SENTENCE_CHARS)}"
        for i, row in enumerate(usable, 1)
    )

    result = await call_and_record(
        ctx,
        "define_write",
        user_content=(
            f"ENTITY: {inline(name, limit=MAX_SUBJECT_CHARS)}\n"
            f"TYPE: {entity_type}\n\nSENTENCES:\n{corpus}"
        ),
        schema=define_write_schema(),
        system_prompt=define_write_system(),
        model=ctx.settings.llm.acronym_model,
        effort="low",
        tools=(),  # no tools: it may only read what we supplied
        timeout=120,
        budget=budget,
    )
    if not result.ok:
        return None

    data = result.data or {}
    if not data.get("found"):
        return None

    definition = (data.get("definition") or "").strip()
    # Out-of-range numbers are dropped rather than clamped: a citation we cannot
    # resolve is not provenance, and a definition left with none is discarded.
    cited: list[str] = []
    for n in data.get("sources") or []:
        if isinstance(n, int) and 1 <= n <= len(usable):
            article_id = usable[n - 1]["article_id"]
            if article_id not in cited:
                cited.append(article_id)
    if not definition or not cited:
        # A definition citing nothing we supplied is a definition from memory.
        return None

    return definition[:MAX_DEFINITION_CHARS], cited


# ── the re-read: a document we hold, for the sentences that define ──


@dataclass
class BackfillReport:
    documents_seen: int = 0
    documents_read: int = 0
    entities_seen: int = 0
    defined: int = 0
    unanswered: int = 0
    deferred: int = 0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if isinstance(v, int)}


async def read_definitions_from_document(
    ctx: RunContext,
    *,
    article_id: str,
    text: str,
    names: list[str],
    budget: budget_mod.Budget | None = None,
) -> dict[str, str] | None:
    """One call, one document, every undefined name in it. Returns name -> sentence.

    The corpus half of the same question `analyze` now answers inline. New
    documents pay nothing for it -- the field rides along on the extraction
    call that was already being made -- but a document read before that field
    existed holds its descriptions where nothing can reach them, and there is
    no way to get at those without reading the text again.

    ONE CALL PER DOCUMENT, not per name. A document naming twelve undefined
    entities costs one read either way, and the per-name shape is what made a
    corpus-wide pass unaffordable enough never to be written.

    `tools=()` for the reason `define_write` has it: the answer must come from
    the supplied text and nothing else. The text is ours -- already fetched,
    already extracted, already stored -- so this is the least exposed of the
    three reads, but it is quoted all the same, because `quote` is what keeps a
    document's own words from being read as instructions.
    """
    result = await call_and_record(
        ctx,
        "define_extract",
        user_content=(
            "NAMES:\n"
            + "\n".join(f"- {inline(name, limit=MAX_SUBJECT_CHARS)}" for name in names)
            + "\n\nARTICLE:\n"
            + quote(text, kind="stored-article", limit=MAX_FETCH_CHARS, keep_lines=True)
        ),
        schema=define_extract_schema(names),
        system_prompt=define_extract_system(),
        model=ctx.settings.llm.acronym_model,
        effort="low",
        tools=(),
        timeout=180,
        budget=budget,
    )
    if not result.ok:
        return None

    found: dict[str, str] = {}
    for item in (result.data or {}).get("definitions") or []:
        name = (item.get("name") or "").strip()
        # The enum constrains this, but a schema is the model's contract and
        # not a guarantee about the row we are about to write. A name we did
        # not supply has no assertion to stamp.
        if name not in names:
            continue
        sentence = verify_definition_sentence(item.get("sentence"), name, text)
        if sentence:
            found[name] = sentence
    return found


async def run_backfill(
    ctx: RunContext,
    *,
    min_salience: float,
    document_limit: int | None = None,
    use_model: bool = True,
    budget: budget_mod.Budget | None = None,
    emit=None,
) -> BackfillReport:
    """Re-read what is already stored, for every name still unexplained.

    The free half runs first and per document: `ground_in_corpus_quote` over
    the sentences we hold, with the widened shapes. Only the names that survive
    it reach the model, so a corpus whose descriptions are mostly appositives
    costs almost nothing to work through.

    `use_model=False` stops after the free pass, which by then has run over
    every eligible name in the corpus.
    Worth doing first: it says how far the shapes reach before any of this is
    charged for, and the same reasoning `venona anchors propose --no-model`
    carries -- read ten of the rows by hand before paying for the rest.
    """
    report = BackfillReport()
    conn = ctx.db
    now = ctx.clock.now()

    # ── the free pass, over every eligible name, before anything is spent ──
    #
    # ENTITY-SCOPED and unbounded by the document loop below. `evidence_for`
    # gathers an entity's sentences from wherever they are, so this reaches
    # names the re-read's document eligibility would have skipped -- and it
    # runs to completion even when `use_model` is off, which is what makes
    # "the whole free pass" a thing that can be asked for on its own.
    candidates = repo.free_pass_candidates(conn, min_salience)
    report.entities_seen = len(candidates)
    for index, candidate in enumerate(candidates):
        ctx.check_cancel()
        if emit is not None and index % YIELD_EVERY == 0:
            await emit(
                "stage_progress",
                "define",
                {"current": index, "total": len(candidates), "name": candidate["name"]},
            )
        # HANDS THE EVENT LOOP BACK, and this loop is the only stage that has
        # to do it deliberately. Every other one awaits a model call every few
        # hundred milliseconds and yields by construction; this one is pure
        # database work -- roughly four statements per name, none of them
        # awaitable, because `db` is psycopg and synchronous.
        #
        # A UI-triggered run executes INSIDE the API process, so a loop that
        # never yields is an outage: 6,193 names held the loop for 51 seconds
        # on 2026-09-15, the scheduler logged both its jobs "missed by
        # 0:00:50", and every request in that window (health checks included)
        # timed out at the proxy as a 502. The run itself succeeded, which is
        # what made it look like a crash rather than a stall.
        if index % YIELD_EVERY == 0:
            await asyncio.sleep(0)
        if ground_in_corpus_quote(conn, entity_id=candidate["id"], name=candidate["name"], now=now):
            report.defined += 1
            await ctx.counter("entities_defined")

    if not use_model:
        return report

    documents = repo.backfill_documents(conn, min_salience, document_limit).fetchall()
    report.documents_seen = len(documents)

    for index, document in enumerate(documents):
        ctx.check_cancel()
        if emit is not None:
            await emit(
                "stage_progress",
                "define",
                {"current": index, "total": len(documents), "article_id": document["id"]},
            )

        row = repo.backfill_document_text(conn, document["id"]).fetchone()
        if row is None or not (row["text_nfc"] or "").strip():
            continue
        text = row["text_nfc"]

        pending = repo.backfill_entities_for_document(conn, document["id"], min_salience).fetchall()
        if not pending:
            continue

        # No second free pass here: the one above has already run over every
        # eligible name, so anything still selected by this query is a name no
        # sentence we hold explains. Re-running it per document would re-read
        # the same evidence once per article that mentions the name.
        remaining = list(pending)
        if not remaining:
            continue

        report.documents_read += 1
        for batch_start in range(0, len(remaining), DEFINE_EXTRACT_BATCH):
            batch = remaining[batch_start : batch_start + DEFINE_EXTRACT_BATCH]
            names = [entity["name"] for entity in batch]
            try:
                found = await read_definitions_from_document(
                    ctx, article_id=document["id"], text=text, names=names, budget=budget
                )
            except budget_mod.BudgetExceeded as exc:
                # STOP, like `run_define` and `analyze` before it. Continuing
                # sends every remaining document into the same exception for
                # the same warning, and a corpus-wide pass has a lot of
                # remaining documents to say it with.
                left = len(documents) - index
                report.deferred += left
                report.warnings.append(budget.deferral(left, "articles") if budget else str(exc))
                return report

            if found is None:
                continue

            for entity in batch:
                sentence = found.get(entity["name"])
                # `''` where the read found nothing, so this document is not
                # offered again for this name. See `set_definition_quote`.
                repo.set_definition_quote(conn, sentence or "", entity["assertion_id"])
                if not sentence:
                    report.unanswered += 1
                    continue
                store_entity_definition(
                    conn,
                    entity_id=entity["id"],
                    definition=sentence,
                    method="in_article",
                    confidence=ENTITY_CONFIDENCE["in_article"],
                    article_id=document["id"],
                    url=row["url"],
                    quote=sentence,
                    now=now,
                )
                report.defined += 1
                await ctx.counter("entities_defined")

    return report


# ── tier 3: the web, on demand ───────────────────────────────────────


class EntityGrounding(web_grounding.WebGrounding):
    """The entity half of tier 3: prompts, and the two checks.

    Three tests before anything is accepted, mirroring `AcronymGrounding` minus
    the initials test, which has no meaning for a name:

      1. the quote is a substring of text WE fetched and extracted
      2. the quote names the entity
      3. the URL is one the model actually proposed (the drive loop's doing)

    The model is a search tool and a reader over text we control. It is never a
    source of facts.
    """

    search_task = "define_search"
    read_task = "define_read"

    @property
    def search_system(self) -> str:
        return define_search_system()

    @property
    def read_system(self) -> str:
        return define_read_system()

    not_found_reason = "not_described_in_page"

    def search_schema(self) -> dict:
        return define_search_schema()

    def read_schema(self) -> dict:
        return define_read_schema()

    def search_prompt(self, subject: str, *, known: list[str] | None) -> str:
        # `known` is unused here: an entity has one description, so there is no
        # "what ELSE does this mean" question to ask. `collect` is likewise
        # never passed by this caller.
        from venona.domain import get_domain

        return (
            f"Find authoritative pages describing {subject!r} {get_domain().search_context}. "
            f"Prefer the organisation's own site, a programme office page, or a "
            f"reference work over a news article."
        )

    def retry_prompt(self, attempted: str) -> str:
        return (
            f"\n\nThese were already tried and did NOT work:\n{attempted}\n\n"
            f"Propose DIFFERENT pages on different hosts. `fetch_status` means the "
            f"server refused us; `not_described_in_page` means the page was readable "
            f"but never says what this is."
        )

    def read_prompt(self, subject: str, extracted: str) -> str:
        # `extracted` is a third-party page this run just fetched -- the most
        # directly attacker-chosen text in the pipeline (ADR 0166). It is read
        # with `tools=()`, so the worst it buys is a definition the verification
        # then rejects; the envelope is what keeps it visible to the operator.
        return (
            f"ENTITY: {inline(subject, limit=MAX_SUBJECT_CHARS)}\n\n"
            f"TEXT:\n{quote(extracted, kind='fetched-page', limit=None, keep_lines=True)}"
        )

    def answer_of(self, data: dict) -> str:
        return (data.get("definition") or "").strip()

    def incomplete(self, answer: str) -> dict:
        return {"definition": answer or None}

    def reject(
        self, subject: str, answer: str, quote: str, extracted: str
    ) -> web_grounding.Rejection | None:
        # (1) the quote must come from OUR text, not the model's memory
        if web_grounding.comparable(quote) not in web_grounding.comparable(extracted):
            return web_grounding.Rejection("quote_not_in_page", {"quote": quote[:200]})
        # (2) and it must be about this entity, or it proves nothing
        if web_grounding.comparable(subject) not in web_grounding.comparable(quote):
            return web_grounding.Rejection("entity_not_in_quote", {"quote": quote[:200]})
        return None

    def finalize(self, answer: str) -> str:
        return answer[:MAX_DEFINITION_CHARS]


async def lookup_web(
    ctx: RunContext,
    entity_id: str,
    name: str,
    *,
    trace: list[dict] | None = None,
    budget: budget_mod.Budget | None = None,
) -> tuple[str, str, str] | None:
    """Returns (definition, quote, url), or None.

    The drive loop is `web_grounding.ground`, shared with `acronyms.py`, and two
    things it holds are what a private copy of it loses:

      * **`tools=` has to be declared.** `call_and_record` defaults it to `()`
        and `llm/harness/claude_code.py` turns that into `--tools ""`, while
        `define_search_system()` asks the model to "locate authoritative pages":
        a search task with no tools searches the web with the web taken away,
        and answers from memory or not at all. Every tier-3 entity lookup ran
        blind that way until 2026-08-09, and the attempt state recording those
        non-answers as answered had to be reset.
      * **The reframed retry round.** Without it a failed first search ends the
        lookup; `web_grounding.py` carries the note that "a bad first search
        otherwise ended the attempt, and the first two live lookups both died
        that way". One extra search call, only on a lookup that already failed.

    `entity_id` is unused and kept: callers pass it positionally, and it is what
    the trace is about even though nothing here reads it.
    """
    return await web_grounding.ground(
        ctx,
        name,
        EntityGrounding(),
        trace=trace,
        budget=budget,
    )


# ── storage ──────────────────────────────────────────────────────────


def store_entity_definition(
    conn: db.Conn,
    *,
    entity_id: str,
    definition: str,
    method: str,
    confidence: float,
    article_id: str | None = None,
    url: str | None = None,
    quote: str | None = None,
    source_article_ids: list[str] | None = None,
    now: str,
) -> None:
    """Write a grounded definition, unless a person has ruled on this one.

    `decided_at` is the first guard, and it is what `0001_baseline.sql` says of
    that column: a human verdict outranks every automated tier, and the stage
    must never stamp it. Without this the next run silently overwrites a
    correction.

    THE SECOND GUARD IS THE TIER LADDER, the entity side of the rule
    `acronyms.store_definition` holds. Without it the on-demand
    `POST /entities/{entity_id}/define` writes `web_grounded`/0.5 straight over
    an existing `in_article`/1.0: a sentence lifted verbatim from an article we
    hold, replaced by a paraphrase of a web page, because someone pressed a
    button. A re-grounding may only move a row UP the ladder.

    When the stored tier is stronger the whole row stands and only `last_seen`
    moves. Note what is deliberately NOT done: keeping the stronger row's method
    and quote while writing the WEAKER row's definition text. That produces a
    row asserting `in_article` provenance for a sentence that came off the web,
    with a quote that does not support it -- worse than either input, and
    invisible, because every column looks individually plausible. The definition
    and its evidence travel together or not at all.
    """
    existing = repo.select_entity_definitions_by_entity_id(conn, entity_id).fetchone()
    if existing is not None and existing["decided_at"]:
        return

    stored_method = existing["method"] if existing else None
    if stored_method and ENTITY_TIER_RANK.get(stored_method, 0) > ENTITY_TIER_RANK.get(method, 0):
        repo.update_entity_definitions_by_last_seen_entity_id(conn, now, entity_id)
        return

    db.upsert(
        conn,
        "entity_definitions",
        {
            "entity_id": entity_id,
            "definition": definition,
            "method": method,
            "confidence": confidence,
            "grounding_article_id": article_id,
            "grounding_url": url,
            "grounding_quote": quote,
            "source_article_ids": db.jdump(source_article_ids) if source_article_ids else None,
            "status": "active",
            # Read back rather than overwritten: `first_seen` is when we first
            # knew this entity, and an upsert that stamps `now` on every pass
            # makes the column mean `last_seen` twice.
            "first_seen": (existing["first_seen"] if existing else now),
            "last_seen": now,
        },
        key=["entity_id"],
    )


def mark_entity_undefined(
    conn: db.Conn,
    *,
    entity_id: str,
    now: str,
    run_id: str | None = None,
    attempted: bool = False,
) -> None:
    """No definition, said honestly.

    `attempted` is the distinction the acronym side draws too: an entity nobody
    has looked up and one that cost a search plus three page fetches are not the
    same row, and the card must not offer to spend money on a question already
    answered.
    """
    existing = repo.select_entity_definitions_by_entity_id_2(conn, entity_id).fetchone()
    if existing is not None and existing["decided_at"]:
        return

    fields: dict = {
        "entity_id": entity_id,
        "definition": None,
        "method": "undefined",
        "confidence": 0.0,
        "status": "undefined",
        "first_seen": (existing["first_seen"] if existing else now),
        "last_seen": now,
        # Carried forward explicitly. A pipeline pass that finds nothing is not
        # an ATTEMPT -- only a web lookup is -- and an upsert that wrote NULL
        # here would erase the record of one, putting the card back to offering
        # a first-time lookup for a question already answered.
        "last_attempt_at": (
            now if attempted else (existing["last_attempt_at"] if existing else None)
        ),
        "last_attempt_run_id": (
            run_id if attempted else (existing["last_attempt_run_id"] if existing else None)
        ),
        "attempt_count": (
            ((existing["attempt_count"] if existing else 0) or 0) + (1 if attempted else 0)
        ),
    }

    db.upsert(conn, "entity_definitions", fields, key=["entity_id"])


def _normalise_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


# ── the stage ────────────────────────────────────────────────────────


def targets(conn: db.Conn, run_id: str, *, min_salience: float) -> list[db.Row]:
    """The entities THIS RUN's brief actually references.

    Scope, and it is deliberate: only an entity a brief references is reachable
    by hovering, and counting 2026-08-07 that was 215 of 1,601. Defining the
    rest on a schedule would spend on entities nobody can open. Everything else
    is defined on demand through `POST /entities/{entity_id}/define`.

    An entity that already has a definition is skipped -- re-grounding a settled
    row costs money and can only make it worse.

    Falls back to the LATEST brief when this run wrote none. `venona run --only
    define` mints a fresh run id and never reaches the brief stage, so keying
    strictly on `ctx.run_id` would make the documented way to re-run this stage
    a no-op -- stages read their inputs from Postgres, and the input here is "the
    brief a reader is looking at".
    """
    scoped = repo.select_briefs_by_run_id(conn, run_id).fetchone()
    if scoped is None:
        latest = repo.select_briefs(conn).fetchone()
        if latest is None:
            return []
        run_id = latest["run_id"]

    # ORDERED BY SALIENCE, not by `e.relevance`.
    #
    # `entities.relevance` is never written -- counted 2026-08-16, 16 of 2277
    # rows were non-zero, and nothing in `pipeline/` or `services/` assigns it;
    # relevance is a CLUSTER score (`clusters.relevance_overall`, written by
    # `analyze`). Ordering on it is ordering by a constant, which leaves the
    # ranking at arbitrary storage order -- and `define_cap_per_run` binds:
    # run `06a80ae3` reported `entities_defined: 25` against a cap of 25, so
    # those 25 definitions were an arbitrary 25 of the candidates rather than
    # the most prominent ones.
    #
    # `a.salience` is populated on every row (4566 of 4566, 0.03-1.00 on the
    # same count) and is already what `min_salience` filters on, so the cap
    # drops the entities that were passing mentions.
    return repo.select_briefs_by_run_id_2(conn, run_id, min_salience).fetchall()


async def run_define(ctx: RunContext, *, budget: budget_mod.Budget | None = None) -> DefineReport:
    """Tier 1 over every target, then tier 2 for what is left, capped.

    Runs AFTER `brief`, because that is the first point at which we know which
    entities the brief references -- the whole scope decision depends on it.
    """
    report = DefineReport()
    conn = ctx.db
    now = ctx.clock.now()
    limits = ctx.settings.pipeline

    rows = targets(conn, ctx.run_id, min_salience=limits.define_min_salience)
    report.entities_seen = len(rows)

    needs_writing: list[db.Row] = []

    for row in rows:
        ctx.check_cancel()
        if ground_in_corpus_quote(conn, entity_id=row["id"], name=row["name"], now=now):
            report.defined_in_article += 1
            await ctx.counter("entities_defined")
        else:
            needs_writing.append(row)

    # Tier 2, capped. Ranked by salience already, so the cap drops the entities
    # a reader is least likely to open rather than an arbitrary tail.
    for index, row in enumerate(needs_writing):
        ctx.check_cancel()
        if report.defined_in_corpus >= limits.define_cap_per_run:
            report.deferred += 1
            mark_entity_undefined(conn, entity_id=row["id"], now=now)
            report.undefined += 1
            continue

        try:
            written = await write_from_corpus(
                ctx,
                entity_id=row["id"],
                name=row["name"],
                entity_type=row["type"],
                budget=budget,
            )
        except budget_mod.BudgetExceeded as exc:
            # STOP. Setting `written = None` and continuing sends every
            # remaining entity back into `write_from_corpus` to raise the same
            # exception and append the same warning, so a run that exhausts its
            # budget with fifty entities left produces fifty identical warnings
            # to learn what it already knew. `analyze.py` breaks here; so does
            # this.
            remaining = needs_writing[index:]
            for pending in remaining:
                mark_entity_undefined(conn, entity_id=pending["id"], now=now)
                report.undefined += 1
                report.deferred += 1
            # And it is deferred on the BUDGET, not only on this report. This
            # stage counted its own deferrals and told the budget nothing, so
            # `budget.summary` -- the line a run's log ends with -- reported
            # zero deferred on a run that had just abandoned fifty entities.
            # `str(exc)` is unreachable while a raised `BudgetExceeded` can only
            # come from a budget that exists; it is what the type says.
            report.warnings.append(budget.deferral(len(remaining), "names") if budget else str(exc))
            break

        if written is None:
            mark_entity_undefined(conn, entity_id=row["id"], now=now)
            report.undefined += 1
            continue

        definition, cited = written
        store_entity_definition(
            conn,
            entity_id=row["id"],
            definition=definition,
            method="in_corpus",
            confidence=ENTITY_CONFIDENCE["in_corpus"],
            source_article_ids=cited,
            now=now,
        )
        report.defined_in_corpus += 1
        await ctx.counter("entities_defined")

    # The partition `reconciles()` asserts, recorded as data. `already_defined`
    # and `defined_web` are declared on the report and never incremented --
    # `targets()` filters defined entities out before this sees them, and there
    # is no tier-3 web path here -- so they are structurally zero and are not
    # given funnel drops that would always read 0.
    funnel = Funnel("define", offered=report.entities_seen)
    funnel.keep(report.defined_in_article + report.defined_in_corpus)
    funnel.drop("undefined", report.undefined)
    funnel.emit(ctx)
    ctx.metric("define", "deferred", report.deferred)
    ctx.metric("define", "cap_per_run", limits.define_cap_per_run)
    ctx.metric("define", "min_salience", limits.define_min_salience)

    return report
