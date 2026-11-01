<script setup lang="ts">
import { computed, ref } from 'vue'

import { humanMs as human } from '@/lib/duration'

/**
 * Model-call latency per task: p50 as a bar, p95 and max as ticks on it.
 *
 * The three numbers belong on ONE row because the question is about their
 * spread, not their values -- a task whose p50 is 45s and whose max is 98s is
 * a very different optimisation problem from one where they are both 45s, and
 * three separate bars make you do that subtraction yourself.
 *
 * Inline SVG on `EntityMiniGraph`'s conventions: deterministic layout, colours
 * as `var(--…)` and never a JS colour table, no native `<title>` (the browser's
 * own tooltip waits a second, cannot be styled, and vanishes when the pointer
 * drifts) -- the readout below is set from `pointerenter` instead.
 *
 * Scaled against the slowest MAX across all tasks, so rows are comparable to
 * each other. Scaling each row to itself would make every task look identical.
 */
export interface Series {
  label: string
  p50_ms: number
  p95_ms: number
  max_ms: number
  calls: number
}

const props = defineProps<{ series: Series[]; width?: number }>()

const W = computed(() => props.width ?? 320)
const ROW_H = 22
const BAR_H = 10

const hovered = ref<Series | null>(null)

const scale = computed(() => Math.max(1, ...props.series.map((s) => s.max_ms)))
const H = computed(() => Math.max(ROW_H, props.series.length * ROW_H))

function x(ms: number): number {
  return (ms / scale.value) * W.value
}

</script>

<template>
  <div class="latency">
    <svg
      :viewBox="`0 0 ${W} ${H}`"
      :width="W"
      :height="H"
      class="max-w-full"
      role="group"
      :aria-label="`call latency per task: ${series.map((s) => `${s.label} median ${human(s.p50_ms)}, 95th percentile ${human(s.p95_ms)}, slowest ${human(s.max_ms)}`).join('; ')}`"
    >
      <g
        v-for="(item, i) in series"
        :key="item.label"
        :transform="`translate(0 ${i * ROW_H})`"
        class="cursor-default"
        @pointerenter="hovered = item"
        @pointerleave="hovered = null"
      >
        <!-- Full-row hit area. Without it the pointer only registers on the bar
             itself, so hovering a fast task means aiming at a few pixels. -->
        <rect :width="W" :height="ROW_H" fill="transparent" />
        <rect
          :y="(ROW_H - BAR_H) / 2"
          :width="W"
          :height="BAR_H"
          fill="var(--surface-inset)"
        />
        <rect
          :y="(ROW_H - BAR_H) / 2"
          :width="Math.max(1, x(item.p50_ms))"
          :height="BAR_H"
          fill="var(--accent-primary)"
          :opacity="hovered && hovered !== item ? 0.4 : 1"
        />
        <!-- p95 and max as ticks. A second and third bar would read as three
             separate measurements rather than as the spread of one. -->
        <rect
          :x="Math.max(0, x(item.p95_ms) - 1)"
          :y="(ROW_H - BAR_H) / 2 - 2"
          width="2"
          :height="BAR_H + 4"
          fill="var(--signal-high)"
        />
        <rect
          :x="Math.max(0, x(item.max_ms) - 1)"
          :y="(ROW_H - BAR_H) / 2 - 2"
          width="2"
          :height="BAR_H + 4"
          fill="var(--signal-critical)"
        />
      </g>
    </svg>

    <!-- The readout, one task at a time, in full. Defaults to the slowest,
         which is the row someone opened this to find. -->
    <p class="mono mt-2 text-xs text-text-secondary">
      <template v-if="hovered ?? series[0]">
        <span class="text-text-primary">{{ (hovered ?? series[0]).label }}</span>
        · {{ (hovered ?? series[0]).calls }} calls · median
        {{ human((hovered ?? series[0]).p50_ms) }} · p95
        {{ human((hovered ?? series[0]).p95_ms) }} · slowest
        {{ human((hovered ?? series[0]).max_ms) }}
      </template>
    </p>
    <p class="text-2xs text-text-tertiary">
      bar median · amber tick 95th percentile · red tick slowest, all to the same scale
    </p>
  </div>
</template>
