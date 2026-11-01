<script setup lang="ts">
import { computed } from 'vue'

import SectionHead from '@/components/ui/SectionHead.vue'

/**
 * A titled region. `bare` is "a rule, not a box": it drops the border and the
 * radius, but its header keeps the bottom rule, and that asymmetry is the whole
 * point of the variant. The operator pages use `bare` almost everywhere -- a
 * label, a hairline, and the content.
 */
const props = withDefaults(
  defineProps<{ title?: string; variant?: 'bare' | 'raised' | 'inset'; padded?: boolean }>(),
  { title: '', variant: 'bare', padded: true },
)

const SURFACE = {
  bare: 'border-0 rounded-none bg-transparent',
  raised: 'border border-border-subtle rounded-lg bg-surface-raised',
  inset: 'border border-border-subtle rounded-md bg-surface-inset',
} as const

const panelClass = computed(() => ['panel', `panel--${props.variant}`, 'min-w-0', SURFACE[props.variant]])
const headerClass = computed(() => [
  'panel__header flex items-center justify-between gap-3 border-b border-b-border-subtle',
  props.variant === 'bare' ? 'px-0 pt-0 pb-2' : 'px-4 py-3',
])
const bodyClass = computed(() => [
  'panel__body',
  props.padded ? (props.variant === 'bare' ? 'pt-4 px-0 pb-0' : 'p-4') : '',
])
</script>

<template>
  <section :class="panelClass">
    <header v-if="title || $slots.header" :class="headerClass">
      <SectionHead v-if="title" as="h2" class="panel__title grow" :label="title" />
      <slot name="header" />
    </header>
    <div :class="bodyClass"><slot /></div>
  </section>
</template>
