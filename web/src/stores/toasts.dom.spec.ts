// @vitest-environment happy-dom
/**
 * The toast contract: everything leaves on its own, an invitation outlives a
 * transient, and HOW it left is reported.
 *
 * That last part is the study nudge's whole promise ("shown once today"): it
 * closes the day off a dismissal, so the clock must not be able to answer for
 * the reader. happy-dom because the store schedules through `window.setTimeout`.
 */
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useToastsStore } from '@/stores/toasts'

describe('toasts', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('a transient toast fades on its own', () => {
    const toasts = useToastsStore()
    toasts.push({ kind: 'info', text: 'saved' })
    expect(toasts.items).toHaveLength(1)
    vi.advanceTimersByTime(5001)
    expect(toasts.items).toHaveLength(0)
  })

  it('a sticky toast outlives a transient, and still leaves', () => {
    const toasts = useToastsStore()
    toasts.push({ kind: 'info', text: 'cards ready', sticky: true })
    // Past the transient's life: an invitation the reader has not reached yet.
    vi.advanceTimersByTime(6000)
    expect(toasts.items).toHaveLength(1)
    // Nothing in this stack is permanent.
    vi.advanceTimersByTime(30_000)
    expect(toasts.items).toHaveLength(0)
  })

  it('reports how a toast left, so the clock cannot answer for the reader', () => {
    const toasts = useToastsStore()

    const timed = toasts.push({ kind: 'info', text: 'cards ready', sticky: true })
    vi.advanceTimersByTime(31_000)
    expect(toasts.lastDismissal).toEqual({ id: timed, reason: 'timeout' })

    const byHand = toasts.push({ kind: 'info', text: 'cards ready', sticky: true })
    toasts.dismiss(byHand)
    expect(toasts.lastDismissal).toEqual({ id: byHand, reason: 'manual' })
  })

  it('reports an eviction as an eviction', () => {
    const toasts = useToastsStore()
    const first = toasts.push({ kind: 'success', text: 'one' })
    toasts.push({ kind: 'success', text: 'two' })
    toasts.push({ kind: 'success', text: 'three' })
    toasts.push({ kind: 'success', text: 'four' })
    expect(toasts.lastDismissal).toEqual({ id: first, reason: 'evicted' })
  })

  it('a toast dismissed twice reports once', () => {
    const toasts = useToastsStore()
    const id = toasts.push({ kind: 'info', text: 'saved' })
    toasts.dismiss(id)
    toasts.lastDismissal = null
    // The timer still fires for a toast a caller already dismissed; it must not
    // report a second departure for something that has already left.
    vi.advanceTimersByTime(6000)
    expect(toasts.lastDismissal).toBeNull()
  })

  it('a keyed toast is pushed once and returns the id already on screen', () => {
    const toasts = useToastsStore()
    // The study nudge's own shape: the component holding it is remounted by
    // the brief on every load, and each new instance pushes again.
    const first = toasts.push({ kind: 'info', text: 'cards ready', sticky: true, key: 'k' })
    const second = toasts.push({ kind: 'info', text: 'cards ready', sticky: true, key: 'k' })
    expect(toasts.items).toHaveLength(1)
    expect(second).toBe(first)

    // Answered, so the invitation may be made again -- tomorrow, or after the
    // condition returns.
    toasts.dismiss(first)

    expect(toasts.push({ kind: 'info', text: 'cards ready', sticky: true, key: 'k' })).not.toBe(
      first,
    )
    expect(toasts.items).toHaveLength(1)
  })

  it('a burst evicts transients before a sticky invitation', () => {
    const toasts = useToastsStore()
    toasts.push({ kind: 'info', text: 'invite', sticky: true })
    toasts.push({ kind: 'success', text: 'one' })
    toasts.push({ kind: 'success', text: 'two' })
    toasts.push({ kind: 'success', text: 'three' })
    expect(toasts.items).toHaveLength(3)
    expect(toasts.items.map((t) => t.text)).toEqual(['invite', 'two', 'three'])
  })
})
