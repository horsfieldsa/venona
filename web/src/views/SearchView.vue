<script setup lang="ts">
/**
 * Search across entities AND brief bodies.
 *
 * WHAT THIS ADDS OVER THE ⌘K PALETTE. Both call `GET /briefs/search`, so both
 * reach body text; the palette caps at five briefs and six names and renders a
 * jump list. This page raises the caps, reports the whole-corpus total, and
 * renders the windowed SNIPPET per match with the field it matched in. The
 * palette links here for exactly that reason.
 *
 * TWO SOURCES, ONE PAGE, AND THEY ANSWER DIFFERENT QUESTIONS. An entity hit
 * means "Venona knows this thing"; a brief hit means "somebody wrote about it on
 * this date". Merging them into one relevance-ranked list would force a
 * comparison between a noun and an article that neither of them supports, so
 * they stay in labelled groups.
 *
 * The query lives in `?q=`, so a search is a link. That is the reason this
 * exists as a route at all rather than as a taller palette.
 */
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import Chip from '@/components/ui/Chip.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import FilterField from '@/components/ui/FilterField.vue'
import SectionHead from '@/components/ui/SectionHead.vue'
import ViewShell from '@/components/ui/ViewShell.vue'
import { apiGet, errorText } from '@/api/client'
import { useStaleGuard } from '@/composables/useStaleGuard'
import { entityColourVar } from '@/lib/entityColour'
import { toEntityRoute } from '@/router/entityRoute'
import type { BriefSearchHit, BriefSearchResults } from '@/types/brief'
import type { EntityHit, EntitySearchResults } from '@/types/search'

const route = useRoute()
const router = useRouter()

const term = ref(typeof route.query.q === 'string' ? route.query.q : '')
const entities = ref<EntityHit[]>([])
const briefs = ref<BriefSearchHit[]>([])
/** The server's whole-corpus figures, not the page's. The header once summed
 *  the two capped lists and announced it as the result count — a total smaller
 *  than the set it claims to describe, on a jump-to surface. */
const briefTotal = ref(0)
const loading = ref(false)
const error = ref<string | null>(null)

const ENTITY_LIMIT = 20

/** Two searches in flight can land out of order, and the slower one would
 *  overwrite fresher results. `useStaleGuard` is the shared answer -- do not
 *  hand-roll a fourth copy. */
const guard = useStaleGuard()

const total = computed(() => entities.value.length + briefs.value.length)
const identity = computed(() => {
  if (!term.value) return 'search'
  // `20+` when the entity list hits its cap: that endpoint reports no total,
  // and stating the cap as the count is the lie LookupView's header comment
  // warns about. The brief figure IS a whole-corpus total, so it says so.
  const entityCount =
    entities.value.length >= ENTITY_LIMIT ? `${ENTITY_LIMIT}+` : `${entities.value.length}`
  return `search · "${term.value}" · ${entityCount} names · ${briefTotal.value} briefs`
})

async function run(q: string) {
  const trimmed = q.trim()
  if (trimmed.length < 2) {
    entities.value = []
    briefs.value = []
    briefTotal.value = 0
    return
  }
  const isCurrent = guard.claim()
  loading.value = true
  error.value = null
  try {
    // Both at once. They are independent reads against different stores, and
    // waiting for the slower one to start the faster one would double the
    // latency of the page for no benefit.
    const [entityResults, briefResults] = await Promise.all([
      apiGet<EntitySearchResults>(`/search?q=${encodeURIComponent(trimmed)}&limit=${ENTITY_LIMIT}`),
      apiGet<BriefSearchResults>(`/briefs/search?q=${encodeURIComponent(trimmed)}&limit=20`),
    ])
    if (!isCurrent()) return
    entities.value = entityResults.entities ?? []
    briefs.value = briefResults.items ?? []
    briefTotal.value = briefResults.total ?? briefs.value.length
  } catch (err) {
    if (!isCurrent()) return
    error.value = errorText(err, 'search failed')
  } finally {
    if (isCurrent()) loading.value = false
  }
}

function submit() {
  // `replace`, not `push`: refining a query is one act of searching, and a
  // history stack with every keystroke in it makes Back useless.
  void router.replace({ name: 'search', query: term.value.trim() ? { q: term.value.trim() } : {} })
}

watch(
  () => route.query.q,
  (q) => {
    term.value = typeof q === 'string' ? q : ''
    void run(term.value)
  },
  { immediate: true },
)
</script>

<template>
  <ViewShell width="wide" :loading="false" :error="error" :identity="identity">
    <!-- The box that drives this page is the page's one control, so it sits
         where every other route's filter sits rather than at the top of the
         column. Enter writes the term into `?q=`, which is what makes a
         search a link. -->
    <template #search>
      <FilterField
        v-model="term"
        hotkey
        collapsible
        hotkey-group="search"
        label="Search names and briefs"
        placeholder="names and brief text…"
        :busy="loading"
        @submit="submit"
      />
    </template>

    <!-- No second "searching…" line: the field carries the spinner, next to the
         text the request is for. -->
    <EmptyState
      v-if="!loading && term.trim().length >= 2 && !total"
      title="Nothing matched that."
      class="mt-4"
    >
      Every name Venona knows, the other names it goes by, and the full text of every brief are all
      searched. A term Venona has never seen returns nothing rather than a guess.
    </EmptyState>

    <template v-else-if="total">
      <template v-if="entities.length">
        <SectionHead
          as="h2"
          :label="entities.length >= ENTITY_LIMIT ? `names · top ${ENTITY_LIMIT}` : 'names'"
          rule
          class="mt-6"
        />
        <ul class="mt-3 flex flex-col">
          <li
            v-for="hit in entities"
            :key="hit.id"
            class="flex items-center gap-2 border-b border-border-subtle py-2"
          >
            <span
              class="dot w-(--dot-md) h-(--dot-md) rounded-pill flex-none"
              :style="{ background: entityColourVar({ id: hit.id, kind: 'entity' }) }"
              aria-hidden="true"
            />
            <!-- The name's own page, not the canvas. A search result is a
                 question about one name, and the graph answers a different one:
                 it opens on a canvas the reader then has to orient themselves
                 in, with the definition they were looking for inside a hover. -->
            <RouterLink :to="toEntityRoute(hit.id)" class="font-ui text-sm text-text-primary">
              {{ hit.name }}
            </RouterLink>
            <Chip tone="neutral">{{ hit.type }}</Chip>
          </li>
        </ul>
      </template>

      <template v-if="briefs.length">
        <SectionHead
          as="h2"
          :label="briefTotal > briefs.length ? `briefs · ${briefs.length} of ${briefTotal}` : 'briefs'"
          rule
          class="mt-6"
        />
        <ul class="mt-3 flex flex-col gap-4">
          <li v-for="hit in briefs" :key="hit.brief.id" class="border-b border-border-subtle pb-4">
            <RouterLink
              :to="`/briefs/${encodeURIComponent(hit.brief.id)}`"
              class="font-ui text-sm text-text-primary"
            >
              {{ hit.brief.title }}
            </RouterLink>
            <p class="mt-1 font-ui text-2xs text-text-quiet">
              {{ hit.brief.date }} ·
              {{ hit.match_count === 1 ? '1 match' : `${hit.match_count} matches` }}
            </p>
            <!-- The snippet is plain text from the server and is rendered as
                 text. C3's rule that the client never parses markdown applies
                 here too, and a highlight injected as markup would be the one
                 place in the app that broke it. -->
            <p
              v-for="(match, i) in hit.matches"
              :key="i"
              class="mt-2 font-prose text-sm leading-normal text-text-tertiary"
            >
              <span v-if="match.headline" class="text-text-secondary">{{ match.headline }} · </span>
              {{ match.snippet }}
            </p>
          </li>
        </ul>
      </template>
    </template>

    <EmptyState v-else title="Search Venona's reading." class="mt-4">
      Two characters is enough. Names match on any name they go by; briefs match on title, summary
      and body text.
    </EmptyState>
  </ViewShell>
</template>
