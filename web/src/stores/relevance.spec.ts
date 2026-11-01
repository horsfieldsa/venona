/**
 * The follow picker's search box.
 *
 * Two failures that look like a broken search rather than a race: results for a
 * query the reader has already typed past (the shorter query matches more rows,
 * so it answers last and lands on top), and a request per keystroke against an
 * endpoint that scans names.
 *
 * `environment: 'node'` with fake timers — the debounce is the subject.
 */
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useRelevanceStore } from './relevance'

const api = vi.hoisted(() => ({ get: vi.fn() }))
const freshness = vi.hoisted(() => ({
  onFreshness: vi.fn<(domain: string, run: () => void) => () => void>(() => () => {}),
}))

vi.mock('@/composables/useFreshness', () => freshness)

vi.mock('@/api/client', async (importOriginal) => {
  const real = await importOriginal<typeof import('@/api/client')>()
  return { ...real, apiGet: api.get }
})

/** Past the 200ms debounce and whatever microtasks the response needs. */
async function settle() {
  await vi.advanceTimersByTimeAsync(250)
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.useFakeTimers()
  api.get.mockReset()
  freshness.onFreshness.mockClear()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('searchCorpus', () => {
  it('issues one request for a burst of typing', async () => {
    api.get.mockResolvedValue({ entities: [{ id: 'org:a', name: 'Alpha' }] })
    const relevance = useRelevanceStore()

    void relevance.searchCorpus('a')
    void relevance.searchCorpus('an')
    const last = relevance.searchCorpus('and')
    await settle()
    await last

    expect(api.get).toHaveBeenCalledTimes(1)
    expect(api.get).toHaveBeenCalledWith(expect.stringContaining('q=and'))
  })

  it('keeps the results that match what is in the box', async () => {
    // The slow first response resolves last, which is the whole bug.
    let releaseFirst = () => {}
    const held = new Promise<void>((resolve) => {
      releaseFirst = resolve
    })
    api.get.mockImplementation(async (path: string) => {
      if (path.includes('q=an&')) {
        await held
        return { entities: [{ id: 'org:stale', name: 'Stale' }] }
      }
      return { entities: [{ id: 'org:fresh', name: 'Fresh' }] }
    })

    const relevance = useRelevanceStore()
    const first = relevance.searchCorpus('an')
    await vi.advanceTimersByTimeAsync(250)
    const second = relevance.searchCorpus('andes')
    await vi.advanceTimersByTimeAsync(250)
    await second

    releaseFirst()
    await first

    expect(relevance.searchHits.map((h) => h.id)).toEqual(['org:fresh'])
    expect(relevance.searching).toBe(false)
  })

  it('clears on an empty query and stays cleared', async () => {
    let releaseFirst = () => {}
    const held = new Promise<void>((resolve) => {
      releaseFirst = resolve
    })
    api.get.mockImplementation(async () => {
      await held
      return { entities: [{ id: 'org:late', name: 'Late' }] }
    })

    const relevance = useRelevanceStore()
    const first = relevance.searchCorpus('alpha')
    await vi.advanceTimersByTimeAsync(250)

    await relevance.searchCorpus('')
    releaseFirst()
    await first

    // A response in flight when the box was cleared must not refill it.
    expect(relevance.searchHits).toEqual([])
    expect(relevance.searching).toBe(false)
  })

  it('makes no request at all for a query that is only whitespace', async () => {
    const relevance = useRelevanceStore()
    await relevance.searchCorpus('   ')

    expect(api.get).not.toHaveBeenCalled()
  })
})

/**
 * A new shared brief landing under an open tab.
 *
 * The reader's brief derives from the shared one, so new reading moves their
 * payload without anything in this tab touching it. The surface checked once
 * on mount before, and a reader logged in all day did not see theirs until
 * they reloaded.
 */
describe('the shared edition landing under an open tab', () => {
  it('subscribes to the briefs stamp and refreshes when it moves', async () => {
    const relevance = useRelevanceStore()
    const call = freshness.onFreshness.mock.calls.at(-1)
    expect(call?.[0]).toBe('briefs')
    const run = call![1]

    // Nothing loaded yet: the first read belongs to whichever surface asked
    // for it, and firing here would spend on a reader who never opened it.
    run()
    expect(api.get).not.toHaveBeenCalled()

    api.get.mockResolvedValue({ auto_due: false, generating: false, brief: null })
    await relevance.loadForYou()
    api.get.mockClear()

    run()
    await settle()
    expect(api.get).toHaveBeenCalledWith('/briefs/for-you')
  })

  it('joins a run already in flight rather than asking for a second one', async () => {
    const relevance = useRelevanceStore()
    api.get.mockResolvedValue({ auto_due: true, generating: true, brief: null })

    await relevance.refreshForYou()
    // `generating` wins: the store waits on the run the server reports, and
    // never POSTs a duplicate the launcher would only fold anyway.
    expect(relevance.watching).toBe(true)
  })
})
