"""Meta-prompts for the domain-setup generation run.

These are system machinery, not domain content: they take the new domain's
descriptor and dossier and ask for documents SHAPED like the exemplar set
under `fixtures/exemplars/` -- frozen copies of the defense instance's files,
which are the quality bar (docs/adr/0147). Never the live `profile/`: a
converted instance's live files are the output of a previous generation.

Prompt-cache discipline is the same as `prompts.py`: system prompts are
stable string joins over the descriptor and exemplars; everything per-run
(the dossier, seeds, failure lists) rides in user content, deterministically
ordered.
"""

from __future__ import annotations

import json
from typing import Any

from venona.domain import DomainDescriptor
from venona.settings import Settings

#: The survey facets, in run order. The dossier is these five answers.
SURVEY_FACETS: tuple[tuple[str, str], ...] = (
    (
        "organizations",
        "The organizations and actors that matter: who builds, who buys or "
        "adopts, who regulates or oversees, who funds, who researches. Name "
        "the load-bearing ones a daily reader would meet in the first week.",
    ),
    (
        "products",
        "The named products, programmes, projects, systems or platforms the "
        "domain's press actually covers -- concrete, buildable or fundable "
        "things with stable names.",
    ),
    (
        "publications",
        "Where this domain's trade press lives: the outlets, newsletters and "
        "official feeds a serious reader follows daily, and which of them "
        "publish RSS or Atom feeds. Include the feed URL when you find one.",
    ),
    (
        "jargon",
        "The domain's acronym and jargon culture: the abbreviations a reader "
        "hits constantly, the ambiguous ones, the short common words that are "
        "also entity names, and the phrases with standard abbreviations.",
    ),
    (
        "themes",
        "The standing sub-topics a daily brief would organise itself around: "
        "the six to nine desks a domain editor would staff, each with the "
        "recurring subjects it covers.",
    ),
)


def _exemplar(settings: Settings, name: str) -> str:
    return (settings.fixtures_dir / "exemplars" / name).read_text()


def _dossier_block(dossier: dict[str, Any]) -> str:
    """The dossier as deterministic prompt text -- sorted keys, no timestamps,
    so re-running one document against a stored dossier replays its fixture."""
    return "# The domain dossier\n\n" + json.dumps(
        dossier, sort_keys=True, ensure_ascii=False, indent=1
    )


def survey_system(d: DomainDescriptor) -> str:
    return f"""\
You research a subject domain so an intelligence pipeline can be configured
for it. The domain: {d.display_name}.

Use web search. Prefer primary and authoritative pages; name things by their
stable, canonical names. Every claim should be the kind a domain insider
would nod at -- this survey seeds the pipeline's configuration, and a wrong
name here becomes a wrong entity there. Where you are unsure, say so in the
detail rather than guessing confidently."""


def survey_user(facet_key: str, instruction: str, d: DomainDescriptor, seeds: str) -> str:
    parts = [
        f"Domain description, from the operator:\n\n{d.tagline}",
        f"Facet to survey: {facet_key}\n\n{instruction}",
    ]
    if seeds:
        parts.append(f"Operator-provided starting points (hints, not the answer):\n\n{seeds}")
    return "\n\n".join(parts)


def sources_system(settings: Settings, d: DomainDescriptor) -> str:
    return f"""\
You propose the source list for an intelligence pipeline covering
{d.display_name}: RSS/Atom feeds plus open-ended discovery sweeps.

Propose only feeds you have reason to believe exist -- the pipeline fetches
every one before it is accepted, and a dead URL is dropped. Sweep tags are
snake_case search themes; they become the spine the research prompt and the
brief's sections are organised around, so pick the six to nine that carve the
domain at its joints.

Here is the source file of a mature instance in another domain, as the
quality bar for id style and coverage breadth:

{_exemplar(settings, "sources.yaml")}"""


def _predicate_contract_lines(ontology) -> str:
    """One line per predicate: name, subject -> object, and its description.

    Rendered into the anchors prompt so the model chooses predicates the way
    the validator will judge them. Without this (and with the edge enum pinned
    to the anchor spine's five), the first generated trunk arrived with 49 of
    189 edges violating its own ontology's domain/range.
    """
    return "\n".join(
        "- {name}: {domain} -> {range}{description}".format(
            name=name,
            domain="|".join(spec.domain) or "any",
            range="|".join(spec.range) or "any",
            description=f" -- {spec.description}" if spec.description else "",
        )
        for name, spec in ontology.predicates.items()
    )


def ontology_system(settings: Settings, d: DomainDescriptor) -> str:
    return f"""\
You write the ontology for an intelligence pipeline covering
{d.display_name}: the entity types and predicates it may assert.

Return the complete YAML file text. Hard requirements:

- Entity types: at most 9, each with a unique lowercase `prefix`, a unique
  `color_slot` (1-9), a `description`, model-facing `guidance`, and
  `examples` from this domain. Keep a `Person` type and a `Country` type if
  people and states act in this domain at all; invent domain types only where
  the generic ones genuinely fail.
- `confusable_with` on each type: the OTHER types a reader of this domain's
  press would routinely file the same thing under. Not the types it relates
  to -- the types it gets MISTAKEN for. Two entities sharing a name across a
  confusable pair are treated as one thing filed twice, and a person is asked
  which kind is right; across every other pair they are treated as two things
  that happen to share a name. Nothing in the two names can tell those apart,
  which is why you are asked.
  Declare it on one side only; it reads both ways. An EMPTY list is the
  stronger statement and should be the default: it says this boundary is real,
  and a shared name across it is two things. Fill it only where you can name
  the sentence that would blur the two, and never between two types whose
  distinction is the reason you created both.
- Predicates: the five anchor predicates (PART_OF, BASED_IN, WORKS_FOR,
  MANAGES, PRODUCES) are mandatory -- the validator refuses a file without
  them. Beyond those, the relation set is YOURS to derive from this domain's
  press, never the exemplar's to copy: the exemplar teaches file shape,
  comment habits and rigor, not which relations exist. Its predicates encode
  the claims ITS domain's press makes daily; reproduced in a domain whose
  press talks about different things, they leave the pipeline unable to say
  what the articles say, and every such assertion is rejected at extraction
  time, one by one.
- Enumerate this domain's daily claim archetypes and make each expressible.
  Work through at least: adoption and use, competition, composition (one
  thing builds on another), funding, production, partnership, regulation,
  membership. For each archetype, name which of YOUR types actually stand on
  each side of that claim in this domain's press, and confirm a drafted
  predicate admits that exact pairing -- remembering that artifacts act too
  where the domain is artifact-centric: products may compete with products,
  a technique may build on a technique, an organization may adopt someone
  else's product. Widen `domain`/`range` or add a predicate until every
  archetype the press asserts fits; an archetype this domain's press
  genuinely never asserts may be dropped, with a comment saying so.
- `excluded_from_paths: true` on any predicate whose range is a small closed
  set that most of its domain points at -- the "where does it belong"
  relation, and BASED_IN is always one. Shortest-path search walks every
  predicate that does not carry this, so once such a relation is filled in,
  the shortest route between any two things becomes "both belong to the same
  one": true, two hops, and it hides the hierarchy route somebody asked for.
  Leave it off every relation that is genuinely a step in a chain.
- Prove it: end the file by type-checking a handful of real facts from the
  dossier against your drafted predicates, one comment line each --
  `# check: <subject> [Type] --PREDICATE--> <object> [Type]`. A fact that
  fails its predicate's contract means the ontology is wrong, not the fact;
  fix the contract before returning the file.
- `version: 1`, and a one-line `changelog` entry saying this is the
  generated ontology.
- Comment the file the way the exemplar is commented: reasons, not
  restatements.

The exemplar, from a mature instance in another domain:

{_exemplar(settings, "ontology.yaml")}"""


#: Per-document instruction + exemplar file, keyed by EDITABLE key.
DOCUMENT_SPECS: dict[str, dict[str, str]] = {
    "domain": {
        "exemplar": "domain.yaml",
        "instruction": (
            "Write the domain descriptor: the phrases the prompts splice in "
            "(press/news/graph descriptors, define and search contexts), the "
            "access-role list for this domain's readers, common_name_words "
            "(ordinary words that are entity names here), and the seven "
            "prompt_fragments with worked examples FROM THIS DOMAIN doing the "
            "same job the exemplar's do. Keep the slug exactly as the "
            "current deployment states it below -- it is frozen."
        ),
    },
    "relevance_profile": {
        "exemplar": "relevance_profile.md",
        "instruction": (
            "Write the domain's reading profile from the description below. "
            "It describes the DOMAIN, never a person or an employer -- no "
            "first person, no 'we': readers tune their own emphasis in the "
            "app, so this document defines what the domain covers, concrete "
            "enough to score against. The technology core, who buys, the "
            "vendor landscape, what to score highly in priority order, and "
            "what the domain does not need."
        ),
    },
    "research_prompt": {
        "exemplar": "research_prompt.md",
        "instruction": (
            "Write the research prompt: one `###` section per discovery sweep, "
            "with headings that MATCH the sweep list given below -- the sweep "
            "ids, the research prompt's headings and the brief's sections are "
            "one spine, and the pipeline checks the coupling."
        ),
    },
    "brief_prompt": {
        "exemplar": "brief_prompt.md",
        "instruction": (
            "Write the brief-voice prompt: how the daily brief is written for "
            "this reader. Keep the exemplar's rules about evidence and tone; "
            "replace its domain-flavoured examples with this domain's."
        ),
    },
    "scoring_rubric": {
        "exemplar": "scoring_rubric.yaml",
        "instruction": (
            "Write the scoring rubric: 5-8 axes with ids shaped for this "
            "domain, weights summing exactly to 1.0, and tier thresholds. The "
            "validator refuses weights that do not sum to 1.0. Each axis "
            "carries two audiences: `description` is YOUR scoring contract "
            "(explicit 0-5 anchors), while `label` (a plain two-or-three word "
            "name) and `plain` (one sentence, under 15 words) are what a "
            "day-one reader sees when tuning their own emphasis -- no scoring "
            "rules, no strategy knowledge assumed, no internal vocabulary. "
            "Anchor each `plain` line to the domain itself, in the domain's "
            "own concrete words (name the subjects plainly), never to a "
            "team, an employer, or anything else about the reader -- and "
            "never to the deployment (a deployment is a domain; 'this "
            "Venona' is not a thing). Tailoring is per individual, so a "
            "line must describe a kind of story on offer, not read as a "
            "question awaiting the reader's answer."
        ),
    },
    "brief_sections": {
        "exemplar": "brief_sections.yaml",
        "instruction": (
            "Write the brief sections: one desk per standing theme, in reading "
            "order, names agreeing with the sweep spine given below. `covers` "
            "lists the subjects each desk owns."
        ),
    },
    "lexicon": {
        "exemplar": "lexicon.yaml",
        "instruction": (
            "Write the lexicon from the jargon facet of the dossier: "
            "`expansions` (phrase -> standard abbreviations), `stoplist` "
            "(letter sequences that are never this domain's acronyms), "
            "`short_whitelist` (short acronyms that ARE meaningful here), "
            "`non_distinguishing` (words that do not distinguish two entity "
            "names), `corporate_form` (words naming an organisation's legal or "
            "corporate form, so that `Acme` and `Acme <word>` are one "
            "organisation) and `generic_tails` (head nouns a writer appends "
            "without naming a different thing).\n\n"
            "Derive the last two from THIS domain rather than copying the "
            "exemplar's. Its lists are arguments about its own names, and the "
            "same word inverts between domains: a word it omits because that "
            "word is part of a real organisation's name there may be pure "
            "corporate form here, and the reverse. Its comments say which of "
            "its choices were close calls and why, which is the part to "
            "imitate. Where a word could plausibly be part of a real name, "
            "LEAVE IT OUT. A missing word costs a merge that a person is then "
            "asked to confirm; a wrong one silently states that two "
            "organisations are one, and nobody is asked anything."
        ),
    },
}


def document_system(settings: Settings, key: str, d: DomainDescriptor) -> str:
    spec = DOCUMENT_SPECS[key]
    return f"""\
You write one configuration document for an intelligence pipeline covering
{d.display_name}. Return the complete file text; comments in the file are
kept and read by the operator, so write them the way the exemplar does --
reasons, not restatements.

{spec["instruction"]}

The exemplar, from a mature instance in another domain. Match its structure,
its comment habits and its level of specificity; replace every
domain-specific fact with this domain's:

{_exemplar(settings, spec["exemplar"])}"""


def document_user(d: DomainDescriptor, dossier: dict[str, Any], extras: str = "") -> str:
    parts = [
        f"The domain: {d.display_name}. {d.tagline}",
        _dossier_block(dossier),
    ]
    if extras:
        parts.append(extras)
    return "\n\n".join(parts)


def anchors_system(settings: Settings, d: DomainDescriptor, *, grounded: bool, ontology) -> str:
    knowledge = (
        "Use web search to confirm the load-bearing facts: this batch is the "
        "top of the trunk, and a stale name here poisons everything under it."
        if grounded
        else (
            "Use what you know about this domain. It is safe here because a "
            "human reviews every line before any of it lands, and one "
            "keystroke discards a row -- omitting costs nothing, a wrong "
            "parent costs a correction."
        )
    )
    return f"""\
You declare the anchor trunk for an intelligence graph covering
{d.display_name}: the entities no article ever states, which everything else
attaches to.

{knowledge}

Rules:

- Names are canonical and stable; code mints ids from them.
- Every edge names two entities from YOUR OWN lists (this batch or the
  already-declared names given to you) -- an edge to an undeclared name is
  dropped.
- PART_OF points child -> parent, always the MOST DIRECT parent.
- Every edge must satisfy its predicate's subject -> object contract below.
  Pick the predicate whose contract FITS rather than stretching a familiar
  one -- a person who runs a company LEADS it, they do not MANAGE it; an
  edge outside its contract fails validation in front of the reviewer.
- `relevance` is how central the entity is to this domain's daily reading,
  0 to 1.
- List genuinely ambiguous surfaces under `ambiguous` with the reason.

Structure is the point, and it is the half that gets skipped. A list of names
with no edges is the failure mode: it takes a run's whole budget and leaves
every node floating, because the corpus supplies the leaves and never the
trunk. Judgement that decides individual edges:

- Give every organisation you declare a home jurisdiction, using whichever
  predicate that domain's ontology defines for it. It is the one attachment
  almost every entity can carry, and it is what stops a declared name from
  floating alone forever.
- Attach to the most specific parent YOU HAVE DECLARED. Skipping past a
  middle that is in your own list is a wrong edge that hides the real one;
  reaching for a broad parent because the exact one is absent is correct, and
  a jurisdiction is the honest parent when no closer one exists.
- A body that belongs to no single jurisdiction gets neither -- an alliance,
  a treaty organisation, a joint venture between two states. Leave it
  unattached rather than inventing a home for it. Same for anything whose
  parent you would be guessing at: omit the edge and say nothing.
- A named local arm takes its own home jurisdiction AND a parent edge to the
  organisation it belongs to. Do not move the parent's home to match it.
- Membership of a jurisdiction is not composition. A body that IS an organ of
  a state belongs to that state's hierarchy; a thing that merely operates
  there does not.
- Declare the jurisdictions themselves. An edge naming one you never declared
  is dropped, and every organisation pointing at it loses its only
  attachment.

The predicates, from this domain's own (drafted) ontology:

{_predicate_contract_lines(ontology)}

The shape being assembled (an excerpt of a mature instance's trunk in
another domain):

{_exemplar(settings, "anchors_excerpt.yaml")}"""
