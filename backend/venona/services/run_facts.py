"""A run row -> what it produced, in the words its kind declared (contract C11).

`run_types.RunType.facts` and `.lines` say WHAT a kind of run reports and in
what words; this resolves them against one row. It is pure apart from the single
`users` lookup `shape="reader"` needs, so every rule below is testable offline
against a dict.

It lives beside the table rather than in `api/schemas/run.py` for the reason
`decorate` gives for resolving `outcome_text` server-side: every surface that
reports a finished run needs the same answer, and a second copy of these rules
in TypeScript would drift inside a release.

Three rules carry the weight:

  * **An absent key is omitted, never zeroed.** A run written before a key
    existed shows fewer facts, not a row of blanks and zeroes claiming nothing
    happened. This is what lets the declaration grow without a migration.
  * **A counted slot resolving to ZERO does not fill.** `Read 6 pages and none
    of them defined it` and `No page could be read` are the same outcome and a
    different fact about the world, and presence-based filling collapses them
    into `Read 0 pages and none of them defined it`. Zero takes the next
    candidate; a presence-only slot (`{?counts.pages_read}`) is how the
    zero-specific sentence still requires the key to have been recorded at all.
  * **The last candidate carries no slots**, so a kind that can reach an outcome
    always has something to say about it. `tests/test_run_facts.py` pins that.
"""

from __future__ import annotations

import re
from typing import Any

from venona import db
from venona.db.repositories import users as users_repo
from venona.services import run_types

#: Three forms. `{counts.stories}` splices a value, `{counts.stories|story|
#: stories}` splices it with its noun, and `{?counts.pages_read}` splices
#: NOTHING but requires the key to be present.
#:
#: The third exists because zero and absent are different sentences. A lookup
#: that read no pages knows it read none; one written before `pages_read` was
#: recorded knows nothing, and must not be given the confident sentence. Without
#: it the slotless "No page could be read" candidate fills for both.
#:
#: Keys are qualified on purpose: `counts` is itself merged across stages, so a
#: bare `{documents_new}` would have three possible sources and no stated winner.
_SLOT = re.compile(r"\{(\?)?([a-z_]+\.[a-z_0-9]+)(?:\|([^|{}]*)\|([^|{}]*))?\}")


def namespaces(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The three places a declared key may point.

    `counts` merges every stage's counts in stage order, later wins. A pipeline
    run's stages write disjoint keys, so the merge is a union in practice; the
    ordering rule is stated so that the day two stages do collide, the answer is
    the later stage rather than whichever the database returned first.
    """
    counts: dict[str, Any] = {}
    for stage in run.get("stages") or []:
        counts.update(stage.get("counts") or {})
    return {
        "counts": counts,
        "params": run.get("params") or {},
        # The run row itself, for the columns that outlive a stage's metadata --
        # `brief_id` above all, which the brief stage reports in its event
        # payload rather than in the counts that get persisted.
        "run": run,
    }


def _lookup(space: dict[str, dict[str, Any]], key: str) -> Any:
    head, _, tail = key.partition(".")
    return space.get(head, {}).get(tail)


def _fillable(value: Any) -> bool:
    """Whether a slot has something to say.

    Zero is deliberately not fillable; see the module docstring. `False` is
    excluded with it, since `bool` is an `int` and a false flag reads the same
    way in a sentence.
    """
    if value is None or value == "":
        return False
    return not (isinstance(value, (int, float)) and value == 0)


def _fill(template: str, space: dict[str, dict[str, Any]]) -> str | None:
    """One candidate sentence, or None when a slot has nothing to put in it."""
    out, last = [], 0
    for match in _SLOT.finditer(template):
        presence_only, key, singular, plural = match.groups()
        value = _lookup(space, key)
        if presence_only:
            # Present at all, zero included. Absent still fails the candidate.
            if value is None:
                return None
            out.append(template[last : match.start()])
            last = match.end()
            continue
        if not _fillable(value):
            return None
        out.append(template[last : match.start()])
        if singular is None:
            out.append(str(value))
        else:
            out.append(f"{value} {singular if value == 1 else plural}")
        last = match.end()
    out.append(template[last:])
    return "".join(out)


def resolve_line(run: dict[str, Any]) -> str | None:
    """The sentence under this run's verdict chip.

    Keyed on `(status, outcome)` rather than on `outcome` alone, because a run
    that succeeded may legitimately have no outcome: `orchestrator` leaves it
    unset when no brief was asked for (`--only fetch`, `skip_brief`) and clears
    it when an exception lands after earlier stages committed, and migration
    0018 left it NULL on every historical row it could not classify. Those are
    most of what someone clicking back through history lands on.
    """
    run_type = run_types.type_for(run.get("kind", ""))
    status, outcome = run.get("status"), run.get("outcome")
    if outcome:
        space = namespaces(run)
        for candidate in run_type.lines.get(outcome, ()):
            filled = _fill(candidate, space)
            if filled is not None:
                return filled
    # A failed run says why, in its own words, before falling back to ours.
    if status == "failed" and run.get("error"):
        return str(run["error"])
    return run_types.STATUS_LINES.get(str(status)) if status else None


def resolve_facts(run: dict[str, Any], *, conn: db.Conn | None = None) -> list[dict[str, Any]]:
    """The declared facts this row can actually answer, in declared order."""
    run_type = run_types.type_for(run.get("kind", ""))
    space = namespaces(run)
    out: list[dict[str, Any]] = []
    for fact in run_type.facts:
        value = _lookup(space, fact.key)
        if value is None or value == "" or value == []:
            continue
        # Zero is dropped for a COUNT and kept for everything else: a fact
        # reading `names new 0` is eleven zeroes in a trench coat, which is the
        # noise `summary` was introduced to stop, while a false flag is an
        # answer ("already held") and has to render.
        if fact.shape == "count" and value == 0:
            continue
        text = _text_for(fact, value, conn=conn)
        # A reader we cannot name is dropped rather than rendered as the user id
        # underneath it. CLAUDE.md bans database values on a surface a person
        # reads, and "written for user:0193c8..." is exactly that.
        if fact.shape == "reader" and not text:
            continue
        out.append(
            {
                "key": fact.key,
                "label": fact.label,
                "shape": fact.shape,
                "value": value,
                "text": text,
                "href": fact.href.replace("{value}", str(value)) if fact.href else None,
            }
        )
    return out


def _text_for(fact: run_types.Fact, value: Any, *, conn: db.Conn | None) -> str | None:
    """What a reader sees, when that is not just the value.

    None means "render the value", which is every count and every string.
    """
    if fact.shape == "flag" and len(fact.words) == 2:
        return fact.words[0] if value else fact.words[1]
    if fact.shape == "reader":
        # `users.by_id` already selects both, so this costs one query and no new
        # SQL. The display name first: it is what the person calls themselves,
        # and the address is the fallback every other actor column uses (C1).
        if conn is None:
            return None
        row = users_repo.by_id(conn, str(value))
        return (row["display_name"] or row["email"]) if row else None
    return None


def subject_text(run: dict[str, Any], key: str | None) -> str | None:
    """What a run was about, for the one column of the history list.

    A list of nine rows all reading "Definition" is not a history. `RunsView`
    worked this out from a cascade over seven param names, which is the same
    kind-inference the result panel was doing and got wrong the same way: it
    returned nothing at all for a reader's brief.

    Deliberately NOT the reader-resolving path `facts` uses. This runs once per
    row of a listing, and one `users` query per row to render a subject column
    is not a trade worth making; the detail page names the person.
    """
    if not key:
        return None
    value = _lookup(namespaces(run), key)
    if value is None or value == "":
        return None
    if isinstance(value, (list, tuple)):
        return f"{len(value)} of them" if value else None
    return str(value)
