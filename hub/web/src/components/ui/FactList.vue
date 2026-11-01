<script setup lang="ts">
/**
 * A label-and-value column: the readout shape, for when the job is to SHOW
 * every field rather than to rank them.
 *
 * The labels are a fixed track so the values line up down the card and the eye
 * can run the column; a two-column grid rather than a table because these are
 * one object's properties, not rows of anything.
 *
 * NOTHING HERE IS TONED. A readout that colours four of its twelve values has
 * told the reader which four to care about, which is a judgement this component
 * is not the place to make -- a caller that genuinely has a state to show
 * passes it in the slot and styles it there.
 */
defineProps<{ facts: readonly { label: string; value: string }[] }>()
</script>

<template>
  <dl class="facts grid grid-cols-[minmax(0,auto)_minmax(0,1fr)] gap-x-4 gap-y-2 m-0">
    <template v-for="fact in facts" :key="fact.label">
      <dt
        class="m-0 font-ui text-2xs uppercase tracking-label text-text-tertiary whitespace-nowrap self-baseline"
      >
        {{ fact.label }}
      </dt>
      <dd class="m-0 font-ui text-sm text-text-secondary [overflow-wrap:anywhere]">
        <slot :name="fact.label" :value="fact.value">{{ fact.value }}</slot>
      </dd>
    </template>
  </dl>
</template>
