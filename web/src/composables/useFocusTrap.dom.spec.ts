// @vitest-environment happy-dom
/**
 * The listener that outlived its overlay.
 *
 * `useFocusTrap` added its `document` keydown handler in the open branch of a
 * watcher and removed it only in the close branch, so an overlay torn down while
 * still open left the handler on `document` for the life of the page and dropped
 * the element it was supposed to return focus to.
 *
 * This is the test that could not be written before: `environment: 'node'` has no
 * `document`, and `ui_smoke` cannot see it either -- the leaked handler
 * early-returns on a null container, so it changes nothing observable. The only
 * way to catch it is to count listeners.
 */
import { effectScope, nextTick, ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useFocusTrap } from './useFocusTrap'

afterEach(() => {
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

/** A panel with two focusables, which is what the trap needs to wrap Tab. */
function panelWith(html: string) {
  const el = document.createElement('div')
  el.innerHTML = html
  document.body.appendChild(el)
  return el
}

describe('useFocusTrap', () => {
  it('removes its document listener when the scope is disposed while still open', async () => {
    const add = vi.spyOn(document, 'addEventListener')
    const remove = vi.spyOn(document, 'removeEventListener')

    const open = ref(false)
    const container = ref<HTMLElement | null>(panelWith('<button>a</button><button>b</button>'))

    const scope = effectScope()
    scope.run(() => useFocusTrap(container, open))

    open.value = true
    await nextTick()
    await nextTick()
    expect(add).toHaveBeenCalledWith('keydown', expect.any(Function))

    // Torn down WITHOUT closing first. This is the case that leaked.
    scope.stop()

    expect(remove).toHaveBeenCalledWith('keydown', expect.any(Function))
    const handler = add.mock.calls.find(([type]) => type === 'keydown')?.[1]
    const removed = remove.mock.calls.find(([type]) => type === 'keydown')?.[1]
    expect(removed).toBe(handler)
  })

  it('gives focus back to where it came from', async () => {
    const outside = document.createElement('button')
    document.body.appendChild(outside)
    outside.focus()
    expect(document.activeElement).toBe(outside)

    const open = ref(false)
    const container = ref<HTMLElement | null>(panelWith('<button>inside</button>'))

    const scope = effectScope()
    scope.run(() => useFocusTrap(container, open))

    open.value = true
    await nextTick()
    await nextTick()
    expect(document.activeElement).not.toBe(outside)

    // On dispose, not just on close: `restoreTo` must survive to here.
    scope.stop()
    expect(document.activeElement).toBe(outside)
  })

  it('still restores focus on the ordinary close path', async () => {
    const outside = document.createElement('button')
    document.body.appendChild(outside)
    outside.focus()

    const open = ref(false)
    const container = ref<HTMLElement | null>(panelWith('<button>inside</button>'))
    effectScope().run(() => useFocusTrap(container, open))

    open.value = true
    await nextTick()
    await nextTick()

    open.value = false
    await nextTick()
    expect(document.activeElement).toBe(outside)
  })
})
