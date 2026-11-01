<script setup lang="ts">
/**
 * What a run did, in one chip.
 *
 * Composes `lib/runOutcome.ts` over the `Chip` primitive rather than adding a
 * second status chip: `StatusChip` is still the generic one, worn by the
 * curation and glossary queues for states that have nothing to do with runs.
 *
 * The rule it renders is stated once, in `verdictFor`: an execution problem
 * wins, otherwise show the answer. So a row reads `defined`, `not found`,
 * `brief written`, `already had it` when the machinery was fine, and
 * `degraded` or `failed` when it was not.
 */
import { computed } from 'vue'

import Chip from '@/components/ui/Chip.vue'
import { verdictWithCount, type VerdictInput } from '@/lib/runOutcome'

const props = defineProps<{
  run: VerdictInput
  /** For a batch run: how many of how many landed. Rendered as `40 of 182` in
   *  place of "some defined", which is the number a reader is after. */
  done?: number
  total?: number
}>()

const verdict = computed(() =>
  verdictWithCount(props.run, { done: props.done, total: props.total }),
)
</script>

<template>
  <!-- Bare: a verdict is not pressable, so it does not get a border. -->
  <Chip variant="bare" :tone="verdict.tone" dot>{{ verdict.text }}</Chip>
</template>
