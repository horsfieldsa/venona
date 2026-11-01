import { defineStore } from 'pinia'
import { ref } from 'vue'

/**
 * Fire-and-forget notices only.
 *
 * The line this store must not cross: anything that needs a DECISION stays
 * inline. Curation's merge refusals are real 409s with reasons ("survivor is
 * itself merged away", "that would make a cycle"), and they render as a
 * persistent `StatusNote` next to the queue. A toast that fades after four
 * seconds during keyboard-speed review is a silently lost merge.
 *
 * So: "saved", "history cleared", "run finished" -- yes. "This merge was
 * rejected because…" -- no.
 *
 * EVERY TOAST LEAVES ON ITS OWN. Nothing in this stack is permanent: a notice
 * the reader has stopped looking at is clutter over the thing they are actually
 * reading, and the bottom-right corner is the one surface that follows them
 * across every view and width, so anything parked there is parked everywhere.
 *
 * `sticky` therefore means LONGER rather than permanent, for the two things
 * that are true for a while rather than done:
 *
 *   * An INVITATION the reader may answer -- the study nudge.
 *   * A CONDITION that ends on its own -- the reader's brief being written.
 *     The caller holds the id and dismisses it when the condition lifts,
 *     because a status that outlives its truth is worse than no status. The
 *     timer is the backstop for a caller that never gets to say so.
 *
 * HOW A TOAST LEFT is reported, because it is not always the same event.
 * `StudyNudge` reads "the reader answered" off the toast leaving the stack and
 * stays quiet for the rest of the day on the strength of it -- so a timer that
 * looked like a dismissal would cost a reader their study prompt for the day
 * without them ever having seen it. `lastDismissal` is how a caller tells the
 * reader's act from the clock's.
 */
const DURATION_MS = 5000
/** Long enough to read an invitation and act on it, short enough that the
 *  corner is clear by the time the reader looks back. */
const STICKY_DURATION_MS = 30000
const MAX = 3

export interface Toast {
  id: number
  /** `running` is the accent: work in flight, not a result. */
  kind: 'info' | 'success' | 'error' | 'running'
  text: string
  /**
   * The id the toast is ABOUT -- `brief:2026-08-06`, `run:06a75fb5`.
   *
   * Structural, not something each caller formats: passed here it renders mono
   * and unbreakable, appended to the sentence with an em dash. Inline in
   * `text` it breaks at its hyphens and lands across two lines, because the
   * break inside `brief:2026-08-06` is the ordinary break-after-hyphen rule.
   * Callers must pass ids here, never in the message.
   */
  subject?: string
  /** Optional route the toast links to, e.g. a finished run's brief. */
  to?: string
  toLabel?: string
  /** Given the longer life, not an unlimited one. See the module docstring. */
  sticky?: boolean
  /**
   * At most one toast carries a given key at a time.
   *
   * The companion of `sticky`, and needed for the same reason: only a toast
   * that outlives the call that pushed it can collide with itself. A caller
   * whose component remounts, or one driven by a handler that fires once per
   * failed request, has no vantage point from which to see the copy already
   * on screen -- so the rule lives here, where the stack is.
   */
  key?: string
}

let nextId = 1

/** Why a toast left the stack. `manual` is the reader: the close button, or a
 *  caller dismissing on the reader's behalf when they followed the link. */
export type DismissReason = 'manual' | 'timeout' | 'evicted'

export const useToastsStore = defineStore('toasts', () => {
  const items = ref<Toast[]>([])
  /** The last departure, for a caller that has to tell them apart. Carries the
   *  id so a watcher can check the toast was its own. */
  const lastDismissal = ref<{ id: number; reason: DismissReason } | null>(null)

  function dismiss(id: number, reason: DismissReason = 'manual') {
    if (!items.value.some((t) => t.id === id)) return
    items.value = items.value.filter((t) => t.id !== id)
    lastDismissal.value = { id, reason }
  }

  function push(toast: Omit<Toast, 'id'>) {
    // The keyed toast already on screen stands, text and all, and its id comes
    // back so a caller that did not push it can still dismiss it. A keyed toast
    // names a condition, and a sentence that moves under a reader who has
    // already read it is a different promise from the one the module docstring
    // makes.
    const held = toast.key && items.value.find((t) => t.key === toast.key)
    if (held) return held.id

    const id = nextId++
    items.value.push({ ...toast, id })
    // Newest wins: a burst must not push the current one off screen unseen.
    // Transient toasts are evicted first -- a sticky invitation losing its
    // spot to a burst of "saved" notices would silently unmake the caller's
    // "shown once today" promise.
    while (items.value.length > MAX) {
      const victim = items.value.findIndex((t) => !t.sticky)
      const index = victim === -1 ? 0 : victim
      const [evicted] = items.value.splice(index, 1)
      if (evicted) lastDismissal.value = { id: evicted.id, reason: 'evicted' }
    }
    window.setTimeout(
      () => dismiss(id, 'timeout'),
      toast.sticky ? STICKY_DURATION_MS : DURATION_MS,
    )
    return id
  }

  return { items, lastDismissal, push, dismiss }
})
