"""Domain setup: research a described domain and draft its entire configuration.

The `domain_setup` run (research lane, C11). Five stages, each a set of
one-shot model calls through `call_and_record` -- the long-horizon "research
run" is OUR orchestration of bounded calls, exactly like the pipeline, so
budget, retries, fixtures and degradation all live where they already live.

Nothing here writes a live document. Every product is a DRAFT
(`draft_service`, docs/adr/0140) an admin reviews in the config editor, plus
the dossier -- the survey's merged output, persisted beside the drafts so a
single-document re-run costs one call, not a fresh survey.

Determinism: `ctx.clock/ids/rng/http` only, dossier serialized with sorted
keys, and the survey's dateline is omitted offline (the discovery precedent)
so replayed fixtures keep their keys.
"""

from __future__ import annotations

import json
from typing import Any

import yaml

from venona.db.models import slugify
from venona.domain import get_domain
from venona.llm import runner
from venona.llm.budget import Budget, BudgetExceeded
from venona.llm.executor import LlmError
from venona.llm.setup_prompts import (
    SURVEY_FACETS,
    anchors_system,
    document_system,
    document_user,
    ontology_system,
    sources_system,
    survey_system,
    survey_user,
)
from venona.llm.setup_schemas import (
    setup_anchors_schema,
    setup_document_schema,
    setup_sources_schema,
    setup_survey_schema,
)
from venona.ontology import get_ontology, parse_ontology
from venona.services import config_service, draft_service
from venona.services.run_scope import StageReport
from venona.settings import Settings

STAGES: tuple[str, ...] = ("survey", "sources", "ontology", "profile", "anchors")

#: EDITABLE keys the profile stage drafts, with their task names.
PROFILE_DOCS: tuple[tuple[str, str], ...] = (
    ("domain", "setup_domain"),
    ("relevance_profile", "setup_relevance"),
    ("research_prompt", "setup_research_prompt"),
    ("brief_prompt", "setup_brief_prompt"),
    ("scoring_rubric", "setup_rubric"),
    ("brief_sections", "setup_sections"),
    ("lexicon", "setup_lexicon"),
)

#: Every draftable product, in generation order. `params.docs` scopes to a
#: subset; absent means all of them.
ALL_DOCS: tuple[str, ...] = (
    "sources",
    "ontology",
    *[key for key, _ in PROFILE_DOCS],
    "anchors",
)

DOSSIER_FILE = "setup_dossier.json"

#: The slug a freshly provisioned instance ships with; `is_configured` treats
#: it as absent. `make new-local-instance` writes it.
PLACEHOLDER_SLUG = "unconfigured"

#: The anchor expansion categories -- one no-tools batch call each.
EXPAND_CATEGORIES: tuple[str, ...] = (
    "the organizations that build and sell",
    "the bodies that buy, adopt, regulate or oversee",
    "the named products, programmes, platforms or systems",
    "the research organisations, publications and communities",
    "the load-bearing named people",
)


def is_configured(settings: Settings) -> bool:
    """Whether this instance has a real domain descriptor.

    The checked-in defense file is configured by construction; only a
    deployment provisioned with the placeholder (or no file at all, which
    falls back to the defense defaults) gates into `/setup`.
    """
    path = settings.config_dir / "domain.yaml"
    if not path.exists():
        return True  # descriptor defaults apply; nothing to set up
    try:
        raw = yaml.safe_load(path.read_text())
        return bool(raw) and raw.get("slug") != PLACEHOLDER_SLUG
    except Exception:  # noqa: BLE001 -- an unreadable file is the editor's problem, not a gate
        return True


def _dossier_path(settings: Settings):
    return settings.profile_dir / draft_service.DRAFTS_DIRNAME / DOSSIER_FILE


def load_dossier(settings: Settings) -> dict[str, Any] | None:
    path = _dossier_path(settings)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def save_dossier(settings: Settings, dossier: dict[str, Any]) -> None:
    draft_service._write_atomic(
        _dossier_path(settings), json.dumps(dossier, sort_keys=True, ensure_ascii=False, indent=1)
    )


#: Fragment placeholders for a freshly described domain -- domain-neutral
#: versions of the seven slots, good enough to run the generation with; the
#: run's own `domain` draft replaces them with worked examples from the
#: domain itself.
NEUTRAL_FRAGMENTS: dict[str, str] = {
    "analyst_surface_rule": (
        "3. Return surfaces exactly as written in the article -- short forms, full\n"
        "   names, acronyms. Do not normalise, expand, or canonicalise them, and never\n"
        "   invent an identifier. Something else maps surfaces to identity."
    ),
    "acronym_search_examples": (
        "Do not anchor on the surrounding usage. An acronym cited as part of a reference\n"
        "names the WORK being cited, not the section -- the page to find defines the\n"
        "work itself.\n"
        "\n"
        "Prefer official pages, standards bodies and reference works. Avoid news\n"
        "aggregators and acronym-farm sites that list dozens of unsourced expansions;\n"
        "an encyclopedia entry or an official glossary is not one of those and is often\n"
        "the only page that states the expansion in a sentence."
    ),
    "acronym_read_examples": (
        "The sentence must contain BOTH the acronym and the expansion. A heading that is\n"
        "merely the expanded name is not a definition: the expansion on its own does not\n"
        "say the acronym means it, and is rejected. Look for the sentence that connects\n"
        'the two -- "..., commonly called X, ...", "(X)", "abbreviated X".'
    ),
    "adjudicate_verdicts": (
        "  merge      One organisation or programme under two names. Includes a short\n"
        "             form and its full legal name.\n"
        "  part_of    The first names a division, subsidiary or unit OF the second.\n"
        "             Say which side is the CHILD.\n"
        "  distinct   Neither. Two separate bodies that happen to share words."
    ),
    "anchor_relations": (
        "  PART_OF    a division, unit or subsidiary OF the anchor.\n"
        "  BASED_IN   an organisation and its HOME country -- where it is headquartered\n"
        "             or incorporated, or the state whose organ it is.\n"
        "  WORKS_FOR  a person and the organisation they serve."
    ),
    "anchor_directness": (
        "Attach to the MOST DIRECT anchor available, never the most impressive one. A\n"
        "division is PART_OF its organisation, not of the ministry above it and not of\n"
        "the country -- the chain reaches the top on its own, and a shortcut past the\n"
        "middle is a wrong edge that hides the real one."
    ),
    "anchor_knowledge_note": (
        "UNLIKE the other tasks in this system, you SHOULD use what you know about\n"
        "these organisations. Background knowledge is exactly what is being asked for\n"
        "here. It is safe because nothing you return is written to the graph: a curator\n"
        'reviews every line and one keystroke discards it. Be accurate, not bold; "none"\n'
        "costs nothing and a wrong parent costs a correction."
    ),
}


def initial_descriptor(
    *, slug: str, display_name: str, tagline: str, login_blurb: str
) -> dict[str, Any]:
    """The descriptor a fresh domain starts from: phrases derived from the
    name, neutral fragments. The generation run drafts the refined version."""
    return {
        "slug": slug,
        "display_name": display_name,
        "tagline": tagline,
        "login_blurb": login_blurb,
        "press_descriptor": f"{display_name} trade press",
        "news_descriptor": f"{display_name} news",
        "graph_descriptor": f"a {display_name} graph",
        "define_context": f"in the context of {display_name}",
        "search_context": f"in the context of {display_name}",
        "user_agent_note": f"personal {display_name} research reader",
        "mcp_title": f"{display_name} intelligence",
        "corpus_descriptor": f"{display_name} trade press",
        "access_roles": ["analyst", "industry", "investor", "press", "academic", "other"],
        "common_name_words": [],
        "prompt_fragments": dict(NEUTRAL_FRAGMENTS),
    }


async def run_domain_setup(target, settings: Settings, params: dict[str, Any]):
    """The work closure's body. Returns a LaunchOutcome-shaped dict via the
    caller (`api/routers/setup.py`), which owns the run-launcher plumbing."""
    from venona.services.run_launcher import LaunchOutcome

    ctx = target.ctx
    descriptor = get_domain()
    # The generation run's own cap, not the pipeline's: Budget counts against
    # `max_calls_per_run`, so hand it an LlmSettings whose cap IS setup's.
    budget = Budget(
        settings=settings.llm.model_copy(update={"max_calls_per_run": settings.llm.setup_max_calls})
    )
    requested: set[str] = set(params.get("docs") or ALL_DOCS)
    refresh_survey: bool = bool(params.get("refresh_survey", False))

    drafted: list[str] = []
    failed: list[str] = []

    async def begin(stage: str) -> str:
        """Open one wizard step, on the lifecycle every other run uses."""
        return await target.scope.open_stage(stage)

    async def finish(
        stage: str, started_at: str, counts: dict[str, Any], *, covers: list[str] | None = None
    ) -> None:
        """Close it, with the status it actually reached.

        THE STATUS IS DERIVED, never passed in: every failing step reaches this
        after its `except`, and hardcoding `ok` made the strip a person watches
        show every part of a broken setup succeeding.

        `covers` exists because ONE step is not one document -- `profile` drafts
        several and records a failure under the DOCUMENT's key, so matching on
        the step name alone would call it `ok`.
        """
        names = covers or [stage]
        errored = [name for name in names if name in failed]
        report = StageReport(
            counts=counts,
            status="error" if errored else "ok",
            # `level == "warning"` as well as the prefix: once notes and todos
            # share this list, a note that happens to start with a document key
            # would be reported as a step failure.
            error="; ".join(
                w.text
                for w in ctx.warnings
                if w.level == "warning" and any(w.text.startswith(f"{n}: ") for n in errored)
            )
            or None,
        )
        await target.scope.close_stage(stage, started_at, report)

    async def progress(stage: str, doc: str, status: str, done: int, total: int) -> None:
        await target.emit(
            "stage_progress", stage, {"doc": doc, "status": status, "done": done, "total": total}
        )

    def store(key: str, content: str, task: str) -> None:
        meta = draft_service.store_draft(
            settings, key, content, run_id=target.run_id, task=task, generated_at=ctx.clock.now()
        )
        drafted.append(key)
        if not meta.valid:
            ctx.warn(f"{key}: draft stored but fails its validator: {meta.error}")

    async def call(task: str, *, system: str, user: str, schema: dict, tools=(), model=None):
        return await runner.call_and_record(
            ctx,
            task,
            user_content=user,
            schema=schema,
            system_prompt=system,
            model=model or settings.llm.setup_model,
            effort=settings.llm.setup_effort,
            tools=tools,
            budget=budget,
        )

    # ── survey ───────────────────────────────────────────────────────
    dossier = load_dossier(settings)
    need_dossier = bool(requested)  # every product reads it
    if need_dossier and (dossier is None or refresh_survey):
        started = await begin("survey")
        dossier = {}
        seeds = str(params.get("seeds") or "")
        for index, (facet, instruction) in enumerate(SURVEY_FACETS, start=1):
            target.check_cancel()
            await progress("survey", facet, "drafting", index - 1, len(SURVEY_FACETS))
            user = survey_user(facet, instruction, descriptor, seeds)
            if not settings.offline:
                # Live only, so replayed fixtures keep their keys -- the
                # discovery dateline precedent.
                user += f"\n\nToday's date: {ctx.clock.now()[:10]}"
            result = await call(
                "setup_survey",
                system=survey_system(descriptor),
                user=user,
                schema=setup_survey_schema(),
                tools=("WebSearch", "WebFetch"),
            )
            if result.data:
                dossier[facet] = result.data
            else:
                ctx.warn(f"survey facet {facet} returned nothing")
        save_dossier(settings, dossier)
        await finish("survey", started, {"facets": len(dossier)})
    elif dossier is None:
        dossier = {}

    persona = str(params.get("persona") or "")
    priorities = str(params.get("priorities") or "")

    # ── sources ──────────────────────────────────────────────────────
    sweep_spine: list[dict[str, Any]] = dossier.get("_sweeps") or []
    if "sources" in requested:
        started = await begin("sources")
        counts: dict[str, Any] = {}
        try:
            content, sweep_spine, counts = await _draft_sources(
                target, settings, descriptor, dossier, call
            )
            store("sources", content, "setup_sources")
            dossier["_sweeps"] = sweep_spine
            save_dossier(settings, dossier)
        except (LlmError, BudgetExceeded) as exc:
            failed.append("sources")
            ctx.warn(f"sources: {exc}")
        await finish("sources", started, counts)

    # ── ontology ─────────────────────────────────────────────────────
    draft_ontology = None
    if "ontology" in requested:
        started = await begin("ontology")
        try:
            text = await _draft_document(
                settings,
                descriptor,
                dossier,
                "ontology",
                "setup_ontology",
                "",
                call,
                system=ontology_system(settings, descriptor),
            )
            store("ontology", text, "setup_ontology")
        except (LlmError, BudgetExceeded) as exc:
            failed.append("ontology")
            ctx.warn(f"ontology: {exc}")
        await finish("ontology", started, {})
    try:
        content, _ = draft_service.read_draft(settings, "ontology")
        draft_ontology = parse_ontology(yaml.safe_load(content))
    except Exception:  # noqa: BLE001 -- no usable draft; anchors fall back to the live set
        draft_ontology = None

    # ── profile documents ────────────────────────────────────────────
    profile_requested = [(k, t) for k, t in PROFILE_DOCS if k in requested]
    if profile_requested:
        started = await begin("profile")
        done = 0
        for key, task in profile_requested:
            target.check_cancel()
            await progress("profile", key, "drafting", done, len(profile_requested))
            extras = _extras_for(key, descriptor, persona, priorities, sweep_spine)
            try:
                text = await _draft_document(settings, descriptor, dossier, key, task, extras, call)
                store(key, text, task)
            except (LlmError, BudgetExceeded) as exc:
                failed.append(key)
                ctx.warn(f"{key}: {exc}")
            done += 1
            await progress("profile", key, "drafted", done, len(profile_requested))
        await finish(
            "profile", started, {"documents": done}, covers=[key for key, _ in profile_requested]
        )

    # ── anchors ──────────────────────────────────────────────────────
    if "anchors" in requested:
        started = await begin("anchors")
        counts = {}
        try:
            content, counts = await _draft_anchors(
                target, settings, descriptor, dossier, draft_ontology, call
            )
            store("anchors", content, "setup_anchors_expand")
        except (LlmError, BudgetExceeded) as exc:
            failed.append("anchors")
            ctx.warn(f"anchors: {exc}")
        await finish("anchors", started, counts)

    data = {"drafted": drafted, "failed": failed, "generated_from": {"domain": descriptor.slug}}
    # Every `failed` key already went through `ctx.warn` above, so the status
    # takes care of itself. What is left to say is how many drafts a reviewer
    # actually has in front of them.
    if not drafted:
        # Drafted nothing and nothing was even attempted to fail -- there was
        # nothing in `requested` to do. A run that attempted and produced none
        # is degraded (the warnings say so) with no drafts to show, which is
        # `nothing_found`, not a failure: the dossier it built is still there.
        verdict = "nothing_found" if failed else "nothing_to_do"
    elif failed:
        verdict = "some"
    else:
        verdict = "delivered"
    return LaunchOutcome(outcome=verdict, data=data)


def _extras_for(
    key: str, descriptor, persona: str, priorities: str, sweeps: list[dict[str, Any]]
) -> str:
    parts: list[str] = []
    if key == "domain":
        parts.append(f"The frozen slug for this deployment: {descriptor.slug}")
    if key == "relevance_profile":
        if persona:
            parts.append(
                f"The reader, in the operator's words (write as this person):\n\n{persona}"
            )
        if priorities:
            parts.append(f"Current priorities, from the operator:\n\n{priorities}")
    if key in ("research_prompt", "brief_sections") and sweeps:
        spine = "\n".join(
            f"- {s['id']}: {s['name']} (tags: {', '.join(s.get('tags') or [])})" for s in sweeps
        )
        parts.append(
            "The discovery sweep spine. Headings and section names must agree "
            f"with these:\n\n{spine}"
        )
    return "\n\n".join(parts)


async def _draft_document(
    settings, descriptor, dossier, key, task, extras, call, *, system: str | None = None
) -> str:
    """One document, with one repair retry through its real validator.

    `system` is for the ontology, whose prompt is its own rather than
    `DOCUMENT_SPECS`'. It takes the repair for the same reason every other
    document does and one more: an unquoted scalar holding a colon fails the
    YAML parse, and anchors are drafted from the ontology draft, so the slip
    costs two documents rather than one.
    """
    system = system or document_system(settings, key, descriptor)
    result = await call(
        task,
        system=system,
        user=document_user(descriptor, dossier, extras),
        schema=setup_document_schema(),
    )
    text = (result.data or {}).get("document", "")
    if not text:
        raise LlmError(f"{task} returned no document")
    try:
        config_service.validate(settings, key, text)
        return text
    except config_service.ConfigError as error:
        repair = await call(
            task,
            system=system,
            user=document_user(
                descriptor,
                dossier,
                extras + f"\n\nYour previous attempt failed validation: {error}\n"
                "Return the corrected complete document.",
            ),
            schema=setup_document_schema(),
        )
        repaired = (repair.data or {}).get("document", "")
        # The stronger of the two attempts; an invalid draft is stored flagged
        # either way, and the editor is the backstop.
        return repaired or text


async def _draft_sources(target, settings, descriptor, dossier, call):
    """Propose, then VERIFY: every rss feed is fetched before it is accepted."""
    from venona.sources.base import SourceSpec
    from venona.sources.rss import collect_rss

    result = await call(
        "setup_sources",
        system=sources_system(settings, descriptor),
        user=document_user(descriptor, dossier),
        schema=setup_sources_schema(),
    )
    proposal = result.data or {}
    feeds = proposal.get("feeds") or []
    sweeps = proposal.get("sweeps") or []

    kept: list[dict[str, Any]] = []
    removed: list[dict[str, str]] = []
    for feed in feeds:
        target.check_cancel()
        try:
            spec = SourceSpec(
                id=slugify(feed["id"]).replace("-", "_") or "feed",
                name=feed["name"],
                kind="rss",
                url=feed["url"],
                publisher=feed.get("publisher"),
            )
        except Exception as exc:  # noqa: BLE001 -- a malformed proposal is a removal, not a crash
            removed.append({"url": str(feed.get("url")), "reason": f"invalid proposal: {exc}"})
            continue
        probe = await collect_rss(spec, target.ctx.http)
        if probe.ok and probe.items:
            kept.append({"feed": feed, "spec": spec})
            await target.emit(
                "log", "sources", {"message": f"verified {spec.id} ({len(probe.items)} items)"}
            )
        else:
            reason = probe.error or "feed returned no items"
            removed.append({"url": feed["url"], "reason": reason})
            await target.emit("log", "sources", {"message": f"dropped {spec.id}: {reason}"})

    content = _compose_sources_yaml(descriptor, kept, sweeps, removed)
    counts = {"proposed": len(feeds), "kept": len(kept), "removed": len(removed)}
    return content, sweeps, counts


def _compose_sources_yaml(descriptor, kept, sweeps, removed) -> str:
    lines = [
        f"# Sources for the {descriptor.display_name} instance -- generated by",
        "# domain setup, verified feed by feed, and edited like any other config.",
        "",
        "defaults:",
        "  max_items: 40",
        "  fetch_body: true",
        "",
        "sources:",
    ]
    for entry in kept:
        feed, spec = entry["feed"], entry["spec"]
        lines += [
            f"  - id: {spec.id}",
            f"    name: {json.dumps(feed['name'])}",
            "    kind: rss",
            f"    url: {feed['url']}",
            f"    publisher: {json.dumps(feed.get('publisher') or '')}",
            "",
        ]
    for sweep in sweeps:
        sweep_id = slugify(sweep["id"]).replace("-", "_")
        if not sweep_id.startswith("sweep_"):
            sweep_id = f"sweep_{sweep_id}"
        tags = ", ".join(slugify(t).replace("-", "_") for t in sweep.get("tags") or [])
        lines += [
            f"  - id: {sweep_id}",
            f"    name: {json.dumps(sweep['name'])}",
            "    kind: discovery",
            f"    tags: [{tags}]",
            "",
        ]
    if removed:
        lines.append("removed:")
        for entry in removed:
            lines += [
                f"  - url: {entry['url']}",
                f"    reason: {json.dumps(entry['reason'])}",
            ]
    return "\n".join(lines).rstrip() + "\n"


async def _draft_anchors(target, settings, descriptor, dossier, draft_ontology, call):
    """The trunk: one web-grounded top call, then no-tools category batches."""
    ontology = draft_ontology or get_ontology()
    type_names = list(ontology.type_names)
    predicate_names = list(ontology.predicate_names)
    # A fresh scaffold carries the skeleton ontology -- version 0, zero types,
    # zero predicates -- until this run's ontology stage drafts one, and both of
    # the anchor schema's enums are built from those lists. An empty enum is not
    # a schema the CLI will accept: it refuses the whole call with a JSON Schema
    # validation dump naming four sub-paths, which tells a reader nothing about
    # the ontology being the thing that is missing.
    if not type_names or not predicate_names:
        raise LlmError(
            "no ontology to anchor against: this instance still has the "
            "skeleton ontology, so there are no entity types or predicates to "
            "offer. The ontology stage has to produce a draft before anchors "
            "can run."
        )
    entities: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []
    ambiguous: list[dict[str, str]] = []
    dropped_edges = 0

    # The jurisdictions ride in the FIRST round on purpose. Each round is shown
    # only what earlier rounds declared, and an edge naming an undeclared entity
    # is dropped -- so a jurisdiction arriving late is one no organisation can
    # point at, and the home-jurisdiction edge is the attachment most entities
    # have. Declaring them last produced a trunk of floating names.
    rounds = [
        (
            "setup_anchors_trunk",
            "the jurisdictions, and the top of the trunk beneath them",
            ("WebSearch", "WebFetch"),
        )
    ]
    rounds += [("setup_anchors_expand", category, ()) for category in EXPAND_CATEGORIES]

    for index, (task, category, tools) in enumerate(rounds, start=1):
        target.check_cancel()
        await target.emit(
            "stage_progress",
            "anchors",
            {"doc": "anchors", "status": category, "done": index - 1, "total": len(rounds)},
        )
        declared = "\n".join(f"- {name}" for name in sorted(entities)) or "(none yet)"
        user = document_user(
            descriptor,
            dossier,
            f"Declare: {category}.\n\nAlready declared (attach edges to these by "
            f"name; do not redeclare):\n{declared}",
        )
        result = await call(
            task,
            system=anchors_system(settings, descriptor, grounded=bool(tools), ontology=ontology),
            user=user,
            schema=setup_anchors_schema(type_names, predicate_names),
            tools=tools,
        )
        batch = result.data or {}
        for row in batch.get("entities") or []:
            name = row["name"].strip()
            if name and name not in entities:
                entities[name] = row
        edges.extend(batch.get("edges") or [])
        ambiguous.extend(batch.get("ambiguous") or [])

    prefix_for = ontology.type_prefix
    id_for = {
        name: f"{prefix_for[row['type']]}:{slugify(name)}"
        for name, row in entities.items()
        if row["type"] in prefix_for
    }

    out_entities = []
    for name, row in entities.items():
        if name not in id_for:
            continue
        entry: dict[str, Any] = {
            "id": id_for[name],
            "type": row["type"],
            "name": name,
            "relevance": round(float(row.get("relevance", 0.5)), 2),
            "summary": row.get("summary", ""),
        }
        aliases = [a for a in (row.get("aliases") or []) if a and a != name]
        if aliases:
            entry["aliases"] = [{"surface": a} for a in aliases]
        out_entities.append(entry)

    out_edges = []
    seen_edges = set()
    for edge in edges:
        src, dst = id_for.get(edge["src_name"]), id_for.get(edge["dst_name"])
        if not src or not dst or src == dst:
            dropped_edges += 1
            continue
        key = (src, edge["predicate"], dst)
        if key in seen_edges:
            continue
        seen_edges.add(key)
        out_edges.append({"src": src, "predicate": edge["predicate"], "dst": dst})

    document = {
        "entities": out_entities,
        "edges": out_edges,
        "ambiguous": [{"surface": row["surface"], "reason": row["reason"]} for row in ambiguous],
    }
    header = (
        f"# Anchor trunk for the {descriptor.display_name} instance -- generated by\n"
        "# domain setup. Every line is a proposal; review before applying, and\n"
        "# remember that saving is not loading.\n"
    )
    content = header + yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=100)
    # How much of what was declared actually got attached to anything. This is
    # the number the whole exercise is judged on, and until now it was neither
    # measured nor shown: a draft of names with no structure counted as a
    # success, and the shortfall only surfaced later as a graph of floating
    # dots that no amount of reading could fix.
    attached = {e["src"] for e in out_edges} | {e["dst"] for e in out_edges}
    floating = [e for e in out_entities if e["id"] not in attached]

    if out_entities and len(floating) > len(out_entities) / 2:
        target.ctx.todo(
            f"{len(floating)} of {len(out_entities)} names in the starting structure "
            f"have nothing attached to them. Read it before applying: no article will "
            f"supply the missing links later.",
            to="config",
        )
    if dropped_edges > max(4, len(out_edges) / 4):
        target.ctx.todo(
            f"{dropped_edges} suggested links named something that was never declared, "
            f"so they could not be kept. The structure is thinner than it looks.",
            to="config",
        )

    counts = {
        "entities": len(out_entities),
        "edges": len(out_edges),
        "dropped_edges": dropped_edges,
        "floating": len(floating),
        "ambiguous": len(ambiguous),
    }
    return content, counts
