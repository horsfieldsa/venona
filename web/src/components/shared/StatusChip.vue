<script setup lang="ts">
/**
 * A run's status, with its tone decided in one place.
 *
 * The mapping was open-coded as `toneFor()` in `RunsView` and again, slightly
 * differently, in the curation and glossary queues. The distinction that has to
 * survive is `succeeded_partial`: it is NOT a failure -- the orchestrator uses
 * it for a run that persisted something and then hit trouble, and CLAUDE.md is
 * explicit that letting a partially useful run report as `failed` is a bug. So
 * it takes the attention tone, not the critical one.
 *
 * NOT the run history's chip any more. A finished run is rendered by
 * `components/runs/RunOutcomeChip.vue`, which shows what the run FOUND when
 * the machinery was fine and falls back to this vocabulary when it was not.
 * This stays the generic chip: stage rows here, and the curation and glossary
 * queues, whose states have nothing to do with runs.
 */
import { computed } from 'vue'

import Chip from '@/components/ui/Chip.vue'

const props = defineProps<{ status: string }>()

const TONES: Record<string, 'ok' | 'high' | 'critical' | 'accent' | 'neutral'> = {
  succeeded: 'ok',
  // Amber, and it can afford to be: `succeeded_partial` means a real
  // degradation and nothing else, so it is rare. A run that found nothing
  // reports that through `runs.outcome` and stays `succeeded`. A pill firing
  // on most rows is a signal turned into a background colour, so if this one
  // becomes common, something is overloading the status field. ADR 0183.
  succeeded_partial: 'high',
  failed: 'critical',
  cancelled: 'neutral',
  running: 'accent',
  queued: 'neutral',
  ok: 'ok',
  error: 'critical',
  // STOPPED, not failed: the stage a cancelled or reaped run was inside.
  // Neutral said nothing on the one row explaining where a run got to.
  interrupted: 'high',
  active: 'ok',
  provisional: 'high',
  merged: 'neutral',
  dormant: 'neutral',
  rejected: 'critical',
}

const tone = computed(() => TONES[props.status] ?? 'neutral')

/** `degraded`, not `partial`. The status means exactly one thing, so the label
 *  says it plainly rather than hedging. Kept in step with `lib/runOutcome.ts`,
 *  which is what the run history renders through. */
const LABELS: Record<string, string> = { succeeded_partial: 'degraded' }
const label = computed(() => LABELS[props.status] ?? props.status.replace(/_/g, ' '))
</script>

<template>
  <!-- Bare: a status is not pressable, so it does not get a border. -->
  <Chip variant="bare" :tone="tone" dot>{{ label }}</Chip>
</template>
