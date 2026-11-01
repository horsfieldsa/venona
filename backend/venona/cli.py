"""`venona` command line. Thin wrapper -- every subcommand delegates to the same
code paths the API uses, so the CLI and the UI can never disagree."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import difflib
import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from venona import logging as venona_logging
from venona.db import core as db
from venona.db.repositories import access as access_repo
from venona.db.repositories import cli as repo
from venona.graph.client import GraphClient
from venona.graph.projector import project
from venona.graph.writer import GraphWriter
from venona.llm.executor import build_executor
from venona.pipeline.types import utc_now_iso
from venona.settings import get_settings

#: What a run's two verdicts mean to a shell (ADR 0183; the table is in
#: `docs/RUNBOOK.md`).
#:
#:   0  it ran and it delivered (fully, or in part)
#:   1  it did not run, or could not finish
#:   2  it ran cleanly and there was nothing to deliver
#:
#: The third code is the point: without it a script cannot tell "the web holds no
#: definition" from "the web was unreachable". 2 rather than a second failure
#: code because nothing went wrong, and `if venona lookup FOO` in bash treats
#: only 0 as success, so a caller that ignores the distinction gets the safe
#: reading.
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_NOTHING_FOUND = 2


def exit_code(status: str, outcome: str | None) -> int:
    """The shell's view of a finished run. See the codes above."""
    if status in ("failed", "cancelled"):
        return EXIT_FAILED
    if outcome in ("nothing_found", "nothing_to_do"):
        return EXIT_NOTHING_FOUND
    return EXIT_OK


def _outcome_word(kind: str, outcome: str | None) -> str:
    """A run's outcome in the words that kind of run uses, for a terminal.

    Falls back to the stored value rather than to nothing: a kind that has not
    declared a phrase should read as slightly raw, not vanish.
    """
    from venona.services.run_types import type_for

    if not outcome:
        return ""
    return type_for(kind).outcomes.get(outcome, outcome.replace("_", " "))


GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
RESET = "\033[0m"

#: How each `RunMessage.level` prints. A note is not a warning, and a terminal
#: that says `warn` in front of every line teaches the same "ignore this" the
#: status field had to be split to stop.
_MESSAGE_STYLE = {
    "warning": (YELLOW, "warn"),
    "todo": (GREEN, "needs you"),
    "note": (DIM, "note"),
}


@contextmanager
def _db(*, migrate: bool = False) -> Iterator[db.Conn]:
    """The CLI's one way to get a connection.

    2026-08-17: of the thirty-one commands that opened their own, four LEAKED --
    `_entity_show` never closed at all, and `_fetch`'s `finally` closed the HTTP
    client and forgot the connection. A command with a second resource to clean
    up is exactly where a hand-written `try/finally` cleans up only one, and
    nothing complains: a leak in a process about to exit looks like working code.
    """
    conn = db.connect(get_settings().database_url)
    try:
        if migrate:
            db.migrate(conn)
        yield conn
    finally:
        conn.close()


def _mark(ok: bool) -> str:
    return f"{GREEN}ok{RESET}" if ok else f"{RED}FAIL{RESET}"


API_BASE = os.environ.get("VENONA_API_URL", "http://localhost:8000")
API_HEALTH_URL = API_BASE + "/api/health"


async def _api_health() -> tuple[bool, str]:
    """Ask the API server whether it is up. Returns (ok, detail).

    Actually ask. A literal `f"api {_mark(True)}"` prints `api ok` with the
    server stopped, which is the one state the check exists to detect and the
    one you are in when you run it.

    It asks `/api/health` and not `/api/health/live` on purpose: `active_run` is
    the detail this prints, and the liveness endpoint deliberately carries no
    dependency state. So it needs the bearer token when auth is on -- and a 401
    gets its own branch, because reporting "unreachable" for a server that
    answered is precisely the misdiagnosis this function was written to stop.
    """
    import httpx

    token = os.environ.get("VENONA_AUTH_API_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(API_HEALTH_URL, headers=headers)
    except Exception as exc:  # noqa: BLE001 - any failure to reach it is "down"
        return False, f"{type(exc).__name__}: {API_HEALTH_URL} unreachable"
    if response.status_code == 401:
        return False, (
            "reachable but not authenticated -- set VENONA_AUTH_API_TOKEN "
            "(`uv run venona auth token` prints one)"
        )
    if response.status_code != 200:
        return False, f"HTTP {response.status_code}"
    active = (response.json() or {}).get("active_run")
    return True, active or ""


async def _health() -> int:
    settings = get_settings()
    graph = GraphClient(settings)
    llm = build_executor(settings)

    try:
        api_ok, api_detail = await _api_health()
        gh = await graph.health()
        parts = [f"api {_mark(api_ok)}" + (f" {DIM}{api_detail}{RESET}" if not api_ok else "")]

        if gh.ok:
            parts.append(f"neo4j {_mark(True)} {DIM}({gh.version} {gh.edition}){RESET}")
        else:
            parts.append(f"neo4j {_mark(False)} {DIM}{gh.error}{RESET}")

        cli_ok = True
        if hasattr(llm, "health"):
            ch = await llm.health()
            cli_ok = ch.ok
            # The service's own name, and its own pin. Naming one vendor here
            # printed a pin mismatch for the life of any instance running the
            # other, by comparing its version against a number belonging to
            # somebody else.
            harness = getattr(llm, "harness", None)
            name = harness.label if harness else "model"
            expected = (
                settings.llm.pinned_version_for(harness.key)
                if harness
                else settings.llm.pinned_version
            )
            if ch.ok:
                pin = "" if ch.version_matches_pin else f" {YELLOW}(pinned {expected}){RESET}"
                # `subscription` is absent for a service that does not report a
                # plan, and an empty parenthesis reads as a missing answer
                # rather than as a question nobody asked.
                facts = ", ".join(str(f) for f in (ch.version, ch.subscription) if f)
                parts.append(f"{name} {_mark(True)} {DIM}({facts}){RESET}{pin}")
            else:
                parts.append(f"{name} {_mark(False)} {DIM}{ch.error}{RESET}")
        else:
            parts.append(f"model {DIM}replay (offline){RESET}")

        # The trunk. A wiped database looks populated after one run -- the
        # pipeline mints its own ids and the corpus is simply worse, in a way
        # nothing else reports -- so the condition is surfaced here, BEFORE a
        # run is paid for, not only by the analyze-stage warning after one.
        trunk_ok = True
        try:
            with _db() as conn:
                t = repo.select_trunk_counts(conn).fetchone()
            trunk_ok = bool(t["active_entities"]) and bool(t["ambiguous_surfaces"])
            if trunk_ok:
                parts.append(
                    f"trunk {_mark(True)} {DIM}({t['active_entities']} active, "
                    f"{t['ambiguous_surfaces']} ambiguous){RESET}"
                )
            else:
                parts.append(f"trunk {_mark(False)} {DIM}empty -- run `venona anchors load`{RESET}")
        except Exception as exc:  # a dead database is its own line, not a crash
            trunk_ok = False
            parts.append(f"trunk {_mark(False)} {DIM}{exc}{RESET}")

        # Also from the API rather than asserted: `run_service.registry` is
        # in-process, so this process never has one.
        active = api_detail if api_ok and api_detail else "none"
        parts.append(f"active_run {DIM}{active}{RESET}")
        print(" · ".join(parts))
        return 0 if (api_ok and gh.ok and cli_ok and trunk_ok) else 1
    finally:
        await graph.close()


def _migrate() -> int:
    # Configuration first. `make setup` and `make reset-bare` reach the schema
    # through here, and a settings file this release cannot read would stop the
    # process a moment later anyway, with a less useful message.
    from venona.services import config_migrations
    from venona.settings import Paths

    applied_config = config_migrations.migrate(Paths())
    if applied_config:
        print(f"applied {len(applied_config)} settings migration(s): {applied_config}")

    with _db() as conn:
        applied = db.migrate(conn)
        if applied:
            print(f"applied {len(applied)} migration(s): {', '.join(applied)}")
        else:
            print("schema up to date")
        return 0


async def _seed() -> int:
    from venona.services.seed import load_seed

    settings = get_settings()
    with _db() as conn:
        graph = GraphClient(settings)
        try:
            db.migrate(conn)
            load_seed(conn, settings.fixtures_dir)
            counts = await project(conn, graph.driver, rebuild=True)
            print(
                f"projected {counts['entities']} entities, {counts['edges']} edges, "
                f"{counts['articles']} articles, {counts['mentions']} mentions"
            )
            return 0
        finally:
            await graph.close()


async def _graph_rebuild() -> int:
    settings = get_settings()
    with _db() as conn:
        graph = GraphClient(settings)
        try:
            counts = await project(conn, graph.driver, rebuild=True)
            print(f"rebuilt: {json.dumps(counts, sort_keys=True)}")
            return 0
        finally:
            await graph.close()


async def _graph_stats() -> int:
    """Output is compared byte-for-byte by the idempotency gate, so it must be
    deterministic: sorted keys, no timestamps."""
    settings = get_settings()
    graph = GraphClient(settings)
    try:
        stats = await GraphWriter(graph.driver).stats()
        print(json.dumps(stats, sort_keys=True, indent=2))
        return 0
    finally:
        await graph.close()


def _ontology_docs() -> int:
    """Regenerate docs/graph-schema.md. `make lint-ontology` fails on drift."""
    from venona.ontology import get_ontology

    ontology = get_ontology()
    target = Path(__file__).resolve().parents[2] / "docs" / "graph-schema.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(ontology.markdown_reference())
    print(f"wrote {target.relative_to(target.parents[1])} (ontology v{ontology.version})")
    return 0


def _ontology_check() -> int:
    """Load and validate. An invalid ontology must fail here, not mid-run."""
    from venona.ontology import OntologyError, get_ontology

    try:
        ontology = get_ontology()
    except OntologyError as exc:
        print(f"{RED}ontology invalid{RESET}: {exc}", file=sys.stderr)
        return 1

    print(
        f"ontology v{ontology.version} {_mark(True)} "
        f"{DIM}({len(ontology.entity_types)} types, "
        f"{len(ontology.predicates)} predicates){RESET}"
    )
    for name, spec in ontology.predicates.items():
        arrow = "<->" if spec.symmetric else "-->"
        print(f"  {DIM}{'|'.join(spec.domain):24} {arrow} {name:16} {'|'.join(spec.range)}{RESET}")
    return 0


async def _analyze(path: str | None, url: str, title: str) -> int:
    """Run one analyze call end to end.

    With VENONA_OFFLINE=1 this replays a recorded envelope and must produce an
    identical result; with VENONA_LLM_RECORD=1 it captures one.
    """
    from venona.llm.prompts import load_profile
    from venona.llm.runner import analyze_document

    settings = get_settings()
    with _db() as conn:
        llm = build_executor(settings)
        profile = load_profile(settings)

        if path:
            text = Path(path).read_text()  # noqa: ASYNC240 - CLI startup, not a hot path
        else:
            row = repo.select_documents(conn).fetchone()
            if row is None:
                print("no documents; run `make seed` or pass a file", file=sys.stderr)
                return 1
            text, title, url = row["text_nfc"], row["title"], row["url"]

        print(f"{DIM}profile {profile.short_hash} · model {settings.llm.analyze_model}{RESET}")
        outcome = await analyze_document(
            conn=conn,
            llm=llm,
            settings=settings,
            title=title,
            url=url,
            text=text,
            run_id="run:manual",
            call_id=f"call:{profile.short_hash}",
            created_at="2026-08-04T12:00:00.000Z",
            profile=profile,
        )

    result = outcome.result
    if not result.ok:
        print(f"{RED}call failed{RESET}: {result.error}", file=sys.stderr)
        return 1

    data = result.data or {}
    rel = data.get("relevance", {})
    print()
    print(f"  score      {outcome.overall}/100  ({outcome.tier})")
    print(f"  note       {rel.get('note', '')[:200]}")
    print(f"  so what    {rel.get('so_what', '')[:160]}")
    print(f"  hooks      {rel.get('profile_hooks', [])}")
    print(f"  entities   {len(data.get('entities', []))}")
    for e in data.get("entities", [])[:8]:
        print(f"      {e['type']:14} {e['surface']}")
    print(f"  relations  {len(data.get('relations', []))}")
    for r in data.get("relations", [])[:8]:
        print(f"      {r['subject_surface']} --{r['predicate']}--> {r['object_surface']}")
    print()
    print(f"  cost ${result.cost_usd:.4f} · {result.duration_ms}ms · models {list(result.models)}")
    print(
        f"  cache_read={result.usage.cache_read_input_tokens} "
        f"cache_creation={result.usage.cache_creation_input_tokens}"
    )
    return 0


async def _verify_sources() -> int:
    """Probe every declared feed. Publisher feed paths drift silently, so this
    is worth running before trusting a run."""
    from venona.sources.http import build_http_client
    from venona.sources.registry import load_sources
    from venona.sources.rss import collect_rss

    settings = get_settings()
    specs = load_sources(settings.config_dir)
    http = build_http_client(settings)
    failures = 0
    probed = 0

    try:
        for spec in specs:
            if not spec.enabled:
                print(f"  {DIM}skip{RESET}   {spec.id}")
                continue
            if spec.kind != "rss":
                # Discovery has no endpoint to probe -- it searches. Passing it
                # to `collect_rss` would GET `None` and report the source as
                # broken, which is exactly the kind of false alarm this command
                # exists to prevent.
                print(
                    f"  {DIM}search{RESET} {spec.id:24} "
                    f"{DIM}kind={spec.kind}, nothing to probe{RESET}"
                )
                continue
            probed += 1
            result = await collect_rss(spec, http)
            if result.ok and result.items:
                newest = result.items[0].published_at or "?"
                print(
                    f"  {_mark(True)}     {spec.id:24} {len(result.items):>3} items  "
                    f"{DIM}newest {newest}{RESET}"
                )
            else:
                failures += 1
                print(f"  {_mark(False)}   {spec.id:24} {RED}{result.error}{RESET}")
    finally:
        await http.aclose()

    print()
    # Counted against sources actually PROBED. Including discovery here would
    # report "9/10 healthy" forever on a perfectly healthy configuration.
    print(f"{probed - failures}/{probed} probed sources healthy")
    return 1 if failures else 0


def _sources(enable: str | None) -> int:
    """Inspect the strike counter, and clear it.

    `auto_disabled` is a one-way door without this: `record_failure` sets it,
    nothing resets it but a success, and an auto-disabled source is excluded from
    the run that would produce that success.
    """
    from venona.sources.registry import STRIKE_LIMIT, clear_strikes, load_sources

    settings = get_settings()
    with _db() as conn:
        db.migrate(conn)

        if enable:
            known = {spec.id for spec in load_sources(settings.config_dir)}
            if enable not in known:
                print(f"{RED}no such source {enable!r}{RESET}", file=sys.stderr)
                return 1
            # Through the shared helper, so the CLI and the Settings button
            # cannot drift into clearing different columns.
            clear_strikes(conn, enable)
            print(f"{GREEN}cleared{RESET} strikes on {enable}")
            return 0

        specs = {spec.id: spec for spec in load_sources(settings.config_dir)}
        rows = {row["source_id"]: row for row in repo.select_source_health(conn)}

        print()
        for source_id, spec in specs.items():
            row = rows.get(source_id)
            strikes = row["consecutive_failures"] if row else 0
            disabled = bool(row["auto_disabled"]) if row else False

            if not spec.enabled:
                state = f"{DIM}off (config){RESET}"
            elif disabled:
                state = f"{RED}AUTO-DISABLED{RESET}"
            elif strikes:
                state = f"{YELLOW}{strikes}/{STRIKE_LIMIT} strikes{RESET}"
            else:
                state = f"{GREEN}ok{RESET}"

            last = (row["last_success_at"] if row else None) or "never"
            print(f"  {spec.id:24} {spec.kind:10} {state:24} {DIM}last ok {last}{RESET}")
            if disabled and row["last_error"]:
                print(f"      {DIM}{row['last_error'][:110]}{RESET}")

        if any(r["auto_disabled"] for r in rows.values()):
            print()
            print(f"{DIM}re-enable with: venona sources --enable <id>{RESET}")
        return 0


async def _fetch(limit: int | None, dry: bool) -> int:
    """Run the fetch stage on its own."""
    from venona.pipeline.fetch import run_fetch
    from venona.pipeline.types import build_context
    from venona.sources.http import build_http_client

    settings = get_settings()

    # Nothing below this branch touches the network or the database. The flag
    # was once parsed and never read, so `--dry` polled every feed and wrote
    # documents -- the opposite of what it says.
    if dry:
        from venona.sources.registry import load_sources

        specs = load_sources(settings.config_dir)
        enabled = [s for s in specs if s.enabled]
        print(
            f"would poll {len(enabled)} of {len(specs)} source(s), "
            f"limit {limit if limit is not None else 'per-source default'}:"
        )
        for spec in enabled:
            print(f"  {DIM}{spec.kind:8}{RESET} {spec.id:28} {spec.url}")
        skipped = [s for s in specs if not s.enabled]
        if skipped:
            print(f"\n  {DIM}disabled: {', '.join(s.id for s in skipped)}{RESET}")
        return 0

    with _db() as conn:
        db.migrate(conn)
        http = build_http_client(settings, record=os.environ.get("VENONA_HTTP_RECORD") == "1")
        llm = build_executor(settings)

        ctx = build_context(
            run_id=f"run:fetch-{get_settings().llm.pinned_version}",
            db=conn,
            llm=llm,
            settings=settings,
            http=http,
        )

        before = repo.select_documents_2(conn).fetchone()["n"]
        try:
            report = await run_fetch(ctx, limit_per_source=limit)
        finally:
            await http.aclose()

        after = repo.select_documents_3(conn).fetchone()["n"]

        print()
        print(
            f"  sources     {report.sources_ok} ok, {report.sources_failed} failed, "
            f"{report.sources_not_modified} not-modified"
        )
        print(f"  items       {report.items_seen} seen")
        print(
            f"  documents   {report.documents_new} new, {report.documents_duplicate} duplicate, "
            f"{report.documents_skipped} skipped (too short)"
        )
        print(f"  clusters    {report.clusters}")
        print(f"  total docs  {before} -> {after}")
        if report.warnings:
            print()
            for warning in report.warnings:
                print(f"  {YELLOW}warn{RESET} {warning}")
        return 0


async def _analyze_run(limit: int | None, max_calls: int | None) -> int:
    """Analyze pending clusters, resolving surfaces into the assertion log."""
    from venona.graph.client import GraphClient
    from venona.graph.projector import project
    from venona.llm.budget import Budget
    from venona.pipeline.analyze import run_analyze
    from venona.pipeline.types import build_context

    settings = get_settings()
    if max_calls:
        settings.llm.max_calls_per_run = max_calls

    with _db() as conn:
        db.migrate(conn)
        llm = build_executor(settings)
        graph = GraphClient(settings)
        budget = Budget(settings=settings.llm)

        ctx = build_context(run_id="run:analyze", db=conn, llm=llm, settings=settings, graph=graph)

        try:
            report = await run_analyze(ctx, limit=limit, budget=budget)
            counts = await project(conn, graph.driver)
        finally:
            await graph.close()

    print()
    print(
        f"  clusters    {report.clusters_analyzed} analyzed, "
        f"{report.clusters_skipped} skipped, {report.clusters_deferred} deferred, "
        f"{report.refused} refused"
    )
    print(
        f"  entities    {report.entities_resolved} resolved "
        f"({report.entities_minted} newly minted), "
        f"{report.entities_ambiguous} left ambiguous"
    )
    print(f"  relations   {report.relations_written} written, {report.relations_rejected} rejected")
    print(f"  promoted    {report.promoted} provisional -> active")
    print(f"  conflicts   {report.alias_conflicts} open alias conflicts")
    print(f"  cost        ${report.cost_usd:.3f} over {budget.calls} calls")
    print(f"  projected   {counts['entities']} entities, {counts['edges']} edges")
    if report.warnings:
        print()
        for warning in report.warnings[:8]:
            print(f"  {YELLOW}warn{RESET} {warning[:150]}")
    return 0


def _entity_merge(from_id: str, to_id: str, reason: str | None) -> int:
    """Merge one entity into another. The command `docs/RUNBOOK.md` documents.

    A merge is an append-only `entity_mappings` row -- `assertions` are never
    rewritten, so `venona graph rebuild` still reconstructs everything and the
    merge is reversible with `venona entity unmerge`.
    """
    from venona.pipeline.types import SystemClock, SystemIdGen
    from venona.services.curate import MergeError, merge_entities

    with _db() as conn:
        clock, ids = SystemClock(), SystemIdGen()
        try:
            merge_entities(
                conn,
                from_id=from_id,
                to_id=to_id,
                merge_id=f"merge:{ids.uuid7()}",
                reason=reason or "manual",
                now=clock.now(),
            )
        except MergeError as exc:
            print(f"{RED}{exc}{RESET}", file=sys.stderr)
            return 1

    print(f"{GREEN}merged{RESET} {from_id} -> {to_id}")
    print(f"{DIM}now run: venona graph rebuild{RESET}")
    return 0


def _entity_unmerge(from_id: str, merge_id: str) -> int:
    from venona.pipeline.types import SystemClock
    from venona.services.curate import MergeError, unmerge

    with _db() as conn:
        try:
            unmerge(conn, from_id=from_id, merge_id=merge_id, now=SystemClock().now())
        except MergeError as exc:
            print(f"{RED}{exc}{RESET}", file=sys.stderr)
            return 1

    print(f"{GREEN}unmerged{RESET} {from_id}")
    print(f"{DIM}now run: venona graph rebuild{RESET}")
    return 0


def _anchors_load(*, dry_run: bool, apply_merges: bool) -> int:
    """Load the declared top-level structure.

    Deliberately NOT part of `venona seed`: that loads a demo fixture including
    a fictional brief and four invented documents, which must never reach a live
    database. Anchors carry their own run id and are re-runnable on their own.
    """
    from venona.services.seed import load_anchors

    settings = get_settings()
    with _db() as conn:
        try:
            load_anchors(
                conn,
                settings.profile_dir,
                apply_merges=apply_merges,
                dry_run=dry_run,
            )
        except (FileNotFoundError, ValueError) as exc:
            print(f"{RED}anchors{RESET}: {exc}", file=sys.stderr)
            return 1
    if not dry_run:
        print(f"{DIM}now run: venona graph rebuild{RESET}")
    return 0


async def _anchors_propose(*, limit: int, use_model: bool) -> int:
    """Propose attachments into the curation queue. Writes no assertion.

    Run `--no-model` first and read ten of the rows by hand: the deterministic
    passes are free, and if their precision is poor it is worth tightening them
    before paying for the model pass.
    """
    from venona.llm.budget import Budget
    from venona.pipeline.anchors import propose_anchors
    from venona.pipeline.types import build_context

    settings = get_settings()
    with _db() as conn:
        llm = build_executor(settings)
        ctx = build_context(
            run_id="run:anchors-propose",
            db=conn,
            llm=llm,
            settings=settings,
        )
        report = await propose_anchors(
            ctx,
            limit=limit,
            use_model=use_model,
            budget=Budget(settings=settings.llm),
        )

    print(f"  isolated       {report.isolated}")
    print(f"  proposed       {report.proposed}")
    print(f"    transitive   {report.from_transitive}")
    print(f"    model        {report.from_model}")
    for warning in report.warnings:
        print(f"  {YELLOW}warn{RESET} {warning}")
    if report.proposed:
        print(f"{DIM}review them: venona conflicts --kind anchor, or /admin/curation{RESET}")
    return 0


async def _define_backfill(
    *, limit: int | None, min_salience: float, max_calls: int | None, use_model: bool
) -> int:
    """Re-read stored articles for the names they explain and nothing reached.

    The backfill half of the definition work. `analyze` now asks for a defining
    sentence on every entity it extracts, which costs nothing extra and covers
    everything read from here on; the articles read before that field existed
    hold their descriptions where only a re-read can get at them.

    RESUMABLE, and meant to be run more than once. Every name a pass covers
    gets its answer stamped onto the assertion -- the sentence, or an empty
    string where the article turned out to describe nothing -- so a second
    invocation starts where the first stopped rather than paying for it again.
    Stop it with Ctrl-C or from the Runs page and nothing is lost.

    The free pass runs first on every article, so the ones whose descriptions
    are plain appositives never reach the model at all.
    """
    from venona.pipeline.define import run_backfill
    from venona.pipeline.types import with_overrides
    from venona.services import run_launcher, run_service

    settings = get_settings()

    with _db() as conn:
        # The LIGHT lane, so this does not refuse -- or get refused by -- the
        # nightly research run. It writes definitions and nothing else. C11.
        async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
            if max_calls is not None:
                # Through the SETTINGS the budget reads, which is where the cap
                # actually lives -- `Budget` holds no ceiling of its own, it
                # checks `settings.llm.max_calls_per_run` on every call. The
                # context's settings are rebound too, so a stage reading
                # `ctx.settings` sees the ceiling this run was asked for.
                target.ctx.settings = with_overrides(target.ctx.settings, max_calls=max_calls)
                target.ctx.budget.settings = target.ctx.settings.llm
            report = await run_backfill(
                target.ctx,
                min_salience=min_salience,
                document_limit=limit,
                use_model=use_model,
                budget=target.ctx.budget,
                emit=target.emit,
            )
            for warning in report.warnings:
                target.ctx.warn(warning)

            # KEYED ON NAMES, not on articles. A free-only pass never reaches
            # the document loop, so `documents_seen` stays 0 and a run that had
            # just explained 25 names reported "everything already explained".
            # `entities_seen` is what every path sets, and it is the question
            # the outcome is actually answering.
            if not report.entities_seen:
                verdict = "nothing_to_do"
            elif not report.defined:
                verdict = "nothing_found"
            elif report.defined < report.entities_seen:
                verdict = "some"
            else:
                verdict = "delivered"
            return run_launcher.LaunchOutcome(outcome=verdict, data=report.as_dict())

        try:
            started = await run_launcher.launch(
                run_launcher.host(settings, llm=None),
                kind="redefine",
                params={"limit": limit, "min_salience": min_salience, "model": use_model},
                work=work,
                stage="define",
            )
        except run_launcher.LaneBusy as exc:
            print(f"{RED}{exc}{RESET}", file=sys.stderr)
            return 1

        print(f"{DIM}run {started['run_id']}{RESET}")
        handle = run_service.registry.handle(started["run_id"])
        if handle is not None and handle.task is not None:
            # A cancel re-raises out of the task it stopped, and a stop
            # somebody asked for is not a crash -- the settled row is the
            # answer. Same reason as `venona run`.
            with contextlib.suppress(asyncio.CancelledError):
                await handle.task
        outcome = _cli_outcome(conn, started["run_id"])

    print()
    print(f"  status      {outcome.status}")
    if outcome.outcome:
        print(f"  outcome     {_outcome_word('redefine', outcome.outcome)}")
    for key, value in sorted(outcome.counters.items()):
        print(f"  {key:<11} {value}")
    for message in outcome.warnings:
        # `get_run` hands these back as dicts, not objects.
        colour, label = _MESSAGE_STYLE.get(message.get("level"), (YELLOW, "warn"))
        print(f"  {colour}{label}{RESET}{' ' * max(1, 12 - len(label))}{message.get('text')}")
    if outcome.error:
        print(f"  {RED}error{RESET}       {outcome.error}", file=sys.stderr)
    return exit_code(outcome.status, outcome.outcome)


def _anchors_status() -> int:
    """How connected is the graph, actually.

    The number the whole anchoring exercise is judged on. Computed over the
    PROJECTED graph -- active endpoints only -- because that is what the
    explorer shows.
    """
    from collections import defaultdict

    from venona.graph.projector import resolve_mappings
    from venona.pipeline.anchors import projected_graph

    with _db() as conn:
        # Shared with the proposer, so the two can never disagree about which
        # nodes are floating.
        active, projectable = projected_graph(conn)

        mappings = resolve_mappings(conn)
        triples = set()
        for row in repo.select_assertions(conn):
            src = mappings.get(row["s"], row["s"])
            dst = mappings.get(row["o"], row["o"])
            if src != dst:
                triples.add((src, row["p"], dst))

        adjacency = defaultdict(set)
        for src, _, dst in projectable:
            adjacency[src].add(dst)
            adjacency[dst].add(src)

        seen, sizes = set(), []
        for node in active:
            if node in seen:
                continue
            stack, size = [node], 0
            seen.add(node)
            while stack:
                current = stack.pop()
                size += 1
                for neighbour in adjacency.get(current, ()):
                    if neighbour not in seen:
                        seen.add(neighbour)
                        stack.append(neighbour)
            sizes.append(size)
        sizes.sort(reverse=True)

        blocked = len(triples) - len(projectable)
        print(f"  active entities   {len(active)}")
        print(f"  components        {len(sizes)}")
        print(f"  isolated          {sum(1 for s in sizes if s == 1)}")
        print(f"  largest           {sizes[0] if sizes else 0}")
        print(f"  relationships     {len(projectable)} projected of {len(triples)} known")
        if blocked:
            print(
                f"  {YELLOW}blocked{RESET}           {blocked} "
                f"{DIM}(an endpoint is not active -- `venona research`){RESET}"
            )
    return 0


def _entity_merges() -> int:
    from venona.services.curate import list_merges

    with _db() as conn:
        rows = list_merges(conn, active_only=False)
    if not rows:
        print("no merges")
        return 0
    print()
    for r in rows:
        state = f"{GREEN}active{RESET}" if r["active"] else f"{DIM}undone{RESET}"
        print(f"  {state}  {r['from_name'] or r['from_id']} -> {r['to_name'] or r['to_id']}")
        print(f"      {DIM}{r['merge_id']} · {r['reason']} · {r['created_at']}{RESET}")
    return 0


def _runs_list(*, include_hidden: bool, limit: int) -> int:
    from venona.services import run_service

    with _db() as conn:
        items, total, hidden = run_service.list_runs(
            conn, limit=limit, include_hidden=include_hidden
        )
    if not items:
        print("no runs")
        return 0
    print()
    for r in items:
        mark = f"{DIM}hidden{RESET}  " if r["hidden_at"] else ""
        cost = f"${r['cost_usd']:.2f}" if r["cost_usd"] else "-"
        # Both verdicts, side by side, because that is the whole point of there
        # being two: `succeeded  not found` is a clean run with a negative
        # answer, and `succeeded_partial  defined` is a definition landed by a
        # run that had trouble on the way. One column could say neither.
        found = _outcome_word(r["kind"], r.get("outcome"))
        print(
            f"  {mark}{r['started_at'][:19]}  {r['status']:<18} {found:<16} {r['kind']:<10} {cost}"
        )
        print(f"      {DIM}{r['id']}{RESET}")
    print(f"\n  {DIM}{total} shown · {hidden} hidden{RESET}")
    return 0


def _runs_hide(run_id: str) -> int:
    from venona.pipeline.types import SystemClock
    from venona.services import run_service

    with _db() as conn:
        try:
            run_service.hide_run(conn, run_id, now=SystemClock().now())
        except LookupError:
            print(f"no run {run_id}")
            return 1
        except run_service.RunIsLive:
            print(f"run {run_id} is still executing")
            return 1
    print(f"hid {run_id}")
    return 0


def _runs_unhide(run_id: str) -> int:
    from venona.services import run_service

    with _db() as conn:
        run_service.unhide_run(conn, run_id)
    print(f"restored {run_id}")
    return 0


def _runs_clear() -> int:
    from venona.pipeline.types import SystemClock
    from venona.services import run_service

    with _db() as conn:
        result = run_service.hide_all(conn, now=SystemClock().now())
    # Say what was skipped. A count that silently omits the live run reads as a
    # bug the next time someone counts the rows themselves.
    skipped = f" · {result['skipped']} still executing" if result["skipped"] else ""
    print(f"hid {result['hidden']} run(s){skipped}")
    print(f"{DIM}nothing was deleted; `venona runs list --all` still shows them{RESET}")
    return 0


async def _research(entity_ids: list[str] | None, limit: int, dry_run: bool) -> int:
    """Chase corroboration for entities that are blocking relationships.

    Runs the whole loop -- sweep, ingest, analyze, promote, project -- and
    reports the graph delta, because "did it work?" is the only question this
    command exists to answer. Deferring analyze to the next run would leave the
    answer a day away and untestable.
    """
    from venona.graph.client import GraphClient
    from venona.graph.projector import project
    from venona.pipeline.analyze import run_analyze
    from venona.pipeline.research import coverage_outcome, run_research, stuck_entities
    from venona.services import run_service

    settings = get_settings()
    with _db() as conn:
        db.migrate(conn)

        if dry_run:
            targets = stuck_entities(conn, limit=limit, entity_ids=entity_ids)
            if not targets:
                print("nothing to research")
                return 0
            print()
            for t in targets:
                print(
                    f"  {t.blocked:>2} blocked  {t.name[:44]:<46} "
                    f"{DIM}{t.entity_type} · {t.articles} article(s), "
                    f"{t.publishers} publisher(s) · {t.status}{RESET}"
                )
            print()
            print(
                f"  {len(targets)} entities blocking "
                f"{sum(t.blocked for t in targets)} relationships"
            )
            print(f"  {DIM}no model calls made{RESET}")
            return 0

        # The lane claim comes first, before a driver or an HTTP client exists,
        # so a refusal costs nothing and leaves nothing open. Without it this
        # command starts a second research run on top of a live one -- two
        # writers over one `documents` table, each with its own budget.
        from venona.services import run_launcher

        graph = GraphClient(settings)
        before = await _entity_edge_count(graph)
        report = analyzed = None

        async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
            nonlocal report, analyzed
            ctx = target.ctx
            budget = ctx.budget
            # ONE STAGE, which is what the kind declares. The sweep analyses
            # what it brings back inside it, and `stage_progress` carries that
            # half -- a second declared stage would be a segment that never
            # lights.
            report = await run_research(ctx, entity_ids=entity_ids, limit=limit, budget=budget)
            if report.documents_new:
                analyzed = await run_analyze(ctx, budget=budget)
            await project(target.conn, graph.driver)
            return run_launcher.LaunchOutcome(
                outcome=coverage_outcome(
                    analyzed.promoted if analyzed else 0, degraded=ctx.degraded
                ),
                data={
                    "targeted": len(report.targeted),
                    "blocked": report.blocked_total,
                    "documents_new": report.documents_new,
                    "documents_duplicate": report.documents_duplicate,
                    **(
                        {
                            "clusters_analyzed": analyzed.clusters_analyzed,
                            "promoted": analyzed.promoted,
                        }
                        if analyzed
                        else {}
                    ),
                },
            )

        try:
            started = await run_launcher.launch(
                run_launcher.host(settings, llm=None, graph=graph),
                kind="research",
                params={"entities": entity_ids, "limit": limit},
                work=work,
                stage="research",
                context={"graph": graph},
            )
        except run_launcher.LaneBusy as exc:
            print(
                f"{RED}{exc}{RESET}\n{DIM}  stop it, or wait for it to finish{RESET}",
                file=sys.stderr,
            )
            await graph.close()
            return 1

        print(f"{DIM}run {started['run_id']}{RESET}")
        handle = run_service.registry.handle(started["run_id"])
        try:
            if handle is not None and handle.task is not None:
                # SUPPRESSED, because a cancel re-raises out of the task it
                # stopped. Ctrl-C or `venona runs cancel` is a stop somebody
                # asked for, and the row the launcher settled is the answer --
                # a traceback in its place would report the request as a crash.
                with contextlib.suppress(asyncio.CancelledError):
                    await handle.task
        finally:
            after = await _entity_edge_count(graph)
            await graph.close()
        outcome = _cli_outcome(conn, started["run_id"])
        if outcome.error:
            print(f"{RED}{outcome.error}{RESET}", file=sys.stderr)

    print()
    if report:
        print(
            f"  targeted    {len(report.targeted)} entities "
            f"(blocking {report.blocked_total} relationships)"
        )
        print(
            f"  documents   {report.documents_new} new, "
            f"{report.documents_duplicate} already had / unusable"
        )
    if analyzed:
        print(f"  analyzed    {analyzed.clusters_analyzed} clusters")
        print(f"  promoted    {analyzed.promoted} entities")
    delta = f"  (+{after - before})" if after > before else ""
    print(f"  graph       {before} -> {after} edges{delta}")
    print(f"  cost        ${float(outcome.counters.get('cost_usd') or 0):.3f}")
    if after == before and report and report.documents_new:
        print()
        print(
            f"  {DIM}no new edges: the coverage found did not corroborate any endpoint. "
            f"Promotion needs a DIFFERENT publisher.{RESET}"
        )
    return exit_code(outcome.status, outcome.outcome)


async def _entity_edge_count(graph) -> int:
    """Entity-to-entity edges only. `graph stats` counts MENTIONS too, which
    swamps the number this command is measuring."""
    async with graph.driver.session() as session:
        result = await session.run(
            "MATCH (a:Entity)-[r]->(b:Entity) WHERE a.status='active' AND b.status='active' "
            "RETURN count(r) AS n"
        )
        record = await result.single()
        return record["n"] if record else 0


def _conflicts(kind: str | None, show_all: bool, keep_apart: str | None) -> int:
    """Work the alias-conflict queue.

    The queue needs an exit: nothing else sets `resolved`, and by 2026-08-06 it
    held 79 rows with no way to act on one. Two actions close a row, and they are
    deliberately different commands --

        venona entity merge LOSER SURVIVOR    the pair was one thing
        venona conflicts --keep-apart ID      the pair is two things

    -- because only the first should change the graph. Merging closes the
    matching conflict automatically; this command exists for the other verdict.
    """
    from venona.pipeline.types import SystemClock
    from venona.services.curate import dismiss_conflict, list_conflicts

    with _db() as conn:
        if keep_apart:
            dismiss_conflict(conn, keep_apart, now=SystemClock().now())
            print(f"{GREEN}kept apart{RESET} {keep_apart}")
            return 0
        rows = list_conflicts(conn, kind=kind, include_resolved=show_all)
    if not rows:
        print("no open conflicts")
        return 0

    print()
    for r in rows:
        state = f"{DIM}{r['resolution']}{RESET}" if r["resolved"] else f"{r['kind'] or '?'}"
        existing = r["existing_name"] or r["existing_entity_id"]
        proposed = r["proposed_name"] or r["proposed_entity_id"]
        print(f"  {state}  {r['surface']!r}")
        print(f"      {existing} ({r['existing_entity_id']})")
        print(f"      {proposed} ({r['proposed_entity_id']})")
        if r.get("suggestion"):
            # Name the child. `part_of` is a direction and the pair prints in
            # filed order, which is the opposite of the answer often enough that
            # the line reads as the reverse of what the model actually said.
            reading = r["suggestion"]
            child = r.get("suggestion_child")
            if reading == "part_of" and child:
                if child == r["existing_entity_id"]:
                    inner, outer = existing, proposed
                else:
                    inner, outer = proposed, existing
                reading = f"part_of: {inner} inside {outer}"
            confidence = r.get("suggestion_confidence")
            said = f"model says {reading}" + (f" ({confidence})" if confidence else "")
            print(f"      {DIM}{said}{RESET}")
        print(f"      {DIM}{r['id']}{RESET}")
    print()
    print(f"  {len(rows)} conflict(s)")
    return 0


def _access_list(show_all: bool, limit: int) -> int:
    """Who has asked for an account.

    Prints the coverage note in full rather than truncating it: it is the only
    thing distinguishing a real analyst from a scraper, it is the single reason
    the field is on the form, and a decision made against the first forty
    characters of it is a decision made against nothing.
    """
    with _db() as conn:
        rows = access_repo.listing(conn, pending_only=not show_all, limit=limit)
    if not rows:
        print(f"{DIM}no {'' if show_all else 'pending '}access requests{RESET}")
        return 0

    for row in rows:
        state = row["status"]
        tint = {"pending": YELLOW, "approved": GREEN, "declined": DIM}.get(state, "")
        meta = f"{row['role']} · {row['created_at']}"
        print(f"{tint}{state:<9}{RESET} {row['email']}  {DIM}{meta}{RESET}")
        if row["coverage"]:
            for line in row["coverage"].splitlines():
                print(f"          {line}")
        if row["note"]:
            print(f"          {DIM}note: {row['note']} ({row['decided_at']}){RESET}")
        print()

    pending = sum(1 for row in rows if row["status"] == "pending")
    if pending:
        print(
            f"{DIM}Approving is two commands, and they stay two on purpose: "
            f"`venona users add <email> --role reader` creates the account, then "
            f"`venona access decide <email> approved` records the decision. "
            f"Neither needs a restart.{RESET}"
        )
    return 0


def _access_decide(email: str, verdict: str, note: str) -> int:
    """Record a decision. Deliberately does NOT create the user.

    Approving a request and creating an account are two actions, and keeping them
    apart is what makes the public form safe: nothing in the request path can
    grant anything. `venona users add` is the other half.
    """
    with _db() as conn:
        with db.transaction(conn):
            # `actor=None`: the CLI decided. Same convention as every other actor
            # column in the schema (C1), and honest -- shell access is not a
            # `users` row and inventing one would be inventing provenance.
            changed = access_repo.settle_by_email(
                conn,
                email=email,
                status=verdict,
                now=utc_now_iso(),
                note=note or None,
                actor=None,
            )
    if not changed:
        print(f"{RED}no pending request for {email}{RESET}", file=sys.stderr)
        return 1
    print(f"{_mark(True)} recorded {verdict} for {email} ({changed} request(s))")
    if verdict == "approved":
        print(
            f"{DIM}This records the decision and nothing else. If they do not have "
            f"an account yet, `venona users add {email} --role reader` creates one "
            f"and takes effect on their next sign-in -- no restart.{RESET}"
        )
    return 0


def _alias_audit(retract: str | None) -> int:
    """List fuzzy-attached aliases for review, and retract one on request.

    Fuzzy matching scores a token SUBSET at exactly 100, which by 2026-08-06 had
    silently merged Amazon into Amazon Web Services, General Dynamics into GDIT
    and Thales into Thales Australia -- on ACTIVE entities already annotating
    briefs. The containment guard in `resolve.py` stops new ones; this finds the
    existing.

    The `?` marker is a HINT, not a verdict. The guard is a good rule for
    deciding a fresh match and a poor classifier applied backwards: it flags
    "Northrop Grumman" -> "Northrop" (same company) and misses
    "Joint Interagency Task Force 401" -> "...South" (different organisations,
    a token substitution rather than a containment). A human reads the list.
    """
    from venona.pipeline.acronyms import load_non_distinguishing
    from venona.pipeline.resolve import _is_qualified_containment

    settings = get_settings()
    with _db() as conn:
        non_distinguishing = load_non_distinguishing(settings)

        if retract:
            row = repo.select_aliases_by_id(conn, retract).fetchone()
            if row is None:
                print(f"{RED}no alias {retract!r}{RESET}", file=sys.stderr)
                return 1
            repo.delete_aliases_by_id(conn, retract)
            conn.commit()
            print(f"{GREEN}retracted{RESET} {row['surface']!r} -> {row['name']!r}")
            print(f"{DIM}run `venona reannotate` to repair existing briefs{RESET}")
            return 0

        rows = repo.select_aliases(conn).fetchall()

        if not rows:
            print("no fuzzy-attached aliases")
            return 0

        print()
        flagged = 0
        for row in rows:
            suspect = _is_qualified_containment(row["surface"], row["name"], non_distinguishing)
            flagged += suspect
            mark = f"{YELLOW}?{RESET}" if suspect else " "
            print(f"  {mark} {row['status']:11} {row['surface'][:40]:42} -> {row['name'][:34]}")
            print(f"      {DIM}{row['id']}{RESET}")

        print()
        print(f"  {len(rows)} fuzzy aliases, {flagged} flagged for a look")
        print(f"  {DIM}retract with: venona alias audit --retract <id>{RESET}")
        return 0


def _entity_show(entity_id: str) -> int:
    """Inspect one entity: aliases, assertions, neighbours."""
    with _db() as conn:
        row = repo.select_entities_by_id(conn, entity_id).fetchone()
        if row is None:
            print(f"no entity {entity_id}", file=sys.stderr)
            return 1

        print(f"{row['name']}  {DIM}({row['type']}, {row['status']}){RESET}")
        if row["summary"]:
            print(f"  {row['summary'][:200]}")
        print(f"  mentions {row['mention_count']} across {row['source_count']} sources")

        print(f"\n  {DIM}aliases{RESET}")
        for alias in repo.select_aliases_by_entity_id(conn, entity_id):
            print(
                f"    {alias['surface']:44} s={alias['strictness']} {DIM}{alias['added_by']}{RESET}"
            )

        print(f"\n  {DIM}relations{RESET}")
        for rel in repo.select_assertions_by_subject_id_raw_object_id_raw(
            conn, entity_id, entity_id
        ):
            arrow = "-->" if rel["s"] == entity_id else "<--"
            other = rel["o"] if rel["s"] == entity_id else rel["s"]
            print(f"    {arrow} {rel['predicate']:14} {other:44} {DIM}x{rel['n']}{RESET}")
        return 0


async def _brief(date: str | None) -> int:
    """Generate today's brief from analyzed clusters."""
    from venona.pipeline.brief import run_brief
    from venona.pipeline.types import build_context

    settings = get_settings()
    with _db() as conn:
        db.migrate(conn)
        llm = build_executor(settings)
        ctx = build_context(run_id="run:brief", db=conn, llm=llm, settings=settings)

        report = await run_brief(ctx, date=date)

    if report.error:
        print(f"{RED}{report.error}{RESET}", file=sys.stderr)
        return EXIT_FAILED
    if report.empty_reason:
        # Ran cleanly, nothing to write. Without this branch the reason lives in
        # a field nothing prints, and a quiet window reports `brief None` and
        # exits 0 -- the one exit code a script must not get here.
        print(f"{DIM}{report.empty_reason}{RESET}")
        return EXIT_NOTHING_FOUND

    print()
    print(f"  brief       {report.brief_id}")
    print(
        f"  clusters    {report.clusters_used}"
        + (f" {YELLOW}(quiet day){RESET}" if report.quiet_day else "")
    )
    print(f"  blocks      {report.blocks}")
    print(f"  spans       {report.spans} annotations, {report.entities_linked} entities linked")
    if report.unresolved:
        print(f"  unresolved  {', '.join(report.unresolved[:6])}")
    print(f"  cost        ${report.cost_usd:.3f}")
    return 0


def _cli_outcome(conn, run_id: str):
    """The settled run row, in the shape the terminal summary prints."""
    from types import SimpleNamespace

    from venona.services import run_service

    row = run_service.get_run(conn, run_id) or {}
    return SimpleNamespace(
        status=row.get("status") or "unknown",
        outcome=row.get("outcome"),
        counters=row.get("counters") or {},
        warnings=row.get("warnings") or [],
        error=row.get("error"),
        brief_id=row.get("brief_id"),
    )


async def _run(
    only: list[str] | None,
    limit: int | None,
    analyze_limit: int | None,
    record: bool,
    skip_brief: bool = False,
    lookback_days: int | None = None,
    max_calls: int | None = None,
    go_looking: bool = False,
) -> int:
    """The full pipeline, the same `execute_run` the API and scheduler drive.

    The one thing this must do that the stage-specific subcommands above do not:
    **pre-flight the active-run check against the database.** `RunRegistry` is
    in-process, so it cannot see a run started by `make dev`'s API. Without this,
    `venona run` in one terminal happily races the UI's run in another -- two
    writers to `documents`/`clusters` with two separate budgets.
    """
    from venona.llm.prompts import load_profile
    from venona.pipeline.orchestrator import STAGES, execute_run
    from venona.services import run_service, run_types

    if only:
        unknown = [s for s in only if s not in STAGES]
        if unknown:
            print(
                f"{RED}unknown stage(s): {', '.join(unknown)}{RESET}\n  known: {', '.join(STAGES)}",
                file=sys.stderr,
            )
            return 2

    # `VENONA_LLM_RECORD` is read by `build_executor`, so honour the HTTP twin
    # here too -- `make record` sets both and neither half is useful alone.
    record = record or os.environ.get("VENONA_HTTP_RECORD") == "1"

    settings = get_settings()
    with _db() as conn:
        db.migrate(conn)

        # Scoped to the RESEARCH lane (contract C11). An unqualified `WHERE
        # status = 'running'` would let a background acronym lookup -- a light
        # run writing a single definition row -- refuse `venona run` from a
        # terminal.
        active = run_service.lane_conflict(conn, lane=run_types.RESEARCH)
        if active:
            print(
                f"{RED}run {active['id']} is already active{RESET}\n"
                f"{DIM}  stop it, or wait for it to finish{RESET}",
                file=sys.stderr,
            )
            return 1

        # `backfill` when no brief is wanted, so the row carries the kind whose
        # `outcomes` and `lines` actually describe what happened. `cli` spells
        # `delivered` as "brief written".
        kind = "backfill" if skip_brief else "cli"
        # THE SAME TWO MODES THE SCREEN OFFERS, so the terminal and the button
        # mean the same thing. Reading what is already stored is the default and
        # searching the web is the switch, because a sweep costs about a dollar a
        # call and the same whether it finds anything. An explicit `--only` wins:
        # somebody naming stages has already said what they want.
        if skip_brief and only is None:
            only = list(STAGES) if go_looking else [s for s in STAGES if s != "fetch"]
        params = {
            "only": only,
            "limit_per_source": limit,
            "analyze_limit": analyze_limit,
            "window_text": (
                f"the last {lookback_days} days" if lookback_days else "everything waiting"
            ),
        }
        # THROUGH `run_launcher`, like every other run. A lifecycle of its own
        # here emits no stage events, no counters and no notice, so nothing
        # outside the terminal can see a `venona run` at all.
        from venona.services import run_launcher

        graph = GraphClient(settings)

        async def work(target: run_launcher.LaunchContext) -> run_launcher.LaunchOutcome:
            result = await execute_run(
                scope=target.scope,
                run_id=target.run_id,
                settings=settings,
                conn=target.conn,
                graph=graph,
                kind=kind,
                limit_per_source=limit,
                analyze_limit=analyze_limit,
                only=only,
                skip_brief=skip_brief,
                lookback_hours=lookback_days * 24 if lookback_days else None,
                max_calls=max_calls,
            )
            return run_launcher.LaunchOutcome(
                status=result.status,
                outcome=result.outcome,
                brief_id=result.brief_id,
                error=result.error,
            )

        try:
            started = await run_launcher.launch(
                run_launcher.host(settings, llm=None, graph=graph),
                kind=kind,
                params=params,
                work=work,
                profile_hash=load_profile(settings).short_hash,
                record=record,
                context={"graph": graph, "profile": load_profile(settings)},
            )
        except run_launcher.LaneBusy as exc:
            print(
                f"{RED}{exc}{RESET}\n{DIM}  stop it, or wait for it to finish{RESET}",
                file=sys.stderr,
            )
            await graph.close()
            return 1

        print(f"{DIM}run {started['run_id']}{RESET}")

        from venona.services import run_service

        handle = run_service.registry.handle(started["run_id"])
        try:
            if handle is not None and handle.task is not None:
                # SUPPRESSED, because a cancel re-raises out of the task it
                # stopped. Ctrl-C or `venona runs cancel` is a stop somebody
                # asked for, and the row the launcher settled is the answer --
                # a traceback in its place would report the request as a crash.
                with contextlib.suppress(asyncio.CancelledError):
                    await handle.task
        finally:
            await graph.close()
        outcome = _cli_outcome(conn, started["run_id"])

    print()
    print(f"  status      {outcome.status}")
    if outcome.outcome:
        print(f"  outcome     {_outcome_word('cli', outcome.outcome)}")
    for key, value in sorted(outcome.counters.items()):
        print(f"  {key:<11} {value}")
    if outcome.brief_id:
        print(f"  brief       {outcome.brief_id}")
    # At the message's own level. Printing every one as `warn` is the terminal
    # version of the bug this split fixes: a note read as a degradation.
    for message in outcome.warnings:
        # `get_run` hands these back as dicts, not objects.
        colour, label = _MESSAGE_STYLE.get(message.get("level"), (YELLOW, "warn"))
        print(f"  {colour}{label}{RESET}{' ' * max(1, 12 - len(label))}{message.get('text')}")
    if outcome.error:
        print(f"  {RED}error{RESET}       {outcome.error}", file=sys.stderr)

    return exit_code(outcome.status, outcome.outcome)


def _rescore() -> int:
    """Reweight every stored cluster. Zero model calls."""
    from venona.services.rescore import rescore_all

    with _db() as conn:
        report = rescore_all(conn)
    print(
        f"  rescored    {report.clusters} clusters, {report.changed} scores changed, "
        f"{report.tier_changes} tier changes"
    )
    if report.missing_axes:
        print(
            f"  {YELLOW}note{RESET}        {report.missing_axes} clusters predate a rubric axis "
            f"(scored 0 for it; re-analyse to fill in)"
        )
    print(
        f"  {DIM}weights: "
        + ", ".join(f"{k}={v}" for k, v in (report.weights or {}).items())
        + RESET
    )
    print(f"  {DIM}zero model calls{RESET}")
    return 0


def _reannotate(brief_id: str | None) -> int:
    """Re-resolve brief spans against the current alias index. Zero model calls."""
    from venona.pipeline.brief import reannotate
    from venona.services.brief_service import latest_brief_id

    with _db() as conn:
        target = brief_id or latest_brief_id(conn)
        if not target:
            print("no briefs", file=sys.stderr)
            return 1
        counts = reannotate(conn, target)
    print(
        f"  {target}: {counts['blocks']} blocks, "
        f"{counts['spans_before']} -> {counts['spans_after']} spans"
    )
    print(f"  {DIM}zero model calls{RESET}")
    return 0


def _briefs_list(limit: int) -> int:
    from venona.services.brief_service import list_briefs

    with _db() as conn:
        items, total = list_briefs(conn, limit=limit)
    if not items:
        print("no briefs")
        return 0
    print()
    for b in items:
        stamp = (b.generated_at or "")[11:16] or "--:--"
        print(f"  {b.date} {stamp}  {b.title[:88]}")
        print(f"      {DIM}{b.id}  ({b.run_id or 'no run'}){RESET}")
    print(f"\n  {DIM}{len(items)} shown · {total} total{RESET}")
    return 0


def _briefs_delete(brief_id: str) -> int:
    """Unlike `venona runs`, this really deletes.

    `runs hide` exists because deleting a run orphans the `documents`,
    `sightings` and `assertions` rows that point at it. A brief owns its
    children outright, and the POINT of deleting one is the side effect: it
    releases that brief's `brief_clusters` rows so the stories become eligible
    again. A soft hide would leave them burned forever, which is exactly what
    breaks the `--only brief` tuning loop.
    """
    from venona.services.brief_service import delete_brief

    with _db() as conn:
        try:
            counts = delete_brief(conn, brief_id)
        except LookupError:
            print(f"no brief {brief_id}", file=sys.stderr)
            return 1

    print(f"deleted {brief_id}")
    print(
        f"  {DIM}{counts['brief_items']} items, {counts['brief_blocks']} blocks, "
        f"{counts['brief_clusters']} clusters released{RESET}"
    )
    return 0


def _dump_schema() -> int:
    migrations = Path(__file__).parent / "db" / "migrations"
    for path in sorted(migrations.glob("*.sql")):
        sys.stdout.write(path.read_text())
    return 0


def _dump_openapi() -> int:
    """The OpenAPI document, built from the app object.

    Built from the app object rather than curled from a running server: that
    would need `make dev` up and a session besides. `app.openapi()` does not
    execute `lifespan`, so this works with no server, no Neo4j and no database,
    which is what `make types` and `make lint-generated` need of it.
    """
    import json

    from venona.main import app

    sys.stdout.write(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n")
    return 0


def _auth(action: str) -> int:
    """Generate auth material. PRINTS, never writes.

    Writing `.env` from here would put a second writer next to
    `config_service`'s "only write path" rule, and a command that silently
    overwrites a secrets file is one typo away from locking the operator out of
    their own machine. Copy the line yourself.
    """
    from venona.api import security

    match action:
        case "new-secret":
            print(security.new_secret())
        case "token":
            print(security.new_token())
        case "mgmt-token":
            print(security.mint_mgmt_token())
    return 0


def _config(command: str, args) -> int:
    """Where this instance's settings live, and whether they are current.

    `path` exists because nothing else prints it, and an instance where the file
    the app reads and the file the config rail writes are different files looks
    exactly like one where they are not.
    """
    import yaml

    from venona.services import config_migrations
    from venona.settings import Paths, Settings, settings_path

    path = settings_path()
    match command:
        case "path":
            print(path)
            return 0
        case "materialize":
            # THE STEP THAT REPLACES THE ENTRYPOINT'S `cp -rn` (ADR 0234), and
            # it runs before `config migrate`, which edits a document it writes.
            # The schema first, because `config_documents` does not exist until
            # the migration has run.
            from venona.db import core as db
            from venona.services import config_service
            from venona.settings import database_dsn

            conn = db.connect(database_dsn())
            try:
                db.migrate(conn)
                written = config_service.materialize(conn, Paths())
            finally:
                conn.close()
            print(f"materialized {len(written)} document(s): {', '.join(written) or 'none'}")
            return 0
        case "migrate":
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            due = config_migrations.pending(text)
            if not due:
                print(f"settings up to date (version {config_migrations.HEAD}): {path}")
                return 0
            if args.dry_run:
                updated, _ = config_migrations.apply_ops(text, [op for m in due for op in m.ops])
                updated = config_migrations.ct.stamp_version(updated, config_migrations.HEAD)
                print(
                    "".join(
                        difflib.unified_diff(
                            text.splitlines(keepends=True),
                            updated.splitlines(keepends=True),
                            fromfile=str(path),
                            tofile=f"{path} (after {[m.version for m in due]})",
                        )
                    )
                )
                return 0
            applied = config_migrations.migrate(Paths())
            print(f"applied {applied}, now at version {config_migrations.HEAD}: {path}")
            return 0
        case "diff":
            # What this instance has decided, as against a stock one. The
            # pre-deploy read: every line here is a value that starts mattering
            # the moment the file is the one being read.
            parsed = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
            stock = Settings()
            rows = []
            for group, body in (parsed or {}).items():
                if not isinstance(body, dict):
                    continue
                for key, value in body.items():
                    default = getattr(getattr(stock, group, None), key, None)
                    if value != default:
                        rows.append(f"  {group}.{key}: {value!r} (stock {default!r})")
            print(f"{path}\n" + ("\n".join(rows) if rows else "  nothing set away from stock"))
            return 0
        case "doctor":
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            print(f"path    {path}")
            print(f"exists  {path.exists()}")
            print(f"version {config_migrations.ct.read_version(text)} of {config_migrations.HEAD}")
            print(f"pending {[m.version for m in config_migrations.pending(text)]}")
            return 0
    return 1


def _instance_seed() -> int:
    """First-boot seeding for a non-defense production instance.

    The entrypoint calls this instead of `cp -rn`-ing `/app/defaults` -- those
    defaults are the DEFENSE document set, and a new domain that boots with
    them impersonates it and never gates into `/setup`. Write-if-missing, so
    a restart never touches an applied document.
    """
    from venona.services import instance_skeleton
    from venona.settings import REPO_ROOT, get_settings

    settings = get_settings()
    defaults_settings = REPO_ROOT / "defaults" / "config" / "settings.yaml"
    if not defaults_settings.exists():
        # Outside the image (no /app/defaults): the repo's own settings.yaml
        # carries the same domain-neutral knobs.
        defaults_settings = REPO_ROOT / "config" / "settings.yaml"
    written = instance_skeleton.seed(
        settings.config_dir,
        settings.profile_dir,
        skeleton_dir=settings.fixtures_dir / "skeleton",
        settings_src=defaults_settings,
    )
    if written:
        print(f"seeded {len(written)} blank document(s); the wizard's drafts replace them")
    else:
        print("all documents present; nothing seeded")
    return 0


def _users(action: str, args: argparse.Namespace) -> int:
    """Who may sign in, and what they may do. Contract C12.

    CLI-ONLY, and that is the same argument `venona mcp token new` makes: an HTTP
    endpoint that grants a role is reachable by anything holding a session, so the
    first admin would be minted by whoever got there first. Over the CLI, granting
    admin requires shell access to the box -- which is the credential that already
    implies everything else.

    `access_requests` is deliberately NOT touched here. Approving a request and
    creating a user are two actions, and keeping them apart is what makes the
    public form safe: nothing in the request path can grant anything.
    """
    import time

    from venona.api import authz
    from venona.db.repositories import users as repo_users
    from venona.pipeline.types import SystemIdGen, utc_now_iso
    from venona.services import user_service

    with _db() as conn:
        match action:
            case "ls":
                rows = user_service.listing(conn)
                if not rows:
                    print(
                        "no users. The API creates them from "
                        "VENONA_AUTH_ALLOWED_EMAILS on first boot, or add one "
                        "with `venona users add`."
                    )
                    return 0
                # The subscription column is here so `venona users comp` has a
                # way to be checked: a comp is invisible everywhere else on this
                # side, and an operator who cannot see one cannot withdraw it.
                print(
                    f"{'EMAIL':<34}  {'ROLE':<7}  {'STATUS':<10}  {'SUBSCRIPTION':<13}  LAST SEEN"
                )
                for r in rows:
                    print(
                        f"{r['email'][:32]:<34}  {r['role']:<7}  {r['status']:<10}  "
                        f"{r['entitlement_status']:<13}  {r['last_seen_at'] or '-'}"
                    )
            case "add":
                existing = user_service.lookup(conn, args.email)
                if existing is not None:
                    print(
                        f"{existing.email} already exists ({existing.role}). "
                        f"Use `venona users role` to change it.",
                        file=sys.stderr,
                    )
                    return 1
                user = user_service.create(
                    conn,
                    user_id=f"user:{SystemIdGen().uuid7()}",
                    email=args.email,
                    role=args.role,
                    now=utc_now_iso(),
                    # THE SHELL EXCEPTION, and the only one. C12 puts the admin
                    # ceiling on everything reachable over a wire; this process
                    # IS the shell access that ceiling defers to (ADR 0168).
                    allow_admin=True,
                )
                print(f"{user.email} added as {user.role}")
                if user.role == authz.READER:
                    print()
                    print("A reader can read the brief, the graph, the glossary and")
                    print("search. Runs, curation and config are yours.")
            case "comp" | "uncomp":
                user = user_service.lookup(conn, args.email)
                if user is None:
                    print(f"no user with address {args.email!r}", file=sys.stderr)
                    return 1

                # CLI-ONLY for the same reason as `role`: this grants the paid
                # product for nothing, so it needs the credential that already
                # implies everything else. The hub cannot do it -- it bills, and
                # has no word for a reader who pays nothing.
                with db.transaction(conn, what="set_comped"):
                    repo_users.set_comped(conn, user_id=user.id, comped=action == "comp")
                if action == "comp":
                    print(f"{user.email} reads everything and is billed nothing")
                else:
                    print(f"{user.email} no longer reads for free")
                    # Worth saying, because the alternative people expect is an
                    # immediate lockout.
                    print("Their next sign-in picks up whatever they have paid for.")
            case "role" | "suspend" | "reinstate":
                user = user_service.lookup(conn, args.email)
                if user is None:
                    print(f"no user with address {args.email!r}", file=sys.stderr)
                    return 1

                # THROUGH THE SAME DOOR THE ADMIN PAGE USES. The last-admin rule
                # and the audit trail are properties of the change rather than of
                # the interface, and a check that lived in the router would be a
                # check this process does not run.
                #
                # `actor=None` -- the CLI decided, which is the same honest NULL
                # every other actor column carries (C1). `force=True` -- shell
                # access is the higher tier, and it is the whole argument for
                # keeping admin-granting here; without it this command could no
                # longer suspend a compromised SOLE admin, which is the one
                # moment it has to work. No route can set it.
                try:
                    # In a transaction because `change_authority` takes
                    # `pg_advisory_xact_lock`, which is released at COMMIT.
                    with db.transaction(conn, what="change_authority"):
                        changed = user_service.change_authority(
                            conn,
                            user_id=user.id,
                            role=args.role if action == "role" else None,
                            status=(
                                None
                                if action == "role"
                                else user_service.SUSPENDED
                                if action == "suspend"
                                else user_service.ACTIVE
                            ),
                            actor=None,
                            # See `users add` above: the shell exception.
                            allow_admin=True,
                            event_id=f"uevent:{SystemIdGen().uuid7()}",
                            now=utc_now_iso(),
                            epoch=time.time(),
                            force=True,
                        )
                except user_service.AuthorityError as exc:
                    print(f"{RED}{exc}{RESET}", file=sys.stderr)
                    return 1

                if action == "role":
                    print(f"{changed.email} is now {changed.role}")
                else:
                    print(f"{changed.email} is now {changed.status}")
                    if action == "suspend":
                        # Worth saying, because the alternative people expect is
                        # a fourteen-day wait for a cookie to expire.
                        print("Their sessions stop working on their next request.")
                    if repo_users.active_admin_count(conn) == 0:
                        # `force` let this through; saying so is the difference
                        # between a deliberate act and a discovery.
                        print()
                        print(
                            f"{YELLOW}This instance now has no active administrator. "
                            f"`venona users role --email <email> --role admin` "
                            f"is the way back."
                            f"{RESET}"
                        )
    return 0


def _role_names() -> frozenset[str]:
    """The roles `--role` accepts, from `api/authz.py` rather than a literal."""
    from venona.api import authz

    return authz.ROLES


def _token_scopes() -> frozenset[str]:
    """The scope names `--scope` accepts, from `api/authz.py` rather than a literal.

    Imported inside the function so `--help` does not pull the API package in on a
    command that has nothing to do with it -- the same reason every other command
    here imports lazily.
    """
    from venona.api import authz

    return frozenset(authz.SCOPES)


def _iso_days_from(now_iso: str, days: int) -> str:
    from datetime import timedelta

    from venona import timeutil

    moment = timeutil.parse(now_iso)
    if moment is None:  # pragma: no cover - `now` is ours
        raise ValueError(f"unparseable timestamp: {now_iso!r}")
    return timeutil.format_seconds(moment + timedelta(days=days))


def _resolve_token_owner(conn, email: str | None):
    """Whose token this is. An agent credential inherits its owner's role.

    REFUSES TO GUESS WHEN GUESSING COULD BE WRONG. With one admin the answer is
    unambiguous and asking would be ceremony; with several, picking one silently
    would hand an agent a role its operator did not choose -- and on a shared
    instance that is the difference between a read-only assistant and one whose
    token opens every admin route.

    Raises `SystemExit` with a message naming the candidates, because this is a
    CLI and a stack trace is not an answer.
    """
    from venona.api import authz
    from venona.services import user_service

    if email:
        owner = user_service.lookup(conn, email)
        if owner is None:
            raise SystemExit(f"no user with address {email!r}; `venona users ls` lists them")
        if not owner.active:
            raise SystemExit(f"{owner.email} is suspended; reinstate before minting for them")
        return owner

    admins = [u for u in user_service.listing(conn) if u["role"] == authz.ADMIN]
    if not admins:
        raise SystemExit(
            "no admin users exist yet. Start the API once so the allow-list in "
            "VENONA_AUTH_ALLOWED_EMAILS is carried into `users`, or add one with "
            "`venona users add --email you@example.com --role admin`."
        )
    if len(admins) > 1:
        listed = ", ".join(u["email"] for u in admins)
        raise SystemExit(
            f"several admins exist ({listed}); name the owner with --email. "
            f"An agent token inherits its owner's role, so which one is not a detail."
        )
    owner = user_service.lookup(conn, admins[0]["email"])
    assert owner is not None  # noqa: S101 - just listed
    return owner


def _mcp_token(action: str, args: argparse.Namespace) -> int:
    """Agent credentials for the MCP server. Like `_auth`, PRINTS and never writes.

    NO ENDPOINT MINTS ONE FROM A CREDENTIAL ALONE, and that is contract C12
    rather than an omission: a compromised credential could otherwise
    manufacture a PERSISTENT one that outlives rotation of the credential that
    made it -- an escalation in duration even though not in scope. Same argument
    as `api/security.py`'s env-only auth material and the Rotate button
    `web/src/views/AccountView.vue` refuses. The OAuth flow mints the same
    `mcp_tokens` row over HTTP (C12's OAuth addendum) because a human signs in
    and consents first; this command is the path for an agent that cannot.
    """
    from venona.api.security import get_auth_config
    from venona.pipeline.types import SystemIdGen, utc_now_iso
    from venona.services import token_service

    with _db() as conn:
        match action:
            case "new":
                now = utc_now_iso()
                owner = _resolve_token_owner(conn, getattr(args, "email", None))
                expires_at = None
                if getattr(args, "expires_days", None):
                    expires_at = _iso_days_from(now, args.expires_days)
                row, secret = token_service.mint(
                    conn,
                    label=args.label,
                    token_id=f"mcp_{SystemIdGen().uuid7()}",
                    now=now,
                    user_id=owner.id,
                    scope=getattr(args, "scope", "mcp") or "mcp",
                    expires_at=expires_at,
                )
                cfg = get_auth_config()
                base = cfg.public_url.rstrip("/")
                print(secret)
                print()
                print(f"  id     {row.id}")
                print(f"  label  {row.label}")
                print(f"  owner  {owner.email} ({owner.role})")
                if expires_at:
                    print(f"  expires {expires_at}")
                print()
                print("This is the only time it is shown -- venona stores a hash, not the")
                print("token. Paste it into a client now:")
                print()
                print(f"  claude mcp add --transport http venona {base}/api/mcp \\")
                print(f'    --header "Authorization: Bearer {secret}"')
                print()
                print("It reaches /api/mcp and nothing else, and every tool there is a read.")
                print()
                # Said HERE because this is where somebody is standing when they
                # reach for the harder path. A client given the address alone
                # runs the sign-in flow and ends up with the same kind of
                # credential without anyone carrying a secret through a
                # clipboard, so this command is for the two cases that flow
                # cannot serve: an agent with no browser, and the day signing in
                # is itself down.
                print("Most clients do not need this. Adding the address alone lets a")
                print("person sign in from the browser, which is safer and expires on its")
                print("own; keep this one for an agent that cannot open a browser.")
            case "ls":
                rows = token_service.listing(conn, include_revoked=args.all)
                if not rows:
                    print('no agent tokens. `venona mcp token new --label "..."` makes one.')
                    return 0
                print(f"{'ID':<42}  {'LABEL':<28}  {'PREFIX':<12}  {'LAST USED':<22}  STATE")
                for r in rows:
                    state = "revoked" if r.revoked_at else "active"
                    print(
                        f"{r.id:<42}  {r.label[:28]:<28}  {r.prefix:<12}  "
                        f"{(r.last_used_at or 'never'):<22}  {state}"
                    )
            case "revoke":
                if token_service.revoke(conn, args.token_id, now=utc_now_iso()):
                    print(f"revoked {args.token_id}; it stops working on the next call")
                else:
                    print(f"no active token {args.token_id}")
                    return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    # The CLI is where a run actually executes -- `venona run` is a separate
    # process from the API, and the scheduler's own path goes through the API.
    # Without this call, everything a run logs falls back to the root logger's
    # defaults: no request/run id, no JSON when piped. 2026-08-09: the
    # projector's rejected-edge warning came out unformatted on a live run.
    venona_logging.configure()

    parser = argparse.ArgumentParser(prog="venona")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health", help="check Neo4j and the claude CLI")
    sub.add_parser("migrate", help="apply pending database migrations")
    sub.add_parser("seed", help="load fixtures and project to Neo4j")
    sub.add_parser("dump-schema", help="print the SQL schema (generates docs/schema.sql)")
    sub.add_parser("dump-openapi", help="print the OpenAPI document (feeds `make types`)")

    auth_parser = sub.add_parser("auth", help="generate auth material (prints; writes nothing)")
    auth_sub = auth_parser.add_subparsers(dest="auth_command", required=True)
    auth_sub.add_parser("new-secret", help="a VENONA_AUTH_SESSION_SECRET")
    auth_sub.add_parser("token", help="a VENONA_AUTH_API_TOKEN")
    auth_sub.add_parser("mgmt-token", help="a VENONA_MGMT_TOKEN (the hub's leashed credential)")

    config_parser = sub.add_parser("config", help="this instance's settings file")
    config_sub = config_parser.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("path", help="which settings.yaml this instance reads")
    config_migrate = config_sub.add_parser("migrate", help="bring settings.yaml up to date")
    config_migrate.add_argument(
        "--dry-run", action="store_true", help="print the change without writing it"
    )
    config_sub.add_parser("diff", help="what this instance has set away from stock")
    config_sub.add_parser("doctor", help="path, version and what is pending")
    config_sub.add_parser("materialize", help="write the authored documents out of the database")

    instance_parser = sub.add_parser("instance", help="instance provisioning")
    instance_sub = instance_parser.add_subparsers(dest="instance_command", required=True)
    instance_sub.add_parser("seed", help="write any missing blank documents (first boot)")

    sub.add_parser("verify-sources", help="probe every declared feed")

    sources_parser = sub.add_parser("sources", help="strike counter: inspect and clear")
    sources_parser.add_argument("--enable", metavar="ID", help="clear strikes and re-enable")

    fetch_parser = sub.add_parser("fetch", help="run the fetch stage")
    fetch_parser.add_argument("--limit", type=int, help="max items per source")
    fetch_parser.add_argument("--dry", action="store_true")

    full_parser = sub.add_parser("run", help="the full pipeline (fetch -> analyze -> brief)")
    full_parser.add_argument(
        "--only",
        action="append",
        metavar="STAGE",
        help="run only this stage; repeatable. Stages read state, not run id, "
        "so `--only brief` re-briefs what is already analyzed.",
    )
    full_parser.add_argument("--limit", type=int, help="max items per source")
    full_parser.add_argument("--analyze-limit", type=int, help="max clusters to analyze")
    full_parser.add_argument(
        "--skip-brief",
        action="store_true",
        help="read for the graph and write no brief. Records the run as a deep read, "
        "and is what `--lookback-days` requires.",
    )
    full_parser.add_argument(
        "--lookback-days",
        type=int,
        help="how far back to read. Only with --skip-brief: a brief may not "
        "retrieve over a window this wide (ADR 0228).",
    )
    full_parser.add_argument(
        "--max-calls", type=int, help="model calls this run may make, overriding the configured cap"
    )
    full_parser.add_argument(
        "--go-looking",
        action="store_true",
        help="with --skip-brief, also search the web for older articles rather than "
        "only reading what is already stored. This is the expensive half of a run.",
    )
    full_parser.add_argument(
        "--record", action="store_true", help="capture HTTP + LLM envelopes into fixtures/"
    )

    run_parser = sub.add_parser("analyze-run", help="analyze pending clusters")
    run_parser.add_argument("--limit", type=int, help="max clusters")
    run_parser.add_argument("--max-calls", type=int, help="cap model calls")

    brief_parser = sub.add_parser("brief", help="generate the brief")
    brief_parser.add_argument("--date", help="YYYY-MM-DD (default: today)")

    sub.add_parser("rescore", help="reweight stored scores (no model calls)")

    reann_parser = sub.add_parser("reannotate", help="re-resolve brief spans (no model calls)")
    reann_parser.add_argument("--brief", help="brief id (default: latest)")

    alias_parser = sub.add_parser("alias", help="alias inspection and repair")
    alias_sub = alias_parser.add_subparsers(dest="alias_command", required=True)
    audit_parser = alias_sub.add_parser("audit", help="review fuzzy-attached aliases")
    audit_parser.add_argument("--retract", metavar="ALIAS_ID", help="remove one alias")

    users_parser = sub.add_parser("users", help="who may sign in, and what they may do")
    users_sub = users_parser.add_subparsers(dest="users_command", required=True)
    users_sub.add_parser("ls", help="list them")
    users_add = users_sub.add_parser("add", help="add one")
    users_add.add_argument("--email", required=True)
    users_add.add_argument(
        "--role",
        default="reader",
        choices=sorted(_role_names()),
        help="reader by default: the safe answer when someone types this in a hurry",
    )
    users_role = users_sub.add_parser("role", help="change a role")
    users_role.add_argument("--email", required=True)
    users_role.add_argument("--role", required=True, choices=sorted(_role_names()))
    users_suspend = users_sub.add_parser("suspend", help="end their access now")
    users_suspend.add_argument("--email", required=True)
    users_reinstate = users_sub.add_parser("reinstate", help="undo a suspension")
    users_reinstate.add_argument("--email", required=True)
    users_comp = users_sub.add_parser("comp", help="they read everything and pay nothing")
    users_comp.add_argument("--email", required=True)
    users_uncomp = users_sub.add_parser("uncomp", help="withdraw a comp")
    users_uncomp.add_argument("--email", required=True)

    mcp_parser = sub.add_parser("mcp", help="the MCP server for agents")
    mcp_sub = mcp_parser.add_subparsers(dest="mcp_command", required=True)
    mcp_token_parser = mcp_sub.add_parser("token", help="agent credentials")
    mcp_token_sub = mcp_token_parser.add_subparsers(dest="mcp_token_command", required=True)
    mcp_new = mcp_token_sub.add_parser("new", help="mint one; prints it once")
    # Required, not optional. The nine rows this listed on 2026-08-17 all read
    # "(no label)", which is not a list -- the same argument `run_launcher` makes
    # for putting the target in `params`.
    mcp_new.add_argument("--label", required=True, help='e.g. "claude-code on the laptop"')
    # An agent token belongs to a USER and inherits that user's role. Optional
    # because the common case is a single-admin instance, where guessing is
    # unambiguous -- see `_resolve_token_owner`, which refuses to guess when it is
    # not.
    mcp_new.add_argument("--email", help="the user who will own it (default: the only admin)")
    mcp_new.add_argument(
        "--scope",
        default="mcp",
        choices=sorted(_token_scopes()),
        help="mcp = the MCP endpoint; annotate = the narrow set the browser extension needs",
    )
    mcp_new.add_argument(
        "--expires-days",
        type=int,
        help="expire after N days (default: never, which is every historical token)",
    )
    mcp_ls = mcp_token_sub.add_parser("ls", help="list them")
    mcp_ls.add_argument("--all", action="store_true", help="include revoked")
    mcp_revoke = mcp_token_sub.add_parser("revoke", help="revoke one by id")
    mcp_revoke.add_argument("token_id", metavar="ID")

    entity_parser = sub.add_parser("entity", help="entity inspection")
    entity_sub = entity_parser.add_subparsers(dest="entity_command", required=True)
    show_parser = entity_sub.add_parser("show", help="aliases, assertions, relations")
    show_parser.add_argument("entity_id")

    merge_parser = entity_sub.add_parser("merge", help="merge one entity into another")
    merge_parser.add_argument("from_id", metavar="LOSER")
    merge_parser.add_argument("to_id", metavar="SURVIVOR")
    merge_parser.add_argument("--reason")

    unmerge_parser = entity_sub.add_parser("unmerge", help="reverse a merge")
    unmerge_parser.add_argument("from_id", metavar="LOSER")
    unmerge_parser.add_argument("merge_id", metavar="MERGE_ID")

    entity_sub.add_parser("merges", help="list every merge")

    define_parser = sub.add_parser("define", help="entity definitions")
    define_sub = define_parser.add_subparsers(dest="define_command", required=True)
    define_backfill = define_sub.add_parser(
        "backfill", help="re-read stored articles for names Venona cannot explain"
    )
    # A DOCUMENT limit, not a name limit, because the call is per document.
    # Leave it off for the whole corpus; the pass is resumable either way.
    define_backfill.add_argument(
        "--limit", type=int, default=None, metavar="N", help="stop after N articles"
    )
    define_backfill.add_argument(
        "--min-salience",
        type=float,
        default=0.3,
        help="skip names the article barely mentions (default 0.3)",
    )
    define_backfill.add_argument(
        "--max-calls", type=int, default=None, metavar="N", help="ceiling on model calls"
    )
    # The free pass alone, over the whole corpus. Run this first: it says how
    # far the sentence shapes reach before anything is charged for.
    define_backfill.add_argument(
        "--no-model", action="store_true", help="the free pass only, no model calls"
    )

    # Run history. Nothing here deletes: `hide` sets `hidden_at`, `unhide`
    # clears it, so tidying the listing never orphans a run's rows.
    runs_parser = sub.add_parser("runs", help="run history")
    runs_sub = runs_parser.add_subparsers(dest="runs_command", required=True)

    runs_list = runs_sub.add_parser("list", help="recent runs")
    runs_list.add_argument("--all", action="store_true", help="include hidden runs")
    runs_list.add_argument("--limit", type=int, default=25)

    runs_hide = runs_sub.add_parser("hide", help="drop a run out of the default listing")
    runs_hide.add_argument("run_id")

    runs_unhide = runs_sub.add_parser("unhide", help="put a hidden run back")
    runs_unhide.add_argument("run_id")

    runs_sub.add_parser("clear", help="hide every visible run (a live run is skipped)")

    # Brief history. `venona brief` GENERATES one; these manage the archive.
    # Unlike `runs`, `delete` here is a real delete -- see `_briefs_delete`.
    briefs_parser = sub.add_parser("briefs", help="the brief archive")
    briefs_sub = briefs_parser.add_subparsers(dest="briefs_command", required=True)

    briefs_list = briefs_sub.add_parser("list", help="stored briefs, newest first")
    briefs_list.add_argument("--limit", type=int, default=25)

    briefs_delete = briefs_sub.add_parser(
        "delete", help="delete a brief AND release its clusters for re-briefing"
    )
    briefs_delete.add_argument("brief_id")

    # Access requests. `POST /api/request-access` writes rows nobody would ever
    # see otherwise: `notify` raises a notice on submission, but with no
    # destination configured that is a no-op, so without this the funnel ends in
    # a table. Nothing here grants anything -- `venona users add` does, and
    # `decide` only records what the operator already did, so the same person is
    # not weighed up twice.
    access_parser = sub.add_parser("access", help="access requests from /request-access")
    access_sub = access_parser.add_subparsers(dest="access_command", required=True)

    access_list = access_sub.add_parser("list", help="who is waiting, newest first")
    access_list.add_argument("--all", action="store_true", help="include requests already decided")
    access_list.add_argument("--limit", type=int, default=50)

    access_decide = access_sub.add_parser(
        "decide", help="record a decision (does NOT change the allow-list)"
    )
    access_decide.add_argument("email")
    access_decide.add_argument("verdict", choices=["approved", "declined"])
    access_decide.add_argument("--note", default="", help="for your own benefit later")

    anchors_parser = sub.add_parser("anchors", help="the top-level structure of the graph")
    anchors_sub = anchors_parser.add_subparsers(dest="anchors_command", required=True)

    anchors_load = anchors_sub.add_parser("load", help="load profile/anchors.yaml")
    anchors_load.add_argument(
        "--dry-run", action="store_true", help="print what would be written, write nothing"
    )
    anchors_load.add_argument(
        "--apply-merges",
        action="store_true",
        help="also apply the `merges:` block (retypes and duplicate folds)",
    )

    anchors_propose = anchors_sub.add_parser(
        "propose", help="file attachment proposals into the curation queue"
    )
    anchors_propose.add_argument(
        "--limit", type=int, default=60, help="how many isolated entities to consider"
    )
    anchors_propose.add_argument(
        "--no-model",
        action="store_true",
        help="deterministic passes only; spends nothing",
    )

    anchors_sub.add_parser("status", help="components, isolated nodes, largest")

    research_parser = sub.add_parser(
        "research", help="chase corroboration for entities blocking relationships"
    )
    research_parser.add_argument(
        "--entity", action="append", metavar="ENTITY_ID", help="target these specifically"
    )
    research_parser.add_argument(
        "--limit", type=int, default=10, help="how many stuck entities to target"
    )
    research_parser.add_argument(
        "--dry-run", action="store_true", help="print the targets and spend nothing"
    )

    conflicts_parser = sub.add_parser("conflicts", help="the alias-conflict review queue")
    conflicts_parser.add_argument(
        "--kind",
        choices=(
            "duplicate",
            "collision",
            "containment",
            "resolver_error",
            "anchor",
            "cross_type",
            "type_conflict",
        ),
        help="show one kind only",
    )
    conflicts_parser.add_argument("--all", action="store_true", help="include rows already settled")
    conflicts_parser.add_argument(
        "--keep-apart",
        metavar="CONFLICT_ID",
        help="record that a pair is genuinely two different things",
    )

    analyze_parser = sub.add_parser("analyze", help="run one analyze call end to end")
    analyze_parser.add_argument("--file", help="path to a text file (default: latest document)")
    analyze_parser.add_argument("--url", default="https://example.test/manual")
    analyze_parser.add_argument("--title", default="Manual analysis")

    onto_parser = sub.add_parser("ontology", help="the research<->graph contract")
    onto_sub = onto_parser.add_subparsers(dest="ontology_command", required=True)
    onto_sub.add_parser("check", help="validate config/ontology.yaml")
    onto_sub.add_parser("docs", help="regenerate docs/graph-schema.md")

    graph_parser = sub.add_parser("graph", help="graph projection commands")
    graph_sub = graph_parser.add_subparsers(dest="graph_command", required=True)
    graph_sub.add_parser("rebuild", help="truncate and re-project from assertions")
    graph_sub.add_parser("stats", help="node/edge counts for the idempotency gate")

    args = parser.parse_args(argv)

    match args.command:
        case "health":
            return asyncio.run(_health())
        case "migrate":
            return _migrate()
        case "seed":
            return asyncio.run(_seed())
        case "dump-schema":
            return _dump_schema()
        case "dump-openapi":
            return _dump_openapi()
        case "auth":
            return _auth(args.auth_command)
        case "config":
            return _config(args.config_command, args)
        case "instance":
            return _instance_seed()
        case "mcp":
            return _mcp_token(args.mcp_token_command, args)
        case "users":
            return _users(args.users_command, args)
        case "verify-sources":
            return asyncio.run(_verify_sources())
        case "sources":
            return _sources(args.enable)
        case "fetch":
            return asyncio.run(_fetch(args.limit, args.dry))
        case "run":
            return asyncio.run(
                _run(
                    args.only,
                    args.limit,
                    args.analyze_limit,
                    args.record,
                    skip_brief=args.skip_brief,
                    lookback_days=args.lookback_days,
                    max_calls=args.max_calls,
                    go_looking=args.go_looking,
                )
            )
        case "analyze-run":
            return asyncio.run(_analyze_run(args.limit, args.max_calls))
        case "brief":
            return asyncio.run(_brief(args.date))
        case "rescore":
            return _rescore()
        case "reannotate":
            return _reannotate(args.brief)
        case "alias":
            return _alias_audit(args.retract)
        case "access":
            match args.access_command:
                case "list":
                    return _access_list(args.all, args.limit)
                case "decide":
                    return _access_decide(args.email, args.verdict, args.note)
        case "entity":
            match args.entity_command:
                case "merge":
                    return _entity_merge(args.from_id, args.to_id, args.reason)
                case "unmerge":
                    return _entity_unmerge(args.from_id, args.merge_id)
                case "merges":
                    return _entity_merges()
                # Indentation decides which `match` an arm belongs to: this one
                # sat a level deeper, inside `case "anchors"`, and
                # `venona entity show company:anduril` fell out of both matches
                # and exited 2 with "unknown command entity".
                case "show":
                    return _entity_show(args.entity_id)
        case "define":
            match args.define_command:
                case "backfill":
                    return asyncio.run(
                        _define_backfill(
                            limit=args.limit,
                            min_salience=args.min_salience,
                            max_calls=args.max_calls,
                            use_model=not args.no_model,
                        )
                    )
        case "anchors":
            match args.anchors_command:
                case "load":
                    return _anchors_load(dry_run=args.dry_run, apply_merges=args.apply_merges)
                case "propose":
                    return asyncio.run(
                        _anchors_propose(limit=args.limit, use_model=not args.no_model)
                    )
                case "status":
                    return _anchors_status()
        case "briefs":
            match args.briefs_command:
                case "list":
                    return _briefs_list(args.limit)
                case "delete":
                    return _briefs_delete(args.brief_id)
        case "runs":
            match args.runs_command:
                case "list":
                    return _runs_list(include_hidden=args.all, limit=args.limit)
                case "hide":
                    return _runs_hide(args.run_id)
                case "unhide":
                    return _runs_unhide(args.run_id)
                case "clear":
                    return _runs_clear()
        case "research":
            return asyncio.run(_research(args.entity, args.limit, args.dry_run))
        case "conflicts":
            return _conflicts(args.kind, args.all, args.keep_apart)
        case "analyze":
            return asyncio.run(_analyze(args.file, args.url, args.title))
        case "ontology":
            return _ontology_check() if args.ontology_command == "check" else _ontology_docs()
        case "graph":
            if args.graph_command == "rebuild":
                return asyncio.run(_graph_rebuild())
            return asyncio.run(_graph_stats())

    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
