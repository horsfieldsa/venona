<script setup lang="ts">
/**
 * One name, at an address.
 *
 * Everything here was reachable and none of it ADDRESSABLE: a hover card gives
 * a definition, three articles and ten neighbours inside 400px, and a search
 * result dropped you on the canvas. Nothing to open in a second tab.
 *
 * THE FIRST SCREEN COSTS ONE REQUEST, and usually not even that: the card
 * carries ten neighbours and four articles and is normally warm from the hover
 * that preceded the click. The full lists are fetched only when asked for.
 *
 * `width="wide"` AND NO SIDE RAIL: `prose`'s rail becomes `display: contents`
 * below 1180px and `#side` lands after the whole column, which under a column
 * that grows by the page is unreachable rather than deprioritised.
 */
import { computed, ref, watch, type Ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import EntityConnections from '@/components/entity/EntityConnections.vue'
import EntityDefinition from '@/components/entity/EntityDefinition.vue'
import EntityMiniGraph from '@/components/entity/EntityMiniGraph.vue'
import EntitySources from '@/components/entity/EntitySources.vue'
import ApiUnreachable from '@/components/shared/ApiUnreachable.vue'
import EmptyCorpus from '@/components/shared/EmptyCorpus.vue'
import Button from '@/components/ui/Button.vue'
import Chip from '@/components/ui/Chip.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import SectionHead from '@/components/ui/SectionHead.vue'
import StateNotice from '@/components/ui/StateNotice.vue'
import ViewShell from '@/components/ui/ViewShell.vue'
import { apiGet } from '@/api/client'
import { useServiceHealth } from '@/composables/useServiceHealth'
import { useStaleGuard } from '@/composables/useStaleGuard'
import { dayLabel } from '@/lib/dates'
import { aliasOriginLabel, mergeReasonLabel } from '@/lib/curation'
import { entityColourVar } from '@/lib/entityColour'
import { entityTypeLabel } from '@/lib/entityType'
import { createPagedList, type PagedList } from '@/lib/paging'
import { safeHref } from '@/lib/safeHref'
import { entityIdFromRoute, isGlossaryId, toEntityRoute } from '@/router/entityRoute'
import { emptyIntent, toGraphQuery } from '@/router/graphRoute'
import { applyTitle } from '@/router/index'
import { useEntitiesStore } from '@/stores/entities'
import { useRelevanceStore } from '@/stores/relevance'
import { useSessionStore } from '@/stores/session'
import { useToastsStore } from '@/stores/toasts'
import type { Signal } from '@/types/brief'
import type { Connection, EntityConnections as Conns, EntityRecord, EntitySources as Srcs } from '@/types/entity'

const route = useRoute()
const router = useRouter()
const entities = useEntitiesStore()
const relevance = useRelevanceStore()
const session = useSessionStore()
const toasts = useToastsStore()
const { apiDown } = useServiceHealth()

/** Null when the params are not a shape C4 could have minted. That is a "no
 *  such name", not a request worth sending. */
const entityId = computed(() => entityIdFromRoute(route.params))

const card = computed(() => (entityId.value ? entities.get(entityId.value) : null))
const error = ref<string | null>(null)

/**
 * "No card yet" is not the same claim as "still fetching".
 *
 * `fetchOne` records a 404 in `missing` and resolves to null, so a card that
 * never arrives would spin for ever if `loading` were `card === null`. This is
 * the derivation `EntityHoverCard` documents, and the same bug is one line away
 * here.
 */
const loading = computed(
  () => !error.value && !card.value && !!entityId.value && !entities.missing.has(entityId.value),
)

/** An acronym is a term, not a name in the graph: it has no connections and no
 *  articles of its own, so the page says so rather than showing two empty
 *  boxes with no explanation. */
const isTerm = computed(() => !!entityId.value && isGlossaryId(entityId.value))

const guard = useStaleGuard()

const conns = createPagedList<Connection>({
  page: 25,
  key: (c) => c.edge_id,
  fetchPage: async (limit, offset) => {
    const body = await apiGet<Conns>(
      `/entities/${encodeURIComponent(entityId.value ?? '')}/connections?limit=${limit}&offset=${offset}`,
    )
    return { items: body.items ?? [], total: body.total ?? 0 }
  },
})

const srcs = createPagedList<Signal>({
  page: 20,
  key: (s) => s.article_id,
  fetchPage: async (limit, offset) => {
    const body = await apiGet<Srcs>(
      `/entities/${encodeURIComponent(entityId.value ?? '')}/sources?limit=${limit}&offset=${offset}`,
    )
    return { items: body.items ?? [], total: body.total ?? 0 }
  },
})

/** Opened on demand. Until then the card's own ten neighbours and four articles
 *  are what is on screen, which is the whole first-screen-is-free design. */
const showAllConnections = ref(false)
const showAllSources = ref(false)

const record = ref<EntityRecord | null>(null)

/**
 * A LAPSED READER IS ALREADY HANDLED, and not here.
 *
 * The card is part of the archive ADR 0211 promises, so the top of this page
 * renders for them; these two lists are the product and are entitled routes. A
 * 403 from one carries `subscription_required`, and `main.ts`'s forbidden hook
 * already answers that everywhere at once -- one keyed toast, then the billing
 * page, which is the one refusal worth a redirect because it is the one the
 * reader can act on. A second answer written into this view would be a second
 * sentence for one state, which is the drift `errorText` exists to prevent.
 */
async function open<T>(list: PagedList<T>, flag: Ref<boolean>) {
  flag.value = true
  await list.refresh()
}

// Two wrappers rather than passing the ref from the template: a `ref` in a
// template is already unwrapped, so the flag would arrive as a plain boolean.
const openConnections = () => open(conns, showAllConnections)
const openSources = () => open(srcs, showAllSources)

async function load() {
  const id = entityId.value
  error.value = null
  record.value = null
  showAllConnections.value = false
  showAllSources.value = false
  conns.reset()
  srcs.reset()
  if (!id) {
    error.value = 'no such name'
    return
  }
  const isCurrent = guard.claim()
  const fetched = await entities.fetchOne(id)
  if (!isCurrent()) return

  if (!fetched) {
    error.value = 'no such name'
    return
  }
  // The card follows merge chains and answers for the SURVIVOR, so a link to a
  // name that has since been merged away lands here with a card belonging to a
  // different id than the address. Move the address to match it, or the page
  // shows one name under another name's URL for ever.
  //
  // Not for a term: `glossary_card` follows no merge chain, and for an
  // ambiguous surface it falls back to the highest-confidence reading, so this
  // would silently rewrite "SDN" into one of its two meanings.
  if (fetched.id !== id && !isGlossaryId(id)) {
    void router.replace(toEntityRoute(fetched.id))
    return
  }
  // The tab title, now that the name has arrived. Declared as a function on the
  // route so this re-stamp has something to read.
  applyTitle()

  if (session.isAdmin()) {
    try {
      record.value = await apiGet<EntityRecord>(`/entities/${encodeURIComponent(id)}/record`)
    } catch {
      // The record is an operator's extra. A page that failed to render it is
      // still the page, so this is deliberately silent.
    }
  }
}

watch(() => route.params, () => void load(), { immediate: true })

// ── the head ────────────────────────────────────────────────────────

const colour = computed(() =>
  card.value ? entityColourVar({ id: card.value.id, kind: card.value.kind }) : undefined,
)

/** Aliases minus the name itself. The resolver's set contains the canonical
 *  name by construction, and the card was printing "Office of Naval Research"
 *  as the first of its own other names. */
const aliases = computed(() => {
  const name = (card.value?.name ?? '').trim().toLowerCase()
  return (card.value?.aliases ?? []).filter((a) => a.trim().toLowerCase() !== name)
})

/** The definition's own sources, which is what `EntityDefinition`'s "below"
 *  wording promises sits under it. A different list from "where it appeared":
 *  these are the articles the MEANING rests on. */
const definitionSources = computed(() => {
  const c = card.value
  if (!c) return []
  return c.definition_sources?.length ? c.definition_sources : (c.signals ?? [])
})

const previewNeighbours = computed(() => card.value?.mini_graph?.neighbors ?? [])

/**
 * The counts, each omitted until the read that knows it has landed.
 *
 * `ViewShell`'s own docblock records the archive announcing `archive · 0 briefs`
 * above the screen saying the API was unreachable: the one sentence on the page
 * making a claim about the corpus, and it was false. A zero here would be the
 * same lie for the beat before the card arrives.
 */
const identity = computed(() => {
  const parts: string[] = []
  if (card.value?.type) parts.push(entityTypeLabel(card.value.type).toLowerCase())
  else if (isTerm.value) parts.push('acronym')
  if (card.value?.neighbor_count) parts.push(`${card.value.neighbor_count} connections`)
  if (srcs.total.value) parts.push(`${srcs.total.value} articles`)
  return parts.join(' · ')
})

const graphTarget = computed(() => {
  if (!card.value || isTerm.value) return null
  return { name: 'graph', query: toGraphQuery({ ...emptyIntent('focus'), focusId: card.value.id }) }
})

// ── follow ──────────────────────────────────────────────────────────

void relevance.loadFollows()
const followPending = ref(false)

async function toggleFollow() {
  const id = card.value?.id
  if (!id || followPending.value) return
  followPending.value = true
  const wanted = !relevance.isFollowed(id)
  try {
    if (wanted) await relevance.follow(id)
    else await relevance.unfollow(id)
    toasts.push({
      text: relevance.isFollowed(id) ? 'now following' : 'stopped following',
      kind: 'success',
    })
  } catch {
    toasts.push({ text: relevance.error || 'could not update your follows', kind: 'error' })
  } finally {
    followPending.value = false
  }
}
</script>

<template>
  <ViewShell width="wide" :loading="loading" :error="error" :identity="identity">
    <!-- BOTH IN `actions`, at the trailing edge. `controls` is centred on the
         window, which is right for tabs and scope chips and wrong for a pair of
         buttons that belong together: the graph link drifted to the middle of
         the bar with nothing beside it. Two actions, one of them filled, which
         is the ceiling `docs/DESIGN.md` sets. -->
    <template #actions>
      <RouterLink v-if="graphTarget" :to="graphTarget">
        <Button variant="outline" icon="waypoints" label="Open in graph">Open in graph</Button>
      </RouterLink>
      <Button
        v-if="card && card.kind === 'entity'"
        :variant="relevance.isFollowed(card.id) ? 'outline' : 'primary'"
        icon="target"
        :label="relevance.isFollowed(card.id) ? 'Following' : 'Follow'"
        :disabled="followPending"
        @click="toggleFollow"
      >
        {{ relevance.isFollowed(card.id) ? 'Following' : 'Follow' }}
      </Button>
    </template>

    <!-- Three real failures, told apart. An outage is not this page's error,
         and a name that does not exist is not an outage. -->
    <template #error>
      <ApiUnreachable v-if="apiDown" class="mt-4" />
      <StateNotice
        v-else-if="entityId"
        status="404 · no such name"
        heading="Venona has no record of this name"
        class="mt-4"
      >
        It may have been merged into another name, or the address may be wrong. Searching for it
        will find it under whatever it is called now.
        <template #action>
          <RouterLink to="/search">
            <Button variant="outline" icon="search" label="Search">Search</Button>
          </RouterLink>
        </template>
      </StateNotice>
      <EmptyCorpus v-else steps class="mt-4" />
    </template>

    <template v-if="card">
      <!-- ── the name ──────────────────────────────────────────────── -->
      <header class="flex flex-col gap-(--space-half)">
        <!-- The type is an EYEBROW, not a chip beside the name. As a `Chip` it
             shares the title's row, and `Chip` is `whitespace-nowrap` while a
             name wraps, so the name is what breaks: "US Department of Defense"
             went to two lines to make room for the word "Organization". -->
        <p
          v-if="card.type || isTerm"
          class="text-2xs font-medium tracking-label uppercase"
          :style="{ color: colour }"
        >
          {{ entityTypeLabel(card.type) || 'acronym' }}
        </p>
        <!-- `text-xl`, matching the brief's own title. `--text-3xl` is display
             size and belongs to the three page-occupying states that use it. -->
        <h1 class="font-ui text-xl font-normal leading-snug text-text-primary m-0 [overflow-wrap:anywhere]">
          {{ card.name || card.surface || card.id }}
        </h1>
        <p v-if="card.expansion && card.expansion !== card.name" class="m-0 mono text-xs text-text-tertiary">
          {{ card.expansion }}
        </p>
        <!-- `provisional` in words. It is a real state and a reader has to know
             the page is thinner for a reason. -->
        <p v-if="card.status === 'provisional'" class="m-0 font-ui text-2xs text-text-quiet">
          Venona has seen this once and has not corroborated it yet.
        </p>
      </header>

      <!-- ── what it is ────────────────────────────────────────────── -->
      <div class="mt-6 flex flex-col gap-2">
        <EntityDefinition :card="card" />
        <!-- Directly under the definition, because that is what its provenance
             line says is there. These are the articles the MEANING rests on,
             which is a different list from every article the name appeared
             in. -->
        <ul v-if="definitionSources.length" class="flex flex-col m-0 p-0 list-none">
          <li v-for="s in definitionSources" :key="s.article_id" class="font-ui text-2xs text-text-quiet">
            <component
              :is="s.url ? 'a' : 'span'"
              v-bind="s.url ? { href: safeHref(s.url), target: '_blank', rel: 'noopener noreferrer' } : {}"
            >
              {{ s.title }}
            </component>
            · {{ dayLabel(s.published_at) }}
          </li>
        </ul>
      </div>


      <!-- ── also known as ─────────────────────────────────────────── -->
      <section v-if="aliases.length" class="mt-6 flex flex-col gap-2">
        <SectionHead as="h2" label="also known as" rule />
        <div class="flex items-center flex-wrap gap-1">
          <Chip v-for="alias in aliases" :key="alias" size="sm">{{ alias }}</Chip>
        </div>
      </section>

      <!-- ── connections ───────────────────────────────────────────── -->
      <section v-if="isTerm" class="mt-6 flex flex-col gap-2">
        <SectionHead as="h2" label="connections" rule />
        <EmptyState title="An acronym is a term, not a name in the graph.">
          Connections are drawn between the things an acronym refers to. Open the name it expands
          to and you will find them there.
        </EmptyState>
      </section>

      <EntityConnections
        v-else-if="showAllConnections"
        class="mt-6"
        :subject="card.name || card.id"
        :items="conns.items.value"
        :total="conns.total.value"
        :loading="conns.loading.value"
        :error="conns.error.value"
        :can-load-more="conns.canLoadMore.value"
        @more="conns.loadMore"
      />

      <!-- The card's own ten, until somebody asks for the rest. No request. -->
      <section v-else class="mt-6 flex flex-col gap-2">
        <SectionHead
          as="h2"
          :label="card.neighbor_count ? `connections · ${card.neighbor_count}` : 'connections'"
          rule
        />
        <EntityMiniGraph
          v-if="previewNeighbours.length && card.mini_graph"
          :graph="card.mini_graph"
          :width="420"
          @select="(id: string) => router.push(toEntityRoute(id))"
        />
        <EmptyState v-else title="Nothing connected yet.">
          A connection is drawn once both names it joins have been corroborated. What Venona has
          read about this one is in the articles below.
        </EmptyState>
        <Button
          v-if="card.neighbor_count"
          variant="outline"
          label="Show all connections"
          class="self-start"
          @click="openConnections"
        >
          Show all connections
        </Button>
      </section>

      <!-- ── where it appeared ─────────────────────────────────────── -->
      <EntitySources
        v-if="showAllSources"
        class="mt-6"
        :items="srcs.items.value"
        :total="srcs.total.value"
        :loading="srcs.loading.value"
        :error="srcs.error.value"
        :can-load-more="srcs.canLoadMore.value"
        @more="srcs.loadMore"
      />
      <section v-else class="mt-6 flex flex-col gap-2">
        <SectionHead as="h2" label="where it appeared" rule />
        <ul v-if="card.signals?.length" class="flex flex-col m-0 p-0 list-none">
          <li
            v-for="s in card.signals"
            :key="s.article_id"
            class="flex flex-col gap-(--space-half) border-b border-border-subtle py-2"
          >
            <component
              :is="s.url ? 'a' : 'span'"
              v-bind="s.url ? { href: safeHref(s.url), target: '_blank', rel: 'noopener noreferrer' } : {}"
              class="font-ui text-sm text-text-primary"
            >
              {{ s.title }}
            </component>
            <span class="font-ui text-2xs text-text-quiet">
              {{ dayLabel(s.published_at) }} · {{ s.publisher || 'unattributed' }}
            </span>
          </li>
        </ul>
        <EmptyState v-else title="No articles yet.">
          This name is known from how this domain was set up rather than from the press. It will
          collect articles as Venona reads.
        </EmptyState>
        <Button
          v-if="card.signals?.length"
          variant="outline"
          label="Show every article"
          class="self-start"
          @click="openSources"
        >
          Show every article
        </Button>
      </section>

      <!-- ── where it came from, for whoever runs this domain ───────── -->
      <section v-if="record && (record.merged_from?.length || record.aliases?.length)" class="mt-8 flex flex-col gap-2">
        <SectionHead as="h2" label="where this name came from" rule />
        <template v-if="record.merged_from?.length">
          <p class="m-0 font-ui text-2xs text-text-quiet">Names that turned out to be this one</p>
          <ul class="flex flex-col m-0 p-0 list-none">
            <li v-for="m in record.merged_from" :key="m.id" class="font-ui text-xs text-text-secondary py-1">
              {{ m.name }}
              <span class="text-text-quiet">
                · {{ mergeReasonLabel(m.reason) }}
                <template v-if="m.merged_by"> · {{ m.merged_by }}</template>
              </span>
            </li>
          </ul>
        </template>
        <template v-if="record.aliases?.length">
          <p class="m-0 mt-2 font-ui text-2xs text-text-quiet">Where each spelling came from</p>
          <ul class="flex flex-col m-0 p-0 list-none">
            <li v-for="a in record.aliases" :key="a.surface" class="font-ui text-xs text-text-secondary py-1">
              {{ a.surface }}
              <span class="text-text-quiet">
                ·
                <template v-if="a.article_title">
                  read in
                  <component
                    :is="a.article_url ? 'a' : 'span'"
                    v-bind="a.article_url ? { href: safeHref(a.article_url), target: '_blank', rel: 'noopener noreferrer' } : {}"
                  >{{ a.article_title }}</component>
                </template>
                <!-- No article on the row is the common case, not a gap: a
                     seeded or generated spelling never had one. Saying where it
                     DID come from beats "no article recorded", which reads as
                     something missing. -->
                <template v-else>{{ aliasOriginLabel(a.added_by) }}</template>
              </span>
            </li>
          </ul>
        </template>
      </section>
    </template>
  </ViewShell>
</template>
