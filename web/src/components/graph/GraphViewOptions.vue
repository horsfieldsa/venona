<script setup lang="ts">
import SegmentedControl from '@/components/ui/SegmentedControl.vue'
import Toggle from '@/components/ui/Toggle.vue'
import type { GraphSpacing } from '@/composables/useCytoscape'

/**
 * How the graph is DRAWN, as opposed to what it shows.
 *
 * "hide unconnected" is the persistent control; the readout over the canvas
 * carries the in-context undo. Both exist because a third of the corpus has no
 * surviving relationship and hiding it by default is only defensible while the
 * count and the way back are permanently on screen.
 */
defineProps<{
  spacing: GraphSpacing
  hideIsolated: boolean
  includeUnconfirmed: boolean
}>()

defineEmits<{
  (e: 'update:spacing', value: GraphSpacing): void
  (e: 'update:hideIsolated', value: boolean): void
  (e: 'update:includeUnconfirmed', value: boolean): void
}>()

const SPACINGS: { value: GraphSpacing; label: string }[] = [
  { value: 'compact', label: 'compact' },
  { value: 'normal', label: 'normal' },
  { value: 'airy', label: 'airy' },
]
</script>

<template>
  <div class="p-3 border-b border-border-subtle">
    <h4 class="mb-2 text-xs font-semibold uppercase tracking-label text-text-tertiary">view</h4>

    <!-- Default size, like every other Toggle in the app. `sm` made the one
         binary control in this rail read as a lesser thing than the full-screen
         toggle two rows up in the bar. -->
    <Toggle
      :model-value="hideIsolated"
      title="Names with no surviving relationship. A third of Venona's reading, and what stretches the layout until it is unreadable."
      @update:model-value="$emit('update:hideIsolated', $event)"
    >
      hide unconnected
    </Toggle>

    <!-- Beside `hide unconnected` because it is the same KIND of control: both
         widen or narrow what the SERVER sends, both refetch, and both are the
         reader deciding how much of Venona's reading to look at. The two source
         and predicate filters further down narrow a payload already in hand. -->
    <Toggle
      :model-value="includeUnconfirmed"
      title="Names only one source has given us, and the relationships resting on them. Drawn hollow."
      @update:model-value="$emit('update:includeUnconfirmed', $event)"
    >
      names seen once
    </Toggle>
    <p class="mt-1 text-2xs text-text-quiet">hollow nodes are names seen once</p>

    <div class="flex items-center flex-wrap gap-1 mt-2">
      <span class="text-2xs text-text-tertiary uppercase tracking-label">spacing</span>
      <SegmentedControl
        :model-value="spacing"
        ariaLabel="layout spacing"
        :options="SPACINGS"
        @update:model-value="(v) => $emit('update:spacing', v as GraphSpacing)"
      />
    </div>
    <!-- Said plainly because it is otherwise a control that looks broken: the
         layout is fitted to the viewport, so a bigger layout just gets a smaller
         zoom. It bites where the fit lands near 1:1, which is focus and brief
         mode. -->
    <p class="mt-1 text-2xs text-text-quiet">most visible when focused on one entity</p>
  </div>
</template>
