<script setup lang="ts">
import { ref } from 'vue'

import Button from '@/components/ui/Button.vue'
import Chip from '@/components/ui/Chip.vue'
import Spinner from '@/components/ui/Spinner.vue'
import { useStaleGuard } from '@/composables/useStaleGuard'
import { apiGet } from '@/api/client'
import { useGraphStore } from '@/stores/graph'
import FilterField from '@/components/ui/FilterField.vue'
import SectionHead from '@/components/ui/SectionHead.vue'
import Input from '@/components/ui/Input.vue'

/**
 * "How is NGC2 related to the Army?" — the question the whole project exists
 * to answer, so it ships in v1 rather than being deferred.
 *
 * Results render as readable chains with arrow direction, because
 * `NGC2 ←MANAGES— PEO C3N —PART_OF→ US Army` tells you something that a
 * highlighted subgraph alone does not.
 */
const graph = useGraphStore()

const from = ref('')
const to = ref('')
const maxDepth = ref(4)
const fromResults = ref<{ id: string; name: string }[]>([])
const toResults = ref<{ id: string; name: string }[]>([])
const fromId = ref<string | null>(null)
const toId = ref<string | null>(null)
const searching = ref(false)

// Without these, a slow request for "an" lands after a fast one for "and" and
// overwrites it -- so the list shows results for a query the user has already
// moved past. `CommandPalette` and `GraphSearch` each grew their own counter
// for this; these two boxes never had one.
const fromGuard = useStaleGuard()
const toGuard = useStaleGuard()

async function search(term: string, which: 'from' | 'to') {
  if (term.trim().length < 2) {
    if (which === 'from') fromResults.value = []
    else toResults.value = []
    return
  }
  searching.value = true
  // Claimed BEFORE the await. Two boxes, so two independent guards: typing in
  // "to" must not invalidate a request "from" is still waiting on.
  const isCurrent = (which === 'from' ? fromGuard : toGuard).claim()
  try {
    const data = await apiGet<{ entities: { id: string; name: string }[] }>(
      `/graph/search?q=${encodeURIComponent(term)}&limit=6`,
    )
    if (!isCurrent()) return
    if (which === 'from') fromResults.value = data.entities
    else toResults.value = data.entities
  } catch {
    /* a failing search box must not break the panel */
  } finally {
    searching.value = false
  }
}

function pick(entity: { id: string; name: string }, which: 'from' | 'to') {
  if (which === 'from') {
    fromId.value = entity.id
    from.value = entity.name
    fromResults.value = []
  } else {
    toId.value = entity.id
    to.value = entity.name
    toResults.value = []
  }
}

async function find() {
  if (!fromId.value || !toId.value) return
  await graph.findPath(fromId.value, toId.value, maxDepth.value)
}

defineExpose({ pick })
</script>

<template>
  <div class="finder flex flex-col gap-2 p-3 border-t border-t-border-subtle">
    <SectionHead as="h4" label="trace a connection" />

    <FilterField
      v-model="from"
      width="full"
      label="Trace from entity"
      placeholder="from…"
      @update:model-value="search(from, 'from')"
    >
      <ul
        v-if="fromResults.length"
        role="listbox"
        :aria-label="'Matches for the from field'"
        class="results absolute z-overlay left-0 right-0 bg-surface-overlay border border-border-default rounded-sm list-none max-h-(--scroll-sm) overflow-y-auto [&_li]:px-2 [&_li]:py-1 [&_li]:text-sm [&_li]:cursor-pointer [&_li:hover]:bg-surface-inset"
      >
        <!-- `mousedown.prevent`, not `click`: a click fires after the input has
             already blurred, and the blur closes this list. Same as GraphSearch. -->
        <li
          v-for="e in fromResults"
          :key="e.id"
          role="option"
          :aria-selected="false"
          tabindex="-1"
          @mousedown.prevent="pick(e, 'from')"
        >
          {{ e.name }}
        </li>
      </ul>
    </FilterField>

    <FilterField
      v-model="to"
      width="full"
      label="Trace to entity"
      placeholder="to…"
      @update:model-value="search(to, 'to')"
    >
      <ul
        v-if="toResults.length"
        role="listbox"
        :aria-label="'Matches for the to field'"
        class="results absolute z-overlay left-0 right-0 bg-surface-overlay border border-border-default rounded-sm list-none max-h-(--scroll-sm) overflow-y-auto [&_li]:px-2 [&_li]:py-1 [&_li]:text-sm [&_li]:cursor-pointer [&_li:hover]:bg-surface-inset"
      >
        <!-- `mousedown.prevent`, not `click`: a click fires after the input has
             already blurred, and the blur closes this list. Same as GraphSearch. -->
        <li
          v-for="e in toResults"
          :key="e.id"
          role="option"
          :aria-selected="false"
          tabindex="-1"
          @mousedown.prevent="pick(e, 'to')"
        >
          {{ e.name }}
        </li>
      </ul>
    </FilterField>

    <div class="flex items-center gap-2">
      <label for="pathfinder-depth" class="text-xs text-text-tertiary">depth</label>
      <Input
        id="pathfinder-depth"
        v-model="maxDepth"
        type="number"
        min="1"
        max="6"
        class="depth w-(--field-xs)"
      />
      <Button icon="search" label="Find" :disabled="!fromId || !toId || graph.loading" @click="find"> Find </Button>
      <Spinner v-if="searching || graph.loading" :size="11" />
    </div>

    <div v-if="graph.pathResult" class="results-box mt-2">
      <template v-if="graph.pathResult.found">
        <div class="flex items-center justify-between">
          <span class="text-xs text-text-secondary">
            {{ graph.pathResult.paths.length }} shortest
            {{ graph.pathResult.paths.length === 1 ? 'path' : 'paths' }}
          </span>
          <Button variant="text" icon="close" label="Clear" @click="graph.clearPath()">clear</Button>
        </div>
        <p v-for="(chain, i) in graph.pathResult.chains" :key="i" class="chain mt-2 p-2 bg-surface-inset border-l-2 border-l-accent-primary rounded-r-sm text-sm leading-normal">
          {{ chain }}
        </p>
      </template>
      <p v-else class="text-xs text-text-secondary">
        No path within {{ graph.pathResult.max_depth }} hops.
        <Chip tone="neutral">try a greater depth</Chip>
      </p>
    </div>
  </div>
</template>
