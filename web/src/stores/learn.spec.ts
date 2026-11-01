/**
 * The session's two promises: loading is guarded (a second mount refetches
 * nothing), and a grade is optimistic — the session advances immediately,
 * and a failed write re-queues the card at the tail instead of rolling the
 * learner back.
 */
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useLearnStore } from './learn'

import type { DeckCard } from '@/types/learning'


function card(key: string): DeckCard {
  return {
    card_key: key,
    namespace: 'def',
    format: 'flashcard',
    front: key,
    reps: 0,
    // Every field the wire carries, because the schema says the KEY is always
    // present even where the value is null. A fixture that omits them tests a
    // shape the server never sends.
    answer: null,
    card: null,
    choices: null,
    due: null,
    entity_id: null,
    grounding: null,
    state: null,
  }
}

function queuePayload(keys: string[]) {
  return {
    due: [],
    new: keys.map(card),
    counts: { due: 0, new_remaining: keys.length, reviewed_today: 0, new_today: 0 },
    streak: 0,
    daily_goal: 30,
  }
}

function respond(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

describe('the learn store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('loads once and holds the session', async () => {
    const fetchMock = vi.fn(async () => respond(queuePayload(['card:def:a', 'card:def:b'])))
    vi.stubGlobal('fetch', fetchMock)

    const learn = useLearnStore()
    await learn.loadSession()
    await learn.loadSession()

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(learn.session.map((c) => c.card_key)).toEqual(['card:def:a', 'card:def:b'])
    expect(learn.current?.card_key).toBe('card:def:a')
  })

  it('advances optimistically and keeps a successful grade', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'POST') {
        return respond({
          card_key: 'card:def:a',
          state: 'review',
          due: '2026-09-01T00:00:00.000Z',
          interval_days: 3,
          reps: 1,
          lapses: 0,
        })
      }
      return respond(queuePayload(['card:def:a', 'card:def:b']))
    })
    vi.stubGlobal('fetch', fetchMock)

    const learn = useLearnStore()
    await learn.loadSession()
    await learn.submitReview(learn.current!, 3)

    expect(learn.current?.card_key).toBe('card:def:b')
    expect(learn.reviewError).toBeNull()
    expect(learn.reviewedToday).toBe(1)
    expect(learn.session).toHaveLength(2)
  })

  it('re-queues the card at the tail when the write fails', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'POST') return respond({ detail: 'boom' }, 500)
      return respond(queuePayload(['card:def:a', 'card:def:b']))
    })
    vi.stubGlobal('fetch', fetchMock)

    const learn = useLearnStore()
    await learn.loadSession()
    await learn.submitReview(learn.current!, 3)

    // Still moved forward — the learner is not blocked on the failure…
    expect(learn.current?.card_key).toBe('card:def:b')
    // …but the card returns at the tail, the count is honest, and the
    // failure is named.
    expect(learn.session.map((c) => c.card_key)).toEqual([
      'card:def:a',
      'card:def:b',
      'card:def:a',
    ])
    expect(learn.reviewedToday).toBe(0)
    expect(learn.reviewError).toBeTruthy()
  })

  it('a failed load lets the next mount retry', async () => {
    const fetchMock = vi.fn(async () => respond({ detail: 'down' }, 500))
    vi.stubGlobal('fetch', fetchMock)

    const learn = useLearnStore()
    await learn.loadSession()
    expect(learn.error).toBeTruthy()

    vi.stubGlobal('fetch', vi.fn(async () => respond(queuePayload(['card:def:a']))))
    await learn.loadSession()
    expect(learn.session).toHaveLength(1)
  })
})
