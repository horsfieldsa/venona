<script setup lang="ts">
import { computed } from 'vue'

/**
 * A proportion, `--spine-h` tall: the freshness meter, a corroboration split, a
 * stage spine.
 *
 * ALWAYS give it a `label` spelling the split out in words. The bar is the fast
 * read, not the only one -- a proportion carried by width and colour alone
 * survives neither greyscale nor a screen reader, and docs/DESIGN.md's rule
 * that meaning is never encoded by appearance alone applies to a bar exactly as
 * it does to a chip.
 */
export interface Segment {
  value: number
  /** A tier-2 token reference, resolved by the caller: `var(--signal-ok)`. */
  color: string
  title?: string
  /** The one segment that is still happening pulses. */
  active?: boolean
}

const props = withDefaults(
  defineProps<{
    segments: Segment[]
    /** Spelled out in words. Required in practice; see the note above. */
    label: string
  }>(),
  { segments: () => [] },
)

const total = computed(() => props.segments.reduce((n, s) => n + s.value, 0) || 1)

function width(value: number): string {
  return `${(value / total.value) * 100}%`
}
</script>

<template>
  <div
    class="statusbar flex h-(--spine-h) gap-(--space-half) bg-surface-inset"
    role="img"
    :aria-label="label"
  >
    <span
      v-for="(seg, i) in segments"
      :key="i"
      class="statusbar__seg"
      :class="seg.active ? 'animate-pulse-stage' : ''"
      :style="{ width: width(seg.value), background: seg.color }"
      :title="seg.title"
    />
  </div>
</template>
