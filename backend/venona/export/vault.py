"""The whole reading, as a folder of linked notes.

PURE. This module reads the database and returns text; it opens no file and
writes no row, which is what lets the golden test compare two builds without a
filesystem in the way.

ONE PASS TO NAME, THEN ONE PASS TO WRITE, and the order matters. A wikilink
resolves by name across the whole folder, so nothing can emit a single link
until every note knows what it is called. That is also the only way to
guarantee the folder has no dead links in it -- see `naming.NoteNames`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from venona.db import core as db
from venona.db.repositories import export as repo
from venona.domain import get_domain
from venona.export import notes, obsidian
from venona.export.frontmatter import render as frontmatter
from venona.export.naming import NoteNames
from venona.graph.projector import resolve_mappings
from venona.ontology import get_ontology

#: Bumped when the folder's shape changes in a way a reader would notice, so an
#: archive can say which arrangement it was written in.
FORMAT_VERSION = 1

NAMES_DIR = "Names"
TERMS_DIR = "Terms"
SOURCES_DIR = "Sources"
BRIEFS_DIR = "Briefs"


@dataclass(frozen=True)
class Member:
    """One file. `path` is relative to the folder and always uses `/`."""

    path: str
    text: str


@dataclass(frozen=True)
class Vault:
    root: str
    members: tuple[Member, ...]
    counts: dict[str, int]


def _humanize(camel: str) -> str:
    """`GovernmentBody` becomes `Government body`.

    Folder names are words a reader knows, and the ontology's type names are
    written for code. The exact value still travels, as the `type` property on
    every note, so the folder can be readable without the vault becoming
    unqueryable.
    """
    out: list[str] = []
    for index, character in enumerate(camel):
        if index and character.isupper() and camel[index - 1].islower():
            out.append(" ")
        out.append(character.lower() if index else character.upper())
    return "".join(out)


def _tag(camel: str) -> str:
    return "venona/" + _humanize(camel).lower().replace(" ", "-")


def _defined(row: db.Row | None) -> bool:
    return bool(row) and row["status"] != "undefined" and bool(row["definition"])


def build_vault(conn: db.Conn, *, now: str) -> Vault:
    """Read everything and return the folder.

    `now` comes from `ctx.clock`, never the wall clock -- it is the only
    changing value in the output and the golden test pins it.
    """
    domain = get_domain()
    ontology = get_ontology()
    # AT THE POINT OF USE, never at import. The ontology is editable over HTTP
    # and a run started from a screen executes in the same process, so a list
    # captured at import silently drops whatever was just saved.
    type_order = {name: index for index, name in enumerate(ontology.entity_types)}
    predicates = set(ontology.predicate_names)
    symmetric = set(ontology.symmetric_predicates)

    mappings = resolve_mappings(conn)

    entity_rows = [
        row for row in repo.entities(conn) if mappings.get(row["id"], row["id"]) == row["id"]
    ]
    entity_rows.sort(key=lambda row: (type_order.get(row["type"], 99), row["name"], row["id"]))
    by_id = {row["id"]: row for row in entity_rows}

    gloss_rows = repo.acronym_definitions(conn)
    document_rows = repo.documents(conn)
    brief_rows = repo.briefs(conn)

    names = NoteNames()
    counts = {"names": 0, "terms": 0, "sources": 0, "briefs": 0, "relationships": 0}

    # ── one pass to name ─────────────────────────────────────────────
    #
    # Fixed order, so the same corpus resolves the same collision the same way
    # in every build. Entities first: a name is the thing, and a short form for
    # it is a way of writing the thing, so the entity keeps the bare title.
    for row in entity_rows:
        names.claim(row["id"], row["name"], qualifier=_humanize(row["type"]))

    #: A glossary entry that already belongs to a name gets NO note of its own.
    #: Its expansion is written onto that name instead, which removes most of
    #: the collisions the qualifier below would otherwise have to resolve.
    standalone_glosses = [row for row in gloss_rows if not row["entity_id"]]
    for row in standalone_glosses:
        names.claim(row["id"], row["surface"], qualifier="term")

    document_titles = {row["id"]: _source_title(row) for row in document_rows}
    for row in document_rows:
        names.claim(row["id"], document_titles[row["id"]], qualifier="source")

    for row in brief_rows:
        names.claim(row["id"], f"{row['date']} brief", qualifier="brief")

    # ── the rest of the reading ──────────────────────────────────────
    aliases, acronyms = _aliases_by_entity(conn, mappings, names)
    definitions = {row["entity_id"]: row for row in repo.entity_definitions(conn)}
    gloss_by_entity: dict[str, db.Row] = {}
    for row in gloss_rows:
        target = mappings.get(row["entity_id"] or "", row["entity_id"] or "")
        if target and target in by_id:
            gloss_by_entity.setdefault(target, row)

    # A FOLDED SHORT FORM HAS TO BECOME AN ALIAS, or typing it resolves to
    # nothing. The glossary and the alias table are separate records and one
    # does not imply the other -- on the live corpus every attached acronym
    # happened to have an alias row too, which is luck rather than a guarantee,
    # and the note that lost the bet would show a short form it is impossible
    # to follow.
    for entity_id, gloss_row in gloss_by_entity.items():
        surface = gloss_row["surface"]
        known = aliases.setdefault(entity_id, [])
        if surface == names.name_for(entity_id) or names.is_taken(surface):
            continue
        if surface not in known:
            known.append(surface)
            known.sort()
        if surface not in acronyms.setdefault(entity_id, []):
            acronyms[entity_id].append(surface)

    # A SURFACE TWO NAMES BOTH ANSWER TO IS NOT AN ALIAS OF EITHER. `MSS` is
    # M1 Support Services and the Maven Smart System and its platform, all in
    # this corpus -- and an `aliases` entry on all three makes `[[MSS]]` resolve
    # to whichever one Obsidian picks, silently and differently from the reader's
    # intent two times in three. Dropped from every note rather than awarded to
    # one: the corpus itself records this surface as ambiguous, and a link that
    # goes somewhere the note never said is the failure this whole pass exists
    # to prevent.
    #
    # The PROSE keeps it. "Also written MSS" is still true of the Maven Smart
    # System, and saying so costs nothing -- it is only the claim that the
    # short form POINTS here that has to go.
    claimed: dict[str, int] = {}
    for surfaces in aliases.values():
        for surface in surfaces:
            claimed[surface] = claimed.get(surface, 0) + 1
    for entity_id, surfaces in aliases.items():
        aliases[entity_id] = [s for s in surfaces if claimed[s] == 1]

    edges, cited, edge_count = _edges(conn, mappings, by_id, predicates, symmetric)

    counts["relationships"] = edge_count
    # A name whose definition cites an article but that carries no relationship
    # would otherwise list no sources at all, which reads as "nothing backs
    # this" on the 315 notes where something does.
    _cite_definitions(cited, by_id, definitions, gloss_by_entity)

    members: list[Member] = []

    for row in entity_rows:
        members.append(
            Member(
                path=f"{NAMES_DIR}/{_humanize(row['type'])}/{names.name_for(row['id'])}.md",
                text=_entity_note(
                    row,
                    names=names,
                    aliases=aliases.get(row["id"], []),
                    acronyms=acronyms.get(row["id"], []),
                    definition=definitions.get(row["id"]),
                    gloss=gloss_by_entity.get(row["id"]),
                    edges=edges.get(row["id"], []),
                    cited=cited.get(row["id"], []),
                ),
            )
        )
        counts["names"] += 1

    for row in standalone_glosses:
        members.append(
            Member(
                path=f"{TERMS_DIR}/{names.name_for(row['id'])}.md",
                text=_term_note(row, names=names),
            )
        )
        counts["terms"] += 1

    quotes = _quotes_by_document(edges, definitions, gloss_rows, names, mappings)
    for row in document_rows:
        members.append(
            Member(
                path=f"{SOURCES_DIR}/{names.name_for(row['id'])}.md",
                text=_source_note(row, names=names, quoted=quotes.get(row["id"], [])),
            )
        )
        counts["sources"] += 1

    for member in _brief_members(conn, brief_rows, names, mappings):
        members.append(member)
        counts["briefs"] += 1

    members.extend(obsidian.config_members(ontology, humanize=_humanize))
    members.append(
        Member(
            path="README.md",
            text=_readme(domain, now=now, counts=counts, names_dir=NAMES_DIR),
        )
    )

    counts["notes"] = counts["names"] + counts["terms"] + counts["sources"] + counts["briefs"]
    root = f"venona-{domain.slug}-{now[:10]}"
    # Byte order, never locale collation: a folder built under a different
    # LC_COLLATE would otherwise order its members differently and the archive
    # would not match.
    members.sort(key=lambda member: member.path.encode("utf-8"))
    return Vault(root=root, members=tuple(members), counts=counts)


#: How much of a source's stored extract the folder carries.
#:
#: MEASURED, not guessed. The median stored extract on a live corpus is 280
#: characters and is a normal opening quotation, but at the ninetieth
#: percentile it is the WHOLE ARTICLE -- the extractor keeps everything when a
#: page is short or oddly marked up, and the longest on that corpus was 6,634
#: characters. Carrying that verbatim would make the archive a redistributable
#: copy of the trade press, which is the one thing the operator ruled out when
#: they chose an extract and a link over the full text. The cap sits just above
#: the median so an ordinary extract survives whole and only the ones that are
#: really the article get cut.
#:
#: The sentences that BACK A CLAIM are separate and uncapped. Those are single
#: sentences (median 181 characters, longest 638) and they are the traceability
#: the whole product rests on -- invariant 3 is worth more than the bytes.
EXTRACT_LIMIT = 320


def _extract(text: str | None, *, limit: int = EXTRACT_LIMIT) -> str | None:
    """An opening quotation, cut at a boundary a reader would have chosen."""
    if not text:
        return None
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    window = cleaned[:limit]
    stop = max(window.rfind(". "), window.rfind("? "), window.rfind("! "))
    if stop > limit // 2:
        return window[: stop + 1]
    return window.rsplit(" ", 1)[0].rstrip(",;:") + "\u2026"


def _source_title(row: db.Row) -> str:
    """`2026-08-14 Breaking Defense - Army picks Palantir for Maven`.

    Date first so the folder sorts chronologically in any file browser, and
    publisher before headline so two outlets covering one story do not collide
    on the title alone.
    """
    date = (row["published_at"] or "")[:10]
    parts = [part for part in (date, row["publisher"], row["title"]) if part]
    if len(parts) == 3:
        return f"{parts[0]} {parts[1]} - {parts[2]}"
    return " ".join(parts) or row["id"]


def _aliases_by_entity(
    conn: db.Conn, mappings: dict[str, str], names: NoteNames
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Every short form, folded onto the name that survived a merge.

    A surface that is ALREADY A NOTE NAME is dropped. Obsidian prefers a file
    to an alias, so leaving it in makes a link that looks like it points at one
    note and goes to another -- with nothing anywhere to say so.
    """
    collected: dict[str, list[str]] = {}
    acronyms: dict[str, list[str]] = {}
    seen: dict[str, set[str]] = {}
    for row in repo.aliases(conn):
        owner = mappings.get(row["entity_id"], row["entity_id"])
        if names.name_for(owner) is None:
            continue
        surface = row["surface"]
        if surface == names.name_for(owner) or names.is_taken(surface):
            continue
        if surface in seen.setdefault(owner, set()):
            continue
        seen[owner].add(surface)
        collected.setdefault(owner, []).append(surface)
        if row["is_acronym"]:
            acronyms.setdefault(owner, []).append(surface)
    for surfaces in collected.values():
        surfaces.sort()
    for surfaces in acronyms.values():
        surfaces.sort()
    return collected, acronyms


def _edges(
    conn: db.Conn,
    mappings: dict[str, str],
    by_id: dict[str, db.Row],
    predicates: set[str],
    symmetric: set[str],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[str]], int]:
    """Relationships, grouped and counted by PUBLISHER.

    `source_count` is distinct site domains, matching
    `services/graph_service.py::edge_provenance`. Two answers to "is this edge
    corroborated" that disagree is the defect the graph-health check exists to
    find, and an export that invented a third would be the worst of them --
    it leaves the building.
    """
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in repo.relation_assertions(conn):
        predicate = row["predicate"]
        if predicate not in predicates:
            continue
        subject = mappings.get(row["subject_id_raw"], row["subject_id_raw"])
        obj = mappings.get(row["object_id_raw"], row["object_id_raw"])
        if subject not in by_id or obj not in by_id or subject == obj:
            continue
        if predicate in symmetric and obj < subject:
            subject, obj = obj, subject
        entry = grouped.setdefault(
            (subject, predicate, obj), {"domains": set(), "articles": [], "quotes": []}
        )
        if row["site_domain"]:
            entry["domains"].add(row["site_domain"])
        if row["article_id"] and row["article_id"] not in entry["articles"]:
            entry["articles"].append(row["article_id"])
            if row["evidence_quote"]:
                entry["quotes"].append((row["article_id"], row["evidence_quote"]))

    by_entity: dict[str, list[dict[str, Any]]] = {}
    cited: dict[str, list[str]] = {}
    for (subject, predicate, obj), entry in sorted(grouped.items()):
        sources = max(len(entry["domains"]), 1)
        shared = {
            "predicate": predicate,
            "sources": sources,
            "articles": entry["articles"],
            "quotes": entry["quotes"],
            "symmetric": predicate in symmetric,
        }
        by_entity.setdefault(subject, []).append({**shared, "other": obj, "outgoing": True})
        by_entity.setdefault(obj, []).append({**shared, "other": subject, "outgoing": False})
        for endpoint in (subject, obj):
            for article in entry["articles"]:
                if article not in cited.setdefault(endpoint, []):
                    cited[endpoint].append(article)
    return by_entity, cited, len(grouped)


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def _cite_definitions(
    cited: dict[str, list[str]],
    by_id: dict[str, db.Row],
    definitions: dict[str, db.Row],
    gloss_by_entity: dict[str, db.Row],
) -> None:
    """Add each definition's own article to the note that carries it."""
    for source in (definitions, gloss_by_entity):
        for entity_id, row in sorted(source.items()):
            article = row["grounding_article_id"]
            if not article or entity_id not in by_id:
                continue
            if article not in cited.setdefault(entity_id, []):
                cited[entity_id].append(article)


def _verb(predicate: str) -> str:
    """`PART_OF` becomes `part of`.

    The same rule the app's own two `predicateLabel` helpers use, so a
    relationship is spelled one way whether it is read on a screen or in a
    folder. The shape of the two sentences is `scripts/export_primer.py`'s,
    copied rather than imported -- `scripts/` is not in the runtime image.
    """
    return predicate.lower().replace("_", " ")


def _edge_line(edge: dict[str, Any], names: NoteNames) -> str | None:
    link = names.link(edge["other"])
    if link is None:
        return None
    verb = _verb(edge["predicate"])
    tail = " · " + _plural(edge["sources"], "source")
    if edge["outgoing"] or edge["symmetric"]:
        return f"{verb} {link}{tail}"
    return f"{link} {verb} this{tail}"


def _entity_note(
    row: db.Row,
    *,
    names: NoteNames,
    aliases: list[str],
    acronyms: list[str],
    definition: db.Row | None,
    gloss: db.Row | None,
    edges: list[dict[str, Any]],
    cited: list[str],
) -> str:
    # THE SHORT FORM IS WHAT A READER LOOKS FOR, and it was only ever in the
    # frontmatter. A note headed `Australian Defence Force` said `ADF` nowhere a
    # person reads, because the only line here rendered the LONG form -- which
    # for a name like that one is the title, so the line suppressed itself.
    # Both directions now: the acronym for a name spelled out, the expansion for
    # a name that is an acronym.
    acronym = gloss["surface"] if gloss else (acronyms[0] if acronyms else None)
    if acronym == row["name"]:
        acronym = None
    expansion = row["expansion"] or (gloss["expansion"] if gloss else None)
    if expansion == row["name"]:
        expansion = None
    other_forms = [form for form in (acronym, expansion) if form]

    properties: dict[str, Any] = {
        "type": row["type"],
        "venona_id": row["id"],
        "defined": _defined(definition),
        "acronym": acronym,
        "expansion": expansion,
        "aliases": aliases,
        "tags": [_tag(row["type"])],
        "mentions": row["mention_count"] or 0,
        "sources": row["source_count"] or 0,
        "first_seen": (row["first_seen"] or "")[:10] or None,
        "last_seen": (row["last_seen"] or "")[:10] or None,
    }
    # OUTGOING ONLY, as properties. `supplied_by` on this note means this note
    # is supplied by the value; the same key written for an incoming edge would
    # claim the reverse fact. Both directions appear in the prose below, and
    # Obsidian's backlinks pane shows the reverse for free.
    for edge in edges:
        if not edge["outgoing"] and not edge["symmetric"]:
            continue
        link = names.link(edge["other"])
        if link is None:
            continue
        properties.setdefault(_verb(edge["predicate"]).replace(" ", "_"), []).append(f"{link}")

    out = [frontmatter(properties), "\n", notes.heading(1, row["name"])]

    if other_forms:
        written = " and ".join(f"**{form}**" for form in other_forms)
        out.append(f"\nAlso written {written}.\n")

    out.append("\n")
    out.append(notes.heading(2, "Definition"))
    out.append("\n")
    out.extend(_definition_body(definition, gloss, names))

    prose = [line for line in (_edge_line(edge, names) for edge in edges) if line]
    if prose:
        out.append("\n")
        out.append(notes.heading(2, "Relationships"))
        out.append("\n")
        out.extend(notes.bullet(line) for line in prose)

    source_links = [link for link in (names.link(article) for article in cited) if link]
    if source_links:
        out.append("\n")
        out.append(notes.heading(2, "Sources"))
        out.append("\n")
        out.extend(notes.bullet(link) for link in source_links)
    return "".join(out)


def _definition_body(
    definition: db.Row | None, gloss: db.Row | None, names: NoteNames
) -> list[str]:
    """What a name means, or an honest statement that nothing says.

    INVARIANT 3 IS THE WHOLE OF THIS FUNCTION. An undefined row is a real row,
    and the note says so in words rather than leaving a gap a reader would fill
    in from memory. Nothing here ever supplies an expansion that is not in the
    database, and the database only holds one that was traced to a sentence.

    A name with no definition can still have a SOURCED SHORT FORM, and 26 of
    them on the live corpus do. Saying only that nothing defines this, while
    holding a sentence that says where the acronym comes from, is a truthful
    sentence arranged into a false impression -- so the evidence is shown.
    """
    if not _defined(definition):
        out = [
            "**Venona has no sourced definition for this.** It appears in the reading, "
            "and no sentence in anything read so far says what it is.\n"
        ]
        if gloss and gloss["grounding_quote"] and gloss["expansion"]:
            out.append(f"\nWhat it does have is where **{gloss['surface']}** comes from:\n\n")
            out.append(
                notes.quote(
                    gloss["grounding_quote"],
                    attribution=names.link(gloss["grounding_article_id"] or ""),
                )
            )
        return out
    out = [f"{definition['definition']}\n"]
    quote = definition["grounding_quote"]
    if quote:
        attribution = names.link(definition["grounding_article_id"] or "")
        out.append("\n")
        out.append(notes.quote(quote, attribution=attribution))
        return out
    # NO BLOCKQUOTE. A tier-2 definition is drawn from several sources and
    # carries no single sentence, and contract C8 forbids presenting one as a
    # quotation. Say how it was arrived at instead.
    article_ids = json.loads(definition["source_article_ids"] or "[]")
    if article_ids:
        out.append(
            f"\nDrawn from {_plural(len(article_ids), 'source')}. No single sentence states it.\n"
        )
    return out


def _term_note(row: db.Row, *, names: NoteNames) -> str:
    defined = row["status"] != "undefined" and bool(row["expansion"])
    properties: dict[str, Any] = {
        "type": "term",
        "venona_id": row["id"],
        "term": row["surface"],
        "expansion": row["expansion"] if defined else None,
        "defined": defined,
        "tags": ["venona/term"],
        "sightings": row["sighting_count"] or 0,
    }
    out = [frontmatter(properties), "\n", notes.heading(1, row["surface"]), "\n"]
    if not defined:
        out.append(
            "**Venona has no sourced expansion for this.** It appears in the reading, "
            "and nothing read so far says what it stands for.\n"
        )
        return "".join(out)
    out.append(f"{row['expansion']}\n")
    if row["grounding_quote"]:
        out.append("\n")
        out.append(
            notes.quote(
                row["grounding_quote"],
                attribution=names.link(row["grounding_article_id"] or ""),
            )
        )
    elif row["corroboration_count"]:
        out.append(
            f"\nDrawn from {_plural(row['corroboration_count'], 'source')}. "
            "No single sentence states it.\n"
        )
    return "".join(out)


def _quotes_by_document(
    edges: dict[str, list[dict[str, Any]]],
    definitions: dict[str, db.Row],
    gloss_rows: list[db.Row],
    names: NoteNames,
    mappings: dict[str, str],
) -> dict[str, list[str]]:
    """The verbatim sentences each source is carrying, and what they back.

    This is the whole of what an article contributes to the folder. The body
    text is never selected, never rendered, and never anywhere near here --
    `db/repositories/export.py::documents` says why.
    """
    collected: dict[str, list[str]] = {}

    def add(article_id: str | None, claim: str, quote: str | None) -> None:
        if not article_id or not quote:
            return
        entry = f"{claim}\n" + notes.quote(quote)
        if entry not in collected.setdefault(article_id, []):
            collected[article_id].append(entry)

    for owner, group in sorted(edges.items()):
        for edge in group:
            if not edge["outgoing"]:
                continue
            subject = names.link(owner)
            other = names.link(edge["other"])
            if not subject or not other:
                continue
            for article_id, quote in edge["quotes"]:
                add(article_id, f"- {subject} {_verb(edge['predicate'])} {other}", quote)

    for entity_id, row in sorted(definitions.items()):
        link = names.link(entity_id)
        if link and row["grounding_quote"]:
            add(row["grounding_article_id"], f"- {link} is defined", row["grounding_quote"])

    for row in gloss_rows:
        key = (
            row["id"] if not row["entity_id"] else mappings.get(row["entity_id"], row["entity_id"])
        )
        link = names.link(key)
        if link and row["grounding_quote"] and row["expansion"]:
            add(
                row["grounding_article_id"],
                f"- {link} means {row['expansion']}",
                row["grounding_quote"],
            )
    return collected


def _source_note(row: db.Row, *, names: NoteNames, quoted: list[str]) -> str:
    properties: dict[str, Any] = {
        "type": "source",
        "venona_id": row["id"],
        "title": row["title"],
        "publisher": row["publisher"],
        "author": row["author"],
        "published": (row["published_at"] or "")[:10] or None,
        "url": row["url"],
        "tags": ["venona/source"],
    }
    out = [frontmatter(properties), "\n", notes.heading(1, row["title"] or row["url"]), "\n"]
    where = ", ".join(part for part in (row["publisher"], (row["published_at"] or "")[:10]) if part)
    if where:
        out.append(f"{where}. [Read it]({row['url']})\n")
    else:
        out.append(f"[Read it]({row['url']})\n")
    extract = _extract(row["excerpt"])
    if extract:
        out.append("\n")
        out.append(notes.quote(extract))
    if quoted:
        out.append("\n")
        out.append(notes.heading(2, "Quoted here"))
        out.append("\n")
        out.append("\n".join(quoted))
    return "".join(out)


def _brief_members(
    conn: db.Conn,
    brief_rows: list[db.Row],
    names: NoteNames,
    mappings: dict[str, str],
) -> list[Member]:
    """Each brief as a note, with every mention it made turned into a link.

    This is the part of the folder worth the most. On the screen a brief's
    programmes and short forms are hoverable; here they are followable, which
    is the same layer rebuilt in the medium Obsidian has.
    """
    items_by_brief: dict[str, list[db.Row]] = {}
    for row in repo.brief_items(conn):
        items_by_brief.setdefault(row["brief_id"], []).append(row)
    blocks_by_brief: dict[str, list[db.Row]] = {}
    for row in repo.brief_blocks(conn):
        blocks_by_brief.setdefault(row["brief_id"], []).append(row)

    members: list[Member] = []
    for brief in brief_rows:
        out = [
            frontmatter(
                {
                    "type": "brief",
                    "venona_id": brief["id"],
                    "date": brief["date"],
                    "title": brief["title"],
                    "tags": ["venona/brief"],
                }
            ),
            "\n",
            notes.heading(1, brief["title"] or brief["date"]),
        ]
        if brief["dek"]:
            out.append(f"\n*{brief['dek']}*\n")

        blocks = blocks_by_brief.get(brief["id"], [])
        items = items_by_brief.get(brief["id"], [])
        if any(block["item_id"] for block in blocks):
            out.extend(_brief_by_item(items, blocks, names, mappings))
        else:
            out.extend(_brief_by_heading(blocks, names, mappings))
        members.append(
            Member(path=f"{BRIEFS_DIR}/{names.name_for(brief['id'])}.md", text="".join(out))
        )
    return members


def _paragraph(block: db.Row, names: NoteNames, mappings: dict[str, str]) -> str:
    text = notes.splice(
        block["text"] or "",
        _resolved_spans(block["spans"], mappings),
        json.loads(block["marks"] or "[]"),
        names,
    )
    if not text.strip():
        return ""
    # The role is the reader's cue for what a paragraph is doing.
    # `brief_service.assemble_body` keeps the two apart on the screen for the
    # same reason: a summary and a judgement about it are different claims and
    # must not run together.
    if block["role"] == "why_it_matters":
        return f"\n**Why it matters.** {text}\n"
    return f"\n{text}\n"


def _brief_by_item(
    items: list[db.Row],
    blocks: list[db.Row],
    names: NoteNames,
    mappings: dict[str, str],
) -> list[str]:
    """A brief written as stories, which is how they are written now."""
    by_item: dict[str, list[db.Row]] = {}
    for block in blocks:
        by_item.setdefault(block["item_id"] or "", []).append(block)
    out: list[str] = []
    section = None
    for item in items:
        if item["section"] != section:
            section = item["section"]
            out.append("\n")
            out.append(notes.heading(2, str(section)))
        out.append("\n")
        out.append(notes.heading(3, item["headline"] or ""))
        out.extend(_paragraph(block, names, mappings) for block in by_item.get(item["id"], []))
    return out


def _brief_by_heading(
    blocks: list[db.Row],
    names: NoteNames,
    mappings: dict[str, str],
) -> list[str]:
    """A brief written before stories were rows, grouped by its heading runs.

    `brief_service.assemble_body` carries the same fallback, and for the same
    reason: the archive predates the item model, and a reader opening an old
    brief in the folder would otherwise get a page of headings with no prose
    under any of them -- which reads as a brief that said nothing.
    """
    out: list[str] = []
    heading = None
    for block in blocks:
        if block["heading"] and block["heading"] != heading:
            heading = block["heading"]
            out.append("\n")
            out.append(notes.heading(2, str(heading)))
        out.append(_paragraph(block, names, mappings))
    return out


def _resolved_spans(raw: str | None, mappings: dict[str, str]) -> list[dict[str, Any]]:
    """A brief's mentions, pointed at the name that survived.

    `brief_blocks.spans` holds the id the extraction saw, and a merge decided
    months later moves it. Linking the raw id would quietly drop the mention --
    every "Pentagon" in every brief, in this corpus -- because the note it names
    is the one the merge removed. The reader would see a term that is plainly a
    programme rendered as flat text and have no way to know why.
    """
    spans = json.loads(raw or "[]")
    for span in spans:
        ref = span.get("ref")
        if ref:
            span["ref"] = mappings.get(ref, ref)
    return spans


def _readme(domain: Any, *, now: str, counts: dict[str, int], names_dir: str) -> str:
    """What the folder is, in the words of whoever is opening it.

    Spliced from the domain descriptor, so nothing here names a subject and
    `make lint-domain` stays satisfied.
    """
    total = counts["names"] + counts["terms"] + counts["sources"] + counts["briefs"]
    return (
        f"# {domain.display_name}\n"
        "\n"
        "This folder is an Obsidian vault. Open Obsidian and choose "
        '"Open folder as vault".\n'
        "\n"
        f"- **{names_dir}**: everything Venona has read about, one note each, with its "
        "relationships and the sources behind them.\n"
        f"- **{TERMS_DIR}**: short forms and what they stand for, each traced to a sentence.\n"
        f"- **{SOURCES_DIR}**: the articles, carrying the exact sentences that back a "
        "definition or a relationship. The article text itself is never copied here.\n"
        f"- **{BRIEFS_DIR}**: what Venona wrote, with every name linked.\n"
        "\n"
        f"Written {now[:10]}. {total} notes.\n"
        "\n"
        "Venona will not expand a term it cannot trace to a sentence in a source. "
        "A note saying nothing defines it means exactly that.\n"
    )
