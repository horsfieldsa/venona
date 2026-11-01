"""Check the claims comments make, for the `comments` skill.

The cheap classes only, and deliberately: a path that does not resolve, a
symbol that is gone, an ADR that has been superseded, a figure with no date or
version to age it. None needs judgement, all of them go stale silently, and a
reviewer who has to grep for each one by hand will not do it for 694 files.

WHAT IT DOES NOT DO is decide whether a comment is TRUE. "The event loop blocks
here" is not checkable by a script and is exactly the kind of claim worth a
person's attention -- so this narrows the field rather than clearing it, and
the skill's own table carries the classes a person still has to read.

    uv run python scripts/check_comment_claims.py                # whole repo
    uv run python scripts/check_comment_claims.py backend/venona/db
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from lint_comments import comments_of, sources  # noqa: E402

#: A path-shaped token inside a comment. Anchored on a real suffix, because
#: prose is full of dotted words and `e.g.` is not a file.
PATH = re.compile(
    r"\b((?:[\w.-]+/)+[\w.-]+\.(?:py|ts|tsx|vue|css|mjs|js|yaml|yml|sql|md|sh|toml))\b"
)
#: The same file named with no directory: a seed_entities.yaml cited in
#: `pipeline/resolve.py` that has never existed. PATH cannot see it, because
#: PATH needs a slash to tell a path from prose; the BACKTICKS do that job
#: here, and `\w` first keeps a bare suffix like `.dom.spec.ts` from reading as
#: a name. The suffixes are only those a bare name RELIABLY means a file in
#: this tree: `.js`/`.mjs` name build output, `.md` names an exported note, and
#: a bare `.css` is a dependency's (`index.css`). All three are correct to
#: cite and none is ever in the tree, so checking them only cries wolf.
BARE_FILE = re.compile(r"`(\w[\w.-]*\.(?:py|ts|tsx|vue|yaml|yml|sql|sh|toml))`")
#: A backticked symbol, with or without its call parentheses, and with or
#: without a qualifier in front: `_cutoff`, `note_new_alias`, `_table()`,
#: `UserRecord.NOTHING`, `pipeline/curate.py::model_merges`. Only the last
#: segment is checked. None of these was looked for until 2026-09-14, and the
#: qualified forms went another batch unchecked -- which is the shape most
#: cross-module claims take, and the shape this exists for.
SYMBOL = re.compile(r"`(?:[\w./]+(?:\.|::))*([A-Za-z_][A-Za-z0-9_]{3,})(?:\(\))?`")
#: A migration cited by NUMBER. The 2026-08-17 Postgres port squashed every
#: migration then existing into `0001_baseline.sql` and the numbers were reused,
#: so a citation written before that date now points at a real but WRONG file --
#: which is worse than a dead one, because it resolves. Five were found this way.
#: A comment naming the `.sql` file instead is exempt: that form ages loudly.
MIGRATION = re.compile(r"migration\s+(\d{4})", re.I)
MIGRATION_FILE = re.compile(r"\b\d{4}_[a-z_]+\.sql\b")
SQUASH = "2026-08-17"
#: `(?!-\d)` because `ADR 2026-09-01` is a date somebody wrote after the word,
#: and reading its year as a record number reports a record that never existed.
ADR = re.compile(r"\b(?:ADR|adr/)\s*0?(\d{3,4})\b(?!-\d)", re.I)
#: A measurement worth checking carries a FIGURE -- a number somebody could go
#: and re-take. One without ("measured rather than assumed") cannot be re-taken
#: by anybody, so flagging it teaches a reader to skim the output, which is the
#: one failure a checker does not recover from.
MEASURED = re.compile(
    r"\b(?:measured|verified|observed)\b[^.]{0,120}?\d"
    r"|\d[^.]{0,120}?\b(?:measured|verified|observed)\b",
    re.I,
)
DATE = re.compile(r"\b20\d\d-\d\d-\d\d\b")
#: Digits that are IDENTIFIERS, not readings. A tier number beside the word
#: "verified" is what a tier means, not something anybody measured, and reading
#: it as a figure is how this check starts crying wolf.
LABEL = re.compile(r"\btier[\s-]?\d|\bC\d{1,2}\b|\bADR\s*\d+|\binvariant \d", re.I)
#: A version pin ages a claim the way a date does: it names what to re-take it
#: against, and `verified against 2.1.220` is re-runnable years later.
VERSION = re.compile(r"\b\d+\.\d+(?:\.\d+)?\b")
#: Claims wrap across lines; the patterns above read one sentence at a time.
FLAT = re.compile(r"\s+")


def _ours(ref: str) -> bool:
    """Is this a path in THIS repo, or a library's?

    `starlette/routing.py` is a real file in a dependency and will never resolve
    here, so flagging it is noise -- and noise is what stops a checker being
    run. The first segment has to name something at the top of the tree, or an
    interior directory, for the reference to be ours to check.
    """
    head = ref.split("/", 1)[0]
    if (REPO_ROOT / head).exists():
        return True
    return any(known.parent.name == head or head in known.parts for known in _repo_files())


def _resolves(ref: str, origin: Path) -> bool:
    """Does this reference name something that exists?

    EVERY ANCESTOR OF THE FILE IS A BASE, not a hardcoded root list. A comment
    in `db/repositories/notify.py` naming `repositories/relevance.py` means the
    sibling package, which is how a person would write it and what a fixed list
    of roots gets wrong -- that exact reference was the first false positive
    this script produced, and a checker that cries wolf is one nobody runs.
    """
    if (origin.parent / ref).exists():
        return True
    base = origin.parent
    while True:
        if (base / ref).exists():
            return True
        if base == REPO_ROOT:
            break
        base = base.parent
    # A SUFFIX MATCH, because comments elide the middle. `routers/curation.py`
    # means `api/routers/curation.py` to a reader, and demanding the full path
    # would flag most correct references in the repo. Falsely clearing a truly
    # dead path needs a real file whose tail matches, which is the rarer and
    # much cheaper mistake.
    return any(str(known).endswith("/" + ref) for known in _repo_files())


_FILE_INDEX: list[Path] = []


def _repo_files() -> list[Path]:
    """Every tracked-looking file, once. Built lazily because a whole-repo walk
    is wasted on a run that finds no unresolved path at all."""
    if not _FILE_INDEX:
        skip = {".git", "node_modules", ".venv", "__pycache__", "dist", "data", "backups"}
        for path in REPO_ROOT.rglob("*"):
            if path.is_file() and not skip & set(path.relative_to(REPO_ROOT).parts):
                _FILE_INDEX.append(path.relative_to(REPO_ROOT))
    return _FILE_INDEX


_WORDS: set[str] = set()


def _identifiers() -> set[str]:
    """Every word appearing in any text file in the repo, for the symbol check.

    A symbol is called gone only when its name appears NOWHERE -- not when it
    moved module, not when its arity changed. Anything tighter needs a parser
    per language, and would flag a name defined in a `.sql` trigger or one only
    ever assembled by string concatenation.
    """
    if not _WORDS:
        # NO `.md`. A symbol has to exist in CODE, and documentation is where a
        # dead one goes on living: `HARNESSES` was cleared by the two docs that
        # still told a reader to edit it, and it has never existed.
        texts = set(".py .ts .tsx .vue .css .mjs .js .sql .yaml .yml .toml .sh".split())
        for rel in _repo_files():
            if rel.suffix in texts:
                body = (REPO_ROOT / rel).read_text(encoding="utf8", errors="replace")
                _WORDS.update(re.findall(r"\w+", body))
    return _WORDS


def _superseded() -> dict[str, str]:
    """ADR number to the record that replaced it, or to "" when nothing says.

    THE RECORD'S OWN `status:` DECIDES, because supersession here is often
    PARTIAL: `0168` carries `supersedes: 0141 (the role claim's authority to
    RAISE; everything else stands)`, and 0141 still says `status: accepted`
    for the half that stands. Trusting the `supersedes` key alone called 0141,
    0128 and 0148 reversed -- 12 of 37 findings, a third of the output, every
    one of them wrong.

    The replacement is still read from BOTH directions: most superseded records
    here carry no `superseded-by` key and are named only from the other side.
    """
    heads = {
        path.name[:4]: path.read_text(encoding="utf8", errors="replace")[:400]
        for path in (REPO_ROOT / "docs" / "adr").glob("0*.md")
    }
    replaced_by: dict[str, str] = {}
    for num, head in heads.items():
        for target in re.findall(r"supersedes:\s*(\d+)", head):
            replaced_by.setdefault(target.zfill(4), num)

    out: dict[str, str] = {}
    for num, head in heads.items():
        if re.search(r"status:\s*superseded", head):
            match = re.search(r"superseded-by:\s*(\d+)", head)
            out[num] = match.group(1) if match else replaced_by.get(num, "")
    return out


def _written_before(needle: str, rel: str, when: str) -> bool:
    """Was this phrase in this file before `when`?

    `git log -S` on the phrase, not `git log -L` on the line: a line carries the
    date of its own first appearance, which for an edited line predates the
    claim that line carries today. Asked of a migration citation, `-L` therefore answers
    "pre-squash" for text somebody typed last week.
    """
    out = subprocess.run(
        ["git", "log", "--format=%ad", "--date=short", "-S", needle, "--", rel],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    ).stdout.split()
    return bool(out) and out[-1] < when


def _adrs() -> set[str]:
    return {p.name[:4] for p in (REPO_ROOT / "docs" / "adr").glob("0*.md")}


def _ambiguous() -> set[str]:
    """Numbers two records share, where a citation names neither of them."""
    seen: dict[str, int] = {}
    for path in (REPO_ROOT / "docs" / "adr").glob("0*.md"):
        seen[path.name[:4]] = seen.get(path.name[:4], 0) + 1
    return {num for num, n in seen.items() if n > 1}


def blocks(path: Path) -> list[tuple[int, int, str]]:
    """`comments_of` spans, with a run of `#` lines joined into one comment.

    Python's tokenizer hands back one span per line, so a claim and the date
    that ages it sit in different spans and the date is never found -- and a
    sentence that wraps is read as two half-sentences.
    """
    out: list[list] = []
    for start, end, body in sorted(comments_of(path)):
        if out and start == out[-1][1] + 1 and end == start and out[-1][3]:
            out[-1][1] = end
            out[-1][2] += " " + body.lstrip("#").strip()
            continue
        out.append([start, end, body, start == end])
    return [(a, b, c) for a, b, c, _ in out]


def main() -> int:
    targets = sys.argv[1:]
    superseded, known, ambiguous = _superseded(), _adrs(), _ambiguous()
    findings: list[str] = []

    for path in sources():
        rel = str(path.relative_to(REPO_ROOT))
        if targets and not any(rel.startswith(t.rstrip("/")) for t in targets):
            continue
        for start, _, body in blocks(path):
            where = f"{rel}:{start}"

            # A path after `TRIGGER:` names the file that does NOT exist yet --
            # the condition for creating it is the whole point of the line.
            declared = body.split("TRIGGER:")[0]
            for ref in set(PATH.findall(declared)):
                if _ours(ref) and not _resolves(ref, path):
                    findings.append(f"{where}: names `{ref}`, which resolves to nothing")
            # A bare name has no directory to judge ownership by, so it is
            # taken as ours: a comment here naming a dependency's file without
            # its path is rare, and `_resolves` clears it on the suffix anyway.
            for ref in set(BARE_FILE.findall(declared)):
                if not _resolves(ref, path):
                    findings.append(f"{where}: names `{ref}`, which resolves to nothing")
            # An underscore or a capital is what separates a symbol from an
            # ordinary backticked word. `running`, `queued` and `failed` are
            # wire values, and a checker that reads those as symbols is one
            # nobody runs.
            for sym in set(SYMBOL.findall(declared)):
                if ("_" in sym or not sym.islower()) and sym not in _identifiers():
                    findings.append(f"{where}: names `{sym}`, which exists nowhere in the repo")

            if not MIGRATION_FILE.search(declared):
                for num in set(MIGRATION.findall(declared)):
                    if _written_before(f"migration {num}", rel, SQUASH):
                        findings.append(
                            f"{where}: cites migration {num} in text written before the "
                            f"{SQUASH} squash -- that number now names a different file"
                        )

            cited = {n.lstrip("0") for n in ADR.findall(body)}
            for num in set(ADR.findall(body)):
                num = num.zfill(4)
                if num not in known:
                    findings.append(f"{where}: cites ADR {num}, which does not exist")
                elif num in ambiguous:
                    findings.append(f"{where}: cites ADR {num}, which two records share")
                elif num in superseded and (superseded[num] or "0").lstrip("0") not in cited:
                    # Naming the superseding record in the same comment IS the
                    # check having been done: the reader is pointed at the live
                    # decision, and a part of a superseded one can still be the
                    # thing being cited.
                    findings.append(
                        f"{where}: cites ADR {num}, superseded by "
                        f"{superseded[num] or 'a record that does not name itself'} -- "
                        f"check it is not arguing the reversed decision"
                    )

            flat = FLAT.sub(" ", body)
            figures = LABEL.sub(" ", flat)
            if MEASURED.search(figures) and not (DATE.search(flat) or VERSION.search(flat)):
                findings.append(f"{where}: measures a figure with nothing to age it out")

    for item in findings:
        print(item)
    scope = ", ".join(targets) if targets else "the repo"
    print(f"\n{len(findings)} claim(s) to check across {scope}.")
    # Never a non-zero exit: this is a worklist for a person, not a gate. Making
    # it fail `make lint` would turn an undated measurement into something
    # somebody deletes at 5pm to get a push through.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
