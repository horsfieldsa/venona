"""System prompt assembly.

Two things matter here and they pull in the same direction: the prefix must be
STABLE across every call in a run, and it must be OURS rather than Claude Code's
default.

*Stable* because prompt caching is the dominant cost lever — a baseline call
measured 2026-08-06 carried ~9.2k tokens of default system prompt, and any
per-document data in the prefix breaks the cache for every subsequent call. So: no timestamps, no
run ids, no document text, and a deterministically sorted gazetteer.

*Ours* because `--system-prompt-file` replaces the default rather than appending
to it, which removes that 9.2k overhead entirely.

The extraction guide is GENERATED from the ontology, not written by hand. The
guidance the model reads and the rules the writer enforces therefore cannot
disagree — which is the failure mode a hand-written guide always eventually hits.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from venona.db import core as db
from venona.db.repositories import prompts as repo
from venona.domain import get_domain
from venona.ontology import get_ontology
from venona.settings import Settings, get_settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Profile:
    """The user-editable inputs, read fresh each run."""

    relevance: str
    research: str
    brief: str
    rubric_prose: str
    profile_hash: str

    @property
    def short_hash(self) -> str:
        return self.profile_hash[:12]


def _read(path: Path) -> str:
    return path.read_text().strip() if path.exists() else ""


def load_profile(settings: Settings | None = None) -> Profile:
    settings = settings or get_settings()
    directory = settings.profile_dir

    relevance = _read(directory / "relevance_profile.md")
    research = _read(directory / "research_prompt.md")
    brief = _read(directory / "brief_prompt.md")
    rubric = _read(directory / "scoring_rubric.yaml")

    # Identifies which profile a run was scored under, so a cache miss or a
    # ranking change is attributable to a specific edit.
    digest = hashlib.sha256("\0".join([relevance, research, brief, rubric]).encode()).hexdigest()

    return Profile(
        relevance=relevance,
        research=research,
        brief=brief,
        rubric_prose=rubric,
        profile_hash=digest,
    )


def rubric_table() -> str:
    from venona.llm.schemas import load_rubric

    rubric = load_rubric()
    scale = rubric.get("scale") or {}
    lines = [
        f"Score each axis from {scale.get('min', 0)} to {scale.get('max', 5)}.",
        "",
    ]
    for axis in rubric["axes"]:
        lines.append(f"- **{axis['id']}** (weight {axis['weight']})")
        lines.append(f"  {axis.get('description', '').strip()}")
    lines += [
        "",
        "Do NOT compute an overall score. It is calculated from these axes in code,",
        "so that weights can be retuned without re-running you.",
    ]
    return "\n".join(lines)


def gazetteer(conn: db.Conn, *, limit: int | None = None) -> str:
    """Known entities, for grounding extraction.

    Deterministically ordered (relevance, then id) and free of any per-run data,
    because this sits inside the cached prefix. An unstable ordering here would
    silently break caching for every call in the run.
    """
    limit = limit or get_settings().llm.gazetteer_size
    rows = repo.select_aliases(conn, limit).fetchall()

    # The cut is ORDERED BY relevance, and only declared anchors carry a nonzero
    # one -- so when this binds it is never the trunk that falls out, it is the
    # organic tail, in alphabetical id order. An entity missing from here is one
    # the extractor re-mints as a duplicate, and the failure looks like a
    # resolution bug rather than a truncated prompt. Say so once, loudly, rather
    # than letting it start silently on the run that crosses the limit.
    if len(rows) >= limit:
        log.warning(
            "the known-entity list is full at %d and is now cutting entities out of "
            "the prompt, lowest weight first; raise llm.gazetteer_size",
            limit,
        )

    lines = ["The graph already knows these entities. Prefer these exact names."]
    for row in rows:
        aliases = f"  [{row['aliases']}]" if row["aliases"] else ""
        lines.append(f"- {row['name']} ({row['type']}){aliases}")
    return "\n".join(lines)


def analyst_instructions() -> str:
    # Read per call, not cached: the descriptor is HTTP-editable and
    # `_invalidate("domain")` can only clear caches it knows about. One string
    # join per run is not worth a staleness bug.
    d = get_domain()
    return f"""\
You are an intelligence analyst reading {d.press_descriptor} for one reader.

Your job on each document: extract the entities and relationships it states, and
score how much it matters to that reader.

Hard rules:

1. Extract only what the document SAYS. Never add entities or relationships from
   your own knowledge, however confident you are. If the article does not state
   it, it does not exist for this purpose.
2. Every relationship needs a verbatim `evidence` sentence from the document
   that actually states it. If you cannot quote it in one sentence, omit the
   relationship.
{d.fragment("analyst_surface_rule")}
4. Direction matters on relationships. Read the ontology below carefully; a
   relationship asserted backwards is worse than one omitted.
5. Prefer fewer, well-evidenced assertions over many speculative ones.
6. `definition_sentence` is a SECOND sentence, and it is not
   `context_sentence`. Scan the whole document for a sentence that says what
   the entity IS -- the kind of thing it is -- and copy it character for
   character. It is checked against the document, and a paraphrase is
   discarded. It is usually nowhere near the mention you quoted: a platform is
   named in the award sentence and described three paragraphs later. Null when
   the document only ever names the entity, which is the common case and the
   right answer -- an invented description reads exactly like a sourced one.

For scoring, read the reader's profile below as a statement of what they care
about. Score against what it actually says, not against a general sense of
importance. Something can be major news and still score low for this reader --
say so plainly in the note when that is the case.

`profile_hooks` must contain verbatim phrases copied from the profile. Copy them
character for character; they are used to show the reader why something scored
the way it did."""


def output_contract(schema: dict, *, root: str = "the response") -> str:
    """Restate a JSON Schema's hard constraints as prose, derived FROM the schema.

    `--json-schema` already validates, and the CLI hands a violation back to the
    model to retry, so nothing is ever silently wrong. What that costs is a
    second full generation of an ~3.8k-token payload.

    **Only some of it is fixable from here, and the split was measured.**
    Classifying every rejection by the tool call that caused it:

        analyze     78 of 117 were the `$PARAMETER_NAME` artifact, 39 genuine
        discovery   13 of 13 genuine

    The artifact is the model emitting the literal tool-call placeholders
    `$PARAMETER_NAME`/`$PARAMETER_VALUE` with the CORRECT payload nested inside
    `$PARAMETER_VALUE`. It knows the schema perfectly; it fails at the
    invocation layer. No wording here touches that, and pretending otherwise
    would be attributing a tool bug to a comprehension problem.

    What this DOES fix is the genuine remainder -- the three shapes below:

        must have required property 'note' / 'so_what'   nested requireds missed
        must NOT have additional properties              invented extra keys
        /candidates: must NOT have more than 12 items    array cap ignored

    Measured on discovery, where every rejection is genuine and the sample is
    clean: **0.75 rejections per call before, 0.13 after** -- an 83% drop across
    two baseline runs and one after. The analyze effect is not yet measurable:
    the only run since the change analysed 14 clusters, and they were the
    deferred leftovers, which are the longest documents by selection.

    Generated from the schema rather than hand-written, because a hand-written
    copy drifts the first time someone adds a field -- and a prompt that
    confidently states the wrong contract is worse than no prompt at all.

    Deterministic: same schema in, same string out, so it stays inside the
    cached system prefix.
    """
    lines: list[str] = []

    def walk(node: dict, path: str) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "object":
            required = node.get("required") or []
            props = node.get("properties") or {}
            if required:
                lines.append(f"- `{path}` must contain exactly: {', '.join(required)}")
            if node.get("additionalProperties") is False and props:
                lines.append(f"- `{path}` must contain NOTHING else")
            for name, child in props.items():
                walk(child, f"{path}.{name}" if path else name)
        elif node.get("type") == "array":
            cap = node.get("maxItems")
            if cap is not None:
                lines.append(f"- `{path}` must have at most {cap} items")
            walk(node.get("items") or {}, f"{path}[]")

    walk(schema, root)
    if not lines:
        return ""
    return (
        "# Output contract\n\n"
        "Every field below is REQUIRED and the validator rejects anything extra. "
        "A rejection costs a full regeneration, so get it right the first time.\n\n"
        + "\n".join(lines)
    )


def analyze_system_prompt(conn: db.Conn, profile: Profile | None = None) -> str:
    """The cached prefix. Fixed order, no per-document content.

    Assembled once per run and passed unchanged via --system-prompt-file to every
    analyze call, so calls 2..N read from cache.
    """
    from venona.llm.schemas import analyze_schema

    profile = profile or load_profile()
    ontology = get_ontology()

    return "\n\n".join(
        [
            analyst_instructions(),
            "# The reader's relevance profile\n\n" + profile.relevance,
            "# Scoring rubric\n\n" + rubric_table(),
            ontology.extraction_guide(),
            output_contract(analyze_schema(), root="response"),
            "# Known entities\n\n" + gazetteer(conn),
        ]
    )


def brief_system_prompt(profile: Profile | None = None) -> str:
    profile = profile or load_profile()
    return "\n\n".join(
        [
            "You write a daily intelligence brief for one reader.",
            "# The reader's relevance profile\n\n" + profile.relevance,
            "# How to write it\n\n" + profile.brief,
        ]
    )


def personal_brief_system_prompt(profile: Profile | None = None) -> str:
    """The cached prefix for every reader's brief in a warm batch.

    Deliberately free of per-reader content: the reader's emphasis and
    followed names ride the USER content, so calls 2..N across different
    readers still share this prefix through the prompt cache -- the same
    assemble-once economics as `analyze_system_prompt`.

    It carries `output_contract` for the reason `analyze` and `discovery` do,
    and it is the task that needed it most: `personal_brief_schema` closes two
    levels with `additionalProperties: false`, requires eight fields on every
    item, and caps the array -- the three shapes the contract measurably fixes
    (0.75 -> 0.13 rejections per call on discovery). Without it, a reader's
    brief had five attempts and no restatement of the constraints, and one run
    spent them all and failed: `error_max_structured_output_retries`, with
    nothing written.

    It does NOT address the envelope artifact -- the model wrapping a correct
    payload in `$PARAMETER_VALUE`. That is an invocation-layer failure no
    wording touches, and `scripts/envelope_experiment.py` is the way to settle
    whether widening the schema's root fixes it.
    """
    from venona.llm.schemas import personal_brief_schema

    profile = profile or load_profile()
    return "\n\n".join(
        [
            (
                "You write a personal daily intelligence brief for one "
                "specific reader. The shared profile below describes the "
                "whole readership; the user content carries THIS reader's "
                "own emphasis and the names they follow. Where they differ, "
                "the reader's emphasis wins, and every why-it-matters "
                "sentence should be argued from it. Write ONE item per "
                "supplied story and put each on the desk it belongs to. Do "
                "not arrange the brief: the supplied order is the reader's "
                "priority and code lays the document out from it and the "
                "desks together. Everything else in the guide applies as "
                "written, the top block and the follow-up states included."
            ),
            "# The shared reading profile\n\n" + profile.relevance,
            "# How to write it\n\n" + profile.brief,
            output_contract(personal_brief_schema(), root="response"),
        ]
    )


def discovery_system_prompt(profile: Profile | None = None) -> str:
    from venona.llm.schemas import discovery_schema

    profile = profile or load_profile()
    return "\n\n".join(
        [
            (
                f"You find {get_domain().news_descriptor} that a specific reader would "
                "want. Return ONLY candidate URLs -- the pipeline fetches and reads "
                "each page itself, so any content you summarise here cannot be used."
            ),
            "# The reader's relevance profile\n\n" + profile.relevance,
            "# What to look for\n\n" + profile.research,
            output_contract(discovery_schema(), root="response"),
        ]
    )


def acronym_search_system() -> str:
    return f"""\
You locate authoritative sources that DEFINE an acronym.

Return only candidate URLs. Do not return an expansion and do not quote
anything: the page text you see is a summary, not the source, so nothing you
assert here can be verified. The pipeline fetches each URL itself and reads the
real text.

A page that USES an acronym almost never defines it. Prefer the page that is
ABOUT the thing the acronym names -- its official "about" page, a reference or
encyclopedia entry for it, or a published glossary -- over a page that merely
happens to contain the letters.

{get_domain().fragment("acronym_search_examples")}

Propose several candidates on DIFFERENT hosts, best first. Some sites refuse
automated fetches and some publish only as PDF, so the pipeline works down the
list -- a single perfect URL that happens to be unreadable ends the lookup with
nothing."""


def acronym_read_system() -> str:
    return f"""\
You are given the text of a document and an acronym.

If the text explicitly defines the acronym, return the expansion and the
verbatim sentence containing it. Copy the sentence character for character from
the supplied text -- it is checked against the source, and a paraphrase is
rejected.

{get_domain().fragment("acronym_read_examples")}

If the text does not define it, return found=false with nulls. Do NOT supply an
expansion from your own knowledge; an ungrounded definition is worse than none,
because it becomes an alias and corrupts entity resolution downstream."""


def acronym_adjudicate_system() -> str:
    """Asked only after the deterministic initials check has already said no.

    Deliberately carries no domain fragment. How letters are taken from words
    is a fact about English morphology, not about any subject domain, and a
    fragment here would add a key to the closed `FRAGMENT_KEYS` set -- which
    every instance's on-disk `domain.yaml` would then be missing, and a missing
    key is a hard `DomainError` at load.
    """
    return """\
You decide ONE narrow question: does a candidate expansion actually DERIVE an
acronym's characters?

You are given the acronym, the expansion, and the verbatim sentence a page used
to define it. Everything else has already been verified -- the sentence is text
we fetched ourselves, and it contains both the acronym and the expansion. What
is left is whether the letters follow from the words.

Answer true when every character of the acronym has a source in the expansion,
taken left to right, under any ordinary construction:

  - the initials of the words, whether or not small words contribute one
  - a syllable or clipped word taken from the start of a word, so
    "modulator-demodulator" gives MODEM and "picture element" gives PIXEL
  - a letter taken from inside a word, where a writer clearly chose it
  - a repeated initial compressed to a digit
  - a numeral or figure carried across unchanged
  - words the acronym skips entirely, so long as the ones it uses are in order

Answer false when a character has no source in the expansion, when the sources
run out of order, or when the expansion merely NAMES or DESCRIBES the thing
rather than being the words the acronym was built from. A model number and the
product it identifies are not expansions of each other.

Two things you must not do. Do not judge whether the definition is CORRECT --
the sentence settles that and you may not second-guess it. And never supply an
expansion of your own, under any circumstances."""


def adjudicate_system() -> str:
    return f"""\
You decide whether two extracted entity names refer to the same thing.

You are given pairs the resolver could not settle. For each, answer with one of:

{get_domain().fragment("adjudicate_verdicts")}

The hard call is merge versus part_of, and one test separates them: would the
two names appear in the same sentence as different actors? A company and its own
subsidiary can sign a contract with each other; a company and its own full legal
name cannot.

Use only the names, types, aliases and headlines you are given. If they do not
settle it, answer `distinct` -- a wrong merge is unrecoverable, and a curator
will see this either way. Never guess from outside knowledge of these
organisations."""


def anchor_system() -> str:
    d = get_domain()
    return f"""\
You attach unconnected entities to the top level of {d.graph_descriptor}.

You are given a list of ANCHORS -- countries, departments, services and major
organisations -- and a list of ENTITIES that currently connect to nothing. For
each entity, name the ONE anchor it most directly belongs under, and the
relation:

{d.fragment("anchor_relations")}

{d.fragment("anchor_directness")}

Answer "none" whenever you are unsure, and whenever the entity is a country, an
alliance, or a body with no clear parent. Roughly a third of any real list
should be "none"; a list with no "none" in it means you guessed.

{d.fragment("anchor_knowledge_note")}"""


# ── entity definitions ───────────────────────────────────────────────
#
# The acronym prompts define a STRING. These define a THING, and the difference
# shows up in what counts as evidence: an acronym is grounded by a sentence
# containing both the letters and the expansion, while an entity is grounded by
# a sentence that says what it is.


def define_write_system() -> str:
    return f"""\
You are given sentences taken from articles we have already ingested, each
numbered [1], [2], [3], and the name of one entity.

Write ONE sentence saying what that entity is: the kind of thing it is, and the
single fact that matters most about it {get_domain().define_context}. A reader who knows
the domain but has not met this entity should finish the sentence knowing what
it is.

Use ONLY the supplied sentences. You have no other source, and anything you add
from your own knowledge cannot be checked and will be discarded. Return the
NUMBERS of the sentences you actually drew from -- they are verified against
what was supplied, and a definition citing a number we did not give you is
rejected whole.

Do not editorialise, do not assess importance to the reader, and do not repeat
the entity's name as the whole sentence. If the supplied sentences only mention
the entity without saying what it is, return found=false. That is a normal
outcome and a better one than a plausible guess: an ungrounded definition reads
exactly like a grounded one, which is what makes it dangerous."""


def define_extract_system() -> str:
    """Re-read one article we hold, for the sentences that define named entities.

    The backfill's system prompt. It differs from `define_read_system` in what
    it is reading -- our own stored article rather than a page just fetched off
    the web -- and in being asked about many names at once, which is what puts
    a corpus-wide pass within reach: one call per document, not one per name.
    """
    return """\
You are given the text of one article and a list of names that appear in it.

For each name, find the sentence in the article that says what that thing IS --
the kind of thing it is -- and copy it out verbatim, character for character.
It is checked against the article text, and a paraphrase is discarded.

The sentence is usually not the one where the name first appears. An article
names a programme in its opening line and describes it three paragraphs down;
it is the description you want.

The sentence must contain the name. A sentence that describes something else,
however nearby, defines nothing about this one.

Return null for every name the article only mentions. That is the common answer
and the right one: an invented description reads exactly like a sourced one,
which is what makes it dangerous. Return one entry per name you were given."""


def define_search_system() -> str:
    return """\
You locate authoritative pages that say what an organisation, programme,
platform or technology IS.

Return only candidate URLs. Do not return a description and do not quote
anything: the page text you see is a summary, not the source, so nothing you
assert here can be verified. The pipeline fetches each URL itself and reads the
real text.

Prefer the entity's own site, the programme office's page, a standards body, or
a reference work -- a page ABOUT the thing rather than a news article that
happens to mention it. A press release announcing a contract rarely says what
the contractor is.

Propose several candidates on DIFFERENT hosts, best first. Some sites refuse
automated fetches and some publish only as PDF, so the pipeline works down the
list -- a single perfect URL that happens to be unreadable ends the lookup with
nothing."""


def define_read_system() -> str:
    return """\
You are given the text of a document and the name of an entity.

If the text says what the entity IS, return a one-sentence definition and the
verbatim sentence it rests on. Copy that sentence character for character from
the supplied text -- it is checked against the source, and a paraphrase is
rejected.

The sentence must name the entity. A page that discusses it without ever saying
what it is does not define it, however much it says.

If the text does not describe it, return found=false with nulls. Do NOT supply a
description from your own knowledge; an ungrounded definition is worse than
none, because a reader cannot tell the two apart."""


def questions_system(settings: Settings | None = None) -> str:
    """Study questions over supplied articles (epic O, docs/adr/0157).

    The operator's style guidance is `profile/learning_prompt.md`, editable on
    the Admin rail like every prompt; the invariant sentences around it are
    code's. Read fresh per run rather than cached: the run is minutes long and
    the whole point of the editable doc is that the next run obeys it.
    """
    from venona.settings import get_settings

    settings = settings or get_settings()
    style = _read(settings.profile_dir / "learning_prompt.md")
    return "\n\n".join(
        [
            (
                f"You write study questions for someone learning about "
                f"{get_domain().corpus_descriptor}. You are given the full text of "
                "numbered articles; every question must be answerable from ONE of "
                "them alone."
            ),
            (
                "For each question return: the article's number as source_index; a "
                "verbatim quote from that article that CONTAINS the answer, copied "
                "character for character (it is checked against the source, and a "
                "paraphrase is rejected whole); the answer; and, for kind=choice, "
                "exactly four options including the answer. kind=cloze questions "
                "put ____ where the missing term goes. Do not number the questions "
                "and do not reference articles by name -- the reader will not see "
                "them."
            ),
            "# Question style\n\n" + style,
        ]
    )
