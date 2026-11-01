"""Entity merges: a row in `entity_mappings`, never an edit to an assertion (C1).

Two properties make a merge safe here:

* **Append-only.** A merge is a row, not an edit. `assertions` are untouched, so
  invariant 1 holds and `venona graph rebuild` still reconstructs everything.
* **Reversible.** Unmerge sets `active = 0` on the mapping. Nothing is lost,
  because nothing was overwritten.

Every read path resolves through that table -- `graph/projector.py`,
`services/entity_service.py`, `services/alias_index.py`. One that does not turns
a merge into a regression: visible in Neo4j, invisible in every hover card, and
the loser's aliases gone from annotation.
"""

from __future__ import annotations

from venona.db import core as db
from venona.db.models import mint_id, normalize
from venona.db.repositories import curate as repo
from venona.graph.projector import resolve_mappings
from venona.ontology import get_ontology

MERGED = "merged"

# How an `alias_conflicts` row was settled.
RESOLUTION_MERGED = "merged"
RESOLUTION_KEPT_APART = "kept_apart"


class MergeError(ValueError):
    """A merge that would corrupt rather than clean up."""


def resolve_entity_id(conn: db.Conn, entity_id: str) -> str:
    """Follow merge chains to the surviving id.

    Reuses the projector's traversal rather than reimplementing it -- it is
    already fixed-point and cycle-guarded, and two chain-collapsers that
    disagreed would be worse than none.
    """
    return resolve_mappings(conn).get(entity_id, entity_id)


def resolved_pair(conn: db.Conn, row) -> tuple[str, str]:
    """A conflict's two sides, as the entities they now ARE.

    An `alias_conflicts` row records the ids that were live when it was filed,
    and nothing rewrites them when one side is later merged away. Everything
    that reads a conflict therefore has to ask this rather than trust the
    columns -- and everything that did not, disagreed with everything that did:
    the queue header joined `entities` on the raw id and showed `Army` while the
    cards resolved it and showed `U.S. Army`, so the curator merged what the
    cards said and the API answered `survivor 'org:army' is merged`, naming an
    id that had never been on screen. ADR 0085.
    """
    mappings = resolve_mappings(conn)
    existing = row["existing_entity_id"]
    proposed = row["proposed_entity_id"]
    return mappings.get(existing, existing), mappings.get(proposed, proposed)


def merge_closure(conn: db.Conn, entity_id: str) -> set[str]:
    """Every raw id that now resolves to `entity_id`'s survivor, itself included.

    The read side's counterpart to `resolve_entity_id`. Assertions keep the ids
    they were written with -- C1 forbids rewriting them -- so anything that
    reads assertions BY entity has to ask for the whole closure. Asking for the
    survivor alone means a merge makes an entity LESS informative than it was
    before: everything the loser contributed simply stops being found.
    """
    mappings = resolve_mappings(conn)
    survivor = mappings.get(entity_id, entity_id)
    return {survivor} | {raw for raw, to in mappings.items() if to == survivor}


def _names(conn: db.Conn, *ids: str) -> dict[str, str]:
    return {r["id"]: r["name"] for r in repo.entity_names(conn, list(ids))}


def _label(names: dict[str, str], entity_id: str) -> str:
    """An entity as a curator can recognise it. The id alone is not enough --
    every id in the refusal ADR 0085 records was one nobody had seen."""
    name = names.get(entity_id)
    return f"{name} ({entity_id})" if name else entity_id


def merge_entities(
    conn: db.Conn,
    *,
    from_id: str,
    to_id: str,
    merge_id: str,
    reason: str,
    now: str,
    actor: str | None = None,
) -> list[str]:
    """Merge `from_id` into `to_id`. Returns the conflict ids it settled.

    `merge_id` and `now` are injected rather than generated: this module lives
    under a `lint_determinism` guard, and offline runs must reproduce exactly.

    `actor` is a `users.id` or None, and it is a plain string on purpose. The
    router knows the principal; this module must not -- a service that can reach a
    `Request` is a service two callers cannot share, and identity does not belong
    in `services/` (contract C7 keeps `RunContext` identity-free for the same
    reason). None is honest: it means the CLI, the pipeline, or a verdict recorded
    before anyone was asked to sign.
    """
    if from_id == to_id:
        raise MergeError("cannot merge an entity into itself")

    # THE LOCK COMES FIRST AND EVERY PRECONDITION IS READ INSIDE IT.
    #
    # A check run outside this transaction is a check made against a mapping
    # table in which a concurrent merge has not committed. `repo.lock_merges`
    # carries the two races that produces; neither raises and neither is visible
    # afterwards -- the graph is simply wrong in a way that reads as a model
    # mistake.
    with db.transaction(conn, what="merge_entities"):
        repo.lock_merges(conn)
        requested = (from_id, to_id)
        names = _names(conn, from_id, to_id)
        if from_id not in names:
            raise MergeError(f"no entity {from_id!r}")
        if to_id not in names:
            raise MergeError(f"no entity {to_id!r}")

        # FOLLOW THE CHAIN, both sides.
        #
        # A merged id is a signpost, not a wall: it has no independent existence,
        # every read path already resolves it, and the card the curator was looking
        # at showed the survivor, so a request naming one can only mean the
        # survivor (ADR 0085).
        #
        # Resolving the LOSER matters as much as the target: merging a tombstone
        # would write a second mapping out of an id that already has one, which is
        # how a chain becomes a fork.
        from_id, to_id = resolve_entity_id(conn, from_id), resolve_entity_id(conn, to_id)

        if from_id == to_id:
            # The commonest way to arrive here is a conflict filed before a merge
            # that has since absorbed one of its sides. Say what happened in the
            # names the curator can see, not in ids.
            raise MergeError(
                f"{_label(names, requested[0])} and {_label(names, requested[1])} are already "
                f"the same entity -- both resolve to {_label(_names(conn, to_id), to_id)}"
            )

        names |= _names(conn, from_id, to_id)
        rows = {r["id"]: r for r in repo.select_entities(conn, from_id, to_id)}
        if from_id not in rows or to_id not in rows:
            raise MergeError(f"no entity {from_id if from_id not in rows else to_id!r}")

        # A PROVISIONAL survivor is fine, and is in fact the common case: measured
        # 2026-08-06 on the live database, 196 of 197 provisionals sat at (1
        # article, 1 source), so most duplicates are two uncorroborated entities.
        # Refusing those would make merging useless exactly where it is most needed
        # -- and `promote_provisionals` aggregates over the merge closure, so
        # consolidating two provisionals is precisely what lets the survivor reach
        # the corroboration gate.
        #
        # A REJECTED survivor is refused: `rejected` is a human verdict that the
        # model hallucinated this entity, and merging into it would quietly
        # reinstate something a person threw away. A tombstone is not that -- it is
        # a signpost, and it is followed above.
        #
        # `merged` is reachable only as data skew: status `merged` with no active
        # mapping to follow, which `unmerge` cannot produce and only a hand edit
        # can. It is still refused, but the error says where the entity went.
        if rows[to_id]["status"] == "rejected":
            raise MergeError(
                f"{_label(names, to_id)} was rejected -- reinstate it before merging into it"
            )
        if rows[to_id]["status"] == MERGED:
            raise MergeError(
                f"{_label(names, to_id)} is marked merged but has no active mapping to follow; "
                f"unmerge or reinstate it first"
            )

        # Would this close a loop? `resolve_mappings` guards traversal against
        # cycles, but a cycle still means two entities each merged into the other,
        # which is not a statement anyone meant to make.
        if resolve_entity_id(conn, to_id) == from_id:
            raise MergeError(f"{to_id!r} already resolves to {from_id!r}; that would be a cycle")

        db.upsert(
            conn,
            "entity_mappings",
            {
                "from_id": from_id,
                "to_id": to_id,
                "merge_id": merge_id,
                "active": 1,
                "reason": reason,
                "created_at": now,
                "merged_by": actor,
            },
            key=["from_id", "merge_id"],
        )
        # `merged` rather than `rejected`: "this is a duplicate" and "the model
        # hallucinated this" are different facts, and conflating them loses the
        # distinction in every later query. It also keeps the loser out of
        # `promote_provisionals`, which would otherwise re-promote it and
        # resurrect a merged entity into the projection.
        repo.update_entities_by_status_last_seen(conn, MERGED, now, from_id)
        # The merge IS the answer to any conflict filed about this pair. Closing
        # it here rather than asking the user to close it twice is the whole
        # reason the queue is worth keeping.
        settled = close_conflicts_for_pair(
            conn, from_id, to_id, resolution=RESOLUTION_MERGED, now=now, actor=actor
        )

    # ── and tell the promotion gate ──────────────────────────────────
    #
    # Consolidating evidence is the POINT of a merge -- `promote_provisionals`
    # aggregates over `entity_mappings`, so two single-article entities become
    # one two-article entity the moment this row lands. The pipeline stages run
    # that gate on the documents they process; a merge from the CLI or the
    # curation API is the other way in, and without this call it consolidates
    # the evidence and tells no one.
    #
    # Measured 2026-08-06 on the live database: 14 entities sat `provisional`
    # while already clearing the gate, including Palantir at 7 articles from 7
    # publishers -- 3.5x over, invisible in the graph, and blocking six
    # relationships. The survivor of a merge is exactly the entity most likely
    # to have just crossed the threshold.
    #
    # After the transaction, so the mapping this merge just wrote is visible to
    # the closure fold.
    #
    # Scoped to the SURVIVOR and promotion-only. A merge changes the evidence of
    # exactly one entity and can only ever add to it, so re-evaluating the whole
    # table here would demote entities this merge said nothing about --
    # a merge that silently demotes is the invisible kind of damage. Dormancy is
    # left off for the same reason: a merge is a statement about identity, never
    # about age.
    _promote_after_merge(conn, survivor=to_id, now=now)
    return settled


def _promote_after_merge(conn: db.Conn, *, survivor: str, now: str) -> list[str]:
    """Re-run the corroboration gate for one entity.

    `pipeline` is imported locally: `services` must not take a module-level
    dependency on it, and `pipeline.resolve` already imports from `services`.
    """
    from venona.pipeline.resolve import promote_provisionals
    from venona.settings import get_settings

    limits = get_settings().pipeline
    return promote_provisionals(
        conn,
        now=now,
        min_sightings=limits.provisional_promote_sightings,
        min_sources=limits.provisional_promote_sources,
        only={survivor},
        promote_only=True,
    )


def close_conflicts_for_pair(
    conn: db.Conn,
    a_id: str,
    b_id: str,
    *,
    resolution: str,
    now: str,
    actor: str | None = None,
) -> list[str]:
    """Settle every open conflict filed about this pair, in either direction.

    Returns the ids it settled, because the CLIENT cannot work them out. One
    real-world pair is routinely filed as several rows -- `prog:orca-xluuv` and
    `prog:orca-...-uncrewed-...-xluuv` were filed twice, once under the
    acronym-generated surface `OELUUV` and once under `OELU2V` -- so one merge
    answers more than the row the curator clicked. Dropping only that row leaves
    the siblings on screen, where clicking one returns "already the same
    entity" -- a decision the curator had in fact already made, offered back to
    them as an error.

    Direction is not meaningful here. The resolver records whichever id it
    happened to see first as `existing`, so the same real-world pair can be
    filed both ways round by different code paths -- `_add_alias` files
    (owner, newcomer) and `_contained` files (candidate, minted).
    """
    # Matched on merge CLOSURES, not on the two ids as given. Callers act on
    # resolved ids, so a merge of (survivor A, survivor B) would not match a row
    # filed under (raw a, raw b), and the conflict that merge just answered would
    # stay open -- the same staleness one layer up. ADR 0085.
    left, right = merge_closure(conn, a_id), merge_closure(conn, b_id)
    settled = [
        row["id"]
        for row in repo.select_alias_conflicts(conn)
        if (row["existing_entity_id"] in left and row["proposed_entity_id"] in right)
        or (row["existing_entity_id"] in right and row["proposed_entity_id"] in left)
    ]
    for conflict_id in settled:
        repo.settle_conflict(conn, 1, resolution, now, conflict_id, actor)
    return settled


def dismiss_conflict(
    conn: db.Conn, conflict_id: str, *, now: str, actor: str | None = None
) -> None:
    """Record that a pair is genuinely two different things.

    The other half of a working queue. Read 2026-08-06 over the live containment
    rows, roughly 40% were correct distinctions the resolver was right to decline
    -- Thales vs Thales Australia, Australian Army vs Australian Army Research
    Centre -- and with no way to say so they sat in the queue forever,
    indistinguishable from rows nobody had looked at.
    """
    cursor = repo.settle_open_conflict(conn, RESOLUTION_KEPT_APART, now, conflict_id, actor)
    if not cursor.rowcount:
        raise MergeError(f"no open conflict {conflict_id!r}")
    conn.commit()


def list_conflicts(
    conn: db.Conn,
    *,
    kind: str | None = None,
    include_resolved: bool = False,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict]:
    """The queue, in the entities the rows now refer to.

    RESOLVED ON READ, not rewritten on merge. The stored ids stay exactly as
    filed, so an unmerge restores the original pair for free and there is no
    data migration to run -- a row heals the moment it is next read. It is also
    what keeps the queue agreeing with the cards beside it, which resolve the
    same way. ADR 0085.
    """
    # The window bounds the SQL read; the moot drop below happens after it, so
    # a bounded page can come back short of `limit` while open rows remain.
    # `None` (every CLI caller) keeps the whole queue.
    rows = [
        dict(r)
        for r in repo.select_conflicts(
            conn, kind=kind, include_resolved=include_resolved, limit=limit, offset=offset
        )
    ]
    if not rows:
        return []

    mappings = resolve_mappings(conn)
    out: list[dict] = []
    for row in rows:
        existing = mappings.get(row["existing_entity_id"], row["existing_entity_id"])
        proposed = mappings.get(row["proposed_entity_id"], row["proposed_entity_id"])
        # Both sides are now one entity, so there is nothing left to decide.
        # Dropped from the OPEN queue here and closed for good by
        # `close_moot_conflicts` on the next curate pass, which is what puts it
        # in the history with a reason rather than silently vanishing.
        if existing == proposed and not include_resolved:
            continue
        row["existing_entity_id"] = existing
        row["proposed_entity_id"] = proposed
        out.append(row)

    # The SQL hides a dormant STORED side; a side merged into a survivor that
    # has since drained is only visible once resolved.
    if not include_resolved:
        drained = repo.dormant_among(
            conn,
            sorted({r["existing_entity_id"] for r in out} | {r["proposed_entity_id"] for r in out}),
        )
        out = [
            r
            for r in out
            if r["existing_entity_id"] not in drained and r["proposed_entity_id"] not in drained
        ]
    if not out:
        return []

    names = _names(
        conn, *{r["existing_entity_id"] for r in out} | {r["proposed_entity_id"] for r in out}
    )
    for row in out:
        row["existing_name"] = names.get(row["existing_entity_id"])
        row["proposed_name"] = names.get(row["proposed_entity_id"])
    return out


def open_conflict_counts(conn: db.Conn) -> dict[str, int]:
    """Open rows per kind, counted by the same rule that lists them.

    A count taken from the table directly includes moot rows and rows with a
    drained side, and the badge then promises questions the queue cannot show.
    """
    counts: dict[str, int] = {}
    for row in list_conflicts(conn):
        if row["kind"]:
            counts[row["kind"]] = counts.get(row["kind"], 0) + 1
    return counts


def close_moot_conflicts(conn: db.Conn, *, now: str) -> int:
    """Settle conflicts whose two sides have become the same entity.

    A conflict outlives the entities it names: merge one side into the other's
    survivor and the question answers itself, but the row stays open and keeps
    being counted. Closing it as `merged` puts it in the decision history with
    a reason instead of leaving it to be filtered out of every read forever.
    """
    mappings = resolve_mappings(conn)
    moot = [
        row["id"]
        for row in repo.select_alias_conflicts_2(conn)
        if mappings.get(row["existing_entity_id"], row["existing_entity_id"])
        == mappings.get(row["proposed_entity_id"], row["proposed_entity_id"])
    ]
    for conflict_id in moot:
        repo.update_alias_conflicts_by_resolution_resolved_at_2(
            conn, RESOLUTION_MERGED, now, conflict_id
        )
    return len(moot)


def unmerge(
    conn: db.Conn, *, from_id: str, merge_id: str, now: str, actor: str | None = None
) -> None:
    """Reverse a merge. The mapping row stays, deactivated, as a record.

    ONE TRANSACTION for all three statements. `db.connect` opens every
    connection in autocommit, so without the block each is its own implicit
    transaction and a failure between them leaves the mapping deactivated with
    the entity still `merged`, or the entity restored with the conflicts it
    closed still closed. Undo is the recovery path for a misclick in a fast
    keyboard queue, so a half-applied undo is exactly the state nobody can
    reason about.
    """
    with db.transaction(conn, what="curate.unmerge"):
        cursor = repo.update_entity_mappings_by_from_id_merge_id(conn, from_id, merge_id)
        if not cursor.rowcount:
            raise MergeError(f"no active merge {merge_id!r} for {from_id!r}")

        repo.update_entities_by_last_seen_id(conn, now, from_id, MERGED)
        # Reopen what the merge closed. An unmerge says the pair was NOT one
        # thing, which puts the original question back on the table -- leaving
        # the row closed would hide a conflict nobody has actually adjudicated.
        # Only `merged` closures reopen; a `kept_apart` verdict was never about
        # this merge and stands on its own.
        repo.update_alias_conflicts_by_resolution_existing_entity_id(
            conn, RESOLUTION_MERGED, from_id, from_id
        )


def list_merges(
    conn: db.Conn, *, active_only: bool = True, limit: int | None = None, offset: int = 0
) -> list[dict]:
    return [
        dict(r)
        for r in repo.select_merges(conn, active_only=active_only, limit=limit, offset=offset)
    ]


def count_merges(conn: db.Conn, *, active_only: bool = True) -> int:
    return repo.count_merges(conn, active_only=active_only)


def correct_entity_type(
    conn: db.Conn,
    conflict_id: str,
    *,
    entity_type: str,
    merge_ids: tuple[str, str],
    now: str,
    actor: str | None = None,
) -> dict:
    """Settle a type conflict by naming the type the thing actually is.

    The verdict offered where Merge is withheld (ADR 0208). Both sides fold into
    ONE entity of `entity_type`, and every id naming either of them still
    resolves to it. Returns the survivor and the conflicts settled on the way.

    WHY THIS MINTS RATHER THAN RETYPING IN PLACE. An id is type-prefixed and
    immutable (C4): `research:us-navy` says ResearchOrg in its own name, and the
    entity hues are derived from that prefix at render time. Rewriting
    `entities.type` alone would leave an entity whose id, colour and type
    disagree, and nothing would ever say so. So the correction is expressed the
    way every other identity change here is expressed -- as a merge, into an
    entity whose prefix already means what the curator said.

    When one side is ALREADY the chosen type there is nothing to mint and this
    is a plain merge; that is the common case. The mint exists for the case
    where the right answer is a third type neither side holds.
    """
    row = repo.select_conflict_sides(conn, conflict_id)
    if row is None:
        raise MergeError(f"no conflict {conflict_id!r}")
    if row["resolved"]:
        raise MergeError("that pair has already been settled")
    if entity_type not in get_ontology().type_names:
        raise MergeError(f"{entity_type!r} is not one of this instance's kinds")

    left, right = resolved_pair(conn, row)
    if left == right:
        raise MergeError("both sides are already the same entity")

    # The fuller name: it is the one a reader recognises, so the minted id is
    # one they would recognise too.
    name = max((row["a_name"], row["b_name"]), key=len)
    survivor = next(
        (
            side
            for side, side_type in ((left, row["a_type"]), (right, row["b_type"]))
            if side_type == entity_type
        ),
        None,
    )
    minted = False
    if survivor is None:
        survivor = mint_id(entity_type, name)
        existing = repo.select_entity(conn, survivor)
        if existing is None:
            repo.insert_retyped_entity(
                conn,
                {
                    "id": survivor,
                    "type": entity_type,
                    "name": name,
                    "canonical_key": normalize(name),
                    # `active` rather than provisional: a person has just stated
                    # that this thing exists and what kind it is, which is a
                    # stronger claim than the sighting count the gate measures.
                    "status": "active",
                    "relevance": 0.0,
                    "first_seen": now,
                    "last_seen": now,
                    "created_at": now,
                },
            )
            minted = True
        elif existing["status"] == MERGED:
            raise MergeError(
                f"{survivor} is the kind you chose but has been merged away; unmerge it first"
            )

    settled: list[str] = []
    for index, loser in enumerate(side for side in (left, right) if side != survivor):
        settled += merge_entities(
            conn,
            from_id=loser,
            to_id=survivor,
            merge_id=merge_ids[index],
            reason=f"curated: type is {entity_type}",
            now=now,
            actor=actor,
        )
    return {"survivor_id": survivor, "minted": minted, "settled_conflict_ids": settled}
