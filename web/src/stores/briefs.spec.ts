/**
 * The archive search, and the race it is built around.
 *
 * The filter is debounced and keystroke-driven, so requests overlap. The one
 * that matters is SLOW-FIRST, FAST-SECOND: `and` matches far more of the corpus
 * than `anduril` and can easily answer after it. On localhost it never happens
 * and by hand it is not reproducible, which is exactly the shape of bug
 * `vue-tsc --noEmit` and a manual pass both miss — the types are identical
 * whether the store checks which query an answer belongs to or not.
 *
 * `useStaleGuard` already covers the generic version of this. These cover THIS
 * store's variant, which discriminates on the echoed `query` field rather than
 * on a claim ticket, because the server tells us what it answered.
 */
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useBriefsStore } from './briefs'

/** `fetch` may be called with a `Request` rather than a URL string -- which is
 *  exactly how `openapi-fetch` calls it. `String(aRequest)` is
 *  `"[object Request]"`, so normalise before parsing. */
function urlOf(input: RequestInfo | URL): string {
  return input instanceof Request ? input.url : String(input)
}

function results(query: string, ids: string[]) {
  return {
    query,
    entity: null,
    total: ids.length,
    truncated: false,
    items: ids.map((id) => ({
      brief: {
        id,
        date: '2026-08-08',
        title: id,
        dek: null,
        block_count: 1,
        run_id: null,
        generated_at: '2026-08-08T00:00:00.000Z',
      },
      matches: [{ where: 'body' as const, item_id: null, headline: null, snippet: '…x…' }],
      match_count: 1,
    })),
  }
}

/** Answers `q` after `delay` ms, so a test can make the SHORTER query slower. */
function delayed(table: Record<string, { ids: string[]; delay: number }>) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const q = new URL(urlOf(input), 'https://t').searchParams.get('q') ?? ''
    const entry = table[q]
    if (entry) await new Promise((r) => setTimeout(r, entry.delay))
    return new Response(JSON.stringify(results(q, entry?.ids ?? [])), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })
  })
}

beforeEach(() => setActivePinia(createPinia()))
afterEach(() => vi.restoreAllMocks())

describe('prev/next arrows', () => {
  it('reads neighbours from the brief payload, not from the paged index', async () => {
    // The index holds only what the reader scrolled to; a deep link lands on a
    // brief the window has never seen, and its arrows must still work.
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              id: 'brief:2026-01-01:deep',
              date: '2026-01-01',
              title: 'x',
              generated_at: '2026-01-01T00:00:00.000Z',
              sections: [],
              older_id: 'brief:2025-12-31:older',
              newer_id: 'brief:2026-01-02:newer',
            }),
            { status: 200, headers: { 'content-type': 'application/json' } },
          ),
      ),
    )
    const store = useBriefsStore()

    await store.load('brief:2026-01-01:deep')

    expect(store.index).toHaveLength(0)
    expect(store.neighbours).toEqual({
      older: 'brief:2025-12-31:older',
      newer: 'brief:2026-01-02:newer',
    })
  })
})

/**
 * Where an arrow lands.
 *
 * `/` and `/briefs/<the newest id>` render the same document and are not the
 * same page: the reader's own brief is offered beside the shared one on `/`
 * alone, and so is the line saying when the next one lands. Both are keyed on
 * the route carrying no id.
 *
 * Every way of reaching a brief addressed it by id, arrows included. So a
 * reader who pressed left off `/` and then pressed right to come back landed on
 * a page that looked correct and had silently lost the "for you" control, with
 * nothing in the arrows able to return them to it.
 */
describe('pathFor', () => {
  const index = [{ id: 'brief:2026-09-01:aaa' }, { id: 'brief:2026-08-21:bbb' }] as never

  it('sends the newest brief home, where its controls are', () => {
    const store = useBriefsStore()
    store.index = index
    expect(store.pathFor('brief:2026-09-01:aaa')).toBe('/')
  })

  it('addresses every older brief by id', () => {
    const store = useBriefsStore()
    store.index = index
    expect(store.pathFor('brief:2026-08-21:bbb')).toBe('/briefs/brief%3A2026-08-21%3Abbb')
  })

  /* An id is a URL segment and carries colons. Unencoded it still routes, which
   * is why this is worth pinning rather than assuming. */
  it('encodes the id', () => {
    expect(useBriefsStore().pathFor('brief:2026-08-21:bbb')).toContain('%3A')
  })

  /* The index is a paged window and a deep link can arrive before it loads.
   * Addressing the brief by id is the same document either way. */
  it('falls back to the id when nothing is known to be newest', () => {
    expect(useBriefsStore().pathFor('brief:2026-09-01:aaa')).toBe(
      '/briefs/brief%3A2026-09-01%3Aaaa',
    )
  })
})

describe('archive search', () => {
  it('discards a slow answer for a query the user has already changed', async () => {
    vi.stubGlobal(
      'fetch',
      delayed({
        and: { ids: ['brief:many', 'brief:more'], delay: 40 },
        anduril: { ids: ['brief:one'], delay: 5 },
      }),
    )
    const briefs = useBriefsStore()

    const slow = briefs.runSearch('and')
    const fast = briefs.runSearch('anduril')
    await Promise.all([fast, slow])

    // The user typed `anduril`; `and` came back afterwards with more rows. What
    // is on screen must be what they asked for last.
    expect(briefs.search?.query).toBe('anduril')
    expect(briefs.search?.items?.map((h) => h.brief.id)).toEqual(['brief:one'])
  })

  it('keeps the spinner up when an OLD request answers while a new one is out', async () => {
    // The other ordering, and the one an unconditional `searching = false`
    // gets wrong: the superseded query answers FIRST. Clearing the flag there
    // hides the spinner while the request the user is actually waiting on is
    // still in flight, which reads as "no results" rather than "still working".
    vi.stubGlobal(
      'fetch',
      delayed({
        and: { ids: [], delay: 5 },
        anduril: { ids: ['brief:one'], delay: 60 },
      }),
    )
    const briefs = useBriefsStore()

    const stale = briefs.runSearch('and')
    const current = briefs.runSearch('anduril')

    await stale
    expect(briefs.searching).toBe(true)

    await current
    expect(briefs.searching).toBe(false)
    expect(briefs.search?.query).toBe('anduril')
  })

  it('clears the results when the box is emptied, without a request', async () => {
    const fetchMock = delayed({ anduril: { ids: ['brief:one'], delay: 0 } })
    vi.stubGlobal('fetch', fetchMock)
    const briefs = useBriefsStore()
    await briefs.runSearch('anduril')
    expect(briefs.search).not.toBeNull()

    await briefs.runSearch('')

    // Null, not an empty result set: `ArchiveView` falls back to the full index
    // when this is null, and an empty result would render "nothing matched" over
    // an archive the reader has not filtered.
    expect(briefs.search).toBeNull()
    expect(briefs.searching).toBe(false)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('treats a whitespace-only query as an empty one', async () => {
    const fetchMock = delayed({})
    vi.stubGlobal('fetch', fetchMock)
    const briefs = useBriefsStore()

    await briefs.runSearch('   ')

    expect(briefs.search).toBeNull()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('reports a failure and does not leave stale rows on screen', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('nope', { status: 500 })),
    )
    const briefs = useBriefsStore()

    await briefs.runSearch('anduril')

    expect(briefs.indexError).toBeTruthy()
    // Keeping the previous results under an error message would say "these are
    // your matches" about a search that did not run.
    expect(briefs.search).toBeNull()
    expect(briefs.searching).toBe(false)
  })
})
