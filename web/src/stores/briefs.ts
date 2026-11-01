import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { apiGet, errorText } from '@/api/client'
import { onFreshness } from '@/composables/useFreshness'
import { createPagedList } from '@/lib/paging'
import type { Brief, BriefList, BriefSearchResults, BriefSummary } from '@/types/brief'

const INDEX_PAGE = 50

export const useBriefsStore = defineStore('briefs', () => {
  const current = ref<Brief | null>(null)
  const loading = ref(false)
  /** The CURRENT BRIEF's failure, and only that. `BriefView` renders it (as
   *  its first-run/404/offline states). The index and the search fail into
   *  `indexError` instead: one shared slot let a `/briefs/latest` 404 --
   *  whose detail says to run `make seed` -- paint the ARCHIVE red, a view
   *  whose own data had loaded fine. */
  const error = ref<string | null>(null)
  /** The archive's failure: the index or a content search. `ArchiveView`
   *  renders this one. */
  const indexError = ref<string | null>(null)

  /** The archive, paged and shared with `ArchiveView`.
   *
   *  Not one brief per day: ids are unique per generation, so a day of tuning
   *  can add several. This holds only what the reader has scrolled to; the
   *  things that once leaned on it holding EVERYTHING now go elsewhere — the
   *  prev/next arrows read the brief's own `older_id`/`newer_id`, and the
   *  command palette asks `/briefs/search`. */
  const indexLoaded = ref(false)
  const indexList = createPagedList<BriefSummary>({
    page: INDEX_PAGE,
    key: (brief) => brief.id,
    onError: (message) => {
      indexError.value = message
    },
    fetchPage: async (limit, offset) => {
      const data = await apiGet<BriefList>(`/briefs?limit=${limit}&offset=${offset}`)
      // Cleared on SUCCESS. Without this a transient index failure left
      // `indexError` set forever, and `ArchiveView` binds it to `ViewShell
      // :error` -- so the archive showed an error state after it had already
      // loaded fine, with no way to clear it but a reload.
      indexError.value = null
      return { items: data.items, total: data.total }
    },
  })
  const index = indexList.items
  const indexTotal = indexList.total

  /**
   * Where the reader was, per brief.
   *
   * The round trip this exists for is: click a mention, look at the entity in
   * the graph, come back. Without it that is a total loss -- the scroll
   * container is the view's own element rather than `window`, so native
   * restoration cannot help even with `<KeepAlive>` and `scrollBehavior`, and
   * you land at the top of a freshly refetched nineteen-item document.
   */
  const scrollTops = ref<Record<string, number>>({})

  /** The item the reader last jumped away from, so the graph can offer a way
   *  back to the exact paragraph rather than to the top of the page. */
  const returnItemId = ref<string | null>(null)

  const latestId = computed(() => index.value[0]?.id ?? null)

  async function loadIndex(force = false) {
    if (indexLoaded.value && !force) return
    indexLoaded.value = true
    await indexList.refresh()
  }

  // ── search over brief content ──────────────────────────────────────

  const search = ref<BriefSearchResults | null>(null)
  const searching = ref(false)

  /** The query the newest REQUEST was for, which is not the same as the query
   *  the newest RESPONSE answered. A short query issued after a long one can
   *  come back first, so the response carries its own `query` and anything that
   *  does not match this is discarded rather than rendered. */
  let inFlight = ''

  async function runSearch(q: string, entity: string | null = null) {
    const needle = q.trim()
    inFlight = needle
    if (!needle && !entity) {
      search.value = null
      searching.value = false
      return
    }
    searching.value = true
    try {
      const params = new URLSearchParams({ q: needle, limit: '50' })
      if (entity) params.set('entity', entity)
      const data = await apiGet<BriefSearchResults>(`/briefs/search?${params}`)
      // Stale answer: the user has typed on since this went out.
      if (data.query !== inFlight) return
      search.value = data
      indexError.value = null
    } catch (err) {
      if (needle !== inFlight) return
      indexError.value = errorText(err, 'could not search the archive')
      search.value = null
    } finally {
      if (needle === inFlight) searching.value = false
    }
  }

  async function fetchInto(path: string) {
    loading.value = true
    error.value = null
    try {
      current.value = await apiGet<Brief>(path)
    } catch (err) {
      error.value = errorText(err, 'something went wrong')
      current.value = null
    } finally {
      loading.value = false
    }
  }

  /** No-op when the latest brief is already loaded. `BriefView` called this
   *  unconditionally on every mount, so returning from the graph refetched and
   *  re-rendered the whole document -- which is what threw the reading position
   *  away even before scroll restoration existed. */
  async function loadLatest(force = false) {
    if (!force && current.value && (latestId.value === null || current.value.id === latestId.value)) {
      return
    }
    await fetchInto('/briefs/latest')
  }

  async function load(id: string) {
    if (current.value?.id === id) return
    await fetchInto(`/briefs/${encodeURIComponent(id)}`)
  }

  /** Older is "previous"; the archive is ordered newest first.
   *
   *  From the brief's own payload, not from scanning the index: the index is a
   *  paged window now, and arrows that only worked for briefs inside it would
   *  go inert on any deep link past the first page. The server computes both
   *  ids against the whole table. */
  const neighbours = computed(() => ({
    older: current.value?.older_id ?? null,
    newer: current.value?.newer_id ?? null,
  }))

  /**
   * Where a brief lives. THE LATEST ONE'S HOME IS `/`, not `/briefs/<its id>`.
   *
   * The two render the same document and are not the same page: `/` is where
   * the reader's own brief is offered beside the shared one, and where the line
   * saying when the next one lands appears. Both are keyed on the route having
   * no id, because there is no personal version of an archived brief.
   *
   * So a reader who arrowed back through the archive and returned to the newest
   * brief arrived at a page that looked right and had quietly lost the "for
   * you" control, with no way back to it but the header. The arrows could not
   * return them: every step pushed `/briefs/<id>`, including the last one.
   *
   * Falls back to the id when the index has not loaded, which is no worse than
   * addressing it by id -- the same document either way.
   */
  function pathFor(id: string): string {
    return id === latestId.value ? '/' : `/briefs/${encodeURIComponent(id)}`
  }

  function rememberScroll(id: string, top: number) {
    scrollTops.value[id] = top
  }

  /**
   * A new brief exists — the nightly run being the whole point. Re-read the
   * index window the reader has scrolled to build (a page-0 refresh would
   * throw their depth away); the new brief arrives at the top of it.
   *
   * The INDEX only. The open brief is left alone: a brief's content is fixed
   * per generation (a rewrite lands as a NEW brief), so refetching `current`
   * here would re-render the document someone is mid-read for nothing.
   */
  onFreshness('briefs', () => {
    if (indexLoaded.value) void indexList.revalidate()
  })

  return {
    current, loading, error, indexError, index, indexTotal, latestId, neighbours, pathFor,
    scrollTops, returnItemId, search, searching,
    indexLoading: indexList.loading, canLoadMoreIndex: indexList.canLoadMore,
    loadIndex, loadMoreIndex: indexList.loadMore, loadLatest, load, rememberScroll, runSearch,
  }
})
