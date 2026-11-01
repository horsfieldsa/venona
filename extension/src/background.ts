/** The only place the annotation path calls the network.
 *
 * Two reasons, and either alone would be sufficient:
 *
 *   1. TOKEN ISOLATION. A content script shares a process with a page it does
 *      not trust. It gets answers; it never gets the bearer.
 *   2. CROSS-ORIGIN ACCESS. An MV3 content-script `fetch` is checked with the
 *      PAGE as `Origin`, so routing annotation through one would mean the API
 *      had to allow every site you read. A worker request is made from the
 *      extension's own context, where `host_permissions` grants the access.
 *
 * The second point is measured rather than assumed, and the measurement is why
 * `VENONA_CORS_ORIGINS` is belt-and-braces here rather than a requirement:
 * ADR 0099.
 */

import { PORT_NAME, type Request, type Response, type RunStarted } from './shared/protocol'
import {
  apiGet,
  apiPost,
  authBlockedKey,
  errorText,
  EXT_CHANNEL,
  loadCapability,
  loadInstances,
  loadSettings,
  loadTokenMeta,
  saveCapability,
  setActiveInstance,
  settingsFor,
  originPatternFor,
  UnauthorizedError,
  type Settings,
} from './shared/client'
import type { AnnotateResponse, Card, CardBatchResponse } from './shared/types'
import {
  CATALOG_TTL_MS,
  fetchCatalog,
  loadCatalog,
  loadEnabled,
  loadPaused,
  reconcile,
  routeForHost,
  saveEnabled,
  touchesSites,
} from './shared/sites'
import { TOKENS_DARK, TOKENS_LIGHT } from '../generated/tokens'
import { watchTheme } from './shared/theme'

/** `CardBatchRequest.ids` is `Field(max_length=100)` (api/schemas/entity.py).
 * A long article routinely carries more distinct refs than that, and the 422 it
 * would otherwise return looks like a server fault rather than a client bug. */
const CARD_CHUNK = 100

/** Two POSTs per page against `ratelimit.py`'s WRITE bucket (60/min) is fine
 * for reading; an SPA re-annotating on every route change is not. Cache in
 * `storage.session`, never a module variable -- the worker is killed on idle
 * and a module variable evaporates with it. */
const ANNOTATE_TTL_MS = 60_000

interface CachedAnnotate {
  at: number
  body: AnnotateResponse
}

function cacheKey(settings: Settings, url: string, blocks: { text: string }[]): string {
  // The URL alone is not enough: an infinite-scroll page keeps its URL while
  // its content grows. Length of the concatenated text is a cheap discriminator
  // that changes whenever anything was added. The instance id is in the key
  // because two configured deployments hold two different corpora -- one's
  // cached answer on a page is wrong for the other.
  const size = blocks.reduce((n, b) => n + b.text.length, 0)
  return `annotate:${settings.instanceId}:${url}:${blocks.length}:${size}`
}

/** Bound on resident cached bodies. The key changes whenever the page grows, so
 * an infinite-scroll article mints a new one on every pass and never reuses its
 * predecessor; unswept, full `AnnotateResponse` bodies stay resident for the
 * whole browser session until the quota ends it, which surfaces as a REJECTED
 * `set` on a request that otherwise succeeded. */
const CACHE_MAX_ENTRIES = 40

async function sweepCache(now: number): Promise<void> {
  const all = await chrome.storage.session.get(null)
  const mine = Object.entries(all).filter(([k]) => k.startsWith('annotate:'))
  const dead = mine.filter(([, v]) => now - (v as CachedAnnotate).at >= ANNOTATE_TTL_MS)
  const live = mine.filter(([, v]) => now - (v as CachedAnnotate).at < ANNOTATE_TTL_MS)
  live.sort((a, b) => (a[1] as CachedAnnotate).at - (b[1] as CachedAnnotate).at)
  const surplus = live.slice(0, Math.max(0, live.length - CACHE_MAX_ENTRIES))
  const drop = [...dead, ...surplus].map(([k]) => k)
  if (drop.length) await chrome.storage.session.remove(drop)
}

async function annotate(
  settings: Settings,
  url: string,
  blocks: { id: string; text: string; hrefs: [number, number][] }[],
): Promise<AnnotateResponse> {
  const key = cacheKey(settings, url, blocks)
  const hit = await chrome.storage.session.get(key)
  const cached = hit[key] as CachedAnnotate | undefined
  if (cached && Date.now() - cached.at < ANNOTATE_TTL_MS) return cached.body

  const body = await apiPost<AnnotateResponse>(settings, '/annotate', { url, blocks })
  // The one request every annotated page makes, so it is where a recovered
  // token is noticed soonest.
  void clearSignedOut(settings)
  // Caching is an optimisation, so it may never fail the request that filled
  // it. Awaiting an unguarded `set` turned a quota rejection into a failed
  // annotate and cost the page every underline it had just earned.
  try {
    await sweepCache(Date.now())
    await chrome.storage.session.set({ [key]: { at: Date.now(), body } satisfies CachedAnnotate })
  } catch (err) {
    console.warn('[Venona] annotate cache write failed; continuing', err)
  }
  return body
}

async function cards(
  settings: Settings,
  ids: string[],
): Promise<{ cards: Record<string, Card>; missing: string[] }> {
  const out: Record<string, Card> = {}
  const missing: string[] = []
  for (let i = 0; i < ids.length; i += CARD_CHUNK) {
    const chunk = ids.slice(i, i + CARD_CHUNK)
    // Per-chunk try/catch, matching `stores/entities.ts`: one failed chunk
    // should cost its own cards, not every card on the page.
    try {
      const res = await apiPost<CardBatchResponse>(settings, '/entities/cards:batch', {
        ids: chunk,
      })
      Object.assign(out, res.cards)
      missing.push(...(res.missing ?? []))
    } catch {
      missing.push(...chunk)
    }
  }
  return { cards: out, missing }
}

/** Two endpoints, one button.
 *
 * An entity gets a tier-3 web lookup written against its own id; a glossary
 * surface gets one against the surface, because ambiguous surfaces have one row
 * per reading and the id would pick a reading for the user. Both are C11 LIGHT
 * lane, so neither is refused while a research run holds the research lane --
 * which is exactly when someone is reading and hits a term they do not know.
 */
function lookupPath(entityId: string, surface: string, isEntity: boolean): string {
  return isEntity
    ? `/entities/${encodeURIComponent(entityId)}/define`
    : `/glossary/${encodeURIComponent(surface)}/lookup`
}

/** Which instance handles a tab: the one whose cached site catalog claims the
 * host, when exactly one does; the active instance otherwise.
 *
 * Cached catalogs ONLY -- routing never fetches, because it sits in front of
 * every annotate and a network wait here would gate the page on it. A stale
 * claim beats none; the 6h alarm keeps them honest. Every request kind routes
 * together: cards, lookups and run polls carry ids minted by the instance
 * that annotated, and splitting them across corpora dangles every reference.
 */
async function settingsForTab(tabUrl?: string): Promise<Settings> {
  const all = await loadInstances()
  if (all.instances.length < 2) return loadSettings()
  let host: string | null = null
  try {
    host = tabUrl ? new URL(tabUrl).hostname : null
  } catch {
    host = null
  }
  const catalogs = await Promise.all(
    all.instances.map(async (instance) => ({
      instanceId: instance.id,
      sites: (await loadCatalog(instance.id))?.sites ?? [],
    })),
  )
  return settingsFor(routeForHost(host, catalogs, all.activeId))
}

async function handle(req: Request, tabUrl?: string): Promise<Response> {
  let settings: Settings | null = null
  try {
    // INSIDE the try. `chrome.storage` rejects when the extension context is
    // invalidated -- a reload, an update -- and outside the try that rejection
    // escaped `handle` entirely. The port listener had no `.catch`, so the
    // request was never answered: the content script's promise stayed pending
    // for ever and the card spun until the tab was closed. A hang is the one
    // failure worse than an error, because nothing reports it.
    settings = await settingsForTab(tabUrl)
    switch (req.kind) {
      case 'annotate':
        return {
          id: req.id,
          ok: true,
          kind: 'annotate',
          data: await annotate(settings, req.url, req.blocks),
        }
      case 'cards':
        return { id: req.id, ok: true, kind: 'cards', data: await cards(settings, req.ids) }
      case 'health':
        return {
          id: req.id,
          ok: true,
          kind: 'health',
          data: await apiGet<{ status: string }>(settings, '/health/live'),
        }
      case 'lookup':
        return {
          id: req.id,
          ok: true,
          kind: 'lookup',
          data: await apiPost<RunStarted>(
            settings,
            lookupPath(req.entityId, req.surface, req.isEntity),
          ),
        }
      case 'ingest':
        return {
          id: req.id,
          ok: true,
          kind: 'ingest',
          data: await apiPost<RunStarted>(settings, '/ingest', { url: req.url }),
        }
      case 'run':
        return {
          id: req.id,
          ok: true,
          kind: 'run',
          data: await apiGet<{ status: string; error?: string | null }>(
            settings,
            `/runs/${encodeURIComponent(req.runId)}`,
          ),
        }
      case 'appUrl':
        // The SPA sits beside the API, not under it. In production the reverse proxy serves
        // both from one origin; in development, pointing the base URL at the
        // Vite proxy (`http://localhost:5173/api`) makes this true there too.
        return {
          id: req.id,
          ok: true,
          kind: 'appUrl',
          data: { url: settings.baseUrl.replace(/\/api$/, '') },
        }
      case 'capabilities':
        // Stored fact only, never a probe: this sits in front of every card
        // mount, and a network wait here would gate the hover on it.
        return {
          id: req.id,
          ok: true,
          kind: 'capabilities',
          data: { full: (await loadCapability(settings.instanceId)) !== 'limited' },
        }
    }
  } catch (err) {
    const status = (err as { status?: number }).status
    // A 401 as a bare console.warn leaves a page with no underlines, which is
    // indistinguishable from a page with no entities on it. The badge is the
    // only UI a service worker has, and the card reads this `status` too.
    if (err instanceof UnauthorizedError && settings) void markSignedOut(settings)
    // A 403 on a gated route is the credential saying what it is. Remember,
    // so the next card offers only buttons that work.
    if (status === 403 && settings && (req.kind === 'lookup' || req.kind === 'run')) {
      void saveCapability(settings.instanceId, 'limited')
    }
    return { id: req.id, ok: false, error: errorText(err), status }
  }
}

// ── signed out ──────────────────────────────────────────────────────

// Per instance: one deployment's refused token must not mute the badge -- or
// the annotation path -- for the others. The key itself lives in
// `shared/client.ts` so the options page can clear it after a re-pair.
const blockedKeyFor = (settings: Settings) => authBlockedKey(settings.instanceId)

/** Persistent, unlike the ingest badge's ten-second tick.
 *
 * This state does not resolve on its own -- it needs someone to go and fix the
 * token -- so a badge that cleared itself would be telling the truth for ten
 * seconds and lying afterwards.
 */
async function markSignedOut(settings: Settings): Promise<void> {
  const key = blockedKeyFor(settings)
  const already = await chrome.storage.session.get(key)
  if (already[key]) return
  await chrome.storage.session.set({ [key]: true })
  await badge('!', BAD_COLOUR())
}

async function clearSignedOut(settings: Settings): Promise<void> {
  const key = blockedKeyFor(settings)
  const already = await chrome.storage.session.get(key)
  if (!already[key]) return
  await chrome.storage.session.remove(key)
  await badge('', CLEAR_COLOUR())
}

/** Badge colours, read out of the generated token maps so that restyling the app
 * restyles the badge.
 *
 * `setBadgeBackgroundColor` takes a literal and an MV3 worker has no DOM to
 * resolve `light-dark()` against, so the side comes from the STORED theme choice
 * (`shared/theme.ts`) rather than `prefers-color-scheme`; under System that is
 * the dark side. ADR 0133.
 *
 * Read per call rather than cached at module scope: a service worker is evicted
 * and respawned constantly, so "the value at import time" is a fiction.
 */
/* The union, not one of them. `as const` gives each map its own literal types --
   which is the point, because it makes a stale key a compile error -- so the two
   are unrelated types and a variable typed as one cannot hold the other. */
let palette: typeof TOKENS_DARK | typeof TOKENS_LIGHT = TOKENS_DARK
watchTheme((choice) => {
  palette = choice === 'light' ? TOKENS_LIGHT : TOKENS_DARK
})

const OK_COLOUR = (): string => palette['--accent-muted']
const BAD_COLOUR = (): string => palette['--signal-critical']
const CLEAR_COLOUR = (): string => palette['--surface-base']

/** Paint the toolbar badge. NEVER throws.
 *
 * `setBadgeText({tabId})` REJECTS with "No tab with id: N" once that tab is
 * closed, and a badge is decoration -- the one thing in this file that must not
 * be able to interrupt anything. It could: see `finishBadge`, where this ran
 * first and its rejection skipped the two lines that clear the pending ingest,
 * so closing the tab you ingested from left an alarm polling a dead tab every
 * 30 seconds for the life of the browser, logging as it went. The tab-scoped
 * call is also the ONLY failure mode here, which is why this swallows rather
 * than reporting: there is no action for anyone to take.
 */
async function badge(text: string, colour: string, tabId?: number): Promise<void> {
  try {
    await chrome.action.setBadgeText({ text, tabId })
    await chrome.action.setBadgeBackgroundColor({ color: colour, tabId })
  } catch {
    /* the tab is gone; there is nothing to decorate */
  }
}

/** The 2-letter code of the instance that would handle the current tab.
 *
 * Only drawn with two or more instances configured -- one instance has nothing
 * to disambiguate. `chrome.tabs.get(...).url` is populated exactly where a
 * host permission exists, which is exactly the set of tabs that can annotate;
 * an ungranted tab shows no code, which is honest. No `tabs` permission
 * needed for any of this -- the events fire regardless, only `url` is gated.
 */
async function updateRouteBadge(tabId: number): Promise<void> {
  const all = await loadInstances()
  if (all.instances.length < 2) {
    await badge('', CLEAR_COLOUR(), tabId)
    return
  }
  let url: string | undefined
  try {
    url = (await chrome.tabs.get(tabId)).url
  } catch {
    return // the tab is gone; nothing to decorate
  }
  if (!url || !/^https?:/.test(url)) {
    await badge('', CLEAR_COLOUR(), tabId)
    return
  }
  const settings = await settingsForTab(url)
  const instance = all.instances.find((candidate) => candidate.id === settings.instanceId)
  const code = (instance?.label || instance?.baseUrl || '').trim().slice(0, 2).toUpperCase()
  await badge(code, CLEAR_COLOUR(), tabId)
}

/** What a transient ✓/✕/… yields back to when its ten seconds are up. */
function repaintBadge(tabId?: number): void {
  if (tabId !== undefined) void updateRouteBadge(tabId)
  else void badge('', CLEAR_COLOUR())
}

chrome.tabs.onActivated.addListener(({ tabId }) => void updateRouteBadge(tabId))
chrome.tabs.onUpdated.addListener((tabId, info) => {
  if (info.status === 'complete') void updateRouteBadge(tabId)
})

/** A toolbar ingest in flight, across worker teardowns.
 *
 * The poll CANNOT live in the click handler. MV3 kills an idle service worker
 * after ~30 seconds and an ingest takes tens of seconds to minutes, so a
 * 120-iteration `setTimeout` loop is terminated somewhere in the middle: the
 * badge stays frozen on `…` for ever, and the next click just rewrites it. The
 * card path solves this by polling from the PAGE, whose port keeps the worker
 * alive -- `HoverCard.vue` says so explicitly. The toolbar has no port, so it
 * needs the other mechanism: state in `storage.session`, an alarm to come back.
 */
const PENDING_KEY = 'ingest:pending'
const POLL_ALARM = 'ingest-poll'
/** Alarms below 30s are clamped by Chrome; this is the floor, expressed. */
const POLL_PERIOD_MINUTES = 0.5
/** Long enough for a slow fetch plus one analyze call. */
const POLL_GIVE_UP_MS = 6 * 60 * 1000

interface PendingIngest {
  runId: string
  tabId?: number
  startedAt: number
}

/** Terminal state: STOP FIRST, then paint.
 *
 * The order is the whole point. Releasing the pending record and the alarm is
 * the part that must happen; the badge is the part the user sees. Painting
 * first meant a cosmetic failure could strand the poll for ever -- the same
 * shape as a leaked lane slot, and just as silent. Clearing first means the
 * worst case is a run that finished without its tick.
 */
async function finishBadge(ok: boolean, tabId?: number): Promise<void> {
  await chrome.storage.session.remove(PENDING_KEY)
  await chrome.alarms.clear(POLL_ALARM)
  await badge(ok ? '\u2713' : '\u2715', ok ? OK_COLOUR() : BAD_COLOUR(), tabId)
  setTimeout(() => repaintBadge(tabId), 10_000)
}

async function pollPending(): Promise<void> {
  const stored = await chrome.storage.session.get(PENDING_KEY)
  const pending = stored[PENDING_KEY] as PendingIngest | undefined
  if (!pending) {
    await chrome.alarms.clear(POLL_ALARM)
    return
  }
  if (Date.now() - pending.startedAt > POLL_GIVE_UP_MS) {
    await finishBadge(false, pending.tabId)
    return
  }
  try {
    const settings = await loadSettings()
    const run = await apiGet<{ status: string }>(
      settings,
      `/runs/${encodeURIComponent(pending.runId)}`,
    )
    // `queued` is IN FLIGHT (C11). Testing `status !== 'running'` for
    // terminality reports a verdict before anything has happened.
    if (run.status === 'queued' || run.status === 'running') return
    await finishBadge(run.status === 'succeeded' || run.status === 'succeeded_partial', pending.tabId)
  } catch (err) {
    console.warn('[Venona] ingest poll failed:', errorText(err))
  }
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === POLL_ALARM) void pollPending()
  if (alarm.name === CATALOG_ALARM) void refreshCatalog()
})

/** Inject into a tab that was granted a moment ago.
 *
 * Registration alone does nothing to the page you are looking at:
 * `registerContentScripts` applies to future navigations, so without this the
 * first click ends with a permission granted, nothing on screen, and no reason
 * given -- the user reloads and it works, and concludes the extension is flaky.
 *
 * `files`, not the registered script: the registration is for NEXT time. Both
 * land in the same isolated world, which is what makes the re-entry guard in
 * `content/index.ts` able to see a double-injection at all.
 */
async function injectNow(tabId: number): Promise<void> {
  try {
    await chrome.scripting.insertCSS({ target: { tabId }, files: ['mentions.css'] })
    await chrome.scripting.executeScript({ target: { tabId }, files: ['content.js'] })
  } catch (err) {
    // A tab that navigated away mid-grant is ordinary. The registration has
    // still landed, so the next load annotates.
    console.warn('[Venona] could not inject after granting:', errorText(err))
  }
}

/**
 * The toolbar button: make Venona work HERE, then ingest.
 *
 * The one action that reaches the API with no content script involved, so it
 * works on a page the allowlist does not cover -- which is most of the point:
 * the article worth adding is usually from a source `config/sources.yaml` has
 * never heard of. Feedback is the badge, because a worker has no UI of its own
 * and a notification for something just clicked is noise.
 *
 * THE FIRST TWO STATEMENTS RUN SYNCHRONOUSLY, AND THAT IS THE WHOLE DESIGN.
 *
 * `chrome.permissions.request` requires a live user gesture and an action click
 * is one -- but only until the handler awaits. So both calls are STARTED here,
 * before the async body, and their promises are awaited inside it. Reordering
 * this so `contains` is awaited first turns every prompt into a silent `false`,
 * which is indistinguishable from the user pressing Deny.
 *
 * Requesting unconditionally is safe: Chrome resolves `true` with no prompt when
 * the origin is already granted, which is exactly why `contains` has to be asked
 * separately -- it is the only way to know whether THIS click is the one that
 * granted it, and therefore whether to annotate or to ingest.
 *
 * The origin pattern is `https://host/*`, not the catalog's `https://*.host/*`.
 * It is the narrower of the two and it is what the prompt names, so the grant
 * matches what the user was actually asked. `enabledPatternFor` is what lets the
 * options page recognise a row granted this way.
 */
chrome.action.onClicked.addListener((tab) => {
  const pattern = tab.url && /^https:/.test(tab.url) ? originPatternFor(tab.url) : null
  // Started, NOT awaited. See above.
  const wasGranted = pattern
    ? chrome.permissions.contains({ origins: [pattern] }).catch(() => false)
    : Promise.resolve(false)
  const nowGranted = pattern
    ? chrome.permissions.request({ origins: [pattern] }).catch(() => false)
    : Promise.resolve(false)

  void (async () => {
    if (!tab.url || !/^https?:/.test(tab.url)) {
      await badge('\u2715', BAD_COLOUR(), tab.id)
      return
    }

    // Signed out: the `!` badge needs somewhere to LEAD. Without this, the
    // badge was a dead end -- the fix lives on the options page and nothing
    // pointed there, so the toolbar click is the pointer.
    const routed = await settingsForTab(tab.url)
    const blocked = await chrome.storage.session.get(blockedKeyFor(routed))
    if (blocked[blockedKeyFor(routed)]) {
      chrome.runtime.openOptionsPage()
      return
    }

    // \u2500\u2500 first click on a new site: grant, register, annotate \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    //
    // Ingest is NOT also done here. This click bought the underlines; asking
    // the research lane for a full ingest in the same gesture would spend
    // model budget the user did not ask for, and C11 could refuse it anyway.
    // The next click, with the site granted, is the one that ingests.
    if (pattern && !(await wasGranted)) {
      if (!(await nowGranted)) {
        await badge('\u2715', BAD_COLOUR(), tab.id)
        setTimeout(() => repaintBadge(tab.id), 10_000)
        return
      }
      // NEVER the Venona app itself. `sites.ts` is explicit that registering
      // the content script against the API origin makes the extension annotate
      // Venona's own pages, and the reconciler avoids it by never putting
      // `host_permissions` into intent. This path can reach it another way: a
      // production deployment on a host the manifest does not name is an
      // ordinary https page to everything above, so clicking the toolbar on
      // your own brief would enable it. The permission stays granted -- talking
      // to the API is what it is for -- but it does not become a SITE.
      // EVERY configured instance's origin, not just the routed one: with two
      // instances, clicking on B's own brief while routing lands on A would
      // otherwise enroll B's app as a site.
      const { instances } = await loadInstances()
      if (instances.some((instance) => pattern === originPatternFor(instance.baseUrl))) {
        await badge('✓', OK_COLOUR(), tab.id)
        setTimeout(() => repaintBadge(tab.id), 10_000)
        return
      }

      // Intent is what `syncRegistration` reconciles against; the grant alone
      // registers nothing.
      await saveEnabled([...new Set([...(await loadEnabled()), pattern])])
      await syncRegistration()
      if (tab.id !== undefined) await injectNow(tab.id)
      await badge('\u2713', OK_COLOUR(), tab.id)
      setTimeout(() => repaintBadge(tab.id), 10_000)
      return
    }

    // A paired credential is annotate-scoped and `/ingest` would 403 it. The
    // honest second click is the app itself: useful, and never a failure badge
    // for a feature the credential was never meant to reach.
    if ((await loadCapability(routed.instanceId)) === 'limited') {
      await chrome.tabs.create({ url: routed.baseUrl.replace(/\/api$/, '') })
      return
    }

    await badge('\u2026', OK_COLOUR(), tab.id)
    try {
      const settings = await settingsForTab(tab.url)
      const started = await apiPost<RunStarted>(settings, '/ingest', { url: tab.url })
      await chrome.storage.session.set({
        [PENDING_KEY]: {
          runId: started.run_id,
          tabId: tab.id,
          startedAt: Date.now(),
        } satisfies PendingIngest,
      })
      await chrome.alarms.create(POLL_ALARM, { periodInMinutes: POLL_PERIOD_MINUTES })
      // One immediate look, so a fast ingest does not wait out the first alarm.
      await pollPending()
    } catch (err) {
      // A busy research lane is a 409 with a sentence in it (C11), not a
      // failure of the extension -- `routers/ingest.py` converts `LaneBusy`.
      console.warn('[Venona] ingest failed:', errorText(err))
      // A 403 here is the credential's shape, not a fault; remember it so the
      // NEXT click opens the app instead of badging another failure.
      if ((err as { status?: number }).status === 403) {
        void saveCapability(routed.instanceId, 'limited')
      }
      await badge('\u2715', BAD_COLOUR(), tab.id)
      setTimeout(() => repaintBadge(tab.id), 10_000)
    }
  })()
})

// ── where the extension runs ────────────────────────────────────────
//
// One registered script holding every granted host, rebuilt whenever the intent
// list, the grants, or the pauses move. `shared/sites.ts` owns the match set,
// and says why a catalog refresh may never touch registration.

const SCRIPT_ID = 'venona-sites'
/** The theme bridge on instance origins the manifest's static block does not
 * name. One registration holding every granted origin, like `venona-sites` --
 * a second id per origin would double the unregister/reconcile bookkeeping
 * for nothing. */
const THEME_SCRIPT_ID = 'venona-theme'
const CATALOG_ALARM = 'sites-refresh'
/** In minutes, for `chrome.alarms`. `CATALOG_TTL_MS` is the same interval. */
const CATALOG_PERIOD_MINUTES = CATALOG_TTL_MS / 60_000

/** Ask the BROWSER what is granted, one pattern at a time.
 *
 * `permissions.contains` per pattern rather than one `getAll()`: `getAll` also
 * returns the manifest's own `host_permissions`, so a set built from it would
 * include `http://127.0.0.1:5173/*` and register the content script against the
 * Venona app itself. Asking about the patterns we actually mean cannot do that.
 */
async function grantedAmong(patterns: string[]): Promise<string[]> {
  const checks = await Promise.all(
    patterns.map(async (pattern) => {
      try {
        return (await chrome.permissions.contains({ origins: [pattern] })) ? pattern : null
      } catch {
        // An invalid pattern throws rather than returning false. Treat it as
        // not-granted; it can never have been.
        return null
      }
    }),
  )
  return checks.filter((pattern): pattern is string => pattern !== null)
}

/** Serialised, because three things race to call it.
 *
 * Granting a site fires `permissions.onAdded` AND writes `sites:enabled`, which
 * fires `storage.onChanged` -- and the toolbar path calls it directly on top of
 * both. Two overlapping runs each read `getRegisteredContentScripts`, each see
 * the same registration, and each call `unregisterContentScripts`; the loser
 * rejects on an id the winner has already removed. That rejection is unhandled, and the
 * registration it was in the middle of re-adding may never land, which presents
 * as a site that was granted and still does not annotate.
 *
 * A chain rather than a lock: calls are cheap, ordering is what matters, and the
 * last one to run is the one whose answer is current. `.catch` before `.then`
 * keeps one failure from poisoning every later call.
 */
let syncing: Promise<string[]> = Promise.resolve([])

export function syncRegistration(): Promise<string[]> {
  syncing = syncing.catch(() => []).then(syncRegistrationNow)
  return syncing
}

async function syncRegistrationNow(): Promise<string[]> {
  const [enabled, paused] = await Promise.all([loadEnabled(), loadPaused()])
  const want = reconcile(enabled, await grantedAmong(enabled), paused)

  // `unregisterContentScripts` REJECTS on an unknown id, so probe rather than
  // swallowing the rejection -- a blanket `.catch` here would also hide a real
  // failure to unregister, which presents as the previous match set staying
  // live.
  const existing = await chrome.scripting.getRegisteredContentScripts({ ids: [SCRIPT_ID] })
  if (existing.length) await chrome.scripting.unregisterContentScripts({ ids: [SCRIPT_ID] })

  if (want.length) {
    await chrome.scripting.registerContentScripts([
      {
        id: SCRIPT_ID,
        matches: want,
        js: ['content.js'],
        css: ['mentions.css'],
        runAt: 'document_idle',
        persistAcrossSessions: true,
      },
    ])
  }

  // The theme bridge follows the CONFIGURED INSTANCES, not the sites list: it
  // reads the app's own theme choice, so it belongs on every granted instance
  // origin the manifest's static `content_scripts` block does not already
  // cover. The skip-set comes from the manifest itself rather than a copied
  // literal, so the static block stays single-injected without a twin to drift.
  const staticMatches = new Set(
    (chrome.runtime.getManifest().content_scripts ?? []).flatMap((entry) => entry.matches ?? []),
  )
  const candidates = (await loadInstances()).instances
    .map((instance) => originPatternFor(instance.baseUrl))
    .filter((pattern): pattern is string => pattern !== null && !staticMatches.has(pattern))
  const themeMatches = await grantedAmong([...new Set(candidates)])

  const themeExisting = await chrome.scripting.getRegisteredContentScripts({
    ids: [THEME_SCRIPT_ID],
  })
  if (themeExisting.length) {
    await chrome.scripting.unregisterContentScripts({ ids: [THEME_SCRIPT_ID] })
  }
  if (themeMatches.length) {
    await chrome.scripting.registerContentScripts([
      {
        id: THEME_SCRIPT_ID,
        matches: themeMatches,
        js: ['theme.js'],
        runAt: 'document_idle',
        persistAcrossSessions: true,
      },
    ])
  }
  return want
}

async function refreshCatalog(): Promise<void> {
  // EVERY configured instance, not just the active one: auto-routing decides
  // by comparing catalogs, and an instance whose catalog was never fetched can
  // never win a tab. Sequential and per-instance guarded -- one unreachable
  // deployment must not starve the others' refreshes.
  const { instances } = await loadInstances()
  for (const instance of instances) {
    try {
      await fetchCatalog(await settingsFor(instance.id))
    } catch (err) {
      // A stale catalog is not a failure worth surfacing: the options page
      // renders the cached copy and says so, and registration ignores it.
      console.warn(`[Venona] site catalog refresh failed for ${instance.id}:`, errorText(err))
    }
  }
}

// `persistAcrossSessions: true` means this is a reconcile, not a rebuild -- but
// it still has to run, because a permission revoked while the browser was closed
// leaves a registration the browser kept and the user did not consent to.
chrome.runtime.onInstalled.addListener((details) => {
  void syncRegistration()
  void refreshCatalog()
  void rebuildInstanceMenu()
  void chrome.alarms.create(CATALOG_ALARM, { periodInMinutes: CATALOG_PERIOD_MINUTES })
  // Store channel, FIRST install only -- `onInstalled` also fires on every
  // update, and a tab popping open per release would be noise. Pairing is the
  // first thing a new install needs and nothing else surfaces the page.
  if (EXT_CHANNEL === 'store' && details.reason === 'install') {
    chrome.runtime.openOptionsPage()
  }
})
chrome.runtime.onStartup.addListener(() => {
  void syncRegistration()
  void rebuildInstanceMenu()
})

/** Say a paired token has ended BEFORE the first silent failure.
 *
 * A paired token expires on the server's schedule (30 days, no refresh token
 * by contract C12), and the first symptom would otherwise be a page with no
 * underlines -- indistinguishable from a page with no entities on it. Pairing
 * recorded `expiresAt`, so the worker can raise the badge preemptively. On
 * every spawn rather than an event: `markSignedOut` is idempotent and the
 * reads are two storage hits.
 */
void (async () => {
  const settings = await loadSettings()
  if (!settings.token) return
  const meta = await loadTokenMeta(settings.instanceId)
  if (meta && new Date(meta.expiresAt).getTime() <= Date.now()) {
    await markSignedOut(settings)
  }
})()

// ── the instance switcher ───────────────────────────────────────────
//
// A radio group on the toolbar button's context menu, because a popup would
// steal the click that annotates. One entry per configured instance; only
// drawn at all once there are two.

const MENU_PREFIX = 'venona-instance:'

async function rebuildInstanceMenu(): Promise<void> {
  const all = await loadInstances()
  await chrome.contextMenus.removeAll()
  if (all.instances.length < 2) return
  for (const instance of all.instances) {
    chrome.contextMenus.create({
      id: `${MENU_PREFIX}${instance.id}`,
      title: `Send to: ${instance.label || instance.baseUrl}`,
      type: 'radio',
      checked: instance.id === all.activeId,
      contexts: ['action'],
    })
  }
}

chrome.contextMenus.onClicked.addListener((info) => {
  const id = String(info.menuItemId)
  if (!id.startsWith(MENU_PREFIX)) return
  void setActiveInstance(id.slice(MENU_PREFIX.length))
})

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'sync' && (changes.instances || changes.activeId)) {
    void rebuildInstanceMenu()
    // The theme bridge's match set is derived from the instance list, so an
    // added or removed instance re-reconciles registration too.
    void syncRegistration()
  }
})

chrome.permissions.onAdded.addListener(() => void syncRegistration())
chrome.permissions.onRemoved.addListener(() => void syncRegistration())

/** The options page writes storage; the worker reacts. NOT a message.
 *
 * `chrome.runtime.sendMessage` from one extension context is not delivered to
 * the sender's own listener, and the options page and the worker are different
 * contexts with different lifetimes -- the worker is dead most of the time. A
 * storage write wakes it, carries the intent, and keeps any other open options
 * tab in step for free. It is also the only handle `scripts/ext_smoke.py` has:
 * the worker is an ES module, so its top-level bindings are not on `globalThis`
 * and CDP cannot call `syncRegistration` directly.
 */
chrome.storage.onChanged.addListener((changes, area) => {
  if (touchesSites(changes, area)) void syncRegistration()
})

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== PORT_NAME) return
  /**
   * The TAB, not its URL.
   *
   * The URL is the routing key -- which instance a page belongs to -- and a
   * single-page site changes it without reconnecting the port, so a URL
   * captured at connect routes every later request by where the reader STARTED
   * rather than where they are. On a multi-instance setup that means annotating
   * against the wrong instance, silently, with a plausible-looking answer.
   *
   * Re-resolved per request rather than tracked: a module-level map fed by
   * `tabs.onUpdated` evaporates when the worker is evicted at ~30s idle, which
   * is the exact hazard the module-variable comment above documents. `tabs.get`
   * is stateless and correct on every respawn.
   *
   * The protocol still does not let the content script claim a URL for itself.
   */
  const tabId = port.sender?.tab?.id
  const senderUrl = port.sender?.url

  async function routingKey(): Promise<string | undefined> {
    if (tabId === undefined) return senderUrl
    try {
      return (await chrome.tabs.get(tabId)).url ?? senderUrl
    } catch {
      // The tab is gone. Falling back keeps the request answerable; a throw
      // here would leave the caller's promise pending for the life of the page.
      return senderUrl
    }
  }

  port.onMessage.addListener((req: Request) => {
    const reply = (res: Response) => {
      // The port is gone if the tab navigated mid-request. That is ordinary,
      // not an error worth surfacing.
      try {
        port.postMessage(res)
      } catch {
        /* disconnected */
      }
    }
    // The `.catch` is not belt-and-braces: every request MUST be answered.
    // `handle` is not allowed to reject, but if it ever does, an unanswered
    // request leaves the caller's promise pending for the life of the page --
    // silent, and indistinguishable from a slow API.
    void routingKey()
      .then((url) => handle(req, url))
      .then(reply)
      .catch((err) => {
        console.warn('[Venona] request handler threw', err)
        reply({ id: req.id, ok: false, error: String(err) })
      })
  })
})
