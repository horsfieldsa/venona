<script setup lang="ts">
/**
 * Every merge ever made, and a way to reverse any of them.
 *
 * The queue's `U` undo is one step, in memory, gone on reload — fine for a
 * misclick, useless for "that merge an hour ago was wrong". The capability to
 * fix it already existed on both sides: `GET /curation/merges?active_only=false`
 * lists them and `POST /curation/merges/{id}/unmerge` reverses one, which is
 * what `venona entity merges` has been printing all along.
 *
 * Nothing is destroyed by a merge or by reversing one — both are rows in
 * `entity_mappings`, which is why this is safe to expose (contract C1).
 */
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import Button from '@/components/ui/Button.vue'
import Chip from '@/components/ui/Chip.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import Table, { type Column } from '@/components/ui/Table.vue'
import { useInfiniteScroll } from '@/composables/useInfiniteScroll'
import { emptyIntent, toGraphQuery } from '@/router/graphRoute'
import { useCurationStore } from '@/stores/curation'

const curation = useCurationStore()
const router = useRouter()

onMounted(() => void curation.loadMerges())

/** Scrolling near the end pulls the next page. The observer root is this
 *  component's own scroll box — the view above it is `scroll="none"` — and
 *  the Load more button stays as the keyboard path. */
const scrollBox = ref<HTMLElement | null>(null)
const moreSentinel = ref<HTMLElement | null>(null)
useInfiniteScroll(scrollBox, moreSentinel, () => void curation.loadMoreMerges(), {
  disabled: () => curation.mergesLoading || !curation.mergesCanLoadMore,
})

// `who` sits beside `when` because they answer the same question. The column
// existed in the database from migration 0002 and reached no screen, which made a
// merge attributable in principle and anonymous in practice -- and on a corpus
// several people read, "somebody merged these" is not something a reader can act
// on.
const COLUMNS: Column[] = [
  { key: 'created_at', label: 'when', width: '9rem' },
  { key: 'merged_by', label: 'who', width: '11rem' },
  { key: 'from_name', label: 'merged away' },
  { key: 'to_name', label: 'survivor' },
  { key: 'reason', label: 'reason' },
  { key: 'actions', label: '', width: '9rem' },
]
</script>

<template>
  <div ref="scrollBox" class="history overflow-y-auto min-h-0">
    <Table :columns="COLUMNS" :rows="curation.merges" row-key="merge_id">
      <template #cell:created_at="{ value }">
        <span class="mono text-xs text-text-tertiary">{{ String(value ?? '').slice(0, 16).replace('T', ' ') }}</span>
      </template>
      <!-- An em dash, not "unknown" or a blank. NULL is a real answer here --
           the CLI and the pipeline both record it -- and a word like "unknown"
           reads as a lookup that failed rather than a verdict nobody signed. -->
      <template #cell:merged_by="{ value }">
        <span v-if="value" class="text-xs text-text-secondary truncate">{{ value }}</span>
        <span v-else class="text-xs text-text-tertiary" title="recorded without a signed-in user">
          &mdash;
        </span>
      </template>
      <template #cell:from_name="{ row }">
        <span :class="{ 'struck line-through text-text-tertiary': row.active }">
          {{ row.from_name ?? row.from_id }}
        </span>
      </template>
      <template #cell:to_name="{ row }">
        <Button
          variant="text"
          @click="
            router.push({
              name: 'graph',
              query: toGraphQuery({
                ...emptyIntent('focus'),
                focusId: row.to_id,
              }),
            })
          "
        >
          {{ row.to_name ?? row.to_id }}
        </Button>
      </template>
      <template #cell:reason="{ value }">
        <span class="text-text-tertiary text-xs truncate">{{ value }}</span>
      </template>
      <template #cell:actions="{ row }">
        <Button
          v-if="row.active"
          variant="text"
          icon="split"
          label="Unmerge"
          :disabled="curation.busy"
          @click="curation.unmerge(row)"
        >
          unmerge
        </Button>
        <Chip v-else tone="neutral">reversed</Chip>
      </template>
      <template #empty>
        <EmptyState title="No merges yet">
          Every merge lands here, however it was made, and any of them can be
          reversed from this list.
        </EmptyState>
      </template>
    </Table>

    <div
      v-if="curation.mergesCanLoadMore"
      ref="moreSentinel"
      class="pt-4 flex items-center justify-center"
    >
      <Button variant="text" icon="down" :disabled="curation.mergesLoading" @click="curation.loadMoreMerges()">
        Load more
      </Button>
    </div>
  </div>
</template>
