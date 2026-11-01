<script setup lang="ts">
/**
 * One entity type's progress as a ring gauge, in that type's own colour:
 * the dim ring is how much of the type you have studied, the full-strength
 * ring is how much is STRONG, and the centre carries the count. Inline SVG
 * on the house chart conventions — `viewBox` and attribute sizes (never a
 * CSS height), colours as `var(--…)` strings, deterministic, no animation.
 * The words are not optional decoration: the aria-label and the caption
 * carry the same facts as the ring, because a proportion encoded only in
 * arc-length and hue survives neither greyscale nor a screen reader.
 */
import { computed } from 'vue'

import { cardTypeLabel } from '@/lib/learnCopy'
import type { TypeCoverage } from '@/types/learning'

const props = defineProps<{
  row: TypeCoverage
  /** A resolved `var(--entity-…)` reference from the caller. */
  colour: string
}>()

const SIZE = 92
const RADIUS = 38
const STROKE = 6
const CIRCUMFERENCE = 2 * Math.PI * RADIUS

function arc(fraction: number): string {
  const drawn = Math.max(0, Math.min(1, fraction)) * CIRCUMFERENCE
  return `${drawn} ${CIRCUMFERENCE}`
}

const studiedArc = computed(() => arc(props.row.total ? props.row.started / props.row.total : 0))
const strongArc = computed(() => arc(props.row.total ? props.row.strong / props.row.total : 0))
const complete = computed(() => props.row.total > 0 && props.row.strong === props.row.total)

const label = computed(() => cardTypeLabel(props.row.type) ?? props.row.type)
const caption = computed(() =>
  complete.value
    ? 'all strong'
    : `${props.row.started} studied · ${props.row.strong} strong`,
)
</script>

<template>
  <figure
    class="flex flex-col items-center gap-1"
    :title="`${label}: ${row.started} of ${row.total} studied, ${row.strong} strong`"
  >
    <div class="relative">
      <!-- max-w-full: in a column narrower than the drawing the ring SCALES
           (the viewBox keeps it round) rather than overlapping its
           neighbour, which fixed sizes silently do. -->
      <svg
        :viewBox="`0 0 ${SIZE} ${SIZE}`"
        :width="SIZE"
        :height="SIZE"
        class="max-w-full h-auto"
        role="img"
        :aria-label="`${label}: ${row.started} of ${row.total} studied, ${row.strong} strong`"
      >
        <circle
          :cx="SIZE / 2"
          :cy="SIZE / 2"
          :r="RADIUS"
          fill="none"
          stroke="var(--surface-selected)"
          :stroke-width="STROKE"
        />
        <circle
          :cx="SIZE / 2"
          :cy="SIZE / 2"
          :r="RADIUS"
          fill="none"
          :stroke="colour"
          stroke-opacity="0.4"
          :stroke-width="STROKE"
          stroke-linecap="round"
          :stroke-dasharray="studiedArc"
          :transform="`rotate(-90 ${SIZE / 2} ${SIZE / 2})`"
        />
        <circle
          :cx="SIZE / 2"
          :cy="SIZE / 2"
          :r="RADIUS"
          fill="none"
          :stroke="colour"
          :stroke-width="STROKE"
          stroke-linecap="round"
          :stroke-dasharray="strongArc"
          :transform="`rotate(-90 ${SIZE / 2} ${SIZE / 2})`"
        />
      </svg>
      <div class="absolute inset-0 flex flex-col items-center justify-center">
        <span class="mono text-md text-text-primary">{{ row.started }}</span>
        <span class="text-2xs text-text-tertiary">of {{ row.total }}</span>
      </div>
    </div>
    <figcaption class="flex flex-col items-center gap-(--space-half)">
      <span class="text-2xs uppercase tracking-label" :style="{ color: colour }">
        {{ label }}
      </span>
      <span class="text-2xs" :class="complete ? 'text-signal-ok' : 'text-text-quiet'">
        {{ caption }}
      </span>
    </figcaption>
  </figure>
</template>
