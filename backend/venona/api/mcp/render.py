"""Service payloads -> compact text for a context window.

REST payloads are sized for a UI. A hover card carries precomputed `mini_graph`
geometry so the browser can draw inline SVG at deterministic angles; to an agent
that is pure noise, and it is noise that costs tokens on every call. So nothing
here is a serialisation of a wire model -- every renderer reads NAMED FIELDS and
writes lines.

THAT RULE IS THE POINT, NOT A STYLE PREFERENCE. There is no `model_dump()`, no
`dict(...)`, no `**` anywhere in this module, and `tests/test_mcp_readonly.py`
greps for them. A field added to `Card` next month for the benefit of the reading
surface cannot leak into an agent's context by default; someone has to come here
and name it.

Two other rules, both learned from the shapes below:

  * NAMES FIRST, IDS ALONGSIDE. The name is what the agent is writing about; the
    id is only ever input to the next tool call.
  * EVERY CAP IS ANNOUNCED. A truncated list that does not say it was truncated
    is read as a complete one, which is worse than returning less.
"""

from __future__ import annotations

from typing import Any

from venona.untrust import inline, quote

# Per-tool ceilings, asserted against the seeded corpus in `tests/test_mcp_tools.py`.
# Sized by what the tool is for: `venona_evidence` is allowed the most because
# quoting sources IS its output, and `venona_path` the least because a rendered
# chain is one line.
BUDGET_CHARS = {
    "venona_search": 4000,
    "venona_entity": 3000,
    "venona_annotate": 8000,
    "venona_relations": 4000,
    "venona_evidence": 6000,
    "venona_path": 2000,
    "venona_briefs": 6000,
    "venona_ontology": 3000,
}


def clip(text: str, limit: int) -> str:
    """Truncate at a line boundary and SAY SO."""
    if len(text) <= limit:
        return text
    kept = text[:limit].rsplit("\n", 1)[0]
    return kept + "\n\n[truncated to fit a context window — narrow the query to see the rest]"


def _definition_lines(card: Any, indent: str = "  ") -> list[str]:
    """A definition, rendered as what it IS.

    Contract C8: `method` distinguishes a QUOTATION from a SYNTHESIS and the two
    must not blur. `in_article` and `web_grounded` carry a verbatim sentence and
    are quoted; `in_corpus` is written from several evidence sentences and is
    rendered as "drawn from N articles" with no quote. A synthesis shown as a
    quotation is exactly the failure invariant 3 exists to prevent.
    """
    definition = getattr(card, "definition", None)
    if not definition:
        return [f"{indent}definition: NONE — venona has no traceable definition for this."]

    method = getattr(card, "method", None) or "unknown"
    confidence = getattr(card, "confidence", None)
    conf = f", {confidence:.2f}" if isinstance(confidence, (int, float)) else ""
    out = [f"{indent}definition [{method}{conf}]: {inline(definition, limit=400)}"]

    grounding = getattr(card, "grounding", None)
    if grounding is not None:
        text = quote(
            getattr(grounding, "quote", None),
            source=getattr(grounding, "publisher", None) or getattr(grounding, "url", None),
        )
        if text:
            out += [f"{indent}{line}" for line in text.splitlines()]
        url = getattr(grounding, "url", None)
        if url:
            out.append(f"{indent}  source: {url}")
    else:
        sources = getattr(card, "definition_sources", None) or []
        if sources:
            out.append(f"{indent}  drawn from {len(sources)} articles (synthesis, not a quotation)")
    return out


def card_block(card: Any, *, full: bool = False) -> list[str]:
    name = inline(getattr(card, "name", None) or getattr(card, "surface", "") or "?")
    entity_id = getattr(card, "id", "?")
    kind = getattr(card, "kind", "") or ""
    status = getattr(card, "status", None)
    head = f"{name}   {entity_id}"
    if status:
        head += f"   {status}"
    lines = [head]

    expansion = getattr(card, "expansion", None)
    if expansion:
        lines.append(f"  expands to: {inline(expansion)}")

    aliases = getattr(card, "aliases", None) or []
    if aliases:
        shown = [inline(a, limit=80) for a in aliases[: 12 if full else 5]]
        more = f" (+{len(aliases) - len(shown)} more)" if len(aliases) > len(shown) else ""
        lines.append(f"  aka: {', '.join(shown)}{more}")

    lines += _definition_lines(card)

    # The ambiguous-acronym case. Loud, because silently picking one is the
    # single most damaging thing an agent can do with this corpus: a wrong
    # expansion becomes an alias and corrupts entity resolution downstream.
    alternatives = getattr(card, "alternatives", None) or []
    if alternatives:
        lines.append(
            f"  AMBIGUOUS — venona holds {len(alternatives) + 1} accepted readings for "
            f'"{name}". Do not pick one silently.'
        )
        for alt in alternatives[:4]:
            alt_expansion = inline(getattr(alt, "expansion", "") or "?")
            alt_method = getattr(alt, "method", None) or "?"
            lines.append(f"    · {alt_expansion}  [{alt_method}]")

    if full and kind:
        lines.append(f"  kind: {kind}")
    return lines


def search(cards: list[Any], missing: list[str]) -> str:
    if not cards:
        from venona.domain import get_domain

        return (
            "venona holds nothing matching that.\n"
            f"It indexes {get_domain().corpus_descriptor} only, and an entity appears "
            "once it has been "
            "seen in an article — so a name absent here has not been written about in the "
            "sources venona follows, which is itself a finding."
        )
    out: list[str] = []
    for card in cards:
        out += card_block(card)
        out.append("")
    if missing:
        out.append(f"no such id: {', '.join(missing)}")
    return "\n".join(out).rstrip()


def entity(card: Any) -> str:
    return "\n".join(card_block(card, full=True))


def relations(entity_id: str, entity_name: str, neighborhood: Any) -> str:
    """`Neighborhood` is nodes + edges, so a row is an edge joined to its far node.

    Direction is read off the edge rather than carried on the node, because the
    same neighbour can sit on both ends of two different predicates.
    """
    edges = getattr(neighborhood, "edges", None) or []
    nodes = {n.id: n for n in (getattr(neighborhood, "nodes", None) or [])}
    total = getattr(neighborhood, "total", 0)

    if not edges:
        return (
            f"venona holds no corroborated relationships for {inline(entity_name)}.\n"
            "Only entities corroborated from two sources are projected, so an absence "
            "here can mean 'not yet corroborated' rather than 'not asserted'."
        )

    out = [f"relationships for {inline(entity_name)}  ({entity_id})", ""]
    for edge in edges:
        outbound = edge.source == entity_id
        far_id = edge.target if outbound else edge.source
        far = nodes.get(far_id)
        far_name = inline(getattr(far, "name", None) or far_id)
        arrow = f"—{edge.predicate}→" if outbound else f"←{edge.predicate}—"
        plural = "s" if edge.source_count != 1 else ""
        out.append(f"  {arrow:<24} {far_name:<40} {far_id}")
        out.append(f"      {edge.source_count} source{plural}   edge_id={edge.id}")
        if edge.source_count == 1:
            out.append("      SINGLE-SOURCE — one publisher asserts this; not corroborated")

    if total and total > len(edges):
        out.append("")
        out.append(
            f"  showing {len(edges)} of {total} — raise `limit` (max 50) or "
            f"`min_confidence` to narrow."
        )
    out.append("")
    out.append("Pass an edge_id to venona_evidence to read the sentences behind a link.")
    return "\n".join(out)


def evidence(payload: dict[str, Any], *, max_quotes: int) -> str:
    """`edge_provenance` returns `source`/`target` endpoint dicts plus `assertions`.

    `source_count` is DISTINCT SITE DOMAINS, not publishers -- `graph_service`
    explains why (a display byline can come from the page's own `og:site_name`,
    so counting it made one outlet corroborate itself). Reporting the same number
    the canvas draws off means the two never disagree about whether an edge is
    single-source.
    """
    subject = inline((payload.get("source") or {}).get("name", "?"))
    obj = inline((payload.get("target") or {}).get("name", "?"))
    predicate = payload.get("predicate", "?")
    assertions = payload.get("assertions") or []
    source_count = payload.get("source_count", 0)

    out = [
        f"{subject} —{predicate}→ {obj}",
        f"asserted in {len(assertions)} article(s) across "
        f"{source_count} distinct publisher domain(s).",
    ]
    if payload.get("single_source"):
        out.append("SINGLE-SOURCE — one domain asserts this. It is not corroborated.")
    out.append("")

    for item in assertions[:max_quotes]:
        title = inline(item.get("title") or "", limit=140)
        publisher = inline(item.get("publisher") or "")
        published = item.get("published_at") or ""
        if publisher or published or title:
            byline = ", ".join(p for p in (publisher, published) if p)
            out.append(f"  {byline} — {title}" if title else f"  {byline}")
        else:
            # An assertion whose article is gone from the corpus -- a seeded
            # anchor, or a document purged since. Saying so beats printing
            # "?, ? —", which reads like a rendering bug rather than like a
            # claim with no citable source behind it.
            out.append("  no article on file for this assertion")
        if item.get("url"):
            out.append(f"  {item['url']}")
        text = quote(item.get("quote"), source=publisher)
        if text:
            out += [f"  {line}" for line in text.splitlines()]
        method = item.get("method")
        if method:
            confidence = item.get("confidence")
            conf = f", {confidence:.2f}" if isinstance(confidence, (int, float)) else ""
            out.append(f"  [{method}{conf}]")
        out.append("")

    if len(assertions) > max_quotes:
        out.append(f"  ... {len(assertions) - max_quotes} more (raise `max_quotes`, up to 20)")

    briefs = payload.get("briefs") or []
    if briefs:
        dates = ", ".join(str(b.get("date", "?")) for b in briefs[:6])
        out.append(f"also cited in {len(briefs)} brief(s): {dates}")
    return "\n".join(out).rstrip()


def path(source_name: str, target_name: str, payload: dict[str, Any]) -> str:
    paths = payload.get("paths") or []
    chains = payload.get("chains") or []
    if not paths:
        return (
            f"no path from {inline(source_name)} to {inline(target_name)} within the depth "
            f"searched.\nThe graph holds only corroborated, active entities, so an absence "
            f"here is 'venona cannot connect these', not 'these are unconnected'."
        )
    out = [f"{len(paths)} shortest path(s):", ""]
    for index, chain in enumerate(chains):
        out.append(f"  {inline(chain, limit=400)}")
        node_ids = paths[index].get("node_ids") if index < len(paths) else None
        if node_ids:
            out.append(f"    via: {' · '.join(node_ids)}")
        out.append("")
    return "\n".join(out).rstrip()


def annotate(
    recognised: list[Any], undefined: list[Any], ambiguous: list[Any], missing: list[str]
) -> str:
    out: list[str] = []
    out.append(f"RECOGNISED ({len(recognised)})")
    if not recognised:
        out.append("  nothing in this text matches an entity or term venona knows.")
    for card in recognised:
        name = inline(getattr(card, "name", None) or getattr(card, "surface", "?"))
        expansion = getattr(card, "expansion", None)
        detail = f" — {inline(expansion)}" if expansion else ""
        out.append(f"  {name}{detail}   {getattr(card, 'id', '?')}")

    out.append("")
    out.append(f"UNDEFINED ({len(undefined)})")
    if undefined:
        for card in undefined:
            name = inline(getattr(card, "name", None) or getattr(card, "surface", "?"))
            out.append(f"  {name}   {getattr(card, 'id', '?')}")
        out.append(
            "  venona has NO traceable definition for these. Do not expand them from "
            "memory: a definition exists only if it can be traced to a sentence in a "
            "document, and an invented expansion is the failure that rule exists to stop."
        )
    else:
        out.append("  none — every term venona recognised here is defined.")

    if ambiguous:
        out.append("")
        out.append(f"AMBIGUOUS ({len(ambiguous)})")
        for card in ambiguous:
            name = inline(getattr(card, "name", None) or getattr(card, "surface", "?"))
            count = len(getattr(card, "alternatives", None) or []) + 1
            out.append(
                f"  {name} — {count} accepted readings. "
                f"Call venona_entity('{getattr(card, 'id', '?')}') before using it."
            )

    # A reference the annotator resolved and the card builder could not answer
    # for. Saying so is the whole point: the three lists above are a promise
    # that everything recognised is accounted for, and dropping these silently
    # broke it in the direction that cannot be noticed -- a term venona DID
    # recognise reads exactly like a term it has never seen.
    if missing:
        out.append("")
        out.append(f"RECOGNISED BUT UNREADABLE ({len(missing)})")
        for ref in missing:
            out.append(f"  {ref}")
        out.append(
            "  venona matched these in the text and then could not open a record "
            "for them. Treat them as recognised and undefined, never as absent."
        )
    return "\n".join(out)


def briefs(hits: list[Any]) -> str:
    """`BriefSearchHit` is a `BriefSummary` plus capped `matches`.

    `where` is carried through because it changes what the answer means: a title
    match says the brief is ABOUT the thing, a body match says one item mentions
    it in passing. An agent deciding what to cite needs that distinction.
    """
    if not hits:
        return (
            "no brief matches that.\n"
            "Briefs are written only about what cleared the relevance profile, so a "
            "term venona knows can still appear in no brief."
        )
    out: list[str] = []
    for hit in hits:
        brief = getattr(hit, "brief", None)
        if brief is None:
            continue
        out.append(f"{getattr(brief, 'date', '?')}  {getattr(brief, 'id', '?')}")
        out.append(f"  {inline(getattr(brief, 'title', '') or '', limit=180)}")
        matches = getattr(hit, "matches", None) or []
        for match in matches[:3]:
            where = getattr(match, "where", "?")
            text = quote(getattr(match, "snippet", None), kind="brief-text", limit=300)
            out.append(f"  [{where}]")
            if text:
                out += [f"  {line}" for line in text.splitlines()]
        count = getattr(hit, "match_count", 0)
        if count > len(matches):
            out.append(f"  ({count} matches in this brief; {len(matches)} shown)")
        out.append("")
    out.append("Pass `brief_id` to read one in full, or brief_id='latest' for today's.")
    return "\n".join(out).rstrip()


def _blocks_text(blocks: list[Any]) -> str:
    return " ".join(getattr(b, "text", "") or "" for b in blocks).strip()


def brief_detail(brief: Any) -> str:
    """One brief, as prose.

    `summary` and `why` are kept APART, because that separation is the brief
    format's whole point (see `BriefItem`): what the article reports is not the
    same kind of claim as venona's judgement about it, and an agent that
    flattens them will attribute the judgement to the publisher.
    """
    out = [
        f"{getattr(brief, 'date', '?')}  {getattr(brief, 'id', '?')}",
        inline(getattr(brief, "title", "") or "", limit=200),
    ]
    dek = getattr(brief, "dek", None)
    if dek:
        out.append(inline(dek, limit=400))
    out.append("")

    for section in getattr(brief, "sections", None) or []:
        heading = getattr(section, "heading", None)
        if heading:
            out.append(f"## {inline(heading, limit=160)}")
            out.append("")
        for item in getattr(section, "items", None) or []:
            out.append(f"### {inline(getattr(item, 'headline', '') or '', limit=200)}")
            publisher = getattr(item, "publisher", None)
            published = getattr(item, "published_at", None)
            if publisher or published:
                out.append(f"  {inline(publisher or '?')}, {published or '?'}")
            url = getattr(item, "url", None)
            if url:
                out.append(f"  {url}")
            reported = _blocks_text(getattr(item, "summary", None) or [])
            if reported:
                out.append("  what the article reports:")
                text = quote(reported, kind="brief-text", limit=1200)
                out += [f"  {line}" for line in text.splitlines()]
            judgement = _blocks_text(getattr(item, "why", None) or [])
            if judgement:
                # venona's own words, not a publisher's -- so NOT wrapped as
                # untrusted. Labelling it is what stops an agent citing venona's
                # judgement to the outlet.
                out.append(f"  why it matters (venona's judgement): {inline(judgement, limit=800)}")
            out.append("")
        # Pre-item briefs kept their prose in `blocks`; the archive still holds
        # some, so rendering them is not dead code.
        legacy = _blocks_text(getattr(section, "blocks", None) or [])
        if legacy:
            text = quote(legacy, kind="brief-text", limit=1600)
            out += text.splitlines()
            out.append("")
    return "\n".join(out).rstrip()


def ontology(onto: Any) -> str:
    out = ["ENTITY TYPES (an id's prefix tells you its type)", ""]
    for name in onto.type_names:
        out.append(f"  {name:<20} {onto.type_prefix.get(name, '?')}:")
    out.append("")
    out.append("PREDICATES — the complete whitelist. Nothing else exists in the graph.")
    out.append("")
    for predicate in onto.predicate_names:
        symmetric = " (symmetric)" if predicate in onto.symmetric_predicates else ""
        out.append(f"  {predicate}{symmetric}")
    out.append("")
    out.append(
        "An edge_id is `subject_id|PREDICATE|object_id`. venona_relations prints one "
        "on every row; venona_evidence also accepts the three parts separately."
    )
    return "\n".join(out)
