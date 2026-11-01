import { computed, ref, type ComputedRef, type Ref } from 'vue'

import { errorText } from '@/api/client'
import { useStaleGuard } from '@/composables/useStaleGuard'

/**
 * One page of a list endpoint. Every paginated route returns an envelope with
 * `items` and a whole-set `total` (docs/EXTENDING.md); the fetcher hands those
 * two through and keeps any surface extras (`hidden`, `counts`) for itself.
 */
export interface Page<T> {
  items: T[]
  total: number
}

export interface PagedListOptions<T> {
  /** Rows per request. Also the chunk size `revalidate()` refetches in. */
  page: number
  /** Stable identity for de-duplication across pages. */
  key: (item: T) => string
  /** Fetch one window. Throws on failure; the list catches and reports. */
  fetchPage: (limit: number, offset: number) => Promise<Page<T>>
  /** Route errors somewhere else (a store's shared `error`). Without it the
   *  list keeps them on its own `error` ref. */
  onError?: (message: string) => void
}

export interface PagedList<T> {
  items: Ref<T[]>
  total: Ref<number>
  loading: Ref<boolean>
  error: Ref<string | null>
  canLoadMore: ComputedRef<boolean>
  refresh: () => Promise<void>
  loadMore: () => Promise<void>
  revalidate: () => Promise<void>
  reset: () => void
}

/**
 * The paged-list state machine every large list shares: `offset = items.length`,
 * append, never replace — generalised from `stores/runs.ts`, the surface that
 * paged first.
 *
 * Three rules earn their keep here rather than in each store:
 *
 * - `loadMore` no-ops while a fetch is in flight. It has three triggers per
 *   surface (sentinel, button, keybinding/watcher) and two calls at the same
 *   offset coalesce in `apiGet` — so both would append the same page.
 * - Appends de-duplicate by `key`. Offset paging over a moving set (a run
 *   inserts a row, a moot conflict drops out) overlaps rather than gaps at the
 *   window edge; absorbing the overlap here is what makes that safe.
 * - A page that adds nothing new marks the list exhausted. `total` can
 *   overcount what the server will actually return (conflicts drop moot rows
 *   after the window), and `length < total` alone would then refetch forever
 *   at the bottom of the scroll.
 */
export function createPagedList<T>(options: PagedListOptions<T>): PagedList<T> {
  const { page, key, fetchPage } = options

  const items = ref<T[]>([]) as Ref<T[]>
  const total = ref(0)
  const loading = ref(false)
  const error = ref<string | null>(null)
  /** True once a page came back adding nothing — the honest end of the list
   *  even when `total` says otherwise. Cleared by any full re-read. */
  const exhausted = ref(false)

  const guard = useStaleGuard()

  const canLoadMore = computed(
    () => !exhausted.value && items.value.length < total.value,
  )

  function fail(err: unknown) {
    const message = errorText(err, 'something went wrong')
    error.value = message
    options.onError?.(message)
  }

  function dedupe(rows: T[]): T[] {
    const seen = new Set<string>()
    const out: T[] = []
    for (const row of rows) {
      const k = key(row)
      if (seen.has(k)) continue
      seen.add(k)
      out.push(row)
    }
    return out
  }

  /** Page 0, replacing everything held. The load path for mount and for any
   *  filter change (callers `reset()` first so stale rows never flash). */
  async function refresh() {
    const fresh = guard.claim()
    loading.value = true
    error.value = null
    try {
      const data = await fetchPage(page, 0)
      if (!fresh()) return
      items.value = dedupe(data.items)
      total.value = data.total
      exhausted.value = false
    } catch (err) {
      if (fresh()) fail(err)
    } finally {
      if (fresh()) loading.value = false
    }
  }

  async function loadMore() {
    if (loading.value || !canLoadMore.value) return
    const fresh = guard.claim()
    loading.value = true
    error.value = null
    try {
      const data = await fetchPage(page, items.value.length)
      if (!fresh()) return
      const before = items.value.length
      items.value = dedupe([...items.value, ...data.items])
      total.value = data.total
      if (items.value.length === before) exhausted.value = true
    } catch (err) {
      if (fresh()) fail(err)
    } finally {
      if (fresh()) loading.value = false
    }
  }

  /**
   * Re-read the window already on screen, in page-sized chunks, and swap it in
   * whole. This is the freshness path: page-0 `refresh()` would truncate an
   * accumulated buffer — closing a detail panel whose row lives past page one,
   * clamping a queue cursor — and one big request would trip the server's
   * per-page `le=` cap. Chunks respect the cap; the swap keeps scroll depth.
   */
  async function revalidate() {
    const target = Math.max(items.value.length, page)
    const fresh = guard.claim()
    loading.value = true
    error.value = null
    try {
      const rows: T[] = []
      let seenTotal = 0
      for (;;) {
        const data = await fetchPage(page, rows.length)
        if (!fresh()) return
        rows.push(...data.items)
        seenTotal = data.total
        if (data.items.length < page || rows.length >= target) break
      }
      items.value = dedupe(rows)
      total.value = seenTotal
      exhausted.value = false
    } catch (err) {
      if (fresh()) fail(err)
    } finally {
      if (fresh()) loading.value = false
    }
  }

  /** Empty the list and supersede anything in flight. Callers follow with
   *  `refresh()` after moving their filters. */
  function reset() {
    guard.claim()
    items.value = []
    total.value = 0
    exhausted.value = false
    error.value = null
    loading.value = false
  }

  return { items, total, loading, error, canLoadMore, refresh, loadMore, revalidate, reset }
}
