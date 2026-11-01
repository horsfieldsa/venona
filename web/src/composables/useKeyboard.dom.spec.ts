// @vitest-environment happy-dom
/**
 * The three ways a binding can sit in the registry, appear in the `?` overlay,
 * and never fire.
 *
 * All three shipped, and none of them is visible to `vue-tsc` or to
 * `ui_smoke.py`: the binding exists, the overlay lists it, the key is pressed
 * and SOMETHING happens -- just the wrong thing, or nothing. There is no error
 * to catch and no rendering to compare.
 *
 * `installKeyboard` is idempotent by design (a hot reload must not stack a
 * second listener), so it is called ONCE for the file and the per-case bindings
 * go in through `useKeyBindings`, which is what a view uses anyway.
 */
import { createPinia, setActivePinia } from 'pinia'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { effectScope, nextTick, ref } from 'vue'

import { installKeyboard, keyboardGroups, useKeyBindings } from './useKeyboard'
import { useOverlay } from './useOverlay'

import { useSessionStore } from '@/stores/session'

/** A keydown as the browser delivers it: `key` is what the layout produced,
 *  `code` is the physical key. Keeping them separable is the whole point of the
 *  macOS case below. */
function press(init: Partial<KeyboardEventInit> & { key: string }) {
  window.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, ...init }))
}

beforeAll(() => {
  setActivePinia(createPinia())
  installKeyboard({ openPalette: () => {}, openHelp: () => {} })
})

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('shift', () => {
  it('prefers shift+<letter> over the bare letter when both are bound', () => {
    // The exact shape `DefinitionQueue` registers, bare letter FIRST -- which is
    // what made this fail. `canonical` never emitted a `shift` part, so Shift+L
    // matched the combo `l`, found the earlier binding, and spent about $0.13 on
    // a plain grounded lookup instead of re-looking-up.
    const bare = vi.fn()
    const shifted = vi.fn()
    const { remove } = useKeyBindings([
      { keys: ['l'], label: 'Look it up', group: 't', run: bare },
      { keys: ['shift+l'], label: 'Look again', group: 't', run: shifted },
    ])

    press({ key: 'L', code: 'KeyL', shiftKey: true })
    expect(shifted).toHaveBeenCalledOnce()
    expect(bare).not.toHaveBeenCalled()

    remove()
  })

  it('still fires the bare letter when only the bare letter is bound', () => {
    // The fallback, and the reason `candidates` returns a LIST. Shift+A on the
    // brief archived before and must keep archiving: nothing that worked is
    // allowed to stop working to fix the case above.
    const bare = vi.fn()
    const { remove } = useKeyBindings([{ keys: ['a'], label: 'Archive', group: 't', run: bare }])

    press({ key: 'A', code: 'KeyA', shiftKey: true })
    expect(bare).toHaveBeenCalledOnce()

    remove()
  })

  it('leaves shifted punctuation as itself, so ? is still ?', () => {
    // `?` IS shift+`/`. Folding shift into punctuation would make every such
    // binding layout-dependent, which is why the rule is letters only.
    const help = vi.fn()
    const { remove } = useKeyBindings([{ keys: ['?'], label: 'Help', group: 't', run: help }])

    press({ key: '?', code: 'Slash', shiftKey: true })
    expect(help).toHaveBeenCalledOnce()

    remove()
  })
})

describe('alt+digit', () => {
  it('matches the physical key, so alt+1 works on macOS', () => {
    // macOS composes Option+1 into `¡`, Option+2 into `™`. `event.key` is that
    // character, so `alt+1` through `alt+4` had been dead on a Mac since the day
    // they were added -- silently, on the platform nobody here develops on.
    const brief = vi.fn()
    const { remove } = useKeyBindings([
      { keys: ['alt+1'], label: 'Brief', group: 't', run: brief },
    ])

    press({ key: '¡', code: 'Digit1', altKey: true })
    expect(brief).toHaveBeenCalledOnce()

    remove()
  })

  it('still matches on layouts where alt leaves the digit alone', () => {
    const brief = vi.fn()
    const { remove } = useKeyBindings([
      { keys: ['alt+1'], label: 'Brief', group: 't', run: brief },
    ])

    press({ key: '1', code: 'Digit1', altKey: true })
    expect(brief).toHaveBeenCalledOnce()

    remove()
  })
})

describe('operator bindings', () => {
  /** A signed-in reader. `isAdmin()` fails OPEN on an unloaded session, so the
   *  probe has to have answered for this to mean anything -- which is exactly
   *  the state the app is in by the time any of this is reachable. */
  function asReader() {
    useSessionStore().apply({
      authenticated: true,
      setup_required: false,
      relevance_tailored: false,
      timezone: null,
      email: 'reader@example.com',
      role: 'reader',
      user_id: 'u1',
      via: 'session',
      created_at: null,
      display_name: null,
      expires_at: null,
      logout_url: null,
      picture_url: null,
      provider: null,
      billing: null,
    })
  }

  it('does not fire for a reader', () => {
    // Before this, alt+5's route jump ran and the ROUTER bounced it -- so a
    // reader pressing a key the overlay had just offered them landed back on the
    // brief with no explanation.
    asReader()
    const curation = vi.fn()
    const { remove } = useKeyBindings([
      { keys: ['alt+5'], label: 'Go to Curation', group: 'global', operator: true, run: curation },
    ])

    press({ key: '5', code: 'Digit5', altKey: true })
    expect(curation).not.toHaveBeenCalled()

    remove()
  })

  it('falls through to a non-operator binding on the same key', () => {
    // `continue`, not `return`. An admin-only binding must not swallow a key it
    // is not allowed to run.
    asReader()
    const admin = vi.fn()
    const reader = vi.fn()
    const outer = useKeyBindings([{ keys: ['alt+5'], label: 'Reader', group: 't', run: reader }])
    const inner = useKeyBindings([
      { keys: ['alt+5'], label: 'Admin', group: 't', operator: true, run: admin },
    ])

    press({ key: '5', code: 'Digit5', altKey: true })
    expect(admin).not.toHaveBeenCalled()
    expect(reader).toHaveBeenCalledOnce()

    inner.remove()
    outer.remove()
  })

  it('fires for an admin', () => {
    useSessionStore().apply({
      authenticated: true,
      setup_required: false,
      relevance_tailored: false,
      timezone: null,
      email: 'admin@example.com',
      role: 'admin',
      user_id: 'u2',
      via: 'session',
      created_at: null,
      display_name: null,
      expires_at: null,
      logout_url: null,
      picture_url: null,
      provider: null,
      billing: null,
    })
    const curation = vi.fn()
    const { remove } = useKeyBindings([
      { keys: ['alt+5'], label: 'Go to Curation', group: 'global', operator: true, run: curation },
    ])

    press({ key: '5', code: 'Digit5', altKey: true })
    expect(curation).toHaveBeenCalledOnce()

    remove()
  })

  it('is dropped from the ? overlay for a reader', () => {
    // The half that is not about firing. A row for a key that cannot run is a
    // page telling you to press something that does nothing.
    asReader()
    const { remove } = useKeyBindings([
      { keys: ['alt+5'], label: 'Go to Curation', group: 'gated', operator: true, run: () => {} },
      { keys: ['alt+3'], label: 'Go to Archive', group: 'gated', run: () => {} },
    ])

    const gated = keyboardGroups.value.find((g) => g.name === 'gated')
    expect(gated?.bindings.map((b) => b.label)).toEqual(['Go to Archive'])

    remove()
  })
})

/**
 * A keystroke aimed at a modal must not reach the surface behind it.
 *
 * The exposure is real and silent: a focus trap parks focus on a `<button>`,
 * which is not editable, so the capture-phase listener still delivers bare
 * letters to the view under the scrim. Tab to Cancel in the entity picker,
 * press `a`, and the definition underneath is accepted — a write the reader
 * never saw, attributed to a key they aimed somewhere else.
 */
describe('an overlay that owns the keyboard', () => {
  function overlay() {
    const open = ref(false)
    const panel = ref<HTMLElement | null>(document.createElement('div'))
    const scope = effectScope()
    scope.run(() =>
      useOverlay(open, panel, () => (open.value = false), { ownsKeyboard: true }),
    )
    return { open, stop: () => scope.stop() }
  }

  it('suspends the view behind it, and gives the keys back on close', async () => {
    const run = vi.fn()
    const { remove } = useKeyBindings([
      { keys: ['a'], label: 'Accept', group: 'curation', run },
    ])
    const { open, stop } = overlay()

    press({ key: 'a', code: 'KeyA' })
    expect(run).toHaveBeenCalledOnce()

    open.value = true
    await nextTick()
    press({ key: 'a', code: 'KeyA' })
    expect(run).toHaveBeenCalledOnce()

    open.value = false
    await nextTick()
    press({ key: 'a', code: 'KeyA' })
    expect(run).toHaveBeenCalledTimes(2)

    stop()
    remove()
  })

  it('can still be dismissed by Escape while it holds the keyboard', async () => {
    const { open, stop } = overlay()
    open.value = true
    await nextTick()

    press({ key: 'Escape', code: 'Escape' })
    await nextTick()

    // Scope 0 is exempt from suspension by design: an overlay that took Esc
    // away from itself would be a modal nothing can close.
    expect(open.value).toBe(false)

    stop()
  })
})
