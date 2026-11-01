// @vitest-environment happy-dom
/**
 * The sentinel that must fire twice without moving.
 *
 * IntersectionObserver only reports edge crossings. When a fetched page is too
 * short to push the sentinel out of view, no new crossing ever happens — so a
 * composable that fires from the observer callback alone loads exactly one
 * page and then sits at the bottom of a half-filled viewport doing nothing.
 * The re-fire on `disabled` releasing is the behaviour under test, and no
 * browser is involved: the observer is stubbed and driven by hand, against a
 * bare element ref (`vitest.config.ts`: no component mounting here).
 */
import { effectScope, nextTick, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useInfiniteScroll } from './useInfiniteScroll'

type Callback = (entries: Array<{ isIntersecting: boolean }>) => void

const observers: Array<{
  callback: Callback
  options: IntersectionObserverInit | undefined
  observed: Element[]
  disconnected: boolean
}> = []

class FakeObserver {
  entry: (typeof observers)[number]
  constructor(callback: Callback, options?: IntersectionObserverInit) {
    this.entry = { callback, options, observed: [], disconnected: false }
    observers.push(this.entry)
  }
  observe(el: Element) {
    this.entry.observed.push(el)
  }
  disconnect() {
    this.entry.disconnected = true
  }
  unobserve() {}
  takeRecords() {
    return []
  }
}

beforeEach(() => {
  observers.length = 0
  vi.stubGlobal('IntersectionObserver', FakeObserver)
})
afterEach(() => vi.unstubAllGlobals())

function intersect(value: boolean) {
  observers.at(-1)!.callback([{ isIntersecting: value }])
}

function mount(onReach: () => void, disabled?: () => boolean) {
  const root = ref<HTMLElement | null>(document.createElement('div'))
  const sentinel = ref<HTMLElement | null>(document.createElement('div'))
  const scope = effectScope()
  scope.run(() => useInfiniteScroll(root, sentinel, onReach, { disabled }))
  return { root, sentinel, scope }
}

describe('useInfiniteScroll', () => {
  it('observes the sentinel against the passed root with a prefetch margin', async () => {
    const { root, sentinel } = mount(() => {})
    await nextTick()

    const observer = observers.at(-1)!
    expect(observer.observed).toEqual([sentinel.value])
    expect(observer.options?.root).toBe(root.value)
    expect(observer.options?.rootMargin).toContain('200px')
  })

  it('fires when the sentinel comes into view, and not while disabled', async () => {
    const onReach = vi.fn()
    const disabled = ref(true)
    mount(onReach, () => disabled.value)
    await nextTick()

    intersect(true)
    await nextTick()
    expect(onReach).not.toHaveBeenCalled()

    disabled.value = false
    await nextTick()
    expect(onReach).toHaveBeenCalledTimes(1)
  })

  it('fires again when loading finishes with the sentinel still in view', async () => {
    // The short-page case: one fetch did not push the sentinel out, so the
    // observer reports nothing new — the release of `disabled` is the trigger.
    const loading = ref(false)
    const onReach = vi.fn(() => {
      loading.value = true
    })
    mount(onReach, () => loading.value)
    await nextTick()

    intersect(true)
    await nextTick()
    expect(onReach).toHaveBeenCalledTimes(1)

    loading.value = false
    await nextTick()
    expect(onReach).toHaveBeenCalledTimes(2)
  })

  it('goes quiet once the sentinel leaves view', async () => {
    const onReach = vi.fn()
    mount(onReach)
    await nextTick()

    intersect(true)
    await nextTick()
    intersect(false)
    await nextTick()

    expect(onReach).toHaveBeenCalledTimes(1)
  })

  it('re-observes when the sentinel element is swapped, and disconnects on dispose', async () => {
    // The sentinel lives under a `v-if="canLoadMore"`, so the element comes and
    // goes; a stale observation would watch a detached node forever.
    const onReach = vi.fn()
    const { sentinel, scope } = mount(onReach)
    await nextTick()
    const first = observers.at(-1)!

    sentinel.value = document.createElement('div')
    await nextTick()
    const second = observers.at(-1)!
    expect(first.disconnected).toBe(true)
    expect(second.observed).toEqual([sentinel.value])

    scope.stop()
    expect(second.disconnected).toBe(true)
  })
})
