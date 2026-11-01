"""Per-domain freshness stamps: "has this part of the database moved?"

WHY THIS EXISTS. The app had exactly one live channel -- `/api/runs/{id}/stream`
-- and every other store loaded once and then mutated its own copy. So nothing
could see a change it did not itself make: a nightly run, a CLI command, a
`lookup_all` finishing, a second tab. The reported symptom was "we aren't
getting real time updates and I often need to refresh", and the duplicate
curation asks were one visible instance of the general case.

The generalisation of `annotate_service.index_version`, which has shipped as the
browser extension's cache hint since annotation did. Same technique -- hash a
handful of cheap aggregates into a short token -- applied per DOMAIN rather than
once for the alias index, and returned on `/api/health`, which the frontend
already polls every three seconds. No new transport, and it catches changes that
never went through this browser at all.

**A HINT, NEVER A CORRECTNESS MECHANISM.** This is the property `index_version`
has and the one that must survive being generalised, because a stamp is
load-bearing for five stores rather than one cache:

    a stamp that MOVES makes a store refetch.
    a stamp that DOES NOT move must never make a store SKIP anything.

So every store keeps its load-on-mount and its existing TTL, and nothing on the
server is ever keyed on a stamp. Being coarse costs a refetch that was not
needed. Being wrong in the other direction -- claiming freshness that is not
there -- is what the absence of any server-side cache makes impossible.

COST. Measured 2026-08-16 at 4 microseconds for the whole query on SQLite, so polling it
every three seconds was free, and a reader never blocked the writer -- which
matters because the poll runs hardest exactly while a run is writing. Postgres
keeps the reader-never-blocks half for the same reason (MVCC rather than WAL),
but the 4 microseconds do NOT carry over and have not been re-measured: the
`run_events` term is a sequential count here rather than an index peek. See the
comment on the `runs` domain below. `index_version`'s docstring warns that 84ms
would justify a monotonic epoch row bumped by every writer; that number is the
ANNOTATION cost, not the version query. If this ever does become the problem,
the epoch row is still the right answer -- and it is a better one than it looks,
because a writer that forgets to bump it is a silent staleness bug, which is why
it is not the starting point.

WHAT EACH AGGREGATE IS FOR. A stamp has to move on UPDATE, not only on INSERT.
`COUNT(*)` alone is a trap here: resolving a conflict, promoting an entity and
merging two entities are all updates, and all three are exactly what a curator
needs to see. Every domain below therefore pairs an insert-sensitive term with
an update-sensitive one, and the comments say which is which.
"""

from __future__ import annotations

import hashlib

from venona.db import core as db
from venona.db.repositories import freshness as repo
from venona.services import config_service, draft_service
from venona.settings import Settings

#: domain -> the SQL scalars whose combination is that domain's fingerprint.
#:
#: Kept as text rather than built from a table list because the UPDATE-sensitive
#: term differs per domain and is the whole point; a generic "count every table"
#: helper would quietly lose it. That includes a SQLAlchemy Core version, which the
#: Postgres plan proposed and `docs/adr/` (2026-08-17) declines: what Core
#: catches is a column that has been dropped, and every column here is counted
#: or `MAX`-ed rather than unpacked into a shape.
#:
#: These fragments do NOT execute here -- `repo.domain_stamps` runs them, inside
#: `db/repositories/`, with every value bound. What crosses the repository boundary
#: is composition, not execution. `scripts/lint_repositories.py` cannot see that
#: and says so.
DOMAIN_TERMS: dict[str, tuple[str, ...]] = {
    # Inserts add rows; a verdict RESOLVES one without changing the count, so
    # the open count is the term that actually moves when a curator decides.
    "conflicts": (
        "(SELECT COUNT(*) FROM alias_conflicts)",
        "(SELECT COUNT(*) FROM alias_conflicts WHERE resolved = 0)",
    ),
    # `last_seen` is the update-sensitive term: `promote_provisionals` flips
    # status and stamps it, and a promotion is invisible to any count.
    # `entity_mappings` earns its place because a merge changes what every id
    # RESOLVES to without touching the entity rows a reader is looking at.
    "entities": (
        "(SELECT COUNT(*) FROM entities)",
        "(SELECT COALESCE(MAX(last_seen), '') FROM entities)",
        "(SELECT COUNT(*) FROM entity_mappings WHERE active = 1)",
    ),
    "briefs": (
        "(SELECT COUNT(*) FROM briefs)",
        "(SELECT COALESCE(MAX(created_at), '') FROM briefs)",
    ),
    # A delivery's whole life is status changes -- queued, sending, delivered --
    # and not one of them moves a row count. `updated_at` is stamped on every
    # claim, every failure and every delivery for exactly this, so the history
    # panel updates while somebody is watching it rather than on the next
    # reload. The count still earns its place: it is what moves when a notice is
    # raised, which is the other half of "something happened".
    "notifications": (
        "(SELECT COUNT(*) FROM notify_outbox)",
        "(SELECT COALESCE(MAX(updated_at), '') FROM notify_outbox)",
        # A destination striking out changes neither of the above, and it is the
        # one state on this surface that nothing else would report.
        "(SELECT COUNT(*) FROM notify_destinations WHERE status = 'struck_out')",
    ),
    # Coarse per instance, and that leaks nothing: counts move on a finished
    # generation run, a verdict (status flips without changing the row count,
    # hence the proposed term), or any learner's own grade -- and the stamp
    # only ever tells a store to refetch what it was already allowed to read.
    "learning": (
        "(SELECT COUNT(*) FROM llm_questions)",
        "(SELECT COUNT(*) FROM llm_questions WHERE status = 'proposed')",
        "(SELECT COUNT(*) FROM learning_reviews)",
    ),
    # `ambiguous_surfaces` is covered for the reason `index_version` covers it:
    # nothing else counts it, and a term becoming ambiguous changes what the
    # glossary should say about it.
    "definitions": (
        "(SELECT COUNT(*) FROM acronym_definitions)",
        "(SELECT COUNT(*) FROM entity_definitions)",
        "(SELECT COUNT(*) FROM ambiguous_surfaces)",
    ),
    # `run_events` is append-only and by far the highest-frequency writer, so
    # its row count alone tracks a live run. The runs count catches a run
    # appearing or finishing when no event happened to land in between.
    #
    # This was `MAX(rowid)` on SQLite. On an append-only table `COUNT(*)` is
    # exactly as sensitive -- nothing here is ever deleted, so the two move
    # together -- but it is a sequential scan rather than an index peek, and this
    # runs on `/api/health` every thirty seconds, and every three while a light
    # run is in flight (`useAppPolling.ts`). That is fine at the size this
    # table is and it is the one term here that will not stay fine. When it
    # stops being fine, the answer is the monotonic epoch row `index_version`'s
    # docstring already names, not a cheaper aggregate.
    "runs": (
        "(SELECT COUNT(*) FROM run_events)",
        "(SELECT COUNT(*) FROM runs)",
    ),
}


def stamps(conn: db.Conn) -> dict[str, str]:
    """A short token per domain, changing when that domain probably changed.

    ONE round trip for all of them: the terms are flattened into a single
    SELECT, because this runs on `/api/health` on every poll and five
    separate queries would be five chances to interleave with a writer and
    report a mix of two states. A single statement sees one consistent snapshot.
    """
    ordered = list(DOMAIN_TERMS.items())
    # EVERY term is aliased, and the results are read back BY NAME. Both halves
    # of that matter and neither did on SQLite, where this sliced the row
    # positionally (`row[cursor : cursor + len(terms)]`):
    #
    #   - a row is a dict now, and a dict does not slice;
    #   - unaliased scalar subqueries do not get distinct column names, so a
    #     dict-shaped row would collapse them into ONE key and every domain after
    #     the first would hash the wrong values -- silently, and identically on
    #     every call, so nothing would ever look stale.
    labelled = [
        (f"{domain}_{i}", term)
        for domain, domain_terms in ordered
        for i, term in enumerate(domain_terms)
    ]
    row = repo.domain_stamps(conn, labelled)

    out: dict[str, str] = {}
    for domain, domain_terms in ordered:
        values = [row[f"{domain}_{i}"] for i in range(len(domain_terms))]
        raw = f"{domain}:" + ":".join(str(v) for v in values)
        # The domain name is inside the hash so two domains that happen to hold
        # identical numbers do not produce the same token -- which they would on
        # a fresh database, where every count is zero and every store would then
        # be unable to tell its own stamp from another's.
        out[domain] = hashlib.sha256(raw.encode()).hexdigest()[:12]
    return out


def config_stamp(settings: Settings) -> str:
    """The `config` domain's stamp: the editable documents and their drafts.

    Stat-derived rather than SQL-derived because this domain lives in files --
    `config_service` is the only write path in-process, but the CLI, a second
    tab's API worker and a hand edit all bypass any in-process signal, and mtime
    is the one thing every one of those moves. Same contract as the SQL domains:
    a moved stamp causes a refetch, an unmoved one never suppresses a load.

    `mtime_ns` AND size: an editor that preserves timestamps is rare, a
    same-second save is not, and nanoseconds plus length make the collision an
    oddity rather than a likelihood. Absent files hash as absent -- a first boot
    with no drafts directory is a state, not an error.
    """
    parts: list[str] = []
    for path in config_service.doc_paths(settings):
        try:
            st = path.stat()
            parts.append(f"{path.name}:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            parts.append(f"{path.name}:absent")
    drafts = draft_service.drafts_dir(settings)
    try:
        entries = sorted(drafts.iterdir())
    except OSError:
        entries = []
    for entry in entries:
        try:
            st = entry.stat()
            parts.append(f"draft:{entry.name}:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            continue
    raw = "config:" + ":".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]
