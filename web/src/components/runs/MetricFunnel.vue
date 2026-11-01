<script setup lang="ts">
import { computed } from 'vue'

import StatusBar from '@/components/marks/StatusBar.vue'

/**
 * One stage's population, and every reason a member of it did not survive.
 *
 * The backend's `Funnel` flattens to `offered`, `kept` and any number of
 * `drop_*` keys, which is deliberately schemaless -- a new stage names its own
 * drops and this renders them without being told about them. So the ONLY thing
 * this component may assume is that prefix.
 *
 * `reconciled: false` is rendered loudly. It means `offered !== kept + drops`,
 * which is a finding about the pipeline -- a stage losing items it does not
 * account for -- and not a rendering problem. Hiding it would defeat the reason
 * the invariant is recorded as data rather than asserted.
 */
const props = defineProps<{
  stage: string
  metrics: Record<string, number>
  reconciled: boolean
}>()

const offered = computed(() => props.metrics.offered ?? 0)
const kept = computed(() => props.metrics.kept ?? 0)

/** Largest drop first: the reason worth acting on is the one at the top. */
const drops = computed(() =>
  Object.entries(props.metrics)
    .filter(([key]) => key.startsWith('drop_'))
    .map(([key, value]) => ({ label: key.slice(5).replace(/_/g, ' '), value }))
    .sort((a, b) => b.value - a.value),
)

/** Everything that is neither the chain nor a drop: caps, thresholds, ratios.
 *  Shown because a funnel without the cap that shaped it invites the wrong
 *  conclusion -- "analyze only did 120" reads as a failure until you can see
 *  `over_limit` next to the limit itself. */
const CHAIN = new Set(['offered', 'kept', 'reconciled', 'duration_ms'])
const context = computed(() =>
  Object.entries(props.metrics)
    .filter(([key]) => !key.startsWith('drop_') && !CHAIN.has(key))
    .map(([key, value]) => ({
      label: key.replace(/_/g, ' '),
      // Ratios carry meaning in their decimals; counts never do.
      value: Number.isInteger(value) ? String(value) : value.toFixed(2),
    })),
)

const segments = computed(() => [
  { value: kept.value, color: 'var(--signal-ok)', title: `kept ${kept.value}` },
  ...drops.value.map((d) => ({
    value: d.value,
    color: 'var(--signal-high)',
    title: `${d.label} ${d.value}`,
  })),
])

const label = computed(
  () =>
    `${props.stage.replace(/_/g, ' ')}: ${offered.value} offered, ${kept.value} kept` +
    (drops.value.length ? `, ${drops.value.map((d) => `${d.value} ${d.label}`).join(', ')}` : ''),
)

function share(value: number): string {
  return offered.value ? `${Math.round((value / offered.value) * 100)}%` : '—'
}
</script>

<template>
  <div class="funnel">
    <div class="flex items-baseline justify-between gap-2">
      <p class="text-2xs tracking-label uppercase text-text-tertiary">{{ stage.replace(/_/g, ' ') }}</p>
      <p class="mono text-xs text-text-secondary">
        {{ offered }} → <span class="text-text-primary">{{ kept }}</span>
        <span v-if="offered" class="text-text-tertiary"> · {{ share(kept) }} kept</span>
      </p>
    </div>

    <StatusBar class="mt-2" :segments="segments" :label="label" />

    <!-- Drops as a list rather than a legend. A legend makes you match colours
         to names; the number IS the point and it should be readable directly. -->
    <ul v-if="drops.length" class="mt-2 flex flex-col gap-(--space-half)">
      <li
        v-for="drop in drops"
        :key="drop.label"
        class="flex items-baseline justify-between gap-3 text-xs"
      >
        <span class="text-text-secondary">{{ drop.label }}</span>
        <span class="mono text-text-tertiary">
          {{ drop.value }}<span class="ml-2">{{ share(drop.value) }}</span>
        </span>
      </li>
    </ul>

    <p v-if="context.length" class="mt-2 text-2xs text-text-tertiary">
      <span v-for="(item, i) in context" :key="item.label">
        <span v-if="i"> · </span>{{ item.label }} <span class="mono">{{ item.value }}</span>
      </span>
    </p>

    <p v-if="!reconciled" class="mt-2 text-xs text-signal-critical">
      The counts do not add up: {{ offered }} offered,
      {{ kept + drops.reduce((n, d) => n + d.value, 0) }} accounted for. This
      step loses items without saying why.
    </p>
  </div>
</template>
