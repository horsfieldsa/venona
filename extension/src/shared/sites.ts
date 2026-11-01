/** Where the extension is allowed to run, and who decides.
 *
 * NOT A LITERAL LIST IN `manifest.json`. A hand-kept `content_scripts.matches`
 * has nothing generating it and nothing checking it against the corpus it is
 * supposed to mirror, so it drifts: five names against fourteen domains the
 * database had actually produced documents from, with `warontherocks.com` (40
 * documents), `gao.gov` (25) and `darpa.mil` (10) all CONFIGURED FEEDS the
 * extension had never once annotated. Every domain the discovery sweeps find is
 * invisible to it by construction -- a `kind: discovery` source carries no `url`
 * for anyone to copy into a manifest -- and adding one means editing JSON,
 * rebuilding, and reloading the extension.
 *
 * WHAT DID NOT CHANGE, AND WHY IT MATTERS MORE.
 *
 * `README.md` records that `matches` briefly carried `https://*.gov/*` and
 * `https://*.mil/*`, which is not "defence trade press" -- it is login.gov and
 * va.gov and irs.gov, on each of which the content script would inject, mutate
 * the DOM, and POST the page's text to `/api/annotate`. The conclusion was "add
 * hosts one at a time, by name."
 *
 * That survives intact. `GET /api/sources/sites` OFFERS a list; it never
 * APPLIES one. Every host still needs a `chrome.permissions.request` the user
 * approves and can revoke from `chrome://extensions`. What went away is the
 * manifest edit and the rebuild, not the consent.
 */

import { apiGet, type Settings } from './client'
import type { AnnotatableSite } from './types'

export type { AnnotatableSite }

/** The API's answer, cached. `local`, not `sync`: it is derived data that any
 * machine can refetch in one request, and `sync` has an 8 KB per-item cap that
 * a few hundred sites with publishers and counts would eventually meet. */
const catalogKey = (instanceId: string) => `sites:catalog:${instanceId}`

/** The user's INTENT -- match patterns they chose to enable.
 *
 * `sync`, because it is a preference rather than a credential and is worth
 * carrying to another machine. Note this is intent, NOT permission: the browser
 * is the only authority on what is actually granted, which is why `reconcile`
 * intersects the two rather than trusting this list.
 */
const ENABLED_KEY = 'sites:enabled'

/** Granted but temporarily off. Separate from `enabled` on purpose: pausing
 * must not drop the permission, or resuming would re-prompt. */
const PAUSED_KEY = 'sites:paused'

/** Long enough that it is not chatter, short enough that a source added in
 * Settings shows up the same day. A refresh NEVER changes registration -- a new
 * domain lands in the options page as an ungranted row and waits for a click. */
export const CATALOG_TTL_MS = 6 * 60 * 60 * 1000

export interface Catalog {
  at: number
  sites: AnnotatableSite[]
}

export async function fetchCatalog(settings: Settings): Promise<Catalog> {
  const body = await apiGet<{ sites: AnnotatableSite[] }>(settings, '/sources/sites')
  const catalog: Catalog = { at: Date.now(), sites: body.sites ?? [] }
  const key = catalogKey(settings.instanceId)
  // A cache write may never fail the request that filled it -- the same rule
  // `background.ts` learned the hard way with the annotate cache, where an
  // awaited quota rejection cost a page every underline it had just earned.
  try {
    await chrome.storage.local.set({ [key]: catalog })
  } catch (err) {
    console.warn('[Venona] site catalog cache write failed; continuing', err)
  }
  return catalog
}

export async function loadCatalog(instanceId: string): Promise<Catalog | null> {
  // Per instance: two deployments advertise two site lists, and rendering
  // one's cache under the other's name would grant against the wrong corpus.
  const key = catalogKey(instanceId)
  const got = await chrome.storage.local.get(key)
  return (got[key] as Catalog | undefined) ?? null
}

export async function loadEnabled(): Promise<string[]> {
  const got = await chrome.storage.sync.get({ [ENABLED_KEY]: [] })
  return (got[ENABLED_KEY] as string[]) ?? []
}

export async function loadPaused(): Promise<string[]> {
  const got = await chrome.storage.sync.get({ [PAUSED_KEY]: [] })
  return (got[PAUSED_KEY] as string[]) ?? []
}

export async function saveEnabled(patterns: string[]): Promise<void> {
  await chrome.storage.sync.set({ [ENABLED_KEY]: [...new Set(patterns)].sort() })
}

export async function savePaused(patterns: string[]): Promise<void> {
  await chrome.storage.sync.set({ [PAUSED_KEY]: [...new Set(patterns)].sort() })
}

/** Does a change to `sync` concern us? Used by the worker's `onChanged` hook. */
export function touchesSites(changes: Record<string, unknown>, area: string): boolean {
  return area === 'sync' && (ENABLED_KEY in changes || PAUSED_KEY in changes)
}

/**
 * What should actually be registered: intent, minus what the browser has not
 * granted, minus what the user paused.
 *
 * PURE, and exported, because it is the one piece of this feature that can go
 * wrong silently. `make ext-smoke` needs a live API and a headless browser;
 * this needs neither, and `sites.spec.ts` covers the three cases that matter.
 *
 * **`granted` must come from `permissions.contains`, never from
 * `permissions.getAll().origins`.** `getAll` also returns the manifest's own
 * `host_permissions` -- `http://127.0.0.1:5173/*` -- so registering from it
 * would inject `content.js` into the Venona app itself. Deriving from the
 * explicit intent list and verifying against the browser is what keeps a
 * revocation from `chrome://extensions` honest without ever widening the set.
 */
/** The host a match pattern is about, with any leading `*.` removed.
 *
 * `https://*.gao.gov/*` -> `gao.gov`; `https://www.defensenews.com/*` ->
 * `www.defensenews.com`. Deliberately string work rather than `new URL()`: a
 * pattern is not a URL, and `new URL('https://*.gao.gov/*')` parses the `*` as
 * part of the host and hands back something that looks right until it does not.
 */
function patternHost(pattern: string): string | null {
  const match = /^[a-z*]+:\/\/([^/]+)\//.exec(pattern)
  return match ? match[1].replace(/^\*\./, '') : null
}

/**
 * The enabled pattern that actually covers a catalog domain, if any.
 *
 * TWO VOCABULARIES REACH `sites:enabled`, and they do not spell a site the same
 * way. The options page enables the catalog's own `https://*.defensenews.com/*`.
 * The toolbar button enables the narrower `https://www.defensenews.com/*` --
 * the exact origin you were standing on, which is what the browser's prompt
 * named and therefore the only thing the user actually agreed to.
 *
 * Without this, a site granted from the toolbar renders as an UNTICKED row in
 * Settings while it is busy annotating: the checkbox asks whether `site.match`
 * is enabled, and it is not. Unticking it would then remove a pattern that was
 * never there and leave the real grant in place -- a control that lies about
 * the state and cannot change it.
 */
export function enabledPatternFor(domain: string, enabled: string[]): string | null {
  return (
    enabled.find((pattern) => {
      const host = patternHost(pattern)
      return host === domain || (host?.endsWith(`.${domain}`) ?? false)
    }) ?? null
  )
}

export function reconcile(enabled: string[], granted: string[], paused: string[]): string[] {
  const isGranted = new Set(granted)
  const isPaused = new Set(paused)
  return enabled.filter((pattern) => isGranted.has(pattern) && !isPaused.has(pattern)).sort()
}

/** Does this catalog claim the host? The `enabledPatternFor` semantics --
 * exact or dot-suffix, never a bare `endsWith`, which would let
 * `notdefensenews.com` ride `defensenews.com`'s claim. */
export function catalogCovers(host: string, sites: AnnotatableSite[]): boolean {
  return sites.some((site) => host === site.domain || host.endsWith(`.${site.domain}`))
}

/**
 * The instance that should handle a tab.
 *
 * EXACTLY ONE covering catalog wins; anything else -- zero claims, a tie, no
 * parseable host -- falls back to the globally active instance. A tie is a
 * genuine ambiguity (two corpora both track the site), and guessing between
 * them silently would send an ingest to a corpus the user did not pick; the
 * context-menu switcher is the disambiguator and it stays authoritative.
 *
 * Pure, like `reconcile`, and for the same reason: this is the one piece of
 * auto-routing that can be wrong silently, and `sites.spec.ts` needs to hold
 * it without a browser.
 */
export function routeForHost(
  host: string | null,
  catalogs: { instanceId: string; sites: AnnotatableSite[] }[],
  activeId: string,
): string {
  if (!host) return activeId
  const hits = catalogs.filter((catalog) => catalogCovers(host, catalog.sites))
  return hits.length === 1 ? hits[0].instanceId : activeId
}
