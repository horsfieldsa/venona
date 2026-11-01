<script setup lang="ts">
import { computed, ref } from 'vue'

import type { SeriesDay } from '@/types/learning'

/**
 * Reviews per day, on `WorkBars`' conventions: a `viewBox` rather than a CSS
 * height, colours as `var(--…)` strings, a full-height hit rect per slot, a
 * readout line below instead of a native tooltip, and a window that ends
 * TODAY with explicit zero days — a quiet week must read as quiet.
 *
 * One encoding. Retention lives in the readout, not as a second overlay: in
 * the early weeks the series is three populated days, and a retention line
 * over that is noise wearing an axis.
 */
const props = defineProps<{ days: SeriesDay[]; span: number }>()

const W = 640
const H = 96
const GAP = 1

const hovered = ref<SeriesDay | null>(null)

const series = computed<SeriesDay[]>(() => {
  const byDay = new Map(props.days.map((d) => [d.day.slice(0, 10), d]))
  const today = Date.parse(`${new Date().toISOString().slice(0, 10)}T00:00:00Z`)
  const out: SeriesDay[] = []
  for (let i = props.span - 1; i >= 0; i--) {
    const day = new Date(today - i * 86_400_000).toISOString().slice(0, 10)
    out.push(byDay.get(day) ?? { day, reviews: 0, graded: 0, lapsed: 0, retention: null })
  }
  return out
})

const total = computed(() => series.value.reduce((sum, d) => sum + d.reviews, 0))
const peak = computed(() => Math.max(1, ...series.value.map((d) => d.reviews)))
const slot = computed(() => W / Math.max(1, series.value.length))

function percent(value: number | null | undefined): string {
  return value == null ? '—' : `${Math.round(value * 100)}% retained`
}

const overallRetention = computed(() => {
  const graded = series.value.reduce((sum, d) => sum + d.graded, 0)
  const lapsed = series.value.reduce((sum, d) => sum + d.lapsed, 0)
  return graded ? 1 - lapsed / graded : null
})

const readout = computed(() => {
  const day = hovered.value
  if (!day) return `${total.value} reviews over ${props.span} days · ${percent(overallRetention.value)}`
  return `${day.day.slice(0, 10)} · ${day.reviews} reviews · ${percent(day.retention)}`
})
</script>

<template>
  <div>
    <svg
      :viewBox="`0 0 ${W} ${H}`"
      :width="W"
      :height="H"
      class="max-w-full"
      role="img"
      :aria-label="`${total} reviews over ${span} days, ${percent(overallRetention)}`"
    >
      <g
        v-for="(day, i) in series"
        :key="day.day"
        @pointerenter="hovered = day"
        @pointerleave="hovered = null"
      >
        <rect :x="i * slot" :width="slot" :height="H" fill="transparent" />
        <rect
          :x="i * slot"
          :y="H - Math.max(day.reviews > 0 ? 1 : 0, (day.reviews / peak) * H)"
          :width="Math.max(1, slot - GAP)"
          :height="Math.max(day.reviews > 0 ? 1 : 0, (day.reviews / peak) * H)"
          :fill="hovered === day ? 'var(--accent-primary)' : 'var(--accent-muted)'"
        />
      </g>
      <rect :y="H - 1" :width="W" height="1" fill="var(--border-subtle)" />
    </svg>

    <p class="mono mt-2 text-xs text-text-secondary">{{ readout }}</p>
  </div>
</template>
