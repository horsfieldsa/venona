"""Delete the corpus. A HARD delete, and the only one in the application.

WHAT THIS IS FOR. `make reset` wipes the database and re-seeds, and it is the
right tool on a workstation with a terminal open. It is the wrong tool on a
deployed instance, where the operator has a browser and no shell -- and "start
over" is a real thing to want after a misconfigured relevance profile has spent
a week filling the corpus with the wrong industry.

WHY IT IS NOT A SOFT DELETE. `runs` already has `hidden_at` and `venona runs
hide` for tidying a listing; that is a different feature and it is the one to
reach for when the answer is "I don't want to look at this". This is the answer
to "I want it gone", and a soft delete that leaves 6,000 assertions on disk
while telling the operator their data is deleted is a lie with a storage bill.

WHAT SURVIVES, AND WHY EACH ONE.

  schema_migrations   the schema VERSION. Dropping it would make the next start
                      re-run every migration against tables that already exist.
                      It is not data, it is the shape of the database.
  sources             the feed registry, which is a projection of
                      `config/sources.yaml` -- a config document, edited through
                      `config_service`, and not something a data purge owns.
                      `source_health` (the strike counters) DOES go: that is
                      accumulated state about those feeds, not the feeds.
  access_requests     people who asked for an account and have not been
                      answered. They are not corpus data, nobody can recover
                      them, and silently discarding a stranger's request while
                      they wait for a reply is the worst outcome in this file.
                      Purge them from the CLI if you mean to.

INVARIANT 1 IS WHY NEO4J IS AN AFTERTHOUGHT HERE. Postgres is the system of
record and the graph is a rebuildable projection, so the graph is not "also
deleted" -- it is *invalidated*, and the caller clears it because leaving a
projection of rows that have been deleted is worse than an empty one.
"""

from __future__ import annotations

from venona.db import core as db
from venona.db.repositories import purge as repo

# ORDER IS LOAD-BEARING, not merely readable. Postgres enforces all twelve
# foreign keys, so a parent deleted before a child raises 23503 and takes the
# whole transaction with it. The order below is safe because
# every corpus foreign key either CASCADEs from a parent that is purged, or
# points at a table that is PRESERVED -- and `tests/test_purge.py` checks that
# against the live catalogue rather than trusting this paragraph.
#
# Grouped by what they are, so a reviewer can see the shape of what goes rather
# than an alphabetical list they have to hold in their head.
CORPUS_TABLES: tuple[str, ...] = (
    # what was read
    "documents",
    "clusters",
    "sightings",
    # what was learned
    "entities",
    "aliases",
    "assertions",
    "entity_definitions",
    "acronym_definitions",
    "ambiguous_surfaces",
    # what a curator decided
    "alias_conflicts",
    "entity_mappings",
    # what was written
    "briefs",
    "brief_items",
    "brief_blocks",
    "brief_clusters",
    # what it cost and how it went
    "runs",
    "run_stages",
    "run_events",
    # Purged with the runs, unlike run_metrics: stale shape evidence would
    # keep feeding the ontology editor's rejection panel after the corpus
    # that produced it was wiped.
    "run_rejected_shapes",
    "llm_calls",
    "source_health",
)

# Named so the UI can state them rather than describing them in prose that
# drifts from the list above.
PRESERVED_TABLES: tuple[str, ...] = (
    "schema_migrations",
    "sources",
    "access_requests",
    # Agent credentials. Preserved by construction already -- `purge_corpus`
    # iterates CORPUS_TABLES, an allowlist, so a table absent from it is never
    # touched. Named here because this tuple is what `DataView` renders as
    # "Kept: ...", and a screen that promises to say what survives should not
    # leave out the thing whose disappearance would silently disconnect every
    # connected agent.
    "mcp_tokens",
    # The archives an operator has already written out. This is the whole point
    # of them: "export first, then delete" is only true if the delete leaves
    # them alone. Safe by construction already, for the reason above -- named
    # here because somebody about to type `delete everything` under a list of
    # real counts needs telling that their own copies are not in it.
    "data_exports",
)


def purge_corpus(conn: db.Conn) -> dict[str, int]:
    """Delete every corpus row. Returns rows deleted per table.

    One transaction for the whole thing: a purge that half-applied would leave
    assertions pointing at deleted entities, which is a worse state than either
    end of it.

    EVERY COUNT IS TAKEN BEFORE ANY DELETE, and that is not tidiness. Postgres
    enforces the schema's foreign keys, so deleting `entities` CASCADEs the 131
    `aliases` rows with it -- and counting each table as it came round reported
    `aliases: 0` for rows the purge had genuinely just removed. This is what
    `DataView` renders as "Removed", so the screen understated what it had done,
    on the one operation nobody can undo. It was accurate on SQLite, where
    foreign keys were not enforced and each DELETE only ever removed its own
    rows. `tests/test_purge.py` pins the counts against a seeded corpus.
    """
    present = [
        table
        for table in CORPUS_TABLES
        # Skip tables a given schema version does not have, so this keeps
        # working across migrations rather than failing on the one row that
        # names a table added later.
        if repo.select_information_schema_by_table_name(conn, table).fetchone()
    ]
    with db.transaction(conn):
        deleted = {table: repo.count_rows(conn, table) for table in present}
        for table in present:
            repo.delete_all(conn, table)
    return deleted
