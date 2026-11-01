/**
 * Copy the app's theme choice into extension storage.
 *
 * A SECOND CONTENT SCRIPT, registered statically against Venona's OWN origins,
 * and it must never grow into the first one.
 *
 * `shared/sites.ts` deliberately refuses to register the ANNOTATING script
 * against Venona's own origin -- deriving the registration from
 * `permissions.getAll().origins` would inject `content.js` into the app itself,
 * which is why that list is built from explicit intent instead. That reason is
 * unchanged. This script is the opposite shape: Venona's origin ONLY, no
 * splicing, no card, no network, and it imports nothing from the splicer.
 * `scripts/finish.mjs` asserts that negatively.
 *
 * WHY IT HAS TO EXIST. `chrome.action.setBadgeBackgroundColor` takes a literal
 * colour, and the service worker that calls it has no DOM and no `matchMedia`,
 * so it cannot resolve `light-dark()` or read `prefers-color-scheme`. The card
 * and the options page can and do -- they need no help under System. The badge
 * needs a stored answer, and the app is where the reader actually chooses.
 *
 * No new permission and no new consent: both origin patterns are already in the
 * manifest's `host_permissions`, because the extension talks to the API there.
 *
 * THE ATTRIBUTE IS THE ANSWER, and this script reads nothing else. `data-theme`
 * on `<html>` is what the app resolves the palette from, so it cannot disagree
 * with what the reader is looking at, and its absence IS System. Reading the
 * app's own storage instead would mean a second copy of a key this file cannot
 * import, and would have to learn wherever that key lives (docs/adr/0177).
 *
 * OBSERVING IT CATCHES THE TAB THAT CAUSED THE CHANGE, which a storage event
 * does not: those fire in OTHER tabs of an origin, never in the one that wrote
 * the value, so a reader clicking the footer toggle would see nothing change
 * until they opened a second tab. The app writes the attribute synchronously on
 * every change INCLUDING back to System, where it is removed, and it re-reads
 * the shared choice when a tab is focused -- so every transition moves this
 * attribute in the tab that is watching.
 *
 * `run_at` is `document_idle` for the static registration and both dynamic ones
 * in `background.ts`, so the render-blocking boot script has long since set the
 * attribute by the time this first reads it.
 */

import { THEME_KEY, type ThemeChoice } from '../shared/theme'

function currentChoice(): ThemeChoice {
  const attribute = document.documentElement.dataset.theme
  return attribute === 'light' || attribute === 'dark' ? attribute : 'system'
}

let last: ThemeChoice | null = null

function sync(): void {
  const choice = currentChoice()
  if (choice === last) return
  last = choice
  // `void`, and it must stay that way: `chrome.storage` REJECTS once the
  // extension context is invalidated (an update, a reload from
  // `chrome://extensions`), and an unhandled rejection in a content script is
  // logged against the HOST PAGE, where it reads as the app throwing.
  void chrome.storage.sync.set({ [THEME_KEY]: choice }).catch(() => {})
}

sync()

new MutationObserver(sync).observe(document.documentElement, {
  attributes: true,
  attributeFilter: ['data-theme'],
})
