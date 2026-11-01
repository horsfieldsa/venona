<script setup lang="ts">
/**
 * Every brief, not just today's.
 *
 * `GET /briefs` has returned the full paginated archive since the beginning and
 * `stores/briefs.ts::load(id)` has existed unused for just as long -- so every
 * brief before the current one was retrievable and unreachable at the same
 * time.
 *
 * **The filter searches brief CONTENT.** `GET /briefs/search` reaches item
 * headlines and block text as well as titles and deks -- a client-side filter
 * over title and dek would not find a program name remembered from the body of
 * an item.
 *
 * Two things stay CLIENT-side and deliberately so: date and time. `2026-08-07`
 * and `22:10` are how a reader narrows an archive they are scrolling, they
 * match no words in any body, and sending them to a text search would return
 * every brief that happens to quote a date. So the filter runs both — the
 * server for content, the client for the two columns it owns — and the row set
 * is the union.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import Button from '@/components/ui/Button.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import FilterField from '@/components/ui/FilterField.vue'
import Table, { type Column } from '@/components/ui/Table.vue'
import EmptyCorpus from '@/components/shared/EmptyCorpus.vue'
import ViewShell from '@/components/ui/ViewShell.vue'
import { useCorpusEmpty } from '@/composables/useCorpusEmpty'
import { useInfiniteScroll } from '@/composables/useInfiniteScroll'
import { useBriefsStore } from '@/stores/briefs'
import { timeLabel } from '@/lib/dates'
import type { BriefSearchHit, BriefSummary } from '@/types/brief'

const briefs = useBriefsStore()
const router = useRouter()
const filter = ref('')

// Unforced: the store's `indexLoaded` guard makes a revisit free, and the
// `briefs` freshness stamp already reloads the index when a run lands a new
// one — forcing here refetched up to 500 summaries per visit to re-learn
// what the stamp guarantees the store already knows.
onMounted(() => void briefs.loadIndex())

/**
 * `archive · 96 briefs · 2026-05-02 → 2026-08-06`.
 *
 * The span, not just the count, because that is the question the identity line
 * is actually answering here: "how far back does this go" is what a reader wants
 * before they start scrolling, and a bare total does not say. Taken from the
 * loaded index, which is newest-first, so the range is last→first.
 */
/* The count, and not the date span with it. The identity sits in a fixed
 * track so the controls beside it cannot move, and two ISO dates do not fit:
 * they truncated to a half-written year, which reads as a bug rather than as
 * an abbreviation. The span is in the table's first and last rows anyway,
 * which is where someone reading it is already looking. */
const identity = computed(
  () => `archive · ${briefs.indexTotal} ${briefs.indexTotal === 1 ? 'brief' : 'briefs'}`,
)

/**
 * The brief currently open, marked in the list.
 *
 * `Table` has taken an `activeKey` since it shipped and rendered it on
 * `--surface-selected`; this view never passed one, so coming back to the
 * archive from a brief gave no clue which of eleven rows you had just been
 * reading. Same "this one" ground as a selected graph node and a selected
 * lookup row.
 */
const activeBriefId = computed(() => briefs.current?.id ?? null)

/** One shared answer to "is there anything yet" -- see `useCorpusEmpty`. */
const { filling } = useCorpusEmpty()
const isEmpty = computed(() => filling.value && !briefs.index.length && !filter.value.trim())

/** `2026-08-06T22:10:28.814Z` -> `22:10` in the reader's zone.
 *
 *  Bare, with no zone suffix: the column is 4.5rem and `tabular-nums`, and the
 *  zone is named once in its header instead. `date` beside it stays the UTC
 *  editorial day, which is what the ids, search and the CLI all key on -- the
 *  two columns answer different questions and are read as a pair, not as one
 *  timestamp. */
function hhmm(iso: string | null | undefined): string {
  return timeLabel(iso)
}

/**
 * Debounced, because this is a keystroke-driven request now rather than a local
 * `Array.filter`. 200ms is below the threshold where a pause reads as lag and
 * above the rate anyone types, so a word costs one request instead of six.
 *
 * The store discards a response whose `query` is not the newest one requested,
 * so a slow answer for `and` cannot overwrite a fast one for `anduril`.
 */
let debounce: ReturnType<typeof setTimeout> | undefined
watch(filter, (next) => {
  clearTimeout(debounce)
  debounce = setTimeout(() => void briefs.runSearch(next), 200)
})

/** What the server matched, keyed by brief id, so a row can show WHY it is
 *  here without a second lookup. */
const hitsById = computed(() => {
  const map = new Map<string, BriefSearchHit>()
  for (const hit of briefs.search?.items ?? []) map.set(hit.brief.id, hit)
  return map
})

/**
 * The join happens HERE, once, rather than five times in one template cell.
 *
 * Under a search, rows come FROM THE SEARCH RESULTS, not from filtering the
 * loaded index: `/briefs/search` covers the whole corpus while the index is a
 * paged window now, so intersecting them would silently drop any hit past the
 * window — a match on brief #80 rendering as "No brief matches that". Each hit
 * carries its own `BriefSummary`, which is exactly a row. The two columns the
 * server does not search — date and time — still come from the loaded index,
 * client-side: a date is how you narrow a list you are looking at, and sending
 * `2026-08` to a text search would match every body that quotes it.
 */
const rows = computed(() => {
  const q = filter.value.trim().toLowerCase()
  if (!q) return briefs.index.map((b) => ({ ...b, hit: null as BriefSearchHit | null }))

  const matched = hitsById.value
  const hits: (BriefSummary & { hit: BriefSearchHit | null })[] = [
    ...matched.values(),
  ].map((hit) => ({ ...hit.brief, hit }))
  const byColumn = briefs.index
    .filter(
      (b) => !matched.has(b.id) && (b.date.includes(q) || hhmm(b.generated_at).includes(q)),
    )
    .map((b) => ({ ...b, hit: null as BriefSearchHit | null }))
  // One list, back in archive order — the hits arrive ranked by match count,
  // which is a different order than the column matches around them.
  return [...hits, ...byColumn].sort((a, b) =>
    `${b.date} ${b.generated_at ?? ''}`.localeCompare(`${a.date} ${a.generated_at ?? ''}`),
  )
})

/**
 * A date filter can name briefs the paged index has not reached — matching is
 * client-side over the loaded window, so keep pulling pages while it matches
 * nothing and there is more to pull. The same idiom as RunsView's filtered
 * history; each append re-fires this via `rows.length`. Content matches need
 * no help (the server searched the whole corpus), so a pending search pauses
 * it rather than paging the index while the real answer is in flight.
 */
watch(
  () => [rows.value.length, filter.value, briefs.indexLoading, briefs.searching] as const,
  () => {
    if (!filter.value.trim() || rows.value.length || briefs.searching) return
    if (briefs.canLoadMoreIndex && !briefs.indexLoading) void briefs.loadMoreIndex()
  },
)

/** Scrolling near the end pulls the next page — through ViewShell's scroller
 *  (`scroll="page"`); the window never scrolls in this app. Hidden while a
 *  search is active: search rows are not the paged list. */
const shell = ref<{ scroller: HTMLElement | null } | null>(null)
const moreSentinel = ref<HTMLElement | null>(null)
useInfiniteScroll(
  () => shell.value?.scroller ?? null,
  moreSentinel,
  () => void briefs.loadMoreIndex(),
  { disabled: () => briefs.indexLoading || !briefs.canLoadMoreIndex },
)

// The dek shares the title's cell rather than taking a column of its own: it is
// null on plenty of briefs, and an empty column is just a gap that makes the
// header row look broken.
//
// `time` is a column and not a suffix on `date` because several briefs can share
// a day: with the date alone those rows are visually identical and the archive
// reads as duplicated.
//
// The header does NOT name the reader's zone, and that is a size decision
// rather than an oversight. `time (GMT+12)` is thirteen characters over a cell
// holding five, and since a column `width` is a floor rather than a cap it
// took 130px at 390px wide -- twice the date column, squeezing the title to a
// sliver on a phone. This column's job, per the note above, is telling two
// briefs on one day apart; it is a disambiguator, not a clock anyone reads an
// hour off. The brief itself names the zone on its dateline, which is where an
// absolute time is actually read.
const COLUMNS: Column[] = [
  { key: 'date', label: 'date', width: '7rem' },
  { key: 'generated_at', label: 'time', width: '4.5rem' },
  { key: 'title', label: 'title' },
  { key: 'block_count', label: 'blocks', numeric: true, width: '5rem' },
]

const WHERE_LABEL: Record<string, string> = {
  title: 'title',
  dek: 'summary',
  headline: 'headline',
  body: 'in the body',
}

function open(row: { id: string }) {
  // The newest row goes to `/`, its real home. See `briefs.pathFor`.
  void router.push(briefs.pathFor(row.id))
}

</script>

<template>
  <ViewShell ref="shell" width="wide" scroll="page" :error="briefs.indexError" :identity="identity">
    <!-- The filter changes WHAT YOU SEE, so it is a control, not an action.
         Hidden on an empty corpus: a search box over nothing is chrome that
         invites a reader to look for something that cannot be there. -->
    <template v-if="!isEmpty" #search>
      <FilterField
        v-model="filter"
        hotkey
        collapsible
        hotkey-group="archive"
        label="Search briefs"
        placeholder="title, text or a date…"
        :busy="briefs.searching"
      />
    </template>

    <EmptyCorpus v-if="isEmpty" />

    <Table
      v-else
      :columns="COLUMNS"
      :rows="rows"
      row-key="id"
      selectable
      :active-key="activeBriefId"
      @select="open"
    >
      <template #cell:date="{ value }">
        <span class="mono text-xs">{{ value }}</span>
      </template>
      <template #cell:generated_at="{ value }">
        <span class="mono text-xs text-text-tertiary">{{ hhmm(value as string | null) }}</span>
      </template>
      <template #cell:title="{ row }">
        <span class="title line-clamp-2 text-text-primary">{{ row.title }}</span>
        <span v-if="row.dek" class="dek block mt-(--space-half) text-xs text-text-tertiary truncate">{{ row.dek }}</span>
        <!-- WHY this row matched. A search that returns rows without showing
             the hit makes the reader open each one to find out, which is the
             scrolling the search was meant to replace. -->
        <template v-if="row.hit">
          <span
            v-for="(match, i) in row.hit.matches"
            :key="i"
            class="hit block mt-(--space-half) text-xs text-text-tertiary"
          >
            <span class="mono text-text-quiet">{{ WHERE_LABEL[match.where] ?? match.where }}</span>
            ·
            {{ match.snippet }}
          </span>
          <span
            v-if="(row.hit.match_count ?? 0) > (row.hit.matches?.length ?? 0)"
            class="block mt-(--space-half) text-xs text-text-quiet"
          >
            +{{ (row.hit.match_count ?? 0) - (row.hit.matches?.length ?? 0) }} more in this brief
          </span>
        </template>
      </template>
      <template #empty>
        <EmptyState :title="filter ? 'No brief matches that' : 'No briefs yet'">
          <template v-if="filter">
            This searches titles, summaries, item headlines and the body of every
            brief, plus the date and time columns. Nothing matched
            “{{ filter }}”.
          </template>
          <template v-else>
            Every run writes its own brief. Trigger one from Runs.
          </template>
        </EmptyState>
      </template>
    </Table>

    <div
      v-if="!isEmpty && !filter.trim() && briefs.canLoadMoreIndex"
      ref="moreSentinel"
      class="pt-4 flex items-center justify-center"
    >
      <Button variant="text" icon="down" :disabled="briefs.indexLoading" @click="briefs.loadMoreIndex()">
        Load more
      </Button>
    </div>
  </ViewShell>
</template>
