<script setup lang="ts">
/**
 * Search and jump. Composes capability that already existed rather than adding
 * any: routes, entity search, and the brief archive.
 *
 * Entity search hits `/graph/search` first because Neo4j returns `degree`, and
 * the ranking signal is most of the feature -- "army" matches a dozen entities
 * and "9 links" identifies the one that matters. It falls back to `/search`,
 * which is SQLite, alias-aware, still works when Neo4j is down, and covers
 * provisional entities the graph has never projected.
 *
 * While this is open the view bindings are suspended, or typing "graph" would
 * fire whatever `g` happens to be bound to on the page behind it.
 */
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute, useRouter, type RouteLocationRaw } from 'vue-router'

import { apiGet } from '@/api/client'
import type { BriefSearchResults } from '@/types/brief'
import { useBriefsStore } from '@/stores/briefs'
import { useConfigDocsStore } from '@/stores/configDocs'
import { useSessionStore } from '@/stores/session'
import { useStaleGuard } from '@/composables/useStaleGuard'
import { toRef, useOverlay } from '@/composables/useOverlay'
import { toEntityRoute } from '@/router/entityRoute'
import type { GraphSearchHit } from '@/types/graph'
import SectionHead from '@/components/ui/SectionHead.vue'
import type { GraphSearchResponse } from '@/types/graph'

interface Row {
  key: string
  section: string
  label: string
  hint?: string
  /** A location, not a path string. The static rows below are paths and stay
   *  paths; a name's row comes from `toEntityRoute`, which returns a named
   *  location so nothing here assembles the URL by hand. `router.push` takes
   *  either. */
  to: RouteLocationRaw
  /** Admin-only. Mirrors `meta.operator` on the route; see `visibleRoutes`. */
  operator?: boolean
}

const ROUTES: Row[] = [
  { key: 'r:/', section: 'go', label: 'Brief', hint: 'latest', to: '/' },
  { key: 'r:/briefs', section: 'go', label: 'Archive', hint: 'previous briefs', to: '/briefs' },
  { key: 'r:/graph', section: 'go', label: 'Graph', to: '/graph' },
  { key: 'r:/lookup', section: 'go', label: 'Lookup', hint: 'every acronym', to: '/lookup' },
  { key: 'r:/learn', section: 'go', label: 'Learn', hint: 'study the domain', to: '/learn' },
  { key: 'r:/admin', section: 'go', label: 'Admin', hint: 'users, data, spend', to: '/admin', operator: true },
  { key: 'r:/admin/people', section: 'go', label: 'People', hint: 'users and access', to: '/admin/people', operator: true },
  { key: 'r:/admin/data', section: 'go', label: 'Data', hint: 'counts, and one delete', to: '/admin/data', operator: true },
  { key: 'r:/admin/model', section: 'go', label: 'Model', hint: 'which service thinks, and its sign-in', to: '/admin/model', operator: true },
  { key: 'r:/admin/config', section: 'go', label: 'Configuration', hint: 'every document', to: '/admin/config', operator: true },
  { key: 'r:/admin/curation', section: 'go', label: 'Curation', hint: 'what Venona proposed', to: '/admin/curation', operator: true },
  { key: 'r:/admin/runs', section: 'go', label: 'Runs', hint: 'what it has done', to: '/admin/runs', operator: true },
  { key: 'r:/settings/account', section: 'go', label: 'Account', hint: 'sign out', to: '/settings/account' },
  { key: 'r:/settings/relevance', section: 'go', label: 'Relevance', hint: 'tune your reading', to: '/settings/relevance' },
  { key: 'r:/settings/agents', section: 'go', label: 'Agents', to: '/settings/agents' },

]

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'update:open', value: boolean): void }>()

const router = useRouter()
const route = useRoute()
const session = useSessionStore()
// Named around this file's own `briefs` ref, which holds search results.
const briefStore = useBriefsStore()
const panel = ref<HTMLElement | null>(null)
const input = ref<HTMLInputElement | null>(null)
const query = ref('')
const active = ref(0)
const entities = ref<Row[]>([])
const briefs = ref<Row[]>([])

function close() {
  emit('update:open', false)
}

/**
 * REGISTERED HERE, above the open-watcher below, and that order is load-bearing.
 *
 * `useFocusTrap` focuses the panel's first focusable after `nextTick`, and the
 * watcher below focuses the search input after `nextTick` too. Both fire; the
 * later-registered one wins, which is why the input ends up focused rather than
 * whatever `<button>` happens to be first in the DOM. Moving this call below
 * that watcher would silently move focus, and nothing type-checks it.
 */
useOverlay(toRef(props, 'open'), panel, close, { ownsKeyboard: true })

/**
 * Config documents, from the shared list store — the same rows the admin rail
 * renders, so an admin who has touched any admin page pays no fetch here, and
 * a boot on the brief pays none either until ⌘K first opens.
 *
 * Loaded on FIRST OPEN rather than at setup or per keystroke: the store's
 * `loaded` guard makes a warm open free, and a cold open's fetch lands while
 * the query is still empty — before `watch(rows)`'s highlight reset could
 * move the selection under anyone's arrow keys.
 */
const configStore = useConfigDocsStore()
const configRows = computed<Row[]>(() =>
  configStore.items.map((d) => ({
    key: `c:${d.key}`,
    // Rendered VERBATIM as the group heading above these rows, so it has to
    // name where they go. The documents live under `/admin/config`.
    section: 'configuration',
    label: d.title,
    // The group's TITLE. `group` is a key on the wire now, so the raw value
    // would put `names`, `sent` and `running` under these rows -- this repo's
    // words, on the surface a reader uses to find a document by what it does.
    hint: configStore.groupTitle(d.group).toLowerCase(),
    to: `/admin/config/${d.key}`,
  })),
)

// `/config` is ADMIN, including the reads: `profile/relevance_profile.md` is the
// operator's own thesis and `GET /api/config/{key}` returns it whole. Asking as a
// reader is a 403 -- harmless here because the `.catch` already tolerates it, but
// it would fire the global forbidden toast on ⌘K, about a list the reader
// cannot use anyway.
watch(
  () => props.open,
  (isOpen) => {
    if (isOpen && session.isKnownAdmin()) void configStore.load().catch(() => {})
  },
)

//: Offered only where there are subscriptions. NOT a `ROUTES` entry, because
//: that is a module constant and would offer it unconditionally -- on an
//: instance that takes no money the page has nothing to say and nothing to link
//: to. `SettingsRail` gates the identical link the same way, and two surfaces
//: disagreeing about whether a destination exists is how a reader lands on a
//: dead page.
const BILLING_ROUTE: Row = {
  key: 'r:/settings/billing',
  section: 'go',
  label: 'Subscription',
  to: '/settings/billing',
}

/** Admin destinations, removed for a reader. The router guard refuses the
 *  navigation anyway; this stops ⌘K offering it. */
const visibleRoutes = computed(() => {
  const rows = ROUTES.filter((r) => !r.operator || session.isAdmin())
  return session.info?.billing?.enforced ? [...rows, BILLING_ROUTE] : rows
})

const routeRows = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return visibleRoutes.value
  return visibleRoutes.value.filter((r) => r.label.toLowerCase().includes(q))
})

/**
 * Search-only, EXCEPT on the configuration surface itself.
 *
 * `ROUTES` renders unfiltered when the query is empty, and the results list is
 * capped at 46vh. Twenty config documents added to the idle list would push
 * entities and briefs below the fold on every open -- so everywhere else the
 * one Configuration row stands for them and the documents appear once you type.
 *
 * On `/admin/config` they ARE the list. This is how you move from one document
 * to the next without going back to the index, and a palette that answered an
 * empty query with nothing would make ⌘K useless for the one job it is holding
 * on this route.
 */
const onConfigSurface = computed(() => route.path.startsWith('/admin/config'))

const configMatches = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return onConfigSurface.value ? configRows.value : []
  return configRows.value.filter((r) => r.label.toLowerCase().includes(q) || (r.hint ?? '').includes(q))
})

/**
 * ON THE CONFIGURATION SURFACE THE DOCUMENTS COME FIRST.
 *
 * The results list is capped at 46vh and the cursor parks on row 0, so behind
 * the fourteen unfiltered `ROUTES` rows the documents are off-screen and Enter
 * navigates to the Brief -- off the surface entirely. Leaving them last would
 * mean this branch still required typing, which is the behaviour it exists to
 * replace.
 */
const rows = computed(() =>
  onConfigSurface.value && !query.value.trim()
    ? [...configMatches.value, ...routeRows.value, ...entities.value, ...briefs.value]
    : [...routeRows.value, ...configMatches.value, ...entities.value, ...briefs.value],
)

/** Out-of-order responses are the classic typeahead bug: a slow request for
 *  "ar" landing after a fast one for "army" replaces good results with stale
 *  ones. `useStaleGuard` discards anything that is not the newest -- this was
 *  the copy its docstring names. */
const guard = useStaleGuard()

async function search(q: string) {
  const isCurrent = guard.claim()
  if (q.trim().length < 2) {
    entities.value = []
    briefs.value = []
    return
  }

  // Started before the entity chain is awaited, not after it. The two searches
  // have nothing to say to each other, and in series a keystroke cost the entity
  // round trip PLUS the brief one before either section appeared. Each still
  // lands on its own, so names keep arriving first when they are faster.
  const briefsReady = apiGet<BriefSearchResults>(
    `/briefs/search?q=${encodeURIComponent(q.trim())}&limit=5`,
  ).catch(() => null)

  let hits: GraphSearchHit[] = []
  try {
    const data = await apiGet<GraphSearchResponse>(`/graph/search?q=${encodeURIComponent(q)}&limit=6`)
    hits = data.entities ?? []
  } catch {
    // Neo4j down, or the entity is provisional and was never projected.
    try {
      const data = await apiGet<GraphSearchResponse>(`/search?q=${encodeURIComponent(q)}&limit=6`)
      hits = data.entities ?? []
    } catch {
      hits = []
    }
  }
  if (!isCurrent()) return
  entities.value = hits.map((hit) => ({
    key: `e:${hit.id}`,
    section: 'names',
    label: hit.name,
    hint: [hit.type, hit.degree != null ? `${hit.degree} links` : null].filter(Boolean).join(' · '),
    // The name's own page. The palette is a jump list, and what somebody
    // typing a name wants is that name -- the graph is a place they go next,
    // from a control on the page, rather than the thing a search lands on.
    // Through the route module, never hand-built.
    to: toEntityRoute(hit.id),
  }))

  // The server, not the briefs store: the archive index is a paged window,
  // so matching against it would silently forget every brief past the page
  // the reader happens to have scrolled — the palette's recall shrinking with
  // someone else's scroll position. `/briefs/search` reads the whole corpus,
  // bodies and headlines included.
  const data = await briefsReady
  if (!isCurrent()) return
  if (data === null) {
    briefs.value = []
  } else {
    briefs.value = (data.items ?? []).map(({ brief: b }) => ({
      key: `b:${b.id}`,
      section: 'briefs',
      label: b.title,
      hint: b.date,
      // The newest brief's home is `/`. See `briefs.pathFor`.
      to: briefStore.pathFor(b.id),
    }))
  }
}

/** Both searches now leave on the same keystroke, so an undebounced box would
 *  put one `/briefs/search` -- a LIKE scan over every body -- on the wire per
 *  character. 200ms is the pause the glossary box already settled on. */
const SEARCH_DEBOUNCE_MS = 200
let searchTimer: ReturnType<typeof setTimeout> | undefined
watch(query, (q) => {
  clearTimeout(searchTimer)
  // Clearing is not a search: below the minimum length the rows have to empty
  // now, or a cleared box keeps showing the last query's hits for a beat.
  if (q.trim().length < 2) {
    void search(q)
    return
  }
  searchTimer = setTimeout(() => void search(q), SEARCH_DEBOUNCE_MS)
})
watch(rows, () => (active.value = 0))

// The focus trap and the dismissable stack are `useOverlay`'s; this is only the
// part that is specific to a palette -- clear the query so it never opens
// showing the last search, and put the caret in the box.
watch(
  () => props.open,
  async (isOpen) => {
    if (!isOpen) return
    query.value = ''
    entities.value = []
    briefs.value = []
    active.value = 0
    await nextTick()
    input.value?.focus()
  },
)

function move(delta: number) {
  if (!rows.value.length) return
  active.value = (active.value + delta + rows.value.length) % rows.value.length
}

function choose(row?: Row) {
  const target = row ?? rows.value[active.value]
  if (!target) return
  close()
  void router.push(target.to)
}

/** Handled here rather than through the registry, because the registry is
 *  suspended while this is open -- deliberately, see the block comment. */
function onKeydown(event: KeyboardEvent) {
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    move(1)
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    move(-1)
  } else if (event.key === 'Enter') {
    event.preventDefault()
    choose()
  }
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="scrim fixed inset-0 z-dialog flex items-start justify-center bg-scrim pt-[12vh] px-5 pb-5"
      @click.self="close"
    >
      <div
        ref="panel"
        class="palette w-overlay-md border border-border-default rounded-lg bg-surface-overlay overflow-hidden"
        role="dialog"
        aria-modal="true"
        aria-label="Search and jump"
      >
        <input
          ref="input"
          v-model="query"
          class="input w-full px-4 py-3 bg-transparent border-0 border-b border-b-border-subtle text-text-primary text-md focus:outline-none"
          type="text"
          placeholder="Jump to a page, a name, or a brief…"
          autocomplete="off"
          spellcheck="false"
          role="combobox"
          aria-expanded="true"
          aria-controls="palette-results"
          @keydown="onKeydown"
        />

        <ul id="palette-results" class="results max-h-[46vh] overflow-y-auto list-none py-1" role="listbox">
          <template v-for="(row, index) in rows" :key="row.key">
            <li
              v-if="index === 0 || rows[index - 1].section !== row.section"
              class="section px-4 pt-2 pb-1"
              role="presentation"
            >
              <SectionHead :label="row.section" />
            </li>
            <li
              class="row flex items-baseline gap-3 px-4 py-1 cursor-pointer"
              :class="index === active ? 'on bg-surface-inset text-text-primary' : 'text-text-secondary'"
              role="option"
              :aria-selected="index === active"
              @mouseenter="active = index"
              @click="choose(row)"
            >
              <span class="label grow min-w-0 truncate">{{ row.label }}</span>
              <span v-if="row.hint" class="hint flex-none text-xs text-text-tertiary">{{ row.hint }}</span>
            </li>
          </template>
          <li v-if="!rows.length" class="empty px-4 py-3 text-text-tertiary text-xs">no matches</li>
        </ul>

        <!-- PINNED, not a row in the list above.
             It was the last item in `rows`, which put the escape hatch below six
             entities and five briefs inside a `max-h-[46vh]` scroller -- so the
             one affordance that exists for "none of these are it" was off screen
             at exactly the moment it was wanted, and you had to scroll past the
             results you had already rejected to reach it.
             Outside the `<ul>`, so it never scrolls and never steals the arrow
             keys or Enter from the results. -->
        <button
          v-if="query.trim().length >= 2"
          type="button"
          class="all flex w-full items-baseline gap-3 border-t border-t-border-subtle bg-transparent px-4 py-2 text-left font-ui text-xs text-text-tertiary cursor-pointer hover:bg-surface-inset hover:text-text-secondary"
          @click="
            choose({
              key: 's:all',
              section: 'search',
              label: '',
              to: `/search?q=${encodeURIComponent(query.trim())}`,
            })
          "
        >
          <span class="grow min-w-0 truncate">Search everything, including brief text</span>
          <span class="flex-none text-text-quiet">{{ rows.length }} here</span>
        </button>
      </div>
    </div>
  </Teleport>
</template>
