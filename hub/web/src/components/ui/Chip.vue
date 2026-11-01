<script setup lang="ts">
import { computed } from 'vue'

/**
 * A small labelled state. The static half of the app's `Chip`: this surface has
 * no filter chips, so the interactive variant and its selected border are not
 * carried over.
 *
 * `--chip-dot` is declared on the ROOT, unconditionally, and `.chip__dot`
 * consumes it. Both halves stay unconditional: a default declared on the DOT
 * instead shadows a caller's inline override, and every dot silently falls back
 * to grey while still looking plausible. That is a bug the app has already
 * had -- an arbitrary property utility rather than a conditional class is what
 * closes it.
 */
const props = withDefaults(
  defineProps<{
    tone?: 'neutral' | 'accent' | 'high' | 'critical' | 'ok' | 'info'
    size?: 'md' | 'sm'
    dot?: boolean
  }>(),
  { tone: 'neutral', size: 'md', dot: false },
)

const TONE = {
  neutral: 'text-text-tertiary',
  accent: 'text-accent-primary',
  high: 'text-signal-high',
  critical: 'text-signal-critical',
  ok: 'text-signal-ok',
  info: 'text-signal-info',
} as const

const classes = computed(() => [
  'chip',
  `chip--${props.tone}`,
  'inline-flex items-center gap-1 font-ui border rounded-sm leading-tight whitespace-nowrap',
  'px-2 py-(--space-half) border-current font-medium tracking-label',
  '[--chip-dot:currentColor]',
  props.size === 'sm' ? 'chip--sm text-2xs' : 'text-xs',
  TONE[props.tone],
])
</script>

<template>
  <span :class="classes">
    <span
      v-if="dot"
      class="chip__dot w-(--dot-sm) h-(--dot-sm) rounded-[50%] bg-(--chip-dot)"
      aria-hidden="true"
    />
    <slot />
  </span>
</template>
