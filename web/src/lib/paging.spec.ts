/**
 * The paged-list state machine, driven through injected fetchers.
 *
 * Every case is an ordering or identity rule the types cannot see: a page
 * appended twice, a slow response landing after a filter change, a `total`
 * that overcounts what the server will actually hand over. All three have a
 * shipped bug behind them somewhere in this repo's history.
 */
import { describe, expect, it, vi } from 'vitest'

import { createPagedList, type Page } from './paging'

interface Row {
  id: string
}

function rows(from: number, count: number): Row[] {
  return Array.from({ length: count }, (_, i) => ({ id: `row:${from + i}` }))
}

/** A fetcher over a fixed server-side list of `size` rows. */
function serverOf(size: number) {
  const calls: Array<{ limit: number; offset: number }> = []
  const all = rows(0, size)
  return {
    calls,
    fetchPage: vi.fn(async (limit: number, offset: number): Promise<Page<Row>> => {
      calls.push({ limit, offset })
      return { items: all.slice(offset, offset + limit), total: size }
    }),
  }
}

describe('createPagedList', () => {
  it('appends on loadMore rather than replacing, at offset = items.length', async () => {
    const server = serverOf(60)
    const list = createPagedList<Row>({ page: 25, key: (r) => r.id, fetchPage: server.fetchPage })

    await list.refresh()
    await list.loadMore()

    expect(list.items.value).toHaveLength(50)
    expect(server.calls).toEqual([
      { limit: 25, offset: 0 },
      { limit: 25, offset: 25 },
    ])
    expect(list.canLoadMore.value).toBe(true)
  })

  it('no-ops a loadMore fired while one is already in flight', async () => {
    // The double-append: the sentinel, the button and a view watcher can all
    // ask at once, and two fetches at the same offset return the same page.
    const server = serverOf(60)
    const list = createPagedList<Row>({ page: 25, key: (r) => r.id, fetchPage: server.fetchPage })
    await list.refresh()

    await Promise.all([list.loadMore(), list.loadMore(), list.loadMore()])

    expect(list.items.value).toHaveLength(50)
    expect(server.fetchPage).toHaveBeenCalledTimes(2)
  })

  it('de-duplicates by key when pages overlap', async () => {
    // Offset paging over a set something else is inserting into: the window
    // slides and the same row arrives on two pages.
    let call = 0
    const list = createPagedList<Row>({
      page: 3,
      key: (r) => r.id,
      fetchPage: async () => {
        call += 1
        return call === 1
          ? { items: rows(0, 3), total: 6 }
          : { items: [{ id: 'row:2' }, ...rows(3, 2)], total: 6 }
      },
    })

    await list.refresh()
    await list.loadMore()

    expect(list.items.value.map((r) => r.id)).toEqual([
      'row:0', 'row:1', 'row:2', 'row:3', 'row:4',
    ])
  })

  it('marks the list exhausted when a page adds nothing new, even under a lying total', async () => {
    // `total` can overcount what the server will hand over — conflicts drop
    // moot rows after the window — and `length < total` alone would refetch
    // forever at the bottom of the scroll.
    const list = createPagedList<Row>({
      page: 3,
      key: (r) => r.id,
      fetchPage: async (_limit, offset) =>
        offset === 0 ? { items: rows(0, 3), total: 10 } : { items: [], total: 10 },
    })

    await list.refresh()
    expect(list.canLoadMore.value).toBe(true)

    await list.loadMore()
    expect(list.items.value).toHaveLength(3)
    expect(list.canLoadMore.value).toBe(false)
  })

  it('drops a superseded response instead of letting it overwrite the newer one', async () => {
    // Filter changed mid-flight: the superseded query's rows must never land.
    let release: (page: Page<Row>) => void = () => {}
    const slow = new Promise<Page<Row>>((resolve) => {
      release = resolve
    })
    let call = 0
    const list = createPagedList<Row>({
      page: 25,
      key: (r) => r.id,
      fetchPage: () => {
        call += 1
        return call === 1 ? slow : Promise.resolve({ items: rows(100, 2), total: 2 })
      },
    })

    const first = list.refresh()
    await list.refresh()
    release({ items: rows(0, 25), total: 99 })
    await first

    expect(list.items.value.map((r) => r.id)).toEqual(['row:100', 'row:101'])
    expect(list.total.value).toBe(2)
    expect(list.loading.value).toBe(false)
  })

  it('revalidates the loaded window in page-sized chunks and swaps it whole', async () => {
    // The freshness path: a page-0 refresh would truncate the buffer someone
    // scrolled to build, and one big request would trip the server's `le=` cap.
    const server = serverOf(80)
    const list = createPagedList<Row>({ page: 25, key: (r) => r.id, fetchPage: server.fetchPage })
    await list.refresh()
    await list.loadMore()
    await list.loadMore()
    expect(list.items.value).toHaveLength(75)
    server.calls.length = 0

    await list.revalidate()

    expect(list.items.value).toHaveLength(75)
    expect(server.calls.every((c) => c.limit === 25)).toBe(true)
    expect(server.calls.map((c) => c.offset)).toEqual([0, 25, 50])
  })

  it('stops a revalidate early when the set shrank underneath it', async () => {
    let size = 80
    const list = createPagedList<Row>({
      page: 25,
      key: (r) => r.id,
      fetchPage: async (limit, offset) => ({
        items: rows(0, size).slice(offset, offset + limit),
        total: size,
      }),
    })
    await list.refresh()
    await list.loadMore()
    await list.loadMore()
    expect(list.items.value).toHaveLength(75)

    size = 30
    await list.revalidate()

    expect(list.items.value).toHaveLength(30)
    expect(list.total.value).toBe(30)
  })

  it('reset supersedes an in-flight fetch and empties the list', async () => {
    let release: (page: Page<Row>) => void = () => {}
    const list = createPagedList<Row>({
      page: 25,
      key: (r) => r.id,
      fetchPage: () =>
        new Promise<Page<Row>>((resolve) => {
          release = resolve
        }),
    })

    const pending = list.refresh()
    list.reset()
    release({ items: rows(0, 25), total: 99 })
    await pending

    expect(list.items.value).toEqual([])
    expect(list.total.value).toBe(0)
    expect(list.loading.value).toBe(false)
  })

  it('reports failures through onError and keeps the loaded rows', async () => {
    let fail = false
    const onError = vi.fn()
    const list = createPagedList<Row>({
      page: 25,
      key: (r) => r.id,
      onError,
      fetchPage: async (limit, offset) => {
        if (fail) throw new Error('boom')
        return { items: rows(offset, limit), total: 100 }
      },
    })
    await list.refresh()
    fail = true

    await list.loadMore()

    expect(list.items.value).toHaveLength(25)
    expect(list.error.value).toBeTruthy()
    expect(onError).toHaveBeenCalledOnce()
    expect(list.loading.value).toBe(false)
  })
})
