import { nextTick, onScopeDispose, watch, type Ref } from 'vue'

/**
 * Keep Tab inside an overlay while it is open, and give focus back when it
 * closes.
 *
 * There was no focus trap anywhere in the app before this, which was fine while
 * there were no overlays. An overlay you can Tab out of into the page behind it
 * moves focus onto things the user cannot see and cannot get back from.
 *
 * This matters MORE since the focus ring came out of `base.css` (see
 * docs/adr/), not less: focus that escapes an overlay is now also
 * invisible, so the trap is the only thing keeping it where it belongs.
 */
const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

export function useFocusTrap(container: Ref<HTMLElement | null>, open: Ref<boolean>) {
  let restoreTo: HTMLElement | null = null

  function onKeydown(event: KeyboardEvent) {
    if (event.key !== 'Tab' || !container.value) return
    const items = [...container.value.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
      (el) => el.offsetParent !== null,
    )
    if (!items.length) return
    const first = items[0]
    const last = items[items.length - 1]
    const active = document.activeElement as HTMLElement | null

    if (event.shiftKey && active === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && active === last) {
      event.preventDefault()
      first.focus()
    }
  }

  /**
   * Teardown, factored out because it has to happen on TWO events and only one
   * of them was covered.
   *
   * Returning focus is the half everyone forgets: without it the next Tab starts
   * from the top of the document, not from where the user was.
   */
  function release() {
    document.removeEventListener('keydown', onKeydown)
    restoreTo?.focus?.()
    restoreTo = null
  }

  watch(open, async (isOpen) => {
    if (isOpen) {
      restoreTo = document.activeElement as HTMLElement | null
      document.addEventListener('keydown', onKeydown)
      await nextTick()
      const target = container.value?.querySelector<HTMLElement>(FOCUSABLE)
      target?.focus()
    } else {
      release()
    }
  })

  /**
   * THE OTHER EVENT: unmounted while still open.
   *
   * The listener was added in the open branch and removed only in the close
   * branch, so an overlay torn down without closing first left a `keydown`
   * handler on `document` for the life of the page, and dropped `restoreTo` on
   * the floor. It is latent today only because all three consumers are teleported
   * and live as long as the app frame does -- which is a fact about the callers,
   * not a property of this composable, and the next caller will not have it.
   *
   * `onScopeDispose` rather than `onUnmounted`: it also covers a bare
   * `effectScope()`, and it needs no `getCurrentInstance()` guard.
   */
  onScopeDispose(release)
}
