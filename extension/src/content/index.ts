/** Orchestration: extract -> annotate -> splice -> watch, then hand over to the
 * card.
 *
 * Everything here runs in the page's process, so it holds no credential and
 * makes no network call of its own -- `port.ts` is the only way out.
 */

import type { AnnotateResponse } from '../shared/types'
import { wireHover } from './card'
import { invalidate, prefetch } from './cards'
import {
  articleRoot,
  extract,
  MAX_BLOCK_CHARS,
  MAX_BLOCKS,
  MAX_TOTAL_CHARS,
  MAX_URL_CHARS,
  type ExtractedBlock,
} from './extract'
import { stampGround } from './ground'
import { ask } from './port'
import { spliceBlock, STAMP, unsplice } from './splice'

let root: Element | null = null
let indexVersion = ''
/** Set while we are the ones writing, so the observer ignores our own spans. */
let writing = false

/** Re-annotation budget.
 *
 * A framework that re-renders the article on a timer would otherwise put us in
 * a loop with it: we splice, it re-renders, we splice. Degrading to nothing is
 * the right failure -- a page with no underlines is a page, a page in a
 * mutation loop is a hung tab.
 */
const RENDER_BUDGET = 6
const RENDER_WINDOW_MS = 10_000
const attempts: number[] = []

function overBudget(): boolean {
  const now = Date.now()
  while (attempts.length && now - attempts[0] > RENDER_WINDOW_MS) attempts.shift()
  attempts.push(now)
  if (attempts.length > RENDER_BUDGET) {
    console.warn('[Venona] this page re-renders faster than it can be annotated; standing down')
    return true
  }
  return false
}

async function pass(): Promise<void> {
  if (!root) return
  // Every pass, not once at start: a site with its own dark-mode toggle repaints
  // under us, and the answer is a property of the page as it is now. It is a
  // short walk up the ancestors and it writes one attribute on `<html>`, which
  // the observer does not watch.
  stampGround(root)

  // Anything stamped at a superseded version goes back to plain
  // text before the walk. `skippable` prunes stamped subtrees, so without this
  // an annotated block is invisible to `extract` FOR EVER and the filter below
  // -- and the whole `index_version` path it belongs to -- can never fire. A
  // term grounded from the card kept its dashed "undefined" underline for the
  // life of the page.
  for (const stale of root.querySelectorAll<HTMLElement>(`[${STAMP}]`)) {
    if (stale.getAttribute(STAMP) !== indexVersion) unsplice(stale)
  }

  const all = extract(root)
  // Only blocks we have not already done at this index version. After a run
  // lands the version moves and every block is fair game again.
  const fresh = all.filter((block) => block.el.getAttribute(STAMP) !== indexVersion)
  if (!fresh.length) return

  // AFTER the early return, not before. `overBudget` mutates the window, so
  // counting passes that annotate nothing spent the budget on ordinary page
  // churn -- a lazy image, an ad refresh, a live-blog timestamp -- and then
  // disconnected the observer permanently, having spliced exactly once. The
  // budget is meant to catch a splice/re-render LOOP, which by definition does
  // work on every pass.
  if (overBudget()) {
    observer?.disconnect()
    return
  }

  // Every server-side cap mirrored, because each one is a WHOLE-REQUEST 422:
  // one oversized block and the entire page comes back with no annotations at
  // all, which looks exactly like "the corpus knows nothing here". `blockOf`
  // falls back to the article root when a page lays its paragraphs out in
  // divs, so a single block holding 40k characters is ordinary, not exotic.
  const sendable: { id: string; text: string; hrefs: [number, number][] }[] = []
  let total = 0
  for (const block of fresh) {
    if (sendable.length >= MAX_BLOCKS) break
    if (block.text.length > MAX_BLOCK_CHARS) continue
    if (total + block.text.length > MAX_TOTAL_CHARS) break
    total += block.text.length
    sendable.push({ id: block.id, text: block.text, hrefs: block.hrefs })
  }
  if (!sendable.length) return

  const res = await ask({
    kind: 'annotate',
    // Truncated, not dropped: it is diagnostics only, and a long tracking URL
    // must not cost the page its underlines.
    url: location.href.slice(0, MAX_URL_CHARS),
    blocks: sendable,
  })
  if (!res.ok) {
    console.warn('[Venona]', res.error)
    return
  }
  if (res.kind !== 'annotate') return

  const body: AnnotateResponse = res.data
  // The corpus moved under us -- a run landed, or a term was grounded from this
  // very card. Every cached card may now say something different.
  if (indexVersion && body.index_version !== indexVersion) invalidate()
  indexVersion = body.index_version

  const byId = new Map<string, ExtractedBlock>(fresh.map((block) => [block.id, block]))
  writing = true
  let drawn = 0
  try {
    for (const out of body.blocks) {
      const block = byId.get(out.id)
      if (!block) continue
      drawn += spliceBlock(block, out.runs, indexVersion)
    }
  } finally {
    // Let the mutations we just made settle before listening again, or the
    // observer sees our own writes and schedules another pass.
    setTimeout(() => {
      writing = false
    }, 0)
  }

  if (drawn) void prefetch(body.refs ?? [])
}

let observer: MutationObserver | null = null
let debounce: number | undefined

function schedule(): void {
  clearTimeout(debounce)
  debounce = window.setTimeout(() => void pass(), 300)
}

function start(): void {
  root = articleRoot()
  wireHover()
  void pass()

  observer = new MutationObserver((records) => {
    if (writing) return
    for (const record of records) {
      // Our own spans mutating is not news.
      if (record.target instanceof Element && record.target.closest('[data-venona-ref]')) continue
      schedule()
      return
    }
  })
  observer.observe(root, { childList: true, subtree: true, characterData: true })
}

/** Run once per document, however we got here.
 *
 * There are two injection paths now: the registration in `background.ts`, which
 * covers future navigations, and `injectNow`, which runs `content.js` into the
 * tab you are looking at the moment you grant it. On the click that grants a
 * site they cannot both fire -- registration only applies to later loads -- but
 * a second click, a re-grant, or an `executeScript` racing a slow document can
 * land two copies in the same isolated world.
 *
 * Two copies is not harmless: each `start()` builds its own MutationObserver on
 * the same root, and each observer sees the other's splices as page churn. They
 * schedule passes at each other until `RENDER_BUDGET` trips and both stand
 * down, which reads as "annotated for a second, then stopped".
 *
 * A module-level flag would NOT catch it -- a second `executeScript` evaluates
 * the bundle afresh with its own module scope. The isolated world's `window` is
 * the one thing the two copies share.
 */
declare global {
  interface Window {
    __venonaContentLoaded?: boolean
  }
}

if (window.__venonaContentLoaded) {
  console.debug('[Venona] content script already running in this document')
} else {
  window.__venonaContentLoaded = true
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true })
  } else {
    start()
  }
}
