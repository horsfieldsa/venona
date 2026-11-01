/**
 * Which input device last moved focus, published as `data-modality` on <html>.
 *
 * WHY THIS EXISTS AT ALL.
 *
 * A global `:focus-visible` ring was removed on 2026-08-08 and the decision
 * recorded that it must not come back by accident. The mechanism, restated
 * because it is the entire reason for this file: Chromium tracks input modality
 * per FRAME, not per focus event. Once any key has been pressed anywhere in the
 * document, `:focus-visible` matches on every subsequent focus whatever caused
 * it. This app sets that flag within seconds -- `mod+k`, `alt+1..4` and Escape
 * are global bindings -- so returning to the window, which makes the browser
 * re-focus `document.activeElement`, brought back a ring around a link the user
 * had CLICKED. `useFocusTrap`'s restore after the palette closes hits the same
 * path. No selector can separate the two cases, because by the time CSS runs the
 * browser has already decided they are the same case.
 *
 * That decision named the way back: track modality ourselves and gate the ring
 * on it. The aqua refresh asked for a focus affordance, which is what makes the
 * machinery worth its cost now -- it was turned down before only because there
 * was no ring to protect.
 *
 * WHY `Tab` AND THE ARROWS, AND NOT ANY KEY.
 *
 * Setting `key` on any keydown would reproduce the original bug exactly, just in
 * our own state instead of Chromium's: `mod+k` would arm it, and the next
 * pointer-driven focus would ring. Only keys that actually MOVE focus count. A
 * shortcut that opens a panel is not the user navigating by keyboard, and the
 * panel's own caret already says where they are.
 */

/** Keys that move focus. `Tab` is the canonical one; the arrows matter because
 *  the graph's mini-map and the palette's result list are both arrow-driven. */
const FOCUS_KEYS = new Set(['Tab', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End'])

export type Modality = 'key' | 'pointer'

function set(modality: Modality): void {
  // Written even when unchanged would be a no-op anyway; guarding avoids
  // touching the DOM on every pointerdown in a drag.
  if (document.documentElement.dataset.modality !== modality) {
    document.documentElement.dataset.modality = modality
  }
}

/**
 * Start tracking. Idempotent, and safe to call before mount.
 *
 * Both listeners are capture-phase: a component that calls `stopPropagation()`
 * on a keydown -- `useKeyboard` does, for handled bindings -- must not be able
 * to leave the modality stale, because a stale `key` is the bug this prevents.
 */
export function trackFocusModality(): () => void {
  const onPointer = () => set('pointer')
  const onKey = (event: KeyboardEvent) => {
    if (FOCUS_KEYS.has(event.key)) set('key')
  }

  window.addEventListener('pointerdown', onPointer, { capture: true })
  window.addEventListener('keydown', onKey, { capture: true })

  // Default to pointer. An unset attribute draws no ring, which is the safe
  // side of the failure: a missing ring is the state the app shipped in for a
  // week, and a wrong ring is what the 2026-08-08 entry is about.
  set('pointer')

  return () => {
    window.removeEventListener('pointerdown', onPointer, { capture: true })
    window.removeEventListener('keydown', onKey, { capture: true })
  }
}
