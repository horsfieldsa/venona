"""Assemble a stored brief into the C3 wire format.

Blocks are stored with offsets (canonical, recomputable); this is where they
become pre-split runs.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from venona.api.schemas.brief import (
    Brief,
    BriefBlock,
    BriefItem,
    BriefSearchHit,
    BriefSearchMatch,
    BriefSearchResults,
    BriefSection,
    BriefSource,
    BriefSummary,
    BriefTop,
    Freshness,
    TextRun,
)
from venona.db import core as db
from venona.db.repositories import brief as repo
from venona.services import annotate_service
from venona.services.annotate import Block, Mark, blocks_to_runs

log = logging.getLogger("venona.brief")


def _row_to_block(row: db.Row) -> Block:
    return Block(
        # `brief_blocks.id` carries the whole brief id ahead of the block id, so
        # the LAST colon-separated field is the one wanted -- safe only while no
        # part of a brief id contains a colon (C3).
        id=row["id"].split(":")[-1],
        type=row["type"],
        depth=row["depth"] or 0,
        text=row["text"],
        section=row["section"],
        heading=row["heading"],
        ordinal=row["ordinal"],
        marks=[Mark(**m) for m in db.jload(row["marks"], [])],
        spans=db.jload(row["spans"], []),
    )


def _age_hours(published_at: str | None, *, now: str) -> int | None:
    """Age at GENERATION time, not at read time: a brief is a snapshot, and
    recomputing the badge would relabel last Tuesday's "3H" item as "5D" (C3)."""
    if not published_at:
        return None
    try:
        then = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        moment = datetime.fromisoformat(now.replace("Z", "+00:00"))
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max(0, int((moment - then).total_seconds() // 3600))


def _freshness(ages: list[int | None]) -> Freshness:
    known = [a for a in ages if a is not None]
    return Freshness(
        under_24h=sum(1 for a in known if a < 24),
        one_to_three_days=sum(1 for a in known if 24 <= a < 72),
        older=sum(1 for a in known if a >= 72),
        total=len(ages),
    )


@dataclass(frozen=True)
class BriefEntities:
    """Both halves, because the caller needs both.

    `named` is what the brief literally says and is what `get_brief` reports.
    `resolved` is what the graph can actually be asked about. Reporting the pair
    is what lets a brief-scoped view say "this brief named 48 entities; 31 are in
    the graph" rather than quietly showing 31 and implying that was all there
    was.
    """

    named: list[str]
    resolved: list[str]


def brief_entities(conn: db.Conn, brief_id: str) -> BriefEntities | None:
    """The GRAPH entities a brief names, raw and merge-resolved, in document order.

    `None` when there is no such brief -- the same convention `get_brief` uses,
    so a caller can answer 404 rather than "a brief that mentions nothing".

    Deliberately NOT `get_brief(...).entities`. That builds every block into
    runs, joins attribution for every item and fetches every source document,
    to yield a list of ids. Same partition rule, same order, one query -- and
    `tests/test_brief.py` pins the two against each other so they cannot drift.

    MERGES ARE RESOLVED HERE, which is the whole reason the graph asks for a
    brief id rather than being handed a list of ids by the client. A brief
    written before a merge names the loser; the projector collapsed that id onto
    the survivor, so the loser is not a node in Neo4j at all. An unresolved id
    set silently omits exactly the entities a merge touched, and the client
    cannot see `entity_mappings` and should not be able to.

    Glossary and undefined refs are excluded: `gloss:` is not an ontology type
    and is not projected (C4).
    """
    from venona.graph.projector import resolve_mappings

    if repo.select_briefs_by_id_2(conn, brief_id).fetchone() is None:
        return None

    rows = repo.select_brief_blocks_by_brief_id(conn, brief_id).fetchall()

    # ONE `seen` set across both kinds, matching `get_brief`. The partitions
    # cannot actually collide -- an entity ref is type-prefixed, a glossary ref
    # is `gloss:` -- but the pinning test compares the two outputs exactly, so
    # the rule is copied rather than reasoned about.
    named: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for span in db.jload(row["spans"], []):
            ref = span["ref"]
            if ref in seen:
                continue
            seen.add(ref)
            if span["kind"] == "entity":
                named.append(ref)

    mappings = resolve_mappings(conn)
    resolved: list[str] = []
    survivors: set[str] = set()
    for entity_id in named:
        survivor = mappings.get(entity_id, entity_id)
        # A merge can collapse two ids the brief named onto one survivor, so
        # this dedupes a second time.
        if survivor in survivors:
            continue
        survivors.add(survivor)
        resolved.append(survivor)
    return BriefEntities(named=named, resolved=resolved)


@dataclass
class AssembledBody:
    """The item-and-block half of a Brief, shared by both documents.

    `get_brief` (the shared archive) and `personal_brief.render_brief` (the
    reader's own document) assemble items identically, so the logic lives once,
    here. Sections group by the stored section value in the order the item rows
    arrive, never by consulting `profile/brief_sections.yaml` -- which is why
    renaming a desk leaves the archive under its old heading (C3).
    """

    sections: list[BriefSection]
    tops: list[BriefTop]
    freshness: Freshness
    title_runs: list[TextRun]
    entities: list[str]
    glossary: list[str]


def assemble_body(
    conn: db.Conn,
    *,
    title: str,
    item_rows: list[db.Row],
    block_rows: list[db.Row],
    generated_at: str,
    log_key: str,
) -> AssembledBody:
    entity_ids: list[str] = []
    glossary_ids: list[str] = []
    seen: set[str] = set()

    # A span's `kind` is frozen when the brief is annotated, and `undefined` is
    # the one value that can stop being true afterwards. `TextRuns.vue` draws
    # `mention--undefined` straight from `run.k`, so an acronym grounded by a
    # later tier-3 lookup kept its dashed underline and its "No grounded
    # definition found" tooltip for the life of the brief, while the hover card
    # resolved the ref live and showed the definition perfectly: the two
    # disagreeing about the same word on the same screen. Re-annotating the
    # stored blocks is the worse fix, because the brief's blocks are what was
    # published. This corrects the RENDERING at read time, from the system of
    # record, exactly as `_grounding` does for the card.
    now_defined = {r["id"] for r in repo.select_acronym_definitions(conn)}

    def to_wire(block_row: db.Row) -> BriefBlock:
        block = _row_to_block(block_row)
        for span in block.spans:
            ref = span["ref"]
            if span["kind"] == "undefined" and ref in now_defined:
                span["kind"] = "glossary"
            if ref not in seen:
                seen.add(ref)
                (glossary_ids if span["kind"] != "entity" else entity_ids).append(ref)
        return BriefBlock(
            id=block.id,
            type=block.type,
            depth=block.depth,
            runs=[TextRun(**run) for run in blocks_to_runs(block)],
        )

    # The title and every headline, annotated at READ time against the current
    # alias index -- `annotate_service`, the extension's path, whose precision
    # posture (strict case, whole-corpus tier_a) is the one already measured on
    # text with no retrieval set. Read time because titles carry no stored
    # spans: nothing published is rewritten and the whole archive gains it at
    # once. Decoration degrades, it never breaks the document -- any failure
    # here renders the plain strings the wire carried anyway.
    title_key = "__title__"
    to_annotate = [(title_key, title, [])]
    to_annotate += [(r["id"], r["headline"] or "", []) for r in item_rows]
    try:
        annotated, _refs = annotate_service.annotate_texts(conn, to_annotate)
        runs_by_key = {key: [TextRun(**run) for run in runs] for key, runs in annotated}
    except Exception:
        log.warning("title annotation failed for %s; rendering plain", log_key, exc_info=True)
        runs_by_key = {}

    by_item: dict[str, list[db.Row]] = {}
    legacy: list[db.Row] = []
    for block_row in block_rows:
        item_id = block_row["item_id"] if "item_id" in block_row.keys() else None
        if item_id:
            by_item.setdefault(item_id, []).append(block_row)
        else:
            legacy.append(block_row)

    sections: list[BriefSection] = []
    section_index: dict[str, BriefSection] = {}
    tops: list[BriefTop] = []
    ages: list[int | None] = []

    for item_row in item_rows:
        age = _age_hours(item_row["published_at"], now=generated_at)
        ages.append(age)
        blocks = by_item.get(item_row["id"], [])
        item = BriefItem(
            id=item_row["id"],
            headline=item_row["headline"],
            headline_runs=runs_by_key.get(item_row["id"], []),
            state=item_row["state"] or "new",
            state_note=item_row["state_note"],
            publisher=item_row["publisher"],
            url=item_row["url"],
            published_at=item_row["published_at"],
            age_hours=age,
            summary=[to_wire(b) for b in blocks if b["role"] == "summary"],
            why=[to_wire(b) for b in blocks if b["role"] == "why_it_matters"],
        )
        name = item_row["section"]
        if name not in section_index:
            section_index[name] = BriefSection(heading=name, blocks=[], items=[])
            sections.append(section_index[name])
        section_index[name].items.append(item)

        if item_row["top_rank"] and item_row["top_line"]:
            tops.append(BriefTop(item_id=item_row["id"], line=item_row["top_line"], age_hours=age))

    tops.sort(key=lambda t: t.item_id)

    # Item-less rows render as narrative sections, split on the stored heading.
    # Dropping the branch would silently empty every pre-item brief (C3).
    if legacy:
        current: str | None = ...  # sentinel: the first block always opens a section
        for block_row in legacy:
            if block_row["heading"] != current:
                current = block_row["heading"]
                sections.append(BriefSection(heading=block_row["heading"], blocks=[], items=[]))
            sections[-1].blocks.append(to_wire(block_row))

    return AssembledBody(
        sections=sections,
        tops=tops,
        freshness=_freshness(ages),
        title_runs=runs_by_key.get(title_key, []),
        entities=entity_ids,
        glossary=glossary_ids,
    )


def brief_sources(conn: db.Conn, source_ids: Any) -> list[BriefSource]:
    """The Sources footer, from a stored `source_ids` column.

    Shared by the daily brief and the reader's own so the footer means the
    same thing on both: every article the run read, with its attribution
    joined from `documents` rather than restated by the model.
    """
    ids = list(db.jload(source_ids, []))
    if not ids:
        return []
    return [
        BriefSource(
            article_id=doc["id"],
            title=doc["title"],
            url=doc["url"],
            publisher=doc["publisher"],
            published_at=doc["published_at"],
        )
        for doc in repo.sources_by_ids(conn, ids)
    ]


def brief_from(
    conn: db.Conn,
    row: db.Row,
    body: AssembledBody,
    *,
    unresolved_mentions: list[str] | None = None,
    older_id: str | None = None,
    newer_id: str | None = None,
) -> Brief:
    """One stored brief plus its assembled body, in the wire shape.

    Two paths reach this shape -- the shared archive and a reader's own brief --
    and thirteen of the sixteen fields are identical across both.
    `response_model` FILTERS, so a field wired into only one of them fails
    nowhere: it silently vanishes from the other brief's JSON and the surface
    that reads it renders a default. Spelling the shape once is what makes that
    impossible.

    The three that genuinely differ are arguments. `unresolved_mentions` is the
    shared curation backlog and a per-reader document must never feed it; the
    neighbour ids are archive order, which a personal brief is not part of.
    """
    return Brief(
        id=row["id"],
        date=row["date"],
        run_id=row["run_id"],
        title=row["title"],
        title_runs=body.title_runs,
        dek=row["dek"],
        generated_at=row["generated_at"],
        freshness=body.freshness,
        top=body.tops,
        sections=body.sections,
        entities=body.entities,
        glossary=body.glossary,
        unresolved_mentions=unresolved_mentions or [],
        sources=brief_sources(conn, row["source_ids"]),
        older_id=older_id,
        newer_id=newer_id,
    )


def get_brief(conn: db.Conn, brief_id: str, *, ceiling: str | None = None) -> Brief | None:
    """One brief, or None -- including when it exists but sits past this
    reader's archive ceiling (ADR 0211). A 404 rather than a 403 on purpose: a
    403 would confirm the brief exists, which is the thing being withheld."""
    row = repo.select_briefs_by_id(conn, brief_id, ceiling=ceiling).fetchone()
    if row is None:
        return None

    body = assemble_body(
        conn,
        title=row["title"] or "",
        # This query joins attribution from `documents` via `cluster_id`, so no
        # claim can be hung on the wrong outlet by the model (C3).
        item_rows=repo.select_brief_items_by_brief_id(conn, brief_id).fetchall(),
        block_rows=repo.select_brief_blocks_by_brief_id_2(conn, brief_id).fetchall(),
        generated_at=row["generated_at"],
        log_key=brief_id,
    )

    # The ceiling reaches the ARROWS too. Without it, a lapsed reader sitting on
    # their last brief is handed a working link to the one immediately past it,
    # which is the one they most want and least may have.
    neighbours = repo.select_brief_neighbours(conn, row["id"], ceiling=ceiling).fetchone()

    return brief_from(
        conn,
        row,
        body,
        unresolved_mentions=db.jload(row["unresolved_mentions"], []),
        older_id=neighbours["older_id"] if neighbours else None,
        newer_id=neighbours["newer_id"] if neighbours else None,
    )


def latest_brief_id(conn: db.Conn, *, ceiling: str | None = None) -> str | None:
    """The newest brief this reader may open. For a lapsed reader that is the
    last one they paid for, not a refusal -- see `repositories/brief.py`."""
    row = repo.select_briefs(conn, ceiling=ceiling).fetchone()
    return row["id"] if row else None


def latest_brief_generated_at(conn: db.Conn) -> str | None:
    """When reading last landed. None on an instance that has never run.

    Beside `latest_brief_id` because they are the same question asked for two
    purposes, and apart from it because they order differently: the id is for
    "which brief does the reader open", which the editorial date leads, and this
    is for "is there anything new", which is wall-clock.
    """
    row = repo.select_latest_brief_generated_at(conn).fetchone()
    return row["generated_at"] if row else None


def list_briefs(conn: db.Conn, *, limit: int = 20, offset: int = 0, ceiling: str | None = None):
    """The archive. `generated_at` is not decoration: a day can hold several
    briefs, and the date alone does not tell them apart.

    The ceiling reaches the COUNT as well as the page, or a lapsed reader is
    told the archive holds four hundred briefs and shown forty.
    """
    rows = repo.select_brief_blocks(conn, limit, offset, ceiling=ceiling).fetchall()
    total = repo.select_briefs_2(conn, ceiling=ceiling).fetchone()["n"]
    items = [
        BriefSummary(
            id=r["id"],
            date=r["date"],
            title=r["title"],
            dek=r["dek"],
            run_id=r["run_id"],
            generated_at=r["generated_at"],
            block_count=r["blocks"],
        )
        for r in rows
    ]
    return items, total


# ── search over brief content ────────────────────────────────────────
#
# Search reaches the BODY of an item, not only its title and dek, because the
# data is already there: `brief_blocks.text` is the plain text the runs
# concatenate back to, and `brief_blocks.spans` carries the entity references
# per block.
#
# `ILIKE '%x%'` rather than real full-text search, and so unable to use an
# index -- deliberate against a corpus this size. `docs/pm/NOT-DOING.md` ("Real
# full-text search") holds the argument and the trigger for revisiting it.

#: Matches shown per brief, so one brief repeating a name cannot crowd the rest
#: of the archive out of the list.
MATCHES_PER_BRIEF = 3

#: Characters of context either side of a hit.
SNIPPET_PAD = 90


def _snippet(text: str, needle: str, pad: int = SNIPPET_PAD) -> str:
    """The hit with context, ellipsised at both ends when trimmed.

    Case-insensitive, and it returns the ORIGINAL casing -- searching `ngc2`
    must not render the brief as though it had been written in lower case.
    """
    at = text.lower().find(needle.lower())
    if at == -1:
        return text[: pad * 2] + ("\u2026" if len(text) > pad * 2 else "")
    start = max(0, at - pad)
    end = min(len(text), at + len(needle) + pad)
    return (
        ("\u2026" if start > 0 else "")
        + text[start:end].strip()
        + ("\u2026" if end < len(text) else "")
    )


def search_briefs(
    conn: db.Conn,
    *,
    q: str = "",
    entity: str | None = None,
    limit: int = 20,
    ceiling: str | None = None,
) -> BriefSearchResults:
    """Which briefs mention this, and where.

    Two independent filters, ANDed when both are given:

      * `q` matches the brief title, the dek, an item headline or a block's
        text.
      * `entity` matches an entity id in `brief_blocks.spans`, which is how
        "which briefs mention `prog:x`" is answered without a text search at
        all -- the annotation already resolved it, so this finds mentions under
        every alias including ones the reader would not think to type.

    Briefs are ordered newest first, like the archive itself. Ordering by match
    count was considered and rejected: the archive is a chronology, and a brief
    jumping to the top because it repeated a word reads as a relevance ranking
    the system has no basis for.

    THE FILTER RUNS IN SQL AND THE PAGE IS BOUNDED, so the detail below is read
    for the briefs on the page rather than for the whole archive -- and in one
    statement each, not one per brief. `total` is counted separately by the same
    predicate, so a bounded page still reports honestly how much matched.

    There is no `offset`, deliberately. Rows are dropped here after the SQL
    window (a brief SQL matched can score zero on the recount -- the needle
    reaches `LIKE` unescaped, so a `%` in the query matches more there than it
    does in Python), and a window over a set filtered afterwards skips rows at
    the page edge. Same reasoning as the conflicts queue; see ADR 0154.
    """
    needle = q.strip()
    if not needle and not entity:
        return BriefSearchResults(query="", entity=entity)

    like = f"%{needle}%"
    hits: list[BriefSearchHit] = []

    total = repo.count_briefs_matching(conn, needle or None, entity, ceiling=ceiling)
    briefs = repo.select_briefs_matching(
        conn, needle or None, entity, limit, ceiling=ceiling
    ).fetchall()

    # Every detail read for the whole page, before the loop rather than inside
    # it. The page is bounded, but `limit` reaches 200, and three statements per
    # row is six hundred round trips to describe one screen.
    ids = [row["id"] for row in briefs]
    headlines_by_brief: dict[str, list[db.Row]] = defaultdict(list)
    blocks_by_brief: dict[str, list[db.Row]] = defaultdict(list)
    referenced_by_brief: dict[str, int] = {}
    if ids and needle:
        for item in repo.brief_items_matching(conn, ids, like):
            headlines_by_brief[item["brief_id"]].append(item)
        for block in repo.brief_blocks_matching(conn, ids, like):
            blocks_by_brief[block["brief_id"]].append(block)
    if ids and entity:
        referenced_by_brief = repo.brief_blocks_referencing(conn, ids, entity)

    for row in briefs:
        matches: list[BriefSearchMatch] = []
        count = 0

        if needle:
            for where, text in (("title", row["title"]), ("dek", row["dek"])):
                if text and needle.lower() in text.lower():
                    count += 1
                    if len(matches) < MATCHES_PER_BRIEF:
                        matches.append(
                            BriefSearchMatch(where=where, snippet=_snippet(text, needle))
                        )

            for item in headlines_by_brief[row["id"]]:
                count += 1
                if len(matches) < MATCHES_PER_BRIEF:
                    matches.append(
                        BriefSearchMatch(
                            where="headline",
                            item_id=item["id"],
                            headline=item["headline"],
                            snippet=_snippet(item["headline"], needle),
                        )
                    )

            for block in blocks_by_brief[row["id"]]:
                count += 1
                if len(matches) < MATCHES_PER_BRIEF:
                    matches.append(
                        BriefSearchMatch(
                            where="body",
                            item_id=block["item_id"],
                            headline=block["headline"],
                            snippet=_snippet(block["text"], needle),
                        )
                    )

        if entity:
            referenced = referenced_by_brief.get(row["id"], 0)
            if not referenced:
                continue
            if not needle:
                count += referenced

        if not count:
            continue

        hits.append(
            BriefSearchHit(
                brief=BriefSummary(
                    id=row["id"],
                    date=row["date"],
                    title=row["title"],
                    dek=row["dek"],
                    run_id=row["run_id"],
                    generated_at=row["generated_at"],
                    block_count=row["blocks"],
                ),
                matches=matches,
                match_count=count,
            )
        )

    return BriefSearchResults(
        query=needle,
        entity=entity,
        items=hits,
        total=total,
        truncated=total > len(hits),
    )


def delete_brief(conn: db.Conn, brief_id: str) -> dict[str, int]:
    """Remove a brief and everything hanging off it.

    This is what makes the tuning loop work: regenerating a date mints a second
    brief rather than overwriting the first (ADR 0080), and deleting one
    releases its `brief_clusters` rows so the next may cover those stories again.

    `brief_blocks` and `brief_clusters` cascade (`ON DELETE CASCADE`, which
    Postgres enforces without being asked). **`brief_items` does not** --
    `0001_baseline.sql` declares its `brief_id` with no REFERENCES -- so it is
    deleted explicitly and must stay that way.
    """
    if repo.select_briefs_by_id_3(conn, brief_id).fetchone() is None:
        raise LookupError(brief_id)

    counts = {
        table: repo.count_for_brief(conn, table, brief_id)
        for table in ("brief_items", "brief_blocks", "brief_clusters")
    }
    # Both deletes or neither. `brief_items` has no cascade, so a failure
    # between the two leaves its rows orphaned under a brief id nothing can
    # reach -- invisible, and counted by nothing.
    with db.transaction(conn, what="delete_brief"):
        repo.delete_brief_items_by_brief_id(conn, brief_id)
        repo.delete_briefs_by_id(conn, brief_id)
    return counts
