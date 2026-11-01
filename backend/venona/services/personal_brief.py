"""One reader's list, and their own brief.

Two surfaces over the same derivation. The LIST is pure Python over stored
axis scores -- every analyzed cluster in the window, re-ranked by the
reader's derived weights, follows grouped on top -- so it renders instantly
and is the fallback whenever the document does not exist yet. The BRIEF is
one light-lane model call over that ranking's top stories, keyed (user,
date, inputs_hash) so unchanged inputs never pay twice.

Model cost is O(readers who actually read), never O(corpus x readers): the
per-cluster analysis is shared, and this module only ever re-rolls it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from venona.api import authz
from venona.api.schemas.brief import Brief
from venona.db import core as db
from venona.db.repositories import pipeline_brief as brief_repo
from venona.db.repositories import relevance as repo
from venona.db.repositories import users as users_repo
from venona.llm.prompts import load_profile, personal_brief_system_prompt
from venona.llm.runner import call_and_record
from venona.llm.schemas import personal_brief_schema, section_names
from venona.pipeline.brief import (
    NOISE_FLOOR,
    RECENT_THREAD_DAYS,
    RECENT_THREAD_LIMIT,
    _cutoff_ms,
)
from venona.pipeline.types import (
    MIN_BRIEFABLE_CHARS,
    RunContext,
    retrieval_window,
)
from venona.services import alias_index, brief_service, run_launcher
from venona.services import personal_relevance as pr
from venona.services.alias_index import build_index
from venona.services.annotate import annotate_blocks, markdown_to_blocks
from venona.settings import Settings
from venona.untrust import inline, quote

log = logging.getLogger("venona.personal_brief")

#: The list is a reading surface, not an archive: enough to fill a quiet
#: morning, small enough that the tail is still worth scrolling.
LIST_CAP = 30

#: A headline, bounded before it becomes a markdown heading in the prompt.
MAX_HEADLINE_CHARS = 300
#: How much of the canonical article the generation call reads, so `summary` can
#: be written from the article rather than from a headline.
MAX_ARTICLE_CHARS = 4000

#: Version of the stored document format, folded into the inputs hash.
#: Nothing else moves the hash when the format changes underneath a current
#: document -- without this, a format change leaves every reader a document
#: that reads current: no stale banner, no "Write it again", and a generate
#: POST that returns `already_current` without generating. Bumping it makes
#: every prior document `stale`, and the next warm pass rewrites each recent
#: reader, spending one of their daily generations.
PERSONAL_BRIEF_FORMAT = 3


def reader_state(conn: db.Conn, *, user_id: str) -> dict[str, Any]:
    """The reader's effective scoring inputs, drift already resolved.

    `emphasis` is what they stored; `effective` is what applies -- empty (the
    instance defaults) when the rubric's axis set changed underneath them,
    with `drifted` saying so.
    """
    row = repo.settings_row(conn, user_id=user_id)
    emphasis: dict[str, str] = db.jload(row["emphasis"], {}) or {} if row else {}
    drifted = bool(emphasis) and pr.is_drifted(emphasis)
    effective = {} if drifted else emphasis
    weights = pr.derive_weights(effective)
    return {
        "tailored": row is not None,
        "drifted": drifted,
        "emphasis": emphasis,
        "effective": effective,
        "weights": weights,
        "cuts": pr.scaled_cuts(weights),
    }


def _inputs_hash(
    conn: db.Conn, *, user_id: str, settings: Settings, effective: dict[str, str]
) -> str:
    follow_ids = sorted(f["entity_id"] for f in repo.follows(conn, user_id=user_id))
    return pr.inputs_hash(
        effective,
        follow_ids,
        f"{load_profile(settings).short_hash}:v{PERSONAL_BRIEF_FORMAT}",
    )


def _ranked_pairs(
    conn: db.Conn, *, user_id: str, state: dict[str, Any], settings: Settings, now_iso: str
) -> list[tuple[dict[str, Any], db.Row]]:
    """The window, scored for this reader, each wire dict paired with its
    cluster row. Follows group first, then the reader's own overall --
    grouping, never a boost into the number. The row carries what the wire
    deliberately strips -- `key_facts`, `flags`, `canonical_doc_id` -- which
    the generation prompt needs and the ForYou payload must not."""
    cutoff, _hours = retrieval_window(conn, now_iso=now_iso, settings=settings)
    rows = repo.window_clusters(conn, min_chars=MIN_BRIEFABLE_CHARS, cutoff=cutoff)

    follows_by_cluster: dict[str, list[str]] = {}
    matches = repo.followed_in_clusters(conn, user_id=user_id, cluster_ids=[r["id"] for r in rows])
    for match in matches:
        name = match["name"] or match["entity_id"]
        follows_by_cluster.setdefault(match["cluster_id"], []).append(name)

    pairs: list[tuple[dict[str, Any], db.Row]] = []
    for row in rows:
        overall, tier = pr.personal_score(row["axes"], weights=state["weights"], cuts=state["cuts"])
        follows = sorted(follows_by_cluster.get(row["id"], []))
        # The shared brief's noise floor, applied to the PERSONAL score -- but
        # a followed mention always surfaces: the reader asked for that name
        # by hand, and hiding it because the axes scored low is exactly the
        # "black box overrode me" feeling follows exist to remove.
        if overall < NOISE_FLOOR and not follows:
            continue
        pairs.append(
            (
                {
                    "cluster_id": row["id"],
                    "headline": row["doc_title"] or row["title"] or "",
                    "url": row["url"],
                    "publisher": row["publisher"],
                    "published_at": row["doc_published"] or row["published_at"],
                    "overall": overall,
                    "tier": tier,
                    "why": row["relevance_note"],
                    "so_what": row["so_what"],
                    "follows": follows,
                },
                row,
            )
        )
    pairs.sort(key=lambda p: (not p[0]["follows"], -p[0]["overall"], p[0]["cluster_id"]))
    return pairs[:LIST_CAP]


def _ranked_items(
    conn: db.Conn, *, user_id: str, state: dict[str, Any], settings: Settings, now_iso: str
) -> list[dict[str, Any]]:
    return [
        item
        for item, _ in _ranked_pairs(
            conn, user_id=user_id, state=state, settings=settings, now_iso=now_iso
        )
    ]


def for_you(
    conn: db.Conn,
    *,
    user_id: str,
    settings: Settings,
    now_iso: str,
    registry: Any | None = None,
) -> dict[str, Any]:
    """The whole personal payload: list, document (when one exists), and the
    currency/cap facts the UI decides with. Zero model calls.

    `now_iso` is required rather than defaulted, because a default would read
    the wall clock inside a determinism-guarded package -- and the staleness
    the UI decides with is measured against it.

    `registry` answers `generating`. Optional because every offline caller
    constructs this without a running API; absent, `generating` is False, which
    is the same answer a reader gets after a restart and is safe in the
    direction that matters -- it never claims a run is in flight when none is.
    """
    now = now_iso
    date = now[:10]
    state = reader_state(conn, user_id=user_id)
    items = _ranked_items(conn, user_id=user_id, state=state, settings=settings, now_iso=now)

    current_hash = _inputs_hash(
        conn, user_id=user_id, settings=settings, effective=state["effective"]
    )
    row = repo.latest_personal_brief(conn, user_id=user_id)
    brief = render_brief(conn, row) if row else None
    stale = bool(row) and row["inputs_hash"] != current_hash
    latest_shared = brief_service.latest_brief_generated_at(conn)

    cap = settings.llm.personal_briefs_per_day
    used = repo.count_personal_brief_runs(conn, user_id=user_id, since=date)
    generating = _is_generating(registry, user_id=user_id)
    return {
        "date": date,
        "tailored": state["tailored"],
        "drifted": state["drifted"],
        "items": items,
        "brief": brief,
        "stale": stale,
        "outdated": bool(row) and not _covers_latest_edition(row, latest_shared=latest_shared),
        "generations_left": max(0, cap - used),
        "generating": generating,
        "auto_due": _auto_due(
            conn,
            user_id=user_id,
            state=state,
            items=items,
            brief_row=row,
            latest_shared=latest_shared,
            used=used,
            cap=cap,
            generating=generating,
        ),
    }


def _is_generating(registry: Any | None, *, user_id: str) -> bool:
    """Whether a `personal_brief` run for this reader is in flight.

    THE LAUNCHER'S OWN COALESCING LOOKUP, asked as a question rather than used
    to fold a duplicate. That is the point: "is one running" and "would a POST
    start a new one" are then the same predicate, and cannot drift apart the
    way two implementations of it would.
    """
    if registry is None:
        return False
    return registry.duplicate_of("personal_brief", {"user_id": user_id}) is not None


def _auto_due(
    conn: db.Conn,
    *,
    user_id: str,
    state: dict[str, Any],
    items: list[dict[str, Any]],
    brief_row: db.Row | None,
    latest_shared: str | None,
    used: int,
    cap: int,
    generating: bool,
) -> bool:
    """Whether opening the reading surface should spend one call, unasked.

    THE ONLY PLACE IN THE APP THAT SAYS "SPEND WITHOUT BEING ASKED", so every
    clause is a brake and none is decoration:

      * `tailored` -- an untailored reader has no emphasis to write from, and
        the derived list is already their whole personal surface.
      * `not generating` -- one is going; the launcher would coalesce it, but
        asking twice for the same thing is how a poll becomes a loop.
      * `items` -- nothing in the window to write about; the reader gets the
        honest empty line instead.
      * a shared brief exists -- an instance that has never run has nothing to
        derive from.
      * their document does not already cover the newest shared brief. THE
        UNIT OF CURRENCY IS THE EDITION, NOT THE DAY: a document written at
        00:05 from yesterday's reading is stale the moment the daily lands,
        and one written at 23:50 is still current at 00:10. Comparing the two
        `generated_at` stamps asks that directly and is timezone-free, which a
        date comparison against a schedule in local time is not.
      * no `personal_brief` run since that shared brief. AUTO TRIES AT MOST
        ONCE PER EDITION, EVEN IF IT FAILED. `count_personal_brief_runs`
        counts runs, so a model that returns nothing usable still counts here;
        without this a quietly failing model would spend the reader's whole
        allowance across three page loads and leave them nothing to retry
        with.
      * under the daily cap. The client re-checks whenever new reading lands,
        so an `auto_due` that stayed true past the cap would POST into the
        rate limiter on every check, forever and silently.

    Staleness deliberately does NOT appear. A reader who changes their emphasis
    gets the Rewrite button, not a charge they did not ask for.
    """
    if not state["tailored"] or generating or not items:
        return False
    if latest_shared is None:
        return False
    if _covers_latest_edition(brief_row, latest_shared=latest_shared):
        return False
    if repo.count_personal_brief_runs(conn, user_id=user_id, since=latest_shared):
        return False
    return used < cap


def _covers_latest_edition(brief_row: db.Row | None, *, latest_shared: str | None) -> bool:
    """Whether this reader's document already reflects the newest shared brief.

    One definition of current, because three call sites ask for it and a
    second spelling of it is how the reader's brief came to trail the shared
    one by half a day. A reader with no document is never covered; an instance
    with no shared brief has nothing for theirs to fall behind.
    """
    if brief_row is None:
        return False
    if latest_shared is None:
        return True
    return brief_row["generated_at"] > latest_shared


def render_brief(conn: db.Conn, row: db.Row) -> Brief:
    """A stored personal document, in the shared Brief wire shape.

    Assembly is `brief_service.assemble_body`, the same code path as the
    shared archive, so a document stored before the item format still renders
    through its legacy branch as narrative blocks.

    `unresolved_mentions` is left unset: a per-reader document must not feed
    the shared curation queue."""
    body = brief_service.assemble_body(
        conn,
        title=row["title"] or "",
        item_rows=repo.personal_brief_items_for(conn, brief_id=row["id"]),
        block_rows=repo.personal_brief_blocks(conn, brief_id=row["id"]),
        generated_at=row["generated_at"],
        log_key=row["id"],
    )
    return brief_service.brief_from(conn, row, body)


def _user_content(
    conn: db.Conn,
    pairs: list[tuple[dict[str, Any], db.Row]],
    *,
    user_id: str,
    state: dict[str, Any],
    date: str,
    now: str,
) -> str:
    """Deterministic inputs for the one model call: the reader's emphasis in
    words, their own thread history, and their top stories with the stored
    evidence attached -- key facts, caveats, supporting statements and the
    article text. Nothing here asks the model to re-judge relevance -- the
    order IS the judgement."""
    lines = [f"# This reader's stories for {date}", ""]

    lines.append("## The reader's emphasis")
    if state["effective"]:
        for axis_id in sorted(state["effective"]):
            lines.append(f"- {axis_id}: {state['effective'][axis_id]}")
    else:
        lines.append("- the shared profile's own weighting, unchanged")
    lines.append("")

    followed = sorted({name for item, _ in pairs for name in item["follows"]})
    lines.append("## Names the reader follows")
    # Entity names are model-extracted from scraped prose, so they are corpus
    # text wearing a reader's choice -- the reader picked which to follow, never
    # what they say.
    lines.append(", ".join(inline(n, limit=200) for n in followed) if followed else "none yet")
    lines.append("")

    recent = repo.personal_recent_items(
        conn,
        user_id=user_id,
        cutoff=_cutoff_ms(now, RECENT_THREAD_DAYS * 24),
        exclude_date=date,
        limit=RECENT_THREAD_LIMIT,
    )
    if recent:
        lines += [
            "## Already briefed for you in the last week",
            "",
            "If a story below continues one of these, mark it `follow_up` and "
            "put what is NEW in `state_note`. Do not re-explain the thread.",
            "",
        ]
        lines += [
            f"- {r['date']} {inline(r['headline'], limit=MAX_HEADLINE_CHARS)}" for r in recent
        ]
        lines.append("")

    lines.append("## The stories, in the reader's priority order")
    lines.append("")
    lines.append(
        "Write ONE item per story below and return its `cluster_id` verbatim. "
        "Do not merge two stories, do not invent one, and do not restate the "
        "publisher or date -- those are attached from the record."
    )
    lines.append("")
    # EVERY CORPUS-DERIVED FIELD BELOW IS ADVERSARY-INFLUENCEABLE (ADR 0166).
    # The headline is the sharpest: it becomes a markdown heading, so a newline
    # in one would forge the section structure this prompt is built from.
    for position, (item, row) in enumerate(pairs, start=1):
        lines.append(f"### [{position}] {inline(item['headline'], limit=MAX_HEADLINE_CHARS)}")
        lines.append(f"cluster_id: {item['cluster_id']}")
        meta = " · ".join(
            part
            for part in (
                inline(item["publisher"], limit=200),
                item["published_at"],
                f"score {item['overall']}/100",
            )
            if part
        )
        lines.append(meta)
        if item["follows"]:
            names = ", ".join(inline(name, limit=200) for name in item["follows"])
            lines.append("mentions followed names: " + names)
        if item["why"]:
            lines.append(f"why it scored: {inline(item['why'], limit=600)}")
        if item["so_what"]:
            lines.append(f"so what: {inline(item['so_what'], limit=600)}")

        key_facts = db.jload(row["key_facts"], []) or []
        if key_facts:
            lines.append("key facts:")
            lines += [f"  - {inline(fact, limit=600)}" for fact in key_facts]

        flags = db.jload(row["flags"], {}) or {}
        raised = sorted(name for name, value in flags.items() if value)
        if raised:
            lines.append(f"caveat: {', '.join(raised)} -- attribute, do not assert")

        facts = brief_repo.select_assertions_by_cluster_id(conn, item["cluster_id"]).fetchall()
        if facts:
            lines.append("supporting statements:")
            lines += [f"  - {inline(f['evidence_quote'], limit=280)}" for f in facts]

        doc = brief_repo.select_documents_by_id(conn, row["canonical_doc_id"]).fetchone()
        if doc:
            body = quote(
                doc["text_nfc"], kind="article-body", limit=MAX_ARTICLE_CHARS, keep_lines=True
            )
            lines.append(f"article text:\n{body}")
        lines.append("")

    entity_ids = [
        r["id"]
        for r in brief_repo.active_entities_in_clusters(
            conn, [item["cluster_id"] for item, _ in pairs]
        )
    ]
    if entity_ids:
        lines += ["## Entities you may refer to", ""]
        for entity in brief_repo.entities_for_prompt(conn, entity_ids):
            lines.append(f"- {entity['id']} — {entity['name']} ({entity['type']})")
        lines += [
            "",
            "Use these EXACT names in your prose; the annotator matches literal",
            "text, so a paraphrased name will not link.",
        ]
    return "\n".join(lines)


def _persist_personal(
    conn: db.Conn,
    ctx: RunContext,
    *,
    run_id: str | None,
    user_id: str,
    date: str,
    inputs_hash: str,
    data: dict[str, Any],
    supplied_ids: list[str],
    source_ids: list[str],
) -> str | None:
    """Replace the reader's brief for this date, all of it or none of it.

    THIS PATH DELETES BEFORE IT WRITES, so a failure partway through is
    destructive rather than merely incomplete: the reader's existing brief is
    gone and the replacement is half there. Validation guards a refused or
    hallucinated response; only the transaction guards a process that dies
    mid-write.
    """
    with db.transaction(conn, what="personal_brief_persist"):
        return _write_personal_brief(
            conn,
            ctx,
            run_id=run_id,
            user_id=user_id,
            date=date,
            inputs_hash=inputs_hash,
            data=data,
            supplied_ids=supplied_ids,
            source_ids=source_ids,
        )


def _top_lines(ranked_items: list[dict[str, Any]]) -> dict[str, str]:
    """The TOP block: exactly min(3, n) lines, keyed by cluster.

    The model's own `top_line` choices first, the leading headlines as
    fallback -- the shared brief's cap and, more importantly, its floor, for
    the reasons in ADR 0120.

    Takes the items in the READER'S RANK ORDER, which is why it is a separate
    function -- the fallback walk means "the highest-ranked stories the model
    did not nominate", and the document is later regrouped by desk.
    """
    wanted = min(3, len(ranked_items))
    lines: dict[str, str] = {}
    for item in ranked_items:
        if len(lines) >= wanted:
            break
        line = (item.get("top_line") or "").strip()
        if line:
            lines[item["cluster_id"]] = line
    for item in ranked_items:
        if len(lines) >= wanted:
            break
        headline = (item.get("headline") or "").strip()
        if item["cluster_id"] not in lines and headline:
            lines[item["cluster_id"]] = headline
    return lines


def _write_personal_brief(
    conn: db.Conn,
    ctx: RunContext,
    *,
    run_id: str | None,
    user_id: str,
    date: str,
    inputs_hash: str,
    data: dict[str, Any],
    supplied_ids: list[str],
    source_ids: list[str],
) -> str | None:
    """Validate and store one reader's item document, mirroring
    `pipeline.brief._persist`'s discipline at the reader's scale.

    Returns None WITHOUT touching the stored document when the model returned
    no usable items: validation precedes the delete, so a refused or
    hallucinated response cannot destroy the reader's existing brief.
    Module-level rather than inside `work` so the offline suite can drive it
    with a RunContext directly, the way `test_brief.py` drives `_persist`.
    """
    # Only supplied clusters may become items -- an invented id would produce
    # an item with no evidence and no attribution. The model's own ordering is
    # not consulted: it supplies the desk, code supplies the arrangement.
    known = set(supplied_ids)
    order = {cluster_id: i for i, cluster_id in enumerate(supplied_ids)}
    items = [
        item
        for item in (data.get("items") or [])
        if isinstance(item, dict) and item.get("cluster_id") in known
    ]
    items.sort(key=lambda i: order[i["cluster_id"]])
    if not items:
        return None

    # THE TOP BLOCK IS CHOSEN HERE, before the desks reorder anything. It
    # names the reader's three most consequential stories, and after the
    # grouping "first in the document" means "first on the earliest desk" --
    # which is an accident of the profile's desk order, not a judgement.
    top_by_cluster = _top_lines(items)

    # Desks in the profile's declared order, the reader's ranking preserved
    # inside each: a stable sort over a list already in rank order. The shared
    # brief does exactly this (`pipeline/brief.py`), which is what makes the
    # two documents the same shape.
    section_order = {name: i for i, name in enumerate(section_names())}
    items.sort(key=lambda i: section_order.get(i.get("section", ""), 99))

    stamp = ctx.clock.now()
    brief_id = f"pbrief:{date}:{ctx.ids.uuid7()}"
    repo.delete_personal_brief(conn, user_id=user_id, date=date)
    db.insert(
        conn,
        "personal_briefs",
        {
            "id": brief_id,
            "user_id": user_id,
            "date": date,
            "run_id": run_id,
            "inputs_hash": inputs_hash,
            "title": (data.get("headline") or "").strip() or f"Your brief for {date}",
            "dek": (data.get("dek") or "").strip() or None,
            "generated_at": stamp,
            # Every article the ranking read, not the handful the brief argued
            # about -- the shared brief's meaning of the word, so the Sources
            # footer credits the same breadth on both documents.
            "source_ids": db.jdump(source_ids),
            "created_at": stamp,
        },
    )

    # Ranked by where each of the three lands in the finished document, which
    # is how the rail reads them back -- it is a contents list, so it walks the
    # page rather than re-ranking it. WHICH three was settled before the desks
    # moved anything.
    top_ranks = {
        cluster_id: rank
        for rank, cluster_id in enumerate(
            (item["cluster_id"] for item in items if item["cluster_id"] in top_by_cluster),
            start=1,
        )
    }

    index = build_index(conn)
    cluster_ids = [item["cluster_id"] for item in items]
    # Both halves, as in the shared brief: the entities the model was offered,
    # plus the single-source ones it was not. A reader's own brief has the same
    # claim on a hover as the shared one, and the same right to be told which
    # names rest on one publisher.
    tier_a = frozenset(
        r["id"] for r in brief_repo.active_entities_in_clusters(conn, cluster_ids)
    ) | frozenset(r["id"] for r in brief_repo.provisional_entities_in_clusters(conn, cluster_ids))
    senses = alias_index.senses(conn)
    # Context for ambiguous acronyms is the whole document, as in the shared
    # brief: the spelled-out first use may be paragraphs from the bare one.
    context = "\n".join(
        part
        for item in items
        for part in (
            (item.get("headline") or ""),
            (item.get("summary") or ""),
            (item.get("why_it_matters") or ""),
            (item.get("top_line") or ""),
        )
        if part
    )
    # And the same unit for generated acronyms, which may only match where this
    # document names the entity some other way.
    present = alias_index.refs_present(index, context, tier_a=tier_a)

    ordinal = 0
    for position, item in enumerate(items, start=1):
        item_id = f"{brief_id}:i{position:02d}"
        db.insert(
            conn,
            "personal_brief_items",
            {
                "id": item_id,
                "brief_id": brief_id,
                "cluster_id": item.get("cluster_id"),
                "section": item.get("section"),
                "headline": (item.get("headline") or "").strip(),
                "state": item.get("state") or "new",
                "state_note": (item.get("state_note") or "").strip() or None,
                "top_rank": top_ranks.get(item["cluster_id"]),
                "top_line": top_by_cluster.get(item["cluster_id"]),
                "ord": position,
                "created_at": stamp,
            },
        )
        # Both halves are annotated identically, so acronym definitions and
        # entity hover cards work in the judgement exactly as in the facts.
        for role in ("summary", "why_it_matters"):
            text = (item.get(role) or "").strip()
            if not text:
                continue
            blocks = annotate_blocks(
                markdown_to_blocks(text, section=position, heading=None),
                index,
                tier_a=tier_a,
                senses=senses,
                context=context,
                present_refs=present,
            )
            for block in blocks:
                ordinal += 1
                block.ordinal = ordinal
                block.id = f"b{ordinal}"
                db.insert(
                    conn,
                    "personal_brief_blocks",
                    {
                        "id": f"{brief_id}:{block.id}",
                        "brief_id": brief_id,
                        "section": position,
                        "heading": None,
                        "ordinal": ordinal,
                        "type": block.type,
                        "depth": block.depth,
                        "text": block.text,
                        "spans": db.jdump(block.spans),
                        "marks": db.jdump([m.as_dict() for m in block.marks]),
                        "item_id": item_id,
                        "role": role,
                    },
                )
    return brief_id


async def start_generation(
    request: Any, *, settings: Settings, user_id: str, announce: bool = False
) -> dict[str, Any]:
    """Launch one reader's brief run. `request` may be a real Request or the
    scheduler's `SimpleNamespace(app=...)` -- the same duality `run_daily`
    already exercises. The per-reader daily cap is the ROUTE's check; the
    warm pass is bounded by its own population instead.

    `announce` says whether to tell the reader it was written, and it defaults
    to OFF because two of the three ways in run while the reader is looking at
    the page: the "write it again" button, and the auto path the client fires
    on arrival, which renders the document underneath them. A message about
    either lands while they are already reading the thing it announces, which
    reads as noise and teaches them to ignore the next one. The warm pass is
    the case where nobody is watching, and the only caller passing True.
    """
    profile = load_profile(settings)

    async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
        conn = target.conn
        ctx = target.ctx
        now = ctx.clock.now()
        date = now[:10]

        state = reader_state(conn, user_id=user_id)
        current_hash = _inputs_hash(
            conn, user_id=user_id, settings=settings, effective=state["effective"]
        )
        existing = repo.latest_personal_brief(conn, user_id=user_id)
        latest_shared = brief_service.latest_brief_generated_at(conn)
        if (
            existing is not None
            and existing["inputs_hash"] == current_hash
            and _covers_latest_edition(existing, latest_shared=latest_shared)
        ):
            # The warm pass and a lazy visit race here by design; whoever is
            # second finds this edition already written for these exact inputs
            # and spends nothing.
            #
            # BOTH conditions, not either. The edition alone would let a
            # `PERSONAL_BRIEF_FORMAT` bump pass unwritten, which is the one
            # thing the warm pass is there to carry; the inputs alone are what
            # held the reader's brief at yesterday's reading all day.
            return run_launcher.LaunchOutcome(
                # Nothing to do, nothing degraded, and nothing spent.
                outcome="nothing_to_do",
                data={"user_id": user_id, "already_current": True},
            )

        # The window this reader was actually shown, and the argued top of it.
        # The brief writes about `brief_top_clusters` stories -- the shared
        # brief's cap, read from the same setting so the two cannot drift --
        # while the Sources footer credits every article the ranking read.
        ranked = _ranked_pairs(conn, user_id=user_id, state=state, settings=settings, now_iso=now)
        pairs = ranked[: settings.pipeline.brief_top_clusters]
        if not pairs:
            return run_launcher.LaunchOutcome(
                # A quiet window is a correct answer, not a broken run.
                outcome="nothing_to_do",
                data={"user_id": user_id, "error": "nothing in the window to write about"},
            )

        result = await call_and_record(
            ctx,
            "personal_brief",
            user_content=_user_content(
                conn, pairs, user_id=user_id, state=state, date=date, now=now
            ),
            schema=personal_brief_schema(),
            system_prompt=personal_brief_system_prompt(profile),
            model=settings.llm.personal_brief_model,
            effort=settings.llm.personal_brief_effort,
            timeout=600,
        )
        brief_id = _persist_personal(
            conn,
            ctx,
            run_id=target.run_id,
            user_id=user_id,
            date=date,
            inputs_hash=current_hash,
            data=(result.data or {}) if result.ok else {},
            supplied_ids=[item["cluster_id"] for item, _ in pairs],
            source_ids=[row["canonical_doc_id"] for _, row in ranked if row["canonical_doc_id"]],
        )
        if brief_id is None:
            # Nothing usable was written -- and the stored document was not
            # touched, so the reader keeps whatever they had. The list is the
            # reader's fallback surface, not this run's product.
            reason = result.error or "the model returned no usable items"
            return run_launcher.LaunchOutcome(
                # Genuinely `failed`, and an explicit override of the status
                # `ctx.degraded` would derive (ADR 0183): the run had a window
                # to write about, made the call, and could not produce the
                # artifact. No outcome -- it never reached an answer.
                status="failed",
                # Both: `data` is what a client watching the stream sees, and
                # `error` is what the run row keeps for whoever reads it later.
                data={"user_id": user_id, "error": reason},
                error=reason,
            )
        if announce:
            # After the document is persisted, and inside the run's own
            # connection: a notice about a brief that failed to write would be
            # worse than none, and `raise_notice` swallows its own failures so
            # this cannot turn a delivered brief into a failed run.
            from venona import notify

            notify.announce_personal_brief(
                conn, user_id=user_id, brief_id=brief_id, reader_started=False
            )
        return run_launcher.LaunchOutcome(
            outcome="delivered",
            data={"user_id": user_id, "brief_id": brief_id, "stories": len(pairs)},
        )

    return await run_launcher.launch(
        request,
        kind="personal_brief",
        # The reader is the coalescing key: their double-click folds, two
        # different readers run side by side on the light lane.
        params={"user_id": user_id},
        work=work,
        stage="personal_brief",
        profile_hash=profile.short_hash,
    )


async def warm_recent_readers(
    app: Any, *, settings: Settings, now_iso: str, scheduled: bool
) -> None:
    """Pre-generate for readers seen in the last `personal_brief_warm_days`.

    Runs after a shared brief lands, through the same launch path as a lazy
    visit: light-lane concurrency paces the fan-out, and the already-current
    check inside `work` makes the whole pass idempotent against lazy
    generations. Idle accounts never appear, so they cost nothing.

    `scheduled` says whether the brief that just landed was the daily. It is
    the caller's fact rather than something inferred here, because the two
    callers are the two answers and neither has to guess.
    """
    fanout = settings.llm.personal_brief_fanout
    if fanout == "off" or (fanout == "scheduled_only" and not scheduled):
        return
    days = settings.llm.personal_brief_warm_days
    if days <= 0:
        return
    now = datetime.strptime(now_iso, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    since = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    conn = db.connect(settings.database_url)
    try:
        # `entitled_only` follows the route gate rather than being
        # unconditional: an instance that takes no money has every account
        # reading `none`, and filtering on it there would silently stop writing
        # the product. `now_iso` is the local brake on a stalled entitlement
        # push -- `users_repo.recently_seen` carries the reason.
        readers = users_repo.recently_seen(
            conn, since=since, now=now_iso, entitled_only=authz.billing_enforced()
        )
    finally:
        conn.close()

    fake_request = SimpleNamespace(app=app)
    for row in readers:
        try:
            await start_generation(
                fake_request, settings=settings, user_id=row["id"], announce=True
            )
        except Exception:  # noqa: BLE001 - one reader's failure must not end the pass
            log.warning("personal brief warm failed for %s", row["id"], exc_info=True)
