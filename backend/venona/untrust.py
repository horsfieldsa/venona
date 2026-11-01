"""Wrapping corpus text so a model reads it as evidence, not as instruction.

venona's corpus is scraped trade press. `assertions.evidence_quote`,
`documents.title`, `acronym_definitions.grounding_quote`, entity names and brief
block text are all adversary-influenceable -- most cheaply by publishing a page
the discovery sweep will fetch.

TWO CONSUMERS, ONE THREAT (ADR 0166). `api/mcp/render.py` hands that text to
somebody else's agent. The pipeline hands the same fields to venona's own model,
in `llm/runner.py`, `pipeline/`, `services/personal_brief.py` and the learning
router. Both are injection channels by construction, so both wrap.

TOP LEVEL, AND WITH NO VENONA IMPORTS, DELIBERATELY. `tests/test_mcp_readonly.py`
bans the token `executor` anywhere under `api/mcp/`, and `render.py` imports this
module. Living inside `llm/` would make that ban depend on `llm/__init__.py`
staying empty; a leaf module cannot develop that problem. The one import is
`notices`, which is a leaf itself and carries nothing beyond the standard
library.

THE ENVELOPE IS HERE, THE SCRUB IS SHARED. `<untrusted>` is a boundary marker
for a MODEL and means nothing to a person, so it belongs to this side. Making
injected text visible is the half a notice needs too, and a second copy of that
would be a second answer to "which characters can hide" -- see `notices.untrust`.

BE HONEST ABOUT WHAT THIS DOES. It moves the odds and closes nothing. There is no
known reliable defence against a model following instructions embedded in
retrieved text. This is here because it is cheap, because it is what a client's
own tool-use confirmation hooks onto, and because the alternative -- handing over
raw scraped prose with no marking at all -- is strictly worse.

One part is not probabilistic: `pipeline/normalize.py` normalises bodies to NFC,
which leaves zero-width joiners, bidirectional overrides and the Tags block
exactly where they were. The category-`Cf` strip below removes the class, so
injected text cannot be invisible to the OPERATOR reading a run transcript. A
reviewer cannot review what they cannot see, whatever the model does.

THE MITIGATIONS THAT ACTUALLY WORK ARE STRUCTURAL AND LIVE ELSEWHERE. On the MCP
side: every tool is read-only, so an injection can make an agent write a wrong
sentence but cannot start a run, spend a subscription or change a stored record
(`docs/CONTRACTS.md` C12, `tests/test_mcp_readonly.py`). On the pipeline side:
tools default to `--tools ""`, ids are never taken from the model, `overall` is
computed in Python, an acronym expansion needs an article id plus a verbatim
quote plus an initials check, and Cypher names pass ontology membership and an
anchored regex. Wrapping text is not a reason to relax any of them.
"""

from __future__ import annotations

from notices.untrust import DELIMITER, MAX_QUOTE_CHARS, scrub

__all__ = ["DELIMITER", "MAX_QUOTE_CHARS", "inline", "quote", "scrub"]


def _attr(value: str) -> str:
    """Escape a value spliced into an HTML-style attribute, so a corpus-derived
    source label cannot close the tag early or add its own -- the model must parse
    exactly the envelope structure venona built, not one the source rewrote."""
    return (
        value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
    )


def quote(
    text: str | None,
    *,
    source: str | None = None,
    kind: str = "article-quote",
    limit: int | None = MAX_QUOTE_CHARS,
    keep_lines: bool = False,
) -> str:
    """A delimited, labelled block. Empty input yields an empty string, not an
    empty envelope -- an envelope around nothing reads as "the corpus said
    nothing here", which is a different claim from "there is no quote"."""
    if not text:
        return ""
    body = scrub(text, limit=limit, keep_lines=keep_lines)
    if not body:
        return ""
    attrs = f' source="{_attr(scrub(source, limit=120))}"' if source else ""
    return f'<untrusted{attrs} kind="{kind}">\n{body}\n</untrusted>'


def inline(text: str | None, *, limit: int = 200) -> str:
    """For a name or a title -- corpus-derived, so sanitised, but too short to
    be worth an envelope. Model-extracted entity names come from scraped prose
    and are just as reachable as a quote; they simply cannot carry a paragraph
    of injected instructions."""
    return scrub(text or "", limit=limit)
