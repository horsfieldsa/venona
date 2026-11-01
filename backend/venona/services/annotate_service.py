"""Annotate arbitrary text against the current alias index.

This is what lets a browser extension put venona's underlines on somebody
else's article. It is the same code path a brief goes through -- `build_index`,
`annotate_blocks`, `blocks_to_runs` -- with two differences that both come from
the same fact: this text is not ours.

WHAT IS DIFFERENT FROM A BRIEF, AND WHY.

`strict_case=True`. `tier_a` on a brief is that brief's retrieval set, so a
strictness-1 alias is already gated to an entity the pipeline decided the
document is about. Here `tier_a` is the whole corpus -- there is no retrieval
set for a page we did not fetch -- and strictness-1 entries live in the FOLDED
automaton, so widening it makes 525 of 1003 aliases looser than the tier
reserved for all-caps acronyms. Measured, before the flag existed:

    'a lattice of horizon effects on the foundry floor'
        -> plat:anduril-s-lattice, plat:horizon, plat:palantir-foundry, all @ 0.9

Three confident underlines, none of them real. See `AliasIndex.find`.

NOTHING IS NORMALISED. `markdown_to_blocks` applies NFC because it owns what
gets stored and every span offset in the database points into that exact
string. This function owns nothing: it is handed text a browser extracted from
a live DOM and must hand back runs that reassemble into precisely that text,
because the caller splices them back into the same DOM nodes. `blocks_to_runs`
slices `block.text` and nothing else, so `"".join(runs) == text` holds for any
input, NFC or not. Normalising here would change the string's LENGTH and every
splice after a composed character would land on the wrong word.

NOTHING IS WRITTEN. No run, no row, no record of what anybody read. This is
pure compute over the index, and `tests/test_annotate_endpoint.py` snapshots
table counts across a call to keep it that way.

THE INDEX CACHE IS GUARDED BY VALUES, NOT BY COUNTS, and that distinction is
the whole of its safety. `build_index` measures ~84ms on a 1900-entry index and
a brief read calls this once for the title and once per headline, so the read
path rebuilt the same index a dozen times to render one page. But the
mutations that change the index are UPDATEs -- `acronym_definitions.status`
going to `active`, which is exactly what grounding a term from the extension
does; `entity_id` being set; status swaps in `entities` -- so a cache keyed on
row counts would keep serving `undefined` for a term the reader had just
grounded, permanently. `alias_index.build_index` therefore hashes the columns
it reads. See `select_index_fingerprint`.
"""

from __future__ import annotations

import hashlib
from typing import Any

from venona.db import core as db
from venona.db.repositories import annotate as repo
from venona.services import alias_index
from venona.services.alias_index import build_index
from venona.services.annotate import Block, Mark, annotate_blocks, blocks_to_runs, verify_runs


def index_version(conn: db.Conn) -> str:
    """A short token that changes when the index probably changed.

    A CLIENT CACHE HINT, not a correctness mechanism -- nothing on this side
    trusts it. The extension uses it to stamp annotated blocks so it can skip
    re-annotating them, and to decide when its card cache is stale. Being coarse
    costs a card that is at most one run out of date, which is the same trade
    `stores/entities.ts` already makes with a ten-minute TTL. Being wrong in the
    other direction -- claiming freshness that isn't there -- is what the
    absence of a server-side cache makes impossible.
    """
    row = repo.select_run_events(conn).fetchone()
    raw = f"{row['events']}:{row['aliases']}:{row['glossary']}:{row['merges']}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def annotate_texts(
    conn: db.Conn, blocks: list[tuple[str, str, list[tuple[int, int]]]]
) -> tuple[list[tuple[str, list[dict[str, Any]]]], list[str]]:
    """Annotate `(id, text, hrefs)` triples. Returns `(id, runs)` pairs and refs.

    The ref set is what the caller feeds to `POST /entities/cards:batch` -- one
    round trip for the whole page, after which every hover is a client-side map
    lookup.
    """
    index = build_index(conn)
    tier_a = frozenset(row["id"] for row in repo.select_entities(conn))
    senses = alias_index.senses(conn)

    # Context for ambiguous acronyms is the whole page, for the same reason a
    # brief uses the whole brief: an acronym spelled out in the second paragraph
    # and used bare in the fifth is the ordinary way people write, and resolving
    # per-block would miss it.
    context = "\n".join(text for _, text, _hrefs in blocks)
    # Once for the page, with the SAME gates the annotating pass uses, then
    # handed to every block. `annotate_blocks` is called per block below, so
    # computing this inside it would ask "is the entity named in this
    # paragraph" instead of "on this page".
    present = alias_index.refs_present(index, context, tier_a=tier_a, strict_case=True)

    out: list[tuple[str, list[dict[str, Any]]]] = []
    refs: list[str] = []
    seen: set[str] = set()

    for block_id, text, hrefs in blocks:
        # `href` marks, the same shape `markdown_to_blocks` produces, so
        # `annotate_blocks` suppresses spans inside links by the one code path
        # rather than the caller pre-deleting the text.
        marks = [Mark(start=start, end=end, type="href") for start, end in hrefs]
        block = Block(id=block_id, type="paragraph", depth=0, text=text, marks=marks, spans=[])
        annotate_blocks(
            [block],
            index,
            tier_a=tier_a,
            senses=senses,
            context=context,
            present_refs=present,
            strict_case=True,
        )
        runs = blocks_to_runs(block)
        if not verify_runs(block, runs):
            # Not `assert`: `python -O` strips those, and this is the invariant
            # the caller's DOM surgery is built on. A block whose runs do not
            # reassemble would splice text into the wrong nodes.
            raise RuntimeError(f"runs do not reassemble block {block_id!r}")
        out.append((block_id, runs))

        for span in block.spans:
            ref = span["ref"]
            if ref not in seen:
                seen.add(ref)
                refs.append(ref)

    return out, refs
