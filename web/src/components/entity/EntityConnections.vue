<script setup lang="ts">
/**
 * Everything one name is connected to, with the publishers behind each.
 *
 * Two things make this more than a list.
 *
 * THE PUBLISHER COUNT IS THE POINT: one outlet and three are different claims,
 * and the canvas already draws the first dashed. Saying it in WORDS too is the
 * rule that epistemic weakness must survive a greyscale print.
 *
 * EVERY ROW OPENS ONTO ITS EVIDENCE. An intelligence graph whose edges cannot
 * be audited is a rumour mill, so the verbatim sentence is one click away, and
 * it is fetched on that click rather than for every row on mount --
 * `Disclosure`'s `@open` fires exactly once.
 *
 * "Load more" and no scroll sentinel: this list is not the last thing on the
 * page, so an observer here would append a page every time the reader scrolled
 * past it and the sections below would recede as fast as they were reached.
 */
import { ref } from 'vue'

import Button from '@/components/ui/Button.vue'
import Disclosure from '@/components/ui/Disclosure.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import SectionHead from '@/components/ui/SectionHead.vue'
import Spinner from '@/components/ui/Spinner.vue'
import StatusNote from '@/components/ui/StatusNote.vue'
import { apiGet, errorText } from '@/api/client'
import { dayLabel } from '@/lib/dates'
import { entityColourVar } from '@/lib/entityColour'
import { entityTypeLabel } from '@/lib/entityType'
import { predicateLabel } from '@/lib/predicate'
import { safeHref } from '@/lib/safeHref'
import { toEntityRoute } from '@/router/entityRoute'
import type { Connection, EdgeProvenance } from '@/types/entity'

defineProps<{
  /** This page's own name, so a row can be read as a whole claim. */
  subject: string
  items: Connection[]
  total: number
  loading: boolean
  error: string | null
  canLoadMore: boolean
}>()
defineEmits<{ (e: 'more'): void }>()

/** One fetch per row, keyed by the id the server gave us. */
const evidence = ref<Record<string, EdgeProvenance>>({})
const failed = ref<Record<string, string>>({})

async function load(edgeId: string) {
  try {
    evidence.value[edgeId] = await apiGet<EdgeProvenance>(
      `/edges/${encodeURIComponent(edgeId)}/provenance`,
    )
  } catch (err) {
    failed.value[edgeId] = errorText(err, 'could not read the evidence for this')
  }
}

/** Written once and bound in both branches. A class list spelled out twice is
 *  two places for one treatment to drift. */
const TYPE_CLASS = 'text-2xs font-medium tracking-label uppercase'

function otherColour(row: Connection): string {
  return entityColourVar({ id: row.other_id, kind: 'entity' })
}

function publishers(count: number): string {
  return count === 1 ? '1 publisher' : `${count} publishers`
}
</script>

<template>
  <section class="flex flex-col gap-2 min-w-0">
    <SectionHead as="h2" :label="total ? `connections · ${total}` : 'connections'" rule />

    <StatusNote v-if="error" kind="error">{{ error }}</StatusNote>

    <EmptyState v-else-if="!items.length && !loading" title="Nothing connected yet.">
      A connection is drawn once both names it joins have been corroborated. This one is waiting
      for that, so what Venona has read about it is in the articles below.
    </EmptyState>

    <ul v-else class="flex flex-col m-0 p-0 list-none">
      <li v-for="row in items" :key="row.edge_id" class="border-b border-border-subtle">
        <Disclosure :hint="publishers(row.publisher_count)" @open="load(row.edge_id)">
          <!-- THE WHOLE CLAIM, subject first, in the order it is asserted.
               A row reading "competes for · Next Generation Command and
               Control" makes the reader supply the subject and gives them no
               way to tell an outgoing connection from an incoming one, which is
               the difference between this name managing a program and a program
               being managed by it. So the row names both ends and puts them
               round the right way; the end that is NOT this page is the link. -->
          <template #label>
            <span class="flex items-baseline gap-2 flex-wrap min-w-0">
              <template v-if="row.direction === 'out'">
                <span class="font-ui text-sm text-text-tertiary">{{ subject }}</span>
                <span class="font-ui text-2xs text-text-quiet">{{ predicateLabel(row.predicate) }}</span>
                <!-- `@click.stop`: a click anywhere in a `<summary>` toggles the
                     `<details>`, so without it following the link also expands
                     the row the reader is leaving. -->
                <RouterLink
                  :to="toEntityRoute(row.other_id)"
                  class="font-ui text-sm text-text-primary"
                  @click.stop
                >
                  {{ row.other_name }}
                </RouterLink>
                <span v-if="row.other_type" :class="TYPE_CLASS" :style="{ color: otherColour(row) }">
                  {{ entityTypeLabel(row.other_type) }}
                </span>
              </template>
              <template v-else>
                <RouterLink
                  :to="toEntityRoute(row.other_id)"
                  class="font-ui text-sm text-text-primary"
                  @click.stop
                >
                  {{ row.other_name }}
                </RouterLink>
                <!-- THE TYPE BELONGS TO THE NAME BESIDE IT, which is why it is
                     written out in both branches rather than once after them.
                     Trailing the row it described whichever end happened to be
                     last, so an incoming connection read "Dan Driscoll works
                     for United States Army PERSON" -- the label landing on the
                     Army. -->
                <span v-if="row.other_type" :class="TYPE_CLASS" :style="{ color: otherColour(row) }">
                  {{ entityTypeLabel(row.other_type) }}
                </span>
                <span class="font-ui text-2xs text-text-quiet">{{ predicateLabel(row.predicate) }}</span>
                <span class="font-ui text-sm text-text-tertiary">{{ subject }}</span>
              </template>
            </span>
          </template>

          <StatusNote v-if="failed[row.edge_id]" kind="error">
            {{ failed[row.edge_id] }}
          </StatusNote>
          <template v-else-if="evidence[row.edge_id]">
            <p
              v-for="a in evidence[row.edge_id].assertions"
              :key="a.assertion_id"
              class="m-0 mb-3 flex flex-col gap-1"
            >
              <!-- The sentence someone published, verbatim. This is the whole
                   reason the row opens. -->
              <span class="font-prose text-sm leading-normal text-text-secondary">
                {{ a.quote }}
              </span>
              <span class="font-ui text-2xs text-text-quiet">
                {{ dayLabel(a.published_at) }} · {{ a.publisher || 'unattributed' }}
                <template v-if="a.url">
                  ·
                  <a :href="safeHref(a.url)" target="_blank" rel="noopener noreferrer">read it</a>
                </template>
              </span>
            </p>
            <p
              v-if="evidence[row.edge_id].declared"
              class="m-0 font-ui text-2xs text-text-quiet"
            >
              Stated when this domain was set up, rather than read in an article.
            </p>
          </template>
          <span v-else class="flex items-center gap-2 text-text-tertiary text-xs">
            <Spinner :size="12" />loading
          </span>
        </Disclosure>
      </li>
    </ul>

    <!-- The button IS the load path, not a fallback for one. -->
    <div v-if="canLoadMore || loading" class="flex items-center gap-2 self-start">
      <Button v-if="canLoadMore" variant="outline" label="Load more" @click="$emit('more')">
        Load more
      </Button>
      <span v-if="loading" class="flex items-center gap-2 text-text-tertiary text-xs">
        <Spinner :size="12" />loading
      </span>
    </div>
  </section>
</template>
