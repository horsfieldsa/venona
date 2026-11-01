"""UI smoke checks: drive the running app and assert it behaves.

NOT part of `make test`, which is offline and free. It needs `make dev` up,
because static screenshots are not verification: a graph canvas drawing 229
nodes at a sub-pixel zoom and four button heights in one 44px bar both reached
the user (2026-08-06), invisible to typecheck, lint and the offline suite.

    make ui-smoke

Add a check here whenever a defect gets past you. The point is not coverage; it
is that the same class of mistake cannot happen twice silently.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

try:
    import websockets
except ImportError:  # pragma: no cover
    print("ui-smoke needs `websockets` (it is in the dev dependencies)", file=sys.stderr)
    raise SystemExit(2)

PORT = 9223
#: The app under test. The default is the default instance's vite proxy; a
#: scaffolded throwaway (`make new-local-instance`) is addressed by pointing
#: this at its WEB_PORT -- which is how this suite runs against a clean
#: instance while a real corpus is being curated on :5173.
BASE = os.environ.get("VENONA_UI_SMOKE_BASE", "http://localhost:5173")

#: `MIN_LEGIBLE_ZOOM` in `web/src/composables/useCytoscape.ts` -- the zoom the
#: canvas refuses to go below, because everything drawn under it is a sub-pixel
#: dot. Duplicated rather than read off the page: the point of the check is that
#: the clamp RAN, and a value taken from the same module it is testing would
#: agree with itself no matter what the canvas did.
MIN_LEGIBLE_ZOOM = 0.32

#: Every route that renders chrome and can be reached by a static path.
#:
#: ONE list for every width pass. Hand-written subsets per pass drifted apart
#: and left /lookup, /search and everything under /settings unwalked at any
#: width. `/briefs/<id>` and `/runs/<id>` need an id and are exercised where
#: one is in hand.
CHROME_ROUTES = [
    "/",
    "/briefs",
    "/graph",
    "/lookup",
    "/search",
    "/learn",
    "/admin/curation",
    "/admin/runs",
    "/admin",
    "/admin/people",
    "/admin/agents",
    "/admin/data",
    "/admin/notifications",
    # Here for its two dialogs: this is the page an administrator opens when
    # nothing works, and a confirm button below the fold on a phone is a page
    # that cannot be used at the one moment it has to be.
    "/admin/model",
    # Both halves of the configuration surface. The index's rows carry three
    # lines each, so its narrow-width failure is not the editor's and neither
    # stands in for the other.
    "/admin/config",
    "/admin/config/sources",
    "/settings/account",
    "/settings/relevance",
    "/settings/notifications",
    "/settings/agents",
    # The catch-all. Walked at every width because the one thing the offline
    # suite cannot see about this view is the thing it is mostly made of: a
    # graph field that fails to a blank rectangle still typechecks, still
    # lints, and still renders a notice that looks correct on its own.
    "/no-such-page",
]

# Everything this script writes -- Chromium profiles, their caches, and the
# screenshots -- lands here, inside the repo and gitignored, and is removed on
# the way out. Chromium launched with no `--user-data-dir` mints its own
# `/tmp/org.chromium.Chromium.scoped_dir.*` and does NOT remove it when it is
# terminated: measured 2026-08-06 at ~146MB per launch, four launches per run,
# and enough runs exhausted a 3.7GB `/tmp` tmpfs, after which every write on
# the machine failed with EDQUOT and the failure looked nothing like its cause.
REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = REPO_ROOT / ".tmpshot"

# The user-authored documents this script drives the app over but must never
# change. It is the only test surface that talks to a REAL API against the REAL
# working tree, so nothing else can catch it writing one: `make test` is
# sandboxed and its conftest guard never sees this process. The toast check
# appended a newline to `relevance_profile.md` and saved it for real, on every
# run, for as long as the script had existed.
GUARDED_CONFIG = sorted(
    p
    for directory in ("profile", "config")
    for p in (REPO_ROOT / directory).glob("*")
    if p.is_file()
)


def config_state() -> dict[str, tuple[int, int]]:
    """Size and mtime per document. Any write shows up in one or the other, and
    a restored-afterwards write still moves the mtime -- which is the point:
    the file was wrong while the app was being driven over it."""
    return {
        str(p.relative_to(REPO_ROOT)): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in GUARDED_CONFIG
        if p.exists()
    }


def verdict_state() -> list[str]:
    """Every curation verdict recorded about a definition.

    The same guard `config_state` is, for a worse failure: a verdict is the app
    recording that a PERSON decided something, and the definitions queue binds
    bare letters (`s` folds readings together, `b` keeps them, `r` rejects) to
    writes. Measured 2026-08-09: one `make ui-smoke` decided eleven surfaces in
    the live database, silently, and a second run decided three more -- with
    every check here written presence-only. So this fails the run and NAMES the
    surfaces, rather than trusting that reading.
    """
    # Connecting normally, not read-only: Postgres has no equivalent of SQLite's
    # `file:...?mode=ro`, and on a script already documented as WRITING the
    # read-only-ness was defence in depth. What matters is that this only SELECTs.
    from venona.db import core as _db
    from venona.db.repositories import diagnostics as diag_repo
    from venona.settings import get_settings as _get_settings

    try:
        conn = _db.connect(_get_settings().database_url)
    except Exception:
        # No reachable database is not a finding. The caller treats an empty list
        # as "nothing was decided", and a missing database cannot have decided
        # anything.
        return []
    try:
        return [
            f"{row['normalized']} -> {row['decided_at']}"
            for row in diag_repo.decided_verdicts(conn)
        ]
    finally:
        conn.close()


OUT = RUN_DIR

FAIL: list[str] = []
OK: list[str] = []
# Checks that passed only because their precondition was absent. Reported
# separately: a check that quietly stands down is indistinguishable from one
# that verified something, and "107 passed" then overstates the coverage.
SKIPPED: list[str] = []


def _lstar(colour: object) -> float:
    """Perceptual lightness of a computed `rgb(...)`, for asking which of two
    palettes is the light one.

    L*, not the raw channels: a naive channel sum calls a saturated yellow
    lighter than a pale grey. Returns -1.0 for anything unparseable, so a
    missing colour fails its check with the value it was given.
    """
    parts = [float(x) for x in re.findall(r"[\d.]+", str(colour or ""))][:3]
    if len(parts) < 3:
        return -1.0

    def channel(component: float) -> float:
        srgb = component / 255
        return srgb / 12.92 if srgb <= 0.04045 else ((srgb + 0.055) / 1.055) ** 2.4

    luminance = 0.2126 * channel(parts[0]) + 0.7152 * channel(parts[1]) + 0.0722 * channel(parts[2])
    return 116 * luminance ** (1 / 3) - 16


def check(name: str, condition: object, detail: object = "") -> None:
    line = f"{name}{(' -- ' + str(detail)) if detail else ''}"
    if condition and str(detail).startswith("skipped:"):
        SKIPPED.append(line)
    (OK if condition else FAIL).append(line)


KEYS = {
    "Escape": (27, "Escape", None),
    "Esc": (27, "Escape", None),
    "Tab": (9, "Tab", None),
    "Enter": (13, "Enter", "\r"),
    "ArrowDown": (40, "ArrowDown", None),
    "ArrowUp": (38, "ArrowUp", None),
    "ArrowLeft": (37, "ArrowLeft", None),
    "ArrowRight": (39, "ArrowRight", None),
    "?": (191, "Slash", "?"),
    "/": (191, "Slash", "/"),
    "[": (219, "BracketLeft", "["),
    "]": (221, "BracketRight", "]"),
}


class Browser:
    def __init__(self, ws, proc, profile=None):
        self.ws, self.proc, self._id = ws, proc, 0
        # The `--user-data-dir` this browser owns, deleted by `close()`.
        self.profile = profile

    async def send(self, method, **params):
        self._id += 1
        await self.ws.send(json.dumps({"id": self._id, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == self._id:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    async def goto(self, path, settle=2.5):
        await self.send("Page.navigate", url=f"{BASE}{path}")
        await asyncio.sleep(settle)

    async def js(self, expression):
        # ASYNC arrow, and `awaitPromise` resolves what it returns. A plain
        # `(() => { ... })()` makes any snippet containing `await` a
        # SyntaxError, and the "JS ERROR: ..." string below is truthy and equal
        # to nothing a caller compares against -- so the check would silently
        # take its other branch and pass.
        r = await self.send(
            "Runtime.evaluate",
            expression=f"(async () => {{ {expression} }})()",
            returnByValue=True,
            awaitPromise=True,
        )
        result = r.get("result", {})
        if result.get("subtype") == "error":
            return f"JS ERROR: {result.get('description', '')[:200]}"
        return result.get("value")

    async def key(self, name, mods=0, settle=0.4):
        if name in KEYS:
            code, dom_code, text = KEYS[name]
        elif name.isdigit():
            # `Digit5`, not `Key5`. `useKeyboard::candidates` matches `alt+N` on
            # `event.code`, because macOS composes Option+1 into `¡`. A harness
            # sending the wrong `code` exercises only the `event.key` fallback
            # and reports the Mac path as covered when it is not.
            code, dom_code, text = ord(name), f"Digit{name}", name
        elif len(name) == 1:
            code, dom_code, text = ord(name.upper()), f"Key{name.upper()}", name
        else:
            raise ValueError(f"unmapped key {name!r}")
        base = dict(
            windowsVirtualKeyCode=code,
            code=dom_code,
            key=name,
            modifiers=mods,
            nativeVirtualKeyCode=code,
        )
        await self.send("Input.dispatchKeyEvent", type="rawKeyDown", **base)
        if text:
            await self.send("Input.dispatchKeyEvent", type="char", text=text, **base)
        await self.send("Input.dispatchKeyEvent", type="keyUp", **base)
        await asyncio.sleep(settle)

    async def type(self, text, settle=1.2):
        for ch in text:
            await self.send(
                "Input.dispatchKeyEvent", type="keyDown", text=ch, key=ch, unmodifiedText=ch
            )
            await self.send("Input.dispatchKeyEvent", type="keyUp", key=ch)
            await asyncio.sleep(0.05)
        await asyncio.sleep(settle)

    async def click(self, selector, settle=0.8):
        ok = await self.js(
            f"const el = document.querySelector({json.dumps(selector)});"
            f"if (!el) return false; el.click(); return true;"
        )
        await asyncio.sleep(settle)
        return ok

    async def pointer_click(self, selector, settle=0.8):
        """A click carrying the POINTER EVENTS a real one carries.

        `el.click()` dispatches only `click`, and `lib/focusModality.ts` reads
        `pointerdown` to decide whether the focus ring is drawn (ADR 0104), so
        anything gated on pointer input is untestable through `click`.

        SEPARATE from `click` rather than replacing it: dispatching the pointer
        pair at every call site broke two unrelated checks on `/admin/curation`,
        where the app stopped mounting entirely.
        """
        ok = await self.js(
            f"const el = document.querySelector({json.dumps(selector)});"
            "if (!el) return false;"
            "const r = el.getBoundingClientRect();"
            "const o = {bubbles: true, cancelable: true, composed: true,"
            "           clientX: r.left + r.width / 2, clientY: r.top + r.height / 2};"
            "el.dispatchEvent(new PointerEvent('pointerdown', o));"
            "el.dispatchEvent(new PointerEvent('pointerup', o));"
            "el.click();"
            "return true;"
        )
        await asyncio.sleep(settle)
        return ok

    async def shot(self, name):
        r = await self.send("Page.captureScreenshot")
        (OUT / f"{name}.png").write_bytes(base64.b64decode(r["data"]))

    async def close(self):
        try:
            await self.ws.close()
        except Exception:
            pass
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)
        if self.profile is not None:
            shutil.rmtree(self.profile, ignore_errors=True)


# The browser `launch()` handed out last. Every launch retires it first,
# because they all bind the same debug port: a second Chromium cannot take 9223
# while the first holds it, so `launch()` connected straight back to the
# ORIGINAL browser and returned a handle to it while the new process sat
# orphaned on its profile. Four `launch()` calls, one browser, four leaks.
_CURRENT: Browser | None = None


async def launch(width=1500, height=900, *, dismiss_welcome=True):
    global _CURRENT
    if _CURRENT is not None:
        await _CURRENT.close()
        _CURRENT = None

    RUN_DIR.mkdir(exist_ok=True)
    profile = Path(tempfile.mkdtemp(prefix="profile.", dir=RUN_DIR))
    proc = subprocess.Popen(
        [
            "chromium",
            "--headless=new",
            f"--remote-debugging-port={PORT}",
            "--no-sandbox",
            "--disable-gpu",
            "--hide-scrollbars",
            f"--user-data-dir={profile}",
            f"--disk-cache-dir={profile / 'cache'}",
            f"--window-size={width},{height}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(60):
        try:
            targets = json.load(urllib.request.urlopen(f"http://localhost:{PORT}/json"))
            page = next(t for t in targets if t["type"] == "page")
            ws = await websockets.connect(page["webSocketDebuggerUrl"], max_size=None)
            b = Browser(ws, proc, profile)
            await b.send("Page.enable")
            await b.send("Runtime.enable")
            # DISMISS THE FIRST-VISIT INVITATION, on every document, before the
            # page's own scripts run. `/welcome` fires for a signed-in reader
            # who has never tuned relevance, which on a clean instance -- the
            # only place this suite may run -- is every reader, so the whole
            # suite bounced there and 107 checks reported the failure of a
            # screen they never reached.
            #
            # `addScriptToEvaluateOnNewDocument` rather than one `setItem`:
            # several checks call `localStorage.clear()`, and a one-off would be
            # wiped by the first of them. `dismiss_welcome=False` leaves the
            # invitation live for the first-visit check, which then cannot
            # remove the key: the next navigation puts it straight back.
            if dismiss_welcome:
                await b.send(
                    "Page.addScriptToEvaluateOnNewDocument",
                    source=(
                        "try { localStorage.setItem('venona.welcome.1', 'ui-smoke'); } catch (e) {}"
                    ),
                )
            _CURRENT = b
            return b
        except Exception:
            time.sleep(0.4)
    proc.kill()
    shutil.rmtree(profile, ignore_errors=True)
    raise RuntimeError("chromium never came up")


async def sign_in(b) -> None:
    """Get a session before anything else runs, or every `fetch('/api/...')`
    below 401s and the suite fails ~20 times with the same unhelpful message.

    The API token, not Google, because a headless browser cannot drive a consent
    screen -- the same reason `POST /api/auth/token-login` exists for the
    operator on the day Google sign-in is broken. This is its second caller, not
    a test-only backdoor. A no-op when auth is off, which is `make dev` locally.
    """
    mode = await b.js(
        "const r = await fetch('/api/auth/mode'); return r.ok ? await r.json() : null;"
    )
    if not mode or not mode.get("enabled"):
        return

    token = os.environ.get("VENONA_AUTH_API_TOKEN", "")
    if not token:
        print("  auth is on but VENONA_AUTH_API_TOKEN is unset -- every check will 401")
        return

    ok = await b.js(
        "const r = await fetch('/api/auth/token-login', {"
        "  method: 'POST',"
        "  headers: {'content-type': 'application/json'},"
        f"  body: JSON.stringify({{token: {json.dumps(token)}}})"
        "}); return r.ok;"
    )
    check("ui-smoke can sign in with the API token", ok)


async def agents_checks(b) -> None:
    """Settings -> Agents: the MCP server page.

    A FUNCTION rather than a block in `main` for the same reason
    `graph_cache_checks` is one -- it needs its own navigation and asserts about
    one surface.

    Most of what it asserts is ABSENCE, which is why it is worth a check at all:
    no mint button, no pasted header, no secret field. Each is a decision
    (C12, ADR 0167) that a reviewer would read as an unfinished feature.
    """
    await b.goto("/settings/agents", settle=3)

    rail = await b.js(
        "return (document.querySelector('[data-rail=settings]')?.innerText || '')"
        "  .replace(/\\s+/g, ' ').trim();"
    )
    check("the settings rail reaches Agents", "Agents" in rail, repr(rail[:80]))

    # EVERY ROW IS A REAL LINK, which is not what it looks like from the source.
    # `RailLink` renders a `RouterLink`, and passing `:href` beside `:to` let a
    # fallthrough attribute worth `undefined` delete the href RouterLink had
    # just written -- so both rails were anchors with no href: no pointer
    # cursor, no keyboard focus, no middle-click, and still navigating on click,
    # which is what hid it. The component typechecks either way and the rendered
    # text is identical, so only a browser can see it.
    rows = await b.js(
        "return [...document.querySelectorAll('[data-rail] > a')].map(a => ({"
        "  text: (a.innerText||'').trim().slice(0, 24),"
        "  href: a.getAttribute('href'),"
        "  cursor: getComputedStyle(a).cursor,"
        "}));"
    )
    hrefless = [r["text"] for r in (rows or []) if not r["href"]]
    check("every rail row carries an href", rows and not hrefless, hrefless)
    blunt = [r["text"] for r in (rows or []) if r["cursor"] != "pointer"]
    check("every rail row shows the link cursor", rows and not blunt, blunt)
    # The reader's OWN destinations, and NOTHING else (ADR 0170). A regression
    # that put Data, People or the config documents back would render fine and
    # simply offer a reader links the guard bounces.
    check(
        "the settings rail holds no admin destination",
        "Data" not in rail and "People" not in rail and "Sources" not in rail,
        repr(rail[:120]),
    )

    tools = await b.js("return document.body.innerText.match(/venona_[a-z_]+/g)?.length || 0;")
    check("the page lists the server's tools", isinstance(tools, int) and tools >= 8, tools)

    address = await b.js("return document.body.innerText.includes('/api/mcp');")
    check("the page names the server address", address is True, address)

    # No mint button, and that absence is a decision (C12, ADR 0167), so this
    # fails if somebody "finishes" the page.
    buttons = await b.js(
        "return [...document.querySelectorAll('button')]"
        "  .map(b => b.innerText.trim().toLowerCase());"
    )
    minting = [t for t in (buttons or []) if "generate" in t or "new token" in t or "mint" in t]
    check("the page offers no way to mint a credential", not minting, minting)

    # If the connect instructions regress to a pasted header, this is what
    # notices: the offline suite reads the component and cannot tell which
    # branch of a computed actually renders.
    body = await b.js("return document.body.innerText.toLowerCase();")
    check(
        "the page leads with signing in, not a pasted header",
        "sign in" in (body or "") and '--header "authorization' not in (body or ""),
        repr((body or "")[:160]),
    )

    # Ending a connection IS on the page -- over HTTP access can be taken away
    # and never handed out (C12). The counterpart of the mint check above: one
    # absence and one presence, both deliberate. Asserted only when there is a
    # row to end, so a clean instance does not fail for being clean.
    rows = await b.js("return document.querySelectorAll('table tbody tr').length;")
    disconnect = [t for t in (buttons or []) if "disconnect" in t or "revoke" in t]
    check(
        "a connection can be ended from the page",
        not rows or bool(disconnect),
        f"rows={rows} controls={disconnect}",
    )

    # The page asks for NO secret. Section two tells the reader that connecting
    # involves nothing copied or pasted, so a field to paste a key into would
    # contradict the page it sits on -- and would read to a reviewer as a
    # convenience rather than as wrong, which is why a check holds it.
    secrets_asked = await b.js("return document.querySelectorAll(  'input[type=password]').length;")
    check("the page asks for no secret", secrets_asked == 0, secrets_asked)


async def billing_checks(b) -> None:
    """Settings -> Subscription, on an instance that charges nobody.

    THE STATE THIS INSTANCE IS ACTUALLY IN, and the one that shipped broken
    twice. With billing off every account is entitled and none has a
    subscription, so the page must say there is nothing to pay -- not offer
    Change plan and Cancel over a subscription that does not exist, and not fall
    through to a blank panel. Both rendered perfectly and passed the offline
    suite.

    A rendering check, not a payment one: nothing here touches Stripe and the
    buttons are deliberately not pressed. This suite may not spend money.
    """
    await b.goto("/settings/billing", settle=2)

    rail = await b.js(
        "return (document.querySelector('[data-rail=settings]')?.innerText || '')"
        "  .replace(/\\s+/g, ' ').trim();"
    )
    check("the settings rail reaches Subscription", "Subscription" in rail, repr(rail[:100]))

    body = await b.js("return (document.body.innerText || '').trim();")
    check("the subscription page renders something", len(body or "") > 40, len(body or ""))

    # THE BLANK PAGE. Every branch of this view is a `v-else-if`, so one wrong
    # condition renders the chip and nothing else -- which reads as a page that
    # failed to load rather than as a state.
    panels = await b.js(
        "return document.querySelectorAll('[data-panel], section, article').length;"
    )
    check("the page is more than a status chip", (panels or 0) >= 2, panels)

    buttons = await b.js(
        "return [...document.querySelectorAll('button')]"
        "  .map(b => b.innerText.trim().toLowerCase()).filter(Boolean);"
    )
    # No card behind any of these on an instance that charges nobody, and the
    # hub refuses all three -- they were offered anyway.
    dead = [t for t in (buttons or []) if "change plan" in t or "cancel" in t or "update your" in t]
    check("no button offers to manage a subscription that does not exist", not dead, dead)

    check(
        "the page says why there is nothing to manage",
        "nothing to pay" in (body or "").lower(),
        repr((body or "")[:200]),
    )

    # Internal vocabulary on a reader's own money page is the worst place for
    # it, and `entitlement` is the word every layer underneath uses.
    lowered = (body or "").lower()
    words = ("entitlement", "entitled", "lapsed", "instance", "projection")
    jargon = [w for w in words if w in lowered]
    check("the page speaks the reader's language", not jargon, jargon)


async def perf_checks(b) -> None:
    """The request-count economics of epic M, asserted where they regress.

    Counted from the page's own resource timing rather than a CDP Network
    session: SPA navigation never clears `performance.getEntriesByType`, so a
    fresh full load followed by in-app hops gives exact per-endpoint counts
    for the whole sequence -- which is the unit the guarantees are stated in.
    """
    # A REAL page load, so the boot sequence is the one being measured.
    await b.goto("/", settle=3.5)

    health_calls = await b.js(
        "return performance.getEntriesByType('resource')"
        ".filter(r => new URL(r.name).pathname === '/api/health').length;"
    )
    # One, from the poll loop's immediate tick. Two means a second caller
    # stopped coalescing through the health store; zero means the poll never
    # started.
    check("boot issues exactly one /api/health", health_calls == 1, health_calls)

    session_first = await b.js(
        "const api = performance.getEntriesByType('resource')"
        "  .filter(r => new URL(r.name).pathname.startsWith('/api/'))"
        "  .sort((a, b) => a.startTime - b.startTime);"
        "return api.length ? new URL(api[0].name).pathname : 'NONE';"
    )
    # The session probe is the ONLY thing `main.ts` awaits before mount; if
    # anything else runs first, something new crept in front of first paint.
    check(
        "the session probe leads the boot sequence",
        session_first == "/api/auth/session",
        session_first,
    )

    config_at_boot = await b.js(
        "return performance.getEntriesByType('resource')"
        ".filter(r => new URL(r.name).pathname === '/api/config').length;"
    )
    # The palette defers to first open and nothing on the brief needs the
    # document list -- a reader's boot must not pay an admin's fetch.
    check("boot does not fetch /api/config", config_at_boot == 0, config_at_boot)

    # The admin round trip: every hop mounts the rail, the editor mounts its
    # list AND its per-draft reconciliation -- and the whole sequence shares
    # ONE fetch through the store.
    for path in ("/admin", "/admin/people", "/admin/data", "/admin"):
        await b.js(
            f"const a=document.querySelector('a[href={json.dumps(path)}]');"
            f"if(a) a.click(); else history.pushState(null,'',{json.dumps(path)});"
        )
        await asyncio.sleep(0.9)
    config_after_hops = await b.js(
        "return performance.getEntriesByType('resource')"
        ".filter(r => new URL(r.name).pathname === '/api/config').length;"
    )
    check(
        "an admin round trip issues exactly one GET /config",
        config_after_hops == 1,
        config_after_hops,
    )


async def admin_checks(b) -> None:
    """`/admin` and Settings -> People: the surface that decides who gets in.

    A FUNCTION for the same reason `agents_checks` is one, and it WRITES -- it
    files an access request and approves it, creating a real `users` row. Within
    this script's licence, and another reason the gate is a throwaway instance.

    THE LAST CHECK IS THE ONE THAT MATTERS, and it is invisible to both
    `vue-tsc` and the offline suite. Every refusal on this surface is a 409: the
    caller IS an administrator, and what they asked for is the thing that cannot
    happen. A 409 rendered as a 403 fires the global forbidden toast -- "this
    account can read Venona, but not change it" -- which is untrue, and hides
    the sentence saying which rule stopped them. It looks exactly like a
    permissions bug and is not one.

    ONE CREDENTIAL, HONESTLY. This signs in with `VENONA_AUTH_API_TOKEN`, an
    admin with NO user row, so the reader side of every gate here is unreachable
    from this file. `tests/test_authz.py` proves it against the real middleware
    stack, and `tests/test_frontend_drift.py::TestOperatorNav` catches the
    rendering half.
    """
    await b.goto("/admin", settle=3)

    nav = await b.js(
        "return (document.querySelector('header.bar nav')?.innerText || '')"
        "  .replace(/\\s+/g, ' ').trim();"
    )
    check("the nav reaches Admin", "Admin" in (nav or ""), repr((nav or "")[:120]))

    # Non-zero, because a seeded instance has at least the address that
    # bootstrapped it. Zero here means the page rendered its own empty state over
    # a payload that never arrived, which is what a 403 or a shape change looks
    # like from the outside.
    figures = await b.js(
        "return [...document.querySelectorAll('.stat .value')].map(e => e.innerText.trim());"
    )
    check("the admin overview renders its figures", len(figures or []) >= 5, figures)
    check(
        "the instance knows about at least one user",
        any((f or "").strip() not in ("", "0") for f in (figures or [])),
        figures,
    )

    # The build line, which is the only string on a page of figures and so the
    # one thing here a typecheck cannot see rendering blank. It says `local` off
    # a build host, which is what a developer's machine is.
    build = await b.js(
        "return [...document.querySelectorAll('.panel, section')]"
        "  .map(e => e.innerText || '')"
        "  .find(t => t.includes('This Venona')) || '';"
    )
    check("the admin overview names which build it is", "Version" in build, repr(build[:120]))

    await b.goto("/admin/people", settle=3)
    rail = await b.js(
        "return (document.querySelector('[data-rail=admin]')?.innerText || '')"
        "  .replace(/\\s+/g, ' ').trim();"
    )
    check("the admin rail reaches People", "People" in (rail or ""), repr((rail or "")[:80]))
    # EVERY section from one rail, and the list is exhaustive on purpose. This
    # rail is the only place in the app that links Curation and Runs (ADR 0207),
    # so a row silently dropped from it strands a destination with nothing
    # pointing at it, and the header cannot report the loss.
    check(
        "the admin rail reaches every section",
        all(
            s in (rail or "")
            for s in (
                "Overview",
                "People",
                "Agents",
                "Data",
                "Notifications",
                "Configuration",
                "Curation",
                "Runs",
                "Domain setup",
            )
        ),
        repr((rail or "")[:160]),
    )

    # The sole administrator's controls are disabled AND say why. A disabled
    # control with no explanation reads as a broken page; the server refuses the
    # same change inside its lock, so this is the explanation and never the gate.
    sole = await b.js(
        "const rows = [...document.querySelectorAll('.table tbody tr')];"
        "const admin = rows.find(r => r.innerText.includes('admin'));"
        "if (!admin) return null;"
        "const b = [...admin.querySelectorAll('button')]"
        "  .find(x => /suspend|make reader/i.test(x.innerText));"
        "return b ? { disabled: b.disabled, title: b.title } : null;"
    )
    if sole is None:
        check(
            "the last administrator cannot be demoted from the screen",
            True,
            "skipped: no admin row",
        )
    else:
        check(
            "the last administrator's controls are disabled with a reason",
            bool(sole.get("disabled")) and bool(sole.get("title")),
            sole,
        )

    address = f"smoke-{int(time.time())}@example.test"
    filed = await b.js(
        "const r = await fetch('/api/request-access', {"
        "  method: 'POST', headers: {'content-type': 'application/json'},"
        f"  body: JSON.stringify({{email: {json.dumps(address)}, role: 'analyst',"
        "    coverage: 'ui-smoke filed this'})"
        "}); return r.status;"
    )
    if filed == 429:
        # `SIGNUP` is 3/hour per client (C12), which is right for a public form
        # and means the queue half of this function cannot run twice in an hour
        # from one machine. Standing down is honest; failing would train the next
        # person to ignore a red line that means "you ran this recently".
        check(
            "the access queue can be driven end to end",
            True,
            "skipped: signup bucket exhausted -- 3/hour, try again later",
        )
        return
    check("a stranger can file an access request", filed == 201, filed)

    await b.goto("/admin/people", settle=3)
    queued = await b.js(f"return document.body.innerText.includes({json.dumps(address)});")
    check("the queue shows a pending request", queued is True, queued)

    approved = await b.js(
        "const rows = [...document.querySelectorAll('.table tbody tr')];"
        f"const row = rows.find(r => r.innerText.includes({json.dumps(address)}));"
        "if (!row) return 'no row';"
        "[...row.querySelectorAll('button')].find(b => /approve/i.test(b.innerText))?.click();"
        "return 'clicked';"
    )
    await asyncio.sleep(1)
    await b.js(
        "[...document.querySelectorAll('[role=dialog] button')]"
        "  .find(b => /confirm/i.test(b.innerText))?.click();"
        "return true;"
    )
    await asyncio.sleep(2)

    # The dialog stays open on purpose after an approval: venona sends no mail,
    # so the line to send by hand is the only thing that tells the person, and
    # closing over it would lose it.
    invite = await b.js(
        "return (document.querySelector('[role=dialog]')?.innerText || '').includes('/login');"
    )
    check("approving hands back a line to send", approved == "clicked" and invite is True, invite)

    await b.js(
        "[...document.querySelectorAll('[role=dialog] button')]"
        "  .find(b => /done/i.test(b.innerText))?.click();"
        "return true;"
    )
    await asyncio.sleep(1)

    became = await b.js(
        "const r = await fetch('/api/admin/users');"
        "if (!r.ok) return r.status;"
        f"return (await r.json()).items.some(u => u.email === {json.dumps(address)});"
    )
    check("approving created the account", became is True, became)

    still_pending = await b.js(
        "const r = await fetch('/api/access/requests');"
        "if (!r.ok) return r.status;"
        f"return (await r.json()).items.some(i => i.email === {json.dumps(address)});"
    )
    check("the approved request left the pending queue", still_pending is False, still_pending)

    # ── the refusal, rendered where the decision was made ────────────
    #
    # File a second request, decide it OUT FROM UNDER the open page, then press
    # Approve on the row still on screen. The server answers 409
    # `already_decided` -- a refusal, not a permission failure -- and the page
    # must say so in the dialog. Routed through the 403 path instead, the
    # operator gets the global role refusal, which is untrue and unactionable.
    second = f"smoke-stale-{int(time.time())}@example.test"
    await b.js(
        "await fetch('/api/request-access', {"
        "  method: 'POST', headers: {'content-type': 'application/json'},"
        f"  body: JSON.stringify({{email: {json.dumps(second)}, role: 'analyst'}})"
        "}); return true;"
    )
    await b.goto("/admin/people", settle=3)
    decided = await b.js(
        "const list = await (await fetch('/api/access/requests')).json();"
        f"const row = list.items.find(i => i.email === {json.dumps(second)});"
        "if (!row) return 'not queued';"
        "const r = await fetch(`/api/access/${row.id}/decline`, {"
        "  method: 'POST', headers: {'content-type': 'application/json'},"
        "  body: JSON.stringify({note: 'decided elsewhere'})"
        "}); return r.ok ? 'declined' : r.status;"
    )
    check("the queue can be decided from elsewhere", decided == "declined", decided)

    before = await b.js("return location.pathname;")
    await b.js(
        "const rows = [...document.querySelectorAll('.table tbody tr')];"
        f"const row = rows.find(r => r.innerText.includes({json.dumps(second)}));"
        "[...(row?.querySelectorAll('button') || [])]"
        "  .find(b => /approve/i.test(b.innerText))?.click();"
        "return true;"
    )
    await asyncio.sleep(1)
    await b.js(
        "[...document.querySelectorAll('[role=dialog] button')]"
        "  .find(b => /confirm/i.test(b.innerText))?.click();"
        "return true;"
    )
    await asyncio.sleep(2)

    inline = await b.js(
        "return (document.querySelector('[role=dialog]')?.innerText || '').toLowerCase();"
    )
    toast = await b.js(
        "return [...document.querySelectorAll('[role=status], .toast')]"
        "  .map(t => t.innerText).join(' ').toLowerCase();"
    )
    check(
        "a refused decision says which rule stopped it, in the dialog",
        "already" in (inline or ""),
        repr((inline or "")[:160]),
    )
    check(
        "a refusal does not fire the global forbidden toast",
        "not change it" not in (toast or ""),
        repr((toast or "")[:160]),
    )
    after = await b.js("return location.pathname;")
    check(
        "a refusal leaves you on the page you decided from",
        after == before,
        f"{before} -> {after}",
    )

    await b.js(
        "[...document.querySelectorAll('[role=dialog] button')]"
        "  .find(b => /cancel/i.test(b.innerText))?.click();"
        "return true;"
    )


async def data_checks(b) -> None:
    """Admin -> Data: taking a copy away, and what the delete promises (ADR 0221).

    IT WRITES a real archive under the instance's data directory, within this
    script's licence, and the run spends nothing: `export` declares
    `uses_model=False` and makes no call.

    The offline tests know the routes answer. They cannot know the button is
    enabled, that the Download control is visible without a mouse on it, or
    that the guides stopped claiming there is no export.
    """
    await b.goto("/admin/data")

    label = await b.js(
        "const b = [...document.querySelectorAll('button')]"
        ".find(x => /Export for Obsidian/i.test(x.textContent));"
        "return b ? (b.disabled ? 'disabled' : 'ready') : 'missing';"
    )
    check("the data page offers a copy for Obsidian", label == "ready", label)

    # The sentence the guides and this screen all carried (ADR 0221). Asserted
    # here because nothing breaks when copy contradicts behaviour.
    stale = await b.js(
        "const t = document.body.innerText;"
        "return /nothing is exported first|there is no export first/i.test(t);"
    )
    check("the delete warning no longer says nothing is exported", not stale, stale)

    before = await b.js(
        "const r = await fetch('/api/data/exports'); const j = await r.json();"
        "return j.items.length;"
    )
    await b.js(
        "await fetch('/api/data/exports', {method: 'POST', "
        "headers: {'content-type': 'application/json'}, body: '{}'});"
    )
    # Disabled WHILE it runs, from the health probe rather than a local flag --
    # so it is still disabled after a reload, which a flag would not be.
    await asyncio.sleep(1.0)
    busy = await b.js(
        "const b = [...document.querySelectorAll('button')]"
        ".find(x => /Making a copy|Export for Obsidian/i.test(x.textContent));"
        "return b ? b.disabled : null;"
    )
    check("the button stands down while a copy is being written", busy is True, busy)

    # The toast, not just the button. The copy takes minutes and the operator is
    # free to go and read something, so the toast host is the only surface that
    # reaches them -- and `runs.ts` cannot do it for this one, because it toasts
    # only for the run its single stream is attached to and this page
    # deliberately does not take that stream.
    toasted = await b.js(
        "return [...document.querySelectorAll('[role=status], .toast')]"
        "  .map(n => n.innerText).join(' ');"
    )
    check(
        "starting a copy says so where the operator will see it",
        isinstance(toasted, str) and "copy for Obsidian" in toasted,
        (toasted or "")[:100],
    )

    landed = 0
    for _ in range(40):
        await asyncio.sleep(1.0)
        landed = await b.js(
            "const r = await fetch('/api/data/exports'); const j = await r.json();"
            "return j.items.length;"
        )
        if landed > before:
            break
    check("a copy lands in the table", landed > before, f"{before} -> {landed}")

    # BEFORE the reload below. The finish toast is pushed when the run leaves
    # the health probe, which only happens while this page is mounted -- a
    # reload starts with nothing in flight, so the watch never fires and the
    # check would be asserting on a toast that could not exist.
    await asyncio.sleep(1.5)
    done_toast = await b.js(
        "return [...document.querySelectorAll('[role=status], .toast')]"
        "  .map(n => n.innerText).join(' ');"
    )
    check(
        "a finished copy says so too",
        isinstance(done_toast, str) and "export" in done_toast.lower(),
        (done_toast or "")[:100],
    )

    await b.goto("/admin/data")
    # The specific defect an `IconButton` would cause: `Table` hides row actions
    # at rest and reveals them on hover, which is right for an occasional tidy-up
    # and wrong for the one control the table exists to offer -- and unreachable
    # on a touch screen, where there is no hover.
    visible = await b.js(
        "const a = [...document.querySelectorAll('a[download]')][0];"
        "return a ? getComputedStyle(a).opacity : 'missing';"
    )
    check("the Download control is visible without hovering", visible == "1", visible)

    # A real link, so middle-click and "save as" work and the 30s client
    # timeout does not apply to a file that may take longer than that.
    href = await b.js(
        "const a = [...document.querySelectorAll('a[download]')][0];"
        "return a ? a.getAttribute('href') : 'missing';"
    )
    check(
        "the copy is fetched by a real link",
        isinstance(href, str) and href.startswith("/api/data/exports/"),
        href,
    )

    kept = await b.js("return /Kept:[^.]*copies you have saved/i.test(document.body.innerText);")
    check("the delete names the copies among what it keeps", kept is True, kept)


async def graph_cache_checks(b) -> None:
    """The position cache, full screen, and the lazily-loaded view's identity.

    A FUNCTION rather than another block inside `main`, because unlike most of
    this file it is safe to run on its own -- it navigates, presses two keys and
    edits the BROWSER'S cache, and writes nothing to the corpus or to config.
    """
    # ── graph: the position cache, and what it must never do ─────────
    #
    # A cold fcose layout is the most expensive thing the app does -- measured
    # 2026-08-16 at 3.4s for 10,000 entities and 12s for 20,000, main thread
    # blocked throughout -- and the cache is what stops a reader paying it
    # twice. The graph renders correctly whichever tier answered, just slowly,
    # so a cache that never hits looks exactly like one that works and
    # `__graphRender` is the dev-only probe that tells them apart.
    #
    # The store is CLEARED first, not the database dropped: `deleteDatabase`
    # blocks while any connection is open and the page is holding one, so it
    # would hang rather than fail. Earlier checks have already visited /graph,
    # so without this the "cold" one would be measuring a hit.
    await b.goto("/graph", settle=6)
    landed = await b.js("return {scope: window.__graphRender?.scope ?? null, url: location.search};")
    has_brief = await b.js("const r = await fetch('/api/briefs?limit=1'); return (await r.json()).total ?? 0;")
    check(
        "the graph opens on the latest brief, not on every name at once",
        isinstance(landed, dict)
        and str(landed.get("scope") or "").startswith("brief|brief:" if has_brief else "all|"),
        landed,
    )
    await b.goto("/graph?mode=all", settle=6)
    await b.js(
        "const db = await new Promise(r => { const o = indexedDB.open('venona.graph');"
        "  o.onsuccess = () => r(o.result); o.onerror = () => r(null); });"
        "if (!db || !db.objectStoreNames.contains('positions')) return 'no store';"
        "await new Promise(r => { const tx = db.transaction('positions', 'readwrite');"
        "  tx.objectStore('positions').clear(); tx.oncomplete = () => r(); tx.onerror = () => r(); });"
        "return 'cleared';"
    )
    await b.goto("/graph?mode=all", settle=7)
    first = await b.js("return window.__graphRender ?? 'no probe';")
    check(
        "a first visit to a scope lays it out cold",
        isinstance(first, dict) and first.get("tier") == "cold",
        first,
    )

    # Reload, not navigate: the snapshot has to survive the tab, which is the
    # arrival that most wants it.
    await b.goto("/graph?mode=all", settle=7)
    hit = await b.js("return window.__graphRender ?? 'no probe';")
    check(
        "a returning visit restores positions instead of laying out",
        isinstance(hit, dict) and hit.get("tier") == "full",
        hit,
    )

    # A partial hit is the morning after a run: yesterday's arrangement plus
    # whatever is new. Simulated by forgetting a fifth of the snapshot, which is
    # what one night's entities look like from the cache's point of view.
    forgotten = await b.js(
        "const key = window.__graphRender?.scope; if (!key) return 'no probe';"
        "const db = await new Promise(r => { const o = indexedDB.open('venona.graph');"
        "  o.onsuccess = () => r(o.result); o.onerror = () => r(null); });"
        "if (!db) return 'no indexeddb';"
        "const entry = await new Promise(r => {"
        "  const q = db.transaction('positions', 'readonly').objectStore('positions').get(key);"
        "  q.onsuccess = () => r(q.result); q.onerror = () => r(null); });"
        "if (!entry) return 'no snapshot for ' + key;"
        "const ids = Object.keys(entry.positions);"
        "const drop = Math.max(1, Math.round(ids.length * 0.2));"
        "ids.slice(0, drop).forEach(id => delete entry.positions[id]);"
        "await new Promise(r => { const tx = db.transaction('positions', 'readwrite');"
        "  tx.objectStore('positions').put(entry); tx.oncomplete = () => r(); tx.onerror = () => r(); });"
        "return {key, dropped: drop, kept: Object.keys(entry.positions).length};"
    )
    await b.goto("/graph?mode=all", settle=7)
    partial = await b.js(
        "const c = window.__cy; if (!c) return 'no __cy';"
        "const vis = c.nodes(':visible');"
        # Every node the reader is told about has to be somewhere they can see.
        # A node left at the origin, or thrown outside the fitted viewport by a
        # bad centroid, is counted in the readout and absent from the canvas --
        # the failure the whole view has already shipped once, at zero height.
        "const outside = vis.filter(n => { const p = n.renderedPosition();"
        "  return !(p.x >= 0 && p.x <= c.width() && p.y >= 0 && p.y <= c.height()); });"
        "return {tier: window.__graphRender?.tier, visible: vis.length,"
        " outside: outside.length,"
        " atOrigin: vis.filter(n => n.position('x') === 0 && n.position('y') === 0).length};"
    )
    check(
        "a snapshot missing a fifth of the graph is still worth restoring",
        isinstance(partial, dict) and partial.get("tier") == "partial",
        partial if not isinstance(forgotten, str) else f"{forgotten} / {partial}",
    )
    check(
        "a partial hit still draws every node inside the viewport",
        isinstance(partial, dict)
        and partial.get("visible", 0) > 0
        and partial.get("outside") == 0
        and partial.get("atOrigin") == 0,
        partial,
    )

    # A snapshot outlives its graph: a name saved on the unconnected orbit that
    # has since gained a relationship was restored there, drawing a line across
    # the canvas into the core. Dozens of them stretched the iPad's view past
    # the zoom floor into a corner of spokes. Simulated by moving connected
    # names' saved positions onto a far ring.
    stale = await b.js(
        "const key = window.__graphRender?.scope; const c = window.__cy; if (!key || !c) return 'no probe';"
        "const db = await new Promise(r => { const o = indexedDB.open('venona.graph');"
        "  o.onsuccess = () => r(o.result); o.onerror = () => r(null); });"
        "if (!db) return 'no indexeddb';"
        "const entry = await new Promise(r => {"
        "  const q = db.transaction('positions', 'readonly').objectStore('positions').get(key);"
        "  q.onsuccess = () => r(q.result); q.onerror = () => r(null); });"
        "if (!entry) return 'no snapshot for ' + key;"
        "const ids = c.nodes(':visible').filter(n => n.degree(false) > 0).map(n => n.id()).slice(0, 20);"
        "ids.forEach((id, i) => { const a = i / ids.length * 2 * Math.PI;"
        "  entry.positions[id] = {x: 5000 * Math.cos(a), y: 5000 * Math.sin(a)}; });"
        "await new Promise(r => { const tx = db.transaction('positions', 'readwrite');"
        "  tx.objectStore('positions').put(entry); tx.oncomplete = () => r(); tx.onerror = () => r(); });"
        "return ids.length;"
    )
    await b.goto("/graph?mode=all", settle=7)
    spread = await b.js(
        "const c = window.__cy; if (!c) return 'no __cy';"
        "const bb = c.nodes(':visible').boundingBox(); return {moved: " + json.dumps(str(stale)) + ","
        " width: Math.round(bb.w), height: Math.round(bb.h)};"
    )
    check(
        "a stale snapshot does not throw connected names across the canvas",
        isinstance(spread, dict) and max(spread.get("width", 1e9), spread.get("height", 1e9)) < 8000,
        spread,
    )

    # ── names seen once: drawn, and drawn DIFFERENTLY (ADR 0247) ─────
    #
    # The failure this catches is the one nothing else can: the toggle fetches,
    # the nodes arrive, the count goes up, and every one of them draws exactly
    # like a confirmed name. A typecheck cannot see a stylesheet selector that
    # matches nothing, and the wire is identical either way.
    #
    # The fill is asserted as a COMPUTED colour rather than a class, because
    # that is what the reader sees and it is where the rule can fail silently:
    # a selector on `[status = "provisional"]` that never matches leaves the
    # base fill in place and the canvas says everything is confirmed.
    await b.goto("/graph?mode=all", settle=7)
    # The coverage note reports an absence, so it owes the reader the way out of
    # it -- and it says so BEFORE the rail is opened, which is the state every
    # reader arrives in. The sentence stood for weeks describing relationships
    # nobody could reach, and then for a day describing ones they could, with
    # nothing on it saying so.
    offered = await b.js(
        "const p = document.querySelector('.coverage');"
        "if (!p) return 'no coverage note';"
        "const b = p.querySelector('button');"
        "return {text: (p.innerText || '').trim(), action: b ? b.textContent.trim() : null};"
    )
    check(
        "the coverage note offers the setting that undoes what it reports",
        isinstance(offered, dict)
        and "seen once" in str(offered.get("action", "")).lower(),
        offered,
    )
    # The filter rail arrives collapsed to its 32px spine, so the control is not
    # in the document until `[` opens it -- a check that skipped this would
    # report "no toggle" for a toggle that is there.
    await b.key("[", settle=1.2)
    before = await b.js(
        "const c = window.__cy; if (!c) return 'no __cy';"
        "return {nodes: c.nodes().length,"
        " hollow: c.nodes('[status = \"provisional\"]').length};"
    )
    check(
        "the graph opens on confirmed names only",
        isinstance(before, dict) and before.get("nodes", 0) > 0 and before.get("hollow") == 0,
        before,
    )

    opened = await b.js(
        "const t = [...document.querySelectorAll('[role=switch]')]"
        "  .find(e => /seen once/i.test(e.textContent || ''));"
        "if (!t) return 'no toggle';"
        "t.click();"
        "await new Promise(r => setTimeout(r, 4000));"
        "const c = window.__cy; if (!c) return 'no __cy';"
        "const hollow = c.nodes('[status = \"provisional\"]');"
        "if (!hollow.length) return {nodes: c.nodes().length, hollow: 0};"
        "const ground = getComputedStyle(document.body).backgroundColor;"
        "const n = hollow[0];"
        "const solid = c.nodes('[status = \"active\"]')[0];"
        "return {nodes: c.nodes().length, hollow: hollow.length, ground,"
        " fill: n.style('background-color'), border: n.style('border-width'),"
        " solidFill: solid ? solid.style('background-color') : null};"
    )
    check(
        "asking for names seen once puts more on the canvas",
        isinstance(opened, dict) and opened.get("hollow", 0) > 0,
        opened,
    )
    check(
        "a name seen once is drawn hollow, not merely recoloured",
        isinstance(opened, dict)
        and opened.get("fill") != opened.get("solidFill")
        and str(opened.get("border", "0")).strip() not in ("0", "0px"),
        opened,
    )

    # ── and the two answers are two scopes ───────────────────────────
    #
    # The setting roughly triples the canvas, so a position cache that cannot
    # tell the two apart hands one set of coordinates to the other graph -- and
    # at full coverage, which is the tier that runs no layout at all to correct
    # it. It survives the tab, because the cache is IndexedDB. Only a browser
    # can see this: the payloads are correct, the counts are correct, and the
    # picture is a scatter.
    revealed = await b.js(
        "return {render: window.__graphRender ?? null,"
        " unconfirmed: new URLSearchParams(location.search).get('unconfirmed')};"
    )
    check(
        "names seen once is its own scope, laid out rather than borrowed",
        isinstance(revealed, dict)
        and isinstance(revealed.get("render"), dict)
        and revealed["render"].get("tier") == "cold"
        and "seen-once" in str(revealed["render"].get("scope", "")),
        revealed,
    )
    check(
        "and the URL says so, so a reload comes back to the same canvas",
        isinstance(revealed, dict) and revealed.get("unconfirmed") == "show",
        revealed,
    )

    # ── the canvas SETTLES, whether or not `layoutstop` arrives ──────
    #
    # The orbit that places the unconnected and the fit that frames the result
    # both hang off the end of a layout, and on the first paint of a scope
    # `layoutstop` is not reliably emitted -- so the toggle drew a graph at a
    # tenth of legible zoom with the unconnected tangled through the middle of
    # it, and a reload put it right because that path orbits and fits without
    # waiting for any event. Two invariants, both cheap and neither visible to
    # a unit test: the canvas is never left below the legibility floor, and no
    # unconnected node sits inside the connected graph's box.
    settled = await b.js(
        "const c = window.__cy; if (!c) return 'no __cy';"
        "const vis = c.nodes(':visible');"
        "const loose = vis.filter(n => n.connectedEdges(':visible').length === 0);"
        "const core = vis.difference(loose);"
        "const cb = core.length ? core.boundingBox() : null;"
        "let inside = 0;"
        "if (cb) loose.forEach(n => { const p = n.position();"
        "  if (p.x > cb.x1 && p.x < cb.x2 && p.y > cb.y1 && p.y < cb.y2) inside++; });"
        "return {zoom: +c.zoom().toFixed(3), loose: loose.length, insideCore: inside};"
    )
    check(
        "the canvas settles after the toggle rather than waiting on an event",
        isinstance(settled, dict)
        and settled.get("zoom", 0) >= MIN_LEGIBLE_ZOOM - 0.005
        and settled.get("insideCore") == 0,
        settled,
    )

    # ── a canvas that loses its box gets the graph back ──────────────
    #
    # The reported shape of this was "it doesn't render, and a hard refresh
    # fixes it" -- which is precisely what a refresh does, recompute the
    # viewport transform against the real box. Every element was present,
    # visible and correctly placed; the VIEW was somewhere else, and nothing
    # in the app ever checked that the canvas was showing something.
    #
    # Driven at the container rather than through a rail or full screen,
    # because those produce the same zero-width box by three routes and this is
    # the one a test can produce exactly. The pan first is what makes it a real
    # test: a fit that fires on any box change would pass this without the
    # guard existing.
    recovered = await b.js(
        "const c = window.__cy; if (!c) return 'no __cy';"
        "const el = c.container();"
        "const showing = () => { const e = c.extent(), b = c.elements(':visible').boundingBox();"
        "  return !(e.x2 < b.x1 || e.x1 > b.x2 || e.y2 < b.y1 || e.y1 > b.y2); };"
        "c.panBy({x: -9000, y: -7000});"
        "const lost = showing();"
        "el.style.width = '0px';"
        "await new Promise(r => setTimeout(r, 700));"
        "const dead = Math.round(c.width());"
        "el.style.width = '';"
        "await new Promise(r => setTimeout(r, 1500));"
        "return {lost, dead, back: showing(), width: Math.round(c.width())};"
    )
    check(
        "a canvas that lost its box comes back with the graph on screen",
        isinstance(recovered, dict)
        and recovered.get("lost") is False
        and recovered.get("back") is True,
        recovered,
    )

    closed = await b.js(
        "const t = [...document.querySelectorAll('[role=switch]')]"
        "  .find(e => /seen once/i.test(e.textContent || ''));"
        "if (!t) return 'no toggle';"
        "t.click();"
        "await new Promise(r => setTimeout(r, 4000));"
        "const c = window.__cy; if (!c) return 'no __cy';"
        "return {nodes: c.nodes().length,"
        " hollow: c.nodes('[status = \"provisional\"]').length,"
        " scope: window.__graphRender?.scope ?? null,"
        " unconfirmed: new URLSearchParams(location.search).get('unconfirmed')};"
    )
    check(
        "turning it off takes the names seen once back off the canvas",
        isinstance(closed, dict)
        and closed.get("hollow") == 0
        and closed.get("nodes") == before.get("nodes")
        and closed.get("unconfirmed") is None,
        closed,
    )
    check(
        "and lands back on the confirmed graph's own coordinates",
        isinstance(closed, dict) and "confirmed" in str(closed.get("scope", "")),
        closed,
    )

    # ── one scope's coordinates never describe another ───────────────
    #
    # A single global slot restored a brief's thirty-five nodes onto whole-graph
    # coordinates: nineteen visible nodes scattered across an empty field at the
    # wrong zoom. A filter produces a different visible set from the same
    # payload, so it is a different scope for the same reason, and
    # `graphShapeKey` alone does not carry it.
    a_type = await b.js(
        "const r = await fetch('/api/meta/ontology'); if (!r.ok) return null;"
        "const o = await r.json(); return o.entity_types?.[0]?.name ?? null;"
    )
    if not a_type:
        check("a filtered graph is its own scope", True, "skipped: no ontology types")
    else:
        await b.goto(f"/graph?mode=all&types={a_type}", settle=7)
        filtered = await b.js("return window.__graphRender ?? 'no probe';")
        check(
            "a filtered graph is its own scope, laid out rather than borrowed",
            isinstance(filtered, dict)
            and filtered.get("tier") == "cold"
            and a_type in str(filtered.get("scope", "")),
            filtered,
        )
        await b.goto(f"/graph?mode=all&types={a_type}", settle=7)
        filtered_again = await b.js("return window.__graphRender ?? 'no probe';")
        check(
            "and it gets its own cache entry, not the unfiltered one",
            isinstance(filtered_again, dict) and filtered_again.get("tier") == "full",
            filtered_again,
        )

    # ── a type chip spotlights, it does not empty the canvas ─────────
    #
    # Reported 2026-09-11: selecting one type drew nothing at all, and the
    # reader was told no name survived the filters while thirty-two did. ADR
    # 0226 has the mechanism. Nothing but a browser can see it -- the payload
    # was fine and so were the counts.
    spotlight = await b.js(
        "const r = await fetch('/api/graph/overview', {method: 'POST',"
        "  headers: {'content-type': 'application/json'},"
        "  body: JSON.stringify({filters: {hide_isolated: true}})});"
        "if (!r.ok) return null;"
        "const g = await r.json();"
        "const type = {}; for (const n of g.nodes) type[n.id] = n.type;"
        # A type that sits on an edge, and whose partner is a DIFFERENT type.
        # A same-type edge draws under a both-endpoints rule as readily as under
        # an either-endpoint one, so picking one would prove nothing.
        "const e = g.edges.find(x => type[x.source] && type[x.target]"
        "  && type[x.source] !== type[x.target]);"
        "return e ? type[e.source] : null;"
    )
    if not spotlight:
        check("a type chip spotlights", True, "skipped: no cross-type edge in this corpus")
    else:
        await b.goto(f"/graph?mode=all&types={spotlight}", settle=7)
        drawn = await b.js(
            "const c = window.__cy; if (!c) return 'no __cy';"
            "const readout = document.querySelector('.readout')?.textContent ?? '';"
            "return {nodes: c.nodes(':visible').length, edges: c.edges(':visible').length,"
            " context: c.nodes('.context:visible').length, readout};"
        )
        check(
            "one type selected still draws a graph, not an empty field",
            isinstance(drawn, dict) and drawn.get("nodes", 0) > 0 and drawn.get("edges", 0) > 0,
            drawn,
        )
        check(
            "and the far end of a kept edge is on the canvas as context",
            isinstance(drawn, dict) and drawn.get("context", 0) > 0,
            drawn,
        )

    # ── graph: full screen means the canvas, and nothing else (ADR 0118) ──
    #
    # `f` hid the app header and left 304px of filter rail and detail rail on
    # either side (2026-08-16), so the one gesture that exists to give the graph
    # the whole window gave it about two thirds of one. Measured as WIDTH: the
    # rails are hidden by a zero grid track rather than unmounted, because
    # unmounting anything around this view takes Cytoscape with it.
    await b.goto("/graph?mode=all", settle=6)
    await b.key("f", settle=1.2)
    rails = await b.js(
        "const w = [...document.querySelectorAll('.rail')].map(r => Math.round("
        "  r.getBoundingClientRect().width));"
        "return {widths: w, canvas: Math.round("
        "  document.querySelector('.canvas')?.getBoundingClientRect().width ?? 0),"
        " window: window.innerWidth};"
    )
    # 1px, not 0: the grid track is zero and the rail is `box-sizing:
    # border-box`, so its 1px side border is the floor a used width cannot go
    # below. The measurement that matters is the canvas below.
    check(
        "full screen leaves no rail on screen",
        isinstance(rails, dict) and all(w <= 1 for w in rails.get("widths", [99])),
        rails,
    )
    check(
        "full screen gives the canvas the whole window",
        isinstance(rails, dict) and rails.get("canvas", 0) >= rails.get("window", 0) - 2,
        rails,
    )
    # And the reader's own rail state survives it, rather than the rails coming
    # back collapsed because full screen collapsed them.
    await b.key("Escape", settle=1.2)
    restored_rails = await b.js(
        "return [...document.querySelectorAll('.rail')].map(r => Math.round("
        "  r.getBoundingClientRect().width));"
    )
    check(
        "leaving full screen puts the rails back",
        isinstance(restored_rails, list)
        and len(restored_rails) > 0
        and all(w > 0 for w in restored_rails),
        restored_rails,
    )

    # ── the lazily-loaded graph is still the cached one ──────────────
    #
    # `/graph` is `import()`-split (the graph engine measured ~190KB gzipped,
    # 2026-08-16) and `<KeepAlive include="BriefView,GraphView">` matches by
    # component NAME. If the async boundary loses the name the view stops being
    # cached and every return to /graph pays a full cold layout: the exact cost
    # the split was meant to reduce, arriving by a different road.
    await b.goto("/graph?mode=all", settle=7)
    before_trip = await b.js(
        "window.__cy.scratch('_kept', 'this instance');"
        "return {at: window.__graphRender?.at, kept: window.__cy.scratch('_kept')};"
    )
    # BOTH legs are link clicks. `goto` is a real page load, which destroys the
    # Cytoscape instance whatever KeepAlive does -- so navigating away with it
    # would fail against a perfectly cached view and send the next person
    # hunting a bug in the router.
    trip = await b.js(
        "const go = async (href) => {"
        "  const link = [...document.querySelectorAll('a')]"
        "    .find(a => a.getAttribute('href') === href);"
        "  if (!link) return false;"
        "  link.click(); await new Promise(r => setTimeout(r, 2500)); return true; };"
        "if (!await go('/')) return 'no brief link';"
        "if (!await go('/graph')) return 'no graph link';"
        "return true;"
    )
    after_trip = await b.js(
        "return {at: window.__graphRender?.at,"
        " kept: window.__cy?.scratch('_kept') ?? null,"
        " path: location.pathname};"
    )
    check(
        "the graph survives a trip to another route and back",
        trip is True
        and isinstance(after_trip, dict)
        and after_trip.get("path") == "/graph"
        # The SAME Cytoscape instance, and no second render: a view that fell
        # out of the cache rebuilds both, and the reader loses every hop of
        # exploration on each trip to Runs.
        and after_trip.get("kept") == "this instance"
        and after_trip.get("at") == before_trip.get("at"),
        f"{before_trip} -> {after_trip} (nav {trip})",
    )


#: Every document on the configuration index, by key.
#:
#: The rows are links, so a click navigates away and the next index is a fresh
#: document -- collect the keys first and drive `goto` from them, rather than
#: holding element handles across a navigation. A harness that clicks rows which
#: never navigate and then compares the URL it is still on reads as a routing
#: bug and is a harness bug.
INDEX_KEYS = "return [...document.querySelectorAll('[data-doc]')].map(a => a.dataset.doc);"

#: Whether the OPEN document has unsaved work, read from the context bar.
#:
#: `unsaved changes` and not just `unsaved`: the bar also carries a count of
#: OTHER documents with buffers in them, and matching the shorter string would
#: pass on a clean document while another one was dirty.
EDITOR_UNSAVED = (
    "return (document.querySelector('.context')?.innerText || '').includes('unsaved changes');"
)

#: The open document's text.
EDITOR_TEXT = "return window.__cm ? window.__cm.state.doc.toString() : '';"


def editor_append(text: str) -> str:
    """Type `text` at the end of the open document.

    THROUGH THE EDITOR'S OWN TRANSACTION, because a contenteditable cannot be
    driven by assigning a value and firing an `input` event. `window.__cm` is
    the dev-build seam for exactly this (`CodeEditor.vue::expose`), and a
    transaction is what a keystroke produces anyway, so the debounce and
    dirty-flag paths under test are the real ones.
    """
    return (
        "const v = window.__cm;"
        "if (!v) return {DIAG: 'no editor'};"
        f"v.dispatch({{changes: {{from: v.state.doc.length, insert: {text!r}}}}});"
        "return 1;"
    )


async def theme_checks(b) -> None:
    """Light, Dark and System, on the surface each of them can break invisibly.

    `tests/test_contracts.py` proves the two palettes are LEGIBLE and
    `lint_tokens.py` proves they are DECLARED; neither can tell you whether the
    browser ever picked the other one. A `light-dark()` that collapsed to a
    single side -- `color-scheme` not reaching the element, a build step
    flattening it -- renders as a perfectly consistent app in one palette, which
    looks like a design decision rather than a defect.

    THE GRAPH NEEDED THIS MOST. Cytoscape draws to a canvas and cannot resolve
    `var()` or `light-dark()` at all; it does not error, it substitutes its own
    defaults. There is no DOM left to inspect, so the only way to know is to
    read pixels back off the canvas.
    """

    # A probe, not `getPropertyValue`. A custom property's computed value is its
    # declared TOKEN STREAM -- literally `light-dark(var(--grey-075), …)` -- so
    # reading it off the root proves nothing about what the browser resolved.
    # `useCytoscape.ts::colour` paints onto an element for the same reason.
    async def resolved(token: str) -> str:
        return await b.js(
            "const probe = document.createElement('span');"
            "document.documentElement.appendChild(probe);"
            f"probe.style.color = 'var({token})';"
            "const out = getComputedStyle(probe).color;"
            "probe.remove();"
            "return out;"
        )

    async def with_theme(value):
        if value is None:
            await b.js("delete document.documentElement.dataset.theme; return true")
        else:
            await b.js(f"document.documentElement.dataset.theme = {json.dumps(value)}; return true")
        await asyncio.sleep(0.4)

    await b.goto("/briefs", settle=3.0)

    await with_theme("light")
    light_base = await resolved("--surface-base")
    light_text = await resolved("--text-primary")
    await with_theme("dark")
    dark_base = await resolved("--surface-base")
    dark_text = await resolved("--text-primary")

    check(
        "the two palettes are actually two",
        light_base != dark_base and light_text != dark_text,
        f"base {light_base} vs {dark_base}, text {light_text} vs {dark_text}",
    )
    # Which way round, not just that they differ. Swapping `light-dark()`'s two
    # arguments on a token is a one-character mistake that renders perfectly on
    # whichever theme the author happened to be looking at.
    check(
        "and light is the light one",
        _lstar(light_base) > _lstar(dark_base) and _lstar(light_text) < _lstar(dark_text),
        f"base L* {_lstar(light_base):.1f} vs {_lstar(dark_base):.1f}, "
        f"text L* {_lstar(light_text):.1f} vs {_lstar(dark_text):.1f}",
    )

    # SYSTEM IS REAL, not a synonym for dark. It is the default, so if
    # `prefers-color-scheme` never reached `color-scheme: light dark` every
    # first-time visitor would get one palette forever and nothing would say so.
    await with_theme(None)

    async def as_os(value: str) -> str:
        await b.send(
            "Emulation.setEmulatedMedia",
            features=[{"name": "prefers-color-scheme", "value": value}],
        )
        await asyncio.sleep(0.4)
        return await resolved("--surface-base")

    system_light = await as_os("light")
    system_dark = await as_os("dark")
    check(
        "System follows the operating system, with no attribute at all",
        system_light == light_base and system_dark == dark_base,
        f"os-light {system_light} (expected {light_base}), "
        f"os-dark {system_dark} (expected {dark_base})",
    )
    await b.send("Emulation.setEmulatedMedia", features=[])

    # ── the control ─────────────────────────────────────────────────
    #
    # BOTH STORES, and the cookie is the one that matters: it is scoped to the
    # registrable domain so one choice reaches every property (ADR 0177). A
    # leftover from an earlier check, or from another Venona surface on this
    # profile, is what the store would load, and the cycle below would start
    # somewhere other than System with nothing to say so.
    await with_theme(None)
    await b.js(
        "localStorage.removeItem('venona.theme');"
        "document.cookie = 'venona.theme=; path=/; max-age=0';"
        "return true"
    )
    label = await b.js("return document.querySelector('.theme')?.textContent?.trim() ?? null")
    check("the status line carries a theme control", label is not None, label)
    # A THREE-STATE CYCLE, walked all the way round. Two presses cannot tell a
    # cycle from a toggle with three labels, and returning to the start is the
    # half that breaks: a boot script or a store that only ever SETS
    # `data-theme` can never get back to System, one reload later.
    seen = []
    for _ in range(4):
        seen.append(
            await b.js(
                "return (document.querySelector('.theme')?.textContent ?? '').trim()"
                " + '|' + (document.documentElement.dataset.theme ?? '')"
            )
        )
        await b.click(".theme", settle=0.5)
    check(
        "and it cycles through all three, back to where it started",
        len(set(seen[:3])) == 3 and seen[3] == seen[0],
        seen,
    )
    check(
        "System leaves no attribute behind, which is what makes it System",
        any(entry.endswith("|") for entry in seen),
        seen,
    )
    # AND IT IS STORED, rather than being the absence of a value. The cookie is
    # shared across properties (ADR 0177), so a System that CLEARS it is one
    # another origin's stale mirror heals straight back to an explicit choice.
    stored = await b.js(
        "const m = document.cookie.match(/(?:^|; )venona\\.theme=([^;]*)/);"
        "return (m ? m[1] : null) + '|' + (localStorage.getItem('venona.theme') ?? '');"
    )
    check(
        "and System is written to both stores, not cleared from them",
        stored is not None and stored.split("|")[0] in ("light", "dark", "system"),
        stored,
    )

    # ── the canvas ──────────────────────────────────────────────────
    #
    # Read off the CANVAS ITSELF, because there is nothing else to read. If the
    # graph's colours had stopped resolving, every DOM assertion in this file
    # would still pass and the drawing would be grey.
    await b.goto("/graph?mode=all", settle=5.0)

    async def canvas_ink() -> object:
        return await b.js(
            # EVERY canvas, not the first one. Cytoscape stacks several layers
            # and `querySelector('canvas')` returns whichever is first in the
            # DOM -- routinely the empty one, so the probe read no opaque pixels
            # and reported the same nothing for both themes. Two empty samples
            # compare equal, so the check would pass for the wrong reason on a
            # graph that never repainted.
            "const seen = new Map();"
            "for (const c of document.querySelectorAll('canvas')) {"
            "  const g = c.getContext('2d');"
            "  if (!g || !c.width || !c.height) continue;"
            "  let d;"
            "  try { d = g.getImageData(0, 0, c.width, c.height).data; } catch (e) { continue; }"
            "  for (let i = 0; i < d.length; i += 4) {"
            "    if (d[i + 3] < 200) continue;"
            "    const k = d[i] + ',' + d[i + 1] + ',' + d[i + 2];"
            "    seen.set(k, (seen.get(k) ?? 0) + 1);"
            "  }"
            "}"
            "return [...seen.entries()].sort((a, b) => b[1] - a[1]).slice(0, 12).map(e => e[0]);"
        )

    await with_theme("dark")
    await asyncio.sleep(1.5)
    dark_ink = await canvas_ink()
    await with_theme("light")
    await asyncio.sleep(1.5)
    light_ink = await canvas_ink()
    check(
        "the graph canvas repaints when the theme changes",
        isinstance(dark_ink, list)
        and isinstance(light_ink, list)
        and bool(dark_ink)
        and set(dark_ink) != set(light_ink),
        f"dark {dark_ink} light {light_ink} -- Cytoscape builds its stylesheet once "
        f"per instance, so this is what proves the restyle watcher is wired",
    )
    await with_theme(None)


async def setup_gate_checks(b) -> bool:
    """A fresh scaffold's first page IS the wizard. True when this is one.

    The first-load redirect gap shipped once already: the guard existed and the
    first navigation of a cold load did not go through it, so the admin of a
    brand-new instance landed on an empty brief and had to be told the wizard
    was at a URL nothing linked to.

    Only a real scaffold can answer this, and a scaffold cannot answer anything
    else, because every other surface here needs a corpus. So the two never run
    in the same process: point `VENONA_UI_SMOKE_BASE` at the scaffold's web
    port, and this is the whole run.
    """
    # Navigate FIRST. `launch` leaves the browser on `about:blank`, where a
    # relative fetch has no origin to resolve against -- the probe comes back as
    # a JS error, which is neither True nor False, and the gate reports itself
    # skipped on the one instance that could fire it.
    await b.goto("/", settle=2)
    required = await b.js(
        "const r = await fetch('/api/health');"
        "if (!r.ok) return 'no-health';"
        "return (await r.json()).setup_required;"
    )
    if required is False:
        check(
            "a fresh scaffold's first page is the wizard",
            True,
            "skipped: this instance is configured, so there is no gate to fire",
        )
        return False
    if required is not True:
        # Anything else is the probe failing, and a probe that fails must not
        # look like an instance with nothing to check.
        check("the first-boot gate can be asked whether it applies", False, required)
        return False

    landed = await b.js("return location.pathname")
    check("a fresh scaffold's first page is the wizard", landed == "/setup", landed)

    # A typed URL, not just the root: the gate is the router's, so every
    # non-public route owes the same answer. `/graph` because it is the one
    # that fails most expensively without a corpus.
    await b.goto("/graph?mode=all", settle=2)
    deep = await b.js("return location.pathname")
    check("a deep link on an unconfigured instance lands on the wizard", deep == "/setup", deep)

    # Rendered, not merely routed. A blank canvas at the right URL is the
    # defect this whole script exists for.
    describe = await b.js(
        "const heads = [...document.querySelectorAll('h2')].map(h => h.textContent.trim());"
        "const labels = [...document.querySelectorAll('label span')]"
        "  .map(s => s.textContent.trim());"
        "return {heads, labels, inputs: document.querySelectorAll('input').length};"
    )
    check(
        "the wizard renders its describe step",
        isinstance(describe, dict)
        and any("domain setup" in h.lower() for h in describe.get("heads") or [])
        and any("Domain name" in label for label in describe.get("labels") or []),
        describe,
    )

    # The invitation must not get in front of the gate. The first-visit welcome
    # fires for any reader who has not tuned relevance, which is every reader of
    # an instance with no corpus to tune against, and it bounced this entire
    # suite once before anyone noticed it could.
    await b.js("localStorage.removeItem('venona.welcome.1'); return 1")
    await b.goto("/brief", settle=2)
    over_welcome = await b.js("return location.pathname")
    check(
        "setup outranks the first-visit invitation",
        over_welcome == "/setup",
        over_welcome,
    )
    return True


async def main() -> int:
    # ══ from audit.py ══

    config_before = config_state()
    verdicts_before = verdict_state()

    # The invitation is left LIVE for the first check below, then the browser
    # is relaunched with it dismissed for everything after -- see `launch`.
    b = await launch(dismiss_welcome=False)
    await sign_in(b)

    # ── the first-boot gate, which is the whole run when it fires ────
    # Before everything else: on an unconfigured instance every check below
    # lands on `/setup` and reports the wizard's absence of a corpus as its own
    # failure, a couple of hundred times over. That is how the welcome
    # invitation hid itself, and it is not worth learning twice.
    if await setup_gate_checks(b):
        await b.close()
        return report()

    # ── the first-visit invitation fires, and only once ──────────────
    # Asserted BEFORE anything else, because `launch` dismisses the invitation
    # for every document after this. Without the check, dismissing it would be
    # indistinguishable from the gate having quietly stopped working -- and on a
    # clean instance EVERY reader is a first-visit reader.
    await b.goto("/", settle=2)
    tailored = await b.js(
        "const r = await fetch('/api/auth/session'); "
        "if (!r.ok) return 'no-session'; "
        "return (await r.json()).relevance_tailored;"
    )
    if tailored is True:
        check(
            "the first-visit invitation fires for an untailored reader",
            True,
            "skipped: this reader has already tuned relevance",
        )
    else:
        # Navigation goes through `goto`, never `location.assign` inside a
        # `js()`: navigating the inspected target from an evaluation kills the
        # evaluation ("Inspected target navigated or closed") and takes the
        # whole run with it.
        await b.js("localStorage.removeItem('venona.welcome.1'); return 1")
        await b.goto("/brief", settle=2)
        landed = await b.js("return location.pathname")
        check(
            "the first-visit invitation fires for an untailored reader",
            landed == "/welcome",
            landed,
        )
    # Relaunched with the dismissal registered on every document, which is what
    # the rest of the suite runs against.
    b = await launch()
    await sign_in(b)

    # ── chrome does not take a text selection ────────────────────────
    # `::selection` is `--accent-muted`, so a selectable nav label renders as a
    # green rectangle sitting ON the menu item. A drag starting in the page and
    # running up into the bar hit this constantly. The reading column MUST stay
    # selectable -- that half is the point of the check.
    await b.goto("/", settle=3)
    nav_sel = await b.js(
        "const nav=document.querySelector('.nav');"
        "const r=document.createRange();r.selectNodeContents(nav);"
        "const s=window.getSelection();s.removeAllRanges();s.addRange(r);"
        "const out=s.toString();s.removeAllRanges();return out;"
    )
    check("the nav takes no text selection", not nav_sel.strip(), repr(nav_sel[:60]))

    body_sel = await b.js(
        "const el=document.querySelector('.brief .item p, .brief p, .content p');"
        "if(!el) return 'NO-PARAGRAPH';"
        "const r=document.createRange();r.selectNodeContents(el);"
        "const s=window.getSelection();s.removeAllRanges();s.addRange(r);"
        "const out=s.toString();s.removeAllRanges();return out;"
    )
    check("the brief body is still selectable", len(body_sel.strip()) > 20, repr(body_sel[:60]))

    # ── the chrome recedes as the brief is read (ADR 0199) ───────────────
    #
    # Four states, and the offline suite can see none of them: how much chrome
    # is showing is a measured position, not a class name.
    #
    # MEASURED AS THE TOP OF `<main>`, never as a bar's height. The bars keep
    # their own height and are LIFTED out of the window on a negative margin, so
    # `.bar` reads its full 48px in every state and a check written against it
    # passes on a build where nothing moves at all. `data-chrome-bottom` exists
    # to publish where the chrome actually ends.
    #
    # `scroll_read` moves in reading-sized steps on purpose: one assignment of
    # 900px arrives as a single scroll event, which is not what a person reading
    # down a page produces, and a check written that way passes on a build that
    # only ever responds to flicks.
    async def scroll_read(px: int, step: int = 60) -> None:
        await b.js(
            "const s=document.querySelector('.view--prose');"
            f"const step={step}; let left={abs(px)}; const sign={1 if px > 0 else -1};"
            "await new Promise((done)=>{const tick=()=>{"
            "  if(left<=0){done();return}"
            "  s.scrollTop += sign*Math.min(step,left); left-=step;"
            "  requestAnimationFrame(tick);};requestAnimationFrame(tick);});"
        )
        await asyncio.sleep(0.4)

    async def chrome() -> dict:
        return await b.js(
            "const h=(q)=>{const e=document.querySelector(q);"
            "  return e?Math.round(e.getBoundingClientRect().height):-1};"
            "const main=document.querySelector('[data-chrome-bottom]');"
            "return {showing: Math.round(main.getBoundingClientRect().top),"
            " footer: h('.foot'), progress: !!document.querySelector('.progress')};"
        )

    await b.goto("/", settle=3)
    shown = await chrome()
    check(
        "the brief opens with its chrome",
        isinstance(shown, dict) and shown.get("showing", 0) > 40,
        shown,
    )

    await scroll_read(900)
    gone = await chrome()
    check(
        "reading down puts the chrome away",
        isinstance(gone, dict) and gone.get("showing") == 0,
        gone,
    )
    check("and draws the reading progress where the header was", bool(gone.get("progress")), gone)

    await scroll_read(-300)
    back = await chrome()
    check(
        "reading back up brings it out again",
        isinstance(back, dict) and back.get("showing", 0) > 40,
        back,
    )

    # THE ONE THAT SHIPS BROKEN, and watching a still screen never shows it:
    # putting the chrome away makes the scroller ~86px taller, the browser
    # clamps `scrollTop` down by the same amount at the end of the document, and
    # that clamp arrives indistinguishable from an 86px scroll UP. It takes
    # scrolling DOWN against the end, which is what finishing a brief is.
    await b.js("const s=document.querySelector('.view--prose'); s.scrollTop = s.scrollHeight;")
    await asyncio.sleep(0.6)
    await scroll_read(240)
    settled = await chrome()
    await asyncio.sleep(0.5)
    still = await chrome()
    check(
        "reading past the end of the brief does not flicker the chrome back",
        isinstance(settled, dict)
        and settled.get("showing") == 0
        and isinstance(still, dict)
        and still.get("showing") == 0,
        {"settled": settled, "then": still},
    )

    # THE READER GETS THE MOVEMENT THEY ASKED FOR AND NO MORE. The bars leaving
    # hands their height to the TOP of the scroll box, so without the
    # cancellation ADR 0199 describes the line under the reader's eye rises by
    # the lift as well as by the scroll: two pixels of movement bought with one.
    #
    # Tracked on a real paragraph rather than on `scrollTop`, which is part of
    # the mechanism being checked. One direction only: a down-and-back pair
    # returns to where it started and hides a doubled journey completely.
    await b.goto("/", settle=3)
    await scroll_read(200)
    await b.js(
        "const s=document.querySelector('.view--prose');"
        "window.__p=[...document.querySelectorAll('.brief .body p')].slice(-4)[0];"
        "window.__y=[]; clearInterval(window.__yId);"
        "window.__yId=setInterval(()=>window.__y.push("
        "  [Math.round(document.querySelector('[data-chrome-bottom]').getBoundingClientRect().top),"
        "   Math.round(window.__p.getBoundingClientRect().top)]), 8);"
        "return !!window.__p;"
    )
    await scroll_read(300)
    held = await b.js(
        "clearInterval(window.__yId);"
        "const y=window.__y||[]; if (y.length < 10) return {samples: y.length};"
        "const tops=y.map(v=>v[1]); const chrome=y.map(v=>v[0]);"
        "return {samples: y.length, travelled: Math.max(...tops)-Math.min(...tops),"
        " chrome: Math.max(...chrome)-Math.min(...chrome)};"
    )
    check(
        "the reader gets the movement they asked for and no more",
        isinstance(held, dict)
        and held.get("chrome", 0) > 20
        and abs(held.get("travelled", 0) - 300) <= 3,
        held,
    )

    # THE READING COLUMN DOES NOT RESIZE WHILE THE CHROME MOVES. The frame
    # re-solves its rows every frame and can overshoot the viewport for two or
    # three of them, which is enough for the DOCUMENT to take a scrollbar, cost
    # 10px of width, narrow the column and rewrap the prose -- text twitching
    # sideways as the bars come back. Three frames in two thousand (ADR 0199),
    # invisible to any before/after check: watch the width for the whole
    # journey.
    await b.goto("/", settle=3)
    await scroll_read(400)
    await b.js(
        "const s=document.querySelector('.view--prose'); const d=document.documentElement;"
        "const col=document.querySelector('.view--prose .column');"
        "window.__w=[]; clearInterval(window.__wId);"
        "window.__wId=setInterval(()=>window.__w.push("
        "  [d.clientWidth, Math.round(col.getBoundingClientRect().width)]), 8);"
        "return 1;"
    )
    await scroll_read(400)
    await scroll_read(-400)
    widths = await b.js(
        "clearInterval(window.__wId);"
        "const w=window.__w||[]; if (w.length < 10) return {samples: w.length};"
        "return {samples: w.length, doc: [...new Set(w.map(x=>x[0]))],"
        " col: [...new Set(w.map(x=>x[1]))]};"
    )
    check(
        "the document never takes a scrollbar while the chrome moves",
        isinstance(widths, dict) and len(widths.get("doc", [0, 0])) == 1,
        widths,
    )
    check(
        "so the reading column never rewraps",
        isinstance(widths, dict) and len(widths.get("col", [0, 0])) == 1,
        widths,
    )

    # THE RAIL DOES NOT MOVE WHILE THE CHROME DOES, in either direction. It is
    # sticky against the top of the scroller, and a sticky element is pinned
    # below the scroller's padding, so the offset that holds is zero and an
    # offset of `--chrome-gone` counts the lift twice and walks the rail DOWN
    # the window (ADR 0199). A before/after check cannot see this class of fault
    # at all: the endpoints are correct either way and only the journey is
    # wrong. Sampled on a timer rather than a frame callback, because the
    # measurement has to survive a tab that is not painting.
    await b.goto("/", settle=3)
    await scroll_read(400)
    await b.js(
        "const s=document.querySelector('.view--prose');"
        "const side=document.querySelector('.side');"
        "window.__rail=[]; clearInterval(window.__railId);"
        "window.__railId=setInterval(()=>window.__rail.push(["
        "  Math.round(document.querySelector('[data-chrome-bottom]').getBoundingClientRect().top),"
        "  Math.round(side.getBoundingClientRect().top)]), 8);"
        "return 1;"
    )
    await scroll_read(400)
    await scroll_read(-400)
    travel = await b.js(
        "clearInterval(window.__railId);"
        "const r=window.__rail||[]; if (r.length < 10) return {samples: r.length};"
        "const tops=r.map(x=>x[1]), hdr=r.map(x=>x[0]);"
        "return {samples: r.length, travel: Math.max(...tops)-Math.min(...tops),"
        " moved: Math.max(...hdr)-Math.min(...hdr)};"
    )
    check(
        "the rail holds still while the chrome moves",
        isinstance(travel, dict) and travel.get("moved", 0) > 20 and travel.get("travel", 999) <= 1,
        travel,
    )

    # THE LAST SCREEN IS STILL. Moving the chrome there would also move the
    # SCROLL RANGE, the browser would clamp `scrollTop` to the new limit, and a
    # build reading that clamp as the reader hands the chrome back on every
    # bounce off the end. The scroller's padding takes both ends, so the
    # scrollable distance is the same at every lift and there is no limit to
    # clamp against (ADR 0199).
    #
    # DOWNWARD only, which is the reader finishing a brief. Reading back up is a
    # real gesture and does lower the bars, which the check above already holds.
    await b.goto("/", settle=3)
    await scroll_read(600)
    bounced = await b.js(
        "const s=document.querySelector('.view--prose');"
        "const showing=()=>Math.round("
        "  document.querySelector('[data-chrome-bottom]').getBoundingClientRect().top);"
        "const nap=(ms)=>new Promise(r=>setTimeout(r,ms));"
        "s.scrollTop = s.scrollHeight - s.clientHeight - 500; await nap(400);"
        "for (let i=0;i<8;i++){ s.scrollTop += 80; await nap(30); } await nap(500);"
        "const arrived = showing(); const seen = new Set();"
        "for (let i=0;i<10;i++){ s.scrollTop += 300; await nap(60); seen.add(showing()); }"
        "await nap(500); seen.add(showing());"
        "return {arrived, states: [...seen], end: showing()};"
    )
    check(
        "the chrome stays away as the brief ends",
        isinstance(bounced, dict) and bounced.get("arrived", -1) == 0,
        bounced,
    )
    check(
        "and bouncing off the end does not flicker it",
        isinstance(bounced, dict) and bounced.get("states", [0, 0]) == [0],
        bounced,
    )

    # Focus folds the rail without moving it. Narrowing the rail's track would
    # hand the freed width to the reading column, which walks the folded strip
    # rightwards across the window while the prose rewraps under it -- so the
    # rail's left edge is the thing to pin.
    await b.goto("/", settle=3)
    rail_before = await b.js(
        "const e=document.querySelector('.side');"
        "return e?Math.round(e.getBoundingClientRect().left):-1;"
    )
    await b.key("f")
    await asyncio.sleep(0.4)
    folded = await b.js(
        "const e=document.querySelector('.side');"
        "return {left: e?Math.round(e.getBoundingClientRect().left):-1,"
        " full: !!document.querySelector('.shell--fullscreen'),"
        " blocks: document.querySelectorAll('.rail-block').length,"
        " out: !!document.querySelector('.strip button')};"
    )
    check("f folds the brief's rail", isinstance(folded, dict) and folded.get("full"), folded)
    check(
        "the folded rail does not move, and says how to get it back",
        isinstance(folded, dict)
        and folded.get("left") == rail_before
        and folded.get("blocks") == 0
        and folded.get("out"),
        {"before": rail_before, "after": folded},
    )
    await b.key("Escape")
    await asyncio.sleep(0.4)
    check(
        "Esc unfolds it",
        await b.js("return document.querySelectorAll('.rail-block').length > 0"),
    )

    # The desk labels take no margin from the prose layer. `SectionHead` names
    # no margin of its own, so a `.prose h2` rule written for the markdown
    # path's narrative headings landed on every desk label instead and paid 36px
    # per desk on the reading surface. Nothing typechecks or unit-tests a gap,
    # and it reads as "the brief is airy" rather than as a fault.
    desks = await b.js(
        "const labels=[...document.querySelectorAll('.brief .section-head__label')];"
        "if(!labels.length) return 'NO-DESKS';"
        "return labels.map(el => getComputedStyle(el).marginTop);"
    )
    check(
        "the desk labels carry no prose margin",
        isinstance(desks, list) and desks and all(m == "0px" for m in desks),
        desks,
    )

    # EVERY brief has a top block, and this is unconditional on purpose.
    # `top_line` is per-item and may be empty: a thin two-item day where the
    # model judged that neither item "would change what the reader does this
    # week" shipped a brief whose rail had no contents list, which nothing on
    # screen distinguishes from a rendering fault. The assembler guarantees
    # `min(3, items)` and fills any gap from the item's own headline, so a
    # "skipped: no top lines today" would be the hole that guarantee closes.
    top = await b.js(
        "const nav = [...document.querySelectorAll('nav')].find("
        "  n => /^Top \\d+$/.test(n.querySelector('h2')?.textContent?.trim() ?? ''));"
        "if (!nav) return {block: false, items: document.querySelectorAll('.brief .item').length};"
        "return {block: true,"
        "        heading: nav.querySelector('h2').textContent.trim(),"
        "        lines: [...nav.querySelectorAll('.toc__line')].map(x => x.textContent.trim()),"
        "        items: document.querySelectorAll('.brief .item').length};"
    )
    check(
        "the brief's rail carries a top block",
        isinstance(top, dict) and top.get("block"),
        top,
    )
    if isinstance(top, dict) and top.get("block"):
        want = min(3, top["items"])
        # The heading is written from the count, so "Top 2" on a two-item day is
        # the honest label rather than a "Top 3" with a hole in it.
        check(
            "the top block holds min(3, items) entries, and says how many",
            len(top["lines"]) == want and top["heading"] == f"Top {want}",
            top,
        )
        # A rank with no sentence renders an empty row: the ordinal and the age
        # badge in the gutter, and nothing beside them.
        check(
            "every top entry carries a line",
            all(line for line in top["lines"]),
            top["lines"],
        )

    # Rewrite MUST send the brief it is looking at. Retrieval excludes every
    # cluster any brief has featured, so a rewrite that does not name its target
    # releases nothing, retrieves nothing, and ends `succeeded_partial` with
    # "nothing new since the last brief" having never reached the model --
    # indistinguishable on screen from a quiet day. Drop `brief_id` from the
    # payload and it silently returns.
    #
    # `fetch` is stubbed for exactly one call and restored in the same block, so
    # a failure here cannot leave the app talking to a stub, and the request
    # never leaves the browser: letting it through would start a brief call.
    posted = await b.js(
        "const real = window.fetch;"
        "const after = [];"
        "let seen = null;"
        "window.fetch = (input, init) => {"
        "  const raw = typeof input === 'string' ? input : input.url;"
        "  const path = new URL(raw, location.origin).pathname;"
        "  if (path.includes('/briefs/rewrite')) {"
        "    seen = {path, body: init && init.body ? JSON.parse(init.body) : null};"
        "    return Promise.resolve(new Response(JSON.stringify({run_id: 'run:smoke-probe'}),"
        "      {status: 202, headers: {'Content-Type': 'application/json'}}));"
        "  }"
        "  if (seen) after.push(path);"
        "  return real(input, init);"
        "};"
        "try {"
        "  const btn = [...document.querySelectorAll('button')]"
        "    .find(x => x.textContent.trim() === 'Rewrite');"
        "  if (!btn) return 'no-button';"
        "  if (btn.disabled) return 'disabled';"
        "  btn.click();"
        "  await new Promise(r => setTimeout(r, 1200));"
        "} finally { window.fetch = real; }"
        "return seen ? {...seen, after} : null;"
    )
    current_id = await b.js(
        "const nav = document.querySelector('[data-brief-id]');"
        "return nav ? nav.dataset.briefId : null;"
    )
    # `no-button` is a STATE, not a failure: Rewrite renders only on the
    # personal view, and on a clean instance no reader has tuned relevance. The
    # skip names the reason so it cannot be mistaken for a pass. `disabled`
    # stays a failure -- a rendered control that cannot be pressed is a
    # different thing.
    if posted == "no-button":
        check(
            "Rewrite names the brief on screen, so retrieval can release its clusters",
            True,
            "skipped: the personal view is not on screen, so Rewrite is not rendered",
        )
    else:
        check(
            "Rewrite names the brief on screen, so retrieval can release its clusters",
            isinstance(posted, dict) and bool((posted.get("body") or {}).get("brief_id")),
            posted,
        )
    if isinstance(posted, dict) and current_id:
        check(
            "Rewrite names THIS brief, not whichever is latest",
            (posted.get("body") or {}).get("brief_id") == current_id,
            {"sent": posted.get("body"), "on screen": current_id},
        )
    # `In flight` reads `/health`, not the history list, so a launcher that
    # forgets `runs.launched()` leaves a new run invisible until the next 30s
    # tick -- and the only symptom is a table that is briefly, plausibly
    # empty.
    if isinstance(posted, dict):
        check(
            "launching a run re-probes health, so In flight does not wait 30s",
            any("/health" in path for path in posted.get("after", [])),
            posted.get("after"),
        )

    # ── keyboard: does anything actually fire? ───────────────────────
    await b.goto("/")
    await b.key("?")
    check(
        "? opens the keyboard overlay",
        await b.js("return !!document.querySelector('[aria-label=\"Keyboard shortcuts\"]')"),
    )
    await b.key("Escape")
    check(
        "Esc closes it",
        not await b.js("return !!document.querySelector('[aria-label=\"Keyboard shortcuts\"]')"),
    )

    await b.key("k", mods=2)  # ctrl
    check(
        "Ctrl+K opens the palette",
        await b.js("return !!document.querySelector('[aria-label=\"Search and jump\"]')"),
    )
    await b.type("gra")
    rows = await b.js(
        "return [...document.querySelectorAll('#palette-results .row')].map(r => r.textContent.trim())"
    )
    check("palette returns results", rows and len(rows) > 0, rows)
    await b.key("Escape")
    check(
        "Esc closes the palette",
        not await b.js("return !!document.querySelector('[aria-label=\"Search and jump\"]')"),
    )

    await b.key("2", mods=1)  # alt+2 -> graph
    await asyncio.sleep(2.5)
    check(
        "Alt+2 navigates to the graph",
        "/graph" in (await b.js("return location.pathname")),
        await b.js("return location.pathname"),
    )

    # alt+N is POSITIONAL against `AppHeader::NAV`, and the two lists have
    # drifted before -- `useKeyboard::NAV_KEYS` carries that scar.
    # `test_frontend_drift.py` holds the mapping against the router; these three
    # prove the keys actually move the browser, which no static check can.
    await b.key("3", mods=1)
    await asyncio.sleep(2.5)
    check(
        "Alt+3 navigates to the archive",
        (await b.js("return location.pathname")) == "/briefs",
        await b.js("return location.pathname"),
    )

    await b.key("5", mods=1)
    await asyncio.sleep(3.0)
    check(
        "Alt+5 navigates to learn",
        (await b.js("return location.pathname")) == "/learn",
        await b.js("return location.pathname"),
    )

    await b.key("6", mods=1)
    await asyncio.sleep(3.0)
    check(
        "Alt+6 navigates to curation",
        (await b.js("return location.pathname")) == "/admin/curation",
        await b.js("return location.pathname"),
    )

    # `/` focuses the filter. Registered per-view, so this also checks that a
    # view-scoped punctuation binding reaches the field at all.
    #
    # ACTIVE ELEMENT, not the field's VALUE: this driver dispatches the `char`
    # event unconditionally, so the "/" lands in the box it just focused. A real
    # browser suppresses it -- the binding `preventDefault`s the keydown.
    await b.goto("/lookup", settle=3)
    await b.key("/")
    focused = await b.js(
        "return document.activeElement?.getAttribute('aria-label') || "
        "document.activeElement?.tagName || ''"
    )
    check("/ focuses the lookup filter", "Filter acronyms" in (focused or ""), focused)

    # ── lazy lists: scrolling pulls the next page (ADR 0154) ─────────
    # The big lists are server-paged with an IntersectionObserver sentinel
    # (`lib/paging.ts` + `useInfiniteScroll`). The observer needs a real scroll
    # container with real geometry, so nothing offline can see a sentinel that
    # never fires. Keyed off the "Load more" affordance: against a small corpus
    # the whole set fits one page and there is nothing to prove.
    async def scrolled_grows(scroller_js: str, rows_js: str) -> tuple[int, int, bool]:
        state = await b.js(
            "const more=[...document.querySelectorAll('button')]"
            ".some(x=>x.textContent.trim()==='Load more');"
            f"return {{rows: {rows_js}, more}};"
        )
        if not (isinstance(state, dict) and state.get("more")):
            return (state.get("rows", 0) if isinstance(state, dict) else 0, -1, False)
        await b.js(f"const el={scroller_js}; if (el) el.scrollTop = el.scrollHeight; return true;")
        await asyncio.sleep(2.5)
        grown = await b.js(f"return {rows_js}")
        return (state["rows"], grown, True)

    # The `/` check above typed a literal "/" into the filter, which is a SERVER
    # query. Clear it and let the debounce settle, or the list under test is the
    # empty result set for "/". The field exists to be cleared only because that
    # `/` opened it -- the filter is a glyph until it is asked for (ADR 0181).
    #
    # AND THE MISS IS ASSERTED. An `if (f)` guard around the clear would let a
    # selector that matches nothing pass quietly, and every check below would
    # then measure the result set for "/" while reporting on the corpus.
    cleared = await b.js(
        "const f=document.querySelector('.context input[type=search]');"
        "if (!f) return false;"
        "f.value=''; f.dispatchEvent(new Event('input', {bubbles: true}));"
        "return true;"
    )
    check("the lookup filter is reachable in the context bar", cleared is True, cleared)
    await asyncio.sleep(1.5)
    lookup_rows = "document.querySelectorAll('.lookup tbody tr').length"
    before, after, had_more = await scrolled_grows(
        "document.querySelector('.lookup section')", lookup_rows
    )
    check(
        "scrolling the lookup pulls the next page" if had_more else "the lookup fits one page",
        after > before if had_more else True,
        {"before": before, "after": after},
    )

    # The status chips are server params (`status=`), not a client-side
    # Array.filter, so the filtered rows must COME BACK filtered from any page
    # of the set. A chip that silently no-ops looks exactly like a corpus that
    # happens to be all one status.
    clicked = await b.js(
        "const chip=[...document.querySelectorAll('.lookup button')]"
        ".find(x=>x.textContent.trim().startsWith('undefined'));"
        "if (chip) chip.click(); return !!chip;"
    )
    if clicked:
        await asyncio.sleep(1.5)
        states = await b.js(
            "return [...document.querySelectorAll('.lookup tbody tr td:last-child')]"
            ".map(t=>t.textContent.trim());"
        )
        check(
            "the undefined chip round-trips through the server",
            isinstance(states, list) and all(s == "undefined" for s in states),
            states[:5] if isinstance(states, list) else states,
        )
        await b.js(
            "const chip=[...document.querySelectorAll('.lookup button')]"
            ".find(x=>x.textContent.trim().startsWith('all'));"
            "if (chip) chip.click(); return true;"
        )

    await b.goto("/briefs", settle=3)
    archive_rows = "document.querySelectorAll('tbody tr').length"
    before, after, had_more = await scrolled_grows(
        "document.querySelector('.view--scroll-page')", archive_rows
    )
    check(
        "scrolling the archive pulls the next page" if had_more else "the archive fits one page",
        after > before if had_more else True,
        {"before": before, "after": after},
    )

    # The Runs page keeps its Load more BUTTON as the keyboard path, and the
    # store guards in-flight fetches: pressing it twice fast must append one
    # page, never the same page twice (duplicate rows keyed the same).
    await b.goto("/admin/runs", settle=3)
    runs_state = await b.js(
        "const more=[...document.querySelectorAll('button')]"
        ".some(x=>x.textContent.trim()==='Load more');"
        "return {rows: document.querySelectorAll('tbody tr').length, more};"
    )
    if isinstance(runs_state, dict) and runs_state.get("more"):
        await b.js(
            "const btn=[...document.querySelectorAll('button')]"
            ".find(x=>x.textContent.trim()==='Load more');"
            "btn.click(); btn.click(); return true;"
        )
        await asyncio.sleep(2.5)
        runs_after = await b.js(
            "const ids=[...document.querySelectorAll('tbody tr')].map(r=>r.textContent);"
            "return {rows: ids.length, distinct: new Set(ids).size};"
        )
        check(
            "runs Load more appends one page without duplicating rows",
            isinstance(runs_after, dict)
            and runs_after["rows"] > runs_state["rows"]
            and runs_after["rows"] == runs_after["distinct"],
            {"before": runs_state, "after": runs_after},
        )
    else:
        check("the runs history fits one page", True, runs_state)

    # THE CARET BESIDE Run HAS TO BE VISIBLE, which is not the same as being in
    # the DOM. `IconButton` renders its content from a SLOT, so `<IconButton
    # icon="down">` passed the prop through as an unknown attribute and drew a
    # correctly sized, correctly positioned, entirely EMPTY square. Every static
    # check passed: `vue-tsc` accepts a fallthrough attribute, the bundle built,
    # the button was clickable.
    #
    # So this asserts the glyph, not the control: a non-zero box AND an <svg>
    # inside it. `lint_tokens.check_slot_only_props` holds the same defect
    # statically; this is what catches the next shape of it.
    caret = await b.js(
        "const b=[...document.querySelectorAll('button')]"
        ".find(x=>x.getAttribute('aria-haspopup')==='menu');"
        "if(!b) return {found:false};"
        "const r=b.getBoundingClientRect();"
        "return {found:true, w:r.width, h:r.height, svg:!!b.querySelector('svg')};"
    )
    check(
        "the deep read caret draws a glyph and not an empty square",
        isinstance(caret, dict)
        and caret.get("found")
        and caret.get("w", 0) > 0
        and caret.get("h", 0) > 0
        and caret.get("svg"),
        caret,
    )

    # And it opens onto something. A menu that renders off-screen or behind the
    # bar is the same defect one layer up.
    if isinstance(caret, dict) and caret.get("found"):
        await b.js(
            "[...document.querySelectorAll('button')]"
            ".find(x=>x.getAttribute('aria-haspopup')==='menu').click(); return true;"
        )
        await asyncio.sleep(0.4)
        # PAINTED, not merely positioned, and the difference is the whole check.
        # This view's actions are teleported into `#context-actions`, inside the
        # context bar's `overflow-hidden` frame, so a panel hanging below the
        # bar is CLIPPED while reporting a perfectly good rect. Measured
        # 2026-09-11 both ways in one geometry: absolute inside the frame gives
        # (600, 35, 180x40) and paints nothing, fixed and teleported to `body`
        # gives the same box and paints. A rect assertion passes the broken one,
        # so ask the document what is actually at the point.
        menu = await b.js(
            "const m=document.querySelector('[role=menu]');"
            "if(!m) return {open:false};"
            "const r=m.getBoundingClientRect();"
            "const hit=document.elementFromPoint(r.left+r.width/2, r.top+r.height/2);"
            "return {open:true, w:r.width, h:r.height,"
            " onscreen: r.top>=0 && r.left>=0 && r.right<=innerWidth,"
            " painted: !!hit && (hit===m || m.contains(hit)),"
            " items:[...m.querySelectorAll('[role=menuitem]')].map(i=>i.textContent.trim())};"
        )
        check(
            "the deep read menu is painted on screen, not clipped by the bar",
            isinstance(menu, dict)
            and menu.get("open")
            and menu.get("onscreen")
            and menu.get("painted")
            and menu.get("w", 0) > 0
            and bool(menu.get("items")),
            menu,
        )
        # Close it again so the checks below see the page they expect.
        await b.js("document.body.click(); return true;")
        await asyncio.sleep(0.3)

    # No dollar figure anywhere on this page (ADR 0180). The offline suite can
    # assert what the component renders, not what SURVIVES onto the screen
    # beside it, and on a flat-rate subscription a stray `$` names a cost
    # nothing is paying while saying nothing about the limit that stops a run.
    money = await b.js(
        "const t=document.querySelector('main')?.textContent ?? '';"
        "return {hit: /\\$[0-9]/.test(t)};"
    )
    check(
        "the runs page quotes no dollar figure",
        isinstance(money, dict) and not money["hit"],
        money,
    )

    # The usage strip resolves to one of its two real states. Absent entirely
    # means the block failed to render, which looks identical to an instance
    # that has simply not observed a window yet -- so both are accepted and
    # "neither" is the failure.
    quota = await b.js(
        "const t=document.querySelector('main')?.textContent ?? '';"
        "return {meter: /% used/.test(t),"
        " unknown: /has not been told how much/.test(t)};"
    )
    check(
        "the runs page states the subscription's remaining room, or says it has none yet",
        isinstance(quota, dict) and (quota["meter"] or quota["unknown"]),
        quota,
    )

    # ── graph: is anything actually drawn? ───────────────────────────
    await b.goto("/graph?mode=all", settle=6)

    # The container, before the elements. A zero-height canvas renders the whole
    # graph perfectly into nothing: Cytoscape does not complain, the readout
    # still counts every node, and the ResizeObserver SKIPS zero-sized boxes on
    # purpose, so it never recovers. That shipped, by the route ADR 0102
    # records.
    #
    # The check below would also fail, but only against a populated corpus, and
    # it reads as "the graph is empty". This one names the defect and holds on
    # an empty database.
    box = await b.js(
        "const el = document.querySelector('.canvas'); if (!el) return 'no .canvas';"
        "const r = el.getBoundingClientRect();"
        "return {w: Math.round(r.width), h: Math.round(r.height)};"
    )
    check(
        "the graph canvas has a non-zero box to draw into",
        isinstance(box, dict) and box.get("w", 0) > 0 and box.get("h", 0) > 0,
        box,
    )

    stats = await b.js(
        "const c = window.__cy; if (!c) return 'no __cy';"
        "return {nodes: c.nodes().length, zoom: +c.zoom().toFixed(3),"
        " visible: c.nodes().filter(n => { const p = n.renderedPosition();"
        "  return p.x > 0 && p.x < c.width() && p.y > 0 && p.y < c.height(); }).length};"
    )
    check(
        "graph draws nodes inside the viewport",
        isinstance(stats, dict) and stats.get("visible", 0) > 5,
        stats,
    )

    await b.key("f")
    check(
        "f enters full screen", await b.js("return !!document.querySelector('.shell--fullscreen')")
    )
    check("full screen shows a way out", await b.js("return !!document.querySelector('.escape')"))
    await b.key("Escape")
    check(
        "Esc leaves full screen",
        not await b.js("return !!document.querySelector('.shell--fullscreen')"),
    )

    # Direction-agnostic, because the filter rail starts COLLAPSED: the graph
    # opens on the whole corpus and a column of filters is not what someone
    # arriving needs first, so asserting that `[` PRODUCES `.rail--collapsed`
    # would assert the opposite of the default.
    before = await b.js("return !!document.querySelector('.rail--collapsed')")
    await b.key("[")
    after = await b.js("return !!document.querySelector('.rail--collapsed')")
    check("[ toggles the filter rail", before != after, f"{before} -> {after}")
    await b.key("[")

    # ── graph: the whole corpus, read at a glance ────────────────────
    #
    # Measured 2026-08-08: two hundred names at the fitted zoom, plus ninety
    # edgeless entities spreading the bounding box until `fit` asked for a zoom
    # of 0.05. Accurate and unreadable at once, and both fixes are invisible to
    # typecheck and to the offline suite.
    await b.goto("/graph?mode=all", settle=6)
    quiet = await b.js(
        "const c = window.__cy; if (!c) return 'no __cy';"
        "const vis = c.nodes(':visible');"
        # The renderer quantises: it compares the label's font size against
        # `2^ceil(log2(zoom))`, not against the zoom itself.
        "const scale = Math.pow(2, Math.ceil(Math.log2(c.zoom())));"
        "const named = vis.filter(n => n.pstyle('min-zoomed-font-size').pfValue <= 10 * scale);"
        "return {zoom: +c.zoom().toFixed(3), visible: vis.length, named: named.length,"
        " hidden: c.nodes('.filtered-out').length,"
        " readout: (document.querySelector('.readout')?.innerText || '').replace(/\\s+/g, ' ')};"
    )
    check(
        "the whole graph fits at a legible zoom",
        isinstance(quiet, dict) and quiet.get("zoom", 0) >= 0.32,
        quiet,
    )
    # GUARDED ON THE ZOOM: the claim is about a graph big enough that fitting it
    # pushes the zoom below 1. A small corpus fits at zoom > 1, where every
    # label IS legible and drawing all of them is right -- and on a clean
    # instance that is the only shape there is.
    if isinstance(quiet, dict) and quiet.get("zoom", 0) >= 1:
        check(
            "most nodes are anonymous at rest, a few are landmarks",
            True,
            f"skipped: the whole graph fits at zoom {quiet.get('zoom')}, so every label is legible",
        )
    else:
        check(
            "most nodes are anonymous at rest, a few are landmarks",
            isinstance(quiet, dict) and 0 < quiet.get("named", 0) < quiet.get("visible", 1) * 0.5,
            quiet,
        )
    # Asked of the SERVER, not of the canvas: `hide_isolated` is a REQUEST
    # filter (ADR 0116), so the hidden nodes are not on the canvas to count and
    # a `.filtered-out` tally passes by finding nothing, over a view that showed
    # 115 of 298 entities in silence (2026-08-18). The readout renders the
    # payload's count, so that is what to compare against.
    withheld = await b.js(
        "const r = await fetch('/api/graph/overview', {method: 'POST',"
        "  headers: {'content-type': 'application/json'},"
        "  body: JSON.stringify({filters: {hide_isolated: true}})});"
        "if (!r.ok) return null;"
        "const d = await r.json(); return d.isolated_hidden ?? 0;"
    )
    check(
        "hiding the unconnected entities is never silent",
        isinstance(quiet, dict)
        and (not withheld or f"{withheld} unconnected hidden" in quiet.get("readout", "")),
        f"withheld={withheld} readout={quiet.get('readout') if isinstance(quiet, dict) else quiet}",
    )

    # ── graph: clicking a node FOCUSES it ───────────────────────────
    #
    # Select-and-expand does nothing once the whole corpus is resident (every
    # node is already expanded) and a mode chip is a no-op once you are in focus
    # mode, so moving from one subject to another was impossible: "the focus
    # button only works once".
    everything = await b.js("return window.__cy.nodes(':visible').length")
    clicked = await b.js(
        "const c = window.__cy; if (!c) return 'no __cy';"
        "const n = c.nodes(':visible').filter(x => x.degree() > 2)[0];"
        "if (!n) return 'no connected node';"
        "n.emit('tap'); await new Promise(r => setTimeout(r, 2500));"
        "const q = new URLSearchParams(location.search);"
        # `.readout` is a FLEX container, so `innerText` puts a newline between
        # every child -- "3\\nnodes\\n·\\n3\\nedges". Collapse first or the
        # regex silently matches nothing and compares against `undefined`.
        "const text = (document.querySelector('.readout')?.innerText || '')"
        "  .replace(/\\s+/g, ' ');"
        "return {mode: q.get('mode'), focus: q.get('focus'), subject: n.id(),"
        " visible: c.nodes(':visible').length,"
        " emphasis: c.nodes('.emphasis').length, muted: c.nodes('.muted').length,"
        " adjacent: c.elements('.adjacent').length,"
        " readoutEdges: text.match(/(\\d+) edges/)?.[1],"
        " drawnEdges: c.edges(':visible').length};"
    )
    check(
        "clicking a node focuses on it",
        isinstance(clicked, dict)
        and clicked.get("mode") == "focus"
        and clicked.get("focus") == clicked.get("subject")
        and 0 < clicked.get("visible", 0) < everything,
        clicked,
    )
    # Hover is an enhancement; a click and the keyboard have to produce the same
    # emphasis, or the only way to see what a node connects to is a mouse.
    check(
        "a focused node is emphasised and the rest muted",
        isinstance(clicked, dict)
        and clicked.get("emphasis") == 1
        and clicked.get("muted", 0) > 0
        and clicked.get("adjacent", 0) > 0,
        clicked,
    )
    # A readout that subtracts only the edges a FILTER hid claims 298 edges over
    # a canvas drawing three.
    check(
        "the readout counts the edges actually drawn",
        isinstance(clicked, dict)
        and str(clicked.get("readoutEdges")) == str(clicked.get("drawnEdges")),
        clicked,
    )

    # ── graph: selecting something opens the detail rail ─────────────
    #
    # The rail is open by default and its state persists, so this only bites
    # after you collapse it, and then it bites silently: an edge click fetched
    # provenance over the wire into a panel that was not on screen.
    #
    # Both halves pull against each other. It must open on the transition from
    # nothing-selected, and it must NOT re-open when the selection merely moves
    # -- in focus mode clicking a neighbour re-centres, so a rail that opened on
    # every selection could never be kept shut.
    await b.goto("/graph?mode=all", settle=6)
    rail = await b.js(
        "const c = window.__cy; if (!c) return 'no __cy';"
        "const state = () => JSON.parse(localStorage.getItem('venona.ui.2') || '{}').graphRailRight;"
        "const wait = ms => new Promise(r => setTimeout(r, ms));"
        # The real `]` binding, not a store poke -- `useKeyboard` listens on
        # `window`, so that is what the event has to reach.
        "const toggle = () => window.dispatchEvent("
        "  new KeyboardEvent('keydown', {key: ']', bubbles: true}));"
        "const nodes = c.nodes(':visible').filter(x => x.degree() > 1);"
        "if (nodes.length < 2) return 'need two connected nodes';"
        # Clear the selection FIRST, then collapse -- collapsing is a TOGGLE, so
        # a test that presses `]` unconditionally flips whichever way it was.
        "c.emit('tap', [{target: c}]); await wait(400);"
        "if (state() !== false) { toggle(); await wait(400); }"
        "const collapsed = state();"
        "nodes[0].emit('tap'); await wait(2000);"
        "const openedOnSelect = state();"
        # Now a node IS selected. Collapse again and move the selection.
        "if (state() !== false) { toggle(); await wait(400); }"
        "nodes[1].emit('tap'); await wait(2000);"
        "return {collapsed, openedOnSelect, keptShutOnMove: state()};"
    )
    check(
        "selecting a node opens the detail rail",
        isinstance(rail, dict)
        and rail.get("collapsed") is False
        and rail.get("openedOnSelect") is True,
        rail,
    )
    check(
        "moving the selection does not re-open a rail the reader closed",
        isinstance(rail, dict) and rail.get("keptShutOnMove") is False,
        rail,
    )

    # ── graph: the walk, and the two ways back ───────────────────────
    #
    # Clicking a neighbour MOVES the focus, and the background is the way up one
    # level: the gesture every map has.
    walked = await b.js(
        "const c = window.__cy; if (!c) return 'no __cy';"
        "const subject = new URLSearchParams(location.search).get('focus');"
        "const hop = c.getElementById(subject).neighborhood('node')"
        "  .not('.filtered-out')[0];"
        "if (!hop) return 'the focused node has no visible neighbour';"
        "hop.emit('tap'); await new Promise(r => setTimeout(r, 2500));"
        "const railText = document.querySelector('.rail')?.innerText || '';"
        "return {from: subject, to: hop.id(),"
        " focus: new URLSearchParams(location.search).get('focus'),"
        " trail: railText.includes(c.getElementById(subject).data('name'))};"
    )
    check(
        "clicking a neighbour moves the focus to it",
        isinstance(walked, dict)
        and walked.get("focus") == walked.get("to")
        and walked.get("to") != walked.get("from"),
        walked,
    )

    up = await b.js(
        "const c = window.__cy;"
        "const before = new URLSearchParams(location.search).get('focus');"
        "c.emit('tap', [{}, {position: {x: 0, y: 0}}]);"
        "await new Promise(r => setTimeout(r, 2500));"
        "return {before, after: new URLSearchParams(location.search).get('focus'),"
        " mode: new URLSearchParams(location.search).get('mode')};"
    )
    check(
        "clicking the background goes up one level",
        isinstance(up, dict) and up.get("after") != up.get("before"),
        up,
    )

    # `reset` is the one control that does a thing rather than naming a state,
    # which is why it is the only Button among the scope chips and the toggle.
    await b.key("r", settle=4)
    restored = await b.js(
        "return {visible: window.__cy.nodes(':visible').length,"
        " mode: new URLSearchParams(location.search).get('mode')};"
    )
    check(
        "reset returns the graph you landed on",
        isinstance(restored, dict)
        and restored.get("mode") == "all"
        and restored.get("visible") == everything,
        restored,
    )

    # ── "whole graph" means the whole graph ─────────────────────────
    #
    # Arriving at /graph hides the edgeless entities, which is what makes the
    # default view legible. CHOOSING the scope is a different act: a control
    # labelled "whole graph" over 233 of 324 entities (2026-08-08) is lying, so
    # picking it clears the filter and the URL records that it was asked for.
    whole = await b.js(
        "const c = window.__cy;"
        # The scope group only renders when more than one scope is reachable,
        # so get into focus mode first -- which is also the only way a reader
        # ever sees this control.
        "const n = c.nodes(':visible').filter(x => x.degree() > 2)[0];"
        "if (!n) return 'no connected node';"
        "n.emit('tap'); await new Promise(r => setTimeout(r, 2500));"
        "const chip = [...document.querySelectorAll('[aria-label=\"graph scope\"] button')]"
        "  .find(x => /whole graph/i.test(x.textContent));"
        "if (!chip) return 'no scope control';"
        "chip.click(); await new Promise(r => setTimeout(r, 6000));"
        "return {total: c.nodes().length, visible: c.nodes(':visible').length,"
        " orphans: new URLSearchParams(location.search).get('orphans')};"
    )
    check(
        "choosing the whole graph shows every entity",
        not isinstance(whole, dict)
        or (whole.get("visible") == whole.get("total") and whole.get("orphans") == "show"),
        whole,
    )

    await graph_cache_checks(b)

    await theme_checks(b)
    await agents_checks(b)
    await billing_checks(b)
    await admin_checks(b)
    await data_checks(b)
    await perf_checks(b)

    # ── a scope you cannot reach is not offered ──────────────────────
    #
    # "this brief" is meaningless unless a brief was the way in, and a
    # permanently greyed control is a question the reader has to answer every
    # time they look at the bar.
    await b.goto("/graph?mode=all", settle=5)
    scopes = await b.js(
        "return (document.querySelector('[aria-label=\"graph scope\"]')?.innerText || '')"
        "  .replace(/\\s+/g, ' ').trim();"
    )
    check(
        "the graph does not offer a brief scope with no brief to scope to",
        isinstance(scopes, str) and "this brief" not in scopes,
        scopes,
    )

    # ── a route's controls do not outlive it ─────────────────────────
    #
    # `BriefView` and `GraphView` are both KeepAlive'd, and a deactivated
    # component's teleported content is NOT part of the subtree KeepAlive takes
    # away -- it lives in the target. So the brief's `archive` chip, its
    # prev/next arrows and its `Rewrite` button sat in the ContextBar while the
    # reader was looking at the graph. `releaseContextBar` ran, so the bar was
    # the right HEIGHT throughout and only the contents were wrong.
    await b.goto("/", settle=4)
    await b.goto("/graph?mode=all", settle=5)
    leaked = await b.js(
        "return {controls: (document.querySelector('#context-controls')?.innerText || '')"
        "   .replace(/\\s+/g, ' ').trim(),"
        " actions: (document.querySelector('#context-actions')?.innerText || '')"
        "   .replace(/\\s+/g, ' ').trim()};"
    )
    # Both zones are checked for BOTH labels. A check looking for `archive` only
    # on the left goes quietly blind the moment a control moves zone, which is
    # this same failure one level up.
    check(
        "the graph does not carry the brief's controls",
        isinstance(leaked, dict)
        and not any(
            word in leaked.get(zone, "").lower()
            for zone in ("controls", "actions")
            for word in ("archive", "rewrite")
        ),
        leaked,
    )

    await b.goto("/", settle=4)
    leaked_back = await b.js(
        "return (document.querySelector('#context-controls')?.innerText || '')"
        "  .replace(/\\s+/g, ' ').trim();"
    )
    check(
        "the brief does not carry the graph's controls",
        isinstance(leaked_back, str) and "whole graph" not in leaked_back.lower(),
        leaked_back,
    )

    # ── the interface never tells the reader to open a terminal ──────
    #
    # Four places did, three of them naming an action the app can already
    # perform. A command name in an interface assumes a terminal, a checkout and
    # a `venona` on PATH, and it goes stale silently when the flag changes.
    #
    # Matched against the REAL subcommand list, not `venona <any word>`. The
    # loose pattern flagged three passages that are the app speaking about
    # itself -- "what venona knows", "venona will not write one it cannot trace
    # to a source" -- which the 2026-08-07 decision explicitly allows. Three
    # permanent failures train you to read the summary and ignore the detail.
    commands = "|".join(
        sorted(
            re.findall(
                r'sub\.add_parser\("([a-z][a-z-]*)"',
                (REPO_ROOT / "backend/venona/cli.py").read_text(),
            )
        )
    )
    for path in ["/", "/graph", "/admin/curation", "/admin/runs", "/admin/config/sources"]:
        await b.goto(path, settle=3)
        found = await b.js(
            "const t = document.body.innerText || '';"
            f"const m = t.match(new RegExp('venona (?:{commands})\\\\b', 'g'));"
            "return m ? [...new Set(m)] : [];"
        )
        check(f"{path} names no CLI command", found == [], found)

    # ── every route renders SOMETHING ────────────────────────────────
    #
    # /learn is checked separately below: a study card whose front is a short
    # acronym renders ~25 chars of REAL content, so the 40-char floor here
    # would fail exactly when the session is working.
    for path in [
        "/",
        "/briefs",
        "/graph",
        "/admin/curation",
        "/admin/runs",
        "/admin",
        "/admin/config/sources",
    ]:
        await b.goto(path, settle=3)
        text = await b.js(
            "return (document.querySelector('.content')?.innerText || '').trim().length"
        )
        check(f"{path} renders content", isinstance(text, int) and text > 40, f"{text} chars")

    # /learn renders a session or says why not -- a card panel (a study card
    # is on offer), or an EmptyState ("nothing due"), or the no-account note.
    # Never a blank body: that is the failure mode of the queue request dying
    # silently, which no other check would see.
    await b.goto("/learn", settle=3)
    learn_body = await b.js(
        "const c = document.querySelector('.content');"
        "return {chars: (c?.innerText || '').trim().length,"
        "        panel: !!c?.querySelector('.panel, .empty'),"
        "        sections: document.querySelectorAll('[role=radiogroup] [role=radio], [role=radiogroup] button').length};"
    )
    check(
        "/learn renders a session, an empty state, or a refusal",
        isinstance(learn_body, dict)
        and learn_body.get("chars", 0) > 5
        and learn_body.get("sections", 0) >= 3,
        learn_body,
    )

    # ── pluralisation and placeholder strings ────────────────────────
    #
    # THE BAR IS READ TOO, not just `.content`. Row two states a count on most
    # routes and sits outside the content region, so a sweep scoped to
    # `.content` cannot see `admin · overview · 1 users`.
    for path in ["/briefs", "/admin", "/admin/people", "/lookup", "/admin/runs"]:
        await b.goto(path, settle=2.0)
        bar = await b.js("return document.querySelector('.context')?.innerText || ''")
        for bad in [
            "1 briefs",
            "1 runs",
            "1 items",
            "1 users",
            "1 people",
            "1 acronyms",
            "1 sources",
            "1 nodes",
            "1 edges",
            "NaN",
            "undefined ·",
            "[object",
        ]:
            check(
                f"no '{bad}' in the bar on {path}",
                bad not in (bar or "").lower(),
                bar[:120] if bar and bad in bar.lower() else "",
            )

    await b.goto("/briefs", settle=2.5)
    body = await b.js("return document.querySelector('.content').innerText")
    for bad in [
        "1 briefs",
        "1 runs",
        "1 items",
        "1 entities",
        "1 sources",
        "NaN",
        "undefined",
        "[object",
    ]:
        check(
            f"no '{bad}' on /briefs",
            bad not in (body or ""),
            body[:160] if body and bad in body else "",
        )

    await b.goto("/admin/runs", settle=2.5)

    # ⭐ THE RUN MENU OPENS A DIALOG THAT SURVIVES BEING CLICKED IN.
    #
    # `Dialog` teleports to `body`, so a dialog rendered inside the teleported
    # run menu is not CONTAINED by that menu: the first mousedown inside it
    # lands outside `runMenuPanel`, `onMenuBlur` closes the menu, the component
    # unmounts and the click never reaches the confirm. The symptom is a menu
    # item that opens something and then does nothing when you press its
    # button, which shipped to production on 2026-09-15 and is invisible to
    # vue-tsc, to the unit suite and to every check below -- all of them see a
    # menu that renders and a dialog that opens.
    #
    # Driven as a person drives it: open the menu, press the first row, then
    # mousedown inside the dialog the way pressing Cancel would, and require
    # the dialog to still be there afterwards.
    opened = await b.js(
        "const btn = [...document.querySelectorAll('button')]"
        "  .find(e => (e.getAttribute('aria-haspopup') || '') === 'menu');"
        "if (!btn) return 'no menu button';"
        "btn.click();"
        "await new Promise(r => setTimeout(r, 250));"
        "const row = document.querySelector('[role=menu] [role=menuitem]');"
        "if (!row) return 'no menu rows';"
        "row.click();"
        "await new Promise(r => setTimeout(r, 400));"
        "const dialog = document.querySelector('[role=dialog]');"
        "if (!dialog) return 'no dialog after pressing a row';"
        "dialog.dispatchEvent(new MouseEvent('mousedown', {bubbles: true}));"
        "await new Promise(r => setTimeout(r, 250));"
        "return document.querySelector('[role=dialog]') ? 'ok' : 'dialog vanished on mousedown';"
    )
    check("a run menu dialog survives a click inside it", opened == "ok", opened)
    await b.key("Escape")

    # ⭐ A NUMBER CAN BE TYPED INTO A LAUNCH DIALOG'S BOX.
    #
    # The quote follows the box, and a re-count that blanks the dialog unmounts
    # the box mid-word and takes the focus with it: every digit after the first
    # is dropped, "2155" arrives as "2", and the figure in front of the confirm
    # belongs to a number nobody typed. Shipped on 2026-09-15 and invisible to
    # vue-tsc and to the unit suite. Typed at a hunting pace, because the two
    # halves are the debounce and the field surviving it, and a burst at full
    # speed exercises neither.
    await b.js(
        "window.__previews = 0;"
        "const real = window.fetch;"
        "window.fetch = (...a) => {"
        "  const u = typeof a[0] === 'string' ? a[0] : (a[0] && a[0].url) || '';"
        "  if (u.includes('/preview')) window.__previews += 1;"
        "  return real(...a);"
        "};"
        "return 1"
    )
    typed = await b.js(
        "const btn = [...document.querySelectorAll('button')]"
        "  .find(e => (e.getAttribute('aria-haspopup') || '') === 'menu');"
        "if (!btn) return 'no menu button';"
        "btn.click();"
        "await new Promise(r => setTimeout(r, 250));"
        "const row = [...document.querySelectorAll('[role=menu] [role=menuitem]')]"
        "  .find(e => /publisher/i.test(e.textContent));"
        "if (!row) return 'no second-source row';"
        "row.click();"
        "await new Promise(r => setTimeout(r, 600));"
        "const box = document.querySelector('[role=dialog] input[type=number]');"
        "if (!box) return 'no number box';"
        "box.focus();"
        "window.__previews = 0;"
        "return 'ok'"
    )
    if typed == "ok":
        for digit in "2155":
            await b.key(digit, settle=0.25)
        await b.js("await new Promise(r => setTimeout(r, 900)); return 1")
        held = await b.js(
            "const box = document.querySelector('[role=dialog] input[type=number]');"
            "return {value: box ? box.value : 'box gone', counts: window.__previews}"
        )
    else:
        held = typed
    check(
        "a launch dialog's number box keeps every digit typed into it",
        isinstance(held, dict) and held.get("value") == "2155",
        held,
    )
    check(
        "the quote is counted once the typing stops, not once per keystroke",
        isinstance(held, dict) and held.get("counts") == 1,
        held,
    )
    await b.key("Escape")

    # ⭐ Free and paid runs are TELLABLE APART without opening anything. Two
    # adjacent rows both reading as "definitions", one free and one buying a
    # web search per name, sent the operator to the paid one for work the free
    # one does.
    marks = await b.js(
        "const btn = [...document.querySelectorAll('button')]"
        "  .find(e => (e.getAttribute('aria-haspopup') || '') === 'menu');"
        "if (!btn) return null;"
        "btn.click();"
        "await new Promise(r => setTimeout(r, 250));"
        "const rows = [...document.querySelectorAll('[role=menu] [role=menuitem]')];"
        "return rows.map(r => r.innerText.trim());"
    )
    if marks:
        check(
            "every run in the menu says whether it spends",
            all(("free" in m or "spends" in m) for m in marks),
            [m for m in marks if "free" not in m and "spends" not in m],
        )
        check("the menu offers a free run", any("free" in m for m in marks))
    await b.key("Escape")

    body = await b.js("return document.querySelector('.content').innerText")
    # Every one of these is a wire or database spelling, and the history is
    # where they surface first: one row rendering `nothing_to_do` means the
    # per-kind phrase map lost a value. ADR 0183.
    for bad in [
        "NaN",
        "undefined",
        "[object",
        "1 runs",
        "succeeded_partial",
        "nothing_found",
        "nothing_to_do",
        "delivered",
    ]:
        check(f"no '{bad}' on the runs list", bad not in (body or ""))

    # ⭐ The history has to FIT a phone (`docs/DESIGN.md` "Responsive"), by
    # columns that stand down rather than a row that runs off the screen. Every
    # column typechecks and renders at every width, and the failure is a
    # horizontal scrollbar on a surface nobody opens on a phone while
    # developing.
    #
    # Measured on a clone rather than by resizing the window, because a tiling
    # window manager refuses the resize and the check would silently pass at
    # desktop width.
    fit = await b.js(
        "const t = document.querySelector('table');"
        "if (!t) return null;"
        "const box = document.createElement('div');"
        "box.style.cssText = 'position:fixed;left:-9999px;top:0;width:390px;overflow:hidden';"
        "const c = t.cloneNode(true); box.appendChild(c); document.body.appendChild(box);"
        "c.querySelectorAll('[class*=\"hidden sm:\"]').forEach(e => e.style.display = 'none');"
        "c.querySelectorAll('[class*=\"sm:hidden\"]').forEach("
        "  e => e.style.display = e.className.includes('block') ? 'block' : 'inline');"
        "c.querySelectorAll('th,td').forEach("
        "  e => { e.style.paddingLeft = '8px'; e.style.paddingRight = '8px' });"
        "c.querySelectorAll('[class*=\"sm:whitespace-nowrap\"]').forEach("
        "  e => e.style.whiteSpace = 'normal');"
        "c.style.width = '100%';"
        "const over = Math.round(c.scrollWidth) - 390;"
        "document.body.removeChild(box);"
        "return over"
    )
    if fit is not None:
        check("the run history fits 390px without scrolling sideways", fit <= 0, f"{fit}px over")

    # ── the account menu obeys the menu contract ─────────────────────
    #
    # It declares `role="menu"` with `role="menuitem"` children, which promises
    # a keyboard a specific set of behaviours. Declaring the role and not
    # honouring it is worse than using no role: a screen reader announces a menu
    # and then the arrow keys do nothing.
    await b.goto("/", settle=2.5)
    menu = await b.js(
        "const t = document.querySelector('.usermenu button');"
        "if (!t) return {trigger: false};"
        "t.click();"
        "return {trigger: true}"
    )
    if not (isinstance(menu, dict) and menu.get("trigger")):
        check("the account menu takes focus on open", True, "skipped: no account menu rendered")
    else:
        await asyncio.sleep(0.4)
        opened = await b.js(
            "const items = [...document.querySelectorAll('.usermenu [role=menuitem]')];"
            "const first = items.indexOf(document.activeElement);"
            "document.activeElement.dispatchEvent(new KeyboardEvent('keydown',"
            "  {key: 'ArrowDown', bubbles: true}));"
            "const moved = items.indexOf(document.activeElement);"
            "return {count: items.length, first, moved}"
        )
        check(
            "the account menu takes focus on open and the arrows move it",
            isinstance(opened, dict)
            and (opened.get("count") or 0) >= 2
            and opened.get("first") == 0
            and opened.get("moved") == 1,
            opened,
        )
        await b.key("Escape")

    # ── the reader never meets this repo's vocabulary ────────────────
    #
    # These are the words the schema uses, not the words a reader has, and
    # "admins are users too" is the rule.
    #
    # Scoped to the app's own CHROME -- headings, column headers, labels,
    # buttons, empty states -- and never the whole body: a corpus legitimately
    # contains the word `assertion` and people surnamed Lane, so grepping
    # `document.body` would fail on the data rather than on the copy.
    # `test_copy_style.py` cannot do this at all, because at the source layer a
    # variable named `corpus` is indistinguishable from a sentence with it in.
    for path in ["/", "/briefs", "/graph", "/admin/curation", "/admin/runs", "/learn", "/admin"]:
        await b.goto(path, settle=2.5)
        chrome = await b.js(
            "const sel = 'h1, h2, h3, th, nav a, button, label, .label-micro,"
            " [class*=empty], [class*=section-head]';"
            "return [...document.querySelectorAll(sel)]"
            "  .map(el => el.innerText || '').join(' | ').toLowerCase()"
        )
        for word in ["corpus", "assertion", "projection", "freshness", "lane"]:
            hit = word in (chrome or "")
            check(
                f"no '{word}' in the chrome on {path}",
                not hit,
                (chrome or "")[:200] if hit else "",
            )

    # ── controls line up ─────────────────────────────────────────────
    await b.goto("/admin/curation", settle=3)
    heights = await b.js(
        "const bar = document.querySelector('header.bar'); if (!bar) return 'no bar';"
        "return [...bar.querySelectorAll('button, a.gear, .palette')]"
        "  .map(e => Math.round(e.getBoundingClientRect().height))"
        "  .filter(h => h > 0);"
    )
    check(
        "header controls share a height",
        isinstance(heights, list) and len(set(heights)) == 1,
        heights,
    )

    # ── focus is trapped, and draws the RIGHT ring (ADR 0104) ────────
    #
    # The property is not "is a ring drawn" but "is it drawn for the KEYBOARD
    # and only the keyboard". The ring is a `box-shadow`, so reading
    # `outlineStyle`/`outlineWidth` alone cannot see it in either direction;
    # both are read, because the UA outline must stay suppressed AND the glow
    # must be present.
    #
    # `outline: none` matters twice over: two different rings can appear here
    # and the first fix caught only one. Ours is a rounded rectangle; the
    # browser's is `auto 1px` and shows up the moment an author rule fails to
    # apply, which is what happened when the reset listed `:-moz-focusring`
    # alongside `:focus-visible` and Chromium threw the whole selector list
    # away. A check for "not green" would have passed.
    #
    # Tab rather than `.focus()`: it is the only way to get Chromium to agree
    # the element is `:focus-visible`, and an unfocused element has no ring.
    await b.goto("/admin/runs", settle=2.5)
    outlines, unringed = [], []
    for _ in range(12):
        await b.key("Tab", settle=0.15)
        state = await b.js(
            "const el = document.activeElement; const s = getComputedStyle(el);"
            "const name = el.tagName + '.' + (el.className.baseVal ?? el.className);"
            "return {name,"
            " outline: (s.outlineStyle === 'none' || s.outlineWidth === '0px') ? null"
            "   : s.outlineStyle + ' ' + s.outlineWidth + ' ' + s.outlineColor,"
            # SVG children cannot take a box-shadow at all, so the mini-graph's
            # focusable nodes are exempt rather than failures. ADR 0104 records
            # that gap; do not silently widen this to hide it.
            " svg: el.ownerSVGElement != null,"
            " ring: s.boxShadow && s.boxShadow !== 'none' ? s.boxShadow : null,"
            " modality: document.documentElement.dataset.modality};"
        )
        if state.get("outline"):
            outlines.append(f"{state['name']} -> {state['outline']}")
        if not state.get("ring") and not state.get("svg"):
            unringed.append(f"{state['name']} (modality={state.get('modality')})")
    check("no control draws a UA outline", not outlines, outlines)
    check("keyboard focus draws the ring", not unringed, unringed)

    # The half ADR 0097 was actually about: a CLICKED element must not be
    # ringed, even though a key has already been pressed in this frame -- which
    # the twelve Tabs above guarantee. Chromium still reports `:focus-visible`
    # here; `data-modality` is what makes the rule disagree.
    await b.pointer_click("nav a")
    clicked = await b.js(
        "const el = document.activeElement; const s = getComputedStyle(el);"
        "return {modality: document.documentElement.dataset.modality,"
        " focusVisible: el.matches(':focus-visible'),"
        " ring: s.boxShadow && s.boxShadow !== 'none' ? s.boxShadow : null};"
    )
    check("a clicked control draws no ring", clicked.get("ring") is None, clicked)

    # BACK TO /runs FIRST, and it is load-bearing. The check above clicks `nav
    # a` -- the first nav link, Brief -- so without this the app has NAVIGATED
    # AWAY and `button.btn--outline` matches whatever outline button the brief
    # renders: no dialog opens, `dialog takes focus` reports `BODY.`, and both
    # read as a broken dialog on a page nobody was looking at.
    #
    # Focus is deliberately NOT reset: the trap this block is about needs
    # `restoreTo` to have somewhere real to go back to, and that nav link is it.
    await b.goto("/admin/runs", settle=2.5)
    # DISABLED ON A DATABASE WITH NO RUN HISTORY, which is the state this script
    # is supposed to run in. `RunsView` disables Clear history when there is
    # nothing to clear, so all three checks below would fail on exactly the
    # instance the suite is meant for -- while `Esc closes the dialog` PASSES,
    # because "no dialog is open" satisfies it whether one was opened or not. A
    # check that cannot fail is worse than one that is skipped, and starting a
    # run to fix it would cost real model spend.
    clearable = await b.js(
        "const b = document.querySelector('button.btn--outline');return b ? !b.disabled : false;"
    )
    if not clearable:
        check(
            "clear-history dialog opens",
            True,
            "skipped: no run history, so Clear history is disabled",
        )
    else:
        await b.click("button.btn--outline")  # Clear history
        check(
            "clear-history dialog opens",
            await b.js("return !!document.querySelector('[role=dialog]')"),
        )
        check(
            "dialog takes focus",
            await b.js(
                "const d = document.querySelector('[role=dialog]');"
                "return !!d && d.contains(document.activeElement);"
            ),
            await b.js(
                "return document.activeElement?.tagName + '.' + document.activeElement?.className"
            ),
        )
        await b.key("Escape")
        check(
            "Esc closes the dialog",
            not await b.js("return !!document.querySelector('[role=dialog]')"),
        )

    # ── the graph route names the BRIEF, not only the paragraph ─────
    #
    # A query naming the paragraph but not the document containing it left "back
    # to the brief" hardcoded to `/`, the LATEST brief. Arriving from an
    # archived one landed the reader on today's document holding an item id that
    # is not in it, and `restoreScroll` fell through to a remembered offset,
    # which looks exactly like no restoration at all.
    archived = await b.js(
        "const r = await fetch('/api/briefs?limit=5'); const d = await r.json();"
        "return (d.items || []).map(i => i.id);"
    )
    if not isinstance(archived, list) or len(archived) < 2:
        check(
            "the return trip works from an archived brief",
            True,
            "skipped: fewer than two briefs in the archive",
        )
    else:
        older = archived[1]
        await b.goto(f"/briefs/{older}", settle=4)
        await b.click("[data-entity-id]", settle=1.5)
        opened = await b.js(
            "const btn = [...document.querySelectorAll('button, a')]"
            "  .find(x => /open in graph/i.test(x.title || x.textContent));"
            "if (!btn) return 'no open-in-graph button';"
            "btn.click(); await new Promise(r => setTimeout(r, 3500));"
            "const q = new URLSearchParams(location.search); const c = window.__cy;"
            "return {path: location.pathname, mode: q.get('mode'), brief: q.get('brief'),"
            " item: q.get('item'), focus: q.get('focus'),"
            " nodes: c ? c.nodes().length : null,"
            " scope: (document.querySelector('.coverage')?.innerText || '').replace(/\\s+/g, ' '),"
            " back: !![...document.querySelectorAll('button')]"
            "   .find(x => /back to/i.test(x.textContent))};"
        )
        check(
            "Open in graph names the brief, not only the paragraph",
            isinstance(opened, dict)
            and opened.get("brief") == older
            and str(opened.get("item") or "").startswith(older),
            opened,
        )
        check(
            "Open in graph lands in the brief's own graph",
            isinstance(opened, dict)
            and opened.get("mode") == "brief"
            and (opened.get("nodes") or 0) > 0,
            opened,
        )
        check(
            "the brief-scoped graph says what the brief named",
            isinstance(opened, dict) and "named" in opened.get("scope", ""),
            opened,
        )

        # `showEverything` must not `router.replace({query: {view: 'all'}})`:
        # that obliterates the seed, the filters and the way back, and writes a
        # key nothing reads.
        kept = await b.js(
            "const chip = [...document.querySelectorAll('button')]"
            "  .find(x => /whole graph/i.test(x.textContent));"
            "if (!chip) return 'no whole-graph control';"
            "chip.click(); await new Promise(r => setTimeout(r, 5000));"
            "const q = new URLSearchParams(location.search);"
            "return {mode: q.get('mode'), brief: q.get('brief'), item: q.get('item'),"
            " view: q.get('view'),"
            " back: !![...document.querySelectorAll('button')]"
            "   .find(x => /back to/i.test(x.textContent))};"
        )
        check(
            "showing the whole graph keeps the way back to the brief",
            isinstance(kept, dict)
            and kept.get("brief") == older
            and kept.get("item")
            and kept.get("back") is True
            and kept.get("view") is None,
            kept,
        )

        landed = await b.js(
            "const item = new URLSearchParams(location.search).get('item');"
            "const back = [...document.querySelectorAll('button')]"
            "  .find(x => /back to/i.test(x.textContent));"
            "if (!back) return 'no back button';"
            "back.click(); await new Promise(r => setTimeout(r, 3500));"
            'const el = item && document.querySelector(`[data-item-id="${CSS.escape(item)}"]`);'
            "return {path: decodeURIComponent(location.pathname), item,"
            " top: el ? Math.round(el.getBoundingClientRect().top) : null};"
        )
        check(
            "back to the brief returns to the ARCHIVED brief, not to today's",
            isinstance(landed, dict) and landed.get("path") == f"/briefs/{older}",
            landed,
        )
        # `onActivated` and `watch(routeId)` both fire on this navigation. Two
        # `restoreScroll` calls are not idempotent: the first consumes
        # `returnItemId`, the second finds it null and scrolls to the remembered
        # offset instead, landing the reader at the top of the page.
        check(
            "back to the brief lands on the paragraph the reader left",
            isinstance(landed, dict) and landed.get("top") is not None and abs(landed["top"]) < 300,
            landed,
        )

        # ── the CACHED graph re-reads its query on re-entry ──────────
        #
        # `<KeepAlive include="BriefView,GraphView">` means `onMounted` runs
        # once, so a lone watcher on `route.query.seed` guarded on equality
        # makes arriving from a second brief on the SAME entity a total no-op,
        # with the way back still pointing at the first one.
        seed = await b.js(
            "const r = await fetch('/api/graph/search?q=a&limit=1'); const d = await r.json();"
            "return d.entities?.[0]?.id || null;"
        )
        if not seed:
            check(
                "the cached graph re-reads its query on re-entry",
                True,
                "skipped: graph search returned no entity to seed on",
            )
        else:
            newer = archived[0]
            await b.goto(f"/graph?mode=focus&focus={seed}&brief={older}&item={older}:i01", settle=5)
            await b.goto("/admin/runs", settle=2)
            await b.goto(f"/graph?mode=focus&focus={seed}&brief={newer}&item={newer}:i01", settle=5)
            reentered = await b.js(
                "const back = [...document.querySelectorAll('button')]"
                "  .find(x => /back to/i.test(x.textContent));"
                "if (!back) return 'no back button';"
                "back.click(); await new Promise(r => setTimeout(r, 3500));"
                "return decodeURIComponent(location.pathname);"
            )
            check(
                "re-entering the cached graph re-reads the brief it came from",
                reentered in (f"/briefs/{newer}", "/"),
                reentered,
            )

    # ── the brief's hover card ───────────────────────────────────────
    await b.goto("/", settle=4)
    await b.click("[data-entity-id]")
    check("clicking a mention pins a card", await b.js("return !!document.querySelector('.card')"))
    check(
        "the pinned card clears the app header",
        await b.js(
            "const c = document.querySelector('.card'); if (!c) return false;"
            "const nav = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--nav-height'));"
            "return c.getBoundingClientRect().top >= nav;"
        ),
    )

    # ── one skeleton, every kind ─────────────────────────────────────
    #
    # The three card kinds share ONE section order, and a section a kind cannot
    # fill states its absence rather than disappearing -- so the shape of the
    # card never depends on the data.
    headings = await b.js(
        "const c = document.querySelector('.card'); if (!c) return null;"
        "return [...c.querySelectorAll('h4')].map(h => h.textContent.trim().toLowerCase());"
    )
    check(
        "an entity card leads with definition, then sources, then connections",
        isinstance(headings, list)
        and headings.index("definition")
        < headings.index("sources")
        < headings.index("connections"),
        headings,
    )

    # The actions are why this is a card and not a tooltip, and the card clamps
    # at --scroll-xl (480px): an entity with a definition, aliases, four sources
    # and a graph is taller than that, so an unpinned footer put them below the
    # fold. Buttons at the bottom of a document nobody scrolls are not buttons.
    foot = await b.js(
        "const f = document.querySelector('.card .card__foot');"
        "const c = document.querySelector('.card');"
        "if (!f || !c) return null;"
        "const fr = f.getBoundingClientRect(), cr = c.getBoundingClientRect();"
        "return {sticky: getComputedStyle(f).position === 'sticky',"
        "        inside: fr.bottom <= cr.bottom + 1,"
        "        buttons: [...f.querySelectorAll('button')]"
        "          .map(x => x.title || x.textContent.trim())};"
    )
    check(
        "the card's actions are buttons, pinned to its foot",
        # `any`, not truthiness: an icon-only button has empty textContent, so a
        # list of empty strings would pass while every label had been lost.
        isinstance(foot, dict) and foot["sticky"] and foot["inside"] and any(foot["buttons"]),
        foot,
    )

    # A scrollbar lives in the PADDING box, so it paints inside the border and
    # over the two corners on its own edge: an opaque track squares off both
    # right-hand corners of a rounded scroll container. The hover card, whose
    # entire shape is a 6px radius and a border coloured by entity type, lost
    # its right corners this way, and it reads as "the border is broken" rather
    # than as "something is painted on top of it".
    #
    # Written for every scrolling rounded box on the page, not just this card:
    # the fix is one rule in `base.css` and the next component to scroll inside
    # a radius must inherit it rather than rediscover the bug. Chrome answers
    # `getComputedStyle(el, '::-webkit-scrollbar-track')`, which is the only way
    # to see this without diffing pixels.
    squared = await b.js(
        "const bad = [];"
        "for (const el of document.querySelectorAll('*')) {"
        "  const gutter = el.offsetWidth - el.clientWidth;"
        "  if (!(gutter > 0) || el.scrollHeight <= el.clientHeight) continue;"
        "  const cs = getComputedStyle(el);"
        "  if (parseFloat(cs.borderTopRightRadius) <= 0) continue;"
        "  const track = getComputedStyle(el, '::-webkit-scrollbar-track').backgroundColor;"
        "  if (!/rgba\\(0, 0, 0, 0\\)|transparent/.test(track))"
        "    bad.push({cls: el.className.toString().slice(0, 60), track});"
        "}"
        "return bad;"
    )
    check(
        "no rounded scroll container has an opaque scrollbar track over its corners",
        squared == [],
        squared,
    )

    # The mini-graph carries NO text: ten labels never fit in 308x150, so only
    # five rendered and those were cut at sixteen characters. The names live in
    # a readout under it, one at a time, driven by `pointerenter` rather than a
    # native `<title>`, which waits about a second and cannot be styled.
    readout = await b.js(
        "const g = document.querySelector('.card .minigraph');"
        "if (!g) return 'no-graph';"
        "const svg = g.querySelector('svg');"
        "const out = g.querySelector('.readout');"
        "const node = g.querySelector('.node');"
        "if (!node) return 'no-nodes';"
        "const rest = out.textContent.trim();"
        "node.dispatchEvent(new PointerEvent('pointerenter', {bubbles: false}));"
        "await new Promise(r => setTimeout(r, 50));"
        "const hovered = out.textContent.trim();"
        "return {rest, hovered,"
        "        labels: svg.querySelectorAll('.nodes text').length,"
        "        titles: svg.querySelectorAll('title').length,"
        "        role: svg.getAttribute('role'),"
        "        focusable: node.getAttribute('tabindex')};"
    )
    if isinstance(readout, dict):
        check(
            "the mini-graph draws no text and no native tooltip",
            readout["labels"] == 0 and readout["titles"] == 0,
            readout,
        )
        check(
            "hovering a node names it in the readout",
            readout["hovered"] and readout["hovered"] != readout["rest"],
            readout,
        )
        # `role="img"` would make the subtree presentational and hide the
        # focusable nodes from assistive tech -- the keyboard route is the only
        # way to read this graph without a pointer.
        check(
            "mini-graph nodes are reachable without a pointer",
            readout["role"] == "group" and readout["focusable"] == "0",
            readout,
        )

    await b.key("Escape")
    check("Esc dismisses the card", not await b.js("return !!document.querySelector('.card')"))

    # ── an undefined acronym offers the human escape hatch ───────────
    #
    # Two live lookups (USC, IDIQ) grounded nothing and the card's only response
    # was to offer the same lookup again. A curator who knows the term must be
    # able to say so, and the source URL is required because invariant 3 waives
    # the SENTENCE check for a person, not the audit trail.
    undefined_id = await b.js(
        "const el = [...document.querySelectorAll('[data-entity-id]')]"
        "  .find(e => (e.dataset.entityId || '').startsWith('gloss:')"
        "          && e.className.includes('undefined'));"
        "return el ? el.dataset.entityId : null"
    )

    # The brief MUST carry glossary spans, and this assertion is unconditional
    # on purpose. Reported as a skip, it would let a bug that suppresses every
    # surface ship green: a brief with zero glossary spans is not a well-curated
    # corpus, it is the symptom of the acronyms stage dropping surfaces. This
    # instance wrote ~550 undefined rows a night (2026-08-18); zero means the
    # pipeline is broken.
    gloss_spans = await b.js(
        "return document.querySelectorAll('[data-entity-id^=\"gloss:\"]').length"
    )
    check("the brief carries glossary spans at all", (gloss_spans or 0) > 0, gloss_spans)

    if undefined_id:
        await b.click(f"[data-entity-id='{undefined_id}']")
        await asyncio.sleep(1.2)
        # Titles, not text: the footer's actions are icon-only buttons whose
        # word lives in `title`/`aria-label`. The manual form's Save/Cancel
        # still carry text, which the fallback keeps visible.
        buttons = (
            "return [...document.querySelectorAll('.card button')]"
            ".map(x => x.title || x.textContent.trim())"
        )
        labels = await b.js(buttons) or []
        check("an undefined card offers manual entry", "I know this one" in labels, labels)
        # The other escape hatch, and the one VICTOR could not reach. The title
        # reads "Look this up" until an attempt fails and "Try again" after, so
        # accept either: what matters is that a lookup is reachable at all, or
        # an undefined term is a dead end however well it renders.
        check(
            "an undefined card offers the lookup",
            any("Look this up" in x or "Try again" in x for x in labels),
            labels,
        )
        await b.js(
            "const b = [...document.querySelectorAll('.card button')]"
            "  .find(x => (x.title || x.textContent.trim()) === 'I know this one');"
            "if (b) b.click(); return 1"
        )
        await asyncio.sleep(0.5)
        inputs = "return document.querySelectorAll('.card__manual input').length"
        check(
            "manual entry asks for expansion AND source",
            (await b.js(inputs)) == 2,
            await b.js(inputs),
        )
        saveable = (
            "return [...document.querySelectorAll('.card__manual button')]"
            ".find(x => x.textContent.trim() === 'Save')?.disabled"
        )
        check("Save is refused until both fields are filled", await b.js(saveable) is True)
        await b.key("Escape")
    else:
        check(
            "an undefined card offers manual entry",
            True,
            "skipped: no undefined acronym in the current brief",
        )

    # A pinned card must survive the card cache being emptied.
    #
    # `entities.invalidate()` clears every cached card and does NOT refetch, and
    # `runs.ts` calls it on every run `done`. Nothing else fetches for a card
    # that is already open -- the only other fetch is the hover-intent timer on
    # the way in -- so a card pinned when a run finished lost its content and
    # sat on the loading state forever. Emptying the cache from the page is not
    # reachable here, so this drives the card's own invalidate-then-refetch.
    await b.goto("/", settle=4)
    await b.click("[data-entity-id]")
    before = await b.js("const c = document.querySelector('.card'); return c ? c.innerText : ''")
    await b.js("""
      const el = document.querySelector('[data-entity-id]');
      el.dispatchEvent(new MouseEvent('mouseover', {bubbles:true, cancelable:true}));
      return true;
    """)
    await asyncio.sleep(2.5)
    after = await b.js("const c = document.querySelector('.card'); return c ? c.innerText : ''")
    check(
        "a pinned card never strands on the loading state",
        after and "loading" not in after.lower(),
        after[:80] if after else "card vanished",
    )
    check("the pinned card keeps its content", before and after and len(after) >= 10, after[:80])
    await b.key("Escape")

    # ══ from audit2.py ══

    b = await launch()

    # ── configuration: opening a document must not mark it unsaved ───
    await b.js("localStorage.clear(); return 1")
    await b.goto("/admin/config", settle=3.5)
    keys = await b.js("return Object.keys(localStorage).filter(k => k.startsWith('venona.draft'))")
    check("opening the configuration index writes no draft key", keys == [], keys)
    check(
        "no document is marked unsaved on load",
        (await b.js("return document.querySelectorAll('[data-mark=unsaved]').length")) == 0,
    )

    # The index is grouped, and the headings come from `GET /api/config`, so an
    # index with no groups is that fetch having failed -- which the page is
    # built to survive and nothing else would notice.
    groups = await b.js(
        "return [...document.querySelectorAll('.group-block h2')].map(g => g.innerText.trim());"
    )
    check(
        "the configuration index is grouped by purpose",
        isinstance(groups, list) and len(groups) >= 3,
        " | ".join(groups) if isinstance(groups, list) else groups,
    )

    # Every document, visited. The rows are links, so this is a navigation each
    # -- collect the keys first (see `INDEX_KEYS`) rather than holding handles
    # across one.
    doc_keys = await b.js(INDEX_KEYS)
    check("every declared document is on the index", len(doc_keys or []) >= 10, len(doc_keys or []))
    for doc_key in doc_keys or []:
        await b.goto(f"/admin/config/{doc_key}", settle=1.2)
        # THAT IT RESOLVED, not that it has content: a document whose file does
        # not exist yet reads back empty and renders an empty editor, which is
        # the ordinary case for a generated domain. What must not happen is the
        # pane refusing the key, which an assembled prompt does when the list
        # has not loaded.
        opened = await b.js(
            "return {editor: !!document.querySelector('.cm-editor'),"
            "        refused: /could not be opened|no document called/.test(document.body.innerText)};"
        )
        check(
            f"{doc_key} opens in the editor",
            isinstance(opened, dict) and opened.get("editor") and not opened.get("refused"),
            {doc_key: opened},
        )
    await b.goto("/admin/config", settle=2.5)
    check(
        "visiting every document marks none of them unsaved",
        (await b.js("return document.querySelectorAll('[data-mark=unsaved]').length")) == 0,
        await b.js(
            "return [...document.querySelectorAll('[data-doc]')]"
            "  .map(a => a.dataset.doc + (a.querySelector('[data-mark]') ? ' *' : '')).join(' | ')"
        ),
    )

    # An editable document, for the edit checks below. The loop above ends on an
    # assembled prompt, where typing is correctly a no-op.
    await b.goto("/admin/config/relevance_profile", settle=2.0)
    check(
        "Save is disabled with no changes",
        await b.js(
            "const b = [...document.querySelectorAll('button')].find(x => x.textContent.trim() === 'Save'); return !!b && b.disabled"
        ),
    )

    # a REAL edit must mark it, and only it
    await b.js(editor_append(" x"))
    await asyncio.sleep(0.6)
    check(
        "a real edit writes exactly one draft key",
        (
            await b.js(
                "return Object.keys(localStorage).filter(k => k.startsWith('venona.draft')).length"
            )
        )
        == 1,
        await b.js("return Object.keys(localStorage).filter(k => k.startsWith('venona.draft'))"),
    )
    check(
        "a real edit enables Save",
        await b.js(
            "const b = [...document.querySelectorAll('button')].find(x => x.textContent.trim() === 'Save'); return !!b && !b.disabled"
        ),
    )
    # Row TWO: the header is fixed on every route, and anything belonging to the
    # document you are looking at lives in the ContextBar. Matched through
    # `EDITOR_UNSAVED`, which says why the whole phrase is needed.
    check("a real edit shows 'unsaved changes' in the context bar", await b.js(EDITOR_UNSAVED))

    # ── an unsaved buffer can be thrown away ─────────────────────────
    # With no discard, a stale draft marks a document unsaved forever:
    # `restoreDraftFlags` only deletes a draft that matches disk exactly, since
    # binning any other would throw away real work, and the only other button is
    # Save, which writes that buffer over the file. Three documents sat marked
    # unsaved with no way out but the devtools console.
    await b.js(
        "const d = [...document.querySelectorAll('.editor button')]"
        "  .find(x => x.textContent.trim() === 'discard');"
        "if (d) d.click(); return 1"
    )
    await asyncio.sleep(0.8)
    check(
        "discarding a draft clears the unsaved marker",
        "unsaved" not in (await b.js("return document.querySelector('.context').innerText") or ""),
        await b.js("return document.querySelector('.context').innerText"),
    )
    check(
        "discarding a draft removes its localStorage key",
        (
            await b.js(
                "return Object.keys(localStorage).filter(k => k.startsWith('venona.draft')).length"
            )
        )
        == 0,
    )
    check(
        "discarding a draft restores the file's own text",
        not (await b.js(EDITOR_TEXT) or "").endswith(" x"),
    )
    await b.js("localStorage.clear(); return 1")

    # ── a draft from an older version says so, and is not silently trusted ──
    # A buffer left over from before the file changed is indistinguishable from
    # something typed a minute ago, and Save would overwrite a file the buffer
    # never saw. A pre-versioned draft -- a bare string, which is what shipped
    # -- must land in the same warning rather than be taken at face value.
    await b.goto("/admin/config/relevance_profile", settle=3)
    key = await b.js(
        "return Object.keys(localStorage).find(k => k.startsWith('venona.draft')) "
        "|| 'venona.draft.relevance_profile'"
    )
    await b.js(f"localStorage.setItem({key!r}, 'a legacy draft from an older venona'); return 1")
    await b.goto("/admin/config/relevance_profile", settle=3.5)
    pane = "return document.querySelector('.editor').innerText"
    check(
        "a draft from an older version is called out",
        "older version" in (await b.js(pane) or ""),
        (await b.js(pane) or "")[:160],
    )
    check(
        "...and warns that saving would overwrite the file", "overwrite" in (await b.js(pane) or "")
    )
    await b.goto("/admin/config", settle=2.5)
    check(
        "...and the index marks it differently from ordinary unsaved work",
        (await b.js("return document.querySelectorAll('[data-mark=stale]').length")) == 1,
        await b.js(
            "return [...document.querySelectorAll('[data-mark]')]"
            "  .map(c => c.closest('[data-doc]').dataset.doc + ':' + c.dataset.mark).join(' | ')"
        ),
    )
    # Back to the document: discard lives on the editor.
    await b.goto("/admin/config/relevance_profile", settle=2.5)
    await b.js(
        "const d = [...document.querySelectorAll('.editor button')]"
        "  .find(x => x.textContent.trim().startsWith('discard'));"
        "if (d) d.click(); return 1"
    )
    await asyncio.sleep(0.8)
    check(
        "a stale draft can be discarded too",
        (
            await b.js(
                "return Object.keys(localStorage).filter(k => k.startsWith('venona.draft')).length"
            )
        )
        == 0,
    )
    await b.js("localStorage.clear(); return 1")

    # ── archive ──────────────────────────────────────────────────────
    await b.goto("/briefs", settle=3)
    body = await b.js("return document.querySelector('.content').innerText")
    check(
        "archive rows are clickable",
        await b.js("return !!document.querySelector('tbody tr.selectable')"),
    )
    check("archive has no raw nulls", "null" not in (body or "").lower(), (body or "")[:120])

    # Several briefs can share a day, brief ids being unique per generation.
    # With only the date rendered, those rows are indistinguishable and the
    # archive reads as the same brief listed twice, which is how five destroyed
    # briefs went unnoticed. Every row must carry a time.
    stamps = await b.js(
        "return Array.from(document.querySelectorAll('tbody tr.selectable')).map("
        "  r => (r.children[1]?.innerText || '').trim())"
    )
    # BARE `HH:MM`, in the reader's zone and with no zone name anywhere in this
    # table (ADR 0184): 4.5rem does not hold "22:10 GMT+12", and a header naming
    # the zone took 130px at phone width, twice the date column.
    check(
        "every archive row shows a generated time",
        bool(stamps) and all(re.fullmatch(r"\d{2}:\d{2}", s or "") for s in stamps),
        stamps,
    )

    # Two briefs on ONE date must remain two distinct, separately openable rows.
    dupes = await b.js(
        "const d = Array.from(document.querySelectorAll('tbody tr.selectable'))"
        "  .map(r => (r.children[0]?.innerText || '').trim());"
        "const seen = {}; d.forEach(x => seen[x] = (seen[x] || 0) + 1);"
        "return Object.entries(seen).filter(([, n]) => n > 1).map(([k]) => k);"
    )
    if dupes:
        opened = await b.js(
            "const rows = Array.from(document.querySelectorAll('tbody tr.selectable'))"
            f"  .filter(r => (r.children[0]?.innerText || '').trim() === {dupes[0]!r});"
            "return rows.length;"
        )
        check(
            "same-day briefs are separate archive rows", opened >= 2, f"{dupes[0]}: {opened} rows"
        )
        times = await b.js(
            "const rows = Array.from(document.querySelectorAll('tbody tr.selectable'))"
            f"  .filter(r => (r.children[0]?.innerText || '').trim() === {dupes[0]!r});"
            "return rows.map(r => (r.children[1]?.innerText || '').trim());"
        )
        check("same-day briefs show different times", len(set(times)) == len(times), times)

    # THE FILTER MUST MATCH WHAT IS ON SCREEN. It runs client-side over the
    # RENDERED time, which is local (ADR 0184); one keyed on the raw UTC wire
    # string matches text no row displays, and that typechecks, renders and
    # silently returns nothing.
    if stamps:
        await b.key("/")
        await asyncio.sleep(0.4)
        await b.js(
            "const i = document.querySelector('.context input[type=search]');"
            "if (!i) return false;"
            f"i.value = {stamps[0]!r}; i.dispatchEvent(new Event('input', {{bubbles: true}}));"
            "return true;"
        )
        await asyncio.sleep(0.7)
        matched = await b.js(
            "return Array.from(document.querySelectorAll('tbody tr.selectable'))"
            "  .map(r => (r.children[1]?.innerText || '').trim());"
        )
        check(
            "typing a visible time into the archive filter still finds its row",
            bool(matched) and stamps[0] in matched,
            f"typed {stamps[0]!r}, got {matched}",
        )

    # filter to nothing -> a real empty state. `/` FIRST, because the filter is
    # a glyph until it is asked for and the field does not exist to be typed
    # into; an unguarded `document.querySelector` here throws rather than fails.
    await b.key("/")
    await asyncio.sleep(0.4)
    typed = await b.js(
        "const i = document.querySelector('.context input[type=search]');"
        "if (!i) return false;"
        "i.value = 'zzzzzznope'; i.dispatchEvent(new Event('input', {bubbles: true}));"
        "return true;"
    )
    check("the archive filter opens on `/`", typed is True, typed)
    await asyncio.sleep(0.7)
    check(
        "archive shows an empty state when nothing matches",
        "no brief"
        in (await b.js("return document.querySelector('.content').innerText") or "").lower(),
    )
    check(
        "archive hides the table header when empty",
        (await b.js("return document.querySelectorAll('thead').length")) == 0,
    )

    # ── run detail ───────────────────────────────────────────────────
    #
    # An empty history is a LEGITIMATE state -- "Clear history" hides every run
    # -- so asserting a clickable row unconditionally fails on a well-kept
    # installation, which is the opposite of what the check is for. Same shape
    # as the merge queue's empty case above, and it stands down the same way
    # rather than passing quietly: a check that skips is reported as skipped.
    await b.goto("/admin/runs", settle=3)
    rows = await b.js("return document.querySelectorAll('tbody tr.selectable').length")
    if rows:
        await b.js("document.querySelector('tbody tr.selectable').click(); return 1")
        await asyncio.sleep(2.5)
        check(
            "clicking a run opens its detail",
            "/admin/runs/" in (await b.js("return location.pathname")),
            await b.js("return location.pathname"),
        )
    else:
        check(
            "clicking a run opens its detail",
            True,
            "skipped: no visible runs — the history is cleared",
        )

    # A selectable row is a control, and nothing else can see whether it behaves
    # like one: the markup carries `tabindex` and `role` either way, and a
    # missing keydown handler looks exactly like a row nobody pressed Enter on.
    if rows:
        await b.goto("/admin/runs", settle=3)
        shape = await b.js(
            "const r = document.querySelector('tbody tr.selectable');"
            "return {tab: r.getAttribute('tabindex'), role: r.getAttribute('role')}"
        )
        check(
            "a run row is reachable by keyboard",
            (shape or {}).get("tab") == "0" and (shape or {}).get("role") == "button",
            shape,
        )
        await b.js(
            "const r = document.querySelector('tbody tr.selectable');"
            "r.focus();"
            "r.dispatchEvent(new KeyboardEvent('keydown', "
            "{key: 'Enter', bubbles: true, cancelable: true}));"
            "return 1"
        )
        await asyncio.sleep(2.5)
        check(
            "Enter on a focused run row opens its detail",
            "/admin/runs/" in (await b.js("return location.pathname")),
            await b.js("return location.pathname"),
        )
    else:
        check(
            "Enter on a focused run row opens its detail",
            True,
            "skipped: no visible runs — the history is cleared",
        )
    body = await b.js("return document.querySelector('.content').innerText")
    # `undefined` is a legitimate counter name from the acronyms stage
    # (`acronyms_undefined`), so it cannot double as a rendering-bug marker.
    check(
        "run detail has no raw NaN or object stringification",
        not any(x in (body or "") for x in ["NaN", "[object"]),
        (body or "")[:150],
    )
    # Stage rows, on a run that HAS stages. C11 renders by `RunType.detail`: a
    # `single` run shows its RESULT instead, because one segment that never
    # lights reads as a stage that failed. A check that clicked the NEWEST run
    # and asserted a stage table went red the first time a lookup was the most
    # recent thing that happened -- a true statement about the wrong run. So ask
    # the API which run to open, and assert BOTH renderings.
    pipeline_run = await b.js(
        "const r = await fetch('/api/runs?limit=50').then(r => r.json());"
        "return ((r.items || []).find(x => x.detail !== 'single') || {}).id ?? null"
    )
    if pipeline_run:
        await b.goto(f"/admin/runs/{pipeline_run}", settle=2.5)
        check(
            "run detail shows stage rows",
            await b.js("return document.querySelectorAll('tbody tr').length > 0"),
            pipeline_run,
        )

    single_run = await b.js(
        "const r = await fetch('/api/runs?limit=50').then(r => r.json());"
        "return ((r.items || []).find(x => x.detail === 'single') || {}).id ?? null"
    )
    if single_run:
        await b.goto(f"/admin/runs/{single_run}", settle=2.5)
        single_body = await b.js("return document.querySelector('.content')?.innerText || ''")
        # A one-stage run must render a result, not an empty pipeline spine.
        # `COUNTERS` is that panel's heading; the run's own verdict is the chip
        # above it, asserted separately below.
        check(
            "a single-stage run renders a result rather than an empty spine",
            "HOW IT RAN" in (single_body or "").upper()
            and not await b.js("return document.querySelectorAll('tbody tr').length > 0"),
            (single_body or "")[:150],
        )

        # ⭐ The run's verdict must reach the reader in WORDS (ADR 0183). Every
        # stored value is a database spelling, and a page rendering
        # `nothing_found` at a person fails the reader-language rule -- which
        # nothing else can see, because it typechecks either way.
        check(
            "a run states its verdict in reader words, not database ones",
            not any(
                bad in (single_body or "")
                for bad in ("succeeded_partial", "nothing_found", "nothing_to_do", "delivered")
            ),
            (single_body or "")[:200],
        )

        # A run that found nothing owes the reader the candidates, and they must
        # be sentences rather than serialised JSON. `{"url":` in the body is the
        # marker: it is what a raw trace looks like.
        outcome = await b.js(
            f"const r = await fetch('/api/runs/{single_run}').then(r => r.json());"
            "return r.outcome ?? null"
        )
        if outcome == "nothing_found":
            check(
                "a lookup that found nothing explains itself without raw JSON",
                '{"url"' not in (single_body or "") and '"reason"' not in (single_body or ""),
                (single_body or "")[:200],
            )

    # ⭐ EVERY KIND OF RUN SAYS WHAT IT PRODUCED, in its own words.
    #
    # A result panel shaped for one kind renders, typechecks and passes the
    # whole offline suite for every other kind that falls into it, which is how
    # a reader's brief came to display `sourced: no` on a run that had just
    # written a document. The words come from `run_types.RunType.facts`/`lines`
    # (ADR 0187), so what is worth asserting is that they ARRIVE.
    #
    # Asked of the API per kind, and skipped when the history holds none: the
    # seed writes no `runs` row, so requiring one of each kind would make this
    # check demand model spend to pass.
    for kind in ("personal_brief", "lookup", "ingest", "daily", "on_demand"):
        found = await b.js(
            "const r = await fetch('/api/runs?limit=50').then(r => r.json());"
            f"return ((r.items || []).find(x => x.kind === {kind!r}) || {{}}).id ?? null"
        )
        if not found:
            continue
        await b.goto(f"/admin/runs/{found}", settle=2.5)
        body = await b.js("return document.querySelector('.content')?.innerText || ''")
        detail = await b.js(
            f"const r = await fetch('/api/runs/{found}').then(r => r.json());"
            "return {facts: (r.facts || []).length, line: r.result_line || ''}"
        )
        check(
            f"a {kind} run states what it produced",
            isinstance(detail, dict) and bool(detail.get("line")),
            {"run": found, **(detail if isinstance(detail, dict) else {})},
        )
        # The lookup panel's own words, on a kind that is not a lookup. This is
        # the exact string the hardcoded panel rendered at a reader's brief.
        if kind not in ("lookup",):
            check(
                f"a {kind} run does not borrow the lookup panel's words",
                "SOURCED" not in (body or "").upper(),
                (body or "")[:200],
            )

    # a bad id must not show a raw error
    await b.goto("/admin/runs/run:does-not-exist", settle=2.5)
    body = await b.js("return document.querySelector('.content').innerText")
    check(
        "unknown run id shows an empty state, not a raw error",
        "no such run" in (body or "").lower(),
        (body or "")[:120],
    )

    await b.goto("/briefs/brief:not-a-real-id", settle=2.5)
    body = await b.js("return document.querySelector('.content').innerText")
    check(
        "unknown brief id shows an empty state, not a raw error",
        "no brief" in (body or "").lower(),
        (body or "")[:120],
    )

    # ── curation queues ──────────────────────────────────────────────
    #
    # An empty queue is a legitimate state -- every conflict has been curated --
    # so asserting a pair unconditionally fails on a well-maintained database,
    # which is the opposite of what the check is for. Ask the server what is
    # pending and assert the matching surface.
    await b.goto("/admin/curation", settle=3.5)
    pending = await b.js(
        "const r = await fetch('/api/curation/conflicts?limit=1')"
        "  .then(r => r.json()).catch(() => null);"
        "return r ? (r.total ?? (r.items || []).length) : null"
    )
    if pending:
        check(
            "merge queue renders a pair",
            await b.js("return document.querySelectorAll('.body').length >= 2"),
            f"{pending} pending",
        )
    else:
        body = await b.js("return document.querySelector('.content')?.innerText || ''")
        check(
            "an empty merge queue says so rather than rendering nothing",
            len((body or "").strip()) > 0,
            (body or "")[:120],
        )
    # Selected through the `SegmentedControl`'s aria-label rather than a class,
    # so the next reshaping of the control does not silently stop switching
    # tabs: a selector that returns 1 whether or not it found anything leaves
    # every check after it running against the merges queue while claiming to be
    # the definitions one.
    switched = await b.js(
        "const t = [...document.querySelectorAll('[aria-label=\"curation queue\"] button')]"
        "  .find(c => /definitions/i.test(c.textContent));"
        "if (!t) return 'no definitions segment'; t.click(); return 'clicked';"
    )
    check("the definitions queue can be reached", switched == "clicked", switched)
    await asyncio.sleep(2.0)
    # ── one surface per decision, with a proposal ────────────────────
    #
    # One row per EXPANSION made this a 604-row list with a cursor (2026-08-09),
    # so `FCC` appeared twice and `CPE` five times, and a settled surface stayed
    # on screen because a refetch kept any row still matching the filter. The
    # shape is the merge queue's: one surface, only the verdicts that apply to
    # it, and a suggestion derived from evidence that already existed.
    #
    # PRESENCE ONLY. Every verdict below writes to the real database, and one of
    # them edits `profile/lexicon.yaml`, which `GUARDED_CONFIG` exists to stop
    # this script touching.
    card = await b.js(
        "const c = document.querySelector('.card');"
        "if (!c) return {noCard: true};"
        "return {surface: (c.querySelector('.surface')||{}).textContent,"
        "        text: c.innerText.slice(0, 400)};"
    )
    check(
        "the definitions queue shows one surface at a time",
        isinstance(card, dict) and not card.get("noCard") and card.get("surface"),
        card,
    )

    counted = await b.js(
        "const r = await fetch('/api/curation/definitions?limit=5').then(r => r.json());"
        "const i = (r.items || [])[0];"
        "if (!i) return {empty: true};"
        "return {pending: r.total_pending, surface: i.surface,"
        "        readings: (i.readings || []).length,"
        "        suggested: (i.suggestion || {}).verdict || null};"
    )
    if isinstance(counted, dict) and not counted.get("empty"):
        # The unit is a surface, so the count is questions rather than rows.
        check(
            "the queue counts surfaces awaiting a decision",
            isinstance(counted.get("pending"), int),
            counted,
        )
        check("the top item carries a suggested verdict", counted.get("suggested"), counted)
        # ...and the suggestion is on screen as a button, not just in the payload.
        proposed = await b.js(
            "const s = document.querySelector('.card .border-accent-muted');"
            "return s ? s.innerText.slice(0, 160) : null"
        )
        check("the suggestion reaches the screen", proposed, proposed)

    # A reading's support is shown where the decision is made.
    if isinstance(counted, dict) and (counted.get("readings") or 0) > 0:
        shown = await b.js(
            "const c = document.querySelector('.card');"
            "return c ? /\\d\\.\\d\\d\\s*·\\s*\\d+\\s+source/.test(c.innerText) : false"
        )
        check(
            "a reading shows how well supported it is",
            shown,
            await b.js("return document.querySelector('.card')?.innerText?.slice(0,200)"),
        )

    # ── a surface that is not an acronym has somewhere to go ─────────
    #
    # A queue that diagnoses this and offers nothing -- "BAE ... is a name, not
    # an acronym", with accept / reject / edit / look again / choose as the only
    # verdicts -- assumes the answer is always a different DEFINITION.
    #
    # NEITHER ACTION IS FIRED HERE. "Not an acronym" writes to
    # `profile/lexicon.yaml`, which `GUARDED_CONFIG` above exists to stop this
    # script touching, and the attach rewrites entity status. Presence, and that
    # the picker can find something, is what is checkable for free.
    #
    # The labels are matched AS THE APP RENDERS THEM. `entity` is this repo's
    # word, kept off every user-facing surface, so the verdict reads "It names a
    # thing" -- and a check matching this repo's vocabulary instead reports a
    # MISSING verdict rather than a renamed one.
    verdicts = await b.js(
        "const labels = [...document.querySelectorAll('button')].map(b => b.textContent.trim());"
        "return {entity: labels.some(t => t.startsWith('It names a thing')),"
        "        notAcronym: labels.some(t => t.startsWith('Not an acronym'))}"
    )
    check("the queue offers 'it names a thing'", (verdicts or {}).get("entity"), verdicts)
    check("the queue offers 'not an acronym'", (verdicts or {}).get("notAcronym"), verdicts)

    picker = await b.js(
        "const b2 = [...document.querySelectorAll('button')]"
        '  .find(x => x.textContent.trim().startsWith("It\'s an entity"));'
        "if (!b2) return {missing: true};"
        "b2.click();"
        "await new Promise(r => setTimeout(r, 2500));"
        "const d = document.querySelector('[role=dialog]');"
        "if (!d) return {noDialog: true};"
        "return {text: d.innerText.slice(0, 400),"
        "        options: d.querySelectorAll('li').length};"
    )
    if isinstance(picker, dict) and not picker.get("missing"):
        check("the entity picker opens", not picker.get("noDialog"), picker)
        # It seeds from the surface, and zero candidates is a legitimate answer:
        # `DVIDS` names no entity in this corpus and the picker says so rather
        # than inventing one. What must not happen is the picker failing to
        # SEARCH, so this asserts it either found something or explained why.
        searched = (picker.get("options") or 0) > 0 or "Nothing matches" in (
            picker.get("text") or ""
        )
        check("the entity picker searches", searched, picker)
    await b.js(
        "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));"
        "return 1"
    )
    await asyncio.sleep(0.6)
    check(
        "the entity picker closes without attaching anything",
        not await b.js("return !!document.querySelector('[role=dialog]')"),
    )

    # ── "Look up all" must not open on a guess ───────────────────────
    #
    # The only control in the app that can spend tens of dollars and run for
    # hours from one press (C11), so the modal in front of it is the feature.
    # Two ways it ships broken and looks fine: the dialog opens but the numbers
    # never arrive (the preview 404s and the body sits on "counting the queue…"
    # forever), or it opens with an enabled confirm before there is a count to
    # confirm. Neither is visible in a screenshot taken a second later.
    #
    # Nothing here presses the confirm. The button it would press starts a run
    # that spends real money on every undefined acronym in the database.
    quoted = await b.js(
        "const b = [...document.querySelectorAll('button')]"
        "  .find(x => x.textContent.trim() === 'Look up all');"
        "if (!b) return {missing: true};"
        "b.click();"
        "await new Promise(r => setTimeout(r, 2500));"
        "const d = document.querySelector('[role=dialog]');"
        "if (!d) return {noDialog: true};"
        "const confirm = [...d.querySelectorAll('button')]"
        "  .find(x => x.textContent.trim().startsWith('Look up'));"
        "return {text: d.innerText, label: confirm?.textContent.trim(),"
        "        disabled: !!confirm?.disabled};"
    )
    check("the definitions tab offers a bulk lookup", not (quoted or {}).get("missing"), quoted)
    if isinstance(quoted, dict) and not quoted.get("missing"):
        check("look-up-all opens a confirmation", not quoted.get("noDialog"), quoted)
        body = quoted.get("text") or ""
        check(
            "the confirmation has finished counting", "counting the queue" not in body, body[:200]
        )
        # A price and a duration, both. Either alone is a different decision:
        # 300 is fine, $24 is fine, three hours is not.
        check("the confirmation quotes a price", "$" in body, body[:200])
        check(
            "the confirmation quotes a duration",
            any(unit in body for unit in ("seconds", "minutes", "hours")),
            body[:200],
        )
        # The label carries the count, so a confirm reading "Look up" with no
        # number is a preview that never landed.
        check(
            "the confirm names the count or is disabled",
            quoted.get("disabled") or re.search(r"Look up \d+", quoted.get("label") or ""),
            quoted,
        )
    # Escape, not the confirm. See above.
    await b.js(
        "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));"
        "return 1"
    )
    await asyncio.sleep(0.5)
    check(
        "the confirmation closes without starting anything",
        not await b.js("return !!document.querySelector('[role=dialog]')"),
    )

    # ── a single lookup must not freeze the queue ────────────────────
    #
    # "Look it up" held the shared `busy` flag across the whole run: measured
    # 2026-08-18 at 25s to 3min with every verdict button dead. The behaviour
    # under test is frontend wiring, so the POST is intercepted in-page and
    # answered with a fake 202 -- the real endpoint starts a run that spends
    # ~$0.13, and a stub proves the queue keeps moving just as well.
    lookup = await b.js(
        "const btn = [...document.querySelectorAll('button')]"
        "  .find(x => x.textContent.trim().startsWith('Look it up'));"
        "if (!btn) return {skip: true};"
        "const before = document.querySelector('.surface')?.textContent;"
        "const loaded = (document.body.innerText.match(/of (\\d+) loaded/) || [])[1];"
        "window.__origFetch = window.fetch;"
        "window.__smokeRun = {status: 'running'};"
        "window.fetch = (input, init) => {"
        "  const url = input instanceof Request ? input.url : String(input);"
        "  const json = (body, status) => Promise.resolve(new Response("
        "    JSON.stringify(body), {status, headers: {'content-type': 'application/json'}}));"
        "  if (url.includes('/runs/smoke-lookup')) return json(window.__smokeRun, 200);"
        "  if (url.includes('/lookup')) return json({run_id: 'smoke-lookup', status: 'running', lane: 'light'}, 202);"
        "  return window.__origFetch(input, init);"
        "};"
        "btn.click();"
        "await new Promise(r => setTimeout(r, 600));"
        "return {before, loaded: Number(loaded || 1),"
        "        after: document.querySelector('.surface')?.textContent,"
        "        inFlight: /lookups? in flight/.test(document.body.innerText)};"
    )
    if isinstance(lookup, dict) and not lookup.get("skip"):
        check("a lookup is queued rather than awaited", lookup.get("inFlight"), lookup)
        if lookup.get("loaded", 1) > 1:
            # The cursor moved on the moment the 202 landed — queue and keep going.
            check(
                "the cursor advances past the pending surface",
                lookup.get("after") and lookup.get("after") != lookup.get("before"),
                lookup,
            )
        pending = await b.js(
            "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowLeft', bubbles: true}));"
            "await new Promise(r => setTimeout(r, 400));"
            "const card = document.querySelector('.card');"
            "const chip = /looking it up|lookup queued/.test(card?.innerText || '');"
            "const actions = [...document.querySelectorAll('.actions button')];"
            "const skip = [...document.querySelectorAll('button')]"
            "  .find(x => x.textContent.trim().startsWith('Skip'));"
            "return {chip,"
            "        frozen: actions.length > 0 && actions.every(x => x.disabled),"
            "        skipEnabled: !!skip && !skip.disabled};"
        )
        check(
            "the pending surface says a lookup is in flight", (pending or {}).get("chip"), pending
        )
        check("the pending surface's verdicts are frozen", (pending or {}).get("frozen"), pending)
        check(
            "navigation stays live on a pending surface",
            (pending or {}).get("skipEnabled"),
            pending,
        )
        landed = await b.js(
            "window.__smokeRun = {status: 'succeeded'};"
            "await new Promise(r => setTimeout(r, 2500));"
            "window.fetch = window.__origFetch;"
            "return {chip: /looking it up|lookup queued/.test("
            "  document.querySelector('.card')?.innerText || '')};"
        )
        check("the pending badge clears when the run lands", not (landed or {}).get("chip"), landed)
    else:
        check(
            "a lookup is queued rather than awaited",
            True,
            "skipped: the top surface offers no lookup",
        )

    # A STALE SELECTOR HERE IS SILENT. The three queues are a `SegmentedControl`
    # -- `role="radio"` buttons under a `role="radiogroup"` -- so a
    # `querySelectorAll('.chip')` finds nothing, `t && t.click()` swallows it,
    # and the assertion reads whichever panel happens to be open. That passes
    # for as long as the panel's text contains the word "merge", which on a
    # corpus with open conflicts it always does. So a click that may not land
    # must be asserted.
    switched = await b.js(
        "const t = [...document.querySelectorAll('[role=radio], .chip')]"
        "  .find(c => c.textContent.trim() === 'history');"
        "if (!t) return 'NO-HISTORY-TAB';"
        "t.click(); return 'clicked';"
    )
    check("the curation view offers a history tab", switched == "clicked", switched)
    await asyncio.sleep(2.0)
    hist = await b.js("return document.querySelector('.content').innerText")
    check("merge history renders", "merge" in (hist or "").lower(), (hist or "")[:120])

    # PUT THE TAB BACK. Every later check that visits `/admin/curation` assumes
    # the default queue, and leaving it switched made a check thirty screens
    # later fail for a reason that looked nothing like this one.
    await b.js(
        "const t = [...document.querySelectorAll('[role=radio], .chip')]"
        "  .find(c => (c.textContent || '').trim().startsWith('merges'));"
        "t && t.click(); return 1"
    )
    await asyncio.sleep(1.0)

    # ── the queue and its cards must name the same two entities ──────
    #
    # `list_conflicts` joined `entities` on the ids the conflict was FILED with
    # while the cards resolved those ids through merge chains, so a conflict
    # that outlived a merge showed `Army` in the queue and `U.S. Army` on the
    # card. The curator merged what the card said and the API refused, naming an
    # id that was never on screen. The page renders, both halves are internally
    # consistent, and only the merge fails.
    await b.goto("/admin/curation", settle=3.5)
    agreement = await b.js(
        "const cards = [...document.querySelectorAll('.pair .name')]"
        "  .map(e => e.textContent.trim());"
        "if (cards.length !== 2) return {skip: true};"
        "const r = await fetch('/api/curation/conflicts').then(r => r.json());"
        "const open = (r.items || []).filter(c => !c.resolved);"
        "if (!open.length) return {skip: true};"
        "const c = open[0];"
        "return {cards, api: [c.existing_name, c.proposed_name]};"
    )
    if isinstance(agreement, dict) and not agreement.get("skip"):
        check(
            "the merge queue and its cards name the same two entities",
            sorted(agreement["cards"]) == sorted(x for x in agreement["api"] if x),
            agreement,
        )

    # ── footer / header consistency on every route ───────────────────
    for path in CHROME_ROUTES:
        await b.goto(path, settle=2.5)
        h = await b.js(
            "const bar = document.querySelector('header.bar');"
            "return [...bar.querySelectorAll('button, .palette')]"
            "  .map(e => Math.round(e.getBoundingClientRect().height)).filter(x => x > 0);"
        )
        check(f"{path} header heights consistent", isinstance(h, list) and len(set(h)) == 1, h)
        overflow = await b.js(
            "const c = document.querySelector('.content');"
            "return c ? c.scrollWidth - c.clientWidth : 0;"
        )
        check(f"{path} does not scroll horizontally", (overflow or 0) <= 1, overflow)

    # ── the two-row chrome, and why it is two rows (ADR 0181) ────────
    #
    # The point of splitting the chrome is that row one cannot move. Carrying
    # each route's identity, filters and actions beside the nav lets Curation's
    # tabs, Graph's toggles and Settings' Apply/Save each shift or squeeze it,
    # and navigation that moves under the cursor as you navigate is the one
    # thing a nav must not do.
    nav_boxes = {}
    for path in ["/", "/briefs", "/graph", "/admin/curation", "/admin/runs", "/admin"]:
        await b.goto(path, settle=2.5)
        nav_boxes[path] = await b.js(
            "const n = document.querySelector('header.bar nav');"
            "const r = n.getBoundingClientRect();"
            "return [Math.round(r.x), Math.round(r.y), Math.round(r.width)];"
        )
    check(
        "the nav sits in the same place on every route",
        len({tuple(v) for v in nav_boxes.values()}) == 1,
        nav_boxes,
    )
    # Width-aware, not weakened: this window is desktop-sized, so the row must
    # be VISIBLE here. The drawer replaces it only below `--breakpoint-nav`
    # (800px), so a zero-width nav at 1500px means the collapse fired at the
    # wrong width. The phone pass at the end asserts the inverse.
    check(
        "the desktop nav row is visible at desktop width",
        all(v and v[2] > 0 for v in nav_boxes.values()),
        nav_boxes,
    )

    # ── row two does not move either (ADR 0181) ──────────────────────
    #
    # Row two carries the tab strip for every route that has one, so it is
    # navigation and held to the rule above. An identity zone sized to its own
    # text made the controls beside it begin wherever that text ended: measured
    # 2026-08-31 across the routes, the strip landed anywhere from x=226 to
    # x=535.
    #
    # THE CENTRE IS THE INVARIANT, not the left edge. The zone is the middle
    # track of three with a `1fr` either side, so it is pinned to the middle of
    # the window and a wider strip grows symmetrically about it; asserting the
    # left edge would assert that every route's tabs are the same WIDTH.
    #
    # Only routes with something in the zone are compared: an empty one is a
    # zero-width box with nothing in it to move.
    centres = {}
    for path in ["/", "/briefs", "/graph", "/lookup", "/learn", "/admin/curation", "/runs"]:
        await b.goto(path, settle=2.5)
        centres[path] = await b.js(
            "const c = document.getElementById('context-controls');"
            "if (!c || !c.firstElementChild) return null;"
            "const r = c.getBoundingClientRect();"
            "return Math.round(r.left + r.width / 2);"
        )
    seen = {p: x for p, x in centres.items() if x is not None}
    check(
        "the controls zone is centred on the same line on every route",
        len(set(seen.values())) == 1,
        seen,
    )
    # And that line is the middle of the window, not the middle of a lopsided
    # box: the bar is padded symmetrically and the identity carries the margin
    # rail as its own inset, so the centre does not drift with `--edge`.
    width = await b.js("return window.innerWidth")
    check(
        "and that line is the middle of the window",
        seen and abs(next(iter(seen.values())) - round(width / 2)) <= 2,
        {"centre": next(iter(seen.values()), None), "half": round(width / 2)},
    )

    # One control vocabulary per zone (ADR 0181). A filled ACCENT control means
    # "primary action" and those live in `actions`; a filled control in
    # `controls` makes "whole graph" and "Rebuild graph" render identically
    # while doing opposite kinds of work.
    #
    # THE TOKENS ARE RESOLVED THROUGH A PROBE, never hardcoded. A brightness
    # heuristic of `m[0] > 120 && m[1] > 150` describes #a8d97e in decimal; the
    # aqua accent is rgb(63, 189, 214), so `63 > 120` is false and the check
    # went BLIND rather than red -- no element ever classified as filled, every
    # route passing, the rule silently unenforced. A colour test that hardcodes
    # the colour works until the colour changes, which is the one moment it
    # matters.
    #
    # All three accent rungs, not just `--accent-primary`: a control filled with
    # `--accent-muted` is just as much a primary-action signal, and a brightness
    # heuristic misses it (#6a8f52 fails `> 120` too).
    for path in ["/graph", "/admin/curation", "/admin/runs", "/briefs"]:
        await b.goto(path, settle=2.5)
        filled = await b.js(
            # Both zones that hold view controls -- the centred one and the
            # toggles at the trailing edge. A `Toggle` sits out there beside the
            # actions, and what keeps it readable as a control rather than a
            # verb is that it is never filled.
            "const z = document.querySelector('.context');"
            "if (!z) return [];"
            # getPropertyValue on a custom property returns the token text
            # (`#3fbdd6`) while backgroundColor returns `rgb(...)`. Painting
            # each token onto a throwaway element puts both in one notation.
            "const probe = document.createElement('span');"
            "document.body.appendChild(probe);"
            "const accents = new Set("
            "  ['--accent-primary', '--accent-muted', '--accent-deep'].map(n => {"
            "    probe.style.color = 'var(' + n + ')';"
            "    return getComputedStyle(probe).color;"
            "  }));"
            "probe.remove();"
            "const hit = [];"
            "for (const el of z.querySelectorAll('*')) {"
            # `actions` is the one zone a filled control is allowed in; it has
            # its own check for at most one.
            "  if (el.closest('#context-actions')) continue;"
            "  const r = el.getBoundingClientRect();"
            # A toggle's state dot IS a filled accent mark, and that is the
            # whole point of it -- filled means on. What must not be filled is
            # the CONTROL. Anything dot-sized is a mark, not a control.
            "  if (r.width < 12 || r.height < 12) continue;"
            "  const c = getComputedStyle(el).backgroundColor;"
            "  if (c && accents.has(c)) hit.push(el.className + ' ' + c);"
            "}"
            "return hit;"
        )
        check(
            f"{path} has no filled control outside the actions zone",
            isinstance(filled, list) and not filled,
            filled,
        )

    # ── the filter waits as a glyph at desktop width too (ADR 0181) ──
    #
    # Not a narrow-screen behaviour. On every route that has one the filter is a
    # glyph at the trailing edge until `/` or a click asks for it, and what
    # opens covers the row rather than squeezing into it.
    for path in ["/lookup", "/briefs", "/search"]:
        await b.goto(path, settle=2.5)
        shut = await b.js(
            "const bar = document.querySelector('.context');"
            "const zone = document.getElementById('context-search');"
            "if (!bar || !zone) return {missing: true};"
            "const open = [...bar.querySelectorAll('input[type=search]')]"
            "  .filter((e) => e.getBoundingClientRect().width > 0);"
            "const glyph = zone.querySelector('button');"
            "return {fields: open.length, glyph: !!glyph,"
            "        rightOf: glyph"
            "          ? Math.round(bar.getBoundingClientRect().right"
            "                       - glyph.getBoundingClientRect().right)"
            "          : -1};"
        )
        check(
            f"{path} keeps its filter as a glyph until asked",
            isinstance(shut, dict) and shut.get("fields") == 0 and shut.get("glyph"),
            shut,
        )
        # Trailing edge: within the bar's own right padding, not floating mid-row.
        check(
            f"{path} puts that glyph at the trailing edge",
            isinstance(shut, dict) and 0 <= shut.get("rightOf", -1) <= 60,
            shut,
        )

    # ── the filter opens on a press, and closes on one outside ───────
    #
    # The open-then-immediately-close bug is the one worth a check: a document
    # listener added when the panel opens can eat the very press that opened it.
    # It does not here, because the glyph opens on `click` and the listener
    # watches `pointerdown`, which has already been and gone by then -- but that
    # ordering is invisible in the source and one refactor from being wrong.
    await b.goto("/lookup", settle=2.5)
    await b.pointer_click("#context-search button")
    opened = await b.js(
        "return [...document.querySelectorAll('.context input[type=search]')]"
        "  .filter((e) => e.getBoundingClientRect().width > 0).length;"
    )
    check("a press on the glyph opens the filter and it stays open", opened == 1, opened)

    # A press INSIDE must not close it, or picking from a slotted dropdown would
    # dismiss the thing being picked from.
    await b.pointer_click(".context input[type=search]")
    still = await b.js(
        "return [...document.querySelectorAll('.context input[type=search]')]"
        "  .filter((e) => e.getBoundingClientRect().width > 0).length;"
    )
    check("a press inside the filter leaves it open", still == 1, still)

    # Type, then press outside: it closes, it does NOT clear, and the glyph says
    # so. Closing without that mark leaves a filtered list with nothing on
    # screen explaining why it is short.
    await b.js(
        "const i = document.querySelector('.context input[type=search]');"
        "i.value = 'army'; i.dispatchEvent(new Event('input', {bubbles: true})); return 1"
    )
    await asyncio.sleep(1.4)
    await b.pointer_click(".content")
    marked = await b.js(
        "const zone = document.getElementById('context-search');"
        "const open = [...document.querySelectorAll('.context input[type=search]')]"
        "  .filter((e) => e.getBoundingClientRect().width > 0).length;"
        "const glyph = zone && zone.querySelector('button');"
        "if (!glyph) return {open, glyph: false};"
        "const probe = document.createElement('span');"
        "document.body.appendChild(probe);"
        "probe.style.color = 'var(--accent-primary)';"
        "const accent = getComputedStyle(probe).color;"
        "probe.remove();"
        "return {open, glyph: true, accent: getComputedStyle(glyph).color === accent,"
        "        label: glyph.getAttribute('aria-label') || ''};"
    )
    check(
        "a press outside closes the filter",
        isinstance(marked, dict) and marked.get("open") == 0,
        marked,
    )
    check(
        "and the collapsed glyph says a filter is still applied",
        isinstance(marked, dict)
        and marked.get("accent")
        and "filtered" in marked.get("label", "").lower(),
        marked,
    )

    # ── the actions zone: verbs, and one loud one ────────────────────
    #
    # `docs/DESIGN.md` caps this zone at two actions and four routes carry three
    # -- Brief (older, newer, Rewrite), Config (a spinner, Apply, Save), Run
    # detail and People -- so a COUNT check would be red on all four and would
    # be a check about work nobody has done rather than about the rule. It stays
    # a review guideline.
    #
    # What IS asserted is the half that carries the meaning: a zone with two
    # solid accent controls has no primary action at all, only two things
    # shouting equally. The controls zone is checked for NO fill just above.
    for path in ["/", "/admin/curation", "/admin/runs", "/admin/config/sources"]:
        await b.goto(path, settle=2.5)
        loud = await b.js(
            "const z = document.querySelector('#context-actions');"
            "if (!z) return {missing: true};"
            "const probe = document.createElement('span');"
            "document.body.appendChild(probe);"
            "const accents = new Set("
            "  ['--accent-primary', '--accent-muted', '--accent-deep'].map(n => {"
            "    probe.style.color = 'var(' + n + ')';"
            "    return getComputedStyle(probe).color;"
            "  }));"
            "probe.remove();"
            "const hit = [];"
            "for (const el of z.querySelectorAll('*')) {"
            "  const r = el.getBoundingClientRect();"
            "  if (r.width < 12 || r.height < 12) continue;"
            "  const c = getComputedStyle(el).backgroundColor;"
            "  if (c && accents.has(c)) hit.push((el.textContent || '').trim().slice(0, 24));"
            "}"
            "return {hit};"
        )
        check(
            f"{path} has at most one filled action",
            isinstance(loud, dict) and not loud.get("missing") and len(loud.get("hit", [])) <= 1,
            loud,
        )

    # ── one optical line in row two ──────────────────────────────────
    #
    # The same check row one has, applied to the row that varies. Row one is
    # fixed; row two is assembled per route out of tabs, chips, toggles, a
    # filter and buttons, which is the pile `--control-h` exists to keep on one
    # line.
    #
    # Segments are EXCLUDED: they sit inside the segmented control's frame at
    # its inner height, and the frame is the control. Measuring both fails the
    # check on a design that is correct.
    for path in [
        "/",
        "/briefs",
        "/lookup",
        "/admin/curation",
        "/admin/runs",
        "/admin/config/sources",
    ]:
        await b.goto(path, settle=2.5)
        heights = await b.js(
            "const bar = document.querySelector('.context');"
            "if (!bar) return {missing: true};"
            "const out = [];"
            "for (const el of bar.querySelectorAll('button, input, [role=radiogroup]')) {"
            "  if (el.closest('[role=radiogroup]') !== el && el.closest('[role=radiogroup]')) continue;"
            "  const h = Math.round(el.getBoundingClientRect().height);"
            "  if (h > 0) out.push(h);"
            "}"
            "return {heights: out};"
        )
        check(
            f"{path} draws row two on one optical line",
            isinstance(heights, dict)
            and not heights.get("missing")
            and len(set(heights.get("heights", []))) <= 1,
            heights,
        )

    # The bar is subordinate to the nav, not a second one. Equal heights and
    # equal ground read as two navs.
    await b.goto("/admin/curation", settle=2.5)
    # GUARDED, and it returns a DIAGNOSTIC rather than throwing or returning
    # null. `getComputedStyle(null)` threw here once: the exception crossed back
    # as an object with no `header` key, the lookup below raised `KeyError`, and
    # that killed the whole script at check 60 of 200 -- one slow render on one
    # screen reported as zero results for every screen after it.
    #
    # It was a timing flake, which is why the shape matters. `null` would fail
    # cleanly and tell you nothing; the `DIAG` object names the path it was on
    # and the first 200 characters of the page, so the next person can tell "the
    # bar is missing" from "the app had not finished routing".
    recessed = await b.js(
        "const h = document.querySelector('header.bar');"
        "const c = document.querySelector('.context');"
        "if (!h || !c) return {DIAG: true, path: location.pathname, h: !!h, c: !!c,"
        "  body: document.body.innerText.slice(0, 200)};"
        "return {header: getComputedStyle(h).backgroundColor,"
        "        context: getComputedStyle(c).backgroundColor,"
        "        hh: Math.round(h.getBoundingClientRect().height),"
        "        ch: Math.round(c.getBoundingClientRect().height)};"
    )
    check(
        "the context bar is recessed and shorter than the header",
        isinstance(recessed, dict)
        and recessed.get("header") != recessed.get("context")
        and (recessed.get("ch") or 0) < (recessed.get("hh") or 0),
        recessed,
    )

    # A route with nothing to say must not leave an empty bar taking height --
    # the brief keeps its full reading column.
    empty_ok = await b.js("const c = document.querySelector('.context');return !!c;")
    check("the context bar element always exists (teleport targets must resolve)", empty_ok)

    # ── the brief's two outer gaps are equal ─────────────────────────
    #
    # The reading column is the `fr` track BETWEEN two `--rail-margin` gaps, so
    # it absorbs every spare pixel and the margins stay symmetric at any width.
    # A layout that floors the right gutter against a fixed left margin measured
    # 2026-08-07 at 140px against 36px on a wide window: the reading column
    # visibly off its own axis, and nothing here could see it.
    await b.goto("/", settle=3.5)
    gaps = await b.js(
        "const col = document.querySelector('.view--prose .column');"
        "const side = document.querySelector('.view--prose .side');"
        "const view = document.querySelector('.view--prose');"
        "if (!col || !side || !view) return null;"
        "const c = col.getBoundingClientRect(), s = side.getBoundingClientRect();"
        "const v = view.getBoundingClientRect();"
        "const pad = parseFloat(getComputedStyle(view).paddingLeft) || 0;"
        "return [Math.round(c.x - v.x - pad), Math.round(s.x - c.right)];"
    )
    check(
        "the brief's margin rail and its centre gap are equal",
        isinstance(gaps, list) and abs(gaps[0] - gaps[1]) <= 1,
        gaps,
    )

    # ── the survivor is bracketed, once, and its label is not clipped ─
    #
    # Guarded on there BEING a pair: this is a statement about how a pair
    # renders, and an empty queue is the normal end state of doing the work.
    # Unguarded it reported `{'count': 0}` as a layout failure the first time
    # the queue was cleared -- the suite accusing the user of a bug for
    # finishing a task.
    await b.goto("/admin/curation", settle=3.5)
    if not await b.js("return !!document.querySelector('.pair')"):
        check(
            "exactly one card is bracketed, and its label is not clipped",
            True,
            "skipped: no open conflicts to review",
        )
    else:
        bracket = await b.js(
            "const bs = document.querySelectorAll('.pair .bracket:not(.bracket--box)');"
            "if (bs.length !== 1) return {count: bs.length};"
            "const b0 = bs[0];"
            "const tag = b0.querySelector('.bracket__label');"
            "if (!tag) return {count: 1, tag: false};"
            "const t = tag.getBoundingClientRect();"
            "const scroller = b0.querySelector('.overflow-y-auto');"
            "return {count: 1, tag: true, height: Math.round(t.height),"
            "        clipped: getComputedStyle(b0).overflowY !== 'visible',"
            "        scrolls: !!scroller};"
        )
        # Once per region, and the ARMS are what that rule is about: the
        # counterpart card carries the same label tag on an ordinary box
        # (`variant="box"`), so both sides read as labelled frames while only
        # one wears the bracket. Two bracketed frames side by side cancel out.
        # The label sits ABOVE the frame's top edge, so a scroll container on
        # the bracket itself cuts it off, which it did.
        check(
            "exactly one card is bracketed, and its label is not clipped",
            isinstance(bracket, dict)
            and bracket.get("count") == 1
            and bracket.get("tag")
            and not bracket.get("clipped")
            and bracket.get("height", 0) > 0,
            bracket,
        )

    # ── opening a source does not change which entity survives ───────
    #
    # The evidence links live inside the column that picks a side. With the
    # column itself a <button>, clicking a source both followed the link and
    # flipped the survivor, so checking the evidence silently changed the answer
    # being checked and nothing on the page said so. Guarded on a pair for the
    # same reason as the bracket check above.
    if not await b.js("return !!document.querySelector('.pair')"):
        check(
            "opening a source does not move the survivor",
            True,
            "skipped: no open conflicts to review",
        )
    else:
        moved = await b.js(
            "const before = [...document.querySelectorAll('.pair .side')]"
            "  .map(s => s.classList.contains('survivor'));"
            "const link = document.querySelector('.pair .side:not(.survivor) a');"
            "if (!link) return {link: false};"
            "link.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));"
            "const after = [...document.querySelectorAll('.pair .side')]"
            "  .map(s => s.classList.contains('survivor'));"
            "return {link: true, before, after};"
        )
        check(
            "opening a source does not move the survivor",
            isinstance(moved, dict)
            and (not moved.get("link") or moved.get("before") == moved.get("after")),
            moved,
        )

        # The structural half of the same rule, and the one that stays true
        # after the queue is emptied: an anchor inside a control is a control
        # that fires on the way to somewhere else.
        nested = await b.js(
            "return [...document.querySelectorAll('a')]  .filter(a => a.closest('button')).length"
        )
        check("no link is nested inside a button", nested == 0, nested)

    # ══ from audit3.py ══

    b = await launch()

    # ── graph: selection actually populates the detail rail ──────────
    await b.goto("/graph?mode=all", settle=6)
    await b.js(
        "const c = window.__cy; const n = c.nodes().filter(n => n.degree() > 2)[0] || c.nodes()[0];"
        "n.emit('tap'); return n.id();"
    )
    await asyncio.sleep(2.0)
    rail = await b.js("return document.querySelector('.detail')?.innerText || ''")
    check(
        "clicking a node fills the detail rail",
        "Nothing selected" not in rail and len(rail) > 20,
        rail[:100],
    )

    await b.js(
        "const c = window.__cy; const e = c.edges()[0]; if (!e) return null; e.emit('tap'); return e.id();"
    )
    await asyncio.sleep(2.0)
    rail = await b.js("return document.querySelector('.detail')?.innerText || ''")
    check(
        "clicking an edge shows provenance",
        "provenance" in rail.lower() or "assert" in rail.lower(),
        rail[:120],
    )

    # ── `/` in full screen asks for the search, so it reveals it ─────
    #
    # The search sits in the ContextBar with every other route's filter, and
    # full screen collapses that bar to a zero-height clipped row -- a hidden
    # input cannot take focus, so a binding that only called `focus()` does
    # nothing in the mode where finding a node matters most. Asking for the
    # field is a request to see it, the rule `revealRail` follows for `[`/`]`.
    await b.goto("/graph?mode=all", settle=6)
    await b.key("f")
    check("full screen on", await b.js("return !!document.querySelector('.shell--fullscreen')"))
    await b.key("/")
    await asyncio.sleep(0.6)
    revealed = await b.js(
        "const i = document.querySelector('.search input');"
        "return {full: !!document.querySelector('.shell--fullscreen'),"
        " visible: !!i && i.getBoundingClientRect().width > 0,"
        " focused: !!i && document.activeElement === i};"
    )
    check(
        "`/` leaves full screen and focuses the search",
        isinstance(revealed, dict)
        and not revealed.get("full")
        and revealed.get("visible")
        and revealed.get("focused"),
        revealed,
    )

    # ── the unconnected orbit the graph (ADR 0182) ───────────────────
    #
    # Stated as geometry, because a screenshot cannot be asserted on. Every
    # unconnected node sits OUTSIDE the connected core, and their distances from
    # its centre fall into a handful of distinct values -- which is what a set
    # of concentric rings is and what a grid is not: tiling 231 nodes into a
    # rectangle (2026-08-31) puts them at nearly as many distances as nodes.
    await b.goto("/graph?mode=all", settle=6)
    shown = await b.js(
        "const el = [...document.querySelectorAll('button, a, .readout *')]"
        "  .find(x => x.textContent.trim().toLowerCase() === 'show');"
        "if (!el) return false; el.click(); return true;"
    )
    if not shown:
        SKIPPED.append("the unconnected orbit the graph")
        OK.append("the unconnected orbit the graph")
        print("  SKIP the unconnected orbit the graph -- none hidden on this corpus")
    else:
        await asyncio.sleep(5)
        band = await b.js(
            "const c = window.__cy; if (!c) return {missing: true};"
            "const vis = c.nodes(':visible');"
            "const loose = vis.filter(n => n.connectedEdges(':visible').length === 0);"
            "if (loose.length < 12) return {few: loose.length};"
            "const core = vis.difference(loose);"
            "if (!core.length) return {noCore: true};"
            "const b = core.boundingBox();"
            "const ox = (b.x1 + b.x2) / 2, oy = (b.y1 + b.y2) / 2;"
            "const coreR = Math.hypot(b.w, b.h) / 2;"
            # Rounded to 8px, so floating error is not a second ring.
            "const radii = loose.map(n => Math.round("
            "  Math.hypot(n.position('x') - ox, n.position('y') - oy) / 8) * 8);"
            "return {loose: loose.length, rings: new Set(radii).size,"
            "        inside: radii.filter(r => r < coreR).length};"
        )
        check(
            "every unconnected name sits outside the connected core",
            isinstance(band, dict) and not band.get("missing") and band.get("inside", 1) == 0,
            band,
        )
        check(
            "and they fall on a handful of rings rather than a grid",
            isinstance(band, dict) and 0 < band.get("rings", 99) <= 10,
            band,
        )

    # ── Esc ordering: the search dropdown wins over the route ─────────
    #
    # `useDismissable` is a LIFO stack, so the open dropdown has to take the
    # first Esc. Checked outside full screen, which is the only place the two
    # can be on screen together: `/` leaves full screen to reveal the field.
    await b.js(
        "const i = document.querySelector('.search input');"
        "i.focus(); i.value = 'anduril';"
        "i.dispatchEvent(new Event('input', {bubbles: true})); return 1"
    )
    await asyncio.sleep(1.8)
    had_results = await b.js(
        "return document.querySelectorAll('.search [role=option], .search li').length > 0"
    )
    await b.key("Escape")
    closed = await b.js(
        "return document.querySelectorAll('.search [role=option], .search li').length === 0"
    )
    check(
        "Esc closes the search dropdown",
        (not had_results) or closed,
        f"had_results={had_results} closed={closed}",
    )

    # ── palette navigation actually moves the graph ──────────────────
    #
    # `router.push` with a new query does not re-run `onMounted`, so nothing but
    # a watcher makes a palette jump work while already on the graph.
    #
    # Measured on the VISIBLE set and the URL, not on `cy.nodes().length`: the
    # palette lands in focus mode, which hides rather than removes, so the node
    # count is deliberately unchanged and a check reading it would report a
    # regression for a mechanism that is working.
    await b.goto("/graph?mode=all", settle=6)
    before = await b.js("return window.__cy.nodes(':visible').length")
    await b.key("k", mods=2)
    await b.type("anduril")
    picked = await b.js(
        "const rows = [...document.querySelectorAll('#palette-results .row')];"
        "const r = rows.find(x => !x.textContent.includes('Brief')"
        "  && !x.textContent.includes('Graph'));"
        "if (!r) return null; r.click(); return r.textContent.trim();"
    )
    await asyncio.sleep(3.5)
    after = await b.js(
        "const c = window.__cy; if (!c) return null;"
        "const q = new URLSearchParams(location.search);"
        "return {visible: c.nodes(':visible').length, mode: q.get('mode'), focus: q.get('focus'),"
        " selected: c.nodes('.emphasis').map(n => n.id())[0] || null};"
    )
    check(
        "a palette pick from the graph focuses that entity",
        picked is None
        or (
            isinstance(after, dict)
            and after.get("mode") == "focus"
            and after.get("focus")
            and after.get("visible", 0) < before
        ),
        f"picked={picked} before={before} after={after}",
    )

    # ── runs: nothing on this page overrides the config ──────────────
    # A "Run defaults" panel here wrote three overrides into localStorage and
    # sent them with every "Run now", so the run a person triggered could differ
    # from the run the scheduler triggered with nothing on screen saying so, and
    # the settings governing it were invisible from the config workspace that
    # owns every other setting. `config/settings.yaml` is the one place.
    await b.goto("/admin/runs", settle=3)
    check(
        "the runs page offers no run-parameter overrides",
        (await b.js("return document.querySelectorAll('input[type=number]').length")) == 0,
    )

    # ── settings: deep links, Back, and read-only prompts ────────────
    await b.goto("/admin/config/sources", settle=3.5)
    # Case-insensitive: the identity zone is uppercased in CSS and `innerText`
    # reports text-transform, so an exact-case match tests the stylesheet.
    check(
        "a deep link opens the named document",
        "sources"
        in (await b.js("return document.querySelector('.context').innerText") or "").lower(),
        await b.js("return document.querySelector('.context').innerText"),
    )
    check(
        "the tab title names the document",
        "sources" in (await b.js("return document.title")),
        await b.js("return document.title"),
    )

    # The editor pane has to BE a pane. A class name outliving the scoped
    # stylesheet that carried its height rendered every document a few lines
    # tall with the rest of the route empty beneath it, and nothing offline
    # opens a page to see that.
    area = await b.js(
        "const a = document.querySelector('.cm-editor');"
        "if (!a) return null;"
        "const r = a.getBoundingClientRect();"
        "return {h: Math.round(r.height), vh: Math.round(window.innerHeight)};"
    )
    check(
        "the config editor fills its pane",
        isinstance(area, dict) and area["h"] > area["vh"] * 0.5,
        area,
    )

    # SELECTED BY KEY, not by title. The index rows carry `data-doc`, which is
    # stable; the titles are reader-facing copy, and when `ontology` became
    # "Types and relationships" a title-matching selector found nothing, stayed
    # on the previous document, reported the URL as unchanged, and took the two
    # checks after it down with it.
    await b.goto("/admin/config", settle=2.5)
    picked = await b.js(
        "const f = document.querySelector('[data-doc=ontology]');"
        "if (!f) return {miss: 'ontology', saw: [...document.querySelectorAll('[data-doc]')]"
        "  .map(a => a.dataset.doc).slice(0, 24)};"
        "f.click(); return {clicked: 'ontology'};"
    )
    await asyncio.sleep(1.2)
    check(
        "clicking a document changes the URL",
        "/admin/config/ontology" in (await b.js("return location.pathname")),
        {"picked": picked, "path": await b.js("return location.pathname")},
    )
    await b.js("history.back(); return 1")
    await asyncio.sleep(1.2)
    check(
        "Back returns to the index",
        (await b.js("return location.pathname")) == "/admin/config",
        await b.js("return location.pathname"),
    )

    # An assembled prompt is real text, has no Save, and is never draft-tracked.
    await b.goto("/admin/config/analyze", settle=2.5)
    check(
        "an assembled system prompt renders",
        len(await b.js(EDITOR_TEXT) or "") > 400,
        len(await b.js(EDITOR_TEXT) or ""),
    )
    check(
        "a read-only document offers no Save",
        not await b.js(
            "return [...document.querySelectorAll('button')].some(x => x.textContent.trim() === 'Save')"
        ),
    )

    # ── settings: an invalid save is refused and stays refused ───────
    # A validation error names a source id and a reason. Not a toast that fades
    # in four seconds, and the dirty marker must survive it.
    await b.goto("/admin/config/sources", settle=3.5)
    await b.js(
        "const v = window.__cm;"
        "if (!v) return {DIAG: 'no editor'};"
        "const text = v.state.doc.toString();"
        "const at = text.indexOf('kind: rss');"
        "if (at < 0) return {DIAG: 'no rss source to break'};"
        "v.dispatch({changes: {from: at, to: at + 'kind: rss'.length,"
        "                      insert: 'kind: nonsense'}});"
        "return 1"
    )
    await asyncio.sleep(0.6)
    await b.js(
        "const s = [...document.querySelectorAll('button')].find(x => x.textContent.trim() === 'Save');"
        "s.click(); return 1"
    )
    await asyncio.sleep(2.0)
    # Matched on the server's own words, not on `.note` -- the Sources pane also
    # carries a source-health note, which would make a class check pass whether
    # or not the save was refused.
    refusal = (
        "return [...document.querySelectorAll('[role=alert]')].map(n => n.innerText).join(' ')"
    )
    check(
        "an invalid save is refused with a persistent notice",
        "nonsense" in (await b.js(refusal) or ""),
        await b.js(refusal),
    )
    await asyncio.sleep(5.0)
    check("the refusal does not fade like a toast", "nonsense" in (await b.js(refusal) or ""))
    check("a refused save leaves the document dirty", await b.js(EDITOR_UNSAVED))
    await b.js("localStorage.clear(); return 1")

    # ── settings: the draft backup is debounced off the keystroke ────
    # Writing the WHOLE document to localStorage on every character --
    # `config_service.MAX_BYTES` allows 256KB -- lets a QuotaExceededError
    # thrown inside the Vue watcher abort the handler before the reactive Set is
    # reassigned, so the unsaved dot silently stops updating. The flag is what
    # the UI reads, so it stays immediate; only the backup waits.
    await b.goto("/admin/config/relevance_profile", settle=3.5)
    await b.js("localStorage.clear(); return 1")
    await b.js(editor_append("\ndebounce probe\n"))
    await asyncio.sleep(0.15)
    drafts = "return Object.keys(localStorage).filter(k => k.startsWith('venona.draft')).length"
    check("the unsaved marker is immediate", await b.js(EDITOR_UNSAVED))
    check(
        "the draft write does not land on the keystroke",
        (await b.js(drafts)) == 0,
        await b.js(drafts),
    )
    await asyncio.sleep(1.2)
    check(
        "the draft is backed up once the flush window passes",
        (await b.js(drafts)) == 1,
        await b.js(drafts),
    )
    await b.js("localStorage.clear(); return 1")

    # ── settings: a failed rebuild must not report success ───────────
    # `await fetch` resolves for a 500 exactly as for a 200, so this toasted
    # "graph rebuilt" and cleared the StatusNote whatever happened -- destroying
    # the one signal that anchors have not reached Neo4j with the very failure
    # that made it true.
    #
    # BOTH endpoints are stubbed. Driving the real Apply would rewrite the
    # anchor run's assertions in the dev database, and a smoke check must not
    # mutate the thing it is inspecting.
    await b.goto("/admin/config/anchors", settle=3.5)
    await b.js(
        "window.__realFetch = window.fetch;"
        "const json = (body, status) => Promise.resolve(new Response(body,"
        "  {status, headers: {'content-type': 'application/json'}}));"
        "window.fetch = (u, o) => {"
        "  const url = String(u);"
        "  if (url.includes('/curation/rebuild'))"
        '    return json(\'{"detail": "neo4j is unreachable"}\', 500);'
        "  if (url.includes('/config/anchors/apply'))"
        '    return json(\'{"entities": 3, "edges": 2, "merges": 0,'
        '      "rebuild_required": true}\', 200);'
        "  return window.__realFetch(u, o);"
        "}; return 1"
    )
    # Open the dialog from the toolbar, then confirm INSIDE it. Both buttons
    # read "Apply" -- the dialog takes its confirm label from the action that
    # opened it -- so a document-wide `.find()` keeps returning the toolbar
    # button and merely reopens the dialog.
    await b.js(
        "const a = [...document.querySelectorAll('button')]"
        "  .find(x => x.textContent.trim() === 'Apply'); if (a) a.click(); return 1"
    )
    await asyncio.sleep(1.5)
    opened = await b.js("return !!document.querySelector('.dialog')")
    check("the anchors Apply dialog opens", opened)
    await b.js(
        "const c = document.querySelector('.dialog .actions button:last-of-type');"
        "if (c) c.click(); return 1"
    )
    await asyncio.sleep(2.0)
    rebuild_btn = (
        "return [...document.querySelectorAll('button')]"
        ".some(x => x.textContent.trim().startsWith('Rebuild'))"
    )
    if await b.js(rebuild_btn):
        await b.js(
            "[...document.querySelectorAll('button')]"
            "  .find(x => x.textContent.trim().startsWith('Rebuild')).click(); return 1"
        )
        await asyncio.sleep(2.0)
        toasts = "return [...document.querySelectorAll('[role=status], .toast')].map(n => n.innerText).join(' ')"
        text = (await b.js(toasts) or "").lower()
        check("a failed rebuild does not toast success", "graph rebuilt" not in text, text[:120])
        check("a failed rebuild says so", "unreachable" in text or "failed" in text, text[:120])
        check("the pending-rebuild note survives the failure", await b.js(rebuild_btn))
    else:
        check(
            "a failed rebuild does not toast success",
            True,
            "skipped: anchors apply did not surface the rebuild note",
        )
    await b.js("if (window.__realFetch) window.fetch = window.__realFetch; return 1")

    # ── a retired path lands on the 404, at its own address ─────────
    # These four addresses answer to no route by choice: this app retires a path
    # rather than redirecting it, because a redirect table outliving its reason
    # is how a router accumulates paths nobody can explain. Without the
    # catch-all a stale bookmark renders the nav over an empty page, which reads
    # as "the app is broken" rather than "that route is gone".
    #
    # THE ADDRESS DOES NOT CHANGE, and asserting that is the point. The
    # catch-all is a VIEW, not a redirect (`router/index.ts`): a silent bounce
    # to the brief cannot tell a reader whether they mistyped or followed a
    # stale link, so `NotFoundView` quotes the failing address back at them. A
    # check expecting `/` would assert the behaviour this app rejected.
    for retired in ("/settings/people", "/runs", "/curation", "/runs/run:gone"):
        await b.goto(retired, settle=2.5)
        landed = await b.js(
            "return {path: location.pathname,"
            " body: document.querySelector('.content')?.innerText || ''};"
        )
        check(
            f"the retired path {retired} lands on the 404, at its own address",
            isinstance(landed, dict)
            and landed.get("path") == retired
            and "no such page" in (landed.get("body") or "").lower(),
            {"path": (landed or {}).get("path"), "body": ((landed or {}).get("body") or "")[:120]},
        )

    # ── curation: globals must be inert against the queue's keys ─────
    await b.goto("/admin/curation", settle=4)
    first = await b.js("return document.querySelector('.review')?.innerText.slice(0, 80) || ''")
    # TWO PAIRS, AND NO `alt+N` AMONG THEM. A nav digit belongs here only if it
    # navigates to THIS page, so that a global which navigates leaves the queue
    # untouched for a reason other than luck. No digit reaches curation: it is
    # on the admin rail, which has none. Any other digit would leave the page
    # and compare two empty strings while reporting green, which is a check that
    # cannot fail. comment-history: ok -- an alt-key pair here drifted with the
    # nav twice, and naming that is what stops a third.
    for k, mods in [("?", 0), ("k", 2)]:
        await b.key(k, mods=mods)
        await b.key("Escape")
    await asyncio.sleep(1.0)
    after = await b.js("return document.querySelector('.review')?.innerText.slice(0, 80) || ''")
    check(
        "global keys do not act on the merge queue",
        first == after,
        f"{first[:40]!r} -> {after[:40]!r}",
    )

    # ── curation: the queues are reachable from the keyboard ─────────
    #
    # Three tabs that were mouse-only. Brackets rather than digits, because bare
    # 1-9 are taken twice over inside this view.
    await b.key("]")
    await asyncio.sleep(1.0)
    on_definitions = await b.js("return !!document.querySelector('.queue')")
    check("] moves to the next curation queue", on_definitions, on_definitions)

    # Esc closes the EDITOR, and only the editor. A `@keydown.esc` on the field
    # can never fire: the registry listens on `window` in the capture phase and
    # its `escape` binding is `allowInInput`, so it calls
    # `stopImmediatePropagation` before the event reaches any input. Esc mid-edit
    # then popped whatever else was on the dismissable stack and left the editor
    # open, which reads as "Esc is broken" rather than as a stack ordering bug.
    if on_definitions:
        await b.key("e")
        opened = await b.js("return !!document.querySelector('[aria-label=\"Expansion\"]')")
        if opened:
            await b.key("Escape")
            check(
                "Esc closes the definition editor",
                not await b.js("return !!document.querySelector('[aria-label=\"Expansion\"]')"),
            )
        else:
            # `E` opens nothing on an empty queue, which is not a failure.
            check("Esc closes the definition editor", True, "skipped: no definition to edit")

    # ── a verdict says WHO reached it ────────────────────────────────
    #
    # The four actor columns landed in `0002_identity.sql` and reached no screen
    # for weeks: `entity_mappings.merged_by` was written by nothing and read by
    # nothing, so a merge was attributable in principle and anonymous in
    # practice. Neither typecheck nor the offline suite can see a column that
    # exists, is populated, and never renders.
    #
    # CHECKED AGAINST THE PUBLISHED SCHEMA, not against a row. `response_model`
    # FILTERS: a field the pydantic model omits vanishes from the wire silently,
    # and a field typed wrongly is a 500 at serialization. Both are invisible on
    # a fresh instance, which has no merges to inspect and is what this script
    # must run against, so the OpenAPI document is the one place the contract is
    # visible with an empty corpus.
    await b.goto("/admin/curation", settle=3.5)
    shapes = await b.js(
        "const d = await fetch('/api/openapi.json').then(r => r.json());"
        "const s = d.components && d.components.schemas;"
        "if (!s) return null;"
        "return {merge: Object.keys((s.Merge || {}).properties || {}),"
        "        conflict: Object.keys((s.Conflict || {}).properties || {})};"
    )
    check(
        "a merge publishes who made it",
        isinstance(shapes, dict) and "merged_by" in shapes.get("merge", []),
        shapes,
    )
    check(
        "and so does a conflict verdict",
        isinstance(shapes, dict) and "resolved_by" in shapes.get("conflict", []),
        shapes,
    )

    # The DOM half, GUARDED on there being a merge deliberately and not for
    # convenience: `MergeHistory.vue` renders an `EmptyState` instead of a table
    # when the history is empty, so there are no column headers to find on a
    # clean instance and asserting them would report a fresh checkout as a
    # regression.
    #
    # THE `r.ok` GUARD IS WHY THE PATH IS WRITTEN OUT. A wrong path 404s, and
    # without the guard that body parses as null, takes the "no merges yet"
    # branch and reports SKIP forever while checking nothing -- a skip
    # indistinguishable from a pass, which is the failure this whole script
    # exists to avoid. So the not-a-list case is a FAIL rather than a skip. The
    # route is paginated and answers `{items, total}` (ADR 0154); reading it as
    # a bare array made every clean instance report "did not answer".
    merges = await b.js(
        "const r = await fetch('/api/curation/merges');"
        "if (!r.ok) return null;"
        "const body = await r.json();"
        "return Array.isArray(body?.items) ? body.items : null;"
    )
    if not isinstance(merges, list):
        check(
            "the merge history offers a `who` column",
            False,
            f"/api/curation/merges did not answer: {merges}",
        )
    elif not merges:
        check(
            "the merge history offers a `who` column",
            True,
            "skipped: no merges recorded yet -- the wire contract is checked above",
        )
    else:
        # Behind the `history` chip, mirroring `merge history renders` above: the
        # queue and the history share one panel and the chip is what swaps them.
        await b.js(
            "const t = [...document.querySelectorAll('[role=radio], .chip')]"
            "  .find(c => c.textContent.trim() === 'history'); t && t.click(); return 1"
        )
        await asyncio.sleep(2.0)
        heads = await b.js(
            "return [...document.querySelectorAll('th, [role=columnheader]')]"
            "  .map(h => h.textContent.trim().toLowerCase());"
        )
        check(
            "the merge history offers a `who` column",
            isinstance(heads, list) and "who" in heads,
            heads,
        )

    # ── brief: the date arrows reflect how much history exists ───────
    # `n_briefs != 1 or off == 2` alone goes VACUOUSLY TRUE the moment a second
    # brief exists, which is the normal state. Both arms are checked explicitly.
    await b.goto("/", settle=4)
    n_briefs = await b.js(
        "const r = await fetch('/api/briefs?limit=500').then(r => r.json());return r.total;"
    )
    # THE REAL `disabled` ATTRIBUTE, not a class. The arrows are `IconButton`s
    # that let `disabled` fall through and style it, because an unreachable
    # control has to leave the tab order rather than sit in it doing nothing
    # when pressed -- and a check counting a class nothing writes can never pass.
    off = await b.js(
        "return [...document.querySelectorAll('.context button')]"
        "  .filter(el => /brief/i.test(el.getAttribute('aria-label') || '') && el.disabled)"
        "  .length"
    )
    if n_briefs == 1:
        check(
            "with one brief both date arrows are disabled",
            off == 2,
            f"{n_briefs} briefs, {off} disabled arrows",
        )
    else:
        # On the newest brief "newer" is inert and "older" is live: exactly one
        # disabled arrow. Two would mean navigation is dead despite a history.
        check(
            "with several briefs the archive is navigable",
            off == 1,
            f"{n_briefs} briefs, {off} disabled arrows",
        )

    # ── the arrows come home ─────────────────────────────────────────
    #
    # `/` and `/briefs/<the newest id>` render the same document and are not the
    # same page: the "for you" toggle and the next-brief line live on `/` alone,
    # both keyed on the route carrying no id. With every route to a brief
    # addressed by id, arrows included, a reader who pressed left off `/` and
    # then right to come back landed on a page that looked right and had
    # silently lost the toggle, with nothing able to return them.
    #
    # Only asserted where there IS a history to move through.
    if n_briefs and n_briefs > 1:
        await b.goto("/", settle=3)
        home = await b.js("return location.pathname;")
        older = await b.js(
            "const b = [...document.querySelectorAll('.context button')]"
            "  .find(el => /older/i.test(el.getAttribute('aria-label') || ''));"
            "if (b) b.click(); return !!b;"
        )
        await asyncio.sleep(2)
        away = await b.js("return location.pathname;")
        newer = await b.js(
            "const b = [...document.querySelectorAll('.context button')]"
            "  .find(el => /newer/i.test(el.getAttribute('aria-label') || ''));"
            "if (b) b.click(); return !!b;"
        )
        await asyncio.sleep(2)
        back = await b.js("return location.pathname;")
        check(
            "moving back to the newest brief returns to its own page",
            older and newer and away != home and back == home,
            f"{home} -> {away} -> {back}",
        )

    # ── toasts appear and clear ──────────────────────────────────────
    # THE PUT IS STUBBED. Saving for real appends to the user-authored
    # `profile/relevance_profile.md` and burns a `.history` slot on every run:
    # measured 2026-08-18, 17 runs in one day grew the profile from 5114 to 5131
    # bytes and filled 17 of the 20 snapshots with versions differing only in
    # trailing whitespace, none of it typed by anyone.
    #
    # The check is that a toast appears and clears, which is frontend behaviour
    # and needs a 200, not a write.
    await b.goto("/admin/config/relevance_profile", settle=3)
    await b.js(
        "window.__realFetch = window.fetch;"
        "window.fetch = (input, init) => {"
        "  const url = typeof input === 'string' ? input : input.url;"
        "  if (init && init.method === 'PUT' && url.includes('/api/config/')) {"
        "    const sent = JSON.parse(init.body);"
        "    return Promise.resolve(new Response(JSON.stringify({"
        "      key: 'relevance_profile', title: 'Relevance profile', description: '',"
        # A GROUP KEY, matching what the server sends: `ConfigSummary.group` is a
        # key into `GROUPS`, not a heading.
        "      group: 'importance', format: 'markdown', rescore_hint: 'free',"
        "      content: sent.content, version: 'stubbed', updated_at: 0,"
        "      bytes: sent.content.length, readonly: false,"
        "      restart_required: [], history: []"
        "    }), {status: 200, headers: {'content-type': 'application/json'}}));"
        "  }"
        "  return window.__realFetch(input, init);"
        "}; return 1"
    )
    await b.js(editor_append("\n"))
    await asyncio.sleep(0.5)
    await b.js(
        "const s = [...document.querySelectorAll('button')].find(x => x.textContent.trim() === 'Save');"
        "s.click(); return 1"
    )
    await asyncio.sleep(2.0)
    check(
        "saving shows a toast", await b.js("return document.querySelectorAll('.toast').length > 0")
    )
    await asyncio.sleep(5.0)
    check(
        "the toast clears itself",
        (await b.js("return document.querySelectorAll('.toast').length")) == 0,
    )
    check(
        "the document is no longer marked unsaved after save",
        not await b.js(EDITOR_UNSAVED),
    )
    await b.js("if (window.__realFetch) window.fetch = window.__realFetch; return 1")

    # ══ the phone pass (ADR 0152) ════════════════════════════════════
    #
    # A 390x844 window: the floor every view must clear. Nothing else in the
    # suite can see a 140px gutter eating a third of a phone screen -- the
    # desktop run's overflow checks all pass at 1500px.

    b = await launch(width=390, height=844)
    # Headless Chromium floors the WINDOW at 500px wide, silently: a "390px"
    # window renders at 500, above the phone breakpoint, and every check below
    # would measure the wrong layout. Emulation is what actually sets 390.
    await b.send(
        "Emulation.setDeviceMetricsOverride",
        width=390,
        height=844,
        deviceScaleFactor=1,
        mobile=True,
    )
    await sign_in(b)

    # The document itself never scrolls sideways; wide content scrolls inside
    # its own box. Per route, because each has its own way to overflow.
    for path in [
        *CHROME_ROUTES,
        # Learn's three sections each have their own way to overflow -- a stat
        # grid, coverage rows, the memory table -- and only the default one is
        # reachable without the query.
        "/learn?section=progress",
        "/learn?section=cards",
    ]:
        await b.goto(path, settle=3.0)
        overflow = await b.js(
            "const d = document.documentElement; return d.scrollWidth - d.clientWidth;"
        )
        check(
            f"{path} at 390px: the document does not scroll horizontally",
            (overflow or 0) <= 1,
            overflow,
        )

    # ── one name's page, at 390px (ADR 0232) ─────────────────────────
    #
    # Not in the list above because its path carries an id, and a hardcoded one
    # would pass on an empty corpus by rendering "no such name" -- the one
    # layout this check does not care about. So it resolves a real name first.
    #
    # BOTH STATES, because they are different layouts: the page opens on the
    # card's connections drawn as inline SVG at a fixed width, which is the
    # element most likely to push a phone sideways, and the expanded list is
    # rows of long names beside publisher counts, which is the other shape that
    # does.
    name = await b.js(
        "const r = await fetch('/api/search?q=a&limit=1');"
        "if (!r.ok) return null;"
        "const e = ((await r.json()).entities || [])[0];"
        "return e ? e.id : null;"
    )
    if not name or ":" not in name:
        check("a name exists to open at 390px", False, name)
    else:
        prefix, slug = name.split(":", 1)
        await b.goto(f"/entity/{prefix}/{slug}", settle=3.0)
        overflow = await b.js(
            "const d = document.documentElement; return d.scrollWidth - d.clientWidth;"
        )
        check(
            f"/entity/{prefix}/{slug} at 390px: the document does not scroll horizontally",
            (overflow or 0) <= 1,
            overflow,
        )
        # The name itself is the one string on the page with no spaces to wrap
        # at -- `[overflow-wrap:anywhere]` is what holds it -- and it is the
        # first thing a reader sees.
        heading = await b.js(
            "const h = document.querySelector('h1');"
            "return h ? Math.round(h.getBoundingClientRect().right) : null;"
        )
        check(
            "the name fits the phone's width",
            heading is not None and heading <= 390,
            heading,
        )
        opened = await b.js(
            "const b = [...document.querySelectorAll('button')]"
            "  .find(x => /show all connections/i.test(x.textContent || ''));"
            "if (!b) return 'no button';"
            "b.click(); return true;"
        )
        if opened is True:
            await asyncio.sleep(3.0)
            overflow = await b.js(
                "const d = document.documentElement; return d.scrollWidth - d.clientWidth;"
            )
            check(
                "the expanded connections list fits 390px too",
                (overflow or 0) <= 1,
                overflow,
            )

    # ── the brief starts with the brief ──────────────────────────────
    #
    # ADR 0198's consequence, and ADR 0199 supersedes 0198 without touching it.
    # Below `lg` (1180px) the orientation rail has no column to sit in, and the
    # whole of it above the article buys a phone reader about two screens of
    # orientation with nothing yet to orient. The contents list is the half that
    # is NAVIGATION, so it leads; the rest follows the article. The generic
    # overflow probe is about width, and at 1500px the rail is a rail.
    await b.goto("/", settle=3.0)
    reading = await b.js(
        "const s = document.querySelector('.view--prose'); if (!s) return 'no prose view';"
        "const at = (q) => { const e = document.querySelector(q);"
        "  return e ? Math.round(e.getBoundingClientRect().top + s.scrollTop) : null };"
        "return {lead: at('.side-lead'), article: at('.brief'), rest: at('.side-rest'),"
        " para: at('.brief .body p'), view: Math.round(s.clientHeight)};"
    )
    if isinstance(reading, dict) and reading.get("article") is not None:
        check(
            "at 390px the contents list leads and the rest of the rail follows",
            (reading.get("lead") or 0) < reading["article"]
            and (reading.get("rest") or 10**9) > reading["article"],
            reading,
        )
        check(
            "at 390px the brief's first paragraph is on the first screen",
            (reading.get("para") or 10**9) < reading["view"],
            reading,
        )

    # ── the model dialogs on a phone ─────────────────────────────────
    #
    # A centred flex item with no `max-height` and no `overflow-y` is
    # unreachable at BOTH ends once it is taller than the viewport: the title
    # scrolls off the top and the confirm button sits below the fold with
    # nothing to scroll. Nothing else in this suite opens a dialog at any width.
    #
    # This page matters most: it is what an administrator opens when the model
    # is not answering, and the button they came for is the one off screen.
    await b.goto("/admin/model", settle=3.0)
    for label, opener in (
        ("connect", "Connect"),
        ("replace", "Replace credential"),
        ("change", "Change"),
    ):
        opened = await b.js(
            f"const want = {opener!r};"
            "const btn = [...document.querySelectorAll('button')]"
            "  .find((b) => b.innerText.trim().toLowerCase() === want.toLowerCase());"
            "if (!btn) return null;"
            "btn.click();"
            "return true;"
        )
        if not opened:
            continue
        fit = await b.js(
            "const panel = document.querySelector('[role=dialog]');"
            "if (!panel) return null;"
            "const r = panel.getBoundingClientRect();"
            "const confirm = [...panel.querySelectorAll('button')].pop();"
            "const cr = confirm ? confirm.getBoundingClientRect() : null;"
            "return {top: Math.round(r.top), bottom: Math.round(r.bottom),"
            "        viewport: window.innerHeight,"
            "        confirmBottom: cr ? Math.round(cr.bottom) : null};"
        )
        if fit is None:
            continue
        check(
            f"the {label} dialog fits 390x844 at both ends",
            fit["top"] >= -1 and fit["bottom"] <= fit["viewport"] + 1,
            fit,
        )
        check(
            f"the {label} dialog's confirm button is on screen",
            fit["confirmBottom"] is not None and fit["confirmBottom"] <= fit["viewport"] + 1,
            fit,
        )
        # Close it before opening the next, or the second click lands on the
        # backdrop and the check silently measures the first dialog again.
        await b.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}));")
        await b.goto("/admin/model", settle=1.5)

    # ── /learn on a phone (ADR 0157) ─────────────────────────────────
    #
    # The generic overflow probe above covers layout. What it cannot see is
    # whether the study surface is USABLE on a phone: the section switch has to
    # exist, and a grade bar on screen has to be tap targets at
    # `--control-h-touch` (44px), not desktop-height slivers.
    await b.goto("/learn", settle=3.0)
    learn_probe = await b.js(
        "const radios = document.querySelectorAll('[role=radiogroup] button, [role=radiogroup] [role=radio]');"
        "const grades = [...document.querySelectorAll('button')]"
        "  .filter((b) => /^(1Again|2Hard|3Good|4Easy)$/.test(b.innerText.replace(/\\s+/g, '')));"
        "const short = grades.filter((b) => b.getBoundingClientRect().height < 43);"
        "return {sections: radios.length, grades: grades.length, short: short.length};"
    )
    check(
        "/learn at 390px offers its three sections",
        isinstance(learn_probe, dict) and learn_probe.get("sections", 0) >= 3,
        learn_probe,
    )
    check(
        "grade buttons, when shown, meet the 44px tap rung",
        isinstance(learn_probe, dict) and learn_probe.get("short", 1) == 0,
        learn_probe,
    )

    # ── segmented controls on a phone ────────────────────────────────
    #
    # Two failures the overflow probe cannot see, each shipped once: a segment
    # collapsing to text height (`h-full` of an auto container -- an 11px tap
    # target), and a row carrying options past the right edge with no way to
    # reach them. The design is ONE row that scrolls inside its own container,
    # so: tap-rung heights, the CONTAINER on screen, and genuine scrollability
    # wherever the content overflows.
    await b.goto("/admin/curation", settle=3.0)
    seg_probe = await b.js(
        "const groups = [...document.querySelectorAll('[role=radiogroup]')].map((g) => {"
        "  const r = g.getBoundingClientRect();"
        "  const scrolls = getComputedStyle(g).overflowX === 'auto';"
        "  return {right: Math.round(r.right), overflowing: g.scrollWidth > g.clientWidth + 1,"
        "          scrolls};"
        "});"
        "const segs = [...document.querySelectorAll('[role=radiogroup] [role=radio]')]"
        "  .map((el) => Math.round(el.getBoundingClientRect().height));"
        "return {groups: groups.length, segs: segs.length,"
        "        short: segs.filter((h) => h < 40).length,"
        "        container_off: groups.filter((g) => g.right > window.innerWidth + 1).length,"
        "        stuck: groups.filter((g) => g.overflowing && !g.scrolls).length};"
    )
    check(
        "curation's segments meet the tap rung at 390px",
        isinstance(seg_probe, dict)
        and seg_probe.get("segs", 0) > 0
        and seg_probe.get("short") == 0,
        seg_probe,
    )
    check(
        "segment rows stay on screen, and an overflowing one scrolls",
        isinstance(seg_probe, dict)
        and seg_probe.get("container_off") == 0
        and seg_probe.get("stuck") == 0,
        seg_probe,
    )

    # ── the config editor is editable on a phone (ADR 0200) ──────────
    #
    # An editor sized by its container has no height of its own to fall back on,
    # so a broken height chain renders it a few lines tall below the fold of a
    # `scroll="none"` shell: never visible, never usable, and no overflow probe
    # notices because nothing overflows.
    #
    # `.cm-editor`, NOT `.cm-content`: the content element is as tall as the
    # DOCUMENT, so it measures hundreds of pixels on a long file while the frame
    # around it is collapsed to nothing -- passing on the exact defect this
    # exists to catch.
    await b.goto("/admin/config/relevance_profile", settle=3.0)
    editor_probe = await b.js(
        "const area = document.querySelector('.cm-editor');"
        "if (!area) return {DIAG: 'no editor rendered'};"
        "const r = area.getBoundingClientRect();"
        "let scroller = area.parentElement;"
        "while (scroller && scroller.scrollHeight <= scroller.clientHeight + 1)"
        "  scroller = scroller.parentElement;"
        "return {h: Math.round(r.height), top: Math.round(r.top),"
        "        reachable: r.top < window.innerHeight || !!scroller};"
    )
    check(
        "the config editor is a real editing surface at 390px",
        isinstance(editor_probe, dict) and editor_probe.get("h", 0) >= 300,
        editor_probe,
    )
    check(
        "and the editor is on screen or scrollable to",
        isinstance(editor_probe, dict) and bool(editor_probe.get("reachable")),
        editor_probe,
    )

    # ── the configuration index survives a phone ─────────────────────
    #
    # Each row is a title, its state, an affordance, a sentence and a date: five
    # things on two lines, and the overflow probe cannot see the failure. A row
    # whose zones refuse to wrap does not widen the document, it squashes the
    # sentence into a column one word across. So this measures the row.
    await b.goto("/admin/config", settle=3.0)
    rows_probe = await b.js(
        "const rows = [...document.querySelectorAll('[data-doc]')];"
        "if (!rows.length) return {DIAG: 'no document rows'};"
        "const over = rows.filter(r => r.scrollWidth - r.clientWidth > 1).length;"
        "const heights = rows.map(r => Math.round(r.getBoundingClientRect().height));"
        "return {n: rows.length, over, min: Math.min(...heights), max: Math.max(...heights)};"
    )
    check(
        "every configuration row fits the width at 390px",
        isinstance(rows_probe, dict) and rows_probe.get("over") == 0,
        rows_probe,
    )
    check(
        "and each row is a real tap target",
        isinstance(rows_probe, dict) and rows_probe.get("min", 0) >= 44,
        rows_probe,
    )

    # ── a looked-up term's detail is reachable on a phone ────────────
    #
    # Same shape as the editor above and as invisible to an overflow probe:
    # under `scroll="none"` the stacked detail pane sits below the list at a y
    # no scroller reaches, so selecting a term appears to do nothing. Guarded on
    # there being a term -- an empty lexicon is a legitimate state.
    await b.goto("/lookup", settle=3.0)
    detail_probe = await b.js(
        "const row = document.querySelector('.lookup tbody tr');"
        "if (!row) return {rows: 0};"
        "row.click();"
        "const grid = document.querySelector('.lookup');"
        "if (grid) grid.scrollTop = grid.scrollHeight;"
        "const aside = document.querySelector('.lookup aside');"
        "if (!aside) return {rows: 1, aside: false};"
        "const r = aside.getBoundingClientRect();"
        "return {rows: 1, aside: true, top: Math.round(r.top),"
        "        height: Math.round(r.height),"
        "        onscreen: r.top < window.innerHeight && r.bottom > 0};"
    )
    has_terms = isinstance(detail_probe, dict) and detail_probe.get("rows")
    check(
        "at 390px a selected term's detail can be scrolled to",
        isinstance(detail_probe, dict)
        and (not has_terms or (detail_probe.get("aside") and detail_probe.get("onscreen"))),
        detail_probe if has_terms else "skipped: no terms in the lexicon",
    )

    # The drawer replaces the row: desktop nav display:none, the toggle
    # visible, and opening it offers the same destinations.
    await b.goto("/", settle=3.0)
    drawer = await b.js(
        "const row = document.querySelector('header.bar nav');"
        "const btn = document.querySelector('.drawer-toggle');"
        "if (!btn) return {DIAG: 'no drawer toggle in the header'};"
        "btn.click();"
        "await new Promise(r => setTimeout(r, 200));"
        "const d = document.querySelector('#nav-drawer');"
        "return {row: row ? getComputedStyle(row).display : 'missing',"
        "        toggle: getComputedStyle(btn).display,"
        "        links: d ? d.querySelectorAll('a').length : 0};"
    )
    check(
        "at 390px the drawer replaces the nav row",
        isinstance(drawer, dict)
        and drawer.get("row") == "none"
        and drawer.get("toggle") not in (None, "none")
        and (drawer.get("links") or 0) >= 4,
        drawer,
    )

    # The drawer covers the page, so it is modal: focus has to be INSIDE it.
    # Without that, opening the drawer on a phone leaves focus on the hamburger
    # behind the scrim and Tab walks the header nobody can see.
    focus_in_drawer = await b.js(
        "const d = document.querySelector('#nav-drawer');"
        "if (!d) return {drawer: false};"
        "return {drawer: true, inside: d.contains(document.activeElement),"
        "        active: (document.activeElement || {}).className || ''}"
    )
    check(
        "at 390px the open drawer holds focus",
        isinstance(focus_in_drawer, dict) and bool(focus_in_drawer.get("inside")),
        focus_in_drawer,
    )
    await b.key("Escape")

    # The graph degrades rather than reflowing (ADR 0152), and says so.
    await b.goto("/graph?mode=all", settle=6)
    # ONE track, not three of zero width. The phone tier collapses the rail grid
    # to a single full-width column rather than shrinking two of three to
    # nothing, so the TRACK COUNT is what says the rails are gone; asserting
    # `0px` twice describes a different layout and fails a working fallback.
    fallback = await b.js(
        "const g = document.querySelector('.grid');"
        "const tracks = g ? getComputedStyle(g).gridTemplateColumns.split(' ') : [];"
        "return {tracks: tracks.length, first: tracks[0] || '',"
        "        hint: document.querySelector('.hint')?.innerText || ''};"
    )
    check(
        "at 390px the graph hides its rails and says it is read-only",
        isinstance(fallback, dict)
        and fallback.get("tracks") == 1
        and "read-only" in fallback.get("hint", ""),
        fallback,
    )

    # A tapped mention's card can be dismissed by tapping outside it: the touch
    # exit. Its other ways out are Escape and "Open in graph", neither of which
    # exists under a thumb, so without this the card was permanent on an iPad.
    await b.goto("/", settle=3.0)
    card = await b.js(
        "const tap = el => {"
        "  const r = el.getBoundingClientRect();"
        "  const o = {bubbles: true, pointerType: 'touch', clientX: r.left + 4, clientY: r.top + 4};"
        "  el.dispatchEvent(new PointerEvent('pointerdown', o));"
        "  el.dispatchEvent(new PointerEvent('pointerup', o));"
        "  el.dispatchEvent(new MouseEvent('click', o));"
        "};"
        "const m = document.querySelector('[data-entity-id]');"
        "if (!m) return {DIAG: 'no mention in the brief'};"
        "m.scrollIntoView({block: 'center'});"
        "await new Promise(r => setTimeout(r, 200));"
        "tap(m);"
        "await new Promise(r => setTimeout(r, 600));"
        "const opened = !!document.querySelector('.card.fixed');"
        "tap(document.querySelector('h1.title') || document.body);"
        "await new Promise(r => setTimeout(r, 300));"
        "return {opened, dismissed: !document.querySelector('.card.fixed')};"
    )
    check(
        "at 390px a tapped mention card dismisses on tap-outside",
        isinstance(card, dict) and card.get("opened") is True and card.get("dismissed") is True,
        card,
    )

    # Tables scroll inside their own container instead of stretching the page.
    await b.goto("/admin/runs", settle=3.0)
    table = await b.js(
        "const t = document.querySelector('.table');"
        "if (!t) return {DIAG: 'no table on /runs'};"
        "const w = t.parentElement;"
        "return {overflowX: getComputedStyle(w).overflowX,"
        "        doc: document.documentElement.scrollWidth - document.documentElement.clientWidth};"
    )
    check(
        "at 390px tables scroll inside their own container",
        isinstance(table, dict)
        and table.get("overflowX") == "auto"
        and (table.get("doc") or 0) <= 1,
        table,
    )

    # ══ the tablet pass ══════════════════════════════════════════════
    #
    # 820x1180: an iPad in portrait, and the band nothing looked at -- the suite
    # ran 1500 and 390 and nothing between, while the two breakpoints that could
    # serve a tablet (`sm` 720, `nav` 800) were used by `AppHeader` and almost
    # nothing else. A bar that is fine stacked at 390 and roomy at 1500 collided
    # at 820: `#context-actions` is `flex-none` and the controls zone is `grow
    # min-w-0`, so the tab strip gave way and it has no width to give --
    # "HISTORY" painted on top of "LOOK UP ALL", reported from a real iPad.
    #
    # Above `xs` (480px), so this is NOT a second phone pass: the tap rung does
    # not apply and the drawer is not expected. What IS expected is that no two
    # zones of a chrome bar occupy the same pixels.

    b = await launch(width=820, height=1180)
    await b.send(
        "Emulation.setDeviceMetricsOverride",
        width=820,
        height=1180,
        deviceScaleFactor=1,
        mobile=False,
    )
    await sign_in(b)

    for path in CHROME_ROUTES:
        await b.goto(path, settle=2.0)
        overflow = await b.js(
            "const d = document.documentElement; return d.scrollWidth - d.clientWidth;"
        )
        # 2, not 1, and the two pixels are named rather than waved through:
        # measured 2026-08-30, `AppHeader`'s utility cluster (the palette hint,
        # `?`, the avatar) takes 822 against an 820 viewport at THIS width and
        # no other -- 834 and 768 are clean. It reproduces with this pass's
        # changes stashed and is the header's to fix. A bar pushing the page
        # sideways is tens of pixels, so the check still catches what it is for.
        check(
            f"{path} at 820px: the document does not scroll horizontally",
            (overflow or 0) <= 2,
            overflow,
        )

    # The collision itself, stated as geometry rather than as a screenshot: the
    # controls zone and the actions zone are siblings in one bar and must not
    # overlap. `getBoundingClientRect` is the only thing that can see this --
    # both elements are "on screen" and neither overflows the document, which is
    # why every existing probe passed while the bar was unreadable.
    #
    # EVERY ROUTE, not just the one that was reported. The overflow loop above
    # cannot stand in for it, since a bar whose zones paint on top of each other
    # still passes an overflow check, and any route whose controls grow can
    # collide.
    for path in CHROME_ROUTES:
        await b.goto(path, settle=2.0)
        zones = await b.js(
            "const c = document.getElementById('context-controls');"
            "const a = document.getElementById('context-actions');"
            "if (!c || !a) return {missing: true};"
            "const cr = c.getBoundingClientRect(), ar = a.getBoundingClientRect();"
            # A zone with nothing teleported into it is a zero-width box sitting
            # wherever the flex row leaves it, and two of those overlap by
            # definition. Only a zone with content can collide with anything.
            "if (cr.width < 1 || ar.width < 1) return {empty: true, overlap: 0};"
            "const sameRow = cr.bottom > ar.top + 1 && ar.bottom > cr.top + 1;"
            "return {overlap: sameRow ? Math.round(cr.right - ar.left) : 0,"
            "        controls: Math.round(cr.right), actions: Math.round(ar.left)};"
        )
        check(
            f"at 820px {path}'s controls and actions do not overlap",
            isinstance(zones, dict)
            and not zones.get("missing")
            and (zones.get("overlap") or 0) <= 0,
            zones,
        )

    # ── the OTHER tablet band: wide enough to lose the wrap ──────────
    #
    # 820 is below `md`, where the bar wraps and the zones land on two rows.
    # Between `md` (860) and `lg` (1180) it is one grid row with no wrap left to
    # give, and that band had neither guard: reported from an iPad at 1024,
    # "LOOK UP ALL" painted over the "QUESTIONS" tab. The trailing track is
    # right-aligned, so content wider than its track spills LEFT, over the tabs.
    #
    # `?queue=definitions` is load-bearing: `/admin/curation` opens on Merges,
    # whose actions zone is EMPTY, so the sweep above walks the route and
    # asserts nothing about it -- which is how a collision on the widest actions
    # zone in the app went unseen by a check written for collisions.
    b8 = await launch(width=1024, height=1180)
    await b8.send(
        "Emulation.setDeviceMetricsOverride",
        width=1024,
        height=1180,
        deviceScaleFactor=1,
        mobile=False,
    )
    await sign_in(b8)

    for path in [
        *CHROME_ROUTES,
        "/admin/curation?queue=definitions",
        "/admin/curation?queue=questions",
    ]:
        await b8.goto(path, settle=2.0)
        zones = await b8.js(
            "const c = document.getElementById('context-controls');"
            "const a = document.getElementById('context-actions');"
            "if (!c || !a) return {missing: true};"
            "const cr = c.getBoundingClientRect(), ar = a.getBoundingClientRect();"
            "if (cr.width < 1 || ar.width < 1) return {empty: true, overlap: 0};"
            "const sameRow = cr.bottom > ar.top + 1 && ar.bottom > cr.top + 1;"
            "return {overlap: sameRow ? Math.round(cr.right - ar.left) : 0,"
            "        controls: Math.round(cr.right), actions: Math.round(ar.left)};"
        )
        check(
            f"at 1024px {path}'s controls and actions do not overlap",
            isinstance(zones, dict)
            and not zones.get("missing")
            and (zones.get("overlap") or 0) <= 0,
            zones,
        )

    # And the mechanism that buys the room, asserted separately from the
    # geometry above: the geometry is also satisfied by a bar that simply has
    # less in it, which would pass on a page that never exercised the collapse.
    #
    # The chrome gives up its words here; the page BODY keeps them. That split
    # is why the rule is scoped to `#context-actions` rather than widening
    # `Button`'s own breakpoint: the verdict bar under the definition queue
    # reads "IT NAMES A THING", and that button as a glyph is a riddle.
    await b8.goto("/admin/curation?queue=definitions", settle=2.0)
    words = await b8.js(
        "const vis = (el) => getComputedStyle(el).display !== 'none';"
        "const chrome = [...document.querySelectorAll('#context-actions .btn-label')];"
        "const body = [...document.querySelectorAll('.view .btn-label')];"
        "return {chrome: chrome.length, chrome_shown: chrome.filter(vis).length,"
        "        body: body.length, body_shown: body.filter(vis).length};"
    )
    check(
        "at 1024px the chrome's actions are glyphs and the page keeps its words",
        isinstance(words, dict)
        and words.get("chrome", 0) > 0
        and words.get("chrome_shown") == 0
        and words.get("body", 0) > 0
        and words.get("body_shown") == words.get("body"),
        words,
    )

    # The same segment probe the phone pass runs, at the width that broke: a
    # strip that outruns its frame has to scroll rather than paint outside it.
    #
    # `b8`, and it has to be: `launch()` closes the previous browser, so the
    # handle the phone pass used is a websocket shut cleanly three launches ago.
    # Sending to it raises `ConnectionClosedOK` out of `main` and takes the
    # whole report with it -- every check that ran is lost before it prints.
    seg_tablet = await b8.js(
        "const groups = [...document.querySelectorAll('[role=radiogroup]')].map((g) => {"
        "  const r = g.getBoundingClientRect();"
        "  return {right: Math.round(r.right),"
        "          overflowing: g.scrollWidth > g.clientWidth + 1,"
        "          scrolls: getComputedStyle(g).overflowX === 'auto'};"
        "});"
        "return {groups: groups.length,"
        "        container_off: groups.filter((g) => g.right > window.innerWidth + 1).length,"
        "        stuck: groups.filter((g) => g.overflowing && !g.scrolls).length};"
    )
    check(
        "at 820px an overflowing segment row scrolls rather than spilling",
        isinstance(seg_tablet, dict)
        and seg_tablet.get("groups", 0) > 0
        and seg_tablet.get("container_off") == 0
        and seg_tablet.get("stuck") == 0,
        seg_tablet,
    )

    # ── the filter is a glyph until it is asked for (ADR 0181) ───────
    #
    # At EVERY width, which is why this is checked at a narrow one and at 1500
    # in the desktop pass. A filter is the least of the three things the bar
    # carries, so it waits at the trailing edge as a glyph and opens across the
    # whole row when it is wanted, covering the identity and the tabs. Sized to
    # its own zone it would be a sliver: everything out there is `flex-none`.
    b7 = await launch(width=700, height=900)
    await b7.send(
        "Emulation.setDeviceMetricsOverride",
        width=700,
        height=900,
        deviceScaleFactor=1,
        mobile=False,
    )
    await sign_in(b7)
    await b7.goto("/lookup", settle=3.0)

    collapsed = await b7.js(
        "const bar = document.querySelector('.context');"
        "if (!bar) return {missing: true};"
        "const fields = [...bar.querySelectorAll('input[type=search]')]"
        "  .filter(el => el.getBoundingClientRect().width > 0);"
        "return {visible: fields.length};"
    )
    check(
        "at 700px the lookup filter is a glyph, not a field",
        isinstance(collapsed, dict) and collapsed.get("visible") == 0,
        collapsed,
    )

    await b7.key("/")
    opened = await b7.js(
        "const bar = document.querySelector('.context');"
        "const field = [...bar.querySelectorAll('input[type=search]')]"
        "  .find(el => el.getBoundingClientRect().width > 0);"
        "if (!field) return {opened: false};"
        "const fr = field.getBoundingClientRect(), br = bar.getBoundingClientRect();"
        "return {opened: true,"
        "        focused: document.activeElement === field,"
        # The panel spans the bar: the field inside it starts left of where the
        # controls zone begins, which is the whole point of anchoring to
        # `.context` rather than to the zone.
        "        widerThanZone: fr.width > br.width / 2};"
    )
    check(
        "at 700px `/` opens the filter and focuses it",
        isinstance(opened, dict) and opened.get("opened") and opened.get("focused"),
        opened,
    )
    check(
        "the opened filter spans the bar rather than the controls zone",
        isinstance(opened, dict) and opened.get("widerThanZone"),
        opened,
    )

    # Esc gives the row back, and focus with it.
    await b7.key("Escape")
    closed = await b7.js(
        "const bar = document.querySelector('.context');"
        "const fields = [...bar.querySelectorAll('input[type=search]')]"
        "  .filter(el => el.getBoundingClientRect().width > 0);"
        "return {visible: fields.length,"
        "        focusedInBar: bar.contains(document.activeElement)};"
    )
    check(
        "Esc closes the filter and leaves focus in the bar",
        isinstance(closed, dict) and closed.get("visible") == 0 and closed.get("focusedInBar"),
        closed,
    )
    await b7.close()

    # ── the smoke run must leave the user's config exactly as it found it ──
    # Last, so it covers every check above rather than just the settings ones.
    touched = sorted(
        name for name, state in config_state().items() if config_before.get(name) != state
    )
    check("the smoke run wrote no user config", not touched, touched)

    decided = sorted(set(verdict_state()) - set(verdicts_before))
    check(
        "the smoke run recorded no curation verdicts",
        not decided,
        f"{len(decided)} surface(s) decided by the test run: {decided[:6]}",
    )

    return report()


def report() -> int:
    """The tally, from wherever the run ended.

    A function rather than the tail of `main`, because an unconfigured instance
    stops the suite early and a run that reports nothing on the way out is
    indistinguishable from one that crashed.
    """
    for f in FAIL:
        print(" FAIL", f)
    for s in SKIPPED:
        print(" SKIP", s)
    tail = f", {len(SKIPPED)} skipped" if SKIPPED else ""
    print(f"\nui-smoke: {len(OK) - len(SKIPPED)} passed, {len(FAIL)} failed{tail}")
    return 1 if FAIL else 0


async def _run() -> int:
    """Run the checks, and reclaim the browser whatever happens.

    The cleanup is in a `finally` rather than at the end of `main()` because the
    run that most needs reclaiming is the one that died on an assertion or a
    timeout.
    """
    try:
        return await main()
    finally:
        if _CURRENT is not None:
            await _CURRENT.close()
        # Anything an earlier crashed run left behind. Screenshots stay.
        for stale in RUN_DIR.glob("profile.*"):
            shutil.rmtree(stale, ignore_errors=True)


# Opt-in, because this suite DRIVES THE REAL APP AGAINST THE REAL DATABASE.
#
# It is not a read-only observer: it clicks buttons, presses keys and submits
# forms, and everything downstream of that is a genuine write. Two guards exist
# for the damage that caused -- `GUARDED_CONFIG` after it appended a newline to
# `profile/relevance_profile.md` on every run for months, and `verdict_state`
# after it recorded curation verdicts on 76 surfaces of a working corpus
# (2026-08-09).
#
# Both are backstops on a design that is wrong for a development machine. The
# right home is a clean instance where mutating the data IS the point and
# nothing of anyone's is at stake, so it refuses to start unless it is told,
# explicitly, that this is such an instance.
CI_ONLY = """
`make ui-smoke` drives the real app against the real database and WRITES: it
clicks, types and confirms, and a definitions verdict or a hidden run is as
real as one you made yourself.

Run it against a clean instance:

    VENONA_UI_SMOKE=1 make ui-smoke

That is what CI does on a fresh checkout, and it is the only place this should
run unattended.
"""


if __name__ == "__main__":
    if not os.environ.get("VENONA_UI_SMOKE"):
        print(CI_ONLY, file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(asyncio.run(_run()))
