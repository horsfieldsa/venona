<script setup lang="ts">
/**
 * What a run produced, in the words its kind declared.
 *
 * Every field here is resolved server-side from `run_types.RunType.facts`
 * (contract C11), so this component never asks what kind of run it is looking
 * at. That is the point. The two panels it replaces were hardcoded: one for
 * `ingest` and one shaped for a lookup, and the four other single-stage kinds
 * fell into the lookup one -- so a reader's brief rendered a `subject` with
 * nothing in it and, on a run that had just written a document, `sourced: no`.
 *
 * A rejection trace is the one shape that is not a value. It is handed to
 * `RunCandidates`, which already knows how to say why each candidate was
 * refused in sentences rather than as serialised JSON.
 */
import { computed } from 'vue'

import RunCandidates from '@/components/runs/RunCandidates.vue'
import FactList from '@/components/ui/FactList.vue'
import type { ResolvedFact } from '@/types/run'

const props = defineProps<{ facts: ResolvedFact[] }>()

const plain = computed(() => props.facts.filter((f) => f.shape !== 'candidates'))

/** Only ever one, but read as a list so a kind declaring two is rendered
 *  rather than silently losing the second. */
const traces = computed(() =>
  props.facts.filter((f) => f.shape === 'candidates' && Array.isArray(f.value)),
)
</script>

<template>
  <div class="run-facts flex flex-col gap-4">
    <FactList v-if="plain.length" :items="plain" />
    <div v-for="trace in traces" :key="trace.key" class="pt-3 border-t border-t-border-subtle">
      <p class="text-2xs tracking-label uppercase text-text-tertiary mb-2">{{ trace.label }}</p>
      <RunCandidates :rejected="(trace.value as unknown[])" />
    </div>
  </div>
</template>
