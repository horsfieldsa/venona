"""Is the graph healthy, and where is it quietly wrong?

    uv run python scripts/graph_health.py           # everything
    uv run python scripts/graph_health.py splits    # one section
    uv run python scripts/graph_health.py effectiveness --deep   # a wider sample

Read-only. It opens the database and never writes.

WHY THIS EXISTS. Every defect it looks for was found by a person asking the
corpus an ordinary question and noticing the answer was wrong -- "how does
Palantir relate to NGC2" turned up one company held as two entities, which made
every corroborated fact about it read SINGLE-SOURCE. Nothing failed. No test
went red. The counters all agreed with themselves.

So the checks here are not "did a stage error". They are the questions a reader
would eventually ask on your behalf, asked early and in bulk:

  splits        is one thing being held as two?
  queue         are the questions the system cannot answer piling up?
  merges        is anything auto-merging that a human then undoes?
  corroboration how much of the graph rests on a single publisher?
  dangling      are we counting evidence we cannot show?
  candidates    what structure does the corpus keep naming that we do not hold?
  runs          are the runs finishing, and what did curate do?
  effectiveness did they find the RIGHT things?

Each section prints ROWS A HUMAN READS, not just a number. A count says the
corpus has 23 problems; it cannot say whether they are the same problem or
whether any of them is real. That distinction is the whole exercise -- it is
the same argument `lookup_report.py` makes about grounded definitions.
"""

from __future__ import annotations

import sys
from bisect import bisect_right
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from venona.db import core as db  # noqa: E402
from venona.db.models import normalize  # noqa: E402
from venona.db.repositories import config_documents as docs_repo  # noqa: E402
from venona.db.repositories import diagnostics as diag  # noqa: E402
from venona.graph.projector import resolve_mappings  # noqa: E402
from venona.pipeline import curate  # noqa: E402
from venona.pipeline.acronyms import load_merge_tokens, parse_merge_tokens  # noqa: E402
from venona.settings import get_settings  # noqa: E402

BAR = "─" * 72


def head(title: str, blurb: str) -> None:
    print(f"\n{BAR}\n{title.upper()}\n{blurb}\n")


#: The pipeline's own rule, imported rather than restated. A second copy here
#: read `name` where the real one reads `_comparable(name)`, so a trailing
#: `(GLOSS)` produced a different key and every glossed variant went unreported
#: -- the report quietly stopped describing what the next run would do, which is
#: the one thing it is for. `cli.py` reaches into `resolve` for
#: `_is_qualified_containment` on the same reasoning.
merge_key = curate._merge_key


def merge_tokens(conn: db.Conn) -> tuple[set[str], set[str]]:
    """The curator's vocabulary, out of the database this report is reading.

    The editable documents are rows, and `materialize` writes them to disk when
    an instance starts -- so `profile/lexicon.yaml` beside this script belongs
    to whichever instance last started on this machine, which is not the corpus
    being audited. Reading the file against a remote database would classify one
    instance's names with another's words, and the two failures are opposite:
    tokens the audited instance does not hold report merges its curator would
    never make, and tokens it holds that this machine lacks hide the splits that
    are really there.
    """
    row = docs_repo.get(conn, key="lexicon")
    if row is None:
        return load_merge_tokens(get_settings())
    import yaml

    return parse_merge_tokens(yaml.safe_load(row["content"]) or {})


def splits(conn: db.Conn) -> None:
    head(
        "splits",
        "One thing held as two ids. Each split halves its own evidence, because\n"
        "source counts are per id -- so this is the check that decides whether a\n"
        "reader is told a corroborated fact is uncorroborated.",
    )
    corporate_form, generic_tails = merge_tokens(conn)
    mappings = resolve_mappings(conn)
    entities = diag.live_entities(conn)
    by_id = {row["id"]: row for row in entities}

    same_type: dict[tuple[str, str], list[db.Row]] = defaultdict(list)
    by_key: dict[str, list[db.Row]] = defaultdict(list)
    for row in entities:
        key = merge_key(row["name"], corporate_form, generic_tails)
        if not key:
            continue
        same_type[(key, row["type"])].append(row)
        by_key[key].append(row)

    groups = [rows for rows in same_type.values() if len(rows) > 1]
    print(f"same name, same type: {len(groups)} group(s)")
    for rows in sorted(groups, key=lambda g: -sum(r["mention_count"] or 0 for r in g)):
        for row in sorted(rows, key=lambda r: -(r["mention_count"] or 0)):
            print(
                f"    {row['id']:<52} {row['status']:<12} {row['mention_count'] or 0:>4} mentions"
            )
        print()

    crossing = [rows for rows in by_key.values() if len({r["type"] for r in rows}) > 1]
    print(f"same name, different types: {len(crossing)} group(s)")
    print("  These are NOT merges. A Program and the Platform it delivers are two")
    print("  things and the ontology says so; the fix is usually to rename one.")
    for rows in crossing:
        print(f"    {rows[0]['name']}")
        for row in sorted(rows, key=lambda r: r["id"]):
            print(f"      {row['id']:<50} {row['type']}")

    # THE MERGE INVARIANT, checked before the name comparisons because it needs
    # none of them. A fix with no backfill is half a fix: the seed loader
    # stopped writing `active` over a tombstone on 2026-09-08 and the three
    # casualties from before it sat in the live corpus unnoticed, one of them
    # the most-mentioned company in the graph. Every one of them also shows up
    # above as an ordinary duplicate, which is how it stayed hidden.
    folded = diag.merged_but_live(conn)
    print(f"\nmerged away but still live: {len(folded)}")
    for row in folded:
        print(
            f"    {row['id']:<50} {row['status']:<12} {row['assertions']:>4} assertions"
            f"  -> {row['to_id']}  [{row['reason']}]\n"
            f"        undo with {row['merge_id']}"
        )

    # An entity whose whole name is a surface a person gave to another entity.
    # The strongest signal available and the one no name comparison can see.
    owners: dict[tuple[str, str], set[str]] = defaultdict(set)
    for alias in diag.declared_aliases(conn):
        owners[(alias["normalized"], alias["type"])].add(alias["entity_id"])
    settled = {
        frozenset((r["existing_entity_id"], r["proposed_entity_id"])): r["resolution"]
        for r in diag.settled_pairs(conn)
    }
    claimed = []
    for row in entities:
        for owner_id in owners.get((normalize(row["name"]), row["type"]), set()) - {row["id"]}:
            if mappings.get(owner_id, owner_id) != mappings.get(row["id"], row["id"]):
                claimed.append((row, by_id.get(owner_id)))
    open_claims = [c for c in claimed if frozenset((c[0]["id"], c[1]["id"])) not in settled if c[1]]
    print(f"\nname is another entity's declared alias: {len(open_claims)}")
    for row, owner in open_claims:
        print(f"    {row['id']:<50} <- declared by {owner['id']}")

    # A settled pair is not a finding, with ONE exception worth printing: a
    # curator ruled the two are related while an anchor still declares one's
    # name an alias of the other. Both are human statements and they contradict
    # each other, so one of them should be withdrawn.
    contradicted = [
        (row, owner, settled[frozenset((row["id"], owner["id"]))])
        for row, owner in claimed
        if owner and frozenset((row["id"], owner["id"])) in settled
    ]
    if contradicted:
        print(f"\ndeclared alias CONTRADICTED by a recorded verdict: {len(contradicted)}")
        for row, owner, verdict in contradicted:
            print(f"    {row['id']} <- alias of {owner['id']}, but ruled {verdict!r}")


def queue(conn: db.Conn) -> None:
    head(
        "curation queue",
        "What the system could not decide. A queue that only grows is a queue\n"
        "nobody works; a queue full of one shape is a rule waiting to be written.",
    )
    rows = diag.conflict_queue(conn)
    if not rows:
        print("empty")
        return
    by_kind: dict[str, list[db.Row]] = defaultdict(list)
    for row in rows:
        by_kind[row["kind"] or "unclassified"].append(row)
    print(f"{len(rows)} open, oldest {rows[0]['created_at']}\n")
    for kind, items in sorted(by_kind.items(), key=lambda kv: -len(kv[1])):
        suggested = sum(1 for r in items if r["suggestion"])
        print(f"  {kind:<16} {len(items):>4}   ({suggested} carry a model suggestion)")
    print("\n  a sample, oldest first:")
    for row in rows[:8]:
        print(
            f"    [{row['kind'] or '?':<13}] {row['a_name']!r} vs {row['b_name']!r}{_verdict(row)}"
        )


def _verdict(row: db.Row) -> str:
    """The model's reading of one pair, in a form that cannot be read backwards.

    A `part_of` verdict IS a direction, and the pair prints in filed order --
    which on the live queue is the opposite of the answer three times over
    (`Senate`, `House`, `Special Operations Command` are each the PARENT while
    reading as the child). Naming the child costs a clause and removes the only
    way to misread the line.
    """
    if not row["suggestion"]:
        return ""
    note = f" · model says {row['suggestion']}"
    if row["suggestion"] == "part_of" and row["suggestion_child"]:
        child = row["suggestion_child"]
        if child == row["existing_entity_id"]:
            note += f", {row['a_name']!r} inside {row['b_name']!r}"
        elif child == row["proposed_entity_id"]:
            note += f", {row['b_name']!r} inside {row['a_name']!r}"
    if row["suggestion_confidence"]:
        note += f" ({row['suggestion_confidence']})"
    return note


def merges(conn: db.Conn) -> None:
    head(
        "merges, by the reason recorded on them",
        "The ONLY check on an auto-merge rule. A cohort a curator keeps undoing is\n"
        "a rule that is wrong, and `unmerge` deactivates rather than deletes so\n"
        "that this number exists at all.",
    )
    rows = diag.merge_cohorts(conn)
    if not rows:
        print("none")
        return
    for row in rows:
        undone = row["undone"] or 0
        total = row["total"]
        rate = f"{undone / total:.0%}" if total else "—"
        flag = "  <-- REVIEW THIS RULE" if total >= 3 and undone / total > 0.2 else ""
        print(
            f"  {(row['reason'] or '—'):<34} {total:>4} merged  {undone:>3} undone  {rate:>5}{flag}"
        )
    print(
        "\n  An undone merge is a person saying the machine was wrong. A rate of zero\n"
        "  means either the rule is right or nobody has looked -- `venona entity\n"
        "  merges` is how you tell the two apart."
    )


def corroboration(conn: db.Conn) -> None:
    head(
        "corroboration",
        "How much of the graph rests on one publisher. Counted on distinct site\n"
        "domains, which is the definition the cards and the canvas both use -- a\n"
        "report that counted articles would disagree with the product.",
    )
    rows = diag.edge_corroboration(conn)
    if not rows:
        print("no edges")
        return
    declared = [r for r in rows if r["declared"]]
    read = [r for r in rows if not r["declared"]]
    if declared:
        print(f"  {len(declared)} declared, which name no article and are not counted below.")
    if not read:
        print("  no edges read from an article")
        return
    single = [r for r in read if (r["domains"] or 0) <= 1]
    print(f"  {len(read)} edges, {len(single)} single-source ({len(single) / len(read):.0%})")
    inflated = [r for r in single if (r["assertions"] or 0) > 1]
    print(f"  {len(inflated)} of those carry more than one ARTICLE from that one outlet.")
    print(
        "  Those are the edges most likely to read as corroborated to someone\n"
        "  skimming an article count instead of a publisher count."
    )


def candidates(conn: db.Conn) -> None:
    head(
        "names the trunk does not hold",
        "Entities several publishers name, attached to nothing, declared by\n"
        "nobody. The corpus's own answer to what the starting structure is\n"
        "missing -- and a list to READ, never to apply: a place, an event and a\n"
        "one-off person all reach it, and a declared entity is permanent.",
    )
    from venona.services.seed import ANCHOR_RUN_ID, SEED_RUN_ID

    rows = diag.anchor_candidates(conn, (SEED_RUN_ID, ANCHOR_RUN_ID))
    if not rows:
        print("  none: everything corroborated is either attached or declared")
        return
    print(f"  {len(rows)} candidate(s), best corroborated first\n")
    for row in rows[:20]:
        print(
            f"    {row['type']:<15} {row['name'][:44]:<44} "
            f"{row['source_count']} publishers, {row['mention_count']} mentions"
        )
    if len(rows) > 20:
        print(f"    ... and {len(rows) - 20} more")
    print(
        "\n  A person decides. Structure that keeps recurring earns a line in the\n"
        "  anchors document; a place, an exercise or a passing name does not, and\n"
        "  the ontology already says which of those are not entities at all."
    )


def dangling(conn: db.Conn) -> None:
    head(
        "dangling endpoints",
        "Assertions naming an entity that no longer exists. They are counted by\n"
        "aggregates and drawn by nothing, which is the shape of every 'showing 3\n"
        "of 5' defect: a total that promises what no query can return.",
    )
    rows = diag.dangling_assertion_endpoints(conn)
    print(f"  {len(rows)} found" + (" (capped at 200)" if len(rows) == 200 else ""))
    for row in rows[:10]:
        print(f"    {row['subject_id_raw']} —{row['predicate']}→ {row['object_id_raw']}")


def runs(conn: db.Conn) -> None:
    head(
        "runs",
        "Did they finish, and what did curate do. `succeeded_partial` is not a\n"
        "failure but it is never nothing: something was skipped and said so.",
    )
    for row in diag.recent_run_health(conn, 8):
        cost = f"${row['cost_usd']:.2f}" if row["cost_usd"] else "free"
        error = f"  {row['error']}" if row["error"] else ""
        print(f"  {row['started_at'][:16]}  {row['kind']:<12} {row['status']:<18} {cost:>7}{error}")

    latest: dict[str, dict[str, float]] = defaultdict(dict)
    for row in diag.curate_metrics(conn, 60):
        latest[row["run_id"]][row["key"]] = row["value"]
    if latest:
        print("\n  curate, most recent runs:")
        for run_id, metrics in list(latest.items())[:5]:
            summary = "  ".join(f"{k}={int(v)}" for k, v in sorted(metrics.items()) if v)
            print(f"    {run_id[:24]}  {summary or 'nothing to do'}")


#: `profile_hooks` held raw model output until `analyze.verified_hooks` landed
#: on 2026-08-27. Counting a cluster analysed before that would present
#: unchecked phrases as verified, and would let one coincidence cover a passage
#: of the profile nothing has really cited.
HOOKS_VERIFIED_FROM = "2026-08-27"

#: A one-word hook matches half the document. Long enough to name something.
MIN_HOOK_CHARS = 12

#: Small enough to read end to end, because the protocol in the skill asks a
#: person to do exactly that before ruling on any of it. A sample nobody
#: finishes is a sample that gets skimmed, and a skimmed sample is worse than
#: none: it produces verdicts with a reader's confidence behind them.
SAMPLE = {"briefs": 3, "entities": 6, "relations": 6, "hooks": 12}

#: Multiplied into `SAMPLE` by `--deep`, for an audit rather than a weekly pass.
DEPTH = 1


def _sample(key: str) -> int:
    return SAMPLE[key] * DEPTH


def profile_text(conn: db.Conn) -> str:
    """The relevance profile, out of the database this report is reading.

    Same reasoning as `merge_tokens`: the editable documents are rows and
    `materialize` writes them to disk at startup, so the copy in this checkout
    belongs to whichever instance last started here. Scoring the audited
    corpus's hooks against another instance's profile would report every
    passage as uncited.
    """
    row = docs_repo.get(conn, key="relevance_profile")
    return (row["content"] if row else "") or ""


def profile_blocks(text: str) -> list[tuple[int, str]]:
    """The profile as markdown blocks, with where each starts once whitespace
    is collapsed the way `verified_hooks` collapses it.

    BLOCKS, NOT LINES, and this is the whole accuracy of the check.
    `verified_hooks` normalises the entire document before matching, so a phrase
    copied from a wrapped bullet spans two source lines and sits inside neither
    of them. Splitting on lines would report both halves of every wrapped bullet
    as never cited, and those are the substantive ones.
    """
    out: list[tuple[int, str]] = []
    pos = 0
    for raw in text.split("\n\n"):
        block = " ".join(raw.split())
        if not block:
            continue
        out.append((pos, block))
        pos += len(block) + 1
    return out


def uncited_blocks(text: str, hooks: list[str]) -> list[str]:
    """Passages of the profile no score has ever cited.

    Found by locating each hook in the haystack and attributing it to the block
    that contains its offset, rather than by asking whether a block contains a
    hook. The two differ exactly on the hooks that span a block boundary, and
    only the first of them is right.
    """
    index = profile_blocks(text)
    hay = " ".join(block for _, block in index).casefold()
    starts = [start for start, _ in index]
    cited: set[int] = set()
    for hook in hooks:
        needle = " ".join(hook.split()).casefold()
        if len(needle) < MIN_HOOK_CHARS:
            continue
        at = hay.find(needle)
        while at != -1:
            cited.add(bisect_right(starts, at) - 1)
            at = hay.find(needle, at + 1)
    return [
        block for i, (_, block) in enumerate(index) if i not in cited and not _is_structure(block)
    ]


def _is_structure(block: str) -> bool:
    """A heading or a rule carries no claim about the domain, so nothing can
    cite it and its absence says nothing. Left in, they are most of the list and
    the one passage that IS a finding reads as more of the same."""
    return block.startswith("#") or set(block) <= set("-=_ ")


def ruled_subjects() -> dict[str, str]:
    """Every cluster, name and relationship a person has already ruled on.

    Read straight out of the fixture rather than through `tests/editorial.py`.
    The validating loader belongs to the suite, and a report that could not run
    without the test package is a report nobody runs; keeping the file well
    formed is the suite's job, and keeping a settled row out of the way is this
    one's.
    """
    path = ROOT / "fixtures/curation/editorial.yaml"
    if not path.exists():
        return {}
    import yaml

    raw = yaml.safe_load(path.read_text()) or {}
    return {
        row["subject"]: row["verdict"]
        for row in (raw.get("cases") or [])
        if row.get("subject") and row.get("verdict")
    }


def _unruled(rows: list[db.Row], ruled: dict[str, str], key: str, want: int) -> list[db.Row]:
    """The sample, minus what somebody has already decided.

    Filtered here rather than in SQL because the ids are per instance: a table
    written against one corpus has to go inert against another rather than
    reach into its query. Over-fetching by the size of the table is what keeps
    the sample full -- trimming after a LIMIT would quietly shrink it every
    time a verdict is recorded, so the reader would be shown less for having
    done the work.
    """
    return [row for row in rows if row[key] not in ruled][:want]


def effectiveness(conn: db.Conn) -> None:
    head(
        "effectiveness",
        "Did the runs find the RIGHT things. No counter answers that, so every\n"
        "block here is a sample with its evidence attached, for a person to rule\n"
        "on. Read all of it before ruling on any of it.",
    )
    briefs = diag.brief_writing_runs(conn, _sample("briefs"))
    if not briefs:
        print("  no briefs yet: nothing has been written to judge")
        return
    run_ids = [row["run_id"] for row in briefs if row["run_id"]]
    ruled = ruled_subjects()

    _retrieved_and_untold(conn, briefs, ruled)
    _profile_is_steering(conn)
    _minted_names(conn, run_ids, ruled)
    _relations_read(conn, run_ids, ruled)
    _where_it_looked(conn, run_ids)
    print(
        f"  {len(ruled)} row(s) already judged are not shown again; they and their\n"
        "  reasons are in fixtures/curation/editorial.yaml."
    )


def _retrieved_and_untold(conn: db.Conn, briefs: list[db.Row], ruled: dict[str, str]) -> None:
    print("what each brief retrieved, and what it went on to tell")
    print("  A brief SPENDS every cluster it retrieves: each one is excluded from")
    print("  every later brief. One it then does not write about is gone, and")
    print("  nobody has seen it. Read the untold ones and say whether that was right.\n")
    rows = diag.consumed_clusters(conn, [b["brief_id"] for b in briefs])
    by_brief: dict[str, list[db.Row]] = defaultdict(list)
    for row in rows:
        by_brief[row["brief_id"]].append(row)
    for brief in briefs:
        items = by_brief.get(brief["brief_id"], [])
        untold = [r for r in items if not r["items"] and r["id"] not in ruled]
        print(
            f"  {brief['date']}  {len(items):>3} retrieved  "
            f"{len(items) - len(untold):>3} told  {len(untold):>3} spent and untold"
        )
        for row in untold:
            print(
                f"      rank {row['rank']:<3} {row['relevance_overall'] or 0:>3}/100 "
                f"{row['tier'] or '?':<14} {(row['title'] or '')[:60]}"
            )
            if row["so_what"]:
                print(f"           {row['so_what'][:100]}")
    print()


def _profile_is_steering(conn: db.Conn) -> None:
    print("the profile phrases that actually moved a score")
    print("  `profile_hooks` is the evidence a reader is shown for a score, and it")
    print("  is verified against the profile before storage. Read the other way")
    print("  round it says which passages of the profile are doing nothing.\n")
    hooks = diag.hook_usage(conn, HOOKS_VERIFIED_FROM)
    if not hooks:
        print(f"  nothing scored since {HOOKS_VERIFIED_FROM}, so there is nothing to read\n")
        return
    for row in hooks[: _sample("hooks")]:
        print(f"    {row['n']:>4}x  {row['hook'][:64]}")
    if len(hooks) > _sample("hooks"):
        print(f"    ... and {len(hooks) - _sample('hooks')} more phrases")

    text = profile_text(conn)
    if not text:
        print("\n  the audited instance holds no relevance profile to compare against\n")
        return
    uncited = uncited_blocks(text, [row["hook"] for row in hooks])
    print(f"\n  passages of the profile nothing has ever cited: {len(uncited)}")
    print("  A heading here is not a finding. A passage stating what matters is.")
    for block in uncited:
        print(f"      {block[:96]}")
    print()


def _minted_names(conn: db.Conn, run_ids: list[str], ruled: dict[str, str]) -> None:
    print("names these runs brought into existence")
    print("  Read the surface inside its own sentence and say whether it is a thing.")
    print("  Fewest sightings first: the likeliest junk, and the rows curate will")
    print("  hold provisional forever without anyone looking at them.\n")
    want = _sample("entities")
    rows = _unruled(diag.minted_entities(conn, run_ids, want + len(ruled)), ruled, "id", want)
    if not rows:
        print("  these runs minted nothing\n")
        return
    for row in rows:
        print(
            f"    {row['id']:<44} {row['source_count'] or 0} publisher(s), "
            f"{row['mention_count'] or 0} mention(s)"
        )
        print(f'        "{row["surface"]}" in: {(row["evidence_quote"] or "")[:150]}')
    print()


def _relations_read(conn: db.Conn, run_ids: list[str], ruled: dict[str, str]) -> None:
    print("relationships these runs asserted, least confident first")
    print("  The quote either supports the relationship or it does not. Grouped by")
    print("  predicate, because one bad row is bad luck and four under one")
    print("  predicate is a rule to change.\n")
    want = _sample("relations")
    rows = _unruled(diag.relation_samples(conn, run_ids, want + len(ruled)), ruled, "id", want)
    if not rows:
        print("  these runs asserted no relationships\n")
        return
    by_predicate: dict[str, list[db.Row]] = defaultdict(list)
    for row in rows:
        by_predicate[row["predicate"]].append(row)
    for predicate, group in sorted(by_predicate.items(), key=lambda kv: -len(kv[1])):
        print(f"    {predicate}  ({len(group)} in this sample)")
        for row in group:
            print(
                f"      {row['subject_name'][:30]} -> {row['object_name'][:30]}"
                f"   confidence {row['confidence']:.2f}   {row['id']}"
            )
            print(f'        "{(row["evidence_quote"] or "")[:150]}"')
    print()


def _where_it_looked(conn: db.Conn, run_ids: list[str]) -> None:
    print("where these runs looked")
    print("  A run cannot find the right things in a place it is not reading, and a")
    print("  feed that quietly stopped returning anything looks exactly like a feed")
    print("  on a slow week until several runs sit side by side.\n")
    rows = diag.source_stats(conn, run_ids)
    if not rows:
        print("  none of these runs went looking: a rewrite reads the corpus it has\n")
        return

    # SUMMED ACROSS THE SAMPLE, not listed per run. Printed per run this is one
    # line per source per day, which is most of the section and asks the reader
    # to hold three columns in their head to see the only thing it says. A feed
    # that offered nothing every time is the finding, and summing puts it on one
    # line the eye lands on.
    totals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    trouble: dict[str, str] = {}
    for row in rows:
        for field in ("offered", "kept", "duplicate", "stale"):
            totals[row["source_id"]][field] += row[field] or 0
        totals[row["source_id"]]["runs"] += 1
        if row["error"]:
            trouble[row["source_id"]] = f"{row['status']}: {row['error'][:60]}"

    for source_id, t in sorted(totals.items(), key=lambda kv: (kv[1]["kept"], kv[1]["offered"])):
        silent = "   <- offered nothing at all" if not t["offered"] else ""
        note = f"   {trouble[source_id]}" if source_id in trouble else silent
        print(
            f"      {source_id:<28} {t['offered']:>4} offered {t['kept']:>4} kept  "
            f"{t['duplicate']:>4} already had  {t['stale']:>4} stale  "
            f"across {t['runs']} run(s){note}"
        )
    print()


SECTIONS = {
    "splits": splits,
    "queue": queue,
    "merges": merges,
    "corroboration": corroboration,
    "candidates": candidates,
    "dangling": dangling,
    "runs": runs,
    "effectiveness": effectiveness,
}


def _where(dsn: str) -> str:
    """Which corpus this is, named at the top of every report.

    A finding is about an instance, and the two on this machine look identical
    once the rows are on screen: an audit of a development database reads as an
    audit of what people are actually being told. That mistake is only visible
    here, before the first section.
    """
    parsed = urlsplit(dsn)
    return f"{parsed.hostname or '?'}{parsed.path or ''}"


def main() -> int:
    global DEPTH
    args = sys.argv[1:]
    if "--deep" in args:
        DEPTH = 5
        args = [a for a in args if a != "--deep"]
    wanted = args or list(SECTIONS)
    unknown = [name for name in wanted if name not in SECTIONS]
    if unknown:
        print(f"unknown section(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"available: {', '.join(SECTIONS)}", file=sys.stderr)
        return 2
    dsn = get_settings().database_url
    conn = db.connect(dsn)
    try:
        print(f"{BAR}\nreading {_where(dsn)}")
        for name in wanted:
            SECTIONS[name](conn)
        print(f"\n{BAR}\nRead-only. Nothing was written.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
