<script setup lang="ts">
/**
 * One figure in a row of them. `.mono` carries `font-variant-numeric:
 * tabular-nums`, which is what stops a row of these shifting as the digits
 * change.
 */
withDefaults(defineProps<{ value: number | string; label: string; tone?: 'neutral' | 'high' | 'critical' }>(), {
  tone: 'neutral',
})

const TONE = {
  neutral: 'text-text-primary',
  high: 'text-signal-high',
  critical: 'text-signal-critical',
} as const
</script>

<template>
  <div class="stat flex flex-col gap-(--space-half) min-w-0">
    <span class="value mono leading-tight text-xl" :class="TONE[tone]">{{ value }}</span>
    <span class="label text-2xs tracking-label uppercase text-text-tertiary whitespace-nowrap">{{
      label
    }}</span>
  </div>
</template>
