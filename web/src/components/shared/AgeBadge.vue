<script setup lang="ts">
/**
 * How old an item is, in the one format the brief uses: `21H` under two days,
 * `5D` after.
 *
 * This arithmetic was written twice -- `BriefView`'s top-N line and
 * `BriefItem`'s per-item badge -- with the same `Math.max(hours, 1)` floor in
 * both, because "0H" reads as an error rather than as "very recent".
 *
 * `age_hours` is frozen at generation time on the server, deliberately: it is
 * stored, not recomputed, so reopening last Tuesday's brief shows the ages the
 * reader was originally told rather than relabelling everything "5D".
 */
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    hours: number | null | undefined
    /** `ok` for the reassuring green in the top-N list; `dim` inside an item. */
    tone?: 'ok' | 'dim'
  }>(),
  { tone: 'dim' },
)

const label = computed(() => {
  if (props.hours == null) return null
  return props.hours < 48
    ? `${Math.max(props.hours, 1)}H`
    : `${Math.round(props.hours / 24)}D`
})
</script>

<template>
  <span
    v-if="label"
    class="age font-ui text-xs font-semibold tabular-nums whitespace-nowrap"
    :class="tone === 'ok' ? 'ok text-signal-ok' : 'dim text-text-tertiary'"
    >{{ label }}</span
  >
</template>
