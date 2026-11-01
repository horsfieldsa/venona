"""Propose the edges that would attach an unconnected entity to the spine.

`profile/anchors.yaml` declares the trunk by hand. This finds the branches that
never reached it: entities that are `active`, so a reader can see them, and hold
no relationship at all, so the explorer draws them floating.

**Nothing here writes an assertion.** Every proposal is an `alias_conflicts` row
and reaches the graph only when a person presses a key. That is not caution for
its own sake -- `CLAUDE.md` is explicit that a wrong edge is invisible and
poisons every downstream query, and 90 machine-minted edges written straight to
`assertions` is exactly the failure it names. The queue already exists
(`MergeReview.vue` plus `POST /curation/conflicts/{id}/relate`), so proposing
into it costs one row and reuses the entire approval path: the well-typed
predicate list, the direction toggle, keep-apart, undo, and the pending-rebuild
banner. Migration 0006 dropped a second queue for exactly this reason -- two
queues for one decision is one too many.

Two passes, cheapest first:

  1. **Deterministic.** Transitive lift (the projection already implies the
     edge) and unit designators (`4th Infantry Division` belongs to a service).
     Free, no model, and it is worth reading its output before paying for the
     second pass.
  2. **The model reads names.** One call per batch, mirroring
     `curate.py::adjudicate`: `call_and_record`, budget-checked, `--json-schema`,
     replayable offline. Both sides of the schema are enums of NAMES -- the
     model returns surfaces and code assigns ids.

The model pass departs from `adjudicate_system()` on one point, deliberately.
That prompt ends "Never guess from outside knowledge of these organisations",
because an acronym expansion must be traceable to a sentence (invariant 3).
Invariant 3 governs DEFINITIONS. A structural edge is a different claim: no
article states that Anthropic is an American company, and the `relate` endpoint
already writes a quote naming the decision rather than a sentence from a
document. The safety here is the keystroke, not the sentence.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from venona.db import core as db
from venona.db.models import normalize
from venona.db.repositories import pipeline_anchors as repo
from venona.ontology import get_ontology
from venona.pipeline.types import RunContext
from venona.services.seed import ANCHOR_RUN_ID

CONFLICT_KIND = "anchor"


@dataclass
class AnchorReport:
    isolated: int = 0
    proposed: int = 0
    from_transitive: int = 0
    from_model: int = 0
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, int]:
        return {k: v for k, v in self.__dict__.items() if isinstance(v, int)}


@dataclass
class Proposal:
    child_id: str
    anchor_id: str
    predicate: str
    why: str
    #: What the model said about its own certainty, or "" for a proposal no
    #: model made. Recorded so the error rate per tier is answerable LATER,
    #: against verdicts a curator actually gave. Nothing reads it to decide
    #: anything, and until that measurement exists nothing should: self-reported
    #: confidence is the gate `pipeline/resolve.py` already removed once.
    confidence: str = ""


def projected_graph(conn: db.Conn) -> tuple[set[str], set[tuple[str, str, str]]]:
    """The active entity ids and the relation triples that actually project.

    Merge-resolved exactly as `graph/projector.py` does it, and shared by both
    `isolated_entities` and `venona anchors status`. Two implementations of "is
    this node connected" that disagreed would have the proposal queue arguing
    with the explorer about which nodes are floating.
    """
    from venona.graph.projector import resolve_mappings

    mappings = resolve_mappings(conn)
    # A node that is a merge SOURCE is skipped by `_entity_rows`, so Neo4j never
    # draws it and it cannot be floating there. Counting it here made this the
    # second opinion the docstring above says it must not be: the queue could
    # offer a curator a placement for a node the explorer does not show.
    active = {
        r["id"] for r in repo.select_entities(conn) if mappings.get(r["id"], r["id"]) == r["id"]
    }

    triples: set[tuple[str, str, str]] = set()
    for row in repo.select_assertions(conn):
        src = mappings.get(row["s"], row["s"])
        dst = mappings.get(row["o"], row["o"])
        if src != dst and src in active and dst in active:
            triples.add((src, row["p"], dst))
    return active, triples


def isolated_entities(conn: db.Conn) -> list[db.Row]:
    """Active entities the explorer draws floating.

    Isolated in the PROJECTION, which is weaker than having no assertions: an
    entity can hold three relationships and still float, because every one of
    them points at something uncorroborated and `writer.upsert_edges`'s
    `MATCH (a), (b)` drops an edge with no node at one end. Both kinds need
    attaching, and they need attaching differently -- the first by a transitive
    lift over evidence already on disk, the second by somebody knowing where it
    belongs.

    Ordered by mention count, so the ones a reader actually meets come first.
    """
    _, triples = projected_graph(conn)
    connected = {t[0] for t in triples} | {t[2] for t in triples}

    return [row for row in repo.select_assertions_2(conn) if row["id"] not in connected]


HUB_DEGREE = 4


def anchor_entities(conn: db.Conn) -> list[db.Row]:
    """Everything an isolated entity could reasonably be attached TO.

    Declared anchors -- every Country, and everything `profile/anchors.yaml`
    names -- plus whatever has become a hub on its own: an entity already
    carrying several relationships is, empirically, part of the spine.

    One row per id, and one per NAME. The model chooses by name and code maps it
    back, so two anchors sharing a name would make that mapping ambiguous and
    the choice unusable. Where names collide the better-connected one wins.
    """
    rows = repo.select_assertions_by_run_id(conn, ANCHOR_RUN_ID).fetchall()

    by_name: dict[str, db.Row] = {}
    for row in rows:
        if not row["declared"] and row["degree"] < HUB_DEGREE:
            continue
        if row["name"] not in by_name:
            by_name[row["name"]] = row
    return list(by_name.values())


def _existing_pairs(conn: db.Conn) -> set[frozenset[str]]:
    """Pairs already decided, in either direction.

    Includes RESOLVED rows: re-proposing something the curator kept apart last
    week is how a queue becomes noise nobody reads.
    """
    return {
        frozenset((r["existing_entity_id"], r["proposed_entity_id"]))
        for r in repo.select_alias_conflicts(conn)
    }


def transitive_proposals(
    conn: db.Conn,
    isolated: list[db.Row],
    anchors: set[str],
    *,
    names: dict[str, str] | None = None,
) -> list[Proposal]:
    """PART_OF chains the projection cannot draw because the middle is missing.

    An isolated node is often not isolated in Postgres at all: it has an assertion
    whose other endpoint is provisional, so `writer.upsert_edges`'s
    `MATCH (a), (b)` drops the row. Where `X PART_OF Y` and `Y PART_OF Z` are
    both on disk and Z is an anchor, `X PART_OF Z` is already implied.

    STRICTLY PART_OF, and strictly in the subject direction. Relations in
    general are not transitive, and an earlier version of this that walked any
    two edges produced `General Atomics PART_OF Northrop Grumman` (they are
    competitors) and `Collaborative Combat Aircraft PART_OF RTX` (a program is
    not part of a company) from nothing more than shared neighbours. Containment
    is the one predicate here that composes; everything else is a co-occurrence
    dressed up as a claim, and a plausible wrong proposal is worse than none
    because a curator has to think about it.

    The grandparent must itself be an ANCHOR. Accepting any active grandparent
    would attach the child to something that is floating too, which proposes a
    second orphan branch rather than reaching the spine -- the entire point of
    the pass.
    """
    names = names or {}

    def label(entity_id: str) -> str:
        return names.get(entity_id, entity_id)

    proposals: list[Proposal] = []
    for row in isolated:
        parent = repo.select_assertions_by_subject_id_raw(conn, row["id"]).fetchone()
        if parent is None or parent["p"] in anchors:
            continue
        grandparent = next(
            (
                candidate
                for candidate in repo.select_assertions_by_subject_id_raw_2(conn, parent["p"])
                if candidate["p"] in anchors
            ),
            None,
        )
        if grandparent is None:
            continue
        proposals.append(
            Proposal(
                child_id=row["id"],
                anchor_id=grandparent["p"],
                predicate="PART_OF",
                # Names, not ids, and both ends of the chain: this is the whole
                # of what the curator reads before pressing the key.
                why=(
                    f"PART_OF {label(parent['p'])}, which is PART_OF "
                    f"{label(grandparent['p'])} — the middle is uncorroborated, "
                    f"so the projection cannot draw the chain"
                ),
            )
        )
    return proposals


def file_proposal(conn: db.Conn, proposal: Proposal, *, names: dict[str, str], now: str) -> bool:
    """Write one proposal as an open `alias_conflicts` row.

    `resolved` is deliberately NOT in the payload. `db.upsert` writes every
    column it is given, so passing `resolved: 0` would silently reopen a verdict
    the curator had already recorded -- the same trap `resolve.py::_add_alias`
    documents.
    """
    types = {
        r["id"]: r["type"]
        for r in repo.select_entities_2(conn, proposal.child_id, proposal.anchor_id)
    }
    if len(types) != 2:
        return False
    if get_ontology().check_relation(
        proposal.predicate, types[proposal.child_id], types[proposal.anchor_id]
    ):
        return False

    child_name = names.get(proposal.child_id, proposal.child_id)
    db.upsert(
        conn,
        "alias_conflicts",
        {
            "id": f"anchor|{proposal.child_id}|{proposal.anchor_id}",
            "kind": CONFLICT_KIND,
            "surface": child_name,
            "normalized": normalize(child_name),
            # The anchor is `existing`, the thing being attached is `proposed`.
            "existing_entity_id": proposal.anchor_id,
            "proposed_entity_id": proposal.child_id,
            "entity_type": types[proposal.child_id],
            "suggestion": "part_of",
            # The ACTION stays `part_of` because that is what the button does;
            # the predicate is a separate column the conflict payload already
            # reads. It was written only into the note, so a BASED_IN placement
            # arrived at the curator labelled as a different relationship.
            "suggestion_predicate": proposal.predicate,
            # Read by MergeReview's pair watcher to orient the direction toggle,
            # so `R` is pre-armed with the right subject.
            "suggestion_child": proposal.child_id,
            # The predicate leads the note. `suggestion` is an ACTION
            # (merge/part_of/distinct) and has no room for one, and where more
            # than one predicate is well-typed for the pair the curator should
            # see which was meant before picking from the dropdown.
            "suggestion_note": (
                f"{proposal.predicate} — {proposal.why}"
                + (f" ({proposal.confidence})" if proposal.confidence else "")
            )[:200],
            "suggested_at": now,
            "created_at": now,
        },
        key=["id"],
    )
    return True


async def propose_anchors(
    ctx: RunContext,
    *,
    limit: int = 60,
    batch: int = 25,
    use_model: bool = True,
    budget=None,
) -> AnchorReport:
    """Find unattached entities and file proposals for a human to approve."""
    conn = ctx.db
    report = AnchorReport()

    isolated = isolated_entities(conn)
    report.isolated = len(isolated)
    if not isolated:
        return report

    anchors = anchor_entities(conn)
    anchor_ids = {r["id"] for r in anchors}
    if not anchors:
        report.warnings.append(
            "There are no starting names to attach these to, so none could be proposed."
        )
        return report

    names = {r["id"]: r["name"] for r in repo.select_entities_3(conn)}
    decided = _existing_pairs(conn)
    now = ctx.clock.now()
    seen: set[frozenset[str]] = set()

    def take(proposals: list[Proposal]) -> int:
        written = 0
        for proposal in proposals:
            pair = frozenset((proposal.child_id, proposal.anchor_id))
            if pair in decided or pair in seen or len(pair) < 2:
                continue
            if file_proposal(conn, proposal, names=names, now=now):
                seen.add(pair)
                written += 1
        return written

    # Only an entity that HOLDS an assertion can be lifted through one; for the
    # rest there is nothing on disk to lift.
    linked = [row for row in isolated if row["has_assertions"]]
    report.from_transitive = take(transitive_proposals(conn, linked, anchor_ids, names=names))

    if use_model:
        remaining = [row for row in isolated if not any(row["id"] in pair for pair in seen)][:limit]
        report.from_model = take(
            await _model_proposals(ctx, remaining, anchors, batch=batch, budget=budget)
        )

    report.proposed = report.from_transitive + report.from_model
    for warning in report.warnings:
        ctx.warn(warning)
    return report


async def _model_proposals(
    ctx: RunContext,
    isolated: list[db.Row],
    anchors: list[db.Row],
    *,
    batch: int,
    budget=None,
) -> list[Proposal]:
    """Ask which anchor each entity belongs under. Names in, names out."""
    from venona.llm import budget as budget_mod
    from venona.llm.prompts import anchor_system
    from venona.llm.runner import call_and_record
    from venona.llm.schemas import anchor_schema

    if not isolated:
        return []

    anchor_names = [r["name"] for r in anchors]
    by_anchor_name = {r["name"]: r["id"] for r in anchors}
    proposals: list[Proposal] = []

    for start in range(0, len(isolated), batch):
        chunk = isolated[start : start + batch]
        ctx.check_cancel()

        if budget is not None:
            try:
                budget.check()
            except budget_mod.BudgetExceeded:
                # `deferral`, not a fifth hand-written sentence about the cap.
                ctx.warn(budget.deferral(len(isolated) - start, "names left unproposed"))
                break

        # Same one-name-one-id rule as the anchors: a duplicate surface in the
        # enum is rejected outright by `--json-schema`, and an ambiguous one
        # would map a verdict onto the wrong entity.
        by_child_name: dict[str, str] = {}
        for r in chunk:
            by_child_name.setdefault(r["name"], r["id"])
        lines = [f"- {name}" for name in by_child_name]
        anchor_lines = [f"- {r['name']} ({r['type']})" for r in anchors]

        result = await call_and_record(
            ctx,
            "anchor",
            user_content=(
                "ANCHORS (choose from these, by name, exactly):\n"
                + "\n".join(anchor_lines)
                + "\n\nENTITIES to attach:\n"
                + "\n".join(lines)
            ),
            schema=anchor_schema(list(by_child_name), anchor_names),
            system_prompt=anchor_system(),
            model=ctx.settings.llm.adjudicate_model,
            effort="low",
            budget=budget,
        )
        if not result.ok:
            ctx.warn(f"anchor proposal failed: {result.error}")
            continue

        for verdict in (result.data or {}).get("attachments", []):
            anchor_id = by_anchor_name.get(verdict.get("anchor") or "")
            child_id = by_child_name.get(verdict.get("entity") or "")
            if anchor_id is None or child_id is None:
                continue  # named neither side, or answered "none"
            proposals.append(
                Proposal(
                    child_id=child_id,
                    anchor_id=anchor_id,
                    predicate=verdict.get("relation") or "PART_OF",
                    why=(verdict.get("why") or "")[:200],
                    confidence=verdict.get("confidence") or "",
                )
            )

    return proposals
