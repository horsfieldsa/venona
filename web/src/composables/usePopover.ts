import { onScopeDispose, watch, type Ref } from 'vue'

/**
 * One loose panel at a time, across the whole app.
 *
 * A menu, a drawer or a typeahead list is not a dialog: it does not own the
 * screen, nothing is trapped inside it, and it closes the moment you look
 * somewhere else. What that leaves undecided is what happens when you look at
 * ANOTHER one, and every panel deciding for itself gets it wrong in the same
 * way -- each dismisses on a press outside itself, so the one you pressed opens
 * underneath the one that was already there and both are on screen at once.
 *
 * On a phone that read as the second panel never opening at all: the nav
 * drawer's scrim covers the page while it is open, so the account menu opened
 * INTO it, behind the scrim, and the only visible effect of the tap was the
 * drawer staying put.
 *
 * So opening claims the slot and shuts whatever held it. Deliberately NOT
 * `useDismissable`, which is the `Esc` stack: that answers "what does Escape
 * close", pops one entry, and a menu must not sit on top of an open dialog and
 * eat that dialog's Escape.
 */
const claimed = new Set<Ref<boolean>>()

export function usePopover(open: Ref<boolean>): void {
  watch(open, (isOpen) => {
    if (!isOpen) {
      claimed.delete(open)
      return
    }
    for (const other of claimed) other.value = false
    claimed.clear()
    claimed.add(open)
  })
  // A panel whose component goes away must not leave a dead flag holding the
  // slot: nothing is left to close it, so the next panel to open would find the
  // slot taken and clear a ref with nothing rendered behind it.
  onScopeDispose(() => claimed.delete(open))
}
