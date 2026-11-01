"""What each note is called, decided once for the whole folder.

A wikilink resolves BY NAME ACROSS THE WHOLE VAULT, not by path, so two notes
called the same thing in different folders are ambiguous and Obsidian picks one
of them. That makes uniqueness a property of the folder rather than of any
directory in it, which is why every name is claimed through one registry before
a single file is written.

The registry is also what makes a dead link impossible. Nothing anywhere builds
a link out of a name -- every link goes through `link()`, which answers `None`
for anything that never got a note, and the caller writes plain text instead.
An unresolved wikilink is not a cosmetic miss in Obsidian: it draws as a
phantom node in the graph view, so a folder full of them misrepresents the
corpus in the exact surface this whole feature exists to produce.
"""

from __future__ import annotations

#: Obsidian's own list plus the operating system's. Both from the same place in
#: the help: `# | ^ [ ] :` mean something inside a wikilink, and `/ \ * ? " < >`
#: are refused by Windows and, for the first two, by every filesystem.
#:
#: Each becomes a SPACE rather than being deleted, so a name like `C4ISR/EW`
#: reads as `C4ISR EW` and not as the word `C4ISREW`, which is not a thing.
_FORBIDDEN = str.maketrans({character: " " for character in '#|^[]:/\\*?"<>'})

#: Long enough for the longest real programme name, short enough that the path
#: survives a deep folder on Windows, whose limit is the whole path and not the
#: component.
NAME_LIMIT = 100


def safe_filename(name: str, *, fallback: str, limit: int = NAME_LIMIT) -> str:
    """A note name Obsidian and the filesystem will both accept.

    Still readable as the name: this deliberately does NOT reuse
    `db/models.py::slugify`, which lowercases and folds to ASCII. A folder of
    `maven-smart-system.md` is a database export wearing a Markdown extension,
    and the point of this one is that a person opens it.
    """
    cleaned = " ".join(name.translate(_FORBIDDEN).split())
    # A leading dot hides the file on every Unix and makes Obsidian skip it; a
    # trailing dot or space is silently dropped by Windows, which turns two
    # distinct names into one file.
    cleaned = cleaned.strip(" .")
    if len(cleaned) > limit:
        cut = cleaned[:limit].rsplit(" ", 1)[0]
        cleaned = (cut or cleaned[:limit]).strip(" .")
    return cleaned or fallback


class NoteNames:
    """Every note name in one folder, and the only place a link is built.

    Claimed in a FIXED ORDER by the caller, so the same corpus resolves the same
    collision the same way every time. Two exports of an unchanged corpus that
    disagreed about which of two identical names got the bare one would produce
    two different archives, and the golden test exists to catch exactly that.
    """

    def __init__(self) -> None:
        self._by_key: dict[str, str] = {}
        #: Folded, because Obsidian resolves a link case-insensitively and
        #: macOS will not store `NATO.md` beside `nato.md` at all. Claiming on
        #: the exact spelling would produce an archive that unpacks differently
        #: depending on who unpacks it.
        self._taken: set[str] = set()

    def claim(self, key: str, name: str, *, qualifier: str | None = None) -> str:
        """Reserve a name for `key` and answer what it actually got.

        Collisions are resolved by saying more, never by saying less. A second
        `Maven Smart System` becomes `Maven Smart System (Platform)` -- which is
        the true thing to call it -- and only a third falls back to a counter.
        """
        if key in self._by_key:
            return self._by_key[key]

        base = safe_filename(name, fallback=key)
        for candidate in self._candidates(base, qualifier):
            if candidate.casefold() not in self._taken:
                self._by_key[key] = candidate
                self._taken.add(candidate.casefold())
                return candidate
        raise AssertionError("unreachable -- the counter is unbounded")  # pragma: no cover

    @staticmethod
    def _candidates(base: str, qualifier: str | None):
        yield base
        if qualifier:
            qualified = safe_filename(f"{base} ({qualifier})", fallback=base)
            yield qualified
            base = qualified
        counter = 2
        while True:
            yield safe_filename(f"{base} {counter}", fallback=base)
            counter += 1

    def link(self, key: str, *, text: str | None = None) -> str | None:
        """A wikilink to `key`, or `None` when nothing was written for it.

        `None` is the whole contract. A caller that gets one writes the plain
        words instead of a link, which is how a merged-away name or a row the
        export declined to carry stops short of becoming a phantom node.
        """
        name = self._by_key.get(key)
        if name is None:
            return None
        if text and text != name:
            return f"[[{name}|{text}]]"
        return f"[[{name}]]"

    def name_for(self, key: str) -> str | None:
        return self._by_key.get(key)

    def is_taken(self, name: str) -> bool:
        """Whether a bare string already names a note.

        Asked of every alias before it is written. An `aliases` entry that
        matches another note's name is silently shadowed by the file -- the link
        goes to the file, the alias never fires, and nothing says so.
        """
        return safe_filename(name, fallback="").casefold() in self._taken


#: The forbidden set, exported so a test can assert the folder never contains
#: one rather than trusting this module to have been called.
FORBIDDEN_CHARACTERS = '#|^[]:/\\*?"<>'
