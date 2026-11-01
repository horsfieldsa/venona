"""The theme switch, asserted at the source rather than in a browser.

A one-frame flash is not reliably observable through CDP, and neither is "the
attribute was absent before the script ran". Both are properties of the SOURCE,
so they are checked here -- cheaply, in the offline suite, where a regression
fails in seconds instead of surviving until somebody notices the page blink.

SEVEN FILES SHARE ONE COOKIE NAME and none of them can import it from the
others: three render-blocking classic scripts with no bundler, two modules in
unrelated npm projects, and two Python templates. The cookie is scoped to the registrable
domain so one choice reaches every property (docs/adr/0177); `localStorage` is a
per-origin mirror behind it. This is what keeps the six in step.
"""

from __future__ import annotations

import re
from pathlib import Path

#: `//` lines and `/* */` blocks. Several assertions below are about the ORDER
#: two reads happen in, and every one of these files argues its case in prose
#: that names both -- so a raw `str.index` finds the sentence, not the statement.
#: `[\s\S]` rather than `.` under DOTALL: the flag is global, so a DOTALL `.*$`
#: in the `//` branch swallows the rest of the file instead of the rest of a line.
_COMMENTS = re.compile(r"/\*[\s\S]*?\*/|^[ \t]*//.*$", re.MULTILINE)


def code_of(path: Path) -> str:
    """The file with its commentary removed."""
    return _COMMENTS.sub("", path.read_text())


REPO = Path(__file__).resolve().parents[1]

KEY = "venona.theme"
SHARE_DOMAIN = ""

BOOT = REPO / "web" / "public" / "theme-boot.js"
INDEX = REPO / "web" / "index.html"
STORE = REPO / "web" / "src" / "stores" / "theme.ts"
HUB = REPO / "hub" / "hub" / "pages.py"
CONSENT = REPO / "backend" / "venona" / "api" / "routers" / "oauth.py"
EXTENSION = REPO / "extension" / "src" / "content" / "theme.ts"
HUB_BOOT = REPO / "hub" / "web" / "public" / "theme-boot.js"

#: Everything that names the cookie.
HOLDERS = (BOOT, STORE, HUB, HUB_BOOT, CONSENT)

#: The one that WRITES it. Every other holder only reads.
WRITERS = (STORE,)

#: The ones that read it in a browser, where the parse rules below apply.
JS_READERS = (BOOT, STORE, HUB_BOOT)

#: The two that answer on the server, with no script on the page at all.
SERVER_READERS = (HUB, CONSENT)


def test_every_holder_of_the_name_spells_it_the_same() -> None:
    """Renaming it in one place is silent everywhere else.

    A boot script would stop finding a stored choice and the app would open on
    System every time; the hub and the consent screen would quietly stop
    following the reader. None of it errors.
    """
    for path in HOLDERS:
        assert KEY in path.read_text(), f"{path.relative_to(REPO)} does not name {KEY!r}"


def test_the_extension_reads_the_attribute_and_holds_no_key() -> None:
    """The theme bridge is deliberately NOT a holder.

    `data-theme` on `<html>` is what the app resolves its palette from, so the
    bridge cannot disagree with what the reader is looking at, and its absence
    is System. A copy of the cookie name here would be a seventh string with
    nothing to hold it, in the one file that can least afford to grow.
    """
    source = EXTENSION.read_text()
    assert KEY not in source, "the theme bridge names the cookie; it should read the attribute"
    assert "localStorage" not in source, (
        "the theme bridge reads storage; it should read the attribute"
    )
    assert "dataset.theme" in source, "the theme bridge no longer reads the attribute"


def test_the_document_ships_no_theme() -> None:
    """System is the ABSENCE of `data-theme`, so the served HTML must carry none.

    With `data-theme="dark"` on `<html>` the document is pinned before any script
    runs, for every reader, whatever their OS says -- and the boot script could
    only ever move it to the other explicit choice. The unstyled first moment has
    to agree with the operating system, and the only way it can is by saying
    nothing.
    """
    html = INDEX.read_text()
    opening = re.search(r"<html[^>]*>", html)
    assert opening, "no <html> tag"
    assert "data-theme" not in opening.group(0), (
        f"{opening.group(0)} pins the pre-boot document; System has to be the absence "
        "of the attribute"
    )


def test_the_boot_script_stays_render_blocking() -> None:
    """`defer` or `type="module"` restores the flash this script exists to stop.

    Both make it non-blocking, so first paint happens with whatever `:root`
    resolves to and the correct theme arrives a frame later. It is exactly the
    kind of attribute somebody adds while tidying, and the symptom -- a blink on
    load -- reads as a rendering quirk rather than as a regression.
    """
    tag = re.search(r"<script[^>]*theme-boot\.js[^>]*>", INDEX.read_text())
    assert tag, "index.html does not load theme-boot.js"
    assert "defer" not in tag.group(0), tag.group(0)
    assert "type=" not in tag.group(0), tag.group(0)


def test_every_browser_reader_can_return_to_system() -> None:
    """A reader that only SETS can never get back to System.

    The attribute would survive from the last explicit choice and outrank
    `:root`'s `color-scheme: light dark` forever -- so choosing System in the
    footer would work until the next reload and then silently revert.
    """
    for path in JS_READERS:
        assert "delete document.documentElement.dataset.theme" in path.read_text(), (
            f"{path.relative_to(REPO)} has no delete branch: System would be unreachable"
        )


def test_system_is_a_stored_value_not_a_missing_one() -> None:
    """Clearing the key on System is what makes System unreachable when it is SHARED.

    Choose System on one property and the cookie goes; open another, whose own
    mirror still holds an explicit choice from an earlier visit, and the heal
    promotes that choice and rewrites the shared cookie. The reader can never
    land on System again from any property that was ever set.
    """
    for path in WRITERS:
        source = path.read_text()
        assert "removeItem" not in source, (
            f"{path.relative_to(REPO)} clears the mirror; System has to be stored, not absent"
        )
        assert "localStorage.setItem(KEY, choice)" in source, (
            f"{path.relative_to(REPO)} does not write every choice to the mirror"
        )
    for path in JS_READERS:
        assert "=== 'system'" in path.read_text(), (
            f"{path.relative_to(REPO)} does not accept 'system' as a stored value"
        )


def test_the_share_domain_is_a_literal_in_every_writer() -> None:
    """Deriving it from the host is the version that fails silently.

    The last two labels of a hosting provider's subdomain are a public suffix,
    which a browser refuses as a cookie Domain -- so the cookie would simply
    never persist there, which is exactly where somebody tests. Naming the
    domain means an unrecognised host falls back to a host-only cookie that
    works.
    """
    for path in WRITERS:
        source = path.read_text()
        assert f"const SHARE_DOMAIN = '{SHARE_DOMAIN}'" in source, (
            f"{path.relative_to(REPO)} does not name the share domain"
        )
        assert "endsWith(`.${SHARE_DOMAIN}`)" in source, (
            f"{path.relative_to(REPO)} does not gate the Domain attribute on the host"
        )
        assert "slice(-2)" not in source, (
            f"{path.relative_to(REPO)} derives the parent domain; name it instead"
        )


def test_every_writer_clears_the_host_only_spelling_first() -> None:
    """Two cookies of one name at different scopes cannot be told apart.

    `document.cookie` returns both with nothing to distinguish them, so once
    both exist there is no recovering from it in the browser -- the only fix is
    never to let it happen.
    """
    for path in WRITERS:
        source = code_of(path)
        clear = source.index("max-age=0")
        write = source.index("max-age=${YEAR_SECONDS}")
        assert clear < write, (
            f"{path.relative_to(REPO)} sets the shared cookie before clearing the host-only one"
        )


def test_every_browser_reader_takes_the_last_match() -> None:
    """RFC 6265 orders same-name cookies by longer path, then EARLIER creation.

    So a stale host-only cookie sorts ahead of the shared one, and a reader that
    stops at the first match reads the value it is trying to replace.
    """
    for path in JS_READERS:
        source = code_of(path)
        assert "for (const pair of document.cookie.split(';'))" in source, (
            f"{path.relative_to(REPO)} does not walk every cookie"
        )
        loop = source[source.index("document.cookie.split(';')") :]
        assert "break" not in loop[:400], (
            f"{path.relative_to(REPO)} stops at a match; the last one is the answer"
        )


def test_every_browser_reader_guards_the_two_stores_separately() -> None:
    """`document.cookie` throws, and it is read FIRST.

    `SecurityError` in a sandboxed frame, and refused outright under some
    cookie-blocking settings. One `try` around both reads would skip the mirror
    fallback entirely and drop a choice the reader had already made.
    """
    for path in JS_READERS:
        source = code_of(path)
        assert source.count("try {") >= 2, (
            f"{path.relative_to(REPO)} guards the cookie and the mirror together"
        )
        assert source.index("document.cookie") < source.index("localStorage"), (
            f"{path.relative_to(REPO)} reads the mirror before the shared cookie"
        )


def test_the_server_readers_pin_only_the_two_explicit_choices() -> None:
    """`system` has to render NO attribute, exactly like an unknown value.

    The absence of `data-theme` is what makes the page follow the operating
    system; there is no third rule, so `data-theme="system"` would be a selector
    nothing matches and the page would pin to whichever side `color-scheme:
    light dark` resolved.
    """
    for path in SERVER_READERS:
        source = path.read_text()
        assert '("light", "dark")' in source, (
            f"{path.relative_to(REPO)} does not gate the attribute on the two explicit choices"
        )
        assert "data-theme='light'" in source and "data-theme='dark'" in source, (
            f"{path.relative_to(REPO)} carries no [data-theme] rules, so the attribute does nothing"
        )
