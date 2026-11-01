"""Per-domain freshness stamps.

The property under test is not "the hash is stable" -- any hash is. It is that
each domain moves for its OWN changes and stays put for everyone else's, because
a stamp that moves for everything makes all five stores refetch on every write
and a stamp that moves for nothing is the bug it was written to fix.
"""

from __future__ import annotations

import pytest
from helpers import NOW, entity
from venona.db import core as db
from venona.services.freshness import DOMAIN_TERMS, config_stamp, stamps


def test_every_domain_gets_a_stamp_on_an_empty_database(conn: db.Conn) -> None:
    out = stamps(conn)
    assert set(out) == set(DOMAIN_TERMS)
    assert all(len(v) == 12 for v in out.values())


def test_domains_are_distinguishable_when_every_count_is_zero(
    conn: db.Conn,
) -> None:
    """The fresh-database case, which is where a naive fingerprint collides.

    Every aggregate is 0 or '' on a new database, so hashing only the values
    would give all five domains the SAME token -- and a store could not tell its
    own stamp from another's. The domain name is inside the hash for this.
    """
    out = stamps(conn)
    assert len(set(out.values())) == len(out)


def test_a_stamp_is_stable_when_nothing_changes(conn: db.Conn) -> None:
    assert stamps(conn) == stamps(conn)


def _moved(before: dict[str, str], after: dict[str, str]) -> set[str]:
    return {k for k in before if before[k] != after[k]}


def test_inserting_an_entity_moves_only_the_entities_stamp(conn: db.Conn) -> None:
    before = stamps(conn)
    entity(conn, "company:anduril", "Anduril Industries")
    assert _moved(before, stamps(conn)) == {"entities"}


def test_promoting_an_entity_moves_the_stamp_though_no_count_changes(
    conn: db.Conn,
) -> None:
    """The case `MAX(rowid)` alone would miss.

    `promote_provisionals` flips `status` and stamps `last_seen`. The row count
    is identical before and after, so an insert-sensitive term sees nothing --
    and a curator watching an entity cross the corroboration gate would be told
    nothing had happened.
    """
    entity(conn, "company:anduril", "Anduril Industries")
    before = stamps(conn)
    conn.execute(
        "UPDATE entities SET status = 'active', last_seen = ? WHERE id = ?",
        ("2026-08-16T18:00:00Z", "company:anduril"),
    )
    assert _moved(before, stamps(conn)) == {"entities"}


def test_resolving_a_conflict_moves_the_stamp_though_no_row_is_added(
    conn: db.Conn,
) -> None:
    """A verdict is an UPDATE. This is the duplicate-curation case that started
    the whole freshness thread: the queue in another tab has to notice."""
    db.upsert(
        conn,
        "alias_conflicts",
        {
            "id": "c1",
            "kind": "duplicate",
            "surface": "FBMRN",
            "normalized": "fbmrn",
            "entity_type": "Program",
            "existing_entity_id": "prog:a",
            "proposed_entity_id": "prog:b",
            "resolved": 0,
            "created_at": NOW,
        },
        key=["id"],
    )
    before = stamps(conn)
    conn.execute("UPDATE alias_conflicts SET resolved = 1, resolution = 'merged' WHERE id = 'c1'")
    assert _moved(before, stamps(conn)) == {"conflicts"}


def test_a_merge_moves_entities_because_ids_now_resolve_elsewhere(
    conn: db.Conn,
) -> None:
    """A mapping row changes what every id RESOLVES to without touching the
    entity rows a reader is looking at."""
    entity(conn, "company:gdls", "GDLS")
    entity(conn, "company:gd-land-systems", "General Dynamics Land Systems")
    before = stamps(conn)
    db.upsert(
        conn,
        "entity_mappings",
        {
            "from_id": "company:gdls",
            "to_id": "company:gd-land-systems",
            "merge_id": "merge:1",
            "active": 1,
            "reason": "acronym of",
            "created_at": NOW,
        },
        key=["from_id", "merge_id"],
    )
    assert _moved(before, stamps(conn)) == {"entities"}


@pytest.mark.parametrize("domain", sorted(DOMAIN_TERMS))
def test_every_domain_has_both_an_insert_and_an_update_sensitive_term(domain: str) -> None:
    """Structural, not behavioural, and cheap insurance against the one mistake
    this module is most likely to acquire later: someone adding a domain with a
    single `MAX(rowid)` term, which cannot see a verdict, a promotion or a
    merge."""
    assert len(DOMAIN_TERMS[domain]) >= 2


# ── the config domain: files, not rows ───────────────────────────────


def test_config_stamp_is_stable_when_nothing_changes(api_settings) -> None:
    assert config_stamp(api_settings) == config_stamp(api_settings)


def test_saving_a_document_moves_the_config_stamp(api_settings) -> None:
    """The stamp is stat-derived, so the property that matters is that the ONE
    sanctioned write path moves it -- if it does, so does any cruder writer
    (the CLI, a hand edit), because they all touch the same mtime."""
    from venona.services import config_service

    before = config_stamp(api_settings)
    doc = config_service.read_doc(api_settings, "relevance_profile")
    config_service.write_doc(
        api_settings, "relevance_profile", doc.content + "\n", base_version=doc.version
    )
    assert config_stamp(api_settings) != before


def test_staging_a_draft_moves_the_config_stamp(api_settings) -> None:
    """Drafts change what the rail SHOWS (the generated badge) without touching
    any document, so the stamp must cover the drafts directory too."""
    from venona.services import draft_service

    before = config_stamp(api_settings)
    draft_service.store_draft(
        api_settings,
        "relevance_profile",
        "a staged proposal\n",
        run_id="run:test",
        task="regenerate",
        generated_at=NOW,
    )
    assert config_stamp(api_settings) != before
