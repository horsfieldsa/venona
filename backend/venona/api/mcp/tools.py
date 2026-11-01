"""The tool handlers. THE ONLY module here that imports `venona.services`.

Every one is thin on purpose: call the service function the REST API already
calls, hand the result to a renderer. Nothing is computed here that is not
computed for the reading surface, so the two can never disagree about what an
entity is.

READ-ONLY, AND PROVABLY SO. Three independent layers hold that, and they are
independent because each catches what the others miss:

  1. `scripts/lint_readonly.py` -- a grep, in `lint_determinism.py`'s style.
  2. `tests/test_mcp_readonly.py` parses the imports below and refuses any bound
     name outside an explicit allowlist. SYMBOL level, not module level, and that
     matters: `brief_service` is on the reuse list for `search_briefs`, and the
     same module holds `delete_brief`, which runs two DELETEs and a commit.
  3. The same test calls every tool in `registry.TOOLS` and snapshots per-table
     row counts across the sweep -- the pattern `tests/test_annotate_endpoint.py`
     already uses for `POST /api/annotate`, but driven off the registry so a tool
     added tomorrow is covered the day it is added rather than when someone
     remembers.

DELIBERATELY ABSENT, and the absence is the security control: anything that
starts a run, ingests a URL, grounds a term, merges an entity, writes config or
purges. An injection that names `venona_purge` names a tool that does not exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from venona.api.mcp import render
from venona.api.mcp.schemas import (
    AnnotateArgs,
    BriefsArgs,
    EntityArgs,
    EvidenceArgs,
    OntologyArgs,
    PathArgs,
    RelationsArgs,
    SearchArgs,
)
from venona.api.schemas.annotate import MAX_BLOCK_CHARS, MAX_BLOCKS

# Only the TYPE, never the module. `venona.db.core` also exposes `insert`,
# `upsert` and `transaction`, and C12's allowlist is symbol-level precisely so
# that a module import cannot smuggle write helpers into this file.
from venona.db.core import Conn
from venona.graph import queries
from venona.ontology import get_ontology
from venona.services.annotate_service import annotate_texts
from venona.services.brief_service import get_brief, latest_brief_id, search_briefs
from venona.services.entity_service import cards_batch, entity_card, glossary_card, neighborhood
from venona.services.entity_service import search as entity_search
from venona.services.graph_service import describe_path, edge_provenance


class ToolError(Exception):
    """A result the agent can act on, not a transport failure.

    Raised for "venona does not hold that", which is a FINDING and must not read
    like a broken tool -- the same honesty rule that makes a run which produced
    nothing `succeeded_partial` rather than `failed`. The dispatcher turns this
    into `isError: true` with the message intact, so the model sees the sentence
    and can change its query instead of retrying forever.
    """


@dataclass
class ToolContext:
    conn: Conn
    graph: Any | None = None


def _card_or_404(conn: Conn, entity_id: str):
    card = (
        glossary_card(conn, entity_id)
        if entity_id.startswith("gloss:")
        else entity_card(conn, entity_id)
    )
    if card is None:
        # Read from the ontology at the point of use, never frozen: the type set
        # is per-domain (ADR 0139), and a stale list sends an agent to guess a
        # prefix that was retired -- which reads back to it as "venona holds
        # nothing about this", the one answer a lookup tool must not invent.
        prefixes = ", ".join(
            f"{p}:" for p in sorted([*get_ontology().type_prefix.values(), "gloss"])
        )
        raise ToolError(
            f"venona holds no entity `{entity_id}`. Ids are type-prefixed "
            f"({prefixes}) — use venona_search to find the right one."
        )
    return card


def tool_search(ctx: ToolContext, args: SearchArgs) -> str:
    rows = entity_search(
        ctx.conn, args.q, limit=args.limit, include_provisional=args.include_provisional
    )
    ids = [row["id"] for row in rows]
    if not ids:
        return render.search([], [])
    cards, missing = cards_batch(ctx.conn, ids)

    # DEDUPED ON THE CARD, not on the id searched for. The query returns
    # distinct ids and `cards_batch` resolves merges, so two hits that fold onto
    # one survivor render it twice -- which is what a reader saw when a merge
    # left its loser `active`: searching the company's name returned the same
    # entity, under the same name, in consecutive rows. The data fault is worth
    # fixing on its own, and a lookup tool should not be the thing that reveals
    # it by repeating itself.
    ordered, seen = [], set()
    for i in ids:
        card = cards.get(i)
        key = getattr(card, "id", None) if card is not None else None
        if card is None or key in seen:
            continue
        seen.add(key)
        ordered.append(card)
    return render.search(ordered, missing)


def tool_entity(ctx: ToolContext, args: EntityArgs) -> str:
    return render.entity(_card_or_404(ctx.conn, args.entity_id))


def tool_relations(ctx: ToolContext, args: RelationsArgs) -> str:
    card = _card_or_404(ctx.conn, args.entity_id)
    hood = neighborhood(
        ctx.conn, args.entity_id, limit=args.limit, min_confidence=args.min_confidence
    )
    # The card resolves merges, the edges are keyed on the survivor, and the
    # renderer decides an arrow's direction by comparing the two. Handing it the
    # id the caller typed made every edge of a merged entity render inbound.
    resolved = getattr(card, "id", None) or args.entity_id
    name = getattr(card, "name", None) or getattr(card, "surface", None) or resolved
    return render.relations(resolved, name, hood)


def tool_evidence(ctx: ToolContext, args: EvidenceArgs) -> str:
    edge_id = args.edge_id or f"{args.subject_id}|{args.predicate}|{args.object_id}"
    payload = edge_provenance(ctx.conn, edge_id)
    if payload is None:
        raise ToolError(
            f"no assertion supports `{edge_id}`. venona does not hold this relationship.\n"
            f"Check the predicate against venona_ontology — only whitelisted predicates "
            f"exist — and the ids with venona_search. An absence here is a finding: it "
            f"means nothing venona has read asserts this link."
        )
    return render.evidence(payload, max_quotes=args.max_quotes)


async def tool_path(ctx: ToolContext, args: PathArgs) -> str:
    if args.from_entity_id == args.to_entity_id:
        raise ToolError("those are the same entity")
    source = _card_or_404(ctx.conn, args.from_entity_id)
    target = _card_or_404(ctx.conn, args.to_entity_id)
    # The projection holds only survivors, so a merged id has a card here and no
    # node in Neo4j -- asking with the raw id returned "no path" for two entities
    # that are one hop apart.
    from_id = getattr(source, "id", None) or args.from_entity_id
    to_id = getattr(target, "id", None) or args.to_entity_id
    if from_id == to_id:
        raise ToolError(
            f"`{args.from_entity_id}` and `{args.to_entity_id}` are two names for "
            f"one entity, so there is no path between them."
        )
    if ctx.graph is None:
        raise ToolError(
            "the Neo4j projection is unavailable. venona_relations answers one hop "
            "from Postgres, which is the system of record, and still works."
        )
    try:
        payload = await queries.shortest_paths(
            ctx.graph.driver, from_id, to_id, max_depth=args.max_depth
        )
    except Exception as exc:  # noqa: BLE001 - any driver failure is the same result here
        # Caught HERE rather than left to `errors.install`: those handlers stamp
        # an HTTP 503, which is the wrong layer for a JSON-RPC result and would
        # reach the agent as a transport failure rather than a readable answer.
        raise ToolError(
            "the Neo4j projection is unavailable. venona_relations answers one hop "
            "from Postgres, which is the system of record, and still works."
        ) from exc
    payload["chains"] = [
        describe_path(ctx.conn, p["node_ids"], payload["edges"]) for p in payload.get("paths", [])
    ]
    source_name = getattr(source, "name", None) or args.from_entity_id
    target_name = getattr(target, "name", None) or args.to_entity_id
    return render.path(source_name, target_name, payload)


def tool_annotate(ctx: ToolContext, args: AnnotateArgs) -> str:
    """Split a draft into paragraph blocks and resolve every venona term in it.

    Returns three named sections, NOT the `runs` structure `POST /api/annotate`
    serves. Those exist for DOM splicing and carry offsets, and CLAUDE.md's own
    reasoning applies: Python offsets are code points, JavaScript's are UTF-16
    units. To an agent editing prose they mean nothing at all.
    """
    paragraphs = [p.strip() for p in args.text.split("\n\n")]
    blocks: list[tuple[str, str, list[tuple[int, int]]]] = []
    for paragraph in paragraphs:
        if not paragraph:
            continue
        # Hard-split anything over the per-block cap rather than refusing the
        # call: a pasted draft with one enormous paragraph is a normal input.
        for offset in range(0, len(paragraph), MAX_BLOCK_CHARS):
            blocks.append((f"b{len(blocks)}", paragraph[offset : offset + MAX_BLOCK_CHARS], []))
            if len(blocks) >= MAX_BLOCKS:
                break
        if len(blocks) >= MAX_BLOCKS:
            break

    if not blocks:
        raise ToolError("there is no prose in that text")

    _annotated, refs = annotate_texts(ctx.conn, blocks)
    if not refs:
        return render.annotate([], [], [], [])

    cards, missing = cards_batch(ctx.conn, refs)
    recognised, undefined, ambiguous = [], [], []
    for ref in refs:
        card = cards.get(ref)
        if card is None:
            continue
        if getattr(card, "alternatives", None):
            ambiguous.append(card)
        elif getattr(card, "definition", None):
            recognised.append(card)
        else:
            undefined.append(card)
    # `missing` is rendered rather than discarded. `cards_batch` de-dupes, so it
    # is already one entry per reference.
    return render.annotate(recognised, undefined, ambiguous, missing)


def tool_briefs(ctx: ToolContext, args: BriefsArgs) -> str:
    if args.brief_id:
        brief_id = args.brief_id
        if brief_id == "latest":
            brief_id = latest_brief_id(ctx.conn) or ""
        brief = get_brief(ctx.conn, brief_id) if brief_id else None
        if brief is None:
            raise ToolError(f"no brief `{args.brief_id}`")
        return render.brief_detail(brief)
    results = search_briefs(ctx.conn, q=args.q, entity=args.entity_id, limit=args.limit)
    # `.items`, and read as an attribute. A `getattr(..., default)` for a field
    # the model declares turns a rename into an empty result rather than an
    # error: the agent is told the archive contains nothing that matches, which
    # is a sentence it has no reason to doubt and no way to check.
    return render.briefs(results.items)


def tool_ontology(ctx: ToolContext, args: OntologyArgs) -> str:
    # Called per request, never bound at import: the ontology is editable over
    # HTTP and a snapshot would silently drop a predicate that was just added.
    return render.ontology(get_ontology())
