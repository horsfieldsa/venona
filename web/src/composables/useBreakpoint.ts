/**
 * Reactive width flags (ADR 0152).
 *
 * For the few places layout cannot be a CSS concern -- the graph forces its
 * rail tracks to zero and swaps its hint copy, neither of which a media query
 * can reach because the tracks arrive as inline custom properties; and
 * `FilterField` has to CLOSE its expanded panel when the window grows past the
 * width that collapsed it, which is state rather than presentation.
 *
 * The pixel values are `--breakpoint-xs`, `--breakpoint-sm` and
 * `--breakpoint-lg` in theme.css.
 * They appear literally here for the same reason 480 appears literally in
 * tokens.css's `--edge` collapse: neither a media query nor `matchMedia` can
 * read a token, and `@theme inline` emits no runtime custom property to ask
 * for. The copies carry the numbers together.
 */
import { onScopeDispose, ref, type Ref } from 'vue'

function useMaxWidth(px: number): Ref<boolean> {
  const matches = ref(false)
  if (typeof window === 'undefined' || !window.matchMedia) return matches
  const media = window.matchMedia(`(max-width: ${px}px)`)
  matches.value = media.matches
  const onChange = (e: MediaQueryListEvent): void => {
    matches.value = e.matches
  }
  media.addEventListener('change', onChange)
  onScopeDispose(() => media.removeEventListener('change', onChange))
  return matches
}

/** Below `--breakpoint-xs`: the phone tier, where the tap rung applies. */
export function useIsPhone(): Ref<boolean> {
  return useMaxWidth(480)
}

/** Below `--breakpoint-sm`: where a worded control collapses to its glyph. */
export function useIsCompact(): Ref<boolean> {
  return useMaxWidth(720)
}

/**
 * Below `--breakpoint-lg`: where the prose grid has no side rail at all.
 *
 * `ViewShell` already handles the LAYOUT there in CSS. This is for the one
 * thing a media query cannot decide -- whether to offer a control at all. The
 * brief's focus switch buys its room by folding that rail, so below this width
 * it would be a switch with nothing to do and no way back out of what it did.
 */
export function useIsRailless(): Ref<boolean> {
  return useMaxWidth(1180)
}
