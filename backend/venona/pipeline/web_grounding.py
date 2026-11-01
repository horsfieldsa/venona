"""Tier-3 grounding: the one implementation, and what varies between kinds.

Two callers ground something against the open web -- `acronyms.py` grounds an
acronym's expansion, `define.py` grounds an entity's description -- and they
share one loop because a private copy of it loses things silently. A search task
that omits `tools=` gets `--tools ""` from `llm/harness/claude_code.py` and
searches nothing while `define_search_system()` asks it to "locate authoritative
pages": every tier-3 entity lookup answered from memory or not at all that way
until 2026-08-09. A copy also drops the reframed retry round at the bottom of
`ground`. Neither is visible from either caller alone.

The shape, and the thing worth preserving (ADR 0003):

    model returns candidate URLs  ->  WE fetch and extract  ->
    second call with NO tools quotes from OUR text  ->  we verify the substring

`WebFetch` hands the model a sub-model *summary* rather than page text, so the
model is a search tool and a reader over text we control. It is never a source
of facts.

What varies between the two kinds is a `WebGrounding` subclass, defined next to
the checks it needs -- `acronyms.py` owns the initials test, `define.py` owns
the name test -- so this module never imports either and nothing is circular.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

import trafilatura

from venona.llm import budget as budget_mod
from venona.llm.runner import call_and_record
from venona.pipeline.types import RunContext
from venona.untrust import inline

# Tier-3 GATING is tunable in `config/settings.yaml` (`pipeline.acronym_*`).
# These are the mechanical limits of one lookup, not policy, which is why they
# are constants.
MAX_FETCH_CHARS = 20000

# Characters a page prints and a model retypes differently. Applied BEFORE
# NFKC, which would otherwise expand `™` to the letters `TM` and leave the
# comparison worse than it started.
_TYPOGRAPHY = str.maketrans(
    {
        **dict.fromkeys("‐‑‒–—―−", "-"),
        **dict.fromkeys("‘’‚‛′", "'"),
        **dict.fromkeys("“”„‟″", '"'),
        **dict.fromkeys("    ", " "),
        **dict.fromkeys("­​‌‍™®", ""),
    }
)


def comparable(text: str) -> str:
    """The form two strings are compared in when asking "is this the same text".

    Whitespace collapsing alone cost real definitions. The quote gate compares
    what the model retyped against what trafilatura extracted, and the two
    disagree on typography the page's author never thought about: curly
    apostrophes, en dashes, non-breaking spaces, a `™` the model dropped. The
    expansion gate has the same problem one level up -- "Army Multi-Purpose
    High-Energy Laser" was rejected (2026-08-29) against a quote reading "High
    Energy", which is one writer's hyphen against another's space.

    So hyphens and spaces fold together too. That cannot admit a DIFFERENT
    expansion: the words and their order still have to be present, which is the
    whole of what these gates verify.

    Not used by `mentions_acronym`, which has its own reason to care about the
    surface's exact punctuation.
    """
    folded = unicodedata.normalize("NFKC", (text or "").translate(_TYPOGRAPHY))
    return re.sub(r"[-\s]+", " ", folded).strip().lower()


# How many pages must be READ before a round gives up, and how many candidates
# it may burn getting there. The gap between them is the point: a 403 or a PDF
# costs a candidate, not one of the `READS_PER_LOOKUP` chances at a definition.
READS_PER_LOOKUP = 3
CANDIDATES_PER_ROUND = 6

#: The longest thing that can be a subject. An acronym is a handful of letters
#: and the longest real entity name on this corpus is well under this, so the
#: cap costs nothing legitimate -- while `ground` is the only pipeline path that
#: hands corpus-derived text to a model holding WebSearch and WebFetch, which is
#: what makes a bound worth having at all (ADR 0166).
MAX_SUBJECT_CHARS = 120


#: Every trace reason, and which part of the funnel it belongs to. Declared
#: HERE, beside the gates that write most of them, because a second copy of the
#: table would drift from the strings it describes -- `scripts/lookup_report.py`
#: reads it rather than keeping its own.
#:
#: The grouping is the actionable part. `search` is a retrieval problem,
#: `fetch` is a plumbing problem, `read` means a page was in our hands and said
#: nothing useful, and only `judgement` is about what the model made of text we
#: supplied. A sweep failing 80% in `fetch` will not be improved by a better
#: prompt, however tempting that is.
REASON_STAGE: dict[str, str] = {
    "subject_too_long": "search",
    "subject_empty_after_scrub": "search",
    "search_failed": "search",
    "search_failed_twice": "search",
    "no_candidates": "search",
    "retry_skipped": "search",
    "retry_search_failed": "search",
    "retry_proposed_nothing_new": "search",
    "fetch_error": "fetch",
    "fetch_status": "fetch",
    "no_extractable_text": "fetch",
    "read_failed": "read",
    "not_defined_in_page": "read",
    "not_described_in_page": "read",
    "incomplete": "read",
    "quote_not_in_page": "judgement",
    "expansion_not_in_quote": "judgement",
    "acronym_not_in_quote": "judgement",
    "entity_not_in_quote": "judgement",
    "initials_mismatch": "judgement",
    # Written by a caller (`acronyms.py`, `api/routers/glossary.py`) rather than
    # by a gate: the page was read, believed and accepted, and our own documents
    # then contradicted it.
    "off_domain": "judgement",
    # An acceptance, not a failure. It is in this table so that a caller
    # classifying a trace cannot meet a reason the table has never heard of.
    "adjudicated": "accepted",
}

#: Stages that mean a page was fetched, extracted and put in front of the model.
_PAGE_IN_HAND = frozenset({"read", "judgement", "accepted"})


def pages_read(trace: list[dict[str, Any]]) -> int:
    """How many candidate pages this lookup actually got to read.

    The difference between a lookup that answered the question and one that
    never asked it. A lookup whose every candidate refused us, timed out or
    published only a PDF has learned nothing about the acronym -- and reporting
    that identically to a lookup that read six pages and judged them tells a
    reader the search is exhausted when the network is the problem.
    """
    return sum(1 for e in trace if REASON_STAGE.get(str(e.get("reason"))) in _PAGE_IN_HAND)


@dataclass
class Rejection:
    """Why a candidate page did not produce an answer.

    `extra` reaches the trace verbatim, because the two kinds report different
    things about a rejection -- an acronym lookup wants the expansion it threw
    away, an entity lookup wants the quote. Losing those turned a trace into a
    list of bare reason strings.
    """

    reason: str
    extra: dict[str, Any]


class WebGrounding:
    """Everything that differs between grounding an acronym and an entity.

    Subclasses supply the task names, prompts and the acceptance checks. The
    drive loop below supplies the retries, the budget checks, the fetch/extract
    cycle and the trace.
    """

    #: `llm_calls.task` for the two calls. Distinct per kind so the ledger and
    #: the fixture keys stay separable.
    search_task: str
    read_task: str
    search_system: str
    read_system: str
    #: The reason recorded when the page was readable but said nothing useful.
    #: Deliberately different per kind -- it is fed back to the model in the
    #: reframed retry, where "never spells the acronym out" and "never says what
    #: this is" ask for different pages.
    not_found_reason: str

    def search_schema(self) -> dict: ...

    def read_schema(self) -> dict: ...

    def search_prompt(self, subject: str, *, known: list[str] | None) -> str: ...

    def retry_prompt(self, attempted: str) -> str:
        """Appended when a round is reframed. `attempted` is the formatted list."""
        ...

    def read_prompt(self, subject: str, extracted: str) -> str: ...

    def answer_of(self, data: dict) -> str:
        """The field this kind calls its answer -- `expansion` or `definition`."""
        ...

    def incomplete(self, answer: str) -> dict[str, Any]:
        """Trace extras when the model returned no answer or no quote."""
        ...

    def reject(self, subject: str, answer: str, quote: str, extracted: str) -> Rejection | None:
        """The verification gate. `None` accepts.

        Every check that keeps a model's memory out of the corpus lives in an
        implementation of this, so the two kinds cannot drift on how strict they
        are without the difference being visible in one place.
        """
        ...

    #: Rejection reasons this kind will let an adjudicator overturn. Empty by
    #: default, and empty for entity grounding: an appeal is only meaningful
    #: against a STRUCTURAL refusal, where the evidence was read and believed
    #: and a mechanical rule disagreed with it. Nothing here may name a check
    #: that keeps the model's memory out of the corpus (ADR 0178).
    adjudicable: frozenset[str] = frozenset()

    async def adjudicate(
        self,
        ctx: RunContext,
        subject: str,
        answer: str,
        sentence: str,
        *,
        budget: budget_mod.Budget | None,
    ) -> tuple[bool, str] | None:
        """`(overturned, why)` for a rejection in `adjudicable`, or None.

        None means "not adjudicated" and the rejection stands, which is what
        the base class does and what a kind that never opted in keeps doing.
        """
        return None

    def finalize(self, answer: str) -> str:
        return answer


def normalise_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


async def ground(
    ctx: RunContext,
    subject: str,
    kind: WebGrounding,
    *,
    trace: list[dict] | None = None,
    budget: budget_mod.Budget | None = None,
    collect: bool = False,
    known: list[str] | None = None,
) -> tuple[str, str, str] | list[tuple[str, str, str]] | None:
    """Returns (answer, quote, url), or None.

    `trace` collects why each candidate was rejected. Without it a lookup that
    fails is indistinguishable from a lookup that never ran -- both produce
    `None` -- and the first two live lookups came back rejected with no way to
    tell a working gate from a broken chain (ADR 0043).

    ── `collect` and `known`: finding the OTHER meanings ────────────────

    Default behaviour returns the first answer that passes, which is right when
    filling a gap: one grounded answer is what an undefined surface needs. It is
    wrong when the surface is genuinely ambiguous, and that failure is silent and
    expensive. Measured on this corpus, 2026-08-09: `FCC` grounded as "Fire
    Control Center" from a Kongsberg product page -- a real definition of a real
    thing, passing every check -- while the articles using FCC meant the Federal
    Communications Commission. First-past-the-post cannot tell those apart,
    because both are true statements about the letters.

    `collect=True` reads every candidate page and returns EVERY distinct
    verified answer, so a person can be shown the alternatives and pick. `known`
    lists answers already on file, which does two things: it tells the search to
    look for pages about *different* meanings, and it stops a re-lookup spending
    its whole budget re-confirming the answer already held.

    The checks are identical in both modes: what changes is how many answers
    survive, not how good one has to be.
    """

    def note(url: str | None, reason: str, **extra) -> None:
        if trace is not None:
            trace.append({"url": url, "reason": reason, **extra})

    def nothing():
        """`[]` and `None` are the same answer in the two modes -- the collect
        caller wants a list it can iterate without a None check."""
        return [] if collect else None

    # THE ONE PLACE IN THE PIPELINE WHERE CORPUS TEXT MEETS A MODEL HOLDING
    # TOOLS: `subject` is a surface or an entity name the model extracted from a
    # scraped page, so the shortest path from "publish an article" to "venona's
    # model makes an outbound request of your choosing" runs through this
    # argument (ADR 0166). `WebSearch` and `WebFetch` run inside the CLI, where
    # `sources/urlguard.py` is not in front of them, so the guard is here.
    #
    # Refused, never truncated: an acronym or an entity name does not reach this
    # length, so something that does is not a subject, and quietly cutting it to
    # fit would send the first `MAX_SUBJECT_CHARS` of an instruction anyway. The
    # rejection carries its reason (ADR 0043).
    if len(subject) > MAX_SUBJECT_CHARS:
        note(None, "subject_too_long", detail=len(subject))
        return nothing()
    subject = inline(subject, limit=MAX_SUBJECT_CHARS)
    if not subject:
        note(None, "subject_empty_after_scrub")
        return nothing()

    async def search_round(attempted: list[dict] | None = None):
        prompt = kind.search_prompt(subject, known=known)
        if attempted:
            already = "\n".join(f"- {a['url']} -- {a['reason']}" for a in attempted if a.get("url"))
            prompt += kind.retry_prompt(already)
        return await call_and_record(
            ctx,
            kind.search_task,
            user_content=prompt,
            schema=kind.search_schema(),
            system_prompt=kind.search_system,
            model=ctx.settings.llm.acronym_model,
            effort=ctx.settings.llm.acronym_effort,
            # dontAsk would DENY these while still exiting 0, so
            # `llm/harness/claude_code.py` pairs them with --allowedTools and
            # hard-fails on permission_denials. Omitting this argument is the
            # whole reason this module exists.
            tools=("WebSearch", "WebFetch"),
            timeout=180,
            budget=budget,
        )

    async def try_candidates(candidates: list[str]):
        """Read down the list until READS_PER_LOOKUP pages have been extracted.

        Counting reads rather than candidates is the point. Taking
        `candidates[:3]` treats a 403 as one of its three attempts, so a lookup
        can spend two of three slots on servers that refuse us and ground
        nothing off the single page that remains.
        """
        # In `collect` mode the loop does not stop at the first hit, so this is
        # where every distinct meaning accumulates. Keyed by the normalised
        # answer: two pages spelling one meaning differently ("Unmanned Aerial
        # System" / "Unmanned aerial system") are corroboration, not a second
        # option, and offering both as choices is how the ambiguous queue filled
        # up with five spellings of one thing (2026-08-09).
        meanings: dict[str, tuple[str, str, str]] = {}
        reads = 0
        for url in candidates:
            if reads >= READS_PER_LOOKUP:
                break
            try:
                response = await ctx.http.get(url)
            except Exception as exc:  # noqa: BLE001 - a dead candidate is not a run failure
                note(url, "fetch_error", detail=f"{type(exc).__name__}: {exc}")
                continue
            if not getattr(response, "ok", False):
                note(url, "fetch_status", detail=getattr(response, "status", None))
                continue

            extracted = trafilatura.extract(response.text, include_comments=False)
            if not extracted or len(extracted) < 200:
                # A PDF lands here: trafilatura yields nothing from one, and DoD
                # glossaries are often published that way. Costs a candidate,
                # not the lookup.
                note(url, "no_extractable_text", detail=len(extracted or ""))
                continue
            extracted = extracted[:MAX_FETCH_CHARS]
            reads += 1

            read = await call_and_record(
                ctx,
                kind.read_task,
                user_content=kind.read_prompt(subject, extracted),
                schema=kind.read_schema(),
                system_prompt=kind.read_system,
                model=ctx.settings.llm.acronym_model,
                effort="low",
                tools=(),  # no tools: it may only read what we supplied
                timeout=120,
                budget=budget,
            )
            if not read.ok:
                note(url, "read_failed", detail=read.error)
                continue

            data = read.data or {}
            if not data.get("found"):
                note(url, kind.not_found_reason)
                continue

            answer = kind.answer_of(data)
            quote = (data.get("quote") or "").strip()
            if not answer or not quote:
                note(url, "incomplete", **kind.incomplete(answer))
                continue

            rejection = kind.reject(subject, answer, quote, extracted)
            if rejection is not None:
                # A structural refusal gets one appeal, and only the kinds that
                # opted in have one. Either verdict reaches the trace: a
                # definition accepted over a mechanical check is auditable only
                # while the grounds for accepting it are written down (ADR 0178).
                verdict = None
                if rejection.reason in kind.adjudicable:
                    verdict = await kind.adjudicate(ctx, subject, answer, quote, budget=budget)
                if verdict is None or not verdict[0]:
                    extra = dict(rejection.extra)
                    if verdict is not None:
                        extra["adjudged"] = verdict[1]
                    note(url, rejection.reason, **extra)
                    continue
                note(
                    url,
                    "adjudicated",
                    **{**rejection.extra, "overturned": rejection.reason, "how": verdict[1]},
                )

            accepted = (kind.finalize(answer), quote, url)
            if not collect:
                return accepted
            meanings.setdefault(normalise_ws(answer), accepted)
        return list(meanings.values()) if collect else None

    def urls_of(search) -> list[str]:
        seen: list[str] = []
        for candidate in (search.data or {}).get("candidate_urls", []):
            url = candidate.get("url")
            if url and url not in seen:
                seen.append(url)
        return seen[:CANDIDATES_PER_ROUND]

    search = await search_round()
    if not search.ok:
        # A failed search must NOT end the lookup here: the retry round below is
        # built for exactly this case, and returning early makes it unreachable.
        # The failure it catches is the model garbling its own structured output
        # -- a formatting slip rather than a verdict about the subject, which a
        # second attempt normally lands. One retry, and only when a budget has
        # not already run out.
        note(None, "search_failed", detail=search.error)
        if budget is not None:
            try:
                budget.check()
            except budget_mod.BudgetExceeded:
                return nothing()
        search = await search_round()
        if not search.ok:
            note(None, "search_failed_twice", detail=search.error)
            return nothing()

    candidates = urls_of(search)
    if not candidates:
        note(None, "no_candidates")
    tried = set(candidates)

    result = await try_candidates(candidates)
    if result:
        return result

    # One reframed retry, and only from here -- a lookup that grounded on its
    # first round never reaches this, so the happy path costs nothing extra. A
    # bad first search otherwise ended the attempt, and the first two live
    # lookups both died that way.
    if budget is not None:
        try:
            budget.check()
        except budget_mod.BudgetExceeded as exc:
            note(None, "retry_skipped", detail=str(exc))
            return nothing()

    retry = await search_round(attempted=list(trace or []))
    if not retry.ok:
        note(None, "retry_search_failed", detail=retry.error)
        return nothing()

    fresh = [u for u in urls_of(retry) if u not in tried]
    if not fresh:
        note(None, "retry_proposed_nothing_new")
        return nothing()

    return await try_candidates(fresh) or nothing()
