/**
 * The card cache's two survival properties, both learned on the curation
 * queue during a live run:
 *
 * 1. `invalidate` is stale-while-revalidate, not a wipe. It fires on every
 *    `entities` freshness tick — every few seconds while a run writes — and
 *    clearing the cards made everything rendering from the cache vanish into
 *    a loading state at that cadence.
 *
 * 2. Only a 404 marks an id `missing`. A transient failure cached as missing
 *    made the card unfetchable for the rest of the session, which rendered
 *    as a pair card whose spinner nothing would ever resolve.
 */
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useEntitiesStore } from './entities'

const CARD = { id: 'org:acme', name: 'Acme', type: 'org' }

beforeEach(() => {
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('useEntitiesStore', () => {
  it('keeps serving a stale card through invalidate, then refetches it', async () => {
    const fetchMock = vi.fn(async () => Response.json(CARD))
    vi.stubGlobal('fetch', fetchMock)
    const entities = useEntitiesStore()

    await entities.fetchOne('org:acme')
    expect(entities.get('org:acme')).toMatchObject({ name: 'Acme' })
    expect(entities.isFresh('org:acme')).toBe(true)

    entities.invalidate()
    // The card is still on screen; only its freshness is gone.
    expect(entities.get('org:acme')).toMatchObject({ name: 'Acme' })
    expect(entities.isFresh('org:acme')).toBe(false)

    await entities.fetchOne('org:acme')
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(entities.isFresh('org:acme')).toBe(true)
  })

  it('retries after a transient failure; only a 404 is terminal', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('boom', { status: 500 })),
    )
    const entities = useEntitiesStore()
    expect(await entities.fetchOne('org:acme')).toBeNull()

    // The 500 must not have been cached as "no such card".
    vi.stubGlobal('fetch', vi.fn(async () => Response.json(CARD)))
    expect(await entities.fetchOne('org:acme')).toMatchObject({ name: 'Acme' })

    // A real 404 is terminal until the next invalidate.
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => Response.json({ detail: 'no card' }, { status: 404 })),
    )
    expect(await entities.fetchOne('org:gone')).toBeNull()
    const after = vi.fn(async () => Response.json(CARD))
    vi.stubGlobal('fetch', after)
    expect(await entities.fetchOne('org:gone')).toBeNull()
    expect(after).not.toHaveBeenCalled()
  })
})

/**
 * The cache grew forever.
 *
 * Every hover on every brief, graph node and lookup row added a card and
 * removed none, so a long reading session accumulated the corpus one card at a
 * time. Nothing surfaces it: the app stays correct and simply uses more memory
 * until the tab is reloaded.
 */
describe('the card cache is bounded', () => {
  /** `prefetch` is the batch path, which is how cards actually arrive. */
  async function fill(count: number, offset = 0) {
    const ids = Array.from({ length: count }, (_, i) => `org:e${i + offset}`)
    // Echoes back exactly what was asked for. `cards` is keyed BY ID, and the
    // endpoint caps at 100 per request, so the store issues several slices.
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init?: RequestInit) => {
        const body = typeof init?.body === 'string' ? init.body : '{}'
        const asked = (JSON.parse(body) as { ids: string[] }).ids
        return Response.json({
          cards: Object.fromEntries(asked.map((id) => [id, { id, name: id, type: 'org' }])),
          missing: [],
        })
      }),
    )
    await useEntitiesStore().prefetch(ids)
    return ids
  }

  it('stops at the cap and drops the oldest fetch first', async () => {
    const entities = useEntitiesStore()
    const first = await fill(400)
    const second = await fill(300, 400)

    expect(entities.cards.size).toBeLessThanOrEqual(500)
    // The most recent batch is the working set and survives whole.
    for (const id of second.slice(-50)) expect(entities.get(id)).not.toBeNull()
    // The oldest fetch is what went.
    expect(entities.get(first[0])).toBeNull()
  })

  it('evicts from fetchedAt too, so an evicted card is not treated as fresh', async () => {
    const entities = useEntitiesStore()
    const first = await fill(400)
    await fill(300, 400)

    expect(entities.isFresh(first[0])).toBe(false)
  })
})
