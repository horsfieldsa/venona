<script setup lang="ts">
/**
 * A run WHILE it runs: the stage spine, the counters as they tick, the model
 * session and the operator log.
 *
 * NOT inline in `RunsView`: that makes the runs list two pages at once — a history
 * list AND a full readout of whichever run happens to be attached. With more
 * than one run in flight (the light lane queues independently of the research
 * lane, contract C11) it is one panel and several runs. The readout belongs to
 * a RUN, at `/admin/runs/:id`, and the list stays a list.
 *
 * It renders the STORE's live state, which is global and attached to exactly
 * one run at a time. The caller is responsible for checking that the store is
 * attached to the run being displayed; this component does not attach, because
 * a component that attached on mount would fight `useRunStream` for the stream.
 */
import { computed } from 'vue'

import ModelTranscript from '@/components/runs/ModelTranscript.vue'
import RunLog from '@/components/runs/RunLog.vue'
import StatBlock from '@/components/shared/StatBlock.vue'
import StatusChip from '@/components/shared/StatusChip.vue'
import Button from '@/components/ui/Button.vue'
import Panel from '@/components/ui/Panel.vue'
import { counterKeys, counterLabel } from '@/lib/runCopy'
import { useRunsStore, type StageState } from '@/stores/runs'

const props = defineProps<{
  /** Shown in the panel header; the run type's label. */
  title: string
  /** `pipeline` gets a spine; `single` gets a sentence. Contract C11. */
  detail: 'pipeline' | 'single'
  /** Counter keys worth showing for this run type, in its declared order. */
  summary: string[]
}>()

defineEmits<{ (e: 'brief'): void }>()

const runs = useRunsStore()

const SEG_BG = {
  ok: 'bg-border-strong',
  running: 'bg-accent-primary',
  error: 'bg-signal-critical',
  // Stopped, not failed. Amber, matching the chip and the timeline.
  interrupted: 'bg-signal-high',
} as const

/**
 * A stage that is not working but is not stopped either.
 *
 * Venona asked, the provider said not now, and Venona is waiting before asking
 * again. It comes from two places -- between a call's attempts, and from the
 * circuit's outage wait, which runs to half an hour -- and to a reader they are
 * one thing, so they arrive on one field.
 *
 * NOT an error colour. Nothing has been lost yet and most of these recover; a
 * strip that goes red for a wait it then comes back from teaches a reader to
 * ignore it. `signal-high` is the register for "worth looking at", which this
 * is, because the alternative reading of a silent stage is that it is wedged.
 */
function waiting(stage: StageState): boolean {
  return stage.status === 'running' && stage.progress?.waiting === true
}

function retryIn(stage: StageState): number | null {
  return stage.progress?.retry_in ?? null
}



/**
 * Counters worth showing for THIS run type, in the type's declared order.
 *
 * A lookup makes one model call and writes one row; rendering it against the
 * full thirteen-name counter list gave eleven zeroes, which reads as eleven
 * things that did not happen rather than eleven that were never asked for.
 */
const shownCounters = computed(() => {
  const keys = props.summary.length ? props.summary : counterKeys()
  return keys
    .map((key) => ({
      key,
      label: counterLabel(key),
      value: (runs.counters as Record<string, number | undefined>)[key],
    }))
    .filter((c) => c.value !== undefined && c.value !== 0)
})

/** A one-stage run gets a status line, not a spine. Five segments that never
 *  light read as five stages that failed. */
const showSpine = computed(() => props.detail === 'pipeline')

/** Never colour and motion alone: the spine spells its state out in words. */
const spineLabel = computed(() => {
  const running = runs.stages.find((s) => s.status === 'running')
  const done = runs.stages.filter((s) => s.status === 'ok').length
  return running
    ? `run: ${running.name.replace(/_/g, ' ')} running, ${done} of ${runs.stages.length} steps done`
    : `run steps: ${runs.stages.map((s) => s.name.replace(/_/g, ' ')).join(', ')}`
})

function fmt(value: number | undefined, key: string) {
  if (value === undefined) return ''
  return key === 'cost_usd' ? `$${value.toFixed(3)}` : String(value)
}

</script>

<template>
  <Panel variant="bare" :title="title">
    <!-- Segments and labels share ONE grid, so each name sits under its own
         bar. As two independent flex rows the labels packed left and the reader
         had to count across to work out which stage was running.

         The column count follows the STAGE LIST, not a literal: it was
         `grid-cols-5`, so a sixth stage packed six bars into five columns and
         put every label under the wrong one. -->
    <div
      v-if="showSpine"
      class="spine grid gap-(--space-half)"
      role="img"
      :aria-label="spineLabel"
      :style="{ gridTemplateColumns: `repeat(${runs.stages.length}, minmax(0, 1fr))` }"
    >
      <span
        v-for="stage in runs.stages"
        :key="stage.name"
        class="seg h-(--spine-h) rounded-hair transition-colors duration-base ease-standard"
        :class="[
          `seg--${stage.status}`,
          waiting(stage)
            ? 'bg-signal-high'
            : (SEG_BG[stage.status as keyof typeof SEG_BG] ?? 'bg-surface-inset'),
          stage.status === 'running' ? 'animate-pulse-stage' : '',
        ]"
        :title="stage.name.replace(/_/g, ' ')"
      />
      <span
        v-for="stage in runs.stages"
        :key="`${stage.name}-label`"
        class="spine__name mt-2 text-2xs tracking-label uppercase text-text-tertiary"
        :class="{ 'on text-accent-primary': stage.status === 'running' }"
      >
        {{ stage.name.replace(/_/g, ' ') }}
        <!-- The live count, under the stage it belongs to. Analyze runs for well
             over an hour and the pulsing segment was its only signal, which
             cannot tell "working" from "wedged". -->
        <span
          v-if="stage.status === 'running' && stage.progress?.total"
          class="mono block text-text-secondary normal-case tracking-normal"
        >
          {{ stage.progress.current ?? 0 }}/{{ stage.progress.total }}
          <template v-if="stage.progress.backlog && stage.progress.backlog > stage.progress.total">
            of {{ stage.progress.backlog }} queued
          </template>
        </span>
        <!-- UNDER the count rather than instead of it: how far through the
             stage is stays true while a call waits, and losing it would make a
             wait look like a reset. -->
        <span
          v-if="waiting(stage)"
          class="block normal-case tracking-normal text-signal-high"
        >
          waiting for the service<template v-if="retryIn(stage)">, retrying in
            {{ retryIn(stage) }}s</template>
        </span>
      </span>
    </div>

    <p v-else class="text-xs text-text-tertiary">
      {{ (runs.stages.find((s) => s.status === 'running')?.name ?? '').replace(/_/g, ' ') }}
    </p>

    <div v-if="shownCounters.length" class="counters pt-4 flex items-center gap-5 flex-wrap">
      <StatBlock v-for="c in shownCounters" :key="c.key" :value="fmt(c.value, c.key)" :label="c.label" />
    </div>

    <div v-if="runs.finished" class="done py-3 flex items-center gap-2 items-baseline">
      <StatusChip :status="runs.finished.status" />
      <!-- Never hot-swap what the reader is looking at: offer the link. -->
      <Button v-if="runs.finished.briefId" variant="text" icon="external" @click="$emit('brief')">
        New brief ready
      </Button>
    </div>

    <ModelTranscript v-if="runs.transcript.length || runs.researchRunning" />

    <div v-if="runs.log.length" class="mt-4 pt-3 border-t border-t-border-subtle">
      <RunLog :events="runs.log" />
    </div>
  </Panel>
</template>
