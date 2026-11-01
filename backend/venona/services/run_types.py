"""Contract C11 -- what kinds of run exist, and which lane each one runs in.

One declarative table. The registry, the API payloads and the whole runs UI all
read from it, so "what does a run of this kind look like" is answered in exactly
one place.

Two scars are why it is declarative, one per layer:

  * **A single global "is anything running?" slot.** Grounding one acronym was
    refused for the tens of minutes a research run takes -- which is precisely
    when someone is reading a brief and hits an undefined term. The hazard that
    one slot guarded against is real but narrow: two full pipelines writing
    `documents`/`clusters` with two separate budgets. It has nothing to say
    about a lookup that writes one row. Lanes answer it.
  * **A single hardcoded stage list in the UI.** The live panel drew six
    segments and a title reading "Research run" for a run that makes one model
    call, so a lookup rendered as a mostly-empty pipeline with counters that
    never moved. `RunType.stages`/`detail` answer it.

`RunType.outcomes` answers a third, per ADR 0183: **what does a run of this kind
CALL what it found.** `runs.outcome` holds four universal words, and "not found"
and "nothing new" are the same one read by two different kinds of reader.

**`RESEARCH_STAGES` is a copy of `orchestrator.STAGES`, deliberately.** Importing
it would close a cycle -- `orchestrator` imports this module for `type_for`.
`tests/test_run_lanes.py` asserts the two are equal, the same way
`test_the_ui_knows_every_stage` pins the frontend's copy. An enforced duplicate
beats an import cycle; an unenforced one is how `pipeline/types.py` ended up
with a nine-name `STAGES` that contradicted the real one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Mirror of `pipeline.orchestrator.STAGES`. Pinned by test, not by import --
# see the module docstring.
RESEARCH_STAGES: tuple[str, ...] = (
    "fetch",
    "analyze",
    "acronyms",
    "brief",
    "define",
    "curate",
)

# How many runs of each lane may execute at once.
#
# `research` is 1 because two pipelines write the same `documents` and
# `clusters` tables with two independent `Budget` objects, and the second one to
# finish decides what the corpus looks like.
#
# `light` is 3 because these runs write a single definition row each and are
# read-mostly otherwise. It is a bound on politeness, not on correctness -- the
# real throughput limit is the model semaphore in `llm/executor.py`.
LANES: dict[str, int] = {"research": 1, "light": 3}

RESEARCH = "research"
LIGHT = "light"

# Counters worth showing for a full pipeline run. A subset of the frozen C6
# list: `RunCounters` has thirteen fields and a run row that renders all of them
# is a wall of zeroes.
FULL_SUMMARY: tuple[str, ...] = (
    "documents_new",
    "clusters",
    "entities_new",
    "entities_defined",
    "edges_written",
    "llm_calls",
    "cost_usd",
)

LOOKUP_SUMMARY: tuple[str, ...] = ("llm_calls", "cost_usd")


# What each OUTCOME is called, in the reader's own words, for one kind of run.
#
# The outcome vocabulary is universal and four words wide; the phrase is not. A
# `lookup` that found nothing says "not found"; a `daily` that found nothing to
# write about says "nothing new". Both are `nothing_to_do` or `nothing_found` in
# the database, and neither of those is a phrase anybody says out loud.
#
# It ships to the UI through `as_payload` as `outcome_text`, because a second
# copy of the phrases in TypeScript would drift inside a release. ADR 0183.
#
# Three constraints these are written under, each with a scar behind it:
#
#   * No phrase contains the literal `undefined`. `scripts/ui_smoke.py` greps
#     every page it walks for it as its marker for a JS value rendered into the
#     DOM, and a legitimate label carrying the word disarms that check for the
#     whole page. This is why `lookup_all` is labelled "Glossary".
#   * No em or en dashes, and no internal vocabulary. Every one of these renders
#     on a screen a person reads (CLAUDE.md, `tests/test_copy_style.py`).
#   * A kind declares a phrase only for outcomes it can actually reach. A
#     `lookup` cannot return `nothing_to_do` -- there is always exactly one
#     surface to look up -- and inventing a phrase for it would be a lie
#     waiting to render.
OutcomePhrases = dict[str, str]


# What a run type SAYS it produced, and in what words. ADR 0187.
#
# Declared here rather than assembled at the render site: a sentence built in
# the component can only read keys off `stages[0].counts`, and nothing makes a
# kind emit the keys another kind's sentence needs. An if-chain over `stories`,
# `pages_read`, `considered` and `grounded` gave a kind that emitted none of
# them another kind's sentence, and a reader's brief rendered the lookup panel's
# `sourced: no` on a run that had just written a document. `run_facts` resolves
# both halves server-side.
@dataclass(frozen=True)
class Fact:
    """One line of a run's result panel, declared by the kind that emits it."""

    #: Where the value comes from, qualified. Three namespaces:
    #: `counts.` is the work's own returned metadata (`LaunchOutcome.data`, or a
    #: stage's `report.counts`, both persisted to `run_stages.counts`),
    #: `params.` is what the run was launched with, and `run.` is a column on
    #: the run row itself.
    #:
    #: NEVER `counters.` -- the thirteen frozen `RunCounters` are what `summary`
    #: is for, and naming one here would render the same number in two panels
    #: under two different labels.
    key: str
    #: Reader words. `entity`, `cluster`, `corpus` and the rest are this repo's
    #: vocabulary, not a reader's.
    label: str
    #: count  -- a number, as a stat block
    #: text   -- a string
    #: flag   -- a boolean, said in `words` rather than as yes/no
    #: url    -- an external address, linked and shown in full
    #: link   -- an internal route, `href` with `{value}` spliced in
    #: reader -- a user id, resolved to an address the way `started_by` is
    #: candidates -- a rejection trace, rendered by `RunCandidates`
    shape: str = "count"
    #: `shape="flag"`: what true and what false are called. A bare yes/no makes
    #: the reader work out what the question was.
    words: tuple[str, ...] = ()
    href: str | None = None


#: What a run says when it has no outcome to report.
#:
#: Keyed on STATUS, because `outcome` is null for every `failed` and every
#: `cancelled` run by design (C6) -- and, less obviously, for two succeeding
#: ones: `orchestrator` leaves it unset when no brief was asked for
#: (`--only fetch`, `skip_brief`), and sets it to None when an exception lands
#: after earlier stages already committed their work. `0018_run_outcome.sql`
#: also left it NULL on every row it could not classify. Keying the sentence on
#: `outcome` alone would leave all of those blank, which is most of what someone
#: clicking back through history actually lands on.
STATUS_LINES: dict[str, str] = {
    "failed": "The run could not finish.",
    "cancelled": "Someone stopped this run before it finished.",
    "succeeded": "The run finished. It was not asked for an answer, so it reports none.",
    "succeeded_partial": "The run got part of the way and stopped short of an answer.",
}


@dataclass(frozen=True)
class RunType:
    """One row of the table.

    `stages` is NOT a slice of `orchestrator.STAGES` -- a lookup's only stage is
    `lookup`, which the pipeline has never heard of and `venona run --only`
    must never accept. The two lists answer different questions: `STAGES` is
    "what can the pipeline be asked to do", this is "what will a run of this
    kind emit".
    """

    kind: str
    # One word. The label is a column value, and every second word it carries
    # is taken from the `subject`/`target` column sitting next to it.
    label: str
    #: One sentence saying what a run of this kind DOES, in the reader's words.
    #:
    #: The label alone cannot carry it. `Anchors` and `Glossary` are names, and
    #: a name is only useful to somebody who already knows what the thing is --
    #: which is nobody, the first time they open the menu. Declared here because
    #: a second copy at a call site is how the History column and the control
    #: that starts the run came to use different words for one job.
    description: str
    lane: str
    stages: tuple[str, ...]
    summary: tuple[str, ...]
    # How the run renders. `pipeline` gets the segmented stage spine; `single`
    # gets one status line, because five segments that never light read as five
    # stages that failed.
    detail: str
    # What started it: `scheduled` for the cron-driven kind, `on_demand` for
    # everything a person or a button launches. Kinds sharing a one-word label
    # (the three research flavours) stay distinguishable through this.
    trigger: str = "on_demand"
    # `run_service.OUTCOMES` value -> what this kind calls it. See above.
    outcomes: OutcomePhrases = field(default_factory=dict)
    #: What this kind's result panel shows, in order. See `Fact`.
    facts: tuple[Fact, ...] = ()
    #: `run_service.OUTCOMES` value -> candidate sentences, first fillable wins.
    #:
    #: Two rules make this total, both pinned by `tests/test_run_facts.py`. The
    #: LAST candidate carries no slots, so a kind that reaches an outcome always
    #: has something to say about it even on a run written before the keys
    #: existed. And a counted slot resolving to ZERO is not fillable, which is
    #: what keeps "Read 6 pages and none of them defined it" from becoming "Read
    #: 0 pages and none of them defined it" on a lookup that could not open a
    #: single page. Zero is a different sentence, not a smaller number.
    lines: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: Whether a run of this kind talks to the model, and therefore whether its
    #: page offers the session behind it. Declared rather than assumed so that a
    #: purely mechanical run type does not offer an empty log; `export` is the
    #: one kind that declares False.
    uses_model: bool = True
    #: Which `params.` key names what the run was ABOUT, for the history list.
    #: `RunsView`'s fallback infers it from param names, and that cascade matched
    #: none of a reader's brief, so those rows named nothing at all.
    subject: str | None = None
    #: Whether a clean run of this kind is worth saying out loud.
    #:
    #: `run.succeeded` is a dead-man's switch, so SILENCE is the alarm and a
    #: ping has to mean the reading happened: the three research flavours that
    #: write the brief, and nothing else. ADR 0242 carries the two near-misses.
    heartbeat: bool = False
    #: Whether the generic failure notice covers this kind.
    #:
    #: True for almost everything: a run that ends badly says so once, from
    #: `run_launcher`, and nothing else has to remember to. False is for a kind
    #: that ANNOUNCES ITS OWN ENDING, where the generic notice would be the
    #: second message about one event and the worse-written of the two -- "A run
    #: did not finish" against copy that can name what the run was for. See
    #: `api/routers/data.py`'s "BOTH ENDINGS ARE ANNOUNCED" block.
    reports_failure: bool = True

    @property
    def queued_slots(self) -> int:
        return LANES.get(self.lane, 1)


# The brief is the artifact, for all three research flavours. A window with
# nothing new in it is a correct result the pipeline names for itself
# (`brief.py::_empty_retrieval_reason`, distinct from `quiet_day`, which means
# the brief was written from few strong clusters) and which had no field on the
# run row to report itself in but "degraded". ADR 0183.
RESEARCH_OUTCOMES: OutcomePhrases = {
    "delivered": "brief written",
    "nothing_to_do": "nothing new",
}

# What a research run made. `run.brief_id`, not `counts.brief_id`: the brief
# stage puts its id in `report.payload`, which reaches the event stream, while
# `report.counts` carries the shape of the document instead. The id that
# survives to the run row is the column.
RESEARCH_FACTS: tuple[Fact, ...] = (
    Fact("run.brief_id", "the brief", shape="link", href="/briefs/{value}"),
    Fact("counts.clusters_used", "stories it drew on"),
    Fact("counts.blocks", "paragraphs written"),
    Fact("counts.spans", "hoverable mentions"),
)

RESEARCH_LINES: dict[str, tuple[str, ...]] = {
    "delivered": ("Read the day and wrote a brief.",),
    "nothing_to_do": ("Nothing new came in that was worth writing about.",),
}

# A definition either landed or it did not. `nothing_to_do` is unreachable:
# there is always exactly one thing to look up.
LOOKUP_OUTCOMES: OutcomePhrases = {
    "delivered": "defined",
    "nothing_found": "not found",
}

# One reference, and the audit trail behind it. Invariant 3: a definition exists
# only if it is traceable to a sentence in a document, so the address it came
# from is a fact about the answer rather than a detail about the run.
LOOKUP_FACTS: tuple[Fact, ...] = (
    Fact("params.surface", "term", shape="text"),
    Fact("counts.expansion", "what it means", shape="text"),
    Fact("counts.url", "sourced from", shape="url"),
    Fact("counts.pages_read", "pages read"),
    Fact("counts.corpus_sources", "documents that agree"),
    Fact("counts.rejected", "also considered", shape="candidates"),
)

LOOKUP_LINES: dict[str, tuple[str, ...]] = {
    "delivered": (
        "Read {counts.pages_read|page|pages} and one of them defined it.",
        "Found a meaning and a source for it.",
    ),
    # Three sentences for one outcome, and the ordering is the whole point: a
    # lookup that read six pages and a lookup that could not open one are the
    # same outcome and a different fact about the world. The zero rule (see
    # `lines`) is what makes the first candidate step aside for the second.
    "nothing_found": (
        "Read {counts.pages_read|page|pages} and none of them defined it.",
        "{?counts.pages_read}No page could be read, so the question is still open.",
        "The work ran and the answer was no.",
    ),
}

# A sweep over a queue, so it has a middle: some of the queue landed.
SWEEP_OUTCOMES: OutcomePhrases = {
    "delivered": "all defined",
    "some": "some defined",
    "nothing_found": "none found",
    "nothing_to_do": "nothing to define",
}

# A sweep's numbers, including the one it computes so nobody has to divide two
# others in their head: `grounded_pct` is the headline this feature is judged
# on, rounded where it is produced rather than left to the reader.
SWEEP_FACTS: tuple[Fact, ...] = (
    Fact("counts.considered", "looked at"),
    Fact("counts.grounded", "sourced"),
    Fact("counts.grounded_pct", "sourced, as a percentage"),
    # Genuinely a degradation rather than an answer: work the run was asked to
    # do and did not attempt, because the budget ran out. It warns as well.
    Fact("counts.deferred", "left for the budget"),
    Fact("counts.retry", "retried the ones that failed before", shape="flag", words=("yes", "no")),
)

#: A placement is never applied by the run, so `delivered` means "there is
#: something to review", not "the graph changed". Saying it any other way would
#: promise a reader work that has not happened yet.
ANCHOR_OUTCOMES: OutcomePhrases = {
    "delivered": "placements to review",
    "some": "some placements",
    "nothing_found": "no placements",
    "nothing_to_do": "everything attached",
}

ANCHOR_LINES: dict[str, tuple[str, ...]] = {
    "delivered": (
        "Found {counts.floating|name|names} with nothing attached and suggested "
        "a place for every one. They are waiting for you to decide.",
        "Suggested a place for everything it found. They are waiting for you to decide.",
    ),
    "some": (
        "Suggested a place for {counts.proposed} of {counts.floating}. "
        "It had no suggestion for the rest.",
        "Suggested a place for some of what it found, and had none for the rest.",
    ),
    "nothing_found": (
        "Looked at {counts.floating|name|names} with nothing attached and had no "
        "suggestion for any of them.",
        "It found nothing it could place.",
    ),
    "nothing_to_do": ("Everything already has something attached to it.",),
}

#: Re-reading stored articles is not looking something up, so the words differ
#: from `SWEEP_OUTCOMES`: `nothing_found` means the articles were read and none
#: of them described anything, which is a fact about the corpus rather than a
#: failure of the reading.
REDEFINE_OUTCOMES: OutcomePhrases = {
    "delivered": "all explained",
    "some": "some explained",
    "nothing_found": "nothing to explain them",
    "nothing_to_do": "everything already explained",
}

REDEFINE_FACTS: tuple[Fact, ...] = (
    Fact("counts.documents_seen", "articles it went back to"),
    Fact("counts.documents_read", "articles it read again"),
    Fact("counts.entities_seen", "names it was looking for"),
    Fact("counts.defined", "names it can now explain"),
    Fact("counts.unanswered", "names the articles never explain"),
    Fact("counts.deferred", "articles left for the budget"),
)

REDEFINE_LINES: dict[str, tuple[str, ...]] = {
    "delivered": (
        "Went back over {counts.documents_seen|article|articles} and can now explain "
        "{counts.defined|name|names} it could not before.",
        "Read what was already stored again and came back able to explain more of it.",
    ),
    "some": (
        "Can now explain {counts.defined} of {counts.entities_seen}. The articles "
        "never say what the rest are.",
        "Explained some of the names it went looking for. The articles do not say "
        "what the rest are.",
    ),
    "nothing_found": (
        "Read {counts.documents_seen|article|articles} again and found nothing in "
        "them that says what these names are.",
        "Read what was stored again and it explains none of them.",
    ),
    "nothing_to_do": ("Every name Venona can reach already has an explanation.",),
}

SWEEP_LINES: dict[str, tuple[str, ...]] = {
    "delivered": (
        "Looked at {counts.considered|term|terms} and sourced every one.",
        "Sourced every term it looked at.",
    ),
    "some": (
        "Sourced {counts.grounded} of {counts.considered}. The rest are still open.",
        "Sourced some of the terms it looked at. The rest are still open.",
    ),
    "nothing_found": (
        "Looked at {counts.considered|term|terms} and sourced none of them.",
        "The work ran and the answer was no.",
    ),
    "nothing_to_do": ("There was nothing waiting to be looked up.",),
}

# A deep read answers a different question from a research run's. Not "was there
# anything worth writing about today" but "was there anything left to read" --
# so `nothing_to_do` is an empty queue rather than a quiet day, and
# `nothing_found` is the real middle case: it read the articles and took nothing
# from them. `some` is unreachable; it reads what it is given and reports one
# answer over the lot.
BACKFILL_OUTCOMES: OutcomePhrases = {
    "delivered": "more read",
    "nothing_found": "nothing worth keeping",
    "nothing_to_do": "nothing left to read",
}

# Keys verified against the stage report dataclasses rather than guessed.
# `documents_new` is only non-zero when the run went looking, and the zero rule
# drops a count that is zero -- so the same declaration renders both modes
# correctly without either knowing about the other.
BACKFILL_FACTS: tuple[Fact, ...] = (
    Fact("counts.clusters_analyzed", "stories it read"),
    Fact("counts.entities_minted", "names it had not seen before"),
    Fact("counts.relations_written", "relationships it recorded"),
    Fact("counts.documents_new", "articles it brought back"),
    Fact("counts.grounded_web", "short forms it explained"),
    Fact("counts.clusters_deferred", "stories left for next time"),
)

BACKFILL_LINES: dict[str, tuple[str, ...]] = {
    "delivered": (
        "Read {counts.clusters_analyzed|story|stories} and came back with "
        "{counts.entities_minted|name|names} Venona had not seen before.",
        "Read {counts.clusters_analyzed|story|stories} and added to what Venona knows.",
        "Read more of what was waiting and learned from it.",
    ),
    "nothing_found": (
        "Read {counts.clusters_analyzed|story|stories} and found nothing in them worth keeping.",
        "Read what was waiting and took nothing from it.",
    ),
    "nothing_to_do": ("There was nothing waiting to be read.",),
}


# What a pass over the queue either settled or did not. `nothing_to_do` is an
# empty queue, which is the goal rather than a disappointment; `nothing_found`
# is a queue it read and could not settle, which is the honest answer when every
# remaining pair is a judgement call.
ADJUDICATE_OUTCOMES: OutcomePhrases = {
    "delivered": "decisions made",
    "some": "some decided",
    "nothing_found": "all left for you",
    "nothing_to_do": "nothing waiting",
}

ADJUDICATE_FACTS: tuple[Fact, ...] = (
    Fact("counts.suggested", "names it read"),
    Fact("counts.merged", "names it joined up"),
    Fact("counts.merged_by_model", "names it joined on the model's reading"),
    Fact("counts.kept_apart_by_model", "pairs it ruled out"),
    Fact("counts.conflicts_open", "still waiting for you"),
)

ADJUDICATE_LINES: dict[str, tuple[str, ...]] = {
    "delivered": (
        "Read the questions waiting about names and settled "
        "{counts.kept_apart_by_model|one|of them}.",
        "Worked through the questions waiting about names.",
    ),
    "some": (
        "Settled some of what was waiting and left {counts.conflicts_open} for you.",
        "Settled some of what was waiting and left the rest for you.",
    ),
    "nothing_found": ("Read what was waiting and every one of them needs you to decide.",),
    "nothing_to_do": ("There was nothing waiting to decide.",),
}


#: An export either wrote a folder or found nothing worth writing. It cannot
#: return `some` or `nothing_found`: it does not go looking for anything, so
#: there is no search to come back half full.
EXPORT_OUTCOMES: OutcomePhrases = {
    "delivered": "file ready",
    "nothing_to_do": "nothing to write",
}

EXPORT_LINES: dict[str, tuple[str, ...]] = {
    "delivered": (
        "Wrote {counts.notes|note|notes} into a folder you can open in Obsidian.",
        "Wrote a folder you can open in Obsidian.",
    ),
    "nothing_to_do": ("There was nothing to write out yet.",),
}


RUN_TYPES: dict[str, RunType] = {
    "daily": RunType(
        kind="daily",
        label="Research",
        description="Reads the morning's press and writes the brief.",
        lane=RESEARCH,
        stages=RESEARCH_STAGES,
        summary=FULL_SUMMARY,
        detail="pipeline",
        trigger="scheduled",
        outcomes=RESEARCH_OUTCOMES,
        facts=RESEARCH_FACTS,
        lines=RESEARCH_LINES,
        subject="run.brief_id",
        heartbeat=True,
    ),
    "on_demand": RunType(
        kind="on_demand",
        label="Research",
        description="Reads the press now and writes a brief, without waiting for the morning.",
        lane=RESEARCH,
        stages=RESEARCH_STAGES,
        summary=FULL_SUMMARY,
        detail="pipeline",
        outcomes=RESEARCH_OUTCOMES,
        facts=RESEARCH_FACTS,
        lines=RESEARCH_LINES,
        subject="run.brief_id",
        heartbeat=True,
    ),
    # Same label as `on_demand` on purpose: a research run started from the
    # terminal is the same work started from the button, and `trigger` already
    # separates the scheduled flavour, which is the distinction that matters.
    "cli": RunType(
        kind="cli",
        label="Research",
        description="Reads the press and writes a brief, started from a terminal.",
        lane=RESEARCH,
        stages=RESEARCH_STAGES,
        summary=FULL_SUMMARY,
        detail="pipeline",
        outcomes=RESEARCH_OUTCOMES,
        facts=RESEARCH_FACTS,
        lines=RESEARCH_LINES,
        subject="run.brief_id",
        heartbeat=True,
    ),
    # `venona research` -- targeted corroboration sweeps over named entities,
    # then the analyze stage over whatever they turned up.
    "research": RunType(
        kind="research",
        # Named for the NUMBER IT CLOSES, not for the thing it sweeps. `/graph`
        # tells a reader that 1,276 relationships (2026-09-12) are hidden until
        # a second source confirms both names, and this is the job that goes
        # looking for one.
        label="Find a second publisher",
        description=(
            "Goes looking for another publisher on the relationships only one"
            " source has reported, worst-blocked names first."
        ),
        lane=RESEARCH,
        # ONE stage, for the reason `ingest` gives above: C11 says `stages` is
        # what a run of this kind will EMIT, and the launcher opens exactly one
        # for it. The sweep does analyse what it brings back, but inside that
        # single stage -- so the analyze half reports itself through
        # `stage_progress`, which `run_launcher` re-homes onto the stage that is
        # actually open. Declared as two, the second segment sat grey for the
        # length of every run and the header counted `1/2` to the end.
        stages=("research",),
        summary=("documents_new", "entities_new", "llm_calls", "cost_usd"),
        # A one-segment spine says nothing a status line does not.
        detail="single",
        # A corroboration sweep writes no brief. What it goes looking for is a
        # SECOND publisher on a relationship only one source has claimed, so
        # "coverage" is the word for what it either found or did not.
        outcomes={"delivered": "coverage found", "nothing_found": "no coverage"},
        facts=(
            Fact("params.entities", "names it went looking for"),
            Fact("counts.targeted", "names it swept"),
            Fact("counts.documents_new", "articles it brought back"),
            Fact("counts.promoted", "relationships that gained a second source"),
        ),
        lines={
            "delivered": (
                "Swept {counts.targeted|name|names} and found other publishers covering them.",
                "Found other coverage of what it went looking for.",
            ),
            "nothing_found": (
                "Swept {counts.targeted|name|names} and nobody else was covering them.",
                "Nobody else was covering what it went looking for.",
            ),
        },
        # NOT a heartbeat, for both of the reasons `backfill` gives. It is
        # started by hand, so its pings put gaps in the dead-man's switch that
        # mean nothing -- and `run.succeeded`'s unscheduled copy reads "Venona
        # read, scored and wrote the brief you asked for", which is a sentence
        # about a brief this kind does not write. Its own `outcomes` above say
        # what it actually did.
        heartbeat=False,
    ),
    # The reading, without the writing. Reaches further back than a brief may --
    # see ADR 0228 -- and turns what it finds into names and relationships
    # rather than into a document. Its stage list has no `brief` in it at all,
    # which is what lets `orchestrator` claim a reading outcome for it and for
    # nothing else.
    "backfill": RunType(
        kind="backfill",
        label="Read what is waiting",
        description=(
            "Reads articles Venona fetched and never got to, however old. Writes no brief."
        ),
        lane=RESEARCH,
        stages=("fetch", "analyze", "acronyms", "define", "curate"),
        summary=FULL_SUMMARY,
        detail="pipeline",
        # NOT `heartbeat`. The daily ping is a dead-man's switch, so it only
        # means anything while it means "the reading happened"; a kind somebody
        # starts by hand would put gaps in it that mean nothing.
        heartbeat=False,
        outcomes=BACKFILL_OUTCOMES,
        facts=BACKFILL_FACTS,
        lines=BACKFILL_LINES,
        subject="params.window_text",
    ),
    # The curation queue, worked by the machine rather than by a person. Its
    # only stage is `curate`, which classifies what is open, applies what is
    # provable, asks the model about the rest and applies the verdicts that
    # carry -- so this kind is the stage, started on its own.
    "adjudicate": RunType(
        kind="adjudicate",
        label="Settle duplicate names",
        description=(
            "Works through the names that might be two spellings of one thing,"
            " and settles what it can."
        ),
        lane=RESEARCH,
        stages=("curate",),
        summary=("llm_calls", "cost_usd"),
        detail="single",
        heartbeat=False,
        outcomes=ADJUDICATE_OUTCOMES,
        facts=ADJUDICATE_FACTS,
        lines=ADJUDICATE_LINES,
    ),
    # One reference, grounded on the web. Covers BOTH the acronym and the entity
    # endpoint: they are near-copies doing the same thing to two kinds of
    # reference, and splitting the kind would mislabel every historical row --
    # both have written `kind="lookup"` since the day they shipped. The target
    # is in `params`.
    "lookup": RunType(
        kind="lookup",
        label="Definition",
        description="Finds what one short form stands for, and where that was said.",
        lane=LIGHT,
        stages=("lookup",),
        summary=LOOKUP_SUMMARY,
        detail="single",
        outcomes=LOOKUP_OUTCOMES,
        facts=LOOKUP_FACTS,
        lines=LOOKUP_LINES,
        subject="params.surface",
    ),
    # Study-question generation (epic O). LIGHT: it writes `llm_questions`
    # rows and nothing else -- no documents, no clusters, no assertions, no
    # brief -- so it must not contend for the research slot someone's daily
    # run is holding.
    "questions": RunType(
        kind="questions",
        label="Write study questions",
        description="Writes study questions from what Venona has read.",
        lane=LIGHT,
        stages=("questions",),
        summary=("llm_calls", "cost_usd"),
        detail="single",
        outcomes={
            "delivered": "questions added",
            "some": "some added",
            "nothing_found": "none usable",
            "nothing_to_do": "nothing to read",
        },
        facts=(
            Fact("counts.articles", "articles read"),
            Fact("counts.generated", "questions kept"),
            # An int here, not the candidate list a lookup puts under the same
            # name. Declaring the shape per kind is what stops one panel
            # guessing it from the key.
            Fact("counts.rejected", "questions dropped"),
            Fact("counts.failed_calls", "calls that did not come back"),
        ),
        lines={
            "delivered": (
                "Read {counts.articles|article|articles} and kept every question they suggested.",
                "Kept every question the reading suggested.",
            ),
            "some": (
                "Kept {counts.generated} questions and dropped {counts.rejected}.",
                "Kept some of the questions the reading suggested.",
            ),
            "nothing_found": (
                "Read {counts.articles|article|articles} and none of them helped.",
                "Nothing usable came back.",
            ),
            "nothing_to_do": ("There was nothing new to read.",),
        },
    ),
    # One reader's own brief (personal relevance). LIGHT for the same reason
    # as `questions`: it writes `personal_briefs` rows and nothing else -- no
    # documents, no clusters, no shared brief -- so it must not contend for
    # the research slot. The reader is in `params`, which is also what keys
    # the launcher's coalescing: a double-click folds, two different readers
    # run side by side.
    "personal_brief": RunType(
        kind="personal_brief",
        label="Reader brief",
        description="Writes one reader's own brief from the day's reading.",
        lane=LIGHT,
        stages=("personal_brief",),
        summary=("llm_calls", "cost_usd"),
        detail="single",
        # "already current" rather than "nothing new": the reader's day may be
        # full of stories, already written, which is a different fact from the
        # shared brief's quiet day.
        outcomes={"delivered": "brief written", "nothing_to_do": "already current"},
        facts=(
            Fact("counts.stories", "stories written about"),
            # WHOSE brief this was. No route addresses another reader's brief --
            # it renders at `/` for that reader alone -- so the useful fact is
            # the person, not a link. A bare user id is a database value and
            # never renders; `reader` resolves it to an address the way
            # `started_by` already is.
            Fact("params.user_id", "written for", shape="reader"),
        ),
        lines={
            "delivered": (
                "Wrote about {counts.stories|story|stories} for this reader.",
                "Wrote this reader their own brief.",
            ),
            "nothing_to_do": (
                "This reader's brief already covers the latest reading, from the same inputs.",
            ),
        },
        # The ADDRESS, not `params.user_id`. A history row is a surface a
        # person reads, and `user:0193c8...` is a database value; the list has
        # no connection to resolve one per row and should not grow a query per
        # row to get it. NULL for a brief the warm pass generated, which has no
        # person behind it, and the row falls through to naming nothing rather
        # than naming an id.
        subject="run.started_by_email",
    ),
    # One article, named by URL, from the browser extension. RESEARCH lane: it
    # writes `documents` and `clusters`, which is exactly what the single
    # research slot guards -- so it is refused while a pipeline run is in
    # flight rather than racing it.
    #
    # ONE stage, not ("fetch", "analyze"). C11 is explicit that `stages` is
    # "what a run of this kind will EMIT", and the launcher opens exactly one
    # for it; the fetch half reports itself through `stage_progress` rather than
    # as a stage of its own. Declaring two and emitting none leaves the result
    # panel reading `stages[0]` of an empty list.
    "ingest": RunType(
        kind="ingest",
        label="Ingest",
        description="Reads one page you named and files what it says.",
        lane=RESEARCH,
        stages=("ingest",),
        summary=("documents_new", "entities_new", "llm_calls", "cost_usd"),
        detail="single",
        outcomes={"delivered": "article added", "nothing_to_do": "already had it"},
        facts=(
            Fact("counts.title", "article", shape="text"),
            Fact("counts.url", "read from", shape="url"),
            Fact("counts.is_new", "document", shape="flag", words=("new", "already held")),
            Fact(
                "counts.analyzed",
                "read for names and relationships",
                shape="flag",
                words=("yes", "no"),
            ),
            Fact("counts.entities_new", "names new to Venona"),
            Fact("counts.entities_seen", "names it already knew"),
        ),
        lines={
            "delivered": (
                "Added the article and found {counts.entities_new|name|names} new to Venona.",
                "Added the article.",
            ),
            "nothing_to_do": ("Venona already held this article.",),
        },
        subject="params.url",
    ),
    # The domain-setup generation run: research the described domain, draft
    # every config document for review. Research lane by C11's own test -- it
    # is long, budget-carrying, and its output (once applied) redefines what
    # every later run does; two racing would produce two answers to one
    # question. Writes only drafts and the dossier, never a live document.
    "domain_setup": RunType(
        kind="domain_setup",
        label="Setup",
        description="Researches a new subject and writes the configuration for it.",
        lane=RESEARCH,
        stages=("survey", "sources", "ontology", "profile", "anchors"),
        summary=("llm_calls", "cost_usd"),
        detail="pipeline",
        outcomes={
            "delivered": "drafts ready",
            "some": "some drafts",
            "nothing_found": "no drafts",
            "nothing_to_do": "nothing to draft",
        },
        facts=(
            Fact("counts.drafted", "documents drafted"),
            Fact("counts.failed", "documents it could not draft"),
        ),
        lines={
            "delivered": (
                "Drafted {counts.drafted|document|documents} for review.",
                "Drafted the settings for review.",
            ),
            "some": (
                "Drafted {counts.drafted} documents and could not draft {counts.failed}.",
                "Drafted some of the settings and could not draft the rest.",
            ),
            "nothing_found": ("The research came back with nothing to draft from.",),
            "nothing_to_do": ("There was nothing left to draft.",),
        },
    ),
    # Re-runs the brief stage alone over the current window. Research lane: the
    # brief is the only opus call in the system, at high effort, and it writes
    # the artefact a pipeline run is trying to write.
    "rewrite": RunType(
        kind="rewrite",
        label="Rewrite",
        description="Writes a brief again from reading that is already in.",
        lane=RESEARCH,
        stages=("brief",),
        summary=("clusters", "llm_calls", "cost_usd"),
        # `single`, because it emits one stage: `LiveRunPanel` gives a one-stage
        # run a status line, not a spine, and a one-segment spine states
        # something false. `test_detail_says_nothing_the_stage_count_does_not`
        # holds `detail` against `len(stages)` for every kind.
        detail="single",
        outcomes={"delivered": "brief rewritten", "nothing_to_do": "nothing new"},
        facts=(
            Fact("run.brief_id", "the new brief", shape="link", href="/briefs/{value}"),
            Fact("params.rewrite_of", "rewritten from", shape="link", href="/briefs/{value}"),
            Fact("counts.clusters_used", "stories it drew on"),
            Fact("counts.blocks", "paragraphs written"),
        ),
        lines={
            "delivered": ("Wrote the brief again over the same window.",),
            "nothing_to_do": ("Nothing new came in since the brief it was asked to replace.",),
        },
        subject="params.rewrite_of",
    ),
    # The whole undefined ACRONYM queue, in one run. Row by row is the only
    # other way through it, and the queue stood at about three hundred rows on
    # 2026-08-09.
    #
    # **Light lane, despite being the longest-running kind in it**, because the
    # lane asks what a run WRITES rather than how long it takes: this writes
    # `acronym_definitions` rows and nothing else. C11 carries the rest of the
    # argument; the cost it does carry is one of three light slots for as long
    # as it runs, which is why the button in front of it counts the queue and
    # quotes a price first.
    "lookup_all": RunType(
        kind="lookup_all",
        # NOT anything containing the word "undefined", even though that is
        # what the sweep is over: `ui_smoke` greps `/admin/runs` for the literal
        # `undefined` as its marker for a JS value rendered into the DOM, and a
        # run label containing the word disarms that check for every row on the
        # page. "Glossary" because the sweep's unit is the whole glossary
        # queue, where `Definition` is one entry of it.
        label="Explain short forms",
        description="Finds meanings for every short form still waiting for one.",
        lane=LIGHT,
        stages=("lookup",),
        summary=("acronyms_grounded", "acronyms_undefined", "llm_calls", "cost_usd"),
        outcomes=SWEEP_OUTCOMES,
        # `single`, like `definitions`: one stage, so one status line. The
        # per-surface progress arrives as `stage_progress` events rather than as
        # segments, because a spine with one segment says nothing a status line
        # does not.
        detail="single",
        facts=SWEEP_FACTS,
        lines=SWEEP_LINES,
    ),
    # The corpus read back for what it already says. `definitions` asks the WEB
    # about names a brief left unexplained; this asks the articles Venona has
    # already fetched, which is both free where a sentence parses and cheaper
    # where it does not -- one call per article rather than one search plus
    # three page fetches per name.
    #
    # LIGHT, on the lane rule as C11 states it: the lane asks what a run
    # WRITES. This writes `entity_definitions` and stamps `definition_quote`
    # onto assertions it does not otherwise touch, so it cannot collide with a
    # pipeline the way a second `documents`/`clusters` writer would. It is
    # `lookup_all`'s shape -- long, resumable, holding one of three slots.
    "redefine": RunType(
        kind="redefine",
        label="Explain names from stored articles",
        description=(
            "Goes back over articles Venona already holds for the sentences that say "
            "what a name is."
        ),
        lane=LIGHT,
        stages=("define",),
        summary=("entities_defined", "llm_calls", "cost_usd"),
        detail="single",
        heartbeat=False,
        outcomes=REDEFINE_OUTCOMES,
        facts=REDEFINE_FACTS,
        lines=REDEFINE_LINES,
    ),
    # Tier-3 lookups for whatever the latest brief still leaves undefined.
    "definitions": RunType(
        kind="definitions",
        # The two definition runs sit next to each other in one menu and differ
        # in exactly one thing a reader cares about: one is free and one buys a
        # web search per name. Labels that both read as "definitions" sent a
        # reader to the paid one for work the free one does, which is a defect
        # of the words rather than of either run.
        label="Search the web for names",
        description=(
            "Looks for a source that says what a name is, when no stored article does. "
            "A search and up to three page reads for each one."
        ),
        lane=LIGHT,
        stages=("lookup",),
        summary=("entities_defined", "llm_calls", "cost_usd"),
        detail="single",
        outcomes=SWEEP_OUTCOMES,
        facts=(
            Fact("counts.considered", "looked at"),
            Fact("counts.grounded", "sourced"),
            # A COUNT, not a trace: `refresh_definitions` increments an integer,
            # where a `lookup` hands back the candidates it turned down. Declaring
            # the trace shape here dropped the number from both branches of
            # `RunFacts.vue` -- excluded from the plain list for being candidates,
            # excluded from the traces for not being an array.
            Fact("counts.rejected", "still unexplained"),
        ),
        lines=SWEEP_LINES,
    ),
    "anchors": RunType(
        kind="anchors",
        # The same word the Admin rail already uses for the document this run
        # proposes edits to. Two names for one thing is worse than a word the
        # reader has to learn once.
        label="Place loose names",
        description=(
            "Suggests where names with nothing attached to them belong. You decide each one."
        ),
        lane=LIGHT,
        stages=("anchors",),
        summary=("llm_calls", "cost_usd"),
        detail="single",
        outcomes=ANCHOR_OUTCOMES,
        facts=(
            Fact("counts.floating", "with nothing attached"),
            Fact("counts.proposed", "placements to review"),
        ),
        lines=ANCHOR_LINES,
    ),
    # Writing the whole reading out as a folder of linked notes.
    #
    # LIGHT, though it writes no definition row and nothing else in this lane
    # looks like it. The lane asks what a run WRITES, and this one writes
    # nothing into the corpus at all -- so the research slot's hazard, two
    # pipelines over one `documents` table, cannot reach it. Light leaves the
    # nightly run alone, keeps two of three slots free for the lookups a reader
    # triggers mid-brief, and hands the second press of one button the run that
    # is already going instead of a second identical file.
    "export": RunType(
        kind="export",
        label="Export",
        description="Writes everything Venona has read into a folder you can open elsewhere.",
        lane=LIGHT,
        stages=("export",),
        # EMPTY, and it has to be. The thirteen frozen counters are all about
        # what was read and what it cost, and this run reads nothing new and
        # spends nothing -- every one of them would render a column that can
        # only ever hold zero.
        summary=(),
        detail="single",
        outcomes=EXPORT_OUTCOMES,
        facts=(
            # An SPA route, NOT the download address. `FactList.vue` renders a
            # `link` through `RouterLink`, so an `/api/...` href would be a
            # client-side navigation to a page that does not exist and no
            # request would ever be made. The data screen is where the file is
            # listed and where whether it still exists is actually known -- the
            # fragment is the row, which `DataView` carries as an element id.
            Fact("counts.export_name", "the file", shape="link", href="/admin/data#{value}"),
            Fact("counts.notes", "notes written"),
            Fact("counts.names", "names"),
            Fact("counts.terms", "terms"),
            Fact("counts.relationships", "relationships"),
            Fact("counts.sources", "sources"),
            Fact("counts.briefs", "briefs"),
        ),
        lines=EXPORT_LINES,
        # No model call at any point, declared rather than left to the default
        # so the detail page does not offer a session log with nothing in it.
        uses_model=False,
        # This kind announces BOTH its endings itself, as `export.finished` --
        # see the "BOTH ENDINGS ARE ANNOUNCED" block in `api/routers/data.py`.
        # The generic notice would be a second message about one event, and the
        # worse of the two: it can only say "a run did not finish" where the
        # export's own copy names what the run was for.
        reports_failure=False,
    ),
}


# A row whose `kind` this table does not know. Not an error: `services/seed.py`
# writes assertions under `run:seed-0001` with no `runs` row at all, kinds have
# been added twice without a migration, and a history table that raises on an
# old row is worse than one that renders it plainly.
#
# It falls back to `research`/`pipeline` because that is the conservative
# answer to both questions it is used for: an unknown kind should not be handed
# a light-lane slot, and it should not be assumed to have exactly one stage.
def _fallback(kind: str) -> RunType:
    return RunType(
        kind=kind,
        label=kind or "run",
        # Empty for the same reason the phrases below are: a kind this build has
        # never heard of cannot be explained, and a sentence invented about one
        # would be a guess presented as a description.
        description="",
        lane=RESEARCH,
        stages=(),
        summary=FULL_SUMMARY,
        detail="pipeline",
        # No phrases, deliberately. An unknown kind's outcome cannot be put
        # into words nobody wrote, and the surfaces fall back to the bare
        # status rather than to an invented sentence. `facts` and `lines` are
        # empty for the same reason: there is no table saying what this kind
        # emits, so anything rendered would be a guess at another kind's shape.
        # That is the failure this whole declaration exists to end.
        outcomes={},
        facts=(),
        lines={},
    )


def type_for(kind: str) -> RunType:
    """The run type for a `runs.kind` value. Never raises."""
    return RUN_TYPES.get(kind) or _fallback(kind)


def lane_for(kind: str) -> str:
    return type_for(kind).lane


def kinds_in_lane(lane: str) -> tuple[str, ...]:
    """Every known kind assigned to a lane.

    Used by the cross-process guards, which have to ask the question in SQL --
    `runs` stores `kind`, and the lane is derived, so `WHERE kind IN (...)` is
    how a query filters by lane. An unknown kind is not in this list and is
    therefore treated as research by `lane_for`; the guards apply the same
    fallback in Python rather than relying on the IN clause alone.
    """
    return tuple(t.kind for t in RUN_TYPES.values() if t.lane == lane)


def as_payload() -> dict:
    """What `GET /api/runs/types` returns, and what the UI renders from."""
    return {
        "types": [
            {
                "kind": t.kind,
                "label": t.label,
                "description": t.description,
                "lane": t.lane,
                "stages": list(t.stages),
                "summary": list(t.summary),
                "detail": t.detail,
                "trigger": t.trigger,
                "outcomes": dict(t.outcomes),
                # The only half of the declaration the client needs: `facts` and
                # `lines` are resolved server-side and reach a row already in
                # words, but whether to OFFER the model session is a question
                # about the kind rather than about any one run.
                "uses_model": t.uses_model,
            }
            for t in RUN_TYPES.values()
        ],
        "lanes": dict(LANES),
    }
