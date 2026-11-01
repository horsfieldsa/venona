/** Hover intent. Port of `web/src/stores/hover.ts`, minus Pinia.
 *
 * The three delays are the whole design and they are not arbitrary:
 *
 *   OPEN     suppresses cards while the pointer crosses a dense paragraph. A
 *            page with two hundred mentions would otherwise flash a card for
 *            every word on the way to the one you wanted.
 *   CLOSE    is the time to travel from the mention into the card. There is no
 *            safe triangle; there is a grace period, which is simpler and,
 *            at this distance, indistinguishable.
 *   PREFETCH starts the fetch before the card is shown, so the common case
 *            renders with data already in hand.
 */

import { shallowRef } from 'vue'

const OPEN_DELAY = 140
const CLOSE_DELAY = 250
const PREFETCH_DELAY = 60

export const activeId = shallowRef<string | null>(null)
export const anchor = shallowRef<HTMLElement | null>(null)
export const open = shallowRef(false)
export const pinned = shallowRef(false)

let openTimer: number | undefined
let closeTimer: number | undefined
let prefetchTimer: number | undefined

/** Set by `card.ts` so this module does not need to know about the cache. */
let onPrefetch: (id: string) => void = () => {}
export function setPrefetch(fn: (id: string) => void): void {
  onPrefetch = fn
}

function clearAll(): void {
  clearTimeout(openTimer)
  clearTimeout(closeTimer)
  clearTimeout(prefetchTimer)
}

export function request(element: HTMLElement, id: string): void {
  if (pinned.value) return
  clearTimeout(closeTimer)
  clearTimeout(openTimer)
  clearTimeout(prefetchTimer)

  prefetchTimer = window.setTimeout(() => onPrefetch(id), PREFETCH_DELAY)
  openTimer = window.setTimeout(() => {
    anchor.value = element
    activeId.value = id
    open.value = true
  }, OPEN_DELAY)
}

export function release(): void {
  if (pinned.value) return
  clearTimeout(openTimer)
  clearTimeout(prefetchTimer)
  closeTimer = window.setTimeout(() => {
    open.value = false
  }, CLOSE_DELAY)
}

/** Wired to the card's own `mouseenter`. This is what makes the gap crossable. */
export function hold(): void {
  clearTimeout(closeTimer)
}

export function pin(element: HTMLElement, id: string): void {
  clearAll()
  anchor.value = element
  activeId.value = id
  open.value = true
  pinned.value = true
}

export function dismiss(): void {
  clearAll()
  open.value = false
  pinned.value = false
}
