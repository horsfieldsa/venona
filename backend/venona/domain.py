"""The domain descriptor -- who this deployment reads for, as configuration.

Every string that names the subject domain lives here, not in code: the
analyst persona line, the discovery opener, the lookup-query context, the
worked-example paragraphs inside the grounding prompts, the OpenAPI blurb, the
User-Agent note, the MCP copy and the access-request roles. Code owns every
invariant sentence -- the hard rules, the verbatim-quote requirements -- and
splices these in, so a venona instance for a new domain is a new
`config/domain.yaml`, never a code change (docs/adr/0138, 0144).

`DEFENSE_DEFAULTS` reproduces the checked-in defense file exactly, and a
contract test holds the two equal: a deployment without the file behaves
byte-for-byte as the defense instance always has, which is also what keeps
every recorded envelope under `fixtures/llm/` valid (cache keys include the
system-prompt sha).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, fields
from functools import lru_cache
from pathlib import Path

import yaml

SLUG = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
ROLE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")

# One-line splices. A phrase is part of a sentence the template owns, so it is
# capped short enough that it cannot smuggle in a paragraph of its own.
PHRASE_FIELDS = (
    "press_descriptor",
    "news_descriptor",
    "graph_descriptor",
    "define_context",
    "search_context",
    "user_agent_note",
    "mcp_title",
    "corpus_descriptor",
)
MAX_PHRASE = 120
MAX_TEXT = 400
MAX_FRAGMENT = 4000

#: The whole-paragraph (or whole-block) slots the prompt templates splice in.
#: Keys are closed: an unknown key in the file is rejected rather than silently
#: never read, for the same reason `parse_ontology` rejects unknown top-level
#: keys. Each value is verbatim prompt text at line granularity -- no leading
#: or trailing blank lines; the template owns the blank lines around it.
FRAGMENT_KEYS = frozenset(
    {
        "analyst_surface_rule",
        "acronym_search_examples",
        "acronym_read_examples",
        "adjudicate_verdicts",
        "anchor_relations",
        "anchor_directness",
        "anchor_knowledge_note",
    }
)


class DomainError(ValueError):
    """A domain descriptor that cannot be loaded. The message is user-facing."""


@dataclass(frozen=True)
class DomainDescriptor:
    slug: str
    display_name: str
    tagline: str
    login_blurb: str
    press_descriptor: str
    news_descriptor: str
    graph_descriptor: str
    define_context: str
    search_context: str
    user_agent_note: str
    mcp_title: str
    corpus_descriptor: str
    access_roles: tuple[str, ...]
    #: Common English words that are also real entity names in this domain
    #: ("army", "titan"). Never matched globally by the alias index; they need
    #: tier-A gating. May be empty -- not every domain reuses ordinary words.
    common_name_words: tuple[str, ...] = ()
    prompt_fragments: dict[str, str] = field(default_factory=dict)

    def fragment(self, key: str) -> str:
        if key not in FRAGMENT_KEYS:
            raise KeyError(f"unknown prompt fragment {key!r}")
        return self.prompt_fragments[key]


def parse_domain(raw: object) -> DomainDescriptor:
    """Validate a parsed domain.yaml. Raises DomainError with the reason."""
    if not isinstance(raw, dict):
        raise DomainError("domain.yaml must be a mapping")

    known = {f.name for f in fields(DomainDescriptor)}
    unknown = set(raw) - known
    if unknown:
        raise DomainError(
            f"unknown key(s) {sorted(unknown)} -- a key this loader does not "
            f"read is never consulted, so the save would silently do nothing"
        )
    missing = known - set(raw) - {"common_name_words"}
    if missing:
        raise DomainError(f"missing key(s): {sorted(missing)}")

    def text(key: str, cap: int) -> str:
        value = raw[key]
        if not isinstance(value, str) or not value.strip():
            raise DomainError(f"{key} must be a non-empty string")
        if len(value) > cap:
            raise DomainError(f"{key} is {len(value)} chars; the cap is {cap}")
        return value

    slug = text("slug", 32)
    if not SLUG.fullmatch(slug):
        raise DomainError(f"slug {slug!r} must match {SLUG.pattern}")

    roles = raw["access_roles"]
    if not isinstance(roles, list) or not roles:
        raise DomainError("access_roles must be a non-empty list")
    for role in roles:
        if not isinstance(role, str) or not ROLE.fullmatch(role):
            raise DomainError(f"access role {role!r} must match {ROLE.pattern}")
    if len(roles) != len(set(roles)):
        raise DomainError("access_roles has duplicates")

    words = raw.get("common_name_words") or []
    if not isinstance(words, list):
        raise DomainError("common_name_words must be a list")
    for word in words:
        if not isinstance(word, str) or not re.fullmatch(r"[a-z][a-z-]{1,31}", word):
            raise DomainError(f"common name word {word!r} must be a lowercase word")
    if len(words) != len(set(words)):
        raise DomainError("common_name_words has duplicates")

    fragments = raw["prompt_fragments"]
    if not isinstance(fragments, dict):
        raise DomainError("prompt_fragments must be a mapping")
    unknown_fragments = set(fragments) - FRAGMENT_KEYS
    if unknown_fragments:
        raise DomainError(
            f"unknown prompt fragment(s) {sorted(unknown_fragments)} -- "
            f"the templates splice a closed set of slots"
        )
    missing_fragments = FRAGMENT_KEYS - set(fragments)
    if missing_fragments:
        raise DomainError(f"missing prompt fragment(s): {sorted(missing_fragments)}")
    for key, value in fragments.items():
        if not isinstance(value, str) or not value.strip():
            raise DomainError(f"prompt fragment {key} must be a non-empty string")
        if len(value) > MAX_FRAGMENT:
            raise DomainError(f"prompt fragment {key} is {len(value)} chars; cap {MAX_FRAGMENT}")
        if value != value.strip("\n"):
            raise DomainError(
                f"prompt fragment {key} starts or ends with a blank line -- "
                f"the template owns the blank lines around a fragment"
            )

    return DomainDescriptor(
        slug=slug,
        display_name=text("display_name", MAX_TEXT),
        tagline=text("tagline", MAX_TEXT),
        login_blurb=text("login_blurb", MAX_TEXT),
        **{name: text(name, MAX_PHRASE) for name in PHRASE_FIELDS},
        access_roles=tuple(roles),
        common_name_words=tuple(words),
        prompt_fragments={key: fragments[key] for key in sorted(fragments)},
    )


# ── the defense instance, as code ────────────────────────────────────
#
# These strings are the ones the code carried as literals before the
# descriptor existed, verbatim. `tests/test_domain_prompts.py` holds the
# assembled prompts byte-identical to golden snapshots of that era, and holds
# this object equal to the checked-in config/domain.yaml.

DEFENSE_DEFAULTS = DomainDescriptor(
    slug="defense",
    display_name="Defense industry",
    tagline="Defense-industry intelligence: research, score, graph, brief.",
    login_blurb=("Venona reads the defense trade press every day and keeps what it learns."),
    press_descriptor="defense-industry press",
    news_descriptor="defense-industry news",
    graph_descriptor="a defence-industry graph",
    define_context="in a defense context",
    search_context="in a US defense context",
    user_agent_note="personal defense-industry research reader",
    mcp_title="defence-industry intelligence",
    corpus_descriptor="defence trade press",
    common_name_words=(
        "army",
        "navy",
        "space",
        "shield",
        "sentinel",
        "vision",
        "anchor",
        "atlas",
        "falcon",
        "titan",
        "sky",
        "guardian",
        "liberty",
        "victory",
        "spirit",
        "eagle",
        "phantom",
        "ghost",
        "reaper",
        "sentry",
    ),
    access_roles=(
        "analyst",
        "government",
        "military",
        "industry",
        "technologist",
        "investor",
        "press",
        "academic",
        "other",
    ),
    prompt_fragments={
        "analyst_surface_rule": (
            '3. Return surfaces exactly as written in the article -- "the Army", "Anduril",\n'
            '   "NGC2". Do not normalise, expand, or canonicalise them, and never invent an\n'
            "   identifier. Something else maps surfaces to identity."
        ),
        "acronym_search_examples": (
            "Do not anchor on the surrounding usage. An acronym cited as part of a reference\n"
            '-- "10 USC 4025", "DoDI 5000.02" -- names the WORK being cited, not the\n'
            "section: the page to find defines the United States Code itself, not Title 10.\n"
            "\n"
            "Prefer official program pages, government sites and standards bodies. Avoid\n"
            "news aggregators and acronym-farm sites that list dozens of unsourced\n"
            "expansions; an encyclopedia entry or an official glossary is not one of those\n"
            "and is often the only page that states the expansion in a sentence."
        ),
        "acronym_read_examples": (
            "The sentence must contain BOTH the acronym and the expansion. A heading that is\n"
            'merely the expanded name is not a definition: "United States Code" on its own\n'
            "does not say that USC means it, and is rejected. Look for the sentence that\n"
            'connects the two -- "..., commonly called IDIQ contracts, ...", "(USC)",\n'
            '"abbreviated USC".'
        ),
        "adjudicate_verdicts": (
            "  merge      One organisation or programme under two names. Includes a short\n"
            '             form and its full legal name -- "Anduril" / "Anduril Industries",\n'
            '             "Northrop" / "Northrop Grumman", "Army" / "U.S. Army".\n'
            "  part_of    The first names a division, subsidiary, command or unit OF the\n"
            '             second -- "General Dynamics Information Technology" is part of\n'
            '             "General Dynamics", "Thales Australia" is part of "Thales".\n'
            "             Say which side is the CHILD.\n"
            "  variant    One is a version of the other's design: a block, mark,\n"
            '             increment or numbered model -- "F-16 Block 70" of "F-16",\n'
            '             "Switchblade 600" of "Switchblade". Not part_of, which is a\n'
            "             separate thing fitted to the other, and not merge: two\n"
            "             variants are two things a buyer chooses between. Say which\n"
            "             side is the CHILD, the version.\n"
            "  distinct   Neither. Two separate bodies that happen to share words --\n"
            '             "Royal Canadian Air Force" and "Air Force" are different services;\n'
            '             "Moonshot" and "Moonshot AI" are different companies.'
        ),
        "anchor_relations": (
            "  PART_OF    a division, command, agency, unit or subsidiary OF the anchor.\n"
            '             "4th Infantry Division" is PART_OF "U.S. Army".\n'
            '             "Raytheon" is PART_OF "RTX".\n'
            "  BASED_IN   an organisation and its HOME country -- where it is headquartered\n"
            "             or incorporated, or the state whose organ it is.\n"
            '             "Anthropic" is BASED_IN "United States".\n'
            "  WORKS_FOR  a person and the organisation they serve."
        ),
        "anchor_directness": (
            "Attach to the MOST DIRECT anchor available, never the most impressive one. A\n"
            "US Army division is PART_OF the Army, not of the Defense Department and not of\n"
            "the United States -- the chain reaches the top on its own, and a shortcut past\n"
            "the middle is a wrong edge that hides the real one."
        ),
        "anchor_knowledge_note": (
            "UNLIKE the other tasks in this system, you SHOULD use what you know about these\n"
            "organisations. Nothing in the corpus states that Nvidia is an American company\n"
            "or that CISA sits under Homeland Security -- that is background knowledge, and\n"
            "it is exactly what is being asked for here. It is safe because nothing you\n"
            "return is written to the graph: a curator reviews every line and one keystroke\n"
            'discards it. Be accurate, not bold; "none" costs nothing and a wrong parent\n'
            "costs a correction."
        ),
    },
)


def _domain_path() -> Path:
    from venona.settings import get_settings

    return get_settings().config_dir / "domain.yaml"


@lru_cache(maxsize=1)
def get_domain() -> DomainDescriptor:
    """The live descriptor: config/domain.yaml if present, defense otherwise.

    The fallback is what keeps a deployment provisioned before this file
    existed booting -- and behaving identically, because the checked-in file
    and DEFENSE_DEFAULTS are held equal by a contract test. Cache is cleared
    by `config_service._invalidate("domain")` on every save.
    """
    path = _domain_path()
    if not path.exists():
        return DEFENSE_DEFAULTS
    return parse_domain(yaml.safe_load(path.read_text()))


def domain_hash(descriptor: DomainDescriptor | None = None) -> str:
    """Stable fingerprint of a descriptor, for run attribution."""
    d = descriptor or get_domain()
    payload = {f.name: getattr(d, f.name) for f in fields(DomainDescriptor)}
    canon = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=list)
    return hashlib.sha256(canon.encode()).hexdigest()
