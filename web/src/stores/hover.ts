import { defineStore } from 'pinia'
import { ref } from 'vue'

import { useEntitiesStore } from '@/stores/entities'

const OPEN_DELAY = 140
const CLOSE_DELAY = 250
const PREFETCH_DELAY = 60

/**
 * Hover-intent state for the single global hover card.
 *
 * Three timings, each doing a specific job:
 *   PREFETCH (60ms)  starts the fetch before the card is shown, so by the time
 *                    it opens the data is usually already resident.
 *   OPEN (140ms)     suppresses cards while the pointer is merely crossing a
 *                    dense paragraph on its way somewhere else.
 *   CLOSE (250ms)    leaves time to travel from the mention into the card.
 */
export const useHoverStore = defineStore('hover', () => {
  const activeId = ref<string | null>(null)
  const anchor = ref<HTMLElement | null>(null)
  const open = ref(false)
  const pinned = ref(false)

  let openTimer: number | undefined
  let closeTimer: number | undefined
  let prefetchTimer: number | undefined

  const entities = useEntitiesStore()

  function clearTimers() {
    window.clearTimeout(openTimer)
    window.clearTimeout(closeTimer)
    window.clearTimeout(prefetchTimer)
  }

  function request(element: HTMLElement, id: string) {
    if (pinned.value) return
    clearTimers()

    prefetchTimer = window.setTimeout(() => void entities.fetchOne(id), PREFETCH_DELAY)
    openTimer = window.setTimeout(() => {
      activeId.value = id
      anchor.value = element
      open.value = true
    }, OPEN_DELAY)
  }

  function release() {
    if (pinned.value) return
    window.clearTimeout(openTimer)
    window.clearTimeout(prefetchTimer)
    closeTimer = window.setTimeout(() => {
      open.value = false
      activeId.value = null
      anchor.value = null
    }, CLOSE_DELAY)
  }

  /** Pointer entered the card itself -- cancel the pending close. */
  function hold() {
    window.clearTimeout(closeTimer)
  }

  /**
   * Click a mention: open the card and keep it open until dismissed.
   *
   * `pinned` was declared here from the start, guarded three times, and never
   * once set to `true` by anything in the codebase -- so the only thing holding
   * a card open was the pointer physically resting on it. Move the mouse during
   * a grounded acronym lookup, which can take three minutes, and the card
   * closed, the lookup state reset, and the answer arrived on a surface nobody
   * was looking at.
   *
   * Clicking must not navigate straight to the graph. In a document with
   * hundreds of dotted mentions that is a trapdoor with no way back to where you
   * were reading. "Open in graph →" inside the card is the deliberate way
   * through.
   */
  function pin(element: HTMLElement, id: string) {
    clearTimers()
    pinned.value = false
    activeId.value = id
    anchor.value = element
    open.value = true
    pinned.value = true
  }

  function dismiss() {
    clearTimers()
    pinned.value = false
    open.value = false
    activeId.value = null
    anchor.value = null
  }

  return { activeId, anchor, open, pinned, request, release, hold, pin, dismiss }
})
