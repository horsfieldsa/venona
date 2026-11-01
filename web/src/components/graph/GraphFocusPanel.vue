<script setup lang="ts">
import Button from '@/components/ui/Button.vue'
import SegmentedControl from '@/components/ui/SegmentedControl.vue'
import { entityColourVar } from '@/lib/entityColour'
import { MAX_FOCUS_DEPTH } from '@/router/graphRoute'
import type { GraphNode } from '@/types/graph'

/**
 * The focus mode rail section: what is being focused, and how far out.
 *
 * The depth control is three chips, not a range input. A slider with three stops
 * is a worse affordance than three buttons, and the chip vocabulary is already
 * established one section up. The minimum-sources control stays a range because
 * it is a magnitude rather than a small set of named states.
 *
 * The node count sits beside the chips so the cost of the next depth is visible
 * before it is paid -- depth 3 from a hub reaches most of the corpus, at which
 * point focus mode is the whole graph with extra steps.
 */
defineProps<{
  subject: GraphNode | null
  /** The entities focused through to get here, oldest first. */
  trail: GraphNode[]
  depth: number
  visibleNodes: number
  visibleEdges: number
}>()

defineEmits<{
  (e: 'update:depth', value: number): void
  /** Jump back to `trail[index]`, dropping everything after it. */
  (e: 'up', index: number): void
  (e: 'exit'): void
}>()

const DEPTHS = Array.from({ length: MAX_FOCUS_DEPTH }, (_, i) => i + 1).map((d) => ({
  value: d,
  label: String(d),
  hint: `Everything within ${d} relationship${d > 1 ? 's' : ''} of this entity`,
}))

</script>

<template>
  <!-- Utilities, not GraphView's scoped `.section`/`.label`: a parent's scoped
       CSS reaches a child's ROOT element and nothing below it, so the heading
       would render unstyled while the wrapper looked fine. -->
  <div class="p-3 border-b border-border-subtle">
    <h4 class="mb-2 text-xs font-semibold uppercase tracking-label text-text-tertiary">
      focused on
    </h4>

    <!-- The walk, oldest first. Clicking a neighbour moves the focus, so this
         is the only record of how the reader got here -- and jumping back four
         steps beats clicking the background four times. The current subject is
         the last item and is not a link. -->
    <p
      v-if="trail.length"
      class="flex items-center flex-wrap gap-1 mb-2 text-2xs text-text-tertiary"
    >
      <template v-for="(n, i) in trail" :key="n.id">
        <!-- `literal`: this is the entity's own name, not a label.
             "LOCKHEED MARTIN" is not what the corpus calls it. -->
        <Button
          variant="text"
          case="literal"
          :title="`Back to ${n.name}`"
          @click="$emit('up', i)"
        >
          {{ n.name }}
        </Button>
        <span aria-hidden="true">›</span>
      </template>
    </p>

    <p v-if="subject" class="flex items-center gap-2 mb-2 text-xs text-text-primary">
      <span
        class="flex-none w-(--dot-md) h-(--dot-md) rounded-[50%]"
        :style="{ background: entityColourVar({ id: subject.id, kind: 'entity' }) }"
        aria-hidden="true"
      />
      <span class="truncate">{{ subject.name }}</span>
    </p>
    <p v-else class="mb-2 text-xs text-text-tertiary">nothing focused</p>

    <div class="flex items-center gap-2 mb-2">
      <span class="text-2xs text-text-tertiary uppercase tracking-label">hops</span>
      <SegmentedControl
        :model-value="depth"
        ariaLabel="focus depth"
        :options="DEPTHS"
        @update:model-value="(v) => $emit('update:depth', v as number)"
      />
    </div>

    <p class="mono text-xs text-text-tertiary">
      {{ visibleNodes }} nodes · {{ visibleEdges }} edges in focus
    </p>

    <Button variant="text" icon="waypoints" label="Show the whole graph" @click="$emit('exit')">show the whole graph</Button>
  </div>
</template>
