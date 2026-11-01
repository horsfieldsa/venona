import { defineStore } from 'pinia'
import { ref } from 'vue'

import { apiGet } from '@/api/client'
import { onFreshness } from '@/composables/useFreshness'
import { createPagedList } from '@/lib/paging'
import type { GlossaryEntry, GlossaryList } from '@/types/glossary'

const PAGE = 200
const QUERY_DEBOUNCE_MS = 200

/**
 * The glossary, paged and shared.
 *
 * The filters live HERE, not in the view, because they shape the request now:
 * the corpus outgrew `GET /glossary`'s per-page cap, so a filter applied
 * client-side would only ever search the rows that happened to be loaded — a
 * missing acronym would read as never seen when it was merely on page two.
 * The chip maps to `status=` (`grounded` is two statuses at once) and the text
 * box to `q=`, debounced so a typist costs one request per pause, not one per
 * keystroke.
 *
 * `counts` rides every page response and is whole-table on the server, so the
 * chip totals stay true from inside any filter.
 */
export const useGlossaryStore = defineStore('glossary', () => {
  const counts = ref<Record<string, number>>({})
  const loaded = ref(false)
  const query = ref('')
  const filter = ref<'all' | 'grounded' | 'undefined'>('all')

  const list = createPagedList<GlossaryEntry>({
    page: PAGE,
    key: (entry) => entry.id,
    fetchPage: async (limit, offset) => {
      const parts = [`limit=${limit}`, `offset=${offset}`]
      if (filter.value === 'grounded') parts.push('status=active,ambiguous')
      if (filter.value === 'undefined') parts.push('status=undefined')
      const needle = query.value.trim()
      if (needle) parts.push(`q=${encodeURIComponent(needle)}`)
      const data = await apiGet<GlossaryList>(`/glossary?${parts.join('&')}`)
      // `items` and `counts` are optional on the model, so these coalesce
      // rather than assert. An empty glossary is a real state — a corpus
      // ingested but never acronym-run — not an error.
      counts.value = data.counts
      return { items: data.items, total: data.total }
    },
  })

  function load(force = false): Promise<void> {
    if (loaded.value && !force) return Promise.resolve()
    loaded.value = true
    return list.refresh()
  }

  /** Deliberately NOT `reset()` first: the rows already held stay on screen
   *  until the filtered page lands, so typing narrows a visible list rather
   *  than blinking it empty on every pause. The stale guard inside the list
   *  drops any out-of-order response. */
  let debounce: ReturnType<typeof setTimeout> | undefined
  function setQuery(next: string) {
    query.value = next
    clearTimeout(debounce)
    debounce = setTimeout(() => void list.refresh(), QUERY_DEBOUNCE_MS)
  }

  function setFilter(next: 'all' | 'grounded' | 'undefined') {
    if (filter.value === next) return
    filter.value = next
    void list.refresh()
  }

  // A run grounded or rejected something. Re-read the window on screen rather
  // than truncating to page one: the reader may have a row from deep in the
  // list open in the detail panel, and a truncation would close it under them.
  onFreshness('definitions', () => {
    if (loaded.value) void list.revalidate()
  })

  return {
    items: list.items,
    total: list.total,
    loading: list.loading,
    error: list.error,
    canLoadMore: list.canLoadMore,
    loadMore: list.loadMore,
    counts,
    loaded,
    query,
    filter,
    load,
    setQuery,
    setFilter,
  }
})
