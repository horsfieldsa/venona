<script setup lang="ts">
import { computed } from 'vue'

import StatusBar from '@/components/marks/StatusBar.vue'
import { humanMs as human } from '@/lib/duration'

/**
 * Where a run's wall-clock actually went.
 *
 * This is the first thing to look at and the reason the feature exists: on the
 * run measured 2026-08-16 while building it, analyze was 85% of 98 minutes and fetch was
 * 15%, which is not what the stage table's six equal-height rows suggest.
 *
 * Built on `StatusBar`, whose own docstring names "a stage spine" as an
 * intended use, rather than a second bar implementation -- CLAUDE.md forbids a
 * second implementation of a `ui/` primitive and a proportion is exactly what
 * it already is.
 */
const props = defineProps<{
  /** Matches the generated `StageMetrics` loosely on purpose: `status` is
   *  optional on the wire and a stage still on its way has none. */
  stages: { stage: string; status?: string | null; duration_ms: number }[]
  /** Time between the run being asked for and its first stage starting. */
  queueMs?: number
}>()

const TONE: Record<string, string> = {
  ok: 'var(--accent-primary)',
  running: 'var(--accent-muted)',
  error: 'var(--signal-critical)',
  // Stopped, not failed. Grey read as a stage that never ran, which is the
  // opposite of what this records: it ran, and something ended it.
  interrupted: 'var(--signal-high)',
}

const total = computed(() => props.stages.reduce((n, s) => n + s.duration_ms, 0))

const rows = computed(() =>
  props.stages
    .filter((s) => s.duration_ms > 0)
    .map((s) => ({
      ...s,
      share: total.value ? s.duration_ms / total.value : 0,
    }))
    .sort((a, b) => b.duration_ms - a.duration_ms),
)

const segments = computed(() =>
  props.stages.map((s) => ({
    value: s.duration_ms,
    color: TONE[s.status ?? ''] ?? 'var(--border-strong)',
    title: `${s.stage.replace(/_/g, ' ')} ${human(s.duration_ms)}`,
    active: s.status === 'running',
  })),
)

/**
 * Offline every duration is exactly 0 -- `FrozenClock` never advances, by
 * design -- so a run replayed from fixtures shows a timeline of nothing. That
 * is correct rather than a bug to paper over, and it is said out loud here so
 * the next person does not go looking for the missing numbers.
 */
const measurable = computed(() => total.value > 0)

const label = computed(
  () =>
    `step durations: ${props.stages.map((s) => `${s.stage.replace(/_/g, ' ')} ${human(s.duration_ms)}`).join(', ')}`,
)
</script>

<template>
  <div class="timeline">
    <div v-if="measurable">
      <div class="flex items-baseline justify-between gap-2">
        <p class="text-2xs tracking-label uppercase text-text-tertiary">wall clock</p>
        <p class="mono text-xs text-text-secondary">
          {{ human(total) }}
          <span v-if="queueMs" class="text-text-tertiary"> · {{ human(queueMs) }} queued</span>
        </p>
      </div>

      <StatusBar class="mt-2" :segments="segments" :label="label" />

      <ul class="mt-3 flex flex-col gap-1">
        <li
          v-for="row in rows"
          :key="row.stage"
          class="grid grid-cols-[var(--field-sm)_1fr_auto] items-baseline gap-3 text-xs"
        >
          <span class="text-text-secondary">{{ row.stage.replace(/_/g, ' ') }}</span>
          <!-- A per-stage bar as well as the spine, because the spine answers
               "what dominated" and this answers "by how much" -- the second
               question is the one you ask when deciding what to optimise. -->
          <span class="h-(--spine-h) bg-surface-inset">
            <span
              class="block h-full bg-accent-muted"
              :style="{ width: `${Math.max(2, row.share * 100)}%` }"
            />
          </span>
          <span class="mono text-text-tertiary">
            {{ human(row.duration_ms) }} · {{ Math.round(row.share * 100) }}%
          </span>
        </li>
      </ul>
    </div>

    <p v-else class="text-xs text-text-tertiary">
      No measurable durations. Replayed runs use a frozen clock, so every step
      reads exactly zero.
    </p>
  </div>
</template>
