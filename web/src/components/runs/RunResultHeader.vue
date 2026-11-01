<script setup lang="ts">
/**
 * What this run did, at the top of its own page, before anything else.
 *
 * The page led with identity and timing and put the answer four panels down,
 * behind a warnings block that fires on runs where nothing went wrong. For the
 * most common non-delivering outcome in the system -- a lookup that found no
 * definition -- a reader had to scroll past three panels to learn it.
 *
 * One chip and one sentence. The chip is the verdict (`lib/runOutcome.ts`); the
 * sentence is `run.result_line`, resolved server-side from the sentences its
 * kind declares (`run_types.RunType.lines`, contract C11).
 *
 * BOTH HALVES COME FROM THE SERVER, and this component interpolates nothing.
 * CLAUDE.md's "never invent words for a run outcome at the render site" covers
 * the sentence as well as the chip: a sentence built here can only read keys
 * off `stages[0].counts`, and nothing makes a kind emit the keys another kind's
 * sentence happens to need. `tests/test_frontend_drift.py` holds the line.
 */
import { computed } from 'vue'

import RunOutcomeChip from '@/components/runs/RunOutcomeChip.vue'
import { verdictFor } from '@/lib/runOutcome'
import type { Run } from '@/types/run'

const props = defineProps<{ run: Run }>()

/**
 * What the chip could not say.
 *
 * The chip shows one thing, and when the machinery had trouble it shows THAT
 * -- which is right, and which hides the fact that a degraded run may still
 * have delivered a brief. So the other half goes beside it, quietly, and only
 * when there is another half to state.
 */
const alsoFound = computed(() =>
  // Gated on the chip actually SHOWING a problem, not on the status: a run
  // whose chip already reads "not found" would otherwise be told it was not
  // found, twice, on one line.
  verdictFor(props.run).isProblem && props.run.outcome_text ? props.run.outcome_text : '',
)
</script>

<template>
  <div class="result flex flex-col gap-2">
    <div class="flex items-center gap-3">
      <RunOutcomeChip :run="run" />
      <span v-if="alsoFound" class="text-xs text-text-tertiary">
        {{ alsoFound }}, but not everything this run set out to do worked
      </span>
    </div>
    <p v-if="run.result_line" class="text-md text-text-primary">{{ run.result_line }}</p>
  </div>
</template>
