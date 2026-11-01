"""Extension smoke checks: load it into a browser and assert it behaves.

NOT part of `make test`, which is offline and free. This needs the API up
(`make dev`, then `make ext-smoke`), and it exists because typecheck, lint and
the offline suite are all blind to what makes this extension hard: a whitespace
map that drops every multi-word entity, a `@source` glob that stops matching, a
host page whose own CSS wins, DOM surgery that changes the document's text.
Every one renders as "it looks fine, there are just no mentions".

UNLIKE `ui_smoke.py`, THIS IS SAFE TO RUN ANYWHERE. It reads: the only endpoints
it touches are `POST /api/annotate` and `POST /api/entities/cards:batch`, both of
which write nothing, and it never clicks "look this up", so it never spends.
There is no `VENONA_EXT_SMOKE` gate for that reason.

Add a check here whenever a defect gets past you.
"""

from __future__ import annotations

import asyncio
import contextlib
import http.server
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
from pathlib import Path

try:
    import websockets
except ImportError:  # pragma: no cover
    print("ext-smoke needs `websockets` (it is in the dev dependencies)", file=sys.stderr)
    raise SystemExit(2)

REPO = Path(__file__).resolve().parents[1]
DIST = REPO / "extension" / "dist"
FIXTURES = REPO / "fixtures" / "ext"

CDP_PORT = 9224
HTTP_PORT = 8891
# The Vite proxy: one setting is both where requests go and, with `/api`
# stripped, where the card's "Open in venona →" points, and those only agree at
# :5173 in development. The harness patches the staged manifest, not the
# settings, so driving uvicorn direct would test a configuration nobody runs.
API = "http://127.0.0.1:5173"

FAIL: list[str] = []
OK: list[str] = []


def check(name: str, condition: object, detail: object = "") -> None:
    line = f"{name}{(' -- ' + str(detail)) if detail else ''}"
    (OK if condition else FAIL).append(line)


# ── legibility on somebody else's page ──────────────────────────────
#
# The fixture is a white page, which is the ground the extension shipped broken
# against: `emit-styles.mjs` resolved `--text-primary` to `#eef2f5` and painted
# it on, so the annotated word was white on white at 1.13:1 with only its
# underline showing, every span in the DOM with the right ref. So the check is
# arithmetic, on the colours the browser actually computed.

WHITE = (255.0, 255.0, 255.0)


def _rgba(value: str) -> tuple[float, float, float, float]:
    """A computed colour, in 0-255 channels with alpha.

    Chrome does not round-trip `color-mix()` as `rgba()`: a plain colour is
    `rgb(0, 0, 0)`, a dimmed rung is `color(srgb 0 0 0 / 0.72)` in 0-1 channels.
    Reading the second as the first is a factor-of-255 error that still parses,
    so the contrast assertion passes while measuring nothing.
    """
    text = (value or "").strip()
    parts = [float(p) for p in re.findall(r"[\d.]+", text)]
    if len(parts) < 3:
        raise ValueError(f"not a colour: {value!r}")
    scale = 255.0 if text.startswith("color(") else 1.0
    alpha = parts[3] if len(parts) > 3 else 1.0
    return parts[0] * scale, parts[1] * scale, parts[2] * scale, alpha


def _over_white(value: str) -> tuple[float, float, float]:
    """Composite onto the page. `color-mix(... currentColor N%, transparent)` is
    a real alpha, so the glossary and undefined rungs must be flattened before
    they can be measured."""
    r, g, b, a = _rgba(value)
    return tuple(c * a + w * (1 - a) for c, w in zip((r, g, b), WHITE))


def _contrast_on_white(value: str) -> float:
    """0.0 when there is no colour to measure.

    A probe returns `None` for every colour when the page annotated NOTHING.
    Raising there took the whole run down with a `ValueError` before one result
    printed, so "no mentions" presented as a crash in the contrast helper.
    Returning 0 lets every check report and fail with the colour it was given.
    """
    if not value:
        return 0.0

    def channel(component: float) -> float:
        srgb = component / 255
        return srgb / 12.92 if srgb <= 0.04045 else ((srgb + 0.055) / 1.055) ** 2.4

    r, g, b = _over_white(value)
    luminance = 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
    return (1.0 + 0.05) / (luminance + 0.05)


# ── harness ─────────────────────────────────────────────────────────


def serve_fixtures() -> http.server.ThreadingHTTPServer:
    """The fixture article, over http.

    Not `file://`: a content script's `matches` cannot cover file URLs without
    a permission the real extension does not ask for, and testing through a
    permission the product does not have proves nothing about the product.
    """

    def handler(*args, **kwargs):
        return http.server.SimpleHTTPRequestHandler(*args, directory=str(FIXTURES), **kwargs)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", HTTP_PORT), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def stage_extension() -> Path:
    """A copy of `dist` permitted to reach the fixture server.

    The shipped extension annotates nowhere by default: where the annotator
    runs is granted at runtime, one host at a time, from the options page
    (`shared/sites.ts` has the reasoning), so the harness patches a copy rather
    than the product.

    `host_permissions` is what changes, not `matches`. An unpacked extension's
    DECLARED host permissions are granted without a prompt, so the fixture
    origin there gives `permissions.contains` something true to say, and the
    reconciler then registers the content script itself -- the code path a real
    grant takes. Nothing here fakes a registration.
    """
    staged = Path(tempfile.mkdtemp(prefix="venona-ext.")) / "dist"
    shutil.copytree(DIST, staged)

    manifest = json.loads((staged / "manifest.json").read_text())
    manifest["host_permissions"] = [f"{API}/*", f"http://127.0.0.1:{HTTP_PORT}/*"]
    (staged / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return staged


class Browser:
    def __init__(self, extension: Path | None) -> None:
        self.extension = extension
        self.profile = Path(tempfile.mkdtemp(prefix="venona-ext-profile."))
        self.proc: subprocess.Popen | None = None
        self.ws = None
        # Opened lazily by `evaluate_in_worker`. A second socket, because
        # `self.ws` is bound to the PAGE target and CDP has no cross-target eval.
        self.worker_ws = None
        self._id = 0

    async def __aenter__(self) -> Browser:
        args = [
            "chromium",
            "--headless=new",
            f"--remote-debugging-port={CDP_PORT}",
            "--no-sandbox",
            "--disable-gpu",
            f"--user-data-dir={self.profile}",
            "--window-size=1100,900",
        ]
        if self.extension:
            args += [
                f"--disable-extensions-except={self.extension}",
                f"--load-extension={self.extension}",
            ]
        args.append(f"http://127.0.0.1:{HTTP_PORT}/article.html")

        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        page = None
        for _ in range(80):
            await asyncio.sleep(0.25)
            try:
                targets = json.load(urllib.request.urlopen(f"http://localhost:{CDP_PORT}/json"))
            except Exception:
                continue
            page = next(
                (t for t in targets if t["type"] == "page" and "article.html" in t["url"]), None
            )
            if page:
                break
        if not page:
            raise SystemExit("ext-smoke: chromium never opened the fixture page")
        self.ws = await websockets.connect(page["webSocketDebuggerUrl"], max_size=None)
        return self

    async def __aexit__(self, *exc) -> None:
        if self.worker_ws:
            await self.worker_ws.close()
        if self.ws:
            await self.ws.close()
        if self.proc:
            self.proc.terminate()
            # WAIT for it. `CDP_PORT` is fixed, so a chromium asked to die but
            # still holding the socket catches the next `Browser`'s attach --
            # which lands on a dying, extension-less instance where every probe
            # reports "no mentions", reading as a real failure of the feature.
            try:
                await asyncio.wait_for(asyncio.to_thread(self.proc.wait), timeout=10)
            except TimeoutError:
                self.proc.kill()
                await asyncio.to_thread(self.proc.wait)
        shutil.rmtree(self.profile, ignore_errors=True)

    async def _evaluate_on(self, socket, expression: str):
        self._id += 1
        message_id = self._id
        await socket.send(
            json.dumps(
                {
                    "id": message_id,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": expression,
                        "awaitPromise": True,
                        "returnByValue": True,
                    },
                }
            )
        )
        while True:
            got = json.loads(await socket.recv())
            if got.get("id") == message_id:
                result = got.get("result", {}).get("result", {})
                # A probe that throws returns `value: undefined` -> None, which
                # every check below reads as "the page annotated nothing", so a
                # broken probe and a broken feature are indistinguishable and
                # the wrong one gets investigated. `ui_smoke.py` does the same.
                if result.get("subtype") == "error":
                    return f"JS ERROR: {result.get('description', '')[:200]}"
                return result.get("value")

    async def evaluate(self, expression: str):
        return await self._evaluate_on(self.ws, expression)

    async def _command(self, method: str, params: dict):
        """One raw CDP command on the page socket, result payload back."""
        self._id += 1
        message_id = self._id
        await self.ws.send(json.dumps({"id": message_id, "method": method, "params": params}))
        while True:
            got = json.loads(await self.ws.recv())
            if got.get("id") == message_id:
                return got.get("result", {})

    async def evaluate_in_card(self, body: str):
        """Run `body` with `root` bound to the card's CLOSED shadow root.

        `card.ts` attaches it with `mode: 'closed'` -- page script must not
        reach a button that spends -- so `host.shadowRoot` is null in every JS
        world. The DOM domain can still see it: `pierce: true` lists closed
        roots and `DOM.resolveNode` turns one into a `Runtime.callFunctionOn`
        receiver. None when the card host or its root does not exist.
        """
        doc = await self._command("DOM.getDocument", {"depth": -1, "pierce": True})

        def find_host(node: dict) -> dict | None:
            attrs = node.get("attributes", [])
            for i in range(0, len(attrs) - 1, 2):
                if attrs[i] == "id" and attrs[i + 1] == "venona-card-host":
                    return node
            for child in node.get("children", []) + node.get("shadowRoots", []):
                found = find_host(child)
                if found:
                    return found
            return None

        host = find_host(doc.get("root", {}))
        roots = (host or {}).get("shadowRoots", [])
        if not roots:
            return None
        resolved = await self._command(
            "DOM.resolveNode", {"backendNodeId": roots[0]["backendNodeId"]}
        )
        object_id = resolved.get("object", {}).get("objectId")
        if not object_id:
            return None
        result = await self._command(
            "Runtime.callFunctionOn",
            {
                "objectId": object_id,
                "functionDeclaration": f"function() {{ const root = this; {body} }}",
                "returnByValue": True,
                "awaitPromise": True,
            },
        )
        payload = result.get("result", {})
        if payload.get("subtype") == "error":
            return f"JS ERROR: {payload.get('description', '')[:200]}"
        return payload.get("value")

    async def evaluate_in_tab(self, url: str, expression: str, settle: float = 2.0):
        """Open `url` in its own tab, evaluate there, close it.

        For the OPTIONS PAGE, a third context: `self.ws` is bound to the fixture
        page and `evaluate_in_worker` to the service worker, and neither can
        reach it.
        """
        request = urllib.request.Request(
            f"http://localhost:{CDP_PORT}/json/new?{urllib.parse.quote(url, safe=':/?=&.')}",
            method="PUT",
        )
        target = json.load(urllib.request.urlopen(request))
        socket = None
        try:
            socket = await websockets.connect(target["webSocketDebuggerUrl"], max_size=None)
            # The page has to finish its async `init()` before anything is on
            # screen. Polling the DOM instead would hide exactly the failure
            # this exists to catch: an init that never completes.
            await asyncio.sleep(settle)
            return await self._evaluate_on(socket, expression)
        finally:
            if socket is not None:
                await socket.close()
            with contextlib.suppress(Exception):
                urllib.request.urlopen(
                    f"http://localhost:{CDP_PORT}/json/close/{target['id']}"
                ).read()

    async def evaluate_in_worker(self, expression: str):
        """Run something in the SERVICE WORKER, not the page.

        Where the extension runs is decided by the worker's reconciler. The
        worker is an ES module, so its top-level bindings are not on
        `globalThis` and `syncRegistration` cannot be called directly:
        `chrome.storage` is the handle, and the worker's `storage.onChanged`
        listener turns a write into a registration -- not a test seam, it is how
        the options page drives the worker too.
        """
        if self.worker_ws is None:
            targets = await self.await_worker()
            if not targets:
                raise RuntimeError("ext-smoke: no service worker to attach to")
            self.worker_ws = await websockets.connect(
                targets[0]["webSocketDebuggerUrl"], max_size=None
            )
        return await self._evaluate_on(self.worker_ws, expression)

    async def worker_targets(self) -> list[dict]:
        targets = json.load(urllib.request.urlopen(f"http://localhost:{CDP_PORT}/json"))
        return [t for t in targets if t["type"] == "service_worker"]

    async def await_worker(self, timeout: float = 15.0) -> list[dict]:
        """Wait for the worker to exist AND for `chrome.*` to be live in it.

        An MV3 service worker starts LAZILY. The target can be absent for a
        second or two after launch and -- the part that actually bit -- present
        with its execution context not yet carrying the extension APIs, so
        `chrome.scripting` reads `undefined`. Both present as "the extension is
        broken" rather than "ask again in a moment".
        """
        deadline = asyncio.get_event_loop().time() + timeout
        socket = None
        try:
            while asyncio.get_event_loop().time() < deadline:
                targets = await self.worker_targets()
                if targets:
                    if socket is None:
                        socket = await websockets.connect(
                            targets[0]["webSocketDebuggerUrl"], max_size=None
                        )
                    ready = await self._evaluate_on(socket, "typeof chrome?.scripting")
                    if ready == "object":
                        return targets
                await asyncio.sleep(0.25)
            return await self.worker_targets()
        finally:
            if socket is not None:
                await socket.close()


# ── the checks ──────────────────────────────────────────────────────

REPORT_JS = """
(() => {
  const refs = (sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    return [...el.querySelectorAll('[data-venona-ref]')].map(s => ({
      ref: s.getAttribute('data-venona-ref'),
      text: s.textContent,
      cls: s.className,
    }));
  };
  const first = document.querySelector('[data-venona-ref]');
  const glossary = document.querySelector('.venona-mention--glossary');
  const colour = (el) => (el ? getComputedStyle(el).color : null);
  return {
    ground: document.documentElement.getAttribute('data-venona-ground'),
    mentionColour: colour(first),
    hostColour: colour(first && first.parentElement),
    glossaryColour: colour(glossary),
    glossaryHostColour: colour(glossary && glossary.parentElement),
    total: document.querySelectorAll('[data-venona-ref]').length,
    wrapped: refs('#wrapped'),
    astral: refs('#astral'),
    linked: refs('#linked'),
    split: refs('#split'),
    common: refs('#commonnouns'),
    code: refs('#code'),
    plain: refs('#plain'),
    nav: document.querySelectorAll('nav [data-venona-ref]').length,
    footer: document.querySelectorAll('footer [data-venona-ref]').length,
    text: document.body.textContent,
    underline: first ? getComputedStyle(first).borderBottomStyle : null,
    underlineColour: first ? getComputedStyle(first).borderBottomColor : null,
  };
})()
"""


async def run() -> int:
    if not DIST.exists():
        print("ext-smoke: extension/dist is missing. Run `npm run build` in extension/.")
        return 2
    # A store-channel build defaults every request at production, so driving it
    # here would annotate against the live corpus and auto-open the options
    # page. `finish.mjs` stamps the marker; a plain `make ext-build` removes it.
    if (DIST / "CHANNEL").exists() and (DIST / "CHANNEL").read_text().strip() == "store":
        print("ext-smoke: dist/ is a STORE build (dist/CHANNEL). Run `make ext-build` first.")
        return 2
    try:
        urllib.request.urlopen(f"{API}/api/health/live", timeout=3)
    except Exception as exc:
        print(f"ext-smoke: the API is not answering on {API} ({exc}). Start `make dev`.")
        return 2

    serve_fixtures()

    # The same page WITHOUT the extension, to compare text against. Reading
    # `textContent` after the content script has run tells you what it produced,
    # not what it started from, so the page has to be loaded twice.
    async with Browser(extension=None) as plain:
        await asyncio.sleep(1.0)
        plain_text = await plain.evaluate("document.body.textContent")
        plain_count = await plain.evaluate("document.querySelectorAll('[data-venona-ref]').length")

    check("a browser with no extension annotates nothing", plain_count == 0, plain_count)

    async with Browser(extension=stage_extension()) as browser:
        workers = await browser.await_worker()
        check("the service worker starts", len(workers) == 1, [w["url"] for w in workers])
        if workers:
            worker_id = workers[0]["url"].split("/")[2]
            check(
                "the manifest key pins the extension id",
                worker_id == "hjkknjcomgnlnnkhgcdenhdkejnjiphg",
                worker_id,
            )

        # ── grant a site, the way the options page does ──────────────
        #
        # The whole feature under test: the annotator is registered nowhere
        # until a host is granted, and writing the intent to `storage.sync` is
        # exactly what ticking a box in the options page does. Filtered to
        # `venona-sites` -- the theme bridge keeps its own persistent
        # `venona-theme` registration, and counting it would make this about the
        # wrong feature.
        pattern = f"http://127.0.0.1:{HTTP_PORT}/*"

        before = await browser.evaluate_in_worker(
            "chrome.scripting.getRegisteredContentScripts({ids: ['venona-sites']})"
            ".then(s => s.length)"
        )
        check("nothing is registered before a site is granted", before == 0, before)

        await browser.evaluate_in_worker(
            f"chrome.storage.sync.set({{'sites:enabled': ['{pattern}']}})"
        )
        # The listener is async and registration is a round trip through the
        # browser's own script registry; there is no event to await from here.
        await asyncio.sleep(2.0)

        registered = await browser.evaluate_in_worker(
            "chrome.scripting.getRegisteredContentScripts()"
            ".then(s => JSON.stringify(s.map(x => x.matches).flat()))"
        )
        check(
            "granting a site registers the content script",
            registered and pattern in str(registered),
            registered,
        )

        # Only now can the page annotate. It loaded before any of this existed.
        await browser.evaluate("location.reload()")

        # The content script's annotate round trip.
        await asyncio.sleep(5.0)
        report = await browser.evaluate(REPORT_JS)

        # ── the whitespace map ──────────────────────────────────────
        #
        # `#wrapped` puts a newline and eight spaces between `Lockheed` and
        # `Martin`, which is what HTML source looks like. Matched literally that
        # is `Lockheed` alone; collapsed and mapped back it is one span holding
        # the ORIGINAL whitespace.
        wrapped = report["wrapped"] or []
        lockheed = [s for s in wrapped if s["ref"] == "company:lockheed-martin"]
        check(
            "a multi-word entity split across a source newline is ONE span",
            len(lockheed) == 1,
            wrapped,
        )
        if lockheed:
            check(
                "and that span holds the page's original whitespace, not a collapsed copy",
                "\n" in lockheed[0]["text"] and "Martin" in lockheed[0]["text"],
                repr(lockheed[0]["text"]),
            )
        check(
            "a second multi-word entity across a newline also resolves",
            any(s["ref"] == "company:booz-allen-hamilton" for s in wrapped),
            wrapped,
        )

        # ── the text is not touched ─────────────────────────────────
        check(
            "splicing does not change one character of the page",
            report["text"] == plain_text,
            f"{len(plain_text)} chars before, {len(report['text'])} after",
        )

        # ── what must NOT be annotated ──────────────────────────────
        check("a mention inside a link is skipped", report["linked"] == [], report["linked"])
        check("code is skipped", report["code"] == [], report["code"])
        check("navigation outside the article is skipped", report["nav"] == 0, report["nav"])
        check("the footer is skipped", report["footer"] == 0, report["footer"])

        # `strict_case`. Every one of these is an entity alias in the corpus and
        # ordinary English in this sentence.
        check(
            "ordinary words that collide with entity names are not annotated",
            report["common"] == [],
            report["common"],
        )

        # ── what must be ───────────────────────────────────────────
        astral = report["astral"] or []
        check(
            "mentions after an astral-plane character land on the right words",
            [s["text"] for s in astral] == ["DARPA", "MOSA"],
            astral,
        )
        # STRUCTURAL, not by id. Promoting a glossary surface to a real entity is
        # the pipeline working as designed, so an assertion naming a `gloss:` id
        # is an assertion that grounding never happens. This one named
        # `gloss:mosa` and `gloss:cjadc2`, and failed silently the day they were
        # grounded, because nothing runs this on a schedule. What the check is
        # for is that a paragraph of ordinary prose resolves several distinct
        # mentions with well-formed type-prefixed ids (contract C4).
        plain_para = report["plain"] or []
        refs = {s["ref"] for s in plain_para}
        check(
            "a plain paragraph resolves several distinct mentions",
            len(refs) >= 2,
            plain_para,
        )
        check(
            "and every ref is a type-prefixed id (C4), not a bare surface",
            refs and all(re.match(r"^[a-z]+:[a-z0-9][a-z0-9-]*$", r) for r in refs),
            sorted(refs),
        )
        # Invariant 3: undefined must render as VISIBLY undefined. Whether this
        # corpus has an undefined surface in this paragraph is not the
        # extension's to control, so what is asserted is the half that can break
        # on OUR side -- the treatment existing in the generated stylesheet at
        # all, without which an undefined term renders as an ordinary one.
        mentions_css = (DIST / "mentions.css").read_text()
        check(
            "the undefined treatment survives into the page stylesheet",
            ".venona-mention--undefined" in mentions_css,
            "invariant 3 does not stop at the app's edge",
        )
        split = report["split"] or []
        check(
            "an entity split by inline markup yields spans sharing one ref",
            len({s["ref"] for s in split}) == 1 and len(split) > 1,
            split,
        )

        # ── the host page does not win ──────────────────────────────
        #
        # The fixture sets `span { border-bottom: none }`, which is exactly the
        # kind of blanket rule `mentions.css` carries `!important` for.
        check(
            "the host page's CSS does not erase the underline",
            report["underline"] == "dotted",
            report["underline"],
        )
        # ── legible on the ground it landed on ──────────────────────
        #
        # The fixture has no background of its own, so the canvas shows through
        # and the ground is white -- the case that shipped broken.
        check(
            "the ground is measured from the page, not from the OS preference",
            report["ground"] == "light",
            report["ground"],
        )
        check(
            "an entity mention inherits the host page's own text colour",
            report["mentionColour"] == report["hostColour"],
            f"{report['mentionColour']} vs the paragraph's {report['hostColour']}",
        )
        check(
            "a glossary mention is dimmed relative to the host, not repainted",
            report["glossaryColour"] is not None
            and report["glossaryColour"] != report["glossaryHostColour"]
            and _rgba(report["glossaryColour"])[3] < 1.0
            and _rgba(report["glossaryColour"])[:3] == _rgba(report["glossaryHostColour"])[:3],
            f"{report['glossaryColour']} vs the paragraph's {report['glossaryHostColour']}",
        )
        for label, value, floor in (
            ("an entity mention", report["mentionColour"], 4.5),
            ("a glossary mention", report["glossaryColour"], 4.5),
        ):
            # A MISSING mention is a failed check, not a traceback. `colour()`
            # returns null when the corpus has no term of that class to render,
            # and crashing here reported a corpus state as a harness bug while
            # burying every check after it.
            if value is None:
                check(
                    f"{label} is present to measure",
                    False,
                    "no mention of that class rendered -- check the corpus, not the CSS",
                )
                continue
            measured = _contrast_on_white(value)
            check(
                f"{label} is legible against a white article",
                measured >= floor,
                f"{value} measures {measured:.2f}:1 on white, needs {floor}",
            )
        # rgb(79, 112, 96) is #4f7060 is `--sage-700`, the LIGHT SIDE of
        # `--entity-company`. One derivation of that number, in the token layer;
        # the retirement note in `emit-styles.mjs` carries the arithmetic and
        # why there is only one.
        check(
            "the underline is the entity's own token colour, on its light-ground rung",
            report["underlineColour"] == "rgb(79, 112, 96)",
            f"{report['underlineColour']} (expected --entity-company's light rung, #4f7060)",
        )
        underline = _contrast_on_white(report["underlineColour"])
        check(
            "the underline clears the hairline's contrast floor on white",
            underline >= 4.5,
            f"{report['underlineColour']} measures {underline:.2f}:1",
        )

        # ── the card ────────────────────────────────────────────────
        #
        # Clicked, not hovered: there is no real pointer in headless, and click
        # pins, which is the same code path plus a flag. The MOSA mention,
        # whatever it resolves to -- SELECT BY THE SURFACE, never by id, for the
        # reason above: an id selector finds nothing and fails every card
        # assertion below with "the card is broken" when the card is fine.
        opened = await browser.evaluate(
            """(() => {
              const m = [...document.querySelectorAll('[data-venona-ref]')]
                .find(e => (e.textContent || '').trim() === 'MOSA');
              if (!m) return 'no MOSA mention';
              m.dispatchEvent(new MouseEvent('click', {bubbles: true}));
              return 'clicked';
            })()"""
        )
        check("a mention is present to click", opened == "clicked", opened)
        await asyncio.sleep(2.5)

        card = await browser.evaluate_in_card(
            """
              const body = root.querySelector('.body');
              return {
                hasBody: !!body,
                text: (body ? body.textContent : '').replace(/\\s+/g, ' ').trim(),
                styled: (root.querySelector('style')?.textContent || '')
                          .includes('.border-l-accent-muted{'),
                bg: body
                  ? getComputedStyle(body.closest('div[style]') || body).backgroundColor
                  : null,
              };
            """
        )
        if not isinstance(card, dict):
            card = {"mounted": False, "detail": card}
        check("the card mounts into a shadow root", "detail" not in card, card)
        check("the card renders EntityCardBody", card.get("hasBody"), card)
        # INVARIANT 3 at the card, whatever the corpus says: either a grounded
        # definition WITH its provenance, or a plain statement that it has none.
        # Do NOT assert `"Modular Open Systems Approach" in text` -- once a term
        # is grounded into a `tech:` entity that string is the entity's NAME, so
        # the check passes while measuring nothing.
        text = card.get("text") or ""
        grounded = "quoted from" in text or "written from" in text
        undefined = "No grounded definition yet" in text
        check(
            "the card states its definition or states that it has none",
            grounded or undefined,
            text[:200],
        )
        check(
            "and a definition never appears without its provenance",
            not (grounded and undefined),
            text[:200],
        )
        check(
            "the card's utility layer survived the build",
            card.get("styled"),
            "no generated `.border-l-accent-muted{` rule in the shadow root -- the card "
            "mounted but Tailwind emitted nothing for it",
        )

        # ── the theme, and the ground, which are different questions ─
        #
        # The doctrine: underlines are spliced into somebody else's prose and
        # follow the MEASURED background of the article; the card is venona's
        # own surface and follows the theme the reader CHOSE. A dark card on a
        # white article is correct; light underlines on a white article are not.
        #
        # The card themes through `color-scheme` and `light-dark()`, whose one
        # silent failure mode is severe and is documented at `card.css`'s
        # `:host` block: every token present, every one resolved to the wrong
        # half, for every reader, with the tokens, the build and the bundle
        # assertions all correct.
        async def card_background(theme: str) -> str | None:
            await browser.evaluate_in_worker(f"chrome.storage.sync.set({{theme: {theme!r}}})")
            # The content script's `watchTheme` listener is a storage event away.
            await asyncio.sleep(1.0)
            return await browser.evaluate_in_card(
                """
                  const body = root.querySelector('.body');
                  if (!body) return null;
                  return getComputedStyle(body.closest('div[style]') || body).backgroundColor;
                """
            )

        dark_bg = await card_background("dark")
        light_bg = await card_background("light")
        check(
            "the card follows the reader's theme, both ways",
            dark_bg is not None and light_bg is not None and dark_bg != light_bg,
            f"dark={dark_bg} light={light_bg} -- if these are equal, `color-scheme` is not "
            f"reaching the shadow root and every light-dark() token is stuck on one side",
        )
        if dark_bg and light_bg:
            # Measured as contrast against white, which is the helper already
            # here: the darker of two opaque colours is the one further from
            # white, so this needs no new arithmetic.
            check(
                "and dark is the darker of the two",
                _contrast_on_white(dark_bg) > _contrast_on_white(light_bg),
                f"dark={dark_bg} light={light_bg} -- the two sides are the wrong way round, "
                f"which is what swapping light-dark()'s arguments looks like",
            )

        # THE DOCTRINE, AS AN INVARIANT. The reader is now in dark mode; the
        # article is still white. The underline must not have moved.
        await browser.evaluate_in_worker("chrome.storage.sync.set({theme: 'dark'})")
        await asyncio.sleep(1.0)
        underline_now = await browser.evaluate(
            """(() => {
              const first = document.querySelector('[data-venona-ref]');
              return first ? getComputedStyle(first).borderBottomColor : null;
            })()"""
        )
        check(
            "a dark-mode reader on a white article still gets light-ground underlines",
            underline_now == report["underlineColour"],
            f"{underline_now} vs the measured-ground {report['underlineColour']} -- the "
            f"mention rules have started following the reader instead of the page",
        )
        await browser.evaluate_in_worker("chrome.storage.sync.remove('theme')")

        # ── the mark, on somebody else's page ────────────────────────
        #
        # THIS CARD IS NOT THE ACTIONLESS ONE, which is the card the
        # unconditional footer exists for: a grounded acronym needs no lookup
        # and has no graph node, so both buttons are absent. This corpus has
        # zero `gloss:` entities and cannot produce one, so nothing below covers
        # that case -- the geometry checks are real, but they measure a card
        # WITH actions.
        #
        # `src` must be a data URI; `finish.mjs` says why, and checks the
        # bundle. This checks what the browser actually resolved.
        lockup = await browser.evaluate_in_card(
            """
              // `img.mark`, not `.lockup img.mark`. The `.lockup` wrapper existed
              // to flex a mark beside a word; `Mark.vue` is one image and needs
              // no box around it. `.word` is queried anyway -- see the
              // mark-alone check below, which is only meaningful if a word
              // appearing here would be caught.
              const mark = root.querySelector('img.mark');
              const word = root.querySelector('.lockup, .word');
              return {
                found: !!mark,
                src: (mark?.getAttribute('src') || '').slice(0, 24),
                word: word ? word.textContent.trim() : '',
                // Zero-sized is the failure that looks like success: the element
                // is in the DOM and the assertion above passes.
                width: mark ? Math.round(mark.getBoundingClientRect().width) : 0,
                // WHERE it is, as fractions of the card box. The lockup moved
                // from the footer to the top-right corner, and every assertion
                // above passes in either place -- they only ever asked whether
                // it exists.
                ...(() => {
                  // Walk up to the CARD -- the element the component positions
                  // with `fixed`. Selecting it by markup shape breaks the first
                  // time the template gains a wrapper; its position does not.
                  const lockEl = mark;
                  let card = lockEl?.parentElement;
                  while (card && getComputedStyle(card).position !== 'fixed') {
                    card = card.parentElement;
                  }
                  if (!lockEl || !card) return {xFrac: null, yFrac: null};
                  const box = card.getBoundingClientRect();
                  const lock = lockEl.getBoundingClientRect();
                  if (!box.width || !box.height) return {xFrac: null, yFrac: null};
                  // The card scrolls, so `box` is the VIEWPORT slice of it.
                  // Scroll to the end first, or a long card reports the lockup
                  // as far below the fold and every fraction is meaningless.
                  card.scrollTop = card.scrollHeight;
                  const box2 = card.getBoundingClientRect();
                  const lock2 = lockEl.getBoundingClientRect();
                  const pad = parseFloat(getComputedStyle(card).paddingBottom) || 0;
                  return {
                    xFrac: (lock2.left - box2.left) / box2.width,
                    yFrac: (lock2.top - box2.top) / box2.height,
                    // Space between the mark and the card's inner bottom edge.
                    gapBelow: Math.round(box2.bottom - pad - lock2.bottom),
                  };
                })(),
              };
            """
        )
        if not isinstance(lockup, dict):
            lockup = {"found": False, "detail": lockup}
        check("the card carries the venona lockup", lockup.get("found"), lockup)
        check(
            "the mark is inlined, not a file the host page would resolve",
            str(lockup.get("src", "")).startswith("data:"),
            lockup.get("src"),
        )
        # The MARK ALONE. `Mark.vue` is deliberate for this surface -- see
        # `HoverCard.vue` for the width it saves and the eyebrow it avoids
        # colliding with -- so a word rendering here is a regression, and means
        # the whole lockup was pulled into the content bundle, where Vite emits
        # it as a FILE the content script cannot resolve.
        check(
            "the corner carries the mark alone, not the wordmark",
            lockup.get("found") and lockup.get("word") == "",
            lockup,
        )
        check("the mark has a non-zero box", (lockup.get("width") or 0) > 0, lockup)
        check(
            "the lockup sits in the card's lower-left corner",
            (lockup.get("xFrac") if lockup.get("xFrac") is not None else 1) < 0.5
            and (lockup.get("yFrac") or 0) > 0.5,
            f"x {lockup.get('xFrac')}, y {lockup.get('yFrac')}",
        )
        # THE BOTTOM PADDING, the footer's other job. The card sets `pb-0` on
        # purpose -- the sticky strip holds its last line off the border -- so
        # hiding the strip runs the text into the edge. That shipped: gating the
        # footer on `hasActions` did it to every grounded-acronym card.
        #
        # VERIFIED NOT TO COVER THAT CASE (2026-08-17): re-gating the footer and
        # re-running left this at 15px, because the card measured here has a
        # button and keeps its footer either way. The weaker invariant it does
        # assert is that the strip supplying the bottom padding is present.
        # Restoring a `gloss:` fixture is what would make this bite.
        check(
            "the footer holds the card's content off its bottom edge",
            (lockup.get("gapBelow") or 0) >= 6,
            f"{lockup.get('gapBelow')}px between the lockup and the card's inner bottom",
        )

        # ── a click INSIDE a pinned card must not dismiss it ──────────────
        #
        # `click` is composed: it crosses the shadow boundary and reaches the
        # document listener retargeted to the host, where `closest` finds no
        # mention. The dismiss branch then fired for a click on the card's own
        # controls -- so "Look this up" started a paid tier-3 run and the card
        # vanished on the same click, with no spinner and no result. Nothing saw
        # it because the harness only ever clicked a mention and pressed Escape.
        still_open = await browser.evaluate_in_card(
            """
              const body = root.querySelector('.body');
              if (!body) return {clicked: false};
              body.dispatchEvent(new MouseEvent('click', {bubbles: true, composed: true}));
              return {clicked: true};
            """
        ) or {"clicked": False}
        await asyncio.sleep(0.4)
        survived = await browser.evaluate_in_card("return !!root.querySelector('.body');")
        check(
            "a click inside the pinned card leaves it open",
            still_open.get("clicked") and survived,
            {"clicked": still_open, "survived": survived},
        )

        # Escape closes it, in the capture phase, so a host page that binds
        # Escape cannot eat it first.
        await browser.evaluate(
            """document.dispatchEvent(new KeyboardEvent('keydown',
                 {key:'Escape', bubbles:true}))"""
        )
        await asyncio.sleep(0.5)
        closed = await browser.evaluate_in_card("return !root.querySelector('.body');")
        check("Escape dismisses the card", closed, closed)

        # ── a limited credential hides the spending button ────────────────
        #
        # A paired token is annotate-scoped (ADR 0161), so `caps: limited` is the
        # steady state for every paired user, and seeding the key writes exactly
        # what pairing writes. Two openings bracket it: under `limited` no card
        # offers a lookup, with the key removed one does again -- never-probed
        # must read as full, or every card would hide a working button while the
        # first probe is in flight. WHICH mention offers a lookup is corpus
        # state, so the bracket hunts for one rather than naming MOSA.
        async def lookup_offered(surface: str) -> dict:
            await browser.evaluate(
                f"""(() => {{
                  const m = [...document.querySelectorAll('[data-venona-ref]')]
                    .find(e => (e.textContent || '').trim() === {surface!r});
                  if (m) m.dispatchEvent(new MouseEvent('click', {{bubbles: true}}));
                }})()"""
            )
            await asyncio.sleep(1.5)
            state = await browser.evaluate_in_card(
                """
                  const labels = [...root.querySelectorAll('button')]
                    .map(b => b.getAttribute('aria-label') || '');
                  return {
                    offered: labels.some(l => /Look this up|Try again/.test(l)),
                    labels,
                    open: !!root.querySelector('.body'),
                  };
                """
            )
            await browser.evaluate(
                """document.dispatchEvent(new KeyboardEvent('keydown',
                     {key:'Escape', bubbles:true}))"""
            )
            await asyncio.sleep(0.4)
            return state if isinstance(state, dict) else {"offered": None, "detail": state}

        surfaces = (
            await browser.evaluate(
                """(() => [...new Set([...document.querySelectorAll('[data-venona-ref]')]
                     .map(e => (e.textContent || '').trim()))].slice(0, 8))()"""
            )
            or []
        )
        offering = None
        for surface in surfaces:
            if (await lookup_offered(surface)).get("offered"):
                offering = surface
                break

        await browser.evaluate_in_worker("chrome.storage.local.set({caps: {default: 'limited'}})")
        limited = await lookup_offered(offering or surfaces[0] if surfaces else "MOSA")
        check(
            "a limited credential's card offers no paid lookup",
            limited.get("open") is True and limited.get("offered") is False,
            limited,
        )
        await browser.evaluate_in_worker("chrome.storage.local.remove('caps')")
        if offering is None:
            check(
                "clearing the capability brings the lookup button back",
                True,
                "every surfaced term on this corpus is defined, so no card offers the "
                "paid lookup with or without caps; only the hiding half is assertable",
            )
        else:
            restored = await lookup_offered(offering)
            check(
                "clearing the capability brings the lookup button back",
                restored.get("offered") is True,
                restored,
            )

        # ── the deep link names the entity, in the word the router reads ──
        #
        # Needs an ENTITY card: `graphHref` is hidden for `gloss:` ids, an
        # acronym being no graph node. The parameter NAME is the whole check.
        # `fromGraphQuery` reads `focus`; the extension wrote `subject`, which
        # decoded to nothing, the intent fell through to its `else`, and "Open in
        # venona →" opened the ENTIRE graph. It rendered a graph either way,
        # which is why it survived so long -- and this card is the only place in
        # the project that builds a graph URL by hand.
        await browser.evaluate(
            """(() => {
              const m = [...document.querySelectorAll('[data-venona-ref]')]
                .find(e => /^(?!gloss:)[a-z]+:/.test(e.getAttribute('data-venona-ref') || ''));
              if (m) m.dispatchEvent(new MouseEvent('click', {bubbles: true}));
              return !!m;
            })()"""
        )
        await asyncio.sleep(2.5)
        link = (
            await browser.evaluate_in_card(
                """
              const a = [...root.querySelectorAll('a[href]')]
                .find(x => (x.title || x.getAttribute('aria-label') || '')
                  .includes('Open in the graph'));
              if (!a) return 'no link';
              const u = new URL(a.href);
              return {origin: u.origin, path: u.pathname,
                      mode: u.searchParams.get('mode'),
                      focus: u.searchParams.get('focus'),
                      subject: u.searchParams.get('subject')};
            """
            )
            or "no card"
        )
        check(
            "the card's graph link focuses the entity, in the parameter the router reads",
            isinstance(link, dict)
            and link.get("mode") == "focus"
            and ":" in (link.get("focus") or "")
            and not (link.get("focus") or "").startswith("gloss:")
            and not link.get("subject"),
            link,
        )
        check(
            "and points at the app, not at uvicorn",
            isinstance(link, dict) and link.get("path") == "/graph" and link.get("origin") == API,
            link,
        )

        # THE NAME'S OWN PAGE, where "Open in Venona" goes. Built by hand for the
        # same reason the graph link is -- the content script shares no bundle
        # with the app, so `entityRoute.ts` is out of reach -- and needing the
        # same check: the colon of a C4 id becomes the path separator, and
        # getting that wrong yields a URL that 404s.
        page_link = (
            await browser.evaluate_in_card(
                """
              const a = [...root.querySelectorAll('a[href]')]
                .find(x => (x.title || x.getAttribute('aria-label') || '')
                  .includes('Open in Venona'));
              if (!a) return 'no link';
              const u = new URL(a.href);
              return {origin: u.origin, path: u.pathname, search: u.search};
            """
            )
            or "no card"
        )
        check(
            "the card's Venona link is the name's own page, type and slug as two segments",
            isinstance(page_link, dict)
            and re.fullmatch(r"/entity/[a-z]+/[a-z0-9][a-z0-9-]*", page_link.get("path") or "")
            is not None
            # No query string, and nothing percent-encoded: splitting the colon
            # out is the whole reason this address needs no escaping.
            and not page_link.get("search")
            and "%" not in (page_link.get("path") or "")
            and page_link.get("origin") == API,
            page_link,
        )

        # ── injecting twice leaves the page as it was ────────────────
        #
        # The toolbar button injects `content.js` directly, so a granted page
        # annotates without a reload: two ways in, both landing in the same
        # isolated world.
        #
        # WHAT THIS PROVES, AND WHAT IT DOES NOT. The page survives a second
        # injection -- no duplicated spans, none nested, no reset to zero. It
        # does NOT prove the re-entry guard in `content/index.ts` fired:
        # verified by removing the guard, whose absence this check does not
        # notice. A second `start()` re-splices nothing because every block is
        # already stamped at the current `index_version`, and the real cost of
        # two copies (MutationObservers treating each other's writes as page
        # churn) needs a mutating page to show. The guard is defensive, not
        # covered.
        before = await browser.evaluate("document.querySelectorAll('[data-venona-ref]').length")
        reinjected = await browser.evaluate_in_worker(
            "chrome.tabs.query({}).then(ts => {"
            "  const t = ts.find(t => (t.url||'').includes('article.html'));"
            "  if (!t) return 'no tab';"
            "  return chrome.scripting"
            "    .executeScript({target: {tabId: t.id}, files: ['content.js']})"
            "    .then(() => 'injected', e => String(e));"
            "})"
        )
        await asyncio.sleep(2.0)
        after = await browser.evaluate("document.querySelectorAll('[data-venona-ref]').length")
        nested = await browser.evaluate(
            "document.querySelectorAll('[data-venona-ref] [data-venona-ref]').length"
        )
        check(
            "a second injection leaves the annotations exactly as they were",
            reinjected == "injected" and before == after and (before or 0) > 0 and nested == 0,
            f"{reinjected}: {before} refs before, {after} after, {nested} nested",
        )

        # ── the options page renders at all ──────────────────────────
        #
        # The surface the user has to operate for any of the above to happen:
        # nothing is annotated until a site is ticked here. Everything else in
        # this file writes `chrome.storage.sync` directly, so the page could
        # throw at module scope for weeks -- an empty settings panel, no list,
        # no error -- and every check would still be green.
        extension_id = await browser.evaluate_in_worker("chrome.runtime.id")
        options = await browser.evaluate_in_tab(
            f"chrome-extension://{extension_id}/options.html",
            """JSON.stringify({
              rows: document.querySelectorAll('#sites li').length,
              ticked: [...document.querySelectorAll('#sites input[type=checkbox]')]
                        .filter(b => b.checked).length,
              status: (document.getElementById('sitesStatus')||{}).textContent || '',
              // Proof `init()` ran to completion rather than dying part way:
              // this is set after the settings load, which is after the font
              // install and before the catalog render.
              origin: (document.getElementById('origin')||{}).textContent || '',
              baseUrl: (document.getElementById('baseUrl')||{}).value || '',
              fontStyle: !!document.getElementById('venona-fonts'),
              faces: document.fonts ? document.fonts.size : 0,
              family: getComputedStyle(document.body).fontFamily,
              pairingHidden: (document.getElementById('pairingRow')||{}).hidden === true,
            })""",
        )
        state = json.loads(options) if isinstance(options, str) else {}
        check(
            "the options page renders its site list",
            state.get("rows", 0) > 0,
            f"{state.get('rows')} rows, status {state.get('status')!r}",
        )
        # The dev server has auth off, so `renderAuthMode` must keep the
        # Connect with Google row hidden -- a regression here offers a sign-in
        # against a server with nothing to sign into.
        check(
            "the pairing row stays hidden against an auth-off server",
            state.get("pairingHidden") is True,
            state.get("pairingHidden"),
        )
        check(
            "and `init()` ran to completion, not part way",
            bool(state.get("origin")) and bool(state.get("baseUrl")),
            state,
        )
        # NOT "the granted site is ticked": the site granted above is the local
        # FIXTURE host and the catalog is the real API's list, so the two never
        # intersect and a tick assertion would test only the fixture's absence.
        check(
            "every row starts unticked, since a grant is per-site and explicit",
            state.get("ticked") == 0,
            f"{state.get('ticked')} ticked of {state.get('rows')}",
        )
        # The typefaces are installed by script here, not by a stylesheet --
        # `shared/fonts.ts` says why -- so nothing else can tell whether they
        # arrived. Without them this page silently renders in the platform mono.
        check(
            "the options page installs its typefaces",
            state.get("fontStyle") is True and state.get("faces", 0) >= 4,
            f"style tag {state.get('fontStyle')}, {state.get('faces')} faces",
        )
        check(
            "and resolves the chrome family from the token layer",
            "JetBrains Mono Variable" in str(state.get("family", "")),
            state.get("family"),
        )

        # THE OPTIONS PAGE FOLLOWS THE READER TOO, and is the surface most likely
        # to be left behind: a `color-scheme: dark` of its own pins it to one
        # palette while it consumes tokens carrying both -- dark chrome around
        # light-mode values, which looks deliberate at a glance. A fresh tab each
        # time, because the page reads the preference in a `watchTheme` at module
        # scope, so the value has to be set before the tab exists.
        async def options_background(theme: str) -> str:
            await browser.evaluate_in_worker(f"chrome.storage.sync.set({{theme: {theme!r}}})")
            return await browser.evaluate_in_tab(
                f"chrome-extension://{extension_id}/options.html",
                "getComputedStyle(document.body).backgroundColor",
            )

        options_dark = await options_background("dark")
        options_light = await options_background("light")
        await browser.evaluate_in_worker("chrome.storage.sync.remove('theme')")
        check(
            "the options page follows the reader's theme, both ways",
            bool(options_dark)
            and bool(options_light)
            and options_dark != options_light
            and _contrast_on_white(options_dark) > _contrast_on_white(options_light),
            f"dark={options_dark} light={options_light}",
        )

        # ── auto-routing: the catalog picks the instance ──────────────
        #
        # Two configured instances, the SECOND one active, and only the DEFAULT
        # one's cached catalog claiming the fixture host. A reload must annotate
        # as `default` -- visible as the session cache key's instance segment --
        # or routing followed the active instance and sent the page to the wrong
        # corpus. The second instance's baseUrl is the fixture origin on purpose:
        # granted, absent from the static theme matches, and therefore also the
        # theme-bridge registration case below.
        await browser.evaluate_in_worker(
            "chrome.storage.session.get(null).then(all => chrome.storage.session.remove("
            "Object.keys(all).filter(k => k.startsWith('annotate:'))))"
        )
        await browser.evaluate_in_worker(
            "(async () => {"
            "  const got = await chrome.storage.sync.get({instances: [], activeId: ''});"
            "  const instances = got.instances.filter(i => i.id !== 'second');"
            f"  instances.push({{id: 'second', label: 'ZZ', baseUrl: 'http://127.0.0.1:{HTTP_PORT}/api'}});"
            "  await chrome.storage.sync.set({instances, activeId: 'second'});"
            "  await chrome.storage.local.set({"
            "    'sites:catalog:default': {at: Date.now(), sites: ["
            "      {domain: '127.0.0.1', match: 'http://127.0.0.1/*',"
            "       configured: true, documents: 1}"
            "    ]},"
            "    'sites:catalog:second': {at: Date.now(), sites: []},"
            "  });"
            "})()"
        )
        await asyncio.sleep(2.0)
        await browser.evaluate("location.reload()")
        await asyncio.sleep(3.0)

        annotate_keys = await browser.evaluate_in_worker(
            "chrome.storage.session.get(null).then(all => "
            "JSON.stringify(Object.keys(all).filter(k => k.startsWith('annotate:'))))"
        )
        keys = json.loads(annotate_keys) if isinstance(annotate_keys, str) else []
        check(
            "a host claimed by exactly one catalog routes there, not to the active instance",
            any(k.startswith("annotate:default:") for k in keys)
            and not any(k.startswith("annotate:second:") for k in keys),
            annotate_keys,
        )

        # ── the theme bridge follows configured instance origins ──────
        theme_matches = await browser.evaluate_in_worker(
            "chrome.scripting.getRegisteredContentScripts({ids: ['venona-theme']})"
            ".then(s => JSON.stringify(s.flatMap(x => x.matches)))"
        )
        check(
            "a granted instance origin outside the static matches registers the theme bridge",
            theme_matches is not None and f"http://127.0.0.1:{HTTP_PORT}/*" in str(theme_matches),
            theme_matches,
        )

        # ── and the bridge actually READS the attribute ───────────────
        #
        # The only place in this file where the theme content script itself runs.
        # Every other theme check writes `chrome.storage.sync` directly, which
        # skips the half that decides WHAT gets written. Reachable only right
        # now: the second instance's grant has just registered the bridge against
        # the fixture origin, and a dynamic registration injects on the NEXT page
        # load. `data-theme` is the whole input; its absence is System, so the
        # two halves below are the two transitions that exist.
        await browser.evaluate_in_tab(
            f"http://127.0.0.1:{HTTP_PORT}/article.html",
            "(async () => {"
            "  document.documentElement.dataset.theme = 'light';"
            "  await new Promise(r => setTimeout(r, 800));"
            "  return document.documentElement.dataset.theme; })()",
            settle=3.0,
        )
        bridged = await browser.evaluate_in_worker(
            "chrome.storage.sync.get({theme: 'unset'}).then(s => s.theme)"
        )
        check(
            "the theme bridge copies an explicit choice out of the page",
            bridged == "light",
            bridged,
        )

        await browser.evaluate_in_tab(
            f"http://127.0.0.1:{HTTP_PORT}/article.html",
            "(async () => {"
            "  document.documentElement.dataset.theme = 'dark';"
            "  await new Promise(r => setTimeout(r, 400));"
            "  delete document.documentElement.dataset.theme;"
            "  await new Promise(r => setTimeout(r, 800));"
            "  return String(document.documentElement.dataset.theme); })()",
            settle=3.0,
        )
        back = await browser.evaluate_in_worker(
            "chrome.storage.sync.get({theme: 'unset'}).then(s => s.theme)"
        )
        check("and follows the removal of the attribute back to System", back == "system", back)
        await browser.evaluate_in_worker("chrome.storage.sync.remove('theme')")

        # Restore the single-instance state every later check assumes; the
        # removal must also deregister the second origin's theme bridge.
        await browser.evaluate_in_worker(
            "(async () => {"
            "  const got = await chrome.storage.sync.get({instances: [], activeId: ''});"
            "  const instances = got.instances.filter(i => i.id !== 'second');"
            "  await chrome.storage.sync.set({instances, activeId: 'default'});"
            "  await chrome.storage.local.remove("
            "    ['sites:catalog:default', 'sites:catalog:second']);"
            "})()"
        )
        await asyncio.sleep(2.0)
        theme_after = await browser.evaluate_in_worker(
            "chrome.scripting.getRegisteredContentScripts({ids: ['venona-theme']})"
            ".then(s => s.length)"
        )
        check("removing the instance deregisters its theme bridge", theme_after == 0, theme_after)

        # ── pausing keeps the grant and stops the injection ───────────
        #
        # The distinction the control exists for and the one invisible from the
        # page: a paused site must stay PERMITTED, or resuming prompts again.
        # Last, because it deregisters the script every check above depends on.
        await browser.evaluate_in_worker(
            f"chrome.storage.sync.set({{'sites:paused': ['{pattern}']}})"
        )
        await asyncio.sleep(2.0)

        paused_state = await browser.evaluate_in_worker(
            "Promise.all(["
            "  chrome.scripting.getRegisteredContentScripts({ids: ['venona-sites']})"
            "    .then(s => s.length),"
            f"  chrome.permissions.contains({{origins: ['{pattern}']}}),"
            "]).then(([n, granted]) => JSON.stringify({n, granted}))"
        )
        state = json.loads(paused_state) if isinstance(paused_state, str) else {}
        check("pausing a site deregisters the content script", state.get("n") == 0, paused_state)
        check(
            "and leaves its permission in place, so resuming does not re-prompt",
            state.get("granted") is True,
            paused_state,
        )

        # ── "Enable all" with nothing to enable ──────────────────────
        #
        # LAST, because it clobbers the cached catalog on purpose. A bare
        # `return` on an empty list is no prompt, no message and no change, and
        # an empty list is the ordinary consequence of the API being down when
        # the page first opened -- the user then clicks the one control that
        # turns annotation on and gets silence. It has to say so. The catalog is
        # cached per instance and a fresh profile migrates to id 'default', so
        # that is the key to clobber.
        await browser.evaluate_in_worker(
            "chrome.storage.local.set({'sites:catalog:default': {at: Date.now(), sites: []}})"
        )
        empty = await browser.evaluate_in_tab(
            f"chrome-extension://{extension_id}/options.html",
            """(async () => {
              document.getElementById('enableAll').click();
              await new Promise(r => setTimeout(r, 300));
              return (document.getElementById('sitesStatus')||{}).textContent || '';
            })()""",
        )
        check(
            "`Enable all` with an empty catalog says so instead of doing nothing",
            isinstance(empty, str) and "Refresh" in empty,
            repr(empty),
        )

    # ── what the browser loads by name ──────────────────────────────
    #
    # Not a browser check, but it belongs with them: nothing else looks at these.
    # `finish.mjs` verifies the PNGs it copies; this verifies the manifest points
    # at what was copied. A mismatch is a grey puzzle piece in the toolbar, which
    # no assertion above can see.
    manifest = json.loads((DIST / "manifest.json").read_text())
    declared = {
        *manifest.get("icons", {}).values(),
        *manifest.get("action", {}).get("default_icon", {}).values(),
    }
    check("the manifest declares icons", len(declared) == 4, sorted(declared))
    missing = [name for name in declared if not (DIST / name).exists()]
    check("every declared icon is in dist", not missing, missing)
    # THE SPLICER IS RUNTIME-GRANTED AND MUST STAY THAT WAY: where `content.js`
    # runs is decided by `shared/sites.ts` from explicit intent verified against
    # `permissions.contains`, and a static block would be a second mechanism
    # deciding the same thing. `theme.js` is the opposite shape -- it runs on
    # venona's OWN origins, the one place `sites.ts` refuses to put the splicer,
    # copying the app's theme choice into `chrome.storage` because the badge's
    # service worker has no DOM and cannot resolve `light-dark()`. So a static
    # block may name `theme.js` and nothing else, and match venona and nothing
    # else. `content.js` here would inject the splicer into the app itself,
    # which looks at first glance like a feature.
    static = manifest.get("content_scripts", [])
    static_js = sorted({name for entry in static for name in entry.get("js", [])})
    static_matches = sorted({m for entry in static for m in entry.get("matches", [])})
    check(
        "the only static content script is the theme sync",
        static_js == ["theme.js"],
        f"{static_js} -- the splicer's registration is granted at runtime; a static "
        f"entry for it would be a second mechanism deciding where it runs",
    )
    check(
        "and it is scoped to venona's own origins, nowhere else",
        static_matches
        and all(
            "127.0.0.1" in pattern for pattern in static_matches
        ),
        static_matches,
    )

    for line in OK:
        print(f"  ok    {line}")
    for line in FAIL:
        print(f"  FAIL  {line}")
    print(f"\n{len(OK)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
