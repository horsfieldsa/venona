/**
 * The verdict is optimistic and honest: approve removes the row at once, and
 * a failed write puts it back where it was with the error named.
 */
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useLearnQuestionsStore } from './learnQuestions'

import type { LearnQuestion } from '@/types/learning'


function question(id: string): LearnQuestion {
  return {
    id,
    kind: 'qa',
    prompt: `what is ${id}?`,
    answer: 'a thing',
    grounding_article_id: 'doc:1',
    grounding_quote: 'a thing it is',
    choices: null,
    entity_id: null,
    status: 'proposed',
    run_id: 'run:1',
    created_at: '2026-08-23T00:00:00.000Z',
  }
}

function respond(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

describe('the question review store', () => {
  beforeEach(() => setActivePinia(createPinia()))
  afterEach(() => vi.unstubAllGlobals())

  it('an approve removes the row and keeps it removed on success', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === 'POST') return respond({ id: 'question:a', status: 'active' })
        return respond({ items: [question('question:a'), question('question:b')], total: 2 })
      }),
    )
    const store = useLearnQuestionsStore()
    await store.load()
    await store.decide(store.items[0], 'approve')

    expect(store.items.map((q) => q.id)).toEqual(['question:b'])
    expect(store.total).toBe(1)
    expect(store.error).toBeNull()
  })

  it('a failed verdict restores the row in place', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === 'POST') return respond({ detail: 'boom' }, 500)
        return respond({ items: [question('question:a'), question('question:b')], total: 2 })
      }),
    )
    const store = useLearnQuestionsStore()
    await store.load()
    await store.decide(store.items[0], 'reject')

    expect(store.items.map((q) => q.id)).toEqual(['question:a', 'question:b'])
    expect(store.total).toBe(2)
    expect(store.error).toBeTruthy()
  })
})
