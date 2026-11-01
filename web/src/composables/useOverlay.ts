import { onScopeDispose, toRef, watch, type Ref } from 'vue'

import { useFocusTrap } from '@/composables/useFocusTrap'
import { resumeBindings, suspendBindings, useDismissable } from '@/composables/useKeyboard'

/**
 * The three things every overlay in this app does, and the one thing two of
 * them do.
 *
 * `Dialog`, `KeyboardHelp` and `CommandPalette` each carried the same block
 * verbatim: mirror the `open` prop into a ref, trap focus in the panel, and push
 * or pop the dismissable stack. Three copies, and the mirror in all three was a
 * hand-rolled `toRef(props, 'open')` -- `ref` plus a `watch` doing what one
 * builtin does, with an extra reactive hop before `useFocusTrap`'s own
 * `nextTick`.
 *
 * ORDERING IS LOAD-BEARING at the call site. `useFocusTrap` focuses the FIRST
 * focusable after `nextTick`, and `CommandPalette` focuses its input after
 * `nextTick` too -- so the component's own watcher wins only because it is
 * registered second. Call this at the same point in `setup()` that the block it
 * replaces occupied, above any component-specific open-watcher, or focus lands
 * on the wrong element and nothing type-checks it.
 *
 * `onScopeDispose` is the same leak class `useFocusTrap` had, and it matters
 * more here: `useKeyboard`'s own docstring records that a stranded dismissable
 * "silently kills Esc for every dialog and palette in the app until reload".
 */
export interface OverlayOptions {
  /**
   * Suspend the global key bindings while this is open.
   *
   * WHY IT IS A FLAG AND NOT SIMPLY ALWAYS ON. A focus trap parks focus on a
   * `<button>`, which is not editable, so the capture-phase listener still
   * reaches the view behind the scrim: someone opening `?` to find out what `M`
   * does, and pressing `M`, merges the pair underneath it. Every modal overlay
   * wants this. It stays a flag because `useOverlay` is also the right shape for
   * things that are dismissable without being modal, and those must not take the
   * keyboard away from the view they sit over.
   *
   * Scope-0 keys -- Esc, `?` -- stay live through suspension by design, so an
   * overlay that owns the keyboard can still be dismissed by it.
   */
  ownsKeyboard?: boolean
}

export function useOverlay(
  open: Ref<boolean>,
  panel: Ref<HTMLElement | null>,
  close: () => void,
  { ownsKeyboard = false }: OverlayOptions = {},
) {
  useFocusTrap(panel, open)

  const dismissable = useDismissable(close)
  let suspended = false

  watch(open, (isOpen, was) => {
    if (isOpen === was) return
    if (isOpen) {
      dismissable.push()
      if (ownsKeyboard) {
        suspendBindings()
        suspended = true
      }
    } else {
      dismissable.pop()
      if (suspended) {
        resumeBindings()
        suspended = false
      }
    }
  })

  // Unmounted while still open — the case all three call sites missed.
  onScopeDispose(() => {
    if (!open.value) return
    dismissable.pop()
    if (suspended) resumeBindings()
  })
}

/** Re-exported so a call site needs one import rather than two. The overlays all
 *  take `open` as a prop, and `toRef(props, 'open')` is what the hand-rolled
 *  `ref` + `watch` mirror was reimplementing. */
export { toRef }
