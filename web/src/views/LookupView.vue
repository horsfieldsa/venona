<script setup lang="ts">
/**
 * The glossary as a SET.
 *
 * Every acronym Venona knows was already reachable — inside a hover card, in
 * prose, one term at a time, if you happened to be reading a brief that
 * mentioned it. The undefined ones were reachable that way too, which is to say
 * not at all: an acronym with no grounded expansion is exactly the one nobody
 * has read a sentence about. This is the list.
 *
 * WHY IT IS NOT `CurationView`. Curation is a QUEUE — a decision per row, and
 * the row leaves when you decide. This is a REFERENCE — every row stays, and
 * most visits end without changing anything. Same table, opposite verbs.
 *
 * The counts in the identity line are the ones the API actually serves:
 * `GET /api/glossary` returns `counts` per status. The design bundle also asks
 * for a total entity count, and there is no endpoint that cheaply produces one
 * (`/api/graph/overview` counts PROJECTED nodes and needs Neo4j up), so rather
 * than print a number that is wrong whenever the projection is stale, it is not
 * printed. Add `entities` here the day a count route exists.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute, useRouter } from 'vue-router'

import Button from '@/components/ui/Button.vue'
import Chip from '@/components/ui/Chip.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import FilterField from '@/components/ui/FilterField.vue'
import SegmentedControl from '@/components/ui/SegmentedControl.vue'
import SectionHead from '@/components/ui/SectionHead.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import EmptyCorpus from '@/components/shared/EmptyCorpus.vue'
import ViewShell from '@/components/ui/ViewShell.vue'
import { apiGet, errorText } from '@/api/client'
import { toEntityRoute } from '@/router/entityRoute'
import { useGlossaryStore } from '@/stores/glossary'
import { useCorpusEmpty } from '@/composables/useCorpusEmpty'
import { useInfiniteScroll } from '@/composables/useInfiniteScroll'
import type { GlossaryEntry as Entry, GlossarySurface as Surface } from '@/types/glossary'

const { filling } = useCorpusEmpty()

// The table is the glossary store's: paged, shared, revalidated by the
// `definitions` freshness stamp when a run grounds or rejects something. The
// FILTERS are the store's too — they shape the request now that the set is
// bigger than one page — so this view keeps only what is truly local to it:
// selection and the detail panel.
const glossary = useGlossaryStore()
const { items, counts, loading, error, canLoadMore } = storeToRefs(glossary)

const query = computed({
  get: () => glossary.query,
  set: (value: string) => glossary.setQuery(value),
})
const selected = ref<string | null>(null)
const detail = ref<Surface | null>(null)
const detailLoading = ref(false)
/** Why the panel is empty, when it is empty because the read failed.
 *  Without it a failed fetch renders as a term with nothing known about it,
 *  which is a real and different state — and the one this view exists to
 *  report honestly. */
const detailError = ref('')

/** `active` is the grounded state; `ambiguous` has an expansion too but has not
 *  been ruled on. Both have something to read, which is what "grounded" means
 *  to a reader here — as opposed to `undefined`, which is the gap. The mapping
 *  to `status=active,ambiguous` lives in the store's fetcher. */
const groundedCount = computed(() => (counts.value.active ?? 0) + (counts.value.ambiguous ?? 0))
const undefinedCount = computed(() => counts.value.undefined ?? 0)

/**
 * From `counts`, which is the whole table, NOT from `items.length`, which is one
 * page of at most 500.
 *
 * This said `items.length` first and rendered "500 acronyms · 740 undefined" —
 * a total smaller than one of its own parts, on a screen whose entire job is to
 * be the authoritative list. `counts` is computed server-side over every row
 * precisely so a capped page cannot misreport the set.
 */
const totalCount = computed(() => Object.values(counts.value).reduce((sum, n) => sum + n, 0))

/** Nothing here AND nothing the reader hid with a filter -- so the shared
 *  first-run screen is the honest answer rather than "nothing matches". */
const showEmptyCorpus = computed(
  () =>
    filling.value && !items.value.length && !query.value.trim() && glossary.filter === 'all',
)

/* The undefined count leads, because it is the question this page answers:
 * how much of the glossary Venona cannot source. Stated as a proportion, it
 * also fits the fixed identity track, where the longer phrasing truncated on
 * the very number worth reading. */
const identity = computed(
  () => `lookup · ${undefinedCount.value} undefined of ${totalCount.value}`,
)

/** The state column, as words rather than a status enum. "3 sources" is what a
 *  reader wants to know; "active" is what the database calls it. */
function stateLabel(entry: Entry): string {
  if (entry.status === 'undefined') return 'undefined'
  if (entry.status === 'rejected') return 'rejected'
  const n = entry.corroboration_count
  return n === 1 ? '1 source' : `${n} sources`
}

function stateClass(entry: Entry): string {
  if (entry.status === 'undefined' || entry.status === 'rejected') return 'text-text-tertiary'
  return entry.corroboration_count >= 3 ? 'text-signal-ok' : 'text-signal-high'
}

async function select(entry: Entry) {
  selected.value = entry.surface
  detailError.value = ''
  detailLoading.value = true
  try {
    // The SURFACE, not the row: an ambiguous acronym has several readings and
    // the panel has to show all of them, including the rejected ones — a
    // rejection with a reason is the most useful thing on this screen.
    detail.value = await apiGet<Surface>(`/glossary/${encodeURIComponent(entry.surface)}`)
  } catch (err) {
    detail.value = null
    detailError.value = errorText(err, 'could not open that term')
  } finally {
    detailLoading.value = false
  }
}

const live = computed(() => detail.value?.entries?.filter((e) => e.status !== 'rejected') ?? [])
const rejected = computed(
  () => detail.value?.entries?.filter((e) => e.status === 'rejected') ?? [],
)
const isUndefined = computed(
  () => live.value.length > 0 && live.value.every((e) => e.status === 'undefined'),
)

onMounted(() => void glossary.load())
// Changing a filter can hide the selected row; the panel then describes
// something off screen. Keep it, but stop pretending it is selected in a list
// it is not in. A freshness revalidate re-reads the whole loaded window, so a
// selected row survives it unless the run genuinely removed the row.
watch(items, (rows) => {
  if (selected.value && !rows.some((r) => r.surface === selected.value)) {
    selected.value = null
    detailError.value = ''
  }
})

/** Scrolling near the list's end pulls the next page. The observer root is the
 *  list's own pane — this view is `scroll="none"`, so nothing above it
 *  scrolls — and the Load more button stays as the keyboard path. */
const listPane = ref<HTMLElement | null>(null)
const moreSentinel = ref<HTMLElement | null>(null)
useInfiniteScroll(listPane, moreSentinel, () => void glossary.loadMore(), {
  disabled: () => glossary.loading || !glossary.canLoadMore,
})

/**
 * Which acronyms are listed, as one of three rather than three chips.
 *
 * The chips said nothing about belonging to each other: `all`, `sourced` and
 * `undefined` are alternatives, exactly one of which is true, which is what a
 * segmented control's single frame means and three separate boxes do not.
 *
 * The counts ride the labels because they are whole-table figures, not the
 * length of the loaded page -- `GET /glossary` returns `counts` for that
 * reason, and the number is most of the answer here: how much of the glossary
 * Venona cannot source is the question this page exists to ask.
 */
const statuses = computed(() => [
  { value: 'all' as const, label: `all ${totalCount.value}` },
  { value: 'grounded' as const, label: `sourced ${groundedCount.value}` },
  { value: 'undefined' as const, label: `undefined ${undefinedCount.value}` },
])

/**
 * `?status=` makes a filtered glossary a link, the way `/learn?section=` does.
 *
 * `replace`, not `push`: switching between three views of one list is one act
 * of looking, and a history entry per tab makes Back useless.
 */
const route = useRoute()
const router = useRouter()

const status = computed({
  get: () => glossary.filter,
  set: (next) => {
    glossary.setFilter(next)
    void router.replace({ query: next === 'all' ? {} : { status: next } })
  },
})

watch(
  () => route.query.status,
  (value) => {
    const next = statuses.value.some((s) => s.value === value) ? String(value) : 'all'
    if (next !== glossary.filter) glossary.setFilter(next as typeof glossary.filter)
  },
  { immediate: true },
)
</script>

<template>
  <!-- The first-run screen is a PAGE, not a canvas: `width`/`scroll` switch
       with it so it inherits the same `--edge` and `--space-5` every other
       route uses. Positioning it with a padding class instead is what left
       the same component at four different offsets -- 166px on the Brief,
       140px on the Archive, 26px here. -->
  <ViewShell
    :width="showEmptyCorpus ? 'wide' : 'full'"
    :scroll="showEmptyCorpus ? 'page' : 'none'"
    :loading="loading && !items.length"
    :error="error"
    :identity="identity"
  >
    <!-- Hidden on an empty corpus, like every other route's chrome: three tabs
         reading `all 0 / sourced 0 / undefined 0` describe nothing. -->
    <template v-if="!showEmptyCorpus" #controls>
      <SegmentedControl
        v-model="status"
        scrollable
        ariaLabel="which acronyms"
        :options="statuses"
      />
    </template>

    <template v-if="!showEmptyCorpus" #search>
      <FilterField
        v-model="query"
        hotkey
        collapsible
        hotkey-group="lookup"
        label="Filter acronyms by term or expansion"
        placeholder="term or expansion…"
      />
    </template>

    <EmptyCorpus v-if="showEmptyCorpus" />

    <!-- max-lg:overflow-y-auto: under `scroll="none"` the one-column stack has
         no page scroller, so the detail pane sits below the list at a y the
         viewport never reaches — a term you select and then cannot read. The
         grid becomes the scroller wherever the panes stack; at lg+ the two
         panes each own their scroll and a third scroller would nest bars.
         The breakpoint is the one the columns change at, not ConfigView's. -->
    <div v-else class="lookup grid h-full min-h-0 grid-cols-1 max-lg:overflow-y-auto lg:grid-cols-[minmax(0,1fr)_var(--rail-width-wide)]">
      <!-- The set -->
      <section ref="listPane" class="min-h-0 overflow-y-auto p-5">
        <EmptyState v-if="!items.length" title="Nothing matches that filter.">
          Every acronym Venona has seen is here, sourced or not. Clear the filter to see the set.
        </EmptyState>

        <table v-else class="w-full border-collapse">
          <thead>
            <tr class="border-b border-border-subtle text-left">
              <th class="w-(--field-md) pb-2 font-ui text-2xs font-normal uppercase tracking-label text-text-tertiary">
                term
              </th>
              <th class="pb-2 font-ui text-2xs font-normal uppercase tracking-label text-text-tertiary">
                expansion
              </th>
              <th class="w-(--field-sm) pb-2 font-ui text-2xs font-normal uppercase tracking-label text-text-tertiary">
                state
              </th>
            </tr>
          </thead>
          <tbody>
            <!-- Selecting a term is the primary action of this view, so the row
                 is a control: a tab stop, a role, and both keys. Spelled out
                 inline here and again in `Table.vue`, which carries the same
                 affordance for the views built on it. This table is hand-rolled
                 (three columns, one width token); migrating it onto `Table` is
                 the real fix and is recorded as a follow-up. -->
            <tr
              v-for="entry in items"
              :key="entry.id"
              tabindex="0"
              role="button"
              :aria-pressed="selected === entry.surface"
              class="cursor-pointer border-b border-border-subtle hover:bg-surface-inset"
              :class="selected === entry.surface ? 'bg-surface-selected' : ''"
              @click="select(entry)"
              @keydown.enter.prevent="select(entry)"
              @keydown.space.prevent="select(entry)"
            >
              <td class="py-2 pr-2 font-mono text-sm text-text-primary align-top">
                {{ entry.surface }}
              </td>
              <td class="py-2 pr-2 font-prose text-sm align-top"
                  :class="entry.expansion ? 'text-text-body' : 'text-text-tertiary'">
                {{ entry.expansion ?? 'no sourced expansion' }}
              </td>
              <td class="py-2 font-ui text-2xs uppercase tracking-label align-top"
                  :class="stateClass(entry)">
                {{ stateLabel(entry) }}
              </td>
            </tr>
          </tbody>
        </table>

        <div
          v-if="canLoadMore"
          ref="moreSentinel"
          class="mt-4 flex items-center justify-center"
        >
          <Button variant="text" icon="down" :disabled="loading" @click="glossary.loadMore()">
            Load more
          </Button>
        </div>
      </section>

      <!-- The one term -->
      <aside
        class="min-h-0 overflow-y-auto border-t lg:border-t-0 lg:border-l border-border-subtle bg-surface-inset p-5"
      >
        <EmptyState v-if="!selected" title="Pick a term.">
          The panel shows every reading of a term, including the ones Venona rejected and why.
        </EmptyState>

        <template v-else>
          <div class="flex items-center gap-2">
            <h2 class="font-mono text-md text-text-primary">{{ selected }}</h2>
            <Chip v-if="isUndefined" tone="neutral">undefined</Chip>
            <Chip v-else-if="detail?.ambiguous" tone="high">ambiguous</Chip>
          </div>

          <p v-if="detailLoading" class="mt-3 font-ui text-sm text-text-tertiary">loading…</p>

          <StatusNote v-else-if="detailError" kind="error" class="mt-3">
            {{ detailError }}
          </StatusNote>

          <template v-else>
            <p v-if="isUndefined" class="mt-3 font-prose text-base leading-normal text-text-secondary">
              No article Venona has read expands this acronym, and none of its approved sources
              define it, so Venona records the gap rather than guessing.
            </p>

            <!-- Every live reading, with its quote. The quote is the whole
                 claim: invariant 3 says a definition exists only if it is
                 traceable to a sentence in a document, so showing the expansion
                 without the sentence would be showing the half that is not
                 evidence. -->
            <div v-for="entry in live" :key="entry.id" class="mt-4">
              <p v-if="entry.expansion" class="font-prose text-base leading-normal text-text-body">
                {{ entry.expansion }}
              </p>
              <blockquote
                v-if="entry.grounding?.quote"
                class="mt-2 border-l-2 border-accent-deep pl-3 font-prose text-sm leading-normal text-text-tertiary"
              >
                {{ entry.grounding?.quote }}
              </blockquote>
              <p v-if="entry.grounding?.article_title" class="mt-1 font-ui text-2xs text-text-quiet">
                <a v-if="entry.grounding?.url" :href="entry.grounding?.url" target="_blank" rel="noopener">
                  {{ entry.grounding?.article_title }}
                </a>
                <span v-else>{{ entry.grounding?.article_title }}</span>
                <span v-if="entry.grounding?.publisher"> · {{ entry.grounding?.publisher }}</span>
                <span v-if="entry.sighting_count"> · {{ entry.sighting_count }} mentions</span>
              </p>
              <!-- Who ruled on this reading, when somebody did. `decided_at`
                   has always said a PERSON settled it; `decided_by` says which,
                   and it is the only place in the app that answers "who decided
                   this meant that" for an acronym. Absent means the pipeline
                   grounded it or the CLI settled it, which is not the same as
                   unknown and so renders as nothing rather than as a name. -->
              <p v-if="entry.decided_by_email" class="mt-1 font-ui text-2xs text-text-quiet">
                decided by {{ entry.decided_by_email }}
              </p>
              <!-- The term's own page: every article it appeared in, rather
                   than the one sentence that defines it. This panel is a
                   reading; the page is the record. -->
              <RouterLink :to="toEntityRoute(entry.id)" class="mt-2 inline-block font-ui text-2xs">
                Everything Venona has on it
              </RouterLink>
            </div>

            <!-- The rejections, and this is the part that cannot be got
                 anywhere else in the app. A candidate that was found and thrown
                 out is the evidence that the gap is deliberate rather than an
                 omission. -->
            <template v-if="rejected.length">
              <SectionHead as="h3" label="rejected" rule class="mt-5" />
              <StatusNote v-for="entry in rejected" :key="entry.id" kind="error" class="mt-2">
                “{{ entry.expansion ?? entry.surface }}”
                <template #hint>
                  {{ entry.grounding?.publisher
                    ? `sourced to ${entry.grounding?.publisher}, which is not an approved source`
                    : 'no approved source supports this reading' }}
                </template>
              </StatusNote>
            </template>
          </template>
        </template>
      </aside>
    </div>
  </ViewShell>
</template>
