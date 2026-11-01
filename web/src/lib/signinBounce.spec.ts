/**
 * The guard on the login page's automatic sign-in.
 *
 * A unit test rather than a mounted view, and a `Storage` stub rather than the
 * real one, for the same reason `useConfigDocs.spec.ts` stubs it: happy-dom
 * does not implement web storage, and the decision under test is arithmetic on
 * a stored timestamp, not rendering.
 */
import { beforeEach, describe, expect, it } from 'vitest'

import { BOUNCE_COOLDOWN_MS, BOUNCE_KEY, bouncedRecently, rememberBounce } from './signinBounce'

function fakeStore(initial: Record<string, string> = {}): Storage {
  const data = new Map(Object.entries(initial))
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => void data.set(key, value),
    removeItem: (key: string) => void data.delete(key),
    clear: () => data.clear(),
    key: (index: number) => [...data.keys()][index] ?? null,
    get length() {
      return data.size
    },
  }
}

const NOW = 1_800_000_000_000

describe('the automatic sign-in guard', () => {
  let store: Storage

  beforeEach(() => {
    store = fakeStore()
  })

  it('lets a first arrival bounce', () => {
    expect(bouncedRecently(store, NOW)).toBe(false)
  })

  it('⭐ refuses a second bounce moments later', () => {
    // The lockout: a reload, a second tab or a half-finished round trip mounts
    // the page again, and each mount spends one of a budget the visitor needs
    // two of to get in at all.
    rememberBounce(store, NOW)

    expect(bouncedRecently(store, NOW + 1_000)).toBe(true)
    expect(store.getItem(BOUNCE_KEY)).toBe(String(NOW))
  })

  it('bounces again once the cooldown has passed', () => {
    // A tab whose session expires hours later should still find its own way in.
    rememberBounce(store, NOW)

    expect(bouncedRecently(store, NOW + BOUNCE_COOLDOWN_MS + 1)).toBe(false)
  })

  it('treats no storage as a first arrival', () => {
    // Site data blocked. One redirect is right there; it takes a second to loop.
    rememberBounce(null, NOW)

    expect(bouncedRecently(null, NOW)).toBe(false)
  })

  it('ignores a stored value that is not a timestamp', () => {
    for (const junk of ['', 'soon', '0', '-1']) {
      expect(bouncedRecently(fakeStore({ [BOUNCE_KEY]: junk }), NOW)).toBe(false)
    }
  })

  it('survives a store that throws on every access', () => {
    const hostile = {
      getItem: () => {
        throw new Error('denied')
      },
      setItem: () => {
        throw new Error('denied')
      },
    } as unknown as Storage

    expect(() => rememberBounce(hostile, NOW)).not.toThrow()
    expect(bouncedRecently(hostile, NOW)).toBe(false)
  })
})
