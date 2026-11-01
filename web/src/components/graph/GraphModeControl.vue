<script setup lang="ts">
import { computed } from 'vue'

import SegmentedControl from '@/components/ui/SegmentedControl.vue'
import { GRAPH_MODES, type GraphMode } from '@/router/graphRoute'

/**
 * What the canvas is scoped to: everything, one entity's neighbourhood, or one
 * brief.
 *
 * ONE OF N, so a `SegmentedControl` -- one pill, segments inside it. This file
 * said "a segmented control" for a long time while drawing three separate
 * chips, which is a row of buttons: nothing said the options belonged to each
 * other. `full screen` next to it is genuinely binary and stays a Toggle;
 * `reset` does a thing and returns to rest, so it is the only Button. Shape
 * follows what the control IS.
 *
 * A scope that cannot be reached is NOT RENDERED, rather than rendered dead.
 * `this brief` is meaningless unless a brief was the way in, and `focus` is
 * meaningless until something is selected -- and a permanently greyed control
 * is a question the reader has to answer ("what would make that work?") every
 * time they look at the bar. The whole group disappears when only one scope is
 * available, because a radio group of one is not a choice.
 */
const props = defineProps<{
  modelValue: GraphMode
  /** Focus needs something to focus on. */
  canFocus: boolean
  /** Brief mode needs to know which brief the reader came from. */
  canBrief: boolean
}>()

defineEmits<{ (e: 'update:modelValue', value: GraphMode): void }>()

const available = computed(() =>
  GRAPH_MODES.filter((m) => {
    if (m.value === 'focus') return props.canFocus || props.modelValue === 'focus'
    if (m.value === 'brief') return props.canBrief || props.modelValue === 'brief'
    return true
  }),
)
</script>

<template>
  <SegmentedControl
    v-if="available.length > 1"
    :model-value="modelValue"
    ariaLabel="graph scope"
    :options="available"
    @update:model-value="(v) => $emit('update:modelValue', v as GraphMode)"
  />
</template>
